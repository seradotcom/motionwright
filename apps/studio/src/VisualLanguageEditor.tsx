import { LockKeyhole, Plus, Trash2, Unlock } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type {
  Change,
  FontRight,
  MotionVerb,
  Project,
  VisualToken,
} from "./types";

import {
  VISUAL_DRAFT_FIELDS, VISUAL_DRAFT_PREFIX, VISUAL_FIELD_LABELS, VISUAL_SESSION_PREFIX,
  createVisualDraft, decodeVisualRecovery, discardVisualRecovery, editVisualField,
  prepareVisualSubmission, readVisualRecoveries, reconcileVisualDraft, releaseVisualSubmission,
  resolveVisualField, visualDraftConflicts, visualDraftHasWork, visualDraftInput,
  visualDraftIssue, visualDraftScope, visualSubmissionMatches, writeVisualRecovery,
} from "./visualLanguageDraft";
import type { VisualDraftField, VisualLanguageDraft, VisualLanguageInput, VisualRecoveryEntry } from "./visualLanguageDraft";

type Commit = (change: Change) => Promise<void>;

const emptyToken = (): VisualToken => ({ name: "", value: "" });
const emptyMotion = (): VisualLanguageInput["motion_grammar"][number] => ({
  name: "",
  meaning: "",
  duration_ms: "180",
  reduced_motion: "static_equivalent",
});
const emptyFont = (): FontRight => ({
  family: "",
  source: "",
  rights_status: "unknown",
});

type VisualDraftSession = {
  draft: VisualLanguageDraft; writerId: string; lastRaw: string | null; retainClean: boolean; listeners: Set<() => void>;
};
// Durable input is primary. The cache preserves navigation while a storage
// outage is visible; it cannot make a failed backup survive a full reload.
const liveVisualDrafts = new Map<string, VisualDraftSession>();
function describeVisualGroup(field: VisualDraftField, raw: string): string {
  if (field === "name") return raw || "(empty name)";
  try {
    const values = JSON.parse(raw) as unknown[];
    if (values.length === 0) return "No entries";
    if (field === "anti_slop_rules") return (values as string[]).map((rule, index) => (index + 1) + ". " + (rule || "(empty rule)")).join("\n");
    if (field === "palette" || field === "type_tokens") return (values as VisualToken[])
      .map(token => (token.name || "(unnamed token)") + ": " + (token.value || "(empty value)")).join("\n");
    if (field === "fonts") return (values as FontRight[]).map(font => (font.family || "(unnamed font)")
      + " · rights: " + font.rights_status + "\n" + (font.source || "No source reference")).join("\n\n");
    return (values as VisualLanguageInput["motion_grammar"]).map(verb => (verb.name || "(unnamed motion)")
      + " · " + (String(verb.duration_ms) || "(empty duration)") + " ms · reduced motion: "
      + verb.reduced_motion.replaceAll("_", " ") + "\n" + (verb.meaning || "(empty meaning)")).join("\n\n");
  } catch { return raw; }
}

