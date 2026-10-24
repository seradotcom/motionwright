import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test as base, type Page } from "@playwright/test";
import { defaultOriginalMix, recipeDraftScope, recipeDraftStorageKey, type DeliveryRecipe } from "../src/deliveryRecipe";
import { recipePointerKey, type RecipeAuthorState } from "../src/deliveryRecipeDraft";
import type { Change, DeliverableProfile, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> desktop invoke fixture -> real StudioService/grants/SQLite.
// Requires the matching target/envelope wire; no simulated-service fallback.
// These authored values are not measurements, native PCM or QC acceptance.
type TargetsFixture = { service: ScopedChangeService; peers: ScopedChangeService[]; initial: Project;
  a: DeliverableProfile; b: DeliverableProfile; cleanups: Set<() => Promise<void>> };
const test = base.extend<{ targets: TargetsFixture }>({
  targets: [async ({ page }, use, info) => {
    const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
    const peers: ScopedChangeService[] = [], cleanups = new Set<() => Promise<void>>(); let initial: Project | undefined;
    try {
      initial = (await service.fixtureBootstrap()).project;
      for (const scene of initial.scenes) if (scene.renderer !== "motion-canvas") initial = await service.apply(initial, { type: "set_scene_renderer", scene_id: scene.id, renderer: "motion-canvas" });
      while (initial.deliverables.length < 2) initial = await service.apply(initial, { type: "upsert_deliverable", profile: newProfile("Target fixture " + initial.deliverables.length) });
      const [a, b] = initial.deliverables;
      const recipe: DeliveryRecipe = { schema: "motionwright.delivery-recipe/1", scene_intents: initial.scenes.map((scene, index) => ({
        scene_id: scene.id, role: index === 0 ? "hook" : "problem", archetype: index === 0 ? "statement" : "diagram_build" })),
        original_mix: { ...defaultOriginalMix(), voice_envelope: { muted: false, fade_in_samples: 2400, fade_out_samples: 1200 },
          music_envelope: { muted: true, fade_in_samples: 0, fade_out_samples: 600 }, sfx_envelope: { muted: false, fade_in_samples: 120, fade_out_samples: 240 } } };
      for (const selected of [a, b]) initial = await service.apply(initial, { type: "set_delivery_recipe", deliverable_profile_id: selected.id, recipe });
      await service.attachFixturePage(page, initial.id); await page.goto("/"); await openTargets(page, a);
      await use({ service, peers, initial, a, b, cleanups });
    } finally {
      const quiescence = await Promise.allSettled([...cleanups].map(close => close()));
      const errors = quiescence.flatMap(outcome => outcome.status === "rejected" ? [String(outcome.reason)] : []);
      let final: Project | undefined, observationError: unknown, finalization: StoreFinalization | undefined;
      try { await page.close(); if (initial) final = await service.project(initial.id); } catch (error) { observationError = error; }
      try {
        finalization = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [async () => {
          if (observationError) throw observationError;
          if (errors.length) throw new AggregateError(errors.map(error => new Error(error)), "Target transport cleanup did not drain");
        }]);
      } finally {
        const path = info.outputPath("delivery-targets-real-service-evidence.json");
        await writeFile(path, JSON.stringify({ schema: "a3.delivery-targets-service-evidence/1", status: info.status,
          scope: "actual_App_UI_real_StudioService_grants_SQLite_full_journal_closed_store",
          exclusions: ["installed Tauri", "native PCM/Film render", "audio measurements", "QC acceptance", "human acceptance", "sealed-copy reopen until owner verifies archive"],
          requiredComposition: "Matching DeliveryRecipe audio_targets nullable pair / exact signed-i32 milli units and bus envelopes",
          initial, final, canonicalOracle: finalization?.oracle, quiescence: quiescence.map(outcome => outcome.status), cleanupErrors: errors,
          observationError: observationError ? String(observationError) : null, finalizationPath: info.outputPath("closed-store-outcomes.json"),
          services: [service, ...peers].map(item => ({ binaryPath: item.binaryPath, databasePath: item.databasePath, transcript: item.transcript,
            processExits: item.processExits, lifecycleTaints: item.lifecycleTaints, stderr: item.stderr })),
        }, null, 2));
        await info.attach("delivery-targets-real-service-evidence", { path, contentType: "application/json" });
        await info.attach("delivery-targets-closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
      }
    }
  }, { timeout: 90_000 }],
});
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Build the exact matching target/envelope and Studio service composition and supply its binary; service admission requires zero skips.");
test.setTimeout(90_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const lufs = (page: Page) => page.getByRole("textbox", { name: "Integrated loudness target (LUFS)", exact: true });
const peak = (page: Page) => page.getByRole("textbox", { name: "True peak ceiling (dBTP)", exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Audio targets unconfirmed save", exact: true });
const recipeFor = (project: Project, profileId: string) => project.production_design?.delivery_recipes?.[profileId] ?? null;
const requests = (service: ScopedChangeService, command: string) => service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === command).length;
const copies = (page: Page) => page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.delivery-recipe-draft/1:") && key.includes(',"targets"]:'))
  .map(key => ({ key, raw: localStorage.getItem(key)! })));
async function activeTargetCopy(page: Page, project: Project, profileId: string) {
  const scope = recipeDraftScope(project, profileId, "targets"), pointer = recipePointerKey(scope);
  const writer = await page.evaluate(key => sessionStorage.getItem(key), pointer);
  if (!writer) throw new Error("The real target editor has not published a writer pointer.");
  const key = recipeDraftStorageKey(scope, writer), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("The real target editor recovery copy is missing.");
  return { scope, pointer, writer, key, raw, state: (JSON.parse(raw) as { state: RecipeAuthorState }).state };
}
/** Fault only the first exact pointer read. Recovery bytes are authored by the real UI. */
async function failFirstTargetPointerRead(page: Page, targets: TargetsFixture, pointer: string) {
  const marker = "motionwright.test.targets-first-read:" + randomUUID();
  await page.addInitScript(({ pointer, marker }) => {
    const original = Storage.prototype.getItem;
    if (original.call(sessionStorage, marker) === "fired") return;
    let armed = true;
    Storage.prototype.getItem = function(key) {
      if (armed && this === sessionStorage && key === pointer) {
        armed = false; sessionStorage.setItem(marker, "fired"); throw new Error("Injected first target pointer read failure.");
      }
      return original.call(this, key);
    };
    (window as unknown as { __restoreTargetPointerRead?: () => void }).__restoreTargetPointerRead = () => { Storage.prototype.getItem = original; };
  }, { pointer, marker });
  targets.cleanups.add(async () => { if (!page.isClosed()) await page.evaluate(() => (window as unknown as { __restoreTargetPointerRead?: () => void }).__restoreTargetPointerRead?.()); });
  return marker;
}
function newProfile(name: string): DeliverableProfile {
  return { id: randomUUID(), name, width: 1920, height: 1080, language: "en", captions: true, caption_format: "web_vtt", video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000,
    brand_profile: null, cut_label: null, parent_profile_id: null, source_revision: null, framing_strategy: "replan", crop_approved: false, timing_locked: false,
    voice_track_id: null, text_overrides: {}, included_scene_ids: [], protected_scene_ids: [], burn_in_captions: false,
    frame_rate: { num: "30", den: "1" }, color_space: "rec709", container: "mp4", adaptation_notes: [] };
}
async function openTargets(page: Page, selected: DeliverableProfile) {
  await button(page, "Deliver").click(); await page.locator(".delivery-profile-row").filter({ hasText: selected.name }).click();
  await expect(page.getByRole("region", { name: "Audio targets draft review", exact: true })).toBeVisible();
}
async function openMix(page: Page, selected: DeliverableProfile) {
  await button(page, "Audio").click(); const panel = page.locator('details[aria-label="Original three-bus native PCM audition"]');
  if (await panel.getAttribute("open") === null) await panel.locator("summary").first().click();
  await panel.getByRole("combobox", { name: "Delivery language / profile", exact: true }).selectOption(selected.id);
}
async function revision(page: Page, expected: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + expected);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done()))));
}
async function save(page: Page, targets: TargetsFixture, label = "Save audio targets") {
  const before = await targets.service.project(targets.initial.id); await button(page, label).click(); await revision(page, before.revision + 1);
  return targets.service.project(before.id);
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; }
async function bounded<T>(promise: Promise<T>, label: string, milliseconds = 15_000): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([promise, new Promise<never>((_resolve, reject) => { timer = setTimeout(() => reject(new Error("Timed out waiting for " + label)), milliseconds); })]); }
  finally { if (timer) clearTimeout(timer); }
}
function hold(targets: TargetsFixture, phase: "before" | "after", loseReply = false) {
  const { service } = targets, original = service.invoke.bind(service), entered = deferred<Extract<Change, { type: "set_delivery_recipe" }>>(), release = deferred<void>(), result = deferred<Project | Error>(), done = deferred<void>();
  let armed = true, started = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const change = (args.request as { change?: Change } | undefined)?.change;
    if (!armed || command !== "apply_change" || change?.type !== "set_delivery_recipe") return original<T>(command, args);
    armed = false; started = true; entered.resolve(structuredClone(change));
    try {
      if (phase === "before") await bounded(release.promise, "target request release", 20_000);
      try {
        const reply = await original<T>(command, args); result.resolve(reply as Project);
        if (phase === "after") await bounded(release.promise, "target reply release", 20_000);
        if (loseReply) throw new Error("Synthetic transport loss after a real target recipe commit");
        return reply;
      } catch (error) { result.resolve(error instanceof Error ? error : new Error(String(error))); throw error; }
    } finally { done.resolve(); }
  };
  const restore = async () => { release.resolve(); service.invoke = original; targets.cleanups.delete(restore); if (started) await bounded(done.promise, "target transport drain"); };
  targets.cleanups.add(restore);
  return { get entered() { return bounded(entered.promise, "target invoke entry"); }, get result() { return bounded(result.promise, "target original result"); }, release: () => release.resolve(), restore };
}

