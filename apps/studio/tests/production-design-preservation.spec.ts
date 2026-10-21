import { expect, test, type Page } from "@playwright/test";
import type { ProductionFixtureRequest } from "./fixtures/production-design-authority";

// Actual Production UI and canonical browser/coordinator paths. Controlled
// scheduling is a component regression fixture, not service/native acceptance.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const headline = (page: Page) => page.getByLabel("Headline", { exact: true });
const concept = (page: Page) => page.getByLabel("Creative concept", { exact: true });
const requests = (page: Page) => page.evaluate(() => window.__PRODUCTION_AUTHORITY__.requests());
async function plan(page: Page) { await button(page, "Production plan").click(); await expect(page.getByRole("heading", { name: "Production plan", exact: true })).toBeVisible(); }
async function component(page: Page) { await button(page, "Components").click(); await expect(headline(page)).toBeVisible(); }
async function submit(page: Page, name: string): Promise<ProductionFixtureRequest> {
  const count = (await requests(page)).length; await button(page, name).click();
  await expect.poll(async () => (await requests(page)).length).toBe(count + 1);
  return (await requests(page))[count];
}
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/production-design-authority.html");
  await expect(headline(page)).toBeVisible();
});

test("Production component raw input survives tool navigation, scene changes, remount and reload without a save", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
  const raw = "  An authored\nhero line  ";
  await headline(page).fill(raw); await page.getByLabel("Hero accent").fill("#bbee11");
  await plan(page); await component(page); await expect(headline(page)).toHaveValue(raw);
  await page.getByLabel("Production working scene").selectOption(initial.scenes[1].id);
  await expect(headline(page)).not.toHaveValue(raw); await headline(page).fill("Other scene line");
  await page.getByLabel("Production working scene").selectOption(initial.scenes[0].id);
  await expect(headline(page)).toHaveValue(raw);
  await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.unmount()); await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.mount());
  await expect(headline(page)).toHaveValue(raw); await page.reload();
  await expect(headline(page)).toHaveValue(raw); await expect(page.getByLabel("Hero accent")).toHaveValue("#bbee11");
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
});

test("Production plan preserves exact lists, reviewer, note, shot choices and clock through navigation and reload", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()); await plan(page);
  const raw = "  A deliberate concept\nKeep author spacing  ", constraints = "  reference A  \n\n reference B \n";
  await concept(page).fill(raw); await page.getByLabel(/Reference-derived constraints/).fill(constraints);
  await page.getByLabel("Reviewer", { exact: true }).fill("  Editor A  "); await page.getByLabel("Approval note", { exact: true }).fill("Keep the reason intact.");
  await page.getByLabel("Purpose", { exact: true }).first().fill("  Authored shot purpose  ");
  await page.getByLabel("Source asset", { exact: true }).first().selectOption(initial.assets[1].id);
  await page.getByLabel("Production governing clock").selectOption("music");
  await component(page); await plan(page); await expect(concept(page)).toHaveValue(raw);
  await page.reload(); await plan(page);
  await expect(concept(page)).toHaveValue(raw); await expect(page.getByLabel(/Reference-derived constraints/)).toHaveValue(constraints);
  await expect(page.getByLabel("Reviewer", { exact: true })).toHaveValue("  Editor A  ");
  await expect(page.getByLabel("Approval note", { exact: true })).toHaveValue("Keep the reason intact.");
  await expect(page.getByLabel("Purpose", { exact: true }).first()).toHaveValue("  Authored shot purpose  ");
  await expect(page.getByLabel("Source asset", { exact: true }).first()).toHaveValue(initial.assets[1].id);
  await expect(page.getByLabel("Production governing clock")).toHaveValue("music");
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
});

for (const target of ["component", "plan"] as const) for (const identity of ["id", "generation", "active_branch"] as const) {
  test(`Production ${target} drafts cannot cross ${identity} and restore the separate author on return`, async ({ page }) => {
    const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
    if (target === "plan") await plan(page);
    const field = target === "plan" ? concept(page) : headline(page);
    await field.fill("Author A retained input");
    await page.evaluate(({ identity, value }) => window.__PRODUCTION_AUTHORITY__.setIdentity(identity, value), { identity, value: initial[identity] + "-other" });
    await expect(field).not.toHaveValue("Author A retained input"); await field.fill("Independent author B");
    await page.evaluate(({ identity, value }) => window.__PRODUCTION_AUTHORITY__.setIdentity(identity, value), { identity, value: initial[identity] });
    await expect(field).toHaveValue("Author A retained input"); expect(await requests(page)).toEqual([]);
  });
}

