import { Clock3, Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import {
  acknowledgeSceneBeatDraft, BEAT_RECOVERY_PREFIX, beatCopyKey, beatHasInput, beatHasWork, beatLocked, beatRaw,
  beatOutcomeMatches, beatPointerKey, beatProjectScope, beatScopeId, beatScopeProject, beatScopeScene,
  beatTarget, beatUnconfirmed, createNewSceneBeatDraft, createSceneBeatDraft, decodeBeatCopy, decodeBeatFile, MAX_BEAT_COPY_BYTES,
  editSceneBeatDraft, encodeBeatCopy, inspectSceneBeatOutcome, prepareSceneBeatSubmission, readBeatCopies,
  reconcileSceneBeatDraft, releaseReviewedSceneBeat, resolveSceneBeatDraft, restoreSceneBeatDraft, retainReviewedBeatIntent,
  retainSceneBeatFailure, sameBeat, sceneBeatConflict, sceneBeatDraftValue, sceneBeatScope, writeBeatCopy,
} from "./sceneBeatDraft";
import type { BeatCopy, BeatCopyEntry, BeatCustody, BeatField, BeatOperation, BeatOutcomeReview, SceneBeatDraft } from "./sceneBeatDraft";
import { formatBeatTime, inspectBeatCoverage, inspectBeatTiming } from "./sceneBeatTiming";
import type { Beat, Change, Project, RationalTime, Scene } from "./types";
import { seconds } from "./types";
import { formatEditorTimecode as formatTime, type TimecodeMode } from "./timecode";

type Commit = (change: Change) => Promise<Project>;
type Session = {
  draft: SceneBeatDraft;
  custody: BeatCustody;
  inFlight: boolean;
  backupError: string | null;
  error: string | null;
  notice: string | null;
};
// Author buffers survive workspace navigation even during a local-storage
// outage. They never own saved Project state or replay an operation.
const sessions = new Map<string, Session>();
const listeners = new Map<string, Set<() => void>>();
let protectsUnbackedInput = false;
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
function protectUnbackedInput() {
  if (protectsUnbackedInput) return;
  protectsUnbackedInput = true;
  // Keep the warning while the editor is unmounted too: a failed backup must
  // not become invisible merely because the author visited another workspace.
  window.addEventListener("beforeunload", event => {
    if ([...sessions.values()].some(session => session.backupError && beatHasWork(session.draft))) {
      event.preventDefault(); event.returnValue = "";
    }
  });
}
function notify(session: Session) { listeners.get(beatScopeProject(session.draft.scope))?.forEach(listener => listener()); }
function projectBusy(project: Project) {
  const owner = beatProjectScope(project);
  return [...sessions.values()].some(session => session.inFlight && beatScopeProject(session.draft.scope) === owner);
}
function forkWriter(session: Session) {
  session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
}
function backup(session: Session): boolean {
  try {
    if (session.custody.expectedRaw !== null) {
      const earlier = decodeBeatCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!beatHasWork(earlier) && JSON.stringify(earlier) !== JSON.stringify(session.draft)) forkWriter(session);
    }
    writeBeatCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.backupError = null;
    return true;
  } catch (reason) {
    session.backupError = message(reason) + " Current input remains in this editor. Confirm a recovery copy before saving; earlier copies were kept.";
    return false;
  }
}
function replace(session: Session, draft: SceneBeatDraft, persist = true) {
  session.draft = draft;
  const confirmed = persist ? backup(session) : true;
  notify(session);
  return confirmed;
}
function sessionFor(project: Project, sceneId: string, beatId: string, explicit?: SceneBeatDraft): Session {
  protectUnbackedInput();
  const scope = sceneBeatScope(project, sceneId, beatId), cached = sessions.get(scope);
  if (cached) return cached;
  let pointer: string | null = null, recovered: SceneBeatDraft | null = null, backupError: string | null = null;
  try {
    pointer = window.sessionStorage.getItem(beatPointerKey(scope));
    if (pointer) {
      const raw = window.localStorage.getItem(beatCopyKey(scope, pointer));
      if (raw === null) throw new Error("This tab's beat pointer references a missing recovery copy.");
      recovered = decodeBeatCopy(raw, scope, pointer).draft;
    }
  } catch (reason) { backupError = message(reason); }
  const source = explicit ?? recovered ?? createSceneBeatDraft(project, sceneId, beatId);
  const session: Session = {
    draft: reconcileSceneBeatDraft(source, project),
    // A cloned tab can inherit its pointer but never the mutable writer key.
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    inFlight: false, backupError, error: null,
    notice: recovered ? "Recovered this tab's beat input. No save was replayed." : null,
  };
  sessions.set(scope, session);
  return session;
}
function downloadRaw(raw: string, name: string) {
  const url = URL.createObjectURL(new Blob([raw], { type: "application/json" })), link = document.createElement("a");
  link.href = url; link.download = name; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
function downloadDraft(session: Session) {
  const raw = encodeBeatCopy({ schema: "motionwright.scene-beat-recovery/1", scope: session.draft.scope,
    writerId: session.custody.writerId, updatedAt: Date.now(), draft: session.draft });
  downloadRaw(raw, "motionwright-beat-draft-" + session.draft.beatId + ".json");
}
function BeatValues({ value }: { value: SceneBeatDraft["raw"] | null }) {
  if (!value) return <span>Removed</span>;
  return <dl style={{ overflowWrap: "anywhere" }}><dt>Label</dt><dd style={{ whiteSpace: "pre-wrap" }}>{value.label}</dd>
    <dt>Objective</dt><dd style={{ whiteSpace: "pre-wrap" }}>{value.objective || "No objective"}</dd>
    <dt>Start</dt><dd>{value.start} seconds</dd><dt>Duration</dt><dd>{value.duration} seconds</dd></dl>;
}
async function submit(session: Session, project: Project, commit: Commit, operation: BeatOperation) {
  if (projectBusy(project) || beatLocked(project, session.draft.sceneId) || beatUnconfirmed(session.draft)) return;
  const id = crypto.randomUUID();
  let change: ReturnType<typeof prepareSceneBeatSubmission>["change"];
  try {
    if (!backup(session)) { notify(session); return; }
    const prepared = prepareSceneBeatSubmission(session.draft, project, operation, id);
    change = prepared.change;
    session.error = null; session.notice = null;
    if (!replace(session, prepared.draft)) {
      // This is live knowledge before any coordinator call. Recovery of the
      // same record still treats NOT_SENT as UNKNOWN, never as replay authority.
      replace(session, retainSceneBeatFailure(session.draft, id, session.backupError ?? "Recovery is unconfirmed.", true), false);
      session.error = "NOT_SENT: the beat recovery copy or writer pointer was not confirmed.";
      notify(session); return;
    }
  } catch (reason) { session.error = message(reason); notify(session); return; }
  session.inFlight = true; notify(session);
  try {
    const receipt = await commit(change);
    if (session.draft.pending?.id !== id) return;
    const confirmed = acknowledgeSceneBeatDraft(session.draft, id, receipt);
    // Never overwrite the armed original while publishing a confirmation.
    // A failed new copy or pointer leaves that original UNKNOWN record intact.
    forkWriter(session);
    session.error = null;
    session.notice = (operation === "remove" ? "Removed beat" : "Saved beat") + " at r" + receipt.revision + "."
      + (beatHasInput(confirmed) ? " Your later input is still available." : "");
    replace(session, confirmed);
  } catch (reason) {
    session.error = message(reason);
    replace(session, retainSceneBeatFailure(session.draft, id, session.error));
  } finally { session.inFlight = false; notify(session); }
}

interface Props {
  project: Project;
  scene: Scene;
  contentLocked: boolean;
  timingLocked: boolean;
  /** Existing App coordinator, preserving the original Project response. */
  commit: Commit;
  rate: RationalTime;
  mode: TimecodeMode;
}
export default function SceneBeatEditor({ project, scene, contentLocked, timingLocked, commit, rate, mode }: Props) {
  const [, redraw] = useState(0);
  const projectRef = useRef(project); projectRef.current = project;
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [fileCandidate, setFileCandidate] = useState<{ raw: string; name: string; copy: BeatCopy } | null>(null);
  const importSequence = useRef(0), mounted = useRef(true);
  const owner = beatProjectScope(project);
  const fileScope = useRef({ owner, sceneId: scene.id }); fileScope.current = { owner, sceneId: scene.id };
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; importSequence.current++; }; }, []);
  useEffect(() => { importSequence.current++; setFileCandidate(null); }, [owner, scene.id]);
  useEffect(() => {
    const changed = () => redraw(value => value + 1);
    const subscribers = listeners.get(owner) ?? new Set<() => void>();
    subscribers.add(changed); listeners.set(owner, subscribers);
    const storageChanged = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(BEAT_RECOVERY_PREFIX)) changed(); };
    window.addEventListener("storage", storageChanged);
    return () => {
      subscribers.delete(changed); if (!subscribers.size) listeners.delete(owner);
      window.removeEventListener("storage", storageChanged);
    };
  }, [owner]);
  const canonicalScene = project.scenes.find(item => item.id === scene.id);
  if (!canonicalScene) return <section className="scene-beat-editor" role="alert">The selected scene is no longer saved. Its retained beat copies are kept under their original scene identity.</section>;
  const locked = contentLocked || timingLocked || beatLocked(project, scene.id), busy = projectBusy(project);
  const coverage = inspectBeatCoverage(canonicalScene.beats, canonicalScene.duration);
  const savedBeats = coverage.ok ? coverage.beats : canonicalScene.beats;
  let catalog: ReturnType<typeof readBeatCopies> = { entries: [], limited: false }, readError: string | null = null;
  try { catalog = readBeatCopies(window.localStorage, project, scene.id); } catch (reason) { readError = message(reason); }
  const rowIds = new Set(savedBeats.map(beat => beat.id));
  for (const value of sessions.values()) if (beatScopeProject(value.draft.scope) === owner && value.draft.sceneId === scene.id && beatHasWork(value.draft)) rowIds.add(value.draft.beatId);
  // Only this tab's pointer may automatically recover a removed beat's draft.
  // Other writers remain explicit choices in the recovery drawer.
  for (const entry of catalog.entries) {
    if (!entry.value || !beatHasWork(entry.value.draft)) continue;
    try {
      if (window.sessionStorage.getItem(beatPointerKey(entry.scope)) === entry.writerId) {
        sessionFor(project, scene.id, beatScopeId(entry.scope)); rowIds.add(beatScopeId(entry.scope));
      }
    } catch { /* The original entry remains visible for explicit recovery. */ }
  }
  const rows = [...rowIds].map(id => sessionFor(project, scene.id, id));
  const firstGap = coverage.ok ? coverage.firstGap : null;
  const covered = coverage.ok && (savedBeats.length === 0 || coverage.exact);

  function fillGap() {
    const current = projectRef.current;
    if (projectBusy(current) || locked) return;
    try {
      const selected = current.scenes.find(item => item.id === scene.id);
      if (!selected) throw new Error("The selected scene is no longer saved.");
      const actual = inspectBeatCoverage(selected.beats, selected.duration);
      if (!actual.ok || !actual.firstGap || selected.beats.length >= 32) throw new Error("Review the current saved timing before filling its gap.");
      const value: Beat = { id: crypto.randomUUID(), label: "New beat", objective: "",
        start: actual.firstGap.start, duration: actual.firstGap.duration };
      const draft = createNewSceneBeatDraft(current, selected.id, value), session = sessionFor(current, selected.id, value.id, draft);
      notify(session); void submit(session, current, commit, "upsert");
    } catch (reason) { setCatalogError(message(reason)); }
  }
  function restore(entry: BeatCopyEntry) {
    if (busy || !entry.value) return;
    try {
      const current = projectRef.current;
      if (beatScopeProject(entry.scope) !== beatProjectScope(current) || beatScopeScene(entry.scope) !== scene.id)
        throw new Error("This copy belongs to another editing scope.");
      const existing = sessions.get(entry.scope);
      if (existing?.inFlight || (existing && beatUnconfirmed(existing.draft))) throw new Error("Review the active unconfirmed beat attempt before restoring another copy.");
      if (existing && beatHasWork(existing.draft) && !backup(existing)) { notify(existing); return; }
      const raw = window.localStorage.getItem(entry.key);
      if (raw !== entry.raw || raw === null) throw new Error("This recovery copy changed. Read the current list before restoring it.");
      const copy = decodeBeatCopy(raw, entry.scope, entry.writerId);
      const session = existing ?? sessionFor(current, scene.id, copy.draft.beatId, copy.draft);
      forkWriter(session);
      session.error = null; session.notice = "Restored a separate beat copy. No Project change was sent.";
      replace(session, restoreSceneBeatDraft(copy.draft, current)); setCatalogError(null);
    } catch (reason) { setCatalogError(message(reason)); }
  }
  function discard(entry: BeatCopyEntry) {
    if (busy) return;
    try {
      if (sessions.get(entry.scope)?.custody.writerId === entry.writerId
        || window.sessionStorage.getItem(beatPointerKey(entry.scope)) === entry.writerId)
        throw new Error("The active recovery copy is retained. Choose another copy to remove.");
      if (window.localStorage.getItem(entry.key) !== entry.raw) throw new Error("This copy changed. Review it before discarding.");
      window.localStorage.removeItem(entry.key);
      if (window.localStorage.getItem(entry.key) !== null) throw new Error("The copy removal was not confirmed.");
      setCatalogError(null); redraw(value => value + 1);
    } catch (reason) { setCatalogError(message(reason)); }
  }
  async function readFile(file: File | undefined) {
    if (!file) return;
    const sequence = ++importSequence.current, selectedOwner = owner, selectedScene = scene.id;
    try {
      if (file.size > MAX_BEAT_COPY_BYTES) throw new Error("The beat recovery file exceeds 128 KiB.");
      const raw = await file.text();
      if (!mounted.current || sequence !== importSequence.current || fileScope.current.owner !== selectedOwner || fileScope.current.sceneId !== selectedScene) return;
      const copy = decodeBeatFile(raw, projectRef.current, selectedScene);
      setFileCandidate({ raw, name: file.name, copy }); setCatalogError(null);
    } catch (reason) { if (mounted.current && sequence === importSequence.current) setCatalogError(message(reason)); }
  }
  function useFile() {
    if (busy || !fileCandidate) return;
    try {
      const current = projectRef.current, copy = decodeBeatFile(fileCandidate.raw, current, scene.id);
      const existing = sessions.get(copy.scope);
      if (existing?.inFlight || (existing && beatUnconfirmed(existing.draft)))
        throw new Error("Review the active unconfirmed beat attempt before replacing it with a file.");
      if (existing && beatHasWork(existing.draft) && !backup(existing)) {
        setCatalogError("The current beat input could not be backed up. Both inputs remain available. Download the current draft and restore browser recovery before replacing it.");
        notify(existing); return;
      }
      const session = existing ?? sessionFor(current, scene.id, copy.draft.beatId, copy.draft);
      forkWriter(session); session.error = null;
      session.notice = "Imported a separate editable beat copy. Earlier input was kept. No Project change was sent."
        + (beatUnconfirmed(copy.draft) ? " Its unconfirmed operation still requires saved-project and history review." : "");
      replace(session, restoreSceneBeatDraft(copy.draft, current)); setFileCandidate(null); setCatalogError(null);
    } catch (reason) { setCatalogError(message(reason)); }
  }
  return <section className="scene-beat-editor" aria-label="Scene beats">
    <div className="scene-beat-editor-head"><div><strong>Scene beats</strong>
      <span>{savedBeats.length} authored · {formatTime(seconds(canonicalScene.duration), rate, mode)}</span></div>
      <span className={"beat-coverage " + (covered ? "ready" : "needs-work")}>
        {savedBeats.length === 0 && coverage.ok ? "scene span" : covered ? "timing covered" : "timing gap"}</span></div>
    {!coverage.ok && <p className="inspector-empty" role="alert" aria-label="Scene beat coverage validation">{coverage.message}</p>}
    {!rows.length && <p className="inspector-empty">No authored beats. Native Film currently preserves this as one full-scene shot.</p>}
    <div className="scene-beat-list">{rows.map(session => <SceneBeatRow key={session.draft.scope} session={session}
      project={project} scene={canonicalScene} locked={locked} commit={commit} rate={rate} mode={mode} />)}</div>
    <button type="button" className="button full" disabled={locked || busy || !firstGap || savedBeats.length >= 32}
      title={firstGap ? "Fill the first uncovered interval" : "No uncovered timing gap remains"} onClick={fillGap}>
      <Plus size={12} /> Fill timing gap</button>
    <div className="renderer-note"><Clock3 size={13} />Authored beats may be incomplete while editing. Full coverage requires exact, non-overlapping beats; native render limits are checked at dispatch.</div>
    <details role="region" aria-label="Beat recovery files"><summary>Recover a beat from a file</summary>
      <p>Import a downloaded beat copy for this project generation, branch and scene, up to 128 KiB. Import never saves or retries a Project change.</p>
      <label>Import beat draft file<input type="file" accept="application/json,.json" aria-label="Import beat draft file"
        onChange={event => { const file = event.target.files?.[0]; event.currentTarget.value = ""; void readFile(file); }} /></label>
      {fileCandidate && <section role="region" aria-label="Imported beat candidate"><h4>{fileCandidate.name}</h4>
        <BeatValues value={fileCandidate.copy.draft.raw} />
        {beatUnconfirmed(fileCandidate.copy.draft) && <p>The imported original outcome is still unconfirmed.</p>}
        <button type="button" className="button compact" disabled={busy} onClick={useFile}>Use imported beat copy</button>
        <button type="button" className="button compact" onClick={() => setFileCandidate(null)}>Discard imported beat candidate</button>
      </section>}
    </details>
    {(catalogError || readError) && <p role="alert" aria-label="Beat recovery list warning">{catalogError ?? readError}</p>}
    {(catalog.entries.length > 0 || catalog.limited) && <details role="region" aria-label="Beat recovery copies">
      <summary>Local beat recovery copies</summary><p>Copies belong to this project generation, branch, scene and beat. Restoring never saves or recreates a beat.</p>
      {catalog.limited && <p role="status">The recovery list reached its inspection limit. No copy was removed automatically.</p>}
      {catalog.entries.map((entry, index) => {
        let active = sessions.get(entry.scope)?.custody.writerId === entry.writerId;
        try { active ||= window.sessionStorage.getItem(beatPointerKey(entry.scope)) === entry.writerId; }
        catch { active = true; }
        return <div role="group" aria-label={"Beat recovery copy " + (index + 1)} key={entry.key}>
          <p>{entry.value ? entry.value.draft.raw.label + " · " + new Date(entry.value.updatedAt).toLocaleString()
            + (beatUnconfirmed(entry.value.draft) ? " · Original unconfirmed attempt" : " · Local input") : "Unreadable copy; original bytes were preserved."}</p>
          {entry.value && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{entry.value.draft.raw.objective.slice(0, 180)}
            {"\n" + entry.value.draft.raw.start + " → +" + entry.value.draft.raw.duration}</pre>}
          {entry.value && <button type="button" className="button compact" disabled={busy || active}
            aria-label={"Restore beat copy " + (index + 1)} onClick={() => restore(entry)}>Restore copy</button>}
          <button type="button" className="button compact" aria-label={"Download beat copy " + (index + 1)}
            onClick={() => downloadRaw(entry.raw, "motionwright-beat-copy-" + entry.writerId + ".json")}>Download copy</button>
          <button type="button" className="button compact" disabled={busy || active}
            aria-label={"Discard beat copy " + (index + 1)} onClick={() => discard(entry)}>Discard this copy</button>
        </div>;
      })}
    </details>}
  </section>;
}

