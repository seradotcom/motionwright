import {describe,expect,it} from "vitest";
import {applyChange} from "./api";
import {emptyProductionDesign} from "./creativeProduction";
import {fixtureProject} from "./fixture";
import type {CaptureSourceRecord} from "./captureSource";
import type {Project} from "./types";
import {CAPTURE_BOARD_DISCLOSURE,applyCaptureBoardChange,captureBoardFromRaw,captureBoardNodeId,captureBoardShape,defaultCaptureBoardRaw,rawCaptureBoard,realizeCaptureBoard,validateCaptureBoards} from "./captureBoard";
import {acknowledgeWorkbenchDraft,createWorkbenchDraft,decodeWorkbenchCopy,editWorkbenchDraft,encodeWorkbenchCopy,prepareWorkbenchSubmission,reconcileWorkbenchDraft,workbenchUnconfirmed} from "./creativeWorkbenchDraft";

const SCREEN="a".repeat(64),MANIFEST="b".repeat(64);
const captureId="00000000-0000-4000-8000-0000000000f1";
const screenshotId="00000000-0000-4000-8000-0000000000f2";
const manifestId="00000000-0000-4000-8000-0000000000f3";
const fixtureId="00000000-0000-4000-8000-0000000000f5";
const claimId="00000000-0000-4000-8000-0000000000f4";
function source():{project:Project,record:CaptureSourceRecord}{
  const project=structuredClone(fixtureProject);
  project.schema_version=2;
  project.revision=7;
  project.locks=[];
  project.branch_workspaces=[];
  project.production_design=emptyProductionDesign();
  project.assets=[
    {id:screenshotId,name:"Approved image.png",media_type:"image/png",
      content_sha256:SCREEN,source_revision:"local-import"},
    {id:manifestId,name:"Capture manifest.json",media_type:"application/json",
      content_sha256:MANIFEST,source_revision:"local-import"},
    {id:fixtureId,name:"Capture data state.json",media_type:"application/json",
      content_sha256:"f".repeat(64),source_revision:"local-import"},
  ];
  project.brief.claims=[{
    id:claimId,
    text:"A local first-party Studio screen is visible in this synthetic source",
    context:"synthetic-first-party-capture-validation",
    source:{kind:"project_revision",revision:project.revision},
    source_revision:"fixture-7",
  }];
  const record:CaptureSourceRecord={
    schema:"motionwright.capture-source/1",id:captureId,
    screenshot_asset_id:screenshotId,screenshot_sha256:SCREEN,
    manifest_asset_id:manifestId,manifest_sha256:MANIFEST,
    fixture_state_asset_id:fixtureId,fixture_state_sha256:"f".repeat(64),
    claim_id:claimId,source_project_id:project.id,
    source_generation:project.generation,source_revision:7,
    app_id:"motionwright-studio",app_version:"0.1.0",source_commit:"c".repeat(40),
    scenario_id:"production-original-ui-local-v1",
    origin:"http://127.0.0.1:1420",route:"/",
    viewport_width:1440,viewport_height:940,device_scale_factor:1,
    screenshot_repeat_sha256:SCREEN,redaction:{
      selector:".revision-chip",x:100,y:64,width:80,height:28,mask_color:"#0B0B0B",
    },
    external_requests:0,credential_fields:0,
    declared_scope:"first-party-synthetic-fixture-only",
  };
  return {project,record};
}


function boardProject(){
  const {project,record}=source();
  project.scenes=[{...project.scenes[0],renderer:"motion-canvas",nodes:[],beats:[],
    camera:{center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05},duration:{num:"6",den:"1"}}];
  project.deliverables=project.deliverables.map(profile=>({...profile,included_scene_ids:[],protected_scene_ids:[],
    text_overrides:{},framing_strategy:"replan",frame_rate:{num:"30",den:"1"}}));
  const second:CaptureSourceRecord={...record,id:crypto.randomUUID(),source_revision:8,
    screenshot_asset_id:crypto.randomUUID(),manifest_asset_id:crypto.randomUUID(),fixture_state_asset_id:crypto.randomUUID(),
    claim_id:crypto.randomUUID(),screenshot_sha256:"d".repeat(64),screenshot_repeat_sha256:"d".repeat(64),
    manifest_sha256:"e".repeat(64),fixture_state_sha256:"1".repeat(64)};
  project.revision=8;
  project.assets.push(
    {id:second.screenshot_asset_id,name:"Second synthetic.png",media_type:"image/png",content_sha256:second.screenshot_sha256,source_revision:"local-import"},
    {id:second.manifest_asset_id,name:"Second manifest.json",media_type:"application/json",content_sha256:second.manifest_sha256,source_revision:"local-import"},
    {id:second.fixture_state_asset_id,name:"Second fixture.json",media_type:"application/json",content_sha256:second.fixture_state_sha256,source_revision:"local-import"});
  project.brief.claims.push({...project.brief.claims[0],id:second.claim_id,source:{kind:"project_revision",revision:8}});
  project.production_design!.captures=[record,second];
  const id=crypto.randomUUID(),sceneId=project.scenes[0].id,raw=defaultCaptureBoardRaw();
  raw.entries[0]={capture_id:record.id,role:"context",label:"Initial state",permission_note:"Test author permits this synthetic source."};
  raw.entries[1]={capture_id:second.id,role:"evidence",label:"Later state",permission_note:"Test author permits this synthetic source."};
  const board=captureBoardFromRaw(project,sceneId,id,raw);
  return {project,board,raw,sceneId};
}

