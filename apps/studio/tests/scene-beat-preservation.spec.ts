import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import type { BeatFixtureRequest } from "./fixtures/scene-beat-authority";

// Component regression evidence only. Props and the browser transport are
// explicitly synthetic; SceneBeatEditor and ProjectMutationGate are real.
const recoveryPrefix = "motionwright.scene-beat-recovery.v1:";
const pointerPrefix = "motionwright.scene-beat-writer.v1:";
const requests = (page: Page) => page.evaluate(() => window.__BEAT_AUTHORITY__.requests());
const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
const input = (row: Locator, field: "label" | "objective" | "start" | "duration") =>
  row.getByLabel("Beat " + field + (field === "start" || field === "duration" ? " seconds" : ""), { exact: true });
async function row(page: Page) {
  const id = await page.evaluate(() => window.__BEAT_AUTHORITY__.beatId());
  return page.locator('.scene-beat-row[data-beat-id="' + id + '"]');
}
async function open(page: Page) {
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/tests/fixtures/scene-beat-authority.html");
  await expect(page.getByRole("region", { name: "Scene beats", exact: true })).toBeVisible();
}
async function submit(page: Page, target: Locator, name = "Save beat"): Promise<BeatFixtureRequest> {
  const before = (await requests(page)).length;
  await button(target, name).click();
  await expect.poll(async () => (await requests(page)).length).toBe(before + 1);
  return (await requests(page))[before];
}
async function copies(page: Page) {
  return page.evaluate(prefix => Object.keys(localStorage).filter(key => key.startsWith(prefix))
    .map(key => ({ key, raw: localStorage.getItem(key)! })), recoveryPrefix);
}
test.beforeEach(async ({ page }) => open(page));

test("all raw beat fields survive workspace remount and page reload, including incomplete fractions and exponents", async ({ page }) => {
  const target = await row(page), raw = { label: "  Author label  ", objective: "  Author objective\nTwo lines  \n", start: "1e", duration: "1001/" };
  for (const field of ["label", "objective", "start", "duration"] as const) await input(target, field).fill(raw[field]);
  await expect(button(target, "Save beat")).toBeDisabled();
  await page.evaluate(() => window.__BEAT_AUTHORITY__.unmount());
  await page.evaluate(() => window.__BEAT_AUTHORITY__.mount());
  for (const field of ["label", "objective", "start", "duration"] as const) await expect(input(target, field)).toHaveValue(raw[field]);
  await page.reload();
  for (const field of ["label", "objective", "start", "duration"] as const) await expect(input(target, field)).toHaveValue(raw[field]);
  expect(await requests(page)).toEqual([]);
});

for (const field of ["id", "generation", "active_branch", "sceneId", "beatId"] as const) {
  test(`equal saved beats under another ${field} cannot adopt the original scope's raw draft`, async ({ page }) => {
    const initial = await page.evaluate(() => ({ project: window.__BEAT_AUTHORITY__.project(), sceneId: window.__BEAT_AUTHORITY__.sceneId(), beatId: window.__BEAT_AUTHORITY__.beatId() }));
    const original = field === "sceneId" ? initial.sceneId : field === "beatId" ? initial.beatId : initial.project[field];
    const source = initial.project.scenes.find(scene => scene.id === initial.sceneId)!.beats.find(beat => beat.id === initial.beatId)!;
    const target = await row(page);
    await input(target, "objective").fill("Private A beat intent"); await input(target, "start").fill("1e");
    await page.evaluate(({ field, value }) => window.__BEAT_AUTHORITY__.setIdentity(field, value), { field, value: "20000000-0000-4000-8000-000000000001" });
    const foreign = await row(page);
    await expect(input(foreign, "objective")).toHaveValue(source.objective);
    await expect(input(foreign, "start")).toHaveValue("1001/30000");
    await input(foreign, "objective").fill("Separate B beat intent");
    await page.evaluate(({ field, value }) => window.__BEAT_AUTHORITY__.setIdentity(field, value), { field, value: original });
    await expect(input(await row(page), "objective")).toHaveValue("Private A beat intent");
    await expect(input(await row(page), "start")).toHaveValue("1e");
    expect(await requests(page)).toEqual([]);
  });
}

