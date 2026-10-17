import { useEffect, useMemo, useRef, useState } from "react";
import { Check, Eye, Plus, Trash2 } from "lucide-react";
import { previewCreativePatch } from "./api";
import CompositionStudy from "./CompositionStudy";
import { previewCreativePatchUndo } from "./creativeUndo";
import { nativeRepairDraftIsCurrent } from "./nativeReviewRepair";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import {
  continueDraftManually, createScopedDraft, decodeScopedDraft, DRAFT_STORAGE_PREFIX, draftEditorDirty,
  draftPatch, encodeScopedDraft, keepCurrentDraftValue, queueDraftEdit, rebaseScopedDraft,
  removeDraftEdit, retainUnqueuedInputs, reviewedChangeWasCommitted, scopedDraftConflicts, scopedDraftHasWork, scopedDraftScope,
  scopedReviewKey, selectDraftNode, unqueuedInputCount,
} from "./scopedChangeDraft";
import type { ReviewedScopedChange, ScopedChangeDraft } from "./scopedChangeDraft";
import type { CanvasNode, Change, DeliverableProfile, Project, Scene } from "./types";
import { seconds } from "./types";

type Preview = Awaited<ReturnType<typeof previewCreativePatch>>;
type Review = ReviewedScopedChange & { preview: Preview };
interface Props {
  project: Project;
  scene: Scene | null;
  commit: (change: Change) => Promise<void>;
  busy: boolean;
  playhead: number;
  onSeek: (time: number) => void;
  displayProfile: DeliverableProfile | null;
  repairDraft: NativeReviewRepairDraft | null;
  onDismissRepair: () => void;
}
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export default function CreativePatchWorkbench(props: Props) {
  // A project, restore generation, branch or scene never inherits another
  // scope's inputs or in-flight preview. Its own session draft can be restored.
  return <ScopedEditor key={scopedDraftScope(props.project, props.scene)} {...props}/>;
}

