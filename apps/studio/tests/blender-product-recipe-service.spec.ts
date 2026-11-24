import { expect,test } from "@playwright/test";
import { mkdir,readFile } from "node:fs/promises";
import { dirname } from "node:path";
import { createHash } from "node:crypto";
import { ScopedChangeService } from "./support/scopedChangeService";
import { proposalButton as button } from "./support/scopedPaintMotionJourney";
import { authorBlenderRecipe,downloadBlenderRecipe,openBlenderRecipe,recipePanel,sourcePanel } from "./support/blenderRecipeJourney";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService with native source save commands is required.");
test.describe.configure({timeout:180_000,retries:0});
test("Studio-authored Blender recipe becomes one immutable source capsule and reopens",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","blender-recipe.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();
    let prepared=await service.apply(boot.project,{type:"add_scene",name:"Dedicated product recipe",objective:"Owned recipe source",duration_seconds:40});
    const scene=prepared.scenes.at(-1)!;
    prepared=await service.apply(prepared,{type:"set_scene_renderer",scene_id:scene.id,renderer:"blender"});
    await service.attachFixturePage(page,prepared.id);await page.goto("/");await openBlenderRecipe(page,scene.id);
    await authorBlenderRecipe(page);
    const downloaded=await downloadBlenderRecipe(page);
    const path=info.outputPath("owned-source",downloaded.suggestedFilename());
    await mkdir(dirname(path),{recursive:true});await downloaded.saveAs(path);
    const bytes=await readFile(path),digest=createHash("sha256").update(bytes).digest("hex");
    expect(JSON.parse(bytes.toString("utf8"))).toMatchObject({version:1,namespace:"StudioVessel",camera_curve:"linear"});
    await sourcePanel(page).getByLabel("Source file path",{exact:true}).fill(path);
    await button(page,"Save source and capsule").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(prepared.revision+1));
    const saved=await service.project(prepared.id),capsules=saved.production_design!.capsules.filter(c=>c.scene_id===scene.id);
    expect(capsules).toHaveLength(1);const capsule=capsules[0];
    expect(capsule.source_sha256).toBe(digest);
    expect(capsule.fidelity).toMatchObject({visual:"unavailable",temporal:"unavailable",structural:"unavailable",editable:"unavailable",evidence_sha256:null});
    expect(saved.assets.find(a=>a.id===capsule.source_asset_id)).toMatchObject({name:downloaded.suggestedFilename(),content_sha256:digest,media_type:"application/vnd.motionwright.blender-product+json"});
    await expect(recipePanel(page)).toContainText("Saved source metadata matches the prepared recipe digest.");
    expect((await service.history(prepared.id,prepared.revision,8)).map(event=>event.change.type)).toEqual(["import_native_capsule_source"]);
    await service.close();await service.start();await page.reload();await openBlenderRecipe(page,scene.id);
    await expect(recipePanel(page).getByLabel("Product coating",{exact:true})).toHaveValue("graphite");
    await expect(recipePanel(page)).toContainText("Saved source metadata matches the prepared recipe digest.");
    expect(await service.project(prepared.id)).toEqual(saved);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),saved,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(saved);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
