import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import type { BrandProfile } from "../src/brandGovernance";
import { defaultHeroConfig } from "../src/creativeProduction";
import {
  BRAND_COPY_PREFIX, brandCopyKey, brandPointerKey, brandScope, decodeBrandCopy,
  type BrandCopy, type BrandTarget, type CampaignRaw, type GovernanceRaw,
} from "../src/brandAuthoringDraft";
import { ScopedChangeService, type ServiceRpcRequest, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> existing coordinator/api.ts -> StudioService -> grants/SQLite/journal.
// All source arrangements use public domain changes. Faults affect only a real
// reply's delivery or browser Storage; no Project or recovery JSON is injected.
// This does not certify installed Tauri, native renderer output or human taste.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Requires the declared source-built StudioService executable; no simulated persistence fallback.");
test.setTimeout(120_000);

declare global {
  interface Window {
    __BRAND_SERVICE_RESTORE_STORAGE__?: () => void;
    __BRAND_SERVICE_DROPPED_WRITES__?: number;
    __BRAND_SERVICE_BLOCKED_READS__?: number;
  }
}

const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
const field = (scope: Page | Locator, name: string) => scope.getByRole("textbox", { name, exact: true });
const governance = (page: Page) => page.locator('[aria-label="Brand and taste governance"]');
const campaign = (page: Page) => page.getByRole("region", { name: "Campaign brand rollout review", exact: true });
const recovery = (page: Page, target: BrandTarget = "governance") => page.getByRole("region", {
  name: (target === "governance" ? "brand and taste" : "campaign") + " draft recovery", exact: true,
});
const digest = (value: string) => createHash("sha256").update(value).digest("hex");
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };

interface ApplyRequest {
  project_id: string; generation: string; revision: number;
  request_id: string; effect_grant: string; change: Change;
}
function applies(service: ScopedChangeService): { rpc: ServiceRpcRequest; request: ApplyRequest }[] {
  return service.transcript.flatMap(entry => {
    if (entry.direction !== "request" || !("command" in entry.payload) || entry.payload.command !== "apply_change") return [];
    const request = entry.payload.args?.request as ApplyRequest | undefined;
    if (!request) throw new Error("An actual apply_change request has no request envelope.");
    return [{ rpc: entry.payload, request }];
  });
}
function mutationCounts(service: ScopedChangeService) {
  return {
    apply: applies(service).length,
    projectEditGrants: service.transcript.filter(entry => entry.direction === "request" &&
      "command" in entry.payload && entry.payload.command === "issue_effect_grant" &&
      (entry.payload.args?.request as { effect?: string } | undefined)?.effect === "project_edit").length,
  };
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openBrand(page: Page) {
  await button(page, "Production").click();
  await button(page, "Brand & taste").click();
  await expect(field(governance(page), "Brand identity")).toBeVisible();
  await expect(campaign(page)).toBeVisible();
}
async function ownCopy(page: Page, project: Project, target: BrandTarget = "governance") {
  const scope = brandScope(project, target);
  const writerId = await page.evaluate(key => sessionStorage.getItem(key), brandPointerKey(scope));
  if (!writerId) throw new Error("The brand input has no confirmed writer pointer.");
  const key = brandCopyKey(scope, writerId), raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("The selected brand writer has no retained bytes.");
  const value = JSON.parse(raw) as BrandCopy;
  expect(value.schema).toBe("motionwright.brand-authoring-recovery/1");
  expect(value.scope).toBe(scope); expect(value.writerId).toBe(writerId);
  expect(value.draft.scope).toBe(scope); expect(value.draft.target).toBe(target);
  return { key, raw, value, sha256: digest(raw) };
}
async function unchangedRaw(page: Page, original: { key: string; raw: string }) {
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
}
async function copies(page: Page, project: Project, target: BrandTarget = "governance") {
  return page.evaluate(prefix => Object.keys(localStorage).filter(key => key.startsWith(prefix)).sort().map(key => {
    const raw = localStorage.getItem(key);
    if (raw === null) throw new Error("A retained brand source disappeared during observation.");
    return { key, raw, value: JSON.parse(raw) as BrandCopy };
  }), BRAND_COPY_PREFIX + brandScope(project, target) + ":");
}
function unrelated(project: Project) {
  const { revision: _revision, schema_version: _schema, updated_at: _updated,
    branches: _branches, reviews: _reviews, scenes: _scenes, production_design: _design, ...rest } = project;
  return rest;
}
function preservedMetadata(before: Project, after: Project) {
  expect(unrelated(after)).toEqual(unrelated(before));
  expect(after.schema_version).toBe(before.schema_version === 1 ? 2 : before.schema_version);
  expect(after.updated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/);
  expect(Number.isFinite(Date.parse(after.updated_at))).toBe(true);
  expect(after.branches).toEqual(before.branches.map(branch => branch.id === before.active_branch
    ? { ...branch, head_revision: after.revision } : branch));
  expect(after.reviews).toEqual(before.reviews.map(review => review.status === "resolved"
    && review.anchor.branch_id === before.active_branch && review.anchor.revision < after.revision
    ? { ...review, status: "needs_recheck", resolution: null, resolved_at: null } : review));
}
function otherDesign(project: Project, omitHeroes = false) {
  const { brand_profile: _brand, brand_exceptions: _exceptions, taste_profile: _taste,
    project_decisions: _decisions, heroes, ...rest } = project.production_design!;
  return omitHeroes ? rest : { ...rest, heroes };
}
function preserved(before: Project, after: Project) {
  preservedMetadata(before, after);
  expect(after.scenes).toEqual(before.scenes);
  expect(otherDesign(after)).toEqual(otherDesign(before));
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
async function bounded<T>(promise: Promise<T>, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(label + " exceeded its 15-second observation bound.")), 15_000);
    })]);
  } finally { clearTimeout(timer); }
}
/** The real SQLite response is observed first; only its delivery to App is lost. */
function holdAndLoseTasteReply(service: ScopedChangeService, type: Change["type"] = "set_taste_profile") {
  const original = service.invoke.bind(service), release = deferred<void>(), committed = deferred<Project>(), finished = deferred<void>();
  void committed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyRequest | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== type) return original<T>(command, args);
    armed = false; matched = true;
    try {
      const saved = await original<Project>(command, args);
      committed.resolve(saved); await release.promise;
      throw new Error("Injected brand reply loss after the actual SQLite commit.");
    } catch (reason) { committed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return {
    committed: committed.promise, release: () => release.resolve(),
    async dispose() {
      service.invoke = original; release.resolve();
      if (matched) await bounded(finished.promise, "Completion of the deliberately lost brand response");
    },
  };
}

