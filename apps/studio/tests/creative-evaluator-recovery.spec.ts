import { randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import type { CreativeFixtureRequest } from "./fixtures/creative-workbench-authority";
import type { CreativeEvaluatorBundle } from "../src/creativeEvaluator";
import { EVALUATOR_COPY_PREFIX, evaluatorCopyKey, evaluatorPointerKey, evaluatorScope, type EvaluatorCopy } from "../src/creativeEvaluatorDraft";

// Actual Workbench, author custody and ProjectMutationGate, with the existing
// labelled component fixture controlling response scheduling and Project props.
// These assertions are not StudioService, SQLite, installed Tauri or native proof.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const recovery = (page: Page) => page.getByRole("region", { name: "Evaluator author input recovery", exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Unconfirmed evaluator operation", exact: true });
const requests = (page: Page) => page.evaluate(() => window.__CREATIVE_AUTHORITY__.requests());
const savedNotice = (page: Page) => recovery(page).getByRole("status").filter({ hasText: "Saved evaluator input at r" });
function bundle(): CreativeEvaluatorBundle {
  return { schema: "motionwright.creative-evaluator/1", id: randomUUID(), version: 1,
    label: "Original component test evaluator", purpose: "Exercise data-only author retention",
    author_attestation: "I authored this controlled test checklist.", playbook: ["Inspect original editable source."],
    criteria: [{ kind: "minimum_nodes", id: randomUUID(), minimum: 1 },
      { kind: "human_question", id: randomUUID(), question: "Does the authored scene communicate clearly?" }] };
}
async function open(page: Page) {
  await button(page, "Evaluators").click();
  await expect(page.getByRole("heading", { name: "Creative evaluation", exact: true })).toBeVisible();
}
async function author(page: Page) {
  await field(page, "Evaluator author declaration").fill("I authored this controlled test checklist.");
  await expect(button(page, "Register data-only evaluator")).toBeEnabled();
}
async function importInput(page: Page, value: CreativeEvaluatorBundle) {
  await field(page, "Import evaluator JSON").fill(JSON.stringify(value));
  await field(page, "Confirm evaluator source rights").check();
  await expect(button(page, "Import original evaluator JSON")).toBeEnabled();
}
async function send(page: Page, name: string): Promise<CreativeFixtureRequest> {
  const count = (await requests(page)).length;
  await button(page, name).click(); await expect.poll(async () => (await requests(page)).length).toBe(count + 1);
  return (await requests(page))[count];
}
async function ownCopy(page: Page) {
  const project = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project()), scope = evaluatorScope(project);
  const writer = await page.evaluate(key => sessionStorage.getItem(key), evaluatorPointerKey(scope));
  if (!writer) throw new Error("No confirmed component evaluator recovery pointer.");
  const key = evaluatorCopyKey(scope, writer), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("No confirmed component evaluator recovery bytes.");
  return { key, raw, copy: JSON.parse(raw) as EvaluatorCopy };
}
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/creative-workbench-authority.html");
  await expect(field(page, "Headline")).toBeVisible(); await open(page);
});

test("Evaluator raw invalid import and numeric input survive navigation, remount and reload without Project changes", async ({ page }) => {
  const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await author(page);
  await field(page, "Evaluator minimum nodes").fill("");
  await field(page, "Evaluator minimum nodes").pressSequentially("1e");
  const invalid = "  {\"schema\":\"unfinished evaluator\",\n";
  await field(page, "Import evaluator JSON").fill(invalid);
  await field(page, "Confirm evaluator source rights").check();
  await button(page, "Import original evaluator JSON").click();
  await expect(recovery(page).getByRole("alert")).toBeVisible();
  await expect(button(page, "Register data-only evaluator")).toBeDisabled();
  await button(page, "Production plan").click(); await open(page);
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.unmount());
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.mount()); await open(page);
  await page.reload(); await expect(field(page, "Headline")).toBeVisible(); await open(page);
  await expect(field(page, "Evaluator minimum nodes")).toHaveValue("1e");
  await expect(field(page, "Import evaluator JSON")).toHaveValue(invalid);
  await expect(field(page, "Confirm evaluator source rights")).toBeChecked();
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
});

