import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { ProductionDraft } from "../src/productionDesignDraft";
import type { ProductionFixtureRequest } from "./fixtures/production-design-authority";

// Actual Production UI and canonical browser/coordinator paths. Controlled
// scheduling is a component regression fixture, not service/native acceptance.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const headline = (page: Page) => page.getByLabel("Headline", { exact: true });
const concept = (page: Page) => page.getByRole("textbox", { name: "Creative concept", exact: true });
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
  await page.getByRole("textbox", { name: "Purpose", exact: true }).first().fill("  Authored shot purpose  ");
  await page.getByRole("combobox", { name: "Source asset", exact: true }).first().selectOption(initial.assets[1].id);
  await page.getByLabel("Production governing clock").selectOption("music");
  await component(page); await plan(page); await expect(concept(page)).toHaveValue(raw);
  await page.reload(); await plan(page);
  await expect(concept(page)).toHaveValue(raw); await expect(page.getByLabel(/Reference-derived constraints/)).toHaveValue(constraints);
  await expect(page.getByLabel("Reviewer", { exact: true })).toHaveValue("  Editor A  ");
  await expect(page.getByLabel("Approval note", { exact: true })).toHaveValue("Keep the reason intact.");
  await expect(page.getByRole("textbox", { name: "Purpose", exact: true }).first()).toHaveValue("  Authored shot purpose  ");
  await expect(page.getByRole("combobox", { name: "Source asset", exact: true }).first()).toHaveValue(initial.assets[1].id);
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

test("an unreadable Production source blocks new custody while original bytes and later input remain downloadable", async ({ page }, info) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
  await headline(page).fill("An earlier recoverable line");
  const corrupt = "{unreadable author copy", later = "  New author input remains  ";
  const original = await page.evaluate(({ project, corrupt }) => {
    const scope = JSON.stringify([project.id, project.generation, project.active_branch, "hero", project.scenes[0].id]);
    const pointer = "motionwright.production-writer.v1:" + encodeURIComponent(scope), writer = sessionStorage.getItem(pointer);
    if (!writer) throw new Error("Missing own Production pointer.");
    const key = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":" + writer;
    localStorage.setItem(key, corrupt); return { key, pointer, writer };
  }, { project: initial, corrupt });
  await page.reload();
  const warning = page.getByRole("alert", { name: "Component backup warning", exact: true }); await expect(warning).toBeVisible();
  await headline(page).fill(later); await expect(warning).toBeVisible();
  await expect(button(page, "Add to scene")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  await button(page, "Retry Component backup").click(); await expect(warning).toBeVisible();
  await expect(button(page, "Keep Component input in a new copy")).toBeDisabled();
  await plan(page); await component(page); await expect(headline(page)).toHaveValue(later);
  await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.unmount()); await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.mount());
  await expect(headline(page)).toHaveValue(later); await expect(warning).toBeVisible();
  const copies = page.getByRole("region", { name: "Component recovery copies", exact: true });
  await copies.locator("summary").first().click();
  await expect(button(page, "Discard Component copy 1")).toBeDisabled();
  await expect(button(page, "Restore Component copy 1")).toHaveCount(0);
  const originalDownload = page.waitForEvent("download"); await button(page, "Download Component copy 1").click();
  const originalPath = await (await originalDownload).path();
  if (!originalPath) throw new Error("The corrupt original bytes did not produce a download.");
  const originalBytes = await readFile(originalPath); expect(originalBytes.equals(Buffer.from(corrupt, "utf8"))).toBe(true);
  const currentDownload = page.waitForEvent("download"); await button(page, "Download Component draft").click();
  const currentPath = await (await currentDownload).path();
  if (!currentPath) throw new Error("The later authored input did not produce a download.");
  const currentBytes = await readFile(currentPath), current = JSON.parse(currentBytes.toString("utf8"));
  expect(current.schema).toBe("motionwright.production-file/1"); expect(current.draft.input.config.headline).toBe(later);
  expect(current.draft.intent).toBe(true); expect(current).not.toHaveProperty("effect_grant"); expect(current.draft).not.toHaveProperty("effect_grant");
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(corrupt);
  expect(await page.evaluate(key => sessionStorage.getItem(key), original.pointer)).toBe(original.writer);
  await expect(button(page, "Add to scene")).toBeDisabled(); await expect(warning).toBeVisible();
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
  await info.attach("unreadable-production-original", { body: originalBytes, contentType: "application/json" });
  await info.attach("later-production-author-input", { body: currentBytes, contentType: "application/json" });
});

