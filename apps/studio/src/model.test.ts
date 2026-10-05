import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { seconds } from "./types";

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
    expect(fixtureProject.audio.cues).toEqual([]);
  });

});
