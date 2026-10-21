import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Beat, Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App -> api.ts -> source-built StudioService -> SQLite and journal.
// Arrangement is synthetic; every saved state and original reply is real.
// Faults delay/drop only transport delivery or browser recovery writes.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built studio_review_session and real SQLite are required; no simulated persistence fallback.");
test.setTimeout(120_000);

const r = (num: string, den = "1") => ({ num, den });
const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
const input = (row: Locator, field: "label" | "objective" | "start" | "duration") =>
  row.getByLabel("Beat " + field + (field === "start" || field === "duration" ? " seconds" : ""), { exact: true });
const unconfirmed = (row: Locator) => row.getByRole("region", { name: "Unconfirmed beat operation", exact: true });
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openScene(page: Page, name: string) {
  await button(page, "Canvas").click();
  await page.locator(".tree-row").filter({ has: page.getByText(name, { exact: true }) }).click();
  await expect(page.getByRole("region", { name: "Scene beats", exact: true })).toBeVisible();
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
function savedBeat(project: Project, sceneId: string, beatId: string): Beat | null {
  return project.scenes.find(scene => scene.id === sceneId)?.beats.find(beat => beat.id === beatId) ?? null;
}
function outsideBeats(project: Project, sceneId: string) {
  return { id: project.id, generation: project.generation, branch: project.active_branch, title: project.title,
    scenes: project.scenes.map(scene => scene.id === sceneId ? { ...scene, beats: [] } : scene),
    narrative: project.narrative, brief: project.brief, assets: project.assets, locks: project.locks,
    audio: project.audio, markers: project.markers, deliverables: project.deliverables,
    visualLanguage: project.visual_language, productionDesign: project.production_design,
    proposals: project.proposal_sets, invocations: project.model_invocations, extensions: project.extensions, handoffs: project.handoffs };
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
/** Hold delivery of a real returned Project, preserving source/receipt bytes. */
function holdReply(service: ScopedChangeService, type: "upsert_scene_beat" | "remove_scene_beat") {
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
async function ownCopy(page: Page, project: Project, sceneId: string, beatId: string) {
  return page.evaluate(scope => {
    const encoded = encodeURIComponent(scope), writer = sessionStorage.getItem("motionwright.scene-beat-writer.v1:" + encoded);
    if (!writer) throw new Error("No verified beat writer pointer was retained.");
    const key = "motionwright.scene-beat-recovery.v1:" + encoded + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No beat recovery bytes were retained.");
    return { key, raw, value: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch, sceneId, beatId]));
}
type Failure = { reason: unknown };
const detail = (reason: unknown) => reason instanceof Error ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
interface BeatCase {
  service: ScopedChangeService; peers: ScopedChangeService[]; baseline: Project; beat: Beat;
  sceneId: string; sceneName: string; row: Locator; applyCount: number;
  observations: Record<string, unknown>; cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>;
  restart(expected: Project): Promise<void>;
}
async function withBeat(page: Page, info: TestInfo, run: (value: BeatCase) => Promise<void>) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  const peers: ScopedChangeService[] = [], cleanup: (() => Promise<unknown>)[] = [], observations: Record<string, unknown> = {};
  const failures: unknown[] = []; let final: Project | undefined, primary: Failure | undefined, projectId: string | undefined;
  let custody: StoreFinalization | undefined;
  page.on("dialog", dialog => dialog.accept());
  try {
    const initial = (await service.fixtureBootstrap()).project; projectId = initial.id; final = initial;
    const sceneName = "Recoverable authored beat";
    const expanded = await service.apply(initial, { type: "add_scene", name: sceneName, objective: "Edit text and exact time without losing author decisions", duration_seconds: 4 });
    final = expanded;
    const scene = expanded.scenes.find(item => !initial.scenes.some(prior => prior.id === item.id));
    if (!scene) throw new Error("The real service did not create the arranged scene.");
    const beat: Beat = { id: randomUUID(), label: "Fractional recovery beat", objective: "Original saved objective", start: r("1001", "30000"), duration: r("1001", "30000") };
    const baseline = await service.apply(expanded, { type: "upsert_scene_beat", scene_id: scene.id, beat });
    final = baseline; observations.baseline = baseline;
    const checkpoints: { label: string; project: Project; journal: Awaited<ReturnType<typeof service.history>> }[] = [];
    observations.checkpoints = checkpoints;
    const observe = async (label: string) => {
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) }); return final;
    };
    await service.attachFixturePage(page, baseline.id); await page.goto("/"); await revision(page, baseline.revision); await openScene(page, sceneName);
    const row = page.locator('.scene-beat-row[data-beat-id="' + beat.id + '"]');
    const restart = async (expected: Project) => {
      for (const peer of peers) await peer.close();
      await service.close(); await service.start(); await page.reload(); await revision(page, expected.revision); await openScene(page, sceneName);
      expect(await observe("new service process and actual App reopen")).toEqual(expected);
    };
    await run({ service, peers, baseline, beat, sceneId: scene.id, sceneName, row,
      applyCount: applies(service).length, observations, cleanup, observe, restart });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    try {
      const path = info.outputPath("scene-beat-draft-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + api.ts + source-built StudioService + grants + SQLite + contiguous journal",
        arrangement: "synthetic scene/beat authored through real service changes",
        exclusions: ["installed Tauri", "native-render frames", "audio sample readback", "human acceptance"],
        observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
        failures: failures.map(detail), services: [service, ...peers].map(value => ({
          databasePath: value.databasePath, binaryPath: value.binaryPath, transcript: value.transcript,
          processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, stderr: value.stderr,
        })),
      }, null, 2));
      await info.attach("scene-beat-draft-service-evidence", { path, contentType: "application/json" });
      await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures, "Beat scenario and store finalization retained separate failures", { cause: primary?.reason });
  }
}

