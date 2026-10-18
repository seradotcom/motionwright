import { invoke } from "@tauri-apps/api/core";
import type { Project } from "./types";

export const DEFAULT_PROJECT_PAGE_SIZE = 20;
export const MAX_PROJECT_PAGE_SIZE = 100;
export const PROJECT_SELECTION_KEY = "motionwright.project-library.selection.v1";
const SELECTION_SCHEMA = "motionwright.project-selection/1";
const MAX_SELECTION_BYTES = 1_024;
const MAX_U64 = 18_446_744_073_709_551_615n;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Stored index metadata. A successful open, not this row, validates a document. */
export interface ProjectLibrarySummary {
  id: string;
  generation: string;
  revision: string;
  title: string;
  updated_at: string;
}

export interface ProjectLibraryCursor {
  updated_at: string;
  id: string;
}

export interface ProjectLibraryPage {
  items: ProjectLibrarySummary[];
  next_cursor: ProjectLibraryCursor | null;
  truncated: boolean;
}

export interface ListProjectsRequest {
  cursor: ProjectLibraryCursor | null;
  limit: number;
}

export interface OpenProjectRequest {
  project_id: string;
}

export type ProjectLibraryInvoke = (
  command: "list_projects" | "open_project",
  args: { request: ListProjectsRequest | OpenProjectRequest },
) => Promise<unknown>;

const desktopInvoke: ProjectLibraryInvoke = (command, args) => invoke(command, args);

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && actual.every(key => keys.includes(key));
}

function projectId(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) throw new Error("Project identity is invalid.");
  return value.toLowerCase();
}

function cursor(value: unknown): ProjectLibraryCursor {
  if (!record(value) || !exactKeys(value, ["updated_at", "id"]) ||
      typeof value.updated_at !== "string" || value.updated_at.length === 0 ||
      new TextEncoder().encode(value.updated_at).length > 128 || /[\u0000-\u001f\u007f-\u009f]/.test(value.updated_at)) {
    throw new Error("The project page cursor is invalid; refresh the library.");
  }
  return { updated_at: value.updated_at, id: projectId(value.id) };
}

function summary(value: unknown): ProjectLibrarySummary {
  if (!record(value) || !exactKeys(value, ["id", "generation", "revision", "title", "updated_at"]) ||
      typeof value.revision !== "string" || !/^(0|[1-9][0-9]{0,19})$/.test(value.revision) ||
      BigInt(value.revision) > MAX_U64 || typeof value.title !== "string" ||
      typeof value.updated_at !== "string") {
    throw new Error("The project index returned an invalid entry.");
  }
  const at = cursor({ id: value.id, updated_at: value.updated_at });
  return {
    id: at.id,
    generation: projectId(value.generation),
    revision: value.revision,
    title: value.title,
    updated_at: at.updated_at,
  };
}

export function parseProjectLibraryPage(value: unknown): ProjectLibraryPage {
  if (!record(value) || !exactKeys(value, ["items", "next_cursor", "truncated"]) ||
      !Array.isArray(value.items) || value.items.length > MAX_PROJECT_PAGE_SIZE ||
      typeof value.truncated !== "boolean") {
    throw new Error("The project index returned an invalid page.");
  }
  const items = value.items.map(summary);
  if (new Set(items.map(item => item.id)).size !== items.length) {
    throw new Error("The project index returned a duplicate project identity.");
  }
  const next = value.next_cursor === null ? null : cursor(value.next_cursor);
  const last = items.at(-1);
  if (value.truncated !== (next !== null) ||
      (next && (!last || last.id !== next.id || last.updated_at !== next.updated_at))) {
    throw new Error("The project page continuation is inconsistent; refresh the library.");
  }
  return { items, next_cursor: next, truncated: value.truncated };
}

