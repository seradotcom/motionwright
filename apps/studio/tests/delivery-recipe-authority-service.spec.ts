import { randomUUID } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { defaultOriginalMix, type DeliveryRecipe } from "../src/deliveryRecipe";
import type { Change, DeliverableProfile, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Real StudioService + effect grants + SQLite + actual App components. Only reply
// scheduling and browser-storage failures are injected; no native result is made up.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build the declared StudioService source and set its exact binary path; no simulated service fallback.");
test.setTimeout(120_000);
type Group = "film" | "mix";
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const title = (group: Group) => group === "film" ? "Scene intent" : "Original mix";
const saveLabel = (group: Group) => group === "film" ? "Save scene intent" : "Save original mix recipe";
const unknown = (page: Page, group: Group) => page.getByRole("region", { name: title(group) + " unconfirmed save", exact: true });
const savedRecipe = (project: Project, profileId: string) => project.production_design!.delivery_recipes![profileId];
const requestCount = (service: ScopedChangeService, command: string) => service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === command).length;
const detail = (failure: unknown) => failure instanceof Error ? { name: failure.name, message: failure.message, stack: failure.stack } : { message: String(failure) };
interface ApplyArgs extends Record<string, unknown> { request: {
  project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change;
} }
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve };
}
function hold(service: ScopedChangeService, phase: "before" | "after") {
  const original = service.invoke.bind(service), entered = deferred<ApplyArgs>(), observed = deferred<{ project: Project } | { error: unknown }>();
  const released = deferred<void>(), finished = deferred<void>(); let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "set_delivery_recipe") return original<T>(command, args);
    armed = false; matched = true; entered.resolve(structuredClone(args) as ApplyArgs);
    try {
      if (phase === "before") await released.promise;
      let outcome: { project: Project } | { error: unknown };
      try { outcome = { project: await original<Project>(command, args) }; } catch (error) { outcome = { error }; }
      observed.resolve(outcome);
      if (phase === "after") await released.promise;
      if ("error" in outcome) throw outcome.error; return outcome.project as T;
    } finally { finished.resolve(); }
  };
  return { entered: entered.promise, observed: observed.promise, release: () => released.resolve(), async dispose() {
    service.invoke = original; released.resolve(); if (matched) await finished.promise;
  } };
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done()))));
}
async function open(page: Page, group: Group, profile: DeliverableProfile) {
  await button(page, group === "film" ? "Deliver" : "Audio").click();
  if (group === "film") await page.locator(".delivery-profile-row").filter({ hasText: profile.name }).click();
  else {
    const panel = page.locator('details[aria-label="Original three-bus native PCM audition"]');
    if (await panel.getAttribute("open") === null) await panel.locator("summary").first().click();
    await panel.getByRole("combobox", { name: "Delivery language / profile", exact: true }).selectOption(profile.id);
  }
  await expect(page.getByRole("region", { name: title(group) + " draft review", exact: true })).toBeVisible();
}
const control = (page: Page, group: Group, project: Project) => group === "film"
  ? page.getByRole("combobox", { name: project.scenes[0].name + " narrative role", exact: true })
  : page.getByRole("textbox", { name: "SFX gain dB", exact: true });
