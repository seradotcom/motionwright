import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { editorialTimeForMasterMedia, programMasterAtPlayhead } from "./programMaster";
import type { MltAvMasterEvidence, NativeMultisegmentAvMasterEvidence, MotionCanvasRenderEvidence, Project } from "./types";

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

const multiVisual: MotionCanvasRenderEvidence = {
  ...visual,
  segments: [
    {...visual.segments[0], segment_id:"first-original", scene_ids:[first.id], frame_count:210},
    {...visual.segments[0], segment_id:"second-original", scene_ids:[second.id], frame_count:270},
  ],
};
const multi: NativeMultisegmentAvMasterEvidence = {
  project_resource:visual.project_resource, generation:visual.generation, revision:visual.revision,
  deliverable_id:profile.id, frame_count:480, video_segments:2, video_codec:"h264", audio_codec:"aac",
  audio_sample_rate:48000, audio_channels:2, master_sha256:"b".repeat(64), export_token:"original-multi",
  review_only:false, provider_project_cleanup:"not_requested_requires_foreground_broker_consent",
  evidence_scope:"native-multisegment-av-verified-technical-output-not-human-approved",
};

describe("source-bound multi-segment Program originals", () => {
  it("uses one original media clock across segment and scene boundaries", () => {
    const before=programMasterAtPlayhead(project,first,profile,multiVisual,multi,6.5);
    const after=programMasterAtPlayhead(project,second,profile,multiVisual,multi,8);
    expect(before).toMatchObject({exportToken:"original-multi",mediaTimeSeconds:6.5,
      mediaDurationSeconds:16,sceneIds:[first.id,second.id],reviewOnly:false,masterSha256:"b".repeat(64)});
    expect(after).toMatchObject({exportToken:"original-multi",mediaTimeSeconds:8,segmentId:"second-original"});
    expect(editorialTimeForMasterMedia(project,after!,6.5)).toBe(6.5);
    expect(editorialTimeForMasterMedia(project,after!,8)).toBe(8);
  });

  it("preserves compacted media time across an omitted project interval", () => {
    const cut=structuredClone(project);
    const final={...cut.scenes[1],start:{num:"12",den:"1"}};
    cut.scenes=[cut.scenes[0],{...cut.scenes[2],start:{num:"7",den:"1"},duration:{num:"5",den:"1"}},final];
    const output={...profile,included_scene_ids:[first.id,second.id]};
    const selection=programMasterAtPlayhead(cut,final,output,multiVisual,multi,13);
    expect(selection?.mediaTimeSeconds).toBe(8);
    expect(editorialTimeForMasterMedia(cut,selection!,8)).toBe(13);
  });

  it.each([
    {...multi,project_resource:"project:other"},
    {...multi,generation:"foreign"},
    {...multi,revision:multi.revision+1},
    {...multi,deliverable_id:"another-output"},
    {...multi,video_segments:3},
    {...multi,frame_count:481},
    {...multi,master_sha256:"not-original"},
  ])("rejects a different original identity or declared extent %#", altered => {
    expect(programMasterAtPlayhead(project,first,profile,multiVisual,altered,0)).toBeNull();
  });

  it("rejects shuffled, duplicate, missing and misclocked segment sources", () => {
    const a=multiVisual.segments[0],b=multiVisual.segments[1];
    for (const segments of [
      [b,a],
      [a,{...b,segment_id:a.segment_id}],
      [a,{...b,scene_ids:[first.id]}],
      [a,{...b,scene_ids:["missing"]}],
      [{...a,frame_count:240},{...b,frame_count:240}],
    ]) {
      expect(programMasterAtPlayhead(project,first,profile,{...multiVisual,segments},multi,0)).toBeNull();
    }
    expect(programMasterAtPlayhead(project,first,{...profile,included_scene_ids:[first.id]},multiVisual,multi,0)).toBeNull();
    expect(programMasterAtPlayhead(project,first,{...profile,frame_rate:{num:"60",den:"1"}},multiVisual,multi,0)).toBeNull();
    const foreign=structuredClone(project);foreign.scenes[1].renderer="blender";
    expect(programMasterAtPlayhead(foreign,foreign.scenes[0],profile,multiVisual,multi,0)).toBeNull();
  });

  it("carries caption review-only status and rejects inconsistent scope claims", () => {
    const captioned: NativeMultisegmentAvMasterEvidence={...multi,review_only:true,
      evidence_scope:"native-captioned-multisegment-av-review-only-not-decoded-pixel-approved"};
    const selection=programMasterAtPlayhead(project,first,profile,multiVisual,captioned,0);
    expect(selection?.reviewOnly).toBe(true);
    expect(programMasterAtPlayhead(project,first,profile,multiVisual,{...captioned,review_only:false},0)).toBeNull();
    expect(programMasterAtPlayhead(project,first,profile,multiVisual,{...multi,review_only:true},0)).toBeNull();
  });
});
