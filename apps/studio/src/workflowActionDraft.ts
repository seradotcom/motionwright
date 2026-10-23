import type { EffectGrantReceipt, Project, WorkflowAction } from "./types";

// Native workflow DTO contract shared with the desktop command boundary.
// Branch ownership belongs only to the author copy, never to this closed wire.
export type WorkflowJson = null | boolean | number | string | WorkflowJson[] | { [key: string]: WorkflowJson };
export type WorkflowArguments = { [key: string]: WorkflowJson };
export type WorkflowActionPayload = {
  project_id: string; generation: string; revision: number; request_id: string;
  action: WorkflowAction; args: WorkflowArguments;
};
export type WorkflowReceiptIdentity = {
  id: string; project_id: string; generation: string; revision: number; request_id: string;
  request_sha256: string; command: string; stage: string;
};
export type WorkflowReceiptObservation = { state: "not_checked" | "not_observed" | "unavailable" }
  | { state: "recorded"; identity: WorkflowReceiptIdentity };
export type WorkflowActionResponse = {
  schema: "motionwright.workflow-action-response/1"; command: string; request_id: string;
  broker_request_id: string; replayed: boolean; authority: WorkflowJson; result: WorkflowJson;
  receipt: { state: "recorded"; identity: WorkflowReceiptIdentity };
};
export const WORKFLOW_ERROR_CODES = ["Unsupported", "Unavailable", "PermissionDenied", "ConsentRequired", "PolicyDenied",
  "NotFound", "AmbiguousTarget", "StaleReference", "BackendFailed", "Timeout", "InvalidArgument", "PluginProtocolError",
  "SandboxDenied", "Conflict", "Cancelled", "Internal", "ProtocolMismatch", "ResourceExhausted"] as const;
