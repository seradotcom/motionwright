import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { applyMeasuredBinding, applyMeasuredTable } from "./measuredData";
import {
  acknowledgeWorkbenchDraft, createWorkbenchDraft, decodeWorkbenchCopy, discardMeasuredPresentation,
  editMeasuredPresentation, editWorkbenchDraft, encodeWorkbenchCopy, measuredBindingProposal,
  measuredPresentation, measuredRaw, prepareWorkbenchSubmission, reconcileWorkbenchDraft,
  resolveWorkbenchDraft, selectMeasuredBinding, selectMeasuredTable, workbenchConflict,
} from "./creativeWorkbenchDraft";
import type { WorkbenchDraft, WorkbenchInput } from "./creativeWorkbenchDraft";

const assetId="00000000-0000-4000-8000-000000000721",tableId="00000000-0000-4000-8000-000000000722";
const textId="00000000-0000-4000-8000-000000000723",barId="00000000-0000-4000-8000-000000000724";
const requestId="00000000-0000-4000-8000-000000000725",writer="00000000-0000-4000-8000-000000000726";
type Input=Extract<WorkbenchInput,{kind:"measured"}>;
const inputOf=(draft:WorkbenchDraft)=>draft.input as Input;
function seeded(){
  const project=structuredClone(fixtureProject);project.schema_version=2;project.locks=[];project.branch_workspaces=[];project.production_design=emptyProductionDesign();
  const scene=project.scenes[0];scene.renderer="motion-canvas";scene.beats=[];
  scene.nodes=scene.nodes.slice(0,2).map((node,index)=>({...node,kind:index?"rectangle":"text",text:index?null:"Authored source",
    parent_id:null,keyframes:[],relations:[],property_locks:[],opacity:1}));
  project.assets.push({id:assetId,name:"Explicit synthetic unit-test rows",media_type:"application/json",content_sha256:"ab".repeat(32),source_revision:null});
  applyMeasuredTable(project,{schema:"motionwright.measured-table/1",id:tableId,label:"Synthetic test rows",version:1,
    source_asset_id:assetId,source_sha256:"ab".repeat(32),rows:[
      {key:"latency",label:"Latency",milli:12500,unit:"milliseconds"},
      {key:"duration",label:"Duration",milli:25000,unit:"milliseconds"},
    ]});
  applyMeasuredBinding(project,{binding_id:textId,scene_id:scene.id,node_id:scene.nodes[0].id,table_id:tableId,
    datum_key:"latency",view:{kind:"counter",decimals:2}});
  applyMeasuredBinding(project,{binding_id:barId,scene_id:scene.id,node_id:scene.nodes[1].id,table_id:tableId,
    datum_key:"latency",view:{kind:"bar",axis_min_milli:0,axis_max_milli:100000,max_width_px:800}});
  const draft=createWorkbenchDraft(project,{kind:"measured",sceneId:scene.id});
  return {project,scene,draft};
}
function choose(state:ReturnType<typeof seeded>,id=textId){
  return editWorkbenchDraft(state.draft,selectMeasuredBinding(inputOf(state.draft),id,state.project,state.scene.id));
}
function patch(draft:WorkbenchDraft,values:Parameters<typeof editMeasuredPresentation>[1]){
  return editWorkbenchDraft(draft,editMeasuredPresentation(inputOf(draft),values));
}
describe("complete measured-output editing",()=>{
  it("updates datum, label format and precision on the same binding while keeping source, style and neighboring objects",async()=>{
    const state=seeded(),draft=patch(choose(state),{selectedKey:"duration",view:"label",decimals:"3"});
    const before=structuredClone(state.project),prepared=await prepareWorkbenchSubmission(draft,state.project,requestId,"binding");
    expect(prepared.change).toEqual({type:"upsert_measured_binding",binding_id:textId,scene_id:state.scene.id,
      node_id:state.scene.nodes[0].id,table_id:tableId,datum_key:"duration",view:{kind:"label",decimals:3}});
    const receipt=await applyChange(state.project,prepared.change),ack=acknowledgeWorkbenchDraft(prepared.draft,requestId,receipt);
    expect(receipt.production_design!.measured_bindings!.map(binding=>binding.id)).toEqual([textId,barId]);
    expect(receipt.scenes[0].nodes[0]).toEqual({...before.scenes[0].nodes[0],text:"Duration · 25.000 ms"});
    expect(receipt.scenes[0].nodes[1]).toEqual(before.scenes[0].nodes[1]);
    expect(receipt.production_design!.measured_tables).toEqual(before.production_design!.measured_tables);
    expect(state.project).toEqual(before);expect(ack.intent).toBe(false);expect(ack.pending).toBeNull();
    const reopened=createWorkbenchDraft(JSON.parse(JSON.stringify(receipt)),state.draft.target);
    expect(measuredPresentation(selectMeasuredBinding(inputOf(reopened),textId,receipt,state.scene.id)))
      .toMatchObject({selectedKey:"duration",view:"label",decimals:"3",selectedNode:state.scene.nodes[0].id});
  });
  it("updates the entire explicit bar axis and datum without altering other geometry",async()=>{
    const state=seeded(),before=structuredClone(state.scene.nodes[1]);
    const draft=patch(choose(state,barId),{selectedKey:"duration",axisMin:"0",axisMax:"50000",maxWidth:"900"});
    const prepared=await prepareWorkbenchSubmission(draft,state.project,requestId,"binding"),receipt=await applyChange(state.project,prepared.change);
    expect(receipt.scenes[0].nodes[1]).toEqual({...before,width:450});
    expect(receipt.production_design!.measured_bindings![1]).toMatchObject({id:barId,view:{kind:"bar",axis_min_milli:0,axis_max_milli:50000,max_width_px:900}});
  });
  it("keeps independent incomplete output, inactive format and source-table buffers while selecting other outputs",()=>{
    const state=seeded();let draft=patch(choose(state),{decimals:"1e",axisMin:"-"});
    let input=selectMeasuredTable(inputOf(draft),tableId,state.project);
    input={...input,tables:{...input.tables,[tableId]:{...measuredRaw(input),label:"  Unsaved table label  "}}};
    input=selectMeasuredBinding(input,barId,state.project,state.scene.id);
    input=editMeasuredPresentation(input,{axisMax:"2e",maxWidth:" 950 "});
    input=selectMeasuredBinding(input,textId,state.project,state.scene.id);
    expect(measuredPresentation(input)).toMatchObject({decimals:"1e",axisMin:"-"});
    expect(input.bindings![barId]).toMatchObject({axisMax:"2e",maxWidth:" 950 "});
    expect(measuredRaw(input).label).toBe("  Unsaved table label  ");
  });
  it("retains later incomplete presentation through the original update acknowledgement",async()=>{
    const state=seeded(),draft=patch(choose(state),{view:"label",decimals:"1"});
    const prepared=await prepareWorkbenchSubmission(draft,state.project,requestId,"binding");
    const later=patch(prepared.draft,{decimals:"1e"}),receipt=await applyChange(state.project,prepared.change);
    const ack=acknowledgeWorkbenchDraft(later,requestId,receipt);
    expect(measuredPresentation(inputOf(ack)).decimals).toBe("1e");expect(ack.intent).toBe(true);
    expect(receipt.production_design!.measured_bindings![0].view).toEqual({kind:"label",decimals:1});
    expect(reconcileWorkbenchDraft(ack,{...receipt,revision:receipt.revision+1})).toBe(ack);
  });
  it("promotes a newly bound output into its original identity without losing input typed during the pending save",async()=>{
    const state=seeded();state.project.production_design!.measured_bindings=[];
    let input=selectMeasuredTable(inputOf(state.draft),tableId,state.project);
    input=editMeasuredPresentation(input,{selectedNode:state.scene.nodes[0].id,selectedKey:"latency",decimals:"3"});
    const prepared=await prepareWorkbenchSubmission(editWorkbenchDraft(state.draft,input),state.project,requestId,"binding");
    const later=patch(prepared.draft,{decimals:"1e"}),receipt=await applyChange(state.project,prepared.change);
    const ack=acknowledgeWorkbenchDraft(later,requestId,receipt),retained=inputOf(ack);
    if(prepared.change.type!=="upsert_measured_binding")throw new Error("Wrong change");
    expect(retained.selectedBinding).toBe(prepared.change.binding_id);
    expect(measuredPresentation(retained).decimals).toBe("1e");expect(measuredRaw(retained).selectedNode).toBe("");
    expect(ack.intent).toBe(true);
  });
  it("does not promote a different output selected after a new binding was sent",async()=>{
    const state=seeded();state.project.production_design!.measured_bindings=[];
    let input=selectMeasuredTable(inputOf(state.draft),tableId,state.project);
    input=editMeasuredPresentation(input,{selectedNode:state.scene.nodes[0].id,selectedKey:"latency"});
    const prepared=await prepareWorkbenchSubmission(editWorkbenchDraft(state.draft,input),state.project,requestId,"binding");
    const later=patch(prepared.draft,{selectedNode:state.scene.nodes[1].id,view:"bar",axisMin:"-",axisMax:"100000",maxWidth:"800"});
    const receipt=await applyChange(state.project,prepared.change),ack=acknowledgeWorkbenchDraft(later,requestId,receipt);
    expect(inputOf(ack).selectedBinding).toBeUndefined();
    expect(measuredPresentation(inputOf(ack))).toMatchObject({selectedNode:state.scene.nodes[1].id,axisMin:"-"});
  });
  it("refuses incomplete, out-of-range, locked, manually changed and removed targets before a request",async()=>{
    const state=seeded(),draft=choose(state,barId);
    for(const values of [{axisMin:"-"},{axisMax:"100"},{maxWidth:"0"},{view:"counter" as const}]){
      await expect(prepareWorkbenchSubmission(patch(draft,values),state.project,requestId,"binding")).rejects.toThrow();
    }
    for(const fault of ["lock","manual","removed","moved"]){
      const changed=structuredClone(state.project);
      if(fault==="lock")changed.scenes[0].nodes[1].property_locks.push("size");
      if(fault==="manual")changed.scenes[0].nodes[1].width++;
      if(fault==="removed")changed.production_design!.measured_bindings=changed.production_design!.measured_bindings!.filter(binding=>binding.id!==barId);
      if(fault==="moved")changed.production_design!.measured_bindings![1].node_id=state.scene.nodes[0].id;
      await expect(prepareWorkbenchSubmission(patch(draft,{maxWidth:"900"}),changed,requestId,"binding")).rejects.toThrow();
    }
    expect(()=>editMeasuredPresentation(inputOf(draft),{selectedNode:state.scene.nodes[0].id})).toThrow("original Canvas node");
  });
  it("requires an explicit revision choice and displays current data through the same table after rebase",async()=>{
    const state=seeded(),draft=patch(choose(state),{decimals:"3"}),peer=structuredClone(state.project);peer.revision++;
    peer.scenes[0].nodes[0].style.fill="#123456";
    expect(workbenchConflict(draft,peer)).toBe(true);
    await expect(prepareWorkbenchSubmission(draft,peer,requestId,"binding")).rejects.toThrow("saved revision");
    const rebased=resolveWorkbenchDraft(draft,peer,"draft"),prepared=await prepareWorkbenchSubmission(rebased,peer,requestId,"binding");
    const receipt=await applyChange(peer,prepared.change);expect(receipt.scenes[0].nodes[0].style.fill).toBe("#123456");
  });
  it("round-trips all raw bindings in the existing recovery schema and rejects added authority fields",()=>{
    const state=seeded();let draft=patch(choose(state),{decimals:"1e"});
    let input=selectMeasuredBinding(inputOf(draft),barId,state.project,state.scene.id);
    draft=editWorkbenchDraft(draft,editMeasuredPresentation(input,{axisMin:"-"}));
    const value={schema:"motionwright.creative-workbench-recovery/1" as const,scope:draft.scope,writerId:writer,updatedAt:1,draft};
    const raw=encodeWorkbenchCopy(value),restored=decodeWorkbenchCopy(raw,draft.scope,writer).draft;
    expect(restored.input).toEqual(draft.input);
    const invalid=JSON.parse(raw);invalid.draft.input.bindings[barId].effectGrant="forged";
    expect(()=>decodeWorkbenchCopy(JSON.stringify(invalid),draft.scope,writer)).toThrow();
    const legacy=createWorkbenchDraft(state.project,state.draft.target);
    expect(decodeWorkbenchCopy(encodeWorkbenchCopy({...value,draft:legacy}),draft.scope,writer).draft.input).toEqual(legacy.input);
  });
  it("uses saved output or discards a removed buffer only after an explicit action while retaining other work",()=>{
    const state=seeded(),draft=patch(choose(state),{decimals:"1e"});
    let input=selectMeasuredBinding(inputOf(draft),barId,state.project,state.scene.id);
    input=editMeasuredPresentation(input,{axisMin:"-"});
    const reset=discardMeasuredPresentation(input,state.project,state.scene.id);
    expect(measuredPresentation(reset).axisMin).toBe("0");expect(reset.bindings![textId].decimals).toBe("1e");
    const removed=structuredClone(state.project);removed.production_design!.measured_bindings=[];
    expect(()=>measuredBindingProposal(input,removed,state.scene.id,requestId)).toThrow("removed");
    const discarded=discardMeasuredPresentation(input,removed,state.scene.id);
    expect(discarded.selectedBinding).toBe("");expect(discarded.bindings![barId]).toBeUndefined();
    expect(discarded.bindings![textId].decimals).toBe("1e");
  });
});
