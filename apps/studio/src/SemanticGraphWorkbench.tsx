import { useEffect, useRef, useState } from "react";
import type { DeliverableProfile, Project, ReviewAnchor, Scene } from "./types";
import { graphPreviewFontFamily, graphPreviewFontLicense, graphPreviewMissingCodepoints, graphPreviewText, loadGraphPreviewFont } from "./semanticGraphFonts";
import { graphAdmissionIssue, graphEdgePoints, graphFrameTime, graphFromRaw, graphLockIssue, graphReviewAnchor,
  graphSame, graphSourceSnapshot, graphToRaw, graphUuid, newSemanticGraph, semanticGraphs, validateSemanticGraphShape } from "./semanticGraph";
import type { GraphAuthoringContext, GraphContent, GraphRaw, GraphTargetBinding, SemanticGraph, SemanticGraphChange } from "./semanticGraph";
import { acknowledgeGraphDraft, assertGraphCustody, assertGraphRecoverySource, createGraphDraft, decodeGraphCopy, discardGraphCopy, editGraphDraft, failGraphSubmission,
  forkGraphCopy, graphDraftScope, graphDownload, graphHasWork, graphOutcomeMatches, graphUnconfirmed,
  inspectGraphOutcome, mergeGraphDraft, prepareGraphSubmission, readGraphCopies, readGraphRecoverySource, reconcileGraphDraft, recoverGraphRead, releaseGraphOutcome,
  reviewGraphDraft, writeGraphCopy, GRAPH_RECOVERY_PREFIX } from "./semanticGraphDraft";
import type { GraphCustody, GraphDraft, GraphOutcomeReview, GraphOutcomeTransport, GraphRecoveryEntry, GraphRecoveryObservation } from "./semanticGraphDraft";

export interface SemanticGraphWorkbenchProps {
  project: Project; scene: Scene | null; displayProfile: DeliverableProfile | null; busy: boolean;
  commit: (change: SemanticGraphChange) => Promise<Project>;
  /** Actual service read model through the existing transport; null means unsupported. */
  readContext: ((project: Project, sceneId: string, profileId: string) => Promise<GraphAuthoringContext>) | null;
  outcomeTransport: GraphOutcomeTransport;
  refreshProject: () => Promise<Project>;
  openReview: (anchor: ReviewAnchor) => void;
  playhead?: number;
}
interface GraphSession {
  draft: GraphDraft; custody: GraphCustody; error: string | null; backupError: string | null; recoveryProblem: string | null;
  recoveryObservation: GraphRecoveryObservation; editedWhileRecoveryBlocked: string[];
  notice: string | null; inFlight: boolean; preparing: boolean; copies: GraphRecoveryEntry[]; copiesLimited: boolean;
  observedAck: { id: string; receipt: Project } | null; notify?: () => void;
}
const sessions = new Map<string, GraphSession>(), newIds = new Map<string, { id: string; problem: string | null }>();
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const clone = <T,>(value: T): T => structuredClone(value);
const notify = (session: GraphSession) => session.notify?.();
function copies(session: GraphSession) {
  try {
    const observed = session.recoveryObservation;
    const result = readGraphCopies(window.localStorage, session.draft.scope, (writerId, raw) => {
      if (session.recoveryProblem && observed.pointerObserved && writerId === observed.pointer) {
        if (observed.rawObserved && observed.raw !== raw) throw new Error("The observed original Graph recovery bytes changed. The original read remains retained for download.");
        // Retain this observation even if enumeration fails on a later entry.
        observed.raw = raw; observed.rawObserved = true;
      }
    });
    session.copies = result.entries; session.copiesLimited = result.limited;
  }
  catch (reason) { session.backupError = message(reason); }
}
function backup(session: GraphSession): boolean {
  try {
    if (session.recoveryProblem) throw new Error(session.recoveryProblem);
    writeGraphCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.backupError = null; copies(session); return session.backupError === null;
  } catch (reason) { session.backupError = message(reason) + " The current raw input remains in this editor."; return false; }
}
function getSession(project: Project, initial: SemanticGraph): GraphSession {
  const scope = graphDraftScope(project, initial.scene_id, initial.id), found = sessions.get(scope); if (found) return found;
  let draft = createGraphDraft(project, initial), recoveryProblem: string | null = null;
  const recoveryObservation: GraphRecoveryObservation = { pointerObserved: false, pointer: null, rawObserved: false, raw: null };
  try {
    draft = readGraphRecoverySource(window.localStorage, window.sessionStorage, scope, recoveryObservation) ?? draft;
  } catch (reason) { recoveryProblem = message(reason); }
  const session: GraphSession = { draft, custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: recoveryObservation.pointer },
    recoveryObservation, editedWhileRecoveryBlocked: [],
    error: null, backupError: null, recoveryProblem, notice: null, inFlight: false, preparing: false, copies: [], copiesLimited: false, observedAck: null };
  sessions.set(scope, session);
  // Every runtime gets its own writer. A cloned session can never overwrite its source.
  if (!recoveryProblem) backup(session); else session.backupError = recoveryProblem;
  copies(session); return session;
}
function createIdentity(project: Project, sceneId: string, kind: GraphContent["kind"]): { id: string; problem: string | null } {
  const key = "motionwright.semantic-graph-new-id.v1:" + JSON.stringify([project.id, project.generation, project.active_branch, sceneId, kind]);
  const found = newIds.get(key); if (found) return found;
  let result: { id: string; problem: string | null } = { id: crypto.randomUUID(), problem: null };
  try { const stored = window.sessionStorage.getItem(key); if (stored !== null) {
    if (!graphUuid(stored)) throw new Error("The new Graph identity is unreadable; its raw bytes were retained."); result.id = stored;
  } else { window.sessionStorage.setItem(key, result.id); if (window.sessionStorage.getItem(key) !== result.id) throw new Error("The new Graph identity could not be retained across reload. Saving is blocked."); } }
  catch (reason) { result = { ...result, problem: message(reason) }; }
  newIds.set(key, result); return result;
}
const optionLabel = (ref: GraphTargetBinding) => ref.kind === "claim" ? "Claim " + ref.claim_id : ref.kind === "canvas_node" ? "Canvas node " + ref.node_id : "Measured " + ref.table_id + " / " + ref.datum_key;
function TextField({ label, value, onChange, multiline = false }: { label: string; value: string; onChange: (value: string) => void; multiline?: boolean }) {
  return <label className="production-field"><span>{label}</span>{multiline ? <textarea aria-label={label} value={value} onChange={e => onChange(e.target.value)} rows={2} />
    : <input aria-label={label} type="text" value={value} onChange={e => onChange(e.target.value)} />}</label>;
}
function RationalFields({ label, value, onChange }: { label: string; value: { num: string; den: string }; onChange: (value: { num: string; den: string }, part: string) => void }) {
  return <fieldset><legend>{label} · exact seconds</legend><div className="production-plan-grid">
    <TextField label={label + " numerator"} value={value.num} onChange={num => onChange({ ...value, num }, "num")} />
    <TextField label={label + " denominator"} value={value.den} onChange={den => onChange({ ...value, den }, "den")} />
  </div></fieldset>;
}
function BoxFields({ label, value, onChange }: { label: string; value: { x: string; y: string; width: string; height: string }; onChange: (key: "x" | "y" | "width" | "height", value: string) => void }) {
  return <fieldset><legend>{label}</legend><div className="production-plan-grid">{(["x", "y", "width", "height"] as const).map(key =>
    <TextField key={key} label={label + " " + key} value={value[key]} onChange={next => onChange(key, next)} />)}</div></fieldset>;
}
export default function SemanticGraphWorkbench(props: SemanticGraphWorkbenchProps) {
  const available = semanticGraphs(props.project).filter(graph => graph.scene_id === props.scene?.id);
  const [selection, setSelection] = useState(available.find(graph => graph.variant_profile_ref === props.displayProfile?.id)?.id ?? "new:causal_diagram");
  const sourceScope = JSON.stringify([props.project.id, props.project.generation, props.project.active_branch, props.scene?.id]);
  const previousScope = useRef(sourceScope);
  useEffect(() => { if (previousScope.current !== sourceScope) { previousScope.current = sourceScope; setSelection(available.find(graph => graph.variant_profile_ref === props.displayProfile?.id)?.id ?? "new:causal_diagram"); } }, [sourceScope, available, props.displayProfile?.id]);
  if (!props.scene || !props.displayProfile) return <section aria-label="Semantic Graph"><h2>Semantic Graph</h2><p>Select a scene and a Deliverable to author its typed Graph.</p></section>;
  const stored = available.find(graph => graph.id === selection), kind: GraphContent["kind"] = selection === "new:system_flow" ? "system_flow" : "causal_diagram";
  const identity = stored ? { id: stored.id, problem: null } : createIdentity(props.project, props.scene.id, kind);
  const existingNew = available.find(graph => graph.id === identity.id);
  const initial = stored ?? existingNew ?? newSemanticGraph(props.project, props.scene, props.displayProfile, kind, identity.id);
  return <section aria-label="Semantic Graph workbench" style={{ display: "flex", flexDirection: "column", minHeight: 0, flex: 1 }}>
    <label className="production-field" style={{ padding: "12px 24px", flexShrink: 0 }}><span>Graph source</span><select aria-label="Graph source" value={selection} onChange={e => setSelection(e.target.value)}>
      {available.map(graph => <option key={graph.id} value={graph.id}>{graph.content.kind === "causal_diagram" ? "Causal diagram" : "System flow"} · {graph.id.slice(0, 8)}</option>)}
      <option value="new:causal_diagram">New causal diagram</option><option value="new:system_flow">New synthetic system flow</option>
    </select></label>
    {identity.problem && <p role="alert">{identity.problem}</p>}
    <GraphEditor {...props} initial={initial} identityProblem={identity.problem} key={graphDraftScope(props.project, initial.scene_id, initial.id)} />
  </section>;
}

