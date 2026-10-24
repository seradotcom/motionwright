import { formatBeatTime, parseBeatTime } from "./sceneBeatTiming";
import type { Change, LockKind, Project, RationalTime } from "./types";

type TimingChange = Extract<Change, { type: "move_scene" | "set_scene_duration" }>;
type Fraction = { n: bigint; d: bigint };

// Use the existing canonical saved-time validation and exact input reduction.
// This is browser parity; Rust/StudioService still owns every persisted write.
function fraction(time: RationalTime): Fraction {
  formatBeatTime(time);
  return { n: BigInt(time.num), d: BigInt(time.den) };
}
function compare(left: RationalTime, right: RationalTime): number {
  const a = fraction(left), b = fraction(right), value = a.n * b.d - b.n * a.d;
  return value < 0n ? -1 : value > 0n ? 1 : 0;
}
function add(left: RationalTime, right: RationalTime): RationalTime {
  const a = fraction(left), b = fraction(right);
  return parseBeatTime(`${a.n * b.d + b.n * a.d}/${a.d * b.d}`);
}
function unlocked(project: Project, resource: string, kinds: LockKind[]) {
  const lock = project.locks.find(entry => entry.resource === resource && kinds.includes(entry.kind));
  if (lock) throw new Error(`resource is locked: ${resource} (${lock.kind})`);
}

/** Publish only a complete, exact, lock-checked scene projection. */
export function applyGeneralSceneTiming(project: Project, change: TimingChange): void {
  unlocked(project, "project:" + project.id, ["timing"]);
  const resource = "scene:" + change.scene_id;
  unlocked(project, resource, change.type === "move_scene" ? ["content", "timing"] : ["timing"]);
  const index = project.scenes.findIndex(scene => scene.id === change.scene_id);
  if (index < 0) throw new Error("resource not found: " + resource);
  const scenes = [...project.scenes];
  if (change.type === "move_scene") {
    if (!Number.isInteger(change.to_index) || change.to_index < 0 || change.to_index >= scenes.length)
      throw new Error("scene destination index is out of bounds");
    const [moved] = scenes.splice(index, 1);
    scenes.splice(change.to_index, 0, moved);
  } else {
    const duration = change.duration, scene = scenes[index];
    if (compare(duration, { num: "0", den: "1" }) <= 0 || compare(duration, { num: "86400", den: "1" }) > 0)
      throw new Error("scene duration is out of bounds");
    if (scene.nodes.some(node => node.keyframes.some(keyframe => compare(keyframe.at, duration) >= 0)))
      throw new Error("scene duration would strand an authored keyframe");
    if (scene.beats.some(beat => compare(add(beat.start, beat.duration), duration) > 0))
      throw new Error("scene duration would strand an authored beat");
    scenes[index] = { ...scene, duration: { ...duration } };
  }
  let cursor: RationalTime = { num: "0", den: "1" };
  const originals = new Map(project.scenes.map(scene => [scene.id, scene]));
  const proposed = scenes.map(scene => {
    const start = cursor;
    cursor = add(cursor, scene.duration);
    const original = originals.get(scene.id)!;
    if (compare(start, original.start) !== 0 || compare(scene.duration, original.duration) !== 0)
      unlocked(project, "scene:" + scene.id, ["timing"]);
    return { ...scene, start };
  });
  project.scenes = proposed;
}
