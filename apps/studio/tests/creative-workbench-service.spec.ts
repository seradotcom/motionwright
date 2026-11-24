import { mkdir, writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App, source-built StudioService, effect grants and SQLite. Faults alter
// only transport reply delivery or local browser recovery; no saved Project is fabricated.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built StudioService runner is required; no simulated persistence fallback.");
test.setTimeout(120_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
type Kind = "stage" | "procedural" | "measured";
const labels = { stage: "ProductStage", procedural: "Procedural", measured: "Measured data" };
const saves = { stage: "Add ProductStage to scene", procedural: "Add procedural field", measured: "Register measured source" };
const fields = { stage: "ProductStage heading", procedural: "Procedural fill", measured: "Measured dataset label" };
const changeTypes = { stage: "upsert_product_stage", procedural: "upsert_procedural_field", measured: "upsert_measured_table" } as const;
const sourceRows = (milli: number) => JSON.stringify([{ key: "latency", label: "Observed latency", milli, unit: "milliseconds" }]);
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openWorkbench(page: Page, kind: Kind) {
  await button(page, "Production").click(); await button(page, labels[kind]).click();
  await expect(page.getByLabel(fields[kind], { exact: true })).toBeVisible();
}
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
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
/** Hold only delivery of one actual apply_change result. */
function holdReply(service: ScopedChangeService, type: Change["type"]) {
  const original = service.invoke.bind(service), release = deferred<void>(), observed = deferred<Project>(), finished = deferred<void>();
  // A real service refusal must reach the scenario/finalizer rather than leave
  // its receipt waiter unresolved until Playwright abandons the test.
  void observed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== type) return original<T>(command, args);
    armed = false; matched = true;
    try {
      const saved = await original<Project>(command, args);
      observed.resolve(saved); await release.promise;
      return saved as T;
    } catch (reason) { observed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return { observed: observed.promise, release: () => release.resolve(), async dispose() {
    service.invoke = original; release.resolve(); if (matched) await finished.promise;
  } };
}
type Failure = { reason: unknown };
const detail = (reason: unknown) => reason instanceof Error ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
async function finish(page: Page, info: TestInfo, service: ScopedChangeService, peers: ScopedChangeService[], final: Project | undefined,
  observations: Record<string, unknown>, preparations: (() => Promise<unknown>)[], primary?: Failure) {
  const failures: unknown[] = primary ? [primary.reason] : [];
  let custody: StoreFinalization | undefined;
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [...preparations, () => page.close()]); }
  catch (reason) { failures.push(reason); }
  try {
    const path = info.outputPath("creative-workbench-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "synthetic authoring scenario against real StudioService + grants + SQLite + App DOM",
      exclusions: ["installed Tauri", "native renderer pixels", "human acceptance"], observations, final,
      canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [service, ...peers].map(value => ({
        databasePath: value.databasePath, binaryPath: value.binaryPath, transcript: value.transcript,
        processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, stderr: value.stderr,
      })),
    }, null, 2));
    await info.attach("creative-workbench-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Creative workbench scenario and store finalization retained separate failures", { cause: primary?.reason });
}
async function setup(info: TestInfo) {
  return ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
}
async function unlocked(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const lock of [...project.locks]) project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  return project;
}
function workbenchScene(project: Project, kind: Kind) {
  // The real service fixture already binds a Hero to its first scene. Stage
  // authoring uses a separate eligible scene; retain that Hero and its objects.
  const scene = project.scenes.find(value => value.renderer === "motion-canvas" && (kind !== "stage" ||
    (BigInt(value.duration.num) >= 2n * BigInt(value.duration.den) &&
      !project.production_design?.heroes.some(hero => hero.scene_id === value.id))));
  if (!scene) throw new Error("No eligible " + kind + " scene exists in the actual service fixture.");
  return scene;
}
function expectOriginalStageWork(actual: Project, initial: Project) {
  const target = workbenchScene(initial, "stage");
  expect(actual.production_design?.heroes).toEqual(initial.production_design?.heroes);
  expect(actual.scenes.filter(scene => scene.id !== target.id)).toEqual(initial.scenes.filter(scene => scene.id !== target.id));
  expect(actual.scenes.find(scene => scene.id === target.id)?.nodes.filter(node => target.nodes.some(original => original.id === node.id))).toEqual(target.nodes);
}
async function attach(page: Page, service: ScopedChangeService, project: Project, kind: Kind) {
  await service.attachFixturePage(page, project.id); await page.goto("/"); await openWorkbench(page, kind);
  await page.getByLabel("Production working scene", { exact: true }).selectOption(workbenchScene(project, kind).id);
}
async function sourceAsset(service: ScopedChangeService, info: TestInfo, project: Project, milli = 12500) {
  const directory = info.outputPath("authored-sources"); await mkdir(directory, { recursive: true });
  const path = info.outputPath("authored-sources", "observed-" + milli + ".json"); await writeFile(path, sourceRows(milli));
  const saved = await service.importAsset(project, path, "Observed fixture source " + milli + ".json", "application/json");
  const asset = saved.assets.find(value => !project.assets.some(previous => previous.id === value.id));
  if (!asset || !asset.content_sha256) throw new Error("The actual import receipt did not add a content-addressed source.");
  return { project: saved, asset, path, bytes: sourceRows(milli) };
}
async function fill(page: Page, kind: Kind, project: Project, milli = 12500) {
  if (kind === "stage") { await page.getByLabel(fields.stage).fill("Clear workflow"); await page.getByLabel("ProductStage rights attestation").fill("Original interface authored only for this controlled service test."); }
  if (kind === "procedural") await page.getByLabel(fields.procedural).fill("#aabbcc");
  if (kind === "measured") {
    const asset = project.assets.find(value => value.name === "Observed fixture source " + milli + ".json");
    if (!asset) throw new Error("Missing actually imported source.");
    await page.getByLabel(fields.measured).fill("Observed source " + milli);
    await page.getByLabel("Measured source asset").selectOption(asset.id);
    await page.getByLabel("Measured source file verification").setInputFiles({ name: "source.json", mimeType: "application/json", buffer: Buffer.from(sourceRows(milli)) });
  }
}
function unrelated(project: Project) {
  return { id: project.id, generation: project.generation, branch: project.active_branch, title: project.title,
    brief: project.brief, narrative: project.narrative, audio: project.audio, assets: project.assets,
    markers: project.markers, locks: project.locks, deliverables: project.deliverables, visualLanguage: project.visual_language,
    proposals: project.proposal_sets, invocations: project.model_invocations, extensions: project.extensions, handoffs: project.handoffs };
}

