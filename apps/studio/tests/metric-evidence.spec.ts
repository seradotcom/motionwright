import { expect, test } from "@playwright/test";

test("MetricEvidence is authored, reflowed, persisted and never labeled as verified evidence", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name:"Add scene", exact:true }).click();
  await page.getByLabel("New scene name").fill("Evidence presentation");
  await page.getByRole("button", { name:"Add", exact:true }).click();
  await page.getByRole("button", { name:"Production", exact:true }).click();
  await page.getByLabel("Production working scene").selectOption({label:"Evidence presentation"});

  const layout=page.getByLabel("Component layout");
  await layout.selectOption("metric_evidence");
  await expect(page.getByRole("heading", { name:"MetricEvidence" })).toBeVisible();
  await page.getByLabel("Authored fact / metric").fill("42% growth");
  await page.getByLabel("Evidence context").fill("An illustrative claim requiring a dated and verified source.");
  await page.getByLabel("Evidence label").fill("DATA");
  for (const aspect of ["16:9","9:16","1:1"]) {
    await page.getByRole("button", { name:aspect, exact:true }).click();
    const preview=page.getByRole("img", { name:new RegExp("MetricEvidence " + aspect) });
    await expect(preview).toContainText("42% growth");
    await expect(preview).toContainText("SOURCE NOT VERIFIED");
    await page.screenshot({path:testInfo.outputPath("metric-evidence-" + aspect.replace(":","-") + ".png"),fullPage:true});
  }
  const header=page.locator(".production-workspace-header p");
  const before=Number((await header.innerText()).match(/revision (\d+)/)?.[1]);
  expect(Number.isFinite(before)).toBe(true);
  await page.getByRole("button", { name:"Add to scene", exact:true }).click();
  await expect(header).toContainText("revision " + (before+1));
  await page.getByRole("button", { name:"Production plan", exact:true }).click();
  await page.getByRole("button", { name:"Components", exact:true }).click();
  await expect(layout).toHaveValue("metric_evidence");
  await page.getByRole("button", { name:"Edit objects", exact:true }).click();
  await expect(page.getByRole("button", { name:"Canvas object MetricEvidence / headline", exact:true })).toContainText("42% growth");
  await expect(page.getByRole("button", { name:"Canvas object MetricEvidence / disclosure", exact:true })).toContainText("SOURCE NOT VERIFIED");
});

test("invalid metric copy fails closed without preview reuse or document mutation", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name:"Production", exact:true }).click();
  const layout=page.getByLabel("Component layout");
  await layout.selectOption("metric_evidence");
  await expect(page.getByRole("heading", { name:"MetricEvidence" })).toBeVisible();
  const revision=await page.locator(".production-workspace-header p").innerText();
  await page.getByLabel("Authored fact / metric").fill("T".repeat(70));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("layout budget");
  await expect(page.locator(".production-study text")).toHaveCount(0);
  await expect(page.getByRole("button", { name:"Add to scene", exact:true })).toBeDisabled();
  await expect(page.locator(".production-workspace-header p")).toHaveText(revision);
  await layout.selectOption("product_hero_reveal");
  await expect(page.getByRole("heading", { name:"ProductHeroReveal" })).toBeVisible();
});
