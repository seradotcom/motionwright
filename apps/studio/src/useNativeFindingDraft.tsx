import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import type { Change, Project } from "./types";
import {
  acknowledgeNativeFindingSubmission, assertNativeFindingRecoverySource, assertNativeFindingReviewOrigins, NATIVE_FINDING_COPY_PREFIX, nativeFindingConflict, nativeFindingCopyKey, nativeFindingHasWork,
  nativeFindingOutcomeMatches, nativeFindingPointerKey, nativeFindingPreparationMatches, nativeFindingScope, nativeFindingCurrent,
  nativeFindingUnknown, createNativeFindingDraft, decodeNativeFindingCopy, dispatchNativeFindingSubmission, editNativeFindingField, encodeNativeFindingFile,
  inspectNativeFindingOutcome, prepareNativeFindingSubmission, readNativeFindingCopies, readNativeFindingRecoverySource, reconcileNativeFindingDraft, releaseNativeFindingOutcome,
  resolveNativeFindingDraft, retainNativeFindingFailure, retainReviewedNativeFindingIntent, settleNativeFindingFailure, writeNativeFindingCopy, nativeFindingReviewOrigins, recoverNativeFindingSource, repairNativeFindingPointer, hasInterruptedNativeFindingIntent, retryRetainedNativeFindingIntent,
} from "./nativeFindingDraft";
import type { NativeFindingCustody, NativeFindingDraft, NativeFindingField, NativeFindingInput, NativeFindingOutcomeReview, NativeFindingSource, NativeFindingRecoveryEntry, NativeFindingRecoveryObservation } from "./nativeFindingDraft";

type NativeFindingSession = { draft: NativeFindingDraft; custody: NativeFindingCustody; preparing: boolean; inFlight: boolean;
  error: string | null; backupError: string | null; recoveryProblem: string | null; notice: string | null;
  recoveryObservation: NativeFindingRecoveryObservation;
  copies: NativeFindingRecoveryEntry[]; limited: boolean; notify?: () => void };
