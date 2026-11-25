import { validateVoiceCutIntervals } from "./voiceCut";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AudioWaveform, ShieldCheck } from "lucide-react";
import { nativeOriginalMixAudition, readOriginalVoiceCutAudition } from "./api";
import { audioReviewScope, buildAudioReviewClock, type AudioReviewActions } from "./audioReviewClock";
import { useLocalAudioElement } from "./audioReviewPlayback";
import { useVoiceCutReview } from "./useVoiceCutReview";
import { originalMixEnvelopeKeys, requireOriginalMixSfx, selectedOriginalMixCues } from "./audioRecipeContract";
import { RecipeDraftReview, containRecipeKeyboard } from "./DeliveryProfiles";
import { savedDeliveryRecipe, useDeliveryRecipeDraft, validateDeliveryRecipe } from "./deliveryRecipe";
import { ORIGINAL_MIX_SAMPLE_PEAK_GUARD_DBFS, type OriginalMixPreviewOptions } from "./nativeMixTypes";
import type { Change, Project, VoiceTrack } from "./types";

const explain = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const wavTypes = ["audio/wav", "audio/x-wav", "audio/wave"];
const envelopeBuses = [
  {key:"voice_envelope", label:"Voice"}, {key:"music_envelope", label:"Music"}, {key:"sfx_envelope", label:"SFX"},
] as const;
const numericFields = [
  { key: "sfx_gain_db", label: "SFX gain dB", help: "−80 to 12 dB; decimals are allowed." },
  { key: "duck_attenuation_db", label: "Music duck attenuation dB", help: "0 to 30 dB; decimals are allowed." },
  { key: "duck_attack_samples", label: "Attack (samples at 48 kHz)", help: "1 to 48,000 whole samples." },
  { key: "duck_release_samples", label: "Release (samples at 48 kHz)", help: "1 to 48,000 whole samples." },
] as const;

function measuredVoiceProblem(voice: VoiceTrack): string | null {
  if (voice.sample_rate_hz !== 48_000 || voice.channels !== 2) return "The delivery's measured voice must be stereo at 48 kHz for this audition.";
  try {
    const num = BigInt(voice.measured_duration.num), den = BigInt(voice.measured_duration.den);
    if (den <= 0n || num <= 0n || (num * 48_000n) % den !== 0n) return "The delivery voice duration must resolve to exact 48-kHz samples.";
    const samples = num * 48_000n / den;
    if (samples < 48_000n || samples > 720_000n) return "The local original PCM audition supports measured takes from 1 to 15 seconds.";
  } catch { return "The delivery voice has no valid measured duration for an original PCM audition."; }
  return null;
}

