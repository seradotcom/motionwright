/** Explicit adapter for the existing native Blender product cycle.
 * The caller retains one intent before preparation and before each dispatch barrier.
 * Lost replies always require readBlenderAttempt; this adapter never repeats a start.
 */
import { invoke } from "@tauri-apps/api/core";
import { sameValue } from "./creativeProduction";
import type { BlenderSourceSnapshot } from "./blenderSourceApi";
import type { Project } from "./types";

export interface BlenderCycleScope {
  project_id: string; generation: string; revision: number; scene_id: string; capsule_id: string;
}
export interface BlenderCycleIntent {
  source: BlenderCycleScope; source_sha256: string; request_id: string; annotation_body: string;
}
export interface BlenderCyclePlan {
  schema: "motionwright.blender.desktop-product-plan/1";
  plan_id: string; source: BlenderCycleScope; source_sha256: string; request_id: string;
  annotation_before: string; annotation_after: string; effect_subject: string;
  expires_in_seconds: number; phases: ["create_original", "edit_original", "verify_edited"];
  native_dispatch_performed: false; execution_grant_inherited: false;
}
export interface BlenderCycleResponse {
  schema: "motionwright.blender.desktop-product-cycle/1";
  source: BlenderCycleScope; original_request_id: string;
  outcome: { Ok: unknown } | { Err: unknown };
  journal: Record<string, unknown>; automatic_retry: false;
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const DIGEST = /^[0-9a-f]{64}$/;
function uuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value) && value !== "00000000-0000-0000-0000-000000000000";
}
function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}
function desktop() {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Blender production requires the desktop project store and owner connections.");
  }
}
function validAnnotation(value: unknown): value is string {
  return typeof value === "string" && /^[\x20-\x7e]+(?:\n[\x20-\x7e]+)?$/.test(value)
    && value.split("\n").every(line => line.length <= 32);
}
function current(project: Project, source: BlenderCycleScope, digest: string, requestId: string) {
  if (!source || source.project_id !== project.id || source.generation !== project.generation
      || source.revision !== project.revision || !Number.isSafeInteger(source.revision) || source.revision < 0
      || ![source.project_id, source.generation, source.scene_id, source.capsule_id].every(uuid)
      || !DIGEST.test(digest) || !requestId.startsWith("blender-ui-") || !uuid(requestId.slice(11))) {
    throw new Error("The prepared Blender scope no longer matches the saved project.");
  }
  const capsules = (project.production_design?.capsules ?? []).filter(item => item.id === source.capsule_id
    && item.scene_id === source.scene_id && item.source_sha256 === digest);
  if (capsules.length !== 1 || !project.scenes.some(scene => scene.id === source.scene_id)
      || !project.assets.some(asset => asset.id === capsules[0].source_asset_id && asset.content_sha256 === digest)) {
    throw new Error("The prepared Blender capsule or its source binding changed.");
  }
}
export function createBlenderCycleIntent(
  project: Project, source: BlenderSourceSnapshot, annotationBody: string,
): BlenderCycleIntent {
  if (!sameValue(project, source.project) || source.source_kind !== "authored_recipe"
      || source.attempt !== null || !source.source_cas_verified || !validAnnotation(annotationBody)
      || annotationBody === source.effective_recipe.annotation) {
    throw new Error("Read the current authored Blender source and choose one explicit annotation edit.");
  }
  const intent: BlenderCycleIntent = {
    source: { project_id: project.id, generation: project.generation, revision: project.revision,
      scene_id: source.scene_id, capsule_id: source.capsule_id },
    source_sha256: source.current_source.sha256, request_id: "blender-ui-" + crypto.randomUUID(),
    annotation_body: annotationBody,
  };
  current(project, intent.source, intent.source_sha256, intent.request_id);
  return intent;
}
export function validateBlenderCyclePlan(
  project: Project, intent: BlenderCycleIntent, value: unknown,
): BlenderCyclePlan {
  current(project, intent.source, intent.source_sha256, intent.request_id);
  if (!record(value) || value.schema !== "motionwright.blender.desktop-product-plan/1" || !uuid(value.plan_id)
      || !sameValue(value.source, intent.source) || value.source_sha256 !== intent.source_sha256
      || value.request_id !== intent.request_id || value.annotation_after !== intent.annotation_body
      || !validAnnotation(value.annotation_before) || value.annotation_before === value.annotation_after
      || typeof value.effect_subject !== "string"
      || !new RegExp("^blender-product\\|" + value.plan_id + "\\|[0-9a-f]{64}$").test(value.effect_subject)
      || !Number.isSafeInteger(value.expires_in_seconds) || Number(value.expires_in_seconds) <= 0
      || Number(value.expires_in_seconds) > 600
      || !sameValue(value.phases, ["create_original", "edit_original", "verify_edited"])
      || value.native_dispatch_performed !== false || value.execution_grant_inherited !== false) {
    throw new Error("The Blender preparation response does not match its original saved intent.");
  }
  return structuredClone(value) as unknown as BlenderCyclePlan;
}
export async function prepareBlenderCycle(
  project: Project, intent: BlenderCycleIntent, beforeDispatch: () => void,
): Promise<BlenderCyclePlan | null> {
  desktop();
  const submitted = structuredClone(intent);
  current(project, submitted.source, submitted.source_sha256, submitted.request_id);
  if (!validAnnotation(submitted.annotation_body)) throw new Error("The Blender annotation is outside its supported source profile.");
  beforeDispatch();
  const result: unknown = await invoke("prepare_blender_product_cycle", { request: submitted });
  return result === null ? null : validateBlenderCyclePlan(project, submitted, result);
}
export class BlenderCycleUnconfirmedError extends Error {
  readonly original: BlenderCycleIntent;
  constructor(intent: BlenderCycleIntent) {
    super("The original Blender operation is unconfirmed. Keep this request and read its original result; starting again is not automatic.");
    this.name = "BlenderCycleUnconfirmedError";
    this.original = structuredClone(intent);
  }
}
export async function startBlenderCycle(
  project: Project, intent: BlenderCycleIntent, plan: BlenderCyclePlan, beforeDispatch: () => void,
): Promise<BlenderCycleResponse> {
  desktop();
  const submitted = structuredClone(intent);
  const prepared = validateBlenderCyclePlan(project, submitted, plan);
  beforeDispatch(); // Caller persists original intent before grant acquisition.
  const grant: unknown = await invoke("issue_effect_grant", { request: {
    effect: "render_local", project_id: prepared.source.project_id, generation: prepared.source.generation,
    revision: prepared.source.revision, subject: prepared.effect_subject,
  } });
  if (!record(grant) || !uuid(grant.token) || grant.effect !== "render_local" || grant.one_time !== true
      || grant.project_id !== prepared.source.project_id || grant.generation !== prepared.source.generation
      || grant.revision !== prepared.source.revision) throw new Error("The Blender effect grant does not match its preparation.");
  beforeDispatch(); // A later project or UI change must stop before Native dispatch.
  let result: unknown;
  try {
    result = await invoke("start_blender_product_cycle", { request: {
      source: prepared.source, source_sha256: prepared.source_sha256,
      request_id: prepared.request_id, plan_id: prepared.plan_id, effect_grant: grant.token,
    } });
  } catch {
    throw new BlenderCycleUnconfirmedError(submitted);
  }
  if (!record(result) || result.schema !== "motionwright.blender.desktop-product-cycle/1"
      || !sameValue(result.source, submitted.source) || result.original_request_id !== submitted.request_id
      || result.automatic_retry !== false || !record(result.outcome) || Object.keys(result.outcome).length !== 1
      || !["Ok", "Err"].some(key => Object.hasOwn(result.outcome as object, key))
      || !record(result.journal) || result.journal.schema !== "motionwright.blender.product-cycle-journal-summary/1"
      || result.journal.automatic_retry !== false) throw new BlenderCycleUnconfirmedError(submitted);
  if (Object.hasOwn(result.outcome, "Ok")) {
    const completed = result.outcome.Ok;
    const before = record(completed) ? completed.expected_before : null;
    const after = record(completed) ? completed.expected_after : null;
    const lineage = record(completed) ? completed.durable_source_lineage : null;
    if (!record(completed) || completed.schema !== "motionwright.blender.product-cycle-caller/2"
        || completed.project_id !== submitted.source.project_id || completed.scene_id !== submitted.source.scene_id
        || completed.capsule_id !== submitted.source.capsule_id || completed.recipe_sha256 !== submitted.source_sha256
        || !record(before) || before.resource !== "project:" + submitted.source.project_id
        || before.generation !== submitted.source.generation || before.revision !== submitted.source.revision
        || !record(after) || after.resource !== before.resource || after.generation !== before.generation
        || !Number.isSafeInteger(after.revision) || Number(after.revision) <= submitted.source.revision
        || completed.same_capsule_repointed !== true || completed.native_sources_persisted_in_cas !== true
        || completed.execution_grant_inherited !== false || completed.visual_acceptance !== false
        || completed.human_acceptance !== false || !record(lineage)
        || lineage.schema !== "motionwright.blender.product-cycle-terminal/1"
        || !uuid(lineage.claim_receipt_id) || !uuid(lineage.terminal_receipt_id)
        || lineage.execution_grant_inherited !== false) throw new BlenderCycleUnconfirmedError(submitted);
  } else if (!record(result.outcome.Err)) throw new BlenderCycleUnconfirmedError(submitted);
  return result as unknown as BlenderCycleResponse;
}
