import { describe, expect, it } from "vitest";
import { applyChange, workflowAction, workflowOverview } from "./api";
import { fixtureProject } from "./fixture";
import { rationalSeconds, seconds } from "./types";

describe("creative project fixture", () => {
  it("uses stable unique scene identities and monotonic timeline positions", () => {
    const ids = new Set(fixtureProject.scenes.map((scene) => scene.id));
    expect(ids.size).toBe(fixtureProject.scenes.length);
    for (let index = 1; index < fixtureProject.scenes.length; index += 1) {
      const previous = fixtureProject.scenes[index - 1];
      const current = fixtureProject.scenes[index];
      expect(seconds(current.start)).toBe(seconds(previous.start) + seconds(previous.duration));
    }
  });

  it("declares portable deliverable aspect ratios", () => {
    expect(fixtureProject.deliverables.map((profile) => profile.name)).toEqual([
      "Master 16:9",
      "Vertical 9:16",
      "Square 1:1",
    ]);
    expect(fixtureProject.deliverables.every((profile) => profile.width > 0 && profile.height > 0)).toBe(true);
    expect(fixtureProject.deliverables.every((profile) => profile.framing_strategy === "replan")).toBe(true);
    expect(fixtureProject.deliverables.every((profile) => profile.frame_rate.num === "30" && profile.frame_rate.den === "1")).toBe(true);
    expect(fixtureProject.deliverables.every((profile) => profile.container === "mp4" && profile.color_space === "rec709")).toBe(true);
  });

  it("never represents project truth only as color", () => {
    expect(["current", "stale", "unknown"]).toContain(fixtureProject.state);
    expect(fixtureProject.locks[0].kind).toBe("timing");
  });

  it("keeps semantic canvas and stored alternatives in the same versioned project model", () => {
    const first = fixtureProject.scenes[0];
    expect(first.camera.zoom).toBeGreaterThan(0);
    expect(first.nodes[0].coordinate_space).toBe("project_pixels");
    expect(first.nodes[0].property_locks).toEqual([]);

    const proposalSet = fixtureProject.proposal_sets[0];
    expect(proposalSet.proposals).toHaveLength(3);
    expect(new Set(proposalSet.proposals.map((proposal) => proposal.structure.join(" > "))).size).toBe(3);
    expect(proposalSet.base_revision + 1).toBe(fixtureProject.revision);
  });

  it("does not fake measured audio in the browser fixture", () => {
    expect(fixtureProject.audio.voice_tracks).toEqual([]);
    expect(fixtureProject.audio.transcript).toEqual([]);
    expect(fixtureProject.audio.cues.length).toBeGreaterThan(0);
    expect(fixtureProject.audio.cues.every((cue) =>
      cue.evidence === "manual" && cue.source_segment_id === null
    )).toBe(true);
  });

  it("reduces UI time values before they cross the application boundary", () => {
    expect(rationalSeconds(8)).toEqual({ num: "8", den: "1" });
    expect(rationalSeconds(1.5)).toEqual({ num: "3", den: "2" });
    expect(rationalSeconds(0)).toEqual({ num: "0", den: "1" });
    expect(() => rationalSeconds(Number.NaN)).toThrow("time must be finite");
  });

  it("keeps visual language policy explicit and versioned", () => {
    expect(fixtureProject.visual_language.version).toBe(1);
    expect(fixtureProject.visual_language.anti_slop_rules).toContain("No glassmorphism");
    expect(fixtureProject.visual_language.motion_grammar[0].reduced_motion).toBe("static_equivalent");
  });

  it("persists typed canvas keyframes with deterministic replacement and bounds", async () => {
    const project = structuredClone(fixtureProject);
    project.locks = [];
    const scene = project.scenes[0];
    const node = scene.nodes[0];

    const first = await applyChange(project, {
      type: "set_canvas_keyframe",
      scene_id: scene.id,
      node_id: node.id,
      keyframe: {
        at: rationalSeconds(0.5),
        property: "opacity",
        value: 0.6,
        interpolation: "ease_in_out",
      },
    });
    expect(first.scenes[0].nodes[0].keyframes).toEqual([
      {
        at: { num: "1", den: "2" },
        property: "opacity",
        value: 0.6,
        interpolation: "ease_in_out",
      },
    ]);

    const replaced = await applyChange(first, {
      type: "set_canvas_keyframe",
      scene_id: scene.id,
      node_id: node.id,
      keyframe: {
        at: rationalSeconds(0.5),
        property: "opacity",
        value: 0.35,
        interpolation: "hold",
      },
    });
    expect(replaced.scenes[0].nodes[0].keyframes).toHaveLength(1);
    expect(replaced.scenes[0].nodes[0].keyframes[0].value).toBe(0.35);
    expect(replaced.scenes[0].nodes[0].keyframes[0].interpolation).toBe("hold");

    await expect(applyChange(replaced, {
      type: "set_scene_duration",
      scene_id: scene.id,
      duration: rationalSeconds(0.5),
    })).rejects.toThrow("strand an authored keyframe");

    const removed = await applyChange(replaced, {
      type: "remove_canvas_keyframe",
      scene_id: scene.id,
      node_id: node.id,
      at: rationalSeconds(0.5),
      property: "opacity",
    });
    expect(removed.scenes[0].nodes[0].keyframes).toEqual([]);
  });

  it("does not fabricate canonical workflow evidence in browser mode", async () => {
    const overview = await workflowOverview(fixtureProject);
    expect(overview.status).toBe("browser_demo");
    expect(overview.connection_identity).toBeNull();
    expect(overview.traces).toEqual({ traces: [] });
    expect(overview.candidates).toEqual({ candidates: [] });
    expect(overview.patterns).toEqual({ patterns: [] });
    expect(overview.suggestions).toEqual({ suggestions: [] });
    expect(overview.proposals).toEqual({ proposals: [] });
    expect(overview.promotions).toEqual({ promotions: [] });
    await expect(
      workflowAction(fixtureProject, "promote", {
        candidate_id: "candidate-not-live",
        slug: "not-live",
      }),
    ).rejects.toThrow("desktop runtime");
  });

});