for (const kind of ["stage", "procedural"] as const) {
  test(`${kind} actual App updates preserve stable editable objects and a human override; detach survives SQLite restart`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { kind };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await unlocked(service); observations.initial = initial; await attach(page, service, initial, kind);
      const sceneId = workbenchScene(initial, kind).id;
      await fill(page, kind, initial); await button(page, saves[kind]).click(); await revision(page, initial.revision + 1);
      const added = await service.project(initial.id), component = kind === "stage" ? added.production_design!.stages![0] : added.production_design!.procedural_fields![0];
      const node = component.baseline.find(value => kind === "stage" ? value.name.endsWith(" / screen_detail") : true)!;
      const human = await service.apply(added, kind === "stage" ? { type: "update_canvas_text", scene_id: sceneId, node_id: node.id, text: "Independently edited detail" }
        : { type: "update_canvas_style", scene_id: sceneId, node_id: node.id, style: { ...node.style, fill: "#eeccaa" } });
      await page.reload(); await openWorkbench(page, kind); await page.getByLabel("Production working scene").selectOption(sceneId);
      if (kind === "stage") await page.getByLabel("ProductStage heading").fill("A revised workflow");
      else await page.getByLabel("Integer seed").fill("58");
      await button(page, kind === "stage" ? "Update ProductStage" : "Update procedural field").click(); await revision(page, human.revision + 1);
      const updated = await service.project(initial.id), current = kind === "stage" ? updated.production_design!.stages![0] : updated.production_design!.procedural_fields![0];
      expect(current.baseline.map(value => value.id)).toEqual(component.baseline.map(value => value.id));
      const overridden = updated.scenes.find(scene => scene.id === sceneId)!.nodes.find(value => value.id === node.id)!;
      if (kind === "stage") expect(overridden.text).toBe("Independently edited detail"); else expect(overridden.style.fill).toBe("#eeccaa");
      const objects = updated.scenes.find(scene => scene.id === sceneId)!.nodes;
      await button(page, kind === "stage" ? "Detach, retain objects" : "Detach generator (keep objects)").click(); await revision(page, updated.revision + 1);
      final = await service.project(initial.id); expect(final.scenes.find(scene => scene.id === sceneId)!.nodes).toEqual(objects);
      expect(kind === "stage" ? final.production_design!.stages : final.production_design!.procedural_fields).toEqual([]);
      expect(unrelated(final)).toEqual(unrelated(initial)); expect(final.scenes.filter(scene => scene.id !== sceneId)).toEqual(initial.scenes.filter(scene => scene.id !== sceneId));
      if (kind === "stage") expectOriginalStageWork(final, initial);
      const events = await service.history(initial.id, initial.revision); expect(events.map(value => value.change.type)).toEqual([changeTypes[kind], kind === "stage" ? "update_canvas_text" : "update_canvas_style", changeTypes[kind], kind === "stage" ? "detach_product_stage" : "detach_procedural_field"]);
      await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
      observations.journal = events; observations.nodeIds = component.baseline.map(value => value.id);
      await page.screenshot({ path: info.outputPath(kind + "-editable-after-detach.png"), fullPage: true });
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, [], final, observations, [], primary); }
  });
}

