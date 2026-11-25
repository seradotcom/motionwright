import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import { audioMediaToProject, audioReviewScope, audioSampleToProject, buildAudioReviewClock, projectToAudioMedia } from "./audioReviewClock";
import type { Project } from "./types";

function fixture(duration = { num: "4", den: "1" }): Project {
  const project = structuredClone(fixtureBootstrap.project), id = "11111111-1111-4111-8111-111111111111";
  const assetId = "22222222-2222-4222-8222-222222222222", digest = "a".repeat(64);
  project.assets.push({ id: assetId, name: "original.wav", media_type: "audio/wav", content_sha256: digest, source_revision: null });
  project.audio.voice_tracks = [{ id, asset_id: assetId, label: "Measured voice", source_sha256: digest,
    sample_rate_hz: 48000, channels: 2, measured_duration: duration, loudness_lufs: null, true_peak_dbfs: null }];
  project.audio.active_voice_track_id = id;
  project.scenes = [{ ...structuredClone(project.scenes[0]), start: { num: "0", den: "1" }, duration }];
  project.deliverables[0].voice_track_id = id;
  project.deliverables[0].audio_sample_rate_hz = 48000;
  project.deliverables[0].included_scene_ids = [];
  return project;
}
const voice = (project: Project) => ({ kind: "voice" as const, sourceId: project.audio.active_voice_track_id! });
const mix = (project: Project) => ({ kind: "mix" as const, sourceId: project.deliverables[0].id });
describe("verified audio review on the saved project clock", () => {
  it("maps one measured source at exact samples without changing saved data", () => {
    const project = fixture(), before = structuredClone(project), clock = buildAudioReviewClock(project, voice(project));
    expect(audioSampleToProject(clock, 1)).toEqual({ num: "1", den: "48000" });
    expect(audioSampleToProject(clock, 48001)).toEqual({ num: "48001", den: "48000" });
    expect(audioSampleToProject(clock, clock.sampleCount)).toEqual({ num: "4", den: "1" });
    expect(audioMediaToProject(clock, 1.5)).toBe(1.5);
    expect(projectToAudioMedia(clock, 1.5)).toBe(1.5);
    expect(project).toEqual(before);
  });
  it("retains mono and non-48-kHz original clocks for active source review", () => {
    const p = fixture({ num: "1", den: "1" });
    p.audio.voice_tracks[0].sample_rate_hz = 44100; p.audio.voice_tracks[0].channels = 1;
    const clock = buildAudioReviewClock(p, voice(p));
    expect(clock.sampleCount).toBe(44100);
    expect(audioSampleToProject(clock, 1)).toEqual({ num: "1", den: "44100" });
    expect(() => buildAudioReviewClock(p, mix(p))).toThrow(/stereo 48-kHz/);
  });
  it("keeps alternate takes independent until a real active-track selection", () => {
    const p = fixture(); p.audio.voice_tracks.push({ ...p.audio.voice_tracks[0], id: "other" });
    expect(() => buildAudioReviewClock(p, { kind: "voice", sourceId: "other" })).toThrow(/Select it explicitly/);
  });
  it("refuses a source beyond the saved visual timeline instead of clipping it", () => {
    const p = fixture(); p.scenes[0].duration = { num: "3", den: "1" };
    expect(() => buildAudioReviewClock(p, voice(p))).toThrow(/extends beyond/);
    expect(() => buildAudioReviewClock(p, mix(p))).toThrow(/same measured duration/);
  });
  it("accumulates 30 fractional scene boundaries without per-scene sample rounding", () => {
    const p = fixture({ num: "1001", den: "1000" }), seed = p.scenes[0];
    const gcd = (a: number, b: number): number => b ? gcd(b, a % b) : a;
    p.scenes = Array.from({ length: 30 }, (_, i) => {
      const divisor = gcd(i * 1001, 30000);
      return { ...structuredClone(seed), id: "scene-" + i,
        start: { num: String(i * 1001 / divisor), den: String(30000 / divisor) }, duration: { num: "1001", den: "30000" } };
    });
    const before = structuredClone(p), clock = buildAudioReviewClock(p, mix(p));
    expect(clock.sampleCount).toBe(48048);
    expect(clock.spans).toHaveLength(30);
    expect(audioSampleToProject(clock, 1601)).toEqual({ num: "1601", den: "48000" });
    expect(audioSampleToProject(clock, 1602)).toEqual({ num: "267", den: "8000" });
    expect(audioSampleToProject(clock, 48048)).toEqual({ num: "1001", den: "1000" });
    expect(p).toEqual(before);
  });
  it("follows the current Native contract for partial or reordered original mixes", () => {
    const p = fixture(), seed = p.scenes[0];
    p.scenes = [ { ...seed, duration: { num: "2", den: "1" } },
      { ...structuredClone(seed), id: "second", start: { num: "2", den: "1" }, duration: { num: "2", den: "1" } } ];
    p.deliverables[0].included_scene_ids = [seed.id];
    expect(() => buildAudioReviewClock(p, mix(p))).toThrow(/complete original cut/);
    p.deliverables[0].included_scene_ids = ["second", seed.id];
    expect(() => buildAudioReviewClock(p, mix(p))).toThrow(/complete original cut/);
    p.deliverables[0].included_scene_ids = [seed.id, "second"];
    expect(buildAudioReviewClock(p, mix(p)).sampleCount).toBe(192000);
  });
  it("rejects gaps, overlaps, malformed clocks and source-digest drift", () => {
    for (const mutate of [
      (p: Project) => { p.scenes[0].start = { num: "1", den: "1" }; },
      (p: Project) => { p.scenes[0].duration = { num: "4", den: "0" }; },
      (p: Project) => { p.scenes[0].duration = { num: "8", den: "2" }; },
      (p: Project) => { p.audio.voice_tracks[0].source_sha256 = "b".repeat(64); },
      (p: Project) => { p.deliverables[0].voice_track_id = "other"; },
    ]) {
      const p = fixture(); mutate(p); expect(() => buildAudioReviewClock(p, mix(p))).toThrow();
    }
  });
  it("leaves unavailable project times and invalid media observations unmapped", () => {
    const p = fixture(), clock = buildAudioReviewClock(p, voice(p));
    for (const value of [-1, NaN, Infinity, 4.1]) {
      expect(projectToAudioMedia(clock, value)).toBeNull();
      expect(audioMediaToProject(clock, value)).toBeNull();
    }
    expect(audioSampleToProject(clock, .5)).toBeNull();
    expect(audioSampleToProject(clock, clock.sampleCount + 1)).toBeNull();
    expect(projectToAudioMedia(clock, 4)).toBe(4);
  });
  it("invalidates every project, generation, branch or revision identity change", () => {
    const p = fixture(), original = audioReviewScope(p);
    for (const key of ["id", "generation", "active_branch"] as const) {
      const changed = structuredClone(p); changed[key] = "different";
      expect(audioReviewScope(changed)).not.toBe(original);
    }
    p.revision++; expect(audioReviewScope(p)).not.toBe(original);
  });
});
