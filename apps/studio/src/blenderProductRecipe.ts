import type { Project, Scene } from "./types";
import { recoveryOrigin } from "./recoveryOrigin";

export const BLENDER_PRODUCT_RECIPE_MIME = "application/vnd.motionwright.blender-product+json";
export const PRODUCT_COATINGS = ["ocean", "clay", "chalk", "graphite"] as const;
export const PRODUCT_CAMERA_CURVES = ["linear", "bezier_auto_clamped"] as const;
export type ProductCoating = typeof PRODUCT_COATINGS[number];
export type ProductCameraCurve = typeof PRODUCT_CAMERA_CURVES[number];
export interface BlenderProductRecipe {
  version: 1; namespace: string; coating: ProductCoating; roughness: number;
  metallic: number; samples: number; annotation: string; camera_curve: ProductCameraCurve;
}
/** Retain raw text, including incomplete numeric input, in the existing native source draft. */
export interface BlenderProductRecipeInput {
  namespace: string; coating: string; roughness: string; metallic: string;
  samples: string; annotation: string; camera_curve: string;
}
const inputFields = ["namespace", "coating", "roughness", "metallic", "samples", "annotation", "camera_curve"] as const;
export function newBlenderProductInput(sceneId: string): BlenderProductRecipeInput {
  return { namespace: "mw_" + sceneId.replace(/-/g, "").slice(0, 24), coating: "ocean", roughness: "0.28",
    metallic: "0.65", samples: "64", annotation: "EDITABLE\nSURFACE", camera_curve: "bezier_auto_clamped" };
}
export function parseBlenderProductInput(raw: string): BlenderProductRecipeInput {
  if (raw.length > 16_384) throw new Error("The product recipe input exceeds its authoring budget.");
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Product recipe input is unreadable.");
  const fields = value as Record<string, unknown>;
  if (Object.keys(fields).length !== inputFields.length || !inputFields.every(key => Object.hasOwn(fields, key) && typeof fields[key] === "string"))
    throw new Error("Product recipe input has an unsupported shape.");
  return fields as unknown as BlenderProductRecipeInput;
}
const decimal = /^(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$/;
function scalar(value: string, label: string): number {
  if (value.length > 64 || !decimal.test(value)) throw new Error(label + " needs a complete number between 0 and 1.");
  const number = Number(value);
  if (!Number.isFinite(number) || number < 0 || number > 1) throw new Error(label + " must be between 0 and 1.");
  return number;
}
/** Mirrors the closed Rust ProductScene v1 validation; this is not native admission. */
export function buildBlenderProductRecipe(input: BlenderProductRecipeInput): BlenderProductRecipe {
  if (!/^[A-Za-z0-9_]{1,32}$/.test(input.namespace))
    throw new Error("Namespace: use 1–32 ASCII letters, digits or underscores.");
  if (!(PRODUCT_COATINGS as readonly string[]).includes(input.coating))
    throw new Error("Choose one of the four admitted coatings.");
  if (!(PRODUCT_CAMERA_CURVES as readonly string[]).includes(input.camera_curve))
    throw new Error("Choose an explicit linear or auto-clamped Bézier camera curve.");
  if (!/^[0-9]{1,3}$/.test(input.samples) || Number(input.samples) < 16 || Number(input.samples) > 256)
    throw new Error("Samples must be an integer between 16 and 256.");
  const lines = input.annotation.split("\n");
  if (lines.length < 1 || lines.length > 2 || lines.some(line => !/^[\x20-\x7e]{1,32}$/.test(line)))
    throw new Error("Annotation: one or two nonempty printable ASCII lines, at most 32 characters each.");
  return { version: 1, namespace: input.namespace, coating: input.coating as ProductCoating,
    roughness: scalar(input.roughness, "Roughness"), metallic: scalar(input.metallic, "Metallic"),
    samples: Number(input.samples), annotation: input.annotation, camera_curve: input.camera_curve as ProductCameraCurve };
}
export function blenderRecipeFile(raw: string, capsuleId: string) {
  const recipe = buildBlenderProductRecipe(parseBlenderProductInput(raw));
  const text = JSON.stringify(recipe, null, 2) + "\n";
  const {sha256, byteLength} = recoveryOrigin(capsuleId, text);
  const name = "blender-product-" + recipe.namespace + ".json";
  return {recipe, text, sha256, byteLength, name};
}
export function blenderProductMetadata(raw: string, capsuleId: string) {
  const file = blenderRecipeFile(raw, capsuleId);
  return {path: "", name: file.name, mediaType: BLENDER_PRODUCT_RECIPE_MIME,
    label: "Product vessel / " + file.recipe.namespace,
    editorHint: "Motionwright Blender product recipe v1",
    parameters: JSON.stringify(["product.coating.variant","product.coating.roughness","product.coating.metallic","product.annotation.body"], null, 2),
    blenderRecipe: raw, blenderRecipeExport: file.sha256};
}
/** Local editor eligibility, not a Broker grant or a native preflight. The existing
 * read_blender_product_source independently validates the persisted scene and CAS. */
export function blenderProductSceneIssues(project: Project, scene: Scene, capsuleId: string): string[] {
  const issues: string[] = [];
  if (project.scenes.filter(s => s.id === scene.id).length !== 1 || scene.renderer !== "blender")
    issues.push("Choose a saved Blender scene.");
  const camera = scene.camera;
  if (camera.center_x !== 960 || camera.center_y !== 540 || camera.zoom !== 1 || camera.rotation_deg !== 0 || camera.safe_margin !== 0.05)
    issues.push("Use the default Canvas camera; this recipe owns its native cameras.");
  if (scene.nodes.length || scene.beats.length)
    issues.push("Use an empty scene without Canvas objects or beats. No existing composition will be removed.");
  const design = project.production_design;
  if ([design?.camera_reframes, design?.heroes, design?.stages, design?.procedural_fields, design?.semantic_graphs]
      .some(group => group?.some(item => item.scene_id === scene.id)))
    issues.push("Use a separate scene without other managed creative components.");
  if (design?.plan && design.plan.clock.kind !== "timeline")
    issues.push("The fixed product recipe uses the timeline production clock, not a voice or music clock.");
  try {
    const sn = BigInt(scene.start.num), sd = BigInt(scene.start.den);
    const dn = BigInt(scene.duration.num), dd = BigInt(scene.duration.den);
    if (sn < 0n || sd <= 0n || dd <= 0n || dn !== 40n * dd || sn * 30n % sd !== 0n)
      issues.push("Set exactly 40 seconds and align the scene start to 30 fps using the existing timeline and Inspector.");
  } catch { issues.push("The saved scene time is invalid."); }
  const attached = (design?.capsules ?? []).filter(c => c.scene_id === scene.id);
  if (attached.length > 1 || attached.some(c => c.id !== capsuleId))
    issues.push("The native product route requires exactly one source capsule in the scene. Choose its original recipe capsule or a separate empty scene.");
  if (attached.some(c => project.assets.find(a => a.id === c.source_asset_id)?.media_type !== BLENDER_PRODUCT_RECIPE_MIME))
    issues.push("A realized .blend or other native source must remain intact. Create a separate recipe scene instead.");
  if (project.locks.some(l => (l.resource === "project:" + project.id || l.resource === "scene:" + scene.id)
      && (l.kind === "content" || l.kind === "renderer")))
    issues.push("The saved project or scene is content/renderer locked.");
  return issues;
}