test("the original beat reply preserves later raw edits through remount and a second explicit save", async ({ page }) => {
  const target = await row(page);
  await input(target, "objective").fill("Submitted objective A");
  const first = await submit(page, target);
  await page.evaluate(() => window.__BEAT_AUTHORITY__.unmount());
  await page.evaluate(() => window.__BEAT_AUTHORITY__.mount());
  await input(target, "objective").fill("  Later objective B\n"); await input(target, "start").fill("1e");
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), first.id);
  await expect(input(target, "objective")).toHaveValue("  Later objective B\n");
  await expect(input(target, "start")).toHaveValue("1e");
  await expect(target.getByRole("region", { name: "Unconfirmed beat operation", exact: true })).toHaveCount(0);
  await expect(button(target, "Save beat")).toBeDisabled();
  await input(target, "start").fill("1/48000");
  const second = await submit(page, target);
  expect(second.source.revision).toBe(first.source.revision + 1);
  expect(second.change.type).toBe("upsert_scene_beat");
  if (second.change.type !== "upsert_scene_beat") throw new Error("Expected actual beat command from the component.");
  expect(second.change.beat.objective).toBe("  Later objective B\n");
  expect(second.change.beat.start).toEqual({ num: "1", den: "48000" });
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), second.id);
  await page.reload();
  await expect(input(target, "objective")).toHaveValue("  Later objective B\n");
  await expect(input(target, "start")).toHaveValue("1/48000");
  await expect(button(target, "Save beat")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
});

test("returning to the old objective during a save remains a later decision after the original ACK", async ({ page }) => {
  const target = await row(page), original = await input(target, "objective").inputValue();
  await input(target, "objective").fill("Submitted replacement"); const request = await submit(page, target);
  await input(target, "objective").fill(original);
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  await expect(input(target, "objective")).toHaveValue(original); await expect(button(target, "Save beat")).toBeEnabled();
  await page.reload();
  await expect(input(target, "objective")).toHaveValue(original); await expect(button(target, "Save beat")).toBeEnabled();
  expect(await requests(page)).toEqual([]);
});

test("matching saved props cannot acknowledge an unresolved original beat request", async ({ page }) => {
  const target = await row(page);
  await input(target, "objective").fill("Matching observed value"); const request = await submit(page, target);
  await page.evaluate(id => window.__BEAT_AUTHORITY__.observeOriginal(id), request.id);
  await expect(target.getByRole("region", { name: "Unconfirmed beat operation", exact: true })).toBeVisible();
  await expect(button(target, "Save beat")).toBeDisabled();
  await input(target, "objective").fill("New decision after observation");
  await page.evaluate(id => window.__BEAT_AUTHORITY__.reject(id, "Original reply unavailable after observation"), request.id);
  await page.reload();
  await expect(input(target, "objective")).toHaveValue("New decision after observation");
  await expect(target.getByRole("region", { name: "Unconfirmed beat operation", exact: true })).toContainText("Original reply unavailable");
  await expect(button(target, "Save beat")).toBeDisabled(); expect(await requests(page)).toEqual([]);
});

test("UNKNOWN keeps submitted and later raw values across reload without replay", async ({ page }) => {
  const target = await row(page);
  await input(target, "objective").fill("Submitted operation A"); const request = await submit(page, target);
  await input(target, "objective").fill("Later local decision B"); await input(target, "duration").fill("1e");
  await page.evaluate(id => window.__BEAT_AUTHORITY__.reject(id, "The original reply was lost"), request.id);
  await page.reload();
  await expect(input(target, "objective")).toHaveValue("Later local decision B");
  await expect(input(target, "duration")).toHaveValue("1e");
  await expect(button(target, "Save beat")).toBeDisabled();
  const retained = (await copies(page)).map(item => JSON.parse(item.raw)).find(item => item.draft.pending?.raw.objective === "Submitted operation A");
  expect(retained.draft.raw.objective).toBe("Later local decision B"); expect(retained.draft.pending.outcome).toBe("UNKNOWN");
  expect(await requests(page)).toEqual([]);
});

for (const destination of ["copy", "pointer"] as const) {
  test(`a silent ${destination} write failure prevents dispatch and explicit retry recovers the draft`, async ({ page }) => {
    const target = await row(page);
    await page.evaluate(destination => {
      const original = Storage.prototype.setItem;
      window.__BEAT_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
      Storage.prototype.setItem = function(key: string, value: string) {
        if (destination === "copy" && this === localStorage && key.startsWith("motionwright.scene-beat-recovery.v1:")) return;
        if (destination === "pointer" && this === sessionStorage && key.startsWith("motionwright.scene-beat-writer.v1:")) return;
        original.call(this, key, value);
      };
    }, destination);
    await input(target, "objective").fill("Unbacked raw author bytes");
    await expect(target.getByRole("alert", { name: "Beat backup warning", exact: true })).toBeVisible();
    await expect(button(target, "Save beat")).toBeDisabled(); expect(await requests(page)).toEqual([]);
    await page.evaluate(() => window.__BEAT_AUTHORITY__.unmount()); await page.evaluate(() => window.__BEAT_AUTHORITY__.mount());
    await expect(input(target, "objective")).toHaveValue("Unbacked raw author bytes");
    await page.evaluate(() => window.__BEAT_TEST_RESTORE__!());
    await button(target, "Retry beat backup").click();
    await expect(button(target, "Save beat")).toBeEnabled();
    const request = await submit(page, target); await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
    await expect(button(target, "Save beat")).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
  });
}

