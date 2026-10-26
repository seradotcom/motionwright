import { FileCheck2, RefreshCw, Save } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Change, Project } from "./types";
import type { DistillationVerdict } from "./componentDistillation";
import { sameProjectRevision } from "./projectMutation";
import { sameValue } from "./creativeProduction";
import { bootstrap, projectHistory } from "./api";
import {
  acknowledgeDistillationDraft, createDistillationDraft, decodeDistillationCopy, editDistillationDraft, encodeDistillationFile,
  inspectDistillationOutcome, observeDistillationRecoveryCopy, prepareDistillationSubmission, readDistillationCopies, readDistillationRecovery, reconcileDistillationDraft,
  releaseDistillationOutcome, resolveDistillationDraft, retainDistillationFailure, retainReviewedDistillationIntent,
  selectDistillationReview, writeDistillationCopy, distillationCompatibleNodes, distillationConflict,
  distillationHasWork, distillationLabel, distillationNegativeCase, distillationOutcomeMatches, distillationPointerKey, distillationCopyKey,
  distillationScope, distillationUnconfirmed, DISTILLATION_RECOVERY_PREFIX,
} from "./componentDistillationDraft";
import type { DistillationAuthorRaw, DistillationCustody, DistillationDraft, DistillationOutcomeReview, DistillationRaw,
  DistillationRecoveryEntry, DistillationRecoveryPointer, DistillationReviewRaw, DistillationTarget } from "./componentDistillationDraft";

type Props = { project: Project; busy: boolean; commit: (change: Change) => Promise<Project> };
type Session = { draft: DistillationDraft; custody: DistillationCustody; recoveryObservation: DistillationRecoveryPointer; inFlight: boolean;
  error: string | null; backupError: string | null; recoveryProblem: string | null; notice: string | null;
  copies: DistillationRecoveryEntry[]; limited: boolean; notify?: () => void };
