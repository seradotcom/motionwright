import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// The App, grants, Project changes, journal, and reopened SQLite are real.
// Faults affect only the browser's recovery read or delivery of one real reply.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "A source-built StudioService runner is required; there is no persistence fallback.");
test.setTimeout(120_000);

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const premise = (page: Page) => page.getByLabel("Narrative premise", { exact: true });
const warning = (page: Page) => page.getByRole("alert", { name: "Narrative premise backup warning", exact: true });
const unconfirmed = (page: Page) => page.getByRole("region", { name: "Unconfirmed premise submission", exact: true });
const diagnostic = "The original Narrative reply was lost after the real service commit";
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
interface Copy { key: string; pointerKey: string; writer: string; raw: string }
function requests(service: ScopedChangeService, command: string) {
  return service.transcript.filter(entry => entry.direction === "request"
    && "command" in entry.payload && entry.payload.command === command);
}
function outsidePremise(project: Project) {
  const { revision: _revision, updated_at: _updatedAt, narrative, ...rest } = project;
  const { premise: _premise, ...otherNarrative } = narrative;
  // Each real commit must advance precisely the active branch head. Preserve
  // every branch's other metadata and every inactive head in the comparison.
  const active = project.branches.filter(branch => branch.id === project.active_branch);
  expect(active).toHaveLength(1);
  expect(active[0].head_revision).toBe(project.revision);
  const branches = project.branches.map(branch => branch.id === project.active_branch
    ? { ...branch, head_revision: 0 } : branch);
  return { ...rest, branches, narrative: otherNarrative };
}
async function open(page: Page) {
  await button(page, "Narrative").click();
  await expect(page.getByRole("heading", { name: "Narrative", exact: true })).toBeVisible();
}
async function copy(page: Page, project: Project): Promise<Copy> {
  return page.evaluate(scope => {
    const pointerKey = "motionwright.narrative-writer.v1:" + encodeURIComponent(scope);
    const writer = sessionStorage.getItem(pointerKey);
    if (!writer) throw new Error("The Narrative pointer is unavailable.");
    const key = "motionwright.narrative-recovery.v1:" + encodeURIComponent(scope) + ":" + writer;
    const raw = localStorage.getItem(key);
    if (raw === null) throw new Error("The Narrative source bytes are unavailable.");
    return { key, pointerKey, writer, raw };
  }, JSON.stringify([project.id, project.generation, project.active_branch, "premise", null]));
}
async function assertOriginal(page: Page, original: Copy, pointer = original.writer) {
  expect(await page.evaluate(value => ({ raw: localStorage.getItem(value.key), pointer: sessionStorage.getItem(value.pointerKey) }), original))
    .toEqual({ raw: original.raw, pointer });
}
async function failFirstSourceReadOnReload(page: Page, original: Copy) {
  const marker = "motionwright.test.narrative-first-read:" + original.writer;
  await page.evaluate(key => sessionStorage.setItem(key, "armed"), marker);
  await page.addInitScript(({ key, marker }) => {
    if (sessionStorage.getItem(marker) !== "armed") return;
    sessionStorage.setItem(marker, "spent");
    const get = Storage.prototype.getItem;
    let armed = true;
    Storage.prototype.getItem = function(name: string) {
      if (armed && this === localStorage && name === key) {
        armed = false;
        throw new Error("Initial Narrative source read is unavailable");
      }
      return get.call(this, name);
    };
  }, { key: original.key, marker });
  await page.reload(); await open(page);
  await expect(warning(page)).toContainText("Initial Narrative source read is unavailable");
}
async function downloadOriginal(page: Page, info: TestInfo, original: Copy) {
  const recovery = page.getByRole("region", { name: "Narrative premise recovery", exact: true });
  await recovery.locator("summary").click();
  const pending = page.waitForEvent("download");
  await button(page, "Download Narrative premise copy 1").click();
  const download = await pending, path = info.outputPath("original-narrative-copy.json");
  await download.saveAs(path);
  const value = JSON.parse(await readFile(path, "utf8"));
  expect(value).toEqual({ schema: "motionwright.local-input-copy/1", storage_key: original.key, raw: original.raw });
  await info.attach("original-narrative-copy", { path, contentType: "application/json" });
}
type Failure = { reason: unknown };
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
async function finish(page: Page, info: TestInfo, service: ScopedChangeService, final: Project | undefined,
  observations: Record<string, unknown>, primary?: Failure, peers: ScopedChangeService[] = []) {
  const failures: unknown[] = primary ? [primary.reason] : [];
  let custody: StoreFinalization | undefined;
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, peers, [() => page.close()]); }
  catch (reason) { failures.push(reason); }
  try {
    const path = info.outputPath("narrative-initial-recovery-service-evidence.json");
    await writeFile(path, JSON.stringify({
      scope: "synthetic authoring and storage-fault scenario against actual App + StudioService + grants + SQLite",
      exclusions: ["installed Tauri", "native renderer pixels", "human acceptance"],
      observations, final, canonicalOracle: custody?.oracle,
      primaryFailure: primary ? detail(primary.reason) : null, failures: failures.map(detail),
      services: [service, ...peers].map(value => ({ databasePath: value.databasePath, binaryPath: value.binaryPath,
        transcript: value.transcript, processExits: value.processExits, lifecycleTaints: value.lifecycleTaints,
        stderr: value.stderr })),
    }, null, 2));
    await info.attach("narrative-initial-recovery-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (reason) { failures.push(reason); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures,
    "Narrative recovery scenario and closed-store finalization retained separate failures", { cause: primary?.reason });
}
async function setup(page: Page, info: TestInfo) {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  return service;
}
async function initialUnknown(page: Page, service: ScopedChangeService, initial: Project,
  observations: Record<string, unknown>) {
  await service.attachFixturePage(page, initial.id); await page.goto("/"); await open(page);
  const invoke = service.invoke.bind(service);
  let armed = true;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "set_narrative_premise") return invoke<T>(command, args);
    armed = false;
    observations.originalServiceOutcome = await invoke<Project>(command, args);
    throw new Error(diagnostic);
  };
  try {
    await premise(page).fill("Submitted premise whose original reply is unavailable");
    await button(page, "Commit premise").click();
    await expect(unconfirmed(page)).toContainText(diagnostic);
    const original = await copy(page, initial), saved = await service.project(initial.id);
    expect(JSON.parse(original.raw).draft.pending.outcome).toBe("UNKNOWN");
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.narrative.premise).toBe("Submitted premise whose original reply is unavailable");
    expect(requests(service, "apply_change")).toHaveLength(1);
    expect((await service.history(initial.id, initial.revision)).map(event => event.change))
      .toEqual([{ type: "set_narrative_premise", premise: saved.narrative.premise }]);
    observations.original = original; observations.savedAfterLostReply = saved;
    return { original, saved };
  } finally { service.invoke = invoke; }
}

