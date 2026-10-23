import type { Project, Scene } from "./types";
import { requireBoundedNativePng } from "./nativeProgressivePreview";

// Local continuity only. These bytes are never a native grant, receipt, render
// result, review verdict or permission to read a file after reopening Studio.
export const PREVIEW_RECOVERY_DB = "motionwright.preview-recovery.v1";
export const PREVIEW_RECOVERY_STORE = "decoded-frames";
export const PREVIEW_RECOVERY_EVENT = "motionwright:preview-recovery";
export const MAX_RECOVERY_FRAME_BYTES = 24 * 1024 * 1024;
export const MAX_RECOVERY_BYTES = 64 * 1024 * 1024;
export const MAX_RECOVERY_RECORDS = 16;
const OWNER_KEY = "motionwright.preview-recovery.writer.v1";

export interface PreviewRecoveryScope {
  projectId: string;
  generation: string;
  branchId: string;
  sceneId: string;
  profileId: string;
}
export interface DecodedPreviewFrame {
  bytes: Uint8Array;
  frameIndex: number;
  frameCount: number;
  pixelWidth: number;
  pixelHeight: number;
}
export interface RetainedPreviewFrame {
  version: 1;
  id: string;
  writerId: string;
  scope: PreviewRecoveryScope;
  scopeKey: string;
  sourceRevision: number;
  sceneName: string;
  frameIndex: number;
  frameCount: number;
  pixelWidth: number;
  pixelHeight: number;
  capturedAt: number;
  byteLength: number;
  blob: Blob;
  // Empty only for a just-decoded memory copy awaiting its durable readback.
  sha256: string;
}
export interface PreviewRecoveryRead {
  frame: RetainedPreviewFrame | null;
  owned: RetainedPreviewFrame[];
  damagedOwned: RetainedPreviewFrame[];
  issues: string[];
}
export interface PreviewRecoveryWrite {
  frame: RetainedPreviewFrame;
  issue: string | null;
}
class PreviewRecoveryCorruption extends Error {}

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function boundedText(value: unknown, max = 256): value is string {
  return typeof value === "string" && value.length > 0
    && new TextEncoder().encode(value).length <= max && !/[\u0000-\u001f\u007f]/.test(value);
}
function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum && value <= maximum;
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const scopeFields = ["projectId", "generation", "branchId", "sceneId", "profileId"] as const;
export function previewRecoveryScopeKey(scope: PreviewRecoveryScope): string {
  if (!object(scope) || Object.keys(scope).length !== scopeFields.length
      || !scopeFields.every(field => boundedText(scope[field]))) {
    throw new Error("Preview recovery requires an exact project, generation, branch, scene and profile.");
  }
  return JSON.stringify(scopeFields.map(field => scope[field]));
}
export function previewRecoveryScope(
  project: Project, scene: Scene | null, profile: Project["deliverables"][number] | null,
): PreviewRecoveryScope | null {
  if (!scene || !profile || !project.scenes.some(value => value.id === scene.id)
      || !project.deliverables.some(value => value.id === profile.id)) return null;
  const scope = { projectId: project.id, generation: project.generation,
    branchId: project.active_branch, sceneId: scene.id, profileId: profile.id };
  try { previewRecoveryScopeKey(scope); return scope; } catch { return null; }
}

export function previewRecoveryWriter(): string {
  // A document cannot delete another writer's rows. Session storage preserves
  // this writer on reload; copies from a different window remain read-only.
  try {
    const previous = sessionStorage.getItem(OWNER_KEY);
    if (previous && uuid.test(previous)) return previous;
    const next = crypto.randomUUID();
    sessionStorage.setItem(OWNER_KEY, next);
    if (sessionStorage.getItem(OWNER_KEY) === next) return next;
  } catch { /* IndexedDB may still work when sessionStorage is unavailable. */ }
  return crypto.randomUUID();
}

