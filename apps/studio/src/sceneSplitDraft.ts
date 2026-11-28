import { projectionUuid } from "./projectMutation";
import { formatBeatTime } from "./sceneBeatTiming";
import { timelineSelectionScope } from "./timelineSelection";
import type { Project, RationalTime, SceneSplitChange } from "./types";

export interface SceneSplitAttempt {
  requestId: string;
  revision: number;
  change: SceneSplitChange;
}

const prefix = (project: Project) => "motionwright.scene-split.v1:pending:" + timelineSelectionScope(project) + ":";

export const sameSplitTime = (a: RationalTime, b: RationalTime) => a.num === b.num && a.den === b.den;

export function readSceneSplitAttempts(storage: Storage, project: Project): SceneSplitAttempt[] {
  if (storage.length > 4096) throw new Error("The scene split recovery catalogue exceeds its lookup limit.");
  const start = prefix(project), result: SceneSplitAttempt[] = [];
  for (let index = 0; index < storage.length; index++) {
    const key = storage.key(index);
    if (!key?.startsWith(start)) continue;
    const raw = storage.getItem(key);
    if (raw === null) continue;
    if (raw.length > 1024 || result.length >= 8) throw new Error("Resolve retained scene splits before starting another.");
    const attempt = JSON.parse(raw) as SceneSplitAttempt;
    if (!attempt || Object.keys(attempt).length !== 3
        || !projectionUuid(attempt.requestId) || attempt.requestId !== key.slice(start.length)
        || !Number.isSafeInteger(attempt.revision) || attempt.revision < 0 || attempt.change?.type !== "split_scene" || Object.keys(attempt.change).length !== 4
        || !projectionUuid(attempt.change.scene_id) || !projectionUuid(attempt.change.right_scene_id)
        || attempt.change.scene_id === attempt.change.right_scene_id) {
      throw new Error("The original scene split could not be restored.");
    }
    formatBeatTime(attempt.change.at);
    result.push(attempt);
  }
  return result;
}

export function retainSceneSplit(storage: Storage, project: Project, attempt: SceneSplitAttempt): void {
  if (readSceneSplitAttempts(storage, project).length) throw new Error("Check the original scene split first.");
  const key = prefix(project) + attempt.requestId;
  const raw = JSON.stringify(attempt);
  if (storage.getItem(key) !== null) throw new Error("The scene split identity already exists.");
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The original scene split could not be retained.");
}

export function finishSceneSplit(storage: Storage, project: Project, attempt: SceneSplitAttempt, keepOriginal = false): void {
  const key = prefix(project) + attempt.requestId, raw = JSON.stringify(attempt);
  if (!keepOriginal && storage.getItem(key) === null) return;
  if (storage.getItem(key) !== raw) throw new Error("The retained scene split changed.");
  if (keepOriginal) {
    const archive = "motionwright.scene-split.v1:retained:" + timelineSelectionScope(project) + ":" + attempt.requestId;
    if (storage.getItem(archive) !== null && storage.getItem(archive) !== raw) throw new Error("The scene split archive changed.");
    storage.setItem(archive, raw);
    if (storage.getItem(archive) !== raw) throw new Error("The original scene split could not be archived.");
  }
  storage.removeItem(key);
}
