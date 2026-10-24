import { useEffect, useRef, useState } from "react";
import { bootstrap, projectHistory } from "./api";
import { asEditorialChange, assessEditorialPreferences, editorialTime, inspectEditorialReorder, narrativeEditChange,
  narrativeEditingScope, narrativeEditingState, parseEditorialDuration, sameEditorialValue } from "./narrativeEditing";
import type { NarrativeEdit, NarrativeEditingChange, NarrativeTimingProposal } from "./narrativeEditing";
import { acknowledgeEditorialSubmission, adoptEditorialFile, compareEditorialField, editEditorialField, editorialCopies,
  editorialFieldConflict, editorialHasInput, editorialRaw, editorialReviewCurrent, exportEditorialDraft, forkEditorialWriter,
  importEditorialDraft, inspectEditorialOutcome, openEditorialSession, originalEditorialRecoveryCopy, persistEditorialSession, prepareEditorialSubmission,
  rebaseReviewedEditorialFields, restoreEditorialCopy, retainEditorialFailure, retryEditorialCustody, EDITORIAL_COPY_PREFIX } from "./narrativeEditingDraft";
import type { EditorialDraft, EditorialOutcomeReview, EditorialOutcomeTransport, EditorialSession } from "./narrativeEditingDraft";
import type { Change, Project } from "./types";

type Props = { project: Project; commit: (change: Change) => Promise<Project>; outcomeTransport?: EditorialOutcomeTransport };
type Live = EditorialSession & { inFlight: boolean; error: string | null; backupError: string | null; notice: string | null; notify?: () => void };
const liveEditors = new Map<string, Live>();
const transport: EditorialOutcomeTransport = { readProject: async id => {
  const project = (await bootstrap(id)).project;
  if (!project || project.id !== id) throw new Error("The selected editorial project is unavailable; no fallback is accepted.");
  return project;
}, readHistory: projectHistory };
function getSession(project: Project): Live {
  const scope = narrativeEditingScope(project), previous = liveEditors.get(scope);
  if (previous) return previous;
  const opened = openEditorialSession(project, window.localStorage, window.sessionStorage);
  const session: Live = { ...opened, inFlight: false, error: null,
    backupError: opened.failedSource ? "This tab's original editorial recovery is unreadable or missing. It may contain an UNKNOWN operation. Retry that exact copy." : null,
    notice: editorialHasInput(opened.draft) || opened.draft.pending ? "Recovered author input. A saved-project observation is not an acknowledgement." : null };
  liveEditors.set(scope, session); return session;
}
const message = (value: unknown) => value instanceof Error ? value.message : String(value);
function notify(session: Live) { session.notify?.(); }
function backup(session: Live): boolean {
  try { persistEditorialSession(session, window.localStorage, window.sessionStorage); session.backupError = null; return true; }
  catch (error) { session.backupError = message(error) + " Current author input remains available for export."; return false; }
}

