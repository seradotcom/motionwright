import {expect,test} from "@playwright/test";

test("delivery cut review traces unsaved scene omissions and protected-scene conflicts",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const report=page.getByRole("region",{name:"Narrative cut continuity review"});
  await expect(report.getByRole("heading",{name:"Narrative cut continuity"})).toBeVisible();
  await expect(report).toContainText("4/4");
  const before=await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button",{name:"Customize cut",exact:true}).click();
  const removed=page.locator(".variant-scene-row").filter({hasText:"Pixels are brittle"});
  await removed.locator('input[type="checkbox"]').first().uncheck();
  await expect(report).toContainText("3/4");
  await expect(report).toContainText("Pixels are brittle");
  await expect(report).toContainText("Reviewing unsaved variant choices");
  await removed.locator('input[type="checkbox"]').nth(1).check();
  await expect(report).toContainText("OMITTED PROTECTED");
  await expect(report).toContainText("BLOCKED CUT");
  expect(await page.locator(".revision-chip").first().innerText()).toBe(before);
  await page.screenshot({path:testInfo.outputPath("narrative-cut-review.png"),fullPage:true});
});

test("cut analysis never claims renderer, source rights or creative approval",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const report=page.getByRole("region",{name:"Narrative cut continuity review"});
  await expect(report).toContainText("PLAN NOT ADMITTED");
  await expect(report).toContainText("not");
  await expect(report).toContainText("permission to publish");
  await expect(report).not.toContainText("Verified creative approval");
});