// Per-tab author inputs only. These sessions neither own nor publish Projects.
const sessions = new Map<string, Session>();
const owner = (scope: string) => JSON.stringify((JSON.parse(scope) as unknown[]).slice(0, 3));
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function notify(session: Session) { for (const value of sessions.values()) if (owner(value.draft.scope) === owner(session.draft.scope)) value.notify?.(); }
function projectBusy(project: Project) { const key = JSON.stringify([project.id, project.generation, project.active_branch]); return [...sessions.values()].some(value => owner(value.draft.scope) === key && value.inFlight); }
function refreshCopies(session: Session) {
  try {
    if (session.recoveryProblem && !session.recoveryObservation.firstRead)
      observeDistillationRecoveryCopy(window.localStorage, session.draft.scope, session.recoveryObservation);
    const found = readDistillationCopies(window.localStorage, session.draft.scope); session.copies = found.entries; session.limited = found.limited;
  }
  catch (reason) { session.backupError = message(reason); }
  finally {
    const source = session.recoveryObservation.firstRead;
    if (source && source.raw !== null) {
      let value: DistillationRecoveryEntry["value"] = null;
      try { value = decodeDistillationCopy(source.raw, session.draft.scope, source.writerId); } catch { /* The original unreadable bytes stay downloadable. */ }
      session.copies = [{ ...source, raw: source.raw, value }, ...session.copies.filter(copy => copy.key !== source.key)];
    }
  }
}
function sessionFor(project: Project, target: DistillationTarget): Session {
  const scope = distillationScope(project, target), existing = sessions.get(scope); if (existing) return existing;
  let recovered: DistillationDraft | null = null, backupError: string | null = null;
  const recoveryObservation: DistillationRecoveryPointer = { observed: false, value: null };
  try {
    recoveryObservation.value = window.sessionStorage.getItem(distillationPointerKey(scope)); recoveryObservation.observed = true;
    const reload = performance.getEntriesByType("navigation").some(entry => (entry as PerformanceNavigationTiming).type === "reload");
    // A cloned/new tab gets its own author buffer and an explicit copies list.
    if (recoveryObservation.value && reload && !window.opener) {
      recovered = readDistillationRecovery(window.localStorage, window.sessionStorage,
        createDistillationDraft(project, target), project, recoveryObservation);
    }
  } catch (reason) { backupError = message(reason); }
  const session: Session = { draft: recovered ? reconcileDistillationDraft(recovered, project) : createDistillationDraft(project, target),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: recoveryObservation.value }, recoveryObservation, inFlight: false,
    error: null, backupError, recoveryProblem: backupError, notice: recovered ? "Recovered this tab's Distillation input. No saved operation was repeated." : null, copies: [], limited: false };
  sessions.set(scope, session); refreshCopies(session); return session;
}
function forkWriter(session: Session) { session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer }; }
function assertFirstRead(session: Session) {
  const source = session.recoveryObservation.firstRead;
  if (!source) return;
  if (source.raw === null || window.localStorage.getItem(source.key) !== source.raw)
    throw new Error("The original Distillation recovery bytes changed or remain missing. The first observation was kept.");
  const pointer = window.sessionStorage.getItem(distillationPointerKey(session.draft.scope));
  if (pointer !== source.writerId && !(pointer === session.custody.writerId && session.custody.expectedRaw !== null
    && window.localStorage.getItem(distillationCopyKey(session.draft.scope, pointer)) === session.custody.expectedRaw))
    throw new Error("The original Distillation recovery pointer changed. No different copy was adopted.");
  if (window.localStorage.getItem(source.key) !== source.raw)
    throw new Error("The original Distillation recovery bytes changed during confirmation.");
}
function backup(session: Session): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem + " Retry reading the earlier copy before another save; it may contain an unconfirmed attempt.");
    if (session.custody.expectedRaw !== null) {
      const before = decodeDistillationCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!distillationHasWork(before) && !sameValue(before, session.draft)) forkWriter(session);
    }
    assertFirstRead(session);
    writeDistillationCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    assertFirstRead(session);
    delete session.recoveryObservation.firstRead;
    session.backupError = null; refreshCopies(session); return session.backupError === null;
  } catch (reason) { session.backupError = message(reason) + " Current input remains in this editor. Saving requires a verified copy and pointer."; refreshCopies(session); return false; }
}
function replace(session: Session, draft: DistillationDraft, persist = true) {
  session.draft = draft; const saved = persist ? backup(session) : true; notify(session); return saved;
}
function useDraft(project: Project, target: DistillationTarget, commit: Props["commit"], externalBusy: boolean) {
  const [session] = useState(() => sessionFor(project, target));
  const [, redraw] = useState(0), [review, setReview] = useState<DistillationOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  const current = useRef(project), mounted = useRef(true), epoch = useRef(0); current.current = project;
  const busy = externalBusy || projectBusy(project), conflict = distillationConflict(session.draft, project);
  const stamp = JSON.stringify([project.id, project.generation, project.active_branch, project.revision]);
  useEffect(() => {
    mounted.current = true; const update = () => redraw(value => value + 1); session.notify = update;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(DISTILLATION_RECOVERY_PREFIX)) { refreshCopies(session); update(); } };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; epoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  useEffect(() => {
    epoch.current++; setReview(null); setReviewing(false);
    const next = reconcileDistillationDraft(session.draft, current.current); if (next !== session.draft) replace(session, next);
  }, [stamp, session]);
  function invalidateReview() { epoch.current++; setReview(null); setReviewing(false); }
  function edit(raw: DistillationRaw) { invalidateReview(); session.error = null; session.notice = null; replace(session, editDistillationDraft(session.draft, raw)); }
  function select(id: string) { session.error = null; replace(session, selectDistillationReview(session.draft, id)); }
  async function send() {
    if (externalBusy || projectBusy(current.current) || session.backupError || distillationUnconfirmed(session.draft)) return;
    const source = structuredClone(session.draft), selected = structuredClone(current.current), id = crypto.randomUUID();
    session.error = null; session.notice = null; invalidateReview();
    let prepared: ReturnType<typeof prepareDistillationSubmission>;
    try {
      prepared = prepareDistillationSubmission(source, selected, id);
      if (!mounted.current || !sameProjectRevision(selected, current.current) || source.sequence !== session.draft.sequence || !sameValue(source.raw, session.draft.raw)) throw new Error("The Distillation source changed before dispatch. Review the current input and save explicitly.");
      if (!replace(session, prepared.draft)) {
        session.draft = retainDistillationFailure(session.draft, id, "Local recovery was not confirmed before dispatch.", true);
        session.error = "NOT_SENT: verify this Distillation input's recovery before saving."; notify(session); return;
      }
    } catch (reason) { session.error = message(reason); notify(session); return; }
    session.inFlight = true; notify(session);
    try {
      const receipt = await commit(prepared.change);
      if (session.draft.pending?.id !== id) return;
      const next = acknowledgeDistillationDraft(session.draft, id, receipt);
      // The armed UNKNOWN record stays intact if post-ACK storage fails.
      forkWriter(session); session.error = null; session.notice = "Saved " + distillationLabel(target) + " at r" + receipt.revision + "."
        + (next.intent ? " Your later input remains editable." : " The exact author input remains available.");
      replace(session, next);
    } catch (reason) { session.error = message(reason); replace(session, retainDistillationFailure(session.draft, id, message(reason))); }
    finally { session.inFlight = false; notify(session); }
  }
  function choose(choice: "saved" | "draft") {
    if (busy || distillationUnconfirmed(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = resolveDistillationDraft(session.draft, current.current, choice); invalidateReview(); forkWriter(session); session.error = null;
      session.notice = "The earlier input remains in a separate copy. " + (choice === "saved" ? "Using the saved comparison values." : "Keep editing and submit any separate decision explicitly.");
      replace(session, next);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function restore(copy: DistillationRecoveryEntry) {
    if (busy || session.recoveryProblem || distillationUnconfirmed(session.draft) || !copy.value) return;
    try {
      if (distillationHasWork(session.draft) && !backup(session)) return;
      const raw = window.localStorage.getItem(copy.key); if (raw === null || raw !== copy.raw) throw new Error("This Distillation copy changed. Refresh the list first.");
      const restored = decodeDistillationCopy(raw, session.draft.scope, copy.writerId); invalidateReview(); forkWriter(session); session.error = null;
      replace(session, reconcileDistillationDraft({ ...restored.draft, intent: true }, current.current));
      session.notice = "Restored a separate editable Distillation copy. No Project change was sent."; notify(session);
    } catch (reason) { session.error = message(reason); refreshCopies(session); notify(session); }
  }
  async function readOutcome() {
    if (busy || reviewing || !distillationUnconfirmed(session.draft)) return;
    const started = ++epoch.current; setReviewing(true); setReview(null);
    try {
      const observed = await inspectDistillationOutcome(session.draft, current.current, {
        readProject: async id => { const read = await bootstrap(id); if (!read.project || read.project_selection.status !== "selected") throw new Error("The saved Distillation project is unavailable."); return read.project; },
        readHistory: projectHistory,
      });
      if (!mounted.current || started !== epoch.current) return;
      if (!distillationOutcomeMatches(session.draft, current.current, observed)) throw new Error("The selected Distillation state changed during inspection. Read it again.");
      setReview(observed);
    } catch (reason) { if (mounted.current && started === epoch.current) { session.error = message(reason); notify(session); } }
    finally { if (mounted.current && started === epoch.current) setReviewing(false); }
  }
  function continueEditing() {
    if (busy || !review) return;
    try {
      const next = releaseDistillationOutcome(session.draft, current.current, review); if (!backup(session)) return;
      session.custody = retainReviewedDistillationIntent(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      invalidateReview(); session.backupError = null; session.error = null;
      session.notice = "The unconfirmed attempt remains in a separate copy. Nothing was acknowledged or retried; submit any new decision explicitly.";
      replace(session, next, false); refreshCopies(session); notify(session);
    } catch (reason) { session.backupError = message(reason); notify(session); }
  }
  function download() {
    try {
      const raw = encodeDistillationFile(session.draft), url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-distillation-" + target.kind + "-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the complete Distillation input. This does not confirm browser backup or a Project save."; notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function discard(copy: DistillationRecoveryEntry) {
    if (busy || copy.writerId === session.custody.writerId) return;
    try {
      if (session.recoveryObservation.firstRead?.key === copy.key)
        throw new Error("The first Distillation recovery observation is still required. Download its original bytes before resolving that source.");
      if (window.sessionStorage.getItem(distillationPointerKey(session.draft.scope)) === copy.writerId && !backup(session)) return;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("This Distillation copy changed. Review it before discarding.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("Discard was not confirmed. The Distillation copy remains retained; no second removal was attempted.");
      session.error = null; session.notice = "Confirmed removal of the selected local Distillation copy."; refreshCopies(session); notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function freshCopy() {
    if (busy) return;
    try {
      if (session.recoveryProblem) throw new Error("Retry the earlier copy before creating another active writer. Its unreadable bytes may retain an unconfirmed attempt; download current input while storage is unavailable.");
      invalidateReview(); session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: window.sessionStorage.getItem(distillationPointerKey(session.draft.scope)) };
      if (backup(session)) session.notice = "Verified a new copy of your current input. Earlier bytes remain separate."; notify(session);
    } catch (reason) { session.backupError = message(reason); notify(session); }
  }
  function downloadCopy(copy: DistillationRecoveryEntry) {
    try {
      const url = URL.createObjectURL(new Blob([copy.raw], { type: "application/json" }));
      const anchor = document.createElement("a"); anchor.href = url; anchor.download = "motionwright-distillation-copy-" + copy.writerId + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the exact retained Distillation copy. No operation was repeated."; notify(session);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function retryBackup() {
    if (busy) return;
    try {
      if (session.recoveryProblem) {
        const observation = session.recoveryObservation;
        let recovered: DistillationDraft;
        try { recovered = readDistillationRecovery(window.localStorage, window.sessionStorage, session.draft, current.current, observation); }
        finally { session.custody.expectedPointer = observation.value; }
        invalidateReview(); forkWriter(session); session.draft = recovered; session.recoveryProblem = null;
      }
      backup(session); notify(session);
    } catch (reason) { session.backupError = message(reason); notify(session); }
  }
  return { session, draft: session.draft, busy, conflict, review, reviewing, edit, select, send, downloadCopy, choose, restore, readOutcome, continueEditing, download, discard, freshCopy, retryBackup };
}

function DraftFeedback({ editor }: { editor: ReturnType<typeof useDraft> }) {
  const { session, draft, busy, conflict, review, reviewing } = editor, label = distillationLabel(draft.target);
  const copies = session.copies.filter(copy => copy.writerId !== session.custody.writerId);
  return <section aria-label={label + " draft status"}>
    {session.notice && <p className="production-notice" role="status">{session.notice}</p>}
    {session.error && <p className="production-error" role="alert">{session.error}</p>}
    {session.backupError && <div className="production-error" role="alert" aria-label={label + " backup warning"}>
      <p>{session.backupError}</p>
      <button type="button" className="secondary-button" disabled={busy} onClick={editor.retryBackup}><RefreshCw size={14}/> Retry {label} backup</button>
      <button type="button" className="text-button" disabled={busy || session.recoveryProblem !== null} onClick={editor.freshCopy}>Keep current {label} input in a new copy</button>
    </div>}
    {distillationUnconfirmed(draft) && draft.pending && <section className="production-notice" role="region" aria-label={"Unconfirmed " + label}>
      <strong>The original {label} result is unconfirmed.</strong>
      <p>Observed values do not confirm who saved them. Keep the earlier comparison and read the saved project and complete history before a separate decision.</p>
      <button type="button" className="secondary-button" disabled={busy || reviewing} onClick={() => void editor.readOutcome()}>Read saved {label} and history</button>
      {review && <div aria-label={label + " outcome review"}>
        <p>Read r{review.project.revision} and {review.events.length} consecutive history records. Matching value records: {review.matchingValueRevisions.join(", ") || "none"}. This is comparison, not an acknowledgement.</p>
        <details><summary>Inspect the submitted source decision</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(draft.pending.raw, null, 2)}</pre></details>
        {review.project.revision <= draft.pending.revision && <p role="status">The original attempt can still commit on this revision. Keep it UNKNOWN and inspect again after the saved revision advances.</p>}
        <button type="button" className="secondary-button" disabled={busy || reviewing || !distillationOutcomeMatches(draft, review.project, review) || review.project.revision <= draft.pending.revision} onClick={editor.continueEditing}>Keep earlier {label} attempt and continue editing</button>
      </div>}
    </section>}
    {conflict && !distillationUnconfirmed(draft) && <section className="production-notice" aria-label={label + " revision conflict"}>
      <p>This input was prepared at r{draft.baseRevision}; the saved project has changed. Keep the old input before choosing a current base.</p>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => editor.choose("draft")}>Keep my {label} input</button>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => editor.choose("saved")}>Use saved {label} values</button>
    </section>}
    {draft.intent && !conflict && !distillationUnconfirmed(draft) && <button type="button" className="text-button" disabled={busy} onClick={() => editor.choose("saved")}>Use saved {label} values</button>}
    <button type="button" className="text-button" onClick={editor.download}>Download {label} draft</button>
    {(copies.length > 0 || session.limited) && <details role="region" aria-label={label + " recovery copies"}>
      <summary>Local {label} recovery copies</summary><p>Copies stay with their project generation, branch and proposal. Restoring sends no Project change.</p>
      {session.limited && <p>Only the first 64 copies were inspected. No copy was removed automatically.</p>}
      {copies.map((copy, index) => <div role="group" aria-label={label + " recovery copy " + (index + 1)} key={copy.key}>
        <p>{copy.value ? new Date(copy.value.updatedAt).toLocaleString() + (distillationUnconfirmed(copy.value.draft) ? " · Unconfirmed attempt" : " · Local input") : "Unreadable copy; original bytes were preserved."}</p>
        {copy.value && <details><summary>Inspect copied input</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(copy.value.draft.raw, null, 2)}</pre></details>}
        {copy.value && <button type="button" className="secondary-button" disabled={busy || !!session.recoveryProblem || distillationUnconfirmed(draft)} onClick={() => editor.restore(copy)}>Restore {label} copy {index + 1}</button>}
        <button type="button" className="text-button" onClick={() => editor.downloadCopy(copy)}>Download {label} copy {index + 1}</button>
        <button type="button" className="text-button" disabled={busy || session.recoveryObservation.firstRead?.key === copy.key} onClick={() => editor.discard(copy)}>Discard {label} copy {index + 1}</button>
      </div>)}
    </details>}
  </section>;
}

export default function ComponentDistillationWorkbench(props: Props) {
  return <DistillationOwner key={JSON.stringify([props.project.id, props.project.generation, props.project.active_branch])} {...props}/>;
}
function DistillationOwner({ project, busy, commit }: Props) {
  const editor = useDraft(project, { kind: "author" }, commit, busy), raw = editor.draft.raw as DistillationAuthorRaw;
  const proposals = project.production_design?.distillations ?? [];
  const sourceScenes = project.scenes.filter(scene => scene.renderer === "motion-canvas" && distillationCompatibleNodes(scene).length >= 2);
  const negativeScenes = project.scenes.flatMap(scene => { const negative = distillationNegativeCase(scene); return negative ? [{ scene, negative }] : []; });
  const count = /^[0-9]+$/.test(raw.rolesCount.trim()) ? Number(raw.rolesCount.trim()) : 0;
  const a = sourceScenes.find(scene => scene.id === raw.aId), b = sourceScenes.find(scene => scene.id === raw.bId), bad = negativeScenes.find(item => item.scene.id === raw.negativeId);
  const selected = a ? distillationCompatibleNodes(a).slice(0, count) : [], matches = b ? selected.map(node => distillationCompatibleNodes(b).find(candidate => candidate.kind === node.kind)) : [];
  const genuine = Number.isSafeInteger(count) && count >= 2 && count <= 6 && !!a && !!b && a.id !== b.id && !!bad && bad.scene.id !== a.id && bad.scene.id !== b.id
    && selected.length === count && matches.length === count && matches.every(Boolean) && new Set(matches.map(node => node?.id)).size === count;
  const requirements = raw.requirements.split("\n").map(value => value.trim()).filter(Boolean);
  const locked = project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
  const canPropose = genuine && !editor.busy && !locked && !editor.conflict && !editor.session.backupError && !distillationUnconfirmed(editor.draft)
    && editor.draft.intent && !!raw.label.trim() && !!raw.author.trim() && !!raw.rights.trim() && requirements.length > 0 && requirements.length <= 12;
  const set = <K extends keyof DistillationAuthorRaw>(key: K, value: DistillationAuthorRaw[K]) => editor.edit({ ...raw, [key]: value });
  const sourceOptions = (value: string, onChange: (value: string) => void, label: string) => <select aria-label={label} value={value} onChange={event => onChange(event.target.value)}>
    <option value="">Choose an authored Motion Canvas scene</option>
    {value && !sourceScenes.some(scene => scene.id === value) && <option value={value}>Earlier source is unavailable</option>}
    {sourceScenes.map(scene => <option value={scene.id} key={scene.id}>{scene.name}</option>)}
  </select>;
  const reviewId = editor.draft.selection;
  return <div className="production-split">
    <section className="production-inspector" aria-label="Component distillation proposal controls">
      <div className="production-section-title"><div><h2>Component distillation</h2><p>Evidence-bound proposals Â· no code installation</p></div></div>
      <p className="production-help">Extract source-owned semantic Canvas roles from two genuinely varied original scenes. Provide an actually rejected counterexample and an author rights declaration. Source code, unverified screenshots and arbitrary permissions are never admitted.</p>
      <h3>Source variation evidence</h3>
      <label className="production-field"><span>Positive scene A</span>{sourceOptions(raw.aId, value => set("aId", value), "Distillation source A")}</label>
      <label className="production-field"><span>Positive scene B</span>{sourceOptions(raw.bId, value => set("bId", value), "Distillation source B")}</label>
      <label className="production-field"><span>Semantic roles</span><select aria-label="Distillation role count" value={raw.rolesCount} onChange={event => set("rolesCount", event.target.value)}>
        {!["2", "3", "4", "5", "6"].includes(raw.rolesCount) && <option value={raw.rolesCount}>Retained role input: {raw.rolesCount || "(empty)"}</option>}
        {[2, 3, 4, 5, 6].map(value => <option key={value} value={String(value)}>{value} original Canvas objects</option>)}
      </select></label>
      <p className="production-help">{genuine ? "Matching editable source roles: " + selected.map(node => node.kind).join(", ") : "Two separate source scenes need matching authored semantic objects."}</p>
      <h3>Observed negative evidence</h3>
      <label className="production-field"><span>Rejected scene</span><select aria-label="Distillation negative scene" value={raw.negativeId} onChange={event => set("negativeId", event.target.value)}>
        <option value="">Choose actually incompatible or locked source</option>
        {raw.negativeId && !negativeScenes.some(item => item.scene.id === raw.negativeId && item.scene.id !== raw.aId && item.scene.id !== raw.bId) && <option value={raw.negativeId}>Earlier counterevidence needs review</option>}
        {negativeScenes.filter(item => item.scene.id !== raw.aId && item.scene.id !== raw.bId).map(item => <option value={item.scene.id} key={item.scene.id}>{item.scene.name} Â· {item.negative.rejection}</option>)}
      </select></label>
      {!negativeScenes.length && <p role="status" className="production-help">No negative case exists. Keep a rejected or locked source scene as real counterevidence.</p>}
      <h3>Original author constraints</h3>
      <label className="production-field"><span>Candidate name</span><input aria-label="Distillation label" maxLength={120} value={raw.label} onChange={event => set("label", event.target.value)}/></label>
      <label className="production-field"><span>Original author</span><input aria-label="Distillation author" maxLength={120} value={raw.author} onChange={event => set("author", event.target.value)}/></label>
      <label className="production-field"><span>Declaration of original-content rights</span><textarea aria-label="Distillation rights" rows={2} maxLength={480} value={raw.rights} onChange={event => set("rights", event.target.value)}/></label>
      <label className="production-field"><span>Versioned requirements (one per line)</span><textarea aria-label="Distillation requirements" rows={3} value={raw.requirements} onChange={event => set("requirements", event.target.value)}/></label>
      <button type="button" className="primary-button" disabled={!canPropose} onClick={() => void editor.send()}><Save size={14}/> Propose original component</button>
      <DraftFeedback editor={editor}/>
      <h3>Separate human compatibility review</h3>
      <label className="production-field"><span>Source proposal</span><select aria-label="Distillation review candidate" value={reviewId} onChange={event => editor.select(event.target.value)}>
        <option value="">Choose an immutable source proposal</option>
        {reviewId && !proposals.some(candidate => candidate.input.id === reviewId) && <option value={reviewId}>Earlier source proposal is unavailable</option>}
        {proposals.map(candidate => <option key={candidate.input.id} value={candidate.input.id}>{candidate.input.label} Â· revision {candidate.input.source_revision}{candidate.review ? " · immutable review recorded" : ""}</option>)}
      </select></label>
      <ReviewControls key={distillationScope(project, { kind: "review", proposalId: reviewId || null })} project={project} busy={busy} commit={commit} proposalId={reviewId || null}/>
    </section>
    <section className="production-viewer" aria-label="Distillation source evidence">
      <div className="production-viewer-toolbar"><div>
        <strong>Frozen proposal registry</strong>
        <span>{proposals.length} source proposals Â· no automatic installation</span>
      </div></div>
      <div className="production-capsule-list">
        {proposals.map(candidate=><article key={candidate.input.id}>
          <header><strong>{candidate.input.label}</strong><span>
            {candidate.review?candidate.review.verdict==="compatible_proposal"?
              "Reviewed proposal / not admitted":"Rejected":"Pending independent review"}
          </span></header>
          <p>{candidate.samples.length} frozen positive source variations Â·
            {candidate.input.negatives.length} observed negative sample(s) Â·
            {candidate.input.roles.length} original roles</p>
          <p className="mono">Source revision {candidate.input.source_revision} Â·
            generation {candidate.input.source_generation}</p>
          <p>{candidate.input.requirements.join(" Â· ")}</p>
          {candidate.review && <p>{candidate.review.reviewer}: {candidate.review.rationale}</p>}
        </article>)}
        {!proposals.length && <p className="production-help">
          No proposals exist. Compatible variations, observed rejection and a rights
          declaration are necessary to create a record.</p>}
      </div>
      <div className="production-evidence-note"><strong>Proposal â‰  admission</strong>
        <p>Reviews never install code, expand SDK permissions, certify IP rights
          or promote this candidate to the executable component registry.</p></div>
    </section>
  </div>;
}

function ReviewControls({ project, busy, commit, proposalId }: Props & { proposalId: string | null }) {
  const target: DistillationTarget = { kind: "review", proposalId }, editor = useDraft(project, target, commit, busy), raw = editor.draft.raw as DistillationReviewRaw;
  const candidate = project.production_design?.distillations?.find(value => value.input.id === proposalId);
  const locked = project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");
  const canReview = !!candidate && !candidate.review && !editor.busy && !locked && !editor.conflict && !editor.session.backupError && !distillationUnconfirmed(editor.draft)
    && editor.draft.intent && !!raw.reviewer.trim() && !!raw.note.trim() && raw.reviewer.trim().toLowerCase() !== candidate.input.author.toLowerCase();
  const set = <K extends keyof DistillationReviewRaw>(key: K, value: DistillationReviewRaw[K]) => editor.edit({ ...raw, [key]: value });
  return <section aria-label="Selected proposal review controls">
    <label className="production-field"><span>Independent reviewer</span><input aria-label="Distillation reviewer" maxLength={120} value={raw.reviewer} onChange={event => set("reviewer", event.target.value)}/></label>
    <label className="production-field"><span>Verdict</span><select aria-label="Distillation verdict" value={raw.verdict} onChange={event => set("verdict", event.target.value as DistillationVerdict)}>
      <option value="compatible_proposal">Compatible proposal Â· not admitted</option><option value="rejected">Reject source candidate</option>
    </select></label>
    <label className="production-field"><span>Review rationale</span><textarea aria-label="Distillation review rationale" rows={2} maxLength={1600} value={raw.note} onChange={event => set("note", event.target.value)}/></label>
    {candidate?.review && <p className="production-help" role="status">This source proposal already has an immutable review. Later notes stay editable here and can be downloaded; they do not replace the recorded review.</p>}
    <button type="button" className="secondary-button" disabled={!canReview} onClick={() => void editor.send()}><FileCheck2 size={14}/> Record review only</button>
    <DraftFeedback editor={editor}/>
  </section>;
}
