import {expect,test} from "@playwright/test";
import {createEditorialAnimation,continueEditorialAnimation,poseEditor} from "./support/editorialAnimationJourney";
// This is actual Studio UI with synthetic browser state, not Native pixels or SQLite evidence.
test("Editorial beats keep a usable complete pose, independent fade and camera through later beat edits",async({page},info)=>{
  test.setTimeout(180_000);await page.goto("/");
  const context=await createEditorialAnimation(page);
  for(const width of [1440,1024]){
    await page.setViewportSize({width,height:940});
    await poseEditor(page).locator(".motion-editor-heading").scrollIntoViewIfNeeded();
    await page.locator(".canvas-properties").screenshot({path:info.outputPath("editorial-animation-"+width+".png")});
  }
  const continued=await continueEditorialAnimation(page,context);
  expect(continued.scenes.filter(scene=>scene.id!==context.sceneId)).toEqual(context.base.scenes.filter(scene=>scene.id!==context.sceneId));
  expect(continued.audio).toEqual(context.base.audio);expect(continued.assets).toEqual(context.base.assets);
});
