import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, DeliverableProfile, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> api.ts -> source-built StudioService -> grants, SQLite and journal.
// Arrangement inputs and transport timing are controlled. No synthetic persistence
// fallback, installed Tauri, native media or human acceptance is asserted.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built studio_review_session and real SQLite are required.");
test.setTimeout(120_000);
declare global { interface Window { __PROFILE_SERVICE_RESTORE__?: () => void } }
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByRole("textbox", { name, exact: true });
const review = (page: Page) => page.getByRole("region", { name: "Output profile draft review", exact: true });
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openProfile(page: Page, id: string) {
  await button(page, "Deliver").click();
  await page.locator('.delivery-profile-row[data-profile-id="' + id + '"]').click();
  await expect(field(page, "Profile name")).toBeVisible();
}
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
function applies(service: ScopedChangeService): ApplyArgs[] {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === "apply_change")
    .map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
}
function grants(service: ScopedChangeService): number {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload
    && entry.payload.command === "issue_effect_grant").length;
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject };
}
function holdReply(service: ScopedChangeService, profileId: string) {
  const original = service.invoke.bind(service), release = deferred<void>(), observed = deferred<Project>(), finished = deferred<void>();
  void observed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "upsert_deliverable"
      || request.change.profile.id !== profileId) return original<T>(command, args);
    armed = false; matched = true;
    try { const actual = await original<Project>(command, args); observed.resolve(actual); await release.promise; return actual as T; }
    catch (reason) { observed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return { observed: observed.promise, release: () => release.resolve(), async dispose() {
    service.invoke = original; release.resolve(); if (matched) await finished.promise;
  } };
}
async function ownCopy(page: Page, project: Project, profileId: string) {
  return page.evaluate(scope => {
    const writer = sessionStorage.getItem("motionwright.delivery-profile-writer.v1:" + scope);
    if (!writer) throw new Error("No verified profile writer pointer.");
    const key = "motionwright.delivery-profile-draft/1:" + scope + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No retained original profile bytes.");
    return { key, raw, value: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch, profileId]));
}
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface ProfileCase {
  service: ScopedChangeService; baseline: Project; profile: DeliverableProfile;
  applyCount: number; observations: Record<string, unknown>; cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>; restart(expected: Project): Promise<void>;
}
async function withProfile(page: Page, info: TestInfo, run: (value: ProfileCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const observations: Record<string, unknown> = {}, cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  let final: Project | undefined, projectId: string | undefined, primary: { reason: unknown } | undefined, custody: StoreFinalization | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    const initial = (await service.fixtureBootstrap()).project; projectId = initial.id; final = initial;
    const profile: DeliverableProfile = { ...structuredClone(initial.deliverables[0]), name: "Profile authority source",
      width: 1920, height: 1080, brand_profile: null, cut_label: null, parent_profile_id: null, source_revision: null,
      adaptation_notes: ["Initial decision"], frame_rate: { num: "30", den: "1" } };
    const baseline = await service.apply(initial, { type: "upsert_deliverable", profile }); final = baseline; observations.baseline = baseline;
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    observations.checkpoints = checkpoints;
    const observe = async (label: string) => {
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) }); return final;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision);
    await openProfile(page, profile.id);
    const restart = async (expected: Project) => {
      await service.close(); await service.start(); await page.reload(); await revision(page, expected.revision); await openProfile(page, profile.id);
      expect(await observe("new service process and actual App reopen")).toEqual(expected);
    };
    await run({ service, baseline, profile, applyCount: applies(service).length, observations, cleanup, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("delivery-profile-authority-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + api.ts + source-built StudioService + grants + SQLite + contiguous journal",
        arrangement: "synthetic profile input authored through real service changes",
        exclusions: ["installed Tauri", "native application or media execution", "human acceptance"],
        observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary === undefined ? null : detail(primary.reason),
        failures: failures.map(detail), service: { databasePath: service.databasePath, binaryPath: service.binaryPath,
          transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr },
      }, null, 2));
      await info.attach("delivery-profile-authority-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Profile scenario and store finalization retained separate failures", { cause: primary?.reason });
  }
}

