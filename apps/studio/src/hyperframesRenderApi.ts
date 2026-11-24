import { invoke } from "@tauri-apps/api/core";
import { sameValue } from "./creativeProduction";
import { parseHyperframesSource, type HyperframesSourceBinding, type HyperframesSourceSnapshot } from "./hyperframesSourceApi";
import type { Project } from "./types";

export const HYPERFRAMES_RENDER_PREFIX = "motionwright:hyperframes-render:v1:";
type RetentionState = "prepared" | "dispatched" | "not_sent" | "completed" | "failed_known";
export interface HyperframesRenderIntent {
  request_id: string; source: HyperframesSourceBinding; fingerprint: string; runtime_lock_sha256: string;
  retention: RetentionState;
}
export interface HyperframesRenderResponse {
  attempt: {
    schema: "motionwright.hyperframes-desktop-attempt/1"; request_id: string;
    source: HyperframesSourceBinding | null; fingerprint: string | null; runtime_lock_sha256: string | null;
    state: "not_found" | "outcome_unknown" | "completed" | "failed_known";
    source_current: boolean; job_ref: string | null; frame_count: number | null;
    project_modified: false; automatic_retry: false;
  };
  preview: { token: string; segment_id: string; scene_ids: string[]; frame_count: number } | null;
  preview_problem: string | null;
}
const uuid = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
  && value !== "00000000-0000-0000-0000-000000000000";
export const hyperframesRuntimeDigest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
function object(value: unknown, fields: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("The original HyperFrames record is malformed.");
  const record = value as Record<string, unknown>;
  if (Object.keys(record).length !== fields.length || fields.some(field => !Object.hasOwn(record, field))) {
    throw new Error("The original HyperFrames record has an unsupported shape.");
  }
  return record;
}
function validBinding(value: unknown): HyperframesSourceBinding {
  const record = object(value, ["project_id", "generation", "revision", "active_branch", "deliverable_id"]);
  if (![record.project_id, record.generation, record.active_branch, record.deliverable_id].every(uuid)
    || !Number.isSafeInteger(record.revision) || Number(record.revision) < 0) {
    throw new Error("The retained HyperFrames source scope is invalid.");
  }
  return record as unknown as HyperframesSourceBinding;
}
function key(project: Pick<Project, "id" | "generation">) {
  if (!uuid(project.id) || !uuid(project.generation)) throw new Error("Select a saved project.");
  return HYPERFRAMES_RENDER_PREFIX + project.id + ":" + project.generation;
}
function parseIntent(value: unknown, project: Pick<Project, "id" | "generation">): HyperframesRenderIntent {
  const record = object(value, ["request_id", "source", "fingerprint", "runtime_lock_sha256", "retention"]);
  const source = validBinding(record.source);
  if (typeof record.request_id !== "string" || !record.request_id.startsWith("hf-ui-")
    || !uuid(record.request_id.slice(6)) || !hyperframesRuntimeDigest(record.fingerprint)
    || !hyperframesRuntimeDigest(record.runtime_lock_sha256)
    || source.project_id !== project.id || source.generation !== project.generation
    || !["prepared", "dispatched", "not_sent", "completed", "failed_known"].includes(String(record.retention))) {
    throw new Error("The retained original HyperFrames request is invalid.");
  }
  return structuredClone(record) as unknown as HyperframesRenderIntent;
}
export function readHyperframesRenderIntents(project: Pick<Project, "id" | "generation">): HyperframesRenderIntent[] {
  const raw = localStorage.getItem(key(project));
  if (raw === null) return [];
  if (raw.length > 128 * 1024) throw new Error("The retained HyperFrames history exceeds its limit; preserve the original copy.");
  const value = object(JSON.parse(raw), ["schema", "attempts"]);
  if (value.schema !== "motionwright.hyperframes-render-retention/1" || !Array.isArray(value.attempts)
    || value.attempts.length > 32) throw new Error("Retained HyperFrames history cannot be read safely.");
  const attempts = value.attempts.map(item => parseIntent(item, project));
  if (new Set(attempts.map(item => item.request_id)).size !== attempts.length) throw new Error("Retained HyperFrames request identities are duplicated.");
  return attempts;
}
function save(project: Project, attempts: HyperframesRenderIntent[]) {
  const encoded = JSON.stringify({ schema: "motionwright.hyperframes-render-retention/1", attempts });
  localStorage.setItem(key(project), encoded);
  if (localStorage.getItem(key(project)) !== encoded) throw new Error("The original render request could not be retained. No render was sent.");
}
function update(project: Project, intent: HyperframesRenderIntent, retention: RetentionState) {
  const attempts = readHyperframesRenderIntents(project);
  const index = attempts.findIndex(item => item.request_id === intent.request_id);
  if (index < 0 || !sameValue({ ...attempts[index], retention: intent.retention }, intent)) {
    throw new Error("The retained render request changed. Preserve the original operation.");
  }
  const prior = attempts[index].retention;
  if ((retention === "dispatched" && prior !== "prepared")
    || (retention === "not_sent" && prior !== "prepared")
    || (prior === "completed" && retention !== "completed")) {
    throw new Error("The original render has advanced; its retained state cannot be replaced.");
  }
  attempts[index] = { ...intent, retention };
  save(project, attempts);
}
export const hyperframesIntentUnconfirmed = (intent: HyperframesRenderIntent) =>
  intent.retention === "prepared" || intent.retention === "dispatched";
