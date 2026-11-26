/** Closed first-party source board. Actual media realization belongs to
 * the original digest-bound Film and existing native asset provider. */
import type {CanvasNode,CanvasKeyframe,Change,Project,Scene} from "./types";
import type {CaptureSourceRecord} from "./captureSource";
import {validateCaptureRecord} from "./captureSource";
import {emptyProductionDesign,sameValue} from "./creativeProduction";
import {stageNodeId} from "./productStage";
import {recipeFrameTime} from "./focusTransfer";

export const CAPTURE_BOARD_DISCLOSURE="SYNTHETIC FIRST-PARTY / NOT EXECUTION EVIDENCE";
export type CaptureBoardMode="reference_board"|"product_flow_bridge";
export type CaptureBoardRole="context"|"mechanism"|"evidence"|"comparison"|"before"|"after";
export interface CaptureBoardEntry {
  capture_id:string;screenshot_sha256:string;manifest_sha256:string;fixture_state_sha256:string;
  role:CaptureBoardRole;label:string;permission_note:string;
}
export interface CaptureBoard {
  id:string;scene_id:string;component_version:1;mode:CaptureBoardMode;entries:[CaptureBoardEntry,CaptureBoardEntry];
  reveal_start_frame:number;reveal_duration_frames:number;
}
export interface CaptureBoardRawEntry {
  capture_id:string;role:CaptureBoardRole;label:string;permission_note:string;
}
export interface CaptureBoardRaw {
  mode:CaptureBoardMode;entries:[CaptureBoardRawEntry,CaptureBoardRawEntry];
  reveal_start_frame:string;reveal_duration_frames:string;
}
type BoardChange=Extract<Change,{type:"upsert_capture_board"|"remove_capture_board"}>;
const roles:readonly CaptureBoardRole[]=["context","mechanism","evidence","comparison","before","after"];
const uuid=/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const digest=/^[0-9a-f]{64}$/;
const control=/\p{Cc}/u;
const bytes=(text:string)=>new TextEncoder().encode(text).length;
const record=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==="object"&&!Array.isArray(value);
const exact=(value:Record<string,unknown>,keys:readonly string[])=>Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const fail=(message:string):never=>{throw new Error("Capture board: "+message);};
const count=(value:string)=>Array.from(value).length;
const validFrame=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0&&value<=36000;
const validText=(entry:CaptureBoardRawEntry)=>entry.label.trim().length>0&&count(entry.label)<=24&&!control.test(entry.label)&&
  count(entry.permission_note.trim())>=12&&bytes(entry.permission_note)<=320&&!control.test(entry.permission_note);
