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
});