export type WorkflowErrorCode = typeof WORKFLOW_ERROR_CODES[number];
export type WorkflowActionFailure = {
  schema: "motionwright.workflow-action-failure/1"; command: string; request_id: string | null;
  code: WorkflowErrorCode; outcome_known: boolean; message: string; receipt: WorkflowReceiptObservation;
};
const commands: Record<WorkflowAction, string> = {
  record_start: "workflow.record.start", record_stop: "workflow.record.stop", compile: "workflow.compile",
  suggestion_compile: "workflow.suggestion.compile", proposal_plan: "workflow.proposal.plan",
  proposal_accept: "workflow.proposal.accept", verify: "workflow.verify", replay: "workflow.replay", promote: "workflow.promote",
};
export const workflowCommand = (action: WorkflowAction): string => commands[action];
const isWorkflowAction = (value: unknown): value is WorkflowAction => typeof value === "string" && Object.hasOwn(commands, value);
const record = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const bytes = (value: string) => new TextEncoder().encode(value).length;
const MAX_BYTES = 8 * 1024 * 1024;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
function uuid(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value) || value === "00000000-0000-0000-0000-000000000000") throw new Error("The workflow owner identity is invalid.");
  return value;
}
function shape(value: unknown, names: string[]): Record<string, unknown> {
  if (!record(value) || Object.keys(value).length !== names.length || names.some(key => !Object.hasOwn(value, key)))
    throw new Error("The workflow value has an unsupported shape.");
  return value;
}
function bounded(value: unknown, limit: number, empty = false): string {
  if (typeof value !== "string" || (!empty && !value.length) || bytes(value) > limit) throw new Error("The workflow value is not supported text.");
  return value;
}
export function readWorkflowRequestId(value: unknown): string {
  const result = bounded(value, 128);
  if (result !== result.trim() || /[\u0000-\u001f\u007f-\u009f]/u.test(result)) throw new Error("Keep the exact original workflow request identity, without whitespace or controls.");
  return result;
}
function jsonValue(value: unknown, depth = 0, budget = { nodes: 0 }): WorkflowJson {
  if (++budget.nodes > 65536 || depth > 64) throw new Error("The workflow JSON exceeds its supported structure.");
  if (value === null || typeof value === "boolean" || typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value) && (!Number.isInteger(value) || Number.isSafeInteger(value))) return value;
  if (Array.isArray(value)) return value.map(item => jsonValue(item, depth + 1, budget));
  if (record(value) && (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null))
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, jsonValue(item, depth + 1, budget)]));
  throw new Error("Workflow arguments must be exact finite JSON values; incomplete input is retained as text.");
}
export function readWorkflowArguments(value: unknown): WorkflowArguments {
  if (!record(value)) throw new Error("Workflow inputs must be a JSON object.");
  const parsed = jsonValue(value);
  if (!record(parsed) || bytes(JSON.stringify(parsed)) > 1024 * 1024) throw new Error("Workflow arguments exceed the bounded request size.");
  // The guard is structural; each value was already recursively decoded above.
  return Object.fromEntries(Object.entries(parsed).map(([key, item]) => [key, jsonValue(item)]));
}
export function readWorkflowPayload(value: unknown): WorkflowActionPayload {
  const v = shape(value, ["project_id", "generation", "revision", "request_id", "action", "args"]);
  if (!integer(v.revision) || !isWorkflowAction(v.action)) throw new Error("The workflow source or action is invalid.");
  return { project_id: uuid(v.project_id), generation: uuid(v.generation), revision: v.revision,
    request_id: readWorkflowRequestId(v.request_id), action: v.action, args: readWorkflowArguments(v.args) };
}
function readIdentity(value: unknown): WorkflowReceiptIdentity {
  const v = shape(value, ["id", "project_id", "generation", "revision", "request_id", "request_sha256", "command", "stage"]);
  if (!integer(v.revision) || typeof v.request_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(v.request_sha256)) throw new Error("The workflow receipt identity is incomplete.");
  return { id: uuid(v.id), project_id: uuid(v.project_id), generation: uuid(v.generation), revision: v.revision,
    request_id: readWorkflowRequestId(v.request_id), request_sha256: v.request_sha256,
    command: bounded(v.command, 256), stage: bounded(v.stage, 128) };
}
function readObservation(value: unknown): WorkflowReceiptObservation {
  if (!record(value)) throw new Error("The workflow receipt observation is missing.");
  if (value.state === "recorded") { const v = shape(value, ["state", "identity"]); return { state: "recorded", identity: readIdentity(v.identity) }; }
  const v = shape(value, ["state"]);
  if (v.state !== "not_checked" && v.state !== "not_observed" && v.state !== "unavailable") throw new Error("The workflow receipt observation is unsupported.");
  return { state: v.state };
}
function readResponse(value: unknown): WorkflowActionResponse {
  const v = shape(value, ["schema", "command", "request_id", "broker_request_id", "replayed", "authority", "result", "receipt"]);
  const receipt = readObservation(v.receipt);
  // The native workflow response contract requires
  // completed for a success. Failure observations retain their opaque stage.
  if (v.schema !== "motionwright.workflow-action-response/1" || typeof v.replayed !== "boolean" || receipt.state !== "recorded"
    || receipt.identity.stage !== "completed") throw new Error("The workflow response is not a recorded completed success.");
  return { schema: v.schema, command: bounded(v.command, 256), request_id: readWorkflowRequestId(v.request_id),
    broker_request_id: bounded(v.broker_request_id, 1024), replayed: v.replayed, authority: jsonValue(v.authority), result: jsonValue(v.result), receipt };
}
export function readWorkflowFailure(value: unknown): WorkflowActionFailure | null {
  try {
    const v = shape(value, ["schema", "command", "request_id", "code", "outcome_known", "message", "receipt"]);
    const code = WORKFLOW_ERROR_CODES.find(code => code === v.code);
    if (v.schema !== "motionwright.workflow-action-failure/1" || !code || typeof v.outcome_known !== "boolean") return null;
    return { schema: v.schema, command: bounded(v.command, 256), request_id: v.request_id === null ? null : readWorkflowRequestId(v.request_id),
      code, outcome_known: v.outcome_known, message: bounded(v.message, 65536, true), receipt: readObservation(v.receipt) };
  } catch { return null; }
}
function identityMatches(identity: WorkflowReceiptIdentity, request: WorkflowActionPayload): boolean {
  return identity.project_id === request.project_id && identity.generation === request.generation && identity.revision === request.revision
    && identity.request_id === request.request_id && identity.command === workflowCommand(request.action);
}
function failureMatches(failure: WorkflowActionFailure, request: WorkflowActionPayload): boolean {
  return failure.request_id === request.request_id && failure.command === workflowCommand(request.action)
    && (failure.receipt.state !== "recorded" || identityMatches(failure.receipt.identity, request));
}
function failureSettlesOriginal(failure: WorkflowActionFailure | null, request: WorkflowActionPayload): failure is WorkflowActionFailure {
  // outcome_known describes this invocation. Only its matching terminal failure
  // receipt can settle the original action; success needs its original response.
  return failure !== null && failure.outcome_known === true && failureMatches(failure, request)
    && failure.receipt.state === "recorded" && failure.receipt.identity.stage === "failed_known";
}
export function assertWorkflowResponse(value: unknown, request: WorkflowActionPayload): WorkflowActionResponse {
  const response = readResponse(value);
  if (response.request_id !== request.request_id || response.command !== workflowCommand(request.action) || !identityMatches(response.receipt.identity, request))
    throw new Error("The workflow response does not identify this original source and action. Its outcome remains UNKNOWN.");
  // The native adapter binds request_sha256 to the original arguments. Its
  // canonical fingerprint is opaque here and is never emulated in JavaScript.
  return response;
}
export function workflowGrantToken(receipt: EffectGrantReceipt, request: WorkflowActionPayload): string {
  if (receipt.effect !== "workflow_mutation" || receipt.project_id !== request.project_id || receipt.generation !== request.generation
    || receipt.revision !== request.revision || receipt.one_time !== true || !Number.isFinite(receipt.expires_in_seconds) || receipt.expires_in_seconds <= 0)
    throw new Error("The workflow grant does not match the original source.");
  return uuid(receipt.token);
}
export function workflowDiagnostic(value: unknown): string {
  if (value instanceof Error) return value.message;
  if (typeof value === "string") return value;
  try { return JSON.stringify(value) ?? "No workflow response was available."; }
  catch { return "The workflow response could not be decoded; the original action remains unconfirmed."; }
}
/** Only code before invoke constructs this local proof. A received DTO cannot
 * masquerade as a not-dispatched Error instance. No effect grant is retained. */
