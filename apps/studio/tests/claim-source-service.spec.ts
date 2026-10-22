import { writeFile } from "node:fs/promises";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Change, Claim, Project } from "../src/types";
import { claimCopyKey, claimPointerKey, claimScope } from "../src/claimSourceDraft";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> api.ts -> source-built StudioService -> SQLite/journal.
// Only arrangement and response-delivery faults are synthetic. No mock persistence.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Requires the exact source-built real service helper.");
test.setTimeout(120_000);
declare global { interface Window { __CLAIM_SERVICE_RESTORE__?: () => void } }
const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openClaim(page: Page, claim: Claim) {
  await button(page, "Brief").click();
  const workspace = page.getByRole("region", { name: "Claims and sources", exact: true });
  await workspace.locator(".claim-choice").filter({ has: page.getByText(claim.text, { exact: true }) }).click();
  const editor = workspace.locator('.claim-editor[data-claim-id="' + claim.id + '"]');
  await expect(editor.getByLabel("Claim text", { exact: true })).toBeVisible();
  return editor;
}
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
const applies = (service: ScopedChangeService) => service.transcript
  .filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === "apply_change")
  .map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function holdReply(service: ScopedChangeService, type: "update_claim" | "add_claim") {
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
async function ownCopy(page: Page, project: Project, claimId: string) {
  const scope = claimScope(project, claimId), pointerKey = claimPointerKey(scope);
  const writer = await page.evaluate(key => sessionStorage.getItem(key), pointerKey);
  if (!writer) throw new Error("No verified Claim writer pointer");
  const key = claimCopyKey(scope, writer), raw = await page.evaluate(value => localStorage.getItem(value), key);
  if (!raw) throw new Error("No original Claim recovery bytes");
  return { key, raw, value: JSON.parse(raw) };
}
async function failInitialClaimReadOnReload(page: Page, key: string, kind: "copy" | "pointer") {
  await page.addInitScript(({ target, kind }) => {
    const guard = "motionwright.claim-test.initial-read-fault:" + target;
    if (sessionStorage.getItem(guard)) return;
    sessionStorage.setItem(guard, "installed");
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = function(name) {
      if (this === (kind === "pointer" ? window.sessionStorage : window.localStorage) && name === target)
        throw new Error("Injected initial Claim " + kind + " read failure");
      return original.call(this, name);
    };
    window.__CLAIM_SERVICE_RESTORE__ = () => { Storage.prototype.getItem = original; };
  }, { target: key, kind });
}
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface ClaimCase {
  service: ScopedChangeService; baseline: Project; claim: Claim; other: Claim; reviewId: string; editor: Locator;
  applyCount: number; observations: Record<string, unknown>; cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>;
  restart(expected: Project, selected?: Claim): Promise<Locator>;
}
async function withClaim(page: Page, info: TestInfo, run: (value: ClaimCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  let final: Project | undefined, projectId: string | undefined, primary: { reason: unknown } | undefined, custody: StoreFinalization | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    let project = (await service.fixtureBootstrap()).project; projectId = project.id; final = project;
    const originals = new Set(project.brief.claims.map(claim => claim.id));
    for (const text of ["Claim source continuity fixture", "Untouched second source fixture"]) {
      project = await service.apply(project, { type: "add_claim", text, context: "Synthetic continuity scenario",
        source: { kind: "manual_note", label: "Original source note" }, source_revision: "v1" }); final = project;
    }
    const [claim, other] = project.brief.claims.filter(value => !originals.has(value.id));
    if (!claim || !other) throw new Error("The actual service did not author both arranged claims.");
    const reviewed = await service.apply(project, { type: "add_review", kind: "editorial", resource: "claim:" + claim.id,
      body: "Review the original source", start: null, end: null, locale: null, profile_id: null });
    final = reviewed;
    const reviewId = reviewed.reviews.find(value => !project.reviews.some(prior => prior.id === value.id))?.id;
    if (!reviewId) throw new Error("The actual service did not author the arranged Claim review.");
    const baseline = await service.apply(reviewed, { type: "resolve_review", review_id: reviewId, resolution: "Original source reviewed" });
    final = baseline; observations.baseline = baseline;
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    observations.checkpoints = checkpoints;
    const observe = async (label: string) => {
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision, 2048) }); return final;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision);
    const editor = await openClaim(page, claim);
    const restart = async (expected: Project, selected = expected.brief.claims.find(value => value.id === claim.id)!) => {
      await service.close(); await service.start(); await page.reload(); await revision(page, expected.revision);
      const next = await openClaim(page, selected);
      expect(await observe("new service process and actual App reopen")).toEqual(expected);
      return next;
    };
    await run({ service, baseline, claim, other, reviewId, editor, applyCount: applies(service).length, observations, cleanup, observe, restart });
    const screenshot = info.outputPath("claim-editor.png");
    await page.screenshot({ path: screenshot, fullPage: true });
    await info.attach("actual-app-claim-editor", { path: screenshot, contentType: "image/png" });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("claim-source-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + api.ts + source-built StudioService + grants + SQLite + contiguous journal",
        arrangement: "synthetic claims and ordinary manual review authored through actual service changes",
        exclusions: ["installed Tauri", "native audiovisual realization", "claim factual verification", "human creative acceptance"],
        observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary === undefined ? null : detail(primary.reason),
        failures: failures.map(detail), service: { databasePath: service.databasePath, binaryPath: service.binaryPath,
          transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr },
      }, null, 2));
      await info.attach("claim-source-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Claim scenario and closed-store custody retained separate failures", { cause: primary?.reason });
  }
}

