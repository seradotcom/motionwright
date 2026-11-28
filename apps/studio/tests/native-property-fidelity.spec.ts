import { expect, test, type Page } from "@playwright/test";

async function sourceReview(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("button", { name: "Native sources", exact: true }).click();
  await page.getByRole("combobox", { name: "Imported source asset" }).selectOption({ label: "native-demo.glb" });
  await page.getByRole("button", { name: "Attach source to scene" }).click();
  const panel = page.getByRole("region", { name: "Native per-property fidelity review", exact: true });
  await expect(panel.getByLabel("Native property path", { exact: true })).toBeVisible();
  return panel;
}
test("one native source retains bytes across a complete property review and later row edit", async ({ page }, info) => {
  const panel = await sourceReview(page), header = page.locator(".production-workspace-header p");
  const prior = Number((await header.innerText()).match(/revision (\d+)/)?.[1]); expect(Number.isFinite(prior)).toBe(true);
  const beforeDigest = await panel.locator(".native-fidelity-source code").innerText();
  await panel.getByLabel("Native property path", { exact: true }).fill("camera.focal_length");
  await panel.getByLabel("Native property fidelity", { exact: true }).selectOption("baked");
  await panel.getByLabel("Native property loss note", { exact: true }).fill("The original GLB camera focal length remains in its source editor.");
  await panel.getByRole("button", { name: "Add property to review", exact: true }).click();
  await panel.getByLabel("Native property path", { exact: true }).fill("geometry.mesh");
  await panel.getByLabel("Native property fidelity", { exact: true }).selectOption("native");
  await panel.getByRole("button", { name: "Add property to review", exact: true }).click();
  await expect(header).toContainText("revision " + prior);
  await panel.getByRole("button", { name: "Save property review", exact: true }).click();
  await expect(header).toContainText("revision " + (prior + 1));
  const capsule = page.locator(".production-capsule-list article").filter({ hasText: beforeDigest });
  const losses = capsule.getByRole("list", { name: "Declared property losses" });
  await expect(losses.getByRole("listitem")).toHaveCount(1);
  await expect(losses).toContainText("camera.focal_length");
  await expect(losses).toContainText("baked");
  await expect(losses).toContainText("The original GLB camera focal length remains in its source editor.");
  const records = panel.getByLabel("Property classifications before and after");
  await expect(records).toContainText("camera.focal_length"); await expect(records).toContainText("geometry.mesh");
  await expect(panel.locator(".native-fidelity-source code")).toHaveText(beforeDigest);
  await panel.getByRole("button", { name: "Edit property review camera.focal_length", exact: true }).click();
  await expect(panel.getByLabel("Native property fidelity", { exact: true })).toHaveValue("baked");
  await panel.getByLabel("Native property loss note", { exact: true }).fill("An updated loss note in the retained review.");
  await panel.getByRole("button", { name: "Update property in review", exact: true }).click();
  await panel.getByRole("button", { name: "Remove property from review geometry.mesh", exact: true }).click();
  await page.getByRole("button", { name: "Production plan", exact: true }).click();
  await page.getByRole("button", { name: "Native sources", exact: true }).click();
  await expect(records).toContainText("Removed in draft");
  await expect(losses).toContainText("The original GLB camera focal length remains in its source editor.");
  await panel.getByRole("button", { name: "Save property review", exact: true }).click();
  await expect(header).toContainText("revision " + (prior + 2));
  await expect(records).not.toContainText("geometry.mesh"); await expect(records).toContainText("updated loss note");
  await expect(losses).toContainText("An updated loss note in the retained review.");
  await expect(panel).toContainText("does not authorize execution");
  await page.screenshot({ path: info.outputPath("native-source-property-review.png"), fullPage: true });
});
test("malformed property input stays in the editor without changing the source revision", async ({ page }) => {
  const panel = await sourceReview(page), header = page.locator(".production-workspace-header p"), snapshot = await header.innerText();
  await panel.getByLabel("Native property path", { exact: true }).fill("camera.__proto__");
  await panel.getByLabel("Native property fidelity", { exact: true }).selectOption("native");
  await panel.getByRole("button", { name: "Add property to review", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("invalid"); await expect(header).toHaveText(snapshot);
  await expect(panel.getByLabel("Property classifications before and after")).not.toContainText("camera.__proto__");
  await expect(panel.getByRole("button", { name: "Save property review", exact: true })).toBeDisabled();
});
test("lossy classification requires a specific note before joining the complete review", async ({ page }) => {
  const panel = await sourceReview(page);
  await panel.getByLabel("Native property path", { exact: true }).fill("animation.keyframes");
  await panel.getByLabel("Native property fidelity", { exact: true }).selectOption("unavailable");
  await panel.getByRole("button", { name: "Add property to review", exact: true }).click();
  await expect(panel.getByRole("alert")).toContainText("specific loss note");
  await expect(panel.getByRole("button", { name: "Save property review", exact: true })).toBeDisabled();
  await expect(panel).toContainText("Source bytes");
});
