import { expect, test } from "@playwright/test";
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/blender-source-inspector.html");
  await page.getByLabel("Source capsule to save").selectOption("bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb");
});
const panelName = "Saved Blender source inspector";

test("saved recipe and UNKNOWN attempt are usable through the real native-source workbench", async ({ page }) => {
  const panel = page.getByRole("region", { name: panelName });
  const read = panel.getByRole("button", { name: "Read saved Blender source", exact: true });
  await read.click();
  await expect(read).toBeDisabled();
  await page.evaluate(() => window.__BLENDER_SOURCE__.source(1, "SAVED RECIPE"));
  await expect(panel).toContainText("SAVED RECIPE");
  await expect(panel).toContainText("Source bytes verified");
  await expect(panel).toContainText("Bezier auto clamped");
  await panel.getByLabel("Original request ID", { exact: true }).fill("original-product-1");
  await panel.getByLabel("Receipt ID (optional)", { exact: true }).fill("11111111-2222-4333-8444-555555555555");
  await panel.getByRole("button", { name: "Read original Blender attempt", exact: true }).click();
  await expect(panel.getByLabel("Verified saved Blender source", { exact: true })).toHaveCount(0);
  await page.evaluate(() => window.__BLENDER_SOURCE__.unknown(2));
  await expect(panel).toContainText("Original attempt outcome unknown");
  await expect(panel).toContainText("Reconcile this original attempt with its owner");
  await expect(panel).not.toContainText("Source bytes verified");
  const requests = await page.evaluate(() => window.__BLENDER_SOURCE__.requests());
  expect(requests.map(read => read.command)).toEqual(["native_blender_product_source", "native_blender_product_attempt"]);
  expect(requests[1].request).toMatchObject({ original_request_id: "original-product-1",
    receipt_id: "11111111-2222-4333-8444-555555555555",
    source: { revision: 4, capsule_id: "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb" } });
});

test("returning to a saved revision cannot inherit an obsolete read or its completion", async ({ page }) => {
  const panel = page.getByRole("region", { name: panelName });
  const read = panel.getByRole("button", { name: "Read saved Blender source", exact: true });
  await read.click();
  await page.evaluate(async () => {
    await window.__BLENDER_SOURCE__.setRevision(5);
    await window.__BLENDER_SOURCE__.setRevision(4);
  });
  await expect(read).toBeEnabled();
  await read.click();
  await page.evaluate(() => window.__BLENDER_SOURCE__.source(1, "OBSOLETE RECIPE"));
  await expect(panel).not.toContainText("OBSOLETE RECIPE");
  await expect(read).toBeDisabled();
  await page.evaluate(() => window.__BLENDER_SOURCE__.source(2, "CURRENT RECIPE"));
  await expect(panel).toContainText("CURRENT RECIPE");
  await expect(read).toBeEnabled();
});

test("switching capsules preserves authored input and discards an old read error", async ({ page }) => {
  const panel = page.getByRole("region", { name: panelName });
  await page.getByLabel("Source file path", { exact: true }).fill("E:\\SavedSources\\editable-product.blend");
  await panel.getByRole("button", { name: "Read saved Blender source", exact: true }).click();
  const selection = page.getByLabel("Source capsule to save");
  await selection.selectOption("new");
  await expect(panel).toHaveCount(0);
  await selection.selectOption("bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb");
  await page.evaluate(() => window.__BLENDER_SOURCE__.reject(1));
  await expect(panel.getByRole("alert")).toHaveCount(0);
  await expect(page.getByLabel("Source file path", { exact: true })).toHaveValue("E:\\SavedSources\\editable-product.blend");
  await expect(panel.getByRole("button", { name: "Read saved Blender source", exact: true })).toBeEnabled();
});
