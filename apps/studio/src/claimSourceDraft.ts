import type { Change, Claim, Project, ProjectEvent, SourceReference } from "./types";
import { sameValue } from "./creativeProduction";
import { sameProjectRevision } from "./projectMutation";

export type ClaimFields = Omit<Claim, "id">;
export type ClaimSourceChange = Extract<Change, { type: "add_claim" }> | {
  type: "update_claim"; claim_id: string; expected: ClaimFields; replacement: ClaimFields;
};
export interface ClaimRaw {
  text: string; context: string; sourceKind: "none" | SourceReference["kind"];
  assetId: string; url: string; projectRevision: string; note: string;
  pinEnabled: boolean; pin: string;
}
export interface ClaimPending {
  id: string; revision: number; sequence: number; change: ClaimSourceChange;
  raw: ClaimRaw; priorIds: string[]; outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface ClaimDraft {
  scope: string; target: string; baseRevision: number; base: ClaimFields | null;
  raw: ClaimRaw; sequence: number; intent: boolean; pending: ClaimPending | null;
}
const clone = <T,>(value: T): T => structuredClone(value);
const equal = sameValue;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const uuid = (value: unknown): value is string => typeof value === "string"
  && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: readonly string[]) =>
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const rawKeys = ["text", "context", "sourceKind", "assetId", "url", "projectRevision", "note", "pinEnabled", "pin"] as const;
const kinds = ["none", "asset", "url", "project_revision", "manual_note"];
export const CLAIM_COPY_PREFIX = "motionwright.claim-source-recovery.v1:";
export const CLAIM_POINTER_PREFIX = "motionwright.claim-source-writer.v1:";
export const MAX_CLAIM_COPY_BYTES = 256 * 1024;
export const MAX_CLAIM_COPIES = 256;
export const MAX_CLAIM_RECOVERY_BYTES = 4 * 1024 * 1024;
const MAX_STORAGE_KEYS = 4096;

export function claimIdentity(scope: string): [string, string, string, string] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 4 || !value.slice(0, 3).every(uuid)
    || (value[3] !== "new" && !uuid(value[3]))) throw new Error("This claim draft has an invalid owner or target.");
  return value as [string, string, string, string];
}
export const claimScope = (project: Project, target: string) =>
  JSON.stringify([project.id, project.generation, project.active_branch, target]);