test("raw source revision and inactive source buffers survive App navigation and reload before one valid commit", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, editor, applyCount, observe, restart }) => {
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("url");
    await editor.getByLabel("Claim source URL", { exact: true }).fill("https://example.invalid/authored-source");
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("project_revision");
    await editor.getByLabel("Claim source revision", { exact: true }).fill("1e");
    await button(page, "Canvas").click(); await openClaim(page, claim);
    await expect(editor.getByLabel("Claim source revision", { exact: true })).toHaveValue("1e");
    await page.reload(); await openClaim(page, claim);
    await expect(editor.getByLabel("Claim source revision", { exact: true })).toHaveValue("1e");
    await expect(button(editor, "Save claim changes")).toBeDisabled();
    expect(await observe("raw incomplete source has no service effect")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("url");
    await expect(editor.getByLabel("Claim source URL", { exact: true })).toHaveValue("https://example.invalid/authored-source");
    await button(editor, "Save claim changes").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicitly saved URL");
    expect(saved.brief.claims.find(value => value.id === claim.id)).toEqual({ ...claim, source: { kind: "url", url: "https://example.invalid/authored-source" } });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["update_claim"]);
    await restart(saved);
  });
});

test("same-text source edit invalidates its review and supports a new situated review through SQLite reopen", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, other, reviewId, editor, observe, restart }) => {
    await editor.getByLabel("Claim source note", { exact: true }).fill("Revised source with unchanged factual text");
    await expect(editor.getByRole("heading", { name: "Your proposal", exact: true })).toBeVisible();
    await button(editor, "Save claim changes").click(); await revision(page, baseline.revision + 1);
    const edited = await observe("same-text source revision");
    expect(edited.brief.claims.find(value => value.id === claim.id)).toEqual({ ...claim, source: { kind: "manual_note", label: "Revised source with unchanged factual text" } });
    expect(edited.brief.claims.find(value => value.id === other.id)).toEqual(other);
    expect(edited.reviews.find(value => value.id === reviewId)).toMatchObject({ status: "needs_recheck", resolution: null });
    await editor.getByLabel("Claim review comment", { exact: true }).fill("Inspect the changed source at this saved revision");
    await button(editor, "Add claim review at r" + edited.revision).click(); await revision(page, edited.revision + 1);
    const commented = await observe("ordinary claim review");
    const review = commented.reviews.find(value => !edited.reviews.some(prior => prior.id === value.id))!;
    expect(review).toMatchObject({ status: "open", anchor: { resource: "claim:" + claim.id, revision: edited.revision } });
    const row = editor.locator('[data-review-id="' + review.id + '"]');
    await row.getByLabel("Resolution for review " + review.id, { exact: true }).fill("Recorded this manual editorial review");
    await button(row, "Resolve").click(); await revision(page, commented.revision + 1);
    const saved = await observe("manual resolution does not certify factual truth");
    expect(saved.reviews.find(value => value.id === review.id)).toMatchObject({ status: "resolved", resolution: "Recorded this manual editorial review" });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["update_claim", "add_review", "resolve_review"]);
    await restart(saved);
  });
});

