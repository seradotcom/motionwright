import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import type { Change, Project } from "./types";
import type { MusicConformTarget } from "./musicClock";
import { applyMusicConformToProject, inspectMusicConform, inspectMusicCut } from "./musicClock";
import { validateSfxProject, type SemanticSfxCue } from "./semanticSfx";
import type { ProductionPlan } from "./creativeProduction";

const SHA="a".repeat(64);
const ASSET="6af88dd2-7667-4f94-96ba-fdb4bf4291b9";
const at=(value:number)=>({num:String(value),den:"1"});
function original():Project {
  const p=structuredClone(fixtureProject);
  p.schema_version=2;
  p.scenes=p.scenes.slice(0,3);
  p.deliverables=[];
  p.markers=[];p.locks=[];p.reviews=[];
  p.audio.cues=[];p.audio.transcript=[];
  p.assets=[{id:ASSET,name:"Owner audio.wav",media_type:"audio/wav",
    content_sha256:SHA,source_revision:"author-marked"}];
  const plan:ProductionPlan={
    objective:"A complete original piece",audience:"Editors",
    concept:"Multiple rational cuts in one source revision",
    reference_constraints:[],exclusions:["Do not infer a measured beat detector."],
    shots:p.scenes.map(scene=>({scene_id:scene.id,purpose:scene.objective,
      claim_ids:[],asset_ids:[],evidence_kind:"graphic_study"})),
    clock:{kind:"music",asset_id:ASSET,beats:[at(0),at(5),at(8),at(13),at(18),at(25)]},
    approval:null,
  };
  p.production_design={plan,heroes:[],capsules:[],patches:[]};
  return p;
}
function targets(project:Project):MusicConformTarget[] {
  return [
    {scene_id:project.scenes[0].id,beat_index:2},
    {scene_id:project.scenes[1].id,beat_index:4},
  ];
}
function command(p:Project,ack=false):Extract<Change,{type:"conform_scenes_to_music"}> {
  return {
    type:"conform_scenes_to_music",
    targets:targets(p),source_sha256:SHA,base_revision:p.revision,
    acknowledge_absolute_anchors:ack,
  };
}
describe("MW05-E08-01 one-transaction multi-scene conform",()=>{
  it("provides a pure exact-rational preview of multiple scene cuts",()=>{
    const p=original();
    const baseline=structuredClone(p);
    const review=inspectMusicConform(p,targets(p),SHA);
    expect(review).toMatchObject({
      project_id:p.id,generation:p.generation,base_revision:p.revision,
      changed_scene_count:3,asset_id:ASSET,source_sha256:SHA,
      affected_cue_count:0,affected_marker_count:0,
      approval_invalidated:false,anchors_need_acknowledgement:false,
    });
    expect(review.scenes[0]).toMatchObject({
      new_start:at(0),new_duration:at(8),changed:true,beat_index:2,
    });
    expect(review.scenes[1]).toMatchObject({
      new_start:at(8),new_duration:at(10),changed:true,beat_index:4,
    });
    expect(review.scenes[2]).toMatchObject({
      new_start:at(18),old_duration:p.scenes[2].duration,
      changed:true,beat_index:null,
    });
    expect(review.original_duration).toEqual(at(28));
    expect(review.conformed_duration).toEqual(at(30));
    expect(p).toEqual(baseline);
  });
  it("commits one atomic revision and preserves every author-created scene identity",async()=>{
    const p=original();
    const next=await applyChange(p,command(p));
    expect(next.revision).toBe(p.revision+1);
    expect(next.scenes.map(s=>s.id)).toEqual(p.scenes.map(s=>s.id));
    expect(next.scenes[0].duration).toEqual(at(8));
    expect(next.scenes[1].start).toEqual(at(8));
    expect(next.scenes[1].duration).toEqual(at(10));
    expect(next.scenes[2].start).toEqual(at(18));
    expect(next.scenes.every(s=>s.status==="draft")).toBe(true);
    expect(p.scenes[0].duration).toEqual(at(7));
    expect(next.production_design?.plan?.clock).toEqual(p.production_design?.plan?.clock);
    expect(JSON.parse(JSON.stringify(next)).scenes).toEqual(next.scenes);
  });
  it("rejects a bad second boundary with zero partial effects",async()=>{
    const p=original();
    const baseline=structuredClone(p);
    const bad:Change={...command(p),targets:[
      {scene_id:p.scenes[0].id,beat_index:2},
      {scene_id:p.scenes[1].id,beat_index:2},
    ]};
    await expect(applyChange(p,bad)).rejects.toThrow("positive");
    expect(p).toEqual(baseline);
  });
  it("rejects revision drift, changed asset hash and nonexistent or unordered scenes",async()=>{
    const p=original();
    await expect(applyChange(p,{...command(p),base_revision:p.revision-1})).rejects.toThrow("stale");
    expect(()=>inspectMusicConform(p,[targets(p)[1],targets(p)[0]],SHA))
      .toThrow("unique");
    expect(()=>inspectMusicConform(p,[targets(p)[0],targets(p)[0]],SHA))
      .toThrow("unique");
    expect(()=>inspectMusicConform(p,[targets(p)[0],{scene_id:crypto.randomUUID(),beat_index:4}],SHA))
      .toThrow("Scene is not present");
    p.assets[0].content_sha256="b".repeat(64);
    await expect(applyChange(p,command(p))).rejects.toThrow("digest changed");
  });
  it("rejects protected project, downstream scene and deliverable timing locks",()=>{
    const p=original();
    p.locks.push({id:crypto.randomUUID(),resource:"scene:"+p.scenes[2].id,
      kind:"timing",note:"Approved closing start"});
    expect(()=>inspectMusicConform(p,targets(p),SHA)).toThrow("Protected");
    p.locks=[];
    p.deliverables=structuredClone(fixtureProject.deliverables.slice(0,1));
    p.deliverables[0].timing_locked=true;
    p.deliverables[0].included_scene_ids=[p.scenes[2].id];
    expect(()=>inspectMusicConform(p,targets(p),SHA)).toThrow("timing-locked");
  });
  it("does not silently move absolute audio cues or markers without explicit impact acknowledgement",async()=>{
    const p=original();
    p.audio.cues.push({id:crypto.randomUUID(),label:"Voice entrance",
      at:at(10),source_segment_id:null,evidence:"manual"});
    p.markers.push({id:crypto.randomUUID(),at:at(20),label:"Fixed title"});
    const unchanged=structuredClone(p);
    const review=inspectMusicConform(p,targets(p),SHA);
    expect(review.anchors_need_acknowledgement).toBe(true);
    expect(review.affected_cue_count).toBe(1);
    expect(review.affected_marker_count).toBe(1);
    await expect(applyChange(p,command(p))).rejects.toThrow("Acknowledge");
    expect(p).toEqual(unchanged);
    const next=await applyChange(p,command(p,true));
    expect(next.audio.cues).toEqual(p.audio.cues);
    expect(next.markers).toEqual(p.markers);
  });
  it("does not strand an authored beat when shortening multiple scenes",()=>{
    const p=original();
    p.scenes[0].beats.push({id:crypto.randomUUID(),label:"Late picture cut",
      objective:"Keep the meaning",start:at(5),duration:at(1)});
    const shortening:MusicConformTarget[]=[
      {scene_id:p.scenes[0].id,beat_index:1},
      {scene_id:p.scenes[1].id,beat_index:3},
    ];
    expect(()=>inspectMusicConform(p,shortening,SHA)).toThrow("scene beat");
    p.scenes[0].beats.pop();
    expect(inspectMusicConform(p,shortening,SHA).scenes[0].new_duration)
      .toEqual(at(5));
  });
  it("rejects one or more than sixteen selection targets",()=>{
    const p=original();
    expect(()=>inspectMusicConform(p,targets(p).slice(0,1),SHA)).toThrow("2–16");
    expect(()=>inspectMusicConform(p,Array(17).fill(targets(p)[0]),SHA)).toThrow("2–16");
  });
  it("keeps original concept approval invalid when project timings change",()=>{
    const p=original();
    p.production_design!.plan!.approval={
      reviewer:"Operator",note:"Approved original cut",
      content_sha256:"c".repeat(64),
    };
    const preview=inspectMusicConform(p,targets(p),SHA);
    expect(preview.approval_invalidated).toBe(true);
    applyMusicConformToProject(p,preview);
    expect(p.production_design!.plan!.approval).toBeNull();
  });
  it("cannot apply a preview to another revision or changed scene order",()=>{
    const p=original();
    const review=inspectMusicConform(p,targets(p),SHA);
    p.revision++;
    expect(()=>applyMusicConformToProject(p,review)).toThrow("source project changed");
    p.revision--;
    p.scenes.reverse();
    const reversed=structuredClone(p);
    expect(()=>applyMusicConformToProject(p,review)).toThrow("order");
    expect(p).toEqual(reversed);
  });
});


