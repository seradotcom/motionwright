/**
 * Typed semantic source and editorial validation. StudioService owns all writes.
 * The domain validates source authority; the renderer owns native projection.
 * These author declarations never grant execution, certify evidence, or admit a component.
 */
import type { DeliverableProfile, Project, RationalTime, ReviewAnchor, Scene } from "./types";
import { brandDigest } from "./brandGovernance";

export const SEMANTIC_GRAPH_COMPONENT_VERSION = 1 as const;

/** Historical authorship context. Exact current matching is required on mutation only. */
export interface GraphSourceSnapshot {
  project_id: string;
  generation: string;
  branch_id: string;
  revision: number;
}

export interface GraphAuthorship {
  author_label: string;
  rationale: string;
  source_snapshot: GraphSourceSnapshot;
}

/** Relative to the owning scene; the interval is [start, start + duration). */
export interface GraphTimeRange {
  start: RationalTime;
  duration: RationalTime;
}

/** Exact logical pixels in the explicitly authored delivery profile. */
export interface GraphPoint { x: number; y: number; }
export interface GraphBox extends GraphPoint { width: number; height: number; }
export interface GraphReferenceFrame { width: number; height: number; }
/** Signed displacement from the route midpoint, not an absolute frame point. */
export interface GraphLabelOffset { x: number; y: number; }
export interface GraphLabelPlacement { kind: "polyline_midpoint"; offset: GraphLabelOffset; }

export type GraphSubjectRef =
  | { kind: "scene"; scene_id: string }
  | { kind: "claim"; claim_id: string; content_sha256: string }
  | { kind: "asset"; asset_id: string; content_sha256: string };

/** A semantic association, not implicit position following or a second editable source. */
export type GraphTargetBinding =
  | { kind: "canvas_node"; scene_id: string; node_id: string; content_sha256: string }
  | { kind: "claim"; claim_id: string; content_sha256: string }
  | {
      kind: "measured_datum";
      table_id: string;
      table_version: number;
      datum_key: string;
      source_sha256: string;
    };

export interface GraphBrandRef {
  id: string;
  version: number;
  content_sha256: string;
}

/** Qualitative is explicit. Unit symbols are labels, never executable expressions. */
export type GraphUnit =
  | { kind: "qualitative" }
  | { kind: "symbol"; symbol: string };

/**
 * Times are relative to graph.time_range.start. Full motion uses linear opacity;
 * motion off uses a step at the same `at`, preserving temporal meaning.
 * The authored duration is retained when motion is off so toggling is lossless.
 */
export interface GraphAppearance {
  at: RationalTime;
  duration: RationalTime;
}

export type GraphMotionPreference = "full" | "off";
export type GraphVertexShape = "rectangle" | "circle";
export type GraphPort = "top" | "right" | "bottom" | "left";

export interface GraphVertex {
  id: string;
  label: string;
  meaning: string;
  unit: GraphUnit;
  box: GraphBox;
  shape: GraphVertexShape;
  target: GraphTargetBinding | null;
  appearance: GraphAppearance;
}

/** Authored polyline; an empty list means one straight segment between ports. */
export interface GraphEdgeGeometry {
  from_vertex_id: string;
  to_vertex_id: string;
  from_port: GraphPort;
  to_port: GraphPort;
  waypoints: GraphPoint[];
  label_box: GraphBox;
  /** Absent retains the manual legacy box. Only an explicit edit opts into following. */
  label_placement?: GraphLabelPlacement;
  appearance: GraphAppearance;
}

/** A reference to a claim records its author declaration, not scientific verification. */
export type GraphCausalBasis =
  | { kind: "author_hypothesis"; disclosure: string }
  | { kind: "claim_reference"; claim_id: string; content_sha256: string };

export interface GraphCausalEdge extends GraphEdgeGeometry {
  id: string;
  label: string;
  influence: "positive" | "negative" | "unspecified";
  basis: GraphCausalBasis;
}

export interface GraphFlowEdge extends GraphEdgeGeometry {
  id: string;
  label: string;
  channel: "data" | "control" | "event";
}

/**
 * This source format admits only explicitly synthetic flow events.
 * A later recorded-execution variant needs verified receipt and asset bindings,
 * not an arbitrary URL, a user-written `observed: true`, or the author's name.
 */
export interface GraphSyntheticFlowSource {
  kind: "synthetic";
  disclosure: string;
}

export interface GraphFlowEvent {
  id: string;
  edge_id: string;
  label: string;
  /** Relative to graph.time_range.start, not to the scene or wall clock. */
  at: RationalTime;
  duration: RationalTime;
}

export type GraphContent =
  | { kind: "causal_diagram"; vertices: GraphVertex[]; edges: GraphCausalEdge[] }
  | {
      kind: "system_flow";
      source: GraphSyntheticFlowSource;
      vertices: GraphVertex[];
      edges: GraphFlowEdge[];
      events: GraphFlowEvent[];
    };

/** Plain Unicode text only; there is no TeX, HTML, SVG, Python or expression field. */
export interface GraphStyle {
  background: string;
  vertex_fill: string;
  text_color: string;
  edge_color: string;
  accent_color: string;
  font_family: "Instrument Sans Variable" | "IBM Plex Mono";
  font_size: number;
  edge_width: number;
  disclosure_box: GraphBox;
}