for (const later of ["  Later premise preserved through an unread original\n", ""] as const) {
  test(`an initial Narrative read failure preserves UNKNOWN and later ${later ? "text" : "blank"} through explicit recovery and SQLite restart`, async ({ page }, info) => {
    const service = await setup(page, info), observations: Record<string, unknown> = { later };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
      const { original, saved } = await initialUnknown(page, service, initial, observations);
      final = saved;
      await failFirstSourceReadOnReload(page, original);
      const grants = requests(service, "issue_effect_grant").length;
      await premise(page).fill(later);
      await expect(premise(page)).toHaveValue(later);
      await expect(button(page, "Commit premise")).toBeDisabled();
      await assertOriginal(page, original);
      await button(page, "Timeline").click(); await open(page);
      await expect(premise(page)).toHaveValue(later);
      await assertOriginal(page, original);
      await button(page, "Retry premise backup").click();
      await expect(warning(page)).toHaveCount(0);
      await expect(unconfirmed(page)).toContainText(diagnostic);
      await expect(premise(page)).toHaveValue(later);
      await expect(button(page, "Commit premise")).toBeDisabled();
      const retained = await copy(page, initial);
      expect(retained.writer).not.toBe(original.writer);
      expect(JSON.parse(retained.raw).draft.pending).toEqual(JSON.parse(original.raw).draft.pending);
      expect(JSON.parse(retained.raw).draft.raw).toBe(later);
      await assertOriginal(page, original, retained.writer);
      expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      expect(requests(service, "apply_change")).toHaveLength(1);
      expect(await service.project(initial.id)).toEqual(saved);
      await page.reload(); await open(page);
      await expect(unconfirmed(page)).toContainText(diagnostic);
      await expect(premise(page)).toHaveValue(later);
      await button(page, "Read saved premise and history").click();
      await expect(page.getByRole("region", { name: "Saved premise outcome review", exact: true }))
        .toContainText("does not identify or acknowledge the earlier request");
      await expect(unconfirmed(page)).toContainText(diagnostic);
      expect(requests(service, "apply_change")).toHaveLength(1);
      await button(page, "Keep earlier premise attempt and continue editing").click();
      await expect(unconfirmed(page)).toHaveCount(0);
      await expect(premise(page)).toHaveValue(later);
      await button(page, "Keep my draft Narrative premise").click();
      await button(page, "Commit premise").click();
      await expect(page.locator(".revision-chip").first()).toHaveText("r" + (saved.revision + 1));
      final = await service.project(initial.id);
      expect(final.narrative.premise).toBe(later);
      expect(outsidePremise(final)).toEqual(outsidePremise(initial));
      expect(requests(service, "apply_change")).toHaveLength(2);
      const events = await service.history(initial.id, initial.revision);
      expect(events.map(event => event.change)).toEqual([
        { type: "set_narrative_premise", premise: saved.narrative.premise },
        { type: "set_narrative_premise", premise: later },
      ]);
      const sent = requests(service, "apply_change").map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
      expect(sent[1].request.request_id).not.toBe(sent[0].request.request_id);
      expect(sent[1].request.effect_grant).not.toBe(sent[0].request.effect_grant);
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      await page.screenshot({ path: info.outputPath("narrative-recovered-separate-intent.png"), fullPage: true });
      await service.close(); await service.start(); await page.reload(); await open(page);
      await expect(premise(page)).toHaveValue(later);
      expect(await service.project(initial.id)).toEqual(final);
      expect(await service.history(initial.id, initial.revision)).toEqual(events);
      expect(requests(service, "apply_change")).toHaveLength(2);
      observations.journal = events; observations.recoveredCopy = retained;
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, final, observations, primary); }
  });
}

