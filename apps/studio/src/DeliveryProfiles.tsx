import { CircleCheck, CircleDashed, FileOutput, Play, Plus, Save, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import NativeAvReview from "./NativeAvReview";
import {
  evaluateFirstNativeRenderGate, firstRenderRuntimeScope,
  type ScopedRuntimeObservation,
} from "./firstNativeProduction";
import VariantContinuityReview from "./VariantContinuityReview";
import DeliveryVariantImpactReview from "./DeliveryVariantImpactReview";
import CaptionReadabilityReview from "./CaptionReadabilityReview";
import { useDeliveryRecipeDraft } from "./deliveryRecipe";
import {
  captureDeliveryDispatch, useProfileMetadataDraft, retainedProfileMetadataCopies,
  type DeliveryProjectState, type RetainedProfileMetadataCopy,
} from "./deliveryProfileDraft";
import {
  assembleNativeAvMaster,
  assembleNativeMultisegmentAvMaster,
  exportCaptionSidecar,
  exportOtio,
  exportVerifiedNativeMaster,
  verifyPortableMediaReceipt,
  preflightMotionCanvas,
  preflightMultiSegmentReadiness,
  renderMotionCanvas,
  productionRuntimeCompatibility,
} from "./api";
import type {
  Change,
  DeliverableProfile,
  MotionCanvasArchetype,
  MotionCanvasNarrativeRole,
  MotionCanvasRenderEvidence,
  MotionCanvasProjectionPreflight,
  MultiSegmentReadinessReport,
  MotionCanvasFilmOptions,
  MltAvMasterEvidence,
  NativeMultisegmentAvMasterEvidence,
  MasterExportReceipt,
  PortableMediaVerification,
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

type RecipeDraftController = ReturnType<typeof useDeliveryRecipeDraft>;
type RecipeFieldDescription = { label: string; controlId?: string };

/** Keep Space's native control action from also starting Studio playback. */
export function containRecipeKeyboard(event: KeyboardEvent<HTMLElement>) {
  if (event.key === " " && event.target instanceof HTMLElement
      && event.target.closest("button, input, textarea, select, summary, audio, video")) event.stopPropagation();
}

/** Shared review of local inputs. Only the existing commit path changes a recipe. */
export function RecipeDraftReview({
  controller, title, revision, fields, saveLabel, clearLabel, hasSavedGroup, blockedReason, onDraftAction,
}: {
  controller: RecipeDraftController;
  title: string;
  revision: number;
  fields: Record<string, RecipeFieldDescription>;
  saveLabel: string;
  clearLabel: string;
  hasSavedGroup: boolean;
  blockedReason?: string | null;
  onDraftAction?: () => void;
}) {
  const review = useRef<HTMLElement>(null);
  const status = useRef<HTMLParagraphElement>(null);
  const label = (field: string) => fields[field]?.label ?? field.replaceAll("_", " ");
  const afterAction = (action: () => void, field?: string) => {
    onDraftAction?.();
    action();
    requestAnimationFrame(() => {
      if (!review.current?.isConnected) return;
      const controlId = field ? fields[field]?.controlId : undefined;
      const control = controlId ? document.getElementById(controlId) : null;
      if (control && !(control as HTMLInputElement).disabled) control.focus();
      else status.current?.focus();
    });
  };
  const missingControls = Object.keys(controller.edits).filter(field => !fields[field]?.controlId
    && !controller.conflicts.includes(field));
  const pendingControls = Object.keys(controller.edits).filter(field => fields[field]?.controlId
    && !controller.conflicts.includes(field));
  const cannotSave = !controller.dirty || controller.pending || controller.locked
    || controller.sourceBehind || controller.recoveryBlocked || controller.conflicts.length > 0
    || Boolean(blockedReason);
  const cannotReplace = controller.dirty || controller.awaiting || controller.pending
    || controller.recoveryBlocked || controller.sourceBehind;
  const showValue = (value: string | undefined) => value === "" || value === undefined ? "Not set" : value;
  const comparison = (field: string) => <div className="portable-plan"
    style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 150px), 1fr))" }}>
    {([
      ["Before", controller.edits[field]?.base],
      ["Saved now", controller.savedValues[field]],
      ["My draft", controller.edits[field]?.value],
    ] as const).map(([heading, value]) => <div key={heading}>
      <span>{heading}</span>
      <p style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 12 }}>{showValue(value)}</p>
    </div>)}
  </div>;
  return <section ref={review} aria-label={title + " draft review"} onKeyDown={containRecipeKeyboard}>
    <p ref={status} tabIndex={-1} role="status" className="production-help">
      {controller.pending ? "Save requested; waiting for the project service. You may continue editing recipe inputs."
        : controller.awaiting ? "A save was requested. Inputs remain pending until they agree with a newer saved project or you resolve them explicitly."
        : controller.dirty ? Object.keys(controller.edits).length + " recipe field(s) have pending input."
        : "Showing saved recipe values at revision r" + revision + "."}
    </p>
    {controller.locked && <p className="portable-message error">The project content lock blocks recipe changes.</p>}
    {controller.sourceBehind && <p className="portable-message error">This draft was edited against a newer project revision. Reopen the current saved project before changing or saving these inputs.</p>}
    {blockedReason && <p className="production-help">{blockedReason}</p>}
    {controller.error && <p className="portable-message error" role="alert">{controller.error}</p>}
    {controller.message && <p className="production-help" role="status">{controller.message}</p>}
    {controller.recoveryBlocked && <div className="portable-operation-copy">
      <p className="production-help">The unreadable copy is retained. Saving and replacing this draft are blocked until you explicitly discard that copy; new input stays in this window.</p>
      <button type="button" className="button" disabled={controller.pending}
        onClick={() => afterAction(controller.discardUnreadableCopy)}>
        Discard unreadable {title.toLowerCase()} copy
      </button>
    </div>}
    {controller.conflicts.length > 0 && <section aria-label={title + " conflicts"}>
      <p className="production-help">The saved project changed these fields. Choose each value before saving; resolving a conflict does not save it.</p>
      {controller.conflicts.map(field => <div key={field} aria-label={label(field) + " conflict"} role="group">
        <strong>{label(field)}</strong>
        {comparison(field)}
        <div className="delivery-editor-actions">
          <button type="button" className="button" disabled={controller.pending || controller.sourceBehind}
            onClick={() => afterAction(() => controller.resolve(field, "saved"), field)}>
            Keep saved {label(field)}
          </button>
          <button type="button" className="button" disabled={controller.pending || controller.sourceBehind}
            onClick={() => afterAction(() => controller.resolve(field, "draft"), field)}>
            Keep my draft {label(field)}
          </button>
        </div>
      </div>)}
    </section>}
    {missingControls.length > 0 && <section aria-label={title + " inputs outside the current view"}>
      <p className="production-help">These pending fields are outside the current scene selection or refer to a removed source. They are retained until you explicitly discard them.</p>
      {missingControls.map(field => <div key={field}>
        <strong>{label(field)}</strong>
        {comparison(field)}
        <button type="button" className="button" disabled={controller.pending || controller.sourceBehind}
          onClick={() => afterAction(() => controller.resolve(field, "saved"))}>
          Discard pending {label(field)}
        </button>
      </div>)}
    </section>}
    {pendingControls.length > 0 && <details aria-label={title + " pending fields"}>
      <summary>Review pending fields ({pendingControls.length})</summary>
      {pendingControls.map(field => <div key={field}>
        <strong>{label(field)}</strong>
        {comparison(field)}
        <button type="button" className="button" disabled={controller.pending || controller.sourceBehind}
          onClick={() => afterAction(() => controller.resolve(field, "saved"), field)}>
          Keep saved {label(field)}
        </button>
      </div>)}
    </details>}
    <div className="delivery-editor-actions">
      <button type="button" className="button button-primary" disabled={cannotSave}
        onClick={() => { onDraftAction?.(); void controller.save(); }}><Save size={14} aria-hidden="true" />
        {controller.pending ? "Saving recipe…" : saveLabel}
      </button>
      <button type="button" className="button"
        disabled={!hasSavedGroup || cannotReplace || controller.locked || Boolean(blockedReason)}
        onClick={() => afterAction(controller.clearGroup)}><Trash2 size={14} aria-hidden="true" />{clearLabel}</button>
    </div>
    <p className="production-help">Clearing creates a pending recipe change; save it explicitly. Recovery stores authoring inputs in this browser profile, with a 256 KiB copy limit and a shared 64-copy / 2 MiB budget. It does not store execution grants or review approval.</p>
    <details aria-label={title + " recovery copies"}>
      <summary>Recovery copies ({controller.copies.length})</summary>
      <p className="production-help">Copies from other editing windows are listed for this project, branch and delivery profile. They are applied only when you choose Recover.</p>
      <button type="button" className="button" onClick={() => afterAction(controller.refreshCopies)}>Refresh {title.toLowerCase()} copies</button>
      {controller.copies.length === 0 && <p className="production-help">No separate copy is currently listed.</p>}
      {controller.copies.map((copy, index) => <div key={copy.key} className="portable-operation"
        style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 180px), 1fr))" }}>
        <div className="portable-operation-copy">
          <strong>Copy {index + 1}</strong>
          <span>{copy.readable ? "Revision r" + copy.revision + " · " + copy.fields + " pending field(s)" : "Unreadable copy · original bytes retained"}</span>
        </div>
        <div className="delivery-editor-actions">
          <button type="button" className="button" disabled={!copy.readable || cannotReplace}
            onClick={() => afterAction(() => controller.recoverCopy(copy.key))}>Recover {title.toLowerCase()} copy {index + 1}</button>
          <button type="button" className="button" disabled={controller.pending}
            onClick={() => afterAction(() => controller.discardCopy(copy.key))}>Discard {title.toLowerCase()} copy {index + 1}</button>
        </div>
      </div>)}
    </details>
  </section>;
}

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

