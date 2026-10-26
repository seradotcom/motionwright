import type { CanvasNode, CanvasTransform, Change, CoordinateSpace, NodeProperty, NodeStyle, NodeRelation, Project, ReparentCoordinatePolicy } from "./types";

export interface CanvasReparentFrame {
  id: string; coordinate_space: CoordinateSpace; geometricMotion: boolean;
  x: number; y: number; width: number; height: number; rotation_deg: number;
}
export interface CanvasReparentSnapshot {
  parentId: string | null;
  child: { parent_id: string | null; coordinate_space: CoordinateSpace; geometricMotion: boolean; pose: CanvasTransform };
  oldParent: CanvasReparentFrame | null; newParent: CanvasReparentFrame | null;
}
export interface CanvasReparentProposal {
  pose: CanvasTransform; positionChanged: boolean; rotationChanged: boolean; snapshot: CanvasReparentSnapshot;
}
export interface CanvasReparentIntent {
  preserve?: ReparentCoordinatePolicy;
  sequence: number; snapshot: CanvasReparentSnapshot;
}
export type CanvasReparentUnchanged = Pick<CanvasNode,
  "id" | "name" | "kind" | "text" | "coordinate_space" | "style" | "relations" | "property_locks" | "keyframes">;
export interface CanvasReparentSubmission extends CanvasReparentIntent { pose: CanvasTransform; unchanged: CanvasReparentUnchanged }

const canvasPose = (node: CanvasTransform): CanvasTransform => ({ x: node.x, y: node.y, width: node.width,
  height: node.height, rotation_deg: node.rotation_deg, opacity: node.opacity });
const geometricMotion = (node: CanvasNode) => node.keyframes.some(key => key.property !== "opacity");
const unchangedReparentFields = (node: CanvasReparentUnchanged): CanvasReparentUnchanged => ({
  id: node.id, name: node.name, kind: node.kind, text: node.text, coordinate_space: node.coordinate_space,
  style: Object.fromEntries(CANVAS_STYLE_FIELDS.map(field => [field, node.style[field]])) as unknown as NodeStyle,
  relations: node.relations.map(item => ({ id: item.id, kind: item.kind, target_id: item.target_id })),
  property_locks: [...node.property_locks],
  keyframes: node.keyframes.map(key => ({ at: { num: key.at.num, den: key.at.den }, property: key.property,
    value: key.value, interpolation: key.interpolation })),
});
const reparentFrame = (node: CanvasNode | undefined): CanvasReparentFrame | null => node ? {
  id: node.id, coordinate_space: node.coordinate_space, geometricMotion: geometricMotion(node),
  x: node.x, y: node.y, width: node.width, height: node.height, rotation_deg: node.rotation_deg,
} : null;
function captureReparentSnapshot(project: Project, sceneId: string, nodeId: string, parentId: string | null): CanvasReparentSnapshot {
  const scene = project.scenes.find(item => item.id === sceneId), node = scene?.nodes.find(item => item.id === nodeId);
  if (!scene || !node) throw new Error("The Canvas object is no longer in this saved scene.");
  return { parentId, child: { parent_id: node.parent_id, coordinate_space: node.coordinate_space,
    geometricMotion: geometricMotion(node), pose: canvasPose(node) },
    oldParent: reparentFrame(scene.nodes.find(item => item.id === node.parent_id)),
    newParent: reparentFrame(scene.nodes.find(item => item.id === parentId)) };
}
function validateReparentPose(pose: CanvasTransform): void {
  if (![pose.x, pose.y, pose.width, pose.height, pose.rotation_deg, pose.opacity].every(Number.isFinite))
    throw new Error("Canvas reparent geometry must remain finite.");
  if (pose.width < 0 || pose.height < 0 || pose.opacity < 0 || pose.opacity > 1)
    throw new Error("Canvas reparent geometry is outside the admitted size or opacity bounds.");
}
function validateReparentPolicy(preserve: unknown): asserts preserve is ReparentCoordinatePolicy | undefined {
  if (preserve !== undefined && preserve !== "world" && preserve !== "local")
    throw new Error("The Canvas reparent policy must be absent, world or local.");
}
const frameIdentity = (frame: CanvasReparentFrame | null) => frame === null
  || (frame.x === 0 && frame.y === 0 && frame.rotation_deg === 0);
const sameReparentFrame = (left: CanvasReparentFrame | null, right: CanvasReparentFrame | null) =>
  (frameIdentity(left) && frameIdentity(right)) || Boolean(left && right && left.x === right.x && left.y === right.y
    && left.rotation_deg === right.rotation_deg
    && (left.rotation_deg === 0 || (left.width === right.width && left.height === right.height)));
function reparentPose(snapshot: CanvasReparentSnapshot, preserve?: ReparentCoordinatePolicy): CanvasTransform {
  validateReparentPolicy(preserve);
  const pose = canvasPose(snapshot.child.pose);
  validateReparentPose(pose);
  // An absent policy is historical world preservation. It never interprets
  // an old coordinate space or changes any stored motion channel.
  if (preserve !== "local") return pose;
  if ((snapshot.child.parent_id !== null && !snapshot.oldParent) || (snapshot.parentId !== null && !snapshot.newParent))
    throw new Error("A captured Canvas parent frame is missing. Review the saved hierarchy.");
  for (const item of [snapshot.child, snapshot.oldParent, snapshot.newParent]) {
    if (item && item.coordinate_space !== "project_pixels")
      throw new Error("Local reparenting requires project_pixels for the child and both direct parents.");
    if (item?.geometricMotion) throw new Error("Local reparenting cannot reinterpret geometric motion keyframes. Keep world geometry or review the authored motion.");
  }
  for (const frame of [snapshot.oldParent, snapshot.newParent]) if (frame) {
    if (![frame.x, frame.y, frame.width, frame.height, frame.rotation_deg].every(Number.isFinite)
      || frame.width < 0 || frame.height < 0) throw new Error("Canvas parent frames must contain finite geometry and nonnegative sizes.");
  }
  if (sameReparentFrame(snapshot.oldParent, snapshot.newParent)) return pose;
  const rotate = (x: number, y: number, degrees: number): [number, number] => {
    const radians = degrees * (Math.PI / 180), cosine = Math.cos(radians), sine = Math.sin(radians);
    return [cosine * x - sine * y, sine * x + cosine * y];
  };
  let cx = pose.x + pose.width / 2, cy = pose.y + pose.height / 2;
  const old = snapshot.oldParent, next = snapshot.newParent;
  // F=T(x,y)T(w/2,h/2)R(theta)T(-w/2,-h/2). These are only
  // the two direct frames; ancestors, camera and opacity do not participate.
  if (old) {
    const local = rotate(cx - (old.x + old.width / 2), cy - (old.y + old.height / 2), -old.rotation_deg);
    cx = local[0] + old.width / 2; cy = local[1] + old.height / 2;
  }
  if (next) {
    const world = rotate(cx - next.width / 2, cy - next.height / 2, next.rotation_deg);
    cx = next.x + next.width / 2 + world[0]; cy = next.y + next.height / 2 + world[1];
  }
  const result = { ...pose, x: cx - pose.width / 2, y: cy - pose.height / 2,
    rotation_deg: pose.rotation_deg + ((next?.rotation_deg ?? 0) - (old?.rotation_deg ?? 0)) };
  validateReparentPose(result);
  return result;
}

/** Shared by the editor and browser adapter. This function only reads the
 * saved Project; it validates the complete operation before either caller
 * changes parent, order or pose. The service remains authoritative. */
export function proposeCanvasReparent(
  project: Project, sceneId: string, nodeId: string, parentId: string | null, preserve?: ReparentCoordinatePolicy,
): CanvasReparentProposal {
  validateReparentPolicy(preserve);
  const scene = project.scenes.find(item => item.id === sceneId), node = scene?.nodes.find(item => item.id === nodeId);
  if (!scene || !node) throw new Error("The Canvas object is no longer in this saved scene.");
  if (parentId !== null && (parentId === nodeId || !scene.nodes.some(item => item.id === parentId)))
    throw new Error("Select another object in the same saved scene as parent.");
  let cursor = parentId, seen = new Set<string>();
  while (cursor !== null) {
    if (cursor === nodeId) throw new Error("Cannot move this object under its descendant.");
    if (seen.has(cursor)) throw new Error("The parent hierarchy is cyclic; review the saved scene.");
    seen.add(cursor);
    const parent = scene.nodes.find(item => item.id === cursor);
    if (!parent) throw new Error("A saved Canvas parent frame is missing.");
    cursor = parent.parent_id;
  }
  const snapshot = captureReparentSnapshot(project, sceneId, nodeId, parentId);
  if (snapshot.child.parent_id !== null && !snapshot.oldParent) throw new Error("The previous Canvas parent frame is missing.");
  const pose = reparentPose(snapshot, preserve), positionChanged = pose.x !== node.x || pose.y !== node.y,
    rotationChanged = pose.rotation_deg !== node.rotation_deg;
  if (project.locks.some(lock => lock.resource === "scene:" + sceneId && (lock.kind === "content" || lock.kind === "position"))
    || node.property_locks.includes("parent") || node.property_locks.includes("order")
    || (positionChanged && node.property_locks.includes("position"))
    || (rotationChanged && node.property_locks.includes("rotation")))
    throw new Error("A saved lock protects these Canvas reparent fields. Your draft is kept.");
  return { pose, positionChanged, rotationChanged, snapshot };
}