test("beat raw drafts survive actual workspace navigation and reload before one exact SQLite commit and restart", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, baseline, beat, sceneId, sceneName, row, applyCount, observe, restart }) => {
    const raw = { label: "  Edited fractional beat  ", objective: "  Author intent\nRaw spacing stays  \n", start: "1e", duration: "1001/" };
    for (const field of ["label", "objective", "start", "duration"] as const) await input(row, field).fill(raw[field]);
    await button(page, "Production").click(); await openScene(page, sceneName);
    for (const field of ["label", "objective", "start", "duration"] as const) await expect(input(row, field)).toHaveValue(raw[field]);
    await page.reload(); await openScene(page, sceneName);
    for (const field of ["label", "objective", "start", "duration"] as const) await expect(input(row, field)).toHaveValue(raw[field]);
    await expect(button(row, "Save beat")).toBeDisabled(); expect(await observe("unsubmitted raw input")).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);
    await input(row, "start").fill("1/48000"); await input(row, "duration").fill("1001/30000"); await button(row, "Save beat").click();
    await revision(page, baseline.revision + 1); await expect(unconfirmed(row)).toHaveCount(0);
    const final = await observe("explicit repaired beat commit");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, label: raw.label.trim(), objective: raw.objective, start: r("1", "48000") });
    expect(outsideBeats(final, sceneId)).toEqual(outsideBeats(baseline, sceneId));
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual([
      { type: "upsert_scene_beat", scene_id: sceneId, beat: savedBeat(final, sceneId, beat.id) },
    ]);
    await page.screenshot({ path: info.outputPath("beat-recovered-and-saved.png"), fullPage: true });
    await restart(final); await expect(input(row, "objective")).toHaveValue(raw.objective); await expect(input(row, "start")).toHaveValue("1/48000");
    expect(applies(service)).toHaveLength(applyCount + 1);
  });
});

