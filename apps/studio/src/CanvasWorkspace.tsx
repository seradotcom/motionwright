import { previewTransform } from "./canvasMotion";
import { Camera, CircleDashed, LockKeyhole, Move, Plus, Unlock } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import CanvasStructureEditor from "./CanvasStructureEditor";
import TypographySafetyReview from "./TypographySafetyReview";
import {
  cameraLayerTransform, cameraPointerDelta,
  PROJECT_CANVAS_HEIGHT as PROJECT_HEIGHT,
  PROJECT_CANVAS_WIDTH as PROJECT_WIDTH,
} from "./canvasCamera";
import type {
  CanvasNode,
  CanvasTransform,
  Change,
  MotionInterpolation,
  MotionProperty,
  NodeProperty,
  Project,
  Scene,
} from "./types";
import { rationalSeconds, seconds } from "./types";
import VisualLanguageEditor from "./VisualLanguageEditor";
import {
  CANVAS_DRAFT_PREFIX, CANVAS_FIELD_LABELS, CANVAS_TRANSFORM_FIELDS, canvasContextConflicts, canvasDraftConflicts, canvasDraftHasWork,
  canvasDraftScope, canvasFieldHasInput, canvasFieldIssue, canvasGroupIssue, canvasNumber,
  canvasSubmissionMatches, createCanvasDraft, decodeCanvasRecovery, discardCanvasRecovery, editCanvasField, prepareCanvasSubmission,
  readCanvasRecoveries, reconcileCanvasDraft, releaseCanvasSubmission, resolveCanvasField, reviewCanvasContext, writeCanvasRecovery,
} from "./canvasWorkspaceDraft";
import type { CanvasDraftField, CanvasDraftGroup, CanvasRecoveryEntry, CanvasWorkspaceDraft } from "./canvasWorkspaceDraft";

type Commit = (change: Change) => Promise<void>;

function nodeTransform(node: CanvasNode): CanvasTransform {
  return {
    x: node.x,
    y: node.y,
    width: node.width,
    height: node.height,
    rotation_deg: node.rotation_deg,
    opacity: node.opacity,
  };
}

