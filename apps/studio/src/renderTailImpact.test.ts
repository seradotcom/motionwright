import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { rationalSeconds } from "./types";
import { inspectRenderTailImpact } from "./renderTailImpact";
import type { RenderImpactCause, RenderTailInspectRequest } from "./renderTailImpact";
import type { Project } from "./types";

function source(): Project {
  const p=structuredClone(fixtureProject);
  const first=structuredClone(p.scenes[0]);
  p.scenes=[0,1,2].map(i=>({
    ...structuredClone(first),
    id:crypto.randomUUID(),name:["Open","Explain","Close"][i],
    objective:"Original source scene",nodes:[],beats:[],
    status:"draft" as const,
    start:rationalSeconds(2*i),duration:rationalSeconds(2),
  }));
  p.deliverables=p.deliverables.slice(0,2);
  for(const profile of p.deliverables){
    profile.frame_rate=rationalSeconds(30);
    profile.included_scene_ids=[];
  }
  return p;
}
function query(p:Project,cause:RenderImpactCause,changed:string[]):RenderTailInspectRequest {
  return {
    expected_generation:p.generation,expected_revision:p.revision,
    changed_scene_ids:changed,cause,tail_frames:0,tail_source_asset_id:null,
  };
}
describe("bounded source-timeline impact",()=>{
  it("affects only one scene without claiming any native cache savings",()=>{
    const p=source();
    const report=inspectRenderTailImpact(p,query(p,"scene_visual",[p.scenes[1].id]));
    expect(report.schema).toBe("motionwright.render-tail-impact/1");
    expect(report.profiles[0].source_timeline_windows).toEqual([{start:60,end:120}]);
    expect(report.profiles[0].affected_scene_ids).toEqual([p.scenes[1].id]);
    expect(report.actually_invalidated_caches).toBe(false);
    expect(report.native_frames_regenerated_or_avoided).toBeNull();
    expect(report.native_renderer_saved_wallclock_micros).toBeNull();
    expect(report.profiles[0].output_frame_projection_verified).toBe(false);
  });
  it("explicit source-bound sound tails can spill into a neighboring scene",()=>{
    const p=source(),audioId=crypto.randomUUID();
    p.assets.push({id:audioId,name:"Original WAV",media_type:"audio/wav",
      content_sha256:"a".repeat(64),source_revision:"source-declared"});
    const request={...query(p,"scene_audio_tail",[p.scenes[0].id]),
      tail_frames:15,tail_source_asset_id:audioId};
    const report=inspectRenderTailImpact(p,request);
    expect(report.profiles[0].source_timeline_windows).toEqual([{start:0,end:75}]);
    expect(report.profiles[0].affected_scene_ids).toEqual([p.scenes[0].id,p.scenes[1].id]);
    p.deliverables[0].included_scene_ids=[p.scenes[1].id];
    const scoped=inspectRenderTailImpact(p,request);
    expect(scoped.profiles[0].source_timeline_windows).toEqual([{start:60,end:75}]);
    expect(scoped.profiles[0].is_scoped_selection).toBe(true);
    expect(scoped.profiles[0].output_frame_projection_verified).toBe(false);
  });
  it("retiming conservatively affects downstream selected scenes",()=>{
    const p=source(),last=p.scenes[2].id;
    p.deliverables[0].included_scene_ids=[last];
    const report=inspectRenderTailImpact(p,query(p,"time_ripple",[p.scenes[1].id]));
    expect(report.profiles[0].source_timeline_windows).toEqual([{start:120,end:180}]);
    expect(report.profiles[0].affected_scene_ids).toEqual([last]);
  });
  it("global mix changes cannot falsely exclude unselected source intervals",()=>{
    const p=source();
    const report=inspectRenderTailImpact(p,query(p,"global_mix",[]));
    expect(report.profiles[0].source_timeline_windows).toEqual([{start:0,end:180}]);
    expect(report.profiles[0].affected_scene_ids).toEqual(p.scenes.map(s=>s.id));
  });
  it("uses exact rational 24 and 30000/1001 frame rates",()=>{
    const p=source();
    p.deliverables[0].frame_rate={num:"24",den:"1"};
    p.deliverables[1].frame_rate={num:"30000",den:"1001"};
    const report=inspectRenderTailImpact(p,query(p,"scene_visual",[p.scenes[0].id]));
    expect(report.profiles[0].source_timeline_windows).toEqual([{start:0,end:48}]);
    expect(report.profiles[1].source_timeline_windows).toEqual([{start:0,end:60}]);
  });
  it("rejects stale generations, duplicate scenes, unknown audio sources and false tail claims",()=>{
    const p=source(),original=structuredClone(p);
    const base=query(p,"scene_visual",[p.scenes[0].id]);
    for(const request of [
      {...base,expected_revision:p.revision+1},
      {...base,expected_generation:crypto.randomUUID()},
      {...base,changed_scene_ids:[p.scenes[0].id,p.scenes[0].id]},
      {...base,changed_scene_ids:[crypto.randomUUID()]},
      {...base,cause:"scene_audio_tail" as const,tail_frames:15},
      {...base,cause:"scene_audio_tail" as const,tail_frames:15,tail_source_asset_id:crypto.randomUUID()},
      {...base,tail_frames:15},
      {...base,cause:"global_mix" as const},
    ]){
      expect(()=>inspectRenderTailImpact(p,request)).toThrow(/preflight/);
    }
    expect(p).toEqual(original);
  });
  it("flags audio tails beyond the film rather than promising saved frames",()=>{
    const p=source(),asset=crypto.randomUUID();
    p.assets.push({id:asset,name:"Original WAV",media_type:"audio/wav",
      content_sha256:"b".repeat(64),source_revision:null});
    const report=inspectRenderTailImpact(p,{
      ...query(p,"scene_audio_tail",[p.scenes[2].id]),tail_frames:30,
      tail_source_asset_id:asset,
    });
    expect(report.profiles[0].exceeds_source_duration).toBe(true);
    expect(report.profiles[0].source_timeline_windows).toEqual([{start:120,end:180}]);
    expect(report.actually_invalidated_caches).toBe(false);
  });
  it("caps pathological rates/timelines without allocating giant intervals",()=>{
    const p=source();
    p.deliverables[0].frame_rate={num:"241",den:"1"};
    expect(()=>inspectRenderTailImpact(p,query(p,"global_mix",[]))).toThrow(/preflight/);
    p.deliverables[0].frame_rate={num:"30",den:"1"};
    p.scenes[2].start={num:"999999999999999",den:"1"};
    expect(()=>inspectRenderTailImpact(p,query(p,"scene_visual",[p.scenes[2].id]))).toThrow(/budget/);
  });
});