test("a committed lost Claim reply stays UNKNOWN through reload and only a separate intent writes again", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, editor, applyCount, observations, cleanup, observe, restart }) => {
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "update_claim") return original<T>(command, args);
      armed = false; observations.originalReturnedProject = await original<Project>(command, args);
      throw new Error("Original Claim reply lost after the actual transaction committed");
    };
    await editor.getByLabel("Claim source note", { exact: true }).fill("Original uncertain source A");
    await button(editor, "Save claim changes").click();
    await expect(editor.getByRole("status", { name: "Unconfirmed claim save" })).toContainText("UNKNOWN");
    await editor.getByLabel("Claim source note", { exact: true }).fill("Later source B retained as author input");
    await page.reload(); await openClaim(page, claim);
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Later source B retained as author input");
    await expect(button(editor, "Save claim changes")).toBeDisabled();
    const prior = await ownCopy(page, baseline, claim.id);
    expect(prior.value.draft.pending.outcome).toBe("UNKNOWN");
    const before = await observe("one committed request still unacknowledged locally");
    expect(before.revision).toBe(baseline.revision + 1); expect(applies(service)).toHaveLength(applyCount + 1);
    await button(editor, "Read saved claim and history").click();
    await expect(editor).toContainText("do not acknowledge the original request");
    expect(applies(service)).toHaveLength(applyCount + 1);
    await button(editor, "Keep original attempt and start a separate edit").click();
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    expect(await service.project(baseline.id)).toEqual(before);
    await button(editor, "Save claim changes").click(); await revision(page, before.revision + 1);
    const saved = await observe("new explicit source edit with a fresh request and grant");
    const actions = applies(service).slice(applyCount);
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    expect(saved.brief.claims.find(value => value.id === claim.id)?.source).toEqual({ kind: "manual_note", label: "Later source B retained as author input" });
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    observations.originalUnknownCopy = prior; await restart(saved);
  });
});

test("a held original reply preserves later source input and another claim's independent draft", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, other, editor, applyCount, cleanup, observe, restart }) => {
    const hold = holdReply(service, "update_claim"); cleanup.push(hold.dispose);
    await editor.getByLabel("Claim source note", { exact: true }).fill("Submitted source A");
    await button(editor, "Save claim changes").click(); await hold.observed;
    await editor.getByLabel("Claim source note", { exact: true }).fill("Later source B");
    const otherEditor = await openClaim(page, other);
    await otherEditor.getByLabel("Claim context", { exact: true }).fill("Other claim's separate local input");
    hold.release(); await revision(page, baseline.revision + 1);
    await expect(otherEditor.getByLabel("Claim context", { exact: true })).toHaveValue("Other claim's separate local input");
    await openClaim(page, claim);
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Later source B");
    await page.reload(); await openClaim(page, claim);
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Later source B");
    await button(editor, "Save claim changes").click(); await revision(page, baseline.revision + 2);
    const saved = await observe("original reply followed by separate later source");
    expect(saved.brief.claims.find(value => value.id === other.id)).toEqual(other);
    expect(applies(service)).toHaveLength(applyCount + 2);
    await restart(saved); await openClaim(page, other);
    await expect(otherEditor.getByLabel("Claim context", { exact: true })).toHaveValue("Other claim's separate local input");
  });
});

