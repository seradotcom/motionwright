import { expect, test } from "@playwright/test";

for (const autoKey of [false, true]) {
  test(`a pending ${autoKey ? "Auto-key" : "pose"} gesture keeps its original revision during another Canvas edit`, async ({ page }, info) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    if (autoKey) await page.getByRole("checkbox", { name: "Auto-key position" }).check();
    const actor = page.getByRole("button", { name: "Canvas object Reasoning headline", exact: true });
    const originalX = await page.getByLabel("Canvas X", { exact: true }).inputValue();
    const originalY = await page.getByLabel("Canvas Y", { exact: true }).inputValue();
    const revision = page.locator(".command-bar .revision-chip");
    const initialRevision = Number((await revision.innerText()).slice(1));
    const bounds = await actor.boundingBox();
    if (!bounds) throw new Error("Canvas object has no visible bounds.");
    const x = bounds.x + bounds.width / 2, y = bounds.y + bounds.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 28, y + 16, { steps: 4 });
    await expect(revision).toHaveText("r" + initialRevision);

    // Keyboard editing remains reachable while a mouse gesture is held. This
    // publishes through the ordinary Canvas inspector, without setting React
    // state or replacing a service response.
    const text = "Saved text while the earlier gesture is held";
    await page.getByLabel("Canvas text", { exact: true }).fill(text);
    await page.getByRole("button", { name: "Commit text", exact: true }).press("Enter");
    await expect(revision).toHaveText("r" + (initialRevision + 1));
    await page.mouse.up();
    await page.evaluate(() => new Promise<void>(resolve =>
      requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));

    await expect(revision).toHaveText("r" + (initialRevision + 1));
    await expect(page.getByLabel("Canvas text", { exact: true })).toHaveValue(text);
    await expect(page.getByLabel("Canvas X", { exact: true })).toHaveValue(originalX);
    await expect(page.getByLabel("Canvas Y", { exact: true })).toHaveValue(originalY);
    await expect(page.locator(".motion-keyframe-row")).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Canvas gesture canceled" })).toBeVisible();
    if (!autoKey) {
      const image = info.outputPath("canvas-canceled.png");
      await page.screenshot({ path: image, fullPage: true });
      await info.attach("canvas-canceled", { path: image, contentType: "image/png" });
    }
  });
}

test("Escape cancels an open Canvas gesture before release", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const actor = page.getByRole("button", { name: "Canvas object Reasoning headline", exact: true });
  const revision = await page.locator(".command-bar .revision-chip").innerText();
  const originalX = await page.getByLabel("Canvas X", { exact: true }).inputValue();
  const bounds = await actor.boundingBox();
  if (!bounds) throw new Error("Canvas object has no visible bounds.");
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + bounds.width / 2 + 24, bounds.y + bounds.height / 2 + 10);
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText(revision);
  await expect(page.getByLabel("Canvas X", { exact: true })).toHaveValue(originalX);
});

test("a second pointer cannot cancel or release the active Canvas gesture", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const actor = page.getByRole("button", { name: "Canvas object Reasoning headline", exact: true });
  const revision = page.locator(".command-bar .revision-chip");
  const initialRevision = Number((await revision.innerText()).slice(1));
  const originalX = await page.getByLabel("Canvas X", { exact: true }).inputValue();
  const bounds = await actor.boundingBox();
  if (!bounds) throw new Error("Canvas object has no visible bounds.");
  const start = { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(start.x + 25, start.y + 15, { steps: 3 });
  for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel", "lostpointercapture"]) {
    await actor.dispatchEvent(type, {
      pointerId: 2, pointerType: "touch", isPrimary: false, bubbles: true,
      button: 0, buttons: 1, clientX: start.x + 200, clientY: start.y + 150,
    });
  }
  await expect(revision).toHaveText("r" + initialRevision);
  await page.mouse.move(start.x + 30, start.y + 18);
  await page.mouse.up();
  await expect(revision).toHaveText("r" + (initialRevision + 1));
  await expect(page.getByLabel("Canvas X", { exact: true })).not.toHaveValue(originalX);
});