test("a held original beat receipt preserves later raw edits through actual workspace remount and a separate save", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, baseline, beat, sceneId, sceneName, row, applyCount, cleanup, observe, restart }) => {
    const hold = holdReply(service, "upsert_scene_beat"); cleanup.push(hold.dispose);
    await input(row, "objective").fill("Submitted objective A"); await button(row, "Save beat").click();
    const actual = await hold.observed; expect(savedBeat(actual, sceneId, beat.id)?.objective).toBe("Submitted objective A");
    await button(page, "Production").click(); await openScene(page, sceneName);
    await input(row, "objective").fill("  Later objective B\n"); await input(row, "start").fill("1e");
    hold.release(); await revision(page, baseline.revision + 1); await expect(unconfirmed(row)).toHaveCount(0);
    await expect(input(row, "objective")).toHaveValue("  Later objective B\n"); await expect(input(row, "start")).toHaveValue("1e");
    await input(row, "start").fill("1/48000"); await button(row, "Save beat").click(); await revision(page, baseline.revision + 2);
    const final = await observe("original ACK followed by a distinct explicit intent");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, objective: "  Later objective B\n", start: r("1", "48000") });
    const actions = applies(service).slice(applyCount);
    expect(actions).toHaveLength(2); expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id);
    expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
    expect(actions.map(value => value.request.revision)).toEqual([baseline.revision, baseline.revision + 1]);
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 2);
  });
});

for (const mode of ["committed", "preempted"] as const) {
  test(`an uncertain beat ${mode} outcome requires real project/history review before any separate intent`, async ({ page }, info) => {
    await withBeat(page, info, async ({ service, peers, baseline, beat, sceneId, sceneName, row, applyCount, cleanup, observations, observe, restart }) => {
      const peer = await service.peer(); peers.push(peer); const original = service.invoke.bind(service);
      cleanup.push(async () => { service.invoke = original; }); let armed = true;
      const diagnostic = "Original beat reply unavailable after actual service outcome";
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!armed || command !== "apply_change" || request?.change.type !== "upsert_scene_beat") return original<T>(command, args);
        armed = false;
        if (mode === "preempted") await peer.apply(await peer.project(baseline.id), { type: "rename_project", title: "Independent title before beat" });
        try { observations.originalServiceOutcome = { returned: await original<Project>(command, args) }; }
        catch (reason) { observations.originalServiceOutcome = { rejected: detail(reason) }; }
        if (mode === "committed") await peer.apply(await peer.project(baseline.id), { type: "rename_project", title: "Independent title after beat" });
        throw new Error(diagnostic);
      };
      await input(row, "objective").fill("Submitted beat objective A"); await button(row, "Save beat").click();
      await expect(unconfirmed(row)).toContainText(diagnostic);
      await input(row, "objective").fill("Later author intent B"); await input(row, "start").fill("1e");
      const before = await observe("actual service outcome before unknown review"), old = await ownCopy(page, baseline, sceneId, beat.id);
      const journal = await service.history(baseline.id, baseline.revision);
      expect(before.revision).toBe(baseline.revision + (mode === "committed" ? 2 : 1));
      expect(savedBeat(before, sceneId, beat.id)?.objective).toBe(mode === "committed" ? "Submitted beat objective A" : beat.objective);
      expect(old.value.draft.pending.outcome).toBe("UNKNOWN");
      await page.reload(); await openScene(page, sceneName); await expect(input(row, "objective")).toHaveValue("Later author intent B");
      await expect(input(row, "start")).toHaveValue("1e"); await expect(button(row, "Save beat")).toBeDisabled();
      expect(applies(service)).toHaveLength(applyCount + 1);
      const historyReads = requests(service, "project_history").length;
      await button(row, "Read saved beat and history").click();
      const review = row.getByRole("region", { name: "Saved beat outcome review", exact: true });
      await expect(review).toContainText("Read saved revision r" + before.revision);
      await expect(review).toContainText("do not identify or acknowledge the earlier request");
      expect(requests(service, "project_history").length).toBeGreaterThan(historyReads);
      await button(row, "Keep earlier beat attempt and continue editing").click(); await expect(unconfirmed(row)).toHaveCount(0);
      expect(await page.evaluate(key => localStorage.getItem(key), old.key)).toBe(old.raw);
      expect(await service.project(baseline.id)).toEqual(before); expect(await service.history(baseline.id, baseline.revision)).toEqual(journal);
      if (mode === "committed") await button(row, "Keep my full beat draft").click();
      await input(row, "start").fill("1/48000"); await button(row, "Save beat").click(); await revision(page, before.revision + 1);
      const final = await observe("separate reviewed author intent");
      expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, objective: "Later author intent B", start: r("1", "48000") });
      expect(final.title).toBe(before.title);
      const actions = applies(service).slice(applyCount); expect(actions).toHaveLength(2);
      expect(actions[1].request.request_id).not.toBe(actions[0].request.request_id); expect(actions[1].request.effect_grant).not.toBe(actions[0].request.effect_grant);
      expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(mode === "committed"
        ? ["upsert_scene_beat", "rename_project", "upsert_scene_beat"] : ["rename_project", "upsert_scene_beat"]);
      observations.retainedOriginal = old; await restart(final); expect(applies(service)).toHaveLength(applyCount + 2);
    });
  });
}

