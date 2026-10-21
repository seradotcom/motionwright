import { expect, test, type Page } from "@playwright/test";
import type { InspectorFixtureRequest } from "./fixtures/scene-inspector-authority";

// Actual Inspector and canonical ProjectMutationGate with controlled replies.
// These are synthetic component regressions, not StudioService or SQLite proof.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const objective = (page: Page) => page.getByLabel("Inspector scene objective", { exact: true });
const duration = (page: Page) => page.getByLabel("Scene duration seconds", { exact: true });
const requests = (page: Page) => page.evaluate(() => window.__INSPECTOR_AUTHORITY__.requests());
async function submit(page: Page, name = "Commit objective"): Promise<InspectorFixtureRequest> {
  const count = (await requests(page)).length;
  await button(page, name).click();
  await expect.poll(async () => (await requests(page)).length).toBe(count + 1);
  return (await requests(page))[count];
}
async function remount(page: Page) {
  await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.unmount());
  await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.mount());
}
test.beforeEach(async ({ page }) => {
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/tests/fixtures/scene-inspector-authority.html");
  await expect(page.locator(".inspector")).toBeVisible();
});
test("causal: exact objective survives scene A-B-A selection", async ({ page }) => {
  const ids = await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.project().scenes.map(scene => scene.id));
  await objective(page).fill("  Authored objective A\n  unfinished  ");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.selectScene(id), ids[1]);
  await objective(page).fill("Separate objective B");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.selectScene(id), ids[0]);
  await expect(objective(page)).toHaveValue("  Authored objective A\n  unfinished  ");
  expect(await requests(page)).toEqual([]);
});
test("causal: exact objective survives remount and reload", async ({ page }) => {
  await objective(page).fill("  Still writing\n  retain all spacing  ");
  await remount(page);
  await expect.soft(objective(page)).toHaveValue("  Still writing\n  retain all spacing  ");
  await page.reload();
  await expect(objective(page)).toHaveValue("  Still writing\n  retain all spacing  ");
  expect(await requests(page)).toEqual([]);
});
test("causal: incomplete duration is never coerced and survives remount", async ({ page }) => {
  await duration(page).evaluate((element, value) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  }, "1001/");
  await expect.soft(duration(page)).toHaveValue("1001/");
  await remount(page);
  await expect(duration(page)).toHaveValue("1001/");
  await expect(button(page, "Ripple")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
});
for (const field of ["id", "generation", "active_branch"] as const) {
  test("causal: raw Inspector belongs to its " + field + " through A-B-A", async ({ page }) => {
    const prior = await page.evaluate(field => window.__INSPECTOR_AUTHORITY__.project()[field], field);
    await objective(page).fill("  Owned by A  ");
    await page.evaluate(({ field, value }) => window.__INSPECTOR_AUTHORITY__.setIdentity(field, value),
      { field, value: "20000000-0000-4000-8000-000000000001" });
    await objective(page).fill("Owned by B");
    await page.evaluate(({ field, value }) => window.__INSPECTOR_AUTHORITY__.setIdentity(field, value), { field, value: prior });
    await expect(objective(page)).toHaveValue("  Owned by A  ");
    expect(await requests(page)).toEqual([]);
  });
}
test("causal: original objective acknowledgement retains later input", async ({ page }) => {
  await objective(page).fill("Submitted objective A");
  const request = await submit(page);
  await objective(page).fill("  Later objective B  ");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolve(id), request.id);
  await expect(objective(page)).toHaveValue("  Later objective B  ");
  await remount(page);
  await expect(objective(page)).toHaveValue("  Later objective B  ");
  expect(await requests(page)).toHaveLength(1);
});
test("causal: matching observed source cannot replace later input or acknowledge", async ({ page }) => {
  await objective(page).fill("Original submitted objective");
  const request = await submit(page);
  await objective(page).fill("  Later text remains mine  ");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.observeOriginal(id), request.id);
  await expect(objective(page)).toHaveValue("  Later text remains mine  ");
  await expect(button(page, "Commit objective")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
});
test("causal: denied recovery blocks transport and keeps author input", async ({ page }) => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("motionwright.scene-inspector-recovery.")) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, key, value);
    };
    window.__INSPECTOR_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
  });
  await objective(page).fill("  Recover this input before sending  ");
  await expect(button(page, "Commit objective")).toBeDisabled();
  await remount(page);
  await expect(objective(page)).toHaveValue("  Recover this input before sending  ");
  expect(await requests(page)).toEqual([]);
});
test("causal: uncertain original cannot be replayed after rejection or remount", async ({ page }) => {
  await objective(page).fill("Uncertain Inspector intent");
  const request = await submit(page);
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.reject(id, "Original reply is uncertain"), request.id);
  await expect(button(page, "Commit objective")).toBeDisabled();
  await remount(page);
  await expect(objective(page)).toHaveValue("Uncertain Inspector intent");
  await expect(button(page, "Commit objective")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
});
test("control: original objective reply updates the saved scene", async ({ page }) => {
  await objective(page).fill("Saved Inspector objective");
  const request = await submit(page);
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolve(id), request.id);
  await expect(objective(page)).toHaveValue("Saved Inspector objective");
  await expect(button(page, "Commit objective")).toBeDisabled();
  expect(await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.project().scenes[0].objective)).toBe("Saved Inspector objective");
});
test("control: clean Inspector follows a saved external objective", async ({ page }) => {
  const id = await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.sceneId());
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.externalChange({ type: "update_scene_objective", scene_id: id, objective: "External saved objective" }), id);
  await expect(objective(page)).toHaveValue("External saved objective");
  expect(await requests(page)).toEqual([]);
});