interface BrandCase {
  service: ScopedChangeService; baseline: Project; initialCounts: ReturnType<typeof mutationCounts>;
  observations: Record<string, unknown>; cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>; restart(expected: Project): Promise<void>;
}
async function withBrand(page: Page, info: TestInfo, run: (value: BrandCase) => Promise<void>,
  arrange?: (service: ScopedChangeService, project: Project) => Promise<Project>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  let final: Project | undefined, projectId: string | undefined, primary: { reason: unknown } | undefined;
  let custody: StoreFinalization | undefined;
  const browser: { kind: string; text: string; truncated: boolean }[] = []; let browserEntries = 0;
  const log = (kind: string, text: string) => {
    browserEntries++; browser.push({ kind, text: text.slice(0, 2000), truncated: text.length > 2000 });
    if (browser.length > 128) browser.shift();
  };
  page.on("console", entry => log(entry.type(), entry.text()));
  page.on("pageerror", error => log("pageerror", error.message));
  try {
    const boot = await service.fixtureBootstrap();
    expect(boot.test_transport).toBe("service-test-jsonl"); expect(boot.fixture.label).toBe("synthetic_test_fixture");
    let project = boot.project; final = project; projectId = project.id;
    for (const lock of [...project.locks]) {
      project = await service.apply(project, { type: "remove_lock", lock_id: lock.id }); final = project;
    }
    if (!project.scenes.length) throw new Error("The selected actual fixture has no scene for a scoped decision.");
    const earlier = project.production_design?.brand_profile;
    const sourceProfile: BrandProfile = {
      id: earlier?.id ?? randomUUID(), label: "Brand source for retention test", version: (earlier?.version ?? 0) + 1,
      rules: [{ kind: "forbidden_phrase", id: randomUUID(), phrase: "unobserved_brand_sentinel_" + randomUUID() }],
    };
    project = await service.apply(project, { type: "set_brand_governance", profile: sourceProfile, exceptions: [] }); final = project;
    project = await service.apply(project, { type: "set_taste_profile", profile: {
      id: project.production_design?.taste_profile?.id ?? randomUUID(), label: "Saved source taste",
      preferences: [{ axis: "pace", preference: "Preserve the authored source meaning", inferred: false }],
    } }); final = project;
    project = await service.apply(project, { type: "record_creative_decision", decision: {
      id: randomUUID(), scene_id: project.scenes[0].id, author: "Earlier test author",
      decision: "Keep the earlier authored source intact", rationale: "A controlled service-test arrangement, not independent creative approval.",
    } }); final = project;
    if (arrange) { project = await arrange(service, project); final = project; }
    const baseline = project, checkpoints: unknown[] = [], restarts: unknown[] = [];
    observations.baseline = baseline; observations.arrangementCounts = mutationCounts(service);
    observations.checkpoints = checkpoints; observations.restarts = restarts;
    const observe = async (label: string) => {
      if (checkpoints.length >= 16) throw new Error("The bounded brand scenario exceeded 16 observation checkpoints.");
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) });
      return final;
    };
    const restart = async (expected: Project) => {
      const before = await service.observeStoredProject(expected), exits = service.processExits.length;
      await service.close();
      expect(service.processExits).toHaveLength(exits + 1);
      expect(service.processExits.at(-1)).toMatchObject({ code: 0, signal: null });
      await service.start();
      const after = await service.observeStoredProject(expected);
      expect(after.project).toEqual(before.project); expect(after.journal).toEqual(before.journal);
      restarts.push({ revision: expected.revision, exit: service.processExits.at(-1),
        originalJournalSha256: digest(JSON.stringify(before.journal)), reopenedJournalSha256: digest(JSON.stringify(after.journal)) });
      await page.reload(); await revision(page, expected.revision); await openBrand(page); final = after.project;
    };
    await service.attachFixturePage(page, baseline.id);
    await page.goto("/"); await revision(page, baseline.revision); await openBrand(page);
    await run({ service, baseline, initialCounts: mutationCounts(service), observations, cleanup, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const transcript = service.transcript.slice(-256).map(entry => {
        const raw = JSON.stringify(entry.payload);
        return { at: entry.at, processId: entry.processId, direction: entry.direction, sha256: digest(raw),
          payloadCharacters: raw.length, excerpt: raw.slice(0, 4096), truncated: raw.length > 4096 };
      });
      const path = info.outputPath("brand-authoring-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + source-built StudioService + project_edit grants + SQLite/CAS + journal",
        exclusions: ["installed Tauri", "native renderer output", "human creative acceptance"],
        statusAtTeardown: info.status, expectedStatus: info.expectedStatus, observations, final,
        primaryFailure: primary ? detail(primary.reason) : null, failures: failures.map(detail), closedStoreOracle: custody?.oracle ?? null,
        service: { databasePath: service.databasePath, binaryPath: service.binaryPath,
          processExits: service.processExits, lifecycleTaints: service.lifecycleTaints,
          transcriptCount: service.transcript.length, transcriptRetained: transcript.length,
          transcriptEntryLimit: 256, transcriptExcerptCharacterLimit: 4096, transcript,
          stderrCharacters: service.stderr.length, stderrTailCharacterLimit: 32768, stderrTail: service.stderr.slice(-32768) },
        browser: { totalEntries: browserEntries, entryLimit: 128, characterLimit: 2000, entries: browser },
      }, null, 2));
      await info.attach("brand-authoring-service-evidence", { path, contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    try { await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" }); }
    catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Brand behavior and closed SQLite custody retained separate failures", { cause: primary?.reason });
  }
}

test("Brand policy, taste and decision saves preserve other raw fields through actual App navigation and SQLite restart", async ({ page }, info) => {
  await withBrand(page, info, async ({ service, baseline, initialCounts, observations, observe, restart }) => {
    const root = governance(page), panel = recovery(page);
    const policyRaw = "  Editable brand identity  ", tasteRaw = "  A measured editorial preference  ";
    const campaignRaw = "  Retain this unsubmitted exception campaign  ";
    const author = "Service test author", reason = "Keep the source work and describe this explicit choice.";
    const decision = "  Preserve a readable source claim\nThen hold its conclusion  ";
    await field(root, "Brand identity").fill(policyRaw);
    await field(root, "Allowed accent colors (#RRGGBB, comma separated)").fill("1e");
    await field(root, "Taste label").fill(tasteRaw);
    await field(root, "Campaign").fill(campaignRaw);
    await field(root, "Recorded author").fill(author);
    await field(root, "Reason for exception").fill(reason);
    await field(root, "Decision").fill(decision);
    const incomplete = await ownCopy(page, baseline); observations.incomplete = incomplete;
    await button(root, "Save brand policy").click();
    await expect(panel.getByRole("alert")).toContainText("Brand palette needs distinct #RRGGBB colors.");
    expect(mutationCounts(service)).toEqual(initialCounts);
    expect(await observe("invalid palette stays local before any grant")).toEqual(baseline);
    await button(page, "Brief").click(); await openBrand(page);
    await page.reload(); await revision(page, baseline.revision); await openBrand(page);
    await expect(field(root, "Allowed accent colors (#RRGGBB, comma separated)")).toHaveValue("1e");
    await expect(field(root, "Taste label")).toHaveValue(tasteRaw);
    await expect(field(root, "Decision")).toHaveValue(decision);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await field(root, "Allowed accent colors (#RRGGBB, comma separated)").fill("");
    await button(root, "Save brand policy").click(); await revision(page, baseline.revision + 1);
    await expect(panel.getByRole("status").filter({ hasText: "Saved brand and taste at r" })).toBeVisible();
    const policySaved = await observe("policy save acknowledges only its represented fields");
    const oldPolicy = baseline.production_design!.brand_profile!;
    expect(policySaved.production_design!.brand_profile).toEqual({ ...oldPolicy, label: policyRaw.trim(), version: oldPolicy.version + 1 });
    expect(policySaved.production_design!.taste_profile).toEqual(baseline.production_design!.taste_profile);
    expect(policySaved.production_design!.project_decisions).toEqual(baseline.production_design!.project_decisions);
    await expect(field(root, "Taste label")).toHaveValue(tasteRaw);
    await expect(field(root, "Campaign")).toHaveValue(campaignRaw);
    await expect(field(root, "Decision")).toHaveValue(decision);
    await button(root, "Save taste").click(); await revision(page, baseline.revision + 2);
    const tasteSaved = await observe("taste save leaves the separate decision and exception raw input");
    expect(tasteSaved.production_design!.taste_profile).toEqual({ ...baseline.production_design!.taste_profile!, label: tasteRaw });
    expect(tasteSaved.production_design!.brand_profile).toEqual(policySaved.production_design!.brand_profile);
    expect(tasteSaved.production_design!.project_decisions).toEqual(baseline.production_design!.project_decisions);
    await expect(field(root, "Campaign")).toHaveValue(campaignRaw);
    await expect(field(root, "Decision")).toHaveValue(decision);
    await button(root, "Record decision for selected scene").click(); await revision(page, baseline.revision + 3);
    const saved = await observe("one authored decision appended to the original history");
    const prior = baseline.production_design!.project_decisions ?? [], decisions = saved.production_design!.project_decisions ?? [];
    expect(decisions.slice(0, prior.length)).toEqual(prior); expect(decisions).toHaveLength(prior.length + 1);
    expect(decisions.at(-1)).toEqual({ id: decisions.at(-1)!.id, scene_id: baseline.scenes[0].id, author, decision, rationale: reason });
    expect(saved.production_design!.brand_profile).toEqual(policySaved.production_design!.brand_profile);
    expect(saved.production_design!.taste_profile).toEqual(tasteSaved.production_design!.taste_profile);
    expect(saved.production_design!.brand_exceptions).toEqual(baseline.production_design!.brand_exceptions);
    preserved(baseline, saved);
    const requests = applies(service).slice(initialCounts.apply);
    expect(requests.map(value => value.request.change.type)).toEqual(["set_brand_governance", "set_taste_profile", "record_creative_decision"]);
    expect(new Set(requests.map(value => value.request.request_id)).size).toBe(3);
    expect(new Set(requests.map(value => value.request.effect_grant)).size).toBe(3);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual(requests.map(value => value.request.change));
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 3, projectEditGrants: initialCounts.projectEditGrants + 3 });
    const beforeRestart = await ownCopy(page, saved); observations.beforeRestart = beforeRestart;
    await restart(saved);
    await expect(field(root, "Brand identity")).toHaveValue(policyRaw);
    await expect(field(root, "Taste label")).toHaveValue(tasteRaw);
    await expect(field(root, "Campaign")).toHaveValue(campaignRaw);
    await expect(field(root, "Decision")).toHaveValue(decision);
    await unchangedRaw(page, beforeRestart);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 3, projectEditGrants: initialCounts.projectEditGrants + 3 });
  });
});

