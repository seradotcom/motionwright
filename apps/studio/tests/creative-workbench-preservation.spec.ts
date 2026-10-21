import { expect, test, type Page } from "@playwright/test";
import type { CreativeFixtureRequest } from "./fixtures/creative-workbench-authority";

// Actual components, realizers, browser applyChange and ProjectMutationGate.
// Reply/File scheduling and source metadata are a labelled component fixture,
// never SQLite, installed-app, source-ingest or native-render evidence.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const kinds = ["stage", "procedural", "measured"] as const;
type Kind = typeof kinds[number];
const labels = { stage: "ProductStage", procedural: "Procedural", measured: "Measured data" };
const inputs = { stage: "ProductStage heading", procedural: "Procedural fill", measured: "Measured dataset label" };
const rawValues = { stage: "  My own stage  ", procedural: "#1e", measured: "  My measured source  " };
const saves = { stage: "Add ProductStage to scene", procedural: "Add procedural field", measured: "Register measured source" };
const sourceA = JSON.stringify([{ key: "latency", label: "Observed latency", milli: 12500, unit: "milliseconds" }]);
const assetA = "00000000-0000-4000-8000-000000000091", assetB = "00000000-0000-4000-8000-000000000092";
const tableA = "00000000-0000-4000-8000-000000000093", tableB = "00000000-0000-4000-8000-000000000094";
const requests = (page: Page) => page.evaluate(() => window.__CREATIVE_AUTHORITY__.requests());
async function open(page: Page, kind: Kind) {
  await button(page, labels[kind]).click(); await expect(page.getByLabel(inputs[kind], { exact: true })).toBeVisible();
}
async function valid(page: Page, kind: Kind) {
  if (kind === "stage") { await page.getByLabel("ProductStage rights attestation").fill("Original interface authored for this controlled fixture."); await page.getByLabel(inputs.stage).fill("Clear workflow"); }
  if (kind === "procedural") await page.getByLabel("Procedural fill").fill("#aabbcc");
  if (kind === "measured") {
    await page.getByLabel("Measured dataset label").fill("Actual fixture source rows");
    await page.getByLabel("Measured source asset").selectOption(assetA);
    await page.getByLabel("Measured source file verification").setInputFiles({ name: "source.json", mimeType: "application/json", buffer: Buffer.from(sourceA) });
  }
  await expect(button(page, saves[kind])).toBeEnabled();
}
async function submit(page: Page, name: string): Promise<CreativeFixtureRequest> {
  const count = (await requests(page)).length; await button(page, name).click();
  await expect.poll(async () => (await requests(page)).length).toBe(count + 1); return (await requests(page))[count];
}
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/creative-workbench-authority.html");
  await expect(page.getByLabel("Headline", { exact: true })).toBeVisible();
});

