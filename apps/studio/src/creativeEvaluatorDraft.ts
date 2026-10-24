import { EVALUATOR_SCHEMA, REPORT_SCHEMA, evaluatorDigest, observeCriterion, validateEvaluator } from "./creativeEvaluator";
import type { CreativeCriterion, CreativeEvaluatorBundle, EvaluatorChange } from "./creativeEvaluator";
import { emptyProductionDesign } from "./creativeProduction";
import type { Project, ProjectEvent } from "./types";

// Raw author input is recoverable comparison data, never a replayable command queue.
export const EVALUATOR_COPY_PREFIX = "motionwright.evaluator-copy.v1:";
export const EVALUATOR_POINTER_PREFIX = "motionwright.evaluator-writer.v1:";
const MAX_BYTES = 8 * 1024 * 1024;
export const EVALUATOR_DEFAULTS = {
  label: "Original scene evaluation",
  purpose: "Review observable editable structure and request independent human feedback",
  author: "",
  steps: "Inspect original authored scene elements.\nConfirm technical constraints without claiming aesthetic approval.",
  minimum: "2", requiredText: "", safeMargin: "50",
  humanQuestion: "Does this scene communicate its intended idea clearly to a human viewer?",
  importText: "", confirmImport: "false", selected: "",
} as const;
export type EvaluatorFieldName = keyof typeof EVALUATOR_DEFAULTS;
export type EvaluatorOperation = { kind: "author" } | { kind: "import" } | { kind: "evaluate"; sceneId: string };
export type EvaluatorStore = Pick<Storage, "getItem" | "setItem" | "key" | "length">;
export type EvaluatorPointers = Pick<Storage, "getItem" | "setItem">;
export interface EvaluatorField { raw: string; base: string; sequence: number }
export interface EvaluatorSource { project_id: string; generation: string; branch_id: string; base_revision: number }
export interface EvaluatorPending {
  id: string; source: EvaluatorSource; sourceProjectJson: string; operation: EvaluatorOperation;
  changeJson: string; sentFields: Record<string, EvaluatorField>; outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface EvaluatorDraft {
  schema: "motionwright.evaluator-draft/1"; scope: string; sequence: number;
  fields: Record<string, EvaluatorField>; pending: EvaluatorPending | null;
}
export interface EvaluatorCopy { schema: "motionwright.evaluator-copy/1"; writer: string; draft: EvaluatorDraft }
export interface EvaluatorCustody {
  writer: string; expectedRaw: string | null; expectedPointer: string | null;
  attempt: { raw: string; previousRaw: string | null; previousPointer: string | null } | null;
}
export interface EvaluatorRecoveryObservation {
  pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null;
}
export interface EvaluatorSession {
  draft: EvaluatorDraft; custody: EvaluatorCustody;
  recoveryObservation: EvaluatorRecoveryObservation | null; failedSource: EvaluatorRecoveryObservation | null; epoch: number;
  // Runtime-only permission created by an explicit prepare. A recovered copy has none.
  liveAttempt: { id: string; changeJson: string; dispatched: boolean; prior: { key: string; raw: string } | null } | null;
}
export interface EvaluatorOutcomeReview {
  scope: string; pendingJson: string; epoch: number; observed: Project; events: ProjectEvent[]; matchingRevisions: number[];
}
export interface EvaluatorOutcomeTransport {
  readProject(id: string): Promise<Project>;
  readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}
export const evaluatorScope = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const evaluatorCopyKey = (scope: string, writer: string) => EVALUATOR_COPY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const evaluatorPointerKey = (scope: string) => EVALUATOR_POINTER_PREFIX + encodeURIComponent(scope);
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const keys = (value: Record<string, unknown>, names: string[]) => Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
const byteLength = (raw: string) => new TextEncoder().encode(raw).length;
const fieldName = (key: string): key is EvaluatorFieldName => Object.hasOwn(EVALUATOR_DEFAULTS, key);
const authorFields: EvaluatorFieldName[] = ["label", "purpose", "author", "steps", "minimum", "requiredText", "safeMargin", "humanQuestion"];
const usedFields = (operation: EvaluatorOperation): EvaluatorFieldName[] => operation.kind === "author" ? [...authorFields, "selected"]
  : operation.kind === "import" ? ["importText", "confirmImport", "selected"] : ["selected"];
export function sameEvaluatorValue(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) || Array.isArray(right)) return Array.isArray(left) && Array.isArray(right)
    && left.length === right.length && left.every((value, index) => sameEvaluatorValue(value, right[index]));
  const a = Object.keys(left).sort(), b = Object.keys(right).sort();
  return a.length === b.length && a.every((key, index) => key === b[index]
    && sameEvaluatorValue(Reflect.get(left, key), Reflect.get(right, key)));
}
function checkScope(scope: string): void {
  const values: unknown = JSON.parse(scope);
  if (!Array.isArray(values) || values.length !== 3 || !values.every(uuid)) throw new Error("Invalid evaluator recovery owner.");
}
function fieldMap(value: unknown, sequence: number): Record<string, EvaluatorField> {
  if (!record(value) || Object.keys(value).length > Object.keys(EVALUATOR_DEFAULTS).length) throw new Error("Unreadable evaluator field collection.");
  const result: Record<string, EvaluatorField> = {};
  for (const [key, field] of Object.entries(value)) {
    if (!fieldName(key) || !record(field) || !keys(field, ["raw", "base", "sequence"])
      || typeof field.raw !== "string" || typeof field.base !== "string" || !integer(field.sequence) || field.sequence > sequence)
      throw new Error("Unreadable evaluator input buffer: " + key);
    result[key] = { raw: field.raw, base: field.base, sequence: field.sequence };
  }
  return result;
}
function source(value: unknown, scope: string): EvaluatorSource {
  if (!record(value) || !keys(value, ["project_id", "generation", "branch_id", "base_revision"])
    || !uuid(value.project_id) || !uuid(value.generation) || !uuid(value.branch_id) || !integer(value.base_revision)
    || JSON.stringify([value.project_id, value.generation, value.branch_id]) !== scope) throw new Error("Unreadable evaluator pending authority.");
  return { project_id: value.project_id, generation: value.generation, branch_id: value.branch_id, base_revision: value.base_revision };
}
const evaluatorSource = (project: Project): EvaluatorSource => ({ project_id: project.id, generation: project.generation,
  branch_id: project.active_branch, base_revision: project.revision });