// These records preserve editor input. Project revisions, locks and the existing
// service remain authoritative; recovery never executes a stored command.
export const CANVAS_TRANSFORM_FIELDS = ["x", "y", "width", "height", "rotation_deg", "opacity"] as const;
export const CANVAS_STYLE_FIELDS = ["fill", "stroke", "stroke_width", "font_family", "font_size", "font_weight", "line_height", "blend_mode"] as const;
export const CANVAS_HIERARCHY_FIELDS = ["parent_id", "z_index"] as const;
export const CANVAS_RELATION_FIELDS = ["relation_kind", "relation_target"] as const;
const CANVAS_RELATION_KINDS = ["align_left","align_center_x","align_right","align_top","align_center_y","align_bottom","follow","attach"] as const;
export const CANVAS_DRAFT_FIELDS = ["text", ...CANVAS_TRANSFORM_FIELDS, ...CANVAS_STYLE_FIELDS, ...CANVAS_HIERARCHY_FIELDS, ...CANVAS_RELATION_FIELDS] as const;
export type CanvasDraftField = typeof CANVAS_DRAFT_FIELDS[number];
export type CanvasDraftGroup = "text" | "transform" | "style" | "hierarchy" | "relations";
export const CANVAS_FIELD_LABELS: Record<CanvasDraftField, string> = {
  text: "Text", x: "X", y: "Y", width: "W", height: "H", rotation_deg: "Rotation", opacity: "Opacity",
  fill: "Fill", stroke: "Stroke", stroke_width: "Stroke width", font_family: "Font family",
  font_size: "Font size", font_weight: "Font weight", line_height: "Line height", blend_mode: "Blend mode",
  parent_id: "Parent", z_index: "Z order", relation_kind: "Relation kind", relation_target: "Relation target",
};
export interface CanvasFieldDraft {
  raw: string; base: string; baseRevision: number; sequence: number; intent?: true;
}
export type CanvasDraftContext = Pick<CanvasNode, "kind" | "parent_id" | "coordinate_space">;
export interface CanvasSubmission {
  id: string; scope: string; revision: number; group: CanvasDraftGroup | "removal";
  context: CanvasDraftContext;
  fields: Partial<Record<CanvasDraftField, { raw: string; sequence: number; value: string }>>;
  relations?: NodeRelation[]; relationOperation?: "add" | "remove";
  reparent?: CanvasReparentSubmission;
}
export interface CanvasWorkspaceDraft {
  schema: "motionwright.canvas-draft/1" | "motionwright.canvas-draft/2" | "motionwright.canvas-draft/3" | "motionwright.canvas-draft/4" | "motionwright.canvas-draft/5" | "motionwright.canvas-draft/6"; scope: string; sceneId: string; nodeId: string;
  baseContext: CanvasDraftContext;
  relationsBase: NodeRelation[];
  fields: Record<CanvasDraftField, CanvasFieldDraft>; awaiting: CanvasSubmission | null;
  reparent?: CanvasReparentIntent;
}
export interface CanvasDraftConflict { field: CanvasDraftField; before: string; current: string; proposed: string; futureRevision: boolean }
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
export const canvasDraftScope = (project: Project, sceneId: string, nodeId: string) =>
  JSON.stringify([project.id, project.generation, project.active_branch, sceneId, nodeId]);
export const canvasGroupFields = (group: CanvasDraftGroup): readonly CanvasDraftField[] =>
  group === "text" ? ["text"] : group === "style" ? CANVAS_STYLE_FIELDS
    : group === "hierarchy" ? CANVAS_HIERARCHY_FIELDS : group === "relations" ? CANVAS_RELATION_FIELDS : CANVAS_TRANSFORM_FIELDS;
export const canvasFieldHasInput = (field: CanvasFieldDraft) => field.intent === true || field.raw !== field.base;
export const canvasDraftHasWork = (draft: CanvasWorkspaceDraft) => draft.awaiting !== null || draft.reparent !== undefined
  || CANVAS_DRAFT_FIELDS.some(field => canvasFieldHasInput(draft.fields[field]));
export function canvasNumber(raw: string): number {
  if (!decimal.test(raw.trim()) || !Number.isFinite(Number(raw))) throw new Error("Enter a finite decimal number.");
  return Number(raw);
}
const styleStringFields = ["fill", "stroke", "font_family"] as const;
const optionalNumericStyle = ["font_size", "font_weight", "line_height"] as const;
const isStyleField = (field: CanvasDraftField): field is typeof CANVAS_STYLE_FIELDS[number] =>
  (CANVAS_STYLE_FIELDS as readonly string[]).includes(field);
const isStyleString = (field: CanvasDraftField) => (styleStringFields as readonly string[]).includes(field);
export function canvasFieldIssue(field: CanvasDraftField, raw: string): string | null {
  if (field === "text") return new TextEncoder().encode(raw).length > 100_000
    ? "Canvas text must fit within 100,000 UTF-8 bytes." : null;
  if (isStyleString(field)) return new TextEncoder().encode(raw.trim()).length > 512
    ? "Canvas style values must fit within 512 UTF-8 bytes." : null;
  if (field === "relation_kind") return (CANVAS_RELATION_KINDS as readonly string[]).includes(raw)
    ? null : "Choose a supported Canvas relation.";
  if (field === "relation_target") return raw.length <= 256 ? null : "Choose a valid object in the same Canvas scene.";
  if (field === "parent_id") return new TextEncoder().encode(raw).length > 256 ? "Select a valid Canvas parent." : null;
  if (field === "z_index" && (!/^[+-]?\d+$/.test(raw.trim()) || !Number.isSafeInteger(Number(raw))
    || Number(raw) < -2147483648 || Number(raw) > 2147483647)) return "Use a signed 32-bit whole Z order.";
  if (field === "blend_mode") return ["normal", "multiply", "screen", "add"].includes(raw)
    ? null : "Choose an admitted blend mode.";
  if ((optionalNumericStyle as readonly string[]).includes(field) && raw.trim() === "") return null;
  if (field === "font_weight" && !/^[+-]?\d+$/.test(raw.trim())) return "Use a whole font weight.";
  try {
    const value = canvasNumber(raw);
    if ((field === "width" || field === "height") && value < 0) return "Use a size of zero or greater.";
    if (field === "opacity" && (value < 0 || value > 1)) return "Use an opacity between zero and one.";
    if (field === "stroke_width" && (value < 0 || value > 1000)) return "Use a stroke width between zero and 1000.";
    if (field === "font_size" && (value < 1 || value > 2048)) return "Use a font size from 1 to 2048.";
    if (field === "font_weight" && (value < 1 || value > 1000 || !Number.isSafeInteger(value))) return "Use a whole font weight from 1 to 1000.";
    if (field === "line_height" && (value < .1 || value > 20)) return "Use a line height between 0.1 and 20.";
    return null;
  } catch { return "Enter a finite decimal number."; }
}
function proposed(field: CanvasDraftField, raw: string): string {
  if (field === "text" || field === "blend_mode" || field === "parent_id" || field === "relation_kind" || field === "relation_target") return raw;
  if (isStyleString(field)) return raw.trim();
  if ((optionalNumericStyle as readonly string[]).includes(field) && raw.trim() === "") return "";
  return canvasFieldIssue(field, raw) ? "invalid:" + raw : String(canvasNumber(raw));
}
const canonical = (node: CanvasNode, field: CanvasDraftField): string =>
  field === "text" ? node.text ?? "" : isStyleField(field)
    ? String(node.style[field] ?? "") : field === "parent_id" ? node.parent_id ?? ""
      : field === "relation_kind" ? "align_left" : field === "relation_target" ? "" : String(node[field]);
const styleFromSubmission = (node: CanvasNode, values: CanvasSubmission["fields"]): NodeStyle => {
  const style = { ...node.style };
  for (const field of CANVAS_STYLE_FIELDS) {
    const raw = values[field]!.value;
    (style as unknown as Record<string, unknown>)[field] = isStyleString(field) || (optionalNumericStyle as readonly string[]).includes(field)
      ? raw === "" ? null : isStyleString(field) ? raw : Number(raw)
      : field === "blend_mode" ? raw : Number(raw);
  }
  return style;
};
const relationField = (field: CanvasDraftField) => (CANVAS_RELATION_FIELDS as readonly string[]).includes(field);
const relationListEqual = (a: NodeRelation[], b: NodeRelation[]): boolean => JSON.stringify(a) === JSON.stringify(b);
const nodeContext = (node: CanvasNode): CanvasDraftContext => ({ kind: node.kind, parent_id: node.parent_id, coordinate_space: node.coordinate_space });
const contextMatches = (before: CanvasDraftContext, current: CanvasDraftContext, group: CanvasDraftGroup) => before.kind === current.kind
  && (group === "text" || (before.parent_id === current.parent_id && before.coordinate_space === current.coordinate_space));
const groupHasInput = (draft: CanvasWorkspaceDraft, group: CanvasDraftGroup) => (group === "hierarchy" && draft.reparent !== undefined)
  || canvasGroupFields(group).some(field => canvasFieldHasInput(draft.fields[field]));