test("retrying a failed Production copy read retains original UNKNOWN and later cleared input without replacing bytes", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()); await plan(page);
  await concept(page).fill("Original uncertain plan input"); await submit(page, "Save production plan");
  const earlier = await page.evaluate(project => {
    const scope = JSON.stringify([project.id, project.generation, project.active_branch, "plan", null]);
    const pointer = "motionwright.production-writer.v1:" + encodeURIComponent(scope), writer = sessionStorage.getItem(pointer);
    if (!writer) throw new Error("Missing own Production pointer.");
    const key = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (!raw) throw new Error("Missing pending Production copy.");
    return { key, raw, pointer, writer, draft: (JSON.parse(raw) as { draft: ProductionDraft }).draft };
  }, initial);
  expect(earlier.draft.pending?.outcome).toBe("UNKNOWN");
  const marker = "motionwright.test.production-preservation-copy-read:" + crypto.randomUUID();
  // A read failure does not replace the original bytes. The marker keeps the
  // one-shot fault from firing again after successful recovery and reload.
  await page.addInitScript(({ key, marker }) => {
    const original = Storage.prototype.getItem;
    if (original.call(sessionStorage, marker) === "fired") return;
    let armed = true;
    Storage.prototype.getItem = function(name) {
      if (armed && this === localStorage && name === key) {
        armed = false; sessionStorage.setItem(marker, "fired");
        throw new Error("Injected first Production copy read failure.");
      }
      return original.call(this, name);
    };
    window.__PRODUCTION_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = original; };
  }, { key: earlier.key, marker });
  await page.reload(); await plan(page);
  const warning = page.getByRole("alert", { name: "Production plan backup warning", exact: true });
  await expect(warning).toContainText("Injected first Production copy read failure");
  expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
  expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
  await concept(page).fill("Newer input during the recovery problem");
  await component(page); await plan(page); await expect(concept(page)).toHaveValue("Newer input during the recovery problem");
  await concept(page).fill("");
  await expect(button(page, "Keep Production plan input in a new copy")).toBeDisabled();
  await expect(button(page, "Save production plan")).toBeDisabled();
  await page.evaluate(() => window.__PRODUCTION_RESTORE_STORAGE__?.());
  await button(page, "Retry Production plan backup").click();
  await expect(warning).toHaveCount(0); await expect(concept(page)).toHaveValue("");
  await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toBeVisible();
  await expect(button(page, "Save production plan")).toBeDisabled();
  const recovered = await page.evaluate(({ pointer, scope }) => {
    const writer = sessionStorage.getItem(pointer);
    if (!writer) throw new Error("Recovery did not publish a verified pointer.");
    const key = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (!raw) throw new Error("Recovery did not retain its new copy.");
    return { writer, draft: (JSON.parse(raw) as { draft: ProductionDraft }).draft };
  }, { pointer: earlier.pointer, scope: earlier.draft.scope });
  expect(recovered.writer).not.toBe(earlier.writer); expect(recovered.draft.pending).toEqual(earlier.draft.pending);
  expect(recovered.draft.sequence).toBeGreaterThan(earlier.draft.sequence);
  expect(recovered.draft.input.kind).toBe("plan");
  if (recovered.draft.input.kind !== "plan") throw new Error("Recovery changed the Production input kind.");
  expect(recovered.draft.input.plan.concept).toBe("");
  expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
  // Reload is asserted only after the new recovery copy was verified.
  await page.reload(); await plan(page); await expect(concept(page)).toHaveValue("");
  await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toBeVisible();
  await expect(button(page, "Save production plan")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
});

