import { expect, test, type Page } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";
import { createScopedDraft, DRAFT_STORAGE_PREFIX, encodeScopedDraft, queueDraftEdit } from "../src/scopedChangeDraft";

// UI regressions only. The companion scoped-change-service suite crosses a
// real StudioService process, SQLite and CAS. These fixtures are not render or
// desktop evidence, and the deliberate delay below never changes preview data.
const scene = fixtureBootstrap.project.scenes[0];
const target = scene.nodes.find(node => node.kind === "text")!;
const second = scene.nodes.find(node => node.id !== target.id)!;
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const xInput = (page: Page) => page.getByLabel("Patch x", { exact: true });
async function openProposal(page: Page, sceneId = scene.id) {
  await button(page, "Production").click();
  await page.getByLabel("Production working scene").selectOption(sceneId);
  await button(page, "Scoped changes").click();
}

test("an empty scene waits for a valid scoped target before accepting object inputs", async ({ page }, testInfo) => {
  const emptyScene = fixtureBootstrap.project.scenes.find(item => item.nodes.length === 0)!;
  await page.goto("/");
  await openProposal(page, emptyScene.id);
  await expect(page.getByLabel("Patch replacement text")).toBeDisabled();
  await expect(page.getByLabel("Patch target object")).toBeDisabled();
  await expect(page.getByLabel("Patch edit kind")).toBeDisabled();
  await expect(button(page, "Add to proposal")).toBeDisabled();
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await expect(page.getByRole("region", { name: "Scoped proposal editor" })).toContainText("This scene has no objects to edit");
  await page.screenshot({ path: testInfo.outputPath("empty-target-controls.png"), fullPage: true });

  // Create a real browser-demo object through the existing Canvas UI, then
  // return to the same scene. No source or successful edit response is injected.
  await button(page, "Canvas").click();
  await button(page, "text").click();
  await expect(page.getByRole("button", { name: "Canvas object Text 1", exact: true })).toBeVisible();
  await openProposal(page, emptyScene.id);
  await expect(page.getByLabel("Patch target object")).toBeEnabled();
  await expect(page.getByLabel("Patch edit kind")).toBeEnabled();
  await expect(page.getByLabel("Patch replacement text")).toBeEnabled();
  await page.getByLabel("Patch replacement text").fill("Input anchored to the newly created object.");
  await page.getByLabel("Patch edit kind").selectOption("transform");
  for (const field of ["x", "y", "width", "height", "rotation_deg", "opacity"]) {
    await expect(page.getByLabel(`Patch ${field}`, { exact: true })).toBeEnabled();
  }
  const x = String(Number(await xInput(page).inputValue()) + 19);
  await xInput(page).fill(x);
  await button(page, "Components").click();
  await button(page, "Scoped changes").click();
  await expect(xInput(page)).toHaveValue(x);
  await page.getByLabel("Patch edit kind").selectOption("text");
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("Input anchored to the newly created object.");
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
  await expect(button(page, "Apply as one revision")).toBeDisabled();
});

