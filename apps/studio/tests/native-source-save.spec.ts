import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

// The real App runs its explicitly labelled browser-demo project. These tests
// prove authored-input custody in Chromium only: no Tauri bridge, file import,
// service receipt, SQLite mutation, renderer or native-save result is mocked.
const COPY_PREFIX = "motionwright.native-source-draft.v1:";
const POINTER_PREFIX = "motionwright.native-source-writer.v1:";
const labels = {
  path: "Source file path",
  name: "Source file name",
  mediaType: "Source media type",
  label: "Capsule label",
  parameters: "Editable parameters (JSON array)",
  editorHint: "Original editor",
} as const;
type Fields = Record<keyof typeof labels, string>;
interface InputDownload {
  schema: "motionwright.native-source-input-download/1";
  draft: {
    schema: "motionwright.native-source-draft/1";
    scope: string; sceneId: string; target: string; capsuleId: string;
    baseRevision: number; baseCapsule: unknown; fields: Fields; sequence: number; dirty: boolean;
    previousCopies: { writerId: string; byteLength: number; sha256: string }[];
  };
  recovery_problem: string | null;
  backup_error: string | null;
}
interface OriginalsDownload {
  schema: "motionwright.native-source-original-copies/1";
  copies: { key: string; raw: string | null }[];
}
const rawInput: Fields = {
  path: "  E:\\Creative\\原稿\\source.tsx  ",
  name: "  Story source 🎥.tsx  ",
  mediaType: "text/ incomplete",
  label: "  Capsule A — first cut  ",
  parameters: '[\n  "headline",\n  "unfinished',
  editorHint: "  Motion Canvas original  ",
};
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const input = (page: Page, field: keyof Fields) => page.locator(".native-source-editor")
  .getByRole("textbox", { name: labels[field], exact: true });

async function openSources(page: Page) {
  await page.getByRole("navigation", { name: "Workspaces", exact: true })
    .getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("navigation", { name: "Production tools", exact: true })
    .getByRole("button", { name: "Native sources", exact: true }).click();
  await expect(input(page, "path")).toBeVisible();
  const details = page.locator(".native-source-editor details").filter({ hasText: "Declared editable parameters" });
  if (await details.getAttribute("open") === null) await details.locator("summary").click();
  await expect(input(page, "parameters")).toBeVisible();
}

async function browserOnly(page: Page) {
  await expect(page.locator(".sdk-chip")).toContainText("browser demo");
  expect(await page.evaluate(() => "__TAURI_INTERNALS__" in window)).toBe(false);
  await expect(page.getByText("Local source saving is available in Motionwright desktop. Your input can still be kept and downloaded here.", { exact: true })).toBeVisible();
  await expect(button(page, "Save source and capsule")).toBeDisabled();
  await expect(button(page, "Read latest saved project")).toBeDisabled();
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.file-import.intent.v1:")))).toEqual([]);
}

async function fillFields(page: Page, values: Fields) {
  for (const field of Object.keys(labels) as (keyof Fields)[]) await input(page, field).fill(values[field]);
}

async function expectFields(page: Page, values: Fields) {
  for (const field of Object.keys(labels) as (keyof Fields)[]) await expect(input(page, field)).toHaveValue(values[field]);
}

async function download<T>(page: Page, name: string): Promise<T> {
  const pending = page.waitForEvent("download");
  await button(page, name).click();
  const result = await pending;
  const path = await result.path();
  expect(path).not.toBeNull();
  return JSON.parse(await readFile(path!, "utf8")) as T;
}

async function authoredInput(page: Page) {
  const value = await download<InputDownload>(page, "Download source input");
  expect(value.schema).toBe("motionwright.native-source-input-download/1");
  expect(value.draft.schema).toBe("motionwright.native-source-draft/1");
  return value;
}

async function demoProject(page: Page) {
  return page.evaluate(async () => {
    const modulePath = "/src/api.ts";
    const api = await import(/* @vite-ignore */ modulePath);
    const project = (await api.bootstrap()).project;
    return {
      id: project.id as string, generation: project.generation as string,
      branch: project.active_branch as string, revision: project.revision as number,
      scenes: project.scenes.map((scene: { id: string; name: string }) => ({ id: scene.id, name: scene.name })),
      assets: project.assets.map((asset: { id: string; name: string; content_sha256?: string }) => ({ id: asset.id, name: asset.name, digest: asset.content_sha256 })),
      capsules: project.production_design?.capsules ?? [],
    };
  });
}