type AttachmentFields = { assetId: string; renderer: string; version: string };
const attachmentAsset = (page: Page) => page.getByRole("combobox", { name: "Imported source asset", exact: true });
const attachmentRenderer = (page: Page) => page.getByRole("textbox", { name: "Native renderer", exact: true });
const attachmentVersion = (page: Page) => page.getByRole("textbox", { name: "Source renderer version", exact: true });
async function attachments(page: Page) {
  await button(page, "Native sources").click(); await expect(attachmentRenderer(page)).toBeVisible();
}
async function fillAttachment(page: Page, input: AttachmentFields) {
  await attachmentAsset(page).selectOption(input.assetId);
  await attachmentRenderer(page).fill(input.renderer); await attachmentVersion(page).fill(input.version);
}
async function expectAttachment(page: Page, input: AttachmentFields) {
  await expect(attachmentAsset(page)).toHaveValue(input.assetId);
  await expect(attachmentRenderer(page)).toHaveValue(input.renderer); await expect(attachmentVersion(page)).toHaveValue(input.version);
}
function fixtureAttachment(project: ProductionFixtureRequest["source"]): AttachmentFields {
  const asset = project.assets.find(value => value.media_type === "model/gltf-binary" && value.content_sha256);
  if (!asset) throw new Error("The declared browser fixture needs its digest-bound opaque source asset.");
  return { assetId: asset.id, renderer: "  Authored renderer  ", version: "  24.5.1 incomplete  " };
}
async function productionCopiesFor(page: Page, scope: string) {
  return page.evaluate(scope => Object.fromEntries(Object.entries(localStorage)
    .filter(([key]) => key.startsWith("motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":"))), scope);
}
async function expectProductionCopies(page: Page, copies: Record<string, string>) {
  expect(await page.evaluate(copies => Object.fromEntries(Object.keys(copies).map(key => [key, localStorage.getItem(key)])), copies)).toEqual(copies);
}
async function downloadedProductionDraft(page: Page, label: string): Promise<ProductionDraft> {
  const pending = page.waitForEvent("download"); await button(page, "Download " + label + " draft").click();
  const path = await (await pending).path();
  if (!path) throw new Error("The recovered Production draft did not produce a download.");
  const value = JSON.parse(await readFile(path, "utf8"));
  expect(value.schema).toBe("motionwright.production-file/1"); return value.draft;
}

test("opaque source attachment keeps all three raw fields through navigation and reload without selecting a replacement for empty or removed assets", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()), raw = fixtureAttachment(initial);
  await attachments(page); await fillAttachment(page, raw);
  await plan(page); await attachments(page); await expectAttachment(page, raw);
  await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.unmount()); await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.mount());
  await attachments(page); await expectAttachment(page, raw);
  await page.reload(); await attachments(page); await expectAttachment(page, raw);
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
  await attachmentAsset(page).selectOption(""); await component(page); await attachments(page); await page.reload(); await attachments(page);
  await expectAttachment(page, { ...raw, assetId: "" }); await expect(button(page, "Attach source to scene")).toBeDisabled();
  await attachmentAsset(page).selectOption(raw.assetId);
  // A controlled browser Change removes this fixture asset; no service or native save is claimed.
  await page.evaluate(asset_id => window.__PRODUCTION_AUTHORITY__.externalChange({ type: "remove_asset", asset_id }), raw.assetId);
  await expect(attachmentAsset(page)).toHaveValue(raw.assetId);
  await expect(attachmentAsset(page).locator("option:checked")).toHaveText("Earlier source asset unavailable");
  await button(page, "Keep my Source attachment input").click();
  await expectAttachment(page, raw); await expect(button(page, "Attach source to scene")).toBeDisabled();
  const changed = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
  await page.reload(); await attachments(page); await expectAttachment(page, raw);
  await expect(button(page, "Attach source to scene")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(changed);
});

for (const fault of ["data", "pointer"] as const) {
  test(`opaque source attachment ${fault} backup failure retains raw input and sends zero changes until explicit recovery`, async ({ page }) => {
    const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()), raw = fixtureAttachment(initial);
    await attachments(page);
    await page.evaluate(fault => {
      const set = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (fault === "data" && this === localStorage && key.startsWith("motionwright.production-recovery.v1:")) return;
        if (fault === "pointer" && this === sessionStorage && key.startsWith("motionwright.production-writer.v1:")) return;
        return set.call(this, key, value);
      };
      window.__PRODUCTION_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; };
    }, fault);
    await fillAttachment(page, raw);
    await expect(button(page, "Attach source to scene")).toBeDisabled();
    await expect(page.getByRole("alert", { name: "Source attachment backup warning", exact: true })).toBeVisible();
    await plan(page); await attachments(page); await expectAttachment(page, raw);
    expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
    await page.evaluate(() => window.__PRODUCTION_RESTORE_STORAGE__?.()); await button(page, "Retry Source attachment backup").click();
    const sent = await submit(page, "Attach source to scene");
    expect(sent.change).toMatchObject({ type: "upsert_native_capsule", capsule: { scene_id: initial.scenes[0].id,
      source_asset_id: raw.assetId, fidelity: { renderer: raw.renderer, renderer_version: raw.version } } });
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), sent.id);
    await expectAttachment(page, raw); await expect(button(page, "Attach source to scene")).toBeDisabled();
    expect(await requests(page)).toHaveLength(1);
  });
}

