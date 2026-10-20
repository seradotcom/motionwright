import type { Change, Project, ProjectEvent } from "./types";

export const BRIEF_FIELDS = ["title", "objective", "audience", "constraints", "exclusions"] as const;
export type BriefField = typeof BRIEF_FIELDS[number];
export type BriefGroup = "title" | "brief";
export const BRIEF_FIELD_LABELS: Record<BriefField, string> = {
  title: "Project title", objective: "Production objective", audience: "Audience",
  constraints: "Constraints", exclusions: "Exclusions",
};
type FieldValue = string | string[];
export interface BriefFieldDraft {
  raw: string; base: FieldValue; baseRevision: number;
  // An edit made while an earlier value was being saved can intentionally return
  // to the old base. Keep that intent through unmount/recovery, without treating
  // the value sent to the service as an observed canonical base.
  intent?: true;
}
export interface BriefDraft {
  schema: "motionwright.brief-draft/1";
  scope: string;
  fields: Record<BriefField, BriefFieldDraft>;
  /** Only a comparison snapshot. NEVER a request grant or replay authority. */
  pending?: BriefPending;
}
export interface BriefConflict {
  field: BriefField;
  before: FieldValue;
  current: FieldValue;
  proposed: FieldValue;
  futureRevision: boolean;
}
export interface BriefSubmission {
  scope: string;
  revision: number;
  fields: BriefField[];
  raw: Partial<Record<BriefField, string>>;
  values: Partial<Record<BriefField, FieldValue>>;
  change: Extract<Change, { type: "rename_project" | "set_brief" }>;
}
/** Safe to persist: normalized values are used only to compare with a freshly
 * read Project. There is no change command, grant, token or request ID here. */
export type BriefPending = Pick<BriefSubmission, "scope" | "revision" | "fields" | "raw" | "values"> & {
  runtimeId: string;
  /** A local comparison identity, never the coordinator request_id. */
  intentId?: string;
  /** Missing in an older copy means UNKNOWN, never NOT_SENT or acknowledged. */
  outcome?: "UNKNOWN" | "NOT_SENT";
  diagnostic?: string;
};
/** A page reload imports this module anew. Workspace navigation does not. */
export const BRIEF_EDITOR_RUNTIME_ID = crypto.randomUUID();

const same = (a: FieldValue, b: FieldValue) => JSON.stringify(a) === JSON.stringify(b);
const copyValue = (value: FieldValue): FieldValue => Array.isArray(value) ? [...value] : value;
export const fieldText = (value: FieldValue): string => Array.isArray(value) ? value.join("\n") : value;
export function briefDraftScope(project: Project): string {
  return JSON.stringify([project.id, project.generation, project.active_branch]);
}
export function briefFieldValue(project: Project, field: BriefField): FieldValue {
  return copyValue(field === "title" ? project.title : project.brief[field]);
}
export function proposedBriefValue(field: BriefField, raw: string): FieldValue {
  return field === "constraints" || field === "exclusions"
    ? raw.split("\n").map(value => value.trim()).filter(Boolean)
    : field === "title" ? raw.trim() : raw;
}
function checkScope(draft: BriefDraft, project: Project) {
  if (draft.scope !== briefDraftScope(project)) throw new Error("This brief draft belongs to another project, generation or branch.");
}
export function createBriefDraft(project: Project): BriefDraft {
  return { schema: "motionwright.brief-draft/1", scope: briefDraftScope(project), fields: Object.fromEntries(
    BRIEF_FIELDS.map(field => {
      const base = briefFieldValue(project, field);
      return [field, { raw: fieldText(base), base, baseRevision: project.revision }];
    }),
  ) as Record<BriefField, BriefFieldDraft> };
}
export function editBriefField(draft: BriefDraft, field: BriefField, raw: string, preserveIntent = false): BriefDraft {
  return { ...draft, fields: { ...draft.fields, [field]: {
    ...draft.fields[field], raw, ...(preserveIntent ? { intent: true as const } : {}),
  } } };
}
export function briefFieldHasInput(field: BriefFieldDraft): boolean {
  // Preserve formatting too. Domain normalization happens only in the explicit submission.
  return field.intent === true || field.raw !== fieldText(field.base);
}
export function briefDraftHasWork(draft: BriefDraft): boolean {
  return Boolean(draft.pending) || BRIEF_FIELDS.some(field => briefFieldHasInput(draft.fields[field]));
}
export function briefGroupFields(group: BriefGroup): BriefField[] {
  return group === "title" ? ["title"] : ["objective", "audience", "constraints", "exclusions"];
}
export function briefDraftConflicts(draft: BriefDraft, project: Project): BriefConflict[] {
  checkScope(draft, project);
  return BRIEF_FIELDS.flatMap(field => {
    const input = draft.fields[field], current = briefFieldValue(project, field);
    const proposed = proposedBriefValue(field, input.raw);
    const futureRevision = input.baseRevision > project.revision;
    return briefFieldHasInput(input) && (futureRevision || (!same(current, input.base) && !same(current, proposed)))
      ? [{ field, before: copyValue(input.base), current, proposed, futureRevision }] : [];
  });
}
/** Only untouched fields follow a new document. Dirty fields retain their source
 * until they converge or the author explicitly resolves the overlapping value. */
