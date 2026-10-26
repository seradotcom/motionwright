/** Author-buffer custody and field reconciliation. This module cannot mutate a Project. */
import type { Project, ProjectEvent } from "./types";
import { graphExact, graphRecord, graphSame, graphUuid, graphSourceSnapshot, graphFromRaw, graphToRaw,
  graphLockIssue, semanticGraphs, validateSemanticGraphForWrite, validateSemanticGraphShape } from "./semanticGraph";
import type { GraphAuthoringContext, GraphRaw, SemanticGraph, SemanticGraphChange } from "./semanticGraph";

export interface GraphPending {
  id: string; revision: number; sequence: number; submitted: SemanticGraphChange; input: GraphRaw;
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface GraphDraft {
  schema: "motionwright.semantic-graph-draft/1";
  scope: string; sceneId: string; graphId: string; baseRevision: number; base: GraphRaw | null;
  input: GraphRaw; sequence: number; edited: Record<string, number>; pending: GraphPending | null;
}
export interface GraphConflict { path: string; base: unknown; current: unknown; input: unknown }
export interface GraphMerge { input: GraphRaw; conflicts: GraphConflict[] }
export interface GraphOutcomeReview { scope: string; pending: GraphPending; sequence: number; input: GraphRaw; project: Project; events: ProjectEvent[] }
export interface GraphOutcomeTransport { readProject(id: string): Promise<Project>; readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]> }
export type GraphStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface GraphCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
/** Unknown reads are different from an observed empty pointer. Observed bytes never change owners on Retry. */
export interface GraphRecoveryObservation { pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null }
export interface GraphCopy { schema: "motionwright.semantic-graph-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: GraphDraft }
export interface GraphRecoveryEntry { key: string; writerId: string; raw: string; value: GraphCopy | null }
export const GRAPH_RECOVERY_PREFIX = "motionwright.semantic-graph-recovery.v1:";
export const GRAPH_POINTER_PREFIX = "motionwright.semantic-graph-writer.v1:";
export const GRAPH_COPY_BYTES = 1024 * 1024, GRAPH_TOTAL_BYTES = 4 * 1024 * 1024, GRAPH_COPY_COUNT = 64;
const clone = <T,>(value: T): T => structuredClone(value);
const size = (value: string) => new TextEncoder().encode(value).length;
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const error = (message: string): never => { throw new Error(message); };
export const graphDraftScope = (project: Project, sceneId: string, graphId: string) => JSON.stringify([project.id, project.generation, project.active_branch, sceneId, graphId]);
export const graphCopyKey = (scope: string, writer: string) => GRAPH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const graphPointerKey = (scope: string) => GRAPH_POINTER_PREFIX + encodeURIComponent(scope);
export const graphHasWork = (draft: GraphDraft) => Object.keys(draft.edited).length > 0 || draft.base === null || draft.pending !== null;
export const graphUnconfirmed = (draft: GraphDraft) => draft.pending?.outcome === "UNKNOWN";
function checkScope(draft: GraphDraft, project: Project) {
  if (draft.scope !== graphDraftScope(project, draft.sceneId, draft.graphId)) error("The Graph draft belongs to another owner scope (project, generation, branch or scene).");
  if (!integer(project.revision)) error("The current revision cannot be represented exactly; no Graph intention can be formed.");
}
const currentRaw = (project: Project, graphId: string) => {
  const graph = semanticGraphs(project).find(item => item.id === graphId); return graph ? graphToRaw(graph) : null;
};
export function createGraphDraft(project: Project, initial: SemanticGraph): GraphDraft {
  if (!integer(project.revision)) error("An exact current revision is required.");
  return { schema: "motionwright.semantic-graph-draft/1", scope: graphDraftScope(project, initial.scene_id, initial.id), sceneId: initial.scene_id,
    graphId: initial.id, baseRevision: project.revision, base: currentRaw(project, initial.id), input: graphToRaw(initial), sequence: 0, edited: {}, pending: null };
}
const idArray = (value: unknown): value is Array<Record<string, unknown> & { id: string }> => Array.isArray(value) && value.every(item => graphRecord(item) && typeof item.id === "string");
function differences(before: unknown, after: unknown, path = ""): string[] {
  if (graphSame(before, after)) return [];
  if (idArray(before) && idArray(after) && /\/(vertices|edges|events)$/.test(path)) {
    const ids = new Set([...before.map(item => item.id), ...after.map(item => item.id)]);
    return [...ids].flatMap(id => differences(before.find(item => item.id === id), after.find(item => item.id === id), path + "/" + id));
  }
  if (graphRecord(before) && graphRecord(after) && before.kind === after.kind) return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .flatMap(key => differences(before[key], after[key], path + "/" + key));
  return [path];
}
export function editGraphDraft(draft: GraphDraft, input: GraphRaw, explicitPaths?: string[]): GraphDraft {
  if (input.id !== draft.graphId || input.scene_id !== draft.sceneId || input.content.kind !== draft.input.content.kind) error("Editing cannot migrate a Graph identity or semantic kind.");
  const sequence = draft.sequence + 1; if (!integer(sequence)) error("Graph edit sequence exhausted; preserve this input before continuing.");
  const edited = { ...draft.edited };
  for (const path of explicitPaths ?? differences(draft.input, input)) edited[path] = sequence;
  return { ...draft, input: clone(input), sequence, edited };
}
const touched = (draft: GraphDraft, path: string) => Object.keys(draft.edited).some(key => key === path || key.startsWith(path + "/") || path.startsWith(key + "/"));
const meaningful = (graph: GraphRaw | null): GraphRaw | null => {
  if (!graph) return null; const result = clone(graph);
  // Authorship stamps name the submitted mutation and do not erase field intent.
  result.authorship.source_snapshot = { project_id: "", generation: "", branch_id: "", revision: 0 }; return result;
};
/** Stable IDs identify collection entries. Matching labels and array indices never do. */
export function mergeGraphDraft(draft: GraphDraft, project: Project): GraphMerge {
  checkScope(draft, project); const latest = currentRaw(project, draft.graphId), conflicts: GraphConflict[] = [];
  if (draft.base === null) {
    if (latest && !graphSame(meaningful(latest), meaningful(draft.input))) conflicts.push({ path: "", base: null, current: latest, input: draft.input });
    return { input: clone(draft.input), conflicts };
  }
  if (!latest) return { input: clone(draft.input), conflicts: [{ path: "", base: draft.base, current: null, input: draft.input }] };
  const merge = (base: unknown, local: unknown, remote: unknown, path: string): unknown => {
    if (path === "/authorship/source_snapshot") return clone(remote);
    if (graphSame(base, remote)) return clone(local);
    if (!touched(draft, path) && graphSame(local, base)) return clone(remote);
    if (graphSame(local, remote)) return clone(local);
    if (idArray(base) && idArray(local) && idArray(remote) && /\/(vertices|edges|events)$/.test(path)) {
      const ids = [...remote.map(item => item.id), ...local.map(item => item.id).filter(id => !remote.some(item => item.id === id))];
      return ids.flatMap(id => {
        const old = base.find(item => item.id === id), own = local.find(item => item.id === id), peer = remote.find(item => item.id === id);
        const result = merge(old, own, peer, path + "/" + id); return result === undefined ? [] : [result];
      });
    }
    if (graphRecord(base) && graphRecord(local) && graphRecord(remote) && base.kind === local.kind && base.kind === remote.kind) {
      return Object.fromEntries([...new Set([...Object.keys(remote), ...Object.keys(local)])].flatMap(key => {
        const result = merge(base[key], local[key], remote[key], path + "/" + key); return result === undefined ? [] : [[key, result]];
      }));
    }
    conflicts.push({ path, base: clone(base), current: clone(remote), input: clone(local) }); return clone(local);
  };
  return { input: merge(draft.base, draft.input, latest, "") as GraphRaw, conflicts };
}
/** Background props may update a clean source only. UNKNOWN retains its exact original snapshot. */
export function reconcileGraphDraft(draft: GraphDraft, project: Project): GraphDraft {
  checkScope(draft, project);
  if (draft.pending || graphHasWork(draft) || draft.baseRevision === project.revision) return draft;
  const latest = currentRaw(project, draft.graphId);
  return latest ? { ...draft, baseRevision: project.revision, base: clone(latest), input: clone(latest) } : draft;
}
function setPath(value: unknown, parts: string[], replacement: unknown): unknown {
  if (!parts.length) return clone(replacement);
  const [head, ...rest] = parts;
  if (idArray(value)) {
    const old = value.find(item => item.id === head), next = setPath(old, rest, replacement);
    if (old) return value.flatMap(item => item.id !== head ? [item] : next === undefined ? [] : [next as Record<string, unknown> & { id: string }]);
    return next === undefined ? clone(value) : [...clone(value), next as Record<string, unknown> & { id: string }];
  }
  if (!graphRecord(value)) error("The selected conflict path no longer exists.");
  const next = setPath((value as Record<string, unknown>)[head], rest, replacement), result = { ...(value as Record<string, unknown>) };
  if (next === undefined) delete result[head]; else result[head] = next; return result;
}
/** One explicit three-way decision, retaining the old copy through the storage fork. */
export function reviewGraphDraft(draft: GraphDraft, project: Project, choices: Record<string, "current" | "input"> = {}): GraphDraft {
  if (draft.pending) error("Review the unconfirmed Graph operation before preparing a new intention.");
  const merged = mergeGraphDraft(draft, project); let input = merged.input;
  for (const conflict of merged.conflicts) {
    const choice = choices[conflict.path]; if (!choice) error("Resolve Graph conflict at " + (conflict.path || "the Graph identity") + ".");
    if (choice === "current" && conflict.path === "" && conflict.current === null) error("The saved Graph was removed. Preserve or discard the local draft explicitly before creating another identity.");
    if (choice === "current") input = setPath(input, conflict.path.split("/").filter(Boolean), conflict.current) as GraphRaw;
  }
  const base = currentRaw(project, draft.graphId), edited: Record<string, number> = {};
  differences(base, input).forEach(path => { edited[path] = draft.sequence + 1; });
  // Human return-to-base remains intent unless that exact leaf was resolved to current.
  for (const [path, sequence] of Object.entries(draft.edited)) if (!Object.keys(choices).some(choice => choices[choice] === "current" && (path === choice || path.startsWith(choice + "/")))) edited[path] = sequence;
  return { ...draft, input, base, baseRevision: project.revision, edited, sequence: draft.sequence + 1, pending: null };
}
export async function prepareGraphSubmission(draft: GraphDraft, project: Project, context: GraphAuthoringContext | null, id: string, operation: "save" | "remove" = "save") {
  checkScope(draft, project); if (!graphUuid(id)) error("Submission identity must be a UUID.");
  if (draft.pending) error("The earlier Graph operation remains unconfirmed; it cannot be replayed.");
  if (draft.baseRevision !== project.revision) error("Review the current saved Graph explicitly before saving a new intention.");
  if (!graphSame(draft.base, currentRaw(project, draft.graphId))) error("Graph source changed without the expected revision. Read and review it again.");
  const graph = graphFromRaw(draft.input); graph.authorship.source_snapshot = graphSourceSnapshot(project);
  await validateSemanticGraphForWrite(project, graph, context);
  let change: SemanticGraphChange = { type: "upsert_semantic_graph", graph };
  if (operation === "remove") {
    const saved = semanticGraphs(project).find(item => item.id === draft.graphId); if (!saved) error("The saved Graph no longer exists.");
    const lock = graphLockIssue(project, saved!); if (lock) error(lock);
    change = { type: "remove_semantic_graph", graph_id: draft.graphId, scene_id: draft.sceneId, source_snapshot: graphSourceSnapshot(project) };
  }
  return { change, draft: { ...draft, pending: { id, revision: project.revision, sequence: draft.sequence, submitted: clone(change), input: clone(draft.input), outcome: "UNKNOWN", diagnostic: null } } as GraphDraft };
}
export function failGraphSubmission(draft: GraphDraft, id: string, diagnostic: string, notSent = false): GraphDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, diagnostic, outcome: notSent ? "NOT_SENT" : "UNKNOWN" } } : draft;
}
export function submittedGraphMatches(project: Project, change: SemanticGraphChange): boolean {
  if (change.type === "remove_semantic_graph") return !semanticGraphs(project).some(item => item.id === change.graph_id);
  return graphSame(semanticGraphs(project).find(item => item.id === change.graph.id), change.graph);
}
/** Call only from the exact original Promise<Project>; props and history never call this. */
export function acknowledgeGraphDraft(draft: GraphDraft, id: string, receipt: Project): GraphDraft {
  checkScope(draft, receipt); const sent = draft.pending;
  if (!sent || sent.id !== id || receipt.revision !== sent.revision + 1 || !submittedGraphMatches(receipt, sent.submitted)) error("The original Graph reply is not the exact scoped r+1 result; retain UNKNOWN.");
  const edited = Object.fromEntries(Object.entries(draft.edited).filter(([, sequence]) => sequence > sent!.sequence));
  const saved = currentRaw(receipt, draft.graphId), hasLater = draft.sequence > sent!.sequence;
  return { ...draft, baseRevision: receipt.revision, base: saved, input: hasLater ? clone(draft.input) : clone(saved ?? draft.input), edited, pending: null };
}
export function graphOutcomeMatches(draft: GraphDraft, selected: Project, review: GraphOutcomeReview | null): boolean {
  return !!review && !!draft.pending && review.scope === draft.scope && graphSame(review.pending, draft.pending) && review.sequence === draft.sequence
    && graphSame(review.input, draft.input) && graphSame(review.project, selected);
}
export async function inspectGraphOutcome(draft: GraphDraft, selected: Project, transport: GraphOutcomeTransport): Promise<GraphOutcomeReview> {
  checkScope(draft, selected); if (!draft.pending) error("There is no unconfirmed Graph operation to inspect.");
  const selectedSource = clone(selected), project = clone(await transport.readProject(selectedSource.id)); checkScope(draft, project);
  if (!graphSame(project, selectedSource) || project.revision < draft.pending!.revision) error("The service read is not the currently selected exact source. Refresh the Project before reviewing this attempt.");
  const events = await transport.readHistory(clone(project), draft.pending!.revision, 256);
  const verify = await transport.readProject(selectedSource.id); if (!graphSame(project, verify)) error("The Graph source changed during outcome inspection. Read again.");
  if (events.some(e => e.revision <= draft.pending!.revision || e.revision > project.revision)) error("Graph history is outside the inspected revision interval.");
  return { scope: draft.scope, pending: clone(draft.pending!), sequence: draft.sequence, input: clone(draft.input), project, events };
}
export function releaseGraphOutcome(draft: GraphDraft, project: Project, review: GraphOutcomeReview): GraphDraft {
  if (!graphOutcomeMatches(draft, project, review)) error("The Graph draft or selected source changed after inspection.");
  if (graphUnconfirmed(draft) && project.revision <= draft.pending!.revision)
    error("The original Graph request could still commit on its base. Keep it UNKNOWN.");
  const sent = draft.pending!, sameSubmitted = submittedGraphMatches(project, sent.submitted), without = { ...draft, pending: null };
  // A saved equality is context for a new human intention, never an ACK. If the
  // exact submitted value is current, rebase later edits onto it after review.
  if (sameSubmitted && sent.submitted.type === "upsert_semantic_graph") return { ...without, base: currentRaw(project, draft.graphId), baseRevision: project.revision,
    edited: Object.fromEntries(Object.entries(draft.edited).filter(([, seq]) => seq > sent.sequence)) };
  // Leave the old base so actual third-value overlaps remain visible to the
  // explicit three-way review. Updating just the revision would lose that proof.
  return without;
}

