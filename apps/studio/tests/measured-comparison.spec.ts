import {createHash} from "node:crypto";
import {expect,test} from "@playwright/test";
import {fixtureProject} from "../src/fixture";
import {emptyProductionDesign} from "../src/creativeProduction";
import {applyMeasuredBinding,applyMeasuredTable} from "../src/measuredData";
import {proposalButton as button,readStudioProject} from "./support/scopedPaintMotionJourney";
import {undoArrangement} from "./support/canvasArrangementJourney";
import type {CanvasNode} from "../src/types";
import {comparisonRows,readComparisonDraft,refuseStaleComparison,reviewComparison,stageComparison} from "./support/measuredComparisonJourney";

const uuid=(n:number)=>"00000000-0000-4000-8000-"+String(n).padStart(12,"0");
function comparisonFixture(){
  const project=structuredClone(fixtureProject);project.schema_version=2;project.locks=[];project.reviews=[];project.branch_workspaces=[];project.production_design=emptyProductionDesign();
  const scene=project.scenes[0];scene.renderer="motion-canvas";scene.status="draft";scene.beats=[];scene.camera={center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  const template=scene.nodes[0];
  scene.nodes=Array.from({length:4},(_,i):CanvasNode=>({...template,id:uuid(800+i),name:"Original bound state "+i,
    kind:i%2===0?"text":"rectangle",x:320+i*20,y:300+i*20,width:800,height:100,parent_id:null,rotation_deg:0,opacity:1,text:i%2===0?"Placeholder":null,
    coordinate_space:"project_pixels",z_index:i,
    style:{...template.style,fill:i%2===0?"#F2F4F3":"#86C8D1",stroke:null,stroke_width:0,
      font_family:i%2===0?"Instrument Sans Variable":null,font_size:i%2===0?48:null,font_weight:i%2===0?400:null,line_height:i%2===0?1.12:null,blend_mode:"normal"},
    property_locks:[],relations:[],keyframes:[]}));
  project.deliverables=project.deliverables.map(profile=>({...profile,included_scene_ids:[],protected_scene_ids:[],text_overrides:{}}));
  const rows=[{key:"before",label:"Before",milli:25000,unit:"percent" as const},{key:"after",label:"After",milli:60000,unit:"percent" as const}];
  const digest=createHash("sha256").update(JSON.stringify(rows)).digest("hex"),assetId=uuid(810),tableId=uuid(820);
  project.assets.push({id:assetId,name:"Synthetic source rows.json",media_type:"application/json",content_sha256:digest,source_revision:null});
  applyMeasuredTable(project,{schema:"motionwright.measured-table/1",id:tableId,label:"Synthetic measured UI fixture",version:1,source_asset_id:assetId,source_sha256:digest,rows});
  for(let i=0;i<4;i++)applyMeasuredBinding(project,{binding_id:uuid(830+i),scene_id:scene.id,node_id:scene.nodes[i].id,table_id:tableId,datum_key:i<2?"before":"after",
    view:i%2===0?{kind:"label",decimals:1}:{kind:"bar",axis_min_milli:0,axis_max_milli:100000,max_width_px:800}});
  return {project,scene,rows,tableId,assetId};
}

test("measured comparison stages and saves the exact source-bound bar ratios as one reversible layout",async({page})=>{
  const {project,scene}=comparisonFixture();
  await page.route("**/src/fixture.ts",async route=>{
    const response=await route.fetch();await route.fulfill({response,body:(await response.text())+"\nfixtureBootstrap.project = "+JSON.stringify(project)+";\n"});
  });
  await page.goto("/");
  await button(page,"Production").click();await page.getByLabel("Production working scene").selectOption(scene.id);
  const base=await readStudioProject(page);
  await button(page,"Craft library").click();await page.getByLabel("Craft family").selectOption("data");
  await page.getByRole("button",{name:/^Measured state comparison/}).click();await button(page,"Open authoring editor").click();
  await page.getByText("Measured state comparison",{exact:true}).click();
  await page.getByLabel("Before state",{exact:true}).selectOption(uuid(830));
  await page.getByLabel("After state",{exact:true}).selectOption(uuid(832));
  await expect(button(page,"Stage measured comparison")).toBeEnabled();
  await expect(page.getByText(/Common axis: 0 to 100 percent/)).toBeVisible();
  await button(page,"Stage measured comparison").click();
  await expect(page.getByRole("region",{name:"Proposed edits",exact:true}).getByLabel(/Remove proposed edit/)).toHaveCount(4);
  expect(await readStudioProject(page)).toEqual(base);
  await button(page,"Preview scoped changes").click();await expect(page.getByText("Editorial diff prepared",{exact:true})).toBeVisible();
  const after=page.getByRole("img",{name:"After scoped changes",exact:true});
  for(const [i,width] of [[1,"200"],[3,"480"]] as const){
    await expect(after.locator('[data-canvas-node-id="'+scene.nodes[i].id+'"] rect')).toHaveAttribute("x","240");
    await expect(after.locator('[data-canvas-node-id="'+scene.nodes[i].id+'"] rect')).toHaveAttribute("width",width);
  }
  await button(page,"Apply as one revision").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(base.revision+1));
  const saved=await readStudioProject(page);
  expect(saved.production_design?.measured_bindings).toEqual(base.production_design?.measured_bindings);
  expect(saved.scenes.find(item=>item.id===scene.id)!.nodes.map(node=>node.x)).toEqual([240,240,240,240]);
  await undoArrangement(page,base.revision+2);
  expect((await readStudioProject(page)).scenes.find(item=>item.id===scene.id)!.nodes).toEqual(base.scenes.find(item=>item.id===scene.id)!.nodes);
});