test("a real taste commit with a lost original reply stays UNKNOWN through later typing, reload and saved-history inspection", async ({ page }, info) => {
  await withBrand(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const root = governance(page), panel = recovery(page), held = holdAndLoseTasteReply(service); cleanup.push(held.dispose);
    const submitted = "Original submitted taste", later = "  Later input after dispatch  ", unrelatedRaw = "  Unsubmitted policy buffer  ";
    await field(root, "Taste label").fill(submitted);
    await button(root, "Save taste").click();
    const actual = await bounded(held.committed, "The actual taste SQLite commit");
    observations.actualOriginalResponse = actual;
    expect(actual.revision).toBe(baseline.revision + 1);
    expect(actual.production_design!.taste_profile!.label).toBe(submitted);
    await field(root, "Taste label").fill(later);
    await field(root, "Brand identity").fill(unrelatedRaw);
    held.release();
    await expect(panel.getByRole("alert")).toContainText("Injected brand reply loss after the actual SQLite commit.");
    await expect(panel.getByRole("heading", { name: "Review the original brand and taste attempt", exact: true })).toBeVisible();
    await expect(button(root, "Save taste")).toBeDisabled();
    const original = await ownCopy(page, baseline); observations.originalUnknown = original;
    expect(original.value.draft.pending?.outcome).toBe("UNKNOWN");
    expect(original.value.draft.pending?.revision).toBe(baseline.revision);
    expect((original.value.draft.pending?.raw as GovernanceRaw).tasteName).toBe(submitted);
    expect((original.value.draft.raw as GovernanceRaw).tasteName).toBe(later);
    expect((original.value.draft.raw as GovernanceRaw).brandName).toBe(unrelatedRaw);
    expect(original.value.draft.baseProject).toEqual(baseline);
    expect(await observe("real committed result differs from the deliberately lost reply delivered to App")).toEqual(actual);
    const request = applies(service).slice(initialCounts.apply)[0];
    expect(request.request.change).toEqual(original.value.draft.pending!.change);
    const replies = service.transcript.filter(entry => entry.direction === "response" && "ok" in entry.payload && entry.payload.id === request.rpc.id);
    expect(replies).toHaveLength(1); expect("ok" in replies[0].payload && replies[0].payload.ok).toBe(true);
    const once = { apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 };
    expect(mutationCounts(service)).toEqual(once);
    await button(page, "Brief").click(); await openBrand(page);
    await page.reload(); await revision(page, actual.revision); await openBrand(page);
    await expect(field(root, "Taste label")).toHaveValue(later);
    await expect(field(root, "Brand identity")).toHaveValue(unrelatedRaw);
    await expect(panel.getByRole("region", { name: "brand and taste uncertain outcome", exact: true })).toContainText("UNKNOWN");
    await expect(button(root, "Save taste")).toBeDisabled();
    await expect(button(panel, "Keep original brand and taste attempt and start a separate edit")).toHaveCount(0);
    expect((await ownCopy(page, baseline)).value.draft.pending).toEqual(original.value.draft.pending);
    await button(panel, "Read saved brand and taste and history").click();
    await expect(panel.getByRole("region", { name: "brand and taste outcome inspection", exact: true }))
      .toContainText("1 contain matching parameters. The original attempt remains UNKNOWN.");
    await expect(button(panel, "Keep original brand and taste attempt and start a separate edit")).toBeEnabled();
    await unchangedRaw(page, original); expect(mutationCounts(service)).toEqual(once);
    await button(panel, "Keep original brand and taste attempt and start a separate edit").click();
    await expect(button(root, "Save taste")).toBeEnabled();
    await unchangedRaw(page, original);
    expect(await observe("explicit separation acknowledges nothing and sends no second change")).toEqual(actual);
    expect(mutationCounts(service)).toEqual(once);
    await button(root, "Save taste").click(); await revision(page, actual.revision + 1);
    const saved = await observe("only a separate explicit Save admits the later taste input");
    expect(saved.production_design!.taste_profile).toEqual({ ...actual.production_design!.taste_profile!, label: later });
    expect(saved.production_design!.brand_profile).toEqual(baseline.production_design!.brand_profile);
    expect(saved.production_design!.project_decisions).toEqual(baseline.production_design!.project_decisions);
    expect(saved.production_design!.brand_exceptions).toEqual(baseline.production_design!.brand_exceptions);
    preserved(baseline, saved);
    const requests = applies(service).slice(initialCounts.apply);
    expect(requests).toHaveLength(2); expect(requests[1].request.revision).toBe(actual.revision);
    expect(requests[1].request.request_id).not.toBe(requests[0].request.request_id);
    expect(requests[1].request.effect_grant).not.toBe(requests[0].request.effect_grant);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual(requests.map(value => value.request.change));
    await restart(saved);
    await expect(field(root, "Taste label")).toHaveValue(later); await expect(field(root, "Brand identity")).toHaveValue(unrelatedRaw);
    await unchangedRaw(page, original);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 2, projectEditGrants: initialCounts.projectEditGrants + 2 });
  });
});

