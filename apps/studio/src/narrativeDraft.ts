import { sameProjectRevision } from "./projectMutation";
import type { Change, Project, ProjectEvent } from "./types";

export type NarrativeTarget = { kind: "premise" } | { kind: "objective"; sceneId: string };
export type NarrativeChange = Extract<Change, { type: "set_narrative_premise" | "update_scene_objective" }>;
export interface NarrativePending {
  /** Local comparison identity only; never a service request ID, grant, or replay token. */
  id: string;
  revision: number;
  sequence: number;
  raw: string;
  value: string;
  outcome: "UNKNOWN" | "NOT_SENT";
  diagnostic?: string;
}
export interface NarrativeDraft {
  schema: "motionwright.narrative-draft/1";
  scope: string;
  target: NarrativeTarget;
  baseRevision: number;
  base: string;
  raw: string;
  sequence: number;
  intent: boolean;
  pending: NarrativePending | null;
}
export const NARRATIVE_RECOVERY_PREFIX = "motionwright.narrative-recovery.v1:";
export const NARRATIVE_POINTER_PREFIX = "motionwright.narrative-writer.v1:";
export const MAX_NARRATIVE_COPY_BYTES = 1024 * 1024;
export const MAX_NARRATIVE_RECOVERY_BYTES = 4 * 1024 * 1024;
export const MAX_NARRATIVE_COPIES = 64;
export const MAX_NARRATIVE_HISTORY = 2048;
export const narrativeScope = (project: Project, target: NarrativeTarget) => JSON.stringify([
  project.id, project.generation, project.active_branch, target.kind,
  target.kind === "objective" ? target.sceneId : null,
]);
export const narrativeCopyKey = (scope: string, writerId: string) =>
  NARRATIVE_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writerId;
export const narrativePointerKey = (scope: string) => NARRATIVE_POINTER_PREFIX + encodeURIComponent(scope);
export const narrativeLabel = (target: NarrativeTarget) => target.kind === "premise" ? "Narrative premise" : "Scene objective";

export function narrativeValue(project: Project, target: NarrativeTarget): string {
  if (target.kind === "premise") return project.narrative.premise;
  const scenes = project.scenes.filter(scene => scene.id === target.sceneId);
  if (scenes.length !== 1) throw new Error("The Narrative scene is unavailable or ambiguous in the saved project.");
  return scenes[0].objective;
}
export const proposedNarrativeValue = (draft: NarrativeDraft) =>
  draft.target.kind === "objective" ? draft.raw.trim() : draft.raw;
