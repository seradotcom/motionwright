import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { CreativePatch } from "../src/creativeProduction";
import type { Project } from "../src/types";
import { nodeTransform } from "../src/scopedChangeDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// This opt-in suite uses the real Rust service, SQLite, CAS and grants. The
// invoke seam is a process test bridge; it does not exercise Tauri or a renderer.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session and set MOTIONWRIGHT_SERVICE_TEST_BIN for real persisted-service coverage.");

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
async function openProposal(page: Page, sceneId: string) {
  await button(page, "Production").click();
  await page.getByLabel("Production working scene").selectOption(sceneId);
  await button(page, "Scoped changes").click();
}
async function queueText(page: Page, nodeId: string, text: string, reason: string) {
  await page.getByLabel("Patch target object").selectOption(nodeId);
  await page.getByLabel("Patch edit kind").selectOption("text");
  await page.getByLabel("Patch replacement text").fill(text);
  await button(page, "Add to proposal").click();
  await page.getByLabel("Patch rationale").fill(reason);
}
async function evidence(testInfo: TestInfo, services: ScopedChangeService[], project: Project | undefined, prepare: (() => Promise<unknown>)[] = []) {
  let finalization: StoreFinalization | undefined;
  try {
    finalization = await services[0].finalizeClosedStore(testInfo.outputPath("closed-store-1"), project, services.slice(1), prepare);
  } finally {
    for (const [index, service] of services.entries()) {
      const path = testInfo.outputPath(`service-${index + 1}-transcript.json`);
      await writeFile(path, JSON.stringify({
        evidenceScope: "real_service_sqlite_cas; excludes_tauri_installer_native_render",
        fixture: "synthetic_test_fixture", databasePath: service.databasePath,
        stderr: service.stderr, transcript: service.transcript,
        processExits: service.processExits, lifecycleTaints: service.lifecycleTaints,
      }, null, 2));
      await testInfo.attach(`real-service-${index + 1}-transcript`, { path, contentType: "application/json" });
    }
    const path = testInfo.outputPath("persisted-project.json");
    await writeFile(path, JSON.stringify(project ?? null, null, 2));
    await testInfo.attach("persisted-project", { path, contentType: "application/json" });
    const oraclePath = testInfo.outputPath("closed-store-1-outcomes.json");
    await testInfo.attach("complete-store-finalization", { path: oraclePath, contentType: "application/json" });
    if (finalization) expect(finalization.oracle?.project).toEqual(project);
  }
}

