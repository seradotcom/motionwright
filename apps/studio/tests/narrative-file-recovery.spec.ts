import { readFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { NarrativeFile } from "../src/narrativeDraft";
import type { NarrativeAuthorityHarness } from "./fixtures/narrative-authority";

// Real browser downloads and file selection on the actual Narrative component.
// This is component/file evidence, not SQLite, installed Tauri, or native-render evidence.
type Field = "premise" | "objective";
const label = (field: Field) => field === "premise" ? "Narrative premise" : "Scene objective";
const input = (page: Page, field: Field = "premise") => page.getByLabel(label(field), { exact: true });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const picker = (page: Page, field: Field = "premise") => page.getByLabel("Import " + label(field) + " recovery file", { exact: true });
const candidate = (page: Page, field: Field = "premise") => page.getByRole("region", { name: label(field) + " imported candidate", exact: true });
const editor = (page: Page, field: Field = "premise") => page.getByRole("region", { name: label(field) + " editor", exact: true });
const requests = (page: Page) => page.evaluate(() => (window.__NARRATIVE_AUTHORITY__ as NarrativeAuthorityHarness).requests());

async function totalStorageFailure(page: Page) {
  await page.addInitScript(() => Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() { throw new DOMException("Narrative test: total local storage failure", "SecurityError"); },
  }));
}
async function open(page: Page) {
  // fixture.ts creates timestamped records at module evaluation, including on reload.
  // Fix Date while timers continue so complete Project equality remains meaningful.
  await page.clock.setFixedTime(new Date("2026-10-21T00:00:00.000Z"));
  await page.goto("/tests/fixtures/narrative-authority.html");
  await expect(page.getByRole("heading", { name: "Narrative", exact: true })).toBeVisible();
}
async function download(page: Page, info: TestInfo, field: Field, name: string) {
  const event = page.waitForEvent("download");
  await button(page, "Download " + label(field) + " draft").click();
  const transfer = await event;
  expect(transfer.suggestedFilename()).toMatch(new RegExp("^motionwright-narrative-" + field + "-[0-9]+\\.json$"));
  const path = info.outputPath(name + ".json");
  await transfer.saveAs(path);
  expect(await transfer.failure()).toBeNull();
  const raw = await readFile(path, "utf8"), value = JSON.parse(raw) as NarrativeFile;
  await info.attach(name, { path, contentType: "application/json" });
  return { path, raw, value };
}

for (const field of ["premise", "objective"] as const) {
  test(`a real ${field} download and clean-file import preserve raw input during total browser storage failure`, async ({ page }, info) => {
    await totalStorageFailure(page); await open(page);
    const initial = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
    const original = await input(page, field).inputValue(), authored = "  Portable " + field + "\n\nKeep Unicode: á 🎬 and spaces  \n";
    await input(page, field).fill(authored);
    await expect(editor(page, field).getByRole("alert")).toContainText("total local storage failure");
    await expect(button(page, "Commit " + field)).toBeDisabled();
    const saved = await download(page, info, field, "author-file-before-reload");
    expect(saved.value.schema).toBe("motionwright.narrative-file/1");
    expect(saved.value.draft.raw).toBe(authored); expect(saved.value.draft.base).toBe(original);
    expect(saved.value.draft.baseRevision).toBe(initial.revision);
    expect(saved.value.draft.sequence).toBeGreaterThan(0); expect(saved.value.draft.pending).toBeNull();
    expect(saved.raw).not.toContain('"effect_grant"'); expect(saved.raw).not.toContain('"request_id"');
    expect(await requests(page)).toEqual([]);
    await page.reload();
    await expect(input(page, field)).toHaveValue(original);
    await picker(page, field).setInputFiles(saved.path);
    await expect(candidate(page, field)).toContainText(authored.trim());
    await expect(input(page, field)).toHaveValue(original);
    expect(await requests(page)).toEqual([]);
    await button(page, "Use imported " + label(field) + " copy").click();
    await expect(input(page, field)).toHaveValue(authored);
    await expect(candidate(page, field)).toHaveCount(0);
    await expect(button(page, "Commit " + field)).toBeDisabled();
    const recovered = await download(page, info, field, "author-file-after-import");
    expect(recovered.value.draft.raw).toBe(saved.value.draft.raw);
    expect(recovered.value.draft.base).toBe(saved.value.draft.base);
    expect(recovered.value.draft.sequence).toBe(saved.value.draft.sequence);
    expect(recovered.value.draft.pending).toBeNull();
    expect(await requests(page)).toEqual([]);
    expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(initial);
  });
}

test("an imported candidate cannot replace current author input when its backup fails; both buffers survive remount", async ({ page }, info) => {
  await totalStorageFailure(page); await open(page);
  const initial = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
  await input(page).fill("Candidate retained in an actual author file");
  const saved = await download(page, info, "premise", "candidate-file");
  await page.reload();
  const current = "  New current input that must not be replaced\n";
  await input(page).fill(current);
  await picker(page).setInputFiles(saved.path);
  await expect(candidate(page)).toContainText(saved.value.draft.raw);
  await button(page, "Use imported Narrative premise copy").click();
  await expect(editor(page).getByRole("alert").filter({ hasText: "Both inputs remain available" })).toBeVisible();
  await expect(input(page)).toHaveValue(current);
  await expect(candidate(page)).toContainText(saved.value.draft.raw);
  await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.unmount());
  await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.mount());
  await expect(input(page)).toHaveValue(current);
  await expect(candidate(page)).toContainText(saved.value.draft.raw);
  const kept = await download(page, info, "premise", "current-author-file");
  expect(kept.value.draft.raw).toBe(current);
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(initial);
});

