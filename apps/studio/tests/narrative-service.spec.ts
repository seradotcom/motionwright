import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Real StudioService, grants, SQLite, and actual App DOM. Fault injection changes
// only reply delivery or browser recovery storage; it never fabricates a saved Project.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built StudioService runner is required; no simulated persistence fallback.");
test.setTimeout(120_000);

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const premise = (page: Page) => page.getByLabel("Narrative premise", { exact: true });
const objective = (page: Page) => page.getByLabel("Scene objective", { exact: true });
const unconfirmed = (page: Page, field = "premise") => page.getByRole("region", { name: "Unconfirmed " + field + " submission", exact: true });
async function openNarrative(page: Page) {
  await button(page, "Narrative").click();
  await expect(page.getByRole("heading", { name: "Narrative", exact: true })).toBeVisible();
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
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
function outsideNarrative(project: Project) {
  return { id: project.id, generation: project.generation, branch: project.active_branch,
    title: project.title, brief: project.brief, scenes: project.scenes.map(({ objective: _objective, ...scene }) => scene),
    beats: project.narrative.beats, protectedSections: project.narrative.protected_sections,
    audio: project.audio, assets: project.assets, markers: project.markers, locks: project.locks,
    deliverables: project.deliverables, visualLanguage: project.visual_language, productionDesign: project.production_design,
    proposals: project.proposal_sets, invocations: project.model_invocations, extensions: project.extensions, handoffs: project.handoffs };
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
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [...preparations, () => page.close()]); }
  catch (reason) { failures.push(reason); }
  try {
    const path = info.outputPath("narrative-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "synthetic authoring scenario against real StudioService + grants + SQLite + App DOM",
      exclusions: ["installed Tauri", "native renderer pixels", "human acceptance"], observations, final,
      canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [service, ...peers].map(value => ({
        databasePath: value.databasePath, binaryPath: value.binaryPath, transcript: value.transcript,
        processExits: value.processExits, lifecycleTaints: value.lifecycleTaints, stderr: value.stderr,
      })),
    }, null, 2));
    await info.attach("narrative-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Narrative scenario and store finalization retained separate failures", { cause: primary?.reason });
}
async function setup(page: Page, info: TestInfo) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  return service;
}
async function attach(page: Page, service: ScopedChangeService, initial: Project) {
  await service.attachFixturePage(page, initial.id); await page.goto("/"); await openNarrative(page);
}
async function ownPremiseCopy(page: Page, project: Project) {
  return page.evaluate(scope => {
    const encoded = encodeURIComponent(scope), writer = sessionStorage.getItem("motionwright.narrative-writer.v1:" + encoded);
    if (!writer) throw new Error("No verified Narrative writer pointer was retained.");
    const key = "motionwright.narrative-recovery.v1:" + encoded + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No Narrative author bytes were retained.");
    return { writer, key, raw, value: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch, "premise", null]));
}

