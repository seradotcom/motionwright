import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import type { EditorialFixtureHarness } from "./fixtures/narrative-editing";

// Actual component + actual ProjectMutationGate. All fixture props/replies and
// history are labelled synthetic; these tests make no service/SQLite claim.
type Request = ReturnType<EditorialFixtureHarness["requests"]>[number];
const script = (page: Page) => page.getByLabel("Editorial script", { exact: true });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Unconfirmed editorial operation", exact: true });
const requests = (page: Page): Promise<Request[]> => page.evaluate(() => window.__EDITORIAL_FIXTURE__.requests());
async function ownCopy(page: Page) {
  return page.evaluate(() => {
    const project = window.__EDITORIAL_FIXTURE__.project();
    const scope = JSON.stringify([project.id, project.generation, project.active_branch]);
    const pointerKey = "motionwright.editorial-writer.v1:" + encodeURIComponent(scope), writer = sessionStorage.getItem(pointerKey);
    if (!writer) throw new Error("The editorial writer was not retained.");
    const key = "motionwright.editorial-copy.v1:" + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (!raw) throw new Error("The editorial author copy was not retained.");
    return { scope, pointerKey, writer, key, raw, copy: JSON.parse(raw) };
  });
}
async function settle(page: Page, count: number) {
  await expect.poll(async () => (await requests(page)).length).toBe(count);
  await page.evaluate(id => window.__EDITORIAL_FIXTURE__.resolve(id), count);
  await expect(unknown(page)).toHaveCount(0);
}
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/narrative-editing.html");
  await expect(page.getByRole("note")).toContainText("explicitly synthetic");
  await expect(page.getByRole("heading", { name: "Editorial beats, script and timing", exact: true })).toBeVisible();
});

test("editorial content is available without a selected scene and remount preserves raw subbuffers", async ({ page }) => {
  const raw = "  Author wording\nwith deliberate whitespace  \n";
  await script(page).fill(raw);
  await page.getByLabel("Preferred editorial duration (seconds, optional)", { exact: true }).fill("1e");
  await page.getByLabel("Duration proposal reason", { exact: true }).fill("Unsubmitted timing explanation");
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.unmount());
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.mount());
  await expect(script(page)).toHaveValue(raw);
  await expect(page.getByLabel("Preferred editorial duration (seconds, optional)", { exact: true })).toHaveValue("1e");
  await expect(page.getByLabel("Duration proposal reason", { exact: true })).toHaveValue("Unsubmitted timing explanation");
  expect(await requests(page)).toHaveLength(0);
});

test("reload restores exact per-beat input and partial time without submitting a request", async ({ page }) => {
  await page.getByLabel("Editorial beat label", { exact: true }).fill("Unsaved Question");
  await page.getByLabel("Minimum editorial hold (seconds, optional)", { exact: true }).fill("1e");
  await button(page, "Evidence").click();
  await page.getByLabel("Editorial beat label", { exact: true }).fill("Unsaved Evidence");
  await page.reload();
  await expect(page.getByLabel("Editorial beat label", { exact: true })).toHaveValue("Unsaved Question");
  await expect(page.getByLabel("Minimum editorial hold (seconds, optional)", { exact: true })).toHaveValue("1e");
  await button(page, "Evidence").click();
  await expect(page.getByLabel("Editorial beat label", { exact: true })).toHaveValue("Unsaved Evidence");
  expect(await requests(page)).toHaveLength(0);
});

test("partial exact time remains raw and cannot reach the original commit callback", async ({ page }) => {
  await page.getByLabel("Preferred editorial duration (seconds, optional)", { exact: true }).fill("1e");
  await button(page, "Save editorial beat").click();
  await expect(page.getByRole("alert")).toContainText(/time|seconds|decimal|rational|complete/i);
  await expect(page.getByLabel("Preferred editorial duration (seconds, optional)", { exact: true })).toHaveValue("1e");
  expect(await requests(page)).toHaveLength(0);
});