test("a silently dropped pending brand copy issues zero grants and Retry only confirms bytes before a separate Save", async ({ page }, info) => {
  await withBrand(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const root = governance(page), panel = recovery(page), raw = "  Retain this complete taste input  ";
    await field(root, "Taste label").fill(raw);
    const before = await ownCopy(page, baseline); observations.beforeDroppedWrite = before;
    await page.evaluate(prefix => {
      const original = Storage.prototype.setItem; window.__BRAND_SERVICE_DROPPED_WRITES__ = 0;
      Storage.prototype.setItem = function(key, value) {
        if (this === window.localStorage && key.startsWith(prefix)) {
          let pending = false;
          try { pending = Boolean(JSON.parse(String(value))?.draft?.pending); } catch { /* Unrelated bytes use the real setter. */ }
          if (pending) { window.__BRAND_SERVICE_DROPPED_WRITES__ = (window.__BRAND_SERVICE_DROPPED_WRITES__ ?? 0) + 1; return; }
        }
        return original.call(this, key, value);
      };
      window.__BRAND_SERVICE_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = original; };
    }, BRAND_COPY_PREFIX + brandScope(baseline, "governance") + ":");
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRAND_SERVICE_RESTORE_STORAGE__?.()); });
    await button(root, "Save taste").click();
    await expect(panel).toContainText("NOT_SENT"); await expect(button(root, "Save taste")).toBeDisabled();
    expect(await page.evaluate(() => window.__BRAND_SERVICE_DROPPED_WRITES__)).toBeGreaterThan(0);
    await unchangedRaw(page, before);
    expect(await observe("silent local pending-copy failure prevents grant and apply")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await button(panel, "Retry brand and taste recovery copy").click();
    await expect(button(root, "Save taste")).toBeDisabled(); await unchangedRaw(page, before);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await page.evaluate(() => window.__BRAND_SERVICE_RESTORE_STORAGE__!());
    await button(panel, "Retry brand and taste recovery copy").click();
    await expect(panel).toContainText("Confirmed the brand and taste recovery copy.");
    await expect(button(root, "Save taste")).toBeEnabled();
    const confirmed = await ownCopy(page, baseline); observations.confirmed = confirmed;
    expect(confirmed.value.draft.pending).toBeNull(); expect((confirmed.value.draft.raw as GovernanceRaw).tasteName).toBe(raw);
    const retained = (await copies(page, baseline)).find(copy => copy.value.draft.pending?.outcome === "NOT_SENT");
    expect(retained).toBeDefined(); expect(retained!.key).not.toBe(confirmed.key); observations.retainedNotSent = retained;
    expect(await observe("Retry confirms a different local copy without a domain change")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await button(root, "Save taste").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicit Save follows confirmation of local bytes");
    expect(saved.production_design!.taste_profile).toEqual({ ...baseline.production_design!.taste_profile!, label: raw });
    expect(saved.production_design!.brand_profile).toEqual(baseline.production_design!.brand_profile);
    expect(saved.production_design!.project_decisions).toEqual(baseline.production_design!.project_decisions);
    expect(saved.production_design!.brand_exceptions).toEqual(baseline.production_design!.brand_exceptions);
    preserved(baseline, saved);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["set_taste_profile"]);
    await restart(saved); await expect(field(root, "Taste label")).toHaveValue(raw); await unchangedRaw(page, retained!);
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  });
});

