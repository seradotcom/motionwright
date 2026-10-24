import NarrativeEditingPanel from "./NarrativeEditingPanel";
import { Type } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import {
  acknowledgeNarrativeDraft, createNarrativeDraft, decodeNarrativeCopy, decodeNarrativeFile, editNarrativeDraft,
  encodeNarrativeFile, importNarrativeFileDraft, inspectNarrativeOutcome, MAX_NARRATIVE_FILE_BYTES,
  narrativeConflict, narrativeHasInput, narrativeHasWork,
  narrativeLabel, narrativeOutcomeMatches, narrativePointerKey, narrativeScope, narrativeUnconfirmed,
  narrativeValue, NARRATIVE_RECOVERY_PREFIX, prepareNarrativeSubmission, proposedNarrativeValue,
  readNarrativeCopies, reconcileNarrativeDraft, releaseNarrativeOutcome, resolveNarrativeDraft,
  retainNarrativeFailure, retainReviewedNarrativeIntent, writeNarrativeCopy,
} from "./narrativeDraft";
import type { NarrativeCustody, NarrativeDraft, NarrativeFile, NarrativeOutcomeReview, NarrativeRecoveryEntry, NarrativeTarget } from "./narrativeDraft";
import {
  assertNarrativeRecoverySource, emptyNarrativeRecoveryObservation, originalNarrativeRecoveryEntry,
  readNarrativeRecoverySource, recoverNarrativeSource, rememberNarrativeRecoveryEntry,
} from "./narrativeRecovery";
import type { NarrativeRecoveryObservation } from "./narrativeRecovery";
import type { Change, Project, Scene } from "./types";

