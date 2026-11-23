/** Explicit IPC fixture: no actual grant, SQLite, Desktop or renderer is exercised. */
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import "../../src/styles.css";
import "../../src/production-design.css";
import HyperframesSourceWorkbench from "../../src/HyperframesSourceWorkbench";
import { fixtureProject } from "../../src/fixture";
import type { Project } from "../../src/types";
let project: Project = structuredClone(fixtureProject); project.revision = 4;
const root = createRoot(document.getElementById("source-root")!);
const render = () => flushSync(() => root.render(<StrictMode>
  <HyperframesSourceWorkbench project={project} desktopMode={true}/>
</StrictMode>));
const source = (p: Project) => ({
  schema: "motionwright.hyperframes-source/1", project: structuredClone(p), deliverable_id: p.deliverables[0].id,
  fingerprint: "c".repeat(64), source_sha256: "a".repeat(64), source_bytes: "300",
  html_sha256: "b".repeat(64), html_bytes: "600", compiler: "semwright-hyperframes-rect-clips-v3",
  hyperframes_version: "0.8.181", width: p.deliverables[0].width, height: p.deliverables[0].height, frame_count: 60,
  project_modified: false, editor_launched: false, native_execution_started: false,
});
type Request = Record<string, unknown>;
const records: { command: string; request: Request }[] = [];
let holdGrant = false, grantReturn: ((value: unknown) => void) | null = null;
const grant = { token: "cccccccc-3333-4333-8333-cccccccccccc" };
(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {
  async invoke(command: string, args: { request: Request }) {
    records.push({ command, request: structuredClone(args.request) });
    if (command === "native_hyperframes_source") return source(project);
    if (command === "issue_effect_grant") {
      if (!holdGrant) return grant;
      return new Promise(resolve => { grantReturn = resolve; });
    }
    if (command === "export_hyperframes_source_file") {
      const request = args.request;
      return { schema: "motionwright.hyperframes-source-export/1",
        source: request.source, fingerprint: request.expected_fingerprint, format: request.format, path: request.path,
        content_sha256: request.format === "html" ? "b".repeat(64) : "a".repeat(64),
        size_bytes: request.format === "html" ? "600" : "300",
        project_modified: false, editor_launched: false, native_execution_started: false };
    }
    throw new Error("Unexpected source fixture command: " + command);
  },
};
(window as unknown as Record<string, unknown>).__hyperframesFixture = {
  records: () => structuredClone(records),
  holdGrant: () => { holdGrant = true; },
  releaseGrant: () => { holdGrant = false; const resolve = grantReturn; grantReturn = null; if (!resolve) throw new Error("No original grant response"); resolve(grant); },
  revision: (value: number) => { project = { ...structuredClone(project), revision: value }; render(); },
};
render();
