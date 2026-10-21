import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { RecipeFixtureRequest } from "./fixtures/delivery-recipe-authority";

// Actual exported components, coordinator and browser API. These controlled
// replies are component regressions, never StudioService / SQLite / native proof.
type Group = "film" | "mix";
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const review = (page: Page, group: Group) => page.getByRole("region", { name: (group === "film" ? "Scene intent" : "Original mix") + " draft review", exact: true });
const unknown = (page: Page, group: Group) => page.getByRole("region", { name: (group === "film" ? "Scene intent" : "Original mix") + " unconfirmed save", exact: true });
const saveLabel = (group: Group) => group === "film" ? "Save scene intent" : "Save original mix recipe";
const requests = (page: Page) => page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.requests());
async function reveal(page: Page, group: Group) {
  if (group === "mix") {
    const panel = page.locator('details[aria-label="Original three-bus native PCM audition"]');
    if (await panel.getAttribute("open") === null) await panel.locator("summary").first().click();
  }
  await expect(review(page, group)).toBeVisible();
}
async function open(page: Page, group: Group = "film") {
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
async function failWrites(page: Page, area: "data" | "pointer") {
  await page.evaluate(area => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (area === "data" && this === localStorage && key.startsWith("motionwright.delivery-recipe-draft/1:")) return;
      if (area === "pointer" && this === sessionStorage && key.startsWith("motionwright.delivery-recipe-writer.v2:")) return;
      return original.call(this, key, value);
    };
    (window as unknown as { __restoreRecipeStorage: () => void }).__restoreRecipeStorage = () => { Storage.prototype.setItem = original; };
  }, area);
}

for (const group of ["film", "mix"] as const) {
  test(`${group}: another author's matching saved recipe never acknowledges the uncertain original request`, async ({ page }) => {
    await open(page, group);
    await edit(page, group, group === "film" ? "reveal" : "-4.5");
    const first = await save(page, group);
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Reply unavailable"), first.id);
    await page.evaluate(change => window.__DELIVERY_RECIPE_AUTHORITY__.externalChange(change), first.change);
    await expect(unknown(page, group)).toBeVisible();
    await expect(review(page, group)).toContainText("unconfirmed");
    await expect(button(page, saveLabel(group))).toBeDisabled();
    expect(await requests(page)).toHaveLength(1);
    const beforeReview = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
    await button(page, "Read saved recipe and history").click();
    await expect(unknown(page, group)).toContainText("Matching recipe values appear");
    await button(page, "Keep earlier recipe attempt and continue editing").click();
    await expect(unknown(page, group)).toHaveCount(0);
    expect(await requests(page)).toHaveLength(1);
    expect(await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project())).toEqual(beforeReview);
    await edit(page, group, group === "film" ? "payoff" : "-7");
    const second = await save(page, group);
    expect(second.source.revision).toBe(beforeReview.revision);
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), second.id);
    await expect(button(page, saveLabel(group))).toBeDisabled();
    await expect(review(page, group)).toContainText("Saved recipe at revision");
  });

  test(`${group}: UNKNOWN and later author input survive reload with no automatic replay`, async ({ page }) => {
    await open(page, group);
    const later = group === "film" ? "payoff" : "  -06.500e0  ";
    await edit(page, group, group === "film" ? "reveal" : "-4.5");
    const request = await save(page, group);
    await edit(page, group, later);
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Reply disappeared"), request.id);
    await page.reload(); await reveal(page, group);
    await expect(await control(page, group)).toHaveValue(later);
    await expect(unknown(page, group)).toBeVisible();
    await expect(button(page, saveLabel(group))).toBeDisabled();
    expect(await requests(page)).toEqual([]);
    const pending = await page.evaluate(() => Object.values(localStorage).filter(raw => raw.includes('"outcome":"UNKNOWN"')));
    expect(pending.length).toBeGreaterThan(0);
  });

  test(`${group}: original ACK across workspace remount preserves a newer author decision and enables a separate save`, async ({ page }) => {
    await open(page, group);
    await edit(page, group, group === "film" ? "reveal" : "-4.5");
    const first = await save(page, group);
    await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.unmount());
    await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.mount());
    await reveal(page, group);
    const later = group === "film" ? "hook" : "  -08.000e0  ";
    await edit(page, group, later);
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), first.id);
    await expect(await control(page, group)).toHaveValue(later);
    await expect(unknown(page, group)).toHaveCount(0);
    const second = await save(page, group);
    expect(second.source.revision).toBe(first.source.revision + 1);
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), second.id);
    await expect(button(page, saveLabel(group))).toBeDisabled();
    expect(await requests(page)).toHaveLength(2);
  });
}