test("a pointer failure leaves a readable orphan which can be explicitly restored after reload", async ({ page }) => {
  const target = await row(page), raw = "Orphaned beat author text remains recoverable";
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key: string, value: string) {
      if (this === sessionStorage && key.startsWith("motionwright.scene-beat-writer.v1:")) return;
      original.call(this, key, value);
    };
  });
  await input(target, "objective").fill(raw); await expect(button(target, "Save beat")).toBeDisabled();
  await page.reload(); await expect(input(target, "objective")).not.toHaveValue(raw);
  const recovery = page.getByRole("region", { name: "Beat recovery copies", exact: true }); await recovery.locator("summary").click();
  const copy = recovery.getByRole("group").filter({ hasText: raw });
  await copy.getByRole("button", { name: /^Restore beat copy / }).click();
  await expect(input(target, "objective")).toHaveValue(raw); await expect(button(target, "Save beat")).toBeEnabled();
  expect(await requests(page)).toEqual([]);
});

test("a failed ACK checkpoint retains the armed original and later input without treating reload as confirmation", async ({ page }) => {
  const target = await row(page);
  await input(target, "objective").fill("Original submitted beat"); const request = await submit(page, target);
  await input(target, "objective").fill("Later author input retained before ACK");
  const armed = (await copies(page)).find(item => JSON.parse(item.raw).draft.pending?.raw.objective === "Original submitted beat")!;
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key: string, value: string) {
      if (this === localStorage && key.startsWith("motionwright.scene-beat-recovery.v1:") && JSON.parse(value).draft.pending === null) return;
      original.call(this, key, value);
    };
  });
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  await expect(input(target, "objective")).toHaveValue("Later author input retained before ACK");
  await expect(target.getByRole("alert", { name: "Beat backup warning", exact: true })).toBeVisible();
  expect(await page.evaluate(key => localStorage.getItem(key), armed.key)).toBe(armed.raw);
  await page.reload();
  await expect(input(target, "objective")).toHaveValue("Later author input retained before ACK");
  await expect(target.getByRole("region", { name: "Unconfirmed beat operation", exact: true })).toBeVisible();
  await expect(button(target, "Save beat")).toBeDisabled(); expect(await requests(page)).toEqual([]);
});

test("a concurrent retime keeps raw text until the author explicitly combines it with saved timing", async ({ page }) => {
  const target = await row(page); await input(target, "objective").fill("My new wording"); await input(target, "start").fill("1e");
  await page.evaluate(async () => {
    const api = window.__BEAT_AUTHORITY__, project = api.project(), scene = project.scenes.find(item => item.id === api.sceneId())!;
    const beat = scene.beats.find(item => item.id === api.beatId())!;
    await api.externalChange({ type: "upsert_scene_beat", scene_id: scene.id, beat: { ...beat, start: { num: "1", den: "4" }, duration: { num: "1", den: "2" } } });
  });
  await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toBeVisible();
  await expect(input(target, "start")).toHaveValue("1e"); await expect(button(target, "Save beat")).toBeDisabled();
  await button(target, "Keep my text with saved timing").click();
  await expect(input(target, "objective")).toHaveValue("My new wording"); await expect(input(target, "start")).toHaveValue("0.25");
  const request = await submit(page, target); await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  expect(request.change.type === "upsert_scene_beat" && request.change.beat.start).toEqual({ num: "1", den: "4" });
  expect((await copies(page)).some(item => JSON.parse(item.raw).draft.raw.start === "1e")).toBe(true);
});

