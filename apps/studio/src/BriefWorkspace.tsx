import { CircleDashed } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Change, Project } from "./types";
import { bootstrap, projectHistory } from "./api";
import {
  acknowledgeBriefSubmission, BRIEF_DRAFT_STORAGE_PREFIX, BRIEF_FIELD_LABELS, BRIEF_FIELDS, briefDraftConflicts, briefDraftHasWork,
  briefDraftScope, briefDraftStorageKey, briefFieldHasInput, briefGroupFields, briefGroupHasChanges, briefSubmissionMatches,
  createBriefDraft, decodeBriefRecovery, editBriefField, fieldText, prepareBriefSubmission, readBriefRecoveries,
  reconcileBriefDraft, resolveBriefField, releaseBriefUnconfirmed, stageBriefSubmission,
  writeBriefRecovery, writeBriefSessionPointer, briefHasUnconfirmedSubmission,
  briefPendingMatches, markBriefNotSent, retainBriefUncertainReply,
  inspectBriefOutcome, briefOutcomeReviewMatches, retainReviewedBriefIntent,
} from "./briefDraft";
import type { BriefDraft, BriefField, BriefGroup, BriefRecoveryEntry, BriefSubmission, BriefOutcomeReview, UnreadableBriefRecovery } from "./briefDraft";

// The existing mutation coordinator must reject a failed or uncertain call.
// A nonthrowing error-display callback cannot establish the original reply.
type Props = { project: Project; commit: (change: Change) => Promise<void> };
const SESSION_POINTER_PREFIX = "motionwright.brief-session.v1:";
// Keeps inputs available across workspace navigation, including a storage outage.
// These are editor buffers; Project and its service remain the only saved state.
type OriginalBriefReply = { submission: BriefSubmission; sessionId: string; intentId: string };
const liveDrafts = new Map<string, {
  draft: BriefDraft; sessionId: string; originalReply?: OriginalBriefReply;
  recoveryError?: string; notify?: () => void;
}>();
const fieldId = (field: BriefField) => field === "title" ? "project-title-rich" : "brief-" + field;

function readRecovery(project: Project) {
  const scope = briefDraftScope(project);
  let entries: BriefRecoveryEntry[] = [], unreadable: UnreadableBriefRecovery[] = [];
  let limited = false;
  let backupError: string | null = null, previousSession: string | null = null;
  try { ({ entries, unreadable, limited } = readBriefRecoveries(window.localStorage, scope)); }
  catch (reason) { backupError = reason instanceof Error ? reason.message : "Local brief recovery is unavailable."; }
  try { previousSession = window.sessionStorage.getItem(SESSION_POINTER_PREFIX + scope); } catch { /* Local copies remain usable. */ }
  const cached = liveDrafts.get(scope);
  // Prefer this tab's own copy over another tab's newer clean checkpoint.
  const ownCopyUnreadable = unreadable.some(copy => copy.sessionId === previousSession);
  // A missing writer pointer must not silently import another tab's "most
  // recent" draft. A durable copy is still visible for deliberate Restore.
  const restored = previousSession && !ownCopyUnreadable
    ? entries.find(entry => entry.value.sessionId === previousSession) : undefined;
  const previous = cached?.draft ?? restored?.value.draft ?? createBriefDraft(project);
  const reply = cached?.originalReply;
  const draft = reply && cached?.sessionId === reply.sessionId && previous.pending?.intentId === reply.intentId
    && briefPendingMatches(previous.pending, reply.submission) && briefSubmissionMatches(project, reply.submission)
    ? acknowledgeBriefSubmission(previous, project, reply.submission, true)
    : reconcileBriefDraft(previous, project);
  return { draft, sessionId: cached?.sessionId ?? crypto.randomUUID(), entries, unreadable, limited,
    backupError: cached?.recoveryError ?? backupError, originalReply: cached?.originalReply,
    notice: cached && briefDraftHasWork(draft) ? "Your uncommitted brief inputs are still available."
      : !cached && restored?.value.draft ? "Recovered this tab's brief draft. Compare it with the current project before saving."
      : !cached && entries.some(entry => entry.value.draft !== null)
        ? "Local brief copies are available. Choose Restore explicitly; no other window's draft was adopted."
        : null };
}

