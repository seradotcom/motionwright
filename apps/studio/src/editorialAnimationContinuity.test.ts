import {describe,expect,it} from "vitest";
import {applyChange} from "./api";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import {nativePoseEdit,type NativePoseDraft} from "./nativePoseDraft";
import {previewTransform} from "./canvasMotion";
import {sampleProfileLayout} from "./profileComposition";
import {contextLabelNode} from "./contextLabel";
import {sampledSceneCamera,CAMERA_REFRAME_MANIM_VERSION} from "./cameraReframe";
import {rationalSeconds,type CanvasNode,type Scene} from "./types";

function source(){
  const project=structuredClone(fixtureProject);
  project.locks=[];project.reviews=[];project.merges=[];project.branch_workspaces=[];
  project.production_design=emptyProductionDesign();
  const scene=project.scenes[0];project.scenes=[scene];
  Object.assign(scene,{start:rationalSeconds(0),duration:rationalSeconds(6),renderer:"motion-canvas",status:"draft",
    camera:{center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05}});
  scene.beats=[0,2,4].map((at,index)=>({id:crypto.randomUUID(),label:"Editorial "+index,objective:"Keep the scene clock",
    start:rationalSeconds(at),duration:rationalSeconds(2)}));
  const node=scene.nodes[0];scene.nodes=[node];
  Object.assign(node,{kind:"rectangle",parent_id:null,x:720,y:400,width:240,height:160,
    rotation_deg:0,opacity:1,text:null,keyframes:[],relations:[],property_locks:[],z_index:0});
  node.style={...node.style,fill:"#86C8D1",stroke:null,stroke_width:0,blend_mode:"normal",
    font_family:null,font_size:null,font_weight:null,line_height:null};
  project.deliverables[0].timing_locked=false;
  return {project,scene,node,profile:project.deliverables[0]};
}
function camera(scene:Scene){
  return {id:crypto.randomUUID(),scene_id:scene.id,component_version:1,
    start_center_x:960,start_center_y:540,end_center_x:978,end_center_y:548,duration_frames:90};
}
function blankLabel(node:CanvasNode):CanvasNode{return {...structuredClone(node),id:crypto.randomUUID(),z_index:1};}

describe("MW05-E01-01/E01-04 editorial beats and a continuous visual clock",()=>{
  it.each(["linear","ease_out_cubic","hold"] as const)("keeps all pose channels and independent fade through beats with %s",async curve=>{
    const {project,scene,node,profile}=source(),before=structuredClone(project);
    const draft:NativePoseDraft={start:{x:640,y:380,width:200,height:130,rotation_deg:-18},
      waypoints:[{frame:90,curve,pose:{x:680,y:420,width:220,height:150,rotation_deg:9}}],
      endFrame:120,curve:"linear",fade:"in_out",fadeCurve:"linear",fadeEndFrame:30,fadeOutStartFrame:90,fadeOutEndFrame:150,fadeOutCurve:"ease_out_cubic"};
    const edit=nativePoseEdit(project,scene,node,profile,draft);
    expect(edit.issue).toBeNull();expect(edit.keys).toHaveLength(19);
    let saved=await applyChange(project,{type:"set_canvas_motion_keyframes",scene_id:scene.id,node_id:node.id,keyframes:edit.keys!});
    saved=await applyChange(saved,{type:"upsert_camera_reframe",reframe:camera(saved.scenes[0])});
    const actual=saved.scenes[0],withoutBeats={...actual,beats:[]};
    expect(actual.beats).toEqual(scene.beats);
    for(const time of [0,1,2-1/30,2,2+1/30,3,4-1/30,4,4+1/30,5,5.9]){
      expect(sampleProfileLayout(saved,actual,profile,time)).toEqual(sampleProfileLayout(saved,withoutBeats,profile,time));
      expect(previewTransform(actual.nodes[0],time).opacity).toBeGreaterThanOrEqual(0);
    }
    expect(previewTransform(actual.nodes[0],2).opacity).toBe(1);
    if(curve==="hold")expect(previewTransform(actual.nodes[0],2)).toMatchObject({...draft.start,opacity:1});
    const reopened=JSON.parse(JSON.stringify(saved));
    expect(sampleProfileLayout(reopened,reopened.scenes[0],profile,2)).toEqual(sampleProfileLayout(saved,actual,profile,2));
    expect(project).toEqual(before);
  });
  it("creates an editable contextual Follow label and preserves its offset across beat boundaries and camera",async()=>{
    const {project,scene,node,profile}=source();
    let saved=await applyChange(project,{type:"set_canvas_out_cubic_position_motion",scene_id:scene.id,node_id:node.id,
      deliverable_id:profile.id,start_x:520,start_y:380,end_frame:120});
    let current=saved.scenes[0],root=current.nodes[0];
    const label=contextLabelNode(current,root,blankLabel(root),profile);
    saved=await applyChange(saved,{type:"add_canvas_node",scene_id:scene.id,node:label});
    saved=await applyChange(saved,{type:"upsert_camera_reframe",reframe:camera(saved.scenes[0])});
    current=saved.scenes[0];root=current.nodes[0];
    for(const time of [1.99,2,2.01,3.99,4,4.01]){
      const sample=sampleProfileLayout(saved,current,profile,time).nodes;
      const a=sample.find(item=>item.id===root.id)!,b=sample.find(item=>item.id===label.id)!;
      expect(b.x-a.x).toBeCloseTo(label.x-root.x,9);expect(b.y-a.y).toBeCloseTo(label.y-root.y,9);
    }
    expect(current.nodes.find(item=>item.id===label.id)?.keyframes).toEqual([]);
    expect(current.nodes.find(item=>item.id===label.id)?.relations).toEqual(label.relations);
    expect(current.beats).toEqual(scene.beats);
  });
  it("attaches camera motion to a static scene with beats and keeps the saved scene clock",async()=>{
    const {project,scene,node}=source(),nodes=structuredClone(scene.nodes),rig=camera(scene);
    const saved=await applyChange(project,{type:"upsert_camera_reframe",reframe:rig});
    expect(saved.scenes[0].nodes).toEqual(nodes);
    expect(saved.scenes[0].beats).toEqual(scene.beats);
    expect(sampledSceneCamera(saved.scenes[0],saved.production_design?.camera_reframes,2).camera?.center_x).toBeCloseTo(972,10);
    expect(saved.scenes[0].nodes[0].id).toBe(node.id);
  });
  it("does not widen the independent Manim camera profile",async()=>{
    const {project,scene}=source();scene.renderer="manim-community";
    const before=structuredClone(project);
    await expect(applyChange(project,{type:"upsert_camera_reframe",reframe:{...camera(scene),component_version:CAMERA_REFRAME_MANIM_VERSION}})).rejects.toThrow();
    expect(project).toEqual(before);
  });
  it("retains source locks and rejects replacement of existing motion",async()=>{
    const {project,scene,node,profile}=source();
    const edit={type:"set_canvas_linear_position_motion" as const,scene_id:scene.id,node_id:node.id,deliverable_id:profile.id,start_x:640,start_y:380,end_frame:120};
    node.property_locks=["position"];
    await expect(applyChange(project,edit)).rejects.toThrow(/locked/);
    node.property_locks=[];const saved=await applyChange(project,edit);
    await expect(applyChange(saved,edit)).rejects.toThrow(/overwrites/);
    expect(saved.scenes[0].beats).toEqual(scene.beats);
  });
});
