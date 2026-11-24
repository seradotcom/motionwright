/** Current-source Desktop lit stills. Retain the original intent before Start.
 * Readback and PNG reads never acquire an execution grant or repeat a render.
 */
import { invoke } from "@tauri-apps/api/core";
import { sameValue } from "./creativeProduction";
import type { Project } from "./types";

export interface BlenderLitScope { project_id: string; generation: string; revision: number; scene_id: string }
export interface BlenderLitSource {
  schema: "motionwright.blender.desktop-lit-source/1"; source: BlenderLitScope;
  source_sha256: string; fingerprint: string; attempt_key: string; attempted: boolean;
  width: number; height: number; object_count: number; profile: "planar-project-cycles-lit-still-v1";
  native_dispatch_performed: false;
}
export interface BlenderLitIntent {
  source: BlenderLitScope; source_sha256: string; fingerprint: string; request_id: string;
}
export interface BlenderLitPlan {
  schema: "motionwright.blender.desktop-lit-plan/1"; plan_id: string; intent: BlenderLitIntent;
  effect_subject: string; attempt_key: string; capsule_id: string; expires_in_seconds: number;
  native_dispatch_performed: false; execution_grant_inherited: false;
}
export interface BlenderLitAsset { asset_id: string; sha256: string; bytes: number }
export interface BlenderLitSavedAssets {
  expected_after: { resource: string; generation: string; revision: number };
  capsule_id: string; image: BlenderLitAsset; editable_source: BlenderLitAsset; width: number; height: number;
}
export interface BlenderLitAttempt {
  schema: "motionwright.blender.desktop-lit-attempt/1"; attempt_key: string; original_request_id: string;
  source: BlenderLitScope; source_sha256: string; fingerprint: string;
  state: "dispatching" | "completed" | "failed_known" | "outcome_unknown" | "native_completed_adoption_unconfirmed" | "terminal_unconfirmed";
  source_current: boolean; native_outcome: { Ok: unknown } | { Err: unknown } | null;
  saved_assets: BlenderLitSavedAssets | null; journal: Record<string, unknown> | null; receipt_id: string | null;
  automatic_retry: false; execution_grant_inherited: false; visual_acceptance: false;
}
const DIGEST = /^[0-9a-f]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const STATES = ["dispatching", "completed", "failed_known", "outcome_unknown", "native_completed_adoption_unconfirmed", "terminal_unconfirmed"];
function uuid(value: unknown): value is string { return typeof value === "string" && UUID.test(value) && value !== "00000000-0000-0000-0000-000000000000"; }
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function integer(value: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): value is number { return Number.isSafeInteger(value) && Number(value) >= min && Number(value) <= max; }
function digest(value: unknown): value is string { return typeof value === "string" && DIGEST.test(value); }
function requestId(value: unknown): value is string { return typeof value === "string" && value.startsWith("blit-ui-") && uuid(value.slice(8)); }
function desktop() {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) throw new Error("Blender lit stills require the desktop project store and an owner connection.");
}
function scope(project: Project, sceneId: string): BlenderLitScope {
  if (![project.id, project.generation, sceneId].every(uuid) || !integer(project.revision)
      || !project.scenes.some(scene => scene.id === sceneId && scene.renderer === "blender")) {
    throw new Error("Select a current saved Blender scene.");
  }
  return { project_id: project.id, generation: project.generation, revision: project.revision, scene_id: sceneId };
}
function current(project: Project, intent: BlenderLitIntent) {
  if (!intent || !sameValue(intent.source, scope(project, intent.source.scene_id))
      || !digest(intent.source_sha256) || !digest(intent.fingerprint) || !requestId(intent.request_id)) {
    throw new Error("The original Blender still intent no longer matches the saved project.");
  }
}
export async function inspectBlenderLitSource(project: Project, sceneId: string): Promise<BlenderLitSource> {
  desktop();
  const source = scope(project, sceneId), value: unknown = await invoke("native_blender_lit_source", { request: source });
  if (!record(value) || value.schema !== "motionwright.blender.desktop-lit-source/1"
      || !sameValue(value.source, source) || !digest(value.source_sha256) || !digest(value.fingerprint)
      || value.attempt_key !== "blender-lit-" + value.fingerprint || typeof value.attempted !== "boolean"
      || !integer(value.width, 1, 16384) || !integer(value.height, 1, 16384) || !integer(value.object_count, 1, 126)
      || value.profile !== "planar-project-cycles-lit-still-v1" || value.native_dispatch_performed !== false) {
    throw new Error("The Blender lit source inspection is unconfirmed.");
  }
  return structuredClone(value) as unknown as BlenderLitSource;
}
export function createBlenderLitIntent(project: Project, source: BlenderLitSource): BlenderLitIntent {
  if (!source || source.schema !== "motionwright.blender.desktop-lit-source/1" || source.attempted
      || source.native_dispatch_performed !== false || source.attempt_key !== "blender-lit-" + source.fingerprint) {
    throw new Error("Read the original Blender still attempt before another render.");
  }
  const intent = { source: structuredClone(source.source), source_sha256: source.source_sha256,
    fingerprint: source.fingerprint, request_id: "blit-ui-" + crypto.randomUUID() };
  current(project, intent); return intent;
}
function validatePlan(project: Project, intent: BlenderLitIntent, value: unknown): BlenderLitPlan {
  current(project, intent);
  if (!record(value) || value.schema !== "motionwright.blender.desktop-lit-plan/1" || !sameValue(value.intent, intent)
      || !uuid(value.plan_id) || !uuid(value.capsule_id) || value.attempt_key !== "blender-lit-" + intent.fingerprint
      || typeof value.effect_subject !== "string" || !new RegExp("^blender-lit\\|" + value.plan_id + "\\|[0-9a-f]{64}$").test(value.effect_subject)
      || !integer(value.expires_in_seconds, 1, 600) || value.native_dispatch_performed !== false || value.execution_grant_inherited !== false) {
    throw new Error("The Blender still preparation does not match its original intent.");
  }
  return structuredClone(value) as unknown as BlenderLitPlan;
}
export async function prepareBlenderLitStill(project: Project, intent: BlenderLitIntent, beforeDispatch: () => void): Promise<BlenderLitPlan | null> {
  desktop(); const submitted = structuredClone(intent); current(project, submitted); beforeDispatch();
  const value: unknown = await invoke("prepare_blender_lit_still", { request: submitted });
  return value === null ? null : validatePlan(project, submitted, value);
}
export class BlenderLitUnconfirmedError extends Error {
  readonly original: BlenderLitIntent;
  constructor(intent: BlenderLitIntent) {
    super("The original Blender still is unconfirmed. Read its retained result; rendering again is not automatic.");
    this.name = "BlenderLitUnconfirmedError"; this.original = structuredClone(intent);
  }
}
function asset(value: unknown, max: number): value is BlenderLitAsset {
  return record(value) && uuid(value.asset_id) && digest(value.sha256) && integer(value.bytes, 1, max);
}
function validateAttempt(project: Project, sceneId: string, attemptKey: string, value: unknown): BlenderLitAttempt {
  if (!record(value) || value.schema !== "motionwright.blender.desktop-lit-attempt/1"
      || value.attempt_key !== attemptKey || !record(value.source) || value.source.project_id !== project.id || value.source.scene_id !== sceneId
      || !uuid(value.source.generation) || !integer(value.source.revision) || !digest(value.source_sha256) || !digest(value.fingerprint)
      || value.attempt_key !== "blender-lit-" + value.fingerprint || !requestId(value.original_request_id)
      || typeof value.source_current !== "boolean" || !STATES.includes(String(value.state))
      || value.automatic_retry !== false || value.execution_grant_inherited !== false || value.visual_acceptance !== false
      || !(value.receipt_id === null || uuid(value.receipt_id))) throw new Error("The original Blender still receipt is unconfirmed.");
  const outcome = value.native_outcome;
  if (value.state === "dispatching") {
    if (outcome !== null || value.saved_assets !== null || !uuid(value.receipt_id)) throw new Error("The original Blender reservation is unconfirmed.");
  } else if (!record(outcome) || Object.keys(outcome).length !== 1
      || !["Ok", "Err"].some(key => Object.hasOwn(outcome, key))) throw new Error("The original Native terminal is unconfirmed.");
  if (value.state === "completed" || value.saved_assets !== null) {
    const saved = value.saved_assets;
    const native = record(outcome) ? outcome.Ok : null;
    if (!record(saved) || !record(saved.expected_after) || saved.expected_after.resource !== "project:" + project.id
        || saved.expected_after.generation !== value.source.generation || !integer(saved.expected_after.revision, Number(value.source.revision) + 1)
        || !uuid(saved.capsule_id) || !asset(saved.image, 32 * 1024 * 1024) || !asset(saved.editable_source, 512 * 1024 * 1024)
        || !integer(saved.width, 1, 16384) || !integer(saved.height, 1, 16384) || !record(native)
        || native.schema !== "motionwright.blender.lit-still/1" || native.phase !== "create"
        || native.profile !== "planar-project-cycles-lit-still-v1" || native.project_json_sha256 !== value.source_sha256
        || native.native_render_invoked !== true || native.native_render_file_measured !== true || native.visual_acceptance !== false
        || native.general_product_3d_authoring !== false || !record(native.image) || !record(native.saved_source)
        || !record(native.saved_source.file) || native.image.sha256 !== saved.image.sha256 || native.image.bytes !== saved.image.bytes
        || native.saved_source.file.sha256 !== saved.editable_source.sha256 || native.saved_source.file.bytes !== saved.editable_source.bytes
        || (value.state === "completed" && !uuid(value.receipt_id))) throw new Error("The completed Blender source/image bindings are unconfirmed.");
  }
  return structuredClone(value) as unknown as BlenderLitAttempt;
}
export async function startBlenderLitStill(project: Project, intent: BlenderLitIntent, plan: BlenderLitPlan, beforeDispatch: () => void): Promise<BlenderLitAttempt> {
  desktop(); const submitted = structuredClone(intent), prepared = validatePlan(project, submitted, plan); beforeDispatch();
  const grant: unknown = await invoke("issue_effect_grant", { request: {
    effect: "render_local", project_id: project.id, generation: project.generation, revision: project.revision, subject: prepared.effect_subject,
  } });
  if (!record(grant) || !uuid(grant.token) || grant.effect !== "render_local" || grant.one_time !== true
      || grant.project_id !== project.id || grant.generation !== project.generation || grant.revision !== project.revision) {
    throw new Error("The Blender still grant does not match its preparation.");
  }
  beforeDispatch();
  try {
    const value: unknown = await invoke("start_blender_lit_still", { request: { intent: submitted, plan_id: prepared.plan_id, effect_grant: grant.token } });
    const result = validateAttempt(project, submitted.source.scene_id, prepared.attempt_key, value);
    if (result.original_request_id !== submitted.request_id || !sameValue(result.source, submitted.source) || result.source_sha256 !== submitted.source_sha256) {
      throw new Error("The Native reply belongs to another intent.");
    }
    return result;
  } catch { throw new BlenderLitUnconfirmedError(submitted); }
}
export async function readBlenderLitAttempt(project: Project, sceneId: string, attemptKey: string): Promise<BlenderLitAttempt | null> {
  desktop(); const source = scope(project, sceneId);
  if (!/^blender-lit-[0-9a-f]{64}$/.test(attemptKey)) throw new Error("Retain the original Blender still key.");
  const value: unknown = await invoke("read_original_blender_lit_still", { request: { source, attempt_key: attemptKey } });
  return value === null ? null : validateAttempt(project, sceneId, attemptKey, value);
}
export async function readBlenderLitPng(project: Project, view: BlenderLitAttempt): Promise<Blob> {
  desktop(); const original = validateAttempt(project, view.source.scene_id, view.attempt_key, view), saved = original.saved_assets;
  if (original.state !== "completed" || !saved
      || (saved.expected_after.generation === project.generation && saved.expected_after.revision > project.revision)
      || !project.assets.some(a => a.id === saved.image.asset_id
        && a.content_sha256 === saved.image.sha256 && a.media_type === "image/png")) {
    throw new Error("Reload the project containing this original Blender image.");
  }
  const value = await invoke<ArrayBuffer>("preview_blender_lit_still", { request: {
    source: scope(project, original.source.scene_id), attempt_key: original.attempt_key,
  } });
  if (!(value instanceof ArrayBuffer) || value.byteLength !== saved.image.bytes || value.byteLength > 32 * 1024 * 1024 || value.byteLength < 24) {
    throw new Error("The original Blender PNG response is unconfirmed.");
  }
  const bytes = new Uint8Array(value), header = [137, 80, 78, 71, 13, 10, 26, 10];
  const dimensions = new DataView(value);
  if (!header.every((v, i) => bytes[i] === v) || dimensions.getUint32(16) !== saved.width || dimensions.getUint32(20) !== saved.height) {
    throw new Error("The original Blender PNG dimensions changed.");
  }
  return new Blob([value], { type: "image/png" });
}