for (const selectedKind of ["text", "transform"] as const) {
  test(`a removed scoped target keeps ${selectedKind} inputs recoverable until the object returns`, async ({ page }, testInfo) => {
    const key = DRAFT_STORAGE_PREFIX + createScopedDraft(fixtureBootstrap.project, scene).scope;
    const replacement = "Preserve this queued line while its object is unavailable.";
    const x = String(target.x + 31);
    await page.goto("/");
    await openProposal(page);
    await page.getByLabel("Patch target object").selectOption(target.id);
    await page.getByLabel("Patch replacement text").fill(replacement);
    await button(page, "Add to proposal").click();
    await page.getByLabel("Patch edit kind").selectOption("transform");
    await xInput(page).fill(x);
    await page.getByLabel("Patch edit kind").selectOption(selectedKind);
    await page.getByLabel("Patch rationale").fill("Keep the queued text and the separate transform input.");
    await expect.poll(() => page.evaluate(key => sessionStorage.getItem(key), key)).toContain("Keep the queued text and the separate transform input.");
    const saved = await page.evaluate(key => sessionStorage.getItem(key), key);

    await button(page, "Canvas").click();
    await page.locator(".canvas-tree-row").filter({ hasText: target.name }).click();
    await button(page, "Remove object").click();
    await expect(page.getByRole("button", { name: "Canvas object " + target.name, exact: true })).toHaveCount(0);
    await openProposal(page);
    await expect(page.getByLabel("Patch edit kind")).toBeDisabled();
    if (selectedKind === "text") {
      await expect(page.getByLabel("Patch replacement text")).toBeDisabled();
      await expect(page.getByLabel("Patch replacement text")).toHaveValue(replacement);
    } else {
      for (const field of ["x", "y", "width", "height", "rotation_deg", "opacity"]) {
        await expect(page.getByLabel(`Patch ${field}`, { exact: true })).toBeDisabled();
      }
      await expect(xInput(page)).toHaveValue(x);
    }
    await expect(page.getByRole("region", { name: "Proposal conflicts" })).toContainText("object unavailable");
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText(target.id);
    await expect(button(page, "Review on current revision")).toBeDisabled();
    await expect(button(page, "Apply as one revision")).toBeDisabled();
    // Decoding canonicalizes key order; preserve the complete draft value.
    const recovered = await page.evaluate(key => sessionStorage.getItem(key), key);
    expect(JSON.parse(recovered!)).toStrictEqual(JSON.parse(saved!));
    await page.screenshot({ path: testInfo.outputPath("unavailable-target-kept.png"), fullPage: true });

    // The browser demo reloads its fixture project, including this same stable
    // object identity. This checks draft recovery when the source is available
    // again; persisted project reopen is covered by the real-service suite.
    await page.reload();
    await openProposal(page);
    await expect(page.getByLabel("Patch edit kind")).toBeEnabled();
    await page.getByLabel("Patch edit kind").selectOption("text");
    await expect(page.getByLabel("Patch replacement text")).toBeEnabled();
    await expect(page.getByLabel("Patch replacement text")).toHaveValue(replacement);
    await page.getByLabel("Patch edit kind").selectOption("transform");
    await expect(xInput(page)).toBeEnabled();
    await expect(xInput(page)).toHaveValue(x);
    await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText(target.name);
    await expect(page.getByRole("region", { name: "Proposal conflicts" })).toHaveCount(0);
    await expect(button(page, "Apply as one revision")).toBeDisabled();
  });
}

test("uncommitted inputs survive changing fields, objects, tabs and reloading", async ({ page }, testInfo) => {
  await page.goto("/");
  await openProposal(page);
  const revision = await page.locator(".revision-chip").innerText();
  await page.getByLabel("Patch target object").selectOption(target.id);
  await page.getByLabel("Patch replacement text").fill("Author input kept before it enters the queue.");
  await page.getByLabel("Patch edit kind").selectOption("transform");
  await xInput(page).fill(String(target.x + 23));
  await page.getByLabel("Patch target object").selectOption(second.id);
  await page.getByLabel("Patch edit kind").selectOption("text");
  await page.getByLabel("Patch replacement text").fill("A second object's independent input.");
  await page.getByLabel("Patch target object").selectOption(target.id);
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("Author input kept before it enters the queue.");
  await page.getByLabel("Patch edit kind").selectOption("transform");
  await expect(xInput(page)).toHaveValue(String(target.x + 23));

  // Keep the incomplete browser input itself, not a coerced zero or a
  // pre-populated storage fixture. It stays outside the numeric proposal.
  const rawX = "1e";
  await xInput(page).fill(rawX);
  await expect(xInput(page)).toHaveValue(rawX);
  await expect(xInput(page)).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#scoped-transform-error")).toHaveText("Complete x with a finite number before adding this transform.");
  await expect(page.locator("#scoped-transform-error")).toBeVisible();
  await expect(button(page, "Add to proposal")).toBeDisabled();
  await page.getByLabel("Patch target object").selectOption(second.id);
  await page.getByLabel("Patch edit kind").selectOption("text");
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("A second object's independent input.");
  await page.getByLabel("Patch target object").selectOption(target.id);
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("Author input kept before it enters the queue.");
  await expect(button(page, "Add to proposal")).toBeEnabled();
  await page.getByLabel("Patch edit kind").selectOption("transform");
  await expect(xInput(page)).toHaveValue(rawX);
  await expect(button(page, "Add to proposal")).toBeDisabled();
  await button(page, "Components").click();
  await button(page, "Scoped changes").click();
  await expect(page.getByLabel("Patch edit kind")).toHaveValue("transform");
  await expect(xInput(page)).toHaveValue(rawX);
  await expect(button(page, "Add to proposal")).toBeDisabled();
  await page.reload();
  await openProposal(page);
  await expect(xInput(page)).toHaveValue(rawX);
  await expect(xInput(page)).toHaveAttribute("aria-invalid", "true");
  await expect(page.locator("#scoped-transform-error")).toBeVisible();
  await expect(button(page, "Add to proposal")).toBeDisabled();
  await page.getByLabel("Patch edit kind").selectOption("text");
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("Author input kept before it enters the queue.");
  await expect(button(page, "Add to proposal")).toBeEnabled();
  await page.getByLabel("Patch target object").selectOption(second.id);
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("A second object's independent input.");
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await expect(page.locator(".revision-chip")).toHaveText(revision);
  await page.screenshot({ path: testInfo.outputPath("recovered-unqueued-inputs.png"), fullPage: true });
});

