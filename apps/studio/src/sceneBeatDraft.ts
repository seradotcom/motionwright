import { formatBeatTime, inspectBeatTiming } from "./sceneBeatTiming";
import type { Beat, Change, Project, ProjectEvent, RationalTime, Scene } from "./types";

export const BEAT_FIELDS = ["label", "objective", "start", "duration"] as const;
export type BeatField = typeof BEAT_FIELDS[number];
export type BeatRaw = Record<BeatField, string>;
export type BeatOperation = "upsert" | "remove";
export type BeatChange = Extract<Change, { type: "upsert_scene_beat" | "remove_scene_beat" }>;
export interface BeatPending {
  /** Local callback correlation only. Never a grant or replayable request ID. */
  id: string;
  revision: number;
  sequence: number;
  operation: BeatOperation;
  raw: BeatRaw;
  value: Beat | null;
  sceneDuration: RationalTime;
  outcome: "UNKNOWN" | "NOT_SENT";
  diagnostic?: string;
}
export interface SceneBeatDraft {
  schema: "motionwright.scene-beat-draft/1";
  scope: string;
  sceneId: string;
  beatId: string;
  baseRevision: number;
  base: Beat | null;
  sceneDuration: RationalTime;
  raw: BeatRaw;
  sequence: number;
  intent: boolean;
  allowCreate: boolean;
  pending: BeatPending | null;
}
export const BEAT_RECOVERY_PREFIX = "motionwright.scene-beat-recovery.v1:";
export const BEAT_POINTER_PREFIX = "motionwright.scene-beat-writer.v1:";
export const MAX_BEAT_COPY_BYTES = 128 * 1024;
export const MAX_BEAT_RECOVERY_BYTES = 4 * 1024 * 1024;
export const MAX_BEAT_COPIES = 256;
export const MAX_BEAT_HISTORY = 2048;
const MAX_STORAGE_KEYS = 8192;
const zero = (): RationalTime => ({ num: "0", den: "1" });
const one = (): RationalTime => ({ num: "1", den: "1" });
const bytes = (raw: string) => new TextEncoder().encode(raw).length;
const clone = <T,>(value: T): T => structuredClone(value);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const safeInteger = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const sameTime = (left: RationalTime, right: RationalTime) => left.num === right.num && left.den === right.den;
export const sameBeat = (left: Beat | null, right: Beat | null): boolean => left === null || right === null ? left === right
  : left.id === right.id && left.label === right.label && left.objective === right.objective
    && sameTime(left.start, right.start) && sameTime(left.duration, right.duration);
export const beatRaw = (beat: Beat): BeatRaw => ({ label: beat.label, objective: beat.objective,
  start: formatBeatTime(beat.start), duration: formatBeatTime(beat.duration) });
const sameRaw = (left: BeatRaw, right: BeatRaw) => BEAT_FIELDS.every(field => left[field] === right[field]);
export const sceneBeatScope = (project: Project, sceneId: string, beatId: string) =>
  JSON.stringify([project.id, project.generation, project.active_branch, sceneId, beatId]);
export const beatProjectScope = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const beatCopyKey = (scope: string, writerId: string) => BEAT_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writerId;
export const beatPointerKey = (scope: string) => BEAT_POINTER_PREFIX + encodeURIComponent(scope);
export function beatScopeIdentity(scope: string): string[] {
  const parts: unknown = JSON.parse(scope);
  if (!Array.isArray(parts) || parts.length !== 5 || parts.some(part => typeof part !== "string" || !part || part.length > 128))
    throw new Error("The beat recovery scope is unreadable.");
  return parts as string[];
}
export const beatScopeProject = (scope: string) => JSON.stringify(beatScopeIdentity(scope).slice(0, 3));
export const beatScopeScene = (scope: string) => beatScopeIdentity(scope)[3];
export const beatScopeId = (scope: string) => beatScopeIdentity(scope)[4];

