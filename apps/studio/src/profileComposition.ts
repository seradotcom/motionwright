import type { CanvasNode, DeliverableProfile, Project, Scene } from "./types";
import { heroNodeId, mergeComponentNodes, realizeProductHero } from "./creativeProduction";
import { realizeProceduralFieldForOutput } from "./proceduralField";
import { sampledSceneCamera } from "./cameraReframe";
import { previewTransform } from "./canvasMotion";
import { resolveCanvasRelations } from "./canvasFollow";
import { alignmentScale } from "./canvasAlignment";
import { nativeGroupOrderIssue } from "./nativeGroupHierarchy";
import { deliveryTextSourceConflict, protectedDeliveryText } from "./deliveryTextSources";

/** Additional line breaks follow actual retained box/font geometry, never
 * resize the type or change an authored character. Native glyphs are unmeasured. */
function fitVariantHeroText(node: CanvasNode, layout: string | undefined, headline: boolean): string {
  const text = node.text ?? "", font = node.style.font_size ?? 0;
  const advance = font * (node.style.line_height ?? 1.12);
  const fail = () => new Error("Delivery Hero " + node.name + " copy exceeds its responsive text budget; author a shorter variant or intentional line break.");
  if (![font, advance, node.width, node.height].every(value => Number.isFinite(value) && value > 0))
    throw fail();
  const roleLimit = headline ? layout === "editorial_quote" ? 4 : 3
    : layout === "typography_focus" || layout === "editorial_quote" ? 3 : 4;
  const lines: string[] = [];
  for (const paragraph of text.split("\n")) {
    let line = "";
    for (const word of paragraph.split(" ")) {
      const count = Array.from(word).length;
      if (!count || count * font * .56 > node.width * .96) throw fail();
      if (line && (Array.from(line).length + 1 + count) * font * .56 > node.width * .96) {
        lines.push(line); line = "";
      }
      line += (line ? " " : "") + word;
    }
    lines.push(line);
  }
  if (!lines.length || lines.length > roleLimit
      || advance * (lines.length - 1) + font * 1.4 > node.height) throw fail();
  const fitted = lines.join("\n");
  if (text.replaceAll("\n"," ") !== fitted.replaceAll("\n"," ")) throw fail();
  return fitted;
}

/** Source-only counterpart of native expressive::responsive_scene. It never
 * changes the Project or authorizes a render. Film still admits every property. */