const rawEntry=(role:CaptureBoardRole):CaptureBoardRawEntry=>({capture_id:"",role,label:"",permission_note:""});
export function defaultCaptureBoardRaw(mode:CaptureBoardMode="reference_board"):CaptureBoardRaw{
  return {mode,entries:mode==="reference_board"?[rawEntry("context"),rawEntry("evidence")]:[rawEntry("before"),rawEntry("after")],
    reveal_start_frame:mode==="reference_board"?"0":"30",reveal_duration_frames:mode==="reference_board"?"0":"15"};
}
export function rawCaptureBoard(board:CaptureBoard):CaptureBoardRaw{
  return {mode:board.mode,entries:board.entries.map(entry=>({capture_id:entry.capture_id,role:entry.role,
    label:entry.label,permission_note:entry.permission_note})) as CaptureBoardRaw["entries"],
    reveal_start_frame:String(board.reveal_start_frame),reveal_duration_frames:String(board.reveal_duration_frames)};
}
function entryShape(value:unknown,committed:boolean):boolean{
  const keys=["capture_id","role","label","permission_note"];
  if(committed)keys.push("screenshot_sha256","manifest_sha256","fixture_state_sha256");
  if(!record(value)||!exact(value,keys)||!roles.includes(value.role as CaptureBoardRole)||
    typeof value.capture_id!=="string"||(!committed&&value.capture_id!==""&&!uuid.test(value.capture_id))||
    (committed&&!uuid.test(value.capture_id))||typeof value.label!=="string"||bytes(value.label)>256||
    typeof value.permission_note!=="string"||bytes(value.permission_note)>640)return false;
  return !committed||["screenshot_sha256","manifest_sha256","fixture_state_sha256"].every(key=>typeof value[key]==="string"&&digest.test(value[key] as string));
}
export function captureBoardRawShape(value:unknown):value is CaptureBoardRaw{
  return record(value)&&exact(value,["mode","entries","reveal_start_frame","reveal_duration_frames"])&&
    ["reference_board","product_flow_bridge"].includes(String(value.mode))&&
    Array.isArray(value.entries)&&value.entries.length===2&&value.entries.every(entry=>entryShape(entry,false))&&
    typeof value.reveal_start_frame==="string"&&value.reveal_start_frame.length<=64&&
    typeof value.reveal_duration_frames==="string"&&value.reveal_duration_frames.length<=64;
}
export function captureBoardShape(value:unknown):value is CaptureBoard{
  return record(value)&&exact(value,["id","scene_id","component_version","mode","entries","reveal_start_frame","reveal_duration_frames"])&&
    typeof value.id==="string"&&uuid.test(value.id)&&value.id!=="00000000-0000-0000-0000-000000000000"&&
    typeof value.scene_id==="string"&&uuid.test(value.scene_id)&&value.component_version===1&&
    ["reference_board","product_flow_bridge"].includes(String(value.mode))&&
    Array.isArray(value.entries)&&value.entries.length===2&&value.entries.every(entry=>entryShape(entry,true))&&
    validFrame(value.reveal_start_frame)&&validFrame(value.reveal_duration_frames);
}
export function captureBoardSources(project:Project,board:CaptureBoard):[CaptureSourceRecord,CaptureSourceRecord]{
  if(!captureBoardShape(board))fail("unsupported version or identity");
  const selected=board.entries.map(entry=>{
    if(!validText(entry))fail("each reference needs a bounded original label and explicit source-use declaration");
    const source=project.production_design?.captures?.find(capture=>capture.id===entry.capture_id);
    if(!source)throw new Error("Capture board: choose two previously registered first-party captures");
    validateCaptureRecord(project,source);
    if(source.screenshot_sha256!==entry.screenshot_sha256||source.manifest_sha256!==entry.manifest_sha256||
      source.fixture_state_sha256!==entry.fixture_state_sha256)fail("registered immutable source identities changed");
    return source;
  }) as [CaptureSourceRecord,CaptureSourceRecord];
  const [a,b]=selected;
  if(a.id===b.id||a.screenshot_sha256===b.screenshot_sha256||a.screenshot_asset_id===b.screenshot_asset_id||
    board.entries[0].role===board.entries[1].role)fail("select distinct original captures and distinct narrative roles");
  if(board.mode==="reference_board"){
    if(board.reveal_start_frame!==0||board.reveal_duration_frames!==0||
      board.entries.some(entry=>entry.role==="before"||entry.role==="after"))fail("reference board is static and uses explicit reference roles");
  }else if(board.entries[0].role!=="before"||board.entries[1].role!=="after"||board.reveal_start_frame===0||
    board.reveal_duration_frames===0||board.reveal_start_frame+board.reveal_duration_frames>36000||
    a.source_generation!==b.source_generation||a.source_commit!==b.source_commit||a.app_version!==b.app_version||
    a.app_id!==b.app_id||a.origin!==b.origin||a.route!==b.route||a.viewport_width!==b.viewport_width||
    a.viewport_height!==b.viewport_height||a.source_revision>=b.source_revision||a.fixture_state_sha256===b.fixture_state_sha256||
    a.manifest_sha256===b.manifest_sha256||a.claim_id===b.claim_id)fail("flow needs distinct ordered states of the same original app/build and an exact editorial reveal");
  return selected;
}
export function captureBoardFromRaw(project:Project,sceneId:string,id:string,raw:CaptureBoardRaw):CaptureBoard{
  if(!captureBoardRawShape(raw))fail("invalid bounded author input");
  const frame=(value:string)=>{
    if(!/^\d{1,5}$/.test(value.trim())||!validFrame(Number(value)))fail("enter complete frame indices");
    return Number(value);
  };
  const entries=raw.entries.map(entry=>{
    const source=project.production_design?.captures?.find(item=>item.id===entry.capture_id);
    if(!source)throw new Error("Capture board: choose two previously registered first-party captures");
    return {...entry,screenshot_sha256:source.screenshot_sha256,manifest_sha256:source.manifest_sha256,
      fixture_state_sha256:source.fixture_state_sha256};
  }) as CaptureBoard["entries"];
  const board:CaptureBoard={id,scene_id:sceneId,component_version:1,mode:raw.mode,entries,
    reveal_start_frame:frame(raw.reveal_start_frame),reveal_duration_frames:frame(raw.reveal_duration_frames)};
  captureBoardSources(project,board);
  return board;
}
export const captureBoardNodeId=(boardId:string,role:string)=>stageNodeId(boardId,"capture-board/"+role);
export async function realizeCaptureBoard(project:Project,board:CaptureBoard,w=1920,h=1080):Promise<CanvasNode[]>{
  const sources=captureBoardSources(project,board),small=Math.min(w,h),wide=w/h>=1.35;
  if(![w,h].every(value=>Number.isInteger(value)&&value>=320&&value<=7680))fail("unsupported output dimensions");
  const title=board.mode==="reference_board"?"REFERENCE BOARD":"PRODUCT FLOW / AUTHORED STATES";
  const nodes:CanvasNode[]=[];
  const add=async(role:string,rect:number[],text:string|null,font:number,weight:number,fill:string,second=false)=>{
    const [x,y,ww,hh]=rect;
    const isText=text!==null;
    const keyframes:CanvasKeyframe[]=second&&board.mode==="product_flow_bridge"?[
      {at:{num:"0",den:"1"},property:"opacity",value:0,interpolation:"linear"},
      {at:recipeFrameTime(board.reveal_start_frame,{num:"30",den:"1"}),property:"opacity",value:0,interpolation:"hold"},
      {at:recipeFrameTime(board.reveal_start_frame+board.reveal_duration_frames,{num:"30",den:"1"}),property:"opacity",value:1,interpolation:"linear"},
    ]:[];
    const node:CanvasNode={id:await captureBoardNodeId(board.id,role),name:"CaptureBoard / "+role,kind:isText?"text":"rectangle",
      parent_id:null,x:x*w,y:y*h,width:ww*w,height:hh*h,rotation_deg:0,opacity:1,text,coordinate_space:"project_pixels",z_index:nodes.length,
      style:{fill,stroke:null,stroke_width:0,font_family:isText?"Instrument Sans Variable":null,font_size:isText?font:null,
        font_weight:isText?weight:null,line_height:isText?1.12:null,blend_mode:"normal"},relations:[],property_locks:[],keyframes};
    if(isText){
      const lines=text!.split("\n");
      if(lines.some(line=>count(line)*font*.64>node.width*.95)||font*(1.4+1.12*(lines.length-1))>node.height)
        fail(node.name+" copy exceeds its bounded text box; shorten the authored label");
    }
    nodes.push(node);
  };
  await add("surface",[.06,.06,.88,.88],null,0,400,"#101820");
  await add("title",[.08,.075,.84,.075],title,small/32,600,"#F2F4F3");
  for(const [i,entry] of board.entries.entries()){
    const x=i===0?.08:.52;
    const label=wide?[x,.17,.40,.115]:i===0?[.08,.16,.84,.085]:[.08,.53,.84,.085];
    const image=wide?[x,.295,.40,.545]:i===0?[.08,.255,.84,.255]:[.08,.625,.84,.24];
    const copy=entry.role[0].toUpperCase()+entry.role.slice(1)+": "+entry.label+"\nSource r"+sources[i].source_revision;
    await add("label-"+(i+1),label,copy,small/40,400,"#F2F4F3",i===1);
    await add("image-"+(i+1),image,null,0,400,"#263B4F",i===1);
  }
  await add("disclosure",[.08,.89,.84,.05],CAPTURE_BOARD_DISCLOSURE,small/48,400,"#F2F4F3");
  return nodes;
}
export async function validateCaptureBoardScene(project:Project,board:CaptureBoard,scene:Scene):Promise<void>{
  captureBoardSources(project,board);
  if(scene.id!==board.scene_id||scene.renderer!=="motion-canvas"||
    !sameValue(scene.camera,{center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05})||scene.beats.length||
    BigInt(scene.duration.num)<2n*BigInt(scene.duration.den))fail("requires a dedicated neutral Motion Canvas scene, at least two seconds, without beats");
  const design=project.production_design;
  if(design?.brand_profile||design?.heroes?.some(row=>row.scene_id===scene.id)||design?.stages?.some(row=>row.scene_id===scene.id)||
    design?.procedural_fields?.some(row=>row.scene_id===scene.id)||design?.semantic_graphs?.some(row=>row.scene_id===scene.id)||
    design?.camera_reframes?.some(row=>row.scene_id===scene.id)||design?.capsules?.some(row=>row.scene_id===scene.id)||
    design?.measured_bindings?.some(row=>row.scene_id===scene.id)||design?.scene_boundaries?.some(row=>row.scene_id===scene.id))
    fail("cannot replace other authored components, boundaries or an active mandatory brand profile");
  if(board.mode==="product_flow_bridge"&&BigInt(board.reveal_start_frame+board.reveal_duration_frames)*BigInt(scene.duration.den)>=30n*BigInt(scene.duration.num))
    fail("reveal must finish strictly inside the scene");
  const baseline=await realizeCaptureBoard(project,board);
  if(scene.nodes.length!==baseline.length||baseline.some(expected=>!scene.nodes.some(actual=>sameValue({...actual,property_locks:expected.property_locks},expected))))
    fail("original board slots and disclosure must retain their exact authored configuration");
  const profiles=project.deliverables.filter(profile=>!profile.included_scene_ids.length||profile.included_scene_ids.includes(scene.id));
  if(!profiles.length)fail("include the board in a saved delivery profile");
  for(const profile of profiles){
    if(profile.framing_strategy!=="replan"||(board.mode==="product_flow_bridge"&&
      (profile.frame_rate.num!=="30"||profile.frame_rate.den!=="1")))fail("requires Replan outputs and exact 30fps for product-state reveal");
    if(project.scenes.some(item=>(!profile.included_scene_ids.length||profile.included_scene_ids.includes(item.id))&&item.camera.safe_margin>.06))
      fail("selected output safe margin exceeds the original board layout");
    await realizeCaptureBoard(project,board,profile.width,profile.height);
    if(baseline.some(node=>node.kind==="text"&&Object.hasOwn(profile.text_overrides,node.id)&&profile.text_overrides[node.id]!==node.text))
      fail("source labels and synthetic disclosure must retain exact wording");
  }
}
export async function validateCaptureBoards(project:Project):Promise<void>{
  const boards=project.production_design?.capture_boards??[];
  if(boards.length>16||new Set(boards.map(board=>board.id)).size!==boards.length||new Set(boards.map(board=>board.scene_id)).size!==boards.length)
    fail("project board capacity or duplicate identity");
  for(const board of boards){
    const scene=project.scenes.find(item=>item.id===board.scene_id);
    if(!scene)throw new Error("Capture board: source scene was removed");
    await validateCaptureBoardScene(project,board,scene);
  }
}
export async function applyCaptureBoardChange(project:Project,change:BoardChange):Promise<void>{
  const sceneId=change.type==="upsert_capture_board"?change.board.scene_id:change.scene_id;
  const scene=project.scenes.find(item=>item.id===sceneId);
  if(!scene)throw new Error("Capture board: choose a saved scene");
  if(project.locks.some(lock=>["project:"+project.id,"scene:"+sceneId].includes(lock.resource)&&
    ["content","position","style","timing","renderer"].includes(lock.kind))||scene.nodes.some(node=>node.property_locks.length))
    fail("unlock the original board objects before replacing or removing their configuration");
  const next=structuredClone(project),target=next.scenes.find(item=>item.id===sceneId)!;
  const design=next.production_design??emptyProductionDesign(),boards=design.capture_boards??[];
  if(change.type==="upsert_capture_board"){
    const board=change.board,existing=boards.find(item=>item.id===board.id);
    if(existing){
      if(existing.scene_id!==sceneId)fail("cannot move an existing board to another scene");
      await validateCaptureBoardScene(project,existing,scene);
    }else if(scene.nodes.length||boards.some(item=>item.scene_id===sceneId))fail("choose an empty original scene; no existing Canvas is replaced");
    target.nodes=await realizeCaptureBoard(project,board);
    design.capture_boards=existing?boards.map(item=>item.id===board.id?structuredClone(board):item):[...boards,structuredClone(board)];
  }else{
    const board=boards.find(item=>item.id===change.board_id&&item.scene_id===sceneId);
    if(!board)throw new Error("Capture board: no matching saved board");
    await validateCaptureBoardScene(project,board,scene);
    target.nodes=[];
    design.capture_boards=boards.filter(item=>item.id!==change.board_id);
    if(!design.capture_boards.length)delete design.capture_boards;
  }
  target.status="draft";next.production_design=design;
  await validateCaptureBoards(next);
  project.scenes=next.scenes;project.production_design=next.production_design;
}