export function beatTarget(project: Project, sceneId: string, beatId: string): { scene: Scene | null; beat: Beat | null } {
  const scenes = project.scenes.filter(scene => scene.id === sceneId);
  if (scenes.length > 1) throw new Error("The saved scene identity is ambiguous.");
  const scene = scenes[0] ?? null, beats = scene?.beats.filter(beat => beat.id === beatId) ?? [];
  if (beats.length > 1) throw new Error("The saved beat identity is ambiguous.");
  return { scene, beat: beats[0] ?? null };
}
function targetFor(draft: SceneBeatDraft, project: Project) {
  if (draft.scope !== sceneBeatScope(project, draft.sceneId, draft.beatId))
    throw new Error("This beat draft belongs to another project, generation, branch, scene, or beat.");
  return beatTarget(project, draft.sceneId, draft.beatId);
}
export function beatLocked(project: Project, sceneId: string): boolean {
  return project.locks.some(lock => (lock.kind === "content" || lock.kind === "timing")
    && (lock.resource === "project:" + project.id || lock.resource === "scene:" + sceneId));
}
export function createSceneBeatDraft(project: Project, sceneId: string, beatId: string): SceneBeatDraft {
  const { scene, beat } = beatTarget(project, sceneId, beatId);
  if (!scene || !beat) throw new Error("Choose an existing saved beat to start its draft.");
  return { schema: "motionwright.scene-beat-draft/1", scope: sceneBeatScope(project, sceneId, beatId),
    sceneId, beatId, baseRevision: project.revision, base: clone(beat), sceneDuration: clone(scene.duration),
    raw: beatRaw(beat), sequence: 0, intent: false, allowCreate: false, pending: null };
}
export function createNewSceneBeatDraft(project: Project, sceneId: string, beat: Beat): SceneBeatDraft {
  const target = beatTarget(project, sceneId, beat.id);
  if (!target.scene || target.beat || !uuid(beat.id)) throw new Error("A new beat needs its own unused identity in the selected scene.");
  return { schema: "motionwright.scene-beat-draft/1", scope: sceneBeatScope(project, sceneId, beat.id),
    sceneId, beatId: beat.id, baseRevision: project.revision, base: null, sceneDuration: clone(target.scene.duration),
    raw: beatRaw(beat), sequence: 0, intent: true, allowCreate: true, pending: null };
}
export const beatHasInput = (draft: SceneBeatDraft) => draft.intent || (draft.base !== null && !sameRaw(draft.raw, beatRaw(draft.base)));
export const beatHasWork = (draft: SceneBeatDraft) => beatHasInput(draft) || draft.pending !== null;
export const beatUnconfirmed = (draft: SceneBeatDraft) => draft.pending?.outcome === "UNKNOWN";
export function editSceneBeatDraft(draft: SceneBeatDraft, field: BeatField, raw: string): SceneBeatDraft {
  const limit = field === "objective" ? 4000 : field === "label" ? 160 : 96;
  if (raw.length > limit) throw new Error("This beat field exceeds its recoverable input limit.");
  return { ...draft, raw: { ...draft.raw, [field]: raw }, sequence: draft.sequence + 1, intent: true };
}
export function sceneBeatDraftValue(draft: SceneBeatDraft, duration: RationalTime): Beat {
  const source = draft.base ?? { id: draft.beatId, label: "New beat", objective: "", start: zero(), duration: one() };
  const timing = inspectBeatTiming(source, draft.raw, duration);
  if (!timing.ok) throw new Error(timing.message);
  const label = draft.raw.label === draft.base?.label ? draft.raw.label : draft.raw.label.trim();
  if (!label.trim() || bytes(label) > 160) throw new Error("Enter a beat label of at most 160 UTF-8 bytes.");
  if (bytes(draft.raw.objective) > 4000) throw new Error("The beat objective must fit within 4000 UTF-8 bytes.");
  return { id: draft.beatId, label, objective: draft.raw.objective, start: timing.start, duration: timing.duration };
}
export type BeatConflict = "scene_missing" | "removed" | "changed" | "retimed" | "later_revision";
export function sceneBeatConflict(draft: SceneBeatDraft, project: Project): BeatConflict | null {
  const { scene, beat } = targetFor(draft, project);
  if (!scene) return "scene_missing";
  if (draft.baseRevision > project.revision) return "later_revision";
  if (!beat && (draft.base !== null || (!draft.allowCreate && beatHasInput(draft)))) return "removed";
  if (!sameBeat(beat, draft.base)) return "changed";
  if (!sameTime(scene.duration, draft.sceneDuration)) return "retimed";
  return null;
}
/** A refreshed document cannot acknowledge a pending save or erase local input. */
export function reconcileSceneBeatDraft(draft: SceneBeatDraft, project: Project): SceneBeatDraft {
  const { scene, beat } = targetFor(draft, project);
  if (!scene || draft.pending || draft.baseRevision > project.revision) return draft;
  const hasInput = beatHasInput(draft);
  if (hasInput && sceneBeatConflict(draft, project)) return draft;
  if (draft.baseRevision === project.revision && sameBeat(draft.base, beat) && sameTime(draft.sceneDuration, scene.duration)) return draft;
  return { ...draft, baseRevision: project.revision, base: clone(beat), sceneDuration: clone(scene.duration),
    raw: hasInput || !beat ? draft.raw : beatRaw(beat), intent: hasInput && draft.intent,
    allowCreate: beat ? false : draft.allowCreate };
}
/** Explicitly choosing a recovery copy retains its values as author intent. */
export function restoreSceneBeatDraft(draft: SceneBeatDraft, project: Project): SceneBeatDraft {
  return reconcileSceneBeatDraft({ ...draft, intent: true }, project);
}
export function resolveSceneBeatDraft(draft: SceneBeatDraft, project: Project, choice: "saved" | "draft" | "text"): SceneBeatDraft {
  const { scene, beat } = targetFor(draft, project);
  if (!scene) throw new Error("The saved scene was removed. Keep or export the retained beat input; it cannot be sent to another scene.");
  if (beatUnconfirmed(draft)) throw new Error("Review the unconfirmed beat outcome before choosing a new intent.");
  if (choice === "text" && !beat) throw new Error("A removed beat has no saved timing to keep.");
  const raw = choice === "saved" && beat ? beatRaw(beat) : choice === "text" && beat
    ? { ...draft.raw, start: formatBeatTime(beat.start), duration: formatBeatTime(beat.duration) } : draft.raw;
  return { ...draft, baseRevision: project.revision, base: clone(beat), sceneDuration: clone(scene.duration),
    raw, intent: choice !== "saved", allowCreate: !beat && choice === "draft", pending: null };
}
export function prepareSceneBeatSubmission(draft: SceneBeatDraft, project: Project, operation: BeatOperation, id: string): {
  draft: SceneBeatDraft; change: BeatChange;
} {
  const { scene, beat } = targetFor(draft, project);
  if (!scene || sceneBeatConflict(draft, project)) throw new Error("Compare the changed saved beat or scene timing before submitting this draft.");
  if (beatLocked(project, draft.sceneId)) throw new Error("The beat's content or timing is locked.");
  if (beatUnconfirmed(draft)) throw new Error("The original beat outcome is unconfirmed; no request is replayed.");
  if (!uuid(id)) throw new Error("A local beat comparison identity is required.");
  if (operation === "remove" && !beat) throw new Error("This beat is already absent from the saved scene.");
  if (!beat && (!draft.allowCreate || scene.beats.length >= 32)) throw new Error("Review recreation and the 32-beat scene limit before saving.");
  const value = operation === "upsert" ? sceneBeatDraftValue(draft, scene.duration) : null;
  if (operation === "upsert" && sameBeat(value, beat)) throw new Error("This beat already matches the saved project.");
  const change: BeatChange = operation === "upsert"
    ? { type: "upsert_scene_beat", scene_id: draft.sceneId, beat: value! }
    : { type: "remove_scene_beat", scene_id: draft.sceneId, beat_id: draft.beatId };
  return { change, draft: { ...draft, baseRevision: project.revision, base: clone(beat), sceneDuration: clone(scene.duration),
    intent: true, pending: { id, revision: project.revision, sequence: draft.sequence, operation,
      raw: clone(draft.raw), value: clone(value), sceneDuration: clone(scene.duration), outcome: "UNKNOWN" } } };
}
export function retainSceneBeatFailure(draft: SceneBeatDraft, id: string, diagnostic: string, notSent = false): SceneBeatDraft {
  if (draft.pending?.id !== id) return draft;
  return { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic: diagnostic.slice(0, 4000) } };
}
/** Only the live original callback can supply originalId and its returned Project. */
export function acknowledgeSceneBeatDraft(draft: SceneBeatDraft, originalId: string, receipt: Project): SceneBeatDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== originalId) return draft;
  const { scene, beat } = targetFor(draft, receipt);
  if (!scene || receipt.revision !== pending.revision + 1 || !sameTime(scene.duration, pending.sceneDuration)
    || !sameBeat(beat, pending.value) || (pending.operation === "upsert" && !pending.value))
    throw new Error("The original beat reply does not confirm the exact submitted operation and revision.");
  const later = draft.sequence !== pending.sequence;
  return { ...draft, baseRevision: receipt.revision, base: clone(beat), sceneDuration: clone(scene.duration),
    raw: later || !beat ? draft.raw : beatRaw(beat), intent: later,
    allowCreate: false, pending: null };
}

