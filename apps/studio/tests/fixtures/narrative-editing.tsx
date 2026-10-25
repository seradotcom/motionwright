import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import NarrativeEditingPanel from "../../src/NarrativeEditingPanel";
import { fixtureProject } from "../../src/fixture";
import { emptyNarrativeEditing, inspectEditorialReorder, narrativeEditingSource, narrativeEditingState } from "../../src/narrativeEditing";
import { ProjectMutationGate } from "../../src/projectMutation";
import type { NarrativeEditingChange } from "../../src/narrativeEditing";
import type { Change, Project, ProjectEvent } from "../../src/types";

// This file supplies adversarial presentation inputs, never a product backend.
// In particular it cannot solve timing, apply duration or exercise SQLite.
export interface EditorialFixtureRequest { id: number; source: Project; change: NarrativeEditingChange; settled: boolean }
export interface EditorialFixtureHarness {
  project(): Project;
  requests(): EditorialFixtureRequest[];
  setScope(field: "id" | "generation" | "active_branch", value: string): Promise<void>;
  observeOriginal(id: number): Promise<void>;
  resolve(id: number): Promise<void>;
  reject(id: number, reason: string): Promise<void>;
  unmount(): Promise<void>;
  mount(): Promise<void>;
}
declare global {
  interface Window {
    __EDITORIAL_FIXTURE__: EditorialFixtureHarness;
    __EDITORIAL_RESTORE_STORAGE__?: () => void;
    __EDITORIAL_RELEASE_FILE__?: (raw: string) => void;
  }
}
const syntheticId = (n: number) => "018f0000-0000-7000-8000-" + String(n).padStart(12, "0");
const initial = structuredClone(fixtureProject);
initial.locks = [];
initial.narrative = { premise: "Synthetic editorial fixture", protected_sections: [], editing: emptyNarrativeEditing(),
  beats: ["Question", "Evidence", "Decision", "Aftercare"].map((label, i) => ({ id: syntheticId(810 + i), label,
    objective: "Synthetic objective " + label, audience_takeaway: "Synthetic takeaway " + label, claim_ids: [],
    preferred_duration: { num: String(3 + i), den: "1" } })) };
const fixtureKey = "TEST-ONLY.editorial.synthetic-props.v1";
const stored = sessionStorage.getItem(fixtureKey);
let { project, events } = stored ? JSON.parse(stored) as { project: Project; events: ProjectEvent[] }
  : { project: initial, events: [] as ProjectEvent[] };
const host = document.getElementById("editorial-root");
if (!host) throw new Error("The editorial fixture root is missing.");
const root = createRoot(host), coordinator = new ProjectMutationGate();
const requests: EditorialFixtureRequest[] = [];
const pending = new Map<number, { resolve(value: Project): void; reject(error: Error): void; observed?: Project }>();
let mounted = true;
const paint = () => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
const commit = (change: Change): Promise<Project> => {
  if (change.type !== "edit_narrative" && change.type !== "add_narrative_beat")
    return Promise.reject(new Error("The component fixture supports only explicit editorial scheduling inputs."));
  return coordinator.run(project, () => project, async source => {
    const request: EditorialFixtureRequest = { id: requests.length + 1, source: structuredClone(source), change: structuredClone(change), settled: false };
    requests.push(request);
    return new Promise<Project>((resolve, reject) => pending.set(request.id, { resolve, reject }));
  }, receipt => { project = receipt; render(); });
};
function render() {
  sessionStorage.setItem(fixtureKey, JSON.stringify({ project, events }));
  flushSync(() => root.render(mounted ? <StrictMode><p data-testid="synthetic-revision">Synthetic props r{project.revision}</p>
    <NarrativeEditingPanel project={project} commit={commit} outcomeTransport={{
      readProject: async id => { if (id !== project.id) throw new Error("The synthetic project changed."); return structuredClone(project); },
      readHistory: async (_project, after, limit) => structuredClone(events.filter(event => event.revision > after).slice(0, limit)),
    }} /></StrictMode> : null));
}
const sha256 = async (raw: string) => [...new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(raw)))]
  .map(byte => byte.toString(16).padStart(2, "0")).join("");
/** Build only an explicitly synthetic original response. The real service suite
 * separately proves every domain decision; this switch is not imported by App. */
