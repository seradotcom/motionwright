import { invoke } from "@tauri-apps/api/core";
import { canonicalRevision, projectionUuid } from "./projectMutation";
import type { PortableBundlePlan, Project } from "./types";

export const BUNDLE_IMPORT_PREFIX = "motionwright.bundle-import-continuity/1/";
type StorageView = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
type Invoke = (command: string, args: Record<string, unknown>) => Promise<unknown>;
export interface BundleImportCommit {
  operation_id: string;
  project_id: string;
  generation: string;
  fingerprint: string;
}
interface RetainedBundleImport {
  schema: "motionwright.bundle-import-continuity/1";
  copyId: string;
  createdAt: number;
  path: string;
  plan: PortableBundlePlan;
}
export interface BundleImportCopy {
  key: string;
  raw: string;
  value: RetainedBundleImport | null;
  error: string | null;
}
const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const digest = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const bytes = (value: string) => new TextEncoder().encode(value).length;
function storageView(): StorageView {
  try { return window.localStorage; }
  catch { throw new Error("The portable import cannot be retained locally. Restore local storage before importing."); }
}
export function checkedBundlePlan(value: unknown): PortableBundlePlan {
  const keys = ["project_id", "title", "source_generation", "revision", "event_count",
    "blob_count", "total_blob_bytes", "rotates_generation", "fingerprint"];
  const hasNotes = record(value) && Object.hasOwn(value, "migration_notes");
  if (hasNotes) keys.push("migration_notes");
  if (!record(value) || !exact(value, keys)
    || (hasNotes && (!Array.isArray(value.migration_notes)
      || value.migration_notes.length === 0 || value.migration_notes.length > 24
      || !value.migration_notes.every(note => typeof note === "string" && note.trim()
        && bytes(note) <= 512 && !/[\u0000-\u001f]/.test(note))))
    || !projectionUuid(value.project_id) || !projectionUuid(value.source_generation)
    || typeof value.title !== "string" || !value.title.trim() || bytes(value.title) > 200
    || !canonicalRevision(value.revision) || !canonicalRevision(value.total_blob_bytes)
    || !count(value.event_count) || !count(value.blob_count) || value.rotates_generation !== true
    || !digest(value.fingerprint)) {
    throw new Error("The portable project inspection is incomplete or unsupported. Inspect the original bundle again.");
  }
  return { ...(value as unknown as PortableBundlePlan) };
}
function retainedPath(path: unknown): string {
  if (typeof path !== "string" || !path.trim() || bytes(path) > 4096 || /[\u0000-\u001f]/.test(path)) {
    throw new Error("Enter the original project folder or archive path.");
  }
  return path.trim();
}
function decode(key: string, raw: string): RetainedBundleImport {
  if (bytes(raw) > 16_384) throw new Error("The retained portable import exceeds its supported size.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "copyId", "createdAt", "path", "plan"])
    || value.schema !== "motionwright.bundle-import-continuity/1" || !projectionUuid(value.copyId)
    || key !== BUNDLE_IMPORT_PREFIX + value.copyId || !count(value.createdAt)) {
    throw new Error("This retained portable import is unreadable. Its original bytes were preserved.");
  }
  return { schema: value.schema, copyId: value.copyId, createdAt: value.createdAt,
    path: retainedPath(value.path), plan: checkedBundlePlan(value.plan) };
}
export function readBundleImports(storage: StorageView = storageView()): { copies: BundleImportCopy[]; limited: boolean } {
  const copies: BundleImportCopy[] = [];
  let limited = storage.length > 4096;
  for (let index = 0; index < Math.min(storage.length, 4096); index++) {
    const key = storage.key(index);
    if (!key?.startsWith(BUNDLE_IMPORT_PREFIX)) continue;
    if (copies.length >= 64) { limited = true; break; }
    const raw = storage.getItem(key);
    if (raw === null) continue;
    try { copies.push({ key, raw, value: decode(key, raw), error: null }); }
    catch (error) { copies.push({ key, raw, value: null, error: String(error) }); }
  }
  copies.sort((a, b) => (b.value?.createdAt ?? 0) - (a.value?.createdAt ?? 0));
  return { copies, limited };
}
function currentCopy(copy: BundleImportCopy, storage: StorageView): RetainedBundleImport {
  if (storage.getItem(copy.key) !== copy.raw) throw new Error("This portable import copy changed in another window. Refresh it.");
  return decode(copy.key, copy.raw);
}
/** Only a new explicit action may dispatch. The UUID names a local continuity
 * copy; it is not a native request identity, grant or proof of a commit. */
