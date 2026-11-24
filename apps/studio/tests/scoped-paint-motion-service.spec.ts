import { expect, test } from "@playwright/test";
import { ScopedChangeService } from "./support/scopedChangeService";
import { inspectPaintMotionProposal, leaveIncompletePaintMotionInput, openPaintMotionProposal, proposalButton,
  queuePaintMotionProposal, verifyIncompletePaintMotionInput } from "./support/scopedPaintMotionJourney";

// Opt-in actual StudioService + SQLite + scoped grants/CAS. The invoke bridge
// excludes Tauri delivery and physical Native frames; no fixture stands in for them.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "An exact-source StudioService binary is required.");
test.describe.configure({ timeout: 150_000, retries: 0 });

test("scoped style and complete animation commit together, survive SQLite restart and remain editable and reversible", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "scoped-motion.sqlite3"), desktopInvokeFixture: true });
  let finalized = false;
  try {
    const boot = await service.fixtureBootstrap(), initial = boot.project;
    const scene = initial.scenes.find(item => item.id === boot.fixture.scene_id)!;
    const target = scene.nodes.find(item => item.id === boot.fixture.node_id)!;
    await service.attachFixturePage(page, initial.id);
    await page.goto("/");
    await openPaintMotionProposal(page, scene.id);
    const proposed = await queuePaintMotionProposal(page, target);
    await leaveIncompletePaintMotionInput(page);
    await inspectPaintMotionProposal(page);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision, 8)).toEqual([]);
    const patch = { scene_id: scene.id, base_revision: initial.revision,
      rationale: "At the entrance, improve contrast and preserve the complete spatial pose while fading the label in.",
      edits: [{ kind: "style", node_id: target.id, style: proposed.style }, { kind: "motion", node_id: target.id, keyframes: proposed.keys }] };
    const domainPreview = await service.invoke("preview_creative_patch", { request: {
      project_id: initial.id, generation: initial.generation, revision: initial.revision, patch,
    } });
    expect(domainPreview.after).toEqual([{ ...target, style: proposed.style, keyframes: proposed.keys }]);
    await proposalButton(page, "Apply as one revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${initial.revision + 1}`);
    const saved = await service.project(initial.id);
    const savedNode = saved.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
    expect(savedNode).toEqual(domainPreview.after[0]);
    expect(saved.production_design.patches.at(-1)?.before).toEqual(domainPreview.before);
    expect(saved.production_design.patches.at(-1)?.after).toEqual(domainPreview.after);
    expect(saved.assets).toEqual(initial.assets);
    expect(saved.scenes.filter(item => item.id !== scene.id)).toEqual(initial.scenes.filter(item => item.id !== scene.id));
    await verifyIncompletePaintMotionInput(page);
    await service.close();
    await service.start();
    await page.reload();
    await openPaintMotionProposal(page, scene.id);
    expect(await service.project(initial.id)).toEqual(saved);
    await verifyIncompletePaintMotionInput(page);
    await expect(proposalButton(page, "Apply as one revision")).toBeDisabled();
    await proposalButton(page, "Discard proposal").click();
    await page.getByLabel("Patch target object").selectOption(target.id);
    await page.getByLabel("Patch edit kind").selectOption("style");
    await expect(page.getByLabel("Patch fill", { exact: true })).toHaveValue(proposed.style.fill!);
    await page.getByLabel("Patch edit kind").selectOption("motion");
    await expect(page.locator(".scoped-motion-row")).toHaveCount(12);
    await expect(page.getByLabel("Keyframe 7 time", { exact: true })).toHaveValue("1.5");
    await proposalButton(page, "Clear animation").click();
    await proposalButton(page, "Add to proposal").click();
    const clearReason = "Keep the approved label still for the evidence interval.";
    await page.getByLabel("Patch rationale").fill(clearReason);
    await inspectPaintMotionProposal(page);
    await proposalButton(page, "Apply as one revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${saved.revision + 1}`);
    const still = await service.project(initial.id);
    expect(still.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)).toEqual({ ...savedNode, keyframes: [] });
    const record = page.getByRole("region", { name: "Reversible scoped changes", exact: true }).locator("article").filter({ hasText: clearReason });
    await expect(record).toHaveCount(1);
    await record.getByRole("button", { name: "Preview undo", exact: true }).click();
    await proposalButton(page, "Apply undo as new revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${still.revision + 1}`);
    const final = await service.project(initial.id);
    expect(final.scenes).toEqual(saved.scenes);
    expect((await service.history(initial.id, initial.revision, 8)).map(event => event.change.type))
      .toEqual(["apply_creative_patch", "apply_creative_patch", "undo_creative_patch"]);
    await page.locator(".production-patch").screenshot({ path: info.outputPath("actual-service-scoped-motion.png") });
    const closure = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [async () => { await page.close(); }]);
    finalized = true;
    expect(closure.oracle?.project).toEqual(final);
    expect(service.lifecycleTaints).toEqual([]);
  } finally {
    if (!page.isClosed()) await page.close();
    if (!finalized) await service.close();
  }
});