for (const kind of kinds) {
  test(`${kind} raw author input survives navigation, scene return, remount and reload without Project writes`, async ({ page }) => {
    await open(page, kind); const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
    const field = page.getByLabel(inputs[kind], { exact: true }); await field.fill(rawValues[kind]);
    await button(page, "Production plan").click(); await open(page, kind); await expect(field).toHaveValue(rawValues[kind]);
    await page.evaluate(id => window.__CREATIVE_AUTHORITY__.selectScene(id), initial.scenes[1].id);
    await expect(field).not.toHaveValue(rawValues[kind]); await field.fill(kind === "procedural" ? "#bbccdd" : "Scene B input");
    await page.evaluate(id => window.__CREATIVE_AUTHORITY__.selectScene(id), initial.scenes[0].id); await expect(field).toHaveValue(rawValues[kind]);
    await page.evaluate(() => window.__CREATIVE_AUTHORITY__.unmount()); await page.evaluate(() => window.__CREATIVE_AUTHORITY__.mount());
    await open(page, kind); await expect(field).toHaveValue(rawValues[kind]); await page.reload(); await open(page, kind);
    await expect(field).toHaveValue(rawValues[kind]); expect(await requests(page)).toEqual([]);
    expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
  });
  for (const identity of ["id", "generation", "active_branch"] as const) {
    test(`${kind} author input is isolated by ${identity} and restores on return`, async ({ page }) => {
      await open(page, kind); const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
      const field = page.getByLabel(inputs[kind], { exact: true }); await field.fill(rawValues[kind]);
      await page.evaluate(({ identity, value }) => window.__CREATIVE_AUTHORITY__.setIdentity(identity, value), { identity, value: initial[identity] + "-other" });
      await expect(field).not.toHaveValue(rawValues[kind]); await field.fill(kind === "procedural" ? "#ddeeff" : "Independent owner");
      await page.evaluate(({ identity, value }) => window.__CREATIVE_AUTHORITY__.setIdentity(identity, value), { identity, value: initial[identity] });
      await expect(field).toHaveValue(rawValues[kind]); expect(await requests(page)).toEqual([]);
    });
  }
  test(`${kind} incomplete numeric input remains exact and cannot dispatch a Project mutation`, async ({ page }) => {
    await open(page, kind);
    const label = kind === "stage" ? "ProductStage focus X" : kind === "procedural" ? "Integer seed" : "Measured decimals";
    const field = page.getByLabel(label, { exact: true }); await field.fill(""); await field.pressSequentially("1e");
    // Keyboard exercises the original number control; fill('1e') would fail in
    // Playwright before reaching the product assertion and is not a causal RED.
    await expect(field).toHaveValue("1e"); await button(page, "Production plan").click(); await open(page, kind);
    await expect(field).toHaveValue("1e"); expect(await requests(page)).toEqual([]);
  });
  test(`${kind} a reply without the submitted operation cannot clear UNKNOWN`, async ({ page }) => {
    await open(page, kind); await valid(page, kind); const request = await submit(page, saves[kind]);
    await page.evaluate(id => window.__CREATIVE_AUTHORITY__.resolveWithoutOperation(id), request.id);
    await expect(page.getByRole("region", { name: "Unconfirmed " + labels[kind], exact: true })).toBeVisible();
    await expect(button(page, saves[kind])).toBeDisabled(); expect((await requests(page)).length).toBe(1);
  });
  test(`${kind} silent recovery data loss blocks dispatch and preserves author input`, async ({ page }) => {
    await open(page, kind); await valid(page, kind);
    const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
    await page.evaluate(() => {
      const write = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (this === localStorage && key.startsWith("motionwright.creative-workbench-recovery.v1:")) return;
        return write.call(this, key, value);
      };
      window.__CREATIVE_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = write; };
    });
    await button(page, saves[kind]).click();
    await expect(page.getByRole("alert", { name: labels[kind] + " backup warning" })).toBeVisible();
    expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
    await expect(button(page, saves[kind])).toBeDisabled(); await page.evaluate(() => window.__CREATIVE_RESTORE_STORAGE__?.());
  });
  test(`${kind} matching observed props and a lost original reply remain UNKNOWN after reload`, async ({ page }) => {
    await open(page, kind); await valid(page, kind); const request = await submit(page, saves[kind]);
    const field = page.getByLabel(inputs[kind], { exact: true }); const later = kind === "procedural" ? "#ddeeff" : "Later author input"; await field.fill(later);
    await page.evaluate(id => window.__CREATIVE_AUTHORITY__.observeOriginal(id), request.id);
    await expect(page.getByRole("region", { name: "Unconfirmed " + labels[kind], exact: true })).toBeVisible();
    await page.evaluate(id => window.__CREATIVE_AUTHORITY__.reject(id, "Original response was lost"), request.id);
    const saved = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project()); await page.reload(); await open(page, kind);
    await expect(field).toHaveValue(later); await expect(page.getByRole("region", { name: "Unconfirmed " + labels[kind], exact: true })).toBeVisible();
    expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(saved);
  });
}

test("Measured table selection preserves each dataset and binding buffer without writes", async ({ page }) => {
  await open(page, "measured"); const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await page.getByLabel("Measured dataset label").fill("  Unregistered author table  ");
  await page.getByLabel("Measured table", { exact: true }).selectOption(tableA); await page.getByLabel("Measured dataset label").fill("  Draft for A  ");
  await page.getByLabel("Measured decimals").fill("3"); await page.getByLabel("Measured datum row").selectOption("latency");
  await page.getByLabel("Measured table", { exact: true }).selectOption(tableB); await page.getByLabel("Measured dataset label").fill("Draft for B");
  await page.getByLabel("Measured table", { exact: true }).selectOption(tableA); await expect(page.getByLabel("Measured dataset label")).toHaveValue("  Draft for A  ");
  await expect(page.getByLabel("Measured decimals")).toHaveValue("3"); await expect(page.getByLabel("Measured datum row")).toHaveValue("latency");
  await page.getByLabel("Measured table", { exact: true }).selectOption("new"); await expect(page.getByLabel("Measured dataset label")).toHaveValue("  Unregistered author table  ");
  expect(await requests(page)).toEqual([]); expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
});