export async function importBundleWithRetainedPlan(
  path: string, plan: PortableBundlePlan,
  dispatch: (path: string, fingerprint: string) => Promise<Project>,
  storage: StorageView = storageView(),
): Promise<{ project: Project; copy: BundleImportCopy }> {
  const existing = readBundleImports(storage);
  if (existing.limited || existing.copies.length) {
    throw new Error("Review the retained portable import before starting another. Saved projects remain available.");
  }
  const value: RetainedBundleImport = { schema: "motionwright.bundle-import-continuity/1",
    copyId: crypto.randomUUID(), createdAt: Date.now(), path: retainedPath(path), plan: checkedBundlePlan(plan) };
  const key = BUNDLE_IMPORT_PREFIX + value.copyId, raw = JSON.stringify(value);
  if (storage.getItem(key) !== null) throw new Error("This local import identity is occupied. Nothing was dispatched.");
  storage.setItem(key, raw);
  const copy: BundleImportCopy = { key, raw, value, error: null };
  currentCopy(copy, storage);
  // Dispatch includes the existing one-time owner grant. Both follow retention.
  const project = await dispatch(value.path, value.plan.fingerprint);
  currentCopy(copy, storage);
  if (!project || project.id !== value.plan.project_id || !projectionUuid(project.generation)
    || project.generation === value.plan.source_generation || !Number.isSafeInteger(project.revision)
    || String(project.revision) !== value.plan.revision || !projectionUuid(project.active_branch)) {
    throw new Error("The import reply does not match its inspected project. Check its original result; no import was repeated.");
  }
  return { project, copy };
}
async function recoveryInvoke(command: string, args: Record<string, unknown>): Promise<unknown> {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) {
    throw new Error("Portable import recovery requires the Motionwright desktop runtime.");
  }
  return invoke(command, args);
}
/** Read only the original SQLite/CAS marker. Never reopen the source folder,
 * request a grant, replay an import, or replace a current project document. */
export async function recoverRetainedBundleImport(
  copy: BundleImportCopy, call: Invoke = recoveryInvoke, storage: StorageView = storageView(),
): Promise<BundleImportCommit | null> {
  const value = currentCopy(copy, storage);
  const result = await call("recover_project_bundle_import", {
    request: { project_id: value.plan.project_id, fingerprint: value.plan.fingerprint },
  });
  currentCopy(copy, storage);
  if (result === null) return null;
  if (!record(result) || !exact(result, ["operation_id", "project_id", "generation", "fingerprint"])
    || !projectionUuid(result.operation_id) || result.project_id !== value.plan.project_id
    || !projectionUuid(result.generation) || result.generation === value.plan.source_generation
    || result.fingerprint !== value.plan.fingerprint) {
    throw new Error("The original import binding is inconsistent. Its continuity copy was preserved.");
  }
  return result as unknown as BundleImportCommit;
}
/** Explicit removal only; canonical projects, CAS and commit markers remain. */
export function discardBundleImportCopy(copy: BundleImportCopy, storage: StorageView = storageView()): void {
  if (storage.getItem(copy.key) !== copy.raw) throw new Error("This portable import copy changed. Refresh it before removing it.");
  storage.removeItem(copy.key);
  if (storage.getItem(copy.key) !== null) throw new Error("The local portable import copy could not be removed.");
}
