import { useEffect, useRef, useState } from "react";
import type { Project } from "./types";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import {
  acknowledgeClaimDraft, CLAIM_COPY_PREFIX, claimChange, claimConflict, claimCopyKey, claimHasWork, claimLocked,
  claimOutcomeMatches, claimPointerKey, claimScope, claimUnknown, createClaimDraft, decodeClaimCopy, editClaimDraft,
  encodeClaimCopy, inspectClaimOutcome, MAX_CLAIM_COPY_BYTES, prepareClaimSubmission, readClaimCopies, readClaimRecoveryPointer,
  reconcileClaimDraft, recoverClaimAfterReadFailure, releaseClaimOutcome, resolveClaimDraft, retainClaimFailure, retainReviewedClaimIntent, writeClaimCopy,
} from "./claimSourceDraft";
import type { ClaimCustody, ClaimDraft, ClaimOutcomeReview, ClaimRaw, ClaimRecoveryEntry, ClaimSourceChange } from "./claimSourceDraft";

type Session = { draft: ClaimDraft; custody: ClaimCustody; saving: boolean; error: string | null; storageError: string | null;
  recoveryProblem: string | null; pointerObserved: boolean; editedWhileRecoveryBlocked: boolean; notice: string | null; copies: ClaimRecoveryEntry[]; limited: boolean; notify?: () => void };
