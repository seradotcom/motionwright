import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// The actual App calls the source-built StudioService, its grants and SQLite.
// Only fixture arrangement and delivery timing are controlled by this test.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "A source-built StudioService and actual SQLite are required.");
test.setTimeout(120_000);
declare global { interface Window { __BRANCH_RESTORE_STORAGE__?: () => void } }
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const nameField = (page: Page) => page.getByLabel("New branch name", { exact: true });
const create = (page: Page) => page.getByRole("button", { name: /^Create from r\d+$/ });
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
function applies(service: ScopedChangeService): ApplyArgs[] {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === "apply_change")
    .map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openChanges(page: Page) {
  await button(page, "Changes").click();
  await expect(page.getByRole("heading", { name: "Changes", exact: true })).toBeVisible();
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function holdCreate(service: ScopedChangeService) {
  const original = service.invoke.bind(service), observed = deferred<Project>(), release = deferred<void>(), finished = deferred<void>();
  void observed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "create_branch") return original<T>(command, args);
    armed = false; matched = true;
    try {
      const saved = await original<Project>(command, args); observed.resolve(saved);
      await release.promise; return saved as T;
    } catch (reason) { observed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return { observed: observed.promise, release: () => release.resolve(), async dispose() {
    service.invoke = original; release.resolve(); if (matched) await finished.promise;
  } };
}
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface BranchCase {
  service: ScopedChangeService; baseline: Project; applyCount: number;
  cleanup: (() => Promise<unknown>)[]; observations: Record<string, unknown>;
  observe(label: string): Promise<Project>;
  restart(expected: Project): Promise<void>;
}
async function withBranch(page: Page, info: TestInfo, run: (value: BranchCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  let final: Project | undefined, projectId: string | undefined, primary: { reason: unknown } | undefined, custody: StoreFinalization | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    const baseline = (await service.fixtureBootstrap()).project; projectId = baseline.id; final = baseline;
    observations.baseline = baseline;
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    observations.checkpoints = checkpoints;
    const observe = async (label: string) => {
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) }); return final;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision); await openChanges(page);
    const restart = async (expected: Project) => {
      await service.close(); await service.start(); await page.reload(); await revision(page, expected.revision); await openChanges(page);
      expect(await observe("new service process and App reopen")).toEqual(expected);
    };
    await run({ service, baseline, applyCount: applies(service).length, cleanup, observations, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("branch-authoring-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + existing create_branch + original grant + StudioService + SQLite + contiguous journal",
        arrangement: "synthetic initial project, actual branch mutations and saved state",
        exclusions: ["installed Tauri", "native render", "human acceptance"], observations, final,
        canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
        failures: failures.map(detail), service: { databasePath: service.databasePath, binaryPath: service.binaryPath,
          transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr },
      }, null, 2));
      await info.attach("branch-authoring-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Branch scenario and store finalization retained separate failures", { cause: primary?.reason });
  }
}

test("causal: a rejected branch creation preserves the exact authored name", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, applyCount, observe }) => {
    const name = "  Rejected branch proposal  ";
    const original = service.invoke.bind(service);
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      // The actual service rejects a stale revision after a concurrent save.
      if (command === "apply_change" && request?.change.type === "create_branch") {
        const current = await service.project(baseline.id);
        await service.apply(current, { type: "rename_project", title: "Concurrent author revision" });
      }
      return original<T>(command, args);
    };
    await nameField(page).fill(name); await create(page).click();
    await expect(page.getByRole("alert").filter({ hasText: /revision|conflict|stale/i })).toBeVisible();
    await expect(nameField(page)).toHaveValue(name);
    const saved = await observe("actual stale CAS rejected branch");
    expect(saved.branches.map(({ head_revision: _head, ...branch }) => branch))
      .toEqual(baseline.branches.map(({ head_revision: _head, ...branch }) => branch));
    expect(saved.branches.find(branch => branch.id === baseline.active_branch)!.head_revision)
      .toBe(baseline.branches.find(branch => branch.id === baseline.active_branch)!.head_revision + 1);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type))
      .toEqual(["rename_project"]);
    expect(applies(service).slice(applyCount).filter(item => item.request.change.type === "create_branch")).toHaveLength(1);
    service.invoke = original;
  });
});

