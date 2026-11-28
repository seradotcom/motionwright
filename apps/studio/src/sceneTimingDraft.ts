import { projectionUuid } from "./projectMutation";
import { formatBeatTime } from "./sceneBeatTiming";
import { timelineSelectionScope } from "./timelineSelection";
import type { Project, RationalTime, SceneTimingChange } from "./types";

export interface SceneTimingAttempt {
  requestId: string;
  revision: number;
  change: SceneTimingChange;
}

const roots = {
  split_scene: "motionwright.scene-split.v1",
  trim_scene_end: "motionwright.scene-trim.v1",
};

const prefix = (project: Project, type: SceneTimingChange["type"], status: "pending" | "retained") =>
  roots[type] + ":" + status + ":" + timelineSelectionScope(project) + ":";

export const sameSceneTime = (a: RationalTime, b: RationalTime) => a.num === b.num && a.den === b.den;

export function readSceneTimingAttempts(storage: Storage, project: Project): SceneTimingAttempt[] {
  if (storage.length > 4096) throw new Error("The scene edit recovery catalogue exceeds its lookup limit.");
  const split = prefix(project, "split_scene", "pending");
  const trim = prefix(project, "trim_scene_end", "pending");
  const result: SceneTimingAttempt[] = [];
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key) continue;
    const start = key.startsWith(split) ? split : key.startsWith(trim) ? trim : null;
    if (!start) continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    if (raw.length > 1024 || result.length >= 8) throw new Error("Resolve retained scene edits before starting another.");
    const attempt = JSON.parse(raw) as SceneTimingAttempt;
    const type = start === split ? "split_scene" : "trim_scene_end";
    if (!attempt || Object.keys(attempt).length !== 3
        || !projectionUuid(attempt.requestId) || attempt.requestId !== key.slice(start.length)
        || !Number.isSafeInteger(attempt.revision) || attempt.revision < 0
        || attempt.change?.type !== type || !projectionUuid(attempt.change.scene_id)) {
      throw new Error("The original scene edit could not be restored.");
    }
    const change = attempt.change;
    if (change.type === "split_scene"
        ? Object.keys(change).length !== 4 || !projectionUuid(change.right_scene_id) || change.scene_id === change.right_scene_id
        : Object.keys(change).length !== 3) {
      throw new Error("The original scene edit could not be restored.");
    }
    formatBeatTime(change.at);
    result.push(attempt);
  }
  return result;
}

export function retainSceneTiming(storage: Storage, project: Project, attempt: SceneTimingAttempt): void {
  if (readSceneTimingAttempts(storage, project).length) throw new Error("Check the original scene edit first.");
  const key = prefix(project, attempt.change.type, "pending") + attempt.requestId;
  const raw = JSON.stringify(attempt);
  if (storage.getItem(key) !== null) throw new Error("The scene edit identity already exists.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The original scene edit could not be retained.");
}

export function finishSceneTiming(storage: Storage, project: Project, attempt: SceneTimingAttempt, keepOriginal = false): void {
  const key = prefix(project, attempt.change.type, "pending") + attempt.requestId;
  const raw = JSON.stringify(attempt);
  if (!keepOriginal && storage.getItem(key) === null) return;
  if (storage.getItem(key) !== raw) throw new Error("The retained scene edit changed.");
  if (keepOriginal) {
    const archive = prefix(project, attempt.change.type, "retained") + attempt.requestId;
    if (storage.getItem(archive) !== null && storage.getItem(archive) !== raw) throw new Error("The scene edit archive changed.");
    storage.setItem(archive, raw);
    if (storage.getItem(archive) !== raw) throw new Error("The original scene edit could not be archived.");
  }
  storage.removeItem(key);
}
