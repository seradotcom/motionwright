import "../../src/styles.css";
import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { RichNarrativeView } from "../../src/CreativeWorkspaces";
import { applyChange } from "../../src/api";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project } from "../../src/types";

export interface NarrativeFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface NarrativeAuthorityHarness {
  project(): Project;
  sceneId(): string;
  requests(): NarrativeFixtureRequest[];
  setScope(patch: Partial<Pick<Project, "id" | "generation" | "active_branch">> & { sceneId?: string }): Promise<void>;
  externalChange(change: Change): Promise<void>;
  resolve(id: number): Promise<void>;
  reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}
declare global { interface Window { __NARRATIVE_AUTHORITY__: NarrativeAuthorityHarness } }

const host = document.getElementById("narrative-root");
if (!host) throw new Error("Narrative fixture root is missing.");
const root = createRoot(host);
const coordinator = new ProjectMutationGate();
let project: Project = structuredClone(fixtureBootstrap.project);
let sceneId = project.scenes[0].id;
let mounted = true;
const requests: NarrativeFixtureRequest[] = [];
const pending = new Map<number, { resolve(project: Project): void; reject(reason: Error): void }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

// The real coordinator owns publication and validates its original source.
// Only the completion timing is controlled; the component's draft state is not copied.
const commit = (change: Change): Promise<Project> => coordinator.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => pending.set(request.id, { resolve, reject }));
}, saved => { project = saved; render(); });

function render() {
  flushSync(() => root.render(mounted ? <StrictMode><RichNarrativeView project={project}
    scene={project.scenes.find(scene => scene.id === sceneId) ?? null} commit={commit} /></StrictMode> : null));
}
function take(id: number) {
  const request = requests.find(request => request.id === id), reply = pending.get(id);
  if (!request || !reply) throw new Error("No pending Narrative fixture reply " + id);
  pending.delete(id); request.settled = true;
  return { request, reply };
}

window.__NARRATIVE_AUTHORITY__ = {
  project: () => structuredClone(project), sceneId: () => sceneId,
  requests: () => structuredClone(requests),
  async setScope(patch) {
    const { sceneId: selected, ...identity } = patch;
    project = { ...project, ...identity };
    if (selected !== undefined) sceneId = selected;
    render(); await paint();
  },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async resolve(id) {
    const { request, reply } = take(id);
    try { reply.resolve(await applyChange(request.source, request.change)); }
    catch (reason) { reply.reject(reason instanceof Error ? reason : new Error(String(reason))); }
    await paint();
  },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
