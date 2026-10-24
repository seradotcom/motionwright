import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import type { ProductionDraft } from "../src/productionDesignDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App, source-built StudioService, effect grants and SQLite. Faults alter
// only transport reply delivery or local browser recovery; no saved Project is fabricated.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built StudioService runner is required; no simulated persistence fallback.");
test.setTimeout(120_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const headline = (page: Page) => page.getByLabel("Headline", { exact: true });
const concept = (page: Page) => page.getByRole("textbox", { name: "Creative concept", exact: true });
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openProduction(page: Page, plan = false) {
  await button(page, "Production").click();
  if (plan) await button(page, "Production plan").click();
  await expect(plan ? concept(page) : headline(page)).toBeVisible();
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
    const path = info.outputPath("production-design-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "synthetic authoring scenario against real StudioService + grants + SQLite + App DOM",
      exclusions: ["installed Tauri", "native renderer pixels", "human acceptance"], observations, final,
      canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [service, ...peers].map(value => ({
        databasePath: value.databasePath, binaryPath: value.binaryPath, transcript: value.transcript,
        processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, stderr: value.stderr,
      })),
    }, null, 2));
    await info.attach("production-design-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Production scenario and store finalization retained separate failures", { cause: primary?.reason });
}
async function setup(info: TestInfo) {
  return ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
}
async function unlocked(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const lock of [...project.locks]) project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  return project;
}
function heroCreationScene(project: Project) {
  const heroes = project.production_design?.heroes ?? [];
  // fixtureBootstrap deliberately keeps an existing editable Hero. Creation
  // must use another eligible scene without replacing that earlier work.
  expect(heroes.length, "The service fixture must retain a pre-existing Hero").toBeGreaterThan(0);
  const scene = project.scenes.find(value => value.renderer === "motion-canvas"
    && BigInt(value.duration.den) > 0n && BigInt(value.duration.num) >= 2n * BigInt(value.duration.den)
    && !heroes.some(hero => hero.scene_id === value.id));
  if (!scene) throw new Error("The service fixture needs a separate Motion Canvas scene of at least two seconds without a Hero.");
  return scene;
}
function expectEarlierProductionUnchanged(before: Project, after: Project, sceneId: string) {
  expect(after.production_design!.heroes.filter(hero => hero.scene_id !== sceneId)).toEqual(before.production_design!.heroes);
  expect(after.scenes.filter(scene => scene.id !== sceneId)).toEqual(before.scenes.filter(scene => scene.id !== sceneId));
  const original = before.scenes.find(scene => scene.id === sceneId)!, current = after.scenes.find(scene => scene.id === sceneId)!;
  expect(current).toBeDefined(); expect({ ...current, nodes: original.nodes }).toEqual(original);
  for (const node of original.nodes) expect(current.nodes.find(value => value.id === node.id)).toEqual(node);
}
async function attach(page: Page, service: ScopedChangeService, project: Project, plan = false) {
  await service.attachFixturePage(page, project.id); await page.goto("/"); await openProduction(page, plan);
  if (!plan) {
    const scene = heroCreationScene(project), selected = page.getByLabel("Production working scene");
    await selected.selectOption(scene.id); await expect(selected).toHaveValue(scene.id);
    await expect(button(page, "Add to scene")).toBeVisible();
  }
}
async function fillPlan(page: Page, value: string) {
  await page.getByRole("textbox", { name: "Objective", exact: true }).fill("Preserve an editable production decision through the real service.");
  await page.getByRole("textbox", { name: "Audience", exact: true }).fill("Editors reviewing this controlled regression scenario.");
  await concept(page).fill(value);
}
function unrelated(project: Project) {
  return { id: project.id, generation: project.generation, branch: project.active_branch, title: project.title,
    brief: project.brief, narrative: project.narrative, audio: project.audio, assets: project.assets,
    markers: project.markers, locks: project.locks, deliverables: project.deliverables, visualLanguage: project.visual_language,
    proposals: project.proposal_sets, invocations: project.model_invocations, extensions: project.extensions, handoffs: project.handoffs };
}