test("a shortened scene retains an out-of-range raw draft until explicit comparison and repair", async ({ page }) => {
  const target = await row(page); await input(target, "start").fill("6"); await input(target, "duration").fill("1");
  await page.evaluate(() => window.__BEAT_AUTHORITY__.externalChange({ type: "set_scene_duration", scene_id: window.__BEAT_AUTHORITY__.sceneId(), duration: { num: "2", den: "1" } }));
  await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toContainText("scene duration changed");
  await button(target, "Keep my full beat draft").click();
  await expect(input(target, "start")).toHaveValue("6"); await expect(button(target, "Save beat")).toBeDisabled();
  await page.reload(); await expect(input(target, "start")).toHaveValue("6");
  await input(target, "start").fill("1/48000"); const request = await submit(page, target);
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id); await expect(button(target, "Save beat")).toBeDisabled();
});

test("a concurrently removed beat keeps raw input and needs explicit recreation before any command", async ({ page }) => {
  const target = await row(page); await input(target, "objective").fill("Retained wording after removal");
  await page.evaluate(() => window.__BEAT_AUTHORITY__.externalChange({ type: "remove_scene_beat", scene_id: window.__BEAT_AUTHORITY__.sceneId(), beat_id: window.__BEAT_AUTHORITY__.beatId() }));
  await page.reload(); await expect(input(target, "objective")).toHaveValue("Retained wording after removal");
  await expect(button(target, "Recreate beat")).toBeDisabled(); expect(await requests(page)).toEqual([]);
  await button(target, "Prepare explicit beat recreation").click();
  await expect(button(target, "Recreate beat")).toBeEnabled(); expect(await requests(page)).toEqual([]);
  const request = await submit(page, target, "Recreate beat"); await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  await expect(button(target, "Save beat")).toBeDisabled(); expect(await requests(page)).toHaveLength(1);
});

test("late typing during the original removal stays recoverable without automatically recreating the beat", async ({ page }) => {
  const target = await row(page), label = await input(target, "label").inputValue();
  const request = await submit(page, target, "Remove beat " + label);
  await input(target, "objective").fill("Late decision while removal was pending");
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  await expect(input(target, "objective")).toHaveValue("Late decision while removal was pending");
  await expect(button(target, "Recreate beat")).toBeDisabled();
  await page.reload(); await expect(input(target, "objective")).toHaveValue("Late decision while removal was pending");
  await expect(button(target, "Recreate beat")).toBeDisabled(); expect(await requests(page)).toEqual([]);
});

test("Fill timing gap uses a retained UNKNOWN draft if its original creation reply is unavailable", async ({ page }) => {
  const request = await submit(page, page.getByRole("region", { name: "Scene beats", exact: true }), "Fill timing gap");
  if (request.change.type !== "upsert_scene_beat") throw new Error("Expected gap-fill beat creation.");
  const target = page.locator('.scene-beat-row[data-beat-id="' + request.change.beat.id + '"]');
  await input(target, "objective").fill("New gap beat later wording");
  await page.evaluate(id => window.__BEAT_AUTHORITY__.reject(id, "Gap creation reply unavailable"), request.id);
  await page.reload();
  await expect(input(target, "objective")).toHaveValue("New gap beat later wording");
  await expect(target.getByRole("region", { name: "Unconfirmed beat operation", exact: true })).toBeVisible();
  await expect(button(target, "Recreate beat")).toBeDisabled(); expect(await requests(page)).toEqual([]);
});

test("two windows retain separate beat writers and never choose the newest foreign copy on reload", async ({ page, context }) => {
  const target = await row(page); await input(target, "objective").fill("Window A original bytes");
  const second = await context.newPage();
  try {
    await open(second); const other = await row(second); await expect(input(other, "objective")).not.toHaveValue("Window A original bytes");
    await input(other, "objective").fill("Window B separate bytes");
    await page.reload(); await second.reload();
    await expect(input(target, "objective")).toHaveValue("Window A original bytes"); await expect(input(other, "objective")).toHaveValue("Window B separate bytes");
    expect(await requests(page)).toEqual([]); expect(await requests(second)).toEqual([]);
  } finally { await second.close(); }
});

test("a corrupt own beat copy stays intact and cannot silently adopt a readable foreign copy", async ({ page, context }) => {
  const target = await row(page); await input(target, "objective").fill("Window A original author bytes");
  const second = await context.newPage();
  try {
    await open(second); await input(await row(second), "objective").fill("Foreign readable author bytes");
    const key = await page.evaluate(({ recoveryPrefix, pointerPrefix }) => {
      const pointer = Object.keys(sessionStorage).find(key => key.startsWith(pointerPrefix))!;
      const key = recoveryPrefix + pointer.slice(pointerPrefix.length) + ":" + sessionStorage.getItem(pointer);
      localStorage.setItem(key, "CORRUPT ORIGINAL BEAT BYTES"); return key;
    }, { recoveryPrefix, pointerPrefix });
    await page.reload(); await expect(input(target, "objective")).not.toHaveValue("Foreign readable author bytes");
    await expect(target.getByRole("alert", { name: "Beat backup warning", exact: true })).toBeVisible();
    expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe("CORRUPT ORIGINAL BEAT BYTES");
    expect(await requests(page)).toEqual([]);
  } finally { await second.close(); }
});