test("opaque source attachment known NOT_SENT remains explicitly saveable on its original revision after verified recovery", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()), raw = fixtureAttachment(initial);
  await attachments(page); await fillAttachment(page, raw);
  await page.evaluate(() => {
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (this === localStorage && key.startsWith("motionwright.production-recovery.v1:") && JSON.parse(value).draft.pending) return;
      return set.call(this, key, value);
    };
    window.__PRODUCTION_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = set; };
  });
  await button(page, "Attach source to scene").click();
  await expect(page.getByRole("status").filter({ hasText: "This preparation was NOT_SENT" })).toBeVisible();
  await expect(button(page, "Attach source to scene")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
  await page.evaluate(() => window.__PRODUCTION_RESTORE_STORAGE__?.()); await button(page, "Retry Source attachment backup").click();
  await expect(button(page, "Attach source to scene")).toBeEnabled();
  const sent = await submit(page, "Attach source to scene"); expect(sent.source.revision).toBe(initial.revision);
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), sent.id);
  await expectAttachment(page, raw); expect(await requests(page)).toHaveLength(1);
});

test("opaque source attachment scene drafts remain separate and a late original ACK preserves later input without retargeting either scene", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()), raw = fixtureAttachment(initial);
  expect(initial.scenes.length).toBeGreaterThan(1);
  const sceneA = initial.scenes[0].id, sceneB = initial.scenes[1].id;
  const later = { ...raw, renderer: "  Later renderer A  ", version: "  Later version A  " }, other = { ...raw, renderer: "Independent scene B", version: "B raw version" };
  await attachments(page); await fillAttachment(page, raw);
  await page.getByLabel("Production working scene").selectOption(sceneB);
  await expectAttachment(page, { assetId: "", renderer: "hyperframes", version: "unverified" }); await fillAttachment(page, other);
  await page.getByLabel("Production working scene").selectOption(sceneA); await expectAttachment(page, raw);
  const original = await submit(page, "Attach source to scene");
  await fillAttachment(page, later); await page.getByLabel("Production working scene").selectOption(sceneB); await expectAttachment(page, other);
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), original.id); await expectAttachment(page, other);
  await page.getByLabel("Production working scene").selectOption(sceneA); await expectAttachment(page, later);
  const second = await submit(page, "Attach source to scene");
  if (original.change.type !== "upsert_native_capsule" || second.change.type !== "upsert_native_capsule") throw new Error("Attachment dispatched a different operation.");
  const firstCapsule = original.change.capsule, secondCapsule = second.change.capsule;
  expect(firstCapsule.scene_id).toBe(sceneA); expect(secondCapsule.scene_id).toBe(sceneA);
  expect(secondCapsule.id).not.toBe(firstCapsule.id);
  expect(secondCapsule.fidelity).toMatchObject({ renderer: later.renderer, renderer_version: later.version });
  expect(second.source.revision).toBe(original.source.revision + 1);
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), second.id);
  const saved = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
  expect(saved.production_design!.capsules.find(capsule => capsule.id === firstCapsule.id)).toEqual(firstCapsule);
  expect(saved.production_design!.capsules.find(capsule => capsule.id === secondCapsule.id)).toEqual(secondCapsule);
  await page.getByLabel("Production working scene").selectOption(sceneB); await expectAttachment(page, other);
  await page.reload(); await attachments(page); await expectAttachment(page, other); expect(await requests(page)).toEqual([]);
});