for (const fault of ["silent-copy", "silent-pointer"] as const) {
  test("unconfirmed " + fault + " blocks all Claim dispatch until explicit recovery succeeds", async ({ page }, info) => {
    await withClaim(page, info, async ({ service, baseline, claim, editor, applyCount, observations, cleanup, observe, restart }) => {
      await page.evaluate(mode => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) {
          if (mode === "silent-copy" && key.startsWith("motionwright.claim-source-recovery.v1:")) return;
          if (mode === "silent-pointer" && key.startsWith("motionwright.claim-source-writer.v1:")) return;
          return original.call(this, key, value);
        };
        window.__CLAIM_SERVICE_RESTORE__ = () => { Storage.prototype.setItem = original; };
      }, fault);
      cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__?.()); });
      await editor.getByLabel("Claim source note", { exact: true }).fill("Input retained while recovery is unavailable");
      await expect(button(editor, "Save claim changes")).toBeDisabled();
      await button(page, "Canvas").click(); await openClaim(page, claim);
      await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Input retained while recovery is unavailable");
      expect(await observe("unconfirmed copy prevents grant and mutation")).toEqual(baseline);
      expect(applies(service)).toHaveLength(applyCount);
      await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__!());
      await button(editor, "Retry claim recovery copy").click();
      if (fault === "silent-pointer") {
        const recovered = await ownCopy(page, baseline, claim.id);
        observations.pointerRetryBeforeReload = recovered;
        expect(recovered.value.draft.raw.note).toBe("Input retained while recovery is unavailable");
        expect(recovered.value.draft.sequence).toBeGreaterThan(0);
        await page.reload(); await revision(page, baseline.revision);
        const reloadedEditor = await openClaim(page, claim);
        await expect(reloadedEditor.getByLabel("Claim source note", { exact: true })).toHaveValue("Input retained while recovery is unavailable");
        await expect(button(reloadedEditor, "Save claim changes")).toBeEnabled();
        const reloaded = await ownCopy(page, baseline, claim.id);
        observations.pointerRetryAfterReload = reloaded;
        expect(reloaded.key).toBe(recovered.key);
        expect(reloaded.raw).toBe(recovered.raw);
        expect(reloaded.value.draft).toEqual(recovered.value.draft);
        expect(applies(service)).toHaveLength(applyCount);
        expect(await observe("pointer retry survives reload before any explicit save")).toEqual(baseline);
      }
      await button(editor, "Save claim changes").click(); await revision(page, baseline.revision + 1);
      const saved = await observe("verified recovery followed by one explicit save");
      expect(applies(service)).toHaveLength(applyCount + 1); await restart(saved);
    });
  });
}

test("a real external edit causes a stale save conflict without overwriting either author's work", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, editor, applyCount, observe, restart }) => {
    await editor.getByLabel("Claim context", { exact: true }).fill("First author's unsaved context");
    const external = await service.apply(baseline, { type: "set_narrative_premise", premise: "A separate real author changed this premise" });
    await button(editor, "Save claim changes").click();
    await expect(editor.getByRole("status", { name: "Unconfirmed claim save" })).toContainText("UNKNOWN");
    expect(await observe("stale save rejected by actual service")).toEqual(external);
    // Stale authority may be rejected while granting, before apply_change.
    const afterRejectedSave = applies(service).length;
    expect(afterRejectedSave).toBeGreaterThanOrEqual(applyCount + 1);
    expect(afterRejectedSave).toBeLessThanOrEqual(applyCount + 2);
    await page.reload(); await openClaim(page, claim);
    await expect(editor.getByLabel("Claim context", { exact: true })).toHaveValue("First author's unsaved context");
    await expect(button(editor, "Save claim changes")).toBeDisabled();
    expect(applies(service)).toHaveLength(afterRejectedSave);
    await button(editor, "Read saved claim and history").click();
    await button(editor, "Keep original attempt and start a separate edit").click();
    await button(editor, "Save claim changes").click(); await revision(page, external.revision + 1);
    const saved = await observe("explicit new edit preserves the other author's premise");
    expect(saved.narrative).toEqual(external.narrative);
    expect(saved.brief.claims.find(value => value.id === claim.id)?.context).toBe("First author's unsaved context");
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["set_narrative_premise", "update_claim"]);
    await restart(saved);
  });
});