export interface SemanticGraph {
  id: string;
  scene_id: string;
  component_version: typeof SEMANTIC_GRAPH_COMPONENT_VERSION;
  subject_ref: GraphSubjectRef;
  time_range: GraphTimeRange;
  /** null means explicitly unbranded; existing project-wide rules still apply. */
  brand_profile_ref: GraphBrandRef | null;
  variant_profile_ref: string;
  reference_frame: GraphReferenceFrame;
  motion: GraphMotionPreference;
  style: GraphStyle;
  content: GraphContent;
  authorship: GraphAuthorship;
}

/** Proposed members of the existing Change union, not a second transport. */
export type SemanticGraphChange =
  | { type: "upsert_semantic_graph"; graph: SemanticGraph }
  | {
      type: "remove_semantic_graph";
      graph_id: string;
      scene_id: string;
      source_snapshot: GraphSourceSnapshot;
    };

export const SEMANTIC_GRAPH_PROFILE = "mw-manim-semantic-graph-v1" as const;
export const SEMANTIC_GRAPH_FONT_WEIGHT = 400 as const;
export const GRAPH_LIMITS = { graphs: 16, vertices: 32, edges: 64, events: 64, waypoints: 8 } as const;
export type GraphProject = Project & { production_design?: NonNullable<Project["production_design"]> & { semantic_graphs?: SemanticGraph[] } };
/** Service read model; a caller must never construct this from browser feature flags.
 * These checks are an authoring gate, never execution grants or evidence of pixels.
 * The service must match the same exact source snapshot before mutation. */
export const GRAPH_BLOCK_REASONS = ["graph_write_unavailable", "native_graph_profile_unavailable", "unsupported_renderer_guard_missing",
  "font_source_unavailable", "dependency_descriptor_unavailable", "source_stale", "profile_unsupported"] as const;
export type GraphBlockReason = typeof GRAPH_BLOCK_REASONS[number];
export interface GraphAuthoringContext {
  schema: "motionwright.semantic-graph-authoring-context/1";
  source_snapshot: GraphSourceSnapshot;
  scene_id: string;
  variant_profile_ref: string;
  component_version: 1;
  native_profile: typeof SEMANTIC_GRAPH_PROFILE;
  font_weight: typeof SEMANTIC_GRAPH_FONT_WEIGHT;
  font_selection: { primary_family: string; mono_family: string };
  availability: "ready" | "blocked";
  blocking_reasons: GraphBlockReason[];
  dependencies: Array<GraphSubjectRef | GraphTargetBinding | { kind: "brand"; id: string; version: number; content_sha256: string }>;
}
export type GraphRawValue<T> = T extends number ? string : T extends Array<infer U> ? GraphRawValue<U>[]
  : T extends object ? { [K in keyof T]: GraphRawValue<T[K]> } : T;
export type GraphRaw = Omit<GraphRawValue<SemanticGraph>, "component_version" | "authorship"> & {
  component_version: 1; authorship: GraphAuthorship;
};
export const graphSourceSnapshot = (project: Project): GraphSourceSnapshot => ({ project_id: project.id,
  generation: project.generation, branch_id: project.active_branch, revision: project.revision });