function RetainedProfileCopyReview({ copy }: { copy: RetainedProfileMetadataCopy }) {
  const [open, setOpen] = useState(false), [limit, setLimit] = useState(20);
  const [error, setError] = useState<string | null>(null);
  const fields = Object.entries(copy.draft?.edits ?? {});
  const previewLimit = 16_384;
  return <details data-retained-profile-id={copy.profileId} data-retained-origin={copy.source}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Profile {copy.profileId.slice(0, 8)} · {copy.source === "window" ? "window input" : "recovery copy"}
      {copy.draft ? " · r" + copy.draft.revision + " · " + fields.length + " field(s)" : " · unreadable original retained"}</summary>
    {open && <>
      <p className="production-help">Removed profile {copy.profileId}. This snapshot is separate from the saved Project.</p>
      {copy.warning && <p className="portable-message error" role="status">{copy.warning}</p>}
      {copy.draft?.awaiting && <p className="portable-message error" role="status">This copy contains unconfirmed submitted input. It does not prove a save or authorize a retry.</p>}
      {copy.draft ? <div style={{ display: "grid", gap: 12 }}>
        {fields.slice(0, limit).map(([field, edit]) => {
          const label = field.replaceAll("_", " ");
          const sent = copy.draft?.awaiting?.fields[field];
          return <div key={field}>
            <label style={{ display: "grid", gap: 5 }}>
              <span className="field-label">{label} · retained input</span>
              <textarea aria-label={label + " retained input"} readOnly rows={2} value={edit.value.slice(0, previewLimit)}
                style={{ resize: "vertical", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }} />
            </label>
            {edit.value.length > previewLimit && <p className="production-help">Showing the first {previewLimit.toLocaleString()} characters. The download contains the complete input.</p>}
            <details><summary>Saved value before editing{sent ? " and submitted input" : ""}</summary>
              <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>Saved before: {edit.base.slice(0, previewLimit)}</p>
              {sent && <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>Input sent: {sent.raw.slice(0, previewLimit)}</p>}
            </details>
          </div>;
        })}
        {fields.length > limit && <button type="button" className="button" onClick={() => setLimit(value => value + 20)}>Show more retained fields</button>}
      </div> : <>
        <p className="portable-message error">The recovery format could not be read. The original bytes remain available as a snapshot; they cannot be applied as profile settings.</p>
        <textarea aria-label="Unreadable retained profile copy" readOnly rows={4} value={copy.raw.slice(0, previewLimit)}
          style={{ resize: "vertical", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }} />
        {copy.raw.length > previewLimit && <p className="production-help">Showing the first {previewLimit.toLocaleString()} characters. The download contains the complete original copy.</p>}
      </>}
      <button type="button" className="button" onClick={() => {
        try {
          const url = URL.createObjectURL(new Blob([copy.raw], { type: "application/json" }));
          const anchor = document.createElement("a");
          anchor.href = url; anchor.download = "motionwright-profile-recovery-" + copy.profileId + "-" + copy.source + ".json";
          anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
          setError(null);
        } catch (failure) { setError("The retained snapshot could not be downloaded: " + String(failure)); }
      }}>Download retained profile copy</button>
      {error && <p className="portable-message error" role="alert">{error}</p>}
    </>}
  </details>;
}