/** Uses only the real desktop invoke. Unavailable runtimes reject; no fixture fallback. */
export async function listProjectLibrary(
  request: Partial<ListProjectsRequest> = {},
  call: ProjectLibraryInvoke = desktopInvoke,
): Promise<ProjectLibraryPage> {
  const limit = request.limit === undefined ? DEFAULT_PROJECT_PAGE_SIZE : request.limit;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_PROJECT_PAGE_SIZE) {
    throw new Error("Project pages must contain between 1 and 100 entries.");
  }
  const after = request.cursor == null ? null : cursor(request.cursor);
  const page = parseProjectLibraryPage(await call("list_projects", { request: { cursor: after, limit } }));
  if (page.items.length > limit || (after && page.next_cursor &&
      page.next_cursor.id === after.id && page.next_cursor.updated_at === after.updated_at)) {
    throw new Error("The project page did not advance; refresh the library.");
  }
  return page;
}

/** The returned Project is admitted by the application's existing mutation gate. */
export async function openProjectFromLibrary(
  requestedId: string,
  call: ProjectLibraryInvoke = desktopInvoke,
): Promise<Project> {
  const id = projectId(requestedId);
  const value = await call("open_project", { request: { project_id: id } });
  return openedProject(value, id);
}

function openedProject(value: unknown, id: string): Project {
  // Desktop validates the domain. Check the envelope here to avoid publishing
  // a different identity or a revision that JavaScript cannot represent.
  if (!record(value) || projectId(value.id) !== id) {
    throw new Error("The opened project differs from the requested identity.");
  }
  projectId(value.generation);
  projectId(value.active_branch);
  if (!Number.isSafeInteger(value.revision) || (value.revision as number) < 0 ||
      typeof value.title !== "string" || !Array.isArray(value.scenes) ||
      !Array.isArray(value.assets) || !Array.isArray(value.deliverables) ||
      !Array.isArray(value.branches) || !Array.isArray(value.branch_workspaces)) {
    throw new Error("The opened project has an invalid or unsupported revision envelope.");
  }
  // Loading neither writes a preference nor changes the active UI document.
  // A caller must first accept this result through its existing coordinator.
  return value as unknown as Project;
}

export interface ProjectCreationIntent { request_id: string; title: string; }
export interface ProjectCreationOutcome { request_id: string; project: Project; replayed: boolean; }
export interface RetainedProjectCreation {
  schema: "motionwright.project-creation-intent/1";
  intent: ProjectCreationIntent;
  createdAt: number;
  phase: "unconfirmed" | "observed";
  projectId: string | null;
}
export interface ProjectCreationCopy {
  key: string;
  raw: string;
  value: RetainedProjectCreation | null;
  error: string | null;
}
export type ProjectCreationStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export type ProjectCreationInvoke = (
  command: "issue_project_creation_grant" | "create_project" | "recover_project_creation",
  args: { request: ProjectCreationIntent | { intent: ProjectCreationIntent; effect_grant: string } },
) => Promise<unknown>;
export const PROJECT_CREATION_PREFIX = "motionwright.project-creation.intent.v1:";
const MAX_CREATION_COPIES = 128;
const MAX_CREATION_BYTES = 4_096;
const NIL_UUID = "00000000-0000-0000-0000-000000000000";
const creationInvoke: ProjectCreationInvoke = (command, args) => invoke(command, args);

function creationStorage(): ProjectCreationStorage {
  try { return window.localStorage; }
  catch { throw new Error("The creation request cannot be retained locally. Keep the title and restore local storage before creating a project."); }
}

function creationIntent(value: unknown): ProjectCreationIntent {
  if (!record(value) || !exactKeys(value, ["request_id", "title"]) || typeof value.title !== "string"
      || !value.title.trim() || new TextEncoder().encode(value.title).length > 200) {
    throw new Error("A creation request needs its original title of at most 200 bytes.");
  }
  const id = projectId(value.request_id);
  if (id === NIL_UUID) throw new Error("A creation request needs a non-nil operation identity.");
  return { request_id: id, title: value.title };
}