test("a same-writer Narrative replacement cannot consume the first listed UNKNOWN; original bytes remain downloadable", async ({ page }, info) => {
  const service = await setup(page, info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
    const { original, saved } = await initialUnknown(page, service, initial, observations); final = saved;
    await failFirstSourceReadOnReload(page, original);
    const later = "Later input must not consume the unread operation";
    await premise(page).fill(later);
    await assertOriginal(page, original);
    const replacement = await page.evaluate(value => {
      const next = JSON.parse(value.raw);
      next.draft.pending = null; next.draft.intent = false;
      next.draft.raw = next.draft.base;
      const raw = JSON.stringify(next); localStorage.setItem(value.key, raw); return raw;
    }, original);
    const grants = requests(service, "issue_effect_grant").length;
    await button(page, "Retry premise backup").click();
    await expect(warning(page)).toContainText("earlier Narrative recovery bytes changed");
    await expect(premise(page)).toHaveValue(later);
    await expect(button(page, "Commit premise")).toBeDisabled();
    expect(await page.evaluate(value => ({ raw: localStorage.getItem(value.key), pointer: sessionStorage.getItem(value.pointerKey) }), original))
      .toEqual({ raw: replacement, pointer: original.writer });
    await downloadOriginal(page, info, original);
    await button(page, "Restore Narrative premise copy 1").click();
    await expect(premise(page)).toHaveValue(later);
    await button(page, "Discard Narrative premise copy 1").click();
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(replacement);
    expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
    expect(requests(service, "apply_change")).toHaveLength(1);
    expect(await service.project(initial.id)).toEqual(saved);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    await page.screenshot({ path: info.outputPath("narrative-replaced-source-blocked.png"), fullPage: true });
    expect(await service.reopen(initial.id)).toEqual(saved);
    observations.replacement = replacement; observations.later = later;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, primary); }
});

test("corrupt Narrative source stays available for download and cannot authorize a real save", async ({ page }, info) => {
  const service = await setup(page, info), observations: Record<string, unknown> = {};
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
    const result = await initialUnknown(page, service, initial, observations); final = result.saved;
    const original = { ...result.original, raw: "{ unreadable original author bytes\n" };
    await page.evaluate(value => localStorage.setItem(value.key, value.raw), original);
    await page.reload(); await open(page);
    await expect(warning(page)).toBeVisible();
    const grants = requests(service, "issue_effect_grant").length;
    await premise(page).fill("Current text retained while the earlier source is corrupt");
    await button(page, "Retry premise backup").click();
    await expect(warning(page)).toBeVisible();
    await expect(button(page, "Commit premise")).toBeDisabled();
    await assertOriginal(page, original);
    await downloadOriginal(page, info, original);
    await button(page, "Discard Narrative premise copy 1").click();
    await assertOriginal(page, original);
    expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
    expect(requests(service, "apply_change")).toHaveLength(1);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    expect(await service.reopen(initial.id)).toEqual(final);
    observations.corruptSource = original;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, primary); }
});