test("late editorial previews cannot approve a changed or removed proposal", async ({ page }) => {
  let injected = false;
  await page.addInitScript(() => {
    const runtime = window as unknown as {
      __scopedPreviewResolvers: Array<() => void>;
      __scopedPreviewCompleted: number[];
      __delayScopedPreview: () => Promise<void>;
    };
    runtime.__scopedPreviewResolvers = [];
    runtime.__scopedPreviewCompleted = [];
    runtime.__delayScopedPreview = () => new Promise(resolve => runtime.__scopedPreviewResolvers.push(resolve));
  });
  await page.route("**/src/api.ts", async route => {
    const response = await route.fetch();
    const body = await response.text();
    const signature = "async function previewCreativePatch(project, patch) {";
    expect(body.split(signature)).toHaveLength(2);
    injected = true;
    const wrapper = signature + `
      const testRequest = window.__scopedPreviewResolvers.length;
      await window.__delayScopedPreview();
      try { return await undelayedScopedPreview(project, patch); }
      finally { window.__scopedPreviewCompleted.push(testRequest); }
    }
    async function undelayedScopedPreview(project, patch) {`;
    await route.fulfill({ response, body: body.replace(signature, wrapper) });
  });
  const count = () => page.evaluate(() => (window as unknown as { __scopedPreviewResolvers: unknown[] }).__scopedPreviewResolvers.length);
  const release = async (index: number) => {
    await page.evaluate(index => (window as unknown as { __scopedPreviewResolvers: Array<() => void> }).__scopedPreviewResolvers[index](), index);
    await expect.poll(() => page.evaluate(index => (window as unknown as { __scopedPreviewCompleted: number[] }).__scopedPreviewCompleted.includes(index), index)).toBe(true);
    // Let the actual async result and React paint finish before checking that
    // no stale review appeared. This is a render fence, not a timing guess.
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  };
  await page.goto("/");
  await openProposal(page);
  expect(injected).toBe(true);
  await page.getByLabel("Patch target object").selectOption(target.id);
  await page.getByLabel("Patch replacement text").fill("The queued replacement.");
  await button(page, "Add to proposal").click();
  await page.getByLabel("Patch rationale").fill("Original reason for a delayed review.");
  await button(page, "Preview scoped changes").click();
  await expect.poll(count).toBe(1);
  await page.getByLabel("Patch rationale").fill("The reason changed while the previous review was pending.");
  await release(0);
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await expect(page.getByText("Editorial diff prepared", { exact: true })).toHaveCount(0);
  await button(page, "Preview scoped changes").click();
  await expect.poll(count).toBe(2);
  await page.getByLabel("Patch rationale").fill("A newer review should survive the older result arriving last.");
  await button(page, "Preview scoped changes").click();
  await expect.poll(count).toBe(3);
  await release(2);
  await expect(page.getByText("Editorial diff prepared", { exact: true })).toBeVisible();
  await expect(button(page, "Apply as one revision")).toBeEnabled();
  await release(1);
  await expect(button(page, "Apply as one revision")).toBeEnabled();
  await page.getByLabel("Patch rationale").fill("A final review will outlive removal of its queued edit.");
  await button(page, "Preview scoped changes").click();
  await expect.poll(count).toBe(4);
  await page.getByRole("button", { name: "Remove proposed edit 1", exact: true }).click();
  await release(3);
  await expect(page.getByText("Editorial diff prepared", { exact: true })).toHaveCount(0);
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText("No edits queued");
});

