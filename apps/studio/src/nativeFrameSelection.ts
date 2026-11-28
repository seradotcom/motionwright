import { resolveSceneRenderReceipt } from "./renderReceipt";
import { graphAddTime, graphFraction } from "./semanticGraph";
import { timelineSceneEnd } from "./timelineNavigation";
import type { MotionCanvasRenderEvidence, Project, RationalTime, Scene } from "./types";
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

interface NativeSceneFrameCoverage extends Omit<NativeFrameSelection, "frameIndex"> {
  startFrame: number;
  endFrame: number;
  startInSegment: RationalTime;
}

function firstFrameAtOrAfter(time: RationalTime, rate: { num: number; den: number }): bigint {
  const { num, den } = graphFraction(time);
  const units = num * BigInt(rate.num);
  const period = den * BigInt(rate.den);
  return (units + period - 1n) / period;
}

export function nativeSceneFrameCoverage(
  project: Project,
  scene: Scene | null,
  displayProfile: Project["deliverables"][number] | null,
  evidence: MotionCanvasRenderEvidence | null,
): NativeSceneFrameCoverage | null {
  const receipt = resolveSceneRenderReceipt(project, scene, displayProfile, evidence);
  if (!scene || receipt?.kind !== "current" || !evidence) return null;
  const segment = receipt.segment;
  const grant = evidence.preview?.find((candidate) =>
    candidate.segment_id === segment.segment_id
    && candidate.frame_count === segment.frame_count
    && candidate.scene_ids.join("|") === segment.scene_ids.join("|")
    && candidate.token.length > 0
  );
  if (!grant || !Number.isSafeInteger(grant.frame_count) || grant.frame_count < 1
    || new Set(segment.scene_ids).size !== segment.scene_ids.length) return null;
  const { num, den } = evidence.frame_rate;
  const fps = num / den;
  if (!Number.isSafeInteger(num) || !Number.isSafeInteger(den) || num <= 0 || den <= 0
    || fps < 1 || fps > 120) return null;

  try {
    // Film joins scenes in receipt order and rebases each segment onto zero.
    // An output frame belongs to the shot containing its sampling instant.
    let startInSegment: RationalTime = { num: "0", den: "1" };
    for (const id of segment.scene_ids) {
      const source = project.scenes.find(candidate => candidate.id === id);
      if (!source || graphFraction(source.duration).num <= 0n) return null;
      const endInSegment = graphAddTime(startInSegment, source.duration);
      if (id === scene.id) {
        const start = firstFrameAtOrAfter(startInSegment, evidence.frame_rate);
        const end = firstFrameAtOrAfter(endInSegment, evidence.frame_rate);
        if (start >= end || end > BigInt(grant.frame_count)) return null;
        return { token: grant.token, segmentId: segment.segment_id, frameCount: grant.frame_count,
          startFrame: Number(start), endFrame: Number(end), startInSegment };
      }
      startInSegment = endInSegment;
    }
  } catch {
    return null;
  }
  return null;
}

export function nativeFrameAtPlayhead(
  project: Project,
  scene: Scene | null,
  displayProfile: Project["deliverables"][number] | null,
  evidence: MotionCanvasRenderEvidence | null,
  playhead: number,
): NativeFrameSelection | null {
  if (!scene || !evidence || !Number.isFinite(playhead)) return null;
  const coverage = nativeSceneFrameCoverage(project, scene, displayProfile, evidence);
  if (!coverage) return null;
  const { num, den } = evidence.frame_rate;
  const sceneStart = seconds(scene.start);
  const sceneEnd = timelineSceneEnd(scene);
  if (!Number.isFinite(sceneStart) || !Number.isFinite(sceneEnd) || playhead < sceneStart || playhead >= sceneEnd) return null;
  const localTime = playhead - sceneStart;
  const frameIndex = Math.floor((seconds(coverage.startInSegment) + localTime) * (num / den) + 1e-7);
  if (frameIndex < coverage.startFrame || frameIndex >= coverage.endFrame) return null;
  return { token: coverage.token, segmentId: coverage.segmentId, frameIndex, frameCount: coverage.frameCount };
}