export function prepareHyperframesRender(project: Project, snapshot: HyperframesSourceSnapshot,
  runtime: string): HyperframesRenderIntent {
  const source = parseHyperframesSource(snapshot, project, snapshot.deliverable_id);
  if (!hyperframesRuntimeDigest(runtime)) throw new Error("Enter the SHA-256 of the explicitly installed HyperFrames runtime lock.");
  const attempts = readHyperframesRenderIntents(project);
  if (attempts.some(hyperframesIntentUnconfirmed)) throw new Error("Read the original unconfirmed render before starting another.");
  // Compact confirmed browser selectors only; original SQLite operations remain.
  // Unconfirmed intents were refused above and are never evicted.
  while (attempts.length >= 32) attempts.shift();
  const intent: HyperframesRenderIntent = {
    request_id: "hf-ui-" + crypto.randomUUID(),
    source: { project_id: project.id, generation: project.generation, revision: project.revision,
      active_branch: project.active_branch, deliverable_id: source.deliverable_id },
    fingerprint: source.fingerprint, runtime_lock_sha256: runtime, retention: "prepared",
  };
  parseIntent(intent, project);
  save(project, [...attempts, intent]);
  return intent;
}
function desktop() {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) throw new Error("Native HyperFrames rendering requires Motionwright Desktop.");
}
export function parseHyperframesRenderResponse(value: unknown, project: Project, intent: HyperframesRenderIntent): HyperframesRenderResponse {
  parseIntent(intent, project);
  const result = object(value, ["attempt", "preview", "preview_problem"]);
  const attempt = object(result.attempt, ["schema", "request_id", "source", "fingerprint", "runtime_lock_sha256",
    "state", "source_current", "job_ref", "frame_count", "project_modified", "automatic_retry"]);
  if (attempt.schema !== "motionwright.hyperframes-desktop-attempt/1" || attempt.request_id !== intent.request_id
    || !["not_found", "outcome_unknown", "completed", "failed_known"].includes(String(attempt.state))
    || typeof attempt.source_current !== "boolean" || attempt.project_modified !== false || attempt.automatic_retry !== false
    || (result.preview_problem !== null && (typeof result.preview_problem !== "string" || result.preview_problem.length > 1024))) {
    throw new Error("The original render response is not bound to this request.");
  }
  if (attempt.state === "not_found") {
    if (attempt.source !== null || attempt.fingerprint !== null || attempt.runtime_lock_sha256 !== null
      || attempt.source_current !== false || attempt.job_ref !== null || attempt.frame_count !== null || result.preview !== null) {
      throw new Error("A missing original render cannot supply a result.");
    }
  } else {
    validBinding(attempt.source);
    if (!sameValue(attempt.source, intent.source) || attempt.fingerprint !== intent.fingerprint
      || attempt.runtime_lock_sha256 !== intent.runtime_lock_sha256) throw new Error("The response belongs to another source or runtime.");
    if (attempt.source_current && (intent.source.revision !== project.revision || intent.source.active_branch !== project.active_branch)) {
      throw new Error("A historical render was presented as current.");
    }
    if (attempt.state === "completed") {
      if (typeof attempt.job_ref !== "string" || !/^hf-[0-9a-f]{64}$/.test(attempt.job_ref)
        || !Number.isSafeInteger(attempt.frame_count) || Number(attempt.frame_count) < 30
        || Number(attempt.frame_count) > 300 || Number(attempt.frame_count) % 30 !== 0) throw new Error("The completed sequence identity is malformed.");
    } else if (attempt.job_ref !== null || attempt.frame_count !== null || result.preview !== null) {
      throw new Error("An unconfirmed render cannot supply a completed sequence.");
    }
  }
  if (result.preview !== null) {
    const preview = object(result.preview, ["token", "segment_id", "scene_ids", "frame_count"]);
    if (attempt.state !== "completed" || attempt.source_current !== true || !uuid(preview.token)
      || preview.segment_id !== attempt.job_ref || preview.frame_count !== attempt.frame_count
      || !sameValue(preview.scene_ids, project.scenes.map(scene => scene.id))) {
      throw new Error("The PNG read handle belongs to another source.");
    }
  }
  return result as unknown as HyperframesRenderResponse;
}
function retainKnownOutcome(project: Project, intent: HyperframesRenderIntent, response: HyperframesRenderResponse) {
  if (response.attempt.state === "completed" || response.attempt.state === "failed_known") {
    update(project, intent, response.attempt.state);
  }
}
export async function dispatchHyperframesRender(project: Project, snapshot: HyperframesSourceSnapshot,
  intent: HyperframesRenderIntent, beforeDispatch: () => void): Promise<HyperframesRenderResponse> {
  desktop();
  const original = structuredClone(project), saved = parseHyperframesSource(snapshot, original, snapshot.deliverable_id);
  const request = parseIntent(intent, original);
  if (request.retention !== "prepared" || request.fingerprint !== saved.fingerprint
    || request.source.revision !== original.revision || request.source.active_branch !== original.active_branch
    || request.source.deliverable_id !== saved.deliverable_id) throw new Error("This render intent cannot be dispatched.");
  const retained = readHyperframesRenderIntents(original).find(item => item.request_id === request.request_id);
  if (!sameValue(retained, request)) throw new Error("The exact original render intent must be retained before dispatch.");
  let dispatched = false;
  try {
    beforeDispatch();
    const subject = ["hyperframes-render", request.source.active_branch, request.source.deliverable_id,
      request.fingerprint, request.runtime_lock_sha256, request.request_id].join("|");
    const grant = await invoke<{ token: string }>("issue_effect_grant", { request: {
      effect: "render_local", project_id: original.id, generation: original.generation, revision: original.revision, subject,
    } });
    if (!uuid(grant?.token)) throw new Error("The exact local render permission was not returned.");
    beforeDispatch();
    update(original, request, "dispatched");
    dispatched = true;
    const result = await invoke<unknown>("render_hyperframes_source", { request: {
      source: request.source, expected_fingerprint: request.fingerprint, runtime_lock_sha256: request.runtime_lock_sha256,
      request_id: request.request_id, effect_grant: grant.token,
    } });
    const response = parseHyperframesRenderResponse(result, original, request);
    retainKnownOutcome(original, request, response);
    return response;
  } catch (error) {
    if (!dispatched) update(original, request, "not_sent");
    throw error;
  }
}
export async function readOriginalHyperframesRender(project: Project, intent: HyperframesRenderIntent): Promise<HyperframesRenderResponse> {
  desktop();
  const original = structuredClone(project), request = parseIntent(intent, original);
  const response = parseHyperframesRenderResponse(await invoke<unknown>("read_original_hyperframes_render", { request: {
    project_id: original.id, generation: original.generation, request_id: request.request_id,
  } }), original, request);
  retainKnownOutcome(original, request, response);
  return response;
}
