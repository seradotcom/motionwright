import {expect,test} from "@playwright/test";
import {authorBlenderCameraComposition} from "./support/blenderCameraComposition";
test("Author filled primitives and saved camera framing for a static Blender source scene",async({page})=>{
  await page.goto("/");
  const result=await authorBlenderCameraComposition(page);
  expect(result.project.revision).toBe(result.base.revision+10);
});
