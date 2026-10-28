import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { recipeDraftScope, recipeDraftStorageKey } from "../src/deliveryRecipe";
import { recipePointerKey, type RecipeAuthorState } from "../src/deliveryRecipeDraft";
import type { Project } from "../src/types";
import type { RecipeFixtureRequest } from "./fixtures/delivery-recipe-authority";

// The actual Delivery components and browser API, with deliberately controlled
// original replies. This fixture does not claim StudioService, SQLite or native QC.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const lufs = (page: Page) => page.getByRole("textbox", { name: "Integrated loudness target (LUFS)", exact: true });
const peak = (page: Page) => page.getByRole("textbox", { name: "True peak ceiling (dBTP)", exact: true });
const review = (page: Page) => page.getByRole("region", { name: "Audio targets draft review", exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Audio targets unconfirmed save", exact: true });
const requests = (page: Page) => page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.requests());
const project = (page: Page) => page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
const copies = (page: Page) => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.delivery-recipe-draft/1:") && key.includes(',"targets"]:'))
  .map(key => ({ key, raw: localStorage.getItem(key)! })));
async function open(page: Page) {
  await page.goto("/tests/fixtures/delivery-recipe-authority.html?group=film"); await expect(review(page)).toBeVisible();
}
async function save(page: Page): Promise<RecipeFixtureRequest> {
  const before = (await requests(page)).length; await button(page, "Save audio targets").click();
  await expect.poll(async () => (await requests(page)).length).toBe(before + 1); return (await requests(page))[before];
}
const resolve = (page: Page, id: number) => page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), id);
const recipe = (request: RecipeFixtureRequest) => {
  if (request.change.type !== "set_delivery_recipe") throw new Error("Expected the existing whole-recipe change");
  return request.change.recipe;
};
async function profile(page: Page, name: string) { await page.locator(".delivery-profile-row").filter({ hasText: name }).click(); }
async function activeTargetCopy(page: Page, owner: Project, profileId: string) {
  const scope = recipeDraftScope(owner, profileId, "targets"), pointer = recipePointerKey(scope);
  const writer = await page.evaluate(key => sessionStorage.getItem(key), pointer);
  if (!writer) throw new Error("The target editor has not published a writer pointer.");
  const key = recipeDraftStorageKey(scope, writer), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("The target editor recovery copy is missing.");
  return { scope, pointer, writer, key, raw, state: (JSON.parse(raw) as { state: RecipeAuthorState }).state };
}
async function failFirstTargetPointerRead(page: Page, pointer: string) {
  const marker = "motionwright.test.targets-first-read:" + randomUUID();
  await page.addInitScript(({ pointer, marker }) => {
    const original = Storage.prototype.getItem;
    if (original.call(sessionStorage, marker) === "fired") return;
    let armed = true;
    Storage.prototype.getItem = function(key) {
      if (armed && this === sessionStorage && key === pointer) {
        armed = false; sessionStorage.setItem(marker, "fired"); throw new Error("Injected first target pointer read failure.");
      }
      return original.call(this, key);
    };
  }, { pointer, marker });
  return marker;
}

for (const raw of ["  1e  ", "-23.00000000000000000001"]) test("invalid raw target survives navigation, first-read failure, Retry and reload without a command: " + raw, async ({ page }) => {
  await open(page); const initial = await project(page); await lufs(page).fill(raw); await peak(page).fill("-1.001");
  await expect(button(page, "Save audio targets")).toBeDisabled(); await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("mix"));
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("film")); await expect(lufs(page)).toHaveValue(raw);
  const originalCopy = await activeTargetCopy(page, initial, initial.deliverables[0].id), marker = await failFirstTargetPointerRead(page, originalCopy.pointer);
  await page.reload(); await expect(review(page)).toBeVisible(); await expect(review(page).getByRole("alert")).toContainText("first target pointer read failure");
  expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired"); await expect(button(page, "Save audio targets")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  await button(page, "Retry audio targets recovery copy").click(); await expect(lufs(page)).toHaveValue(raw); await expect(peak(page)).toHaveValue("-1.001");
  const recoveredCopy = await activeTargetCopy(page, initial, initial.deliverables[0].id);
  expect(recoveredCopy.writer).not.toBe(originalCopy.writer); expect(recoveredCopy.state).toEqual(originalCopy.state); expect(await requests(page)).toEqual([]);
  await page.reload(); await expect(review(page)).toBeVisible(); await expect(lufs(page)).toHaveValue(raw); await expect(peak(page)).toHaveValue("-1.001");
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  await expect(button(page, "Save audio targets")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  // The fixture seeds its browser project on load. Compare the saved recipe,
  // never reinterpret its reseeding as persistence evidence.
  expect((await project(page)).production_design?.delivery_recipes).toEqual(initial.production_design?.delivery_recipes);
});

