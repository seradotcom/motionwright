import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import ProductionDesignWorkspace from "../../src/ProductionDesignWorkspace";
import type { NativeSourceServices } from "../../src/NativeSourceWorkbench";
import { applyChange } from "../../src/api";
import { fixtureBootstrap } from "../../src/fixture";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { Change, Project, CanvasNode } from "../../src/types";
import type { ComponentDistillationInput } from "../../src/componentDistillation";
import "../../src/styles.css";

// Actual component, realization functions, browser Change application and
// ProjectMutationGate. Only reply scheduling and incoming props are
// controlled. This fixture is not StudioService, installed-app or native proof.
export interface DistillationFixtureRequest { id: number; source: Project; change: Change; settled: boolean }
export interface DistillationAuthorityHarness {
  project(): Project; sceneId(): string; requests(): DistillationFixtureRequest[];
  setIdentity(field: "id" | "generation" | "active_branch", value: string): Promise<void>;
  selectScene(value: string): Promise<void>; externalChange(change: Change): Promise<void>;
  observeOriginal(id: number): Promise<void>; resolve(id: number): Promise<void>;
  resolveWithoutOperation(id: number): Promise<void>; reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>; mount(): Promise<void>;
}
declare global { interface Window { __DISTILLATION_AUTHORITY__: DistillationAuthorityHarness; __DISTILLATION_RESTORE_STORAGE__?: () => void } }
const host = document.getElementById("distillation-root");
if (!host) throw new Error("Missing Production regression fixture host.");
const root = createRoot(host), coordinator = new ProjectMutationGate();
const fixtureKey = "TEST-ONLY.distillation.synthetic-props.v1";
const fixture = structuredClone(fixtureBootstrap.project); fixture.locks = [];
let project: Project = fixture, sceneId = fixture.scenes[0].id, mounted = true, playhead = 2;
const saved = sessionStorage.getItem(fixtureKey);
if (saved) ({ project, sceneId, playhead } = JSON.parse(saved) as { project: Project; sceneId: string; playhead: number });
const requests: DistillationFixtureRequest[] = [];
const replies = new Map<number, { resolve(project: Project): void; reject(reason: Error): void; observed?: Project }>();
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const commit = (change: Change): Promise<Project> => coordinator.run(project, () => project, async source => {
  const request = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
  requests.push(request);
  return new Promise<Project>((resolve, reject) => replies.set(request.id, { resolve, reject }));
}, receipt => { project = receipt; render(); });
const legacyCommit = async (change: Change): Promise<void> => { await commit(change); };
// Graph is outside this labelled synthetic Distillation oracle. Unsupported
// reads fail explicitly; no Graph Project, history or Ready context is made up.
const unavailableGraph = (): never => { throw new Error("Graph is unavailable in this synthetic distillation fixture"); };
const graphServices = {
  readContext: null,
  outcomeTransport: { readProject: async () => unavailableGraph(), readHistory: async () => unavailableGraph() },
  refreshProject: async () => unavailableGraph(), openReview: unavailableGraph,
};
// Native source transport is outside this synthetic component fixture.
const sourceServices: NativeSourceServices = {
  saveSource: async () => { throw new Error("Native source saving is unavailable in this synthetic component fixture."); },
  refreshProject: async () => { throw new Error("Native source refresh is unavailable in this synthetic component fixture."); },
};
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify({ project, sceneId, playhead }));
  const scene = project.scenes.find(value => value.id === sceneId) ?? null;
  flushSync(() => root.render(mounted ? <StrictMode><ProductionDesignWorkspace sourceServices={sourceServices} graphServices={graphServices} project={project} scene={scene}
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
window.__DISTILLATION_AUTHORITY__ = {
  project: () => structuredClone(project), sceneId: () => sceneId, requests: () => structuredClone(requests),
  async setIdentity(field, value) { project = { ...project, [field]: value }; render(); await paint(); },
  async selectScene(value) { sceneId = value; render(); await paint(); },
  async externalChange(change) { project = await applyChange(project, change); render(); await paint(); },
  async observeOriginal(id) { const { request, reply } = find(id); reply.observed ??= await applyChange(request.source, request.change); project = structuredClone(reply.observed); render(); await paint(); },
  async resolve(id) { const { request, reply } = take(id); try { reply.resolve(reply.observed ?? await applyChange(request.source, request.change)); } catch (reason) { reply.reject(reason instanceof Error ? reason : new Error(String(reason))); } await paint(); },
  async resolveWithoutOperation(id) { const { request, reply } = take(id); reply.resolve({ ...structuredClone(request.source), revision: request.source.revision + 1 }); await paint(); },
  async reject(id, reason) { take(id).reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); }, async mount() { mounted = true; render(); await paint(); },
};
// Source setup is synthetic author input through the actual browser Change
// implementation. No immutable proposal/sample/review is fabricated or edited.
async function start() {
  if (!saved) {
    const scenes = project.scenes.filter(scene => scene.renderer === "motion-canvas").slice(0, 2);
    const textTemplate = project.scenes.flatMap(scene => scene.nodes).find(node => node.kind === "text");
    if (!textTemplate) throw new Error("Missing complete original Canvas node template.");
    for (const [index, scene] of scenes.entries()) {
      if (!scene.nodes.some(node => node.kind === "text")) {
        project = await applyChange(project, { type: "add_canvas_node", scene_id: scene.id, node: {
          ...structuredClone(textTemplate), id: "00000000-0000-4000-8000-000000000095", name: "Original second source heading",
          text: "A distinct authored source variation.", x: 220, parent_id: null, relations: [], property_locks: [], keyframes: [],
        } });
      }
      const node: CanvasNode = { ...structuredClone(textTemplate),
        id: "00000000-0000-4000-8000-00000000009" + (index + 1), name: "Original variation panel " + index,
        kind: "rectangle", text: null, x: 230 + index * 20, y: 460, width: 500, height: 180,
        parent_id: null, relations: [], property_locks: [], keyframes: [] };
      project = await applyChange(project, { type: "add_canvas_node", scene_id: scene.id, node });
    }
    for (const [index, name] of ["Candidate A", "Candidate B"].entries()) {
      const positives = project.scenes.filter(scene => scene.renderer === "motion-canvas").slice(0, 2).map(scene => ({ scene_id: scene.id,
        node_ids: [scene.nodes.find(node => node.kind === "text")!.id, scene.nodes.find(node => node.kind === "rectangle")!.id] }));
      const input: ComponentDistillationInput = { id: "00000000-0000-4000-8000-00000000009" + (index + 3), label: name,
        author: "Original fixture author", original_rights_attestation: "Source nodes are authored for this controlled regression fixture.",
        source_project_id: project.id, source_generation: project.generation, source_revision: project.revision,
        roles: [{ key: "slot_01", kind: "text" }, { key: "slot_02", kind: "rectangle" }], positives,
        negatives: [{ scene_id: project.scenes.find(scene => scene.renderer !== "motion-canvas")!.id, node_id: null, rejection: "non_motion_canvas" }],
        requirements: ["Retain editable author-owned source roles"] };
      project = await applyChange(project, { type: "propose_component_distillation", input });
    }
  }
  render();
}
void start().catch(reason => { host.textContent = "Distillation fixture setup failed: " + String(reason); });
