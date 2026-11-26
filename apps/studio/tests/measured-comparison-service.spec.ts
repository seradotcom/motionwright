import {createHash,randomUUID} from "node:crypto";
import {mkdir,writeFile,unlink} from "node:fs/promises";
import {expect,test,type TestInfo} from "@playwright/test";
import type {Project} from "../src/types";
import {ScopedChangeService} from "./support/scopedChangeService";
import {comparisonNodes,comparisonRows,comparisonSceneName,openComparisonEditor,readComparisonDraft,
  refuseStaleComparison,reviewComparison,stageComparison} from "./support/measuredComparisonJourney";
import {proposalButton as button} from "./support/scopedPaintMotionJourney";
import {undoArrangement} from "./support/canvasArrangementJourney";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});

async function importRows(service:ScopedChangeService,info:TestInfo,project:Project,tableId:string,version:number,
  rows:ReturnType<typeof comparisonRows>){
  const directory=info.outputPath("comparison-sources");
  await mkdir(directory,{recursive:true});
  const path=info.outputPath("comparison-sources","source-v"+version+".json");
  const bytes=JSON.stringify(rows);
  await writeFile(path,bytes,{flag:"wx"});
  const imported=await service.importAsset(project,path,"Synthetic measured comparison v"+version+".json","application/json");
  const asset=imported.assets.find(item=>!project.assets.some(old=>old.id===item.id));
  expect(asset?.content_sha256).toBe(createHash("sha256").update(bytes).digest("hex"));
  // Prove subsequent table registration is bound to the retained CAS bytes,
  // not a second read of the owner's mutable file.
  await unlink(path);
  return service.apply(imported,{type:"upsert_measured_table",table:{schema:"motionwright.measured-table/1",
    id:tableId,label:"Synthetic comparison measurement",version,source_asset_id:asset!.id,
    source_sha256:asset!.content_sha256!,rows}});
}

test("Measured comparison uses real Service grants, rejects a recovered stale source, saves and reopens the exact comparison",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","measured-comparison.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    let project=(await service.fixtureBootstrap()).project;
    for(const lock of [...project.locks])project=await service.apply(project,{type:"remove_lock",lock_id:lock.id});
    const template=project.scenes[0].nodes[0],ids=Array.from({length:4},()=>randomUUID());
    const bindingIds=Array.from({length:4},()=>randomUUID()),tableId=randomUUID();
    project=await service.apply(project,{type:"add_scene",name:comparisonSceneName,objective:"Compare two controlled synthetic observations",duration_seconds:6});
    const sceneId=project.scenes.at(-1)!.id;
    for(const node of comparisonNodes(template,ids))project=await service.apply(project,{type:"add_canvas_node",scene_id:sceneId,node});
    project=await importRows(service,info,project,tableId,1,comparisonRows());
    for(let i=0;i<4;i++)project=await service.apply(project,{type:"upsert_measured_binding",
      binding_id:bindingIds[i],scene_id:sceneId,node_id:ids[i],table_id:tableId,datum_key:i<2?"before":"after",
      view:i%2===0?{kind:"label",decimals:1}:{kind:"bar",axis_min_milli:0,axis_max_milli:100000,max_width_px:800}});
    const base=project,baseScene=base.scenes.find(item=>item.id===sceneId)!;
    await service.attachFixturePage(page,base.id);
    await page.goto("/");
    await stageComparison(page,sceneId,bindingIds[0],bindingIds[2],true);
    await reviewComparison(page,ids,[200,480]);
    expect(await service.project(base.id)).toEqual(base);
    expect(await service.history(base.id,base.revision,8)).toEqual([]);
    const retained=await readComparisonDraft(page,base,sceneId);
    expect(retained.draft.recipeSource).toEqual({kind:"measured_comparison",revision:base.revision});

    // An actual source revision changes the unit but not the numeric bar width.
    const stale=await importRows(service,info,base,tableId,2,comparisonRows(60000,"count"));
    const staleScene=stale.scenes.find(item=>item.id===sceneId)!;
    expect(staleScene.nodes.map(node=>[node.id,node.x,node.y,node.width,node.height])).toEqual(
      baseScene.nodes.map(node=>[node.id,node.x,node.y,node.width,node.height]));
    expect(staleScene.nodes[2].text).not.toBe(baseScene.nodes[2].text);
    await service.close();await service.start();await page.reload();
    const discard=await refuseStaleComparison(page,sceneId,bindingIds[0],bindingIds[2]);
    expect((await readComparisonDraft(page,stale,sceneId)).draft).toEqual(retained.draft);
    await discard();
    expect(await service.project(base.id)).toEqual(stale);
    expect(await service.history(base.id,stale.revision,8)).toEqual([]);

    // Restore compatible measurement units and change the value on the same
    // saved table. A fresh recipe must project the new ratio, not the old one.
    const current=await importRows(service,info,stale,tableId,3,comparisonRows(75000));
    await page.reload();
    await stageComparison(page,sceneId,bindingIds[0],bindingIds[2]);
    await reviewComparison(page,ids,[200,600]);
    expect(await service.project(base.id)).toEqual(current);
    await button(page,"Apply as one revision").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(current.revision+1));
    const saved=await service.project(base.id),savedScene=saved.scenes.find(item=>item.id===sceneId)!;
    expect(saved.revision).toBe(current.revision+1);
    expect(savedScene.nodes.map(node=>node.x)).toEqual([240,240,240,240]);
    expect(savedScene.nodes.map(node=>node.width)).toEqual([800,200,800,600]);
    expect(saved.production_design?.measured_bindings).toEqual(current.production_design?.measured_bindings);
    expect(saved.production_design?.measured_tables).toEqual(current.production_design?.measured_tables);
    expect(saved.scenes.filter(item=>item.id!==sceneId)).toEqual(current.scenes.filter(item=>item.id!==sceneId));

    await service.close();await service.start();await page.reload();
    await openComparisonEditor(page,sceneId);
    expect(await service.project(base.id)).toEqual(saved);
    await expect(button(page,"Apply as one revision")).toBeDisabled();
    await undoArrangement(page,saved.revision+1);
    const final=await service.project(base.id);
    expect(final.scenes).toEqual(current.scenes);
    expect(final.production_design?.measured_bindings).toEqual(current.production_design?.measured_bindings);
    expect(final.production_design?.measured_tables).toEqual(current.production_design?.measured_tables);
    expect((await service.history(base.id,current.revision,8)).map(event=>event.change.type)).toEqual(["apply_creative_patch","undo_creative_patch"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[async()=>{await page.close();}]);
    finalized=true;
    expect(closure.oracle?.project).toEqual(final);
    expect(service.lifecycleTaints).toEqual([]);
  }finally{
    if(!page.isClosed())await page.close();
    if(!finalized)await service.close();
  }
});
