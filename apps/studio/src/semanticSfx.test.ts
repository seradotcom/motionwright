import { describe,expect,it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import {
  previewSemanticSfxEdit,validateSemanticSfx,validateSfxProject,SFX_CUE_SCHEMA,
} from "./semanticSfx";
import type { SemanticSfxCue } from "./semanticSfx";
import type { Project } from "./types";

const SHA="a".repeat(64);
const sceneId="00000000-0000-4000-8000-0000000000f0";
const assetId="00000000-0000-4000-8000-0000000000f1";
const cueId="00000000-0000-4000-8000-0000000000f2";
function ready():Project{
  const p=structuredClone(fixtureProject);
  p.schema_version=2;p.locks=[];p.branch_workspaces=[];p.reviews=[];
  p.scenes=p.scenes.slice(0,1);
  p.scenes[0].id=sceneId;
  p.scenes[0].start={num:"0",den:"1"};
  p.scenes[0].duration={num:"6",den:"1"};
  p.scenes[0].beats=[];
  p.assets.push({id:assetId,name:"Original source",media_type:"audio/wav",
    content_sha256:SHA,source_revision:"fixture-only"});
  p.audio.sfx_cues=[];
  return p;
}
function cue():SemanticSfxCue{
  return {
    schema:SFX_CUE_SCHEMA,id:cueId,scene_id:sceneId,event:"ui_focus",
    anchor:{kind:"scene_start"},offset_frames:5,
    source_asset_id:assetId,source_sha256:SHA,source_frames:30,
    gain_milli_db:-12000,attack_frames:3,release_frames:8,
    original_source_attestation:"I authored this original stereo WAV",
  };
}
describe("source-bound semantic SFX choreography at 30 fps / 48 kHz",()=>{
  it("calculates exact scene-anchored frames and actual 48 kHz PCM sample regions",async()=>{
    const p=ready(),placed=cue();
    expect(validateSemanticSfx(p,placed)).toEqual({
      cue_id:cueId,absolute_start_frame:5,absolute_end_frame:35,
      first_sample:8000,sample_count:48000,
    });
    const added=await applyChange(p,{type:"upsert_semantic_sfx",cue:placed});
    expect(added.audio.sfx_cues).toEqual([placed]);
    expect(added.scenes[0].status).toBe("draft");
    expect(added.assets).toEqual(p.assets);
    expect(added.revision).toBe(p.revision+1);
    await expect(applyChange(added,{type:"remove_asset",asset_id:assetId}))
      .rejects.toThrow(/SFX cue/);
    const removed=await applyChange(added,{type:"remove_semantic_sfx",cue_id:cueId});
    expect(removed.audio.sfx_cues).toEqual([]);
    const cleared=await applyChange(removed,{type:"remove_asset",asset_id:assetId});
    expect(cleared.assets.some(a=>a.id===assetId)).toBe(false);
  });
  it("anchors to explicitly authored beats without inferred clock/time stretch",()=>{
    const p=ready();
    p.scenes[0].beats=[{id:"00000000-0000-4000-8000-0000000000f3",
      label:"UI focus",objective:"Original semantic cue",
      start:{num:"4",den:"5"},duration:{num:"2",den:"5"}}];
    const anchored={...cue(),anchor:{kind:"beat_start" as const,
      beat_id:p.scenes[0].beats[0].id},offset_frames:-4};
    expect(validateSemanticSfx(p,anchored).first_sample).toBe(32000);
    const stale={...anchored,anchor:{kind:"beat_start" as const,
      beat_id:crypto.randomUUID()}};
    expect(()=>validateSemanticSfx(p,stale)).toThrow(/no longer/);
    p.scenes[0].beats[0].start={num:"1",den:"43"};
    expect(()=>validateSemanticSfx(p,anchored)).toThrow(/exact at 30/);
  });
  it("rejects invented WAV rights, unknown schema, overrun and source digest mismatch",()=>{
    const p=ready(),c=cue();
    const bad=[
      {...c,source_sha256:"b".repeat(64)},
      {...c,source_asset_id:crypto.randomUUID()},
      {...c,original_source_attestation:""},
      {...c,offset_frames:-10},
      {...c,source_frames:0},
      {...c,release_frames:30},
      {...c,gain_milli_db:1},
      {...c,source_frames:200},
      {...c,external_audio_url:"https://example.org/track.wav"} as unknown as SemanticSfxCue,
      {...c,anchor:{kind:"scene_start" as const,arbitrary_script:"bad"}} as unknown as SemanticSfxCue,
    ];
    for(const source of bad)expect(()=>validateSemanticSfx(p,source)).toThrow();
  });
  it("forbids five overlapping sources and duplicate identities without mutating source",async()=>{
    let p=ready();
    for(let n=0;n<4;n++){
      p=await applyChange(p,{type:"upsert_semantic_sfx",
        cue:{...cue(),id:crypto.randomUUID()}});
    }
    const original=structuredClone(p);
    await expect(applyChange(p,{type:"upsert_semantic_sfx",
      cue:{...cue(),id:crypto.randomUUID()}})).rejects.toThrow(/simultaneous/);
    expect(p).toEqual(original);
    p.audio.sfx_cues!.push(p.audio.sfx_cues![0]);
    expect(()=>validateSfxProject(p)).toThrow(/Duplicate/);
  });
  it("updates an original SFX in place, preserving identity, source and review anchors",async()=>{
    let p=ready();
    p=await applyChange(p,{type:"upsert_semantic_sfx",cue:cue()});
    const original=structuredClone(p);
    const edited={...cue(),offset_frames:9,gain_milli_db:-18000,
      attack_frames:4,release_frames:9};
    expect(previewSemanticSfxEdit(p,edited,cueId)).toEqual({
      cue_id:cueId,absolute_start_frame:9,absolute_end_frame:39,
      first_sample:14400,sample_count:48000,
    });
    expect(p).toEqual(original);
    const next=await applyChange(p,{type:"upsert_semantic_sfx",cue:edited});
    expect(next.revision).toBe(p.revision+1);
    expect(next.audio.sfx_cues).toEqual([edited]);
    expect(next.audio.sfx_cues![0].id).toBe(cueId);
    expect(next.scenes[0].status).toBe("draft");
    expect(next.assets).toEqual(p.assets);
    expect(validateSfxProject(next)[0].first_sample).toBe(14400);
  });
  it("fails closed for a moved, missing or duplicate original SFX editor identity",async()=>{
    const p=await applyChange(ready(),{type:"upsert_semantic_sfx",cue:cue()});
    const copy=structuredClone(p);
    const badScene={...cue(),scene_id:"00000000-0000-4000-8000-0000000000ab"};
    expect(()=>previewSemanticSfxEdit(p,badScene,cueId)).toThrow(/identity|scene/);
    expect(()=>previewSemanticSfxEdit(p,cue(),"unknown")).toThrow(/identity/);
    expect(()=>previewSemanticSfxEdit(p,cue(),null)).toThrow(/duplicates/);
    expect(()=>previewSemanticSfxEdit(p,{...cue(),source_sha256:"b".repeat(64)},cueId))
      .toThrow(/source|Asset/);
    expect(p).toEqual(copy);
  });
  it("rejects an invalid authored SFX tail before attempting owner CAS save",async()=>{
    const p=await applyChange(ready(),{type:"upsert_semantic_sfx",cue:cue()});
    const copy=structuredClone(p);
    expect(()=>previewSemanticSfxEdit(p,{...cue(),source_frames:200},cueId))
      .toThrow();
    expect(()=>previewSemanticSfxEdit(p,{...cue(),release_frames:31},cueId))
      .toThrow();
    expect(p).toEqual(copy);
  });
  it("loads legacy projects without a synthesized or approved SFX mix",()=>{
    const p=ready();
    delete p.audio.sfx_cues;
    expect(validateSfxProject(JSON.parse(JSON.stringify(p)))).toEqual([]);
  });
});
