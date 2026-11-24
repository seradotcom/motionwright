import type { ScopedCanvasEdit } from "./creativeProduction";
import { isStaticNativeGroup, staticNativeGroupAncestors } from "./nativeGroupHierarchy";
import type { CanvasNode, CanvasTransform, Scene } from "./types";

export const ARRANGEMENT_ACTIONS = {
  align_left: "Align left", align_center_x: "Align horizontal centers", align_right: "Align right",
  align_top: "Align top", align_center_y: "Align vertical centers", align_bottom: "Align bottom",
  gaps_x: "Distribute horizontal gaps", gaps_y: "Distribute vertical gaps",
  centers_x: "Distribute horizontal centers", centers_y: "Distribute vertical centers",
} as const;
export type ArrangementAction = keyof typeof ARRANGEMENT_ACTIONS;
/** Selection preferences only. Staging captures the current authoring revision;
 * this structure contains neither geometry, a prepared diff nor authority. */
export interface ArrangementSelection { action: ArrangementAction; nodeIds: string[] }
export const emptyArrangementSelection = (): ArrangementSelection => ({ action: "align_left", nodeIds: [] });
export function validArrangementSelection(value: unknown): value is ArrangementSelection {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const raw = value as Record<string, unknown>;
  return typeof raw.action === "string" && Object.hasOwn(ARRANGEMENT_ACTIONS, raw.action) &&
    Array.isArray(raw.nodeIds) && raw.nodeIds.length <= 128 && raw.nodeIds.every(id => typeof id === "string" && id.length <= 256) &&
    new Set(raw.nodeIds).size === raw.nodeIds.length;
}
export function arrangementNodeIssue(node: CanvasNode, scene: Scene): string | null {
  if (!["text", "shape", "rectangle", "circle", "group", "clip_group"].includes(node.kind))
    return "This object's editable frame is not admitted for Canvas arrangement.";
  if (node.coordinate_space !== "project_pixels" || !staticNativeGroupAncestors(node, scene))
    return "Arrange frames in one valid project-pixel parent scope.";
  if (["group", "clip_group"].includes(node.kind) && !isStaticNativeGroup(node))
    return "This group needs an admitted static frame before arrangement.";
  if (node.relations.some(relation => relation.kind !== "follow"))
    return "This frame is controlled by an alignment or attachment relation. Review that relation first.";
  if (![node.x, node.y, node.width, node.height, node.rotation_deg].every(Number.isFinite) || node.width < 0 || node.height < 0)
    return "The object has invalid frame geometry.";
  return null;
}
interface Bounds { left: number; right: number; top: number; bottom: number; cx: number; cy: number }
function bounds(node: CanvasNode): Bounds {
  const angle = ((node.rotation_deg % 360) + 360) % 360;
  const quarter = angle % 90 === 0;
  const c = quarter ? angle % 180 === 0 ? 1 : 0 : Math.abs(Math.cos(angle * Math.PI / 180));
  const s = quarter ? angle % 180 === 0 ? 0 : 1 : Math.abs(Math.sin(angle * Math.PI / 180));
  const halfWidth = (node.width * c + node.height * s) / 2;
  const halfHeight = (node.width * s + node.height * c) / 2;
  const cx = node.x + node.width / 2, cy = node.y + node.height / 2;
  const result = { left: cx - halfWidth, right: cx + halfWidth, top: cy - halfHeight, bottom: cy + halfHeight, cx, cy };
  if (!Object.values(result).every(Number.isFinite)) throw new Error("The selected frame bounds exceed finite Canvas coordinates.");
  return result;
}
const transform = (node: CanvasNode): CanvasTransform => ({
  x: node.x, y: node.y, width: node.width, height: node.height, rotation_deg: node.rotation_deg, opacity: node.opacity,
});
type ArrangementEdit = Extract<ScopedCanvasEdit, { kind: "transform" | "motion" }>;
export function arrangeCanvasFrames(scene: Scene, selection: ArrangementSelection): { edits: ArrangementEdit[]; rationale: string } {
  if (!validArrangementSelection(selection)) throw new Error("Choose a supported bounded arrangement selection.");
  const distribution = selection.action.startsWith("gaps_") || selection.action.startsWith("centers_");
  if (selection.nodeIds.length < (distribution ? 3 : 2)) throw new Error(distribution ? "Select at least three frames to distribute." : "Select at least two frames to align.");
  const selected = selection.nodeIds.map(id => {
    const node = scene.nodes.find(item => item.id === id);
    if (!node) throw new Error("A selected object is no longer in this scene. Review the selection.");
    const issue = arrangementNodeIssue(node, scene);
    if (issue) throw new Error(node.name + ": " + issue);
    return { node, box: bounds(node) };
  });
  if (new Set(selected.map(item => item.node.parent_id)).size !== 1)
    throw new Error("Choose sibling frames with the same parent. Reparenting remains an explicit local/world decision.");
  const left = Math.min(...selected.map(item => item.box.left)), right = Math.max(...selected.map(item => item.box.right));
  const top = Math.min(...selected.map(item => item.box.top)), bottom = Math.max(...selected.map(item => item.box.bottom));
  const delta = new Map<string, { x: number; y: number }>();
  if (distribution) {
    const horizontal = selection.action.endsWith("_x"), centers = selection.action.startsWith("centers_");
    const start = horizontal ? "left" : "top", end = horizontal ? "right" : "bottom", center = horizontal ? "cx" : "cy";
    const ordered = [...selected].sort((a, b) => a.box[centers ? center : start] - b.box[centers ? center : start] ||
      scene.nodes.indexOf(a.node) - scene.nodes.indexOf(b.node));
    const low = centers ? ordered[0].box[center] : horizontal ? left : top;
    const high = centers ? ordered.at(-1)!.box[center] : horizontal ? right : bottom;
    const occupied = centers ? 0 : ordered.reduce((sum, item) => sum + item.box[end] - item.box[start], 0);
    const gap = (high - low - occupied) / (ordered.length - 1);
    if (!Number.isFinite(gap) || gap < 0) throw new Error("These frames cannot fit with nonnegative equal gaps. Widen the span or resize the frames before distributing.");
    let cursor = low;
    ordered.forEach((item, index) => {
      const position = centers ? index === ordered.length - 1 ? high : low + index * gap : cursor;
      const amount = position - item.box[centers ? center : start];
      delta.set(item.node.id, { x: horizontal ? amount : 0, y: horizontal ? 0 : amount });
      if (!centers) cursor += item.box[end] - item.box[start] + gap;
    });
  } else {
    for (const { node, box } of selected) {
      let x = 0, y = 0;
      switch (selection.action) {
        case "align_left": x = left - box.left; break;
        case "align_center_x": x = (left + right) / 2 - box.cx; break;
        case "align_right": x = right - box.right; break;
        case "align_top": y = top - box.top; break;
        case "align_center_y": y = (top + bottom) / 2 - box.cy; break;
        case "align_bottom": y = bottom - box.bottom; break;
      }
      delta.set(node.id, { x, y });
    }
  }
  const edits: ArrangementEdit[] = [];
  for (const { node } of selected) {
    const move = delta.get(node.id)!;
    if (!move.x && !move.y) continue;
    if (node.property_locks.includes("position")) throw new Error(node.name + ": position is locked.");
    const next = { ...transform(node), x: node.x + move.x, y: node.y + move.y };
    if (!Number.isFinite(next.x) || !Number.isFinite(next.y)) throw new Error("Arrangement exceeds finite Canvas coordinates.");
    edits.push({ kind: "transform", node_id: node.id, transform: next });
    const changesTrack = node.keyframes.some(key => key.property === "x" && move.x !== 0 || key.property === "y" && move.y !== 0);
    if (changesTrack) {
      const keyframes = node.keyframes.map(key => ({ ...key, at: { ...key.at },
        value: key.value + (key.property === "x" ? move.x : key.property === "y" ? move.y : 0) }));
      if (keyframes.some(key => !Number.isFinite(key.value))) throw new Error("Arrangement exceeds finite motion values.");
      edits.push({ kind: "motion", node_id: node.id, keyframes });
    }
  }
  if (!edits.length) throw new Error("The selected frames already have this arrangement.");
  if (edits.length > 128) throw new Error("This arrangement exceeds the 128-edit proposal budget. Choose fewer frames.");
  return { edits, rationale: ARRANGEMENT_ACTIONS[selection.action] + " for " + selected.length +
    " sibling object frames. Translate authored motion with each moved frame; preserve timing, curves, style, hierarchy and layer order." };
}
