import { resolveSceneRenderReceipt } from "./renderReceipt";
import type {
  MltAvMasterEvidence,
  MotionCanvasRenderEvidence,
  Project,
  Scene,
} from "./types";
import { seconds } from "./types";

/** A verified media binding between one published Film segment and a
 * master mux, never inferred from a filename or anonymous video bytes.
 */
export interface ProgramMasterSelection {
  exportToken: string;
  segmentId: string;
  sceneIds: string[];
  mediaTimeSeconds: number;
  mediaDurationSeconds: number;
  profileId: string;
}

function finitePositive(value: number): boolean {
  return Number.isFinite(value) && value > 0;
}

export function programMasterAtPlayhead(
  project: Project,
  scene: Scene | null,
  displayProfile: Project["deliverables"][number] | null,
  visual: MotionCanvasRenderEvidence | null,
  master: MltAvMasterEvidence | null,
  playhead: number,
): ProgramMasterSelection | null {
  if (!scene || !visual || !master || !displayProfile
    || !Number.isFinite(playhead) || typeof master.export_token !== "string"
    || !master.export_token || master.master?.profile !== "h264-aac-mp4"
  ) return null;
  const receipt = resolveSceneRenderReceipt(project, scene, displayProfile, visual);
  if (receipt?.kind !== "current") return null;
  const segment = receipt.segment;
  if (visual.segments.length !== 1
    || segment.segment_id !== master.motion_segment_id
    || segment.frame_count !== master.frame_count
    || master.project_resource !== "project:" + project.id
    || master.generation !== project.generation
    || master.revision !== project.revision
    || master.deliverable_id !== displayProfile.id
    || visual.deliverable_id !== displayProfile.id
    || master.frame_rate.num !== visual.frame_rate.num
    || master.frame_rate.den !== visual.frame_rate.den
  ) return null;
  const fps = master.frame_rate.num / master.frame_rate.den;
  if (!finitePositive(fps) || fps > 120 || !Number.isSafeInteger(master.frame_count)
    || master.frame_count <= 0
  ) return null;

  const sceneIds = segment.scene_ids;
  if (sceneIds.length === 0 || !sceneIds.includes(scene.id)
    || new Set(sceneIds).size !== sceneIds.length) return null;

  let previousDuration = 0;
  let totalDuration = 0;
  let selectedFound = false;
  let priorProjectIndex = -1;
  for (const id of sceneIds) {
    const index = project.scenes.findIndex((candidate) => candidate.id === id);
    if (index <= priorProjectIndex || index < 0) return null;
    priorProjectIndex = index;
    const current = project.scenes[index];
    if (current.renderer !== "motion-canvas") return null;
    const duration = seconds(current.duration);
    if (!finitePositive(duration)) return null;
    if (id === scene.id) {
      previousDuration = totalDuration;
      selectedFound = true;
    }
    totalDuration += duration;
  }
  if (!selectedFound || !finitePositive(totalDuration)) return null;
  const nativeDuration = master.frame_count / fps;
  if (!finitePositive(nativeDuration)
    || Math.abs(nativeDuration - totalDuration) > 1 / fps + 1e-7) return null;
  const inScene = Math.max(0,
    Math.min(seconds(scene.duration), playhead - seconds(scene.start)));
  const mediaTimeSeconds = Math.max(0,
    Math.min(nativeDuration, previousDuration + inScene));
  return {
    exportToken: master.export_token,
    segmentId: segment.segment_id,
    sceneIds,
    mediaTimeSeconds,
    mediaDurationSeconds: nativeDuration,
    profileId: displayProfile.id,
  };
}

/** Reverse-map current decoded media time to the actual authored scene clock.
 * Selected source scenes need not be contiguous in the project: Film compacts
 * an explicit included cut, while editorial scene positions stay untouched.
 */
export function editorialTimeForMasterMedia(
  project: Project,
  selection: ProgramMasterSelection,
  mediaTime: number,
): number | null {
  if (!Number.isFinite(mediaTime) || mediaTime < 0
    || mediaTime > selection.mediaDurationSeconds + 1e-3) return null;
  let remaining = mediaTime;
  let last: Scene | null = null;
  for (const id of selection.sceneIds) {
    const scene = project.scenes.find((candidate) => candidate.id === id);
    if (!scene) return null;
    last = scene;
    const duration = seconds(scene.duration);
    if (!finitePositive(duration)) return null;
    if (remaining < duration) return seconds(scene.start) + remaining;
    remaining -= duration;
  }
  // End of an included cut is still the final selected scene, not an
  // unrelated following scene that has no audio/video master.
  return last ? seconds(last.start) + Math.max(0, seconds(last.duration) - 1e-3) : null;
}
