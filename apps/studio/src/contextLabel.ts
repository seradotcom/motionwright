import type { CanvasNode, DeliverableProfile, Scene } from "./types";
import { resolveCanvasRelations } from "./canvasFollow";

/** Assemble an ordinary editable Canvas label. Relations, source text and
 * identity travel through the existing AddCanvasNode/CAS/recovery route. */
export function contextLabelNode(scene: Scene, target: CanvasNode | null,
  source: CanvasNode, profile: DeliverableProfile | null): CanvasNode {
  if (!target || !scene.nodes.some(node => node.id === target.id))
    throw new Error("Select the source object for this context label.");
  if (scene.renderer !== "motion-canvas")
    throw new Error("Context labels require a Motion Canvas scene.");
  if (!["text", "shape", "rectangle", "circle"].includes(target.kind) || target.parent_id !== null ||
    scene.nodes.some(node => node.parent_id === target.id))
    throw new Error("Choose an independent planar text or shape as the label source.");
  const animated = target.keyframes.length > 0 || target.relations.some(relation => relation.kind === "follow");
  if (animated && !profile) throw new Error("Choose an output profile to resolve the label's exact motion clock.");
  const label: CanvasNode = { ...structuredClone(source), name: target.name + " · label",
    kind: "text", parent_id: null, text: target.name,
    width: 320, height: 56, rotation_deg: 0, opacity: 1, keyframes: [],
    style: {...source.style, font_size: 32, font_weight: 600, line_height: 1.2},
    x: target.x + (target.width - 320) / 2, y: target.y + target.height - 56,
    relations: (animated ? ["follow"] as const : ["align_center_x", "align_bottom"] as const)
      .map(kind => ({ id: crypto.randomUUID(), target_id: target.id, kind })),
  };
  const result = resolveCanvasRelations([...scene.nodes, label], undefined, false,
    { duration: scene.duration, fps: profile?.frame_rate, continuous: true });
  if (result.issue) throw new Error("Context label: " + result.issue);
  return label;
}
