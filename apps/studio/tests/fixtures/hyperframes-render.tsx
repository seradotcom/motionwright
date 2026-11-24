/** Explicit synthetic IPC fixture; no real Desktop, permission, runtime or rendering. */
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import "../../src/styles.css";
import "../../src/production-design.css";
import HyperframesSourceWorkbench from "../../src/HyperframesSourceWorkbench";
import { fixtureProject } from "../../src/fixture";
let project = structuredClone(fixtureProject); project.revision = 4;
project.deliverables[0].width = 64; project.deliverables[0].height = 36;
const root = createRoot(document.getElementById("render-root")!);
const render = () => flushSync(() => root.render(<StrictMode>
  <HyperframesSourceWorkbench project={project} desktopMode={true}/>
</StrictMode>));
type Request = Record<string, unknown>;
const records: { command: string; request: Request }[] = JSON.parse(sessionStorage.getItem("hf-fixture-calls") ?? "[]");
let loseNext = false, hold = false, release: ((value: unknown) => void) | null = null;
const grant = { token: "cccccccc-3333-4333-8333-cccccccccccc" };
const source = () => ({
  schema:"motionwright.hyperframes-source/1", project:structuredClone(project), deliverable_id:project.deliverables[0].id,
  fingerprint:"c".repeat(64), source_sha256:"a".repeat(64), source_bytes:"300", html_sha256:"b".repeat(64), html_bytes:"600",
  compiler:"semwright-hyperframes-rect-clips-v3", hyperframes_version:"0.8.181",
  width:64, height:36, frame_count:60, project_modified:false, editor_launched:false, native_execution_started:false,
});
function result(request: Request) {
  const binding = request.source as { revision:number; active_branch:string };
  const current = binding.revision === project.revision && binding.active_branch === project.active_branch;
  return { attempt: {
    schema:"motionwright.hyperframes-desktop-attempt/1", request_id:request.request_id,
    source:request.source, fingerprint:request.expected_fingerprint, runtime_lock_sha256:request.runtime_lock_sha256,
    state:"completed", source_current:current, job_ref:"hf-"+"9".repeat(64), frame_count:60,
    project_modified:false, automatic_retry:false,
  }, preview:current ? { token:grant.token, segment_id:"hf-"+"9".repeat(64),
      scene_ids:project.scenes.map(scene => scene.id), frame_count:60 } : null,
    preview_problem:null };
}
(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
  async invoke(command: string, args: { request: Request }) {
    records.push({ command, request:structuredClone(args.request) });
    sessionStorage.setItem("hf-fixture-calls", JSON.stringify(records));
    if (command === "native_hyperframes_source") return source();
    if (command === "issue_effect_grant") {
      if (hold) return new Promise(resolve => { release = resolve; });
      return grant;
    }
    if (command === "render_hyperframes_source") {
      sessionStorage.setItem("hf-fixture-original", JSON.stringify(args.request));
      if (loseNext) { loseNext = false; throw new Error("Synthetic original acknowledgement lost after completion."); }
      return result(args.request);
    }
    if (command === "read_original_hyperframes_render") {
      const original = JSON.parse(sessionStorage.getItem("hf-fixture-original") ?? "null") as Request | null;
      if (original?.request_id === args.request.request_id) return result(original);
      return { attempt: {
        schema:"motionwright.hyperframes-desktop-attempt/1", request_id:args.request.request_id, source:null,
        fingerprint:null, runtime_lock_sha256:null, state:"not_found", source_current:false,
        job_ref:null, frame_count:null, project_modified:false, automatic_retry:false,
      }, preview:null, preview_problem:null };
    }
    if (command === "preview_native_frame") return Uint8Array.from(atob(PNG_BASE64), char => char.charCodeAt(0));
    throw new Error("Unexpected synthetic fixture command: " + command);
  },
};
(window as unknown as Record<string, unknown>).__hyperframesRenderFixture = {
  records: () => structuredClone(records),
  loseNext: () => { loseNext = true; },
  holdGrant: () => { hold = true; },
  releaseGrant: () => { hold = false; const pending = release; release = null; if (!pending) throw new Error("No pending original grant."); pending(grant); },
  revision: (revision: number) => { project = { ...structuredClone(project), revision }; render(); },
};
const PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAEAAAAAkCAYAAAA5DDySAAAAZElEQVR4nO3QMREAIBDAsNeAFrTg3wnIyECH7L3O2uf+bHSA1gAdoDVAB2gN0AFaA3SA1gAdoDVAB2gN0AFaA3SA1gAdoDVAB2gN0AFaA3SA1gAdoDVAB2gN0AFaA3SA1gAdoD1uSHPT73TMfQAAAABJRU5ErkJggg==";
render();
