import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { inspectBranchCombination, type BranchCombinationInput } from "./branchCombinationReview";

function sample() {
  const project = structuredClone(fixtureProject);
  const donor = project.branches.find(branch => branch.id !== project.active_branch)!;
  const state = project.branch_workspaces.find(workspace => workspace.branch_id === donor.id)!.current_state;
  const scene = project.scenes[0], node = scene.nodes[0];
  const other = state.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === node.id)!;
  other.x += 100;
  other.keyframes = [{ at: { num: "1", den: "1" }, property: "x", value: other.x + 200, interpolation: "linear" }];
  const raw: BranchCombinationInput = {
    candidateId: "018f0000-0000-7000-8000-000000000999", name: "Combined study",
    rationale: "Retain the active composition and transfer the authored motion.",
    donorBranchId: donor.id, sceneId: scene.id, picks: [{ node_id: node.id, field: "motion" }],
  };
  return { project, raw, state, other };
}
describe("canonical branch combination preparation", () => {
  it("retains both exact parents and sends choices without inventing values", () => {
    const {project,raw}=sample(), before=structuredClone(project), retained=structuredClone(raw);
    const result=inspectBranchCombination(project,before,raw);
    expect(result.change.input.source).toEqual({ resource:"project:"+project.id, generation:project.generation, revision:project.revision });
    expect(result.change.input.candidate_branch_id).toBe(raw.candidateId);
    expect(result.change.input.picks).toEqual(raw.picks);
    expect(result.requiresCanonicalPreview).toBe(true);
    expect(result.nativeRenderObserved).toBe(false);
    expect(project).toEqual(before); expect(raw).toEqual(retained);
  });
  it("refuses a changed complete project even when its revision number is reused", () => {
    const {project,raw}=sample(), observed=structuredClone(project);
    project.brief.objective+=" changed";
    expect(()=>inspectBranchCombination(project,observed,raw)).toThrow("source changed");
  });
  it("keeps unfinished names and stable candidate identity untouched", () => {
    const {project,raw}=sample(); raw.name="á".repeat(61); const before=structuredClone(raw);
    expect(()=>inspectBranchCombination(project,project,raw)).toThrow("120 UTF-8");
    expect(raw).toEqual(before);
    raw.name=" "; expect(()=>inspectBranchCombination(project,project,raw)).toThrow();
    expect(raw.candidateId).toBe(before.candidateId);
  });
  it("requires compatible voice, frame window and selected node identity context", () => {
    const audio=sample(); audio.state.audio.mix.voice_gain_db+=1;
    expect(()=>inspectBranchCombination(audio.project,audio.project,audio.raw)).toThrow("same brief");
    const timing=sample(); timing.state.scenes[0].duration={num:"999",den:"1"};
    expect(()=>inspectBranchCombination(timing.project,timing.project,timing.raw)).toThrow("same scene window");
    const locks=sample(); locks.other.property_locks.push("position");
    expect(()=>inspectBranchCombination(locks.project,locks.project,locks.raw)).toThrow("identity context or locks");
  });
  it("requires a real structural choice and rejects duplicate picks", () => {
    const {project,raw}=sample(); raw.picks[0].field="style";
    expect(()=>inspectBranchCombination(project,project,raw)).toThrow("changed transform or motion");
    raw.picks[0].field="motion"; raw.picks.push({...raw.picks[0]});
    expect(()=>inspectBranchCombination(project,project,raw)).toThrow("twice");
  });
});