async function syntheticReceipt(request: EditorialFixtureRequest): Promise<Project> {
  const p = structuredClone(request.source), change = request.change;
  p.narrative.editing ??= emptyNarrativeEditing();
  const state = p.narrative.editing;
  if (change.type === "add_narrative_beat") {
    const { type: _type, ...fields } = change;
    p.narrative.beats.push({ id: crypto.randomUUID(), ...fields });
  } else {
    const edit = change.edit;
    switch (edit.kind) {
      case "set_script": state.script = { id: edit.script_id, version: (edit.expected_version ?? 0) + 1,
        text: edit.text, sha256: await sha256(edit.text), approval: null, last_release: state.script?.last_release ?? null }; break;
      case "update_beat": p.narrative.beats = p.narrative.beats.map(beat => beat.id === edit.beat.id ? structuredClone(edit.beat) : beat); break;
      case "set_minimum_hold": state.minimum_holds = state.minimum_holds.filter(hold => hold.beat_id !== edit.beat_id);
        if (edit.duration) state.minimum_holds.push({ beat_id: edit.beat_id, duration: edit.duration }); break;
      case "set_beat_protection": p.narrative.protected_sections = p.narrative.protected_sections.filter(section => section.beat_id !== edit.beat_id);
        if (edit.protected) p.narrative.protected_sections.push({ beat_id: edit.beat_id, reason: edit.reason }); break;
      case "reorder": state.last_reorder = inspectEditorialReorder(request.source, edit.ordered_ids);
        p.narrative.beats = edit.ordered_ids.map(id => { const beat = p.narrative.beats.find(value => value.id === id); if (!beat) throw new Error("Missing synthetic beat"); return beat; }); break;
      case "upsert_binding": state.bindings = [...state.bindings.filter(binding => binding.id !== edit.binding.id), structuredClone(edit.binding)]; break;
      case "remove_binding": state.bindings = state.bindings.filter(binding => binding.id !== edit.binding_id); break;
      case "upsert_relation": state.relations = [...state.relations.filter(relation => relation.id !== edit.relation.id), structuredClone(edit.relation)]; break;
      case "remove_relation": state.relations = state.relations.filter(relation => relation.id !== edit.relation_id); break;
      case "approve_script": {
        const script = state.script; if (!script) throw new Error("Missing synthetic script");
        script.approval = { id: edit.approval_id, script_id: script.id, script_version: script.version, script_sha256: script.sha256,
          source: narrativeEditingSource(request.source), reviewer: edit.reviewer, reason: edit.reason }; break;
      }
      case "release_script_approval": {
        const script = state.script; if (!script?.approval) throw new Error("Missing synthetic approval");
        script.last_release = { approval: script.approval, source: narrativeEditingSource(request.source), reviewer: edit.reviewer, reason: edit.reason }; script.approval = null; break;
      }
      case "propose_duration": {
        const scene = p.scenes.find(value => value.id === edit.scene_id); if (!scene) throw new Error("Missing synthetic scene");
        state.timing_proposals.push({ id: edit.proposal_id, source: narrativeEditingSource(request.source), scene_id: scene.id,
          original_duration: scene.duration, requested_duration: edit.requested_duration, reason: edit.reason,
          script_snapshot: structuredClone(narrativeEditingState(request.source).script), decision: null,
          feasibility: { command_allowed: false, rejection: "SYNTHETIC_COMPONENT_FIXTURE: no domain feasibility was evaluated",
            scene_changes: [], before_preferences: [], after_preferences: null, timing_locks_present: [] } }); break;
      }
      case "decide_duration": {
        if (edit.decision === "apply_duration") throw new Error("Only the real StudioService tests may exercise duration application.");
        const proposal = state.timing_proposals.find(value => value.id === edit.proposal_id); if (!proposal) throw new Error("Missing synthetic proposal");
        proposal.decision = { decision: edit.decision, source: narrativeEditingSource(request.source), reason: edit.reason }; break;
      }
    }
  }
  p.revision = request.source.revision + 1;
  return p;
}
function find(id: number) {
  const request = requests.find(value => value.id === id), reply = pending.get(id);
  if (!request || !reply) throw new Error("No pending editorial fixture reply " + id);
  return { request, reply };
}
function record(request: EditorialFixtureRequest, receipt: Project) {
  if (!events.some(event => event.revision === receipt.revision)) events.push({ revision: receipt.revision,
    change: JSON.parse(JSON.stringify(request.change)), created_at: "2026-10-21T00:00:00Z" });
}
window.__EDITORIAL_FIXTURE__ = {
  project: () => structuredClone(project), requests: () => structuredClone(requests),
  async setScope(field, value) { project = { ...project, [field]: value }; render(); await paint(); },
  async observeOriginal(id) {
    const { request, reply } = find(id); reply.observed ??= await syntheticReceipt(request);
    record(request, reply.observed); project = structuredClone(reply.observed); render(); await paint();
  },
  async resolve(id) {
    const { request, reply } = find(id); pending.delete(id); request.settled = true;
    try { const receipt = reply.observed ?? await syntheticReceipt(request); record(request, receipt); reply.resolve(receipt); }
    catch (error) { reply.reject(error instanceof Error ? error : new Error(String(error))); }
    await paint();
  },
  async reject(id, reason) { const { request, reply } = find(id); pending.delete(id); request.settled = true; reply.reject(new Error(reason)); await paint(); },
  async unmount() { mounted = false; render(); await paint(); },
  async mount() { mounted = true; render(); await paint(); },
};
render();
