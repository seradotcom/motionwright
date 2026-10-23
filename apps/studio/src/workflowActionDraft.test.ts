import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { acknowledgeWorkflowSubmission, adoptWorkflowDraft, assertWorkflowResponse, closeKnownWorkflowAttempt, decodeWorkflowDraft,
  editWorkflowField, exportWorkflowDraft, forkWorkflowWriter, guardWorkflowDispatch, importWorkflowDraft, openWorkflowSession,
  persistWorkflowSession, prepareWorkflowSubmission, readWorkflowArguments, readWorkflowFailure, readWorkflowPayload, readWorkflowRequestId,
  requireWorkflowCustody, restoreWorkflowCopy, retainWorkflowFailure, retryWorkflowCustody, WORKFLOW_ERROR_CODES, WorkflowNotDispatchedError,
  workflowCandidateField, workflowCommand, workflowCopies, workflowCopyKey, workflowDiagnostic, workflowGrantToken, workflowPointerKey,
  workflowRaw, workflowScope } from "./workflowActionDraft";
import type { WorkflowActionFailure, WorkflowActionResponse, WorkflowActionPayload, WorkflowSubmission } from "./workflowActionDraft";
import type { Project } from "./types";

const assert: {
  equal(actual: unknown, expected: unknown): void; notEqual(actual: unknown, expected: unknown): void;
  deepEqual(actual: unknown, expected: unknown): void; throws(action: () => unknown): void;
  ok(value: unknown): asserts value;
} = {
  equal: (actual, expected) => expect(actual).toBe(expected), notEqual: (actual, expected) => expect(actual).not.toBe(expected),
  deepEqual: (actual, expected) => expect(actual).toEqual(expected), throws: action => { expect(action).toThrow(); },
  ok: value => { expect(value).toBeTruthy(); },
};

// Pure custody/DTO oracles with explicit synthetic receipt values. No native
// adapter, canonical fingerprint, broker effect, storage receipt or SQLite is
// produced by these helpers.
const id = (n: number) => "018f0000-0000-7000-8000-" + String(n).padStart(12, "0");
class MemoryStorage {
  data = new Map<string, string>();
  silent = false; onRead: ((key: string) => void) | null = null;
  get length() { return this.data.size; }
  key(index: number) { return [...this.data.keys()][index] ?? null; }
  getItem(key: string) { this.onRead?.(key); return this.data.get(key) ?? null; }
  setItem(key: string, value: string) { if (!this.silent) this.data.set(key, value); }
}
function world() {
  const project = structuredClone(fixtureProject); project.revision = 12;
  const storage = new MemoryStorage(), pointers = new MemoryStorage();
  const session = openWorkflowSession(project, storage, pointers);
  return { project, storage, pointers, session };
}
function prepare(w: ReturnType<typeof world>, action: WorkflowActionPayload["action"] = "compile", args = { name: "Original candidate", trace_ids: ["trace-A"], description: "Original description" }) {
  editWorkflowField(w.session, "compile.name", "  Original candidate  ");
  editWorkflowField(w.session, "compile.description", "Original description");
  editWorkflowField(w.session, "compile.traces", '["trace-A"]');
  persistWorkflowSession(w.session, w.storage, w.pointers); forkWorkflowWriter(w.session);
  const submission = prepareWorkflowSubmission(w.session, w.project, action, args, ["compile.name", "compile.description", "compile.traces"], "caller-original-1");
  persistWorkflowSession(w.session, w.storage, w.pointers); return submission;
}
function sent(w: ReturnType<typeof world>) { const submission = prepare(w); guardWorkflowDispatch(w.session, submission, w.project, w.storage, w.pointers); return submission; }
function response(submission: WorkflowSubmission): WorkflowActionResponse {
  const request = submission.request;
  return { schema: "motionwright.workflow-action-response/1", command: workflowCommand(request.action), request_id: request.request_id,
    broker_request_id: "synthetic-broker-request-distinct-from-caller", replayed: false, authority: null, result: { synthetic_scheduling_only: true },
    receipt: { state: "recorded", identity: { id: id(900), project_id: request.project_id, generation: request.generation,
      revision: request.revision, request_id: request.request_id, request_sha256: "a".repeat(64), command: workflowCommand(request.action), stage: "completed" } } };
}
function failure(submission: WorkflowSubmission, known = false): WorkflowActionFailure {
  return { schema: "motionwright.workflow-action-failure/1", command: workflowCommand(submission.request.action), request_id: submission.request.request_id,
    code: "Timeout", outcome_known: known, message: "Original native response diagnostic", receipt: { state: "unavailable" } };
}
function terminalFailure(submission: WorkflowSubmission): WorkflowActionFailure {
  const receipt = response(submission).receipt; receipt.identity.stage = "failed_known";
  return { ...failure(submission, true), receipt };
}