for (const target of ["component", "plan", "source"] as const) {
  test(`Production ${target} UNKNOWN on its original revision stays blocked; a newer reviewed revision preserves original bytes without replay`, async ({ page }) => {
    // Align the fixture's independently stored props with its canonical browser
    // Project/history through a declared browser Change before inspecting them.
    await page.evaluate(target => window.__PRODUCTION_AUTHORITY__.externalChange({ type: "rename_project", title: "Production outcome baseline " + target }), target);
    const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
    const label = target === "component" ? "Component" : target === "plan" ? "Production plan" : "Source attachment";
    const submitLabel = target === "component" ? "Add to scene" : target === "plan" ? "Save production plan" : "Attach source to scene";
    const kind = target === "component" ? "hero" : target;
    const scope = JSON.stringify([initial.id, initial.generation, initial.active_branch, kind, target === "plan" ? null : initial.scenes[0].id]);
    if (target === "plan") { await plan(page); await concept(page).fill("Original uncertain plan"); }
    else if (target === "source") { await attachments(page); await fillAttachment(page, fixtureAttachment(initial)); }
    else await headline(page).fill("Original uncertain component");
    const sent = await submit(page, submitLabel);
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.reject(id, "Controlled response delivery failure; no original browser Change was observed."), sent.id);
    const input = target === "component" ? headline(page) : target === "plan" ? concept(page) : attachmentRenderer(page);
    const retainedInput = target === "source" ? fixtureAttachment(initial).renderer : "  Later authored input retained  ";
    if (target !== "source") await input.fill(retainedInput);
    const originals = await productionCopiesFor(page, scope); expect(Object.keys(originals).length).toBeGreaterThan(0);
    const originalAttempts = Object.values(originals).map(raw => (JSON.parse(raw) as { draft: ProductionDraft }).draft.pending).filter(value => value !== null);
    expect(originalAttempts).toHaveLength(1);
    const unknown = page.getByRole("region", { name: "Unconfirmed " + label, exact: true }); await expect(unknown).toBeVisible();
    await button(page, "Read saved " + label + " and history").click();
    const reviewed = page.getByLabel(label + " outcome review", { exact: true });
    await expect(reviewed).toContainText("Read saved r" + initial.revision + " and 0 consecutive history records");
    await expect(reviewed).toContainText("could still commit");
    await expect(button(page, "Keep earlier " + label + " attempt and continue editing")).toBeDisabled();
    await expect(button(page, submitLabel)).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
    await page.reload(); if (target === "plan") await plan(page); else if (target === "source") await attachments(page);
    await expect(input).toHaveValue(retainedInput); await expect(unknown).toBeVisible();
    await expect(button(page, submitLabel)).toBeDisabled();
    expect((await downloadedProductionDraft(page, label)).pending).toEqual(originalAttempts[0]);
    expect(await requests(page)).toEqual([]); await expectProductionCopies(page, originals);
    // This component fixture restores props on reload, while api.ts starts a
    // fresh in-memory browser Project/history. A declared browser Change below
    // aligns them before another read. SQLite reads after reload are covered by
    // production-design-service.spec.ts; no saved history is fabricated here.
    await page.evaluate(target => window.__PRODUCTION_AUTHORITY__.externalChange({ type: "rename_project", title: "Independent later revision " + target }), target);
    const advanced = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
    expect(advanced.revision).toBe(initial.revision + 1);
    expect(advanced.branches.find(branch => branch.id === advanced.active_branch)?.head_revision).toBe(advanced.revision);
    await button(page, "Read saved " + label + " and history").click();
    await expect(reviewed).toContainText("Read saved r" + advanced.revision + " and 1 consecutive history records");
    await expect(button(page, "Keep earlier " + label + " attempt and continue editing")).toBeEnabled();
    // A different clock value must not rewrite an identical retained attempt.
    await page.evaluate(() => { const now = Date.now(); Date.now = () => now + 2_000; });
    await button(page, "Keep earlier " + label + " attempt and continue editing").click();
    await expect(unknown).toHaveCount(0); await expect(input).toHaveValue(retainedInput);
    await expectProductionCopies(page, originals); expect(await requests(page)).toEqual([]);
    expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(advanced);
    await expect(button(page, submitLabel)).toBeEnabled();
    if (target === "source") {
      // With valid unchanged fields, Continue is explicit separation of intent,
      // not an ACK or an attachment. Only this later Attach prepares a new ID.
      const separate = await submit(page, submitLabel);
      if (sent.change.type !== "upsert_native_capsule" || separate.change.type !== "upsert_native_capsule") throw new Error("Attachment dispatched a different operation.");
      const originalCapsule = sent.change.capsule, separateCapsule = separate.change.capsule;
      expect(separateCapsule.id).not.toBe(originalCapsule.id);
      expect({ ...separateCapsule, id: originalCapsule.id }).toEqual(originalCapsule);
      expect(separate.source.revision).toBe(advanced.revision);
      await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.resolve(id), separate.id);
      expect(await requests(page)).toHaveLength(1); await expectProductionCopies(page, originals);
      await expect(button(page, submitLabel)).toBeDisabled();
    }
  });
}

