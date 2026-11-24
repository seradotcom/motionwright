import { describe,expect,it } from "vitest";
import { fixtureProject } from "./fixture";
import { parseBeatTime } from "./sceneBeatTiming";
import { applyChange,previewSceneBeatTimeline } from "./api";
import { emptyProductionDesign } from "./creativeProduction";
import { emptyNarrativeEditing,narrativeEditingSource } from "./narrativeEditing";
import { applySceneBeatTimeline,beatTimelineHistory,savedBeatTimelineInput,sceneBeatTimelineProposal } from "./sceneBeatTimeline";
import type { BeatTimelineRaw,SceneBeatTimelineChange } from "./sceneBeatTimeline";
import { acknowledgeWorkbenchDraft,createWorkbenchDraft,decodeWorkbenchCopy,editWorkbenchDraft,encodeWorkbenchCopy,
  prepareWorkbenchSubmission,retainWorkbenchFailure,workbenchUnconfirmed } from "./creativeWorkbenchDraft";
import type { Beat,Project,ProjectEvent,RationalTime } from "./types";
const id=(number:number)=>"70000000-0000-4000-8000-"+String(number).padStart(12,"0");
const time=(n:number|string,d:number|string=1):RationalTime=>({num:String(n),den:String(d)});
const requestId=id(99),writerId=id(98);
function source():Project {
  const project=structuredClone(fixtureProject);project.schema_version=2;project.locks=[];project.branch_workspaces=[];
  project.production_design=emptyProductionDesign();project.narrative.editing=undefined;project.narrative.protected_sections=[];
  const scene=project.scenes[0];scene.status="draft";
  scene.beats=[0,1,2].map((part):Beat=>({id:id(part+1),label:"Part "+(part+1),objective:"Keep original part "+(part+1),
    start:time(part*2),duration:time(2)}));
  scene.duration=time(6);
  return project;
}
function raw(project:Project,patch:Partial<BeatTimelineRaw>={}):BeatTimelineRaw {
  return {...savedBeatTimelineInput(project.scenes[0]).config,start:"1/3",splitId:id(4),rationale:"Create one exact editorial cut.",...patch};
}
const proposal=(project:Project,patch:Partial<BeatTimelineRaw>={})=>sceneBeatTimelineProposal(project,project.scenes[0].id,raw(project,patch));
const event=(change:SceneBeatTimelineChange):ProjectEvent=>({revision:change.expected.base_revision+1,change,created_at:"2026-11-24T08:00:00Z"});
describe("scene beat timing operations as one conditional edit",()=>{
  it("splits exactly, keeps the original left identity and preserves text, media and every unrelated clock",async()=>{
    const project=source(),before=structuredClone(project),change=proposal(project);
    expect(change.beats.map(beat=>[beat.id,beat.start,beat.duration])).toEqual([
      [id(1),time(0),time(1,3)],[id(4),time(1,3),time(5,3)],[id(2),time(2),time(2)],[id(3),time(4),time(2)]]);
    expect(change.beats[1].label).toBe(change.beats[0].label);
    expect(change.beats[1].objective).toBe(change.beats[0].objective);
    const preview=await previewSceneBeatTimeline(project,change);expect(project).toEqual(before);
    const saved=await applyChange(project,change);expect(saved).toEqual({...preview,updated_at:saved.updated_at});
    expect(saved.revision).toBe(project.revision+1);
    expect(saved.scenes[0]).toEqual({...project.scenes[0],beats:change.beats});
    expect(saved.scenes.slice(1)).toEqual(project.scenes.slice(1));expect(saved.audio).toEqual(project.audio);expect(saved.assets).toEqual(project.assets);
  });
  it("trims a half-open subinterval without filling the resulting gaps",()=>{
    const change=proposal(source(),{operation:"trim",start:"1/3",end:"5/3"});
    expect(change.beats[0]).toMatchObject({start:time(1,3),duration:time(4,3)});
    expect(change.beats.slice(1)).toEqual(source().scenes[0].beats.slice(1));
  });
  it("moves only the chosen beat into existing room and restores canonical ordering",()=>{
    const project=source();project.scenes[0].beats=project.scenes[0].beats.slice(0,2).map(beat=>({...beat,duration:time(1)}));
    const change=proposal(project,{operation:"move",start:"4"});
    expect(change.beats.map(beat=>[beat.id,beat.start])).toEqual([[id(2),time(2)],[id(1),time(4)]]);
    expect(change.beats.map(beat=>beat.duration)).toEqual([time(1),time(1)]);
  });
  it("ripples every later beat exactly while preserving gaps and the scene duration",()=>{
    const project=source(),change=proposal(project,{operation:"ripple",duration:"3/2"});
    expect(change.beats.map(beat=>[beat.start,beat.duration])).toEqual([[time(0),time(3,2)],[time(3,2),time(2)],[time(7,2),time(2)]]);
    expect(change.expected_duration).toEqual(time(6));
    expect(()=>proposal(project,{operation:"ripple",duration:"7/3"})).toThrow(/fit|scene|duration/i);
  });
  it("reorders a fully covered scene with every identity and duration intact",()=>{
    const project=source(),change=proposal(project,{operation:"reorder",position:"3"});
    expect(change.beats.map(beat=>[beat.id,beat.start,beat.duration])).toEqual([[id(2),time(0),time(2)],[id(3),time(2),time(2)],[id(1),time(4),time(2)]]);
    project.scenes[0].beats[0].duration=time(1);
    expect(()=>proposal(project,{operation:"reorder",position:"3"})).toThrow(/coverage/);
  });
  it("inverts journal timing after unrelated edits without restoring human approval and supports redo",async()=>{
    const project=source();project.scenes[0].status="approved";
    const change=proposal(project),saved=await applyChange(project,change);
    expect(saved.scenes[0].status).toBe("draft");
    const unrelated=await applyChange(saved,{type:"rename_project",title:"Keep this later title"});
    const history=beatTimelineHistory([event(change)],unrelated,project.scenes[0].id);
    const inverse=proposal(unrelated,{operation:"undo",undo:history[0]});
    const reopened=JSON.parse(JSON.stringify(unrelated)) as Project;
    const restored=await applyChange(reopened,inverse);
    expect(restored.title).toBe("Keep this later title");expect(restored.scenes[0].beats).toEqual(project.scenes[0].beats);
    expect(restored.scenes[0].status).toBe("draft");
    const redo=proposal(restored,{operation:"undo",undo:beatTimelineHistory([event(inverse)],restored,project.scenes[0].id)[0]});
    expect((await applyChange(restored,redo)).scenes[0].beats).toEqual(saved.scenes[0].beats);
  });
  it("refuses an inverse after one beat changed instead of overwriting later work",async()=>{
    const project=source(),change=proposal(project),saved=await applyChange(project,change);
    const later=await applyChange(saved,{type:"upsert_scene_beat",scene_id:saved.scenes[0].id,beat:{...saved.scenes[0].beats[1],label:"Later wording"}});
    const before=structuredClone(later);
    expect(()=>proposal(later,{operation:"undo",undo:beatTimelineHistory([event(change)],later,later.scenes[0].id)[0]})).toThrow(/conditional source/);
    expect(later).toEqual(before);
  });
  it.each(["project-content","project-timing","scene-content","scene-timing"] as const)("refuses %s lock without writing a partial timeline",lock=>{
    const project=source(),change=proposal(project),[scope,kind]=lock.split("-");
    project.locks.push({id:id(90),resource:scope+":"+(scope==="scene"?project.scenes[0].id:project.id),kind:kind as "content"|"timing",note:"Protected timing"});
    const before=structuredClone(project);
    expect(()=>applySceneBeatTimeline(project,change)).toThrow(/locked/);expect(project).toEqual(before);
  });
  it.each(["revision","branch","generation","duration","beats"] as const)("refuses changed %s source without mutation",field=>{
    const project=source(),change=proposal(project);
    if(field==="revision")change.expected.base_revision++;
    else if(field==="branch")change.expected.branch_id=id(90);
    else if(field==="generation")change.expected.generation=id(90);
    else if(field==="duration")change.expected_duration=time(7);
    else change.expected_beats[0].objective="Not the observed source";
    const before=structuredClone(project);
    expect(()=>applySceneBeatTimeline(project,change)).toThrow(/source/);expect(project).toEqual(before);
  });
  it.each(["0","2","1/","-1","6"])("rejects invalid split instant %s",start=>{
    const project=source(),before=structuredClone(project);expect(()=>proposal(project,{start})).toThrow();expect(project).toEqual(before);
  });
  it("rejects overlapping moves, duplicate identities, identity stealing, oversized and no-op edits",()=>{
    const project=source();
    expect(()=>proposal(project,{operation:"move",start:"1"})).toThrow(/overlap/);
    expect(()=>proposal(project,{splitId:id(2)})).toThrow(/identity/);
    project.scenes[1].beats.push({...project.scenes[0].beats[0],id:id(4)});
    expect(()=>proposal(project)).toThrow(/identity/);
    project.scenes[1].beats=[];project.scenes[0].beats=Array.from({length:32},(_,i)=>({...project.scenes[0].beats[0],id:id(100+i),start:parseBeatTime(i+"/8"),duration:time(1,8)}));
    expect(()=>proposal(project,{start:"1/16"})).toThrow(/32/);
    expect(()=>proposal(source(),{operation:"move",start:"0"})).toThrow(/already/);
  });
  it("retains rational source instants beyond binary64 precision",()=>{
    const project=source(),n="9007199254740993",d="9007199254740995";
    project.scenes[0].beats=[{...project.scenes[0].beats[0],duration:time(n,d)}];
    const change=proposal(project,{start:"1/"+d});
    expect(change.beats[1].duration).toEqual(time("9007199254740992",d));
  });
  it("runs the existing editorial minimum-hold guard before the timing proposal can be accepted",async()=>{
    const project=source(),editorial={id:id(86),label:"Explain source",objective:"Keep reading time",audience_takeaway:"Understand",claim_ids:[],preferred_duration:null};
    project.narrative.beats=[editorial];
    project.narrative.editing={...emptyNarrativeEditing(),
      bindings:[{id:id(85),beat_id:editorial.id,target:{kind:"scene_beat",scene_id:project.scenes[0].id,scene_beat_id:id(1)}}],
      minimum_holds:[{beat_id:editorial.id,duration:time(1)}]};
    const before=structuredClone(project);
    expect(()=>proposal(project)).toThrow(/hold|minimum/);
    const change=proposal(source());change.expected=narrativeEditingSource(project);
    await expect(previewSceneBeatTimeline(project,change)).rejects.toThrow(/hold|minimum/);
    await expect(applyChange(project,change)).rejects.toThrow(/hold|minimum/);expect(project).toEqual(before);
  });
  it("filters inverses by real journal revision, project generation, branch and scene",async()=>{
    const project=source(),change=proposal(project),saved=await applyChange(project,change),good=event(change);
    const forged=structuredClone(good);forged.revision++;
    const foreign=structuredClone(good);(foreign.change as SceneBeatTimelineChange).expected.branch_id=id(90);
    expect(beatTimelineHistory([good,good,forged,foreign],saved,project.scenes[0].id)).toHaveLength(1);
    expect(beatTimelineHistory([good],{...saved,generation:id(90)},project.scenes[0].id)).toEqual([]);
  });
  it("reuses raw recovery, preserves later input, and keeps recovered NOT_SENT conservative",async()=>{
    const project=source(),target={kind:"beat_timeline" as const,sceneId:project.scenes[0].id};
    let draft=createWorkbenchDraft(project,target);if(draft.input.kind!=="beat_timeline")throw new Error("Missing input");
    draft=editWorkbenchDraft(draft,{...draft.input,config:raw(project,{start:"1/"})});
    const copy=()=>encodeWorkbenchCopy({schema:"motionwright.creative-workbench-recovery/1",scope:draft.scope,writerId,updatedAt:1,draft});
    const encoded=copy();expect(decodeWorkbenchCopy(encoded,draft.scope,writerId).draft).toEqual(draft);
    await expect(prepareWorkbenchSubmission(draft,project,requestId,"save")).rejects.toThrow();
    if(draft.input.kind!=="beat_timeline")throw new Error("Missing input");
    draft=editWorkbenchDraft(draft,{...draft.input,config:raw(project)});
    const prepared=await prepareWorkbenchSubmission(draft,project,requestId,"save");
    const saved=await applyChange(project,prepared.change);
    const clean=acknowledgeWorkbenchDraft(prepared.draft,requestId,saved);expect(clean.pending).toBeNull();expect(clean.intent).toBe(false);
    if(prepared.draft.input.kind!=="beat_timeline")throw new Error("Missing input");
    const later=editWorkbenchDraft(prepared.draft,{...prepared.draft.input,config:{...prepared.draft.input.config,start:"2/"}});
    const ack=acknowledgeWorkbenchDraft(later,requestId,saved);expect(ack.input).toEqual(later.input);expect(ack.intent).toBe(true);
    draft=retainWorkbenchFailure(prepared.draft,requestId,"Copy was not confirmed",true);
    const recovered=decodeWorkbenchCopy(copy(),draft.scope,writerId).draft;
    expect(workbenchUnconfirmed(recovered)).toBe(true);
    await expect(prepareWorkbenchSubmission(recovered,saved,id(97),"save")).rejects.toThrow(/unconfirmed/);
    const corrupt=JSON.parse(encoded);corrupt.draft.input.config.effect_grant="not authority";
    expect(()=>decodeWorkbenchCopy(JSON.stringify(corrupt),draft.scope,writerId)).toThrow(/shape/);
    expect(narrativeEditingSource(saved).base_revision).toBe(project.revision+1);
  });
});
