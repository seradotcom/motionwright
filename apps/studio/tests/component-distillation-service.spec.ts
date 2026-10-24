import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { CanvasNode, Change, Project } from "../src/types";
import type { ComponentDistillationInput } from "../src/componentDistillation";
import { DISTILLATION_RECOVERY_PREFIX, distillationPointerKey, distillationScope } from "../src/componentDistillationDraft";
import type { DistillationDraft, DistillationTarget } from "../src/componentDistillationDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App + original coordinator + source-built StudioService + grants +
// SQLite. Synthetic author/reviewer inputs are test data, never human acceptance.
// Reply delivery/storage faults do not fabricate saved Projects or source samples.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built StudioService runner is required; no simulated persistence fallback.");
test.setTimeout(120_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const label = (page: Page) => page.getByLabel("Distillation label", { exact: true });
const note = (page: Page) => page.getByLabel("Distillation review rationale", { exact: true });
const saveNames = { author: "Propose original component", review: "Record review only" };
const changeTypes = { author: "propose_component_distillation", review: "review_component_distillation" } as const;
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function open(page: Page) {
  await button(page, "Production").click(); await button(page, "Distillation").click();
  await expect(label(page)).toBeVisible();
}
async function activeCopy(page: Page, project: Project, target: DistillationTarget) {
  const scope = distillationScope(project, target), pointer = distillationPointerKey(scope);
  return page.evaluate(({ scope, pointer, prefix }) => {
    const writer = sessionStorage.getItem(pointer);
    if (!writer) throw new Error("The real editor has not published a recovery pointer.");
    const key = prefix + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("The real editor recovery copy is missing.");
    return { scope, pointer, writer, key, raw, draft: (JSON.parse(raw) as { draft: DistillationDraft }).draft };
  }, { scope, pointer, prefix: DISTILLATION_RECOVERY_PREFIX });
}
/** One failed original sessionStorage read; all copy bytes come from actual UI edits. */
async function failFirstPointerRead(page: Page, pointer: string) {
  const marker = "motionwright.test.distillation-first-read:" + crypto.randomUUID();
  await page.addInitScript(({ pointer, marker }) => {
    const original = Storage.prototype.getItem;
    if (original.call(sessionStorage, marker) === "fired") return;
    let armed = true;
    Storage.prototype.getItem = function(key) {
      if (armed && this === sessionStorage && key === pointer) {
        armed = false; sessionStorage.setItem(marker, "fired");
        throw new Error("Injected first Distillation pointer read failure.");
      }
      return original.call(this, key);
    };
    (window as unknown as { __RESTORE_DISTILLATION_READ__?: () => void }).__RESTORE_DISTILLATION_READ__ = () => { Storage.prototype.getItem = original; };
  }, { pointer, marker });
  return marker;
}
async function restorePointerRead(page: Page) {
  await page.evaluate(() => (window as unknown as { __RESTORE_DISTILLATION_READ__?: () => void }).__RESTORE_DISTILLATION_READ__?.());
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
  try {
    const pendingPreparations = preparations.map(action => Promise.resolve().then(action));
    // The finalizer observes every original outcome. Page closure waits for
    // restoration/gate disposal to settle, including rejected preparations.
    const preparationsSettled = Promise.allSettled(pendingPreparations);
    custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [
      ...pendingPreparations.map(pending => () => pending),
      async () => { await preparationsSettled; await page.close(); },
    ]);
  }
  catch (reason) { failures.push(reason); }
  try {
    const path = info.outputPath("component-distillation-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "synthetic authoring scenario against real StudioService + grants + SQLite + App DOM",
      exclusions: ["installed Tauri", "native renderer pixels", "human acceptance"], observations, final,
      canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [service, ...peers].map(value => ({
        databasePath: value.databasePath, binaryPath: value.binaryPath, transcript: value.transcript,
        processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, stderr: value.stderr,
      })),
    }, null, 2));
    await info.attach("component-distillation-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Component distillation scenario and store finalization retained separate failures", { cause: primary?.reason });
}
async function setup(info: TestInfo) {
  return ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
}
async function unlocked(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const lock of [...project.locks]) project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  return project;
}
const sourceNames = ["Distillation original A", "Distillation original B"];
const rejectedName = "Distillation locked counterexample";
async function sources(service: ScopedChangeService) {
  let project = await unlocked(service);
  const original = project.scenes.flatMap(scene => scene.nodes).find(value => value.kind === "text");
  if (!original) throw new Error("The real fixture has no complete Canvas text node template.");
  // The service fixture contains a Hero and independent note, not the browser
  // fixture scenes. Add three genuinely authored sources through real changes.
  for (const name of [...sourceNames, rejectedName]) project = await service.apply(project, { type: "add_scene", name,
    objective: "Controlled original source evidence for immutable distillation", duration_seconds: 6 });
  for (const [index, name] of sourceNames.entries()) {
    const scene = project.scenes.find(value => value.name === name)!;
    for (const kind of ["text", "rectangle"]) {
      const node: CanvasNode = { ...structuredClone(original), id: crypto.randomUUID(), name: "Authored " + kind + " variation " + index,
        kind, text: kind === "text" ? "Distinct source heading " + index : null, x: 230 + index * 20, y: kind === "text" ? 230 : 460,
        width: 500, height: 180, parent_id: null, relations: [], property_locks: [], keyframes: [] };
      project = await service.apply(project, { type: "add_canvas_node", scene_id: scene.id, node });
    }
  }
  project = await service.apply(project, { type: "add_canvas_node", scene_id: project.scenes.find(value => value.name === rejectedName)!.id,
    node: { ...structuredClone(original), id: crypto.randomUUID(), name: "Actually locked original text", text: "Excluded locked source",
      parent_id: null, relations: [], property_locks: ["text"], keyframes: [] } });
  return project;
}
function proposalInput(project: Project, name: string): ComponentDistillationInput {
  return { id: crypto.randomUUID(), label: name, author: "Original test author",
    original_rights_attestation: "Original source roles authored for this controlled service test.",
    source_project_id: project.id, source_generation: project.generation, source_revision: project.revision,
    roles: [{ key: "slot_01", kind: "text" }, { key: "slot_02", kind: "rectangle" }],
    positives: sourceNames.map(name => project.scenes.find(scene => scene.name === name)!).map(scene => ({ scene_id: scene.id,
      node_ids: [scene.nodes.find(node => node.kind === "text")!.id, scene.nodes.find(node => node.kind === "rectangle")!.id] })),
    negatives: [{ scene_id: project.scenes.find(scene => scene.name === rejectedName)!.id, node_id: project.scenes.find(scene => scene.name === rejectedName)!.nodes[0].id, rejection: "locked_object" }],
    requirements: ["Preserve editable original source roles", "Keep immutable review separate from executable admission"] };
}
async function withProposals(service: ScopedChangeService, count: number) {
  let project = await sources(service); const ids: string[] = [];
  for (let index = 0; index < count; index++) {
    const input = proposalInput(project, "Candidate " + String.fromCharCode(65 + index)); ids.push(input.id);
    project = await service.apply(project, { type: "propose_component_distillation", input });
  }
  return { project, ids };
}
async function attach(page: Page, service: ScopedChangeService, project: Project) {
  await service.attachFixturePage(page, project.id); await page.goto("/"); await open(page);
}
async function author(page: Page, project: Project) {
  const scenes = sourceNames.map(name => project.scenes.find(scene => scene.name === name)!);
  await page.getByLabel("Distillation source A").selectOption(scenes[0].id);
  await page.getByLabel("Distillation source B").selectOption(scenes[1].id);
  await page.getByLabel("Distillation negative scene").selectOption(project.scenes.find(scene => scene.name === rejectedName)!.id);
  await label(page).fill("New service candidate"); await page.getByLabel("Distillation author", { exact: true }).fill("Original test author");
  await page.getByLabel("Distillation rights").fill("Original source roles authored for this controlled service test.");
  await page.getByLabel("Distillation requirements").fill("Preserve editable original source roles\nKeep immutable review separate from executable admission");
}
async function reviewer(page: Page, id: string) {
  await page.getByLabel("Distillation review candidate").selectOption(id);
  await page.getByLabel("Distillation reviewer").fill("Independent test reviewer");
  await note(page).fill("Compatible source proposal; test review does not admit executable code.");
}
function unrelated(project: Project) {
  const result = structuredClone(project);
  // Revision bookkeeping changes for real native writes. Everything outside the
  // immutable registry, including source objects and admission registries, must stay exact.
  return { id: result.id, generation: result.generation, active_branch: result.active_branch,
    title: result.title, brief: result.brief, narrative: result.narrative, audio: result.audio,
    scenes: result.scenes, assets: result.assets, markers: result.markers, locks: result.locks,
    deliverables: result.deliverables, visual_language: result.visual_language,
    proposal_sets: result.proposal_sets, model_invocations: result.model_invocations,
    extensions: result.extensions, handoffs: result.handoffs,
    otherProduction: { ...result.production_design, distillations: [] } };
}