test("Production plan raw drafts, claim references and content-bound approval survive actual App navigation and SQLite restart", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const seed = await unlocked(service);
    const initial = await service.apply(seed, { type: "add_claim", text: "An explicitly authored claim for a controlled editing test.",
      source: { kind: "manual_note", label: "Synthetic test author input; not independent evidence" }, context: "Review references without upgrading their evidence.", source_revision: null });
    observations.initial = initial; const baseApplies = applies(service).length;
    await attach(page, service, initial, true);
    const raw = "  An editable concept\nPreserve its author spacing  "; await fillPlan(page, raw);
    await page.getByLabel(/Reference-derived constraints/).fill("  Keep the source reference  \n\n  Preserve the author override  \n");
    await page.getByLabel("Reviewer", { exact: true }).fill("Test editor"); await page.getByLabel("Approval note", { exact: true }).fill("Content decision only for this exact authored test plan.");
    const claim = initial.brief.claims.at(-1)!;
    await page.locator(".production-shot").first().getByRole("checkbox", { name: claim.text, exact: true }).check();
    const digestAsset = initial.assets.find(asset => asset.content_sha256);
    if (digestAsset) await page.getByRole("combobox", { name: "Source asset", exact: true }).first().selectOption(digestAsset.id);
    await button(page, "Timeline").click(); await openProduction(page, true); await expect(concept(page)).toHaveValue(raw);
    await page.reload(); await openProduction(page, true); await expect(concept(page)).toHaveValue(raw);
    await expect(page.getByLabel(/Reference-derived constraints/)).toHaveValue("  Keep the source reference  \n\n  Preserve the author override  \n");
    expect(await service.project(initial.id)).toEqual(initial); expect(applies(service)).toHaveLength(baseApplies);
    await button(page, "Save production plan").click(); await revision(page, initial.revision + 1);
    await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toHaveCount(0);
    const saved = await service.project(initial.id), plan = saved.production_design!.plan!;
    expect(plan.concept).toBe(raw); expect(plan.reference_constraints).toEqual(["Keep the source reference", "Preserve the author override"]);
    expect(plan.shots[0].claim_ids).toEqual([claim.id]); expect(plan.shots[0].asset_ids).toEqual(digestAsset ? [digestAsset.id] : []); expect(plan.approval).toBeNull();
    await expect(page.getByLabel("Reviewer", { exact: true })).toHaveValue("Test editor");
    await button(page, "Approve this concept digest").click(); await revision(page, initial.revision + 2);
    final = await service.project(initial.id); expect(final.production_design!.plan!.approval).toMatchObject({ reviewer: "Test editor", note: "Content decision only for this exact authored test plan." });
    expect(final.production_design!.plan!.approval!.content_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(unrelated(final)).toEqual(unrelated(initial)); expect(final.scenes).toEqual(initial.scenes);
    const events = await service.history(initial.id, initial.revision); expect(events.map(event => event.change.type)).toEqual(["set_production_plan", "set_production_plan"]);
    expect(applies(service)).toHaveLength(baseApplies + 2);
    await page.screenshot({ path: info.outputPath("production-plan-persisted.png"), fullPage: true });
    await service.close(); await service.start(); await page.reload(); await openProduction(page, true);
    expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
    await expect(concept(page)).toHaveValue(raw); observations.journal = events;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

test("Production Hero updates retain editable node identities and a human override; detach preserves objects across SQLite restart", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await unlocked(service); observations.initial = initial; await attach(page, service, initial);
    const sceneId = heroCreationScene(initial).id; observations.workingSceneId = sceneId;
    await headline(page).fill("An editable hero"); await button(page, "Add to scene").click(); await revision(page, initial.revision + 1);
    const added = await service.project(initial.id), hero = added.production_design!.heroes.find(value => value.scene_id === sceneId)!;
    expect(added.production_design!.heroes).toHaveLength(initial.production_design!.heroes.length + 1);
    expectEarlierProductionUnchanged(initial, added, sceneId);
    expect(hero.baseline).toHaveLength(6); const node = hero.baseline.find(value => value.name.endsWith(" / body"))!;
    const human = await service.apply(added, { type: "update_canvas_text", scene_id: sceneId, node_id: node.id, text: "A deliberate independent human override." });
    expectEarlierProductionUnchanged(initial, human, sceneId);
    await page.reload(); await openProduction(page); await page.getByLabel("Production working scene").selectOption(sceneId);
    await page.getByLabel("Hero accent").fill("#d9a46e"); await button(page, "Update component").click(); await revision(page, human.revision + 1);
    const updated = await service.project(initial.id), updatedHero = updated.production_design!.heroes.find(value => value.id === hero.id)!;
    expectEarlierProductionUnchanged(initial, updated, sceneId);
    expect(updatedHero.baseline.map(value => value.id)).toEqual(hero.baseline.map(value => value.id));
    expect(updated.scenes.find(value => value.id === sceneId)!.nodes.find(value => value.id === node.id)?.text).toBe("A deliberate independent human override.");
    const objects = updated.scenes.find(value => value.id === sceneId)!.nodes;
    await button(page, "Detach, keep objects").click(); await revision(page, updated.revision + 1);
    final = await service.project(initial.id); expect(final.production_design!.heroes.find(value => value.id === hero.id)).toBeUndefined();
    expectEarlierProductionUnchanged(initial, final, sceneId);
    expect(final.scenes.find(value => value.id === sceneId)!.nodes).toEqual(objects); expect(unrelated(final)).toEqual(unrelated(initial));
    await expect(headline(page)).toHaveValue("An editable hero");
    const events = await service.history(initial.id, initial.revision);
    expect(events.map(event => event.change.type)).toEqual(["upsert_product_hero", "update_canvas_text", "upsert_product_hero", "detach_product_hero"]);
    await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
    observations.journal = events; observations.nodeIds = hero.baseline.map(value => value.id);
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

for (const target of ["component", "plan"] as const) {
  test(`a held original Production ${target} receipt preserves later typing across actual App remount and a separate save`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { target };
    let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
    try {
      const initial = await unlocked(service); observations.initial = initial; await attach(page, service, initial, target === "plan");
      const sceneId = target === "component" ? heroCreationScene(initial).id : null; observations.workingSceneId = sceneId;
      const baseApplies = applies(service).length;
      if (target === "plan") await fillPlan(page, "First plan direction"); else await headline(page).fill("First hero direction");
      hold = holdReply(service, target === "plan" ? "set_production_plan" : "upsert_product_hero");
      await button(page, target === "plan" ? "Save production plan" : "Add to scene").click();
      const observed = await hold.observed; expect(observed.revision).toBe(initial.revision + 1);
      if (target === "component") expectEarlierProductionUnchanged(initial, observed, sceneId!);
      await button(page, "Timeline").click(); await openProduction(page, target === "plan");
      if (target === "component") await expect(page.getByLabel("Production working scene")).toHaveValue(sceneId!);
      const input = target === "plan" ? concept(page) : headline(page), later = target === "plan" ? "  Later plan input\nStill editable  " : "Later hero direction";
      await input.fill(later); hold.release(); await hold.dispose(); await revision(page, initial.revision + 1); await expect(input).toHaveValue(later);
      await expect(page.getByRole("region", { name: "Unconfirmed " + (target === "plan" ? "Production plan" : "Component"), exact: true })).toHaveCount(0);
      await button(page, target === "plan" ? "Save production plan" : "Update component").click(); await revision(page, initial.revision + 2);
      final = await service.project(initial.id); expect(target === "plan" ? final.production_design!.plan!.concept : final.production_design!.heroes.find(hero => hero.scene_id === sceneId)!.config.headline).toBe(later);
      if (target === "component") {
        expectEarlierProductionUnchanged(initial, final, sceneId!);
        expect(final.production_design!.heroes.find(hero => hero.scene_id === sceneId)!.id)
          .toBe(observed.production_design!.heroes.find(hero => hero.scene_id === sceneId)!.id);
      }
      const sent = applies(service).slice(baseApplies); expect(sent).toHaveLength(2); expect(sent[1].request.revision).toBe(initial.revision + 1);
      expect(sent[1].request.request_id).not.toBe(sent[0].request.request_id); expect(sent[1].request.effect_grant).not.toBe(sent[0].request.effect_grant);
      expect(unrelated(final)).toEqual(unrelated(initial)); expect(await service.reopen(initial.id)).toEqual(final); observations.originalReceipt = observed;
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
  });
}

for (const result of ["committed", "matching-peer"] as const) {
  test(`an unknown Production plan ${result} result survives reload without replay and needs a fresh history review`, async ({ page }, info) => {
    const service = await setup(info), peers: ScopedChangeService[] = [], observations: Record<string, unknown> = { result };
    const original = service.invoke.bind(service); let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await unlocked(service); observations.initial = initial; const peer = await service.peer(); peers.push(peer);
      await attach(page, service, initial, true); const baseApplies = applies(service).length, baseGrants = requests(service, "issue_effect_grant").length;
      let armed = true;
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!armed || command !== "apply_change" || request?.change.type !== "set_production_plan") return original<T>(command, args);
        armed = false;
        if (result === "committed") await original<Project>(command, args);
        else await peer.apply(await peer.project(initial.id), request.change);
        throw new Error("Original Production response was unavailable; observed values do not identify its writer.");
      };
      await fillPlan(page, "Identical saved values do not acknowledge the original request"); await button(page, "Save production plan").click();
      const unknown = page.getByRole("region", { name: "Unconfirmed Production plan", exact: true });
      await expect(unknown).toBeVisible(); await expect(unknown).toContainText("original response is unconfirmed");
      await expect(page.getByRole("alert").filter({ hasText: "Original Production response was unavailable" })).toBeVisible();
      service.invoke = original;
      const observed = await service.project(initial.id); expect(observed.revision).toBe(initial.revision + 1);
      const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
      await page.reload(); await openProduction(page, true); await expect(unknown).toBeVisible();
      await expect(button(page, "Save production plan")).toBeDisabled(); expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      expect(grants).toBe(baseGrants + 1); expect(writes).toBe(baseApplies + (result === "committed" ? 1 : 0));
      await button(page, "Read saved Production plan and history").click();
      await expect(page.getByLabel("Production plan outcome review", { exact: true })).toContainText("consecutive history records");
      await button(page, "Keep earlier Production plan attempt and continue editing").click(); await expect(unknown).toHaveCount(0);
      expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      await concept(page).fill("A separate explicit intent after reviewing saved history"); await button(page, "Save production plan").click(); await revision(page, initial.revision + 2);
      final = await service.project(initial.id); expect(final.production_design!.plan!.concept).toBe("A separate explicit intent after reviewing saved history");
      expect(unrelated(final)).toEqual(unrelated(initial)); expect(await service.reopen(initial.id)).toEqual(final); observations.firstObserved = observed;
    } catch (reason) { primary = { reason }; }
    finally { service.invoke = original; await finish(page, info, service, peers, final, observations, [], primary); }
  });
}

