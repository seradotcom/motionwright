import { validateEvaluator, type CreativeEvaluatorBundle, type EvaluatorChange } from "./creativeEvaluator";
import {
  EVALUATOR_EXPERIMENT_SCHEMA, previewEvaluatorExperiment, revalidateEvaluatorExperiment,
  type EvaluatorExperimentResult,
} from "./evaluatorExperiment";
import { assertEvaluatorChangeReceipt, checkedEvaluatorProject as fullProject,
  sameEvaluatorValue as sameExperimentValue } from "./creativeEvaluatorDraft";
import type { Project, ProjectEvent } from "./types";

// Retained input is comparison data, never a resumable command or executable skill.
export const EXPERIMENT_COPY_PREFIX = "motionwright.evaluator-experiment-copy.v1:";
export const EXPERIMENT_POINTER_PREFIX = "motionwright.evaluator-experiment-writer.v1:";
const MAX_BYTES = 8 * 1024 * 1024;
export const EXPERIMENT_DEFAULTS = {
  candidateText: "", selectedScenes: "[]", reviewerReason: "", ackRights: "false", ackLimit: "false",
} as const;
export type ExperimentFieldName = keyof typeof EXPERIMENT_DEFAULTS;
export type ExperimentStore = Pick<Storage, "getItem" | "setItem" | "key" | "length">;
export type ExperimentPointers = Pick<Storage, "getItem" | "setItem">;
export interface ExperimentField { raw: string; base: string; sequence: number }
export interface ExperimentSource { project_id: string; generation: string; branch_id: string; base_revision: number }
export interface ExperimentPending {
  id: string; source: ExperimentSource; sourceProjectJson: string; changeJson: string;
  comparisonJson: string; sentFields: Record<string, ExperimentField>;
  outcome: "UNKNOWN" | "NOT_SENT"; diagnostic: string | null;
}
export interface ExperimentDraft {
  schema: "motionwright.evaluator-experiment-draft/1"; scope: string; initialScenes: string;
  sequence: number; fields: Record<string, ExperimentField>; pending: ExperimentPending | null;
}
export interface ExperimentCopy { schema: "motionwright.evaluator-experiment-copy/1"; writer: string; draft: ExperimentDraft }
export interface ExperimentCustody {
  writer: string; expectedRaw: string | null; expectedPointer: string | null;
  attempt: { raw: string; previousRaw: string | null; previousPointer: string | null } | null;
}
export interface ExperimentRecoveryObservation {
  pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null;
}
export interface ExperimentSession {
  draft: ExperimentDraft; custody: ExperimentCustody;
  recoveryObservation: ExperimentRecoveryObservation | null; failedSource: ExperimentRecoveryObservation | null; epoch: number;
  liveAttempt: { id: string; changeJson: string; dispatched: boolean; prior: { key: string; raw: string } | null } | null;
}
export interface ExperimentComparison {
  scope: string; sourceProject: Project; candidateText: string; selectedScenes: string;
  candidateSequence: number; scenesSequence: number; result: EvaluatorExperimentResult;
}
export interface ExperimentOutcomeReview {
  scope: string; pendingJson: string; epoch: number; observed: Project; events: ProjectEvent[]; matchingRevisions: number[];
}
export interface ExperimentOutcomeTransport {
  readProject(id: string): Promise<Project>;
  readHistory(project: Project, after: number, limit: number): Promise<ProjectEvent[]>;
}
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const keys = (value: Record<string, unknown>, names: readonly string[]) => Object.keys(value).length === names.length && names.every(name => Object.hasOwn(value, name));
const byteLength = (raw: string) => new TextEncoder().encode(raw).length;
const fieldName = (key: string): key is ExperimentFieldName => Object.hasOwn(EXPERIMENT_DEFAULTS, key);
const fieldNames = Object.keys(EXPERIMENT_DEFAULTS) as ExperimentFieldName[];
const projectOwner = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
export const experimentScope = (project: Project, baseline: Pick<CreativeEvaluatorBundle, "id" | "version">) =>
  JSON.stringify([project.id, project.generation, project.active_branch, baseline.id, baseline.version]);