test("targets survive a failed first pointer read, profile changes, Film/mix saves, SQLite restart and actual UI reopen", async ({ page, targets }, info) => {
  const { service, initial, a, b } = targets, originalA = recipeFor(initial, a.id)!;
  await lufs(page).fill("  -023.0010  "); await peak(page).fill("-1.125");
  const originalCopy = await activeTargetCopy(page, initial, a.id), writes = requests(service, "apply_change"), grants = requests(service, "issue_effect_grant");
  const marker = await failFirstTargetPointerRead(page, targets, originalCopy.pointer);
  await page.reload(); await openTargets(page, a);
  await expect(page.getByRole("region", { name: "Audio targets draft review", exact: true }).getByRole("alert")).toContainText("first target pointer read failure");
  expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
  await expect(lufs(page)).toHaveValue(""); await expect(button(page, "Save audio targets")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  await button(page, "Retry audio targets recovery copy").click(); await expect(lufs(page)).toHaveValue("  -023.0010  ");
  const recoveredCopy = await activeTargetCopy(page, initial, a.id);
  expect(recoveredCopy.writer).not.toBe(originalCopy.writer); expect(recoveredCopy.state).toEqual(originalCopy.state);
  await page.reload(); await openTargets(page, a); await expect(lufs(page)).toHaveValue("  -023.0010  "); await expect(peak(page)).toHaveValue("-1.125");
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  expect(requests(service, "apply_change")).toBe(writes); expect(requests(service, "issue_effect_grant")).toBe(grants); expect(await service.project(initial.id)).toEqual(initial);
  let saved = await save(page, targets);
  const expectedA = { ...originalA, audio_targets: { target_lufs: -23.001, target_true_peak_dbtp: -1.125 } };
  expect(recipeFor(saved, a.id)).toEqual(expectedA); expect(saved.audio.mix).toEqual(initial.audio.mix); expect(saved.deliverables).toEqual(initial.deliverables);
  await openTargets(page, b); await expect(lufs(page)).toHaveValue(""); await peak(page).fill("-2.5"); saved = await save(page, targets);
  const savedB = recipeFor(saved, b.id)!; expect(savedB.audio_targets).toEqual({ target_lufs: null, target_true_peak_dbtp: -2.5 }); expect(recipeFor(saved, a.id)).toEqual(expectedA);
  await openTargets(page, a); await page.getByRole("combobox", { name: initial.scenes[0].name + " narrative role", exact: true }).selectOption("payoff"); saved = await save(page, targets, "Save scene intent");
  expect(recipeFor(saved, a.id)!.audio_targets).toEqual(expectedA.audio_targets); expect(recipeFor(saved, a.id)!.original_mix).toEqual(originalA.original_mix);
  await openMix(page, a); await page.getByRole("textbox", { name: "SFX gain dB", exact: true }).fill("-11"); saved = await save(page, targets, "Save original mix recipe");
  expect(recipeFor(saved, a.id)!.audio_targets).toEqual(expectedA.audio_targets); expect(recipeFor(saved, a.id)!.original_mix).toEqual({ ...originalA.original_mix, sfx_gain_db: -11 }); expect(recipeFor(saved, b.id)).toEqual(savedB);
  const journal = await service.history(initial.id, initial.revision); expect(journal.map(event => event.change.type)).toEqual(Array(4).fill("set_delivery_recipe"));
  await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(saved); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
  await page.reload(); await openTargets(page, a); await revision(page, saved.revision); await expect(lufs(page)).toHaveValue("-23.001"); await expect(peak(page)).toHaveValue("-1.125");
  await openTargets(page, b); await expect(lufs(page)).toHaveValue(""); await expect(peak(page)).toHaveValue("-2.5"); expect(await service.project(initial.id)).toEqual(saved);
  await info.attach("real-service-delivery-targets-reopened", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
});

test("targets-only UI save remains non-null, partial clear writes explicit null, and explicit clear removes the last target", async ({ page, targets }) => {
  const { service, initial, a } = targets;
  const baseline = await service.apply(initial, { type: "set_delivery_recipe", deliverable_profile_id: a.id, recipe: null }); await page.reload(); await openTargets(page, a);
  await lufs(page).fill("-14"); await peak(page).fill("-1"); let saved = await save(page, targets);
  expect(recipeFor(saved, a.id)).toEqual({ schema: "motionwright.delivery-recipe/1", scene_intents: [], original_mix: null, audio_targets: { target_lufs: -14, target_true_peak_dbtp: -1 } });
  expect(await service.reopen(initial.id)).toEqual(saved); await page.reload(); await openTargets(page, a); await expect(lufs(page)).toHaveValue("-14");
  await lufs(page).fill(""); saved = await save(page, targets); expect(recipeFor(saved, a.id)!.audio_targets).toEqual({ target_lufs: null, target_true_peak_dbtp: -1 });
  const beforeClear = saved; await button(page, "Clear saved audio targets").click(); expect(await service.project(initial.id)).toEqual(beforeClear);
  saved = await save(page, targets); expect(recipeFor(saved, a.id)).toBeNull(); expect(saved.deliverables).toEqual(baseline.deliverables);
  expect((await service.history(initial.id, baseline.revision)).map(event => event.change.type)).toEqual(Array(3).fill("set_delivery_recipe"));
  expect(await service.reopen(initial.id)).toEqual(saved); await page.reload(); await openTargets(page, a); await expect(lufs(page)).toHaveValue(""); await expect(peak(page)).toHaveValue("");
});

test("a failed first read with no earlier pointer preserves incomplete raw through Retry and reload before one explicit correction commits", async ({ page, targets }) => {
  const { service, initial, a, b } = targets, applies = requests(service, "apply_change"), grants = requests(service, "issue_effect_grant");
  const pointer = recipePointerKey(recipeDraftScope(initial, a.id, "targets"));
  expect(await page.evaluate(key => sessionStorage.getItem(key), pointer)).toBeNull();
  const marker = await failFirstTargetPointerRead(page, targets, pointer);
  await page.reload(); await openTargets(page, a);
  await expect(page.getByRole("region", { name: "Audio targets draft review", exact: true }).getByRole("alert")).toContainText("first target pointer read failure");
  expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
  await lufs(page).fill("  1e  "); await peak(page).fill("-1.125"); await expect(button(page, "Save audio targets")).toBeDisabled();
  expect(await page.evaluate(key => sessionStorage.getItem(key), pointer)).toBeNull();
  await button(page, "Retry audio targets recovery copy").click();
  const retained = await activeTargetCopy(page, initial, a.id); expect(retained.state.input.edits.target_lufs.value).toBe("  1e  ");
  expect(retained.state.input.edits.target_true_peak_dbtp.value).toBe("-1.125"); expect(retained.state.pending).toBeNull();
  expect(requests(service, "apply_change")).toBe(applies); expect(requests(service, "issue_effect_grant")).toBe(grants); expect(await service.project(initial.id)).toEqual(initial);
  await openTargets(page, b); await expect(lufs(page)).toHaveValue(""); await openTargets(page, a); await expect(lufs(page)).toHaveValue("  1e  ");
  await button(page, "Timeline").click(); await openTargets(page, a); await expect(lufs(page)).toHaveValue("  1e  ");
  await page.reload(); await openTargets(page, a); await expect(lufs(page)).toHaveValue("  1e  "); await expect(peak(page)).toHaveValue("-1.125");
  await lufs(page).fill("-23.00000000000000000001"); await expect(button(page, "Save audio targets")).toBeDisabled();
  expect(requests(service, "apply_change")).toBe(applies); expect(requests(service, "issue_effect_grant")).toBe(grants); expect(await service.project(initial.id)).toEqual(initial);
  expect(await service.history(initial.id, initial.revision)).toEqual([]); await lufs(page).fill("-23.001"); const saved = await save(page, targets);
  expect(recipeFor(saved, a.id)).toEqual({ ...recipeFor(initial, a.id)!, audio_targets: { target_lufs: -23.001, target_true_peak_dbtp: -1.125 } });
  expect(requests(service, "apply_change")).toBe(applies + 1); expect(requests(service, "issue_effect_grant")).toBe(grants + 1);
});

test("a lost real target reply retains UNKNOWN and later clear/ABA raw through a failed pointer read, reload and current-history review", async ({ page, targets }) => {
  const { service, initial, a } = targets; await lufs(page).fill("-14"); const gate = hold(targets, "after", true);
  try {
    await button(page, "Save audio targets").click(); const actual = await gate.result; expect(actual).not.toBeInstanceOf(Error);
    await lufs(page).fill("  1e  "); await peak(page).fill("-2.125"); gate.release(); await expect(unknown(page)).toBeVisible();
    await expect(page.getByRole("region", { name: "Audio targets draft review", exact: true }).getByRole("alert")).toContainText("Synthetic transport loss after a real target recipe commit");
    const count = requests(service, "apply_change"), grants = requests(service, "issue_effect_grant"), originalCopy = await activeTargetCopy(page, initial, a.id);
    expect((await copies(page)).filter(copy => copy.key.startsWith("motionwright.delivery-recipe-draft/1:" + originalCopy.scope + ":")))
      .toEqual([{ key: originalCopy.key, raw: originalCopy.raw }]);
    const marker = await failFirstTargetPointerRead(page, targets, originalCopy.pointer);
    await page.reload(); await openTargets(page, a); await revision(page, initial.revision + 1);
    await expect(page.getByRole("region", { name: "Audio targets draft review", exact: true }).getByRole("alert")).toContainText("first target pointer read failure");
    expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
    await expect(button(page, "Save audio targets")).toBeDisabled(); await expect(button(page, "Clear saved audio targets")).toBeDisabled();
    const recoveryCopies = page.locator('details[aria-label="Audio targets recovery copies"]'); await recoveryCopies.locator("summary").click();
    await recoveryCopies.getByRole("button", { name: "Discard audio targets copy 1", exact: true }).click();
    expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
    await peak(page).fill("-3"); await peak(page).fill("");
    expect(requests(service, "apply_change")).toBe(count); expect(requests(service, "issue_effect_grant")).toBe(grants);
    await button(page, "Retry audio targets recovery copy").click();
    await expect(unknown(page)).toBeVisible(); await expect(lufs(page)).toHaveValue("  1e  "); await expect(peak(page)).toHaveValue("");
    const recoveredCopy = await activeTargetCopy(page, initial, a.id);
    expect(recoveredCopy.writer).not.toBe(originalCopy.writer); expect(recoveredCopy.state.pending).toEqual(originalCopy.state.pending);
    expect(recoveredCopy.state.input.awaiting).toEqual(originalCopy.state.input.awaiting); expect(recoveredCopy.state.input.edits.target_true_peak_dbtp.value).toBe("");
    await page.reload(); await openTargets(page, a); await expect(unknown(page)).toBeVisible(); await expect(lufs(page)).toHaveValue("  1e  "); await expect(peak(page)).toHaveValue("");
    await expect(button(page, "Save audio targets")).toBeDisabled();
    expect((await activeTargetCopy(page, initial, a.id)).state).toEqual(recoveredCopy.state);
    const retained = (await copies(page)).filter(copy => JSON.parse(copy.raw).state.pending?.outcome === "UNKNOWN"); expect(retained.length).toBeGreaterThan(0);
    await unknown(page).getByRole("button", { name: "Read saved recipe and history", exact: true }).click(); await expect(unknown(page)).toContainText("Matching recipe values appear at r" + (initial.revision + 1));
    await button(page, "Keep earlier recipe attempt and continue editing").click(); await expect(unknown(page)).toHaveCount(0);
    for (const copy of retained) expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
    // The late invalid LUFS is still an overlap with the saved -14. The user
    // must choose the current base explicitly before a separate valid save.
    await button(page, "Keep my draft Integrated loudness target (LUFS)").click(); await expect(lufs(page)).toHaveValue("  1e  ");
    expect(requests(service, "apply_change")).toBe(count); expect(requests(service, "issue_effect_grant")).toBe(grants);
    await lufs(page).fill("-16"); await peak(page).fill("-2.125"); const saved = await save(page, targets); expect(recipeFor(saved, a.id)!.audio_targets).toEqual({ target_lufs: -16, target_true_peak_dbtp: -2.125 });
    expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
    expect((await service.history(initial.id, initial.revision)).map(event => event.change.type)).toEqual(["set_delivery_recipe", "set_delivery_recipe"]); expect(await service.reopen(initial.id)).toEqual(saved);
  } finally { await gate.restore(); }
});

test("another real writer's equal r+1 cannot acknowledge the rejected original or resend it", async ({ page, targets }) => {
  const { service, initial, a, peers } = targets; await lufs(page).fill("-14"); const gate = hold(targets, "before");
  try {
    await button(page, "Save audio targets").click(); const change = await gate.entered; const peer = await service.peer(); peers.push(peer);
    const external = await peer.apply(initial, change); gate.release(); expect(await gate.result).toBeInstanceOf(Error); await expect(unknown(page)).toBeVisible();
    await page.reload(); await openTargets(page, a); await revision(page, external.revision); await expect(unknown(page)).toBeVisible(); await expect(lufs(page)).toHaveValue("-14"); await expect(button(page, "Save audio targets")).toBeDisabled();
    const count = requests(service, "apply_change"), grants = requests(service, "issue_effect_grant"); await unknown(page).getByRole("button", { name: "Read saved recipe and history", exact: true }).click();
    await expect(unknown(page)).toContainText("Matching recipe values appear"); await button(page, "Keep earlier recipe attempt and continue editing").click();
    expect(await service.project(initial.id)).toEqual(external); expect(requests(service, "apply_change")).toBe(count); expect(requests(service, "issue_effect_grant")).toBe(grants);
    await lufs(page).fill("-16"); const saved = await save(page, targets); expect(saved.revision).toBe(initial.revision + 2);
    expect(recipeFor(saved, a.id)!.audio_targets).toEqual({ target_lufs: -16, target_true_peak_dbtp: null }); expect((await service.history(initial.id, initial.revision)).map(event => event.change.type)).toEqual(["set_delivery_recipe", "set_delivery_recipe"]);
  } finally { await gate.restore(); }
});

test("silent loss of the armed recovery copy sends no real request, and verified Retry permits only an explicit save", async ({ page, targets }) => {
  const { service, initial, a } = targets; await lufs(page).fill("-14"); const count = requests(service, "apply_change"), grants = requests(service, "issue_effect_grant");
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (this === localStorage && key.startsWith("motionwright.delivery-recipe-draft/1:") && key.includes(',"targets"]:') && JSON.parse(value).state?.pending) return;
      return original.call(this, key, value);
    };
    (window as unknown as { __restoreTargetReservation(): void }).__restoreTargetReservation = () => { Storage.prototype.setItem = original; };
  });
  await button(page, "Save audio targets").click(); await expect(page.getByRole("region", { name: "Audio targets draft review", exact: true })).toContainText("NOT_SENT");
  expect(requests(service, "apply_change")).toBe(count); expect(requests(service, "issue_effect_grant")).toBe(grants); expect(await service.project(initial.id)).toEqual(initial);
  await page.evaluate(() => (window as unknown as { __restoreTargetReservation(): void }).__restoreTargetReservation()); await button(page, "Retry audio targets recovery copy").click();
  expect(requests(service, "apply_change")).toBe(count); const saved = await save(page, targets); expect(recipeFor(saved, a.id)!.audio_targets).toEqual({ target_lufs: -14, target_true_peak_dbtp: null });
  expect(requests(service, "apply_change")).toBe(count + 1); expect(requests(service, "issue_effect_grant")).toBe(grants + 1);
});

