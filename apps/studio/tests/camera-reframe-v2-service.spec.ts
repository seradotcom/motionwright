import { writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import type { CameraReframe } from "../src/cameraReframe";
import type { CanvasNode, Project } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "The current Rust/SQLite service binary is required.");
test.setTimeout(120_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const requests=(service:ScopedChangeService)=>service.transcript.filter(item=>
  item.direction==="request"&&"command" in item.payload&&item.payload.command==="apply_change");

test("saved Manim v2 Camera preserves its authored pair through raw recovery, two UI edits and SQLite reopen",async({page},info)=>{
  const service=await ScopedChangeService.create({
    databasePath:info.outputPath("store","camera-v2.sqlite3"),desktopInvokeFixture:true,
  });
  let latest:Project|undefined;
  try{
    const initial=(await service.fixtureBootstrap()).project;
    let source=await service.apply(initial,{type:"add_scene",name:"Editable planar vector camera",
      objective:"Retain two original vector objects through bounded camera edits",duration_seconds:3});
    const sceneId=source.scenes.find(scene=>!initial.scenes.some(old=>old.id===scene.id))!.id;
    const prototype=initial.scenes.flatMap(scene=>scene.nodes)[0];
    expect(prototype).toBeDefined();
    for(const [index,kind] of ["rectangle","circle"].entries()){
      const circle=kind==="circle";
      const node:CanvasNode={...structuredClone(prototype),id:randomUUID(),name:"Original vector "+index,
        kind,parent_id:null,x:circle?1050:400,y:circle?430:400,width:circle?160:220,height:circle?160:120,
        rotation_deg:0,opacity:1,text:null,coordinate_space:"project_pixels",z_index:index,
        style:{fill:"#86C8D1",stroke:null,stroke_width:0,blend_mode:"normal",
          font_family:null,font_size:null,font_weight:null,line_height:null},
        relations:[],property_locks:[],keyframes:[]};
      source=await service.apply(source,{type:"add_canvas_node",scene_id:sceneId,node});
    }
    source=await service.apply(source,{type:"set_scene_renderer",scene_id:sceneId,renderer:"manim-community"});
    const camera:CameraReframe={id:randomUUID(),scene_id:sceneId,component_version:2,
      start_center_x:960,start_center_y:540,end_center_x:1000,end_center_y:544,duration_frames:24};
    source=await service.apply(source,{type:"upsert_camera_reframe",reframe:camera});
    latest=source;
    const originalScene=structuredClone(source.scenes.find(scene=>scene.id===sceneId)!);
    const originalOtherScenes=structuredClone(source.scenes.filter(scene=>scene.id!==sceneId));
    // The backend's pair validation is observed separately from UI validation.
    await expect(service.apply(source,{type:"upsert_camera_reframe",
      reframe:{...camera,component_version:1}})).rejects.toThrow();
    expect(await service.project(source.id)).toEqual(source);
    const arrangedRequests=requests(service).length;

    await service.attachFixturePage(page,source.id);await page.goto("/");
    const open=async()=>{
      await button(page,"Production").click();
      await page.getByLabel("Production working scene",{exact:true}).selectOption({value:sceneId});
      await button(page,"Camera").click();
      await expect(page.getByRole("heading",{name:"Camera Reframe",exact:true})).toBeVisible();
    };
    const endX=page.getByLabel("End center X",{exact:true});
    const frames=page.getByLabel("Camera duration frames",{exact:true});
    const save=button(page,"Update camera reframe");
    await open();
    await expect(endX).toHaveValue("1000");
    await expect(save).toBeEnabled();
    await endX.fill("1e");await frames.fill("");
    await expect(save).toBeDisabled();
    await button(page,"Brief").click();await open();
    await expect(endX).toHaveValue("1e");await expect(frames).toHaveValue("");
    await page.reload();await open();
    await expect(endX).toHaveValue("1e");await expect(frames).toHaveValue("");
    expect(requests(service)).toHaveLength(arrangedRequests);
    await endX.fill("1012");await frames.fill("24");
    await expect(save).toBeEnabled();await save.click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+1));
    latest=await service.project(source.id);
    expect(latest.production_design?.camera_reframes?.find(value=>value.id===camera.id))
      .toEqual({...camera,end_center_x:1012});
    const scene=latest.scenes.find(value=>value.id===sceneId)!;
    expect(scene.renderer).toBe("manim-community");expect(scene.nodes).toEqual(originalScene.nodes);
    expect(scene.camera.center_x).toBe(1012);
    expect(latest.scenes.filter(value=>value.id!==sceneId)).toEqual(originalOtherScenes);

    await button(page,"Brief").click();await open();await frames.fill("30");
    await save.click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(source.revision+2));
    latest=await service.project(source.id);
    expect(latest.production_design?.camera_reframes?.find(value=>value.id===camera.id))
      .toEqual({...camera,end_center_x:1012,duration_frames:30});
    expect(latest.scenes.find(value=>value.id===sceneId)?.nodes).toEqual(originalScene.nodes);
    expect(latest.id).toBe(source.id);expect(latest.generation).toBe(source.generation);
    expect(latest.active_branch).toBe(source.active_branch);
    const journal=await service.history(source.id,source.revision);
    expect(journal.map(event=>event.change)).toEqual([
      {type:"upsert_camera_reframe",reframe:{...camera,end_center_x:1012}},
      {type:"upsert_camera_reframe",reframe:{...camera,end_center_x:1012,duration_frames:30}},
    ]);
    expect(requests(service)).toHaveLength(arrangedRequests+2);
    await service.close();await service.start();await page.reload();await open();
    await expect(endX).toHaveValue("1012");await expect(frames).toHaveValue("30");
    await expect(save).toBeEnabled();
    expect(await service.project(source.id)).toEqual(latest);
    expect(await service.history(source.id,source.revision)).toEqual(journal);
    expect(requests(service)).toHaveLength(arrangedRequests+2);
    await page.screenshot({path:info.outputPath("camera-v2-reopened.png"),fullPage:true});
    const evidence=info.outputPath("camera-v2-real-service-evidence.json");
    await writeFile(evidence,JSON.stringify({
      schema:"motionwright.camera-v2-service-evidence/1",
      evidenceKind:"real Studio browser, StudioService and SQLite",
      projectId:source.id,sceneId,cameraId:camera.id,componentVersion:2,renderer:"manim-community",
      initialRevision:source.revision,finalRevision:latest.revision,
      uiMutationCount:2,originalNodes:originalScene.nodes,journal,events:journal.map(event=>event.change.type),
      excludes:["native Manim pixels","Tauri installer","human approval"],
    },null,2));
    await info.attach("camera-v2-real-service-evidence",{path:evidence,contentType:"application/json"});
  }finally{
    try{
      const finalization=await service.finalizeClosedStore(info.outputPath("closed-camera-store"),latest,
        [],[()=>page.close()]);
      await writeFile(info.outputPath("camera-v2-finalization.json"),JSON.stringify(finalization,null,2));
    }finally{
      await writeFile(info.outputPath("camera-v2-transcript.json"),JSON.stringify({
        transcript:service.transcript,exits:service.processExits,taints:service.lifecycleTaints,
      },null,2));
    }
  }
});
