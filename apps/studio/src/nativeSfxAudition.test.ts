import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { nativeSfxAuditionCues, sourceFrameLabel } from "./nativeSfxAudition";
import { SFX_CUE_SCHEMA } from "./semanticSfx";
import type { MltAvMasterEvidence, Project } from "./types";

const SHA="a".repeat(64);
const sceneId="00000000-0000-4000-8000-0000000000a1";
const assetId="00000000-0000-4000-8000-0000000000a2";
const cueId="00000000-0000-4000-8000-0000000000a3";

function sample():{project:Project;receipt:MltAvMasterEvidence} {
  const project=structuredClone(fixtureProject);
  project.scenes=[project.scenes[0]];
  project.scenes[0].id=sceneId;
  project.scenes[0].renderer="motion-canvas";
  project.scenes[0].start={num:"0",den:"1"};
  project.scenes[0].duration={num:"6",den:"1"};
  project.scenes[0].beats=[];
  project.assets.push({
    id:assetId,name:"Original imported PCM16",
    media_type:"audio/wav",content_sha256:SHA,source_revision:"original source",
  });
  project.audio.sfx_cues=[{
    schema:SFX_CUE_SCHEMA,id:cueId,scene_id:sceneId,
    event:"ui_focus",anchor:{kind:"scene_start"},
    offset_frames:5,source_asset_id:assetId,source_sha256:SHA,
    source_frames:30,gain_milli_db:-12000,attack_frames:3,
    release_frames:8,original_source_attestation:"Owner original WAV",
  }];
  const id=project.deliverables[0].id;
  const receipt:MltAvMasterEvidence={
    project_resource:"project:"+project.id,
    generation:project.generation,revision:project.revision,
    deliverable_id:id,motion_segment_id:"owner-registered",
    frame_rate:{num:30,den:1},frame_count:180,
    mezzanine:{},source_audio:{relative_path:"owner-only.wav",sha256:SHA,
      sample_rate:48000,channels:2},
    master:{},decoded_audio:{},sync:null,
    semantic_sfx_source_proof:{
      project_id:project.id,generation:project.generation,
      revision:project.revision,deliverable_id:id,
      master_frames:180,original_asset_sha256:[SHA],
      exact_pcm_sha256:SHA,sample_peak_pcm16:2500,
    },
    semantic_sfx_cue_count:1,
    semantic_sfx_human_creative_approval:"REQUIRED",
  };
  return {project,receipt};
}
describe("v0.5 original SFX reviewed MP4 cue chronology",()=>{
  it("exposes original authored frame and exact sample offsets without changing source",()=>{
    const {project,receipt}=sample(),copy=structuredClone(project);
    const rows=nativeSfxAuditionCues(project,project.deliverables[0].id,receipt);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      cueId,sceneId,event:"UI focus",atFrame:5,endFrame:35,
      atSeconds:5/30,absolutePcmSample:8000,durationFrames:30,
      originalSourceDigest:SHA,
    });
    expect(project).toEqual(copy);
    expect(sourceFrameLabel(5)).toBe("00:05f");
    expect(sourceFrameLabel(36)).toBe("01:06f");
    expect(sourceFrameLabel(900)).toBe("30:00f");
  });
  it("sorts simultaneous and later authored cues by frame and stable cue ID",()=>{
    const {project,receipt}=sample();
    project.audio.sfx_cues!.push({
      ...project.audio.sfx_cues![0],
      id:"00000000-0000-4000-8000-0000000000a4",offset_frames:30,
    });
    receipt.semantic_sfx_cue_count=2;
    const rows=nativeSfxAuditionCues(project,project.deliverables[0].id,receipt);
    expect(rows.map(r=>r.atFrame)).toEqual([5,30]);
    expect(rows[1].absolutePcmSample).toBe(48000);
  });
  it("fails closed on stale creative revisions and source identity changes",()=>{
    const {project,receipt}=sample(),id=project.deliverables[0].id;
    expect(nativeSfxAuditionCues(project,id,receipt)).toHaveLength(1);
    expect(nativeSfxAuditionCues(project,"bad-profile",receipt)).toEqual([]);
    receipt.revision++;
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
    receipt.revision--;
    receipt.semantic_sfx_source_proof!.original_asset_sha256=["b".repeat(64)];
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
    receipt.semantic_sfx_source_proof!.original_asset_sha256=[SHA];
    receipt.semantic_sfx_human_creative_approval=undefined;
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
  });
  it("never treats a voice+SFX profile or non-30fps receipt as this master",()=>{
    const {project,receipt}=sample(),id=project.deliverables[0].id;
    project.deliverables[0].voice_track_id="00000000-0000-4000-8000-0000000000bb";
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
    project.deliverables[0].voice_track_id=null;
    receipt.frame_rate={num:30000,den:1001};
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
  });
  it("rejects malformed cues or releases beyond the returned master duration",()=>{
    const {project,receipt}=sample(),id=project.deliverables[0].id;
    receipt.frame_count=34;
    receipt.semantic_sfx_source_proof!.master_frames=34;
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
    receipt.frame_count=180;
    receipt.semantic_sfx_source_proof!.master_frames=180;
    project.audio.sfx_cues![0].source_frames=220;
    expect(nativeSfxAuditionCues(project,id,receipt)).toEqual([]);
    expect(sourceFrameLabel(-1)).toBe("Unavailable");
    expect(sourceFrameLabel(NaN)).toBe("Unavailable");
  });
});