export class WorkflowNotDispatchedError extends Error {
  readonly requestId: string | null;
  constructor(requestId: string | null, message: string) { super(message); this.name = "WorkflowNotDispatchedError"; this.requestId = requestId; }
}

export const WORKFLOW_COPY_PREFIX = "motionwright.workflow-copy.v1:";
export const WORKFLOW_POINTER_PREFIX = "motionwright.workflow-writer.v1:";
export type WorkflowStore = Pick<Storage, "getItem" | "setItem" | "key" | "length">;
export type WorkflowPointers = Pick<Storage, "getItem" | "setItem">;
export const workflowScope = (project: Pick<Project, "id" | "generation" | "active_branch">): string => JSON.stringify([project.id, project.generation, project.active_branch]);
export const workflowStamp = (project: Pick<Project, "id" | "generation" | "revision">): string => JSON.stringify([project.id, project.generation, project.revision]);
export const workflowCopyKey = (scope: string, writer: string): string => WORKFLOW_COPY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const workflowPointerKey = (scope: string): string => WORKFLOW_POINTER_PREFIX + encodeURIComponent(scope);
export interface WorkflowField { raw: string; sequence: number }
export interface WorkflowPending {
  requestJson: string; sentFields: Record<string, WorkflowField>;
  outcome: "UNKNOWN" | "NOT_SENT" | "FAILED_KNOWN"; diagnostic: string | null; failure: WorkflowActionFailure | null;
}
export type WorkflowRetainedOutcome = { requestJson: string; kind: "acknowledged"; response: WorkflowActionResponse }
  | { requestJson: string; kind: "failed_known"; failure: WorkflowActionFailure }
  | { requestJson: string; kind: "not_dispatched"; diagnostic: string };
