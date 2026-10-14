/** Bounded observations of already-authorized native-frame thumbnails.
 * No read/dispatch authority, independent verification, aesthetic score or edits.
 */
import { addFrameOffset, nativeFindingBody } from "./nativeInspection";
import type { NativeInspectionPlan, NativeSampleEvidence } from "./nativeInspection";
import type { Change, Project, RationalTime } from "./types";

export const NATIVE_ANALYSIS_METHOD = "motionwright.bounded-native-thumbnail-analysis/1";
const MAX_EDGE = 384;
const CHANNEL_THRESHOLD = 16;
const TRANSIENT_MEAN_THRESHOLD = 35;
const validationNote = { severity: "suggestion", confidence: "uncertain", nodeId: null,
  violatedConstraint: "Bounded source membership", observation: "Read-only source validation",
  proposedRepair: "No repair requested" } as const;

export interface InspectionRaster extends NativeSampleEvidence {
  thumbnail: { width: number; height: number; pixels: Uint8ClampedArray };
}
export interface ThumbnailDifference {
  fromFrame: number; toFrame: number; adjacent: boolean;
  comparedPixels: number; changedPixels: number; maxChannelDelta: number;
  meanChannelDelta: number;
  changedBox: { x: number; y: number; width: number; height: number } | null;
}
export interface NativeAnalysisCandidate {
  key: string;
  code: "expected_motion_unchanged" | "isolated_thumbnail_change";
  anchorFrame: number; referenceFrame: number; evidenceFrames: number[];
  start: RationalTime; end: RationalTime;
  observation: string; consequence: string; proposedRepair: string;
}
export interface NativeFrameAnalysis {
  method: typeof NATIVE_ANALYSIS_METHOD;
  expectedMotion: boolean;
  coverage: {
    selectedFrames: number; adjacentPairs: number; adjacentTriples: number;
    temporalGaps: number; sampling: "consecutive_window" | "sampled_frames";
    width: number; height: number;
  };
  differences: ThumbnailDifference[];
  candidates: NativeAnalysisCandidate[];
  creativeApproval: "not_assessed";
  effectsVerdict: "not_issued";
}

function boundedInteger(value: number, min: number, max: number): boolean {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}
function validateRaster(plan: NativeInspectionPlan, sample: InspectionRaster): void {
  nativeFindingBody(plan, sample, validationNote);
  const image = sample.thumbnail;
  if (!image || !boundedInteger(image.width, 1, MAX_EDGE) ||
      !boundedInteger(image.height, 1, MAX_EDGE) ||
      !(image.pixels instanceof Uint8ClampedArray) ||
      image.pixels.length !== image.width * image.height * 4) {
    throw new Error("Invalid bounded native thumbnail raster.");
  }
}

/** Internal request identity. Contains live read tokens: never serialize it into a review. */
export function nativeInspectionRequestKey(plan: NativeInspectionPlan | null): string {
  return plan ? JSON.stringify([plan.projectId, plan.generation, plan.revision,
    plan.sceneId, plan.profileId, plan.width, plan.height, plan.frameRate,
    plan.samples.map(s => [s.segmentId, s.token, s.frameCount, s.frameIndex, s.timelineTime])]) : "unavailable";
}

/** Select an in-scene consecutive range under the existing grant, never a new render. */
export function nativeInspectionWindow(
  overview: NativeInspectionPlan, firstFrame: number, count = 8,
): NativeInspectionPlan {
  const first = overview.samples[0], last = overview.samples.at(-1);
  if (!first || !last || overview.samples.length > 8 ||
      !boundedInteger(count, 1, 8) ||
      !boundedInteger(firstFrame, first.frameIndex, last.frameIndex) ||
      overview.samples.some((s, i) => s.token !== first.token || s.segmentId !== first.segmentId ||
        s.frameCount !== first.frameCount || (i > 0 && s.frameIndex <= overview.samples[i - 1].frameIndex))) {
    throw new Error("Native inspection window is outside this scene or read grant.");
  }
  const end = Math.min(last.frameIndex, firstFrame + count - 1);
  return { ...overview, samples: Array.from({ length: end - firstFrame + 1 }, (_, offset) => {
    const frameIndex = firstFrame + offset;
    return { ...first, frameIndex, localFrame: first.localFrame + frameIndex - first.frameIndex,
      timelineTime: addFrameOffset(first.timelineTime, frameIndex - first.frameIndex, overview.frameRate) };
  }) };
}