async function edit(page: Page, group: Group, project: Project, value: string) {
  if (group === "film") await control(page, group, project).selectOption(value); else await control(page, group, project).fill(value);
}
async function setup(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const scene of project.scenes) if (scene.renderer !== "motion-canvas")
    project = await service.apply(project, { type: "set_scene_renderer", scene_id: scene.id, renderer: "motion-canvas" });
  const profile: DeliverableProfile = project.deliverables[0] ?? {
    id: randomUUID(), name: "Authority recipe profile", width: 1920, height: 1080, language: "en", captions: true, caption_format: "web_vtt",
    video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000, brand_profile: null, cut_label: null, parent_profile_id: null,
    source_revision: null, framing_strategy: "replan", crop_approved: false, timing_locked: false, voice_track_id: null, text_overrides: {},
    included_scene_ids: [], protected_scene_ids: [], burn_in_captions: false, frame_rate: { num: "30", den: "1" }, color_space: "rec709", container: "mp4", adaptation_notes: [],
  };
  if (!project.deliverables.length) project = await service.apply(project, { type: "upsert_deliverable", profile });
  const recipe: DeliveryRecipe = { schema: "motionwright.delivery-recipe/1", original_mix: defaultOriginalMix(),
    scene_intents: project.scenes.map((scene, index) => ({ scene_id: scene.id, role: index === 0 ? "hook" : "problem", archetype: index === 0 ? "statement" : "diagram_build" })) };
  project = await service.apply(project, { type: "set_delivery_recipe", deliverable_profile_id: profile.id, recipe });
  return { project, profile };
}
async function attach(page: Page, service: ScopedChangeService, project: Project, group: Group, profile: DeliverableProfile) {
  await service.attachFixturePage(page, project.id); await page.goto("/"); await open(page, group, profile);
}
async function copy(page: Page, project: Project, profile: DeliverableProfile, group: Group) {
  return page.evaluate(scope => {
    const writer = sessionStorage.getItem("motionwright.delivery-recipe-writer.v2:" + scope);
    if (!writer) throw new Error("No verified recipe pointer");
    const key = "motionwright.delivery-recipe-draft/1:" + scope + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No verified recipe recovery bytes");
    return { key, raw, value: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch, profile.id, group]));
}
type Failure = { reason: unknown };
async function finish(page: Page, info: TestInfo, service: ScopedChangeService, peers: ScopedChangeService[], final: Project | undefined,
  observations: Record<string, unknown>, preparations: (() => Promise<unknown>)[], primary?: Failure) {
  const failures: unknown[] = primary ? [primary.reason] : []; let custody: StoreFinalization | undefined;
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [...preparations, () => page.close()]); }
  catch (failure) { failures.push(failure); }
  try {
    const path = info.outputPath("recipe-authority-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "real StudioService, grants, SQLite, and actual App DOM; synthetic author inputs",
      exclusions: ["installed Tauri", "native Film or PCM output", "human creative acceptance"],
      observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [service, ...peers].map(value => ({
        databasePath: value.databasePath, binaryPath: value.binaryPath, transcript: value.transcript,
        processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, stderr: value.stderr,
      })),
    }, null, 2));
    await info.attach("recipe-authority-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (failure) { failures.push(failure); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Recipe authority scenario and finalization retained separate failures", { cause: primary?.reason });
}

for (const group of ["film", "mix"] as const) test(`real ${group} peer values never acknowledge an uncertain recipe request; fresh history review precedes a separate save`, async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const peers: ScopedChangeService[] = [], observations: Record<string, unknown> = {}, preparations: (() => Promise<unknown>)[] = [];
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const { project, profile } = await setup(service); final = project;
    const peer = await service.peer(); peers.push(peer);
    await attach(page, service, project, group, profile);
    await edit(page, group, project, group === "film" ? "reveal" : "-4.5");
    const held = hold(service, "before"); preparations.push(held.dispose);
    await button(page, saveLabel(group)).click(); const request = await held.entered;
    // The peer independently writes exactly the same proposal with its own grant.
    const winner = await peer.apply(project, request.request.change); final = winner;
    held.release(); const refusal = await held.observed; await held.dispose();
    expect("error" in refusal).toBe(true);
    if ("error" in refusal) expect(String(refusal.error)).toMatch(/stale|revision|conflict/i);
    await expect(unknown(page, group)).toBeVisible();
    await page.reload(); await open(page, group, profile); await revision(page, winner.revision);
    await expect(unknown(page, group)).toBeVisible(); await expect(button(page, saveLabel(group))).toBeDisabled();
    const retained = await copy(page, winner, profile, group);
    expect(retained.value.state.pending.outcome).toBe("UNKNOWN");
    const applies = requestCount(service, "apply_change"), grants = requestCount(service, "issue_effect_grant");
    await button(page, "Read saved recipe and history").click();
    await expect(unknown(page, group)).toContainText("Matching recipe values appear at r" + winner.revision);
    await button(page, "Keep earlier recipe attempt and continue editing").click();
    await expect(unknown(page, group)).toHaveCount(0);
    expect(await page.evaluate(key => localStorage.getItem(key), retained.key)).toBe(retained.raw);
    expect(await service.project(project.id)).toEqual(winner);
    expect(requestCount(service, "apply_change")).toBe(applies); expect(requestCount(service, "issue_effect_grant")).toBe(grants);
    await edit(page, group, winner, group === "film" ? "payoff" : "-11");
    await button(page, saveLabel(group)).click(); await revision(page, winner.revision + 1);
    final = await service.project(project.id);
    expect(requestCount(service, "apply_change")).toBe(applies + 1); expect(requestCount(service, "issue_effect_grant")).toBe(grants + 1);
    expect((await service.history(project.id, project.revision)).map(event => event.change.type)).toEqual(["set_delivery_recipe", "set_delivery_recipe"]);
    const saved = savedRecipe(final, profile.id);
    if (group === "film") { expect(saved.scene_intents.find(intent => intent.scene_id === project.scenes[0].id)!.role).toBe("payoff"); expect(saved.original_mix).toEqual(savedRecipe(project, profile.id).original_mix); }
    else { expect(saved.original_mix!.sfx_gain_db).toBe(-11); expect(saved.scene_intents).toEqual(savedRecipe(project, profile.id).scene_intents); }
    await service.close(); await service.start(); expect(await service.project(project.id)).toEqual(final);
    observations.winner = winner; observations.earlierUnknown = retained; observations.originalRefusal = "error" in refusal ? detail(refusal.error) : null;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, peers, final, observations, preparations, primary); }
});

