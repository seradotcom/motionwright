import { describe, expect, it, vi } from "vitest";
import { fixtureBootstrap } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { graphSourceSnapshot, newSemanticGraph, type GraphProject, type SemanticGraphChange } from "./semanticGraph";
import { captureGraphRecoveryRequest, decodeGraphOperationRecovery, GRAPH_RECOVERY_READ_ERROR,
  type GraphOperationRecovery, type GraphRecoveryRecord } from "./semanticGraphRecovery";

const KEY = "ABCDEF12-3456-4789-ABCD-EF1234567890";
function fixture(remove = false) {
  const project = structuredClone(fixtureBootstrap.project) as GraphProject;
  project.production_design = emptyProductionDesign();
  const graph = newSemanticGraph(project, project.scenes[0], project.deliverables[0], "causal_diagram");
  graph.authorship.author_label = "Recovery test author"; graph.authorship.rationale = "Synthetic read-only receipt fixture";
  const change: SemanticGraphChange = remove
    ? { type: "remove_semantic_graph", graph_id: graph.id, scene_id: graph.scene_id, source_snapshot: graphSourceSnapshot(project) }
    : { type: "upsert_semantic_graph", graph };
  const historical = structuredClone(project); historical.revision++;
  historical.production_design!.semantic_graphs = remove ? [] : [structuredClone(graph)];
  return { project, graph, change, historical };
}
function wire(change: SemanticGraphChange, record: GraphRecoveryRecord = { state: "pending" }): GraphOperationRecovery {
  return { schema: "motionwright.semantic-graph-operation-recovery/1", request_id: KEY,
    source_snapshot: structuredClone(change.type === "upsert_semantic_graph" ? change.graph.authorship.source_snapshot : change.source_snapshot),
    record: structuredClone(record) };
}
function rejected(value: unknown, change: SemanticGraphChange, key = KEY) {
  let failure: unknown;
  try { decodeGraphOperationRecovery(value, key, change); } catch (reason) { failure = reason; }
  expect(failure).toBeInstanceOf(Error); expect((failure as Error).message).toBe(GRAPH_RECOVERY_READ_ERROR);
  expect(failure).not.toHaveProperty("outcome_known"); expect(failure).not.toHaveProperty("cause");
}