test("a deferred Production concept digest cannot overwrite or send later author input", async ({ page }) => {
  await plan(page); await concept(page).fill("Original concept to consider");
  await page.getByLabel("Reviewer", { exact: true }).fill("Editor A"); await page.getByLabel("Approval note", { exact: true }).fill("Original approval reason");
  await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.holdNextPlanDigest());
  await button(page, "Approve this concept digest").click(); await expect.poll(() => page.evaluate(() => window.__PRODUCTION_AUTHORITY__.heldDigests())).toBe(1);
  await concept(page).fill("Later concept remains unapproved"); await page.getByLabel("Approval note", { exact: true }).fill("New author reason");
  await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.releasePlanDigest());
  await expect(concept(page)).toHaveValue("Later concept remains unapproved");
  await expect(page.getByLabel("Approval note", { exact: true })).toHaveValue("New author reason");
  await expect(page.getByRole("status").filter({ hasText: "No change was sent" })).toBeVisible();
  expect(await requests(page)).toEqual([]);
  const request = await submit(page, "Approve this concept digest");
  expect(request.change).toMatchObject({ type: "set_production_plan", plan: { concept: "Later concept remains unapproved", approval: { reviewer: "Editor A", note: "New author reason" } } });
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), request.id);
  await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toHaveCount(0);
});

for (const change of ["owner", "revision", "unmount"] as const) {
  test(`a Production digest completing after ${change} does not dispatch an obsolete approval`, async ({ page }) => {
    const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()); await plan(page);
    await concept(page).fill("Concept waiting for a scoped digest");
    await page.getByLabel("Reviewer", { exact: true }).fill("Editor A"); await page.getByLabel("Approval note", { exact: true }).fill("Review exact scope");
    await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.holdNextPlanDigest()); await button(page, "Approve this concept digest").click();
    await expect.poll(() => page.evaluate(() => window.__PRODUCTION_AUTHORITY__.heldDigests())).toBe(1);
    if (change === "owner") await page.evaluate(value => window.__PRODUCTION_AUTHORITY__.setIdentity("id", value), initial.id + "-other");
    else if (change === "revision") await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.externalChange({ type: "set_narrative_premise", premise: "External narrative revision" }));
    else { await component(page); await plan(page); }
    await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.releasePlanDigest());
    expect(await requests(page)).toEqual([]);
    if (change === "owner") await page.evaluate(value => window.__PRODUCTION_AUTHORITY__.setIdentity("id", value), initial.id);
    await expect(concept(page)).toHaveValue("Concept waiting for a scoped digest");
  });
}

for (const target of ["component", "plan"] as const) {
  test(`an original Production ${target} ACK retains later input and permits its separate second save`, async ({ page }) => {
    if (target === "plan") await plan(page);
    const input = target === "plan" ? concept(page) : headline(page);
    await input.fill("First authored direction");
    const first = await submit(page, target === "plan" ? "Save production plan" : "Add to scene");
    await input.fill("Later authored direction");
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), first.id);
    await expect(input).toHaveValue("Later authored direction");
    const second = await submit(page, target === "plan" ? "Save production plan" : "Update component");
    expect(second.source.revision).toBe(first.source.revision + 1);
    expect(second.change).toMatchObject(target === "plan" ? { type: "set_production_plan", plan: { concept: "Later authored direction" } }
      : { type: "upsert_product_hero", config: { headline: "Later authored direction" } });
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), second.id); expect(await requests(page)).toHaveLength(2);
  });
  test(`observed matching Production ${target} values do not acknowledge an unknown original result`, async ({ page }) => {
    if (target === "plan") await plan(page);
    const input = target === "plan" ? concept(page) : headline(page), label = target === "plan" ? "Production plan" : "Component";
    await input.fill("An unconfirmed authored direction");
    const request = await submit(page, target === "plan" ? "Save production plan" : "Add to scene");
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.observeOriginal(id), request.id);
    await expect(page.getByRole("region", { name: "Unconfirmed " + label, exact: true })).toBeVisible();
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.reject(id, "Original response unavailable"), request.id);
    await page.reload(); if (target === "plan") await plan(page);
    await expect(input).toHaveValue("An unconfirmed authored direction");
    await expect(page.getByRole("region", { name: "Unconfirmed " + label, exact: true })).toBeVisible();
    await expect(button(page, target === "plan" ? "Save production plan" : "Update component")).toBeDisabled();
    expect(await requests(page)).toEqual([]);
  });
}

