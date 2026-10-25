import { assertEditorialReceipt, editorialFieldBase, narrativeEditingScope, narrativeEditingSource, narrativeEditingState,
  sameEditorialValue } from "./narrativeEditing";
import type { NarrativeEditingChange, NarrativeEditSource } from "./narrativeEditing";
import type { Project, ProjectEvent } from "./types";

export const EDITORIAL_COPY_PREFIX = "motionwright.editorial-copy.v1:";
export const EDITORIAL_POINTER_PREFIX = "motionwright.editorial-writer.v1:";
const MAX_BYTES = 8 * 1024 * 1024;
const MAX_FIELDS = 4096;
export type EditorialStore = Pick<Storage, "getItem" | "setItem" | "key" | "length">;
export type EditorialPointers = Pick<Storage, "getItem" | "setItem">;
export interface EditorialField { raw: string; base: string; sequence: number }
export interface EditorialPending {
  id: string; source: NarrativeEditSource; changeJson: string; sentFields: Record<string, EditorialField>;
  beatIds: string[]; scriptJson: string; outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface EditorialDraft {
  schema: "motionwright.editorial-draft/1"; scope: string; sequence: number;
  fields: Record<string, EditorialField>; pending: EditorialPending | null;
}
export interface EditorialCopy { schema: "motionwright.editorial-copy/1"; writer: string; draft: EditorialDraft }
export interface EditorialCustody {
  writer: string; expectedRaw: string | null; expectedPointer: string | null;
  attempt: { raw: string; previousRaw: string | null; previousPointer: string | null } | null;
}
/** A failed read is distinct from an observed empty pointer or missing copy.
 * Pin the first bytes before decoding, including an unreadable author record. */
export interface EditorialRecoveryObservation {
  pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null;
}
export interface EditorialSession {
  draft: EditorialDraft; custody: EditorialCustody;
  recoveryObservation: EditorialRecoveryObservation | null;
  failedSource: EditorialRecoveryObservation | null;
  epoch: number;
}
export interface EditorialOutcomeReview {
  scope: string; pendingJson: string; epoch: number; observed: Project; events: ProjectEvent[]; matchingRevisions: number[];
}
export interface EditorialOutcomeTransport {
  readProject(id: string): Promise<Project>;
  readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}
export const editorialCopyKey = (scope: string, writer: string) => EDITORIAL_COPY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const editorialPointerKey = (scope: string) => EDITORIAL_POINTER_PREFIX + encodeURIComponent(scope);
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const keys = (value: Record<string, unknown>, names: string[]) => Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
const byteLength = (raw: string) => new TextEncoder().encode(raw).length;
function checkScope(scope: string): void {
  const values: unknown = JSON.parse(scope);
  if (!Array.isArray(values) || values.length !== 3 || !values.every(uuid)) throw new Error("Invalid editorial recovery owner.");
}
function fieldMap(value: unknown, sequence: number): Record<string, EditorialField> {
  if (!record(value) || Object.keys(value).length > MAX_FIELDS) throw new Error("Unreadable editorial field collection.");
  const result: Record<string, EditorialField> = {};
  for (const [key, field] of Object.entries(value)) {
    if (key.length > 180 || key === "__proto__" || key === "constructor" || key === "prototype"
      || !record(field) || !keys(field, ["raw", "base", "sequence"]) || typeof field.raw !== "string"
      || typeof field.base !== "string" || !integer(field.sequence) || field.sequence > sequence)
      throw new Error("Unreadable editorial input buffer: " + key);
    result[key] = { raw: field.raw, base: field.base, sequence: field.sequence };
  }
  return result;
}
function source(value: unknown, scope: string): NarrativeEditSource {
  if (!record(value) || !keys(value, ["project_id", "generation", "branch_id", "base_revision"])
    || !uuid(value.project_id) || !uuid(value.generation) || !uuid(value.branch_id) || !integer(value.base_revision)
    || JSON.stringify([value.project_id, value.generation, value.branch_id]) !== scope) throw new Error("Unreadable editorial pending authority.");
  return { project_id: value.project_id, generation: value.generation, branch_id: value.branch_id, base_revision: value.base_revision };
}
export function decodeEditorialDraft(value: unknown, scope: string, recovered = true): EditorialDraft {
  checkScope(scope);
  if (!record(value) || !keys(value, ["schema", "scope", "sequence", "fields", "pending"])
    || value.schema !== "motionwright.editorial-draft/1" || value.scope !== scope || !integer(value.sequence))
    throw new Error("Editorial recovery has a foreign owner or unsupported shape.");
  const fields = fieldMap(value.fields, value.sequence);
  let pending: EditorialPending | null = null;
  if (value.pending !== null) {
    const entry = value.pending;
    if (!record(entry) || !keys(entry, ["id", "source", "changeJson", "sentFields", "beatIds", "scriptJson", "outcome", "diagnostic"])
      || !uuid(entry.id) || typeof entry.changeJson !== "string" || typeof entry.scriptJson !== "string"
      || !Array.isArray(entry.beatIds) || !entry.beatIds.every(uuid) || new Set(entry.beatIds).size !== entry.beatIds.length
      || !["UNKNOWN", "NOT_SENT"].includes(String(entry.outcome)) || !(entry.diagnostic === null || typeof entry.diagnostic === "string"))
      throw new Error("The editorial pending intent is unreadable. It cannot be treated as unsent.");
    const parsed: unknown = JSON.parse(entry.changeJson); JSON.parse(entry.scriptJson);
    if (!record(parsed) || !["edit_narrative", "add_narrative_beat"].includes(String(parsed.type)))
      throw new Error("Unsupported retained editorial command. No stored command will be replayed.");
    const expected = source(entry.source, scope);
    if (parsed.type === "edit_narrative" && !sameEditorialValue(source(parsed.expected, scope), expected))
      throw new Error("Retained editorial comparison source differs from its operation.");
    pending = { id: entry.id, source: expected, changeJson: entry.changeJson, sentFields: fieldMap(entry.sentFields, value.sequence),
      beatIds: [...entry.beatIds], scriptJson: entry.scriptJson, outcome: recovered ? "UNKNOWN" : entry.outcome as "UNKNOWN" | "NOT_SENT",
      diagnostic: entry.diagnostic };
  }
  return { schema: "motionwright.editorial-draft/1", scope, sequence: value.sequence, fields, pending };
}
export function decodeEditorialCopy(raw: string, scope: string, writer: string): EditorialCopy {
  if (byteLength(raw) > MAX_BYTES || !uuid(writer)) throw new Error("Editorial copy exceeds its import budget or has an invalid writer.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !keys(value, ["schema", "writer", "draft"]) || value.schema !== "motionwright.editorial-copy/1" || value.writer !== writer)
    throw new Error("Editorial copy has a foreign writer or unsupported shape.");
  return { schema: "motionwright.editorial-copy/1", writer, draft: decodeEditorialDraft(value.draft, scope) };
}
export function newEditorialDraft(project: Project): EditorialDraft {
  return { schema: "motionwright.editorial-draft/1", scope: narrativeEditingScope(project), sequence: 0, fields: {}, pending: null };
}
const custody = (pointer: string | null): EditorialCustody => ({ writer: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer, attempt: null });
function assertEditorialRecoverySource(scope: string, observation: EditorialRecoveryObservation,
  storage: EditorialStore, pointers: EditorialPointers, own?: { writer: string; raw: string }): void {
  if (!observation.pointerObserved) throw new Error("Read the original editorial recovery pointer before making a new copy.");
  if (observation.pointer !== null) {
    if (!observation.rawObserved || observation.raw === null) throw new Error("The original editorial recovery copy is still missing.");
    if (storage.getItem(editorialCopyKey(scope, observation.pointer)) !== observation.raw)
      throw new Error("The original editorial recovery bytes changed. The first observation remains retained.");
    decodeEditorialCopy(observation.raw, scope, observation.pointer);
  }
  const pointer = pointers.getItem(editorialPointerKey(scope));
  if (pointer !== observation.pointer && !(own && pointer === own.writer && storage.getItem(editorialCopyKey(scope, own.writer)) === own.raw))
    throw new Error("The original editorial recovery pointer changed. No different copy was adopted.");
  if (observation.pointer !== null && storage.getItem(editorialCopyKey(scope, observation.pointer)) !== observation.raw)
    throw new Error("The original editorial recovery bytes changed during confirmation. The first observation remains retained.");
}
function readEditorialRecoverySource(scope: string, observation: EditorialRecoveryObservation,
  storage: EditorialStore, pointers: EditorialPointers): EditorialDraft | null {
  const pointer = pointers.getItem(editorialPointerKey(scope));
  if (observation.pointerObserved && pointer !== observation.pointer)
    throw new Error("The original editorial recovery pointer changed. No different copy was adopted.");
  if (!observation.pointerObserved) { observation.pointer = pointer; observation.pointerObserved = true; }
  if (pointer === null) { assertEditorialRecoverySource(scope, observation, storage, pointers); return null; }
  const raw = storage.getItem(editorialCopyKey(scope, pointer));
  if (observation.rawObserved && raw !== observation.raw)
    throw new Error("The original editorial recovery bytes changed. The first observation remains retained.");
  if (!observation.rawObserved) { observation.raw = raw; observation.rawObserved = true; }
  if (raw === null) throw new Error("The original editorial recovery copy is still missing.");
  const draft = decodeEditorialCopy(raw, scope, pointer).draft;
  assertEditorialRecoverySource(scope, observation, storage, pointers);
  return draft;
}
export function openEditorialSession(project: Project, storage: EditorialStore, pointers: EditorialPointers): EditorialSession {
  const scope = narrativeEditingScope(project);
  const observation: EditorialRecoveryObservation = { pointerObserved: false, pointer: null, rawObserved: false, raw: null };
  const result: EditorialSession = { draft: newEditorialDraft(project), custody: custody(null),
    recoveryObservation: observation, failedSource: null, epoch: 0 };
  try {
    const recovered = readEditorialRecoverySource(scope, observation, storage, pointers);
    if (recovered) result.draft = recovered;
  } catch { result.failedSource = observation; }
  // New runtime, new writer, even when a browser duplicated sessionStorage.
  result.custody = custody(observation.pointer);
  return result;
}
export function editorialHasInput(draft: EditorialDraft): boolean { return Object.keys(draft.fields).length > 0; }
export function editorialRaw(draft: EditorialDraft, project: Project, key: string): string {
  return draft.fields[key]?.raw ?? editorialFieldBase(project, key);
}
export function editEditorialField(session: EditorialSession, project: Project, key: string, raw: string): void {
  if (session.draft.scope !== narrativeEditingScope(project)) throw new Error("Editorial owner changed.");
  const sequence = session.draft.sequence + 1;
  if (!Number.isSafeInteger(sequence)) throw new Error("Editorial input sequence exhausted.");
  const old = session.draft.fields[key];
  session.draft = { ...session.draft, sequence, fields: { ...session.draft.fields,
    [key]: { raw, base: old?.base ?? editorialFieldBase(project, key), sequence } } };
  session.epoch++;
}
export function editorialFieldConflict(draft: EditorialDraft, project: Project, key: string): boolean {
  if (narrativeEditingScope(project) !== draft.scope) return true;
  const field = draft.fields[key];
  if (!field) return false;
  try { return field.base !== editorialFieldBase(project, key); } catch { return true; }
}
export function forkEditorialWriter(session: EditorialSession): void {
  if (session.custody.attempt) throw new Error("Verify the incomplete editorial recovery write before changing writer.");
  session.custody = custody(session.custody.expectedPointer);
}
function assertCurrentCustody(session: EditorialSession, storage: EditorialStore, pointers: EditorialPointers): void {
  const c = session.custody, scope = session.draft.scope;
  if (pointers.getItem(editorialPointerKey(scope)) !== c.expectedPointer) throw new Error("Another window changed this tab's editorial writer pointer.");
  if (storage.getItem(editorialCopyKey(scope, c.writer)) !== c.expectedRaw) throw new Error("The editable editorial copy changed outside this writer.");
}
export function persistEditorialSession(session: EditorialSession, storage: EditorialStore, pointers: EditorialPointers): void {
  if (session.failedSource) throw new Error("Read the exact failed source copy before enabling editorial save.");
  if (session.custody.attempt) throw new Error("An editorial recovery write is unconfirmed. Retry its exact readback first.");
  const original = session.recoveryObservation, c = session.custody, scope = session.draft.scope;
  if (original) assertEditorialRecoverySource(scope, original, storage, pointers,
    c.expectedRaw === null ? undefined : { writer: c.writer, raw: c.expectedRaw });
  assertCurrentCustody(session, storage, pointers);
  const clean = decodeEditorialDraft(session.draft, scope, false);
  const raw = JSON.stringify({ schema: "motionwright.editorial-copy/1", writer: c.writer, draft: clean } satisfies EditorialCopy);
  if (byteLength(raw) > MAX_BYTES) throw new Error("The editorial recovery exceeds 8 MiB. Export the author input before reducing retained records.");
  const key = editorialCopyKey(scope, c.writer), pointerKey = editorialPointerKey(scope);
  c.attempt = { raw, previousRaw: c.expectedRaw, previousPointer: c.expectedPointer };
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The editorial copy did not survive write/readback.");
  if (pointers.getItem(pointerKey) !== c.expectedPointer) throw new Error("Editorial pointer changed while saving its copy.");
  pointers.setItem(pointerKey, c.writer);
  if (pointers.getItem(pointerKey) !== c.writer) throw new Error("The editorial writer pointer did not survive readback.");
  if (storage.getItem(key) !== raw) throw new Error("The editorial data changed during the final pointer readback.");
  if (original) assertEditorialRecoverySource(scope, original, storage, pointers, { writer: c.writer, raw });
  c.expectedRaw = raw; c.expectedPointer = c.writer; c.attempt = null;
  session.recoveryObservation = null;
}
/** Retry only the observed failed source and this writer's exact attempted bytes.
 * Reading an earlier UNKNOWN cannot accidentally become permission to save. */
export function retryEditorialCustody(session: EditorialSession, storage: EditorialStore, pointers: EditorialPointers): void {
  const scope = session.draft.scope;
  if (session.failedSource) {
    const failed = session.failedSource;
    const recovered = readEditorialRecoverySource(scope, failed, storage, pointers);
    let adopted = session.draft;
    if (recovered) {
      if (session.draft.pending && recovered.pending && session.draft.pending.id !== recovered.pending.id)
        throw new Error("Two distinct UNKNOWN editorial operations require separate review; neither was discarded.");
      const fields = { ...recovered.fields }, later = Object.entries(session.draft.fields);
      let sequence = Math.max(recovered.sequence, session.draft.sequence);
      for (const [key, value] of later) {
        if (!Number.isSafeInteger(++sequence)) throw new Error("Editorial input sequence exhausted. Both inputs remain retained.");
        fields[key] = { ...value, sequence };
      }
      adopted = { ...recovered, sequence, fields, pending: session.draft.pending ?? recovered.pending };
    }
    // Do not mutate the current buffer until the entire original observation
    // has survived a second read. A later field remains intent even if blank.
    assertEditorialRecoverySource(scope, failed, storage, pointers);
    session.draft = adopted; session.failedSource = null; session.custody = custody(failed.pointer); session.epoch++;
  }
  const c = session.custody;
  if (c.attempt) {
    const attempt = c.attempt, raw = storage.getItem(editorialCopyKey(scope, c.writer)), pointer = pointers.getItem(editorialPointerKey(scope));
    if ((raw !== attempt.raw && raw !== attempt.previousRaw) || (pointer !== c.writer && pointer !== attempt.previousPointer))
      throw new Error("The incomplete editorial write no longer matches its exact bytes/pointer.");
    if (storage.getItem(editorialCopyKey(scope, c.writer)) !== raw || pointers.getItem(editorialPointerKey(scope)) !== pointer)
      throw new Error("Editorial recovery changed while its incomplete write was inspected.");
    c.expectedRaw = raw; c.expectedPointer = pointer; c.attempt = null;
  }
  persistEditorialSession(session, storage, pointers);
}

function sameObservedProject(left: Project, right: Project): boolean {
  return narrativeEditingScope(left) === narrativeEditingScope(right) && left.revision === right.revision
    && sameEditorialValue(left, right);
}
export function editorialReviewCurrent(session: EditorialSession, project: Project, review: EditorialOutcomeReview | null): boolean {
  return !!review && session.draft.pending !== null && review.scope === session.draft.scope
    && review.epoch === session.epoch && review.pendingJson === JSON.stringify(session.draft.pending)
    && sameObservedProject(project, review.observed);
}
export async function inspectEditorialOutcome(session: EditorialSession, project: Project, transport: EditorialOutcomeTransport): Promise<EditorialOutcomeReview> {
  const pending = session.draft.pending;
  if (!pending || session.draft.scope !== narrativeEditingScope(project)) throw new Error("No matching unconfirmed editorial operation exists.");
  const pendingJson = JSON.stringify(pending), epoch = session.epoch;
  const observed = structuredClone(await transport.readProject(project.id));
  if (!sameObservedProject(observed, project)) throw new Error("Reload the current saved project before reviewing this editorial outcome.");
  if (!Number.isSafeInteger(observed.revision) || observed.revision < pending.source.base_revision
    || observed.revision - pending.source.base_revision > 2048) throw new Error("The complete editorial history range is unavailable.");
  const events: ProjectEvent[] = []; let cursor = pending.source.base_revision;
  while (cursor < observed.revision) {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || !page.length || page.length > 256) throw new Error("The editorial history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > observed.revision || !event.change
        || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The editorial history is not a contiguous saved revision sequence.");
      events.push(structuredClone(event)); cursor = event.revision;
    }
  }
  const after = await transport.readProject(project.id);
  if (!sameObservedProject(after, observed) || session.epoch !== epoch || JSON.stringify(session.draft.pending) !== pendingJson)
    throw new Error("The editorial buffer or saved project changed during outcome review.");
  return { scope: session.draft.scope, pendingJson, epoch, observed, events,
    matchingRevisions: events.filter(event => sameEditorialValue(event.change, JSON.parse(pending.changeJson))).map(event => event.revision) };
}

export function prepareEditorialSubmission(session: EditorialSession, project: Project, change: NarrativeEditingChange, usedFields: string[],
  review: EditorialOutcomeReview | null = null, id = crypto.randomUUID()): { draft: EditorialDraft; change: NarrativeEditingChange; id: string } {
  if (session.failedSource || session.custody.attempt) throw new Error("Confirm the editorial recovery copy and pointer before saving.");
  if (session.draft.scope !== narrativeEditingScope(project)) throw new Error("Editorial project/generation/branch changed.");
  if (session.draft.pending && !editorialReviewCurrent(session, project, review))
    throw new Error("UNKNOWN is retained. Read the current project/history before creating a separate explicit operation.");
  for (const key of usedFields) if (editorialFieldConflict(session.draft, project, key)) throw new Error("Saved editorial input changed: " + key + ". Compare and choose its base explicitly.");
  if (change.type === "edit_narrative" && !sameEditorialValue(change.expected, narrativeEditingSource(project))) throw new Error("Editorial source changed before submission.");
  const sentFields: Record<string, EditorialField> = {};
  for (const key of usedFields) if (session.draft.fields[key]) sentFields[key] = { ...session.draft.fields[key] };
  const pending: EditorialPending = { id, source: narrativeEditingSource(project), changeJson: JSON.stringify(change), sentFields,
    beatIds: project.narrative.beats.map(beat => beat.id), scriptJson: JSON.stringify(narrativeEditingState(project).script), outcome: "UNKNOWN", diagnostic: null };
  return { id, change, draft: { ...session.draft, pending } };
}
export function acknowledgeEditorialSubmission(draft: EditorialDraft, id: string, originalChange: NarrativeEditingChange, receipt: Project): EditorialDraft {
  const pending = draft.pending;
  if (!pending || pending.id !== id || pending.changeJson !== JSON.stringify(originalChange)) throw new Error("An unrelated editorial response cannot consume this intent.");
  assertEditorialReceipt(originalChange, pending, receipt);
  const fields = { ...draft.fields };
  for (const [key, sent] of Object.entries(pending.sentFields)) {
    const later = fields[key];
    if (!later) continue;
    if (later.sequence === sent.sequence) delete fields[key];
    else fields[key] = { ...later, base: editorialFieldBase(receipt, key) };
  }
  return { ...draft, fields, pending: null };
}
export function retainEditorialFailure(draft: EditorialDraft, id: string, diagnostic: string, notSent = false): EditorialDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } } : draft;
}
export function compareEditorialField(session: EditorialSession, project: Project, key: string, useSaved: boolean): void {
  if (session.draft.pending) throw new Error("Inspect the UNKNOWN operation first; comparing input cannot discard it.");
  const field = session.draft.fields[key]; if (!field) return;
  const fields = { ...session.draft.fields };
  if (useSaved) delete fields[key]; else fields[key] = { ...field, base: editorialFieldBase(project, key) };
  session.draft = { ...session.draft, fields }; session.epoch++;
}
/** A review authorizes a separately constructed next intent, never consumption
 * of the UNKNOWN operation. Its armed source stays durable across this choice. */
