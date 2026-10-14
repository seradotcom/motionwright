import { expect, test } from "@playwright/test";

test("Two original craft layouts reflow as editable studies without evidence misrepresentation",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Original craft study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Original craft study"});
  await page.getByRole("button",{name:"Components",exact:true}).click();

  await page.getByLabel("Component layout").selectOption("contrast_comparison");
  await expect(page.getByRole("heading",{name:"ContrastComparison",exact:true})).toBeVisible();
  await page.getByLabel("Before: original concept").fill("A deliberate original idea");
  await page.getByLabel("After: original concept").fill("A clearly authored future direction");
  await expect(page.getByRole("img",{name:/ContrastComparison 16:9/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("conceptual-contrast-16-9.png"),fullPage:true});
  await page.getByRole("button",{name:"9:16",exact:true}).click();
  await expect(page.getByRole("img",{name:/ContrastComparison 9:16/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("conceptual-contrast-9-16.png"),fullPage:true});
  await page.getByRole("button",{name:"16:9",exact:true}).click();
  await page.getByRole("button",{name:"Add to scene",exact:true}).click();
  await expect(page.getByRole("button",{name:"Update component"})).toBeVisible();

  await page.getByLabel("Component layout").selectOption("editorial_quote");
  await expect(page.getByRole("heading",{name:"EditorialQuote",exact:true})).toBeVisible();
  await page.getByLabel("Illustrative quotation").fill("An original, illustrative thought");
  await page.getByLabel("Original quote context").fill("Authored editorial context, not a sourced endorsement");
  await expect(page.getByRole("img",{name:/EditorialQuote 16:9/})).toBeVisible();
  await page.getByRole("button",{name:"1:1",exact:true}).click();
  await expect(page.getByRole("img",{name:/EditorialQuote 1:1/})).toBeVisible();
  await page.getByRole("button",{name:"16:9",exact:true}).click();
  await page.getByRole("button",{name:"Update component"}).click();
  await page.getByRole("button",{name:"Edit objects"}).click();
  await expect(page.getByRole("button",{name:"Canvas object EditorialQuote / headline",exact:true}))
    .toContainText("An original, illustrative thought");
});
test("Oversized editable craft copy refuses projection instead of clipping native text",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Component layout").selectOption("editorial_quote");
  await page.getByLabel("Illustrative quotation").fill("VERY-LONG-UNBREAKABLE-WORD-ALREADY-EXCEEDING-THE-ANCHOR-SIZE");
  await expect(page.getByRole("alert")).toContainText(/layout|native editable screen anchor/);
  await expect(page.getByRole("button",{name:"Add to scene"})).toBeDisabled();
});