export function canvasDraftNode(draft: CanvasWorkspaceDraft, project: Project): CanvasNode {
  if (draft.scope !== canvasDraftScope(project, draft.sceneId, draft.nodeId)) throw new Error("This Canvas draft belongs to another project, generation, branch or object.");
  const node = project.scenes.find(scene => scene.id === draft.sceneId)?.nodes.find(item => item.id === draft.nodeId);
  if (!node) throw new Error("This Canvas object is no longer in the saved project. Its draft has been kept.");
  return node;
}
export function createCanvasDraft(project: Project, sceneId: string, nodeId: string): CanvasWorkspaceDraft {
  const draft: CanvasWorkspaceDraft = { schema: "motionwright.canvas-draft/5", scope: canvasDraftScope(project, sceneId, nodeId),
    sceneId, nodeId, baseContext: { kind: "", parent_id: null, coordinate_space: "project_pixels" },
    relationsBase: [], fields: {} as CanvasWorkspaceDraft["fields"], awaiting: null };
  const node = canvasDraftNode(draft, project);
  draft.baseContext = nodeContext(node);
  draft.relationsBase = structuredClone(node.relations);
  for (const field of CANVAS_DRAFT_FIELDS) draft.fields[field] = {
    raw: canonical(node, field), base: canonical(node, field), baseRevision: project.revision, sequence: 0,
  };
  return draft;
}
export function editCanvasField(draft: CanvasWorkspaceDraft, field: CanvasDraftField, raw: string, project?: Project): CanvasWorkspaceDraft {
  const current = draft.fields[field];
  const next = { ...draft, fields: { ...draft.fields, [field]: { ...current, raw, sequence: current.sequence + 1,
    ...(draft.awaiting?.fields[field] ? { intent: true as const } : {}) } } };
  if (!(CANVAS_HIERARCHY_FIELDS as readonly string[]).includes(field)) return next;
  if (project) {
    canvasDraftNode(draft, project);
    const captured = captureReparentSnapshot(project, draft.sceneId, draft.nodeId, next.fields.parent_id.raw || null);
    const snapshot = draft.reparent
      ? field === "parent_id" && current.raw !== raw
        ? { ...draft.reparent.snapshot, parentId: captured.parentId, newParent: captured.newParent }
        : draft.reparent.snapshot
      : captured;
    return { ...next, schema: "motionwright.canvas-draft/6", reparent: {
      ...(draft.reparent?.preserve === undefined ? {} : { preserve: draft.reparent.preserve }),
      sequence: (draft.reparent?.sequence ?? 0) + 1, snapshot: structuredClone(snapshot),
    } };
  }
  return draft.reparent ? { ...next, reparent: { ...draft.reparent, sequence: draft.reparent.sequence + 1 } } : next;
}
export function editCanvasReparentPolicy(
  draft: CanvasWorkspaceDraft, project: Project, preserve: ReparentCoordinatePolicy | undefined,
): CanvasWorkspaceDraft {
  validateReparentPolicy(preserve);
  canvasDraftNode(draft, project);
  return { ...draft, schema: "motionwright.canvas-draft/6", reparent: {
    ...(preserve === undefined ? {} : { preserve }), sequence: (draft.reparent?.sequence ?? 0) + 1,
    snapshot: structuredClone(draft.reparent?.snapshot
      ?? captureReparentSnapshot(project, draft.sceneId, draft.nodeId, draft.fields.parent_id.raw || null)),
  } };
}
function reparentSnapshotMatches(draft: CanvasWorkspaceDraft, project: Project): boolean {
  return !draft.reparent || JSON.stringify(draft.reparent.snapshot) === JSON.stringify(
    captureReparentSnapshot(project, draft.sceneId, draft.nodeId, draft.fields.parent_id.raw || null));
}
/** The displayed proposal comes from the retained frames. A separate context
 * conflict blocks preparation if the saved child or either frame has drifted. */
export function canvasDraftReparentProposal(draft: CanvasWorkspaceDraft, project: Project): CanvasReparentProposal {
  canvasDraftNode(draft, project);
  if (!draft.reparent) return proposeCanvasReparent(project, draft.sceneId, draft.nodeId, draft.fields.parent_id.raw || null);
  const snapshot = draft.reparent.snapshot, pose = reparentPose(snapshot, draft.reparent.preserve);
  return { pose, snapshot: structuredClone(snapshot), positionChanged: pose.x !== snapshot.child.pose.x || pose.y !== snapshot.child.pose.y,
    rotationChanged: pose.rotation_deg !== snapshot.child.pose.rotation_deg };
}
export function canvasDraftConflicts(draft: CanvasWorkspaceDraft, project: Project): CanvasDraftConflict[] {
  const node = canvasDraftNode(draft, project);
  const valueConflicts = CANVAS_DRAFT_FIELDS.flatMap(field => {
    if (relationField(field)) return [];
    const input = draft.fields[field], current = canonical(node, field), futureRevision = input.baseRevision > project.revision;
    return canvasFieldHasInput(input) && (futureRevision || (current !== input.base && current !== proposed(field, input.raw)))
      ? [{ field, before: input.base, current, proposed: input.raw, futureRevision }] : [];
  });
  if (groupHasInput(draft, "relations") && !relationListEqual(draft.relationsBase, node.relations)) {
    valueConflicts.push({ field: "relation_target", before: JSON.stringify(draft.relationsBase),
      current: JSON.stringify(node.relations), proposed: draft.fields.relation_target.raw,
      futureRevision: false });
  }
  return valueConflicts;
}
/** The same structure invariant used by scoped proposals, with the coordinate
 * space included: equal numbers do not establish equal creative intent after
 * reparenting or changing an object's kind. */
