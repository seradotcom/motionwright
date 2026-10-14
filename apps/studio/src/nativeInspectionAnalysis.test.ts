import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { addFrameOffset } from "./nativeInspection";
import type { NativeInspectionPlan } from "./nativeInspection";
import { analyzeNativeInspection, nativeAnalysisReviewChange, nativeInspectionRequestKey,
  nativeInspectionWindow, thumbnailDifference } from "./nativeInspectionAnalysis";
import type { InspectionRaster } from "./nativeInspectionAnalysis";

function fixture(indices = [0, 1, 2]) {
  const project = structuredClone(fixtureProject), scene = project.scenes[0], profile = project.deliverables[0];
  const plan: NativeInspectionPlan = {
    projectId: project.id, generation: project.generation, revision: project.revision,
    sceneId: scene.id, profileId: profile.id, width: profile.width, height: profile.height,
    frameRate: { num: 30, den: 1 }, samples: indices.map(frameIndex => ({
      frameIndex, localFrame: frameIndex, frameCount: 210, segmentId: "synthetic-segment",
      token: "DO-NOT-SERIALIZE-LIVE-GRANT", timelineTime: addFrameOffset(scene.start, frameIndex, { num: 30, den: 1 }),
    })),
  };
  const samples: InspectionRaster[] = plan.samples.map(selection => ({
    frameIndex: selection.frameIndex, timelineTime: selection.timelineTime,
    pngSha256: "ab".repeat(32), width: plan.width, height: plan.height,
    thumbnail: { width: 2, height: 2, pixels: new Uint8ClampedArray([20,30,40,255,20,30,40,255,20,30,40,255,20,30,40,255]) },
  }));
  return { project, scene, profile, plan, samples };
}

describe("bounded native thumbnail observations, not an independent critic", () => {
  it("does not mistake a static scene for a failed animation without an expectation", () => {
    const { plan, samples } = fixture();
    const analysis = analyzeNativeInspection(plan, samples);
    expect(analysis.candidates).toEqual([]);
    expect(analysis.coverage).toMatchObject({ adjacentPairs: 2, adjacentTriples: 1, temporalGaps: 0 });
    expect(analysis.creativeApproval).toBe("not_assessed");
    expect(analysis.effectsVerdict).toBe("not_issued");
  });
  it("localizes an unchanged run only under a user-declared motion expectation", () => {
    const { plan, samples } = fixture();
    const candidate = analyzeNativeInspection(plan, samples, true).candidates[0];
    expect(candidate.code).toBe("expected_motion_unchanged");
    expect(candidate.evidenceFrames).toEqual([0, 1, 2]);
    expect(candidate.end).toEqual(addFrameOffset(samples[2].timelineTime, 1, plan.frameRate));
    expect(candidate.consequence).toContain("designed hold");
  });
  it("never bridges a sampled gap with a motion or isolated-frame diagnosis", () => {
    const { plan, samples } = fixture([0, 2, 4]);
    samples[1].thumbnail.pixels.fill(255);
    const analysis = analyzeNativeInspection(plan, samples, true);
    expect(analysis.candidates).toEqual([]);
    expect(analysis.coverage).toMatchObject({ adjacentPairs: 0, adjacentTriples: 0, temporalGaps: 2, sampling: "sampled_frames" });
    expect(analysis.differences.every(value => !value.adjacent)).toBe(true);
  });
  it("observes an adjacent A-B-A candidate without claiming that a flash is a defect", () => {
    const { plan, samples } = fixture();
    samples[1].thumbnail.pixels.fill(255);
    const candidate = analyzeNativeInspection(plan, samples).candidates[0];
    expect(candidate.code).toBe("isolated_thumbnail_change");
    expect(candidate.anchorFrame).toBe(1);
    expect(candidate.consequence).toContain("deliberate flash");
  });
  it("splits unchanged runs at gaps instead of conflating them", () => {
    const { plan, samples } = fixture([0, 1, 2, 5, 6, 7]);
    expect(analyzeNativeInspection(plan, samples, true).candidates.map(value => value.evidenceFrames))
      .toEqual([[0, 1, 2], [5, 6, 7]]);
  });
  it("reports changes in thumbnail coordinates including alpha without claiming full-resolution equivalence", () => {
    const { samples } = fixture();
    samples[1].thumbnail.pixels[15] = 0;
    const result = thumbnailDifference(samples[0], samples[1]);
    expect(result.changedPixels).toBe(1);
    expect(result.changedBox).toEqual({ x: 1, y: 1, width: 1, height: 1 });
    expect(result.maxChannelDelta).toBe(255);
  });
  it("refuses partial, reordered and duplicated read batches", () => {
    const { plan, samples } = fixture();
    expect(() => analyzeNativeInspection(plan, samples.slice(1))).toThrow("Incomplete");
    expect(() => analyzeNativeInspection(plan, [samples[1], samples[0], samples[2]])).toThrow("reordered");
    expect(() => analyzeNativeInspection(plan, [samples[0], samples[0], samples[2]])).toThrow("reordered");
  });
  it("refuses malformed, oversized and mismatched decoded buffers", () => {
    const { plan, samples } = fixture();
    expect(() => analyzeNativeInspection(plan, samples.map((s, i) => i === 0 ? { ...s, thumbnail: { ...s.thumbnail, width: 385 } } : s))).toThrow("bounded");
    expect(() => analyzeNativeInspection(plan, samples.map((s, i) => i === 0 ? { ...s, thumbnail: { ...s.thumbnail, pixels: new Uint8ClampedArray(3) } } : s))).toThrow("bounded");
    expect(() => analyzeNativeInspection(plan, samples.map((s, i) => i === 0 ? { ...s, thumbnail: { width: 1, height: 1, pixels: new Uint8ClampedArray(4) } } : s))).toThrow("incompatible");
  });
  it("refuses a foreign digest, time or source dimensions", () => {
    const { plan, samples } = fixture();
    for (const change of [{ pngSha256: "invalid" }, { width: plan.width + 1 }, { timelineTime: { num: "999", den: "1" } }]) {
      expect(() => analyzeNativeInspection(plan, [{ ...samples[0], ...change }, ...samples.slice(1)])).toThrow("outside");
    }
  });
  it("rejects oversized batches and non-boolean expectations", () => {
    const { plan, samples } = fixture(Array.from({ length: 9 }, (_, i) => i));
    expect(() => analyzeNativeInspection(plan, samples)).toThrow("oversized");
    const small = fixture();
    expect(() => analyzeNativeInspection(small.plan, small.samples, "yes" as unknown as boolean)).toThrow("Incomplete");
  });
});