test("a cloned tab inherits a readable draft but forks its writer before independent edits", async ({ page, context }) => {
  const target = await row(page); await input(target, "objective").fill("Inherited initial draft");
  const opened = context.waitForEvent("page"); await page.evaluate(() => { window.open("/tests/fixtures/scene-beat-authority.html", "_blank"); });
  const second = await opened; second.on("dialog", dialog => dialog.accept());
  try {
    await expect(second.getByRole("region", { name: "Scene beats", exact: true })).toBeVisible();
    const other = await row(second); await expect(input(other, "objective")).toHaveValue("Inherited initial draft");
    await input(other, "objective").fill("Cloned tab B independent text"); await input(target, "objective").fill("Original tab A later text");
    const pointer = (page: Page) => page.evaluate(prefix => Object.keys(sessionStorage).filter(key => key.startsWith(prefix)).map(key => sessionStorage.getItem(key)), pointerPrefix);
    expect(await pointer(page)).not.toEqual(await pointer(second));
    await page.reload(); await second.reload();
    await expect(input(target, "objective")).toHaveValue("Original tab A later text"); await expect(input(other, "objective")).toHaveValue("Cloned tab B independent text");
    expect(await requests(page)).toEqual([]); expect(await requests(second)).toEqual([]);
  } finally { await second.close(); }
});

test("a late original reply cannot replace a different selected owner's beat draft", async ({ page }) => {
  const originalId = await page.evaluate(() => window.__BEAT_AUTHORITY__.project().id), target = await row(page);
  await input(target, "objective").fill("A submitted value"); const request = await submit(page, target);
  await input(target, "objective").fill("A later retained value");
  await page.evaluate(() => window.__BEAT_AUTHORITY__.setIdentity("id", "30000000-0000-4000-8000-000000000001"));
  await input(target, "objective").fill("B separate author value");
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  await expect(input(target, "objective")).toHaveValue("B separate author value");
  await expect(unconfirmedFor(target)).toHaveCount(0);
  await page.evaluate(id => window.__BEAT_AUTHORITY__.setIdentity("id", id), originalId);
  await expect(input(target, "objective")).toHaveValue("A later retained value");
  await expect(unconfirmedFor(target)).toBeVisible(); expect(await requests(page)).toHaveLength(1);
});

function unconfirmedFor(target: Locator) { return target.getByRole("region", { name: "Unconfirmed beat operation", exact: true }); }

test("a downloaded draft survives complete local recovery failure and imports as editable raw input", async ({ page }) => {
  const target = await row(page);
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key: string, value: string) {
      if (key.startsWith("motionwright.scene-beat-recovery.v1:") || key.startsWith("motionwright.scene-beat-writer.v1:")) throw new Error("Recovery storage unavailable");
      original.call(this, key, value);
    };
  });
  await input(target, "objective").fill("  Portable author words\n"); await input(target, "start").fill("1e");
  const pendingDownload = page.waitForEvent("download"); await button(target, "Download beat draft").click();
  const download = await pendingDownload, path = await download.path(); if (!path) throw new Error("The browser did not retain the downloaded file.");
  const raw = await readFile(path, "utf8"); expect(JSON.parse(raw).draft.raw.start).toBe("1e");
  expect(JSON.parse(raw).draft.raw.objective).toBe("  Portable author words\n");
  await page.reload(); await expect(input(target, "objective")).not.toHaveValue("  Portable author words\n");
  const files = page.getByRole("region", { name: "Beat recovery files", exact: true }); await files.locator("summary").click();
  await page.getByLabel("Import beat draft file", { exact: true }).setInputFiles({ name: "beat-recovered.json", mimeType: "application/json", buffer: Buffer.from(raw) });
  await expect(files.getByRole("region", { name: "Imported beat candidate", exact: true })).toContainText("Portable author words");
  await expect(input(target, "objective")).not.toHaveValue("  Portable author words\n"); expect(await requests(page)).toEqual([]);
  await button(files, "Use imported beat copy").click(); await expect(input(target, "objective")).toHaveValue("  Portable author words\n");
  await expect(input(target, "start")).toHaveValue("1e"); await expect(button(target, "Save beat")).toBeDisabled();
  await page.reload(); await expect(input(target, "start")).toHaveValue("1e");
  await input(target, "start").fill("1/48000"); const request = await submit(page, target);
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id); await expect(button(target, "Save beat")).toBeDisabled();
});

