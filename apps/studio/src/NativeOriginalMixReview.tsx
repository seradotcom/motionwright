import { useEffect, useMemo, useRef, useState } from "react";
import { AudioWaveform, ShieldCheck } from "lucide-react";
import { nativeOriginalMixAudition } from "./api";
import { RecipeDraftReview, containRecipeKeyboard } from "./DeliveryProfiles";
import { savedDeliveryRecipe, useDeliveryRecipeDraft, validateDeliveryRecipe } from "./deliveryRecipe";
import { ORIGINAL_MIX_SAMPLE_PEAK_GUARD_DBFS, type OriginalMixPreviewOptions } from "./nativeMixTypes";
import type { Change, Project, VoiceTrack } from "./types";

const explain = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const wavTypes = ["audio/wav", "audio/x-wav", "audio/wave"];
const numericFields = [
  { key: "sfx_gain_db", label: "SFX gain dB", help: "−80 to 12 dB; decimals are allowed." },
  { key: "duck_attenuation_db", label: "Music duck attenuation dB", help: "0 to 30 dB; decimals are allowed." },
  { key: "duck_attack_samples", label: "Attack (samples at 48 kHz)", help: "1 to 48,000 whole samples." },
  { key: "duck_release_samples", label: "Release (samples at 48 kHz)", help: "1 to 48,000 whole samples." },
] as const;

function measuredVoiceProblem(voice: VoiceTrack): string | null {
  if (voice.sample_rate_hz !== 48_000 || voice.channels !== 2) return "The active measured voice must be stereo at 48 kHz for this audition.";
  try {
    const num = BigInt(voice.measured_duration.num), den = BigInt(voice.measured_duration.den);
    if (den <= 0n || num <= 0n || (num * 48_000n) % den !== 0n) return "The active voice duration must resolve to exact 48-kHz samples.";
    const samples = num * 48_000n / den;
    if (samples < 48_000n || samples > 720_000n) return "The local original PCM audition supports measured takes from 1 to 15 seconds.";
  } catch { return "The active voice has no valid measured duration for an original PCM audition."; }
  return null;
}