const VISUAL_RECOVERY_STYLES = `
.visual-draft-status {display:grid;gap:8px;font-size:11px;line-height:1.5;overflow-wrap:anywhere;}
.visual-draft-status p {margin:0;}
.visual-draft-status .production-notice,.visual-draft-status .production-error {padding:9px;}
.visual-draft-status details {border:1px solid var(--border);border-radius:6px;padding:8px;background:var(--surface-2);}
.visual-draft-status summary {cursor:pointer;font-weight:600;}
.visual-draft-status details>div {border-top:1px solid var(--border-soft);margin-top:8px;padding-top:8px;}
.visual-draft-status .visual-recovery-actions {display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}
.visual-draft-status .button {white-space:normal;text-align:left;min-height:30px;}
.visual-draft-status dl {display:grid;gap:5px;margin:8px 0;}
.visual-draft-status dt {color:var(--muted);font-size:10px;}
.visual-draft-status dd {margin:0;white-space:pre-wrap;max-height:180px;overflow:auto;}
`;
function recoverVisualDraft(project: Project) {
  const scope = visualDraftScope(project);
  let copies: ReturnType<typeof readVisualRecoveries> = { entries: [], unreadable: [], limited: false };
  let backupError: string | null = null, previousWriter: string | null = null;
  try { copies = readVisualRecoveries(window.localStorage, scope); }
  catch (reason) { backupError = reason instanceof Error ? reason.message : "Local visual input could not be read."; }
  try { previousWriter = window.sessionStorage.getItem(VISUAL_SESSION_PREFIX + scope); } catch { /* Copies remain explicit recovery choices. */ }
  const cached = liveVisualDrafts.get(scope);
  const recovered = copies.entries.find(entry => entry.value.writerId === previousWriter);
  const draft = reconcileVisualDraft(cached?.draft ?? recovered?.value.draft ?? createVisualDraft(project), project);
  // Every fresh page gets a separate writer, even if a duplicated browser tab
  // copied sessionStorage. The recovered source stays available unchanged.
  const session = cached ?? { draft, writerId: crypto.randomUUID(),
    lastRaw: null, retainClean: false, listeners: new Set<() => void>() };
  session.draft = draft;
  return { session, copies, backupError,
    notice: visualDraftHasWork(draft) ? "Recovered your local visual input. Review and commit to apply it." : null };
}
export default function VisualLanguageEditor(props: { project: Project; commit: Commit }) {
  return <VisualLanguageDraftEditor key={visualDraftScope(props.project)} {...props} />;
}
function VisualLanguageDraftEditor({ project, commit }: { project: Project; commit: Commit }) {
  const language = project.visual_language;
  const [initial] = useState(() => recoverVisualDraft(project));
  const session = useRef(initial.session);
  const [buffer, setBuffer] = useState(initial.session.draft);
  const draft = visualDraftInput(buffer);
  const projectRef = useRef(project); projectRef.current = project;
  const [copies, setCopies] = useState(initial.copies);
  const [backupError, setBackupError] = useState(initial.backupError);
  const [notice, setNotice] = useState<string | null>(initial.notice);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [resetPending, setResetPending] = useState(false);
  const [removal, setRemoval] = useState<{ key: string; raw: string; writerId: string } | null>(null);
  const resetButton = useRef<HTMLButtonElement>(null), resetCancel = useRef<HTMLButtonElement>(null);
  const removeCancel = useRef<HTMLButtonElement>(null), systemName = useRef<HTMLInputElement>(null);
  const lockButton = useRef<HTMLButtonElement>(null);
  const inFlight = useRef(false), mounted = useRef(true);
  const scope = buffer.scope;
  const styleLock = useMemo(() => project.locks.find(lock => lock.resource === "project:" + project.id && lock.kind === "style") ?? null,
    [project.id, project.locks]);
  const dirty = visualDraftHasWork(buffer);
  const issue = visualDraftIssue(buffer, project);
  const conflicts = visualDraftConflicts(buffer, project);
  const earlier = copies.entries.filter(entry => entry.value.writerId !== session.current.writerId);

  function refreshCopies() {
    try { if (mounted.current) setCopies(readVisualRecoveries(window.localStorage, scope)); }
    catch (reason) { if (mounted.current) setBackupError(reason instanceof Error ? reason.message : "Local visual recovery could not be read."); }
  }
  function backup(next: VisualLanguageDraft): boolean {
    const writer = session.current;
    if (!visualDraftHasWork(next) && writer.lastRaw === null && !writer.retainClean) return true;
    try {
      if (visualDraftHasWork(next) && writer.lastRaw !== null && decodeVisualRecovery(writer.lastRaw, scope, writer.writerId).draft === null) {
        writer.writerId = crypto.randomUUID(); writer.lastRaw = null;
      }
      writer.lastRaw = writeVisualRecovery(window.localStorage, { schema: "motionwright.visual-recovery/1", scope,
        writerId: writer.writerId, updatedAt: Date.now(), draft: visualDraftHasWork(next) ? next : null }, writer.lastRaw);
      try { window.sessionStorage.setItem(VISUAL_SESSION_PREFIX + scope, writer.writerId); }
      catch { throw new Error("A local recovery copy was saved, but this window could not remember which copy to reopen. Restore session storage before reloading; otherwise choose the copy explicitly."); }
      writer.retainClean = false;
      if (mounted.current) setBackupError(null);
      return true;
    } catch (reason) {
      if (mounted.current) setBackupError((reason instanceof Error ? reason.message : "Local visual recovery is unavailable.")
        + " Your input stays in this session. The latest edit may not survive a full reload until backup succeeds.");
      return false;
    }
  }
  function replace(next: VisualLanguageDraft): boolean {
    const writer = session.current;
    if (visualDraftHasWork(writer.draft) && !visualDraftHasWork(next)) writer.retainClean = true;
    if (visualDraftHasWork(next) && !visualDraftHasWork(writer.draft)) { writer.writerId = crypto.randomUUID(); writer.lastRaw = null; }
    writer.draft = next; liveVisualDrafts.set(scope, writer);
    if (liveVisualDrafts.size > 128) for (const [key, cached] of liveVisualDrafts) {
      if (liveVisualDrafts.size <= 128) break;
      if (key !== scope && cached.listeners.size === 0 && !visualDraftHasWork(cached.draft)) liveVisualDrafts.delete(key);
    }
    writer.listeners.forEach(listener => listener());
    if (mounted.current) setBuffer(next);
    return backup(next);
  }
  function observe(current: Project) {
    const previous = session.current.draft, attempt = previous.awaiting;
    const next = reconcileVisualDraft(previous, current);
    if (attempt && next.awaiting === null && mounted.current) {
      if (visualSubmissionMatches(current, previous, attempt)) {
        setError(null); setNotice("Saved visual system at r" + current.revision + "."
          + (visualDraftHasWork(next) ? " Your later input has been kept." : ""));
      } else setError("A newer saved revision does not confirm the submitted visual system. Your input is kept; review overlapping groups.");
    }
    replace(next);
  }
  useEffect(() => {
    mounted.current = true;
    const writer = session.current, refresh = () => setBuffer(writer.draft);
    writer.listeners.add(refresh);
    const storageChanged = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(VISUAL_DRAFT_PREFIX)) refreshCopies(); };
    window.addEventListener("storage", storageChanged);
    return () => { mounted.current = false; writer.listeners.delete(refresh); window.removeEventListener("storage", storageChanged); };
  }, []);
  useEffect(() => { observe(project); }, [project]);
  useEffect(() => { if (resetPending) resetCancel.current?.focus(); }, [resetPending]);
  useEffect(() => { if (removal) removeCancel.current?.focus(); }, [removal]);

  // Existing row controls edit one collection as a unit. Rows have no stable
  // identity, so merging by their index or editable name would lose decisions.
  function setDraft(update: (current: VisualLanguageInput) => VisualLanguageInput) {
    const current = session.current.draft, before = visualDraftInput(current), next = update(before);
    let edited = current;
    for (const field of VISUAL_DRAFT_FIELDS) if (JSON.stringify(before[field]) !== JSON.stringify(next[field])) {
      edited = editVisualField(edited, field, next[field]);
    }
    setError(null); setNotice(null); setResetPending(false); replace(edited);
  }
  const patchToken = (collection: "palette" | "type_tokens", index: number, patch: Partial<VisualToken>) =>
    setDraft(current => ({ ...current, [collection]: current[collection].map((token, i) => i === index ? { ...token, ...patch } : token) }));
  const removeToken = (collection: "palette" | "type_tokens", index: number) =>
    setDraft(current => ({ ...current, [collection]: current[collection].filter((_, i) => i !== index) }));
  const patchMotion = (index: number, patch: Partial<VisualLanguageInput["motion_grammar"][number]>) =>
    setDraft(current => ({ ...current, motion_grammar: current.motion_grammar.map((verb, i) => i === index ? { ...verb, ...patch } : verb) }));
  const patchFont = (index: number, patch: Partial<FontRight>) =>
    setDraft(current => ({ ...current, fonts: current.fonts.map((font, i) => i === index ? { ...font, ...patch } : font) }));

  async function commitLanguage() {
    if (inFlight.current) return;
    let attempt: ReturnType<typeof prepareVisualSubmission>;
    try { attempt = prepareVisualSubmission(session.current.draft, projectRef.current, crypto.randomUUID()); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return; }
    if (!replace(attempt.draft)) {
      replace(releaseVisualSubmission(session.current.draft, attempt.submission.id));
      setError("The visual submission could not be retained locally. Nothing was sent; restore the backup before committing."); return;
    }
    inFlight.current = true; setPending(true); setError(null); setNotice(null);
    try {
      await commit(attempt.change);
      // The promise alone cannot acknowledge the saved Project snapshot.
      if (mounted.current) observe(projectRef.current);
    } catch (reason) {
      // Keep the original awaiting snapshot across an uncertain response.
      // Recovery is an explicit review, never an automatic replay.
      if (mounted.current) setError((reason instanceof Error ? reason.message : String(reason)) + " Your visual input has been kept.");
    } finally { inFlight.current = false; if (mounted.current) setPending(false); }
  }
  async function toggleStyleLock() {
    if (inFlight.current) return;
    inFlight.current = true; setPending(true); setError(null);
    try { await commit(styleLock ? { type: "remove_lock", lock_id: styleLock.id }
      : { type: "set_lock", resource: "project:" + project.id, kind: "style", note: "Approved visual language" }); }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { inFlight.current = false; if (mounted.current) setPending(false); }
  }
  function resolve(field: VisualDraftField, choice: "saved" | "draft") {
    if (inFlight.current) return;
    replace(resolveVisualField(session.current.draft, projectRef.current, field, choice));
    setError(null); setNotice(choice === "saved" ? "Kept the saved " + VISUAL_FIELD_LABELS[field] + ". Other inputs are unchanged."
      : "Kept your " + VISUAL_FIELD_LABELS[field] + " against r" + project.revision + ". Commit explicitly to apply it.");
    const label = field === "name" ? "Visual system name" : field === "palette" ? "Palette tokens name 1"
      : field === "type_tokens" ? "Type tokens name 1" : field === "motion_grammar" ? "Motion verb name 1"
        : field === "anti_slop_rules" ? "Anti-slop rule 1" : "Font family 1";
    window.setTimeout(() => {
      const target = document.querySelector<HTMLInputElement>('[aria-label="' + label + '"]');
      if (target && !target.disabled) target.focus();
      else focusSavedInput();
    }, 0);
  }
  function restore(entry: VisualRecoveryEntry) {
    if (inFlight.current || !entry.value.draft) return;
    try {
      if (window.localStorage.getItem(entry.key) !== entry.raw) {
        setError("This copy changed after it was shown. Refresh and review its current input before restoring."); refreshCopies(); return;
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "This recovery copy is unavailable."); return; }
    if (visualDraftHasWork(session.current.draft) && !backup(session.current.draft)) {
      setError("Back up your current visual input before restoring another copy."); return;
    }
    session.current.writerId = crypto.randomUUID(); session.current.lastRaw = null;
    replace(reconcileVisualDraft(entry.value.draft, projectRef.current)); refreshCopies();
    setNotice("Restored a separate visual draft. Review conflicts and commit explicitly to apply it.");
  }
  function download(raw: string, name: string, key?: string) {
    // A JSON string preserves the original UTF-16 input, including invalid
    // surrogate code units that a plain UTF-8 Blob would otherwise replace.
    const payload = JSON.stringify({ schema: "motionwright.local-input-copy/1", storage_key: key ?? null, raw }, null, 2);
    const url = URL.createObjectURL(new Blob([payload], { type: "application/json" }));
    const link = document.createElement("a"); link.href = url; link.download = name; link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  function cancelRemoval() {
    const id = removal?.writerId; setRemoval(null);
    if (id) window.setTimeout(() => document.getElementById("visual-copy-remove-" + id)?.focus(), 0);
  }
  function cancelReset() {
    setResetPending(false); window.setTimeout(() => resetButton.current?.focus(), 0);
  }
  function focusSavedInput() {
    window.setTimeout(() => {
      if (systemName.current && !systemName.current.disabled) systemName.current.focus();
      else lockButton.current?.focus();
    }, 0);
  }

  return (
    <section className="visual-language-editor" aria-label="Visual language">
      <div className="visual-language-head">
        <div>
          <span className="canvas-panel-heading inline-heading">Visual system</span>
          <strong>{language.name}</strong>
        </div>
        <span className="revision-chip">v{language.version}</span>
      </div>

      <div className="visual-language-lock">
        <span>
          {styleLock
            ? "Project style is explicitly locked."
            : "Edits create a new visual-language version."}
        </span>
        <button
          type="button"
          className={"button compact" + (styleLock ? " warning" : "")}
          ref={lockButton}
          disabled={pending}
          onClick={() => void toggleStyleLock()}
        >
          {styleLock ? <Unlock size={12} /> : <LockKeyhole size={12} />}
          {styleLock ? "Unlock style" : "Protect style"}
        </button>
      </div>

      <style>{VISUAL_RECOVERY_STYLES}</style>
      <div className="visual-draft-status">
        {dirty && <p role="status">{pending && buffer.awaiting
          ? "Saving this visual snapshot. You can keep editing."
          : "Local visual draft · not yet committed."}</p>}
        {notice && <p className="production-notice" role="status">{notice}</p>}
        {error && <p className="production-error" role="alert">{error}</p>}
        {backupError && <section className="production-notice" role="alert" aria-label="Visual draft backup warning">
          <p>{backupError}</p>
          <div className="visual-recovery-actions">
            <button className="button compact" type="button" onClick={() => { backup(session.current.draft); refreshCopies(); }}>Retry visual draft backup</button>
            <button className="button compact" type="button" disabled={pending} onClick={() => {
              session.current.writerId = crypto.randomUUID(); session.current.lastRaw = null;
              if (replace(session.current.draft)) {
                setError(null); setNotice("Kept your input in a new recovery copy. The existing copy was left unchanged.");
              }
              refreshCopies();
            }}>Keep visual input in a new recovery copy</button>
            <button className="button compact" type="button" onClick={() => download(JSON.stringify(session.current.draft), "visual-draft-" + project.id + ".json")}>Download visual draft</button>
          </div>
        </section>}
        {(earlier.length > 0 || copies.unreadable.length > 0 || copies.limited) && <details>
          <summary>Local visual recovery copies</summary>
          <p>Other windows' input stays separate until you restore a copy.</p>
          <button className="button compact" type="button" onClick={refreshCopies}>Refresh visual recovery copies</button>
          {copies.limited && <p role="status">The recovery scan reached its size or count limit. Existing copies were kept. Download and remove copies you no longer need before inspecting more.</p>}
          {copies.unreadable.map(copy => <div key={copy.key}>
            <p role="status">An unreadable visual copy was kept unchanged.</p>
            <div className="visual-recovery-actions">
              <button className="button compact" type="button" disabled={copy.raw === null} onClick={() => {
                if (copy.raw !== null) download(copy.raw, "visual-unreadable-copy.json", copy.key);
              }} aria-label={"Download unreadable visual draft " + copy.writerId}>Download original copy</button>
              <button className="button compact" type="button" id={"visual-copy-remove-" + copy.writerId} disabled={pending || copy.raw === null}
                onClick={() => { if (copy.raw !== null) setRemoval({ key: copy.key, raw: copy.raw, writerId: copy.writerId }); }}
                aria-label={"Remove unreadable visual draft " + copy.writerId}>Remove copy</button>
            </div>
          </div>)}
          {earlier.map(entry => <div key={entry.key}>
            <p>{new Date(entry.value.updatedAt).toLocaleString()} · {entry.value.draft ? "local visual input" : "clean checkpoint"}</p>
            <div className="visual-recovery-actions">
              {entry.value.draft && <button className="button compact" type="button" disabled={pending} onClick={() => restore(entry)}
                aria-label={"Restore visual draft " + entry.value.writerId}>Restore copy</button>}
              <button className="button compact" type="button" onClick={() => download(entry.raw, "visual-recovery-" + entry.value.writerId + ".json", entry.key)}
                aria-label={"Download visual draft " + entry.value.writerId}>Download copy</button>
              <button className="button compact" type="button" disabled={pending} id={"visual-copy-remove-" + entry.value.writerId}
                onClick={() => setRemoval({ key: entry.key, raw: entry.raw, writerId: entry.value.writerId })}
                aria-label={"Remove visual draft " + entry.value.writerId}>Remove copy</button>
            </div>
          </div>)}
        </details>}
        {removal && <section className="production-notice" role="group" aria-label="Remove local visual copy" onKeyDown={event => {
          if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); cancelRemoval(); }
        }}>
          <p>Remove this local copy? Download it first if you need to keep its input.</p>
          <div className="visual-recovery-actions">
            <button className="button compact" type="button" ref={removeCancel} onClick={cancelRemoval}>Keep visual copy</button>
            <button className="button compact warning" type="button" disabled={pending} onClick={() => {
              try { discardVisualRecovery(window.localStorage, removal.key, removal.raw); setRemoval(null); refreshCopies(); focusSavedInput();
                setNotice("Removed the selected local copy. Saved project revisions are unchanged."); }
              catch (reason) { setError(reason instanceof Error ? reason.message : "The local copy could not be removed."); refreshCopies(); }
            }}>Confirm remove visual copy</button>
          </div>
        </section>}
        {buffer.awaiting && !pending && <section className="production-notice" role="region" aria-label="Unconfirmed visual submission">
          <p>A previous submission has no matching observed saved revision. Reopen the saved project and review this input before committing again.</p>
          <button className="button compact" type="button" onClick={() => {
            const current = session.current.draft;
            if (current.awaiting) replace(reconcileVisualDraft(releaseVisualSubmission(current, current.awaiting.id), projectRef.current));
            setError(null); setNotice("Reviewed the retained input against r" + projectRef.current.revision + ". Resolve overlaps and commit explicitly.");
          }}>Review visual draft on current revision</button>
        </section>}
        {buffer.awaiting && pending && <section className="production-notice" role="region" aria-label="Pending visual submission">
          <p>If the response does not arrive, reopen Studio to review the saved project. Reloading does not cancel the save or resend this input.</p>
          <button className="button compact" type="button" onClick={() => window.location.reload()}>Reload and review saved visual system</button>
        </section>}
        {conflicts.map(conflict => <section className="production-notice" role="region" aria-label={"Visual conflict: " + VISUAL_FIELD_LABELS[conflict.field]} key={conflict.field}>
          <strong>Review {VISUAL_FIELD_LABELS[conflict.field]}</strong>
          <p>{conflict.futureRevision ? "This input came from a later revision. Choose how to apply it to the currently open revision."
            : "The saved project and your draft both changed this group. Choose which values to keep."}</p>
          <dl><dt>Before</dt><dd>{describeVisualGroup(conflict.field, conflict.before)}</dd>
            <dt>Saved now</dt><dd>{describeVisualGroup(conflict.field, conflict.current)}</dd>
            <dt>Your input</dt><dd>{describeVisualGroup(conflict.field, conflict.proposed)}</dd></dl>
          <div className="visual-recovery-actions">
            <button className="button compact" type="button" disabled={pending} onClick={() => resolve(conflict.field, "saved")}>Use saved {VISUAL_FIELD_LABELS[conflict.field]}</button>
            <button className="button compact" type="button" disabled={pending} onClick={() => resolve(conflict.field, "draft")}>Keep my {VISUAL_FIELD_LABELS[conflict.field]}</button>
          </div>
        </section>)}
        {resetPending && <section className="production-notice" role="group" aria-label="Reset local visual input" onKeyDown={event => {
          if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); cancelReset(); }
        }}>
          <p>Replace the current local input with the saved visual system?</p>
          <div className="visual-recovery-actions">
            <button className="button compact" type="button" ref={resetCancel} onClick={cancelReset}>Keep visual input</button>
            <button className="button compact" type="button" disabled={pending} onClick={() => {
              if (dirty && !backup(session.current.draft)) { setError("Back up the current visual input before resetting it."); return; }
              // A fresh clean session leaves the previous input recoverable.
              session.current.writerId = crypto.randomUUID(); session.current.lastRaw = null;
              const clean = createVisualDraft(projectRef.current);
              session.current.retainClean = true;
              const retained = replace(clean);
              setResetPending(false); setError(null); refreshCopies(); focusSavedInput();
              setNotice(retained ? "Showing the saved visual system. Your earlier local input remains a recovery copy."
                : "Showing the saved visual system in this session. Restore the backup before reloading to keep this reset.");
            }}>Reset to saved visual system</button>
          </div>
        </section>}
      </div>

      <label className="visual-field">
        <span className="field-label">System name</span>
        <input
          aria-label="Visual system name"
          ref={systemName}
          value={draft.name}
          disabled={Boolean(styleLock)}
          maxLength={256}
          onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))}
        />
      </label>

      <TokenSection
        title="Palette tokens"
        collection={draft.palette}
        disabled={Boolean(styleLock)}
        onPatch={(index, patch) => patchToken("palette", index, patch)}
        onRemove={(index) => removeToken("palette", index)}
        onAdd={() =>
          setDraft((current) => ({
            ...current,
            palette: [...current.palette, emptyToken()],
          }))
        }
      />

      <TokenSection
        title="Type tokens"
        collection={draft.type_tokens}
        disabled={Boolean(styleLock)}
        onPatch={(index, patch) => patchToken("type_tokens", index, patch)}
        onRemove={(index) => removeToken("type_tokens", index)}
        onAdd={() =>
          setDraft((current) => ({
            ...current,
            type_tokens: [...current.type_tokens, emptyToken()],
          }))
        }
      />

      <div className="visual-editor-section">
        <div className="visual-section-title">
          <span>Motion grammar</span>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add motion verb"
            disabled={Boolean(styleLock)}
            onClick={() =>
              setDraft((current) => ({
                ...current,
                motion_grammar: [...current.motion_grammar, emptyMotion()],
              }))
            }
          >
            <Plus size={13} />
          </button>
        </div>
        <div className="visual-list">
          {draft.motion_grammar.map((verb, index) => (
            <div className="motion-row" key={index}>
              <input
                aria-label={"Motion verb name " + (index + 1)}
                placeholder="verb"
                value={verb.name}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchMotion(index, { name: event.target.value })}
              />
              <input
                aria-label={"Motion verb duration " + (index + 1)}
                type="text"
                inputMode="numeric"
                value={verb.duration_ms}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  patchMotion(index, { duration_ms: event.target.value })
                }
              />
              <select
                aria-label={"Reduced motion behavior " + (index + 1)}
                value={verb.reduced_motion}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  patchMotion(index, {
                    reduced_motion: event.target.value as MotionVerb["reduced_motion"],
                  })
                }
              >
                <option value="instant">Instant</option>
                <option value="fade_only">Fade only</option>
                <option value="static_equivalent">Static equivalent</option>
              </select>
              <button
                type="button"
                className="plain-icon"
                aria-label={"Remove motion verb " + (index + 1)}
                disabled={Boolean(styleLock)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    motion_grammar: current.motion_grammar.filter(
                      (_, verbIndex) => verbIndex !== index,
                    ),
                  }))
                }
              >
                <Trash2 size={12} />
              </button>
              <input
                className="motion-meaning"
                aria-label={"Motion verb meaning " + (index + 1)}
                placeholder="semantic meaning"
                value={verb.meaning}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchMotion(index, { meaning: event.target.value })}
              />
            </div>
          ))}
          {draft.motion_grammar.length === 0 && (
            <span className="visual-empty">No motion verbs defined.</span>
          )}
        </div>
      </div>

      <div className="visual-editor-section">
        <div className="visual-section-title">
          <span>Anti-slop rules</span>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add anti-slop rule"
            disabled={Boolean(styleLock)}
            onClick={() =>
              setDraft((current) => ({
                ...current,
                anti_slop_rules: [...current.anti_slop_rules, ""],
              }))
            }
          >
            <Plus size={13} />
          </button>
        </div>
        <div className="visual-list">
          {draft.anti_slop_rules.map((rule, index) => (
            <div className="rule-row" key={index}>
              <input
                aria-label={"Anti-slop rule " + (index + 1)}
                value={rule}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    anti_slop_rules: current.anti_slop_rules.map((item, ruleIndex) =>
                      ruleIndex === index ? event.target.value : item,
                    ),
                  }))
                }
              />
              <button
                type="button"
                className="plain-icon"
                aria-label={"Remove anti-slop rule " + (index + 1)}
                disabled={Boolean(styleLock)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    anti_slop_rules: current.anti_slop_rules.filter(
                      (_, ruleIndex) => ruleIndex !== index,
                    ),
                  }))
                }
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      </div>

      <div className="visual-editor-section">
        <div className="visual-section-title">
          <span>Font rights</span>
          <button
            type="button"
            className="plain-icon"
            aria-label="Add font right"
            disabled={Boolean(styleLock)}
            onClick={() =>
              setDraft((current) => ({
                ...current,
                fonts: [...current.fonts, emptyFont()],
              }))
            }
          >
            <Plus size={13} />
          </button>
        </div>
        <div className="visual-list">
          {draft.fonts.map((font, index) => (
            <div className="font-row" key={index}>
              <input
                aria-label={"Font family " + (index + 1)}
                placeholder="family"
                value={font.family}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchFont(index, { family: event.target.value })}
              />
              <input
                aria-label={"Font source " + (index + 1)}
                placeholder="source / license reference"
                value={font.source}
                disabled={Boolean(styleLock)}
                onChange={(event) => patchFont(index, { source: event.target.value })}
              />
              <select
                aria-label={"Font rights status " + (index + 1)}
                value={font.rights_status}
                disabled={Boolean(styleLock)}
                onChange={(event) =>
                  patchFont(index, {
                    rights_status: event.target.value as FontRight["rights_status"],
                  })
                }
              >
                <option value="unknown">Unknown</option>
                <option value="cleared">Cleared</option>
                <option value="restricted">Restricted</option>
              </select>
              <button
                type="button"
                className="plain-icon"
                aria-label={"Remove font right " + (index + 1)}
                disabled={Boolean(styleLock)}
                onClick={() =>
                  setDraft((current) => ({
                    ...current,
                    fonts: current.fonts.filter((_, fontIndex) => fontIndex !== index),
                  }))
                }
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {draft.fonts.length === 0 && (
            <span className="visual-empty">No font rights records attached.</span>
          )}
        </div>
      </div>

      <div className="visual-language-actions">
        <button
          type="button"
          className="button"
          ref={resetButton}
          disabled={!dirty || pending}
          onClick={() => setResetPending(true)}
        >
          Reset
        </button>
        <button
          type="button"
          className="button button-primary"
          disabled={!dirty || Boolean(issue) || Boolean(backupError) || pending}
          onClick={() => void commitLanguage()}
        >
          Commit visual system
        </button>
      </div>
      {issue && dirty && <p className="visual-validation" role="status">{issue}</p>}
    </section>
  );
}

