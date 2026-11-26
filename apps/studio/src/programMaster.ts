import { resolveSceneRenderReceipt } from "./renderReceipt";
import type {
  MltAvMasterEvidence,
  NativeMultisegmentAvMasterEvidence,
  MotionCanvasRenderEvidence,
  Project,
  Scene,
} from "./types";
import { seconds } from "./types";

/** Current original master metadata, with every represented source scene on
 * one cut clock. The opaque owner token remains the media read authority.
 */
export interface ProgramMasterSelection {
  exportToken: string;
  segmentId: string;
  sceneIds: string[];
  mediaTimeSeconds: number;
  mediaDurationSeconds: number;
  profileId: string;
  reviewOnly: boolean;
  masterSha256: string | null;
}

export type ProgramAvEvidence = MltAvMasterEvidence | NativeMultisegmentAvMasterEvidence;

function finitePositive(value: number): boolean {
  return Number.isFinite(value) && value > 0;
}

function sameProfileRate(profile: Project["deliverables"][number], rate: {num:number;den:number}): boolean {
  if (!Number.isSafeInteger(rate.num) || !Number.isSafeInteger(rate.den)
    || rate.num <= 0 || rate.den <= 0) return false;
  try {
    const num = BigInt(profile.frame_rate.num), den = BigInt(profile.frame_rate.den);
    return num > 0n && den > 0n && num * BigInt(rate.den) === den * BigInt(rate.num);
  } catch { return false; }
}

export function programMasterAtPlayhead(
  project: Project,
  scene: Scene | null,
  displayProfile: Project["deliverables"][number] | null,
  visual: MotionCanvasRenderEvidence | null,
  master: ProgramAvEvidence | null,
  playhead: number,
): ProgramMasterSelection | null {
  if (!scene || !visual || !master || !displayProfile
    || !Number.isFinite(playhead) || typeof master.export_token !== "string"
    || !master.export_token
    || master.project_resource !== "project:" + project.id
    || master.generation !== project.generation
    || master.revision !== project.revision
    || master.deliverable_id !== displayProfile.id
    || visual.deliverable_id !== displayProfile.id
    || !sameProfileRate(displayProfile, visual.frame_rate)
  ) return null;
  const receipt = resolveSceneRenderReceipt(project, scene, displayProfile, visual);
  if (receipt?.kind !== "current") return null;
  const segment = receipt.segment;
  const fps = visual.frame_rate.num / visual.frame_rate.den;
  if (!finitePositive(fps) || fps > 120 || !Number.isSafeInteger(master.frame_count)
    || master.frame_count <= 0 || master.frame_count > 36_000
  ) return null;

  let sceneIds: string[];
  let reviewOnly = false;
  let masterSha256: string | null = null;
  if ("video_segments" in master) {
    const expectedScope = master.review_only
      ? "native-captioned-multisegment-av-review-only-not-decoded-pixel-approved"
      : "native-multisegment-av-verified-technical-output-not-human-approved";
    if (typeof master.review_only !== "boolean"
      || master.evidence_scope !== expectedScope
      || !Number.isSafeInteger(master.video_segments) || master.video_segments < 2
      || master.video_segments !== visual.segments.length
      || master.video_codec !== "h264" || master.audio_codec !== "aac"
      || master.audio_sample_rate !== 48_000 || master.audio_channels !== 2
      || !/^[0-9a-f]{64}$/.test(master.master_sha256)
      || new Set(visual.segments.map(item => item.segment_id)).size !== visual.segments.length
    ) return null;
    let totalFrames = 0;
    for (const part of visual.segments) {
      if (!part.segment_id || !part.job_ref || !part.scene_ids.length
        || !Number.isSafeInteger(part.frame_count) || part.frame_count <= 0) return null;
      const duration = part.scene_ids.reduce((sum, id) => {
        const source = project.scenes.find(item => item.id === id);
        return sum + (source ? seconds(source.duration) : Number.NaN);
      }, 0);
      if (!finitePositive(duration)
        || Math.abs(part.frame_count / fps - duration) > 1 / fps + 1e-7) return null;
      totalFrames += part.frame_count;
      if (!Number.isSafeInteger(totalFrames) || totalFrames > 36_000) return null;
    }
    if (totalFrames !== master.frame_count) return null;
    sceneIds = visual.segments.flatMap(part => part.scene_ids);
    reviewOnly = master.review_only;
    masterSha256 = master.master_sha256;
  } else {
    if (master.master?.profile !== "h264-aac-mp4" || visual.segments.length !== 1
      || segment.segment_id !== master.motion_segment_id
      || segment.frame_count !== master.frame_count
      || master.frame_rate.num !== visual.frame_rate.num
      || master.frame_rate.den !== visual.frame_rate.den
    ) return null;
    sceneIds = segment.scene_ids;
    const artifact = master.master.artifact as {sha256?:unknown} | undefined;
    if (typeof artifact?.sha256 === "string" && /^[0-9a-f]{64}$/.test(artifact.sha256)) {
      masterSha256 = artifact.sha256;
    }
  }
  if (sceneIds.length === 0 || !sceneIds.includes(scene.id)
    || new Set(sceneIds).size !== sceneIds.length
    || (displayProfile.included_scene_ids.length > 0
      && sceneIds.some(id => !displayProfile.included_scene_ids.includes(id)))) return null;

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
  const sceneStart = seconds(scene.start);
  const sceneEnd = sceneStart + seconds(scene.duration);
  if (!Number.isFinite(sceneStart) || !Number.isFinite(sceneEnd)
    || playhead < sceneStart || playhead >= sceneEnd) return null;
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
    reviewOnly,
    masterSha256,
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
  return last ? seconds(last.start) + Math.max(0, seconds(last.duration) - 1e-3) : null;
}