function decodeCreation(key: string, raw: string): RetainedProjectCreation {
  if (new TextEncoder().encode(raw).length > MAX_CREATION_BYTES) throw new Error("The retained creation request exceeds its supported size.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exactKeys(value, ["schema", "intent", "createdAt", "phase", "projectId"])
      || value.schema !== "motionwright.project-creation-intent/1" || !Number.isSafeInteger(value.createdAt)
      || (value.createdAt as number) < 0 || !["unconfirmed", "observed"].includes(value.phase as string)) {
    throw new Error("This retained creation request is unreadable. Its original bytes were preserved.");
  }
  const intent = creationIntent(value.intent);
  if (key !== PROJECT_CREATION_PREFIX + intent.request_id
      || (value.phase === "unconfirmed" ? value.projectId !== null
        : typeof value.projectId !== "string" || projectId(value.projectId) === NIL_UUID)) {
    throw new Error("The retained creation identity is inconsistent. Its original bytes were preserved.");
  }
  return { schema: value.schema, intent, createdAt: value.createdAt as number,
    phase: value.phase as RetainedProjectCreation["phase"], projectId: value.projectId as string | null };
}

export function readProjectCreations(storage: ProjectCreationStorage = creationStorage()): { copies: ProjectCreationCopy[]; limited: boolean } {
  const copies: ProjectCreationCopy[] = [];
  let limited = storage.length > 4_096;
  const count = Math.min(storage.length, 4_096);
  for (let index = 0; index < count; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(PROJECT_CREATION_PREFIX)) continue;
    if (copies.length >= MAX_CREATION_COPIES) { limited = true; break; }
    const raw = storage.getItem(key);
    if (raw === null) continue;
    try { copies.push({ key, raw, value: decodeCreation(key, raw), error: null }); }
    catch (error) { copies.push({ key, raw, value: null, error: String(error) }); }
  }
  copies.sort((a, b) => (b.value?.createdAt ?? 0) - (a.value?.createdAt ?? 0));
  return { copies, limited };
}

function requireCreationCopy(copy: ProjectCreationCopy, storage: ProjectCreationStorage): RetainedProjectCreation {
  if (storage.getItem(copy.key) !== copy.raw) throw new Error("This retained creation changed in another window. Refresh its entry before checking it.");
  return decodeCreation(copy.key, copy.raw);
}

function creationOutcome(value: unknown, intent: ProjectCreationIntent): ProjectCreationOutcome {
  if (!record(value) || !exactKeys(value, ["request_id", "project", "replayed"])
      || value.request_id !== intent.request_id || typeof value.replayed !== "boolean" || !record(value.project)) {
    throw new Error("The creation result does not match the retained request. Check the original request; no retry was made.");
  }
  const original = openedProject(value.project, projectId(value.project.id));
  if (original.id === NIL_UUID || original.generation === NIL_UUID || original.revision !== 0 || original.title !== intent.title) {
    throw new Error("The original creation result has an inconsistent identity, title or revision. No current project was replaced.");
  }
  return { request_id: intent.request_id, project: original, replayed: value.replayed };
}

function observeCreation(copy: ProjectCreationCopy, outcome: ProjectCreationOutcome, storage: ProjectCreationStorage) {
  try {
    const old = requireCreationCopy(copy, storage);
    const value: RetainedProjectCreation = { ...old, phase: "observed", projectId: outcome.project.id };
    const raw = JSON.stringify(value);
    storage.setItem(copy.key, raw);
    if (storage.getItem(copy.key) !== raw) throw new Error("The local creation receipt changed while it was being updated.");
    return { copy: { key: copy.key, raw, value, error: null } as ProjectCreationCopy, warning: null };
  } catch (error) {
    return { copy, warning: "Creation was observed, but its local record could not be updated. " + String(error) };
  }
}

/** Only a new explicit action enters this function. A loaded record can use
 * recoverRetainedProjectCreation below, which never issues a grant or creates.
 * localStorage is a continuity copy; the Store remains the receipt authority. */
