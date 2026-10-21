import { writeFile } from "node:fs/promises";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> api.ts -> source-built StudioService -> SQLite and journal.
// Arrangement is synthetic; saved state, grants, original replies and closed-store
// observations are real. This suite has no simulated persistence fallback.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built studio_review_session and real SQLite are required.");
test.setTimeout(120_000);
declare global { interface Window { __REVIEW_SERVICE_RESTORE__?: () => void } }
const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
const comment = (page: Page) => page.getByLabel("Review comment", { exact: true });
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openReview(page: Page, sceneName: string) {
  await button(page, "Canvas").click();
  await page.locator(".tree-row").filter({ has: page.getByText(sceneName, { exact: true }) }).click();
  await button(page, "Review").click();
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
}
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
function applies(service: ScopedChangeService): ApplyArgs[] {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === "apply_change")
    .map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject };
}
function holdReply(service: ScopedChangeService, type: "add_review" | "resolve_review") {
  const original = service.invoke.bind(service), release = deferred<void>(), observed = deferred<Project>(), finished = deferred<void>();
  void observed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== type) return original<T>(command, args);
    armed = false; matched = true;
    try { const actual = await original<Project>(command, args); observed.resolve(actual); await release.promise; return actual as T; }
    catch (reason) { observed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return { observed: observed.promise, release: () => release.resolve(), async dispose() {
    service.invoke = original; release.resolve(); if (matched) await finished.promise;
  } };
}
async function ownCopy(page: Page, project: Project, resource: string) {
  return page.evaluate(scope => {
    const encoded = encodeURIComponent(scope), writer = sessionStorage.getItem("motionwright.review-writer.v1:" + encoded);
    if (!writer) throw new Error("No verified Review writer pointer");
    const key = "motionwright.review-recovery.v1:" + encoded + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No original Review recovery bytes");
    return { key, raw, value: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch, "comment", resource]));
}
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface ReviewCase {
  service: ScopedChangeService; baseline: Project; sceneId: string; sceneName: string; reviewId: string; alternativeId: string;
  applyCount: number; observations: Record<string, unknown>; cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>;
  restart(expected: Project): Promise<void>;
}
async function withReview(page: Page, info: TestInfo, run: (value: ReviewCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  let final: Project | undefined, projectId: string | undefined, primary: { reason: unknown } | undefined, custody: StoreFinalization | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    const initial = (await service.fixtureBootstrap()).project; projectId = initial.id; final = initial;
    const sceneName = "Review recovery source";
    const expanded = await service.apply(initial, { type: "add_scene", name: sceneName, objective: "Keep exact author review intent", duration_seconds: 5 });
    final = expanded;
    const scene = expanded.scenes.find(item => !initial.scenes.some(prior => prior.id === item.id));
    if (!scene) throw new Error("The real service did not create the arranged Review scene.");
    const reviewed = await service.apply(expanded, { type: "add_review", kind: "creative", resource: "scene:" + scene.id,
      body: "Existing review for resolution custody", start: null, end: null, locale: null, profile_id: null });
    final = reviewed;
    const reviewId = reviewed.reviews.find(item => !expanded.reviews.some(prior => prior.id === item.id))?.id;
    if (!reviewId) throw new Error("The real service did not create the arranged anchored review.");
    const branched = await service.apply(reviewed, { type: "create_branch", name: "Explicit Review alternative" });
    final = branched;
    const alternativeId = branched.branches.find(item => !reviewed.branches.some(prior => prior.id === item.id))?.id;
    if (!alternativeId) throw new Error("The real service did not create the arranged alternative.");
    const alternate = await service.apply(branched, { type: "checkout_branch", branch_id: alternativeId });
    const edited = await service.apply(alternate, { type: "update_scene_objective", scene_id: scene.id, objective: "A distinct saved alternative for editorial review" });
    const baseline = await service.apply(edited, { type: "checkout_branch", branch_id: reviewed.active_branch });
    final = baseline; observations.baseline = baseline;
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    observations.checkpoints = checkpoints;
    const observe = async (label: string) => {
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) }); return final;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision);
    await openReview(page, sceneName);
    const restart = async (expected: Project) => {
      await service.close(); await service.start(); await page.reload(); await revision(page, expected.revision); await openReview(page, sceneName);
      expect(await observe("new service process and actual App reopen")).toEqual(expected);
    };
    await run({ service, baseline, sceneId: scene.id, sceneName, reviewId, alternativeId,
      applyCount: applies(service).length, observations, cleanup, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("review-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + api.ts + source-built StudioService + grants + SQLite + contiguous journal",
        arrangement: "synthetic scene, review and alternative authored through real service changes",
        exclusions: ["installed Tauri", "native application or media execution", "human acceptance"],
        observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary === undefined ? null : detail(primary.reason),
        failures: failures.map(detail), service: { databasePath: service.databasePath, binaryPath: service.binaryPath,
          transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr },
      }, null, 2));
      await info.attach("review-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Review scenario and store finalization retained separate failures", { cause: primary?.reason });
  }
}