test("exact fraction request retains later incomplete duration through original ACK and reload", async ({ page }) => {
  await duration(page).fill("  1001/30  ");
  const request = await submit(page, "Ripple");
  expect(request.change).toMatchObject({ type: "set_scene_duration", duration: { num: "1001", den: "30" } });
  await duration(page).fill("1e");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolve(id), request.id);
  await expect(duration(page)).toHaveValue("1e");
  await page.reload();
  await expect(duration(page)).toHaveValue("1e");
  await expect(button(page, "Ripple")).toBeDisabled();
});
test("unrelated r+1 original reply leaves UNKNOWN and raw input", async ({ page }) => {
  await objective(page).fill("Exact operation required");
  const request = await submit(page);
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolveWithoutOperation(id), request.id);
  await expect(objective(page)).toHaveValue("Exact operation required");
  await expect(page.getByRole("region", { name: "Unconfirmed Inspector save", exact: true })).toBeVisible();
  await expect(button(page, "Commit objective")).toBeDisabled();
});
for (const kind of ["recovery", "pointer"] as const) {
  test("silent " + kind + " loss blocks dispatch until verified explicit save", async ({ page }) => {
    await page.evaluate(kind => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        const prefix = kind === "recovery" ? "motionwright.scene-inspector-recovery." : "motionwright.scene-inspector-writer.";
        if (key.startsWith(prefix)) return;
        return original.call(this, key, value);
      };
      window.__INSPECTOR_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
    }, kind);
    await objective(page).fill("  Verify storage before dispatch  ");
    await expect(button(page, "Commit objective")).toBeDisabled();
    expect(await requests(page)).toEqual([]);
    await page.evaluate(() => window.__INSPECTOR_TEST_RESTORE__!());
    await button(page, "Verify Inspector recovery copy").click();
    await expect(button(page, "Commit objective")).toBeEnabled();
    expect(await requests(page)).toEqual([]);
    const request = await submit(page);
    await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolve(id), request.id);
    await expect(objective(page)).toHaveValue("Verify storage before dispatch");
  });
}
test("UNKNOWN survives a real page reload without request replay", async ({ page }) => {
  await objective(page).fill("  Original uncertain raw  ");
  const request = await submit(page);
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.observeOriginal(id), request.id);
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.reject(id, "Reply lost"), request.id);
  await page.reload();
  await expect(objective(page)).toHaveValue("  Original uncertain raw  ");
  await expect(button(page, "Commit objective")).toBeDisabled();
  await expect(page.getByRole("region", { name: "Unconfirmed Inspector save", exact: true })).toBeVisible();
  expect(await requests(page)).toEqual([]);
});
async function originalCopy(page: Page) {
  return page.evaluate(() => {
    const project = window.__INSPECTOR_AUTHORITY__.project(), scope = JSON.stringify([
      project.id, project.generation, project.active_branch, window.__INSPECTOR_AUTHORITY__.sceneId(),
    ]);
    const writer = sessionStorage.getItem("motionwright.scene-inspector-writer.v1:" + encodeURIComponent(scope));
    const key = "motionwright.scene-inspector-recovery.v1:" + encodeURIComponent(scope) + ":" + writer;
    const raw = localStorage.getItem(key);
    if (!raw) throw new Error("No original Inspector custody");
    return { key, raw, value: JSON.parse(raw) };
  });
}
for (const later of [false, true]) {
  test("explicit inspected UNKNOWN keeps original copy before a separate " + (later ? "later" : "clean") + " intent", async ({ page }) => {
    await objective(page).fill("Original scene intent");
    const first = await submit(page);
    await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.observeOriginal(id), first.id);
    await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.reject(id, "Original delivery remains uncertain"), first.id);
    if (later) await objective(page).fill("  Separate scene intent  ");
    const original = await originalCopy(page);
    expect(original.value.draft.pending.outcome).toBe("UNKNOWN");
    await button(page, "Read saved scene and history").click();
    await expect(page.getByRole("region", { name: "Saved Inspector outcome", exact: true }))
      .toContainText("do not identify or acknowledge the original request");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await requests(page)).toHaveLength(1);
    await button(page, "Keep original attempt and start a separate scene edit").click();
    await expect(objective(page)).toHaveValue(later ? "  Separate scene intent  " : "Original scene intent");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await requests(page)).toHaveLength(1);
    if (later) {
      const second = await submit(page), separate = await originalCopy(page);
      expect(separate.value.draft.pending.id).not.toBe(original.value.draft.pending.id);
      expect(second.source.revision).toBe(first.source.revision + 1);
      await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolve(id), second.id);
      await expect(objective(page)).toHaveValue("Separate scene intent");
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    } else await expect(button(page, "Commit objective")).toBeDisabled();
  });
}
test("stale author input requires explicit retention against the current source", async ({ page }) => {
  await objective(page).fill("  Keep this objective  ");
  const original = await originalCopy(page);
  const id = await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.sceneId());
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.externalChange({ type: "set_scene_status", scene_id: id, status: "review" }), id);
  await expect(objective(page)).toHaveValue("  Keep this objective  ");
  await expect(button(page, "Commit objective")).toBeDisabled();
  await button(page, "Retain Inspector input against current revision").click();
  await expect(button(page, "Commit objective")).toBeEnabled();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  expect(await requests(page)).toEqual([]);
});
test("hidden original callback cannot overwrite another selected scene input", async ({ page }) => {
  const ids = await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.project().scenes.map(scene => scene.id));
  await objective(page).fill("Scene A submitted");
  const first = await submit(page);
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.selectScene(id), ids[1]);
  await objective(page).fill("  Scene B current input  ");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.resolve(id), first.id);
  await expect(objective(page)).toHaveValue("  Scene B current input  ");
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.selectScene(id), ids[0]);
  await expect(objective(page)).toHaveValue("Scene A submitted");
  expect(await requests(page)).toHaveLength(1);
});
test("empty selection and return retain the scene's author buffer", async ({ page }) => {
  const id = await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.sceneId());
  await objective(page).fill("  Retain through empty selection  ");
  await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.selectScene(""));
  await expect(page.getByText("Nothing selected", { exact: true })).toBeVisible();
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.selectScene(id), id);
  await expect(objective(page)).toHaveValue("  Retain through empty selection  ");
});
test("copy overflow blocks without eviction or transport", async ({ page }) => {
  await page.evaluate(() => {
    for (let i = 0; i < 256; i++) localStorage.setItem("motionwright.scene-inspector-recovery.v1:existing:" + i, "retained-" + i);
  });
  await objective(page).fill("  New input remains in memory  ");
  await expect(button(page, "Commit objective")).toBeDisabled();
  await expect(objective(page)).toHaveValue("  New input remains in memory  ");
  expect(await page.evaluate(() => Array.from({ length: 256 }, (_, i) =>
    localStorage.getItem("motionwright.scene-inspector-recovery.v1:existing:" + i)))).toEqual(Array.from({ length: 256 }, (_, i) => "retained-" + i));
  expect(await requests(page)).toEqual([]);
});
test("content and timing locks disable only their existing author fields", async ({ page }) => {
  const id = await page.evaluate(() => window.__INSPECTOR_AUTHORITY__.sceneId());
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.externalChange({ type: "set_lock", resource: "scene:" + id, kind: "content", note: "Keep content" }), id);
  await expect(objective(page)).toBeDisabled(); await expect(duration(page)).toBeEnabled();
  await page.evaluate(id => window.__INSPECTOR_AUTHORITY__.externalChange({ type: "set_lock", resource: "scene:" + id, kind: "timing", note: "Keep timing" }), id);
  await expect(duration(page)).toBeDisabled(); await expect(button(page, "Ripple")).toBeDisabled();
});