export default function NarrativeEditingPanel(props: Props) {
  return <EditorialOwner key={narrativeEditingScope(props.project)} {...props} />;
}
function EditorialOwner({ project, commit, outcomeTransport = transport }: Props) {
  const [session] = useState(() => getSession(project)), [, redraw] = useState(0);
  const projectRef = useRef(project), mounted = useRef(true); projectRef.current = project;
  const [selectedBeat, setSelectedBeat] = useState(project.narrative.beats[0]?.id ?? "");
  const [review, setReview] = useState<EditorialOutcomeReview | null>(null), [reviewConfirmed, setReviewConfirmed] = useState(false);
  const [reviewing, setReviewing] = useState(false), [readingFile, setReadingFile] = useState(false);
  const [fileCandidate, setFileCandidate] = useState<{ name: string; draft: EditorialDraft } | null>(null);
  const fileEpoch = useRef(0);
  const state = narrativeEditingState(project), currentBeat = project.narrative.beats.find(beat => beat.id === selectedBeat);
  const pending = session.draft.pending;
  const reviewReady = reviewConfirmed && editorialReviewCurrent(session, project, review);
  const contentLocked = project.locks.some(lock => lock.kind === "content" && lock.resource === "project:" + project.id);
  const blocked = session.inFlight || session.backupError !== null || session.failedSource !== null || contentLocked || (!!pending && !reviewReady);
  const raw = (key: string, fallback = "") => { try { return editorialRaw(session.draft, project, key) || fallback; } catch { return session.draft.fields[key]?.raw ?? fallback; } };
  // Empty raw text is authored input. Do not substitute a saved/default value.
  const value = (key: string, fallback = "") => session.draft.fields[key] ? session.draft.fields[key].raw : raw(key, fallback);
  function invalidateFileRead() { fileEpoch.current++; setReadingFile(false); setFileCandidate(null); }
  useEffect(() => {
    mounted.current = true; const update = () => redraw(count => count + 1); session.notify = update;
    const changed = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(EDITORIAL_COPY_PREFIX)) update(); };
    window.addEventListener("storage", changed);
    return () => { mounted.current = false; fileEpoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", changed); };
  }, [session]);
  function authored(key: string, text: string) {
    try {
      if (!editorialHasInput(session.draft) && !session.draft.pending && session.custody.expectedRaw !== null) forkEditorialWriter(session);
      editEditorialField(session, projectRef.current, key, text); invalidateFileRead(); setReviewConfirmed(false); backup(session);
    } catch (error) { session.error = message(error); }
    notify(session);
  }
  function attempt(action: () => void) {
    try { action(); session.error = null; } catch (error) {
      session.error = message(error);
      if (session.custody.attempt || session.failedSource) session.backupError = message(error);
    }
    notify(session);
  }
  async function save(change: NarrativeEditingChange, fields: string[]) {
    const current = projectRef.current;
    if (session.inFlight || session.backupError || session.failedSource || contentLocked) return;
    let prepared: ReturnType<typeof prepareEditorialSubmission>;
    try {
      prepared = prepareEditorialSubmission(session, current, change, fields, reviewReady ? review : null);
      // Retain the original author buffer and any previous UNKNOWN before
      // arming this separately constructed operation under a new writer.
      if (!backup(session)) return;
      forkEditorialWriter(session); session.draft = prepared.draft; session.epoch++;
      if (!backup(session)) {
        session.draft = retainEditorialFailure(session.draft, prepared.id, session.backupError ?? "Recovery unconfirmed", true);
        session.error = "NOT_SENT: the callback was not invoked because the exact recovery copy/pointer could not be confirmed.";
        notify(session); return;
      }
      // Freeze the armed key. Later typing uses a distinct mutable writer.
      forkEditorialWriter(session); session.inFlight = true; session.error = null; session.notice = null;
      setReview(null); setReviewConfirmed(false); notify(session);
    } catch (error) { session.error = message(error); notify(session); return; }
    try {
      const receipt = await commit(asEditorialChange(prepared.change));
      if (session.draft.pending?.id !== prepared.id) return;
      const acknowledged = acknowledgeEditorialSubmission(session.draft, prepared.id, prepared.change, receipt);
      // ACK authority and successful retention are different facts. A failed
      // local write never turns the old armed record into a replayable request.
      if (session.custody.attempt) retryEditorialCustody(session, window.localStorage, window.sessionStorage);
      forkEditorialWriter(session); session.draft = acknowledged; session.epoch++;
      session.notice = "Saved editorial intent at r" + receipt.revision + "." + (editorialHasInput(acknowledged) ? " Later author input is still retained." : "");
      backup(session);
    } catch (error) {
      session.draft = retainEditorialFailure(session.draft, prepared.id, message(error)); session.error = message(error); backup(session);
    } finally { session.inFlight = false; notify(session); }
  }
  function edit(action: NarrativeEdit, fields: string[] = []) { void save(narrativeEditChange(projectRef.current, action), fields); }
  const fieldConflict = (key: string) => editorialFieldConflict(session.draft, project, key);
  function conflict(key: string) {
    if (!fieldConflict(key)) return null;
    return <div role="alert">The saved value changed for {key}. Your input is retained.
      <button disabled={!!pending || session.inFlight} onClick={() => attempt(() => {
        if (!backup(session)) return; forkEditorialWriter(session); compareEditorialField(session, projectRef.current, key, false); invalidateFileRead(); backup(session);
      })}>Keep typed value against current saved base</button>
      <button disabled={!!pending || session.inFlight} onClick={() => attempt(() => {
        if (!backup(session)) return; forkEditorialWriter(session); compareEditorialField(session, projectRef.current, key, true); invalidateFileRead(); backup(session);
      })}>Use saved value for {key}</button></div>;
  }
  function input(label: string, key: string, fallback = "", multiline = false) {
    return <label>{label}{multiline
      ? <textarea aria-label={label} value={value(key, fallback)} onChange={event => authored(key, event.target.value)} />
      : <input aria-label={label} value={value(key, fallback)} onChange={event => authored(key, event.target.value)} />}{conflict(key)}</label>;
  }
  let ordered = project.narrative.beats.map(beat => beat.id), orderError: string | null = null;
  try { const parsed: unknown = JSON.parse(value("order", JSON.stringify(ordered))); if (!Array.isArray(parsed) || !parsed.every(id => typeof id === "string")) throw new Error("Unreadable author order."); ordered = parsed; }
  catch (error) { orderError = message(error); }
  let reorderImpact: ReturnType<typeof inspectEditorialReorder> | null = null;
  try { reorderImpact = inspectEditorialReorder(project, ordered); } catch (error) { orderError = message(error); }
  let assessments: ReturnType<typeof assessEditorialPreferences> = [], assessmentError: string | null = null;
  try { assessments = assessEditorialPreferences(project); } catch (error) { assessmentError = message(error); }
  function move(id: string, delta: number) {
    const next = [...ordered], index = next.indexOf(id), target = index + delta;
    if (index < 0 || target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]]; authored("order", JSON.stringify(next));
  }
  async function inspectOutcome() {
    setReviewing(true); setReviewConfirmed(false); setReview(null);
    try {
      const next = await inspectEditorialOutcome(session, projectRef.current, outcomeTransport);
      if (mounted.current && editorialReviewCurrent(session, projectRef.current, next)) setReview(next);
    } catch (error) { session.error = message(error); notify(session); }
    finally { if (mounted.current) setReviewing(false); }
  }
  function exportInput() {
    const blob = new Blob([exportEditorialDraft(session.draft)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = "motionwright-editorial-author-input.json"; link.click(); URL.revokeObjectURL(url);
  }
  function exportCopy(entry: { key: string; raw: string }) {
    // Wrapping the original string also preserves malformed JSON and escaped
    // code units. This is source recovery data, never an executable command.
    const blob = new Blob([JSON.stringify({ schema: "motionwright.local-input-copy/1", storage_key: entry.key, raw: entry.raw }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob), link = document.createElement("a");
    link.href = url; link.download = "motionwright-editorial-recovery-copy.json"; link.click(); URL.revokeObjectURL(url);
  }
  async function readFile(file: File | undefined) {
    if (!file) return;
    const epoch = ++fileEpoch.current, authorEpoch = session.epoch; setReadingFile(true); setFileCandidate(null);
    try {
      const text = await file.text();
      if (!mounted.current || epoch !== fileEpoch.current || session.epoch !== authorEpoch) return;
      const draft = importEditorialDraft(text, session.draft.scope); setFileCandidate({ name: file.name, draft });
    } catch (error) { if (mounted.current && epoch === fileEpoch.current) { session.error = message(error); notify(session); } }
    finally { if (mounted.current && epoch === fileEpoch.current) setReadingFile(false); }
  }
  let copies: ReturnType<typeof editorialCopies> = [], copiesError: string | null = null;
  try { copies = editorialCopies(window.localStorage, session.draft.scope, session.recoveryObservation); } catch (error) { copiesError = message(error); }
  const originalCopy = originalEditorialRecoveryCopy(session.draft.scope, session.recoveryObservation);
  if (originalCopy) copies = [originalCopy, ...copies.filter(entry => entry.key !== originalCopy.key)];
  const beatKeys = currentBeat ? ["label", "objective", "audience_takeaway", "preferred"].map(field => `beat:${currentBeat.id}:${field}`) : [];
  const newKeys = ["new.label", "new.objective", "new.takeaway", "new.preferred"];
  const script = state.script;
  const scriptUntyped = value("script.text") === (script?.text ?? "");
  function durationProposal(proposal: NarrativeTimingProposal) {
    const reasonKey = `proposal:${proposal.id}:reason`;
    const fresh = proposal.source.project_id === project.id && proposal.source.generation === project.generation
      && proposal.source.branch_id === project.active_branch && proposal.source.base_revision + 1 === project.revision;
    return <article key={proposal.id} aria-label={"Timing proposal " + proposal.id}>
      <h4>Scene {proposal.scene_id}: {editorialTime(proposal.original_duration)} → {editorialTime(proposal.requested_duration)} seconds</h4>
      <p>Proposal {proposal.id}, from r{proposal.source.base_revision}. {proposal.reason}</p>
      <p>{proposal.feasibility.command_allowed ? "The domain guard allowed this exact candidate at the recorded revision." : "Candidate conflict: " + proposal.feasibility.rejection}</p>
      <p>Approved editorial text retained: {proposal.script_snapshot?.approval ? proposal.script_snapshot.sha256 : "No approved script was present."}</p>
      <details><summary>Inspect original wording, affected scenes, preferences and hard locks</summary>
        {proposal.script_snapshot && <pre>{proposal.script_snapshot.text}</pre>}
        <pre>{JSON.stringify(proposal.feasibility, null, 2)}</pre>
      </details>
      {proposal.decision ? <p>Recorded decision: {proposal.decision.decision} at source r{proposal.decision.source.base_revision}. {proposal.decision.reason}</p>
        : <>{input("Timing decision reason " + proposal.id, reasonKey)}
          <button disabled={blocked || !value(reasonKey).trim()} onClick={() => edit({ kind: "decide_duration", proposal_id: proposal.id, decision: "keep_current", reason: value(reasonKey) }, [reasonKey])}>Keep current timing for {proposal.id}</button>
          <button disabled={blocked || !fresh || !proposal.feasibility.command_allowed || !value(reasonKey).trim()} onClick={() => edit({ kind: "decide_duration", proposal_id: proposal.id, decision: "apply_duration", reason: value(reasonKey) }, [reasonKey])}>Apply reviewed duration for {proposal.id}</button>
          {!fresh && <p>This proposal is retained for comparison. A later revision requires a fresh assessment.</p>}
          <button disabled={blocked} onClick={() => edit({ kind: "propose_duration", proposal_id: crypto.randomUUID(), scene_id: proposal.scene_id,
            requested_duration: proposal.requested_duration, reason: "Reassess retained proposal " + proposal.id + ": " + proposal.reason })}>Reassess without applying {proposal.id}</button></>}
    </article>;
  }

  return <section aria-label="Editorial narrative editor" className="inspector-section">
    <h3>Editorial beats, script and timing</h3>
    <p>Editorial beats describe changes in understanding. Their order is independent of scene timing. The script below is a separate author resource from recognized words and the recorded voice take.</p>
    {session.notice && <p role="status">{session.notice}</p>}
    {session.error && <p role="alert">{session.error}</p>}
    {session.backupError && <div role="alert">{session.backupError}<button onClick={() => attempt(() => {
      retryEditorialCustody(session, window.localStorage, window.sessionStorage); session.backupError = null; invalidateFileRead(); setReviewConfirmed(false);
    })}>Retry editorial recovery</button></div>}
    {pending && <section aria-label="Unconfirmed editorial operation">
      <h4>{pending.outcome}: editorial operation {pending.id}</h4>
      <p>The original response is unconfirmed. This editor never automatically resends it. A saved value or history entry is an observation, not its original acknowledgement.</p>
      <button disabled={session.inFlight || reviewing} onClick={() => void inspectOutcome()}>Inspect current editorial project and history</button>
      {review && editorialReviewCurrent(session, project, review) && <>
        <p>Read r{review.observed.revision}; {review.events.length} contiguous later revisions. Matching command observations: {review.matchingRevisions.join(", ") || "none"}.</p>
        <details><summary>Inspect retained operation and current saved narrative</summary><pre>{pending.changeJson}</pre><pre>{JSON.stringify(review.observed.narrative, null, 2)}</pre></details>
        <button disabled={session.inFlight || !!session.backupError} onClick={() => attempt(() => {
          const next = rebaseReviewedEditorialFields(session, projectRef.current, review, window.localStorage, window.sessionStorage);
          invalidateFileRead(); setReview(next); setReviewConfirmed(true);
        })}>Keep typed input for a separate explicit intent</button>
        <button disabled={session.inFlight || !!session.backupError} onClick={() => attempt(() => {
          const next = rebaseReviewedEditorialFields(session, projectRef.current, review, window.localStorage, window.sessionStorage, true);
          invalidateFileRead(); setReview(next); setReviewConfirmed(true);
        })}>Use reviewed saved inputs for a separate intent</button>
        {reviewReady && <p>The next save is a new explicit operation. The original UNKNOWN record remains retained.</p>}
      </>}
    </section>}
    <fieldset><legend>Ordered editorial beats</legend>
      <ol>{ordered.map((id, index) => { const beat = project.narrative.beats.find(beat => beat.id === id); return <li key={id}>
        <button onClick={() => setSelectedBeat(id)}>{beat?.label ?? "Missing editorial beat"}</button> <code>{id}</code>
        <button aria-label={"Move editorial beat " + (beat?.label ?? id) + " earlier"} disabled={index === 0} onClick={() => move(id, -1)}>Earlier</button>
        <button aria-label={"Move editorial beat " + (beat?.label ?? id) + " later"} disabled={index === ordered.length - 1} onClick={() => move(id, 1)}>Later</button>
      </li>; })}</ol>
      {orderError && <p role="alert">{orderError}</p>}{conflict("order")}
      {reorderImpact && <p>{reorderImpact.affected_relations.length} declared relationships change; {reorderImpact.conflicting_relation_ids.length} continuity/order conflicts; {reorderImpact.unbound_moved_beat_ids.length} moved beats have no realization binding. Renderer dependencies remain unassessed.</p>}
      <button disabled={blocked || !!orderError || fieldConflict("order") || sameEditorialValue(ordered, project.narrative.beats.map(beat => beat.id))}
        onClick={() => edit({ kind: "reorder", expected_order: project.narrative.beats.map(beat => beat.id), ordered_ids: ordered }, ["order"])}>Save editorial beat order</button>
      {state.last_reorder && <details><summary>Saved reorder impact and exact affected IDs</summary><pre>{JSON.stringify(state.last_reorder, null, 2)}</pre></details>}
    </fieldset>
    <fieldset><legend>Add an editorial beat</legend>
      {input("New editorial beat label", newKeys[0])}{input("New editorial objective", newKeys[1], "", true)}
      {input("New audience takeaway", newKeys[2], "", true)}{input("New preferred duration (seconds, optional)", newKeys[3])}
      <button disabled={blocked || !value(newKeys[0]).trim() || !value(newKeys[1]).trim()} onClick={() => attempt(() => {
        void save({ type: "add_narrative_beat", label: value(newKeys[0]), objective: value(newKeys[1]), audience_takeaway: value(newKeys[2]), claim_ids: [], preferred_duration: parseEditorialDuration(value(newKeys[3]), true) }, newKeys);
      })}>Add editorial beat</button>
    </fieldset>
    {currentBeat && <fieldset><legend>Edit editorial beat {currentBeat.label}</legend>
      {input("Editorial beat label", beatKeys[0])}{input("Editorial beat objective", beatKeys[1], "", true)}
      {input("Editorial audience takeaway", beatKeys[2], "", true)}{input("Preferred editorial duration (seconds, optional)", beatKeys[3])}
      <p>Claim IDs are retained: {currentBeat.claim_ids.join(", ") || "none"}.</p>
      <button disabled={blocked || beatKeys.some(fieldConflict)} onClick={() => attempt(() => edit({ kind: "update_beat", beat: { ...currentBeat,
        label: value(beatKeys[0]), objective: value(beatKeys[1]), audience_takeaway: value(beatKeys[2]), preferred_duration: parseEditorialDuration(value(beatKeys[3]), true) } }, beatKeys))}>Save editorial beat</button>
      {input("Minimum editorial hold (seconds, optional)", `beat:${currentBeat.id}:minimum`)}
      <button disabled={blocked || fieldConflict(`beat:${currentBeat.id}:minimum`)} onClick={() => attempt(() => edit({ kind: "set_minimum_hold", beat_id: currentBeat.id,
        duration: parseEditorialDuration(value(`beat:${currentBeat.id}:minimum`), true) }, [`beat:${currentBeat.id}:minimum`]))}>Save minimum editorial hold</button>
      {input("Beat protection decision reason", `protection:${currentBeat.id}:reason`)}
      <button disabled={blocked || !value(`protection:${currentBeat.id}:reason`).trim()} onClick={() => edit({ kind: "set_beat_protection", beat_id: currentBeat.id,
        protected: !project.narrative.protected_sections.some(section => section.beat_id === currentBeat.id), reason: value(`protection:${currentBeat.id}:reason`) }, [`protection:${currentBeat.id}:reason`])}>
        {project.narrative.protected_sections.some(section => section.beat_id === currentBeat.id) ? "Release editorial beat protection" : "Protect editorial beat wording"}</button>
    </fieldset>}
    <fieldset><legend>Realization bindings and declared relationships</legend>
      <p>Bindings refer to existing authored scenes or SceneBeats. They allocate dwell; they do not prove visual continuity or speech timing.</p>
      <label>Realization editorial beat<select value={value("binding.beat", selectedBeat)} onChange={event => authored("binding.beat", event.target.value)}>
        <option value="">Select beat</option>{project.narrative.beats.map(beat => <option key={beat.id} value={beat.id}>{beat.label}</option>)}</select></label>
      <label>Realization scene<select value={value("binding.scene", project.scenes[0]?.id ?? "")} onChange={event => authored("binding.scene", event.target.value)}>
        <option value="">Select scene</option>{project.scenes.map(scene => <option key={scene.id} value={scene.id}>{scene.name}</option>)}</select></label>
      <label>Realization interval<select value={value("binding.target", "scene")} onChange={event => authored("binding.target", event.target.value)}>
        <option value="scene">Whole authored scene</option>{project.scenes.find(scene => scene.id === value("binding.scene", project.scenes[0]?.id ?? ""))?.beats.map(beat => <option key={beat.id} value={beat.id}>{beat.label}</option>)}</select></label>
      <button disabled={blocked || !value("binding.beat", selectedBeat) || !value("binding.scene", project.scenes[0]?.id ?? "")} onClick={() => {
        const sceneId = value("binding.scene", project.scenes[0]?.id ?? ""), interval = value("binding.target", "scene");
        edit({ kind: "upsert_binding", binding: { id: crypto.randomUUID(), beat_id: value("binding.beat", selectedBeat),
          target: interval === "scene" ? { kind: "scene", scene_id: sceneId } : { kind: "scene_beat", scene_id: sceneId, scene_beat_id: interval } } }, ["binding.beat", "binding.scene", "binding.target"]);
      }}>Bind editorial realization</button>
      <ul>{state.bindings.map(binding => <li key={binding.id}>{binding.beat_id} → {binding.target.scene_id}{binding.target.kind === "scene_beat" ? " / " + binding.target.scene_beat_id : " (whole scene)"}
        <button disabled={blocked} onClick={() => edit({ kind: "remove_binding", binding_id: binding.id })}>Remove binding {binding.id}</button></li>)}</ul>
      <label>Relation source beat<select value={value("relation.from", selectedBeat)} onChange={event => authored("relation.from", event.target.value)}>
        <option value="">Select source</option>{project.narrative.beats.map(beat => <option key={beat.id} value={beat.id}>{beat.label}</option>)}</select></label>
      <label>Relation target beat<select value={value("relation.to")} onChange={event => authored("relation.to", event.target.value)}>
        <option value="">Select target</option>{project.narrative.beats.map(beat => <option key={beat.id} value={beat.id}>{beat.label}</option>)}</select></label>
      <label>Editorial relationship<select value={value("relation.kind", "precedes")} onChange={event => authored("relation.kind", event.target.value)}><option value="precedes">Source precedes target</option><option value="adjacent">Target follows source directly</option></select></label>
      {input("Editorial relationship reason", "relation.reason")}
      <button disabled={blocked || !value("relation.from", selectedBeat) || !value("relation.to") || !value("relation.reason").trim()} onClick={() => edit({ kind: "upsert_relation",
        relation: { id: crypto.randomUUID(), from_beat_id: value("relation.from", selectedBeat), to_beat_id: value("relation.to"),
          kind: value("relation.kind", "precedes") === "adjacent" ? "adjacent" : "precedes", reason: value("relation.reason") } }, ["relation.from", "relation.to", "relation.kind", "relation.reason"])}>Save editorial relationship</button>
      <ul>{state.relations.map(relation => <li key={relation.id}>{relation.from_beat_id} {relation.kind} {relation.to_beat_id}: {relation.reason}
        <button disabled={blocked} onClick={() => edit({ kind: "remove_relation", relation_id: relation.id })}>Remove relationship {relation.id}</button></li>)}</ul>
      {assessmentError && <p role="alert">{assessmentError}</p>}
      <table><caption>Exact authored allocation, separate from hard timing locks</caption><thead><tr><th>Beat</th><th>Allocated</th><th>Minimum hold</th><th>Shortfall</th><th>Preferred</th><th>Delta</th><th>Conflict</th></tr></thead>
        <tbody>{assessments.map(row => <tr key={row.beat_id}><td>{project.narrative.beats.find(beat => beat.id === row.beat_id)?.label}</td>
          <td>{row.allocated_duration ? editorialTime(row.allocated_duration) : "UNKNOWN — unbound"}</td><td>{editorialTime(row.minimum_hold) || "Not set"}</td>
          <td>{row.minimum_shortfall ? editorialTime(row.minimum_shortfall) : "Not measured/not set"}</td><td>{editorialTime(row.preferred_duration) || "Not set"}</td>
          <td>{row.preferred_delta ? editorialTime(row.preferred_delta) : "Not measured/not set"}</td><td>{row.contradictory_preferences ? "Minimum exceeds preference" : "None declared"}</td></tr>)}</tbody></table>
    </fieldset>
    <fieldset><legend>Editorial script and explicit content approval</legend>
      {input("Editorial script", "script.text", "", true)}
      <p>Saved script {script?.id ?? "not created"}; version {script?.version ?? 0}. {script?.approval ? "Exact text is approved; explicit release is required before editing the saved wording." : "No editorial content approval is recorded."}</p>
      <button disabled={blocked || !!script?.approval || fieldConflict("script.text") || scriptUntyped} onClick={() => edit({ kind: "set_script", script_id: script?.id ?? crypto.randomUUID(), expected_version: script?.version ?? null, text: value("script.text") }, ["script.text"])}>Save editorial script</button>
      {input("Editorial reviewer label", "decision.reviewer")}{input("Editorial approval or release reason", "decision.reason")}
      <button disabled={blocked || !script || !!script.approval || !script.text.trim() || !scriptUntyped || !value("decision.reviewer").trim() || !value("decision.reason").trim()}
        onClick={() => { if (script) edit({ kind: "approve_script", approval_id: crypto.randomUUID(), script_id: script.id, expected_version: script.version,
          expected_sha256: script.sha256, reviewer: value("decision.reviewer"), reason: value("decision.reason") }, ["decision.reviewer", "decision.reason"]); }}>Approve exact editorial text</button>
      <button disabled={blocked || !script?.approval || !value("decision.reviewer").trim() || !value("decision.reason").trim()} onClick={() => {
        if (script?.approval) edit({ kind: "release_script_approval", approval_id: script.approval.id, expected_sha256: script.sha256,
          reviewer: value("decision.reviewer"), reason: value("decision.reason") }, ["decision.reviewer", "decision.reason"]);
      }}>Release exact editorial approval</button>
      <p>These are recorded content decisions. They do not authenticate reviewer identity, align voice audio, or grant publication acceptance.</p>
    </fieldset>
    <fieldset><legend>Retained duration proposals</legend>
      <label>Proposal scene<select value={value("proposal.scene", project.scenes[0]?.id ?? "")} onChange={event => authored("proposal.scene", event.target.value)}>
        <option value="">Select scene</option>{project.scenes.map(scene => <option key={scene.id} value={scene.id}>{scene.name}</option>)}</select></label>
      {input("Proposed scene duration (exact seconds)", "proposal.duration")}{input("Duration proposal reason", "proposal.reason", "", true)}
      <button disabled={blocked || !value("proposal.scene", project.scenes[0]?.id ?? "") || !value("proposal.reason").trim()} onClick={() => attempt(() => {
        const duration = parseEditorialDuration(value("proposal.duration")); if (!duration) throw new Error("Enter a complete exact duration.");
        edit({ kind: "propose_duration", proposal_id: crypto.randomUUID(), scene_id: value("proposal.scene", project.scenes[0]?.id ?? ""), requested_duration: duration, reason: value("proposal.reason") }, ["proposal.scene", "proposal.duration", "proposal.reason"]);
      })}>Retain duration proposal without applying</button>
      <p>No script shortening, audio speed change or native generation is performed. A hard conflict is retained as a reviewable result.</p>
      {state.timing_proposals.map(durationProposal)}
    </fieldset>
    <details><summary>Editorial recovery and portable author input</summary>
      <button onClick={() => attempt(exportInput)}>Export editorial author input</button>
      <label>Import editorial author input<input type="file" accept="application/json,.json" disabled={readingFile} onChange={event => void readFile(event.target.files?.[0])} /></label>
      {fileCandidate && <div>Candidate: {fileCandidate.name}. No input has been replaced.
        <button disabled={session.inFlight || !!pending} onClick={() => attempt(() => { adoptEditorialFile(session, fileCandidate.draft, window.localStorage, window.sessionStorage); invalidateFileRead(); setReviewConfirmed(false); session.backupError = null; })}>Preserve current input and import candidate</button></div>}
      {copiesError && <p role="alert">{copiesError}</p>}
      <ul>{copies.map(entry => <li key={entry.key}><code>{entry.key}</code> {entry.copy
        ? entry.copy.draft.pending ? "Retained UNKNOWN operation" : editorialHasInput(entry.copy.draft) ? "Retained author input" : "Clean checkpoint; no pending author input"
        : "Unreadable copy retained; not permission to save"}
        <button onClick={() => attempt(() => exportCopy(entry))}>Download editorial copy {entry.copy?.writer ?? entry.key}</button>
        <button disabled={session.inFlight || !!pending || !entry.copy || (!editorialHasInput(entry.copy.draft) && !entry.copy.draft.pending)} onClick={() => attempt(() => {
          if (!entry.copy) return; restoreEditorialCopy(session, entry.raw, entry.copy.writer, window.localStorage, window.sessionStorage); invalidateFileRead(); setReviewConfirmed(false); session.backupError = null;
        })}>Recover editorial copy {entry.copy?.writer ?? entry.key}</button></li>)}</ul>
    </details>
  </section>;
}