for (const fault of ["data", "pointer"] as const) {
  test(`silent Production ${fault} recovery failure blocks actual Project dispatch and preserves raw input`, async ({ page }) => {
    const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()); await plan(page);
    await page.evaluate(fault => {
      const set = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (fault === "data" && this === localStorage && key.startsWith("motionwright.production-recovery.v1:")) return;
        if (fault === "pointer" && this === sessionStorage && key.startsWith("motionwright.production-writer.v1:")) return;
        return set.call(this, key, value);
      };
      window.__PRODUCTION_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; };
    }, fault);
    await concept(page).fill("  Work survives the unavailable backup  ");
    await expect(button(page, "Save production plan")).toBeDisabled();
    await expect(page.getByRole("alert", { name: "Production plan backup warning", exact: true })).toBeVisible();
    expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
    await page.evaluate(() => window.__PRODUCTION_RESTORE_STORAGE__?.()); await button(page, "Retry Production plan backup").click();
    const request = await submit(page, "Save production plan"); await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), request.id);
    await expect(concept(page)).toHaveValue("  Work survives the unavailable backup  ");
  });
}

test("a silent Production copy removal reports unconfirmed discard and keeps the original author bytes visible", async ({ page }) => {
  await headline(page).fill("A retained component copy"); const request = await submit(page, "Add to scene");
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), request.id);
  const recovery = page.getByRole("region", { name: "Component recovery copies", exact: true });
  await recovery.locator("summary").first().click();
  const copies = await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.startsWith("motionwright.production-recovery.v1:"))));
  await page.evaluate(() => {
    const remove = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function(key) { if (this === localStorage && key.startsWith("motionwright.production-recovery.v1:")) return; remove.call(this, key); };
    window.__PRODUCTION_RESTORE_STORAGE__ = () => { Storage.prototype.removeItem = remove; };
  });
  await button(page, "Discard Component copy 1").click();
  await expect(page.getByRole("alert").filter({ hasText: "Discard was not confirmed" })).toBeVisible();
  await expect(button(page, "Discard Component copy 1")).toBeVisible();
  expect(await page.evaluate(() => Object.fromEntries(Object.entries(localStorage).filter(([key]) => key.startsWith("motionwright.production-recovery.v1:"))))).toEqual(copies);
  expect(await requests(page)).toHaveLength(1);
});

test("an unreadable own Production copy stays warned while typing; an explicit new copy preserves both buffers", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
  await headline(page).fill("An earlier recoverable line");
  const corruptKey = await page.evaluate(project => {
    const scope = JSON.stringify([project.id, project.generation, project.active_branch, "hero", project.scenes[0].id]);
    const writer = sessionStorage.getItem("motionwright.production-writer.v1:" + encodeURIComponent(scope));
    if (!writer) throw new Error("Missing own Production pointer.");
    const key = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":" + writer;
    localStorage.setItem(key, "{unreadable author copy"); return key;
  }, initial);
  await page.reload();
  const warning = page.getByRole("alert", { name: "Component backup warning", exact: true }); await expect(warning).toBeVisible();
  await headline(page).fill("New author input remains"); await expect(warning).toBeVisible();
  await expect(button(page, "Add to scene")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(key => localStorage.getItem(key), corruptKey)).toBe("{unreadable author copy");
  await button(page, "Retry Component backup").click(); await expect(warning).toBeVisible();
  await button(page, "Keep Component input in a new copy").click(); await expect(warning).toHaveCount(0);
  await expect(button(page, "Add to scene")).toBeEnabled();
  expect(await page.evaluate(key => localStorage.getItem(key), corruptKey)).toBe("{unreadable author copy");
  await page.reload(); await expect(headline(page)).toHaveValue("New author input remains");
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
});

test("retrying a readable Production recovery retains its UNKNOWN alongside newer author input", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()); await plan(page);
  await concept(page).fill("Original uncertain plan input"); await submit(page, "Save production plan");
  const earlier = await page.evaluate(project => {
    const scope = JSON.stringify([project.id, project.generation, project.active_branch, "plan", null]);
    const writer = sessionStorage.getItem("motionwright.production-writer.v1:" + encodeURIComponent(scope));
    if (!writer) throw new Error("Missing own Production pointer.");
    const key = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (!raw) throw new Error("Missing pending Production copy.");
    localStorage.setItem(key, "{temporarily unreadable"); return { key, raw };
  }, initial);
  await page.reload(); await plan(page);
  await expect(page.getByRole("alert", { name: "Production plan backup warning", exact: true })).toBeVisible();
  await concept(page).fill("Newer input during the recovery problem");
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), earlier);
  await button(page, "Retry Production plan backup").click();
  await expect(concept(page)).toHaveValue("Newer input during the recovery problem");
  await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toBeVisible();
  await expect(button(page, "Save production plan")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
});
