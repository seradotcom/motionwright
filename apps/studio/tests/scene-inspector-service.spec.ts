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
declare global { interface Window { __INSPECTOR_SERVICE_RESTORE__?: () => void } }
const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
const objective = (page: Page) => page.getByLabel("Inspector scene objective", { exact: true });
const duration = (page: Page) => page.getByLabel("Scene duration seconds", { exact: true });
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openInspector(page: Page, sceneName: string) {
  await button(page, "Canvas").click();
  await page.locator(".tree-row").filter({ has: page.getByText(sceneName, { exact: true }) }).click();
  await expect(objective(page)).toBeVisible();
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
function holdReply(service: ScopedChangeService, type: "update_scene_objective" | "set_scene_duration") {
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
async function ownCopy(page: Page, project: Project, sceneId: string) {
  return page.evaluate(scope => {
    const encoded = encodeURIComponent(scope), writer = sessionStorage.getItem("motionwright.scene-inspector-writer.v1:" + encoded);
    if (!writer) throw new Error("No verified Inspector writer pointer");
    const key = "motionwright.scene-inspector-recovery.v1:" + encoded + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No original Inspector recovery bytes");
    return { key, raw, value: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch, sceneId]));
}
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface InspectorCase {
  service: ScopedChangeService; baseline: Project; sceneId: string; sceneName: string;
  applyCount: number; observations: Record<string, unknown>; cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>;
  restart(expected: Project): Promise<void>;
}
async function withInspector(page: Page, info: TestInfo, run: (value: InspectorCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  let final: Project | undefined, projectId: string | undefined, primary: { reason: unknown } | undefined, custody: StoreFinalization | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    const initial = (await service.fixtureBootstrap()).project; projectId = initial.id; final = initial;
    const sceneName = "Inspector recovery source";
    const baseline = await service.apply(initial, { type: "add_scene", name: sceneName, objective: "Saved Inspector source", duration_seconds: 5 });
    final = baseline; observations.baseline = baseline;
    const scene = baseline.scenes.find(item => !initial.scenes.some(prior => prior.id === item.id));
    if (!scene) throw new Error("The real service did not create the arranged Inspector scene.");
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    observations.checkpoints = checkpoints;
    const observe = async (label: string) => {
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) }); return final;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision);
    await openInspector(page, sceneName);
    const restart = async (expected: Project) => {
      await service.close(); await service.start(); await page.reload(); await revision(page, expected.revision); await openInspector(page, sceneName);
      expect(await observe("new service process and actual App reopen")).toEqual(expected);
    };
    await run({ service, baseline, sceneId: scene.id, sceneName,
      applyCount: applies(service).length, observations, cleanup, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("scene-inspector-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + api.ts + source-built StudioService + grants + SQLite + contiguous journal",
        arrangement: "synthetic scene authored through real service changes",
        exclusions: ["installed Tauri", "native application or media execution", "human acceptance"],
        observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary === undefined ? null : detail(primary.reason),
        failures: failures.map(detail), service: { databasePath: service.databasePath, binaryPath: service.binaryPath,
          transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr },
      }, null, 2));
      await info.attach("scene-inspector-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Inspector scenario and store finalization retained separate failures", { cause: primary?.reason });
  }
}