test("Narrative drafts survive actual workspace navigation/reload; separate premise and objective commits survive SQLite restart", async ({ page }, info) => {
  const service = await setup(page, info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
    await attach(page, service, initial);
    const rawPremise = "  End-to-end project narrative\nPreserve this author spacing  \n";
    const rawObjective = "  End-to-end selected scene objective\nPreserve until explicit commit  \n";
    await premise(page).fill(rawPremise); await objective(page).fill(rawObjective);
    await button(page, "Timeline").click(); await openNarrative(page);
    await expect(premise(page)).toHaveValue(rawPremise); await expect(objective(page)).toHaveValue(rawObjective);
    await page.reload(); await openNarrative(page);
    await expect(premise(page)).toHaveValue(rawPremise); await expect(objective(page)).toHaveValue(rawObjective);
    expect(await service.project(initial.id)).toEqual(initial); expect(applies(service)).toHaveLength(0);
    await button(page, "Commit premise").click(); await revision(page, initial.revision + 1);
    await expect(unconfirmed(page)).toHaveCount(0);
    await expect(objective(page)).toHaveValue(rawObjective);
    const premiseSaved = await service.project(initial.id);
    expect(premiseSaved.narrative.premise).toBe(rawPremise);
    expect(premiseSaved.scenes.map(scene => scene.objective)).toEqual(initial.scenes.map(scene => scene.objective));
    await button(page, "Commit objective").click(); await revision(page, initial.revision + 2);
    await expect(unconfirmed(page, "objective")).toHaveCount(0);
    final = await service.project(initial.id);
    expect(final.scenes[0].objective).toBe(rawObjective.trim());
    expect(final.scenes.slice(1)).toEqual(initial.scenes.slice(1));
    expect(outsideNarrative(final)).toEqual(outsideNarrative(initial));
    const events = await service.history(initial.id, initial.revision);
    expect(events.map(event => event.change)).toEqual([
      { type: "set_narrative_premise", premise: rawPremise },
      { type: "update_scene_objective", scene_id: initial.scenes[0].id, objective: rawObjective.trim() },
    ]);
    expect(applies(service)).toHaveLength(2);
    await page.screenshot({ path: info.outputPath("narrative-saved-fields.png"), fullPage: true });
    await service.close(); await service.start(); await page.reload(); await openNarrative(page);
    await expect(premise(page)).toHaveValue(rawPremise); await expect(objective(page)).toHaveValue(rawObjective.trim());
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toEqual(events);
    expect(applies(service)).toHaveLength(2); observations.journal = events;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

for (const field of ["premise", "objective"] as const) {
  test(`a held original ${field} receipt preserves newer typing across actual workspace remount`, async ({ page }, info) => {
    const service = await setup(page, info), observations: Record<string, unknown> = { field };
    let final: Project | undefined, primary: Failure | undefined, hold: ReturnType<typeof holdReply> | undefined;
    try {
      const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
      await attach(page, service, initial);
      const input = field === "premise" ? premise(page) : objective(page);
      const originalRaw = await input.inputValue(), submitted = "Submitted original " + field;
      await input.fill(submitted);
      hold = holdReply(service, field === "premise" ? "set_narrative_premise" : "update_scene_objective");
      await button(page, "Commit " + field).click();
      await expect.poll(() => applies(service).length).toBe(1);
      const observed = await hold.observed;
      expect(observed.revision).toBe(initial.revision + 1);
      await button(page, "Timeline").click(); await openNarrative(page);
      const later = field === "objective" ? originalRaw : "  Later premise typed after service commit\n";
      await input.fill(later);
      hold.release(); await hold.dispose();
      await revision(page, initial.revision + 1);
      await expect(input).toHaveValue(later);
      await expect(unconfirmed(page, field)).toHaveCount(0);
      expect(applies(service)).toHaveLength(1);
      expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
      await page.screenshot({ path: info.outputPath("narrative-later-input-after-original-reply.png"), fullPage: true });
      await expect(button(page, "Commit " + field)).toBeEnabled();
      await button(page, "Commit " + field).click(); await revision(page, initial.revision + 2);
      final = await service.project(initial.id);
      expect(field === "premise" ? final.narrative.premise : final.scenes[0].objective).toBe(field === "premise" ? later : later.trim());
      expect(outsideNarrative(final)).toEqual(outsideNarrative(initial));
      const sent = applies(service); expect(sent).toHaveLength(2);
      expect(sent[1].request.revision).toBe(initial.revision + 1);
      expect(sent[1].request.request_id).not.toBe(sent[0].request.request_id);
      expect(sent[1].request.effect_grant).not.toBe(sent[0].request.effect_grant);
      expect(await service.reopen(initial.id)).toEqual(final);
      observations.originalReceipt = observed; observations.later = later;
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, [], final, observations, hold ? [() => hold!.dispose()] : [], primary); }
  });
}

for (const mode of ["unrelated", "committed"] as const) {
  test(`an uncertain Narrative ${mode} outcome survives reload and is reviewed before a separate intent`, async ({ page }, info) => {
    const service = await setup(page, info), peers: ScopedChangeService[] = [], observations: Record<string, unknown> = { mode };
    const original = service.invoke.bind(service);
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
      const peer = await service.peer(); peers.push(peer); await attach(page, service, initial);
      let armed = true;
      const diagnostic = "Original Narrative transport reply was lost after the real service outcome";
      service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
        const request = args.request as ApplyArgs["request"] | undefined;
        if (!armed || command !== "apply_change" || request?.change.type !== "set_narrative_premise") return original<T>(command, args);
        armed = false;
        if (mode === "unrelated") await peer.apply(await peer.project(initial.id), { type: "rename_project", title: "Independent title before Narrative" });
        try { observations.originalServiceOutcome = { returned: await original<Project>(command, args) }; }
        catch (reason) { observations.originalServiceOutcome = { rejected: detail(reason) }; }
        if (mode === "committed") await peer.apply(await peer.project(initial.id), { type: "rename_project", title: "Independent title after Narrative" });
        throw new Error(diagnostic);
      };
      const submitted = "Submitted premise with unavailable original reply", later = "  Later author input\nPreserved through UNKNOWN\n";
      await premise(page).fill(submitted); await button(page, "Commit premise").click();
      await expect(unconfirmed(page)).toContainText(diagnostic);
      await premise(page).fill(later);
      const saved = await peer.project(initial.id), journal = await peer.history(initial.id, initial.revision);
      expect(saved.revision).toBe(initial.revision + (mode === "committed" ? 2 : 1));
      expect(saved.narrative.premise).toBe(mode === "committed" ? submitted : initial.narrative.premise);
      expect(applies(service)).toHaveLength(1);
      const earlier = await ownPremiseCopy(page, initial);
      expect(earlier.value.draft.pending.outcome).toBe("UNKNOWN");
      await page.reload(); await openNarrative(page);
      await expect(premise(page)).toHaveValue(later);
      await expect(unconfirmed(page)).toContainText(diagnostic);
      await expect(button(page, "Commit premise")).toBeDisabled();
      expect(applies(service)).toHaveLength(1);
      const historyReads = requests(service, "project_history").length;
      await button(page, "Read saved premise and history").click();
      const review = page.getByRole("region", { name: "Saved premise outcome review", exact: true });
      await expect(review).toContainText("Read saved revision r" + saved.revision);
      await expect(review).toContainText("does not identify or acknowledge the earlier request");
      expect(requests(service, "project_history").length).toBeGreaterThan(historyReads);
      await button(page, "Keep earlier premise attempt and continue editing").click();
      await expect(unconfirmed(page)).toHaveCount(0); await expect(premise(page)).toHaveValue(later);
      expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
      expect(await peer.project(initial.id)).toEqual(saved);
      expect(await peer.history(initial.id, initial.revision)).toEqual(journal);
      expect(applies(service)).toHaveLength(1);
      if (mode === "committed") await button(page, "Keep my draft Narrative premise").click();
      await button(page, "Commit premise").click(); await revision(page, saved.revision + 1);
      final = await service.project(initial.id);
      expect(final.narrative.premise).toBe(later); expect(final.title).toBe(saved.title);
      expect(final.scenes).toEqual(saved.scenes); expect(final.brief).toEqual(saved.brief);
      expect(applies(service)).toHaveLength(2);
      expect((await service.history(initial.id, initial.revision)).map(event => event.change.type)).toEqual(mode === "committed"
        ? ["set_narrative_premise", "rename_project", "set_narrative_premise"] : ["rename_project", "set_narrative_premise"]);
      expect(await service.reopen(initial.id)).toEqual(final);
      observations.beforeReview = saved; observations.beforeReviewJournal = journal; observations.retainedOriginal = earlier;
    } catch (reason) { primary = { reason }; }
    finally { service.invoke = original; await finish(page, info, service, peers, final, observations, [], primary); }
  });
}