test("a peer retime preserves the local beat draft until explicit text with saved timing is committed", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, peers, baseline, beat, sceneId, sceneName, row, applyCount, observe, restart }) => {
    const peer = await service.peer(); peers.push(peer);
    await input(row, "objective").fill("Local wording across a peer retime"); await input(row, "start").fill("1e");
    const retimed = { ...beat, start: r("1", "4"), duration: r("1", "2") };
    await peer.apply(await peer.project(baseline.id), { type: "upsert_scene_beat", scene_id: sceneId, beat: retimed });
    await page.reload(); await openScene(page, sceneName);
    await expect(input(row, "objective")).toHaveValue("Local wording across a peer retime"); await expect(input(row, "start")).toHaveValue("1e");
    await expect(row.getByRole("region", { name: "Beat draft conflict", exact: true })).toBeVisible();
    await expect(button(row, "Save beat")).toBeDisabled(); expect(applies(service)).toHaveLength(applyCount);
    const original = await ownCopy(page, baseline, sceneId, beat.id);
    await button(row, "Keep my text with saved timing").click();
    await expect(input(row, "start")).toHaveValue("0.25"); await expect(input(row, "duration")).toHaveValue("0.5");
    await button(row, "Save beat").click(); await revision(page, baseline.revision + 2);
    const final = await observe("explicit wording edit preserving independently saved timing");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...retimed, objective: "Local wording across a peer retime" });
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["upsert_scene_beat", "upsert_scene_beat"]);
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 1);
  });
});

test("a peer removal retains beat text across reload and only an explicit recreation creates a new event", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, peers, baseline, beat, sceneId, sceneName, row, applyCount, observe, restart }) => {
    const peer = await service.peer(); peers.push(peer);
    await input(row, "objective").fill("Retain this decision when the peer removes the beat");
    const removed = await peer.apply(await peer.project(baseline.id), { type: "remove_scene_beat", scene_id: sceneId, beat_id: beat.id });
    await page.reload(); await openScene(page, sceneName);
    await expect(input(row, "objective")).toHaveValue("Retain this decision when the peer removes the beat");
    await expect(button(row, "Recreate beat")).toBeDisabled(); expect(await service.project(baseline.id)).toEqual(removed);
    expect(applies(service)).toHaveLength(applyCount);
    await button(row, "Prepare explicit beat recreation").click(); await expect(button(row, "Recreate beat")).toBeEnabled();
    expect(await service.project(baseline.id)).toEqual(removed); expect(applies(service)).toHaveLength(applyCount);
    await button(row, "Recreate beat").click(); await revision(page, baseline.revision + 2);
    const final = await observe("explicit recreation after saved withdrawal");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, objective: "Retain this decision when the peer removes the beat" });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual([
      { type: "remove_scene_beat", scene_id: sceneId, beat_id: beat.id },
      { type: "upsert_scene_beat", scene_id: sceneId, beat: savedBeat(final, sceneId, beat.id) },
    ]);
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 1);
  });
});

