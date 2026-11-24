import type { CanvasNode, DeliverableProfile, Project, Scene } from "./types";
import { mergeComponentNodes, realizeProductHero } from "./creativeProduction";
import { realizeProceduralFieldForOutput } from "./proceduralField";
import { sampledSceneCamera } from "./cameraReframe";
import { previewTransform } from "./canvasMotion";
import { resolveCanvasRelations } from "./canvasFollow";
import { alignmentScale } from "./canvasAlignment";

/** Source-only counterpart of native expressive::responsive_scene. It never
 * changes the Project or authorizes a render. Film still admits every property. */
export async function reflowSceneForProfile(project: Project, scene: Scene, profile: DeliverableProfile): Promise<Scene> {
  if (![profile.width, profile.height].every(value => Number.isInteger(value) && value >= 320 && value <= 7680))
    throw new Error("Output layout dimensions are out of bounds.");
  const output = structuredClone(scene);
  if (profile.framing_strategy !== "replan" || (profile.width === 1920 && profile.height === 1080)) return output;
  const hero = project.production_design?.heroes.find(value => value.scene_id === scene.id);
  if (hero) {
    const incoming = await realizeProductHero(hero.id, hero.config, profile.width, profile.height);
    const sx = profile.width / 1920, sy = profile.height / 1080, scale = Math.min(sx, sy);
    for (const node of incoming) {
      const destinationY = node.y, centerX = node.x + node.width / 2, centerY = node.y + node.height / 2;
      node.width /= scale; node.height /= scale;
      node.x = centerX / sx - node.width / 2; node.y = centerY / sy - node.height / 2;
      if (node.style.font_size !== null) node.style.font_size /= scale;
      node.style.stroke_width /= scale;
      for (const key of node.keyframes) if (key.property === "y") key.value = node.y + (key.value - destinationY) / sy;
    }
    output.nodes = mergeComponentNodes(hero.baseline, output.nodes, incoming);
  }
  const field = project.production_design?.procedural_fields?.find(value =>
    value.scene_id === scene.id && value.config.distribution === "adaptive_grid");
  if (field) {
    const incoming = await realizeProceduralFieldForOutput(field.id, field.config, profile.width, profile.height);
    output.nodes = mergeComponentNodes(field.baseline, output.nodes, incoming);
  }
  return output;
}
export interface ProfileLayoutSample { nodes: CanvasNode[]; boundaryIssues: string[] }
/** Samples the authored geometry before applying Film's position/size projection.
 * This is an editorial layout, not font shaping, decoded media or native pixels. */
export function sampleProfileLayout(project: Project, scene: Scene, profile: DeliverableProfile, time: number): ProfileLayoutSample {
  const sampled = sampledSceneCamera(scene, project.production_design?.camera_reframes, time);
  if (!sampled.camera) throw new Error(sampled.issue);
  if (sampled.camera.zoom !== 1 || sampled.camera.rotation_deg !== 0)
    throw new Error("This output study requires the planar camera profile. Review other camera poses in Native.");
  if (![profile.width, profile.height].every(value => Number.isInteger(value) && value >= 320 && value <= 7680))
    throw new Error("Output layout dimensions are out of bounds.");
  const alignment = resolveCanvasRelations(scene.nodes, alignmentScale(profile.width, profile.height, profile.framing_strategy), false,
    {duration:scene.duration,fps:profile.frame_rate,continuous:!scene.beats.length});
  if (alignment.issue) throw new Error("Output layout relation is unresolved: " + alignment.issue);
  const replan = profile.framing_strategy === "replan";
  if (!replan && profile.framing_strategy !== "crop") throw new Error("Unknown output framing strategy.");
  const scaleX = profile.width / 1920, scaleY = profile.height / 1080;
  const sx = replan ? scaleX : Math.max(scaleX, scaleY), sy = replan ? scaleY : sx;
  const scale = replan ? Math.min(scaleX, scaleY) : sx;
  const offsetX = (replan ? 0 : (profile.width - 1920 * scale) / 2) + (960 - sampled.camera.center_x) * sx;
  const offsetY = (replan ? 0 : (profile.height - 1080 * scale) / 2) + (540 - sampled.camera.center_y) * sy;
  const boundaryIssues: string[] = [];
  const nodes = scene.nodes.map(node => {
    const track = alignment.tracks.get(node.id);
    const pose = { ...previewTransform(track ? { ...node, keyframes: track } : node, time), ...alignment.positions.get(node.id) };
    const width = pose.width * scale, height = pose.height * scale;
    const projected: CanvasNode = { ...structuredClone(node), ...pose,
      x: (pose.x + pose.width / 2) * sx + offsetX - width / 2,
      y: (pose.y + pose.height / 2) * sy + offsetY - height / 2, width, height,
      style: { ...node.style, font_size: node.style.font_size === null ? null : node.style.font_size * scale,
        stroke_width: node.style.stroke_width * scale }, keyframes: [], relations: [] };
    if (replan && !["group", "clip_group"].includes(node.kind) && pose.opacity > 0) {
      const radians = pose.rotation_deg * Math.PI / 180;
      const halfX = (Math.abs(width * Math.cos(radians)) + Math.abs(height * Math.sin(radians))) / 2;
      const halfY = (Math.abs(width * Math.sin(radians)) + Math.abs(height * Math.cos(radians))) / 2;
      const x = projected.x + width / 2, y = projected.y + height / 2;
      const mx = profile.width * sampled.camera.safe_margin, my = profile.height * sampled.camera.safe_margin;
      if (x - halfX < mx || y - halfY < my || x + halfX > profile.width - mx || y + halfY > profile.height - my)
        boundaryIssues.push(node.name + ": sampled geometry crosses the output safe area.");
    }
    return projected;
  });
  return { nodes, boundaryIssues };
}