export function captureDecodedPreview(
  scope: PreviewRecoveryScope, sourceRevision: number, sceneName: string,
  decoded: DecodedPreviewFrame, writerId: string,
): RetainedPreviewFrame {
  requireBoundedNativePng(decoded.bytes);
  if (decoded.bytes.length > MAX_RECOVERY_FRAME_BYTES || !integer(sourceRevision, 0)
      || !integer(decoded.frameCount, 1, 72_000) || !integer(decoded.frameIndex, 0, decoded.frameCount - 1)
      || !integer(decoded.pixelWidth, 1, 32_768) || !integer(decoded.pixelHeight, 1, 32_768)
      || !boundedText(sceneName, 1_024) || !uuid.test(writerId)) {
    throw new Error("The decoded preview cannot be retained within the recovery bounds.");
  }
  const bytes = new Uint8Array(decoded.bytes.length);
  bytes.set(decoded.bytes);
  return { version: 1, id: crypto.randomUUID(), writerId,
    scope: { ...scope }, scopeKey: previewRecoveryScopeKey(scope), sourceRevision, sceneName,
    frameIndex: decoded.frameIndex, frameCount: decoded.frameCount,
    pixelWidth: decoded.pixelWidth, pixelHeight: decoded.pixelHeight,
    capturedAt: Date.now(), byteLength: bytes.length,
    blob: new Blob([bytes.buffer], { type: "image/png" }), sha256: "" };
}

const recordFields = ["version", "id", "writerId", "scope", "scopeKey", "sourceRevision", "sceneName",
  "frameIndex", "frameCount", "pixelWidth", "pixelHeight", "capturedAt", "byteLength", "blob", "sha256"];
function requireRecordShape(value: unknown, sealed: boolean): RetainedPreviewFrame {
  if (!object(value) || Object.keys(value).length !== recordFields.length
      || !recordFields.every(field => Object.hasOwn(value, field)) || value.version !== 1
      || typeof value.id !== "string" || !uuid.test(value.id)
      || typeof value.writerId !== "string" || !uuid.test(value.writerId)
      || !integer(value.sourceRevision, 0) || !boundedText(value.sceneName, 1_024)
      || !integer(value.frameCount, 1, 72_000) || !integer(value.frameIndex, 0, Number(value.frameCount) - 1)
      || !integer(value.pixelWidth, 1, 32_768) || !integer(value.pixelHeight, 1, 32_768)
      || !integer(value.capturedAt, 0) || !integer(value.byteLength, 32, MAX_RECOVERY_FRAME_BYTES)
      || !(value.blob instanceof Blob) || value.blob.type !== "image/png"
      || value.blob.size !== value.byteLength || typeof value.sha256 !== "string"
      || (sealed ? !/^[0-9a-f]{64}$/.test(value.sha256) : value.sha256 !== "")) {
    throw new Error("A retained preview is incomplete or corrupt; its stored bytes were preserved.");
  }
  const record = value as unknown as RetainedPreviewFrame;
  if (record.scopeKey !== previewRecoveryScopeKey(record.scope)) {
    throw new Error("A retained preview has inconsistent scope; its stored bytes were preserved.");
  }
  return record;
}
function metadata(record: RetainedPreviewFrame): string {
  return JSON.stringify([record.version, record.id, record.writerId, record.scopeKey, record.sourceRevision,
    record.sceneName, record.frameIndex, record.frameCount, record.pixelWidth, record.pixelHeight,
    record.capturedAt, record.byteLength]);
}
async function digest(record: RetainedPreviewFrame): Promise<string> {
  const bytes = new Uint8Array(await record.blob.arrayBuffer());
  try { requireBoundedNativePng(bytes); }
  catch { throw new PreviewRecoveryCorruption("A retained PNG is damaged; its stored bytes were preserved."); }
  const header = new TextEncoder().encode(metadata(record));
  const payload = new Uint8Array(header.length + 1 + bytes.length);
  payload.set(header); payload[header.length] = 0; payload.set(bytes, header.length + 1);
  if (!crypto.subtle) throw new Error("Preview recovery needs a local SHA-256 verifier.");
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", payload)),
    value => value.toString(16).padStart(2, "0")).join("");
}
export async function sealPreviewRecovery(record: RetainedPreviewFrame): Promise<RetainedPreviewFrame> {
  requireRecordShape(record, false);
  return { ...record, sha256: await digest(record) };
}
export async function verifyPreviewRecovery(
  value: unknown, scope: PreviewRecoveryScope, maximumRevision: number,
): Promise<RetainedPreviewFrame> {
  const record = requireRecordShape(value, true);
  if (record.scopeKey !== previewRecoveryScopeKey(scope)) throw new Error("Preview recovery scope changed.");
  if (!integer(maximumRevision, 0) || record.sourceRevision > maximumRevision) {
    throw new Error("A saved preview belongs to a later revision and was not restored.");
  }
  if (await digest(record) !== record.sha256) {
    throw new PreviewRecoveryCorruption("A retained preview failed its local SHA-256 check; its stored bytes were preserved.");
  }
  return record;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let request: IDBOpenDBRequest;
    try { request = indexedDB.open(PREVIEW_RECOVERY_DB, 1); }
    catch { reject(new Error("Preview recovery storage is unavailable. Existing copies were preserved.")); return; }
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore(PREVIEW_RECOVERY_STORE, { keyPath: "id" });
      store.createIndex("scopeKey", "scopeKey", { unique: false });
    };
    request.onerror = () => {
      settled = true;
      reject(new Error("Preview recovery storage could not open. Existing copies were preserved."));
    };
    request.onblocked = () => {
      settled = true;
      reject(new Error("Another Studio window is holding preview recovery storage. Retry after closing it."));
    };
    request.onsuccess = () => {
      if (settled) request.result.close();
      else resolve(request.result);
    };
  });
}
async function rows(scopeKey: string): Promise<unknown[]> {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(PREVIEW_RECOVERY_STORE, "readonly");
      const request = transaction.objectStore(PREVIEW_RECOVERY_STORE).index("scopeKey")
        .getAll(IDBKeyRange.only(scopeKey), MAX_RECOVERY_RECORDS + 1);
      transaction.oncomplete = () => {
        if (request.result.length > MAX_RECOVERY_RECORDS) reject(new Error("Preview recovery exceeds its record bound."));
        else resolve(request.result as unknown[]);
      };
      transaction.onabort = transaction.onerror = () => reject(new Error("Preview recovery could not be read."));
    });
  } finally { database.close(); }
}
async function one(id: string): Promise<unknown> {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction(PREVIEW_RECOVERY_STORE, "readonly");
      const request = transaction.objectStore(PREVIEW_RECOVERY_STORE).get(id);
      transaction.oncomplete = () => resolve(request.result as unknown);
      transaction.onabort = transaction.onerror = () => reject(new Error("Preview recovery readback failed."));
    });
  } finally { database.close(); }
}
function problem(reason: unknown): string {
  return reason instanceof Error ? reason.message : "Preview recovery failed; existing copies were preserved.";
}
function announce(scopeKey: string): void {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(PREVIEW_RECOVERY_EVENT, { detail: scopeKey }));
}

