import { invoke } from "@tauri-apps/api/core";
import { sameValue } from "./creativeProduction";
import type { Project } from "./types";

export type HyperframesSourceFormat = "html" | "json";
export interface HyperframesSourceBinding {
  project_id: string; generation: string; revision: number; active_branch: string; deliverable_id: string;
}
export interface HyperframesSourceSnapshot {
  schema: "motionwright.hyperframes-source/1"; project: Project; deliverable_id: string;
  fingerprint: string; source_sha256: string; source_bytes: string; html_sha256: string; html_bytes: string;
  compiler: string; hyperframes_version: string; width: number; height: number; frame_count: number;
  project_modified: false; editor_launched: false; native_execution_started: false;
}
export interface HyperframesSourceExport {
  schema: "motionwright.hyperframes-source-export/1"; source: HyperframesSourceBinding;
  fingerprint: string; format: HyperframesSourceFormat; path: string; content_sha256: string; size_bytes: string;
  project_modified: false; editor_launched: false; native_execution_started: false;
}
const uuid = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
  && value !== "00000000-0000-0000-0000-000000000000";
const sha = (value: unknown) => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const bytes = (value: unknown) => typeof value === "string" && /^[1-9][0-9]{0,5}$/.test(value) && Number(value) <= 256 * 1024;
function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("The saved source response is malformed.");
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== fields.length || fields.some(key => !Object.hasOwn(result, key))) {
    throw new Error("The saved source response has an unsupported shape.");
  }
  return result;
}
function desktop() {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Saved HyperFrames source export requires Motionwright Desktop.");
  }
}
function binding(project: Project, profileId: string): HyperframesSourceBinding {
  if (![project.id, project.generation, project.active_branch, profileId].every(uuid)
    || !Number.isSafeInteger(project.revision) || project.revision < 0
    || project.deliverables.filter(profile => profile.id === profileId).length !== 1) {
    throw new Error("Select one saved delivery profile before inspecting its source.");
  }
  return { project_id: project.id, generation: project.generation, revision: project.revision,
    active_branch: project.active_branch, deliverable_id: profileId };
}
function inert(value: Record<string, unknown>) {
  if (value.project_modified !== false || value.editor_launched !== false || value.native_execution_started !== false) {
    throw new Error("The source operation returned unexpected project or native effects.");
  }
}
export function parseHyperframesSource(value: unknown, project: Project, profileId: string): HyperframesSourceSnapshot {
  binding(project, profileId);
  const result = record(value, ["schema", "project", "deliverable_id", "fingerprint", "source_sha256", "source_bytes",
    "html_sha256", "html_bytes", "compiler", "hyperframes_version", "width", "height", "frame_count",
    "project_modified", "editor_launched", "native_execution_started"]);
  if (result.schema !== "motionwright.hyperframes-source/1" || !sameValue(result.project, project)
    || result.deliverable_id !== profileId || !sha(result.fingerprint) || !sha(result.source_sha256)
    || !sha(result.html_sha256) || !bytes(result.source_bytes) || !bytes(result.html_bytes)
    || typeof result.compiler !== "string" || !/^[a-z0-9-]{1,96}$/.test(result.compiler)
    || typeof result.hyperframes_version !== "string" || !/^[0-9]+\.[0-9]+\.[0-9]+$/.test(result.hyperframes_version)
    || !Number.isSafeInteger(result.width) || Number(result.width) < 16 || Number(result.width) > 1920
    || !Number.isSafeInteger(result.height) || Number(result.height) < 16 || Number(result.height) > 1080
    || !Number.isSafeInteger(result.frame_count) || Number(result.frame_count) < 30
    || Number(result.frame_count) > 300 || Number(result.frame_count) % 30 !== 0) {
    throw new Error("The saved HyperFrames source does not match this project and profile.");
  }
  const profile = project.deliverables.find(item => item.id === profileId)!;
  if (result.width !== profile.width || result.height !== profile.height) {
    throw new Error("The source dimensions differ from the saved delivery profile.");
  }
  inert(result);
  return result as unknown as HyperframesSourceSnapshot;
}
export async function readHyperframesSource(project: Project, profileId: string): Promise<HyperframesSourceSnapshot> {
  desktop();
  const original = structuredClone(project);
  const request = binding(original, profileId);
  return parseHyperframesSource(await invoke<unknown>("native_hyperframes_source", { request }), original, profileId);
}
export async function exportHyperframesSource(
  project: Project, snapshot: HyperframesSourceSnapshot, format: HyperframesSourceFormat,
  destination: string, beforeDispatch: () => void,
): Promise<HyperframesSourceExport> {
  desktop();
  const original = structuredClone(project);
  const saved = parseHyperframesSource(structuredClone(snapshot), original, snapshot.deliverable_id);
  const source = binding(original, saved.deliverable_id);
  const path = destination.trim();
  if (format !== "html" && format !== "json") throw new Error("Choose the native HTML or editable JSON source.");
  const filename = format === "html" ? "index.html" : "semwright-hyperframes.json";
  if (!path || /[\u0000-\u001f\u007f]/.test(path)
    || !/^(?:[A-Za-z]:[\\/]|\/|\\\\)/.test(path) || path.split(/[\\/]/).at(-1) !== filename) {
    throw new Error("Choose " + filename + " in an existing absolute directory.");
  }
  const subject = ["hyperframes-source", original.active_branch, saved.deliverable_id, saved.fingerprint, format, path].join("|");
  if (new TextEncoder().encode(subject).length > 2_048) throw new Error("The export destination is too long.");
  beforeDispatch();
  const grant = await invoke<{ token: string }>("issue_effect_grant", { request: {
    effect: "deliver_local", project_id: original.id, generation: original.generation, revision: original.revision, subject,
  } });
  if (!uuid(grant?.token)) throw new Error("The local export permission was not returned.");
  beforeDispatch();
  const result = record(await invoke<unknown>("export_hyperframes_source_file", { request: {
    source, expected_fingerprint: saved.fingerprint, format, path, effect_grant: grant.token,
  } }), ["schema", "source", "fingerprint", "format", "path", "content_sha256", "size_bytes",
    "project_modified", "editor_launched", "native_execution_started"]);
  const expectedHash = format === "html" ? saved.html_sha256 : saved.source_sha256;
  const expectedBytes = format === "html" ? saved.html_bytes : saved.source_bytes;
  if (result.schema !== "motionwright.hyperframes-source-export/1" || !sameValue(result.source, source)
    || result.fingerprint !== saved.fingerprint || result.format !== format || result.path !== path
    || result.content_sha256 !== expectedHash || result.size_bytes !== expectedBytes) {
    throw new Error("The export acknowledgement differs from the inspected source. Inspect the destination before another export.");
  }
  inert(result);
  return result as unknown as HyperframesSourceExport;
}
