import { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import type { Change, Project } from "./types";
import { sameValue } from "./creativeProduction";
import { sameProjectRevision } from "./projectMutation";
import { bootstrap, projectHistory } from "./api";
import {
  acknowledgeWorkbenchDraft, createWorkbenchDraft, decodeWorkbenchCopy, editWorkbenchDraft, encodeWorkbenchFile,
  inspectWorkbenchOutcome, prepareWorkbenchSubmission, workbenchConflict, workbenchCopyKey, workbenchHasWork,
  workbenchLabel, workbenchOutcomeMatches, workbenchPointerKey, workbenchPreparationMatches, workbenchScope,
  workbenchUnconfirmed, workbenchSavedInput, WORKBENCH_RECOVERY_PREFIX, readWorkbenchCopies, reconcileWorkbenchDraft,
  releaseWorkbenchOutcome, resolveWorkbenchDraft, retainWorkbenchFailure, retainReviewedWorkbenchIntent, writeWorkbenchCopy,
} from "./creativeWorkbenchDraft";
import type { WorkbenchCustody, WorkbenchDraft, WorkbenchInput, WorkbenchOperation, WorkbenchOutcomeReview, WorkbenchRecoveryEntry, WorkbenchTarget, MeasuredByteProof } from "./creativeWorkbenchDraft";
type WorkbenchCommit = (change: Change) => Promise<Project>;
const workbenchHasInput = (draft: WorkbenchDraft) => draft.intent;

type WorkbenchSession = { draft: WorkbenchDraft; custody: WorkbenchCustody; inFlight: boolean; preparing: boolean;
  error: string | null; backupError: string | null; recoveryProblem: string | null; notice: string | null; copies: WorkbenchRecoveryEntry[]; limited: boolean; notify?: () => void };
// Author buffers survive tab/workspace navigation. They never own Project state
// and never replay a stored mutation. App's existing coordinator is the writer.
const workbenchSessions = new Map<string, WorkbenchSession>();
const workbenchOwner = (scope: string) => JSON.stringify((JSON.parse(scope) as unknown[]).slice(0, 3));
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function notifyWorkbench(session: WorkbenchSession) {
  for (const value of workbenchSessions.values()) if (workbenchOwner(value.draft.scope) === workbenchOwner(session.draft.scope)) value.notify?.();
}
function workbenchBusy(project: Project) {
  const owner = JSON.stringify([project.id, project.generation, project.active_branch]);
  return [...workbenchSessions.values()].some(value => workbenchOwner(value.draft.scope) === owner && (value.inFlight || value.preparing));
}
function refreshWorkbenchCopies(session: WorkbenchSession) {
  try { const result = readWorkbenchCopies(window.localStorage, session.draft.scope); session.copies = result.entries; session.limited = result.limited; }
  catch (reason) { session.backupError = message(reason); }
}
function getWorkbenchSession(project: Project, target: WorkbenchTarget): WorkbenchSession {
  const scope = workbenchScope(project, target), existing = workbenchSessions.get(scope);
  if (existing) return existing;
  let pointer: string | null = null, recovered: WorkbenchDraft | null = null, backupError: string | null = null;
  try {
    pointer = window.sessionStorage.getItem(workbenchPointerKey(scope));
    // A cloned/new tab starts an independent buffer. Recovery of that tab's own
    // pointer is automatic only on reload; all copies remain explicitly visible.
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      const raw = window.localStorage.getItem(workbenchCopyKey(scope, pointer));
      if (raw === null) throw new Error("The Workbench pointer refers to a missing copy. Earlier author bytes were not replaced.");
      recovered = decodeWorkbenchCopy(raw, scope, pointer).draft;
    }
  } catch (reason) { backupError = message(reason); }
  const session: WorkbenchSession = {
    draft: recovered ? reconcileWorkbenchDraft(recovered, project) : createWorkbenchDraft(project, target),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    inFlight: false, preparing: false, error: null, backupError, recoveryProblem: backupError,
    notice: recovered ? "Recovered this tab's Workbench input. No saved operation was repeated." : null, copies: [], limited: false,
  };
  workbenchSessions.set(scope, session); refreshWorkbenchCopies(session); return session;
}
function forkWorkbenchWriter(session: WorkbenchSession) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function backupWorkbench(session: WorkbenchSession): boolean {
  try {
    // A failed own-copy recovery is not erased by the first new keystroke.
    // Keep the original pointer/bytes until an explicit recovery action.
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Retry reading that copy or explicitly keep the current input in a new copy.");
    if (session.custody.expectedRaw !== null) {
      const old = decodeWorkbenchCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!workbenchHasWork(old) && !sameValue(old, session.draft)) forkWorkbenchWriter(session);
    }
    writeWorkbenchCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.backupError = null; refreshWorkbenchCopies(session); return session.backupError === null;
  } catch (reason) {
    session.backupError = message(reason) + " Your current input remains in this editor; saving requires a verified copy and pointer.";
    refreshWorkbenchCopies(session); return false;
  }
}
function replaceWorkbench(session: WorkbenchSession, draft: WorkbenchDraft, persist = true) {
  session.draft = draft;
  const saved = persist ? backupWorkbench(session) : true;
  notifyWorkbench(session); return saved;
}
export function useCreativeWorkbenchDraft(project: Project, target: WorkbenchTarget, commit: WorkbenchCommit, externalBusy: boolean) {
  const [session] = useState(() => getWorkbenchSession(project, target));
  const [, redraw] = useState(0), [review, setReview] = useState<WorkbenchOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0);
  current.current = project;
  const busy = externalBusy || workbenchBusy(project), conflict = workbenchConflict(session.draft, project);
  useEffect(() => {
    mounted.current = true; const update = () => redraw(value => value + 1); session.notify = update;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(WORKBENCH_RECOVERY_PREFIX)) { refreshWorkbenchCopies(session); update(); } };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  useEffect(() => {
    const next = reconcileWorkbenchDraft(session.draft, project);
    if (next !== session.draft) replaceWorkbench(session, next);
    setReview(value => workbenchOutcomeMatches(session.draft, project, value) ? value : null);
  }, [project, session]);
  function edit(input: WorkbenchInput) {
    session.error = null; session.notice = null; setReview(null);
    replaceWorkbench(session, editWorkbenchDraft(session.draft, input));
  }
  async function send(operation: WorkbenchOperation = "save", proof: MeasuredByteProof | null = null) {
    if (externalBusy || workbenchBusy(current.current) || session.backupError || workbenchUnconfirmed(session.draft)) return;
    const source = structuredClone(session.draft), sourceProject = structuredClone(current.current), started = epoch.current, id = crypto.randomUUID();
    session.preparing = true; session.error = null; session.notice = null; notifyWorkbench(session);
    let prepared: Awaited<ReturnType<typeof prepareWorkbenchSubmission>>;
    try {
      prepared = await prepareWorkbenchSubmission(source, sourceProject, id, operation, proof);
      if (!mounted.current || started !== epoch.current || !workbenchPreparationMatches(source, sourceProject, session.draft, current.current)) {
        session.notice = "The Workbench input or selected project changed during preparation. No change was sent; review the current input and save explicitly.";
        return;
      }
      if (!replaceWorkbench(session, prepared.draft)) {
        session.draft = retainWorkbenchFailure(session.draft, id, "Local recovery was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: verify this Workbench input's recovery before saving."; return;
      }
    } catch (reason) { session.error = message(reason); return; }
    finally { session.preparing = false; notifyWorkbench(session); }
    session.inFlight = true; notifyWorkbench(session);
    try {
      const receipt = await commit(prepared.change);
      if (session.draft.pending?.id !== id) return;
      const next = acknowledgeWorkbenchDraft(session.draft, id, receipt);
      // Keep the armed UNKNOWN copy intact even when the post-ACK checkpoint
      // cannot be retained. A later reload remains conservative.
      forkWorkbenchWriter(session); session.error = null;
      session.notice = "Saved " + workbenchLabel(target) + " at r" + receipt.revision + "."
        + (workbenchHasInput(next) ? " Your later input remains editable." : "");
      replaceWorkbench(session, next);
    } catch (reason) { session.error = message(reason); replaceWorkbench(session, retainWorkbenchFailure(session.draft, id, message(reason))); }
    finally { session.inFlight = false; notifyWorkbench(session); }
  }
  function choose(choice: "saved" | "draft") {
    if (busy || workbenchUnconfirmed(session.draft)) return;
    try {
      if (!backupWorkbench(session)) return;
      const next = resolveWorkbenchDraft(session.draft, current.current, choice);
      // An explicit buffer/base decision supersedes any pending local file read,
      // even when it keeps the same numeric edit sequence.
      epoch.current++;
      forkWorkbenchWriter(session); session.error = null;
      session.notice = "The earlier input remains in a separate copy. " + (choice === "saved" ? "Using saved values." : "Keep editing and save this draft explicitly.");
      replaceWorkbench(session, next); setReview(null);
    } catch (reason) { session.error = message(reason); notifyWorkbench(session); }
  }
  function restore(copy: WorkbenchRecoveryEntry) {
    if (busy || workbenchUnconfirmed(session.draft) || !copy.value) return;
    try {
      if (workbenchHasWork(session.draft) && !backupWorkbench(session)) return;
      const raw = window.localStorage.getItem(copy.key);
      if (raw !== copy.raw) throw new Error("This Workbench recovery copy changed. Refresh the list first.");
      const restored = decodeWorkbenchCopy(raw, session.draft.scope, copy.writerId);
      epoch.current++;
      forkWorkbenchWriter(session); session.error = null;
      replaceWorkbench(session, reconcileWorkbenchDraft({ ...restored.draft, intent: true }, current.current));
      session.notice = "Restored a separate editable Workbench copy. No Project change was sent."; setReview(null); notifyWorkbench(session);
    } catch (reason) { session.error = message(reason); refreshWorkbenchCopies(session); notifyWorkbench(session); }
  }
  async function readOutcome() {
    if (busy || reviewing || !workbenchUnconfirmed(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setReview(null);
    try {
      const result = await inspectWorkbenchOutcome(session.draft, current.current, {
        readProject: async id => { const result = await bootstrap(id); if (!result.project || result.project_selection.status !== "selected") throw new Error("The saved Workbench project is unavailable."); return result.project; },
        readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!workbenchOutcomeMatches(session.draft, current.current, result)) throw new Error("The selected Workbench state changed during inspection. Read it again.");
      setReview(result);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); notifyWorkbench(session); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function continueEditing() {
    if (busy || !review) return;
    try {
      const next = releaseWorkbenchOutcome(session.draft, current.current, review);
      if (!backupWorkbench(session)) return;
      session.custody = retainReviewedWorkbenchIntent(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      session.backupError = null; session.error = null; session.notice = "The unconfirmed attempt remains in a separate copy. Nothing was acknowledged or retried; save any new intent explicitly.";
      replaceWorkbench(session, next, false); setReview(null); refreshWorkbenchCopies(session); notifyWorkbench(session);
    } catch (reason) { session.backupError = message(reason); notifyWorkbench(session); }
  }
  function download() {
    try {
      const raw = encodeWorkbenchFile(session.draft), url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-workbench-" + target.kind + "-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the complete Workbench input. This does not confirm a browser backup or a Project save."; notifyWorkbench(session);
    } catch (reason) { session.error = message(reason); notifyWorkbench(session); }
  }
  function discard(copy: WorkbenchRecoveryEntry) {
    if (busy || copy.writerId === session.custody.writerId) return;
    try {
      if (window.sessionStorage.getItem(workbenchPointerKey(session.draft.scope)) === copy.writerId && !backupWorkbench(session)) return;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This Workbench recovery changed. Review it before discarding.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("Discard was not confirmed. The Workbench copy remains retained; no second removal was attempted.");
      session.error = null; session.notice = "Confirmed removal of the selected local Workbench copy.";
      refreshWorkbenchCopies(session); notifyWorkbench(session);
    } catch (reason) { session.error = message(reason); notifyWorkbench(session); }
  }
  function freshCopy() {
    if (busy) return;
    // A failed reload may conceal UNKNOWN. Keeping newer typing is not
    // permission to bypass the exact source that this tab could not read.
    if (session.recoveryProblem) { retryBackup(); return; }
    try {
      session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: window.sessionStorage.getItem(workbenchPointerKey(session.draft.scope)) };
      session.recoveryProblem = null;
      if (backupWorkbench(session)) session.notice = "Verified a new copy of your current input. Earlier unreadable or changed bytes were retained separately.";
      notifyWorkbench(session);
    } catch (reason) { session.backupError = message(reason); notifyWorkbench(session); }
  }
  function retryBackup() {
    try {
      if (session.recoveryProblem) {
        const pointer = window.sessionStorage.getItem(workbenchPointerKey(session.draft.scope));
        if (pointer !== session.custody.expectedPointer) throw new Error("The earlier recovery pointer changed. Review its copies or explicitly keep current input in a new copy.");
        if (pointer) {
          const raw = window.localStorage.getItem(workbenchCopyKey(session.draft.scope, pointer));
          if (raw === null) throw new Error("The earlier recovery copy is still missing. Current input remains in this editor.");
          const recovered = decodeWorkbenchCopy(raw, session.draft.scope, pointer).draft;
          if (workbenchUnconfirmed(recovered)) {
            // A restored readable record may reveal a previously uncertain
            // operation. Carry that comparison forward, preserving newer raw
            // input and a later sequence; verification cannot clear UNKNOWN.
            session.draft = { ...recovered, input: session.draft.input, intent: true,
              sequence: Math.max(recovered.sequence, session.draft.sequence) + 1 };
          }
        }
        // Successful explicit verification retains both inputs; it never loads
        // an earlier copy over the author's newer typing.
        session.recoveryProblem = null;
      }
      backupWorkbench(session); notifyWorkbench(session);
    } catch (reason) { session.backupError = message(reason); notifyWorkbench(session); }
  }
  function authorToken() { return { scope: session.draft.scope, sequence: session.draft.sequence, epoch: epoch.current, project: structuredClone(current.current) }; }
  function tokenCurrent(token: ReturnType<typeof authorToken>) {
    return mounted.current && token.epoch === epoch.current && token.scope === session.draft.scope && token.sequence === session.draft.sequence
      && sameProjectRevision(token.project, current.current) && workbenchScope(current.current, target) === token.scope;
  }
  return { session, draft: session.draft, busy, conflict, review, reviewing, edit, send, choose, restore, readOutcome, continueEditing, download, discard, freshCopy, authorToken, tokenCurrent,
    retry: retryBackup,
    blocked: busy || !!session.backupError || conflict || workbenchUnconfirmed(session.draft) };
}
type WorkbenchEditor = ReturnType<typeof useCreativeWorkbenchDraft>;
export function WorkbenchDraftFeedback({ editor, project }: { editor: WorkbenchEditor; project: Project }) {
  const { session, draft, busy, conflict, review } = editor, label = workbenchLabel(draft.target);
  const copies = session.copies.filter(copy => copy.writerId !== session.custody.writerId);
  return <section aria-label={label + " draft recovery"}>
    {session.notice && <p className="production-notice" role="status">{session.notice}</p>}
    {session.error && <p className="production-error" role="alert">{session.error}</p>}
    {session.backupError && <div className="production-notice" role="alert" aria-label={label + " backup warning"}>
      <strong>Local recovery needs attention.</strong><p>{session.backupError}</p>
      <button className="secondary-button" type="button" disabled={busy} onClick={editor.retry}><RefreshCw size={14}/> Retry {label} backup</button>
      <button className="secondary-button" type="button" disabled={busy} onClick={editor.freshCopy}>Keep {label} input in a new copy</button>
    </div>}
    {draft.pending?.outcome === "NOT_SENT" && <p role="status">This preparation was NOT_SENT. Confirm its copy before a separate explicit save.</p>}
    {workbenchUnconfirmed(draft) && draft.pending && <section className="production-notice" role="region" aria-label={"Unconfirmed " + label}>
      <strong>Unconfirmed {label} submission from r{draft.pending.revision}</strong>
      <p>The original response is unconfirmed. Submitted parameters and later input are retained. Observing matching values cannot acknowledge or replay that request.</p>
      <button className="secondary-button" disabled={busy || editor.reviewing} onClick={() => window.location.reload()}>Reload saved {label}</button>
      <button className="secondary-button" disabled={busy || editor.reviewing} onClick={() => void editor.readOutcome()}>Read saved {label} and history</button>
      {review && <div aria-label={label + " outcome review"}>
        <p>Read saved r{review.project.revision} and {review.events.length} consecutive history records. {review.matchingValueRevisions.length} contain matching values; this does not identify the original request.</p>
        <details><summary>Submitted and saved parameters</summary><pre>{JSON.stringify({ submitted: draft.pending.submitted, saved: workbenchSavedInput(review.project, draft.target, draft.input) }, null, 2)}</pre></details>
        <button className="secondary-button" disabled={busy || editor.reviewing || !workbenchOutcomeMatches(draft, project, review)} onClick={editor.continueEditing}>Keep earlier {label} attempt and continue editing</button>
      </div>}
    </section>}
    {conflict && !workbenchUnconfirmed(draft) && <section className="production-notice" role="region" aria-label={label + " conflict"}>
      <strong>Review the current saved revision before applying this draft.</strong><p>Your input started at r{draft.baseRevision}; the current project is r{project.revision}.</p>
      <details><summary>Compare saved and authored parameters</summary><pre>{JSON.stringify({ base: draft.base, saved: workbenchSavedInput(project, draft.target, draft.input), input: draft.input }, null, 2)}</pre></details>
      <button className="secondary-button" disabled={busy} onClick={() => editor.choose("draft")}>Keep my {label} input</button>
      <button className="secondary-button" disabled={busy} onClick={() => editor.choose("saved")}>Use saved {label} values</button>
    </section>}
    {workbenchHasInput(draft) && !conflict && !workbenchUnconfirmed(draft) && <button className="text-button" disabled={busy} onClick={() => editor.choose("saved")}>Use saved {label} values</button>}
    <button className="text-button" type="button" onClick={editor.download}>Download {label} draft</button>
    {(copies.length > 0 || session.limited) && <details role="region" aria-label={label + " recovery copies"}>
      <summary>Local {label} recovery copies</summary><p>Copies are scoped to this project generation, branch, and editor target. Restoring never writes a Project.</p>
      {session.limited && <p>Only the first 64 copies were inspected. No copy was removed automatically.</p>}
      {copies.map((copy, index) => <div role="group" aria-label={label + " recovery copy " + (index + 1)} key={copy.key}>
        <p>{copy.value ? new Date(copy.value.updatedAt).toLocaleString() + (workbenchUnconfirmed(copy.value.draft) ? " · Unconfirmed attempt" : " · Local input") : "Unreadable copy; original bytes were preserved."}</p>
        {copy.value && <details><summary>Inspect copied input</summary><pre>{JSON.stringify(copy.value.draft.input, null, 2)}</pre></details>}
        {copy.value && <button className="secondary-button" disabled={busy || workbenchUnconfirmed(draft)} onClick={() => editor.restore(copy)}>Restore {label} copy {index + 1}</button>}
        <button className="text-button" disabled={busy} onClick={() => editor.discard(copy)}>Discard {label} copy {index + 1}</button>
      </div>)}
    </details>}
  </section>;
}