// App supplies the existing original-response coordinator (the same callback
// used by Camera). A swallowed error or a Project observation is not a receipt.
type Props = { project: Project; scene: Scene | null; commit: (change: Change) => Promise<Project> };
type Session = {
  draft: NarrativeDraft;
  custody: NarrativeCustody;
  inFlight: boolean;
  backupError: string | null;
  recoveryProblem: string | null;
  recoveryObservation: NarrativeRecoveryObservation;
  recoveryOriginPending: boolean;
  error: string | null;
  notice: string | null;
  copies: NarrativeRecoveryEntry[];
  limited: boolean;
  fileCandidate?: { name: string; file: NarrativeFile };
  notify?: () => void;
};
// Editor buffers survive workspace navigation, including a storage outage.
// Only the service owns saved Project state; these entries never replay a save.
const sessions = new Map<string, Session>();
const projectPart = (scope: string) => JSON.stringify((JSON.parse(scope) as unknown[]).slice(0, 3));
function notify(session: Session) {
  const owner = projectPart(session.draft.scope);
  for (const value of sessions.values()) if (projectPart(value.draft.scope) === owner) value.notify?.();
}
function projectBusy(project: Project): boolean {
  const owner = JSON.stringify([project.id, project.generation, project.active_branch]);
  return [...sessions.values()].some(session => session.inFlight && projectPart(session.draft.scope) === owner);
}
function refreshCopies(session: Session) {
  try {
    const copies = readNarrativeCopies(window.localStorage, session.draft.scope, copy => {
      if (session.recoveryOriginPending) rememberNarrativeRecoveryEntry(session.recoveryObservation, copy);
    });
    session.copies = copies.entries; session.limited = copies.limited;
  } catch (reason) {
    session.backupError = reason instanceof Error ? reason.message : String(reason);
  } finally {
    if (session.recoveryOriginPending) {
      const original = originalNarrativeRecoveryEntry(session.draft.scope, session.recoveryObservation);
      if (original) session.copies = [original, ...session.copies.filter(copy => copy.key !== original.key)];
    }
  }
}
function getSession(project: Project, target: NarrativeTarget): Session {
  const scope = narrativeScope(project, target), cached = sessions.get(scope);
  if (cached) return cached;
  let recovered: NarrativeDraft | null = null, backupError: string | null = null;
  const recoveryObservation = emptyNarrativeRecoveryObservation();
  try {
    recovered = readNarrativeRecoverySource(window.localStorage, window.sessionStorage, scope, recoveryObservation);
  } catch (reason) { backupError = reason instanceof Error ? reason.message : String(reason); }
  const session: Session = {
    draft: recovered ? reconcileNarrativeDraft(recovered, project) : createNarrativeDraft(project, target),
    // Fork every new page runtime. Cloned tabs must never share an editable key.
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: recoveryObservation.pointer },
    inFlight: false, backupError, recoveryProblem: backupError, recoveryObservation,
    recoveryOriginPending: true, error: null,
    notice: recovered ? "Recovered this tab's Narrative input. Compare it with the saved project before committing." : null,
    copies: [], limited: false,
  };
  refreshCopies(session); sessions.set(scope, session);
  return session;
}
function forkWriter(session: Session) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function backup(session: Session): boolean {
  try {
    if (session.recoveryProblem)
      throw new Error(session.recoveryProblem + " Retry the earlier recovery read before replacing this tab's copy.");
    if (session.recoveryOriginPending)
      assertNarrativeRecoverySource(window.localStorage, window.sessionStorage, session.draft.scope,
        session.recoveryObservation, session.custody);
    const raw = session.custody.expectedRaw;
    if (raw !== null) {
      const previous = decodeNarrativeCopy(raw, session.draft.scope, session.custody.writerId).draft;
      if (!narrativeHasWork(previous) && JSON.stringify(previous) !== JSON.stringify(session.draft)) forkWriter(session);
    }
    writeNarrativeCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    if (session.recoveryOriginPending)
      assertNarrativeRecoverySource(window.localStorage, window.sessionStorage, session.draft.scope,
        session.recoveryObservation, session.custody);
    session.recoveryOriginPending = false;
    session.backupError = null;
    refreshCopies(session);
    return session.backupError === null;
  } catch (reason) {
    session.backupError = (reason instanceof Error ? reason.message : String(reason))
      + " Your current input remains in this editor; the recovery copy or its writer pointer is unconfirmed. Earlier copies were kept.";
    refreshCopies(session);
    return false;
  }
}
function replace(session: Session, draft: NarrativeDraft, persist = true) {
  session.draft = draft;
  const confirmed = persist ? backup(session) : true;
  notify(session);
  return confirmed;
}

export default function NarrativeWorkspace({ project, scene, commit }: Props) {
  const selected = scene && project.scenes.find(candidate => candidate.id === scene.id);
  if (!selected) return <div className="workspace-scroll narrative-view">
    <header className="workspace-heading"><div><h2>Narrative</h2><p>Editorial intent is available before selecting or creating a scene.</p></div></header>
    <NarrativeEditingPanel project={project} commit={commit} />
    <div className="empty-workspace"><Type size={24} /><strong>Select a scene for its objective</strong></div>
  </div>;
  const premise: NarrativeTarget = { kind: "premise" };
  const objective: NarrativeTarget = { kind: "objective", sceneId: selected.id };
  return <div className="workspace-scroll narrative-view">
    <header className="workspace-heading"><div><h2>Narrative</h2>
      <p>The project premise and selected-scene objective remain semantic intent, independent of renderer code.</p></div>
      <span className={"status-pill status-" + selected.status}>{selected.status.replace("_", " ").toUpperCase()}</span></header>
    <div className="narrative-columns">
      <NarrativeEditor key={narrativeScope(project, premise)} project={project} target={premise} commit={commit} />
      <NarrativeEditor key={narrativeScope(project, objective)} project={project} target={objective} commit={commit} />
    </div>
    <NarrativeEditingPanel project={project} commit={commit} />
  </div>;
}