for (const fault of ["data", "pointer"] as const) {
  test(`Production ${fault} recovery failure sends no real service grant or change; explicit healthy retry persists the retained draft`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { fault };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await unlocked(service); observations.initial = initial; await attach(page, service, initial, true);
      const beforeApplies = applies(service).length, beforeGrants = requests(service, "issue_effect_grant").length;
      await page.evaluate(fault => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          if (fault === "data" && this === localStorage && key.startsWith("motionwright.production-recovery.v1:")) return;
          if (fault === "pointer" && this === sessionStorage && key.startsWith("motionwright.production-writer.v1:")) return;
          return original.call(this, key, value);
        };
        (window as unknown as { __RESTORE_PRODUCTION_STORAGE__: () => void }).__RESTORE_PRODUCTION_STORAGE__ = () => { Storage.prototype.setItem = original; };
      }, fault);
      await fillPlan(page, "  Retained raw author input despite unavailable local backup  ");
      await expect(button(page, "Save production plan")).toBeDisabled(); await expect(page.getByRole("alert", { name: "Production plan backup warning", exact: true })).toBeVisible();
      expect(await service.project(initial.id)).toEqual(initial); expect(applies(service)).toHaveLength(beforeApplies); expect(requests(service, "issue_effect_grant")).toHaveLength(beforeGrants);
      await page.evaluate(() => (window as unknown as { __RESTORE_PRODUCTION_STORAGE__: () => void }).__RESTORE_PRODUCTION_STORAGE__());
      await button(page, "Retry Production plan backup").click(); await button(page, "Save production plan").click(); await revision(page, initial.revision + 1);
      final = await service.project(initial.id); expect(final.production_design!.plan!.concept).toBe("  Retained raw author input despite unavailable local backup  ");
      expect(applies(service)).toHaveLength(beforeApplies + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(beforeGrants + 1);
      expect(unrelated(final)).toEqual(unrelated(initial)); expect(await service.reopen(initial.id)).toEqual(final);
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, [], final, observations, [], primary); }
  });
}

