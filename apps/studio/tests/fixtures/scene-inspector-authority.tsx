import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import SceneInspector from "../../src/SceneInspector";
import { applyChange } from "../../src/api";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project } from "../../src/types";

export interface InspectorFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface InspectorAuthorityHarness {
  project(): Project;
  sceneId(): string;
  requests(): InspectorFixtureRequest[];
  setIdentity(field: "id" | "generation" | "active_branch", value: string): Promise<void>;
  selectScene(value: string): Promise<void>;
  externalChange(change: Change): Promise<void>;
  observeOriginal(id: number): Promise<void>;
  resolve(id: number): Promise<void>;
  resolveWithoutOperation(id: number): Promise<void>;
  reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}
declare global { interface Window { __INSPECTOR_AUTHORITY__: InspectorAuthorityHarness; __INSPECTOR_TEST_RESTORE__?: () => void } }

const host = document.getElementById("inspector-root");
if (!host) throw new Error("Inspector regression fixture root is missing.");
const root = createRoot(host), coordinator = new ProjectMutationGate();
// Synthetic props survive reload only to keep the fixture source stable.
// This key is not product persistence, a service result or acceptance evidence.
const fixtureKey = "TEST-ONLY.inspector.synthetic-props.v1";
const fixture = structuredClone(fixtureBootstrap.project);
fixture.locks = [];
let project: Project = fixture, sceneId = fixture.scenes[0].id, mounted = true;
const stored = sessionStorage.getItem(fixtureKey);
if (stored) ({ project, sceneId } = JSON.parse(stored) as { project: Project; sceneId: string });
const requests: InspectorFixtureRequest[] = [];
const pending = new Map<number, { resolve(project: Project): void; reject(reason: Error): void; observed?: Project }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
// Use the same presentation coordinator as App. Only reply scheduling and
// adversarial incoming props are synthetic; there is no native service here.
const commit = (change: Change): Promise<Project> => coordinator.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => pending.set(request.id, { resolve, reject }));
}, saved => { project = saved; render(); });
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify({ project, sceneId }));
  const scene = project.scenes.find(item => item.id === sceneId) ?? null;
  flushSync(() => root.render(mounted ? <StrictMode><SceneInspector project={project} scene={scene}
    commit={async change => { try { await commit(change); } catch { /* The App notification path owns this void callback. */ } }}
    commitScene={commit} commitBeat={commit} rate={{ num: "30", den: "1" }} mode="ndf"
    rendererLabels={{ "motion-canvas": "Motion Canvas", mlt: "MLT", blender: "Blender", "manim-community": "Manim Community", remotion: "Remotion", "manim-gl": "ManimGL" }} /></StrictMode> : null));
}
function find(id: number) {
  const request = requests.find(item => item.id === id), reply = pending.get(id);
  if (!request || !reply) throw new Error("No pending Inspector fixture reply " + id);
  return { request, reply };
}
function take(id: number) {
  const result = find(id); pending.delete(id); result.request.settled = true; return result;
}
window.__INSPECTOR_AUTHORITY__ = {
  project: () => structuredClone(project), sceneId: () => sceneId, requests: () => structuredClone(requests),
  async setIdentity(field, value) { project = { ...project, [field]: value }; render(); await paint(); },
  async selectScene(value) { sceneId = value; render(); await paint(); },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async observeOriginal(id) {
    const { request, reply } = find(id);
    reply.observed ??= await applyChange(request.source, request.change);
    project = structuredClone(reply.observed); render(); await paint();
  },
  async resolve(id) {
    const { request, reply } = take(id);
    try { reply.resolve(reply.observed ?? await applyChange(request.source, request.change)); }
    catch (reason) { reply.reject(reason instanceof Error ? reason : new Error(String(reason))); }
    await paint();
  },
  async resolveWithoutOperation(id) {
    const { request, reply } = take(id);
    reply.resolve({ ...structuredClone(request.source), revision: request.source.revision + 1 });
    await paint();
  },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
