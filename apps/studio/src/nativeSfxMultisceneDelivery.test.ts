import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { nativeSfxAuditionCues, originalSfxListeningIssue } from "./nativeSfxAudition";
import { SFX_CUE_SCHEMA } from "./semanticSfx";
import { currentOriginalSfxSourceProof, originalSfxAvReadiness } from "./sfxNativeDelivery";
import type { MltAvMasterEvidence, Project } from "./types";

const FIRST_SHA="a".repeat(64);
const THIRD_SHA="b".repeat(64);
const MIX_SHA="c".repeat(64);
const MP4_SHA="d".repeat(64);
const SCENES=[
  "00000000-0000-4000-8000-000000000501",
  "00000000-0000-4000-8000-000000000502",
  "00000000-0000-4000-8000-000000000503",
] as const;
const ASSETS=[
  "00000000-0000-4000-8000-000000000504",
  "00000000-0000-4000-8000-000000000505",
] as const;
const CUES=[
  "00000000-0000-4000-8000-000000000506",
  "00000000-0000-4000-8000-000000000507",
] as const;

function threeSceneOwner():{project:Project;master:MltAvMasterEvidence} {
  const project=structuredClone(fixtureProject);
  project.scenes=SCENES.map((id,i)=>{
    const scene=structuredClone(fixtureProject.scenes[0]);
    scene.id=id;
    scene.name=["Original ProductStage","Original procedural shapes","Measured data scene"][i];
    scene.renderer="motion-canvas";
    scene.start={num:String(i*6),den:"1"};
    scene.duration={num:"6",den:"1"};
    scene.beats=[];
    return scene;
  });
  project.assets.push(
    {id:ASSETS[0],name:"Original focus stereo.wav",media_type:"audio/wav",
      content_sha256:FIRST_SHA,source_revision:"fixture-generated original"},
    {id:ASSETS[1],name:"Original confirm stereo.wav",media_type:"audio/wav",
      content_sha256:THIRD_SHA,source_revision:"fixture-generated original"},
  );
  project.audio.sfx_cues=[
    {schema:SFX_CUE_SCHEMA,id:CUES[0],scene_id:SCENES[0],
      event:"ui_focus",anchor:{kind:"scene_start"},offset_frames:6,
      source_asset_id:ASSETS[0],source_sha256:FIRST_SHA,source_frames:24,
      gain_milli_db:-12000,attack_frames:3,release_frames:6,
      original_source_attestation:"Original generated PCM focus, rights not verified"},
    {schema:SFX_CUE_SCHEMA,id:CUES[1],scene_id:SCENES[2],
      event:"confirmation",anchor:{kind:"scene_start"},offset_frames:20,
      source_asset_id:ASSETS[1],source_sha256:THIRD_SHA,source_frames:24,
      gain_milli_db:-12000,attack_frames:3,release_frames:6,
      original_source_attestation:"Original generated PCM confirmation, rights not verified"},
  ];
  const profile=project.deliverables[0];
  const master:MltAvMasterEvidence={
    project_resource:"project:"+project.id,
    generation:project.generation,revision:project.revision,
    deliverable_id:profile.id,motion_segment_id:"one-real-540f-film-run",
    frame_rate:{num:30,den:1},frame_count:540,
    mezzanine:{},source_audio:{
      relative_path:"owner-only-digest-addressed.wav",sha256:MIX_SHA,
      sample_rate:48000,channels:2,
    },
    master:{artifact:{sha256:MP4_SHA}},decoded_audio:{},sync:null,
    semantic_sfx_source_proof:{
      project_id:project.id,generation:project.generation,revision:project.revision,
      deliverable_id:profile.id,master_frames:540,
      original_asset_sha256:[FIRST_SHA,THIRD_SHA],
      exact_pcm_sha256:MIX_SHA,sample_peak_pcm16:2760,
    },
    semantic_sfx_cue_count:2,
    semantic_sfx_human_creative_approval:"REQUIRED",
  };
  return {project,master};
}

