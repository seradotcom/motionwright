import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { resolveSceneRenderReceipt } from "./renderReceipt";
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
    segment_id: "mc-segment-one",
    scene_ids: [scene.id],
    frame_count: 210,
    plan_ref: "plan-1",
    fingerprint: "digest-1",
    job_ref: "job-1",
    artifact: { kind: "native-render" },
    verification: { status: "PASS" },
  }],
};

describe("scene-bound native render receipt metadata", () => {
  it("exposes a current receipt only for the same project, generation and profile", () => {
    const result = resolveSceneRenderReceipt(project, scene, profile, evidence);
    expect(result).toMatchObject({
      kind: "current",
      profileName: profile.name,
      revision: project.revision,
      segment: { frame_count: 210, job_ref: "job-1" },
    });
  });

  it("does not turn another delivery profile into this program's frame evidence", () => {
    const result = resolveSceneRenderReceipt(project, scene, project.deliverables[1], evidence);
    expect(result?.kind).toBe("other_profile");
  });

  it("preserves original revision and marks receipts stale after a creative edit", () => {
    const newRevision = { ...project, revision: project.revision + 1 };
    expect(resolveSceneRenderReceipt(newRevision, scene, profile, evidence)).toMatchObject({
      kind: "stale", revision: project.revision,
    });
  });

  it("does not attach another project or generation, another scene, or another renderer", () => {
    expect(resolveSceneRenderReceipt(project, scene, profile, null)).toBeNull();
    expect(resolveSceneRenderReceipt(project, scene, profile, {
      ...evidence, generation: "unrelated",
    })).toBeNull();
    expect(resolveSceneRenderReceipt(project, scene, profile, {
      ...evidence, project_resource: "project:other",
    })).toBeNull();
    expect(resolveSceneRenderReceipt(project, project.scenes[1], profile, evidence)).toBeNull();
    expect(resolveSceneRenderReceipt(project, { ...scene, renderer: "blender" }, profile, evidence)).toBeNull();
  });

  it("fails closed on unlinked or missing job receipts", () => {
    expect(resolveSceneRenderReceipt(project, scene, profile, {
      ...evidence, segments: [{ ...evidence.segments[0], frame_count: 0 }],
    })).toBeNull();
    expect(resolveSceneRenderReceipt(project, scene, profile, {
      ...evidence, segments: [{ ...evidence.segments[0], job_ref: "" }],
    })).toBeNull();
    expect(resolveSceneRenderReceipt(project, scene, profile, {
      ...evidence, deliverable_id: "missing-profile",
    })).toBeNull();
  });
});
