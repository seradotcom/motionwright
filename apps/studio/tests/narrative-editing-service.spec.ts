import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { narrativeEditChange, narrativeEditingState } from "../src/narrativeEditing";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Actual App + original commit coordinator + real StudioService/grants/SQLite.
// Faults affect response delivery or browser custody only. No Project or domain
// feasibility response is synthesized and there is no browser backend fallback.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Source-built StudioService is required; no simulated persistence fallback.");
test.setTimeout(120_000);
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const script = (page: Page) => page.getByLabel("Editorial script", { exact: true });
const unknown = (page: Page) => page.getByRole("region", { name: "Unconfirmed editorial operation", exact: true });
const names = ["Editorial question Svc", "Editorial evidence Svc", "Editorial decision Svc", "Editorial aftercare Svc"];
interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
function calls(service: ScopedChangeService, command: string) {
  return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === command);
}
function applies(service: ScopedChangeService): ApplyArgs[] {
  return calls(service, "apply_change").map(entry => ("args" in entry.payload ? entry.payload.args : undefined) as ApplyArgs);
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(unknown(page)).toHaveCount(0);
}
async function openNarrative(page: Page) {
  await button(page, "Narrative").click();
  await expect(page.getByRole("heading", { name: "Editorial beats, script and timing", exact: true })).toBeVisible();
}
async function attach(page: Page, service: ScopedChangeService, initial: Project) {
  await service.attachFixturePage(page, initial.id); await page.goto("/"); await openNarrative(page);
}
async function setup(info: TestInfo) {
  return ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
}
async function seed(service: ScopedChangeService) {
  let project = (await service.fixtureBootstrap()).project;
  for (const lock of project.locks) project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  for (const label of names) project = await service.apply(project, { type: "add_narrative_beat", label,
    objective: "Authored understanding for " + label, audience_takeaway: "Takeaway for " + label, claim_ids: [], preferred_duration: null });
  return project;
}
function unaffected(project: Project) {
  return { id: project.id, generation: project.generation, activeBranch: project.active_branch, title: project.title,
    brief: project.brief, scenes: project.scenes, assets: project.assets, audio: project.audio, locks: project.locks,
    deliverables: project.deliverables, productionDesign: project.production_design, visualLanguage: project.visual_language };
}
function deferred<T>() { let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function holdScriptReceipt(service: ScopedChangeService) {
  const original = service.invoke.bind(service), release = deferred<void>(), observed = deferred<Project>(), done = deferred<void>();
  void observed.promise.catch(() => undefined); let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "edit_narrative" || request.change.edit.kind !== "set_script") return original<T>(command, args);
    armed = false; matched = true;
    try { const saved = await original<Project>(command, args); observed.resolve(saved); await release.promise; return saved as T; }
    catch (error) { observed.reject(error); throw error; } finally { done.resolve(); }
  };
  return { observed: observed.promise, release: () => release.resolve(), async dispose() {
    service.invoke = original; release.resolve(); if (matched) await done.promise;
  } };
}
type Failure = { reason: unknown };
const detail = (reason: unknown) => reason instanceof Error ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };
async function finish(page: Page, info: TestInfo, service: ScopedChangeService, final: Project | undefined,
  observations: Record<string, unknown>, preparations: (() => Promise<unknown>)[], primary?: Failure) {
  const failures: unknown[] = primary ? [primary.reason] : []; let custody: StoreFinalization | undefined;
  try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [...preparations, () => page.close()]); }
  catch (error) { failures.push(error); }
  try {
    const path = info.outputPath("narrative-editing-service-evidence.json");
    await writeFile(path, JSON.stringify({ scope: "Synthetic authored scenario through actual App + StudioService + grants + SQLite",
      exclusions: ["installed Tauri", "native renderer", "speech alignment", "human artistic acceptance"],
      observations, final, canonicalOracle: custody?.oracle, primaryFailure: primary ? detail(primary.reason) : null,
      failures: failures.map(detail), services: [{ databasePath: service.databasePath, binaryPath: service.binaryPath,
        transcript: service.transcript, processExits: service.processExits, lifecycleTaints: service.lifecycleTaints, stderr: service.stderr }] }, null, 2));
    await info.attach("narrative-editing-service-evidence", { path, contentType: "application/json" });
    await info.attach("closed-store-outcomes", { path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json" });
  } catch (error) { failures.push(error); }
  if (failures.length === 1) throw failures[0];
  if (failures.length > 1) throw new AggregateError(failures, "Editorial scenario and custody failures are retained separately", { cause: primary?.reason });
}
async function ownCopy(page: Page, project: Project) {
  return page.evaluate(scope => {
    const writer = sessionStorage.getItem("motionwright.editorial-writer.v1:" + encodeURIComponent(scope));
    if (!writer) throw new Error("No verified editorial writer pointer.");
    const key = "motionwright.editorial-copy.v1:" + encodeURIComponent(scope) + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No retained editorial author copy."); return { key, raw, copy: JSON.parse(raw) };
  }, JSON.stringify([project.id, project.generation, project.active_branch]));
}

test("actual editorial reorder preserves UUIDs, unaffected relationships and scenes through closed SQLite reopen", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  try {
    let initial = await seed(service); const ids = names.map(name => initial.narrative.beats.find(beat => beat.label === name)!.id);
    const changed = { id: randomUUID(), from_beat_id: ids[0], to_beat_id: ids[1], kind: "adjacent" as const, reason: "Question directly prepares evidence" };
    const unchanged = { id: randomUUID(), from_beat_id: ids[2], to_beat_id: ids[3], kind: "precedes" as const, reason: "Independent decision before aftercare" };
    for (const relation of [changed, unchanged]) initial = await service.apply(initial, narrativeEditChange(initial, { kind: "upsert_relation", relation }));
    for (let i = 0; i < 2; i++) initial = await service.apply(initial, narrativeEditChange(initial, { kind: "upsert_binding",
      binding: { id: randomUUID(), beat_id: ids[i], target: { kind: "scene", scene_id: initial.scenes[i].id } } }));
    const before = applies(service).length; observations.initial = initial; await attach(page, service, initial);
    await button(page, "Move editorial beat " + names[0] + " later").click();
    await expect(page.getByRole("region", { name: "Editorial narrative editor", exact: true })).toContainText("1 declared relationships change");
    expect(applies(service)).toHaveLength(before);
    await button(page, "Save editorial beat order").click(); await revision(page, initial.revision + 1);
    final = await service.project(initial.id); const impact = narrativeEditingState(final).last_reorder!;
    const expectedOrder = initial.narrative.beats.map(beat => beat.id), a = expectedOrder.indexOf(ids[0]);
    [expectedOrder[a], expectedOrder[a + 1]] = [expectedOrder[a + 1], expectedOrder[a]];
    expect(final.narrative.beats.map(beat => beat.id)).toEqual(expectedOrder);
    for (const beat of initial.narrative.beats) expect(final.narrative.beats.find(value => value.id === beat.id)).toEqual(beat);
    expect(impact.affected_relations.map(relation => relation.relation_id)).toEqual([changed.id]);
    expect(impact.conflicting_relation_ids).toEqual([changed.id]); expect(impact.affected_binding_ids).toHaveLength(2);
    expect(narrativeEditingState(final).relations).toEqual(narrativeEditingState(initial).relations);
    expect(unaffected(final)).toEqual(unaffected(initial));
    const journal = await service.history(initial.id, initial.revision); expect(journal).toHaveLength(1);
    expect(journal[0].change).toEqual(applies(service).at(-1)!.request.change);
    await page.getByText("Saved reorder impact and exact affected IDs", { exact: true }).click();
    await page.screenshot({ path: info.outputPath("editorial-reorder-impact.png"), fullPage: true });
    await service.close(); await service.start(); await page.reload(); await openNarrative(page);
    expect(await service.project(initial.id)).toEqual(final); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    expect(applies(service)).toHaveLength(before + 1); observations.impact = impact;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("approved editorial text stays separate from audio while an actual timing conflict is retained and reviewed", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  try {
    let initial = await seed(service); const scene = initial.scenes[0];
    initial = await service.apply(initial, { type: "set_lock", resource: "scene:" + scene.id, kind: "timing", note: "Fixed scene duration in editorial scenario" });
    const before = applies(service).length; observations.initial = initial; await attach(page, service, initial);
    const raw = "  Approved editorial wording\nA deliberate phrase and pause stay intact.  \n";
    await script(page).fill(raw); await button(page, "Save editorial script").click(); await revision(page, initial.revision + 1);
    await page.getByLabel("Editorial reviewer label", { exact: true }).fill("Synthetic content owner");
    await page.getByLabel("Editorial approval or release reason", { exact: true }).fill("Keep these exact words across timing decisions");
    await button(page, "Approve exact editorial text").click(); await revision(page, initial.revision + 2);
    const approved = await service.project(initial.id), approvedScript = narrativeEditingState(approved).script;
    expect(approvedScript?.text).toBe(raw); expect(approvedScript?.approval).not.toBeNull();
    await page.getByRole("combobox", { name: "Proposal scene", exact: true }).selectOption(scene.id);
    await page.getByLabel("Proposed scene duration (exact seconds)", { exact: true }).fill("1e");
    await page.getByLabel("Duration proposal reason", { exact: true }).fill("Inspect the hard conflict without shortening the words");
    await button(page, "Retain duration proposal without applying").click(); expect(applies(service)).toHaveLength(before + 2);
    await page.getByLabel("Proposed scene duration (exact seconds)", { exact: true }).fill("1/1000");
    await button(page, "Retain duration proposal without applying").click(); await revision(page, initial.revision + 3);
    const proposed = await service.project(initial.id), proposal = narrativeEditingState(proposed).timing_proposals.at(-1)!;
    expect(proposal.feasibility.command_allowed).toBe(false); expect(proposal.script_snapshot).toEqual(approvedScript);
    expect(proposal.decision).toBeNull(); expect(narrativeEditingState(proposed).script).toEqual(approvedScript);
    expect(proposed.scenes).toEqual(initial.scenes); expect(proposed.audio).toEqual(initial.audio);
    await expect(button(page, "Apply reviewed duration for " + proposal.id)).toBeDisabled();
    await service.close(); await service.start(); await page.reload(); await openNarrative(page);
    await expect(script(page)).toHaveValue(raw); await expect(button(page, "Save editorial script")).toBeDisabled();
    await page.getByLabel("Timing decision reason " + proposal.id, { exact: true }).fill("Retain current duration; proposal remains evidence of the conflict");
    await button(page, "Keep current timing for " + proposal.id).click(); await revision(page, initial.revision + 4);
    final = await service.project(initial.id);
    expect(narrativeEditingState(final).script).toEqual(approvedScript);
    expect(narrativeEditingState(final).timing_proposals.at(-1)?.decision?.decision).toBe("keep_current");
    expect(final.scenes).toEqual(initial.scenes); expect(final.audio).toEqual(initial.audio);
    expect(final.production_design?.narration_take_lock).toEqual(initial.production_design?.narration_take_lock);
    expect(applies(service)).toHaveLength(before + 4); expect(await service.reopen(initial.id)).toEqual(final);
    observations.proposal = proposal; await page.screenshot({ path: info.outputPath("editorial-approved-conflict.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("authoring a beat, exact preferred duration and hold survives navigation, binding and SQLite reopen", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await seed(service), before = applies(service).length; observations.initial = initial; await attach(page, service, initial);
    await page.getByLabel("New editorial beat label", { exact: true }).fill("An explicitly authored fifth beat");
    await page.getByLabel("New editorial objective", { exact: true }).fill("Keep its original UUID through later edits");
    await page.getByLabel("New audience takeaway", { exact: true }).fill("Exact authored understanding");
    await page.getByLabel("New preferred duration (seconds, optional)", { exact: true }).fill("1001/30000");
    await button(page, "Timeline").click(); await openNarrative(page); await page.reload(); await openNarrative(page);
    await expect(page.getByLabel("New preferred duration (seconds, optional)", { exact: true })).toHaveValue("1001/30000");
    expect(applies(service)).toHaveLength(before);
    await button(page, "Add editorial beat").click(); await revision(page, initial.revision + 1);
    let saved = await service.project(initial.id), added = saved.narrative.beats.find(beat => beat.label === "An explicitly authored fifth beat")!;
    expect(added.preferred_duration).toEqual({ num: "1001", den: "30000" });
    await button(page, added.label).click();
    await page.getByLabel("Minimum editorial hold (seconds, optional)", { exact: true }).fill("1/3");
    await button(page, "Save minimum editorial hold").click(); await revision(page, initial.revision + 2);
    await expect(page.getByRole("region", { name: "Editorial narrative editor", exact: true }).getByRole("table")).toContainText("UNKNOWN — unbound");
    await page.getByRole("combobox", { name: "Realization editorial beat", exact: true }).selectOption(added.id);
    await page.getByRole("combobox", { name: "Realization scene", exact: true }).selectOption(initial.scenes[0].id);
    await page.getByRole("combobox", { name: "Realization interval", exact: true }).selectOption("scene");
    await button(page, "Bind editorial realization").click(); await revision(page, initial.revision + 3);
    final = await service.project(initial.id); saved = final; added = saved.narrative.beats.find(beat => beat.id === added.id)!;
    expect(added.preferred_duration).toEqual({ num: "1001", den: "30000" });
    expect(narrativeEditingState(final).minimum_holds.find(hold => hold.beat_id === added.id)?.duration).toEqual({ num: "1", den: "3" });
    expect(narrativeEditingState(final).bindings.find(binding => binding.beat_id === added.id)?.target).toEqual({ kind: "scene", scene_id: initial.scenes[0].id });
    expect(unaffected(final)).toEqual(unaffected(initial)); expect(applies(service)).toHaveLength(before + 3);
    await service.close(); await service.start(); await page.reload(); await openNarrative(page); await button(page, added.label).click();
    await expect(page.getByLabel("Preferred editorial duration (seconds, optional)", { exact: true })).toHaveValue("1001/30000");
    await expect(page.getByLabel("Minimum editorial hold (seconds, optional)", { exact: true })).toHaveValue("1/3");
    expect(await service.project(initial.id)).toEqual(final); observations.addedBeat = added;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("a held original StudioService script receipt preserves later return-to-base typing across workspace remount", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  let hold: ReturnType<typeof holdScriptReceipt> | undefined;
  try {
    const initial = await seed(service), before = applies(service).length; await attach(page, service, initial);
    const oldBase = await script(page).inputValue(); await script(page).fill("The submitted original editorial wording");
    hold = holdScriptReceipt(service); await button(page, "Save editorial script").click();
    const receipt = await hold.observed; expect(narrativeEditingState(receipt).script?.text).toBe("The submitted original editorial wording");
    await button(page, "Timeline").click(); await openNarrative(page);
    await script(page).fill("A later draft"); await script(page).fill(oldBase);
    hold.release(); await hold.dispose(); await revision(page, initial.revision + 1);
    await expect(script(page)).toHaveValue(oldBase); await expect(button(page, "Save editorial script")).toBeEnabled();
    expect(applies(service)).toHaveLength(before + 1);
    await button(page, "Save editorial script").click(); await revision(page, initial.revision + 2);
    final = await service.project(initial.id); expect(narrativeEditingState(final).script?.text).toBe(oldBase);
    expect(narrativeEditingState(final).script?.version).toBe(2); expect(unaffected(final)).toEqual(unaffected(initial));
    expect(applies(service)).toHaveLength(before + 2); expect(await service.reopen(initial.id)).toEqual(final);
    observations.originalReceipt = receipt; observations.laterRaw = oldBase;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, hold ? [() => hold!.dispose()] : [], primary); }
});

test("an explicitly reviewed fresh duration is applied by the actual UI and reopened without editing approved words", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await seed(service), before = applies(service).length, scene = initial.scenes[0];
    await attach(page, service, initial); const raw = "Keep this exact approved wording while increasing authored dwell.";
    await script(page).fill(raw); await button(page, "Save editorial script").click(); await revision(page, initial.revision + 1);
    await page.getByLabel("Editorial reviewer label", { exact: true }).fill("Synthetic scenario owner");
    await page.getByLabel("Editorial approval or release reason", { exact: true }).fill("An exact content decision separate from timing");
    await button(page, "Approve exact editorial text").click(); await revision(page, initial.revision + 2);
    const approved = narrativeEditingState(await service.project(initial.id)).script;
    const duration = { num: String(BigInt(scene.duration.num) + BigInt(scene.duration.den)), den: scene.duration.den };
    await page.getByRole("combobox", { name: "Proposal scene", exact: true }).selectOption(scene.id);
    await page.getByLabel("Proposed scene duration (exact seconds)", { exact: true }).fill(duration.num + "/" + duration.den);
    await page.getByLabel("Duration proposal reason", { exact: true }).fill("One additional exact second, subject to the actual domain guard");
    await button(page, "Retain duration proposal without applying").click(); await revision(page, initial.revision + 3);
    const staged = await service.project(initial.id), proposal = narrativeEditingState(staged).timing_proposals.at(-1)!;
    expect(staged.scenes).toEqual(initial.scenes); expect(proposal.feasibility.command_allowed).toBe(true);
    await page.getByLabel("Timing decision reason " + proposal.id, { exact: true }).fill("Apply the fresh inspected duration; retain editorial content");
    await button(page, "Apply reviewed duration for " + proposal.id).click(); await revision(page, initial.revision + 4);
    final = await service.project(initial.id); expect(final.scenes[0].duration).toEqual(duration);
    expect(narrativeEditingState(final).script).toEqual(approved); expect(final.audio).toEqual(initial.audio);
    expect(narrativeEditingState(final).timing_proposals.at(-1)?.decision?.decision).toBe("apply_duration");
    expect(applies(service)).toHaveLength(before + 4);
    await service.close(); await service.start(); await page.reload(); await openNarrative(page);
    await expect(script(page)).toHaveValue(raw); expect(await service.project(initial.id)).toEqual(final);
    observations.proposal = proposal; observations.approvedScript = approved;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("a real committed result with a lost reply stays UNKNOWN across reload until reviewed as a separate intent", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}, original = service.invoke.bind(service);
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await seed(service), before = applies(service).length; await attach(page, service, initial); let armed = true;
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "edit_narrative" || request.change.edit.kind !== "set_script") return original<T>(command, args);
      armed = false; observations.originalServiceOutcome = await original<Project>(command, args);
      throw new Error("Actual service committed; original response delivery was intentionally lost");
    };
    await script(page).fill("First exact authored script"); await button(page, "Save editorial script").click();
    await expect(page.getByRole("alert").filter({ hasText: "original response delivery was intentionally lost" }).first()).toBeVisible();
    await expect(unknown(page)).toBeVisible();
    await script(page).fill("A separately authored later script"); const earlier = await ownCopy(page, initial);
    expect(earlier.copy.draft.pending.outcome).toBe("UNKNOWN"); const committed = await service.project(initial.id);
    await page.reload(); await openNarrative(page); await expect(script(page)).toHaveValue("A separately authored later script");
    await expect(unknown(page)).toBeVisible(); await expect(button(page, "Save editorial script")).toBeDisabled();
    expect(applies(service)).toHaveLength(before + 1);
    const reads = calls(service, "project_history").length; await button(page, "Inspect current editorial project and history").click();
    await expect(unknown(page)).toContainText("Matching command observations: " + committed.revision);
    expect(calls(service, "project_history").length).toBeGreaterThan(reads);
    await button(page, "Keep typed input for a separate explicit intent").click();
    expect((await ownCopy(page, initial)).copy.draft.pending.outcome).toBe("UNKNOWN");
    expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
    expect(await service.project(initial.id)).toEqual(committed); expect(applies(service)).toHaveLength(before + 1);
    await button(page, "Save editorial script").click(); await revision(page, committed.revision + 1);
    final = await service.project(initial.id); expect(narrativeEditingState(final).script?.text).toBe("A separately authored later script");
    const sent = applies(service).slice(before); expect(sent).toHaveLength(2);
    expect(sent[1].request.request_id).not.toBe(sent[0].request.request_id); expect(sent[1].request.effect_grant).not.toBe(sent[0].request.effect_grant);
    expect(sent[1].request.revision).toBe(committed.revision);
    expect((await service.history(initial.id, initial.revision)).map(event => event.change.type)).toEqual(["edit_narrative", "edit_narrative"]);
    expect(await service.reopen(initial.id)).toEqual(final); observations.retainedUnknown = earlier;
  } catch (reason) { primary = { reason }; }
  finally { service.invoke = original; await finish(page, info, service, final, observations, [], primary); }
});

test("a silent editorial copy failure blocks actual grants and SQLite mutations until exact recovery succeeds", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}; let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await seed(service), before = applies(service).length; await attach(page, service, initial);
    const grants = calls(service, "issue_effect_grant").length;
    await page.evaluate(() => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function (key, value) { if (this === localStorage && key.startsWith("motionwright.editorial-copy.v1:")) return; return original.call(this, key, value); };
      Object.assign(window, { __EDITORIAL_SERVICE_RESTORE__: () => { Storage.prototype.setItem = original; } });
    });
    await script(page).fill("Author input before any effect grant"); await expect(button(page, "Save editorial script")).toBeDisabled();
    expect(applies(service)).toHaveLength(before); expect(calls(service, "issue_effect_grant")).toHaveLength(grants);
    expect(await service.project(initial.id)).toEqual(initial); expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await page.evaluate(() => (window as unknown as { __EDITORIAL_SERVICE_RESTORE__(): void }).__EDITORIAL_SERVICE_RESTORE__());
    await button(page, "Retry editorial recovery").click(); await expect(button(page, "Save editorial script")).toBeEnabled();
    expect(applies(service)).toHaveLength(before); await button(page, "Save editorial script").click(); await revision(page, initial.revision + 1);
    final = await service.project(initial.id); expect(narrativeEditingState(final).script?.text).toBe("Author input before any effect grant");
    expect(applies(service)).toHaveLength(before + 1); expect(await service.reopen(initial.id)).toEqual(final); observations.grantsBefore = grants;
  } catch (reason) { primary = { reason }; }
  finally { await finish(page, info, service, final, observations, [], primary); }
});