test("Evaluator original import ACK preserves later JSON, confirmation and a deliberately blank selection", async ({ page }) => {
  const value = bundle(); await importInput(page, value);
  const sent = await send(page, "Import original evaluator JSON"), armed = await ownCopy(page);
  const later = "  {\"later\":\"unfinished input\",\n";
  await field(page, "Import evaluator JSON").fill(later);
  await field(page, "Confirm evaluator source rights").uncheck();
  // Selecting the existing blank option is explicit later intent even though
  // its raw value is the same as the pre-send default.
  await field(page, "Evaluator source version").selectOption("");
  await page.evaluate(id => window.__CREATIVE_AUTHORITY__.resolve(id), sent.id);
  await expect(savedNotice(page)).toBeVisible(); await expect(unknown(page)).toHaveCount(0);
  await expect(field(page, "Import evaluator JSON")).toHaveValue(later);
  await expect(field(page, "Confirm evaluator source rights")).not.toBeChecked();
  await expect(field(page, "Evaluator source version")).toHaveValue("");
  const after = await ownCopy(page);
  expect(after.copy.draft.pending).toBeNull(); expect(after.copy.draft.fields.selected.raw).toBe("");
  expect(after.copy.draft.fields.selected.sequence).toBeGreaterThan(armed.copy.draft.pending!.sentFields.selected.sequence);
  expect(await page.evaluate(key => localStorage.getItem(key), armed.key)).toBe(armed.raw);
  const saved = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  expect(saved.production_design?.evaluator_bundles?.at(-1)).toEqual(value);
  await page.reload(); await expect(field(page, "Headline")).toBeVisible(); await open(page);
  await expect(field(page, "Import evaluator JSON")).toHaveValue(later);
  await expect(field(page, "Evaluator source version")).toHaveValue("");
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(saved);
  expect(await requests(page)).toEqual([]);
});

test("Evaluator revision-only callback cannot clear pending input or announce registration", async ({ page }) => {
  await author(page); const sent = await send(page, "Register data-only evaluator");
  await page.evaluate(id => window.__CREATIVE_AUTHORITY__.resolveWithoutOperation(id), sent.id);
  await expect(unknown(page)).toContainText("UNKNOWN"); await expect(savedNotice(page)).toHaveCount(0);
  await expect(button(page, "Register data-only evaluator")).toBeDisabled();
  await expect(field(page, "Evaluator author declaration")).toHaveValue("I authored this controlled test checklist.");
  const retained = await ownCopy(page); expect(retained.copy.draft.pending?.changeJson).toBe(JSON.stringify(sent.change));
  expect((await requests(page)).length).toBe(1);
});

test("Evaluator observed matching Project plus lost original reply remains UNKNOWN after remount and reload", async ({ page }) => {
  const value = bundle(); await importInput(page, value); const sent = await send(page, "Import original evaluator JSON");
  await field(page, "Import evaluator JSON").fill("  retained after dispatch  ");
  await page.evaluate(id => window.__CREATIVE_AUTHORITY__.observeOriginal(id), sent.id);
  await expect(unknown(page)).toBeVisible(); await expect(savedNotice(page)).toHaveCount(0);
  await page.evaluate(id => window.__CREATIVE_AUTHORITY__.reject(id, "Controlled original reply delivery loss"), sent.id);
  const stored = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await page.reload(); await expect(field(page, "Headline")).toBeVisible(); await open(page);
  await expect(field(page, "Import evaluator JSON")).toHaveValue("  retained after dispatch  ");
  await expect(unknown(page)).toContainText("UNKNOWN"); await expect(savedNotice(page)).toHaveCount(0);
  await expect(button(page, "Import original evaluator JSON")).toBeDisabled();
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(stored);
});