test("a failed original UNKNOWN recovery read cannot be escaped by restoring a different ordinary copy", async ({ page }, info) => {
  await withBrand(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe, restart }) => {
    const otherPage = await page.context().newPage();
    cleanup.push(async () => { if (!otherPage.isClosed()) await otherPage.close(); });
    await service.attachFixturePage(otherPage, baseline.id);
    await otherPage.goto("/"); await revision(otherPage, baseline.revision); await openBrand(otherPage);
    const otherRaw = "A ordinary retained draft from another tab";
    await field(governance(otherPage), "Taste label").fill(otherRaw);
    const a = await ownCopy(otherPage, baseline); observations.otherOrdinaryCopy = a;
    expect(a.value.draft.pending).toBeNull(); await otherPage.close();
    expect(mutationCounts(service)).toEqual(initialCounts);

    const held = holdAndLoseTasteReply(service); cleanup.push(held.dispose);
    const submitted = "W original unknown submitted taste", root = governance(page), panel = recovery(page);
    await field(root, "Taste label").fill(submitted); await button(root, "Save taste").click();
    const actual = await bounded(held.committed, "The committed taste behind the initial recovery-read fault");
    observations.actualOriginalResponse = actual; held.release();
    await expect(panel.getByRole("alert")).toContainText("Injected brand reply loss after the actual SQLite commit.");
    const w = await ownCopy(page, baseline); observations.originalUnknownCopy = w;
    expect(w.value.draft.pending?.outcome).toBe("UNKNOWN"); expect(w.key).not.toBe(a.key);
    const pointerKey = brandPointerKey(brandScope(baseline, "governance")), marker = "brand-test-initial-read:" + w.key;
    await page.addInitScript(({ target, marker }) => {
      if (sessionStorage.getItem(marker)) return;
      const original = Storage.prototype.getItem; let armed = true;
      window.__BRAND_SERVICE_BLOCKED_READS__ = 0;
      Storage.prototype.getItem = function(key) {
        if (armed && this === window.localStorage && key === target) {
          armed = false; sessionStorage.setItem(marker, "fired");
          window.__BRAND_SERVICE_BLOCKED_READS__ = (window.__BRAND_SERVICE_BLOCKED_READS__ ?? 0) + 1;
          throw new Error("Injected initial read of the original UNKNOWN brand copy.");
        }
        return original.call(this, key);
      };
      window.__BRAND_SERVICE_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = original; };
    }, { target: w.key, marker });
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRAND_SERVICE_RESTORE_STORAGE__?.()); });
    await page.reload(); await revision(page, actual.revision); await openBrand(page);
    await expect(panel.getByRole("alert", { name: "brand and taste recovery warning", exact: true }))
      .toContainText("Injected initial read of the original UNKNOWN brand copy.");
    expect(await page.evaluate(() => window.__BRAND_SERVICE_BLOCKED_READS__)).toBe(1);
    expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
    // Do not edit the provisional clean draft: this is the path that formerly
    // skipped backup() and discarded the unread pending authority on Restore.
    await expect(panel.getByRole("heading", { name: "Review the original brand and taste attempt", exact: true })).toHaveCount(0);
    await panel.locator('details[aria-label="brand and taste recovery copies"]').locator("summary").first().click();
    const otherGroup = panel.getByRole("group", { name: /^brand and taste recovery copy \d+$/ }).filter({ hasText: otherRaw });
    await expect(otherGroup).toHaveCount(1);
    const restore = otherGroup.getByRole("button", { name: /^Restore brand and taste copy \d+$/ });
    await expect(restore).toBeDisabled();
    await expect(button(root, "Save taste")).toBeDisabled();
    expect(await page.evaluate(key => sessionStorage.getItem(key), pointerKey)).toBe(w.value.writerId);
    await unchangedRaw(page, a); await unchangedRaw(page, w);
    const once = { apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 };
    expect(mutationCounts(service)).toEqual(once);
    expect(await observe("unread original UNKNOWN blocks adopting another ordinary recovery copy")).toEqual(actual);

    await button(panel, "Retry brand and taste recovery copy").click();
    await expect(panel.getByRole("heading", { name: "Review the original brand and taste attempt", exact: true })).toBeVisible();
    await expect(field(root, "Taste label")).toHaveValue(submitted);
    await expect(button(root, "Save taste")).toBeDisabled(); await expect(restore).toBeDisabled();
    const recovered = await ownCopy(page, baseline); observations.recoveredOriginal = recovered;
    expect(recovered.value.draft.pending).toEqual(w.value.draft.pending);
    expect(recovered.value.draft.baseProject).toEqual(w.value.draft.baseProject);
    expect(recovered.value.draft.origins.some(origin => origin.key === w.key && origin.raw === w.raw)).toBe(true);
    await unchangedRaw(page, a); await unchangedRaw(page, w);
    expect(mutationCounts(service)).toEqual(once);
    expect(await observe("Retry restores W pending authority and does not send or acknowledge it")).toEqual(actual);
    await restart(actual);
    await expect(panel.getByRole("heading", { name: "Review the original brand and taste attempt", exact: true })).toBeVisible();
    await expect(button(root, "Save taste")).toBeDisabled();
    await unchangedRaw(page, a); await unchangedRaw(page, w); expect(mutationCounts(service)).toEqual(once);
  });
});

