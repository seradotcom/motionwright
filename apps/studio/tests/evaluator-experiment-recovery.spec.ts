import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { CreativeEvaluatorBundle } from "../src/creativeEvaluator";
import { experimentCopyKey, experimentPointerKey, experimentScope, type ExperimentCopy } from "../src/evaluatorExperimentDraft";
import type { CreativeAuthorityHarness } from "./fixtures/creative-workbench-authority";

// The existing explicitly labelled component fixture runs actual browser
// domain changes and Workbench code. It does not supply native admission,
// StudioService persistence, installed Tauri or a creative quality judgment.
declare global { interface Window { __EXPERIMENT_RESTORE_STORAGE__?: () => void } }
const harness = (page: Page) => page.evaluate(() => (window.__CREATIVE_AUTHORITY__ as CreativeAuthorityHarness).project());
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const candidateField = (page: Page) => page.getByLabel("Evaluator experimental candidate JSON", { exact: true });
const rationaleField = (page: Page) => page.getByLabel("Experiment owner review rationale", { exact: true });
const comparison = (page: Page) => page.getByRole("region", { name: "Source-bound evaluator experiment", exact: true });
const recovery = (page: Page) => page.getByRole("region", { name: "Evaluator experiment recovery", exact: true });
function sourceBundle(): CreativeEvaluatorBundle {
  return { schema: "motionwright.creative-evaluator/1", id: randomUUID(), version: 1, label: "Original component experiment source",
    purpose: "Inspect bounded original source data", author_attestation: "I authored this controlled data-only checklist.",
    playbook: ["Inspect original editable source objects."], criteria: [
      { kind: "minimum_nodes", id: randomUUID(), minimum: 2 },
      { kind: "human_question", id: randomUUID(), question: "Does the authored source communicate clearly?" },
    ] };
}
async function open(page: Page) {
  await button(page, "Evaluators").click(); await expect(candidateField(page)).toBeVisible();
}
async function validCandidate(page: Page) {
  await button(page, "Prepare a proposed v2 manifest").click();
  const value = JSON.parse(await candidateField(page).inputValue()) as CreativeEvaluatorBundle;
  const minimum = value.criteria.find(item => item.kind === "minimum_nodes");
  if (!minimum || minimum.kind !== "minimum_nodes") throw new Error("Original fixture lacks its bounded criterion.");
  minimum.minimum++;
  await candidateField(page).fill(JSON.stringify(value)); return value;
}
async function ownCopy(page: Page) {
  const project = await harness(page), baseline = project.production_design!.evaluator_bundles!.at(-1)!;
  const scope = experimentScope(project, baseline), writer = await page.evaluate(key => sessionStorage.getItem(key), experimentPointerKey(scope));
  if (!writer) throw new Error("Missing retained experiment pointer.");
  const key = experimentCopyKey(scope, writer), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("Missing retained experiment bytes."); return { key, raw, copy: JSON.parse(raw) as ExperimentCopy };
}
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/creative-workbench-authority.html");
  await expect(page.getByLabel("Headline", { exact: true })).toBeVisible();
  await page.evaluate(bundle => window.__CREATIVE_AUTHORITY__.externalChange({ type: "register_creative_evaluator", bundle }), sourceBundle());
  await open(page);
});

test("Experiment invalid candidate, rationale and chosen source scenes survive navigation, remount and reload", async ({ page }) => {
  const before = await harness(page), raw = "  {\"candidate\":\"unfinished\",\n", reason = "  My retained local review reasoning  ";
  await candidateField(page).fill(raw); await rationaleField(page).fill(reason);
  const scenes = page.getByRole("group", { name: "Choose up to three existing scenes to compare", exact: true });
  await scenes.getByRole("checkbox", { name: before.scenes[1].name, exact: true }).check();
  await page.getByLabel(/I have permission to register this original/).check();
  const original = await ownCopy(page);
  await button(page, "Production plan").click(); await open(page);
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.unmount()); await page.evaluate(() => window.__CREATIVE_AUTHORITY__.mount()); await open(page);
  await page.reload(); await expect(page.getByLabel("Headline", { exact: true })).toBeVisible(); await open(page);
  await expect(candidateField(page)).toHaveValue(raw); await expect(rationaleField(page)).toHaveValue(reason);
  await expect(scenes.getByRole("checkbox", { name: before.scenes[1].name, exact: true })).toBeChecked();
  await expect(page.getByLabel(/I have permission to register this original/)).toBeChecked();
  await expect(comparison(page)).toHaveCount(0); expect(await harness(page)).toEqual(before);
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.requests())).toEqual([]);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
});

