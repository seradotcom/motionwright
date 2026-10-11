import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import type { Change, Project } from "./types";
import { seconds } from "./types";
import type { ProductionPlan } from "./creativeProduction";
import {
  applyMusicCutToProject,
  formatMarkedBeat,
  generateAuthorBeatGrid,
  inspectMusicCut,
  parseMarkedBeatGrid,
} from "./musicClock";

const SHA = "a".repeat(64);
const SOURCE = "6af88dd2-7667-4f94-96ba-fdb4bf4291b9";
const ts = (seconds: number) => ({num:String(seconds),den:"1"});
function project(): Project {
  const p=structuredClone(fixtureProject);
  p.schema_version=2;
  p.scenes=p.scenes.slice(0,2);
  p.deliverables=[];
  p.markers=[];
  p.locks=[];
  p.reviews=[];
  p.audio.cues=[];
  p.audio.transcript=[];
  p.assets=[{
    id:SOURCE,name:"Owner audio.wav",media_type:"audio/wav",
    content_sha256:SHA,source_revision:"operator-marked",
  }];
  const plan:ProductionPlan={
    objective:"Honor an original product film rhythm",
    audience:"Editors",
    concept:"Keep the cuts aligned to reviewed beat timestamps",
    reference_constraints:[],exclusions:["Do not infer listening or waveform analysis."],
    shots:p.scenes.map(scene=>({
      scene_id:scene.id,purpose:scene.objective,claim_ids:[],asset_ids:[],evidence_kind:"graphic_study",
    })),
    clock:{kind:"music",asset_id:SOURCE,beats:[ts(0),ts(5),ts(8),ts(13)]},
    approval:null,
  };
  p.production_design={plan,heroes:[],capsules:[],patches:[]};
  return p;
}

