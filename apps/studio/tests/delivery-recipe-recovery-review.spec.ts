import { expect, test, type Page } from "@playwright/test";
import type { RecipeFixtureRequest } from "./fixtures/delivery-recipe-authority";

// Existing real components + ProjectMutationGate + browser adapter. Storage
// timing and reply scheduling are adversarial fixtures, not SQLite/native proof.
type Group = "film" | "mix";
const dataPrefix = "motionwright.delivery-recipe-draft/1:";
const pointerPrefix = "motionwright.delivery-recipe-writer.v2:";
const title = (group: Group) => group === "film" ? "Scene intent" : "Original mix";
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const review = (page: Page, group: Group) => page.getByRole("region", { name: title(group) + " draft review", exact: true });
const unknown = (page: Page, group: Group) => page.getByRole("region", { name: title(group) + " unconfirmed save", exact: true });
const saveLabel = (group: Group) => group === "film" ? "Save scene intent" : "Save original mix recipe";
const requests = (page: Page) => page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.requests());
async function reveal(page: Page, group: Group) {
  if (group === "mix") {
    const panel = page.locator('details[aria-label="Original three-bus native PCM audition"]');
    if (await panel.getAttribute("open") === null) await panel.locator("summary").first().click();
  }
  await expect(review(page, group)).toBeVisible();
}
async function open(page: Page, group: Group) {
  await page.goto("/tests/fixtures/delivery-recipe-authority.html?group=" + group);
  await reveal(page, group);
}
async function control(page: Page, group: Group) {
  if (group === "mix") return page.getByRole("textbox", { name: "SFX gain dB", exact: true });
  const scene = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project().scenes[0]);
  return page.getByRole("combobox", { name: scene.name + " narrative role", exact: true });
}
async function edit(page: Page, group: Group, value: string) {
  const field = await control(page, group);
  if (group === "film") await field.selectOption(value); else await field.fill(value);
}
async function save(page: Page, group: Group): Promise<RecipeFixtureRequest> {
  const before = (await requests(page)).length;
  await button(page, saveLabel(group)).click();
  await expect.poll(async () => (await requests(page)).length).toBe(before + 1);
  return (await requests(page))[before];
}
async function ownCopy(page: Page, group: Group) {
  return page.evaluate(({ group, dataPrefix, pointerPrefix }) => {
    const pointer = Object.keys(sessionStorage).find(key => key.startsWith(pointerPrefix) && key.endsWith(',"' + group + '"]'));
    if (!pointer) throw new Error("No actual recipe pointer");
    const scope = pointer.slice(pointerPrefix.length), key = dataPrefix + scope + ":" + sessionStorage.getItem(pointer);
    const raw = localStorage.getItem(key); if (raw === null) throw new Error("No retained own recipe copy");
    return { key, raw };
  }, { group, dataPrefix, pointerPrefix });
}
async function peerValue(page: Page, group: Group, value: "reveal" | "payoff" | "-4.5" | "-7") {
  await page.evaluate(async ({ group, value }) => {
    const h = window.__DELIVERY_RECIPE_AUTHORITY__, project = h.project(), profileId = project.deliverables[0].id;
    const recipe = structuredClone(project.production_design!.delivery_recipes![profileId]);
    if (group === "film") recipe.scene_intents[0].role = value as "reveal" | "payoff";
    else recipe.original_mix!.sfx_gain_db = Number(value);
    await h.externalChange({ type: "set_delivery_recipe", deliverable_profile_id: profileId, recipe });
  }, { group, value });
}

test("recipe dispatch is blocked if UNKNOWN data changes while its pointer is published", async ({ page }) => {
  await open(page, "mix"); await edit(page, "mix", "-4.5");
  const initial = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
  await page.evaluate(({ dataPrefix, pointerPrefix }) => {
    const originalSet = Storage.prototype.setItem, originalGet = Storage.prototype.getItem;
    Storage.prototype.setItem = function(key, value) {
      originalSet.call(this, key, value);
      if (this !== sessionStorage || !key.startsWith(pointerPrefix)) return;
      const recordKey = dataPrefix + key.slice(pointerPrefix.length) + ":" + value;
      const raw = originalGet.call(localStorage, recordKey);
      if (raw && JSON.parse(raw).state?.pending?.outcome === "UNKNOWN")
        originalSet.call(localStorage, recordKey, "foreign replacement during pointer publication");
    };
  }, { dataPrefix, pointerPrefix });
  await button(page, saveLabel("mix")).click();
  await expect(review(page, "mix").getByRole("alert")).toContainText("not confirmed");
  await expect(button(page, saveLabel("mix"))).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project())).toEqual(initial);
  expect((await ownCopy(page, "mix")).raw).toBe("foreign replacement during pointer publication");
});