export default function BriefWorkspace(props: Props) {
  return <BriefEditor key={briefDraftScope(props.project)} {...props}/>;
}

function BriefEditor({ project, commit }: Props) {
  const scope = briefDraftScope(project);
  const [initial] = useState(() => readRecovery(project));
  const [draft, setDraft] = useState(initial.draft);
  const draftRef = useRef(draft), projectRef = useRef(project), sessionId = useRef(initial.sessionId);
  projectRef.current = project;
  const [entries, setEntries] = useState(initial.entries);
  const [unreadable, setUnreadable] = useState(initial.unreadable);
  const [copiesLimited, setCopiesLimited] = useState(initial.limited);
  const [backupError, setBackupError] = useState(initial.backupError);
  const [notice, setNotice] = useState(initial.notice);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [outcomeReview, setOutcomeReview] = useState<BriefOutcomeReview | null>(null);
  const [reviewingOutcome, setReviewingOutcome] = useState(false);
  const backupFailure = useRef<string | null>(null);
  const originalReply = useRef<OriginalBriefReply | null>(initial.originalReply ?? null);
  const inFlight = useRef(false), mounted = useRef(true);
  const submitted = useRef<BriefSubmission | null>(null);
  const conflicts = briefDraftConflicts(draft, project);
  const contentLocked = project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content");

  function refreshCopies() {
    try {
      const copies = readBriefRecoveries(window.localStorage, scope);
      setEntries(copies.entries); setUnreadable(copies.unreadable); setCopiesLimited(copies.limited);
    } catch (reason) { setBackupError(reason instanceof Error ? reason.message : "Local brief recovery is unavailable."); }
  }
  function remember(next: BriefDraft) {
    draftRef.current = next;
    liveDrafts.set(scope, { ...liveDrafts.get(scope), draft: next, sessionId: sessionId.current });
  }
  function backup(next: BriefDraft): boolean {
    try {
      const hasWork = briefDraftHasWork(next);
      const previous = window.localStorage.getItem(briefDraftStorageKey(scope, sessionId.current));
      if (hasWork && previous !== null && decodeBriefRecovery(previous, scope, sessionId.current).draft === null) {
        // A completed checkpoint stays immutable so another tab can safely
        // compact redundant clean copies. New input gets its own writer.
        sessionId.current = crypto.randomUUID();
        liveDrafts.set(scope, { ...liveDrafts.get(scope), draft: next, sessionId: sessionId.current });
      }
      writeBriefRecovery(window.localStorage, { schema: "motionwright.brief-recovery/1", scope,
        sessionId: sessionId.current, updatedAt: Date.now(), draft: hasWork ? next : null });
      // The copy alone is NOT automatic reload recovery: its session pointer
      // must be both written and read back before permitting Project writes.
      // A failed pointer still leaves original bytes for explicit Restore.
      writeBriefSessionPointer(window.sessionStorage, SESSION_POINTER_PREFIX + scope, sessionId.current);
      backupFailure.current = null;
      const cached = liveDrafts.get(scope);
      if (cached) delete cached.recoveryError;
      setBackupError(null);
      return true;
    } catch (reason) {
      backupFailure.current = (reason instanceof Error ? reason.message : "Local storage is unavailable.")
        + " Your current input remains in this editor. The recovery copy or writer pointer is unconfirmed; restore a durable copy explicitly if needed. Unreadable copies were kept.";
      const cached = liveDrafts.get(scope);
      if (cached) cached.recoveryError = backupFailure.current;
      setBackupError(backupFailure.current);
      return false;
    }
  }
  function replace(next: BriefDraft, persist = true): boolean {
    // A former clean key may already have been compacted by another tab. Never
    // reuse it for input, even when that old checkpoint is no longer in storage.
    if (briefDraftHasWork(next) && !briefDraftHasWork(draftRef.current)) sessionId.current = crypto.randomUUID();
    remember(next); setDraft(next);
    return persist ? backup(next) : true;
  }
  function edit(field: BriefField, raw: string) {
    const preserveIntent = inFlight.current || Boolean(submitted.current?.fields.includes(field))
      || Boolean(briefHasUnconfirmedSubmission(draftRef.current) && draftRef.current.pending?.fields.includes(field));
    if (!inFlight.current) submitted.current = null;
    setError(null); setNotice(null);
    replace(editBriefField(draftRef.current, field, raw, preserveIntent));
  }
  function observe(current: Project) {
    const reply = originalReply.current, attempt = reply?.submission;
    const matches = reply !== null && attempt !== undefined && sessionId.current === reply.sessionId
      && draftRef.current.pending?.intentId === reply.intentId && briefSubmissionMatches(current, attempt)
      && briefPendingMatches(draftRef.current.pending, attempt);
    const next = matches ? acknowledgeBriefSubmission(draftRef.current, current, attempt!, true)
      : reconcileBriefDraft(draftRef.current, current);
    if (matches) {
      submitted.current = null; originalReply.current = null; setError(null);
      const cached = liveDrafts.get(scope);
      if (cached) delete cached.originalReply;
      setNotice("The original save reply and saved revision r" + current.revision
        + " agree." + (briefDraftHasWork(next) ? " Your other uncommitted inputs are still available." : ""));
    }
    if (JSON.stringify(next) !== JSON.stringify(draftRef.current)) replace(next);
    return matches;
  }
  function publishReply(attempt: BriefSubmission, writerId: string, intentId: string, diagnostic?: string) {
    const cached = liveDrafts.get(scope);
    if (!cached || cached.sessionId !== writerId || cached.draft.pending?.intentId !== intentId
      || !briefPendingMatches(cached.draft.pending, attempt)) return;
    if (diagnostic === undefined) {
      // This in-memory fact came from the original throwing coordinator call.
      // It is not serialized into recovery and is never replay authority.
      cached.originalReply = { submission: attempt, sessionId: writerId, intentId };
    } else {
      delete cached.originalReply;
      cached.draft = retainBriefUncertainReply(cached.draft, attempt, diagnostic);
      try {
        writeBriefRecovery(window.localStorage, {
          schema: "motionwright.brief-recovery/1", scope, sessionId: cached.sessionId,
          updatedAt: Date.now(), draft: cached.draft,
        });
      } catch (reason) {
        cached.recoveryError = (reason instanceof Error ? reason.message : String(reason))
          + " The unconfirmed outcome remains in this editor; its earlier recovery copy was not reclassified.";
      }
    }
    liveDrafts.set(scope, cached);
    cached.notify?.();
  }

  useEffect(() => {
    mounted.current = true;
    remember(draftRef.current);
    const receiveOutcome = () => {
      const cached = liveDrafts.get(scope);
      if (!cached) return;
      sessionId.current = cached.sessionId;
      draftRef.current = cached.draft; setDraft(cached.draft);
      originalReply.current = cached.originalReply ?? null;
      if (cached.recoveryError) setBackupError(cached.recoveryError);
      if (cached.draft.pending?.diagnostic && !cached.originalReply)
        setError(cached.draft.pending.diagnostic + " The Brief outcome remains unconfirmed; inspect the saved state before a separate intent.");
      observe(projectRef.current);
    };
    liveDrafts.get(scope)!.notify = receiveOutcome;
    return () => {
      mounted.current = false;
      const cached = liveDrafts.get(scope);
      if (cached?.notify === receiveOutcome) delete cached.notify;
    };
    // A scope owns this editor instance. Canonical revisions do not remount it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    observe(project);
    // An observed document reconciles fields; it never resets the whole form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key.startsWith(BRIEF_DRAFT_STORAGE_PREFIX)) refreshCopies();
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  async function save(group: BriefGroup) {
    if (inFlight.current || contentLocked || briefHasUnconfirmedSubmission(draftRef.current)) {
      if (briefHasUnconfirmedSubmission(draftRef.current)) setError("Review the saved outcome of the earlier brief submission first.");
      return;
    }
    let attempt: BriefSubmission, staged: BriefDraft;
    try {
      attempt = prepareBriefSubmission(draftRef.current, projectRef.current, group);
      staged = stageBriefSubmission(draftRef.current, projectRef.current, attempt);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return; }
    submitted.current = attempt; originalReply.current = null;
    const previous = liveDrafts.get(scope);
    if (previous) delete previous.originalReply;
    inFlight.current = true; setPending(true); setError(null); setNotice(null); setOutcomeReview(null);
    if (!replace(staged)) {
      // This branch has not invoked commit, obtained a grant or sent a request.
      // Retrying local backup does not acquire an uncertain dispatched attempt.
      replace(markBriefNotSent(draftRef.current, attempt,
        backupFailure.current ?? "The Brief recovery record or pointer was not confirmed."), false);
      submitted.current = null; inFlight.current = false; setPending(false);
      setError("NOT_SENT: the Brief recovery record or its writer pointer was not confirmed. No Project change was sent. Retry backup before saving.");
      return;
    }
    const writerId = sessionId.current, intentId = draftRef.current.pending!.intentId!;
    try {
      await commit(attempt.change);
      publishReply(attempt, writerId, intentId);
    } catch (reason) {
      // Retain the result before any local custody failure or workspace unmount.
      publishReply(attempt, writerId, intentId, reason instanceof Error ? reason.message : String(reason));
    } finally {
      inFlight.current = false;
      if (mounted.current) setPending(false);
    }
  }
  async function inspectOutcome() {
    if (inFlight.current || reviewingOutcome || !briefHasUnconfirmedSubmission(draftRef.current)) return;
    setReviewingOutcome(true); setOutcomeReview(null); setError(null);
    try {
      const review = await inspectBriefOutcome(draftRef.current, projectRef.current, {
        readProject: async id => {
          const saved = await bootstrap(id);
          if (!saved.project || saved.project_selection.status !== "selected")
            throw new Error("The selected saved Project is unavailable.");
          return saved.project;
        },
        readHistory: projectHistory,
      });
      if (!mounted.current) return;
      if (!briefOutcomeReviewMatches(draftRef.current, projectRef.current, review))
        throw new Error("The Brief attempt or selected Project changed during inspection. Inspect the current state again.");
      setOutcomeReview(review);
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (mounted.current) setReviewingOutcome(false);
    }
  }
  function continueAfterReview() {
    if (inFlight.current || !outcomeReview) return;
    try {
      const before = draftRef.current;
      const next = releaseBriefUnconfirmed(before, projectRef.current, outcomeReview);
      // Keep the old UNKNOWN record, verify the new writer while it still
      // contains that pending snapshot, then retain the reviewed new draft.
      const retained = retainReviewedBriefIntent(window.localStorage, window.sessionStorage,
        SESSION_POINTER_PREFIX + scope,
        { schema: "motionwright.brief-recovery/1", scope, sessionId: sessionId.current,
          updatedAt: Date.now(), draft: before }, next, crypto.randomUUID());
      sessionId.current = retained.sessionId;
      submitted.current = null; originalReply.current = null;
      const cached = liveDrafts.get(scope);
      if (cached) { delete cached.originalReply; delete cached.recoveryError; }
      replace(next, false); setBackupError(null); backupFailure.current = null;
      setOutcomeReview(null); refreshCopies(); setError(null);
      setNotice("The earlier unconfirmed attempt remains in a separate recovery copy. Your current input is available for a new explicit save; nothing was retried or acknowledged.");
    } catch (reason) {
      const diagnostic = reason instanceof Error ? reason.message : String(reason);
      setBackupError(diagnostic); setError(diagnostic + " The unconfirmed attempt is still retained; no new Project change was sent.");
    }
  }
  function resolve(field: BriefField, choice: "saved" | "draft") {
    submitted.current = null; setError(null);
    replace(resolveBriefField(draftRef.current, projectRef.current, field, choice));
    setNotice(choice === "saved" ? "Kept the saved " + BRIEF_FIELD_LABELS[field].toLowerCase() + ". Other inputs are unchanged."
      : "Kept your " + BRIEF_FIELD_LABELS[field].toLowerCase() + " draft against revision r" + project.revision + ". Save explicitly to apply it.");
    document.getElementById(fieldId(field))?.focus();
  }
  function restore(entry: BriefRecoveryEntry) {
    if (inFlight.current || briefHasUnconfirmedSubmission(draftRef.current) || !entry.value.draft) return;
    // Fork before replacing this editor's inputs; its previous dirty copy stays recoverable.
    if (briefDraftHasWork(draftRef.current) && !backup(draftRef.current)) {
      setError("Keep the current brief open until its recovery copy can be written before restoring another draft."); return;
    }
    sessionId.current = crypto.randomUUID(); submitted.current = null; originalReply.current = null; setError(null);
    const cached = liveDrafts.get(scope);
    if (cached) delete cached.originalReply;
    replace(reconcileBriefDraft(entry.value.draft, projectRef.current));
    setNotice("Restored this local copy as a separate brief draft. It has not been committed to the project.");
    refreshCopies();
  }
  function discardUnreadable(key: string) {
    try { window.localStorage.removeItem(key); refreshCopies(); }
    catch (reason) { setBackupError(reason instanceof Error ? reason.message : "The unreadable copy could not be removed."); }
  }
  const groupBlocked = (group: BriefGroup) => pending || contentLocked || backupError !== null || briefHasUnconfirmedSubmission(draft)
    || conflicts.some(conflict => briefGroupFields(group).includes(conflict.field))
    || !briefGroupHasChanges(draft, project, group)
    || (group === "title" && !draft.fields.title.raw.trim());
  const textField = (field: Exclude<BriefField, "title">, rows: number) => <label>
    <span className="field-label">{BRIEF_FIELD_LABELS[field]}{field === "constraints" || field === "exclusions" ? " · one per line" : ""}</span>
    <textarea id={fieldId(field)} aria-label={BRIEF_FIELD_LABELS[field]} rows={rows} value={draft.fields[field].raw}
      aria-invalid={conflicts.some(conflict => conflict.field === field)} onChange={event => edit(field, event.target.value)}/>
  </label>;
  const earlierCopies = entries.filter(entry => entry.value.sessionId !== sessionId.current);

  return <div className="workspace-scroll document-view" onKeyDown={event => {
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (event.code === "Space" && !event.ctrlKey && !event.metaKey && !event.altKey && target?.closest("button, summary")) {
      // Preserve native button/summary activation without leaking Space to App transport.
      event.stopPropagation();
    }
  }}>
    <header className="workspace-heading"><div><h2>Project brief</h2>
      <p>Audience, objective and constraints are versioned project state. They are not hidden prompt context.</p></div>
      <span className={"status-pill status-" + project.state}>{project.state.toUpperCase()}</span></header>
    {notice && <p className="production-notice" role="status">{notice}</p>}
    {error && <p className="production-error" role="alert">{error}</p>}
    {draft.pending?.outcome === "NOT_SENT" && <p role="status" aria-label="Brief change not sent">
      The last Brief preparation was not sent. Retry local backup, then save explicitly when ready.
    </p>}
    {briefHasUnconfirmedSubmission(draft) && draft.pending && <section className="production-notice" role="region" aria-label="Unconfirmed brief submission">
      <strong>Unconfirmed Brief change from r{draft.pending.revision}</strong>
      <p>The original reply was not confirmed. The saved state may contain the change. Your sent values and later input remain available; nothing is replayed automatically.</p>
      {draft.pending.diagnostic && <p role="status">{draft.pending.diagnostic}</p>}
      <button className="button compact" type="button" disabled={pending || reviewingOutcome}
        onClick={() => window.location.reload()}>Reload to inspect saved Brief revision</button>
      <button className="button compact" type="button" disabled={pending || reviewingOutcome}
        onClick={() => void inspectOutcome()}>Read saved Brief and history</button>
      {outcomeReview && <section aria-label="Saved Brief outcome review">
        <p>Read saved revision r{outcomeReview.project.revision} and {outcomeReview.events.length} consecutive history records.</p>
        <p>{outcomeReview.matchingValueRevisions.length
          ? "History contains matching values at " + outcomeReview.matchingValueRevisions.map(value => "r" + value).join(", ") + "."
          : "No matching change values occur in the inspected history."}
          {" "}This observation does not identify or acknowledge the earlier request.</p>
        <dl>{draft.pending.fields.map(field => <div key={field}>
          <dt>{BRIEF_FIELD_LABELS[field]} · submitted</dt><dd style={{ whiteSpace: "pre-wrap" }}>{fieldText(draft.pending!.values[field]!) || "(empty)"}</dd>
          <dt>{BRIEF_FIELD_LABELS[field]} · saved now</dt><dd style={{ whiteSpace: "pre-wrap" }}>{fieldText(field === "title"
            ? outcomeReview.project.title : outcomeReview.project.brief[field]) || "(empty)"}</dd>
        </div>)}</dl>
        <details><summary>Inspected revision history</summary><ol>{outcomeReview.events.map(event =>
          <li key={event.revision}>r{event.revision} · {event.change.type} · {event.created_at}</li>)}</ol></details>
        {!briefOutcomeReviewMatches(draft, project, outcomeReview) && <p role="status">The selected state changed. Read its current history again.</p>}
        <button className="button compact" type="button"
          disabled={pending || reviewingOutcome || !briefOutcomeReviewMatches(draft, project, outcomeReview)}
          onClick={continueAfterReview}>Keep earlier attempt and continue editing</button>
      </section>}
    </section>}
    {backupError && <section className="production-notice" role="alert" aria-label="Brief draft backup warning">
      <strong>Local recovery needs attention.</strong><span>{backupError}</span>
      <button className="button compact" type="button" onClick={() => { backup(draftRef.current); refreshCopies(); }}>Retry brief draft backup</button>
    </section>}
    {(earlierCopies.length > 0 || unreadable.length > 0 || copiesLimited) && <section className="production-notice" role="region" aria-label="Brief draft recovery">
      <strong>Local brief recovery</strong><span>Readable copies belong to this project generation and branch. Restoring a copy never commits it.</span>
      {copiesLimited && <p role="status">More than 64 recovery copies are stored for this scope. Only the first 64 were inspected. Discard copies you no longer need to inspect the remaining copies; nothing was removed automatically.</p>}
      {unreadable.map(copy => <div key={copy.key}>
        <p role="status">{copy.unscoped ? "A stored brief copy has an unreadable project identity." : "A brief recovery copy could not be read."} Its original contents were left untouched.</p>
        <button className="button compact" type="button" aria-label={"Discard unreadable brief draft " + copy.sessionId}
          onClick={() => discardUnreadable(copy.key)}>Discard unreadable copy</button>
      </div>)}
      {earlierCopies.length > 0 && <details><summary>{earlierCopies.length} earlier brief recovery {earlierCopies.length === 1 ? "copy" : "copies"}</summary>
        {earlierCopies.map(entry => <div key={entry.key} className="sheet-actions">
          <span>{new Date(entry.value.updatedAt).toLocaleString()} · {entry.value.draft ? "local draft" : "clean checkpoint, no pending input"}</span>
          {entry.value.draft && <button className="button compact" type="button" disabled={pending || briefHasUnconfirmedSubmission(draft)} aria-label={"Restore brief draft " + entry.value.sessionId}
            onClick={() => restore(entry)}>Restore copy</button>}
          <button className="button compact" type="button" disabled={pending} aria-label={"Discard brief draft " + entry.value.sessionId}
            onClick={() => discardUnreadable(entry.key)}>Discard copy</button>
        </div>)}
      </details>}
    </section>}
    {conflicts.length > 0 && <section className="production-notice" role="region" aria-label="Brief conflicts">
      <strong>Review overlapping brief changes</strong><p>Your input has been kept. Resolve only the affected fields; nothing here commits a change.</p>
      {conflicts.map(conflict => <section key={conflict.field} aria-label={BRIEF_FIELD_LABELS[conflict.field] + " conflict"}>
        <h3>{BRIEF_FIELD_LABELS[conflict.field]}</h3>
        {conflict.futureRevision && <p>This recovered field came from a later revision. Check the current project before choosing its new base.</p>}
        <dl><dt>Draft started from</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{fieldText(conflict.before) || "(empty)"}</dd>
          <dt>Saved now · r{project.revision}</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{fieldText(conflict.current) || "(empty)"}</dd>
          <dt>Your draft</dt><dd style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{draft.fields[conflict.field].raw || "(empty)"}</dd></dl>
        <div className="sheet-actions">
          <button className="button compact" type="button" disabled={pending} onClick={() => resolve(conflict.field, "saved")}>Keep saved {BRIEF_FIELD_LABELS[conflict.field]}</button>
          <button className="button compact" type="button" disabled={pending} onClick={() => resolve(conflict.field, "draft")}>Keep my draft {BRIEF_FIELD_LABELS[conflict.field]}</button>
        </div>
      </section>)}
    </section>}
    <div className="document-grid"><section className="document-main">
      <label className="field-label" htmlFor="project-title-rich">Project title</label>
      <div className="inline-save-field"><input id="project-title-rich" value={draft.fields.title.raw}
        aria-invalid={conflicts.some(conflict => conflict.field === "title")} onChange={event => edit("title", event.target.value)}/>
        <button className="button button-primary" type="button" disabled={groupBlocked("title")} onClick={() => void save("title")}>Save title</button></div>
      <div className="form-stack">{textField("objective", 5)}{textField("audience", 3)}
        <div className="two-column-fields">{textField("constraints", 6)}{textField("exclusions", 6)}</div>
        <div className="sheet-actions"><span>{project.brief.claims.length} evidence-linked claims</span>
          <button className="button button-primary" type="button" disabled={groupBlocked("brief")} onClick={() => void save("brief")}>Commit brief</button></div>
      </div>
    </section><aside className="document-side"><h3>Project authority</h3>
      <div className="brief-facts vertical"><div><span>Revision</span><strong>r{project.revision}</strong></div>
        <div><span>Claims</span><strong>{project.brief.claims.length}</strong></div>
        <div><span>Model receipts</span><strong>{project.model_invocations.length}</strong></div></div>
      <p>{BRIEF_FIELDS.filter(field => briefFieldHasInput(draft.fields[field])).length} fields with local input.</p>
      <p>Local recovery is separate from the saved project. Saving this brief does not rewrite an existing production plan or approve its realization.</p>
      <p>Recovery allows 1 MiB per copy and 64 copies / 4 MiB across projects. Copies with uncommitted input are preserved; redundant clean checkpoints can be removed automatically.</p>
      {contentLocked && <p role="status">Project content is locked. You can keep drafting; saving requires the existing project lock to be released.</p>}
      <button className="button compact" type="button" disabled={pending || briefHasUnconfirmedSubmission(draft) || !briefDraftHasWork(draft)} onClick={() => {
        submitted.current = null; setError(null); replace(createBriefDraft(projectRef.current));
        setNotice("Discarded this local brief draft. The saved project is unchanged.");
      }}>Discard this brief draft</button>
      <div className="evidence-callout compact-callout"><CircleDashed size={17}/><div><strong>No invisible approval</strong>
        <span>Missing evidence stays explicit until a source is attached.</span></div></div>
    </aside></div>
  </div>;
}