test("a real original recipe ACK preserves later raw numeric typing even when it returns to the submitted value", async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, preparations: (() => Promise<unknown>)[] = [];
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const { project, profile } = await setup(service); final = project;
    await attach(page, service, project, "mix", profile);
    const raw = "  -04.500e0  "; await edit(page, "mix", project, raw);
    const held = hold(service, "after"); preparations.push(held.dispose);
    await button(page, saveLabel("mix")).click(); const outcome = await held.observed;
    if ("error" in outcome) throw outcome.error; final = outcome.project;
    await edit(page, "mix", project, "-6"); await edit(page, "mix", project, raw);
    held.release(); await held.dispose(); await revision(page, final.revision);
    await expect(control(page, "mix", project)).toHaveValue(raw); await expect(unknown(page, "mix")).toHaveCount(0);
    await expect(button(page, saveLabel("mix"))).toBeDisabled();
    const retained = await copy(page, final, profile, "mix");
    expect(retained.value.state.pending).toBeNull(); expect(retained.value.state.input.edits.sfx_gain_db.value).toBe(raw);
    expect(savedRecipe(final, profile.id).original_mix!.sfx_gain_db).toBe(-4.5);
    await button(page, "Timeline").click(); await open(page, "mix", profile);
    await expect(control(page, "mix", project)).toHaveValue(raw);
    await page.reload(); await open(page, "mix", profile); await expect(control(page, "mix", project)).toHaveValue(raw);
    await edit(page, "mix", final, "-11"); await button(page, saveLabel("mix")).click(); await revision(page, final.revision + 1);
    final = await service.project(project.id); expect(savedRecipe(final, profile.id).original_mix!.sfx_gain_db).toBe(-11);
    expect((await service.history(project.id, project.revision)).map(event => event.change.type)).toEqual(["set_delivery_recipe", "set_delivery_recipe"]);
    expect(savedRecipe(final, profile.id).scene_intents).toEqual(savedRecipe(project, profile.id).scene_intents);
    await service.close(); await service.start(); expect(await service.project(project.id)).toEqual(final);
    observations.retainedLaterInput = retained;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, preparations, primary); }
});

for (const area of ["data", "pointer"] as const) test(`silent recipe ${area} recovery failure prevents a real grant or SQLite write; verified retry saves once`, async ({ page }, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  try {
    const { project, profile } = await setup(service); final = project;
    await attach(page, service, project, "mix", profile);
    const grants = requestCount(service, "issue_effect_grant"), applies = requestCount(service, "apply_change");
    await page.evaluate(area => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (area === "data" && this === localStorage && key.startsWith("motionwright.delivery-recipe-draft/1:")) return;
        if (area === "pointer" && this === sessionStorage && key.startsWith("motionwright.delivery-recipe-writer.v2:")) return;
        return original.call(this, key, value);
      };
      (window as unknown as { __restoreRecipeStorage: () => void }).__restoreRecipeStorage = () => { Storage.prototype.setItem = original; };
    }, area);
    const raw = "  -06.750e0  "; await edit(page, "mix", project, raw);
    await expect(button(page, saveLabel("mix"))).toBeDisabled();
    await expect(page.getByRole("region", { name: "Original mix draft review", exact: true }).getByRole("alert")).toContainText("not confirmed");
    await button(page, "Timeline").click(); await open(page, "mix", profile); await expect(control(page, "mix", project)).toHaveValue(raw);
    expect(await service.project(project.id)).toEqual(project); expect(await service.history(project.id, project.revision)).toEqual([]);
    expect(requestCount(service, "issue_effect_grant")).toBe(grants); expect(requestCount(service, "apply_change")).toBe(applies);
    await page.evaluate(() => (window as unknown as { __restoreRecipeStorage: () => void }).__restoreRecipeStorage());
    await button(page, "Retry original mix recovery copy").click();
    await button(page, saveLabel("mix")).click(); await revision(page, project.revision + 1);
    final = await service.project(project.id); expect(savedRecipe(final, profile.id).original_mix!.sfx_gain_db).toBe(-6.75);
    expect(requestCount(service, "issue_effect_grant")).toBe(grants + 1); expect(requestCount(service, "apply_change")).toBe(applies + 1);
    await service.close(); await service.start(); expect(await service.project(project.id)).toEqual(final);
    observations.noGrantBeforeReadback = true; observations.raw = raw;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});
