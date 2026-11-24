import { formatBeatTime, inspectBeatCoverage, inspectBeatTiming, parseBeatTime } from "./sceneBeatTiming";
import { narrativeEditingSource, preserveEditorialMinimumHolds, sameEditorialValue } from "./narrativeEditing";
import type { NarrativeEditSource } from "./narrativeEditing";
import type { Beat, Project, ProjectEvent, RationalTime, Scene } from "./types";

export type SceneBeatTimelineChange = {
  type: "edit_scene_beat_timeline"; expected: NarrativeEditSource; scene_id: string;
  expected_duration: RationalTime; expected_beats: Beat[]; beats: Beat[]; rationale: string;
};
export interface BeatTimelineUndo {
  eventRevision: number; change: SceneBeatTimelineChange;
}
export interface BeatTimelineRaw {
  operation: "split" | "trim" | "move" | "ripple" | "reorder" | "undo";
  beatId: string; start: string; end: string; duration: string; position: string;
  splitId: string; rationale: string; undo: BeatTimelineUndo | null;
}
export interface BeatTimelineInput {
  kind: "beat_timeline"; config: BeatTimelineRaw; source: { duration: RationalTime; beats: Beat[] };
}
const clone = <T,>(value:T):T => structuredClone(value);
const record = (value:unknown):value is Record<string,unknown> => !!value && typeof value==="object" && !Array.isArray(value);
const exact = (value:Record<string,unknown>,keys:string[]) => Object.keys(value).length===keys.length && keys.every(key=>Object.hasOwn(value,key));
const uuid = (value:unknown):value is string => typeof value==="string" && /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const bytes = (value:string) => new TextEncoder().encode(value).length;
const equal = sameEditorialValue;
const zero = ():RationalTime => ({num:"0",den:"1"});
export function compareBeatTimelineTime(a:RationalTime,b:RationalTime):number {
  formatBeatTime(a);formatBeatTime(b);
  const value=BigInt(a.num)*BigInt(b.den)-BigInt(b.num)*BigInt(a.den);
  return value<0n?-1:value>0n?1:0;
}
function arithmetic(a:RationalTime,b:RationalTime,subtract=false):RationalTime {
  formatBeatTime(a);formatBeatTime(b);
  return parseBeatTime((BigInt(a.num)*BigInt(b.den)+(subtract?-1n:1n)*BigInt(b.num)*BigInt(a.den))+"/"+(BigInt(a.den)*BigInt(b.den)));
}
export const beatTimelineEnd = (beat:Beat) => arithmetic(beat.start,beat.duration);
function timeShape(value:unknown):value is RationalTime {
  if(!record(value)||!exact(value,["num","den"])||typeof value.num!=="string"||typeof value.den!=="string")return false;
  try{formatBeatTime(value as unknown as RationalTime);return true;}catch{return false;}
}
function sourceShape(value:unknown):value is NarrativeEditSource {
  return record(value)&&exact(value,["project_id","generation","branch_id","base_revision"])
    &&[value.project_id,value.generation,value.branch_id].every(uuid)
    &&typeof value.base_revision==="number"&&Number.isSafeInteger(value.base_revision)&&value.base_revision>=0;
}
export function beatTimelineBeatsShape(value:unknown):value is Beat[] {
  return Array.isArray(value)&&value.length<=32&&value.every(beat=>record(beat)
    &&exact(beat,["id","label","objective","start","duration"])&&uuid(beat.id)
    &&typeof beat.label==="string"&&bytes(beat.label)<=160&&!!beat.label.trim()
    &&typeof beat.objective==="string"&&bytes(beat.objective)<=4000&&timeShape(beat.start)&&timeShape(beat.duration));
}
export function sceneBeatTimelineChangeShape(value:unknown):value is SceneBeatTimelineChange {
  return record(value)&&exact(value,["type","expected","scene_id","expected_duration","expected_beats","beats","rationale"])
    &&value.type==="edit_scene_beat_timeline"&&sourceShape(value.expected)&&uuid(value.scene_id)
    &&timeShape(value.expected_duration)&&beatTimelineBeatsShape(value.expected_beats)&&beatTimelineBeatsShape(value.beats)
    &&typeof value.rationale==="string"&&bytes(value.rationale)<=4000&&!!value.rationale.trim();
}
function undoShape(value:unknown):value is BeatTimelineUndo {
  return record(value)&&exact(value,["eventRevision","change"])&&typeof value.eventRevision==="number"
    &&Number.isSafeInteger(value.eventRevision)&&value.eventRevision>0&&sceneBeatTimelineChangeShape(value.change)
    &&value.eventRevision===value.change.expected.base_revision+1;
}
export function beatTimelineRawShape(value:unknown):value is BeatTimelineRaw {
  return record(value)&&exact(value,["operation","beatId","start","end","duration","position","splitId","rationale","undo"])
    &&["split","trim","move","ripple","reorder","undo"].includes(String(value.operation))
    &&typeof value.beatId==="string"&&(value.beatId===""||uuid(value.beatId))&&uuid(value.splitId)
    &&["start","end","duration","position"].every(key=>typeof value[key]==="string"&&String(value[key]).length<=128)
    &&typeof value.rationale==="string"&&bytes(value.rationale)<=4000&&(value.undo===null||undoShape(value.undo));
}
export function beatTimelineInputShape(value:unknown):value is BeatTimelineInput {
  return record(value)&&exact(value,["kind","config","source"])&&value.kind==="beat_timeline"&&beatTimelineRawShape(value.config)
    &&record(value.source)&&exact(value.source,["duration","beats"])&&timeShape(value.source.duration)&&beatTimelineBeatsShape(value.source.beats);
}
export function savedBeatTimelineInput(scene:Scene,previous?:BeatTimelineInput):BeatTimelineInput {
  const selected=scene.beats.find(beat=>beat.id===previous?.config.beatId)??scene.beats[0];
  const start=selected?.start??zero(),duration=selected?.duration??scene.duration;
  return {kind:"beat_timeline",source:{duration:clone(scene.duration),beats:clone(scene.beats)},
    config:{operation:"split",beatId:selected?.id??"",start:formatBeatTime(start),
      end:formatBeatTime(arithmetic(start,duration)),duration:formatBeatTime(duration),position:"1",
      splitId:crypto.randomUUID(),rationale:"",undo:null}};
}
function unlocked(project:Project,sceneId:string) {
  const lock=project.locks.find(lock=>["project:"+project.id,"scene:"+sceneId].includes(lock.resource)
    &&(lock.kind==="content"||lock.kind==="timing"));
  if(lock)throw new Error("resource is locked: "+lock.resource+" ("+lock.kind+")");
}
/** Browser authoring parity; actual saved mutations use the same Domain Change. */
export function applySceneBeatTimeline(project:Project,change:SceneBeatTimelineChange):void {
  if(!sceneBeatTimelineChangeShape(change))throw new Error("Invalid bounded scene beat timeline change.");
  if(!equal(change.expected,narrativeEditingSource(project)))throw new Error("scene beat timeline source is stale or foreign");
  unlocked(project,change.scene_id);
  const scene=project.scenes.find(scene=>scene.id===change.scene_id);
  if(!scene)throw new Error("resource not found: scene:"+change.scene_id);
  if(!equal(scene.duration,change.expected_duration)||!equal(scene.beats,change.expected_beats))
    throw new Error("scene beat timeline no longer matches its conditional source");
  if(equal(scene.beats,change.beats))throw new Error("scene beat timeline already matches the proposal");
  const ids=new Set<string>();
  for(const beat of change.beats) {
    if(ids.has(beat.id)||project.narrative.beats.some(item=>item.id===beat.id)
      ||project.scenes.some(item=>item.id!==scene.id&&item.beats.some(other=>other.id===beat.id)))
      throw new Error("beat identity already belongs to another project resource");
    ids.add(beat.id);
    const valid=inspectBeatTiming(beat,{start:formatBeatTime(beat.start),duration:formatBeatTime(beat.duration)},scene.duration);
    if(!valid.ok)throw new Error(valid.message);
  }
  const coverage=inspectBeatCoverage(change.beats,scene.duration);
  if(!coverage.ok)throw new Error(coverage.message);
  if(!equal(coverage.beats,change.beats))throw new Error("scene beats must use deterministic timeline order");
  let previousEnd=zero();
  for(const beat of change.beats) {
    if(compareBeatTimelineTime(beat.start,previousEnd)<0)throw new Error("scene beat timeline intervals overlap");
    previousEnd=beatTimelineEnd(beat);
  }
  const updated={...scene,beats:clone(change.beats),status:"draft" as const};
  preserveEditorialMinimumHolds(project,{...project,scenes:project.scenes.map(item=>item.id===scene.id?updated:item)});
  scene.beats=updated.beats;
  scene.status=updated.status;
}
export function sceneBeatTimelineProposal(project:Project,sceneId:string,raw:BeatTimelineRaw):SceneBeatTimelineChange {
  if(!beatTimelineRawShape(raw))throw new Error("Complete the bounded scene beat timing input.");
  const scene=project.scenes.find(scene=>scene.id===sceneId);
  if(!scene)throw new Error("The selected scene is unavailable.");
  let expectedBeats=clone(scene.beats),expectedDuration=clone(scene.duration),beats=clone(scene.beats);
  if(raw.operation==="undo") {
    if(!raw.undo||!undoShape(raw.undo))throw new Error("Read an original saved timeline change before preparing its inverse.");
    const original=raw.undo.change,source=original.expected,current=narrativeEditingSource(project);
    if(source.project_id!==current.project_id||source.generation!==current.generation||source.branch_id!==current.branch_id
      ||original.scene_id!==sceneId||raw.undo.eventRevision>project.revision)
      throw new Error("The saved timeline change belongs to another source or a later revision.");
    expectedBeats=clone(original.beats);expectedDuration=clone(original.expected_duration);beats=clone(original.expected_beats);
  } else {
    const coverage=inspectBeatCoverage(beats,scene.duration);
    if(!coverage.ok)throw new Error(coverage.message);
    const index=beats.findIndex(beat=>beat.id===raw.beatId),beat=beats[index];
    if(!beat)throw new Error("Select an existing saved scene beat.");
    const end=beatTimelineEnd(beat);
    if(raw.operation==="split") {
      const at=parseBeatTime(raw.start);
      if(compareBeatTimelineTime(at,beat.start)<=0||compareBeatTimelineTime(at,end)>=0)
        throw new Error("Split strictly inside the selected half-open beat interval.");
      if(beats.length>=32)throw new Error("A scene can contain at most 32 authored beats.");
      const right={...clone(beat),id:raw.splitId,start:at,duration:arithmetic(end,at,true)};
      beats[index]={...beat,duration:arithmetic(at,beat.start,true)};beats.splice(index+1,0,right);
    } else if(raw.operation==="trim") {
      const start=parseBeatTime(raw.start),finish=parseBeatTime(raw.end);
      if(compareBeatTimelineTime(start,beat.start)<0||compareBeatTimelineTime(finish,end)>0||compareBeatTimelineTime(start,finish)>=0)
        throw new Error("Trim must retain a positive interval inside the selected saved beat.");
      beats[index]={...beat,start,duration:arithmetic(finish,start,true)};
    } else if(raw.operation==="move") {
      beats[index]={...beat,start:parseBeatTime(raw.start)};
    } else if(raw.operation==="ripple") {
      const duration=parseBeatTime(raw.duration),delta=arithmetic(duration,beat.duration,true);
      beats[index]={...beat,duration};
      for(let next=index+1;next<beats.length;next++)beats[next]={...beats[next],start:arithmetic(beats[next].start,delta)};
    } else {
      if(!coverage.exact)throw new Error("Reordering requires complete beat coverage; resolve its gaps before changing the order.");
      if(!/^[1-9][0-9]*$/.test(raw.position))throw new Error("Choose a whole beat position, starting at one.");
      const position=Number(raw.position)-1;
      if(!Number.isSafeInteger(position)||position<0||position>=beats.length)throw new Error("The target beat position is outside this scene.");
      beats.splice(index,1);beats.splice(position,0,beat);
      let cursor=zero();
      beats=beats.map(item=>{const moved={...item,start:cursor};cursor=arithmetic(cursor,item.duration);return moved;});
    }
    beats.sort((a,b)=>compareBeatTimelineTime(a.start,b.start)||a.id.localeCompare(b.id));
  }
  const change:SceneBeatTimelineChange={type:"edit_scene_beat_timeline",expected:narrativeEditingSource(project),
    scene_id:sceneId,expected_duration:expectedDuration,expected_beats:expectedBeats,beats,rationale:raw.rationale};
  applySceneBeatTimeline(clone(project),change);
  return change;
}
/** Read persisted journal entries, never synthesize success or inverse history. */
export function beatTimelineHistory(events:ProjectEvent[],project:Project,sceneId:string):BeatTimelineUndo[] {
  const current=narrativeEditingSource(project),seen=new Set<number>();
  return events.flatMap(event=>{
    const value:unknown={eventRevision:event.revision,change:event.change};
    if(!undoShape(value)||seen.has(event.revision)||event.revision>project.revision)return [];
    const source=value.change.expected;
    if(value.change.scene_id!==sceneId||source.project_id!==current.project_id||source.generation!==current.generation||source.branch_id!==current.branch_id)return [];
    seen.add(event.revision);return [clone(value)];
  }).sort((a,b)=>b.eventRevision-a.eventRevision);
}