test("Measured actual source bytes, versioned table and Canvas binding persist through App editing and SQLite restart", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const imported = await sourceAsset(service, info, await unlocked(service)), initial = imported.project; observations.initial = initial;
    const scene = initial.scenes.find(value => value.renderer === "motion-canvas")!, node = scene.nodes.find(value => value.kind === "text")!;
    await attach(page, service, initial, "measured"); await fill(page, "measured", initial);
    await page.getByLabel("Measured dataset label").fill("  Retained source label  ");
    await button(page, "Timeline").click(); await openWorkbench(page, "measured");
    await expect(page.getByLabel("Measured dataset label")).toHaveValue("  Retained source label  ");
    await expect(button(page, "Register measured source")).toBeDisabled();
    await page.getByLabel("Measured source file verification").setInputFiles({ name: "source.json", mimeType: "application/json", buffer: Buffer.from(imported.bytes) });
    const baseWrites = applies(service).length; await button(page, "Register measured source").click(); await revision(page, initial.revision + 1);
    const registered = await service.project(initial.id), table = registered.production_design!.measured_tables![0];
    expect(table.rows).toEqual(JSON.parse(imported.bytes)); expect(table.source_sha256).toBe(imported.asset.content_sha256); expect(table.label).toBe("  Retained source label  ");
    await expect(page.getByLabel("Measured table", { exact: true })).toHaveValue(table.id);
    await page.getByLabel("Measured datum row").selectOption("latency"); await page.getByLabel("Measured Canvas node").selectOption(node.id);
    await button(page, "Bind measured output").click(); await revision(page, registered.revision + 1);
    const bound = await service.project(initial.id); expect(bound.scenes.find(value => value.id === scene.id)!.nodes.find(value => value.id === node.id)?.text).toBe("12.50 ms");
    const second = await sourceAsset(service, info, bound, 18000);
    await page.reload(); await openWorkbench(page, "measured"); await page.getByLabel("Measured table", { exact: true }).selectOption(table.id);
    // The prior clean checkpoint is old after a separate actual import. Resolve
    // that observed revision explicitly before preparing a new version.
    const keep = button(page, "Keep my Measured data input"); if (await keep.isVisible()) await keep.click();
    await page.getByLabel("Measured source asset").selectOption(second.asset.id);
    await page.getByLabel("Measured source file verification").setInputFiles({ name: "source-next.json", mimeType: "application/json", buffer: Buffer.from(second.bytes) });
    await button(page, "Commit next measured source revision").click(); await revision(page, second.project.revision + 1);
    const updated = await service.project(initial.id); expect(updated.production_design!.measured_tables![0].version).toBe(2);
    expect(updated.scenes.find(value => value.id === scene.id)!.nodes.find(value => value.id === node.id)?.text).toBe("18.00 ms");
    const objects = updated.scenes.find(value => value.id === scene.id)!.nodes;
    await button(page, "Detach (keep Canvas)").click(); await revision(page, updated.revision + 1);
    final = await service.project(initial.id); expect(final.production_design!.measured_bindings).toEqual([]); expect(final.scenes.find(value => value.id === scene.id)!.nodes).toEqual(objects);
    expect(applies(service)).toHaveLength(baseWrites + 4); const events = await service.history(initial.id, initial.revision);
    expect(events.filter(value => value.change.type !== "add_asset").map(value => value.change.type)).toEqual(["upsert_measured_table", "upsert_measured_binding", "upsert_measured_table", "detach_measured_binding"]);
    await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
    observations.sourcePaths = [imported.path, second.path]; observations.journal = events;
    await page.screenshot({ path: info.outputPath("measured-source-and-editable-output.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

test("Measured original binding receipt preserves later raw selection and precision across actual App remount", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const imported = await sourceAsset(service, info, await unlocked(service)); await attach(page, service, imported.project, "measured"); await fill(page, "measured", imported.project);
    await button(page, "Register measured source").click(); await revision(page, imported.project.revision + 1);
    const initial = await service.project(imported.project.id), scene = initial.scenes.find(value => value.renderer === "motion-canvas")!, node = scene.nodes.find(value => value.kind === "text")!;
    observations.initial = initial; await page.getByLabel("Measured datum row").selectOption("latency"); await page.getByLabel("Measured Canvas node").selectOption(node.id);
    hold = holdReply(service, "upsert_measured_binding"); await button(page, "Bind measured output").click(); const receipt = await hold.observed;
    await button(page, "Timeline").click(); await openWorkbench(page, "measured"); await page.getByLabel("Measured decimals").fill("1e");
    hold.release(); await hold.dispose(); await revision(page, initial.revision + 1);
    await expect(page.getByLabel("Measured Canvas node")).toHaveValue(node.id); await expect(page.getByLabel("Measured decimals")).toHaveValue("1e");
    final = await service.project(initial.id); expect(final).toEqual(receipt); expect(final.production_design!.measured_bindings![0].view).toEqual({ kind: "counter", decimals: 2 });
    expect(await service.reopen(initial.id)).toEqual(final); observations.originalReceipt = receipt;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

for (const kind of ["stage", "procedural", "measured"] as const) {
  test(`${kind} an actual committed response lost in transport remains UNKNOWN through reload and requires fresh history review`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { kind }, original = service.invoke.bind(service);
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const seed = await unlocked(service), initial = kind === "measured" ? (await sourceAsset(service, info, seed)).project : seed; observations.initial = initial;
      await attach(page, service, initial, kind); await fill(page, kind, initial); const baseWrites = applies(service).length, baseGrants = requests(service, "issue_effect_grant").length;
      let arm = true;
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!arm || command !== "apply_change" || request?.change.type !== changeTypes[kind]) return original<T>(command, args);
        arm = false; await original<Project>(command, args); throw new Error("Original workbench response was lost after actual commit.");
      };
      await button(page, saves[kind]).click(); const unknown = page.getByRole("region", { name: "Unconfirmed " + labels[kind], exact: true });
      await expect(unknown).toBeVisible(); await expect(page.getByRole("alert").filter({ hasText: "lost after actual commit" })).toBeVisible(); service.invoke = original;
      const saved = await service.project(initial.id); expect(saved.revision).toBe(initial.revision + 1);
      await page.reload(); await openWorkbench(page, kind);
      await page.getByLabel("Production working scene", { exact: true }).selectOption(workbenchScene(initial, kind).id);
      await expect(unknown).toBeVisible();
      expect(applies(service)).toHaveLength(baseWrites + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(baseGrants + 1);
      await button(page, "Read saved " + labels[kind] + " and history").click();
      await expect(page.getByLabel(labels[kind] + " outcome review", { exact: true })).toContainText("consecutive history records");
      await button(page, "Keep earlier " + labels[kind] + " attempt and continue editing").click(); await expect(unknown).toHaveCount(0);
      expect(applies(service)).toHaveLength(baseWrites + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(baseGrants + 1);
      final = await service.project(initial.id); expect(final).toEqual(saved); expect(await service.reopen(initial.id)).toEqual(final);
      if (kind === "stage") expectOriginalStageWork(final, initial);
      observations.retainedCopies = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith("motionwright.creative-workbench-recovery.v1:")));
      expect((observations.retainedCopies as unknown[]).length).toBeGreaterThanOrEqual(2);
    } catch (reason) { primary = { reason }; }
    finally { service.invoke = original; await finish(page, info, service, [], final, observations, [], primary); }
  });
}

for (const fault of ["data", "pointer"] as const) {
  test(`creative workbench ${fault} backup failure sends no real grant or change; explicit healthy retry saves retained input`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { fault };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await unlocked(service); observations.initial = initial; await attach(page, service, initial, "stage");
      const baseWrites = applies(service).length, baseGrants = requests(service, "issue_effect_grant").length;
      await page.evaluate(fault => {
        const write = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          if (fault === "data" && this === localStorage && key.startsWith("motionwright.creative-workbench-recovery.v1:")) return;
          if (fault === "pointer" && this === sessionStorage && key.startsWith("motionwright.creative-workbench-writer.v1:")) return;
          return write.call(this, key, value);
        };
        (window as unknown as { __RESTORE_WORKBENCH_STORAGE__: () => void }).__RESTORE_WORKBENCH_STORAGE__ = () => { Storage.prototype.setItem = write; };
      }, fault);
      await fill(page, "stage", initial); await expect(button(page, saves.stage)).toBeDisabled();
      await expect(page.getByRole("alert", { name: "ProductStage backup warning", exact: true })).toBeVisible();
      expect(await service.project(initial.id)).toEqual(initial); expect(applies(service)).toHaveLength(baseWrites); expect(requests(service, "issue_effect_grant")).toHaveLength(baseGrants);
      await page.evaluate(() => (window as unknown as { __RESTORE_WORKBENCH_STORAGE__: () => void }).__RESTORE_WORKBENCH_STORAGE__());
      await button(page, "Retry ProductStage backup").click(); await button(page, saves.stage).click(); await revision(page, initial.revision + 1);
      final = await service.project(initial.id); expect(final.production_design!.stages![0].config.screen_heading).toBe("Clear workflow");
      expectOriginalStageWork(final, initial);
      expect(applies(service)).toHaveLength(baseWrites + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(baseGrants + 1); expect(await service.reopen(initial.id)).toEqual(final);
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, [], final, observations, [], primary); }
  });
}

