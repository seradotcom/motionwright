import { useEffect, useState } from "react";
import type { CanvasNode, Change, Project, Scene } from "./types";
import { MAX_POSE_WAYPOINTS, nativePoseEdit, type NativePoseDraft } from "./nativePoseDraft";
import { bootstrap, projectHistory } from "./api";
import { acknowledgePose, decodePoseRecovery, freshPoseRecovery, parsePoseRaw, poseConflict,
  poseRecoveryKey, poseScope, preparePose, rebasePose, writePoseRecovery, inspectPoseOutcome, poseReviewMatches, retainSeparatePose, type PoseOutcomeReview, type PoseRaw, type PoseRecovery, type PoseWaypointRaw } from "./nativePoseRecovery";

type Props = {
  project: Project; scene: Scene; node: CanvasNode;
  profile: Project["deliverables"][number] | null;
  commit: (change: Change) => Promise<Project | void>;
};
type Session = { draft: PoseRecovery; retained: string | null; busy: boolean; backupError: string | null;
  corrupt: boolean; error: string | null; review: PoseOutcomeReview | null; listeners: Set<() => void> };
const sessions = new Map<string, Session>();
const message = (cause: unknown) => cause instanceof Error ? cause.message : String(cause);
function open(props: Props): Session {
  const { project, scene, node, profile } = props, scope = poseScope(project, scene, node);
  const existing = sessions.get(scope);
  if (existing) return existing;
  const session: Session = { draft: freshPoseRecovery(project, scene, node, profile), retained: null,
    busy: false, backupError: null, corrupt: false, error: null, review: null, listeners: new Set() };
  try {
    session.retained = window.localStorage.getItem(poseRecoveryKey(scope));
    if (session.retained !== null) session.draft = decodePoseRecovery(session.retained, scope);
  } catch (cause) {
    session.corrupt = true;
    session.backupError = message(cause);
  }
  sessions.set(scope, session);
  return session;
}
function publish(session: Session) {
  for (const listener of session.listeners) listener();
}
function backup(session: Session, next: PoseRecovery): boolean {
  if (session.corrupt) return false;
  try {
    session.retained = writePoseRecovery(window.localStorage, next, session.retained);
    session.draft = next;
    session.backupError = null;
    publish(session);
    return true;
  } catch (cause) {
    session.backupError = message(cause);
    publish(session);
    return false;
  }
}