test("reordering changes only editorial ID order and retains an explicit impact receipt", async ({ page }) => {
  const initial = await page.evaluate(() => window.__EDITORIAL_FIXTURE__.project());
  await button(page, "Move editorial beat Question later").click();
  expect(await requests(page)).toHaveLength(0);
  await button(page, "Save editorial beat order").click();
  const sent = await requests(page); expect(sent).toHaveLength(1);
  expect(sent[0].change).toMatchObject({ type: "edit_narrative", edit: { kind: "reorder",
    expected_order: initial.narrative.beats.map(beat => beat.id),
    ordered_ids: [initial.narrative.beats[1].id, initial.narrative.beats[0].id, ...initial.narrative.beats.slice(2).map(beat => beat.id)] } });
  await settle(page, 1);
  const saved = await page.evaluate(() => window.__EDITORIAL_FIXTURE__.project());
  expect(saved.scenes).toEqual(initial.scenes); expect(saved.audio).toEqual(initial.audio);
  await page.getByText("Saved reorder impact and exact affected IDs", { exact: true }).click();
  await expect(page.getByText("Saved reorder impact and exact affected IDs", { exact: true }).locator("..")).toContainText("declared_editorial_relations_only_native_dependencies_unknown");
});

test("the original ACK preserves late typing that returns to the old empty base", async ({ page }) => {
  await script(page).fill("Submitted wording"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await script(page).fill("Later wording"); await script(page).fill("");
  await settle(page, 1);
  await expect(script(page)).toHaveValue(""); await expect(button(page, "Save editorial script")).toBeEnabled();
  const copy = await ownCopy(page); expect(copy.copy.draft.fields["script.text"].raw).toBe("");
  expect(copy.copy.draft.fields["script.text"].base).toBe("Submitted wording");
});

test("an original script ACK cannot consume the independent timing and new-beat buffers", async ({ page }) => {
  await script(page).fill("Submitted wording");
  await page.getByLabel("New editorial beat label", { exact: true }).fill("An independent new beat");
  await page.getByLabel("Proposed scene duration (exact seconds)", { exact: true }).fill("1001/30000");
  await button(page, "Save editorial script").click(); await settle(page, 1);
  await expect(page.getByLabel("New editorial beat label", { exact: true })).toHaveValue("An independent new beat");
  await expect(page.getByLabel("Proposed scene duration (exact seconds)", { exact: true })).toHaveValue("1001/30000");
});

test("matching props and history remain UNKNOWN until a separate explicit new intent", async ({ page }) => {
  await script(page).fill("Observed saved wording"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.observeOriginal(1));
  await expect(unknown(page)).toBeVisible();
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.reject(1, "Synthetic delivery loss after observing the saved props"));
  await script(page).fill("A separately requested later wording");
  await expect(button(page, "Save editorial script")).toBeDisabled();
  await button(page, "Inspect current editorial project and history").click();
  await expect(unknown(page)).toContainText("Matching command observations:");
  await button(page, "Keep typed input for a separate explicit intent").click();
  expect((await ownCopy(page)).copy.draft.pending.outcome).toBe("UNKNOWN");
  expect(await requests(page)).toHaveLength(1);
  await button(page, "Save editorial script").click(); await settle(page, 2);
  const sent = await requests(page);
  expect(sent[1].source.revision).toBe(sent[0].source.revision + 1);
  expect(sent[1].change).toMatchObject({ type: "edit_narrative", edit: { kind: "set_script", text: "A separately requested later wording", expected_version: 1 } });
  expect(await page.evaluate(() => Object.keys(localStorage).some(key => key.startsWith("motionwright.editorial-copy.v1:")
    && JSON.parse(localStorage.getItem(key)!).draft.pending?.outcome === "UNKNOWN"))).toBe(true);
});

for (const field of ["id", "generation", "active_branch"] as const) {
  test(`a late original response cannot publish into a different ${field} or consume its buffer`, async ({ page }) => {
    const before = await page.evaluate(() => window.__EDITORIAL_FIXTURE__.project());
    await script(page).fill("Original scoped author input"); await button(page, "Save editorial script").click();
    await expect.poll(async () => (await requests(page)).length).toBe(1);
    await page.evaluate(({ field }) => window.__EDITORIAL_FIXTURE__.setScope(field, "018f0000-0000-7000-8000-000000008999"), { field });
    await expect(script(page)).toHaveValue(""); await script(page).fill("Foreign-scope author buffer");
    await page.evaluate(() => window.__EDITORIAL_FIXTURE__.resolve(1));
    await expect(script(page)).toHaveValue("Foreign-scope author buffer");
    await page.evaluate(({ field, value }) => window.__EDITORIAL_FIXTURE__.setScope(field, value), { field, value: before[field] });
    await expect(script(page)).toHaveValue("Original scoped author input"); await expect(unknown(page)).toBeVisible();
    await expect(button(page, "Save editorial script")).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
  });
}

