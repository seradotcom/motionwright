import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import { assertSavedArrangement, createArrangementScene, selectArrangement, stageAndInspectArrangement,
  undoArrangement, verifyArrangementSelection } from "./support/canvasArrangementJourney";
import { proposalButton as button } from "./support/scopedPaintMotionJourney";
import type { CreativePatch } from "../src/creativeProduction";
import type { CanvasNode } from "../src/types";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "An exact-source StudioService binary is required.");
test.describe.configure({ timeout: 180_000, retries: 0 });

test("Canvas arrangement uses real grants and SQLite, preserves camera and paths across restart, and undoes through the same journal", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "canvas-arrangement.sqlite3"), desktopInvokeFixture: true });
  let finalized = false;
  try {
    const boot = await service.fixtureBootstrap();
    await service.attachFixturePage(page, boot.project.id);
    await page.goto("/");
    const context = await createArrangementScene(page), base = await service.project(boot.project.id);
    await selectArrangement(page, context.ids);
    await page.reload();
    await button(page, "Canvas").click();
    await page.locator(".tree-row").filter({ hasText: context.name }).click();
    await button(page, "Arrange objects").click();
    await verifyArrangementSelection(page, context.ids);
    await stageAndInspectArrangement(page, context.ids[1]);
    expect(await service.project(base.id)).toEqual(base);
    expect(await service.history(base.id, base.revision, 8)).toEqual([]);
    const node = base.scenes.find(scene => scene.id === context.sceneId)!.nodes.find(item => item.id === context.ids[1])!;
    const patch: CreativePatch = { scene_id: context.sceneId, base_revision: base.revision,
      rationale: "Distribute horizontal gaps for 3 sibling object frames. Translate authored motion with each moved frame; preserve timing, curves, style, hierarchy and layer order.",
      edits: [
        { kind: "transform", node_id: node.id, transform: { x: 500, y: node.y, width: node.width, height: node.height, rotation_deg: node.rotation_deg, opacity: node.opacity } },
        { kind: "motion", node_id: node.id, keyframes: node.keyframes.map(key => ({ ...key, value: key.value + (key.property === "x" ? 20 : 0) })) },
      ] };
    const preview = await service.invoke<{before:CanvasNode[];after:CanvasNode[]}>("preview_creative_patch", { request: {
      project_id: base.id, generation: base.generation, revision: base.revision, patch,
    } });
    await button(page, "Apply as one revision").click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (base.revision + 1));
    const saved = await service.project(base.id);
    assertSavedArrangement(base, saved, context.sceneId, context.ids);
    expect(saved.production_design!.patches!.at(-1)!.after).toEqual(preview.after);
    await service.close(); await service.start(); await page.reload();
    await button(page, "Canvas").click();
    await page.locator(".tree-row").filter({ hasText: context.name }).click();
    await button(page, "Arrange objects").click();
    expect(await service.project(base.id)).toEqual(saved);
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await undoArrangement(page, saved.revision + 1);
    const final = await service.project(base.id);
    expect(final.scenes).toEqual(base.scenes);
    expect(final.production_design!.camera_reframes).toEqual(base.production_design!.camera_reframes);
    expect((await service.history(base.id, base.revision, 8)).map(event => event.change.type))
      .toEqual(["apply_creative_patch", "undo_creative_patch"]);
    const closure = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [async () => { await page.close(); }]);
    finalized = true;
    expect(closure.oracle?.project).toEqual(final);
    expect(service.lifecycleTaints).toEqual([]);
  } finally {
    if (!page.isClosed()) await page.close();
    if (!finalized) await service.close();
  }
});