export interface WorkflowDraft {
  schema: "motionwright.workflow-author-draft/1"; scope: string; sequence: number;
  fields: Record<string, WorkflowField>; pending: WorkflowPending | null; lastOutcome: WorkflowRetainedOutcome | null;
}
export interface WorkflowCustody {
  writer: string; expectedRaw: string | null; expectedPointer: string | null;
  attempt: { raw: string; previousRaw: string | null; previousPointer: string | null } | null;
}
export interface WorkflowSession {
  draft: WorkflowDraft; custody: WorkflowCustody; failedSource: { pointer: string | null; raw: string | null } | null;
}
export interface WorkflowSubmission { scope: string; request: WorkflowActionPayload; requestJson: string; sentFields: Record<string, WorkflowField> }
const submissions = new WeakMap<WorkflowSubmission, { session: WorkflowSession; sealed: string; dispatched: boolean; settled: boolean }>();
const fixedFields = ["record.name", "record.intent", "record.capture", "record.hint", "compile.name", "compile.description", "compile.traces", "candidate.selected"];
export function workflowCandidateField(id: string, field: "inputs" | "slug"): string {
  return "candidate:" + encodeURIComponent(bounded(id, 1024)) + ":" + field;
}
function fieldAllowed(key: string): boolean {
  if (fixedFields.includes(key)) return true;
  const match = /^candidate:([^:]+):(inputs|slug)$/.exec(key);
  try { return !!match && encodeURIComponent(bounded(decodeURIComponent(match[1]), 1024)) === match[1]; } catch { return false; }
}
function checkScope(scope: string): string[] {
  const value: unknown = JSON.parse(scope);
  if (!Array.isArray(value) || value.length !== 3) throw new Error("The workflow copy owner is unreadable.");
  return value.map(uuid);
}
function checkRequestScope(request: WorkflowActionPayload, scope: string): void {
  const owner = checkScope(scope);
  if (request.project_id !== owner[0] || request.generation !== owner[1]) throw new Error("The retained workflow action belongs to another owner.");
}
function readFields(value: unknown, sequence: number): Record<string, WorkflowField> {
  if (!record(value) || Object.keys(value).length > 2048) throw new Error("The retained workflow fields are unreadable.");
  return Object.fromEntries(Object.entries(value).map(([key, raw]) => {
    const field = shape(raw, ["raw", "sequence"]);
    if (!fieldAllowed(key) || !integer(field.sequence) || field.sequence > sequence) throw new Error("A retained workflow field is invalid.");
    const text = bounded(field.raw, MAX_BYTES, true);
    if (key === "record.capture" && text !== "true" && text !== "false") throw new Error("The retained capture option is unreadable.");
    if (key === "record.hint" && text !== "active" && text !== "inactive") throw new Error("The retained recording hint is unreadable.");
    if (key === "compile.traces") {
      const traces: unknown = JSON.parse(text);
      if (!Array.isArray(traces) || traces.length > 8 || traces.some(item => typeof item !== "string" || !item.length || bytes(item) > 1024)
        || new Set(traces).size !== traces.length) throw new Error("The retained trace selection is unreadable.");
    }
    return [key, { raw: text, sequence: field.sequence }];
  }));
}
export function decodeWorkflowDraft(value: unknown, scope: string, recovered = true): WorkflowDraft {
  checkScope(scope);
  const v = shape(value, ["schema", "scope", "sequence", "fields", "pending", "lastOutcome"]);
  if (v.schema !== "motionwright.workflow-author-draft/1" || v.scope !== scope || !integer(v.sequence)) throw new Error("The workflow draft has a foreign owner or schema.");
  const fields = readFields(v.fields, v.sequence);
  let pending: WorkflowPending | null = null, lastOutcome: WorkflowRetainedOutcome | null = null;
  if (v.pending !== null) {
    const p = shape(v.pending, ["requestJson", "sentFields", "outcome", "diagnostic", "failure"]);
    const requestJson = bounded(p.requestJson, MAX_BYTES), request = readWorkflowPayload(JSON.parse(requestJson)); checkRequestScope(request, scope);
    if (p.outcome !== "UNKNOWN" && p.outcome !== "NOT_SENT" && p.outcome !== "FAILED_KNOWN") throw new Error("The workflow pending outcome is unsupported.");
    const failure = p.failure === null ? null : readWorkflowFailure(p.failure);
    if (p.failure !== null && (!failure || !failureMatches(failure, request))) throw new Error("The retained failure does not identify the original workflow request.");
    if (p.outcome === "FAILED_KNOWN" && !failure) throw new Error("The retained known failure is incomplete.");
    const known = p.outcome === "NOT_SENT" ? failure === null
      : p.outcome === "FAILED_KNOWN" && failureSettlesOriginal(failure, request);
    pending = { requestJson, sentFields: readFields(p.sentFields, v.sequence), outcome: !recovered && known ? p.outcome : "UNKNOWN",
      diagnostic: p.diagnostic === null ? null : bounded(p.diagnostic, MAX_BYTES, true), failure };
  }
  if (v.lastOutcome !== null) {
    if (!record(v.lastOutcome)) throw new Error("The retained workflow response is unreadable.");
    const kind = v.lastOutcome.kind;
    const out = shape(v.lastOutcome, kind === "acknowledged" ? ["requestJson", "kind", "response"] : kind === "failed_known" ? ["requestJson", "kind", "failure"] : ["requestJson", "kind", "diagnostic"]);
    const requestJson = bounded(out.requestJson, MAX_BYTES), request = readWorkflowPayload(JSON.parse(requestJson)); checkRequestScope(request, scope);
    if (kind === "acknowledged") lastOutcome = { requestJson, kind, response: assertWorkflowResponse(out.response, request) };
    else if (kind === "failed_known") {
      const failure = readWorkflowFailure(out.failure);
      if (!failureSettlesOriginal(failure, request)) throw new Error("The historical workflow failure does not settle its original action.");
      lastOutcome = { requestJson, kind, failure };
    } else if (kind === "not_dispatched") lastOutcome = { requestJson, kind, diagnostic: bounded(out.diagnostic, MAX_BYTES, true) };
    else throw new Error("The historical workflow outcome is unsupported.");
  }
  return { schema: v.schema, scope, sequence: v.sequence, fields, pending, lastOutcome };
}
const newDraft = (project: Project): WorkflowDraft => ({ schema: "motionwright.workflow-author-draft/1", scope: workflowScope(project), sequence: 0, fields: {}, pending: null, lastOutcome: null });
const custody = (pointer: string | null): WorkflowCustody => ({ writer: crypto.randomUUID(), expectedRaw: null, expectedPointer: pointer, attempt: null });
function decodeCopy(raw: string, scope: string, writer: string): WorkflowDraft {
  if (bytes(raw) > MAX_BYTES) throw new Error("The workflow copy exceeds its recovery budget. Its original bytes remain available.");
  const value = shape(JSON.parse(raw), ["schema", "writer", "draft"]);
  if (value.schema !== "motionwright.workflow-author-copy/1" || value.writer !== uuid(writer)) throw new Error("The workflow copy writer is inconsistent.");
  return decodeWorkflowDraft(value.draft, scope);
}
export function openWorkflowSession(project: Project, storage: WorkflowStore, pointers: WorkflowPointers): WorkflowSession {
  const scope = workflowScope(project); checkScope(scope);
  const session: WorkflowSession = { draft: newDraft(project), custody: custody(null), failedSource: null };
  let pointer: string | null = null, raw: string | null = null;
  try {
    pointer = pointers.getItem(workflowPointerKey(scope));
    if (pointer !== null) { raw = storage.getItem(workflowCopyKey(scope, uuid(pointer))); if (raw === null) throw new Error("The original copy is missing."); session.draft = decodeCopy(raw, scope, pointer); }
  } catch { session.failedSource = { pointer, raw }; }
  // A duplicated tab may inherit a pointer, but never inherits a writer.
  session.custody = custody(pointer);
  return session;
}
export const workflowRaw = (draft: WorkflowDraft, key: string, fallback = ""): string => draft.fields[key]?.raw ?? fallback;
export function editWorkflowField(session: WorkflowSession, key: string, raw: string): void {
  if (!fieldAllowed(key) || typeof raw !== "string") throw new Error("The workflow input key is unsupported.");
  const sequence = session.draft.sequence + 1;
  if (!Number.isSafeInteger(sequence)) throw new Error("The workflow input sequence is exhausted.");
  session.draft = { ...session.draft, sequence, fields: { ...session.draft.fields, [key]: { raw, sequence } } };
}
export function requireWorkflowCustody(session: WorkflowSession, storage: WorkflowStore, pointers: WorkflowPointers): void {
  if (session.failedSource || session.custody.attempt) throw new Error("Confirm the original workflow recovery copy before taking another action.");
  const c = session.custody;
  if (pointers.getItem(workflowPointerKey(session.draft.scope)) !== c.expectedPointer || storage.getItem(workflowCopyKey(session.draft.scope, c.writer)) !== c.expectedRaw)
    throw new Error("The workflow writer pointer or author copy changed outside this editor.");
}
export function persistWorkflowSession(session: WorkflowSession, storage: WorkflowStore, pointers: WorkflowPointers): void {
  requireWorkflowCustody(session, storage, pointers);
  const c = session.custody, scope = session.draft.scope, clean = decodeWorkflowDraft(session.draft, scope, false);
  const raw = JSON.stringify({ schema: "motionwright.workflow-author-copy/1", writer: c.writer, draft: clean });
  if (bytes(raw) > MAX_BYTES) throw new Error("The workflow author copy exceeds 8 MiB. Export its input before reducing it.");
  const key = workflowCopyKey(scope, c.writer), pointerKey = workflowPointerKey(scope);
  c.attempt = { raw, previousRaw: c.expectedRaw, previousPointer: c.expectedPointer };
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The workflow author copy did not survive write/readback.");
  if (pointers.getItem(pointerKey) !== c.expectedPointer) throw new Error("The workflow writer pointer changed during backup.");
  pointers.setItem(pointerKey, c.writer);
  if (pointers.getItem(pointerKey) !== c.writer) throw new Error("The workflow writer pointer did not survive readback.");
  if (storage.getItem(key) !== raw) throw new Error("The workflow data changed during final pointer readback.");
  c.expectedRaw = raw; c.expectedPointer = c.writer; c.attempt = null;
}
export function forkWorkflowWriter(session: WorkflowSession): void {
  if (session.failedSource || session.custody.attempt) throw new Error("Confirm the existing workflow copy before forking its writer.");
  session.custody = custody(session.custody.expectedPointer);
}
function mergeRecovered(current: WorkflowDraft, imported: WorkflowDraft): WorkflowDraft {
  if (current.scope !== imported.scope) throw new Error("The workflow copy belongs to another branch or owner.");
  if (current.pending && imported.pending && current.pending.requestJson !== imported.pending.requestJson)
    throw new Error("Two different unconfirmed workflow actions cannot replace each other. Export and keep both copies.");
  let sequence = Math.max(current.sequence, imported.sequence);
  const fields = { ...imported.fields };
  for (const [key, value] of Object.entries(current.fields)) fields[key] = { raw: value.raw, sequence: ++sequence };
  if (!Number.isSafeInteger(sequence)) throw new Error("The merged workflow input sequence is exhausted.");
  return { ...imported, sequence, fields, pending: current.pending ?? imported.pending, lastOutcome: current.lastOutcome ?? imported.lastOutcome };
}
export function retryWorkflowCustody(session: WorkflowSession, storage: WorkflowStore, pointers: WorkflowPointers): void {
  const scope = session.draft.scope;
  if (session.failedSource) {
    const failed = session.failedSource;
    if (pointers.getItem(workflowPointerKey(scope)) !== failed.pointer) throw new Error("The original failed workflow pointer changed. Inspect independent copies.");
    if (failed.pointer !== null) {
      const raw = storage.getItem(workflowCopyKey(scope, failed.pointer));
      if (raw === null) throw new Error("The original workflow recovery copy is still missing.");
      const imported = decodeCopy(raw, scope, failed.pointer);
      if (storage.getItem(workflowCopyKey(scope, failed.pointer)) !== raw || pointers.getItem(workflowPointerKey(scope)) !== failed.pointer)
        throw new Error("The original workflow copy changed during recovery.");
      session.draft = mergeRecovered(session.draft, imported);
    }
    session.failedSource = null; session.custody = custody(failed.pointer);
  }
  const c = session.custody;
  if (c.attempt) {
    const attempt = c.attempt, key = workflowCopyKey(scope, c.writer), pointerKey = workflowPointerKey(scope);
    const raw = storage.getItem(key), pointer = pointers.getItem(pointerKey);
    if ((raw !== attempt.raw && raw !== attempt.previousRaw) || (pointer !== c.writer && pointer !== attempt.previousPointer)
      || storage.getItem(key) !== raw || pointers.getItem(pointerKey) !== pointer) throw new Error("The incomplete workflow backup no longer has its exact observed bytes and pointer.");
    c.expectedRaw = raw; c.expectedPointer = pointer; c.attempt = null;
  }
  persistWorkflowSession(session, storage, pointers);
}
export function prepareWorkflowSubmission(session: WorkflowSession, project: Project, action: WorkflowAction, args: unknown,
  usedFields: string[], requestId: string = crypto.randomUUID()): WorkflowSubmission {
  if (session.draft.pending) throw new Error("The original workflow action is still retained. No new request identity or automatic retry is permitted.");
  if (session.failedSource || session.custody.attempt || session.draft.scope !== workflowScope(project)) throw new Error("The workflow source or recovery custody changed.");
  const request = readWorkflowPayload({ project_id: project.id, generation: project.generation, revision: project.revision, request_id: requestId, action, args });
  const sentFields = Object.fromEntries(usedFields.filter(key => !!session.draft.fields[key]).map(key => [key, { ...session.draft.fields[key] }]));
  const submission: WorkflowSubmission = { scope: session.draft.scope, request, requestJson: JSON.stringify(request), sentFields };
  session.draft = { ...session.draft, pending: { requestJson: submission.requestJson, sentFields: structuredClone(sentFields), outcome: "UNKNOWN", diagnostic: null, failure: null } };
  submissions.set(submission, { session, sealed: JSON.stringify(submission), dispatched: false, settled: false });
  return submission;
}
function liveSubmission(session: WorkflowSession, submission: WorkflowSubmission) {
  const proof = submissions.get(submission);
  if (!proof || proof.session !== session || proof.settled || proof.sealed !== JSON.stringify(submission)
    || session.draft.scope !== submission.scope || session.draft.pending?.requestJson !== submission.requestJson)
    throw new Error("Only this original live workflow invocation can settle its retained request.");
  return proof;
}
export function guardWorkflowDispatch(session: WorkflowSession, submission: WorkflowSubmission, current: Project,
  storage: WorkflowStore, pointers: WorkflowPointers): void {
  const proof = liveSubmission(session, submission);
  if (proof.dispatched) throw new Error("This original workflow invocation has already entered dispatch.");
  if (workflowScope(current) !== submission.scope || workflowStamp(current) !== JSON.stringify([submission.request.project_id, submission.request.generation, submission.request.revision]))
    throw new Error("The selected project, branch or revision changed while workflow permission was requested.");
  requireWorkflowCustody(session, storage, pointers);
  const saved = session.custody.expectedRaw;
  if (saved === null || JSON.stringify(decodeCopy(saved, session.draft.scope, session.custody.writer).pending) !== JSON.stringify(session.draft.pending))
    throw new Error("The exact original workflow intent is not confirmed in its author copy.");
  proof.dispatched = true;
}
export function acknowledgeWorkflowSubmission(session: WorkflowSession, submission: WorkflowSubmission, value: unknown): WorkflowActionResponse {
  const proof = liveSubmission(session, submission);
  if (!proof.dispatched) throw new Error("A workflow response cannot acknowledge an invocation that was never dispatched.");
  const response = assertWorkflowResponse(value, submission.request), fields = { ...session.draft.fields };
  if (submission.request.action === "compile") {
    for (const [key, sent] of Object.entries(submission.sentFields))
      if (fields[key]?.sequence === sent.sequence && fields[key]?.raw === sent.raw) delete fields[key];
  }
  let sequence = session.draft.sequence;
  if (submission.request.action === "record_start" || submission.request.action === "record_stop") {
    if (!Number.isSafeInteger(++sequence)) throw new Error("The workflow input sequence is exhausted; retain the original recording response for review.");
    fields["record.hint"] = { raw: submission.request.action === "record_start" ? "active" : "inactive", sequence };
  }
  session.draft = { ...session.draft, sequence, fields, pending: null, lastOutcome: { kind: "acknowledged", requestJson: submission.requestJson, response } };
  proof.settled = true;
  return response;
}
export function retainWorkflowFailure(session: WorkflowSession, submission: WorkflowSubmission, reason: unknown): void {
  const proof = liveSubmission(session, submission), pending = session.draft.pending!;
  const parsed = readWorkflowFailure(reason), failure = parsed && failureMatches(parsed, submission.request) ? parsed : null;
  const notSent = !proof.dispatched && reason instanceof WorkflowNotDispatchedError && reason.requestId === submission.request.request_id;
  const known = proof.dispatched && failureSettlesOriginal(failure, submission.request);
  session.draft = { ...session.draft, pending: { ...pending, outcome: notSent ? "NOT_SENT" : known ? "FAILED_KNOWN" : "UNKNOWN",
    diagnostic: failure?.message ?? workflowDiagnostic(reason), failure } };
  proof.settled = true;
}
export function closeKnownWorkflowAttempt(session: WorkflowSession, storage: WorkflowStore, pointers: WorkflowPointers): void {
  requireWorkflowCustody(session, storage, pointers);
  const pending = session.draft.pending;
  if (!pending || pending.outcome === "UNKNOWN"
    || (pending.outcome === "NOT_SENT" && pending.failure !== null)
    || (pending.outcome === "FAILED_KNOWN" && !failureSettlesOriginal(pending.failure, readWorkflowPayload(JSON.parse(pending.requestJson)))))
    throw new Error("UNKNOWN cannot be dismissed as a known result or replaced by another request identity.");
  const lastOutcome: WorkflowRetainedOutcome = pending.outcome === "NOT_SENT"
    ? { kind: "not_dispatched", requestJson: pending.requestJson, diagnostic: pending.diagnostic ?? "The local invocation was not dispatched." }
    : { kind: "failed_known", requestJson: pending.requestJson, failure: pending.failure! };
  session.draft = { ...session.draft, pending: null, lastOutcome };
}
export function exportWorkflowDraft(draft: WorkflowDraft): string {
  return JSON.stringify({ schema: "motionwright.workflow-author-input/1", draft: decodeWorkflowDraft(draft, draft.scope, false) }, null, 2);
}
export function importWorkflowDraft(raw: string, scope: string): WorkflowDraft {
  if (bytes(raw) > MAX_BYTES) throw new Error("The imported workflow copy exceeds 8 MiB. Its input is not truncated.");
  const value = shape(JSON.parse(raw), ["schema", "draft"]);
  if (value.schema !== "motionwright.workflow-author-input/1") throw new Error("Choose a workflow author-input export.");
  return decodeWorkflowDraft(value.draft, scope);
}
export function adoptWorkflowDraft(session: WorkflowSession, imported: WorkflowDraft, storage: WorkflowStore, pointers: WorkflowPointers): void {
  requireWorkflowCustody(session, storage, pointers);
  const merged = mergeRecovered(session.draft, decodeWorkflowDraft(imported, session.draft.scope));
  // Preserve the confirmed writer and adopt UNKNOWN before any fallible write.
  // Retry can then confirm this independent merge without losing the original action.
  forkWorkflowWriter(session); session.draft = merged;
  persistWorkflowSession(session, storage, pointers);
}
export interface WorkflowCopyEntry { key: string; raw: string; writer: string; draft: WorkflowDraft | null; error: string | null }
export function workflowCopies(scope: string, storage: WorkflowStore): { entries: WorkflowCopyEntry[]; limited: boolean; error: string | null } {
  const entries: WorkflowCopyEntry[] = []; let limited = false;
  try {
    const prefix = WORKFLOW_COPY_PREFIX + encodeURIComponent(scope) + ":", length = storage.length, seen = new Set<string>();
    if (!integer(length)) throw new Error("The workflow copy list could not be read.");
    limited = length > 4096;
    for (let index = 0; index < Math.min(length, 4096); index++) {
      const key = storage.key(index); if (key === null || seen.has(key)) { limited = true; continue; } seen.add(key);
      if (!key.startsWith(prefix)) continue;
      if (entries.length >= 128) { limited = true; break; }
      const raw = storage.getItem(key); if (raw === null) { limited = true; continue; }
      const writer = key.slice(prefix.length);
      try { entries.push({ key, raw, writer, draft: decodeCopy(raw, scope, writer), error: null }); }
      catch (error) { entries.push({ key, raw, writer, draft: null, error: workflowDiagnostic(error) }); }
    }
    if (storage.length !== length) limited = true;
    return { entries, limited, error: null };
  } catch (error) { return { entries, limited, error: workflowDiagnostic(error) }; }
}
export function restoreWorkflowCopy(session: WorkflowSession, copy: WorkflowCopyEntry, storage: WorkflowStore, pointers: WorkflowPointers): void {
  if (storage.getItem(copy.key) !== copy.raw) throw new Error("The chosen workflow copy changed during selection.");
  const draft = decodeCopy(copy.raw, session.draft.scope, copy.writer);
  if (copy.key !== workflowCopyKey(session.draft.scope, copy.writer) || storage.getItem(copy.key) !== copy.raw) throw new Error("The chosen workflow copy has a different owner or changed during readback.");
  adoptWorkflowDraft(session, draft, storage, pointers);
}