test("causal: a late original branch reply preserves a later name", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, cleanup, observe, restart }) => {
    const hold = holdCreate(service); cleanup.push(hold.dispose);
    await nameField(page).fill("Submitted branch A"); await create(page).click(); await hold.observed;
    await nameField(page).fill("  Later branch B  "); hold.release(); await revision(page, baseline.revision + 1);
    await expect(nameField(page)).toHaveValue("  Later branch B  ");
    const saved = await observe("original branch committed without erasing later name");
    expect(saved.branches.filter(branch => branch.name === "Submitted branch A")).toHaveLength(1);
    await restart(saved); await expect(nameField(page)).toHaveValue("  Later branch B  ");
  });
});

test("causal: an unsaved branch name survives App navigation and reload without a mutation", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, applyCount, observe }) => {
    await nameField(page).fill("  Branch before closing Studio  ");
    await button(page, "Brief").click(); await openChanges(page);
    await expect(nameField(page)).toHaveValue("  Branch before closing Studio  ");
    await page.reload(); await openChanges(page);
    await expect(nameField(page)).toHaveValue("  Branch before closing Studio  ");
    expect(await observe("unsaved name did not create a branch")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);
  });
});

test("one explicit name creates one editable branch through SQLite and a new service process", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, applyCount, observe, restart }) => {
    await nameField(page).fill("  Editorial alternative with preserved work  "); await create(page).click();
    await revision(page, baseline.revision + 1); await expect(nameField(page)).toHaveValue("");
    const saved = await observe("one exact branch creation");
    const added = saved.branches.filter(branch => !baseline.branches.some(prior => prior.id === branch.id));
    expect(added).toHaveLength(1); expect(added[0].name).toBe("Editorial alternative with preserved work");
    const workspace = saved.branch_workspaces.find(item => item.branch_id === added[0].id)!;
    expect(workspace.base_state.scenes).toEqual(baseline.scenes);
    expect(workspace.current_state.scenes).toEqual(baseline.scenes);
    expect(workspace.current_state.brief).toEqual(baseline.brief);
    expect(workspace.current_state.audio).toEqual(baseline.audio);
    expect(saved.active_branch).toBe(baseline.active_branch);
    expect(applies(service)).toHaveLength(applyCount + 1);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["create_branch"]);
    await restart(saved); await expect(nameField(page)).toHaveValue("");
    const screenshot = info.outputPath("branch-created-reopened.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await info.attach("branch-created-reopened", { path: screenshot, contentType: "image/png" });
  });
});

test("UTF-8 branch limit keeps invalid input without sending it", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, applyCount, observe }) => {
    const raw = "🙂".repeat(31);
    await nameField(page).fill(raw); await expect(create(page)).toBeDisabled();
    await expect(page.getByRole("alert").filter({ hasText: "120 UTF-8 bytes" })).toBeVisible();
    await page.reload(); await openChanges(page); await expect(nameField(page)).toHaveValue(raw);
    expect(await observe("invalid name did not mutate")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
  });
});

for (const failure of ["bytes", "pointer"] as const) {
  test("silent branch recovery " + failure + " loss blocks creation until exact readback", async ({ page }, info) => {
    await withBranch(page, info, async ({ service, baseline, applyCount, cleanup, observe, restart }) => {
      await page.evaluate(kind => {
        const put = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          if (key.startsWith(kind === "bytes" ? "motionwright.branch-recovery.v1:" : "motionwright.branch-writer.v1:")) return;
          put.call(this, key, value);
        };
        window.__BRANCH_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = put; };
      }, failure);
      cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__?.()); });
      await nameField(page).fill("  Recovered after " + failure + " failure  "); await expect(create(page)).toBeDisabled();
      await expect(page.getByRole("alert").filter({ hasText: "not confirmed" })).toBeVisible();
      expect(await observe("unconfirmed backup prevented invoke")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
      await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__!()); await button(page, "Retry branch recovery copy").click();
      await create(page).click(); await revision(page, baseline.revision + 1);
      const saved = await observe("explicit creation after verified readback");
      expect(saved.branches.some(branch => branch.name === "Recovered after " + failure + " failure")).toBe(true);
      expect(applies(service)).toHaveLength(applyCount + 1); await restart(saved);
    });
  });
}

