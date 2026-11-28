import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import type { Change, Project } from "./types";

// The browser implementation is only a parity layer. These assertions are
// paired with domain + real StudioService close/reopen tests in this proposal.
const original = () => {
  const project = structuredClone(fixtureProject);
  project.locks = [];
  return project;
};
const changes = ["duration", "reorder"] as const;
function timingChange(project: Project, kind: typeof changes[number]): Change {
  return kind === "duration"
    ? { type: "set_scene_duration", scene_id: project.scenes[0].id, duration: { num: "8", den: "1" } }
    : { type: "move_scene", scene_id: project.scenes[0].id, to_index: 1 };
}

describe("general scene timing respects affected resource authority", () => {
  for (const kind of changes) for (const resource of ["project", "downstream"] as const) {
    it(`${kind} cannot move ${resource} locked timing and leaves the source exact`, async () => {
      const source = original();
      const locked = await applyChange(source, {
        type: "set_lock", resource: resource === "project" ? "project:" + source.id : "scene:" + source.scenes[1].id,
        kind: "timing", note: "Keep this approved timing exact",
      });
      const before = structuredClone(locked);
      await expect(applyChange(locked, timingChange(locked, kind))).rejects.toThrow("resource is locked");
      expect(locked).toEqual(before);
    });
  }

  it("a lock on an unchanged prefix does not prohibit an unlocked later duration", async () => {
    const source = original();
    const locked = await applyChange(source, { type: "set_lock", resource: "scene:" + source.scenes[0].id, kind: "timing", note: "Prefix" });
    const before = structuredClone(locked);
    const saved = await applyChange(locked, { type: "set_scene_duration", scene_id: locked.scenes[1].id, duration: { num: "10", den: "1" } });
    expect(saved.scenes[0]).toEqual(before.scenes[0]);
    expect(saved.scenes[1].duration).toEqual({ num: "10", den: "1" });
    expect(saved.scenes[2].start).toEqual({ num: "17", den: "1" });
    expect(locked).toEqual(before);
  });

  it("reorders explicit cuts by scene membership and preserves protected delivery timing", async () => {
    const source = original();
    const [first, second, suffix] = source.scenes;
    source.deliverables[0].included_scene_ids = [first.id, second.id, suffix.id];
    source.deliverables[0].protected_scene_ids = [suffix.id];
    source.deliverables[1].included_scene_ids = [first.id, suffix.id];
    const locked = await applyChange(source, { type: "set_lock", resource: "scene:" + suffix.id, kind: "timing", note: "Suffix" });
    const before = structuredClone(locked);
    const saved = await applyChange(locked, timingChange(locked, "reorder"));
    expect(saved.scenes.map(scene => scene.id)).toEqual([second.id, first.id, ...before.scenes.slice(2).map(scene => scene.id)]);
    expect(saved.deliverables).toEqual([
      { ...before.deliverables[0], included_scene_ids: [second.id, first.id, suffix.id] },
      ...before.deliverables.slice(1),
    ]);
    expect(saved.scenes[2]).toEqual(before.scenes[2]);
    expect(locked).toEqual(before);

    const unlocked = await applyChange(saved, { type: "remove_lock", lock_id: saved.locks[0].id });
    await expect(applyChange(unlocked, { type: "move_scene", scene_id: suffix.id, to_index: 0 }))
      .rejects.toThrow("protected delivery timing");
    expect(unlocked.scenes).toEqual(saved.scenes);
    expect(unlocked.deliverables).toEqual(saved.deliverables);
  });

  it("fractional duration and reorder keep reduced rational starts exact", async () => {
    const source = original(), before = structuredClone(source);
    const timed = await applyChange(source, { type: "set_scene_duration", scene_id: source.scenes[0].id, duration: { num: "22", den: "3" } });
    expect(timed.scenes[1].start).toEqual({ num: "22", den: "3" });
    expect(timed.scenes[2].start).toEqual({ num: "49", den: "3" });
    const reordered = await applyChange(timed, timingChange(timed, "reorder"));
    expect(reordered.scenes[1].start).toEqual({ num: "9", den: "1" });
    expect(reordered.scenes[2].start).toEqual({ num: "49", den: "3" });
    expect(source).toEqual(before);
  });
});
