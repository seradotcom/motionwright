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

test("typographic focus and contrast remain editable through grouped motion, aspect changes and retained human styling", async ({ page }, info) => {
  const button = (name: string) => page.getByRole("button", { name, exact: true });
  await page.goto("/");
  await button("Add scene").click(); await page.getByLabel("New scene name").fill("Authored typography"); await button("Add").click();
  const open = async () => {
    await button("Production").click();
    await page.getByLabel("Production working scene", { exact: true }).selectOption({ label: "Authored typography" });
    await button("Components").click();
  };
  await open();
  await page.getByLabel("Component layout", { exact: true }).selectOption("typography_focus");
  await page.getByLabel("Eyebrow", { exact: true }).fill("DIRECCIÓN / ORIGINAL");
  await page.getByLabel("Headline", { exact: true }).fill("Una idea clara");
  await page.getByLabel("Supporting copy", { exact: true }).fill("Espacio para lo que importa.");
  await page.getByLabel("Wordmark", { exact: true }).fill("MW");
  for (const aspect of ["16:9", "1:1", "9:16"]) {
    await button(aspect).click();
    const study = page.getByRole("img", { name: new RegExp("^TypographyFocus " + aspect) });
    await expect(study.locator("[data-canvas-node-id] > text")).toHaveCount(4);
    await expect(study.locator("[data-canvas-node-id] > rect")).toHaveCount(2);
    await expect(study).toContainText("Una idea clara");
  }
  await button("16:9").click(); await button("Add to scene").click(); await expect(button("Update component")).toBeVisible();
  const focusStudy = page.getByRole("img", { name: /^TypographyFocus 16:9/ });
  const ids = await focusStudy.locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-canvas-node-id")));
  await button("Entrance").click();
  const alpha = await focusStudy.locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("opacity")));
  expect(new Set(alpha).size).toBe(1); expect(Number(alpha[0])).toBeGreaterThan(0); expect(Number(alpha[0])).toBeLessThan(1);
  await button("Edit objects").click();
  await page.getByRole("button", { name: "Canvas object TypographyFocus / headline", exact: true }).click();
  await page.getByLabel("Canvas fill", { exact: true }).fill("#FFAA22");
  const before = await page.locator(".command-bar .revision-chip").innerText();
  await button("Commit style").click(); await expect(page.locator(".command-bar .revision-chip")).not.toHaveText(before);
  await open(); await page.getByLabel("Component layout", { exact: true }).selectOption("typography_contrast");
  await page.getByLabel("Supporting idea", { exact: true }).fill("");
  await expect(button("Update component")).toBeDisabled();
  await button("Brief").click(); await open();
  await expect(page.getByLabel("Component layout", { exact: true })).toHaveValue("typography_contrast");
  await expect(page.getByLabel("Supporting idea", { exact: true })).toHaveValue("");
  await page.getByLabel("Supporting idea", { exact: true }).fill("Un contexto que apoya la idea.");
  const review = page.getByRole("region", { name: "Component change review", exact: true });
  await expect(review).toContainText("#FFAA22");
  await expect(button("Update component")).toBeEnabled();
  const revision = await page.locator(".command-bar .revision-chip").innerText();
  await button("Update component").click(); await expect(page.locator(".command-bar .revision-chip")).not.toHaveText(revision);
  await button("9:16").click(); await button("Settled").click();
  const contrast = page.getByRole("img", { name: /^TypographyContrast 9:16/ });
  await expect(contrast).toBeVisible();
  expect(await contrast.locator("[data-canvas-node-id]").evaluateAll(nodes => nodes.map(node => node.getAttribute("data-canvas-node-id")))).toEqual(ids);
  const headlineId = ids[1];
  await expect(contrast.locator('[data-canvas-node-id="' + headlineId + '"] > text')).toHaveAttribute("fill", "#FFAA22");
  await button("Enlarge study").click();
  const expanded = page.getByRole("img", { name: "Expanded editorial composition study", exact: true });
  await expect(expanded.locator('[data-canvas-node-id="' + headlineId + '"] > text')).toHaveAttribute("fill", "#FFAA22");
  await expanded.screenshot({ path: info.outputPath("typography-contrast-portrait-editable.png") });
  await button("Close expanded study").click();
  await page.getByLabel("Animate entrance", { exact: true }).uncheck();
  const animatedRevision = await page.locator(".command-bar .revision-chip").innerText();
  await button("Update component").click(); await expect(page.locator(".command-bar .revision-chip")).not.toHaveText(animatedRevision);
  await button("Detach, keep objects").click(); await expect(button("Add to scene")).toBeVisible();
  await button("Canvas").click();
  await expect(page.getByRole("button", { name: /^Canvas object TypographyContrast/ })).toHaveCount(6);
  await page.getByRole("button", { name: "Canvas object TypographyContrast / headline", exact: true }).click();
  await expect(page.getByLabel("Canvas fill", { exact: true })).toHaveValue("#FFAA22");
});
