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
export interface CreativeFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface CreativeAuthorityHarness {
  project(): Project; sceneId(): string; requests(): CreativeFixtureRequest[];
  setIdentity(field: "id" | "generation" | "active_branch", value: string): Promise<void>;
  selectScene(value: string): Promise<void>; externalChange(change: Change): Promise<void>;
  observeOriginal(id: number): Promise<void>; resolve(id: number): Promise<void>;
  resolveWithoutOperation(id: number): Promise<void>; reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>; mount(): Promise<void>;
  holdNextFileRead(): void; heldFileReads(): number; releaseFileRead(fail?: boolean): Promise<void>;
}
declare global { interface Window { __CREATIVE_AUTHORITY__: CreativeAuthorityHarness; __CREATIVE_RESTORE_STORAGE__?: () => void } }
const host = document.getElementById("creative-root");
if (!host) throw new Error("Missing Production regression fixture host.");
const root = createRoot(host), coordinator = new ProjectMutationGate();
const fixtureKey = "TEST-ONLY.creative.synthetic-props.v1";
const fixture = structuredClone(fixtureBootstrap.project); fixture.locks = [];
let project: Project = fixture, sceneId = fixture.scenes[0].id, mounted = true, playhead = 2;
const saved = sessionStorage.getItem(fixtureKey);
if (saved) ({ project, sceneId, playhead } = JSON.parse(saved) as { project: Project; sceneId: string; playhead: number });
const requests: CreativeFixtureRequest[] = [];
const replies = new Map<number, { resolve(project: Project): void; reject(reason: Error): void; observed?: Project }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const commit = (change: Change): Promise<Project> => coordinator.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => replies.set(request.id, { resolve, reject }));
}, receipt => { project = receipt; render(); });
const legacyCommit = async (change: Change): Promise<void> => { await commit(change); };
// This component fixture does not provide a native Graph transport.
const unavailableGraph = (): never => { throw new Error("Graph is unavailable in this synthetic Production fixture."); };
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify({ project, sceneId, playhead }));
  const scene = project.scenes.find(value => value.id === sceneId) ?? null;
  flushSync(() => root.render(mounted ? <StrictMode><ProductionDesignWorkspace project={project} scene={scene}
    graphServices={{
      readContext: null,
      outcomeTransport: { readProject: async () => unavailableGraph(), readHistory: async () => unavailableGraph() },
      refreshProject: async () => unavailableGraph(), openReview: unavailableGraph,
    }}
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
const originalFileRead = File.prototype.arrayBuffer;
let armFile = false, fileCount = 0, releaseFile: ((fail?: boolean) => void) | null = null;
File.prototype.arrayBuffer = function() {
  const result = originalFileRead.call(this);
  if (!armFile) return result;
  armFile = false; fileCount++;
  const gate = new Promise<void>((resolve, reject) => { releaseFile = fail => fail ? reject(new Error("Deferred fixture file failure")) : resolve(); });
  return Promise.all([result, gate]).then(([bytes]) => bytes);
};
window.__CREATIVE_AUTHORITY__ = {
  project: () => structuredClone(project), sceneId: () => sceneId, requests: () => structuredClone(requests),
  async setIdentity(field, value) { project = { ...project, [field]: value }; render(); await paint(); },
  async selectScene(value) { sceneId = value; render(); await paint(); },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async observeOriginal(id) { const { request, reply } = find(id); reply.observed ??= await applyChange(request.source, request.change); project = structuredClone(reply.observed); render(); await paint(); },
  async resolve(id) { const { request, reply } = take(id); try { reply.resolve(reply.observed ?? await applyChange(request.source, request.change)); } catch (reason) { reply.reject(reason instanceof Error ? reason : new Error(String(reason))); } await paint(); },
  async resolveWithoutOperation(id) { const { request, reply } = take(id); reply.resolve({ ...structuredClone(request.source), revision: request.source.revision + 1 }); await paint(); },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); }, async mount() { mounted = true; render(); await paint(); },
  holdNextFileRead() { armFile = true; }, heldFileReads: () => fileCount,
  async releaseFileRead(fail = false) { const release = releaseFile; releaseFile = null; if (!release) throw new Error("No held source file read."); release(fail); await paint(); },
};
// Controlled source metadata is explicitly a component fixture. Actual source
// ingestion and SQLite CAS are reserved for creative-workbench-service.spec.ts.
async function start() {
  if (!saved) {
    const inputs = [
      { id: "00000000-0000-4000-8000-000000000091", table: "00000000-0000-4000-8000-000000000093", label: "Fixture dataset A", milli: 12500 },
      { id: "00000000-0000-4000-8000-000000000092", table: "00000000-0000-4000-8000-000000000094", label: "Fixture dataset B", milli: 18000 },
    ];
    for (const input of inputs) {
      const rows = [{ key: "latency", label: "Observed latency", milli: input.milli, unit: "milliseconds" as const }];
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(rows)))), value => value.toString(16).padStart(2, "0")).join("");
      project = await applyChange(project, { type: "add_asset", asset: { id: input.id, name: input.label + ".json", media_type: "application/json", content_sha256: digest, source_revision: null } });
      project = await applyChange(project, { type: "upsert_measured_table", table: { schema: "motionwright.measured-table/1", id: input.table, label: input.label, version: 1, source_asset_id: input.id, source_sha256: digest, rows } });
    }
  }
  render();
}
void start().catch(reason => { host.textContent = "Fixture setup failed: " + String(reason); });
