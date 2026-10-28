import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import SemanticGraphWorkbench from "../../src/SemanticGraphWorkbench";
import { emptyProductionDesign } from "../../src/creativeProduction";
import { fixtureBootstrap } from "../../src/fixture";
import { graphSame, graphSourceSnapshot } from "../../src/semanticGraph";
import { captureGraphRecoveryRequest, type GraphOperationRecovery } from "../../src/semanticGraphRecovery";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { GraphAuthoringContext, GraphProject, SemanticGraphChange } from "../../src/semanticGraph";
import type { Project, ReviewAnchor } from "../../src/types";
import "../../src/styles.css";
import "../../src/production-design.css";

interface FixtureRequest { id: number; requestId: string; source: Project; change: SemanticGraphChange; settled: boolean; recorded: GraphProject | null }
interface GraphAuthorityHarness {
  project(): GraphProject; saved(): GraphProject; requests(): FixtureRequest[]; reviews(): ReviewAnchor[];
  resolve(id: number): Promise<void>; reject(id: number, reason: string, committed?: boolean): Promise<void>; rejectKnown(id: number): Promise<void>;
  recordLateOriginal(id: number): Promise<void>; observeOriginal(id: number): Promise<void>;
  publish(project: GraphProject): Promise<void>; refresh(): Promise<void>; unmount(): Promise<void>; mount(): Promise<void>;
  deferContext(value: boolean): void; resolveContext(): Promise<void>; blockContext(value: boolean): Promise<void>;
  setPlayhead(seconds: number): Promise<void>;
}
declare global { interface Window { __GRAPH_AUTHORITY__: GraphAuthorityHarness; __GRAPH_RESTORE_STORAGE__?: () => void } }
const host = document.getElementById("graph-root"); if (!host) throw new Error("Graph fixture host is missing.");
const root = createRoot(host), gate = new ProjectMutationGate(), key = "TEST-ONLY.semantic-graph.synthetic-state.v1";
const initial = structuredClone(fixtureBootstrap.project) as GraphProject; initial.locks = []; initial.production_design = emptyProductionDesign();
initial.scenes[0].duration = { num: "5", den: "1" }; initial.deliverables[0].frame_rate = { num: "30", den: "1" }; initial.deliverables[0].included_scene_ids = [];
let project = initial, saved = structuredClone(initial), requests: FixtureRequest[] = [], mounted = true, deferred = false, blocked = false, playhead = 0;
const stored = sessionStorage.getItem(key); if (stored) ({ project, saved, requests } = JSON.parse(stored) as { project: GraphProject; saved: GraphProject; requests: FixtureRequest[] });
const pending = new Map<number, { resolve(project: Project): void; reject(error: unknown): void; observed?: GraphProject }>();
const contexts: Array<{ value: GraphAuthoringContext; resolve(value: GraphAuthoringContext): void }> = [], reviews: ReviewAnchor[] = [];
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
function syntheticResult(request: FixtureRequest): GraphProject {
  const next = structuredClone(request.source) as GraphProject; next.revision++; next.production_design ??= emptyProductionDesign();
  const graphs = next.production_design.semantic_graphs ?? [], change = request.change;
  next.production_design.semantic_graphs = change.type === "remove_semantic_graph" ? graphs.filter(g => g.id !== change.graph_id)
    : [...graphs.filter(g => g.id !== change.graph.id), structuredClone(change.graph)];
  return next;
}
function context(source: Project, sceneId: string, profileId: string): GraphAuthoringContext {
  return { schema: "motionwright.semantic-graph-authoring-context/1", source_snapshot: graphSourceSnapshot(source), scene_id: sceneId, variant_profile_ref: profileId,
    component_version: 1, native_profile: "mw-manim-semantic-graph-v1", font_weight: 400, font_selection: { primary_family: "Instrument Sans Variable", mono_family: "IBM Plex Mono" },
    availability: blocked ? "blocked" : "ready", blocking_reasons: blocked ? ["native_graph_profile_unavailable"] : [], dependencies: [] };
}
const commit = (change: SemanticGraphChange, requestId: string) => gate.run(project, () => project, async source => {
  // Capture the actual editor-selected original request key, not the fixture counter.
  const original = captureGraphRecoveryRequest(requestId, change);
  const request: FixtureRequest = { id: requests.length + 1, requestId: original.request_id, source: structuredClone(source),
    change: structuredClone(original.change), settled: false, recorded: null }; requests.push(request);
  render(); return new Promise<Project>((resolve, reject) => pending.set(request.id, { resolve, reject }));
}, receipt => { project = structuredClone(receipt) as GraphProject; saved = structuredClone(receipt) as GraphProject; render(); });
const refresh = async () => { project = structuredClone(saved); render(); await paint(); return structuredClone(project); };
function render() {
  sessionStorage.setItem(key, JSON.stringify({ project, saved, requests }));
  flushSync(() => root.render(mounted ? <StrictMode><SemanticGraphWorkbench project={project} scene={project.scenes[0]} displayProfile={project.deliverables[0]} busy={false} playhead={playhead}
    commit={commit} readContext={async (source, sceneId, profileId) => { const value = context(source, sceneId, profileId); return deferred ? new Promise<GraphAuthoringContext>(resolve => contexts.push({ value, resolve })) : value; }}
    outcomeTransport={{
      readProject: async () => structuredClone(saved),
      readHistory: async () => [],
      readOperationRecovery: async (requestId, change): Promise<GraphOperationRecovery> => {
        const original = captureGraphRecoveryRequest(requestId, change);
        const request = requests.find(item => item.requestId === original.request_id);
        if (request && !graphSame(request.change, original.change))
          throw new Error("Original synthetic Graph operation identity changed");
        const source = original.change.type === "upsert_semantic_graph"
          ? original.change.graph.authorship.source_snapshot : original.change.source_snapshot;
        // A missing/unresolved original is UNKNOWN, never an invented rejection
        // or result. Only a retained original historical commit gets recorded.
        return { schema: "motionwright.semantic-graph-operation-recovery/1",
          request_id: original.request_id, source_snapshot: structuredClone(source),
          record: request?.recorded ? { state: "recorded", historical_project: structuredClone(request.recorded) }
            : { state: "outcome_unknown" } };
      },
    }} refreshProject={refresh}
    openReview={anchor => { reviews.push(structuredClone(anchor)); }} /></StrictMode> : null));
}
function find(id: number) { const request = requests.find(item => item.id === id), reply = pending.get(id); if (!request || !reply) throw new Error("Missing pending Graph fixture request " + id); return { request, reply }; }
window.__GRAPH_AUTHORITY__ = {
  project: () => structuredClone(project), saved: () => structuredClone(saved), requests: () => structuredClone(requests), reviews: () => structuredClone(reviews),
  async resolve(id) { const { request, reply } = find(id); pending.delete(id); request.settled = true; saved = structuredClone(reply.observed ?? syntheticResult(request)); request.recorded = structuredClone(saved); reply.resolve(structuredClone(saved)); await paint(); render(); },
  async reject(id, reason, committed = false) { const { request, reply } = find(id); pending.delete(id); request.settled = true;
    if (committed) { saved = structuredClone(reply.observed ?? syntheticResult(request)); request.recorded = structuredClone(saved); }
    reply.reject(new Error(reason)); await paint(); render(); },
  async rejectKnown(id) { const { request, reply } = find(id); pending.delete(id); request.settled = true;
    // Test-only closed error from the same original Promise and retained UUID.
    reply.reject({ schema: "motionwright.graph-mutation-error/1", request_id: request.requestId,
      outcome_known: true, code: "graph_mutation_failed" }); await paint(); render(); },
  async recordLateOriginal(id) { const request = requests.find(item => item.id === id);
    if (!request || !request.settled || request.recorded || !graphSame(saved, request.source))
      throw new Error("Cannot fabricate or replay a later synthetic Graph original");
    // Model delayed observation of the original backend commit without calling
    // another author action. This deliberately synthetic fixture is not Native.
    const historical = syntheticResult(request); request.recorded = structuredClone(historical);
    project = structuredClone(historical); saved = structuredClone(historical); render(); await paint(); },
  async observeOriginal(id) { const { request, reply } = find(id); reply.observed ??= syntheticResult(request); project = structuredClone(reply.observed); saved = structuredClone(reply.observed); request.recorded = structuredClone(saved); render(); await paint(); },
  async publish(value) { project = structuredClone(value); saved = structuredClone(value); render(); await paint(); },
  async refresh() { await refresh(); },
  async unmount() { mounted = false; render(); await paint(); }, async mount() { mounted = true; render(); await paint(); },
  deferContext(value) { deferred = value; }, async resolveContext() { const request = contexts.shift(); if (!request) throw new Error("Missing deferred Graph context"); request.resolve(request.value); await paint(); },
  async blockContext(value) { blocked = value; render(); await paint(); },
  async setPlayhead(seconds) { if (!Number.isFinite(seconds)) throw new Error("Invalid fixture playhead"); playhead = seconds; render(); await paint(); },
};
render();
