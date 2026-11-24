import {expect,test} from "@playwright/test";
import {ScopedChangeService} from "./support/scopedChangeService";
import {authorFilledRectangles} from "./support/filledRectangleJourney";
import {proposalButton as button} from "./support/scopedPaintMotionJourney";
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
test("Filled rectangles survive actual StudioService grants, SQLite restart and continued editing",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","rectangles.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();
    await service.attachFixturePage(page,boot.project.id);await page.goto("/");
    const result=await authorFilledRectangles(page);
    expect(await service.project(boot.project.id)).toEqual(result.project);
    expect((await service.history(boot.project.id,result.base.revision,16)).map(event=>event.change.type))
      .toEqual(["add_scene","add_canvas_node","transform_canvas_node","update_canvas_style","add_canvas_node"]);
    await service.close();await service.start();await page.reload();
    await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:result.name}).click();
    const source=result.project.scenes.find(scene=>scene.id===result.sceneId)!.nodes.find(node=>node.id===result.firstId)!;
    await page.locator(".canvas-tree-row").filter({hasText:source.name}).click();
    await expect(page.getByLabel("Canvas fill",{exact:true})).toHaveValue("#E0A940");
    await expect(page.getByLabel("Canvas Opacity",{exact:true})).toHaveValue("0.625");
    await page.getByLabel("Canvas fill",{exact:true}).fill("#72D6BE");
    await button(page,"Commit style").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(result.project.revision+1));
    const continued=await service.project(boot.project.id);
    expect(continued.scenes.find(scene=>scene.id===result.sceneId)!.nodes.find(node=>node.id===result.firstId)?.style.fill).toBe("#72D6BE");
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),continued,[],[async()=>{await page.close();}]);finalized=true;
    expect(closure.oracle?.project).toEqual(continued);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