export const claimCopyKey = (scope: string, writer: string) => CLAIM_COPY_PREFIX + scope + ":" + writer;
export const claimPointerKey = (scope: string) => CLAIM_POINTER_PREFIX + scope;
export const claimFields = (claim: Claim): ClaimFields => ({
  text: claim.text, source: clone(claim.source), context: claim.context, source_revision: claim.source_revision,
});
export function claimSaved(project: Project, target: string): ClaimFields | null {
  if (target === "new") return null;
  const claim = project.brief.claims.find(value => value.id === target);
  if (!claim) throw new Error("This claim is no longer in the selected project and branch. Your draft is retained.");
  return claimFields(claim);
}
export function claimRaw(fields: ClaimFields | null): ClaimRaw {
  const source = fields?.source;
  return {
    text: fields?.text ?? "", context: fields?.context ?? "", sourceKind: source?.kind ?? "none",
    assetId: source?.kind === "asset" ? source.asset_id : "",
    url: source?.kind === "url" ? source.url : "",
    projectRevision: source?.kind === "project_revision" ? String(source.revision) : "",
    note: source?.kind === "manual_note" ? source.label : "",
    pinEnabled: fields?.source_revision !== null && fields?.source_revision !== undefined,
    pin: fields?.source_revision ?? "",
  };
}
function owner(draft: ClaimDraft, project: Project) {
  claimIdentity(draft.scope);
  if (draft.scope !== claimScope(project, draft.target)) throw new Error("This claim draft belongs to another project, generation or branch.");
}
export function createClaimDraft(project: Project, target: string): ClaimDraft {
  const scope = claimScope(project, target); claimIdentity(scope);
  const base = claimSaved(project, target);
  return { scope, target, baseRevision: project.revision, base, raw: claimRaw(base), sequence: 0, intent: false, pending: null };
}
export const claimHasWork = (draft: ClaimDraft) => draft.intent || draft.pending !== null;
export const claimUnknown = (draft: ClaimDraft) => draft.pending?.outcome === "UNKNOWN";
export function editClaimDraft<K extends keyof ClaimRaw>(draft: ClaimDraft, field: K, value: ClaimRaw[K]): ClaimDraft {
  return { ...draft, raw: { ...draft.raw, [field]: value }, sequence: draft.sequence + 1, intent: true };
}
export function recoverClaimAfterReadFailure(current: ClaimDraft, recovered: ClaimDraft, editedWhileBlocked: boolean): ClaimDraft {
  claimIdentity(current.scope); claimIdentity(recovered.scope);
  if (current.scope !== recovered.scope || current.target !== recovered.target)
    throw new Error("The recovered claim draft belongs to another owner or target.");
  // Recovering an existing attempt is not a new author edit. In particular,
  // a failed initial read must not turn saved values into a later intention.
  if (!editedWhileBlocked) return recovered;
  if (current.pending && !equal(current.pending, recovered.pending))
    throw new Error("Keep the active uncertain attempt separately before recovering a different attempt.");
  if (claimUnknown(recovered)) return { ...recovered, raw: clone(current.raw), intent: true,
    sequence: Math.max(recovered.sequence, current.sequence) + 1 };
  return current;
}
export interface ClaimPointerObservation { observed: boolean; value: string | null }
export function readClaimRecoveryPointer(storage: Pick<ClaimStorage, "getItem">, scope: string, prior: ClaimPointerObservation): ClaimPointerObservation {
  claimIdentity(scope);
  const value = storage.getItem(claimPointerKey(scope));
  if (prior.observed && value !== prior.value)
    throw new Error("The earlier claim pointer changed; its original bytes remain protected.");
  return { observed: true, value };
}
export function reconcileClaimDraft(draft: ClaimDraft, project: Project): ClaimDraft {
  owner(draft, project);
  if (claimHasWork(draft)) return draft;
  const next = createClaimDraft(project, draft.target);
  return equal(next, draft) ? draft : next;
}
export function claimConflict(draft: ClaimDraft, project: Project): boolean {
  try { owner(draft, project); return draft.baseRevision !== project.revision || !equal(draft.base, claimSaved(project, draft.target)); }
  catch { return true; }
}
export function claimLocked(project: Project, target: string): boolean {
  return project.locks.some(lock => lock.kind === "content"
    && (lock.resource === "project:" + project.id || (target !== "new" && lock.resource === "claim:" + target)));
}
function text(value: string, limit: number, label: string, required = false) {
  if ((required && !value.trim()) || bytes(value) > limit) throw new Error(label + (required ? " is required and must fit " : " must fit ") + limit + " UTF-8 bytes.");
}
export function parseClaimRaw(raw: ClaimRaw, project: Project, target: string): ClaimFields {
  text(raw.text, 4000, "Claim text", true); text(raw.context, 4000, "Claim context");
  if (raw.pinEnabled) text(raw.pin, 256, "Source version");
  let source: SourceReference | null = null;
  switch (raw.sourceKind) {
    case "none": break;
    case "url": text(raw.url, 2048, "Source URL", true); source = { kind: "url", url: raw.url }; break;
    case "manual_note": text(raw.note, 512, "Source note", true); source = { kind: "manual_note", label: raw.note }; break;
    case "asset":
      if (!project.assets.some(asset => asset.id === raw.assetId)) throw new Error("Choose a source asset from this project.");
      source = { kind: "asset", asset_id: raw.assetId }; break;
    case "project_revision": {
      if (!/^(0|[1-9][0-9]*)$/.test(raw.projectRevision)) throw new Error("Source revision must be a complete non-negative integer. The raw input is kept.");
      const revision = Number(raw.projectRevision);
      if (!Number.isSafeInteger(revision) || revision > project.revision) throw new Error("Source revision must be an exact saved revision of this project.");
      source = { kind: "project_revision", revision }; break;
    }
    default: throw new Error("Choose a supported source type.");
  }
  for (const capture of (project.production_design?.captures ?? []).filter(item => item.claim_id === target)) {
    if (raw.context !== "synthetic-first-party-capture-validation" || source?.kind !== "project_revision"
      || source.revision !== capture.source_revision)
      throw new Error("This claim's context and source revision are bound to an existing capture. Keep that link; create new evidence for a different source.");
  }
  return { text: raw.text, context: raw.context, source, source_revision: raw.pinEnabled ? raw.pin : null };
}
export function claimChange(draft: ClaimDraft, project: Project): ClaimSourceChange {
  owner(draft, project);
  if (draft.pending) throw new Error("Keep the original attempt separate before preparing another save.");
  if (claimConflict(draft, project)) throw new Error("Compare your draft with the current saved claim before choosing its new base.");
  if (claimLocked(project, draft.target)) throw new Error("Claim content is locked. Your draft remains editable.");
  const replacement = parseClaimRaw(draft.raw, project, draft.target);
  if (draft.target === "new") {
    if (project.brief.claims.length >= 512) throw new Error("This project has reached its existing 512-claim limit.");
    return { type: "add_claim", ...replacement };
  }
  if (!draft.base) throw new Error("The existing claim's original fields are missing.");
  if (equal(replacement, draft.base)) throw new Error("There are no changes to this saved claim.");
  return { type: "update_claim", claim_id: draft.target, expected: clone(draft.base), replacement };
}
export function prepareClaimSubmission(draft: ClaimDraft, project: Project, id: string): { draft: ClaimDraft; change: ClaimSourceChange } {
  if (!uuid(id)) throw new Error("A local attempt identity is required.");
  const change = claimChange(draft, project);
  const pending: ClaimPending = { id, revision: project.revision, sequence: draft.sequence, change: clone(change),
    raw: clone(draft.raw), priorIds: project.brief.claims.map(item => item.id), outcome: "UNKNOWN", diagnostic: null };
  return { change, draft: { ...draft, pending } };
}
export function retainClaimFailure(draft: ClaimDraft, id: string, diagnostic: string, notSent = false): ClaimDraft {
  if (draft.pending?.id !== id) return draft;
  return { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } };
}
export function acknowledgeClaimDraft(draft: ClaimDraft, id: string, project: Project, submittedProject: Project): ClaimDraft {
  owner(draft, project); owner(draft, submittedProject);
  const pending = draft.pending;
  if (!pending || pending.id !== id || pending.outcome !== "UNKNOWN" || project.revision !== pending.revision + 1)
    throw new Error("The original claim response does not match its exact owner and revision.");
  const expected = pending.change.type === "add_claim" ? {
    text: pending.change.text, source: pending.change.source, context: pending.change.context, source_revision: pending.change.source_revision,
  } : pending.change.replacement;
  const matches = project.brief.claims.filter(item => equal(claimFields(item), expected)
    && (pending.change.type === "add_claim" ? !pending.priorIds.includes(item.id) : item.id === pending.change.claim_id));
  if (matches.length !== 1) throw new Error("The original claim response does not contain the submitted claim.");
  if (submittedProject.revision !== pending.revision || !equal(submittedProject.brief.claims.map(item => item.id), pending.priorIds))
    throw new Error("The original claim source snapshot does not match this attempt.");
  const expectedClaims = pending.change.type === "add_claim"
    ? [...submittedProject.brief.claims, matches[0]]
    : submittedProject.brief.claims.map(item => item.id === draft.target ? { id: item.id, ...expected } : item);
  if (!equal(project.brief.claims, expectedClaims))
    throw new Error("The original claim response did not preserve the other claims and their order.");
  const later = draft.sequence !== pending.sequence;
  const base = draft.target === "new" ? null : claimFields(matches[0]);
  return { ...draft, baseRevision: project.revision, base, raw: later ? draft.raw : claimRaw(base), pending: null, intent: later };
}
export function resolveClaimDraft(draft: ClaimDraft, project: Project, choice: "saved" | "draft"): ClaimDraft {
  owner(draft, project);
  if (claimUnknown(draft)) throw new Error("Read the original uncertain outcome before starting a separate edit.");
  const base = claimSaved(project, draft.target);
  return { ...draft, baseRevision: project.revision, base, raw: choice === "saved" ? claimRaw(base) : draft.raw,
    sequence: draft.sequence + 1, intent: choice === "draft", pending: null };
}
export interface ClaimOutcomeTransport {
  readProject(id: string): Promise<Project>;
  readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}
