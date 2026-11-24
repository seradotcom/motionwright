import { expect, test } from "@playwright/test";
import { assertSavedArrangement, createArrangementScene, selectArrangement, stageAndInspectArrangement,
  undoArrangement, verifyArrangementSelection } from "./support/canvasArrangementJourney";
import { proposalButton as button, readStudioProject } from "./support/scopedPaintMotionJourney";

// Product interactions against browser fixture state; durable Service and Native
// execution remain separately reported.
test("Canvas arranges complete animated frames under camera, preserves drafts, compares and reverses one revision", async ({ page }, info) => {
  test.setTimeout(120_000);
  await page.goto("/");
  const context = await createArrangementScene(page);
  const base = await readStudioProject(page);
  await selectArrangement(page, context.ids);
  await button(page, "Back to object editing").click();
  await button(page, "Arrange objects").click();
  await verifyArrangementSelection(page, context.ids);
  expect(await readStudioProject(page)).toEqual(base);
  await stageAndInspectArrangement(page, context.ids[1]);
  expect(await readStudioProject(page)).toEqual(base);
  for (const width of [1440,1024]) {
    await page.setViewportSize({ width, height: 940 });
    const region = page.getByRole("region", { name: "Canvas arrangement workspace", exact: true });
    const bounds = await region.boundingBox();
    expect(bounds).not.toBeNull();
    const labels = page.locator(".canvas-arrangement-selection label");
    await expect(labels).toHaveCount(3);
    for (const label of await labels.all()) {
      const row = await label.boundingBox(), control = await label.locator("input").boundingBox(), text = await label.locator("span").boundingBox();
      expect(row && control && text).toBeTruthy();
      expect(control!.width).toBe(16); expect(control!.height).toBe(16);
      expect(text!.width).toBeGreaterThan(100);
      expect(text!.x).toBeGreaterThan(control!.x + control!.width);
      expect(text!.x + text!.width).toBeLessThanOrEqual(row!.x + row!.width + 1);
    }
    for (const selector of [".canvas-arrangement", ".production-compare-grid"]) {
      const content = page.locator(selector);
      const box = await content.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
      expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
      await content.screenshot({ path: info.outputPath((selector.includes("compare") ? "arrange-comparison-" : "arrange-controls-") + width + ".png") });
    }
  }
  await button(page, "Apply as one revision").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (base.revision + 1));
  const saved = await readStudioProject(page);
  assertSavedArrangement(base, saved, context.sceneId, context.ids);
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await button(page, "Back to object editing").click();
  await page.getByLabel("Motion playhead", { exact: true }).fill("0.5");
  const sourceStudy = page.getByRole("img", { name: "Editable Canvas source composition", exact: true });
  await expect(sourceStudy.locator('[data-canvas-node-id="' + context.ids[1] + '"] rect')).toHaveAttribute("x", "495");
  await button(page, "Timeline").click();
  const program = page.getByRole("img", { name: "Program Canvas editorial study: not native output", exact: true });
  await expect(program.locator('[data-canvas-node-id="' + context.ids[1] + '"] rect')).toHaveAttribute("x", "495");
  await button(page, "Canvas").click();
  await button(page, "Arrange objects").click();
  await undoArrangement(page, saved.revision + 1);
  expect((await readStudioProject(page)).scenes).toEqual(base.scenes);
});
