import { invoke } from "@tauri-apps/api/core";
import { bootstrap } from "./api";
import { openProjectFromLibrary } from "./projectLibrary";
import {
  canonicalRevision, compareDecimalRevision, projectionUuid, sameProjectionRevision, validProjectionRevision,
  type ProjectionRevision, type ProjectViewRevision,
} from "./projectMutation";
import type { Bootstrap, CanvasNode, RationalTime, RendererKind, SceneStatus } from "./types";

export type ProjectionIdentity = ProjectionRevision;
export interface ProjectionHead { identity: ProjectionIdentity; schema_version: number; title: string; scene_count: number; }
export interface ProjectionSceneRef { ordinal: number; id: string; }
export interface ProjectionSceneCursor { identity: ProjectionIdentity; next_ordinal: number; }
export interface ProjectionNodeCursor extends ProjectionSceneCursor { scene: ProjectionSceneRef; }
export interface ProjectionSceneSummary extends ProjectionSceneRef {
  name: string; start: RationalTime; duration: RationalTime; renderer: RendererKind; status: SceneStatus;
  beat_count: number; node_count: number;
}
export interface ProjectionScenePage {
  identity: ProjectionIdentity; total_scenes: number; start_ordinal: number;
  items: ProjectionSceneSummary[]; next_cursor: ProjectionSceneCursor | null;
}
export interface ProjectionNodePage {
  identity: ProjectionIdentity; scene: ProjectionSceneRef; total_nodes: number; start_ordinal: number;
  items: Readonly<CanvasNode>[]; next_cursor: ProjectionNodeCursor | null;
}
export type ProjectOpenMode = "auto" | "pages" | "editor";
export type StudioProjectView =
  | { kind: "full"; boot: Bootstrap }
  | { kind: "projection"; head: ProjectionHead; scenes: ProjectionScenePage; native_sdk: Bootstrap["native_sdk"] };

export const PROJECT_AUTO_SCENE_THRESHOLD = 64;
export const PROJECT_SCENE_PAGE_SIZE = 64;
export const PROJECT_NODE_PAGE_SIZE = 128;
const MAX_RESPONSE_BYTES = 1024 * 1024;
const MAX_EDITOR_REVISION = "9007199254740991";
type ProjectionCommand = "project_projection_head" | "project_scene_page" | "project_scene_nodes";
export type ProjectionInvoke = (command: ProjectionCommand, args: { request: unknown }) => Promise<unknown>;
const desktopInvoke: ProjectionInvoke = (command, args) => invoke(command, args);

