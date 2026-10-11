import { expect,test } from "@playwright/test";

test("ProductStage requires authorship, reflows original generic UI and preserves reusable Canvas objects",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Owned generic screen");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Owned generic screen"});
  await page.getByRole("button",{name:"ProductStage",exact:true}).click();
  await expect(page.getByRole("heading",{name:"ProductStage"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Add ProductStage to scene"})).toBeDisabled();
  await expect(page.getByText(/Original-content rights must be explicitly declared/)).toBeVisible();
  await page.getByLabel("ProductStage rights attestation").fill("I made this original generic UI graphic and authorize this preview.");
  await expect(page.getByRole("button",{name:"Add ProductStage to scene"})).toBeEnabled();
  await expect(page.getByRole("img",{name:/ProductStage browser 16:9, original graphic/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("product-stage-browser.png"),fullPage:true});
  await page.getByLabel("ProductStage frame").selectOption("generic_handset");
  await page.getByRole("button",{name:"9:16",exact:true}).click();
  await expect(page.getByRole("img",{name:/ProductStage generic_handset 9:16/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("product-stage-handset-portrait.png"),fullPage:true});
  await page.getByRole("button",{name:"16:9",exact:true}).click();
  await page.getByRole("button",{name:"Add ProductStage to scene"}).click();
  await expect(page.getByRole("button",{name:"Update ProductStage"})).toBeVisible();
  await page.getByLabel("ProductStage heading").fill("Better original flow");
  await page.getByRole("button",{name:"Update ProductStage"}).click();
  await expect(page.getByRole("img",{name:/ProductStage generic_handset 16:9/}))
    .toContainText("Better original flow");
  await page.getByRole("button",{name:"Edit stage objects"}).click();
  await expect(page.getByRole("button",{name:"Canvas object ProductStage / screen_heading",exact:true}))
    .toContainText("Better original flow");
});
test("ProductStage rejects unreviewed third-party captures and oversized screen copy",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"ProductStage",exact:true}).click();
  await page.getByLabel("ProductStage rights attestation").fill("Original UI authored by this project's editor");
  await page.getByLabel("ProductStage frame").selectOption("generic_handset");
  await page.getByLabel("ProductStage heading").fill("W".repeat(40));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("screen anchor");
  await expect(page.getByRole("button",{name:"Add ProductStage to scene"})).toBeDisabled();
});

test("ProductStage refuses policy-prohibited copy before saving",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Governed original screen");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Governed original screen"});
  await page.getByRole("button",{name:"Brand & taste",exact:true}).click();
  await page.getByText("Brand identity",{exact:true}).locator("..").locator("input").fill("Governed UI");
  await page.getByText("Forbidden copy phrases (one per line)")
    .locator("..").locator("textarea").fill("screenbound");
  await page.getByRole("button",{name:"Save brand policy"}).click();
  await page.getByRole("button",{name:"ProductStage",exact:true}).click();
  await page.getByLabel("ProductStage rights attestation")
    .fill("I authored this generic original UI and authorize publication.");
  await page.getByLabel("ProductStage heading").fill("screenbound");
  await expect(page.getByText(/phrase forbidden by the active BrandProfile/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Add ProductStage to scene"})).toBeDisabled();
  await page.getByLabel("ProductStage heading").fill("Independent original workflow");
  await expect(page.getByRole("button",{name:"Add ProductStage to scene"})).toBeEnabled();
});
