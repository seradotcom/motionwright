import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";
import { fixtureProject } from "../src/fixture";
import { emptyProductionDesign } from "../src/creativeProduction";
import { applyMeasuredBinding, applyMeasuredTable } from "../src/measuredData";

const tableId="00000000-0000-4000-8000-000000000731",assetId="00000000-0000-4000-8000-000000000732";
const counterId="00000000-0000-4000-8000-000000000733",barId="00000000-0000-4000-8000-000000000734";
// Deliberate browser-only fixture. Real import, grants and SQLite are covered
// by creative-workbench-service.spec.ts, never claimed by this UI scenario.
test("measured output editing retains independent formats and explicit axes on the original objects",async({page},info)=>{
  const project=structuredClone(fixtureProject);project.schema_version=2;project.locks=[];project.production_design=emptyProductionDesign();
  const scene=project.scenes[0];scene.renderer="motion-canvas";scene.beats=[];
  scene.nodes=scene.nodes.slice(0,2).map((node,index)=>({...node,kind:index?"rectangle":"text",text:index?null:"Synthetic UI label",
    parent_id:null,property_locks:[],relations:[],keyframes:[],opacity:1}));
  const rows=[{key:"latency",label:"Synthetic latency",milli:12500,unit:"milliseconds" as const},
    {key:"duration",label:"Synthetic duration",milli:25000,unit:"milliseconds" as const}];
  const digest=createHash("sha256").update(JSON.stringify(rows)).digest("hex");
  project.assets.push({id:assetId,name:"Synthetic UI fixture rows.json",media_type:"application/json",content_sha256:digest,source_revision:null});
  applyMeasuredTable(project,{schema:"motionwright.measured-table/1",id:tableId,label:"Synthetic UI fixture",version:1,source_asset_id:assetId,source_sha256:digest,rows});
  for(const [binding_id,node_id,view] of [
    [counterId,scene.nodes[0].id,{kind:"counter" as const,decimals:2}],
    [barId,scene.nodes[1].id,{kind:"bar" as const,axis_min_milli:0,axis_max_milli:100000,max_width_px:800}],
  ] as const)applyMeasuredBinding(project,{binding_id,node_id,view,scene_id:scene.id,table_id:tableId,datum_key:"latency"});
  await page.route("**/src/fixture.ts",async route=>{
    const response=await route.fetch();
    await route.fulfill({response,body:(await response.text())+"\nfixtureBootstrap.project = "+JSON.stringify(project)+";\n"});
  });
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene",{exact:true}).selectOption(scene.id);
  await page.getByRole("button",{name:"Measured data",exact:true}).click();
  await page.getByLabel("Measured output to edit",{exact:true}).selectOption(counterId);
  await expect(page.getByLabel("Measured Canvas node",{exact:true})).toBeDisabled();
  await page.getByLabel("Measured output kind",{exact:true}).selectOption("label");
  await page.getByLabel("Measured datum row",{exact:true}).selectOption("duration");
  await page.getByLabel("Measured decimals",{exact:true}).fill("1e");
  await expect(page.getByRole("button",{name:"Update measured output",exact:true})).toBeDisabled();
  await page.getByLabel("Measured output to edit",{exact:true}).selectOption(barId);
  await page.getByLabel("Measured axis minimum",{exact:true}).fill("-");
  await page.getByLabel("Measured axis maximum",{exact:true}).fill("50000");
  await page.getByLabel("Measured maximum bar width",{exact:true}).fill("900");
  await page.getByLabel("Measured output to edit",{exact:true}).selectOption(counterId);
  await expect(page.getByLabel("Measured decimals",{exact:true})).toHaveValue("1e");
  await page.getByLabel("Measured decimals",{exact:true}).fill("3");
  await expect(page.getByLabel("Measured output comparison",{exact:true})).toContainText("Synthetic duration · 25.000 ms");
  await page.getByRole("button",{name:"Update measured output",exact:true}).click();
  await expect(page.getByRole("button",{name:"Update measured output",exact:true})).toBeDisabled();
  await page.getByLabel("Measured output to edit",{exact:true}).selectOption(barId);
  await expect(page.getByLabel("Measured axis minimum",{exact:true})).toHaveValue("-");
  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByRole("button",{name:"Measured data",exact:true}).click();
  await expect(page.getByLabel("Measured axis minimum",{exact:true})).toHaveValue("-");
  await page.getByLabel("Measured axis minimum",{exact:true}).fill("0");
  await page.getByLabel("Measured datum row",{exact:true}).selectOption("duration");
  await expect(page.getByLabel("Measured output comparison",{exact:true})).toContainText("450 px wide");
  await expect(page.getByRole("img",{name:"Measured output before · saved Canvas",exact:true})).toBeVisible();
  await expect(page.getByRole("img",{name:"Measured output after · proposed Canvas",exact:true})).toBeVisible();
  await page.getByRole("region",{name:"Measured output editorial study",exact:true}).screenshot({path:info.outputPath("measured-output-editorial-study.png")});
  await page.getByRole("button",{name:"Update measured output",exact:true}).click();
  await expect(page.getByRole("button",{name:"Update measured output",exact:true})).toBeDisabled();
  await expect(page.getByLabel("Measured output to edit",{exact:true})).toHaveValue(barId);
  await page.getByLabel("Measured maximum bar width",{exact:true}).fill("0");
  await expect(page.getByRole("button",{name:"Update measured output",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Use saved output",exact:true}).click();
  await expect(page.getByLabel("Measured maximum bar width",{exact:true})).toHaveValue("900");
  await page.screenshot({path:info.outputPath("measured-output-comparison.png"),fullPage:true});
  const row=page.getByRole("article",{name:"Measured binding "+barId,exact:true});
  await row.getByRole("button",{name:"Detach (keep Canvas)",exact:true}).click();
  await expect(page.getByText("This measured binding was removed.",{exact:false})).toBeVisible();
  await expect(page.getByRole("button",{name:"Update measured output",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Discard removed output draft",exact:true}).click();
  await expect(page.getByLabel("Measured output to edit",{exact:true})).toHaveValue("");
});
