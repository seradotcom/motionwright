import ExpandedStudy from "./ExpandedStudy";
import NativeInspectionWorkbench from "./NativeInspectionWorkbench";
import NativeContrastWorkbench from "./NativeContrastWorkbench";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowUpRight, Check, FileBox, Layers, Plus, RefreshCw, Save } from "lucide-react";
import type { CanvasNode, Change, Project, Scene, DeliverableProfile, MotionCanvasRenderEvidence } from "./types";
import { seconds } from "./types";
import CompositionStudy from "./CompositionStudy";
import { inheritedTextInk } from "./canvasInheritedInk";
import { emptyProductionDesign, realizeProductHero, sameValue } from "./creativeProduction";
import ComponentRevisionReview, { useComponentRevisionReview } from "./ComponentRevisionReview";
import { bootstrap, projectHistory } from "./api";
import {
  acknowledgeProductionDraft, assertProductionOrigins, cleanProductionPlan, createProductionDraft, decodeProductionCopy, editProductionDraft,
  encodeProductionFile, inspectProductionOutcome, prepareProductionSubmission, productionConflict,
  productionCopyKey, productionHasInput, productionHasWork, productionLabel, productionOutcomeMatches,
  productionPointerKey, productionPreparationMatches, productionScope, productionUnconfirmed, productionValue,
  PRODUCTION_RECOVERY_PREFIX, readProductionCopies, readProductionRecoveryPointer, recoverProductionAfterReadFailure, reconcileProductionDraft, releaseProductionOutcome,
  resolveProductionDraft, retainProductionFailure, retainProductionOrigin, retainReviewedProductionIntent, writeProductionCopy,
} from "./productionDesignDraft";
import type { ProductionCustody, ProductionDraft, ProductionInput, ProductionOperation, ProductionOutcomeReview, ProductionRecoveryEntry, ProductionTarget } from "./productionDesignDraft";
import type { HeroConfig, ProductionPlan, NarrativeEvidenceKind, NativeCapsule } from "./creativeProduction";
import PaidGenerationWorkspace from "./PaidGenerationWorkspace";
import CreativePatchWorkbench from "./CreativePatchWorkbench";
import ProceduralFieldWorkbench from "./ProceduralFieldWorkbench";
import BrandTasteWorkbench from "./BrandTasteWorkbench";
import CreativeEvaluatorWorkbench from "./CreativeEvaluatorWorkbench";
import MusicClockWorkbench from "./MusicClockWorkbench";
import ProductStageWorkbench from "./ProductStageWorkbench";
import CameraReframeWorkbench from "./CameraReframeWorkbench";
import SemanticGraphWorkbench, { type SemanticGraphWorkbenchProps } from "./SemanticGraphWorkbench";
import CaptureSourceWorkbench from "./CaptureSourceWorkbench";
import IdentityOriginWorkbench from "./IdentityOriginWorkbench";
import MeasuredDataWorkbench from "./MeasuredDataWorkbench";
import RenderTailWorkbench from "./RenderTailWorkbench";
import NativePropertyFidelityWorkbench from "./NativePropertyFidelityWorkbench";
import NativeSourceWorkbench, { type NativeSourceServices } from "./NativeSourceWorkbench";
import ComponentDistillationWorkbench from "./ComponentDistillationWorkbench";
import type { NativeReviewRepairDraft } from "./nativeReviewRepair";
import "./production-design.css";

type Commit = (change: Change) => Promise<void>;
type ProductionCommit = (change: Change) => Promise<Project>;
const previewIdentity = "00000000-0000-4000-8000-000000000005";
const aspects = [{ label:"16:9", width:1920,height:1080 },{ label:"9:16",width:1080,height:1920 },{label:"1:1",width:1080,height:1080}];

type ObservedProductionCopy = { key: string; writerId: string; raw: string | null };
type ProductionSession = { draft: ProductionDraft; custody: ProductionCustody; inFlight: boolean; preparing: boolean;
  firstRead: ObservedProductionCopy | null;
  pointerObserved: boolean; editedWhileRecoveryBlocked: boolean;
  error: string | null; backupError: string | null; recoveryProblem: string | null; notice: string | null; copies: ProductionRecoveryEntry[]; limited: boolean; notify?: () => void };