test("import cannot overwrite current unbacked input, and foreign-owner files are rejected", async ({ page }) => {
  const target = await row(page); await input(target, "objective").fill("File candidate A");
  const raw = (await copies(page))[0].raw;
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key: string, value: string) {
      if (this === localStorage && key.startsWith("motionwright.scene-beat-recovery.v1:")) return;
      original.call(this, key, value);
    };
  });
  await input(target, "objective").fill("Current unbacked author B");
  const files = page.getByRole("region", { name: "Beat recovery files", exact: true }); await files.locator("summary").click();
  await page.getByLabel("Import beat draft file", { exact: true }).setInputFiles({ name: "prior.json", mimeType: "application/json", buffer: Buffer.from(raw) });
  await button(files, "Use imported beat copy").click();
  await expect(input(target, "objective")).toHaveValue("Current unbacked author B");
  await expect(files.getByRole("region", { name: "Imported beat candidate", exact: true })).toContainText("File candidate A");
  await expect(page.getByRole("alert", { name: "Beat recovery list warning", exact: true })).toContainText("Both inputs remain available");
  const foreign = JSON.parse(raw), identity = JSON.parse(foreign.scope); identity[1] = "40000000-0000-4000-8000-000000000001";
  foreign.scope = JSON.stringify(identity); foreign.draft.scope = foreign.scope;
  await page.getByLabel("Import beat draft file", { exact: true }).setInputFiles({ name: "foreign.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(foreign)) });
  await expect(page.getByRole("alert", { name: "Beat recovery list warning", exact: true })).toContainText("another project");
  await expect(input(target, "objective")).toHaveValue("Current unbacked author B"); expect(await requests(page)).toEqual([]);
});

async function savedCleanCheckpoint(page: Page, objective: string) {
  const target = await row(page);
  await input(target, "objective").fill(objective);
  const request = await submit(page, target);
  await page.evaluate(id => window.__BEAT_AUTHORITY__.resolve(id), request.id);
  await expect(button(target, "Save beat")).toBeDisabled();
  const checkpoint = (await copies(page)).find(item => {
    const draft = JSON.parse(item.raw).draft;
    return draft.pending === null && draft.intent === false && draft.raw.objective === objective;
  });
  if (!checkpoint) throw new Error("The original ACK did not produce the expected clean checkpoint.");
  return checkpoint;
}

async function changeSavedObjective(page: Page, objective: string) {
  await page.evaluate(async objective => {
    const api = window.__BEAT_AUTHORITY__, project = api.project();
    const scene = project.scenes.find(item => item.id === api.sceneId())!;
    const beat = scene.beats.find(item => item.id === api.beatId())!;
    await api.externalChange({ type: "upsert_scene_beat", scene_id: scene.id, beat: { ...beat, objective } });
  }, objective);
}

async function restoreCatalogObjective(page: Page, objective: string) {
  const recovery = page.getByRole("region", { name: "Beat recovery copies", exact: true });
  await recovery.locator("summary").click();
  const copy = recovery.getByRole("group").filter({ hasText: objective }).filter({ hasText: "Local input" });
  await expect(copy).toHaveCount(1);
  await copy.getByRole("button", { name: /^Restore beat copy / }).click();
}