export const narrativeHasInput = (draft: NarrativeDraft) => draft.intent || draft.raw !== draft.base;
export const narrativeHasWork = (draft: NarrativeDraft) => narrativeHasInput(draft) || draft.pending !== null;
export const narrativeUnconfirmed = (draft: NarrativeDraft) => draft.pending?.outcome === "UNKNOWN";
function checkScope(draft: NarrativeDraft, project: Project) {
  if (draft.scope !== narrativeScope(project, draft.target))
    throw new Error("This Narrative draft belongs to another project, generation, branch, or scene.");
  narrativeValue(project, draft.target);
}
export function createNarrativeDraft(project: Project, target: NarrativeTarget): NarrativeDraft {
  const base = narrativeValue(project, target);
  return { schema: "motionwright.narrative-draft/1", scope: narrativeScope(project, target),
    target: { ...target }, baseRevision: project.revision, base, raw: base, sequence: 0, intent: false, pending: null };
}
export function editNarrativeDraft(draft: NarrativeDraft, raw: string): NarrativeDraft {
  return { ...draft, raw, sequence: draft.sequence + 1, intent: draft.intent || narrativeUnconfirmed(draft) };
}
export function narrativeConflict(draft: NarrativeDraft, project: Project): boolean {
  checkScope(draft, project);
  const current = narrativeValue(project, draft.target);
  return narrativeHasInput(draft) && (draft.baseRevision > project.revision
    || (current !== draft.base && current !== proposedNarrativeValue(draft)));
}
/** A document observation never acknowledges a pending operation. */
export function reconcileNarrativeDraft(draft: NarrativeDraft, project: Project): NarrativeDraft {
  checkScope(draft, project);
  if (draft.pending || (narrativeHasInput(draft) && draft.baseRevision > project.revision)) return draft;
  const current = narrativeValue(project, draft.target), dirty = narrativeHasInput(draft);
  if (dirty && current !== draft.base && current !== proposedNarrativeValue(draft)) return draft;
  if (draft.baseRevision === project.revision && draft.base === current && (dirty || draft.raw === current)) return draft;
  return { ...draft, base: current, baseRevision: project.revision, raw: dirty ? draft.raw : current };
}
export function resolveNarrativeDraft(draft: NarrativeDraft, project: Project, choice: "saved" | "draft"): NarrativeDraft {
  checkScope(draft, project);
  if (narrativeUnconfirmed(draft)) throw new Error("Inspect the unconfirmed Narrative outcome before resolving its draft.");
  const base = narrativeValue(project, draft.target);
  return { ...draft, base, baseRevision: project.revision,
    raw: choice === "saved" ? base : draft.raw, intent: choice === "draft" && draft.intent, pending: null };
}
export function prepareNarrativeSubmission(draft: NarrativeDraft, project: Project, id: string): {
  draft: NarrativeDraft; change: NarrativeChange;
} {
  checkScope(draft, project);
  if (narrativeUnconfirmed(draft)) throw new Error("Review the earlier Narrative outcome before a separate save.");
  if (narrativeConflict(draft, project)) throw new Error("Resolve the overlapping Narrative value before saving.");
  if (!uuid(id)) throw new Error("A local Narrative comparison identity is required.");
  const value = proposedNarrativeValue(draft);
  if (value === narrativeValue(project, draft.target)) throw new Error("This Narrative value already matches the saved project.");
  if (draft.target.kind === "objective" && !value) throw new Error("Enter a scene objective before saving.");
  const change: NarrativeChange = draft.target.kind === "premise"
    ? { type: "set_narrative_premise", premise: value }
    : { type: "update_scene_objective", scene_id: draft.target.sceneId, objective: value };
  return { change, draft: { ...draft, intent: true, pending: {
    id, revision: project.revision, sequence: draft.sequence, raw: draft.raw, value, outcome: "UNKNOWN",
  } } };
}
export function retainNarrativeFailure(draft: NarrativeDraft, id: string, diagnostic: string, notSent = false): NarrativeDraft {
  if (draft.pending?.id !== id) return draft;
  return { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } };
}
/** Only call with the Project returned by the original throwing coordinator call. */
export function acknowledgeNarrativeDraft(draft: NarrativeDraft, originalId: string, receipt: Project): NarrativeDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== originalId) return draft;
  checkScope(draft, receipt);
  if (receipt.revision !== pending.revision + 1 || narrativeValue(receipt, draft.target) !== pending.value)
    throw new Error("The original Narrative reply does not confirm the exact submitted snapshot.");
  const laterInput = draft.sequence !== pending.sequence;
  return { ...draft, base: pending.value, baseRevision: receipt.revision,
    raw: laterInput ? draft.raw : pending.value, intent: laterInput,
    pending: null };
}

export interface NarrativeOutcomeTransport {
  readProject(projectId: string): Promise<Project>;
  readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}
export interface NarrativeOutcomeReview {
  scope: string;
  pending: NarrativePending;
  project: Project;
  events: ProjectEvent[];
  matchingValueRevisions: number[];
}
const samePending = (a: NarrativePending | null, b: NarrativePending) => a !== null
  && a.id === b.id && a.revision === b.revision && a.sequence === b.sequence
  && a.raw === b.raw && a.value === b.value && a.outcome === b.outcome;
