import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import type { Change, Project } from "./types";
import {
  acknowledgeIntegrationSubmission, assertIntegrationReviewOrigins, assertIntegrationRecoverySource, readIntegrationRecoverySource, originalIntegrationRecoveryCopy, INTEGRATION_COPY_PREFIX, integrationConflict, integrationCopyKey, integrationHasWork,
  integrationOutcomeMatches, integrationPointerKey, integrationPreparationMatches, integrationScope,
  integrationUnknown, createIntegrationDraft, decodeIntegrationCopy, dispatchIntegrationSubmission, editIntegrationField, editIntegrationFields, integrationSnapshot, encodeIntegrationFile,
  inspectIntegrationOutcome, prepareIntegrationSubmission, readIntegrationCopies, reconcileIntegrationDraft, releaseIntegrationOutcome,
  resolveIntegrationDraft, retainIntegrationFailure, retainReviewedIntegrationIntent, settleIntegrationFailure, writeIntegrationCopy, integrationReviewOrigins, recoverIntegrationSource, repairIntegrationPointer, hasInterruptedIntegrationIntent, retryRetainedIntegrationIntent,
} from "./integrationAuthoringDraft";
import type { IntegrationCustody, IntegrationDraft, IntegrationField, IntegrationInput, IntegrationOutcomeReview, IntegrationAction, IntegrationRecoveryEntry, IntegrationRecoveryObservation } from "./integrationAuthoringDraft";

type IntegrationSession = { draft: IntegrationDraft; custody: IntegrationCustody; preparing: boolean; inFlight: boolean;
  error: string | null; backupError: string | null; recoveryProblem: string | null; notice: string | null;
  recoveryObservation: IntegrationRecoveryObservation | null;
  copies: IntegrationRecoveryEntry[]; limited: boolean; notify?: () => void };
