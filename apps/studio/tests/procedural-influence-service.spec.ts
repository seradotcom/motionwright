import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import type { ProceduralConfig } from "../src/proceduralField";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> grants -> source-built StudioService -> SQLite. The fault cases
// change only reply delivery or browser-local backup. No Project is fabricated.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Requires the exact influence-capable source-built service; no simulated persistence fallback.");
test.setTimeout(60_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const baselineConfig: ProceduralConfig = {
  seed: 41, count: 3, columns: 3, distribution: "grid", shape: "square", origin_x: 300, origin_y: 300,
  area_width: 600, area_height: 200, size: 32, opacity_percent: 100, fill: "#aabbcc",
  reveal_step_frames: 0, reveal_duration_frames: 12,
};
const influence = { center_x: 600, center_y: 400, radius: 150, target_size: 64 };
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
function requests(service: ScopedChangeService, command: string) {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === command);
}
function applies(service: ScopedChangeService): ApplyArgs[] {
  return requests(service, "apply_change").map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function holdReply(service: ScopedChangeService, lost: boolean) {
  const original: ScopedChangeService["invoke"] = service.invoke.bind(service), delivered = deferred<void>(), observed = deferred<Project>(), finished = deferred<void>();
  void observed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "upsert_procedural_field") return original<T>(command, args);
    armed = false; matched = true;
    try {
      const saved = await original<Project>(command, args); observed.resolve(saved); await delivered.promise;
      if (lost) throw new Error("Influence original response lost after the actual SQLite commit.");
      return saved as T;
    } catch (reason) { observed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return { observed: observed.promise, release: () => delivered.resolve(), async dispose() {
    service.invoke = original; delivered.resolve(); if (matched) await finished.promise;
  } };
}
type Failure = { reason: unknown };
const detail = (reason: unknown) => reason instanceof Error ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
async function finish(page: Page, info: TestInfo, service: ScopedChangeService, final: Project | undefined,
  observations: Record<string, unknown>, preparations: (() => Promise<unknown>)[], primary?: Failure) {
  const failures: unknown[] = primary ? [primary.reason] : [];
  let custody: StoreFinalization | undefined;
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [...preparations, () => page.close()]); }
  catch (reason) { failures.push(reason); }
  try {
    const path = info.outputPath("procedural-influence-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "actual App, effect grants, source-built influence service, SQLite and original closed-store oracle",
      exclusions: ["installed Tauri", "native renderer pixels", "human craft acceptance", "catalog admission"],
      observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [{ databasePath: service.databasePath, binaryPath: service.binaryPath,
        transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr }],
    }, null, 2));
    await info.attach("procedural-influence-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Influence scenario and finalization retained separate failures", { cause: primary?.reason });
}
async function setup(info: TestInfo) {
  return ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
}
async function unlocked(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const lock of [...project.locks]) project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  return project;
}
function sceneId(project: Project) {
  const scene = project.scenes.find(value => value.renderer === "motion-canvas" && BigInt(value.duration.num) >= 2n * BigInt(value.duration.den));
  if (!scene) throw new Error("No actual eligible service fixture scene");
  return scene.id;
}
async function open(page: Page, id: string) {
  await button(page, "Production").click(); await button(page, "Procedural").click();
  await page.getByLabel("Production working scene", { exact: true }).selectOption(id);
  await expect(page.getByLabel("Enable influence field", { exact: true })).toBeVisible();
}
async function attach(page: Page, service: ScopedChangeService, project: Project) {
  await service.attachFixturePage(page, project.id); await page.goto("/"); await open(page, sceneId(project));
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function configureBase(page: Page) {
  await page.getByLabel("Procedural distribution", { exact: true }).selectOption("grid");
  for (const [label, value] of [
    ["Number of objects", "3"], ["Columns", "3"], ["Stage X", "300"], ["Stage Y", "300"],
    ["Field width", "600"], ["Field height", "200"], ["Object size", "32"], ["Integer seed", "41"],
  ]) await page.getByLabel(label, { exact: true }).fill(value);
  await page.getByLabel("Procedural fill", { exact: true }).fill("#aabbcc");
}
async function configureInfluence(page: Page) {
  await page.getByLabel("Enable influence field", { exact: true }).check();
  for (const [label, value] of [["Influence center X", "600"], ["Influence center Y", "400"], ["Influence radius", "150"], ["Influence target size", "64"]])
    await page.getByLabel(label, { exact: true }).fill(value);
}
async function copies(page: Page) {
  return page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith("motionwright.creative-workbench-recovery.v1:")));
}
function preserved(actual: Project, initial: Project, target: string) {
  expect(actual.scenes.filter(value => value.id !== target)).toEqual(initial.scenes.filter(value => value.id !== target));
  const initialScene = initial.scenes.find(value => value.id === target)!;
  expect(actual.scenes.find(value => value.id === target)!.nodes.filter(value => initialScene.nodes.some(node => node.id === value.id))).toEqual(initialScene.nodes);
  for (const key of ["id", "generation", "active_branch", "title", "brief", "narrative", "audio", "assets", "markers", "locks",
    "deliverables", "visual_language", "proposal_sets", "model_invocations", "extensions", "handoffs"] as const) expect(actual[key]).toEqual(initial[key]);
  expect(actual.production_design?.heroes).toEqual(initial.production_design?.heroes);
}