export async function createProjectWithRetainedIntent(
  title: string,
  call: ProjectCreationInvoke = creationInvoke,
  storage: ProjectCreationStorage = creationStorage(),
): Promise<{ outcome: ProjectCreationOutcome; copy: ProjectCreationCopy; warning: string | null }> {
  const existing = readProjectCreations(storage);
  if (existing.limited || existing.copies.some(copy => !copy.value || copy.value.phase === "unconfirmed")) {
    throw new Error("Review the retained creation request before creating another project. Existing projects remain available.");
  }
  const intent = creationIntent({ request_id: crypto.randomUUID(), title });
  const key = PROJECT_CREATION_PREFIX + intent.request_id;
  const value: RetainedProjectCreation = { schema: "motionwright.project-creation-intent/1", intent,
    createdAt: Date.now(), phase: "unconfirmed", projectId: null };
  const raw = JSON.stringify(value);
  if (storage.getItem(key) !== null) throw new Error("This creation identity already exists. No operation was dispatched.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The original creation request could not be retained. No operation was dispatched.");
  const copy: ProjectCreationCopy = { key, raw, value, error: null };
  // The barrier above precedes both IPC calls. A grant failure, timeout or lost
  // result leaves the same original request available only for read recovery.
  const grant = await call("issue_project_creation_grant", { request: structuredClone(intent) });
  if (!record(grant) || projectId(grant.token) === NIL_UUID || grant.effect !== "project_create"
      || grant.project_id !== null || grant.generation !== null || grant.revision !== null
      || projectId(grant.workspace_session) === NIL_UUID || grant.one_time !== true
      || typeof grant.expires_in_seconds !== "number" || grant.expires_in_seconds <= 0) {
    throw new Error("The desktop returned an invalid creation grant. The original request was kept for review.");
  }
  requireCreationCopy(copy, storage);
  const result = creationOutcome(await call("create_project", { request: { intent: structuredClone(intent), effect_grant: grant.token as string } }), intent);
  return { outcome: result, ...observeCreation(copy, result, storage) };
}

export async function recoverRetainedProjectCreation(
  copy: ProjectCreationCopy,
  call: ProjectCreationInvoke = creationInvoke,
  storage: ProjectCreationStorage = creationStorage(),
): Promise<{ outcome: ProjectCreationOutcome | null; copy: ProjectCreationCopy; warning: string | null }> {
  const retained = requireCreationCopy(copy, storage);
  const result = await call("recover_project_creation", { request: structuredClone(retained.intent) });
  if (result === null) return { outcome: null, copy,
    warning: "No creation receipt was found for this request. It remains unconfirmed; this does not authorize another creation attempt." };
  const outcome = creationOutcome(result, retained.intent);
  return { outcome, ...observeCreation(copy, outcome, storage) };
}

/** Explicitly remove a continuity copy, never a canonical project or receipt. */
export function discardProjectCreationCopy(copy: ProjectCreationCopy, storage: ProjectCreationStorage = creationStorage()): void {
  if (storage.getItem(copy.key) !== copy.raw) throw new Error("The retained creation changed. Refresh it before removing this local copy.");
  storage.removeItem(copy.key);
  if (storage.getItem(copy.key) !== null) throw new Error("Removal of the local creation copy could not be verified.");
}

/** A later page may repeat a row moved by a concurrent edit; retain one row. */
export function appendProjectLibraryItems(
  previous: readonly ProjectLibrarySummary[],
  incoming: readonly ProjectLibrarySummary[],
): ProjectLibrarySummary[] {
  const items = new Map(previous.map(item => [item.id, item]));
  for (const item of incoming) items.set(item.id, item);
  return [...items.values()];
}

export type ProjectSelectionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type ProjectSelectionRead =
  | { status: "empty"; raw: null }
  | { status: "remembered"; raw: string; projectId: string }
  | { status: "invalid"; raw: string; message: string }
  | { status: "unavailable"; raw: null; message: string };

export interface ProjectSelectionUpdate {
  status: "saved" | "cleared" | "conflict" | "blocked" | "unavailable";
  observed: ProjectSelectionRead;
  message: string | null;
}

