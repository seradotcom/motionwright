/** Follow projects the target's exact translation without persisting derived
 * keys. Each follower keeps its saved terminal offset, text and opacity. */
import type { CanvasKeyframe, CanvasNode, MotionInterpolation, RationalTime } from "./types";
import { resolveCanvasAlignments, type AlignmentScale, type AlignmentResult } from "./canvasAlignment";
import { exactCanvasOpacitySpans } from "./canvasOpacity";

/** Continuous means the source scene clock; editorial beats never reset it. */
export interface FollowClock { duration: RationalTime; fps?: RationalTime; continuous: boolean; }
export interface RelationProjection extends AlignmentResult { tracks: Map<string, CanvasKeyframe[]>; }
type TranslationPoint = {x:number;y:number;at:RationalTime;curve:MotionInterpolation};
type Translation = TranslationPoint[];
const ZERO: RationalTime = {num:"0",den:"1"};
const GEOMETRY = ["x","y","width","height","rotation_deg"] as const;

function rational(value:RationalTime): [bigint,bigint] {
  if (!/^(0|[1-9][0-9]{0,18})$/.test(value.num) || !/^[1-9][0-9]{0,18}$/.test(value.den))
    throw new Error("Invalid rational Follow clock.");
  const n=BigInt(value.num),d=BigInt(value.den),limit=9223372036854775807n;
  let a=n,b=d;
  while(b) [a,b]=[b,a%b];
  if(n>limit || d>limit || a!==1n) throw new Error("Invalid rational Follow clock.");
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
    node.width<=16384 && node.height<=16384 && [node.x,node.y,node.width,node.height].every(Number.isFinite);
}
function opacity(node:CanvasNode,clock?:FollowClock):void {
  if(!node.keyframes.some(key=>key.property==="opacity")) return;
  if(!clock?.fps) throw new Error("Follow opacity needs the saved scene duration and output frame rate.");
  exactCanvasOpacitySpans(node,clock,clock.fps);
}
function rootTranslation(node:CanvasNode,clock?:FollowClock):Translation {
  const fail=()=>new Error("The root needs 2 to 6 exact translation poses with common linear, ease-out-cubic or Hold curves and unchanged size and orientation.");
  if(node.relations.length || clock?.continuous===false) throw fail();
  opacity(node,clock);
  const slots=new Map<string,CanvasKeyframe[]>();
  for(const key of node.keyframes.filter(key=>key.property!=="opacity")){
    rational(key.at);
    const id=JSON.stringify([key.at.num,key.at.den]);
    slots.set(id,[...(slots.get(id)??[]),key]);
  }
  const ordered=[...slots.values()].sort((a,b)=>compare(a[0].at,b[0].at));
  if(ordered.length<2 || ordered.length>6) throw fail();
  const points:Translation=[];
  let grammar:string|null=null;
  for(const keys of ordered){
    const at=keys[0].at,curve=keys[0].interpolation;
    const properties=keys.map(key=>key.property).sort().join("|");
    const full=[...GEOMETRY].sort().join("|");
    if((properties!=="x|y" && properties!==full) || (ordered.length>2 && properties!==full) ||
      (grammar!==null && grammar!==properties) || !["linear","ease_out_cubic","hold"].includes(curve) ||
      keys.some(key=>key.interpolation!==curve || !Number.isFinite(key.value)) ||
      (points.length===0 && (compare(at,ZERO)!==0 || curve!=="linear")) ||
      (clock && compare(at,clock.duration)>=0)) throw fail();
    grammar=properties;
    for(const key of keys) if(!["x","y"].includes(key.property) && key.value!==node[key.property]) throw fail();
    if(clock?.fps){
      const [n,d]=rational(at),[fn,fd]=rational(clock.fps),num=n*fn,den=d*fd;
      if(fn<=0n || num%den!==0n || num/den>36000n) throw fail();
      if(curve==="hold"){
        const [sn,sd]=rational(clock.duration);
        if((n*fn+d*fd)*sd>sn*d*fn) throw fail();
      }
    }
    points.push({at:{...at},curve,x:keys.find(key=>key.property==="x")!.value,y:keys.find(key=>key.property==="y")!.value});
  }
  const end=points.at(-1)!;
  if(end.x!==node.x || end.y!==node.y || !points.some(point=>point.x!==node.x || point.y!==node.y)) throw fail();
  return points;
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
      if(node.relations.length!==1 || node.keyframes.some(key=>key.property!=="opacity") || !admitted(node,parents) ||
        positionLocked || node.property_locks.includes("position"))
        throw new Error("Each follower needs one Follow, an unlocked independent pose and no authored geometry keys.");
      opacity(node,clock);
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
      const translation=inherited.map(point=>({...point,x:node.x+(point.x-target.x),y:node.y+(point.y-target.y)}));
      if(translation.some(point=>![point.x,point.y].every(Number.isFinite)) ||
        !translation.some(point=>point.x!==node.x || point.y!==node.y))
        throw new Error("Derived translation is not representable at the follower's saved coordinates.");
      // Keep the established two-key X/Y projection byte-for-byte. Complete
      // waypoint poses carry constant local dimensions for the native grammar.
      const properties=translation.length>2 ? GEOMETRY : ["x","y"] as const;
      const keys:CanvasKeyframe[]=translation.flatMap(point=>properties.map(property=>({
        at:{...point.at},property,value:property==="x"?point.x:property==="y"?point.y:node[property],
        interpolation:point.curve,
      })));
      keys.push(...node.keyframes.filter(key=>key.property==="opacity").map(key=>({...key,at:{...key.at}})));
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