test("Studio previews, commits, reopens, undoes and redoes against the real persisted service", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let final: Project | undefined;
  try {
    const boot = await service.fixtureBootstrap();
    const initial = boot.project;
    const scene = initial.scenes.find(item => item.id === boot.fixture.scene_id)!;
    const target = scene.nodes.find(item => item.id === boot.fixture.node_id)!;
    const other = scene.nodes.find(item => item.kind === "text" && item.id !== target.id)!;
    expect(boot.test_transport).toBe("service-test-jsonl");
    expect(initial.assets.some(asset => asset.content_sha256 === boot.fixture.asset_sha256)).toBe(true);
    const text = "A reviewed line stored by the real StudioService.";
    const pendingText = "This second input stays outside the reviewed queue.";
    const rawX = "1e";
    const expectPendingInputs = async () => {
      await expect(page.getByLabel("Patch target object")).toHaveValue(other.id);
      await page.getByLabel("Patch edit kind").selectOption("transform");
      await expect(page.getByLabel("Patch x", { exact: true })).toHaveValue(rawX);
      await expect(page.getByLabel("Patch x", { exact: true })).toHaveAttribute("aria-invalid", "true");
      await expect(button(page, "Add to proposal")).toBeDisabled();
      await page.getByLabel("Patch edit kind").selectOption("text");
      await expect(page.getByLabel("Patch replacement text")).toHaveValue(pendingText);
    };
    const rationale = "Shorten the body and move only its authored position.";
    const transform = { ...nodeTransform(target), x: target.x + 18 };
    const patch: CreativePatch = { scene_id: scene.id, base_revision: initial.revision, rationale,
      edits: [{ kind: "text", node_id: target.id, text }, { kind: "transform", node_id: target.id, transform }] };
    await service.attachFixturePage(page);
    await page.goto("/");
    await openProposal(page, scene.id);
    await page.getByLabel("Patch target object").selectOption(other.id);
    await page.getByLabel("Patch edit kind").selectOption("transform");
    await page.getByLabel("Patch x", { exact: true }).fill(rawX);
    await expect(page.getByLabel("Patch x", { exact: true })).toHaveValue(rawX);
    await expect(button(page, "Add to proposal")).toBeDisabled();
    await queueText(page, target.id, text, rationale);
    await page.getByLabel("Patch edit kind").selectOption("transform");
    await page.getByLabel("Patch x", { exact: true }).fill(String(transform.x));
    await button(page, "Add to proposal").click();
    await page.getByLabel("Patch target object").selectOption(other.id);
    await expect(page.getByLabel("Patch x", { exact: true })).toHaveValue(rawX);
    await expect(button(page, "Add to proposal")).toBeDisabled();
    await page.getByLabel("Patch edit kind").selectOption("text");
    await page.getByLabel("Patch replacement text").fill(pendingText);
    await expect(button(page, "Add to proposal")).toBeEnabled();
    await button(page, "Preview scoped changes").click();
    await expect(page.getByText("Editorial diff prepared", { exact: true })).toBeVisible();
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    const domainPreview = await service.invoke("preview_creative_patch", { request: {
      project_id: initial.id, generation: initial.generation, revision: initial.revision, patch,
    } });
    const editorialPreview = await page.evaluate(async ({ project, patch }) => {
      const path = "/src/api.ts";
      const api = await import(path);
      return api.previewCreativePatch(project, patch);
    }, { project: initial, patch });
    expect(editorialPreview).toEqual(domainPreview);
    await page.screenshot({ path: testInfo.outputPath("real-service-reviewed-proposal.png"), fullPage: true });
    const playhead = page.getByLabel("Comparison playhead", { exact: true });
    await playhead.press("Home");
    await playhead.press("PageUp");
    await playhead.press("PageUp");
    expect(Number(await playhead.inputValue())).toBeGreaterThan(0);
    await page.locator(".production-compare-grid").screenshot({ path: testInfo.outputPath("real-service-editorial-before-after.png") });
    await button(page, "Apply as one revision").dblclick();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${initial.revision + 1}`);
    const committed = await service.project(initial.id);
    expect(committed.revision).toBe(initial.revision + 1);
    expect(committed.production_design.patches.at(-1)?.before).toEqual(editorialPreview.before);
    expect(committed.production_design.patches.at(-1)?.after).toEqual(editorialPreview.after);
    expect(committed.scenes.filter(item => item.id !== scene.id)).toEqual(initial.scenes.filter(item => item.id !== scene.id));
    expect(committed.scenes.find(item => item.id === scene.id)!.nodes.filter(item => item.id !== target.id)).toEqual(scene.nodes.filter(item => item.id !== target.id));
    expect(committed.assets).toEqual(initial.assets);
    expect(committed.scenes.map(item => [item.id, item.start, item.duration])).toEqual(initial.scenes.map(item => [item.id, item.start, item.duration]));
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await expect(page.getByLabel("Patch replacement text")).toHaveValue(pendingText);
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
    await expectPendingInputs();

    await service.close();
    await service.start();
    await page.reload();
    await openProposal(page, scene.id);
    expect(await service.project(initial.id)).toEqual(committed);
    await expect(page.getByLabel("Patch replacement text")).toHaveValue(pendingText);
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await expectPendingInputs();

    // A later author's style decision is real persisted input, outside the
    // first proposal. Undo/redo must conserve it and the pending editor input.
    const currentTarget = committed.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
    const style = { ...currentTarget.style, fill: "#D9A46E" };
    const styled = await service.apply(committed, { type: "update_canvas_style", scene_id: scene.id, node_id: target.id, style });
    await page.reload();
    await openProposal(page, scene.id);
    await expect(page.locator(".production-patch")).toContainText("Your proposal is kept");
    await expectPendingInputs();
    await button(page, "Preview undo").click();
    await page.screenshot({ path: testInfo.outputPath("real-service-undo-preserves-author-input.png"), fullPage: true });
    await button(page, "Apply undo as new revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${styled.revision + 1}`);
    const undone = await service.project(initial.id);
    const undoneNode = undone.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
    expect(undoneNode.text).toBe(target.text);
    expect(nodeTransform(undoneNode)).toEqual(nodeTransform(target));
    expect(undoneNode.style).toEqual(style);
    await expect(page.getByLabel("Patch replacement text")).toHaveValue(pendingText);
    await expectPendingInputs();
    await button(page, "Preview redo").click();
    await button(page, "Apply undo as new revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${undone.revision + 1}`);
    const redone = await service.project(initial.id);
    const redoneNode = redone.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
    expect(redoneNode.text).toBe(text);
    expect(nodeTransform(redoneNode)).toEqual(transform);
    expect(redoneNode.style).toEqual(style);
    await expect(page.getByLabel("Patch replacement text")).toHaveValue(pendingText);
    await expectPendingInputs();
    await button(page, "Review on current revision").click();
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await button(page, "Add to proposal").click();
    await page.getByLabel("Patch rationale").fill("Complete the remaining independently authored input.");
    await button(page, "Preview scoped changes").click();
    await button(page, "Apply as one revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${redone.revision + 1}`);
    final = await service.project(initial.id);
    expect(final.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === other.id)?.text).toBe(pendingText);
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
    await expectPendingInputs();
    for (const stored of [committed, styled, undone, redone, final]) {
      const storedOther = stored.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === other.id)!;
      expect(nodeTransform(storedOther)).toEqual(nodeTransform(other));
    }
    expect(await service.history(initial.id, initial.revision)).toHaveLength(5);
    const journal = await service.history(initial.id, initial.revision);
    expect(journal.map(event => event.change)).toEqual([
      { type: "apply_creative_patch", patch },
      { type: "update_canvas_style", scene_id: scene.id, node_id: target.id, style },
      { type: "undo_creative_patch", patch_id: committed.production_design.patches.at(-1)!.id },
      { type: "undo_creative_patch", patch_id: undone.production_design.patches.at(-1)!.id },
      { type: "apply_creative_patch", patch: {
        scene_id: scene.id, base_revision: redone.revision,
        rationale: "Complete the remaining independently authored input.",
        edits: [{ kind: "text", node_id: other.id, text: pendingText }],
      } },
    ]);
    await service.close();
    await service.start();
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    await page.reload();
    await openProposal(page, scene.id);
    await expectPendingInputs();
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    await page.screenshot({ path: testInfo.outputPath("real-service-completed-editable-project.png"), fullPage: true });
  } finally {
    await evidence(testInfo, [service], final, [() => page.close()]);
  }
});

