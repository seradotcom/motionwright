import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { evaluatorDigest, type CreativeEvaluatorBundle } from "../src/creativeEvaluator";
import { emptyProductionDesign } from "../src/creativeProduction";
import { evaluatorCopyKey, evaluatorPointerKey, evaluatorScope, type EvaluatorCopy } from "../src/creativeEvaluatorDraft";
import { ScopedChangeService, type ServiceRpcRequest, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> original coordinator/api.ts -> source-built StudioService ->
// project_edit grants -> SQLite/CAS/journal. Authored fixture data is labelled;
// fault controls only delay/loss of a real response or actual browser Storage.
// No synthetic Project response, installed Tauri or native/aesthetic claim.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Requires the source-built StudioService executable; no simulated persistence fallback.");
test.setTimeout(120_000);
declare global { interface Window {
  __EVALUATOR_SERVICE_RESTORE_STORAGE__?: () => void;
  __EVALUATOR_SERVICE_INITIAL_READ_FAILURES__?: number;
} }
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const recovery = (page: Page) => page.getByRole("region", { name: "Evaluator author input recovery", exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Unconfirmed evaluator operation", exact: true });
const savedNotice = (page: Page) => recovery(page).getByRole("status").filter({ hasText: "Saved evaluator input at r" });
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
async function importInput(page: Page, value: CreativeEvaluatorBundle) {
  await field(page, "Import evaluator JSON").fill(JSON.stringify(value));
  await field(page, "Confirm evaluator source rights").check();
  await expect(button(page, "Import original evaluator JSON")).toBeEnabled();
}
async function ownCopy(page: Page, project: Project) {
  const scope = evaluatorScope(project), writer = await page.evaluate(key => sessionStorage.getItem(key), evaluatorPointerKey(scope));
  if (!writer) throw new Error("The evaluator input has no confirmed writer pointer.");
  const key = evaluatorCopyKey(scope, writer), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("The selected evaluator writer has no retained bytes.");
  const copy = JSON.parse(raw) as EvaluatorCopy;
  expect(copy.schema).toBe("motionwright.evaluator-copy/1"); expect(copy.writer).toBe(writer); expect(copy.draft.scope).toBe(scope);
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
interface EvaluatorCase {
  service: ScopedChangeService; services: ScopedChangeService[]; baseline: Project;
  initialCounts: ReturnType<typeof mutationCounts>; observations: Record<string, unknown>;
  cleanup: (() => Promise<unknown>)[]; observe(label: string): Promise<Project>; restart(expected: Project): Promise<void>;
}
async function withEvaluator(page: Page, info: TestInfo, run: (value: EvaluatorCase) => Promise<void>) {
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
    if (!project.scenes.length) throw new Error("The actual selected fixture has no editable scene.");
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
      const path = info.outputPath("creative-evaluator-service-evidence.json");
      await writeFile(path, JSON.stringify({ scope: "authored fixture through actual App + StudioService + grants + SQLite/CAS + complete journal",
        exclusions: ["installed Tauri", "native renderer", "human creative approval", "archive independent verification pending runner"],
        statusAtTeardown: info.status, expectedStatus: info.expectedStatus, observations, final,
        primaryFailure: primary ? detail(primary.reason) : null, failures: failures.map(detail), canonicalOracle: custody?.oracle ?? null,
        services: services.map(value => ({ databasePath: value.databasePath, binaryPath: value.binaryPath,
          processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, transcript: value.transcript, stderr: value.stderr })),
        browser: { retainedEntryLimit: 128, characterLimit: 2000, entries: browser },
      }, null, 2));
      await info.attach("creative-evaluator-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Evaluator behavior and closed SQLite custody retained separate failures", { cause: primary?.reason });
  }
}

test("Evaluator actual author, immutable import and source-bound evaluation survive complete SQLite reopen", async ({ page }, info) => {
  await withEvaluator(page, info, async ({ service, baseline, initialCounts, observations, observe, restart }) => {
    // An existing saved selection can be evaluated without an artificial edit
    // merely to initialize recovery storage.
    await expect(button(page, "Evaluate authored scene")).toBeEnabled();
    await button(page, "Evaluate authored scene").click(); await revision(page, baseline.revision + 1);
    await expect(savedNotice(page)).toBeVisible(); await expect(unknown(page)).toHaveCount(0);
    const untouchedEvaluation = await observe("existing saved evaluator acknowledged without changing any author field");
    const label = "  My exact reusable evaluator  ", author = "I authored and may publish this original checklist.";
    await field(page, "Evaluator label").fill(label); await field(page, "Evaluator author declaration").fill(author);
    await button(page, "Register data-only evaluator").click(); await revision(page, baseline.revision + 2);
    await expect(savedNotice(page)).toBeVisible(); const registered = await observe("author registers actual immutable version one");
    const v1 = registered.production_design!.evaluator_bundles!.at(-1)!;
    expect(v1.label).toBe(label.trim()); expect(v1.author_attestation).toBe(author); expect(v1.version).toBe(1);
    expect(registered.production_design!.evaluator_bundles!.slice(0, -1)).toEqual(baseline.production_design!.evaluator_bundles);
    const v2: CreativeEvaluatorBundle = { ...structuredClone(v1), version: 2, label: "Explicit reusable version two" };
    await importInput(page, v2); await button(page, "Import original evaluator JSON").click();
    await revision(page, baseline.revision + 3); await expect(savedNotice(page)).toBeVisible();
    await expect(field(page, "Import evaluator JSON")).toHaveValue(""); await expect(field(page, "Confirm evaluator source rights")).not.toBeChecked();
    await expect(field(page, "Evaluator source version")).toHaveValue(v2.id + ":2");
    const invalidLater = "  {\"next author input\":\n";
    await field(page, "Import evaluator JSON").fill(invalidLater);
    await button(page, "Evaluate authored scene").click(); await revision(page, baseline.revision + 4);
    await expect(savedNotice(page)).toBeVisible(); await expect(unknown(page)).toHaveCount(0);
    const saved = await observe("evaluation records exact source version without consuming unrelated import input");
    expect(saved.production_design!.evaluator_bundles).toEqual([...(baseline.production_design!.evaluator_bundles ?? []), v1, v2]);
    const reports = saved.production_design!.evaluation_reports!, oldReports = baseline.production_design!.evaluation_reports ?? [];
    expect(reports.slice(0, oldReports.length)).toEqual(oldReports); expect(reports).toHaveLength(oldReports.length + 2);
    expect(reports[oldReports.length]).toEqual(untouchedEvaluation.production_design!.evaluation_reports!.at(-1));
    const report = reports.at(-1)!;
    expect(report.evaluator_id).toBe(v2.id); expect(report.evaluator_version).toBe(2);
    expect(report.evaluator_sha256).toBe(await evaluatorDigest(v2)); expect(report.source_revision).toBe(baseline.revision + 3);
    expect(report.source_generation).toBe(baseline.generation); expect(report.scene_id).toBe(baseline.scenes[0].id);
    expect(report.creative_approval).toBe("not_assessed");
    const question = v2.criteria.find(value => value.kind === "human_question")!;
    expect(report.observations.find(value => value.criterion_id === question.id)?.outcome).toBe("not_assessed");
    await expect(field(page, "Evaluator label")).toHaveValue(label); await expect(field(page, "Evaluator author declaration")).toHaveValue(author);
    await expect(field(page, "Import evaluator JSON")).toHaveValue(invalidLater); preserved(baseline, saved);
    const requests = applies(service).slice(initialCounts.apply);
    expect(requests.map(value => value.request.change.type)).toEqual([
      "evaluate_creative_scene", "register_creative_evaluator", "register_creative_evaluator", "evaluate_creative_scene",
    ]);
    expect(new Set(requests.map(value => value.request.request_id)).size).toBe(4);
    expect(new Set(requests.map(value => value.request.effect_grant)).size).toBe(4);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual(requests.map(value => value.request.change));
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 4, projectEditGrants: initialCounts.projectEditGrants + 4 });
    const copy = await ownCopy(page, saved); observations.lastAuthorCopy = copy; await restart(saved);
    await expect(field(page, "Evaluator label")).toHaveValue(label); await expect(field(page, "Import evaluator JSON")).toHaveValue(invalidLater);
    expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
    expect((await ownCopy(page, saved)).copy.draft.pending).toBeNull();
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 4, projectEditGrants: initialCounts.projectEditGrants + 4 });
  });
});