test("save and explicit clear preserve the complete saved Film/mix recipe", async ({ page }) => {
  await open(page); const initial = await project(page), selected = initial.deliverables[0], original = initial.production_design!.delivery_recipes![selected.id];
  await lufs(page).fill("  -023.0010  "); await peak(page).fill("-1.125"); const first = await save(page);
  expect(recipe(first)).toEqual({ ...original, audio_targets: { target_lufs: -23.001, target_true_peak_dbtp: -1.125 } }); await resolve(page, first.id);
  await expect(lufs(page)).toHaveValue("-23.001"); await expect(peak(page)).toHaveValue("-1.125");
  await expect(review(page)).toContainText("Saved recipe at revision");
  await button(page, "Clear saved audio targets").click(); await expect(lufs(page)).toHaveValue(""); await expect(peak(page)).toHaveValue("");
  expect(await requests(page)).toHaveLength(1); expect((await project(page)).production_design!.delivery_recipes![selected.id].audio_targets).toEqual({ target_lufs: -23.001, target_true_peak_dbtp: -1.125 });
  const clearing = await save(page); expect(recipe(clearing)).toEqual(original); await resolve(page, clearing.id);
  expect(Object.hasOwn((await project(page)).production_design!.delivery_recipes![selected.id], "audio_targets")).toBe(false);
  expect((await project(page)).audio.mix).toEqual(initial.audio.mix);
});

test("a targets-only recipe saves non-null, and clearing the last target explicitly clears the recipe", async ({ page }) => {
  await open(page); const initial = await project(page), id = initial.deliverables[0].id;
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.externalChange({ type: "set_delivery_recipe", deliverable_profile_id: id, recipe: null }), id);
  await lufs(page).fill("-14"); const first = await save(page);
  expect(recipe(first)).toEqual({ schema: "motionwright.delivery-recipe/1", scene_intents: [], original_mix: null, audio_targets: { target_lufs: -14, target_true_peak_dbtp: null } });
  await resolve(page, first.id); await expect(lufs(page)).toHaveValue("-14");
  await button(page, "Clear saved audio targets").click(); const clearing = await save(page); expect(recipe(clearing)).toBeNull(); await resolve(page, clearing.id);
  expect((await project(page)).production_design?.delivery_recipes?.[id] ?? null).toBeNull();
});

test("matching props from another author remain UNKNOWN until explicit current-history review", async ({ page }) => {
  await open(page); await lufs(page).fill("-14"); await peak(page).fill("-1"); const first = await save(page);
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Original reply unavailable"), first.id);
  await page.evaluate(change => window.__DELIVERY_RECIPE_AUTHORITY__.externalChange(change), first.change);
  await expect(unknown(page)).toBeVisible(); await expect(button(page, "Save audio targets")).toBeDisabled();
  const retained = (await copies(page)).filter(copy => JSON.parse(copy.raw).state.pending?.outcome === "UNKNOWN"); expect(retained.length).toBeGreaterThan(0);
  const before = await project(page); await unknown(page).getByRole("button", { name: "Read saved recipe and history", exact: true }).click();
  await expect(unknown(page)).toContainText("Matching recipe values appear"); await button(page, "Keep earlier recipe attempt and continue editing").click();
  await expect(unknown(page)).toHaveCount(0); expect(await project(page)).toEqual(before); expect(await requests(page)).toHaveLength(1);
  for (const copy of retained) expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
  await lufs(page).fill("-16"); const second = await save(page); await resolve(page, second.id); expect(await requests(page)).toHaveLength(2);
});