test("explicit removal of source and version preserves claim identity through restart", async ({ page }, info) => {
  await withClaim(page, info, async ({ baseline, claim, editor, observe, restart }) => {
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("none");
    await editor.getByRole("checkbox", { name: "Record a source version", exact: true }).uncheck();
    await expect(editor.getByRole("heading", { name: "Your proposal", exact: true })).toBeVisible();
    await button(editor, "Save claim changes").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("explicit nullable source withdrawal");
    expect(saved.brief.claims.find(value => value.id === claim.id)).toEqual({ ...claim, source: null, source_revision: null });
    await restart(saved);
    await expect(editor.getByLabel("Claim source type", { exact: true })).toHaveValue("none");
    await expect(editor.getByRole("checkbox", { name: "Record a source version", exact: true })).not.toBeChecked();
  });
});

test("a held Add reply cannot erase the next authored claim in the new-claim editor", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, applyCount, cleanup, observe }) => {
    const hold = holdReply(service, "add_claim"); cleanup.push(hold.dispose);
    const workspace = page.getByRole("region", { name: "Claims and sources", exact: true });
    await button(workspace, "New claim").click();
    const editor = workspace.locator('.claim-editor[data-claim-id="new"]');
    await editor.getByLabel("Claim text", { exact: true }).fill("First explicitly authored new claim");
    await button(editor, "Add claim").click(); await hold.observed;
    await editor.getByLabel("Claim text", { exact: true }).fill("Next authored new claim stays local");
    hold.release(); await revision(page, baseline.revision + 1);
    await expect(editor.getByLabel("Claim text", { exact: true })).toHaveValue("Next authored new claim stays local");
    await page.reload(); await button(page, "Brief").click(); await button(workspace, "New claim").click();
    await expect(editor.getByLabel("Claim text", { exact: true })).toHaveValue("Next authored new claim stays local");
    await button(editor, "Add claim").click(); await revision(page, baseline.revision + 2);
    await expect(editor.getByLabel("Claim text", { exact: true })).toHaveValue("");
    const saved = await observe("two explicit authorship actions each create exactly one new identity");
    const added = saved.brief.claims.filter(claim => !baseline.brief.claims.some(prior => prior.id === claim.id));
    expect(added.map(claim => claim.text)).toEqual(["First explicitly authored new claim", "Next authored new claim stays local"]);
    expect(new Set(added.map(claim => claim.id)).size).toBe(2);
    expect(applies(service)).toHaveLength(applyCount + 2);
    await service.close(); await service.start(); expect(await service.project(saved.id)).toEqual(saved);
  });
});

for (const fault of ["copy", "pointer"] as const) test("retry after initial " + fault + " read failure restores untouched raw input before one explicit real save", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, editor, applyCount, observations, cleanup, observe, restart }) => {
    await editor.getByLabel("Claim source note", { exact: true }).fill("Recovered original raw note");
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("project_revision");
    await editor.getByLabel("Claim source revision", { exact: true }).fill("1e");
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("manual_note");
    const original = await ownCopy(page, baseline, claim.id); observations.originalCopy = original;
    await failInitialClaimReadOnReload(page, fault === "copy" ? original.key : claimPointerKey(claimScope(baseline, claim.id)), fault);
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__?.()); });
    await page.reload(); await openClaim(page, claim);
    await expect(editor).toContainText("Injected initial Claim " + fault + " read failure");
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Original source note");
    await expect(button(editor, "Save claim changes")).toBeDisabled();
    await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__!());
    await button(editor, "Retry claim recovery copy").click();
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Recovered original raw note");
    const recovered = await ownCopy(page, baseline, claim.id); observations.recoveredCopy = recovered;
    expect(recovered.value.draft).toEqual(original.value.draft);
    expect(recovered.value.draft.raw.projectRevision).toBe("1e");
    expect(recovered.key).not.toBe(original.key);
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(applies(service)).toHaveLength(applyCount);
    expect(await observe("read recovery did not alter SQLite")).toEqual(baseline);
    await button(editor, "Save claim changes").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("explicit save after original buffers recovered");
    expect(saved.brief.claims.find(value => value.id === claim.id)?.source).toEqual({ kind: "manual_note", label: "Recovered original raw note" });
    expect(applies(service)).toHaveLength(applyCount + 1); await restart(saved);
  });
});