export const experimentCopyKey = (scope: string, writer: string) => EXPERIMENT_COPY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const experimentPointerKey = (scope: string) => EXPERIMENT_POINTER_PREFIX + encodeURIComponent(scope);
function checkScope(scope: string): [string, string, string, string, number] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 5 || !value.slice(0, 4).every(uuid) || !integer(value[4]) || value[4] < 1)
    throw new Error("Invalid evaluator experiment recovery owner.");
  return value as [string, string, string, string, number];
}
function matchingProject(scope: string, project: Project): boolean {
  return JSON.stringify(checkScope(scope).slice(0, 3)) === projectOwner(project);
}
function baselineFor(project: Project, scope: string): CreativeEvaluatorBundle {
  const [, , , id, version] = checkScope(scope);
  if (!matchingProject(scope, project)) throw new Error("Experiment project/generation/branch changed.");
  const found = project.production_design?.evaluator_bundles?.find(bundle => bundle.id === id && bundle.version === version);
  if (!found) throw new Error("The immutable experiment baseline is unavailable.");
  validateEvaluator(found); return found;
}
export function experimentSelectedScenes(raw: string): string[] {
  const values: unknown = JSON.parse(raw);
  if (!Array.isArray(values) || values.length > 3 || !values.every(uuid) || new Set(values).size !== values.length)
    throw new Error("Retain up to three distinct source scene IDs; no unavailable selection was silently removed.");
  return [...values];
}
export function experimentFieldBase(draft: ExperimentDraft, key: ExperimentFieldName): string {
  return key === "selectedScenes" ? draft.initialScenes : EXPERIMENT_DEFAULTS[key];
}
export function experimentRaw(draft: ExperimentDraft, key: ExperimentFieldName): string {
  return draft.fields[key]?.raw ?? experimentFieldBase(draft, key);
}
function fieldMap(value: unknown, sequence: number): Record<string, ExperimentField> {
  if (!record(value) || Object.keys(value).length > fieldNames.length) throw new Error("Unreadable experiment field collection.");
  const result: Record<string, ExperimentField> = {};
  for (const [key, field] of Object.entries(value)) {
    if (!fieldName(key) || !record(field) || !keys(field, ["raw", "base", "sequence"])
      || typeof field.raw !== "string" || typeof field.base !== "string" || !integer(field.sequence) || field.sequence > sequence)
      throw new Error("Unreadable experiment input buffer: " + key);
    result[key] = { raw: field.raw, base: field.base, sequence: field.sequence };
  }
  return result;
}
const sourceOf = (project: Project): ExperimentSource => ({ project_id: project.id, generation: project.generation,
  branch_id: project.active_branch, base_revision: project.revision });