function sameObservedProject(a: Project, b: Project, target: NarrativeTarget): boolean {
  return sameProjectRevision(a, b) && narrativeValue(a, target) === narrativeValue(b, target);
}
export function narrativeOutcomeMatches(draft: NarrativeDraft, project: Project, review: NarrativeOutcomeReview | null): boolean {
  return Boolean(review && narrativeUnconfirmed(draft) && review.scope === draft.scope
    && narrativeScope(project, draft.target) === draft.scope && samePending(draft.pending, review.pending)
    && sameObservedProject(project, review.project, draft.target));
}
export async function inspectNarrativeOutcome(draft: NarrativeDraft, selected: Project,
  transport: NarrativeOutcomeTransport): Promise<NarrativeOutcomeReview> {
  checkScope(draft, selected);
  if (!narrativeUnconfirmed(draft)) throw new Error("There is no unconfirmed Narrative attempt to inspect.");
  const pending = structuredClone(draft.pending!);
  const observed = structuredClone(await transport.readProject(selected.id));
  if (narrativeScope(observed, draft.target) !== draft.scope || !sameObservedProject(observed, selected, draft.target))
    throw new Error("The saved project changed. Reload its current state before reviewing the Narrative outcome.");
  if (!Number.isSafeInteger(observed.revision) || observed.revision < pending.revision
    || observed.revision - pending.revision > MAX_NARRATIVE_HISTORY)
    throw new Error("The Narrative history range is unavailable or exceeds its bounded review window.");
  const events: ProjectEvent[] = [];
  let cursor = pending.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision))
      throw new Error("The saved Narrative history is incomplete.");
    for (const event of page) {
      if (!event || !Number.isSafeInteger(event.revision) || event.revision !== cursor + 1
        || event.revision > observed.revision || !event.change || typeof event.change.type !== "string"
        || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved Narrative history is not a contiguous revision sequence.");
      events.push(structuredClone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  const after = await transport.readProject(selected.id);
  if (!sameObservedProject(after, observed, draft.target))
    throw new Error("The saved project changed while its Narrative history was read. Inspect it again.");
  return { scope: draft.scope, pending, project: observed, events,
    matchingValueRevisions: events.filter(event => draft.target.kind === "premise"
      ? event.change.type === "set_narrative_premise" && event.change.premise === pending.value
      : event.change.type === "update_scene_objective" && event.change.scene_id === draft.target.sceneId
        && event.change.objective === pending.value).map(event => event.revision) };
}
/** Preparing a separate intent is not an acknowledgement; retain its old copy first. */
export function releaseNarrativeOutcome(draft: NarrativeDraft, project: Project, review: NarrativeOutcomeReview): NarrativeDraft {
  if (!narrativeOutcomeMatches(draft, project, review))
    throw new Error("Read the current saved project and complete history before starting a separate Narrative intent.");
  return reconcileNarrativeDraft({ ...draft, pending: null }, project);
}

export type NarrativeStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface NarrativeCopy {
  schema: "motionwright.narrative-recovery/1";
  scope: string;
  writerId: string;
  updatedAt: number;
  draft: NarrativeDraft;
}
export interface NarrativeCustody {
  writerId: string;
  expectedRaw: string | null;
  expectedPointer: string | null;
}
export interface NarrativeRecoveryEntry { key: string; writerId: string; raw: string; value: NarrativeCopy | null }
export interface NarrativeFile {
  schema: "motionwright.narrative-file/1";
  scope: string;
  exportedAt: number;
  draft: NarrativeDraft;
}
export const MAX_NARRATIVE_FILE_BYTES = 8 * 1024 * 1024;
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const safeInteger = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const bytes = (raw: string) => new TextEncoder().encode(raw).length;

function cleanNarrativeDraft(value: unknown, scope: string): NarrativeDraft {
  if (!record(value) || value.schema !== "motionwright.narrative-draft/1" || value.scope !== scope
    || !record(value.target) || !["premise", "objective"].includes(String(value.target.kind))
    || (value.target.kind === "objective" && (typeof value.target.sceneId !== "string" || !value.target.sceneId))
    || !safeInteger(value.baseRevision) || !safeInteger(value.sequence) || typeof value.base !== "string"
    || typeof value.raw !== "string" || typeof value.intent !== "boolean")
    throw new Error("The Narrative recovery has an unsupported shape or scope.");
  const identity: unknown = JSON.parse(scope);
  if (!Array.isArray(identity) || identity.length !== 5 || identity.slice(0, 3).some(part => typeof part !== "string" || !part)
    || identity[3] !== value.target.kind || identity[4] !== (value.target.kind === "objective" ? value.target.sceneId : null))
    throw new Error("The Narrative recovery target does not match its project identity.");
  let pending: NarrativePending | null = null;
  if (value.pending !== null) {
    const sent = value.pending;
    if (!record(sent) || !uuid(sent.id) || !safeInteger(sent.revision) || !safeInteger(sent.sequence)
      || sent.sequence > value.sequence || typeof sent.raw !== "string" || typeof sent.value !== "string"
      || !["UNKNOWN", "NOT_SENT"].includes(String(sent.outcome))
      || (sent.diagnostic !== undefined && typeof sent.diagnostic !== "string")
      || sent.value !== (value.target.kind === "objective" ? sent.raw.trim() : sent.raw))
      throw new Error("The Narrative pending comparison snapshot is unreadable.");
    // Only the live caller can know it stopped before dispatch. A recovered
    // NOT_SENT label is comparison data, never permission to retry a request.
    pending = { id: sent.id, revision: sent.revision, sequence: sent.sequence, raw: sent.raw, value: sent.value,
      outcome: "UNKNOWN", ...(typeof sent.diagnostic === "string" ? { diagnostic: sent.diagnostic } : {}) };
  }
  return { schema: "motionwright.narrative-draft/1", scope,
    target: value.target.kind === "premise" ? { kind: "premise" } : { kind: "objective", sceneId: value.target.sceneId as string },
    baseRevision: value.baseRevision, base: value.base, raw: value.raw, sequence: value.sequence, intent: value.intent, pending };
}

const exactFields = (value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []) =>
  required.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => required.includes(key) || optional.includes(key));
/** Portable author input only. A download never serializes a grant or a receipt.
 * Export is available even when browser storage and its quota are unavailable.
 * Import has a separate byte bound; an oversized author file is still exportable.
 */
export function encodeNarrativeFile(draft: NarrativeDraft, exportedAt = Date.now()): string {
  if (!safeInteger(exportedAt)) throw new Error("The Narrative file timestamp is invalid.");
  const clean = cleanNarrativeDraft(draft, draft.scope);
  return JSON.stringify({ schema: "motionwright.narrative-file/1", scope: clean.scope, exportedAt, draft: clean } satisfies NarrativeFile, null, 2);
}
export function decodeNarrativeFile(raw: string, expectedScope: string): NarrativeFile {
  if (bytes(raw) > MAX_NARRATIVE_FILE_BYTES) throw new Error("The Narrative file exceeds the 8 MiB import limit.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exactFields(value, ["schema", "scope", "exportedAt", "draft"])
    || value.schema !== "motionwright.narrative-file/1" || value.scope !== expectedScope || !safeInteger(value.exportedAt)
    || !record(value.draft) || !exactFields(value.draft,
      ["schema", "scope", "target", "baseRevision", "base", "raw", "sequence", "intent", "pending"]))
    throw new Error("The Narrative file has an unsupported schema, fields, or project identity.");
  const source = value.draft, target = source.target, pending = source.pending;
  if (!record(target) || !exactFields(target, target.kind === "premise" ? ["kind"] : ["kind", "sceneId"]))
    throw new Error("The Narrative file target has unsupported fields.");
  if (pending !== null && (!record(pending) || pending.outcome !== "UNKNOWN"
    || !exactFields(pending, ["id", "revision", "sequence", "raw", "value", "outcome"], ["diagnostic"])))
    throw new Error("The Narrative file contains an unsupported pending snapshot or authority fields.");
  return { schema: "motionwright.narrative-file/1", scope: expectedScope, exportedAt: value.exportedAt,
    draft: cleanNarrativeDraft(source, expectedScope) };
}
/** Explicitly using a file expresses an author intent, including an old clean
 * checkpoint. It cannot acknowledge its pending snapshot or infer a service call.
 */
export function importNarrativeFileDraft(file: NarrativeFile, project: Project): NarrativeDraft {
  const verified = decodeNarrativeFile(JSON.stringify(file), narrativeScope(project, file.draft.target));
  checkScope(verified.draft, project);
  return reconcileNarrativeDraft({ ...verified.draft, intent: true }, project);
}
export function decodeNarrativeCopy(raw: string, scope: string, writerId: string): NarrativeCopy {
  if (bytes(raw) > MAX_NARRATIVE_COPY_BYTES) throw new Error("The Narrative recovery copy exceeds 1 MiB.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.schema !== "motionwright.narrative-recovery/1" || value.scope !== scope
    || value.writerId !== writerId || !uuid(writerId) || !safeInteger(value.updatedAt))
    throw new Error("The Narrative recovery copy is unreadable or belongs to another writer.");
  return { schema: "motionwright.narrative-recovery/1", scope, writerId,
    updatedAt: value.updatedAt, draft: cleanNarrativeDraft(value.draft, scope) };
}
export function encodeNarrativeCopy(copy: NarrativeCopy): string {
  const raw = JSON.stringify(copy);
  decodeNarrativeCopy(raw, copy.scope, copy.writerId);
  return raw;
}
export function readNarrativeCopies(storage: NarrativeStorage, scope: string,
  onEntry?: (entry: NarrativeRecoveryEntry) => void): { entries: NarrativeRecoveryEntry[]; limited: boolean } {
  const prefix = NARRATIVE_RECOVERY_PREFIX + encodeURIComponent(scope) + ":";
  const entries: NarrativeRecoveryEntry[] = [];
  let limited = false;
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(prefix)) continue;
    if (entries.length >= MAX_NARRATIVE_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key);
    if (raw === null) continue;
    const writerId = key.slice(prefix.length);
    let value: NarrativeCopy | null = null;
    try { value = decodeNarrativeCopy(raw, scope, writerId); } catch { /* Original bytes remain recoverable. */ }
    const entry = { key, writerId, raw, value };
    onEntry?.(entry);
    entries.push(entry);
  }
  return { entries, limited };
}
/** Verifies both bytes and this tab's pointer before any Project mutation. */
export function writeNarrativeCopy(storage: NarrativeStorage, session: Pick<Storage, "getItem" | "setItem">,
  draft: NarrativeDraft, custody: NarrativeCustody): NarrativeCustody {
  const key = narrativeCopyKey(draft.scope, custody.writerId), pointerKey = narrativePointerKey(draft.scope);
  const pointer = session.getItem(pointerKey);
  if (pointer !== custody.expectedPointer && pointer !== custody.writerId)
    throw new Error("Another Narrative writer changed this tab's recovery pointer. Original copies were kept.");
  if (storage.getItem(key) !== custody.expectedRaw)
    throw new Error("The Narrative recovery copy changed in another window. Original copies were kept.");
  const copy: NarrativeCopy = { schema: "motionwright.narrative-recovery/1", scope: draft.scope,
    writerId: custody.writerId, updatedAt: Date.now(), draft };
  const raw = encodeNarrativeCopy(copy);
  if (custody.expectedRaw !== null) {
    const previous = decodeNarrativeCopy(custody.expectedRaw, draft.scope, custody.writerId);
    if (!narrativeHasWork(previous.draft) && JSON.stringify(previous.draft) !== JSON.stringify(draft))
      throw new Error("A clean Narrative checkpoint is immutable. Start a separate recovery writer.");
  }
  let total = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const existing = storage.key(index);
    if (!existing?.startsWith(NARRATIVE_RECOVERY_PREFIX) || existing === key) continue;
    total += bytes(existing) + bytes(storage.getItem(existing) ?? ""); count++;
  }
  if (total > MAX_NARRATIVE_RECOVERY_BYTES || count > MAX_NARRATIVE_COPIES)
    throw new Error("Narrative recovery is full (64 copies / 4 MiB). Remove only copies you no longer need; all author input was kept.");
  // Enumeration can observe storage shared with another window. Recheck our
  // custody after that work before replacing any author bytes.
  const currentPointer = session.getItem(pointerKey);
  if (currentPointer !== custody.expectedPointer && currentPointer !== custody.writerId)
    throw new Error("Another Narrative writer changed this tab's recovery pointer. Original copies were kept.");
  if (storage.getItem(key) !== custody.expectedRaw)
    throw new Error("The Narrative recovery copy changed in another window. Original copies were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The Narrative recovery write was not confirmed.");
  // If the pointer write fails, the caller may retry the verified data copy
  // without replacing an older, uncertain operation or another writer's bytes.
  custody.expectedRaw = raw;
  session.setItem(pointerKey, custody.writerId);
  if (session.getItem(pointerKey) !== custody.writerId)
    throw new Error("The Narrative recovery writer pointer was not confirmed.");
  if (storage.getItem(key) !== raw)
    throw new Error("The Narrative recovery copy changed while its writer pointer was published.");
  custody.expectedPointer = custody.writerId;
  return custody;
}