for (const method of ["catalog", "file"] as const) {
  test("explicit " + method + " restore keeps clean checkpoint A when the saved beat is B", async ({ page }) => {
    const target = await row(page), checkpoint = await savedCleanCheckpoint(page, "Clean checkpoint A");
    const original = JSON.parse(checkpoint.raw).draft;
    let downloaded: string | null = null;
    if (method === "file") {
      const pending = page.waitForEvent("download");
      await button(target, "Download beat draft").click();
      const download = await pending, path = await download.path();
      if (!path) throw new Error("The browser did not retain the clean checkpoint download.");
      downloaded = await readFile(path, "utf8");
      expect(JSON.parse(downloaded).draft.intent).toBe(false);
      expect(JSON.parse(downloaded).draft.raw).toEqual(original.raw);
    }
    await changeSavedObjective(page, "Saved peer beat B");
    await expect(input(target, "objective")).toHaveValue("Saved peer beat B");
    await input(target, "objective").fill("Current unsent input C");
    await input(target, "start").fill("1e");
    const previous = (await copies(page)).find(item => {
      const draft = JSON.parse(item.raw).draft;
      return draft.pending === null && draft.raw.objective === "Current unsent input C" && draft.raw.start === "1e";
    });
    if (!previous) throw new Error("The current unsent input did not obtain its own recovery copy.");
    if (method === "catalog") {
      await restoreCatalogObjective(page, "Clean checkpoint A");
    } else {
      const files = page.getByRole("region", { name: "Beat recovery files", exact: true });
      await files.locator("summary").click();
      await page.getByLabel("Import beat draft file", { exact: true }).setInputFiles({
        name: "clean-checkpoint.json", mimeType: "application/json", buffer: Buffer.from(downloaded!),
      });
      await expect(files.getByRole("region", { name: "Imported beat candidate", exact: true })).toContainText("Clean checkpoint A");
      await button(files, "Use imported beat copy").click();
    }
    expect(await page.evaluate(key => localStorage.getItem(key), previous.key)).toBe(previous.raw);
    expect(await page.evaluate(key => localStorage.getItem(key), checkpoint.key)).toBe(checkpoint.raw);
    for (const field of ["label", "objective", "start", "duration"] as const)
      await expect(input(target, field)).toHaveValue(original.raw[field]);
    await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toBeVisible();
    await expect(button(target, "Save beat")).toBeDisabled();
    expect(await requests(page)).toHaveLength(1);
    await page.reload();
    await expect(input(target, "objective")).toHaveValue("Clean checkpoint A");
    await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toBeVisible();
    expect(await requests(page)).toEqual([]);
    await button(target, "Keep my full beat draft").click();
    await expect(button(target, "Save beat")).toBeEnabled();
    expect(await requests(page)).toEqual([]);
  });
}