test("opaque source attachment lost committed reply keeps the submitted identity and later cleared fields through reload and explicit review without replay", async ({ page }) => {
  const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project()), raw = fixtureAttachment(initial);
  await attachments(page); await fillAttachment(page, raw);
  const sent = await submit(page, "Attach source to scene");
  if (sent.change.type !== "upsert_native_capsule") throw new Error("Attachment dispatched a different operation.");
  const submittedCapsule = sent.change.capsule;
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.observeOriginal(id), sent.id);
  await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.reject(id, "The browser Change committed; original reply delivery was lost."), sent.id);
  const later = { assetId: "", renderer: "", version: "  Later incomplete version  " }; await fillAttachment(page, later);
  const scope = JSON.stringify([initial.id, initial.generation, initial.active_branch, "source", initial.scenes[0].id]);
  const originals = await productionCopiesFor(page, scope); expect(Object.keys(originals).length).toBeGreaterThan(0);
  const observed = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
  expect(observed.production_design!.capsules.find(capsule => capsule.id === submittedCapsule.id)).toEqual(submittedCapsule);
  await expectAttachment(page, later); await expect(button(page, "Attach source to scene")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
  // Inspect the actual browser Change while this fixture's in-memory journal
  // still exists. Its independent props survive reload; that journal does not.
  // The real-service case reads committed history after reload from SQLite.
  await button(page, "Read saved Source attachment and history").click();
  const review = page.getByLabel("Source attachment outcome review", { exact: true });
  await expect(review).toContainText("1 contain matching values");
  await review.locator("summary").click();
  const snapshots = JSON.parse((await review.locator("pre").textContent())!);
  expect(snapshots.submitted).toEqual(submittedCapsule); expect(snapshots.saved).toEqual([submittedCapsule]);
  await button(page, "Keep earlier Source attachment attempt and continue editing").click();
  await expect(page.getByRole("region", { name: "Unconfirmed Source attachment", exact: true })).toHaveCount(0);
  await expectAttachment(page, later); await expect(button(page, "Attach source to scene")).toBeDisabled();
  await expectProductionCopies(page, originals); expect(await requests(page)).toHaveLength(1);
  expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(observed);
  await page.reload(); await attachments(page); await expectAttachment(page, later);
  await expect(page.getByRole("region", { name: "Unconfirmed Source attachment", exact: true })).toHaveCount(0);
  await expect(button(page, "Attach source to scene")).toBeDisabled();
  await expectProductionCopies(page, originals); expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(observed);
});

