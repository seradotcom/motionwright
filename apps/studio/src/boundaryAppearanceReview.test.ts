import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { CanvasNode, SceneBoundary, SceneBoundaryEndpoint } from "./types";
import { reviewBoundaryAppearance } from "./boundaryAppearanceReview";

function fixture() {
  const project = structuredClone(fixtureProject);
  const previous = project.scenes[0];
  const from: CanvasNode = { ...structuredClone(previous.nodes[0]), kind: "rectangle", parent_id: null,
    x: 650, y: 350, width: 250, height: 180, rotation_deg: 0, opacity: 1,
    text: null, coordinate_space: "project_pixels" as const, relations: [], keyframes: [] };
  from.style.blend_mode = "normal";
  previous.renderer = "motion-canvas"; previous.beats = []; previous.nodes = [from];
  previous.camera = { center_x: 960, center_y: 540, rotation_deg: 0, zoom: 1, safe_margin: 0.05 };
  const to: CanvasNode = { ...structuredClone(from), id: crypto.randomUUID(), x: 730,
    keyframes: [
      { at: { num: "0", den: "1" }, property: "x" as const, value: 650, interpolation: "linear" as const },
      { at: { num: "1", den: "2" }, property: "x" as const, value: 730, interpolation: "linear" as const },
      { at: { num: "0", den: "1" }, property: "y" as const, value: 350, interpolation: "linear" as const },
      { at: { num: "1", den: "2" }, property: "y" as const, value: 350, interpolation: "linear" as const },
    ] };
  const next = { ...structuredClone(previous), id: crypto.randomUUID(), nodes: [to] };
  project.scenes = [previous, next];
  const profile = project.deliverables[0];
  profile.included_scene_ids = [];
  const endpoint = (scene: typeof previous): SceneBoundaryEndpoint => ({
    nodes: structuredClone(scene.nodes), camera: structuredClone(scene.camera), assets: [],
  });
  const predecessor: SceneBoundary = {
    id: crypto.randomUUID(), version: 1, scene_id: previous.id, input: { kind: "self_contained" },
    definition: { kind: "explicit", source: { project_id: project.id, generation: project.generation,
      branch_id: project.active_branch, authored_revision: project.revision, deliverable_profile_id: profile.id,
      dependencies_sha256: "0".repeat(64), film_options: { frame_rate: { num: 30, den: 1 },
        font_family: "Instrument Sans Variable", mono_font_family: "IBM Plex Mono", role: "mechanism", archetype: "statement" } },
      entry: endpoint(previous), exit: endpoint(previous) },
  };
  const appearance = { predecessor_node_id: from.id, successor_node_id: to.id };
  return { project, previous, next, profile, predecessor, appearance, endpoint, to };
}

describe("authored focal appearance at a cut", () => {
  it("compares the real initial pose rather than the saved terminal position", () => {
    const f = fixture();
    const before = structuredClone(f.project);
    const result = reviewBoundaryAppearance(f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance);
    expect(result.status).toBe("matching_pose");
    expect(result.displacement).toBe(0);
    expect(f.project).toEqual(before);
  });
  it("compares the saved boundary pose when geometry uses smoothstep", () => {
    const f = fixture();
    f.to.keyframes[1].interpolation = "ease_in_out";
    f.to.keyframes[3].interpolation = "ease_in_out";
    const result = reviewBoundaryAppearance(
      f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance,
    );
    expect(result.status).toBe("matching_pose");
    expect(result.displacement).toBe(0);

    f.to.keyframes.push(
      { at: { num: "0", den: "1" }, property: "opacity", value: 1, interpolation: "linear" },
      { at: { num: "1", den: "2" }, property: "opacity", value: 0, interpolation: "ease_in_out" },
    );
    expect(reviewBoundaryAppearance(
      f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance,
    ).status).toBe("unsupported");
  });
  it("reports jumps, stale snapshots, and incompatible delivery order", () => {
    const f = fixture();
    f.to.keyframes[0].value += 25;
    expect(reviewBoundaryAppearance(f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance).status).toBe("loss");
    const entry = f.endpoint(f.next);
    entry.nodes[0].width += 5;
    expect(reviewBoundaryAppearance(f.project, f.next, f.profile, f.predecessor, entry, f.appearance).detail).toMatch(/snapshots/);
    f.profile.included_scene_ids = [f.next.id, f.previous.id];
    expect(reviewBoundaryAppearance(f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance).detail).toMatch(/adjacent/);
  });
  it("accounts for camera translation and refuses an unsupported hierarchy", () => {
    const f = fixture();
    f.next.camera.center_x += 30;
    f.to.keyframes[0].value += 30;
    expect(reviewBoundaryAppearance(f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance).status).toBe("matching_pose");
    f.to.parent_id = crypto.randomUUID();
    expect(reviewBoundaryAppearance(f.project, f.next, f.profile, f.predecessor, f.endpoint(f.next), f.appearance).status).toBe("unsupported");
  });
});