function GraphEditor(props: SemanticGraphWorkbenchProps & { initial: SemanticGraph; identityProblem: string | null }) {
  const [session] = useState(() => getSession(props.project, props.initial));
  const [, redraw] = useState(0), [context, setContext] = useState<GraphAuthoringContext | null>(null), [inspecting, setInspecting] = useState(false);
  const [review, setReview] = useState<GraphOutcomeReview | null>(null), [reviewing, setReviewing] = useState(false), [choices, setChoices] = useState<Record<string, "current" | "input">>({});
  const [reviewResource, setReviewResource] = useState("graph:" + session.draft.graphId);
  const [previewMode, setPreviewMode] = useState<"complete" | "playhead">("complete");
  const current = useRef(props), mounted = useRef(true), epoch = useRef(0); current.current = props;
  const draft = session.draft, raw = draft.input, pending = !!draft.pending, busy = props.busy || session.inFlight || session.preparing;
  const profile = props.project.deliverables.find(item => item.id === raw.variant_profile_ref);
  const admission = graphAdmissionIssue(props.project, draft.sceneId, raw.variant_profile_ref, context);
  const changed = draft.baseRevision !== props.project.revision || !graphSame(draft.base, (() => { const value = semanticGraphs(props.project).find(graph => graph.id === draft.graphId); return value ? graphToRaw(value) : null; })());
  let model: SemanticGraph | null = null, inputIssue: string | null = null;
  try { model = graphFromRaw(raw); validateSemanticGraphShape(model, profile?.frame_rate); }
  catch (reason) { inputIssue = message(reason); }
  let preview: SemanticGraph | null = null;
  try { const candidate = graphFromRaw(raw); candidate.authorship.author_label ||= "Editorial preview"; candidate.authorship.rationale ||= "Uncommitted editorial preview"; validateSemanticGraphShape(candidate, profile?.frame_rate); preview = candidate; } catch { /* Incomplete numeric input remains editable without invented coordinates. */ }
  const lock = model ? graphLockIssue(props.project, model) : null;
  const canSave = !busy && !pending && !admission && !inputIssue && !session.backupError && !session.recoveryProblem && !changed && !lock && !props.identityProblem;
  const merge = changed && !pending ? mergeGraphDraft(draft, props.project) : null;
  useEffect(() => {
    mounted.current = true; const update = () => redraw(value => value + 1); session.notify = update;
    const storage = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(GRAPH_RECOVERY_PREFIX)) { copies(session); update(); } };
    window.addEventListener("storage", storage);
    return () => { mounted.current = false; epoch.current++; if (session.notify === update) delete session.notify; window.removeEventListener("storage", storage); };
  }, [session]);
  useEffect(() => {
    const next = reconcileGraphDraft(session.draft, props.project); if (next !== session.draft) { session.draft = next; backup(session); notify(session); }
    setContext(value => value && graphSame(value.source_snapshot, graphSourceSnapshot(props.project)) ? value : null);
    setReview(value => graphOutcomeMatches(session.draft, props.project, value) ? value : null);
  }, [props.project, session]);
  function edit(mutator: (input: GraphRaw) => void, path: string) {
    const next = clone(session.draft.input); mutator(next);
    // A verified clean checkpoint remains immutable; a new author edit gets a writer.
    if (!graphHasWork(session.draft)) session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
    session.draft = editGraphDraft(session.draft, next, [path]);
    if (session.recoveryProblem) session.editedWhileRecoveryBlocked = [...new Set([...session.editedWhileRecoveryBlocked, path])];
    session.error = null; session.notice = null; epoch.current++; setReview(null); setChoices({}); backup(session); notify(session);
  }
  async function inspectContext() {
    if (!current.current.readContext || inspecting || busy) return; const token = ++epoch.current, sequence = session.draft.sequence, before = clone(current.current.project), profileId = session.draft.input.variant_profile_ref;
    setInspecting(true); setContext(null); session.error = null;
    try { const value = await current.current.readContext(before, session.draft.sceneId, profileId);
      if (!mounted.current || token !== epoch.current || sequence !== session.draft.sequence || !graphSame(graphSourceSnapshot(before), graphSourceSnapshot(current.current.project)) || profileId !== session.draft.input.variant_profile_ref) return;
      const issue = graphAdmissionIssue(current.current.project, session.draft.sceneId, profileId, value); setContext(value); if (issue) session.error = issue;
    } catch (reason) { if (mounted.current && token === epoch.current) session.error = message(reason); }
    finally { if (mounted.current) setInspecting(false); notify(session); }
  }
  async function send(operation: "save" | "remove") {
    if (!canSave) return; const id = crypto.randomUUID(), source = clone(session.draft), project = clone(current.current.project), token = epoch.current;
    session.preparing = true; session.error = null; notify(session);
    let prepared: Awaited<ReturnType<typeof prepareGraphSubmission>>;
    try {
      prepared = await prepareGraphSubmission(source, project, context, id, operation);
      if (!mounted.current || token !== epoch.current || !graphSame(source, session.draft) || !graphSame(graphSourceSnapshot(project), graphSourceSnapshot(current.current.project))) { session.notice = "Graph input changed during validation. Nothing was sent; save the current input explicitly."; return; }
      session.draft = prepared.draft;
      if (!backup(session)) { session.draft = failGraphSubmission(session.draft, id, "Recovery custody was not confirmed before dispatch.", true); session.error = "NOT_SENT: confirm the exact recoverable Graph copy before another intention."; return; }
      assertGraphCustody(window.localStorage, window.sessionStorage, session.draft, session.custody);
    } catch (reason) { session.error = message(reason); if (session.draft.pending?.id === id) session.draft = failGraphSubmission(session.draft, id, message(reason), true); return; }
    finally { session.preparing = false; notify(session); }
    session.inFlight = true; notify(session);
    try {
      const receipt = await current.current.commit(prepared.change);
      if (session.draft.pending?.id !== id) return;
      const next = acknowledgeGraphDraft(session.draft, id, receipt);
      session.observedAck = { id, receipt: clone(receipt) };
      const custody = forkGraphCopy(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
      session.custody = custody; session.draft = next; session.observedAck = null; session.backupError = null;
      session.notice = "Saved Graph at r" + receipt.revision + "." + (Object.keys(next.edited).length ? " Later author input remains pending." : ""); copies(session);
    } catch (reason) {
      session.error = message(reason);
      if (session.observedAck) session.backupError = "The original reply was observed, but its local custody transfer was not confirmed. Saving remains blocked; retain both copies and inspect the saved source.";
      else { session.draft = failGraphSubmission(session.draft, id, message(reason)); backup(session); }
    } finally { session.inFlight = false; notify(session); }
  }
  async function readOutcome() {
    if (busy || reviewing || !session.draft.pending) return; const token = ++epoch.current; setReviewing(true); setReview(null); session.error = null;
    try { const result = await inspectGraphOutcome(clone(session.draft), current.current.project, current.current.outcomeTransport);
      if (mounted.current && token === epoch.current && graphOutcomeMatches(session.draft, current.current.project, result)) setReview(result);
    } catch (reason) { if (mounted.current && token === epoch.current) session.error = message(reason); }
    finally { if (mounted.current) setReviewing(false); notify(session); }
  }
  function fork(next: GraphDraft, notice: string) {
    if (!backup(session)) return; const custody = forkGraphCopy(window.localStorage, window.sessionStorage, session.draft, next, session.custody, crypto.randomUUID());
    session.custody = custody; session.draft = next; session.backupError = null; session.error = null; session.notice = notice; epoch.current++; setReview(null); setChoices({}); copies(session); notify(session);
  }
  function continueEditing() {
    if (busy || !review) return;
    try { fork(releaseGraphOutcome(session.draft, current.current.project, review), "The earlier operation remains in a separate copy. No ACK or retry was inferred. Review conflicts and save a new intention explicitly."); }
    catch (reason) { session.error = message(reason); notify(session); }
  }
  function reconcile() {
    if (busy || pending) return; try { fork(reviewGraphDraft(session.draft, current.current.project, choices), "Reviewed the current Graph source. Other authors' unrelated edits were preserved; save the new intention explicitly."); }
    catch (reason) { session.error = message(reason); notify(session); }
  }
  async function refresh() { try { await current.current.refreshProject(); } catch (reason) { session.error = message(reason); notify(session); } }
  function retryBackup() {
    if (busy) return;
    try {
      if (session.recoveryProblem) {
        const restored = readGraphRecoverySource(window.localStorage, window.sessionStorage, session.draft.scope, session.recoveryObservation);
        const next = restored ? recoverGraphRead(session.draft, restored, session.editedWhileRecoveryBlocked) : session.draft;
        assertGraphRecoverySource(window.localStorage, window.sessionStorage, session.draft.scope, session.recoveryObservation);
        session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.recoveryObservation.pointer };
        session.draft = next; session.recoveryProblem = null; session.editedWhileRecoveryBlocked = [];
        epoch.current++; setReview(null); setChoices({});
      }
      if (backup(session)) session.notice = "Verified the exact Graph data and pointer. No Project operation was sent."; notify(session);
    } catch (reason) { session.backupError = message(reason); notify(session); }
  }
  function download(raw = graphDownload(session.draft), filename = "motionwright-graph-author-input.json") {
    const url = URL.createObjectURL(new Blob([raw], { type: "application/json" })), a = document.createElement("a"); a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 0);
  }
  function restore(copy: GraphRecoveryEntry) {
    if (busy || pending || !copy.value) return;
    try { if (window.localStorage.getItem(copy.key) !== copy.raw) throw new Error("The selected Graph copy changed; refresh the list first.");
      const restored = decodeGraphCopy(copy.raw, session.draft.scope, copy.writerId).draft;
      fork(restored, "Restored a separate Graph draft. Earlier copies were retained; no save was sent.");
    } catch (reason) { session.error = message(reason); notify(session); }
  }
  function discard(copy: GraphRecoveryEntry) {
    try { discardGraphCopy(window.localStorage, window.sessionStorage, session.draft.scope, copy, session.custody.writerId); copies(session); session.notice = "Confirmed removal of the selected obsolete Graph copy."; notify(session); }
    catch (reason) { session.error = message(reason); notify(session); }
  }
  const dependencies = (context?.dependencies ?? []).filter((ref): ref is GraphTargetBinding => ref.kind === "claim" || ref.kind === "canvas_node" || ref.kind === "measured_datum");
  const saved = semanticGraphs(props.project).find(graph => graph.id === draft.graphId), linkedReviews = props.project.reviews.filter(item => item.anchor.resource === "graph:" + draft.graphId || item.anchor.resource.startsWith("graph-vertex:" + draft.graphId + ":") || item.anchor.resource.startsWith("graph-edge:" + draft.graphId + ":"));
  const reviewAnchor = graphReviewAnchor(props.project, reviewResource);
  return <div className="production-split">
    <section className="production-inspector" aria-label="Semantic Graph controls">
      <h2>Semantic Graph</h2><p>{raw.content.kind === "causal_diagram" ? "Causal diagram · declared influence and basis" : "System flow · declared synthetic events"}</p>
      <p className="production-help">The saved Graph is the editable source. The vector study is editorial; native realization and its evidence use the matching provider profile.</p>
      <button className="secondary-button" disabled={busy || inspecting || !props.readContext} onClick={() => void inspectContext()}>Inspect current Graph support and sources</button>
      {admission && <p role="status">{admission}</p>}
      <label className="production-field"><span>Graph Deliverable</span><select aria-label="Graph Deliverable" value={raw.variant_profile_ref} onChange={e => edit(next => { next.variant_profile_ref = e.target.value; }, "/variant_profile_ref")}>
        {props.project.deliverables.map(item => <option key={item.id} value={item.id}>{item.name} · {item.width} × {item.height}</option>)}
      </select></label>
      <p>Authored frame: {raw.reference_frame.width} × {raw.reference_frame.height}. Geometry is retained when the Deliverable changes.</p>
      <button className="secondary-button" disabled={!profile} onClick={() => profile && edit(next => { next.reference_frame = { width: String(profile.width), height: String(profile.height) }; }, "/reference_frame")}>Use selected Deliverable frame without rescaling geometry</button>
      <TextField label="Graph author label" value={raw.authorship.author_label} onChange={value => edit(next => { next.authorship.author_label = value; }, "/authorship/author_label")} />
      <TextField label="Graph rationale" value={raw.authorship.rationale} multiline onChange={value => edit(next => { next.authorship.rationale = value; }, "/authorship/rationale")} />
      <label className="production-field"><span>Graph brand reference</span><select aria-label="Graph brand reference" value={JSON.stringify(raw.brand_profile_ref)} onChange={e => edit(next => { next.brand_profile_ref = JSON.parse(e.target.value) as GraphRaw["brand_profile_ref"]; }, "/brand_profile_ref")}>
        <option value="null">Explicitly unbranded · current project rules still apply</option>
        {raw.brand_profile_ref && <option value={JSON.stringify(raw.brand_profile_ref)}>Current brand reference · verify freshness</option>}
        {(context?.dependencies ?? []).filter(ref => ref.kind === "brand").map(ref => ref.kind === "brand" && <option key={ref.id} value={JSON.stringify({ id: ref.id, version: String(ref.version), content_sha256: ref.content_sha256 })}>Brand {ref.id} · version {ref.version}</option>)}
      </select></label>
      <label className="production-field"><span>Graph subject</span><select aria-label="Graph subject" value={JSON.stringify(raw.subject_ref)} onChange={e => edit(next => { next.subject_ref = JSON.parse(e.target.value) as GraphRaw["subject_ref"]; }, "/subject_ref")}>
        <option value={JSON.stringify(raw.subject_ref)}>Current {raw.subject_ref.kind} subject</option>
        {props.project.scenes.map(scene => <option key={scene.id} value={JSON.stringify({ kind: "scene", scene_id: scene.id })}>{scene.name}</option>)}
        {(context?.dependencies ?? []).filter(ref => ref.kind === "claim" || ref.kind === "asset").map(ref => <option key={JSON.stringify(ref)} value={JSON.stringify(ref)}>{ref.kind} · {"claim_id" in ref ? ref.claim_id : "asset_id" in ref ? ref.asset_id : ""}</option>)}
      </select></label>
      <RationalFields label="Graph start" value={raw.time_range.start} onChange={(value, part) => edit(next => { next.time_range.start = value; }, "/time_range/start/" + part)} />
      <RationalFields label="Graph duration" value={raw.time_range.duration} onChange={(value, part) => edit(next => { next.time_range.duration = value; }, "/time_range/duration/" + part)} />
      <label className="production-field"><span>Graph motion</span><select aria-label="Graph motion" value={raw.motion} onChange={e => edit(next => { next.motion = e.target.value as "full" | "off"; }, "/motion")}><option value="full">Full · linear reveals</option><option value="off">Off · same event times without interpolation</option></select></label>
      {raw.content.kind === "system_flow" && <TextField label="Synthetic flow disclosure" multiline value={raw.content.source.disclosure} onChange={value => edit(next => { if (next.content.kind === "system_flow") next.content.source.disclosure = value; }, "/content/source/disclosure")} />}
      <p>Line breaks are preserved. Text does not wrap automatically; adjust its text, box or font size to fit.</p>
      <fieldset><legend>Graph style · fixed font weight 400</legend>
        <label className="production-field"><span>Graph font family</span><select aria-label="Graph font family" value={raw.style.font_family} onChange={e => edit(next => { next.style.font_family = e.target.value as GraphRaw["style"]["font_family"]; }, "/style/font_family")}><option>Instrument Sans Variable</option><option>IBM Plex Mono</option></select></label>
        {preview && <small><a href={graphPreviewFontLicense(preview.style.font_family)} target="_blank" rel="noreferrer">Preview font license</a></small>}
        {(["background", "vertex_fill", "text_color", "edge_color", "accent_color", "font_size", "edge_width"] as const).map(key => <TextField key={key} label={"Graph " + key} value={raw.style[key]} onChange={value => edit(next => { next.style[key] = value; }, "/style/" + key)} />)}
        <BoxFields label="Graph disclosure box" value={raw.style.disclosure_box} onChange={(key, value) => edit(next => { next.style.disclosure_box[key] = value; }, "/style/disclosure_box/" + key)} />
      </fieldset>
      <h3>Vertices</h3>
      {raw.content.vertices.map((v, index) => {
        const prefix = "Vertex " + (index + 1), path = "/content/vertices/" + v.id;
        const change = (fn: (vertex: GraphRaw["content"]["vertices"][number]) => void, field: string) => edit(next => fn(next.content.vertices.find(item => item.id === v.id)!), path + field);
        return <details key={v.id} open={index === 0} data-graph-vertex={v.id}><summary>{prefix} · {v.label || "Untitled concept"}</summary>
          <TextField label={prefix + " label"} multiline value={v.label} onChange={value => change(vertex => { vertex.label = value; }, "/label")} />
          <TextField label={prefix + " meaning"} value={v.meaning} multiline onChange={value => change(vertex => { vertex.meaning = value; }, "/meaning")} />
          <label className="production-field"><span>{prefix} unit type</span><select aria-label={prefix + " unit type"} value={v.unit.kind} onChange={e => change(vertex => { vertex.unit = e.target.value === "qualitative" ? { kind: "qualitative" } : { kind: "symbol", symbol: "" }; }, "/unit")}><option value="qualitative">Qualitative</option><option value="symbol">Measured unit symbol</option></select></label>
          {v.unit.kind === "symbol" && <TextField label={prefix + " unit symbol"} multiline value={v.unit.symbol} onChange={value => change(vertex => { vertex.unit = { kind: "symbol", symbol: value }; }, "/unit/symbol")} />}
          <label className="production-field"><span>{prefix} shape</span><select aria-label={prefix + " shape"} value={v.shape} onChange={e => change(vertex => { vertex.shape = e.target.value as "rectangle" | "circle"; }, "/shape")}><option value="rectangle">Rectangle</option><option value="circle">Circle</option></select></label>
          <BoxFields label={prefix + " box"} value={v.box} onChange={(key, value) => change(vertex => { vertex.box[key] = value; }, "/box/" + key)} />
          <label className="production-field"><span>{prefix} source binding</span><select aria-label={prefix + " source binding"} value={JSON.stringify(v.target)} onChange={e => change(vertex => { const selected = JSON.parse(e.target.value) as GraphTargetBinding | null; vertex.target = selected?.kind === "measured_datum" ? { ...selected, table_version: String(selected.table_version) } : selected; }, "/target")}>
            <option value="null">No association</option>{v.target && <option value={JSON.stringify(v.target)}>Current binding · verify freshness</option>}
            {dependencies.map(ref => <option key={JSON.stringify(ref)} value={JSON.stringify(ref)}>{optionLabel(ref)}</option>)}
          </select></label>
          <RationalFields label={prefix + " appearance start"} value={v.appearance.at} onChange={(value, part) => change(vertex => { vertex.appearance.at = value; }, "/appearance/at/" + part)} />
          <RationalFields label={prefix + " appearance duration"} value={v.appearance.duration} onChange={(value, part) => change(vertex => { vertex.appearance.duration = value; }, "/appearance/duration/" + part)} />
          <button className="text-button" disabled={raw.content.vertices.length <= 1 || raw.content.edges.some(edge => edge.from_vertex_id === v.id || edge.to_vertex_id === v.id)} onClick={() => edit(next => { next.content.vertices = next.content.vertices.filter(item => item.id !== v.id); }, path)}>Remove {prefix.toLowerCase()}</button>
          {raw.content.edges.some(edge => edge.from_vertex_id === v.id || edge.to_vertex_id === v.id) && <p>Remove or reconnect its edges before removing this vertex.</p>}
        </details>;
      })}
      <button className="secondary-button" disabled={raw.content.vertices.length >= 32} onClick={() => { const id = crypto.randomUUID(); edit(next => { const vertex = clone(next.content.vertices[0]); vertex.id = id; vertex.label = "New concept"; vertex.meaning = "Describe this concept."; vertex.target = null; next.content.vertices.push(vertex); }, "/content/vertices/" + id); }}>Add vertex</button>
      <h3>Edges</h3>
      {raw.content.edges.map((e, index) => {
        const prefix = "Edge " + (index + 1), path = "/content/edges/" + e.id;
        const change = (fn: (edge: GraphRaw["content"]["edges"][number]) => void, field: string) => edit(next => fn(next.content.edges.find(item => item.id === e.id)!), path + field);
        return <details key={e.id} data-graph-edge={e.id}><summary>{prefix} · {e.label || "Untitled relation"}</summary>
          <TextField label={prefix + " label"} multiline value={e.label} onChange={value => change(edge => { edge.label = value; }, "/label")} />
          {(["from", "to"] as const).map(side => <div key={side}>
            <label className="production-field"><span>{prefix + " " + side + " vertex"}</span><select aria-label={prefix + " " + side + " vertex"} value={e[side === "from" ? "from_vertex_id" : "to_vertex_id"]} onChange={event => change(edge => { edge[side === "from" ? "from_vertex_id" : "to_vertex_id"] = event.target.value; }, "/" + side + "_vertex_id")}>
              {raw.content.vertices.map(vertex => <option key={vertex.id} value={vertex.id}>{vertex.label || vertex.id}</option>)}
            </select></label><label className="production-field"><span>{prefix + " " + side + " port"}</span><select aria-label={prefix + " " + side + " port"} value={e[side === "from" ? "from_port" : "to_port"]} onChange={event => change(edge => { edge[side === "from" ? "from_port" : "to_port"] = event.target.value as "top" | "right" | "bottom" | "left"; }, "/" + side + "_port")}>
              {["top", "right", "bottom", "left"].map(port => <option key={port}>{port}</option>)}
            </select></label></div>)}
          {"influence" in e ? <>
            <label className="production-field"><span>{prefix} influence</span><select aria-label={prefix + " influence"} value={e.influence} onChange={event => change(edge => { if ("influence" in edge) edge.influence = event.target.value as typeof edge.influence; }, "/influence")}><option value="positive">Positive (+)</option><option value="negative">Negative (−)</option><option value="unspecified">Unspecified (?)</option></select></label>
            <label className="production-field"><span>{prefix} causal basis</span><select aria-label={prefix + " causal basis"} value={JSON.stringify(e.basis)} onChange={event => change(edge => { if ("basis" in edge) edge.basis = JSON.parse(event.target.value) as typeof edge.basis; }, "/basis")}>
              <option value={JSON.stringify(e.basis)}>Current {e.basis.kind}</option><option value={JSON.stringify({ kind: "author_hypothesis", disclosure: "" })}>Declare an author hypothesis</option>
              {dependencies.filter(ref => ref.kind === "claim").map(ref => <option key={JSON.stringify(ref)} value={JSON.stringify({ ...ref, kind: "claim_reference" })}>Claim {"claim_id" in ref ? ref.claim_id : ""}</option>)}
            </select></label>
            {e.basis.kind === "author_hypothesis" && <TextField label={prefix + " hypothesis disclosure"} multiline value={e.basis.disclosure} onChange={value => change(edge => { if ("basis" in edge && edge.basis.kind === "author_hypothesis") edge.basis.disclosure = value; }, "/basis/disclosure")} />}
          </> : <label className="production-field"><span>{prefix} channel</span><select aria-label={prefix + " channel"} value={e.channel} onChange={event => change(edge => { if ("channel" in edge) edge.channel = event.target.value as typeof edge.channel; }, "/channel")}><option value="data">Data</option><option value="control">Control</option><option value="event">Event</option></select></label>}
          <BoxFields label={prefix + " label box"} value={e.label_box} onChange={(key, value) => change(edge => { edge.label_box[key] = value; }, "/label_box/" + key)} />
          {e.waypoints.map((point, i) => <fieldset key={i}><legend>{prefix} waypoint {i + 1}</legend>{(["x", "y"] as const).map(key => <TextField key={key} label={prefix + " waypoint " + (i + 1) + " " + key} value={point[key]} onChange={value => change(edge => { edge.waypoints[i][key] = value; }, "/waypoints")} />)}<button className="text-button" onClick={() => change(edge => { edge.waypoints.splice(i, 1); }, "/waypoints")}>Remove {prefix.toLowerCase()} waypoint {i + 1}</button></fieldset>)}
          <button className="secondary-button" disabled={e.waypoints.length >= 8} onClick={() => change(edge => { edge.waypoints.push({ x: "0", y: "0" }); }, "/waypoints")}>Add {prefix.toLowerCase()} waypoint</button>
          <RationalFields label={prefix + " appearance start"} value={e.appearance.at} onChange={(value, part) => change(edge => { edge.appearance.at = value; }, "/appearance/at/" + part)} />
          <RationalFields label={prefix + " appearance duration"} value={e.appearance.duration} onChange={(value, part) => change(edge => { edge.appearance.duration = value; }, "/appearance/duration/" + part)} />
          <button className="text-button" disabled={raw.content.kind === "system_flow" && raw.content.events.some(event => event.edge_id === e.id)} onClick={() => edit(next => { if (next.content.kind === "causal_diagram") next.content.edges = next.content.edges.filter(edge => edge.id !== e.id); else next.content.edges = next.content.edges.filter(edge => edge.id !== e.id); }, path)}>Remove {prefix.toLowerCase()}</button>
        </details>;
      })}
      <button className="secondary-button" disabled={raw.content.vertices.length < 2 || raw.content.edges.length >= 64 || !profile} onClick={() => {
        if (!profile) return; const base = graphToRaw(newSemanticGraph(props.project, props.scene!, profile, raw.content.kind)).content.edges[0], id = crypto.randomUUID();
        edit(next => { const edge = { ...base, id, from_vertex_id: next.content.vertices[0].id, to_vertex_id: next.content.vertices[1].id };
          if (next.content.kind === "causal_diagram" && "basis" in edge) next.content.edges.push(edge); else if (next.content.kind === "system_flow" && "channel" in edge) next.content.edges.push(edge); }, "/content/edges/" + id);
      }}>Add edge</button>
      {raw.content.kind === "system_flow" && <>
        <h3>Synthetic flow events</h3>{raw.content.events.map((event, index) => {
          const prefix = "Event " + (index + 1), path = "/content/events/" + event.id;
          const change = (fn: (value: typeof event) => void, field: string) => edit(next => { if (next.content.kind === "system_flow") fn(next.content.events.find(item => item.id === event.id)!); }, path + field);
          return <details key={event.id} data-graph-event={event.id}><summary>{prefix} · {event.label}</summary>
            <TextField label={prefix + " label"} multiline value={event.label} onChange={value => change(item => { item.label = value; }, "/label")} />
            <label className="production-field"><span>{prefix} edge</span><select aria-label={prefix + " edge"} value={event.edge_id} onChange={e => change(item => { item.edge_id = e.target.value; }, "/edge_id")}>{raw.content.edges.map(edge => <option key={edge.id} value={edge.id}>{edge.label}</option>)}</select></label>
            <RationalFields label={prefix + " start"} value={event.at} onChange={(value, part) => change(item => { item.at = value; }, "/at/" + part)} />
            <RationalFields label={prefix + " duration"} value={event.duration} onChange={(value, part) => change(item => { item.duration = value; }, "/duration/" + part)} />
            <button className="text-button" onClick={() => edit(next => { if (next.content.kind === "system_flow") next.content.events = next.content.events.filter(item => item.id !== event.id); }, path)}>Remove {prefix.toLowerCase()}</button>
          </details>;
        })}
        <button className="secondary-button" disabled={!profile || !raw.content.edges.length || raw.content.events.length >= 64} onClick={() => {
          if (!profile) return; const id = crypto.randomUUID(); edit(next => { if (next.content.kind === "system_flow") next.content.events.push({ id, edge_id: next.content.edges[0].id, label: "Synthetic transfer", at: graphFrameTime(2, profile.frame_rate), duration: graphFrameTime(1, profile.frame_rate) }); }, "/content/events/" + id);
        }}>Add synthetic event</button>
      </>}
      {changed && !pending && <section aria-label="Graph source reconciliation"><h3>Saved Graph changed</h3><p>Your raw input remains retained. Review this revision before saving.</p>
        {merge?.conflicts.map(conflict => <label className="production-field" key={conflict.path}><span>{conflict.path || "Graph identity"}</span><select aria-label={"Resolve Graph " + (conflict.path || "identity")} value={choices[conflict.path] ?? ""} onChange={e => setChoices(old => ({ ...old, [conflict.path]: e.target.value as "current" | "input" }))}><option value="">Choose explicitly</option><option value="input">Keep my input</option><option value="current">Use current saved value</option></select><small>Saved: {JSON.stringify(conflict.current)} · Input: {JSON.stringify(conflict.input)}</small></label>)}
        <button className="secondary-button" disabled={busy} onClick={reconcile}>Review current Graph and keep unrelated changes</button>
      </section>}
      {pending && <section aria-label="Unconfirmed Graph submission"><h3>{graphUnconfirmed(draft) ? "Graph save outcome is unconfirmed" : "Graph change was not sent"}</h3>
        <p>The original attempt and later input are retained. A matching saved value or history entry does not acknowledge the original Promise.</p>
        <button className="secondary-button" disabled={busy} onClick={() => void refresh()}>Refresh saved Project</button>
        <button className="secondary-button" disabled={busy || reviewing} onClick={() => void readOutcome()}>Inspect saved Graph outcome</button>
        {review && <><p>Read current source at r{review.project.revision}. {review.events.length} history entries inspected; none is treated as an ACK.</p>
          {graphUnconfirmed(draft) && review.project.revision <= review.pending.revision && <p role="status">
            The original Graph request can still commit on this revision. Keep it UNKNOWN and inspect again after the saved revision advances.
          </p>}
          <button className="secondary-button" disabled={busy || (graphUnconfirmed(draft) && review.project.revision <= review.pending.revision)}
            onClick={continueEditing}>Continue Graph as a new reviewed intention</button></>}
      </section>}
      {inputIssue && <p role="status" className="production-error">{inputIssue}</p>}{lock && <p role="status">{lock}</p>}
      {session.backupError && <section aria-label="Graph recovery needs attention"><p role="alert">{session.backupError}</p><button className="secondary-button" disabled={busy} onClick={retryBackup}>Verify Graph recovery again</button>
        {session.recoveryProblem && session.recoveryObservation.rawObserved && session.recoveryObservation.raw !== null && <button className="secondary-button"
          onClick={() => download(session.recoveryObservation.raw!, "motionwright-graph-original-" + session.recoveryObservation.pointer + ".json")}>Download observed original Graph copy</button>}
      </section>}
      {session.error && <p role="alert" className="production-error">{session.error}</p>}{session.notice && <p role="status">{session.notice}</p>}
      <div className="production-button-row"><button className="primary-button" disabled={!canSave} onClick={() => void send("save")}>{saved ? "Save Graph changes" : "Save semantic Graph"}</button>
        {saved && <button className="text-button" disabled={!canSave} onClick={() => void send("remove")}>Remove saved Graph</button>}
        <button className="secondary-button" onClick={() => download()}>Download complete Graph input</button></div>
      <details><summary>Recoverable Graph copies ({session.copies.length}{session.copiesLimited ? "+" : ""})</summary>
        <button className="secondary-button" onClick={() => { copies(session); notify(session); }}>Refresh Graph copies</button>
        {session.copies.map(copy => <div key={copy.key}><p>{copy.writerId} · {copy.value ? copy.value.draft.pending ? "Unconfirmed attempt retained" : "Author input" : "Unreadable bytes retained"}</p>
          <button className="secondary-button" onClick={() => download(copy.raw, "motionwright-graph-copy-" + copy.writerId + ".json")}>Download copy {copy.writerId.slice(0, 8)}</button>
          <button className="secondary-button" disabled={busy || pending || !copy.value || copy.writerId === session.custody.writerId} onClick={() => restore(copy)}>Restore copy {copy.writerId.slice(0, 8)}</button>
          <button className="text-button" disabled={busy || copy.writerId === session.custody.writerId} onClick={() => discard(copy)}>Discard copy {copy.writerId.slice(0, 8)}</button></div>)}
      </details>
      <section aria-label="Saved Graph reviews"><h3>Review saved Graph</h3><label className="production-field"><span>Graph review anchor</span><select aria-label="Graph review anchor" value={reviewResource} onChange={e => setReviewResource(e.target.value)}>
        <option value={"graph:" + draft.graphId}>Whole Graph</option>{saved?.content.vertices.map(vertex => <option key={vertex.id} value={"graph-vertex:" + draft.graphId + ":" + vertex.id}>{vertex.label}</option>)}
        {saved?.content.edges.map(edge => <option key={edge.id} value={"graph-edge:" + draft.graphId + ":" + edge.id}>{edge.label}</option>)}
      </select></label><button className="secondary-button" disabled={!reviewAnchor || busy} onClick={() => reviewAnchor && props.openReview(reviewAnchor)}>Open review of saved Graph</button>
        <p>Reviews refer to the saved revision and stable Graph identity. Editorial or technical review does not claim native fidelity or human craft acceptance.</p>
        {linkedReviews.map(item => <p key={item.id}>{item.status} · {item.kind} · {item.body}</p>)}
      </section>
    </section>
    <section className="production-viewer" aria-label="Graph editorial preview"><h3>Editorial vector study</h3><p>Uncommitted source preview · no native render, SQLite save or fidelity acceptance is asserted.</p>
      <label className="production-field"><span>Graph editorial view</span><select aria-label="Graph editorial view" value={previewMode} onChange={e => setPreviewMode(e.target.value as "complete" | "playhead")}><option value="complete">Complete topology</option><option value="playhead">Follow the scene playhead</option></select></label>
      {preview ? <div className="production-preview-mat"><GraphPreview graph={preview} time={previewMode === "playhead" && props.playhead !== undefined
        ? props.playhead - Number(props.scene!.start.num) / Number(props.scene!.start.den) - Number(preview.time_range.start.num) / Number(preview.time_range.start.den) : undefined} /></div>
        : <p role="status">Finish the current geometric and timing fields to view the editorial study. Every raw input remains retained.</p>}
    </section>
  </div>;
}

