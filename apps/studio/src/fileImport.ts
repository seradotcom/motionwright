import type { Project } from "./types";

export type FileImportIntent =
  | { kind: "asset"; name: string; media_type: string }
  | { kind: "voice"; name: string; media_type: string; label: string };

export interface FileImportInput {
  kind: "asset" | "voice";
  path: string;
  name?: string | null;
  media_type?: string | null;
  label?: string | null;
}

type SavedInput = Required<FileImportInput>;
interface FileImportStamp { project_id: string; generation: string; revision: number; }
export interface FileImportPrepareRequest extends FileImportStamp, SavedInput {}
export interface PreparedFileImport extends FileImportStamp {
  schema: "motionwright.file-import-preparation/1";
  path: string;
  intent: FileImportIntent;
}
export interface FileImportRecoveryRequest extends FileImportStamp {
  request_id: string;
  intent: FileImportIntent;
}
export interface FileImportDispatchRequest extends FileImportRecoveryRequest {
  path: string;
  effect_grant: string;
}
export interface FileImportCopy {
  schema: "motionwright.file-import-intent/1";
  request_id: string;
  created_at: number;
  preparation: PreparedFileImport;
  input: SavedInput;
}
export interface RetainedFileImport { key: string; raw: string; copy: FileImportCopy; }
export type FileImportEntry =
  | { kind: "retained"; retained: RetainedFileImport }
  | { kind: "invalid"; key: string; raw: string; error: string };
export interface FileImportsRead {
  copies: FileImportEntry[];
  limited: boolean;
  error: string | null;
}
export type FileImportRead =
  | { kind: "empty" }
  | { kind: "retained"; retained: RetainedFileImport }
  | { kind: "invalid"; key: string; raw: string; error: string }
  | { kind: "unreadable"; error: string };
export interface FileImportReceipt {
  schema: "motionwright.file-import-receipt/1";
  request_id: string;
  intent: FileImportIntent;
  project: Project;
  replayed: boolean;
}
export type FileImportStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface FileImportTransport {
  prepare(request: FileImportPrepareRequest): Promise<unknown>;
  grant(preparation: PreparedFileImport): Promise<unknown>;
  dispatch(kind: FileImportIntent["kind"], request: FileImportDispatchRequest): Promise<unknown>;
  recover(request: FileImportRecoveryRequest): Promise<unknown>;
  current(projectId: string): Promise<Project>;
}

export const FILE_IMPORT_PREFIX = "motionwright.file-import.intent.v1:";
export const FILE_IMPORT_CHANGED = "motionwright:file-import-copy-changed";
const MAX_COPY_BYTES = 65_536;
const MAX_IMPORT_COPIES = 128;
const MAX_STORAGE_KEYS = 4_096;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const NIL_UUID = "00000000-0000-0000-0000-000000000000";
const textBytes = (value: string) => new TextEncoder().encode(value).length;
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const keys = (value: Record<string, unknown>, expected: string[]) =>
  Object.keys(value).length === expected.length && expected.every(key => Object.hasOwn(value, key));

function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value) || value === NIL_UUID) {
    throw new Error("The import needs a canonical, non-empty identity.");
  }
  return value;
}

function boundedText(value: unknown, max: number, allowEmpty = false): string {
  if (typeof value !== "string" || (!allowEmpty && !value.length) || value.includes("\0")
      || value.length > max || textBytes(value) > max) {
    throw new Error("The import contains invalid or unsupported text.");
  }
  return value;
}

function stamp(value: Record<string, unknown>): FileImportStamp {
  if (!Number.isSafeInteger(value.revision) || (value.revision as number) < 0
      || (value.revision as number) >= Number.MAX_SAFE_INTEGER) {
    throw new Error("The import revision cannot be represented safely.");
  }
  return { project_id: uuid(value.project_id), generation: uuid(value.generation), revision: value.revision as number };
}

function savedInput(input: FileImportInput): SavedInput {
  if (input.kind !== "asset" && input.kind !== "voice") throw new Error("The import kind is unsupported.");
  const optional = (value: unknown) => value == null ? null : boundedText(value, MAX_COPY_BYTES, true);
  const result = { kind: input.kind, path: boundedText(input.path, MAX_COPY_BYTES),
    name: optional(input.name), media_type: optional(input.media_type), label: optional(input.label) };
  if (!result.path.trim() || (result.kind === "asset" && result.label !== null)) {
    throw new Error("Choose the original file and its matching import kind.");
  }
  return result;
}

