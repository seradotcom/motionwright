import { writeFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import type { Project } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A binary built from this exact Rust/SQLite source revision is required.");
test.setTimeout(120_000);
const button=(page:Page,name:string)=>page.getByRole("button",{name,exact:true});
const applyRequests=(service:ScopedChangeService)=>service.transcript.filter(item=>
  item.direction==="request" && "command" in item.payload &&
  item.payload.command==="apply_change");
const camera=(page:Page)=>page.getByLabel("End center X",{exact:true});

test("Camera reframe keeps raw input, commits twice and reopens exact SQLite Project",async({page},info)=>{
 const service=await ScopedChangeService.create({
  databasePath:info.outputPath("store","camera.sqlite3"),desktopInvokeFixture:true,
 });
 let latest:Project|undefined;
 try{
  const initial=(await service.fixtureBootstrap()).project;
  const scene=initial.scenes.find(item=>item.renderer==="motion-canvas" &&
    item.nodes.length>0 && item.nodes.length<=32 && item.beats.length===0 &&
    item.nodes.every(node=>node.keyframes.length===0 && node.parent_id===null&&node.kind!=="group") &&
    item.camera.zoom===1 && item.camera.rotation_deg===0);
  if(!scene)throw new Error("The real service Project has no eligible original planar Camera scene.");
  await service.attachFixturePage(page,initial.id);await page.goto("/");
  const open=async()=>{
    await button(page,"Production").click();
    await page.getByLabel("Production working scene",{exact:true}).selectOption({value:scene.id});
    await button(page,"Camera").click();
    await expect(page.getByRole("heading",{name:"Camera Reframe",exact:true})).toBeVisible();
  };
  await open();
  await camera(page).fill("1e");
  await page.getByLabel("Camera duration frames",{exact:true}).fill("");
  await expect(button(page,"Attach camera reframe")).toBeDisabled();
  await button(page,"Brief").click();await open();
  await expect(camera(page)).toHaveValue("1e");
  await expect(page.getByLabel("Camera duration frames",{exact:true})).toHaveValue("");
  await page.reload();await open();
  await expect(camera(page)).toHaveValue("1e");
  await expect(page.getByLabel("Camera duration frames",{exact:true})).toHaveValue("");
  expect(applyRequests(service)).toHaveLength(0);
  const firstX=scene.camera.center_x+12, secondX=scene.camera.center_x+19;
  await camera(page).fill(String(firstX));
  await page.getByLabel("Camera duration frames",{exact:true}).fill("90");
  await expect(button(page,"Attach camera reframe")).toBeEnabled();
  await button(page,"Attach camera reframe").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(initial.revision+1));
  latest=await service.project(initial.id);
  expect(latest.revision).toBe(initial.revision+1);
  const first=latest.production_design?.camera_reframes?.find(item=>item.scene_id===scene.id);
  expect(first?.end_center_x).toBe(firstX);
  expect(latest.scenes.find(item=>item.id===scene.id)?.camera.center_x).toBe(firstX);
  await button(page,"Brief").click();await open();
  await expect(camera(page)).toHaveValue(String(firstX));
  await camera(page).fill(String(secondX));
  await button(page,"Update camera reframe").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(initial.revision+2));
  latest=await service.project(initial.id);
  const next=latest.production_design?.camera_reframes?.find(item=>item.scene_id===scene.id);
  expect(next?.id).toBe(first?.id);
  expect(next?.end_center_x).toBe(secondX);
  const journal=await service.history(initial.id,initial.revision);
  expect(journal.map(item=>item.change.type)).toEqual(["upsert_camera_reframe","upsert_camera_reframe"]);
  expect(applyRequests(service)).toHaveLength(2);
  await service.close();await service.start();await page.reload();await open();
  await expect(camera(page)).toHaveValue(String(secondX));
  expect(await service.project(initial.id)).toEqual(latest);
  expect(await service.history(initial.id,initial.revision)).toEqual(journal);
  expect(applyRequests(service)).toHaveLength(2);
  const evidence=info.outputPath("camera-reframe-source-real-sqlite.json");
  await writeFile(evidence,JSON.stringify({
    source:"real StudioService + SQLite and authored browser Camera interaction",
    initialRevision:initial.revision,finalRevision:latest.revision,
    sceneId:scene.id,cameraId:next!.id,firstX,secondX,
    events:journal.map(item=>item.change.type),applyCount:2,
    excludes:["native Film pixels","installed app","human approval"],
  },null,2));
  await info.attach("camera-reframe-source-real-sqlite",{path:evidence,contentType:"application/json"});
 }finally{
  await service.finalizeClosedStore(info.outputPath("closed-camera-store"),latest,
    [],[()=>page.close()]);
 }
});

test("Camera unconfirmed response cannot replay a real SQLite mutation",async({page},info)=>{
 const service=await ScopedChangeService.create({
  databasePath:info.outputPath("store","camera-lost-reply.sqlite3"),desktopInvokeFixture:true,
 });
 let latest:Project|undefined;
 const original=service.invoke.bind(service);
 let lost=false;
 try{
  const initial=(await service.fixtureBootstrap()).project;
  const scene=initial.scenes.find(item=>item.renderer==="motion-canvas"&&item.nodes.length>0&&
    item.nodes.length<=32&&!item.beats.length&&
    item.nodes.every(node=>node.keyframes.length===0&&!node.parent_id&&node.kind!=="group"));
  if(!scene)throw new Error("No eligible authored Camera scene in real SQLite fixture.");
  await service.attachFixturePage(page,initial.id);await page.goto("/");
  await button(page,"Production").click();
  await page.getByLabel("Production working scene",{exact:true}).selectOption({value:scene.id});
  await button(page,"Camera").click();
  await expect(page.getByRole("heading",{name:"Camera Reframe"})).toBeVisible();
  service.invoke=async <T=unknown>(command:string,args:Record<string,unknown>={}):Promise<T>=>{
    const result=await original<T>(command,args);
    const change=(args.request as {change?:{type?:string}}|undefined)?.change;
    if(!lost&&command==="apply_change"&&change?.type==="upsert_camera_reframe"){
      lost=true;
      throw new Error("Transport lost its reply after real SQLite Camera commit");
    }
    return result;
  };
  const accepted=scene.camera.center_x+12;
  await camera(page).fill(String(accepted));
  await expect(button(page,"Attach camera reframe")).toBeEnabled();
  await button(page,"Attach camera reframe").click();
  await expect(page.getByRole("region",{name:"Unconfirmed Camera submission"})).toBeVisible();
  await expect(button(page,"Attach camera reframe")).toBeDisabled();
  latest=await service.project(initial.id);
  expect(latest.revision).toBe(initial.revision+1);
  expect(latest.production_design?.camera_reframes?.[0]?.end_center_x).toBe(accepted);
  const journal=await service.history(initial.id,initial.revision);
  expect(journal.map(item=>item.change.type)).toEqual(["upsert_camera_reframe"]);
  expect(applyRequests(service)).toHaveLength(1);
  await button(page,"Brief").click();await button(page,"Production").click();
  await button(page,"Camera").click();
  expect(applyRequests(service)).toHaveLength(1);
  await page.reload();await button(page,"Production").click();await button(page,"Camera").click();
  await expect(camera(page)).toHaveValue(String(accepted));
  expect(await service.project(initial.id)).toEqual(latest);
  expect(await service.history(initial.id,initial.revision)).toEqual(journal);
  expect(applyRequests(service)).toHaveLength(1);
 }finally{
  service.invoke=original;
  await service.finalizeClosedStore(info.outputPath("closed-camera-store"),latest,
    [],[()=>page.close()]);
 }
});