test("Experiment comparison remains technical-only and is invalidated by later candidate or working-scene changes", async ({ page }) => {
  const before = await harness(page), value = await validCandidate(page);
  await rationaleField(page).fill("Preserve this explicit local reviewer explanation.");
  await button(page, "Run disposable technical comparison").click(); await expect(comparison(page)).toBeVisible();
  await expect(comparison(page)).toContainText("Human questions always remain not assessed");
  await page.getByLabel(/I have permission to register this original/).check();
  await page.getByLabel(/I reviewed the per-scene technical changes/).check();
  await expect(page.getByRole("button", { name: /Register reviewed v2 via project CAS/ })).toBeDisabled();
  await expect(page.getByText(/Browser demo may compare local data/)).toBeVisible();
  await candidateField(page).fill(JSON.stringify({ ...value, label: "A later original candidate label" }));
  await expect(comparison(page)).toHaveCount(0);
  await button(page, "Run disposable technical comparison").click(); await expect(comparison(page)).toBeVisible();
  await page.evaluate(sceneId => window.__CREATIVE_AUTHORITY__.selectScene(sceneId), before.scenes[1].id);
  await expect(comparison(page)).toHaveCount(0); await expect(rationaleField(page)).toHaveValue("Preserve this explicit local reviewer explanation.");
  expect(await harness(page)).toEqual(before); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.requests())).toEqual([]);
});

test("Experiment failed initial Storage accessor recovers exact older input while retaining later blank rationale", async ({ page }) => {
  const raw = JSON.stringify(await validCandidate(page)); await rationaleField(page).fill("Original local review rationale before storage access fails.");
  const before = await harness(page), original = await ownCopy(page);
  await page.addInitScript(() => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    if (!descriptor) throw new Error("Missing actual localStorage accessor.");
    Object.defineProperty(window, "localStorage", { configurable: true, get() {
      throw new DOMException("Controlled experiment first Storage access failure", "SecurityError");
    } });
    window.__EXPERIMENT_RESTORE_STORAGE__ = () => Object.defineProperty(window, "localStorage", descriptor);
  });
  await page.reload(); await expect(page.getByLabel("Headline", { exact: true })).toBeVisible(); await open(page);
  await expect(button(page, "Retry experiment recovery")).toBeVisible();
  await rationaleField(page).fill("Later explanation while the original is inaccessible"); await rationaleField(page).fill("");
  await page.evaluate(() => window.__EXPERIMENT_RESTORE_STORAGE__?.()); await button(page, "Retry experiment recovery").click();
  await expect(button(page, "Retry experiment recovery")).toHaveCount(0); await expect(candidateField(page)).toHaveValue(raw);
  await expect(rationaleField(page)).toHaveValue(""); await expect(comparison(page)).toHaveCount(0);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  expect(await harness(page)).toEqual(before); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.requests())).toEqual([]);
});

test("Experiment corrupt first-observed source cannot be replaced and its exact original bytes remain downloadable", async ({ page }) => {
  await validCandidate(page); await rationaleField(page).fill("Earlier local review input retained under this writer.");
  const before = await harness(page), original = await ownCopy(page), corrupt = "{original corrupt experiment source\n";
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: corrupt });
  await page.reload(); await expect(page.getByLabel("Headline", { exact: true })).toBeVisible(); await open(page);
  await expect(button(page, "Retry experiment recovery")).toBeVisible(); await rationaleField(page).fill("Later input must stay available.");
  const replacement = structuredClone(original.copy); replacement.draft.fields = {}; replacement.draft.pending = null; replacement.draft.sequence = 0;
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: JSON.stringify(replacement) });
  await button(page, "Retry experiment recovery").click();
  await expect(recovery(page).getByRole("alert")).toContainText("original experiment recovery bytes changed");
  await expect(rationaleField(page)).toHaveValue("Later input must stay available.");
  await page.getByText("Experiment recovery copies", { exact: true }).click();
  const pending = page.waitForEvent("download"); await button(page, "Download original experiment recovery bytes").click();
  const path = await (await pending).path(); expect(path).not.toBeNull(); expect(await readFile(path!, "utf8")).toBe(corrupt);
  expect(await harness(page)).toEqual(before); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.requests())).toEqual([]);
});
