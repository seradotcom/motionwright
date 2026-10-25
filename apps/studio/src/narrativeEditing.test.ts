import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { assertEditorialReceipt, assessEditorialPreferences, emptyNarrativeEditing, inspectEditorialReorder,
  narrativeEditChange, narrativeEditingSource, parseEditorialDuration, sameEditorialValue } from "./narrativeEditing";
import type { Project } from "./types";

const id = (n: number) => "018f0000-0000-7000-8000-" + String(n).padStart(12, "0");
function source(): Project {
  const project = structuredClone(fixtureProject); project.schema_version = 2; project.locks = [];
  project.narrative = { premise: "Independent unit input", protected_sections: [], beats: [1, 2, 3, 4].map(n => ({ id: id(700 + n),
    label: String(n), objective: "Comprehension " + n, audience_takeaway: "Meaning " + n, claim_ids: [], preferred_duration: null })), editing: emptyNarrativeEditing() };
  project.scenes = project.scenes.slice(0, 2); project.scenes[0].start = { num: "0", den: "1" }; project.scenes[0].duration = { num: "6", den: "1" };
  project.scenes[1].start = { num: "6", den: "1" }; project.scenes[1].duration = { num: "6", den: "1" };
  return project;
}
describe("Editorial model comparisons — synthetic input, no service projection", () => {
  it("reorders original editorial UUIDs and changes only the affected declared relationship", () => {
    const project = source(), beats = project.narrative.beats, state = project.narrative.editing!;
    state.relations = [{ id: id(801), from_beat_id: beats[0].id, to_beat_id: beats[1].id, kind: "adjacent", reason: "Meaning depends on order" },
      { id: id(802), from_beat_id: beats[2].id, to_beat_id: beats[3].id, kind: "precedes", reason: "Independent" }];
    state.bindings = [{ id: id(901), beat_id: beats[0].id, target: { kind: "scene", scene_id: project.scenes[0].id } }];
    const before = structuredClone(project), result = inspectEditorialReorder(project, [beats[1].id, beats[0].id, beats[2].id, beats[3].id]);
    expect(result.affected_relations.map(row => row.relation_id)).toEqual([id(801)]);
    expect(result.conflicting_relation_ids).toEqual([id(801)]); expect(result.affected_binding_ids).toEqual([id(901)]);
    expect(result.unbound_moved_beat_ids).toEqual([beats[1].id]); expect(project).toEqual(before);
  });
  it("rejects missing, duplicate and foreign reorder identities", () => {
    const project = source(), ids = project.narrative.beats.map(beat => beat.id);
    expect(() => inspectEditorialReorder(project, ids.slice(1))).toThrow(/exactly once/);
    expect(() => inspectEditorialReorder(project, [ids[0], ids[0], ids[2], ids[3]])).toThrow(/exactly once/);
    expect(() => inspectEditorialReorder(project, [ids[0], ids[1], ids[2], id(999)])).toThrow(/exactly once/);
  });
  it("reports existing order conflicts separately from newly affected relations", () => {
    const project = source(), ids = project.narrative.beats.map(beat => beat.id);
    project.narrative.editing!.relations = [{ id: id(801), from_beat_id: ids[3], to_beat_id: ids[2], kind: "precedes", reason: "Already conflicting" }];
    const result = inspectEditorialReorder(project, [ids[1], ids[0], ids[2], ids[3]]);
    expect(result.affected_relations).toEqual([]); expect(result.conflicting_relation_ids).toEqual([id(801)]);
  });
  it("unions overlapping whole-scene and SceneBeat bindings without double counting", () => {
    const project = source(), beat = project.narrative.beats[0], scene = project.scenes[0];
    scene.beats = [{ id: id(902), label: "Overlap", objective: "Same allocation", start: { num: "1001", den: "30000" }, duration: { num: "1", den: "3" } }];
    project.narrative.editing!.bindings = [{ id: id(901), beat_id: beat.id, target: { kind: "scene", scene_id: scene.id } },
      { id: id(903), beat_id: beat.id, target: { kind: "scene_beat", scene_id: scene.id, scene_beat_id: id(902) } }];
    const result = assessEditorialPreferences(project)[0]; expect(result.allocated_duration).toEqual({ num: "6", den: "1" }); expect(result.binding_ids).toEqual([id(901), id(903)]);
  });
  it("retains an exact fractional allocation and preferred delta", () => {
    const project = source(), beat = project.narrative.beats[0], scene = project.scenes[0];
    scene.beats = [{ id: id(902), label: "Exact", objective: "No rounded authority", start: { num: "0", den: "1" }, duration: { num: "1001", den: "30000" } }];
    beat.preferred_duration = { num: "1", den: "30" };
    project.narrative.editing!.bindings = [{ id: id(901), beat_id: beat.id, target: { kind: "scene_beat", scene_id: scene.id, scene_beat_id: id(902) } }];
    const result = assessEditorialPreferences(project)[0]; expect(result.allocated_duration).toEqual({ num: "1001", den: "30000" }); expect(result.preferred_delta).toEqual({ num: "1", den: "30000" });
  });
  it("unbound beats have unknown allocation even with contradictory authored preferences", () => {
    const project = source(), beat = project.narrative.beats[0]; beat.preferred_duration = { num: "1", den: "3" };
    project.narrative.editing!.minimum_holds = [{ beat_id: beat.id, duration: { num: "1", den: "2" } }];
    const result = assessEditorialPreferences(project)[0]; expect(result.allocated_duration).toBeNull(); expect(result.minimum_shortfall).toBeNull(); expect(result.preferred_delta).toBeNull(); expect(result.contradictory_preferences).toBe(true);
  });
  it("distinguishes a minimum-hold shortfall from a preferred-duration difference", () => {
    const project = source(), beat = project.narrative.beats[0]; beat.preferred_duration = { num: "13", den: "2" };
    project.narrative.editing!.bindings = [{ id: id(901), beat_id: beat.id, target: { kind: "scene", scene_id: project.scenes[0].id } }];
    project.narrative.editing!.minimum_holds = [{ beat_id: beat.id, duration: { num: "7", den: "1" } }];
    const result = assessEditorialPreferences(project)[0]; expect(result.minimum_shortfall).toEqual({ num: "1", den: "1" }); expect(result.preferred_delta).toEqual({ num: "-1", den: "2" });
  });
  it("never replaces a missing realization with zero or heuristic duration", () => {
    const project = source(); project.narrative.editing!.bindings = [{ id: id(901), beat_id: project.narrative.beats[0].id, target: { kind: "scene", scene_id: id(999) } }];
    expect(() => assessEditorialPreferences(project)).toThrow(/Missing realization/);
  });
  it("keeps incomplete raw time unsaveable and parses complete exact fractions", () => {
    expect(() => parseEditorialDuration("1e")).toThrow(); expect(() => parseEditorialDuration("0")).toThrow(/greater than zero/);
    expect(parseEditorialDuration("1001/30000")).toEqual({ num: "1001", den: "30000" }); expect(parseEditorialDuration("", true)).toBeNull();
    expect(parseEditorialDuration("9007199254740993/30000")).toEqual({ num: "3002399751580331", den: "10000" });
  });
  it("compares receipt JSON structurally instead of relying on object key order", () => {
    expect(sameEditorialValue({ kind: "scene", scene_id: id(1) }, { scene_id: id(1), kind: "scene" })).toBe(true);
    expect(sameEditorialValue([id(1), id(2)], [id(2), id(1)])).toBe(false);
  });
  it("requires exact original-response authority and confirmed target payload", () => {
    const project = source(), change = narrativeEditChange(project, { kind: "set_script", script_id: id(905), expected_version: null, text: "Original author text" });
    const before = { source: narrativeEditingSource(project), beatIds: project.narrative.beats.map(beat => beat.id), scriptJson: "null" };
    const receipt = structuredClone(project); receipt.revision++;
    receipt.narrative.editing!.script = { id: id(905), version: 1, text: "Original author text", sha256: "a".repeat(64), approval: null, last_release: null };
    assertEditorialReceipt(change, before, receipt);
    for (const dimension of ["id", "generation", "active_branch"] as const) { const foreign = structuredClone(receipt); foreign[dimension] = id(999); expect(() => assertEditorialReceipt(change, before, foreign)).toThrow(/foreign/); }
    const advanced = structuredClone(receipt); advanced.revision++; expect(() => assertEditorialReceipt(change, before, advanced)).toThrow(/advanced/);
    receipt.narrative.editing!.script!.text = "Someone else's text"; expect(() => assertEditorialReceipt(change, before, receipt)).toThrow(/exact requested/);
  });
});