test("an undo preview and its dismissal keep a separate uncommitted proposal", async ({ page }) => {
  const targetName = "ProductHeroReveal / body";
  await page.goto("/");
  // The original fixture's only populated scene has an intentional timing
  // lock. Author a new editable scene through the existing UI for this case.
  await button(page, "Add scene").click();
  await page.getByLabel("New scene name").fill("Uncommitted proposal history");
  await button(page, "Add").click();
  await button(page, "Production").click();
  await page.getByLabel("Production working scene").selectOption({ label: "Uncommitted proposal history" });
  await button(page, "Add to scene").click();
  await expect(button(page, "Update component")).toBeVisible();
  await button(page, "Scoped changes").click();
  await page.getByLabel("Patch target object").selectOption({ label: targetName });
  await page.getByLabel("Patch replacement text").fill("The first reviewed line.");
  await button(page, "Add to proposal").click();
  await page.getByLabel("Patch rationale").fill("A bounded reversible line.");
  await button(page, "Preview scoped changes").click();
  await button(page, "Apply as one revision").click();
  await expect(button(page, "Preview undo")).toBeEnabled();
  await button(page, "Discard proposal").click();
  await page.getByLabel("Patch target object").selectOption({ label: targetName });
  await page.getByLabel("Patch replacement text").fill("An independent proposal that is not yet applied.");
  await button(page, "Add to proposal").click();
  await page.getByLabel("Patch rationale").fill("Keep my proposal while inspecting history.");
  await button(page, "Preview scoped changes").click();
  await button(page, "Preview undo").click();
  await expect(page.locator(".production-patch .production-error")).toHaveCount(0);
  await expect(button(page, "Apply undo as new revision")).toBeEnabled();
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("An independent proposal that is not yet applied.");
  await expect(page.getByLabel("Patch rationale")).toHaveValue("Keep my proposal while inspecting history.");
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true })).toContainText(targetName);
  await button(page, "Back to proposal").click();
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await button(page, "Preview scoped changes").click();
  await expect(button(page, "Apply as one revision")).toBeEnabled();
});

test("corrupt recovery data remains untouched until it is explicitly discarded", async ({ page }) => {
  const key = "motionwright.scoped-change-draft.v1:" + JSON.stringify([
    fixtureBootstrap.project.id, fixtureBootstrap.project.generation,
    fixtureBootstrap.project.active_branch, scene.id,
  ]);
  await page.addInitScript(({ key }) => sessionStorage.setItem(key, "{broken proposal bytes"), { key });
  await page.goto("/");
  await openProposal(page);
  await expect(page.getByRole("alert")).toContainText("recovery copy has been left untouched");
  expect(await page.evaluate(key => sessionStorage.getItem(key), key)).toBe("{broken proposal bytes");
  await button(page, "Discard saved copy and current draft").click();
  expect(await page.evaluate(key => sessionStorage.getItem(key), key)).toBeNull();
  await expect(button(page, "Apply as one revision")).toBeDisabled();
});

test("a stale frame-bound draft can continue explicitly as manual work without losing inputs", async ({ page }) => {
  // Deliberately synthetic observation metadata. No frame or native renderer
  // response is fabricated; this tests the saved-input continuation only.
  const project = fixtureBootstrap.project;
  let draft = createScopedDraft(project, scene);
  draft = queueDraftEdit({ ...draft, editor: { ...draft.editor, text: "Keep this repair input for manual review." } });
  draft = { ...draft, baseRevision: project.revision - 1, rationale: "A synthetic earlier observation, without a renderer quality claim.",
    nativeSource: { schema: "motionwright.native-review-repair-draft/1", projectId: project.id,
      generation: project.generation, revision: project.revision - 1, sceneId: scene.id,
      profileId: project.deliverables[0].id, frameIndex: 3, frameSha256: "a".repeat(64),
      targetNodeId: target.id, editKind: "text", rationale: "Synthetic source context only" } };
  await page.addInitScript(({ key, raw }) => sessionStorage.setItem(key, raw), {
    key: DRAFT_STORAGE_PREFIX + draft.scope, raw: encodeScopedDraft(draft),
  });
  await page.goto("/");
  await openProposal(page);
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await button(page, "Continue as a manual proposal").click();
  await expect(page.getByLabel("Patch replacement text")).toHaveValue("Keep this repair input for manual review.");
  await expect(page.getByLabel("Patch rationale")).toHaveValue(/context only, not current renderer verification/);
  await expect(page.getByLabel("Patch rationale")).toHaveValue(new RegExp("a".repeat(64)));
  await expect(page.getByText(/Native frame 3 · source/)).toHaveCount(0);
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await button(page, "Review on current revision").click();
  await expect(button(page, "Apply as one revision")).toBeDisabled();
  await button(page, "Preview scoped changes").click();
  await expect(button(page, "Apply as one revision")).toBeEnabled();
});
