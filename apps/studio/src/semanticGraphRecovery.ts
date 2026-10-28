/** Read-only recovery of one retained Graph operation; never write authority. */
import type { Project } from "./types";
import { graphExact, graphSame, graphUuid, semanticGraphs, validateSemanticGraphShape,
  type GraphSourceSnapshot, type SemanticGraphChange } from "./semanticGraph";

export const GRAPH_RECOVERY_READ_ERROR = "The original Graph operation could not be verified. Its outcome remains unconfirmed; keep the original request.";
const SCHEMA = "motionwright.semantic-graph-operation-recovery/1" as const;
export type GraphRecoveryRecord = { state: "recorded"; historical_project: Project }
  | { state: "pending" } | { state: "outcome_unknown" }
  | { state: "retention_expired"; current_epoch: number };
export interface GraphOperationRecovery {
  schema: typeof SCHEMA; request_id: string; source_snapshot: GraphSourceSnapshot; record: GraphRecoveryRecord;
}
const invalid = (): never => { throw new Error(GRAPH_RECOVERY_READ_ERROR); };
const uuid = (value: unknown): value is string => typeof value === "string" && value.length === 36
  && value !== "00000000-0000-0000-0000-000000000000" && graphUuid(value);
const natural = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

/** Copy JSON data without accessors/toJSON. This is not a Project domain decoder. */
function copyData(value: unknown, ancestors = new Set<object>()): unknown {
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : invalid();
  if (typeof value !== "object" || ancestors.has(value)) return invalid();
  ancestors.add(value);
  try {
    const prototype = Object.getPrototypeOf(value);
    if (Array.isArray(value)) {
      if (prototype !== Array.prototype) return invalid();
      const descriptor = Object.getOwnPropertyDescriptor(value, "length");
      if (!descriptor || !("value" in descriptor) || !natural(descriptor.value)) return invalid();
      const length: number = descriptor.value, keys = Reflect.ownKeys(value);
      if (keys.length !== length + 1 || keys.some(key => typeof key !== "string")) return invalid();
      return Array.from({ length }, (_, index) => {
        const item = Object.getOwnPropertyDescriptor(value, String(index));
        if (!item || !("value" in item) || !item.enumerable) return invalid();
        return copyData(item.value, ancestors);
      });
    }
    if (prototype !== Object.prototype && prototype !== null) return invalid();
    const result: Record<string, unknown> = Object.create(null);
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== "string") return invalid();
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor || !("value" in descriptor) || !descriptor.enumerable) return invalid();
      result[key] = copyData(descriptor.value, ancestors);
    }
    return result;
  } finally { ancestors.delete(value); }
}
function snapshot(value: unknown): GraphSourceSnapshot {
  graphExact(value, ["project_id", "generation", "branch_id", "revision"], "Graph recovery source");
  if (!uuid(value.project_id) || !uuid(value.generation) || !uuid(value.branch_id) || !natural(value.revision)) return invalid();
  return value as unknown as GraphSourceSnapshot;
}
const sourceOf = (change: SemanticGraphChange): GraphSourceSnapshot => change.type === "upsert_semantic_graph"
  ? change.graph.authorship.source_snapshot : change.source_snapshot;

/** Capture before awaiting; never trim, replace or rebase the retained request. */
export function captureGraphRecoveryRequest(requestId: string, change: SemanticGraphChange): { request_id: string; change: SemanticGraphChange } {
  try {
    if (!uuid(requestId)) return invalid();
    const captured = copyData(change); if (!object(captured)) return invalid();
    if (captured.type === "upsert_semantic_graph") {
      graphExact(captured, ["type", "graph"], "Graph recovery change"); validateSemanticGraphShape(captured.graph);
      if (!uuid(captured.graph.id) || !uuid(captured.graph.scene_id) || !uuid(captured.graph.variant_profile_ref)) return invalid();
      snapshot(captured.graph.authorship.source_snapshot);
    } else if (captured.type === "remove_semantic_graph") {
      graphExact(captured, ["type", "graph_id", "scene_id", "source_snapshot"], "Graph recovery change");
      if (!uuid(captured.graph_id) || !uuid(captured.scene_id)) return invalid();
      snapshot(captured.source_snapshot);
    } else return invalid();
    return { request_id: requestId, change: captured as unknown as SemanticGraphChange };
  } catch { return invalid(); }
}

/** The backend validates Project. Here bind its original Graph result and closed DTO. */
export function decodeGraphOperationRecovery(value: unknown, requestId: string, change: SemanticGraphChange): GraphOperationRecovery {
  try {
    const original = captureGraphRecoveryRequest(requestId, change), expected = sourceOf(original.change), wire = copyData(value);
    graphExact(wire, ["schema", "request_id", "source_snapshot", "record"], "Graph operation recovery");
    if (wire.schema !== SCHEMA || !uuid(wire.request_id) || wire.request_id !== original.request_id) return invalid();
    const source = snapshot(wire.source_snapshot); if (!graphSame(source, expected)) return invalid();
    const record = wire.record; if (!object(record)) return invalid();
    let decoded: GraphRecoveryRecord;
    switch (record.state) {
      case "pending":
      case "outcome_unknown":
        graphExact(record, ["state"], "Graph recovery record"); decoded = { state: record.state }; break;
      case "retention_expired":
        graphExact(record, ["state", "current_epoch"], "Graph recovery record");
        if (!natural(record.current_epoch)) return invalid();
        // The SDK's original epoch is not a Project revision and is not available
        // here. Expiration never establishes resolution or permission to replay.
        decoded = { state: "retention_expired", current_epoch: record.current_epoch }; break;
      case "recorded": {
        graphExact(record, ["state", "historical_project"], "Graph recovery record");
        const data = record.historical_project;
        if (!object(data) || data.id !== expected.project_id || data.generation !== expected.generation
          || data.active_branch !== expected.branch_id || !natural(data.revision) || expected.revision === Number.MAX_SAFE_INTEGER
          || data.revision !== expected.revision + 1 || !object(data.production_design)) return invalid();
        const design = data.production_design;
        if (Object.hasOwn(design, "semantic_graphs") && !Array.isArray(design.semantic_graphs)) return invalid();
        const project = data as unknown as Project, graphs = semanticGraphs(project);
        if (!graphs.every(graph => object(graph) && uuid(graph.id))) return invalid();
        if (original.change.type === "upsert_semantic_graph") {
          const submitted = original.change.graph, matches = graphs.filter(graph => graph.id === submitted.id);
          if (matches.length !== 1 || !graphSame(matches[0], submitted)) return invalid();
        } else {
          const graphId = original.change.graph_id; if (graphs.some(graph => graph.id === graphId)) return invalid();
        }
        decoded = { state: "recorded", historical_project: project }; break;
      }
      default: return invalid();
    }
    return { schema: SCHEMA, request_id: original.request_id, source_snapshot: source, record: decoded };
  } catch { return invalid(); }
}