const sessions = new Map<string, IntegrationSession>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function refreshCopies(session: IntegrationSession) {
  try { const value = readIntegrationCopies(window.localStorage, session.draft.scope, session.recoveryObservation); session.copies = value.entries; session.limited = value.limited; }
  catch (reason) { session.backupError = message(reason); }
  finally {
    // A later listing or same-writer replacement cannot erase the first bytes
    // seen while reopening. Their existing Download action remains exact.
    const original = originalIntegrationRecoveryCopy(session.draft.scope, session.recoveryObservation);
    if (original) session.copies = [original, ...session.copies.filter(copy => copy.key !== original.key)];
  }
}
function getSession(project: Project): IntegrationSession {
  const scope = integrationScope(project), existing = sessions.get(scope); if (existing) return existing;
  let pointer: string | null = null, recovered: IntegrationDraft | null = null, problem: string | null = null;
  const observation: IntegrationRecoveryObservation = { pointerObserved: false, pointer: null, rawObserved: false, raw: null };
  let recoveryObservation: IntegrationRecoveryObservation | null = null;
  try {
    pointer = window.sessionStorage.getItem(integrationPointerKey(scope));
    observation.pointer = pointer; observation.pointerObserved = true;
    const reload = performance.getEntriesByType("navigation").some(value => (value as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      recoveryObservation = observation;
      recovered = readIntegrationRecoverySource(scope, observation, window.localStorage, window.sessionStorage);
    }
  } catch (reason) { problem = message(reason); recoveryObservation = observation; }
  const session: IntegrationSession = { draft: recovered ? reconcileIntegrationDraft(recovered, project) : createIntegrationDraft(project),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer }, preparing: false, inFlight: false,
    error: null, backupError: problem, recoveryProblem: problem, recoveryObservation,
    notice: recovered ? "Recovered your Integration input. No operation was repeated." : null, copies: [], limited: false };
  sessions.set(scope, session); refreshCopies(session); return session;
}
function fork(session: IntegrationSession) { session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer }; }
function backup(session: IntegrationSession): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Read or explicitly review that copy before saving new input.");
    if (session.custody.expectedRaw !== null) {
      const old = decodeIntegrationCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!integrationHasWork(old) && !sameValue(old, session.draft)) fork(session);
    }
    const original = session.recoveryObservation, custody = session.custody;
    if (original) assertIntegrationRecoverySource(session.draft.scope, original, window.localStorage, window.sessionStorage,
      custody.expectedRaw === null ? undefined : { writerId: custody.writerId, raw: custody.expectedRaw });
    writeIntegrationCopy(window.localStorage, window.sessionStorage, session.draft, custody);
    if (original) assertIntegrationRecoverySource(session.draft.scope, original, window.localStorage, window.sessionStorage,
      custody.expectedRaw === null ? undefined : { writerId: custody.writerId, raw: custody.expectedRaw });
    session.recoveryObservation = null;
    session.backupError = null; refreshCopies(session); return session.backupError === null;
  } catch (reason) {
    session.backupError = message(reason) + " Current input remains in this editor. Saving requires a verified recovery copy and pointer.";
    refreshCopies(session); return false;
  }
}
function replace(session: IntegrationSession, draft: IntegrationDraft, persist = true): boolean {
  session.draft = draft; const ok = persist ? backup(session) : true; session.notify?.(); return ok;
}
export function useIntegrationAuthoringDraft(project: Project, commit: (change: Change) => Promise<Project>, externalBusy = false) {
  const [session] = useState(() => getSession(project)), [, redraw] = useState(0);
  const [review, setReview] = useState<IntegrationOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0); current.current = project;
  const busy = externalBusy || session.preparing || session.inFlight;
  const conflict = integrationConflict(session.draft, project);
  useEffect(() => {
    mounted.current = true; const notify = () => redraw(value => value + 1); session.notify = notify;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(INTEGRATION_COPY_PREFIX)) {
      refreshCopies(session); try { assertIntegrationReviewOrigins(window.localStorage, session.draft); } catch (reason) { session.backupError = message(reason); }
      notify();
    } };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === notify) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  useEffect(() => {
    const next = reconcileIntegrationDraft(session.draft, project); if (next !== session.draft) replace(session, next);
    setReview(value => integrationOutcomeMatches(session.draft, project, value) ? value : null);
  }, [project, session]);
  function edit<K extends IntegrationField>(field: K, value: IntegrationInput[K]) {
    try { session.error = null; session.notice = null; setReview(null); replace(session, editIntegrationField(session.draft, field, value)); }
    catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function editMany(values:Partial<IntegrationInput>) {
    try { session.error=null;session.notice=null;setReview(null);replace(session,editIntegrationFields(session.draft,values)); }
    catch(reason){session.error=message(reason);session.notify?.();}
  }
  function loadFields(values:Partial<IntegrationInput>) {
    if(busy||integrationUnknown(session.draft))return;
    try {
      if(!backup(session))return;
      fork(session);editMany(values);
    }catch(reason){session.error=message(reason);session.notify?.();}
  }
  async function send(action: IntegrationAction, targetId: string | null = null) {
    if (externalBusy || session.preparing || session.inFlight || session.backupError || session.recoveryProblem || integrationUnknown(session.draft)) return;
    const source = structuredClone(session.draft), sourceProject = structuredClone(current.current), started = epoch.current, id = crypto.randomUUID();
    session.preparing = true; session.error = null; session.notice = null; session.notify?.();
    let prepared: Awaited<ReturnType<typeof prepareIntegrationSubmission>>;
    try {
      prepared = await prepareIntegrationSubmission(source, sourceProject, action, targetId, id);
      if (!mounted.current || started !== epoch.current || !integrationPreparationMatches(source, sourceProject, session.draft, current.current)) {
        session.notice = "Integration input or its selected source changed during preparation. Nothing was sent; save the current intent explicitly."; return;
      }
      if (!replace(session, prepared.draft)) {
        session.draft = retainIntegrationFailure(session.draft, id, "Recovery was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: verify Integration recovery before a separate save."; return;
      }
      if (!integrationPreparationMatches(prepared.draft, sourceProject, session.draft, current.current)) throw new Error("Integration source or input changed before dispatch; nothing was sent.");
      dispatchIntegrationSubmission(prepared, session.draft, current.current);
    } catch (reason) { session.error = "NOT_SENT: " + message(reason); session.draft = retainIntegrationFailure(session.draft, id, message(reason), true); if (session.draft.pending?.id === id) backup(session); return; }
    finally { session.preparing = false; session.notify?.(); }
    session.inFlight = true; session.notify?.();
    try {
      const result = await commit(prepared.change);
      if (session.draft.pending?.id !== id) return;
      const next = acknowledgeIntegrationSubmission(session.draft, prepared, result);
      fork(session); session.error = null;
      session.notice = ("__TAURI_INTERNALS__" in window ? "Saved integration change at r" : "Updated browser demonstration memory at r") + result.revision
        + (next.touched.length ? ". Your other and later inputs remain editable." : ".");
      replace(session, next); return true;
    } catch (reason) { settleIntegrationFailure(prepared); session.error = message(reason); replace(session, retainIntegrationFailure(session.draft, id, message(reason))); }
    finally { session.inFlight = false; session.notify?.(); }
  }
  function choose(choice: "empty" | "draft") {
    if (busy || integrationUnknown(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = resolveIntegrationDraft(session.draft, current.current, choice);
      epoch.current++; fork(session); session.error = null; setReview(null);
      session.notice = "The earlier Integration input remains in a separate copy. " + (choice === "empty" ? "Starting empty integration input." : "Your exact integration fields and scene identities use the reviewed source; prepare and save explicitly.");
      replace(session, next);
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function retry() {
    try {
      const retained=retryRetainedIntegrationIntent(window.localStorage,window.sessionStorage,session.draft,session.custody);
      if(retained){
        session.custody={writerId:retained.writerId,expectedRaw:retained.expectedRaw,expectedPointer:retained.expectedPointer};
        session.error=null;session.backupError=null;session.notice="Verified the interrupted local Integration copy and kept later input. No operation was acknowledged or retried.";
        replace(session,retained.draft,false);setReview(null);refreshCopies(session);session.notify?.();return;
      }
      if (session.recoveryProblem) {
        const observed = session.recoveryObservation;
        if (!observed) throw new Error("The original Integration recovery observation is unavailable. No different copy was adopted.");
        const recovered = readIntegrationRecoverySource(session.draft.scope, observed, window.localStorage, window.sessionStorage);
        // A failed read can conceal an UNKNOWN. Keep its exact operation and
        // overlay only fields actually authored later, including empty text.
        const adopted = recovered ? recoverIntegrationSource(recovered, session.draft, current.current) : session.draft;
        assertIntegrationRecoverySource(session.draft.scope, observed, window.localStorage, window.sessionStorage);
        session.draft = adopted;
        session.custody.expectedPointer = observed.pointer; session.recoveryProblem = null;
        epoch.current++; setReview(null);
      }
      repairIntegrationPointer(window.localStorage, window.sessionStorage, session.draft, session.custody);
      backup(session); session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  function freshCopy() {
    if (busy) return;
    if (session.recoveryProblem || hasInterruptedIntegrationIntent(session.custody)) { retry(); return; }
    try {
      const pointer = window.sessionStorage.getItem(integrationPointerKey(session.draft.scope));
      if (pointer !== session.custody.expectedPointer || window.localStorage.getItem(integrationCopyKey(session.draft.scope, session.custody.writerId)) !== session.custody.expectedRaw)
        throw new Error("A separate Integration copy cannot adopt changed origin bytes or a foreign pointer. Keep both observations separate until their custody is reviewed.");
      assertIntegrationReviewOrigins(window.localStorage, session.draft);
      session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer };
      if (backup(session)) session.notice = "Verified a separate Integration input copy. Earlier bytes remain untouched.";
      session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  async function readOutcome() {
    if (busy || reviewing || !integrationUnknown(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setReview(null);
    try {
      const result = await inspectIntegrationOutcome(session.draft, current.current, {
        readProject: async id => { const value = await bootstrap(id); if (!value.project || value.project_selection.status !== "selected") throw new Error("The saved Integration project is unavailable."); return value.project; }, readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!integrationOutcomeMatches(session.draft, current.current, result)) throw new Error("The selected Integration source changed while reading. Inspect it again.");
      setReview(result);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); session.notify?.(); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function continueEditing() {
    if (busy || !review) return;
    try {
      const next = releaseIntegrationOutcome(session.draft, current.current, review); if (!backup(session)) return;
      const retained = retainReviewedIntegrationIntent(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      session.custody = { writerId: retained.writerId, expectedRaw: retained.expectedRaw, expectedPointer: retained.expectedPointer };
      session.error = null; session.backupError = null; session.notice = "The earlier unknown attempt stays in its own copy. No request was acknowledged or retried. Author and save a new choice explicitly.";
      replace(session, retained.draft, false); setReview(null); refreshCopies(session); session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  function download(copy?: IntegrationRecoveryEntry) {
    try {
      const raw = copy ? JSON.stringify({ schema: "motionwright.local-input-copy/1", storage_key: copy.key, raw: copy.raw }, null, 2) : encodeIntegrationFile(session.draft);
      const url = URL.createObjectURL(new Blob([raw], { type: "application/json" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-integration-input-" + Date.now() + ".json"; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading Integration input. This does not confirm backup or a saved Project."; session.notify?.();
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function restore(copy: IntegrationRecoveryEntry) {
    if (busy || integrationUnknown(session.draft) || !copy.value) return;
    try {
      if (session.recoveryProblem || integrationHasWork(session.draft) && !backup(session)) throw new Error("Preserve current Integration input and resolve the earlier recovery before restoring another copy.");
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The selected Integration copy changed. Review it again.");
      const recovered = decodeIntegrationCopy(copy.raw, session.draft.scope, copy.writerId).draft;
      assertIntegrationReviewOrigins(window.localStorage, recovered);
      if (session.draft.reviewRetention && !sameValue(session.draft.reviewRetention, recovered.reviewRetention))
        throw new Error("A restored copy cannot erase the current Integration review origin conditions.");
      epoch.current++; fork(session); setReview(null); session.error = null;
      replace(session, reconcileIntegrationDraft(recovered, current.current)); session.notice = "Restored a separate Integration input copy. No Project change was sent."; session.notify?.();
    } catch (reason) { session.error = message(reason); refreshCopies(session); session.notify?.(); }
  }
  function discard(copy: IntegrationRecoveryEntry) {
    if (busy || copy.writerId === session.custody.writerId) return;
    if(hasInterruptedIntegrationIntent(session.custody)){session.error="Retry the interrupted local Integration publication before discarding its retained copies.";session.notify?.();return;}
    try {
      const pointerKey = integrationPointerKey(session.draft.scope), pointsHere = window.sessionStorage.getItem(pointerKey) === copy.writerId;
      if (session.recoveryObservation?.pointer === copy.writerId) throw new Error("The original Integration recovery observation remains required. Download its exact bytes before resolving that source; it cannot be discarded during recovery.");
      if (integrationReviewOrigins(session.draft).has(copy.writerId)) throw new Error("This UNKNOWN copy is a retained Integration review origin. Discard cannot erase that publication condition.");
      if (pointsHere && session.recoveryProblem) throw new Error("The earlier Integration source may contain an UNKNOWN operation. Its exact bytes remain preserved until that source can be read and reviewed.");
      if (pointsHere && !session.recoveryProblem && !backup(session)) return;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The selected Integration copy changed. Review its bytes again before discarding.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("Removal was not confirmed. No second removal was attempted.");
      session.error = null; session.notice = "Confirmed removal of the explicitly reviewed local Integration copy."; refreshCopies(session); session.notify?.();
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function authorToken() { return { source: structuredClone(current.current), draft: structuredClone(session.draft), epoch: epoch.current }; }
  function tokenCurrent(token: ReturnType<typeof authorToken>) {
    return mounted.current && token.epoch === epoch.current
      && integrationPreparationMatches(token.draft, token.source, session.draft, current.current);
  }
  return { project, session, draft: session.draft, busy, conflict, review, reviewing, edit, editMany, loadFields, send, choose, retry, freshCopy, readOutcome, continueEditing, download, restore, discard, authorToken, tokenCurrent,
    blocked: busy || !!session.backupError || !!session.recoveryProblem || conflict || integrationUnknown(session.draft), hasInput: session.draft.touched.length > 0 };
}

type IntegrationEditor=ReturnType<typeof useIntegrationAuthoringDraft>;
export function IntegrationRecoveryFeedback({editor}:{editor:IntegrationEditor}) {
  const {session,draft,project}=editor;
  const current=integrationSnapshot(project);
  return <section className="integration-section compact-integration-note" aria-label="Integration input recovery">
    <div>
      <strong>Recoverable integration input</strong>
      <p role="status" aria-live="polite">{session.notice??(
        editor.busy?"Saving the selected integration change. Other author input remains editable.":
        editor.hasInput?"Your descriptor and binding inputs are retained locally. They remain separate from the saved project.":
        "Descriptor and binding inputs stay editable after saving. Start another record explicitly when needed.")}</p>
      {session.error&&<p role="alert">{session.error}</p>}
      {session.backupError&&<div role="alert">
        <p>{session.backupError}</p>
        <button type="button" className="button secondary" disabled={editor.busy} onClick={editor.retry}>Retry integration recovery</button>
        <button type="button" className="button secondary" disabled={editor.busy} onClick={editor.freshCopy}>Keep separate integration copy</button>
      </div>}
      <button type="button" className="button secondary compact" onClick={()=>editor.download()}>Download integration input</button>
      {integrationUnknown(draft)&&<section aria-label="Unconfirmed integration submission">
        <h3>Unconfirmed integration submission</h3>
        <p>The earlier result is unknown. No descriptor, binding, toggle or removal is repeated automatically.
          Your current input is retained. Reload the saved project before inspecting if its revision changed.</p>
        <button type="button" className="button secondary" disabled={editor.busy||editor.reviewing}
          onClick={()=>void editor.readOutcome()}>{editor.reviewing?"Reading saved integration outcome…":"Read saved integration outcome"}</button>
        {editor.review&&<>
          <p>Read saved revision r{editor.review.project.revision} and {editor.review.events.length} consecutive journal entries.
            Matching value revisions: {editor.review.matchingValueRevisions.join(", ")||"none"}.
            A matching value does not acknowledge the earlier request.</p>
          <button type="button" className="button secondary" disabled={editor.busy}
            onClick={editor.continueEditing}>Keep integration input and continue separately</button>
        </>}
      </section>}
      {editor.conflict&&!integrationUnknown(draft)&&<section aria-label="Integration source conflict">
        <h3>Review the changed integration source</h3>
        <p>Your input started at r{draft.baseRevision}; the selected project is r{project.revision}.
          Review the saved descriptors, bindings and resource identities before choosing a new base.</p>
        <details><summary>Compare saved integration source</summary>
          <h4>Earlier source</h4><pre>{JSON.stringify(draft.base,null,2)}</pre>
          <h4>Current source</h4><pre>{JSON.stringify(current,null,2)}</pre>
        </details>
        <button type="button" className="button secondary" disabled={editor.busy}
          onClick={()=>editor.choose("draft")}>Keep integration input on reviewed source</button>
        <button type="button" className="button secondary" disabled={editor.busy}
          onClick={()=>editor.choose("empty")}>Start separate empty integration input</button>
      </section>}
      <details>
        <summary>Local integration copies ({session.copies.length}{session.limited?"+":""})</summary>
        <p>These are author-input copies, not package trust, remote acceptance or permission to replay a request.</p>
        {session.copies.map(copy=><article key={copy.key}>
          <strong>{copy.value?"Retained input":"Unreadable original bytes"} · {copy.writerId.slice(0,8)}</strong>
          {copy.value&&<span> · source r{copy.value.draft.baseRevision}{integrationUnknown(copy.value.draft)?" · unknown attempt":""}</span>}
          <button type="button" className="button secondary compact" onClick={()=>editor.download(copy)}>Download copy {copy.writerId.slice(0,8)}</button>
          <button type="button" className="button secondary compact" disabled={editor.busy||integrationUnknown(draft)||!copy.value}
            onClick={()=>editor.restore(copy)}>Restore copy {copy.writerId.slice(0,8)}</button>
          <button type="button" className="button secondary compact" disabled={editor.busy||copy.writerId===session.custody.writerId||copy.writerId===session.recoveryObservation?.pointer}
            onClick={()=>editor.discard(copy)}>Discard copy {copy.writerId.slice(0,8)}</button>
        </article>)}
      </details>
    </div>
  </section>;
}