/** Two-phase local custody: retain UNKNOWN under the new writer and verify its
 * pointer before releasing the new intent. No service command is created. */
export function retainReviewedNarrativeIntent(storage: NarrativeStorage, session: Pick<Storage, "getItem" | "setItem">,
  before: NarrativeDraft, next: NarrativeDraft, original: NarrativeCustody, writerId: string): NarrativeCustody {
  if (!narrativeUnconfirmed(before) || next.pending || before.scope !== next.scope || !uuid(writerId)
    || writerId === original.writerId || original.expectedRaw === null)
    throw new Error("The reviewed Narrative recovery fork is invalid.");
  const originalKey = narrativeCopyKey(before.scope, original.writerId);
  const originalRaw = storage.getItem(originalKey);
  if (originalRaw !== original.expectedRaw || originalRaw === null
    || JSON.stringify(decodeNarrativeCopy(originalRaw, before.scope, original.writerId).draft) !== JSON.stringify(before))
    throw new Error("The earlier Narrative attempt was not retained exactly.");
  const custody: NarrativeCustody = { writerId, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeNarrativeCopy(storage, session, before, custody);
  if (storage.getItem(originalKey) !== originalRaw) throw new Error("The earlier Narrative recovery changed during review.");
  writeNarrativeCopy(storage, session, next, custody);
  if (storage.getItem(originalKey) !== originalRaw) throw new Error("The earlier Narrative recovery changed during review.");
  return custody;
}
