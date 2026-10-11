import { expect, test } from "@playwright/test";

test("Production procedural designer creates and revises real editable Canvas objects", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Procedural design study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Procedural design study"});
  await page.getByRole("button",{name:"Procedural",exact:true}).click();

  await expect(page.getByRole("heading",{name:"Procedural field / repetition"})).toBeVisible();
  const count=page.getByLabel("Number of objects");
  await count.fill("18");
  await page.getByLabel("Procedural distribution").selectOption("staggered");
  const study=page.getByRole("img",{name:/Procedural editorial preview, staggered distribution, 18 editable objects/});
  await expect(study).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("procedural-study-staggered.png"),fullPage:true});

  await page.getByRole("button",{name:"Add procedural field"}).click();
  await expect(page.getByRole("button",{name:"Update procedural field"})).toBeVisible();

  await count.fill("65");
  await expect(page.getByRole("alert")).toContainText("budget exceeded");
  await expect(page.getByRole("button",{name:"Update procedural field"})).toBeDisabled();

  await count.fill("8");
  await expect(page.getByRole("img",{name:/8 editable objects/})).toBeVisible();
  await page.getByRole("button",{name:"Update procedural field"}).click();
  await page.getByRole("button",{name:"Detach generator (keep objects)"}).click();
  await expect(page.getByRole("button",{name:"Add procedural field"})).toBeVisible();
  await page.getByRole("button",{name:"Edit individual objects"}).click();
  await expect(page.getByRole("button",{name:"Canvas object ProceduralField / item 000",exact:true})).toBeVisible();
});