export default function NativeOriginalMixReview({ project, desktopMode, commit }: {
  project: Project;
  desktopMode: boolean;
  commit: (change: Change) => Promise<Project>;
}) {
  const [profileId, setProfileId] = useState(() => project.deliverables.find(profile =>
    savedDeliveryRecipe(project, profile.id)?.original_mix)?.id ?? project.deliverables[0]?.id ?? "");
  const selectedProfile = project.deliverables.find(profile => profile.id === profileId) ?? project.deliverables[0] ?? null;
  const mixDraft = useDeliveryRecipeDraft(project, selectedProfile?.id ?? "", "mix", commit);
  const recipe = selectedProfile ? savedDeliveryRecipe(project, selectedProfile.id) : null;
  const savedMix = recipe?.original_mix ?? null;
  const sourceAudio = useMemo(() => project.assets.filter(asset =>
    wavTypes.includes(asset.media_type) && /^[a-f0-9]{64}$/.test(asset.content_sha256 ?? "")), [project.assets]);
  const selectedVoice = project.audio.voice_tracks.find(track => track.id === project.audio.active_voice_track_id);
  const voiceAsset = selectedVoice ? project.assets.find(asset => asset.id === selectedVoice.asset_id) : undefined;
  const selectableSources = sourceAudio.filter(asset => asset.id !== selectedVoice?.asset_id);
  const [attestedScope, setAttestedScope] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [audition, setAudition] = useState<{ scope: string; url: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const serial = useRef(0);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const draftBlocksAudition = mixDraft.dirty || mixDraft.awaiting || mixDraft.pending
    || mixDraft.conflicts.length > 0 || mixDraft.recoveryBlocked || mixDraft.sourceBehind;
  const scope = JSON.stringify([project.id, project.generation, project.active_branch, project.revision,
    selectedProfile?.id, selectedVoice?.id, savedMix, project.audio.mix,
    mixDraft.values, draftBlocksAudition]);
  const liveScope = useRef(scope);
  liveScope.current = scope;
  const selfAttested = attestedScope === scope;
  const currentAudition = audition?.scope === scope ? audition : null;
  const fields = Object.fromEntries([
    ["enabled", "Enable original mix recipe"],
    ["music_asset_id", "Original music asset"],
    ["sfx_asset_id", "Original SFX asset"],
    ...numericFields.map(field => [field.key, field.label]),
  ].map(([key, label]) => [key, { label, controlId: "mix-" + selectedProfile?.id + "-" + key }]));

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; serial.current++; };
  }, []);
  useEffect(() => () => { if (audition) URL.revokeObjectURL(audition.url); }, [audition]);
  useEffect(() => {
    serial.current++;
    setAudition(null);
    setError(null);
    setAttestedScope(null);
  }, [scope]);

  const invalidateAudition = () => {
    serial.current++;
    setAudition(null);
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
  if (!savedMix) blockers.push("Save an enabled original mix recipe for this profile before auditioning.");
  if (selectedProfile && (selectedProfile.audio_sample_rate_hz !== 48_000 || !["en", "es", "de"].includes(selectedProfile.language))) {
    blockers.push("This local audition supports saved 48-kHz delivery profiles with language en, es or de.");
  }
  if (selectedProfile && recipe) {
    try { validateDeliveryRecipe(project, selectedProfile.id, recipe); }
    catch (failure) { blockers.push("The saved recipe needs correction: " + explain(failure)); }
  }
  if (!selectedVoice) blockers.push("Select an active measured voice take in Audio before auditioning.");
  else {
    const voiceProblem = measuredVoiceProblem(selectedVoice);
    if (voiceProblem) blockers.push(voiceProblem);
    if (!voiceAsset || !wavTypes.includes(voiceAsset.media_type)
        || !/^[a-f0-9]{64}$/.test(voiceAsset.content_sha256 ?? "")
        || voiceAsset.content_sha256 !== selectedVoice.source_sha256) {
      blockers.push("The active measured voice must still reference its exact imported WAV digest.");
    }
    if (selectedProfile?.voice_track_id && selectedProfile.voice_track_id !== selectedVoice.id) {
      blockers.push("The delivery profile is bound to a different voice take than the active Audio take. Select that active take or explicitly save a matching profile before auditioning.");
    }
    const voiceSegments = project.audio.transcript.filter(segment => segment.voice_track_id === selectedVoice.id);
    if (voiceSegments.length === 0 || voiceSegments.some(segment => segment.alignment.kind !== "manual"
        && !(segment.alignment.kind === "measured" && (segment.alignment.confidence_millis ?? 0) >= 800))) {
      blockers.push("Music ducking requires every transcript interval for the active voice to have manually aligned or high-confidence measured timing.");
    }
  }
  if (savedMix) {
    if (!savedMix.music_asset_id || !savedMix.sfx_asset_id) blockers.push("The saved recipe needs both an original music source and an original SFX source.");
    else {
      if (!sourceAudio.some(asset => asset.id === savedMix.music_asset_id)
          || !sourceAudio.some(asset => asset.id === savedMix.sfx_asset_id)) blockers.push("The saved music and SFX sources must be current imported WAV assets with content hashes.");
      if (savedMix.music_asset_id === savedMix.sfx_asset_id || savedMix.music_asset_id === selectedVoice?.asset_id
          || savedMix.sfx_asset_id === selectedVoice?.asset_id) blockers.push("Voice, music and SFX must use three distinct original source assets.");
    }
  }
  if ((project.audio.sfx_cues?.length ?? 0) > 0) blockers.push("This audition cannot apply the project's semantic SFX cue schedule. The schedule is retained; the three-bus whole-source mixer is blocked so it cannot silently omit those cues.");
  const eligible = blockers.length === 0 && selfAttested && !working;
  const inputsDisabled = !selectedProfile || mixDraft.locked || mixDraft.sourceBehind;

  const preview = async () => {
    if (!eligible || inFlight.current || !selectedProfile || !savedMix?.music_asset_id || !savedMix.sfx_asset_id) return;
    const options: OriginalMixPreviewOptions = {
      deliverable_profile_id: selectedProfile.id,
      music_asset_id: savedMix.music_asset_id,
      sfx_asset_id: savedMix.sfx_asset_id,
      sfx_gain_db: savedMix.sfx_gain_db,
      duck_attenuation_db: savedMix.duck_attenuation_db,
      duck_attack_samples: savedMix.duck_attack_samples,
      duck_release_samples: savedMix.duck_release_samples,
      owner_attests_preview_rights: true,
    };
    const ticket = ++serial.current, originalScope = scope;
    inFlight.current = true;
    setWorking(true);
    setError(null);
    setAudition(null);
    try {
      const wav = await nativeOriginalMixAudition(project, options);
      if (!mounted.current || ticket !== serial.current || liveScope.current !== originalScope) return;
      const owned = new Uint8Array(wav.length);
      owned.set(wav);
      setAudition({ scope: originalScope, url: URL.createObjectURL(new Blob([owned.buffer], { type: "audio/wav" })) });
    } catch (failure) {
      if (mounted.current && ticket === serial.current && liveScope.current === originalScope) setError(explain(failure));
    } finally {
      inFlight.current = false;
      if (mounted.current) setWorking(false);
    }
  };

  return <details className="audio-editor-section" aria-label="Original three-bus native PCM audition" onKeyDown={containRecipeKeyboard}>
    <summary><AudioWaveform size={15} /> Voice / music / SFX · original source mix</summary>
    <p className="production-help">Save an original mix recipe for each delivery profile, then request a local PCM audition of its exact saved sources and automation. The mixer decodes the real imported voice, music and SFX; every stem must be stereo at 48 kHz with the same measured duration.</p>
    <label className="production-field"><span>Delivery language / profile</span>
      <select value={selectedProfile?.id ?? ""} disabled={project.deliverables.length === 0}
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
    {([
      ["music_asset_id", "Original music asset", "Select imported WAV music"],
      ["sfx_asset_id", "Original SFX asset", "Select imported WAV effects"],
    ] as const).map(([key, label, placeholder]) => <label key={key} className="production-field"><span>{label}</span>
      <select id={fields[key].controlId} aria-label={label} value={mixDraft.values[key] ?? ""} disabled={inputsDisabled}
        onChange={event => setField(key, event.target.value)}>
        <option value="">{placeholder}</option>
        {mixDraft.values[key] && !selectableSources.some(asset => asset.id === mixDraft.values[key]) && <option value={mixDraft.values[key]}>Unavailable for this audition · {mixDraft.values[key]}</option>}
        {selectableSources.map(asset => <option key={asset.id} value={asset.id}>{asset.name} · sha256:{asset.content_sha256?.slice(0, 10)}</option>)}
      </select>
    </label>)}
    <div className="delivery-field-grid">
      {numericFields.map(field => <label className="production-field" key={field.key}>
        <span>{field.label}</span>
        <input id={fields[field.key].controlId} aria-label={field.label} type="text" inputMode="decimal" maxLength={128}
          aria-describedby={fields[field.key].controlId + "-help"} value={mixDraft.values[field.key] ?? ""}
          disabled={inputsDisabled} onChange={event => setField(field.key, event.target.value)} />
        <small id={fields[field.key].controlId + "-help"} className="production-help">{field.help} Input is retained up to 128 characters.</small>
      </label>)}
    </div>
    <RecipeDraftReview controller={mixDraft} title="Original mix" revision={project.revision} fields={fields}
      saveLabel="Save original mix recipe" clearLabel="Clear saved original mix recipe" hasSavedGroup={savedMix !== null}
      blockedReason={!selectedProfile ? "Create a delivery profile before saving these inputs."
        : working ? "Wait for the current local audition before saving a recipe change." : null} />
    <div className="portable-plan" aria-label="Saved global mix and audition policy"
      style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 150px), 1fr))" }}>
      <div><span>Saved voice gain</span><strong>{project.audio.mix.voice_gain_db} dB</strong></div>
      <div><span>Saved music gain</span><strong>{project.audio.mix.music_gain_db} dB</strong></div>
      <div><span>Sample peak guard policy</span><strong>{ORIGINAL_MIX_SAMPLE_PEAK_GUARD_DBFS} dBFS · fixed</strong></div>
    </div>
    <p className="production-help">Audition uses the active measured voice{selectedVoice ? " (" + selectedVoice.label + ")" : ""}, the selected saved profile and the saved global voice/music gains shown above. An unbound profile uses that active voice. The sample peak guard is a native mixer policy; it is separate from LUFS and true-peak targets.</p>
    {blockers.length > 0 && <section aria-label="Original mix audition requirements">
      <ul className="production-help">{blockers.map(reason => <li key={reason}>{reason}</li>)}</ul>
    </section>}
    <label className="production-check">
      <input type="checkbox" checked={selfAttested} disabled={blockers.length > 0 || working}
        onChange={event => {
          serial.current++;
          setAudition(null);
          setError(null);
          setAttestedScope(event.target.checked ? scope : null);
        }} />
      I confirm I have permission to use these exact original sources for local audition only. This is not an independently verified rights grant.
    </label>
    <p className="production-help">This confirmation is required again after an input, source, profile or saved revision changes, and when reopening the workspace. It is never stored in the recipe or a recovery copy.</p>
    {error && <p className="portable-message error" role="alert">{error}</p>}
    <button type="button" className="button button-primary" disabled={!eligible}
      onClick={() => void preview()}><ShieldCheck size={14} aria-hidden="true" />
      {working ? "Mixing verified sources…" : "Create local original PCM audition"}
    </button>
    {currentAudition && <div aria-label="Unmastered original PCM preview" className="audio-narration-source-evidence">
      <strong>Local source PCM only · 48 kHz · not a mastered deliverable</strong>
      <audio controls preload="metadata" src={currentAudition.url} />
      <p>Source, clock and sample-peak guards passed for this PCM. Integrated LUFS, true peak, listening intelligibility and editorial approval require separate measurement and review. No timeline event or mastered deliverable was created.</p>
    </div>}
  </details>;
}
