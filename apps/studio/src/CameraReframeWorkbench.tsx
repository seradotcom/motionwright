import { useEffect, useId, useRef, useState } from "react";
import { ArrowUpRight, RefreshCw, Save } from "lucide-react";
import type { Change, DeliverableProfile, Project, Scene } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { cameraSvgTransform } from "./canvasCamera";
import {
  CAMERA_DRAFT_FIELDS, CAMERA_REFRAME_MANIM_VERSION, cameraDraftEdited, cameraDraftKey,
  cameraDraftMove, cameraReframePose, cameraSceneFrameCount, cameraSourceSignature, createCameraDraft,
  decodeCameraDraft, manimCameraRenderNotes, rebaseCameraDraft, reconcileCameraDraft,
  sameCameraReframe, validateCameraReframe,
} from "./cameraReframe";
import type { CameraDraftField, CameraDraftRecord, CameraReframe } from "./cameraReframe";
type Props = { project: Project; scene: Scene | null; busy: boolean;
  commit: (change: Change) => Promise<void>; playhead: number;
  onSeek: (time: number) => void; onOpenCanvas: () => void;
  displayProfile?: DeliverableProfile | null; onOpenRender?: () => void; };
const LABELS: Record<CameraDraftField, string> = { start_center_x: "Start center X", start_center_y: "Start center Y",
  end_center_x: "End center X", end_center_y: "End center Y", duration_frames: "Camera duration frames" };