export default function NativeOriginalMixReview({ project, desktopMode, commit, audioReview }: {
  project: Project;
  desktopMode: boolean;
  commit: (change: Change) => Promise<Project>;
  audioReview?: AudioReviewActions;
}) {
  const player = useLocalAudioElement();
  const [expanded, setExpanded] = useState(false);
  const panelId = useId();
  const [profileId, setProfileId] = useState(() => project.deliverables.find(profile =>
    savedDeliveryRecipe(project, profile.id)?.original_mix || savedDeliveryRecipe(project, profile.id)?.voice_only_cut_sha256)?.id ?? project.deliverables[0]?.id ?? "");
  const selectedProfile = project.deliverables.find(profile => profile.id === profileId) ?? project.deliverables[0] ?? null;
  const mixDraft = useDeliveryRecipeDraft(project, selectedProfile?.id ?? "", "mix", commit);
  const recipe = selectedProfile ? savedDeliveryRecipe(project, selectedProfile.id) : null;
  const savedMix = recipe?.original_mix ?? null;
  const voiceOnly = !savedMix && recipe?.voice_only_cut_sha256 !== undefined;
  const voiceCut = useVoiceCutReview(project, selectedProfile);
  const sourceAudio = useMemo(() => project.assets.filter(asset =>
    wavTypes.includes(asset.media_type) && /^[a-f0-9]{64}$/.test(asset.content_sha256 ?? "")), [project.assets]);
  const selectedVoice = project.audio.voice_tracks.find(track => track.id === selectedProfile?.voice_track_id);
  const voiceAsset = selectedVoice ? project.assets.find(asset => asset.id === selectedVoice.asset_id) : undefined;
  const selectableSources = sourceAudio.filter(asset => asset.id !== selectedVoice?.asset_id);
  const [attestedScope, setAttestedScope] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [audition, setAudition] = useState<{ scope: string; url: string; wav: Blob } | null>(null);
  // A real decoded PCM is available before WebKit builds media controls.
  // Defer their subtree until the user actually asks to listen.
  const [playbackEnabled, setPlaybackEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const serial = useRef(0);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const draftBlocksAudition = mixDraft.dirty || mixDraft.awaiting || mixDraft.pending
    || mixDraft.conflicts.length > 0 || mixDraft.recoveryBlocked || mixDraft.sourceBehind;
  const scope = JSON.stringify([project.id, project.generation, project.active_branch, project.revision,
    selectedProfile?.id, selectedVoice?.id, savedMix, recipe?.voice_only_cut_sha256, project.audio.mix,
    mixDraft.values, draftBlocksAudition]);
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const selfAttested = attestedScope === scope;
  const currentAudition = audition?.scope === scope ? audition : null;
  const fields = Object.fromEntries([
    ["enabled", "Enable original mix recipe"],
    ["music_asset_id", "Original music asset"],
    ["music_source_start_sample", "Music source start sample"],
    ["voice_cut_sha256", "Reviewed original voice cut binding"],
    ["sfx_asset_id", "Original SFX asset"],
    ...numericFields.map(field => [field.key, field.label]),
    ...envelopeBuses.flatMap(bus => [
      [bus.key + ":muted", "Mute " + bus.label.toLowerCase() + " bus"],
      [bus.key + ":fade_in_samples", bus.label + " fade-in samples"],
      [bus.key + ":fade_out_samples", bus.label + " fade-out samples"],
    ]),
  ].map(([key, label]) => [key, { label, controlId: "mix-" + selectedProfile?.id + "-" + key.replaceAll(":", "-") }]));

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; serial.current++; };
  }, []);
  useEffect(() => () => { if (audition) URL.revokeObjectURL(audition.url); }, [audition]);
  useEffect(() => {
    serial.current++;
    setAudition(null);
    setPlaybackEnabled(false);
    setError(null);
    setAttestedScope(null);
  }, [scope]);

  const invalidateAudition = () => {
    if(selectedProfile) audioReview?.invalidate({kind:"mix",sourceId:selectedProfile.id});
    serial.current++;
    setAudition(null);
    setPlaybackEnabled(false);
    setError(null);
    setAttestedScope(null);
  };
  const setField = (field: string, value: string) => {
    invalidateAudition();
    mixDraft.set(field, value);
  };

  const blockers: string[] = [];
  if (!desktopMode) blockers.push("Local PCM audition requires the desktop native service. Recipe authoring and saving are available here.");
  if (!selectedProfile) blockers.push("Create a delivery profile before saving or auditioning an original mix recipe.");
  if (draftBlocksAudition) blockers.push("Save or resolve the pending original mix recipe before auditioning; playback uses only saved values.");
  if (!savedMix && !voiceOnly) blockers.push("Save an enabled original mix recipe or reviewed voice-only cut for this profile before auditioning.");
  if (selectedProfile && (selectedProfile.audio_sample_rate_hz !== 48_000 || (savedMix && !["en", "es", "de", "en-US", "es-MX", "es-ES", "de-DE"].includes(selectedProfile.language)))) {
    blockers.push("This local audition supports saved 48-kHz delivery profiles with language en, es, de, en-US, es-MX, es-ES or de-DE.");
  }
  if (selectedProfile && recipe) {
    try { validateDeliveryRecipe(project, selectedProfile.id, recipe); }
    catch (failure) { blockers.push("The saved recipe needs correction: " + explain(failure)); }
  }
  if (!selectedProfile?.voice_track_id) blockers.push("Explicitly bind this delivery profile to a measured voice take before auditioning.");
  else if (!selectedVoice) blockers.push("The saved delivery voice is no longer available.");
  else {
    const voiceProblem = measuredVoiceProblem(selectedVoice);
    if (voiceProblem) blockers.push(voiceProblem);
    if (!voiceAsset || !wavTypes.includes(voiceAsset.media_type)
        || !/^[a-f0-9]{64}$/.test(voiceAsset.content_sha256 ?? "")
        || voiceAsset.content_sha256 !== selectedVoice.source_sha256) {
      blockers.push("The delivery's measured voice must still reference its exact imported WAV digest.");
    }
    const voiceSegments = project.audio.transcript.filter(segment => segment.voice_track_id === selectedVoice.id);
    if (savedMix && (voiceSegments.length === 0 || voiceSegments.some(segment => segment.alignment.kind !== "manual"
        && !(segment.alignment.kind === "measured" && segment.alignment.source_sha256 === selectedVoice.source_sha256
          && (segment.alignment.confidence_millis ?? 0) >= 800)))) {
      blockers.push("Music ducking requires every transcript interval for the delivery voice to have manually aligned or high-confidence source-bound measured timing.");
    }
  }
  if (savedMix) {
    if (voiceCut.montage && (!voiceCut.digest || savedMix.voice_cut_sha256 !== voiceCut.digest))
      blockers.push(voiceCut.error ?? "Review and save the exact original voice cut binding before playback.");
    if (voiceCut.cut && selectedProfile) {
      try { validateVoiceCutIntervals(project, selectedProfile, voiceCut.cut); }
      catch (failure) { blockers.push(explain(failure)); }
    }
    if (!voiceCut.montage && savedMix.voice_cut_sha256 !== undefined)
      blockers.push("Saved voice cut binding is stale; clear it before playback.");
    if (!savedMix.music_asset_id) blockers.push("The saved recipe needs an original music source.");
    if (!sourceAudio.some(asset => asset.id === savedMix.music_asset_id)
        || (savedMix.sfx_asset_id !== null && !sourceAudio.some(asset => asset.id === savedMix.sfx_asset_id))) {
      blockers.push("Saved music and any imported SFX base must be current imported WAV assets with content hashes.");
    }
    if (savedMix.music_asset_id === selectedVoice?.asset_id
        || (savedMix.sfx_asset_id !== null && (savedMix.music_asset_id === savedMix.sfx_asset_id
          || savedMix.sfx_asset_id === selectedVoice?.asset_id))) {
      blockers.push("Voice, music and an imported SFX base require distinct original source assets.");
    }
    if (selectedProfile) {
      try { requireOriginalMixSfx(project, selectedProfile.id, savedMix); }
      catch (failure) { blockers.push(explain(failure)); }
    }
  }
  if (voiceOnly && selectedProfile) {
    if (!voiceCut.montage || !voiceCut.digest || recipe?.voice_only_cut_sha256 !== voiceCut.digest)
      blockers.push(voiceCut.error ?? "Review and save the exact voice-only cut binding before playback.");
    if (project.audio.mix.voice_gain_db !== 0 || project.audio.mix.target_lufs !== null
      || project.audio.mix.target_true_peak_dbfs !== null)
      blockers.push("Voice-only cut cannot discard authored voice gain or global mastering targets.");
    try {
      if (selectedOriginalMixCues(project, selectedProfile.id).length)
        blockers.push("Voice-only cut cannot discard selected authored SFX. Save an original mix instead.");
    } catch (failure) { blockers.push(explain(failure)); }
  }
  const eligible = blockers.length === 0 && selfAttested && !working;
  const inputsDisabled = !selectedProfile || mixDraft.locked || mixDraft.sourceBehind;

  const preview = async () => {
    if (!eligible || inFlight.current || !selectedProfile || (!voiceOnly && !savedMix?.music_asset_id)) return;
    const options: OriginalMixPreviewOptions | null = savedMix?.music_asset_id ? {
      deliverable_profile_id: selectedProfile.id,
      music_asset_id: savedMix.music_asset_id,
      sfx_asset_id: savedMix.sfx_asset_id,
      sfx_gain_db: savedMix.sfx_gain_db,
      duck_attenuation_db: savedMix.duck_attenuation_db,
      duck_attack_samples: savedMix.duck_attack_samples,
      duck_release_samples: savedMix.duck_release_samples,
      owner_attests_preview_rights: true,
    } : null;
    const ticket = ++serial.current, originalScope = scope;
    inFlight.current = true;
    setWorking(true);
    setError(null);
    setPlaybackEnabled(false);
    setAudition(null);
    try {
      const wav = voiceOnly
        ? await readOriginalVoiceCutAudition(project, selectedProfile.id, selfAttested)
        : await nativeOriginalMixAudition(project, options!);
      if (!mounted.current || ticket !== serial.current || liveScope.current !== originalScope) return;
      const owned = new Uint8Array(wav.length);
      owned.set(wav);
      const verifiedWav = new Blob([owned.buffer], { type: "audio/wav" });
      setAudition({ scope: originalScope, url: URL.createObjectURL(verifiedWav), wav: verifiedWav });
    } catch (failure) {
      if (mounted.current && ticket === serial.current && liveScope.current === originalScope) setError(explain(failure));
    } finally {
      inFlight.current = false;
      if (mounted.current) setWorking(false);
    }
  };

  let reviewProblem: string | null = null;
  try { buildAudioReviewClock(project,{kind:"mix",sourceId:selectedProfile?.id??""}); }
  catch(reason) { reviewProblem = explain(reason); }

  const draftSourceStatus = `Draft source bindings: music ${mixDraft.values.music_asset_id || "none"}; SFX ${mixDraft.values.sfx_asset_id || "none"}.`;
  return <section className="audio-editor-section" aria-label="Original three-bus native PCM audition" onKeyDown={containRecipeKeyboard}>
    <button type="button" className="button button-secondary" aria-expanded={expanded}
      aria-controls={panelId} onClick={() => { player.pause(); setPlaybackEnabled(false); setExpanded(value => !value); }}>
      <AudioWaveform size={15} aria-hidden="true" /> Voice / music / SFX · original source mix
    </button>
    <div id={panelId} hidden={!expanded}>
    <p className="production-help">Save an original mix recipe for each delivery profile, then request a local PCM audition of its exact saved sources and automation. The mixer decodes the imported voice and music, an optional imported SFX base, and the authored SFX cues in this delivery cut. Voice uses the original stereo 48-kHz take. A reviewed montage binding conforms it by selected scene ranges. Imported SFX base must already match the output cut; semantic cues follow the saved scene schedule. Choose an explicit music start sample to consume a matching contiguous window of a longer original WAV. The native service validates cue timing and source bytes.</p>
    <label className="production-field"><span>Delivery language / profile</span>
      <select aria-label="Delivery language / profile" value={selectedProfile?.id ?? ""} disabled={project.deliverables.length === 0}
        onChange={event => { setProfileId(event.target.value); invalidateAudition(); }}>
        {project.deliverables.length === 0 && <option value="">No delivery profile</option>}
        {project.deliverables.map(profile => <option key={profile.id} value={profile.id}>{profile.name} · {profile.language}</option>)}
      </select>
    </label>
    <label className="production-check">
      <input id={fields.enabled.controlId} type="checkbox" checked={mixDraft.values.enabled === "true"}
        disabled={inputsDisabled} onChange={event => setField("enabled", String(event.target.checked))} />
      Enable original mix recipe
    </label>
    <p className="production-help">Leave the mix disabled to save and audition only the reviewed original narration cut, without music or SFX.</p>
    {(voiceCut.montage || mixDraft.values.voice_cut_sha256) && <section aria-label="Original voice cut review" className="production-notice">
      <strong>Conform original narration to saved cut</strong>
      <p className="production-help">The original WAV is never modified. Review the exact ordered source samples (exclusive ends) and output offsets; no stretch, pad or inferred word cut.</p>
      {voiceCut.error && <p role="status">{voiceCut.error}</p>}
      {voiceCut.cut && <>
        <p>{voiceCut.cut.sourceFrames} source samples to {voiceCut.cut.outputFrames} output samples at 48 kHz.</p>
        <table><thead><tr><th>Scene</th><th>Source samples</th><th>Output start</th></tr></thead>
          <tbody>{voiceCut.cut.spans.map(span => <tr key={span.sceneId}>
            <td>{project.scenes.find(scene => scene.id === span.sceneId)?.name ?? span.sceneId}</td>
            <td>{span.sourceStart} to {span.sourceEnd}</td><td>{span.outputStart}</td>
          </tr>)}</tbody></table>
        <p role="status">{voiceCut.digest && mixDraft.values.voice_cut_sha256 === voiceCut.digest
          ? "Exact cut binding selected." : "Review required; the current cut is not bound."}</p>
        <button type="button" className="button button-secondary" disabled={inputsDisabled || !voiceCut.digest}
          onClick={() => {if (voiceCut.digest) setField("voice_cut_sha256", voiceCut.digest);}}>Bind reviewed original voice cut</button>
      </>}
      {mixDraft.values.voice_cut_sha256 && <button type="button" className="button button-secondary" disabled={inputsDisabled}
        onClick={() => setField("voice_cut_sha256", "")}>Clear original voice cut binding</button>}
    </section>}
    {([
      ["music_asset_id", "Original music asset", "Select imported WAV music"],
      ["sfx_asset_id", "Original SFX asset", "No imported base; use authored cues in this cut"],
    ] as const).map(([key, label, placeholder]) => <label key={key} className="production-field"><span>{label}</span>
      <select id={fields[key].controlId} aria-label={label} value={mixDraft.values[key] ?? ""} disabled={inputsDisabled}
        onChange={event => setField(key, event.target.value)}>
        <option value="">{placeholder}</option>
        {mixDraft.values[key] && !selectableSources.some(asset => asset.id === mixDraft.values[key]) && <option value={mixDraft.values[key]}>Unavailable for this audition · {mixDraft.values[key]}</option>}
        {selectableSources.map(asset => <option key={asset.id} value={asset.id}>{asset.name} · sha256:{asset.content_sha256?.slice(0, 10)}</option>)}
      </select>
    </label>)}
    <label className="production-field">
      <span>Music source start sample</span>
      <input id={fields.music_source_start_sample.controlId} aria-label="Music source start sample"
        type="text" inputMode="numeric" maxLength={128} placeholder="Whole original source"
        value={mixDraft.values.music_source_start_sample ?? ""} disabled={inputsDisabled || mixDraft.values.enabled !== "true"}
        aria-invalid={mixDraft.conflicts.includes("music_source_start_sample")}
        aria-describedby={fields.music_source_start_sample.controlId + "-help"}
        onChange={event => setField("music_source_start_sample", event.target.value)} />
      <small id={fields.music_source_start_sample.controlId + "-help"}>Blank uses the complete original music file. Zero or a positive integer selects the exact 48 kHz sample at which the full saved cut begins (48,000 = 1 second). Only 1:1 playback is supported; no repeat, pad or time stretch. Actual source length is checked when preparing PCM. A different music asset needs an explicit new window decision.</small>
    </label>
    {savedMix?.music_source_start_sample !== undefined && <p className="production-help" aria-label="Saved music source window">
      Saved music starts at source sample {savedMix.music_source_start_sample}. Its end is the exclusive start plus the exact full-cut sample count. No source bytes have been changed.
    </p>}
    <p className="production-help" role="status" aria-label={draftSourceStatus}>
      {draftSourceStatus}
    </p>
    {selectedProfile && <p className="production-help">Authored SFX cues in this cut: {(() => {
      try { return selectedOriginalMixCues(project, selectedProfile.id).length; }
      catch { return "unavailable"; }
    })()}. Cues in other cuts are retained.</p>}
    <div className="delivery-field-grid">
      {numericFields.map(field => <label className="production-field" key={field.key}>
        <span>{field.label}</span>
        <input id={fields[field.key].controlId} aria-label={field.label} type="text" inputMode="decimal" maxLength={128}
          aria-describedby={fields[field.key].controlId + "-help"} value={mixDraft.values[field.key] ?? ""}
          disabled={inputsDisabled} onChange={event => setField(field.key, event.target.value)} />
        <small id={fields[field.key].controlId + "-help"} className="production-help">{field.help} Input is retained up to 128 characters.</small>
      </label>)}
    </div>
    <section aria-label="Original mix bus automation">
      <h4>Voice, music and SFX automation</h4>
      <p className="production-help">Set mute and linear fades for each bus in the selected delivery cut.
        Fade lengths use exact 48-kHz samples: 48,000 samples is one second. Zero disables a fade.
        The two fades must fit without overlap. A muted voice does not duck the music.</p>
      {envelopeBuses.map(bus => <section key={bus.key} className="production-notice" aria-label={bus.label + " bus automation"}>
        <strong>{bus.label} bus</strong>
        <label className="production-check">
          <input id={fields[bus.key + ":muted"].controlId} type="checkbox"
            checked={mixDraft.values[bus.key + ":muted"] === "true"}
            disabled={inputsDisabled || mixDraft.values.enabled !== "true"}
            onChange={event => setField(bus.key + ":muted", String(event.target.checked))} />
          {fields[bus.key + ":muted"].label}
        </label>
        <div className="delivery-field-grid">
          {(["fade_in_samples", "fade_out_samples"] as const).map(property => {
            const key = bus.key + ":" + property, field = fields[key];
            return <label key={key} className="production-field">
              <span>{field.label}</span>
              <input id={field.controlId} aria-label={field.label} type="text" inputMode="numeric" maxLength={128}
                aria-invalid={mixDraft.conflicts.includes(key)}
                value={mixDraft.values[key] ?? "0"} disabled={inputsDisabled || mixDraft.values.enabled !== "true"}
                onChange={event => setField(key, event.target.value)} />
            </label>;
          })}
        </div>
      </section>)}
    </section>
    {savedMix && originalMixEnvelopeKeys.some(key => savedMix[key]) && <section aria-label="Saved bus envelopes">
      <h4>Saved bus envelopes</h4>
      <p className="production-help">These are the saved decisions used by audition and production. Editing the controls above keeps them pending until Save original mix recipe confirms the complete change.</p>
      <ul className="production-help">{originalMixEnvelopeKeys.map(key => {
        const envelope = savedMix[key];
        return envelope ? <li key={key}>{key.replace("_envelope", "")}: {envelope.muted ? "muted" : "audible"}; fade in {envelope.fade_in_samples} samples; fade out {envelope.fade_out_samples} samples.</li> : null;
      })}</ul>
    </section>}
    {recipe?.audio_targets && <p className="production-help" aria-label="Saved delivery audio targets">
      Saved QC intent: {recipe.audio_targets.target_lufs === null ? "LUFS target unset" : recipe.audio_targets.target_lufs + " LUFS"};
      {" "}{recipe.audio_targets.target_true_peak_dbtp === null ? "true-peak target unset" : recipe.audio_targets.target_true_peak_dbtp + " dBTP"}.
      These targets are retained when editing this recipe; audition does not certify them.
    </p>}
    <RecipeDraftReview controller={mixDraft} title="Original mix" revision={project.revision} fields={fields}
      saveLabel="Save original mix recipe" clearLabel="Clear saved original mix recipe" hasSavedGroup={savedMix !== null || voiceOnly}
      blockedReason={!selectedProfile ? "Create a delivery profile before saving these inputs."
        : working ? "Wait for the current local audition before saving a recipe change." : null} />
    <div className="portable-plan" aria-label="Saved global mix and audition policy"
      style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 150px), 1fr))" }}>
      <div><span>Saved voice gain</span><strong>{project.audio.mix.voice_gain_db} dB</strong></div>
      <div><span>Saved music gain</span><strong>{project.audio.mix.music_gain_db} dB</strong></div>
      <div><span>Sample peak guard policy</span><strong>{voiceOnly ? "Not applied to voice-only source" : ORIGINAL_MIX_SAMPLE_PEAK_GUARD_DBFS + " dBFS (fixed)"}</strong></div>
    </div>
    <p className="production-help">{voiceOnly
      ? "Voice-only audition copies the reviewed original PCM16 sample ranges without gain, music, SFX or normalization. It does not change the active Audio take."
      : <>Audition uses the delivery's explicitly bound measured voice{selectedVoice ? " (" + selectedVoice.label + ")" : ""} and the saved global voice/music gains shown above. It does not change the active Audio take. The sample peak guard is a native mixer policy; it is separate from LUFS and true-peak targets.</>}</p>
    {blockers.length > 0 && <section aria-label="Original mix audition requirements">
      <ul className="production-help">{blockers.map(reason => <li key={reason}>{reason}</li>)}</ul>
    </section>}
    <label className="production-check">
      <input type="checkbox" checked={selfAttested} disabled={blockers.length > 0 || working}
        onChange={event => {
          if(selectedProfile) audioReview?.invalidate({kind:"mix",sourceId:selectedProfile.id});
          serial.current++;
          setAudition(null);
          setPlaybackEnabled(false);
          setError(null);
          setAttestedScope(event.target.checked ? scope : null);
        }} />
      I confirm I have permission to use these exact original sources for local audition only. This is not an independently verified rights grant.
    </label>
    <p className="production-help">This confirmation is required again after an input, source, profile or saved revision changes, and when reopening the workspace. It is never stored in the recipe or a recovery copy.</p>
    {error && <p className="portable-message error" role="alert">{error}</p>}
    <button type="button" className="button button-primary" disabled={!eligible}
      aria-label={currentAudition
        ? "Create local original PCM audition · Local source PCM only · 48 kHz · not a mastered deliverable"
        : "Create local original PCM audition"}
      onClick={() => void preview()}><ShieldCheck size={14} aria-hidden="true" />
      {working ? "Mixing verified sources…" : "Create local original PCM audition"}
    </button>
    {currentAudition && <div aria-label="Unmastered original PCM preview" className="audio-narration-source-evidence">
      <strong>Local source PCM only · 48 kHz · not a mastered deliverable</strong>
      {playbackEnabled
        ? <audio aria-label="Original mix playback" ref={player.bind} onPlay={player.claim} controls preload="none" src={currentAudition.url} />
        : <button type="button" className="button button-secondary"
            aria-label="Enable local original PCM playback controls"
            onClick={() => setPlaybackEnabled(true)}>
            Enable playback controls
          </button>}
      {audioReview&&<div>
        <button type="button" className="button button-secondary" disabled={!!reviewProblem}
          onClick={()=>{if(!currentAudition||!selectedProfile||reviewProblem)return;player.pause();audioReview.open({
            kind:"mix",sourceId:selectedProfile.id,scope:audioReviewScope(project),
            label:selectedProfile.name+(voiceOnly?" voice-only cut":" original mix"),wav:currentAudition.wav});}}>
          {voiceOnly ? "Review saved voice cut on project timeline" : "Review saved mix on project timeline"}
        </button>
        {reviewProblem&&<p role="status" className="production-help">{reviewProblem}</p>}
      </div>}
      <p>{voiceOnly ? "Original source and exact selected sample clock verified. No mixing or sample-peak normalization was performed." : "Source, clock and sample-peak guards passed for this PCM."} Integrated LUFS, true peak, listening intelligibility and editorial approval require separate measurement and review. No timeline event or mastered deliverable was created.</p>
    </div>}
    </div>
  </section>;
}
