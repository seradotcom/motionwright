import { useState } from "react";
import { createRoot } from "react-dom/client";
import IntegrationsWorkspace from "../../src/IntegrationsWorkspace";
import { fixtureBootstrap } from "../../src/fixture";
import type { Bootstrap, Change, ProductionRuntimeCompatibility, Project } from "../../src/types";
import "../../src/styles.css";

// Real Integration component and API wrappers, explicitly synthetic deferred IPC.
// These requests do not inspect/install a runtime or mutate canonical projects.
type Command = "production_runtime_status" | "choose_semwright_connection" | "clear_saved_semwright_connection";
type Request = Record<string, unknown>;
type Pending = { id: number; command: Command; request: Request; settled: boolean;
  resolve(value: unknown): void; reject(error: Error): void };
const sdk: Bootstrap["native_sdk"] = { ...fixtureBootstrap.native_sdk, mode: "tauri" };
const projectA: Project = { ...structuredClone(fixtureBootstrap.project),
  title: "Synthetic runtime source A" };
const projectB: Project = { ...structuredClone(projectA),
  id: "10000000-0000-4000-8000-000000000001",
  generation: "20000000-0000-4000-8000-000000000001",
  title: "Synthetic runtime source B" };
const originalProjects = JSON.stringify([projectA, projectB]);
const projects = new Map([projectA, projectB].map(project => [project.id, project]));
const pending: Pending[] = [];
const unexpectedCommands: string[] = [];
let commits = 0;
const runtime = window as unknown as Record<string, unknown>;
runtime.__TAURI_INTERNALS__ = {
  invoke(command: string, args: { request?: Request }): Promise<unknown> {
    if (!(["production_runtime_status", "choose_semwright_connection",
      "clear_saved_semwright_connection"] as string[]).includes(command) || !args.request) {
      unexpectedCommands.push(command);
      return Promise.reject(new Error("Unrelated command in synthetic runtime fixture: " + command));
    }
    const request = structuredClone(args.request);
    const project = projects.get(String(request.project_id));
    const generation = command === "production_runtime_status" ? request.expected_generation : request.generation;
    const revision = command === "production_runtime_status" ? request.expected_revision : request.revision;
    if (!project || generation !== project.generation || revision !== project.revision) {
      throw new Error("API request did not identify one of the exact fixture sources.");
    }
    let entry: Pending;
    return new Promise<unknown>((resolve, reject) => {
      entry = { id: pending.length, command: command as Command, request, settled: false, resolve, reject };
      pending.push(entry);
    }).finally(() => { entry.settled = true; });
  },
};
function unresolved(id: number): Pending {
  const entry = pending[id];
  if (!entry || entry.settled) throw new Error("No unresolved scheduled request: " + id);
  return entry;
}
runtime.__integrationRuntimeSchedule = {
  snapshot: () => ({
    calls: pending.map(({ id, command, request, settled }) => ({ id, command, request, settled })),
    commits, unexpectedCommands: [...unexpectedCommands],
    projectsIntact: JSON.stringify([projectA, projectB]) === originalProjects,
    projectA: projectA.id, projectB: projectB.id,
  }),
  status(id: number, label: string) {
    const entry = unresolved(id);
    if (entry.command !== "production_runtime_status") throw new Error("Expected a runtime probe.");
    const status: ProductionRuntimeCompatibility = {
      status: "unconfigured", reason: "SYNTHETIC STATUS: " + label,
      expected_version: sdk.version, observed_version: null, version_compatible: null,
      pinned_revision: sdk.pinned_revision, connection_identity: null, executable_sha256: null,
    };
    entry.resolve(status);
  },
  selection(id: number, changed: boolean) {
    const entry = unresolved(id);
    if (entry.command === "production_runtime_status") throw new Error("Expected a chooser/clear request.");
    entry.resolve(changed);
  },
  reject(id: number, label: string) {
    unresolved(id).reject(new Error("SYNTHETIC FAILURE: " + label));
  },
};
async function rejectCanonicalCommit(_change: Change): Promise<never> {
  commits++;
  throw new Error("Canonical project mutations are not part of the runtime scheduling fixture.");
}
function Fixture() {
  const [project, setProject] = useState(projectA);
  return <main>
    <h1>Synthetic Integration runtime scheduling fixture</h1>
    <p>Actual component and API; explicit deferred IPC. No installed runtime, native diagnosis, grants, project persistence or human acceptance are claimed.</p>
    <p aria-label="Fixture source">{project.id} / {project.generation} / r{project.revision}</p>
    <nav aria-label="Synthetic source controls">
      <button onClick={() => setProject(projectA)}>Source A</button>
      <button onClick={() => setProject(projectB)}>Source B</button>
    </nav>
    <IntegrationsWorkspace project={project} nativeSdk={sdk} commit={rejectCanonicalCommit} />
  </main>;
}
createRoot(document.getElementById("root")!).render(<Fixture />);
