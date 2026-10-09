import { expect, test } from "@playwright/test";

async function moveCanvasNode(
  page: import("@playwright/test").Page,
  dx: number,
  dy: number,
) {
  const node = page.getByRole("button", { name: "Canvas object Reasoning headline" });
  const rect = await node.boundingBox();
  if (!rect) throw new Error("Expected a visible semantic canvas object");
  const x = rect.x + rect.width / 2;
  const y = rect.y + rect.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 8 });
  await page.mouse.up();
}

test("explicit Auto-key drag commits X/Y atomically without altering base pose", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const checkbox = page.getByRole("checkbox", { name: "Auto-key position" });
  await expect(checkbox).not.toBeChecked();
  const initialRevision = await page.locator(".revision-chip").first().innerText();
  const baseX = await page.getByLabel("Canvas X").inputValue();
  const baseY = await page.getByLabel("Canvas Y").inputValue();
  const slider = page.getByRole("slider", { name: "Motion playhead" });
  await slider.focus();
  await slider.press("End");
  await expect(page.locator(".motion-playhead .mono")).toContainText("/ 7.00s");
  await expect(page.locator(".revision-chip").first()).toHaveText(initialRevision);

  await checkbox.check();
  await expect(page.getByText("KEYED POSITION")).toBeVisible();
  await moveCanvasNode(page, 24, 12);

  await expect(page.locator(".motion-keyframe-row")).toHaveCount(2);
  await expect(page.locator(".motion-keyframe-row").first()).toContainText("x");
  await expect(page.locator(".motion-keyframe-row").nth(1)).toContainText("y");
  await expect(page.getByLabel("Canvas X")).toHaveValue(baseX);
  await expect(page.getByLabel("Canvas Y")).toHaveValue(baseY);
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(initialRevision);

  await page.getByRole("button", { name: "Changes", exact: true }).click();
  const ledger = page.getByRole("table", { name: "Project event journal" });
  await expect(ledger).toContainText("set canvas position keyframe");
  await expect(ledger.getByText("set canvas position keyframe", { exact: true })).toHaveCount(1);
});

test("Auto-key remains opt-in, base editing is distinct, and position locks block gestures", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const checkbox = page.getByRole("checkbox", { name: "Auto-key position" });
  const xBefore = await page.getByLabel("Canvas X").inputValue();
  const revision = await page.locator(".revision-chip").first().innerText();
  await moveCanvasNode(page, 30, 0);
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(revision);
  await expect(page.locator(".motion-keyframe-row")).toHaveCount(0);
  await expect.poll(async () => page.getByLabel("Canvas X").inputValue()).not.toBe(xBefore);

  await checkbox.check();
  await page.locator(".property-locks").getByRole("button", { name: "position" }).click();
  await expect(page.getByRole("checkbox", { name: "Auto-key position" })).toBeChecked();
  await expect(page.getByRole("button", { name: "Canvas object Reasoning headline" })).toHaveClass(/locked/);
  const lockedRevision = await page.locator(".revision-chip").first().innerText();
  await moveCanvasNode(page, 24, 10);
  await expect(page.locator(".revision-chip").first()).toHaveText(lockedRevision);
  await expect(page.locator(".motion-keyframe-row")).toHaveCount(0);
});