test("UNKNOWN and exact later clear/ABA raw survive a failed first pointer read, Retry and reload without resend", async ({ page }) => {
  await open(page); await lufs(page).fill("-14"); const first = await save(page); await lufs(page).fill("  1e  "); await peak(page).fill("-2.125");
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Original reply lost"), first.id);
  await expect(review(page).getByRole("alert")).toContainText("Original reply lost");
  const owner = await project(page), originalCopy = await activeTargetCopy(page, owner, owner.deliverables[0].id), marker = await failFirstTargetPointerRead(page, originalCopy.pointer);
  expect((await copies(page)).filter(copy => copy.key.startsWith("motionwright.delivery-recipe-draft/1:" + originalCopy.scope + ":")))
    .toEqual([{ key: originalCopy.key, raw: originalCopy.raw }]);
  await page.reload(); await expect(review(page)).toBeVisible(); await expect(review(page).getByRole("alert")).toContainText("first target pointer read failure");
  expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired"); await expect(button(page, "Save audio targets")).toBeDisabled();
  const recoveryCopies = page.locator('details[aria-label="Audio targets recovery copies"]'); await recoveryCopies.locator("summary").click();
  await recoveryCopies.getByRole("button", { name: "Discard audio targets copy 1", exact: true }).click();
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  await peak(page).fill("-3"); await peak(page).fill(""); await button(page, "Retry audio targets recovery copy").click();
  await expect(unknown(page)).toBeVisible(); await expect(lufs(page)).toHaveValue("  1e  "); await expect(peak(page)).toHaveValue("");
  const recoveredCopy = await activeTargetCopy(page, owner, owner.deliverables[0].id);
  expect(recoveredCopy.writer).not.toBe(originalCopy.writer); expect(recoveredCopy.state.pending).toEqual(originalCopy.state.pending);
  expect(recoveredCopy.state.input.awaiting).toEqual(originalCopy.state.input.awaiting); expect(recoveredCopy.state.input.edits.target_true_peak_dbtp.value).toBe("");
  await page.reload(); await expect(review(page)).toBeVisible(); await expect(unknown(page)).toBeVisible(); await expect(lufs(page)).toHaveValue("  1e  ");
  await expect(peak(page)).toHaveValue(""); await expect(button(page, "Save audio targets")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  expect((await activeTargetCopy(page, owner, owner.deliverables[0].id)).state).toEqual(recoveredCopy.state);
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  const retained = (await copies(page)).map(copy => JSON.parse(copy.raw).state).filter(state => state.pending?.outcome === "UNKNOWN");
  expect(retained.some(state => state.pending.fields.target_lufs === "-14" && state.input.edits.target_lufs.value === "  1e  ")).toBe(true);
});

test("the original ACK preserves later B to A to B raw and does not resend an equal value", async ({ page }) => {
  await open(page); const raw = "  -014.000  "; await lufs(page).fill(raw); const first = await save(page);
  await lufs(page).fill(""); await lufs(page).fill(raw); await resolve(page, first.id);
  await expect(lufs(page)).toHaveValue(raw); await expect(unknown(page)).toHaveCount(0); await expect(review(page)).toContainText("pending input");
  await expect(button(page, "Save audio targets")).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
  const details = page.locator('details[aria-label="Audio targets pending fields"]'); await details.locator("summary").click();
  await details.getByRole("button", { name: "Keep saved Integrated loudness target (LUFS)", exact: true }).click();
  await expect(lufs(page)).toHaveValue("-14"); expect(await requests(page)).toHaveLength(1);
});

for (const area of ["data", "pointer"] as const) test("a silent " + area + " backup failure blocks target dispatch until both copies read back", async ({ page }) => {
  await open(page); const before = await project(page);
  await page.evaluate(area => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (area === "data" && this === localStorage && key.startsWith("motionwright.delivery-recipe-draft/1:") && key.includes(',"targets"]:')) return;
      if (area === "pointer" && this === sessionStorage && key.startsWith("motionwright.delivery-recipe-writer.v2:") && key.endsWith(',"targets"]')) return;
      return original.call(this, key, value);
    };
    (window as unknown as { __restoreTargetsStorage(): void }).__restoreTargetsStorage = () => { Storage.prototype.setItem = original; };
  }, area);
  await lufs(page).fill("-14"); await expect(button(page, "Save audio targets")).toBeDisabled(); await expect(review(page).getByRole("alert")).toContainText("not confirmed");
  expect(await requests(page)).toEqual([]); expect(await project(page)).toEqual(before);
  await page.evaluate(() => (window as unknown as { __restoreTargetsStorage(): void }).__restoreTargetsStorage());
  await button(page, "Retry audio targets recovery copy").click(); const first = await save(page); await resolve(page, first.id); expect(await requests(page)).toHaveLength(1);
});

for (const field of ["id", "generation", "active_branch"] as const) test("target author input does not follow another " + field + " and returns with its original scope", async ({ page }) => {
  await open(page); const before = await project(page); await lufs(page).fill("  -014.000  ");
  await page.evaluate(({ field, value }) => window.__DELIVERY_RECIPE_AUTHORITY__.setScope({ [field]: value }), { field, value: randomUUID() });
  await expect(lufs(page)).toHaveValue(""); await lufs(page).fill("-16");
  await page.evaluate(({ field, value }) => window.__DELIVERY_RECIPE_AUTHORITY__.setScope({ [field]: value }), { field, value: before[field] });
  await expect(lufs(page)).toHaveValue("  -014.000  "); expect(await requests(page)).toEqual([]);
});