test("original profile reply confirms exact raw-derived fields in SQLite and after process restart", async ({ page }, info) => {
  await withProfile(page, info, async ({ service, baseline, profile, applyCount, observe, restart }) => {
    await field(page, "Profile name").fill("  Exact profile spelling  ");
    await field(page, "Width").fill("001280"); await field(page, "Language / locale").fill("es-MX");
    await button(page, "Save profile").click(); await revision(page, baseline.revision + 1);
    await expect(review(page)).toContainText("Showing saved output values");
    const saved = await observe("one original exact upsert");
    expect(saved.deliverables.find(item => item.id === profile.id)).toEqual({ ...profile, name: "  Exact profile spelling  ", width: 1280, language: "es-MX" });
    expect(applies(service)).toHaveLength(applyCount + 1);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["upsert_deliverable"]);
    await restart(saved); await expect(field(page, "Profile name")).toHaveValue("  Exact profile spelling  ");
  });
});
test("held original profile reply retains later identical spelling and incomplete input before a separately granted save", async ({ page }, info) => {
  await withProfile(page, info, async ({ service, baseline, profile, applyCount, cleanup, observe, restart }) => {
    const held = holdReply(service, profile.id); cleanup.push(() => held.dispose());
    await field(page, "Profile name").fill("Submitted profile"); await button(page, "Save profile").click();
    const actual = await held.observed; expect(actual.revision).toBe(baseline.revision + 1);
    await field(page, "Profile name").fill("Intervening later spelling");
    await field(page, "Profile name").fill("Submitted profile"); await field(page, "Width").fill("1e");
    held.release(); await revision(page, actual.revision);
    await expect(field(page, "Width")).toHaveValue("1e");
    await expect(review(page)).toContainText("2 output field(s) have pending input.");
    await field(page, "Width").fill("001280"); await button(page, "Save profile").click(); await revision(page, actual.revision + 1);
    const saved = await observe("later author version"), actions = applies(service).slice(applyCount);
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    expect(saved.deliverables.find(item => item.id === profile.id)).toEqual({ ...profile, name: "Submitted profile", width: 1280 });
    await restart(saved);
  });
});
for (const later of [true, false]) test("lost committed profile reply retains UNKNOWN through restart and explicit " + (later ? "later intent" : "clean inspection"), async ({ page }, info) => {
  await withProfile(page, info, async ({ service, baseline, profile, applyCount, observations, cleanup, observe, restart }) => {
    const original = service.invoke.bind(service); let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "upsert_deliverable"
        || request.change.profile.id !== profile.id) return original<T>(command, args);
      armed = false; observations.originalReturnedProject = await original<Project>(command, args);
      throw new Error("Original profile reply delivery was lost after actual commit");
    };
    await field(page, "Profile name").fill("Saved but unacknowledged profile"); await button(page, "Save profile").click();
    await expect(page.getByRole("region", { name: "Unconfirmed profile save", exact: true })).toBeVisible();
    if (later) await field(page, "Profile name").fill("  Distinct later profile intent  ");
    const prior = await ownCopy(page, baseline, profile.id), before = await observe("original saved value remains only an observation");
    expect(prior.value.awaiting.original.id).toBeTruthy(); expect(before.revision).toBe(baseline.revision + 1);
    await restart(before);
    await expect(button(page, "Save profile")).toBeDisabled();
    await expect(button(page, "Keep my input name")).toBeDisabled();
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    await button(page, "Read saved profile and history").click();
    await expect(page.getByRole("region", { name: "Saved profile outcome", exact: true })).toBeVisible();
    await button(page, "Keep original attempt and start a separate profile edit").click();
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    expect(await service.project(baseline.id)).toEqual(before); expect(applies(service)).toHaveLength(applyCount + 1);
    if (later) {
      const separate = await ownCopy(page, baseline, profile.id); expect(separate.key).not.toBe(prior.key);
      await button(page, "Save profile").click(); await revision(page, before.revision + 1);
      const saved = await observe("separate explicit profile intent"), actions = applies(service).slice(applyCount);
      expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
      expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
      expect(saved.deliverables.find(item => item.id === profile.id)!.name).toBe("  Distinct later profile intent  ");
      await restart(saved);
    } else {
      await expect(review(page)).toContainText("Showing saved output values");
      await expect(button(page, "Save profile")).toBeDisabled();
      await restart(before); expect(applies(service)).toHaveLength(applyCount + 1);
    }
    expect(await page.evaluate(key => localStorage.getItem(key), prior.key)).toBe(prior.raw);
    observations.originalUnknownCopy = prior;
  });
});
test("silent profile writer-pointer failure prevents grants and SQLite changes until verified recovery", async ({ page }, info) => {
  await withProfile(page, info, async ({ service, baseline, profile, applyCount, cleanup, observe, restart }) => {
    const grantsBefore = grants(service);
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key.startsWith("motionwright.delivery-profile-writer.v1:")) return;
        return original.call(this, key, value);
      };
      window.__PROFILE_SERVICE_RESTORE__ = () => { Storage.prototype.setItem = original; };
    });
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__PROFILE_SERVICE_RESTORE__?.()); });
    await field(page, "Profile name").fill("  Retained before profile pointer  ");
    await expect(button(page, "Save profile")).toBeDisabled();
    expect(await observe("unverified profile pointer")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount); expect(grants(service)).toBe(grantsBefore);
    await page.evaluate(() => window.__PROFILE_SERVICE_RESTORE__!());
    await button(page, "Verify profile recovery copy").click();
    expect(applies(service)).toHaveLength(applyCount); expect(grants(service)).toBe(grantsBefore);
    await button(page, "Save profile").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("verified pointer followed by explicit save");
    expect(saved.deliverables.find(item => item.id === profile.id)!.name).toBe("  Retained before profile pointer  ");
    expect(applies(service)).toHaveLength(applyCount + 1); await restart(saved);
  });
});
test("profile recovery budget blocks actual transport without evicting any retained original", async ({ page }, info) => {
  await withProfile(page, info, async ({ service, baseline, applyCount, observations, observe, restart }) => {
    const grantsBefore = grants(service);
    const originals = await page.evaluate(() => {
      const rows: { key: string; raw: string }[] = [];
      for (let index = 0; index < 64; index++) {
        const scope = JSON.stringify([crypto.randomUUID(), crypto.randomUUID(), crypto.randomUUID(), crypto.randomUUID()]);
        const key = "motionwright.delivery-profile-draft/1:" + scope + ":" + crypto.randomUUID();
        const raw = JSON.stringify({ version: 1, scope, revision: 0, edits: { name: { base: '"Before"', value: "Retained " + index } } });
        localStorage.setItem(key, raw); rows.push({ key, raw });
      }
      return rows;
    });
    await field(page, "Profile name").fill("New input after the recovery budget");
    await expect(button(page, "Save profile")).toBeDisabled();
    await expect(review(page).getByRole("alert")).toContainText("64-copy");
    expect(await observe("copy budget refuses transport")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount); expect(grants(service)).toBe(grantsBefore);
    expect(await page.evaluate(rows => rows.map(row => ({ key: row.key, raw: localStorage.getItem(row.key) })), originals)).toEqual(originals);
    observations.retainedCopies = originals;
    // The unsaved new input is only in this live window; download before reload.
    const download = page.waitForEvent("download"); await button(page, "Download current profile input").click();
    const artifact = await download; await artifact.saveAs(info.outputPath("retained-overflow-profile-input.json"));
    await restart(baseline);
    expect(await page.evaluate(rows => rows.map(row => ({ key: row.key, raw: localStorage.getItem(row.key) })), originals)).toEqual(originals);
    expect(applies(service)).toHaveLength(applyCount);
  });
});