export class PreviewRecoveryStore {
  readonly writerId: string;
  private readonly observedOwn = new Map<string, RetainedPreviewFrame[]>();
  private readonly createdOwn = new Map<string, RetainedPreviewFrame>();
  constructor(writerId = previewRecoveryWriter()) {
    if (!uuid.test(writerId)) throw new Error("Invalid preview recovery writer.");
    this.writerId = writerId;
  }
  async read(scope: PreviewRecoveryScope, maximumRevision: number): Promise<PreviewRecoveryRead> {
    const candidates = await rows(previewRecoveryScopeKey(scope));
    const valid: RetainedPreviewFrame[] = [], damagedOwned: RetainedPreviewFrame[] = [], issues: string[] = [];
    for (const raw of candidates) {
      try { valid.push(await verifyPreviewRecovery(raw, scope, maximumRevision)); }
      catch (reason) {
        issues.push(problem(reason));
        // Only a well-formed, exact-scope own envelope with damaged bytes/hash
        // can be offered for explicit removal. Unknown/foreign/future rows stay.
        if (reason instanceof PreviewRecoveryCorruption) {
          const frame = requireRecordShape(raw, true);
          if (frame.writerId === this.writerId) damagedOwned.push(frame);
        }
      }
    }
    valid.sort((a, b) => b.capturedAt - a.capturedAt || b.id.localeCompare(a.id));
    const owned = valid.filter(frame => frame.writerId === this.writerId);
    this.observedOwn.set(previewRecoveryScopeKey(scope), owned);
    return { frame: valid[0] ?? null, owned,
      damagedOwned, issues: [...new Set(issues)] };
  }
  async save(capture: RetainedPreviewFrame): Promise<PreviewRecoveryWrite> {
    if (capture.writerId !== this.writerId) throw new Error("Only this window's decoded copy can be saved.");
    requireRecordShape(capture, false);
    // Capture exact prior IDs before hashing or I/O. Duplicating a browser tab
    // can copy the session writer; equal timestamps do not prove that a peer's
    // newly inserted row is our predecessor. Only previously observed rows or
    // rows actually created by this store instance are eligible for cleanup.
    const own = [...(this.observedOwn.get(capture.scopeKey) ?? [])];
    const created = this.createdOwn.get(capture.scopeKey);
    if (created && !own.some(frame => frame.id === created.id)) own.push(created);
    const before = own.slice();
    const frame = await sealPreviewRecovery(capture);
    // Immutable insertion, then independent hash readback, then scoped cleanup.
    // A quota failure or corrupt new row cannot overwrite the previous good row.
    const database = await openDatabase();
    try {
      await new Promise<void>((resolve, reject) => {
        const transaction = database.transaction(PREVIEW_RECOVERY_STORE, "readwrite");
        const store = transaction.objectStore(PREVIEW_RECOVERY_STORE);
        let failure = "The recovery copy could not be saved. Existing copies were preserved.";
        const all = store.getAll(undefined, MAX_RECOVERY_RECORDS + 1);
        all.onsuccess = () => {
          const records = all.result as unknown[];
          const bytes = records.reduce<number>((total, value) => total
            + (object(value) && value.blob instanceof Blob ? value.blob.size : MAX_RECOVERY_BYTES + 1), 0);
          if (records.length >= MAX_RECOVERY_RECORDS || bytes + frame.byteLength > MAX_RECOVERY_BYTES) {
            failure = "Preview recovery storage is full. Existing copies were preserved; remove one of your retained copies before retrying.";
            transaction.abort(); return;
          }
          try { store.add(frame); }
          catch {
            failure = "The recovery copy could not be saved, possibly because local storage is full. Existing copies were preserved.";
            transaction.abort();
          }
        };
        transaction.oncomplete = () => resolve();
        transaction.onerror = transaction.onabort = () => reject(new Error(failure));
      });
    } finally { database.close(); }
    const readback = await verifyPreviewRecovery(await one(frame.id), frame.scope, frame.sourceRevision);
    if (readback.sha256 !== frame.sha256) throw new Error("Preview recovery changed during readback; earlier copies were preserved.");
    this.createdOwn.set(frame.scopeKey, readback);
    let cleanupIssue: string | null = null;
    for (const previous of before) {
      // Never erase a peer's copy or a row which was not observed before this
      // save. Immutable IDs + digest comparison preserve concurrent successors.
      if (previous.writerId !== this.writerId || previous.scopeKey !== frame.scopeKey
          || previous.capturedAt > frame.capturedAt || previous.sourceRevision > frame.sourceRevision) continue;
      try {
        await this.discard(previous, false);
      } catch { cleanupIssue = "The new recovery copy was verified; an earlier copy was preserved because cleanup could not be confirmed."; }
    }
    announce(frame.scopeKey);
    return { frame: readback, issue: cleanupIssue };
  }
  async discard(frame: RetainedPreviewFrame, notify = true): Promise<void> {
    if (frame.writerId !== this.writerId) throw new Error("A different window owns this retained copy; it was preserved.");
    requireRecordShape(frame, true);
    const database = await openDatabase();
    try {
      await new Promise<void>((resolve, reject) => {
        const transaction = database.transaction(PREVIEW_RECOVERY_STORE, "readwrite");
        const store = transaction.objectStore(PREVIEW_RECOVERY_STORE);
        const read = store.get(frame.id);
        let failure = "The retained copy could not be removed.";
        read.onsuccess = () => {
          const current: unknown = read.result;
          if (current === undefined) return;
          if (!object(current) || current.writerId !== this.writerId || current.scopeKey !== frame.scopeKey
              || current.sha256 !== frame.sha256) {
            failure = "The retained copy changed in another window and was preserved.";
            transaction.abort(); return;
          }
          store.delete(frame.id);
        };
        transaction.oncomplete = () => resolve();
        transaction.onerror = transaction.onabort = () => reject(new Error(failure));
      });
    } finally { database.close(); }
    this.observedOwn.set(frame.scopeKey, (this.observedOwn.get(frame.scopeKey) ?? []).filter(copy => copy.id !== frame.id));
    if (this.createdOwn.get(frame.scopeKey)?.id === frame.id) this.createdOwn.delete(frame.scopeKey);
    if (notify) announce(frame.scopeKey);
  }
}