function intent(value: unknown): FileImportIntent {
  if (!record(value) || (value.kind !== "asset" && value.kind !== "voice")
      || !keys(value, value.kind === "voice" ? ["kind", "name", "media_type", "label"] : ["kind", "name", "media_type"])) {
    throw new Error("The prepared file metadata is invalid.");
  }
  const name = boundedText(value.name, 512);
  const media_type = boundedText(value.media_type, 255);
  if (!name.trim()) throw new Error("The prepared file name is empty.");
  if (/\s/.test(media_type) || (value.kind === "voice" && !media_type.startsWith("audio/"))) {
    throw new Error("The prepared media type is invalid.");
  }
  if (value.kind === "asset") return { kind: "asset", name, media_type };
  const label = boundedText(value.label, 256);
  if (!label.trim()) throw new Error("The prepared voice label is empty.");
  return { kind: "voice", name, media_type, label };
}

function sameIntent(a: FileImportIntent, b: FileImportIntent): boolean {
  return a.kind === b.kind && a.name === b.name && a.media_type === b.media_type
    && (a.kind !== "voice" || (b.kind === "voice" && a.label === b.label));
}

function prepared(value: unknown): PreparedFileImport {
  if (!record(value) || !keys(value, ["schema", "project_id", "generation", "revision", "path", "intent"])
      || value.schema !== "motionwright.file-import-preparation/1") {
    throw new Error("The desktop returned an unsupported import preparation.");
  }
  const path = boundedText(value.path, MAX_COPY_BYTES);
  if (path !== path.trim()) throw new Error("The prepared path differs from the selected file.");
  return { schema: value.schema, ...stamp(value), path, intent: intent(value.intent) };
}

function matchesInput(preparation: PreparedFileImport, input: SavedInput): void {
  if (preparation.path !== input.path.trim() || preparation.intent.kind !== input.kind
      || (input.name?.trim() && input.name.trim() !== preparation.intent.name)
      || (input.media_type?.trim() && input.media_type.trim() !== preparation.intent.media_type)
      || (input.kind === "voice" && input.label?.trim()
        && (preparation.intent.kind !== "voice" || preparation.intent.label !== input.label.trim()))) {
    throw new Error("The prepared metadata differs from the selected import. Keep the original input.");
  }
}

function legacyFileImportKey(projectId: string): string { return FILE_IMPORT_PREFIX + uuid(projectId); }
export function fileImportProjectPrefix(projectId: string): string { return legacyFileImportKey(projectId) + ":"; }
export function fileImportKey(projectId: string, requestId: string): string {
  return fileImportProjectPrefix(projectId) + uuid(requestId);
}

function belongsToProject(key: string, projectId: string): boolean {
  // Invalid request suffixes still belong to this exact project namespace.
  // Their original key and bytes remain available for explicit download/removal.
  return key === legacyFileImportKey(projectId) || key.startsWith(fileImportProjectPrefix(projectId));
}

function localStorageForImport(): FileImportStorage {
  try { return window.localStorage; }
  catch { throw new Error("The import request cannot be retained locally. Restore local storage before importing."); }
}

function decode(key: string, raw: string): FileImportCopy {
  if (raw.length > MAX_COPY_BYTES || textBytes(raw) > MAX_COPY_BYTES) throw new Error("The retained import exceeds its supported size. Its original copy was preserved.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !keys(value, ["schema", "request_id", "created_at", "preparation", "input"])
      || value.schema !== "motionwright.file-import-intent/1" || !Number.isSafeInteger(value.created_at)
      || (value.created_at as number) < 0 || !record(value.input)
      || !keys(value.input, ["kind", "path", "name", "media_type", "label"])) {
    throw new Error("The retained import is unreadable. Its original copy was preserved.");
  }
  const preparation = prepared(value.preparation);
  const input = savedInput(value.input as unknown as FileImportInput);
  matchesInput(preparation, input);
  const requestId = uuid(value.request_id);
  if (key !== legacyFileImportKey(preparation.project_id) && key !== fileImportKey(preparation.project_id, requestId)) {
    throw new Error("The retained import identity differs from its key. Its original copy was preserved.");
  }
  return { schema: value.schema, request_id: requestId, created_at: value.created_at as number, preparation, input };
}