test("a committed but lost branch reply survives reopen and explicit inspection without replay", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, applyCount, cleanup, observations, observe, restart }) => {
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "create_branch") return original<T>(command, args);
      armed = false; observations.originalReturnedProject = await original<Project>(command, args);
      throw new Error("The original branch reply was lost after the real service committed");
    };
    await nameField(page).fill("Original uncertain branch A"); await create(page).click();
    await expect(page.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
    await nameField(page).fill("  Separately authored branch B  "); await page.reload(); await openChanges(page);
    await expect(nameField(page)).toHaveValue("  Separately authored branch B  "); await expect(create(page)).toBeDisabled();
    await page.reload(); await openChanges(page);
    await expect(nameField(page)).toHaveValue("  Separately authored branch B  "); await expect(create(page)).toBeDisabled();
    const prior = await page.evaluate(scope => {
      const pointer = sessionStorage.getItem("motionwright.branch-writer.v1:" + encodeURIComponent(scope));
      const key = "motionwright.branch-recovery.v1:" + encodeURIComponent(scope) + ":" + pointer;
      const raw = localStorage.getItem(key); if (!raw) throw new Error("Missing original branch copy");
      return { key, raw, value: JSON.parse(raw) };
    }, JSON.stringify([baseline.id, baseline.generation, baseline.active_branch]));
    expect(prior.value.draft.pending.outcome).toBe("UNKNOWN");
    await button(page, "Read saved branches and history").click();
    await expect(page.getByRole("region", { name: "Saved branch outcome" })).toContainText("do not identify or acknowledge");
    const before = await observe("original observed but not acknowledged");
    await button(page, "Keep original attempt and start another branch").click();
    await expect(nameField(page)).toHaveValue("  Separately authored branch B  ");
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    expect(await service.project(baseline.id)).toEqual(before); expect(applies(service)).toHaveLength(applyCount + 1);
    await create(page).click(); await revision(page, baseline.revision + 2);
    const saved = await observe("distinct explicit branch after source review"), actions = applies(service).slice(applyCount);
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    expect(saved.branches.filter(branch => ["Original uncertain branch A", "Separately authored branch B"].includes(branch.name))).toHaveLength(2);
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    observations.retainedOriginal = prior; await restart(saved);
  });
});

test("a later A-to-B-to-A branch name stays editable after its original reply and reload", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, cleanup, observe, restart }) => {
    const hold = holdCreate(service); cleanup.push(hold.dispose);
    await nameField(page).fill("Original branch A"); await create(page).click(); await hold.observed;
    await nameField(page).fill("Intermediate branch B"); await nameField(page).fill("Original branch A");
    hold.release(); await revision(page, baseline.revision + 1);
    await expect(nameField(page)).toHaveValue("Original branch A"); await expect(create(page)).toBeDisabled();
    await expect(page.getByRole("alert").filter({ hasText: "already in use" })).toBeVisible();
    const saved = await observe("later ABA input retained without duplicate creation");
    await restart(saved); await expect(nameField(page)).toHaveValue("Original branch A");
  });
});

declare global { interface Window { __BRANCH_POINTER_FAILURE_COUNT__?: number } }