test("two Studio clients reject a stale commit and let the author resolve only the overlapping property", async ({ page, context }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let peer: ScopedChangeService | undefined;
  let secondPage: Page | undefined;
  let final: Project | undefined;
  try {
    peer = await service.peer();
    secondPage = await context.newPage();
    const initial = (await service.fixtureBootstrap()).project;
    const scene = initial.scenes[0];
    const target = scene.nodes.find(item => item.name === "ProductHeroReveal / body")!;
    await service.attachFixturePage(page);
    await peer.attachFixturePage(secondPage);
    await page.goto("/");
    await secondPage.goto("/");
    await openProposal(page, scene.id);
    await openProposal(secondPage, scene.id);
    await queueText(page, target.id, "Client A's preserved draft.", "First client proposes text and movement.");
    await page.getByLabel("Patch edit kind").selectOption("transform");
    await page.getByLabel("Patch x", { exact: true }).fill(String(target.x + 37));
    await button(page, "Add to proposal").click();
    await page.getByLabel("Patch edit kind").selectOption("text");
    await button(page, "Preview scoped changes").click();
    await queueText(secondPage, target.id, "Client B's persisted decision.", "Second client finishes its current revision first.");
    await button(secondPage, "Preview scoped changes").click();
    await button(secondPage, "Apply as one revision").click();
    await expect(secondPage.locator(".production-workspace-header p")).toContainText(`revision ${initial.revision + 1}`);
    await button(page, "Apply as one revision").click();
    await expect(page.locator(".error-banner")).toBeVisible();
    await expect(page.getByLabel("Patch replacement text")).toHaveValue("Client A's preserved draft.");
    expect((await service.project(initial.id)).revision).toBe(initial.revision + 1);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    await page.reload();
    await openProposal(page, scene.id);
    const conflicts = page.getByRole("region", { name: "Proposal conflicts", exact: true });
    await expect(conflicts).toContainText("text · value changed");
    await expect(button(page, "Review on current revision")).toBeDisabled();
    await conflicts.getByText("Compare original, current and proposed values", { exact: true }).click();
    await expect(conflicts).toContainText("Client A's preserved draft.");
    await expect(conflicts).toContainText("Client B's persisted decision.");
    await page.screenshot({ path: testInfo.outputPath("real-service-two-client-conflict.png"), fullPage: true });
    await conflicts.screenshot({ path: testInfo.outputPath("real-service-conflict-values.png") });
    await button(page, `Keep current text for ${target.name}`).click();
    await expect(page.getByLabel("Patch replacement text")).toHaveValue("Client B's persisted decision.");
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("/ transform");
    await button(page, "Review on current revision").click();
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    await button(page, "Preview scoped changes").click();
    await button(page, "Apply as one revision").click();
    await expect(page.locator(".production-workspace-header p")).toContainText(`revision ${initial.revision + 2}`);
    final = await service.project(initial.id);
    const edited = final.scenes.find(item => item.id === scene.id)!.nodes.find(item => item.id === target.id)!;
    expect(edited.text).toBe("Client B's persisted decision.");
    expect(edited.x).toBe(target.x + 37);
    expect(final.production_design.patches).toHaveLength(2);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(2);
    expect(await peer.reopen(initial.id)).toEqual(final);
  } finally {
    await evidence(testInfo, peer ? [service, peer] : [service], final,
      [() => page.close(), ...(secondPage ? [() => secondPage!.close()] : [])]);
  }
});