export interface ClaimOutcomeReview {
  scope: string; sequence: number; pending: ClaimPending; project: Project; events: ProjectEvent[]; matchingValueRevisions: number[];
}
export function claimOutcomeMatches(draft: ClaimDraft, project: Project, review: ClaimOutcomeReview | null): boolean {
  return !!review && claimUnknown(draft) && review.scope === draft.scope && review.sequence === draft.sequence
    && claimScope(project, draft.target) === draft.scope && equal(review.pending, draft.pending)
    && sameProjectRevision(project, review.project) && equal(project, review.project);
}
export async function inspectClaimOutcome(draft: ClaimDraft, selected: Project, transport: ClaimOutcomeTransport): Promise<ClaimOutcomeReview> {
  owner(draft, selected);
  if (!claimUnknown(draft)) throw new Error("There is no uncertain claim attempt to inspect.");
  const pending = clone(draft.pending!), project = clone(await transport.readProject(selected.id));
  owner(draft, project);
  if (!sameProjectRevision(project, selected) || !equal(project, selected))
    throw new Error("Reload the saved project before comparing this outcome.");
  if (!integer(project.revision) || project.revision < pending.revision || project.revision - pending.revision > 2048)
    throw new Error("History is outside the bounded 2048-revision review window.");
  const events: ProjectEvent[] = []; let cursor = pending.revision;
  do {
    const page = await transport.readHistory(project, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (!page.length && cursor < project.revision)) throw new Error("Saved history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > project.revision || typeof event.change?.type !== "string"
        || typeof event.created_at !== "string" || !event.created_at) throw new Error("Saved history is not a complete contiguous sequence.");
      events.push(clone(event)); cursor = event.revision;
    }
  } while (cursor < project.revision);
  const after = await transport.readProject(selected.id);
  if (!sameProjectRevision(after, project) || !equal(after, project)) throw new Error("The saved project changed during comparison. Read it again.");
  return { scope: draft.scope, sequence: draft.sequence, pending, project, events,
    matchingValueRevisions: events.filter(event => equal(event.change, pending.change)).map(event => event.revision) };
}
export function releaseClaimOutcome(draft: ClaimDraft, project: Project, review: ClaimOutcomeReview): ClaimDraft {
  if (!claimOutcomeMatches(draft, project, review)) throw new Error("Read the current project and its complete history before a separate edit.");
  if (project.revision <= draft.pending!.revision) throw new Error("The original request could still commit on this base. Keep it UNKNOWN until a later saved revision makes that base obsolete.");
  const base = claimSaved(project, draft.target);
  // An unchanged uncertain Add is not silently restaged as another Add.
  const raw = draft.target === "new" && draft.sequence === draft.pending!.sequence ? claimRaw(null) : draft.raw;
  return { ...draft, baseRevision: project.revision, base, raw, pending: null, intent: true, sequence: draft.sequence + 1 };
}