describe("Original workflow caller custody — synthetic DTOs only", () => {
  it("keeps caller identity exact and rejects whitespace, controls and UTF-8 overflow", () => {
    assert.equal(readWorkflowRequestId("é".repeat(64)), "é".repeat(64));
    for (const value of ["", " id", "id ", "id\n", "id\u0085", "é".repeat(65), null, 1]) assert.throws(() => readWorkflowRequestId(value));
  });
  it("decodes the closed request without a branch or effect-grant field in its retained source", () => {
    const w = world(), submission = prepare(w), source = submission.request;
    assert.deepEqual(Object.keys(source), ["project_id", "generation", "revision", "request_id", "action", "args"]);
    assert.equal(source.request_id, "caller-original-1"); assert.deepEqual(readWorkflowPayload(JSON.parse(submission.requestJson)), source);
    for (const extra of [{ branch_id: w.project.active_branch }, { effect_grant: id(3) }, { hidden_retry: true }])
      assert.throws(() => readWorkflowPayload({ ...source, ...extra }));
    assert.throws(() => readWorkflowPayload({ ...source, revision: Number.MAX_SAFE_INTEGER + 1 }));
  });
  it("refuses lossy non-JSON arguments while leaving incomplete author input verbatim", () => {
    const w = world(), key = workflowCandidateField("candidate-A", "inputs"), raw = ' {"gain":1e';
    editWorkflowField(w.session, key, raw); persistWorkflowSession(w.session, w.storage, w.pointers);
    for (const value of [{ n: NaN }, { n: Infinity }, { n: undefined }, { n: 1n }, { date: new Date() }, [], { n: 9007199254740992 }])
      assert.throws(() => readWorkflowArguments(value));
    const recovered = openWorkflowSession(w.project, w.storage, w.pointers);
    assert.equal(workflowRaw(recovered.draft, key), raw);
    assert.deepEqual(readWorkflowArguments(JSON.parse('{"__proto__":{"kept":true},"value":0.25}')), JSON.parse('{"__proto__":{"kept":true},"value":0.25}'));
  });
  it("retains original action, source, args and raw field sequences before dispatch", () => {
    const w = world(), submission = prepare(w), raw = w.storage.getItem(workflowCopyKey(w.session.draft.scope, w.session.custody.writer))!;
    const saved = JSON.parse(raw);
    assert.equal(saved.draft.pending.outcome, "UNKNOWN"); assert.equal(saved.draft.pending.requestJson, submission.requestJson);
    assert.equal(saved.draft.fields["compile.name"].raw, "  Original candidate  ");
    assert.equal(submission.request.args.name, "Original candidate"); assert.equal(raw.includes("effect_grant"), false);
    assert.throws(() => prepareWorkflowSubmission(w.session, w.project, "verify", { candidate_id: "A" }, [], "new-id"));
    assert.equal(w.session.draft.pending!.requestJson, submission.requestJson);
  });
  it("cannot dispatch until the original pending copy, pointer and final readback are confirmed", () => {
    const w = world(); const submission = prepareWorkflowSubmission(w.session, w.project, "verify", { candidate_id: "A" }, [], "original");
    assert.throws(() => guardWorkflowDispatch(w.session, submission, w.project, w.storage, w.pointers));
    persistWorkflowSession(w.session, w.storage, w.pointers);
    guardWorkflowDispatch(w.session, submission, w.project, w.storage, w.pointers);
    assert.throws(() => guardWorkflowDispatch(w.session, submission, w.project, w.storage, w.pointers));
  });
  it("rejects selection changes after permission was requested without changing original identity", () => {
    for (const changed of ["id", "generation", "active_branch", "revision"] as const) {
      const w = world(), submission = prepare(w), current = structuredClone(w.project);
      if (changed === "revision") current.revision++; else current[changed] = id(901);
      assert.throws(() => guardWorkflowDispatch(w.session, submission, current, w.storage, w.pointers));
      retainWorkflowFailure(w.session, submission, new WorkflowNotDispatchedError(submission.request.request_id, "Source changed before invoke."));
      assert.equal(w.session.draft.pending!.outcome, "NOT_SENT"); assert.equal(w.session.draft.pending!.requestJson, submission.requestJson);
    }
  });
  it("does not let a saved or reconstructed invocation token acknowledge an original action", () => {
    const w = world(), submission = sent(w), reconstructed = structuredClone(submission);
    assert.throws(() => acknowledgeWorkflowSubmission(w.session, reconstructed, response(submission)));
    const recovered = openWorkflowSession(w.project, w.storage, w.pointers);
    assert.throws(() => acknowledgeWorkflowSubmission(recovered, submission, response(submission)));
    assert.throws(() => retainWorkflowFailure(recovered, submission, failure(submission, true)));
    assert.equal(recovered.draft.pending!.outcome, "UNKNOWN");
  });
  it("binds success to caller request, command, project, generation and original revision", () => {
    const w = world(), submission = sent(w), good = response(submission);
    for (const field of ["request_id", "command", "project_id", "generation", "revision", "receipt_id", "digest", "schema", "extra"] as const) {
      const bad: Record<string, unknown> = structuredClone(good);
      const receipt = structuredClone(good.receipt);
      if (field === "request_id") bad.request_id = "another-caller";
      if (field === "command") bad.command = "workflow.promote";
      if (field === "project_id") receipt.identity.project_id = id(902);
      if (field === "generation") receipt.identity.generation = id(903);
      if (field === "revision") receipt.identity.revision++;
      if (field === "receipt_id") receipt.identity.id = "not-a-uuid";
      if (field === "digest") receipt.identity.request_sha256 = "not-a-native-fingerprint";
      if (field === "schema") bad.schema = "unversioned-response";
      if (field === "extra") bad.effect_grant = id(9);
      bad.receipt = receipt; assert.throws(() => assertWorkflowResponse(bad, submission.request));
    }
    const observed = assertWorkflowResponse(good, submission.request);
    assert.equal(observed.request_id, submission.request.request_id);
    assert.notEqual(observed.broker_request_id, observed.request_id);
    assert.equal(observed.receipt.identity.request_sha256, "a".repeat(64)); // Opaque synthetic fingerprint, not recomputed.
  });
  it("accepts the contract's arbitrary JSON authority value without widening the response shape", () => {
    const w = world(), submission = sent(w), good = response(submission);
    for (const authority of [null, "reported authority", ["one", 1], { source: "synthetic" }])
      assert.deepEqual(assertWorkflowResponse({ ...good, authority }, submission.request).authority, authority);
  });
  it("requires the native success stage completed while keeping failure stages opaque", () => {
    const w = world(), submission = sent(w);
    for (const stage of ["dispatched", "failed", "COMPLETED", "completed ", "future-stage"]) {
      const bad = response(submission); bad.receipt.identity.stage = stage;
      assert.throws(() => assertWorkflowResponse(bad, submission.request));
      const observedFailure = readWorkflowFailure({ ...failure(submission), receipt: bad.receipt });
      assert.ok(observedFailure?.receipt.state === "recorded");
      assert.equal(observedFailure.receipt.identity.stage, stage);
    }
    assert.equal(assertWorkflowResponse(response(submission), submission.request).receipt.identity.stage, "completed");
    assert.equal(w.session.draft.pending!.outcome, "UNKNOWN");
  });
  it("keeps UNKNOWN for malformed or foreign failures, even with a truthy outcome flag", () => {
    for (const bad of ["lost reply", { outcome_known: true }, { ...failure(sent(world()), true), code: "InventedKnownCode" }]) {
      const w = world(), submission = sent(w); retainWorkflowFailure(w.session, submission, bad);
      assert.equal(w.session.draft.pending!.outcome, "UNKNOWN"); assert.throws(() => closeKnownWorkflowAttempt(w.session, w.storage, w.pointers));
    }
    const w = world(), submission = sent(w); const foreign = failure(submission, true); foreign.request_id = "foreign";
    retainWorkflowFailure(w.session, submission, foreign); assert.equal(w.session.draft.pending!.outcome, "UNKNOWN");
  });
  it("decodes only the pinned SDK error codes, with a boolean outcome flag", () => {
    const w = world(), submission = sent(w);
    for (const code of WORKFLOW_ERROR_CODES) assert.equal(readWorkflowFailure({ ...failure(submission), code })!.code, code);
    assert.equal(readWorkflowFailure({ ...failure(submission), outcome_known: "true" }), null);
    assert.equal(readWorkflowFailure({ ...failure(submission), request_id: null })!.request_id, null);
    assert.equal(readWorkflowFailure({ ...failure(submission), guessed_resolution: "safe" }), null);
  });
  it("does not turn absent, unavailable or unchecked receipts into permission to send", () => {
    for (const state of ["not_observed", "unavailable", "not_checked"] as const) {
      const w = world(), submission = sent(w);
      retainWorkflowFailure(w.session, submission, { ...failure(submission), receipt: { state } });
      persistWorkflowSession(w.session, w.storage, w.pointers);
      const recovered = openWorkflowSession(w.project, w.storage, w.pointers);
      assert.equal(recovered.draft.pending!.outcome, "UNKNOWN");
      assert.equal(recovered.draft.pending!.failure!.receipt.state, state);
      assert.throws(() => prepareWorkflowSubmission(recovered, w.project, "verify", { candidate_id: "A" }, []));
      assert.equal(recovered.draft.pending!.requestJson, submission.requestJson);
    }
  });
  it("keeps a mismatched recorded failure UNKNOWN instead of borrowing another receipt", () => {
    const w = world(), submission = sent(w), receipt = response(submission).receipt;
    receipt.identity.generation = id(904);
    retainWorkflowFailure(w.session, submission, { ...failure(submission, true), receipt });
    assert.equal(w.session.draft.pending!.outcome, "UNKNOWN"); assert.equal(w.session.draft.pending!.failure, null);
  });
  it("preserves a known failure of the original live invocation until an explicit local acknowledgment", () => {
    const w = world(), submission = sent(w), original = terminalFailure(submission);
    retainWorkflowFailure(w.session, submission, original); persistWorkflowSession(w.session, w.storage, w.pointers);
    assert.equal(w.session.draft.pending!.outcome, "FAILED_KNOWN");
    assert.throws(() => prepareWorkflowSubmission(w.session, w.project, "verify", { candidate_id: "A" }, []));
    closeKnownWorkflowAttempt(w.session, w.storage, w.pointers); persistWorkflowSession(w.session, w.storage, w.pointers);
    assert.equal(w.session.draft.pending, null); assert.equal(w.session.draft.lastOutcome!.kind, "failed_known");
    assert.equal(workflowRaw(w.session.draft, "compile.name"), "  Original candidate  ");
    assert.equal(w.session.draft.lastOutcome!.requestJson, submission.requestJson);
  });
  it("reload cannot upgrade a prior unconfirmed action using a retained known-failure flag", () => {
    const w = world(), submission = sent(w); retainWorkflowFailure(w.session, submission, terminalFailure(submission));
    persistWorkflowSession(w.session, w.storage, w.pointers);
    const recovered = openWorkflowSession(w.project, w.storage, w.pointers);
    assert.equal(recovered.draft.pending!.outcome, "UNKNOWN"); assert.equal(recovered.draft.pending!.failure!.outcome_known, true);
    assert.throws(() => closeKnownWorkflowAttempt(recovered, w.storage, w.pointers));
  });
  it("only a local pre-invoke Error with this request id can establish NOT_SENT", () => {
    const w = world(), submission = prepare(w);
    retainWorkflowFailure(w.session, submission, new WorkflowNotDispatchedError(submission.request.request_id, "Grant unavailable before invoke."));
    assert.equal(w.session.draft.pending!.outcome, "NOT_SENT");
    for (const reason of [new WorkflowNotDispatchedError("foreign", "No send"), { name: "WorkflowNotDispatchedError", requestId: "caller-original-1", message: "No send" }]) {
      const another = world(), pending = prepare(another); retainWorkflowFailure(another.session, pending, reason);
      assert.equal(another.session.draft.pending!.outcome, "UNKNOWN");
    }
    const dispatched = world(), invoked = sent(dispatched);
    retainWorkflowFailure(dispatched.session, invoked, new WorkflowNotDispatchedError(invoked.request.request_id, "Too late to claim no dispatch."));
    assert.equal(dispatched.session.draft.pending!.outcome, "UNKNOWN");
  });
  it("keeps exact diagnostics instead of coercing structured native failures to Object text", () => {
    const w = world(), submission = sent(w), original = failure(submission); original.message = "  Native UNKNOWN diagnostic\n  ";
    retainWorkflowFailure(w.session, submission, original); persistWorkflowSession(w.session, w.storage, w.pointers);
    const recovered = openWorkflowSession(w.project, w.storage, w.pointers);
    assert.equal(recovered.draft.pending!.diagnostic, original.message);
    assert.equal(recovered.draft.pending!.failure!.code, "Timeout");
    assert.equal(workflowDiagnostic({ detail: "Unrecognized response" }), '{"detail":"Unrecognized response"}');
  });
  it("acknowledges only the original Promise and preserves later edits field by field", () => {
    const w = world(), submission = sent(w);
    editWorkflowField(w.session, "compile.name", "  Later candidate  ");
    editWorkflowField(w.session, workflowCandidateField("candidate-B", "inputs"), '{"incomplete":1e');
    persistWorkflowSession(w.session, w.storage, w.pointers);
    const reply = acknowledgeWorkflowSubmission(w.session, submission, response(submission));
    assert.equal(reply.request_id, "caller-original-1"); assert.equal(w.session.draft.pending, null);
    assert.equal(workflowRaw(w.session.draft, "compile.name"), "  Later candidate  ");
    assert.equal(workflowRaw(w.session.draft, "compile.description"), ""); assert.equal(workflowRaw(w.session.draft, "compile.traces", "[]"), "[]");
    assert.equal(workflowRaw(w.session.draft, workflowCandidateField("candidate-B", "inputs")), '{"incomplete":1e');
    assert.equal(submission.request.args.name, "Original candidate");
    assert.throws(() => acknowledgeWorkflowSubmission(w.session, submission, response(submission)));
  });
  it("retains a malformed success as UNKNOWN with all author input", () => {
    const w = world(), submission = sent(w), original = structuredClone(w.session.draft.fields);
    const malformed = { ...response(submission), receipt: { state: "not_observed" } };
    assert.throws(() => acknowledgeWorkflowSubmission(w.session, submission, malformed));
    retainWorkflowFailure(w.session, submission, new Error("Malformed success; original result unknown."));
    assert.equal(w.session.draft.pending!.outcome, "UNKNOWN"); assert.deepEqual(w.session.draft.fields, original);
  });
  it("uses a new writer on reload or duplicated tabs and never overwrites the inherited copy", () => {
    const w = world(), submission = prepare(w), oldKey = workflowCopyKey(w.session.draft.scope, w.session.custody.writer), oldRaw = w.storage.getItem(oldKey);
    const duplicatedPointers = new MemoryStorage(); duplicatedPointers.data = new Map(w.pointers.data);
    const next = openWorkflowSession(w.project, w.storage, duplicatedPointers);
    assert.notEqual(next.custody.writer, w.session.custody.writer); assert.equal(next.draft.pending!.requestJson, submission.requestJson);
    editWorkflowField(next, "compile.name", "Other tab input"); persistWorkflowSession(next, w.storage, duplicatedPointers);
    assert.equal(w.storage.getItem(oldKey), oldRaw); assert.equal(workflowRaw(w.session.draft, "compile.name"), "  Original candidate  ");
  });
  it("fails closed on a missing or corrupt pointed copy and retains independent raw bytes", () => {
    for (const raw of [null, '{"truncated":']) {
      const w = world(), scope = workflowScope(w.project), writer = id(905); w.pointers.setItem(workflowPointerKey(scope), writer);
      if (raw !== null) w.storage.setItem(workflowCopyKey(scope, writer), raw);
      const recovered = openWorkflowSession(w.project, w.storage, w.pointers);
      assert.ok(recovered.failedSource); assert.equal(recovered.failedSource.raw, raw);
      assert.throws(() => prepareWorkflowSubmission(recovered, w.project, "verify", { candidate_id: "A" }, []));
      if (raw !== null) assert.equal(workflowCopies(scope, w.storage).entries[0].raw, raw);
    }
  });
  it("detects silent data writes and repairs only the original attempted copy without another identity", () => {
    const w = world(); const submission = prepareWorkflowSubmission(w.session, w.project, "verify", { candidate_id: "A" }, [], "original");
    w.storage.silent = true; assert.throws(() => persistWorkflowSession(w.session, w.storage, w.pointers));
    assert.throws(() => guardWorkflowDispatch(w.session, submission, w.project, w.storage, w.pointers));
    w.storage.silent = false; retryWorkflowCustody(w.session, w.storage, w.pointers);
    assert.equal(w.session.draft.pending!.requestJson, submission.requestJson);
    assert.equal(w.session.custody.attempt, null); assert.throws(() => prepareWorkflowSubmission(w.session, w.project, "verify", {}, []));
  });
  it("detects a silently dropped pointer and data corruption at the final readback", () => {
    const w = world(); prepare(w); forkWorkflowWriter(w.session); w.pointers.silent = true;
    assert.throws(() => persistWorkflowSession(w.session, w.storage, w.pointers));
    w.pointers.silent = false; retryWorkflowCustody(w.session, w.storage, w.pointers);
    const another = world(), submission = prepare(another); let reads = 0;
    another.pointers.onRead = () => { if (++reads === 3) another.storage.data.set(workflowCopyKey(another.session.draft.scope, another.session.custody.writer), "corrupt-after-pointer"); };
    assert.throws(() => persistWorkflowSession(another.session, another.storage, another.pointers));
    assert.throws(() => guardWorkflowDispatch(another.session, submission, another.project, another.storage, another.pointers));
  });
  it("does not overwrite a changed copy or pointer and keeps later author text exportable", () => {
    const w = world(), submission = prepare(w), key = workflowCopyKey(w.session.draft.scope, w.session.custody.writer);
    w.storage.data.set(key, "other-writer-bytes"); editWorkflowField(w.session, "compile.description", "Later unsaved input");
    assert.throws(() => persistWorkflowSession(w.session, w.storage, w.pointers)); assert.equal(w.storage.getItem(key), "other-writer-bytes");
    const exported = JSON.parse(exportWorkflowDraft(w.session.draft));
    assert.equal(exported.draft.pending.requestJson, submission.requestJson); assert.equal(exported.draft.fields["compile.description"].raw, "Later unsaved input");
    w.pointers.data.set(workflowPointerKey(w.session.draft.scope), id(906)); assert.throws(() => requireWorkflowCustody(w.session, w.storage, w.pointers));
  });
  it("stores incomplete replay JSON and slug drafts separately for every candidate", () => {
    const w = world();
    for (const candidate of ["candidate-A", "candidate:two/three"]) {
      editWorkflowField(w.session, workflowCandidateField(candidate, "inputs"), '  {"candidate":"' + candidate + '", "n":1e');
      editWorkflowField(w.session, workflowCandidateField(candidate, "slug"), "  " + candidate + " /  ");
    }
    persistWorkflowSession(w.session, w.storage, w.pointers); const next = openWorkflowSession(w.project, w.storage, w.pointers);
    assert.deepEqual(next.draft.fields, w.session.draft.fields);
    const foreign = { ...w.project, active_branch: id(907) }; assert.deepEqual(openWorkflowSession(foreign, w.storage, w.pointers).draft.fields, {});
  });
  it("merges imported UNKNOWN without dropping later local fields or executing a request", () => {
    const source = world(), submission = prepare(source); const exported = exportWorkflowDraft(source.session.draft);
    const destination = world(); editWorkflowField(destination.session, "compile.name", "Later local input");
    const imported = importWorkflowDraft(exported, destination.session.draft.scope);
    adoptWorkflowDraft(destination.session, imported, destination.storage, destination.pointers);
    assert.equal(destination.session.draft.pending!.requestJson, submission.requestJson); assert.equal(destination.session.draft.pending!.outcome, "UNKNOWN");
    assert.equal(workflowRaw(destination.session.draft, "compile.name"), "Later local input");
    assert.throws(() => acknowledgeWorkflowSubmission(destination.session, submission, response(submission)));
    assert.throws(() => importWorkflowDraft(exported, workflowScope({ ...destination.project, generation: id(908) })));
  });
  it("refuses importing a different original UNKNOWN over the current one", () => {
    const w = world(), submission = prepare(w), incoming = structuredClone(w.session.draft);
    const request = JSON.parse(incoming.pending!.requestJson); request.request_id = "other-original"; incoming.pending!.requestJson = JSON.stringify(request);
    const before = exportWorkflowDraft(w.session.draft);
    assert.throws(() => adoptWorkflowDraft(w.session, incoming, w.storage, w.pointers));
    assert.equal(exportWorkflowDraft(w.session.draft), before); assert.equal(w.session.draft.pending!.requestJson, submission.requestJson);
  });
  it("validates selected copy bytes again before restoring and refuses a changed source", () => {
    const w = world(); prepare(w); const entries = workflowCopies(w.session.draft.scope, w.storage).entries;
    const copy = entries.find(entry => entry.draft?.pending)!;
    w.storage.data.set(copy.key, "changed-after-selection");
    assert.throws(() => restoreWorkflowCopy(w.session, copy, w.storage, w.pointers)); assert.equal(w.storage.getItem(copy.key), "changed-after-selection");
  });
  it("rejects extra draft fields and forged native grant data rather than retaining executable authority", () => {
    const w = world(); prepare(w);
    assert.throws(() => decodeWorkflowDraft({ ...w.session.draft, effect_grant: id(909) }, w.session.draft.scope));
    const altered = structuredClone(w.session.draft), request = JSON.parse(altered.pending!.requestJson); request.effect_grant = id(909);
    altered.pending!.requestJson = JSON.stringify(request); assert.throws(() => decodeWorkflowDraft(altered, altered.scope));
  });
  it("checks the one-time grant source independently of its opaque token", () => {
    const w = world(), submission = prepare(w), request = submission.request;
    const grant = { token: id(910), effect: "workflow_mutation" as const, project_id: request.project_id, generation: request.generation,
      revision: request.revision, expires_in_seconds: 30, one_time: true };
    assert.equal(workflowGrantToken(grant, request), id(910));
    for (const changed of [{ ...grant, generation: id(911) }, { ...grant, revision: request.revision + 1 }, { ...grant, one_time: false }, { ...grant, token: "bad-token" }])
      assert.throws(() => workflowGrantToken(changed, request));
  });
  it("shows a retained prior success as history while a different live action remains UNKNOWN", () => {
    const w = world(), original = sent(w); acknowledgeWorkflowSubmission(w.session, original, response(original));
    persistWorkflowSession(w.session, w.storage, w.pointers);
    const next = prepareWorkflowSubmission(w.session, w.project, "verify", { candidate_id: "A" }, [], "second-original");
    persistWorkflowSession(w.session, w.storage, w.pointers); const reopened = openWorkflowSession(w.project, w.storage, w.pointers);
    assert.equal(reopened.draft.lastOutcome!.kind, "acknowledged"); assert.equal(reopened.draft.pending!.requestJson, next.requestJson);
    assert.equal(reopened.draft.pending!.outcome, "UNKNOWN"); assert.throws(() => closeKnownWorkflowAttempt(reopened, w.storage, w.pointers));
  });
  it("rejects sequence exhaustion without dropping input", () => {
    const w = world(); editWorkflowField(w.session, "compile.name", "Preserve me"); w.session.draft.sequence = Number.MAX_SAFE_INTEGER;
    assert.throws(() => editWorkflowField(w.session, "compile.name", "Replacement")); assert.equal(workflowRaw(w.session.draft, "compile.name"), "Preserve me");
  });
});