export function reconcileBriefDraft(draft: BriefDraft, project: Project): BriefDraft {
  checkScope(draft, project);
  // A newer Project is observed state, not a receipt for this attempt.
  // An uncertain pending record survives even a matching r+1 value.
  const fields = { ...draft.fields };
  for (const field of BRIEF_FIELDS) {
    const input = fields[field], current = briefFieldValue(project, field);
    const dirty = briefFieldHasInput(input);
    if (dirty && input.baseRevision > project.revision) continue;
    if (!dirty || same(current, input.base) || same(current, proposedBriefValue(field, input.raw))) {
      fields[field] = { ...input, raw: dirty ? input.raw : fieldText(current), base: current, baseRevision: project.revision };
    }
  }
  return { ...draft, fields };
}
export function resolveBriefField(draft: BriefDraft, project: Project, field: BriefField, choice: "saved" | "draft"): BriefDraft {
  checkScope(draft, project);
  const base = briefFieldValue(project, field);
  return { ...draft, fields: { ...draft.fields, [field]: {
    raw: choice === "saved" ? fieldText(base) : draft.fields[field].raw, base, baseRevision: project.revision,
    ...(choice === "draft" && draft.fields[field].intent ? { intent: true as const } : {}),
  } } };
}
export function briefGroupHasChanges(draft: BriefDraft, project: Project, group: BriefGroup): boolean {
  checkScope(draft, project);
  return briefGroupFields(group).some(field => !same(proposedBriefValue(field, draft.fields[field].raw), briefFieldValue(project, field)));
}
export function prepareBriefSubmission(draft: BriefDraft, project: Project, group: BriefGroup): BriefSubmission {
  const current = reconcileBriefDraft(draft, project), fields = briefGroupFields(group);
  if (briefHasUnconfirmedSubmission(current)) throw new Error("Review the unconfirmed brief submission against the saved Project before sending another change.");
  if (briefDraftConflicts(current, project).some(conflict => fields.includes(conflict.field))) {
    throw new Error("Resolve the overlapping brief fields before saving.");
  }
  const values: Partial<Record<BriefField, FieldValue>> = {}, raw: Partial<Record<BriefField, string>> = {};
  for (const field of fields) {
    const input = current.fields[field];
    raw[field] = input.raw;
    values[field] = briefFieldHasInput(input) ? proposedBriefValue(field, input.raw) : briefFieldValue(project, field);
  }
  if (!briefGroupHasChanges(current, project, group)) throw new Error("These values already match the saved brief.");
  if (group === "title" && !(values.title as string).length) throw new Error("Enter a project title before saving.");
  const change: BriefSubmission["change"] = group === "title"
    ? { type: "rename_project", title: values.title as string }
    : { type: "set_brief", objective: values.objective as string, audience: values.audience as string,
      constraints: [...values.constraints as string[]], exclusions: [...values.exclusions as string[]] };
  return { scope: current.scope, revision: project.revision, fields, raw, values, change };
}
export function retainBriefSubmissionIntent(draft: BriefDraft, project: Project, submission: BriefSubmission): BriefDraft {
  checkScope(draft, project);
  if (submission.scope !== draft.scope || submission.revision !== project.revision) {
    throw new Error("The brief submission does not match this observed project revision.");
  }
  // Mark the fields being changed before leaving the editor or awaiting the
  // service. A remounted editor then preserves a later return to their old base
  // even though the previous component's in-flight reference no longer exists.
  const fields = { ...draft.fields };
  for (const field of submission.fields) {
    if (!same(submission.values[field]!, briefFieldValue(project, field))) fields[field] = { ...fields[field], intent: true };
  }
  return { ...draft, fields };
}
/** Only the actually submitted snapshot is stored. Old callers that use
 * retainBriefSubmissionIntent without sending a command retain their original
 * v1 semantics and never acquire an unconfirmed send by implication. */
