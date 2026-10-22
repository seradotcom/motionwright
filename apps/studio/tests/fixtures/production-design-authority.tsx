import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import ProductionDesignWorkspace from "../../src/ProductionDesignWorkspace";
import { applyChange } from "../../src/api";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project } from "../../src/types";
import "../../src/styles.css";

// Actual component, realization functions, browser Change application and
// ProjectMutationGate. Only reply/digest scheduling and incoming props are
// controlled. This fixture is not StudioService, installed-app or native proof.
export interface ProductionFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface ProductionAuthorityHarness {
  project(): Project; sceneId(): string; requests(): ProductionFixtureRequest[];
  setIdentity(field: "id" | "generation" | "active_branch", value: string): Promise<void>;
  selectScene(value: string): Promise<void>; externalChange(change: Change): Promise<void>;
  observeOriginal(id: number): Promise<void>; resolve(id: number): Promise<void>;
  resolveWithoutOperation(id: number): Promise<void>; reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>; mount(): Promise<void>;
  holdNextPlanDigest(): void; heldDigests(): number; releasePlanDigest(): Promise<void>;
}
declare global { interface Window { __PRODUCTION_AUTHORITY__: ProductionAuthorityHarness; __PRODUCTION_RESTORE_STORAGE__?: () => void } }
const host = document.getElementById("production-root");
if (!host) throw new Error("Missing Production regression fixture host.");
const root = createRoot(host), coordinator = new ProjectMutationGate();
const fixtureKey = "TEST-ONLY.production.synthetic-props.v1";
const fixture = structuredClone(fixtureBootstrap.project); fixture.locks = [];
let project: Project = fixture, sceneId = fixture.scenes[0].id, mounted = true, playhead = 2;
const saved = sessionStorage.getItem(fixtureKey);
if (saved) ({ project, sceneId, playhead } = JSON.parse(saved) as { project: Project; sceneId: string; playhead: number });
const requests: ProductionFixtureRequest[] = [];
const replies = new Map<number, { resolve(project: Project): void; reject(reason: Error): void; observed?: Project }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const commit = (change: Change): Promise<Project> => coordinator.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => replies.set(request.id, { resolve, reject }));
}, receipt => { project = receipt; render(); });
const legacyCommit = async (change: Change): Promise<void> => { await commit(change); };
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify({ project, sceneId, playhead }));
  const scene = project.scenes.find(value => value.id === sceneId) ?? null;
  flushSync(() => root.render(mounted ? <StrictMode><ProductionDesignWorkspace project={project} scene={scene}
    commit={legacyCommit} commitCamera={commit} busy={false} playhead={playhead}
    onSeek={value => { if (playhead !== value) { playhead = value; queueMicrotask(render); } }}
    onSelectScene={id => { sceneId = id; render(); }} onOpenCanvas={() => {}} onOpenRender={() => {}}
    displayProfile={null} evidence={null}/></StrictMode> : null));
}
function find(id: number) {
  const request = requests.find(value => value.id === id), reply = replies.get(id);
  if (!request || !reply) throw new Error("No pending Production reply " + id);
  return { request, reply };
}
function take(id: number) { const value = find(id); replies.delete(id); value.request.settled = true; return value; }
const originalDigest = SubtleCrypto.prototype.digest;
let armDigest = false, digestCount = 0, releaseDigest: (() => void) | null = null;
SubtleCrypto.prototype.digest = function(algorithm, data) {
  const result = originalDigest.call(this, algorithm, data);
  const raw = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  const text = new TextDecoder().decode(raw);
  if (!armDigest || !text.startsWith('{"objective":') || !text.includes('"shots":') || !text.endsWith('"approval":null}')) return result;
  armDigest = false; digestCount++;
  const gate = new Promise<void>(resolve => { releaseDigest = resolve; });
  return Promise.all([result, gate]).then(([bytes]) => bytes);
};
window.__PRODUCTION_AUTHORITY__ = {
  project: () => structuredClone(project), sceneId: () => sceneId, requests: () => structuredClone(requests),
  async setIdentity(field, value) { project = { ...project, [field]: value }; render(); await paint(); },
  async selectScene(value) { sceneId = value; render(); await paint(); },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async observeOriginal(id) { const { request, reply } = find(id); reply.observed ??= await applyChange(request.source, request.change); project = structuredClone(reply.observed); render(); await paint(); },
  async resolve(id) { const { request, reply } = take(id); try { reply.resolve(reply.observed ?? await applyChange(request.source, request.change)); } catch (reason) { reply.reject(reason instanceof Error ? reason : new Error(String(reason))); } await paint(); },
  async resolveWithoutOperation(id) { const { request, reply } = take(id); reply.resolve({ ...structuredClone(request.source), revision: request.source.revision + 1 }); await paint(); },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); }, async mount() { mounted = true; render(); await paint(); },
  holdNextPlanDigest() { armDigest = true; }, heldDigests: () => digestCount,
  async releasePlanDigest() { const release = releaseDigest; releaseDigest = null; if (!release) throw new Error("No held Production digest."); release(); await paint(); },
};
render();