for (const failure of ["silent", "throw", "write-then-throw"] as const) {
  test("restoring an unconfirmed branch preserves both copies after " + failure + " pointer failure", async ({ page }, info) => {
    await withBranch(page, info, async ({ service, baseline, applyCount, cleanup, observations, observe, restart }) => {
      const originalInvoke = service.invoke.bind(service);
      let loseReply = true;
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!loseReply || command !== "apply_change" || request?.change.type !== "create_branch")
          return originalInvoke<T>(command, args);
        loseReply = false;
        observations.originalReturnedProject = await originalInvoke<Project>(command, args);
        throw new Error("The branch was committed but its original reply was lost");
      };
      cleanup.push(async () => { service.invoke = originalInvoke; });
      const author = await page.context().newPage();
      cleanup.push(async () => { if (!author.isClosed()) await author.close(); });
      await service.attachFixturePage(author, baseline.id);
      await author.goto("/"); await revision(author, baseline.revision); await openChanges(author);
      const originalName = "Original uncertain branch for " + failure;
      await nameField(author).fill(originalName); await create(author).click();
      await expect(author.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
      await expect(author.locator(".branch-recovery").getByRole("alert").filter({
        hasText: "The branch was committed but its original reply was lost",
      })).toBeVisible();
      const scope = JSON.stringify([baseline.id, baseline.generation, baseline.active_branch]);
      const originalCopy = await author.evaluate(owner => {
        const pointer = "motionwright.branch-writer.v1:" + encodeURIComponent(owner);
        const writerId = sessionStorage.getItem(pointer);
        if (!writerId) throw new Error("The original author has no recovery pointer");
        const key = "motionwright.branch-recovery.v1:" + encodeURIComponent(owner) + ":" + writerId;
        const raw = localStorage.getItem(key);
        if (!raw) throw new Error("The original uncertain branch has no retained bytes");
        return { pointer, writerId, key, raw };
      }, scope);
      expect(JSON.parse(originalCopy.raw).draft.pending.outcome).toBe("UNKNOWN");
      await author.close();
      service.invoke = originalInvoke;
      const saved = await observe("one real creation with a lost original reply");
      expect(saved.revision).toBe(baseline.revision + 1);
      expect(applies(service)).toHaveLength(applyCount + 1);

      // Another author buffer starts from the actual saved revision and owns
      // a different local copy. Neither buffer is fabricated by the test.
      await page.reload(); await revision(page, saved.revision); await openChanges(page);
      const laterName = "  Separate branch B after " + failure + "  ";
      await nameField(page).fill(laterName);
      const laterCopy = await page.evaluate(owner => {
        const pointer = "motionwright.branch-writer.v1:" + encodeURIComponent(owner);
        const writerId = sessionStorage.getItem(pointer);
        if (!writerId) throw new Error("The later author has no recovery pointer");
        const key = "motionwright.branch-recovery.v1:" + encodeURIComponent(owner) + ":" + writerId;
        const raw = localStorage.getItem(key);
        if (!raw) throw new Error("The later author has no recovery bytes");
        return { pointer, writerId, key, raw };
      }, scope);
      expect(laterCopy.writerId).not.toBe(originalCopy.writerId);
      expect(JSON.parse(laterCopy.raw).draft.name).toBe(laterName);
      const grantRequests = () => service.transcript.filter(entry => entry.direction === "request"
        && "command" in entry.payload && entry.payload.command === "issue_effect_grant").length;
      const grantsBeforeRestore = grantRequests();

      await page.evaluate(({ pointer, target, failure }) => {
        const put = Storage.prototype.setItem;
        window.__BRANCH_POINTER_FAILURE_COUNT__ = 0;
        Storage.prototype.setItem = function(key, value) {
          if (this === sessionStorage && key === pointer && value === target) {
            window.__BRANCH_POINTER_FAILURE_COUNT__!++;
            if (failure === "silent") return;
            if (failure === "write-then-throw") put.call(this, key, value);
            throw new Error("The branch pointer publication failed");
          }
          put.call(this, key, value);
        };
        window.__BRANCH_RESTORE_STORAGE__ = () => { Storage.prototype.setItem = put; };
      }, { pointer: originalCopy.pointer, target: originalCopy.writerId, failure });
      cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__?.()); });
      await page.getByText("Branch recovery copies", { exact: true }).click();
      const source = page.locator(".branch-recovery details > div").filter({
        has: page.getByText(originalName, { exact: true }),
      });
      await expect(source).toHaveCount(1);
      await source.getByRole("button", { name: "Restore branch copy", exact: true }).click();
      expect(await page.evaluate(() => window.__BRANCH_POINTER_FAILURE_COUNT__)).toBe(1);
      await expect(nameField(page)).toHaveValue(originalName);
      await expect(page.getByRole("alert").filter({ hasText: /pointer/ })).toBeVisible();
      await expect(create(page)).toBeDisabled();
      expect(await page.evaluate(key => localStorage.getItem(key), laterCopy.key)).toBe(laterCopy.raw);
      expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);

      await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__!());
      await button(page, "Retry branch recovery copy").click();
      // This exact assertion fails on the original hook: retry writes A into
      // B's writer, destroying the sole durable copy of the later name.
      expect(await page.evaluate(key => localStorage.getItem(key), laterCopy.key)).toBe(laterCopy.raw);
      expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
      await expect(page.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
      await expect(create(page)).toBeDisabled();
      if (failure !== "write-then-throw") {
        const successor = await page.evaluate(pointer => sessionStorage.getItem(pointer), originalCopy.pointer);
        expect(successor).not.toBe(originalCopy.writerId);
        expect(successor).not.toBe(laterCopy.writerId);
        const restored = await page.evaluate(({ scope, writer }) => {
          const raw = localStorage.getItem("motionwright.branch-recovery.v1:" + encodeURIComponent(scope) + ":" + writer);
          if (!raw) throw new Error("The restored branch successor is missing");
          return JSON.parse(raw);
        }, { scope, writer: successor });
        expect(restored.draft.pending).toEqual(JSON.parse(originalCopy.raw).draft.pending);
        expect(restored.draft.origins).toContainEqual({ key: originalCopy.key, raw: originalCopy.raw });
      } else {
        // A setter can write and then throw. The old pointer expectation must
        // still reject retry; reload recovers the verified original instead.
        await expect(page.getByRole("alert").filter({ hasText: /pointer changed/ })).toBeVisible();
      }
      expect(grantRequests()).toBe(grantsBeforeRestore);
      expect(await observe("restore and retry did not dispatch a second creation")).toEqual(saved);
      expect(applies(service)).toHaveLength(applyCount + 1);
      expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["create_branch"]);

      await restart(saved);
      await expect(nameField(page)).toHaveValue(originalName);
      await expect(page.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
      await expect(create(page)).toBeDisabled();
      expect(await page.evaluate(key => localStorage.getItem(key), laterCopy.key)).toBe(laterCopy.raw);
      expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
      expect(applies(service)).toHaveLength(applyCount + 1);
      observations.pointerFailure = failure;
      observations.retainedOriginal = originalCopy;
      observations.retainedLater = laterCopy;
    });
  });
}