export type ProjectProjectionFailure =
  | { code: "invalid_request_or_content" | "not_found" | "generation_changed" | "unavailable" }
  | { code: "too_large"; limit: number }
  | { code: "stale_revision"; expected: string; actual: string };

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function keys(value: Record<string, unknown>, fields: string[]): boolean {
  return Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
}
function count(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 0xffff_ffff;
}
function sameScene(a: ProjectionSceneRef, b: ProjectionSceneRef): boolean {
  return a.ordinal === b.ordinal && a.id.toLowerCase() === b.id.toLowerCase();
}
function sceneRef(value: unknown): value is ProjectionSceneRef {
  return record(value) && keys(value, ["ordinal", "id"]) && count(value.ordinal) && projectionUuid(value.id);
}
function fraction(value: unknown): value is RationalTime {
  return record(value) && keys(value, ["num", "den"]) && typeof value.num === "string" && typeof value.den === "string"
    && /^(0|[1-9][0-9]{0,19})$/.test(value.num) && /^[1-9][0-9]{0,19}$/.test(value.den);
}
function failure(value: unknown): value is ProjectProjectionFailure {
  if (!record(value)) return false;
  switch (value.code) {
    case "invalid_request_or_content": case "not_found": case "generation_changed": case "unavailable":
      return keys(value, ["code"]);
    case "too_large": return keys(value, ["code", "limit"]) && Number.isSafeInteger(value.limit) && (value.limit as number) > 0;
    case "stale_revision": return keys(value, ["code", "expected", "actual"]) && canonicalRevision(value.expected) && canonicalRevision(value.actual);
    default: return false;
  }
}
export class ProjectProjectionReadError extends Error {
  readonly failure: ProjectProjectionFailure | null;
  constructor(reason: unknown) {
    const known = failure(reason) ? reason : null;
    const messages = {
      invalid_request_or_content: "The saved page could not be read because its request or content is invalid.",
      too_large: "This page exceeds the saved-page size limit. Its contents were not truncated.",
      not_found: "The requested saved project is unavailable.",
      stale_revision: "The saved project changed. Reload its saved pages before continuing.",
      generation_changed: "The saved project was restored or replaced. Reload its saved pages before continuing.",
      unavailable: "The local project store is unavailable. You can retry this read.",
    };
    super(known ? messages[known.code] : "The saved-page response could not be verified. The current view was preserved.");
    this.name = "ProjectProjectionReadError";
    this.failure = known;
  }
}
function rejectPage(): never { throw new ProjectProjectionReadError(null); }
function responseBudget(value: unknown): void {
  try { if (new TextEncoder().encode(JSON.stringify(value)).length > MAX_RESPONSE_BYTES) rejectPage(); }
  catch (reason) { if (reason instanceof ProjectProjectionReadError) throw reason; rejectPage(); }
}
async function read(command: ProjectionCommand, request: unknown, call: ProjectionInvoke): Promise<unknown> {
  try { return await call(command, { request }); }
  catch (reason) { throw new ProjectProjectionReadError(reason); }
}
function requireIdentity(value: unknown, expected: ProjectionIdentity): asserts value is ProjectionIdentity {
  if (!validProjectionRevision(value) || !sameProjectionRevision(value, expected)) rejectPage();
}
function validCursor(value: unknown, identity: ProjectionIdentity, next: number): value is ProjectionSceneCursor {
  return record(value) && keys(value, ["identity", "next_ordinal"]) && validProjectionRevision(value.identity)
    && sameProjectionRevision(value.identity, identity) && value.next_ordinal === next;
}
function requestStart(cursor: ProjectionSceneCursor | null, identity: ProjectionIdentity, total: number, limit: number, maximum: number): number {
  if (!validProjectionRevision(identity) || !count(total) || !Number.isInteger(limit) || limit < 1 || limit > maximum) rejectPage();
  if (cursor !== null && (!validProjectionRevision(cursor.identity) || !sameProjectionRevision(cursor.identity, identity)
      || !count(cursor.next_ordinal) || cursor.next_ordinal > total)) rejectPage();
  return cursor?.next_ordinal ?? 0;
}

export async function readProjectionHead(projectId: string, call: ProjectionInvoke = desktopInvoke): Promise<ProjectionHead> {
  if (!projectionUuid(projectId)) rejectPage();
  const value = await read("project_projection_head", { project_id: projectId.toLowerCase() }, call);
  responseBudget(value);
  if (!record(value) || !keys(value, ["identity", "schema_version", "title", "scene_count"])
      || !validProjectionRevision(value.identity) || value.identity.project_id.toLowerCase() !== projectId.toLowerCase()
      || !count(value.schema_version) || value.schema_version === 0 || typeof value.title !== "string" || !count(value.scene_count)) rejectPage();
  return value as unknown as ProjectionHead;
}

export async function readProjectionScenes(head: ProjectionHead, cursor: ProjectionSceneCursor | null = null,
  limit = PROJECT_SCENE_PAGE_SIZE, call: ProjectionInvoke = desktopInvoke): Promise<ProjectionScenePage> {
  const start = requestStart(cursor, head.identity, head.scene_count, limit, PROJECT_SCENE_PAGE_SIZE);
  const value = await read("project_scene_page", { identity: head.identity, cursor, limit }, call);
  responseBudget(value);
  if (!record(value) || !keys(value, ["identity", "total_scenes", "start_ordinal", "items", "next_cursor"])) rejectPage();
  requireIdentity(value.identity, head.identity);
  const end = Math.min(start + limit, head.scene_count);
  if (value.total_scenes !== head.scene_count || value.start_ordinal !== start || !Array.isArray(value.items)
      || value.items.length !== end - start) rejectPage();
  const ids = new Set<string>();
  for (let index = 0; index < value.items.length; index++) {
    const item: unknown = value.items[index];
    if (!record(item) || !keys(item, ["ordinal", "id", "name", "start", "duration", "renderer", "status", "beat_count", "node_count"])
        || item.ordinal !== start + index || !projectionUuid(item.id) || ids.has(item.id.toLowerCase())
        || typeof item.name !== "string" || !fraction(item.start) || !fraction(item.duration)
        || item.duration.num === "0" || !["motion-canvas", "mlt", "blender", "manim-community", "remotion", "manim-gl"].includes(item.renderer as string)
        || !["draft", "review", "approved", "needs_work"].includes(item.status as string)
        || !count(item.beat_count) || !count(item.node_count)) rejectPage();
    ids.add(item.id.toLowerCase());
  }
  if (end < head.scene_count ? !validCursor(value.next_cursor, head.identity, end) : value.next_cursor !== null) rejectPage();
  return value as unknown as ProjectionScenePage;
}

