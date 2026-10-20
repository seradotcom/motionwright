import { CircleDashed } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Change, Project } from "./types";
import {
  acknowledgeBriefSubmission, BRIEF_DRAFT_STORAGE_PREFIX, BRIEF_FIELD_LABELS, BRIEF_FIELDS, briefDraftConflicts, briefDraftHasWork,
  briefDraftScope, briefDraftStorageKey, briefFieldHasInput, briefGroupFields, briefGroupHasChanges, briefSubmissionMatches,
  createBriefDraft, decodeBriefRecovery, editBriefField, fieldText, prepareBriefSubmission, readBriefRecoveries,
  reconcileBriefDraft, resolveBriefField, retainBriefSubmissionIntent, writeBriefRecovery,
  writeBriefSessionPointer,
} from "./briefDraft";
import type { BriefDraft, BriefField, BriefGroup, BriefRecoveryEntry, BriefSubmission, UnreadableBriefRecovery } from "./briefDraft";

type Props = { project: Project; commit: (change: Change) => Promise<void> };
const SESSION_POINTER_PREFIX = "motionwright.brief-session.v1:";
// Keeps inputs available across workspace navigation, including a storage outage.
// These are editor buffers; Project and its service remain the only saved state.
const liveDrafts = new Map<string, { draft: BriefDraft; sessionId: string }>();
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
  const draft = reconcileBriefDraft(cached?.draft ?? restored?.value.draft ?? createBriefDraft(project), project);
  return { draft, sessionId: cached?.sessionId ?? crypto.randomUUID(), entries, unreadable, limited, backupError,
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
    liveDrafts.set(scope, { draft: next, sessionId: sessionId.current });
  }
  function backup(next: BriefDraft): boolean {
    try {
      const hasWork = briefDraftHasWork(next);
      const previous = window.localStorage.getItem(briefDraftStorageKey(scope, sessionId.current));
      if (hasWork && previous !== null && decodeBriefRecovery(previous, scope, sessionId.current).draft === null) {
        // A completed checkpoint stays immutable so another tab can safely
        // compact redundant clean copies. New input gets its own writer.
        sessionId.current = crypto.randomUUID();
        liveDrafts.set(scope, { draft: next, sessionId: sessionId.current });
      }
      writeBriefRecovery(window.localStorage, { schema: "motionwright.brief-recovery/1", scope,
        sessionId: sessionId.current, updatedAt: Date.now(), draft: hasWork ? next : null });
      // The copy alone is NOT automatic reload recovery: its session pointer
      // must be both written and read back before permitting Project writes.
      // A failed pointer still leaves original bytes for explicit Restore.
      writeBriefSessionPointer(window.sessionStorage, SESSION_POINTER_PREFIX + scope, sessionId.current);
      setBackupError(null);
      return true;
    } catch (reason) {
      setBackupError((reason instanceof Error ? reason.message : "Local storage is unavailable.")
        + " Your current input remains in this editor. If its writer pointer failed, restore the durable copy explicitly after reload; no Project change was sent. Unreadable copies were kept.");
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
    const preserveIntent = inFlight.current || Boolean(submitted.current?.fields.includes(field));
    if (!inFlight.current) submitted.current = null;
    setError(null); setNotice(null);
    replace(editBriefField(draftRef.current, field, raw, preserveIntent));
  }
  function observe(current: Project) {
    const attempt = submitted.current;
    const matches = attempt !== null && briefSubmissionMatches(current, attempt);
    const next = matches ? acknowledgeBriefSubmission(draftRef.current, current, attempt!)
      : reconcileBriefDraft(draftRef.current, current);
    if (matches) {
      submitted.current = null; setError(null);
      setNotice("The submitted values match saved revision r" + current.revision
        + "." + (briefDraftHasWork(next) ? " Your other uncommitted inputs are still available." : ""));
    } else if (attempt && current.revision > attempt.revision) {
      submitted.current = null;
      setError("The observed revision does not match the submitted values. Your draft has been kept; review any changed fields before saving again.");
    }
    if (JSON.stringify(next) !== JSON.stringify(draftRef.current)) replace(next);
    return matches;
  }

  useEffect(() => {
    mounted.current = true;
    remember(draftRef.current);
    return () => { mounted.current = false; };
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
    if (inFlight.current || contentLocked) return;
    let attempt: BriefSubmission;
    try { attempt = prepareBriefSubmission(draftRef.current, projectRef.current, group); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return; }
    submitted.current = attempt; inFlight.current = true; setPending(true); setError(null); setNotice(null);
    if (!replace(retainBriefSubmissionIntent(draftRef.current, projectRef.current, attempt))) {
      submitted.current = null;
      inFlight.current = false;
      setPending(false);
      setError("The brief recovery copy or its session pointer is unconfirmed. Nothing was sent to the Project. Restore or retry backup before committing.");
      return;
    }
    try {
      await commit(attempt.change);
      if (!mounted.current) return;
      // Legacy consumers resolve void even after failure. A resolved callback is
      // insufficient: only the exact observed canonical values recognize this snapshot.
      if (submitted.current && !observe(projectRef.current)) {
        setError("Saving has not been confirmed by a matching project revision. Your draft is still available.");
      }
    } catch (reason) {
      if (!mounted.current) return;
      submitted.current = null;
      setError((reason instanceof Error ? reason.message : String(reason)) + " Your draft has been kept.");
    } finally {
      inFlight.current = false;
      if (mounted.current) setPending(false);
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
    if (inFlight.current || !entry.value.draft) return;
    // Fork before replacing this editor's inputs; its previous dirty copy stays recoverable.
    if (briefDraftHasWork(draftRef.current) && !backup(draftRef.current)) {
      setError("Keep the current brief open until its recovery copy can be written before restoring another draft."); return;
    }
    sessionId.current = crypto.randomUUID(); submitted.current = null; setError(null);
    replace(reconcileBriefDraft(entry.value.draft, projectRef.current));
    setNotice("Restored this local copy as a separate brief draft. It has not been committed to the project.");
    refreshCopies();
  }
  function discardUnreadable(key: string) {
    try { window.localStorage.removeItem(key); refreshCopies(); }
    catch (reason) { setBackupError(reason instanceof Error ? reason.message : "The unreadable copy could not be removed."); }
  }
  const groupBlocked = (group: BriefGroup) => pending || contentLocked || backupError !== null
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
          {entry.value.draft && <button className="button compact" type="button" disabled={pending} aria-label={"Restore brief draft " + entry.value.sessionId}
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
      <button className="button compact" type="button" disabled={pending || !briefDraftHasWork(draft)} onClick={() => {
        submitted.current = null; setError(null); replace(createBriefDraft(projectRef.current));
        setNotice("Discarded this local brief draft. The saved project is unchanged.");
      }}>Discard this brief draft</button>
      <div className="evidence-callout compact-callout"><CircleDashed size={17}/><div><strong>No invisible approval</strong>
        <span>Missing evidence stays explicit until a source is attached.</span></div></div>
    </aside></div>
  </div>;
}
