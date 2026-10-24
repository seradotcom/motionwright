import { useEffect, useRef, useState } from "react";
import { parseBeatTime } from "./sceneBeatTiming";
import { bootstrap, projectHistory } from "./api";
import { sameValue } from "./creativeProduction";
import { graphReviewAnchor, isGraphReviewResource } from "./semanticGraph";
import type { Change, CreativeReview, Project, ProjectEvent, RationalTime, ReviewKind } from "./types";

export type ReviewChange = Extract<Change, { type: "add_review" | "resolve_review" | "reopen_review" }>;
export type ReviewTarget = { kind: "comment"; id: string } | { kind: "resolution"; id: string } | { kind: "alternative"; id: string };
export interface ReviewRaw {
  kind: ReviewKind; timeMode: "resource" | "playhead" | "range";
  rangeStart: string; rangeEnd: string; body: string; resolution: string;
  direction: "positive" | "negative"; rationale: string;
}
export interface ReviewPending {
  id: string; revision: number; sequence: number; change: ReviewChange;
  raw: ReviewRaw; priorIds: string[]; priorReview: CreativeReview | null;
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic?: string;
}
export interface ReviewDraft {
  scope: string; baseRevision: number; sequence: number; intent: boolean;
  raw: ReviewRaw; clean: ReviewRaw; pending: ReviewPending | null;
}
export const REVIEW_RECOVERY_PREFIX = "motionwright.review-recovery.v1:";
export const REVIEW_POINTER_PREFIX = "motionwright.review-writer.v1:";
export const MAX_REVIEW_COPY_BYTES = 128 * 1024;
export const MAX_REVIEW_COPIES = 256;
export const MAX_REVIEW_RECOVERY_BYTES = 4 * 1024 * 1024;
const MAX_STORAGE_KEYS = 4096;
const fields = ["kind", "timeMode", "rangeStart", "rangeEnd", "body", "resolution", "direction", "rationale"] as const;
const clone = <T,>(value: T): T => structuredClone(value);
const same = sameValue;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const number = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0;
const exactKeys = (value: Record<string, unknown>, keys: readonly string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
export const reviewOwner = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const reviewScope = (project: Project, target: ReviewTarget) =>
  JSON.stringify([project.id, project.generation, project.active_branch, target.kind, target.id]);
export function reviewIdentity(scope: string): [string, string, string, ReviewTarget["kind"], string] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 5 || !value.slice(0, 3).every(uuid)
    || !["comment", "resolution", "alternative"].includes(value[3]) || typeof value[4] !== "string"
    || (value[3] === "comment" ? !isGraphReviewResource(value[4]) && (!/^(project|scene|claim):/.test(value[4]) || value[4].split(":").length !== 2 || !uuid(value[4].split(":")[1])) : !uuid(value[4])))
    throw new Error("The review recovery owner or target is invalid.");
  const [id, generation, branch, kind, target] = value as [string, string, string, ReviewTarget["kind"], string];
  if (kind === "comment" && target.startsWith("project:") && target !== "project:" + id)
    throw new Error("A project review cannot belong to another project.");
  return [id, generation, branch, kind, target];
}
const ownerOf = (draft: ReviewDraft) => JSON.stringify(reviewIdentity(draft.scope).slice(0, 3));
const targetOf = (draft: ReviewDraft): ReviewTarget => {
  const identity = reviewIdentity(draft.scope); return { kind: identity[3], id: identity[4] };
};
function assertOwner(draft: ReviewDraft, project: Project): ReviewTarget {
  if (ownerOf(draft) !== reviewOwner(project)) throw new Error("This review draft belongs to another project, generation or branch.");
  return targetOf(draft);
}
function assertTarget(draft: ReviewDraft, project: Project): ReviewTarget {
  const target = assertOwner(draft, project);
  if (target.kind === "comment" && target.id.startsWith("claim:") && !project.brief.claims.some(claim => "claim:" + claim.id === target.id))
    throw new Error("The reviewed claim is no longer present. Its raw input was kept.");
  if (target.kind === "comment" && target.id.startsWith("scene:") && !project.scenes.some(scene => "scene:" + scene.id === target.id))
    throw new Error("The reviewed scene is no longer present. Its raw input was kept.");
  if (target.kind === "comment" && isGraphReviewResource(target.id) && !graphReviewAnchor(project, target.id))
    throw new Error("The reviewed Graph identity or profile is no longer present. Its raw input was kept.");
  if (target.kind === "resolution" && !project.reviews.some(review => review.id === target.id))
    throw new Error("The reviewed item is no longer present. Its resolution input was kept.");
  if (target.kind === "alternative" && (!project.branches.some(branch => branch.id === target.id && branch.id !== project.active_branch)
    || !project.branch_workspaces.some(workspace => workspace.branch_id === target.id)))
    throw new Error("The saved alternative is no longer available. Its reason was kept.");
  return target;
}
export function createReviewDraft(project: Project, target: ReviewTarget): ReviewDraft {
  const raw: ReviewRaw = { kind: "creative", timeMode: "resource", rangeStart: "", rangeEnd: "",
    body: "", resolution: "", direction: "positive", rationale: "" };
  const draft = { scope: reviewScope(project, target), baseRevision: project.revision, sequence: 0,
    intent: false, raw, clean: clone(raw), pending: null };
  reviewIdentity(draft.scope); return draft;
}
export const reviewHasInput = (draft: ReviewDraft) => draft.intent || !same(draft.raw, draft.clean);
export const reviewHasWork = (draft: ReviewDraft) => reviewHasInput(draft) || draft.pending !== null;
export const reviewUnknown = (draft: ReviewDraft) => draft.pending?.outcome === "UNKNOWN";
export function editReviewDraft<K extends keyof ReviewRaw>(draft: ReviewDraft, field: K, value: ReviewRaw[K]): ReviewDraft {
  return { ...draft, raw: { ...draft.raw, [field]: value }, intent: true, sequence: draft.sequence + 1 };
}
export function reconcileReviewDraft(draft: ReviewDraft, project: Project): ReviewDraft {
  assertOwner(draft, project);
  // A property observation never acknowledges, clears or rebases author intent.
  return reviewHasWork(draft) ? draft : { ...draft, baseRevision: project.revision };
}
export function advanceReviewDraft(draft: ReviewDraft, project: Project): ReviewDraft {
  assertTarget(draft, project);
  if (reviewUnknown(draft)) throw new Error("The original review outcome must remain separate from a new intent.");
  return { ...draft, baseRevision: project.revision, sequence: draft.sequence + 1, intent: true, pending: null };
}
const fraction = (time: RationalTime) => ({ n: BigInt(time.num), d: BigInt(time.den) });
const compare = (left: RationalTime, right: RationalTime) => {
  const a = fraction(left), b = fraction(right), delta = a.n * b.d - b.n * a.d;
  return delta < 0n ? -1 : delta > 0n ? 1 : 0;
};
const sum = (left: RationalTime, right: RationalTime): RationalTime => {
  const a = fraction(left), b = fraction(right);
  return { num: (a.n * b.d + b.n * a.d).toString(), den: (a.d * b.d).toString() };
};
/** Use the existing exact author-time parser; incomplete inputs stay raw. */
export function reviewTimes(project: Project, resource: string, raw: ReviewRaw, playhead: number): {
  start: RationalTime | null; end: RationalTime | null;
} {
  const graph = isGraphReviewResource(resource) ? graphReviewAnchor(project, resource) : null;
  if (isGraphReviewResource(resource) && !graph) throw new Error("The saved Graph review source is unavailable.");
  if (raw.timeMode === "resource") return { start: graph?.start ?? null, end: graph?.end ?? null };
  const scene = resource.startsWith("scene:") ? project.scenes.find(item => "scene:" + item.id === resource) : null;
  if (resource !== "project:" + project.id && !scene && !graph) throw new Error("The review resource is unavailable.");
  const zero = { num: "0", den: "1" }, one = { num: "1", den: "1" };
  const ends = [...project.scenes.map(item => sum(item.start, item.duration)),
    ...project.audio.voice_tracks.map(track => track.measured_duration),
    ...project.audio.transcript.map(segment => segment.end), ...project.audio.cues.map(cue => cue.at),
    ...project.markers.map(marker => marker.at)];
  const lower = graph?.start ?? scene?.start ?? zero;
  const upper = graph?.end ?? (scene ? sum(scene.start, scene.duration) : ends.reduce((a, b) => compare(a, b) < 0 ? b : a, one));
  const start = parseBeatTime(raw.timeMode === "playhead" ? String(playhead) : raw.rangeStart);
  const end = raw.timeMode === "range" ? parseBeatTime(raw.rangeEnd) : null;
  if (compare(start, lower) < 0 || compare(start, upper) > 0 || (end && (compare(end, start) < 0 || compare(end, upper) > 0)))
    throw new Error("Review time must stay within its resource; the end cannot precede the start.");
  return { start, end };
}
export function prepareReviewSubmission(draft: ReviewDraft, project: Project, change: ReviewChange, id: string): ReviewDraft {
  const target = assertTarget(draft, project);
  if (!uuid(id)) throw new Error("A local original-attempt identity is required.");
  if (reviewUnknown(draft)) throw new Error("The original review outcome is UNKNOWN. No request is replayed.");
  if (draft.baseRevision !== project.revision) throw new Error("Review the draft against the current revision before saving.");
  if (target.kind === "comment" && (change.type !== "add_review" || change.resource !== target.id
    || change.kind !== draft.raw.kind || change.body !== draft.raw.body.trim() || !change.body))
    throw new Error("The comment request does not match its exact draft and target.");
  if (target.kind === "alternative" && (change.type !== "add_review" || change.kind !== "creative"
    || change.resource !== "project:" + project.id || !draft.raw.rationale.trim()
    || !change.body.startsWith("MANUAL " + draft.raw.direction.toUpperCase() + " CREATIVE ALTERNATIVE REVIEW\n")
    || !change.body.endsWith("Human editorial reason: " + draft.raw.rationale.trim())))
    throw new Error("The alternative request does not match the selected author reason.");
  if (target.kind === "resolution" && (change.type === "add_review" || change.review_id !== target.id
    || (change.type === "resolve_review" && (!draft.raw.resolution.trim() || change.resolution !== draft.raw.resolution.trim()))))
    throw new Error("The resolution request does not match its exact draft and review.");
  if (!validChange(change)) throw new Error("The review request is outside the supported domain contract.");
  if (target.kind === "comment" && change.type === "add_review") {
    const raw = draft.raw, graph = graphReviewAnchor(project, change.resource);
    const exactRange = raw.timeMode !== "range" || (same(change.start, parseBeatTime(raw.rangeStart)) && same(change.end, parseBeatTime(raw.rangeEnd)));
    const resourceOnly = raw.timeMode !== "resource" || (same(change.start, graph?.start ?? null) && same(change.end, graph?.end ?? null));
    const pointOnly = raw.timeMode !== "playhead" || (change.start !== null && change.end === null);
    if (!exactRange || !resourceOnly || !pointOnly || change.locale !== (graph?.locale ?? null) || change.profile_id !== (graph?.profile_id ?? null))
      throw new Error("The request does not preserve its exact authored review time.");
  }
  const priorReview = change.type === "add_review" ? null : project.reviews.find(review => review.id === change.review_id) ?? null;
  if (change.type === "resolve_review" && priorReview?.status === "dismissed") throw new Error("A dismissed review cannot be resolved.");
  return { ...draft, pending: { id, revision: project.revision, sequence: draft.sequence,
    change: clone(change), raw: clone(draft.raw), priorIds: project.reviews.map(review => review.id),
    priorReview: clone(priorReview), outcome: "UNKNOWN" } };
}
export function retainReviewFailure(draft: ReviewDraft, id: string, diagnostic: string, notSent = false): ReviewDraft {
  if (draft.pending?.id !== id) return draft;
  return { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic: diagnostic.slice(0, 4000) } };
}
function confirmReview(pending: ReviewPending, project: Project): boolean {
  const change = pending.change;
  if (change.type === "add_review") {
    const added = project.reviews.filter(review => !pending.priorIds.includes(review.id));
    if (added.length !== 1) return false;
    const review = added[0];
    return uuid(review.id) && review.kind === change.kind && review.body === change.body
      && review.status === "open" && review.resolution === null && review.resolved_at === null
      && review.anchor.resource === change.resource && review.anchor.branch_id === project.active_branch
      && review.anchor.revision === pending.revision && same(review.anchor.start, change.start)
      && same(review.anchor.end, change.end) && review.anchor.locale === change.locale
      && review.anchor.profile_id === change.profile_id;
  }
  const review = project.reviews.find(item => item.id === change.review_id), prior = pending.priorReview;
  if (!review || !prior || review.id !== prior.id || review.kind !== prior.kind || review.body !== prior.body
    || !same(review.anchor, prior.anchor) || review.created_at !== prior.created_at) return false;
  return change.type === "resolve_review"
    ? review.status === "resolved" && review.resolution === change.resolution && typeof review.resolved_at === "string" && !!review.resolved_at
    : review.status === "needs_recheck" && review.resolution === null && review.resolved_at === null;
}
/** Only the live original callback supplies this attempt identity and its Project. */
export function acknowledgeReviewDraft(draft: ReviewDraft, originalId: string, receipt: Project): ReviewDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== originalId) return draft;
  assertOwner(draft, receipt);
  if (receipt.revision !== pending.revision + 1 || !confirmReview(pending, receipt))
    throw new Error("The original reply did not confirm this exact review operation. Input and UNKNOWN were kept.");
  const later = draft.sequence !== pending.sequence;
  const target = targetOf(draft), raw = clone(draft.raw);
  if (!later && pending.change.type !== "reopen_review")
    raw[target.kind === "comment" ? "body" : target.kind === "alternative" ? "rationale" : "resolution"] = "";
  const retain = later || (pending.change.type === "reopen_review" && reviewHasInput(draft));
  return { ...draft, baseRevision: receipt.revision, raw, clean: retain ? draft.clean : clone(raw), intent: retain, pending: null };
}

