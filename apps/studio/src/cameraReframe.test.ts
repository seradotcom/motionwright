import {describe,expect,it} from "vitest";
import {applyChange} from "./api";
import {emptyProductionDesign} from "./creativeProduction";
import {fixtureProject} from "./fixture";
import {rationalSeconds} from "./types";
import {cameraPreviewOffset,validateCameraReframe} from "./cameraReframe";
import type {CameraReframe} from "./cameraReframe";
import type {Project} from "./types";

function source():{project:Project;camera:CameraReframe}{
  const project=structuredClone(fixtureProject);
  project.schema_version=2;
  project.branch_workspaces=[];project.reviews=[];project.merges=[];project.locks=[];
  project.production_design=emptyProductionDesign();
  project.scenes=project.scenes.slice(0,1);
  const scene=project.scenes[0];
  scene.renderer="motion-canvas";scene.status="draft";scene.beats=[];
  scene.start=rationalSeconds(0);scene.duration=rationalSeconds(6);
  scene.camera={center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  const node=structuredClone(fixtureProject.scenes[0].nodes[0]);
  node.id=crypto.randomUUID();node.parent_id=null;node.kind="rectangle";node.text=null;
  node.x=440;node.y=420;node.width=260;node.height=92;node.z_index=0;
  node.keyframes=[];node.relations=[];node.property_locks=[];
  node.style={...node.style,fill:"#86C8D1"};
  scene.nodes=[node];
  const camera:CameraReframe={
    id:"00000000-0000-4000-8000-000000000203",scene_id:scene.id,
    component_version:1,start_center_x:960,start_center_y:540,
    end_center_x:978,end_center_y:548,duration_frames:90,
  };
  return {project,camera};
}
describe("source-authoritative planar camera reframe",()=>{
  it("prioritizes invalid authored coordinates while still rejecting a source scene without original subjects",()=>{
    const {project,camera}=source();
    project.scenes[0].nodes=[];
    expect(()=>validateCameraReframe({...camera,end_center_x:1450},project.scenes[0]))
      .toThrow(/end x is outside the admitted source canvas/);
    expect(()=>validateCameraReframe(camera,project.scenes[0]))
      .toThrow(/original, ungrouped and motion-free Canvas shot/);
  });
  it("updates original camera only, preserving all Canvas node identities, layers and camera-safe dimensions",async()=>{
    const {project,camera}=source();
    const nodes=structuredClone(project.scenes[0].nodes);
    const p=await applyChange(project,{type:"upsert_camera_reframe",reframe:camera});
    expect(p.production_design?.camera_reframes).toEqual([camera]);
    expect(p.scenes[0].camera.center_x).toBe(978);
    expect(p.scenes[0].nodes).toEqual(nodes);
    expect(p.revision).toBe(project.revision+1);
    expect(cameraPreviewOffset(camera,0)).toEqual({x:0,y:0});
    expect(cameraPreviewOffset(camera,3)).toEqual({x:-18,y:-8});
    expect(cameraPreviewOffset(camera,6)).toEqual({x:-18,y:-8});
    expect(JSON.parse(JSON.stringify(p)).production_design.camera_reframes).toEqual([camera]);
    const detached=await applyChange(p,{type:"detach_camera_reframe",id:camera.id});
    expect(detached.production_design?.camera_reframes).toEqual([]);
    expect(detached.scenes[0].nodes).toEqual(nodes);
    expect(detached.scenes[0].camera.center_x).toBe(978);
  });
  it("rejects optical zoom, unsafe crops, nonnative frame rates and loss of source identity",async()=>{
    const {project,camera}=source();
    const rejections=[
      {...camera,end_center_x:1150},
      {...camera,end_center_x:camera.start_center_x,end_center_y:camera.start_center_y},
      {...camera,duration_frames:1},
      {...camera,duration_frames:181},
      {...camera,third_party_camera_script:"run"} as unknown as CameraReframe,
    ];
    for(const bad of rejections)
      await expect(applyChange(project,{type:"upsert_camera_reframe",reframe:bad}))
        .rejects.toThrow(/CameraReframe/);
    const zoom=source();zoom.project.scenes[0].camera.zoom=1.2;
    await expect(applyChange(zoom.project,{type:"upsert_camera_reframe",reframe:zoom.camera}))
      .rejects.toThrow(/optical zoom/);
    const cropped=source();cropped.project.scenes[0].nodes[0].x=85;
    await expect(applyChange(cropped.project,{type:"upsert_camera_reframe",reframe:cropped.camera}))
      .rejects.toThrow(/cropped/);
    expect(project.production_design?.camera_reframes).toEqual([]);
  });
  it("does not silently override source subject keyframes or scene position locks",async()=>{
    const {project,camera}=source();
    project.scenes[0].nodes[0].keyframes=[{
      at:rationalSeconds(0),property:"x",value:440,interpolation:"hold",
    }];
    await expect(applyChange(project,{type:"upsert_camera_reframe",reframe:camera}))
      .rejects.toThrow(/motion-free/);
    project.scenes[0].nodes[0].keyframes=[];
    project.locks.push({
      id:crypto.randomUUID(),resource:"scene:"+project.scenes[0].id,
      kind:"position",note:"Owner camera authority",
    });
    await expect(applyChange(project,{type:"upsert_camera_reframe",reframe:camera}))
      .rejects.toThrow(/locked/);
  });
  it("refuses duplicate moves on the same semantic scene and retained old-camera metadata",async()=>{
    const {project,camera}=source();
    const p=await applyChange(project,{type:"upsert_camera_reframe",reframe:camera});
    await expect(applyChange(p,{type:"upsert_camera_reframe",reframe:{
      ...camera,id:crypto.randomUUID(),
    }})).rejects.toThrow(/one camera/);
    const old=source();
    validateCameraReframe(old.camera,{...old.project.scenes[0],camera:{
      ...old.project.scenes[0].camera,
      center_x:old.camera.end_center_x,center_y:old.camera.end_center_y,
    }});
  });
});

// Closed V2 consumer and local draft continuity; these are not native pixels.
import { CAMERA_REFRAME_VERSION, CAMERA_REFRAME_MANIM_VERSION, applyCameraReframeChange,
  cameraDraftEdited, cameraDraftKey, cameraDraftMove, cameraReframePose, cameraReframeVersion,
  cameraSceneFrameCount, cameraSourceSignature, createCameraDraft, decodeCameraDraft, manimCameraRenderNotes,
  newCameraReframe, rebaseCameraDraft, reconcileCameraDraft, sameCameraReframe } from "./cameraReframe";
import type { CanvasNode, RendererKind, Scene } from "./types";
function manimSource(){
  const value=source(),s=value.project.scenes[0];s.renderer="manim-community";s.duration=rationalSeconds(1);
  const n=s.nodes[0];n.kind="rectangle";n.coordinate_space="project_pixels";n.opacity=1;n.rotation_deg=0;
  n.x=420;n.y=270;n.width=270;n.height=180;
  n.style={fill:"#FF0000",stroke:null,stroke_width:0,blend_mode:"normal",font_family:null,font_size:null,font_weight:null,line_height:null};
  const c=structuredClone(n);c.id=crypto.randomUUID();c.kind="circle";c.x=1125;c.y=540;c.width=180;c.height=180;c.style.fill="#00FF00";s.nodes.push(c);
  value.camera={...value.camera,component_version:2,end_center_x:1000,end_center_y:520,duration_frames:24};
  s.camera.center_x=1000;s.camera.center_y=520;value.project.audio.cues=[];value.project.audio.sfx_cues=[];
  value.project.visual_language.palette=[{name:"background",value:"#000000"}];return value;
}
describe("closed Manim camera V2",()=>{
  it("retains V1 and eight fields, admitting only the closed renderer/version pairs",()=>{
    expect(CAMERA_REFRAME_VERSION).toBe(1);expect(CAMERA_REFRAME_MANIM_VERSION).toBe(2);
    for(const renderer of ["motion-canvas","manim-community","mlt","blender","remotion","manim-gl"] as RendererKind[])
      for(const version of [0,1,2,3]){
        const {project,camera}=manimSource();project.scenes[0].renderer=renderer;camera.component_version=version;
        const check=()=>validateCameraReframe(camera,project.scenes[0]);
        if((renderer==="motion-canvas"&&version===1)||(renderer==="manim-community"&&version===2))expect(check).not.toThrow();else expect(check).toThrow();
      }
    expect(cameraReframeVersion("manim-gl")).toBeNull();expect(Object.keys(manimSource().camera)).toHaveLength(8);
  });
  const loss:[string,(n:CanvasNode)=>void][]=[
    ["text kind",n=>{n.kind="text";}],["coordinates",n=>{n.coordinate_space="normalized";}],
    ["relations",n=>{n.relations=[{id:crypto.randomUUID(),kind:"follow",target_id:crypto.randomUUID()}];}],
    ["opacity",n=>{n.opacity=.99;}],["rotation",n=>{n.rotation_deg=.01;}],["text payload",n=>{n.text="Human copy";}],
    ["stroke",n=>{n.style.stroke="#FFFFFF";}],["stroke width",n=>{n.style.stroke_width=1;}],
    ["blend",n=>{n.style.blend_mode="multiply";}],["font family",n=>{n.style.font_family="Inter";}],
    ["font size",n=>{n.style.font_size=18;}],["font weight",n=>{n.style.font_weight=400;}],
    ["line height",n=>{n.style.line_height=1.2;}],["ellipse",n=>{n.kind="circle";}],
    ["named fill",n=>{n.style.fill="red";}],["alpha fill",n=>{n.style.fill="#FF0000FF";}],
    ["missing fill",n=>{n.style.fill=null;}],["short fill",n=>{n.style.fill="#F00";}],
    ["keyframes",n=>{n.keyframes=[{at:rationalSeconds(0),property:"x",value:420,interpolation:"linear"}];}],
    ["parent",n=>{n.parent_id=crypto.randomUUID();}],
  ];
  it.each(loss)("rejects %s without partially mutating the scene",(_label,mutate)=>{
    const {project,camera}=manimSource();mutate(project.scenes[0].nodes[0]);const before=structuredClone(project);
    expect(()=>applyCameraReframeChange(project,{type:"upsert_camera_reframe",reframe:camera})).toThrow();expect(project).toEqual(before);
  });
  it("rejects unsafe, nonfinite, too short and unknown sources atomically",()=>{
    for(const mutate of [(s:Scene)=>{s.camera.safe_margin=.049;},(s:Scene)=>{s.camera.safe_margin=NaN;},
      (s:Scene)=>{s.nodes[0].width=Infinity;},(s:Scene)=>{s.nodes[0].x=95;},
      (s:Scene)=>{s.nodes=Array.from({length:33},()=>({...s.nodes[0],id:crypto.randomUUID()}));},
      (s:Scene)=>{s.duration={num:"38399",den:"48000"};}]){
      const {project,camera}=manimSource();mutate(project.scenes[0]);const before=structuredClone(project);
      expect(()=>applyCameraReframeChange(project,{type:"upsert_camera_reframe",reframe:camera})).toThrow();expect(project).toEqual(before);
    }
    const {project,camera}=manimSource();
    expect(()=>applyCameraReframeChange(project,{type:"upsert_camera_reframe",reframe:{...camera,renderer:"manim-community"} as CameraReframe})).toThrow(/unknown/);
    expect(()=>applyCameraReframeChange(project,{type:"upsert_camera_reframe",reframe:{...camera,scene_id:crypto.randomUUID()}})).toThrow(/scene not found/);
  });
  it("preserves object identities across ten camera updates and detach",async()=>{
    let {project,camera}=manimSource();const nodes=structuredClone(project.scenes[0].nodes);
    for(let step=0;step<10;step++){
      camera={...camera,end_center_x:1000+step};const before=project.revision;
      project=await applyChange(project,{type:"upsert_camera_reframe",reframe:camera});
      expect(project.revision).toBe(before+1);expect(project.production_design?.camera_reframes).toEqual([camera]);
      expect(project.scenes[0].nodes).toEqual(nodes);expect(project.scenes[0].camera.center_x).toBe(1000+step);
    }
    project=await applyChange(project,{type:"detach_camera_reframe",id:camera.id});
    expect(project.production_design?.camera_reframes).toEqual([]);expect(project.scenes[0].camera.center_x).toBe(1009);expect(project.scenes[0].nodes).toEqual(nodes);
  });
  it("preserves subpixels and distinguishes exact duration from one sample short",()=>{
    const {project,camera}=manimSource(),s=project.scenes[0];camera.end_center_x=1000.125;s.camera.center_x=1000.125;
    s.duration={num:"4",den:"5"};expect(()=>validateCameraReframe(camera,s)).not.toThrow();expect(cameraSceneFrameCount(s)).toBe(24);
    s.duration={num:"38399",den:"48000"};expect(()=>validateCameraReframe(camera,s)).toThrow(/exact frames/);expect(cameraSceneFrameCount(s)).toBeNull();
  });
  it("does not produce a false sample from invalid data",()=>{
    const {camera}=manimSource();for(const t of [NaN,Infinity,-Infinity])expect(cameraReframePose(camera,t)).toBeNull();
    expect(cameraReframePose({...camera,duration_frames:0},0)).toBeNull();expect(cameraReframePose({...camera,component_version:3},0)).toBeNull();
    expect(()=>cameraPreviewOffset({...camera,duration_frames:NaN},0)).toThrow(/cannot preview/);
  });
  it("reports native source limits separately from domain intent",()=>{
    const {project,camera}=manimSource(),s=project.scenes[0];
    const p={...project.deliverables[0],width:640,height:360,frame_rate:{num:"30",den:"1"},container:"mp4" as const,video_codec:"h264" as const,color_space:"rec709" as const,included_scene_ids:[]};
    expect(manimCameraRenderNotes(project,s,p)).toEqual([]);
    expect(manimCameraRenderNotes(project,s,{...p,width:1920})[0]).toMatch(/640 × 360/);
    expect(manimCameraRenderNotes(project,s,{...p,included_scene_ids:[crypto.randomUUID()]})).toEqual(["The selected profile excludes this scene."]);
    project.visual_language.palette=[{name:"surface",value:"#000000"},{name:"background",value:"#FFFFFF"}];
    expect(manimCameraRenderNotes(project,s,p)).toContain("The Manim contribution requires an authored opaque black background (#000000).");expect(()=>validateCameraReframe(camera,s)).not.toThrow();
  });
});
describe("camera draft continuity",()=>{
  it("retains equivalent numeric input without inventing an unsaved creative difference",()=>{
    const {project,camera}=manimSource(),s=project.scenes[0];project.production_design!.camera_reframes=[camera];
    const d=createCameraDraft(project,s);d.fields.end_center_x="1.0e3";d.fields.duration_frames="24.0";d.fields.start_center_x="+960";
    expect(cameraDraftMove(d)).toEqual(camera);expect(cameraDraftEdited(d)).toBe(false);
    expect(decodeCameraDraft(JSON.stringify(d),project,s).fields).toEqual(d.fields);
  });
  it("creates fitting V2 drafts and round-trips the local envelope",()=>{
    const {project}=manimSource(),s=project.scenes[0],move=newCameraReframe(s);
    expect(move.component_version).toBe(2);expect(move.duration_frames).toBe(30);expect(Object.keys(move)).toHaveLength(8);
    const d=createCameraDraft(project,s);expect(decodeCameraDraft(JSON.stringify(d),project,s)).toEqual(d);
  });
  it("preserves invalid text without coercing it to a camera change",()=>{
    const {project}=manimSource(),s=project.scenes[0];
    for(const input of [""," ","-","not-a-number","Infinity","0x3e8","1e999"]){
      const d=createCameraDraft(project,s);d.fields.end_center_x=input;
      const r=decodeCameraDraft(JSON.stringify(d),project,s);expect(r.fields.end_center_x).toBe(input);
      expect(()=>cameraDraftMove(r)).toThrow(/finite number/);expect(cameraDraftEdited(r)).toBe(true);
    }
  });
  it("rebases ten unrelated revisions without changing any entered string",()=>{
    const {project}=manimSource(),s=project.scenes[0];let d=createCameraDraft(project,s);d.fields.end_center_x="1012.125";d.fields.duration_frames="24";
    const fields=structuredClone(d.fields),id=d.base.id;
    for(let i=0;i<10;i++){project.title="Human revision "+i;project.revision++;d=reconcileCameraDraft(d,project,s);
      expect(d.base_revision).toBe(project.revision);expect(d.fields).toEqual(fields);expect(d.base.id).toBe(id);}
  });
  it("requires explicit conflict resolution, keeping values on the current camera identity",()=>{
    const {project,camera}=manimSource(),s=project.scenes[0];project.production_design!.camera_reframes=[camera];
    let d=createCameraDraft(project,s);d.fields.end_center_x="1012";s.nodes[0].x+=12;project.revision++;
    expect(reconcileCameraDraft(d,project,s)).toBe(d);
    const other={...camera,id:crypto.randomUUID(),end_center_x:1004};s.camera.center_x=1004;project.production_design!.camera_reframes=[other];project.revision++;
    d=rebaseCameraDraft(d,project,s);expect(d.base_revision).toBe(project.revision);expect(cameraDraftMove(d).id).toBe(other.id);expect(d.fields.end_center_x).toBe("1012");
  });
  it("recognizes reordered service ACK fields while preserving input typed after dispatch",()=>{
    const {project,camera}=manimSource(),s=project.scenes[0];project.production_design!.camera_reframes=[camera];
    let d=createCameraDraft(project,s);d.fields.end_center_x="1012";const sent=cameraDraftMove(d);
    const ack=Object.fromEntries(Object.entries(sent).sort(([a],[b])=>a.localeCompare(b))) as unknown as CameraReframe;
    expect(JSON.stringify(ack)).not.toBe(JSON.stringify(sent));expect(sameCameraReframe(ack,sent)).toBe(true);
    d.fields.end_center_x="1013.5";d.fields.duration_frames="";project.production_design!.camera_reframes=[ack];s.camera.center_x=1012;project.revision++;
    const sourceSignature=cameraSourceSignature(project,s);
    const another=structuredClone(project);another.scenes[0].nodes[0].x+=4;another.revision++;
    expect(reconcileCameraDraft(d,another,another.scenes[0],{move:sent,source_signature:sourceSignature})).toBe(d);
    d=reconcileCameraDraft(d,project,s,{move:sent,source_signature:sourceSignature});expect(d.base_revision).toBe(project.revision);expect(d.base).toEqual(ack);
    expect(d.fields.end_center_x).toBe("1013.5");expect(d.fields.duration_frames).toBe("");
  });
  it("isolates branches, project IDs, scene IDs and portable import generations",()=>{
    const {project}=manimSource(),s=project.scenes[0],d=createCameraDraft(project,s),raw=JSON.stringify(d),key=cameraDraftKey(project,s);
    for(const changed of [{...project,id:crypto.randomUUID()},{...project,generation:crypto.randomUUID()},{...project,active_branch:"alternate"}]){
      expect(cameraDraftKey(changed,s)).not.toBe(key);expect(()=>decodeCameraDraft(raw,changed,s)).toThrow();expect(()=>reconcileCameraDraft(d,changed,s)).toThrow(/identity changed/);
    }
    expect(()=>decodeCameraDraft(raw,project,{...s,id:crypto.randomUUID()})).toThrow();
    for(const bad of ["{",JSON.stringify({...d,extra:true}),JSON.stringify({...d,base_revision:-1}),JSON.stringify({...d,fields:{...d.fields,end_center_x:"x".repeat(65)}})])expect(()=>decodeCameraDraft(bad,project,s)).toThrow();
  });
});
