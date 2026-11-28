import { projectionUuid } from "./projectMutation";
import { decodeScopedDraft, draftPatch, encodeScopedDraft, MAX_DRAFT_BYTES } from "./scopedChangeDraft";
import type { ScopedChangeDraft } from "./scopedChangeDraft";
import type { ScopedMutationChange } from "./types";

const ROOT = "motionwright.scoped-save.v1:";
const MAX_ATTEMPTS = 8;
const pendingPrefix = (scope: string) => ROOT + "pending:" + scope + ":";

export interface ScopedSaveAttempt {
  requestId: string;
  revision: number;
  draft: ScopedChangeDraft;
  change: ScopedMutationChange;
  raw: string;
}

export function scopedSaveStorageChanged(key: string | null, scope: string) {
  return key === null || key.startsWith(pendingPrefix(scope));
}

function decodeAttempt(raw: string, scope: string, sceneId: string, requestId: string): ScopedSaveAttempt {
  if (new TextEncoder().encode(raw).byteLength > 2 * MAX_DRAFT_BYTES + 1024) {
    throw new Error("The retained save exceeds its storage limit.");
  }
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("The retained save could not be read.");
  const input = value as Record<string, unknown>;
  if (Object.keys(input).length !== 6 || input.schema !== "motionwright.scoped-save/1"
      || !projectionUuid(requestId) || input.requestId !== requestId || input.sceneId !== sceneId
      || !Number.isSafeInteger(input.revision) || Number(input.revision) < 0 || typeof input.draft !== "string"
      || (input.undoPatchId !== null && !projectionUuid(input.undoPatchId))) {
    throw new Error("The retained save belongs to another editing scope or has an invalid identity.");
  }
  const revision = Number(input.revision);
  const draft = decodeScopedDraft(input.draft, scope);
  if (input.undoPatchId === null && (draft.baseRevision !== revision || !draft.edits.length || !draft.rationale.trim())) {
    throw new Error("The retained changes do not match the submitted revision.");
  }
  const change: ScopedMutationChange = input.undoPatchId === null ? { type: "apply_creative_patch", patch: draftPatch(draft, sceneId) }
    : { type: "undo_creative_patch", patch_id: input.undoPatchId };
  return { requestId, revision, draft, change, raw };
}

export function readScopedSaves(storage: Storage, scope: string, sceneId: string): ScopedSaveAttempt[] {
  if (storage.length > 4096) throw new Error("The local recovery storage exceeds its lookup limit.");
  const prefix = pendingPrefix(scope);
  const found: ScopedSaveAttempt[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key?.startsWith(prefix)) continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    if (found.length >= MAX_ATTEMPTS) throw new Error("Resolve the retained proposal saves before submitting more changes.");
    found.push(decodeAttempt(raw, scope, sceneId, key.slice(prefix.length)));
  }
  return found.sort((a, b) => a.revision - b.revision || a.requestId.localeCompare(b.requestId));
}

export function retainScopedSave(
  storage: Storage, scope: string, sceneId: string, requestId: string, revision: number,
  draft: ScopedChangeDraft, undoPatchId: string | null,
): ScopedSaveAttempt {
  if (readScopedSaves(storage, scope, sceneId).length) throw new Error("Check the retained proposal save first.");
  const raw = JSON.stringify({ schema: "motionwright.scoped-save/1", requestId, sceneId, revision,
    draft: encodeScopedDraft(draft), undoPatchId });
  const attempt = decodeAttempt(raw, scope, sceneId, requestId);
  const key = pendingPrefix(scope) + requestId;
  if (storage.getItem(key) !== null) throw new Error("This proposal save identity already exists.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The original proposal save could not be retained.");
  return attempt;
}

export function sameRetainedDraft(draft: ScopedChangeDraft, attempt: ScopedSaveAttempt): boolean {
  return encodeScopedDraft(decodeScopedDraft(encodeScopedDraft(draft), draft.scope)) === encodeScopedDraft(attempt.draft);
}

export function finishScopedSave(storage: Storage, scope: string, attempt: ScopedSaveAttempt, keepOriginal = false) {
  const key = pendingPrefix(scope) + attempt.requestId;
  const current = storage.getItem(key);
  if (current === null && !keepOriginal) return;
  if (current !== attempt.raw) throw new Error("The retained proposal save changed. Reopen its original record.");
  if (keepOriginal) {
    const archived = ROOT + "retained:" + scope + ":" + attempt.requestId;
    const existing = storage.getItem(archived);
    if (existing !== null && existing !== current) throw new Error("The retained proposal copy changed.");
    storage.setItem(archived, current);
    if (storage.getItem(archived) !== current) throw new Error("The original proposal could not be kept.");
  }
  storage.removeItem(key);
}
