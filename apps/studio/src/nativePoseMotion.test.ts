import { describe, expect, it } from "vitest";
import { nativePoseEdit, savedNativePoseDraft, type NativePoseDraft } from "./nativePoseDraft";
import { fixtureProject } from "./fixture";
import { applyChange } from "./api";
import { previewTransform } from "./canvasMotion";

function source() {
  const project = structuredClone(fixtureProject), scene = project.scenes[0], node = scene.nodes[0];
  scene.beats = []; scene.renderer = "motion-canvas"; project.locks = [];
  if (project.production_design) project.production_design.camera_reframes = [];
  Object.assign(node, { kind: "shape", parent_id: null, coordinate_space: "project_pixels",
    x: 1100, y: 430, width: 360, height: 220, rotation_deg: 0, opacity: 1,
    keyframes: [], property_locks: [], relations: [] });
  node.style.blend_mode = "normal";
  const draft: NativePoseDraft = {
    start: { x: 1000, y: 410, width: 280, height: 160, rotation_deg: -18 },
    endFrame: 30, curve: "ease_out_cubic", fade: "out", fadeCurve: "linear",
  };
  return { project, scene, node, profile: project.deliverables[0], draft };
}

describe("one complete editable Native pose", () => {
  it.each(["linear", "ease_out_cubic", "hold"] as const)("saves all channels with %s geometry and an independent fade", async curve => {
    const { project, scene, node, profile, draft } = source();
    draft.curve = curve;
    const edit = nativePoseEdit(project, scene, node, profile, draft);
    expect(edit.issue).toBeNull(); expect(edit.keys).toHaveLength(12);
    const original = structuredClone(node);
    const result = await applyChange(project, { type: "set_canvas_motion_keyframes", scene_id: scene.id, node_id: node.id, keyframes: edit.keys! });
    expect(result.revision).toBe(project.revision + 1);
    const saved = result.scenes[0].nodes[0];
    expect(saved.keyframes).toHaveLength(12);
    expect(node).toEqual(original);
    expect({ ...saved, keyframes: [] }).toEqual(original);
    expect(previewTransform(saved, 0)).toEqual({ ...draft.start, opacity: 1 });
    expect(previewTransform(saved, 1)).toEqual({ x: node.x, y: node.y, width: node.width, height: node.height, rotation_deg: 0, opacity: 0 });
    expect(previewTransform(saved, 0.5).opacity).toBe(0.5);
    if (curve === "hold") expect(previewTransform(saved, 0.999).width).toBe(draft.start.width);
    expect(savedNativePoseDraft(saved, profile)).toEqual(draft);
    const reopenedDraft = { ...savedNativePoseDraft(saved, profile)!, endFrame: 45, fade: "none" as const };
    const replacement = nativePoseEdit(result, result.scenes[0], saved, profile, reopenedDraft);
    expect(replacement.keys).toHaveLength(10);
    const retimed = await applyChange(result, { type: "set_canvas_motion_keyframes", scene_id: scene.id, node_id: node.id, keyframes: replacement.keys! });
    const updated = retimed.scenes[0].nodes[0];
    expect(updated.keyframes).toHaveLength(10);
    expect(updated.keyframes.some(key => key.at.num === "1" && key.at.den === "1")).toBe(false);
    expect(savedNativePoseDraft(updated, profile)?.endFrame).toBe(45);
    expect(previewTransform(updated, 1.5)).toEqual({ x: node.x, y: node.y, width: node.width, height: node.height, rotation_deg: 0, opacity: 1 });
  });
  it("keeps fractional FPS exact and admits a shape inside a static Clip", () => {
    const { project, scene, node, profile, draft } = source();
    const parent = structuredClone(node); Object.assign(parent, { id: "source-clip", kind: "clip_group" });
    scene.nodes.push(parent); node.parent_id = parent.id;
    profile.frame_rate = { num: "30000", den: "1001" };
    const edit = nativePoseEdit(project, scene, node, profile, draft);
    expect(edit.keys?.filter(key => key.at.num !== "0").map(key => key.at))
      .toEqual(Array(6).fill({ num: "1001", den: "1000" }));
    parent.rotation_deg = 5;
    expect(nativePoseEdit(project, scene, node, profile, draft).issue).toMatch(/root Clip/);
  });
  it("rejects a locked last property, duplicate slot or invalid last value before any write", async () => {
    const { project, scene, node, profile, draft } = source();
    const keys = nativePoseEdit(project, scene, node, profile, draft).keys!;
    const command = { type: "set_canvas_motion_keyframes" as const, scene_id: scene.id, node_id: node.id, keyframes: keys };
    node.property_locks = ["opacity"];
    expect(nativePoseEdit(project, scene, node, profile, draft).issue).toMatch(/Unlock/);
    await expect(applyChange(project, command)).rejects.toThrow(/locked/);
    node.property_locks = [];
    await expect(applyChange(project, { ...command, keyframes: [...keys, keys[0]] })).rejects.toThrow(/duplicate/);
    await expect(applyChange(project, { ...command, keyframes: [...keys.slice(0, -1), { ...keys.at(-1)!, value: 1.1 }] })).rejects.toThrow(/bounds/);
    expect(node.keyframes).toEqual([]);
    const saved = await applyChange(project, command);
    const savedNode = saved.scenes[0].nodes[0];
    savedNode.property_locks = ["opacity"];
    const geometryOnly = keys.filter(key => key.property !== "opacity");
    await expect(applyChange(saved, { ...command, keyframes: geometryOnly })).rejects.toThrow(/locked/);
    expect(savedNode.keyframes).toHaveLength(12);
    savedNode.property_locks = [];
    savedNode.keyframes.push({ ...keys[0], at: { num: "1", den: "2" } });
    expect(savedNativePoseDraft(savedNode, profile)).toBeNull();
    expect(nativePoseEdit(saved, saved.scenes[0], savedNode, profile, draft).issue).toMatch(/independent or additional/);
  });
});