for (const baseKind of ["saved", "blank"] as const) {
  test(`a mounted Narrative read failure retains an explicit ${baseKind} base through a real peer change and library reopen`, async ({ page }, info) => {
    const service = await setup(page, info), peers: ScopedChangeService[] = [];
    const observations: Record<string, unknown> = { baseKind };
    let final: Project | undefined, primary: Failure | undefined;
    try {
      const initial = (await service.fixtureBootstrap()).project; observations.initial = initial;
      const { original, saved } = await initialUnknown(page, service, initial, observations);
      final = saved;
      const peer = await service.peer(); peers.push(peer);
      const expectedChanges: Change[] = [{ type: "set_narrative_premise", premise: saved.narrative.premise }];
      // This preparation changes the canonical base through a second real
      // process, before the reload that induces the first recovery-read fault.
      if (baseKind === "blank") {
        const blank: Change = { type: "set_narrative_premise", premise: "" };
        final = await peer.apply(final, blank); expectedChanges.push(blank);
      }
      const observedBase = final;
      const later = observedBase.narrative.premise;
      await failFirstSourceReadOnReload(page, original);
      await expect(premise(page)).toHaveValue(later);
      await premise(page).fill("Intermediate author typing before explicitly returning to the base");
      await premise(page).fill(later);
      await expect(premise(page)).toHaveValue(later);
      await expect(button(page, "Commit premise")).toBeDisabled();
      await assertOriginal(page, original);
      const mountedInput = await premise(page).elementHandle();
      if (!mountedInput) throw new Error("The mounted Narrative input is unavailable before the project read.");
      const grants = requests(service, "issue_effect_grant").length;
      const peerChange: Change = { type: "set_narrative_premise", premise: "Independent saved premise while original recovery is unread" };
      final = await peer.apply(observedBase, peerChange); expectedChanges.push(peerChange);
      expect(final.narrative.premise).not.toBe(later);
      expect(await service.project(initial.id)).toEqual(final);

      // The existing library reopens the current project via App's real read
      // coordinator. No page reload, workspace switch, or replacement Project
      // fixture participates in this update of the mounted Narrative editor.
      await button(page, "Open project library").click();
      const library = page.getByRole("dialog", { name: "Project library", exact: true });
      await expect(library).toBeVisible();
      await library.getByRole("button", { name: "Reopen " + final.title, exact: true }).click();
      await expect(library).not.toBeVisible();
      await expect(page.locator(".revision-chip").first()).toHaveText("r" + final.revision);
      await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
      expect(await mountedInput.evaluate(element => ({
        connected: element.isConnected,
        current: document.querySelector('textarea[aria-label="Narrative premise"]') === element,
        value: (element as HTMLTextAreaElement).value,
      }))).toEqual({ connected: true, current: true, value: later });
      await expect(warning(page)).toBeVisible();
      await expect(premise(page)).toHaveValue(later);
      await expect(button(page, "Commit premise")).toBeDisabled();
      await assertOriginal(page, original);
      expect(requests(service, "apply_change")).toHaveLength(1);
      expect(requests(service, "issue_effect_grant")).toHaveLength(grants);

      await button(page, "Retry premise backup").click();
      await expect(warning(page)).toHaveCount(0);
      await expect(unconfirmed(page)).toContainText(diagnostic);
      await expect(premise(page)).toHaveValue(later);
      await expect(button(page, "Commit premise")).toBeDisabled();
      const retained = await copy(page, initial), retainedDraft = JSON.parse(retained.raw).draft;
      expect(retained.writer).not.toBe(original.writer);
      expect(retainedDraft.pending).toEqual(JSON.parse(original.raw).draft.pending);
      expect(retainedDraft.raw).toBe(later);
      expect(retainedDraft.intent).toBe(true);
      expect(retainedDraft.sequence).toBeGreaterThan(retainedDraft.pending.sequence);
      await assertOriginal(page, original, retained.writer);
      expect(outsidePremise(final)).toEqual(outsidePremise(initial));
      const events = await service.history(initial.id, initial.revision);
      expect(events.map(event => event.change)).toEqual(expectedChanges);
      expect(requests(service, "apply_change")).toHaveLength(1);
      expect(requests(service, "issue_effect_grant")).toHaveLength(grants);
      expect(requests(peer, "apply_change")).toHaveLength(baseKind === "blank" ? 2 : 1);
      expect(await peer.project(initial.id)).toEqual(final);
      await page.screenshot({ path: info.outputPath("narrative-mounted-" + baseKind + "-base-retained.png"), fullPage: true });
      observations.projectAtFailedRead = observedBase;
      observations.projectAfterPeerChange = final;
      observations.later = later;
      observations.sameMountedInput = true;
      observations.recoveredCopy = retained;
      observations.journal = events;
      await mountedInput.dispose();
    } catch (reason) { primary = { reason }; }
    finally { await finish(page, info, service, final, observations, primary, peers); }
  });
}