for (const fault of ["copy", "pointer"] as const) test("an edit and return to saved values during initial " + fault + " read failure preserves later intent and original UNKNOWN", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, claim, editor, applyCount, observations, cleanup, observe, restart }) => {
    const invoke = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = invoke; if (!page.isClosed()) await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__?.()); });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "update_claim") return invoke<T>(command, args);
      armed = false; observations.originalReturnedProject = await invoke<Project>(command, args);
      throw new Error("Claim response lost after actual commit before recovery-read scenario");
    };
    await editor.getByLabel("Claim source note", { exact: true }).fill("Actual saved source A");
    await button(editor, "Save claim changes").click();
    await expect(editor).toContainText("Claim response lost after actual commit before recovery-read scenario");
    await editor.getByLabel("Claim source note", { exact: true }).fill("Original unsaved source R");
    const original = await ownCopy(page, baseline, claim.id); observations.originalUnknownCopy = original;
    const saved = await observe("only the original uncertain request committed");
    expect(applies(service)).toHaveLength(applyCount + 1);
    await failInitialClaimReadOnReload(page, fault === "copy" ? original.key : claimPointerKey(claimScope(baseline, claim.id)), fault);
    await page.reload(); await revision(page, saved.revision); await openClaim(page, claim);
    await expect(editor).toContainText("Injected initial Claim " + fault + " read failure");
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Actual saved source A");
    await editor.getByLabel("Claim source note", { exact: true }).fill("Explicit temporary input");
    await editor.getByLabel("Claim source note", { exact: true }).fill("Actual saved source A");
    await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__!());
    await button(editor, "Retry claim recovery copy").click();
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Actual saved source A");
    await expect(editor.getByRole("status", { name: "Unconfirmed claim save" })).toContainText("UNKNOWN");
    await expect(button(editor, "Save claim changes")).toBeDisabled();
    const recovered = await ownCopy(page, baseline, claim.id); observations.recoveredLaterIntent = recovered;
    expect(recovered.value.draft.pending).toEqual(original.value.draft.pending);
    expect(recovered.value.draft.sequence).toBeGreaterThan(original.value.draft.pending.sequence);
    expect(recovered.value.draft.intent).toBe(true);
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    await button(page, "Canvas").click(); await openClaim(page, claim);
    await restart(saved);
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Actual saved source A");
    await expect(editor.getByRole("status", { name: "Unconfirmed claim save" })).toContainText("UNKNOWN");
    expect(await observe("recovery and reopen did not repeat or acknowledge UNKNOWN")).toEqual(saved);
    expect(applies(service)).toHaveLength(applyCount + 1);
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  });
});