describe("MW05-E08-01 source-bound author-marked music clock",()=>{
  it("accepts exact increasing decimals and canonicalizes millisecond fractions",()=>{
    const beats=parseMarkedBeatGrid("0.000\n0.500\n1.250\n2.000");
    expect(beats).toEqual([
      ts(0),{num:"1",den:"2"},{num:"5",den:"4"},ts(2),
    ]);
    expect(beats.map(formatMarkedBeat)).toEqual(["0.000","0.500","1.250","2.000"]);
  });
  it("keeps nonmillisecond BPM beats rational, never rounds to a frame",()=>{
    const ticks=generateAuthorBeatGrid(90,4,ts(0));
    expect(ticks).toEqual([ts(0),{num:"2",den:"3"},{num:"4",den:"3"},ts(2)]);
    expect(ticks.map(formatMarkedBeat)).toEqual(["0.000","2/3","4/3","2.000"]);
    expect(parseMarkedBeatGrid(ticks.map(formatMarkedBeat).join("\n"))).toEqual(ticks);
  });
  it("preserves an exact offset in a generated irregular grid",()=>{
    const ticks=generateAuthorBeatGrid(73,3,{num:"7",den:"2"});
    expect(ticks[0]).toEqual({num:"7",den:"2"});
    expect(ticks[1]).toEqual({num:"631",den:"146"});
    expect(ticks[2]).toEqual({num:"751",den:"146"});
  });
  it("rejects malformed, duplicate, unsorted, negative and overbudget beat grids",()=>{
    for(const bad of ["","1\n1","3\n2","1.0001","-1","NaN","1e3","1/0","1/3\n1/4",
      "99999","86400.001","1".repeat(9000),Array(514).fill("1").join("\n")]) {
      expect(()=>parseMarkedBeatGrid(bad)).toThrow();
    }
    expect(()=>generateAuthorBeatGrid(0,8,ts(0))).toThrow();
    expect(()=>generateAuthorBeatGrid(90.5,8,ts(0))).toThrow();
    expect(()=>generateAuthorBeatGrid(90,900,ts(0))).toThrow();
  });
  it("generates a source-bound, exact ripple impact before any mutation",()=>{
    const p=project();
    p.audio.cues.push({id:crypto.randomUUID(),label:"Narrator enters",at:ts(10),source_segment_id:null,evidence:"manual"});
    p.markers.push({id:crypto.randomUUID(),at:ts(11),label:"Key message"});
    const prior=structuredClone(p);
    const result=inspectMusicCut(p,p.scenes[0].id,2);
    expect(result).toMatchObject({
      beat_index:2,asset_id:SOURCE,source_sha256:SHA,
      original_end:ts(7),target_end:ts(8),old_duration:ts(7),
      new_duration:ts(8),ripple:ts(1),
      affected_cue_count:1,affected_marker_count:1,affected_transcript_count:0,
      anchors_need_acknowledgement:true,
    });
    expect(result.downstream_scene_ids).toEqual([p.scenes[1].id]);
    expect(p).toEqual(prior);
  });
  it("keeps the native motion cut a single revisioned project change",async()=>{
    const p=project();
    const first=p.scenes[0].id;
    const change:Change={
      type:"snap_scene_cut_to_music",scene_id:first,beat_index:2,
      source_sha256:SHA,base_revision:p.revision,acknowledge_absolute_anchors:false,
    };
    const next=await applyChange(p,change);
    expect(next.revision).toBe(p.revision+1);
    expect(next.scenes[0].duration).toEqual(ts(8));
    expect(next.scenes[1].start).toEqual(ts(8));
    expect(next.scenes[1].duration).toEqual(ts(9));
    expect(next.scenes[0].status).toBe("draft");
    expect(p.scenes[0].duration).toEqual(ts(7));
    expect(next.production_design?.plan?.clock).toEqual(p.production_design?.plan?.clock);
  });
  it("fails closed if absolute anchors are not acknowledged, without any edits",async()=>{
    const p=project();
    p.audio.cues.push({id:crypto.randomUUID(),label:"Audio cue",at:ts(8),source_segment_id:null,evidence:"manual"});
    const original=structuredClone(p);
    await expect(applyChange(p,{
      type:"snap_scene_cut_to_music",scene_id:p.scenes[0].id,beat_index:2,
      source_sha256:SHA,base_revision:p.revision,acknowledge_absolute_anchors:false,
    })).rejects.toThrow("Acknowledge");
    expect(p).toEqual(original);
    const next=await applyChange(p,{
      type:"snap_scene_cut_to_music",scene_id:p.scenes[0].id,beat_index:2,
      source_sha256:SHA,base_revision:p.revision,acknowledge_absolute_anchors:true,
    });
    expect(next.audio.cues).toEqual(p.audio.cues);
    expect(next.scenes[1].start).toEqual(ts(8));
  });
  it("rejects stale base revisions and changed audio bytes",async()=>{
    const p=project();
    const request:Change={
      type:"snap_scene_cut_to_music",scene_id:p.scenes[0].id,beat_index:2,
      source_sha256:SHA,base_revision:p.revision,acknowledge_absolute_anchors:true,
    };
    await expect(applyChange(p,{...request,base_revision:p.revision-1})).rejects.toThrow("stale");
    p.assets[0].content_sha256="b".repeat(64);
    expect(()=>inspectMusicCut(p,p.scenes[0].id,2,SHA)).toThrow("changed");
    await expect(applyChange(p,request)).rejects.toThrow("changed");
  });
  it("rejects global and downstream scene timing locks",()=>{
    const p=project();
    p.locks.push({id:crypto.randomUUID(),resource:"project:"+p.id,kind:"timing",note:"Approved cut"});
    expect(()=>inspectMusicCut(p,p.scenes[0].id,2)).toThrow("lock");
    p.locks[0].resource="scene:"+p.scenes[1].id;
    expect(()=>inspectMusicCut(p,p.scenes[0].id,2)).toThrow("lock");
  });
  it("rejects affected delivery timing locks and preserves protected cuts",()=>{
    const p=project();
    p.deliverables=structuredClone(fixtureProject.deliverables).slice(0,1);
    p.deliverables[0].timing_locked=true;
    p.deliverables[0].included_scene_ids=[];
    expect(()=>inspectMusicCut(p,p.scenes[0].id,2)).toThrow("timing-locked");
  });
  it("rejects a target before the scene start or an identical cut",()=>{
    const p=project();
    expect(()=>inspectMusicCut(p,p.scenes[1].id,1)).toThrow("positive scene duration");
    p.production_design!.plan!.clock={kind:"music",asset_id:SOURCE,beats:[ts(7),ts(8)]};
    expect(()=>inspectMusicCut(p,p.scenes[0].id,0)).toThrow("already matches");
  });
  it("protects scene-local authored beats and keyframes when shrinking",()=>{
    const p=project();
    p.scenes[0].beats.push({
      id:crypto.randomUUID(),label:"Protected late beat",objective:"Do not truncate",
      start:ts(5),duration:ts(1),
    });
    expect(()=>inspectMusicCut(p,p.scenes[0].id,1)).toThrow("authored scene beat");
    p.scenes[0].beats.pop();
    p.scenes[0].nodes[0].keyframes=[{at:ts(5),property:"x",value:20,interpolation:"hold"}];
    expect(()=>inspectMusicCut(p,p.scenes[0].id,1)).toThrow("authored canvas keyframe");
  });
  it("keeps an approval invalidated, and fixed markers intact",()=>{
    const p=project();
    p.production_design!.plan!.approval={reviewer:"Operator",note:"This concept was approved",content_sha256:"c".repeat(64)};
    p.markers=[{id:crypto.randomUUID(),at:ts(9),label:"Do not silently shift"}];
    const preview=inspectMusicCut(p,p.scenes[0].id,2);
    expect(preview.approval_invalidated).toBe(true);
    applyMusicCutToProject(p,preview);
    expect(p.production_design!.plan!.approval).toBeNull();
    expect(p.markers[0].at).toEqual(ts(9));
    expect(seconds(p.scenes[1].start)).toBe(8);
  });
  it("serializes a music-cut change without leaking unknown fields",()=>{
    const p=project();
    const request:Change={
      type:"snap_scene_cut_to_music",scene_id:p.scenes[0].id,beat_index:2,
      source_sha256:SHA,base_revision:p.revision,acknowledge_absolute_anchors:false,
    };
    expect(JSON.parse(JSON.stringify(request))).toEqual(request);
    expect(inspectMusicCut(JSON.parse(JSON.stringify(p)),p.scenes[0].id,2).target_end).toEqual(ts(8));
  });
});
