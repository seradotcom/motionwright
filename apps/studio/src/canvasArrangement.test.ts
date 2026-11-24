import { describe, expect, it } from "vitest";
import { applyChange, previewCreativePatch } from "./api";
import { fixtureProject } from "./fixture";
import { arrangeCanvasFrames } from "./canvasArrangement";
import { createScopedDraft, decodeScopedDraft, draftPatch, encodeScopedDraft, queueDraftEdit,
  rebaseScopedDraft, scopedDraftScope, selectDraftNode, stageDraftArrangement,
  unqueuedInputCount, updateDraftArrangement, updateDraftTransformInput } from "./scopedChangeDraft";
import { SCOPED_MOTION_PROPERTIES } from "./scopedPaintMotion";
import { previewTransform } from "./canvasMotion";
import type { ArrangementAction } from "./canvasArrangement";
import type { CanvasKeyframe, Project } from "./types";

function source() {
  const project: Project = structuredClone(fixtureProject);
  project.schema_version = 2; project.branch_workspaces = []; project.reviews = []; project.merges = []; project.locks = [];
  project.production_design = { plan: null, heroes: [], capsules: [], patches: [] };
  project.scenes = project.scenes.slice(0, 1);
  const scene = project.scenes[0]; scene.status = "draft"; scene.beats = [];
  scene.start = { num: "0", den: "1" }; scene.duration = { num: "6", den: "1" };
  scene.camera = { center_x: 960, center_y: 540, zoom: 1, rotation_deg: 0, safe_margin: .05 };
  const original = scene.nodes[0];
  scene.nodes = [[100,100,100,60], [280,210,200,80], [600,80,80,100]].map(([x,y,width,height], index) => ({
    ...structuredClone(original), id: "30000000-0000-4000-8000-00000000000" + (index + 1),
    name: "Frame " + (index + 1), kind: "shape", text: null, x, y, width, height, rotation_deg: 0, opacity: 1,
    z_index: index, parent_id: null, relations: [], property_locks: [], keyframes: [],
  }));
  return { project, scene, ids: scene.nodes.map(node => node.id) };
}
function motion(node: ReturnType<typeof source>["scene"]["nodes"][number]): CanvasKeyframe[] {
  return [0,1].flatMap(time => SCOPED_MOTION_PROPERTIES.map(property => ({
    at: { num: String(time), den: "1" }, property,
    value: !time && property === "x" ? node.x - 40 : !time && property === "opacity" ? 0 : node[property],
    interpolation: time && property !== "opacity" ? "ease_out_cubic" : "linear",
  })));
}
function stage(project: Project, action: ArrangementAction = "gaps_x") {
  const scene = project.scenes[0];
  return stageDraftArrangement(updateDraftArrangement(createScopedDraft(project, scene), {
    action, nodeIds: scene.nodes.map(node => node.id),
  }), project, scene);
}
async function after(project: Project, action: ArrangementAction) {
  return (await previewCreativePatch(project, draftPatch(stage(project, action), project.scenes[0].id))).after;
}
describe("Canvas arrangement through the existing scoped change transaction", () => {
  it.each([
    ["align_left", "x", [100,100,100]], ["align_center_x", "x", [340,290,350]],
    ["align_right", "x", [580,480,600]], ["align_top", "y", [80,80,80]],
    ["align_center_y", "y", [155,145,135]], ["align_bottom", "y", [230,210,190]],
  ] as const)("%s uses all selected authored frames and retains their identity and dimensions", async (action, field, expected) => {
    const { project, scene, ids } = source(), frozen = structuredClone(project);
    const changed = new Map((await after(project, action)).map(node => [node.id, node]));
    expect(scene.nodes.map(node => (changed.get(node.id) ?? node)[field])).toEqual(expected);
    expect(project).toEqual(frozen);
    for (const node of scene.nodes) expect(changed.get(node.id) ?? node).toMatchObject({
      id: node.id, width: node.width, height: node.height, rotation_deg: node.rotation_deg, z_index: node.z_index, parent_id: null,
    });
    expect(scene.nodes.map(node => node.id)).toEqual(ids);
  });

  it.each([["gaps_x","x",300],["centers_x","x",295],["gaps_y","y",340],["centers_y","y",350]] as const)(
    "%s distributes complete frame bounds with fixed outer endpoints", async (action, axis, middle) => {
      const { project, scene } = source();
      if (axis === "y") scene.nodes.forEach((node, index) => { node.y = [100,280,600][index]; });
      const changed = new Map((await after(project, action)).map(node => [node.id, node]));
      expect((changed.get(scene.nodes[1].id) ?? scene.nodes[1])[axis]).toBe(middle);
      expect(changed.has(scene.nodes[0].id)).toBe(false);
      expect(changed.has(scene.nodes[2].id)).toBe(false);
    });

  it("translates complete authored paths, then commits, reopens and undoes without changing timing, curves, size or alpha", async () => {
    const { project, scene } = source(), node = scene.nodes[1];
    node.keyframes = motion(node);
    const original = structuredClone(project), draft = stage(project);
    expect(draft.edits.map(edit => edit.kind)).toEqual(["transform","motion"]);
    expect(unqueuedInputCount(draft)).toBe(0);
    const restored = decodeScopedDraft(encodeScopedDraft(draft), scopedDraftScope(project, scene));
    expect(restored).toEqual(draft);
    const saved = await applyChange(project, { type: "apply_creative_patch", patch: draftPatch(restored, scene.id) });
    const translated = saved.scenes[0].nodes[1];
    expect(translated.x).toBe(300);
    for (const time of [0,.5,1,2]) {
      const before = previewTransform(node, time), actual = previewTransform(translated, time);
      expect(actual.x).toBeCloseTo(before.x + 20, 10);
      expect({ ...actual, x: before.x }).toEqual(before);
    }
    expect(translated.keyframes.map(key => ({ at: key.at, property: key.property, interpolation: key.interpolation })))
      .toEqual(node.keyframes.map(key => ({ at: key.at, property: key.property, interpolation: key.interpolation })));
    const reopened = JSON.parse(JSON.stringify(saved)) as Project;
    const undone = await applyChange(reopened, { type: "undo_creative_patch", patch_id: saved.production_design!.patches!.at(-1)!.id });
    expect(undone.scenes[0].nodes).toEqual(original.scenes[0].nodes);
    expect(project).toEqual(original);
  });

  it("keeps animated arrangement compatible with an existing camera instead of validating half of a new pose", async () => {
    let { project, scene } = source();
    scene.nodes.forEach(node => { node.x += 200; node.y += 220; });
    scene.nodes[1].keyframes = motion(scene.nodes[1]);
    project = await applyChange(project, { type: "upsert_camera_reframe", reframe: {
      id: "30000000-0000-4000-8000-000000000200", scene_id: scene.id, component_version: 1,
      start_center_x: 960, start_center_y: 540, end_center_x: 978, end_center_y: 548, duration_frames: 90,
    } });
    const saved = await applyChange(project, { type: "apply_creative_patch", patch: draftPatch(stage(project), scene.id) });
    expect(saved.scenes[0].nodes[1].x).toBe(500);
    expect(saved.production_design!.camera_reframes).toEqual(project.production_design!.camera_reframes);
  });

  it("uses exact quarter-turn frame bounds and leaves rotation authored", () => {
    const { scene, ids } = source(); scene.nodes[0].rotation_deg = 90;
    const proposal = arrangeCanvasFrames(scene, { action: "align_left", nodeIds: ids });
    expect(proposal.edits).toHaveLength(2);
    expect(proposal.edits.filter(edit => edit.kind === "transform").map(edit => edit.transform.x)).toEqual([120,120]);
    expect(scene.nodes[0].rotation_deg).toBe(90);
  });

  it("supports sibling frames inside a static clip and refuses implicit cross-parent conversion", () => {
    const { scene, ids } = source();
    const parent = { ...structuredClone(scene.nodes[0]), id: "30000000-0000-4000-8000-000000000201", name: "Clip",
      kind: "clip_group", x: 40, y: 20, width: 1000, height: 800, z_index: -1 };
    scene.nodes.forEach(node => { node.parent_id = parent.id; }); scene.nodes.unshift(parent);
    expect(arrangeCanvasFrames(scene, { action: "gaps_x", nodeIds: ids }).edits[0]).toMatchObject({ kind: "transform", node_id: ids[1], transform: { x: 300 } });
    scene.nodes[1].parent_id = null;
    expect(() => arrangeCanvasFrames(scene, { action: "gaps_x", nodeIds: ids })).toThrow(/same parent/);
  });

  it("keeps selection preferences through recovery and requires an explicit rebase before capturing changed source", () => {
    const { project, scene, ids } = source();
    const selected = updateDraftArrangement(createScopedDraft(project, scene), { action: "gaps_x", nodeIds: ids });
    const restored = decodeScopedDraft(encodeScopedDraft(selected), scopedDraftScope(project, scene));
    expect(restored.arrangement).toEqual(selected.arrangement);
    const next = structuredClone(project); next.revision++; next.scenes[0].nodes[1].x = 270;
    expect(() => stageDraftArrangement(restored, next, next.scenes[0])).toThrow(/current project revision/);
    const rebased = rebaseScopedDraft(restored, next, next.scenes[0]);
    const staged = stageDraftArrangement(rebased, next, next.scenes[0]);
    expect(staged.origins.find(node => node.id === ids[1])!.x).toBe(270);
    expect(staged.edits[0]).toMatchObject({ kind: "transform", transform: { x: 300 } });
    expect(staged.arrangement?.nodeIds).toEqual([]);
  });

  it("preserves raw movement and queued movement instead of overwriting them with a layout command", () => {
    const { project, scene, ids } = source();
    let draft = selectDraftNode(createScopedDraft(project, scene), scene.nodes[1]);
    draft = updateDraftArrangement(draft, { action: "gaps_x", nodeIds: ids });
    draft = updateDraftTransformInput(draft, "x", "1e");
    const before = structuredClone(draft);
    expect(() => stageDraftArrangement(draft, project, scene)).toThrow(/uncommitted movement/);
    expect(draft).toEqual(before);
    draft = updateDraftTransformInput(draft, "x", "290");
    draft = queueDraftEdit({ ...draft, editor: { ...draft.editor, kind: "transform" } });
    expect(() => stageDraftArrangement(draft, project, scene)).toThrow(/proposed movement/);
  });

  it("rejects missing identities, impossible gaps, controlled relations and locked movement without a partial proposal", () => {
    const { scene, ids } = source();
    expect(() => arrangeCanvasFrames(scene, { action: "gaps_x", nodeIds: [ids[0],ids[1],"missing"] })).toThrow(/no longer/);
    const overlap = structuredClone(scene); overlap.nodes.forEach((node, index) => { node.x = 100 + index * 20; });
    expect(() => arrangeCanvasFrames(overlap, { action: "gaps_x", nodeIds: ids })).toThrow(/cannot fit/);
    const locked = structuredClone(scene); locked.nodes[1].property_locks = ["position"];
    expect(() => arrangeCanvasFrames(locked, { action: "gaps_x", nodeIds: ids })).toThrow(/locked/);
    const linked = structuredClone(scene); linked.nodes[1].relations = [{ id: "relation", kind: "align_left", target_id: ids[0] }];
    expect(() => arrangeCanvasFrames(linked, { action: "gaps_x", nodeIds: ids })).toThrow(/controlled/);
    scene.nodes[0].property_locks = ["position"];
    expect(arrangeCanvasFrames(scene, { action: "gaps_x", nodeIds: ids }).edits).toHaveLength(1);
  });

  it("pairs one frame and complete track in either order while retaining motion locks and refusing ambiguous repeated transforms", async () => {
    let { project, scene } = source();
    scene.nodes.forEach(node => { node.x += 200; node.y += 220; });
    scene.nodes[1].keyframes = motion(scene.nodes[1]);
    project = await applyChange(project, { type: "upsert_camera_reframe", reframe: {
      id: "30000000-0000-4000-8000-000000000202", scene_id: scene.id, component_version: 1,
      start_center_x: 960, start_center_y: 540, end_center_x: 978, end_center_y: 548, duration_frames: 90,
    } });
    const patch = draftPatch(stage(project), scene.id), frozen = structuredClone(project);
    const forward = await previewCreativePatch(project, patch);
    const reverse = await previewCreativePatch(project, { ...patch, edits: [...patch.edits].reverse() });
    expect(reverse).toEqual(forward);
    const locked = structuredClone(project); locked.scenes[0].nodes[1].property_locks = ["size"];
    await expect(previewCreativePatch(locked, patch)).rejects.toThrow(/locked/);
    const repeated = { ...patch, edits: [patch.edits[0],...patch.edits] };
    await expect(previewCreativePatch(project, repeated)).rejects.toThrow(/CameraReframe/);
    const bad = structuredClone(patch), track = bad.edits.find(edit => edit.kind === "motion")!;
    if (track.kind !== "motion") throw new Error("Missing complete track");
    track.keyframes.push(structuredClone(track.keyframes[0]));
    await expect(applyChange(project, { type: "apply_creative_patch", patch: bad })).rejects.toThrow(/duplicate/);
    expect(project).toEqual(frozen);
  });
});