export function thumbnailDifference(a: InspectionRaster, b: InspectionRaster): ThumbnailDifference {
  if (a.thumbnail.width !== b.thumbnail.width || a.thumbnail.height !== b.thumbnail.height ||
      a.thumbnail.pixels.length !== b.thumbnail.pixels.length) {
    throw new Error("Native thumbnails are not comparable; no resize or substitution is performed.");
  }
  const width = a.thumbnail.width, height = a.thumbnail.height;
  let changed = 0, total = 0, maximum = 0;
  let left = width, top = height, right = -1, bottom = -1;
  for (let pixel = 0; pixel < width * height; pixel++) {
    let local = 0;
    for (let channel = 0; channel < 4; channel++) {
      const delta = Math.abs(a.thumbnail.pixels[pixel * 4 + channel] - b.thumbnail.pixels[pixel * 4 + channel]);
      total += delta; local = Math.max(local, delta);
    }
    maximum = Math.max(maximum, local);
    if (local > CHANNEL_THRESHOLD) {
      changed++;
      const x = pixel % width, y = Math.floor(pixel / width);
      left = Math.min(left, x); right = Math.max(right, x);
      top = Math.min(top, y); bottom = Math.max(bottom, y);
    }
  }
  return { fromFrame: a.frameIndex, toFrame: b.frameIndex,
    adjacent: b.frameIndex === a.frameIndex + 1,
    comparedPixels: width * height, changedPixels: changed, maxChannelDelta: maximum,
    meanChannelDelta: total / (width * height * 4),
    changedBox: changed ? { x: left, y: top, width: right - left + 1, height: bottom - top + 1 } : null };
}

/** Decode coverage is limited to the supplied, complete read batch; never ALL_FRAMES. */
export function analyzeNativeInspection(
  plan: NativeInspectionPlan, samples: InspectionRaster[], expectedMotion = false,
): NativeFrameAnalysis {
  if (typeof expectedMotion !== "boolean" || samples.length < 1 || samples.length > 8 ||
      samples.length !== plan.samples.length) throw new Error("Incomplete or oversized native inspection batch.");
  samples.forEach((sample, index) => {
    validateRaster(plan, sample);
    if (sample.frameIndex !== plan.samples[index].frameIndex ||
        (index > 0 && sample.frameIndex <= samples[index - 1].frameIndex)) {
      throw new Error("Native inspection samples are reordered or duplicated.");
    }
    if (sample.thumbnail.width !== samples[0].thumbnail.width ||
        sample.thumbnail.height !== samples[0].thumbnail.height) {
      throw new Error("Native thumbnails have incompatible dimensions.");
    }
  });
  const differences = samples.slice(1).map((sample, i) => thumbnailDifference(samples[i], sample));
  const candidates: NativeAnalysisCandidate[] = [];
  let triples = 0;
  for (let i = 1; i < samples.length - 1; i++) {
    if (!differences[i - 1].adjacent || !differences[i].adjacent) continue;
    triples++;
    const outside = thumbnailDifference(samples[i - 1], samples[i + 1]);
    if (outside.maxChannelDelta === 0 && differences[i - 1].meanChannelDelta >= TRANSIENT_MEAN_THRESHOLD) {
      const center = samples[i];
      candidates.push({ key: `isolated:${center.frameIndex}`, code: "isolated_thumbnail_change",
        anchorFrame: center.frameIndex, referenceFrame: samples[i - 1].frameIndex,
        evidenceFrames: samples.slice(i - 1, i + 2).map(s => s.frameIndex),
        start: center.timelineTime, end: addFrameOffset(center.timelineTime, 1, plan.frameRate),
        observation: "An isolated thumbnail change occurs between two identical adjacent thumbnails.",
        consequence: "This may be a deliberate flash or a rendering discontinuity. Full-resolution motion and creative intent were not assessed.",
        proposedRepair: "Inspect this native three-frame interval and review its keyframes before proposing a localized edit." });
    }
  }
  if (expectedMotion) {
    let start = 0;
    const flush = (end: number) => {
      if (end - start >= 3) {
        const selected = samples.slice(start, end), first = selected[0], last = selected.at(-1)!;
        candidates.push({ key: `unchanged:${first.frameIndex}:${last.frameIndex}`, code: "expected_motion_unchanged",
          anchorFrame: first.frameIndex, referenceFrame: last.frameIndex,
          evidenceFrames: selected.map(s => s.frameIndex), start: first.timelineTime,
          end: addFrameOffset(last.timelineTime, 1, plan.frameRate),
          observation: `${selected.length} consecutive thumbnails are unchanged inside a user-declared motion-expected window.`,
          consequence: "A designed hold, subpixel motion or a stalled animation cannot be distinguished by this bounded thumbnail observation alone.",
          proposedRepair: "Review the motion expectation and source keyframes; keep the hold or draft a scoped repair after human inspection." });
      }
    };
    for (let end = 1; end <= samples.length; end++) {
      if (end === samples.length || !differences[end - 1].adjacent || differences[end - 1].maxChannelDelta !== 0) {
        flush(end); start = end;
      }
    }
  }
  const adjacentPairs = differences.filter(p => p.adjacent).length;
  return { method: NATIVE_ANALYSIS_METHOD, expectedMotion,
    coverage: { selectedFrames: samples.length, adjacentPairs, adjacentTriples: triples,
      temporalGaps: differences.length - adjacentPairs,
      sampling: samples.length > 1 && adjacentPairs === differences.length ? "consecutive_window" : "sampled_frames",
      width: samples[0].thumbnail.width, height: samples[0].thumbnail.height },
    differences, candidates, creativeApproval: "not_assessed", effectsVerdict: "not_issued" };
}