function SceneBeatRow({ session, project, scene, locked, commit, rate, mode }: {
  session: Session; project: Project; scene: Scene; locked: boolean; commit: Commit; rate: RationalTime; mode: TimecodeMode;
}) {
  const projectRef = useRef(project), mounted = useRef(true); projectRef.current = project;
  const [review, setReview] = useState<BeatOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const next = reconcileSceneBeatDraft(session.draft, project);
    if (next !== session.draft) replace(session, next, beatHasWork(session.draft) || session.custody.expectedRaw !== null);
  }, [project, session]);
  const { draft } = session, target = beatTarget(project, draft.sceneId, draft.beatId), busy = projectBusy(project);
  const conflict = sceneBeatConflict(draft, project), hintId = "beat-timing-" + draft.beatId;
  let proposed: Beat | null = null, validation: string | null = null;
  try { proposed = sceneBeatDraftValue(draft, scene.duration); } catch (reason) { validation = message(reason); }
  const timing = inspectBeatTiming(draft.base ?? { start: { num: "0", den: "1" }, duration: { num: "1", den: "1" } }, draft.raw, scene.duration);
  const blocked = locked || busy || session.backupError !== null || beatUnconfirmed(draft) || conflict !== null || !proposed || sameBeat(proposed, target.beat);

  function edit(field: BeatField, raw: string) {
    session.error = null; session.notice = null; setReview(null);
    replace(session, editSceneBeatDraft(session.draft, field, raw));
  }
  function choose(choice: "saved" | "draft" | "text") {
    if (busy || beatUnconfirmed(session.draft)) return;
    try {
      if (!backup(session)) { notify(session); return; }
      const next = resolveSceneBeatDraft(session.draft, projectRef.current, choice);
      forkWriter(session); session.error = null;
      session.notice = choice === "saved" ? "Kept the saved beat state. Earlier author input remains in a separate copy."
        : choice === "text" ? "Kept your text with the saved timing. Save this new intent explicitly."
          : "Kept your draft against the reviewed saved state. Save or recreate it explicitly.";
      replace(session, next); setReview(null);
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  async function readOutcome() {
    if (busy || reviewing || !beatUnconfirmed(session.draft)) return;
    setReviewing(true); setReview(null); session.error = null; notify(session);
    try {
      const observed = await inspectSceneBeatOutcome(session.draft, projectRef.current, {
        readProject: async id => {
          const boot = await bootstrap(id);
          if (!boot.project || boot.project_selection.status !== "selected") throw new Error("The saved beat project is unavailable.");
          return boot.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current) return;
      if (!beatOutcomeMatches(session.draft, projectRef.current, observed)) throw new Error("The beat scope or saved state changed during review. Inspect it again.");
      setReview(observed);
    } catch (reason) { if (mounted.current) { session.error = message(reason); notify(session); } }
    finally { if (mounted.current) setReviewing(false); }
  }
  function continueAfterReview() {
    if (busy || !review) return;
    try {
      const next = releaseReviewedSceneBeat(session.draft, projectRef.current, review);
      if (!backup(session)) { notify(session); return; }
      const custody = retainReviewedBeatIntent(window.localStorage, window.sessionStorage,
        session.draft, next, session.custody, crypto.randomUUID());
      session.custody = custody; session.backupError = null; session.error = null;
      session.notice = "The earlier unconfirmed attempt remains in a separate copy. No request was retried or acknowledged. Compare any saved changes before a new save.";
      replace(session, next, false); setReview(null);
    } catch (reason) {
      session.backupError = message(reason);
      session.error = session.backupError + " Earlier submitted and later author input remain retained."; notify(session);
    }
  }
  return <div className="scene-beat-row" data-beat-id={draft.beatId} data-beat-scope={draft.scope}>
    <div className="scene-beat-row-head"><input aria-label="Beat label" value={draft.raw.label} disabled={locked} maxLength={160}
      onChange={event => edit("label", event.target.value)} />
      <button type="button" className="plain-icon" aria-label={"Remove beat " + (target.beat?.label ?? draft.raw.label)}
        disabled={locked || busy || !target.beat || beatUnconfirmed(draft) || conflict !== null || session.backupError !== null}
        onClick={() => { setReview(null); void submit(session, projectRef.current, commit, "remove"); }}><X size={12} /></button></div>
    <textarea aria-label="Beat objective" value={draft.raw.objective} disabled={locked} rows={2} maxLength={4000}
      onChange={event => edit("objective", event.target.value)} />
    <div className="scene-beat-time-grid">{(["start", "duration"] as const).map(field => <label key={field}>
      <span>{field === "start" ? "Start" : "Duration"}</span><input aria-label={"Beat " + field + " seconds"} type="text" inputMode="decimal" maxLength={96}
        value={draft.raw[field]} disabled={locked} aria-invalid={!timing.ok} aria-describedby={hintId}
        onChange={event => edit(field, event.target.value)} /></label>)}</div>
    <div id={hintId} className="renderer-note">{validation ? <span role="alert" aria-label="Beat timing validation">{validation}</span>
      : "Seconds accept decimals or fractions. Text edits preserve exact saved times."}</div>
    <div className="scene-beat-row-foot"><span className="mono">{timing.ok
      ? formatTime(seconds(timing.start), rate, mode) + " → " + formatTime(seconds(timing.start) + seconds(timing.duration), rate, mode)
      : "Complete both times to save"}</span>
      <button type="button" className="button compact" disabled={blocked}
        onClick={() => { setReview(null); void submit(session, projectRef.current, commit, "upsert"); }}>{target.beat ? "Save beat" : "Recreate beat"}</button></div>
    {locked && <p role="status">Content or timing is locked. Your retained draft is kept.</p>}
    {!target.beat && <p role="status">This beat is absent from the saved scene. Retained input never recreates it automatically.</p>}
    {session.notice && <p className="production-notice" role="status">{session.notice}</p>}
    {session.error && <p className="production-error" role="alert">{session.error}</p>}
    {session.backupError && <section className="production-notice" role="alert" aria-label="Beat backup warning">
      <strong>Local recovery needs attention.</strong><p>{session.backupError}</p>
      <button type="button" className="button compact" onClick={() => { backup(session); notify(session); }}>Retry beat backup</button></section>}
    <button type="button" className="button compact" onClick={() => downloadDraft(session)}>Download beat draft</button>
    {draft.pending?.outcome === "NOT_SENT" && <p role="status">The last preparation was not sent. Confirm its backup before another explicit save.</p>}
    {beatUnconfirmed(draft) && draft.pending && <section className="production-notice" role="region" aria-label="Unconfirmed beat operation">
      <strong>Unconfirmed {draft.pending.operation === "remove" ? "removal" : "save"} from r{draft.pending.revision}</strong>
      <p>The saved project may contain the operation. Submitted values and later input are retained. Nothing is replayed automatically.</p>
      {draft.pending.diagnostic && <p>{draft.pending.diagnostic}</p>}
      <button type="button" className="button compact" disabled={busy || reviewing} onClick={() => window.location.reload()}>Reload to inspect saved beat</button>
      <button type="button" className="button compact" disabled={busy || reviewing} onClick={() => void readOutcome()}>Read saved beat and history</button>
      {review && <section aria-label="Saved beat outcome review"><p>Read saved revision r{review.project.revision} and {review.events.length} consecutive history records.</p>
        <p>{review.matchingValueRevisions.length ? "History contains matching values at " + review.matchingValueRevisions.map(value => "r" + value).join(", ") + "."
          : "No matching operation values occur in the inspected history."} Matching values do not identify or acknowledge the earlier request.</p>
        <h4>Submitted input</h4><BeatValues value={draft.pending.raw} />
        <h4>Saved now</h4><BeatValues value={(() => { const saved = beatTarget(review.project, draft.sceneId, draft.beatId).beat; return saved ? beatRaw(saved) : null; })()} />
        <details><summary>Inspected revision history</summary><ol>{review.events.map(event => <li key={event.revision}>r{event.revision} · {event.change.type} · {event.created_at}</li>)}</ol></details>
        <button type="button" className="button compact" disabled={busy || reviewing || !beatOutcomeMatches(draft, project, review)} onClick={continueAfterReview}>Keep earlier beat attempt and continue editing</button>
      </section>}
    </section>}
    {conflict && <section className="production-notice" role="region" aria-label="Beat draft conflict">
      <strong>{conflict === "removed" ? "The saved beat was removed" : conflict === "retimed" ? "The scene duration changed" : "Review changed beat timing or content"}</strong>
      <p>Your raw input was kept. Compare the saved beat and scene duration before choosing a new intent.</p>
      <h4>Draft started from</h4><BeatValues value={draft.base ? beatRaw(draft.base) : null} />
      <h4>Saved now · r{project.revision}</h4><BeatValues value={target.beat ? beatRaw(target.beat) : null} />
      <p>Current scene duration: {formatBeatTime(scene.duration)} seconds</p>
      <button type="button" className="button compact" disabled={busy || beatUnconfirmed(draft)} onClick={() => choose("saved")}>Keep saved beat state</button>
      {target.beat && <button type="button" className="button compact" disabled={busy || beatUnconfirmed(draft)} onClick={() => choose("text")}>Keep my text with saved timing</button>}
      <button type="button" className="button compact" disabled={busy || beatUnconfirmed(draft)} onClick={() => choose("draft")}>{target.beat ? "Keep my full beat draft" : "Prepare explicit beat recreation"}</button>
    </section>}
    {beatHasInput(draft) && !conflict && <button type="button" className="button compact" disabled={busy || beatUnconfirmed(draft)} onClick={() => choose("saved")}>Use saved beat</button>}
  </div>;
}
