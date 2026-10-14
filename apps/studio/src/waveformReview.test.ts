import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import type { VoiceTrack, WaveformPage } from "./types";
import { analyzeWaveformPage, validateWaveformReviewPage, waveformReviewChange, waveformReviewKey } from "./waveformReview";

function fixture() {
  const project = structuredClone(fixtureProject);
  const track: VoiceTrack = { id: "018f0000-0000-7000-8000-000000000990", asset_id: "018f0000-0000-7000-8000-000000000991",
    label: "Synthetic measured take", sample_rate_hz: 8000, channels: 2,
    measured_duration: { num: "3", den: "10" }, source_sha256: "ab".repeat(32), loudness_lufs: null, true_peak_dbfs: null };
  project.audio.voice_tracks = [track]; project.audio.active_voice_track_id = track.id;
  const page: WaveformPage = { schema: "motionwright.waveform-page.v1", algorithm: "sample-peak-max-abs-v1",
    source_sha256: track.source_sha256, sample_rate_hz: 8000, channels: 2,
    frames_per_peak: 400, total_frames: 2400, peak_count: 6, page_index: 0, page_size: 6,
    start_peak: 0, peaks: [.1, 1, 1, 0, 0, 0], has_previous: false, has_next: false };
  return { project, track, page, key: waveformReviewKey(project, track, 0, 6) };
}

describe("measured waveform page source and coverage", () => {
  it("validates a page against the independently selected track and request", () => {
    const { project, track, page } = fixture();
    expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).not.toThrow();
  });
  it.each([
    { source_sha256: "cd".repeat(32) }, { schema: "unknown" },
    { sample_rate_hz: 48000 }, { channels: 1 }, { algorithm: "made-up" },
    { page_index: 1 }, { page_size: 12 }, { start_peak: 1 },
    { has_next: true }, { has_previous: true }, { peak_count: 7 }, { total_frames: 2399 },
  ])("rejects a mismatched or contradictory page: %j", override => {
    const { project, track, page } = fixture();
    expect(() => validateWaveformReviewPage(project, track, { ...page, ...override } as WaveformPage, 0, 6)).toThrow();
  });
  it.each([NaN, Infinity, -0.1, 1.1])("rejects invalid normalized peak %s", peak => {
    const { project, track, page } = fixture();
    expect(() => validateWaveformReviewPage(project, track, { ...page, peaks: [peak, ...page.peaks.slice(1)] }, 0, 6)).toThrow("invalid");
  });
  it("rejects an incomplete page instead of treating absent bins as silence", () => {
    const { project, track, page } = fixture();
    expect(() => validateWaveformReviewPage(project, track, { ...page, peaks: [0, 0] }, 0, 6)).toThrow("incomplete");
  });
  it("rejects a page for an inactive, missing or substituted voice track", () => {
    const { project, track, page } = fixture();
    project.audio.active_voice_track_id = null;
    expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).toThrow("active");
    project.audio.active_voice_track_id = track.id;
    project.audio.voice_tracks = [{ ...track, asset_id: "another-source-asset" }];
    expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).toThrow("active");
  });
  it("checks the exact sample-count duration, not rounded display milliseconds", () => {
    const { project, track, page } = fixture();
    track.measured_duration = { num: "299", den: "1000" };
    expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).toThrow("extent");
    track.measured_duration = { num: "6", den: "20" };
    expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).not.toThrow();
  });
  it("rejects ambiguous counts, invalid rationals and unsafe page offsets", () => {
    const { project, track, page } = fixture();
    expect(() => validateWaveformReviewPage(project, track, { ...page, frames_per_peak: 0 }, 0, 6)).toThrow();
    expect(() => validateWaveformReviewPage(project, track, page, Number.MAX_SAFE_INTEGER, 6)).toThrow();
    track.measured_duration = { num: "3", den: "0" };
    expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).toThrow("duration");
  });
});