describe("native consecutive windows retain the existing grant and exact rational time", () => {
  it("bounds an eight-frame window to the current scene's final frame", () => {
    const { plan } = fixture([0, 30, 104, 209]);
    const result = nativeInspectionWindow(plan, 206);
    expect(result.samples.map(s => s.frameIndex)).toEqual([206, 207, 208, 209]);
    expect(result.samples.every(s => s.token === plan.samples[0].token)).toBe(true);
  });
  it("preserves an NTSC offset on a nonzero segment origin", () => {
    const { plan } = fixture([0, 10]);
    plan.frameRate = { num: 30000, den: 1001 };
    plan.samples[0].timelineTime = { num: "7", den: "1" };
    const result = nativeInspectionWindow(plan, 1, 1);
    expect(result.samples[0].timelineTime).toEqual({ num: "211001", den: "30000" });
  });
  it.each([-1, 210, 1.5, NaN, Infinity])("rejects out-of-scene or nonintegral start %s", start => {
    const { plan } = fixture([0, 209]);
    expect(() => nativeInspectionWindow(plan, start)).toThrow("outside");
  });
  it("rejects mixed read tokens and invalid frame budgets", () => {
    const { plan } = fixture([0, 209]);
    expect(() => nativeInspectionWindow(plan, 0, 9)).toThrow("outside");
    plan.samples[1].token = "OTHER-GRANT";
    expect(() => nativeInspectionWindow(plan, 0)).toThrow("outside");
  });
  it("keys requests by project, range, revision and grant rather than a single stale token", () => {
    const { plan } = fixture(); const key = nativeInspectionRequestKey(plan);
    expect(nativeInspectionRequestKey({ ...plan, projectId: "another-project" })).not.toBe(key);
    expect(nativeInspectionRequestKey(nativeInspectionWindow(plan, 1))).not.toBe(key);
    expect(nativeInspectionRequestKey({ ...plan, revision: plan.revision + 1 })).not.toBe(key);
    expect(nativeInspectionRequestKey(null)).toBe("unavailable");
  });
});

describe("analysis candidates use the existing explicit review change without repairing content", () => {
  it("recomputes a source-bound candidate and serializes no live token, URL or pixel array", () => {
    const { project, plan, samples } = fixture(); const original = structuredClone(project);
    const candidate = analyzeNativeInspection(plan, samples, true).candidates[0];
    const change = nativeAnalysisReviewChange(project, plan, samples, candidate.key, true);
    expect(change.type).toBe("add_review"); if (change.type !== "add_review") throw new Error("Wrong change");
    const body = JSON.parse(change.body);
    expect(body.schema).toBe("motionwright.native-thumbnail-review-candidate/1");
    expect(body.interpretation).toBe("automated_candidate_recorded_for_human_review");
    expect(body.repair_applied).toBe(false);
    expect(body.evidence).toHaveLength(3);
    expect(change.body).not.toContain("DO-NOT-SERIALIZE-LIVE-GRANT");
    expect(change.body).not.toContain('"pixels"');
    expect(new TextEncoder().encode(change.body).length).toBeLessThanOrEqual(8_000);
    expect(project).toEqual(original);
  });
  it("rejects stale project revisions, generations and disappeared profiles", () => {
    const { project, plan, samples } = fixture();
    const key = analyzeNativeInspection(plan, samples, true).candidates[0].key;
    for (const current of [{ ...project, revision: project.revision + 1 }, { ...project, generation: "another" }, { ...project, deliverables: [] }]) {
      expect(() => nativeAnalysisReviewChange(current, plan, samples, key, true)).toThrow("stale");
    }
  });
  it("rejects a fabricated candidate and an unchanged run after the expectation is withdrawn", () => {
    const { project, plan, samples } = fixture();
    expect(() => nativeAnalysisReviewChange(project, plan, samples, "forged", true)).toThrow("not present");
    const key = analyzeNativeInspection(plan, samples, true).candidates[0].key;
    expect(() => nativeAnalysisReviewChange(project, plan, samples, key, false)).toThrow("not present");
  });
});
