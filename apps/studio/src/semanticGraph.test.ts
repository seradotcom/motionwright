import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { graphAddTime, graphAdmissionIssue, graphAuthoredNumber, graphEdgeLabelBox, graphEdgePoints, graphExactFrame, graphFraction, graphFrameTime,
  graphFromRaw, graphReviewAnchor, graphSourceSnapshot, graphToRaw, isGraphReviewResource, newSemanticGraph, semanticGraphs, validateSemanticGraphForWrite, validateSemanticGraphShape } from "./semanticGraph";
import type { GraphAuthoringContext, GraphProject, SemanticGraph } from "./semanticGraph";

function source(kind: "causal_diagram" | "system_flow" = "causal_diagram") {
  const project = structuredClone(fixtureBootstrap.project) as GraphProject; project.locks = [];
  project.production_design = emptyProductionDesign(); const scene = project.scenes[0], profile = project.deliverables[0];
  scene.duration = { num: "5", den: "1" }; profile.width = 1920; profile.height = 1080; profile.frame_rate = { num: "30", den: "1" }; profile.included_scene_ids = [];
  const graph = newSemanticGraph(project, scene, profile, kind); graph.authorship.author_label = "Test author"; graph.authorship.rationale = "Explicit synthetic fixture for validator tests.";
  const context: GraphAuthoringContext = { schema: "motionwright.semantic-graph-authoring-context/1", source_snapshot: graphSourceSnapshot(project), scene_id: scene.id,
    variant_profile_ref: profile.id, component_version: 1, native_profile: "mw-manim-semantic-graph-v1", font_weight: 400,
    font_selection: { primary_family: "Instrument Sans Variable", mono_family: "IBM Plex Mono" }, availability: "ready", blocking_reasons: [], dependencies: [] };
  return { project, scene, profile, graph, context };
}
describe("closed semantic Graph editorial contract", () => {
  it.each(["causal_diagram", "system_flow"] as const)("retains the complete %s source through raw editing and exact wire", async kind => {
    const { project, graph, context, profile } = source(kind); const before = structuredClone(project);
    validateSemanticGraphShape(graph, profile.frame_rate); await validateSemanticGraphForWrite(project, graph, context);
    expect(graphFromRaw(graphToRaw(graph))).toEqual(graph); expect(project).toEqual(before); expect(semanticGraphs(project)).toEqual([]);
  });
  it.each([
    ["future version", (g: SemanticGraph) => { (g as { component_version: number }).component_version = 2; }],
    ["top-level executable", (g: SemanticGraph) => { Object.assign(g, { python: "import os" }); }],
    ["nested executable", (g: SemanticGraph) => { Object.assign(g.content.vertices[0], { html: "<b>label</b>" }); }],
    ["font fallback", (g: SemanticGraph) => { g.style.font_family = "Instrument Sans" as SemanticGraph["style"]["font_family"]; }],
    ["duplicate vertex", (g: SemanticGraph) => { g.content.vertices[1].id = g.content.vertices[0].id; }],
    ["dangling endpoint", (g: SemanticGraph) => { g.content.edges[0].to_vertex_id = crypto.randomUUID(); }],
    ["self loop", (g: SemanticGraph) => { g.content.edges[0].to_vertex_id = g.content.edges[0].from_vertex_id; }],
    ["circle geometry", (g: SemanticGraph) => { g.content.vertices[0].shape = "circle"; }],
    ["frame overflow", (g: SemanticGraph) => { g.content.vertices[0].box.x = g.reference_frame.width; }],
    ["nonfinite coordinate", (g: SemanticGraph) => { g.content.vertices[0].box.x = Infinity; }],
    ["early edge", (g: SemanticGraph) => { g.content.edges[0].appearance.at = { num: "0", den: "1" }; }],
    ["unsafe revision", (g: SemanticGraph) => { g.authorship.source_snapshot.revision = Number.MAX_SAFE_INTEGER + 1; }],
    ["unsafe brand version", (g: SemanticGraph) => { g.brand_profile_ref = { id: crypto.randomUUID(), version: Number.MAX_SAFE_INTEGER + 1, content_sha256: "a".repeat(64) }; }],
    ["oversized UTF-8 label", (g: SemanticGraph) => { g.content.vertices[0].label = "字".repeat(86); }],
    ["control character", (g: SemanticGraph) => { g.content.edges[0].label = "bad\u0000text"; }],
  ] as const)("rejects %s before any source mutation", (_name, mutate) => {
    const { graph } = source(); mutate(graph); const before = structuredClone(graph);
    expect(() => validateSemanticGraphShape(graph, { num: "30", den: "1" })).toThrow(); expect(graph).toEqual(before);
  });
  it("keeps code-looking strings literal rather than interpreting or censoring text", () => {
    const { graph } = source(); graph.content.vertices[0].label = "Python: __import__('os')"; graph.content.edges[0].label = "<script>alert(1)</script>";
    expect(() => validateSemanticGraphShape(graph)).not.toThrow(); expect(graphFromRaw(graphToRaw(graph))).toEqual(graph);
  });
  it("uses actual endpoint ports and authored waypoints without auto-layout", () => {
    const { graph } = source(); const edge = graph.content.edges[0]; edge.waypoints = [{ x: 800, y: 300 }, { x: 1100, y: 300 }];
    graph.content.vertices[0].box = { x: 100, y: 100, width: 200, height: 100 }; graph.content.vertices[1].box = { x: 1200, y: 100, width: 200, height: 100 };
    expect(graphEdgePoints(graph, edge)).toEqual([{ x: 300, y: 150 }, ...edge.waypoints, { x: 1200, y: 150 }]);
  });
  it("keeps causal and synthetic flow semantics distinct", () => {
    const { graph } = source("system_flow"); if (graph.content.kind !== "system_flow") throw new Error("Fixture kind");
    Object.assign(graph.content.source, { kind: "recorded_execution", receipt: "invented" }); expect(() => validateSemanticGraphShape(graph)).toThrow();
    const causal = source().graph; Object.assign(causal.content.edges[0], { channel: "data" }); expect(() => validateSemanticGraphShape(causal)).toThrow();
  });
  it("checks event identity, edge, visibility and overlap", () => {
    const { graph, profile } = source("system_flow"); if (graph.content.kind !== "system_flow") throw new Error("Fixture kind");
    const event = { id: crypto.randomUUID(), edge_id: graph.content.edges[0].id, label: "Synthetic event", at: graphFrameTime(2, profile.frame_rate), duration: graphFrameTime(3, profile.frame_rate) };
    graph.content.events.push(event); validateSemanticGraphShape(graph, profile.frame_rate);
    graph.content.events.push({ ...event, id: crypto.randomUUID() }); expect(() => validateSemanticGraphShape(graph, profile.frame_rate)).toThrow(/Overlapping/);
    graph.content.events.pop(); graph.content.events[0].edge_id = crypto.randomUUID(); expect(() => validateSemanticGraphShape(graph)).toThrow(/missing/);
    graph.content.events[0].edge_id = graph.content.edges[0].id; graph.content.events[0].at = graphFrameTime(1, profile.frame_rate); expect(() => validateSemanticGraphShape(graph)).toThrow(/fully visible/);
  });
  it.each([
    { num: "0", den: "2" }, { num: "2", den: "4" }, { num: "+1", den: "1" }, { num: "01", den: "1" }, { num: "-0", den: "1" },
    { num: "1", den: "0" }, { num: "1", den: "-1" }, { num: "9223372036854775808", den: "1" }, { num: "1", den: "9223372036854775808" },
  ])("rejects noncanonical or out-of-range rational $num/$den", value => { expect(() => graphFraction(value)).toThrow(); });
  it("uses exact frame arithmetic for integer and fractional frame rates", () => {
    const rate = { num: "30000", den: "1001" }; expect(graphFrameTime(1, rate)).toEqual({ num: "1001", den: "30000" });
    expect(graphExactFrame({ num: "1001", den: "30000" }, rate)).toBe(1n);
    expect(() => graphExactFrame({ num: "1", den: "100" }, { num: "30", den: "1" })).toThrow(/exact frame/);
    expect(graphAddTime({ num: "1", den: "3" }, { num: "1", den: "6" })).toEqual({ num: "1", den: "2" });
    expect(() => graphAddTime({ num: "9223372036854775807", den: "1" }, { num: "1", den: "1" })).toThrow();
  });
  it.each(["", "1e", "-", ".", " 1", "1 ", "Infinity", "NaN"])("preserves raw %s and refuses numeric coercion", raw => {
    const { graph } = source(); const input = graphToRaw(graph); input.content.vertices[0].box.x = raw;
    expect(() => graphFromRaw(input)).toThrow(); expect(input.content.vertices[0].box.x).toBe(raw); expect(() => graphAuthoredNumber(raw, "X")).toThrow();
  });
  it.each(["full", "off"] as const)("keeps reveal ordering under %s", motion => {
    const { graph } = source(); graph.motion = motion; graph.content.edges[0].appearance.at = { num: "0", den: "1" }; expect(() => validateSemanticGraphShape(graph)).toThrow(/fully visible/);
  });
});

