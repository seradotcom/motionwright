/** Explicit synthetic IPC fixture; no real Desktop, permission, runtime or rendering. */
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import "../../src/styles.css";
import "../../src/production-design.css";
import HyperframesSourceWorkbench from "../../src/HyperframesSourceWorkbench";
import { fixtureProject } from "../../src/fixture";
import { RUNTIME_RETENTION_PREFIX, type RuntimeInstallation } from "../../src/runtimeProfileApi";
let project = structuredClone(fixtureProject); project.revision = 4;
project.deliverables[0].width = 64; project.deliverables[0].height = 36;
const root = createRoot(document.getElementById("render-root")!);
const render = () => flushSync(() => root.render(<StrictMode>
  <HyperframesSourceWorkbench project={project} desktopMode={true}/>
</StrictMode>));
type Request = Record<string, unknown>;
const records: { command: string; request: Request }[] = JSON.parse(sessionStorage.getItem("hf-fixture-calls") ?? "[]");
let loseNext = false, hold = false, release: ((value: unknown) => void) | null = null;
type ObservedState = "running" | "cancelling" | "completed" | "failed_known" | "outcome_unknown";
let pendingNext = false, nativeState: ObservedState = "running", holdObservation = false;
let returnObservation: (() => void) | null = null;
const grant = { token: "cccccccc-3333-4333-8333-cccccccccccc" };
const installId = "11111111-7777-4777-8777-111111111111";
const destination = "/synthetic/runtime-profiles-v1/" + installId;
let runtimeState: RuntimeInstallation["state"] = "installed";
let holdRuntime = false, returnRuntime: (() => void) | null = null;
const installation = (scope: unknown, state: RuntimeInstallation["state"]) => ({
  schema: "motionwright.runtime-profile-installation/1", scope, install_id: installId,
  profile: "hyperframes", label: "HyperFrames", version: "0.8.181", platform: "linux-x86_64",
  manifest_sha256: "a".repeat(64), total_bytes: 4096, file_count: 6,
  destination, disabled_destination: "/synthetic/runtime-profiles-v1/disabled-" + installId,
  node_path: destination + "/tools/node", entry_path: destination + "/runtime/semwright-hyperframes-runner.mjs",
  lock_path: destination + "/runtime/runtime-lock.json", lock_sha256: "d".repeat(64),
  state, content_verified: state === "installed", rollback_available: state !== "rolled_back",
  rollback_subject: "runtime-profile-rollback|" + installId + "|" + "b".repeat(64),
  execution_performed: false, automatic_retry: false,
});
const source = () => ({
  schema:"motionwright.hyperframes-source/1", project:structuredClone(project), deliverable_id:project.deliverables[0].id,
  fingerprint:"c".repeat(64), source_sha256:"a".repeat(64), source_bytes:"300", html_sha256:"b".repeat(64), html_bytes:"600",
  compiler:"semwright-hyperframes-rect-clips-v3", hyperframes_version:"0.8.181",
  width:64, height:36, frame_count:60, project_modified:false, editor_launched:false, native_execution_started:false,
});
function result(request: Request, state: ObservedState = "completed") {
  const binding = request.source as { revision:number; active_branch:string };
  const current = binding.revision === project.revision && binding.active_branch === project.active_branch;
  return { attempt: {
    schema:"motionwright.hyperframes-desktop-attempt/1", request_id:request.request_id,
    source:request.source, fingerprint:request.expected_fingerprint, runtime_lock_sha256:request.runtime_lock_sha256,
    state, source_current:current, job_ref:state === "outcome_unknown" ? null : "hf-"+"9".repeat(64), frame_count:state === "completed" ? 60 : null,
    project_modified:false, automatic_retry:false,
  }, preview:current && state === "completed" ? { token:grant.token, segment_id:"hf-"+"9".repeat(64),
      scene_ids:project.scenes.map(scene => scene.id), frame_count:60 } : null,
    preview_problem:null };
}
(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
  async invoke(command: string, args: { request: Request }) {
    records.push({ command, request:structuredClone(args.request) });
    sessionStorage.setItem("hf-fixture-calls", JSON.stringify(records));
    if (command === "native_hyperframes_source") return source();
    if (command === "runtime_profile_inventory") return {
      schema: "motionwright.runtime-profile-inventory/1", scope: args.request, supported: true,
      platform: "linux-x86_64", profiles: [installation(args.request, "copy_completed")], alternatives: "",
    };
    if (command === "read_original_runtime_profile") {
      if (args.request.install_id !== installId) throw new Error("Foreign synthetic installation");
      const result = installation(structuredClone(args.request.scope), runtimeState);
      if (holdRuntime) return new Promise(resolve => { returnRuntime = () => resolve(result); });
      return result;
    }
    if (command === "issue_effect_grant") {
      if (hold) return new Promise(resolve => { release = resolve; });
      return grant;
    }
    if (command === "render_hyperframes_source") {
      sessionStorage.setItem("hf-fixture-original", JSON.stringify(args.request));
      sessionStorage.setItem("hf-fixture-retained-state", pendingNext ? "outcome_unknown" : "completed");
      if (pendingNext) { pendingNext = false; throw new Error("Synthetic original acknowledgement lost while native job remained pending."); }
      if (loseNext) { loseNext = false; throw new Error("Synthetic original acknowledgement lost after completion."); }
      return result(args.request);
    }
    if (command === "hyperframes_render_history") {
      const original = JSON.parse(sessionStorage.getItem("hf-fixture-original") ?? "null") as Request | null;
      return { schema:"motionwright.hyperframes-render-history/1", project_id:project.id,
        generation:project.generation, revision:project.revision, next:null, items:original ? [{
          request_id:original.request_id, source:original.source,
          fingerprint:original.expected_fingerprint, runtime_lock_sha256:original.runtime_lock_sha256,
          retention:"dispatched",
        }] : [] };
    }
    if (command === "read_original_hyperframes_render") {
      const original = JSON.parse(sessionStorage.getItem("hf-fixture-original") ?? "null") as Request | null;
      if (original?.request_id === args.request.request_id) return result(original, (sessionStorage.getItem("hf-fixture-retained-state") ?? "completed") as ObservedState);
      return { attempt: {
        schema:"motionwright.hyperframes-desktop-attempt/1", request_id:args.request.request_id, source:null,
        fingerprint:null, runtime_lock_sha256:null, state:"not_found", source_current:false,
        job_ref:null, frame_count:null, project_modified:false, automatic_retry:false,
      }, preview:null, preview_problem:null };
    }
    if (command === "observe_original_hyperframes_job") {
      const original = JSON.parse(sessionStorage.getItem("hf-fixture-original") ?? "null") as Request | null;
      if (original?.request_id !== args.request.request_id) throw new Error("No acknowledged original fixture job");
      const observed = result(original, nativeState);
      sessionStorage.setItem("hf-fixture-retained-state", nativeState);
      if (holdObservation) return new Promise(resolve => { returnObservation = () => resolve(observed); });
      return observed;
    }
    if (command === "preview_native_frame") return Uint8Array.from(atob(PNG_BASE64), char => char.charCodeAt(0));
    throw new Error("Unexpected synthetic fixture command: " + command);
  },
};
(window as unknown as Record<string, unknown>).__hyperframesRenderFixture = {
  records: () => structuredClone(records),
  loseNext: () => { loseNext = true; },
  pendingNext: () => { pendingNext = true; },
  jobState: (state: ObservedState) => { nativeState = state; },
  holdObservation: () => { holdObservation = true; },
  releaseObservation: () => { holdObservation = false; const pending = returnObservation; returnObservation = null; if (!pending) throw new Error("No pending original observation"); pending(); },
  runtimeState: (state: RuntimeInstallation["state"]) => { runtimeState = state; },
  holdRuntime: () => { holdRuntime = true; },
  releaseRuntime: () => { holdRuntime = false; const pending = returnRuntime; returnRuntime = null; if (!pending) throw new Error("No original runtime read."); pending(); },
  retainRollback: () => {
    runtimeState = "rolled_back";
    localStorage.setItem(RUNTIME_RETENTION_PREFIX + project.id + ":" + project.generation, JSON.stringify({
      schema: "motionwright.runtime-profile-retention/1", operations: [{
        install_id: installId, scope: { project_id: project.id, generation: project.generation, revision: project.revision },
        manifest_sha256: "a".repeat(64), profile: "hyperframes", kind: "rollback", state: "dispatched",
      }],
    }));
  },
  holdGrant: () => { hold = true; },
  releaseGrant: () => { hold = false; const pending = release; release = null; if (!pending) throw new Error("No pending original grant."); pending(grant); },
  revision: (revision: number) => { project = { ...structuredClone(project), revision }; render(); },
};
const PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAkCAYAAAA5DDySAAAAZElEQVR4nO3QMREAIBDAsNeAFrTg3wnIyECH7L3O2uf+bHSA1gAdoDVAB2gN0AFaA3SA1gAdoDVAB2gN0AFaA3SA1gAdoDVAB2gN0AFaA3SA1gAdoDVAB2gN0AFaA3SA1gAdoD1uSHPT73TMfQAAAABJRU5ErkJggg==";
render();