describe("closed original Graph recovery records", () => {
  it.each([false, true])("accepts all four exact states for the retained change (remove=%s)", remove => {
    const { change, historical } = fixture(remove);
    for (const record of [{ state: "pending" }, { state: "outcome_unknown" },
      { state: "retention_expired", current_epoch: 1 }, { state: "recorded", historical_project: historical }] as GraphRecoveryRecord[]) {
      const value = wire(change, record), result = decodeGraphOperationRecovery(value, KEY, change);
      expect(result).toEqual(value); expect(result).not.toBe(value); expect(result.record).not.toBe(value.record);
    }
  });
  it("requires the exact raw nonnil key without whitespace or case normalization", () => {
    const { change } = fixture(), value = wire(change);
    for (const key of ["", KEY.toLowerCase(), " " + KEY, KEY + "\n", "00000000-0000-0000-0000-000000000000"]) rejected(value, change, key);
    for (const key of [" " + KEY, KEY + "\n", KEY.toLowerCase(), "00000000-0000-0000-0000-000000000000"])
      rejected({ ...value, request_id: key }, change);
  });
  it("requires every original source field and rejects additional identity/authority fields", () => {
    const { change } = fixture(), value = wire(change);
    for (const field of ["project_id", "generation", "branch_id", "revision"] as const) {
      const altered = structuredClone(value); (altered.source_snapshot as unknown as Record<string, unknown>)[field] = field === "revision" ? value.source_snapshot.revision + 1 : crypto.randomUUID();
      rejected(altered, change);
      const missing = structuredClone(value); delete (missing.source_snapshot as Partial<typeof missing.source_snapshot>)[field]; rejected(missing, change);
    }
    for (const field of ["schema", "request_id", "source_snapshot", "record"]) {
      const missing = structuredClone(value) as unknown as Record<string, unknown>; delete missing[field]; rejected(missing, change);
    }
    rejected({ ...value, schema: "motionwright.semantic-graph-operation-recovery/2" }, change);
    rejected({ ...value, expected_store: "private" }, change);
    rejected({ ...value, source_snapshot: { ...value.source_snapshot, epoch: 7 } }, change);
  });
  it("rejects open record variants and malformed expiration without treating it as resolution", () => {
    const { change } = fixture();
    for (const record of [
      { state: "recorded" }, { state: "pending", historical_project: {} },
      { state: "outcome_unknown", outcome_known: true }, { state: "unknown" },
      { kind: "pending" }, { state: "retention_expired" },
      ...[-1, 1.5, Number.MAX_SAFE_INTEGER + 1, NaN, Infinity, "1", null].map(current_epoch => ({ state: "retention_expired", current_epoch })),
      { state: "retention_expired", current_epoch: 7, replay_allowed: true },
    ]) rejected({ ...wire(change), record }, change);
    expect(decodeGraphOperationRecovery(wire(change, { state: "retention_expired", current_epoch: Number.MAX_SAFE_INTEGER }), KEY, change).record.state).toBe("retention_expired");
  });
  it("requires original scope, exact r+1, one exact submitted Graph and its original authorship", () => {
    const { change, historical } = fixture();
    for (const field of ["id", "generation", "active_branch"] as const) {
      const altered = structuredClone(historical); altered[field] = crypto.randomUUID();
      rejected(wire(change, { state: "recorded", historical_project: altered }), change);
    }
    for (const revision of [historical.revision - 1, historical.revision + 1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
      rejected(wire(change, { state: "recorded", historical_project: { ...historical, revision } }), change);
    }
    for (const kind of ["absent", "duplicate", "label", "authorship"] as const) {
      const altered = structuredClone(historical), graphs = altered.production_design!.semantic_graphs!;
      if (kind === "absent") graphs.length = 0;
      if (kind === "duplicate") graphs.push(structuredClone(graphs[0]));
      if (kind === "label") graphs[0].content.vertices[0].label = "Private replacement";
      if (kind === "authorship") graphs[0].authorship.source_snapshot.revision++;
      rejected(wire(change, { state: "recorded", historical_project: altered }), change);
    }
  });
  it("accepts omitted empty Graph serialization for Remove but rejects a missing design or surviving target", () => {
    const { change, graph, historical } = fixture(true);
    delete historical.production_design!.semantic_graphs;
    expect(decodeGraphOperationRecovery(wire(change, { state: "recorded", historical_project: historical }), KEY, change).record.state).toBe("recorded");
    for (const design of [undefined, null, { semantic_graphs: null }, { semantic_graphs: {} }, { semantic_graphs: [graph] }]) {
      const value = wire(change, { state: "recorded", historical_project: historical });
      (value.record as { historical_project: unknown }).historical_project = { ...historical, production_design: design };
      rejected(value, change);
    }
  });
  it("captures exact request data without accepting another command, caller fields or a rebased source", () => {
    const { change } = fixture(), captured = captureGraphRecoveryRequest(KEY, change), original = structuredClone(change);
    if (change.type === "upsert_semantic_graph") { change.graph.id = crypto.randomUUID(); change.graph.authorship.source_snapshot.revision++; }
    expect(captured).toEqual({ request_id: KEY, change: original });
    for (const malformed of [{ type: "rename_project", title: "No write" }, { ...original, grant: "private" },
      { type: "remove_semantic_graph", graph_id: "00000000-0000-0000-0000-000000000000", scene_id: fixture().graph.scene_id, source_snapshot: wire(original).source_snapshot }]) {
      expect(() => captureGraphRecoveryRequest(KEY, malformed as SemanticGraphChange)).toThrow(GRAPH_RECOVERY_READ_ERROR);
    }
  });
  it("rejects non-JSON data without evaluating getters or toJSON and sanitizes all diagnostics", () => {
    const { change, historical } = fixture(), getter = vi.fn(() => { throw new Error("PRIVATE_RECOVERY_SENTINEL"); });
    const top = wire(change); Object.defineProperty(top, "record", { enumerable: true, get: getter });
    const nested = wire(change, { state: "recorded", historical_project: historical });
    Object.defineProperty((nested.record as { historical_project: GraphProject }).historical_project.production_design!, "semantic_graphs", { enumerable: true, get: getter });
    const symbol = wire(change); Object.defineProperty(symbol, Symbol("private"), { value: 1 });
    const hidden = wire(change); Object.defineProperty(hidden, "private", { value: 1 });
    const circular = wire(change) as unknown as Record<string, unknown>; circular.loop = circular;
    const sparse = wire(change, { state: "recorded", historical_project: historical });
    (sparse.record as { historical_project: GraphProject }).historical_project.production_design!.semantic_graphs = new Array(1);
    const custom = Object.assign(Object.create({ private: true }), wire(change));
    const toJSON = { ...wire(change), toJSON: getter };
    for (const value of [top, nested, symbol, hidden, circular, sparse, custom, toJSON, JSON.stringify(wire(change))]) rejected(value, change);
    expect(getter).not.toHaveBeenCalled();
  });
});
