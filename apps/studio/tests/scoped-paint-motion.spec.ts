import { expect, test } from "@playwright/test";
import { inspectPaintMotionProposal, leaveIncompletePaintMotionInput, openPaintMotionProposal, proposalButton,
  queuePaintMotionProposal, readStudioProject, verifyIncompletePaintMotionInput } from "./support/scopedPaintMotionJourney";

// Browser fixture UX only. No Service, filesystem project persistence or Native
// frame claim is made by this test; the companion Service suite exercises SQLite.
test("scoped style and complete motion survive draft recovery, one-revision apply, undo and deliberate removal", async ({ page }, info) => {
  test.setTimeout(120_000);
  await page.goto("/");
  const initial = await readStudioProject(page);
  const scene = initial.scenes.find(item => Number(item.duration.num) / Number(item.duration.den) > 2 &&
    item.nodes.some(node => node.kind === "text" && !node.parent_id && !node.relations.length && !node.property_locks.length))!;
  expect(scene).toBeDefined();
  const target = scene.nodes.find(node => node.kind === "text" && !node.parent_id && !node.relations.length && !node.property_locks.length)!;
  await openPaintMotionProposal(page, scene.id);
  const proposed = await queuePaintMotionProposal(page, target);
  await leaveIncompletePaintMotionInput(page);
  expect(await readStudioProject(page)).toEqual(initial);
  await page.reload();
  await openPaintMotionProposal(page, scene.id);
  const restoredSource = await readStudioProject(page);
  expect(restoredSource.scenes).toEqual(initial.scenes);
  expect(restoredSource.revision).toBe(initial.revision);
  await expect(page.getByLabel("Patch target object")).toHaveValue(target.id);
  await verifyIncompletePaintMotionInput(page);
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true }).getByLabel(/Remove proposed edit/)).toHaveCount(2);
  // The fixture's approved timing is intentionally locked. Preview must reject
  // without writes; only the explicit Inspector action can release this lock.
  await proposalButton(page, "Preview scoped changes").click();
  await expect(page.locator(".production-patch [role=alert]")).toContainText("locked");
  await expect(proposalButton(page, "Apply as one revision")).toBeDisabled();
  expect(await readStudioProject(page)).toEqual(restoredSource);
  await proposalButton(page, "Storyboard").click();
  await page.locator(".tree-row").filter({ hasText: scene.name }).click();
  await expect(proposalButton(page, "Remove timing lock")).toHaveCount(1);
  await proposalButton(page, "Remove timing lock").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (initial.revision + 1));
  await openPaintMotionProposal(page, scene.id);
  await proposalButton(page, "Review on current revision").click();
  await verifyIncompletePaintMotionInput(page);
  const base = await readStudioProject(page);
  await inspectPaintMotionProposal(page);
  expect(await readStudioProject(page)).toEqual(base);
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 940 });
    await page.locator(".scoped-motion-keys").evaluate(element => { element.scrollTop = 0; });
    const row = page.locator(".scoped-motion-row").nth(0);
    const bounds = await row.boundingBox();
    expect(bounds).not.toBeNull();
    for (const input of await row.locator("input, select").all()) {
      const control = await input.boundingBox();
      expect(control).not.toBeNull();
      expect(control!.x).toBeGreaterThanOrEqual(bounds!.x);
      expect(control!.x + control!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
    }
    await page.getByRole("region", { name: "Complete animation proposal", exact: true }).screenshot({ path: info.outputPath(`scoped-motion-controls-${width}.png`) });
    await page.locator(".production-compare-grid").screenshot({ path: info.outputPath(`scoped-motion-comparison-${width}.png`) });
  }
  await proposalButton(page, "Apply as one revision").click();
  await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${base.revision + 1}`);
  const saved = await readStudioProject(page);
  const savedNode = saved.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
  expect(savedNode).toEqual({ ...target, style: proposed.style, keyframes: proposed.keys });
  expect(saved.production_design!.patches!.at(-1)!.source_revision).toBe(base.revision);
  await expect(proposalButton(page, "Apply as one revision")).toBeDisabled();
  await verifyIncompletePaintMotionInput(page);
  await proposalButton(page, "Discard proposal").click();
  const history = page.getByRole("region", { name: "Reversible scoped changes", exact: true });
  await history.getByRole("button", { name: "Preview undo", exact: true }).click();
  await proposalButton(page, "Apply undo as new revision").click();
  await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${saved.revision + 1}`);
  const undone = await readStudioProject(page);
  expect(undone.scenes).toEqual(base.scenes.map(item => item.id === scene.id ? { ...item, status: "draft" } : item));
  // Undo restores authoring values, never the prior human approval.
  await history.getByRole("button", { name: "Preview redo", exact: true }).click();
  await proposalButton(page, "Apply undo as new revision").click();
  await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${undone.revision + 1}`);
  expect((await readStudioProject(page)).scenes).toEqual(saved.scenes);
  await proposalButton(page, "Discard proposal").click();
  await page.getByLabel("Patch target object").selectOption(target.id);
  await page.getByLabel("Patch edit kind").selectOption("motion");
  await expect(page.locator(".scoped-motion-row")).toHaveCount(12);
  await proposalButton(page, "Clear animation").click();
  await proposalButton(page, "Add to proposal").click();
  await page.getByLabel("Patch rationale").fill("Hold this object still for the evidence interval.");
  await inspectPaintMotionProposal(page);
  await proposalButton(page, "Apply as one revision").click();
  await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${saved.revision + 3}`);
  const still = await readStudioProject(page);
  const stillNode = still.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
  expect(stillNode).toEqual({ ...savedNode, keyframes: [] });
});