test("Distillation actual App restores author input after its first pointer read fails and commits frozen samples plus independent review through SQLite restart", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await sources(service); observations.initial = initial; await attach(page, service, initial); await author(page, initial);
    await label(page).fill("  Original service candidate  ");
    await page.getByLabel("Distillation requirements").fill("  Preserve source roles  \n\nKeep review explicit\n");
    await button(page, "Timeline").click(); await open(page); await expect(label(page)).toHaveValue("  Original service candidate  ");
    const beforeRecovery = await activeCopy(page, initial, { kind: "author" }), marker = await failFirstPointerRead(page, beforeRecovery.pointer);
    const beforeWrites = applies(service).length, beforeGrants = requests(service, "issue_effect_grant").length;
    await page.reload(); await open(page);
    await expect(page.getByRole("alert", { name: "Distillation author backup warning", exact: true })).toContainText("first Distillation pointer read failure");
    expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
    await expect(label(page)).toHaveValue(""); await expect(button(page, saveNames.author)).toBeDisabled();
    await expect(button(page, "Keep current Distillation author input in a new copy")).toBeDisabled();
    expect(await page.evaluate(key => localStorage.getItem(key), beforeRecovery.key)).toBe(beforeRecovery.raw);
    expect(await service.project(initial.id)).toEqual(initial);
    await button(page, "Retry Distillation author backup").click();
    await expect(label(page)).toHaveValue("  Original service candidate  ");
    const afterRecovery = await activeCopy(page, initial, { kind: "author" });
    expect(afterRecovery.writer).not.toBe(beforeRecovery.writer); expect(afterRecovery.draft).toEqual(beforeRecovery.draft);
    expect(await page.evaluate(key => localStorage.getItem(key), beforeRecovery.key)).toBe(beforeRecovery.raw);
    await page.reload(); await open(page); await expect(label(page)).toHaveValue("  Original service candidate  ");
    await expect(page.getByLabel("Distillation requirements")).toHaveValue("  Preserve source roles  \n\nKeep review explicit\n");
    expect((await activeCopy(page, initial, { kind: "author" })).draft).toEqual(afterRecovery.draft);
    expect(applies(service)).toHaveLength(beforeWrites); expect(requests(service, "issue_effect_grant")).toHaveLength(beforeGrants);
    expect(await service.project(initial.id)).toEqual(initial); observations.firstReadRecovery = { beforeRecovery, afterRecovery };
    const writes = applies(service).length; await button(page, saveNames.author).click(); await revision(page, initial.revision + 1);
    const proposed = await service.project(initial.id), candidate = proposed.production_design!.distillations!.at(-1)!;
    expect(candidate.input.label).toBe("Original service candidate"); expect(candidate.input.requirements).toEqual(["Preserve source roles", "Keep review explicit"]);
    expect(candidate.review).toBeNull(); expect(candidate.samples).toEqual(candidate.input.positives.map(selector => ({ scene_id: selector.scene_id,
      nodes: selector.node_ids.map(id => initial.scenes.find(scene => scene.id === selector.scene_id)!.nodes.find(node => node.id === id)) })));
    await reviewer(page, candidate.input.id); await button(page, saveNames.review).click(); await revision(page, proposed.revision + 1);
    final = await service.project(initial.id); const reviewed = final.production_design!.distillations!.at(-1)!;
    expect(reviewed.input).toEqual(candidate.input); expect(reviewed.samples).toEqual(candidate.samples);
    expect(reviewed.review).toEqual({ reviewer: "Independent test reviewer", rationale: "Compatible source proposal; test review does not admit executable code.",
      verdict: "compatible_proposal", source_revision: candidate.input.source_revision, compatibility: "motion_canvas_original_nodes_v1" });
    expect(unrelated(final)).toEqual(unrelated(initial)); expect(applies(service)).toHaveLength(writes + 2);
    const events = await service.history(initial.id, initial.revision); expect(events.map(event => event.change.type)).toEqual([changeTypes.author, changeTypes.review]);
    await service.close(); await service.start(); expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(events);
    observations.journal = events; observations.frozenCandidate = reviewed;
    await page.screenshot({ path: info.outputPath("distillation-frozen-originals-reviewed.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [() => restorePointerRead(page)], primary); }
});

test("Distillation actual original proposal receipt preserves later author text through App remount", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const initial = await sources(service); observations.initial = initial; await attach(page, service, initial); await author(page, initial);
    hold = holdReply(service, changeTypes.author); await button(page, saveNames.author).click(); const receipt = await hold.observed;
    await button(page, "Timeline").click(); await open(page); await label(page).fill("  Later unsent candidate  ");
    await page.getByLabel("Distillation requirements").fill("  Later raw requirement\n\n ");
    hold.release(); await hold.dispose(); await revision(page, initial.revision + 1);
    await expect(label(page)).toHaveValue("  Later unsent candidate  "); await expect(page.getByLabel("Distillation requirements")).toHaveValue("  Later raw requirement\n\n ");
    final = await service.project(initial.id); expect(final).toEqual(receipt); expect(final.production_design!.distillations!.at(-1)!.input.label).toBe("New service candidate");
    expect(unrelated(final)).toEqual(unrelated(initial)); expect(await service.reopen(initial.id)).toEqual(final);
    observations.originalReceipt = receipt;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

test("Distillation actual review receipt cannot erase another proposal's authoring buffer", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const { project: initial, ids } = await withProposals(service, 2); observations.initial = initial;
    await attach(page, service, initial); await reviewer(page, ids[0]);
    hold = holdReply(service, changeTypes.review); await button(page, saveNames.review).click(); const receipt = await hold.observed;
    await reviewer(page, ids[1]); await page.getByLabel("Distillation reviewer").fill("Later reviewer B"); await note(page).fill("  Retain B rationale  ");
    hold.release(); await hold.dispose(); await revision(page, initial.revision + 1);
    await expect(page.getByLabel("Distillation review candidate")).toHaveValue(ids[1]); await expect(note(page)).toHaveValue("  Retain B rationale  ");
    await page.reload(); await open(page); await expect(page.getByLabel("Distillation reviewer")).toHaveValue("Later reviewer B"); await expect(note(page)).toHaveValue("  Retain B rationale  ");
    final = await service.project(initial.id); expect(final).toEqual(receipt);
    expect(final.production_design!.distillations!.find(candidate => candidate.input.id === ids[1])).toEqual(initial.production_design!.distillations!.find(candidate => candidate.input.id === ids[1]));
    expect(unrelated(final)).toEqual(unrelated(initial)); expect(await service.reopen(initial.id)).toEqual(final);
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

test("Distillation later rationale survives its actual immutable review receipt and cannot rewrite the saved review", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
  try {
    const { project: initial, ids } = await withProposals(service, 1); observations.initial = initial;
    await attach(page, service, initial); await reviewer(page, ids[0]); const writes = applies(service).length;
    hold = holdReply(service, changeTypes.review); await button(page, saveNames.review).click(); const receipt = await hold.observed;
    await note(page).fill("  Later local reconsideration\nKeep the original review immutable  "); hold.release(); await hold.dispose(); await revision(page, initial.revision + 1);
    await expect(button(page, saveNames.review)).toBeDisabled(); await expect(note(page)).toHaveValue("  Later local reconsideration\nKeep the original review immutable  ");
    await page.reload(); await open(page); await expect(note(page)).toHaveValue("  Later local reconsideration\nKeep the original review immutable  ");
    final = await service.project(initial.id); expect(final).toEqual(receipt); expect(applies(service)).toHaveLength(writes + 1);
    expect(final.production_design!.distillations![0].review!.rationale).toBe("Compatible source proposal; test review does not admit executable code.");
    expect(await service.reopen(initial.id)).toEqual(final);
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

for (const target of ["author", "review"] as const) {
  test(`Distillation ${target} actual reply loss retains UNKNOWN and later cleared input after a first pointer read failure, reload and current-history review`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { target }, original = service.invoke.bind(service);
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const seed = target === "author" ? { project: await sources(service), ids: [] } : await withProposals(service, 1);
      const initial = seed.project; observations.initial = initial; await attach(page, service, initial);
      if (target === "author") await author(page, initial); else await reviewer(page, seed.ids[0]);
      const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length; let armed = true;
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!armed || command !== "apply_change" || request?.change.type !== changeTypes[target]) return original<T>(command, args);
        armed = false; await original<Project>(command, args); throw new Error("Original Distillation response lost after actual commit.");
      };
      await button(page, saveNames[target]).click(); const unknown = page.getByRole("region", { name: "Unconfirmed Distillation " + target, exact: true });
      await expect(unknown).toBeVisible(); await expect(page.getByRole("alert").filter({ hasText: "lost after actual commit" })).toBeVisible(); service.invoke = original;
      const saved = await service.project(initial.id); expect(saved.revision).toBe(initial.revision + 1);
      const editorTarget: DistillationTarget = target === "author" ? { kind: "author" } : { kind: "review", proposalId: seed.ids[0] };
      const beforeRecovery = await activeCopy(page, initial, editorTarget), marker = await failFirstPointerRead(page, beforeRecovery.pointer);
      expect(beforeRecovery.draft.pending?.outcome).toBe("UNKNOWN");
      await page.reload(); await open(page);
      if (target === "review") await page.getByLabel("Distillation review candidate").selectOption(seed.ids[0]);
      await expect(page.getByRole("alert", { name: "Distillation " + target + " backup warning", exact: true })).toContainText("first Distillation pointer read failure");
      expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
      await expect(button(page, saveNames[target])).toBeDisabled();
      const input = target === "author" ? label(page) : note(page);
      await input.fill("Later input while the earlier copy is unreadable"); await input.fill("");
      expect(await page.evaluate(key => localStorage.getItem(key), beforeRecovery.key)).toBe(beforeRecovery.raw);
      expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
      await button(page, "Retry Distillation " + target + " backup").click();
      await expect(unknown).toBeVisible(); await expect(input).toHaveValue(""); await expect(button(page, saveNames[target])).toBeDisabled();
      const afterRecovery = await activeCopy(page, saved, editorTarget);
      expect(afterRecovery.writer).not.toBe(beforeRecovery.writer); expect(afterRecovery.draft.pending).toEqual(beforeRecovery.draft.pending);
      expect(afterRecovery.draft.baseRevision).toBe(beforeRecovery.draft.baseRevision); expect(afterRecovery.draft.baseProposal).toEqual(beforeRecovery.draft.baseProposal);
      expect(afterRecovery.draft.sequence).toBeGreaterThan(beforeRecovery.draft.sequence);
      expect(await page.evaluate(key => localStorage.getItem(key), beforeRecovery.key)).toBe(beforeRecovery.raw);
      await page.reload(); await open(page);
      if (target === "review") await page.getByLabel("Distillation review candidate").selectOption(seed.ids[0]);
      await expect(unknown).toBeVisible(); await expect(input).toHaveValue(""); await expect(button(page, saveNames[target])).toBeDisabled();
      expect((await activeCopy(page, saved, editorTarget)).draft).toEqual(afterRecovery.draft);
      expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
      await button(page, "Read saved Distillation " + target + " and history").click();
      await expect(page.getByLabel("Distillation " + target + " outcome review", { exact: true })).toContainText("consecutive history records");
      await button(page, "Keep earlier Distillation " + target + " attempt and continue editing").click(); await expect(unknown).toHaveCount(0);
      expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
      final = await service.project(initial.id); expect(final).toEqual(saved); expect(await service.reopen(initial.id)).toEqual(final);
      expect(await page.evaluate(key => localStorage.getItem(key), beforeRecovery.key)).toBe(beforeRecovery.raw);
      observations.firstReadRecovery = { beforeRecovery, afterRecovery };
      observations.retainedCopies = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith("motionwright.component-distillation-recovery.v1:")));
      expect((observations.retainedCopies as unknown[]).length).toBeGreaterThanOrEqual(2);
    } catch (reason) { primary = { reason }; }
    finally { service.invoke = original; await finish(page, info, service, [], final, observations, [() => restorePointerRead(page)], primary); }
  });
}