for (const target of ["copy", "pointer"] as const) {
  test(`silent ${target} write failure blocks submission and keeps the raw author input`, async ({ page }) => {
    await page.evaluate(target => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) {
        if (target === "copy" && this === localStorage && key.startsWith("motionwright.editorial-copy.v1:")) return;
        if (target === "pointer" && this === sessionStorage && key.startsWith("motionwright.editorial-writer.v1:")) return;
        return original.call(this, key, value);
      };
      window.__EDITORIAL_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = original; };
    }, target);
    await script(page).fill("Keep despite silent failure");
    await expect(button(page, "Save editorial script")).toBeDisabled();
    await expect(script(page)).toHaveValue("Keep despite silent failure"); expect(await requests(page)).toHaveLength(0);
    await page.evaluate(() => window.__EDITORIAL_RESTORE_STORAGE__!());
    await button(page, "Retry editorial recovery").click();
    await expect(button(page, "Save editorial script")).toBeEnabled(); expect(await requests(page)).toHaveLength(0);
  });
}

test("reload and Retry reread a transiently unreadable original UNKNOWN before allowing any new intent", async ({ page }) => {
  await script(page).fill("Original unknown intent"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.reject(1, "Synthetic unknown response"));
  const copy = await ownCopy(page);
  await page.addInitScript(key => {
    const get = Storage.prototype.getItem; let failed = false;
    Storage.prototype.getItem = function (name) {
      if (this === localStorage && name === key && !failed) { failed = true; throw new Error("Temporary original-copy read failure"); }
      return get.call(this, name);
    };
  }, copy.key); await page.reload();
  await script(page).fill("Typed after recovery read failed"); await expect(button(page, "Save editorial script")).toBeDisabled();
  await button(page, "Retry editorial recovery").click();
  await expect(unknown(page)).toBeVisible(); await expect(script(page)).toHaveValue("Typed after recovery read failed");
  await expect(button(page, "Save editorial script")).toBeDisabled(); expect(await requests(page)).toHaveLength(0);
  expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
});

test("UNKNOWN survives reload and never replays the stored command", async ({ page }) => {
  await script(page).fill("Unknown before reload"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.reject(1, "Synthetic uncertain transport"));
  await page.reload(); await expect(unknown(page)).toBeVisible();
  await expect(script(page)).toHaveValue("Unknown before reload"); expect(await requests(page)).toHaveLength(0);
  await expect(button(page, "Save editorial script")).toBeDisabled();
});

test("a deferred author-file read cannot replace input typed after file selection", async ({ page }) => {
  await script(page).fill("Input before selecting file");
  const prior = await ownCopy(page), file = JSON.stringify({ schema: "motionwright.editorial-author-file/1", draft: prior.copy.draft });
  await page.getByText("Editorial recovery and portable author input", { exact: true }).click();
  await page.evaluate(() => {
    File.prototype.text = function () { return new Promise<string>(resolve => { window.__EDITORIAL_RELEASE_FILE__ = resolve; }); };
  });
  await page.getByLabel("Import editorial author input", { exact: true }).setInputFiles({ name: "earlier-author-input.json", mimeType: "application/json", buffer: Buffer.from("fixture-placeholder") });
  await expect.poll(() => page.evaluate(() => !!window.__EDITORIAL_RELEASE_FILE__)).toBe(true);
  await script(page).fill("Later input must survive");
  await page.evaluate(raw => window.__EDITORIAL_RELEASE_FILE__!(raw), file);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(script(page)).toHaveValue("Later input must survive");
  await expect(button(page, "Preserve current input and import candidate")).toHaveCount(0);
  await expect(page.getByLabel("Import editorial author input", { exact: true })).toBeEnabled(); expect(await requests(page)).toHaveLength(0);
});

