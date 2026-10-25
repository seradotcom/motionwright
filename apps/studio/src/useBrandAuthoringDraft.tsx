import { useEffect, useMemo, useRef, useState } from "react";
import type { Change, Project } from "./types";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import {
  acknowledgeBrandDraft, assertBrandOrigins, brandConflict, brandCopyKey, brandHasWork, brandIdentity,
  brandLabel, brandLocked, brandOutcomeMatches, brandPointerKey, brandPreparationMatches, brandProposalCurrent,
  brandSavedRaw, brandScope, brandUnknown, BRAND_COPY_PREFIX, createBrandDraft, decodeBrandCopy,
  editBrandDraft, inspectBrandOutcome, prepareBrandProposal, prepareBrandSubmission, readBrandCopies,
  readBrandPointer, reconcileBrandDraft, recoverBrandRead, resolveBrandDraft, retainBrandFailure,
  retainBrandOrigin, retainBrandProposal, separateBrandIntent, writeBrandCopy,
} from "./brandAuthoringDraft";
import type {
  BrandCustody, BrandDraft, BrandOperation, BrandOutcome, BrandRaw, BrandRecoveryEntry, BrandTarget,
} from "./brandAuthoringDraft";

type ObservedBrandCopy = { key: string; writerId: string; raw: string | null };
interface BrandSession {
  draft: BrandDraft; custody: BrandCustody; pointerObserved: boolean; editedWhileRecoveryBlocked: boolean;
  firstRead: ObservedBrandCopy | null;
  preparing: boolean; saving: boolean; recoveryProblem: string | null; storageError: string | null;
  error: string | null; notice: string | null; copies: BrandRecoveryEntry[]; limited: boolean; notify?: () => void;
}
const sessions = new Map<string, BrandSession>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const owner = (scope: string) => JSON.stringify(brandIdentity(scope).slice(0, 3));
const projectOwner = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
function notify(session: BrandSession) {
  for (const value of sessions.values()) if (owner(value.draft.scope) === owner(session.draft.scope)) value.notify?.();
}
function busy(project: Project) {
  return [...sessions.values()].some(value => owner(value.draft.scope) === projectOwner(project) && (value.saving || value.preparing));
}
function refresh(session: BrandSession) {
  try {
    // A successful inventory read after an initial failure belongs to this
    // recovery attempt even if subsequent enumeration cannot complete.
    if (session.recoveryProblem && session.pointerObserved && session.custody.expectedPointer !== null && !session.firstRead)
      observeBrandCopy(session.draft.scope, session.custody.expectedPointer, session);
    const value = readBrandCopies(window.localStorage, session.draft.scope);
    session.copies = value.entries; session.limited = value.limited;
  } catch (reason) { session.storageError = message(reason); }
  finally {
    const source = session.firstRead;
    if (source && source.raw !== null) {
      let value: BrandRecoveryEntry["value"] = null, error: string | null = null;
      try { value = decodeBrandCopy(source.raw, session.draft.scope, source.writerId); } catch (reason) { error = message(reason); }
      session.copies = [{ ...source, raw: source.raw, value, error }, ...session.copies.filter(copy => copy.key !== source.key)];
    }
  }
}
function fork(session: BrandSession) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function observeBrandCopy(scope: string, writerId: string, state: Pick<BrandSession, "firstRead">) {
  const key = brandCopyKey(scope, writerId), raw = window.localStorage.getItem(key);
  if (state.firstRead && (state.firstRead.key !== key || state.firstRead.raw !== raw))
    throw new Error("The original brand recovery bytes changed. The first observation remains available for download.");
  state.firstRead ??= { key, writerId, raw };
  return { key, raw };
}
function assertBrandFirstRead(session: BrandSession) {
  const source = session.firstRead;
  if (!source) return;
  if (source.raw === null || window.localStorage.getItem(source.key) !== source.raw)
    throw new Error("The original brand recovery bytes changed or remain missing. The first observation was kept.");
  const pointer = window.sessionStorage.getItem(brandPointerKey(session.draft.scope));
  if (pointer !== source.writerId && !(pointer === session.custody.writerId && session.custody.expectedRaw !== null
    && window.localStorage.getItem(brandCopyKey(session.draft.scope, pointer)) === session.custody.expectedRaw))
    throw new Error("The original brand recovery pointer changed. No different copy was adopted.");
  if (window.localStorage.getItem(source.key) !== source.raw)
    throw new Error("The original brand recovery bytes changed during confirmation.");
}
function readOriginal(scope: string, writerId: string, state: Pick<BrandSession, "firstRead">) {
  const { key, raw } = observeBrandCopy(scope, writerId, state);
  if (raw === null) throw new Error("The earlier brand copy is missing. Its pointer and the current input were kept.");
  const draft = retainBrandOrigin(decodeBrandCopy(raw, scope, writerId).draft, { key, raw });
  assertBrandOrigins(window.localStorage, draft);
  if (window.localStorage.getItem(key) !== raw) throw new Error("The earlier brand copy changed while reading.");
  return { draft, key, raw };
}
function sessionFor(project: Project, target: BrandTarget): BrandSession {
  const scope = brandScope(project, target), found = sessions.get(scope);
  if (found) return found;
  let pointer: string | null = null, observed = false, recovered: BrandDraft | null = null, problem: string | null = null;
  const observation: Pick<BrandSession, "firstRead"> = { firstRead: null };
  try {
    pointer = window.sessionStorage.getItem(brandPointerKey(scope)); observed = true;
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      recovered = readOriginal(scope, pointer, observation).draft;
      if (window.sessionStorage.getItem(brandPointerKey(scope)) !== pointer)
        throw new Error("The brand pointer changed while reading its original copy.");
    }
  } catch (reason) { problem = message(reason); recovered = null; }
  const session: BrandSession = {
    draft: recovered ?? createBrandDraft(project, target),
    firstRead: observation.firstRead,
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    pointerObserved: observed, editedWhileRecoveryBlocked: false, preparing: false, saving: false,
    recoveryProblem: problem, storageError: problem, error: null,
    notice: recovered ? "Recovered this tab's " + brandLabel(target) + " input. No operation was repeated." : null,
    copies: [], limited: false,
  };
  sessions.set(scope, session); refresh(session);
  return session;
}
function backup(session: BrandSession): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Read that source before continuing; current input remains exportable.");
    assertBrandFirstRead(session);
    writeBrandCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    assertBrandFirstRead(session);
    session.firstRead = null;
    session.storageError = null; refresh(session);
    return session.storageError === null;
  } catch (reason) { session.storageError = message(reason); refresh(session); return false; }
}
function replace(session: BrandSession, draft: BrandDraft, persist = true) {
  session.draft = draft;
  const retained = persist ? backup(session) : true;
  notify(session); return retained;
}
function currentDraft(session: BrandSession): BrandDraft {
  return session.draft;
}
export function useBrandAuthoringDraft(
  project: Project, target: BrandTarget, commit: (change: Change) => Promise<Project>, externalBusy: boolean,
) {
  const scope = brandScope(project, target);
  const session = useMemo(() => sessionFor(project, target), [scope]);
  const [, render] = useState(0), [outcome, setOutcome] = useState<BrandOutcome | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0);
  current.current = project;
  const working = externalBusy || busy(project), conflict = brandConflict(session.draft, project);
  useEffect(() => {
    mounted.current = true;
    const update = () => render(value => value + 1); session.notify = update;
    const changed = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith(BRAND_COPY_PREFIX) || event.key === brandPointerKey(scope)) {
        try {
          assertBrandOrigins(window.localStorage, session.draft);
          if (session.custody.expectedRaw !== null
              && window.localStorage.getItem(brandCopyKey(scope, session.custody.writerId)) !== session.custody.expectedRaw)
            throw new Error("The current brand recovery copy changed in another window. Current input was kept.");
          if (session.pointerObserved && window.sessionStorage.getItem(brandPointerKey(scope)) !== session.custody.expectedPointer)
            throw new Error("The brand recovery pointer changed. Current input was kept.");
        } catch (reason) { session.storageError = message(reason); }
        refresh(session); update();
      }
    };
    window.addEventListener("storage", changed);
    return () => {
      mounted.current = false; epoch.current++;
      if (session.notify === update) delete session.notify;
      window.removeEventListener("storage", changed);
    };
  }, [session, scope]);
  useEffect(() => {
    try {
      const next = reconcileBrandDraft(session.draft, project);
      if (!sameValue(next, session.draft)) replace(session, next, brandHasWork(next));
      setOutcome(value => brandOutcomeMatches(session.draft, project, value) ? value : null);
    } catch (reason) { session.error = message(reason); notify(session); }
  }, [project, session]);
  function invalidate() { epoch.current++; setOutcome(null); setReviewing(false); }
  function edit(input: BrandRaw | ((raw: BrandRaw) => BrandRaw)) {
    try {
      invalidate(); session.error = null; session.notice = null;
      if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
      const raw = typeof input === "function" ? input(structuredClone(session.draft.raw)) : input;
      replace(session, editBrandDraft(session.draft, raw));
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  async function preview() {
    if (externalBusy || busy(current.current) || session.storageError || session.draft.pending) return;
    const source = structuredClone(session.draft), selected = structuredClone(current.current), started = epoch.current;
    session.preparing = true; session.error = null; session.notice = null; notify(session);
    try {
      const proposal = await prepareBrandProposal(source, selected, crypto.randomUUID());
      if (!mounted.current || started !== epoch.current || !brandPreparationMatches(source, selected, session.draft, current.current)) {
        session.notice = "The input or saved Project changed during preview. The earlier proposal is retained; review explicitly again.";
        return;
      }
      if (brandHasWork(session.draft) && !backup(session)) return;
      const next = retainBrandProposal(session.draft, proposal);
      // A new proposal never overwrites the bytes of the preceding proposal.
      fork(session); invalidate(); replace(session, next);
      session.notice = "Prepared a source-bound campaign proposal. No Project was changed.";
    } catch (reason) { session.error = message(reason); }
    finally { session.preparing = false; notify(session); }
  }
  async function submit(operation: BrandOperation) {
    if (externalBusy || busy(current.current) || session.storageError || session.draft.pending) return;
    const id = crypto.randomUUID(), source = structuredClone(session.draft), submitted = structuredClone(current.current), started = epoch.current;
    session.preparing = true; session.error = null; session.notice = null; notify(session);
    let prepared: Awaited<ReturnType<typeof prepareBrandSubmission>>;
    try {
      prepared = await prepareBrandSubmission(source, submitted, id, operation);
      if (!mounted.current || started !== epoch.current || !brandPreparationMatches(source, submitted, session.draft, current.current)) {
        session.notice = "The input or saved Project changed during preparation. No change was sent; save the current input explicitly.";
        return;
      }
      if (!replace(session, prepared.draft)) {
        session.draft = retainBrandFailure(session.draft, id, "The recovery copy was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: confirm the recoverable brand input before a separate explicit save.";
        return;
      }
    } catch (reason) { session.error = message(reason); return; }
    finally { session.preparing = false; notify(session); }
    const original = structuredClone(prepared.draft.pending!);
    const originalProof = prepared.receiptProof ? structuredClone(prepared.receiptProof) : null;
    session.saving = true; invalidate(); notify(session);
    try {
      const receipt = await commit(prepared.change);
      const live = currentDraft(session);
      if (live.pending?.id !== id) return;
      // Synchronous acknowledgement reads the latest raw buffer after the await.
      const next = acknowledgeBrandDraft(live, id, receipt, submitted, original, originalProof);
      fork(session); session.error = null;
      replace(session, next);
      session.notice = "Saved " + brandLabel(target) + " at r" + receipt.revision + " from the original service response."
        + (next.intent ? " Your other or later input remains editable." : "");
    } catch (reason) {
      session.error = message(reason);
      replace(session, retainBrandFailure(session.draft, id, message(reason)));
    } finally { session.saving = false; notify(session); }
  }
  function choose(choice: "saved" | "draft") {
    if (externalBusy || busy(current.current) || brandUnknown(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = resolveBrandDraft(session.draft, current.current, choice);
      fork(session); invalidate(); session.error = null; replace(session, next);
      session.notice = choice === "saved" ? "Using saved values. The earlier raw input and proposal remain in their copy."
        : "Using the displayed full Project as the explicit base. Earlier input and proposals remain retained.";
      notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function finishRetry() {
    if (backup(session)) {
      // Only the live pre-dispatch failure has this authority. Decoded copies are UNKNOWN.
      if (session.draft.pending?.outcome === "NOT_SENT") {
        fork(session); replace(session, { ...session.draft, pending: null });
      }
      if (!session.storageError) session.notice = "Confirmed the " + brandLabel(target) + " recovery copy. No Project change was sent; save explicitly.";
    }
  }
  function retry() {
    if (externalBusy || busy(current.current)) return;
    try {
      if (session.recoveryProblem) {
        const observed = readBrandPointer(window.sessionStorage, scope, { observed: session.pointerObserved, value: session.custody.expectedPointer });
        session.pointerObserved = observed.observed; session.custody.expectedPointer = observed.value;
        let next = session.draft, original: ReturnType<typeof readOriginal> | null = null;
        if (observed.value !== null) {
          original = readOriginal(scope, observed.value, session);
          next = recoverBrandRead(session.draft, original.draft, session.editedWhileRecoveryBlocked);
        }
        assertBrandOrigins(window.localStorage, next);
        if (window.sessionStorage.getItem(brandPointerKey(scope)) !== observed.value
            || (original && window.localStorage.getItem(original.key) !== original.raw))
          throw new Error("The brand recovery source changed before adoption.");
        fork(session); session.draft = next; session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
      }
      finishRetry();
    } catch (reason) { session.storageError = message(reason); }
    notify(session);
  }
  function freshCopy() {
    if (externalBusy || busy(current.current)) return;
    try {
      if (session.recoveryProblem) throw new Error("Read the earlier recovery source before keeping another copy.");
      readBrandPointer(window.sessionStorage, scope, { observed: session.pointerObserved, value: session.custody.expectedPointer });
      assertBrandOrigins(window.localStorage, session.draft);
      // A different writer keeps any unconfirmed partial data write untouched.
      fork(session); finishRetry();
    } catch (reason) { session.storageError = message(reason); }
    notify(session);
  }
  function restore(copy: BrandRecoveryEntry) {
    if (externalBusy || busy(current.current) || session.draft.pending || !copy.value) return;
    if (session.recoveryProblem) {
      session.storageError = session.recoveryProblem + " Retry the original recovery read before restoring another copy; its pending attempt is not yet known.";
      notify(session); return;
    }
    try {
      if (brandHasWork(session.draft) && !backup(session)) return;
      const observed = readBrandPointer(window.sessionStorage, scope, { observed: session.pointerObserved, value: session.custody.expectedPointer });
      session.pointerObserved = observed.observed; session.custody.expectedPointer = observed.value;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This brand copy changed. Refresh before restoring.");
      const restored = retainBrandOrigin(decodeBrandCopy(copy.raw, scope, copy.writerId).draft, { key: copy.key, raw: copy.raw });
      assertBrandOrigins(window.localStorage, restored);
      if (window.sessionStorage.getItem(brandPointerKey(scope)) !== observed.value || window.localStorage.getItem(copy.key) !== copy.raw)
        throw new Error("The brand recovery source changed before restoration.");
      // Fork before adopting A or publishing a pointer: a failed publication cannot
      // leave A in memory with the previous B writer's custody.
      fork(session); session.draft = restored; session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
      invalidate(); session.error = null; backup(session);
      session.notice = "Restored this brand input into a separate copy. No Project change was sent.";
    } catch (reason) { session.storageError = message(reason); refresh(session); }
    notify(session);
  }
  async function inspect() {
    if (externalBusy || busy(current.current) || reviewing || !brandUnknown(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setOutcome(null);
    try {
      const value = await inspectBrandOutcome(session.draft, current.current, {
        readProject: async id => {
          const result = await bootstrap(id);
          if (!result.project || result.project_selection.status !== "selected") throw new Error("The saved brand Project is unavailable.");
          return result.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!brandOutcomeMatches(session.draft, current.current, value)) throw new Error("The current brand input changed during inspection.");
      setOutcome(value); session.error = null; notify(session);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); notify(session); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function separate() {
    if (externalBusy || busy(current.current) || !outcome) return;
    try {
      if (!backup(session)) return;
      const retained = retainBrandOrigin(session.draft, { key: brandCopyKey(scope, session.custody.writerId), raw: session.custody.expectedRaw! });
      const next = { ...separateBrandIntent(session.draft, current.current, outcome), origins: retained.origins };
      assertBrandOrigins(window.localStorage, next);
      fork(session); invalidate(); session.error = null; replace(session, next);
      session.notice = "Kept the original uncertain attempt. No result was acknowledged or retried; this is a separate editable intent.";
      notify(session);
    } catch (reason) { session.storageError = message(reason); notify(session); }
  }
  function discardProposal() {
    if (externalBusy || busy(current.current) || session.draft.pending) return;
    try {
      if (!backup(session)) return;
      fork(session); invalidate(); replace(session, { ...session.draft, proposal: null });
      session.notice = "Removed the proposal from this editor. Its earlier copy and the current raw input were retained.";
      notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function download(raw?: string) {
    try {
      // Export remains possible even if a complete source snapshot exceeds the local-copy budget.
      const value = raw ?? JSON.stringify({ schema: "motionwright.brand-authoring-export/1", scope, draft: session.draft });
      const url = URL.createObjectURL(new Blob([value], { type: "application/json" })), anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-brand-input-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading retained brand input. A recovery copy does not confirm a Project commit.";
      notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  return {
    draft: session.draft, session, working, conflict, unknown: brandUnknown(session.draft), reviewing, outcome,
    proposalCurrent: brandProposalCurrent(session.draft, project),
    blocked: working || !!session.storageError || conflict || brandLocked(project) || !!session.draft.pending,
    edit, preview, submit, choose, retry, freshCopy, restore, inspect, separate, discardProposal, download,
    refresh: () => { refresh(session); notify(session); },
  };
}
export type BrandEditor = ReturnType<typeof useBrandAuthoringDraft>;
export function BrandDraftFeedback({ editor, project }: { editor: BrandEditor; project: Project }) {
  const { draft, session, working, conflict, unknown, outcome } = editor, label = brandLabel(draft.target);
  const copies = session.copies.filter(copy => copy.writerId !== session.custody.writerId);
  return <section aria-label={label + " draft recovery"}>
    {session.notice && <p className="production-notice" role="status">{session.notice}</p>}
    {session.error && <p className="production-error" role="alert">{session.error}</p>}
    {session.storageError && <div className="production-notice" role="alert" aria-label={label + " recovery warning"}>
      <strong>Local recovery needs attention.</strong><p>{session.storageError}</p>
      <button type="button" className="secondary-button" disabled={working} onClick={editor.retry}>Retry {label} recovery copy</button>
      <button type="button" className="secondary-button" disabled={working || !!session.recoveryProblem} onClick={editor.freshCopy}>Keep current {label} input in a separate copy</button>
    </div>}
    {draft.pending?.outcome === "NOT_SENT" && <p role="status">NOT_SENT: this preparation was not dispatched. Confirm its recovery copy before a separate explicit save.</p>}
    {unknown && draft.pending && <section className="production-notice" aria-label={label + " uncertain outcome"}>
      <h4>Review the original {label} attempt</h4>
      <p>UNKNOWN: the original service response is unconfirmed. Submitted parameters, the exact proposal and later input are retained. Matching saved values do not acknowledge or repeat this request.</p>
      <button type="button" className="secondary-button" disabled={working || editor.reviewing} onClick={() => window.location.reload()}>Reload saved {label}</button>
      <button type="button" className="secondary-button" disabled={working || editor.reviewing} onClick={() => void editor.inspect()}>Read saved {label} and history</button>
      {outcome && <section aria-label={label + " outcome inspection"}>
        <p>Read saved r{outcome.project.revision} and {outcome.events.length} consecutive history records. {outcome.matchingRevisions.length} contain matching parameters. The original attempt remains UNKNOWN.</p>
        <button type="button" className="secondary-button"
          disabled={working || editor.reviewing || !brandOutcomeMatches(draft, project, outcome) || project.revision <= draft.pending.revision}
          onClick={editor.separate}>Keep original {label} attempt and start a separate edit</button>
      </section>}
    </section>}
    {conflict && !unknown && <section className="production-notice" aria-label={label + " source conflict"}>
      <strong>Review the saved Project before using a new base.</strong>
      <p>This input began at r{draft.baseRevision}; the displayed Project is r{project.revision}. Earlier input and proposals remain unchanged.</p>
      <details><summary>Compare saved values and raw input</summary>
        <pre>{JSON.stringify({ saved: brandSavedRaw(project, draft.target), input: draft.raw }, null, 2)}</pre>
      </details>
      <button type="button" className="secondary-button" disabled={working || !!session.storageError} onClick={() => editor.choose("draft")}>Keep my {label} input on this saved revision</button>
      <button type="button" className="secondary-button" disabled={working || !!session.storageError} onClick={() => editor.choose("saved")}>Use saved {label} values</button>
    </section>}
    <button type="button" className="text-button" onClick={() => editor.download()}>Download {label} input</button>
    {(copies.length > 0 || session.limited) && <details aria-label={label + " recovery copies"}>
      <summary>Local {label} recovery copies</summary>
      <p>Copies retain their original project, generation, branch and editor. Restoring a copy does not write the Project.</p>
      {session.limited && <p>The bounded scan is incomplete. No copy was removed.</p>}
      <button type="button" className="text-button" disabled={working} onClick={editor.refresh}>Refresh {label} copies</button>
      {copies.map((copy, index) => <div role="group" aria-label={label + " recovery copy " + (index + 1)} key={copy.key}>
        <p>{copy.value ? new Date(copy.value.updatedAt).toLocaleString() + (copy.value.draft.pending ? " · UNKNOWN attempt" : " · Retained input")
          : "Unreadable copy; original bytes remain retained."}</p>
        {copy.value && <details><summary>Inspect retained input and proposal</summary>
          <pre>{JSON.stringify({ input: copy.value.draft.raw, proposal: copy.value.draft.proposal }, null, 2)}</pre>
        </details>}
        {copy.value && <button type="button" className="secondary-button" disabled={working || !!draft.pending || !!session.recoveryProblem}
          onClick={() => editor.restore(copy)}>Restore {label} copy {index + 1}</button>}
        <button type="button" className="text-button" onClick={() => editor.download(copy.raw)}>Download original {label} copy {index + 1}</button>
      </div>)}
    </details>}
  </section>;
}