export const semanticGraphs = (project: Project): SemanticGraph[] => (project as GraphProject).production_design?.semantic_graphs ?? [];
export function isGraphReviewResource(resource: string): boolean {
  const [kind, graphId, childId, ...extra] = resource.split(":");
  return !extra.length && graphUuid(graphId) && (kind === "graph" ? childId === undefined : ["graph-vertex", "graph-edge"].includes(kind) && graphUuid(childId));
}
/** Resolve the existing review anchor against the selected saved source only. */
export function graphReviewAnchor(project: Project, resource: string): ReviewAnchor | null {
  if (!isGraphReviewResource(resource) || !Number.isSafeInteger(project.revision) || project.revision < 0) return null;
  const [kind, graphId, childId] = resource.split(":"), graph = semanticGraphs(project).find(item => item.id === graphId);
  if (!graph || (kind === "graph-vertex" && !graph.content.vertices.some(v => v.id === childId)) || (kind === "graph-edge" && !graph.content.edges.some(e => e.id === childId))) return null;
  const scene = project.scenes.find(item => item.id === graph.scene_id), profile = project.deliverables.find(item => item.id === graph.variant_profile_ref);
  if (!scene || !profile) return null;
  return { resource, branch_id: project.active_branch, revision: project.revision, start: graphAddTime(scene.start, graph.time_range.start),
    end: graphAddTime(scene.start, graphAddTime(graph.time_range.start, graph.time_range.duration)), locale: profile.language, profile_id: profile.id };
}
export const graphSame = (a: unknown, b: unknown): boolean => {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((item, i) => graphSame(item, b[i]));
  if (graphRecord(a) && graphRecord(b)) return Object.keys(a).length === Object.keys(b).length
    && Object.keys(a).every(key => Object.hasOwn(b, key) && graphSame(a[key], b[key]));
  return false;
};
export const graphRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
export function graphExact(value: unknown, keys: string[], label: string): asserts value is Record<string, unknown> {
  if (!graphRecord(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key)))
    throw new Error(label + " has missing or unsupported fields.");
}
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const graphUuid = (value: unknown): value is string => typeof value === "string" && uuidPattern.test(value);
const shaPattern = /^[0-9a-f]{64}$/;
const fail = (message: string): never => { throw new Error(message); };
function identifier(value: unknown, label: string) { if (!graphUuid(value)) fail(label + " must be a UUID."); }
function digest(value: unknown, label: string) { if (typeof value !== "string" || !shaPattern.test(value)) fail(label + " must be a lowercase SHA-256 digest from the domain descriptor."); }
function text(value: unknown, limit: number, label: string) {
  if (typeof value !== "string" || !value.trim() || new TextEncoder().encode(value).length > limit || /[\u0000-\u0009\u000b-\u001f\u007f]/u.test(value))
    fail(label + " requires literal text within " + limit + " UTF-8 bytes, without control characters.");
}
function member(value: unknown, values: readonly string[], label: string) { if (typeof value !== "string" || !values.includes(value)) fail("Unsupported " + label + "."); }
function integer(value: unknown, label: string, min = 0, max = Number.MAX_SAFE_INTEGER) {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max) fail(label + " must be an exact safe integer.");
}
function finite(value: unknown, label: string, positive = false) { if (typeof value !== "number" || !Number.isFinite(value) || (positive && value <= 0)) fail(label + " must be finite" + (positive ? " and positive." : ".")); }
export interface GraphFraction { num: bigint; den: bigint }
const I64_MAX = 9223372036854775807n, I64_MIN = -9223372036854775808n;
const gcd = (a: bigint, b: bigint): bigint => { a = a < 0n ? -a : a; while (b) [a, b] = [b, a % b]; return a; };
export function graphFraction(value: unknown, label = "Graph time"): GraphFraction {
  graphExact(value, ["num", "den"], label);
  if (typeof value.num !== "string" || typeof value.den !== "string" || !/^(0|-[1-9][0-9]*|[1-9][0-9]*)$/.test(value.num)
    || !/^[1-9][0-9]*$/.test(value.den) || value.num.length > 20 || value.den.length > 19) fail(label + " requires canonical integer numerator and positive denominator.");
  const num = BigInt(value.num as string), den = BigInt(value.den as string);
  if (num < I64_MIN || num > I64_MAX || den > I64_MAX || gcd(num, den) !== 1n) fail(label + " is noncanonical or outside i64.");
  return { num, den };
}
export const graphCompareTime = (a: RationalTime, b: RationalTime): number => {
  const left = graphFraction(a), right = graphFraction(b), delta = left.num * right.den - right.num * left.den;
  return delta < 0n ? -1 : delta > 0n ? 1 : 0;
};
export function graphAddTime(a: RationalTime, b: RationalTime): RationalTime {
  const x = graphFraction(a), y = graphFraction(b), raw = x.num * y.den + y.num * x.den, den = x.den * y.den, divisor = gcd(raw, den);
  const result = { num: (raw / divisor).toString(), den: (den / divisor).toString() }; graphFraction(result); return result;
}
export function graphFrameTime(frames: number, rate: RationalTime): RationalTime {
  integer(frames, "Frame count"); const r = graphFraction(rate, "Frame rate"); if (r.num <= 0n) fail("Frame rate must be positive.");
  const n = BigInt(frames) * r.den, d = r.num, divisor = gcd(n, d);
  const result = { num: (n / divisor).toString(), den: (d / divisor).toString() }; graphFraction(result); return result;
}
export function graphExactFrame(time: RationalTime, rate: RationalTime, label = "Graph time"): bigint {
  const t = graphFraction(time, label), r = graphFraction(rate, "Frame rate");
  if (r.num <= 0n || (t.num * r.num) % (t.den * r.den) !== 0n) fail(label + " must lie on an exact frame; no rounding is applied.");
  return (t.num * r.num) / (t.den * r.den);
}
function snapshot(value: unknown) {
  graphExact(value, ["project_id", "generation", "branch_id", "revision"], "Graph source snapshot");
  identifier(value.project_id, "Project"); identifier(value.generation, "Generation"); identifier(value.branch_id, "Branch"); integer(value.revision, "Source revision");
}
function point(value: unknown, label: string, frame: GraphReferenceFrame) {
  graphExact(value, ["x", "y"], label); finite(value.x, label + " X"); finite(value.y, label + " Y");
  if ((value.x as number) < 0 || (value.y as number) < 0 || (value.x as number) > frame.width || (value.y as number) > frame.height) fail(label + " is outside the authored frame.");
}
function box(value: unknown, label: string, frame: GraphReferenceFrame) {
  graphExact(value, ["x", "y", "width", "height"], label);
  point({ x: value.x, y: value.y }, label, frame); finite(value.width, label + " width", true); finite(value.height, label + " height", true);
  if ((value.x as number) + (value.width as number) > frame.width || (value.y as number) + (value.height as number) > frame.height) fail(label + " extends outside the authored frame.");
}
function target(value: unknown, subject: boolean) {
  if (!graphRecord(value)) fail("A graph reference must be typed.");
  const v = value as Record<string, unknown>;
  if (v.kind === "scene" && subject) { graphExact(v, ["kind", "scene_id"], "Scene subject"); identifier(v.scene_id, "Subject scene"); }
  else if (v.kind === "asset" && subject) { graphExact(v, ["kind", "asset_id", "content_sha256"], "Asset subject"); identifier(v.asset_id, "Subject asset"); digest(v.content_sha256, "Asset digest"); }
  else if (v.kind === "claim") { graphExact(v, ["kind", "claim_id", "content_sha256"], "Claim reference"); identifier(v.claim_id, "Claim"); digest(v.content_sha256, "Claim descriptor digest"); }
  else if (v.kind === "canvas_node" && !subject) { graphExact(v, ["kind", "scene_id", "node_id", "content_sha256"], "Canvas reference"); identifier(v.scene_id, "Target scene"); identifier(v.node_id, "Target node"); digest(v.content_sha256, "Canvas descriptor digest"); }
  else if (v.kind === "measured_datum" && !subject) { graphExact(v, ["kind", "table_id", "table_version", "datum_key", "source_sha256"], "Measured reference"); identifier(v.table_id, "Measured table"); integer(v.table_version, "Measured table version", 1, 4294967295); text(v.datum_key, 64, "Datum key"); digest(v.source_sha256, "Measured source digest"); }
  else fail("Unsupported graph reference kind.");
}
function interval(at: unknown, duration: unknown, limit: RationalTime, rate: RationalTime | undefined, label: string) {
  const a = graphFraction(at, label + " start"), d = graphFraction(duration, label + " duration");
  if (a.num < 0n || d.num <= 0n || graphCompareTime(graphAddTime(at as RationalTime, duration as RationalTime), limit) > 0) fail(label + " must fit its containing interval.");
  if (rate) { graphExactFrame(at as RationalTime, rate, label + " start"); graphExactFrame(duration as RationalTime, rate, label + " duration"); }
}
function appearance(value: unknown, limit: RationalTime, rate: RationalTime | undefined, label: string) {
  graphExact(value, ["at", "duration"], label); interval(value.at, value.duration, limit, rate, label);
}
export function graphPortPoint(vertex: GraphVertex, port: GraphPort): GraphPoint {
  const { x, y, width: w, height: h } = vertex.box;
  return { top: { x: x + w / 2, y }, right: { x: x + w, y: y + h / 2 }, bottom: { x: x + w / 2, y: y + h }, left: { x, y: y + h / 2 } }[port];
}
export function graphEdgePoints(graph: SemanticGraph, edge: GraphEdgeGeometry): GraphPoint[] {
  const from = graph.content.vertices.find(item => item.id === edge.from_vertex_id), to = graph.content.vertices.find(item => item.id === edge.to_vertex_id);
  if (!from || !to) fail("An edge endpoint is missing.");
  return [graphPortPoint(from!, edge.from_port), ...edge.waypoints, graphPortPoint(to!, edge.to_port)];
}
/** Resolve presentation geometry without mutating the authored manual box or route.
 * Repeated/all-zero routes are defined here; the profile's edge admission remains separate.
 */