describe("Imported workflow UNKNOWN custody", () => {
  for (const mode of ["silent", "throw", "write-then-throw"] as const) {
    it(`keeps the imported original and later text when the first adoption write is ${mode}`, () => {
      const source = world(), submission = sent(source);
      retainWorkflowFailure(source.session, submission, failure(submission));
      persistWorkflowSession(source.session, source.storage, source.pointers);
      const exported = exportWorkflowDraft(source.session.draft);
      const destination = world(), candidateField = workflowCandidateField("candidate-A", "inputs");
      editWorkflowField(destination.session, "compile.name", "Later local input");
      editWorkflowField(destination.session, candidateField, ' {"gain":1e');
      persistWorkflowSession(destination.session, destination.storage, destination.pointers);
      const previousWriter = destination.session.custody.writer;
      const previousKey = workflowCopyKey(destination.session.draft.scope, previousWriter);
      const previousRaw = destination.storage.getItem(previousKey);
      editWorkflowField(destination.session, "compile.name", "Later local input after the confirmed copy");
      const original = importWorkflowDraft(exported, destination.session.draft.scope);
      const put = destination.storage.setItem.bind(destination.storage);
      let writes = 0;
      destination.storage.setItem = (key, raw) => {
        writes++;
        if (writes === 1) {
          if (mode === "write-then-throw") put(key, raw);
          if (mode !== "silent") throw new Error("Injected first adoption write failure");
          return;
        }
        put(key, raw);
      };
      expect(() => adoptWorkflowDraft(destination.session, original, destination.storage, destination.pointers)).toThrow();
      expect(writes).toBe(1);
      destination.storage.setItem = put;
      expect(destination.session.draft.pending?.requestJson).toBe(submission.requestJson);
      expect(destination.storage.getItem(previousKey)).toBe(previousRaw);
      expect(workflowRaw(destination.session.draft, "compile.name")).toBe("Later local input after the confirmed copy");
      retryWorkflowCustody(destination.session, destination.storage, destination.pointers);
      expect(destination.session.draft.pending?.requestJson).toBe(submission.requestJson);
      expect(destination.session.draft.pending?.outcome).toBe("UNKNOWN");
      expect(destination.session.draft.pending?.sentFields).toEqual(source.session.draft.pending!.sentFields);
      expect(destination.storage.getItem(previousKey)).toBe(previousRaw);
      expect(destination.session.custody.writer).not.toBe(previousWriter);
      expect(workflowRaw(destination.session.draft, "compile.name")).toBe("Later local input after the confirmed copy");
      expect(workflowRaw(destination.session.draft, candidateField)).toBe(' {"gain":1e');
      expect(() => prepareWorkflowSubmission(destination.session, destination.project, "verify", { candidate_id: "A" }, [], "another-id")).toThrow();
      const reopened = openWorkflowSession(destination.project, destination.storage, destination.pointers);
      expect(reopened.draft.pending).toEqual(destination.session.draft.pending);
      expect(workflowRaw(reopened.draft, "compile.name")).toBe("Later local input after the confirmed copy");
      expect(destination.storage.getItem(previousKey)).toBe(previousRaw);
      expect(importWorkflowDraft(exported, source.session.draft.scope).pending?.requestJson).toBe(submission.requestJson);
    });
  }
});