function numberValue(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

const MOTION_PROPERTIES: MotionProperty[] = ["x", "y", "width", "height", "rotation_deg", "opacity"];

function propertyLock(property: MotionProperty): NodeProperty {
  if (property === "x" || property === "y") return "position";
  if (property === "width" || property === "height") return "size";
  if (property === "rotation_deg") return "rotation";
  return "opacity";
}

function createNode(scene: Scene, kind: "text" | "shape" | "group"): CanvasNode {
  const count = scene.nodes.filter((node) => node.kind === kind).length + 1;
  const zIndex = scene.nodes.reduce((highest, node) => Math.max(highest, node.z_index), 0) + 1;
  const dimensions = kind === "text"
    ? { width: 520, height: 120 }
    : kind === "group"
      ? { width: 640, height: 360 }
      : { width: 360, height: 220 };
  return {
    id: crypto.randomUUID(),
    name: kind[0].toUpperCase() + kind.slice(1) + " " + count,
    kind,
    parent_id: null,
    x: 240 + (count % 4) * 34,
    y: 220 + (count % 4) * 34,
    width: dimensions.width,
    height: dimensions.height,
    rotation_deg: 0,
    opacity: 1,
    text: kind === "text" ? "Text" : null,
    coordinate_space: "project_pixels",
    z_index: zIndex,
    style: {
      fill: kind === "text" || kind === "group" ? null : "#1D242C",
      stroke: kind === "text" ? null : "#5A6570",
      stroke_width: kind === "text" ? 0 : 1,
      font_family: kind === "text" ? "Instrument Sans Variable" : null,
      font_size: kind === "text" ? 64 : null,
      font_weight: kind === "text" ? 700 : null,
      line_height: kind === "text" ? 1.05 : null,
      blend_mode: "normal",
    },
    relations: [],
    property_locks: [],
    keyframes: [],
  };
}

const CANVAS_SESSION_PREFIX = "motionwright.canvas-session.v1:";
// Scoped here so the object editor can evolve without changing shared Studio
// surfaces owned by other workstreams.
const CANVAS_DRAFT_STYLES = `
.canvas-draft-inspector {display:grid;gap:9px;padding:0 10px 12px;font-size:11px;line-height:1.5;}
.canvas-draft-inspector p {margin:0;}
.canvas-draft-inspector .inspector-note {color:var(--muted);}
.canvas-draft-inspector .canvas-field-grid {padding:0;align-items:start;}
.canvas-draft-inspector .canvas-field-grid label {align-content:start;}
.canvas-draft-inspector .canvas-field-grid .production-error {font-size:10px;line-height:1.45;text-transform:none;letter-spacing:normal;}
.canvas-draft-inspector .canvas-text-editor {padding:0;gap:4px;}
.canvas-draft-inspector .button.full {width:100%;margin:0;}
.canvas-draft-inspector .production-notice {padding:10px;gap:7px;}
.canvas-draft-inspector dl {display:grid;gap:6px;margin:8px 0;}
.canvas-draft-inspector dt {font-size:9px;color:var(--muted);}
.canvas-draft-inspector dd {margin:0;color:var(--text);}
.canvas-draft-inspector details {border:1px solid var(--border);border-radius:6px;padding:8px;background:var(--surface-2);}
.canvas-draft-inspector summary {cursor:pointer;font-weight:600;}
.canvas-draft-inspector details>div {border-top:1px solid var(--border-soft);padding-top:7px;margin-top:7px;}
`;
type CanvasDraftSession = { draft: CanvasWorkspaceDraft; writerId: string; lastRaw: string | null; listeners: Set<() => void> };
// Synchronous durable writes are primary. The cache also preserves navigation
// during a storage outage, which is reported before a user leaves the editor.
const liveCanvasDrafts = new Map<string, CanvasDraftSession>();
function recoverCanvasDraft(project: Project, sceneId: string, nodeId: string) {
  const scope = canvasDraftScope(project, sceneId, nodeId);
  let copies: ReturnType<typeof readCanvasRecoveries> = { entries: [], unreadable: [], limited: false };
  let backupError: string | null = null, previousWriter: string | null = null;
  try { copies = readCanvasRecoveries(window.localStorage, scope); }
  catch (reason) { backupError = reason instanceof Error ? reason.message : "Local Canvas recovery is unavailable."; }
  try { previousWriter = window.sessionStorage.getItem(CANVAS_SESSION_PREFIX + scope); } catch { /* Local copies remain available. */ }
  const cached = liveCanvasDrafts.get(scope);
  // Other windows' input is available through explicit Restore. An absent or
  // damaged own pointer never authorizes adopting somebody else's draft.
  const recovered = copies.entries.find(entry => entry.value.writerId === previousWriter);
  const draft = reconcileCanvasDraft(cached?.draft ?? recovered?.value.draft ?? createCanvasDraft(project, sceneId, nodeId), project);
  const session = cached ?? { draft, writerId: crypto.randomUUID(), lastRaw: null, listeners: new Set<() => void>() };
  session.draft = draft;
  return { session, copies, backupError,
    notice: canvasDraftHasWork(draft) ? "Recovered your local Canvas input. Commit to apply it." : null };
}

function CanvasDraftInspector({ project, scene, node, commit, dragPreview }: {
  project: Project; scene: Scene; node: CanvasNode; commit: Commit; dragPreview?: CanvasTransform;
}) {
  const [initial] = useState(() => recoverCanvasDraft(project, scene.id, node.id));
  const session = useRef(initial.session);
  const [draft, setDraft] = useState(initial.session.draft);
  const projectRef = useRef(project); projectRef.current = project;
  const [copies, setCopies] = useState(initial.copies);
  const [backupError, setBackupError] = useState(initial.backupError);
  const [notice, setNotice] = useState<string | null>(initial.notice);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false), mounted = useRef(true);
  const scope = draft.scope;
  const conflicts = canvasDraftConflicts(draft, project);
  const contextConflicts = canvasContextConflicts(draft, project);
  const scenePositionLocked = project.locks.some(lock => lock.resource === "scene:" + scene.id && lock.kind === "position");
  const sceneContentLocked = project.locks.some(lock => lock.resource === "scene:" + scene.id && lock.kind === "content");
  const fieldId = (field: CanvasDraftField) => "canvas-draft-" + node.id + "-" + field;

  function refreshCopies() {
    try { if (mounted.current) setCopies(readCanvasRecoveries(window.localStorage, scope)); }
    catch (reason) { if (mounted.current) setBackupError(reason instanceof Error ? reason.message : "Local Canvas recovery is unavailable."); }
  }
  function backup(next: CanvasWorkspaceDraft): boolean {
    try {
      const writer = session.current;
      if (canvasDraftHasWork(next) && writer.lastRaw !== null && decodeCanvasRecovery(writer.lastRaw, scope, writer.writerId).draft === null) {
        writer.writerId = crypto.randomUUID(); writer.lastRaw = null;
      }
      writer.lastRaw = writeCanvasRecovery(window.localStorage, { schema: "motionwright.canvas-recovery/1", scope,
        writerId: writer.writerId, updatedAt: Date.now(), draft: canvasDraftHasWork(next) ? next : null }, writer.lastRaw);
      try { window.sessionStorage.setItem(CANVAS_SESSION_PREFIX + scope, writer.writerId); } catch { /* The local copy is still durable. */ }
      if (mounted.current) setBackupError(null);
      return true;
    } catch (reason) {
      if (mounted.current) setBackupError((reason instanceof Error ? reason.message : "Local recovery is unavailable.")
        + " Your input is kept in this session. The latest edit may not survive reloading until backup succeeds.");
      return false;
    }
  }
  function replace(next: CanvasWorkspaceDraft): boolean {
    const writer = session.current;
    if (canvasDraftHasWork(next) && !canvasDraftHasWork(writer.draft)) { writer.writerId = crypto.randomUUID(); writer.lastRaw = null; }
    writer.draft = next; liveCanvasDrafts.set(scope, writer);
    // Dirty buffers survive failed persistence. Inactive clean buffers can be
    // reconstructed instead of growing with every selected object.
    if (liveCanvasDrafts.size > 128) for (const [key, cached] of liveCanvasDrafts) {
      if (liveCanvasDrafts.size <= 128) break;
      if (key !== scope && cached.listeners.size === 0 && !canvasDraftHasWork(cached.draft)) liveCanvasDrafts.delete(key);
    }
    writer.listeners.forEach(listener => listener());
    if (mounted.current) setDraft(next);
    return backup(next);
  }
  function observe(current: Project) {
    const previous = session.current.draft, attempt = previous.awaiting;
    const next = reconcileCanvasDraft(previous, current);
    if (attempt && next.awaiting === null && mounted.current) {
      if (canvasSubmissionMatches(current, previous, attempt)) {
        setError(null); setNotice("Saved Canvas " + attempt.group + " at r" + current.revision
          + "." + (canvasDraftHasWork(next) ? " Your later and other uncommitted inputs are kept." : ""));
      } else setError("A newer saved revision does not confirm the submitted snapshot. Your input is kept; review any overlapping fields.");
    }
    replace(next);
  }
  useEffect(() => {
    mounted.current = true;
    const writer = session.current, refresh = () => setDraft(writer.draft);
    writer.listeners.add(refresh);
    const storageChanged = (event: StorageEvent) => { if (event.key?.startsWith(CANVAS_DRAFT_PREFIX)) refreshCopies(); };
    window.addEventListener("storage", storageChanged);
    return () => { mounted.current = false; writer.listeners.delete(refresh); window.removeEventListener("storage", storageChanged); };
  }, []);
  useEffect(() => { observe(project); }, [project]);

  function edit(field: CanvasDraftField, raw: string) {
    setError(null); setNotice(null); replace(editCanvasField(session.current.draft, field, raw));
  }
  async function save(group: CanvasDraftGroup) {
    if (inFlight.current) return;
    let attempt: ReturnType<typeof prepareCanvasSubmission>;
    try { attempt = prepareCanvasSubmission(session.current.draft, projectRef.current, group, crypto.randomUUID()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return; }
    // Retain the input snapshot before invoking the service. Recovery never
    // treats this snapshot as permission to replay its operation.
    if (!replace(attempt.draft)) {
      replace(releaseCanvasSubmission(session.current.draft, attempt.submission.id));
      setError("Canvas recovery could not retain this submission. Nothing was sent; retry the backup before saving."); return;
    }
    inFlight.current = true; setPending(true); setError(null); setNotice(null);
    try {
      await commit(attempt.change);
      // React publication can follow a void callback. Only the matching saved
      // Project acknowledges this input; a resolved promise is insufficient.
      if (mounted.current) observe(projectRef.current);
    } catch (reason) {
      replace(releaseCanvasSubmission(session.current.draft, attempt.submission.id));
      if (mounted.current) setError((reason instanceof Error ? reason.message : String(reason)) + " Your Canvas input has been kept.");
    } finally {
      inFlight.current = false; if (mounted.current) setPending(false);
    }
  }
  function resolve(field: CanvasDraftField, choice: "saved" | "draft") {
    if (inFlight.current) return;
    replace(resolveCanvasField(session.current.draft, projectRef.current, field, choice));
    setError(null); setNotice(choice === "saved" ? "Kept the saved " + CANVAS_FIELD_LABELS[field] + ". Other draft fields are unchanged."
      : "Kept your " + CANVAS_FIELD_LABELS[field] + " draft against r" + project.revision + ". Commit explicitly to apply it.");
    document.getElementById(fieldId(field))?.focus();
  }
  function restore(entry: CanvasRecoveryEntry) {
    if (inFlight.current || !entry.value.draft) return;
    try {
      if (window.localStorage.getItem(entry.key) !== entry.raw) {
        setError("This Canvas copy changed after it was shown. Refresh the recovery copies before restoring it."); refreshCopies(); return;
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "This recovery copy is unavailable."); return; }
    if (canvasDraftHasWork(session.current.draft) && !backup(session.current.draft)) {
      setError("Back up the current object draft before replacing it with another recovery copy."); return;
    }
    session.current.writerId = crypto.randomUUID(); session.current.lastRaw = null;
    replace(reconcileCanvasDraft(entry.value.draft, projectRef.current)); refreshCopies();
    setNotice("Restored this object input as a separate local draft. Nothing was committed.");
  }
  function discardCopy(key: string, raw: string) {
    try { discardCanvasRecovery(window.localStorage, key, raw); refreshCopies(); }
    catch (reason) { setBackupError(reason instanceof Error ? reason.message : "The recovery copy could not be removed."); }
  }
  function download(raw: string, name: string) {
    const url = URL.createObjectURL(new Blob([raw], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = name; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  const transformIssue = canvasGroupIssue(draft, project, "transform"), textIssue = canvasGroupIssue(draft, project, "text");
  const earlier = copies.entries.filter(entry => entry.value.writerId !== session.current.writerId);

  return <section className="canvas-draft-inspector" aria-label="Canvas object draft">
    <style>{CANVAS_DRAFT_STYLES}</style>
    {canvasDraftHasWork(draft) && <p className="inspector-note" role="status">
      {pending ? "Saving this snapshot. You can keep editing." : "Local Canvas draft · not yet committed."}
    </p>}
    {notice && <p className="production-notice" role="status">{notice}</p>}
    {error && <p className="production-error" role="alert">{error}</p>}
    {backupError && <section className="production-notice" role="alert" aria-label="Canvas draft backup warning">
      <p>{backupError}</p>
      <button className="button compact" type="button" onClick={() => { backup(session.current.draft); refreshCopies(); }}>Retry Canvas draft backup</button>
      <button className="button compact" type="button" disabled={pending} onClick={() => {
        // A changed existing copy remains untouched. Forking is an explicit
        // recovery choice and never an implicit retry of the service mutation.
        session.current.writerId = crypto.randomUUID(); session.current.lastRaw = null;
        if (replace(session.current.draft)) {
          setError(null); setNotice("Kept your current input in a new recovery copy. The existing copy was left unchanged.");
        }
        refreshCopies();
      }}>Keep Canvas input in a new recovery copy</button>
      <button className="button compact" type="button" onClick={() => download(JSON.stringify(session.current.draft, null, 2), "canvas-object-draft-" + node.id + ".json")}>Download Canvas draft</button>
    </section>}
    {(earlier.length > 0 || copies.unreadable.length > 0 || copies.limited) && <details>
      <summary>Local Canvas recovery copies</summary>
      <p>Other windows' input stays separate until you explicitly restore a copy.</p>
      <button className="button compact" type="button" onClick={refreshCopies}>Refresh Canvas recovery copies</button>
      {copies.limited && <p role="status">Only the first 128 copies for this object were inspected. Discard copies you no longer need to inspect more; none were removed automatically.</p>}
      {copies.unreadable.map(copy => <div key={copy.key}><p role="status">An unreadable Canvas copy was kept unchanged.</p>
        <button className="button compact" type="button" disabled={copy.raw === null} onClick={() => { if (copy.raw !== null) download(copy.raw, "canvas-unreadable-" + copy.writerId + ".txt"); }} aria-label={"Download unreadable Canvas draft " + copy.writerId}>Download original copy</button>
        <button className="button compact" type="button" disabled={copy.raw === null} onClick={() => { if (copy.raw !== null) discardCopy(copy.key, copy.raw); }} aria-label={"Discard unreadable Canvas draft " + copy.writerId}>Discard unreadable copy</button></div>)}
      {earlier.map(entry => <div key={entry.key}>
        <p>{new Date(entry.value.updatedAt).toLocaleString()} · {entry.value.draft ? "local object input" : "clean checkpoint"}</p>
        {entry.value.draft && <button className="button compact" type="button" disabled={pending} onClick={() => restore(entry)} aria-label={"Restore Canvas draft " + entry.value.writerId}>Restore copy</button>}
        <button className="button compact" type="button" onClick={() => download(entry.raw, "canvas-recovery-" + entry.value.writerId + ".json")} aria-label={"Download Canvas draft " + entry.value.writerId}>Download copy</button>
        <button className="button compact" type="button" disabled={pending} onClick={() => discardCopy(entry.key, entry.raw)} aria-label={"Discard Canvas draft " + entry.value.writerId}>Discard copy</button>
      </div>)}
    </details>}
    {draft.awaiting && !pending && <section className="production-notice" role="region" aria-label="Unconfirmed Canvas submission">
      <p>A previous submission has no matching observed saved revision. The retained input will not be sent automatically.</p>
      <button className="button compact" type="button" onClick={() => {
        const current = session.current.draft;
        if (current.awaiting) replace(reconcileCanvasDraft(releaseCanvasSubmission(current, current.awaiting.id), projectRef.current));
        setNotice("Reviewed the retained Canvas input against the current saved revision. Resolve overlaps and commit explicitly.");
      }}>Review Canvas draft on current revision</button>
    </section>}
    {contextConflicts.length > 0 && <section className="production-notice" role="region" aria-label="Canvas object context">
      <strong>Review the changed object context</strong>
      <p>The object's kind, parent or coordinate space changed. The retained {contextConflicts.join(" and ")} input needs your review even if its numbers still match.</p>
      <dl><dt>Original context</dt><dd style={{ overflowWrap: "anywhere" }}>{draft.baseContext.kind} · parent {draft.baseContext.parent_id ?? "scene root"} · {draft.baseContext.coordinate_space}</dd>
        <dt>Saved context</dt><dd style={{ overflowWrap: "anywhere" }}>{node.kind} · parent {node.parent_id ?? "scene root"} · {node.coordinate_space}</dd></dl>
      <button className="button compact" type="button" disabled={pending} onClick={() => {
        replace(reviewCanvasContext(session.current.draft, projectRef.current));
        setNotice("Kept the raw inputs in the current object context. Resolve any value overlaps and commit explicitly.");
      }}>Review Canvas object context</button>
    </section>}
    {conflicts.length > 0 && <section className="production-notice" role="region" aria-label="Canvas conflicts">
      <strong>Review overlapping Canvas changes</strong><p>Your input is kept. Choosing a value does not commit it.</p>
      {conflicts.map(conflict => <section key={conflict.field} aria-label={CANVAS_FIELD_LABELS[conflict.field] + " conflict"}>
        <strong>{CANVAS_FIELD_LABELS[conflict.field]}</strong>
        {conflict.futureRevision && <p>The draft was based on a newer revision than this saved project.</p>}
        <dl>{([["Original", conflict.before], ["Saved", conflict.current], ["Your draft", conflict.proposed]] as const).map(([label, value]) => <div key={label}>
          <dt>{label}</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{value || "(empty)"}</dd>
        </div>)}</dl>
        <button className="button compact" type="button" disabled={pending} onClick={() => resolve(conflict.field, "saved")}>Keep saved {CANVAS_FIELD_LABELS[conflict.field]}</button>
        <button className="button compact" type="button" disabled={pending} onClick={() => resolve(conflict.field, "draft")}>Keep my draft {CANVAS_FIELD_LABELS[conflict.field]}</button>
      </section>)}
    </section>}
    <div className="canvas-field-grid">
      {CANVAS_TRANSFORM_FIELDS.map(field => {
        const input = draft.fields[field], issue = canvasFieldIssue(field, input.raw);
        const shown = dragPreview && !canvasFieldHasInput(input) ? String(dragPreview[field]) : input.raw;
        return <label key={field}><span>{CANVAS_FIELD_LABELS[field]}</span>
          <input id={fieldId(field)} aria-label={"Canvas " + CANVAS_FIELD_LABELS[field]}
            type="text" inputMode="decimal" role="spinbutton"
            aria-valuenow={issue ? undefined : canvasNumber(shown)}
            aria-valuemin={field === "opacity" || field === "width" || field === "height" ? 0 : undefined}
            aria-valuemax={field === "opacity" ? 1 : undefined}
            aria-invalid={Boolean(issue) || conflicts.some(conflict => conflict.field === field)}
            aria-describedby={issue ? fieldId(field) + "-error" : undefined}
            value={shown} onChange={event => edit(field, event.target.value)}
            disabled={scenePositionLocked || node.property_locks.includes(propertyLock(field))}
            onKeyDown={event => {
              if ((event.key !== "ArrowUp" && event.key !== "ArrowDown") || event.ctrlKey || event.metaKey || event.altKey || event.nativeEvent.isComposing) return;
              event.preventDefault(); event.stopPropagation();
              let value = (issue ? node[field] : canvasNumber(input.raw)) + (event.key === "ArrowUp" ? 1 : -1) * (field === "opacity" ? 0.05 : 1);
              if (field === "opacity") value = Math.max(0, Math.min(1, Number(value.toFixed(4))));
              if (field === "width" || field === "height") value = Math.max(0, value);
              edit(field, String(value));
            }}/>
          {issue && <span id={fieldId(field) + "-error"} className="production-error">{issue}</span>}
        </label>;
      })}
    </div>
    <button className="button full" type="button" onClick={() => void save("transform")} disabled={pending || transformIssue !== null} title={transformIssue ?? undefined}>Commit transform</button>
    <label className="canvas-text-editor"><span className="field-label">Text</span>
      <textarea id={fieldId("text")} aria-label="Canvas text" rows={4} value={draft.fields.text.raw}
        disabled={sceneContentLocked || node.property_locks.includes("text")}
        aria-invalid={Boolean(canvasFieldIssue("text", draft.fields.text.raw)) || conflicts.some(conflict => conflict.field === "text")}
        aria-describedby={canvasFieldIssue("text", draft.fields.text.raw) ? fieldId("text") + "-error" : undefined}
        onChange={event => edit("text", event.target.value)}/>
      {canvasFieldIssue("text", draft.fields.text.raw) && <span id={fieldId("text") + "-error"} className="production-error">{canvasFieldIssue("text", draft.fields.text.raw)}</span>}
    </label>
    <button className="button full" type="button" onClick={() => void save("text")} disabled={pending || textIssue !== null} title={textIssue ?? undefined}>Commit text</button>
    {canvasDraftHasWork(draft) && <button className="button full" type="button" disabled={pending} onClick={() => {
      replace(createCanvasDraft(projectRef.current, scene.id, node.id));
      setError(null); setNotice("Discarded this object's local input. The saved project and other object drafts are unchanged.");
    }}>Discard Canvas object draft</button>}
  </section>;
}

export default function CanvasWorkspace({
  project,
  scene,
  commit,
  commitRequired,
  playhead,
  onSeek,
  profile,
}: {
  project: Project;
  scene: Scene | null;
  commit: Commit;
  commitRequired: Commit;
  playhead: number;
  onSeek: (absoluteSeconds: number) => void;
  profile: Project["deliverables"][number] | null;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(scene?.nodes[0]?.id ?? null);
  const [addBusy, setAddBusy] = useState(false);
  const addPending = useRef(false);
  const [addError, setAddError] = useState<string | null>(null);
  const creationScope = JSON.stringify([project.id, project.generation, project.active_branch, scene?.id ?? null]);
  const visibleCreationScope = useRef(creationScope);
  visibleCreationScope.current = creationScope;
  const localInteractionVersion = useRef(0);
  const [draftTransforms, setDraftTransforms] = useState<Record<string, CanvasTransform>>({});
  const [motionProperty, setMotionProperty] = useState<MotionProperty>("opacity");
  const [motionValue, setMotionValue] = useState(scene?.nodes[0]?.opacity ?? 1);
  const [motionInterpolation, setMotionInterpolation] = useState<MotionInterpolation>("linear");
  // Explicit opt-in: dragging writes one pair of motion keys instead of
  // silently altering the base layout when editing a later animation time.
  const [autoKeyPosition, setAutoKeyPosition] = useState(false);
  const [nativeStartX, setNativeStartX] = useState(scene?.nodes[0] ? scene.nodes[0].x - 100 : 0);
  const [nativeStartY, setNativeStartY] = useState(scene?.nodes[0]?.y ?? 0);
  const [nativeEndFrame, setNativeEndFrame] = useState(30);
  const [nativeMotionCurve, setNativeMotionCurve] = useState<"linear" | "ease_out_cubic">("linear");
  const drag = useRef<{
    pointerId: number;
    nodeId: string;
    clientX: number;
    clientY: number;
    initial: CanvasTransform;
    autoKey: boolean;
    at: number;
    interpolation: MotionInterpolation;
  } | null>(null);

  useEffect(() => { setAddError(null); }, [creationScope]);

  useEffect(() => {
    const next = scene?.nodes[0] ?? null;
    setSelectedNodeId(next?.id ?? null);
    setDraftTransforms({});
    // Auto-key is consent for the selected scene, not a sticky global input
    // mode that can silently author motion on another project or scene.
    setAutoKeyPosition(false);
    drag.current = null;
  }, [scene?.id, project.generation]);

  const selectedNode = useMemo(
    () => scene?.nodes.find((node) => node.id === selectedNodeId) ?? null,
    [scene, selectedNodeId],
  );

  const selectedMotionValue = selectedNode?.[motionProperty];
  useEffect(() => {
    // An unrelated canonical publication can clone the selected object without
    // changing this property. Preserve its unsent motion input in that case.
    if (selectedMotionValue !== undefined) setMotionValue(selectedMotionValue);
  }, [selectedNode?.id, selectedMotionValue, motionProperty]);

  useEffect(() => {
    setNativeStartX(selectedNode ? selectedNode.x - 100 : 0);
    setNativeStartY(selectedNode?.y ?? 0);
    setNativeEndFrame(30);
  }, [selectedNode?.id, project.generation]);

  if (!scene) {
    return (
      <div className="empty-workspace">
        <Move size={24} />
        <strong>Select a scene</strong>
        <span>Canvas edits are scoped to stable scene and object identities.</span>
      </div>
    );
  }

  const scenePositionLocked = project.locks.some(
    (lock) => lock.resource === "scene:" + scene.id && lock.kind === "position",
  );
  const sceneContentLocked = project.locks.some(
    (lock) => lock.resource === "scene:" + scene.id && lock.kind === "content",
  );
  const sceneDurationSeconds = seconds(scene.duration);
  const nativeFrameRate = profile
    ? Number(profile.frame_rate.num) / Number(profile.frame_rate.den) : Number.NaN;
  const nativeMoveTime = nativeEndFrame / nativeFrameRate;
  const nativeMoveAllowed = Boolean(profile && selectedNode
    && scene.renderer === "motion-canvas" && scene.beats.length === 0
    && !scenePositionLocked && !selectedNode.property_locks.includes("position")
    && selectedNode.parent_id === null && selectedNode.kind !== "group"
    && !scene.nodes.some((node) => node.parent_id === selectedNode.id)
    && selectedNode.keyframes.length === 0
    && Number.isFinite(nativeStartX) && Number.isFinite(nativeStartY)
    && (nativeStartX !== selectedNode.x || nativeStartY !== selectedNode.y)
    && Number.isInteger(nativeEndFrame) && nativeEndFrame >= 1
    && nativeEndFrame <= 36_000 && Number.isFinite(nativeMoveTime)
    && nativeMoveTime > 0 && nativeMoveTime < sceneDurationSeconds);
  const maxPlayhead = Math.max(0, sceneDurationSeconds - 0.001);
  const sceneStartSeconds = seconds(scene.start);
  const scenePlayhead = Math.max(0, Math.min(playhead - sceneStartSeconds, maxPlayhead));
  const seekSceneTime = (relativeSeconds: number) => {
    onSeek(sceneStartSeconds + Math.max(0, Math.min(relativeSeconds, maxPlayhead)));
  };

  const addNode = async (kind: "text" | "shape" | "group") => {
    if (addPending.current) return;
    addPending.current = true;
    setAddBusy(true);
    setAddError(null);
    const submittedScope = creationScope;
    const submittedInteraction = localInteractionVersion.current;
    const node = createNode(scene, kind);
    try {
      await commitRequired({ type: "add_canvas_node", scene_id: scene.id, node });
      // Canonical publication is independent of the local inspector. Even in
      // this same scene, a later selection or edit supersedes auto-selection.
      if (visibleCreationScope.current !== submittedScope
        || localInteractionVersion.current !== submittedInteraction) return;
      setSelectedNodeId(node.id);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setAddError(visibleCreationScope.current === submittedScope ? message
        : "Creating " + kind + " in “" + scene.name + "” needs attention: " + message);
    } finally {
      addPending.current = false;
      setAddBusy(false);
    }
  };

  const selectNode = (node: CanvasNode) => {
    localInteractionVersion.current += 1;
    setSelectedNodeId(node.id);
  };

  const objectTransform = (node: CanvasNode) => draftTransforms[node.id] ?? nodeTransform(node);
  const stageTransform = (node: CanvasNode) => draftTransforms[node.id] ?? previewTransform(node, scenePlayhead);

  const beginDrag = (event: React.PointerEvent<HTMLButtonElement>, node: CanvasNode) => {
    selectNode(node);
    if (scenePositionLocked || node.property_locks.includes("position")) return;
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = {
      pointerId: event.pointerId,
      nodeId: node.id,
      clientX: event.clientX,
      clientY: event.clientY,
      // Freeze the entire gesture mode/time so a slider seek or toggled
      // checkbox while dragging cannot split the transaction.
      initial: autoKeyPosition ? stageTransform(node) : objectTransform(node),
      autoKey: autoKeyPosition,
      at: scenePlayhead,
      interpolation: motionInterpolation,
    };
  };

  const moveDrag = (event: React.PointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const stage = event.currentTarget.closest(".canvas-stage") as HTMLElement | null;
    if (!stage) return;
    const width = Math.max(stage.clientWidth, 1);
    const height = Math.max(stage.clientHeight, 1);
    const delta = cameraPointerDelta(
      scene.camera,
      event.clientX - active.clientX,
      event.clientY - active.clientY,
      width,
      height,
    );
    if (!delta) return;
    const next = {
      ...active.initial,
      x: active.initial.x + delta.x,
      y: active.initial.y + delta.y,
    };
    setDraftTransforms((current) => ({ ...current, [active.nodeId]: next }));
  };

  const endDrag = (event: React.PointerEvent<HTMLButtonElement>) => {
    const active = drag.current;
    if (!active || active.pointerId !== event.pointerId) return;
    drag.current = null;
    const next = draftTransforms[active.nodeId];
    if (!next) return;
    void commit(active.autoKey ? {
      type: "set_canvas_position_keyframe",
      scene_id: scene.id,
      node_id: active.nodeId,
      at: rationalSeconds(active.at),
      x: next.x,
      y: next.y,
      interpolation: active.interpolation,
    } : {
      type: "transform_canvas_node",
      scene_id: scene.id,
      node_id: active.nodeId,
      transform: next,
    }).finally(() => {
      setDraftTransforms((current) => {
        const copy = { ...current };
        delete copy[active.nodeId];
        return copy;
      });
    });
  };

  const toggleLock = (property: NodeProperty) => {
    if (!selectedNode) return;
    void commit({
      type: "set_node_property_lock",
      scene_id: scene.id,
      node_id: selectedNode.id,
      property,
      locked: !selectedNode.property_locks.includes(property),
    });
  };

  const commitKeyframe = () => {
    if (!selectedNode) return;
    const at = scenePlayhead;
    void commit({
      type: "set_canvas_keyframe",
      scene_id: scene.id,
      node_id: selectedNode.id,
      keyframe: {
        at: rationalSeconds(at),
        property: motionProperty,
        value: motionValue,
        interpolation: motionInterpolation,
      },
    });
  };

  const removeKeyframe = (at: ReturnType<typeof rationalSeconds>, property: MotionProperty) => {
    if (!selectedNode) return;
    void commit({
      type: "remove_canvas_keyframe",
      scene_id: scene.id,
      node_id: selectedNode.id,
      at,
      property,
    });
  };

  return (
    <div className="canvas-workspace" onKeyDown={event => {
      if (event.code === "Space" && !event.ctrlKey && !event.metaKey && !event.altKey
        && event.target instanceof HTMLElement && event.target.closest("button, summary")) event.stopPropagation();
    }}>
      <header className="canvas-toolbar">
        <div>
          <strong>Canvas</strong>
          <span>{scene.name}</span>
        </div>
        <div className="canvas-toolbar-state">
          <label className="motion-playhead">
            <span className="mono">{scenePlayhead.toFixed(2)}s / {sceneDurationSeconds.toFixed(2)}s</span>
            <input
              aria-label="Motion playhead"
              title="Scene-relative seconds · linked to project timeline"
              type="range"
              min="0"
              max={Math.max(maxPlayhead, 0)}
              step="0.001"
              value={scenePlayhead}
              onChange={(event) => seekSceneTime(numberValue(event.target.value, 0))}
            />
          </label>
          <label className="canvas-auto-key" title="When enabled, dragging an object creates one atomic X/Y motion keyframe at the current scene-relative playhead instead of changing its base layout.">
            <input
              type="checkbox"
              aria-label="Auto-key position"
              checked={autoKeyPosition}
              disabled={scenePositionLocked}
              onChange={(event) => setAutoKeyPosition(event.target.checked)}
            />
            <span>Auto-key X/Y</span>
          </label>
          <span className="status-pill status-unknown">
            {autoKeyPosition ? "KEYED POSITION" : "BASE POSITION"}
          </span>
          <span className="mono">{scene.nodes.length} objects</span>
        </div>
      </header>

      <div className="canvas-body">
        <aside className="canvas-object-tree" aria-label="Canvas object tree">
          <div className="canvas-panel-heading">Objects</div>
          <div className="canvas-create-row" aria-label="Create canvas object">
            {(["text", "shape", "group"] as const).map((kind) => (
              <button
                type="button"
                className="canvas-create-button"
                key={kind}
                disabled={sceneContentLocked || scenePositionLocked || addBusy}
                onClick={() => void addNode(kind)}
              >
                <Plus size={10} />
                {kind}
              </button>
            ))}
          </div>
          {addError && <p className="production-error" role="alert">{addError}</p>}
          {scene.nodes.length === 0 ? (
            <div className="canvas-empty-note">This scene has no semantic canvas objects yet.</div>
          ) : (
            <div className="canvas-tree-list">
              {[...scene.nodes].sort((a, b) => b.z_index - a.z_index).map((node) => (
                <button
                  type="button"
                  key={node.id}
                  className={"canvas-tree-row" + (selectedNodeId === node.id ? " selected" : "")}
                  onClick={() => selectNode(node)}
                >
                  <span className="canvas-kind">{node.kind}</span>
                  <span>{node.name}</span>
                  {node.property_locks.length > 0 && <LockKeyhole size={12} aria-label="Object has property locks" />}
                </button>
              ))}
            </div>
          )}
          <div className="canvas-camera-summary">
            <Camera size={14} />
            <div>
              <strong>Camera</strong>
              <span>{scene.camera.zoom.toFixed(2)}× · safe {Math.round(scene.camera.safe_margin * 100)}%</span>
            </div>
          </div>
        </aside>

        <section className="canvas-stage-shell" aria-label="Semantic canvas">
          <div className="canvas-stage-meta">
            <span>{PROJECT_WIDTH} × {PROJECT_HEIGHT}</span>
            <span>Semantic camera · no renderer evidence</span>
          </div>
          <div className="canvas-stage">
            <div className="canvas-safe-area" style={{ inset: (scene.camera.safe_margin * 100) + "%" }}>
              <span>SAFE</span>
            </div>
            <div className="canvas-camera-layer" data-testid="canvas-camera-layer"
              style={{ transform: cameraLayerTransform(scene.camera) }}>
              {scene.nodes.map((node) => {
              const transform = stageTransform(node);
              const positionLocked = scenePositionLocked || node.property_locks.includes("position");
              return (
                <button
                  type="button"
                  key={node.id}
                  aria-label={"Canvas object " + node.name}
                  className={[
                    "canvas-object",
                    node.kind === "text" ? "text-object" : "",
                    selectedNodeId === node.id ? "selected" : "",
                    positionLocked ? "locked" : "",
                  ].join(" ")}
                  style={{
                    left: ((transform.x / PROJECT_WIDTH) * 100) + "%",
                    top: ((transform.y / PROJECT_HEIGHT) * 100) + "%",
                    width: ((transform.width / PROJECT_WIDTH) * 100) + "%",
                    height: ((transform.height / PROJECT_HEIGHT) * 100) + "%",
                    opacity: transform.opacity,
                    transform: "rotate(" + transform.rotation_deg + "deg)",
                    zIndex: node.z_index + 2,
                  }}
                  onPointerDown={(event) => beginDrag(event, node)}
                  onPointerMove={moveDrag}
                  onPointerUp={endDrag}
                  onPointerCancel={() => {
                    const aborted = drag.current;
                    drag.current = null;
                    if (aborted) setDraftTransforms((current) => {
                      const copy = { ...current };
                      delete copy[aborted.nodeId];
                      return copy;
                    });
                  }}
                  onClick={() => selectNode(node)}
                >
                  <span className="canvas-object-label">{node.text || node.name}</span>
                  {positionLocked && <LockKeyhole className="canvas-object-lock" size={12} />}
                </button>
              );
              })}
            </div>
            {scene.nodes.length === 0 && (
              <div className="canvas-stage-empty">
                <CircleDashed size={25} />
                <strong>No semantic objects</strong>
                <span>The renderer preview is intentionally not fabricated.</span>
              </div>
            )}
          </div>
        </section>

        <aside className="canvas-properties" aria-label="Canvas properties"
          onChangeCapture={() => { localInteractionVersion.current += 1; }}>
          <div className="canvas-panel-heading">Object</div>
          {!selectedNode ? (
            <div className="canvas-empty-note">Select an object to inspect its semantic properties.</div>
          ) : (
            <>
              <div className="canvas-selection-title">
                <div>
                  <strong>{selectedNode.name}</strong>
                  <span>{selectedNode.kind} · z{selectedNode.z_index}</span>
                </div>
                {selectedNode.property_locks.length > 0 && <span className="status-pill status-review">LOCKED FIELDS</span>}
              </div>

              <CanvasDraftInspector key={canvasDraftScope(project, scene.id, selectedNode.id)}
                project={project} scene={scene} node={selectedNode} commit={commitRequired}
                dragPreview={draftTransforms[selectedNode.id]}/>

              <section className="motion-editor" aria-label="Typed motion keyframes">
                <div className="motion-editor-heading">
                  <div>
                    <span className="field-label">Motion</span>
                    <strong>{selectedNode.keyframes.length} keyframe{selectedNode.keyframes.length === 1 ? "" : "s"}</strong>
                  </div>
                  <span className="mono">{scenePlayhead.toFixed(3)}s</span>
                </div>
                <div className="canvas-field-grid motion-fields">
                  <label>
                    <span>Property</span>
                    <select
                      aria-label="Motion property"
                      value={motionProperty}
                      onChange={(event) => setMotionProperty(event.target.value as MotionProperty)}
                    >
                      {MOTION_PROPERTIES.map((property) => <option value={property} key={property}>{property.replace("_deg", "")}</option>)}
                    </select>
                  </label>
                  <label>
                    <span>Value</span>
                    <input
                      aria-label="Motion keyframe value"
                      type="number"
                      step={motionProperty === "opacity" ? "0.05" : "1"}
                      min={motionProperty === "opacity" || motionProperty === "width" || motionProperty === "height" ? "0" : undefined}
                      max={motionProperty === "opacity" ? "1" : undefined}
                      value={motionValue}
                      onChange={(event) => setMotionValue(numberValue(event.target.value, motionValue))}
                    />
                  </label>
                  <label>
                    <span>Interpolation</span>
                    <select
                      aria-label="Motion interpolation"
                      value={motionInterpolation}
                      onChange={(event) => setMotionInterpolation(event.target.value as MotionInterpolation)}
                    >
                      <option value="linear">Linear</option>
                      <option value="ease_in_out">Ease in/out</option>
                      <option value="ease_out_cubic">Ease out cubic</option>
                      <option value="hold">Hold</option>
                    </select>
                  </label>
                </div>
                <button
                  className="button full"
                  type="button"
                  disabled={scenePositionLocked || selectedNode.property_locks.includes(propertyLock(motionProperty))}
                  onClick={commitKeyframe}
                >
                  Add or replace keyframe at playhead
                </button>
                <div className="motion-keyframe-list">
                  {selectedNode.keyframes.length === 0 ? (
                    <span className="canvas-empty-note">No authored motion on this object.</span>
                  ) : [...selectedNode.keyframes]
                    .sort((left, right) => seconds(left.at) - seconds(right.at) || left.property.localeCompare(right.property))
                    .map((keyframe) => (
                      <div className="motion-keyframe-row" key={keyframe.at.num + "/" + keyframe.at.den + ":" + keyframe.property}>
                        <button
                          type="button"
                          className="motion-keyframe-jump"
                          onClick={() => seekSceneTime(seconds(keyframe.at))}
                          title="Move playhead to keyframe"
                        >
                          <span className="mono">{seconds(keyframe.at).toFixed(3)}s</span>
                          <strong>{keyframe.property.replace("_deg", "")}</strong>
                          <span>{keyframe.value} · {keyframe.interpolation.replaceAll("_", " ")}</span>
                        </button>
                        <button
                          type="button"
                          className="icon-button"
                          aria-label={"Remove " + keyframe.property + " keyframe at " + seconds(keyframe.at).toFixed(3) + " seconds"}
                          disabled={scenePositionLocked || selectedNode.property_locks.includes(propertyLock(keyframe.property))}
                          onClick={() => removeKeyframe(keyframe.at, keyframe.property)}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                </div>
                <p className="inspector-note">
                  Editorial preview. Native Film preserves admitted synchronized entrances; other motion is rejected rather than approximated.
                </p>
              </section>

              <section className="native-motion-authoring" aria-label="Native linear position motion">
                <div className="motion-editor-heading">
                  <div>
                    <span className="field-label">Native Film · Position</span>
                    <strong>Frame-exact X/Y move</strong>
                  </div>
                  <span className="mono">{profile?.name ?? "No profile"}</span>
                </div>
                <p className="inspector-note">
                  One frame-aligned movement from a starting position to this object's
                  unchanged base X/Y. Four paired keys in one revision; the selected native easing is preserved without sampling or rounding.
                </p>
                <div className="canvas-field-grid motion-fields">
                  <label>
                    <span>Start X</span>
                    <input type="number" step="1" aria-label="Native move start X"
                      value={nativeStartX}
                      onChange={(event) =>
                        setNativeStartX(numberValue(event.target.value, nativeStartX))} />
                  </label>
                  <label>
                    <span>Start Y</span>
                    <input type="number" step="1" aria-label="Native move start Y"
                      value={nativeStartY}
                      onChange={(event) =>
                        setNativeStartY(numberValue(event.target.value, nativeStartY))} />
                  </label>
                  <label>
                    <span>End frame</span>
                    <input type="number" min="1" max="36000" step="1"
                      aria-label="Native move end frame"
                      value={nativeEndFrame}
                      onChange={(event) =>
                        setNativeEndFrame(numberValue(event.target.value, nativeEndFrame))} />
                  </label>
                  <label>
                    <span>Native curve</span>
                    <select aria-label="Native move easing" value={nativeMotionCurve}
                      onChange={event=>setNativeMotionCurve(event.target.value as "linear" | "ease_out_cubic")}>
                      <option value="linear">Linear</option>
                      <option value="ease_out_cubic">Cubic ease-out · exact</option>
                    </select>
                  </label>
                </div>
                <div className="inspector-note">
                  {profile ? "Output " + profile.name + " · " + profile.frame_rate.num + "/"
                    + profile.frame_rate.den + " fps · endpoint " +
                    (Number.isFinite(nativeMoveTime) ? nativeMoveTime.toFixed(3) + "s" : "unsupported")
                    : "Select a saved delivery profile with a valid frame rate."}
                </div>
                <button className="button full" type="button"
                  aria-label={nativeMotionCurve==="linear"?"Create native linear move":"Create native ease-out cubic move"}
                  disabled={!nativeMoveAllowed}
                  onClick={() => {
                    if (!selectedNode || !profile || !nativeMoveAllowed) return;
                    void commit({
                      type: nativeMotionCurve==="linear"?"set_canvas_linear_position_motion":"set_canvas_out_cubic_position_motion",
                      scene_id: scene.id,
                      node_id: selectedNode.id,
                      deliverable_id: profile.id,
                      start_x: nativeStartX,
                      start_y: nativeStartY,
                      end_frame: nativeEndFrame,
                    });
                  }}>
                  {nativeMotionCurve==="linear"?"Create native linear move":"Create native ease-out cubic move"}
                </button>
                <p className="inspector-note">
                  Existing motion keys, scene beats, nested objects and locked positions
                  are not overwritten. Film preflight still verifies native framing.
                </p>
              </section>

              <div className="property-locks">
                <span className="field-label">Property locks</span>
                {(["position", "size", "rotation", "opacity", "text", "style", "parent", "order"] as NodeProperty[]).map((property) => {
                  const locked = selectedNode.property_locks.includes(property);
                  return (
                    <button type="button" className={"lock-toggle" + (locked ? " active" : "")} key={property} onClick={() => toggleLock(property)}>
                      {locked ? <LockKeyhole size={12} /> : <Unlock size={12} />}
                      <span>{property}</span>
                    </button>
                  );
                })}
              </div>

              <div className="camera-controls">
                <span className="field-label">Camera</span>
                {([
                  ["Center X", "center_x"],
                  ["Center Y", "center_y"],
                  ["Rotation", "rotation_deg"],
                ] as const).map(([label, property]) => (
                  <label key={property}>
                    <span>{label}</span>
                    <input type="number" step="1"
                      aria-label={"Camera " + label}
                      value={scene.camera[property]}
                      onChange={(event) => {
                        const value = numberValue(event.target.value, scene.camera[property]);
                        void commit({ type: "set_camera", scene_id: scene.id,
                          camera: { ...scene.camera, [property]: value } });
                      }} />
                  </label>
                ))}
                <label>
                  <span>Zoom</span>
                  <input
                    aria-label="Camera zoom"
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.05"
                    value={scene.camera.zoom}
                    onChange={(event) => {
                      const zoom = numberValue(event.target.value, scene.camera.zoom);
                      void commit({ type: "set_camera", scene_id: scene.id, camera: { ...scene.camera, zoom } });
                    }}
                  />
                </label>
                <label>
                  <span>Safe area</span>
                  <input
                    aria-label="Camera safe area"
                    type="number"
                    min="0"
                    max="0.49"
                    step="0.01"
                    value={scene.camera.safe_margin}
                    onChange={(event) => {
                      const safe_margin = numberValue(event.target.value, scene.camera.safe_margin);
                      void commit({ type: "set_camera", scene_id: scene.id, camera: { ...scene.camera, safe_margin } });
                    }}
                  />
                </label>
              </div>

              <CanvasStructureEditor scene={scene} node={selectedNode} commit={commit} />
            </>
          )}
          <TypographySafetyReview
            project={project} scene={scene} profile={profile}
            sceneTime={scenePlayhead} onSelectNode={selectNode}
          />
          <VisualLanguageEditor project={project} commit={commit} />
        </aside>
      </div>
    </div>
  );
}