test("browser demo: all six raw source fields and stable capsule identity survive navigation and reload without a native save", async ({ page }) => {
  await page.goto("/");
  const before = await demoProject(page);
  await openSources(page);
  await fillFields(page, rawInput);
  const original = await authoredInput(page);
  expect(original.draft.fields).toEqual(rawInput);
  expect(original.draft.dirty).toBe(true);
  expect(original.backup_error).toBeNull();
  await browserOnly(page);

  await page.getByRole("navigation", { name: "Production tools", exact: true })
    .getByRole("button", { name: "Production plan", exact: true }).click();
  await openSources(page);
  await expectFields(page, rawInput);
  await page.getByRole("navigation", { name: "Workspaces", exact: true })
    .getByRole("button", { name: "Timeline", exact: true }).click();
  await openSources(page);
  await expectFields(page, rawInput);

  await page.reload();
  await openSources(page);
  await expectFields(page, rawInput);
  const restored = await authoredInput(page);
  expect(restored.draft.capsuleId).toBe(original.draft.capsuleId);
  expect(restored.draft.scope).toBe(original.draft.scope);
  expect(restored.draft.baseRevision).toBe(before.revision);
  expect(restored.backup_error).toBeNull();
  expect(await demoProject(page)).toEqual(before);
  await browserOnly(page);
});

test("browser demo: the real scene selector keeps independent raw source drafts and restores both capsule identities", async ({ page }) => {
  await page.goto("/");
  const before = await demoProject(page);
  expect(before.scenes.length).toBeGreaterThan(1);
  await openSources(page);
  const scene = page.getByLabel("Production working scene", { exact: true });
  await scene.selectOption(before.scenes[0].id);
  await fillFields(page, rawInput);
  const first = await authoredInput(page);

  await scene.selectOption(before.scenes[1].id);
  await expect(input(page, "label")).toHaveValue("");
  const details = page.locator(".native-source-editor details");
  if (await details.getAttribute("open") === null) await details.locator("summary").click();
  await expect(input(page, "parameters")).toHaveValue("[]");
  const secondFields = { ...rawInput, name: "Scene B source", label: "  Independent second scene  ", parameters: '["unfinished B' };
  await fillFields(page, secondFields);
  const second = await authoredInput(page);
  expect(second.draft.capsuleId).not.toBe(first.draft.capsuleId);
  expect(JSON.parse(first.draft.scope)[3]).toBe(before.scenes[0].id);
  expect(JSON.parse(second.draft.scope)[3]).toBe(before.scenes[1].id);

  await scene.selectOption(before.scenes[0].id);
  await openSources(page);
  await expectFields(page, rawInput);
  expect((await authoredInput(page)).draft.capsuleId).toBe(first.draft.capsuleId);
  await page.reload();
  await openSources(page);
  await page.getByLabel("Production working scene", { exact: true }).selectOption(before.scenes[1].id);
  await openSources(page);
  await expectFields(page, secondFields);
  expect((await authoredInput(page)).draft.capsuleId).toBe(second.draft.capsuleId);
  expect(await demoProject(page)).toEqual(before);
  await browserOnly(page);
});