test("a retained measured comparison refuses a changed source unit after browser recovery",async({page})=>{
  const {project,scene,tableId}=comparisonFixture();
  const updatedRows=comparisonRows(60000,"count"),bytes=JSON.stringify(updatedRows);
  const nextAssetId=uuid(811);
  project.assets.push({id:nextAssetId,name:"Synthetic source rows v2.json",media_type:"application/json",
    content_sha256:createHash("sha256").update(bytes).digest("hex"),source_revision:null});
  await page.route("**/src/fixture.ts",async route=>{
    const response=await route.fetch();
    await route.fulfill({response,body:(await response.text())+"\nfixtureBootstrap.project = "+JSON.stringify(project)+";\n"});
  });
  await page.goto("/");
  const base=await readStudioProject(page);
  await stageComparison(page,scene.id,uuid(830),uuid(832),true);
  await reviewComparison(page,scene.nodes.map(node=>node.id),[200,480]);
  const retained=await readComparisonDraft(page,base,scene.id);
  expect(retained.draft.recipeSource).toEqual({kind:"measured_comparison",revision:base.revision});
  expect(await readStudioProject(page)).toEqual(base);
  await button(page,"Measured data").click();
  await page.getByLabel("Measured table",{exact:true}).selectOption(tableId);
  await page.getByLabel("Measured source asset",{exact:true}).selectOption(nextAssetId);
  await page.getByLabel("Measured source file verification",{exact:true}).setInputFiles({
    name:"source-v2.json",mimeType:"application/json",buffer:Buffer.from(bytes)});
  const commit=button(page,"Commit next measured source revision");
  await expect(commit).toBeEnabled();
  await commit.click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(base.revision+1));
  const changed=await readStudioProject(page);
  expect(changed.production_design?.measured_tables?.[0].version).toBe(2);
  expect(changed.scenes[0].nodes.map(node=>[node.id,node.width])).toEqual(base.scenes[0].nodes.map(node=>[node.id,node.width]));
  expect(changed.scenes[0].nodes[2].text).not.toBe(base.scenes[0].nodes[2].text);
  // Demo reload restores the exact observed saved document, not a fabricated
  // successful patch receipt. The original local draft survives independently.
  await page.unroute("**/src/fixture.ts");
  await page.route("**/src/fixture.ts",async route=>{
    const response=await route.fetch();
    await route.fulfill({response,body:(await response.text())+"\nfixtureBootstrap.project = "+JSON.stringify(changed)+";\n"});
  });
  await page.reload();
  const discard=await refuseStaleComparison(page,scene.id,uuid(830),uuid(832));
  expect((await readComparisonDraft(page,changed,scene.id)).draft).toEqual(retained.draft);
  await discard();
  expect(await readStudioProject(page)).toEqual(changed);
});