/** A bounded view of independent recovery copies, never an atomic storage snapshot. */
export function readFileImports(projectId: string, storage?: FileImportStorage): FileImportsRead {
  const copies: FileImportEntry[] = [];
  let limited = false;
  let error: string | null = null;
  try {
    const legacyKey = legacyFileImportKey(projectId);
    const target = storage ?? localStorageForImport();
    const read = (key: string, raw: string) => {
      try { copies.push({ kind: "retained", retained: { key, raw, copy: decode(key, raw) } }); }
      catch (reason) { copies.push({ kind: "invalid", key, raw, error: errorText(reason) }); }
    };
    // This known legacy location is checked even if unrelated keys exhaust the scan budget.
    const legacyRaw = target.getItem(legacyKey);
    if (legacyRaw !== null) read(legacyKey, legacyRaw);
    const count = target.length;
    if (!Number.isSafeInteger(count) || count < 0) throw new Error("The saved import index could not be read.");
    limited = count > MAX_STORAGE_KEYS;
    const indexed = new Set<string>();
    for (let index = 0; index < Math.min(count, MAX_STORAGE_KEYS); index++) {
      const key = target.key(index);
      if (key === null || indexed.has(key)) { limited = true; continue; }
      indexed.add(key);
      if (key === legacyKey || !belongsToProject(key, projectId)) continue;
      if (copies.length >= MAX_IMPORT_COPIES) { limited = true; break; }
      const raw = target.getItem(key);
      if (raw === null) { limited = true; continue; }
      read(key, raw);
    }
    if (target.length !== count) limited = true;
  } catch (reason) { error = errorText(reason); }
  const entryKey = (entry: FileImportEntry) => entry.kind === "retained" ? entry.retained.key : entry.key;
  const entryTime = (entry: FileImportEntry) => entry.kind === "retained" ? entry.retained.copy.created_at : 0;
  copies.sort((a, b) => entryTime(b) - entryTime(a) || entryKey(a).localeCompare(entryKey(b)));
  return { copies, limited, error };
}

/** Compatibility for a single visible copy; callers choosing entries use readFileImports. */
export function readFileImport(projectId: string, storage?: FileImportStorage): FileImportRead {
  const result = readFileImports(projectId, storage);
  if (result.error !== null || result.limited) {
    return { kind: "unreadable", error: result.error ?? "The saved import list is incomplete. Refresh it before starting an import." };
  }
  if (result.copies.length > 1) return { kind: "unreadable", error: "Choose the original request from the saved import list." };
  return result.copies[0] ?? { kind: "empty" };
}

function changed(projectId: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(FILE_IMPORT_CHANGED, { detail: projectId }));
}

export function assertNoFileImport(projectId: string, storage?: FileImportStorage): void {
  const state = readFileImports(projectId, storage);
  if (state.error !== null) throw new Error(state.error);
  if (state.limited) throw new Error("The saved import list is incomplete. Refresh or review its copies before starting another import.");
  if (state.copies.length !== 0) throw new Error("This project has a retained import. Check its original result or explicitly remove its local recovery copy before starting another.");
}

/** This guards this view's saved intent. It is not a cross-window lock or backend CAS. */
export function requireFileImport(retained: RetainedFileImport, storage?: FileImportStorage): FileImportCopy {
  const saved = (storage ?? localStorageForImport()).getItem(retained.key);
  if (saved !== retained.raw) throw new Error("The retained import changed or became unavailable. No further import was sent.");
  const original = decode(retained.key, retained.raw);
  if (JSON.stringify(original) !== JSON.stringify(retained.copy)) {
    throw new Error("The import in this view differs from its retained original. Reload the recovery copy.");
  }
  return original;
}

export function retainFileImport(
  preparation: PreparedFileImport, input: FileImportInput, storage?: FileImportStorage,
  requestId: string = crypto.randomUUID(), createdAt = Date.now(),
): RetainedFileImport {
  const target = storage ?? localStorageForImport();
  const normalized = prepared(preparation);
  const originalInput = savedInput(input);
  matchesInput(normalized, originalInput);
  const key = fileImportKey(normalized.project_id, requestId);
  const raw = JSON.stringify({ schema: "motionwright.file-import-intent/1", request_id: requestId,
    created_at: createdAt, preparation: normalized, input: originalInput });
  const copy = decode(key, raw);
  // This precheck helps this view, but cannot exclude another window. Distinct
  // operation keys preserve both requests if two windows pass it together.
  assertNoFileImport(normalized.project_id, target);
  if (target.getItem(key) !== null) throw new Error("This import request identity is already retained. No import was dispatched.");
  target.setItem(key, raw);
  const retained = { key, raw, copy };
  requireFileImport(retained, target);
  changed(normalized.project_id);
  return retained;
}

/** Explicit local removal or post-publication cleanup. Never cancels a committed import. */
export function removeFileImport(projectId: string, key: string, expectedRaw: string, storage?: FileImportStorage): void {
  const target = storage ?? localStorageForImport();
  if (!belongsToProject(key, projectId)) throw new Error("The recovery copy key belongs to another project.");
  // Cooperating windows never rewrite an operation key. This comparison is a
  // continuity guard, not atomic compare-and-delete for arbitrary storage writers.
  if (target.getItem(key) !== expectedRaw) throw new Error("The recovery copy changed. Read its current contents before removing it.");
  target.removeItem(key);
  changed(projectId);
  if (target.getItem(key) !== null) throw new Error("The local recovery copy is still present. Its project result was not changed.");
}

