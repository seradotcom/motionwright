import { expect, test } from "@playwright/test";

async function savedMusicClock(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByLabel("Production governing clock").selectOption("music");
  await page.getByLabel("Music beat timestamps").fill("0.000\n6.000\n18.000\n30.000\n42.000");
  await page.getByRole("button",{name:"Use marked times in plan draft"}).click();
  await page.getByRole("button",{name:"Save production plan"}).click();
  await expect(page.getByRole("heading",{name:"Conform multiple cuts to the music grid"})).toBeVisible();
}

test("multiple source-scoped scene cuts are reviewed and applied as exactly one revision",async({page})=>{
  await savedMusicClock(page);
  const panel=page.getByRole("region",{name:"Multi-scene musical conform"});
  // The first fixture scene has a real authored Timing lock; it must not be
  // silently bypassed merely because two other scenes are editable.
  await expect(panel).toContainText("Protected project or scene timing");
  const button=panel.getByRole("button",{name:"Apply multi-scene music conform"});
  await expect(button).toBeDisabled();
  await panel.getByLabel("Conform Reasoning is solved").uncheck();
  await panel.getByLabel("Conform Semwright acts natively").check();
  const review=panel.getByLabel("Multi-scene conform impact preview");
  await expect(review).toContainText("36.000s");
  await expect(review).toContainText("38.000s");
  await expect(panel).toContainText("Saved beat");
  await expect(panel.getByLabel("Acknowledge fixed anchors across conformed scenes")).toBeVisible();
  await expect(button).toBeDisabled();
  const before=await page.locator(".revision-chip").first().innerText();
  await panel.getByLabel("Acknowledge fixed anchors across conformed scenes").check();
  await expect(button).toBeEnabled();
  await button.click();
  await expect(page.locator(".revision-chip").first()).not.toHaveText(before);
  await expect(panel).toContainText("One atomic musical conform committed");
  await expect(page.getByLabel("Selected audio fingerprint")).toContainText("5f5f5f5f");
  await expect(page.getByLabel("Music beat timestamps")).toHaveValue("0.000\n6.000\n18.000\n30.000\n42.000");
});

test("multi-cut preview is read-only and a locked scene cannot be conformed",async({page})=>{
  await savedMusicClock(page);
  const panel=page.getByRole("region",{name:"Multi-scene musical conform"});
  const revision=await page.locator(".revision-chip").first().innerText();
  await expect(panel).toContainText("Protected project or scene timing");
  await expect(panel.getByRole("button",{name:"Apply multi-scene music conform"})).toBeDisabled();
  await panel.getByLabel("Beat for Pixels are brittle").selectOption("3");
  await expect(panel).toContainText("Protected project or scene timing");
  expect(await page.locator(".revision-chip").first().innerText()).toBe(revision);
});