for (const group of ["film", "mix"] as const) {
  test(`${group}: Recover preserves an equal author decision through a later peer recipe change`, async ({ page, context }) => {
    await open(page, group);
    const authored = group === "film" ? "reveal" : "-4.5";
    await edit(page, group, authored);
    const original = await ownCopy(page, group), child = await context.newPage();
    await open(child, group); await peerValue(child, group, authored);
    const copies = child.locator('details[aria-label="' + title(group) + ' recovery copies"]');
    await copies.locator("summary").click();
    await button(child, "Recover " + title(group).toLowerCase() + " copy 1").click();
    await expect(await control(child, group)).toHaveValue(authored);
    await peerValue(child, group, group === "film" ? "payoff" : "-7");
    await expect(await control(child, group)).toHaveValue(authored);
    await expect(child.getByRole("region", { name: title(group) + " conflicts", exact: true })).toBeVisible();
    await expect(button(child, saveLabel(group))).toBeDisabled();
    expect(await requests(child)).toEqual([]); expect(await requests(page)).toEqual([]);
    expect(await child.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    await child.close();
  });

  test(`${group}: Retry decodes a transiently unreadable own UNKNOWN copy and keeps later input without replay`, async ({ page }) => {
    await open(page, group); await edit(page, group, group === "film" ? "reveal" : "-4.5");
    const request = await save(page, group);
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Original reply unavailable"), request.id);
    const original = await ownCopy(page, group);
    await page.addInitScript(key => {
      let blocked = true;
      const originalGet = Storage.prototype.getItem;
      Storage.prototype.getItem = function(candidate) {
        if (blocked && this === localStorage && candidate === key) throw new Error("Transient own-copy read failure");
        return originalGet.call(this, candidate);
      };
      (window as unknown as { __allowRecipeRecoveryRead: () => void }).__allowRecipeRecoveryRead = () => { blocked = false; };
    }, original.key);
    await page.reload(); await reveal(page, group);
    await expect(review(page, group).getByRole("alert")).toContainText("Transient own-copy read failure");
    const later = group === "film" ? "hook" : "  1e  ";
    if (group === "film") await edit(page, group, "payoff");
    await edit(page, group, later);
    // A new saved snapshot must not replace the "returned to hook" decision
    // merely because it had matched the old saved value during the read outage.
    if (group === "film") await peerValue(page, group, "payoff");
    await page.evaluate(() => (window as unknown as { __allowRecipeRecoveryRead: () => void }).__allowRecipeRecoveryRead());
    await button(page, "Retry " + title(group).toLowerCase() + " recovery copy").click();
    await expect(await control(page, group)).toHaveValue(later);
    await expect(unknown(page, group)).toBeVisible();
    await expect(button(page, "Discard unreadable " + title(group).toLowerCase() + " copy")).toHaveCount(0);
    await expect(button(page, saveLabel(group))).toBeDisabled();
    expect(await requests(page)).toEqual([]);
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    const retained = await ownCopy(page, group), decoded = JSON.parse(retained.raw), earlier = JSON.parse(original.raw);
    expect(retained.key).not.toBe(original.key);
    expect(decoded.state.pending).toEqual(earlier.state.pending);
    const field = group === "mix" ? "sfx_gain_db" : (await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project().scenes[0].id)) + ":role";
    expect(decoded.state.input.edits[field].value).toBe(later);
    expect(decoded.state.fieldSequences[field]).toBeGreaterThan(decoded.state.pending.sequences[field]);
    await page.reload(); await reveal(page, group);
    await expect(await control(page, group)).toHaveValue(later);
    await expect(unknown(page, group)).toBeVisible();
    expect(await requests(page)).toEqual([]);
  });
}

test("a clean ACK checkpoint is labelled without historical author fields and cannot claim to restore a recipe", async ({ page, context }) => {
  await open(page, "mix"); await edit(page, "mix", "-4.5");
  const request = await save(page, "mix");
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), request.id);
  await expect(review(page, "mix")).toContainText("Saved recipe at revision");
  const checkpoint = await ownCopy(page, "mix");
  expect(JSON.parse(checkpoint.raw).state.input.edits).toEqual({});
  const child = await context.newPage(); await open(child, "mix");
  const copies = child.locator('details[aria-label="Original mix recovery copies"]');
  await copies.locator("summary").click();
  const clean = copies.locator(".portable-operation").filter({ hasText: "Clean checkpoint · no pending author fields" });
  await expect(clean).toHaveCount(1);
  await expect(clean.getByRole("button", { name: /^Recover original mix copy / })).toBeDisabled();
  expect(await requests(child)).toEqual([]);
  expect(await child.evaluate(key => localStorage.getItem(key), checkpoint.key)).toBe(checkpoint.raw);
  // The armed historical copy remains available, but restores UNKNOWN only.
  const uncertain = copies.locator(".portable-operation").filter({ hasText: "pending field(s)" });
  await expect(uncertain).toHaveCount(1);
  await uncertain.getByRole("button", { name: /^Recover original mix copy / }).click();
  await expect(unknown(child, "mix")).toBeVisible();
  await expect(button(child, saveLabel("mix"))).toBeDisabled();
  expect(await requests(child)).toEqual([]);
  await child.close();
});