test("a reviewed selected campaign changes the real semantic Hero while preserving other scenes and proposal bytes through SQLite restart", async ({ page }, info) => {
  let selectedId = "", targetAccent = "";
  await withBrand(page, info, async ({ service, baseline, initialCounts, observations, observe, restart }) => {
    const root = campaign(page), panel = recovery(page, "campaign");
    const selected = baseline.scenes.find(scene => scene.id === selectedId)!;
    const hero = baseline.production_design!.heroes.find(value => value.scene_id === selectedId)!;
    const campaignRaw = "  Selected source-bound campaign  ", owner = "Test author declaration", why = "Preserve all earlier scenes while changing one authored accent.";
    await field(root, "Brand rollout campaign").fill(campaignRaw);
    await field(root, "Brand rollout owner").fill(owner);
    await field(root, "Brand rollout rationale").fill(why);
    await root.getByRole("combobox", { name: "Brand rollout accent", exact: true }).selectOption(targetAccent);
    await root.getByRole("group", { name: "Select up to eight existing editable scenes", exact: true })
      .locator("label").filter({ hasText: selected.name }).getByRole("checkbox").check();
    await root.getByLabel(/I understand existing Hero components will be rebuilt/).check();
    await button(root, "Review per-scene brand changes").click();
    const proposed = root.getByRole("region", { name: "Proposed brand campaign changes", exact: true });
    await expect(proposed).toContainText("1 component(s) would change");
    const first = await ownCopy(page, baseline, "campaign"), proposal = first.value.draft.proposal!;
    observations.originalProposal = first;
    expect(proposal.rollout.scene_ids).toEqual([selectedId]);
    expect(proposal.sourceRevision).toBe(baseline.revision);
    expect(proposal.rollout.campaign).toBe(campaignRaw); expect(proposal.rollout.reviewer_declaration).toBe(owner);
    expect(proposal.rollout.rationale).toBe(why); expect(proposal.rollout.accent).toBe(targetAccent);
    expect(proposal.preview.rows.map(row => row.hero_id)).toEqual([hero.id]);
    expect((first.value.draft.raw as CampaignRaw).sceneIds).toEqual([selectedId]);
    expect(await observe("a real-source preview is local and has no grant")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await button(page, "Brief").click(); await openBrand(page);
    await page.reload(); await revision(page, baseline.revision); await openBrand(page);
    const reopened = await ownCopy(page, baseline, "campaign");
    expect(reopened.value.draft.proposal).toEqual(proposal); await unchangedRaw(page, first);
    await expect(field(root, "Brand rollout campaign")).toHaveValue(campaignRaw);
    await expect(button(root, "Apply owner-reviewed campaign via project CAS")).toBeDisabled();
    expect(mutationCounts(service)).toEqual(initialCounts);
    await root.getByLabel(/I reviewed the exact rows, campaign, brand digest/).check();
    await button(root, "Apply owner-reviewed campaign via project CAS").click(); await revision(page, baseline.revision + 1);
    await expect(panel.getByRole("status").filter({ hasText: "Saved campaign at r" })).toBeVisible();
    await expect(panel.getByRole("region", { name: "campaign uncertain outcome", exact: true })).toHaveCount(0);
    const saved = await observe("one original real campaign result acknowledges the exact source-bound rollout");
    const savedHero = saved.production_design!.heroes.find(value => value.id === hero.id)!;
    expect(saved.production_design!.heroes.map(value => value.id)).toEqual(baseline.production_design!.heroes.map(value => value.id));
    expect(savedHero.config).toEqual({ ...hero.config, accent: targetAccent });
    expect(saved.production_design!.heroes.filter(value => value.id !== hero.id)).toEqual(baseline.production_design!.heroes.filter(value => value.id !== hero.id));
    expect(saved.scenes.map(scene => scene.id)).toEqual(baseline.scenes.map(scene => scene.id));
    expect(saved.scenes.filter(scene => scene.id !== selectedId)).toEqual(baseline.scenes.filter(scene => scene.id !== selectedId));
    const savedScene = saved.scenes.find(scene => scene.id === selectedId)!;
    expect(selected.status).toBe("needs_work");
    expect({ ...savedScene, nodes: selected.nodes }).toEqual({ ...selected, status: "draft" });
    expect(savedScene.nodes.map(node => node.id)).toEqual(selected.nodes.map(node => node.id));
    const governedIds = new Set(hero.baseline.map(node => node.id));
    expect(savedScene.nodes.filter(node => !governedIds.has(node.id))).toEqual(selected.nodes.filter(node => !governedIds.has(node.id)));
    preservedMetadata(baseline, saved); expect(otherDesign(saved, true)).toEqual(otherDesign(baseline, true));
    expect(saved.production_design!.brand_profile).toEqual(baseline.production_design!.brand_profile);
    expect(saved.production_design!.brand_exceptions).toEqual(baseline.production_design!.brand_exceptions);
    expect(saved.production_design!.taste_profile).toEqual(baseline.production_design!.taste_profile);
    const prior = baseline.production_design!.project_decisions ?? [], decisions = saved.production_design!.project_decisions ?? [];
    expect(decisions.slice(0, prior.length)).toEqual(prior); expect(decisions).toHaveLength(prior.length + 1);
    expect(decisions.at(-1)).toEqual({ id: decisions.at(-1)!.id, scene_id: selectedId, author: owner,
      decision: "Campaign " + campaignRaw + ": brand accent " + hero.config.accent + " to " + targetAccent
        + ", wordmark " + hero.config.wordmark + " to " + hero.config.wordmark + "; policy SHA-256 " + proposal.rollout.brand_sha256,
      rationale: why });
    const requests = applies(service).slice(initialCounts.apply);
    expect(requests).toHaveLength(1); expect(requests[0].request.change).toEqual({ type: "apply_brand_campaign", rollout: proposal.rollout });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual([requests[0].request.change]);
    const after = await ownCopy(page, saved, "campaign"); observations.acknowledged = after;
    expect(after.value.draft.pending).toBeNull(); expect(after.value.draft.proposal).toEqual(proposal);
    await restart(saved); await unchangedRaw(page, first);
    await expect(proposed).toContainText("Retained proposal from r" + baseline.revision);
    await expect(button(root, "Apply owner-reviewed campaign via project CAS")).toBeDisabled();
    expect(mutationCounts(service)).toEqual({ apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 });
  }, async (service, project) => {
    const existingIds = new Set(project.scenes.map(scene => scene.id));
    project = await service.apply(project, { type: "add_scene", name: "Brand campaign isolated source", objective: "One selected semantic component in a controlled persistence test.", duration_seconds: 6 });
    const added = project.scenes.filter(scene => !existingIds.has(scene.id)); expect(added).toHaveLength(1);
    selectedId = added[0].id;
    project = await service.apply(project, { type: "upsert_product_hero", instance_id: randomUUID(), scene_id: selectedId, config: defaultHeroConfig() });
    project = await service.apply(project, { type: "set_scene_status", scene_id: selectedId, status: "needs_work" });
    const sourceAccent = project.production_design!.heroes.find(hero => hero.scene_id === selectedId)!.config.accent;
    targetAccent = sourceAccent.toLowerCase() === "#f5b84a" ? "#a5c8df" : "#f5b84a";
    // Admit every current authored color in this test arrangement; the test is
    // about bounded propagation/recovery, not intentionally violating old work.
    const colors = new Set<string>([targetAccent]);
    for (const scene of project.scenes) for (const node of scene.nodes)
      if (node.style.fill && /^#[a-f0-9]{6}$/i.test(node.style.fill)) colors.add(node.style.fill.toLowerCase());
    for (const hero of project.production_design!.heroes) colors.add(hero.config.accent.toLowerCase());
    for (const stage of project.production_design!.stages ?? []) colors.add(stage.config.accent.toLowerCase());
    for (const field of project.production_design!.procedural_fields ?? []) colors.add(field.config.fill.toLowerCase());
    if (colors.size > 16) throw new Error("This real fixture requires more than the canonical 16-color policy budget; revise the explicit test arrangement.");
    const profile = project.production_design!.brand_profile!;
    return service.apply(project, { type: "set_brand_governance", profile: { ...profile, version: profile.version + 1,
      rules: [...profile.rules, { kind: "allowed_accents", id: randomUUID(), colors: [...colors] }] }, exceptions: [] });
  });
});

for (const target of ["governance", "campaign"] as const) {
  test(`Brand ${target} retains its first observed UNKNOWN bytes when a failed recovery read is followed by a same-writer replacement`, async ({ page }, info) => {
    let selectedId = "", accent = "";
    await withBrand(page, info, async ({ service, baseline, initialCounts, observations, cleanup, observe }) => {
      const root = target === "governance" ? governance(page) : campaign(page), panel = recovery(page, target);
      const label = target === "governance" ? "brand and taste" : "campaign";
      const input = field(root, target === "governance" ? "Taste label" : "Brand rollout campaign");
      const disabledAction = target === "governance" ? "Save taste" : "Review per-scene brand changes";
      const submitted = "Original " + target + " input with a real uncertain result";
      const held = holdAndLoseTasteReply(service, target === "governance" ? "set_taste_profile" : "apply_brand_campaign");
      cleanup.push(held.dispose);
      await input.fill(submitted);
      if (target === "campaign") {
        await field(root, "Brand rollout owner").fill("Synthetic author declaration");
        await field(root, "Brand rollout rationale").fill("A controlled first-observed-copy recovery regression.");
        await root.getByRole("combobox", { name: "Brand rollout accent", exact: true }).selectOption(accent);
        const scene = baseline.scenes.find(value => value.id === selectedId)!;
        await root.getByRole("group", { name: "Select up to eight existing editable scenes", exact: true })
          .locator("label").filter({ hasText: scene.name }).getByRole("checkbox").check();
        await root.getByLabel(/I understand existing Hero components will be rebuilt/).check();
        await button(root, "Review per-scene brand changes").click();
        await expect(root.getByRole("region", { name: "Proposed brand campaign changes", exact: true }))
          .toContainText("1 component(s) would change");
        await root.getByLabel(/I reviewed the exact rows, campaign, brand digest/).check();
      }
      await button(root, target === "governance" ? "Save taste" : "Apply owner-reviewed campaign via project CAS").click();
      const saved = await bounded(held.committed, "Original real " + target + " response before recovery failure");
      held.release();
      await expect(panel.getByRole("alert")).toContainText("Injected brand reply loss after the actual SQLite commit.");
      const first = await ownCopy(page, baseline, target), before = await service.observeStoredProject(saved);
      expect(first.value.draft.pending?.outcome).toBe("UNKNOWN");
      expect(saved.revision).toBe(baseline.revision + 1);
      const once = { apply: initialCounts.apply + 1, projectEditGrants: initialCounts.projectEditGrants + 1 };
      expect(mutationCounts(service)).toEqual(once);
      const marker = "brand-test-first-observed-source";
      await page.addInitScript(marker => {
        const key = sessionStorage.getItem(marker); if (key === null) return;
        sessionStorage.removeItem(marker);
        const get = Storage.prototype.getItem; let fired = false;
        window.__BRAND_SERVICE_BLOCKED_READS__ = 0;
        Storage.prototype.getItem = function(name) {
          if (this === localStorage && name === key && !fired) {
            fired = true; window.__BRAND_SERVICE_BLOCKED_READS__ = 1;
            throw new Error("Initial brand source read temporarily unavailable.");
          }
          return get.call(this, name);
        };
        window.__BRAND_SERVICE_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = get; };
      }, marker);
      const failRead = async (key: string) => {
        await page.evaluate(({ marker, key }) => sessionStorage.setItem(marker, key), { marker, key });
        await page.reload(); await openBrand(page);
        await expect(panel.getByRole("alert", { name: label + " recovery warning", exact: true }))
          .toContainText("Initial brand source read temporarily unavailable.");
        expect(await page.evaluate(() => window.__BRAND_SERVICE_BLOCKED_READS__)).toBe(1);
        await page.evaluate(() => window.__BRAND_SERVICE_RESTORE_STORAGE__?.());
      };
      await failRead(first.key);
      await input.fill("An edit while original recovery is blocked"); await input.fill("");
      await button(panel, "Retry " + label + " recovery copy").click();
      await expect(panel.getByRole("alert", { name: label + " recovery warning", exact: true })).toHaveCount(0);
      await expect(panel.getByRole("heading", { name: "Review the original " + label + " attempt", exact: true })).toBeVisible();
      await expect(input).toHaveValue(""); await expect(button(root, disabledAction)).toBeDisabled();
      const recovered = await ownCopy(page, baseline, target);
      expect(recovered.value.draft.pending).toEqual(first.value.draft.pending);
      await unchangedRaw(page, first); expect(mutationCounts(service)).toEqual(once);
      await failRead(recovered.key);
      await input.fill("Later raw input after the second recovery failure");
      const replacementCopy = JSON.parse(recovered.raw); replacementCopy.draft.pending = null;
      const replacement = JSON.stringify(replacementCopy);
      expect(decodeBrandCopy(replacement, brandScope(baseline, target), recovered.value.writerId).draft.pending).toBeNull();
      await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: recovered.key, raw: replacement });
      await button(panel, "Retry " + label + " recovery copy").click();
      await expect(panel.getByRole("alert", { name: label + " recovery warning", exact: true }),
        "BRAND_FIRST_READ_SAME_WRITER_REPLACEMENT_REFUSED").toContainText("original brand recovery bytes changed");
      await expect(input).toHaveValue("Later raw input after the second recovery failure");
      await expect(button(root, disabledAction)).toBeDisabled();
      await panel.locator('details[aria-label="' + label + ' recovery copies"]').locator("summary").first().click();
      const received = page.waitForEvent("download"); await button(panel, "Download original " + label + " copy 1").click();
      const downloaded = await (await received).path();
      if (!downloaded) throw new Error("The first brand observation did not produce a download.");
      const bytes = await readFile(downloaded);
      expect(bytes.equals(Buffer.from(recovered.raw, "utf8"))).toBe(true);
      await expect(button(panel, "Restore " + label + " copy 1")).toBeDisabled();
      expect(await page.evaluate(key => localStorage.getItem(key), recovered.key)).toBe(replacement);
      expect(mutationCounts(service)).toEqual(once);
      expect(await observe("replacement refusal preserves the real Project and original attempt")).toEqual(saved);
      const after = await service.observeStoredProject(saved);
      expect(after.project).toEqual(before.project); expect(after.journal).toEqual(before.journal);
      await service.close(); await service.start();
      expect(await service.project(saved.id)).toEqual(saved); expect(await service.history(saved.id, 0)).toEqual(before.journal);
      Object.assign(observations, { target, first, recovered, replacement, before, after, counts: once, additionalMutations: 0, additionalGrants: 0 });
      await info.attach("first-observed-brand-copy", { body: bytes, contentType: "application/json" });
      await page.screenshot({ path: info.outputPath("brand-first-observed-copy.png"), fullPage: true });
    }, target === "campaign" ? async (service, project) => {
      const ids = new Set(project.scenes.map(scene => scene.id));
      project = await service.apply(project, { type: "add_scene", name: "First read campaign source", objective: "A real editable component for recovery testing.", duration_seconds: 6 });
      const added = project.scenes.filter(scene => !ids.has(scene.id)); expect(added).toHaveLength(1); selectedId = added[0].id;
      project = await service.apply(project, { type: "upsert_product_hero", instance_id: randomUUID(), scene_id: selectedId, config: defaultHeroConfig() });
      const sourceAccent = project.production_design!.heroes.find(hero => hero.scene_id === selectedId)!.config.accent;
      accent = sourceAccent.toLowerCase() === "#f5b84a" ? "#a5c8df" : "#f5b84a";
      const colors = new Set<string>([accent]);
      for (const scene of project.scenes) for (const node of scene.nodes)
        if (node.style.fill && /^#[a-f0-9]{6}$/i.test(node.style.fill)) colors.add(node.style.fill.toLowerCase());
      for (const hero of project.production_design!.heroes) colors.add(hero.config.accent.toLowerCase());
      for (const stage of project.production_design!.stages ?? []) colors.add(stage.config.accent.toLowerCase());
      for (const procedural of project.production_design!.procedural_fields ?? []) colors.add(procedural.config.fill.toLowerCase());
      if (colors.size > 16) throw new Error("The test fixture exceeds the real brand palette budget.");
      const profile = project.production_design!.brand_profile!;
      return service.apply(project, { type: "set_brand_governance", profile: { ...profile, version: profile.version + 1,
        rules: [...profile.rules, { kind: "allowed_accents", id: randomUUID(), colors: [...colors] }] }, exceptions: [] });
    } : undefined);
  });
}