function ScopedEditor({ project, scene, commit, busy, playhead, onSeek, displayProfile, repairDraft, onDismissRepair }: Props) {
  const scope = scopedDraftScope(project, scene);
  const storageKey = DRAFT_STORAGE_PREFIX + scope;
  const [recovery] = useState(() => {
    try {
      const raw = window.sessionStorage.getItem(storageKey);
      return { draft: raw ? decodeScopedDraft(raw, scope) : createScopedDraft(project, scene), blocked: false, error: null as string | null };
    } catch {
      return { draft: createScopedDraft(project, scene), blocked: true,
        error: "The saved draft could not be restored. Its recovery copy has been left untouched; your project is unchanged." };
    }
  });
  const [draft, setDraft] = useState(recovery.draft);
  const [backupBlocked, setBackupBlocked] = useState(recovery.blocked);
  const [backupError, setBackupError] = useState(recovery.error);
  const [review, setReview] = useState<Review | null>(null);
  const [undoTarget, setUndoTarget] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const epoch = useRef(0);
  const inFlight = useRef(false);
  const submitted = useRef<Review | null>(null);
  const nativeSource = draft.nativeSource;
  const sourceStale = nativeSource !== null && !nativeRepairDraftIsCurrent(project, scene, nativeSource, displayProfile?.id ?? null);
  const draftStale = draft.baseRevision !== project.revision || sourceStale;
  const key = scopedReviewKey(project, scene, draft, undoTarget) + JSON.stringify(displayProfile?.id ?? null);
  const latest = useRef(key);
  latest.current = key;
  const activeReview = review?.key === key && (undoTarget !== null || !draftStale) ? review : null;
  const preview = activeReview?.preview ?? null;
  const node = scene?.nodes.find(item => item.id === draft.editor.nodeId);
  const lockedUi = busy || submitting;
  const conflicts = scopedDraftConflicts(draft, scene);

  const invalidate = () => {
    epoch.current += 1;
    // A later editing decision must not be cleared by an unconfirmed earlier
    // submission if the project is refreshed after its callback failed.
    submitted.current = null;
    setReview(null);
    setPending(false);
    setError(null);
  };
  const update = (next: ScopedChangeDraft) => {
    invalidate();
    setUndoTarget(null);
    setNotice(null);
    setDraft(next);
  };
  useEffect(() => {
    if (backupBlocked) return;
    try {
      if (scopedDraftHasWork(draft)) window.sessionStorage.setItem(storageKey, encodeScopedDraft(draft));
      else window.sessionStorage.removeItem(storageKey);
      setBackupError(null);
    } catch {
      setBackupError("This tab could not keep a recovery copy. Your draft is still open; keep this view open until you commit or discard it.");
    }
  }, [draft, storageKey, backupBlocked]);
  useEffect(() => () => { epoch.current += 1; }, []);
  useEffect(() => {
    epoch.current += 1;
    setReview(null);
    setPending(false);
    const receipt = submitted.current;
    if (receipt && reviewedChangeWasCommitted(project, receipt)) {
      const next = receipt.change.type === "undo_creative_patch" && scopedDraftHasWork(draft)
        ? draft : retainUnqueuedInputs(draft, project, scene);
      const keepDraft = scopedDraftHasWork(next);
      setDraft(next);
      if (!keepDraft) onDismissRepair();
      setUndoTarget(null);
      setNotice(keepDraft ? "The reviewed change is stored. Your other uncommitted inputs are still available."
        : "The reviewed change is stored in this project revision.");
    } else if (!scopedDraftHasWork(draft)) {
      setDraft(createScopedDraft(project, scene));
    }
    submitted.current = null;
    // A new revision invalidates a preview, never an operator's draft. Commit
    // callbacks may resolve after an error; only persisted matching state clears it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.revision, project.generation, project.id, project.active_branch, displayProfile?.id]);

  const incomingRepair = repairDraft && JSON.stringify(repairDraft) !== JSON.stringify(nativeSource) &&
    nativeRepairDraftIsCurrent(project, scene, repairDraft, displayProfile?.id ?? null) ? repairDraft : null;
  const beginRepair = (source: NativeReviewRepairDraft) => {
    let next = selectDraftNode(createScopedDraft(project, scene), scene?.nodes.find(item => item.id === source.targetNodeId));
    next = { ...next, baseRevision: source.revision, rationale: source.rationale, nativeSource: structuredClone(source),
      editor: { ...next.editor, kind: source.editKind } };
    update(next);
  };
  useEffect(() => {
    if (incomingRepair && !scopedDraftHasWork(draft)) beginRepair(incomingRepair);
    // An explicit handoff initializes an empty draft. A different existing draft
    // requires the visible replace/keep choice below and survives ordinary refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incomingRepair && JSON.stringify(incomingRepair)]);

  const background = project.visual_language.palette.find(token => ["surface", "background"].includes(token.name.toLowerCase()))?.value ?? "#0F1216";
  const time = scene ? Math.max(0, Math.min(seconds(scene.duration), playhead - seconds(scene.start))) : 0;
  const resultNodes = useMemo(() => {
    if (!scene || !preview) return scene?.nodes ?? [];
    const updated = new Map(preview.after.map(item => [item.id, item]));
    return scene.nodes.map(item => updated.get(item.id) ?? item);
  }, [scene, preview]);
  const sourceUnchanged = nativeSource !== null && !draftEditorDirty(draft);
  const queue = () => {
    if (!node || draftStale || sourceUnchanged || lockedUi || (nativeSource && node.id !== nativeSource.targetNodeId)) return;
    try { update(queueDraftEdit(draft)); }
    catch (reason) { setError(message(reason)); }
  };
  const selectTarget = (nodeId: string) => {
    try { update(selectDraftNode(draft, scene?.nodes.find(item => item.id === nodeId))); }
    catch (reason) { setError(message(reason)); }
  };
  const inspect = async () => {
    if (!scene || draftStale || !draft.edits.length || !draft.rationale.trim() || lockedUi || pending) return;
    invalidate();
    setUndoTarget(null);
    setPending(true);
    const requestEpoch = ++epoch.current;
    const requestKey = scopedReviewKey(project, scene, draft, null) + JSON.stringify(displayProfile?.id ?? null);
    const change: Change = { type: "apply_creative_patch", patch: draftPatch(draft, scene.id) };
    try {
      const result = await previewCreativePatch(project, structuredClone(change.patch));
      if (requestEpoch !== epoch.current || requestKey !== latest.current) return;
      if (result.source_revision !== project.revision || result.scene_id !== scene.id) throw new Error("The preview belongs to a different source. Inspect the proposal again.");
      setReview({ key: requestKey, projectId: project.id, generation: project.generation, branchId: project.active_branch,
        revision: project.revision, change: structuredClone(change), preview: result });
    } catch (reason) {
      if (requestEpoch === epoch.current && requestKey === latest.current) setError(message(reason));
    } finally {
      if (requestEpoch === epoch.current && requestKey === latest.current) setPending(false);
    }
  };
  const reset = () => {
    update(createScopedDraft(project, scene));
    setBackupBlocked(false);
    onDismissRepair();
  };
  const rebase = () => {
    try {
      update(rebaseScopedDraft(draft, project, scene));
      setNotice("Draft kept on the current revision. Preview the proposal again before applying it.");
    } catch (reason) { setError(message(reason)); }
  };
  const records = (project.production_design?.patches ?? []).filter(record => record.scene_id === scene?.id);
  const inspectUndo = (id: string) => {
    if (lockedUi || pending) return;
    invalidate();
    setUndoTarget(id);
    try {
      const result = previewCreativePatchUndo(project, id);
      const requestKey = scopedReviewKey(project, scene, draft, id) + JSON.stringify(displayProfile?.id ?? null);
      setReview({ key: requestKey, projectId: project.id, generation: project.generation, branchId: project.active_branch,
        revision: project.revision, change: { type: "undo_creative_patch", patch_id: id }, preview: result });
    } catch (reason) { setError(message(reason)); }
  };
  const applyReviewed = async () => {
    if (!activeReview || lockedUi || pending || inFlight.current || activeReview.key !== latest.current) return;
    const exactReview = structuredClone(activeReview);
    inFlight.current = true;
    submitted.current = exactReview;
    setSubmitting(true);
    setError(null);
    try { await commit(structuredClone(exactReview.change)); }
    catch (reason) { setError(message(reason)); }
    finally { inFlight.current = false; setSubmitting(false); }
  };
  const editSummary = (edit: { node_id: string; kind: string }) => `${scene?.nodes.find(item => item.id === edit.node_id)?.name ?? edit.node_id} / ${edit.kind}`;

  return <div className="production-patch">
    <div className="production-section-title"><Eye size={18}/><div><h2>Scoped changes</h2><p>Stage a bounded proposal, inspect the difference and commit once.</p></div></div>
    {incomingRepair && scopedDraftHasWork(draft) && <div className="production-notice" role="status">
      <strong>A new native-frame observation is ready.</strong><span>Your existing proposal is still here.</span>
      <div className="production-button-row"><button className="secondary-button" disabled={lockedUi} onClick={() => beginRepair(incomingRepair)}>Replace draft with frame repair</button>
        <button className="secondary-button" disabled={lockedUi} onClick={onDismissRepair}>Keep this draft</button></div>
    </div>}
    {nativeSource && <div className="production-notice" role="status"><strong>Native frame {nativeSource.frameIndex} · source r{nativeSource.revision} · uncommitted repair</strong>
      <span>PNG SHA-256 {nativeSource.frameSha256.slice(0, 16)}… (full digest retained in the rationale). This handoff is a manual observation, not source authenticity or a quality PASS. The target object is fixed for this draft; discard it to select another. Enter a replacement, preview the scoped diff, then explicitly commit.</span></div>}
    <div className="production-notice"><strong>Revision {draft.baseRevision} · one scene · {draft.edits.length} proposed edit{draft.edits.length === 1 ? "" : "s"}</strong>
      <span>Scene and property locks are checked in preview and again by the revisioned service at commit. This view is an editorial comparison, not renderer verification.</span></div>
    <p className="production-help" role="status">Uncommitted inputs are kept in this tab for this project, branch and scene. Restoring a draft always requires a new preview.</p>
    {backupError && <div className="production-error" role="alert">{backupError}{backupBlocked && <button className="secondary-button" disabled={lockedUi} onClick={reset}>Discard saved copy and current draft</button>}</div>}
    {notice && <p className="production-help" role="status">{notice}</p>}
    {draftStale && <div className="production-error" role="alert">{sourceStale
      ? "The native frame repair no longer matches the selected output profile, project revision or scene. Restore the original unchanged profile, re-inspect current native frames, or continue these inputs as a manual proposal."
      : `The project changed to revision ${project.revision}. Your proposal is kept and cannot commit against its old base.`}
      <div className="production-button-row">{!sourceStale && <button className="secondary-button" disabled={lockedUi || conflicts.length > 0 || !scene} onClick={rebase}>Review on current revision</button>}
        {sourceStale && <button className="secondary-button" disabled={lockedUi} onClick={() => {
          update(continueDraftManually(draft));
          setNotice("Your inputs are kept. The earlier frame is context only; resolve any conflicts and prepare a new editorial preview.");
        }}>Continue as a manual proposal</button>}
        <button className="secondary-button" disabled={lockedUi} onClick={reset}>Start a new scoped draft</button></div></div>}
    {draftStale && conflicts.length > 0 && <section aria-label="Proposal conflicts" className="production-patch-queue"><h3>Changed since this draft</h3>
      <p className="production-help">Keep the current value to remove only that proposed property. Other edits remain available for review.</p>
      {conflicts.map(conflict => <article key={conflict.nodeId + conflict.kind}>
        <strong>{conflict.label} / {conflict.kind} · {conflict.reason === "missing" ? "object unavailable" : conflict.reason === "structure" ? "object structure changed" : "value changed"}</strong>
        <details className="production-diff-details"><summary>Compare original, current and proposed values</summary><pre>{JSON.stringify({ original: conflict.before, current: conflict.current, proposed: conflict.proposed }, null, 2)}</pre></details>
        <button className="secondary-button" disabled={lockedUi} onClick={() => update(keepCurrentDraftValue(draft, conflict, scene))}>Keep current {conflict.kind} for {conflict.label}</button>
      </article>)}</section>}
    <div className="production-plan-grid">
      <section className="production-patch-editor" aria-label="Scoped proposal editor">
        <label className="production-field"><span>Target object</span><select aria-label="Patch target object" value={draft.editor.nodeId} disabled={lockedUi || draftStale || nativeSource !== null}
          onChange={event => selectTarget(event.target.value)}>
          <option value="" disabled>Select an object</option>{!node && draft.editor.nodeId && <option value={draft.editor.nodeId}>Unavailable draft object</option>}
          {scene?.nodes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label className="production-field"><span>Edit</span><select aria-label="Patch edit kind" value={draft.editor.kind} disabled={lockedUi}
          onChange={event => update({ ...draft, editor: { ...draft.editor, kind: event.target.value as "text" | "transform" } })}><option value="text">Text only</option><option value="transform">Transform only</option></select></label>
        {draft.editor.kind === "text" ? <label className="production-field"><span>Replacement text</span><textarea aria-label="Patch replacement text" rows={4} value={draft.editor.text} disabled={lockedUi}
          onChange={event => update({ ...draft, editor: { ...draft.editor, text: event.target.value } })}/></label>
          : <div className="production-transform-grid">{(["x", "y", "width", "height", "rotation_deg", "opacity"] as const).map(field => <label className="production-field" key={field}><span>{field.replace("_deg", " (deg)")}</span>
            <input aria-label={`Patch ${field}`} type="number" step={field === "opacity" ? .01 : 1} value={draft.editor.transform[field]} disabled={lockedUi}
              onChange={event => update({ ...draft, editor: { ...draft.editor, transform: { ...draft.editor.transform, [field]: Number(event.target.value) } } })}/></label>)}</div>}
        <button className="secondary-button" disabled={!node || draftStale || sourceUnchanged || lockedUi || (nativeSource !== null && node.id !== nativeSource.targetNodeId)} onClick={queue}><Plus size={14}/> Add to proposal</button>
        <label className="production-field"><span>Visible reason for this change</span><textarea aria-label="Patch rationale" rows={3} placeholder="What is wrong, where is it visible, and why does this edit help?" value={draft.rationale} disabled={lockedUi}
          onChange={event => update({ ...draft, rationale: event.target.value })}/></label>
      </section>
      <section className="production-patch-queue" aria-label="Proposed edits"><h3>Proposed edits</h3>
        {unqueuedInputCount(draft) > 0 && <p className="production-help" role="status">{unqueuedInputCount(draft)} input{unqueuedInputCount(draft) === 1 ? " is" : "s are"} kept outside this proposal. Select the object and add each edit you want to include; applying the queue keeps the remaining inputs.</p>}
        {draft.edits.length === 0 ? <p className="production-help">No edits queued. Selecting or typing does not change the project.</p>
          : draft.edits.map((edit, index) => <div key={edit.node_id + edit.kind}><span>{editSummary(edit)}</span><button className="icon-button" disabled={lockedUi} aria-label={"Remove proposed edit " + (index + 1)}
            onClick={() => update(removeDraftEdit(draft, edit.node_id, edit.kind))}><Trash2 size={14}/></button></div>)}
        <button className="primary-button" disabled={!scene || !draft.edits.length || !draft.rationale.trim() || draftStale || pending || lockedUi} onClick={inspect}><Eye size={14}/>{pending ? "Inspecting proposal…" : "Preview scoped changes"}</button>
        {error && <p className="production-error" role="alert">{error}</p>}
        {preview && <div className="production-patch-receipt"><strong>Editorial diff prepared</strong><p>{preview.before.length} object{preview.before.length === 1 ? "" : "s"} · full selected scene invalidated: {seconds(preview.dirty_start).toFixed(3)}–{seconds(preview.dirty_end).toFixed(3)} s.</p>
          <p>Prepared for revision {activeReview!.revision}. Changing the proposal requires a new preview.</p></div>}
      </section>
    </div>
    {scene && <><div className="production-compare-grid"><figure><figcaption>BEFORE · current project</figcaption><CompositionStudy nodes={scene.nodes} time={time} background={background} label="Before scoped changes"/></figure>
      <figure><figcaption>AFTER · {undoTarget ? "uncommitted undo" : "uncommitted proposal"}</figcaption><CompositionStudy nodes={resultNodes} time={time} background={background} label="After scoped changes"/></figure></div>
      <div className="production-transport"><span className="mono">{time.toFixed(3)} s</span><input aria-label="Comparison playhead" type="range" min={0} max={seconds(scene.duration)} step={1 / 30} value={time}
        onChange={event => onSeek(seconds(scene.start) + Number(event.target.value))}/></div>
      <p className="production-help">Both sides share the application playhead. This bounded study draws flat text, shapes and circles; parenting, imported media and advanced paint effects must be reviewed in native frames.</p></>}
    <div className="production-button-row"><button className="primary-button" disabled={!activeReview || lockedUi || pending} onClick={applyReviewed}><Check size={14}/> {undoTarget ? "Apply undo as new revision" : "Apply as one revision"}</button>
      {undoTarget && <button className="secondary-button" disabled={lockedUi} onClick={() => { invalidate(); setUndoTarget(null); }}>Back to proposal</button>}
      <button className="secondary-button" disabled={lockedUi} onClick={reset}>Discard proposal</button></div>
    <section className="production-undo-history" aria-label="Reversible scoped changes"><h3>Reversible scoped changes</h3>
      <p className="production-help">Undo and redo create new revisions and preserve unrelated edits. Inspecting an undo keeps your uncommitted proposal. The latest 64 changes are available within a four-megabyte window.</p>
      {records.length === 0 ? <p className="production-help">No applied scoped changes in this scene yet.</p> : [...records].reverse().map(record => {
        const reverted = records.some(other => other.reverts === record.id);
        return <article key={record.id}><div><strong>{record.rationale}</strong><span>Base r{record.source_revision} · {record.before.length} object{record.before.length === 1 ? "" : "s"}{reverted ? " · inverse recorded" : ""}</span></div>
          <button className="secondary-button" disabled={lockedUi || pending || reverted} onClick={() => inspectUndo(record.id)}>{record.reverts ? "Preview redo" : "Preview undo"}</button></article>;
      })}
    </section>
    {preview && <details className="production-diff-details"><summary>Exact changed object values</summary><pre>{JSON.stringify(preview.after.map((after, index) => ({ object: after.name, before: preview.before[index] as CanvasNode, after })), null, 2)}</pre></details>}
  </div>;
}