test("Evaluator actual concurrent SQLite authority rejection retains import input and never announces registration", async ({ page }, info) => {
  await withEvaluator(page, info, async ({ service, services, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const peer = await ScopedChangeService.create({ databasePath: service.databasePath, seedFixture: false, desktopInvokeFixture: true });
    services.push(peer); const original = service.invoke.bind(service), changed = deferred<Project>();
    void changed.promise.catch(() => undefined); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyRequest | undefined;
      if (armed && command === "apply_change" && request?.change.type === "register_creative_evaluator") {
        armed = false;
        try {
          // The App already obtained a real grant. A second actual service now
          // changes the source before forwarding the original exact apply.
          const current = await peer.project(baseline.id);
          const locked = await peer.apply(current, { type: "set_lock", resource: "project:" + current.id,
            kind: "content", note: "Actual concurrent author content lock for the rejection scenario" });
          changed.resolve(locked);
        } catch (reason) { changed.reject(reason); throw reason; }
      }
      return original<T>(command, args);
    };
    const value = originalBundle("Input refused by current service authority"), raw = JSON.stringify(value);
    await importInput(page, value); await button(page, "Import original evaluator JSON").click();
    const locked = await bounded(changed.promise, "Concurrent service lock");
    await expect(recovery(page).getByRole("alert")).toBeVisible(); await expect(unknown(page)).toContainText("UNKNOWN");
    await expect(savedNotice(page)).toHaveCount(0); await expect(field(page, "Import evaluator JSON")).toHaveValue(raw);
    await expect(field(page, "Confirm evaluator source rights")).toBeChecked();
    const attempts = applies(service).slice(initialCounts.apply); expect(attempts).toHaveLength(1);
    const actualRejection = service.transcript.find(entry => entry.direction === "response" &&
      "ok" in entry.payload && entry.payload.id === attempts[0].rpc.id && entry.payload.ok === false);
    expect(actualRejection).toBeDefined(); observations.actualServiceRejection = actualRejection;
    const stored = await observe("only the concurrent actual lock exists; import was refused by the backend");
    expect(stored).toEqual(locked); expect(stored.production_design).toEqual(baseline.production_design);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["set_lock"]);
    const retained = await ownCopy(page, baseline); expect(retained.copy.draft.pending?.changeJson).toBe(JSON.stringify(attempts[0].request.change));
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    service.invoke = original; observations.retainedAfterRejection = retained; await restart(stored);
    await expect(field(page, "Import evaluator JSON")).toHaveValue(raw); await expect(unknown(page)).toBeVisible();
    await expect(button(page, "Import original evaluator JSON")).toBeDisabled(); await expect(savedNotice(page)).toHaveCount(0);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("Evaluator actual delayed import ACK preserves later blank input and a changed selected version", async ({ page }, info) => {
  await withEvaluator(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const value = originalBundle("Original import held after durable commit"), hold = holdRegistration(service, false);
    cleanup.push(() => hold.dispose()); await importInput(page, value);
    await button(page, "Import original evaluator JSON").click(); const committed = await bounded(hold.observed, "Actual evaluator import");
    const armed = await ownCopy(page, baseline); expect(armed.copy.draft.pending?.outcome).toBe("UNKNOWN");
    await field(page, "Import evaluator JSON").fill("A later input before deliberately returning to blank");
    await field(page, "Import evaluator JSON").fill(""); await field(page, "Confirm evaluator source rights").uncheck();
    await field(page, "Evaluator source version").selectOption("");
    hold.release(); await revision(page, committed.revision); await expect(savedNotice(page)).toBeVisible();
    await expect(unknown(page)).toHaveCount(0); await expect(field(page, "Import evaluator JSON")).toHaveValue("");
    await expect(field(page, "Evaluator source version")).toHaveValue(""); await expect(field(page, "Confirm evaluator source rights")).not.toBeChecked();
    const retained = await ownCopy(page, committed);
    expect(retained.copy.draft.pending).toBeNull(); expect(retained.copy.draft.fields.importText.raw).toBe("");
    expect(retained.copy.draft.fields.importText.sequence).toBeGreaterThan(armed.copy.draft.pending!.sentFields.importText.sequence);
    expect(retained.copy.draft.fields.selected.raw).toBe("");
    expect(await page.evaluate(key => localStorage.getItem(key), armed.key)).toBe(armed.raw);
    expect(await observe("the exact original commit plus later unsubmitted blank input")).toEqual(committed);
    expect(committed.production_design!.evaluator_bundles!.at(-1)).toEqual(value); preserved(baseline, committed);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    observations.armed = armed; observations.later = retained; await hold.dispose(); await restart(committed);
    await expect(field(page, "Import evaluator JSON")).toHaveValue(""); await expect(field(page, "Evaluator source version")).toHaveValue("");
    expect((await ownCopy(page, committed)).copy.draft.fields.importText).toEqual(retained.copy.draft.fields.importText);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("Evaluator real UNKNOWN survives first recovery read failure and explicit Retry without replay or clearing later edits", async ({ page }, info) => {
  await withEvaluator(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const value = originalBundle("Actual import before original response delivery loss"), hold = holdRegistration(service, true);
    cleanup.push(() => hold.dispose()); await importInput(page, value); await button(page, "Import original evaluator JSON").click();
    const committed = await bounded(hold.observed, "Actual evaluator commit before lost reply"); hold.release();
    await expect(recovery(page).getByRole("alert")).toContainText("original response delivery lost");
    await field(page, "Import evaluator JSON").fill("Later import retained before reload");
    await field(page, "Evaluator purpose").fill("Independent author purpose retained before reload");
    const original = await ownCopy(page, baseline), pending = structuredClone(original.copy.draft.pending);
    expect(pending?.outcome).toBe("UNKNOWN"); const journal = await service.history(baseline.id, baseline.revision);
    expect(journal.map(event => event.change)).toEqual([applies(service).at(-1)!.request.change]);
    await page.addInitScript(key => {
      const get = Storage.prototype.getItem; let failed = false;
      window.__EVALUATOR_SERVICE_INITIAL_READ_FAILURES__ = 0;
      Storage.prototype.getItem = function(name) {
        if (this === sessionStorage && name === key && !failed) {
          failed = true; window.__EVALUATOR_SERVICE_INITIAL_READ_FAILURES__!++;
          throw new Error("Controlled first evaluator pointer read unavailable");
        }
        return get.call(this, name);
      };
      window.__EVALUATOR_SERVICE_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = get; };
    }, evaluatorPointerKey(evaluatorScope(baseline)));
    await page.reload(); await revision(page, committed.revision); await open(page);
    expect(await page.evaluate(() => window.__EVALUATOR_SERVICE_INITIAL_READ_FAILURES__)).toBe(1);
    await expect(button(page, "Retry evaluator recovery")).toBeVisible();
    await field(page, "Import evaluator JSON").fill("Typed while the earlier source is unreadable");
    await field(page, "Import evaluator JSON").fill("");
    await field(page, "Evaluator label").fill("Later independent author label");
    await expect(button(page, "Register data-only evaluator")).toBeDisabled();
    await button(page, "Retry evaluator recovery").click(); await expect(button(page, "Retry evaluator recovery")).toHaveCount(0);
    await expect(unknown(page)).toContainText("UNKNOWN"); await expect(field(page, "Import evaluator JSON")).toHaveValue("");
    await expect(field(page, "Evaluator purpose")).toHaveValue("Independent author purpose retained before reload");
    await expect(field(page, "Evaluator label")).toHaveValue("Later independent author label");
    const recovered = await ownCopy(page, baseline); expect(recovered.copy.draft.pending).toEqual(pending);
    expect(recovered.copy.draft.fields.importText.raw).toBe("");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    const reads = calls(service, "project_history").length;
    await button(page, "Read saved evaluator result").click();
    await expect(unknown(page)).toContainText("1 journal changes match the retained operation");
    expect(calls(service, "project_history").length).toBeGreaterThan(reads);
    await expect(savedNotice(page)).toHaveCount(0); expect((await ownCopy(page, baseline)).copy.draft.pending).toEqual(pending);
    expect(await observe("reading a matching saved result does not ACK or resend UNKNOWN")).toEqual(committed);
    expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    observations.original = original; observations.recovered = recovered; observations.savedJournal = journal;
    await page.evaluate(() => window.__EVALUATOR_SERVICE_RESTORE_STORAGE__?.()); await hold.dispose(); await restart(committed);
    // The init fault deliberately occurs again on reload. Explicit recovery of
    // the same bytes must remain possible without promoting the observed result.
    await button(page, "Retry evaluator recovery").click(); await expect(unknown(page)).toBeVisible();
    await expect(field(page, "Import evaluator JSON")).toHaveValue("");
    expect((await ownCopy(page, baseline)).copy.draft.pending).toEqual(pending);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("Evaluator observed corrupt bytes reject same-writer replacement and remain exactly downloadable without service mutation", async ({ page }, info) => {
  await withEvaluator(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe }) => {
    const value = originalBundle("Actual UNKNOWN before corrupted local source"), hold = holdRegistration(service, true);
    cleanup.push(() => hold.dispose()); await importInput(page, value); await button(page, "Import original evaluator JSON").click();
    const committed = await bounded(hold.observed, "Actual import preceding corrupted recovery"); hold.release();
    await expect(recovery(page).getByRole("alert")).toContainText("original response delivery lost");
    await field(page, "Import evaluator JSON").fill("Later input after actual lost response");
    const original = await ownCopy(page, baseline), journal = await service.history(baseline.id, baseline.revision);
    expect(original.copy.draft.pending?.outcome).toBe("UNKNOWN");
    const corrupt = "{original corrupt evaluator bytes after actual SQLite commit\n";
    await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: corrupt });
    await page.reload(); await revision(page, committed.revision); await open(page);
    await expect(button(page, "Retry evaluator recovery")).toBeVisible();
    const later = JSON.stringify(originalBundle("Current input while original bytes are unreadable"));
    await field(page, "Import evaluator JSON").fill(later); await field(page, "Confirm evaluator source rights").check();
    const replacement = structuredClone(original.copy);
    replacement.draft.pending = null; replacement.draft.fields = {}; replacement.draft.sequence = 0;
    const replacementRaw = JSON.stringify(replacement);
    await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: replacementRaw });
    await button(page, "Retry evaluator recovery").click();
    await expect(button(page, "Import original evaluator JSON")).toBeDisabled();
    await expect(recovery(page).getByRole("alert")).toContainText("original evaluator recovery bytes changed");
    await expect(field(page, "Import evaluator JSON")).toHaveValue(later); await expect(savedNotice(page)).toHaveCount(0);
    await page.getByText("Evaluator recovery copies", { exact: true }).click();
    const download = page.waitForEvent("download"); await button(page, "Download original evaluator recovery bytes").click();
    const path = await (await download).path(); expect(path).not.toBeNull();
    const downloaded = await readFile(path!, "utf8"); expect(downloaded).toBe(corrupt);
    const retainedPath = info.outputPath("original-corrupt-evaluator-bytes.txt"); await writeFile(retainedPath, downloaded);
    await info.attach("original-corrupt-evaluator-bytes", { path: retainedPath, contentType: "text/plain" });
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(replacementRaw);
    expect(await observe("corrupt original retained; replacing its writer did not authorize save")).toEqual(committed);
    expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
    observations.originalUnknown = original; observations.corrupt = { raw: corrupt, sha256: digest(corrupt) };
    observations.replacement = { raw: replacementRaw, sha256: digest(replacementRaw) }; observations.downloadSha256 = digest(downloaded);
  });
});

test("Evaluator duplicate windows preserve independent raw drafts and exact original bytes with no service writes", async ({ page }, info) => {
  await withEvaluator(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe }) => {
    await field(page, "Evaluator label").fill("First window original input");
    await field(page, "Import evaluator JSON").fill("  { first incomplete JSON\n");
    const original = await ownCopy(page, baseline), second = await page.context().newPage();
    cleanup.push(() => second.close()); await service.attachFixturePage(second, baseline.id);
    await second.addInitScript(({ key, writer }) => sessionStorage.setItem(key, writer), {
      key: evaluatorPointerKey(evaluatorScope(baseline)), writer: original.copy.writer,
    });
    await second.goto("/"); await revision(second, baseline.revision); await open(second);
    await expect(field(second, "Evaluator label")).toHaveValue("First window original input");
    await field(second, "Evaluator label").fill("Second window separate input");
    await field(second, "Import evaluator JSON").fill("  { second incomplete JSON\n");
    const separate = await ownCopy(second, baseline); expect(separate.key).not.toBe(original.key);
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    await expect(field(page, "Evaluator label")).toHaveValue("First window original input");
    await page.reload(); await revision(page, baseline.revision); await open(page);
    await expect(field(page, "Import evaluator JSON")).toHaveValue("  { first incomplete JSON\n");
    await expect(field(second, "Import evaluator JSON")).toHaveValue("  { second incomplete JSON\n");
    expect(await page.evaluate(key => localStorage.getItem(key), separate.key)).toBe(separate.raw);
    expect(await observe("two windows preserve separate inputs without domain writes")).toEqual(baseline);
    expect(await service.history(baseline.id, baseline.revision)).toEqual([]); expect(mutationCounts(service)).toEqual(initialCounts);
    observations.first = original; observations.second = separate;
  });
});
