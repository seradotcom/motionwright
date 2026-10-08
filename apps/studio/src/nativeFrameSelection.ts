import { resolveSceneRenderReceipt } from "./renderReceipt";
import type { MotionCanvasRenderEvidence, Project, Scene } from "./types";
import { seconds } from "./types";

/** Resolve the exact PNG index in a native Motion Canvas segment.
 * Segment order follows the delivered cut, not arbitrary project indices.
 * UI time is quantized down to the preceding frame; no source media is retimed.
 */
export interface NativeFrameSelection {
  token: string;
  segmentId: string;
  frameIndex: number;
  frameCount: number;
}

export function nativeFrameAtPlayhead(
  project: Project,
  scene: Scene | null,
  displayProfile: Project["deliverables"][number] | null,
  evidence: MotionCanvasRenderEvidence | null,
  playhead: number,
): NativeFrameSelection | null {
  const receipt = resolveSceneRenderReceipt(project, scene, displayProfile, evidence);
  if (!scene || receipt?.kind !== "current" || !evidence) return null;
  const segment = receipt.segment;
  const grant = evidence.preview?.find((candidate) =>
    candidate.segment_id === segment.segment_id
    && candidate.frame_count === segment.frame_count
    && candidate.scene_ids.join("|") === segment.scene_ids.join("|")
    && candidate.token.length > 0
  );
  if (!grant || !Number.isSafeInteger(grant.frame_count) || grant.frame_count < 1) return null;
  if (!Number.isFinite(playhead)) return null;
  const { num, den } = evidence.frame_rate;
  const fps = num / den;
  if (!Number.isFinite(fps) || fps < 1 || fps > 120) return null;

  let preceding = 0;
  for (const id of segment.scene_ids) {
    if (id === scene.id) break;
    const prior = project.scenes.find((candidate) => candidate.id === id);
    if (!prior) return null;
    preceding += seconds(prior.duration);
  }
  const sceneStart = seconds(scene.start);
  const sceneDuration = seconds(scene.duration);
  if (!Number.isFinite(sceneDuration) || sceneDuration <= 0 || !Number.isFinite(preceding)) return null;
  const localTime = Math.max(0, Math.min(sceneDuration, playhead - sceneStart));
  const frameIndex = Math.max(0, Math.min(grant.frame_count - 1,
    Math.floor((preceding + localTime) * fps + 1e-7)));
  return {
    token: grant.token,
    segmentId: segment.segment_id,
    frameIndex,
    frameCount: grant.frame_count,
  };
}