function TokenSection({
  title,
  collection,
  disabled,
  onPatch,
  onRemove,
  onAdd,
}: {
  title: string;
  collection: VisualToken[];
  disabled: boolean;
  onPatch: (index: number, patch: Partial<VisualToken>) => void;
  onRemove: (index: number) => void;
  onAdd: () => void;
}) {
  return (
    <div className="visual-editor-section">
      <div className="visual-section-title">
        <span>{title}</span>
        <button
          type="button"
          className="plain-icon"
          aria-label={"Add " + title.toLowerCase()}
          disabled={disabled}
          onClick={onAdd}
        >
          <Plus size={13} />
        </button>
      </div>
      <div className="visual-list">
        {collection.map((token, index) => (
          <div className="token-row" key={index}>
            <input
              aria-label={title + " name " + (index + 1)}
              placeholder="token"
              value={token.name}
              disabled={disabled}
              onChange={(event) => onPatch(index, { name: event.target.value })}
            />
            <input
              aria-label={title + " value " + (index + 1)}
              placeholder="value"
              value={token.value}
              disabled={disabled}
              onChange={(event) => onPatch(index, { value: event.target.value })}
            />
            <button
              type="button"
              className="plain-icon"
              aria-label={"Remove " + title.toLowerCase() + " " + (index + 1)}
              disabled={disabled}
              onClick={() => onRemove(index)}
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        {collection.length === 0 && (
          <span className="visual-empty">No tokens defined.</span>
        )}
      </div>
    </div>
  );
}