test("a shorter saved scene retains invalid beat times until the author explicitly compares and repairs them", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, peers, baseline, beat, sceneId, sceneName, row, applyCount, observe, restart }) => {
    const peer = await service.peer(); peers.push(peer);
    await input(row, "start").fill("3"); await input(row, "duration").fill("1");
    const shorter = await peer.apply(await peer.project(baseline.id), { type: "set_scene_duration", scene_id: sceneId, duration: r("2") });
    await page.reload(); await openScene(page, sceneName); await expect(input(row, "start")).toHaveValue("3");
    await expect(row.getByRole("region", { name: "Beat draft conflict", exact: true })).toContainText("scene duration changed");
    await button(row, "Keep my full beat draft").click(); await expect(button(row, "Save beat")).toBeDisabled();
    expect(await service.project(baseline.id)).toEqual(shorter); expect(applies(service)).toHaveLength(applyCount);
    await page.reload(); await openScene(page, sceneName); await expect(input(row, "start")).toHaveValue("3");
    await input(row, "start").fill("1/48000"); await button(row, "Save beat").click(); await revision(page, baseline.revision + 2);
    const final = await observe("explicitly repaired interval in shorter saved scene");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, start: r("1", "48000"), duration: r("1") });
    expect(final.scenes.find(scene => scene.id === sceneId)?.duration).toEqual(r("2"));
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 1);
  });
});

test("a silent beat writer pointer failure blocks real grants and saves until an explicit copy restore", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, baseline, beat, sceneId, sceneName, row, applyCount, observe, restart }) => {
    const grants = requests(service, "issue_effect_grant").length, raw = "Retained raw words after silent beat pointer failure";
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key: string, value: string) {
        if (this === sessionStorage && key.startsWith("motionwright.scene-beat-writer.v1:")) return;
        original.call(this, key, value);
      };
    });
    await input(row, "objective").fill(raw); await expect(row.getByRole("alert", { name: "Beat backup warning", exact: true })).toContainText("pointer was not confirmed");
    await expect(button(row, "Save beat")).toBeDisabled(); expect(applies(service)).toHaveLength(applyCount);
    expect(requests(service, "issue_effect_grant")).toHaveLength(grants); expect(await observe("blocked before real grant or apply")).toEqual(baseline);
    await page.reload(); await openScene(page, sceneName); await expect(input(row, "objective")).not.toHaveValue(raw);
    const recovery = page.getByRole("region", { name: "Beat recovery copies", exact: true }); await recovery.locator("summary").click();
    await recovery.getByRole("group").filter({ hasText: raw }).getByRole("button", { name: /^Restore beat copy / }).click();
    await expect(input(row, "objective")).toHaveValue(raw); await button(row, "Save beat").click(); await revision(page, baseline.revision + 1);
    const final = await observe("restored copy explicitly saved exactly once"); expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, objective: raw });
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 1);
  });
});

test("a failed beat ACK checkpoint preserves original UNKNOWN bytes and later text until real fresh review", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, baseline, beat, sceneId, sceneName, row, applyCount, cleanup, observations, observe, restart }) => {
    const hold = holdReply(service, "upsert_scene_beat"); cleanup.push(hold.dispose);
    await input(row, "objective").fill("Submitted before ACK backup outage"); await button(row, "Save beat").click(); await hold.observed;
    await input(row, "objective").fill("Later text before ACK backup outage");
    const old = await ownCopy(page, baseline, sceneId, beat.id);
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key: string, value: string) {
        if (this === localStorage && key.startsWith("motionwright.scene-beat-recovery.v1:") && JSON.parse(value).draft.pending === null) return;
        original.call(this, key, value);
      };
    });
    hold.release(); await revision(page, baseline.revision + 1);
    await expect(row.getByRole("alert", { name: "Beat backup warning", exact: true })).toBeVisible();
    await expect(input(row, "objective")).toHaveValue("Later text before ACK backup outage"); await expect(button(row, "Save beat")).toBeDisabled();
    expect(await page.evaluate(key => localStorage.getItem(key), old.key)).toBe(old.raw);
    await page.reload(); await openScene(page, sceneName);
    await expect(input(row, "objective")).toHaveValue("Later text before ACK backup outage"); await expect(unconfirmed(row)).toBeVisible();
    expect(applies(service)).toHaveLength(applyCount + 1);
    await button(row, "Read saved beat and history").click();
    await expect(row.getByRole("region", { name: "Saved beat outcome review", exact: true })).toContainText("Read saved revision r" + (baseline.revision + 1));
    await button(row, "Keep earlier beat attempt and continue editing").click(); await button(row, "Keep my full beat draft").click();
    await button(row, "Save beat").click(); await revision(page, baseline.revision + 2);
    const final = await observe("new explicit save after original ACK custody failure");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, objective: "Later text before ACK backup outage" });
    expect(await page.evaluate(key => localStorage.getItem(key), old.key)).toBe(old.raw); observations.retainedOriginal = old;
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 2);
  });
});

