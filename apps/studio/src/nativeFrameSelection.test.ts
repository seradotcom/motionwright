import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { nativeFrameAtPlayhead } from "./nativeFrameSelection";
import type { MotionCanvasRenderEvidence } from "./types";

const project = structuredClone(fixtureProject);
const scene = project.scenes[0];
const profile = project.deliverables[0];
const evidence: MotionCanvasRenderEvidence = {
  project_resource: "project:" + project.id,
  generation: project.generation,
  revision: project.revision,
  deliverable_id: profile.id,
  frame_rate: { num: 30, den: 1 },
  segments: [{
    segment_id: "test-segment",
    scene_ids: [project.scenes[0].id, project.scenes[1].id],
    frame_count: 480,
    plan_ref: "plan",
    fingerprint: "fingerprint",
    job_ref: "job",
    artifact: {},
    verification: {},
  }],
  preview: [{
    token: "session-token",
    segment_id: "test-segment",
    scene_ids: [project.scenes[0].id, project.scenes[1].id],
    frame_count: 480,
  }],
};

describe("native frame selection follows authored scene timing", () => {
  it("maps the selected scene and next scene into the same canonical segment", () => {
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 0)?.frameIndex).toBe(0);
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 0.5)?.frameIndex).toBe(15);
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, evidence, 7)?.frameIndex).toBe(210);
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, evidence, 7.5)?.frameIndex).toBe(225);
    const reordered = structuredClone(evidence);
    reordered.segments[0].scene_ids.reverse();
    reordered.preview![0].scene_ids.reverse();
    const cut = structuredClone(project);
    const [reorderedProfile] = cut.deliverables;
    reorderedProfile.included_scene_ids = reordered.segments[0].scene_ids;
    expect(nativeFrameAtPlayhead(cut, cut.scenes[0], reorderedProfile, reordered, 0)?.frameIndex).toBe(270);
    expect(nativeFrameAtPlayhead(cut, cut.scenes[1], reorderedProfile, reordered, 7)?.frameIndex).toBe(0);
  });

  it("fails closed on stale revision, profile mismatch, missing or forged grants", () => {
    expect(nativeFrameAtPlayhead(project, scene, profile, null, 0)).toBeNull();
    expect(nativeFrameAtPlayhead({ ...project, revision: project.revision + 1 }, scene, profile, evidence, 0)).toBeNull();
    expect(nativeFrameAtPlayhead(project, scene, project.deliverables[1], evidence, 0)).toBeNull();
    expect(nativeFrameAtPlayhead(project, scene, profile, { ...evidence, preview: [] }, 0)).toBeNull();
    expect(nativeFrameAtPlayhead(project, scene, profile, {
      ...evidence,
      preview: [{ ...evidence.preview![0], frame_count: 400 }],
    }, 0)).toBeNull();
  });

  it("does not substitute an end frame from a grant that stops inside the selected scene", () => {
    const shortGrant = {
      ...evidence,
      segments: [{ ...evidence.segments[0], frame_count: 450 }],
      preview: [{ ...evidence.preview![0], frame_count: 450 }],
    };
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, shortGrant, 7.5)).toBeNull();
  });

  it("uses exact render fps instead of assuming 24 or display NDF rounding", () => {
    const nt = { ...evidence, frame_rate: { num: 30000, den: 1001 } };
    expect(nativeFrameAtPlayhead(project, scene, profile, nt, 0.5)?.frameIndex).toBe(14);
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, Number.NaN)).toBeNull();
  });

  it("keeps the selected scene half-open even when the next scene shares its grant", () => {
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 7 - 1 / 30)?.frameIndex).toBe(209);
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 7)).toBeNull();
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 10_000)).toBeNull();
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, evidence, 7 - 1 / 30)).toBeNull();
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, evidence, 7)?.frameIndex).toBe(210);
  });

  it("waits for the first native sample of a scene whose boundary falls between frames", () => {
    const source = structuredClone(project);
    const [first, next] = source.scenes;
    first.duration = { num: "1001", den: "20000" };
    next.start = first.duration;
    next.duration = first.duration;
    const original = {
      ...evidence,
      frame_rate: { num: 30000, den: 1001 },
      segments: [{ ...evidence.segments[0], frame_count: 3 }],
      preview: [{ ...evidence.preview![0], frame_count: 3 }],
    };
    expect(nativeFrameAtPlayhead(source, first, profile, original, 1001 / 30000)?.frameIndex).toBe(1);
    expect(nativeFrameAtPlayhead(source, next, profile, original, 1001 / 20000)).toBeNull();
    expect(nativeFrameAtPlayhead(source, next, profile, original, 1001 / 15000)?.frameIndex).toBe(2);
  });
});