export function rebaseReviewedEditorialFields(session: EditorialSession, project: Project, review: EditorialOutcomeReview,
  storage: EditorialStore, pointers: EditorialPointers, useSaved = false): EditorialOutcomeReview {
  if (!editorialReviewCurrent(session, project, review)) throw new Error("Read the current editorial project/history again before choosing a new base.");
  persistEditorialSession(session, storage, pointers);
  const fields: Record<string, EditorialField> = {};
  if (!useSaved) for (const [key, value] of Object.entries(session.draft.fields)) fields[key] = { ...value, base: editorialFieldBase(project, key) };
  forkEditorialWriter(session);
  session.draft = { ...session.draft, fields }; session.epoch++;
  persistEditorialSession(session, storage, pointers);
  return { ...review, epoch: session.epoch, pendingJson: JSON.stringify(session.draft.pending) };
}
export function originalEditorialRecoveryCopy(scope: string, observation: EditorialRecoveryObservation | null): { key: string; raw: string; copy: EditorialCopy | null } | null {
  if (!observation?.pointerObserved || observation.pointer === null || !observation.rawObserved || observation.raw === null) return null;
  let copy: EditorialCopy | null = null;
  try { copy = decodeEditorialCopy(observation.raw, scope, observation.pointer); } catch { /* Keep original unreadable bytes downloadable. */ }
  return { key: editorialCopyKey(scope, observation.pointer), raw: observation.raw, copy };
}
export function editorialCopies(storage: EditorialStore, scope: string,
  observation?: EditorialRecoveryObservation | null): { key: string; raw: string; copy: EditorialCopy | null }[] {
  const prefix = EDITORIAL_COPY_PREFIX + encodeURIComponent(scope) + ":", result: { key: string; raw: string; copy: EditorialCopy | null }[] = [];
  if (storage.length > 10000) throw new Error("Editorial copy enumeration is too large; export current input and inspect storage.");
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i); if (!key?.startsWith(prefix)) continue;
    if (result.length >= 128) throw new Error("More than 128 editorial recovery copies exist. No copy was silently deleted.");
    const raw = storage.getItem(key);
    // Pin a source encountered during listing before another index/read can
    // fail, including an observed absence. Retry cannot silently substitute
    // newer bytes under this writer after a listed copy was missing.
    if (observation?.pointerObserved && observation.pointer !== null && !observation.rawObserved && key === editorialCopyKey(scope, observation.pointer)) {
      observation.raw = raw; observation.rawObserved = true;
    }
    if (raw === null) continue;
    let copy: EditorialCopy | null = null;
    try { copy = decodeEditorialCopy(raw, scope, key.slice(prefix.length)); } catch { /* opaque copy remains visible */ }
    result.push({ key, raw, copy });
  }
  return result;
}
export function restoreEditorialCopy(session: EditorialSession, raw: string, writer: string, storage: EditorialStore, pointers: EditorialPointers): void {
  if (session.draft.pending) throw new Error("An UNKNOWN operation cannot be replaced by a different copy.");
  const restored = decodeEditorialCopy(raw, session.draft.scope, writer).draft;
  if (!editorialHasInput(restored) && !restored.pending) throw new Error("This is a clean checkpoint with no pending author input.");
  // Verify both the current buffer and selected source before any replacement.
  persistEditorialSession(session, storage, pointers);
  const scope = session.draft.scope, key = editorialCopyKey(scope, writer);
  if (storage.getItem(key) !== raw) throw new Error("The selected editorial copy changed.");
  forkEditorialWriter(session);
  // The prior buffer was verified under its original key. Keep the explicitly
  // selected candidate in memory if retention fails, especially its UNKNOWN;
  // rollback to a clean buffer would let Retry erase that known operation.
  session.draft = restored; session.epoch++;
  persistEditorialSession(session, storage, pointers);
}
export function exportEditorialDraft(draft: EditorialDraft): string {
  return JSON.stringify({ schema: "motionwright.editorial-author-file/1", draft: decodeEditorialDraft(draft, draft.scope, false) }, null, 2);
}
export function importEditorialDraft(raw: string, scope: string): EditorialDraft {
  if (byteLength(raw) > MAX_BYTES) throw new Error("The editorial author file exceeds 8 MiB.");
  const file: unknown = JSON.parse(raw);
  if (!record(file) || !keys(file, ["schema", "draft"]) || file.schema !== "motionwright.editorial-author-file/1") throw new Error("Unsupported editorial author file.");
  return decodeEditorialDraft(file.draft, scope);
}
export function adoptEditorialFile(session: EditorialSession, draft: EditorialDraft, storage: EditorialStore, pointers: EditorialPointers): void {
  if (session.draft.pending) throw new Error("An UNKNOWN operation cannot be replaced by an author file.");
  const decoded = decodeEditorialDraft(draft, session.draft.scope);
  persistEditorialSession(session, storage, pointers);
  forkEditorialWriter(session);
  session.draft = decoded; session.epoch++;
  persistEditorialSession(session, storage, pointers);
}