test("Measured binding original ACK preserves later Canvas selection and raw precision", async ({ page }) => {
  await open(page, "measured"); await page.getByLabel("Measured table", { exact: true }).selectOption(tableA);
  await page.getByLabel("Measured datum row").selectOption("latency");
  const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project()); const node = initial.scenes[0].nodes.find(value => value.kind === "text")!;
  await page.getByLabel("Measured Canvas node").selectOption(node.id); const request = await submit(page, "Bind measured output");
  await page.getByLabel("Measured decimals").fill("3"); await page.evaluate(id => window.__CREATIVE_AUTHORITY__.resolve(id), request.id);
  await expect(page.getByLabel("Measured Canvas node")).toHaveValue(node.id); await expect(page.getByLabel("Measured decimals")).toHaveValue("3");
  const saved = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project()); expect(saved.revision).toBe(initial.revision + 1);
  expect(saved.production_design?.measured_bindings?.[0].view).toEqual({ kind: "counter", decimals: 2 }); expect((await requests(page)).length).toBe(1);
});

for (const mutation of ["typing", "asset", "owner", "unmount", "late-error"] as const) {
  test(`Measured pending file ${mutation} cannot publish rows, proof or an obsolete error`, async ({ page }) => {
    await open(page, "measured"); await page.getByLabel("Measured dataset label").fill("Source awaiting bytes");
    await page.getByLabel("Measured source asset").selectOption(assetA); const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
    await page.evaluate(() => window.__CREATIVE_AUTHORITY__.holdNextFileRead());
    await page.getByLabel("Measured source file verification").setInputFiles({ name: "source.json", mimeType: "application/json", buffer: Buffer.from(sourceA) });
    await expect.poll(() => page.evaluate(() => window.__CREATIVE_AUTHORITY__.heldFileReads())).toBe(1);
    if (mutation === "asset") await page.getByLabel("Measured source asset").selectOption(assetB);
    else if (mutation === "owner") await page.evaluate(value => window.__CREATIVE_AUTHORITY__.setIdentity("id", value), initial.id + "-other");
    else if (mutation === "unmount") { await button(page, "Production plan").click(); await open(page, "measured"); }
    else await page.getByLabel("Measured dataset label").fill("Later author label");
    await page.evaluate(fail => window.__CREATIVE_AUTHORITY__.releaseFileRead(fail), mutation === "late-error");
    await expect(page.getByLabel("Measured numeric rows JSON")).toHaveValue("[]"); await expect(button(page, "Register measured source")).toBeDisabled();
    await expect(page.getByRole("alert").filter({ hasText: "Deferred fixture file failure" })).toHaveCount(0); expect(await requests(page)).toEqual([]);
  });
}

test("Measured completed byte proof tolerates label edits but is never recovered as submit authority", async ({ page }) => {
  await open(page, "measured"); await valid(page, "measured"); await page.getByLabel("Measured dataset label").fill("Renamed after exact byte verification");
  await expect(button(page, "Register measured source")).toBeEnabled(); const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await page.reload(); await open(page, "measured"); await expect(page.getByLabel("Measured dataset label")).toHaveValue("Renamed after exact byte verification");
  expect(JSON.parse(await page.getByLabel("Measured numeric rows JSON").inputValue())).toEqual(JSON.parse(sourceA));
  await expect(button(page, "Register measured source")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
});

test("Measured pending file cannot resurrect a buffer after an explicit saved-value choice", async ({ page }) => {
  await open(page, "measured");
  await page.getByLabel("Measured dataset label").fill("Earlier file direction");
  await page.getByLabel("Measured source asset").selectOption(assetA);
  const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.holdNextFileRead());
  await page.getByLabel("Measured source file verification").setInputFiles({ name: "source.json", mimeType: "application/json", buffer: Buffer.from(sourceA) });
  await expect.poll(() => page.evaluate(() => window.__CREATIVE_AUTHORITY__.heldFileReads())).toBe(1);
  await button(page, "Use saved Measured data values").click();
  await expect(page.getByLabel("Measured dataset label")).toHaveValue("");
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.releaseFileRead());
  await expect(page.getByLabel("Measured dataset label")).toHaveValue("");
  await expect(page.getByLabel("Measured source asset")).toHaveValue("");
  await expect(page.getByLabel("Measured numeric rows JSON")).toHaveValue("[]");
  await expect(button(page, "Register measured source")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
});

