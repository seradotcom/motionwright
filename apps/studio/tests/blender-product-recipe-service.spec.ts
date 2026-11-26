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


test("Spatial camera presets retain the original authored recipe and reopen both paths without Native dispatch",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","blender-spatial-recipe.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();
    let prepared=await service.apply(boot.project,{type:"add_scene",name:"Dedicated spatial product recipe",objective:"Original three-waypoint arc and fixed lens dolly",duration_seconds:40});
    const scene=prepared.scenes.at(-1)!;
    prepared=await service.apply(prepared,{type:"set_scene_renderer",scene_id:scene.id,renderer:"blender"});
    await service.attachFixturePage(page,prepared.id);await page.goto("/");
    await button(page,"Production").click();
    await page.getByRole("combobox",{name:"Production working scene",exact:true}).selectOption(scene.id);
    await button(page,"Craft library").click();
    const library=page.getByRole("region",{name:"Craft component library",exact:true});
    await library.getByLabel("Craft family").selectOption("camera");
    await library.getByRole("button",{name:/Arc reveal/}).click();
    await library.getByRole("button",{name:"Open authoring editor",exact:true}).click();
    let panel=recipePanel(page);
    await expect(panel.getByText(/Library choice: Arc reveal/)).toBeVisible();
    await authorBlenderRecipe(page);
    await panel.getByRole("button",{name:"Use Arc reveal",exact:true}).click();
    await expect(panel.getByLabel("Product arc degrees",{exact:true})).toHaveValue("25");
    await expect(panel.getByLabel("Product dolly percent",{exact:true})).toHaveValue("0");
    await panel.getByLabel("Product arc degrees",{exact:true}).fill("1e");
    await expect(panel.getByRole("button",{name:"Download recipe and prepare source input",exact:true})).toBeDisabled();
    // Recovery retains incomplete original author input, not a coerced path.
    await page.reload();await openBlenderRecipe(page,scene.id);
    panel=recipePanel(page);
    await expect(panel.getByLabel("Product arc degrees",{exact:true})).toHaveValue("1e");
    await panel.getByRole("button",{name:"Use Dolly focus",exact:true}).click();
    await expect(panel.getByLabel("Product arc degrees",{exact:true})).toHaveValue("0");
    await expect(panel.getByLabel("Product dolly percent",{exact:true})).toHaveValue("12");
    await panel.getByLabel("Product arc degrees",{exact:true}).fill("25");
    expect(await service.project(prepared.id)).toEqual(prepared);
    const downloaded=await downloadBlenderRecipe(page);
    const path=info.outputPath("owned-source",downloaded.suggestedFilename());
    await mkdir(dirname(path),{recursive:true});await downloaded.saveAs(path);
    const bytes=await readFile(path),digest=createHash("sha256").update(bytes).digest("hex");
    expect(JSON.parse(bytes.toString("utf8"))).toMatchObject({version:1,camera_curve:"linear",camera_motion:{arc_degrees:25,dolly_percent:12}});
    await sourcePanel(page).getByLabel("Source file path",{exact:true}).fill(path);
    await button(page,"Save source and capsule").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(prepared.revision+1));
    const saved=await service.project(prepared.id);
    const capsule=saved.production_design!.capsules.find(item=>item.scene_id===scene.id)!;
    expect(capsule.source_sha256).toBe(digest);
    expect(capsule.fidelity).toMatchObject({visual:"unavailable",temporal:"unavailable",structural:"unavailable",editable:"unavailable",evidence_sha256:null});
    const {unlink}=await import("node:fs/promises");await unlink(path);
    await service.close();await service.start();await page.reload();await openBlenderRecipe(page,scene.id);
    panel=recipePanel(page);
    await expect(panel.getByLabel("Product arc degrees",{exact:true})).toHaveValue("25");
    await expect(panel.getByLabel("Product dolly percent",{exact:true})).toHaveValue("12");
    await expect(panel.getByLabel("Native camera curve",{exact:true})).toHaveValue("linear");
    await expect(panel).toContainText("Saved source metadata matches the prepared recipe digest.");
    expect(await service.project(prepared.id)).toEqual(saved);
    expect((await service.history(prepared.id,prepared.revision,8)).map(event=>event.change.type)).toEqual(["import_native_capsule_source"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),saved,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(saved);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
