/** A Follow relation inherits only a target's exact paired translation.
 * The terminal offset comes from the original saved poses; derived keys
 * never enter the editable Project or its change journal. */
import type { CanvasKeyframe, CanvasNode, MotionInterpolation, RationalTime } from "./types";
import { resolveCanvasAlignments, type AlignmentScale, type AlignmentResult } from "./canvasAlignment";

export interface FollowClock { duration: RationalTime; fps?: RationalTime; continuous: boolean; }
export interface RelationProjection extends AlignmentResult { tracks: Map<string, CanvasKeyframe[]>; }
type Translation = {startX:number;startY:number;end:RationalTime;curve:MotionInterpolation};
const ZERO: RationalTime = {num:"0",den:"1"};

function rational(value:RationalTime): [bigint,bigint] {
  if (!/^-?\d{1,19}$/.test(value.num) || !/^\d{1,19}$/.test(value.den)) throw new Error("Invalid rational Follow clock.");
  const n=BigInt(value.num),d=BigInt(value.den);
  if (d<=0n) throw new Error("Invalid rational Follow clock.");
  return [n,d];
}
function compare(a:RationalTime,b:RationalTime):number {
  const [an,ad]=rational(a),[bn,bd]=rational(b);
  const delta=an*bd-bn*ad;
  return delta<0n?-1:delta>0n?1:0;
}
function admitted(node:CanvasNode,parents:Set<string>):boolean {
  return ["shape","rectangle","circle","text"].includes(node.kind) && node.parent_id===null &&
    !parents.has(node.id) && node.coordinate_space==="project_pixels" && node.rotation_deg===0 &&
    node.opacity===1 && node.style.blend_mode==="normal" && node.width>0 && node.height>0 &&
    [node.x,node.y,node.width,node.height].every(Number.isFinite);
}
function rootTranslation(node:CanvasNode,clock?:FollowClock):Translation {
  const fail=()=>new Error("The root target needs one exact paired linear or ease-out-cubic X/Y translation.");
  if(node.relations.length || node.keyframes.length!==4 || clock?.continuous===false) throw fail();
  const x=node.keyframes.filter(key=>key.property==="x").sort((a,b)=>compare(a.at,b.at));
  const y=node.keyframes.filter(key=>key.property==="y").sort((a,b)=>compare(a.at,b.at));
  if(x.length!==2 || y.length!==2) throw fail();
  const end=x[1].at,curve=x[1].interpolation;
  if(!node.keyframes.every(key=>Number.isFinite(key.value)) ||
      compare(x[0].at,ZERO)!==0 || compare(y[0].at,ZERO)!==0 ||
      compare(end,ZERO)<=0 || compare(y[1].at,end)!==0 ||
      (clock && compare(end,clock.duration)>=0) ||
      x[0].interpolation!=="linear" || y[0].interpolation!=="linear" ||
      y[1].interpolation!==curve || !["linear","ease_out_cubic"].includes(curve) ||
      x[1].value!==node.x || y[1].value!==node.y ||
      (x[0].value===node.x && y[0].value===node.y)) throw fail();
  if(clock?.fps){
    const [n,d]=rational(end),[fn,fd]=rational(clock.fps),num=n*fn,den=d*fd;
    if(num<=0n||den<=0n||num%den!==0n||num/den>36000n) throw fail();
  }
  return {startX:x[0].value,startY:y[0].value,end,curve};
}
function followTracks(source:readonly CanvasNode[],positionLocked:boolean,clock?:FollowClock):Map<string,CanvasKeyframe[]> {
  const tracks=new Map<string,CanvasKeyframe[]>();
  if(!source.some(node=>node.relations.some(r=>r.kind==="follow"))) return tracks;
  if(source.length>512 || clock?.continuous===false) throw new Error("Follow requires a bounded continuous scene.");
  const all=new Map(source.map(node=>[node.id,node]));
  if(all.size!==source.length) throw new Error("Duplicate object identity.");
  const parents=new Set(source.flatMap(node=>node.parent_id?[node.parent_id]:[]));
  const participants=new Set<string>(),others=new Set<string>(),remaining=new Map<string,string>();
  for(const node of source){
    if(node.relations.some(r=>r.kind==="follow")){
      if(node.relations.length!==1 || node.keyframes.length || !admitted(node,parents) ||
        positionLocked || node.property_locks.includes("position"))
        throw new Error("Each follower needs one Follow, an unlocked independent pose and no authored keys.");
      const target=node.relations[0].target_id,targetNode=all.get(target);
      if(target===node.id || !targetNode || !admitted(targetNode,parents))
        throw new Error("Follow target must be another independent planar drawable.");
      participants.add(node.id);participants.add(target);remaining.set(node.id,target);
    }else for(const r of node.relations){others.add(node.id);others.add(r.target_id);}
  }
  if([...participants].some(id=>others.has(id))) throw new Error("Follow and other relations share a participant.");
  const translations=new Map<string,Translation>();
  for(const id of participants) if(!remaining.has(id)) translations.set(id,rootTranslation(all.get(id)!,clock));
  while(remaining.size){
    const ready=[...remaining].filter(([,target])=>translations.has(target));
    if(!ready.length) throw new Error("Cyclic Follow dependencies or no translating root.");
    for(const [id,targetId] of ready){
      const node=all.get(id)!,target=all.get(targetId)!,inherited=translations.get(targetId)!;
      const translation={...inherited,startX:node.x+(inherited.startX-target.x),startY:node.y+(inherited.startY-target.y)};
      if(![translation.startX,translation.startY].every(Number.isFinite) ||
          (translation.startX===node.x && translation.startY===node.y))
        throw new Error("Derived translation is not representable at the follower's saved coordinates.");
      const keys:CanvasKeyframe[]=[
        {at:{...ZERO},property:"x",value:translation.startX,interpolation:"linear"},
        {at:{...ZERO},property:"y",value:translation.startY,interpolation:"linear"},
        {at:{...translation.end},property:"x",value:node.x,interpolation:translation.curve},
        {at:{...translation.end},property:"y",value:node.y,interpolation:translation.curve},
      ];
      translations.set(id,translation);tracks.set(id,keys);remaining.delete(id);
    }
  }
  return tracks;
}

/** Disjoint static alignment and Follow graphs share one all-or-nothing
 * projection. A conflict is surfaced rather than partially rendering it. */
export function resolveCanvasRelations(source:readonly CanvasNode[],scale?:AlignmentScale,
  positionLocked=false,clock?:FollowClock):RelationProjection {
  const participants=new Set<string>();
  for(const node of source)for(const relation of node.relations){participants.add(node.id);participants.add(relation.target_id);}
  try{
    const tracks=followTracks(source,positionLocked,clock);
    const staticSource=source.map(node=>tracks.has(node.id)?{...node,relations:[]}:node);
    const aligned=resolveCanvasAlignments(staticSource,scale,positionLocked);
    if(aligned.issue) throw new Error(aligned.issue);
    return {positions:aligned.positions,tracks,participants,issue:null};
  }catch(reason){
    return {positions:new Map(),tracks:new Map(),participants,issue:reason instanceof Error?reason.message:String(reason)};
  }
}
