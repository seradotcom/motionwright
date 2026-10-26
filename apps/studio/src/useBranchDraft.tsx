import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import type { Change, Project } from "./types";
import {
  acknowledgeBranchDraft, advanceBranchDraft, assertBranchOrigins, branchCopyKey, branchHasWork,
  branchOutcomeMatches, branchPointerKey, branchScope, branchUnknown, BRANCH_RECOVERY_PREFIX,
  createBranchDraft, decodeBranchCopy, editBranchDraft, inspectBranchOutcome, prepareBranchSubmission,
  readBranchCopies, readBranchRecoveryPointer, recoverBranchAfterReadFailure, reconcileBranchDraft, retainBranchFailure, retainBranchOrigin, separateBranchIntent, validateBranchName, writeBranchCopy,
} from "./branchDraft";
import type { BranchCustody, BranchDraft, BranchOutcome } from "./branchDraft";

type Copies = ReturnType<typeof readBranchCopies>;
interface BranchSession {
  draft: BranchDraft; custody: BranchCustody; pointerObserved: boolean; editedWhileRecoveryBlocked: boolean;
  inFlight: boolean; backupError: string | null;
  recoveryProblem: string | null; error: string | null; notice: string | null; copies: Copies; notify?: () => void;
}
const sessions = new Map<string, BranchSession>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const fork = (session: BranchSession) => { session.custody = {
  writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer,
}; };
function refresh(session: BranchSession) {
  try { session.copies = readBranchCopies(window.localStorage, session.draft.scope); }
  catch (reason) { session.backupError = message(reason); }
}
function backup(session: BranchSession): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem);
    writeBranchCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.backupError = null; refresh(session); return session.backupError === null;
  } catch (reason) { session.backupError = message(reason); refresh(session); return false; }
}
function getSession(project: Project): BranchSession {
  const scope = branchScope(project), existing = sessions.get(scope); if (existing) return existing;
  let pointer: string | null = null, pointerObserved = false, recovered: BranchDraft | null = null, problem: string | null = null;
  try {
    pointer = window.sessionStorage.getItem(branchPointerKey(scope));
    pointerObserved = true;
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      const raw = window.localStorage.getItem(branchCopyKey(scope, pointer));
      if (raw === null) throw new Error("The branch recovery pointer refers to a missing copy. Its earlier input was not replaced.");
      recovered = retainBranchOrigin(decodeBranchCopy(raw, scope, pointer).draft, { key: branchCopyKey(scope, pointer), raw });
      assertBranchOrigins(window.localStorage, recovered);
    }
  } catch (reason) { problem = message(reason); }
  const session: BranchSession = {
    draft: recovered ? recoverBranchAfterReadFailure(recovered, createBranchDraft(project), false, project) : createBranchDraft(project),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    pointerObserved, editedWhileRecoveryBlocked: false,
    inFlight: false, backupError: problem, recoveryProblem: problem, error: null,
    notice: recovered ? "Recovered the branch draft. No creation request was repeated." : null, copies: [],
  };
  sessions.set(scope, session); refresh(session); return session;
}
function replace(session: BranchSession, draft: BranchDraft, persist = true) {
  session.draft = draft; const retained = persist ? backup(session) : true; session.notify?.(); return retained;
}
export function useBranchDraft(project: Project, commit: (change: Change) => Promise<Project>, externalBusy: boolean) {
  const [session] = useState(() => getSession(project)), [, render] = useState(0);
  const [outcome, setOutcome] = useState<BranchOutcome | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), alive = useRef(true), epoch = useRef(0); current.current = project;
  const busy = externalBusy || session.inFlight;
  useEffect(() => {
    alive.current = true; const notify = () => render(value => value + 1); session.notify = notify;
    const storage = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith(BRANCH_RECOVERY_PREFIX)) {
        try { assertBranchOrigins(window.localStorage, session.draft); }
        catch (reason) { session.backupError = message(reason); }
        refresh(session); notify();
      }
    };
    window.addEventListener("storage", storage);
    return () => { alive.current = false; epoch.current++; if (session.notify === notify) delete session.notify; window.removeEventListener("storage", storage); };
  }, [session]);
  useEffect(() => {
    const next = reconcileBranchDraft(session.draft, project);
    if (next !== session.draft) replace(session, next, branchHasWork(next));
    setOutcome(value => branchOutcomeMatches(session.draft, project, value) ? value : null);
  }, [project, session]);
  function edit(name: string) {
    try {
      if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
      session.error = null; session.notice = null;
      replace(session, editBranchDraft(session.draft, name));
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  async function save() {
    if (busy || session.backupError || branchUnknown(session.draft)) return;
    const id = crypto.randomUUID(); let prepared: ReturnType<typeof prepareBranchSubmission>;
    try {
      prepared = prepareBranchSubmission(session.draft, current.current, id);
      session.error = null; session.notice = null;
      if (!replace(session, prepared.draft)) {
        session.draft = retainBranchFailure(session.draft, id, "The recovery copy was not confirmed before dispatch.", true);
        session.error = "No creation request was sent. Confirm recovery before trying again."; session.notify?.(); return;
      }
    } catch (reason) { session.error = message(reason); session.notify?.(); return; }
    session.inFlight = true; session.notify?.();
    try {
      const receipt = await commit(prepared.change);
      const next = acknowledgeBranchDraft(session.draft, id, receipt);
      fork(session); session.error = null;
      session.notice = "Created “" + prepared.draft.pending!.name + "” at r" + receipt.revision + "."
        + (next.name ? " Your later name remains editable." : "");
      replace(session, next);
    } catch (reason) {
      session.error = message(reason);
      replace(session, retainBranchFailure(session.draft, id, message(reason)));
    } finally { session.inFlight = false; session.notify?.(); }
  }
  function retry() {
    try {
      if (session.recoveryProblem) {
        const observation = readBranchRecoveryPointer(window.sessionStorage, session.draft.scope, {
          observed: session.pointerObserved, expectedPointer: session.custody.expectedPointer,
        });
        session.pointerObserved = observation.observed;
        session.custody.expectedPointer = observation.expectedPointer;
        const pointer = observation.expectedPointer;
        if (pointer === null) {
          // A previously observed writer cannot reach this branch: the
          // observation check rejects its removal. Keep any current raw
          // input and UNKNOWN state when the first successful read is empty.
          assertBranchOrigins(window.localStorage, session.draft);
          if (window.sessionStorage.getItem(branchPointerKey(session.draft.scope)) !== null)
            throw new Error("The branch recovery pointer changed before publication.");
          session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false; fork(session);
        } else {
          const raw = window.localStorage.getItem(branchCopyKey(session.draft.scope, pointer));
          if (raw === null) throw new Error("The earlier branch copy is missing; current input remains available for export.");
          const recovered = retainBranchOrigin(decodeBranchCopy(raw, session.draft.scope, pointer).draft, { key: branchCopyKey(session.draft.scope, pointer), raw });
          assertBranchOrigins(window.localStorage, recovered);
          assertBranchOrigins(window.localStorage, session.draft);
          const next = recoverBranchAfterReadFailure(recovered, session.draft, session.editedWhileRecoveryBlocked, current.current);
          assertBranchOrigins(window.localStorage, next);
          if (window.sessionStorage.getItem(branchPointerKey(session.draft.scope)) !== pointer
            || window.localStorage.getItem(branchCopyKey(session.draft.scope, pointer)) !== raw)
            throw new Error("The branch recovery source changed before adoption. Its earlier input was kept.");
          // Adopt ordinary and uncertain input before clearing the read blocker.
          // A later backup failure retries this adopted state, not a fresh placeholder.
          session.draft = next;
          session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false; fork(session);
        }
      }
      if (backup(session)) session.notice = "Current recovery copy confirmed. No creation request was sent.";
    } catch (reason) { session.backupError = message(reason); }
    session.notify?.();
  }
  function advance() {
    if (busy || branchUnknown(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = advanceBranchDraft(session.draft, current.current); fork(session);
      replace(session, next); session.error = null; session.notice = "Using the currently displayed branch as the source. No branch was created.";
      session.notify?.();
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  async function inspect() {
    if (busy || reviewing || !branchUnknown(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setOutcome(null);
    try {
      const result = await inspectBranchOutcome(session.draft, current.current, {
        readProject: async id => {
          const value = await bootstrap(id);
          if (!value.project || value.project_selection.status !== "selected") throw new Error("The saved source branch is unavailable.");
          return value.project;
        }, readHistory: projectHistory,
      });
      if (!alive.current || started !== epoch.current) return;
      if (!branchOutcomeMatches(session.draft, current.current, result)) throw new Error("The source branch changed during inspection. Read it again.");
      setOutcome(result); session.error = null;
    } catch (reason) { if (alive.current && started === epoch.current) { session.error = message(reason); session.notify?.(); } }
    finally { if (alive.current && started === epoch.current) setReviewing(false); }
  }
  function separate() {
    if (busy || !outcome || current.current.revision <= (session.draft.pending?.revision ?? Number.POSITIVE_INFINITY)) return;
    try {
      if (!backup(session)) return;
      const next = separateBranchIntent(session.draft, current.current, outcome, {
        key: branchCopyKey(session.draft.scope, session.custody.writerId), raw: session.custody.expectedRaw!,
      });
      // Origins travel in the published successor and are checked again after
      // data/pointer publication and on reload, not only after this function.
      fork(session); replace(session, next); setOutcome(null);
      session.notice = "The original unconfirmed creation remains in a separate copy. This starts a new name without repeating it.";
      session.error = null; session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  function restore(copy: Copies[number]) {
    if (busy || branchUnknown(session.draft) || !copy.value) return;
    try {
      if (branchHasWork(session.draft) && !backup(session)) return;
      const observation = readBranchRecoveryPointer(window.sessionStorage, session.draft.scope, {
        observed: session.pointerObserved, expectedPointer: session.custody.expectedPointer,
      });
      session.pointerObserved = observation.observed;
      session.custody.expectedPointer = observation.expectedPointer;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This branch recovery copy changed. Refresh it before restoring.");
      const restored = retainBranchOrigin(decodeBranchCopy(copy.raw, session.draft.scope, copy.writerId).draft, { key: copy.key, raw: copy.raw });
      assertBranchOrigins(window.localStorage, restored);
      const next = recoverBranchAfterReadFailure(restored, createBranchDraft(current.current), false, current.current);
      if (window.localStorage.getItem(copy.key) !== copy.raw
        || window.sessionStorage.getItem(branchPointerKey(session.draft.scope)) !== session.custody.expectedPointer)
        throw new Error("The branch recovery source changed before restoration.");
      // Separate custody before adopting the restored input. If pointer
      // publication fails, retry must never write it over the previous draft.
      fork(session);
      session.draft = next; session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
      if (window.sessionStorage.getItem(branchPointerKey(session.draft.scope)) !== session.custody.expectedPointer)
        throw new Error("The writer pointer changed before restoration.");
      window.sessionStorage.setItem(branchPointerKey(session.draft.scope), copy.writerId);
      if (window.sessionStorage.getItem(branchPointerKey(session.draft.scope)) !== copy.writerId) throw new Error("The restored recovery pointer was not confirmed.");
      session.custody.expectedPointer = copy.writerId; session.pointerObserved = true;
      backup(session); setOutcome(null); session.notice = "Restored a separate editable branch name. No creation request was sent.";
    } catch (reason) { session.backupError = message(reason); }
    session.notify?.();
  }
  function download() {
    try {
      refresh(session);
      const value = JSON.stringify({ schema: "motionwright.branch-recovery-export/1", draft: session.draft,
        copies: session.copies.map(copy => ({ key: copy.key, raw: copy.raw })) }, null, 2);
      const url = URL.createObjectURL(new Blob([value], { type: "application/json" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-branch-recovery-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the branch input and retained copies. This does not confirm a saved branch."; session.notify?.();
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  let nameError: string | null = null;
  if (session.draft.name !== "") { try { validateBranchName(session.draft.name, project); } catch (reason) { nameError = message(reason); } }
  const conflict = session.draft.baseRevision !== project.revision;
  return { session, busy, nameError, conflict, outcome, reviewing, edit, save, retry, advance, inspect, separate, restore, download,
    canSeparate: !busy && branchOutcomeMatches(session.draft, project, outcome)
      && project.revision > (session.draft.pending?.revision ?? Number.POSITIVE_INFINITY),
    canSave: !busy && !session.backupError && !session.recoveryProblem && !branchUnknown(session.draft) && !conflict && !nameError && !!session.draft.name.trim() };
}
export function BranchRecovery({ editor }: { editor: ReturnType<typeof useBranchDraft> }) {
  const { session } = editor;
  return <div className="branch-recovery" aria-label="Branch name recovery">
    {session.notice && <p role="status">{session.notice}</p>}
    {session.error && <p className="error-banner inline-error" role="alert">{session.error}</p>}
    {editor.nameError && <p role="alert">{editor.nameError}</p>}
    {session.backupError && <div role="alert"><p>{session.backupError} Your current name remains in this editor.</p>
      <button type="button" className="button compact" disabled={editor.busy} onClick={editor.retry}>Retry branch recovery copy</button></div>}
    {branchUnknown(session.draft) && <section aria-label="Unconfirmed branch creation">
      <p role="status">Creation not confirmed. Check the saved branches before starting another; no request is repeated automatically.</p>
      <button type="button" className="button compact" disabled={editor.busy || editor.reviewing} onClick={() => void editor.inspect()}>
        {editor.reviewing ? "Reading saved branches…" : "Read saved branches and history"}</button>
    </section>}
    {editor.outcome && <section aria-label="Saved branch outcome">
      <p>Read saved revision r{editor.outcome.project.revision} and {editor.outcome.events.length} committed changes.
        Matching names do not identify or acknowledge the original request.</p>
      {session.draft.pending && editor.outcome.project.revision <= session.draft.pending.revision && <p role="status">
        The original request can still commit on this revision. Keep it UNKNOWN and inspect again after the saved revision advances.
      </p>}
      <button type="button" className="button compact" disabled={!editor.canSeparate}
        onClick={editor.separate}>Keep original attempt and start another branch</button>
    </section>}
    {editor.conflict && !branchUnknown(session.draft) && <p>
      This name was prepared from r{session.draft.baseRevision}; the displayed source is newer. Review the branch list below.
      <button type="button" className="button compact" disabled={editor.busy} onClick={editor.advance}>Use the displayed source branch</button>
    </p>}
    {(branchHasWork(session.draft) || session.copies.length > 0 || session.backupError) && <details>
      <summary>Branch recovery copies</summary>
      <p>Copies keep author input separately from saved project history. Restoring a copy never creates a branch.</p>
      <button type="button" className="button compact" onClick={editor.download}>Export branch recovery copy</button>
      {session.copies.filter(copy => copy.writerId !== session.custody.writerId).map(copy => <div key={copy.key}>
        <span>{copy.value?.draft.name || (copy.value ? "Earlier empty name" : "Unreadable retained copy")}</span>
        {copy.value && <button type="button" className="button compact" disabled={editor.busy || branchUnknown(session.draft)}
          onClick={() => editor.restore(copy)}>Restore branch copy</button>}
      </div>)}
    </details>}
  </div>;
}