test("a lost Narrative writer pointer blocks real grants and writes; explicit Restore commits exactly once", async ({ page }, info) => {
  const service = await setup(page, info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project; await attach(page, service, initial);
    const beforeGrants = requests(service, "issue_effect_grant").length, raw = "Author narrative preserved through a silent pointer write failure";
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key: string, value: string) {
        if (this === sessionStorage && key.startsWith("motionwright.narrative-writer.v1:")) return;
        original.call(this, key, value);
      };
    });
    await premise(page).fill(raw);
    await expect(page.getByRole("alert", { name: "Narrative premise backup warning", exact: true })).toContainText("pointer was not confirmed");
    await expect(button(page, "Commit premise")).toBeDisabled();
    expect(applies(service)).toHaveLength(0); expect(requests(service, "issue_effect_grant")).toHaveLength(beforeGrants);
    expect(await service.project(initial.id)).toEqual(initial); expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await page.reload(); await openNarrative(page); await expect(premise(page)).not.toHaveValue(raw);
    const recovery = page.getByRole("region", { name: "Narrative premise recovery", exact: true });
    await recovery.locator("summary").click(); await expect(recovery).toContainText(raw);
    await button(page, "Restore Narrative premise copy 1").click(); await expect(premise(page)).toHaveValue(raw);
    await button(page, "Commit premise").click(); await revision(page, initial.revision + 1);
    final = await service.project(initial.id); expect(final.narrative.premise).toBe(raw);
    expect(outsideNarrative(final)).toEqual(outsideNarrative(initial));
    expect(applies(service)).toHaveLength(1); expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    await service.close(); await service.start(); await page.reload(); await openNarrative(page);
    await expect(premise(page)).toHaveValue(raw); expect(await service.project(initial.id)).toEqual(final);
    expect(applies(service)).toHaveLength(1); observations.grantsBefore = beforeGrants;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