export default function NativePoseMotion(props: Props) {
  const { project, scene, node, profile, commit } = props;
  const [session] = useState(() => open(props));
  const [, redraw] = useState(0);
  const { draft, busy, backupError, corrupt, error } = session;
  const stale = poseConflict(draft, project, scene, node, profile);
  const unresolved = draft.pending !== null;
  useEffect(() => {
    const listener = () => redraw(value => value + 1);
    session.listeners.add(listener);
    listener();
    const check = (event?: StorageEvent) => {
      const key = poseRecoveryKey(session.draft.authority.scope);
      if (event && event.key !== null && event.key !== key) return;
      try {
        if (window.localStorage.getItem(key) !== session.retained) {
          session.backupError = "Another window changed the retained pose. Your current input is preserved in this session. No automatic mutation will be sent.";
          publish(session);
        }
      } catch (cause) {
        session.backupError = message(cause);
        publish(session);
      }
    };
    check();
    window.addEventListener("storage", check);
    return () => { session.listeners.delete(listener); window.removeEventListener("storage", check); };
  }, [session]);
  useEffect(() => {
    if (stale && !draft.dirty && !busy && !unresolved && !backupError && project.revision >= draft.authority.revision) {
      session.draft = freshPoseRecovery(project, scene, node, profile, draft.blocked);
      session.error = null;
      publish(session);
    }
  }, [session, stale, draft, busy, unresolved, backupError, project, scene, node, profile]);
  let issue: string | null = null;
  try { issue = nativePoseEdit(project, scene, node, profile, parsePoseRaw(draft.raw)).issue; }
  catch (cause) { issue = message(cause); }
  const replaceRaw = (raw: PoseRaw) => {
    if (session.busy || session.draft.pending || session.corrupt) return;
    const next: PoseRecovery = { ...session.draft, dirty: true, raw };
    session.draft = next; // Keep the original exact input even if local storage fails.
    session.error = null;
    backup(session, next);
  };
  const changeRaw = (field: Exclude<keyof PoseRaw, "waypoints">, value: string) =>
    replaceRaw({ ...session.draft.raw, [field]: value });
  const changeWaypoint = (index: number, field: keyof PoseWaypointRaw, value: string) => {
    const raw = session.draft.raw, waypoints = [...(raw.waypoints ?? [])];
    if (!waypoints[index]) return;
    waypoints[index] = { ...waypoints[index], [field]: value };
    replaceRaw({ ...raw, waypoints });
  };
  const addWaypoint = () => {
    const raw = session.draft.raw, waypoints = [...(raw.waypoints ?? [])];
    if (waypoints.length >= MAX_POSE_WAYPOINTS) return;
    const prior = Number(waypoints.at(-1)?.frame ?? "0"), end = Number(raw.endFrame);
    const midpoint = Math.floor((prior + end) / 2);
    const frame = Number.isSafeInteger(midpoint) && midpoint > prior && midpoint < end ? String(midpoint) : "";
    const previous = waypoints.at(-1) ?? raw;
    waypoints.push({ frame, curve: "linear", x: previous.x, y: previous.y, width: previous.width,
      height: previous.height, rotation_deg: previous.rotation_deg });
    replaceRaw({ ...raw, waypoints });
  };
  const removeWaypoint = (index: number) => {
    const raw = session.draft.raw;
    replaceRaw({ ...raw, waypoints: (raw.waypoints ?? []).filter((_, i) => i !== index) });
  };
  const useSaved = () => {
    if (session.busy || session.draft.pending || session.corrupt) return;
    session.error = null;
    backup(session, freshPoseRecovery(project, scene, node, profile, session.draft.blocked));
  };
  const rebase = () => {
    if (session.busy || session.draft.pending || session.corrupt) return;
    try { backup(session, rebasePose(session.draft, project, scene, node, profile)); }
    catch (cause) { session.error = message(cause); publish(session); }
  };
  const save = async () => {
    if (session.busy || session.draft.pending || session.backupError || session.corrupt) return;
    session.error = null;
    let submission: ReturnType<typeof preparePose>;
    try { submission = preparePose(session.draft, project, scene, node, profile, crypto.randomUUID()); }
    catch (cause) { session.error = message(cause); publish(session); return; }
    // The original intent and exact source must be durably retained before any
    // write. Never dispatch from hydration, source observation or reload.
    if (!backup(session, submission.draft)) return;
    session.busy = true;
    publish(session);
    try {
      const receipt = await commit(submission.change);
      if (!receipt) throw new Error("The original mutation did not return its saved project.");
      const confirmed = acknowledgePose(session.draft, submission.draft.pending!.id, receipt);
      if (!backup(session, confirmed)) throw new Error("The write replied but its local confirmation could not be retained. The original attempt remains locked.");
    } catch (cause) {
      session.error = message(cause);
    } finally {
      session.busy = false;
      publish(session);
    }
  };
  const reviewOutcome = async () => {
    if (session.busy || !session.draft.pending || session.backupError || session.corrupt) return;
    session.busy = true;
    session.review = null;
    session.error = null;
    publish(session);
    try {
      if (!("__TAURI_INTERNALS__" in window)) throw new Error("Review requires the actual StudioService.");
      const review = await inspectPoseOutcome(session.draft, project, {
        readProject: async id => {
          const result = await bootstrap(id);
          if (result.project_selection.status !== "selected" || !result.project || result.project.id !== id)
            throw new Error("The saved project is unavailable.");
          return result.project;
        },
        readHistory: projectHistory,
      });
      if (!poseReviewMatches(session.draft, project, review)) throw new Error("The pose changed during review.");
      session.review = review;
    } catch (cause) { session.error = message(cause); }
    finally { session.busy = false; publish(session); }
  };
  const separate = () => {
    if (session.busy || session.backupError || !session.review) return;
    try {
      const next = retainSeparatePose(window.localStorage, session.draft, session.retained,
        project, scene, node, profile, session.review);
      session.draft = next.draft;
      session.retained = next.raw;
      session.review = null;
      session.error = null;
      publish(session);
    } catch (cause) { session.error = message(cause); publish(session); }
  };
  const reviewCurrent = poseReviewMatches(draft, project, session.review);
  const fieldsLocked = busy || unresolved || corrupt;
  return <section className="native-motion-authoring" aria-label="Native pose animation">
    <div className="motion-editor-heading"><strong>{node.keyframes.length ? "Edit the saved pose animation" : "Animate a complete pose"}</strong></div>
    <p className="inspector-note">Move, resize and rotate through up to four intermediate poses into the saved destination. Each incoming geometry curve and fade timing is editable. All keys save together in one revision.</p>
    {scene.beats.length > 0 && <p className="inspector-note" aria-label="Editorial animation continuity">Animation uses the scene clock. Editorial beats keep their timing without restarting poses, opacity, Follow or camera motion.</p>}
    <div className="canvas-field-grid motion-fields">
      {([["Start X", "x"], ["Start Y", "y"], ["Start width", "width"], ["Start height", "height"], ["Start rotation", "rotation_deg"]] as const)
        .map(([label, property]) => <label key={property}><span>{label}</span>
          <input disabled={fieldsLocked} type="text" inputMode="decimal" maxLength={64} aria-label={"Pose " + label.toLowerCase()}
            value={draft.raw[property]} onChange={event => changeRaw(property, event.target.value)} />
        </label>)}
      <label><span>End frame</span><input disabled={fieldsLocked} type="text" inputMode="numeric" maxLength={64} aria-label="Pose end frame"
        value={draft.raw.endFrame} onChange={event => changeRaw("endFrame", event.target.value)} /></label>
      <label><span>Curve into destination</span><select disabled={fieldsLocked} aria-label="Pose geometry curve" value={draft.raw.curve}
        onChange={event => changeRaw("curve", event.target.value as NativePoseDraft["curve"])}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Cubic ease-out</option><option value="hold">Hold then cut</option>
      </select></label>
      <label><span>Opacity</span><select disabled={fieldsLocked} aria-label="Pose opacity" value={draft.raw.fade}
        onChange={event => changeRaw("fade", event.target.value as NativePoseDraft["fade"])}>
        <option value="none">Keep visible</option><option value="in">Fade in</option><option value="out">Fade out</option>
        <option value="in_out">Fade in, hold, fade out</option>
      </select></label>
      {draft.raw.fade !== "none" && <label><span>{draft.raw.fade === "in_out" ? "Fade-in curve" : "Fade curve"}</span><select disabled={fieldsLocked} aria-label="Pose fade curve" value={draft.raw.fadeCurve}
        onChange={event => changeRaw("fadeCurve", event.target.value as NativePoseDraft["fadeCurve"])}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Cubic ease-out</option>
      </select></label>}
      {draft.raw.fade !== "none" && <label><span>{draft.raw.fade === "in_out" ? "Fade-in end frame (optional)" : "Fade end frame (optional)"}</span>
        <input disabled={fieldsLocked} type="text" inputMode="numeric" maxLength={64}
          aria-label="Pose fade end frame" placeholder="Same as geometry" value={draft.raw.fadeEndFrame ?? ""}
          onChange={event => changeRaw("fadeEndFrame", event.target.value)} />
      </label>}
      {draft.raw.fade === "in_out" && <>
        <label><span>Fade-out start frame</span>
          <input disabled={fieldsLocked} type="text" inputMode="numeric" maxLength={64}
            aria-label="Pose fade-out start frame" value={draft.raw.fadeOutStartFrame ?? ""}
            onChange={event => changeRaw("fadeOutStartFrame", event.target.value)} />
        </label>
        <label><span>Fade-out end frame</span>
          <input disabled={fieldsLocked} type="text" inputMode="numeric" maxLength={64}
            aria-label="Pose fade-out end frame" value={draft.raw.fadeOutEndFrame ?? ""}
            onChange={event => changeRaw("fadeOutEndFrame", event.target.value)} />
        </label>
        <label><span>Fade-out curve</span>
          <select disabled={fieldsLocked} aria-label="Pose fade-out curve" value={draft.raw.fadeOutCurve ?? draft.raw.fadeCurve}
            onChange={event => changeRaw("fadeOutCurve", event.target.value)}>
            <option value="linear">Linear</option><option value="ease_out_cubic">Cubic ease-out</option>
          </select>
        </label>
      </>}
    </div>
    {(draft.raw.waypoints ?? []).map((waypoint, index) => <fieldset className="canvas-field-grid motion-fields" key={index}>
      <legend>Intermediate pose {index + 1}</legend>
      <label><span>Frame</span><input disabled={fieldsLocked} type="text" inputMode="numeric" maxLength={64}
        aria-label={"Intermediate pose " + (index + 1) + " frame"} value={waypoint.frame}
        onChange={event => changeWaypoint(index, "frame", event.target.value)} /></label>
      {([["X", "x"], ["Y", "y"], ["Width", "width"], ["Height", "height"], ["Rotation", "rotation_deg"]] as const)
        .map(([label, field]) => <label key={field}><span>{label}</span>
          <input disabled={fieldsLocked} type="text" inputMode="decimal" maxLength={64}
            aria-label={"Intermediate pose " + (index + 1) + " " + label.toLowerCase()}
            value={waypoint[field]} onChange={event => changeWaypoint(index, field, event.target.value)} />
        </label>)}
      <label><span>Incoming curve</span><select disabled={fieldsLocked}
        aria-label={"Intermediate pose " + (index + 1) + " curve"} value={waypoint.curve}
        onChange={event => changeWaypoint(index, "curve", event.target.value)}>
        <option value="linear">Linear</option><option value="ease_out_cubic">Cubic ease-out</option><option value="hold">Hold then cut</option>
      </select></label>
      <button className="button" type="button" disabled={fieldsLocked} onClick={() => removeWaypoint(index)}
        aria-label={"Remove intermediate pose " + (index + 1)}>Remove pose</button>
    </fieldset>)}
    <button className="button full" type="button" disabled={fieldsLocked || (draft.raw.waypoints?.length ?? 0) >= MAX_POSE_WAYPOINTS}
      onClick={addWaypoint}>Add intermediate pose</button>
    {draft.dirty && !backupError && <p className="inspector-note">Pose draft retained locally, based on revision {draft.authority.revision}. It is not saved project content.</p>}
    {issue && <p className="inspector-note">{issue}</p>}
    {stale && draft.dirty && !unresolved && <div role="alert">
      <p>The saved project, selected object, parent Clip or output context changed. Your exact draft remains.</p>
      <button className="button" type="button" disabled={busy || !!backupError || corrupt || project.revision < draft.authority.revision} onClick={useSaved}>Use saved pose</button>
      <button className="button" type="button" disabled={busy || !!backupError || corrupt || project.revision < draft.authority.revision} onClick={rebase}>Keep draft on reviewed source</button>
    </div>}
    {backupError && <div role="alert">
      <p>Pose recovery is blocked: {backupError} No automatic mutation will be sent.</p>
      {!corrupt && !busy && <button className="button" type="button" onClick={() => backup(session, session.draft)}>Retry local backup only</button>}
      <details><summary>Copy retained input</summary><textarea aria-label="Pose recovery copy" readOnly value={JSON.stringify(draft, null, 2)} /></details>
    </div>}
    {error && <p role="alert">{error}</p>}
    {unresolved && <div role="alert">
      <p>The original pose save at revision {draft.authority.revision} has an unresolved result. The submitted values are preserved. A reload or matching saved pose does not acknowledge it; no mutation will be retried.</p>
      <p>Current visible project revision: {project.revision}. Saved motion keys: {node.keyframes.length}.</p>
      <button className="button" type="button" disabled={busy || !!backupError} onClick={() => window.location.reload()}>Reload project to inspect saved pose</button>
      <button className="button" type="button" disabled={busy || !!backupError} onClick={() => void reviewOutcome()}>Read saved pose history</button>
      {reviewCurrent && <div>
        <p>Readback found {session.review!.matchingValueRevisions.length} matching change(s) in the saved journal. This is evidence of values, not acknowledgement of the original attempt.</p>
        <button className="button" type="button" disabled={busy || !!backupError || profile?.id !== draft.authority.profileId} onClick={separate}>Retain unknown attempt and start a distinct edit</button>
      </div>}
    </div>}
    <button className="button full" type="button" disabled={busy || stale || unresolved || !!backupError || corrupt || !!issue} onClick={() => void save()}>
      {busy ? "Saving pose animation..." : node.keyframes.length ? "Save native pose animation" : "Create native pose animation"}
    </button>
    <p className="inspector-note">A static root Group keeps its local frame; a Clip keeps its clipping plane during this motion. Camera framing and the selected output are checked before rendering.</p>
  </section>;
}
