import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {defaultOriginalMix,emptyDeliveryRecipe} from "./deliveryRecipe";
import {inspectMusicCut,inspectMusicConform,mapMusicSourceBeat,musicClockOriginSamples,musicBeatClockLabel} from "./musicClock";
import {applyChange} from "./api";
import type {Project,RationalTime} from "./types";
const at=(n:number,d=1):RationalTime=>({num:String(n),den:String(d)});
const digest="a".repeat(64);
function sample(){
  const p:Project=structuredClone(fixtureProject);
  p.schema_version=2;
  p.scenes=p.scenes.slice(0,3);p.locks=[];p.reviews=[];p.markers=[];
  p.audio.cues=[];p.audio.transcript=[];p.audio.sfx_cues=[];
  p.scenes.forEach((s,i)=>{s.start=at([0,7,16][i]);s.duration=at([7,9,6][i]);s.beats=[];s.nodes=[];});
  const asset=p.assets.find(a=>a.media_type==="audio/wav")!;
  asset.content_sha256=digest;
  p.deliverables.forEach(profile=>{profile.timing_locked=false;profile.included_scene_ids=[];profile.protected_scene_ids=[];});
  p.production_design={heroes:[],capsules:[],patches:[],plan:{
    objective:"Edit the original source clock",audience:"Editors",concept:"Exact window mapping",
    reference_constraints:[],exclusions:[],shots:p.scenes.map(s=>({scene_id:s.id,purpose:s.objective,claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"})),
    clock:{kind:"music",asset_id:asset.id,beats:[at(0),at(8),at(18),at(180),at(25,7)]},approval:null,
  },delivery_recipes:{[p.deliverables[0].id]:{...emptyDeliveryRecipe(),
    original_mix:{...defaultOriginalMix(),music_asset_id:asset.id,music_source_start_sample:24001}}}};
  // Keep the saved grid strictly increasing, including a deliberately off-sample tick.
  if(p.production_design.plan?.clock.kind==="music")p.production_design.plan.clock.beats=[at(0),at(25,7),at(8),at(18),at(180)];
  return {p,asset,recipe:p.production_design.delivery_recipes![p.deliverables[0].id]};
}
describe("source clock follows the saved exact music window",()=>{
  it("maps a non-millisecond start to the project clock without rounding or changing source beats",async()=>{
    const {p,asset}=sample(),before=structuredClone(p);
    expect(musicClockOriginSamples(p,asset.id)).toBe(24001);
    expect(mapMusicSourceBeat(at(8),24001)).toEqual(at(359999,48000));
    expect(musicBeatClockLabel(p,asset.id,at(8))).toContain("359999/48000s timeline");
    const cut=inspectMusicCut(p,p.scenes[0].id,2,digest);
    expect(cut.target_end).toEqual(at(359999,48000));
    expect(cut.ripple).toEqual(at(23999,48000));
    expect(p).toEqual(before);
    const result=await applyChange(p,{type:"snap_scene_cut_to_music",scene_id:p.scenes[0].id,beat_index:2,
      source_sha256:digest,base_revision:p.revision,acknowledge_absolute_anchors:false});
    expect(result.scenes[1].start).toEqual(cut.target_end);
    expect(result.production_design?.plan?.clock).toEqual(before.production_design?.plan?.clock);
    expect(result.production_design?.delivery_recipes).toEqual(before.production_design?.delivery_recipes);
  });
  it("applies the same origin to every multi-scene cut boundary",()=>{
    const {p}=sample(),before=structuredClone(p);
    const targets=[{scene_id:p.scenes[0].id,beat_index:2},{scene_id:p.scenes[1].id,beat_index:3}];
    const review=inspectMusicConform(p,targets,digest);
    expect(review.scenes[0].new_duration).toEqual(at(359999,48000));
    expect(review.scenes[1].new_duration).toEqual(at(10));
    expect(review.scenes[2].new_start).toEqual(at(839999,48000));
    expect(p).toEqual(before);
  });
  it("rejects pre-window marks, fractional output sample counts and cut windows exceeding the decode budget",()=>{
    const {p}=sample();
    expect(()=>inspectMusicCut(p,p.scenes[0].id,0,digest)).toThrow("precedes");
    expect(()=>inspectMusicCut(p,p.scenes[0].id,1,digest)).toThrow("exact 48");
    expect(()=>inspectMusicCut(p,p.scenes[0].id,4,digest)).toThrow("180-second");
  });
  it("blocks incompatible variants rather than choosing an arbitrary source offset",()=>{
    const {p,asset,recipe}=sample();
    const other=structuredClone(recipe);delete other.original_mix!.music_source_start_sample;
    p.production_design!.delivery_recipes![p.deliverables[1].id]=other;
    expect(()=>musicClockOriginSamples(p,asset.id)).toThrow("different start samples");
    expect(()=>inspectMusicCut(p,p.scenes[0].id,2,digest)).toThrow("different start samples");
    expect(()=>inspectMusicConform(p,[{scene_id:p.scenes[0].id,beat_index:2},{scene_id:p.scenes[1].id,beat_index:3}],digest)).toThrow("different start samples");
    other.original_mix!.music_source_start_sample=24001;
    expect(musicClockOriginSamples(p,asset.id)).toBe(24001);
    other.original_mix!.music_asset_id="11111111-1111-4111-8111-111111111111";
    other.original_mix!.music_source_start_sample=0;
    expect(musicClockOriginSamples(p,asset.id)).toBe(24001);
  });
  it("treats legacy whole-source and explicit zero as the same origin",()=>{
    const {p,asset,recipe}=sample();
    recipe.original_mix!.music_source_start_sample=0;
    const other=structuredClone(recipe);delete other.original_mix!.music_source_start_sample;
    p.production_design!.delivery_recipes![p.deliverables[1].id]=other;
    expect(musicClockOriginSamples(p,asset.id)).toBe(0);
    expect(inspectMusicCut(p,p.scenes[0].id,2,digest).target_end).toEqual(at(8));
  });
});
