import { expect, test } from "@playwright/test";

test("SplitExplanation is editable in all aspects, preserves objects and survives workspace switches", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Add scene", exact: true }).click();
  await page.getByLabel("New scene name").fill("Two-part explanation");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByLabel("Production working scene").selectOption({ label: "Two-part explanation" });

  const layout = page.getByLabel("Component layout");
  await expect(layout).toHaveValue("product_hero_reveal");
  await layout.selectOption("split_explanation");
  await expect(page.getByRole("heading", { name: "SplitExplanation" })).toBeVisible();
  await page.getByLabel("Left panel idea").fill("The first problem");
  await page.getByLabel("Right panel narrative").fill("A clear second part that remains editable.");

  const preview = page.getByRole("img", { name: /SplitExplanation 16:9/ });
  await expect(preview).toBeVisible();
  await expect(preview).toContainText("The first problem");
  await page.screenshot({ path: testInfo.outputPath("split-explanation-landscape.png"), fullPage: true });

  await page.getByRole("button", { name: "9:16", exact: true }).click();
  await expect(page.getByRole("img", { name: /SplitExplanation 9:16/ })).toContainText("The first problem");
  await page.screenshot({ path: testInfo.outputPath("split-explanation-portrait.png"), fullPage: true });

  await page.getByRole("button", { name: "1:1", exact: true }).click();
  await expect(page.getByRole("img", { name: /SplitExplanation 1:1/ })).toContainText("A clear second part");
  await page.screenshot({ path: testInfo.outputPath("split-explanation-square.png"), fullPage: true });

  const header = page.locator(".production-workspace-header p");
  const revision = Number((await header.innerText()).match(/revision (\d+)/)![1]);
  await page.getByRole("button", { name: "Add to scene", exact: true }).click();
  await expect(header).toContainText(`revision ${revision+1}`);
  await expect(page.getByRole("button", { name: "Update component", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Production plan", exact: true }).click();
  await page.getByRole("button", { name: "Components", exact: true }).click();
  await expect(layout).toHaveValue("split_explanation");

  await page.getByRole("button", { name: "Edit objects", exact: true }).click();
  await expect(page.getByRole("button", { name: "Canvas object SplitExplanation / headline", exact: true })).toContainText("The first problem");
  await expect(page.getByRole("button", { name: "Canvas object SplitExplanation / body", exact: true })).toContainText("A clear second part");
});

test("legacy family is the default and unsafe copy fails closed without stale preview", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Production", exact: true }).click();
  const layout = page.getByLabel("Component layout");
  await expect(layout).toHaveValue("product_hero_reveal");
  await layout.selectOption("split_explanation");
  await page.getByLabel("Left panel idea").fill("X".repeat(65));
  await expect(page.locator(".production-inspector [role=alert]")).toContainText("layout budget");
  await expect(page.locator(".production-study text")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add to scene", exact: true })).toBeDisabled();
  await layout.selectOption("product_hero_reveal");
  await expect(page.getByRole("heading", { name: "ProductHeroReveal" })).toBeVisible();
});
