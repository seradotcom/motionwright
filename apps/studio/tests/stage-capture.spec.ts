import {expect,test} from "@playwright/test";

test("ProductStage source panel cannot synthesize a registered screenshot or bypass its provenance",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Controlled first-party source tile");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene")
    .selectOption({label:"Controlled first-party source tile"});
  await page.getByRole("button",{name:"ProductStage",exact:true}).click();
  await expect(page.getByRole("heading",{name:"First-party source tile"})).toBeVisible();
  await expect(page.getByText("Save the original ProductStage before attaching a source.")).toBeVisible();
  await page.getByLabel("ProductStage rights attestation")
    .fill("I authored these unbranded original graphics and authorize use of them.");
  await page.getByRole("button",{name:"Add ProductStage to scene"}).click();
  await expect(page.getByRole("button",{name:"Update ProductStage"})).toBeVisible();
  await expect(page.getByText("No registered first-party source capture is available.")).toBeVisible();
  await expect(page.getByRole("button",{name:"Bind source tile"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Detach source tile"})).toBeDisabled();
  await expect(page.getByText(/editorial vector preview does not display this binary/)).toHaveCount(0);
  await expect(page.getByRole("img",{name:/ProductStage browser 16:9, original graphic/})).toBeVisible();
});
