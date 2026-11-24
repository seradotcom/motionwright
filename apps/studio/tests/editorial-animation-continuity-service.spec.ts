import {expect,test} from "@playwright/test";
import {ScopedChangeService} from "./support/scopedChangeService";
import {createEditorialAnimation,continueEditorialAnimation} from "./support/editorialAnimationJourney";
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
test("A continuous editorial production saves through real grants, restarts SQLite and continues the same motion source",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","editorial-animation.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap();await service.attachFixturePage(page,boot.project.id);await page.goto("/");
    const context=await createEditorialAnimation(page);
    expect(await service.project(boot.project.id)).toEqual(context.project);
    expect((await service.history(boot.project.id,context.base.revision,16)).map(event=>event.change.type))
      .toEqual(["edit_scene_beat_timeline","set_canvas_motion_keyframes","upsert_camera_reframe","edit_scene_beat_timeline"]);
    await service.close();await service.start();await page.reload();
    expect(await service.project(boot.project.id)).toEqual(context.project);
    const continued=await continueEditorialAnimation(page,context);
    expect(await service.project(boot.project.id)).toEqual(continued);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),continued,[],[async()=>{await page.close();}]);finalized=true;
    expect(closure.oracle?.project).toEqual(continued);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