// Fallback keeps tab/scene navigation safe if session storage is unavailable.
// It is not a project store and carries no revision or effect authority.
const memoryDrafts = new Map<string, CameraDraftRecord>();
const volatileDrafts = new Set<string>();
function message(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function readDraft(project: Project, scene: Scene): { record: CameraDraftRecord; recoveryError: string | null; storageError: string | null } {
  const key = cameraDraftKey(project, scene);
  if (volatileDrafts.has(key) && memoryDrafts.has(key)) return {
    record: memoryDrafts.get(key)!, recoveryError: null,
    storageError: "The latest camera draft is retained in this page. Browser session storage could not keep its latest input.",
  };
  let raw: string | null;
  try { raw = sessionStorage.getItem(key); }
  catch { return { record: memoryDrafts.get(key) ?? createCameraDraft(project, scene), recoveryError: null,
    storageError: "Browser session storage is unavailable. Keep this page open until the camera is saved to the project." }; }
  if (raw !== null) {
    try { return { record: decodeCameraDraft(raw, project, scene), recoveryError: null, storageError: null }; }
    catch { return { record: createCameraDraft(project, scene), recoveryError:
      "The retained camera draft could not be read. Its stored copy has been kept; choose whether to load the saved project camera.", storageError: null }; }
  }
  return { record: memoryDrafts.get(key) ?? createCameraDraft(project, scene), recoveryError: null, storageError: null };
}
export default function CameraReframeWorkbench(props: Props) {
  if (!props.scene) return <section className="production-inspector" aria-label="Planar camera controls">
    <h2>Camera Reframe</h2><p role="status">Select a Motion Canvas or Manim Community scene with editable objects.</p>
  </section>;
  return <SceneCameraWorkbench key={cameraDraftKey(props.project, props.scene)} {...props} scene={props.scene}/>;
}
function SceneCameraWorkbench({ project, scene, busy, commit, playhead, onSeek, onOpenCanvas, displayProfile, onOpenRender }: Props & { scene: Scene }) {
  const [initial] = useState(() => readDraft(project, scene));
  const [record, setRecord] = useState(initial.record);
  const [recoveryError, setRecoveryError] = useState(initial.recoveryError);
  const [storageError, setStorageError] = useState(initial.storageError);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [finalPose, setFinalPose] = useState(false);
  const [safeGuide, setSafeGuide] = useState(true);
  const viewportClip = useId();
  const inFlight = useRef(false);
  const submitted = useRef<{ revision: number; move: CameraReframe | null; source_signature: string } | null>(null);
  const firstInput = useRef<HTMLInputElement>(null);
  const discardButton = useRef<HTMLButtonElement>(null);
  const stored = project.production_design?.camera_reframes?.find(move => move.scene_id === scene.id);
  const key = cameraDraftKey(project, scene);
  const stale = record.base_revision !== project.revision;
  const dirty = cameraDraftEdited(record);
  const locked = project.locks.some(lock => ["project:" + project.id, "scene:" + scene.id].includes(lock.resource) &&
    ["content", "position", "timing", "renderer"].includes(lock.kind));
  let draft: CameraReframe | null = null, problem: string | null = null;
  try {
    draft = cameraDraftMove(record);
    validateCameraReframe(draft, { ...scene, camera: { ...scene.camera,
      center_x: draft.end_center_x, center_y: draft.end_center_y } });
  } catch (error) { problem = message(error); }
  const canApply = !problem && !stale && !locked && !busy && !pending && !recoveryError;
  const frameCount = cameraSceneFrameCount(scene);
  const lastFrame = Math.max(0, (frameCount ?? Math.ceil(seconds(scene.duration) * 30)) - 1);
  const localTime = Math.max(0, Math.min(seconds(scene.duration), playhead - seconds(scene.start)));
  const frame = Math.max(0, Math.min(lastFrame, Math.floor(localTime * 30 + 1e-8)));
  const sampleTime = finalPose && draft ? draft.duration_frames / 30 : frame / 30;
  const pose = draft && !problem ? cameraReframePose(draft, sampleTime, scene.camera.safe_margin) : null;
  const transform = pose ? cameraSvgTransform(pose) : null;
  const palette = project.visual_language.palette;
  const authoredBackground = (palette.find(row => row.name === "background") ?? palette.find(row => row.name === "surface"))?.value;
  const background = authoredBackground && /^#[0-9a-f]{6}([0-9a-f]{2})?$/i.test(authoredBackground) ? authoredBackground : "#0F1216";
  const renderNotes = manimCameraRenderNotes(project, scene, displayProfile);

  useEffect(() => {
    if (recoveryError) return;
    memoryDrafts.set(key, record);
    try { sessionStorage.setItem(key, JSON.stringify(record)); volatileDrafts.delete(key); setStorageError(null); }
    catch { volatileDrafts.add(key); setStorageError("The latest camera draft could not be retained in browser session storage. Keep this page open until it is saved to the project."); }
  }, [key, record, recoveryError]);
  useEffect(() => {
    const ack = submitted.current;
    const savedMatches = ack?.move && stored && sameCameraReframe(ack.move, stored);
    const detached = ack && ack.move === null && !stored;
    if (ack && project.revision > ack.revision && (savedMatches || detached)) {
      if (detached && cameraSourceSignature(project, scene) === ack.source_signature) setRecord(old => {
        const current = createCameraDraft(project, scene, old.base.id);
        return cameraDraftEdited(old) ? { ...current, fields: { ...old.fields } } : current;
      });
      else setRecord(old => reconcileCameraDraft(old, project, scene,
        ack.move ? { move: ack.move, source_signature: ack.source_signature } : undefined));
      submitted.current = null; inFlight.current = false; setPending(false);
      setNotice(detached ? "Camera move detached. Saved objects and final camera pose are preserved." :
        `Camera saved at revision ${project.revision}. Later input remains in your draft.`);
    } else if (!recoveryError) setRecord(old => reconcileCameraDraft(old, project, scene));
  }, [project, scene, stored, recoveryError]);
  useEffect(() => { if (confirmReplace) discardButton.current?.focus(); }, [confirmReplace]);
  function patch(field: CameraDraftField, value: string) {
    setRecord(old => ({ ...old, fields: { ...old.fields, [field]: value } }));
    setError(null); setNotice(null);
  }
  function useSaved() {
    setRecord(createCameraDraft(project, scene, record.base.id)); setRecoveryError(null);
    setError(null); setNotice("Loaded the saved project camera."); setConfirmReplace(false);
    firstInput.current?.focus();
  }
  function keepValues() {
    setRecord(rebaseCameraDraft(record, project, scene)); setError(null);
    setNotice("Draft values retained against the current scene. Review the preview before saving.");
    firstInput.current?.focus();
  }
  async function save() {
    if (!canApply || !draft || inFlight.current) return;
    const move = structuredClone(draft);
    inFlight.current = true; setPending(true); setError(null); setNotice(null);
    submitted.current = { revision: project.revision, move,
      source_signature: cameraSourceSignature(project, { ...scene, camera: { ...scene.camera,
        center_x: move.end_center_x, center_y: move.end_center_y } }, move) };
    try { await commit({ type: "upsert_camera_reframe", reframe: move }); }
    catch (error) { submitted.current = null; inFlight.current = false; setPending(false); setError(message(error)); }
    // Existing App.commit reports its errors in the application banner and
    // resolves void. Completion releases the send lock, never the draft base.
    // Only the authoritative Project effect above acknowledges a saved camera.
    finally { inFlight.current = false; setPending(false); }
  }
  async function detach() {
    if (!stored || stale || busy || locked || pending || dirty || recoveryError || inFlight.current) return;
    inFlight.current = true; setPending(true); setError(null); setNotice(null);
    submitted.current = { revision: project.revision, move: null, source_signature: cameraSourceSignature(project, scene, null) };
    try { await commit({ type: "detach_camera_reframe", id: stored.id }); }
    catch (error) { submitted.current = null; inFlight.current = false; setPending(false); setError(message(error)); }
    finally { inFlight.current = false; setPending(false); }
  }
  return <div className="production-split">
    <section className="production-inspector" aria-label="Planar camera controls" onKeyDown={event => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); void save(); }
      if (event.key === "Escape" && confirmReplace) { event.preventDefault(); setConfirmReplace(false); firstInput.current?.focus(); }
    }}>
      <div className="production-section-title"><div><h2>Camera Reframe</h2>
        <p>{record.base.component_version === CAMERA_REFRAME_MANIM_VERSION ? "Manim Community · version 2" : "Motion Canvas · version 1"} · planar pan</p>
      </div></div>
      <p className="production-help">Translate the camera at 30 fps while keeping your objects editable. Both endpoints preserve the safe area; the final pose holds.</p>
      {record.base.component_version === CAMERA_REFRAME_MANIM_VERSION && <details className="production-help"><summary>Manim source limits</summary><p>
        Manim accepts opaque rectangles and circles in project pixels, with no text, strokes, rotation, relations or object keyframes.
        These controls do not add zoom, depth or optical camera effects.</p></details>}
      {(stale || recoveryError) && <div className="production-notice" role="status">
        <strong>{recoveryError ? "Retained camera draft needs attention" : `Camera source changed: draft revision ${record.base_revision}, current revision ${project.revision}.`}</strong>
        <span>{recoveryError ?? "Your input is intact. Review the current source before applying it."}</span>
        {stale && !recoveryError && <>
          <dl aria-label="Camera revision differences">
            {CAMERA_DRAFT_FIELDS.filter(field => record.fields[field] !== String(stored?.[field] ?? "—")).map(field => <div key={field}>
              <dt>{LABELS[field]}</dt><dd>Draft {record.fields[field] || "(empty)"} · saved {stored?.[field] ?? "no attached camera"}</dd>
            </div>)}
          </dl>
          <button className="secondary-button" disabled={pending || busy} onClick={keepValues}>Keep my values on the current camera</button>
        </>}
        <button className="secondary-button" disabled={pending} onClick={() => setConfirmReplace(true)}><RefreshCw size={14}/> Use saved camera…</button>
      </div>}
      {confirmReplace && <div className="production-notice" role="alertdialog" aria-label="Replace local camera draft" aria-describedby="camera-draft-discard-description">
        <strong>Load the saved project camera?</strong><p id="camera-draft-discard-description">This replaces the retained local camera input for this scene. The saved project is unchanged.</p>
        <button ref={discardButton} className="secondary-button" onClick={useSaved}>Discard draft and load saved camera</button>
        <button className="text-button" onClick={() => { setConfirmReplace(false); firstInput.current?.focus(); }}>Keep draft</button>
      </div>}
      <div className="production-plan-grid">
        {CAMERA_DRAFT_FIELDS.slice(0, 4).map((field, index) => <label className="production-field" key={field}>
          <span>{LABELS[field]} · source pixels</span>
          <input ref={index === 0 ? firstInput : undefined} aria-label={LABELS[field]} type="text" inputMode="decimal"
            value={record.fields[field]} maxLength={64} onChange={event => patch(field, event.target.value)}/>
        </label>)}
      </div>
      <label className="production-field"><span>Duration · 30 fps frames</span>
        <input aria-label={LABELS.duration_frames} type="text" inputMode="numeric" maxLength={64}
          value={record.fields.duration_frames} onChange={event => patch("duration_frames", event.target.value)}/>
        <small>12–180 frames, within this scene. The endpoint is reached at frame D and held afterward.</small>
      </label>
      {problem && <p role="status" className="production-error">{problem}</p>}
      {locked && <p role="status" className="production-notice">The project or scene is locked for this camera change. Your draft remains editable and retained.</p>}
      {error && <p role="alert" className="production-error">{error}</p>}
      {notice && <p role="status" className="production-notice">{notice}</p>}
      {storageError && <p role="alert" className="production-error">{storageError}</p>}
      <button className="primary-button" disabled={!canApply} onClick={() => void save()}><Save size={14}/>
        {pending ? "Waiting for saved camera…" : stored ? "Update camera reframe" : "Attach camera reframe"}</button>
      <p className="production-help">{dirty ? "Local input differs from the saved camera." : "Camera input is based on the saved project."}
        {" "}Drafts are retained for this browser session; Save writes the project. Ctrl/⌘ S saves from these controls.</p>
      <div className="production-button-row">
        <button className="secondary-button" onClick={onOpenCanvas}><ArrowUpRight size={14}/> Inspect editable objects</button>
        {dirty && !stale && <button className="text-button" disabled={pending} onClick={() => setConfirmReplace(true)}>Use saved camera…</button>}
        {stored && <button className="text-button" disabled={busy || stale || locked || pending || dirty || !!recoveryError} onClick={() => void detach()}>
          Detach, preserve final camera</button>}
      </div>
    </section>
    <section className="production-viewer" aria-label="Camera reframe editorial viewer" style={{ overflowY: "auto" }}>
      <div className="production-viewer-toolbar"><div><strong>{finalPose ? "Authored final pose" : `Camera sample ${frame} / ${lastFrame}`}</strong>
        <span>1920 × 1080 project canvas · editorial preview</span></div>
        <label><input type="checkbox" checked={safeGuide} onChange={event => setSafeGuide(event.target.checked)}/> Safe-area guide</label>
      </div>
      <div className="production-preview-mat">
        {transform ? <svg className="production-study" viewBox="0 0 1920 1080" role="img"
          aria-label={finalPose ? "Authored final camera pose, editorial preview" : `Camera sample ${frame} at ${frame}/30 seconds, editorial preview`}>
          <defs><clipPath id={viewportClip}><rect width={1920} height={1080}/></clipPath></defs>
          <rect data-camera-matte="true" width={1920} height={1080} fill={background}/>
          <g clipPath={`url(#${viewportClip})`}>
            <g data-camera-viewport="true" transform={transform} aria-hidden="true">
              <CompositionStudy nodes={scene.nodes} width={1920} height={1080} time={sampleTime} background="#00000000" label="Unchanged world objects"/>
            </g>
          </g>
          {safeGuide && <rect x={1920 * Math.max(.05, scene.camera.safe_margin)} y={1080 * Math.max(.05, scene.camera.safe_margin)}
            width={1920 * (1 - 2 * Math.max(.05, scene.camera.safe_margin))} height={1080 * (1 - 2 * Math.max(.05, scene.camera.safe_margin))}
            fill="none" stroke="#FFFFFF" strokeOpacity={.45} strokeDasharray="8 8" vectorEffect="non-scaling-stroke" pointerEvents="none"/>}
        </svg> : <p role="status">Resolve the camera source requirements to preview this draft.</p>}
      </div>
      <div className="production-transport">
        <span className="mono">{finalPose ? "Final pose" : `${frame}/30 s`}</span>
        <input aria-label="Camera reframe playhead" type="range" min={0} max={lastFrame} step={1} value={frame}
          disabled={!transform || finalPose} onChange={event => { setFinalPose(false); onSeek(seconds(scene.start) + Number(event.target.value) / 30); }}/>
        <button className="text-button" disabled={!draft || !!problem} aria-pressed={finalPose} onClick={() => setFinalPose(value => !value)}>
          {finalPose ? "Return to scene samples" : "Inspect final pose"}</button>
      </div>
      <div className="production-evidence-note" style={{ flexShrink: 0 }}>
        <strong>Editorial view · native frames require render review</strong>
        <p>{finalPose ? "The final pose is an editable state. It does not add a frame to the scene. " : "Scene samples run from frame 0 through N−1. "}
          The preview applies the camera once over unchanged objects. It does not certify native pixels, audio, export or creative acceptance.</p>
        {renderNotes.length > 0 && <details open><summary>Current Manim render requirements</summary><ul>{renderNotes.map(note => <li key={note}>{note}</li>)}</ul></details>}
        {onOpenRender && <button className="secondary-button" onClick={onOpenRender}>Review render requirements</button>}
      </div>
    </section>
  </div>;
}