function RetainedProfileDrafts({ project }: { project: Project }) {
  const [epoch, setEpoch] = useState(0), [limit, setLimit] = useState(12);
  const view = useMemo(() => retainedProfileMetadataCopies(project), [project, epoch]);
  useEffect(() => {
    const refresh = () => setEpoch(value => value + 1);
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, []);
  if (!view.copies.length && !view.warning) return null;
  return <section className="caption-export-panel" aria-label="Retained output profile drafts">
    <header><div><strong>Retained output profile drafts</strong>
      <span>Input from removed profiles remains available for review. A matching profile must be restored before its input can be edited or saved.</span></div></header>
    <p className="production-help">Only this project generation and branch are shown. Download preserves the exact snapshot displayed here. It does not recreate a profile or start production.</p>
    {view.warning && <p className="portable-message error" role="alert">{view.warning}</p>}
    <button type="button" className="button" onClick={() => setEpoch(value => value + 1)}>Refresh retained profile copies</button>
    {view.copies.slice(0, limit).map(copy => <RetainedProfileCopyReview key={copy.source + ":" + copy.key} copy={copy} />)}
    {view.copies.length > limit && <button type="button" className="button" onClick={() => setLimit(value => value + 12)}>Show more retained profile drafts</button>}
  </section>;
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
  onOpenAudio,
  currentProjectState,
}: {
  project: Project;
  commit: Commit;
  desktopMode: boolean;
  onOpenAudio: () => void;
  currentProjectState: () => DeliveryProjectState;
  renderEvidence: MotionCanvasRenderEvidence | null;
  onRenderEvidence: (evidence: MotionCanvasRenderEvidence) => void;
  avEvidence: MltAvMasterEvidence | null;
  onAvEvidence: (evidence: MltAvMasterEvidence) => void;
  exportEvidence: MasterExportReceipt | null;
  onExportEvidence: (receipt: MasterExportReceipt) => void;
}) {
  const [nativeInputSerial, setNativeInputSerial] = useState(0);
  const nativeInputSerialRef = useRef(0);
  const noteNativeInput = useCallback(() => {
    const next = ++nativeInputSerialRef.current;
    setNativeInputSerial(next);
  }, []);
  const [selectedId, setSelectedIdValue] = useState(() =>
    project.deliverables.find((profile) => profile.id === renderEvidence?.deliverable_id)?.id
      ?? project.deliverables[0]?.id ?? "");
  const setSelectedId = useCallback((id: string) => {
    noteNativeInput();
    setSelectedIdValue(id);
  }, [noteNativeInput]);
  const selected = project.deliverables.find((profile) => profile.id === selectedId) ?? project.deliverables[0] ?? null;
  const profileDraft = useProfileMetadataDraft(project, selected?.id ?? "", commit, currentProjectState);
  const draft = profileDraft.profile;
  const customCut = profileDraft.values.cut_mode === "custom";
  const emptyCustomCut = customCut && draft?.included_scene_ids.length === 0;
  const setCut = (mode: "full" | "custom", ids: string[]) => {
    noteNativeInput();
    profileDraft.setFields({ included_scene_ids: JSON.stringify(ids), cut_mode: mode });
  };
  const setDraft = (value: DeliverableProfile) => {
    noteNativeInput();
    profileDraft.setPreview(value);
  };
  const setProfileField = (field: string, value: string) => {
    noteNativeInput();
    profileDraft.set(field, value);
  };
  const [sidecarPath, setSidecarPath] = useState("");
  const [otioPath, setOtioPath] = useState("");
  const [masterDeliveryPath, setMasterDeliveryPath] = useState("");
  const [includeMediaIntegrity, setIncludeMediaIntegrity] = useState(false);
  const [verifyManifestPath, setVerifyManifestPath] = useState("");
  const [trustedMediaSha256, setTrustedMediaSha256] = useState("");
  const [verifiedMedia, setVerifiedMedia] = useState<PortableMediaVerification | null>(null);
  const [showNativeAvReview, setShowNativeAvReview] = useState(false);
  const [multiMaster, setMultiMaster] = useState<NativeMultisegmentAvMasterEvidence | null>(null);
  const [otioLosses, setOtioLosses] = useState<string[]>([]);
  const fontFamily = "Instrument Sans Variable";
  const monoFontFamily = "IBM Plex Mono";
  const filmDraft = useDeliveryRecipeDraft(project, selected?.id ?? "", "film", commit);
  const visibleEvidence = renderEvidence?.generation === project.generation
    && renderEvidence.deliverable_id === selected?.id ? renderEvidence : null;
  const [busy, setBusy] = useState<"save" | "remove" | "caption" | "otio" | "render" | "preflight" | "multi-source" | "master" | "multi-master" | "export-master" | "verify-media" | "new" | null>(null);
  const [preflight, setPreflight] = useState<{
    key: string;
    report: MotionCanvasProjectionPreflight;
  } | null>(null);
  const [multiSource, setMultiSource] = useState<{
    key: string;
    report: MultiSegmentReadinessReport;
  } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runtimeObservation, setRuntimeObservation] = useState<ScopedRuntimeObservation | null>(null);
  const [runtimeProbeBusy, setRuntimeProbeBusy] = useState(false);
  const [runtimeProbeSerial, setRuntimeProbeSerial] = useState(0);

  // Read-only CLI version/digest observation scoped to the exact creative
  // revision. It neither contacts a rendering provider nor grants execution.
  useEffect(() => {
    let active = true;
    const scope = firstRenderRuntimeScope(project);
    setRuntimeObservation(null);
    setRuntimeProbeBusy(desktopMode);
    if (desktopMode) {
      void productionRuntimeCompatibility(project)
        .then((value) => { if (active) setRuntimeObservation({ scope, value }); })
        .catch(() => { if (active) setRuntimeObservation(null); })
        .finally(() => { if (active) setRuntimeProbeBusy(false); });
    }
    return () => { active = false; };
  }, [project.id, project.generation, project.active_branch, project.revision, desktopMode, runtimeProbeSerial]);

  useEffect(() => {
    const current = project.deliverables.find((profile) => profile.id === selectedId) ?? project.deliverables[0] ?? null;
    if (current && current.id !== selectedId) setSelectedId(current.id);
    setSidecarPath("");
    setOtioLosses([]);

  }, [project.id, project.generation, project.active_branch, project.revision, selectedId, setSelectedId]);

  const motionScenes = useMemo(() => {
    const selectedSceneIds = selected?.included_scene_ids ?? [];
    const included = selectedSceneIds.length > 0 ? new Set(selectedSceneIds) : null;
    return project.scenes.filter(
      (scene) => scene.renderer === "motion-canvas" && (!included || included.has(scene.id)),
    );
  }, [project.scenes, selected]);

  const textNodes = useMemo(() => {
    const selectedSceneIds = draft?.included_scene_ids ?? [];
    const included = customCut ? new Set(selectedSceneIds) : null;
    return project.scenes
      .filter((scene) => !included || included.has(scene.id))
      .flatMap((scene) =>
        scene.nodes
          .filter((node) => node.kind === "text")
          .map((node) => ({ scene, node })),
      );
  }, [draft?.included_scene_ids, project.scenes, customCut]);

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
  const dirty = profileDraft.dirty;
  const profileDraftBlocked = dirty || emptyCustomCut || profileDraft.awaiting || profileDraft.pending
    || profileDraft.conflicts.length > 0 || profileDraft.recoveryBlocked || profileDraft.sourceBehind;
  const captionReady = Boolean(
    draft?.captions &&
    profileTranscript.length > 0 &&
    unknownTimings === 0 &&
    (draft.voice_track_id !== null || transcriptTrackCount <= 1),
  );
  const filmDraftBlocked = filmDraft.dirty || filmDraft.awaiting || filmDraft.pending
    || filmDraft.conflicts.length > 0 || filmDraft.recoveryBlocked || filmDraft.sourceBehind;
  const renderIntentsComplete = motionScenes.length > 0 && motionScenes.every((scene) =>
    narrativeRoles.some(option => option.value === filmDraft.savedValues[scene.id + ":role"])
      && motionArchetypes.some(option => option.value === filmDraft.savedValues[scene.id + ":archetype"]));
  const filmFields: Record<string, RecipeFieldDescription> = Object.fromEntries(project.scenes.flatMap(scene => {
    const visible = motionScenes.some(item => item.id === scene.id);
    return [
      [scene.id + ":role", { label: scene.name + " narrative role", ...(visible ? { controlId: "film-" + selected?.id + "-" + scene.id + "-role" } : {}) }],
      [scene.id + ":archetype", { label: scene.name + " motion archetype", ...(visible ? { controlId: "film-" + selected?.id + "-" + scene.id + "-archetype" } : {}) }],
    ];
  }));
  const nativePaletteReady = useMemo(() => {
    const names = new Set(project.visual_language.palette.map((token) => token.name.toLowerCase()));
    return (names.has("text") || names.has("ink")) &&
      (names.has("background") || names.has("surface"));
  }, [project.visual_language.palette]);
  const renderReady = Boolean(
    desktopMode &&
    selected &&
    !profileDraftBlocked &&
    !filmDraftBlocked &&
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
  const multiSourceToken = currentMotion && currentMotion.segments.length > 1
    ? currentMotion.preview?.find((grant) => (
      grant.segment_id === currentMotion.segments[0].segment_id
    ))?.token ?? null
    : null;
  const multiSourceKey = [
    project.id, project.generation, project.revision, selected?.id, multiSourceToken,
  ].join(":");
  const currentMultiSource = multiSource?.key === multiSourceKey ? multiSource.report : null;
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
  const masterReady = Boolean(desktopMode && selected && !profileDraftBlocked && !filmDraftBlocked
    && verifiedWavIntent && voiceAligned && masterPreviewToken && masterProfileSupported);
  const currentAv = avEvidence?.generation === project.generation
    && avEvidence.deliverable_id === selected?.id ? avEvidence : null;
  const currentMultiMaster = multiMaster?.generation === project.generation
    && multiMaster.deliverable_id === selected?.id ? multiMaster : null;
  const multiSourceFrames = currentMotion?.segments.reduce(
    (sum, segment) => sum + segment.frame_count, 0,
  ) ?? 0;
  const multiVideoSeconds = currentMotion?.segments.length && currentMotion.segments.length > 1
    ? multiSourceFrames / motionFrameRate : Number.NaN;
  const multiVoiceAligned = Number.isFinite(voiceDurationSeconds)
    && Number.isFinite(multiVideoSeconds)
    && Math.abs(voiceDurationSeconds - multiVideoSeconds) <= 1 / 48_000;
  const multiMasterReady = Boolean(desktopMode && selected && !profileDraftBlocked && !filmDraftBlocked
    && verifiedWavIntent && multiVoiceAligned && multiSourceToken
    && currentMultiSource && currentMultiSource.total_frames === multiSourceFrames
    && masterProfileSupported);
  // A captioned master may be inspected from a verified SHA-bound owner
  // token, but its decoded production glyphs were NOT attested by the
  // synthetic CI fixture. Never promote a review token to export authority.
  const activeReviewToken = currentMotion?.segments.length && currentMotion.segments.length > 1
    ? currentMultiMaster?.revision === project.revision
      ? currentMultiMaster.export_token : null
    : currentAv?.revision === project.revision ? currentAv.export_token : null;
  const activeExportToken = currentMotion?.segments.length && currentMotion.segments.length > 1
    && (currentMultiMaster?.review_only
      || currentMultiMaster?.evidence_scope === "native-captioned-multisegment-av-review-only-not-decoded-pixel-approved")
    ? null : activeReviewToken;
  const masterArtifact = currentAv?.master.artifact as Record<string, unknown> | undefined;
  const masterDigest = typeof masterArtifact?.sha256 === "string" ? masterArtifact.sha256 : null;
  const currentExport = exportEvidence?.deliverable_id === selected?.id ? exportEvidence : null;
  const reviewAvailable = Boolean(desktopMode && selected && !profileDraftBlocked
    && Boolean(activeReviewToken));
  const exportReady = Boolean(desktopMode && selected && !profileDraftBlocked && !filmDraftBlocked
    && Boolean(activeExportToken)
    && masterDeliveryPath.trim().toLowerCase().endsWith(".mp4"));

  const preflightKey = JSON.stringify([
    project.id,
    project.generation,
    project.active_branch,
    project.revision,
    selected?.id,
    selected?.frame_rate,
    motionScenes.map((scene) => scene.id),
    filmDraft.savedValues,
    fontFamily,
    monoFontFamily,
  ]);
  const currentPreflight = !filmDraftBlocked && preflight?.key === preflightKey ? preflight.report : null;
  const liveFilm = useRef({ key: preflightKey, ready: renderReady, mounted: true });
  liveFilm.current.key = preflightKey;
  liveFilm.current.ready = renderReady;
  useEffect(() => {
    liveFilm.current.mounted = true;
    return () => { liveFilm.current.mounted = false; };
  }, []);
  type NativeAction = "render" | "master" | "multi-master" | "export";
  const nativeActionKeys: Record<NativeAction, string> = {
    render: preflightKey,
    master: JSON.stringify([selectedVoice?.id, masterPreviewToken]),
    "multi-master": JSON.stringify([selectedVoice?.id, multiSourceToken, multiSourceKey, currentMultiSource?.total_frames]),
    export: JSON.stringify([activeExportToken, masterDeliveryPath, includeMediaIntegrity]),
  };
  const nativeReadiness: Record<NativeAction, boolean> = {
    render: renderReady, master: masterReady, "multi-master": multiMasterReady, export: exportReady,
  };
  const liveNative = useRef({
    project, profileId: selected?.id ?? null, inputSerial: nativeInputSerial,
    keys: nativeActionKeys, readiness: nativeReadiness,
    draftBlocked: profileDraftBlocked || filmDraftBlocked, currentProjectState,
  });
  liveNative.current = {
    project, profileId: selected?.id ?? null, inputSerial: nativeInputSerial,
    keys: nativeActionKeys, readiness: nativeReadiness,
    draftBlocked: profileDraftBlocked || filmDraftBlocked, currentProjectState,
  };
  const prepareNativeDispatch = (action: NativeAction) => {
    if (!selected) throw new Error("Choose a current delivery profile before starting a native action.");
    return captureDeliveryDispatch({
      project, profileId: selected.id, inputSerial: nativeInputSerial,
      actionKey: nativeActionKeys[action],
    }, () => {
      const view = liveNative.current;
      return {
        currentProject: view.currentProjectState(), renderedProject: view.project,
        profileId: view.profileId, inputSerial: nativeInputSerialRef.current,
        renderedInputSerial: view.inputSerial, actionKey: view.keys[action],
        ready: view.readiness[action], draftBlocked: view.draftBlocked,
        mounted: liveFilm.current.mounted,
      };
    });
  };
  const notePreviousNativeResult = () => {
    if (liveFilm.current.mounted) setMessage("The native action returned for its original inputs. The current view was kept; this result was not applied to it.");
  };

  const firstRenderGate = evaluateFirstNativeRenderGate({
    project,
    profileId: selected?.id ?? null,
    desktopMode,
    profileDirty: profileDraftBlocked,
    sceneIntentComplete: renderIntentsComplete,
    nativePaletteReady,
    preflight: currentPreflight,
    runtime: runtimeObservation,
  });

  function filmOptions(): MotionCanvasFilmOptions {
    if (!selected) throw new Error("Select a saved profile before building a Film projection.");
    if (profileDraftBlocked) throw new Error("Save or resolve the output profile draft before building a Film projection.");
    if (filmDraftBlocked) throw new Error("Save or resolve the scene intent draft before building a Film projection.");
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
        const role = narrativeRoles.find(option => option.value === filmDraft.savedValues[scene.id + ":role"])?.value;
        const archetype = motionArchetypes.find(option => option.value === filmDraft.savedValues[scene.id + ":archetype"])?.value;
        if (!role || !archetype) {
          throw new Error("Every included Motion Canvas scene requires a saved role and archetype.");
        }
        return {
          scene_id: scene.id,
          role,
          archetype,
        };
      }),
    };
  }

  const deliveryOperationPending = useRef(false);
  const run = async (kind: typeof busy, operation: () => Promise<void>) => {
    if (deliveryOperationPending.current) return;
    deliveryOperationPending.current = true;
    setBusy(kind);
    setMessage(null);
    setError(null);
    try {
      await operation();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      deliveryOperationPending.current = false;
      setBusy(null);
    }
  };

  const selectCreatedProfile = (profileId: string, inputSerial: number) => {
    const current = currentProjectState().project;
    if (liveFilm.current.mounted && nativeInputSerialRef.current === inputSerial
      && current?.id === project.id && current.generation === project.generation
      && current.active_branch === project.active_branch) setSelectedId(profileId);
  };
  const createProfile = () => void run("new", async () => {
    const inputSerial = nativeInputSerialRef.current;
    const profile = freshProfile(project.deliverables.length + 1);
    await commit({ type: "upsert_deliverable", profile });
    selectCreatedProfile(profile.id, inputSerial);
  });

  const deriveProfile = () => {
    if (!selected) return;
    void run("new", async () => {
      const inputSerial = nativeInputSerialRef.current;
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
      selectCreatedProfile(profile.id, inputSerial);
    });
  };

  const saveProfile = () => {
    noteNativeInput();
    void run("save", profileDraft.save);
  };

  const removeProfile = () => {
    if (!selected || profileDraftBlocked || filmDraftBlocked) return;
    void run("remove", async () => {
      await commit({ type: "remove_deliverable", profile_id: selected.id });
    });
  };

  const exportCaptions = () => {
    if (!draft || profileDraftBlocked || !sidecarPath.trim()) return;
    const source = { project, profileId: draft.id, profileName: draft.name };
    void run("caption", async () => {
      const result = await exportCaptionSidecar(source.project, source.profileId, sidecarPath.trim());
      if (!liveFilm.current.mounted) return;
      const current = liveNative.current;
      const previousSelection = current.project.id !== source.project.id
        || current.project.generation !== source.project.generation
        || current.project.active_branch !== source.project.active_branch
        || current.project.revision !== source.project.revision
        || current.profileId !== source.profileId;
      setMessage("Caption sidecar written" + (previousSelection ? " for a previous selection" : "")
        + " · " + source.project.title + " / " + source.profileName + " (" + source.profileId + ")"
        + " · r" + (result.revision ?? source.project.revision) + " · " + result.cue_count + " cues · " + result.path
        + (result.source_current === false ? ". The source changed or could not be rechecked; the written file keeps its original revision." : ""));
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
      if (!liveFilm.current.mounted || liveFilm.current.key !== key || !liveFilm.current.ready) return;
      setPreflight({ key, report });
      setMessage(report.verdict === "projection_ready"
        ? "Semantic Film projection is supported at this revision. Native rendering is a separate operation."
        : "Semantic Film projection rejected this revision. No render or native job was dispatched.");
    });
  };

  const checkMultiSegmentSources = () => {
    if (!selected || !multiSourceToken || profileDraftBlocked || !desktopMode) return;
    const key = multiSourceKey;
    void run("multi-source", async () => {
      const report = await preflightMultiSegmentReadiness(
        project, selected.id, multiSourceToken
      );
      setMultiSource({ key, report });
      setMessage("Verified " + report.segments.length + " native source manifests and "
        + report.total_frames + " exact timeline frames. No final MP4 was rendered.");
    });
  };

  const exportMaster = () => {
    if (!exportReady || !activeExportToken) return;
    void run("export-master", async () => {
      const gesture = prepareNativeDispatch("export");
      const receipt = await exportVerifiedNativeMaster(
        project,
        activeExportToken,
        masterDeliveryPath.trim(),
        includeMediaIntegrity,
        gesture.beforeDispatch,
      );
      if (!gesture.stillCurrent()) { notePreviousNativeResult(); return; }
      onExportEvidence(receipt);
      setMessage("Verified MP4 delivered to " + receipt.destination
        + " · SHA-256 " + receipt.sha256.slice(0, 16) + "… · source r" + receipt.revision
        + (receipt.integrity_manifest_path
          ? " · portable verification receipt written"
          : ""));
    });
  };

  const inspectPortableMedia = () => {
    setVerifiedMedia(null);
    void run("verify-media", async () => {
      const result = await verifyPortableMediaReceipt(verifyManifestPath, trustedMediaSha256);
      setVerifiedMedia(result);
      setMessage("Local MP4 bytes match the unsigned receipt · SHA-256 "
        + result.sha256.slice(0, 16) + "… · source r" + result.source_revision
        + (result.trusted_anchor_matched ? " · independent hash matched" : ""));
    });
  };

  const assembleMultiMaster = () => {
    if (!selected || !selectedVoice || !multiSourceToken || !multiMasterReady) return;
    void run("multi-master", async () => {
      const gesture = prepareNativeDispatch("multi-master");
      const verified = await assembleNativeMultisegmentAvMaster(
        project, selected.id, selectedVoice.id, multiSourceToken, gesture.beforeDispatch,
      );
      if (!gesture.stillCurrent()) { notePreviousNativeResult(); return; }
      setMultiMaster(verified);
      setMessage(verified.review_only
        ? "Native source-bound H.264/AAC captioned MP4 is available for read-only owner review. "
          + "Export is disabled: device-local decoded glyph pixels and human legibility remain unverified."
        : "Semwright native semantic MLT produced a source-bound " + verified.frame_count
          + "-frame H.264/AAC MP4 using a measured 48 kHz stereo voice take. "
          + "Technical integrity is verified; human sound and creative approval remain separate.");
    });
  };

  const assembleMaster = () => {
    if (!selected || !selectedVoice || !masterPreviewToken || !masterReady) return;
    void run("master", async () => {
      const gesture = prepareNativeDispatch("master");
      const result = await assembleNativeAvMaster(
        project, selected.id, selectedVoice.id, masterPreviewToken, gesture.beforeDispatch,
      );
      if (!gesture.stillCurrent()) { notePreviousNativeResult(); return; }
      onAvEvidence(result);
      setMessage("Native H.264/AAC MP4 master recorded for r" + result.revision
        + " with " + result.frame_count + " native frames. Verify output metadata before distribution.");
    });
  };

  const startNativeRender = () => {
    if (!selected || !renderReady || !firstRenderGate.may_request_native_render) return;
    void run("render", async () => {
      const gesture = prepareNativeDispatch("render");
      const options = filmOptions();
      // Runtime selection or CLI bytes may have changed since the UI probe.
      // Recheck immediately before requesting a one-time native effect grant.
      const scope = firstRenderRuntimeScope(project);
      const latest = await productionRuntimeCompatibility(project);
      gesture.beforeDispatch();
      setRuntimeObservation({ scope, value: latest });
      const gate = evaluateFirstNativeRenderGate({
        project,
        profileId: selected.id,
        desktopMode,
        profileDirty: profileDraftBlocked,
        sceneIntentComplete: renderIntentsComplete,
        nativePaletteReady,
        preflight: currentPreflight,
        runtime: { scope, value: latest },
      });
      if (!gate.may_request_native_render) {
        throw new Error("Native runtime or Film preparation changed. " + gate.explanation);
      }
      const result = await renderMotionCanvas(project, selected.id, options, gesture.beforeDispatch);
      if (!gesture.stillCurrent()) { notePreviousNativeResult(); return; }
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
    <section className="delivery-profile-workbench" aria-label="Delivery profiles" onKeyDown={containRecipeKeyboard}>
      <header className="delivery-profile-heading">
        <div>
          <strong>Output profiles</strong>
          <span>Versioned intent for frame, language, codecs and portable captions.</span>
        </div>
        <div className="delivery-editor-actions">
          <button type="button" className="button" disabled={!selected || busy !== null || filmDraft.pending} onClick={deriveProfile}>
            <Plus size={14} /> Derive selected
          </button>
          <button type="button" className="button" disabled={busy !== null || filmDraft.pending} onClick={createProfile}>
            <Plus size={14} /> New profile
          </button>
        </div>
      </header>

      <RetainedProfileDrafts project={project} />

      <div className="delivery-profile-layout">
        <div className="delivery-profile-index" role="list" aria-label="Delivery profile index">
          {project.deliverables.map((profile) => (
            <button
              type="button"
              role="listitem"
              key={profile.id}
              data-profile-id={profile.id}
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
              <span className={profileDraftBlocked ? "status-pill status-stale" : "status-pill status-current"}>
                {profileDraft.awaiting ? "SAVE UNCONFIRMED" : profileDraftBlocked ? "DRAFT" : "CURRENT"}
              </span>
            </div>


            <section className="caption-export-panel" aria-label="Output profile draft review" onKeyDown={containRecipeKeyboard}>
              <p role="status" className="production-help">
                {profileDraft.pending ? "Save requested. You may keep editing; later input will remain separate."
                  : profileDraft.awaiting ? "The submitted input has not been observed in a newer saved revision. Review it before another save."
                  : dirty ? Object.keys(profileDraft.edits).length + " output field(s) have pending input."
                  : "Showing saved output values at revision r" + project.revision + "."}
              </p>
              {profileDraft.locked && <p className="portable-message error">The project content lock blocks profile changes.</p>}
              {profileDraft.sourceBehind && <p className="portable-message error">This draft refers to a newer saved revision. Open the current project before saving or resolving it.</p>}
              {profileDraft.error && <p className="portable-message error" role="alert">{profileDraft.error}</p>}
              {profileDraft.message && <p className="production-help" role="status">{profileDraft.message}</p>}
              {profileDraft.recoveryReadUnverified && <button type="button" className="button" disabled={profileDraft.pending}
                onClick={() => { noteNativeInput(); profileDraft.rereadRecoveryCopy(); }}>Read profile recovery copy again</button>}
              {profileDraft.recoveryBlocked && !profileDraft.recoveryReadUnverified && <button type="button" className="button" disabled={profileDraft.pending}
                onClick={() => { noteNativeInput(); profileDraft.discardUnreadableCopy(); }}>Discard blocked profile recovery copy</button>}
              {Object.keys(profileDraft.edits).length > 0 && <details open={profileDraft.conflicts.length > 0 || profileDraft.awaiting}
                aria-label="Output profile pending fields">
                <summary>Review pending output fields ({Object.keys(profileDraft.edits).length})</summary>
                {Object.entries(profileDraft.edits).map(([field, edit]) => {
                  const label = field.startsWith("text:")
                    ? "Localized text " + (project.scenes.flatMap(scene => scene.nodes).find(node => node.id === field.slice(5))?.name ?? field.slice(5))
                    : field.replaceAll("_", " ");
                  const sent = profileDraft.sentFields[field];
                  return <div key={field} role="group" aria-label={label + " pending input"}>
                    <strong>{label}{profileDraft.conflicts.includes(field) ? " · saved value changed" : ""}</strong>
                    <div className="portable-plan" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 150px), 1fr))" }}>
                      {([["Saved before", edit.base], ["Saved now", profileDraft.savedValues[field] ?? "null"],
                        ...(sent ? [["Input sent", sent.raw]] : []), ["My current input", edit.value]] as string[][]).map(([heading, value]) =>
                        <div key={heading}><span>{heading}</span>
                          <p style={{ margin: 0, whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 12 }}>{value === "" ? "(empty input)" : value}</p>
                        </div>)}
                    </div>
                    <div className="delivery-editor-actions">
                      <button type="button" className="button" disabled={profileDraft.pending || profileDraft.sourceBehind}
                        onClick={() => { noteNativeInput(); profileDraft.resolve(field, "saved"); }}>Keep saved {label}</button>
                      {(profileDraft.conflicts.includes(field) || sent) && <button type="button" className="button"
                        disabled={profileDraft.pending || profileDraft.sourceBehind}
                        onClick={() => { noteNativeInput(); profileDraft.resolve(field, "draft"); }}>Keep my input {label}</button>}
                    </div>
                  </div>;
                })}
              </details>}
              <p className="production-help">Profile changes are saved explicitly. A save result does not replace later typing, and a recovery copy does not authorize native production.</p>
              <details aria-label="Output profile recovery copies">
                <summary>Profile recovery copies ({profileDraft.copies.length})</summary>
                <p className="production-help">Separate editing windows are listed only for this project generation, branch and profile. Recovery keeps the original copy.</p>
                <button type="button" className="button" onClick={profileDraft.refreshCopies}>Refresh profile copies</button>
                {profileDraft.copies.map((copy, index) => <div key={copy.key}>
                  <span>{copy.readable ? "Copy " + (index + 1) + " · r" + copy.revision + " · " + copy.fields + " field(s)" : "Unreadable copy · original retained"}</span>
                  <button type="button" className="button" disabled={!copy.readable || profileDraftBlocked}
                    onClick={() => { noteNativeInput(); profileDraft.recoverCopy(copy.key); }}>Recover profile copy {index + 1}</button>
                </div>)}
              </details>
            </section>

            <fieldset disabled={filmDraft.pending} aria-label="Output profile settings"
              style={{ margin: 0, padding: 0, border: 0, minWidth: 0, display: "grid", gap: 14 }}>
            {parentProfile && (
              <div className="delivery-truth-note">
                Derived from {parentProfile.name} · source revision r{draft.source_revision ?? "?"} · original remains independently inspectable.
              </div>
            )}

            <div className="delivery-field-grid">
              <label className="span-2">
                <span className="field-label">Profile name</span>
                <input value={profileDraft.values.name} maxLength={160} onChange={(event) => setProfileField("name", event.target.value)} />
              </label>
              <label>
                <span className="field-label">Width</span>
                <input type="text" inputMode="numeric" value={profileDraft.values.width} onChange={(event) => setProfileField("width", event.target.value)} />
              </label>
              <label>
                <span className="field-label">Height</span>
                <input type="text" inputMode="numeric" value={profileDraft.values.height} onChange={(event) => setProfileField("height", event.target.value)} />
              </label>
              <label>
                <span className="field-label">Language / locale</span>
                <input value={profileDraft.values.language} maxLength={64} onChange={(event) => setProfileField("language", event.target.value)} />
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
                  {!["24/1", "25/1", "30000/1001", "30/1", "60000/1001", "60/1"].includes(draft.frame_rate.num + "/" + draft.frame_rate.den)
                    && <option value={draft.frame_rate.num + "/" + draft.frame_rate.den}>{draft.frame_rate.num}/{draft.frame_rate.den} fps · saved exact rate</option>}
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
                <input value={profileDraft.values.cut_label} maxLength={256} placeholder="e.g. social-short" onChange={(event) => setProfileField("cut_label", event.target.value)} />
              </label>
              <label>
                <span className="field-label">Brand profile</span>
                <input value={profileDraft.values.brand_profile} maxLength={256} placeholder="Optional" onChange={(event) => setProfileField("brand_profile", event.target.value)} />
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
                  aria-label="Adaptation notes · one decision per line"
                  value={profileDraft.values.adaptation_notes}
                  placeholder="Why this variant differs from its parent."
                  onChange={(event) => setProfileField("adaptation_notes", event.target.value)}
                />
                <small>Raw spacing and blank lines stay while editing. Save turns each nonblank line into one trimmed note.</small>
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
                  onClick={() => setCut(customCut ? "full" : "custom", customCut ? [] : project.scenes.map(scene => scene.id))}
                >
                  {customCut ? "Use full sequence" : "Customize cut"}
                </button>
              </header>
              <div className="variant-scene-ledger">
                {project.scenes.map((scene) => {
                  const included = !customCut || draft.included_scene_ids.includes(scene.id);
                  const protectedScene = draft.protected_scene_ids.includes(scene.id);
                  return (
                    <div className="variant-scene-row" key={scene.id}
                      style={{ display: "flex", flexWrap: "wrap", columnGap: 20, rowGap: 8 }}>
                      <div style={{ flex: "1 1 260px" }}>
                        <strong>{scene.name}</strong>
                        <span>{scene.objective}</span>
                      </div>
                      <div className="caption-toggle-group" style={{ display: "flex", flexWrap: "wrap", gap: 14 }}>
                      <label className="caption-toggle" style={{ minHeight: 32 }}>
                        <input
                          type="checkbox"
                          style={{ width: 16, height: 16, minHeight: 16, margin: 0, flexShrink: 0 }}
                          checked={included}
                          disabled={!customCut}
                          onChange={(event) => {
                            const ids = event.target.checked
                              ? project.scenes.filter(item => draft.included_scene_ids.includes(item.id) || item.id === scene.id).map(item => item.id)
                              : draft.included_scene_ids.filter((id) => id !== scene.id);
                            setCut("custom", ids);
                          }}
                        />
                        Include
                      </label>
                      <label className="caption-toggle" style={{ minHeight: 32 }}>
                        <input
                          type="checkbox"
                          style={{ width: 16, height: 16, minHeight: 16, margin: 0, flexShrink: 0 }}
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
                    </div>
                  );
                })}
              </div>
              {emptyCustomCut && <p className="delivery-truth-note warning" role="status">
                Choose at least one scene for the custom cut, or choose Use full sequence explicitly. Your empty selection is retained; saving and production remain blocked.
              </p>}
              {customCut && draft.protected_scene_ids.some((id) => !draft.included_scene_ids.includes(id)) && (
                <div className="delivery-truth-note warning"><CircleDashed size={14} /> A protected scene is outside this cut. Saving will remain blocked until it is included.</div>
              )}
            </section>

            {!emptyCustomCut && <>
              <VariantContinuityReview project={project} profile={draft} saved={selected} />
              <DeliveryVariantImpactReview project={project} draft={draft} saved={selected} />
            </>}

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
                        value={profileDraft.values["text:" + node.id] ?? ""}
                        placeholder={localizedVariant ? "Required localized text" : "Optional override"}
                        onChange={(event) => setProfileField("text:" + node.id, event.target.value)}
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
              <button type="button" className="button button-primary"
                disabled={!dirty || emptyCustomCut || busy !== null || profileDraft.pending || profileDraft.awaiting || profileDraft.locked
                  || profileDraft.sourceBehind || profileDraft.recoveryBlocked || profileDraft.conflicts.length > 0}
                onClick={saveProfile}>
                <Save size={14} /> {busy === "save" ? "Saving…" : "Save profile"}
              </button>
              <button type="button" className="button" disabled={project.deliverables.length <= 1 || busy !== null || profileDraftBlocked || filmDraftBlocked} onClick={removeProfile}>
                <Trash2 size={14} /> Remove
              </button>
            </div>
            </fieldset>

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
                    <span>Save each scene's role and visual archetype in this delivery profile. Film checks and rendering use those saved values.</span>
                  </div>
                  <span className={renderIntentsComplete && !filmDraftBlocked ? "status-pill status-current" : "status-pill status-unknown"}>
                    {filmDraftBlocked ? "PENDING REVIEW" : renderIntentsComplete ? "SAVED COMPLETE" : "SAVED INCOMPLETE"}
                  </span>
                </header>
                {motionScenes.map((scene) => {
                  const roleKey = scene.id + ":role";
                  const archetypeKey = scene.id + ":archetype";
                  return (
                    <div className="portable-operation" key={scene.id}
                      style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 180px), 1fr))" }}>
                      <div className="portable-operation-copy">
                        <strong>{scene.name}</strong>
                        <span className="mono">scene:{scene.id.slice(0, 8)}</span>
                      </div>
                      <label>
                        <span className="field-label">Narrative role</span>
                        <select
                          id={filmFields[roleKey]?.controlId}
                          aria-label={scene.name + " narrative role"}
                          value={filmDraft.values[roleKey] ?? ""}
                          disabled={filmDraft.locked || filmDraft.sourceBehind}
                          onChange={(event) => { noteNativeInput(); liveFilm.current.ready = false; filmDraft.set(roleKey, event.target.value); }}
                        >
                          <option value="">Choose role…</option>
                          {filmDraft.values[roleKey] && !narrativeRoles.some(option => option.value === filmDraft.values[roleKey]) && <option value={filmDraft.values[roleKey]}>Unrecognized input: {filmDraft.values[roleKey]}</option>}
                          {narrativeRoles.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                      </label>
                      <label>
                        <span className="field-label">Archetype</span>
                        <select
                          id={filmFields[archetypeKey]?.controlId}
                          aria-label={scene.name + " motion archetype"}
                          value={filmDraft.values[archetypeKey] ?? ""}
                          disabled={filmDraft.locked || filmDraft.sourceBehind}
                          onChange={(event) => { noteNativeInput(); liveFilm.current.ready = false; filmDraft.set(archetypeKey, event.target.value); }}
                        >
                          <option value="">Choose archetype…</option>
                          {filmDraft.values[archetypeKey] && !motionArchetypes.some(option => option.value === filmDraft.values[archetypeKey]) && <option value={filmDraft.values[archetypeKey]}>Unrecognized input: {filmDraft.values[archetypeKey]}</option>}
                          {motionArchetypes.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select>
                      </label>
                    </div>
                  );
                })}
                <RecipeDraftReview controller={filmDraft} title="Scene intent" revision={project.revision}
                  onDraftAction={noteNativeInput}
                  fields={filmFields} saveLabel="Save scene intent" clearLabel="Clear saved scene intent"
                  hasSavedGroup={Object.values(filmDraft.savedValues).some(Boolean)}
                  blockedReason={profileDraftBlocked ? "Save or resolve the output profile settings before saving scene intent. Your recipe inputs remain pending."
                    : busy !== null ? "Wait for the current delivery operation before saving a recipe change." : null} />
              </div>

              {filmDraftBlocked && <p className="delivery-truth-note warning" role="status">
                Save or resolve pending scene intent before checking Film, rendering, assembling or exporting a native master. These operations use only the saved recipe at the current revision.
              </p>}

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
                  disabled={!renderReady || !firstRenderGate.may_request_native_render || busy !== null || runtimeProbeBusy}
                  onClick={startNativeRender}
                  title={firstRenderGate.explanation}
                >
                  <Play size={14} /> {busy === "render" ? "Rendering…" : "Render native segments"}
                </button>
              </div>
              <div className={"delivery-truth-note " +
                (firstRenderGate.may_request_native_render ? "" : "warning")}
                role="status" aria-label="First native production readiness">
                {firstRenderGate.may_request_native_render
                  ? <CircleCheck size={14} aria-hidden="true" />
                  : <CircleDashed size={14} aria-hidden="true" />}
                <span>
                  {firstRenderGate.code.replaceAll("_", " ")} — {firstRenderGate.explanation}
                  {" "}No native provider health, rendered frames or release approval are attested by this gate.
                </span>
              </div>
              <div className="delivery-editor-actions">
                <button type="button" className="button secondary"
                  disabled={!desktopMode || runtimeProbeBusy || busy !== null}
                  onClick={() => setRuntimeProbeSerial((previous) => previous + 1)}>
                  {runtimeProbeBusy ? "Checking Semwright CLI…" : "Recheck Semwright CLI"}
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
              {profileDraftBlocked && (
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
              {currentMotion && currentMotion.segments.length > 1 && (
                <div className="delivery-multi-source" role="region"
                  aria-label="Multi-segment native MLT source readiness">
                  <div>
                    <strong>Multi-segment MLT source check</strong>
                    <span>Verify the full native Film cut and actual segment manifests.
                      No rendering, muxing or arbitrary filesystem access.</span>
                  </div>
                  <button type="button" className="button compact"
                    aria-label="Check native multi-segment sources"
                    disabled={!multiSourceToken || profileDraftBlocked || !desktopMode || busy !== null}
                    onClick={checkMultiSegmentSources}>
                    <CircleDashed size={14} />
                    {busy === "multi-source" ? "Checking source manifests…" : "Check native sources"}
                  </button>
                  {!multiSourceToken && (
                    <span className="delivery-truth-note warning">
                      Native source handles are not available in this desktop session. Render again to establish trusted source identity.
                    </span>
                  )}
                  {currentMultiSource && (
                    <div className="delivery-truth-note" role="status"
                      aria-label="Multi-segment source preflight result">
                      <CircleCheck size={14} aria-hidden="true" />
                      Manifest-ready at r{currentMultiSource.revision}:
                      {" "}{currentMultiSource.segments.length} segments,
                      {" "}{currentMultiSource.total_frames} exact frames,
                      {" "}MLT profile {currentMultiSource.mlt_profile}.
                      This checks source manifests only; a multi-segment master MP4 has
                      NOT been assembled.
                    </div>
                  )}
                </div>
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
                  <span>Verified Motion Canvas cut + measured source WAV → Semwright MLT → H.264/AAC MP4.</span>
                </div>
                <span className="status-pill status-unknown">OWNER-GRANTED</span>
              </header>
              <div className="delivery-editor-actions">
                {(!currentMotion || currentMotion.segments.length <= 1) && (
                  <button className="button button-primary" type="button"
                    disabled={!masterReady || busy !== null}
                    onClick={assembleMaster}>
                    <Play size={14} />
                    {busy === "master" ? "Mastering…" : "Assemble native AV master"}
                  </button>
                )}
                {currentMotion && currentMotion.segments.length > 1 && (
                  <button className="button button-primary" type="button"
                    disabled={!multiMasterReady || busy !== null}
                    onClick={assembleMultiMaster}>
                    <Play size={14} />
                    {busy === "multi-master" ? "Assembling native MLT + AV…"
                      : selected?.burn_in_captions ? "Assemble captioned MP4 for review"
                        : "Assemble multi-segment MP4"}
                  </button>
                )}
              </div>
              {!masterPreviewToken && !multiSourceToken && (
                <div className="delivery-truth-note">
                  <CircleDashed size={14} /> Complete a real native render of one contiguous Motion Canvas segment in this desktop session first.
                </div>
              )}
              {!verifiedWavIntent && (
                <div className="delivery-truth-note">
                  <CircleDashed size={14} /> Bind one saved, imported measured 48 kHz stereo WAV voice take to the output profile.
                </div>
              )}
              {multiSourceToken && !currentMultiSource && (
                <div className="delivery-truth-note">
                  <CircleDashed size={14} /> Run the multi-segment source preflight before
                  requesting native MLT+AV mastering. The desktop will resolve the
                  original Film options from the opaque preview token.
                </div>
              )}
              {multiSourceToken && verifiedWavIntent && !multiVoiceAligned && (
                <div className="delivery-truth-note warning">
                  <CircleDashed size={14} /> Measured voice must match the entire native
                  multi-segment cut within one 48 kHz PCM sample. No padding or retiming.
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
              {currentMultiMaster && (
                <div className="portable-plan" aria-label="Native multi-segment AV master evidence" role="status">
                  <div><span>Source</span><strong>{currentMultiMaster.video_segments} semantic MLT segments</strong></div>
                  <div><span>Frames</span><strong>{currentMultiMaster.frame_count}</strong></div>
                  <div><span>Master</span><strong className="mono">
                    {currentMultiMaster.master_sha256.slice(0, 16)}… SHA-256
                  </strong></div>
                  <div><span>Format</span><strong>H.264 · AAC · 48 kHz stereo</strong></div>
                  <div><span>Delivery</span><strong>{
                    currentMultiMaster.review_only
                      ? "REVIEW ONLY · CAPTION PIXELS NOT VERIFIED LOCALLY"
                      : "Verified source · export eligible"
                  }</strong></div>
                  <div><span>MLT resource cleanup</span><strong>
                    {currentMultiMaster.provider_project_cleanup ===
                      "not_requested_requires_foreground_broker_consent"
                      ? "Pending foreground Broker consent"
                      : "Unknown · verify operator approval"}
                  </strong></div>
                  <div><span>Applicability</span><strong>
                    {currentMultiMaster.revision === project.revision
                      ? "CURRENT · NATIVE" : "STALE · HISTORICAL"}
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
              {showNativeAvReview && reviewAvailable && activeReviewToken && (
                <NativeAvReview
                  key={project.id + ":" + project.revision + ":" + activeReviewToken}
                  project={project}
                  exportToken={activeReviewToken}
                  reviewOnly={Boolean(currentMultiMaster?.review_only)}
                />
              )}
              {currentMultiMaster?.review_only && currentMultiMaster.revision === project.revision && (
                <div className="delivery-truth-note warning" role="status">
                  <CircleDashed size={14} /> This captioned H.264/AAC master is review-only.
                  Its native source pixels were checked, but its installed master has
                  not passed independent decoded glyph verification. Local export
                  remains blocked, even with a DeliverLocal grant. Small media
                  can be reviewed under the owner SHA-256 token; this does not
                  certify real speech or accessible reading.
                </div>
              )}
              <div className="delivery-truth-note">
                <CircleDashed size={14} /> Native master playback is an explicit, SHA-256-verified local review action limited to 16 MiB. For ordinary approved masters, larger MP4 files must be exported for external playback. Decoder compatibility, timeline synchronization and human mix approval remain separate.
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
                    onChange={(event) => { noteNativeInput(); setMasterDeliveryPath(event.target.value); }}
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
                  onChange={(event) => { noteNativeInput(); setIncludeMediaIntegrity(event.target.checked); }}
                />
                Write portable SHA-256 verification receipt (.json) next to the MP4
              </label>
              <div className="delivery-truth-note">
                <CircleDashed size={14} /> Optional receipt checks file bytes and source revision offline.
                It is unsigned: it does not prove publisher identity, codec quality or human approval.
              </div>
              {!activeExportToken && (
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

            <section className="caption-export-panel" aria-label="Verify portable MP4 integrity">
              <header>
                <div>
                  <strong>Verify a delivered MP4</strong>
                  <span>Open an existing portable receipt and compare the local MP4 bytes, even after the original editing session has ended.</span>
                </div>
                <span className="status-pill status-unknown">READ ONLY</span>
              </header>
              <div className="caption-controls">
                <label className="caption-path">
                  <span className="field-label">Receipt · absolute .mp4.motionwright-integrity.json path</span>
                  <input type="text"
                    aria-label="Portable MP4 receipt path"
                    placeholder="/absolute/path/final-master.mp4.motionwright-integrity.json"
                    value={verifyManifestPath}
                    disabled={!desktopMode || busy !== null}
                    onChange={(event) => {
                      setVerifyManifestPath(event.target.value);
                      setVerifiedMedia(null);
                      setMessage(null);
                      setError(null);
                    }}
                  />
                </label>
                <button className="button button-primary" type="button"
                  disabled={!desktopMode || !verifyManifestPath.trim() || busy !== null}
                  onClick={inspectPortableMedia}>
                  <CircleCheck size={14} aria-hidden="true" />
                  {busy === "verify-media" ? "Comparing bytes…" : "Verify local MP4"}
                </button>
              </div>
              <label className="caption-path">
                <span className="field-label">Independent trusted SHA-256 · optional, not copied from the receipt</span>
                <input type="text"
                  aria-label="Independent trusted MP4 SHA-256"
                  maxLength={64}
                  spellCheck={false}
                  className="mono"
                  placeholder="64 lowercase hexadecimal characters"
                  value={trustedMediaSha256}
                  disabled={!desktopMode || busy !== null}
                  onChange={(event) => {
                    setTrustedMediaSha256(event.target.value);
                    setVerifiedMedia(null);
                    setMessage(null);
                    setError(null);
                  }}
                />
              </label>
              {verifiedMedia && (
                <div className="portable-plan" aria-label="Portable MP4 integrity result" role="status">
                  <div><span>Byte comparison</span><strong>SHA-256 CONTENT MATCH</strong></div>
                  <div><span>File</span><strong>{verifiedMedia.filename}</strong></div>
                  <div><span>Size</span><strong>{verifiedMedia.size_bytes.toLocaleString()} bytes</strong></div>
                  <div><span>SHA-256</span><strong className="mono">{verifiedMedia.sha256}</strong></div>
                  <div><span>Origin described</span><strong className="mono">r{verifiedMedia.source_revision} · self-declared</strong></div>
                  <div><span>Independent anchor</span><strong>{verifiedMedia.trusted_anchor_matched ? "MATCHED" : "NOT PROVIDED"}</strong></div>
                </div>
              )}
              <div className="delivery-truth-note">
                <CircleDashed size={14} aria-hidden="true" /> Local SHA-256 consistency does not authenticate the publisher, certify playback or constitute human approval. A receipt can be replaced together with its MP4; use a hash obtained independently when authenticity matters.
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
                  disabled={!desktopMode || !captionReady || profileDraftBlocked || !sidecarPath.trim() || busy !== null}
                  onClick={exportCaptions}
                >
                  <FileOutput size={14} /> {busy === "caption" ? "Writing…" : "Export sidecar"}
                </button>
              </div>
              {!desktopMode && (
                <div className="delivery-truth-note"><CircleDashed size={14} /> Desktop filesystem capability is required.</div>
              )}
              {draft.burn_in_captions && (
                <div className="delivery-truth-note warning" role="status">
                  <CircleDashed size={14} /> Saved Burn-in renders timed transcript text through the pinned Semwright Motion Canvas Film when the current voice digest and exact frame times are valid. Sidecar export remains separate. A separately authorized native MLT H.264/AAC master is restricted to two or more verified Motion Canvas segments and a measured WAV. Its decoded pixel proof and human font/legibility review remain independent publication gates; other profiles cannot silently burn in captions.
                </div>
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
              {profileDraftBlocked && <div className="delivery-truth-note">Save or resolve the profile before exporting its sidecar.</div>}
              <CaptionReadabilityReview
                project={project} profile={draft} saved={selected}
                onOpenAudio={onOpenAudio}
              />
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