function NarrativeEditor({ project, target, commit }: Omit<Props, "scene"> & { target: NarrativeTarget }) {
  const [session] = useState(() => getSession(project, target));
  const [, redraw] = useState(0);
  const projectRef = useRef(project), mounted = useRef(true);
  projectRef.current = project;
  const [review, setReview] = useState<NarrativeOutcomeReview | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [readingFile, setReadingFile] = useState(false);
  const fileReadEpoch = useRef(0);
  const label = narrativeLabel(target), short = target.kind === "premise" ? "premise" : "objective";
  const { draft } = session;
  const busy = projectBusy(project);
  const conflict = narrativeConflict(draft, project);
  const saved = narrativeValue(project, target);
  const locked = project.locks.some(lock => lock.kind === "content" && (lock.resource === "project:" + project.id
    || (target.kind === "objective" && lock.resource === "scene:" + target.sceneId)));
  const blocked = busy || locked || session.backupError !== null || session.recoveryProblem !== null || narrativeUnconfirmed(draft) || conflict
    || proposedNarrativeValue(draft) === saved || (target.kind === "objective" && !draft.raw.trim());

  useEffect(() => {
    mounted.current = true;
    const update = () => redraw(value => value + 1);
    session.notify = update;
    const storageChanged = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith(NARRATIVE_RECOVERY_PREFIX)) { refreshCopies(session); update(); }
    };
    window.addEventListener("storage", storageChanged);
    return () => {
      mounted.current = false;
      fileReadEpoch.current++;
      if (session.notify === update) delete session.notify;
      window.removeEventListener("storage", storageChanged);
    };
  }, [session]);
  useEffect(() => {
    // This temporary buffer may contain a later author decision, even when
    // it equals its old base. Resolve the unread origin before reconciling it.
    if (session.recoveryProblem) return;
    const next = reconcileNarrativeDraft(session.draft, project);
    if (next !== session.draft) replace(session, next);
  }, [project, session]);

  async function save() {
    const current = projectRef.current;
    if (projectBusy(current) || locked || session.backupError || session.recoveryProblem || narrativeUnconfirmed(session.draft)) return;
    let change: ReturnType<typeof prepareNarrativeSubmission>["change"];
    const id = crypto.randomUUID();
    try {
      const prepared = prepareNarrativeSubmission(session.draft, current, id);
      change = prepared.change;
      session.error = null; session.notice = null; setReview(null);
      if (!replace(session, prepared.draft)) {
        // No coordinator call, grant, or Project write has been attempted.
        replace(session, retainNarrativeFailure(session.draft, id,
          session.backupError ?? "Local Narrative recovery was not confirmed.", true), false);
        session.error = "NOT_SENT: confirm the local recovery copy and its writer pointer before saving.";
        notify(session); return;
      }
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); return; }
    session.inFlight = true; notify(session);
    try {
      const receipt = await commit(change);
      if (session.draft.pending?.id !== id) return;
      const acknowledged = acknowledgeNarrativeDraft(session.draft, id, receipt);
      // Keep the original armed record intact even if the confirmation copy
      // or pointer cannot be written after the real Project save succeeded.
      forkWriter(session);
      session.error = null;
      session.notice = "Saved " + short + " at revision r" + receipt.revision + "."
        + (narrativeHasInput(acknowledged) ? " Your later typing is still available." : "");
      replace(session, acknowledged);
    } catch (reason) {
      const diagnostic = reason instanceof Error ? reason.message : String(reason);
      session.error = diagnostic;
      replace(session, retainNarrativeFailure(session.draft, id, diagnostic));
    } finally { session.inFlight = false; notify(session); }
  }
  async function readOutcome() {
    if (busy || reviewing || !narrativeUnconfirmed(session.draft)) return;
    setReviewing(true); setReview(null); session.error = null; notify(session);
    try {
      const observed = await inspectNarrativeOutcome(session.draft, projectRef.current, {
        readProject: async id => {
          const result = await bootstrap(id);
          if (!result.project || result.project_selection.status !== "selected") throw new Error("The saved Narrative project is unavailable.");
          return result.project;
        },
        readHistory: projectHistory,
      });
      if (!mounted.current) return;
      if (!narrativeOutcomeMatches(session.draft, projectRef.current, observed))
        throw new Error("The selected Narrative state changed during inspection. Read its current history again.");
      setReview(observed);
    } catch (reason) {
      if (mounted.current) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); }
    } finally { if (mounted.current) setReviewing(false); }
  }
  function continueAfterReview() {
    if (busy || !review) return;
    try {
      const next = releaseNarrativeOutcome(session.draft, projectRef.current, review);
      if (!backup(session)) throw new Error(session.backupError ?? "Retain the earlier Narrative copy before continuing.");
      const custody = retainReviewedNarrativeIntent(window.localStorage, window.sessionStorage,
        session.draft, next, session.custody, crypto.randomUUID());
      session.custody = custody; session.backupError = null; session.error = null;
      session.notice = "The earlier unconfirmed attempt remains in a separate recovery copy. Nothing was retried or acknowledged; save a new intent explicitly.";
      replace(session, next, false); setReview(null); refreshCopies(session); notify(session);
    } catch (reason) {
      session.backupError = reason instanceof Error ? reason.message : String(reason);
      session.error = session.backupError + " The unconfirmed input remains retained. Reload to recover the verified local copy if its pointer changed.";
      notify(session);
    }
  }
  function choose(choice: "saved" | "draft") {
    if (busy || narrativeUnconfirmed(session.draft)) return;
    try {
      if (!backup(session)) return;
      const next = resolveNarrativeDraft(session.draft, projectRef.current, choice);
      forkWriter(session); session.error = null;
      session.notice = choice === "saved" ? "Kept the saved " + short + ". Your earlier input remains in a recovery copy."
        : "Kept your " + short + " draft against the current saved revision. Commit it explicitly when ready.";
      replace(session, next); setReview(null);
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); }
  }
  function restore(copy: NarrativeRecoveryEntry) {
    if (busy || narrativeUnconfirmed(session.draft) || !copy.value) return;
    try {
      if (session.recoveryProblem) throw new Error("Retry the earlier Narrative recovery read before restoring another copy.");
      if (narrativeHasWork(session.draft) && !backup(session)) return;
      const raw = window.localStorage.getItem(copy.key);
      if (raw !== copy.raw) throw new Error("This Narrative copy changed. Read the current recovery list again.");
      const restored = decodeNarrativeCopy(raw, session.draft.scope, copy.writerId);
      forkWriter(session); session.error = null; session.notice = "Restored a separate local " + short + " copy. No Project change was sent.";
      // Explicitly choosing an old checkpoint is new author intent, even when
      // that copy was clean. Keep any pending UNKNOWN comparison unchanged.
      replace(session, reconcileNarrativeDraft({ ...restored.draft, intent: true }, projectRef.current)); setReview(null);
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); refreshCopies(session); notify(session); }
  }
  function discardCopy(copy: NarrativeRecoveryEntry) {
    if (busy || copy.writerId === session.custody.writerId) return;
    try {
      if (session.recoveryOriginPending && session.recoveryObservation.pointer === copy.writerId)
        throw new Error("The earlier Narrative copy may contain an unconfirmed operation. Read and retain it before discarding it.");
      if (window.sessionStorage.getItem(narrativePointerKey(session.draft.scope)) === copy.writerId && !backup(session)) return;
      if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The local Narrative copy changed; review it before discarding.");
      window.localStorage.removeItem(copy.key);
      if (window.localStorage.getItem(copy.key) !== null) throw new Error("The Narrative copy removal was not confirmed. Nothing else was removed.");
      refreshCopies(session); notify(session);
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); }
  }
  function retryBackup() {
    if (busy) return;
    try {
      if (session.recoveryProblem) {
        const recovered = readNarrativeRecoverySource(window.localStorage, window.sessionStorage,
          session.draft.scope, session.recoveryObservation);
        const next = recoverNarrativeSource(recovered, session.draft, projectRef.current);
        assertNarrativeRecoverySource(window.localStorage, window.sessionStorage,
          session.draft.scope, session.recoveryObservation);
        session.draft = next;
        session.custody.expectedPointer = session.recoveryObservation.pointer;
        session.recoveryProblem = null;
        session.error = null; setReview(null);
        session.notice = "Read the earlier Narrative copy and kept your later text. No operation was acknowledged or repeated.";
      }
      backup(session); notify(session);
    } catch (reason) {
      session.backupError = reason instanceof Error ? reason.message : String(reason);
      refreshCopies(session); notify(session);
    }
  }
  function downloadCopy(copy: NarrativeRecoveryEntry) {
    try {
      const raw = JSON.stringify({ schema: "motionwright.local-input-copy/1", storage_key: copy.key, raw: copy.raw }, null, 2);
      const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-narrative-original-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the exact earlier Narrative bytes. This does not confirm a saved Project or acknowledge its operation.";
      notify(session);
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); }
  }
  function downloadCurrent() {
    try {
      const raw = encodeNarrativeFile(session.draft);
      const url = URL.createObjectURL(new Blob([raw], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "motionwright-narrative-" + short + "-" + Date.now() + ".json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 0);
      session.notice = "Started downloading the current " + short + " input. Keep the file in a location you control; this does not confirm a browser backup or a Project save."
        + (new TextEncoder().encode(raw).length > MAX_NARRATIVE_FILE_BYTES
          ? " Its original text is retained in JSON, but this file exceeds the 8 MiB import limit." : "");
      notify(session);
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); }
  }
  async function readFile(file: File) {
    const epoch = ++fileReadEpoch.current;
    setReadingFile(true); session.error = null; notify(session);
    try {
      if (file.size > MAX_NARRATIVE_FILE_BYTES) throw new Error("The Narrative file exceeds the 8 MiB import limit.");
      const raw = await file.text();
      const candidate = decodeNarrativeFile(raw, session.draft.scope);
      if (!mounted.current || epoch !== fileReadEpoch.current) return;
      if (narrativeScope(projectRef.current, candidate.draft.target) !== session.draft.scope)
        throw new Error("The selected project or scene changed while the Narrative file was read.");
      session.fileCandidate = { name: file.name, file: candidate };
      session.notice = "The Narrative file is ready for review. Your current input is unchanged; using this copy requires a separate action.";
      notify(session);
    } catch (reason) {
      if (mounted.current && epoch === fileReadEpoch.current) {
        session.error = (reason instanceof Error ? reason.message : String(reason))
          + " Your active input and any earlier valid candidate were kept.";
        notify(session);
      }
    } finally { if (mounted.current && epoch === fileReadEpoch.current) setReadingFile(false); }
  }
  function useFileCandidate() {
    const candidate = session.fileCandidate;
    if (!candidate || projectBusy(projectRef.current) || narrativeUnconfirmed(session.draft)) return;
    try {
      if (session.recoveryProblem) throw new Error("Retry the earlier Narrative recovery read before using an imported copy.");
      // Validate again before touching local custody. An import never clears an
      // unconfirmed operation and never uses a downloaded file as write authority.
      const imported = importNarrativeFileDraft(candidate.file, projectRef.current);
      if (narrativeHasWork(session.draft) && !backup(session)) {
        session.error = "The current Narrative input could not be preserved. Both inputs remain available. Download the current draft to keep an external copy. Restore browser storage and confirm its backup before replacing it.";
        notify(session); return;
      }
      forkWriter(session); session.error = null;
      session.notice = "Using the imported " + short + " as editable input. The earlier input was kept; no Project change was sent."
        + (narrativeUnconfirmed(imported) ? " Its unconfirmed submission still requires a fresh saved-project and history review." : "");
      replace(session, imported);
      delete session.fileCandidate; setReview(null); notify(session);
    } catch (reason) { session.error = reason instanceof Error ? reason.message : String(reason); notify(session); }
  }
  const earlierCopies = session.copies.filter(copy => copy.writerId !== session.custody.writerId);
  return <section className="narrative-sheet" aria-label={label + " editor"}>
    <span className="sheet-label">{target.kind === "premise" ? "Project premise" : "Selected scene objective"}</span>
    {target.kind === "objective" && <h3>{project.scenes.find(scene => scene.id === target.sceneId)?.name}</h3>}
    <textarea aria-label={label} rows={8} value={draft.raw} aria-invalid={conflict}
      onChange={event => { session.error = null; session.notice = null; setReview(null);
        replace(session, editNarrativeDraft(session.draft, event.target.value)); }} />
    <div className="sheet-actions"><span>{target.kind === "premise" ? project.narrative.beats.length + " narrative beats"
      : new TextEncoder().encode(proposedNarrativeValue(draft)).length + " / 4000 bytes"}</span>
      <button type="button" className="button button-primary" disabled={blocked} onClick={() => void save()}>Commit {short}</button></div>
    {session.notice && <p className="production-notice" role="status">{session.notice}</p>}
    {session.error && <p className="production-error" role="alert">{session.error}</p>}
    <section className="production-notice" aria-label={label + " file recovery"}
      style={{ display: "grid", gap: 8, marginTop: 16 }}>
      <strong>Draft file</strong>
      <p style={{ margin: 0 }}>Download your current text even if local recovery is unavailable. Import a copy to review before using it.</p>
      <button type="button" className="button" style={{ justifySelf: "start" }}
        onClick={downloadCurrent}>Download {label} draft</button>
      <label className="field" style={{ display: "grid", gap: 6 }}><span>Import {label} recovery file · JSON, up to 8 MiB</span>
        <input type="file" accept="application/json,.json" aria-label={"Import " + label + " recovery file"}
          onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; if (file) void readFile(file); }} /></label>
      {readingFile && <p role="status">Reading the Narrative file. Current input remains unchanged.</p>}
      {session.fileCandidate && <section role="region" aria-label={label + " imported candidate"}>
        <p>Selected file: {session.fileCandidate.name}</p>
        <dl><dt>Imported input</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{session.fileCandidate.file.draft.raw || "(empty)"}</dd>
          <dt>File base revision</dt><dd>r{session.fileCandidate.file.draft.baseRevision}</dd></dl>
        {narrativeUnconfirmed(session.fileCandidate.file.draft) && <p>The file retains an unconfirmed submission. Importing cannot acknowledge or resend it.</p>}
        <button type="button" className="button compact" disabled={busy || readingFile || narrativeUnconfirmed(draft)}
          onClick={useFileCandidate}>Use imported {label} copy</button>
        <button type="button" className="button compact" onClick={() => {
          fileReadEpoch.current++; setReadingFile(false); delete session.fileCandidate; notify(session);
        }}>Discard imported {label} candidate</button>
      </section>}
    </section>
    {locked && <p role="status">Content is locked. You can keep drafting; saving requires the existing lock to be released.</p>}
    {session.backupError && <section className="production-notice" role="alert" aria-label={label + " backup warning"}>
      <strong>Local recovery needs attention.</strong><p>{session.backupError}</p>
      <button type="button" className="button compact" disabled={busy} onClick={retryBackup}>Retry {short} backup</button></section>}
    {draft.pending?.outcome === "NOT_SENT" && <p role="status">This {short} preparation was not sent. Confirm its backup before a separate explicit save.</p>}
    {narrativeUnconfirmed(draft) && draft.pending && <section className="production-notice" role="region" aria-label={"Unconfirmed " + short + " submission"}>
      <strong>Unconfirmed {short} change from r{draft.pending.revision}</strong>
      <p>The original result is unconfirmed. The saved project may contain the change. Your submitted value and later input are retained; nothing is replayed automatically.</p>
      {draft.pending.diagnostic && <p>{draft.pending.diagnostic}</p>}
      <button type="button" className="button compact" disabled={busy || reviewing} onClick={() => window.location.reload()}>Reload to inspect saved {short}</button>
      <button type="button" className="button compact" disabled={busy || reviewing} onClick={() => void readOutcome()}>Read saved {short} and history</button>
      {review && <section aria-label={"Saved " + short + " outcome review"}>
        <p>Read saved revision r{review.project.revision} and {review.events.length} consecutive history records.</p>
        <p>{review.matchingValueRevisions.length ? "History contains matching values at " + review.matchingValueRevisions.map(value => "r" + value).join(", ") + "."
          : "No matching change values occur in the inspected history."} This observation does not identify or acknowledge the earlier request.</p>
        <dl><dt>Submitted</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{draft.pending.value || "(empty)"}</dd>
          <dt>Saved now</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{narrativeValue(review.project, target) || "(empty)"}</dd></dl>
        <details><summary>Inspected revision history</summary><ol>{review.events.map(event => <li key={event.revision}>r{event.revision} · {event.change.type} · {event.created_at}</li>)}</ol></details>
        <button type="button" className="button compact" disabled={busy || reviewing || !narrativeOutcomeMatches(draft, project, review)}
          onClick={continueAfterReview}>Keep earlier {short} attempt and continue editing</button>
      </section>}
    </section>}
    {conflict && <section className="production-notice" role="region" aria-label={label + " conflict"}>
      <strong>Review overlapping {short} changes</strong><p>Your input was kept. Choose the saved value or keep your draft against this revision.</p>
      {draft.baseRevision > project.revision && <p>This recovery came from a later revision. Review the current project before choosing a new base.</p>}
      <dl><dt>Draft started from</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{draft.base || "(empty)"}</dd>
        <dt>Saved now · r{project.revision}</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{saved || "(empty)"}</dd>
        <dt>Your draft</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{draft.raw || "(empty)"}</dd></dl>
      <button type="button" className="button compact" disabled={busy || narrativeUnconfirmed(draft)} onClick={() => choose("saved")}>Keep saved {label}</button>
      <button type="button" className="button compact" disabled={busy || narrativeUnconfirmed(draft)} onClick={() => choose("draft")}>Keep my draft {label}</button>
    </section>}
    {(earlierCopies.length > 0 || session.limited) && <details role="region" aria-label={label + " recovery"}>
      <summary>Local {short} recovery copies</summary><p>These copies belong to this project generation, branch, and field. Restoring a copy never commits it.</p>
      {session.limited && <p role="status">Only the first 64 copies were inspected. Nothing was removed automatically.</p>}
      {earlierCopies.map((copy, index) => <div role="group" aria-label={label + " recovery copy " + (index + 1)} key={copy.key}>
        <p>{copy.value ? new Date(copy.value.updatedAt).toLocaleString() + (narrativeUnconfirmed(copy.value.draft) ? " · Unconfirmed earlier save" : " · Local input")
          : "Unreadable copy; original bytes were preserved."}</p>
        {copy.value && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{copy.value.draft.raw.slice(0, 240)}</pre>}
        {copy.value && <button type="button" className="button compact" disabled={busy || narrativeUnconfirmed(draft)}
          aria-label={"Restore " + label + " copy " + (index + 1)} onClick={() => restore(copy)}>Restore copy</button>}
        <button type="button" className="button compact" aria-label={"Download " + label + " copy " + (index + 1)}
          onClick={() => downloadCopy(copy)}>Download original copy</button>
        <button type="button" className="button compact" disabled={busy} aria-label={"Discard " + label + " copy " + (index + 1)}
          onClick={() => discardCopy(copy)}>Discard this copy</button>
      </div>)}
    </details>}
    {narrativeHasInput(draft) && !conflict && <button type="button" className="button compact" disabled={busy || narrativeUnconfirmed(draft)}
      onClick={() => choose("saved")}>Use saved {short}</button>}
  </section>;
}