export const MAX_REVIEW_HISTORY = 2048;
export interface ReviewOutcomeTransport {
  readProject(projectId: string): Promise<Project>;
  readHistory(project: Project, afterRevision: number, limit: number): Promise<ProjectEvent[]>;
}
export interface ReviewOutcome {
  scope: string; pending: ReviewPending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[];
}
function sameObserved(left: Project, right: Project, draft: ReviewDraft): boolean {
  return ownerOf(draft) === reviewOwner(left) && ownerOf(draft) === reviewOwner(right)
    && left.revision === right.revision && same(left, right);
}
export function reviewOutcomeMatches(draft: ReviewDraft, project: Project, outcome: ReviewOutcome | null): boolean {
  return Boolean(outcome && reviewUnknown(draft) && outcome.scope === draft.scope
    && same(draft.pending, outcome.pending) && sameObserved(project, outcome.project, draft));
}
/** Read the selected saved source twice around a contiguous bounded journal.
 * Matching authored values are observations, never an original-request receipt.
 */
export async function inspectReviewOutcome(draft: ReviewDraft, selected: Project, transport: ReviewOutcomeTransport): Promise<ReviewOutcome> {
  assertOwner(draft, selected);
  if (!reviewUnknown(draft)) throw new Error("There is no unconfirmed review attempt to inspect.");
  const pending = clone(draft.pending!);
  const observed = clone(await transport.readProject(selected.id));
  if (!sameObserved(observed, selected, draft)) throw new Error("The saved project changed. Reload it before reviewing the original outcome.");
  if (!number(observed.revision) || observed.revision < pending.revision || observed.revision - pending.revision > MAX_REVIEW_HISTORY)
    throw new Error("The review history is outside its bounded inspection window.");
  const events: ProjectEvent[] = [];
  let cursor = pending.revision;
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < observed.revision))
      throw new Error("The saved review history is incomplete.");
    for (const event of page) {
      if (!event || !number(event.revision) || event.revision !== cursor + 1 || event.revision > observed.revision
        || !event.change || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The saved review history is not a contiguous revision sequence.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < observed.revision);
  if (!sameObserved(await transport.readProject(selected.id), observed, draft))
    throw new Error("The saved project changed during review. Read it and the history again.");
  return { scope: draft.scope, pending, project: observed, events,
    matchingValueRevisions: events.filter(event => same(event.change, pending.change)).map(event => event.revision) };
}
/** Begin a distinct author intent only after explicit source/history review.
 * Unchanged submitted text starts blank so an add_review cannot be replayed by
 * the next Save. Later author text is retained. The original remains UNKNOWN.
 */
