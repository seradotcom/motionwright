import {expect,test,type Page} from "@playwright/test";
import type {SavedMotionHarness} from "./fixtures/saved-motion-capture";
declare global {interface Window {__SAVED_MOTION__:SavedMotionHarness}}
const receipt="11111111-1111-4111-8111-111111111111";
async function source(page:Page){
  return page.evaluate(()=>{
    const project=window.__SAVED_MOTION__.project(),profile=project.deliverables[0];
    const hint={receipt_id:"11111111-1111-4111-8111-111111111111",capture_sha256:"a".repeat(64),
      segment_count:1,frame_count:60,fps_num:Number(profile.frame_rate.num),fps_den:Number(profile.frame_rate.den)};
    const segment={segment_id:"synthetic-original-segment",scene_ids:[project.scenes[0].id],frame_count:60};
    return {inventory:{schema:"motionwright.saved-motion-inventory/1",project_id:project.id,generation:project.generation,
      revision:project.revision,deliverable_id:profile.id,capture:hint},
      result:{receipt_id:hint.receipt_id,capture_sha256:hint.capture_sha256,
        project_resource:"project:"+project.id,generation:project.generation,revision:project.revision,
        deliverable_id:profile.id,frame_rate:{num:hint.fps_num,den:hint.fps_den},
        segments:[{...segment,plan_ref:"synthetic-plan",fingerprint:"b".repeat(64),job_ref:"synthetic-job",
          artifact:{frame_count:60,manifest_sha256:"c".repeat(64)},verification:{}}],
        preview:[{...segment,token:"22222222-2222-4222-8222-222222222222"}]}};
  });
}
async function last(page:Page,command:string){
  await expect.poll(()=>page.evaluate(command=>window.__SAVED_MOTION__.calls().filter(call=>call.command===command).length,command)).toBeGreaterThan(0);
  return page.evaluate(command=>window.__SAVED_MOTION__.calls().filter(call=>call.command===command).at(-1)!,command);
}
async function discover(page:Page){
  await page.goto("/tests/fixtures/saved-motion-capture.html");
  await expect(page.getByRole("button",{name:"Find saved capture",exact:true})).toBeEnabled();
  const original=await source(page);
  await page.getByRole("button",{name:"Find saved capture",exact:true}).click();
  const request=await last(page,"saved_motion_capture_inventory");
  await page.evaluate(({id,value})=>window.__SAVED_MOTION__.resolve(id,value),{id:request.id,value:original.inventory});
  return original;
}
test("Deliver recovers original preview handles without rendering or requesting an effect grant",async({page})=>{
  const original=await discover(page);
  await expect(page.getByRole("region",{name:"Saved Motion Canvas capture"})).toContainText("60 frames");
  await page.getByRole("button",{name:"Reopen saved capture",exact:true}).click();
  const request=await last(page,"recover_saved_motion_capture");
  expect(request.args).toEqual({request:{project_id:original.inventory.project_id,generation:original.inventory.generation,
    revision:original.inventory.revision,deliverable_id:original.inventory.deliverable_id,receipt_id:receipt}});
  await page.evaluate(({id,value})=>window.__SAVED_MOTION__.resolve(id,value),{id:request.id,value:original.result});
  await expect.poll(()=>page.evaluate(()=>window.__SAVED_MOTION__.adopted().length)).toBe(1);
  await expect(page.getByText("Saved native capture reopened:",{exact:false})).toBeVisible();
  const commands=await page.evaluate(()=>window.__SAVED_MOTION__.calls().map(call=>call.command));
  expect(commands).not.toContain("issue_effect_grant");
  expect(commands).not.toContain("render_motion_canvas");
  expect(commands).not.toContain("assemble_av_master");
});
test("a late recovered capture cannot replace a changed project view",async({page})=>{
  const original=await discover(page);
  await page.getByRole("button",{name:"Reopen saved capture",exact:true}).click();
  const request=await last(page,"recover_saved_motion_capture");
  await page.evaluate(()=>window.__SAVED_MOTION__.scope({generation:"33333333-3333-4333-8333-333333333333"}));
  await page.evaluate(({id,value})=>window.__SAVED_MOTION__.resolve(id,value),{id:request.id,value:original.result});
  expect(await page.evaluate(()=>window.__SAVED_MOTION__.adopted())).toEqual([]);
  await expect(page.getByText("Saved native capture reopened:",{exact:false})).toHaveCount(0);
});
test("leaving and returning to a source cannot revive its pending inventory",async({page})=>{
  await page.goto("/tests/fixtures/saved-motion-capture.html");
  const original=await source(page);
  await page.getByRole("button",{name:"Find saved capture",exact:true}).click();
  const request=await last(page,"saved_motion_capture_inventory");
  await page.evaluate(()=>window.__SAVED_MOTION__.scope({generation:"33333333-3333-4333-8333-333333333333"}));
  await page.evaluate(generation=>window.__SAVED_MOTION__.scope({generation}),original.inventory.generation);
  await page.evaluate(({id,value})=>window.__SAVED_MOTION__.resolve(id,value),{id:request.id,value:original.inventory});
  await expect(page.getByRole("button",{name:"Reopen saved capture",exact:true})).toHaveCount(0);
  expect(await page.evaluate(()=>window.__SAVED_MOTION__.adopted())).toEqual([]);
});