const sessions = new Map<string, Session>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const ownerKey = (scope: string) => JSON.stringify((JSON.parse(scope) as unknown[]).slice(0, 3));
function notify(session: Session) {
  for (const other of sessions.values()) if (ownerKey(other.draft.scope) === ownerKey(session.draft.scope)) other.notify?.();
}
function busy(project: Project) {
  const owner = JSON.stringify([project.id, project.generation, project.active_branch]);
  return [...sessions.values()].some(session => ownerKey(session.draft.scope) === owner && session.saving);
}
function refresh(session: Session) {
  try { const value = readClaimCopies(window.localStorage, session.draft.scope); session.copies = value.entries; session.limited = value.limited; }
  catch (reason) { session.storageError = message(reason); }
}
function fork(session: Session) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function sessionFor(project: Project, target: string): Session {
  const scope = claimScope(project, target), found = sessions.get(scope); if (found) return found;
  let pointer: string | null = null, recovered: ClaimDraft | null = null, problem: string | null = null;
  let pointerObserved = false;
  try {
    pointer = window.sessionStorage.getItem(claimPointerKey(scope)); pointerObserved = true;
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      const raw = window.localStorage.getItem(claimCopyKey(scope, pointer));
      if (raw === null) throw new Error("The earlier claim recovery copy is missing. Its pointer was retained.");
      recovered = decodeClaimCopy(raw, scope, pointer).draft;
    }
  } catch (reason) { problem = message(reason); }
  const session: Session = { draft: recovered ?? createClaimDraft(project, target),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    saving: false, error: null, storageError: problem, recoveryProblem: problem, pointerObserved, editedWhileRecoveryBlocked: false,
    notice: recovered ? "Recovered this tab's claim draft. No operation was repeated." : null, copies: [], limited: false };
  sessions.set(scope, session); refresh(session); return session;
}
function backup(session: Session): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Read that original copy before continuing; newer input can be exported.");
    if (session.custody.expectedRaw !== null) {
      const prior = decodeClaimCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!claimHasWork(prior) && !sameValue(prior, session.draft)) fork(session);
    }
    writeClaimCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.storageError = null; refresh(session); return session.storageError === null;
  } catch (reason) { session.storageError = message(reason); refresh(session); return false; }
}
function replace(session: Session, draft: ClaimDraft, persist = true) {
  session.draft = draft; const result = persist ? backup(session) : true; notify(session); return result;
}
export function useClaimSourceDraft(project: Project, target: string, commit: (change: ClaimSourceChange) => Promise<Project>) {
  const [session] = useState(() => sessionFor(project, target));
  const [, redraw] = useState(0), [outcome, setOutcome] = useState<ClaimOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0); current.current = project;
  const saving = busy(project), conflict = claimConflict(session.draft, project), locked = claimLocked(project, target);
  useEffect(() => {
    mounted.current = true; const update = () => redraw(value => value + 1); session.notify = update;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(CLAIM_COPY_PREFIX)) { refresh(session); update(); } };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  useEffect(() => {
    try {
      const next = reconcileClaimDraft(session.draft, project); if (next !== session.draft) replace(session, next);
      setOutcome(value => claimOutcomeMatches(session.draft, project, value) ? value : null);
    } catch (reason) { session.error = message(reason); notify(session); }
  }, [project, session]);
  function edit<K extends keyof ClaimRaw>(field: K, value: ClaimRaw[K]) {
    epoch.current++; session.error = null; session.notice = null; setOutcome(null);
    if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
    replace(session, editClaimDraft(session.draft, field, value));
  }
  async function save() {
    if (busy(current.current) || session.storageError || session.draft.pending) return;
    const id = crypto.randomUUID(), submittedProject = structuredClone(current.current);
    let prepared: ReturnType<typeof prepareClaimSubmission>;
    try {
      prepared = prepareClaimSubmission(session.draft, submittedProject, id);
      if (!replace(session, prepared.draft)) {
        session.draft = retainClaimFailure(session.draft, id, "The recoverable copy was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: verify this claim draft's local recovery before saving."; notify(session); return;
      }
    } catch (reason) { session.error = message(reason); notify(session); return; }
    session.saving = true; session.error = null; session.notice = null; setOutcome(null); notify(session);
    try {
      const receipt = await commit(prepared.change);
      if ((session.draft as ClaimDraft).pending?.id !== id) return;
      const next = acknowledgeClaimDraft(session.draft, id, receipt, submittedProject);
      fork(session);
      session.notice = "Saved claim at r" + receipt.revision + "." + (next.intent ? " Your later input remains editable." : "");
      replace(session, next);
    } catch (reason) {
      session.error = message(reason);
      replace(session, retainClaimFailure(session.draft, id, message(reason)));
    } finally { session.saving = false; notify(session); }
  }
  function choose(choice: "saved" | "draft") {
    if (busy(current.current) || claimUnknown(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = resolveClaimDraft(session.draft, current.current, choice); epoch.current++; fork(session);
      session.error = null; session.notice = "The earlier draft remains in its own copy. " + (choice === "saved" ? "Using saved claim values." : "The current saved claim is now the explicit base for this draft.");
      replace(session, next); setOutcome(null);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function restore(entry: ClaimRecoveryEntry) {
    if (busy(current.current) || session.draft.pending || !entry.value) return;
    try {
      if (claimHasWork(session.draft) && !backup(session)) return;
      const raw = window.localStorage.getItem(entry.key);
      if (raw !== entry.raw) throw new Error("This claim copy changed. Refresh its contents before restoring.");
      const restored = decodeClaimCopy(raw, session.draft.scope, entry.writerId).draft;
      epoch.current++; fork(session);
      if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
      replace(session, { ...restored, intent: true });
      session.notice = "Restored this claim input into a separate copy. No project change was sent."; setOutcome(null); notify(session);
    } catch (reason) { session.error = message(reason); refresh(session); notify(session); }
  }
  async function readOutcome() {
    if (busy(current.current) || reviewing || !claimUnknown(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setOutcome(null);
    try {
      const result = await inspectClaimOutcome(session.draft, current.current, {
        readProject: async id => {
          const value = await bootstrap(id);
          if (!value.project || value.project_selection.status !== "selected") throw new Error("The saved claim project is unavailable.");
          return value.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!claimOutcomeMatches(session.draft, current.current, result)) throw new Error("The current claim input changed during comparison.");
      setOutcome(result);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); notify(session); } }
    finally { if (mounted.current) setReviewing(false); }
  }
  function separateIntent() {
    if (busy(current.current) || !outcome) return;
    try {
      const next = releaseClaimOutcome(session.draft, current.current, outcome);
      session.custody = retainReviewedClaimIntent(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      epoch.current++; session.storageError = null; session.error = null;
      session.notice = "Kept the original uncertain attempt separately. No result was acknowledged or retried; save a new edit explicitly.";
      replace(session, next, false); setOutcome(null); refresh(session); notify(session);
    } catch (reason) { session.storageError = message(reason); notify(session); }
  }
  function download(raw?: string) {
    try {
      const value = raw ?? encodeClaimCopy(session.draft, session.custody.writerId);
      const url = URL.createObjectURL(new Blob([value], { type: "application/json" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-claim-recovery-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the claim input. This does not confirm a project save."; notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  async function importCopy(file: File) {
    if (busy(current.current) || session.draft.pending) return;
    const started = ++epoch.current, sequence = session.draft.sequence, source = current.current;
    try {
      if (file.size > MAX_CLAIM_COPY_BYTES) throw new Error("This claim recovery file exceeds 256 KiB.");
      const raw = await file.text();
      if (!mounted.current || started !== epoch.current || session.draft.sequence !== sequence || source !== current.current)
        throw new Error("The editor changed while reading the file. Choose the file again after reviewing this draft.");
      const copy = decodeClaimCopy(raw, session.draft.scope);
      if (claimHasWork(session.draft) && !backup(session)) return;
      if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
      fork(session); replace(session, { ...copy.draft, intent: true }); setOutcome(null);
      session.notice = "Imported the claim input into a new copy. No saved request was repeated."; notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function retryBackup() {
    if (busy(current.current)) return;
    try {
      if (session.recoveryProblem) {
        const observed = readClaimRecoveryPointer(window.sessionStorage, session.draft.scope,
          { observed: session.pointerObserved, value: session.custody.expectedPointer });
        session.custody.expectedPointer = observed.value; session.pointerObserved = true;
        const pointer = observed.value;
        let next = session.draft;
        if (pointer !== null) {
          const key = claimCopyKey(session.draft.scope, pointer);
          const raw = window.localStorage.getItem(key);
          if (raw === null) throw new Error("The earlier claim copy is still missing. Export the current input before leaving.");
          const recovered = decodeClaimCopy(raw, session.draft.scope, pointer).draft;
          next = recoverClaimAfterReadFailure(session.draft, recovered, session.editedWhileRecoveryBlocked);
          if (window.localStorage.getItem(key) !== raw)
            throw new Error("The earlier claim copy changed while reading. Read it again before continuing.");
        }
        if (window.sessionStorage.getItem(claimPointerKey(session.draft.scope)) !== pointer)
          throw new Error("The claim recovery pointer changed while reading. Read it again before continuing.");
        // A confirmed empty slot retains the complete current draft. Existing source bytes stay under their writer.
        fork(session); session.draft = next;
        session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
        session.notice = pointer === null
          ? "Confirmed this tab had no earlier claim copy. Retained the current input; no operation was repeated."
          : "Read the earlier claim copy. Its original bytes remain available; no operation was repeated.";
      }
      backup(session); notify(session);
    } catch (reason) { session.storageError = message(reason); notify(session); }
  }
  function freshCopy() {
    if (busy(current.current)) return;
    if (session.recoveryProblem) { retryBackup(); return; }
    try {
      session.custody = { writerId: crypto.randomUUID(), expectedRaw: null,
        expectedPointer: window.sessionStorage.getItem(claimPointerKey(session.draft.scope)) };
      if (backup(session)) session.notice = "Confirmed a new local copy. Earlier copies remain available."; notify(session);
    } catch (reason) { session.storageError = message(reason); notify(session); }
  }
  function discard(entry: ClaimRecoveryEntry) {
    if (busy(current.current) || entry.writerId === session.custody.writerId) return;
    try {
      if (window.sessionStorage.getItem(claimPointerKey(session.draft.scope)) === entry.writerId && !backup(session)) return;
      if (window.localStorage.getItem(entry.key) !== entry.raw) throw new Error("The selected copy changed; read it before removing it.");
      window.localStorage.removeItem(entry.key);
      if (window.localStorage.getItem(entry.key) !== null) throw new Error("The copy removal was not confirmed. No second removal was attempted.");
      session.notice = "Removed only the selected local claim copy."; refresh(session); notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  let validation: string | null = null;
  if (!session.draft.pending && !conflict && !locked) {
    try { claimChange(session.draft, project); } catch (reason) { validation = message(reason); }
  }
  return { session, draft: session.draft, saving, conflict, locked, outcome, reviewing, validation,
    blocked: saving || !!session.storageError || conflict || locked || !!session.draft.pending || !!validation,
    unknown: claimUnknown(session.draft), edit, save, choose, restore, readOutcome, separateIntent, download, importCopy,
    retryBackup, freshCopy, discard, refresh: () => { refresh(session); notify(session); } };
}
