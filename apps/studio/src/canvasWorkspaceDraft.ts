import type { CanvasNode, CanvasTransform, Change, NodeProperty, NodeStyle, NodeRelation, Project } from "./types";

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
  id: string; scope: string; revision: number; group: CanvasDraftGroup;
  context: CanvasDraftContext;
  fields: Partial<Record<CanvasDraftField, { raw: string; sequence: number; value: string }>>;
  relations?: NodeRelation[]; relationOperation?: "add" | "remove";
}
export interface CanvasWorkspaceDraft {
  schema: "motionwright.canvas-draft/1" | "motionwright.canvas-draft/2" | "motionwright.canvas-draft/3" | "motionwright.canvas-draft/4"; scope: string; sceneId: string; nodeId: string;
  baseContext: CanvasDraftContext;
  relationsBase: NodeRelation[];
  fields: Record<CanvasDraftField, CanvasFieldDraft>; awaiting: CanvasSubmission | null;
}
export interface CanvasDraftConflict { field: CanvasDraftField; before: string; current: string; proposed: string; futureRevision: boolean }
const decimal = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
export const canvasDraftScope = (project: Project, sceneId: string, nodeId: string) =>
  JSON.stringify([project.id, project.generation, project.active_branch, sceneId, nodeId]);
export const canvasGroupFields = (group: CanvasDraftGroup): readonly CanvasDraftField[] =>
  group === "text" ? ["text"] : group === "style" ? CANVAS_STYLE_FIELDS
    : group === "hierarchy" ? CANVAS_HIERARCHY_FIELDS : group === "relations" ? CANVAS_RELATION_FIELDS : CANVAS_TRANSFORM_FIELDS;