export async function readProjectionNodes(head: ProjectionHead, scene: ProjectionSceneSummary,
  cursor: ProjectionNodeCursor | null = null, limit = PROJECT_NODE_PAGE_SIZE,
  call: ProjectionInvoke = desktopInvoke): Promise<ProjectionNodePage> {
  const ref: ProjectionSceneRef = { ordinal: scene.ordinal, id: scene.id };
  if (!sceneRef(ref) || ref.ordinal >= head.scene_count || (cursor !== null && (!sceneRef(cursor.scene) || !sameScene(cursor.scene, ref)))) rejectPage();
  const start = requestStart(cursor, head.identity, scene.node_count, limit, PROJECT_NODE_PAGE_SIZE);
  const value = await read("project_scene_nodes", { identity: head.identity, scene: ref, cursor, limit }, call);
  responseBudget(value);
  if (!record(value) || !keys(value, ["identity", "scene", "total_nodes", "start_ordinal", "items", "next_cursor"])) rejectPage();
  requireIdentity(value.identity, head.identity);
  const end = Math.min(start + limit, scene.node_count);
  if (!sceneRef(value.scene) || !sameScene(value.scene, ref) || value.total_nodes !== scene.node_count
      || value.start_ordinal !== start || !Array.isArray(value.items) || value.items.length !== end - start) rejectPage();
  const ids = new Set<string>();
  // Desktop validates each full node. Admit the fields rendered here while
  // retaining the entire original payload, including references off this page.
  for (const item of value.items as unknown[]) {
    if (!record(item) || !projectionUuid(item.id) || ids.has(item.id.toLowerCase())
        || typeof item.name !== "string" || typeof item.kind !== "string"
        || !(item.parent_id === null || projectionUuid(item.parent_id)) || !Array.isArray(item.relations)
        || !item.relations.every((relation: unknown) => record(relation) && projectionUuid(relation.id)
          && typeof relation.kind === "string" && projectionUuid(relation.target_id))) rejectPage();
    ids.add(item.id.toLowerCase());
  }
  if (end < scene.node_count) {
    const next = value.next_cursor;
    if (!record(next) || !keys(next, ["identity", "scene", "next_ordinal"])
        || !validProjectionRevision(next.identity) || !sameProjectionRevision(next.identity, head.identity)
        || !sceneRef(next.scene) || !sameScene(next.scene, ref) || next.next_ordinal !== end) rejectPage();
  } else if (value.next_cursor !== null) rejectPage();
  return value as unknown as ProjectionNodePage;
}

export function projectViewRevision(view: StudioProjectView | null): ProjectViewRevision | null {
  if (view === null) return null;
  return view.kind === "full" ? { kind: "full", project: view.boot.project } : { kind: "projection", identity: view.head.identity };
}
export function projectViewId(view: StudioProjectView): string {
  return view.kind === "full" ? view.boot.project.id : view.head.identity.project_id;
}

/** Scene count selects a UI route; it does not bound document bytes or nodes. */
export async function openSavedProjectView(projectId: string, mode: ProjectOpenMode = "auto",
  onShell?: (sdk: Bootstrap["native_sdk"]) => void): Promise<StudioProjectView> {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Saved projects require the local desktop runtime.");
  }
  if (!projectionUuid(projectId)) rejectPage();
  const shell = await bootstrap(null);
  if (shell.native_sdk.mode !== "tauri") rejectPage();
  onShell?.(shell.native_sdk);
  const head = await readProjectionHead(projectId);
  if (mode === "pages" || (mode === "auto" && head.scene_count > PROJECT_AUTO_SCENE_THRESHOLD)) {
    const scenes = await readProjectionScenes(head);
    return { kind: "projection", head, scenes, native_sdk: shell.native_sdk };
  }
  if (compareDecimalRevision(head.identity.revision, MAX_EDITOR_REVISION) > 0) {
    throw new Error("This saved revision cannot be represented by the complete editor. Choose Browse pages to inspect it.");
  }
  const project = await openProjectFromLibrary(projectId);
  const identity = { project_id: project.id, generation: project.generation, revision: String(project.revision) };
  if (!validProjectionRevision(identity) || !sameProjectionRevision(identity, head.identity) || project.scenes.length !== head.scene_count) rejectPage();
  return { kind: "full", boot: { project, native_sdk: shell.native_sdk } };
}