test("review raw range survives actual App navigation and reload before one exact SQLite commit and restart", async ({ page }, info) => {
  await withReview(page, info, async ({ service, baseline, sceneName, applyCount, observe, restart }) => {
    await page.getByLabel("Review resource scope", { exact: true }).selectOption("project");
    await comment(page).fill("  Authored review retained exactly before explicit submit  ");
    await page.getByLabel("Review time anchor", { exact: true }).selectOption("range");
    await page.getByLabel("Review range start", { exact: true }).fill("1e");
    await page.getByLabel("Review range end", { exact: true }).fill("1001/");
    await button(page, "Brief").click(); await openReview(page, sceneName);
    await page.getByLabel("Review resource scope", { exact: true }).selectOption("project");
    await expect(comment(page)).toHaveValue("  Authored review retained exactly before explicit submit  ");
    await page.reload(); await openReview(page, sceneName);
    await page.getByLabel("Review resource scope", { exact: true }).selectOption("project");
    await expect(page.getByLabel("Review range start", { exact: true })).toHaveValue("1e");
    await expect(page.getByLabel("Review range end", { exact: true })).toHaveValue("1001/");
    await expect(button(page, "Add anchored review")).toBeDisabled();
    expect(await observe("raw input has no saved effect")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
    await page.getByLabel("Review range start", { exact: true }).fill("1001/30000");
    await page.getByLabel("Review range end", { exact: true }).fill("1/2");
    await button(page, "Add anchored review").click(); await revision(page, baseline.revision + 1);
    await expect(comment(page)).toHaveValue("");
    const saved = await observe("explicit exact-time review");
    const added = saved.reviews.filter(item => !baseline.reviews.some(prior => prior.id === item.id));
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({ body: "Authored review retained exactly before explicit submit", status: "open",
      anchor: { resource: "project:" + baseline.id, branch_id: baseline.active_branch, revision: baseline.revision,
        start: { num: "1001", den: "30000" }, end: { num: "1", den: "2" } } });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["add_review"]);
    await restart(saved); expect(applies(service)).toHaveLength(applyCount + 1);
  });
});
test("a held original review reply preserves later input before a distinct granted save", async ({ page }, info) => {
  await withReview(page, info, async ({ service, baseline, sceneName, applyCount, cleanup, observe, restart }) => {
    const hold = holdReply(service, "add_review"); cleanup.push(hold.dispose);
    await comment(page).fill("Submitted comment A"); await button(page, "Add anchored review").click();
    await hold.observed;
    await button(page, "Brief").click(); await openReview(page, sceneName);
    await comment(page).fill("  Later comment B  "); hold.release();
    await revision(page, baseline.revision + 1); await expect(comment(page)).toHaveValue("  Later comment B  ");
    await button(page, "Add anchored review").click(); await revision(page, baseline.revision + 2);
    await expect(comment(page)).toHaveValue("");
    const saved = await observe("original receipt and separate later comment"), actions = applies(service).slice(applyCount);
    expect(saved.reviews.filter(item => ["Submitted comment A", "Later comment B"].includes(item.body))).toHaveLength(2);
    expect(actions).toHaveLength(2);
    expect(actions.map(value => value.request.revision)).toEqual([baseline.revision, baseline.revision + 1]);
    expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    await restart(saved);
  });
});
test("a committed but lost review reply remains UNKNOWN through explicit saved-history inspection and a separate intent", async ({ page }, info) => {
  await withReview(page, info, async ({ service, baseline, sceneId, sceneName, applyCount, observations, cleanup, observe, restart }) => {
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "add_review") return original<T>(command, args);
      armed = false;
      observations.originalReturnedProject = await original<Project>(command, args);
      throw new Error("Original review reply delivery was lost after the actual service committed");
    };
    await comment(page).fill("Original uncertain comment A"); await button(page, "Add anchored review").click();
    await expect(page.getByRole("status").filter({ hasText: "Original review outcome UNKNOWN" })).toHaveCount(1);
    await comment(page).fill("  Separate author comment B  ");
    await page.reload(); await openReview(page, sceneName);
    await expect(comment(page)).toHaveValue("  Separate author comment B  ");
    await expect(button(page, "Add anchored review")).toBeDisabled();
    const prior = await ownCopy(page, baseline, "scene:" + sceneId);
    expect(prior.value.draft.pending.outcome).toBe("UNKNOWN");
    const before = await observe("committed original remains unacknowledged in editor");
    expect(before.revision).toBe(baseline.revision + 1); expect(applies(service)).toHaveLength(applyCount + 1);
    await button(page, "Read saved review and history").click();
    await expect(page.getByRole("region", { name: "Saved review outcome", exact: true }))
      .toContainText("do not identify or acknowledge the original request");
    await button(page, "Keep original attempt and start a separate review").click();
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    expect(await service.project(baseline.id)).toEqual(before); expect(applies(service)).toHaveLength(applyCount + 1);
    await button(page, "Add anchored review").click(); await revision(page, before.revision + 1);
    const saved = await observe("new explicitly authored review"), actions = applies(service).slice(applyCount);
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    expect(saved.reviews.filter(item => ["Original uncertain comment A", "Separate author comment B"].includes(item.body))).toHaveLength(2);
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    observations.originalUnknownCopy = prior; await restart(saved);
  });
});
test("denied review custody prevents any SQLite mutation until a confirmed copy and explicit save", async ({ page }, info) => {
  await withReview(page, info, async ({ service, baseline, sceneName, applyCount, cleanup, observe, restart }) => {
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key.startsWith("motionwright.review-recovery.v1:")) throw new DOMException("quota", "QuotaExceededError");
        return original.call(this, key, value);
      };
      window.__REVIEW_SERVICE_RESTORE__ = () => { Storage.prototype.setItem = original; };
    });
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__REVIEW_SERVICE_RESTORE__?.()); });
    await comment(page).fill("  Kept despite denied recovery storage  ");
    await expect(button(page, "Add anchored review")).toBeDisabled();
    await button(page, "Brief").click(); await openReview(page, sceneName);
    await expect(comment(page)).toHaveValue("  Kept despite denied recovery storage  ");
    expect(await observe("no custody means no request")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
    await page.evaluate(() => window.__REVIEW_SERVICE_RESTORE__!()); await button(page, "Retry review recovery copy").click();
    await button(page, "Add anchored review").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("verified custody followed by explicit review");
    expect(saved.reviews.some(item => item.body === "Kept despite denied recovery storage")).toBe(true);
    expect(applies(service)).toHaveLength(applyCount + 1); await restart(saved);
  });
});
test("an original resolution keeps a later raw reason through actual reopen and new SQLite acknowledgement", async ({ page }, info) => {
  await withReview(page, info, async ({ service, baseline, sceneName, reviewId, applyCount, cleanup, observe, restart }) => {
    const row = page.locator('.critique-copy[data-review-id="' + reviewId + '"]');
    const field = row.getByLabel("Resolution for review " + reviewId, { exact: true });
    const hold = holdReply(service, "resolve_review"); cleanup.push(hold.dispose);
    await field.fill("Submitted resolution A"); await button(row, "Resolve").click(); await hold.observed;
    await button(page, "Brief").click(); await openReview(page, sceneName);
    await field.fill("  Later resolution B  "); hold.release(); await revision(page, baseline.revision + 1);
    await expect(field).toHaveValue("  Later resolution B  ");
    await button(row, "Reopen for recheck").click(); await revision(page, baseline.revision + 2);
    await expect(field).toHaveValue("  Later resolution B  ");
    await button(row, "Resolve").click(); await revision(page, baseline.revision + 3);
    const saved = await observe("exact resolution, reopen and distinct resolution");
    expect(saved.reviews.find(item => item.id === reviewId)).toMatchObject({ status: "resolved", resolution: "Later resolution B" });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type))
      .toEqual(["resolve_review", "reopen_review", "resolve_review"]);
    expect(new Set(applies(service).slice(applyCount).map(value => value.request.request_id)).size).toBe(3);
    await restart(saved);
  });
});
test("an actual saved alternative retains raw rationale and records only an open manual review", async ({ page }, info) => {
  await withReview(page, info, async ({ service, baseline, sceneName, alternativeId, applyCount, observe, restart }) => {
    await page.getByLabel("Creative alternative branch", { exact: true }).selectOption(alternativeId);
    await button(page, "Negative study").click();
    const field = page.getByLabel("Alternative review rationale", { exact: true });
    await field.fill("  This specific saved alternative weakens the authored narrative contrast.  ");
    await button(page, "Brief").click(); await openReview(page, sceneName);
    await page.getByLabel("Creative alternative branch", { exact: true }).selectOption(alternativeId);
    await expect(field).toHaveValue("  This specific saved alternative weakens the authored narrative contrast.  ");
    await expect(button(page, "Negative study")).toHaveAttribute("aria-pressed", "true");
    expect(await observe("raw manual reason only")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
    await button(page, "Record manual negative observation").click(); await revision(page, baseline.revision + 1);
    await expect(field).toHaveValue("");
    const saved = await observe("ordinary open creative review"), added = saved.reviews.filter(item => !baseline.reviews.some(prior => prior.id === item.id));
    expect(added).toHaveLength(1); expect(added[0]).toMatchObject({ kind: "creative", status: "open", resolution: null });
    expect(added[0].body).toContain("MANUAL NEGATIVE CREATIVE ALTERNATIVE REVIEW");
    expect(added[0].body).toContain("Human editorial reason: This specific saved alternative weakens the authored narrative contrast.");
    expect(saved.production_design).toEqual(baseline.production_design);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["add_review"]);
    await restart(saved);
  });
});