function fullProject(value: unknown): Project {
  const arrays = ["branches", "branch_workspaces", "reviews", "merges", "scenes", "markers", "assets", "locks", "deliverables",
    "proposal_sets", "model_invocations", "extensions", "handoffs"];
  if (!record(value) || !uuid(value.id) || !uuid(value.generation) || !uuid(value.active_branch) || !integer(value.revision)
    || !integer(value.schema_version) || typeof value.title !== "string" || typeof value.updated_at !== "string"
    || !["current", "stale", "unknown"].includes(String(value.state)) || arrays.some(key => !Array.isArray(value[key]))
    || ["brief", "narrative", "audio", "visual_language"].some(key => !record(value[key]))
    || (Object.hasOwn(value, "production_design") && !record(value.production_design)))
    throw new Error("The complete saved evaluator Project is unavailable.");
  return value as unknown as Project;
}
function operation(value: unknown): EvaluatorOperation {
  if (!record(value)) throw new Error("Unreadable evaluator operation.");
  if ((value.kind === "author" || value.kind === "import") && keys(value, ["kind"])) return { kind: value.kind };
  if (value.kind === "evaluate" && keys(value, ["kind", "sceneId"]) && uuid(value.sceneId)) return { kind: "evaluate", sceneId: value.sceneId };
  throw new Error("Unsupported evaluator operation. Stored commands are never replayed.");
}
function evaluatorChange(value: unknown): EvaluatorChange {
  if (!record(value)) throw new Error("Unreadable evaluator command.");
  if (value.type === "register_creative_evaluator" && keys(value, ["type", "bundle"])) {
    validateEvaluator(value.bundle as CreativeEvaluatorBundle);
    return value as unknown as EvaluatorChange;
  }
  if (value.type === "evaluate_creative_scene" && keys(value, ["type", "evaluator_id", "evaluator_version", "scene_id"])
    && uuid(value.evaluator_id) && uuid(value.scene_id) && integer(value.evaluator_version) && value.evaluator_version > 0)
    return value as unknown as EvaluatorChange;
  throw new Error("Unsupported retained evaluator command. No stored command will be replayed.");
}
export function evaluatorFieldBase(project: Project, key: string): string {
  if (!fieldName(key)) throw new Error("Unsupported evaluator input field: " + key);
  if (key !== "selected") return EVALUATOR_DEFAULTS[key];
  const bundles = project.production_design?.evaluator_bundles ?? [], last = bundles[bundles.length - 1];
  return last ? last.id + ":" + last.version : "";
}
function numeric(raw: string, name: string): number {
  // Preserve raw text in custody; only complete finite integer input reaches the existing grammar.
  const text = raw.trim();
  if (!text || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)
    || !Number.isSafeInteger(Number(text))) throw new Error("Enter a complete integer for evaluator " + name + ".");
  return Number(text);
}
function authorBundle(raw: (key: EvaluatorFieldName) => string, bundleId: string, criterionId: () => string): CreativeEvaluatorBundle {
  const criteria: CreativeCriterion[] = [{ kind: "minimum_nodes", id: criterionId(), minimum: numeric(raw("minimum"), "minimum nodes") }];
  if (raw("requiredText").trim()) criteria.push({ kind: "text_contains", id: criterionId(), phrase: raw("requiredText").trim() });
  criteria.push({ kind: "project_pixel_safe_area", id: criterionId(), margin_permille: numeric(raw("safeMargin"), "safe-area margin") });
  criteria.push({ kind: "human_question", id: criterionId(), question: raw("humanQuestion").trim() });
  const bundle: CreativeEvaluatorBundle = { schema: EVALUATOR_SCHEMA, id: bundleId, version: 1,
    label: raw("label").trim(), purpose: raw("purpose").trim(), author_attestation: raw("author").trim(),
    playbook: raw("steps").split("\n").map(line => line.trim()).filter(Boolean), criteria };
  validateEvaluator(bundle); return bundle;
}
function assertSentFields(change: EvaluatorChange, op: EvaluatorOperation, fields: Record<string, EvaluatorField>): void {
  if (!keys(fields, usedFields(op))) throw new Error("The retained evaluator operation has incomplete sent input.");
  if (op.kind === "evaluate") {
    if (change.type !== "evaluate_creative_scene" || change.scene_id !== op.sceneId
      || fields.selected.raw !== change.evaluator_id + ":" + change.evaluator_version) throw new Error("Evaluator selection differs from its retained operation.");
  } else {
    if (change.type !== "register_creative_evaluator") throw new Error("Evaluator registration differs from its retained operation.");
    if (op.kind === "import") {
      if (fields.confirmImport.raw !== "true" || byteLength(fields.importText.raw) > 16 * 1024
        || !sameEvaluatorValue(JSON.parse(fields.importText.raw), change.bundle)) throw new Error("Retained evaluator import differs from the confirmed source JSON.");
    } else {
      let index = 0;
      const expected = authorBundle(key => fields[key].raw, change.bundle.id, () => change.bundle.criteria[index++]?.id ?? "");
      if (!sameEvaluatorValue(expected, change.bundle)) throw new Error("Retained evaluator author input differs from its operation.");
    }
  }
}
export function decodeEvaluatorDraft(value: unknown, scope: string, recovered = true): EvaluatorDraft {
  checkScope(scope);
  if (!record(value) || !keys(value, ["schema", "scope", "sequence", "fields", "pending"])
    || value.schema !== "motionwright.evaluator-draft/1" || value.scope !== scope || !integer(value.sequence))
    throw new Error("Evaluator recovery has a foreign owner or unsupported shape.");
  const fields = fieldMap(value.fields, value.sequence);
  let pending: EvaluatorPending | null = null;
  if (value.pending !== null) {
    const entry = value.pending;
    if (!record(entry) || !keys(entry, ["id", "source", "sourceProjectJson", "operation", "changeJson", "sentFields", "outcome", "diagnostic"])
      || !uuid(entry.id) || typeof entry.changeJson !== "string" || typeof entry.sourceProjectJson !== "string"
      || !["UNKNOWN", "NOT_SENT"].includes(String(entry.outcome)) || !(entry.diagnostic === null || typeof entry.diagnostic === "string"))
      throw new Error("The evaluator pending intent is unreadable. It cannot be treated as unsent.");
    const expected = source(entry.source, scope), project = fullProject(JSON.parse(entry.sourceProjectJson));
    if (!sameEvaluatorValue(evaluatorSource(project), expected)) throw new Error("The retained evaluator Project differs from its source authority.");
    const op = operation(entry.operation), change = evaluatorChange(JSON.parse(entry.changeJson)), sentFields = fieldMap(entry.sentFields, value.sequence);
    assertSentFields(change, op, sentFields);
    pending = { id: entry.id, source: expected, sourceProjectJson: entry.sourceProjectJson, operation: op,
      changeJson: entry.changeJson, sentFields, outcome: recovered ? "UNKNOWN" : entry.outcome as "UNKNOWN" | "NOT_SENT", diagnostic: entry.diagnostic };
  }
  return { schema: "motionwright.evaluator-draft/1", scope, sequence: value.sequence, fields, pending };
}
export function decodeEvaluatorCopy(raw: string, scope: string, writer: string): EvaluatorCopy {
  if (byteLength(raw) > MAX_BYTES || !uuid(writer)) throw new Error("Evaluator copy exceeds its import budget or has an invalid writer.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !keys(value, ["schema", "writer", "draft"]) || value.schema !== "motionwright.evaluator-copy/1" || value.writer !== writer)
    throw new Error("Evaluator copy has a foreign writer or unsupported shape.");
  return { schema: "motionwright.evaluator-copy/1", writer, draft: decodeEvaluatorDraft(value.draft, scope) };
}
export function newEvaluatorDraft(project: Project): EvaluatorDraft {
  checkScope(evaluatorScope(project));
  return { schema: "motionwright.evaluator-draft/1", scope: evaluatorScope(project), sequence: 0, fields: {}, pending: null };
}

const custody = (pointer: string | null): EvaluatorCustody => ({ writer: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer, attempt: null });
function assertEvaluatorRecoverySource(scope: string, observation: EvaluatorRecoveryObservation,
  storage: EvaluatorStore, pointers: EvaluatorPointers, own?: { writer: string; raw: string }): void {
  if (!observation.pointerObserved) throw new Error("Read the original evaluator recovery pointer before making a new copy.");
  if (observation.pointer !== null) {
    if (!observation.rawObserved || observation.raw === null) throw new Error("The original evaluator recovery copy is still missing.");
    if (storage.getItem(evaluatorCopyKey(scope, observation.pointer)) !== observation.raw)
      throw new Error("The original evaluator recovery bytes changed. The first observation remains retained.");
    decodeEvaluatorCopy(observation.raw, scope, observation.pointer);
  }
  const pointer = pointers.getItem(evaluatorPointerKey(scope));
  if (pointer !== observation.pointer && !(own && pointer === own.writer && storage.getItem(evaluatorCopyKey(scope, own.writer)) === own.raw))
    throw new Error("The original evaluator recovery pointer changed. No different copy was adopted.");
  if (observation.pointer !== null && storage.getItem(evaluatorCopyKey(scope, observation.pointer)) !== observation.raw)
    throw new Error("The original evaluator recovery bytes changed during confirmation. The first observation remains retained.");
}
function readEvaluatorRecoverySource(scope: string, observation: EvaluatorRecoveryObservation,
  storage: EvaluatorStore, pointers: EvaluatorPointers): EvaluatorDraft | null {
  const pointer = pointers.getItem(evaluatorPointerKey(scope));
  if (observation.pointerObserved && pointer !== observation.pointer)
    throw new Error("The original evaluator recovery pointer changed. No different copy was adopted.");
  if (!observation.pointerObserved) { observation.pointer = pointer; observation.pointerObserved = true; }
  if (pointer === null) { assertEvaluatorRecoverySource(scope, observation, storage, pointers); return null; }
  const raw = storage.getItem(evaluatorCopyKey(scope, pointer));
  if (observation.rawObserved && raw !== observation.raw)
    throw new Error("The original evaluator recovery bytes changed. The first observation remains retained.");
  if (!observation.rawObserved) { observation.raw = raw; observation.rawObserved = true; }
  if (raw === null) throw new Error("The original evaluator recovery copy is still missing.");
  const draft = decodeEvaluatorCopy(raw, scope, pointer).draft;
  assertEvaluatorRecoverySource(scope, observation, storage, pointers);
  return draft;
}
export function openEvaluatorSession(project: Project, storage: EvaluatorStore, pointers: EvaluatorPointers): EvaluatorSession {
  const scope = evaluatorScope(project);
  const observation: EvaluatorRecoveryObservation = { pointerObserved: false, pointer: null, rawObserved: false, raw: null };
  const result: EvaluatorSession = { draft: newEvaluatorDraft(project), custody: custody(null),
    recoveryObservation: observation, failedSource: null, epoch: 0, liveAttempt: null };
  try {
    const recovered = readEvaluatorRecoverySource(scope, observation, storage, pointers);
    if (recovered) result.draft = recovered;
  } catch { result.failedSource = observation; }
  // New runtime, new writer, even when a browser duplicated sessionStorage.
  result.custody = custody(observation.pointer);
  return result;
}
export function evaluatorHasInput(draft: EvaluatorDraft): boolean { return Object.keys(draft.fields).length > 0; }
export function evaluatorRaw(draft: EvaluatorDraft, key: EvaluatorFieldName,
  fallback: string = EVALUATOR_DEFAULTS[key]): string {
  return draft.fields[key]?.raw ?? fallback;
}
export function editEvaluatorField(session: EvaluatorSession, project: Project, key: EvaluatorFieldName, raw: string): void {
  if (session.draft.scope !== evaluatorScope(project)) throw new Error("Evaluator owner changed.");
  if (!fieldName(key) || typeof raw !== "string") throw new Error("Unsupported evaluator input field.");
  const sequence = session.draft.sequence + 1;
  if (!Number.isSafeInteger(sequence)) throw new Error("Evaluator input sequence exhausted.");
  const old = session.draft.fields[key];
  session.draft = { ...session.draft, sequence, fields: { ...session.draft.fields,
    [key]: { raw, base: old?.base ?? evaluatorFieldBase(project, key), sequence } } };
  session.epoch++;
}
export function evaluatorFieldConflict(draft: EvaluatorDraft, project: Project, key: string): boolean {
  if (evaluatorScope(project) !== draft.scope) return true;
  const field = draft.fields[key];
  if (!field) return false;
  try { return field.base !== evaluatorFieldBase(project, key); } catch { return true; }
}
export function forkEvaluatorWriter(session: EvaluatorSession): void {
  if (session.custody.attempt) throw new Error("Verify the incomplete evaluator recovery write before changing writer.");
  session.custody = custody(session.custody.expectedPointer);
}
function assertCurrentCustody(session: EvaluatorSession, storage: EvaluatorStore, pointers: EvaluatorPointers): void {
  const c = session.custody, scope = session.draft.scope;
  if (pointers.getItem(evaluatorPointerKey(scope)) !== c.expectedPointer) throw new Error("Another window changed this tab's evaluator writer pointer.");
  if (storage.getItem(evaluatorCopyKey(scope, c.writer)) !== c.expectedRaw) throw new Error("The editable evaluator copy changed outside this writer.");
}
export function persistEvaluatorSession(session: EvaluatorSession, storage: EvaluatorStore, pointers: EvaluatorPointers): void {
  if (session.failedSource) throw new Error("Read the exact failed source copy before enabling evaluator save.");
  if (session.custody.attempt) throw new Error("An evaluator recovery write is unconfirmed. Retry its exact readback first.");
  const original = session.recoveryObservation, c = session.custody, scope = session.draft.scope;
  if (session.liveAttempt?.prior && session.liveAttempt.prior.key === evaluatorCopyKey(scope, c.writer)
    && session.draft.pending?.id === session.liveAttempt.id)
    throw new Error("Fork the evaluator writer before replacing a retained UNKNOWN with a separately prepared operation.");
  if (original) assertEvaluatorRecoverySource(scope, original, storage, pointers,
    c.expectedRaw === null ? undefined : { writer: c.writer, raw: c.expectedRaw });
  assertCurrentCustody(session, storage, pointers);
  const clean = decodeEvaluatorDraft(session.draft, scope, false);
  const raw = JSON.stringify({ schema: "motionwright.evaluator-copy/1", writer: c.writer, draft: clean } satisfies EvaluatorCopy);
  if (byteLength(raw) > MAX_BYTES) throw new Error("The evaluator recovery exceeds 8 MiB. Export the author input before reducing retained records.");
  const key = evaluatorCopyKey(scope, c.writer), pointerKey = evaluatorPointerKey(scope);
  c.attempt = { raw, previousRaw: c.expectedRaw, previousPointer: c.expectedPointer };
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The evaluator copy did not survive write/readback.");
  if (pointers.getItem(pointerKey) !== c.expectedPointer) throw new Error("Evaluator pointer changed while saving its copy.");
  pointers.setItem(pointerKey, c.writer);
  if (pointers.getItem(pointerKey) !== c.writer) throw new Error("The evaluator writer pointer did not survive readback.");
  if (storage.getItem(key) !== raw) throw new Error("The evaluator data changed during the final pointer readback.");
  if (original) assertEvaluatorRecoverySource(scope, original, storage, pointers, { writer: c.writer, raw });
  c.expectedRaw = raw; c.expectedPointer = c.writer; c.attempt = null;
  session.recoveryObservation = null;
}
/** Retry only the observed failed source and this writer's exact attempted bytes.
 * Reading an earlier UNKNOWN cannot accidentally become permission to save. */
export function retryEvaluatorCustody(session: EvaluatorSession, storage: EvaluatorStore, pointers: EvaluatorPointers): void {
  const scope = session.draft.scope;
  if (session.failedSource) {
    const failed = session.failedSource;
    const recovered = readEvaluatorRecoverySource(scope, failed, storage, pointers);
    let adopted = session.draft;
    if (recovered) {
      if (session.draft.pending && recovered.pending && session.draft.pending.id !== recovered.pending.id)
        throw new Error("Two distinct UNKNOWN evaluator operations require separate review; neither was discarded.");
      const fields = { ...recovered.fields }, later = Object.entries(session.draft.fields);
      let sequence = Math.max(recovered.sequence, session.draft.sequence);
      for (const [key, value] of later) {
        if (!Number.isSafeInteger(++sequence)) throw new Error("Evaluator input sequence exhausted. Both inputs remain retained.");
        fields[key] = { ...value, sequence };
      }
      adopted = { ...recovered, sequence, fields, pending: session.draft.pending ?? recovered.pending };
    }
    // Do not mutate the current buffer until the entire original observation
    // has survived a second read. A later field remains intent even if blank.
    assertEvaluatorRecoverySource(scope, failed, storage, pointers);
    session.draft = adopted; session.failedSource = null; session.custody = custody(failed.pointer); session.liveAttempt = null; session.epoch++;
  }
  const c = session.custody;
  if (c.attempt) {
    const attempt = c.attempt, raw = storage.getItem(evaluatorCopyKey(scope, c.writer)), pointer = pointers.getItem(evaluatorPointerKey(scope));
    if ((raw !== attempt.raw && raw !== attempt.previousRaw) || (pointer !== c.writer && pointer !== attempt.previousPointer))
      throw new Error("The incomplete evaluator write no longer matches its exact bytes/pointer.");
    if (storage.getItem(evaluatorCopyKey(scope, c.writer)) !== raw || pointers.getItem(evaluatorPointerKey(scope)) !== pointer)
      throw new Error("Evaluator recovery changed while its incomplete write was inspected.");
    c.expectedRaw = raw; c.expectedPointer = pointer; c.attempt = null;
  }
  persistEvaluatorSession(session, storage, pointers);
}


function sameObservedProject(left: Project, right: Project): boolean {
  return evaluatorScope(left) === evaluatorScope(right) && left.revision === right.revision && sameEvaluatorValue(left, right);
}
export function evaluatorReviewCurrent(session: EvaluatorSession, project: Project, review: EvaluatorOutcomeReview | null): boolean {
  return !!review && session.draft.pending !== null && review.scope === session.draft.scope && review.epoch === session.epoch
    && review.pendingJson === JSON.stringify(session.draft.pending) && sameObservedProject(project, review.observed);
}
export async function inspectEvaluatorOutcome(session: EvaluatorSession, project: Project, transport: EvaluatorOutcomeTransport): Promise<EvaluatorOutcomeReview> {
  const pending = session.draft.pending;
  if (!pending || session.draft.scope !== evaluatorScope(project)) throw new Error("No matching unconfirmed evaluator operation exists.");
  const selected = structuredClone(fullProject(project)), pendingJson = JSON.stringify(pending), epoch = session.epoch;
  const observed = structuredClone(fullProject(await transport.readProject(selected.id)));
  if (!sameObservedProject(observed, selected)) throw new Error("Reload the current saved project before reviewing this evaluator outcome.");
  if (!Number.isSafeInteger(observed.revision) || observed.revision < pending.source.base_revision
    || observed.revision - pending.source.base_revision > 2048) throw new Error("The complete evaluator history range is unavailable.");
  const events: ProjectEvent[] = []; let cursor = pending.source.base_revision;
  while (cursor < observed.revision) {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || !page.length || page.length > 256) throw new Error("The evaluator history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > observed.revision || !event.change
        || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The evaluator history is not a contiguous saved revision sequence.");
      events.push(structuredClone(event)); cursor = event.revision;
    }
  }
  const after = fullProject(await transport.readProject(selected.id));
  if (!sameObservedProject(after, observed) || session.epoch !== epoch || JSON.stringify(session.draft.pending) !== pendingJson)
    throw new Error("The evaluator buffer or saved project changed during outcome review.");
  return { scope: session.draft.scope, pendingJson, epoch, observed, events,
    matchingRevisions: events.filter(event => sameEvaluatorValue(event.change, JSON.parse(pending.changeJson))).map(event => event.revision) };
}
function assertCommandSource(project: Project, change: EvaluatorChange): void {
  if (project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content"))
    throw new Error("Project content locked: cannot update creative evaluator.");
  const bundles = project.production_design?.evaluator_bundles ?? [], reports = project.production_design?.evaluation_reports ?? [];
  if (change.type === "register_creative_evaluator") {
    validateEvaluator(change.bundle);
    if (bundles.length >= 64) throw new Error("Evaluator bundle version budget exceeded.");
    const latest = Math.max(0, ...bundles.filter(bundle => bundle.id === change.bundle.id).map(bundle => bundle.version));
    if (change.bundle.version !== latest + 1) throw new Error("Manifest version must follow locally recorded source history.");
  } else {
    if (project.locks.some(lock => lock.resource === "scene:" + change.scene_id && lock.kind === "content"))
      throw new Error("Scene content locked: cannot record evaluation.");
    if (reports.length >= 256) throw new Error("Creative evaluation report budget exceeded.");
    const bundle = bundles.find(bundle => bundle.id === change.evaluator_id && bundle.version === change.evaluator_version);
    if (!bundle || !project.scenes.some(scene => scene.id === change.scene_id)) throw new Error("Choose an existing evaluator version and scene.");
    validateEvaluator(bundle);
  }
}
export function prepareEvaluatorSubmission(session: EvaluatorSession, project: Project, requested: EvaluatorOperation,
  review: EvaluatorOutcomeReview | null = null, id: string = crypto.randomUUID()): { id: string; change: EvaluatorChange; draft: EvaluatorDraft } {
  if (session.failedSource || session.custody.attempt || session.custody.expectedRaw === null
    || session.custody.expectedPointer !== session.custody.writer) throw new Error("Confirm the evaluator recovery copy and pointer before saving.");
  if (session.draft.scope !== evaluatorScope(project)) throw new Error("Evaluator project/generation/branch changed.");
  if (session.draft.pending && !evaluatorReviewCurrent(session, project, review))
    throw new Error("UNKNOWN is retained. Read the current project/history before creating a separate explicit operation.");
  if (!uuid(id) || session.draft.pending?.id === id) throw new Error("Use a new evaluator operation identity.");
  const checkedProject = fullProject(project), op = operation(requested);
  if (!Number.isSafeInteger(checkedProject.revision + 1)) throw new Error("The next evaluator revision cannot be represented exactly.");
  const saved: unknown = JSON.parse(session.custody.expectedRaw);
  if (!record(saved) || !sameEvaluatorValue(decodeEvaluatorDraft(saved.draft, session.draft.scope, false), session.draft))
    throw new Error("Confirm the current evaluator author input before preparing its operation.");
  const sentFields: Record<string, EvaluatorField> = {};
  for (const key of usedFields(op)) sentFields[key] = session.draft.fields[key] ? { ...session.draft.fields[key] }
    : { raw: evaluatorFieldBase(project, key), base: evaluatorFieldBase(project, key), sequence: 0 };
  let change: EvaluatorChange;
  if (op.kind === "author") {
    change = { type: "register_creative_evaluator", bundle: authorBundle(key => sentFields[key].raw, crypto.randomUUID(), () => crypto.randomUUID()) };
  } else if (op.kind === "import") {
    if (sentFields.confirmImport.raw !== "true") throw new Error("Confirm the evaluator source rights before import.");
    if (byteLength(sentFields.importText.raw) > 16 * 1024) throw new Error("Evaluator import exceeds bounded JSON budget.");
    const bundle = JSON.parse(sentFields.importText.raw) as CreativeEvaluatorBundle;
    validateEvaluator(bundle); change = { type: "register_creative_evaluator", bundle };
  } else {
    const match = /^([0-9a-f-]+):([1-9][0-9]*)$/i.exec(sentFields.selected.raw);
    if (!match || !uuid(match[1]) || !Number.isSafeInteger(Number(match[2]))) throw new Error("Choose an immutable evaluator version.");
    change = { type: "evaluate_creative_scene", evaluator_id: match[1], evaluator_version: Number(match[2]), scene_id: op.sceneId };
  }
  assertCommandSource(checkedProject, change); assertSentFields(change, op, sentFields);
  const pending: EvaluatorPending = { id, source: evaluatorSource(checkedProject), sourceProjectJson: JSON.stringify(checkedProject), operation: op,
    changeJson: JSON.stringify(change), sentFields, outcome: "UNKNOWN", diagnostic: null };
  const draft = decodeEvaluatorDraft({ ...session.draft, pending }, session.draft.scope, false);
  session.liveAttempt = { id, changeJson: pending.changeJson, dispatched: false,
    prior: session.draft.pending ? { key: evaluatorCopyKey(session.draft.scope, session.custody.writer), raw: session.custody.expectedRaw } : null };
  return { id, change, draft };
}
/** Called once immediately before the original commit callback, after exact copy/pointer readback. */
export function guardEvaluatorSubmission(session: EvaluatorSession, project: Project, id: string, change: EvaluatorChange,
  storage: EvaluatorStore, pointers: EvaluatorPointers): void {
  const pending = session.draft.pending, live = session.liveAttempt, c = session.custody;
  if (!live || live.dispatched || live.id !== id || !pending || pending.id !== id
    || live.changeJson !== JSON.stringify(change) || pending.changeJson !== live.changeJson)
    throw new Error("Only this newly prepared evaluator attempt may be dispatched once. UNKNOWN is never replayed.");
  if (session.failedSource || session.recoveryObservation || c.attempt || c.expectedRaw === null || c.expectedPointer !== c.writer)
    throw new Error("Confirm the evaluator recovery copy and pointer before dispatch.");
  if (!sameObservedProject(fullProject(project), fullProject(JSON.parse(pending.sourceProjectJson))))
    throw new Error("The complete evaluator Project changed before dispatch.");
  assertCommandSource(project, change); assertCurrentCustody(session, storage, pointers);
  const saved: unknown = JSON.parse(c.expectedRaw);
  if (!record(saved) || !sameEvaluatorValue(decodeEvaluatorDraft(saved.draft, session.draft.scope, false), session.draft))
    throw new Error("The confirmed evaluator copy does not contain this exact pending input.");
  if (live.prior && (live.prior.key === evaluatorCopyKey(session.draft.scope, c.writer) || storage.getItem(live.prior.key) !== live.prior.raw))
    throw new Error("Preserve the original UNKNOWN under its original key before dispatching a separate operation.");
  assertCurrentCustody(session, storage, pointers);
  live.dispatched = true;
}
function stableReceiptContent(before: Project, receipt: Project): void {
  // Revision/branch heads/review bookkeeping are confirmed by the transaction coordinator.
  // Compare all other source fields, including optional fields, without requiring presentation fixtures to mint bookkeeping.
  const stable = (project: Project) => Object.fromEntries(Object.entries(project).filter(([key]) =>
    !["revision", "updated_at", "branches", "reviews", "schema_version", "production_design"].includes(key)));
  if (!sameEvaluatorValue(stable(before), stable(receipt))) throw new Error("The evaluator receipt changed unrelated saved source content.");
  if (receipt.schema_version !== before.schema_version && !(before.schema_version === 1 && receipt.schema_version === 2))
    throw new Error("The evaluator receipt has an unrelated project schema.");
  const branches = before.branches.map(branch => branch.id === before.active_branch ? { ...branch, head_revision: receipt.revision } : branch);
  if (!sameEvaluatorValue(receipt.branches, before.branches) && !sameEvaluatorValue(receipt.branches, branches))
    throw new Error("The evaluator receipt changed unrelated branch authority.");
  const reviews = before.reviews.map(review => review.anchor.branch_id === before.active_branch && review.anchor.revision < receipt.revision && review.status === "resolved"
    ? { ...review, status: "needs_recheck", resolution: null, resolved_at: null } : review);
  if (!sameEvaluatorValue(receipt.reviews, before.reviews) && !sameEvaluatorValue(receipt.reviews, reviews))
    throw new Error("The evaluator receipt changed unrelated review history.");
  const design = (project: Project) => Object.fromEntries(Object.entries({ ...emptyProductionDesign(), ...project.production_design })
    .filter(([key]) => !["evaluator_bundles", "evaluation_reports"].includes(key)));
  if (!sameEvaluatorValue(design(before), design(receipt))) throw new Error("The evaluator receipt changed unrelated production design.");
}
export async function assertEvaluatorChangeReceipt(change: EvaluatorChange, sourceProject: Project, value: Project): Promise<void> {
  const before = structuredClone(fullProject(sourceProject)), receipt = structuredClone(fullProject(value));
  if (evaluatorScope(receipt) !== evaluatorScope(before) || receipt.revision !== before.revision + 1
    || !Number.isSafeInteger(receipt.revision)) throw new Error("The original evaluator response has foreign or advanced authority.");
  assertCommandSource(before, change); stableReceiptContent(before, receipt);
  const oldBundles = before.production_design?.evaluator_bundles ?? [], oldReports = before.production_design?.evaluation_reports ?? [];
  const bundles = receipt.production_design?.evaluator_bundles ?? [], reports = receipt.production_design?.evaluation_reports ?? [];
  if (change.type === "register_creative_evaluator") {
    if (!sameEvaluatorValue(bundles, [...oldBundles, change.bundle]) || !sameEvaluatorValue(reports, oldReports))
      throw new Error("The evaluator receipt does not confirm the exact immutable bundle and preserved history.");
    return;
  }
  if (!sameEvaluatorValue(bundles, oldBundles) || reports.length !== oldReports.length + 1
    || !sameEvaluatorValue(reports.slice(0, -1), oldReports)) throw new Error("The evaluation receipt does not preserve immutable source/report history.");
  const bundle = oldBundles.find(bundle => bundle.id === change.evaluator_id && bundle.version === change.evaluator_version)!;
  const scene = before.scenes.find(scene => scene.id === change.scene_id)!;
  const added = reports[reports.length - 1], observations = bundle.criteria.map(criterion => observeCriterion(scene, criterion));
  const conclusion = observations.some(observation => observation.outcome === "fail") ? "fail"
    : observations.some(observation => observation.outcome === "not_assessed") ? "needs_human_review" : "pass_technical_only";
  if (!uuid(added?.id) || oldReports.some(report => report.id === added.id)) throw new Error("The evaluation report has no new canonical identity.");
  const expected = { schema: REPORT_SCHEMA, id: added.id, evaluator_id: bundle.id, evaluator_version: bundle.version,
    evaluator_sha256: await evaluatorDigest(bundle), scene_id: scene.id, source_generation: before.generation,
    source_revision: before.revision, observations, conclusion, creative_approval: "not_assessed" };
  if (!sameEvaluatorValue(added, expected)) throw new Error("The evaluation receipt does not confirm the exact source-bound technical report; human approval remains not assessed.");
}
/** Shared read-only receipt validation; neither export grants dispatch authority. */
export { fullProject as checkedEvaluatorProject };
export async function assertEvaluatorReceipt(change: EvaluatorChange, pending: EvaluatorPending, value: Project): Promise<void> {
  const before = fullProject(JSON.parse(pending.sourceProjectJson)), receipt = structuredClone(fullProject(value));
  if (evaluatorScope(receipt) !== evaluatorScope(before) || receipt.revision !== before.revision + 1
    || !Number.isSafeInteger(receipt.revision)) throw new Error("The original evaluator response has foreign or advanced authority.");
  if (pending.changeJson !== JSON.stringify(change) || !sameEvaluatorValue(pending.source, evaluatorSource(before)))
    throw new Error("The evaluator response differs from its retained source/command.");
  await assertEvaluatorChangeReceipt(change, before, receipt);
}
/** Only the original live callback may consume pending intent. Readbacks/recovery have no live dispatch permission. */
export async function acknowledgeEvaluatorSubmission(session: EvaluatorSession, id: string, change: EvaluatorChange, value: Project): Promise<void> {
  const pending = session.draft.pending, live = session.liveAttempt;
  if (!pending || pending.id !== id || pending.changeJson !== JSON.stringify(change) || !live || live.id !== id || !live.dispatched
    || live.changeJson !== pending.changeJson) throw new Error("An unrelated evaluator response or observed snapshot cannot consume this intent.");
  const pendingJson = JSON.stringify(pending), receipt = structuredClone(fullProject(value));
  await assertEvaluatorReceipt(change, pending, receipt);
  if (session.liveAttempt !== live || JSON.stringify(session.draft.pending) !== pendingJson)
    throw new Error("The original evaluator attempt changed while its receipt was checked.");
  const fields = { ...session.draft.fields };
  // Author controls retain their exact visible raw bytes. Import consumes only the sent controls with no later edit sequence.
  if (pending.operation.kind === "import") for (const key of ["importText", "confirmImport"] as const) {
    if (fields[key]?.sequence === pending.sentFields[key].sequence) delete fields[key];
  }
  let sequence = session.draft.sequence;
  if (change.type === "register_creative_evaluator" && (fields.selected?.sequence ?? 0) === pending.sentFields.selected.sequence) {
    if (!Number.isSafeInteger(++sequence)) throw new Error("Evaluator input sequence exhausted. The confirmed source remains retained.");
    const selected = change.bundle.id + ":" + change.bundle.version;
    fields.selected = { raw: selected, base: selected, sequence };
  }
  session.draft = { ...session.draft, sequence, fields, pending: null }; session.liveAttempt = null; session.epoch++;
}
export function retainEvaluatorFailure(draft: EvaluatorDraft, id: string, diagnostic: string, notSent = false): EvaluatorDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, outcome: notSent ? "NOT_SENT" : "UNKNOWN", diagnostic } } : draft;
}