function source(value: unknown, scope: string): ExperimentSource {
  if (!record(value) || !keys(value, ["project_id", "generation", "branch_id", "base_revision"])
    || !uuid(value.project_id) || !uuid(value.generation) || !uuid(value.branch_id) || !integer(value.base_revision)
    || JSON.stringify([value.project_id, value.generation, value.branch_id]) !== JSON.stringify(checkScope(scope).slice(0, 3)))
    throw new Error("Unreadable experiment pending authority.");
  return { project_id: value.project_id, generation: value.generation, branch_id: value.branch_id, base_revision: value.base_revision };
}
function registration(value: unknown): Extract<EvaluatorChange, { type: "register_creative_evaluator" }> {
  if (!record(value) || !keys(value, ["type", "bundle"]) || value.type !== "register_creative_evaluator")
    throw new Error("Unsupported retained experiment command; it cannot be replayed.");
  validateEvaluator(value.bundle as CreativeEvaluatorBundle);
  return value as unknown as Extract<EvaluatorChange, { type: "register_creative_evaluator" }>;
}
function rationale(raw: string): boolean {
  return raw.trim().length >= 12 && raw.trim().length <= 400 && !/[\u0000-\u001f\u007f]/u.test(raw);
}
function retainedComparison(value: unknown, project: Project, scope: string, change: EvaluatorChange): EvaluatorExperimentResult {
  const baseline = baselineFor(project, scope);
  if (!record(value) || !keys(value, ["schema", "projectId", "generation", "revision", "baselineId", "baselineVersion", "baselineSha256",
    "candidateVersion", "candidateSha256", "selectedSceneIds", "comparisons", "removedCriterionIds", "addedCriterionIds", "status",
    "humanCreativeApproval", "rightsAuthenticated", "executableCodeRun", "evaluatorInstalled", "providerCalled", "candidate"])
    || value.schema !== EVALUATOR_EXPERIMENT_SCHEMA || value.projectId !== project.id || value.generation !== project.generation
    || value.revision !== project.revision || value.baselineId !== baseline.id || value.baselineVersion !== baseline.version
    || change.type !== "register_creative_evaluator" || value.candidateVersion !== change.bundle.version
    || value.candidateVersion !== baseline.version + 1 || !sameExperimentValue(value.candidate, change.bundle)
    || ![value.baselineSha256, value.candidateSha256].every(item => typeof item === "string" && /^[a-f0-9]{64}$/.test(item))
    || value.status !== "TECHNICAL_OBSERVATION_ONLY" || value.humanCreativeApproval !== "not_assessed"
    || value.rightsAuthenticated !== false || value.executableCodeRun !== false || value.evaluatorInstalled !== false || value.providerCalled !== false
    || !Array.isArray(value.comparisons) || !Array.isArray(value.addedCriterionIds) || !value.addedCriterionIds.every(uuid)
    || !Array.isArray(value.removedCriterionIds) || !value.removedCriterionIds.every(uuid))
    throw new Error("The retained experiment comparison has an unsupported shape or claimed authority.");
  const ids = experimentSelectedScenes(JSON.stringify(value.selectedSceneIds));
  if (!ids.length || value.comparisons.length !== ids.length || ids.some(id => !project.scenes.some(scene => scene.id === id)))
    throw new Error("The retained experiment has missing or unrelated source scenes.");
  for (let i = 0; i < value.comparisons.length; i++) {
    const entry = value.comparisons[i];
    if (!record(entry) || !keys(entry, ["sceneId", "sceneName", "baseline", "candidate", "baselinePass", "baselineFail", "baselineAbstain",
      "candidatePass", "candidateFail", "candidateAbstain", "matchingCriterionRegressions", "matchingCriterionRecoveries"])
      || entry.sceneId !== ids[i] || typeof entry.sceneName !== "string"
      || ["baselinePass", "baselineFail", "baselineAbstain", "candidatePass", "candidateFail", "candidateAbstain", "matchingCriterionRegressions", "matchingCriterionRecoveries"]
        .some(key => !integer(entry[key]))) throw new Error("Unreadable retained experiment scene comparison.");
    for (const key of ["baseline", "candidate"] as const) {
      const observations = entry[key];
      if (!Array.isArray(observations) || observations.length > 16 || observations.some(item => !record(item)
        || !keys(item, ["criterion_id", "outcome", "explanation"]) || !uuid(item.criterion_id)
        || !["pass", "fail", "not_assessed"].includes(String(item.outcome)) || typeof item.explanation !== "string"))
        throw new Error("Unreadable retained experiment observations.");
    }
  }
  // Custody decoding is not revalidation/admission. A new submission always
  // runs the unchanged existing experiment interpreter again.
  return value as unknown as EvaluatorExperimentResult;
}
function assertSentFields(change: EvaluatorChange, result: EvaluatorExperimentResult, fields: Record<string, ExperimentField>) {
  if (!keys(fields, fieldNames) || fields.ackRights.raw !== "true" || fields.ackLimit.raw !== "true" || !rationale(fields.reviewerReason.raw)
    || change.type !== "register_creative_evaluator" || !sameExperimentValue(JSON.parse(fields.candidateText.raw), change.bundle)
    || !sameExperimentValue(experimentSelectedScenes(fields.selectedScenes.raw), result.selectedSceneIds))
    throw new Error("The retained experiment request differs from the explicit owner review input.");
}
export function decodeExperimentDraft(value: unknown, scope: string, recovered = true): ExperimentDraft {
  checkScope(scope);
  if (!record(value) || !keys(value, ["schema", "scope", "initialScenes", "sequence", "fields", "pending"])
    || value.schema !== "motionwright.evaluator-experiment-draft/1" || value.scope !== scope
    || typeof value.initialScenes !== "string" || !integer(value.sequence)) throw new Error("Experiment recovery has a foreign owner or unsupported shape.");
  experimentSelectedScenes(value.initialScenes);
  const fields = fieldMap(value.fields, value.sequence); let pending: ExperimentPending | null = null;
  if (value.pending !== null) {
    const entry = value.pending;
    if (!record(entry) || !keys(entry, ["id", "source", "sourceProjectJson", "changeJson", "comparisonJson", "sentFields", "outcome", "diagnostic"])
      || !uuid(entry.id) || typeof entry.changeJson !== "string" || typeof entry.sourceProjectJson !== "string" || typeof entry.comparisonJson !== "string"
      || !["UNKNOWN", "NOT_SENT"].includes(String(entry.outcome)) || !(entry.diagnostic === null || typeof entry.diagnostic === "string"))
      throw new Error("The experiment pending intent is unreadable. It cannot be treated as unsent.");
    const expected = source(entry.source, scope), project = fullProject(JSON.parse(entry.sourceProjectJson));
    if (!sameExperimentValue(expected, sourceOf(project))) throw new Error("Retained experiment Project differs from its original authority.");
    const change = registration(JSON.parse(entry.changeJson)), result = retainedComparison(JSON.parse(entry.comparisonJson), project, scope, change);
    const sentFields = fieldMap(entry.sentFields, value.sequence); assertSentFields(change, result, sentFields);
    pending = { id: entry.id, source: expected, sourceProjectJson: entry.sourceProjectJson, changeJson: entry.changeJson,
      comparisonJson: entry.comparisonJson, sentFields, outcome: recovered ? "UNKNOWN" : entry.outcome as "UNKNOWN" | "NOT_SENT", diagnostic: entry.diagnostic };
  }
  return { schema: "motionwright.evaluator-experiment-draft/1", scope, initialScenes: value.initialScenes, sequence: value.sequence, fields, pending };
}
export function decodeExperimentCopy(raw: string, scope: string, writer: string): ExperimentCopy {
  if (byteLength(raw) > MAX_BYTES || !uuid(writer)) throw new Error("Experiment copy exceeds its import budget or has an invalid writer.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !keys(value, ["schema", "writer", "draft"]) || value.schema !== "motionwright.evaluator-experiment-copy/1" || value.writer !== writer)
    throw new Error("Experiment copy has a foreign writer or unsupported shape.");
  return { schema: "motionwright.evaluator-experiment-copy/1", writer, draft: decodeExperimentDraft(value.draft, scope) };
}
export function newExperimentDraft(project: Project, baseline: CreativeEvaluatorBundle, sceneId: string | null): ExperimentDraft {
  const scope = experimentScope(project, baseline); checkScope(scope); baselineFor(project, scope);
  const ids = sceneId === null ? [] : [sceneId]; experimentSelectedScenes(JSON.stringify(ids));
  return { schema: "motionwright.evaluator-experiment-draft/1", scope, initialScenes: JSON.stringify(ids), sequence: 0, fields: {}, pending: null };
}
export function openExperimentSession(project: Project, baseline: CreativeEvaluatorBundle, sceneId: string | null,
  storage: ExperimentStore, pointers: ExperimentPointers): ExperimentSession {
  const scope = experimentScope(project, baseline), observation: ExperimentRecoveryObservation = {
    pointerObserved: false, pointer: null, rawObserved: false, raw: null,
  };
  const session: ExperimentSession = { draft: newExperimentDraft(project, baseline, sceneId), custody: custody(null),
    recoveryObservation: observation, failedSource: null, epoch: 0, liveAttempt: null };
  try { const recovered = readExperimentRecoverySource(scope, observation, storage, pointers); if (recovered) session.draft = recovered; }
  catch { session.failedSource = observation; }
  session.custody = custody(observation.pointer); return session;
}
export const experimentHasInput = (draft: ExperimentDraft) => Object.keys(draft.fields).length > 0;
export function editExperimentField(session: ExperimentSession, project: Project, baseline: CreativeEvaluatorBundle,
  key: ExperimentFieldName, raw: string): void {
  if (session.draft.scope !== experimentScope(project, baseline) || !fieldName(key) || typeof raw !== "string")
    throw new Error("Experiment owner or input field changed.");
  const sequence = session.draft.sequence + 1;
  if (!Number.isSafeInteger(sequence)) throw new Error("Experiment input sequence exhausted.");
  const previous = session.draft.fields[key];
  session.draft = { ...session.draft, sequence, fields: { ...session.draft.fields,
    [key]: { raw, base: previous?.base ?? experimentFieldBase(session.draft, key), sequence } } }; session.epoch++;
}
export function experimentComparisonCurrent(session: ExperimentSession, project: Project, comparison: ExperimentComparison | null): boolean {
  return !!comparison && comparison.scope === session.draft.scope && matchingProject(session.draft.scope, project)
    && sameExperimentValue(project, comparison.sourceProject)
    && experimentRaw(session.draft, "candidateText") === comparison.candidateText
    && experimentRaw(session.draft, "selectedScenes") === comparison.selectedScenes
    && (session.draft.fields.candidateText?.sequence ?? 0) === comparison.candidateSequence
    && (session.draft.fields.selectedScenes?.sequence ?? 0) === comparison.scenesSequence;
}
export async function compareExperiment(session: ExperimentSession, project: Project): Promise<ExperimentComparison> {
  const sourceProject = structuredClone(fullProject(project)), baseline = baselineFor(sourceProject, session.draft.scope);
  const candidateText = experimentRaw(session.draft, "candidateText"), selectedScenes = experimentRaw(session.draft, "selectedScenes");
  const candidateSequence = session.draft.fields.candidateText?.sequence ?? 0, scenesSequence = session.draft.fields.selectedScenes?.sequence ?? 0;
  const result = await previewEvaluatorExperiment(sourceProject, baseline.id, baseline.version, candidateText, experimentSelectedScenes(selectedScenes));
  const compared = { scope: session.draft.scope, sourceProject, candidateText, selectedScenes, candidateSequence, scenesSequence, result };
  if (!experimentComparisonCurrent(session, project, compared)) throw new Error("The experimental input changed during comparison. Later input was retained.");
  return compared;
}
function assertCommandSource(project: Project, change: EvaluatorChange, scope: string) {
  const baseline = baselineFor(project, scope);
  if (!Number.isSafeInteger(project.revision + 1)) throw new Error("Experiment revision exceeds exact integer authority.");
  if (project.locks.some(lock => lock.resource === "project:" + project.id && lock.kind === "content")) throw new Error("Project content locked: cannot register an experiment.");
  const bundles = project.production_design?.evaluator_bundles ?? [];
  if (bundles.length >= 64) throw new Error("Creative evaluator version budget exceeded.");
  if (change.type !== "register_creative_evaluator" || change.bundle.id !== baseline.id
    || change.bundle.version !== Math.max(...bundles.filter(item => item.id === baseline.id).map(item => item.version)) + 1)
    throw new Error("The experiment must follow the actual latest immutable source version.");
  validateEvaluator(change.bundle);
}
export async function prepareExperimentSubmission(session: ExperimentSession, project: Project, comparison: ExperimentComparison,
  review: ExperimentOutcomeReview | null = null, id: string = crypto.randomUUID()): Promise<{ id: string; change: EvaluatorChange; draft: ExperimentDraft }> {
  if (session.failedSource || session.custody.attempt || session.custody.expectedRaw === null || session.custody.expectedPointer !== session.custody.writer)
    throw new Error("Confirm the experiment recovery copy and pointer before registration.");
  if (session.draft.pending && !experimentReviewCurrent(session, project, review))
    throw new Error("UNKNOWN is retained. Read the current project/history before a separate explicit experiment registration.");
  if (!uuid(id) || id === session.draft.pending?.id || !experimentComparisonCurrent(session, project, comparison))
    throw new Error("Compare the exact current experiment input before a new registration.");
  const saved: unknown = JSON.parse(session.custody.expectedRaw);
  if (!record(saved) || !sameExperimentValue(decodeExperimentDraft(saved.draft, session.draft.scope, false), session.draft))
    throw new Error("Confirm the current experiment input before preparing its operation.");
  const epoch = session.epoch, snapshot = structuredClone(session.draft), sourceProject = structuredClone(fullProject(project));
  const frozenComparison = structuredClone(comparison.result), sentFields: Record<string, ExperimentField> = {};
  for (const key of fieldNames) sentFields[key] = snapshot.fields[key] ? { ...snapshot.fields[key] }
    : { raw: experimentFieldBase(snapshot, key), base: experimentFieldBase(snapshot, key), sequence: 0 };
  if (sentFields.ackRights.raw !== "true" || sentFields.ackLimit.raw !== "true" || !rationale(sentFields.reviewerReason.raw))
    throw new Error("Confirm source rights, technical limits and the explicit owner review rationale before registration.");
  const bundle = await revalidateEvaluatorExperiment(sourceProject, frozenComparison);
  if (session.epoch !== epoch || !experimentComparisonCurrent(session, project, comparison))
    throw new Error("Experiment input changed during revalidation. No operation was sent; later input remains retained.");
  const change: EvaluatorChange = { type: "register_creative_evaluator", bundle };
  assertCommandSource(sourceProject, change, snapshot.scope); assertSentFields(change, frozenComparison, sentFields);
  const pending: ExperimentPending = { id, source: sourceOf(sourceProject), sourceProjectJson: JSON.stringify(sourceProject),
    changeJson: JSON.stringify(change), comparisonJson: JSON.stringify(frozenComparison), sentFields, outcome: "UNKNOWN", diagnostic: null };
  const draft = decodeExperimentDraft({ ...snapshot, pending }, snapshot.scope, false);
  session.liveAttempt = { id, changeJson: pending.changeJson, dispatched: false,
    prior: snapshot.pending ? { key: experimentCopyKey(snapshot.scope, session.custody.writer), raw: session.custody.expectedRaw } : null };
  return { id, change, draft };
}
export function guardExperimentSubmission(session: ExperimentSession, project: Project, id: string, change: EvaluatorChange,
  storage: ExperimentStore, pointers: ExperimentPointers): void {
  const pending = session.draft.pending, live = session.liveAttempt, c = session.custody;
  if (!pending || pending.id !== id || !live || live.id !== id || live.dispatched
    || pending.changeJson !== JSON.stringify(change) || live.changeJson !== pending.changeJson)
    throw new Error("Only the newly prepared original experiment may dispatch once. UNKNOWN cannot replay.");
  if (session.failedSource || session.recoveryObservation || c.attempt || c.expectedRaw === null || c.expectedPointer !== c.writer)
    throw new Error("Confirm the experiment recovery bytes and pointer before dispatch.");
  if (!sameExperimentValue(fullProject(project), fullProject(JSON.parse(pending.sourceProjectJson))))
    throw new Error("The complete experiment source Project changed before dispatch.");
  assertCommandSource(project, change, session.draft.scope); assertCurrentCustody(session, storage, pointers);
  const saved: unknown = JSON.parse(c.expectedRaw);
  if (!record(saved) || !sameExperimentValue(decodeExperimentDraft(saved.draft, session.draft.scope, false), session.draft))
    throw new Error("The confirmed experiment copy does not contain the exact pending request.");
  if (live.prior && (live.prior.key === experimentCopyKey(session.draft.scope, c.writer) || storage.getItem(live.prior.key) !== live.prior.raw))
    throw new Error("Preserve the original UNKNOWN separately before dispatching a new experiment.");
  assertCurrentCustody(session, storage, pointers); live.dispatched = true;
}
export async function acknowledgeExperimentSubmission(session: ExperimentSession, id: string, change: EvaluatorChange, value: Project): Promise<void> {
  const pending = session.draft.pending, live = session.liveAttempt;
  if (!pending || pending.id !== id || pending.changeJson !== JSON.stringify(change) || !live || live.id !== id
    || !live.dispatched || live.changeJson !== pending.changeJson) throw new Error("Only the original experiment callback can acknowledge this pending input.");
  const pendingJson = JSON.stringify(pending);
  await assertEvaluatorChangeReceipt(change, fullProject(JSON.parse(pending.sourceProjectJson)), value);
  if (session.liveAttempt !== live || JSON.stringify(session.draft.pending) !== pendingJson)
    throw new Error("The original experiment attempt changed while its receipt was checked.");
  // Keep the exact candidate, scene choices and declarations visible. No old
  // snapshot is restored over input authored while the response was pending.
  session.draft = { ...session.draft, pending: null }; session.liveAttempt = null; session.epoch++;
}
export function retainExperimentFailure(draft: ExperimentDraft, id: string, diagnostic: string, notSent = false): ExperimentDraft {
  return draft.pending?.id === id ? { ...draft, pending: { ...draft.pending, diagnostic, outcome: notSent ? "NOT_SENT" : "UNKNOWN" } } : draft;
}


// Exact custody/readback mechanisms retained from the existing evaluator author session.
const custody = (pointer: string | null): ExperimentCustody => ({ writer: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer, attempt: null });
function assertExperimentRecoverySource(scope: string, observation: ExperimentRecoveryObservation,
  storage: ExperimentStore, pointers: ExperimentPointers, own?: { writer: string; raw: string }): void {
  if (!observation.pointerObserved) throw new Error("Read the original experiment recovery pointer before making a new copy.");
  if (observation.pointer !== null) {
    if (!observation.rawObserved || observation.raw === null) throw new Error("The original experiment recovery copy is still missing.");
    if (storage.getItem(experimentCopyKey(scope, observation.pointer)) !== observation.raw)
      throw new Error("The original experiment recovery bytes changed. The first observation remains retained.");
    decodeExperimentCopy(observation.raw, scope, observation.pointer);
  }
  const pointer = pointers.getItem(experimentPointerKey(scope));
  if (pointer !== observation.pointer && !(own && pointer === own.writer && storage.getItem(experimentCopyKey(scope, own.writer)) === own.raw))
    throw new Error("The original experiment recovery pointer changed. No different copy was adopted.");
  if (observation.pointer !== null && storage.getItem(experimentCopyKey(scope, observation.pointer)) !== observation.raw)
    throw new Error("The original experiment recovery bytes changed during confirmation. The first observation remains retained.");
}
function readExperimentRecoverySource(scope: string, observation: ExperimentRecoveryObservation,
  storage: ExperimentStore, pointers: ExperimentPointers): ExperimentDraft | null {
  const pointer = pointers.getItem(experimentPointerKey(scope));
  if (observation.pointerObserved && pointer !== observation.pointer)
    throw new Error("The original experiment recovery pointer changed. No different copy was adopted.");
  if (!observation.pointerObserved) { observation.pointer = pointer; observation.pointerObserved = true; }
  if (pointer === null) { assertExperimentRecoverySource(scope, observation, storage, pointers); return null; }
  const raw = storage.getItem(experimentCopyKey(scope, pointer));
  if (observation.rawObserved && raw !== observation.raw)
    throw new Error("The original experiment recovery bytes changed. The first observation remains retained.");
  if (!observation.rawObserved) { observation.raw = raw; observation.rawObserved = true; }
  if (raw === null) throw new Error("The original experiment recovery copy is still missing.");
  const draft = decodeExperimentCopy(raw, scope, pointer).draft;
  assertExperimentRecoverySource(scope, observation, storage, pointers);
  return draft;
}

export function forkExperimentWriter(session: ExperimentSession): void {
  if (session.custody.attempt) throw new Error("Verify the incomplete experiment recovery write before changing writer.");
  session.custody = custody(session.custody.expectedPointer);
}
function assertCurrentCustody(session: ExperimentSession, storage: ExperimentStore, pointers: ExperimentPointers): void {
  const c = session.custody, scope = session.draft.scope;
  if (pointers.getItem(experimentPointerKey(scope)) !== c.expectedPointer) throw new Error("Another window changed this tab's experiment writer pointer.");
  if (storage.getItem(experimentCopyKey(scope, c.writer)) !== c.expectedRaw) throw new Error("The editable experiment copy changed outside this writer.");
}
export function persistExperimentSession(session: ExperimentSession, storage: ExperimentStore, pointers: ExperimentPointers): void {
  if (session.failedSource) throw new Error("Read the exact failed source copy before enabling experiment save.");
  if (session.custody.attempt) throw new Error("An experiment recovery write is unconfirmed. Retry its exact readback first.");
  const original = session.recoveryObservation, c = session.custody, scope = session.draft.scope;
  if (session.liveAttempt?.prior && session.liveAttempt.prior.key === experimentCopyKey(scope, c.writer)
    && session.draft.pending?.id === session.liveAttempt.id)
    throw new Error("Fork the experiment writer before replacing a retained UNKNOWN with a separately prepared operation.");
  if (original) assertExperimentRecoverySource(scope, original, storage, pointers,
    c.expectedRaw === null ? undefined : { writer: c.writer, raw: c.expectedRaw });
  assertCurrentCustody(session, storage, pointers);
  const clean = decodeExperimentDraft(session.draft, scope, false);
  const raw = JSON.stringify({ schema: "motionwright.evaluator-experiment-copy/1", writer: c.writer, draft: clean } satisfies ExperimentCopy);
  if (byteLength(raw) > MAX_BYTES) throw new Error("The experiment recovery exceeds 8 MiB. Export the author input before reducing retained records.");
  const key = experimentCopyKey(scope, c.writer), pointerKey = experimentPointerKey(scope);
  c.attempt = { raw, previousRaw: c.expectedRaw, previousPointer: c.expectedPointer };
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The experiment copy did not survive write/readback.");
  if (pointers.getItem(pointerKey) !== c.expectedPointer) throw new Error("Experiment pointer changed while saving its copy.");
  pointers.setItem(pointerKey, c.writer);
  if (pointers.getItem(pointerKey) !== c.writer) throw new Error("The experiment writer pointer did not survive readback.");
  if (storage.getItem(key) !== raw) throw new Error("The experiment data changed during the final pointer readback.");
  if (original) assertExperimentRecoverySource(scope, original, storage, pointers, { writer: c.writer, raw });
  c.expectedRaw = raw; c.expectedPointer = c.writer; c.attempt = null;
  session.recoveryObservation = null;
}
/** Retry only the observed failed source and this writer's exact attempted bytes.
 * Reading an earlier UNKNOWN cannot accidentally become permission to save. */
export function retryExperimentCustody(session: ExperimentSession, storage: ExperimentStore, pointers: ExperimentPointers): void {
  const scope = session.draft.scope;
  if (session.failedSource) {
    const failed = session.failedSource;
    const recovered = readExperimentRecoverySource(scope, failed, storage, pointers);
    let adopted = session.draft;
    if (recovered) {
      if (session.draft.pending && recovered.pending && session.draft.pending.id !== recovered.pending.id)
        throw new Error("Two distinct UNKNOWN experiment operations require separate review; neither was discarded.");
      const fields = { ...recovered.fields }, later = Object.entries(session.draft.fields);
      let sequence = Math.max(recovered.sequence, session.draft.sequence);
      for (const [key, value] of later) {
        if (!Number.isSafeInteger(++sequence)) throw new Error("Experiment input sequence exhausted. Both inputs remain retained.");
        fields[key] = { ...value, sequence };
      }
      adopted = { ...recovered, sequence, fields, pending: session.draft.pending ?? recovered.pending };
    }
    // Do not mutate the current buffer until the entire original observation
    // has survived a second read. A later field remains intent even if blank.
    assertExperimentRecoverySource(scope, failed, storage, pointers);
    session.draft = adopted; session.failedSource = null; session.custody = custody(failed.pointer); session.liveAttempt = null; session.epoch++;
  }
  const c = session.custody;
  if (c.attempt) {
    const attempt = c.attempt, raw = storage.getItem(experimentCopyKey(scope, c.writer)), pointer = pointers.getItem(experimentPointerKey(scope));
    if ((raw !== attempt.raw && raw !== attempt.previousRaw) || (pointer !== c.writer && pointer !== attempt.previousPointer))
      throw new Error("The incomplete experiment write no longer matches its exact bytes/pointer.");
    if (storage.getItem(experimentCopyKey(scope, c.writer)) !== raw || pointers.getItem(experimentPointerKey(scope)) !== pointer)
      throw new Error("Experiment recovery changed while its incomplete write was inspected.");
    c.expectedRaw = raw; c.expectedPointer = pointer; c.attempt = null;
  }
  persistExperimentSession(session, storage, pointers);
}



function sameObservedProject(left: Project, right: Project): boolean {
  return projectOwner(left) === projectOwner(right) && left.revision === right.revision && sameExperimentValue(left, right);
}
export function experimentReviewCurrent(session: ExperimentSession, project: Project, review: ExperimentOutcomeReview | null): boolean {
  return !!review && session.draft.pending !== null && review.scope === session.draft.scope && review.epoch === session.epoch
    && review.pendingJson === JSON.stringify(session.draft.pending) && sameObservedProject(project, review.observed);
}
export async function inspectExperimentOutcome(session: ExperimentSession, project: Project, transport: ExperimentOutcomeTransport): Promise<ExperimentOutcomeReview> {
  const pending = session.draft.pending;
  if (!pending || !matchingProject(session.draft.scope, project)) throw new Error("No matching unconfirmed experiment operation exists.");
  const selected = structuredClone(fullProject(project)), pendingJson = JSON.stringify(pending), epoch = session.epoch;
  const observed = structuredClone(fullProject(await transport.readProject(selected.id)));
  if (!sameObservedProject(observed, selected)) throw new Error("Reload the current saved project before reviewing this experiment outcome.");
  if (!Number.isSafeInteger(observed.revision) || observed.revision < pending.source.base_revision
    || observed.revision - pending.source.base_revision > 2048) throw new Error("The complete experiment history range is unavailable.");
  const events: ProjectEvent[] = []; let cursor = pending.source.base_revision;
  while (cursor < observed.revision) {
    const page = await transport.readHistory(observed, cursor, 256);
    if (!Array.isArray(page) || !page.length || page.length > 256) throw new Error("The experiment history is incomplete.");
    for (const event of page) {
      if (!event || event.revision !== cursor + 1 || event.revision > observed.revision || !event.change
        || typeof event.change.type !== "string" || typeof event.created_at !== "string" || !event.created_at)
        throw new Error("The experiment history is not a contiguous saved revision sequence.");
      events.push(structuredClone(event)); cursor = event.revision;
    }
  }
  const after = fullProject(await transport.readProject(selected.id));
  if (!sameObservedProject(after, observed) || session.epoch !== epoch || JSON.stringify(session.draft.pending) !== pendingJson)
    throw new Error("The experiment buffer or saved project changed during outcome review.");
  return { scope: session.draft.scope, pendingJson, epoch, observed, events,
    matchingRevisions: events.filter(event => sameExperimentValue(event.change, JSON.parse(pending.changeJson))).map(event => event.revision) };
}

export function originalExperimentRecoveryCopy(scope: string, observation: ExperimentRecoveryObservation | null): { key: string; raw: string; copy: ExperimentCopy | null } | null {
  if (!observation?.pointerObserved || observation.pointer === null || !observation.rawObserved || observation.raw === null) return null;
  let copy: ExperimentCopy | null = null;
  try { copy = decodeExperimentCopy(observation.raw, scope, observation.pointer); } catch { /* Keep original unreadable bytes downloadable. */ }
  return { key: experimentCopyKey(scope, observation.pointer), raw: observation.raw, copy };
}
export function experimentCopies(storage: ExperimentStore, scope: string,
  observation?: ExperimentRecoveryObservation | null): { key: string; raw: string; copy: ExperimentCopy | null }[] {
  const prefix = EXPERIMENT_COPY_PREFIX + encodeURIComponent(scope) + ":", result: { key: string; raw: string; copy: ExperimentCopy | null }[] = [];
  if (storage.length > 10000) throw new Error("Experiment copy enumeration is too large; export current input and inspect storage.");
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i); if (!key?.startsWith(prefix)) continue;
    if (result.length >= 128) throw new Error("More than 128 experiment recovery copies exist. No copy was silently deleted.");
    const raw = storage.getItem(key);
    // Pin a source encountered during listing before another index/read can
    // fail, including an observed absence. Retry cannot silently substitute
    // newer bytes under this writer after a listed copy was missing.
    if (observation?.pointerObserved && observation.pointer !== null && !observation.rawObserved && key === experimentCopyKey(scope, observation.pointer)) {
      observation.raw = raw; observation.rawObserved = true;
    }
    if (raw === null) continue;
    let copy: ExperimentCopy | null = null;
    try { copy = decodeExperimentCopy(raw, scope, key.slice(prefix.length)); } catch { /* opaque copy remains visible */ }
    result.push({ key, raw, copy });
  }
  return result;
}
export function restoreExperimentCopy(session: ExperimentSession, raw: string, writer: string, storage: ExperimentStore, pointers: ExperimentPointers): void {
  if (session.draft.pending) throw new Error("An UNKNOWN operation cannot be replaced by a different copy.");
  const restored = decodeExperimentCopy(raw, session.draft.scope, writer).draft;
  if (!experimentHasInput(restored) && !restored.pending) throw new Error("This is a clean checkpoint with no pending author input.");
  // Verify both the current buffer and selected source before any replacement.
  persistExperimentSession(session, storage, pointers);
  const scope = session.draft.scope, key = experimentCopyKey(scope, writer);
  if (storage.getItem(key) !== raw) throw new Error("The selected experiment copy changed.");
  forkExperimentWriter(session);
  // The prior buffer was verified under its original key. Keep the explicitly
  // selected candidate in memory if retention fails, especially its UNKNOWN;
  // rollback to a clean buffer would let Retry erase that known operation.
  session.draft = restored; session.liveAttempt = null; session.epoch++;
  persistExperimentSession(session, storage, pointers);
}
export function exportExperimentDraft(draft: ExperimentDraft): string {
  return JSON.stringify({ schema: "motionwright.evaluator-experiment-author-file/1", draft: decodeExperimentDraft(draft, draft.scope, false) }, null, 2);
}
export function importExperimentDraft(raw: string, scope: string): ExperimentDraft {
  if (byteLength(raw) > MAX_BYTES) throw new Error("The experiment author file exceeds 8 MiB.");
  const file: unknown = JSON.parse(raw);
  if (!record(file) || !keys(file, ["schema", "draft"]) || file.schema !== "motionwright.evaluator-experiment-author-file/1") throw new Error("Unsupported experiment author file.");
  return decodeExperimentDraft(file.draft, scope);
}
export function adoptExperimentFile(session: ExperimentSession, draft: ExperimentDraft, storage: ExperimentStore, pointers: ExperimentPointers): void {
  if (session.draft.pending) throw new Error("An UNKNOWN operation cannot be replaced by an author file.");
  const decoded = decodeExperimentDraft(draft, session.draft.scope);
  persistExperimentSession(session, storage, pointers);
  forkExperimentWriter(session);
  session.draft = decoded; session.liveAttempt = null; session.epoch++;
  persistExperimentSession(session, storage, pointers);
}