test("Measured pending file cannot replace an explicitly restored copy with the same edit sequence", async ({ page }) => {
  await open(page, "measured");
  await page.getByLabel("Measured dataset label").fill("Captured file direction");
  await page.getByLabel("Measured source asset").selectOption(assetA);
  const initial = await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project());
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.holdNextFileRead());
  await page.getByLabel("Measured source file verification").setInputFiles({ name: "source.json", mimeType: "application/json", buffer: Buffer.from(sourceA) });
  await expect.poll(() => page.evaluate(() => window.__CREATIVE_AUTHORITY__.heldFileReads())).toBe(1);
  await page.evaluate(() => {
    const pointer = Object.keys(sessionStorage).find(key => key.startsWith("motionwright.creative-workbench-writer.v1:")
      && JSON.parse(decodeURIComponent(key.slice("motionwright.creative-workbench-writer.v1:".length)))[3] === "measured");
    if (!pointer) throw new Error("Missing measured recovery pointer");
    const scope = decodeURIComponent(pointer.slice("motionwright.creative-workbench-writer.v1:".length));
    const own = localStorage.getItem("motionwright.creative-workbench-recovery.v1:" + encodeURIComponent(scope) + ":" + sessionStorage.getItem(pointer));
    if (!own) throw new Error("Missing measured source copy");
    const copy = JSON.parse(own); copy.writerId = crypto.randomUUID();
    copy.draft.input.tables[copy.draft.input.selectedTable].label = "Explicitly restored direction";
    const key = "motionwright.creative-workbench-recovery.v1:" + encodeURIComponent(scope) + ":" + copy.writerId;
    localStorage.setItem(key, JSON.stringify(copy));
    window.dispatchEvent(new StorageEvent("storage", { key, storageArea: localStorage }));
  });
  const copies = page.getByRole("region", { name: "Measured data recovery copies", exact: true });
  await copies.locator("summary").first().click();
  const selected = copies.getByRole("group").filter({ hasText: "Explicitly restored direction" });
  await selected.getByRole("button", { name: /^Restore Measured data copy / }).click();
  await expect(page.getByLabel("Measured dataset label")).toHaveValue("Explicitly restored direction");
  await page.evaluate(() => window.__CREATIVE_AUTHORITY__.releaseFileRead());
  await expect(page.getByLabel("Measured dataset label")).toHaveValue("Explicitly restored direction");
  await expect(page.getByLabel("Measured numeric rows JSON")).toHaveValue("[]");
  await expect(button(page, "Register measured source")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  expect(await page.evaluate(() => window.__CREATIVE_AUTHORITY__.project())).toEqual(initial);
});

for (const corruption of ["missing", "corrupt"] as const) {
  test(`Measured new-copy action cannot bypass a repaired UNKNOWN after ${corruption} reload`, async ({ page }) => {
    await open(page, "measured"); await valid(page, "measured");
    const request = await submit(page, "Register measured source");
    await page.evaluate(id => window.__CREATIVE_AUTHORITY__.reject(id, "Original result is uncertain"), request.id);
    await expect(page.getByRole("region", { name: "Unconfirmed Measured data", exact: true })).toBeVisible();
    const retained = await page.evaluate(corruption => {
      const pointer = Object.keys(sessionStorage).find(key => key.startsWith("motionwright.creative-workbench-writer.v1:")
        && JSON.parse(decodeURIComponent(key.slice("motionwright.creative-workbench-writer.v1:".length)))[3] === "measured");
      if (!pointer) throw new Error("Missing measured recovery pointer");
      const scope = decodeURIComponent(pointer.slice("motionwright.creative-workbench-writer.v1:".length));
      const key = "motionwright.creative-workbench-recovery.v1:" + encodeURIComponent(scope) + ":" + sessionStorage.getItem(pointer);
      const raw = localStorage.getItem(key); if (!raw) throw new Error("Missing UNKNOWN source copy");
      if (JSON.parse(raw).draft.pending?.outcome !== "UNKNOWN") throw new Error("The retained source is not UNKNOWN");
      if (corruption === "missing") localStorage.removeItem(key); else localStorage.setItem(key, "{broken");
      return { key, raw };
    }, corruption);
    await page.reload(); await open(page, "measured");
    await expect(page.getByRole("alert", { name: "Measured data backup warning", exact: true })).toBeVisible();
    await page.getByLabel("Measured dataset label").fill("Later retained author input");
    await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), retained);
    await button(page, "Keep Measured data input in a new copy").click();
    await expect(page.getByRole("region", { name: "Unconfirmed Measured data", exact: true })).toBeVisible();
    await expect(page.getByLabel("Measured dataset label")).toHaveValue("Later retained author input");
    await expect(button(page, "Register measured source")).toBeDisabled();
    expect(await page.evaluate(key => localStorage.getItem(key), retained.key)).toBe(retained.raw);
    expect(await requests(page)).toEqual([]);
  });
}
