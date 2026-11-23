import { useEffect, useRef, useState } from "react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import { defaultNativePoseDraft, nativePoseEdit, savedNativePoseDraft, type NativePoseDraft } from "./nativePoseDraft";

export default function NativePoseMotion({ project, scene, node, profile, commit }: {
  project: Project; scene: Scene; node: CanvasNode;
  profile: Project["deliverables"][number] | null;
  commit: (change: Change) => Promise<Project | void>;
}) {
  const source = JSON.stringify([node, scene.duration, profile?.id, profile?.frame_rate]);
  const [draft, setDraft] = useState<NativePoseDraft>(() => savedNativePoseDraft(node, profile) ?? defaultNativePoseDraft(node));
  const [draftSource, setDraftSource] = useState(source);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [resolutionRequired, setResolutionRequired] = useState(false);
  const stale = draftSource !== source;
  useEffect(() => {
    if (stale && !dirty && !busy && !resolutionRequired) {
      setDraft(savedNativePoseDraft(node, profile) ?? defaultNativePoseDraft(node));
      setDraftSource(source);
      setError(null);
    }
  }, [source, stale, dirty, busy, resolutionRequired, node, profile]);
  const edit = nativePoseEdit(project, scene, node, profile, draft);
  const changeDraft = (update: (current: NativePoseDraft) => NativePoseDraft) => {
    setDirty(true); setDraft(update);
  };
  const resetToSaved = () => {
    setDraft(savedNativePoseDraft(node, profile) ?? defaultNativePoseDraft(node));
    setDraftSource(source); setDirty(false); setError(null);
  };
  const save = async () => {
    if (!edit.keys || pending.current || stale || resolutionRequired) return;
    pending.current = true; setBusy(true); setError(null);
    try {
      await commit({ type: "set_canvas_motion_keyframes", scene_id: scene.id, node_id: node.id, keyframes: edit.keys });
      setDirty(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The motion edit could not be saved.");
      setResolutionRequired(true);
    } finally { pending.current = false; setBusy(false); }
  };
  return <section className="native-motion-authoring" aria-label="Native pose animation">
    <div className="motion-editor-heading"><strong>{node.keyframes.length ? "Edit the saved pose animation" : "Animate a complete pose"}</strong></div>
    <p className="inspector-note">Move, resize and rotate into the saved pose, with an optional fade. All keys save together in one editable revision.</p>
    <div className="canvas-field-grid motion-fields">
      {([["Start X", "x"], ["Start Y", "y"], ["Start width", "width"], ["Start height", "height"], ["Start rotation", "rotation_deg"]] as const)
        .map(([label, property]) => <label key={property}><span>{label}</span>
          <input disabled={busy || resolutionRequired} type="number" aria-label={"Pose " + label.toLowerCase()} step="1"
            value={Number.isNaN(draft.start[property]) ? "" : draft.start[property]}
            onChange={event => changeDraft(current => ({ ...current, start: { ...current.start, [property]: event.target.value === "" ? NaN : Number(event.target.value) } }))} />
        </label>)}
      <label><span>End frame</span><input disabled={busy || resolutionRequired} type="number" min="1" max="36000" step="1" aria-label="Pose end frame"
        value={Number.isNaN(draft.endFrame) ? "" : draft.endFrame}
        onChange={event => changeDraft(current => ({ ...current, endFrame: event.target.value === "" ? NaN : Number(event.target.value) }))} /></label>
      <label><span>Geometry curve</span><select disabled={busy || resolutionRequired} aria-label="Pose geometry curve" value={draft.curve}
        onChange={event => changeDraft(current => ({ ...current, curve: event.target.value as NativePoseDraft["curve"] }))}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Cubic ease-out</option><option value="hold">Hold then cut</option>
      </select></label>
      <label><span>Opacity</span><select disabled={busy || resolutionRequired} aria-label="Pose opacity" value={draft.fade}
        onChange={event => changeDraft(current => ({ ...current, fade: event.target.value as NativePoseDraft["fade"] }))}>
        <option value="none">Keep visible</option><option value="in">Fade in</option><option value="out">Fade out</option>
      </select></label>
      {draft.fade !== "none" && <label><span>Fade curve</span><select disabled={busy || resolutionRequired} aria-label="Pose fade curve" value={draft.fadeCurve}
        onChange={event => changeDraft(current => ({ ...current, fadeCurve: event.target.value as NativePoseDraft["fadeCurve"] }))}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Cubic ease-out</option>
      </select></label>}
    </div>
    {edit.issue && <p className="inspector-note">{edit.issue}</p>}
    {stale && dirty && !resolutionRequired && <div role="alert">
      <p>The saved object changed while this draft was open. Your draft is still shown.</p>
      <button className="button" type="button" disabled={busy} onClick={resetToSaved}>Use saved pose</button>
    </div>}
    {error && <p role="alert">{error}</p>}
    {resolutionRequired && <div>
      <p className="inspector-note">The save has an unresolved result. Reload the project to inspect its saved state before another edit.</p>
      <button className="button" type="button" onClick={() => window.location.reload()}>Reload project to resolve save</button>
    </div>}
    <button className="button full" type="button" disabled={busy || stale || resolutionRequired || !edit.keys} onClick={() => void save()}>
      {busy ? "Saving pose animation..." : node.keyframes.length ? "Save native pose animation" : "Create native pose animation"}
    </button>
    <p className="inspector-note">A root Clip keeps its clipping plane during this motion. Native preflight checks the selected output and framing.</p>
  </section>;
}