describe("A known invocation rejection cannot settle an uncertain original workflow", () => {
  const observations = [
    ...(["not_checked", "not_observed", "unavailable"] as const).map(state => ({
      name: state,
      failure: (submission: WorkflowSubmission): WorkflowActionFailure => ({ ...failure(submission, true), receipt: { state } }),
    })),
    ...["dispatching", "outcome_unknown", "future-stage", "completed"].map(stage => ({
      name: stage,
      failure: (submission: WorkflowSubmission): WorkflowActionFailure => {
        const receipt = response(submission).receipt; receipt.identity.stage = stage;
        return { ...failure(submission, true), receipt };
      },
    })),
  ];
  for (const observation of observations) {
    it(`retains UNKNOWN after a known rejection with ${observation.name} and after reload`, () => {
      const w = world(), submission = sent(w), original = observation.failure(submission);
      retainWorkflowFailure(w.session, submission, original);
      expect(w.session.draft.pending?.outcome).toBe("UNKNOWN");
      expect(w.session.draft.pending?.requestJson).toBe(submission.requestJson);
      expect(w.session.draft.pending?.failure).toEqual(original);
      persistWorkflowSession(w.session, w.storage, w.pointers);
      const reopened = openWorkflowSession(w.project, w.storage, w.pointers);
      expect(reopened.draft.pending).toEqual(w.session.draft.pending);
      expect(() => closeKnownWorkflowAttempt(reopened, w.storage, w.pointers)).toThrow();
      expect(() => prepareWorkflowSubmission(reopened, w.project, "verify", { candidate_id: "A" }, [], "new-id")).toThrow();
    });
    it(`decodes a retained FAILED_KNOWN claim with ${observation.name} as UNKNOWN`, () => {
      const w = world(), submission = sent(w), original = observation.failure(submission);
      const claimed = structuredClone(w.session.draft);
      claimed.pending = { ...claimed.pending!, outcome: "FAILED_KNOWN", failure: original };
      const untouched = JSON.stringify(claimed);
      const decoded = decodeWorkflowDraft(claimed, claimed.scope, false);
      expect(decoded.pending?.outcome).toBe("UNKNOWN");
      expect(decoded.pending?.requestJson).toBe(submission.requestJson);
      expect(decoded.pending?.failure).toEqual(original);
      expect(JSON.stringify(claimed)).toBe(untouched);
      const imported = importWorkflowDraft(exportWorkflowDraft(claimed), claimed.scope);
      expect(imported.pending).toEqual(decoded.pending);
    });
    it(`refuses to close a FAILED_KNOWN claim with ${observation.name}`, () => {
      const w = world(), submission = sent(w), original = observation.failure(submission);
      w.session.draft.pending = { ...w.session.draft.pending!, outcome: "FAILED_KNOWN", failure: original };
      const before = JSON.stringify(w.session.draft);
      expect(() => closeKnownWorkflowAttempt(w.session, w.storage, w.pointers)).toThrow();
      expect(JSON.stringify(w.session.draft)).toBe(before);
      expect(() => prepareWorkflowSubmission(w.session, w.project, "verify", { candidate_id: "A" }, [], "new-id")).toThrow();
    });
  }
  it("does not trust a failed_known receipt when the invocation itself is uncertain", () => {
    const w = world(), submission = sent(w);
    retainWorkflowFailure(w.session, submission, { ...terminalFailure(submission), outcome_known: false });
    expect(w.session.draft.pending?.outcome).toBe("UNKNOWN");
    expect(() => closeKnownWorkflowAttempt(w.session, w.storage, w.pointers)).toThrow();
  });
  it("retains the original bytes and blocks a legacy retired failure with an uncertain receipt", () => {
    const w = world(), submission = sent(w), original = terminalFailure(submission);
    if (original.receipt.state !== "recorded") throw new Error("Expected the explicit receipt fixture");
    original.receipt.identity.stage = "outcome_unknown";
    const retired = { ...w.session.draft, pending: null, lastOutcome: { kind: "failed_known", requestJson: submission.requestJson, failure: original } };
    const raw = JSON.stringify({ schema: "motionwright.workflow-author-copy/1", writer: w.session.custody.writer, draft: retired });
    w.storage.setItem(workflowCopyKey(w.session.draft.scope, w.session.custody.writer), raw);
    const reopened = openWorkflowSession(w.project, w.storage, w.pointers);
    expect(reopened.failedSource?.raw).toBe(raw);
    expect(() => requireWorkflowCustody(reopened, w.storage, w.pointers)).toThrow();
    expect(() => prepareWorkflowSubmission(reopened, w.project, "verify", { candidate_id: "A" }, [], "new-id")).toThrow();
    expect(w.storage.getItem(workflowCopyKey(w.session.draft.scope, w.session.custody.writer))).toBe(raw);
  });
  it("does not decode or close NOT_SENT when a native failure contradicts that local claim", () => {
    const w = world(), submission = sent(w), original = failure(submission, true);
    w.session.draft.pending = { ...w.session.draft.pending!, outcome: "NOT_SENT", failure: original };
    const decoded = decodeWorkflowDraft(w.session.draft, w.session.draft.scope, false);
    expect(decoded.pending?.outcome).toBe("UNKNOWN");
    expect(decoded.pending?.requestJson).toBe(submission.requestJson);
    expect(() => closeKnownWorkflowAttempt(w.session, w.storage, w.pointers)).toThrow();
  });
});
