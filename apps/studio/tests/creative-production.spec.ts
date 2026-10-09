import { expect, test } from "@playwright/test";

test("creative workstation adds an editable component and commits a scoped proposal once",async({page},testInfo)=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Add scene",exact:true}).click();
  await page.getByLabel("New scene name").fill("Hero design study");
  await page.getByRole("button",{name:"Add",exact:true}).click();
  await expect(page.locator(".tree-row")).toHaveCount(5);
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Production working scene").selectOption({label:"Hero design study"});
  await expect(page.getByRole("heading",{name:"ProductHeroReveal",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"Add to scene",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Add to scene",exact:true}).click();
  await expect(page.getByRole("button",{name:"Update component",exact:true})).toBeVisible();
  await expect(page.getByRole("img",{name:/ProductHeroReveal 16:9/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("production-hero-landscape.png"),fullPage:true});
  await page.getByRole("button",{name:"9:16",exact:true}).click();
  await expect(page.getByRole("img",{name:/ProductHeroReveal 9:16/})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("production-hero-portrait.png"),fullPage:true});
  await page.getByRole("button",{name:"Scoped changes",exact:true}).click();
  await page.getByLabel("Patch target object").selectOption({label:"ProductHeroReveal / body"});
  await page.getByLabel("Patch replacement text").fill("A deliberate human editorial decision.");
  await page.getByRole("button",{name:"Add to proposal",exact:true}).click();
  await page.getByLabel("Patch rationale").fill("Keep this line while exploring a different brand accent.");
  await page.getByRole("button",{name:"Preview scoped changes",exact:true}).click();
  await expect(page.getByText("Editorial diff prepared",{exact:true})).toBeVisible();
  await page.screenshot({path:testInfo.outputPath("production-scoped-diff.png"),fullPage:true});
  const before=await page.locator(".production-workspace-header p").innerText();
  const revision=Number(before.match(/revision (\d+)/)?.[1]);
  expect(Number.isFinite(revision)).toBeTruthy();
  await page.getByRole("button",{name:"Apply as one revision",exact:true}).click();
  await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${revision+1}`);
  await expect(page.getByRole("button",{name:"Apply as one revision",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"Components",exact:true}).click();
  await page.getByLabel("Hero accent").fill("#D9A46E");
  await page.getByRole("button",{name:"Update component",exact:true}).click();
  await page.getByRole("button",{name:"Edit objects",exact:true}).click();
  await expect(page.getByRole("button",{name:"Canvas object ProductHeroReveal / body",exact:true})).toContainText("A deliberate human editorial decision.");
});

test("invalid component copy cannot be committed or leave an old preview displayed",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByLabel("Headline",{exact:true}).fill("X".repeat(65));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("layout budget");
  await expect(page.getByRole("button",{name:"Add to scene",exact:true})).toBeDisabled();
  await expect(page.locator(".production-study text")).toHaveCount(0);
});

test("native inspection never substitutes editorial thumbnails for missing native evidence",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Native inspection",exact:true}).click();
  await expect(page.getByRole("heading",{name:"No current native frame grant",exact:true})).toBeVisible();
  await expect(page.locator(".native-inspection-grid img")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Inspect current native frames",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"Open Canvas and native preview",exact:true}).click();
  await expect(page.locator(".canvas-workspace")).toBeVisible();
});
