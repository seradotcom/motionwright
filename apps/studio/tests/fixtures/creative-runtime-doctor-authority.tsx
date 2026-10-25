import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import CreativeRuntimeDoctorPanel from "../../src/CreativeRuntimeDoctorPanel";
import { browserCreativeDoctor } from "../../src/creativeDoctor";
import { fixtureBootstrap } from "../../src/fixture";
import type { Bootstrap, CreativeRuntimeDoctorReport, ProductionRuntimeCompatibility, Project } from "../../src/types";

type Scope = {
  project_id: string; generation: string; revision: number; branch_id: string;
  sdk_pin: string; mode: Bootstrap["native_sdk"]["mode"];
  connection_identity: string | null; executable_sha256: string | null;
};
type RecordedRead = { id: number; command: string; request: {project_id: string; generation: string; revision: number}; scope: Scope; settled: boolean };
type PendingRead = { record: RecordedRead; project: Project; resolve(value: CreativeRuntimeDoctorReport): void; reject(reason: Error): void };
export interface DoctorAuthorityHarness {
  scope(): Scope;
  requests(): RecordedRead[];
  setScope(patch: Partial<Scope>): Promise<void>;
  resolve(id: number, marker: string, patch?: Partial<CreativeRuntimeDoctorReport>): Promise<void>;
  reject(id: number, message: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}
declare global { interface Window { __DOCTOR_AUTHORITY__: DoctorAuthorityHarness } }

const container = document.getElementById("doctor-root");
if (!container) throw new Error("Doctor fixture root is missing.");
const root = createRoot(container);
let project: Project = structuredClone(fixtureBootstrap.project);
let nativeSdk: Bootstrap["native_sdk"] = {...fixtureBootstrap.native_sdk, mode: "tauri"};
let runtime: ProductionRuntimeCompatibility = {
  status: "ready", reason: null, expected_version: "1.0.0",
  observed_version: "1.0.0", version_compatible: true,
  pinned_revision: nativeSdk.pinned_revision, connection_identity: "connection-a",
  executable_sha256: "a".repeat(64),
};
let mounted = true;
const records: RecordedRead[] = [];
const pending = new Map<number, PendingRead>();
const scope = (): Scope => ({
  project_id: project.id, generation: project.generation, revision: project.revision,
  branch_id: project.active_branch, sdk_pin: nativeSdk.pinned_revision, mode: nativeSdk.mode,
  connection_identity: runtime.connection_identity, executable_sha256: runtime.executable_sha256,
});
const render = () => flushSync(() => root.render(mounted
  ? <StrictMode><CreativeRuntimeDoctorPanel project={project} nativeSdk={nativeSdk} runtime={runtime}/></StrictMode>
  : null));
const settle = () => new Promise<void>((resolve) =>
  requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const take = (id: number) => {
  const value = pending.get(id);
  if (!value) throw new Error("No pending diagnosis " + id);
  pending.delete(id); value.record.settled = true; return value;
};
// These responses are explicit transport fixtures. The real component, API,
// and request ownership logic are unchanged; no native installation is claimed.
const response = (read: PendingRead, marker: string): CreativeRuntimeDoctorReport => ({
  ...browserCreativeDoctor(read.project, read.record.scope.sdk_pin),
  runtime_version_evidence: "cli_version_compatible",
  runtime_version_detail: marker,
  verdict: "preflight_only",
  broker_dispatch_performed: true,
  checks: [
    {id: "broker_renderer_registry", status: "validated", detail: "Synthetic authenticated registry observation."},
    {id: "native_runtime_mlt_version", status: "observed", detail: "Synthetic version observation; compatibility not assessed."},
  ],
});
const transport = window as unknown as Record<string, unknown>;
transport.__TAURI_INTERNALS__ = {
  invoke(command: string, args: {request: RecordedRead["request"]}) {
    if (command !== "creative_runtime_doctor" || !args?.request)
      return Promise.reject(new Error("Unexpected command in read-only fixture: " + command));
    const record: RecordedRead = {id: records.length + 1, command,
      request: structuredClone(args.request), scope: scope(), settled: false};
    records.push(record);
    return new Promise<CreativeRuntimeDoctorReport>((resolve, reject) => {
      pending.set(record.id, {record, project: structuredClone(project), resolve, reject});
    });
  },
};
window.__DOCTOR_AUTHORITY__ = {
  scope, requests: () => structuredClone(records),
  async setScope(patch) {
    project = {...project, id: patch.project_id ?? project.id,
      generation: patch.generation ?? project.generation, revision: patch.revision ?? project.revision,
      active_branch: patch.branch_id ?? project.active_branch};
    nativeSdk = {...nativeSdk, pinned_revision: patch.sdk_pin ?? nativeSdk.pinned_revision,
      mode: patch.mode ?? nativeSdk.mode};
    runtime = {...runtime,
      connection_identity: patch.connection_identity === undefined ? runtime.connection_identity : patch.connection_identity,
      executable_sha256: patch.executable_sha256 === undefined ? runtime.executable_sha256 : patch.executable_sha256};
    render(); await settle();
  },
  async resolve(id, marker, patch = {}) {
    const read = take(id); read.resolve({...response(read, marker), ...patch}); await settle();
  },
  async reject(id, message) { take(id).reject(new Error(message)); await settle(); },
  async unmount() { mounted = false; render(); await settle(); },
  async mount() { mounted = true; render(); await settle(); },
};
render();
