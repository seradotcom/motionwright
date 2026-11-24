import { describe,expect,it } from "vitest";
import { defaultTimelineView,packTimelineRanges,readTimelineView,snapTimelineTime,
  timelineExtraTracks,timelineTicks,timelineWindow,zoomTimeline } from "./timelineNavigation";
import { fixtureProject } from "./fixture";
import { resolveTimelineSelection } from "./timelineSelection";
import type { TimelineRangeEntry } from "./timelineNavigation";

const rate={num:"30000",den:"1001"};
describe("shared Timeline viewport and saved-source lanes",()=>{
  it("anchors zoom, clamps pan and bounds recovered preferences without touching source",()=>{
    const view={...defaultTimelineView(),start:10,zoom:2};
    const zoomed=zoomTimeline(view,60,4,20);
    expect(timelineWindow(60,zoomed)).toEqual({start:15,end:30,span:15});
    expect(timelineWindow(60,{...view,start:999})).toEqual({start:30,end:60,span:30});
    expect(readTimelineView(JSON.stringify({...zoomed,grant:"not restored"}))).toEqual(zoomed);
    expect(()=>readTimelineView('{"zoom":100000,"start":0,"snap":"off","expanded":false}')).toThrow();
  });
  it("snaps to an exact NTSC frame and retains original cue fractions instead of decimal reconstruction",()=>{
    const project=structuredClone(fixtureProject);
    project.audio.cues=[{id:"cue",label:"Exact source cue",at:{num:"10000000000000001",den:"3000000000000000"},source_segment_id:null,evidence:"manual"}];
    expect(snapTimelineTime(project,100.10001,"frames",rate,0)).toEqual({num:"1001",den:"10"});
    expect(snapTimelineTime(project,3.334,"cues",rate,.01)).toEqual(project.audio.cues[0].at);
    expect(snapTimelineTime(project,3.334,"off",rate,.01)).not.toEqual(project.audio.cues[0].at);
  });
  it("bounds ruler density while every displayed tick remains on the chosen frame grid",()=>{
    const ticks=timelineTicks(100.05,1.25,1200,rate);
    expect(ticks.length).toBeGreaterThan(2);expect(ticks.length).toBeLessThan(128);
    for(const time of ticks){expect(time).toBeGreaterThanOrEqual(100.05);expect(time).toBeLessThanOrEqual(101.3);
      expect(Math.abs(time*30000/1001-Math.round(time*30000/1001))).toBeLessThan(1e-8);}
  });
  it("projects animation, camera, music intent and historical review intervals from saved identities without rewriting them",()=>{
    const project=structuredClone(fixtureProject), scene=project.scenes[0], profile=project.deliverables[0], node=scene.nodes[0];
    node.keyframes=[{at:{num:"0",den:"1"},property:"x",value:node.x-40,interpolation:"linear"},
      {at:{num:"3",den:"2"},property:"x",value:node.x,interpolation:"hold"}];
    project.production_design ??= {plan:null,heroes:[],capsules:[],patches:[]};
    const music={id:"40000000-0000-4000-8000-000000000010",name:"Original music",media_type:"audio/wav",content_sha256:"a".repeat(64),source_revision:null};
    project.assets.push(music);
    project.production_design.delivery_recipes={[profile.id]:{schema:"motionwright.delivery-recipe/1",scene_intents:[],original_mix:{
      music_asset_id:music.id,sfx_asset_id:null,sfx_gain_db:-8,duck_attenuation_db:14,duck_attack_samples:2400,duck_release_samples:4800}}};
    project.production_design.camera_reframes=[{id:"40000000-0000-4000-8000-000000000011",scene_id:scene.id,component_version:1,
      start_center_x:960,start_center_y:540,end_center_x:978,end_center_y:548,duration_frames:90}];
    project.reviews=[{id:"40000000-0000-4000-8000-000000000012",kind:"creative",anchor:{
      resource:"scene:"+scene.id,branch_id:project.active_branch,revision:project.revision-1,start:{num:"1",den:"1"},end:{num:"2",den:"1"},locale:null,profile_id:profile.id},
      body:"Review the entrance",status:"open",resolution:null,created_at:project.updated_at,resolved_at:null}];
    const frozen=structuredClone(project), tracks=timelineExtraTracks(project,profile.id,scene.id,profile.frame_rate);
    expect(tracks.object[0]).toMatchObject({key:node.id,sceneId:scene.id,end:{num:"3",den:"2"}});
    expect(tracks.camera[0].end).toEqual({num:"3",den:"1"});
    expect(tracks.music[0].label).toBe("Music intent · Original music");
    expect(tracks.review[0].historical).toBe(true);
    for(const entry of [...tracks.object,...tracks.camera,...tracks.music,...tracks.review])
      expect(resolveTimelineSelection(project,entry.selection)).not.toBeNull();
    expect(project).toEqual(frozen);
  });
  it("places overlapping source ranges on separate visual rows and reuses a row at a half-open join",()=>{
    const entries=[["a",0,2],["b",1,3],["c",2,3]].map(([id,start,end])=>({
      key:id,selection:{kind:"object",id},label:id,start:{num:String(start),den:"1"},end:{num:String(end),den:"1"},
    })) as TimelineRangeEntry[];
    const packed=packTimelineRanges(entries);
    expect(packed.entries.map(entry=>entry.row)).toEqual([0,1,0]);
    expect(packed.height).toBe(54);
  });
});