test("procedural influence edits and reopens one source-bound field with independent human overrides and incomplete input retained", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    let initial = await unlocked(service);
    const ids = new Set(initial.scenes.map(scene => scene.id));
    initial = await service.apply(initial, { type: "add_scene", name: "Influence component", objective: "Bounded source-editable field", duration_seconds: 6 });
    const sceneId = initial.scenes.find(scene => !ids.has(scene.id))!.id;
    await service.attachFixturePage(page, initial.id); await page.goto("/");
    const open = async () => { await openWorkbench(page, "procedural"); await page.getByLabel("Production working scene", { exact: true }).selectOption(sceneId); };
    await open();
    await page.getByLabel("Procedural distribution", { exact: true }).selectOption("grid");
    for (const [label, value] of [["Number of objects", "3"], ["Columns", "3"], ["Stage X", "200"], ["Stage Y", "200"],
      ["Field width", "300"], ["Field height", "100"], ["Object size", "40"], ["Procedural fill", "#000000"]] as const)
      await page.getByLabel(label, { exact: true }).fill(value);
    await page.getByLabel("Enable influence field", { exact: true }).check();
    for (const [label, value] of [["Influence center X", "350"], ["Influence center Y", "250"], ["Influence radius (px)", "200"],
      ["Influence offset X", "20"], ["Influence offset Y", "-10"], ["Influence center scale (%)", "150"],
      ["Influence static orientation (degrees)", "30"], ["Influence center fill", "#FFFFFF"]] as const)
      await page.getByLabel(label, { exact: true }).fill(value);
    await button(page, "Add procedural field").click(); await revision(page, initial.revision + 1);
    const saved = await service.project(initial.id), field = saved.production_design!.procedural_fields!.find(value => value.scene_id === sceneId)!;
    const nodes = saved.scenes.find(scene => scene.id === sceneId)!.nodes;
    expect(nodes.map(node => [node.x, node.y, node.width, node.rotation_deg, node.style.fill])).toEqual([
      [235, 220, 50, 15, "#7F7F7F"], [340, 210, 60, 30, "#FFFFFF"], [435, 220, 50, 15, "#7F7F7F"],
    ]);
    const human = await service.apply(saved, { type: "update_canvas_style", scene_id: sceneId, node_id: nodes[0].id,
      style: { ...nodes[0].style, stroke: "#123456", stroke_width: 2 } });
    await page.reload(); await open();
    await expect(page.getByLabel("Influence center scale (%)", { exact: true })).toHaveValue("150");
    await page.getByLabel("Influence offset X", { exact: true }).fill("-20");
    await page.getByLabel("Influence center fill", { exact: true }).fill("#40A0FF");
    await button(page, "Update procedural field").click(); await revision(page, human.revision + 1);
    const updated = await service.project(initial.id), updatedField = updated.production_design!.procedural_fields!.find(value => value.id === field.id)!;
    expect(updatedField.config.influence?.offset_x).toBe(-20);
    const updatedNodes = updated.scenes.find(scene => scene.id === sceneId)!.nodes;
    expect(updatedNodes.map(node => node.id)).toEqual(nodes.map(node => node.id)); expect(updatedNodes[0].style.stroke).toBe("#123456");
    await page.getByLabel("Influence offset X", { exact: true }).fill("-");
    await page.reload(); await open();
    await expect(page.getByLabel("Influence offset X", { exact: true })).toHaveValue("-");
    await expect(button(page, "Update procedural field")).toBeDisabled();
    expect(await service.project(initial.id)).toEqual(updated);
    await page.getByLabel("Enable influence field", { exact: true }).uncheck();
    await button(page, "Update procedural field").click(); await revision(page, updated.revision + 1);
    final = await service.project(initial.id);
    const finalField = final.production_design!.procedural_fields!.find(value => value.id === field.id)!;
    expect(finalField.config.influence).toBeUndefined();
    expect(final.scenes.find(scene => scene.id === sceneId)!.nodes.map(node => [node.x, node.y, node.width, node.rotation_deg, node.style.fill])).toEqual([
      [230, 230, 40, 0, "#000000"], [330, 230, 40, 0, "#000000"], [430, 230, 40, 0, "#000000"],
    ]);
    expect(final.scenes.find(scene => scene.id === sceneId)!.nodes[0].style.stroke).toBe("#123456");
    expect(final.assets).toEqual(initial.assets);
    expect(final.scenes.filter(scene => scene.id !== sceneId)).toEqual(initial.scenes.filter(scene => scene.id !== sceneId));
    const oracle = await service.observeStoredProject(final); await service.close(); await service.start();
    expect(await service.project(final.id)).toEqual(final);
    await page.reload(); await open();
    await expect(page.getByLabel("Enable influence field", { exact: true })).not.toBeChecked();
    await expect(page.getByLabel("Influence offset X", { exact: true })).toHaveValue("-");
    const changes = oracle.journal.filter(event => event.revision > initial.revision).map(event => event.change.type);
    expect(changes).toEqual(["upsert_procedural_field", "update_canvas_style", "upsert_procedural_field", "upsert_procedural_field"]);
    Object.assign(observations, { initial, saved, updated, oracle, instanceId: field.id, changes });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

test("Measured complete output editing preserves independent drafts, source revisions and the original SQLite bindings", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const imported = await sourceAsset(service, info, await unlocked(service)), initial = imported.project;
    observations.initial = initial;
    const scene = workbenchScene(initial, "measured"), text = scene.nodes.find(node => node.kind === "text"), bar = scene.nodes.find(node => node.kind === "rectangle");
    if (!text || !bar) throw new Error("The real source fixture needs an editable text and rectangle for this measured-output scenario.");
    await attach(page, service, initial, "measured"); await fill(page, "measured", initial);
    const initialWrites = applies(service).length;
    await button(page, "Register measured source").click(); await revision(page, initial.revision + 1);
    const registered = await service.project(initial.id), table = registered.production_design!.measured_tables![0];
    await page.getByLabel("Measured datum row", { exact: true }).selectOption("latency");
    await page.getByLabel("Measured Canvas node", { exact: true }).selectOption(text.id);
    await button(page, "Bind measured output").click(); await revision(page, registered.revision + 1);
    const counterSaved = await service.project(initial.id), counter = counterSaved.production_design!.measured_bindings!.find(binding => binding.node_id === text.id)!;
    await expect(page.getByLabel("Measured output to edit", { exact: true })).toHaveValue(counter.id);
    await page.getByLabel("Measured output to edit", { exact: true }).selectOption("");
    await page.getByLabel("Measured output kind", { exact: true }).selectOption("bar");
    await page.getByLabel("Measured datum row", { exact: true }).selectOption("latency");
    await page.getByLabel("Measured Canvas node", { exact: true }).selectOption(bar.id);
    for (const [label, value] of [["Measured axis minimum", "0"], ["Measured axis maximum", "100000"], ["Measured maximum bar width", "800"]])
      await page.getByLabel(label, { exact: true }).fill(value);
    await button(page, "Bind measured output").click(); await revision(page, counterSaved.revision + 1);
    const bound = await service.project(initial.id), barBinding = bound.production_design!.measured_bindings!.find(binding => binding.node_id === bar.id)!;
    await page.getByLabel("Measured output to edit", { exact: true }).selectOption(counter.id);
    await page.getByLabel("Measured output kind", { exact: true }).selectOption("label");
    await page.getByLabel("Measured decimals", { exact: true }).fill("1");
    hold = holdReply(service, "upsert_measured_binding");
    await button(page, "Update measured output").click(); const original = await hold.observed;
    await page.getByLabel("Measured decimals", { exact: true }).fill("1e");
    await page.getByLabel("Measured output to edit", { exact: true }).selectOption(barBinding.id);
    await page.getByLabel("Measured axis minimum", { exact: true }).fill("-");
    hold.release(); await hold.dispose(); await revision(page, original.revision);
    await expect(page.getByLabel("Measured axis minimum", { exact: true })).toHaveValue("-");
    await page.getByLabel("Measured axis minimum", { exact: true }).fill("0");
    await page.getByLabel("Measured axis maximum", { exact: true }).fill("50000");
    await page.getByLabel("Measured maximum bar width", { exact: true }).fill("900");
    await button(page, "Update measured output").click(); await revision(page, original.revision + 1);
    await page.reload(); await openWorkbench(page, "measured");
    await page.getByLabel("Measured output to edit", { exact: true }).selectOption(counter.id);
    await expect(page.getByLabel("Measured decimals", { exact: true })).toHaveValue("1e");
    await expect(button(page, "Update measured output")).toBeDisabled();
    await button(page, "Use saved output").click();
    await expect(page.getByLabel("Measured decimals", { exact: true })).toHaveValue("1");
    await page.getByLabel("Measured decimals", { exact: true }).fill("3");
    await button(page, "Update measured output").click(); await revision(page, original.revision + 2);
    const edited = await service.project(initial.id);
    expect(edited.production_design!.measured_bindings!.map(binding => binding.id)).toEqual([counter.id, barBinding.id]);
    expect(edited.scenes.find(item => item.id === scene.id)!.nodes.find(node => node.id === bar.id)!.width).toBe(225);
    expect(edited.scenes.find(item => item.id === scene.id)!.nodes.find(node => node.id === text.id)!.text).toBe("Observed latency · 12.500 ms");
    const nextSource = await sourceAsset(service, info, edited, 22500);
    await page.reload(); await openWorkbench(page, "measured");
    await page.getByLabel("Measured table", { exact: true }).selectOption(table.id);
    const keep = button(page, "Keep my Measured data input"); if (await keep.isVisible()) await keep.click();
    await page.getByLabel("Measured source asset", { exact: true }).selectOption(nextSource.asset.id);
    await page.getByLabel("Measured source file verification", { exact: true }).setInputFiles({ name: "next.json", mimeType: "application/json", buffer: Buffer.from(nextSource.bytes) });
    await button(page, "Commit next measured source revision").click(); await revision(page, nextSource.project.revision + 1);
    final = await service.project(initial.id);
    expect(final.production_design!.measured_bindings!.map(binding => binding.id)).toEqual([counter.id, barBinding.id]);
    const finalScene = final.scenes.find(item => item.id === scene.id)!;
    expect(finalScene.nodes.find(node => node.id === text.id)).toEqual({ ...text, text: "Observed latency · 22.500 ms" });
    expect(finalScene.nodes.find(node => node.id === bar.id)).toEqual({ ...bar, width: 405 });
    expect(final.production_design!.measured_tables![0]).toMatchObject({ id: table.id, version: 2, source_sha256: nextSource.asset.content_sha256 });
    expect(applies(service).slice(initialWrites).filter(entry => entry.request.change.type === "upsert_measured_binding")).toHaveLength(5);
    const history = await service.history(initial.id, initial.revision);
    await service.close(); await service.start();
    expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(history);
    observations.sourcePaths = [imported.path, nextSource.path]; observations.originalHeldReceipt = original; observations.journal = history;
    await page.screenshot({ path: info.outputPath("measured-complete-output-editing.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});