export function canvasContextConflicts(draft: CanvasWorkspaceDraft, project: Project): CanvasDraftGroup[] {
  const current = nodeContext(canvasDraftNode(draft, project));
  return (["text", "transform", "style", "hierarchy", "relations"] as const).filter(group => groupHasInput(draft, group)
    && (!contextMatches(draft.baseContext, current, group) || (group === "hierarchy" && !reparentSnapshotMatches(draft, project))));
}
export function reviewCanvasContext(draft: CanvasWorkspaceDraft, project: Project): CanvasWorkspaceDraft {
  if (draft.awaiting?.group === "hierarchy") throw new Error("Review the unconfirmed Canvas hierarchy submission before rebasing its retained frames.");
  const reviewed = { ...draft, baseContext: nodeContext(canvasDraftNode(draft, project)),
    ...(draft.reparent ? { reparent: { ...draft.reparent, sequence: draft.reparent.sequence + 1,
      snapshot: captureReparentSnapshot(project, draft.sceneId, draft.nodeId, draft.fields.parent_id.raw || null) } } : {}) };
  return reconcileCanvasDraft(reviewed, project);
}
export function canvasSubmissionMatches(project: Project, draft: CanvasWorkspaceDraft, attempt: CanvasSubmission): boolean {
  return canvasSubmissionStateMatches(project, draft, attempt, false);
}
function originalReparentPoseMatches(node: CanvasNode, submitted: CanvasReparentSubmission): boolean {
  const predicted = submitted.pose;
  if (CANVAS_TRANSFORM_FIELDS.some(field => !Number.isFinite(node[field]) || !Number.isFinite(predicted[field]))) return false;
  if (["width", "height", "rotation_deg", "opacity"].some(field => node[field as keyof CanvasTransform] !== predicted[field as keyof CanvasTransform])) return false;
  if (submitted.preserve !== "local" || sameReparentFrame(submitted.snapshot.oldParent, submitted.snapshot.newParent))
    return node.x === predicted.x && node.y === predicted.y;
  // This bound is exclusively the declared original-response ACK relation.
  // Returned values never enter its scale; source drift and locks stay exact.
  const terms = [1, predicted.x, predicted.y];
  for (const frame of [submitted.snapshot.child.pose, submitted.snapshot.oldParent, submitted.snapshot.newParent])
    if (frame) terms.push(frame.x, frame.y, frame.width, frame.height);
  if (!terms.every(Number.isFinite)) return false;
  const scale = Math.max(...terms.map(Math.abs)), tolerance = 1e-9 + 64 * Number.EPSILON * scale;
  if (!Number.isFinite(tolerance)) return false;
  return Math.abs(node.x - predicted.x) <= tolerance && Math.abs(node.y - predicted.y) <= tolerance;
}
function canvasSubmissionStateMatches(project: Project, draft: CanvasWorkspaceDraft, attempt: CanvasSubmission, originalResponse: boolean): boolean {
  // A local removal token is not the service request_id. Absence can be
  // observed separately, but cannot acknowledge this particular invocation.
  if (attempt.group === "removal" || draft.scope !== attempt.scope || project.revision !== attempt.revision + 1) return false;
  let node: CanvasNode;
  try { node = canvasDraftNode(draft, project); } catch { return false; }
  // Reparent is the only group allowed to change parent context; the exact
  // committed parent and Z values are still checked below.
  return (attempt.group === "hierarchy"
    ? attempt.context.kind === node.kind && attempt.context.coordinate_space === node.coordinate_space
    : contextMatches(attempt.context, nodeContext(node), attempt.group))
    && (attempt.group !== "hierarchy" || !attempt.reparent
      || ((originalResponse ? originalReparentPoseMatches(node, attempt.reparent)
        : CANVAS_TRANSFORM_FIELDS.every(field => node[field] === attempt.reparent!.pose[field]))
        && JSON.stringify(unchangedReparentFields(node)) === JSON.stringify(attempt.reparent.unchanged)))
    && (attempt.group === "relations"
      ? Boolean(attempt.relations && relationListEqual(node.relations, attempt.relations))
      : canvasGroupFields(attempt.group).every(field => canonical(node, field) === attempt.fields[field]?.value));
}
export function reconcileCanvasDraft(draft: CanvasWorkspaceDraft, project: Project): CanvasWorkspaceDraft {
  return reconcileCanvasDraftObservation(draft, project);
}
// Recovery may reconstruct the same closed JSON object in a different key order.
// Keep every own field and array position exact without rewriting either payload.
function sameCanvasSubmissionValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((value, index) => sameCanvasSubmissionValue(value, right[index]));
  }
  if (!record(left) || !record(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length
    && keys.every(key => Object.hasOwn(right, key) && sameCanvasSubmissionValue(left[key], right[key]));
}
/** Only the caller of the original successful mutation may supply this
 * Project. Recovery and project observations never manufacture this signal. */
export function acknowledgeCanvasReparent(draft: CanvasWorkspaceDraft, project: Project, original: CanvasSubmission): CanvasWorkspaceDraft {
  const pending = draft.awaiting;
  if (pending?.group !== "hierarchy" || !pending.reparent || !sameCanvasSubmissionValue(pending, original)
    || !canvasSubmissionStateMatches(project, draft, pending, true)) return draft;
  return reconcileCanvasDraftObservation(draft, project, original.id);
}
function reconcileCanvasDraftObservation(draft: CanvasWorkspaceDraft, project: Project, acknowledgedHierarchyId?: string): CanvasWorkspaceDraft {
  if (draft.awaiting?.group === "removal" && canvasRemovalObservation(draft, project) === "absent") {
    // Keep both the uncertainty record and any input authored after dispatch.
    // A removed object is never reconstructed from its local recovery copy.
    return draft;
  }
  const node = canvasDraftNode(draft, project), fields = { ...draft.fields };
  // v1 recovery never held style fields. Hydrate from the *opened* canonical
  // object, never from the recovery copy belonging to another writer.
  if (draft.schema === "motionwright.canvas-draft/1") for (const field of CANVAS_STYLE_FIELDS) {
    const value = canonical(node, field);
    fields[field] = { raw: value, base: value, baseRevision: project.revision, sequence: 0 };
  }
  if (draft.schema === "motionwright.canvas-draft/1" || draft.schema === "motionwright.canvas-draft/2") for (const field of CANVAS_HIERARCHY_FIELDS) {
    const value = canonical(node, field);
    fields[field] = { raw: value, base: value, baseRevision: project.revision, sequence: 0 };
  }
  if (draft.schema !== "motionwright.canvas-draft/4" && draft.schema !== "motionwright.canvas-draft/5"
    && draft.schema !== "motionwright.canvas-draft/6") for (const field of CANVAS_RELATION_FIELDS) {
    const value = canonical(node, field);
    fields[field] = { raw: value, base: value, baseRevision: project.revision, sequence: 0 };
  }
  const earlierRelations = draft.schema === "motionwright.canvas-draft/4" || draft.schema === "motionwright.canvas-draft/5" || draft.schema === "motionwright.canvas-draft/6"
    ? draft.relationsBase : structuredClone(node.relations);
  let awaiting = draft.awaiting;
  const ownRelationAcknowledged = Boolean(awaiting && awaiting.group === "relations"
    && canvasSubmissionMatches(project, draft, awaiting));
  const ownHierarchyAcknowledged = Boolean(awaiting && awaiting.group === "hierarchy" && awaiting.reparent
    && acknowledgedHierarchyId === awaiting.id
    && canvasSubmissionStateMatches(project, draft, awaiting, true));
  const hierarchyAttempt = awaiting?.group === "hierarchy" ? awaiting : null;
  if (hierarchyAttempt && !ownHierarchyAcknowledged) {
    // Neither equal values nor a newer peer revision identifies the original
    // invocation. Keep its payload and author bases frozen, including legacy
    // hierarchy snapshots which have no pose/frame metadata to reconstruct.
    if (draft.schema === "motionwright.canvas-draft/3") return { ...draft,
      schema: "motionwright.canvas-draft/5", fields, relationsBase: earlierRelations };
    return draft;
  }
  if (awaiting && awaiting.group !== "removal" && (awaiting.group !== "hierarchy" || ownHierarchyAcknowledged)
    && canvasSubmissionStateMatches(project, draft, awaiting, ownHierarchyAcknowledged)) {
    if (awaiting.group === "relations") {
      if (awaiting.relationOperation === "add") for (const field of CANVAS_RELATION_FIELDS) {
        const input = fields[field], sent = awaiting.fields[field]!;
        // Keep the selected kind for the next operation. A consumed target is
        // cleared only if the human did not edit it again while awaiting ACK.
        const unchanged = input.sequence === sent.sequence && input.raw === sent.raw;
        const base = field === "relation_target" ? "" : sent.raw;
        fields[field] = { raw: unchanged ? base : input.raw, base, baseRevision: project.revision,
          sequence: input.sequence, ...(!unchanged ? { intent: true as const } : {}) };
      }
    } else for (const field of canvasGroupFields(awaiting.group)) {
      const input = fields[field], sent = awaiting.fields[field]!, base = canonical(node, field);
      // Comparing raw alone loses an edit that changes away and back while an
      // acknowledgement is in flight. The input sequence distinguishes it.
      const unchanged = input.sequence === sent.sequence && input.raw === sent.raw;
      fields[field] = { raw: unchanged ? base : input.raw, base, baseRevision: project.revision,
        sequence: input.sequence, ...(!unchanged ? { intent: true as const } : {}) };
    }
    awaiting = null;
  } else if (awaiting && project.revision > awaiting.revision && awaiting.group !== "relations"
    && awaiting.group !== "removal" && awaiting.group !== "hierarchy") {
    // This is not an acknowledgement of our snapshot. Keep every input and
    // its original base; overlapping current values require an explicit choice.
    awaiting = null;
  }
  for (const field of CANVAS_DRAFT_FIELDS) {
    const input = fields[field], current = canonical(node, field), dirty = canvasFieldHasInput(input);
    // The relation selector is an author's choice, not a saved node attribute.
    // Never reset kind/target merely because a peer publishes another revision.
    if (relationField(field)) {
      if (!dirty) fields[field] = { ...input, baseRevision: project.revision };
      continue;
    }
    if (dirty && input.baseRevision > project.revision) continue;
    if (dirty && !contextMatches(draft.baseContext, nodeContext(node), field === "text" ? "text" : isStyleField(field) ? "style" : (CANVAS_HIERARCHY_FIELDS as readonly string[]).includes(field) ? "hierarchy" : "transform")) continue;
    if (!dirty || current === input.base || current === proposed(field, input.raw)) fields[field] = {
      ...input, raw: dirty ? input.raw : current, base: current, baseRevision: project.revision,
    };
  }
  const next: CanvasWorkspaceDraft = { ...draft, schema: draft.schema === "motionwright.canvas-draft/6" ? draft.schema : "motionwright.canvas-draft/5", fields, awaiting,
    relationsBase: ownRelationAcknowledged || (!awaiting && !groupHasInput({ ...draft, fields, awaiting } as CanvasWorkspaceDraft, "relations"))
      ? structuredClone(node.relations) : earlierRelations }, currentContext = nodeContext(node);
  if (ownHierarchyAcknowledged && hierarchyAttempt?.reparent && next.reparent
    && next.reparent.sequence === hierarchyAttempt.reparent.sequence
    && CANVAS_HIERARCHY_FIELDS.every(field => fields[field].sequence === hierarchyAttempt.fields[field]?.sequence
      && !canvasFieldHasInput(fields[field]))) delete next.reparent;
  const textInput = groupHasInput(next, "text"), transformInput = groupHasInput(next, "transform"),
    styleInput = groupHasInput(next, "style"), hierarchyInput = groupHasInput(next, "hierarchy"),
    relationInput = groupHasInput(next, "relations");
  next.baseContext = {
    kind: textInput || transformInput || styleInput || hierarchyInput || relationInput ? draft.baseContext.kind : currentContext.kind,
    // A matching hierarchy ACK intentionally changes parent. A later Z edit
    // must be rebased onto that acknowledged parent without erasing its raw
    // intent. Independently authored transform/style still require review.
    parent_id: transformInput || styleInput || relationInput || (hierarchyInput && !ownHierarchyAcknowledged)
      ? draft.baseContext.parent_id : currentContext.parent_id,
    coordinate_space: transformInput || styleInput || hierarchyInput || relationInput ? draft.baseContext.coordinate_space : currentContext.coordinate_space,
  };
  return next;
}
export function resolveCanvasField(draft: CanvasWorkspaceDraft, project: Project, field: CanvasDraftField, choice: "saved" | "draft"): CanvasWorkspaceDraft {
  const currentNode = canvasDraftNode(draft, project);
  const base = canonical(currentNode, field), input = draft.fields[field];
  const peerRelationsConflict = field === "relation_target" && !relationListEqual(draft.relationsBase, currentNode.relations);
  return { ...draft, ...(peerRelationsConflict ? { relationsBase: structuredClone(currentNode.relations) } : {}),
    fields: { ...draft.fields, [field]: {
    raw: choice === "saved" ? base : input.raw,
    base: peerRelationsConflict && choice === "draft" ? input.base : base, baseRevision: project.revision,
    sequence: input.sequence + 1, ...(choice === "draft" && input.intent ? { intent: true as const } : {}),
  } } };
}
export function releaseCanvasSubmission(draft: CanvasWorkspaceDraft, id: string): CanvasWorkspaceDraft {
  // The caller uses this after a verified pre-dispatch backup failure. An
  // uncertain hierarchy outcome instead requires the guarded, separate-copy
  // review below; this function is not a service rejection receipt.
  return draft.awaiting?.id === id ? { ...draft, awaiting: null } : draft;
}
export function canvasHierarchyReviewIssue(draft: CanvasWorkspaceDraft, project: Project): string | null {
  if (draft.awaiting?.group !== "hierarchy") return "There is no retained Canvas hierarchy submission to review.";
  try { canvasDraftNode(draft, project); }
  catch (reason) { return reason instanceof Error ? reason.message : String(reason); }
  if (project.revision <= draft.awaiting.revision)
    return "A later saved revision is required before separating another hierarchy intent. This revision does not establish the original outcome.";
  return null;
}
/** The UI must verify and retain the original writer bytes before writing this
 * result under a new writer. It creates input only and sends no operation. */
export function reviewCanvasHierarchySubmission(draft: CanvasWorkspaceDraft, project: Project): CanvasWorkspaceDraft {
  const issue = canvasHierarchyReviewIssue(draft, project);
  if (issue) throw new Error(issue);
  return reviewCanvasContext({ ...draft, awaiting: null }, project);
}
function transformLock(field: CanvasDraftField): NodeProperty {
  return field === "x" || field === "y" ? "position" : field === "width" || field === "height" ? "size"
    : field === "rotation_deg" ? "rotation" : field === "text" ? "text" : "opacity";
}
export function canvasGroupIssue(draft: CanvasWorkspaceDraft, project: Project, group: CanvasDraftGroup): string | null {
  const node = canvasDraftNode(draft, project), fields = canvasGroupFields(group);
  if (draft.awaiting) return "Review the unconfirmed Canvas submission before saving again.";
  if (canvasContextConflicts(draft, project).includes(group)) return "Review the changed Canvas object context before saving these inputs.";
  if (canvasDraftConflicts(draft, project).some(conflict => fields.includes(conflict.field))) return "Resolve the overlapping Canvas fields before saving.";
  for (const field of fields) {
    const issue = canvasFieldIssue(field, draft.fields[field].raw);
    if (issue) return CANVAS_FIELD_LABELS[field] + ": " + issue;
  }
  const changed = fields.filter(field => canvasFieldHasInput(draft.fields[field])
    && proposed(field, draft.fields[field].raw) !== canonical(node, field));
  if (changed.length === 0) return "These values already match the saved object.";
  if (group === "hierarchy" && changed.includes("parent_id")) {
    const proposedParent = draft.fields.parent_id.raw;
    const scene = project.scenes.find(item => item.id === draft.sceneId);
    if (proposedParent && (proposedParent === node.id || !scene?.nodes.some(item => item.id === proposedParent))) {
      return "Select another object in the same saved scene as parent.";
    }
    let cursor = scene?.nodes.find(item => item.id === proposedParent);
    const seen = new Set<string>();
    while (cursor) {
      if (cursor.id === node.id) return "Cannot move this object under its descendant.";
      if (seen.has(cursor.id)) return "The parent hierarchy is cyclic; review the saved scene.";
      seen.add(cursor.id);
      cursor = scene?.nodes.find(item => item.id === cursor?.parent_id);
    }
  }
  if (group === "hierarchy") {
    try { proposeCanvasReparent(project, draft.sceneId, draft.nodeId, draft.fields.parent_id.raw || null, draft.reparent?.preserve); }
    catch (reason) { return reason instanceof Error ? reason.message : String(reason); }
  }
  const sceneLock = group === "hierarchy"
    ? project.locks.some(lock => lock.resource === "scene:" + draft.sceneId && (lock.kind === "content" || lock.kind === "position"))
    : project.locks.some(lock => lock.resource === "scene:" + draft.sceneId
      && lock.kind === (group === "text" ? "content" : group === "style" ? "style" : "position"));
  // Rust ReparentCanvasNode protects both fields as one coupled command.
  if (sceneLock || (group === "hierarchy" && (node.property_locks.includes("parent") || node.property_locks.includes("order")))
    || (group !== "hierarchy" && changed.some(field => node.property_locks.includes(group === "style" ? "style" : transformLock(field))))) {
    return "A saved lock protects these Canvas fields. Your draft is kept.";
  }
  return null;
}
export function prepareCanvasSubmission(draft: CanvasWorkspaceDraft, project: Project, group: Exclude<CanvasDraftGroup, "relations">, id: string): {
  draft: CanvasWorkspaceDraft; submission: CanvasSubmission; change: Extract<Change, { type: "update_canvas_text" | "transform_canvas_node" | "update_canvas_style" | "reparent_canvas_node" }>;
} {
  const current = reconcileCanvasDraft(draft, project), issue = canvasGroupIssue(current, project, group);
  if (issue) throw new Error(issue);
  const node = canvasDraftNode(current, project), fields = { ...current.fields };
  const submission: CanvasSubmission = { id, scope: current.scope, revision: project.revision, group, context: nodeContext(node), fields: {} };
  for (const field of canvasGroupFields(group)) {
    const input = fields[field], value = canvasFieldHasInput(input) ? proposed(field, input.raw) : canonical(node, field);
    submission.fields[field] = { raw: input.raw, sequence: input.sequence, value };
    if (value !== canonical(node, field)) fields[field] = { ...input, intent: true };
  }
  let reparent = current.reparent;
  if (group === "hierarchy") {
    const proposal = proposeCanvasReparent(project, current.sceneId, current.nodeId, submission.fields.parent_id!.value || null, reparent?.preserve);
    reparent = reparent ?? { sequence: 0, snapshot: proposal.snapshot };
    submission.reparent = { ...structuredClone(reparent), pose: proposal.pose, unchanged: unchangedReparentFields(node) };
  }
  const change: Extract<Change, { type: "update_canvas_text" | "transform_canvas_node" | "update_canvas_style" | "reparent_canvas_node" }> =
    group === "text"
      ? { type: "update_canvas_text", scene_id: current.sceneId, node_id: current.nodeId, text: submission.fields.text!.value || null }
      : group === "style"
        ? { type: "update_canvas_style", scene_id: current.sceneId, node_id: current.nodeId,
            style: styleFromSubmission(node, submission.fields) }
        : group === "hierarchy"
          ? { type: "reparent_canvas_node", scene_id: current.sceneId, node_id: current.nodeId,
              parent_id: submission.fields.parent_id!.value || null, z_index: Number(submission.fields.z_index!.value),
              ...(reparent?.preserve === undefined ? {} : { preserve: reparent.preserve }) }
          : { type: "transform_canvas_node", scene_id: current.sceneId, node_id: current.nodeId,
              transform: Object.fromEntries(CANVAS_TRANSFORM_FIELDS.map(field => [field, Number(submission.fields[field]!.value)])) as unknown as CanvasTransform };
  return { draft: { ...current, fields, awaiting: submission,
    ...(group === "hierarchy" ? { schema: "motionwright.canvas-draft/6" as const, reparent } : {}) }, submission, change };
}

/** Service validates the complete relation list. Every edit carries the
 * exact proposed list and a stable UUID; uncertain replies are never replayed. */
export function canvasRelationIssue(
  draft: CanvasWorkspaceDraft, project: Project, operation: "add" | "remove", relationId?: string,
): string | null {
  const node = canvasDraftNode(draft, project), scene = project.scenes.find(item => item.id === draft.sceneId);
  if (draft.awaiting) return "Review the unconfirmed Canvas submission before editing relations.";
  if (canvasContextConflicts(draft, project).includes("relations"))
    return "Review the changed Canvas object context before editing relations.";
  if (groupHasInput(draft, "relations") && !relationListEqual(draft.relationsBase, node.relations))
    return "Review the changed relation list and choose the saved or authored intent.";
  if (project.locks.some(lock => lock.resource === "scene:" + draft.sceneId && lock.kind === "position")
    || node.property_locks.includes("position")) return "The saved position lock protects Canvas relations.";
  if (operation === "remove")
    return !relationId || !node.relations.some(item => item.id === relationId)
      ? "The selected relation is no longer saved. Refresh the object." : null;
  const kind = draft.fields.relation_kind.raw, target = draft.fields.relation_target.raw;
  if (canvasFieldIssue("relation_kind", kind)) return "Choose a valid Canvas relation.";
  if (!target || target === node.id || !scene?.nodes.some(item => item.id === target))
    return "Select another saved Canvas object as the relation target.";
  if (node.relations.length >= 128) return "Canvas relations are limited to 128.";
  if (node.relations.some(item => item.kind === kind && item.target_id === target))
    return "The same relation already exists; review the saved list.";
  return null;
}
export function prepareCanvasRelationSubmission(
  draft: CanvasWorkspaceDraft, project: Project, operation: "add" | "remove",
  id: string, relationId?: string,
): { draft: CanvasWorkspaceDraft; submission: CanvasSubmission; change: Extract<Change, { type: "set_canvas_relations" }> } {
  const current = reconcileCanvasDraft(draft, project);
  const issue = canvasRelationIssue(current, project, operation, relationId);
  if (issue) throw new Error(issue);
  const node = canvasDraftNode(current, project);
  const relations: NodeRelation[] = operation === "add"
    ? [...node.relations, { id, kind: current.fields.relation_kind.raw as NodeRelation["kind"],
        target_id: current.fields.relation_target.raw }]
    : node.relations.filter(item => item.id !== relationId);
  const fields: CanvasSubmission["fields"] = {};
  if (operation === "add") for (const field of CANVAS_RELATION_FIELDS) {
    const input = current.fields[field];
    fields[field] = { raw: input.raw, sequence: input.sequence, value: proposed(field, input.raw) };
  }
  const submission: CanvasSubmission = { id, scope: current.scope, revision: project.revision,
    group: "relations", context: nodeContext(node), fields,
    relations: structuredClone(relations), relationOperation: operation };
  return { draft: { ...current, awaiting: submission }, submission,
    change: { type: "set_canvas_relations", scene_id: current.sceneId,
      node_id: current.nodeId, relations } };
}

/** Local removal tracking shares the existing Canvas recovery and mutation
 * gate. It holds intent, not an effect grant or a native request receipt. */
export function prepareCanvasRemovalSubmission(
  draft: CanvasWorkspaceDraft, project: Project, id: string,
): { draft: CanvasWorkspaceDraft; submission: CanvasSubmission; change: Extract<Change, { type: "remove_canvas_node" }> } {
  const current = reconcileCanvasDraft(draft, project);
  if (canvasDraftHasWork(current)) throw new Error("Commit or review the retained Canvas draft before removing this object.");
  const node = canvasDraftNode(current, project);
  const submission: CanvasSubmission = { id, scope: current.scope, revision: project.revision,
    group: "removal", context: nodeContext(node), fields: {} };
  return { draft: { ...current, awaiting: submission }, submission,
    change: { type: "remove_canvas_node", scene_id: current.sceneId, node_id: current.nodeId } };
}

/** Canonical state observation only. Neither absence nor presence identifies
 * which service request committed, and neither permits an automatic retry. */
export function canvasRemovalObservation(draft: CanvasWorkspaceDraft, project: Project): "unconfirmed" | "present" | "absent" {
  const attempt = draft.awaiting;
  if (attempt?.group !== "removal" || draft.scope !== attempt.scope
    || draft.scope !== canvasDraftScope(project, draft.sceneId, draft.nodeId)
    || project.revision < attempt.revision) return "unconfirmed";
  const scene = project.scenes.find(item => item.id === draft.sceneId);
  if (!scene) return "unconfirmed";
  if (scene.nodes.some(node => node.id === draft.nodeId)) return "present";
  return project.revision > attempt.revision ? "absent" : "unconfirmed";
}

/** Confirm both directions of the browser-session pointer. A localStorage
 * recovery record without its writer pointer is recoverable only by explicit
 * selection after reload; it must not authorize a mutation automatically. */
export function writeCanvasSessionPointer(
  storage: Pick<Storage, "getItem" | "setItem">, key: string, writerId: string,
): void {
  storage.setItem(key, writerId);
  if (storage.getItem(key) !== writerId) throw new Error("Canvas recovery writer pointer was not confirmed.");
}

export const CANVAS_DRAFT_PREFIX = "motionwright.canvas-draft.v1:";
// A node may contain 100,000 UTF-8 bytes of valid text. Recovery also carries its
// base and an in-flight snapshot, so a small audio-sized copy would block edits
// to otherwise valid large text objects.
export const CANVAS_COPY_BYTES = 1024 * 1024;
export const CANVAS_COPY_LIMIT = 128;
export const CANVAS_TOTAL_BYTES = 4 * 1024 * 1024;
export type CanvasDraftStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface CanvasRecovery { schema: "motionwright.canvas-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: CanvasWorkspaceDraft | null }
export interface CanvasRecoveryEntry { key: string; raw: string; value: CanvasRecovery }
export interface CanvasUnreadableRecovery { key: string; raw: string | null; writerId: string }
export const canvasRecoveryKey = (scope: string, writerId: string) => CANVAS_DRAFT_PREFIX + JSON.stringify([scope, writerId]);
const bytes = (raw: string) => new TextEncoder().encode(raw).length;
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const safeInteger = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
function parsedScope(scope: string): [string, string, string, string, string] | null {
  try {
    const parts: unknown = JSON.parse(scope);
    return scope.length <= 2048 && Array.isArray(parts) && parts.length === 5
      && parts.every(part => typeof part === "string" && part.length > 0 && part.length <= 256)
      ? parts as [string, string, string, string, string] : null;
  } catch { return null; }
}
function cleanContext(value: unknown): CanvasDraftContext {
  if (!record(value) || typeof value.kind !== "string" || !value.kind || value.kind.length > 256
    || (value.parent_id !== null && (typeof value.parent_id !== "string" || !value.parent_id || value.parent_id.length > 256))
    || (value.coordinate_space !== "project_pixels" && value.coordinate_space !== "normalized" && value.coordinate_space !== "scene_local")) {
    throw new Error("The Canvas recovery copy has an invalid object context.");
  }
  return { kind: value.kind, parent_id: value.parent_id, coordinate_space: value.coordinate_space };
}
function identity(key: string): [string, string] | null {
  try {
    const pair: unknown = JSON.parse(key.slice(CANVAS_DRAFT_PREFIX.length));
    return Array.isArray(pair) && pair.length === 2 && typeof pair[0] === "string" && parsedScope(pair[0]) !== null
      && typeof pair[1] === "string" && pair[1].length > 0 && pair[1].length <= 128 ? [pair[0], pair[1]] : null;
  } catch { return null; }
}
function cleanRelationList(value: unknown): NodeRelation[] {
  if (!Array.isArray(value) || value.length > 128) throw new Error("The Canvas relation list is invalid.");
  const ids = new Set<string>(), relations: NodeRelation[] = [];
  for (const entry of value) {
    if (!record(entry) || typeof entry.id !== "string" || !entry.id || entry.id.length > 128
      || typeof entry.target_id !== "string" || !entry.target_id || entry.target_id.length > 256
      || typeof entry.kind !== "string" || !(CANVAS_RELATION_KINDS as readonly string[]).includes(entry.kind)
      || ids.has(entry.id)) throw new Error("A Canvas recovery relation is invalid.");
    ids.add(entry.id);
    relations.push({ id: entry.id, kind: entry.kind as NodeRelation["kind"], target_id: entry.target_id });
  }
  return relations;
}
function reparentExact(value: unknown, keys: readonly string[], label: string): asserts value is Record<string, unknown> {
  if (!record(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key)))
    throw new Error("The Canvas reparent " + label + " has unknown or missing fields.");
}
function reparentId(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && value.length > 0 && value.length <= 256);
}
function reparentSpace(value: unknown): value is CoordinateSpace {
  return value === "project_pixels" || value === "normalized" || value === "scene_local";
}
function cleanReparentPose(value: unknown): CanvasTransform {
  reparentExact(value, CANVAS_TRANSFORM_FIELDS, "pose snapshot");
  if (CANVAS_TRANSFORM_FIELDS.some(field => typeof value[field] !== "number"))
    throw new Error("The Canvas reparent pose snapshot must contain numbers.");
  const pose = Object.fromEntries(CANVAS_TRANSFORM_FIELDS.map(field => [field, value[field]])) as unknown as CanvasTransform;
  validateReparentPose(pose);
  return pose;
}
function cleanReparentFrame(value: unknown): CanvasReparentFrame | null {
  if (value === null) return null;
  reparentExact(value, ["id", "coordinate_space", "geometricMotion", "x", "y", "width", "height", "rotation_deg"], "parent frame snapshot");
  if (typeof value.id !== "string" || !value.id || value.id.length > 256 || !reparentSpace(value.coordinate_space)
    || typeof value.geometricMotion !== "boolean"
    || ["x", "y", "width", "height", "rotation_deg"].some(field => typeof value[field] !== "number" || !Number.isFinite(value[field]))
    || (value.width as number) < 0 || (value.height as number) < 0)
    throw new Error("The Canvas reparent parent frame snapshot is invalid.");
  return { id: value.id, coordinate_space: value.coordinate_space, geometricMotion: value.geometricMotion,
    x: value.x as number, y: value.y as number, width: value.width as number, height: value.height as number,
    rotation_deg: value.rotation_deg as number };
}
function cleanReparentSnapshot(value: unknown): CanvasReparentSnapshot {
  reparentExact(value, ["parentId", "child", "oldParent", "newParent"], "frame snapshot");
  if (!reparentId(value.parentId)) throw new Error("The Canvas reparent target identity is invalid.");
  const child = value.child;
  reparentExact(child, ["parent_id", "coordinate_space", "geometricMotion", "pose"], "child snapshot");
  if (!reparentId(child.parent_id) || !reparentSpace(child.coordinate_space) || typeof child.geometricMotion !== "boolean")
    throw new Error("The Canvas reparent child snapshot is invalid.");
  const oldParent = cleanReparentFrame(value.oldParent), newParent = cleanReparentFrame(value.newParent);
  if ((oldParent && oldParent.id !== child.parent_id) || (newParent && newParent.id !== value.parentId))
    throw new Error("The Canvas reparent snapshot does not identify the captured parents.");
  return { parentId: value.parentId, child: { parent_id: child.parent_id, coordinate_space: child.coordinate_space,
    geometricMotion: child.geometricMotion, pose: cleanReparentPose(child.pose) }, oldParent, newParent };
}
function cleanReparentIntent(value: unknown): CanvasReparentIntent {
  if (!record(value)) throw new Error("The Canvas reparent intent snapshot is invalid.");
  reparentExact(value, ["sequence", "snapshot", ...(Object.hasOwn(value, "preserve") ? ["preserve"] : [])], "intent snapshot");
  validateReparentPolicy(value.preserve);
  if (!safeInteger(value.sequence)) throw new Error("The Canvas reparent intent sequence is invalid.");
  return { ...(value.preserve === undefined ? {} : { preserve: value.preserve }), sequence: value.sequence,
    snapshot: cleanReparentSnapshot(value.snapshot) };
}
function cleanReparentUnchanged(value: unknown): CanvasReparentUnchanged {
  reparentExact(value, ["id", "name", "kind", "text", "coordinate_space", "style", "relations", "property_locks", "keyframes"], "unchanged node snapshot");
  if (typeof value.id !== "string" || !value.id || value.id.length > 256 || typeof value.name !== "string"
    || typeof value.kind !== "string" || !value.kind || !reparentSpace(value.coordinate_space)
    || (value.text !== null && (typeof value.text !== "string" || canvasFieldIssue("text", value.text))))
    throw new Error("The Canvas reparent unchanged node snapshot is invalid.");
  const style = value.style;
  reparentExact(style, CANVAS_STYLE_FIELDS, "unchanged style snapshot");
  for (const field of CANVAS_STYLE_FIELDS) {
    const item = style[field], optionalNumber = (optionalNumericStyle as readonly string[]).includes(field);
    if ((isStyleString(field) && item !== null && typeof item !== "string")
      || (optionalNumber && item !== null && typeof item !== "number")
      || (field === "stroke_width" && typeof item !== "number")
      || (field === "blend_mode" && typeof item !== "string")
      || canvasFieldIssue(field, item === null ? "" : String(item)))
      throw new Error("The Canvas reparent unchanged style snapshot is invalid.");
  }
  if (!Array.isArray(value.relations)) throw new Error("The Canvas reparent unchanged relation snapshot is invalid.");
  for (const item of value.relations) reparentExact(item, ["id", "kind", "target_id"], "unchanged relation snapshot");
  cleanRelationList(value.relations);
  const locks = value.property_locks, properties = ["position", "size", "rotation", "opacity", "text", "style", "parent", "order"];
  if (!Array.isArray(locks) || locks.some(lock => typeof lock !== "string" || !properties.includes(lock))
    || new Set(locks).size !== locks.length) throw new Error("The Canvas reparent unchanged lock snapshot is invalid.");
  if (!Array.isArray(value.keyframes)) throw new Error("The Canvas reparent unchanged motion snapshot is invalid.");
  for (const key of value.keyframes) {
    reparentExact(key, ["at", "property", "value", "interpolation"], "unchanged motion snapshot");
    reparentExact(key.at, ["num", "den"], "unchanged motion time");
    if (typeof key.at.num !== "string" || typeof key.at.den !== "string"
      || !/^[+-]?\d+$/.test(key.at.num) || !/^[+-]?\d+$/.test(key.at.den)
      || BigInt(key.at.num) < 0n || BigInt(key.at.den) <= 0n
      || typeof key.property !== "string" || !(CANVAS_TRANSFORM_FIELDS as readonly string[]).includes(key.property)
      || typeof key.value !== "number" || !Number.isFinite(key.value)
      || ((key.property === "width" || key.property === "height") && key.value < 0)
      || (key.property === "opacity" && (key.value < 0 || key.value > 1))
      || typeof key.interpolation !== "string" || !["hold", "linear", "ease_in_out", "ease_out_cubic"].includes(key.interpolation))
      throw new Error("The Canvas reparent unchanged motion snapshot is invalid.");
  }
  return unchangedReparentFields(value as unknown as CanvasReparentUnchanged);
}
function cleanReparentSubmission(value: unknown): CanvasReparentSubmission {
  if (!record(value)) throw new Error("The Canvas reparent submission snapshot is invalid.");
  reparentExact(value, ["sequence", "snapshot", "pose", "unchanged", ...(Object.hasOwn(value, "preserve") ? ["preserve"] : [])], "submission snapshot");
  const { pose, unchanged, ...rawIntent } = value, intent = cleanReparentIntent(rawIntent), cleanedPose = cleanReparentPose(pose);
  if ((intent.snapshot.child.parent_id !== null && !intent.snapshot.oldParent)
    || (intent.snapshot.parentId !== null && !intent.snapshot.newParent))
    throw new Error("The Canvas reparent submission snapshot is missing a participating frame.");
  const expected = reparentPose(intent.snapshot, intent.preserve);
  if (CANVAS_TRANSFORM_FIELDS.some(field => cleanedPose[field] !== expected[field]))
    throw new Error("The Canvas reparent pose does not match its frozen frame snapshot.");
  return { ...intent, pose: cleanedPose, unchanged: cleanReparentUnchanged(unchanged) };
}
export function decodeCanvasRecovery(raw: string, scope: string, writerId: string): CanvasRecovery {
  if (bytes(raw) > CANVAS_COPY_BYTES) throw new Error("This Canvas recovery copy exceeds 1 MiB.");
  const value: unknown = JSON.parse(raw), scopeParts = parsedScope(scope);
  if (!scopeParts || !record(value) || value.schema !== "motionwright.canvas-recovery/1" || value.scope !== scope || value.writerId !== writerId
    || !writerId || writerId.length > 128 || !safeInteger(value.updatedAt)) throw new Error("The Canvas recovery copy has an invalid identity or version.");
  let draft: CanvasWorkspaceDraft | null = null;
  if (value.draft !== null) {
    const saved = value.draft;
    if (!record(saved) || (saved.schema !== "motionwright.canvas-draft/1" && saved.schema !== "motionwright.canvas-draft/2"
        && saved.schema !== "motionwright.canvas-draft/3" && saved.schema !== "motionwright.canvas-draft/4"
        && saved.schema !== "motionwright.canvas-draft/5" && saved.schema !== "motionwright.canvas-draft/6") || saved.scope !== scope
      || saved.sceneId !== scopeParts[3] || saved.nodeId !== scopeParts[4] || !record(saved.fields)) throw new Error("The Canvas draft has an invalid object scope.");
    if (Object.hasOwn(saved, "reparent") && saved.schema !== "motionwright.canvas-draft/6")
      throw new Error("Legacy Canvas recovery cannot introduce a reparent frame snapshot.");
    const reparent = Object.hasOwn(saved, "reparent") ? cleanReparentIntent(saved.reparent) : undefined;
    const fields = {} as CanvasWorkspaceDraft["fields"];
    for (const field of CANVAS_DRAFT_FIELDS) {
      if ((saved.schema === "motionwright.canvas-draft/1" && isStyleField(field))
        || ((saved.schema === "motionwright.canvas-draft/1" || saved.schema === "motionwright.canvas-draft/2")
          && (CANVAS_HIERARCHY_FIELDS as readonly string[]).includes(field))
        || ((saved.schema !== "motionwright.canvas-draft/4" && saved.schema !== "motionwright.canvas-draft/5"
          && saved.schema !== "motionwright.canvas-draft/6") && relationField(field))) continue;
      const input = saved.fields[field];
      if (!record(input) || typeof input.raw !== "string" || typeof input.base !== "string" || !safeInteger(input.baseRevision)
        || !safeInteger(input.sequence) || (input.intent !== undefined && input.intent !== true)
        || canvasFieldIssue(field, input.base)) throw new Error("The Canvas recovery copy contains an invalid field.");
      fields[field] = { raw: input.raw, base: input.base, baseRevision: input.baseRevision, sequence: input.sequence,
        ...(input.intent ? { intent: true as const } : {}) };
    }
    let awaiting: CanvasSubmission | null = null;
    if (saved.awaiting !== null) {
      const sent = saved.awaiting;
      if (!record(sent) || typeof sent.id !== "string" || !sent.id || sent.id.length > 128 || sent.scope !== scope
        || !safeInteger(sent.revision) || (sent.group !== "text" && sent.group !== "transform" && sent.group !== "style"
          && sent.group !== "hierarchy" && sent.group !== "relations" && sent.group !== "removal") || !record(sent.fields)) throw new Error("The Canvas submission snapshot is unreadable.");
      if (sent.group === "removal" && ((saved.schema !== "motionwright.canvas-draft/5" && saved.schema !== "motionwright.canvas-draft/6")
        || Object.keys(sent.fields).length !== 0)) throw new Error("The Canvas removal snapshot is invalid.");
      awaiting = { id: sent.id, scope, revision: sent.revision, group: sent.group, context: cleanContext(sent.context), fields: {} };
      if (sent.group === "relations") {
        if ((sent.relationOperation !== "add" && sent.relationOperation !== "remove")
          || !Array.isArray(sent.relations) || sent.relations.length > 128) {
          throw new Error("The Canvas relation submission is invalid.");
        }
        awaiting.relationOperation = sent.relationOperation;
        awaiting.relations = cleanRelationList(sent.relations);
      }
      for (const field of sent.group === "removal" || (sent.group === "relations" && sent.relationOperation === "remove")
        ? [] : canvasGroupFields(sent.group)) {
        const input = sent.fields[field];
        if (!record(input) || typeof input.raw !== "string" || typeof input.value !== "string" || !safeInteger(input.sequence)
          || input.sequence > fields[field].sequence || canvasFieldIssue(field, input.value)
          || proposed(field, input.raw) !== input.value) throw new Error("The Canvas submission contains an invalid field snapshot.");
        awaiting.fields[field] = { raw: input.raw, value: input.value, sequence: input.sequence };
      }
      if (Object.hasOwn(sent, "reparent")) {
        if (saved.schema !== "motionwright.canvas-draft/6" || sent.group !== "hierarchy" || !reparent)
          throw new Error("The Canvas reparent snapshot is not part of a current hierarchy draft.");
        awaiting.reparent = cleanReparentSubmission(sent.reparent);
        if (awaiting.reparent.sequence > reparent.sequence
          || awaiting.reparent.snapshot.parentId !== (awaiting.fields.parent_id?.value || null)
          || awaiting.reparent.snapshot.child.parent_id !== awaiting.context.parent_id
          || awaiting.reparent.snapshot.child.coordinate_space !== awaiting.context.coordinate_space
          || awaiting.reparent.unchanged.id !== scopeParts[4] || awaiting.reparent.unchanged.kind !== awaiting.context.kind
          || awaiting.reparent.unchanged.coordinate_space !== awaiting.context.coordinate_space
          || awaiting.reparent.unchanged.keyframes.some(key => key.property !== "opacity") !== awaiting.reparent.snapshot.child.geometricMotion)
          throw new Error("The Canvas reparent snapshot does not match its submitted identity or context.");
      }
    }
    draft = { schema: saved.schema as CanvasWorkspaceDraft["schema"], scope,
      sceneId: scopeParts[3], nodeId: scopeParts[4], baseContext: cleanContext(saved.baseContext),
      relationsBase: saved.schema === "motionwright.canvas-draft/4" || saved.schema === "motionwright.canvas-draft/5" || saved.schema === "motionwright.canvas-draft/6"
        ? cleanRelationList(saved.relationsBase) : [], fields, awaiting, ...(reparent ? { reparent } : {}) };
  }
  return { schema: "motionwright.canvas-recovery/1", scope, writerId, updatedAt: value.updatedAt, draft };
}
export function readCanvasRecoveries(storage: CanvasDraftStorage, scope: string): {
  entries: CanvasRecoveryEntry[]; unreadable: CanvasUnreadableRecovery[]; limited: boolean;
} {
  const entries: CanvasRecoveryEntry[] = [], unreadable: CanvasUnreadableRecovery[] = [];
  let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(CANVAS_DRAFT_PREFIX)) continue;
    const pair = identity(key);
    if (pair && pair[0] !== scope) continue;
    if (entries.length + unreadable.length >= CANVAS_COPY_LIMIT) { limited = true; break; }
    let raw: string | null = null;
    try {
      raw = storage.getItem(key);
      if (raw === null) continue;
      if (!pair) throw new Error("Unidentified recovery copy");
      entries.push({ key, raw, value: decodeCanvasRecovery(raw, scope, pair[1]) });
    } catch { unreadable.push({ key, raw, writerId: pair?.[1] ?? "unidentified-" + index }); }
  }
  entries.sort((a, b) => b.value.updatedAt - a.value.updatedAt || a.key.localeCompare(b.key));
  return { entries, unreadable, limited };
}
/** Removal records remain inspectable after the node has disappeared, using
 * the same bounded, validated recovery format. Other project scopes are not
 * adopted and malformed copies remain untouched. */