describe("page-local peak review candidates", () => {
  it("flags near-full-scale bins, not unmeasured true peak or definite clipping", () => {
    const { project, track, page } = fixture();
    const result = analyzeWaveformPage(project, track, page, 0, 6);
    expect(result.candidates).toHaveLength(1);
    expect(result.candidates[0]).toMatchObject({ code: "near_full_scale_proxy", start: { num: "1", den: "20" }, end: { num: "3", den: "20" } });
    expect(result.candidates[0].consequence).toContain("not proof of clipping");
    expect(result.loudness).toBe("not_measured"); expect(result.truePeak).toBe("not_measured");
    expect(result.creativeApproval).toBe("not_assessed");
  });
  it("requires an explicit sound expectation before reporting quiet spans", () => {
    const { project, track, page } = fixture();
    const result = analyzeWaveformPage(project, track, page, 0, 6, true);
    const candidate = result.candidates.find(c => c.code === "expected_sound_quiet_proxy")!;
    expect(candidate.start).toEqual({ num: "3", den: "20" });
    expect(candidate.end).toEqual({ num: "3", den: "10" });
    expect(candidate.consequence).toContain("Silence may be intentional");
  });
  it("does not promote an empty candidate list to a quality PASS", () => {
    const { project, track, page } = fixture(); page.peaks.fill(.2);
    const result = analyzeWaveformPage(project, track, page, 0, 6, true);
    expect(result.candidates).toEqual([]); expect(result.creativeApproval).toBe("not_assessed");
  });
  it("exposes page truncation and uses the actual final sample count", () => {
    const { project, track, page } = fixture();
    track.measured_duration = { num: "39", den: "80" };
    Object.assign(page, { total_frames: 3900, peak_count: 10, page_index: 1, start_peak: 6,
      peaks: [0, 0, 0, 0], has_previous: true });
    const result = analyzeWaveformPage(project, track, page, 1, 6, true);
    expect(result.candidates[0].clippedToPage).toBe(true);
    expect(result.candidates[0].start).toEqual({ num: "3", den: "10" });
    expect(result.candidates[0].end).toEqual(track.measured_duration);
    expect(result.inspectedPeaks).toBe(4); expect(result.totalPeaks).toBe(10);
  });
  it("does not extend a short quiet segment to reach a threshold", () => {
    const { project, track, page } = fixture(); page.peaks = [.2, .2, .2, .2, 0, 0];
    expect(analyzeWaveformPage(project, track, page, 0, 6, true).candidates).toEqual([]);
  });
  it("bounds detailed candidates while exposing the omitted count", () => {
    const { project, track, page } = fixture(); track.measured_duration = { num: "2", den: "1" };
    Object.assign(page, { total_frames: 16000, peak_count: 40, page_size: 40,
      peaks: Array.from({ length: 40 }, (_, i) => i % 2 === 0 ? 1 : .2) });
    const result = analyzeWaveformPage(project, track, page, 0, 40);
    expect(result.candidates).toHaveLength(16); expect(result.omittedCandidates).toBe(4);
  });
});

describe("source-bound analytical audio review uses existing review/CAS contracts", () => {
  it("records take-relative evidence without inventing a project-timeline anchor", () => {
    const { project, track, page, key } = fixture(), original = structuredClone(project);
    const candidate = analyzeWaveformPage(project, track, page, 0, 6).candidates[0];
    const change = waveformReviewChange(project, track, page, 0, 6, false, candidate.key, key);
    expect(change.type).toBe("add_review"); if (change.type !== "add_review") throw new Error("Wrong change");
    expect(change.kind).toBe("technical"); expect(change.resource).toBe("project:" + project.id);
    expect(change.start).toBeNull(); expect(change.end).toBeNull();
    const body = JSON.parse(change.body);
    expect(body.coverage.coordinate_space).toBe("voice_take_relative");
    expect(body.source.source_sha256).toBe(track.source_sha256);
    expect(body.repair_applied).toBe(false);
    expect(body.creative_approval).toBe("not_assessed");
    expect(new TextEncoder().encode(change.body).length).toBeLessThanOrEqual(8_000);
    expect(project).toEqual(original);
  });
  it("does not admit an old page after a revision or generation changes", () => {
    const { project, track, page, key } = fixture();
    const candidate = analyzeWaveformPage(project, track, page, 0, 6).candidates[0].key;
    expect(() => waveformReviewChange({ ...project, revision: project.revision + 1 }, track, page, 0, 6, false, candidate, key)).toThrow("changed");
    expect(() => waveformReviewChange({ ...project, generation: "new-generation" }, track, page, 0, 6, false, candidate, key)).toThrow("changed");
  });
  it("rejects fabricated findings and withdrawn expectations", () => {
    const { project, track, page, key } = fixture();
    expect(() => waveformReviewChange(project, track, page, 0, 6, false, "forged", key)).toThrow("absent");
    const quiet = analyzeWaveformPage(project, track, page, 0, 6, true).candidates.find(c => c.code === "expected_sound_quiet_proxy")!;
    expect(() => waveformReviewChange(project, track, page, 0, 6, false, quiet.key, key)).toThrow("absent");
  });
  it("keys all source identity and pagination fields, never just a track ID", () => {
    const { project, track, key } = fixture();
    expect(waveformReviewKey(project, { ...track, source_sha256: "cd".repeat(32) }, 0, 6)).not.toBe(key);
    expect(waveformReviewKey(project, track, 1, 6)).not.toBe(key);
    expect(waveformReviewKey(project, track, 0, 7)).not.toBe(key);
    expect(waveformReviewKey({ ...project, id: "different-project" }, track, 0, 6)).not.toBe(key);
    expect(waveformReviewKey(project, null, 0, 6)).toBe("unavailable");
  });
});

it("matches the native proxy's case-insensitive hexadecimal digest comparison", () => {
  const { project, track, page } = fixture();
  track.source_sha256 = track.source_sha256.toUpperCase();
  expect(() => validateWaveformReviewPage(project, track, page, 0, 6)).not.toThrow();
  expect(() => validateWaveformReviewPage(project, track, { ...page, source_sha256: "invalid" }, 0, 6)).toThrow();
});

it("handles a complete empty source page without fabricating samples or approval", () => {
  const { project, track, page } = fixture();
  track.measured_duration = { num: "0", den: "1" };
  Object.assign(page, { total_frames: 0, peak_count: 0, peaks: [] });
  const analysis = analyzeWaveformPage(project, track, page, 0, 6, true);
  expect(analysis.inspectedPeaks).toBe(0);
  expect(analysis.candidates).toEqual([]);
  expect(analysis.creativeApproval).toBe("not_assessed");
});