/** Exact 3-4-5 route: endpoint ports, authored turns and midpoint are distinct. */
function labelPlacementSource(kind: "causal_diagram" | "system_flow" = "causal_diagram") {
  const result = source(kind), { graph } = result;
  graph.content.vertices[0].box = { x: 100, y: 100, width: 100, height: 100 };
  graph.content.vertices[1].box = { x: 700, y: 350, width: 100, height: 100 };
  const edge = graph.content.edges[0];
  edge.waypoints = [{ x: 500, y: 550 }, { x: 700, y: 550 }];
  edge.label_box = { x: 20, y: 650, width: 100, height: 40 };
  return { ...result, edge };
}

describe("explicit Graph edge-label placement", () => {
  it("keeps both legacy edge kinds manual and leaves the optional wire member absent", () => {
    for (const kind of ["causal_diagram", "system_flow"] as const) {
      const { graph, edge } = labelPlacementSource(kind), before = structuredClone(graph);
      const raw = JSON.stringify(graph);
      expect(Object.hasOwn(edge, "label_placement")).toBe(false);
      expect(graphEdgeLabelBox(graph, edge)).toEqual(edge.label_box);
      graph.content.vertices[0].box.x += 300;
      expect(graphEdgeLabelBox(graph, edge)).toEqual(before.content.edges[0].label_box);
      graph.content.vertices[0].box.x -= 300;
      expect(JSON.stringify(graphFromRaw(graphToRaw(graph)))).toBe(raw);
      expect(graph).toEqual(before);
    }
  });
  it("uses half the total Euclidean route length with signed offset for causal and flow labels", () => {
    for (const kind of ["causal_diagram", "system_flow"] as const) {
      const { graph, edge } = labelPlacementSource(kind);
      edge.label_placement = { kind: "polyline_midpoint", offset: { x: -5, y: 10 } };
      const before = structuredClone(graph);
      // Segments have lengths 500 + 200 + 150. Half, 425, is 85% of the first.
      expect(graphEdgePoints(graph, edge)).toEqual([
        { x: 200, y: 150 }, { x: 500, y: 550 }, { x: 700, y: 550 }, { x: 700, y: 400 },
      ]);
      expect(graphEdgeLabelBox(graph, edge)).toEqual({ x: 400, y: 480, width: 100, height: 40 });
      validateSemanticGraphShape(graph);
      expect(graphFromRaw(graphToRaw(graph))).toEqual(graph);
      expect(graph).toEqual(before);
    }
  });
  it("follows changed endpoint ports with fixed waypoints and restores the untouched manual box", () => {
    const { graph, edge } = labelPlacementSource();
    edge.label_placement = { kind: "polyline_midpoint", offset: { x: -5, y: 10 } };
    const manual = structuredClone(edge.label_box), waypoints = JSON.stringify(edge.waypoints);
    graph.content.vertices[0].box.x = 400; // New source port (500,150): lengths 400+200+150.
    expect(graphEdgeLabelBox(graph, edge)).toEqual({ x: 445, y: 515, width: 100, height: 40 });
    graph.content.vertices[1].box.y = 450; // New destination (700,500): lengths 400+200+50.
    expect(graphEdgeLabelBox(graph, edge)).toEqual({ x: 445, y: 465, width: 100, height: 40 });
    expect(edge.label_box).toEqual(manual);
    expect(JSON.stringify(edge.waypoints)).toBe(waypoints);
    delete edge.label_placement;
    expect(graphEdgeLabelBox(graph, edge)).toEqual(manual);
    expect(Object.hasOwn(edge, "label_placement")).toBe(false);
  });
  it("takes current box size without baking the resolved position into manual geometry", () => {
    const { graph, edge } = labelPlacementSource();
    edge.label_placement = { kind: "polyline_midpoint", offset: { x: -5, y: 10 } };
    edge.label_box.width = 200; edge.label_box.height = 80;
    const manual = structuredClone(edge.label_box);
    expect(graphEdgeLabelBox(graph, edge)).toEqual({ x: 350, y: 460, width: 200, height: 80 });
    expect(edge.label_box).toEqual(manual);
  });
  it("defines repeated and all-zero routes without expanding legacy edge admission", () => {
    const { graph, edge } = labelPlacementSource();
    edge.label_placement = { kind: "polyline_midpoint", offset: { x: -5, y: 10 } };
    edge.waypoints.unshift({ x: 200, y: 150 });
    edge.waypoints.push({ x: 700, y: 400 });
    expect(graphEdgeLabelBox(graph, edge)).toEqual({ x: 400, y: 480, width: 100, height: 40 });
    expect(() => validateSemanticGraphShape(graph)).toThrow(/degenerate/);
    graph.content.vertices[1].box = { x: 200, y: 100, width: 100, height: 100 };
    edge.waypoints = [{ x: 200, y: 150 }, { x: 200, y: 150 }];
    edge.label_placement.offset = { x: 0, y: 0 };
    expect(graphEdgeLabelBox(graph, edge)).toEqual({ x: 150, y: 130, width: 100, height: 40 });
    expect(() => validateSemanticGraphShape(graph)).toThrow(/degenerate/);
  });
  it("rejects unsupported placement structure and nonnumeric vectors without changing source", () => {
    const invalid = [
      null,
      { kind: "vertex", offset: { x: 0, y: 0 } },
      { kind: "polyline_midpoint", offset: { x: 0, y: 0 }, code: "ignored()" },
      { kind: "polyline_midpoint", offset: { x: 0, y: 0, z: 0 } },
      { kind: "polyline_midpoint", offset: { x: 0 } },
      { kind: "polyline_midpoint", offset: { x: "0", y: 0 } },
      { kind: "polyline_midpoint", offset: { x: Infinity, y: 0 } },
    ];
    for (const placement of invalid) {
      const { graph, edge } = labelPlacementSource();
      Object.assign(edge, { label_placement: placement });
      const before = structuredClone(graph);
      expect(() => validateSemanticGraphShape(graph)).toThrow();
      expect(graph).toEqual(before);
    }
  });
  it("rejects a resolved box outside the frame instead of clamping or altering the stored box", () => {
    for (const offset of [{ x: -1000, y: 0 }, { x: 1920, y: 0 }, { x: 0, y: -1080 }, { x: 0, y: Number.MAX_VALUE }]) {
      const { graph, edge } = labelPlacementSource();
      edge.label_placement = { kind: "polyline_midpoint", offset };
      const before = structuredClone(graph);
      expect(() => validateSemanticGraphShape(graph)).toThrow(/outside/);
      expect(() => graphEdgeLabelBox(graph, edge)).toThrow(/outside/);
      expect(graph).toEqual(before);
    }
  });
});
describe("Graph current-source write preflight is not authority", () => {
  it("resolves graph, vertex and edge review anchors from exact saved identity and absolute time", () => {
    const { project, graph, scene } = source(); scene.start = { num: "7", den: "1" }; graph.time_range.start = { num: "1", den: "1" }; graph.time_range.duration = { num: "4", den: "1" };
    project.production_design!.semantic_graphs = [graph];
    for (const resource of ["graph:" + graph.id, "graph-vertex:" + graph.id + ":" + graph.content.vertices[0].id, "graph-edge:" + graph.id + ":" + graph.content.edges[0].id]) {
      expect(isGraphReviewResource(resource)).toBe(true); expect(graphReviewAnchor(project, resource)).toMatchObject({ resource, revision: project.revision, branch_id: project.active_branch, start: { num: "8", den: "1" }, end: { num: "12", den: "1" }, profile_id: graph.variant_profile_ref });
    }
    expect(graphReviewAnchor(project, "graph-vertex:" + graph.id + ":" + crypto.randomUUID())).toBeNull();
    expect(graphReviewAnchor(project, "graph:" + graph.id + ":extra")).toBeNull();
    graph.content.vertices.shift(); expect(graphReviewAnchor(project, "graph-vertex:" + graph.id + ":" + crypto.randomUUID())).toBeNull();
  });
  it("blocks missing, stale, wrong-profile and unavailable service context", async () => {
    const { project, graph, context } = source(); expect(graphAdmissionIssue(project, graph.scene_id, graph.variant_profile_ref, null)).toMatch(/awaits/);
    for (const invalid of [{ ...context, source_snapshot: { ...context.source_snapshot, revision: project.revision + 1 } },
      { ...context, native_profile: "mw-manim-planar-camera-v1" }, { ...context, availability: "blocked", blocking_reasons: ["unsupported_renderer_guard_missing"] },
      { ...context, font_selection: { primary_family: "Instrument Sans", mono_family: "IBM Plex Mono" } },
      { ...context, font_selection: { primary_family: "Instrument Sans Variable", mono_family: "IBM Plex Mono", path: "arbitrary.ttf" } }]) {
      await expect(validateSemanticGraphForWrite(project, graph, invalid as GraphAuthoringContext)).rejects.toThrow();
    }
  });
  it.each(["project_id", "generation", "branch_id", "revision"] as const)("rejects foreign source snapshot %s", async field => {
    const { project, graph, context } = source(); if (field === "revision") graph.authorship.source_snapshot.revision++; else graph.authorship.source_snapshot[field] = crypto.randomUUID();
    await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/snapshot/);
  });
  it("keeps stored historical authorship readable but requires the new exact stamp to write", async () => {
    const { project, graph, context } = source(); graph.authorship.source_snapshot.generation = crypto.randomUUID();
    validateSemanticGraphShape(graph); await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/snapshot/);
  });
  it("refuses silent rescale or retime after source changes", async () => {
    const { project, graph, context, scene, profile } = source(); profile.width = 1080; await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/rescale/);
    profile.width = 1920; scene.duration = { num: "1", den: "1" }; await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/trim/);
  });
  it("preserves third-party collections while rejecting an identity migration", async () => {
    const { project, graph, context } = source(); project.production_design!.semantic_graphs = [structuredClone(graph)];
    project.production_design!.semantic_graphs[0].content = source("system_flow").graph.content;
    const before = structuredClone(project); await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/identity/); expect(project).toEqual(before);
  });
  it("requires current dependency descriptors rather than a client-invented hash", async () => {
    const { project, graph, context } = source(); const ref = { kind: "canvas_node" as const, scene_id: graph.scene_id, node_id: project.scenes[0].nodes[0].id, content_sha256: "a".repeat(64) };
    graph.content.vertices[0].target = ref; await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/fingerprint/);
    context.dependencies.push(ref); await validateSemanticGraphForWrite(project, graph, context);
    context.dependencies[0] = { ...ref, content_sha256: "b".repeat(64) }; await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/stale/);
  });
  it("applies project, scene and graph locks before writing", async () => {
    const { project, graph, context } = source();
    for (const resource of ["project:" + project.id, "scene:" + graph.scene_id, "graph:" + graph.id, "graph-vertex:" + graph.id + ":" + graph.content.vertices[0].id]) {
      project.locks = [{ id: crypto.randomUUID(), resource, kind: "content", note: "Test protection" }]; await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/lock/);
    }
  });
  it("retains the lock of a removed saved vertex during whole-Graph preflight", async () => {
    const { project, graph, context } = source(), removed = graph.content.vertices[1];
    project.production_design!.semantic_graphs = [structuredClone(graph)];
    project.locks = [{ id: crypto.randomUUID(), resource: "graph-vertex:" + graph.id + ":" + removed.id, kind: "content", note: "Keep this saved concept" }];
    graph.content.edges = []; graph.content.vertices = graph.content.vertices.slice(0, 1);
    await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/lock/);
  });
  it("checks the measured unit of every vertex that shares the same datum", async () => {
    const { project, graph, context } = source(), tableId = crypto.randomUUID();
    const binding = { kind: "measured_datum" as const, table_id: tableId, table_version: 1, datum_key: "duration", source_sha256: "a".repeat(64) };
    project.production_design!.measured_tables = [{ schema: "motionwright.measured-table/1", id: tableId, label: "Unit preflight fixture", version: 1,
      source_asset_id: crypto.randomUUID(), source_sha256: binding.source_sha256, rows: [{ key: "duration", label: "Duration", milli: 2000, unit: "seconds" }] }];
    context.dependencies.push(binding); graph.content.vertices.forEach(vertex => { vertex.target = binding; vertex.unit = { kind: "symbol", symbol: "seconds" }; });
    await validateSemanticGraphForWrite(project, graph, context); graph.content.vertices[1].unit = { kind: "symbol", symbol: "bytes" };
    await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/unit/);
  });
  it("null brand binding does not exempt Graph labels and disclosures from current policy", async () => {
    const { project, graph, context } = source(); project.production_design!.brand_profile = { id: crypto.randomUUID(), label: "Test brand", version: 1, rules: [{ id: "no-claim", kind: "forbidden_phrase", phrase: "guaranteed" }] };
    if (graph.content.kind !== "causal_diagram" || graph.content.edges[0].basis.kind !== "author_hypothesis") throw new Error("Fixture kind");
    graph.content.edges[0].basis.disclosure = "Guaranteed effect"; await expect(validateSemanticGraphForWrite(project, graph, context)).rejects.toThrow(/BrandProfile/);
  });
});