test("first pointer read failure recovers the original UNKNOWN and a later explicit blank without replay", async ({ page }) => {
  await script(page).fill("Earlier unknown editorial script"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.reject(1, "Synthetic uncertain response before reload"));
  const copy = await ownCopy(page);
  await page.addInitScript(pointerKey => {
    const get = Storage.prototype.getItem; let failed = false;
    Storage.prototype.getItem = function (key) {
      if (this === sessionStorage && key === pointerKey && !failed) { failed = true; throw new Error("Initial editorial pointer read unavailable"); }
      return get.call(this, key);
    };
  }, copy.pointerKey);
  await page.reload(); await script(page).fill("Later wording while blocked"); await script(page).fill("");
  await expect(button(page, "Save editorial script")).toBeDisabled();
  await button(page, "Retry editorial recovery").click();
  await expect(unknown(page)).toBeVisible(); await expect(script(page)).toHaveValue("");
  await expect(button(page, "Save editorial script")).toBeDisabled(); expect(await requests(page)).toHaveLength(0);
  expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
  expect((await ownCopy(page)).copy.draft.pending.id).toBe(copy.copy.draft.pending.id);
});

test("replacing observed corrupt bytes under the same writer stays blocked and the original remains downloadable", async ({ page }) => {
  await script(page).fill("Earlier unknown editorial script"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.reject(1, "Synthetic uncertain response before corruption"));
  const copy = await ownCopy(page), corrupt = "{unreadable original editorial UNKNOWN";
  await page.evaluate(({ key, corrupt }) => localStorage.setItem(key, corrupt), { key: copy.key, corrupt });
  await page.reload(); await script(page).fill("Later author input must remain");
  const replacement = structuredClone(copy.copy); replacement.draft.fields = {}; replacement.draft.pending = null; replacement.draft.sequence = 0;
  await page.evaluate(({ key, replacement }) => localStorage.setItem(key, JSON.stringify(replacement)), { key: copy.key, replacement });
  await button(page, "Retry editorial recovery").click();
  await expect(page.getByRole("alert").filter({ hasText: "original editorial recovery bytes changed" }).first()).toBeVisible();
  await expect(button(page, "Save editorial script")).toBeDisabled(); await expect(script(page)).toHaveValue("Later author input must remain");
  expect(await requests(page)).toHaveLength(0);
  await page.getByText("Editorial recovery and portable author input", { exact: true }).click();
  const downloaded = page.waitForEvent("download"); await button(page, "Download editorial copy " + copy.key).click();
  const path = await (await downloaded).path(); expect(path).not.toBeNull();
  const preserved = JSON.parse(await readFile(path!, "utf8")); expect(preserved.storage_key).toBe(copy.key); expect(preserved.raw).toBe(corrupt);
});

test("a source seen before copy enumeration fails cannot later be replaced under its writer", async ({ page }) => {
  await script(page).fill("Unknown source before partial listing"); await button(page, "Save editorial script").click();
  await expect.poll(async () => (await requests(page)).length).toBe(1);
  await page.evaluate(() => window.__EDITORIAL_FIXTURE__.reject(1, "Synthetic uncertain response"));
  const copy = await ownCopy(page);
  await page.addInitScript(sourceKey => {
    const get = Storage.prototype.getItem, key = Storage.prototype.key; let failed = false;
    Storage.prototype.getItem = function (name) {
      if (this === localStorage && name === sourceKey && !failed) { failed = true; throw new Error("Initial copy read unavailable"); }
      return get.call(this, name);
    };
    Storage.prototype.key = function (index) {
      if (this === localStorage) { if (index === 0) return sourceKey; throw new Error("Later copy index unavailable"); }
      return key.call(this, index);
    };
    window.__EDITORIAL_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = get; Storage.prototype.key = key; };
  }, copy.key);
  await page.reload(); await page.getByText("Editorial recovery and portable author input", { exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Later copy index unavailable" })).toBeVisible();
  await script(page).fill("Later author input");
  await page.evaluate(({ key, raw }) => {
    window.__EDITORIAL_RESTORE_STORAGE__!();
    const replacement = JSON.parse(raw); replacement.draft.pending = null; replacement.draft.fields = {}; replacement.draft.sequence = 0;
    localStorage.setItem(key, JSON.stringify(replacement));
  }, copy);
  await button(page, "Retry editorial recovery").click();
  await expect(page.getByRole("alert").filter({ hasText: "original editorial recovery bytes changed" }).first()).toBeVisible();
  await expect(button(page, "Save editorial script")).toBeDisabled(); await expect(script(page)).toHaveValue("Later author input");
  expect(await requests(page)).toHaveLength(0);
  const downloaded = page.waitForEvent("download"); await button(page, "Download editorial copy " + copy.writer).click();
  const path = await (await downloaded).path(); expect(path).not.toBeNull();
  expect(JSON.parse(await readFile(path!, "utf8")).raw).toBe(copy.raw);
});
