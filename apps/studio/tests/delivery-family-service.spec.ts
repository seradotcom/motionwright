import { expect,test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { ScopedChangeService } from "./support/scopedChangeService";
import { proposalButton as button } from "./support/scopedPaintMotionJourney";
import type { DeliverableProfile } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,"An exact-source StudioService binary is required.");
test.describe.configure({timeout:180_000,retries:0});
test("Selected descendants update together through Service and preserve untouched versions after reopen",async({page},info)=>{
  const service=await ScopedChangeService.create({databasePath:info.outputPath("service","family.sqlite3"),desktopInvokeFixture:true});
  let finalized=false;
  try{
    const boot=await service.fixtureBootstrap(),source={...boot.project.deliverables[0],brand_profile:"Campaign 2"};
    let project=await service.apply(boot.project,{type:"upsert_deliverable",profile:source});
    const variants:DeliverableProfile[]=[];
    for(const [name,width,height,brand] of [["Vertical test",1080,1920,"Campaign 1"],["Square test",1080,1080,"Old square"],["Keep test",720,1280,"Own brand"]] as const){
      const profile:DeliverableProfile={...structuredClone(source),id:randomUUID(),name,width,height,brand_profile:brand,
        parent_profile_id:source.id,source_revision:project.revision,adaptation_notes:["Explicit adaptation"]};
      project=await service.apply(project,{type:"upsert_deliverable",profile});
      variants.push(profile);
    }
    await service.attachFixturePage(page,project.id);await page.goto("/");await button(page,"Deliver").click();
    await page.locator('[data-profile-id="'+source.id+'"]').click();
    const panel=page.getByRole("region",{name:"Propagate saved delivery setting",exact:true});
    await expect(panel).toContainText("Campaign 2");
    await expect(panel.getByRole("button",{name:"Apply to 0 selected profiles",exact:true})).toBeDisabled();
    await panel.getByRole("checkbox",{name:"Update Vertical test",exact:true}).check();
    await panel.getByRole("checkbox",{name:"Update Square test",exact:true}).check();
    await panel.getByRole("button",{name:"Apply to 2 selected profiles",exact:true}).click();
    await expect(page.locator(".revision-chip")).toHaveText("r"+(project.revision+1));
    const saved=await service.project(project.id);
    const expected=project.deliverables.map(p=>variants.slice(0,2).some(v=>v.id===p.id)?{...p,brand_profile:"Campaign 2"}:p);
    expect(saved.deliverables).toEqual(expected);
    expect((await service.history(project.id,project.revision,8)).map(event=>event.change.type)).toEqual(["propagate_delivery_setting"]);
    await service.close();await service.start();await page.reload();await button(page,"Deliver").click();
    await page.locator('[data-profile-id="'+source.id+'"]').click();
    await expect(panel.getByRole("checkbox",{name:"Update Vertical test",exact:true})).toBeDisabled();
    await expect(panel.getByRole("checkbox",{name:"Update Keep test",exact:true})).toBeEnabled();
    expect((await service.project(project.id)).deliverables).toEqual(expected);
    const closure=await service.finalizeClosedStore(info.outputPath("closed-store"),saved,[],[async()=>{await page.close();}]);
    finalized=true;expect(closure.oracle?.project).toEqual(saved);expect(service.lifecycleTaints).toEqual([]);
  }finally{if(!page.isClosed())await page.close();if(!finalized)await service.close();}
});