export function originalEvaluatorRecoveryCopy(scope: string, observation: EvaluatorRecoveryObservation | null): { key: string; raw: string; copy: EvaluatorCopy | null } | null {
  if (!observation?.pointerObserved || observation.pointer === null || !observation.rawObserved || observation.raw === null) return null;
  let copy: EvaluatorCopy | null = null;
  try { copy = decodeEvaluatorCopy(observation.raw, scope, observation.pointer); } catch { /* Keep original unreadable bytes downloadable. */ }
  return { key: evaluatorCopyKey(scope, observation.pointer), raw: observation.raw, copy };
}
export function evaluatorCopies(storage: EvaluatorStore, scope: string,
  observation?: EvaluatorRecoveryObservation | null): { key: string; raw: string; copy: EvaluatorCopy | null }[] {
  const prefix = EVALUATOR_COPY_PREFIX + encodeURIComponent(scope) + ":", result: { key: string; raw: string; copy: EvaluatorCopy | null }[] = [];
  if (storage.length > 10000) throw new Error("Evaluator copy enumeration is too large; export current input and inspect storage.");
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i); if (!key?.startsWith(prefix)) continue;
    if (result.length >= 128) throw new Error("More than 128 evaluator recovery copies exist. No copy was silently deleted.");
    const raw = storage.getItem(key);
    // Pin a source encountered during listing before another index/read can
    // fail, including an observed absence. Retry cannot silently substitute
    // newer bytes under this writer after a listed copy was missing.
    if (observation?.pointerObserved && observation.pointer !== null && !observation.rawObserved && key === evaluatorCopyKey(scope, observation.pointer)) {
      observation.raw = raw; observation.rawObserved = true;
    }
    if (raw === null) continue;
    let copy: EvaluatorCopy | null = null;
    try { copy = decodeEvaluatorCopy(raw, scope, key.slice(prefix.length)); } catch { /* opaque copy remains visible */ }
    result.push({ key, raw, copy });
  }
  return result;
}
export function restoreEvaluatorCopy(session: EvaluatorSession, raw: string, writer: string, storage: EvaluatorStore, pointers: EvaluatorPointers): void {
  if (session.draft.pending) throw new Error("An UNKNOWN operation cannot be replaced by a different copy.");
  const restored = decodeEvaluatorCopy(raw, session.draft.scope, writer).draft;
  if (!evaluatorHasInput(restored) && !restored.pending) throw new Error("This is a clean checkpoint with no pending author input.");
  // Verify both the current buffer and selected source before any replacement.
  persistEvaluatorSession(session, storage, pointers);
  const scope = session.draft.scope, key = evaluatorCopyKey(scope, writer);
  if (storage.getItem(key) !== raw) throw new Error("The selected evaluator copy changed.");
  forkEvaluatorWriter(session);
  // The prior buffer was verified under its original key. Keep the explicitly
  // selected candidate in memory if retention fails, especially its UNKNOWN;
  // rollback to a clean buffer would let Retry erase that known operation.
  session.draft = restored; session.liveAttempt = null; session.epoch++;
  persistEvaluatorSession(session, storage, pointers);
}
export function exportEvaluatorDraft(draft: EvaluatorDraft): string {
  return JSON.stringify({ schema: "motionwright.evaluator-author-file/1", draft: decodeEvaluatorDraft(draft, draft.scope, false) }, null, 2);
}
export function importEvaluatorDraft(raw: string, scope: string): EvaluatorDraft {
  if (byteLength(raw) > MAX_BYTES) throw new Error("The evaluator author file exceeds 8 MiB.");
  const file: unknown = JSON.parse(raw);
  if (!record(file) || !keys(file, ["schema", "draft"]) || file.schema !== "motionwright.evaluator-author-file/1") throw new Error("Unsupported evaluator author file.");
  return decodeEvaluatorDraft(file.draft, scope);
}
export function adoptEvaluatorFile(session: EvaluatorSession, draft: EvaluatorDraft, storage: EvaluatorStore, pointers: EvaluatorPointers): void {
  if (session.draft.pending) throw new Error("An UNKNOWN operation cannot be replaced by an author file.");
  const decoded = decodeEvaluatorDraft(draft, session.draft.scope);
  persistEvaluatorSession(session, storage, pointers);
  forkEvaluatorWriter(session);
  session.draft = decoded; session.liveAttempt = null; session.epoch++;
  persistEvaluatorSession(session, storage, pointers);
}