const planScope = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch, "plan", null]);
async function activePlanCopy(page: Page, scope: string) {
  const result = await page.evaluate(scope => {
    const writerId = sessionStorage.getItem("motionwright.production-writer.v1:" + encodeURIComponent(scope));
    if (!writerId) throw new Error("The plan has no confirmed local writer.");
    const key = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":" + writerId, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("The plan writer has no retained copy.");
    return { writerId, key, raw };
  }, scope);
  return { ...result, draft: JSON.parse(result.raw).draft as ProductionDraft };
}
async function injectFirstPlanRead(page: Page, scope: string, fault: "pointer" | "copy") {
  await page.addInitScript(({ scope, fault }) => {
    const original = Storage.prototype.getItem, marker = "motionwright-test-production-read:" + fault + ":" + scope;
    if (original.call(sessionStorage, marker) === "used") return;
    const pointerKey = "motionwright.production-writer.v1:" + encodeURIComponent(scope);
    const copyPrefix = "motionwright.production-recovery.v1:" + encodeURIComponent(scope) + ":";
    const observation = { fired: false, key: "", fault };
    const exposed = window as unknown as { __PRODUCTION_READ_FAULT__: typeof observation; __RESTORE_PRODUCTION_READ__: () => void };
    exposed.__PRODUCTION_READ_FAULT__ = observation;
    exposed.__RESTORE_PRODUCTION_READ__ = () => { Storage.prototype.getItem = original; };
    Storage.prototype.getItem = function(key: string) {
      if (!observation.fired && ((fault === "pointer" && this === sessionStorage && key === pointerKey)
        || (fault === "copy" && this === localStorage && key.startsWith(copyPrefix)))) {
        observation.fired = true; observation.key = key; sessionStorage.setItem(marker, "used");
        throw new Error("Injected first Production " + fault + " read failure");
      }
      return original.call(this, key);
    };
  }, { scope, fault });
}
async function restorePlanRead(page: Page) {
  return page.evaluate(() => {
    const exposed = window as unknown as { __PRODUCTION_READ_FAULT__: { fired: boolean; key: string; fault: string }; __RESTORE_PRODUCTION_READ__: () => void };
    exposed.__RESTORE_PRODUCTION_READ__(); return exposed.__PRODUCTION_READ_FAULT__;
  });
}