test("mix: later typing back to the submitted numeric value preserves its raw representation after original ACK", async ({ page }) => {
  await open(page, "mix");
  const raw = "  -04.500e0  ";
  await edit(page, "mix", raw);
  const first = await save(page, "mix");
  await edit(page, "mix", "-6"); await edit(page, "mix", raw);
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), first.id);
  await expect(await control(page, "mix")).toHaveValue(raw);
  await expect(unknown(page, "mix")).toHaveCount(0);
  await expect(button(page, saveLabel("mix"))).toBeDisabled(); // Already equal, but later raw is still authored input.
  await edit(page, "mix", "-7");
  const second = await save(page, "mix");
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), second.id);
  expect(await requests(page)).toHaveLength(2);
});

for (const area of ["data", "pointer"] as const) {
  test(`a silent ${area} recovery failure blocks recipe dispatch until verified readback succeeds`, async ({ page }) => {
    await open(page, "mix");
    const before = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
    await failWrites(page, area);
    await edit(page, "mix", "-4.5");
    await expect(button(page, saveLabel("mix"))).toBeDisabled();
    await expect(review(page, "mix").getByRole("alert")).toContainText("not confirmed");
    expect(await requests(page)).toEqual([]);
    expect(await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project())).toEqual(before);
    await page.evaluate(() => (window as unknown as { __restoreRecipeStorage: () => void }).__restoreRecipeStorage());
    await button(page, "Retry original mix recovery copy").click();
    const request = await save(page, "mix");
    await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), request.id);
    await expect(button(page, saveLabel("mix"))).toBeDisabled();
  });
}

for (const field of ["id", "generation", "active_branch"] as const) {
  test(`equal recipe values under another ${field} cannot adopt this author's raw input`, async ({ page }) => {
    await open(page, "mix");
    const initial = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
    await edit(page, "mix", "  -04.500e0  ");
    await page.evaluate(({ field, value }) => window.__DELIVERY_RECIPE_AUTHORITY__.setScope({ [field]: value }), { field, value: initial[field] + "-other" });
    await expect(await control(page, "mix")).toHaveValue("-8");
    await edit(page, "mix", "-11");
    await page.evaluate(({ field, value }) => window.__DELIVERY_RECIPE_AUTHORITY__.setScope({ [field]: value }), { field, value: initial[field] });
    await expect(await control(page, "mix")).toHaveValue("  -04.500e0  ");
    expect(await requests(page)).toEqual([]);
  });
}

test("profile and Film/mix navigation preserve separate raw inputs without project writes", async ({ page }) => {
  await open(page, "mix");
  const initial = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
  const a = initial.deliverables[0], b = initial.deliverables[1];
  await edit(page, "mix", "1e");
  await page.getByRole("combobox", { name: "Delivery language / profile", exact: true }).selectOption(b.id);
  await expect(await control(page, "mix")).toHaveValue("-8"); await edit(page, "mix", "-11");
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("film"));
  await edit(page, "film", "reveal");
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("mix")); await reveal(page, "mix");
  await page.getByRole("combobox", { name: "Delivery language / profile", exact: true }).selectOption(a.id);
  await expect(await control(page, "mix")).toHaveValue("1e");
  await page.getByRole("combobox", { name: "Delivery language / profile", exact: true }).selectOption(b.id);
  await expect(await control(page, "mix")).toHaveValue("-11");
  await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.show("film"));
  await expect(await control(page, "film")).toHaveValue("reveal");
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project())).toEqual(initial);
});

test("a read started before A to B to A cannot publish a late saved-history review", async ({ page }) => {
  await open(page, "mix"); await edit(page, "mix", "-4.5");
  const request = await save(page, "mix");
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Unknown reply"), request.id);
  await page.evaluate(async () => {
    const h = window.__DELIVERY_RECIPE_AUTHORITY__, original = h.project().generation;
    const read = [...document.querySelectorAll("button")].find(button => button.textContent?.trim() === "Read saved recipe and history");
    if (!read) throw new Error("Missing actual history-review control");
    read.click();
    const b = h.setScope({ generation: original + "-B" });
    const a = h.setScope({ generation: original });
    await Promise.all([b, a]);
  });
  await expect(unknown(page, "mix")).toBeVisible();
  await expect(button(page, "Keep earlier recipe attempt and continue editing")).toHaveCount(0);
  await button(page, "Read saved recipe and history").click();
  await expect(button(page, "Keep earlier recipe attempt and continue editing")).toBeVisible();
  expect(await requests(page)).toHaveLength(1);
});

