import {expect,test} from "@playwright/test";
import {ScopedChangeService} from "./support/scopedChangeService";
import {authorBlenderCameraComposition} from "./support/blenderCameraComposition";
import {proposalButton as button} from "./support/scopedPaintMotionJourney";
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
test("Blender camera and primitive authoring survive actual Service grants and SQLite reopen",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","blender-camera.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();
    await service.attachFixturePage(page,boot.project.id);await page.goto("/");
    const result=await authorBlenderCameraComposition(page);
    expect(await service.project(boot.project.id)).toEqual(result.project);
    expect((await service.history(boot.project.id,result.base.revision,16)).map(e=>e.change.type))
      .toEqual(["add_scene","set_scene_renderer","add_canvas_node","add_canvas_node","transform_canvas_node","update_canvas_style",
        "set_camera","set_camera","set_camera","set_camera"]);
    await service.close();await service.start();await page.reload();
    await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:result.name}).click();
    await page.locator(".canvas-tree-row").filter({hasText:"Circle 1"}).click();
    await expect(page.getByLabel("Camera zoom",{exact:true})).toHaveValue("2");
    await expect(page.getByLabel("Camera Rotation",{exact:true})).toHaveValue("30");
    await expect(page.getByLabel("Canvas fill",{exact:true})).toHaveValue("#E0A940");
    expect(await service.project(boot.project.id)).toEqual(result.project);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),result.project,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(result.project);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