test("influence raw input survives navigation and reload, and disabling preserves its unfinished parameters", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await unlocked(service), target = sceneId(initial); observations.initial = initial; await attach(page, service, initial);
    await configureBase(page); await configureInfluence(page);
    const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
    await page.getByLabel("Influence radius", { exact: true }).fill("1e");
    await expect(button(page, "Add procedural field")).toBeDisabled();
    await button(page, "Timeline").click(); await open(page, target);
    await expect(page.getByLabel("Influence radius", { exact: true })).toHaveValue("1e");
    await page.reload(); await open(page, target);
    await expect(page.getByLabel("Influence radius", { exact: true })).toHaveValue("1e");
    await expect(button(page, "Add procedural field")).toBeDisabled();
    expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
    expect(await service.project(initial.id)).toEqual(initial);
    await page.getByLabel("Enable influence field", { exact: true }).uncheck();
    await button(page, "Add procedural field").click(); await revision(page, initial.revision + 1);
    final = await service.project(initial.id);
    const field = final.production_design!.procedural_fields![0];
    expect(field.generator_version).toBe(1); expect(field.config).not.toHaveProperty("influence");
    await page.reload(); await open(page, target);
    await expect(page.getByLabel("Enable influence field", { exact: true })).not.toBeChecked();
    await expect(page.getByLabel("Influence radius", { exact: true })).toHaveValue("1e");
    await page.getByLabel("Enable influence field", { exact: true }).check();
    await expect(button(page, "Update procedural field")).toBeDisabled();
    expect(applies(service)).toHaveLength(writes + 1); preserved(final, initial, target);
    observations.retainedCopies = await copies(page); observations.journal = await service.history(initial.id, initial.revision);
    await page.screenshot({ path: info.outputPath("influence-unfinished-input-retained.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("saved and draft review shows actual influence geometry while preserving IDs and human overrides through detach", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await unlocked(service), target = sceneId(initial); observations.initial = initial; await attach(page, service, initial);
    await configureBase(page); await button(page, "Add procedural field").click(); await revision(page, initial.revision + 1);
    const added = await service.project(initial.id), field = added.production_design!.procedural_fields![0], middle = field.baseline[1];
    expect(field.generator_version).toBe(1);
    const human = await service.apply(added, { type: "update_canvas_style", scene_id: target, node_id: middle.id, style: { ...middle.style, fill: "#eebb44" } });
    await page.reload(); await open(page, target); await configureInfluence(page);
    const modes = page.getByRole("group", { name: "Procedural preview source", exact: true });
    const shape = page.locator(".production-study").locator('g[data-canvas-node-id="' + middle.id + '"] > rect');
    await modes.getByRole("button", { name: "Draft", exact: true }).click();
    await expect(shape).toHaveAttribute("width", "64"); await expect(shape).toHaveAttribute("x", "568"); await expect(shape).toHaveAttribute("y", "368");
    await expect(shape).toHaveAttribute("fill", "#eebb44");
    await page.getByLabel("Procedural preview playhead", { exact: true }).press("Home"); await expect(shape).toHaveAttribute("width", "64");
    await page.getByLabel("Procedural preview playhead", { exact: true }).press("End"); await expect(shape).toHaveAttribute("width", "64");
    await page.screenshot({ path: info.outputPath("influence-draft-real-objects.png"), fullPage: true });
    await modes.getByRole("button", { name: "Saved", exact: true }).click();
    await expect(shape).toHaveAttribute("width", "32"); await expect(shape).toHaveAttribute("fill", "#eebb44");
    await page.screenshot({ path: info.outputPath("influence-saved-real-objects.png"), fullPage: true });
    expect(await service.project(initial.id)).toEqual(human);
    await button(page, "Update procedural field").click(); await revision(page, human.revision + 1);
    const updated = await service.project(initial.id), current = updated.production_design!.procedural_fields![0];
    expect(current.id).toBe(field.id); expect(current.generator_version).toBe(2); expect(current.config.influence).toEqual(influence);
    expect(current.baseline.map(value => [value.x, value.y, value.width, value.height])).toEqual([[384, 384, 32, 32], [568, 368, 64, 64], [784, 384, 32, 32]]);
    expect(current.baseline.map(value => value.id)).toEqual(field.baseline.map(value => value.id));
    expect(updated.scenes.find(value => value.id === target)!.nodes.find(value => value.id === middle.id)!.style.fill).toBe("#eebb44");
    const objects = updated.scenes.find(value => value.id === target)!.nodes;
    await button(page, "Detach generator (keep objects)").click(); await revision(page, updated.revision + 1);
    final = await service.project(initial.id); expect(final.production_design!.procedural_fields).toEqual([]);
    expect(final.scenes.find(value => value.id === target)!.nodes).toEqual(objects); preserved(final, initial, target);
    const events = await service.history(initial.id, initial.revision);
    expect(events.map(value => value.change.type)).toEqual(["upsert_procedural_field", "update_canvas_style", "upsert_procedural_field", "detach_procedural_field"]);
    observations.journal = events; observations.saved = added; observations.human = human; observations.influenced = updated;
    await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("incompatible influence is blocked in App and rejected atomically by the actual service", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await unlocked(service), target = sceneId(initial); await attach(page, service, initial);
    await configureBase(page); await button(page, "Add procedural field").click(); await revision(page, initial.revision + 1);
    const saved = await service.project(initial.id), history = await service.history(initial.id, 0), field = saved.production_design!.procedural_fields![0];
    observations.before = saved; observations.historyBefore = history;
    const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
    await configureInfluence(page); await page.getByLabel("Field height", { exact: true }).fill("64");
    await page.getByLabel("Influence center Y", { exact: true }).fill("332");
    await page.getByLabel("Influence target size", { exact: true }).fill("128");
    await expect(button(page, "Update procedural field")).toBeDisabled();
    await expect(page.getByRole("alert").filter({ hasText: "exceeds the authored field" })).toBeVisible();
    expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
    await expect(service.apply(saved, { type: "upsert_procedural_field", instance_id: field.id, scene_id: target,
      config: { ...baselineConfig, area_height: 64, influence: { ...influence, center_y: 332, target_size: 128 } } })).rejects.toThrow();
    final = await service.project(initial.id); expect(final).toEqual(saved); expect(await service.history(initial.id, 0)).toEqual(history);
    expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
    await page.reload(); await open(page, target);
    await expect(page.getByLabel("Influence target size", { exact: true })).toHaveValue("128");
    await expect(button(page, "Update procedural field")).toBeDisabled();
    observations.historyAfter = await service.history(initial.id, 0);
    await page.screenshot({ path: info.outputPath("influence-bounds-refusal-retained.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("lost influence response retains UNKNOWN and later input until separate history review and explicit save", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const initial = await unlocked(service), target = sceneId(initial); observations.initial = initial; await attach(page, service, initial);
    await configureBase(page); await configureInfluence(page);
    const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
    hold = holdReply(service, true); await button(page, "Add procedural field").click(); const committed = await hold.observed;
    await button(page, "Timeline").click(); await open(page, target);
    await page.getByLabel("Influence radius", { exact: true }).fill("1e"); hold.release();
    const unknown = page.getByRole("region", { name: "Unconfirmed Procedural", exact: true });
    await expect(unknown).toBeVisible(); await hold.dispose();
    await expect(page.getByRole("alert").filter({ hasText: "lost after the actual SQLite commit" })).toBeVisible();
    await page.reload(); await open(page, target);
    await expect(page.getByLabel("Influence radius", { exact: true })).toHaveValue("1e"); await expect(unknown).toBeVisible();
    const originals = (await copies(page)).filter(([, raw]) => JSON.parse(raw).draft.pending?.outcome === "UNKNOWN");
    expect(originals.length).toBeGreaterThan(0);
    await button(page, "Read saved Procedural and history").click();
    await expect(page.getByLabel("Procedural outcome review", { exact: true })).toContainText("consecutive history records");
    await button(page, "Keep earlier Procedural attempt and continue editing").click(); await expect(unknown).toHaveCount(0);
    await expect(page.getByLabel("Influence radius", { exact: true })).toHaveValue("1e");
    expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
    expect(await service.project(initial.id)).toEqual(committed);
    await page.getByLabel("Influence radius", { exact: true }).fill("200");
    await button(page, "Update procedural field").click(); await revision(page, committed.revision + 1);
    final = await service.project(initial.id); expect(final.production_design!.procedural_fields![0].config.influence?.radius).toBe(200);
    expect(applies(service)).toHaveLength(writes + 2); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 2);
    const retained = new Map(await copies(page)); for (const [key, raw] of originals) expect(retained.get(key)).toBe(raw);
    observations.originalCommit = committed; observations.retainedOriginals = originals; observations.journal = await service.history(initial.id, initial.revision);
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

test("the original influence ACK preserves later raw input across actual App navigation", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const initial = await unlocked(service), target = sceneId(initial); await attach(page, service, initial);
    await configureBase(page); await configureInfluence(page); const writes = applies(service).length;
    hold = holdReply(service, false); await button(page, "Add procedural field").click(); const receipt = await hold.observed;
    await button(page, "Timeline").click(); await open(page, target);
    await page.getByLabel("Influence target size", { exact: true }).fill("1e");
    hold.release(); await hold.dispose(); await revision(page, initial.revision + 1);
    await expect(page.getByLabel("Influence target size", { exact: true })).toHaveValue("1e");
    await expect(page.getByRole("region", { name: "Unconfirmed Procedural", exact: true })).toHaveCount(0);
    await expect(button(page, "Update procedural field")).toBeDisabled();
    final = await service.project(initial.id); expect(final).toEqual(receipt); expect(final.production_design!.procedural_fields![0].config.influence).toEqual(influence);
    await page.reload(); await open(page, target); await expect(page.getByLabel("Influence target size", { exact: true })).toHaveValue("1e");
    expect(applies(service)).toHaveLength(writes + 1); observations.originalReceipt = receipt;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

for (const fault of ["data", "pointer"] as const) {
  test("influence " + fault + " backup refusal grants nothing before explicit verified retry", async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { fault };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await unlocked(service); observations.initial = initial; await attach(page, service, initial);
      const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
      // Install before the first author edit: a silent pointer write of a value
      // already present is indistinguishable from success and is not this fault.
      await page.evaluate(fault => {
        const write = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          if (fault === "data" && this === localStorage && key.startsWith("motionwright.creative-workbench-recovery.v1:")) return;
          if (fault === "pointer" && this === sessionStorage && key.startsWith("motionwright.creative-workbench-writer.v1:")) return;
          return write.call(this, key, value);
        };
        (window as unknown as { __RESTORE_INFLUENCE_STORAGE__: () => void }).__RESTORE_INFLUENCE_STORAGE__ = () => { Storage.prototype.setItem = write; };
      }, fault);
      await configureBase(page); await configureInfluence(page);
      await page.getByLabel("Influence target size", { exact: true }).fill("96");
      await expect(button(page, "Add procedural field")).toBeDisabled();
      await expect(page.getByRole("alert", { name: "Procedural backup warning", exact: true })).toBeVisible();
      expect(await service.project(initial.id)).toEqual(initial);
      expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      await page.evaluate(() => (window as unknown as { __RESTORE_INFLUENCE_STORAGE__: () => void }).__RESTORE_INFLUENCE_STORAGE__());
      await button(page, "Retry Procedural backup").click(); await button(page, "Add procedural field").click(); await revision(page, initial.revision + 1);
      final = await service.project(initial.id); expect(final.production_design!.procedural_fields![0].config.influence?.target_size).toBe(96);
      expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
      observations.retainedCopies = await copies(page);
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, final, observations, [], primary); }
  });
}
