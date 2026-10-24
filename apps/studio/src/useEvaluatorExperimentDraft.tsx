import { useEffect, useMemo, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import type { Change, Project } from "./types";
import type { CreativeEvaluatorBundle } from "./creativeEvaluator";
import {
  acknowledgeExperimentSubmission, compareExperiment, editExperimentField, experimentComparisonCurrent,
  experimentCopies, experimentCopyKey, experimentHasInput, experimentPointerKey, experimentRaw,
  experimentReviewCurrent, experimentScope, exportExperimentDraft, forkExperimentWriter,
  guardExperimentSubmission, inspectExperimentOutcome, openExperimentSession, originalExperimentRecoveryCopy,
  persistExperimentSession, prepareExperimentSubmission, restoreExperimentCopy, retainExperimentFailure,
  retryExperimentCustody, EXPERIMENT_COPY_PREFIX,
  type ExperimentComparison, type ExperimentFieldName, type ExperimentOutcomeReview, type ExperimentSession,
} from "./evaluatorExperimentDraft";

type Live = ExperimentSession & {
  preparing: boolean; inFlight: boolean; error: string | null; backupError: string | null; notice: string | null;
  notify?: () => void;
};
type Copy = ReturnType<typeof experimentCopies>[number];
const sessions = new Map<string, Live>();
const diagnostic = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const localCopies = {
  get length() { return window.localStorage.length; }, key(index: number) { return window.localStorage.key(index); },
  getItem(key: string) { return window.localStorage.getItem(key); }, setItem(key: string, value: string) { window.localStorage.setItem(key, value); },
};
const tabPointers = {
  getItem(key: string) { return window.sessionStorage.getItem(key); }, setItem(key: string, value: string) { window.sessionStorage.setItem(key, value); },
};
const transport = {
  readProject: async (id: string): Promise<Project> => {
    const project = (await bootstrap(id)).project;
    if (!project || project.id !== id) throw new Error("The saved experiment Project is unavailable. No fallback was adopted.");
    return project;
  }, readHistory: projectHistory,
};
function sessionFor(project: Project, baseline: CreativeEvaluatorBundle, sceneId: string | null): Live {
  const scope = experimentScope(project, baseline), found = sessions.get(scope); if (found) return found;
  const opened = openExperimentSession(project, baseline, sceneId, localCopies, tabPointers);
  const session: Live = { ...opened, preparing: false, inFlight: false, error: null,
    backupError: opened.failedSource ? "The original experiment recovery could not be read. It may contain an UNKNOWN registration. Retry that exact source." : null,
    notice: experimentHasInput(opened.draft) || opened.draft.pending
      ? "Recovered experimental input. Run a fresh disposable comparison; recovery never sends or acknowledges a registration." : null,
  }; sessions.set(scope, session); return session;
}
function backup(session: Live): boolean {
  try { persistExperimentSession(session, localCopies, tabPointers); session.backupError = null; return true; }
  catch (reason) { session.backupError = diagnostic(reason) + " Current input remains available for download."; return false; }
}
const notify = (session: Live) => session.notify?.();
function download(raw: string, name: string) {
  const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
}

export function useEvaluatorExperimentDraft(project: Project, baseline: CreativeEvaluatorBundle, sceneId: string | null,
  commit: (change: Change) => Promise<Project>, pinBaseline: () => void, externalBusy: boolean, registrationBlocked: boolean) {
  const scope = experimentScope(project, baseline), session = useMemo(() => sessionFor(project, baseline, sceneId), [scope]);
  const current = useRef({ project, baseline, sceneId, registrationBlocked }), mounted = useRef(true);
  current.current = { project, baseline, sceneId, registrationBlocked };
  const [, repaint] = useState(0), [copies, setCopies] = useState<Copy[]>([]), [copyError, setCopyError] = useState<string | null>(null);
  const [comparison, setComparison] = useState<ExperimentComparison | null>(null), [comparing, setComparing] = useState(false);
  const [review, setReview] = useState<ExperimentOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const visibleComparison = experimentComparisonCurrent(session, project, comparison) ? comparison : null;
  const visibleReview = experimentReviewCurrent(session, project, review) ? review : null;
  const reviewReady = confirmed && visibleReview !== null;
  const working = externalBusy || session.inFlight || session.preparing;
  const blocked = working || registrationBlocked || !!session.failedSource || !!session.backupError || !!session.custody.attempt
    || (!!session.draft.pending && !reviewReady)
    || project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
  const raw = (key: ExperimentFieldName) => experimentRaw(session.draft, key);
  function invalidateReview() { setReview(null); setConfirmed(false); }
  function refreshCopies() {
    try { setCopies(experimentCopies(localCopies, scope, session.failedSource ?? session.recoveryObservation)); setCopyError(null); }
    catch (reason) { setCopyError(diagnostic(reason)); }
  }
  useEffect(() => {
    mounted.current = true; setComparison(null); setComparing(false); invalidateReview(); setReviewing(false);
    const update = () => { repaint(value => value + 1); refreshCopies(); }; session.notify = update; refreshCopies();
    const changed = (event: StorageEvent) => {
      if (event.key !== null && !event.key.startsWith(EXPERIMENT_COPY_PREFIX) && event.key !== experimentPointerKey(scope)) return;
      try {
        const c = session.custody;
        if (tabPointers.getItem(experimentPointerKey(scope)) !== c.expectedPointer) throw new Error("The experiment pointer changed outside this tab. Input was retained.");
        if (c.expectedRaw !== null && localCopies.getItem(experimentCopyKey(scope, c.writer)) !== c.expectedRaw)
          throw new Error("Another writer changed the selected experiment recovery bytes. Input was retained.");
      } catch (reason) { session.backupError = diagnostic(reason); }
      invalidateReview(); update();
    };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
    // Scope owns the live input; current source props are read through current.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, scope]);
  useEffect(() => {
    // Keep the existing experiment rule: selecting a different working scene
    // invalidates its displayed comparison without discarding retained inputs.
    setComparison(null); invalidateReview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneId]);
  function edit(key: ExperimentFieldName, value: string) {
    try {
      pinBaseline();
      editExperimentField(session, current.current.project, current.current.baseline, key, value);
      if (key === "candidateText" || key === "selectedScenes") setComparison(null);
      invalidateReview(); session.error = null; session.notice = null; backup(session);
    } catch (reason) { session.error = diagnostic(reason); }
    notify(session);
  }
  async function compare() {
    if (working || comparing) return;
    // A newly requested comparison needs its own review declarations. The raw
    // candidate, rationale and scene choices remain unchanged.
    edit("ackRights", "false"); edit("ackLimit", "false");
    setComparing(true); setComparison(null); session.error = null;
    const owner = scope, sourceScene = current.current.sceneId;
    try {
      const result = await compareExperiment(session, current.current.project);
      if (mounted.current && owner === experimentScope(current.current.project, current.current.baseline)
        && current.current.sceneId === sourceScene
        && experimentComparisonCurrent(session, current.current.project, result)) setComparison(result);
    } catch (reason) {
      if (owner === experimentScope(current.current.project, current.current.baseline) && current.current.sceneId === sourceScene) {
        session.error = diagnostic(reason); notify(session);
      }
    }
    finally { if (mounted.current && owner === experimentScope(current.current.project, current.current.baseline)) setComparing(false); }
  }
  async function submit() {
    if (blocked || comparing || !visibleComparison || session.preparing || session.inFlight) return;
    let prepared: Awaited<ReturnType<typeof prepareExperimentSubmission>>;
    const sourceScene = current.current.sceneId;
    session.preparing = true; session.error = null; session.notice = null; notify(session);
    try {
      if (!backup(session)) return;
      prepared = await prepareExperimentSubmission(session, current.current.project, visibleComparison, reviewReady ? visibleReview : null);
      if (current.current.registrationBlocked || current.current.sceneId !== sourceScene
        || !experimentComparisonCurrent(session, current.current.project, visibleComparison))
        throw new Error("The complete experiment source changed during revalidation. No registration was sent.");
      // Recheck current bytes after async interpretation before freezing a new
      // original. A recovered/foreign writer cannot be adopted in that interval.
      if (!backup(session)) return;
      forkExperimentWriter(session); session.draft = prepared.draft; session.epoch++;
      if (!backup(session)) {
        session.draft = retainExperimentFailure(session.draft, prepared.id, session.backupError ?? "Experiment retention failed.", true);
        session.error = "NOT_SENT: the exact experiment copy and pointer were not confirmed."; return;
      }
      guardExperimentSubmission(session, current.current.project, prepared.id, prepared.change, localCopies, tabPointers);
      forkExperimentWriter(session); session.inFlight = true; invalidateReview();
    } catch (reason) { session.error = diagnostic(reason); return; }
    finally { session.preparing = false; notify(session); }
    try {
      const receipt = await commit(prepared.change);
      await acknowledgeExperimentSubmission(session, prepared.id, prepared.change, receipt);
      if (session.custody.attempt) retryExperimentCustody(session, localCopies, tabPointers);
      forkExperimentWriter(session);
      session.notice = "Registered experiment from its original saved Project response at r" + receipt.revision + ". Later input remains editable; human creative approval is not assessed.";
      backup(session);
    } catch (reason) {
      session.draft = retainExperimentFailure(session.draft, prepared.id, diagnostic(reason));
      session.error = diagnostic(reason); backup(session);
    } finally { session.inFlight = false; notify(session); }
  }
  function retry() {
    if (working) return;
    try {
      retryExperimentCustody(session, localCopies, tabPointers); session.backupError = null; session.error = null; invalidateReview();
      session.notice = "Confirmed the original experiment recovery bytes. No registration was sent or acknowledged.";
    } catch (reason) { session.backupError = diagnostic(reason); }
    notify(session);
  }
  function restore(copy: Copy) {
    if (working || session.draft.pending || !copy.copy) return;
    try {
      restoreExperimentCopy(session, copy.raw, copy.copy.writer, localCopies, tabPointers);
      session.backupError = null; session.error = null; setComparison(null); invalidateReview();
      session.notice = "Restored a separate experimental input copy. Run a fresh comparison before any registration.";
    } catch (reason) { session.error = diagnostic(reason); }
    notify(session);
  }
  async function inspectOutcome() {
    if (working || reviewing || !session.draft.pending) return;
    setReviewing(true); invalidateReview(); session.error = null; const owner = scope;
    try {
      const result = await inspectExperimentOutcome(session, current.current.project, transport);
      if (mounted.current && owner === experimentScope(current.current.project, current.current.baseline)
        && experimentReviewCurrent(session, current.current.project, result)) setReview(result);
    } catch (reason) { session.error = diagnostic(reason); notify(session); }
    finally { if (mounted.current && owner === experimentScope(current.current.project, current.current.baseline)) setReviewing(false); }
  }
  function downloadCurrent() {
    try { download(exportExperimentDraft(session.draft), "motionwright-evaluator-experiment-input.json"); }
    catch (reason) { session.error = diagnostic(reason); notify(session); }
  }
  const downloadCopy = (copy: { raw: string }) => download(copy.raw, "motionwright-evaluator-experiment-original.json");
  return { session, raw, edit, compare, comparing, comparison: visibleComparison, submit, blocked, working,
    copies, copyError, original: originalExperimentRecoveryCopy(scope, session.failedSource ?? session.recoveryObservation),
    retry, restore, downloadCurrent, downloadCopy, inspectOutcome, reviewing, review: visibleReview, confirmed, setConfirmed, reviewReady };
}

export function ExperimentDraftFeedback({ editor }: { editor: ReturnType<typeof useEvaluatorExperimentDraft> }) {
  const { session } = editor, pending = session.draft.pending;
  return <section className="production-draft-feedback" aria-label="Evaluator experiment recovery">
    {session.notice && <p role="status">{session.notice}</p>}
    {session.error && <p role="alert">{session.error}</p>}
    {session.backupError && <div role="alert"><p>{session.backupError}</p>
      <button className="secondary-button" disabled={editor.working} onClick={editor.retry}>Retry experiment recovery</button>
    </div>}
    {pending && <section aria-label="Unconfirmed evaluator experiment registration">
      <p>The original registration is {pending.outcome}. Its candidate and review input remain retained; recovery and saved-project observations never repeat or acknowledge it.</p>
      <p>Reopen the current saved project, compare the candidate again if necessary, then inspect its complete history before confirming a separate registration intent.</p>
      <button className="secondary-button" disabled={editor.working || editor.reviewing || !!session.failedSource || !!session.custody.attempt}
        onClick={() => void editor.inspectOutcome()}>{editor.reviewing ? "Reading saved experiment outcome…" : "Inspect current experiment project and history"}</button>
      {editor.review && <div role="status">
        <p>Read saved revision {editor.review.observed.revision}; {editor.review.matchingRevisions.length} journal changes match the original registration. This is an observation, not its original response.</p>
        <label className="production-check"><input type="checkbox" aria-label="Confirm separate experiment registration"
          checked={editor.confirmed} onChange={event => editor.setConfirmed(event.target.checked)}/>
          I compared the saved outcome and intend a separate new registration.</label>
      </div>}
    </section>}
    <details><summary>Experiment recovery copies</summary>
      <button className="secondary-button" onClick={editor.downloadCurrent}>Download current experiment input</button>
      {editor.original && <button className="secondary-button" onClick={() => editor.downloadCopy(editor.original!)}>Download original experiment recovery bytes</button>}
      {editor.copyError && <p role="alert">{editor.copyError}</p>}
      {editor.copies.map((copy, index) => <div role="group" aria-label={"Experiment recovery copy " + (index + 1)} key={copy.key + ":" + index}>
        <p>{copy.copy ? "Retained experimental input" : "Unreadable original copy; exact bytes remain available"}</p>
        <button className="secondary-button" onClick={() => editor.downloadCopy(copy)}>Download experiment copy {index + 1}</button>
        {copy.copy && <button className="secondary-button" disabled={editor.working || !!pending || !!session.failedSource}
          onClick={() => editor.restore(copy)}>Restore experiment copy {index + 1}</button>}
      </div>)}
    </details>
  </section>;
}