export function stageBriefSubmission(
  draft: BriefDraft, project: Project, submission: BriefSubmission,
): BriefDraft {
  const retained = retainBriefSubmissionIntent(draft, project, submission);
  if (briefHasUnconfirmedSubmission(retained)) throw new Error("Review the earlier unconfirmed brief submission first.");
  const pending: BriefPending = {
    scope: submission.scope, revision: submission.revision, runtimeId: BRIEF_EDITOR_RUNTIME_ID, intentId: crypto.randomUUID(), outcome: "UNKNOWN",
    fields: [...submission.fields], raw: { ...submission.raw },
    values: Object.fromEntries(submission.fields.map(field => [
      field, copyValue(submission.values[field]!),
    ])) as BriefPending["values"],
  };
  return { ...retained, pending };
}
/** A matching canonical document, not a request receipt or a renderer claim. */
export function briefSubmissionMatches(
  project: Project, submission: Pick<BriefSubmission, "scope" | "revision" | "fields" | "values">,
): boolean {
  return briefDraftScope(project) === submission.scope && project.revision === submission.revision + 1
    && submission.fields.every(field => same(briefFieldValue(project, field), submission.values[field]!));
}
export function acknowledgeBriefSubmission(
  draft: BriefDraft, project: Project, submission: Pick<BriefSubmission, "scope" | "revision" | "fields" | "raw" | "values">,
  originalReplyObserved = false,
): BriefDraft {
  // Legacy value-only callers retain their behavior. A durable pending send
  // can be consumed here only after its original throwing callback succeeded.
  if (draft.pending && (!originalReplyObserved || !briefPendingMatches(draft.pending, submission))) return draft;
  if (draft.scope !== submission.scope || !briefSubmissionMatches(project, submission)) return draft;
  const fields = { ...draft.fields };
  for (const field of submission.fields) {
    const input = fields[field], base = briefFieldValue(project, field);
    fields[field] = { raw: input.raw === submission.raw[field] ? fieldText(base) : input.raw,
      base, baseRevision: project.revision };
  }
  const completed: BriefDraft = { ...draft, fields };
  delete completed.pending;
  return reconcileBriefDraft(completed, project);
}

