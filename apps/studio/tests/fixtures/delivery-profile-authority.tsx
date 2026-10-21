import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import DeliveryProfiles from "../../src/DeliveryProfiles";
import { applyChange } from "../../src/api";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project } from "../../src/types";

export interface ProfileFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface ProfileAuthorityHarness {
  project(): Project; requests(): ProfileFixtureRequest[];
  setIdentity(field: "id" | "generation" | "active_branch", value: string): Promise<void>;
  externalChange(change: Change): Promise<void>;
  observeOriginal(id: number): Promise<void>;
  resolve(id: number): Promise<void>; resolveWithoutOperation(id: number): Promise<void>;
  reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>; mount(): Promise<void>;
}
declare global { interface Window { __PROFILE_AUTHORITY__: ProfileAuthorityHarness; __PROFILE_TEST_RESTORE__?: () => void } }
const host = document.getElementById("delivery-profile-root");
if (!host) throw new Error("Profile authority fixture root is missing.");
const root = createRoot(host), gate = new ProjectMutationGate();
// Synthetic props remain stable across a browser reload. This is fixture state,
// never StudioService, SQLite persistence, native evidence or acceptance.
const fixtureKey = "TEST-ONLY.profile.synthetic-props.v1";
const stored = sessionStorage.getItem(fixtureKey);
let project: Project = stored ? JSON.parse(stored) as Project : structuredClone(fixtureBootstrap.project);
if (!stored) project.locks = [];
let mounted = true;
const requests: ProfileFixtureRequest[] = [];
const replies = new Map<number, { resolve(project: Project): void; reject(reason: Error): void; observed?: Project }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const commit = (change: Change): Promise<Project> => gate.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request); return new Promise<Project>((resolve, reject) => replies.set(request.id, { resolve, reject }));
}, saved => { project = saved; render(); });
const legacyCommit = async (change: Change) => { try { await commit(change); } catch { /* The App void callback reports its own notification. */ } };
const unexpectedNative = () => { throw new Error("Native operations are outside this synthetic component fixture."); };
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify(project));
  flushSync(() => root.render(mounted ? <StrictMode>
    <DeliveryProfiles project={project} commit={legacyCommit} commitRecipe={commit}
      desktopMode={false} currentProjectState={() => ({ project, ...gate.activity })}
      renderEvidence={null} onRenderEvidence={unexpectedNative} avEvidence={null} onAvEvidence={unexpectedNative}
      exportEvidence={null} onExportEvidence={unexpectedNative} onOpenAudio={unexpectedNative} />
  </StrictMode> : null));
}
function find(id: number) {
  const request = requests.find(item => item.id === id), reply = replies.get(id);
  if (!request || !reply) throw new Error("No pending Profile fixture reply " + id);
  return { request, reply };
}
function take(id: number) { const result = find(id); replies.delete(id); result.request.settled = true; return result; }
window.__PROFILE_AUTHORITY__ = {
  project: () => structuredClone(project), requests: () => structuredClone(requests),
  async setIdentity(field, value) { project = { ...project, [field]: value }; render(); await paint(); },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async observeOriginal(id) {
    const { request, reply } = find(id); reply.observed ??= await applyChange(request.source, request.change);
    project = structuredClone(reply.observed); render(); await paint();
  },
  async resolve(id) {
    const { request, reply } = take(id);
    try { reply.resolve(reply.observed ?? await applyChange(request.source, request.change)); }
    catch (failure) { reply.reject(failure instanceof Error ? failure : new Error(String(failure))); }
    await paint();
  },
  async resolveWithoutOperation(id) {
    const { request, reply } = take(id); reply.resolve({ ...structuredClone(request.source), revision: request.source.revision + 1 }); await paint();
  },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