/** Explicit AddReview through the existing CAS service; never an automatic scene repair. */
export function nativeAnalysisReviewChange(
  project: Project, plan: NativeInspectionPlan, samples: InspectionRaster[],
  candidateKey: string, expectedMotion: boolean,
): Change {
  if (project.id !== plan.projectId || project.generation !== plan.generation ||
      project.revision !== plan.revision || !project.scenes.some(s => s.id === plan.sceneId) ||
      !project.deliverables.some(p => p.id === plan.profileId && p.width === plan.width && p.height === plan.height)) {
    throw new Error("Native analysis source is stale or belongs to another project/profile.");
  }
  // Recompute instead of admitting a caller-edited observation or score.
  const analysis = analyzeNativeInspection(plan, samples, expectedMotion);
  const candidate = analysis.candidates.find(item => item.key === candidateKey);
  if (!candidate) throw new Error("Candidate is not present in the current native inspection.");
  const body = JSON.stringify({ schema: "motionwright.native-thumbnail-review-candidate/1",
    method: NATIVE_ANALYSIS_METHOD,
    source: { project_id: plan.projectId, generation: plan.generation, revision: plan.revision,
      scene_id: plan.sceneId, profile_id: plan.profileId, segment_id: plan.samples[0].segmentId,
      frame_rate: plan.frameRate, source_width: plan.width, source_height: plan.height },
    coverage: analysis.coverage, expected_motion_declared_by_user: expectedMotion,
    measurements: { channel_delta_threshold: CHANNEL_THRESHOLD, transient_mean_threshold: TRANSIENT_MEAN_THRESHOLD,
      units: "browser_color_managed_thumbnail_8bit_rgba", full_resolution_equivalence: "not_evaluated" },
    candidate,
    evidence: samples.filter(s => candidate.evidenceFrames.includes(s.frameIndex)).map(s => ({
      frame_index: s.frameIndex, time: s.timelineTime, png_sha256: s.pngSha256,
      source_width: s.width, source_height: s.height,
      thumbnail_width: s.thumbnail.width, thumbnail_height: s.thumbnail.height,
    })),
    interpretation: "automated_candidate_recorded_for_human_review",
    confidence: "uncertain", creative_approval: "not_assessed", effects_verdict: "not_issued",
    observer_admission: "not_claimed", repair_applied: false,
  });
  if (new TextEncoder().encode(body).length > 8_000) throw new Error("Native analysis exceeds the existing review body budget.");
  return { type: "add_review", kind: "creative", resource: "scene:" + plan.sceneId,
    body, start: candidate.start, end: candidate.end, locale: null, profile_id: plan.profileId };
}