test("Evaluator late reply for a former generation does not acknowledge or erase the current owner's input", async ({ page }) => {
  await author(page); const before = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  const sent = await send(page, "Register data-only evaluator"), other = randomUUID();
  await page.evaluate(generation => window.__CREATIVE_AUTHORITY__.setIdentity("generation", generation), other);
  await field(page, "Evaluator label").fill("Independent current owner");
  await page.evaluate(id => window.__CREATIVE_AUTHORITY__.resolve(id), sent.id);
  await expect(field(page, "Evaluator label")).toHaveValue("Independent current owner");
  await expect(savedNotice(page)).toHaveCount(0); await expect(unknown(page)).toHaveCount(0);
  expect((await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).generation).toBe(other);
  await page.evaluate(generation => window.__CREATIVE_AUTHORITY__.setIdentity("generation", generation), before.generation);
  await expect(field(page, "Evaluator author declaration")).toHaveValue("I authored this controlled test checklist.");
  await expect(unknown(page)).toContainText("UNKNOWN"); expect((await requests(page)).length).toBe(1);
});

test("Evaluator initial Storage accessor failure keeps its editor mounted and retains later raw input", async ({ page }) => {
  await author(page); await field(page, "Evaluator label").fill("Original input before inaccessible storage");
  const before = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await page.addInitScript(() => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    if (!descriptor) throw new Error("No actual localStorage accessor to fault.");
    Object.defineProperty(window, "localStorage", { configurable: true, get() {
      throw new DOMException("Controlled initial evaluator Storage accessor failure", "SecurityError");
    } });
    window.__CREATIVE_RESTORE_STORAGE__ = () => Object.defineProperty(window, "localStorage", descriptor);
  });
  await page.reload(); await expect(field(page, "Headline")).toBeVisible(); await open(page);
  await expect(button(page, "Retry evaluator recovery")).toBeVisible();
  await field(page, "Evaluator label").fill("Later input while the original copy is inaccessible");
  await expect(button(page, "Register data-only evaluator")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  await page.evaluate(() => window.__CREATIVE_RESTORE_STORAGE__?.());
  await button(page, "Retry evaluator recovery").click();
  await expect(button(page, "Retry evaluator recovery")).toHaveCount(0);
  await expect(field(page, "Evaluator label")).toHaveValue("Later input while the original copy is inaccessible");
  await expect(field(page, "Evaluator author declaration")).toHaveValue("I authored this controlled test checklist.");
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(before);
  expect(await requests(page)).toEqual([]);
});

for (const lost of ["copy", "pointer"] as const) {
  test(`Evaluator silent ${lost} write loss blocks callback until exact readback is repaired`, async ({ page }) => {
    await author(page); const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
    await page.evaluate(({ lost, prefix }) => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if ((lost === "copy" && this === localStorage && key.startsWith(prefix)) ||
          (lost === "pointer" && this === sessionStorage && key.startsWith("motionwright.evaluator-writer.v1:"))) return;
        original.call(this, key, value);
      };
      window.__CREATIVE_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = original; };
    }, { lost, prefix: EVALUATOR_COPY_PREFIX });
    try {
      // Change bytes so a silent no-op cannot pass equality against the old copy.
      await field(page, "Evaluator label").fill("Input requiring a newly confirmed copy");
      if (lost === "pointer") await button(page, "Register data-only evaluator").click();
      await expect(button(page, "Retry evaluator recovery")).toBeVisible();
      await expect(button(page, "Register data-only evaluator")).toBeDisabled();
      expect(await requests(page)).toEqual([]);
      expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
      await expect(field(page, "Evaluator label")).toHaveValue("Input requiring a newly confirmed copy");
    } finally { await page.evaluate(() => window.__CREATIVE_RESTORE_STORAGE__?.()); }
    await button(page, "Retry evaluator recovery").click();
    await expect(button(page, "Retry evaluator recovery")).toHaveCount(0);
    expect(await requests(page)).toEqual([]);
  });
}
