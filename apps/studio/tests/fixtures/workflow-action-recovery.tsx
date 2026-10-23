import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import WorkflowWorkspace from "../../src/WorkflowWorkspace";
import { fixtureProject } from "../../src/fixture";
import { workflowCommand, workflowCopyKey, workflowPointerKey, workflowScope } from "../../src/workflowActionDraft";
import type { WorkflowActionPayload, WorkflowActionResponse } from "../../src/workflowActionDraft";
import type { Project, WorkflowOverview } from "../../src/types";
import "../../src/styles.css";

type TestRequest = WorkflowActionPayload & { effect_grant: string | null };
export interface WorkflowFixtureInvocation { index: number; request: TestRequest; settled: boolean }
export interface WorkflowFixtureGrant {
  index: number; request: { project_id: string; generation: string; revision: number; effect: string; subject: string };
  originalCopyAtGrant: string | null; settled: boolean;
}
export interface WorkflowFixtureHarness {
  project(): Project; requests(): WorkflowFixtureInvocation[]; grants(): WorkflowFixtureGrant[]; overviewReads(): number;
  setSource(field: "id" | "generation" | "active_branch" | "revision", value: string | number): Promise<void>;
  holdGrants(value: boolean): void; releaseGrant(index: number, rejection?: unknown): Promise<void>;
  syntheticResponse(index: number): WorkflowActionResponse;
  resolve(index: number, value?: unknown): Promise<void>; reject(index: number, value: unknown): Promise<void>;
  unmount(): Promise<void>; mount(): Promise<void>;
}
declare global { interface Window {
  __WORKFLOW_RECOVERY_FIXTURE__: WorkflowFixtureHarness; __WORKFLOW_RESTORE_STORAGE__?: () => void;
  __WORKFLOW_RELEASE_FILE__?: () => void;
} }
const id = (n: number) => "018f0000-0000-7000-8000-" + String(n).padStart(12, "0");
const stateKey = "TEST-ONLY.workflow-caller-synthetic-source.v1", rawState = sessionStorage.getItem(stateKey);
type FixtureState = { project: Project; requests: WorkflowFixtureInvocation[]; grants: WorkflowFixtureGrant[]; overviewReads: number };
const state: FixtureState = rawState ? JSON.parse(rawState) : { project: { ...structuredClone(fixtureProject), revision: 12 }, requests: [], grants: [], overviewReads: 0 };
let holdGrants = false, mounted = true;
const pending = new Map<number, { resolve(value: unknown): void; reject(reason: unknown): void }>();
const pendingGrants = new Map<number, { resolve(value: unknown): void; reject(reason: unknown): void }>();
const persist = () => sessionStorage.setItem(stateKey, JSON.stringify(state));
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
function overview(): WorkflowOverview {
  return { status: "available", reason: null, connection_identity: "SYNTHETIC-SCHEDULING-NOT-A-NATIVE-CONNECTION", authority: { synthetic_fixture: true },
    traces: { traces: [{ id: "trace-A", name: "Trace Alpha", status: "explicitly_synthetic" }, { id: "trace-B", name: "Trace Beta", status: "explicitly_synthetic" }] },
    candidates: { candidates: [{ id: "candidate-A", name: "Candidate Alpha", static_verified: false, successful_replays: 0 },
      { id: "candidate-B", name: "Candidate Beta", static_verified: false, successful_replays: 0 }] },
    patterns: { patterns: [] }, suggestions: { suggestions: [{ suggestion_id: "suggestion-A", suggested_name: "Suggestion Alpha" }] },
    proposals: { proposals: [{ id: "proposal-A", suggested_name: "Proposal Alpha" }] }, promotions: { promotions: [] } };
}
function grantValue(grant: WorkflowFixtureGrant) {
  return { token: id(700 + grant.index), effect: grant.request.effect, project_id: grant.request.project_id,
    generation: grant.request.generation, revision: grant.request.revision, expires_in_seconds: 30, one_time: true };
}
// A test-only interception of the existing Tauri call site. No production
// module imports this fixture or substitutes this boundary in browser mode.
Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {
  invoke: async (command: string, args: { request: unknown }) => {
    if (command === "workflow_overview") { state.overviewReads++; persist(); return overview(); }
    if (command === "issue_effect_grant") {
      const scope = workflowScope(state.project), writer = sessionStorage.getItem(workflowPointerKey(scope));
      const originalCopyAtGrant = writer ? localStorage.getItem(workflowCopyKey(scope, writer)) : null;
      const grant: WorkflowFixtureGrant = { index: state.grants.length + 1, request: structuredClone(args.request) as WorkflowFixtureGrant["request"], originalCopyAtGrant, settled: !holdGrants };
      state.grants.push(grant); persist();
      if (!holdGrants) return grantValue(grant);
      return new Promise((resolve, reject) => pendingGrants.set(grant.index, { resolve, reject }));
    }
    if (command === "workflow_action") {
      const invocation: WorkflowFixtureInvocation = { index: state.requests.length + 1, request: structuredClone(args.request) as TestRequest, settled: false };
      state.requests.push(invocation); persist();
      return new Promise((resolve, reject) => pending.set(invocation.index, { resolve, reject }));
    }
    throw new Error("Unexpected command at synthetic workflow boundary: " + command);
  },
} });
const host = document.getElementById("workflow-recovery-root"); if (!host) throw new Error("Missing fixture host.");
const root = createRoot(host);
function render() { persist(); flushSync(() => root.render(<StrictMode>{mounted ? <WorkflowWorkspace project={state.project} /> : null}</StrictMode>)); }
function invocation(index: number) {
  const request = state.requests.find(value => value.index === index); if (!request) throw new Error("No synthetic request at index " + index); return request;
}
function syntheticResponse(index: number): WorkflowActionResponse {
  const request = invocation(index).request;
  return { schema: "motionwright.workflow-action-response/1", command: workflowCommand(request.action), request_id: request.request_id,
    broker_request_id: "synthetic-broker-" + index, replayed: false, authority: { synthetic_fixture: true }, result: { synthetic_scheduling_only: true },
    receipt: { state: "recorded", identity: { id: id(800 + index), project_id: request.project_id, generation: request.generation, revision: request.revision,
      request_id: request.request_id, request_sha256: "a".repeat(64), command: workflowCommand(request.action), stage: "completed" } } };
}
window.__WORKFLOW_RECOVERY_FIXTURE__ = {
  project: () => structuredClone(state.project), requests: () => structuredClone(state.requests), grants: () => structuredClone(state.grants), overviewReads: () => state.overviewReads,
  async setSource(field, value) {
    if (field === "revision") { if (typeof value !== "number") throw new Error("Expected synthetic revision number."); state.project = { ...state.project, revision: value }; }
    else { if (typeof value !== "string") throw new Error("Expected synthetic identity."); state.project = { ...state.project, [field]: value }; }
    render(); await paint();
  },
  holdGrants: value => { holdGrants = value; },
  async releaseGrant(index, rejection) {
    const original = state.grants.find(grant => grant.index === index), reply = pendingGrants.get(index);
    if (!original || !reply) throw new Error("No pending synthetic grant " + index);
    pendingGrants.delete(index); original.settled = true; persist();
    if (rejection !== undefined) reply.reject(rejection); else reply.resolve(grantValue(original)); await paint();
  },
  syntheticResponse,
  async resolve(index, value) {
    const original = invocation(index), reply = pending.get(index); if (!reply) throw new Error("Original invocation is not live in this test process.");
    original.settled = true; pending.delete(index); persist(); reply.resolve(value ?? syntheticResponse(index)); await paint();
  },
  async reject(index, value) {
    const original = invocation(index), reply = pending.get(index); if (!reply) throw new Error("Original invocation is not live in this test process.");
    original.settled = true; pending.delete(index); persist(); reply.reject(value); await paint();
  },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