export interface BeatOutcomeTransport {
  readProject(projectId: string): Promise<Project>;
  readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}
export interface BeatOutcomeReview {
  scope: string;
  pending: BeatPending;
  project: Project;
  events: ProjectEvent[];
  matchingValueRevisions: number[];
}
function sameObserved(left: Project, right: Project, draft: SceneBeatDraft): boolean {
  if (sceneBeatScope(left, draft.sceneId, draft.beatId) !== sceneBeatScope(right, draft.sceneId, draft.beatId)
    || left.revision !== right.revision) return false;
  const a = beatTarget(left, draft.sceneId, draft.beatId), b = beatTarget(right, draft.sceneId, draft.beatId);
  return Boolean(a.scene && b.scene && sameTime(a.scene.duration, b.scene.duration) && sameBeat(a.beat, b.beat));
}
export function beatOutcomeMatches(draft: SceneBeatDraft, project: Project, review: BeatOutcomeReview | null): boolean {
  return Boolean(review && beatUnconfirmed(draft) && review.scope === draft.scope
    && sceneBeatScope(project, draft.sceneId, draft.beatId) === draft.scope
    && JSON.stringify(draft.pending) === JSON.stringify(review.pending) && sameObserved(project, review.project, draft));
}
function eventMatches(event: ProjectEvent, draft: SceneBeatDraft, pending: BeatPending): boolean {
  if (event.change.scene_id !== draft.sceneId) return false;
  if (pending.operation === "remove") return event.change.type === "remove_scene_beat" && event.change.beat_id === draft.beatId;
  if (event.change.type !== "upsert_scene_beat") return false;
  try { return sameBeat(cleanBeat(event.change.beat), pending.value); } catch { return false; }
}
export async function inspectSceneBeatOutcome(draft: SceneBeatDraft, selected: Project, transport: BeatOutcomeTransport): Promise<BeatOutcomeReview> {
  targetFor(draft, selected);
  if (!beatUnconfirmed(draft)) throw new Error("There is no unconfirmed beat operation to inspect.");
  const pending = clone(draft.pending!);
  const observed = clone(await transport.readProject(selected.id));
  if (!sameObserved(observed, selected, draft)) throw new Error("The saved project changed. Reload it before reviewing this beat outcome.");
  if (!safeInteger(observed.revision) || observed.revision < pending.revision
    || observed.revision - pending.revision > MAX_BEAT_HISTORY) throw new Error("The beat history is outside the bounded review window.");
  const events: ProjectEvent[] = [];
  let cursor = pending.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision))
      throw new Error("The saved beat history is incomplete.");
    for (const event of page) {
      if (!event || !safeInteger(event.revision) || event.revision !== cursor + 1 || event.revision > observed.revision
        || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved beat history is not a contiguous revision sequence.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  if (!sameObserved(await transport.readProject(selected.id), observed, draft))
    throw new Error("The saved project changed during beat review. Read it again.");
  return { scope: draft.scope, pending, project: observed, events,
    matchingValueRevisions: events.filter(event => eventMatches(event, draft, pending)).map(event => event.revision) };
}
/** A separate editor intent; this neither identifies nor acknowledges the old operation. */
export function releaseReviewedSceneBeat(draft: SceneBeatDraft, project: Project, review: BeatOutcomeReview): SceneBeatDraft {
  if (!beatOutcomeMatches(draft, project, review)) throw new Error("Read the current saved beat and its full history before continuing.");
  return reconcileSceneBeatDraft({ ...draft, pending: null }, project);
}

export type BeatStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface BeatCopy {
  schema: "motionwright.scene-beat-recovery/1";
  scope: string;
  writerId: string;
  updatedAt: number;
  draft: SceneBeatDraft;
}
export interface BeatCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface BeatCopyEntry { key: string; scope: string; writerId: string; raw: string; value: BeatCopy | null }
function cleanTime(value: unknown): RationalTime {
  if (!record(value) || typeof value.num !== "string" || typeof value.den !== "string") throw new Error("The beat comparison time is unreadable.");
  const result = { num: value.num, den: value.den }; formatBeatTime(result); return result;
}
function cleanBeat(value: unknown): Beat {
  if (!record(value) || !uuid(value.id) || typeof value.label !== "string" || !value.label.trim() || bytes(value.label) > 160
    || typeof value.objective !== "string" || bytes(value.objective) > 4000) throw new Error("The beat comparison snapshot is unreadable.");
  return { id: value.id, label: value.label, objective: value.objective, start: cleanTime(value.start), duration: cleanTime(value.duration) };
}
function cleanRaw(value: unknown): BeatRaw {
  if (!record(value) || BEAT_FIELDS.some(field => typeof value[field] !== "string"
    || (value[field] as string).length > (field === "objective" ? 4000 : field === "label" ? 160 : 96)))
    throw new Error("The recovered beat input is unreadable or exceeds its field limits.");
  return Object.fromEntries(BEAT_FIELDS.map(field => [field, value[field]])) as BeatRaw;
}
function cleanDraft(value: unknown, scope: string): SceneBeatDraft {
  const identity = beatScopeIdentity(scope);
  if (!record(value) || value.schema !== "motionwright.scene-beat-draft/1" || value.scope !== scope
    || value.sceneId !== identity[3] || value.beatId !== identity[4] || !uuid(value.sceneId) || !uuid(value.beatId)
    || !safeInteger(value.baseRevision) || !safeInteger(value.sequence) || typeof value.intent !== "boolean" || typeof value.allowCreate !== "boolean")
    throw new Error("The beat recovery target does not match its editing scope.");
  const base = value.base === null ? null : cleanBeat(value.base), sceneDuration = cleanTime(value.sceneDuration), raw = cleanRaw(value.raw);
  if (base && (base.id !== value.beatId || !inspectBeatTiming(base, beatRaw(base), sceneDuration).ok))
    throw new Error("The original beat snapshot does not belong to its retained scene interval.");
  if (BigInt(sceneDuration.num) <= 0n || (base && value.allowCreate)) throw new Error("The saved beat recovery interval is invalid.");
  let pending: BeatPending | null = null;
  if (value.pending !== null) {
    const sent = value.pending;
    if (!record(sent) || !uuid(sent.id) || !safeInteger(sent.revision) || sent.revision !== value.baseRevision
      || !safeInteger(sent.sequence) || sent.sequence > value.sequence || (sent.operation !== "upsert" && sent.operation !== "remove")
      || (sent.outcome !== "UNKNOWN" && sent.outcome !== "NOT_SENT")
      || (sent.diagnostic !== undefined && (typeof sent.diagnostic !== "string" || sent.diagnostic.length > 4000)))
      throw new Error("The pending beat comparison snapshot is unreadable.");
    const submitted = sent.value === null ? null : cleanBeat(sent.value), submittedRaw = cleanRaw(sent.raw), submittedDuration = cleanTime(sent.sceneDuration);
    if (!sameTime(submittedDuration, sceneDuration) || (sent.operation === "remove" ? submitted !== null || base === null
      : submitted === null || submitted.id !== value.beatId || !inspectBeatTiming(submitted, submittedRaw, submittedDuration).ok))
      throw new Error("The pending beat operation does not match its retained source.");
    if (submitted) {
      const timing = inspectBeatTiming(submitted, submittedRaw, submittedDuration);
      if (!timing.ok || !sameTime(timing.start, submitted.start) || !sameTime(timing.duration, submitted.duration)
        || submitted.objective !== submittedRaw.objective || (submitted.label !== submittedRaw.label && submitted.label !== submittedRaw.label.trim()))
        throw new Error("The pending beat raw input differs from its submitted values.");
    }
    pending = { id: sent.id, revision: sent.revision, sequence: sent.sequence, operation: sent.operation,
      raw: submittedRaw, value: submitted, sceneDuration: submittedDuration,
      // A recovered label cannot prove that its previous runtime stopped before dispatch.
      outcome: "UNKNOWN", ...(sent.diagnostic === undefined ? {} : { diagnostic: sent.diagnostic as string }) };
  }
  return { schema: "motionwright.scene-beat-draft/1", scope, sceneId: value.sceneId, beatId: value.beatId,
    baseRevision: value.baseRevision, base, sceneDuration, raw, sequence: value.sequence,
    intent: value.intent, allowCreate: value.allowCreate, pending };
}
export function decodeBeatCopy(raw: string, scope: string, writerId: string): BeatCopy {
  if (bytes(raw) > MAX_BEAT_COPY_BYTES) throw new Error("The beat recovery copy exceeds 128 KiB.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.schema !== "motionwright.scene-beat-recovery/1" || value.scope !== scope
    || value.writerId !== writerId || !uuid(writerId) || !safeInteger(value.updatedAt))
    throw new Error("The beat recovery copy is unreadable or belongs to another writer.");
  return { schema: "motionwright.scene-beat-recovery/1", scope, writerId, updatedAt: value.updatedAt, draft: cleanDraft(value.draft, scope) };
}
export function encodeBeatCopy(copy: BeatCopy): string {
  const raw = JSON.stringify(copy); decodeBeatCopy(raw, copy.scope, copy.writerId); return raw;
}
/** A portable copy is an untrusted candidate, never a save/receipt authority. */
export function decodeBeatFile(raw: string, project: Project, sceneId: string): BeatCopy {
  if (bytes(raw) > MAX_BEAT_COPY_BYTES) throw new Error("The beat recovery file exceeds 128 KiB.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || typeof value.scope !== "string" || typeof value.writerId !== "string")
    throw new Error("The beat recovery file has no readable editing identity.");
  const copy = decodeBeatCopy(raw, value.scope, value.writerId);
  if (beatScopeProject(copy.scope) !== beatProjectScope(project) || copy.draft.sceneId !== sceneId)
    throw new Error("The beat file belongs to another project, generation, branch or scene.");
  if (!project.scenes.some(scene => scene.id === sceneId)) throw new Error("The original saved scene is unavailable.");
  return copy;
}
function copyIdentity(key: string): { scope: string; writerId: string } | null {
  try {
    const tail = key.slice(BEAT_RECOVERY_PREFIX.length), split = tail.lastIndexOf(":"), scope = decodeURIComponent(tail.slice(0, split));
    beatScopeIdentity(scope);
    const writerId = tail.slice(split + 1);
    return split > 0 && uuid(writerId) ? { scope, writerId } : null;
  } catch { return null; }
}
export function readBeatCopies(storage: BeatStorage, project: Project, sceneId?: string): { entries: BeatCopyEntry[]; limited: boolean } {
  const entries: BeatCopyEntry[] = [], owner = beatProjectScope(project);
  let limited = storage.length > MAX_STORAGE_KEYS;
  for (let index = 0; index < Math.min(storage.length, MAX_STORAGE_KEYS); index++) {
    const key = storage.key(index);
    if (!key?.startsWith(BEAT_RECOVERY_PREFIX)) continue;
    const identity = copyIdentity(key);
    if (!identity || beatScopeProject(identity.scope) !== owner || (sceneId && beatScopeScene(identity.scope) !== sceneId)) continue;
    if (entries.length >= MAX_BEAT_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key);
    if (raw === null) continue;
    let value: BeatCopy | null = null;
    try { value = decodeBeatCopy(raw, identity.scope, identity.writerId); } catch { /* Preserve unreadable original bytes. */ }
    entries.push({ key, ...identity, raw, value });
  }
  return { entries, limited };
}
/** Verify both bytes and this tab's pointer before admitting a Project command. */
export function writeBeatCopy(storage: BeatStorage, session: Pick<Storage, "getItem" | "setItem">,
  draft: SceneBeatDraft, custody: BeatCustody): BeatCustody {
  const key = beatCopyKey(draft.scope, custody.writerId), pointerKey = beatPointerKey(draft.scope);
  const pointer = session.getItem(pointerKey);
  if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another beat writer changed this tab's recovery pointer. Earlier copies were kept.");
  if (storage.getItem(key) !== custody.expectedRaw) throw new Error("The beat recovery changed in another window. Earlier copies were kept.");
  const raw = encodeBeatCopy({ schema: "motionwright.scene-beat-recovery/1", scope: draft.scope,
    writerId: custody.writerId, updatedAt: Date.now(), draft });
  if (custody.expectedRaw !== null) {
    const previous = decodeBeatCopy(custody.expectedRaw, draft.scope, custody.writerId);
    if (!beatHasWork(previous.draft) && JSON.stringify(previous.draft) !== JSON.stringify(draft))
      throw new Error("A clean beat checkpoint is immutable. Use a separate recovery writer.");
    // A byte-identical retained draft needs verification, not a timestamp-only
    // rewrite. This keeps the prior copy immutable when an explicit comparison
    // forks a new author intent immediately afterward.
    if (JSON.stringify(previous.draft) === JSON.stringify(draft) && pointer === custody.writerId) {
      if (storage.getItem(key) !== custody.expectedRaw || session.getItem(pointerKey) !== custody.writerId)
        throw new Error("The beat recovery changed while its existing copy was being verified.");
      custody.expectedPointer = custody.writerId; return custody;
    }
  }
  let total = bytes(key) + bytes(raw), count = 1;
  if (storage.length > MAX_STORAGE_KEYS) throw new Error("The local recovery key budget is full. Earlier input was kept.");
  for (let index = 0; index < storage.length; index++) {
    const existing = storage.key(index);
    if (!existing?.startsWith(BEAT_RECOVERY_PREFIX) || existing === key) continue;
    total += bytes(existing) + bytes(storage.getItem(existing) ?? ""); count++;
  }
  if (total > MAX_BEAT_RECOVERY_BYTES || count > MAX_BEAT_COPIES)
    throw new Error("Beat recovery is full (256 copies / 4 MiB). Remove only copies you no longer need; author input was kept.");
  if (storage.getItem(key) !== custody.expectedRaw || session.getItem(pointerKey) !== pointer)
    throw new Error("Beat recovery changed before the write. Earlier copies were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The beat recovery write was not confirmed.");
  custody.expectedRaw = raw;
  session.setItem(pointerKey, custody.writerId);
  if (session.getItem(pointerKey) !== custody.writerId) throw new Error("The beat recovery writer pointer was not confirmed.");
  if (storage.getItem(key) !== raw) throw new Error("The beat recovery changed before its writer pointer was confirmed.");
  custody.expectedPointer = custody.writerId;
  return custody;
}
/** Stage UNKNOWN under a new verified pointer before releasing a separate intent. */
export function retainReviewedBeatIntent(storage: BeatStorage, session: Pick<Storage, "getItem" | "setItem">,
  before: SceneBeatDraft, next: SceneBeatDraft, original: BeatCustody, writerId: string): BeatCustody {
  if (!beatUnconfirmed(before) || next.pending || before.scope !== next.scope || !uuid(writerId)
    || writerId === original.writerId || original.expectedRaw === null) throw new Error("The reviewed beat recovery fork is invalid.");
  const key = beatCopyKey(before.scope, original.writerId), originalRaw = storage.getItem(key);
  if (originalRaw === null || originalRaw !== original.expectedRaw
    || JSON.stringify(decodeBeatCopy(originalRaw, before.scope, original.writerId).draft) !== JSON.stringify(before))
    throw new Error("The earlier unconfirmed beat input was not retained exactly.");
  const custody: BeatCustody = { writerId, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeBeatCopy(storage, session, before, custody);
  if (storage.getItem(key) !== originalRaw) throw new Error("The earlier beat copy changed while review was staged.");
  writeBeatCopy(storage, session, next, custody);
  if (storage.getItem(key) !== originalRaw) throw new Error("The earlier unconfirmed beat copy changed during review.");
  return custody;
}
