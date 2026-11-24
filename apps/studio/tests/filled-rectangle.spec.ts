import {expect,test} from "@playwright/test";
import {authorFilledRectangles} from "./support/filledRectangleJourney";
test("Filled rectangle creation and editing preserves exact geometry, alpha, fill and layer IDs",async({page},info)=>{
  await page.goto("/");
  const result=await authorFilledRectangles(page);
  for(const width of [1440,1024]){
    await page.setViewportSize({width,height:940});
    const controls=page.getByLabel("Create canvas object",{exact:true});
    const bounds=await controls.boundingBox();expect(bounds).not.toBeNull();
    for(const control of await controls.getByRole("button").all()){
      const box=await control.boundingBox();expect(box).not.toBeNull();
      expect(box!.x+box!.width).toBeLessThanOrEqual(bounds!.x+bounds!.width+1);
    }
    await page.screenshot({path:info.outputPath("rectangles-"+width+".png")});
  }
  expect(result.firstId).not.toBe(result.secondId);
});