const sessions = new Map<string, NativeFindingSession>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function refreshCopies(session: NativeFindingSession) {
  const observed = session.recoveryProblem ? session.recoveryObservation : undefined;
  try { const value = readNativeFindingCopies(window.localStorage, session.draft.scope, observed); session.copies = value.entries; session.limited = value.limited; }
  catch (reason) { session.backupError = message(reason); }
  finally {
    // The existing Download copy control retains the exact first observation,
    // even if a later index read fails or another runtime replaces this writer.
    if (observed?.pointer && observed.rawObserved && observed.raw !== null) {
      let value: NativeFindingRecoveryEntry["value"] = null;
      try { value = decodeNativeFindingCopy(observed.raw, session.draft.scope, observed.pointer); } catch { /* Keep unreadable original bytes too. */ }
      const original = { key: nativeFindingCopyKey(session.draft.scope, observed.pointer), writerId: observed.pointer, raw: observed.raw, value };
      session.copies = [original, ...session.copies.filter(copy => copy.key !== original.key)];
    }
  }
}
function getSession(project: Project): NativeFindingSession {
  const scope = nativeFindingScope(project), existing = sessions.get(scope); if (existing) return existing;
  let recovered: NativeFindingDraft | null = null, problem: string | null = null;
  const recoveryObservation: NativeFindingRecoveryObservation = { pointerObserved: false, pointer: null, rawObserved: false, raw: null };
  try {
    const pointer = window.sessionStorage.getItem(nativeFindingPointerKey(scope));
    recoveryObservation.pointer = pointer; recoveryObservation.pointerObserved = true;
    const reload = performance.getEntriesByType("navigation").some(value => (value as PerformanceNavigationTiming).type === "reload");
    if (pointer !== null && reload && !window.opener)
      recovered = readNativeFindingRecoverySource(window.localStorage, window.sessionStorage, scope, recoveryObservation);
  } catch (reason) { problem = message(reason); }
  const session: NativeFindingSession = { draft: recovered ? reconcileNativeFindingDraft(recovered, project) : createNativeFindingDraft(project),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: recoveryObservation.pointer }, preparing: false, inFlight: false,
    error: null, backupError: problem, recoveryProblem: problem, recoveryObservation,
    notice: recovered ? "Recovered your NativeFinding input. No operation was repeated." : null, copies: [], limited: false };
  sessions.set(scope, session); refreshCopies(session); return session;
}
function fork(session: NativeFindingSession) { session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer }; }
function backup(session: NativeFindingSession): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Read or explicitly review that copy before saving new input.");
    if (session.custody.expectedRaw !== null) {
      const old = decodeNativeFindingCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!nativeFindingHasWork(old) && !sameValue(old, session.draft)) fork(session);
    }
    writeNativeFindingCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.backupError = null; refreshCopies(session); return session.backupError === null;
  } catch (reason) {
    session.backupError = message(reason) + " Current input remains in this editor. Saving requires a verified recovery copy and pointer.";
    refreshCopies(session); return false;
  }
}
function replace(session: NativeFindingSession, draft: NativeFindingDraft, persist = true): boolean {
  session.draft = draft; const ok = persist ? backup(session) : true; session.notify?.(); return ok;
}
export function useNativeFindingDraft(project: Project, currentSource: NativeFindingSource | null, commit: (change: Change) => Promise<Project>, externalBusy: boolean) {
  const [session] = useState(() => getSession(project)), [, redraw] = useState(0);
  const [review, setReview] = useState<NativeFindingOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0); current.current = project;
  const inspectedSource = useRef(currentSource); inspectedSource.current = currentSource;
  const busy = externalBusy || session.preparing || session.inFlight;
  const conflict = nativeFindingConflict(session.draft, project);
  useEffect(() => {
    mounted.current = true; const notify = () => redraw(value => value + 1); session.notify = notify;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(NATIVE_FINDING_COPY_PREFIX)) {
      refreshCopies(session); try { assertNativeFindingReviewOrigins(window.localStorage, session.draft); } catch (reason) { session.backupError = message(reason); }
      notify();
    } };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === notify) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  useEffect(() => {
    const next = reconcileNativeFindingDraft(session.draft, project); if (next !== session.draft) replace(session, next);
    setReview(value => nativeFindingOutcomeMatches(session.draft, project, value) ? value : null);
  }, [project, session]);
  useEffect(() => {
    if (currentSource && !session.draft.input.source && !nativeFindingHasWork(session.draft) && !session.recoveryProblem)
      replace(session, editNativeFindingField(session.draft, "source", currentSource));
  }, [currentSource, session]);
  function verifyCurrent(): boolean {
    if (busy || nativeFindingUnknown(session.draft) || !nativeFindingCurrent(session.draft, current.current, inspectedSource.current)) return false;
    return backup(session);
  }
  function edit<K extends NativeFindingField>(field: K, value: NativeFindingInput[K]) {
    try { session.error = null; session.notice = null; setReview(null); replace(session, editNativeFindingField(session.draft, field, value)); }
    catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  async function send() {
    if (externalBusy || session.preparing || session.inFlight || session.backupError || nativeFindingUnknown(session.draft)) return;
    const source = structuredClone(session.draft), sourceProject = structuredClone(current.current), sourceFrame = structuredClone(inspectedSource.current), started = epoch.current, id = crypto.randomUUID();
    session.preparing = true; session.error = null; session.notice = null; session.notify?.();
    let prepared: Awaited<ReturnType<typeof prepareNativeFindingSubmission>>;
    try {
      prepared = await prepareNativeFindingSubmission(source, sourceProject, sourceFrame, id);
      if (!sameValue(sourceFrame, inspectedSource.current) || !mounted.current || started !== epoch.current || !nativeFindingPreparationMatches(source, sourceProject, session.draft, current.current)) {
        session.notice = "NativeFinding input or its selected source changed during preparation. Nothing was sent; save the current intent explicitly."; return;
      }
      if (!replace(session, prepared.draft)) {
        session.draft = retainNativeFindingFailure(session.draft, id, "Recovery was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: verify NativeFinding recovery before a separate save."; return;
      }
      if (!sameValue(sourceFrame, inspectedSource.current) || !nativeFindingPreparationMatches(prepared.draft, sourceProject, session.draft, current.current)) throw new Error("NativeFinding source or input changed before dispatch; nothing was sent.");
      dispatchNativeFindingSubmission(prepared, session.draft, current.current);
    } catch (reason) { session.error = "NOT_SENT: " + message(reason); session.draft = retainNativeFindingFailure(session.draft, id, message(reason), true); if (session.draft.pending?.id === id) backup(session); return; }
    finally { session.preparing = false; session.notify?.(); }
    session.inFlight = true; session.notify?.();
    try {
      const result = await commit(prepared.change);
      if (session.draft.pending?.id !== id) return;
      const next = acknowledgeNativeFindingSubmission(session.draft, prepared, result);
      fork(session); session.error = null;
      session.notice = ("__TAURI_INTERNALS__" in window ? "Saved source-anchored review at r" : "Updated browser demonstration memory at r") + result.revision
        + (next.touched.length ? ". Your other and later inputs remain editable." : ".");
      replace(session, next); return true;
    } catch (reason) { settleNativeFindingFailure(prepared); session.error = message(reason); replace(session, retainNativeFindingFailure(session.draft, id, message(reason))); }
    finally { session.inFlight = false; session.notify?.(); }
  }
  function chooseCurrent() {
    const choice = "draft" as const;
    if (busy || nativeFindingUnknown(session.draft)) return;
    try {
      if (!backup(session)) return;
      const frame = inspectedSource.current; if (!frame) throw new Error("Inspect a current native frame before choosing its source.");
      const next = editNativeFindingField(resolveNativeFindingDraft(session.draft, current.current, choice), "source", frame);
      epoch.current++; fork(session); session.error = null; setReview(null);
      session.notice = "The earlier finding input remains in a separate copy. Your exact fields now refer to the explicitly selected native frame; save this choice separately.";
      replace(session, next);
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function retry() {
    try {
      const retained=retryRetainedNativeFindingIntent(window.localStorage,window.sessionStorage,session.draft,session.custody);
      if(retained){
        session.custody={writerId:retained.writerId,expectedRaw:retained.expectedRaw,expectedPointer:retained.expectedPointer};
        session.error=null;session.backupError=null;session.notice="Verified the interrupted local NativeFinding copy and kept later input. No operation was acknowledged or retried.";
        replace(session,retained.draft,false);setReview(null);refreshCopies(session);session.notify?.();return;
      }
      if (session.recoveryProblem) {
        const recovered = readNativeFindingRecoverySource(window.localStorage, window.sessionStorage, session.draft.scope, session.recoveryObservation);
        // Failed recovery can conceal an UNKNOWN. Carry it forward without
        // overwriting any input typed while the read was unavailable.
        const adopted = recovered ? recoverNativeFindingSource(recovered, session.draft, current.current) : session.draft;
        assertNativeFindingRecoverySource(window.localStorage, window.sessionStorage, session.draft.scope, session.recoveryObservation);
        session.draft = adopted; session.custody.expectedPointer = session.recoveryObservation.pointer; session.recoveryProblem = null;
      }
      repairNativeFindingPointer(window.localStorage, window.sessionStorage, session.draft, session.custody);
      backup(session); session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  function freshCopy() {
    if (busy) return;
    if (session.recoveryProblem || hasInterruptedNativeFindingIntent(session.custody)) { retry(); return; }
    try {
      const pointer = window.sessionStorage.getItem(nativeFindingPointerKey(session.draft.scope));
      if (pointer !== session.custody.expectedPointer || window.localStorage.getItem(nativeFindingCopyKey(session.draft.scope, session.custody.writerId)) !== session.custody.expectedRaw)
        throw new Error("A separate NativeFinding copy cannot adopt changed origin bytes or a foreign pointer. Keep both observations separate until their custody is reviewed.");
      assertNativeFindingReviewOrigins(window.localStorage, session.draft);
      session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer };
      if (backup(session)) session.notice = "Verified a separate NativeFinding input copy. Earlier bytes remain untouched.";
      session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  async function readOutcome() {
    if (busy || reviewing || !nativeFindingUnknown(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setReview(null);
    try {
      const result = await inspectNativeFindingOutcome(session.draft, current.current, {
        readProject: async id => { const value = await bootstrap(id); if (!value.project || value.project_selection.status !== "selected") throw new Error("The saved NativeFinding project is unavailable."); return value.project; }, readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!nativeFindingOutcomeMatches(session.draft, current.current, result)) throw new Error("The selected NativeFinding source changed while reading. Inspect it again.");
      setReview(result);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); session.notify?.(); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function continueEditing() {
    if (busy || !review) return;
    try {
      const next = releaseNativeFindingOutcome(session.draft, current.current, review); if (!backup(session)) return;
      const retained = retainReviewedNativeFindingIntent(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      session.custody = { writerId: retained.writerId, expectedRaw: retained.expectedRaw, expectedPointer: retained.expectedPointer };
      session.error = null; session.backupError = null; session.notice = "The earlier unknown attempt stays in its own copy. No request was acknowledged or retried. Author and save a new choice explicitly.";
      replace(session, retained.draft, false); setReview(null); refreshCopies(session); session.notify?.();
    } catch (reason) { session.backupError = message(reason); session.notify?.(); }
  }
  function download(copy?: NativeFindingRecoveryEntry) {
    try {
      const raw = copy ? JSON.stringify({ schema: "motionwright.local-input-copy/1", storage_key: copy.key, raw: copy.raw }, null, 2) : encodeNativeFindingFile(session.draft);
      const url = URL.createObjectURL(new Blob([raw], { type: "application/json" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-nativeFinding-input-" + Date.now() + ".json"; document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading NativeFinding input. This does not confirm backup or a saved Project."; session.notify?.();
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function restore(copy: NativeFindingRecoveryEntry) {
    if (busy || nativeFindingUnknown(session.draft) || !copy.value) return;
    try {
      if (session.recoveryProblem || nativeFindingHasWork(session.draft) && !backup(session)) throw new Error("Preserve current NativeFinding input and resolve the earlier recovery before restoring another copy.");
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The selected NativeFinding copy changed. Review it again.");
      const recovered = decodeNativeFindingCopy(copy.raw, session.draft.scope, copy.writerId).draft;
      assertNativeFindingReviewOrigins(window.localStorage, recovered);
      if (session.draft.reviewRetention && !sameValue(session.draft.reviewRetention, recovered.reviewRetention))
        throw new Error("A restored copy cannot erase the current NativeFinding review origin conditions.");
      epoch.current++; fork(session); setReview(null); session.error = null;
      replace(session, reconcileNativeFindingDraft(recovered, current.current)); session.notice = "Restored a separate NativeFinding input copy. No Project change was sent."; session.notify?.();
    } catch (reason) { session.error = message(reason); refreshCopies(session); session.notify?.(); }
  }
  function discard(copy: NativeFindingRecoveryEntry) {
    if (busy || copy.writerId === session.custody.writerId) return;
    if(hasInterruptedNativeFindingIntent(session.custody)){session.error="Retry the interrupted local NativeFinding publication before discarding its retained copies.";session.notify?.();return;}
    try {
      const pointerKey = nativeFindingPointerKey(session.draft.scope), pointsHere = window.sessionStorage.getItem(pointerKey) === copy.writerId;
      if (nativeFindingReviewOrigins(session.draft).has(copy.writerId)) throw new Error("This UNKNOWN copy is a retained NativeFinding review origin. Discard cannot erase that publication condition.");
      if (pointsHere && session.recoveryProblem) throw new Error("The earlier NativeFinding source may contain an UNKNOWN operation. Its exact bytes remain preserved until that source can be read and reviewed.");
      if (pointsHere && !session.recoveryProblem && !backup(session)) return;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The selected NativeFinding copy changed. Review its bytes again before discarding.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("Removal was not confirmed. No second removal was attempted.");
      session.error = null; session.notice = "Confirmed removal of the explicitly reviewed local NativeFinding copy."; refreshCopies(session); session.notify?.();
    } catch (reason) { session.error = message(reason); session.notify?.(); }
  }
  function authorToken() { return { source: structuredClone(current.current), draft: structuredClone(session.draft), epoch: epoch.current }; }
  function tokenCurrent(token: ReturnType<typeof authorToken>) {
    return mounted.current && token.epoch === epoch.current
      && nativeFindingPreparationMatches(token.draft, token.source, session.draft, current.current);
  }
  return { session, draft: session.draft, busy, conflict, review, reviewing, edit, send, chooseCurrent, verifyCurrent, retry, freshCopy, readOutcome, continueEditing, download, restore, discard, authorToken, tokenCurrent,
    blocked: busy || !!session.backupError || conflict || nativeFindingUnknown(session.draft) || !nativeFindingCurrent(session.draft, project, currentSource), hasInput: session.draft.touched.length > 0 };
}