for (const fault of ["data", "pointer"] as const) {
  test(`Distillation ${fault} recovery failure sends no actual grant or write; healthy explicit retry retains source choices`, async ({ page }, info) => {
    const service = await setup(info), observations: Record<string, unknown> = { fault };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = await sources(service); observations.initial = initial; await attach(page, service, initial);
      const writes = applies(service).length, grants = requests(service, "issue_effect_grant").length;
      await page.evaluate(fault => {
        const original = Storage.prototype.setItem; Storage.prototype.setItem = function(key, value) {
          if (fault === "data" && this === localStorage && key.startsWith("motionwright.component-distillation-recovery.v1:")) return;
          if (fault === "pointer" && this === sessionStorage && key.startsWith("motionwright.component-distillation-writer.v1:")) { this.removeItem(key); return; }
          return original.call(this, key, value);
        };
        (window as unknown as { __RESTORE_DISTILLATION__: () => void }).__RESTORE_DISTILLATION__ = () => { Storage.prototype.setItem = original; };
      }, fault);
      await author(page, initial); await expect(button(page, saveNames.author)).toBeDisabled();
      await expect(page.getByRole("alert", { name: "Distillation author backup warning", exact: true })).toBeVisible();
      expect(await service.project(initial.id)).toEqual(initial); expect(applies(service)).toHaveLength(writes); expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      await page.evaluate(() => (window as unknown as { __RESTORE_DISTILLATION__: () => void }).__RESTORE_DISTILLATION__());
      await button(page, "Retry Distillation author backup").click(); await button(page, saveNames.author).click(); await revision(page, initial.revision + 1);
      final = await service.project(initial.id); expect(final.production_design!.distillations!.at(-1)!.input.label).toBe("New service candidate");
      expect(final.scenes).toEqual(initial.scenes); expect(applies(service)).toHaveLength(writes + 1); expect(requests(service, "issue_effect_grant")).toHaveLength(grants + 1);
      expect(await service.reopen(initial.id)).toEqual(final);
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, [], final, observations, [], primary); }
  });
}
