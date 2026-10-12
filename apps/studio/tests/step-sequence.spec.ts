import {expect,test} from "@playwright/test";

test("StepSequence is an editable persisted native procedure card across 16:9, 9:16, 1:1",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Step 02 / Inspect");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Step 02 / Inspect"});
  const layout=page.getByLabel("Component layout");
  await layout.selectOption("step_sequence");
  await expect(page.getByRole("heading",{name:"StepSequence"})).toBeVisible();
  await page.getByLabel("Step heading").fill("Inspect the intended result");
  await page.getByLabel("What happens at this step").fill(
    "Read the real project state and confirm what changed before approving another action."
  );
  await page.getByLabel("Step index (e.g. 01 / 03)").fill("02 / 04");

  for(const aspect of ["16:9","9:16","1:1"]) {
    await page.getByRole("button",{name:aspect,exact:true}).click();
    const preview=page.getByRole("img",{name:new RegExp("StepSequence "+aspect)});
    await expect(preview).toContainText("Inspect the intended result");
    await expect(preview).toContainText("02 / 04");
    await expect(preview).toContainText("NOT AN OBSERVED WORKFLOW");
    await page.screenshot({
      path:testInfo.outputPath("step-sequence-"+aspect.replace(":","-")+".png"),
      fullPage:true,
    });
  }

  const header=page.locator(".production-workspace-header p");
  const before=Number((await header.innerText()).match(/revision (\d+)/)?.[1]);
  expect(Number.isInteger(before)).toBe(true);
  await page.getByRole("button",{name:"Add to scene",exact:true}).click();
  await expect(header).toContainText("revision "+(before+1));
  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByRole("button",{name:"Components",exact:true}).click();
  await expect(layout).toHaveValue("step_sequence");
  await page.getByRole("button",{name:"Edit objects",exact:true}).click();
  await expect(page.getByRole("button",{name:"Canvas object StepSequence / headline",exact:true}))
    .toContainText("Inspect the intended result");
  await expect(page.getByRole("button",{name:"Canvas object StepSequence / disclosure",exact:true}))
    .toContainText("NOT AN OBSERVED WORKFLOW");
});

test("StepSequence invalid copy fails closed without creating nodes or incrementing project revision",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Component layout").selectOption("step_sequence");
  await expect(page.getByRole("heading",{name:"StepSequence"})).toBeVisible();
  const before=await page.locator(".production-workspace-header p").innerText();
  await page.getByLabel("Step heading").fill("X".repeat(24));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("wider");
  await expect(page.getByRole("button",{name:"Add to scene",exact:true})).toBeDisabled();
  await expect(page.locator(".production-study text")).toHaveCount(0);
  await expect(page.locator(".production-workspace-header p")).toHaveText(before);
  await page.getByLabel("Component layout").selectOption("product_hero_reveal");
  await expect(page.getByRole("heading",{name:"ProductHeroReveal"})).toBeVisible();
});