for (const fault of ["pointer", "copy"] as const) for (const outcome of ["ordinary", "UNKNOWN"] as const) {
  test(`Production retries an initial ${fault} read for ${outcome} input without replacing raw intent or replaying a real operation`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { fault, outcome };
    const originalInvoke = service.invoke.bind(service);
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await unlocked(service), scope = planScope(initial); observations.initial = initial;
      await attach(page, service, initial, true);
      const raw = "  Earlier synthetic author direction\nKeep its exact spacing  "; await fillPlan(page, raw);
      await page.getByLabel("Reviewer", { exact: true }).fill("  Retained author reviewer  ");
      await page.getByLabel("Approval note", { exact: true }).fill("  An unsent reviewer note\n  ");
      if (outcome === "UNKNOWN") {
        let armed = true;
        service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
          const request = args.request as ApplyArgs["request"] | undefined;
          if (!armed || command !== "apply_change" || request?.change.type !== "set_production_plan") return originalInvoke<T>(command, args);
          armed = false; await originalInvoke<Project>(command, args);
          throw new Error("The real plan committed; only its original reply was lost for this recovery scenario.");
        };
        await button(page, "Save production plan").click();
        await expect(page.getByRole("alert").filter({ hasText: "only its original reply was lost" })).toBeVisible();
        await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toBeVisible();
        service.invoke = originalInvoke;
      }
      const before = await activePlanCopy(page, scope), savedBeforeRecovery = await service.project(initial.id);
      expect(before.draft.pending?.outcome ?? null).toBe(outcome === "UNKNOWN" ? "UNKNOWN" : null);
      const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
      observations.originalCopy = before; observations.savedBeforeRecovery = savedBeforeRecovery;
      await injectFirstPlanRead(page, scope, fault); await page.reload(); await openProduction(page, true);
      const warning = page.getByRole("alert", { name: "Production plan backup warning", exact: true });
      await expect(warning).toContainText("Injected first Production " + fault + " read failure");
      const freshCopyBlocked = await button(page, "Keep Production plan input in a new copy").isDisabled();
      await expect(button(page, "Save production plan")).toBeDisabled();
      const injection = await restorePlanRead(page); expect(injection.fired).toBe(true);
      expect(injection.key).toBe(fault === "pointer" ? "motionwright.production-writer.v1:" + encodeURIComponent(scope) : before.key);
      observations.injection = injection;
      // The other two paths are intentionally untouched. This distinguishes
      // recovery from a new intent and detects the old placeholder overwrite.
      const gesture = outcome === "ordinary" && fault === "copy" ? "ABA" : outcome === "UNKNOWN" && fault === "pointer" ? "clear" : "untouched";
      let expectedConcept = raw;
      if (gesture !== "untouched") {
        const initialPlaceholder = await concept(page).inputValue();
        await concept(page).fill("Temporary explicit edit while recovery is blocked");
        expectedConcept = gesture === "clear" ? "" : initialPlaceholder; await concept(page).fill(expectedConcept);
      }
      expect(await service.project(initial.id)).toEqual(savedBeforeRecovery);
      expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      await button(page, "Retry Production plan backup").click(); await expect(warning).toHaveCount(0);
      await expect(concept(page)).toHaveValue(expectedConcept);
      const recovered = await activePlanCopy(page, scope);
      expect(recovered.writerId).not.toBe(before.writerId);
      expect(await page.evaluate(key => localStorage.getItem(key), before.key)).toBe(before.raw);
      if (gesture === "untouched") {
        expect(recovered.draft.input).toEqual(before.draft.input); expect(recovered.draft.sequence).toBe(before.draft.sequence);
        expect(recovered.draft.intent).toBe(before.draft.intent);
        await expect(page.getByLabel("Reviewer", { exact: true })).toHaveValue("  Retained author reviewer  ");
      } else {
        expect(recovered.draft.sequence).toBeGreaterThan(before.draft.sequence); expect(recovered.draft.intent).toBe(true);
      }
      expect(recovered.draft.pending).toEqual(before.draft.pending);
      expect(freshCopyBlocked).toBe(true); observations.freshCopyBlocked = freshCopyBlocked;
      if (outcome === "UNKNOWN") expect(recovered.draft.origins).toContainEqual({ key: before.key, raw: before.raw });
      if (outcome === "UNKNOWN" && fault === "copy") {
        const copies = page.getByRole("region", { name: "Production plan recovery copies", exact: true });
        await copies.locator("summary").first().click();
        await expect(copies.getByRole("button", { name: /^Download Production plan copy [0-9]+$/ })).toHaveCount(1);
        const received = page.waitForEvent("download"); await button(page, "Download Production plan copy 1").click();
        const download = await received, path = await download.path();
        if (!path) throw new Error("The retained Production original did not produce a download.");
        const bytes = await readFile(path); expect(bytes.equals(Buffer.from(before.raw, "utf8"))).toBe(true);
        await info.attach("exact-retained-production-original", { body: bytes, contentType: "application/json" });
        observations.originalDownloadBytes = bytes.byteLength;
      }
      expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      observations.gesture = gesture; observations.recoveredCopy = recovered;
      await page.reload(); await openProduction(page, true); await expect(concept(page)).toHaveValue(expectedConcept);
      expect(await service.project(initial.id)).toEqual(savedBeforeRecovery);
      expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      if (outcome === "UNKNOWN") {
        await expect(button(page, "Save production plan")).toBeDisabled();
        await button(page, "Read saved Production plan and history").click();
        await expect(page.getByLabel("Production plan outcome review", { exact: true })).toContainText("consecutive history records");
        await button(page, "Keep earlier Production plan attempt and continue editing").click();
        await expect(page.getByRole("region", { name: "Unconfirmed Production plan", exact: true })).toHaveCount(0);
        expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      }
      const separate = "Separate explicit plan after " + fault + " / " + outcome + " recovery";
      await fillPlan(page, separate); await button(page, "Save production plan").click(); await revision(page, savedBeforeRecovery.revision + 1);
      final = await service.project(initial.id); expect(final.production_design!.plan!.concept).toBe(separate);
      expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
      expect(unrelated(final)).toEqual(unrelated(initial));
      expect(await page.evaluate(key => localStorage.getItem(key), before.key)).toBe(before.raw);
      const events = await service.history(initial.id, initial.revision);
      expect(events.map(event => event.change.type)).toEqual(outcome === "UNKNOWN" ? ["set_production_plan", "set_production_plan"] : ["set_production_plan"]);
      await service.close(); await service.start();
      expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
      observations.journal = events;
    } catch (reason) { primary = { reason }; }
    finally { service.invoke = originalInvoke; await finish(page, info, service, [], final, observations, [], primary); }
  });
}