function GraphPreview({ graph, time }: { graph: SemanticGraph; time?: number }) {
  const family = graph.style.font_family;
  const [fontAttempt, retryFont] = useState(0);
  const [fontState, setFontState] = useState<{ family: typeof family; ready: boolean; error: string | null }>({ family, ready: false, error: null });
  useEffect(() => {
    let current = true;
    setFontState({ family, ready: false, error: null });
    void loadGraphPreviewFont(family).then(() => {
      if (current) setFontState({ family, ready: true, error: null });
    }, reason => {
      if (current) setFontState({ family, ready: false, error: message(reason) });
    });
    return () => { current = false; };
  }, [family, fontAttempt]);
  const missing = graphPreviewMissingCodepoints(family, graphPreviewText(graph));
  if (missing.length) return <p role="status" className="production-help">Preview paused: {family} does not include {missing.slice(0, 8).join(", ")}{missing.length > 8 ? " and more characters" : ""}. Choose another supported font or edit the labels. The Graph input is preserved.</p>;
  if (fontState.family !== family || !fontState.ready) return fontState.family === family && fontState.error
    ? <div className="production-help"><p role="alert">Preview unavailable: {fontState.error} The Graph input is preserved.</p><button className="secondary-button" onClick={() => retryFont(value => value + 1)}>Retry Graph preview fonts</button></div>
    : <p role="status">Loading Graph preview fonts...</p>;
  const t = time ?? Number(graph.time_range.duration.num) / Number(graph.time_range.duration.den), w = graph.reference_frame.width, h = graph.reference_frame.height;
  const inInterval = time === undefined || (t >= 0 && t < Number(graph.time_range.duration.num) / Number(graph.time_range.duration.den));
  const activeEvents = graph.content.kind === "system_flow" ? graph.content.events.filter(event => {
    const at = Number(event.at.num) / Number(event.at.den), end = at + Number(event.duration.num) / Number(event.duration.den); return t >= at && t < end;
  }) : [];
  const opacity = (appearance: { at: { num: string; den: string }; duration: { num: string; den: string } }) => {
    const at = Number(appearance.at.num) / Number(appearance.at.den), duration = Number(appearance.duration.num) / Number(appearance.duration.den);
    return graph.motion === "off" ? t >= at ? 1 : 0 : Math.max(0, Math.min(1, (t - at) / duration));
  };
  const textBox = (label: string, box: { x: number; y: number; width: number; height: number }, key: string) => {
    // Fixed v1 positions; this editorial SVG does not attest native font availability or fit.
    // Preserve literal LF, spaces and empty lines; never wrap, normalize, clip or scale the source.
    const lines = label.split("\n"), fontSize = graph.style.font_size, lineHeight = 1.2 * fontSize;
    return <g key={key} data-graph-label={key}>{lines.map((line, index) => <text key={index} data-graph-label-line={index}
      x={box.x + box.width / 2} y={box.y + box.height / 2 + (index - (lines.length - 1) / 2) * lineHeight + .35 * fontSize}
      textAnchor="middle" dominantBaseline="alphabetic" xmlSpace="preserve" fill={graph.style.text_color}
      style={{ fontFamily: graphPreviewFontFamily(family), fontWeight: 400, fontSize, fontStyle: "normal", fontStretch: "normal",
        fontKerning: "normal", fontVariantLigatures: "normal", fontSynthesis: "none", letterSpacing: 0, wordSpacing: 0, whiteSpace: "pre" }}>{line}</text>)}</g>;
  };
  return <svg className="production-study" role="img" aria-label="Editorial semantic Graph vector study" viewBox={`0 0 ${w} ${h}`} style={{ background: graph.style.background }}>
    <defs><marker id={"graph-arrow-" + graph.id} markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="strokeWidth"><path d="M 0 0 L 10 5 L 0 10 z" fill={graph.style.edge_color} /></marker></defs>
    <g opacity={inInterval ? 1 : 0} data-graph-active-interval={inInterval}>
    {graph.content.edges.map(edge => <g key={edge.id} opacity={opacity(edge.appearance)}><polyline points={graphEdgePoints(graph, edge).map(point => `${point.x},${point.y}`).join(" ")} stroke={graph.style.edge_color} strokeWidth={graph.style.edge_width} fill="none" markerEnd={`url(#graph-arrow-${graph.id})`} />
      {textBox(edge.label + ("influence" in edge ? " · " + ({ positive: "+", negative: "−", unspecified: "?" }[edge.influence]) : " · " + edge.channel)
        + activeEvents.filter(event => event.edge_id === edge.id).map(event => "\n" + event.label).join(""), edge.label_box, edge.id + "-label")}</g>)}
    {graph.content.vertices.map(vertex => <g key={vertex.id} opacity={opacity(vertex.appearance)}>{vertex.shape === "circle" ? <ellipse cx={vertex.box.x + vertex.box.width / 2} cy={vertex.box.y + vertex.box.height / 2} rx={vertex.box.width / 2} ry={vertex.box.height / 2} fill={graph.style.vertex_fill} /> : <rect {...vertex.box} rx={Math.min(vertex.box.width, vertex.box.height) * .04} ry={Math.min(vertex.box.width, vertex.box.height) * .04} fill={graph.style.vertex_fill} />}
      {textBox(vertex.label + (vertex.unit.kind === "symbol" ? " (" + vertex.unit.symbol + ")" : ""), vertex.box, vertex.id + "-label")}</g>)}
    {textBox(graph.content.kind === "system_flow" ? graph.content.source.disclosure : graph.content.edges.map(edge => edge.basis.kind === "author_hypothesis" ? "Hypothesis: " + edge.basis.disclosure : "Declared claim: " + edge.basis.claim_id).join("\n"), graph.style.disclosure_box, "disclosure")}
    {activeEvents.map(event => {
      const edge = graph.content.edges.find(item => item.id === event.edge_id)!; return <g key={event.id}><polyline points={graphEdgePoints(graph, edge).map(point => `${point.x},${point.y}`).join(" ")} stroke={graph.style.accent_color} strokeWidth={graph.style.edge_width * 2} fill="none" /></g>;
    })}
    </g>
    {!inInterval && <text x={w / 2} y={h / 2} textAnchor="middle" fill={graph.style.text_color} fontSize={graph.style.font_size} style={{ fontFamily: graphPreviewFontFamily(family), fontWeight: 400, fontSynthesis: "none" }}>Outside the authored Graph interval</text>}
  </svg>;
}