function browserSelectionStorage(): ProjectSelectionStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** A UUID-only view preference, never a cached Project, revision, or effect grant. */
export function readProjectSelection(
  storage: ProjectSelectionStorage | null = browserSelectionStorage(),
): ProjectSelectionRead {
  if (!storage) return { status: "unavailable", raw: null, message: "Remembered project selection is unavailable." };
  let raw: string | null;
  try {
    raw = storage.getItem(PROJECT_SELECTION_KEY);
  } catch {
    return { status: "unavailable", raw: null, message: "Remembered project selection could not be read." };
  }
  if (raw === null) return { status: "empty", raw: null };
  try {
    if (new TextEncoder().encode(raw).length > MAX_SELECTION_BYTES) throw new Error("oversized");
    const value: unknown = JSON.parse(raw);
    if (!record(value) || !exactKeys(value, ["schema", "project_id"]) || value.schema !== SELECTION_SCHEMA) {
      throw new Error("unsupported selection");
    }
    return { status: "remembered", raw, projectId: projectId(value.project_id) };
  } catch {
    // Do not replace or erase unreadable preference bytes during startup.
    return { status: "invalid", raw, message: "The remembered selection is unreadable. Choose a project explicitly; the stored preference has been retained." };
  }
}

function selectionConflict(observed: ProjectSelectionRead): ProjectSelectionUpdate {
  return {
    status: "conflict", observed,
    message: "The remembered selection changed. Refresh it before trying to remember another project.",
  };
}

/**
 * Call only after open/import/create has been admitted by the existing App gate.
 * `before` is the preference observed before that operation started. This is a
 * checked preference write, not a cross-window atomic CAS or project authority.
 * Storage quota/availability failures must not undo a successful project open.
 */
export function rememberOpenedProject(
  opened: Pick<Project, "id">,
  before: ProjectSelectionRead,
  storage: ProjectSelectionStorage | null = browserSelectionStorage(),
): ProjectSelectionUpdate {
  const id = projectId(opened.id);
  if (before.status === "invalid" || before.status === "unavailable") {
    return { status: "blocked", observed: before, message: before.message };
  }
  const current = readProjectSelection(storage);
  if (current.status === "unavailable") return { status: "unavailable", observed: current, message: current.message };
  if (current.status === "remembered" && current.projectId === id) {
    return { status: "saved", observed: current, message: null };
  }
  if (current.raw !== before.raw) return selectionConflict(current);
  const raw = JSON.stringify({ schema: SELECTION_SCHEMA, project_id: id });
  try {
    storage!.setItem(PROJECT_SELECTION_KEY, raw);
  } catch {
    return { status: "unavailable", observed: readProjectSelection(storage), message: "The opened project could not be remembered." };
  }
  const observed = readProjectSelection(storage);
  if (observed.status === "unavailable") return { status: "unavailable", observed, message: "The remembered selection could not be verified." };
  if (observed.raw !== raw) return selectionConflict(observed);
  return { status: "saved", observed, message: null };
}

/** Explicitly forgets only the view preference; it never deletes a project. */
export function forgetProjectSelection(
  before: ProjectSelectionRead,
  storage: ProjectSelectionStorage | null = browserSelectionStorage(),
): ProjectSelectionUpdate {
  if (before.status === "unavailable") return { status: "blocked", observed: before, message: before.message };
  const current = readProjectSelection(storage);
  if (current.status === "unavailable") return { status: "unavailable", observed: current, message: current.message };
  if (current.raw !== before.raw) return selectionConflict(current);
  try {
    if (current.raw !== null) storage!.removeItem(PROJECT_SELECTION_KEY);
  } catch {
    return { status: "unavailable", observed: readProjectSelection(storage), message: "The remembered selection could not be cleared." };
  }
  const observed = readProjectSelection(storage);
  if (observed.status === "unavailable") return { status: "unavailable", observed, message: "Clearing the remembered selection could not be verified." };
  if (observed.status !== "empty") return selectionConflict(observed);
  return { status: "cleared", observed, message: null };
}
