import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import SceneBeatEditor from "../../src/SceneBeatEditor";
import { applyChange } from "../../src/api";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project } from "../../src/types";

export interface BeatFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface BeatAuthorityHarness {
  project(): Project;
  sceneId(): string;
  beatId(): string;
  requests(): BeatFixtureRequest[];
  setIdentity(field: "id" | "generation" | "active_branch" | "sceneId" | "beatId", value: string): Promise<void>;
  externalChange(change: Change): Promise<void>;
  observeOriginal(id: number): Promise<void>;
  resolve(id: number): Promise<void>;
  reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}
declare global { interface Window { __BEAT_AUTHORITY__: BeatAuthorityHarness; __BEAT_TEST_RESTORE__?: () => void } }

const host = document.getElementById("beat-root");
if (!host) throw new Error("Beat regression fixture root is missing.");
const root = createRoot(host), coordinator = new ProjectMutationGate();
// This key stores only synthetic fixture props across a page reload. It is not
// product persistence and is separate from the component's actual recovery keys.
const fixtureKey = "TEST-ONLY.scene-beat.synthetic-props.v1";
const fixture = structuredClone(fixtureBootstrap.project);
fixture.locks = [];
const initialScene = fixture.scenes[0], initialBeat = initialScene.beats[0];
initialScene.duration = { num: "7", den: "1" };
initialScene.beats = [{ ...initialBeat, start: { num: "1001", den: "30000" }, duration: { num: "1001", den: "30000" } }];
let project: Project = fixture, sceneId = initialScene.id, beatId = initialBeat.id;
const stored = sessionStorage.getItem(fixtureKey);
if (stored) ({ project, sceneId, beatId } = JSON.parse(stored) as { project: Project; sceneId: string; beatId: string });
let mounted = true;
const requests: BeatFixtureRequest[] = [];
const pending = new Map<number, { resolve(project: Project): void; reject(reason: Error): void; observed?: Project }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));

// The real coordinator validates the captured source and owns publication.
// The fixture controls only reply scheduling and adversarial incoming props.
const commit = (change: Change): Promise<Project> => coordinator.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => pending.set(request.id, { resolve, reject }));
}, saved => { project = saved; render(); });

function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify({ project, sceneId, beatId }));
  const scene = project.scenes.find(item => item.id === sceneId);
  if (!scene) throw new Error("The selected synthetic fixture scene is missing.");
  flushSync(() => root.render(mounted ? <StrictMode><SceneBeatEditor project={project} scene={scene}
    contentLocked={false} timingLocked={false} commit={commit} rate={{ num: "30000", den: "1001" }} mode="ndf" /></StrictMode> : null));
}
function find(id: number) {
  const request = requests.find(item => item.id === id), reply = pending.get(id);
  if (!request || !reply) throw new Error("No pending Beat fixture reply " + id);
  return { request, reply };
}
function take(id: number) {
  const result = find(id); pending.delete(id); result.request.settled = true; return result;
}

window.__BEAT_AUTHORITY__ = {
  project: () => structuredClone(project), sceneId: () => sceneId, beatId: () => beatId,
  requests: () => structuredClone(requests),
  async setIdentity(field, value) {
    if (field === "sceneId") {
      project = { ...project, scenes: project.scenes.map(scene => scene.id === sceneId ? { ...scene, id: value } : scene) };
      sceneId = value;
    } else if (field === "beatId") {
      project = { ...project, scenes: project.scenes.map(scene => scene.id === sceneId
        ? { ...scene, beats: scene.beats.map(beat => beat.id === beatId ? { ...beat, id: value } : beat) } : scene) };
      beatId = value;
    } else project = { ...project, [field]: value };
    render(); await paint();
  },
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
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
