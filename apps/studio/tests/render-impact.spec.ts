import {expect,test} from "@playwright/test";

test("Render dependency inspector is source-revision bound and never claims actual native savings",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Render impact",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Render dependency impact"})).toBeVisible();
  await expect(page.getByText(/Dependency preflight \/ technical comparison not run/)).toBeVisible();
  await expect(page.getByText(/No native frames regenerated or avoided/)).toBeVisible();
  await expect(page.getByLabel("Render impact cause")).toHaveValue("scene_visual");
  const first=page.getByLabel("Dirty source frames Master 16:9");
  await expect(first).toContainText(/\[[0-9]+, [0-9]+\)/);
  await page.getByLabel("Render impact cause").selectOption("global_mix");
  await expect(page.getByText("No source windows selected")).toHaveCount(0);
  await page.getByLabel("Render impact cause").selectOption("scene_audio_tail");
  await expect(page.getByLabel("Audio tail source asset")).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(/tail source/);
  await expect(page.getByText(/Tail length is not independently measured/)).toBeVisible();
});


test("Render impact preserves StepSequence and planar Camera navigation without changing project revision", async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  const baseline=await page.locator(".revision-chip").first().innerText();
  const layout=page.getByLabel("Component layout");
  await expect(layout.locator('option[value="step_sequence"]')).toHaveCount(1);
  await page.getByRole("button",{name:"Render impact",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Render dependency impact"})).toBeVisible();
  await page.getByRole("button",{name:"Camera",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Camera Reframe"})).toBeVisible();
  await page.getByRole("button",{name:"Render impact",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Render dependency impact"})).toBeVisible();
  await page.getByRole("button",{name:"Components",exact:true}).click();
  await expect(page.getByLabel("Component layout").locator('option[value="step_sequence"]')).toHaveCount(1);
  await expect(page.locator(".revision-chip").first()).toHaveText(baseline);
});