describe("MW05-E08 540f original three-scene native SFX Studio contract",()=>{
  it("admits exactly the 540f whole-Film profile and never claims local CAS bytes",()=>{
    const {project,master}=threeSceneOwner();
    const profile=project.deliverables[0];
    const original=structuredClone(project);
    const preflight=originalSfxAvReadiness(project,profile,540,true,true,false);
    expect(preflight).toMatchObject({
      ready:true,cueCount:2,frames:540,sourceAuthority:"editorial_only",
    });
    expect(preflight.reason).toMatch(/Native CAS byte verification/);
    expect(currentOriginalSfxSourceProof(project,profile.id,master))
      .toMatchObject({master_frames:540,exact_pcm_sha256:MIX_SHA});
    expect(project).toEqual(original);
  });
  it("reviews both exact start samples in scene 0 and scene 2, preserving middle silence",()=>{
    const {project,master}=threeSceneOwner();
    const cues=nativeSfxAuditionCues(project,project.deliverables[0].id,master);
    expect(cues).toHaveLength(2);
    expect(cues.map(c=>c.atFrame)).toEqual([6,380]);
    expect(cues.map(c=>c.endFrame)).toEqual([30,404]);
    expect(cues.map(c=>c.absolutePcmSample)).toEqual([9600,608000]);
    expect(cues.map(c=>c.sceneId)).toEqual([SCENES[0],SCENES[2]]);
    expect(cues[0]).toMatchObject({event:"UI focus",originalSourceDigest:FIRST_SHA});
    expect(cues[1]).toMatchObject({event:"Confirmation",originalSourceDigest:THIRD_SHA});
    expect(cues.every(c=>c.atFrame<180||c.atFrame>=360)).toBe(true);
  });
  it("anchors an explicit third-scene manual review to 38/3–202/15 seconds",async()=>{
    const {project,master}=threeSceneOwner(),profile=project.deliverables[0];
    const cue=nativeSfxAuditionCues(project,profile.id,master)[1];
    const change=originalSfxListeningIssue(
      project,profile.id,master,cue,
      "The original confirmation cue is delayed compared with the third scene motion.",true,
    );
    expect(change.type).toBe("add_review");
    if(change.type!=="add_review")throw new Error("Wrong manual review kind");
    expect(change).toMatchObject({
      kind:"creative",resource:"scene:"+SCENES[2],profile_id:profile.id,
      start:{num:"38",den:"3"},end:{num:"202",den:"15"},
    });
    const data=JSON.parse(change.body);
    expect(data).toMatchObject({
      source:{
        cue_id:CUES[1],scene_id:SCENES[2],start_frame:380,
        end_frame_exclusive:404,absolute_pcm_sample:608000,
        original_wav_sha256:THIRD_SHA,
        exact_mix_pcm_sha256:MIX_SHA,native_mp4_sha256:MP4_SHA,
      },
      verdict:"needs_adjustment",
    });
    const updated=await applyChange(project,change);
    expect(updated.revision).toBe(project.revision+1);
    expect(updated.audio.sfx_cues).toEqual(project.audio.sfx_cues);
    expect(nativeSfxAuditionCues(updated,profile.id,master)).toEqual([]);
  });
  it("refuses shortened Film, missing scene or voice/mix proof substitution",()=>{
    const {project,master}=threeSceneOwner(),profile=project.deliverables[0];
    expect(originalSfxAvReadiness(project,profile,539,true,true,false).ready).toBe(false);
    expect(nativeSfxAuditionCues(project,profile.id,{...master,frame_count:539})).toEqual([]);
    const bad=structuredClone(project);
    bad.scenes[2].start={num:"121",den:"10"};
    expect(originalSfxAvReadiness(bad,profile,540,true,true,false).ready).toBe(false);
    expect(nativeSfxAuditionCues(bad,profile.id,master)).toEqual([]);
    const voiced=structuredClone(project);
    voiced.deliverables[0].voice_track_id="00000000-0000-4000-8000-0000000005ab";
    expect(nativeSfxAuditionCues(voiced,profile.id,master)).toEqual([]);
    const wrongMix=structuredClone(master);
    wrongMix.source_audio.sha256=FIRST_SHA;
    expect(currentOriginalSfxSourceProof(project,profile.id,wrongMix)).toBeNull();
    expect(nativeSfxAuditionCues(project,profile.id,wrongMix)).toEqual([]);
  });
});