test("a corrupt own recovery copy stays byte-exact until explicit discard while newer raw input survives", async ({ page }) => {
  await open(page, "mix"); await edit(page, "mix", "-4.5");
  const corrupt = "{author-copy:incomplete";
  const key = await page.evaluate(corrupt => {
    const pointerKey = Object.keys(sessionStorage).find(key => key.startsWith("motionwright.delivery-recipe-writer.v2:") && key.endsWith(',"mix"]'));
    if (!pointerKey) throw new Error("No current recipe writer pointer");
    const scope = pointerKey.slice("motionwright.delivery-recipe-writer.v2:".length);
    const key = "motionwright.delivery-recipe-draft/1:" + scope + ":" + sessionStorage.getItem(pointerKey);
    localStorage.setItem(key, corrupt); return key;
  }, corrupt);
  await page.reload(); await reveal(page, "mix");
  await edit(page, "mix", "1e");
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe(corrupt);
  await expect(button(page, saveLabel("mix"))).toBeDisabled();
  await button(page, "Discard unreadable original mix copy").click();
  await expect(await control(page, "mix")).toHaveValue("1e");
  expect(await page.evaluate(key => localStorage.getItem(key), key)).toBeNull();
  await edit(page, "mix", "-11");
  const request = await save(page, "mix");
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.resolve(id), request.id);
});

test("a copied browser tab offers recovery explicitly and never shares the source writer's editable key", async ({ page, context }) => {
  await open(page, "mix"); await edit(page, "mix", "  -04.500e0  ");
  const original = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.startsWith("motionwright.delivery-recipe-draft/1:"))));
  const childEvent = context.waitForEvent("page");
  await page.evaluate(() => { window.open(location.href, "_blank"); });
  const child = await childEvent;
  await reveal(child, "mix");
  await expect(await control(child, "mix")).toHaveValue("-8");
  const copies = child.locator('details[aria-label="Original mix recovery copies"]');
  await copies.locator("summary").click();
  await button(child, "Recover original mix copy 1").click();
  await expect(await control(child, "mix")).toHaveValue("  -04.500e0  ");
  await edit(child, "mix", "-11");
  for (const [key, raw] of Object.entries(original)) expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe(raw);
  await expect(await control(page, "mix")).toHaveValue("  -04.500e0  ");
  expect(await requests(page)).toEqual([]); expect(await requests(child)).toEqual([]);
});

test("author JSON downloads retain raw and UNKNOWN during total storage failure without project writes", async ({ page }, info) => {
  await open(page, "mix"); await edit(page, "mix", "-4.5");
  const request = await save(page, "mix");
  await page.evaluate(id => window.__DELIVERY_RECIPE_AUTHORITY__.reject(id, "Original response unavailable"), request.id);
  const initial = await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project());
  await page.evaluate(() => Object.defineProperty(window, "localStorage", { configurable: true,
    get() { throw new DOMException("Recipe test storage inaccessible", "SecurityError"); } }));
  const raw = "  1e  "; await edit(page, "mix", raw);
  const downloaded = page.waitForEvent("download");
  await button(page, "Download original mix author input").click();
  const download = await downloaded, path = info.outputPath("recipe-author-input.json");
  await download.saveAs(path); expect(await download.failure()).toBeNull();
  const text = await readFile(path, "utf8"), file = JSON.parse(text);
  expect(file.schema).toBe("motionwright.recipe-author-file/1");
  expect(file.state.input.edits.sfx_gain_db.value).toBe(raw);
  expect(file.state.pending.outcome).toBe("UNKNOWN");
  expect(file.state.pending.fields.sfx_gain_db).toBe("-4.5");
  expect(text).not.toContain('"effect_grant"'); expect(text).not.toContain('"request_id"');
  await info.attach("author-file", { path, contentType: "application/json" });
  expect(await requests(page)).toHaveLength(1);
  expect(await page.evaluate(() => window.__DELIVERY_RECIPE_AUTHORITY__.project())).toEqual(initial);
});