/** No request, grant or command is created by any of these recovery helpers. */
export function briefHasUnconfirmedSubmission(draft: BriefDraft): boolean {
  return Boolean(draft.pending && draft.pending.outcome !== "NOT_SENT");
}
export function briefPendingMatches(
  pending: BriefPending | undefined,
  submission: Pick<BriefSubmission, "scope" | "revision" | "fields" | "raw" | "values">,
): boolean {
  return pending !== undefined && pending.scope === submission.scope && pending.revision === submission.revision
    && JSON.stringify(pending.fields) === JSON.stringify(submission.fields)
    && submission.fields.every(field => pending.raw[field] === submission.raw[field]
      && same(pending.values[field]!, submission.values[field]!));
}
export function markBriefNotSent(
  draft: BriefDraft, submission: BriefSubmission, diagnostic: string,
): BriefDraft {
  if (!briefPendingMatches(draft.pending, submission)) throw new Error("The unsent Brief preparation changed.");
  return { ...draft, pending: { ...draft.pending!, outcome: "NOT_SENT", diagnostic } };
}
export function retainBriefUncertainReply(
  draft: BriefDraft, submission: BriefSubmission, diagnostic: string,
): BriefDraft {
  if (!briefPendingMatches(draft.pending, submission)) throw new Error("The unconfirmed Brief attempt changed.");
  return { ...draft, pending: { ...draft.pending!, outcome: "UNKNOWN", diagnostic } };
}

export interface BriefOutcomeTransport {
  readProject: (projectId: string) => Promise<Project>;
  readHistory: (project: Project, afterRevision: number, limit: number) => Promise<ProjectEvent[]>;
}
export interface BriefOutcomeReview {
  scope: string;
  pendingFingerprint: string;
  project: Project;
  events: ProjectEvent[];
  matchingValueRevisions: number[];
}
export const MAX_BRIEF_OUTCOME_EVENTS = 2048;
const pendingFingerprint = (pending: BriefPending) => JSON.stringify(pending);
const sameProject = (left: Project, right: Project) => JSON.stringify(left) === JSON.stringify(right);

function journalHasSubmittedValues(event: ProjectEvent, pending: BriefPending): boolean {
  const expectedType = pending.fields.length === 1 ? "rename_project" : "set_brief";
  return event.change.type === expectedType
    && pending.fields.every(field => same(event.change[field] as FieldValue, pending.values[field]!));
}

/** Read the existing saved Project and journal only. A matching event does
 * not identify the caller's request: this journal has no request identity. */
export async function inspectBriefOutcome(
  draft: BriefDraft, selected: Project, transport: BriefOutcomeTransport,
): Promise<BriefOutcomeReview> {
  checkScope(draft, selected);
  if (!briefHasUnconfirmedSubmission(draft)) throw new Error("There is no unconfirmed Brief attempt to inspect.");
  const pending = structuredClone(draft.pending!);
  const observed = structuredClone(await transport.readProject(selected.id));
  if (briefDraftScope(observed) !== pending.scope || !sameProject(observed, selected)) {
    throw new Error("The saved Project changed. Reload its current state before reviewing the Brief outcome.");
  }
  if (!Number.isSafeInteger(observed.revision) || observed.revision < pending.revision
    || observed.revision - pending.revision > MAX_BRIEF_OUTCOME_EVENTS) {
    throw new Error("The Brief history range is unavailable or exceeds its bounded review window.");
  }
  const events: ProjectEvent[] = [];
  let cursor = pending.revision;
  // Even an unchanged Project performs an actual journal read.
  do {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || page.length > 256 || (page.length === 0 && cursor < observed.revision)) {
      throw new Error("The saved Brief history is incomplete.");
    }
    for (const event of page) {
      if (!event || !Number.isSafeInteger(event.revision) || event.revision !== cursor + 1
        || event.revision > observed.revision || !event.change || typeof event.change.type !== "string"
        || typeof event.created_at !== "string" || !event.created_at) {
        throw new Error("The saved Brief history changed or is not a contiguous revision sequence.");
      }
      events.push(structuredClone(event));
      cursor = event.revision;
    }
  } while (cursor < observed.revision);
  const after = await transport.readProject(selected.id);
  if (briefDraftScope(after) !== pending.scope || !sameProject(after, observed)) {
    throw new Error("The saved Project changed while its Brief history was read. Inspect it again.");
  }
  return {
    scope: pending.scope, pendingFingerprint: pendingFingerprint(pending), project: observed, events,
    matchingValueRevisions: events.filter(event => journalHasSubmittedValues(event, pending)).map(event => event.revision),
  };
}
export function briefOutcomeReviewMatches(
  draft: BriefDraft, project: Project, review: BriefOutcomeReview | null,
): boolean {
  return Boolean(review && briefHasUnconfirmedSubmission(draft)
    && review.scope === draft.scope && briefDraftScope(project) === draft.scope
    && review.pendingFingerprint === pendingFingerprint(draft.pending!)
    && sameProject(review.project, project));
}