function recoveryRequest(copy: FileImportCopy): FileImportRecoveryRequest {
  const { project_id, generation, revision } = copy.preparation;
  return { project_id, generation, revision, request_id: copy.request_id, intent: structuredClone(copy.preparation.intent) };
}

function projectEnvelope(value: unknown): Project {
  if (!record(value) || !Number.isSafeInteger(value.revision) || (value.revision as number) < 0
      || typeof value.title !== "string" || !Array.isArray(value.scenes) || !Array.isArray(value.assets)
      || !Array.isArray(value.deliverables) || !Array.isArray(value.branches) || !Array.isArray(value.branch_workspaces)) {
    throw new Error("The imported project has an invalid revision envelope. Keep its recovery copy.");
  }
  uuid(value.id); uuid(value.generation); uuid(value.active_branch);
  return value as unknown as Project;
}

export function parseFileImportReceipt(value: unknown, copy: FileImportCopy): FileImportReceipt {
  if (!record(value) || !keys(value, ["schema", "request_id", "intent", "project", "replayed"])
      || value.schema !== "motionwright.file-import-receipt/1" || value.request_id !== copy.request_id
      || typeof value.replayed !== "boolean") throw new Error("The import response does not identify the retained request. Keep its recovery copy.");
  const returnedIntent = intent(value.intent);
  const project = projectEnvelope(value.project);
  if (!sameIntent(returnedIntent, copy.preparation.intent) || project.id !== copy.preparation.project_id
      || project.generation !== copy.preparation.generation || project.revision !== copy.preparation.revision + 1) {
    throw new Error("The import receipt differs from the original request. Keep its recovery copy.");
  }
  return { schema: value.schema, request_id: copy.request_id, intent: returnedIntent, project, replayed: value.replayed };
}

async function currentAfterReceipt(receipt: FileImportReceipt, visible: Project, port: FileImportTransport): Promise<Project> {
  const current = projectEnvelope(await port.current(receipt.project.id));
  if (current.id !== receipt.project.id || current.generation !== receipt.project.generation
      || current.revision < Math.max(receipt.project.revision, visible.revision)) {
    throw new Error("The current project could not be reconciled with the import. Keep its recovery copy and reopen the project.");
  }
  return current;
}

export async function importFileWithRetention(
  project: Project, input: FileImportInput, port: FileImportTransport, storage?: FileImportStorage,
): Promise<{ project: Project; retained: RetainedFileImport }> {
  assertNoFileImport(project.id, storage);
  const originalInput = savedInput(input);
  const request = { ...stamp({ project_id: project.id, generation: project.generation, revision: project.revision }), ...originalInput };
  // Preparation checks only the project stamp and lexical metadata, without source I/O or an effect grant.
  const preparation = prepared(await port.prepare(structuredClone(request)));
  if (preparation.project_id !== project.id || preparation.generation !== project.generation || preparation.revision !== project.revision) {
    throw new Error("The prepared import belongs to another project revision.");
  }
  const retained = retainFileImport(preparation, originalInput, storage);
  requireFileImport(retained, storage);
  const grant = await port.grant(structuredClone(preparation));
  if (!record(grant) || grant.effect !== "import_local" || grant.project_id !== project.id
      || grant.generation !== project.generation || grant.revision !== project.revision || grant.one_time !== true) {
    throw new Error("The import grant does not match the original request. Keep its recovery copy.");
  }
  const token = uuid(grant.token);
  const original = requireFileImport(retained, storage);
  const receipt = parseFileImportReceipt(await port.dispatch(original.preparation.intent.kind,
    { ...recoveryRequest(original), path: original.preparation.path, effect_grant: token }), original);
  const current = await currentAfterReceipt(receipt, project, port);
  // The caller clears only after its existing mutation coordinator publishes current.
  return { project: current, retained };
}

export async function recoverFileImportWithRetention(
  project: Project, retained: RetainedFileImport, port: FileImportTransport, storage?: FileImportStorage,
): Promise<Project | null> {
  const original = requireFileImport(retained, storage);
  if (project.id !== original.preparation.project_id || project.generation !== original.preparation.generation) {
    throw new Error("This copy belongs to a different project authority. Preserve or download it; it cannot be used by this project generation.");
  }
  const value = await port.recover(recoveryRequest(original));
  if (value === null) return null;
  return currentAfterReceipt(parseFileImportReceipt(value, original), project, port);
}

/** JSON escaping preserves even damaged UTF-16 localStorage contents for manual recovery. */
export function fileImportDownload(projectId: string, raw: string): string {
  return JSON.stringify({ schema: "motionwright.file-import-local-copy/1", project_id: uuid(projectId), raw }, null, 2) + "\n";
}