test("first-null recovery, profile switching and Film/mix navigation preserve target buffers without writing the project", async ({ page }) => {
  await open(page); const seeded = await project(page), pointer = recipePointerKey(recipeDraftScope(seeded, seeded.deliverables[0].id, "targets"));
  expect(await page.evaluate(key => sessionStorage.getItem(key), pointer)).toBeNull(); const marker = await failFirstTargetPointerRead(page, pointer);
  await page.reload(); await expect(review(page)).toBeVisible(); await expect(review(page).getByRole("alert")).toContainText("first target pointer read failure");
  expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired"); await lufs(page).fill("1e");
  expect(await page.evaluate(key => sessionStorage.getItem(key), pointer)).toBeNull(); await button(page, "Retry audio targets recovery copy").click();
  expect((await activeTargetCopy(page, seeded, seeded.deliverables[0].id)).state.input.edits.target_lufs.value).toBe("1e"); expect(await requests(page)).toEqual([]);
  await page.reload(); await expect(review(page)).toBeVisible(); await expect(lufs(page)).toHaveValue("1e");
  const before = await project(page), a = before.deliverables[0], b = before.deliverables[1];
  await profile(page, b.name); await expect(lufs(page)).toHaveValue(""); await peak(page).fill("-2.125");
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("mix")); await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("film"));
  await profile(page, a.name); await expect(lufs(page)).toHaveValue("1e"); await expect(peak(page)).toHaveValue("");
  await profile(page, b.name); await expect(lufs(page)).toHaveValue(""); await expect(peak(page)).toHaveValue("-2.125");
  expect(await requests(page)).toEqual([]); expect(await project(page)).toEqual(before);
});

test("Film and mix changes retain saved targets, while pending targets block native preparation", async ({ page }) => {
  await open(page); await lufs(page).fill("-23"); await peak(page).fill("-1"); const first = await save(page); await resolve(page, first.id);
  const saved = recipe(first)!, before = await project(page), scene = before.scenes.find(item => item.renderer === "motion-canvas")!;
  await page.getByRole("combobox", { name: scene.name + " narrative role", exact: true }).selectOption("payoff");
  await button(page, "Save scene intent").click(); await expect.poll(async () => (await requests(page)).length).toBe(2);
  const film = (await requests(page))[1]; expect(recipe(film)!.audio_targets).toEqual(saved.audio_targets); expect(recipe(film)!.original_mix).toEqual(saved.original_mix); await resolve(page, film.id);
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("mix")); const panel = page.locator('details[aria-label="Original three-bus native PCM audition"]');
  if (await panel.getAttribute("open") === null) await panel.locator("summary").first().click();
  await page.getByRole("textbox", { name: "SFX gain dB", exact: true }).fill("-11"); await button(page, "Save original mix recipe").click();
  await expect.poll(async () => (await requests(page)).length).toBe(3); const mix = (await requests(page))[2]; expect(recipe(mix)!.audio_targets).toEqual(saved.audio_targets); await resolve(page, mix.id);
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("film")); await lufs(page).fill("1e");
  await expect(page.getByText("Save or resolve pending audio targets before checking Film, rendering, assembling or exporting a native master. Those operations use the saved recipe.", { exact: true })).toBeVisible();
  expect(await requests(page)).toHaveLength(3);
});

test("an author-file download retains exact target raw and UNKNOWN during inaccessible local storage", async ({ page }, info) => {
  await open(page); await lufs(page).fill("-14"); const first = await save(page); await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "No original reply"), first.id);
  const before = await project(page); await page.evaluate(() => Object.defineProperty(window, "localStorage", { configurable: true,
    get() { throw new DOMException("Targets fixture storage unavailable", "SecurityError"); } }));
  await lufs(page).fill("  1e  "); const event = page.waitForEvent("download"); await button(page, "Download audio targets author input").click();
  const download = await event, path = info.outputPath("delivery-targets-author-input.json"); await download.saveAs(path); expect(await download.failure()).toBeNull();
  const text = await readFile(path, "utf8"), file = JSON.parse(text); expect(file.schema).toBe("motionwright.recipe-author-file/1");
  expect(file.state.input.edits.target_lufs.value).toBe("  1e  "); expect(file.state.pending.outcome).toBe("UNKNOWN"); expect(file.state.pending.fields.target_lufs).toBe("-14");
  expect(text).not.toContain('"effect_grant"'); expect(text).not.toContain('"request_id"'); expect(await requests(page)).toHaveLength(1); expect(await project(page)).toEqual(before);
  await info.attach("delivery-target-author-input", { path, contentType: "application/json" });
});