/** Pure preparation for an explicit new editor intent. The caller must retain
 * the old UNKNOWN recovery copy and fork its writer before installing this
 * result. This does not acknowledge, deny, replay or discard the old operation. */
export function releaseBriefUnconfirmed(
  draft: BriefDraft, project: Project, review: BriefOutcomeReview,
): BriefDraft {
  checkScope(draft, project);
  if (!briefOutcomeReviewMatches(draft, project, review)) {
    throw new Error("Inspect the current saved Project and complete journal before starting a separate Brief intent.");
  }
  const released: BriefDraft = { ...draft };
  delete released.pending;
  return reconcileBriefDraft(released, project);
}

/** The existing recovery store is the only journal for this editor buffer.
 * All new-copy and pointer writes are read back before returning a released
 * draft. Failures leave the caller blocked; they never invoke a Project write. */
export function retainReviewedBriefIntent(
  storage: DraftStorage, session: Pick<Storage, "getItem" | "setItem">, pointerKey: string,
  original: BriefRecovery, next: BriefDraft, newWriter: string,
): BriefRecovery {
  if (!original.draft || !briefHasUnconfirmedSubmission(original.draft)
    || next.pending || next.scope !== original.scope || !newWriter || newWriter === original.sessionId) {
    throw new Error("The reviewed Brief recovery fork is invalid.");
  }
  function readRetained(expected: BriefRecovery): { raw: string; value: BriefRecovery } {
    const raw = storage.getItem(briefDraftStorageKey(expected.scope, expected.sessionId));
    if (raw === null) throw new Error("The reviewed Brief recovery copy is unavailable.");
    const value = decodeBriefRecovery(raw, expected.scope, expected.sessionId);
    // The existing writer advances recovery timestamps monotonically. Verify
    // all content and retain its actual bytes instead of inventing a timestamp.
    if (value.updatedAt < expected.updatedAt
      || encodeBriefRecovery({ ...value, updatedAt: expected.updatedAt }) !== encodeBriefRecovery(expected)) {
      throw new Error("The reviewed Brief recovery content was not retained exactly.");
    }
    return { raw, value };
  }
  const originalKey = briefDraftStorageKey(original.scope, original.sessionId);
  const existingRaw = storage.getItem(originalKey);
  let retainedOriginal: { raw: string; value: BriefRecovery } | undefined;
  if (existingRaw !== null) {
    const existing = decodeBriefRecovery(existingRaw, original.scope, original.sessionId);
    if (encodeBriefRecovery({ ...existing, updatedAt: original.updatedAt }) === encodeBriefRecovery(original))
      retainedOriginal = { raw: existingRaw, value: existing };
  }
  if (!retainedOriginal) {
    writeBriefRecovery(storage, original);
    retainedOriginal = readRetained(original);
  }
  const pendingCopy: BriefRecovery = { ...retainedOriginal.value, sessionId: newWriter };
  writeBriefRecovery(storage, pendingCopy);
  const retainedPending = readRetained(pendingCopy);
  writeBriefSessionPointer(session, pointerKey, newWriter);
  if (storage.getItem(originalKey) !== retainedOriginal.raw) throw new Error("The earlier Brief recovery changed during review.");
  const released: BriefRecovery = { ...retainedPending.value, draft: next };
  writeBriefRecovery(storage, released);
  const retained = readRetained(released);
  if (storage.getItem(originalKey) !== retainedOriginal.raw) throw new Error("The earlier unconfirmed Brief recovery changed.");
  return retained.value;
}