function attachedSfx(project:Project,sceneIndex:number,offset_frames:number,source_frames:number):SemanticSfxCue {
  const cue:SemanticSfxCue={
    schema:"motionwright.semantic-sfx/1",
    id:crypto.randomUUID(),scene_id:project.scenes[sceneIndex].id,
    event:"transition",anchor:{kind:"scene_start"},
    offset_frames,source_asset_id:ASSET,source_sha256:SHA,
    source_frames,gain_milli_db:-18000,attack_frames:0,release_frames:10,
    original_source_attestation:"Author-owned original test source",
  };
  project.audio.sfx_cues??=[];
  project.audio.sfx_cues.push(cue);
  validateSfxProject(project);
  return cue;
}
describe("MW05-E08 music cuts preserve semantic SFX tails",()=>{
  it("rejects shortened batch and single cuts at preview, without touching the project",async()=>{
    const p=original();
    attachedSfx(p,0,30,180); // starts 1s, 6s source, original scene is 7s
    const unchanged=structuredClone(p);
    const shortening:MusicConformTarget[]=[
      {scene_id:p.scenes[0].id,beat_index:1}, // 5s (would sever the 7s SFX tail)
      {scene_id:p.scenes[1].id,beat_index:3},
    ];
    expect(()=>inspectMusicConform(p,shortening,SHA)).toThrow(/semantic SFX tail/);
    expect(()=>inspectMusicCut(p,p.scenes[0].id,1,SHA)).toThrow(/semantic SFX tail/);
    await expect(applyChange(p,{...command(p,true),targets:shortening}))
      .rejects.toThrow(/semantic SFX tail/);
    expect(p).toEqual(unchanged);
  });
  it("retimes scene-linked SFX with the scene, discloses impact and requires acknowledgement",async()=>{
    const p=original();
    const cue=attachedSfx(p,2,0,90);
    const old=validateSfxProject(p).find(item=>item.cue_id===cue.id)!;
    const review=inspectMusicConform(p,targets(p),SHA);
    expect(review.affected_sfx_cue_count).toBe(1);
    expect(review.anchors_need_acknowledgement).toBe(true);
    await expect(applyChange(p,command(p))).rejects.toThrow(/Acknowledge/);
    const after=await applyChange(p,command(p,true));
    const next=validateSfxProject(after).find(item=>item.cue_id===cue.id)!;
    expect(next.absolute_start_frame).toBe(18*30);
    expect(next.absolute_start_frame).not.toBe(old.absolute_start_frame);
    expect(after.audio.sfx_cues).toEqual(p.audio.sfx_cues);
    expect(after.scenes[2].start).toEqual(at(18));
  });
  it("rejects a stale local conform review before mutating the timeline",()=>{
    const p=original();
    const shortening:MusicConformTarget[]=[
      {scene_id:p.scenes[0].id,beat_index:1},
      {scene_id:p.scenes[1].id,beat_index:3},
    ];
    const review=inspectMusicConform(p,shortening,SHA);
    const old=structuredClone(p);
    // Simulate a newly authored SFX after a preview without revision update:
    // a local adversarial contract case; the real CAS requires a new revision.
    attachedSfx(p,0,30,180);
    const withCue=structuredClone(p);
    expect(()=>applyMusicConformToProject(p,review)).toThrow(/SFX source tail/);
    expect(p).toEqual(withCue);
    expect(old.scenes[0].duration).toEqual(p.scenes[0].duration);
  });
  it("single cut discloses downstream semantic SFX without shifting its source bytes",async()=>{
    const p=original();
    const cue=attachedSfx(p,2,0,30);
    const originalCues=structuredClone(p.audio.sfx_cues);
    const review=inspectMusicCut(p,p.scenes[0].id,2,SHA);
    expect(review.affected_sfx_cue_count).toBe(1);
    expect(review.anchors_need_acknowledgement).toBe(true);
    expect(p.audio.sfx_cues).toEqual(originalCues);
    expect(validateSfxProject(p).find(item=>item.cue_id===cue.id)?.absolute_start_frame)
      .toBe(16*30);
    const next=await applyChange(p,{
      type:"snap_scene_cut_to_music",
      scene_id:p.scenes[0].id,beat_index:2,source_sha256:SHA,
      base_revision:p.revision,acknowledge_absolute_anchors:true,
    });
    expect(next.scenes[1].status).toBe("draft");
    expect(next.scenes[2].status).toBe("draft");
    expect(next.audio.sfx_cues).toEqual(originalCues);
  });
});