test("retry after the first pointer read fails accepts a confirmed empty slot without losing newly authored raw input", async ({ page }, info) => {
  await withClaim(page, info, async ({ service, baseline, applyCount, observations, cleanup, observe, restart }) => {
    const scope = claimScope(baseline, "new"), pointerKey = claimPointerKey(scope);
    const emptySlot = () => page.evaluate(({ scope, pointerKey }) => ({
      pointer: sessionStorage.getItem(pointerKey),
      copies: Object.keys(localStorage).filter(key => {
        try { return JSON.parse(localStorage.getItem(key) ?? "null")?.draft?.scope === scope; }
        catch { return false; }
      }),
    }), { scope, pointerKey });
    observations.initialEmptySlot = await emptySlot();
    expect(observations.initialEmptySlot).toEqual({ pointer: null, copies: [] });
    const marker = "motionwright.claim-test.first-empty-read-fault:" + pointerKey;
    await page.addInitScript(({ target, marker }) => {
      if (sessionStorage.getItem(marker)) return;
      sessionStorage.setItem(marker, "armed");
      const original = Storage.prototype.getItem;
      let armed = true;
      Storage.prototype.getItem = function(name) {
        if (armed && this === window.sessionStorage && name === target) {
          armed = false; sessionStorage.setItem(marker, "fired");
          throw new Error("Injected first Claim pointer read failure");
        }
        return original.call(this, name);
      };
      window.__CLAIM_SERVICE_RESTORE__ = () => { Storage.prototype.getItem = original; };
    }, { target: pointerKey, marker });
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__CLAIM_SERVICE_RESTORE__?.()); });
    await page.reload(); await revision(page, baseline.revision); await button(page, "Brief").click();
    const workspace = page.getByRole("region", { name: "Claims and sources", exact: true });
    await button(workspace, "New claim").click();
    const editor = workspace.locator('.claim-editor[data-claim-id="new"]');
    await expect(editor).toContainText("Injected first Claim pointer read failure");
    expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
    await editor.getByLabel("Claim text", { exact: true }).fill("New claim retained after an unavailable first recovery read");
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("project_revision");
    await editor.getByLabel("Claim source revision", { exact: true }).fill("1e");
    await editor.getByLabel("Claim source type", { exact: true }).selectOption("manual_note");
    await editor.getByLabel("Claim source note", { exact: true }).fill("Author note retained while the first recovery read was blocked");
    await expect(button(editor, "Add claim")).toBeDisabled();
    expect(await emptySlot()).toEqual({ pointer: null, copies: [] });
    expect(await observe("first read failure preserves the empty slot and sends no change")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);

    await button(editor, "Retry claim recovery copy").click();
    await expect(button(editor, "Add claim")).toBeEnabled();
    await expect(editor.getByLabel("Claim text", { exact: true })).toHaveValue("New claim retained after an unavailable first recovery read");
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Author note retained while the first recovery read was blocked");
    const recovered = await ownCopy(page, baseline, "new"); observations.recoveredEmptySlot = recovered;
    expect(recovered.value.draft.raw.projectRevision).toBe("1e");
    expect(recovered.value.draft.pending).toBeNull();
    expect(recovered.value.draft.sequence).toBeGreaterThan(0);
    expect(await observe("confirmed empty-slot recovery only writes a local copy")).toEqual(baseline);
    expect(await service.history(baseline.id, baseline.revision)).toEqual([]);
    expect(applies(service)).toHaveLength(applyCount);

    await page.reload(); await revision(page, baseline.revision); await button(page, "Brief").click();
    await button(workspace, "New claim").click();
    await expect(editor.getByLabel("Claim text", { exact: true })).toHaveValue("New claim retained after an unavailable first recovery read");
    await expect(editor.getByLabel("Claim source note", { exact: true })).toHaveValue("Author note retained while the first recovery read was blocked");
    const reloaded = await ownCopy(page, baseline, "new"); observations.reloadedEmptySlot = reloaded;
    expect(reloaded.value.draft).toEqual(recovered.value.draft);
    expect(applies(service)).toHaveLength(applyCount);
    expect(await observe("reload retains all raw buffers and sequence without dispatch")).toEqual(baseline);

    await button(editor, "Add claim").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicit Add after empty-slot recovery");
    const added = saved.brief.claims.filter(value => !baseline.brief.claims.some(prior => prior.id === value.id));
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({
      text: "New claim retained after an unavailable first recovery read",
      source: { kind: "manual_note", label: "Author note retained while the first recovery read was blocked" },
    });
    expect(saved.brief.claims.filter(value => baseline.brief.claims.some(prior => prior.id === value.id))).toEqual(baseline.brief.claims);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["add_claim"]);
    expect(applies(service)).toHaveLength(applyCount + 1);
    await restart(saved);
  });
});
