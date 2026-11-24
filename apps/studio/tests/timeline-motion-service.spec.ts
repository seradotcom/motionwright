import { expect,test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import { createArrangementScene } from "./support/canvasArrangementJourney";
import { openTimelineAnimation,leaveMotionRangeInput,applyMotionRangeProposal } from "./support/timelineMotionJourney";
import { proposalButton as button } from "./support/scopedPaintMotionJourney";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
test("Timeline animation timing retains incomplete inputs, commits under actual Service authority and survives SQLite restart",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","timeline-motion.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();await service.attachFixturePage(page,boot.project.id);await page.goto("/");
    const context=await createArrangementScene(page),base=await service.project(boot.project.id),nodeId=context.ids[1];
    await button(page,"Fit scene").click();await openTimelineAnimation(page,nodeId);await leaveMotionRangeInput(page);
    await page.reload();await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:context.name}).click();
    await openTimelineAnimation(page,nodeId);await applyMotionRangeProposal(page,nodeId);
    expect(await service.project(base.id)).toEqual(base);
    expect(await service.history(base.id,base.revision,8)).toEqual([]);
    await button(page,"Apply as one revision").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(base.revision+1));
    const saved=await service.project(base.id),original=base.scenes.find(scene=>scene.id===context.sceneId)!.nodes.find(node=>node.id===nodeId)!;
    expect(saved.scenes.find(scene=>scene.id===context.sceneId)!.nodes.find(node=>node.id===nodeId)).toEqual({
      ...original,keyframes:original.keyframes.map(key=>({...key,at:key.at.num==="0"?key.at:{num:"3",den:"1"}}))});
    expect(saved.production_design!.camera_reframes).toEqual(base.production_design!.camera_reframes);
    await service.close();await service.start();await page.reload();
    await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:context.name}).click();await openTimelineAnimation(page,nodeId);
    expect(await service.project(base.id)).toEqual(saved);
    await expect(page.getByLabel("Keyframe 6 time",{exact:true})).toHaveValue("3");
    const history=page.getByRole("region",{name:"Reversible scoped changes",exact:true});
    await history.getByRole("button",{name:"Preview undo",exact:true}).click();await button(page,"Apply undo as new revision").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(saved.revision+1));
    const final=await service.project(base.id);expect(final.scenes).toEqual(base.scenes);
    expect((await service.history(base.id,base.revision,8)).map(event=>event.change.type)).toEqual(["apply_creative_patch","undo_creative_patch"]);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),final,[],[async()=>{await page.close();}]);finalized=true;
    expect(closure.oracle?.project).toEqual(final);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
