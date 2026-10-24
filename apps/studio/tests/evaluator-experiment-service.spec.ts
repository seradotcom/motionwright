import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { evaluatorDigest, type CreativeEvaluatorBundle } from "../src/creativeEvaluator";
import { emptyProductionDesign } from "../src/creativeProduction";
import { experimentCopyKey, experimentPointerKey, experimentScope, type ExperimentCopy } from "../src/evaluatorExperimentDraft";
import { ScopedChangeService, type ServiceRpcRequest, type StoreFinalization } from "./support/scopedChangeService";

// Actual App and its original coordinator -> source-built StudioService ->
// grants/SQLite/CAS/journal. Data fixtures and response/Storage faults are named;
// there are no synthetic Project replies, installed/native or creative claims.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Requires the source-built StudioService executable; no simulated persistence fallback.");
test.setTimeout(120_000);
declare global { interface Window {
  __EXPERIMENT_SERVICE_RESTORE_STORAGE__?: () => void;
  __EXPERIMENT_SERVICE_INITIAL_READ_FAILURES__?: number;
} }
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const recovery = (page: Page) => page.getByRole("region", { name: "Evaluator experiment recovery", exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Unconfirmed evaluator experiment registration", exact: true });
const savedNotice = (page: Page) => recovery(page).getByRole("status").filter({ hasText: "Registered experiment from its original saved Project response at r" });
const digest = (raw: string) => createHash("sha256").update(raw).digest("hex");
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface ApplyRequest {
  project_id: string; generation: string; revision: number;
  request_id: string; effect_grant: string; change: Change;
}
function calls(service: ScopedChangeService, command: string) {
  return service.transcript.filter(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === command);
}
function applies(service: ScopedChangeService): { rpc: ServiceRpcRequest; request: ApplyRequest }[] {
  return calls(service, "apply_change").map(entry => {
    if (!("command" in entry.payload)) throw new Error("Expected an actual service request.");
    const request = entry.payload.args?.request as ApplyRequest | undefined;
    if (!request) throw new Error("Actual apply_change has no scoped request envelope.");
    return { rpc: entry.payload, request };
  });
}
function mutationCounts(service: ScopedChangeService) {
  return { apply: applies(service).length,
    projectEditGrants: calls(service, "issue_effect_grant").filter(entry => "command" in entry.payload &&
      (entry.payload.args?.request as { effect?: string } | undefined)?.effect === "project_edit").length };
}
function originalBundle(label = "Saved source evaluator"): CreativeEvaluatorBundle {
  return { schema: "motionwright.creative-evaluator/1", id: randomUUID(), version: 1, label,
    purpose: "Observe authored editable source without aesthetic approval",
    author_attestation: "I authored this original controlled service-test checklist.",
    playbook: ["Inspect original editable objects.", "Ask a human reviewer about the creative question."],
    criteria: [{ kind: "minimum_nodes", id: randomUUID(), minimum: 1 },
      { kind: "human_question", id: randomUUID(), question: "Does the source communicate its intended idea?" }] };
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function open(page: Page) {
  await button(page, "Production").click(); await button(page, "Evaluators").click();
  await expect(page.getByRole("heading", { name: "Creative evaluation", exact: true })).toBeVisible();
}
async function ownCopy(page: Page, project: Project) {
  const source = project.production_design!.evaluator_bundles!.at(-1)!;
  const scope = experimentScope(project, source), writer = await page.evaluate(key => sessionStorage.getItem(key), experimentPointerKey(scope));
  if (!writer) throw new Error("The evaluator input has no confirmed writer pointer.");
  const key = experimentCopyKey(scope, writer), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("The selected evaluator writer has no retained bytes.");
  const copy = JSON.parse(raw) as ExperimentCopy;
  expect(copy.schema).toBe("motionwright.evaluator-experiment-copy/1"); expect(copy.writer).toBe(writer); expect(copy.draft.scope).toBe(scope);
  return { key, raw, copy, sha256: digest(raw) };
}
function unrelated(project: Project) {
  const { revision: _revision, updated_at: _updated, schema_version: _schema,
    branches: _branches, reviews: _reviews, production_design: _design, ...rest } = project;
  return rest;
}
function otherDesign(project: Project) {
  const { evaluator_bundles: _bundles, evaluation_reports: _reports, ...rest } = {
    ...emptyProductionDesign(), ...project.production_design,
  }; return rest;
}
function preserved(before: Project, after: Project) {
  expect(unrelated(after)).toEqual(unrelated(before)); expect(otherDesign(after)).toEqual(otherDesign(before));
  expect(after.schema_version).toBe(before.schema_version === 1 ? 2 : before.schema_version);
  expect(after.updated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/);
  expect(Number.isFinite(Date.parse(after.updated_at))).toBe(true);
  expect(after.branches).toEqual(before.branches.map(branch => branch.id === before.active_branch
    ? { ...branch, head_revision: after.revision } : branch));
  expect(after.reviews).toEqual(before.reviews.map(review => review.status === "resolved"
    && review.anchor.branch_id === before.active_branch && review.anchor.revision < after.revision
    ? { ...review, status: "needs_recheck", resolution: null, resolved_at: null } : review));
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject };
}
async function bounded<T>(promise: Promise<T>, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([promise, new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + " exceeded its 15-second observation bound.")), 15_000);
  })]); } finally { clearTimeout(timer); }
}
/** The backend commits first; the selected fault controls delivery only. */
function holdRegistration(service: ScopedChangeService, lose: boolean) {
  const original = service.invoke.bind(service), release = deferred<void>(), observed = deferred<Project>(), done = deferred<void>();
  void observed.promise.catch(() => undefined); let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyRequest | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "register_creative_evaluator") return original<T>(command, args);
    armed = false; matched = true;
    try {
      const saved = await original<Project>(command, args); observed.resolve(saved); await release.promise;
      if (lose) throw new Error("Evaluator original response delivery lost after actual SQLite commit.");
      return saved as T;
    } catch (reason) { observed.reject(reason); throw reason; } finally { done.resolve(); }
  };
  return { observed: observed.promise, release: () => release.resolve(), async dispose() {
    service.invoke = original; release.resolve(); if (matched) await bounded(done.promise, "Held evaluator response cleanup");
  } };
}
interface ExperimentCase {
  service: ScopedChangeService; services: ScopedChangeService[]; baseline: Project;
  initialCounts: ReturnType<typeof mutationCounts>; observations: Record<string, unknown>;
  cleanup: (() => Promise<unknown>)[]; observe(label: string): Promise<Project>; restart(expected: Project): Promise<void>;
}
async function withExperiment(page: Page, info: TestInfo, run: (value: ExperimentCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const services = [service], observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [];
  const failures: unknown[] = []; let primary: { reason: unknown } | undefined, final: Project | undefined, custody: StoreFinalization | undefined;
  const browser: { kind: string; text: string }[] = [];
  const log = (kind: string, text: string) => { browser.push({ kind, text: text.slice(0, 2000) }); if (browser.length > 128) browser.shift(); };
  page.on("console", entry => log(entry.type(), entry.text())); page.on("pageerror", error => log("pageerror", error.message));
  try {
    const boot = await service.fixtureBootstrap();
    expect(boot.test_transport).toBe("service-test-jsonl"); expect(boot.fixture.label).toBe("synthetic_test_fixture");
    let project = boot.project; final = project;
    for (const lock of [...project.locks]) { project = await service.apply(project, { type: "remove_lock", lock_id: lock.id }); final = project; }
    if (project.scenes.length < 2) throw new Error("These experiment scenarios require two distinct existing editable fixture scenes.");
    const original = originalBundle();
    project = await service.apply(project, { type: "register_creative_evaluator", bundle: original }); final = project;
    project = await service.apply(project, { type: "evaluate_creative_scene", evaluator_id: original.id,
      evaluator_version: original.version, scene_id: project.scenes[0].id }); final = project;
    const baseline = project, checkpoints: unknown[] = [], restarts: unknown[] = [];
    observations.baseline = baseline; observations.checkpoints = checkpoints; observations.restarts = restarts;
    const observe = async (label: string) => {
      if (checkpoints.length >= 16) throw new Error("Evaluator scenario exceeded its 16 checkpoint bound.");
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) }); return final;
    };
    const restart = async (expected: Project) => {
      const before = await service.observeStoredProject(expected);
      for (const participant of services) await participant.close();
      // A distinct source-built service process opens the same quiescent SQLite
      // store. The archive runner must still verify a separate extracted copy.
      const reopened = await ScopedChangeService.create({ databasePath: service.databasePath, seedFixture: false, desktopInvokeFixture: true });
      services.push(reopened);
      const after = await reopened.observeStoredProject(expected);
      expect(after.project).toEqual(before.project); expect(after.journal).toEqual(before.journal);
      await reopened.close(); expect(reopened.processExits.at(-1)).toMatchObject({ code: 0, signal: null });
      restarts.push({ source: "distinct StudioService process opening original SQLite", project: after.project,
        journal: after.journal, beforeJournalSha256: digest(JSON.stringify(before.journal)),
        afterJournalSha256: digest(JSON.stringify(after.journal)), exits: reopened.processExits });
      await service.start(); await page.reload(); await revision(page, expected.revision); await open(page); final = after.project;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision); await open(page);
    await run({ service, services, baseline, initialCounts: mutationCounts(service), observations, cleanup, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (final) { try { final = await service.project(final.id); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, services.slice(1), [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("evaluator-experiment-service-evidence.json");
      await writeFile(path, JSON.stringify({ scope: "authored fixture through actual App + StudioService + grants + SQLite/CAS + complete journal",
        exclusions: ["installed Tauri", "native renderer", "human creative approval", "archive independent verification pending runner"],
        statusAtTeardown: info.status, expectedStatus: info.expectedStatus, observations, final,
        primaryFailure: primary ? detail(primary.reason) : null, failures: failures.map(detail), canonicalOracle: custody?.oracle ?? null,
        services: services.map(value => ({ databasePath: value.databasePath, binaryPath: value.binaryPath,
          processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, transcript: value.transcript, stderr: value.stderr })),
        browser: { retainedEntryLimit: 128, characterLimit: 2000, entries: browser },
      }, null, 2));
      await info.attach("evaluator-experiment-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Experiment behavior and closed SQLite custody retained separate failures", { cause: primary?.reason });
  }
}


const candidateField = (page: Page) => field(page, "Evaluator experimental candidate JSON");
const rationaleField = (page: Page) => field(page, "Experiment owner review rationale");
const comparison = (page: Page) => page.getByRole("region", { name: "Source-bound evaluator experiment", exact: true });
const registerButton = (page: Page) => button(page, "Register reviewed v2 via project CAS");
async function prepareCandidate(page: Page, reason = "I reviewed the exact technical differences and retained human abstention.") {
  await button(page, "Prepare a proposed v2 manifest").click();
  const candidate = JSON.parse(await candidateField(page).inputValue()) as CreativeEvaluatorBundle;
  const criterion = candidate.criteria.find(value => value.kind === "minimum_nodes");
  if (!criterion || criterion.kind !== "minimum_nodes") throw new Error("The actual arranged source lacks its bounded criterion.");
  criterion.minimum++; await candidateField(page).fill(JSON.stringify(candidate));
  await button(page, "Run disposable technical comparison").click(); await expect(comparison(page)).toBeVisible();
  await rationaleField(page).fill(reason); await page.getByLabel(/I have permission to register this original/).check();
  await page.getByLabel(/I reviewed the per-scene technical changes/).check();
  return candidate;
}

test("Experiment actual comparison and original registration preserve the visible baseline, source history and raw review through SQLite reopen", async ({ page }, info) => {
  await withExperiment(page, info, async ({ service, baseline, initialCounts, observations, observe, restart }) => {
    const source = baseline.production_design!.evaluator_bundles!.at(-1)!;
    const scenes = page.getByRole("group", { name: "Choose up to three existing scenes to compare", exact: true });
    await scenes.getByRole("checkbox", { name: baseline.scenes[1].name, exact: true }).check();
    const reason = "  I reviewed these exact original technical differences and the human abstentions.  ";
    const candidate = await prepareCandidate(page, reason), raw = await candidateField(page).inputValue();
    await expect(comparison(page)).toContainText("2 source scene(s)");
    await expect(comparison(page)).toContainText("Human questions always remain not assessed");
    expect(mutationCounts(service)).toEqual(initialCounts); expect(await observe("disposable comparison makes no Project write")).toEqual(baseline);
    await expect(registerButton(page)).toBeEnabled(); await registerButton(page).click(); await revision(page, baseline.revision + 1);
    await expect(savedNotice(page)).toBeVisible(); await expect(unknown(page)).toHaveCount(0); await expect(comparison(page)).toHaveCount(0);
    // The parent pins a selection when authoring starts. Appending v2 must not
    // silently switch this editor to a fresh v2 baseline before its original ACK.
    await expect(field(page, "Evaluator source version")).toHaveValue(source.id + ":1");
    await expect(candidateField(page)).toHaveValue(raw); await expect(rationaleField(page)).toHaveValue(reason);
    const saved = await observe("the original Project response confirms exactly one immutable candidate");
    expect(saved.production_design!.evaluator_bundles).toEqual([...(baseline.production_design!.evaluator_bundles ?? []), candidate]);
    expect(saved.production_design!.evaluation_reports).toEqual(baseline.production_design!.evaluation_reports); preserved(baseline, saved);
    const request = applies(service).slice(initialCounts.apply); expect(request).toHaveLength(1);
    expect(request[0].request.change).toEqual({ type: "register_creative_evaluator", bundle: candidate });
    expect(request[0].request.revision).toBe(baseline.revision);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual([request[0].request.change]);
    const retained = await ownCopy(page, baseline); expect(retained.copy.draft.pending).toBeNull(); observations.reviewInput = retained;
    observations.candidateSha256 = await evaluatorDigest(candidate);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    await restart(saved); await expect(candidateField(page)).toHaveValue(raw); await expect(rationaleField(page)).toHaveValue(reason);
    await expect(field(page, "Evaluator source version")).toHaveValue(source.id + ":1");
    await expect(scenes.getByRole("checkbox", { name: baseline.scenes[1].name, exact: true })).toBeChecked();
    expect(await page.evaluate(key => localStorage.getItem(key), retained.key)).toBe(retained.raw);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("Experiment actual concurrent authority rejection remains UNKNOWN until fresh comparison, saved-history review and a separate registration", async ({ page }, info) => {
  await withExperiment(page, info, async ({ service, services, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const peer = await ScopedChangeService.create({ databasePath: service.databasePath, seedFixture: false, desktopInvokeFixture: true });
    services.push(peer); const original = service.invoke.bind(service), concurrent = deferred<Project>(); void concurrent.promise.catch(() => undefined);
    let armed = true; cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyRequest | undefined;
      if (armed && command === "apply_change" && request?.change.type === "register_creative_evaluator") {
        armed = false;
        try {
          const current = await peer.project(baseline.id);
          concurrent.resolve(await peer.apply(current, { type: "set_lock", resource: "project:" + current.id,
            kind: "content", note: "Actual concurrent lock after the experiment App grant" }));
        } catch (reason) { concurrent.reject(reason); throw reason; }
      }
      return original<T>(command, args);
    };
    const candidate = await prepareCandidate(page), raw = await candidateField(page).inputValue(); await registerButton(page).click();
    const locked = await bounded(concurrent.promise, "Concurrent experiment authority change");
    await expect(recovery(page).getByRole("alert")).toBeVisible(); await expect(unknown(page)).toContainText("UNKNOWN");
    await expect(savedNotice(page)).toHaveCount(0); await expect(registerButton(page)).toBeDisabled(); await expect(candidateField(page)).toHaveValue(raw);
    const firstAttempt = applies(service).slice(initialCounts.apply); expect(firstAttempt).toHaveLength(1);
    const rejection = service.transcript.find(entry => entry.direction === "response" && "ok" in entry.payload &&
      entry.payload.id === firstAttempt[0].rpc.id && entry.payload.ok === false);
    expect(rejection).toBeDefined(); observations.originalRejection = rejection;
    expect(await observe("first registration was actually rejected; only the concurrent lock exists")).toEqual(locked);
    expect(locked.production_design).toEqual(baseline.production_design);
    const oldPending = structuredClone((await ownCopy(page, baseline)).copy.draft.pending);
    const lock = locked.locks.find(value => value.resource === "project:" + baseline.id && value.kind === "content");
    if (!lock) throw new Error("The actual concurrent lock was not found for explicit release.");
    const released = await peer.apply(locked, { type: "remove_lock", lock_id: lock.id });
    service.invoke = original; await page.reload(); await revision(page, released.revision); await open(page);
    await expect(unknown(page)).toContainText("UNKNOWN"); await expect(candidateField(page)).toHaveValue(raw);
    await button(page, "Run disposable technical comparison").click(); await expect(comparison(page)).toBeVisible();
    await rationaleField(page).fill("I reviewed the current original source after its explicit lock release.");
    await page.getByLabel(/I have permission to register this original/).check(); await page.getByLabel(/I reviewed the per-scene technical changes/).check();
    await expect(registerButton(page)).toBeDisabled();
    await button(page, "Inspect current experiment project and history").click();
    await expect(unknown(page)).toContainText("0 journal changes match the original registration");
    expect((await ownCopy(page, baseline)).copy.draft.pending).toEqual(oldPending);
    await expect(registerButton(page)).toBeDisabled();
    await field(page, "Confirm separate experiment registration").check(); await expect(registerButton(page)).toBeEnabled();
    const previousUnknown = await ownCopy(page, baseline);
    await registerButton(page).click(); await revision(page, released.revision + 1);
    await expect(savedNotice(page)).toBeVisible(); await expect(unknown(page)).toHaveCount(0);
    const saved = await observe("a separately authorized original request registers the candidate once");
    expect(saved.production_design!.evaluator_bundles).toEqual([...(baseline.production_design!.evaluator_bundles ?? []), candidate]);
    expect(saved.production_design!.evaluation_reports).toEqual(baseline.production_design!.evaluation_reports); preserved(baseline, saved);
    const attempts = applies(service).slice(initialCounts.apply); expect(attempts).toHaveLength(2);
    expect(attempts[1].request.request_id).not.toBe(attempts[0].request.request_id);
    expect(attempts[1].request.effect_grant).not.toBe(attempts[0].request.effect_grant); expect(attempts[1].request.revision).toBe(released.revision);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["set_lock", "remove_lock", "register_creative_evaluator"]);
    expect(await page.evaluate(key => localStorage.getItem(key), previousUnknown.key)).toBe(previousUnknown.raw);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 2, projectEditGrants: initialCounts.projectEditGrants + 2 });
    observations.previousUnknown = previousUnknown; await restart(saved);
    await expect(candidateField(page)).toHaveValue(raw); await expect(unknown(page)).toHaveCount(0);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 2, projectEditGrants: initialCounts.projectEditGrants + 2 });
  });
});

test("Experiment actual delayed original ACK preserves later blank candidate, rationale and independent scene choices", async ({ page }, info) => {
  await withExperiment(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const candidate = await prepareCandidate(page), hold = holdRegistration(service, false); cleanup.push(() => hold.dispose());
    await registerButton(page).click(); const committed = await bounded(hold.observed, "Actual experiment commit before delayed delivery");
    const armed = await ownCopy(page, baseline); expect(armed.copy.draft.pending?.outcome).toBe("UNKNOWN");
    await candidateField(page).fill("Later author input before a deliberate blank"); await candidateField(page).fill("");
    await rationaleField(page).fill("");
    const scenes = page.getByRole("group", { name: "Choose up to three existing scenes to compare", exact: true });
    await scenes.getByRole("checkbox", { name: baseline.scenes[0].name, exact: true }).uncheck();
    await scenes.getByRole("checkbox", { name: baseline.scenes[1].name, exact: true }).check();
    hold.release(); await revision(page, committed.revision); await expect(savedNotice(page)).toBeVisible(); await expect(unknown(page)).toHaveCount(0);
    await expect(candidateField(page)).toHaveValue(""); await expect(rationaleField(page)).toHaveValue("");
    await expect(scenes.getByRole("checkbox", { name: baseline.scenes[0].name, exact: true })).not.toBeChecked();
    await expect(scenes.getByRole("checkbox", { name: baseline.scenes[1].name, exact: true })).toBeChecked();
    const later = await ownCopy(page, baseline); expect(later.copy.draft.pending).toBeNull();
    expect(later.copy.draft.fields.candidateText.raw).toBe(""); expect(later.copy.draft.fields.reviewerReason.raw).toBe("");
    expect(later.copy.draft.fields.candidateText.sequence).toBeGreaterThan(armed.copy.draft.pending!.sentFields.candidateText.sequence);
    expect(JSON.parse(later.copy.draft.fields.selectedScenes.raw)).toEqual([baseline.scenes[1].id]);
    expect(await page.evaluate(key => localStorage.getItem(key), armed.key)).toBe(armed.raw);
    expect(await observe("the exact original candidate is saved while later source input remains local")).toEqual(committed);
    expect(committed.production_design!.evaluator_bundles!.at(-1)).toEqual(candidate); preserved(baseline, committed);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    observations.originalArmed = armed; observations.laterInput = later; await hold.dispose(); await restart(committed);
    await expect(candidateField(page)).toHaveValue(""); await expect(rationaleField(page)).toHaveValue("");
    expect((await ownCopy(page, baseline)).copy.draft.fields).toEqual(later.copy.draft.fields);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("Experiment actual UNKNOWN and later input survive failed first recovery read, explicit Retry and saved-result observation without replay", async ({ page }, info) => {
  await withExperiment(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    await prepareCandidate(page); const hold = holdRegistration(service, true); cleanup.push(() => hold.dispose());
    await registerButton(page).click(); const committed = await bounded(hold.observed, "Actual experiment before lost delivery"); hold.release();
    await expect(recovery(page).getByRole("alert")).toContainText("original response delivery lost");
    const laterRaw = "  {\"later candidate\":\"unfinished\",\n";
    await candidateField(page).fill(laterRaw); await rationaleField(page).fill("Independent review words retained before reload.");
    const original = await ownCopy(page, baseline), pending = structuredClone(original.copy.draft.pending);
    expect(pending?.outcome).toBe("UNKNOWN"); const journal = await service.history(baseline.id, baseline.revision);
    const source = baseline.production_design!.evaluator_bundles!.at(-1)!;
    await page.addInitScript(key => {
      const get = Storage.prototype.getItem; let first = true; window.__EXPERIMENT_SERVICE_INITIAL_READ_FAILURES__ = 0;
      Storage.prototype.getItem = function(name) {
        if (this === sessionStorage && name === key && first) {
          first = false; window.__EXPERIMENT_SERVICE_INITIAL_READ_FAILURES__!++;
          throw new Error("Controlled initial experiment pointer read unavailable");
        }
        return get.call(this, name);
      };
      window.__EXPERIMENT_SERVICE_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = get; };
    }, experimentPointerKey(experimentScope(baseline, source)));
    await page.reload(); await revision(page, committed.revision); await open(page);
    await expect(button(page, "Retry experiment recovery")).toBeVisible();
    expect(await page.evaluate(() => window.__EXPERIMENT_SERVICE_INITIAL_READ_FAILURES__)).toBe(1);
    await rationaleField(page).fill("Later words while original input is inaccessible"); await rationaleField(page).fill("");
    await button(page, "Retry experiment recovery").click(); await expect(button(page, "Retry experiment recovery")).toHaveCount(0);
    await expect(unknown(page)).toContainText("UNKNOWN"); await expect(candidateField(page)).toHaveValue(laterRaw); await expect(rationaleField(page)).toHaveValue("");
    const recovered = await ownCopy(page, baseline); expect(recovered.copy.draft.pending).toEqual(pending);
    expect(recovered.copy.draft.fields.reviewerReason.raw).toBe("");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    const reads = calls(service, "project_history").length;
    await button(page, "Inspect current experiment project and history").click();
    await expect(unknown(page)).toContainText("1 journal changes match the original registration");
    expect(calls(service, "project_history").length).toBeGreaterThan(reads);
    await expect(savedNotice(page)).toHaveCount(0); await expect(registerButton(page)).toBeDisabled();
    expect((await ownCopy(page, baseline)).copy.draft.pending).toEqual(pending);
    expect(await observe("matching saved observation retains the original UNKNOWN and later input")).toEqual(committed);
    expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    observations.original = original; observations.recovered = recovered; observations.savedJournal = journal;
    await page.evaluate(() => window.__EXPERIMENT_SERVICE_RESTORE_STORAGE__?.()); await hold.dispose(); await restart(committed);
    await button(page, "Retry experiment recovery").click(); await expect(unknown(page)).toBeVisible();
    await expect(candidateField(page)).toHaveValue(laterRaw); await expect(rationaleField(page)).toHaveValue("");
    expect((await ownCopy(page, baseline)).copy.draft.pending).toEqual(pending);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("Experiment observed corrupt source rejects valid same-writer replacement and permits exact download without grants or SQLite changes", async ({ page }, info) => {
  await withExperiment(page, info, async ({ service, baseline, initialCounts, observations, observe }) => {
    const candidate = await prepareCandidate(page), original = await ownCopy(page, baseline), corrupt = "{original corrupt experiment recovery bytes\n";
    await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: corrupt });
    await page.reload(); await revision(page, baseline.revision); await open(page); await expect(button(page, "Retry experiment recovery")).toBeVisible();
    await candidateField(page).fill(JSON.stringify(candidate)); await rationaleField(page).fill("Preserve this current reviewed input while the original is unreadable.");
    const replacement = structuredClone(original.copy); replacement.draft.fields = {}; replacement.draft.pending = null; replacement.draft.sequence = 0;
    const replacementRaw = JSON.stringify(replacement);
    await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: replacementRaw });
    await button(page, "Retry experiment recovery").click();
    await expect(recovery(page).getByRole("alert")).toContainText("original experiment recovery bytes changed");
    // A valid comparison is still read-only. It must not let a same-writer
    // replacement bypass the failed original recovery before dispatch.
    await button(page, "Run disposable technical comparison").click(); await expect(comparison(page)).toBeVisible();
    await page.getByLabel(/I have permission to register this original/).check(); await page.getByLabel(/I reviewed the per-scene technical changes/).check();
    await expect(registerButton(page)).toBeDisabled(); await expect(savedNotice(page)).toHaveCount(0);
    await page.getByText("Experiment recovery copies", { exact: true }).click();
    const downloaded = page.waitForEvent("download"); await button(page, "Download original experiment recovery bytes").click();
    const path = await (await downloaded).path(); expect(path).not.toBeNull(); const bytes = await readFile(path!, "utf8"); expect(bytes).toBe(corrupt);
    const retainedPath = info.outputPath("original-corrupt-experiment-bytes.txt"); await writeFile(retainedPath, bytes);
    await info.attach("original-corrupt-experiment-bytes", { path: retainedPath, contentType: "text/plain" });
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(replacementRaw);
    expect(await observe("valid read-only comparison did not authorize destructive recovery or a service mutation")).toEqual(baseline);
    expect(await service.history(baseline.id, baseline.revision)).toEqual([]); expect(mutationCounts(service)).toEqual(initialCounts);
    observations.original = original; observations.corrupt = { raw: corrupt, sha256: digest(corrupt) };
    observations.replacement = { raw: replacementRaw, sha256: digest(replacementRaw) }; observations.downloadSha256 = digest(bytes);
  });
});

test("Experiment duplicate actual App windows retain separate candidates and review input through reload without service writes", async ({ page }, info) => {
  await withExperiment(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe }) => {
    const firstCandidate = await prepareCandidate(page, "First window original local review reasoning."), original = await ownCopy(page, baseline);
    const second = await page.context().newPage(); cleanup.push(() => second.close()); await service.attachFixturePage(second, baseline.id);
    const source = baseline.production_design!.evaluator_bundles!.at(-1)!;
    await second.addInitScript(({ key, writer }) => { if (sessionStorage.getItem(key) === null) sessionStorage.setItem(key, writer); }, {
      key: experimentPointerKey(experimentScope(baseline, source)), writer: original.copy.writer,
    });
    await second.goto("/"); await revision(second, baseline.revision); await open(second);
    await expect(candidateField(second)).toHaveValue(JSON.stringify(firstCandidate));
    const secondCandidate = { ...structuredClone(firstCandidate), label: "Second window original candidate" };
    await candidateField(second).fill(JSON.stringify(secondCandidate)); await rationaleField(second).fill("Second window independent local review reasoning.");
    const independent = await ownCopy(second, baseline); expect(independent.key).not.toBe(original.key);
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    await page.reload(); await revision(page, baseline.revision); await open(page);
    await second.reload(); await revision(second, baseline.revision); await open(second);
    await expect(candidateField(page)).toHaveValue(JSON.stringify(firstCandidate)); await expect(rationaleField(page)).toHaveValue("First window original local review reasoning.");
    await expect(candidateField(second)).toHaveValue(JSON.stringify(secondCandidate)); await expect(rationaleField(second)).toHaveValue("Second window independent local review reasoning.");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await second.evaluate(key => localStorage.getItem(key), independent.key)).toBe(independent.raw);
    expect(await observe("both windows reopen their own retained inputs without domain writes")).toEqual(baseline);
    expect(await service.history(baseline.id, baseline.revision)).toEqual([]); expect(mutationCounts(service)).toEqual(initialCounts);
    observations.first = original; observations.second = independent;
  });
});