export type ClaimStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface ClaimCustody { writerId: string; expectedRaw: string | null; expectedPointer: string | null }
export interface ClaimCopy { schema: "motionwright.claim-source-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: ClaimDraft }
export interface ClaimRecoveryEntry { key: string; writerId: string; raw: string; value: ClaimCopy | null; error: string | null }
function rawShape(value: unknown): value is ClaimRaw {
  return record(value) && exact(value, rawKeys) && rawKeys.filter(key => key !== "pinEnabled").every(key => typeof value[key] === "string")
    && typeof value.pinEnabled === "boolean" && kinds.includes(String(value.sourceKind));
}
function sourceShape(value: unknown): boolean {
  if (value === null) return true;
  if (!record(value)) return false;
  switch (value.kind) {
    case "asset": return exact(value, ["kind", "asset_id"]) && uuid(value.asset_id);
    case "url": return exact(value, ["kind", "url"]) && typeof value.url === "string";
    case "manual_note": return exact(value, ["kind", "label"]) && typeof value.label === "string";
    case "project_revision": return exact(value, ["kind", "revision"]) && integer(value.revision);
    default: return false;
  }
}
function fieldsShape(value: unknown): value is ClaimFields {
  return record(value) && exact(value, ["text", "source", "context", "source_revision"])
    && typeof value.text === "string" && typeof value.context === "string" && sourceShape(value.source)
    && (value.source_revision === null || typeof value.source_revision === "string");
}
function changeShape(value: unknown): value is ClaimSourceChange {
  if (!record(value)) return false;
  if (value.type === "add_claim" && exact(value, ["type", "text", "source", "context", "source_revision"])) {
    const { type: _, ...fields } = value; return fieldsShape(fields);
  }
  return value.type === "update_claim" && exact(value, ["type", "claim_id", "expected", "replacement"])
    && uuid(value.claim_id) && fieldsShape(value.expected) && fieldsShape(value.replacement);
}
function draftShape(value: unknown): value is ClaimDraft {
  if (!record(value) || !exact(value, ["scope", "target", "baseRevision", "base", "raw", "sequence", "intent", "pending"])
    || typeof value.scope !== "string" || typeof value.target !== "string" || !integer(value.baseRevision)
    || !integer(value.sequence) || typeof value.intent !== "boolean" || !rawShape(value.raw)
    || (value.target === "new" ? value.base !== null : !fieldsShape(value.base))) return false;
  const identity = claimIdentity(value.scope);
  if (identity[3] !== value.target) return false;
  const p = value.pending;
  if (p === null) return true;
  return record(p) && exact(p, ["id", "revision", "sequence", "change", "raw", "priorIds", "outcome", "diagnostic"])
    && uuid(p.id) && integer(p.revision) && p.revision === value.baseRevision && integer(p.sequence) && p.sequence <= value.sequence
    && changeShape(p.change) && rawShape(p.raw) && Array.isArray(p.priorIds) && p.priorIds.length <= 512
    && p.priorIds.every(uuid) && new Set(p.priorIds).size === p.priorIds.length
    && (p.outcome === "UNKNOWN" || p.outcome === "NOT_SENT") && (p.diagnostic === null || typeof p.diagnostic === "string")
    && (value.target === "new" ? p.change.type === "add_claim"
      : p.change.type === "update_claim" && p.change.claim_id === value.target && equal(p.change.expected, value.base));
}
export function encodeClaimCopy(draft: ClaimDraft, writerId: string, updatedAt = Date.now()): string {
  if (!uuid(writerId) || !integer(updatedAt) || !draftShape(draft)) throw new Error("The claim recovery record is invalid.");
  const raw = JSON.stringify({ schema: "motionwright.claim-source-recovery/1", scope: draft.scope, writerId, updatedAt, draft } satisfies ClaimCopy);
  if (bytes(raw) > MAX_CLAIM_COPY_BYTES) throw new Error("This claim recovery copy exceeds 256 KiB. Export and shorten the raw input before saving.");
  return raw;
}
export function decodeClaimCopy(raw: string, scope: string, writerId?: string): ClaimCopy {
  if (bytes(raw) > MAX_CLAIM_COPY_BYTES) throw new Error("This claim recovery copy exceeds its size limit.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || JSON.stringify(value) !== raw || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"])
    || value.schema !== "motionwright.claim-source-recovery/1" || value.scope !== scope || !uuid(value.writerId)
    || (writerId !== undefined && value.writerId !== writerId) || !integer(value.updatedAt) || !draftShape(value.draft)
    || value.draft.scope !== scope) throw new Error("This claim recovery copy has an incompatible owner, shape or encoding. Its original bytes were retained.");
  const copy = clone(value as unknown as ClaimCopy);
  // A recovered local checkpoint is not evidence that an operation was never sent.
  if (copy.draft.pending) copy.draft.pending.outcome = "UNKNOWN";
  return copy;
}
function budget(storage: ClaimStorage, key: string, raw: string) {
  if (storage.length > MAX_STORAGE_KEYS) throw new Error("Local recovery enumeration exceeds its safe limit.");
  let count = 0, size = 0;
  for (let index = 0; index < storage.length; index++) {
    const current = storage.key(index); if (!current?.startsWith(CLAIM_COPY_PREFIX) || current === key) continue;
    const value = storage.getItem(current);
    if (value !== null) { count++; size += bytes(value); }
  }
  if (count >= MAX_CLAIM_COPIES || size + bytes(raw) > MAX_CLAIM_RECOVERY_BYTES)
    throw new Error("Claim recovery storage is full. Export and explicitly remove an older copy; no author input was deleted.");
}
export function writeClaimCopy(storage: ClaimStorage, pointers: ClaimStorage, draft: ClaimDraft, custody: ClaimCustody): string {
  const key = claimCopyKey(draft.scope, custody.writerId), pointer = claimPointerKey(draft.scope);
  const check = () => {
    if (storage.getItem(key) !== custody.expectedRaw || pointers.getItem(pointer) !== custody.expectedPointer)
      throw new Error("Another writer changed this claim copy or pointer. Earlier bytes were kept.");
  };
  check();
  let raw: string | null = null;
  if (custody.expectedRaw !== null) {
    const prior = decodeClaimCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (equal(prior, draft)) {
      check();
      if (custody.expectedPointer === custody.writerId) return custody.expectedRaw;
      // Equal data bytes do not confirm this tab's recovery pointer.
      raw = custody.expectedRaw;
    } else if (!claimHasWork(prior)) throw new Error("A saved claim checkpoint must remain separate from new author input.");
  }
  if (raw === null) {
    raw = encodeClaimCopy(draft, custody.writerId); budget(storage, key, raw); check();
    storage.setItem(key, raw);
    if (storage.getItem(key) !== raw) throw new Error("The claim copy did not read back exactly. No project change may be sent.");
    custody.expectedRaw = raw;
  }
  if (pointers.getItem(pointer) !== custody.expectedPointer) throw new Error("The claim pointer changed before publication.");
  pointers.setItem(pointer, custody.writerId);
  if (pointers.getItem(pointer) !== custody.writerId) throw new Error("The claim pointer did not read back exactly.");
  custody.expectedPointer = custody.writerId;
  if (storage.getItem(key) !== raw) throw new Error("The claim copy changed during pointer publication.");
  return raw;
}
export function readClaimCopies(storage: ClaimStorage, scope: string): { entries: ClaimRecoveryEntry[]; limited: boolean } {
  const entries: ClaimRecoveryEntry[] = []; const limit = Math.min(storage.length, MAX_STORAGE_KEYS);
  for (let index = 0; index < limit; index++) {
    const key = storage.key(index); if (!key?.startsWith(CLAIM_COPY_PREFIX + scope + ":")) continue;
    const raw = storage.getItem(key); if (raw === null) continue;
    const writerId = key.slice((CLAIM_COPY_PREFIX + scope + ":").length);
    try { entries.push({ key, writerId, raw, value: decodeClaimCopy(raw, scope, writerId), error: null }); }
    catch (reason) { entries.push({ key, writerId, raw, value: null, error: reason instanceof Error ? reason.message : String(reason) }); }
  }
  return { entries, limited: storage.length > MAX_STORAGE_KEYS };
}
export function retainReviewedClaimIntent(storage: ClaimStorage, pointers: ClaimStorage, previous: ClaimDraft,
  next: ClaimDraft, custody: ClaimCustody, writerId: string): ClaimCustody {
  if (!claimUnknown(previous) || next.pending || previous.scope !== next.scope || writerId === custody.writerId)
    throw new Error("A separate claim edit must retain the exact original uncertain attempt.");
  writeClaimCopy(storage, pointers, previous, custody);
  const fork: ClaimCustody = { writerId, expectedRaw: null, expectedPointer: custody.expectedPointer };
  writeClaimCopy(storage, pointers, previous, fork);
  writeClaimCopy(storage, pointers, next, fork);
  return fork;
}
