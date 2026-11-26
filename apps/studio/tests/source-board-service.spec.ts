import {createHash,randomUUID} from "node:crypto";
import {mkdir,writeFile,unlink,readFile} from "node:fs/promises";
import {expect,test,type Page,type TestInfo} from "@playwright/test";
import type {Project} from "../src/types";
import type {CaptureSourceRecord} from "../src/captureSource";
import {ScopedChangeService} from "./support/scopedChangeService";
import {proposalButton as button} from "./support/scopedPaintMotionJourney";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});

const sha=(bytes:Buffer|string)=>createHash("sha256").update(bytes).digest("hex");
async function importOriginal(service:ScopedChangeService,project:Project,path:string,bytes:Buffer|string,mime:string){
  await writeFile(path,bytes,{flag:"wx"});
  const next=await service.importAsset(project,path,path.split(/[\\/]/).at(-1),mime);
  const asset=next.assets.find(item=>!project.assets.some(old=>old.id===item.id));
  expect(asset?.content_sha256).toBe(sha(bytes));
  await unlink(path);
  return {project:next,asset:asset!};
}
async function captureFixture(service:ScopedChangeService,project:Project,fixture:Page,info:TestInfo,index:number){
  // Execute the existing admitted first-party scenario on actual Studio
  // backed by this original Service. The fixture data remains synthetic.
  project=await service.apply(project,{type:"rename_project",
    title:index===0?"Source board before state":"Source board after state"});
  const observed=project.revision;
  const origin=new URL(info.project.use.baseURL!).origin;
  expect(origin).toBe("http://127.0.0.1:1420");
  const commit=process.env.GITHUB_SHA!;
  expect(commit).toMatch(/^[0-9a-f]{40}$/);
  const version=JSON.parse(await readFile("package.json","utf8")).version as string;
  await fixture.setViewportSize({width:1440,height:940});
  if(index===0)await service.attachFixturePage(fixture,project.id);
  const external:string[]=[];
  await fixture.route("**/*",async route=>{
    if(new URL(route.request().url()).origin!==origin){
      external.push(route.request().url());await route.abort("blockedbyclient");
    }else await route.continue();
  });
  await fixture.goto("/");
  await button(fixture,"Production").click();
  await expect(fixture.getByRole("heading",{name:"ProductHeroReveal",exact:true})).toBeVisible();
  await fixture.addStyleTag({content:"* { animation: none !important; transition: none !important; caret-color: transparent !important; }"});
  await fixture.evaluate(async()=>{await document.fonts.ready;});
  expect(await fixture.locator("input[type=password]:visible").count()).toBe(0);
  expect(await fixture.evaluate(()=>window.devicePixelRatio)).toBe(1);
  const chip=fixture.locator(".revision-chip");
  await expect(chip).toHaveText("r"+observed);
  const box=await chip.boundingBox();expect(box).not.toBeNull();
  const x=Math.floor(box!.x),y=Math.floor(box!.y);
  const mask={selector:".revision-chip",x,y,width:Math.ceil(box!.x+box!.width)-x,
    height:Math.ceil(box!.y+box!.height)-y,mask_color:"#0B0B0B"};
  const capture=()=>fixture.screenshot({scale:"css",animations:"disabled",caret:"hide",
    mask:[chip],maskColor:mask.mask_color,fullPage:false});
  const bytes=await capture();
  expect(sha(await capture())).toBe(sha(bytes));
  expect(external).toEqual([]);
  await fixture.unroute("**/*");
  const directory=info.outputPath("original-source-"+index);
  await mkdir(directory,{recursive:true});
  const image=await importOriginal(service,project,info.outputPath("original-source-"+index,"capture.png"),bytes,"image/png");
  project=await service.apply(image.project,{type:"add_claim",text:"Synthetic first-party source board fixture "+index,
    source:{kind:"project_revision",revision:observed},context:"synthetic-first-party-capture-validation",source_revision:"synthetic-board-test"});
  const claim=project.brief.claims.at(-1)!;
  const state=await importOriginal(service,project,info.outputPath("original-source-"+index,"fixture.json"),JSON.stringify({
    id:project.id,generation:project.generation,revision:observed,claims:[claim],
  }),"application/json");
  const screenshotSha=image.asset.content_sha256!,stateSha=state.asset.content_sha256!;
  const manifest=await importOriginal(service,state.project,info.outputPath("original-source-"+index,"manifest.json"),JSON.stringify({
    schema:"motionwright.first-party-capture-evidence/1",
    app:{id:"motionwright-studio",version,source_revision:commit},
    scenario:{id:"production-original-ui-local-v1",origin:"http://127.0.0.1:1420",route:"/",
      actions:["open:/","click:Production"],viewport:{width:1440,height:940,device_scale_factor:1},
      browser:"chromium",fixture_kind:"synthetic-first-party-data",auth_mode:"none",external_requests:0,credential_fields:0},
    claim_id:claim.id,fixture_state_sha256:stateSha,redactions:[mask],
    capture:{path:"capture.png",sha256:screenshotSha,repeated_sha256:screenshotSha,pixel_equal:true,redacted:true,
      raw_unredacted_saved:false,width:1440,height:940},
    review_status:"technical-evidence-only-human-review-required",
  }),"application/json");
  const record:CaptureSourceRecord={
    schema:"motionwright.capture-source/1",id:randomUUID(),screenshot_asset_id:image.asset.id,screenshot_sha256:screenshotSha,
    screenshot_repeat_sha256:screenshotSha,manifest_asset_id:manifest.asset.id,manifest_sha256:manifest.asset.content_sha256!,
    fixture_state_asset_id:state.asset.id,fixture_state_sha256:stateSha,claim_id:claim.id,
    source_project_id:project.id,source_generation:project.generation,source_revision:observed,
    app_id:"motionwright-studio",app_version:version,source_commit:commit,
    scenario_id:"production-original-ui-local-v1",origin:"http://127.0.0.1:1420",route:"/",
    viewport_width:1440,viewport_height:940,device_scale_factor:1,redaction:mask,external_requests:0,credential_fields:0,
    declared_scope:"first-party-synthetic-fixture-only",
  };
  return {project:await service.apply(manifest.project,{type:"register_capture_source",record}),record};
}
async function openBoard(page:Page,sceneId:string){
  await button(page,"Production").click();
  await page.getByLabel("Production working scene",{exact:true}).selectOption(sceneId);
  await button(page,"Source boards").click();
  return page.getByRole("region",{name:"Source board authoring",exact:true});
}