test("restoring a clean checkpoint retains the prior unsent draft as a separately recoverable copy", async ({ page }) => {
  const target = await row(page);
  await savedCleanCheckpoint(page, "Clean checkpoint A");
  await changeSavedObjective(page, "Saved peer beat B");
  await input(target, "objective").fill("Prior unsent author input C");
  await input(target, "start").fill("1001/");
  const previous = (await copies(page)).find(item => {
    const draft = JSON.parse(item.raw).draft;
    return draft.pending === null && draft.raw.objective === "Prior unsent author input C" && draft.raw.start === "1001/";
  });
  if (!previous) throw new Error("The active author input did not obtain a recovery copy.");
  await restoreCatalogObjective(page, "Clean checkpoint A");
  expect(await page.evaluate(key => localStorage.getItem(key), previous.key)).toBe(previous.raw);
  await page.reload();
  await restoreCatalogObjective(page, "Prior unsent author input C");
  await expect(input(target, "objective")).toHaveValue("Prior unsent author input C");
  await expect(input(target, "start")).toHaveValue("1001/");
  await expect(button(target, "Save beat")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
});

test("automatic reload of a clean checkpoint follows the newer saved beat without creating author intent", async ({ page }) => {
  const target = await row(page), checkpoint = await savedCleanCheckpoint(page, "Clean checkpoint A");
  await page.evaluate(() => window.__BEAT_AUTHORITY__.unmount());
  await changeSavedObjective(page, "Saved peer beat B");
  await page.reload();
  await expect(input(target, "objective")).toHaveValue("Saved peer beat B");
  await expect(button(target, "Save beat")).toBeDisabled();
  await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toHaveCount(0);
  await expect(unconfirmedFor(target)).toHaveCount(0);
  expect(await page.evaluate(key => localStorage.getItem(key), checkpoint.key)).toBe(checkpoint.raw);
  expect(await requests(page)).toEqual([]);
});

test("a closed recovery catalog exposes every retained copy after storage enumeration changes", async ({ page }) => {
  // Only storage enumeration varies. The existing synthetic reply fixture
  // still drives the real editor and coordinator; the override leaves all
  // stored recovery bytes and returned project snapshots unchanged.
  await page.evaluate(() => {
    const originalKey = Storage.prototype.key;
    window.__BEAT_TEST_RESTORE__ = () => { Storage.prototype.key = originalKey; };
    Storage.prototype.key = function(index: number) {
      if (this !== localStorage) return originalKey.call(this, index);
      const entries = Array.from({ length: this.length }, (_, at) => originalKey.call(this, at))
        .filter((key): key is string => key !== null)
        .map(key => ({ key, draft: key.startsWith("motionwright.scene-beat-recovery.v1:")
          ? JSON.parse(this.getItem(key)!).draft : null }));
      const hasCurrent = entries.some(entry => entry.draft?.raw.objective === "Current unsent input C");
      const hasPeer = entries.some(entry => entry.draft?.raw.objective === "Saved peer beat B");
      const rank = (entry: typeof entries[number]) => {
        if (!entry.draft) return 9;
        if (hasCurrent) {
          if (entry.draft.raw.objective === "Current unsent input C") return 0;
          if (!entry.draft.pending && entry.draft.raw.objective === "Clean checkpoint A") return 1;
          return entry.draft.pending ? 2 : 3;
        }
        if (hasPeer) return entry.draft.pending ? 0
          : entry.draft.raw.objective === "Saved peer beat B" ? 1 : 2;
        return entry.draft.pending ? 0 : 1;
      };
      entries.sort((left, right) => rank(left) - rank(right)
        || (left.key < right.key ? -1 : left.key > right.key ? 1 : 0));
      return entries[index]?.key ?? null;
    };
  });
  try {
    const target = await row(page), checkpoint = await savedCleanCheckpoint(page, "Clean checkpoint A");
    const original = JSON.parse(checkpoint.raw).draft;
    const armed = (await copies(page)).find(item => JSON.parse(item.raw).draft.pending !== null);
    if (!armed) throw new Error("The original submitted attempt was not retained.");
    await changeSavedObjective(page, "Saved peer beat B");
    await expect(input(target, "objective")).toHaveValue("Saved peer beat B");
    await input(target, "objective").fill("Current unsent input C");
    await input(target, "start").fill("1e");
    const retained = await copies(page);
    expect(retained).toHaveLength(4);
    expect(new Set(retained.map(item => item.key)).size).toBe(4);
    const previous = retained.find(item => {
      const draft = JSON.parse(item.raw).draft;
      return draft.pending === null && draft.raw.objective === "Current unsent input C" && draft.raw.start === "1e";
    });
    if (!previous) throw new Error("Current author input did not obtain its own recovery copy.");
    expect(retained.find(item => item.key === checkpoint.key)?.raw).toBe(checkpoint.raw);
    expect(retained.find(item => item.key === armed.key)?.raw).toBe(armed.raw);
    const enumerated = await page.evaluate(() => Array.from({ length: localStorage.length }, (_, at) => localStorage.key(at))
      .filter((key): key is string => key !== null && key.startsWith("motionwright.scene-beat-recovery.v1:"))
      .map(key => {
        const draft = JSON.parse(localStorage.getItem(key)!).draft;
        return [draft.raw.objective, draft.pending === null ? "local" : "unknown"];
      }));
    expect(enumerated).toEqual([
      ["Current unsent input C", "local"], ["Clean checkpoint A", "local"],
      ["Clean checkpoint A", "unknown"], ["Saved peer beat B", "local"],
    ]);
    const recovery = page.getByRole("region", { name: "Beat recovery copies", exact: true });
    expect(await recovery.getAttribute("open")).toBeNull();
    await recovery.locator("summary").click();
    await expect(recovery).toHaveAttribute("open", "");
    const groups = recovery.getByRole("group");
    await expect(groups).toHaveCount(4);
    const clean = groups.filter({ hasText: "Clean checkpoint A" }).filter({ hasText: "Local input" });
    await expect(clean).toHaveCount(1);
    await expect(clean).toBeVisible();
    await expect(groups.filter({ hasText: "Current unsent input C" })).toBeVisible();
    await expect(groups.filter({ hasText: "Saved peer beat B" })).toBeVisible();
    await expect(groups.filter({ hasText: "Original unconfirmed attempt" })).toBeVisible();
    await clean.getByRole("button", { name: /^Restore beat copy / }).click();
    for (const field of ["label", "objective", "start", "duration"] as const)
      await expect(input(target, field)).toHaveValue(original.raw[field]);
    await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toBeVisible();
    await expect(button(target, "Save beat")).toBeDisabled();
    expect(await requests(page)).toHaveLength(1);
    for (const copy of [checkpoint, previous, armed])
      expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
    await page.reload();
    await expect(input(target, "objective")).toHaveValue("Clean checkpoint A");
    await expect(target.getByRole("region", { name: "Beat draft conflict", exact: true })).toBeVisible();
    expect(await requests(page)).toEqual([]);
  } finally {
    await page.evaluate(() => window.__BEAT_TEST_RESTORE__?.());
  }
});
