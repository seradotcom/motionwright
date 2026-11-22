import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { previewTransform } from "./canvasMotion";
import { fixtureProject } from "./fixture";
import type { Change, MotionInterpolation, Project } from "./types";

async function authoredFadeOut(interpolation: MotionInterpolation): Promise<{ original: Project; saved: Project }> {
  const original = structuredClone(fixtureProject);
  original.scenes[0].beats = [];
  original.scenes[0].nodes[0].keyframes = [];
  original.locks = [];
  const scene = original.scenes[0], node = scene.nodes[0];
  const key = (num: "0" | "1", value: number, ease: MotionInterpolation): Change => ({
    type: "set_canvas_keyframe", scene_id: scene.id, node_id: node.id,
    keyframe: { at: { num, den: "1" }, property: "opacity", value, interpolation: ease },
  });
  const staged = await applyChange(original, key("0", 1, "linear"));
  return { original, saved: await applyChange(staged, key("1", 0, interpolation)) };
}

describe("MW05-E01-01 native-compatible authored FadeOut", () => {
  it("commits an exact editable 1-to-0 linear opacity curve", async () => {
    const { original, saved } = await authoredFadeOut("linear");
    const before = original.scenes[0].nodes[0], node = saved.scenes[0].nodes[0];
    expect(node.keyframes).toEqual([
      { at: { num: "0", den: "1" }, property: "opacity", value: 1, interpolation: "linear" },
      { at: { num: "1", den: "1" }, property: "opacity", value: 0, interpolation: "linear" },
    ]);
    expect(saved.revision).toBe(original.revision + 2);
    expect({ ...node, keyframes: [] }).toEqual({ ...before, keyframes: [] });
    expect(previewTransform(node, 0).opacity).toBe(1);
    expect(previewTransform(node, 0.5).opacity).toBeCloseTo(0.5, 12);
    expect(previewTransform(node, 1).opacity).toBe(0);
    expect(previewTransform(node, 1.5).opacity).toBe(0);
  });

  it("renders the same mathematically exact cubic-out alpha progression in preview", async () => {
    const { original, saved } = await authoredFadeOut("ease_out_cubic");
    const node = saved.scenes[0].nodes[0];
    expect(node.keyframes[1].interpolation).toBe("ease_out_cubic");
    expect(previewTransform(node, 0.25).opacity).toBeCloseTo(0.421875, 12);
    expect(previewTransform(node, 0.5).opacity).toBeCloseTo(0.125, 12);
    expect(previewTransform(node, 0.75).opacity).toBeCloseTo(0.015625, 12);
    expect(previewTransform(node, 1).opacity).toBe(0);
    expect(saved.scenes[0].nodes[0].id).toBe(original.scenes[0].nodes[0].id);
    expect(saved.scenes[0].nodes[0].style).toEqual(original.scenes[0].nodes[0].style);
  });
});