// Local editor recovery only. No effect grants, Changes or acknowledgements are stored.
/** A durable brief copy is automatically attributable after reload ONLY
 * if this tab's writer pointer is verified. A failed/silent session write is
 * an explicit recovery workflow, not permission to submit a Project change. */
export function writeBriefSessionPointer(
  storage: Pick<Storage, "getItem" | "setItem">, key: string, writerId: string,
): void {
  storage.setItem(key, writerId);
  if (storage.getItem(key) !== writerId) {
    throw new Error("Brief recovery writer pointer was not confirmed.");
  }
}

export const BRIEF_DRAFT_STORAGE_PREFIX = "motionwright.brief-draft.v1:";
export const MAX_BRIEF_DRAFT_BYTES = 1024 * 1024;
export const MAX_BRIEF_RECOVERY_BYTES = 4 * 1024 * 1024;
export const MAX_BRIEF_RECOVERY_COPIES = 64;
export interface BriefRecovery {
  schema: "motionwright.brief-recovery/1";
  scope: string;
  sessionId: string;
  updatedAt: number;
  // A small clean checkpoint keeps an older copy from being silently restored
  // after a completed/discarded edit. Earlier copies remain explicitly available.
  draft: BriefDraft | null;
}
export interface BriefRecoveryEntry { key: string; value: BriefRecovery }
export interface UnreadableBriefRecovery { key: string; sessionId: string; unscoped?: true }
export type DraftStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
const bytes = (text: string) => new TextEncoder().encode(text).length;
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const safeRevision = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
export function briefDraftStorageKey(scope: string, sessionId: string): string {
  return BRIEF_DRAFT_STORAGE_PREFIX + JSON.stringify([scope, sessionId]);
}
/** Strictly bound comparison data, never a replayable command or grant. */
function cleanBriefPending(value: unknown, scope: string): BriefPending {
  if (!record(value) || value.scope !== scope || !safeRevision(value.revision)
    || typeof value.runtimeId !== "string" || !/^[0-9a-f-]{36}$/.test(value.runtimeId)
    || !Array.isArray(value.fields) || !record(value.raw) || !record(value.values)
    || (value.intentId !== undefined && (typeof value.intentId !== "string" || !/^[0-9a-f-]{36}$/.test(value.intentId)))
    || (value.outcome !== undefined && value.outcome !== "UNKNOWN" && value.outcome !== "NOT_SENT")
    || (value.diagnostic !== undefined && typeof value.diagnostic !== "string")) {
    throw new Error("The brief recovery contains an invalid unconfirmed submission.");
  }
  const fields = value.fields;
  const accepted = (fields.length === 1 && fields[0] === "title")
    || (fields.length === 4 && fields.every((field, index) => field === BRIEF_FIELDS[index + 1]));
  if (!accepted) throw new Error("The brief recovery contains an invalid submission field set.");
  const raw: BriefPending["raw"] = {}, values: BriefPending["values"] = {};
  for (const field of fields as BriefField[]) {
    const text = value.raw[field], parsed = value.values[field];
    const list = field === "constraints" || field === "exclusions";
    if (typeof text !== "string" || (list
      ? !Array.isArray(parsed) || parsed.length > 128 || !parsed.every(item => typeof item === "string")
      : typeof parsed !== "string")
      || !same(proposedBriefValue(field, text), parsed as FieldValue)) {
      throw new Error("The brief recovery contains an invalid submitted value.");
    }
    raw[field] = text;
    values[field] = copyValue(parsed as FieldValue);
  }
  return { scope, revision: value.revision, runtimeId: value.runtimeId,
    fields: [...fields] as BriefField[], raw, values,
    ...(value.intentId === undefined ? {} : { intentId: value.intentId }),
    ...(value.outcome === undefined ? {} : { outcome: value.outcome }),
    ...(value.diagnostic === undefined ? {} : { diagnostic: value.diagnostic }) };
}
function cleanBriefDraft(value: unknown, scope: string): BriefDraft {
  if (!record(value) || value.schema !== "motionwright.brief-draft/1" || value.scope !== scope || !record(value.fields)) {
    throw new Error("The brief recovery copy has an unsupported shape or editing scope.");
  }
  const fields = {} as Record<BriefField, BriefFieldDraft>;
  for (const field of BRIEF_FIELDS) {
    const item = value.fields[field];
    const list = field === "constraints" || field === "exclusions";
    if (!record(item) || typeof item.raw !== "string" || !safeRevision(item.baseRevision)
      || (item.intent !== undefined && item.intent !== true)
      || (list ? !Array.isArray(item.base) || item.base.length > 128 || !item.base.every(text => typeof text === "string")
        : typeof item.base !== "string")) throw new Error("The brief recovery copy contains an invalid field.");
    fields[field] = { raw: item.raw, base: copyValue(item.base as FieldValue), baseRevision: item.baseRevision,
      ...(item.intent ? { intent: true as const } : {}) };
  }
  return { schema: "motionwright.brief-draft/1", scope, fields,
    ...(value.pending === undefined ? {} : { pending: cleanBriefPending(value.pending, scope) }),
  };
}
export function decodeBriefRecovery(raw: string, scope: string, sessionId: string): BriefRecovery {
  if (bytes(raw) > MAX_BRIEF_DRAFT_BYTES) throw new Error("The brief recovery copy exceeds its local size limit.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || value.schema !== "motionwright.brief-recovery/1" || value.scope !== scope
    || value.sessionId !== sessionId || !sessionId || sessionId.length > 128 || !safeRevision(value.updatedAt)) {
    throw new Error("The brief recovery copy has an unsupported version or editing scope.");
  }
  return { schema: "motionwright.brief-recovery/1", scope, sessionId, updatedAt: value.updatedAt,
    draft: value.draft === null ? null : cleanBriefDraft(value.draft, scope) };
}
export function encodeBriefRecovery(value: BriefRecovery): string {
  const raw = JSON.stringify(value);
  // The decoder also strips fields that could have arrived through untrusted storage.
  return JSON.stringify(decodeBriefRecovery(raw, value.scope, value.sessionId));
}
function recoveryIdentity(key: string): [string, string] | null {
  try {
    const identity: unknown = JSON.parse(key.slice(BRIEF_DRAFT_STORAGE_PREFIX.length));
    return Array.isArray(identity) && identity.length === 2 && typeof identity[0] === "string"
      && typeof identity[1] === "string" && identity[1].length > 0 && identity[1].length <= 128
      ? [identity[0], identity[1]] : null;
  } catch { return null; }
}
export function readBriefRecoveries(storage: DraftStorage, scope: string): {
  entries: BriefRecoveryEntry[]; unreadable: UnreadableBriefRecovery[]; limited: boolean;
} {
  const entries: BriefRecoveryEntry[] = [], unreadable: UnreadableBriefRecovery[] = [];
  let limited = false;
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (!key?.startsWith(BRIEF_DRAFT_STORAGE_PREFIX)) continue;
    const identity = recoveryIdentity(key);
    if (identity && identity[0] !== scope) continue;
    if (entries.length + unreadable.length >= MAX_BRIEF_RECOVERY_COPIES) {
      limited = true; break;
    }
    if (!identity) {
      // A malformed identity cannot be assigned to a project. Show it in any
      // Brief recovery panel so its budget usage is never an invisible dead end.
      unreadable.push({ key, sessionId: "unidentified-" + (i + 1), unscoped: true }); continue;
    }
    try {
      const raw = storage.getItem(key);
      if (raw === null) continue;
      entries.push({ key, value: decodeBriefRecovery(raw, scope, identity[1]) });
    } catch { unreadable.push({ key, sessionId: identity[1] }); }
  }
  entries.sort((a, b) => b.value.updatedAt - a.value.updatedAt || a.key.localeCompare(b.key));
  return { entries, unreadable, limited };
}
export function writeBriefRecovery(storage: DraftStorage, value: BriefRecovery): void {
  const key = briefDraftStorageKey(value.scope, value.sessionId);
  let raw = encodeBriefRecovery(value);
  const previous = storage.getItem(key);
  if (previous !== null) {
    const existing = decodeBriefRecovery(previous, value.scope, value.sessionId);
    if (existing.draft === null) {
      if (value.draft !== null) throw new Error("A clean brief checkpoint is immutable. Start a new recovery copy for new input.");
      return;
    }
  }
  // Clean records never become editable records again. This makes it safe to
  // reclaim redundant checkpoints without racing another tab's new input.
  type Stored = { key: string; raw: string; scope: string | null; value: BriefRecovery | null };
  const stored: Stored[] = [];
  for (let i = 0; i < storage.length; i++) {
    const existingKey = storage.key(i);
    if (!existingKey?.startsWith(BRIEF_DRAFT_STORAGE_PREFIX)) continue;
    const existingRaw = storage.getItem(existingKey);
    if (existingRaw === null) continue;
    const identity = recoveryIdentity(existingKey);
    let decoded: BriefRecovery | null = null;
    if (identity) {
      try { decoded = decodeBriefRecovery(existingRaw, identity[0], identity[1]); } catch { /* Keep unreadable bytes. */ }
    }
    stored.push({ key: existingKey, raw: existingRaw, scope: identity?.[0] ?? null, value: decoded });
  }
  // Order successful writes in the scope even when two edits share a clock tick
  // or the wall clock moves backwards. This is recovery ordering, not a receipt.
  const latestTime = stored.reduce((latest, item) => item.value?.scope === value.scope
    ? Math.max(latest, item.value.updatedAt) : latest, -1);
  value = { ...value, updatedAt: Math.max(value.updatedAt, latestTime + 1) };
  raw = encodeBriefRecovery(value);
  // Never remove another author's durable checkpoint while attempting a
  // replacement. The browser can reject writes (including silently) and a
  // full namespace has no transactional spare slot. Refuse such admission
  // without side effects; deliberate cleanup is a separate user action.
  function removeClean(item: Stored) {
    if (storage.getItem(item.key) === item.raw) storage.removeItem(item.key);
  }
  function budget() {
    let total = bytes(key) + bytes(raw), count = 1;
    for (let i = 0; i < storage.length; i++) {
      const existing = storage.key(i);
      if (!existing?.startsWith(BRIEF_DRAFT_STORAGE_PREFIX) || existing === key) continue;
      total += bytes(existing) + bytes(storage.getItem(existing) ?? ""); count++;
    }
    return total <= MAX_BRIEF_RECOVERY_BYTES && count <= MAX_BRIEF_RECOVERY_COPIES;
  }
  if (!budget()) {
    throw new Error("The brief recovery budget is full (64 copies / 4 MiB across projects). Copies with input were kept; remove only copies you no longer need, including in other projects if needed.");
  }
  // A peer window may have changed our own key since enumeration. A stale
  // writer is never allowed to overwrite its distinct, newer author input.
  if (storage.getItem(key) !== previous) {
    throw new Error("The brief recovery copy changed before save. Review the current local copy.");
  }
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) {
    throw new Error("Brief recovery write was not confirmed. Earlier bytes were kept.");
  }
  // Only after an independently verified NEW clean marker may superseded
  // clean markers from this exact editing scope be discarded. Dirty, foreign
  // and unreadable copies are never compacted as a side effect of a failure.
  if (value.draft === null) for (const item of stored) {
    if (item.key !== key && item.scope === value.scope && item.value?.draft === null) removeClean(item);
  }

}