// Author buffers survive tab/workspace navigation. They never own Project state
// and never replay a stored mutation. App's existing coordinator is the writer.
const productionSessions = new Map<string, ProductionSession>();
const productionOwner = (scope: string) => JSON.stringify((JSON.parse(scope) as unknown[]).slice(0, 3));
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function notifyProduction(session: ProductionSession) {
  for (const value of productionSessions.values()) if (productionOwner(value.draft.scope) === productionOwner(session.draft.scope)) value.notify?.();
}
function productionBusy(project: Project) {
  const owner = JSON.stringify([project.id, project.generation, project.active_branch]);
  return [...productionSessions.values()].some(value => productionOwner(value.draft.scope) === owner && (value.inFlight || value.preparing));
}
function observeProductionCopy(scope: string, writerId: string, state: Pick<ProductionSession, "firstRead">) {
  const key = productionCopyKey(scope, writerId), raw = window.localStorage.getItem(key);
  if (state.firstRead && (state.firstRead.key !== key || state.firstRead.raw !== raw))
    throw new Error("The original Production recovery bytes changed. The first observation remains available for download.");
  // Record even an observed absence before decoding or another read can fail.
  state.firstRead ??= { key, writerId, raw };
  return { key, raw };
}
function assertProductionFirstRead(session: ProductionSession) {
  const source = session.firstRead;
  if (!source) return;
  if (source.raw === null || window.localStorage.getItem(source.key) !== source.raw)
    throw new Error("The original Production recovery bytes changed or remain missing. The first observation was kept.");
  const pointer = window.sessionStorage.getItem(productionPointerKey(session.draft.scope));
  if (pointer !== source.writerId && !(pointer === session.custody.writerId && session.custody.expectedRaw !== null
    && window.localStorage.getItem(productionCopyKey(session.draft.scope, pointer)) === session.custody.expectedRaw))
    throw new Error("The original Production recovery pointer changed. No different copy was adopted.");
  if (window.localStorage.getItem(source.key) !== source.raw)
    throw new Error("The original Production recovery bytes changed during confirmation.");
}
function refreshProductionCopies(session: ProductionSession) {
  try {
    // An inventory read may be the first successful read after a transient
    // failure. Anchor it before a later copy/listing can fail or change.
    if (session.recoveryProblem && session.pointerObserved && session.custody.expectedPointer !== null && !session.firstRead)
      observeProductionCopy(session.draft.scope, session.custody.expectedPointer, session);
    const result = readProductionCopies(window.localStorage, session.draft.scope); session.copies = result.entries; session.limited = result.limited;
  }
  catch (reason) { session.backupError = message(reason); }
  finally {
    const source = session.firstRead;
    if (source && source.raw !== null) {
      let value: ProductionRecoveryEntry["value"] = null;
      try { value = decodeProductionCopy(source.raw, session.draft.scope, source.writerId); } catch { /* Exact unreadable input stays downloadable. */ }
      session.copies = [{ ...source, raw: source.raw, value }, ...session.copies.filter(copy => copy.key !== source.key)];
    }
  }
}
function getProductionSession(project: Project, target: ProductionTarget): ProductionSession {
  const scope = productionScope(project, target), existing = productionSessions.get(scope);
  if (existing) return existing;
  let pointer: string | null = null, pointerObserved = false, recovered: ProductionDraft | null = null, backupError: string | null = null;
  const observation: Pick<ProductionSession, "firstRead"> = { firstRead: null };
  try {
    pointer = window.sessionStorage.getItem(productionPointerKey(scope));
    pointerObserved = true;
    // A cloned/new tab starts an independent buffer. Recovery of that tab's own
    // pointer is automatic only on reload; all copies remain explicitly visible.
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    if (pointer && reload && !window.opener) {
      const { key, raw } = observeProductionCopy(scope, pointer, observation);
      if (raw === null) throw new Error("The Production pointer refers to a missing copy. Earlier author bytes were not replaced.");
      const candidate = retainProductionOrigin(decodeProductionCopy(raw, scope, pointer).draft, { key, raw });
      assertProductionOrigins(window.localStorage, candidate);
      if (window.sessionStorage.getItem(productionPointerKey(scope)) !== pointer || window.localStorage.getItem(key) !== raw)
        throw new Error("The Production recovery source changed before adoption. Earlier input was kept.");
      recovered = recoverProductionAfterReadFailure(candidate, createProductionDraft(project, target), false, project);
    }
  } catch (reason) { backupError = message(reason); }
  const session: ProductionSession = {
    draft: recovered ?? createProductionDraft(project, target),
    firstRead: observation.firstRead,
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    pointerObserved, editedWhileRecoveryBlocked: false,
    inFlight: false, preparing: false, error: null, backupError, recoveryProblem: backupError,
    notice: recovered ? "Recovered this tab's Production input. No saved operation was repeated." : null, copies: [], limited: false,
  };
  productionSessions.set(scope, session); refreshProductionCopies(session); return session;
}
function forkProductionWriter(session: ProductionSession) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function backupProduction(session: ProductionSession): boolean {
  try {
    // A failed own-copy recovery is not erased by the first new keystroke.
    // Keep the original pointer/bytes until an explicit recovery action.
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Retry reading the earlier copy before starting new custody; current input remains available for download.");
    if (session.custody.expectedRaw !== null) {
      const old = decodeProductionCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!productionHasWork(old) && !sameValue(old, session.draft)) forkProductionWriter(session);
    }
    assertProductionFirstRead(session);
    writeProductionCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    assertProductionFirstRead(session);
    session.firstRead = null;
    session.backupError = null; refreshProductionCopies(session); return session.backupError === null;
  } catch (reason) {
    session.backupError = message(reason) + " Your current input remains in this editor; saving requires a verified copy and pointer.";
    refreshProductionCopies(session); return false;
  }
}
function replaceProduction(session: ProductionSession, draft: ProductionDraft, persist = true) {
  session.draft = draft;
  const saved = persist ? backupProduction(session) : true;
  notifyProduction(session); return saved;
}
function useProductionEditor(project: Project, target: ProductionTarget, commit: ProductionCommit, externalBusy: boolean) {
  const [session] = useState(() => getProductionSession(project, target));
  const [, redraw] = useState(0), [review, setReview] = useState<ProductionOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0);
  current.current = project;
  const busy = externalBusy || productionBusy(project), conflict = productionConflict(session.draft, project);
  useEffect(() => {
    mounted.current = true; const update = () => redraw(value => value + 1); session.notify = update;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(PRODUCTION_RECOVERY_PREFIX)) {
      try { assertProductionOrigins(window.localStorage, session.draft); }
      catch (reason) { session.backupError = message(reason); }
      refreshProductionCopies(session); update();
    } };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  useEffect(() => {
    const next = reconcileProductionDraft(session.draft, project);
    if (next !== session.draft) replaceProduction(session, next);
    setReview(value => productionOutcomeMatches(session.draft, project, value) ? value : null);
  }, [project, session]);
  function edit(input: ProductionInput) {
    try {
      const next = editProductionDraft(session.draft, input);
      if (session.recoveryProblem) session.editedWhileRecoveryBlocked = true;
      session.error = null; session.notice = null; setReview(null);
      replaceProduction(session, next);
    } catch (reason) { session.error = message(reason); notifyProduction(session); }
  }
  async function send(operation: ProductionOperation = "save") {
    if (externalBusy || productionBusy(current.current) || session.backupError || productionUnconfirmed(session.draft)) return;
    const source = structuredClone(session.draft), sourceProject = structuredClone(current.current), started = epoch.current, id = crypto.randomUUID();
    session.preparing = true; session.error = null; session.notice = null; notifyProduction(session);
    let prepared: Awaited<ReturnType<typeof prepareProductionSubmission>>;
    try {
      prepared = await prepareProductionSubmission(source, sourceProject, id, operation);
      if (!mounted.current || started !== epoch.current || !productionPreparationMatches(source, sourceProject, session.draft, current.current)) {
        session.notice = "The Production input or selected project changed during preparation. No change was sent; review the current input and save explicitly.";
        return;
      }
      if (!replaceProduction(session, prepared.draft)) {
        session.draft = retainProductionFailure(session.draft, id, "Local recovery was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: verify this Production input's recovery before saving."; return;
      }
    } catch (reason) { session.error = message(reason); return; }
    finally { session.preparing = false; notifyProduction(session); }
    session.inFlight = true; notifyProduction(session);
    try {
      const receipt = await commit(prepared.change);
      if (session.draft.pending?.id !== id) return;
      const next = acknowledgeProductionDraft(session.draft, id, receipt);
      // Keep the armed UNKNOWN copy intact even when the post-ACK checkpoint
      // cannot be retained. A later reload remains conservative.
      forkProductionWriter(session); session.error = null;
      session.notice = "Saved " + productionLabel(target) + " at r" + receipt.revision + "."
        + (productionHasInput(next) ? " Your later input remains editable." : "");
      replaceProduction(session, next);
    } catch (reason) { session.error = message(reason); replaceProduction(session, retainProductionFailure(session.draft, id, message(reason))); }
    finally { session.inFlight = false; notifyProduction(session); }
  }
  function choose(choice: "saved" | "draft") {
    if (busy || productionUnconfirmed(session.draft)) return;
    try {
      if (!backupProduction(session)) return;
      const next = resolveProductionDraft(session.draft, current.current, choice);
      forkProductionWriter(session); session.error = null;
      session.notice = "The earlier input remains in a separate copy. " + (choice === "saved" ? "Using saved values." : "Keep editing and save this draft explicitly.");
      replaceProduction(session, next); setReview(null);
    } catch (reason) { session.error = message(reason); notifyProduction(session); }
  }
  function restore(copy: ProductionRecoveryEntry) {
    if (busy || productionUnconfirmed(session.draft) || !copy.value) return;
    try {
      if (session.recoveryProblem) throw new Error("Retry the earlier Production copy before restoring another input. Its unconfirmed attempt must remain recoverable.");
      if (productionHasWork(session.draft) && !backupProduction(session)) return;
      const observation = readProductionRecoveryPointer(window.sessionStorage, session.draft.scope, {
        observed: session.pointerObserved, expectedPointer: session.custody.expectedPointer,
      });
      session.pointerObserved = observation.observed; session.custody.expectedPointer = observation.expectedPointer;
      const raw = window.localStorage.getItem(copy.key);
      if (raw !== copy.raw) throw new Error("This Production recovery copy changed. Refresh the list first.");
      const restored = retainProductionOrigin(decodeProductionCopy(raw, session.draft.scope, copy.writerId).draft, { key: copy.key, raw });
      assertProductionOrigins(window.localStorage, restored);
      const next = recoverProductionAfterReadFailure({ ...restored, intent: true }, createProductionDraft(current.current, target), false, current.current);
      if (window.localStorage.getItem(copy.key) !== raw
        || window.sessionStorage.getItem(productionPointerKey(session.draft.scope)) !== session.custody.expectedPointer)
        throw new Error("The Production recovery source changed before restoration.");
      forkProductionWriter(session); session.error = null;
      session.editedWhileRecoveryBlocked = false;
      replaceProduction(session, next);
      session.notice = "Restored a separate editable Production copy. No Project change was sent."; setReview(null); notifyProduction(session);
    } catch (reason) { session.error = message(reason); refreshProductionCopies(session); notifyProduction(session); }
  }
  async function readOutcome() {
    if (busy || reviewing || !productionUnconfirmed(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setReview(null);
    try {
      const result = await inspectProductionOutcome(session.draft, current.current, {
        readProject: async id => { const result = await bootstrap(id); if (!result.project || result.project_selection.status !== "selected") throw new Error("The saved Production project is unavailable."); return result.project; },
        readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!productionOutcomeMatches(session.draft, current.current, result)) throw new Error("The selected Production state changed during inspection. Read it again.");
      setReview(result);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); notifyProduction(session); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function continueEditing() {
    if (busy || !review) return;
    try {
      const next = releaseProductionOutcome(session.draft, current.current, review);
      if (!backupProduction(session)) return;
      session.custody = retainReviewedProductionIntent(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      session.backupError = null; session.error = null; session.notice = "The unconfirmed attempt remains in a separate copy. Nothing was acknowledged or retried; save any new intent explicitly.";
      replaceProduction(session, next, false); setReview(null); refreshProductionCopies(session); notifyProduction(session);
    } catch (reason) { session.backupError = message(reason); notifyProduction(session); }
  }
  function download(copyRaw?: string) {
    try {
      const raw = copyRaw ?? encodeProductionFile(session.draft), url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-production-" + target.kind + (copyRaw === undefined ? "-draft-" : "-copy-") + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the complete Production input. This does not confirm a browser backup or a Project save."; notifyProduction(session);
    } catch (reason) { session.error = message(reason); notifyProduction(session); }
  }
  function discard(copy: ProductionRecoveryEntry) {
    if (busy || copy.writerId === session.custody.writerId) return;
    try {
      if (session.firstRead?.key === copy.key) throw new Error("The first Production recovery observation is still required. Download its original bytes before resolving that source.");
      if (session.draft.origins?.some(origin => origin.key === copy.key)) throw new Error("This copy retains an earlier unconfirmed Production attempt. Download it without removing its original bytes.");
      if (window.sessionStorage.getItem(productionPointerKey(session.draft.scope)) === copy.writerId && !backupProduction(session)) return;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This Production recovery changed. Review it before discarding.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("Discard was not confirmed. The Production copy remains retained; no second removal was attempted.");
      session.error = null; session.notice = "Confirmed removal of the selected local Production copy.";
      refreshProductionCopies(session); notifyProduction(session);
    } catch (reason) { session.error = message(reason); notifyProduction(session); }
  }
  function freshCopy() {
    if (busy) return;
    try {
      if (session.recoveryProblem) throw new Error("Retry the earlier Production copy before creating new custody. It may contain an unconfirmed attempt; current input remains available for download.");
      const observation = readProductionRecoveryPointer(window.sessionStorage, session.draft.scope, {
        observed: session.pointerObserved, expectedPointer: session.custody.expectedPointer,
      });
      session.pointerObserved = observation.observed; session.custody.expectedPointer = observation.expectedPointer;
      assertProductionOrigins(window.localStorage, session.draft); forkProductionWriter(session);
      if (backupProduction(session)) session.notice = "Verified a new copy of your current input. Earlier unreadable or changed bytes were retained separately.";
      notifyProduction(session);
    } catch (reason) { session.backupError = message(reason); notifyProduction(session); }
  }
  function retryBackup() {
    if (busy) return;
    try {
      if (session.recoveryProblem) {
        const observation = readProductionRecoveryPointer(window.sessionStorage, session.draft.scope, {
          observed: session.pointerObserved, expectedPointer: session.custody.expectedPointer,
        });
        // Latch a successful observation even if the following copy read fails.
        session.pointerObserved = observation.observed; session.custody.expectedPointer = observation.expectedPointer;
        const pointer = observation.expectedPointer;
        if (pointer === null) {
          // A known writer's removal was rejected above. An initially unreadable
          // empty pointer may now start custody without replacing current input.
          assertProductionOrigins(window.localStorage, session.draft);
          if (window.sessionStorage.getItem(productionPointerKey(session.draft.scope)) !== null)
            throw new Error("The Production recovery pointer changed before publication.");
        } else {
          const { key, raw } = observeProductionCopy(session.draft.scope, pointer, session);
          if (raw === null) throw new Error("The earlier recovery copy is still missing. Current input remains in this editor.");
          const recovered = retainProductionOrigin(decodeProductionCopy(raw, session.draft.scope, pointer).draft, { key, raw });
          assertProductionOrigins(window.localStorage, recovered); assertProductionOrigins(window.localStorage, session.draft);
          const next = recoverProductionAfterReadFailure(recovered, session.draft, session.editedWhileRecoveryBlocked, current.current);
          assertProductionOrigins(window.localStorage, next);
          if (window.sessionStorage.getItem(productionPointerKey(session.draft.scope)) !== pointer || window.localStorage.getItem(key) !== raw)
            throw new Error("The Production recovery source changed before adoption. Earlier input was kept.");
          session.draft = next;
        }
        // Adopt before clearing the blocker. A failed new copy retries the
        // adopted ordinary/UNKNOWN input, never the temporary placeholder.
        session.recoveryProblem = null; session.editedWhileRecoveryBlocked = false;
        forkProductionWriter(session); setReview(null);
      }
      if (backupProduction(session)) session.notice = "Current Production recovery copy confirmed. No Project change was sent.";
      notifyProduction(session);
    } catch (reason) { session.backupError = message(reason); notifyProduction(session); }
  }
  return { session, draft: session.draft, busy, conflict, review, reviewing, edit, send, choose, restore, readOutcome, continueEditing, download, discard, freshCopy,
    retry: retryBackup,
    blocked: busy || !!session.backupError || conflict || productionUnconfirmed(session.draft) };
}
type ProductionEditor = ReturnType<typeof useProductionEditor>;
function ProductionDraftFeedback({ editor, project }: { editor: ProductionEditor; project: Project }) {
  const { session, draft, busy, conflict, review } = editor, label = productionLabel(draft.target);
  const copies = session.copies.filter(copy => copy.writerId !== session.custody.writerId);
  return <section aria-label={label + " draft recovery"}>
    {session.notice && <p className="production-notice" role="status">{session.notice}</p>}
    {session.error && <p className="production-error" role="alert">{session.error}</p>}
    {session.backupError && <div className="production-notice" role="alert" aria-label={label + " backup warning"}>
      <strong>Local recovery needs attention.</strong><p>{session.backupError}</p>
      <button className="secondary-button" type="button" disabled={busy} onClick={editor.retry}><RefreshCw size={14}/> Retry {label} backup</button>
      <button className="secondary-button" type="button" disabled={busy || session.recoveryProblem !== null} onClick={editor.freshCopy}>Keep {label} input in a new copy</button>
    </div>}
    {draft.pending?.outcome === "NOT_SENT" && <p role="status">This preparation was NOT_SENT. Confirm its copy before a separate explicit save.</p>}
    {productionUnconfirmed(draft) && draft.pending && <section className="production-notice" role="region" aria-label={"Unconfirmed " + label}>
      <strong>Unconfirmed {label} submission from r{draft.pending.revision}</strong>
      <p>The original response is unconfirmed. Submitted parameters and later input are retained. Observing matching values cannot acknowledge or replay that request.</p>
      <button className="secondary-button" disabled={busy || editor.reviewing} onClick={() => window.location.reload()}>Reload saved {label}</button>
      <button className="secondary-button" disabled={busy || editor.reviewing} onClick={() => void editor.readOutcome()}>Read saved {label} and history</button>
      {review && <div aria-label={label + " outcome review"}>
        <p>Read saved r{review.project.revision} and {review.events.length} consecutive history records. {review.matchingValueRevisions.length} contain matching values; this does not identify the original request.</p>
        <details><summary>Submitted and saved parameters</summary><pre>{JSON.stringify({ submitted: draft.pending.value, saved: productionValue(review.project, draft.target) }, null, 2)}</pre></details>
        <button className="secondary-button" disabled={busy || editor.reviewing || !productionOutcomeMatches(draft, project, review)} onClick={editor.continueEditing}>Keep earlier {label} attempt and continue editing</button>
      </div>}
    </section>}
    {conflict && !productionUnconfirmed(draft) && <section className="production-notice" role="region" aria-label={label + " conflict"}>
      <strong>Review the current saved revision before applying this draft.</strong><p>Your input started at r{draft.baseRevision}; the current project is r{project.revision}.</p>
      <details><summary>Compare saved and authored parameters</summary><pre>{JSON.stringify({ base: draft.base, saved: productionValue(project, draft.target), input: draft.input }, null, 2)}</pre></details>
      <button className="secondary-button" disabled={busy} onClick={() => editor.choose("draft")}>Keep my {label} input</button>
      <button className="secondary-button" disabled={busy} onClick={() => editor.choose("saved")}>Use saved {label} values</button>
    </section>}
    {productionHasInput(draft) && !conflict && !productionUnconfirmed(draft) && <button className="text-button" disabled={busy} onClick={() => editor.choose("saved")}>Use saved {label} values</button>}
    <button className="text-button" type="button" onClick={() => editor.download()}>Download {label} draft</button>
    {(copies.length > 0 || session.limited) && <details role="region" aria-label={label + " recovery copies"}>
      <summary>Local {label} recovery copies</summary><p>Copies are scoped to this project generation, branch, and editor target. Restoring never writes a Project.</p>
      {session.limited && <p>Only the first 64 copies were inspected. No copy was removed automatically.</p>}
      {copies.map((copy, index) => <div role="group" aria-label={label + " recovery copy " + (index + 1)} key={copy.key}>
        <p>{copy.value ? new Date(copy.value.updatedAt).toLocaleString() + (productionUnconfirmed(copy.value.draft) ? " · Unconfirmed attempt" : " · Local input") : "Unreadable copy; original bytes were preserved."}</p>
        {copy.value && <details><summary>Inspect copied input</summary><pre>{JSON.stringify(copy.value.draft.input, null, 2)}</pre></details>}
        {copy.value && <button className="secondary-button" disabled={busy || session.recoveryProblem !== null || productionUnconfirmed(draft)} onClick={() => editor.restore(copy)}>Restore {label} copy {index + 1}</button>}
        <button className="text-button" type="button" onClick={() => editor.download(copy.raw)}>Download {label} copy {index + 1}</button>
        <button className="text-button" disabled={busy || session.firstRead?.key === copy.key || draft.origins?.some(origin => origin.key === copy.key)} onClick={() => editor.discard(copy)}>Discard {label} copy {index + 1}</button>
      </div>)}
    </details>}
  </section>;
}
function HeroWorkbench({ project, scene, commit, commitCamera, busy, playhead, onSeek, onOpenCanvas, displayProfile }: {
  project:Project; scene:Scene|null; displayProfile?:DeliverableProfile|null; commit:Commit; commitCamera:ProductionCommit; busy:boolean; playhead:number; onSeek:(time:number)=>void; onOpenCanvas:()=>void;
}) {
  const instance = project.production_design?.heroes.find(hero => hero.scene_id === scene?.id);
  const editor = useProductionEditor(project, { kind: "hero", sceneId: scene?.id ?? null }, commitCamera, busy);
  const draft = (editor.draft.input as Extract<ProductionInput, { kind: "hero" }>).config;
  const revisionReview = useComponentRevisionReview(project, scene?.id ?? null, instance?.id ?? null, draft, editor.draft.sequence);
  const setDraft = (config: HeroConfig) => editor.edit({ kind: "hero", config });
  const familyName = draft.layout === "split_explanation" ? "SplitExplanation" :
    draft.layout === "metric_evidence" ? "MetricEvidence" :
    draft.layout === "step_sequence" ? "StepSequence" :
    draft.layout === "contrast_comparison" ? "ContrastComparison" :
    draft.layout === "editorial_quote" ? "EditorialQuote" : "ProductHeroReveal";
  const [aspect,setAspect] = useState(aspects[0]);
  const [nodes,setNodes] = useState<CanvasNode[]>([]);
  const [error,setError] = useState<string|null>(null);
  const [previewLoading,setPreviewLoading] = useState(true);
  const [expanded,setExpanded] = useState(false);
  useEffect(() => {
    if (scene) onSeek(seconds(scene.start)+Math.min(2,Math.max(0,seconds(scene.duration)-1/30)));
    // Selection changes the study time, never the durable author buffer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene?.id,project.id,project.generation,project.active_branch]);
  useEffect(() => {
    let current = true; setPreviewLoading(true); setError(null);
    realizeProductHero(instance?.id ?? previewIdentity,draft,aspect.width,aspect.height).then(value => {
      if (current) { setNodes(value); setPreviewLoading(false); }
    }, reason => { if (current) { setNodes([]); setError(String(reason instanceof Error ? reason.message : reason)); setPreviewLoading(false); } });
    return () => { current=false; };
  }, [draft,aspect,instance?.id]);
  const localTime = scene ? Math.max(0,Math.min(seconds(scene.duration),playhead-seconds(scene.start))) : 2;
  const background = project.visual_language.palette.find(t => ["surface","background"].includes(t.name.toLowerCase()))?.value ?? "#0F1216";
  const locked = project.locks.some(lock => ["project:"+project.id,"scene:"+scene?.id].includes(lock.resource) && ["content","position","style","timing","renderer"].includes(lock.kind));
  const canApply = !!scene && scene.renderer === "motion-canvas" && seconds(scene.duration)>=2 && !editor.blocked && !error && !previewLoading && !locked && (!instance || revisionReview.ready);
  return <div className="production-split">
    <section className="production-inspector" aria-label={`${familyName} controls`}>
      <div className="production-section-title"><Layers size={16}/><div><h2>{familyName}</h2><p>Editable first-party composition · v1</p></div></div>
      <span className="production-status">CREATIVE REVIEW REQUIRED</span>
      <p className="production-help">Six persistent semantic objects, synchronized cubic entrances and a responsive composition. These families are graphic studies, not fabricated screenshots, validated statistics, measured comparisons or claims about actual software.</p>
      <label className="production-field"><span>Composition family</span><select aria-label="Component layout" value={draft.layout ?? "product_hero_reveal"} onChange={e => setDraft({...draft, layout:e.target.value as NonNullable<HeroConfig["layout"]>})}>
        <option value="product_hero_reveal">ProductHeroReveal · product statement</option>
        <option value="split_explanation">SplitExplanation · two-part explanation</option>
        <option value="contrast_comparison">ContrastComparison - authored conceptual before/after</option>
        <option value="editorial_quote">EditorialQuote - illustrative original quotation</option>
        <option value="metric_evidence">MetricEvidence · authored fact and context</option>
        <option value="step_sequence">StepSequence · one editable procedural step</option>
      </select></label>
      <ProductionDraftFeedback editor={editor} project={project}/>
      {([ ["eyebrow","Eyebrow",48], ["headline",draft.layout==="split_explanation"?"Left panel idea":draft.layout==="metric_evidence"?"Authored fact / metric":draft.layout==="step_sequence"?"Step heading":draft.layout==="contrast_comparison"?"Before: original concept":draft.layout==="editorial_quote"?"Illustrative quotation":"Headline",64], ["body",draft.layout==="split_explanation"?"Right panel narrative":draft.layout==="metric_evidence"?"Evidence context":draft.layout==="step_sequence"?"What happens at this step":draft.layout==="contrast_comparison"?"After: original concept":draft.layout==="editorial_quote"?"Original quote context":"Supporting copy",150], ["wordmark",draft.layout==="split_explanation"?"Right panel label":draft.layout==="metric_evidence"?"Evidence label":draft.layout==="step_sequence"?"Step index (e.g. 01 / 03)":"Wordmark",8] ] as const).map(([field,label,max]) =>
        <label className="production-field" key={field}><span>{label}<small>{Array.from(draft[field]).length}/{max}</small></span>
          {field === "headline" || field === "body" ? <textarea aria-label={label} rows={field==="headline"?3:4} value={draft[field]} onChange={e => setDraft({...draft,[field]:e.target.value})}/>
            : <input aria-label={label} value={draft[field]} onChange={e => setDraft({...draft,[field]:e.target.value})}/>} </label>)}
      <div className="production-color-fields">
        <label className="production-field"><span>Foreground</span><input aria-label="Hero foreground" type="color" value={draft.foreground} onChange={e => setDraft({...draft,foreground:e.target.value})}/></label>
        <label className="production-field"><span>Accent</span><input aria-label="Hero accent" type="color" value={draft.accent} onChange={e => setDraft({...draft,accent:e.target.value})}/></label>
      </div>
      <label className="production-check"><input type="checkbox" checked={draft.motion} onChange={e => setDraft({...draft,motion:e.target.checked})}/> Animate entrance</label>
      <p className="production-help">Turning motion off creates a static equivalent. All six families preserve stable editable objects and human override conflict checks across landscape, square and portrait. StepSequence is an authored step, not observed behavior; MetricEvidence is not verified data; ContrastComparison is not measured improvement; EditorialQuote is not a verified testimonial. Their disclosures cannot be treated as source authentication.</p>
      {draft.layout==="metric_evidence" && <p className="production-help">SOURCE NOT VERIFIED is protected while this component remains attached. Detaching preserves its objects but releases that semantic guard without verifying the claim.</p>}
      {error && <p className="production-error" role="alert">{error}</p>}
      {!scene && <button className="primary-button" disabled={busy} onClick={() => commit({type:"add_scene",name:"Product hero",objective:"An original, editable product reveal",duration_seconds:6})}><Plus size={14}/> Create a six-second scene</button>}
      {scene && (scene.renderer!=="motion-canvas" || seconds(scene.duration)<2) && <p className="production-error">Select a Motion Canvas scene at least two seconds long. Existing scene settings will not be replaced automatically.</p>}
      <button className="primary-button" disabled={!canApply} onClick={() => void editor.send("save")}>
        <Save size={14}/>{instance?"Update component":"Add to scene"}</button>
      {instance && <div className="production-button-row"><button className="secondary-button" onClick={onOpenCanvas}><ArrowUpRight size={14}/> Edit objects</button>
        <button className="text-button" disabled={editor.blocked || locked} onClick={() => void editor.send("detach")}>Detach, keep objects</button></div>}
    </section>
    <section className="production-viewer" aria-label="Component study viewer">
      <div className="production-viewer-toolbar"><div><strong>Composition study</strong><span>{sameValue(draft,instance?.config)?"Stored parameters":"Uncommitted parameters"} · editorial preview</span></div>
        <div className="production-button-row"><div className="production-segmented" aria-label="Preview aspect">{aspects.map(value => <button key={value.label} aria-pressed={value.label===aspect.label} onClick={() => setAspect(value)}>{value.label}</button>)}</div><button className="secondary-button" disabled={previewLoading || !!error || !scene} onClick={()=>setExpanded(true)}>Enlarge study</button></div></div>
      <div className={"production-preview-mat " + (aspect.height>aspect.width?"portrait":"")}>
        {previewLoading ? <p role="status">Preparing the editable study…</p> : <CompositionStudy nodes={nodes} width={aspect.width} height={aspect.height} time={localTime} background={background} ink={inheritedTextInk(project.visual_language.palette)} textOverrides={displayProfile?.text_overrides} label={`${familyName} ${aspect.label}, editorial frame at ${localTime.toFixed(3)} seconds`}/>}
      </div>
      <div className="production-transport"><span className="mono">{localTime.toFixed(3)} s</span>
        <input aria-label="Component playhead" type="range" min={0} max={scene?seconds(scene.duration):6} step={1/30} value={localTime} disabled={!scene} onChange={e => onSeek(seconds(scene!.start)+Number(e.target.value))}/>
        <button className="secondary-button" disabled={!scene} onClick={() => onSeek(seconds(scene!.start)+.65)}>Entrance</button>
        <button className="secondary-button" disabled={!scene} onClick={() => onSeek(seconds(scene!.start)+Math.min(2,seconds(scene!.duration)-1/30))}>Settled</button></div>
      <div className="production-evidence-note"><strong>{aspect.width} × {aspect.height} · project revision {project.revision}</strong>
        <p>This parameter study does not replace custom edits on scene objects. Use Canvas to inspect those overrides. This preview audits composition and timing intent. Use the native frame stage and Deliver for authoritative renderer frames and export verification. The editorial viewer uses a system fallback when the required font is not installed. Glyph metrics, clipping and creative approval are separate checks.</p></div>
      {instance && <ComponentRevisionReview review={revisionReview} renderScene={(reviewNodes, label) =>
        <div className="production-preview-mat"><CompositionStudy nodes={reviewNodes} width={1920} height={1080}
          time={localTime} background={background} ink={inheritedTextInk(project.visual_language.palette)} label={label}/></div>}/>}
    </section>
    {expanded && scene && <ExpandedStudy nodes={nodes} width={aspect.width} height={aspect.height} time={localTime} duration={seconds(scene.duration)} background={background} ink={inheritedTextInk(project.visual_language.palette)} textOverrides={displayProfile?.text_overrides} onSeek={time=>onSeek(seconds(scene.start)+time)} onClose={()=>setExpanded(false)}/>}
  </div>;
}

function PlanWorkbench({project,commit,commitCamera,busy,onSeek}:{project:Project;commit:Commit;commitCamera:ProductionCommit;busy:boolean;onSeek:(time:number)=>void}) {
  const stored = project.production_design?.plan;
  const editor = useProductionEditor(project, { kind: "plan" }, commitCamera, busy);
  const input = editor.draft.input as Extract<ProductionInput, { kind: "plan" }>;
  const draft = input.plan, base = editor.draft.baseRevision, reviewer = input.reviewer, note = input.note;
  const [error,setError] = useState<string|null>(null);
  const patch = (value:Partial<ProductionPlan>) => editor.edit({...input, plan:{...draft,...value,approval:null}});
  const setReviewer = (reviewer:string) => editor.edit({...input,reviewer});
  const setNote = (note:string) => editor.edit({...input,note});
  return <div className="production-plan">
    <div className="production-section-title"><Check size={18}/><div><h2>Production plan</h2><p>Narrative, evidence, reference constraints and the governing clock.</p></div></div>
    <ProductionDraftFeedback editor={editor} project={project}/>
    <div className="production-plan-grid"><label className="production-field"><span>Objective</span><textarea rows={3} value={draft.objective} onChange={e => patch({objective:e.target.value})}/></label>
      <label className="production-field"><span>Audience</span><textarea rows={3} value={draft.audience} onChange={e => patch({audience:e.target.value})}/></label></div>
    <label className="production-field"><span>Creative concept</span><textarea rows={4} value={draft.concept} onChange={e => patch({concept:e.target.value})}/></label>
    <div className="production-plan-grid"><label className="production-field"><span>Reference-derived constraints · one per line</span><textarea rows={4} value={draft.reference_constraints.join("\n")} onChange={e => patch({reference_constraints:e.target.value.split("\n")})}/></label>
      <label className="production-field"><span>Exclusions · one per line</span><textarea rows={4} value={draft.exclusions.join("\n")} onChange={e => patch({exclusions:e.target.value.split("\n")})}/></label></div>
    <label className="production-field"><span>Governing clock</span><select aria-label="Production governing clock" value={draft.clock.kind === "voice" ? draft.clock.voice_track_id : draft.clock.kind} onChange={e => {
      const selection=e.target.value;
      if (selection==="timeline") patch({clock:{kind:"timeline"}});
      else if (selection==="music") {
        const source=project.assets.find(asset=>asset.media_type.startsWith("audio/") && /^[0-9a-f]{64}$/.test(asset.content_sha256 ?? ""));
        if (!source) {setError("Import a SHA-256 verified audio asset before selecting the music clock.");return;}
        patch({clock:{kind:"music",asset_id:source.id,beats:[{num:"0",den:"1"}]}});
      } else patch({clock:{kind:"voice",voice_track_id:selection}});
    }}>
      <option value="timeline">Editorial timeline</option>
      {project.audio.voice_tracks.map(track => <option key={track.id} value={track.id}>Measured voice · {track.label}</option>)}
      <option value="music" disabled={!project.assets.some(asset=>asset.media_type.startsWith("audio/") && /^[0-9a-f]{64}$/.test(asset.content_sha256 ?? ""))}>Music · author-marked beat grid</option>
    </select></label>
    {draft.clock.kind==="music" && <MusicClockWorkbench project={project} draftClock={draft.clock} storedPlan={stored}
      draftMatchesSaved={!!stored && sameValue(cleanProductionPlan(draft),cleanProductionPlan(stored))} baseRevision={base} busy={busy || editor.busy || productionUnconfirmed(editor.draft)} onSeek={onSeek} commit={commit}
      onClockChange={clock=>patch({clock})}/>}

    <div className="production-shot-list"><h3>Shot and evidence plan</h3>{draft.shots.map((shot,i) => <div className="production-shot" key={shot.scene_id}>
      <strong>{String(i+1).padStart(2,"0")} / {project.scenes.find(s => s.id===shot.scene_id)?.name ?? "Missing scene"}</strong>
      <label className="production-field"><span>Purpose</span><textarea rows={2} value={shot.purpose} onChange={e => patch({shots:draft.shots.map((s,j)=>j===i?{...s,purpose:e.target.value}:s)})}/></label>
      <label className="production-field"><span>Evidence classification</span><select value={shot.evidence_kind} onChange={e => patch({shots:draft.shots.map((s,j)=>j===i?{...s,evidence_kind:e.target.value as NarrativeEvidenceKind}:s)})}>
        <option value="graphic_study">Graphic study · not proof of behavior</option><option value="real_capture">Real capture · source required</option><option value="licensed_footage">Licensed footage · source required</option><option value="synthetic_labeled">Synthetic · labeled</option></select></label>
      <label className="production-field"><span>Source asset</span><select value={shot.asset_ids[0] ?? ""} onChange={e => patch({shots:draft.shots.map((s,j)=>j===i?{...s,asset_ids:e.target.value?[e.target.value]:[]}:s)})}><option value="">No source attached</option>{project.assets.filter(a=>a.content_sha256).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      {!!project.brief.claims.length && <fieldset className="production-claims"><legend>Claims this shot supports</legend>{project.brief.claims.map(claim=><label key={claim.id}><input type="checkbox" checked={shot.claim_ids.includes(claim.id)} onChange={e=>patch({shots:draft.shots.map((s,j)=>j===i?{...s,claim_ids:e.target.checked?[...s.claim_ids,claim.id]:s.claim_ids.filter(id=>id!==claim.id)}:s)})}/>{claim.text}</label>)}</fieldset>}
    </div>)}</div>
    <div className="production-button-row"><button className="primary-button" disabled={editor.blocked} onClick={() => void editor.send("save")}><Save size={14}/> Save production plan</button>
      <button className="secondary-button" disabled={busy} onClick={() => patch({shots:project.scenes.map(s => draft.shots.find(shot=>shot.scene_id===s.id) ?? {scene_id:s.id,purpose:s.objective || s.name,claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"})})}>Refresh shot list</button></div>
    <section className="production-approval"><h3>Content-bound concept approval</h3><p>A creative decision only. It does not approve renderer fidelity, capture provenance, rights or technical delivery.</p>
      <div className="production-plan-grid"><label className="production-field"><span>Reviewer</span><input value={reviewer} onChange={e=>setReviewer(e.target.value)}/></label><label className="production-field"><span>Approval note</span><input value={note} onChange={e=>setNote(e.target.value)}/></label></div>
      <button className="secondary-button" disabled={editor.blocked || !reviewer.trim() || !note.trim()} onClick={() => void editor.send("approve")}>Approve this concept digest</button>
      {stored?.approval && <p className="mono">Recorded for {stored.approval.content_sha256.slice(0,16)}… by {stored.approval.reviewer}</p>}{error && <p className="production-error" role="alert">{error}</p>}
    </section>
  </div>;
}

function SourcesWorkbench({project,scene,commit,busy,sourceServices}:{project:Project;scene:Scene|null;commit:Commit;busy:boolean;sourceServices:NativeSourceServices}) {
  const [assetId,setAssetId]=useState(""); const [renderer,setRenderer]=useState("hyperframes"); const [version,setVersion]=useState("unverified");
  const asset = project.assets.find(a=>a.id===assetId);
  const capsules = project.production_design?.capsules ?? [];
  const attach = () => {
    if (!scene || !asset?.content_sha256) return;
    const capsule:NativeCapsule={id:crypto.randomUUID(),scene_id:scene.id,source_asset_id:asset.id,source_sha256:asset.content_sha256,label:asset.name,
      fidelity:{renderer,renderer_version:version,visual:"unavailable",temporal:"unavailable",structural:"native",editable:"unavailable",losses:["Source preserved as an opaque attachment. Parameter editing and renderer execution are not admitted."],evidence_sha256:null},editable_parameters:[],native_editor_hint:"Open with the original application's trusted environment"};
    return commit({type:"upsert_native_capsule",capsule});
  };
  return <div className="production-sources"><div className="production-section-title"><FileBox size={18}/><div><h2>Native source capsules</h2><p>Attach first. Preserve source bytes before considering translation or baking.</p></div></div>
    <NativeSourceWorkbench project={project} scene={scene} busy={busy} {...sourceServices}/>
    <h3>Attach an existing source asset</h3>
    <p className="production-help">Select an already imported source asset. This operation preserves its digest and attachment identity; it does not install a runtime, execute source code or claim editable interoperability.</p>
    <div className="production-plan-grid"><label className="production-field"><span>Imported source asset</span><select value={assetId} onChange={e=>setAssetId(e.target.value)}><option value="">Choose a digest-bound asset</option>{project.assets.filter(a=>a.content_sha256).map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="production-field"><span>Native renderer</span><input value={renderer} onChange={e=>setRenderer(e.target.value)}/></label><label className="production-field"><span>Source renderer version</span><input value={version} onChange={e=>setVersion(e.target.value)}/></label></div>
    <button className="primary-button" disabled={busy || !scene || !asset?.content_sha256 || !renderer.trim() || !version.trim()} onClick={attach}><Plus size={14}/> Attach source to scene</button>
    <div className="production-capsule-list">{capsules.map(c=><article key={c.id}><header><strong>{c.label}</strong><span>{c.fidelity.renderer} / {c.fidelity.renderer_version}</span></header>
      <p className="mono">sha256:{c.source_sha256}</p><dl>{(["visual","temporal","structural","editable"] as const).map(d=><div key={d}><dt>{d}</dt><dd>{c.fidelity[d]}</dd></div>)}</dl>
      {c.fidelity.losses.map(loss=><p key={loss}>{loss}</p>)}<small>Declared support metadata, not an independently verified renderer receipt.</small></article>)}</div>
    <NativePropertyFidelityWorkbench project={project} commit={commit} busy={busy}/>
  </div>;
}

export default function ProductionDesignWorkspace(props:{sourceServices:NativeSourceServices;graphServices:Pick<SemanticGraphWorkbenchProps,"readContext"|"outcomeTransport"|"refreshProject"|"openReview">;project:Project;scene:Scene|null;commit:Commit; commitCamera:(change:Change)=>Promise<Project>;busy:boolean;playhead:number;onSeek:(time:number)=>void;onSelectScene:(id:string)=>void;onOpenCanvas:()=>void;onOpenRender:()=>void;displayProfile:DeliverableProfile|null;evidence:MotionCanvasRenderEvidence|null}) {
  const [tab,setTab]=useState<"component"|"stage"|"plan"|"brand"|"sources"|"captures"|"data"|"procedural"|"camera"|"graph"|"impact"|"evaluator"|"identity"|"distillation"|"generation"|"patch"|"inspection"|"contrast">("component");
  const [repairDraft,setRepairDraft]=useState<NativeReviewRepairDraft|null>(null);
  const design=props.project.production_design ?? emptyProductionDesign();
  const sceneOptions=useMemo(()=>props.project.scenes.map(scene=><option key={scene.id} value={scene.id}>{scene.name}</option>),[props.project.scenes]);
  return <section className="production-workspace" aria-label="Creative production workstation">
    <header className="production-workspace-header"><div><span className="eyebrow">CREATIVE PRODUCTION</span><h1>Direct the work. Preserve the decisions.</h1><p>{design.heroes.length} component{design.heroes.length===1?"":"s"} · {design.capsules.length} native source{design.capsules.length===1?"":"s"} · revision {props.project.revision}</p></div>
      <label className="production-scene-select"><span>Working scene</span><select aria-label="Production working scene" value={props.scene?.id ?? ""} onChange={e=>{setRepairDraft(null);props.onSelectScene(e.target.value);}}><option value="" disabled>Select a scene</option>{sceneOptions}</select></label></header>
    <nav className="production-tabs" aria-label="Production tools">{([ ["component","Components"],["stage","ProductStage"],["camera","Camera"],["graph","Graph"],["plan","Production plan"],["brand","Brand & taste"],["sources","Native sources"],["captures","Capture sources"],["identity","Identity & consent"],["generation","Generation journal"],["data","Measured data"],["procedural","Procedural"],["impact","Render impact"],["evaluator","Evaluators"],["distillation","Distillation"],["patch","Scoped changes"],["inspection","Native inspection"],["contrast","Pixel contrast"] ] as const).map(([key,label])=><button key={key} aria-current={tab===key?"page":undefined} onClick={()=>{if(tab==="patch" && key!=="patch") setRepairDraft(null);setTab(key);}}>{label}</button>)}</nav>
    {tab==="component"?<HeroWorkbench key={productionScope(props.project,{kind:"hero",sceneId:props.scene?.id ?? null})} {...props}/>:tab==="stage"?<ProductStageWorkbench {...props} commit={props.commitCamera}/>:tab==="camera"?<CameraReframeWorkbench {...props}/>:tab==="graph"?<SemanticGraphWorkbench {...props} {...props.graphServices} commit={props.commitCamera}/>:tab==="plan"?<PlanWorkbench key={productionScope(props.project,{kind:"plan"})} {...props}/>:tab==="brand"?<BrandTasteWorkbench project={props.project} commit={props.commitCamera} busy={props.busy}/>:tab==="sources"?<SourcesWorkbench {...props}/>:tab==="captures"?<CaptureSourceWorkbench project={props.project} commit={props.commit} busy={props.busy}/>:tab==="generation"?<PaidGenerationWorkspace key={props.project.generation} project={props.project} scene={props.scene} busy={props.busy} commit={props.commit}/>:tab==="identity"?<IdentityOriginWorkbench project={props.project} commit={props.commit} busy={props.busy}/>:tab==="data"?<MeasuredDataWorkbench project={props.project} scene={props.scene} busy={props.busy} commit={props.commitCamera} onOpenCanvas={props.onOpenCanvas}/>:tab==="procedural"?<ProceduralFieldWorkbench project={props.project} scene={props.scene} commit={props.commitCamera} busy={props.busy} onOpenCanvas={props.onOpenCanvas} displayProfile={props.displayProfile}/>:tab==="impact"?<RenderTailWorkbench project={props.project}/>:tab==="evaluator"?<CreativeEvaluatorWorkbench project={props.project} scene={props.scene} commit={props.commitCamera} busy={props.busy} onOpenCanvas={props.onOpenCanvas}/>:tab==="distillation"?<ComponentDistillationWorkbench project={props.project} commit={props.commitCamera} busy={props.busy}/>:tab==="contrast"?<NativeContrastWorkbench project={props.project} scene={props.scene} displayProfile={props.displayProfile} evidence={props.evidence} busy={props.busy} onOpenRender={props.onOpenRender}/>:tab==="inspection"?<NativeInspectionWorkbench key={JSON.stringify([props.project.id,props.project.generation,props.project.active_branch])} {...props} commit={props.commitCamera} onDraftRepair={draft=>{setRepairDraft(draft);setTab("patch");}}/>:<CreativePatchWorkbench {...props} repairDraft={repairDraft} onDismissRepair={()=>setRepairDraft(null)}/>}
  </section>;
}