for (const viewport of [{ width: 1440, height: 940 }, { width: 720, height: 470 }]) {
  test(`opaque source recovery JSON stays contained and keyboard reachable at ${viewport.width}x${viewport.height} CSS pixels without changing author intent`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    // Styled component QA only. 720x470 is a reflow equivalent of 1440x940 at
    // 200 percent, not a claim that browser zoom or SQLite was exercised.
    await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.externalChange({ type: "rename_project", title: "Component source reflow observation" }));
    const initial = await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project());
    const raw = { ...fixtureAttachment(initial), renderer: "R".repeat(117), version: "  Authored layout observation  " };
    await attachments(page); await fillAttachment(page, raw);
    // The existing reset action retains a separate copy, without a Project
    // mutation. This copy has valid long input to inspect beside a later UNKNOWN.
    await button(page, "Reset Source attachment input").click();
    await expectAttachment(page, { assetId: "", renderer: "hyperframes", version: "unverified" });
    await fillAttachment(page, raw);
    const sent = await submit(page, "Attach source to scene");
    if (sent.change.type !== "upsert_native_capsule") throw new Error("The reflow scenario dispatched a different operation.");
    const submittedCapsule = sent.change.capsule;
    await page.evaluate(id => window.__PRODUCTION_AUTHORITY__.reject(id, "Controlled component layout observation: original reply unavailable."), sent.id);
    const unknown = page.getByRole("region", { name: "Unconfirmed Source attachment", exact: true }); await expect(unknown).toBeVisible();
    await button(page, "Read saved Source attachment and history").click();
    const review = page.getByLabel("Source attachment outcome review", { exact: true });
    await expect(review).toContainText("Read saved r" + initial.revision + " and 0 consecutive history records");
    await expect(button(page, "Keep earlier Source attachment attempt and continue editing")).toBeDisabled();
    const outcomeSummary = review.locator("summary"), outcomePre = review.locator("pre");
    await outcomeSummary.focus(); await page.keyboard.press("Space"); await expect(outcomePre).toBeVisible();
    const copies = page.getByRole("region", { name: "Source attachment recovery copies", exact: true });
    const copiesSummary = copies.locator(":scope > summary");
    await copiesSummary.focus(); await page.keyboard.press("Space");
    const copiedGroup = copies.getByRole("group", { name: /^Source attachment recovery copy [0-9]+$/ }).filter({ hasText: raw.renderer });
    await expect(copiedGroup).toHaveCount(1);
    const copyName = await copiedGroup.getAttribute("aria-label");
    if (!copyName) throw new Error("The retained source input has no named recovery group.");
    const copyNumber = copyName.match(/copy ([0-9]+)$/)?.[1];
    if (!copyNumber) throw new Error("The source recovery group has no copy identity.");
    const copiedSummary = copiedGroup.locator("summary"), copiedPre = copiedGroup.locator("pre");
    await copiedSummary.focus(); await page.keyboard.press("Space"); await expect(copiedPre).toBeVisible();
    expect(JSON.parse((await outcomePre.textContent())!)).toEqual({ submitted: submittedCapsule, saved: [] });
    expect(JSON.parse((await copiedPre.textContent())!)).toEqual({ kind: "source", ...raw });
    const scope = JSON.stringify([initial.id, initial.generation, initial.active_branch, "source", initial.scenes[0].id]);
    const originalCopies = await productionCopiesFor(page, scope), beforeRequests = await requests(page);
    expect(beforeRequests).toHaveLength(1);
    const geometry = await page.evaluate(copyName => {
      const workspace = document.querySelector<HTMLElement>(".production-workspace"), sources = document.querySelector<HTMLElement>(".production-sources");
      const outcome = document.querySelector<HTMLElement>('[aria-label="Source attachment outcome review"] pre');
      const copy = [...document.querySelectorAll<HTMLElement>('[role="group"]')].find(element => element.getAttribute("aria-label") === copyName)?.querySelector<HTMLElement>("pre");
      if (!workspace || !sources || !outcome || !copy) throw new Error("The actual Production recovery layout is incomplete.");
      const measure = (element: HTMLElement) => {
        const rect = element.getBoundingClientRect(), style = getComputedStyle(element);
        return { left: rect.left, right: rect.right, width: rect.width, height: rect.height,
          clientWidth: element.clientWidth, scrollWidth: element.scrollWidth, overflowX: style.overflowX, whiteSpace: style.whiteSpace };
      };
      return { viewport: { width: innerWidth, height: innerHeight }, workspace: measure(workspace), sources: measure(sources),
        grids: getComputedStyle(sources.querySelector<HTMLElement>(":scope > .production-plan-grid")!).gridTemplateColumns,
        json: [measure(outcome), measure(copy)] };
    }, copyName);
    expect(geometry.viewport).toEqual(viewport);
    expect(geometry.workspace.scrollWidth, "Production workspace must not gain horizontal overflow from a recovery JSON block").toBeLessThanOrEqual(geometry.workspace.clientWidth + 1);
    expect(geometry.sources.scrollWidth, "Source authoring controls must stay within their own content width").toBeLessThanOrEqual(geometry.sources.clientWidth + 1);
    for (const json of geometry.json) {
      expect(json.left).toBeGreaterThanOrEqual(geometry.sources.left - 1); expect(json.right).toBeLessThanOrEqual(geometry.sources.right + 1);
      if (json.scrollWidth > json.clientWidth + 1) expect(["auto", "scroll"]).toContain(json.overflowX);
    }
    const focusObservations: { name: string; outline: string; boxShadow: string;
      scrollBefore: { left: number; top: number; maxX: number; maxY: number }; scrollAfter: { left: number; top: number } }[] = [];
    for (const view of [
      { summary: outcomeSummary, pre: outcomePre, name: "Source attachment submitted and saved parameters" },
      { summary: copiedSummary, pre: copiedPre, name: "Source attachment copied input " + copyNumber },
    ]) {
      await view.summary.focus(); await expect(view.summary).toBeFocused();
      await page.keyboard.press("Tab"); await expect(view.pre, "Tab must reach the actual scrollable JSON region").toBeFocused();
      await expect(page.getByRole("region", { name: view.name, exact: true })).toBeFocused();
      const focus = await view.pre.evaluate(element => {
        const style = getComputedStyle(element);
        return { outline: style.outline, outlineVisible: style.outlineStyle !== "none" && Number.parseFloat(style.outlineWidth) > 0,
          boxShadow: style.boxShadow };
      });
      expect(focus.outlineVisible || focus.boxShadow !== "none", "Keyboard focus must remain visible on the JSON region").toBe(true);
      const scrollBefore = await view.pre.evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop,
        maxX: element.scrollWidth - element.clientWidth, maxY: element.scrollHeight - element.clientHeight }));
      if (scrollBefore.maxX > 1) {
        await page.keyboard.press("ArrowRight");
        await expect.poll(() => view.pre.evaluate(element => element.scrollLeft), { message: "Keyboard must reach horizontally overflowing JSON text" }).toBeGreaterThan(scrollBefore.left);
      }
      if (scrollBefore.maxY > 1) {
        await page.keyboard.press("ArrowDown");
        await expect.poll(() => view.pre.evaluate(element => element.scrollTop), { message: "Keyboard must reach vertically overflowing JSON text" }).toBeGreaterThan(scrollBefore.top);
      }
      const scrollAfter = await view.pre.evaluate(element => ({ left: element.scrollLeft, top: element.scrollTop }));
      focusObservations.push({ name: view.name, outline: focus.outline, boxShadow: focus.boxShadow, scrollBefore, scrollAfter });
      await page.keyboard.press("Shift+Tab"); await expect(view.summary).toBeFocused();
      await page.keyboard.press("Space"); await expect(view.pre).toBeHidden();
      await page.keyboard.press("Space"); await expect(view.pre).toBeVisible();
    }
    await page.keyboard.press("Tab"); await expect(copiedPre).toBeFocused();
    expect(JSON.parse((await outcomePre.textContent())!)).toEqual({ submitted: submittedCapsule, saved: [] });
    expect(JSON.parse((await copiedPre.textContent())!)).toEqual({ kind: "source", ...raw });
    await expectAttachment(page, raw); await expect(unknown).toBeVisible();
    await expect(button(page, "Attach source to scene")).toBeDisabled();
    expect(await requests(page)).toEqual(beforeRequests); expect(await page.evaluate(() => window.__PRODUCTION_AUTHORITY__.project())).toEqual(initial);
    await expectProductionCopies(page, originalCopies);
    await info.attach("source-recovery-reflow-observation", { body: JSON.stringify({ scope: "styled component layout and keyboard only; no service, native render, or actual zoom claim",
      geometry, focusObservations, requestsBeforeAndAfter: beforeRequests.length, authorInputUnchanged: true }), contentType: "application/json" });
    await page.screenshot({ path: info.outputPath(`source-recovery-${viewport.width}x${viewport.height}.png`), fullPage: true });
  });
}