export function readCanvasRemovalRecoveries(storage: CanvasDraftStorage, project: Project, sceneId: string): { entries: CanvasRecoveryEntry[]; limited: boolean } {
  const entries: CanvasRecoveryEntry[] = [];
  let inspected = 0, limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(CANVAS_DRAFT_PREFIX)) continue;
    if (inspected++ >= CANVAS_COPY_LIMIT) { limited = true; break; }
    const pair = identity(key), scope = pair ? parsedScope(pair[0]) : null;
    if (!pair || !scope || pair[0] !== canvasDraftScope(project, sceneId, scope[4])) continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    try {
      const value = decodeCanvasRecovery(raw, pair[0], pair[1]);
      if (value.draft?.awaiting?.group === "removal") entries.push({ key, raw, value });
    } catch { /* Unreadable originals remain available in recovery management. */ }
  }
  return { entries: entries.sort((a, b) => b.value.updatedAt - a.value.updatedAt || a.key.localeCompare(b.key)), limited };
}

export function writeCanvasRecovery(storage: CanvasDraftStorage, value: CanvasRecovery, expectedRaw: string | null): string {
  const key = canvasRecoveryKey(value.scope, value.writerId), previous = storage.getItem(key);
  if (previous !== expectedRaw) throw new Error("This Canvas recovery copy changed in another window. Its contents were kept.");
  const normalized = decodeCanvasRecovery(JSON.stringify(value), value.scope, value.writerId);
  const earlier = previous === null ? null : decodeCanvasRecovery(previous, value.scope, value.writerId);
  const unchangedHierarchyPending = normalized.draft?.awaiting?.group === "hierarchy" && earlier !== null
    && JSON.stringify(normalized.draft) === JSON.stringify(earlier.draft);
  let raw = JSON.stringify(normalized);
  if (previous !== null && earlier?.draft === null) {
    if (value.draft !== null) throw new Error("A clean Canvas checkpoint is immutable. Start a new copy for new input.");
    return previous;
  }
  const stored: { key: string; raw: string; value: CanvasRecovery | null; scope: string | null }[] = [];
  for (let index = 0; index < storage.length; index++) {
    const existing = storage.key(index);
    if (!existing?.startsWith(CANVAS_DRAFT_PREFIX)) continue;
    if (stored.length >= CANVAS_COPY_LIMIT) throw new Error("Canvas recovery is full (128 copies). Discard copies you no longer need; no input was removed.");
    const original = storage.getItem(existing);
    if (original === null) continue;
    const pair = identity(existing);
    let decoded: CanvasRecovery | null = null;
    try { if (pair) decoded = decodeCanvasRecovery(original, pair[0], pair[1]); } catch { /* Preserve unreadable bytes. */ }
    stored.push({ key: existing, raw: original, value: decoded, scope: pair?.[0] ?? null });
  }
  const latest = stored.reduce((at, item) => item.value?.scope === value.scope ? Math.max(at, item.value.updatedAt) : at, -1);
  value = { ...value, updatedAt: Math.max(value.updatedAt, latest + 1) };
  // A confirmation/fork of the same UNKNOWN snapshot retains the original
  // bytes and timestamp. Bounds, CAS and readback still run; the caller also
  // confirms its own session pointer before permitting an explicit fork.
  raw = unchangedHierarchyPending ? previous! : JSON.stringify(decodeCanvasRecovery(JSON.stringify(value), value.scope, value.writerId));
  // Only remove obsolete clean checkpoints after replacement bytes are
  // persisted and read back. Quota failures must preserve every prior copy.
  const removeClean = (item: typeof stored[number]) => {
    if (storage.getItem(item.key) === item.raw) storage.removeItem(item.key);
  };
  const fits = () => {
    let count = 1, total = bytes(key) + bytes(raw);
    for (let index = 0; index < storage.length; index++) {
      const existing = storage.key(index);
      if (!existing?.startsWith(CANVAS_DRAFT_PREFIX) || existing === key) continue;
      count++; total += bytes(existing) + bytes(storage.getItem(existing) ?? "");
    }
    return count <= CANVAS_COPY_LIMIT && total <= CANVAS_TOTAL_BYTES;
  };
  if (!fits()) throw new Error("Canvas recovery is full (128 copies / 4 MiB). Inputs and unreadable copies were kept; discard only copies you no longer need.");
  if (storage.getItem(key) !== previous) throw new Error("This Canvas recovery copy changed in another window. Its contents were kept.");
  if (raw !== previous) storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The latest Canvas recovery write could not be verified.");
  // A verified clean checkpoint may supersede older clean copies only for
  // the same object scope. Other projects and unreadable copies remain intact.
  if (value.draft === null) for (const item of stored) {
    if (item.key !== key && item.scope === value.scope && item.value?.draft === null) removeClean(item);
  }
  return raw;
}
export function discardCanvasRecovery(storage: CanvasDraftStorage, key: string, expectedRaw: string): void {
  if (!key.startsWith(CANVAS_DRAFT_PREFIX) || storage.getItem(key) !== expectedRaw) {
    throw new Error("This Canvas recovery copy changed after it was shown. Refresh the copies before discarding it.");
  }
  const pair = identity(key);
  let retained: CanvasRecovery | null = null;
  try { if (pair) retained = decodeCanvasRecovery(expectedRaw, pair[0], pair[1]); } catch { /* Explicit unreadable-copy housekeeping stays unchanged. */ }
  if (retained?.draft?.awaiting?.group === "hierarchy")
    throw new Error("The original unconfirmed Canvas hierarchy copy must be retained.");
  storage.removeItem(key);
}