export async function reflowSceneForProfile(project: Project, scene: Scene, profile: DeliverableProfile): Promise<Scene> {
  if (![profile.width, profile.height].every(value => Number.isInteger(value) && value >= 320 && value <= 7680))
    throw new Error("Output layout dimensions are out of bounds.");
  const sourceConflict=deliveryTextSourceConflict(project,profile,scene);
  if(sourceConflict) throw new Error(sourceConflict);
  const output = structuredClone(scene);
  const replan = profile.framing_strategy === "replan";
  const resized = replan && (profile.width !== 1920 || profile.height !== 1080);
  const wrappedOverrides = new Map<string,{copy:string;layout:string|undefined;headline:boolean}>();
  const hero = replan ? project.production_design?.heroes.find(value => value.scene_id === scene.id) : undefined;
  if (hero) {
    const config = structuredClone(hero.config), selected = new Set<string>();
    for (const role of ["headline","body"] as const) {
      const id=await heroNodeId(hero.id,role), retained=protectedDeliveryText(project,scene,id);
      if(retained) config[role]=retained.value;
      else if (Object.hasOwn(profile.text_overrides,id)) {
        config[role]=profile.text_overrides[id];
        selected.add(id);
      }
    }
    if (resized || selected.size) {
      const incoming = await realizeProductHero(hero.id, config, profile.width, profile.height);
      for (const node of incoming) {
        if(protectedDeliveryText(project,scene,node.id)){
          const baseline=hero.baseline.find(base=>base.id===node.id);
          if(!baseline)throw new Error("Protected Hero baseline identity missing.");
          node.text=baseline.text;
          continue;
        }
        if(!selected.has(node.id))continue;
        const raw = profile.text_overrides[node.id], wrapped = node.text;
        if (wrapped === null || wrapped.split("\n").some(line=>!line.trim())
            || raw.replaceAll("\n"," ") !== wrapped.replaceAll("\n"," "))
          throw new Error("Delivery Hero copy cannot be wrapped without altering authored characters or whitespace.");
        wrappedOverrides.set(node.id,{copy:wrapped,layout:config.layout,headline:node.id===await heroNodeId(hero.id,"headline")});
        const baseline = hero.baseline.find(base=>base.id===node.id);
        if (!baseline) throw new Error("Delivery Hero baseline identity missing.");
        node.text = baseline.text;
      }
      const sx = profile.width / 1920, sy = profile.height / 1080, scale = Math.min(sx, sy);
      for (const node of incoming) {
        if (!resized) continue;
        const destinationY = node.y, centerX = node.x + node.width / 2, centerY = node.y + node.height / 2;
        node.width /= scale; node.height /= scale;
        node.x = centerX / sx - node.width / 2; node.y = centerY / sy - node.height / 2;
        if (node.style.font_size !== null) node.style.font_size /= scale;
        node.style.stroke_width /= scale;
        for (const key of node.keyframes) if (key.property === "y") key.value = node.y + (key.value - destinationY) / sy;
      }
      output.nodes = mergeComponentNodes(hero.baseline, output.nodes, incoming);
    }
  }
  const field = resized ? project.production_design?.procedural_fields?.find(value =>
    value.scene_id === scene.id && value.config.distribution === "adaptive_grid") : undefined;
  if (field) {
    const incoming = await realizeProceduralFieldForOutput(field.id, field.config, profile.width, profile.height);
    output.nodes = mergeComponentNodes(field.baseline, output.nodes, incoming);
  }
  for (const node of output.nodes) {
    const wrapped = wrappedOverrides.get(node.id);
    if (wrapped) {
      node.text = wrapped.copy;
      node.text = fitVariantHeroText(node,wrapped.layout,wrapped.headline);
    } else if (node.kind === "text" && Object.hasOwn(profile.text_overrides,node.id)) {
      node.text = profile.text_overrides[node.id];
    }
    const retained=protectedDeliveryText(project,scene,node.id);
    if(retained && node.text!==retained.value)throw new Error(retained.reason+": "+node.name);
  }
  return output;
}
/** Preserve local-copy provenance in the SVG without reapplying the raw
 * saved override over its already-resolved layout lines. */
export function resolvedProfileTextOverrides(nodes: CanvasNode[], profile: DeliverableProfile): Record<string,string> {
  return Object.fromEntries(nodes.filter(node=>node.kind==="text" && node.text!==null
    && Object.hasOwn(profile.text_overrides,node.id)).map(node=>[node.id,node.text!]));
}
export interface ProfileLayoutSample { nodes: CanvasNode[]; boundaryIssues: string[] }
/** Samples the authored geometry before applying Film's position/size projection.
 * This is an editorial layout, not font shaping, decoded media or native pixels. */
export function sampleProfileLayout(project: Project, scene: Scene, profile: DeliverableProfile, time: number): ProfileLayoutSample {
  // Validate the retained tree before sampling erases tracks and relations.
  const hierarchyIssue = nativeGroupOrderIssue(scene);
  if (hierarchyIssue) throw new Error("Output layout hierarchy is unresolved: " + hierarchyIssue);
  const sampled = sampledSceneCamera(scene, project.production_design?.camera_reframes, time);
  if (!sampled.camera) throw new Error(sampled.issue);
  if (sampled.camera.zoom !== 1 || sampled.camera.rotation_deg !== 0)
    throw new Error("This output study requires the planar camera profile. Review other camera poses in Native.");
  if (![profile.width, profile.height].every(value => Number.isInteger(value) && value >= 320 && value <= 7680))
    throw new Error("Output layout dimensions are out of bounds.");
  const alignment = resolveCanvasRelations(scene.nodes, alignmentScale(profile.width, profile.height, profile.framing_strategy), false,
    {duration:scene.duration,fps:profile.frame_rate,continuous:true});
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
