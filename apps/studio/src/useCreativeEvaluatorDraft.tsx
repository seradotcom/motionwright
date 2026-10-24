import { useEffect, useMemo, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import type { Change, Project } from "./types";
import {
  acknowledgeEvaluatorSubmission, editEvaluatorField, evaluatorCopies, evaluatorCopyKey,
  evaluatorFieldBase, evaluatorHasInput, evaluatorPointerKey, evaluatorRaw, evaluatorReviewCurrent,
  evaluatorScope, exportEvaluatorDraft, forkEvaluatorWriter, guardEvaluatorSubmission,
  inspectEvaluatorOutcome, openEvaluatorSession, originalEvaluatorRecoveryCopy,
  persistEvaluatorSession, prepareEvaluatorSubmission, restoreEvaluatorCopy, retainEvaluatorFailure,
  retryEvaluatorCustody, EVALUATOR_COPY_PREFIX,
} from "./creativeEvaluatorDraft";
import type {
  EvaluatorFieldName, EvaluatorOperation, EvaluatorOutcomeReview, EvaluatorSession,
} from "./creativeEvaluatorDraft";

type Live = EvaluatorSession & {
  inFlight: boolean; backupError: string | null; error: string | null; notice: string | null;
  notify?: () => void;
};
type Copy = ReturnType<typeof evaluatorCopies>[number];
const sessions = new Map<string, Live>();
const diagnostic = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
// Browser Storage access itself may throw. Defer the actual getters until the
// recovery reader can record the failed observation; never substitute no data.
const localCopies = {
  get length() { return window.localStorage.length; },
  key(index: number) { return window.localStorage.key(index); },
  getItem(key: string) { return window.localStorage.getItem(key); },
  setItem(key: string, value: string) { window.localStorage.setItem(key, value); },
};
const tabPointers = {
  getItem(key: string) { return window.sessionStorage.getItem(key); },
  setItem(key: string, value: string) { window.sessionStorage.setItem(key, value); },
};
const transport = {
  readProject: async (id: string): Promise<Project> => {
    const project = (await bootstrap(id)).project;
    if (!project || project.id !== id) throw new Error("The saved evaluator project is unavailable. No fallback source was adopted.");
    return project;
  },
  readHistory: projectHistory,
};
function sessionFor(project: Project): Live {
  const scope = evaluatorScope(project), found = sessions.get(scope);
  if (found) return found;
  const opened = openEvaluatorSession(project, localCopies, tabPointers);
  const session: Live = { ...opened, inFlight: false, error: null,
    backupError: opened.failedSource
      ? "This tab's original evaluator recovery could not be read. It may contain an unconfirmed operation. Retry that exact copy."
      : null,
    notice: evaluatorHasInput(opened.draft) || opened.draft.pending
      ? "Recovered evaluator input. No saved-project observation acknowledges or repeats an operation." : null,
  };
  sessions.set(scope, session); return session;
}
function backup(session: Live): boolean {
  try {
    persistEvaluatorSession(session, window.localStorage, window.sessionStorage);
    session.backupError = null; return true;
  } catch (reason) {
    session.backupError = diagnostic(reason) + " Current input remains available for download.";
    return false;
  }
}
function notify(session: Live) { session.notify?.(); }
function download(raw: string, name: string) {
  const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click();
  URL.revokeObjectURL(url);
}

/** Presentation and local author custody only. Existing E13 changes, the App
 * coordinator and StudioService retain all revision and mutation authority. */
export function useCreativeEvaluatorDraft(project: Project, commit: (change: Change) => Promise<Project>, externalBusy: boolean) {
  const scope = evaluatorScope(project), session = useMemo(() => sessionFor(project), [scope]);
  const [, repaint] = useState(0), current = useRef(project), mounted = useRef(true); current.current = project;
  const [copies, setCopies] = useState<Copy[]>([]), [copyError, setCopyError] = useState<string | null>(null);
  const [review, setReview] = useState<EvaluatorOutcomeReview | null>(null);
  const [reviewConfirmed, setReviewConfirmed] = useState(false), [reviewing, setReviewing] = useState(false);
  const projectLocked = project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
  const reviewReady = reviewConfirmed && evaluatorReviewCurrent(session, project, review);
  const working = externalBusy || session.inFlight;
  const blocked = working || projectLocked || !!session.backupError || !!session.failedSource
    || !!session.custody.attempt || (!!session.draft.pending && !reviewReady);
  function refreshCopies() {
    try {
      setCopies(evaluatorCopies(window.localStorage, scope, session.failedSource ?? session.recoveryObservation));
      setCopyError(null);
    } catch (reason) { setCopyError(diagnostic(reason)); }
  }
  function invalidateReview() { setReview(null); setReviewConfirmed(false); }
  useEffect(() => {
    mounted.current = true;
    const update = () => { repaint(value => value + 1); refreshCopies(); };
    session.notify = update; refreshCopies();
    const changed = (event: StorageEvent) => {
      if (event.key !== null && !event.key.startsWith(EVALUATOR_COPY_PREFIX)
        && event.key !== evaluatorPointerKey(scope)) return;
      try {
        const custody = session.custody;
        if (window.sessionStorage.getItem(evaluatorPointerKey(scope)) !== custody.expectedPointer)
          throw new Error("The evaluator recovery pointer changed outside this tab. Your input was retained.");
        if (custody.expectedRaw !== null && window.localStorage.getItem(evaluatorCopyKey(scope, custody.writer)) !== custody.expectedRaw)
          throw new Error("Another writer changed the selected evaluator recovery bytes. Your input was retained.");
      } catch (reason) { session.backupError = diagnostic(reason); }
      invalidateReview(); update();
    };
    window.addEventListener("storage", changed);
    return () => {
      mounted.current = false;
      if (session.notify === update) delete session.notify;
      window.removeEventListener("storage", changed);
    };
    // A live owner is stable; fresh Project values are read through current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, scope]);
  const raw = (key: EvaluatorFieldName) => evaluatorRaw(session.draft, key, evaluatorFieldBase(project, key));
  function edit(key: EvaluatorFieldName, value: string) {
    try {
      editEvaluatorField(session, current.current, key, value);
      session.error = null; session.notice = null; invalidateReview(); backup(session);
    } catch (reason) { session.error = diagnostic(reason); }
    notify(session);
  }
  async function submit(operation: EvaluatorOperation) {
    if (working || session.inFlight || session.backupError || session.failedSource || session.custody.attempt
      || current.current.locks.some(lock => lock.resource === "project:" + current.current.id && lock.kind === "content")) return;
    let prepared: ReturnType<typeof prepareEvaluatorSubmission>;
    try {
      // Confirm even an untouched existing evaluator selection, while retaining
      // any earlier UNKNOWN, before constructing a separate request.
      if (!backup(session)) { notify(session); return; }
      prepared = prepareEvaluatorSubmission(session, current.current, operation, reviewReady ? review : null);
      forkEvaluatorWriter(session); session.draft = prepared.draft; session.epoch++;
      if (!backup(session)) {
        session.draft = retainEvaluatorFailure(session.draft, prepared.id,
          session.backupError ?? "The evaluator copy was not confirmed.", true);
        session.error = "NOT_SENT: the exact evaluator recovery bytes and pointer were not confirmed.";
        notify(session); return;
      }
      guardEvaluatorSubmission(session, current.current, prepared.id, prepared.change, window.localStorage, window.sessionStorage);
      // The armed record never receives later typing or a response. A mutable
      // successor keeps later input separately, even if it returns to blank.
      forkEvaluatorWriter(session);
      session.inFlight = true; session.error = null; session.notice = null; invalidateReview(); notify(session);
    } catch (reason) { session.error = diagnostic(reason); notify(session); return; }
    try {
      const receipt = await commit(prepared.change);
      await acknowledgeEvaluatorSubmission(session, prepared.id, prepared.change, receipt);
      // A confirmed source response and retention are separate facts. If a
      // local write failed, keep its original bytes and block the next save.
      if (session.custody.attempt) retryEvaluatorCustody(session, window.localStorage, window.sessionStorage);
      forkEvaluatorWriter(session);
      session.notice = "Saved evaluator input at r" + receipt.revision + " from its original Project response. Later input remains editable.";
      backup(session);
    } catch (reason) {
      session.draft = retainEvaluatorFailure(session.draft, prepared.id, diagnostic(reason));
      session.error = diagnostic(reason); backup(session);
    } finally { session.inFlight = false; notify(session); }
  }
  function retry() {
    if (working) return;
    try {
      retryEvaluatorCustody(session, window.localStorage, window.sessionStorage);
      session.backupError = null; session.error = null;
      session.notice = "Confirmed the original evaluator recovery copy. No operation was sent or repeated.";
      invalidateReview();
    } catch (reason) { session.backupError = diagnostic(reason); }
    notify(session);
  }
  function restore(copy: Copy) {
    if (working || session.draft.pending || !copy.copy) return;
    try {
      restoreEvaluatorCopy(session, copy.raw, copy.copy.writer, window.localStorage, window.sessionStorage);
      session.backupError = null; session.error = null; invalidateReview();
      session.notice = "Restored a separate evaluator author copy. No operation was sent or acknowledged.";
    } catch (reason) { session.error = diagnostic(reason); }
    notify(session);
  }
  async function inspectOutcome() {
    if (working || reviewing || !session.draft.pending) return;
    setReviewing(true); invalidateReview(); session.error = null;
    try {
      const result = await inspectEvaluatorOutcome(session, current.current, transport);
      if (mounted.current && evaluatorReviewCurrent(session, current.current, result)) setReview(result);
    } catch (reason) { session.error = diagnostic(reason); notify(session); }
    finally { if (mounted.current) setReviewing(false); }
  }
  function downloadCurrent() {
    try { download(exportEvaluatorDraft(session.draft), "motionwright-evaluator-author-input.json"); }
    catch (reason) { session.error = diagnostic(reason); notify(session); }
  }
  function downloadCopy(copy: { raw: string }) { download(copy.raw, "motionwright-evaluator-recovery-original.json"); }
  function reportError(reason: unknown) { session.error = diagnostic(reason); notify(session); }
  const original = originalEvaluatorRecoveryCopy(scope, session.failedSource ?? session.recoveryObservation);
  return { session, raw, edit, submit, blocked, working, projectLocked, copies, copyError,
    original, retry, restore, downloadCurrent, downloadCopy, reportError,
    inspectOutcome, review, reviewing, reviewConfirmed, setReviewConfirmed, reviewReady };
}

export function EvaluatorDraftFeedback({ editor }: { editor: ReturnType<typeof useCreativeEvaluatorDraft> }) {
  const { session, working, copies, original } = editor, pending = session.draft.pending;
  return <section className="production-draft-feedback" aria-label="Evaluator author input recovery">
    {session.notice && <p role="status">{session.notice}</p>}
    {session.error && <p role="alert">{session.error}</p>}
    {session.backupError && <div role="alert"><p>{session.backupError}</p>
      <button className="secondary-button" disabled={working} onClick={editor.retry}>Retry evaluator recovery</button>
    </div>}
    {pending && <section aria-label="Unconfirmed evaluator operation">
      <p>The original evaluator operation is {pending.outcome}. Its input remains retained. No request is repeated by recovery or by reading the saved project.</p>
      <p>To prepare a separate operation, reopen the current saved project, then read its complete history and compare the result.</p>
      <button className="secondary-button" disabled={working || editor.reviewing || !!session.failedSource || !!session.custody.attempt}
        onClick={() => void editor.inspectOutcome()}>{editor.reviewing ? "Reading saved evaluator result…" : "Read saved evaluator result"}</button>
      {editor.review && <div role="status">
        <p>Read saved revision {editor.review.observed.revision}; {editor.review.matchingRevisions.length} journal changes match the retained operation. This observation does not acknowledge its original response.</p>
        <label className="production-check"><input type="checkbox" aria-label="Confirm separate evaluator intent"
          checked={editor.reviewConfirmed} onChange={event => editor.setReviewConfirmed(event.target.checked)}/>
          I compared the saved result and want to prepare a separate evaluator operation.</label>
      </div>}
    </section>}
    <details><summary>Evaluator recovery copies</summary>
      <button className="secondary-button" onClick={editor.downloadCurrent}>Download current evaluator input</button>
      {original && <div role="group" aria-label="Original evaluator recovery bytes">
        <p>The first observed source bytes are retained, including unreadable content.</p>
        <button className="secondary-button" onClick={() => editor.downloadCopy(original)}>Download original evaluator recovery bytes</button>
      </div>}
      {editor.copyError && <p role="alert">{editor.copyError}</p>}
      {copies.map((copy, index) => <div role="group" aria-label={"Evaluator recovery copy " + (index + 1)} key={copy.key + ":" + index}>
        <p>{copy.copy ? "Retained evaluator author input" : "Unreadable copy; original bytes remain available"}</p>
        <button className="secondary-button" onClick={() => editor.downloadCopy(copy)}>Download evaluator copy {index + 1}</button>
        {copy.copy && <button className="secondary-button" disabled={working || !!pending || !!session.failedSource}
          onClick={() => editor.restore(copy)}>Restore evaluator copy {index + 1}</button>}
      </div>)}
    </details>
  </section>;
}