export function separateReviewedIntent(draft: ReviewDraft, project: Project, outcome: ReviewOutcome): ReviewDraft {
  if (!reviewOutcomeMatches(draft, project, outcome)) throw new Error("Read the current saved review and its complete history before starting a separate intent.");
  assertTarget(draft, project);
  const target = targetOf(draft), pending = draft.pending!;
  // An author may deliberately return to the submitted value after another
  // edit. Equality cannot erase that later intent (A -> B -> A), including
  // after reload. The persisted sequence is the same causal guard as the
  // original-callback acknowledgement above; it is never request authority.
  const later = draft.sequence !== pending.sequence;
  const raw = clone(draft.raw);
  if (!later) raw[target.kind === "comment" ? "body" : target.kind === "alternative" ? "rationale" : "resolution"] = "";
  return { ...draft, baseRevision: project.revision, raw, sequence: draft.sequence + 1, intent: true, pending: null };
}

export type ReviewStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem">;
export interface ReviewCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface ReviewCopy { schema: "motionwright.review-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: ReviewDraft }
export const reviewCopyKey = (scope: string, writerId: string) => REVIEW_RECOVERY_PREFIX + encodeURIComponent(scope) + ":" + writerId;
export const reviewPointerKey = (scope: string) => REVIEW_POINTER_PREFIX + encodeURIComponent(scope);
function validRaw(raw: unknown): raw is ReviewRaw {
  return object(raw) && exactKeys(raw, fields) && fields.every(field => typeof raw[field] === "string")
    && ["creative", "technical", "editorial"].includes(String(raw.kind))
    && ["resource", "playhead", "range"].includes(String(raw.timeMode)) && ["positive", "negative"].includes(String(raw.direction))
    && ["rangeStart", "rangeEnd"].every(field => String(raw[field]).length <= 256)
    && String(raw.body).length <= 8000 && String(raw.resolution).length <= 4000 && String(raw.rationale).length <= 1200;
}
function validChange(value: unknown): value is ReviewChange {
  if (!object(value)) return false;
  if (value.type === "reopen_review") return exactKeys(value, ["type", "review_id"]) && uuid(value.review_id);
  if (value.type === "resolve_review") return exactKeys(value, ["type", "review_id", "resolution"]) && uuid(value.review_id)
    && typeof value.resolution === "string" && bytes(value.resolution) <= 4000 && !!value.resolution.trim();
  const time = (input: unknown) => input === null || (object(input) && exactKeys(input, ["num", "den"])
    && typeof input.num === "string" && typeof input.den === "string"
    && /^-?(?:0|[1-9]\d{0,18})$/.test(input.num) && /^[1-9]\d{0,18}$/.test(input.den));
  return value.type === "add_review" && exactKeys(value, ["type", "kind", "resource", "body", "start", "end", "locale", "profile_id"])
    && ["creative", "technical", "editorial"].includes(String(value.kind))
    && typeof value.resource === "string" && (/^(project|scene|claim):[0-9a-f-]{36}$/i.test(value.resource) || isGraphReviewResource(value.resource))
    && typeof value.body === "string" && bytes(value.body) <= 8000 && !!value.body.trim() && time(value.start) && time(value.end)
    && (value.locale === null || typeof value.locale === "string") && (value.profile_id === null || uuid(value.profile_id));
}
export function decodeReviewCopy(raw: string, scope: string, writerId: string): ReviewCopy {
  if (bytes(raw) > MAX_REVIEW_COPY_BYTES) throw new Error("The review recovery exceeds its 128 KiB copy budget.");
  const value: unknown = JSON.parse(raw);
  reviewIdentity(scope);
  if (!object(value) || !exactKeys(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || value.schema !== "motionwright.review-recovery/1" || value.scope !== scope || value.writerId !== writerId
    || !uuid(writerId) || !number(value.updatedAt) || !object(value.draft)) throw new Error("The review recovery envelope is invalid.");
  const draft = value.draft;
  if (!exactKeys(draft, ["scope", "baseRevision", "sequence", "intent", "raw", "clean", "pending"]) || draft.scope !== scope
    || !number(draft.baseRevision) || !number(draft.sequence) || typeof draft.intent !== "boolean"
    || !validRaw(draft.raw) || !validRaw(draft.clean)) throw new Error("The review recovery draft is invalid.");
  if (draft.pending !== null) {
    const pending = draft.pending;
    if (!object(pending) || !exactKeys(pending, ["id", "revision", "sequence", "change", "raw", "priorIds", "priorReview", "outcome",
        ...(pending.diagnostic === undefined ? [] : ["diagnostic"])]) || !uuid(pending.id) || !number(pending.revision) || pending.revision !== draft.baseRevision
      || !number(pending.sequence) || pending.sequence > draft.sequence || !validRaw(pending.raw) || !validChange(pending.change)
      || !["UNKNOWN", "NOT_SENT"].includes(String(pending.outcome)) || !Array.isArray(pending.priorIds)
      || !pending.priorIds.every(uuid) || new Set(pending.priorIds).size !== pending.priorIds.length
      || !(pending.priorReview === null || object(pending.priorReview))
      || (pending.diagnostic !== undefined && (typeof pending.diagnostic !== "string" || pending.diagnostic.length > 4000)))
      throw new Error("The review recovery attempt is invalid.");
  }
  return clone(value as unknown as ReviewCopy);
}
export function encodeReviewCopy(copy: ReviewCopy): string {
  const raw = JSON.stringify(copy); decodeReviewCopy(raw, copy.scope, copy.writerId); return raw;
}
export function writeReviewCopy(storage: ReviewStorage, tab: Pick<Storage, "getItem" | "setItem">,
  draft: ReviewDraft, custody: ReviewCustody): void {
  const key = reviewCopyKey(draft.scope, custody.writerId), pointerKey = reviewPointerKey(draft.scope);
  const pointer = tab.getItem(pointerKey);
  if (pointer !== custody.expectedPointer && pointer !== custody.writerId) throw new Error("Another writer changed this tab's review pointer. Earlier copies were kept.");
  if (storage.getItem(key) !== custody.expectedRaw) throw new Error("Another window changed the review copy. Its bytes were kept.");
  const raw = encodeReviewCopy({ schema: "motionwright.review-recovery/1", scope: draft.scope,
    writerId: custody.writerId, updatedAt: Date.now(), draft });
  if (custody.expectedRaw !== null) {
    const earlier = decodeReviewCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!reviewHasWork(earlier) && !same(earlier, draft)) throw new Error("A clean review checkpoint is immutable; use a new writer.");
    if (same(earlier, draft) && pointer === custody.writerId) {
      if (storage.getItem(key) !== custody.expectedRaw || tab.getItem(pointerKey) !== custody.writerId)
        throw new Error("The review recovery changed while its existing bytes were being verified.");
      custody.expectedPointer = custody.writerId; return;
    }
  }
  if (storage.length > MAX_STORAGE_KEYS) throw new Error("The recovery key budget is full. Current input remains in the editor.");
  let total = bytes(key) + bytes(raw), count = 1;
  for (let index = 0; index < storage.length; index++) {
    const existing = storage.key(index);
    if (!existing?.startsWith(REVIEW_RECOVERY_PREFIX) || existing === key) continue;
    total += bytes(existing) + bytes(storage.getItem(existing) ?? ""); count++;
  }
  if (count > MAX_REVIEW_COPIES || total > MAX_REVIEW_RECOVERY_BYTES)
    throw new Error("Review recovery is full (256 copies / 4 MiB). Export current input before removing only copies you no longer need.");
  if (storage.getItem(key) !== custody.expectedRaw || tab.getItem(pointerKey) !== pointer)
    throw new Error("Review recovery changed before writing. Earlier copies were kept.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The review recovery write was not confirmed.");
  custody.expectedRaw = raw;
  tab.setItem(pointerKey, custody.writerId);
  if (tab.getItem(pointerKey) !== custody.writerId || storage.getItem(key) !== raw)
    throw new Error("The review recovery pointer or bytes were not confirmed.");
  custody.expectedPointer = custody.writerId;
}


/** Verify a separate UNKNOWN checkpoint before publishing the new intent's
 * pointer. A partial failure leaves an UNKNOWN copy recoverable, never a
 * silently released request. The original writer's bytes remain immutable.
 */
export function retainSeparateReviewIntent(storage: ReviewStorage, tab: Pick<Storage, "getItem" | "setItem">,
  before: ReviewDraft, next: ReviewDraft, original: ReviewCustody, writerId: string): ReviewCustody {
  if (!reviewUnknown(before) || next.pending || before.scope !== next.scope || !uuid(writerId)
    || writerId === original.writerId || original.expectedRaw === null)
    throw new Error("The separate review recovery transition is invalid.");
  const key = reviewCopyKey(before.scope, original.writerId), originalRaw = storage.getItem(key);
  if (originalRaw === null || originalRaw !== original.expectedRaw
    || !same(decodeReviewCopy(originalRaw, before.scope, original.writerId).draft, before))
    throw new Error("The original UNKNOWN review input was not retained exactly.");
  const custody: ReviewCustody = { writerId, expectedRaw: null, expectedPointer: original.expectedPointer };
  writeReviewCopy(storage, tab, before, custody);
  if (storage.getItem(key) !== originalRaw) throw new Error("The original review copy changed while inspection was staged.");
  writeReviewCopy(storage, tab, next, custody);
  if (storage.getItem(key) !== originalRaw) throw new Error("The original UNKNOWN copy changed while the separate intent was saved.");
  return custody;
}

interface Session {
  draft: ReviewDraft; custody: ReviewCustody; inFlight: boolean;
  storageError: string | null; readError: string | null; error: string | null; notice: string | null;
  outcome: ReviewOutcome | null; reviewing: boolean;
}
// Author buffers only. Canonical Project mutation remains owned by App's gate.
// These sessions preserve input during navigation and never replay an operation.
const sessions = new Map<string, Session>(), listeners = new Map<string, Set<() => void>>();
let unloadProtected = false;
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const notify = (session: Session) => listeners.get(ownerOf(session.draft))?.forEach(listener => listener());
const busy = (project: Project) => [...sessions.values()].some(session => session.inFlight && ownerOf(session.draft) === reviewOwner(project));
function backup(session: Session): boolean {
  try {
    if (session.readError) throw new Error(session.readError);
    if (session.custody.expectedRaw !== null) {
      const earlier = decodeReviewCopy(session.custody.expectedRaw, session.draft.scope, session.custody.writerId).draft;
      if (!reviewHasWork(earlier) && !same(earlier, session.draft))
        session.custody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: session.custody.expectedPointer };
    }
    writeReviewCopy(window.localStorage, window.sessionStorage, session.draft, session.custody);
    session.storageError = null; return true;
  } catch (reason) {
    session.storageError = message(reason) + " Saving is blocked until this input has a verified recovery copy.";
    return false;
  }
}
function sessionFor(project: Project, target: ReviewTarget): Session {
  if (!unloadProtected) {
    unloadProtected = true;
    window.addEventListener("beforeunload", event => {
      if ([...sessions.values()].some(session => session.storageError && reviewHasWork(session.draft))) {
        event.preventDefault(); event.returnValue = "";
      }
    });
  }
  const scope = reviewScope(project, target), cached = sessions.get(scope);
  if (cached) {
    cached.draft = reconcileReviewDraft(cached.draft, project); return cached;
  }
  let pointer: string | null = null, recovered: ReviewDraft | null = null, readError: string | null = null;
  try {
    pointer = window.sessionStorage.getItem(reviewPointerKey(scope));
    if (pointer !== null) {
      const raw = window.localStorage.getItem(reviewCopyKey(scope, pointer));
      if (raw === null) throw new Error("This tab's review recovery copy is missing.");
      recovered = decodeReviewCopy(raw, scope, pointer).draft;
    }
  } catch (reason) { readError = message(reason) + " Earlier recovery bytes were kept."; }
  const session: Session = { draft: reconcileReviewDraft(recovered ?? createReviewDraft(project, target), project),
    custody: { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer },
    inFlight: false, readError, storageError: readError, error: null, notice: null, outcome: null, reviewing: false };
  sessions.set(scope, session);
  if (recovered && reviewHasWork(recovered)) backup(session);
  return session;
}
export function useReviewDraft(project: Project, target: ReviewTarget, commit: (change: Change) => Promise<Project>) {
  const session = sessionFor(project, target), owner = reviewOwner(project);
  const [, refresh] = useState(0);
  const projectRef = useRef(project), visibleScope = useRef(session.draft.scope), mounted = useRef(true);
  projectRef.current = project; visibleScope.current = session.draft.scope;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const listener = () => refresh(value => value + 1), set = listeners.get(owner) ?? new Set<() => void>();
    set.add(listener); listeners.set(owner, set);
    return () => { set.delete(listener); if (!set.size) listeners.delete(owner); };
  }, [owner]);
  const edit = <K extends keyof ReviewRaw>(field: K, value: ReviewRaw[K]) => {
    session.draft = editReviewDraft(session.draft, field, value); session.error = null; session.notice = null;
    backup(session); notify(session);
  };
  const submit = async (change: ReviewChange): Promise<boolean> => {
    if (busy(project) || reviewUnknown(session.draft)) return false;
    const id = crypto.randomUUID();
    try {
      session.draft = prepareReviewSubmission(session.draft, project, change, id);
    } catch (reason) { session.error = message(reason); notify(session); return false; }
    session.inFlight = true; session.error = null; session.notice = null;
    if (!backup(session)) {
      session.draft = retainReviewFailure(session.draft, id, "No request was sent because recovery was not confirmed.", true);
      session.inFlight = false; backup(session); notify(session); return false;
    }
    notify(session);
    try {
      const receipt = await commit(change);
      session.draft = acknowledgeReviewDraft(session.draft, id, receipt);
      session.notice = "The original reply confirmed this review. Any later author input was retained.";
      backup(session);
      return true;
    } catch (reason) {
      session.draft = retainReviewFailure(session.draft, id, message(reason));
      session.error = message(reason); backup(session); return false;
    } finally { session.inFlight = false; notify(session); }
  };
  const advance = () => {
    try {
      if (session.inFlight || reviewUnknown(session.draft)) throw new Error("Keep the original attempt until its outcome is known.");
      if (!backup(session)) { notify(session); return; }
      const before = session.custody;
      const next = advanceReviewDraft(session.draft, project);
      const candidate: ReviewCustody = { writerId: crypto.randomUUID(), expectedRaw: null, expectedPointer: before.expectedPointer };
      writeReviewCopy(window.localStorage, window.sessionStorage, next, candidate);
      session.custody = candidate; session.draft = next; session.error = null; session.storageError = null;
      session.notice = "The author explicitly retained this draft against the current revision; the earlier copy was kept.";
    } catch (reason) { session.error = message(reason); }
    notify(session);
  };

  const readOutcome = async () => {
    if (session.inFlight || session.reviewing || !reviewUnknown(session.draft)) return;
    session.reviewing = true; session.outcome = null; session.error = null; notify(session);
    try {
      const observed = await inspectReviewOutcome(session.draft, projectRef.current, {
        readProject: async id => {
          const boot = await bootstrap(id);
          if (!boot.project || boot.project_selection.status !== "selected") throw new Error("The saved review project is unavailable.");
          return boot.project;
        }, readHistory: projectHistory,
      });
      if (!mounted.current || visibleScope.current !== session.draft.scope) return;
      if (!reviewOutcomeMatches(session.draft, projectRef.current, observed))
        throw new Error("The review owner, target or saved source changed. Inspect it again.");
      session.outcome = observed;
    } catch (reason) { session.error = message(reason); }
    finally { session.reviewing = false; notify(session); }
  };
  const separateIntent = () => {
    if (session.inFlight || session.reviewing || !session.outcome) return;
    try {
      const next = separateReviewedIntent(session.draft, projectRef.current, session.outcome);
      if (!backup(session)) { notify(session); return; }
      const retained = retainSeparateReviewIntent(window.localStorage, window.sessionStorage,
        session.draft, next, session.custody, crypto.randomUUID());
      session.custody = retained; session.draft = next; session.outcome = null;
      session.storageError = null; session.error = null;
      session.notice = "The original UNKNOWN copy is unchanged. This is a separate author intent; no original request was acknowledged or replayed.";
    } catch (reason) { session.storageError = message(reason); session.error = "The original attempt and current author input were kept."; }
    notify(session);
  };

  const retryBackup = () => { backup(session); notify(session); };
  const download = () => {
    // Export is author-input custody only; it grants no Project or reply authority.
    const raw = JSON.stringify({ schema: "motionwright.review-recovery/1", scope: session.draft.scope,
      writerId: session.custody.writerId, updatedAt: Date.now(), draft: session.draft }, null, 2);
    const url = URL.createObjectURL(new Blob([raw], { type: "application/json" })), anchor = document.createElement("a");
    anchor.href = url; anchor.download = "motionwright-review-draft-" + session.custody.writerId + ".json";
    anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return { draft: session.draft, edit, submit, advance, retryBackup, download, readOutcome, separateIntent,
    outcome: reviewOutcomeMatches(session.draft, project, session.outcome) ? session.outcome : null, reviewing: session.reviewing,
    saving: session.inFlight, busy: busy(project), unknown: reviewUnknown(session.draft),
    stale: reviewHasInput(session.draft) && session.draft.baseRevision !== project.revision,
    storageError: session.storageError, error: session.error, notice: session.notice,
    blocked: busy(project) || reviewUnknown(session.draft) || session.storageError !== null
      || (reviewHasInput(session.draft) && session.draft.baseRevision !== project.revision) };
}