export function graphEdgeLabelBox(graph: SemanticGraph, edge: GraphEdgeGeometry): GraphBox {
  box(edge.label_box, "Edge label box", graph.reference_frame);
  if (!Object.hasOwn(edge, "label_placement")) return { ...edge.label_box };
  const placement = edge.label_placement;
  graphExact(placement, ["kind", "offset"], "Edge label placement");
  member(placement.kind, ["polyline_midpoint"], "edge label placement");
  graphExact(placement.offset, ["x", "y"], "Edge label offset");
  finite(placement.offset.x, "Edge label offset X"); finite(placement.offset.y, "Edge label offset Y");
  const points = graphEdgePoints(graph, edge);
  points.forEach(value => point(value, "Edge route point", graph.reference_frame));
  const lengths = points.slice(1).map((value, index) => Math.hypot(value.x - points[index].x, value.y - points[index].y));
  const total = lengths.reduce((sum, length) => sum + length, 0);
  finite(total, "Edge route length");
  let midpoint = points[0], remaining = total / 2;
  if (total > 0) for (let index = 0; index < lengths.length; index++) {
    const length = lengths[index];
    if (length === 0) continue;
    if (remaining <= length) {
      const from = points[index], to = points[index + 1], ratio = remaining / length;
      midpoint = { x: from.x + (to.x - from.x) * ratio, y: from.y + (to.y - from.y) * ratio };
      break;
    }
    remaining -= length;
  }
  const resolved = { x: midpoint.x + placement.offset.x - edge.label_box.width / 2,
    y: midpoint.y + placement.offset.y - edge.label_box.height / 2,
    width: edge.label_box.width, height: edge.label_box.height };
  box(resolved, "Resolved edge label box", graph.reference_frame);
  return resolved;
}
/** Closed shape and historical validity; current references and authoring admission are separate checks. */
export function validateSemanticGraphShape(candidate: unknown, rate?: RationalTime): asserts candidate is SemanticGraph {
  graphExact(candidate, ["id", "scene_id", "component_version", "subject_ref", "time_range", "brand_profile_ref", "variant_profile_ref", "reference_frame", "motion", "style", "content", "authorship"], "Semantic Graph");
  const g = candidate as unknown as SemanticGraph;
  identifier(g.id, "Graph"); identifier(g.scene_id, "Graph scene"); identifier(g.variant_profile_ref, "Deliverable profile");
  if (g.component_version !== 1) fail("Unsupported semantic Graph component version."); target(g.subject_ref, true); member(g.motion, ["full", "off"], "motion preference");
  graphExact(g.reference_frame, ["width", "height"], "Reference frame"); integer(g.reference_frame.width, "Frame width", 1); integer(g.reference_frame.height, "Frame height", 1);
  graphExact(g.time_range, ["start", "duration"], "Graph interval"); const start = graphFraction(g.time_range.start), duration = graphFraction(g.time_range.duration);
  if (start.num < 0n || duration.num <= 0n) fail("Graph interval requires nonnegative start and positive duration."); graphAddTime(g.time_range.start, g.time_range.duration);
  if (rate) { graphExactFrame(g.time_range.start, rate); graphExactFrame(g.time_range.duration, rate); }
  graphExact(g.authorship, ["author_label", "rationale", "source_snapshot"], "Graph authorship");
  text(g.authorship.author_label, 160, "Author label"); text(g.authorship.rationale, 2000, "Author rationale"); snapshot(g.authorship.source_snapshot);
  if (g.brand_profile_ref !== null) { graphExact(g.brand_profile_ref, ["id", "version", "content_sha256"], "Brand reference"); identifier(g.brand_profile_ref.id, "Brand"); integer(g.brand_profile_ref.version, "Brand version", 1); digest(g.brand_profile_ref.content_sha256, "Brand digest"); }
  graphExact(g.style, ["background", "vertex_fill", "text_color", "edge_color", "accent_color", "font_family", "font_size", "edge_width", "disclosure_box"], "Graph style");
  for (const key of ["background", "vertex_fill", "text_color", "edge_color", "accent_color"] as const) if (typeof g.style[key] !== "string" || !/^#[0-9a-f]{6}$/i.test(g.style[key])) fail("Graph " + key + " must be #RRGGBB.");
  member(g.style.font_family, ["Instrument Sans Variable", "IBM Plex Mono"], "Graph font family (fixed weight 400)");
  finite(g.style.font_size, "Graph font size", true); finite(g.style.edge_width, "Graph edge width", true); box(g.style.disclosure_box, "Disclosure box", g.reference_frame);
  if (!graphRecord(g.content)) fail("Graph content must be typed.");
  if (g.content.kind === "causal_diagram") graphExact(g.content, ["kind", "vertices", "edges"], "Causal content");
  else if (g.content.kind === "system_flow") { graphExact(g.content, ["kind", "source", "vertices", "edges", "events"], "Flow content"); graphExact(g.content.source, ["kind", "disclosure"], "Flow source"); if (g.content.source.kind !== "synthetic") fail("System flow v1 admits only a declared synthetic source."); text(g.content.source.disclosure, 2000, "Synthetic flow disclosure"); }
  else fail("Unsupported Graph kind.");
  if (!Array.isArray(g.content.vertices) || !g.content.vertices.length || g.content.vertices.length > GRAPH_LIMITS.vertices || !Array.isArray(g.content.edges) || g.content.edges.length > GRAPH_LIMITS.edges) fail("Graph vertex or edge budget exceeded.");
  const ids = new Set<string>();
  const ownId = (id: unknown) => { identifier(id, "Semantic identity"); if (ids.has(id as string)) fail("Duplicate semantic Graph identity."); ids.add(id as string); };
  for (const v of g.content.vertices) {
    graphExact(v, ["id", "label", "meaning", "unit", "box", "shape", "target", "appearance"], "Vertex"); ownId(v.id);
    text(v.label, 256, "Vertex label"); text(v.meaning, 2000, "Vertex meaning"); member(v.shape, ["rectangle", "circle"], "vertex shape"); box(v.box, "Vertex box", g.reference_frame);
    if (v.shape === "circle" && v.box.width !== v.box.height) fail("Circle vertices require equal width and height.");
    if (!graphRecord(v.unit)) fail("Vertex unit must be explicit.");
    if (v.unit.kind === "qualitative") graphExact(v.unit, ["kind"], "Qualitative unit");
    else if (v.unit.kind === "symbol") { graphExact(v.unit, ["kind", "symbol"], "Unit symbol"); text(v.unit.symbol, 64, "Unit symbol"); } else fail("Unsupported vertex unit.");
    if (v.target !== null) target(v.target, false); appearance(v.appearance, g.time_range.duration, rate, "Vertex appearance");
  }
  for (const e of g.content.edges) {
    const keys = ["id", "label", "from_vertex_id", "to_vertex_id", "from_port", "to_port", "waypoints", "label_box", "appearance"];
    graphExact(e, [...keys, ...(graphRecord(e) && Object.hasOwn(e, "label_placement") ? ["label_placement"] : []),
      ...(g.content.kind === "causal_diagram" ? ["influence", "basis"] : ["channel"])], "Edge"); ownId(e.id); text(e.label, 256, "Edge label");
    const from = g.content.vertices.find(v => v.id === e.from_vertex_id), to = g.content.vertices.find(v => v.id === e.to_vertex_id);
    if (!from || !to || from.id === to.id) fail("An edge needs two distinct existing endpoints; self loops are not admitted by this profile.");
    member(e.from_port, ["top", "right", "bottom", "left"], "edge source port"); member(e.to_port, ["top", "right", "bottom", "left"], "edge target port");
    if (!Array.isArray(e.waypoints) || e.waypoints.length > GRAPH_LIMITS.waypoints) fail("Graph waypoint budget exceeded.");
    e.waypoints.forEach(p => point(p, "Edge waypoint", g.reference_frame)); box(e.label_box, "Edge label box", g.reference_frame);
    const points = graphEdgePoints(g, e); for (let i = 1; i < points.length; i++) if (graphSame(points[i], points[i - 1])) fail("Edge geometry has a degenerate segment.");
    graphEdgeLabelBox(g, e);
    appearance(e.appearance, g.time_range.duration, rate, "Edge appearance");
    if (graphCompareTime(e.appearance.at, graphAddTime(from!.appearance.at, from!.appearance.duration)) < 0 || graphCompareTime(e.appearance.at, graphAddTime(to!.appearance.at, to!.appearance.duration)) < 0) fail("Edges begin only after both vertices are fully visible, including motion off.");
    if (g.content.kind === "causal_diagram") {
      const causal = e as GraphCausalEdge; member(causal.influence, ["positive", "negative", "unspecified"], "causal influence");
      if (!graphRecord(causal.basis)) fail("Causal basis must be explicit.");
      if (causal.basis.kind === "author_hypothesis") { graphExact(causal.basis, ["kind", "disclosure"], "Causal hypothesis"); text(causal.basis.disclosure, 2000, "Causal disclosure"); }
      else if (causal.basis.kind === "claim_reference") { graphExact(causal.basis, ["kind", "claim_id", "content_sha256"], "Causal claim"); identifier(causal.basis.claim_id, "Causal claim"); digest(causal.basis.content_sha256, "Causal claim digest"); } else fail("Unsupported causal basis.");
    } else member((e as GraphFlowEdge).channel, ["data", "control", "event"], "flow channel");
  }
  if (g.content.kind === "system_flow") {
    if (!Array.isArray(g.content.events) || g.content.events.length > GRAPH_LIMITS.events) fail("Flow event budget exceeded.");
    for (const event of g.content.events) {
      graphExact(event, ["id", "edge_id", "label", "at", "duration"], "Flow event"); ownId(event.id); text(event.label, 256, "Event label");
      const edge = g.content.edges.find(item => item.id === event.edge_id); if (!edge) fail("Flow event edge is missing.");
      interval(event.at, event.duration, g.time_range.duration, rate, "Flow event");
      if (graphCompareTime(event.at, graphAddTime(edge!.appearance.at, edge!.appearance.duration)) < 0) fail("A flow event starts only after its edge is fully visible.");
      for (const other of g.content.events) if (other.id !== event.id && other.edge_id === event.edge_id
        && graphCompareTime(event.at, graphAddTime(other.at, other.duration)) < 0 && graphCompareTime(other.at, graphAddTime(event.at, event.duration)) < 0) fail("Overlapping events on the same edge are not admitted by this profile.");
    }
  }
}
export function graphAdmissionIssue(project: Project, sceneId: string, profileId: string, context: GraphAuthoringContext | null): string | null {
  if (!context) return "Graph saving awaits the current StudioService authoring context and matching native consumer.";
  try {
    graphExact(context, ["schema", "source_snapshot", "scene_id", "variant_profile_ref", "component_version", "native_profile", "font_weight", "font_selection", "availability", "blocking_reasons", "dependencies"], "Graph authoring context");
    snapshot(context.source_snapshot); identifier(context.scene_id, "Context scene"); identifier(context.variant_profile_ref, "Context profile");
    graphExact(context.font_selection, ["primary_family", "mono_family"], "Graph font selection");
    if (!Array.isArray(context.dependencies) || !Array.isArray(context.blocking_reasons) || context.blocking_reasons.some(reason => !GRAPH_BLOCK_REASONS.includes(reason))) fail("Graph context has unsupported dependency or blocking-reason data.");
    for (const ref of context.dependencies) {
      if (ref.kind === "brand") { graphExact(ref, ["kind", "id", "version", "content_sha256"], "Brand descriptor"); identifier(ref.id, "Brand descriptor"); integer(ref.version, "Brand descriptor version", 1); digest(ref.content_sha256, "Brand descriptor digest"); }
      else target(ref, ref.kind === "scene" || ref.kind === "asset" || ref.kind === "claim");
    }
  } catch (reason) { return reason instanceof Error ? reason.message : String(reason); }
  if (!graphSame(context.source_snapshot, graphSourceSnapshot(project)) || context.scene_id !== sceneId || context.variant_profile_ref !== profileId) return "Graph authoring context is stale or belongs to another project, branch, scene or profile. Read it again.";
  if (context.schema !== "motionwright.semantic-graph-authoring-context/1" || context.component_version !== 1 || context.native_profile !== SEMANTIC_GRAPH_PROFILE
    || context.font_weight !== 400 || context.availability !== "ready" || !Array.isArray(context.blocking_reasons) || context.blocking_reasons.length !== 0)
    return "Graph authoring is blocked by the service: " + (Array.isArray(context.blocking_reasons) ? context.blocking_reasons.join(", ") : "unsupported authoring context") + ".";
  if (context.font_selection.primary_family !== "Instrument Sans Variable" || context.font_selection.mono_family !== "IBM Plex Mono") return "This Graph profile requires its declared primary and mono fonts at fixed weight 400; no fallback is available.";
  return null;
}
export function graphLockIssue(project: Project, graph: SemanticGraph): string | null {
  const previous = semanticGraphs(project).find(item => item.id === graph.id);
  const resources = new Set(["project:" + project.id, "scene:" + graph.scene_id, "graph:" + graph.id,
    ...[...graph.content.vertices, ...(previous?.content.vertices ?? [])].map(v => "graph-vertex:" + graph.id + ":" + v.id),
    ...[...graph.content.edges, ...(previous?.content.edges ?? [])].map(e => "graph-edge:" + graph.id + ":" + e.id)]);
  const locked = project.locks.find(lock => resources.has(lock.resource) && ["content", "timing", "position", "style", "renderer"].includes(lock.kind));
  return locked ? "Semantic Graph is protected by the " + locked.kind + " lock on " + locked.resource + "." : null;
}
function graphReferences(graph: SemanticGraph): Array<GraphSubjectRef | GraphTargetBinding> {
  const refs: Array<GraphSubjectRef | GraphTargetBinding> = [graph.subject_ref];
  graph.content.vertices.forEach(v => { if (v.target) refs.push(v.target); });
  if (graph.content.kind === "causal_diagram") graph.content.edges.forEach(e => { if (e.basis.kind === "claim_reference") refs.push({ kind: "claim", claim_id: e.basis.claim_id, content_sha256: e.basis.content_sha256 }); });
  return refs;
}
export async function validateSemanticGraphForWrite(project: Project, graph: SemanticGraph, context: GraphAuthoringContext | null): Promise<void> {
  const scene = project.scenes.find(s => s.id === graph.scene_id), profile = project.deliverables.find(p => p.id === graph.variant_profile_ref);
  if (!scene || !profile) fail("Graph scene or Deliverable no longer exists; the draft was retained.");
  validateSemanticGraphShape(graph, profile!.frame_rate); const admission = graphAdmissionIssue(project, graph.scene_id, graph.variant_profile_ref, context); if (admission) fail(admission);
  snapshot(graphSourceSnapshot(project));
  if (!graphSame(graph.authorship.source_snapshot, graphSourceSnapshot(project))) fail("Graph authorship requires the exact current source snapshot.");
  if (graph.reference_frame.width !== profile!.width || graph.reference_frame.height !== profile!.height) fail("Deliverable dimensions changed. Review geometry explicitly; no rescale was applied.");
  if (profile!.included_scene_ids.length && !profile!.included_scene_ids.includes(scene!.id)) fail("Graph scene is excluded from the selected Deliverable.");
  if (graphCompareTime(graphAddTime(graph.time_range.start, graph.time_range.duration), scene!.duration) > 0) fail("Graph interval exceeds the current scene. Review timing explicitly; no trim was applied.");
  const lock = graphLockIssue(project, graph); if (lock) fail(lock);
  const all = semanticGraphs(project), previous = all.find(g => g.id === graph.id);
  if (previous && (previous.scene_id !== graph.scene_id || previous.content.kind !== graph.content.kind)) fail("Graph identity cannot change scene or semantic kind. Create a separate Graph.");
  if ((!previous && all.length >= GRAPH_LIMITS.graphs) || all.some(g => g.id !== graph.id && g.scene_id === graph.scene_id && g.variant_profile_ref === graph.variant_profile_ref)) fail("This scene/profile already has a Graph or the Project Graph budget is full.");
  for (const ref of graphReferences(graph)) {
    if (ref.kind === "scene") { if (!project.scenes.some(s => s.id === ref.scene_id)) fail("Graph subject scene no longer exists."); }
    else if (ref.kind === "asset") { if (!project.assets.some(a => a.id === ref.asset_id && a.content_sha256 === ref.content_sha256)) fail("Graph asset source changed."); }
    else if (!context!.dependencies.some(actual => graphSame(actual, ref))) fail("Graph dependency is stale or lacks a current domain fingerprint. Reinspect it explicitly.");
    if (ref.kind === "measured_datum") {
      const table = project.production_design?.measured_tables?.find(t => t.id === ref.table_id), row = table?.rows.find(r => r.key === ref.datum_key);
      const vertices = graph.content.vertices.filter(v => graphSame(v.target, ref));
      if (!table || !row || table.version !== ref.table_version || table.source_sha256 !== ref.source_sha256
        || !vertices.length || vertices.some(vertex => (vertex.unit.kind === "symbol" ? vertex.unit.symbol : "") !== row.unit)) fail("Measured Graph binding requires the exact table version, datum source and unit.");
    }
  }
  const brand = project.production_design?.brand_profile, brandHash = brand ? await brandDigest(brand) : null;
  if (graph.brand_profile_ref && (!brand || graph.brand_profile_ref.id !== brand.id || graph.brand_profile_ref.version !== brand.version || graph.brand_profile_ref.content_sha256 !== brandHash)) fail("Graph BrandProfile reference is stale.");
  const strings = graphTextContent(graph);
  if (brand) for (const rule of brand.rules) {
    const waived = project.production_design?.brand_exceptions?.some(e => e.scene_id === graph.scene_id && e.rule_id === rule.id && e.brand_sha256 === brandHash);
    if (waived) continue;
    if (rule.kind === "forbidden_phrase" && strings.some(s => s.toLocaleLowerCase().includes(rule.phrase.toLocaleLowerCase()))) fail("Graph text violates the active BrandProfile forbidden phrase rule.");
    if (rule.kind === "allowed_accents" && ![graph.style.accent_color, graph.style.edge_color, graph.style.vertex_fill].every(c => rule.colors.some(allowed => allowed.toLowerCase() === c.toLowerCase()))) fail("Graph colors violate the active BrandProfile palette.");
    if (rule.kind === "required_wordmark" && !graph.content.vertices.some(v => v.label.includes(rule.text))) fail("The required wordmark must be visible in an authored Graph vertex or an approved scene exception.");
  }
}
export function graphTextContent(graph: SemanticGraph): string[] {
  const result = graph.content.vertices.flatMap(v => [v.label, v.meaning, ...(v.unit.kind === "symbol" ? [v.unit.symbol] : [])]);
  result.push(...graph.content.edges.map(e => e.label));
  if (graph.content.kind === "system_flow") result.push(graph.content.source.disclosure, ...graph.content.events.map(e => e.label));
  else result.push(...graph.content.edges.flatMap(e => e.basis.kind === "author_hypothesis" ? [e.basis.disclosure] : []));
  return result;
}
/** Transform only numeric slots; rational strings and every other authored byte survive. */
export function graphToRaw(graph: SemanticGraph): GraphRaw {
  const walk = (value: unknown): unknown => typeof value === "number" ? String(value) : Array.isArray(value) ? value.map(walk)
    : graphRecord(value) ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, walk(item)])) : value;
  return { ...(walk(graph) as GraphRaw), component_version: 1, authorship: structuredClone(graph.authorship) };
}
export function graphAuthoredNumber(value: string, label: string): number {
  if (typeof value !== "string" || !/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) fail(label + " is incomplete; finish the number before saving.");
  const n = Number(value); if (!Number.isFinite(n)) fail(label + " must be finite."); return n;
}
export function graphFromRaw(raw: GraphRaw): SemanticGraph {
  const g = structuredClone(raw) as unknown as SemanticGraph;
  const number = (value: unknown, label: string) => graphAuthoredNumber(value as string, label);
  const b = (value: GraphBox, label: string) => { value.x = number(value.x, label + " X"); value.y = number(value.y, label + " Y"); value.width = number(value.width, label + " width"); value.height = number(value.height, label + " height"); };
  g.reference_frame.width = number(g.reference_frame.width, "Frame width"); g.reference_frame.height = number(g.reference_frame.height, "Frame height");
  g.style.font_size = number(g.style.font_size, "Font size"); g.style.edge_width = number(g.style.edge_width, "Edge width"); b(g.style.disclosure_box, "Disclosure");
  if (g.brand_profile_ref) g.brand_profile_ref.version = number(g.brand_profile_ref.version, "Brand version");
  g.content.vertices.forEach(v => { b(v.box, "Vertex"); if (v.target?.kind === "measured_datum") v.target.table_version = number(v.target.table_version, "Table version"); });
  g.content.edges.forEach(e => {
    b(e.label_box, "Edge label"); e.waypoints.forEach(p => { p.x = number(p.x, "Waypoint X"); p.y = number(p.y, "Waypoint Y"); });
    if (Object.hasOwn(e, "label_placement")) {
      graphExact(e.label_placement, ["kind", "offset"], "Edge label placement");
      member(e.label_placement.kind, ["polyline_midpoint"], "edge label placement");
      graphExact(e.label_placement.offset, ["x", "y"], "Edge label offset");
      e.label_placement.offset.x = number(e.label_placement.offset.x, "Edge label offset X");
      e.label_placement.offset.y = number(e.label_placement.offset.y, "Edge label offset Y");
    }
  });
  return g;
}
export function newSemanticGraph(project: Project, scene: Scene, profile: DeliverableProfile, kind: GraphContent["kind"], id: string = crypto.randomUUID()): SemanticGraph {
  const w = profile.width, h = profile.height, vertexWidth = w * .26, vertexHeight = h * .18, reveal = graphFrameTime(1, profile.frame_rate);
  const vertex = (label: string, x: number): GraphVertex => ({ id: crypto.randomUUID(), label, meaning: "Describe this concept in the explanation.", unit: { kind: "qualitative" },
    box: { x, y: h * .30, width: vertexWidth, height: vertexHeight }, shape: "rectangle", target: null, appearance: { at: { num: "0", den: "1" }, duration: reveal } });
  const vertices = [vertex(kind === "causal_diagram" ? "Cause" : "Input", w * .08), vertex(kind === "causal_diagram" ? "Outcome" : "Process", w * .66)];
  const geometry: GraphEdgeGeometry = { from_vertex_id: vertices[0].id, to_vertex_id: vertices[1].id, from_port: "right", to_port: "left", waypoints: [],
    label_box: { x: w * .36, y: h * .52, width: w * .28, height: h * .14 }, appearance: { at: reveal, duration: reveal } };
  return { id, scene_id: scene.id, component_version: 1, subject_ref: { kind: "scene", scene_id: scene.id }, time_range: { start: { num: "0", den: "1" }, duration: structuredClone(scene.duration) },
    brand_profile_ref: null, variant_profile_ref: profile.id, reference_frame: { width: w, height: h }, motion: "full",
    style: { background: "#101820", vertex_fill: "#263B4F", text_color: "#FFFFFF", edge_color: "#77C9D4", accent_color: "#E9B872", font_family: "Instrument Sans Variable", font_size: Math.min(w, h) / 28,
      edge_width: Math.max(2, Math.min(w, h) / 240), disclosure_box: { x: w * .06, y: h * .78, width: w * .88, height: h * .16 } },
    content: kind === "causal_diagram" ? { kind, vertices, edges: [{ ...geometry, id: crypto.randomUUID(), label: "Proposed\ninfluence", influence: "unspecified", basis: { kind: "author_hypothesis", disclosure: "Author hypothesis;\ncausal effect has not\nbeen established." } }] }
      : { kind, source: { kind: "synthetic", disclosure: "Synthetic system flow\nauthored for explanation;\nthis is not a recorded execution." }, vertices, edges: [{ ...geometry, id: crypto.randomUUID(), label: "Transfer\n", channel: "data" }], events: [] },
    authorship: { author_label: "", rationale: "", source_snapshot: graphSourceSnapshot(project) } };
}