test("an explicitly cleared brand rule issues zero grants and remains empty after a real SQLite restart", async ({ page }, info) => {
  await withBrand(page, info, async ({ service, baseline, initialCounts, observe, restart }) => {
    const root = governance(page), rule = root.getByRole("combobox", { name: "Brand rule", exact: true });
    const submit = button(root, "Record scene-scoped exception");
    const selected = baseline.production_design!.brand_profile!.rules[1].id;
    await rule.selectOption(selected);
    await field(root, "Campaign").fill("Retained explicit rule choice");
    await field(root, "Recorded author").fill("Test author");
    await field(root, "Reason for exception").fill("Only my chosen saved rule may receive this exception.");
    await expect(submit).toBeEnabled();
    await rule.selectOption("");
    await expect(rule).toHaveValue(""); await expect(submit).toBeDisabled();
    const copy = await ownCopy(page, baseline);
    expect((copy.value.draft.raw as GovernanceRaw).ruleId).toBe("");
    expect(mutationCounts(service)).toEqual(initialCounts);
    expect(await observe("empty selection performs no mutation")).toEqual(baseline);
    await button(page, "Production plan").click(); await button(page, "Brand & taste").click();
    await expect(rule).toHaveValue(""); await expect(submit).toBeDisabled();
    await restart(baseline);
    await expect(rule).toHaveValue(""); await expect(submit).toBeDisabled();
    expect(mutationCounts(service)).toEqual(initialCounts);
    await rule.selectOption(selected);
    await submit.click(); await revision(page, baseline.revision + 1);
    const saved = await observe("only the explicit second rule receives the exception");
    expect(saved.production_design!.brand_exceptions).toHaveLength(baseline.production_design!.brand_exceptions.length + 1);
    expect(saved.production_design!.brand_exceptions.at(-1)!.rule_id).toBe(selected);
    expect(saved.production_design!.brand_profile).toEqual(baseline.production_design!.brand_profile);
    expect(applies(service).slice(initialCounts.apply)).toHaveLength(1);
    await restart(saved);
    expect(await observe("selected exception persists after reopen")).toEqual(saved);
  }, async (service, source) => {
    const profile = source.production_design!.brand_profile!;
    return service.apply(source, { type: "set_brand_governance", profile: {
      ...profile, version: profile.version + 1,
      rules: [...profile.rules, { kind: "forbidden_phrase", id: randomUUID(), phrase: "unobserved_second_rule_" + randomUUID() }],
    }, exceptions: [] });
  });
});