export const canvasFieldHasInput = (field: CanvasFieldDraft) => field.intent === true || field.raw !== field.base;
export const canvasDraftHasWork = (draft: CanvasWorkspaceDraft) => draft.awaiting !== null
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
const groupHasInput = (draft: CanvasWorkspaceDraft, group: CanvasDraftGroup) => canvasGroupFields(group).some(field => canvasFieldHasInput(draft.fields[field]));
export function canvasDraftNode(draft: CanvasWorkspaceDraft, project: Project): CanvasNode {
  if (draft.scope !== canvasDraftScope(project, draft.sceneId, draft.nodeId)) throw new Error("This Canvas draft belongs to another project, generation, branch or object.");
  const node = project.scenes.find(scene => scene.id === draft.sceneId)?.nodes.find(item => item.id === draft.nodeId);
  if (!node) throw new Error("This Canvas object is no longer in the saved project. Its draft has been kept.");
  return node;
}
export function createCanvasDraft(project: Project, sceneId: string, nodeId: string): CanvasWorkspaceDraft {
  const draft: CanvasWorkspaceDraft = { schema: "motionwright.canvas-draft/4", scope: canvasDraftScope(project, sceneId, nodeId),
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
export function editCanvasField(draft: CanvasWorkspaceDraft, field: CanvasDraftField, raw: string): CanvasWorkspaceDraft {
  const current = draft.fields[field];
  return { ...draft, fields: { ...draft.fields, [field]: { ...current, raw, sequence: current.sequence + 1,
    ...(draft.awaiting?.fields[field] ? { intent: true as const } : {}) } } };
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
  return (["text", "transform", "style", "hierarchy", "relations"] as const).filter(group => groupHasInput(draft, group) && !contextMatches(draft.baseContext, current, group));
}
export function reviewCanvasContext(draft: CanvasWorkspaceDraft, project: Project): CanvasWorkspaceDraft {
  return reconcileCanvasDraft({ ...draft, baseContext: nodeContext(canvasDraftNode(draft, project)) }, project);
}
export function canvasSubmissionMatches(project: Project, draft: CanvasWorkspaceDraft, attempt: CanvasSubmission): boolean {
  if (draft.scope !== attempt.scope || project.revision !== attempt.revision + 1) return false;
  let node: CanvasNode;
  try { node = canvasDraftNode(draft, project); } catch { return false; }
  // Reparent is the only group allowed to change parent context; the exact
  // committed parent and Z values are still checked below.
  return (attempt.group === "hierarchy"
    ? attempt.context.kind === node.kind && attempt.context.coordinate_space === node.coordinate_space
    : contextMatches(attempt.context, nodeContext(node), attempt.group))
    && (attempt.group === "relations"
      ? Boolean(attempt.relations && relationListEqual(node.relations, attempt.relations))
      : canvasGroupFields(attempt.group).every(field => canonical(node, field) === attempt.fields[field]?.value));
}
export function reconcileCanvasDraft(draft: CanvasWorkspaceDraft, project: Project): CanvasWorkspaceDraft {
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
  if (draft.schema !== "motionwright.canvas-draft/4") for (const field of CANVAS_RELATION_FIELDS) {
    const value = canonical(node, field);
    fields[field] = { raw: value, base: value, baseRevision: project.revision, sequence: 0 };
  }
  const earlierRelations = draft.schema === "motionwright.canvas-draft/4"
    ? draft.relationsBase : structuredClone(node.relations);
  let awaiting = draft.awaiting;
  const ownRelationAcknowledged = Boolean(awaiting && awaiting.group === "relations"
    && canvasSubmissionMatches(project, draft, awaiting));
  const ownHierarchyAcknowledged = Boolean(awaiting && awaiting.group === "hierarchy"
    && canvasSubmissionMatches(project, draft, awaiting));
  if (awaiting && canvasSubmissionMatches(project, draft, awaiting)) {
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
  } else if (awaiting && project.revision > awaiting.revision && awaiting.group !== "relations") {
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
  const next: CanvasWorkspaceDraft = { ...draft, schema: "motionwright.canvas-draft/4", fields, awaiting,
    relationsBase: ownRelationAcknowledged || (!awaiting && !groupHasInput({ ...draft, fields, awaiting } as CanvasWorkspaceDraft, "relations"))
      ? structuredClone(node.relations) : earlierRelations }, currentContext = nodeContext(node);
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
  return draft.awaiting?.id === id ? { ...draft, awaiting: null } : draft;
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
  const change: Extract<Change, { type: "update_canvas_text" | "transform_canvas_node" | "update_canvas_style" | "reparent_canvas_node" }> =
    group === "text"
      ? { type: "update_canvas_text", scene_id: current.sceneId, node_id: current.nodeId, text: submission.fields.text!.value || null }
      : group === "style"
        ? { type: "update_canvas_style", scene_id: current.sceneId, node_id: current.nodeId,
            style: styleFromSubmission(node, submission.fields) }
        : group === "hierarchy"
          ? { type: "reparent_canvas_node", scene_id: current.sceneId, node_id: current.nodeId,
              parent_id: submission.fields.parent_id!.value || null, z_index: Number(submission.fields.z_index!.value) }
          : { type: "transform_canvas_node", scene_id: current.sceneId, node_id: current.nodeId,
              transform: Object.fromEntries(CANVAS_TRANSFORM_FIELDS.map(field => [field, Number(submission.fields[field]!.value)])) as unknown as CanvasTransform };
  return { draft: { ...current, fields, awaiting: submission }, submission, change };
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
export function decodeCanvasRecovery(raw: string, scope: string, writerId: string): CanvasRecovery {
  if (bytes(raw) > CANVAS_COPY_BYTES) throw new Error("This Canvas recovery copy exceeds 1 MiB.");
  const value: unknown = JSON.parse(raw), scopeParts = parsedScope(scope);
  if (!scopeParts || !record(value) || value.schema !== "motionwright.canvas-recovery/1" || value.scope !== scope || value.writerId !== writerId
    || !writerId || writerId.length > 128 || !safeInteger(value.updatedAt)) throw new Error("The Canvas recovery copy has an invalid identity or version.");
  let draft: CanvasWorkspaceDraft | null = null;
  if (value.draft !== null) {
    const saved = value.draft;
    if (!record(saved) || (saved.schema !== "motionwright.canvas-draft/1" && saved.schema !== "motionwright.canvas-draft/2"
        && saved.schema !== "motionwright.canvas-draft/3" && saved.schema !== "motionwright.canvas-draft/4") || saved.scope !== scope
      || saved.sceneId !== scopeParts[3] || saved.nodeId !== scopeParts[4] || !record(saved.fields)) throw new Error("The Canvas draft has an invalid object scope.");
    const fields = {} as CanvasWorkspaceDraft["fields"];
    for (const field of CANVAS_DRAFT_FIELDS) {
      if ((saved.schema === "motionwright.canvas-draft/1" && isStyleField(field))
        || ((saved.schema === "motionwright.canvas-draft/1" || saved.schema === "motionwright.canvas-draft/2")
          && (CANVAS_HIERARCHY_FIELDS as readonly string[]).includes(field))
        || (saved.schema !== "motionwright.canvas-draft/4" && relationField(field))) continue;
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
          && sent.group !== "hierarchy" && sent.group !== "relations") || !record(sent.fields)) throw new Error("The Canvas submission snapshot is unreadable.");
      awaiting = { id: sent.id, scope, revision: sent.revision, group: sent.group, context: cleanContext(sent.context), fields: {} };
      if (sent.group === "relations") {
        if ((sent.relationOperation !== "add" && sent.relationOperation !== "remove")
          || !Array.isArray(sent.relations) || sent.relations.length > 128) {
          throw new Error("The Canvas relation submission is invalid.");
        }
        awaiting.relationOperation = sent.relationOperation;
        awaiting.relations = cleanRelationList(sent.relations);
      }
      for (const field of sent.group === "relations" && sent.relationOperation === "remove"
        ? [] : canvasGroupFields(sent.group)) {
        const input = sent.fields[field];
        if (!record(input) || typeof input.raw !== "string" || typeof input.value !== "string" || !safeInteger(input.sequence)
          || input.sequence > fields[field].sequence || canvasFieldIssue(field, input.value)
          || proposed(field, input.raw) !== input.value) throw new Error("The Canvas submission contains an invalid field snapshot.");
        awaiting.fields[field] = { raw: input.raw, value: input.value, sequence: input.sequence };
      }
    }
    draft = { schema: saved.schema as CanvasWorkspaceDraft["schema"], scope,
      sceneId: scopeParts[3], nodeId: scopeParts[4], baseContext: cleanContext(saved.baseContext),
      relationsBase: saved.schema === "motionwright.canvas-draft/4"
        ? cleanRelationList(saved.relationsBase) : [], fields, awaiting };
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
export function writeCanvasRecovery(storage: CanvasDraftStorage, value: CanvasRecovery, expectedRaw: string | null): string {
  const key = canvasRecoveryKey(value.scope, value.writerId), previous = storage.getItem(key);
  if (previous !== expectedRaw) throw new Error("This Canvas recovery copy changed in another window. Its contents were kept.");
  let raw = JSON.stringify(decodeCanvasRecovery(JSON.stringify(value), value.scope, value.writerId));
  if (previous !== null && decodeCanvasRecovery(previous, value.scope, value.writerId).draft === null) {
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
  raw = JSON.stringify(decodeCanvasRecovery(JSON.stringify(value), value.scope, value.writerId));
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
  storage.setItem(key, raw);
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
  storage.removeItem(key);
}
