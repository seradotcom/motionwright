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


describe("Camera v2 client profile consistency",()=>{
  function manimSource(){
    const {project,camera}=source(),scene=project.scenes[0];
    scene.renderer="manim-community";
    const rectangle=scene.nodes[0];
    rectangle.x=400;rectangle.y=400;rectangle.width=220;rectangle.height=120;
    rectangle.rotation_deg=0;rectangle.opacity=1;rectangle.coordinate_space="project_pixels";
    rectangle.style={fill:"#86C8D1",stroke:null,stroke_width:0,blend_mode:"normal",
      font_family:null,font_size:null,font_weight:null,line_height:null};
    const circle=structuredClone(rectangle);circle.id=crypto.randomUUID();circle.kind="circle";
    circle.x=1050;circle.y=430;circle.width=160;circle.height=160;circle.z_index=1;
    scene.nodes=[rectangle,circle];
    camera.component_version=2;camera.end_center_x=1000;camera.end_center_y=544;camera.duration_frames=24;
    return {project,camera};
  }
  it("admits the existing Manim v2 pair without changing original vector shapes",async()=>{
    // The same two-shape bounds and camera values are admitted by the Rust domain.
    const {project,camera}=manimSource(),before=structuredClone(project);
    const saved=await applyChange(project,{type:"upsert_camera_reframe",reframe:camera});
    expect(saved.production_design?.camera_reframes).toEqual([camera]);
    expect(saved.scenes[0].nodes).toEqual(before.scenes[0].nodes);
    expect(saved.scenes[0].renderer).toBe("manim-community");
    expect(saved.id).toBe(before.id);expect(saved.active_branch).toBe(before.active_branch);
    expect(()=>validateCameraReframe(camera,saved.scenes[0])).not.toThrow();
    const edited=await applyChange(saved,{type:"upsert_camera_reframe",
      reframe:{...camera,end_center_x:1012}});
    expect(edited.production_design?.camera_reframes?.[0].component_version).toBe(2);
    expect(edited.scenes[0].nodes).toEqual(before.scenes[0].nodes);
    expect(project).toEqual(before);
  });
  it("rejects cross-version renderer pairs without inferring a migration",async()=>{
    for(const [version,renderer] of [[1,"manim-community"],[2,"motion-canvas"],[3,"manim-community"]] as const){
      const {project,camera}=manimSource();
      camera.component_version=version;project.scenes[0].renderer=renderer;
      const before=structuredClone(project);
      await expect(applyChange(project,{type:"upsert_camera_reframe",reframe:camera}))
        .rejects.toThrow(/CameraReframe/);
      expect(project).toEqual(before);
    }
  });
  it("rejects every unrepresented Manim shape feature without modifying authored source",async()=>{
    const mutators:Array<(project:Project)=>void>=[
      p=>{p.scenes[0].camera.safe_margin=.049;},
      p=>{p.scenes[0].nodes[0].kind="text";},
      p=>{p.scenes[0].nodes[1].height=159;},
      p=>{p.scenes[0].nodes[0].coordinate_space="normalized";},
      p=>{p.scenes[0].nodes[0].opacity=.7;},
      p=>{p.scenes[0].nodes[0].rotation_deg=5;},
      p=>{p.scenes[0].nodes[0].text="Retain this text";},
      p=>{p.scenes[0].nodes[0].style.stroke="#101010";},
      p=>{p.scenes[0].nodes[0].style.stroke_width=1;},
      p=>{p.scenes[0].nodes[0].style.blend_mode="multiply";},
      p=>{p.scenes[0].nodes[0].style.font_family="system";},
      p=>{p.scenes[0].nodes[0].style.font_size=14;},
      p=>{p.scenes[0].nodes[0].style.font_weight=700;},
      p=>{p.scenes[0].nodes[0].style.line_height=1.2;},
      p=>{p.scenes[0].nodes[0].style.fill=null;},
      p=>{p.scenes[0].nodes[0].style.fill="red";},
      p=>{p.scenes[0].nodes[0].style.fill="#86C8D180";},
      p=>{p.scenes[0].nodes[0].relations=[{id:crypto.randomUUID(),
        target_id:p.scenes[0].nodes[1].id,kind:"align_center_x"}];},
    ];
    for(const mutate of mutators){
      const {project,camera}=manimSource();mutate(project);
      const before=structuredClone(project);
      await expect(applyChange(project,{type:"upsert_camera_reframe",reframe:camera}))
        .rejects.toThrow(/CameraReframe/);
      expect(project).toEqual(before);
    }
  });
});