test("a real UNKNOWN survives first pointer read failure and explicit Retry with later author fields and no replay", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}, original = service.invoke.bind(service);
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await seed(service), before = applies(service).length; observations.initial = initial;
    await attach(page, service, initial); let armed = true;
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "edit_narrative" || request.change.edit.kind !== "set_script") return original<T>(command, args);
      armed = false; observations.originalServiceOutcome = await original<Project>(command, args);
      throw new Error("Actual editorial commit completed; original response lost before recovery read fault");
    };
    const submitted = "Exact script committed by the real service before first-read failure";
    await script(page).fill(submitted); await button(page, "Save editorial script").click();
    await expect(page.getByRole("alert").filter({ hasText: "original response lost before recovery read fault" }).first()).toBeVisible();
    await expect(unknown(page)).toBeVisible();
    await script(page).fill("Later author script retained before reload");
    await page.getByLabel("Duration proposal reason", { exact: true }).fill("Independent explanation retained before reload");
    const earlier = await ownCopy(page, initial), pending = structuredClone(earlier.copy.draft.pending);
    expect(pending.outcome).toBe("UNKNOWN");
    const committed = await service.project(initial.id); final = committed;
    const journal = await service.history(initial.id, initial.revision), grants = calls(service, "issue_effect_grant").length;
    expect(committed.revision).toBe(initial.revision + 1); expect(narrativeEditingState(committed).script?.text).toBe(submitted);
    expect(journal).toHaveLength(1); expect(journal[0].change).toEqual(applies(service).at(-1)!.request.change);
    const scope = JSON.stringify([initial.id, initial.generation, initial.active_branch]);
    const pointerKey = "motionwright.editorial-writer.v1:" + encodeURIComponent(scope);
    await page.addInitScript(key => {
      const get = Storage.prototype.getItem; let failed = false;
      Storage.prototype.getItem = function (name) {
        if (this === sessionStorage && name === key && !failed) { failed = true; throw new Error("Initial editorial pointer read unavailable"); }
        return get.call(this, name);
      };
    }, pointerKey);
    await page.reload(); await openNarrative(page);
    await script(page).fill("New wording while the earlier source is unreadable"); await script(page).fill("");
    await page.getByLabel("New editorial beat label", { exact: true }).fill("Later independent beat input");
    await expect(button(page, "Save editorial script")).toBeDisabled();
    await button(page, "Retry editorial recovery").click();
    await expect(unknown(page)).toBeVisible(); await expect(script(page)).toHaveValue("");
    await expect(page.getByLabel("Duration proposal reason", { exact: true })).toHaveValue("Independent explanation retained before reload");
    await expect(page.getByLabel("New editorial beat label", { exact: true })).toHaveValue("Later independent beat input");
    await expect(button(page, "Save editorial script")).toBeDisabled();
    const recovered = await ownCopy(page, initial);
    expect(recovered.copy.draft.pending).toEqual(pending); expect(recovered.copy.draft.fields["script.text"].raw).toBe("");
    expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(earlier.raw);
    await button(page, "Timeline").click(); await openNarrative(page);
    await expect(script(page)).toHaveValue(""); await expect(unknown(page)).toBeVisible();
    expect(applies(service)).toHaveLength(before + 1); expect(calls(service, "issue_effect_grant")).toHaveLength(grants);
    expect(await service.project(initial.id)).toEqual(committed); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    await service.close(); await service.start();
    expect(await service.project(initial.id)).toEqual(committed); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    observations.originalCopy = earlier; observations.recoveredCopy = recovered; observations.retainedPending = pending;
    observations.fullProjectUnchangedAfterRecovery = committed; observations.journalUnchangedAfterRecovery = journal;
    observations.applyCount = before + 1; observations.grantCount = grants;
    await page.screenshot({ path: info.outputPath("editorial-initial-read-recovered-unknown.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { service.invoke = original; await finish(page, info, service, final, observations, [], primary); }
});

test("same-writer replacement after observed corruption cannot clear real UNKNOWN recovery or mutate SQLite", async ({ page }, info) => {
  const service = await setup(info), observations: Record<string, unknown> = {}, original = service.invoke.bind(service);
  let final: Project | undefined, primary: Failure | undefined;
  try {
    const initial = await seed(service), before = applies(service).length; observations.initial = initial;
    await attach(page, service, initial); let armed = true;
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyArgs["request"] | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "edit_narrative" || request.change.edit.kind !== "set_script") return original<T>(command, args);
      armed = false; observations.originalServiceOutcome = await original<Project>(command, args);
      throw new Error("Actual editorial commit completed; original response lost before observed corruption");
    };
    const submitted = "Exact script committed before the original browser copy became unreadable";
    await script(page).fill(submitted); await button(page, "Save editorial script").click();
    await expect(page.getByRole("alert").filter({ hasText: "original response lost before observed corruption" }).first()).toBeVisible();
    await expect(unknown(page)).toBeVisible(); await script(page).fill("Author wording after the lost original reply");
    const earlier = await ownCopy(page, initial); expect(earlier.copy.draft.pending.outcome).toBe("UNKNOWN");
    const committed = await service.project(initial.id); final = committed;
    const journal = await service.history(initial.id, initial.revision), grants = calls(service, "issue_effect_grant").length;
    expect(committed.revision).toBe(initial.revision + 1); expect(narrativeEditingState(committed).script?.text).toBe(submitted);
    expect(journal).toHaveLength(1); expect(journal[0].change).toEqual(applies(service).at(-1)!.request.change);
    const corrupt = "{unreadable original editorial source after a real UNKNOWN";
    await page.evaluate(({ key, corrupt }) => localStorage.setItem(key, corrupt), { key: earlier.key, corrupt });
    await page.reload(); await openNarrative(page);
    const later = "Later author wording remains available despite corrupt source replacement";
    await script(page).fill(later); await expect(button(page, "Save editorial script")).toBeDisabled();
    const replacement = structuredClone(earlier.copy); replacement.draft.pending = null; replacement.draft.fields = {}; replacement.draft.sequence = 0;
    const replacementRaw = JSON.stringify(replacement);
    await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: earlier.key, raw: replacementRaw });
    await button(page, "Retry editorial recovery").click();
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    // Causal control against 6033: its Retry adopts the same-writer replacement
    // and enables this button, despite the earlier real UNKNOWN being unread.
    await expect(button(page, "Save editorial script")).toBeDisabled();
    await expect(page.getByRole("alert").filter({ hasText: "original editorial recovery bytes changed" }).first()).toBeVisible();
    await expect(script(page)).toHaveValue(later);
    expect((await ownCopy(page, initial)).key).toBe(earlier.key);
    expect(await page.evaluate(key => localStorage.getItem(key), earlier.key)).toBe(replacementRaw);
    await page.getByText("Editorial recovery and portable author input", { exact: true }).click();
    const downloaded = page.waitForEvent("download"); await button(page, "Download editorial copy " + earlier.key).click();
    const downloadPath = await (await downloaded).path(); expect(downloadPath).not.toBeNull();
    const downloadRaw = await readFile(downloadPath!, "utf8"), recoveredBytes = JSON.parse(downloadRaw);
    expect(recoveredBytes.schema).toBe("motionwright.local-input-copy/1"); expect(recoveredBytes.storage_key).toBe(earlier.key);
    expect(recoveredBytes.raw).toBe(corrupt);
    const preservedPath = info.outputPath("original-corrupt-editorial-copy.json"); await writeFile(preservedPath, downloadRaw);
    await info.attach("original-corrupt-editorial-copy", { path: preservedPath, contentType: "application/json" });
    expect(applies(service)).toHaveLength(before + 1); expect(calls(service, "issue_effect_grant")).toHaveLength(grants);
    expect(await service.project(initial.id)).toEqual(committed); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    await service.close(); await service.start();
    expect(await service.project(initial.id)).toEqual(committed); expect(await service.history(initial.id, initial.revision)).toEqual(journal);
    observations.originalUnknownCopy = earlier; observations.originalCorruptRaw = corrupt; observations.replacementRaw = replacementRaw;
    observations.download = recoveredBytes; observations.laterRaw = later;
    observations.fullProjectUnchangedAfterRejectedRecovery = committed; observations.journalUnchangedAfterRejectedRecovery = journal;
    observations.applyCount = before + 1; observations.grantCount = grants;
    await page.screenshot({ path: info.outputPath("editorial-corrupt-source-replacement-blocked.png"), fullPage: true });
  } catch (reason) { primary = { reason }; }
  finally { service.invoke = original; await finish(page, info, service, final, observations, [], primary); }
});