declare global { interface Window { __BRANCH_READ_FAILURE_COUNT__?: number } }
async function branchCopy(page: Page, project: Project) {
  return page.evaluate(owner => {
    const pointer = "motionwright.branch-writer.v1:" + encodeURIComponent(owner);
    const writerId = sessionStorage.getItem(pointer);
    if (!writerId) throw new Error("The authored branch has no recovery pointer");
    const key = "motionwright.branch-recovery.v1:" + encodeURIComponent(owner) + ":" + writerId;
    const raw = localStorage.getItem(key);
    if (!raw) throw new Error("The authored branch has no recovery bytes");
    return { pointer, writerId, key, raw };
  }, JSON.stringify([project.id, project.generation, project.active_branch]));
}
async function failBranchReadOnce(page: Page, copy: Pick<Awaited<ReturnType<typeof branchCopy>>, "pointer" | "key" | "writerId">, kind: "pointer" | "copy") {
  const marker = "motionwright.test.branch-read-once:" + copy.writerId + ":" + kind;
  await page.addInitScript(({ pointer, key, kind, marker }) => {
    if (sessionStorage.getItem(marker) === "fired") return;
    const get = Storage.prototype.getItem;
    let armed = true;
    window.__BRANCH_READ_FAILURE_COUNT__ = 0;
    Storage.prototype.getItem = function(candidate) {
      if (armed && candidate === (kind === "pointer" ? pointer : key)
        && this === (kind === "pointer" ? sessionStorage : localStorage)) {
        armed = false;
        sessionStorage.setItem(marker, "fired");
        window.__BRANCH_READ_FAILURE_COUNT__!++;
        throw new Error("The first branch recovery " + kind + " read failed");
      }
      return get.call(this, candidate);
    };
    window.__BRANCH_RESTORE_STORAGE__ = () => { Storage.prototype.getItem = get; };
  }, { pointer: copy.pointer, key: copy.key, kind, marker });
}
for (const kind of ["pointer", "copy"] as const) {
  test("an ordinary branch name survives a failed first " + kind + " read before one explicit creation", async ({ page }, info) => {
    await withBranch(page, info, async ({ service, baseline, applyCount, cleanup, observations, observe, restart }) => {
      const rawName = "  Original raw branch after " + kind + " read failure  ";
      await nameField(page).fill(rawName);
      await expect(nameField(page)).toHaveValue(rawName);
      const original = await branchCopy(page, baseline);
      expect(JSON.parse(original.raw).draft.name).toBe(rawName);
      expect(JSON.parse(original.raw).draft.pending).toBeNull();
      await failBranchReadOnce(page, original, kind);
      cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__?.()); });
      await page.reload(); await revision(page, baseline.revision); await openChanges(page);
      expect(await page.evaluate(() => window.__BRANCH_READ_FAILURE_COUNT__)).toBe(1);
      await expect(page.locator(".branch-recovery").getByRole("alert").filter({ hasText: "first branch recovery" })).toBeVisible();
      await expect(create(page)).toBeDisabled();
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      expect(await observe("read failure retained the saved project")).toEqual(baseline);
      expect(applies(service)).toHaveLength(applyCount);

      await button(page, "Retry branch recovery copy").click();
      await expect(nameField(page)).toHaveValue(rawName);
      await expect(create(page)).toBeEnabled();
      const recovered = await branchCopy(page, baseline);
      expect(recovered.writerId).not.toBe(original.writerId);
      expect(JSON.parse(recovered.raw).draft.name).toBe(rawName);
      expect(JSON.parse(recovered.raw).draft.baseRevision).toBe(baseline.revision);
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      expect(applies(service)).toHaveLength(applyCount);
      expect(await observe("recovery retry did not create a branch")).toEqual(baseline);

      await create(page).click(); await revision(page, baseline.revision + 1);
      const saved = await observe("one explicit creation after original input recovery");
      expect(saved.branches.filter(branch => branch.name === rawName.trim())).toHaveLength(1);
      expect(applies(service)).toHaveLength(applyCount + 1);
      expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["create_branch"]);
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      observations.readFailure = kind; observations.retainedOriginal = original;
      observations.recoveredBeforeCreate = recovered;
      await restart(saved);
      expect(applies(service)).toHaveLength(applyCount + 1);
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    });
  });

  test("an UNKNOWN branch and a deliberately cleared name survive failed first " + kind + " read without replay", async ({ page }, info) => {
    await withBranch(page, info, async ({ service, baseline, applyCount, cleanup, observations, observe, restart }) => {
      const invoke = service.invoke.bind(service); let armed = true;
      cleanup.push(async () => { service.invoke = invoke; });
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!armed || command !== "apply_change" || request?.change.type !== "create_branch") return invoke<T>(command, args);
        armed = false;
        observations.originalReturnedProject = await invoke<Project>(command, args);
        throw new Error("The original branch reply was lost after its real SQLite commit");
      };
      const originalName = "Uncertain original branch before " + kind + " failure";
      await nameField(page).fill(originalName); await create(page).click();
      await expect(page.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
      await expect(page.locator(".branch-recovery").getByRole("alert").filter({ hasText: "real SQLite commit" })).toBeVisible();
      const original = await branchCopy(page, baseline), originalDraft = JSON.parse(original.raw).draft;
      expect(originalDraft.pending.outcome).toBe("UNKNOWN");
      const saved = await observe("one real committed branch before recovery read failure");
      expect(saved.revision).toBe(baseline.revision + 1);
      expect(applies(service)).toHaveLength(applyCount + 1);

      await failBranchReadOnce(page, original, kind);
      cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__?.()); });
      await page.reload(); await revision(page, saved.revision); await openChanges(page);
      expect(await page.evaluate(() => window.__BRANCH_READ_FAILURE_COUNT__)).toBe(1);
      await expect(create(page)).toBeDisabled();
      await nameField(page).fill("A later name typed during recovery failure");
      await expect(nameField(page)).toHaveValue("A later name typed during recovery failure");
      await nameField(page).fill("");
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      await button(page, "Retry branch recovery copy").click();
      await expect(nameField(page)).toHaveValue("");
      await expect(page.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
      await expect(create(page)).toBeDisabled();
      const recovered = await branchCopy(page, baseline), recoveredDraft = JSON.parse(recovered.raw).draft;
      expect(recovered.writerId).not.toBe(original.writerId);
      expect(recoveredDraft.name).toBe("");
      expect(recoveredDraft.sequence).toBeGreaterThan(originalDraft.pending.sequence);
      expect(recoveredDraft.pending).toEqual(originalDraft.pending);
      expect(recoveredDraft.origins).toContainEqual({ key: original.key, raw: original.raw });
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      expect(await observe("recovered UNKNOWN and explicit clear did not replay")).toEqual(saved);
      expect(applies(service)).toHaveLength(applyCount + 1);

      await button(page, "Read saved branches and history").click();
      await expect(page.getByRole("region", { name: "Saved branch outcome" })).toContainText("do not identify or acknowledge");
      expect(applies(service)).toHaveLength(applyCount + 1);
      await restart(saved);
      await expect(nameField(page)).toHaveValue("");
      await expect(page.getByRole("region", { name: "Unconfirmed branch creation" })).toBeVisible();
      await expect(create(page)).toBeDisabled();
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      expect(applies(service)).toHaveLength(applyCount + 1);
      expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["create_branch"]);
      observations.readFailure = kind; observations.retainedOriginal = original; observations.recoveredAfterClear = recovered;
    });
  });
}

