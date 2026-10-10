import { CircleCheck, CircleDashed, FileOutput, Play, Plus, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import NativeAvReview from "./NativeAvReview";
import {
  assembleNativeAvMaster,
  exportCaptionSidecar,
  exportOtio,
  exportVerifiedNativeMaster,
  preflightMotionCanvas,
  renderMotionCanvas,
} from "./api";
import type {
  Change,
  DeliverableProfile,
  MotionCanvasArchetype,
  MotionCanvasNarrativeRole,
  MotionCanvasRenderEvidence,
  MotionCanvasProjectionPreflight,
  MotionCanvasFilmOptions,
  MltAvMasterEvidence,
  MasterExportReceipt,
  Project,
} from "./types";

type Commit = (change: Change) => Promise<void>;

const codecLabel: Record<DeliverableProfile["video_codec"], string> = {
  h264: "H.264",
  hevc: "HEVC",
  prores_422_hq: "ProRes 422 HQ",
  vp9: "VP9",
  av1: "AV1",
};

const narrativeRoles: Array<{ value: MotionCanvasNarrativeRole; label: string }> = [
  { value: "hook", label: "Hook" },
  { value: "problem", label: "Problem" },
  { value: "mechanism", label: "Mechanism" },
  { value: "evidence", label: "Evidence" },
  { value: "comparison", label: "Comparison" },
  { value: "reveal", label: "Reveal" },
  { value: "payoff", label: "Payoff" },
  { value: "cta", label: "CTA" },
];

const motionArchetypes: Array<{ value: MotionCanvasArchetype; label: string }> = [
  { value: "statement", label: "Statement" },
  { value: "split_explanation", label: "Split explanation" },
  { value: "architecture_reveal", label: "Architecture reveal" },
  { value: "comparison", label: "Comparison" },
  { value: "metric", label: "Metric" },
  { value: "timeline", label: "Timeline" },
  { value: "code_focus", label: "Code focus" },
  { value: "diagram_build", label: "Diagram build" },
  { value: "object_spotlight", label: "Object spotlight" },
  { value: "evidence_frame", label: "Evidence frame" },
  { value: "product_proof", label: "Product proof" },
  { value: "endcard", label: "End card" },
];

type SceneIntentDraft = {
  role: MotionCanvasNarrativeRole | "";
  archetype: MotionCanvasArchetype | "";
};

function freshProfile(index: number): DeliverableProfile {
  return {
    id: crypto.randomUUID(),
    name: "Delivery " + index,
    width: 1920,
    height: 1080,
    language: "en",
    captions: true,
    caption_format: "web_vtt",
    video_codec: "h264",
    audio_codec: "aac",
    audio_sample_rate_hz: 48000,
    brand_profile: null,
    cut_label: null,
    parent_profile_id: null,
    source_revision: null,
    framing_strategy: "replan",
    crop_approved: false,
    timing_locked: false,
    voice_track_id: null,
    text_overrides: {},
    included_scene_ids: [],
    protected_scene_ids: [],
    burn_in_captions: false,
    frame_rate: { num: "30", den: "1" },
    color_space: "rec709",
    container: "mp4",
    adaptation_notes: [],
  };
}

export default function DeliveryProfiles({
  project,
  commit,
  desktopMode,
  renderEvidence,
  onRenderEvidence,
  avEvidence,
  onAvEvidence,
  exportEvidence,
  onExportEvidence,
}: {
  project: Project;
  commit: Commit;
  desktopMode: boolean;
  renderEvidence: MotionCanvasRenderEvidence | null;
  onRenderEvidence: (evidence: MotionCanvasRenderEvidence) => void;
  avEvidence: MltAvMasterEvidence | null;
  onAvEvidence: (evidence: MltAvMasterEvidence) => void;
  exportEvidence: MasterExportReceipt | null;
  onExportEvidence: (receipt: MasterExportReceipt) => void;
}) {
  const [selectedId, setSelectedId] = useState(() =>
    project.deliverables.find((profile) => profile.id === renderEvidence?.deliverable_id)?.id
      ?? project.deliverables[0]?.id ?? "");
  const selected = project.deliverables.find((profile) => profile.id === selectedId) ?? project.deliverables[0] ?? null;
  const [draft, setDraft] = useState<DeliverableProfile | null>(() => selected ? structuredClone(selected) : null);
  const [sidecarPath, setSidecarPath] = useState("");
  const [otioPath, setOtioPath] = useState("");
  const [masterDeliveryPath, setMasterDeliveryPath] = useState("");
  const [includeMediaIntegrity, setIncludeMediaIntegrity] = useState(false);
  const [showNativeAvReview, setShowNativeAvReview] = useState(false);
  const [otioLosses, setOtioLosses] = useState<string[]>([]);
  const fontFamily = "Instrument Sans Variable";
  const monoFontFamily = "IBM Plex Mono";
  const [sceneIntents, setSceneIntents] = useState<Record<string, SceneIntentDraft>>({});
  const visibleEvidence = renderEvidence?.generation === project.generation
    && renderEvidence.deliverable_id === selected?.id ? renderEvidence : null;
  const [busy, setBusy] = useState<"save" | "remove" | "caption" | "otio" | "render" | "preflight" | "master" | "export-master" | "new" | null>(null);
  const [preflight, setPreflight] = useState<{
    key: string;
    report: MotionCanvasProjectionPreflight;
  } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const current = project.deliverables.find((profile) => profile.id === selectedId) ?? project.deliverables[0] ?? null;
    if (current && current.id !== selectedId) setSelectedId(current.id);
    setDraft(current ? structuredClone(current) : null);
    setSidecarPath("");
    setOtioLosses([]);

  }, [project.generation, project.revision, selectedId]);

  const motionScenes = useMemo(() => {
    const selectedSceneIds = selected?.included_scene_ids ?? [];
    const included = selectedSceneIds.length > 0 ? new Set(selectedSceneIds) : null;
    return project.scenes.filter(
      (scene) => scene.renderer === "motion-canvas" && (!included || included.has(scene.id)),
    );
  }, [project.scenes, selected]);

  const textNodes = useMemo(() => {
    const selectedSceneIds = draft?.included_scene_ids ?? [];
    const included = selectedSceneIds.length > 0 ? new Set(selectedSceneIds) : null;
    return project.scenes
      .filter((scene) => !included || included.has(scene.id))
      .flatMap((scene) =>
        scene.nodes
          .filter((node) => node.kind === "text")
          .map((node) => ({ scene, node })),
      );
  }, [draft?.included_scene_ids, project.scenes]);

  useEffect(() => {
    setSceneIntents((current) => Object.fromEntries(
      motionScenes.map((scene) => [scene.id, current[scene.id] ?? { role: "", archetype: "" }]),
    ));
  }, [motionScenes]);

  const profileTranscript = useMemo(
    () => draft?.voice_track_id
      ? project.audio.transcript.filter((segment) => segment.voice_track_id === draft.voice_track_id)
      : project.audio.transcript,
    [draft?.voice_track_id, project.audio.transcript],
  );
  const transcriptTrackCount = useMemo(
    () => new Set(profileTranscript.map((segment) => segment.voice_track_id)).size,
    [profileTranscript],
  );
  const unknownTimings = useMemo(
    () => profileTranscript.filter((segment) => segment.alignment.kind === "unknown").length,
    [profileTranscript],
  );
  const parentProfile = draft?.parent_profile_id
    ? project.deliverables.find((profile) => profile.id === draft.parent_profile_id) ?? null
    : null;
  const localizedVariant = Boolean(draft && parentProfile && draft.language !== parentProfile.language);
  const dirty = Boolean(selected && draft && JSON.stringify(selected) !== JSON.stringify(draft));
  const captionReady = Boolean(
    draft?.captions &&
    profileTranscript.length > 0 &&
    unknownTimings === 0 &&
    (draft.voice_track_id !== null || transcriptTrackCount <= 1),
  );
  const renderIntentsComplete = motionScenes.length > 0 && motionScenes.every((scene) => {
    const intent = sceneIntents[scene.id];
    return Boolean(intent?.role && intent?.archetype);
  });
  const nativePaletteReady = useMemo(() => {
    const names = new Set(project.visual_language.palette.map((token) => token.name.toLowerCase()));
    return (names.has("text") || names.has("ink")) &&
      (names.has("background") || names.has("surface"));
  }, [project.visual_language.palette]);
  const renderReady = Boolean(
    desktopMode &&
    selected &&
    !dirty &&
    renderIntentsComplete &&
    nativePaletteReady &&
    fontFamily.trim() &&
    monoFontFamily.trim(),
  );

  const selectedVoice = project.audio.voice_tracks.find((track) =>
    track.id === selected?.voice_track_id
  ) ?? null;
  const voiceAsset = selectedVoice
    ? project.assets.find((asset) => asset.id === selectedVoice.asset_id) ?? null
    : null;
  const verifiedWavIntent = Boolean(selectedVoice && voiceAsset
    && voiceAsset.content_sha256 === selectedVoice.source_sha256
    && ["audio/wav", "audio/wave", "audio/x-wav"].includes(voiceAsset.media_type)
    && selectedVoice.sample_rate_hz === 48_000 && selectedVoice.channels === 2);
  const currentMotion = visibleEvidence?.revision === project.revision ? visibleEvidence : null;
  const masterPreviewToken = currentMotion?.segments.length === 1
    ? currentMotion.preview?.find((handle) =>
      handle.segment_id === currentMotion.segments[0].segment_id)?.token ?? null
    : null;
  const voiceDurationSeconds = selectedVoice
    ? Number(selectedVoice.measured_duration.num) / Number(selectedVoice.measured_duration.den)
    : Number.NaN;
  const motionFrameRate = currentMotion
    ? currentMotion.frame_rate.num / currentMotion.frame_rate.den : Number.NaN;
  const nativeVideoSeconds = currentMotion?.segments.length === 1
    ? currentMotion.segments[0].frame_count / motionFrameRate : Number.NaN;
  const voiceAligned = Number.isFinite(voiceDurationSeconds)
    && Number.isFinite(nativeVideoSeconds)
    && Math.abs(voiceDurationSeconds - nativeVideoSeconds) <= 1 / 48_000;
  const masterProfileSupported = Boolean(selected
    && selected.video_codec === "h264"
    && selected.audio_codec === "aac"
    && selected.audio_sample_rate_hz === 48000
    && selected.color_space === "rec709"
    && selected.container === "mp4");
  const masterReady = Boolean(desktopMode && selected && !dirty
    && verifiedWavIntent && voiceAligned && masterPreviewToken && masterProfileSupported);
  const currentAv = avEvidence?.generation === project.generation
    && avEvidence.deliverable_id === selected?.id ? avEvidence : null;
  const masterArtifact = currentAv?.master.artifact as Record<string, unknown> | undefined;
  const masterDigest = typeof masterArtifact?.sha256 === "string" ? masterArtifact.sha256 : null;
  const currentExport = exportEvidence?.deliverable_id === selected?.id ? exportEvidence : null;
  const reviewAvailable = Boolean(desktopMode && selected && !dirty
    && currentAv?.revision === project.revision && currentAv.export_token);
  const exportReady = Boolean(desktopMode && selected && !dirty
    && currentAv?.revision === project.revision && currentAv?.export_token
    && masterDeliveryPath.trim().toLowerCase().endsWith(".mp4"));

  const preflightKey = JSON.stringify([
    project.id,
    project.generation,
    project.revision,
    selected?.id,
    selected?.frame_rate,
    motionScenes.map((scene) => scene.id),
    sceneIntents,
    fontFamily,
    monoFontFamily,
  ]);
  const currentPreflight = preflight?.key === preflightKey ? preflight.report : null;

  function filmOptions(): MotionCanvasFilmOptions {
    if (!selected) throw new Error("Select a saved profile before building a Film projection.");
    const num = Number(selected.frame_rate.num);
    const den = Number(selected.frame_rate.den);
    if (!Number.isInteger(num) || !Number.isInteger(den) || num <= 0 || den <= 0) {
      throw new Error("The saved delivery profile has an invalid canonical frame rate.");
    }
    return {
      frame_rate: { num, den },
      font_family: fontFamily.trim(),
      mono_font_family: monoFontFamily.trim(),
      scene_intents: motionScenes.map((scene) => {
        const intent = sceneIntents[scene.id];
        if (!intent?.role || !intent.archetype) {
          throw new Error("Every Motion Canvas scene requires an explicit role and archetype.");
        }
        return {
          scene_id: scene.id,
          role: intent.role,
          archetype: intent.archetype,
        };
      }),
    };
  }

  const run = async (kind: typeof busy, operation: () => Promise<void>) => {
    setBusy(kind);
    setMessage(null);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  };

  const createProfile = () => void run("new", async () => {
    const profile = freshProfile(project.deliverables.length + 1);
    await commit({ type: "upsert_deliverable", profile });
    setSelectedId(profile.id);
  });

  const deriveProfile = () => {
    if (!selected) return;
    void run("new", async () => {
      const profile = structuredClone(selected);
      profile.id = crypto.randomUUID();
      profile.name = selected.name + " variant";
      profile.parent_profile_id = selected.id;
      profile.source_revision = project.revision;
      profile.framing_strategy = "replan";
      profile.crop_approved = false;
      profile.timing_locked = true;
      profile.adaptation_notes = [
        "Derived from " + selected.name + " at revision r" + project.revision + ".",
      ];
      await commit({ type: "upsert_deliverable", profile });
      setSelectedId(profile.id);
    });
  };

  const saveProfile = () => {
    if (!draft) return;
    void run("save", async () => {
      await commit({ type: "upsert_deliverable", profile: draft });
    });
  };

  const removeProfile = () => {
    if (!selected) return;
    void run("remove", async () => {
      await commit({ type: "remove_deliverable", profile_id: selected.id });
    });
  };

  const exportCaptions = () => {
    if (!draft || dirty || !sidecarPath.trim()) return;
    void run("caption", async () => {
      const result = await exportCaptionSidecar(project, draft.id, sidecarPath.trim());
      setMessage("Caption sidecar written · " + result.cue_count + " cues · " + result.path);
    });
  };

  const exportTimeline = () => {
    if (!otioPath.trim()) return;
    void run("otio", async () => {
      const result = await exportOtio(project, otioPath.trim());
      setOtioLosses(result.loss_report);
      setMessage("OTIO cut written · " + result.scene_count + " scenes · " + result.path);
    });
  };

  const inspectNativeProjection = () => {
    if (!selected || !renderReady) return;
    const key = preflightKey;
    void run("preflight", async () => {
      const report = await preflightMotionCanvas(project, selected.id, filmOptions());
      setPreflight({ key, report });
      setMessage(report.verdict === "projection_ready"
        ? "Semantic Film projection is supported at this revision. Native rendering is a separate operation."
        : "Semantic Film projection rejected this revision. No render or native job was dispatched.");
    });
  };

  const exportMaster = () => {
    if (!exportReady || !currentAv?.export_token) return;
    void run("export-master", async () => {
      const receipt = await exportVerifiedNativeMaster(
        project,
        currentAv.export_token!,
        masterDeliveryPath.trim(),
        includeMediaIntegrity,
      );
      onExportEvidence(receipt);
      setMessage("Verified MP4 delivered to " + receipt.destination
        + " · SHA-256 " + receipt.sha256.slice(0, 16) + "… · source r" + receipt.revision
        + (receipt.integrity_manifest_path
          ? " · portable verification receipt written"
          : ""));
    });
  };

  const assembleMaster = () => {
    if (!selected || !selectedVoice || !masterPreviewToken || !masterReady) return;
    void run("master", async () => {
      const result = await assembleNativeAvMaster(
        project, selected.id, selectedVoice.id, masterPreviewToken,
      );
      onAvEvidence(result);
      setMessage("Native H.264/AAC MP4 master recorded for r" + result.revision
        + " with " + result.frame_count + " native frames. Verify output metadata before distribution.");
    });
  };

  const startNativeRender = () => {
    if (!selected || !renderReady) return;
    void run("render", async () => {
      const result = await renderMotionCanvas(project, selected.id, filmOptions());
      onRenderEvidence(result);
      const frames = result.segments.reduce((sum, segment) => sum + segment.frame_count, 0);
      setMessage(
        "Native Motion Canvas evidence recorded · " + result.segments.length +
        " segment" + (result.segments.length === 1 ? "" : "s") +
        " · " + frames + " verified frames · revision r" + result.revision,
      );
    });
  };

  return (
    <section className="delivery-profile-workbench" aria-label="Delivery profiles">
      <header className="delivery-profile-heading">
        <div>
          <strong>Output profiles</strong>
          <span>Versioned intent for frame, language, codecs and portable captions.</span>
        </div>
        <div className="delivery-editor-actions">
          <button type="button" className="button" disabled={!selected || busy !== null} onClick={deriveProfile}>
            <Plus size={14} /> Derive selected
          </button>
          <button type="button" className="button" disabled={busy !== null} onClick={createProfile}>
            <Plus size={14} /> New profile
          </button>
        </div>
      </header>

      <div className="delivery-profile-layout">
        <div className="delivery-profile-index" role="list" aria-label="Delivery profile index">
          {project.deliverables.map((profile) => (
            <button
              type="button"
              role="listitem"
              key={profile.id}
              className={profile.id === selected?.id ? "delivery-profile-row selected" : "delivery-profile-row"}
              onClick={() => setSelectedId(profile.id)}
            >
              <span className="format-frame" style={{ aspectRatio: profile.width + " / " + profile.height }} />
              <span className="delivery-profile-row-copy">
                <strong>{profile.name}</strong>
                <span>{profile.width} × {profile.height} · {profile.language.toUpperCase()}</span>
              </span>
              <span className="delivery-codec">{codecLabel[profile.video_codec]}</span>
            </button>
          ))}
        </div>

        {draft && selected && (
          <div className="delivery-profile-editor">
            <div className="delivery-profile-editor-head">
              <div>
                <strong>{selected.name}</strong>
                <span className="mono">profile:{selected.id.slice(0, 8)}</span>
              </div>
              <span className={dirty ? "status-pill status-stale" : "status-pill status-current"}>
                {dirty ? "DIRTY" : "CURRENT"}
              </span>
            </div>

            {parentProfile && (
              <div className="delivery-truth-note">
                Derived from {parentProfile.name} · source revision r{draft.source_revision ?? "?"} · original remains independently inspectable.
              </div>
            )}

            <div className="delivery-field-grid">
              <label className="span-2">
                <span className="field-label">Profile name</span>
                <input value={draft.name} maxLength={160} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
              </label>
              <label>
                <span className="field-label">Width</span>
                <input type="number" min={1} max={16384} value={draft.width} onChange={(event) => setDraft({ ...draft, width: Number(event.target.value) })} />
              </label>
              <label>
                <span className="field-label">Height</span>
                <input type="number" min={1} max={16384} value={draft.height} onChange={(event) => setDraft({ ...draft, height: Number(event.target.value) })} />
              </label>
              <label>
                <span className="field-label">Language / locale</span>
                <input value={draft.language} maxLength={64} onChange={(event) => setDraft({ ...draft, language: event.target.value })} />
              </label>
              <label>
                <span className="field-label">Framing</span>
                <select
                  aria-label="Framing"
                  value={draft.framing_strategy}
                  onChange={(event) => {
                    const framing = event.target.value as DeliverableProfile["framing_strategy"];
                    setDraft({ ...draft, framing_strategy: framing, crop_approved: framing === "crop" ? draft.crop_approved : false });
                  }}
                >
                  <option value="replan">Replan composition</option>
                  <option value="crop">Approved crop</option>
                </select>
              </label>
              <label>
                <span className="field-label">Frame rate</span>
                <select
                  aria-label="Frame rate"
                  value={draft.frame_rate.num + "/" + draft.frame_rate.den}
                  onChange={(event) => {
                    const [num, den] = event.target.value.split("/");
                    setDraft({ ...draft, frame_rate: { num, den } });
                  }}
                >
                  <option value="24/1">24 fps</option>
                  <option value="25/1">25 fps</option>
                  <option value="30000/1001">29.97 fps</option>
                  <option value="30/1">30 fps</option>
                  <option value="60000/1001">59.94 fps</option>
                  <option value="60/1">60 fps</option>
                </select>
              </label>
              <label>
                <span className="field-label">Color space</span>
                <select value={draft.color_space} onChange={(event) => setDraft({ ...draft, color_space: event.target.value as DeliverableProfile["color_space"] })}>
                  <option value="rec709">Rec.709</option>
                  <option value="display_p3">Display P3</option>
                  <option value="rec2020">Rec.2020</option>
                </select>
              </label>
              <label>
                <span className="field-label">Container</span>
                <select value={draft.container} onChange={(event) => setDraft({ ...draft, container: event.target.value as DeliverableProfile["container"] })}>
                  <option value="mp4">MP4</option>
                  <option value="mov">MOV</option>
                  <option value="webm">WebM</option>
                  <option value="mkv">Matroska</option>
                </select>
              </label>
              <label>
                <span className="field-label">Video codec</span>
                <select value={draft.video_codec} onChange={(event) => setDraft({ ...draft, video_codec: event.target.value as DeliverableProfile["video_codec"] })}>
                  <option value="h264">H.264</option>
                  <option value="hevc">HEVC</option>
                  <option value="prores_422_hq">ProRes 422 HQ</option>
                  <option value="vp9">VP9</option>
                  <option value="av1">AV1</option>
                </select>
              </label>
              <label>
                <span className="field-label">Audio codec</span>
                <select value={draft.audio_codec} onChange={(event) => setDraft({ ...draft, audio_codec: event.target.value as DeliverableProfile["audio_codec"] })}>
                  <option value="aac">AAC</option>
                  <option value="pcm_s16_le">PCM 16-bit</option>
                  <option value="opus">Opus</option>
                </select>
              </label>
              <label>
                <span className="field-label">Sample rate</span>
                <select value={draft.audio_sample_rate_hz} onChange={(event) => setDraft({ ...draft, audio_sample_rate_hz: Number(event.target.value) })}>
                  <option value={44100}>44.1 kHz</option>
                  <option value={48000}>48 kHz</option>
                  <option value={96000}>96 kHz</option>
                </select>
              </label>
              <label>
                <span className="field-label">Voice take</span>
                <select
                  value={draft.voice_track_id ?? ""}
                  onChange={(event) => setDraft({ ...draft, voice_track_id: event.target.value || null })}
                >
                  <option value="">Unbound / single transcript only</option>
                  {project.audio.voice_tracks.map((track) => (
                    <option key={track.id} value={track.id}>{track.label} · {track.measured_duration.num}/{track.measured_duration.den}s</option>
                  ))}
                </select>
              </label>
              <label>
                <span className="field-label">Cut label</span>
                <input value={draft.cut_label ?? ""} maxLength={256} placeholder="e.g. social-short" onChange={(event) => setDraft({ ...draft, cut_label: event.target.value.trim() ? event.target.value : null })} />
              </label>
              <label>
                <span className="field-label">Brand profile</span>
                <input value={draft.brand_profile ?? ""} maxLength={256} placeholder="Optional" onChange={(event) => setDraft({ ...draft, brand_profile: event.target.value.trim() ? event.target.value : null })} />
              </label>
              <label className="delivery-inline-check">
                <input
                  type="checkbox"
                  checked={draft.timing_locked}
                  onChange={(event) => setDraft({ ...draft, timing_locked: event.target.checked })}
                />
                <span><strong>Lock source timing</strong><small>Reject changed measured VO duration instead of silently stretching it.</small></span>
              </label>
              <label className="delivery-inline-check">
                <input
                  type="checkbox"
                  checked={draft.crop_approved}
                  disabled={draft.framing_strategy !== "crop"}
                  onChange={(event) => setDraft({ ...draft, crop_approved: event.target.checked })}
                />
                <span><strong>Approve crop</strong><small>Required only when framing is explicitly set to crop.</small></span>
              </label>
              <label className="span-2">
                <span className="field-label">Adaptation notes · one decision per line</span>
                <textarea
                  rows={3}
                  value={draft.adaptation_notes.join("\n")}
                  placeholder="Why this variant differs from its parent."
                  onChange={(event) => setDraft({
                    ...draft,
                    adaptation_notes: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean),
                  })}
                />
              </label>
            </div>

            <section className="caption-export-panel" aria-label="Variant content">
              <header>
                <div>
                  <strong>Variant content</strong>
                  <span>Choose the narrative cut and protect scenes that must survive shortening.</span>
                </div>
                <button
                  type="button"
                  className="button"
                  onClick={() => setDraft({
                    ...draft,
                    included_scene_ids: draft.included_scene_ids.length > 0 ? [] : project.scenes.map((scene) => scene.id),
                  })}
                >
                  {draft.included_scene_ids.length > 0 ? "Use full sequence" : "Customize cut"}
                </button>
              </header>
              <div className="variant-scene-ledger">
                {project.scenes.map((scene) => {
                  const customCut = draft.included_scene_ids.length > 0;
                  const included = !customCut || draft.included_scene_ids.includes(scene.id);
                  const protectedScene = draft.protected_scene_ids.includes(scene.id);
                  return (
                    <div className="variant-scene-row" key={scene.id}>
                      <div>
                        <strong>{scene.name}</strong>
                        <span>{scene.objective}</span>
                      </div>
                      <label className="caption-toggle">
                        <input
                          type="checkbox"
                          checked={included}
                          disabled={!customCut}
                          onChange={(event) => {
                            const ids = event.target.checked
                              ? [...draft.included_scene_ids, scene.id]
                              : draft.included_scene_ids.filter((id) => id !== scene.id);
                            setDraft({ ...draft, included_scene_ids: ids });
                          }}
                        />
                        Include
                      </label>
                      <label className="caption-toggle">
                        <input
                          type="checkbox"
                          checked={protectedScene}
                          onChange={(event) => {
                            const ids = event.target.checked
                              ? [...draft.protected_scene_ids, scene.id]
                              : draft.protected_scene_ids.filter((id) => id !== scene.id);
                            setDraft({ ...draft, protected_scene_ids: ids });
                          }}
                        />
                        Protect
                      </label>
                    </div>
                  );
                })}
              </div>
              {draft.included_scene_ids.length > 0 && draft.protected_scene_ids.some((id) => !draft.included_scene_ids.includes(id)) && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> A protected scene is outside this cut. Saving will remain blocked until it is included.</div>
              )}
            </section>

            {(localizedVariant || Object.keys(draft.text_overrides).length > 0) && (
              <section className="caption-export-panel" aria-label="Localized text">
                <header>
                  <div>
                    <strong>Localized canvas text</strong>
                    <span>Every text object is explicit for a locale derivative; native verification still enforces no truncation.</span>
                  </div>
                  <span className={localizedVariant ? "status-pill status-unknown" : "status-pill status-current"}>
                    {localizedVariant ? "LOCALE VARIANT" : "OVERRIDES"}
                  </span>
                </header>
                <div className="localized-text-ledger">
                  {textNodes.map(({ scene, node }) => (
                    <label key={node.id}>
                      <span>
                        <strong>{scene.name} · {node.name}</strong>
                        <small>{node.text ?? "No source text"}</small>
                      </span>
                      <input
                        value={draft.text_overrides[node.id] ?? ""}
                        placeholder={localizedVariant ? "Required localized text" : "Optional override"}
                        onChange={(event) => {
                          const text_overrides = { ...draft.text_overrides };
                          if (event.target.value) text_overrides[node.id] = event.target.value;
                          else delete text_overrides[node.id];
                          setDraft({ ...draft, text_overrides });
                        }}
                      />
                    </label>
                  ))}
                </div>
                {localizedVariant && textNodes.some(({ node }) => !draft.text_overrides[node.id]?.trim()) && (
                  <div className="delivery-truth-note warning"><CircleDashed size={14} /> Localized derivatives require explicit text for every canvas text object.</div>
                )}
                {localizedVariant && parentProfile?.voice_track_id && draft.voice_track_id === parentProfile.voice_track_id && (
                  <div className="delivery-truth-note warning"><CircleDashed size={14} /> Choose a locale-specific voice take. Parent-language timing cannot be relabeled as localized evidence.</div>
                )}
              </section>
            )}

            <div className="delivery-editor-actions">
              <button type="button" className="button button-primary" disabled={!dirty || busy !== null} onClick={saveProfile}>
                <Save size={14} /> {busy === "save" ? "Saving…" : "Save profile"}
              </button>
              <button type="button" className="button" disabled={project.deliverables.length <= 1 || busy !== null} onClick={removeProfile}>
                <Trash2 size={14} /> Remove
              </button>
            </div>

            <section className="caption-export-panel" aria-label="Native Motion Canvas production">
              <header>
                <div>
                  <strong>Native Motion Canvas production</strong>
                  <span>Canonical Film → Semwright plan/apply → Driver Host render → native verification.</span>
                </div>
                <span className="status-pill status-current">SEMWRIGHT NATIVE</span>
              </header>

              <div className="delivery-field-grid">
                <label>
                  <span className="field-label">Saved frame rate</span>
                  <input value={selected.frame_rate.num + "/" + selected.frame_rate.den + " fps"} readOnly aria-readonly="true" />
                </label>
                <label>
                  <span className="field-label">Primary font</span>
                  <input value={fontFamily} readOnly aria-readonly="true" />
                </label>
                <label>
                  <span className="field-label">Mono font</span>
                  <input value={monoFontFamily} readOnly aria-readonly="true" />
                </label>
              </div>

              <div className="portable-project">
                <header className="portable-heading">
                  <div>
                    <strong>Scene intent</strong>
                    <span>Role and visual archetype are explicit authoring inputs; Motionwright does not infer them at render time.</span>
                  </div>
                  <span className={renderIntentsComplete ? "status-pill status-current" : "status-pill status-unknown"}>
                    {renderIntentsComplete ? "COMPLETE" : "REQUIRED"}
                  </span>
                </header>
                {motionScenes.map((scene) => {
                  const intent = sceneIntents[scene.id] ?? { role: "", archetype: "" };
                  return (
                    <div className="portable-operation" key={scene.id}>
                      <div className="portable-operation-copy">
                        <strong>{scene.name}</strong>
                        <span className="mono">scene:{scene.id.slice(0, 8)}</span>
                      </div>
                      <label>
                        <span className="field-label">Narrative role</span>
                        <select
                          aria-label={scene.name + " narrative role"}
                          value={intent.role}
                          disabled={busy !== null}
                          onChange={(event) => setSceneIntents((current) => ({
                            ...current,
                            [scene.id]: {
                              ...(current[scene.id] ?? { role: "", archetype: "" }),
                              role: event.target.value as MotionCanvasNarrativeRole | "",
                            },
                          }))}
                        >
                          <option value="">Choose role…</option>
                          {narrativeRoles.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                      </label>
                      <label>
                        <span className="field-label">Archetype</span>
                        <select
                          aria-label={scene.name + " motion archetype"}
                          value={intent.archetype}
                          disabled={busy !== null}
                          onChange={(event) => setSceneIntents((current) => ({
                            ...current,
                            [scene.id]: {
                              ...(current[scene.id] ?? { role: "", archetype: "" }),
                              archetype: event.target.value as MotionCanvasArchetype | "",
                            },
                          }))}
                        >
                          <option value="">Choose archetype…</option>
                          {motionArchetypes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                      </label>
                    </div>
                  );
                })}
              </div>

              <div className="delivery-editor-actions">
                <button
                  type="button"
                  className="button"
                  disabled={!renderReady || busy !== null}
                  onClick={inspectNativeProjection}
                >
                  <CircleDashed size={14} /> {busy === "preflight" ? "Checking Film…" : "Check Film projection"}
                </button>
                <button
                  type="button"
                  className="button button-primary"
                  disabled={!renderReady || busy !== null || currentPreflight?.verdict === "unsupported"}
                  onClick={startNativeRender}
                  title={currentPreflight?.verdict === "unsupported"
                    ? "The current semantic Film projection is rejected. Change the scene/profile and check again."
                    : "Start an authorized native render using the saved project revision."}
                >
                  <Play size={14} /> {busy === "render" ? "Rendering…" : "Render native segments"}
                </button>
              </div>
              {currentPreflight && (
                <div className={"delivery-truth-note " +
                  (currentPreflight.verdict === "unsupported" ? "warning" : "")}
                  role="status" aria-label="Native Film preflight result">
                  {currentPreflight.verdict === "projection_ready" ? (
                    <>
                      <CircleCheck size={14} aria-hidden="true" />
                      Film projection supported: {currentPreflight.segment_count} segment(s),
                      {" "}{currentPreflight.total_frames} planned frames at r{currentPreflight.revision}.
                      Renderer execution, actual pixels and AV mastering are not yet proven by this check.
                    </>
                  ) : (
                    <>
                      <CircleDashed size={14} aria-hidden="true" />
                      Film projection unsupported: {currentPreflight.reason ?? "Review scene constraints and output profile."}
                      No native job was dispatched.
                    </>
                  )}
                </div>
              )}

              {!desktopMode && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Native production requires the desktop runtime and an owner-provisioned Semwright connection.</div>
              )}
              {dirty && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Save the selected output profile before rendering it.</div>
              )}
              {selected.framing_strategy === "crop" && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> This profile uses an explicitly approved crop. Replan is the default for alternate aspect ratios.</div>
              )}
              {!nativePaletteReady && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> Native Motion Canvas production requires explicit ink/text and surface/background tokens in Visual language.</div>
              )}
              {motionScenes.length === 0 && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> This revision has no scenes assigned to Motion Canvas.</div>
              )}
              {visibleEvidence && (
                <div className="portable-plan" aria-label="Native render evidence">
                  <div><span>Revision</span><strong className="mono">r{visibleEvidence.revision}</strong></div>
                  <div><span>Segments</span><strong>{visibleEvidence.segments.length}</strong></div>
                  <div><span>Frames</span><strong>{visibleEvidence.segments.reduce((sum, segment) => sum + segment.frame_count, 0)}</strong></div>
                  <div><span>Verification</span><strong>{visibleEvidence.revision === project.revision ? "NATIVE · PASS" : "STALE · HISTORICAL"}</strong></div>
                </div>
              )}
            </section>

            <section className="caption-export-panel" aria-label="Canonical audiovisual mastering">
              <header>
                <div>
                  <strong>Native audiovisual master</strong>
                  <span>Verified Motion Canvas segment + measured source WAV → Semwright MLT → H.264/AAC MP4.</span>
                </div>
                <span className="status-pill status-unknown">OWNER-GRANTED</span>
              </header>
              <div className="delivery-editor-actions">
                <button className="button button-primary" type="button"
                  disabled={!masterReady || busy !== null}
                  onClick={assembleMaster}>
                  <Play size={14} />
                  {busy === "master" ? "Mastering…" : "Assemble native AV master"}
                </button>
              </div>
              {!masterPreviewToken && (
                <div className="delivery-truth-note">
                  <CircleDashed size={14} /> Complete a real native render of one contiguous Motion Canvas segment in this desktop session first.
                </div>
              )}
              {!verifiedWavIntent && (
                <div className="delivery-truth-note">
                  <CircleDashed size={14} /> Bind one saved, imported measured 48 kHz stereo WAV voice take to the output profile.
                </div>
              )}
              {masterPreviewToken && verifiedWavIntent && !voiceAligned && (
                <div className="delivery-truth-note warning">
                  <CircleDashed size={14} /> The measured voice duration must match the exact native video cut (within one 48 kHz sample). Edit the cut or re-author audio; no silent padding or retiming.
                </div>
              )}
              {!masterProfileSupported && (
                <div className="delivery-truth-note warning">
                  <CircleDashed size={14} /> This certified path requires H.264 video, AAC audio, 48 kHz, Rec.709 and MP4.
                </div>
              )}
              {currentAv && (
                <div className="portable-plan" aria-label="Native AV master evidence">
                  <div><span>Revision</span><strong className="mono">r{currentAv.revision}</strong></div>
                  <div><span>Frames</span><strong>{currentAv.frame_count}</strong></div>
                  <div><span>Native master</span><strong>{masterDigest
                    ? masterDigest.slice(0, 16) + "… SHA-256"
                    : "Recorded · inspect canonical result"}</strong></div>
                  <div><span>Applicability</span><strong>
                    {currentAv.revision === project.revision ? "CURRENT · NATIVE" : "STALE · HISTORICAL"}
                  </strong></div>
                </div>
              )}
              <div className="delivery-editor-actions">
                <button className="button" type="button"
                  disabled={!reviewAvailable || busy !== null}
                  onClick={() => setShowNativeAvReview((visible) => !visible)}>
                  <Play size={14} aria-hidden="true" />
                  {showNativeAvReview && reviewAvailable ? "Close native review" : "Review native MP4"}
                </button>
              </div>
              {showNativeAvReview && reviewAvailable && currentAv?.export_token && (
                <NativeAvReview
                  key={project.id + ":" + project.revision + ":" + currentAv.export_token}
                  project={project}
                  exportToken={currentAv.export_token}
                />
              )}
              <div className="delivery-truth-note">
                <CircleDashed size={14} /> Native master playback is an explicit, SHA-256-verified local review action limited to 16 MiB. Larger MP4 files must be exported for external playback. Decoder compatibility, timeline synchronization and human mix approval remain separate.
              </div>
            </section>

            <section className="caption-export-panel" aria-label="Verified native MP4 delivery">
              <header>
                <div>
                  <strong>Deliver verified MP4</strong>
                  <span>Copy the completed native H.264/AAC master with SHA-256 verification. Existing files are never overwritten.</span>
                </div>
                <span className="status-pill status-unknown">LOCAL · UNPUBLISHED</span>
              </header>
              <div className="caption-controls">
                <label className="caption-path">
                  <span className="field-label">Destination · absolute .mp4 path</span>
                  <input type="text"
                    aria-label="Verified MP4 export path"
                    value={masterDeliveryPath}
                    disabled={!desktopMode}
                    placeholder="/absolute/path/final-master.mp4"
                    onChange={(event) => setMasterDeliveryPath(event.target.value)}
                  />
                </label>
                <button className="button button-primary" type="button"
                  disabled={!exportReady || busy !== null}
                  onClick={exportMaster}>
                  <FileOutput size={14} />
                  {busy === "export-master" ? "Verifying…" : "Export verified MP4"}
                </button>
              </div>
              <label className="caption-toggle">
                <input type="checkbox"
                  aria-label="Write portable MP4 integrity receipt"
                  checked={includeMediaIntegrity}
                  disabled={!desktopMode}
                  onChange={(event) => setIncludeMediaIntegrity(event.target.checked)}
                />
                Write portable SHA-256 verification receipt (.json) next to the MP4
              </label>
              <div className="delivery-truth-note">
                <CircleDashed size={14} /> Optional receipt checks file bytes and source revision offline.
                It is unsigned: it does not prove publisher identity, codec quality or human approval.
              </div>
              {!currentAv?.export_token && (
                <div className="delivery-truth-note">
                  <CircleDashed size={14} /> Complete a real owner-authorized MLT AV master in this desktop session first. No arbitrary master paths are accepted.
                </div>
              )}
              {currentAv && currentAv.revision !== project.revision && (
                <div className="delivery-truth-note warning">
                  <CircleDashed size={14} /> Previous MP4 master belongs to revision r{currentAv.revision}. Re-render current creative changes before exporting.
                </div>
              )}
              {currentExport && (
                <div className="portable-plan" aria-label="Verified MP4 delivery receipt">
                  <div><span>Destination</span><strong className="mono">{currentExport.destination}</strong></div>
                  <div><span>Bytes copied</span><strong>{currentExport.size_bytes}</strong></div>
                  <div><span>SHA-256</span><strong className="mono">{currentExport.sha256.slice(0, 16)}…</strong></div>
                  {currentExport.integrity_manifest_path && (
                    <div><span>Integrity JSON</span><strong className="mono">{currentExport.integrity_manifest_path}</strong></div>
                  )}
                  <div><span>Status</span><strong>{currentExport.source_current
                    && currentExport.revision === project.revision ? "VERIFIED · CURRENT" : "VERIFIED · HISTORICAL"}</strong></div>
                </div>
              )}
              <div className="delivery-truth-note">
                <CircleDashed size={14} /> The MP4 copy is local and SHA-256 verified. No upload, external publication, human review or media signing is performed.
              </div>
            </section>

            <section className="caption-export-panel" aria-label="Caption sidecar export">
              <header>
                <div>
                  <strong>Caption sidecar</strong>
                  <span>Generated only from transcript segments with known timing evidence.</span>
                </div>
                <div className="caption-toggle-group">
                  <label className="caption-toggle">
                    <input type="checkbox" checked={draft.captions} onChange={(event) => setDraft({ ...draft, captions: event.target.checked })} />
                    Sidecar
                  </label>
                  <label className="caption-toggle">
                    <input
                      type="checkbox"
                      checked={draft.burn_in_captions}
                      disabled={!draft.captions}
                      onChange={(event) => setDraft({ ...draft, burn_in_captions: event.target.checked })}
                    />
                    Burn-in
                  </label>
                </div>
              </header>
              <div className="caption-controls">
                <label>
                  <span className="field-label">Format</span>
                  <select disabled={!draft.captions} value={draft.caption_format} onChange={(event) => {
                    const format = event.target.value as DeliverableProfile["caption_format"];
                    setDraft({ ...draft, caption_format: format });
                    setSidecarPath("");
                  }}>
                    <option value="web_vtt">WebVTT</option>
                    <option value="srt">SubRip (.srt)</option>
                  </select>
                </label>
                <label className="caption-path">
                  <span className="field-label">Destination · absolute path</span>
                  <input
                    value={sidecarPath}
                    disabled={!desktopMode || !draft.captions}
                    placeholder={draft.caption_format === "srt" ? "/absolute/path/captions.srt" : "/absolute/path/captions.vtt"}
                    onChange={(event) => setSidecarPath(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button"
                  disabled={!desktopMode || !captionReady || dirty || !sidecarPath.trim() || busy !== null}
                  onClick={exportCaptions}
                >
                  <FileOutput size={14} /> {busy === "caption" ? "Writing…" : "Export sidecar"}
                </button>
              </div>
              {!desktopMode && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Desktop filesystem capability is required.</div>
              )}
              {draft.captions && profileTranscript.length === 0 && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> The selected voice take has no transcript segments; no caption file is implied.</div>
              )}
              {draft.captions && !draft.voice_track_id && transcriptTrackCount > 1 && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> Multiple transcript tracks exist. Bind this profile to one voice take before exporting captions.</div>
              )}
              {draft.captions && unknownTimings > 0 && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> {unknownTimings} transcript segment{unknownTimings === 1 ? "" : "s"} still have UNKNOWN timing.</div>
              )}
              {dirty && <div className="delivery-truth-note">Save the profile before exporting its sidecar.</div>}
            </section>

            <section className="caption-export-panel" aria-label="OpenTimelineIO export">
              <header>
                <div>
                  <strong>OpenTimelineIO interchange</strong>
                  <span>Conservative linear cut only. Unsupported Motionwright semantics are returned as a loss report.</span>
                </div>
                <span className="status-pill status-unknown">LOSS-AWARE</span>
              </header>
              <div className="caption-controls">
                <label className="caption-path">
                  <span className="field-label">Destination · absolute .otio path</span>
                  <input
                    aria-label="OpenTimelineIO export path"
                    value={otioPath}
                    disabled={!desktopMode}
                    placeholder="/absolute/path/project.otio"
                    onChange={(event) => setOtioPath(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button"
                  disabled={!desktopMode || !otioPath.trim() || busy !== null}
                  onClick={exportTimeline}
                >
                  <FileOutput size={14} /> {busy === "otio" ? "Writing…" : "Export .otio"}
                </button>
              </div>
              {!desktopMode && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Desktop filesystem capability is required; browser demo does not fabricate an OTIO file.</div>
              )}
              {otioLosses.length > 0 && (
                <div className="otio-loss-report" role="status" aria-label="OTIO loss report">
                  <strong>Loss report · {otioLosses.length}</strong>
                  <ul>{otioLosses.map((loss) => <li key={loss}>{loss}</li>)}</ul>
                </div>
              )}
            </section>

            {error && <div className="portable-message error" role="alert"><strong>Delivery operation blocked.</strong><span>{error}</span></div>}
            {message && <div className="portable-message success" role="status"><CircleCheck size={15} /><span>{message}</span></div>}
          </div>
        )}
      </div>
    </section>
  );
}
