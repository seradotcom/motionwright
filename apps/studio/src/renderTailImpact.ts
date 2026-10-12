/** Exact-rational source-timeline invalidation proposal; no renderer effects.
 * The Rust domain is the canonical cross-process source of truth.
 */
import type { Project, RationalTime } from "./types";

export type RenderImpactCause="scene_visual"|"scene_audio_tail"|"time_ripple"|"global_mix";
export interface RenderTailInspectRequest {
  expected_generation:string;
  expected_revision:number;
  changed_scene_ids:string[];
  cause:RenderImpactCause;
  tail_frames:number;
  tail_source_asset_id:string|null;
}
export interface SourceFrameWindow {start:number;end:number}
export interface ProfileRenderTailImpact {
  deliverable_id:string;
  frame_rate:RationalTime;
  is_scoped_selection:boolean;
  source_timeline_windows:SourceFrameWindow[];
  affected_scene_ids:string[];
  exceeds_source_duration:boolean;
  output_frame_projection_verified:boolean;
}
export interface RenderTailImpactReport {
  schema:"motionwright.render-tail-impact/1";
  project_id:string;
  project_generation:string;
  project_revision:number;
  cause:RenderImpactCause;
  requested_scene_ids:string[];
  declared_audio_tail_frames:number;
  profiles:ProfileRenderTailImpact[];
  actually_invalidated_caches:false;
  native_frames_regenerated_or_avoided:null;
  native_renderer_saved_wallclock_micros:null;
  technical_acceptance:"DEPENDENCY_PREFLIGHT_ONLY_NATIVE_COMPARISON_NOT_RUN";
}
const LIMIT=1_080_000;
const DIGEST=/^[0-9a-f]{64}$/;
function invalid(detail:string):never{throw new Error("Render impact preflight: "+detail);}
function rat(value:RationalTime):[bigint,bigint] {
  const n=String(value.num),d=String(value.den);
  if(!/^-?[0-9]+$/.test(n)||!/^[1-9][0-9]*$/.test(d))
    invalid("invalid rational value");
  const numerator=BigInt(n),denominator=BigInt(d);
  if(denominator===0n)invalid("zero rational denominator");
  return [numerator,denominator];
}
function fpsOf(frameRate:RationalTime):[bigint,bigint]{
  const [n,d]=rat(frameRate);
  if(n<=0n || n>240_000n || d>10_000n || n<d || n>d*120n)
    invalid("frame rate outside 1..120fps range");
  return [n,d];
}
function frameAt(time:RationalTime,fps:[bigint,bigint],ceil:boolean):number{
  const [n,d]=rat(time);
  if(n<0n)invalid("negative source timestamp");
  const top=n*fps[0],bottom=d*fps[1];
  const frame=ceil?(top+bottom-1n)/bottom:top/bottom;
  if(frame>BigInt(LIMIT))invalid("source frame budget exceeded");
  return Number(frame);
}
function frameEnd(start:RationalTime,duration:RationalTime,fps:[bigint,bigint]):number {
  const [a,b]=rat(start),[c,d]=rat(duration);
  if(a<0n || c<=0n)invalid("invalid scene duration or start");
  const numerator=(a*d+c*b)*fps[0],denominator=b*d*fps[1];
  const end=(numerator+denominator-1n)/denominator;
  if(end>BigInt(LIMIT))invalid("source frame budget exceeded");
  return Number(end);
}
function union(windows:SourceFrameWindow[]):SourceFrameWindow[] {
  const sorted=[...windows].sort((a,b)=>a.start-b.start||a.end-b.end);
  const answer:SourceFrameWindow[]=[];
  for(const item of sorted){
    const previous=answer.at(-1);
    if(previous && item.start<=previous.end){
      previous.end=Math.max(previous.end,item.end);
    }else answer.push({...item});
  }
  return answer;
}
export function inspectRenderTailImpact(
  project:Project,request:RenderTailInspectRequest
):RenderTailImpactReport {
  if(request.expected_generation!==project.generation ||
    request.expected_revision!==project.revision)
    invalid("expected project generation/revision is stale");
  if(!project.scenes.length || project.scenes.length>256 ||
     !project.deliverables.length || project.deliverables.length>64)
    invalid("source scene/output budget invalid");
  const changed=new Set(request.changed_scene_ids);
  if(changed.size!==request.changed_scene_ids.length ||
    [...changed].some(id=>!project.scenes.some(scene=>scene.id===id)))
    invalid("unknown or repeated changed-scene identity");
  if((request.cause==="global_mix") !== (request.changed_scene_ids.length===0) ||
    changed.size>64 || !["scene_visual","scene_audio_tail","time_ripple","global_mix"].includes(request.cause))
    invalid("invalid or mixed global/scene change scope");
  if(request.cause==="scene_audio_tail"){
    if(!Number.isInteger(request.tail_frames) || request.tail_frames<1 || request.tail_frames>300)
      invalid("audio tail must be explicitly bounded and nonzero");
    if(!project.assets.some(asset=>asset.id===request.tail_source_asset_id &&
      asset.media_type.startsWith("audio/") && DIGEST.test(asset.content_sha256??"")))
      invalid("tail source must be an identified audio asset");
  }else if(request.tail_frames!==0 || request.tail_source_asset_id!==null){
    invalid("non-audio cause cannot invent tail frames");
  }
  const profiles=project.deliverables.map((profile):ProfileRenderTailImpact=>{
    const fps=fpsOf(profile.frame_rate);
    const scenes=project.scenes.map(scene=>{
      const start=frameAt(scene.start,fps,false);
      const end=frameEnd(scene.start,scene.duration,fps);
      if(end<=start)invalid("scene interval collapses at profile fps");
      return {id:scene.id,range:{start,end}};
    });
    const filmEnd=Math.max(...scenes.map(s=>s.range.end));
    let changedRanges:SourceFrameWindow[];
    switch(request.cause){
      case "global_mix":changedRanges=[{start:0,end:filmEnd}];break;
      case "time_ripple":{
        const start=Math.min(...scenes.filter(s=>changed.has(s.id)).map(s=>s.range.start));
        changedRanges=[{start,end:filmEnd}];break;
      }
      case "scene_visual":
      case "scene_audio_tail":
        changedRanges=scenes.filter(s=>changed.has(s.id)).map(s=>({
          start:s.range.start,end:s.range.end+request.tail_frames,
        }));break;
    }
    const scoped=profile.included_scene_ids.length>0;
    let affected:string[]=[];
    let dirty:SourceFrameWindow[]=[];
    for(const scene of scenes){
      if(scoped&&!profile.included_scene_ids.includes(scene.id))continue;
      const hits=changedRanges.map(range=>({
        start:Math.max(scene.range.start,range.start),
        end:Math.min(scene.range.end,range.end),
      })).filter(range=>range.start<range.end);
      if(hits.length){affected.push(scene.id);dirty.push(...hits);}
    }
    if(request.cause==="global_mix"&&!scoped)dirty=changedRanges;
    return {
      deliverable_id:profile.id,
      frame_rate:structuredClone(profile.frame_rate),
      is_scoped_selection:scoped,
      source_timeline_windows:union(dirty),
      affected_scene_ids:affected,
      exceeds_source_duration:changedRanges.some(range=>range.end>filmEnd),
      output_frame_projection_verified:false,
    };
  });
  return {
    schema:"motionwright.render-tail-impact/1",
    project_id:project.id,
    project_generation:project.generation,
    project_revision:project.revision,
    cause:request.cause,
    requested_scene_ids:[...request.changed_scene_ids],
    declared_audio_tail_frames:request.tail_frames,
    profiles,
    actually_invalidated_caches:false,
    native_frames_regenerated_or_avoided:null,
    native_renderer_saved_wallclock_micros:null,
    technical_acceptance:"DEPENDENCY_PREFLIGHT_ONLY_NATIVE_COMPARISON_NOT_RUN",
  };
}