for (const foreign of [
  { name: "project", index: 0 },
  { name: "generation", index: 1 },
  { name: "branch", index: 2 },
] as const) {
  test(`browser demo: a pre-existing ${foreign.name} scope cannot supply this project's source draft`, async ({ page }) => {
    await page.goto("/");
    const before = await demoProject(page);
    const scope = [before.id, before.generation, before.branch, before.scenes[0].id, "new"];
    scope[foreign.index] = "20000000-0000-4000-8000-000000000001";
    const foreignScope = JSON.stringify(scope);
    const writer = "20000000-0000-4000-8000-000000000002";
    const capsuleId = "20000000-0000-4000-8000-000000000003";
    const pointerKey = POINTER_PREFIX + encodeURIComponent(foreignScope);
    const copyKey = COPY_PREFIX + encodeURIComponent(foreignScope) + ":" + writer;
    const raw = JSON.stringify({
      schema: "motionwright.native-source-copy/1", writerId: writer,
      draft: {
        schema: "motionwright.native-source-draft/1", scope: foreignScope,
        sceneId: before.scenes[0].id, target: "new", capsuleId,
        baseRevision: before.revision, baseCapsule: null, fields: rawInput, sequence: 6, dirty: true, previousCopies: [],
      },
    });
    // Storage fixture only. No project identity or native command is replaced.
    await page.evaluate(({ pointerKey, writer, copyKey, raw }) => {
      localStorage.setItem(copyKey, raw);
      sessionStorage.setItem(pointerKey, writer);
    }, { pointerKey, writer, copyKey, raw });

    await openSources(page);
    await expect(input(page, "path")).toHaveValue("");
    await expect(input(page, "label")).toHaveValue("");
    await expect(input(page, "parameters")).toHaveValue("[]");
    const localFields = { ...rawInput, label: "Current project input only" };
    await fillFields(page, localFields);
    const local = await authoredInput(page);
    expect(JSON.parse(local.draft.scope)).toEqual([before.id, before.generation, before.branch, before.scenes[0].id, "new"]);
    expect(local.draft.capsuleId).not.toBe(capsuleId);

    await page.reload();
    await openSources(page);
    await expectFields(page, localFields);
    expect((await authoredInput(page)).draft.capsuleId).toBe(local.draft.capsuleId);
    expect(await page.evaluate(({ pointerKey, copyKey }) => ({ pointer: sessionStorage.getItem(pointerKey), raw: localStorage.getItem(copyKey) }), { pointerKey, copyKey }))
      .toEqual({ pointer: writer, raw });
    expect(await demoProject(page)).toEqual(before);
    await browserOnly(page);
  });
}

test("browser demo: corrupt source copies remain downloadable and Retry never adopts a later replacement", async ({ page }) => {
  await page.goto("/");
  const before = await demoProject(page);
  await openSources(page);
  await fillFields(page, rawInput);
  const original = await authoredInput(page);
  const pointerKey = POINTER_PREFIX + encodeURIComponent(original.draft.scope);
  const saved = await page.evaluate(({ pointerKey, prefix, scope }) => {
    const writer = sessionStorage.getItem(pointerKey)!;
    const key = prefix + encodeURIComponent(scope) + ":" + writer;
    return { writer, key, raw: localStorage.getItem(key)! };
  }, { pointerKey, prefix: COPY_PREFIX, scope: original.draft.scope });
  const corrupt = '{"unfinished source copy":\n"raw Unicode: \ud800';
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: saved.key, raw: corrupt });
  await page.reload();
  await openSources(page);
  await expect(page.getByRole("alert").filter({ hasText: "Source input recovery needs attention" })).toBeVisible();
  const later = { ...rawInput, label: "Later local input after corrupt recovery", parameters: '["later unfinished' };
  await fillFields(page, later);
  await button(page, "Read source input again").click();
  await expectFields(page, later);
  expect(await page.evaluate(key => localStorage.getItem(key), saved.key)).toBe(corrupt);
  expect(await page.evaluate(key => sessionStorage.getItem(key), pointerKey)).toBe(saved.writer);
  const originals = await download<OriginalsDownload>(page, "Download original source copies");
  expect(originals.schema).toBe("motionwright.native-source-original-copies/1");
  expect(originals.copies).toContainEqual({ key: saved.key, raw: corrupt });
  expect((await authoredInput(page)).draft.fields).toEqual(later);

  // A valid replacement under the old key is still not the first observed raw
  // copy. Explicit Retry must preserve both that original and current typing.
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), saved);
  await button(page, "Read source input again").click();
  await expectFields(page, later);
  await expect(page.getByRole("alert").filter({ hasText: "Source input recovery needs attention" })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), saved.key)).toBe(saved.raw);
  expect(await page.evaluate(key => sessionStorage.getItem(key), pointerKey)).toBe(saved.writer);
  const preserved = await download<OriginalsDownload>(page, "Download original source copies");
  expect(preserved.copies).toContainEqual({ key: saved.key, raw: corrupt });
  expect((await authoredInput(page)).draft.fields).toEqual(later);
  expect(await demoProject(page)).toEqual(before);
  await browserOnly(page);
});