function rawString(value: unknown) { if (typeof value !== "string") error("Recovery raw fields must retain strings."); }
function rawRational(value: unknown) { graphExact(value, ["num", "den"], "Raw rational"); rawString(value.num); rawString(value.den); }
function rawBox(value: unknown) { graphExact(value, ["x", "y", "width", "height"], "Raw box"); Object.values(value).forEach(rawString); }
function rawAppearance(value: unknown) { graphExact(value, ["at", "duration"], "Raw appearance"); rawRational(value.at); rawRational(value.duration); }
function rawRef(value: unknown, subject: boolean) {
  if (!graphRecord(value)) error("Unreadable raw Graph reference."); const v = value as Record<string, unknown>;
  const fields = v.kind === "scene" && subject ? ["kind", "scene_id"] : v.kind === "asset" && subject ? ["kind", "asset_id", "content_sha256"]
    : v.kind === "claim" ? ["kind", "claim_id", "content_sha256"] : v.kind === "canvas_node" && !subject ? ["kind", "scene_id", "node_id", "content_sha256"]
    : v.kind === "measured_datum" && !subject ? ["kind", "table_id", "table_version", "datum_key", "source_sha256"] : null;
  if (!fields) error("Unknown raw Graph reference kind."); graphExact(v, fields!, "Raw reference"); Object.values(v).forEach(rawString);
}
/** Closed recovery shape accepts incomplete text/numbers; validation belongs to Save. */
export function validateGraphRaw(raw: unknown): asserts raw is GraphRaw {
  graphExact(raw, ["id", "scene_id", "component_version", "subject_ref", "time_range", "brand_profile_ref", "variant_profile_ref", "reference_frame", "motion", "style", "content", "authorship"], "Raw Graph");
  if (!graphUuid(raw.id) || !graphUuid(raw.scene_id) || !graphUuid(raw.variant_profile_ref) || raw.component_version !== 1 || !["full", "off"].includes(String(raw.motion))) error("Raw Graph identity or version is invalid.");
  rawRef(raw.subject_ref, true); graphExact(raw.time_range, ["start", "duration"], "Raw interval"); rawRational(raw.time_range.start); rawRational(raw.time_range.duration);
  graphExact(raw.reference_frame, ["width", "height"], "Raw frame"); Object.values(raw.reference_frame).forEach(rawString);
  if (raw.brand_profile_ref !== null) { graphExact(raw.brand_profile_ref, ["id", "version", "content_sha256"], "Raw brand"); Object.values(raw.brand_profile_ref).forEach(rawString); }
  graphExact(raw.style, ["background", "vertex_fill", "text_color", "edge_color", "accent_color", "font_family", "font_size", "edge_width", "disclosure_box"], "Raw style");
  Object.entries(raw.style).forEach(([key, value]) => key === "disclosure_box" ? rawBox(value) : rawString(value));
  graphExact(raw.authorship, ["author_label", "rationale", "source_snapshot"], "Raw authorship"); rawString(raw.authorship.author_label); rawString(raw.authorship.rationale);
  graphExact(raw.authorship.source_snapshot, ["project_id", "generation", "branch_id", "revision"], "Raw source");
  if (![raw.authorship.source_snapshot.project_id, raw.authorship.source_snapshot.generation, raw.authorship.source_snapshot.branch_id].every(graphUuid) || !integer(raw.authorship.source_snapshot.revision)) error("Unreadable authorship source snapshot.");
  if (!graphRecord(raw.content)) error("Unreadable raw content."); const content = raw.content as Record<string, unknown>;
  graphExact(content, content.kind === "causal_diagram" ? ["kind", "vertices", "edges"] : content.kind === "system_flow" ? ["kind", "source", "vertices", "edges", "events"] : [], "Raw content");
  if (!Array.isArray(content.vertices) || content.vertices.length > 32 || !Array.isArray(content.edges) || content.edges.length > 64) error("Unreadable Graph collection budget.");
  const ids = new Set<string>(); const own = (id: unknown) => { if (!graphUuid(id) || ids.has(id)) error("Duplicate or invalid recovery identity."); ids.add(id as string); };
  for (const v of content.vertices as unknown[]) {
    graphExact(v, ["id", "label", "meaning", "unit", "box", "shape", "target", "appearance"], "Raw vertex"); own(v.id); rawString(v.label); rawString(v.meaning); rawString(v.shape);
    if (!graphRecord(v.unit)) error("Unreadable raw unit."); graphExact(v.unit, (v.unit as Record<string, unknown>).kind === "qualitative" ? ["kind"] : ["kind", "symbol"], "Raw unit"); Object.values(v.unit).forEach(rawString);
    rawBox(v.box); rawAppearance(v.appearance); if (v.target !== null) rawRef(v.target, false);
  }
  for (const e of content.edges as unknown[]) {
    graphExact(e, ["id", "label", "from_vertex_id", "to_vertex_id", "from_port", "to_port", "waypoints", "label_box", "appearance",
      ...(graphRecord(e) && Object.hasOwn(e, "label_placement") ? ["label_placement"] : []),
      ...(content.kind === "causal_diagram" ? ["influence", "basis"] : ["channel"])], "Raw edge"); own(e.id);
    [e.label, e.from_vertex_id, e.to_vertex_id, e.from_port, e.to_port].forEach(rawString); rawBox(e.label_box); rawAppearance(e.appearance);
    if (Object.hasOwn(e, "label_placement")) {
      graphExact(e.label_placement, ["kind", "offset"], "Raw edge label placement");
      if (e.label_placement.kind !== "polyline_midpoint") error("Unsupported raw edge label placement.");
      graphExact(e.label_placement.offset, ["x", "y"], "Raw edge label offset");
      Object.values(e.label_placement.offset).forEach(rawString);
    }
    if (!Array.isArray(e.waypoints) || e.waypoints.length > 8) error("Unreadable raw waypoints."); for (const p of e.waypoints as unknown[]) { graphExact(p, ["x", "y"], "Raw waypoint"); Object.values(p).forEach(rawString); }
    if (content.kind === "causal_diagram") { rawString(e.influence); if (!graphRecord(e.basis)) error("Unreadable raw causal basis."); graphExact(e.basis, (e.basis as Record<string, unknown>).kind === "author_hypothesis" ? ["kind", "disclosure"] : ["kind", "claim_id", "content_sha256"], "Raw causal basis"); Object.values(e.basis).forEach(rawString); }
    else rawString(e.channel);
  }
  if (content.kind === "system_flow") { graphExact(content.source, ["kind", "disclosure"], "Raw flow source"); if (content.source.kind !== "synthetic") error("Recovered flow must remain synthetic."); rawString(content.source.disclosure);
    if (!Array.isArray(content.events) || content.events.length > 64) error("Unreadable raw events."); for (const e of content.events as unknown[]) { graphExact(e, ["id", "edge_id", "label", "at", "duration"], "Raw event"); own(e.id); rawString(e.edge_id); rawString(e.label); rawRational(e.at); rawRational(e.duration); }
  }
}
function cleanGraphDraft(value: unknown, scope: string, restored: boolean): GraphDraft {
  graphExact(value, ["schema", "scope", "sceneId", "graphId", "baseRevision", "base", "input", "sequence", "edited", "pending"], "Graph recovery draft");
  if (value.schema !== "motionwright.semantic-graph-draft/1" || value.scope !== scope || !graphUuid(value.sceneId) || !graphUuid(value.graphId)
    || !integer(value.baseRevision) || !integer(value.sequence) || !graphRecord(value.edited)) error("Unreadable Graph recovery identity.");
  const parts: unknown = JSON.parse(scope);
  if (!Array.isArray(parts) || parts.length !== 5 || !parts.every(graphUuid) || parts[3] !== value.sceneId || parts[4] !== value.graphId) error("Graph recovery belongs to another owner scope.");
  if (value.base !== null) validateGraphRaw(value.base); validateGraphRaw(value.input);
  if (value.input.id !== value.graphId || value.input.scene_id !== value.sceneId || (value.base && ((value.base as GraphRaw).id !== value.graphId || (value.base as GraphRaw).scene_id !== value.sceneId))) error("Graph recovery identities disagree.");
  if (Object.entries(value.edited as Record<string, unknown>).some(([path, sequence]) => !path.startsWith("/") && path !== "" || !integer(sequence) || sequence > (value.sequence as number))) error("Unreadable Graph field edit sequence.");
  let pending: GraphPending | null = null;
  if (value.pending !== null) {
    graphExact(value.pending, ["id", "revision", "sequence", "submitted", "input", "outcome", "diagnostic"], "Graph pending comparison"); const p = value.pending;
    if (!graphUuid(p.id) || !integer(p.revision) || p.revision !== value.baseRevision || !integer(p.sequence) || p.sequence > (value.sequence as number)
      || !["UNKNOWN", "NOT_SENT"].includes(String(p.outcome)) || (p.diagnostic !== null && typeof p.diagnostic !== "string")) error("Unreadable Graph pending comparison.");
    validateGraphRaw(p.input); if (p.input.id !== value.graphId || p.input.scene_id !== value.sceneId) error("Foreign pending Graph input.");
    if (!graphRecord(p.submitted)) error("Unreadable submitted Graph change."); const submitted = p.submitted as Record<string, unknown>;
    if (submitted.type === "upsert_semantic_graph") { graphExact(submitted, ["type", "graph"], "Submitted Graph"); validateSemanticGraphShape(submitted.graph); if (submitted.graph.id !== value.graphId || submitted.graph.scene_id !== value.sceneId || submitted.graph.authorship.source_snapshot.revision !== p.revision) error("Foreign submitted Graph comparison."); }
    else if (submitted.type === "remove_semantic_graph") { graphExact(submitted, ["type", "graph_id", "scene_id", "source_snapshot"], "Graph removal"); graphExact(submitted.source_snapshot, ["project_id", "generation", "branch_id", "revision"], "Removal source"); if (submitted.graph_id !== value.graphId || submitted.scene_id !== value.sceneId || submitted.source_snapshot.revision !== p.revision) error("Foreign Graph removal comparison."); }
    else error("Unsupported submitted Graph change.");
    const stamp = submitted.type === "upsert_semantic_graph" ? (submitted.graph as SemanticGraph).authorship.source_snapshot : submitted.source_snapshot;
    if (!graphRecord(stamp) || stamp.project_id !== (parts as string[])[0] || stamp.generation !== (parts as string[])[1] || stamp.branch_id !== (parts as string[])[2]) error("The pending Graph comparison belongs to another source.");
    pending = { ...(clone(p) as unknown as GraphPending), outcome: restored ? "UNKNOWN" : p.outcome as GraphPending["outcome"] };
  }
  return { ...(clone(value) as unknown as GraphDraft), pending };
}
export function decodeGraphCopy(raw: string, scope: string, writerId: string, restored = true): GraphCopy {
  if (size(raw) > GRAPH_COPY_BYTES) error("Graph recovery exceeds 1 MiB; preserve the original bytes."); const value: unknown = JSON.parse(raw);
  graphExact(value, ["schema", "scope", "writerId", "updatedAt", "draft"], "Graph recovery copy");
  if (value.schema !== "motionwright.semantic-graph-recovery/1" || value.scope !== scope || value.writerId !== writerId || !graphUuid(writerId) || !integer(value.updatedAt)) error("Unreadable or foreign Graph recovery copy.");
  return { schema: "motionwright.semantic-graph-recovery/1", scope, writerId, updatedAt: value.updatedAt as number, draft: cleanGraphDraft(value.draft, scope, restored) };
}
/** Read only. A failed read preserves every identity/byte already observed for the next explicit Retry. */
export function readGraphRecoverySource(storage: Pick<GraphStorage, "getItem">, pointers: Pick<Storage, "getItem">,
  scope: string, observed: GraphRecoveryObservation): GraphDraft | null {
  const pointerKey = graphPointerKey(scope), pointer = pointers.getItem(pointerKey);
  if (observed.pointerObserved && pointer !== observed.pointer) error("Another runtime changed the earlier Graph pointer. The original source and current input were retained.");
  observed.pointer = pointer; observed.pointerObserved = true;
  let draft: GraphDraft | null = null;
  if (pointer !== null) {
    if (!graphUuid(pointer)) error("The Graph recovery pointer is unreadable; its bytes were retained.");
    const raw = storage.getItem(graphCopyKey(scope, pointer));
    if (observed.rawObserved && raw !== observed.raw) error("The earlier Graph recovery bytes changed. Retry cannot adopt a replacement source.");
    observed.raw = raw; observed.rawObserved = true;
    if (raw === null) return error("The earlier Graph recovery copy is missing.");
    draft = decodeGraphCopy(raw, scope, pointer).draft;
  }
  assertGraphRecoverySource(storage, pointers, scope, observed);
  return draft;
}
/** Recheck the original source before transferring custody to a fresh writer. */
export function assertGraphRecoverySource(storage: Pick<GraphStorage, "getItem">, pointers: Pick<Storage, "getItem">,
  scope: string, observed: GraphRecoveryObservation): void {
  if (!observed.pointerObserved || pointers.getItem(graphPointerKey(scope)) !== observed.pointer)
    error("The Graph recovery pointer changed before adoption. No original source was discarded.");
  if (observed.pointer !== null && (!observed.rawObserved || observed.raw === null
      || storage.getItem(graphCopyKey(scope, observed.pointer)) !== observed.raw))
    error("The original Graph recovery bytes are not confirmed. No original source was discarded.");
  if (pointers.getItem(graphPointerKey(scope)) !== observed.pointer)
    error("The Graph recovery pointer changed while its source was checked.");
}
/** Later explicit fields, including empty/ABA, overlay the recovered source by stable identity. */
export function recoverGraphRead(current: GraphDraft, recovered: GraphDraft, editedPaths: readonly string[]): GraphDraft {
  if (current.scope !== recovered.scope || current.sceneId !== recovered.sceneId || current.graphId !== recovered.graphId
      || current.input.content.kind !== recovered.input.content.kind) error("Graph recovery cannot migrate an owner or semantic kind.");
  const attempt = (pending: GraphPending | null) => pending && { ...pending, outcome: "UNKNOWN", diagnostic: null };
  if (current.pending && !graphSame(attempt(current.pending), attempt(recovered.pending)))
    error("Another unconfirmed Graph operation is already retained.");
  const paths = [...new Set(editedPaths)];
  if (!paths.length) return { ...clone(recovered), pending: recovered.pending ? { ...clone(recovered.pending), outcome: "UNKNOWN" } : null };
  const sequence = Math.max(current.sequence, recovered.sequence) + 1;
  if (!integer(sequence)) error("Graph edit sequence exhausted; preserve this input before continuing.");
  const overlay = (target: unknown, source: unknown, parts: string[]): unknown => {
    if (!parts.length) return clone(source);
    const [head, ...rest] = parts;
    if (idArray(target) && idArray(source)) {
      const own = source.find(item => item.id === head), prior = target.find(item => item.id === head);
      // A provisional child has its own stable ID. Never match another child by label or position.
      const next = own === undefined ? undefined : prior === undefined ? clone(own) : overlay(prior, own, rest);
      return prior ? target.flatMap(item => item.id !== head ? [clone(item)] : next === undefined ? [] : [next])
        : next === undefined ? clone(target) : [...clone(target), next];
    }
    if (!graphRecord(source) || !Object.hasOwn(source, head)) return error("The explicit Graph recovery field is no longer available.");
    if (!graphRecord(target) || ("kind" in target && "kind" in source && target.kind !== source.kind)) return clone(source);
    return { ...clone(target), [head]: overlay(target[head], source[head], rest) };
  };
  let input: unknown = clone(recovered.input);
  const edited = { ...recovered.edited };
  for (const path of paths) {
    if (!path.startsWith("/") || path.split("/").slice(1).some(part => !part) || !Object.hasOwn(current.edited, path))
      error("The Graph recovery edit marker is invalid.");
    edited[path] = sequence;
  }
  // A final explicit parent contains its later child intent, including a return
  // to another variant. Do not read obsolete descendants of that final value.
  const effectivePaths = paths.filter(path => !paths.some(parent => path !== parent && path.startsWith(parent + "/")));
  for (const path of effectivePaths) {
    const parts = path.slice(1).split("/");
    // Manual is an explicit deletion of this one optional field. It must survive
    // a failed earlier recovery read without deleting the frozen submission.
    const manualEdge = parts.length === 4 && parts[0] === "content" && parts[1] === "edges" && parts[3] === "label_placement"
      ? current.input.content.edges.find(edge => edge.id === parts[2]) : undefined;
    const retainedEdge = manualEdge && (input as GraphRaw).content.edges.some(edge => edge.id === manualEdge.id);
    if (manualEdge && retainedEdge && !Object.hasOwn(manualEdge, "label_placement")) input = setPath(input, parts, undefined);
    else input = overlay(input, current.input, parts);
  }
  validateGraphRaw(input);
  return { ...clone(recovered), input, sequence, edited,
    pending: recovered.pending ? { ...clone(recovered.pending), outcome: "UNKNOWN" } : null };
}
export function encodeGraphCopy(copy: GraphCopy): string { const raw = JSON.stringify(copy); decodeGraphCopy(raw, copy.scope, copy.writerId, false); return raw; }
export function graphDownload(draft: GraphDraft): string { return JSON.stringify({ schema: "motionwright.semantic-graph-author-file/1", exportedAt: Date.now(), draft: cleanGraphDraft(draft, draft.scope, false) }, null, 2); }
export function writeGraphCopy(storage: GraphStorage, pointers: Pick<Storage, "getItem" | "setItem">, draft: GraphDraft, custody: GraphCustody): GraphCustody {
  const key = graphCopyKey(draft.scope, custody.writerId), pointerKey = graphPointerKey(draft.scope);
  const check = () => { const pointer = pointers.getItem(pointerKey); if (pointer !== custody.expectedPointer && pointer !== custody.writerId) error("Another runtime changed the Graph pointer. Earlier copies were retained.");
    if (storage.getItem(key) !== custody.expectedRaw) error("Another runtime changed the Graph copy. Earlier bytes were retained."); };
  check(); let raw = encodeGraphCopy({ schema: "motionwright.semantic-graph-recovery/1", scope: draft.scope, writerId: custody.writerId, updatedAt: Date.now(), draft });
  // Confirming identical input must preserve its original bytes, including a
  // live caller's NOT_SENT. Recovery normalization belongs to recovery reads.
  if (custody.expectedRaw !== null && graphSame(decodeGraphCopy(custody.expectedRaw, draft.scope, custody.writerId, false).draft, draft)) raw = custody.expectedRaw;
  let total = size(key) + size(raw), count = 1;
  for (let index = 0; index < storage.length; index++) { const other = storage.key(index); if (!other?.startsWith(GRAPH_RECOVERY_PREFIX) || other === key) continue; total += size(other) + size(storage.getItem(other) ?? ""); count++; }
  if (total > GRAPH_TOTAL_BYTES || count > GRAPH_COPY_COUNT) error("Graph recovery is full (64 copies / 4 MiB). Review and discard only obsolete copies.");
  check(); if (raw !== custody.expectedRaw) storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) error("Graph recovery data write was not confirmed."); custody.expectedRaw = raw;
  pointers.setItem(pointerKey, custody.writerId); if (pointers.getItem(pointerKey) !== custody.writerId) error("Graph recovery pointer write was not confirmed.");
  if (storage.getItem(key) !== raw) error("Graph recovery bytes changed during pointer publication."); custody.expectedPointer = custody.writerId; return custody;
}
export function assertGraphCustody(storage: Pick<Storage, "getItem">, pointers: Pick<Storage, "getItem">, draft: GraphDraft, custody: GraphCustody) {
  if (!custody.expectedRaw || pointers.getItem(graphPointerKey(draft.scope)) !== custody.writerId || storage.getItem(graphCopyKey(draft.scope, custody.writerId)) !== custody.expectedRaw
    || !graphSame(decodeGraphCopy(custody.expectedRaw, draft.scope, custody.writerId, false).draft, draft)) error("The exact Graph input and pointer are not in confirmed recovery custody.");
}
/** Copy-on-fork retains the earlier UNKNOWN or author snapshot even if publication fails. */
export function forkGraphCopy(storage: GraphStorage, pointers: Pick<Storage, "getItem" | "setItem">, before: GraphDraft, next: GraphDraft, original: GraphCustody, writerId: string): GraphCustody {
  if (before.scope !== next.scope || !graphUuid(writerId) || writerId === original.writerId) error("Invalid Graph custody fork.");
  assertGraphCustody(storage, pointers, before, original); const oldKey = graphCopyKey(before.scope, original.writerId), oldRaw = original.expectedRaw;
  const custody = { writerId, expectedRaw: null as string | null, expectedPointer: original.expectedPointer };
  writeGraphCopy(storage, pointers, next, custody);
  if (storage.getItem(oldKey) !== oldRaw) error("The original Graph copy changed during custody transfer.");
  return custody;
}
export function readGraphCopies(storage: GraphStorage, scope: string, observe?: (writerId: string, raw: string) => void): { entries: GraphRecoveryEntry[]; limited: boolean } {
  const prefix = GRAPH_RECOVERY_PREFIX + encodeURIComponent(scope) + ":", entries: GraphRecoveryEntry[] = []; let limited = false;
  for (let i = 0; i < storage.length; i++) { const key = storage.key(i); if (!key?.startsWith(prefix)) continue; if (entries.length >= GRAPH_COPY_COUNT) { limited = true; continue; }
    const raw = storage.getItem(key); if (raw === null) continue; const writerId = key.slice(prefix.length); observe?.(writerId, raw); let value: GraphCopy | null = null;
    try { value = decodeGraphCopy(raw, scope, writerId); } catch { /* Retain corrupt bytes for explicit export/discard. */ } entries.push({ key, writerId, raw, value }); }
  return { entries, limited };
}
export function discardGraphCopy(storage: GraphStorage, pointers: Pick<Storage, "getItem">, scope: string, copy: GraphRecoveryEntry, activeWriter: string): void {
  if (copy.writerId === activeWriter || pointers.getItem(graphPointerKey(scope)) === copy.writerId) error("The active Graph recovery copy cannot be discarded.");
  if (copy.key !== graphCopyKey(scope, copy.writerId) || storage.getItem(copy.key) !== copy.raw) error("The selected Graph copy changed; inspect it before discarding.");
  if (pointers.getItem(graphPointerKey(scope)) === copy.writerId) error("The Graph pointer changed before discard. The copy was retained.");
  storage.removeItem(copy.key); if (storage.getItem(copy.key) !== null) error("Graph discard was not confirmed; no second removal was attempted.");
  if (pointers.getItem(graphPointerKey(scope)) === copy.writerId) error("The Graph pointer changed during discard. The active editor must verify its recovery again.");
}
