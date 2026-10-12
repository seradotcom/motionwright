import {expect,test} from "@playwright/test";

test("Render dependency inspector is source-revision bound and never claims actual native savings",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Render impact",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Render dependency impact"})).toBeVisible();
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