describe("Source boards and authored product flows",()=>{
  it("replans two original references while preserving exact identities, labels and disclosure",async()=>{
    const {project,board}=await boardProject(),ids=await Promise.all(["surface","title","label-1","image-1","label-2","image-2","disclosure"].map(role=>captureBoardNodeId(board.id,role)));
    for(const [w,h] of [[1920,1080],[1080,1080],[1080,1920]]){
      const nodes=await realizeCaptureBoard(project,board,w,h);
      expect(nodes.map(node=>node.id)).toEqual(ids);
      expect(nodes[6].text).toBe(CAPTURE_BOARD_DISCLOSURE);
      expect(nodes[2].text).toBe("Context: Initial state\nSource r7");
      expect(nodes[4].text).toBe("Evidence: Later state\nSource r8");
      expect(nodes.every(node=>node.keyframes.length===0)).toBe(true);
      expect(w/h>=1.35?nodes[3].x<nodes[5].x:nodes[3].y<nodes[5].y).toBe(true);
    }
    expect(captureBoardShape(board)).toBe(true);
    expect(rawCaptureBoard(board).entries[1].capture_id).toBe(board.entries[1].capture_id);
  });
  it("uses exact delayed opacity keys, rejects provenance substitution, and removes only its own generated slots",async()=>{
    const {project,board,sceneId}=await boardProject(),source=structuredClone(project);
    await applyCaptureBoardChange(project,{type:"upsert_capture_board",board});
    const ids=project.scenes[0].nodes.map(node=>node.id);
    const flow={...board,mode:"product_flow_bridge" as const,reveal_start_frame:30,reveal_duration_frames:15};
    flow.entries=[{...board.entries[0],role:"before"},{...board.entries[1],role:"after"}];
    const bad=structuredClone(flow);bad.entries[1].manifest_sha256="9".repeat(64);
    await expect(applyCaptureBoardChange(project,{type:"upsert_capture_board",board:bad})).rejects.toThrow(/identities changed/);
    const reversed=structuredClone(flow);reversed.entries=[{...flow.entries[1],role:"before"},{...flow.entries[0],role:"after"}];
    await expect(applyCaptureBoardChange(project,{type:"upsert_capture_board",board:reversed})).rejects.toThrow(/ordered states/);
    await applyCaptureBoardChange(project,{type:"upsert_capture_board",board:flow});
    expect(project.scenes[0].nodes.map(node=>node.id)).toEqual(ids);
    const keys=project.scenes[0].nodes[5].keyframes;
    expect(keys.map(key=>key.value)).toEqual([0,0,1]);
    expect(keys.map(key=>key.at)).toEqual([{num:"0",den:"1"},{num:"1",den:"1"},{num:"3",den:"2"}]);
    expect(project.scenes[0].nodes[4].keyframes).toEqual(keys);
    expect(project.scenes[0].nodes[3].keyframes).toEqual([]);
    await validateCaptureBoards(project);
    const edited=structuredClone(project);edited.scenes[0].nodes[6].text="Misleading claim";
    await expect(validateCaptureBoards(edited)).rejects.toThrow(/exact authored configuration/);
    await applyCaptureBoardChange(project,{type:"remove_capture_board",scene_id:sceneId,board_id:board.id});
    expect(project.scenes[0].nodes).toEqual([]);
    expect(project.production_design?.capture_boards).toBeUndefined();
    expect(project.production_design?.captures).toEqual(source.production_design?.captures);
    expect(project.assets).toEqual(source.assets);expect(project.brief.claims).toEqual(source.brief.claims);
  });
  it("keeps workbench original-ACK authority and later raw input through recovery",async()=>{
    const {project,raw,sceneId}=await boardProject();
    const request=crypto.randomUUID(),writer=crypto.randomUUID();
    const draft=editWorkbenchDraft(createWorkbenchDraft(project,{kind:"capture_board",sceneId}),{kind:"capture_board",config:raw});
    const prepared=await prepareWorkbenchSubmission(draft,project,request,"save");
    const encoded=encodeWorkbenchCopy({schema:"motionwright.creative-workbench-recovery/1",scope:draft.scope,writerId:writer,updatedAt:1,draft:prepared.draft});
    const recovered=decodeWorkbenchCopy(encoded,draft.scope,writer).draft;
    expect(recovered).toEqual(prepared.draft);
    const receipt=await applyChange(project,prepared.change);
    expect(workbenchUnconfirmed(reconcileWorkbenchDraft(recovered,receipt))).toBe(true);
    const later=editWorkbenchDraft(recovered,{kind:"capture_board",config:{...raw,entries:[{...raw.entries[0],label:"Later raw input"},raw.entries[1]]}});
    const ack=acknowledgeWorkbenchDraft(later,request,receipt);
    expect(ack.input).toEqual(later.input);expect(ack.pending).toBeNull();expect(ack.intent).toBe(true);
  });
});