test("Two original CAS capture fixtures survive source board authoring, ordered reveal, SQLite reopen and precise removal",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","source-board.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  const fixture=await page.context().newPage();
  try{
    let project=(await service.fixtureBootstrap()).project;
    for(const lock of [...project.locks])project=await service.apply(project,{type:"remove_lock",lock_id:lock.id});
    project=await service.apply(project,{type:"add_scene",name:"Original source board journey",objective:"Editorial comparison of synthetic fixtures",duration_seconds:6});
    const sceneId=project.scenes.at(-1)!.id;
    const a=await captureFixture(service,project,fixture,info,0),b=await captureFixture(service,a.project,fixture,info,1);
    await fixture.close();
    expect(a.record.screenshot_sha256).not.toBe(b.record.screenshot_sha256);
    project=b.project;
    for(const profile of [...project.deliverables]){
      project=await service.apply(project,{type:"upsert_deliverable",profile:{...profile,framing_strategy:"replan",
        frame_rate:{num:"30",den:"1"},included_scene_ids:[sceneId],protected_scene_ids:[],text_overrides:{}}});
    }
    const base=project;
    await service.attachFixturePage(page,base.id);await page.goto("/");
    let editor=await openBoard(page,sceneId);
    for(const [i,source] of [a.record,b.record].entries()){
      const n=String(i+1);
      await editor.getByLabel("Board capture "+n,{exact:true}).selectOption(source.id);
      await editor.getByLabel("Board label "+n,{exact:true}).fill(i===0?"Initial state":"Later state");
      await editor.getByLabel("Board source permission "+n,{exact:true}).fill("Test author permits this original synthetic reference.");
    }
    const permission=editor.getByLabel("Board source permission 2",{exact:true});
    await editor.getByLabel("Board capture 2",{exact:true}).selectOption(a.record.id);
    await expect(permission).toHaveValue("");
    await expect(editor.getByRole("button",{name:"Add source board",exact:true})).toBeDisabled();
    await editor.getByLabel("Board capture 2",{exact:true}).selectOption(b.record.id);
    await permission.fill("Test author permits this original synthetic reference.");
    await expect(editor.getByRole("button",{name:"Add source board",exact:true})).toBeEnabled();
    expect(await service.project(base.id)).toEqual(base);
    await editor.getByRole("button",{name:"Add source board",exact:true}).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(base.revision+1));
    const saved=await service.project(base.id);
    const board=saved.production_design!.capture_boards![0],ids=saved.scenes.find(item=>item.id===sceneId)!.nodes.map(node=>node.id);
    expect(ids).toHaveLength(7);expect(board.mode).toBe("reference_board");
    expect(board.entries.map(entry=>entry.screenshot_sha256)).toEqual([a.record.screenshot_sha256,b.record.screenshot_sha256]);
    await editor.getByLabel("Source board mode",{exact:true}).selectOption("product_flow_bridge");
    await editor.getByLabel("Board reveal start frame",{exact:true}).fill("30");
    await editor.getByLabel("Board reveal duration frames",{exact:true}).fill("15");
    await expect(editor.getByRole("button",{name:"Update source board",exact:true})).toBeEnabled();
    await editor.getByRole("button",{name:"Update source board",exact:true}).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(saved.revision+1));
    const flow=await service.project(base.id),flowScene=flow.scenes.find(item=>item.id===sceneId)!;
    expect(flow.production_design?.capture_boards?.[0]).toMatchObject({id:board.id,mode:"product_flow_bridge",reveal_start_frame:30,reveal_duration_frames:15});
    expect(flowScene.nodes.map(node=>node.id)).toEqual(ids);
    expect(flowScene.nodes[5].keyframes.map(key=>key.value)).toEqual([0,0,1]);

    await service.close();await service.start();await page.reload();
    editor=await openBoard(page,sceneId);
    await expect(editor.getByLabel("Board capture 1",{exact:true})).toHaveValue(a.record.id);
    await expect(editor.getByLabel("Source board mode",{exact:true})).toHaveValue("product_flow_bridge");
    expect(await service.project(base.id)).toEqual(flow);
    await expect(editor.getByRole("button",{name:"Remove board and generated slots",exact:true})).toBeEnabled();
    await editor.getByRole("button",{name:"Remove board and generated slots",exact:true}).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(flow.revision+1));
    const final=await service.project(base.id);
    expect(final.scenes.find(item=>item.id===sceneId)?.nodes).toEqual([]);
    expect(final.production_design?.captures).toEqual(base.production_design?.captures);
    expect(final.assets).toEqual(base.assets);expect(final.brief.claims).toEqual(base.brief.claims);
    expect(final.scenes.filter(item=>item.id!==sceneId)).toEqual(base.scenes.filter(item=>item.id!==sceneId));
    expect((await service.history(base.id,base.revision,8)).map(event=>event.change.type)).toEqual(["upsert_capture_board","upsert_capture_board","remove_capture_board"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(final);expect(service.lifecycleTaints).toEqual([]);
  }finally{
    if(!fixture.isClosed())await fixture.close();
    if(!page.isClosed())await page.close();
    if(!finalized)await service.close();
  }
});