test("a failed initial pointer read can recover an empty session without discarding newly typed input", async ({ page }, info) => {
  await withBranch(page, info, async ({ service, baseline, applyCount, cleanup, observations, observe, restart }) => {
    const scope = JSON.stringify([baseline.id, baseline.generation, baseline.active_branch]);
    const pointer = "motionwright.branch-writer.v1:" + encodeURIComponent(scope);
    expect(await page.evaluate(key => sessionStorage.getItem(key), pointer)).toBeNull();
    expect(await page.evaluate(owner => Object.keys(localStorage).filter(key =>
      key.startsWith("motionwright.branch-recovery.v1:" + encodeURIComponent(owner) + ":")), scope)).toEqual([]);
    // Only a read-fault selector is fabricated; no recovery envelope or saved
    // Project is supplied by this test.
    await failBranchReadOnce(page, { pointer, key: "unused", writerId: "empty-session-" + baseline.id }, "pointer");
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__BRANCH_RESTORE_STORAGE__?.()); });
    await page.reload(); await revision(page, baseline.revision); await openChanges(page);
    expect(await page.evaluate(() => window.__BRANCH_READ_FAILURE_COUNT__)).toBe(1);
    await expect(create(page)).toBeDisabled();
    const rawName = "  First branch typed while storage was unavailable  ";
    await nameField(page).fill(rawName);
    await expect(create(page)).toBeDisabled();
    expect(await page.evaluate(key => sessionStorage.getItem(key), pointer)).toBeNull();
    await button(page, "Retry branch recovery copy").click();
    await expect(nameField(page)).toHaveValue(rawName);
    await expect(create(page)).toBeEnabled();
    const recovered = await branchCopy(page, baseline);
    expect(JSON.parse(recovered.raw).draft.name).toBe(rawName);
    expect(JSON.parse(recovered.raw).draft.pending).toBeNull();
    expect(await observe("first successful empty read only confirmed a current recovery copy")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);
    await page.reload(); await revision(page, baseline.revision); await openChanges(page);
    await expect(nameField(page)).toHaveValue(rawName);
    expect(applies(service)).toHaveLength(applyCount);
    await create(page).click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicit creation after empty-session read recovery");
    expect(saved.branches.filter(branch => branch.name === rawName.trim())).toHaveLength(1);
    expect(applies(service)).toHaveLength(applyCount + 1);
    observations.recoveredEmptySession = recovered;
    await restart(saved);
    expect(applies(service)).toHaveLength(applyCount + 1);
  });
});