test("a real original beat removal keeps later typing through restart without an automatic recreation event", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, baseline, beat, sceneId, row, applyCount, cleanup, observe, restart }) => {
    const hold = holdReply(service, "remove_scene_beat"); cleanup.push(hold.dispose);
    await button(row, "Remove beat " + beat.label).click(); const removed = await hold.observed;
    expect(savedBeat(removed, sceneId, beat.id)).toBeNull();
    await input(row, "objective").fill("Late wording after removal was submitted"); hold.release(); await revision(page, baseline.revision + 1);
    await expect(input(row, "objective")).toHaveValue("Late wording after removal was submitted"); await expect(button(row, "Recreate beat")).toBeDisabled();
    const final = await observe("original removal with newer retained draft"); await restart(final);
    await expect(input(row, "objective")).toHaveValue("Late wording after removal was submitted"); await expect(button(row, "Recreate beat")).toBeDisabled();
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual([
      { type: "remove_scene_beat", scene_id: sceneId, beat_id: beat.id },
    ]);
    expect(applies(service)).toHaveLength(applyCount + 1);
  });
});

test("a beat exported during local recovery failure imports without a write and saves exactly once to SQLite after repair", async ({ page }, info) => {
  await withBeat(page, info, async ({ service, baseline, beat, sceneId, sceneName, row, applyCount, observations, observe, restart }) => {
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key: string, value: string) {
        if (key.startsWith("motionwright.scene-beat-recovery.v1:") || key.startsWith("motionwright.scene-beat-writer.v1:")) throw new Error("Recovery storage unavailable");
        original.call(this, key, value);
      };
    });
    const rawObjective = "  Portable beat author intent\nPreserved despite local recovery outage  \n";
    await input(row, "objective").fill(rawObjective); await input(row, "start").fill("1e");
    await expect(button(row, "Save beat")).toBeDisabled();
    const pendingDownload = page.waitForEvent("download"); await button(row, "Download beat draft").click();
    const download = await pendingDownload, path = await download.path(); if (!path) throw new Error("The browser did not retain the beat download.");
    const raw = await readFile(path, "utf8"); observations.exportedCopy = JSON.parse(raw);
    expect(JSON.parse(raw).draft.raw.objective).toBe(rawObjective); expect(JSON.parse(raw).draft.raw.start).toBe("1e");
    expect(await observe("download without any service mutation")).toEqual(baseline); expect(applies(service)).toHaveLength(applyCount);
    await page.reload(); await openScene(page, sceneName); await expect(input(row, "objective")).not.toHaveValue(rawObjective);
    const files = page.getByRole("region", { name: "Beat recovery files", exact: true }); await files.locator("summary").click();
    await page.getByLabel("Import beat draft file", { exact: true }).setInputFiles({ name: "retained-beat.json", mimeType: "application/json", buffer: Buffer.from(raw) });
    await expect(files.getByRole("region", { name: "Imported beat candidate", exact: true })).toContainText("Portable beat author intent");
    await button(files, "Use imported beat copy").click(); await expect(input(row, "objective")).toHaveValue(rawObjective);
    await expect(input(row, "start")).toHaveValue("1e"); expect(await service.project(baseline.id)).toEqual(baseline);
    expect(applies(service)).toHaveLength(applyCount);
    await input(row, "start").fill("1/48000"); await button(row, "Save beat").click(); await revision(page, baseline.revision + 1);
    const final = await observe("imported raw input explicitly repaired and saved");
    expect(savedBeat(final, sceneId, beat.id)).toEqual({ ...beat, objective: rawObjective, start: r("1", "48000") });
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change)).toEqual([
      { type: "upsert_scene_beat", scene_id: sceneId, beat: savedBeat(final, sceneId, beat.id) },
    ]);
    await restart(final); expect(applies(service)).toHaveLength(applyCount + 1);
  });
});