test("Production confirms an empty pointer after an initial read failure while retaining the input typed during the blocker", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await unlocked(service), scope = planScope(initial); observations.initial = initial;
    await service.attachFixturePage(page, initial.id); await injectFirstPlanRead(page, scope, "pointer");
    await page.goto("/"); await openProduction(page, true);
    const warning = page.getByRole("alert", { name: "Production plan backup warning", exact: true });
    await expect(warning).toContainText("Injected first Production pointer read failure");
    const raw = "  New raw direction while the first pointer read is unavailable  "; await fillPlan(page, raw);
    await expect(button(page, "Keep Production plan input in a new copy")).toBeDisabled();
    await expect(button(page, "Save production plan")).toBeDisabled();
    const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
    const injection = await restorePlanRead(page); expect(injection.fired).toBe(true); observations.injection = injection;
    expect(await page.evaluate(scope => sessionStorage.getItem("motionwright.production-writer.v1:" + encodeURIComponent(scope)), scope)).toBeNull();
    await button(page, "Retry Production plan backup").click(); await expect(warning).toHaveCount(0); await expect(concept(page)).toHaveValue(raw);
    const copy = await activePlanCopy(page, scope); expect(copy.draft.pending).toBeNull(); expect(copy.draft.intent).toBe(true);
    expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
    expect(await service.project(initial.id)).toEqual(initial);
    await page.reload(); await openProduction(page, true); await expect(concept(page)).toHaveValue(raw);
    expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
    await button(page, "Save production plan").click(); await revision(page, initial.revision + 1);
    final = await service.project(initial.id); expect(final.production_design!.plan!.concept).toBe(raw);
    expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
    expect(unrelated(final)).toEqual(unrelated(initial)); expect(await service.reopen(initial.id)).toEqual(final);
    observations.recoveredCopy = copy;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});