test("a real objective validation refusal preserves both fields until fresh review and correction", async ({ page }, info) => {
  const service = await setup(page, info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project; await attach(page, service, initial);
    const unrelated = "  Keep this separate premise draft  \n", invalid = "x".repeat(4_001);
    await premise(page).fill(unrelated); await objective(page).fill(invalid);
    await button(page, "Commit objective").click();
    await expect(unconfirmed(page, "objective")).toContainText("scene objective is too long");
    await expect(objective(page)).toHaveValue(invalid); await expect(premise(page)).toHaveValue(unrelated);
    expect(await service.project(initial.id)).toEqual(initial); expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await page.reload(); await openNarrative(page);
    await expect(objective(page)).toHaveValue(invalid); await expect(premise(page)).toHaveValue(unrelated);
    await expect(button(page, "Commit objective")).toBeDisabled();
    await button(page, "Read saved objective and history").click();
    await expect(page.getByRole("region", { name: "Saved objective outcome review", exact: true })).toContainText("0 consecutive history records");
    await button(page, "Keep earlier objective attempt and continue editing").click();
    await objective(page).fill("A corrected scene objective within the real domain limit");
    await button(page, "Commit objective").click(); await revision(page, initial.revision + 1);
    final = await service.project(initial.id);
    expect(final.scenes[0].objective).toBe("A corrected scene objective within the real domain limit");
    expect(final.narrative).toEqual(initial.narrative); await expect(premise(page)).toHaveValue(unrelated);
    expect(outsideNarrative(final)).toEqual(outsideNarrative(initial));
    expect(applies(service)).toHaveLength(2); expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    expect(await service.reopen(initial.id)).toEqual(final); observations.refusedLength = invalid.length;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, [], final, observations, [], primary); }
});

test("an independent service author's overlapping premise is reviewed without consuming a disjoint objective draft", async ({ page }, info) => {
  const service = await setup(page, info), peers: ScopedChangeService[] = [], observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project, peer = await service.peer(); peers.push(peer);
    await attach(page, service, initial);
    await premise(page).fill("Local premise decision"); await objective(page).fill("  Disjoint unsent scene objective  \n");
    const peerPremise = await peer.apply(initial, { type: "set_narrative_premise", premise: "Independent saved premise" });
    const saved = await peer.apply(peerPremise, { type: "rename_project", title: "Independent title to preserve" });
    await page.reload(); await openNarrative(page);
    await expect(premise(page)).toHaveValue("Local premise decision");
    await expect(objective(page)).toHaveValue("  Disjoint unsent scene objective  \n");
    const conflict = page.getByRole("region", { name: "Narrative premise conflict", exact: true });
    await expect(conflict).toContainText("Independent saved premise"); await expect(button(page, "Commit premise")).toBeDisabled();
    await button(page, "Keep my draft Narrative premise").click();
    expect(await peer.project(initial.id)).toEqual(saved); expect(applies(service)).toHaveLength(0);
    await button(page, "Commit premise").click(); await revision(page, saved.revision + 1);
    final = await service.project(initial.id);
    expect(final.narrative.premise).toBe("Local premise decision"); expect(final.title).toBe(saved.title);
    expect(final.scenes).toEqual(saved.scenes); await expect(objective(page)).toHaveValue("  Disjoint unsent scene objective  \n");
    expect(applies(service)).toHaveLength(1);
    expect((await service.history(initial.id, initial.revision)).map(event => event.change.type)).toEqual([
      "set_narrative_premise", "rename_project", "set_narrative_premise",
    ]);
    expect(await peer.reopen(initial.id)).toEqual(final); observations.peerSaved = saved;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, peers, final, observations, [], primary); }
});
