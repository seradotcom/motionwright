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
    frame_count: 450,
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
    frame_count: 450,
  }],
};

describe("native frame selection follows authored scene timing", () => {
  it("maps the selected scene and next scene into the same canonical segment", () => {
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 0)?.frameIndex).toBe(0);
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 0.5)?.frameIndex).toBe(15);
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, evidence, 7)?.frameIndex).toBe(210);
    expect(nativeFrameAtPlayhead(project, project.scenes[1], profile, evidence, 7.5)?.frameIndex).toBe(225);
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

  it("uses exact render fps instead of assuming 24 or display NDF rounding", () => {
    const nt = { ...evidence, frame_rate: { num: 30000, den: 1001 } };
    expect(nativeFrameAtPlayhead(project, scene, profile, nt, 0.5)?.frameIndex).toBe(14);
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, Number.NaN)).toBeNull();
    expect(nativeFrameAtPlayhead(project, scene, profile, evidence, 10_000)?.frameIndex).toBe(210);
  });
});