test("explicit file use retains the previous author copy before replacement and survives reload without Project writes", async ({ page }, info) => {
  await open(page);
  const initial = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
  await input(page).fill("The selected author-file decision");
  const saved = await download(page, info, "premise", "selected-author-file");
  const previous = "  Previous live author decision to keep separately\n";
  await input(page).fill(previous);
  await picker(page).setInputFiles(saved.path);
  await expect(candidate(page)).toContainText(saved.value.draft.raw);
  await expect(input(page)).toHaveValue(previous);
  await button(page, "Use imported Narrative premise copy").click();
  await expect(input(page)).toHaveValue(saved.value.draft.raw);
  const retained = await page.evaluate(() => Object.keys(localStorage)
    .filter(key => key.startsWith("motionwright.narrative-recovery.v1:"))
    .map(key => JSON.parse(localStorage.getItem(key)!)));
  expect(retained.some(copy => copy.draft.raw === previous)).toBe(true);
  expect(retained.some(copy => copy.draft.raw === saved.value.draft.raw)).toBe(true);
  expect(await requests(page)).toEqual([]);
  await page.reload();
  await expect(input(page)).toHaveValue(saved.value.draft.raw);
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(initial);
});

for (const fault of ["corrupt JSON", "foreign project", "unknown schema", "injected authority", "oversized bytes"] as const) {
  test(`a file with ${fault} cannot replace current input or an earlier valid candidate`, async ({ page }, info) => {
    await open(page);
    await input(page).fill("A legitimate candidate selected explicitly");
    const saved = await download(page, info, "premise", "legitimate-author-file");
    const current = "Current author input remains active";
    await input(page).fill(current); await picker(page).setInputFiles(saved.path);
    await expect(candidate(page)).toContainText(saved.value.draft.raw);
    const before = await page.evaluate(() => Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])));
    const forged = JSON.parse(saved.raw);
    if (fault === "foreign project") {
      const scope = JSON.parse(forged.scope); scope[0] += "-foreign";
      forged.scope = JSON.stringify(scope); forged.draft.scope = forged.scope;
    } else if (fault === "unknown schema") forged.schema = "motionwright.narrative-file/999";
    else if (fault === "injected authority") forged.effect_grant = "never-an-authority";
    const buffer = fault === "oversized bytes" ? Buffer.alloc(8 * 1024 * 1024 + 1, " ")
      : Buffer.from(fault === "corrupt JSON" ? "{not valid JSON" : JSON.stringify(forged));
    await picker(page).setInputFiles({ name: "untrusted-author-file.json", mimeType: "application/json", buffer });
    await expect(editor(page).getByRole("alert")).toContainText("Your active input and any earlier valid candidate were kept");
    await expect(input(page)).toHaveValue(current);
    await expect(candidate(page)).toContainText(saved.value.draft.raw);
    expect(await page.evaluate(() => Object.fromEntries(Object.keys(localStorage).map(key => [key, localStorage.getItem(key)])))).toEqual(before);
    expect(await requests(page)).toEqual([]);
  });
}

test("an actual exported uncertain attempt imports as UNKNOWN and cannot acknowledge or resend its earlier operation", async ({ page, context }, info) => {
  await open(page);
  await input(page).fill("Submitted text whose original result is unknown");
  await button(page, "Commit premise").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  const original = (await requests(page))[0];
  await input(page).fill("  Later author text retained with the UNKNOWN attempt\n");
  await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.reject(id, "Original result unavailable"), original.id);
  await expect(page.getByRole("region", { name: "Unconfirmed premise submission", exact: true })).toContainText("Original result unavailable");
  const saved = await download(page, info, "premise", "uncertain-author-file");
  expect(saved.value.draft.pending?.outcome).toBe("UNKNOWN");
  expect(saved.value.draft.pending?.raw).toBe("Submitted text whose original result is unknown");
  const separate = await context.newPage();
  try {
    await open(separate);
    const initial = await separate.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
    await picker(separate).setInputFiles(saved.path);
    await expect(candidate(separate)).toContainText("Importing cannot acknowledge or resend it");
    await button(separate, "Use imported Narrative premise copy").click();
    await expect(input(separate)).toHaveValue(saved.value.draft.raw);
    await expect(page.getByRole("region", { name: "Unconfirmed premise submission", exact: true })).toBeVisible();
    await expect(separate.getByRole("region", { name: "Unconfirmed premise submission", exact: true })).toContainText("Original result unavailable");
    await expect(button(separate, "Commit premise")).toBeDisabled();
    const imported = await download(separate, info, "premise", "imported-uncertain-file");
    expect(imported.value.draft.pending).toEqual(saved.value.draft.pending);
    expect(imported.value.draft.sequence).toBe(saved.value.draft.sequence);
    expect(await requests(separate)).toEqual([]);
    expect(await separate.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(initial);
    await separate.reload();
    await expect(button(separate, "Commit premise")).toBeDisabled();
    await expect(separate.getByRole("region", { name: "Unconfirmed premise submission", exact: true })).toContainText("Original result unavailable");
    expect(await requests(separate)).toEqual([]);
  } finally { await separate.close(); }
});
