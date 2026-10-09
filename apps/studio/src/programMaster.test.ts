import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { editorialTimeForMasterMedia, programMasterAtPlayhead } from "./programMaster";
import type { MltAvMasterEvidence, MotionCanvasRenderEvidence, Project } from "./types";

const project = structuredClone(fixtureProject);
const first = project.scenes[0];
const second = project.scenes[1];
const profile = project.deliverables[0];
const visual: MotionCanvasRenderEvidence = {
  project_resource: "project:" + project.id,
  generation: project.generation,
  revision: project.revision,
  deliverable_id: profile.id,
  frame_rate: { num: 30, den: 1 },
  segments: [{
    segment_id: "real-native-segment",
    scene_ids: [first.id, second.id],
    frame_count: 480,
    plan_ref: "plan",
    fingerprint: "digest",
    job_ref: "job",
    artifact: {},
    verification: {},
  }],
};
const av: MltAvMasterEvidence = {
  export_token: "session-scope-token",
  project_resource: visual.project_resource,
  generation: visual.generation,
  revision: visual.revision,
  deliverable_id: profile.id,
  motion_segment_id: "real-native-segment",
  frame_rate: visual.frame_rate,
  frame_count: 480,
  mezzanine: {},
  source_audio: { relative_path: "owned.wav", sha256: "a".repeat(64), sample_rate: 48000, channels: 2 },
  master: { profile: "h264-aac-mp4" },
  decoded_audio: {},
  sync: null,
};

describe("source-bound Program AV monitor clock projection", () => {
  it("maps current visual and audio masters to both eligible source scenes", () => {
    const atOpening = programMasterAtPlayhead(project, first, profile, visual, av, 0);
    const afterHandoff = programMasterAtPlayhead(project, second, profile, visual, av, 8);
    expect(atOpening).toMatchObject({
      exportToken: "session-scope-token", segmentId: "real-native-segment",
      mediaTimeSeconds: 0, mediaDurationSeconds: 16,
    });
    expect(afterHandoff?.mediaTimeSeconds).toBeCloseTo(8, 8);
    expect(editorialTimeForMasterMedia(project, afterHandoff!, 8)).toBeCloseTo(8, 8);
    expect(editorialTimeForMasterMedia(project, afterHandoff!, 0)).toBeCloseTo(0, 8);
    expect(editorialTimeForMasterMedia(project, afterHandoff!, 16)).toBeCloseTo(15.999, 8);
  });

  it("rejects stale revisions, changed output profiles, unknown tokens and unrelated renderers", () => {
    expect(programMasterAtPlayhead(project, first, profile, visual, null, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, visual, { ...av, export_token: null }, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, project.deliverables[1], visual, av, 0)).toBeNull();
    expect(programMasterAtPlayhead({ ...project, revision: project.revision + 1 }, first, profile, visual, av, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, project.scenes[2], profile, visual, av, 16)).toBeNull();
    expect(programMasterAtPlayhead(project, second, profile, visual, av, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, visual, av, 7)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, visual, { ...av, motion_segment_id: "other" }, 0)).toBeNull();
  });

  it("rejects silently reinterpreting a different frame rate, scene cut or master length", () => {
    expect(programMasterAtPlayhead(project, first, profile, visual, { ...av, frame_count: 900 }, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, visual, {
      ...av, frame_rate: { num: 30000, den: 1001 },
    }, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, {
      ...visual, segments: [{ ...visual.segments[0], scene_ids: [first.id] }],
    }, av, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, {
      ...visual, segments: [visual.segments[0], visual.segments[0]],
    }, av, 0)).toBeNull();
    expect(programMasterAtPlayhead(project, first, profile, visual, av, Number.NaN)).toBeNull();
  });

  it("maps explicitly selected noncontiguous cuts onto authored project offsets", () => {
    const thirdProject: Project = structuredClone(project);
    const included = [thirdProject.scenes[0], thirdProject.scenes[1]];
    const altered = { ...visual, segments: [{
      ...visual.segments[0], scene_ids: [included[1].id], frame_count: 270,
    }] };
    const mastering = { ...av, frame_count: 270 };
    const selected = programMasterAtPlayhead(thirdProject, included[1], profile, altered, mastering, 8);
    expect(selected?.mediaTimeSeconds).toBeCloseTo(1, 8);
    expect(editorialTimeForMasterMedia(thirdProject, selected!, 1)).toBeCloseTo(8, 8);
    expect(editorialTimeForMasterMedia(thirdProject, selected!, -1)).toBeNull();
    expect(editorialTimeForMasterMedia(thirdProject, selected!, 20)).toBeNull();
  });
});
