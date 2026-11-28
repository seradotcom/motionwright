/** Read-only authored cut review. The Native Film guard independently
 * admits the exact pinned renderer subset; this is not raster evidence. */
import type { CanvasNode, DeliverableProfile, Project, Scene, SceneBoundary, SceneBoundaryAppearance, SceneBoundaryEndpoint } from "./types";
import { seconds } from "./types";
import { nativeStaticShapeRotation, previewTransform } from "./canvasMotion";
import { sameValue } from "./creativeProduction";

export interface AppearanceReview {
  status: "matching_pose" | "loss" | "unsupported";
  detail: string;
  displacement?: number;
}

function cutPose(project: Project, scene: Scene, node: CanvasNode, profile: DeliverableProfile, entering: boolean) {
  const pose = previewTransform(node, entering ? 0 : seconds(scene.duration));
  const reframe = project.production_design?.camera_reframes?.find(item => item.scene_id === scene.id);
  const cx = entering && reframe ? reframe.start_center_x : scene.camera.center_x;
  const cy = entering && reframe ? reframe.start_center_y : scene.camera.center_y;
  let sx = profile.width / 1920, sy = profile.height / 1080;
  const scale = profile.framing_strategy === "crop" ? Math.max(sx, sy) : Math.min(sx, sy);
  if (profile.framing_strategy === "crop") sx = sy = scale;
  return {
    x: (node.x + node.width / 2 - cx) * sx + (pose.x - node.x) * sx + (pose.width - node.width) * scale / 2,
    y: (node.y + node.height / 2 - cy) * sy + (pose.y - node.y) * sy + (pose.height - node.height) * scale / 2,
    width: pose.width * scale, height: pose.height * scale, rotation: pose.rotation_deg, opacity: pose.opacity,
  };
}

export function reviewBoundaryAppearance(project: Project, scene: Scene, profile: DeliverableProfile,
  predecessor: SceneBoundary, entry: SceneBoundaryEndpoint, appearance: SceneBoundaryAppearance): AppearanceReview {
  const loss = (detail: string): AppearanceReview => ({ status: "loss", detail });
  const unsupported = (detail: string): AppearanceReview => ({ status: "unsupported", detail });
  if (predecessor.definition.kind !== "explicit") return unsupported("Bind an explicit predecessor first.");
  if (predecessor.definition.source.deliverable_profile_id !== profile.id) return loss("Predecessor belongs to another profile.");
  const previous = project.scenes.find(item => item.id === predecessor.scene_id);
  const from = previous?.nodes.find(node => node.id === appearance.predecessor_node_id);
  const to = scene.nodes.find(node => node.id === appearance.successor_node_id);
  if (!previous || !from || !to) return loss("A mapped saved object is missing.");
  const ids = profile.included_scene_ids.length ? profile.included_scene_ids : project.scenes.map(item => item.id);
  if (!ids.some((id, index) => id === previous.id && ids[index + 1] === scene.id)) return loss("The mapped shots are not adjacent in this delivery.");
  const exit = predecessor.definition.exit;
  if (!sameValue(exit.nodes.find(node => node.id === from.id), from) ||
      !sameValue(entry.nodes.find(node => node.id === to.id), to) ||
      !sameValue(exit.camera, previous.camera) || !sameValue(entry.camera, scene.camera))
    return loss("Mapped snapshots differ from saved objects or camera. Copy current source.");
  for (const [shot, node] of [[previous, from], [scene, to]] as const) {
    if (shot.renderer !== "motion-canvas" || shot.beats.length || !["shape", "rectangle", "circle"].includes(node.kind) ||
        node.parent_id || node.relations.length || shot.nodes.some(item => item.parent_id === node.id) ||
        node.coordinate_space !== "project_pixels" || node.style.blend_mode !== "normal" ||
        node.opacity !== 1 || (node.rotation_deg !== 0 && !nativeStaticShapeRotation(node)) || shot.camera.zoom !== 1 || shot.camera.rotation_deg !== 0)
      return unsupported("Only independent planar Motion Canvas shapes have a bounded cut projection.");
    if (project.production_design?.heroes.some(hero => hero.scene_id === shot.id) &&
        profile.framing_strategy === "replan" && (profile.width !== 1920 || profile.height !== 1080))
      return unsupported("Responsive component layout requires native source admission.");
  }
  const rectangle = (node: CanvasNode) => ["shape", "rectangle"].includes(node.kind);
  if (!(from.kind === to.kind || rectangle(from) && rectangle(to)) || !sameValue(from.style, to.style) || from.text !== to.text)
    return loss("The mapped appearance changed.");
  const a = cutPose(project, previous, from, profile, false), b = cutPose(project, scene, to, profile, true);
  const displacement = Math.hypot(a.x - b.x, a.y - b.y);
  const equal = (a: number, b: number) => Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= 1e-6;
  if (a.opacity !== 1 || b.opacity !== 1 || !equal(a.x, b.x) || !equal(a.y, b.y) ||
      !equal(a.width, b.width) || !equal(a.height, b.height) || !equal(a.rotation, b.rotation))
    return { status: "loss", displacement, detail: "Mapped object jumps, changes geometry or is invisible at the authored cut. Edit Canvas motion." };
  return { status: "matching_pose", displacement, detail: "Authored boundary pose matches. Native source admission and rendered evidence are still required." };
}
