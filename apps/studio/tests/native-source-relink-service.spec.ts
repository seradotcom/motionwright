import {expect,test,type Page} from "@playwright/test";
import {mkdir,writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {ScopedChangeService} from "./support/scopedChangeService";
import {proposalButton as button} from "./support/scopedPaintMotionJourney";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
async function sources(page:Page, sceneId:string){
  await button(page,"Production").click();
  await page.getByLabel("Production working scene").selectOption(sceneId);
  await button(page,"Native sources").click();
}
test("Relink retained source through actual StudioService grants, CAS and SQLite restart",async({page},info)=>{
  const oldPath=info.outputPath("authored-sources","old.json"),newPath=info.outputPath("authored-sources","new.json");
  await mkdir(dirname(oldPath),{recursive:true});
  await writeFile(oldPath,JSON.stringify({title:"Original owned fixture",version:1}));
  await writeFile(newPath,JSON.stringify({title:"Revised owned fixture",version:2}));
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","relink.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();
    const withOld=await service.importAsset(boot.project,oldPath,"old.json","application/json");
    const prepared=await service.importAsset(withOld,newPath,"new.json","application/json");
    const old=prepared.assets.find(a=>a.name==="old.json")!,next=prepared.assets.find(a=>a.name==="new.json")!;
    expect(old.content_sha256).not.toBe(next.content_sha256);
    const sceneId=prepared.scenes[0].id;
    await service.attachFixturePage(page,prepared.id);await page.goto("/");await sources(page,sceneId);
    await page.getByRole("combobox",{name:"Imported source asset",exact:true}).selectOption(next.id);
    await button(page,"Attach source to scene").click();
    await expect(page.locator(".revision-chip")).toHaveText("r"+(prepared.revision+1));
    const attached=await service.project(prepared.id);
    const capsule=attached.production_design!.capsules.find(c=>c.source_asset_id===next.id)!;
    expect(capsule.scene_id).toBe(sceneId);
    const review=page.getByRole("region",{name:"Native per-property fidelity review",exact:true});
    await review.getByLabel("Native capsule for property negotiation",{exact:true}).selectOption(capsule.id);
    await page.getByLabel("Capsule to relink",{exact:true}).selectOption(capsule.id);
    await review.getByLabel("Native property path",{exact:true}).fill("title");
    await review.getByLabel("Native property fidelity",{exact:true}).selectOption("native");
    await review.getByRole("button",{name:"Add property to review",exact:true}).click();
    await review.getByRole("button",{name:"Save property review",exact:true}).click();
    await expect(page.locator(".revision-chip")).toHaveText("r"+(attached.revision+1));
    const reviewed=await service.project(prepared.id);
    expect(reviewed.production_design!.capsules.find(c=>c.id===capsule.id)?.property_fidelity).toHaveLength(1);
    const panel=page.getByRole("region",{name:"Relink saved source",exact:true});
    await expect(panel.getByRole("button",{name:"Relink saved source",exact:true})).toBeDisabled();
    await panel.getByLabel("Replacement retained source",{exact:true}).selectOption(old.id);
    await expect(panel).toContainText(old.content_sha256!);
    await button(page,"Production plan").click();await button(page,"Native sources").click();
    await expect(panel.getByLabel("Replacement retained source",{exact:true})).toHaveValue(old.id);
    await panel.getByRole("button",{name:"Relink saved source",exact:true}).click();
    await expect(page.locator(".revision-chip")).toHaveText("r"+(reviewed.revision+1));
    const saved=await service.project(prepared.id);
    const restored=saved.production_design!.capsules.find(c=>c.id===capsule.id)!;
    expect(restored).toMatchObject({id:capsule.id,scene_id:sceneId,source_asset_id:old.id,source_sha256:old.content_sha256,
      fidelity:{renderer:"source-relink",visual:"unavailable",temporal:"unavailable",structural:"unavailable",editable:"unavailable",evidence_sha256:null},
      editable_parameters:[]});
    expect(restored.property_fidelity??[]).toEqual([]);
    expect(saved.assets).toEqual(prepared.assets);
    expect(saved.scenes).toEqual(prepared.scenes);
    expect((await service.history(prepared.id,reviewed.revision,16)).map(event=>event.change.type)).toEqual(["relink_native_capsule_source"]);
    await service.close();await service.start();await page.reload();await sources(page,sceneId);
    await page.getByLabel("Capsule to relink",{exact:true}).selectOption(capsule.id);
    await expect(panel).toContainText(old.content_sha256!);
    await expect(panel.getByLabel("Replacement retained source",{exact:true})).toHaveValue("");
    await panel.getByLabel("Replacement retained source",{exact:true}).selectOption(next.id);
    await panel.getByRole("button",{name:"Relink saved source",exact:true}).click();
    await expect(page.locator(".revision-chip")).toHaveText("r"+(saved.revision+1));
    const final=await service.project(prepared.id);
    expect(final.production_design!.capsules.find(c=>c.id===capsule.id)?.source_asset_id).toBe(next.id);
    expect(final.assets).toEqual(prepared.assets);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(final);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
