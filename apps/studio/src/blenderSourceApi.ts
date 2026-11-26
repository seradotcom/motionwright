/** Canonical saved Blender reads. These responses never authorize native work. */
import { invoke } from "@tauri-apps/api/core";
import { sameValue } from "./creativeProduction";
import type { Project } from "./types";
import {isProductCameraMotion,type ProductCameraMotion} from "./blenderProductRecipe";

export const BLENDER_RECIPE_MEDIA_TYPE = "application/vnd.motionwright.blender-product+json";
export type BlenderAttemptState = "completed" | "failed_known" | "outcome_unknown";
export interface BlenderRecipe {
  version: 1; namespace: string; coating: "ocean" | "clay" | "chalk" | "graphite";
  roughness: number; metallic: number; samples: number; annotation: string;
  camera_curve: "linear" | "bezier_auto_clamped";
  camera_motion?:ProductCameraMotion;
}
interface Stamp { resource: string; generation: string; revision: number; }
interface AssetBinding { asset_id: string; sha256: string; }
interface NativeBinding extends AssetBinding { bytes: number; }
export interface BlenderAttemptSummary {
  original_request_id: string; expected_before: Stamp; claim_receipt_id: string;
  terminal_receipt_id: string | null; state: BlenderAttemptState;
  native_outcome_known: boolean; error_code: string | null;
}
export interface BlenderLineage {
  original_request_id: string; claim_receipt_id: string; terminal_receipt_id: string;
  expected_before: Stamp; original_attached: Stamp; expected_after: Stamp;
  recipe: AssetBinding; original: NativeBinding; edited: NativeBinding;
  annotation: { before: string; after: string };
  original_surface_png_sha256: string; edited_surface_png_sha256: string;
}
interface ReadFlags {
  execution_grant_inherited: false; native_dispatch_performed: false;
  full_1200_frame_render: false; decoded_pixel_difference_verified: false;
  visual_acceptance: false; human_acceptance: false;
}
export interface BlenderSourceSnapshot extends ReadFlags {
  schema: "motionwright.blender.product-source-snapshot/1";
  project: Project; scene_id: string; capsule_id: string;
  current_source: AssetBinding; original_recipe: AssetBinding; effective_recipe: BlenderRecipe;
  source_kind: "authored_recipe" | "edited_native_source";
  lineage: BlenderLineage | null; attempt: BlenderAttemptSummary | null;
  source_cas_verified: true; source_acceptance_inherited: false;
}
export interface BlenderAttemptProjection extends ReadFlags {
  schema: "motionwright.blender.product-attempt-projection/1";
  project: Project; scene_id: string; capsule_id: string; attempt: BlenderAttemptSummary;
  lineage: BlenderLineage | null; current_source_matches: boolean; source_cas_verified: boolean;
  effective_recipe: BlenderRecipe | null; source_unavailable_reason: string | null; automatic_retry: false;
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const FLAGS = ["execution_grant_inherited", "native_dispatch_performed", "full_1200_frame_render",
  "decoded_pixel_difference_verified", "visual_acceptance", "human_acceptance"];
function invalid(): never {
  throw new Error("The Blender source read does not match the selected saved project or its read-only contract.");
}
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function keys(value: Record<string, unknown>, expected: string[]) {
  if (Object.keys(value).length !== expected.length || expected.some(key => !Object.hasOwn(value, key))) invalid();
}
function uuid(value: unknown): asserts value is string {
  if (typeof value !== "string" || !UUID.test(value) || value === "00000000-0000-0000-0000-000000000000") invalid();
}
function digest(value: unknown): asserts value is string {
  if (typeof value !== "string" || !DIGEST.test(value)) invalid();
}
function integer(value: unknown, minimum = 0): asserts value is number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) invalid();
}
function requestId(value: unknown): asserts value is string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]{1,48}$/.test(value)) invalid();
}
function stamp(project: Project, value: unknown): Stamp {
  const row = object(value); keys(row, ["resource", "generation", "revision"]);
  uuid(row.generation); integer(row.revision);
  if (row.resource !== "project:" + project.id) invalid();
  return row as unknown as Stamp;
}
function binding(project: Project, value: unknown, native: boolean, requireCurrentAsset: boolean): AssetBinding | NativeBinding {
  const row = object(value); keys(row, ["asset_id", "sha256", ...(native ? ["bytes"] : [])]);
  uuid(row.asset_id); digest(row.sha256);
  if (native) integer(row.bytes, 1);
  if (requireCurrentAsset) {
    const assets = project.assets.filter(asset => asset.id === row.asset_id);
    if (assets.length !== 1 || assets[0].content_sha256 !== row.sha256) invalid();
  }
  return row as unknown as AssetBinding | NativeBinding;
}
function recipe(value: unknown): BlenderRecipe {
  const row = object(value);
  const hasMotion=Object.hasOwn(row,"camera_motion");
  keys(row, ["version", "namespace", "coating", "roughness", "metallic", "samples", "annotation", "camera_curve",...(hasMotion?["camera_motion"]:[])]);
  if(hasMotion&&!isProductCameraMotion(row.camera_motion,row.camera_curve))invalid();
  if (row.version !== 1 || typeof row.namespace !== "string" || !/^[A-Za-z0-9_]{1,32}$/.test(row.namespace)
      || !["ocean", "clay", "chalk", "graphite"].includes(String(row.coating))
      || !["linear", "bezier_auto_clamped"].includes(String(row.camera_curve))) invalid();
  for (const field of ["roughness", "metallic"]) {
    const value = row[field];
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) invalid();
  }
  integer(row.samples, 16); if (row.samples > 256) invalid();
  if (typeof row.annotation !== "string" || !/^[\x20-\x7e]+(?:\n[\x20-\x7e]+)?$/.test(row.annotation)
      || row.annotation.split("\n").some(line => line.length > 32)) invalid();
  return row as unknown as BlenderRecipe;
}
function attempt(project: Project, value: unknown): BlenderAttemptSummary {
  const row = object(value);
  keys(row, ["original_request_id", "expected_before", "claim_receipt_id", "terminal_receipt_id",
    "state", "native_outcome_known", "error_code"]);
  requestId(row.original_request_id); stamp(project, row.expected_before); uuid(row.claim_receipt_id);
  if (row.terminal_receipt_id !== null) uuid(row.terminal_receipt_id);
  if (row.error_code !== null && (typeof row.error_code !== "string" || !/^[A-Za-z_]{1,96}$/.test(row.error_code))) invalid();
  if (!["completed", "failed_known", "outcome_unknown"].includes(String(row.state))
      || row.native_outcome_known !== (row.state !== "outcome_unknown")) invalid();
  if (row.state !== "outcome_unknown" && row.terminal_receipt_id === null) invalid();
  if (row.state === "completed" && row.error_code !== null) invalid();
  return row as unknown as BlenderAttemptSummary;
}
function lineage(project: Project, value: unknown, requireCurrentAssets: boolean): BlenderLineage {
  const row = object(value);
  keys(row, ["original_request_id", "claim_receipt_id", "terminal_receipt_id", "expected_before",
    "original_attached", "expected_after", "recipe", "original", "edited", "annotation",
    "original_surface_png_sha256", "edited_surface_png_sha256"]);
  requestId(row.original_request_id); uuid(row.claim_receipt_id); uuid(row.terminal_receipt_id);
  const before = stamp(project, row.expected_before), original = stamp(project, row.original_attached),
    after = stamp(project, row.expected_after);
  if (before.generation !== original.generation || before.generation !== after.generation
      || original.revision <= before.revision || after.revision <= original.revision) invalid();
  binding(project, row.recipe, false, requireCurrentAssets);
  binding(project, row.original, true, requireCurrentAssets); binding(project, row.edited, true, requireCurrentAssets);
  const annotation = object(row.annotation); keys(annotation, ["before", "after"]);
  if (typeof annotation.before !== "string" || typeof annotation.after !== "string"
      || !annotation.before || !annotation.after || annotation.before === annotation.after
      || annotation.before.length > 65 || annotation.after.length > 65) invalid();
  digest(row.original_surface_png_sha256); digest(row.edited_surface_png_sha256);
  return row as unknown as BlenderLineage;
}
function matchLineage(summary: BlenderAttemptSummary, chain: BlenderLineage) {
  if (summary.state !== "completed" || summary.original_request_id !== chain.original_request_id
      || summary.claim_receipt_id !== chain.claim_receipt_id || summary.terminal_receipt_id !== chain.terminal_receipt_id
      || !sameValue(summary.expected_before, chain.expected_before)) invalid();
}
function selected(project: Project, sceneId: string, capsuleId: string) {
  uuid(project.id); uuid(project.generation); integer(project.revision); uuid(sceneId); uuid(capsuleId);
  if (project.scenes.filter(scene => scene.id === sceneId).length !== 1) invalid();
  const capsules = project.production_design?.capsules.filter(capsule => capsule.id === capsuleId) ?? [];
  if (capsules.length !== 1 || capsules[0].scene_id !== sceneId) invalid();
  const capsule = capsules[0];
  binding(project, { asset_id: capsule.source_asset_id, sha256: capsule.source_sha256 }, false, true);
  return capsule;
}
function envelope(project: Project, sceneId: string, capsuleId: string, value: unknown, schema: string, fields: string[]) {
  selected(project, sceneId, capsuleId);
  const row = object(value);
  keys(row, ["schema", "project", "scene_id", "capsule_id", ...fields, ...FLAGS]);
  if (row.schema !== schema || !sameValue(row.project, project) || row.scene_id !== sceneId || row.capsule_id !== capsuleId) invalid();
  for (const field of FLAGS) if (row[field] !== false) invalid();
  return row;
}
export function validateBlenderSource(project: Project, sceneId: string, capsuleId: string, value: unknown): BlenderSourceSnapshot {
  const row = envelope(project, sceneId, capsuleId, value, "motionwright.blender.product-source-snapshot/1",
    ["current_source", "original_recipe", "effective_recipe", "source_kind", "lineage", "attempt",
      "source_cas_verified", "source_acceptance_inherited"]);
  if (row.source_cas_verified !== true || row.source_acceptance_inherited !== false) invalid();
  const capsule = selected(project, sceneId, capsuleId);
  const current = binding(project, row.current_source, false, true), original = binding(project, row.original_recipe, false, true);
  const effective = recipe(row.effective_recipe);
  if (current.asset_id !== capsule.source_asset_id || current.sha256 !== capsule.source_sha256) invalid();
  const observation = row.attempt === null ? null : attempt(project, row.attempt);
  if (row.source_kind === "authored_recipe") {
    if (row.lineage !== null || !sameValue(current, original)) invalid();
  } else if (row.source_kind === "edited_native_source") {
    const chain = lineage(project, row.lineage, true);
    if (!observation) invalid();
    matchLineage(observation, chain);
    if (chain.expected_after.generation !== project.generation || chain.expected_after.revision > project.revision) invalid();
    if (chain.edited.asset_id !== current.asset_id || chain.edited.sha256 !== current.sha256
        || !sameValue(chain.recipe, original) || chain.annotation.after !== effective.annotation) invalid();
  } else invalid();
  return row as unknown as BlenderSourceSnapshot;
}
export function validateBlenderAttempt(project: Project, sceneId: string, capsuleId: string,
  originalRequestId: string, receiptId: string | null, value: unknown): BlenderAttemptProjection {
  requestId(originalRequestId); if (receiptId !== null) uuid(receiptId);
  const row = envelope(project, sceneId, capsuleId, value, "motionwright.blender.product-attempt-projection/1",
    ["attempt", "lineage", "current_source_matches", "source_cas_verified", "effective_recipe",
      "source_unavailable_reason", "automatic_retry"]);
  if (row.automatic_retry !== false || typeof row.current_source_matches !== "boolean"
      || typeof row.source_cas_verified !== "boolean") invalid();
  const summary = attempt(project, row.attempt);
  if (summary.original_request_id !== originalRequestId
      || (receiptId !== null && ![summary.claim_receipt_id, summary.terminal_receipt_id].includes(receiptId))) invalid();
  const chain = row.lineage === null ? null : lineage(project, row.lineage, false);
  if (chain) matchLineage(summary, chain);
  if (summary.state === "completed" && !chain) invalid();
  if (row.source_cas_verified) {
    lineage(project, row.lineage, true);
    const effective = recipe(row.effective_recipe);
    const capsule = selected(project, sceneId, capsuleId);
    if (!chain || !row.current_source_matches || row.source_unavailable_reason !== null
        || chain.expected_after.generation !== project.generation || chain.expected_after.revision > project.revision
        || chain.edited.asset_id !== capsule.source_asset_id || chain.edited.sha256 !== capsule.source_sha256
        || chain.annotation.after !== effective.annotation) invalid();
  } else if (row.effective_recipe !== null || ![
    "original_attempt_has_no_completed_lineage", "current_source_cas_or_lineage_unavailable",
    "original_attempt_generation_changed", "current_source_binding_changed",
  ].includes(String(row.source_unavailable_reason))) invalid();
  return row as unknown as BlenderAttemptProjection;
}
function scope(project: Project, sceneId: string, capsuleId: string) {
  selected(project, sceneId, capsuleId);
  return { project_id: project.id, generation: project.generation, revision: project.revision, scene_id: sceneId, capsule_id: capsuleId };
}
function desktop() {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Saved Blender source inspection requires the desktop project store.");
  }
}
async function read(command: string, request: unknown): Promise<unknown> {
  try { return await invoke(command, { request }); }
  catch {
    throw new Error("The saved Blender read could not be completed. Refresh the saved project before reading again. The original native attempt has not been changed.");
  }
}
export async function readBlenderSource(project: Project, sceneId: string, capsuleId: string): Promise<BlenderSourceSnapshot> {
  desktop();
  const result = await read("native_blender_product_source", scope(project, sceneId, capsuleId));
  return validateBlenderSource(project, sceneId, capsuleId, result);
}
export async function readBlenderAttempt(project: Project, sceneId: string, capsuleId: string,
  originalRequestId: string, receiptId: string | null = null): Promise<BlenderAttemptProjection> {
  desktop(); requestId(originalRequestId); if (receiptId !== null) uuid(receiptId);
  const result = await read("native_blender_product_attempt", {
    source: scope(project, sceneId, capsuleId), original_request_id: originalRequestId, receipt_id: receiptId,
  });
  return validateBlenderAttempt(project, sceneId, capsuleId, originalRequestId, receiptId, result);
}