test("the exact original real response preserves later B to A to B raw across a workspace remount", async ({ page, targets }) => {
  const { service, initial, a } = targets, raw = "  -014.000  "; await lufs(page).fill(raw); const gate = hold(targets, "after");
  try {
    await button(page, "Save audio targets").click(); expect(await gate.result).not.toBeInstanceOf(Error);
    await button(page, "Timeline").click(); await openTargets(page, a); await lufs(page).fill(""); await lufs(page).fill(raw); gate.release();
    await revision(page, initial.revision + 1); await expect(lufs(page)).toHaveValue(raw); await expect(unknown(page)).toHaveCount(0); await expect(button(page, "Save audio targets")).toBeDisabled();
    const count = requests(service, "apply_change"), saved = await service.project(initial.id); expect(recipeFor(saved, a.id)!.audio_targets).toEqual({ target_lufs: -14, target_true_peak_dbtp: null });
    await page.reload(); await openTargets(page, a); await expect(lufs(page)).toHaveValue(raw); expect(requests(service, "apply_change")).toBe(count);
    const pending = page.locator('details[aria-label="Audio targets pending fields"]'); await pending.locator("summary").click(); await pending.getByRole("button", { name: "Keep saved Integrated loudness target (LUFS)", exact: true }).click();
    await expect(lufs(page)).toHaveValue("-14"); expect(await service.project(initial.id)).toEqual(saved); expect(await service.reopen(initial.id)).toEqual(saved);
  } finally { await gate.restore(); }
});