test("Inspector raw objective and exact fraction survive actual App navigation before SQLite commits and restart", async ({ page }, info) => {
  await withInspector(page, info, async ({ service, baseline, sceneId, sceneName, applyCount, observations, observe, restart }) => {
    const workspaces = page.getByRole("navigation", { name: "Workspaces", exact: true });
    await expect(button(workspaces, "Canvas")).toHaveAttribute("aria-current", "page");
    await objective(page).fill("  Exact Inspector objective\n  retained  ");
    await duration(page).fill("  1001/30  ");
    await button(page, "Brief").click(); await openInspector(page, sceneName);
    await page.reload(); await revision(page, baseline.revision); await openInspector(page, sceneName);
    await expect(objective(page)).toHaveValue("  Exact Inspector objective\n  retained  ");
    await expect(duration(page)).toHaveValue("  1001/30  ");
    await expect(button(workspaces, "Canvas")).toHaveAttribute("aria-current", "page");
    await button(page, "Commit objective").click(); await revision(page, baseline.revision + 1);
    await expect(duration(page)).toHaveValue("  1001/30  ");
    await button(page, "Ripple").click(); await revision(page, baseline.revision + 2);
    const saved = await observe("two exact Inspector field commits"), scene = saved.scenes.find(item => item.id === sceneId)!;
    expect(scene.objective).toBe("Exact Inspector objective\n  retained");
    expect(scene.duration).toEqual({ num: "1001", den: "30" });
    expect(applies(service)).toHaveLength(applyCount + 2);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type))
      .toEqual(["update_scene_objective", "set_scene_duration"]);
    // Keep the original two Canvas commits and compare the whole journal after
    // their original process restart before exercising the other final routes.
    const firstOracle = await service.observeStoredProject(saved);
    await restart(saved);
    const firstReopened = await service.observeStoredProject(saved);
    expect(firstReopened.project).toEqual(firstOracle.project);
    expect(firstReopened.journal).toEqual(firstOracle.journal);
    await expect(button(workspaces, "Canvas")).toHaveAttribute("aria-current", "page");

    await button(workspaces, "Storyboard").click();
    await expect(button(workspaces, "Storyboard")).toHaveAttribute("aria-current", "page");
    const storyCard = page.locator(".story-card").filter({ has: page.getByText(sceneName, { exact: true }) });
    await expect(storyCard).toHaveCount(1); await storyCard.click();
    await expect(storyCard).toHaveClass(/selected/);
    await expect(page.locator(".inspector .read-field")).toHaveText(sceneName);
    const storyboardObjective = "Storyboard-authored objective retained through a new service process";
    await objective(page).fill(storyboardObjective);
    await expect(button(workspaces, "Storyboard")).toHaveAttribute("aria-current", "page");
    await button(page, "Commit objective").click(); await revision(page, baseline.revision + 3);
    const storyboardSaved = await observe("Storyboard route original objective commit");
    expect(storyboardSaved.scenes.find(item => item.id === sceneId)!.objective).toBe(storyboardObjective);
    expect(applies(service)).toHaveLength(applyCount + 3);
    expect(applies(service).at(-1)!.request.change).toEqual({
      type: "update_scene_objective", scene_id: sceneId, objective: storyboardObjective,
    });
    await expect(storyCard.locator(".story-copy p")).toHaveText(storyboardObjective);

    await button(workspaces, "Timeline").click();
    await expect(button(workspaces, "Timeline")).toHaveAttribute("aria-current", "page");
    const clip = page.locator(".timeline-clip").filter({ has: page.getByText(sceneName, { exact: true }) });
    await expect(clip).toHaveCount(1); await clip.click();
    await expect(clip).toHaveClass(/selected/);
    await expect(page.locator(".inspector .read-field")).toHaveText(sceneName);
    await duration(page).fill("1501/30");
    await expect(button(workspaces, "Timeline")).toHaveAttribute("aria-current", "page");
    await button(page, "Ripple").click(); await revision(page, baseline.revision + 4);
    const routedSaved = await observe("Timeline route original exact duration commit");
    expect(routedSaved.scenes.find(item => item.id === sceneId)!.duration).toEqual({ num: "1501", den: "30" });
    expect(routedSaved.scenes.find(item => item.id === sceneId)!.objective).toBe(storyboardObjective);
    expect(routedSaved.scenes.map(item => item.id)).toEqual(saved.scenes.map(item => item.id));
    const routeRequests = applies(service).slice(applyCount);
    expect(routeRequests).toHaveLength(4);
    expect(routeRequests.map(value => value.request.revision))
      .toEqual([baseline.revision, baseline.revision + 1, baseline.revision + 2, baseline.revision + 3]);
    expect(routeRequests.map(value => value.request.change)).toEqual([
      { type: "update_scene_objective", scene_id: sceneId, objective: "Exact Inspector objective\n  retained" },
      { type: "set_scene_duration", scene_id: sceneId, duration: { num: "1001", den: "30" } },
      { type: "update_scene_objective", scene_id: sceneId, objective: storyboardObjective },
      { type: "set_scene_duration", scene_id: sceneId, duration: { num: "1501", den: "30" } },
    ]);
    expect(new Set(routeRequests.map(value => value.request.request_id)).size).toBe(4);
    expect(new Set(routeRequests.map(value => value.request.effect_grant)).size).toBe(4);
    const routeJournal = await service.history(baseline.id, baseline.revision);
    expect(routeJournal.map(event => event.revision))
      .toEqual([baseline.revision + 1, baseline.revision + 2, baseline.revision + 3, baseline.revision + 4]);
    expect(routeJournal.map(event => event.change)).toEqual(routeRequests.map(value => value.request.change));

    const beforeReopen = await service.observeStoredProject(routedSaved);
    const exitsBeforeReopen = service.processExits.length;
    const priorProcessId = service.transcript.at(-1)?.processId;
    expect(priorProcessId).toBeDefined();
    await restart(routedSaved);
    expect(service.processExits).toHaveLength(exitsBeforeReopen + 1);
    expect(service.processExits.at(-1)).toMatchObject({ processId: priorProcessId, code: 0, signal: null });
    const afterReopen = await service.observeStoredProject(routedSaved);
    expect(afterReopen.project).toEqual(beforeReopen.project);
    expect(afterReopen.journal).toEqual(beforeReopen.journal);
    const reopenedProcessId = service.transcript.at(-1)?.processId;
    expect(reopenedProcessId).toBeDefined();
    // The close/start helper observes a new child lifetime; PID numbers alone
    // are not an oracle because an OS may reuse a previous number.
    await expect(button(workspaces, "Canvas")).toHaveAttribute("aria-current", "page");
    await expect(objective(page)).toHaveValue(storyboardObjective);
    await expect(duration(page)).toHaveValue("1501/30");
    await page.screenshot({ path: info.outputPath("inspector-reopened-canvas.png"), fullPage: true });
    await button(workspaces, "Storyboard").click(); await storyCard.click();
    await expect(button(workspaces, "Storyboard")).toHaveAttribute("aria-current", "page");
    await expect(storyCard).toHaveClass(/selected/);
    await expect(storyCard.locator(".story-copy p")).toHaveText(storyboardObjective);
    await expect(objective(page)).toHaveValue(storyboardObjective);
    await expect(duration(page)).toHaveValue("1501/30");
    await page.screenshot({ path: info.outputPath("inspector-reopened-storyboard.png"), fullPage: true });
    await button(workspaces, "Timeline").click(); await clip.click();
    await expect(button(workspaces, "Timeline")).toHaveAttribute("aria-current", "page");
    await expect(clip).toHaveClass(/selected/);
    await expect(objective(page)).toHaveValue(storyboardObjective);
    await expect(duration(page)).toHaveValue("1501/30");
    await page.screenshot({ path: info.outputPath("inspector-reopened-timeline.png"), fullPage: true });
    expect(applies(service)).toHaveLength(applyCount + 4);
    expect(await service.project(baseline.id)).toEqual(routedSaved);
    expect((await service.observeStoredProject(routedSaved)).journal).toEqual(beforeReopen.journal);
    observations.finalEditorRoutes = {
      scope: "synthetic actual App authoring through shared Inspector, real service and SQLite; no native or human acceptance",
      commitWorkspaces: ["Canvas", "Canvas", "Storyboard", "Timeline"], sceneId,
      requestIds: routeRequests.map(value => value.request.request_id),
      firstOracle, firstReopened, beforeReopen, afterReopen,
      priorProcessId, reopenedProcessId, originalExit: service.processExits.at(-1),
      reopenedWorkspaces: ["Canvas", "Storyboard", "Timeline"],
      screenshots: ["inspector-reopened-canvas.png", "inspector-reopened-storyboard.png", "inspector-reopened-timeline.png"],
      sourceRevisions: [baseline.revision, saved.revision, storyboardSaved.revision, routedSaved.revision],
      applyCountAfterReadback: applies(service).length,
    };
  });
});
test("held original Inspector reply retains later input before a separately granted save", async ({ page }, info) => {
  await withInspector(page, info, async ({ service, baseline, sceneId, applyCount, cleanup, observe, restart }) => {
    const held = holdReply(service, "update_scene_objective"); cleanup.push(() => held.dispose());
    await objective(page).fill("Submitted Inspector A"); await button(page, "Commit objective").click();
    const originalSaved = await held.observed;
    expect(originalSaved.revision).toBe(baseline.revision + 1);
    await objective(page).fill("  Later Inspector B  "); await duration(page).fill("1e");
    held.release(); await revision(page, baseline.revision + 1);
    await expect(objective(page)).toHaveValue("  Later Inspector B  "); await expect(duration(page)).toHaveValue("1e");
    await button(page, "Commit objective").click(); await revision(page, baseline.revision + 2);
    const saved = await observe("distinct later objective"), actions = applies(service).slice(applyCount);
    expect(saved.scenes.find(item => item.id === sceneId)!.objective).toBe("Later Inspector B");
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    await restart(saved); await expect(duration(page)).toHaveValue("1e"); await expect(button(page, "Ripple")).toBeDisabled();
  });
});
test("committed lost Inspector reply stays UNKNOWN through inspection and a distinct new intent", async ({ page }, info) => {
  await withInspector(page, info, async ({ service, baseline, sceneId, sceneName, applyCount, observations, cleanup, observe, restart }) => {
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "update_scene_objective") return original<T>(command, args);
      armed = false; observations.originalReturnedProject = await original<Project>(command, args);
      throw new Error("Original Inspector reply delivery was lost after the actual commit");
    };
    await objective(page).fill("Original uncertain scene"); await button(page, "Commit objective").click();
    await expect(page.getByRole("region", { name: "Unconfirmed Inspector save", exact: true })).toBeVisible();
    await objective(page).fill("  Distinct later scene intent  ");
    await page.reload(); await openInspector(page, sceneName);
    await expect(objective(page)).toHaveValue("  Distinct later scene intent  "); await expect(button(page, "Commit objective")).toBeDisabled();
    const prior = await ownCopy(page, baseline, sceneId), before = await observe("original saved value is only an observation");
    expect(prior.value.draft.pending.outcome).toBe("UNKNOWN"); expect(before.revision).toBe(baseline.revision + 1);
    await button(page, "Read saved scene and history").click();
    await expect(page.getByRole("region", { name: "Saved Inspector outcome", exact: true }))
      .toContainText("do not identify or acknowledge the original request");
    await button(page, "Keep original attempt and start a separate scene edit").click();
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    expect(await service.project(baseline.id)).toEqual(before); expect(applies(service)).toHaveLength(applyCount + 1);
    await button(page, "Commit objective").click(); await revision(page, before.revision + 1);
    const saved = await observe("separate explicit scene intent"), actions = applies(service).slice(applyCount);
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    expect(saved.scenes.find(item => item.id === sceneId)!.objective).toBe("Distinct later scene intent");
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    observations.originalUnknownCopy = prior; await restart(saved);
  });
});
test("denied Inspector custody prevents SQLite mutation until verified recovery and explicit save", async ({ page }, info) => {
  await withInspector(page, info, async ({ service, baseline, sceneId, sceneName, applyCount, cleanup, observe, restart }) => {
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key.startsWith("motionwright.scene-inspector-recovery.v1:")) throw new DOMException("quota", "QuotaExceededError");
        return original.call(this, key, value);
      };
      window.__INSPECTOR_SERVICE_RESTORE__ = () => { Storage.prototype.setItem = original; };
    });
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__INSPECTOR_SERVICE_RESTORE__?.()); });
    await objective(page).fill("  Stored before transport  "); await expect(button(page, "Commit objective")).toBeDisabled();
    await button(page, "Brief").click(); await openInspector(page, sceneName);
    await expect(objective(page)).toHaveValue("  Stored before transport  ");
    expect(await observe("storage failure blocks transport")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
    await page.evaluate(() => window.__INSPECTOR_SERVICE_RESTORE__!()); await button(page, "Verify Inspector recovery copy").click();
    expect(applies(service)).toHaveLength(applyCount);
    await button(page, "Commit objective").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("explicit save with verified custody");
    expect(saved.scenes.find(item => item.id === sceneId)!.objective).toBe("Stored before transport");
    expect(applies(service)).toHaveLength(applyCount + 1); await restart(saved);
  });
});
test("incomplete Inspector duration survives actual service restart without mutation", async ({ page }, info) => {
  await withInspector(page, info, async ({ service, baseline, applyCount, observe, restart }) => {
    await duration(page).fill("  1001/  "); await expect(button(page, "Ripple")).toBeDisabled();
    expect(await observe("incomplete duration stays author input")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);
    await restart(baseline); await expect(duration(page)).toHaveValue("  1001/  ");
    await expect(button(page, "Ripple")).toBeDisabled(); expect(applies(service)).toHaveLength(applyCount);
  });
});
test("Inspector stale input requires explicit retention against actual saved source", async ({ page }, info) => {
  await withInspector(page, info, async ({ service, baseline, sceneId, sceneName, applyCount, observe, restart }) => {
    await objective(page).fill("  Retained across unrelated source change  ");
    const original = await ownCopy(page, baseline, sceneId);
    const updated = await service.apply(baseline, { type: "set_scene_status", scene_id: sceneId, status: "review" });
    await page.reload(); await revision(page, updated.revision); await openInspector(page, sceneName);
    await expect(objective(page)).toHaveValue("  Retained across unrelated source change  ");
    await expect(button(page, "Commit objective")).toBeDisabled();
    await button(page, "Retain Inspector input against current revision").click();
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await service.project(baseline.id)).toEqual(updated);
    await button(page, "Commit objective").click(); await revision(page, updated.revision + 1);
    const saved = await observe("new revision explicitly reviewed");
    expect(saved.scenes.find(item => item.id === sceneId)!.objective).toBe("Retained across unrelated source change");
    expect(applies(service)).toHaveLength(applyCount + 2); await restart(saved);
  });
});
