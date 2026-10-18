import { writeFile } from "node:fs/promises";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// These are synthetic authoring scenarios against the real StudioService,
// grants and SQLite journal. The invoke bridge is test transport, not evidence
// of a Tauri installation, a model provider, Film or a native renderer.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session and set MOTIONWRIGHT_SERVICE_TEST_BIN; these tests have no simulated service fallback.");

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const input = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const briefFields = ["Project title", "Production objective", "Audience", "Constraints", "Exclusions"] as const;

async function openBrief(page: Page) {
  await button(page, "Brief").click();
  await expect(page.getByRole("heading", { name: "Project brief", exact: true })).toBeVisible();
}

async function revision(page: Page, expected: number) {
  await expect(page.locator(".revision-chip").first()).toHaveText(`r${expected}`);
  await paint(page);
  await expect(page.locator(".error-banner")).toHaveCount(0);
  await expect(page.locator(".document-view .production-error[role='alert']")).toHaveCount(0);
}

async function fieldsEqual(page: Page, values: readonly string[]) {
  expect(values).toHaveLength(briefFields.length);
  for (const [index, field] of briefFields.entries()) await expect(input(page, field)).toHaveValue(values[index]);
}

async function paint(page: Page) {
  // Flush the input render and its recovery effect, without a timing sleep.
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

function outsideBrief(project: Project) {
  return {
    id: project.id, generation: project.generation, activeBranch: project.active_branch,
    scenes: project.scenes, markers: project.markers, assets: project.assets,
    locks: project.locks, deliverables: project.deliverables, claims: project.brief.claims,
    narrative: project.narrative, audio: project.audio, visualLanguage: project.visual_language,
    productionDesign: project.production_design, reviews: project.reviews,
    proposals: project.proposal_sets, modelInvocations: project.model_invocations,
  };
}

interface ApplyArgs extends Record<string, unknown> {
  request: {
    project_id: string; generation: string; revision: number;
    request_id: string; effect_grant: string; change: Change;
  };
}

function applyRequests(service: ScopedChangeService): ApplyArgs[] {
  return service.transcript.flatMap(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === "apply_change"
    ? [entry.payload.args as ApplyArgs] : []);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}

/** Delay only transport scheduling; every command and result stays real. */
function holdNextApply(service: ScopedChangeService, change: Change["type"], phase: "before" | "after") {
  const original = service.invoke.bind(service);
  const entered = deferred<ApplyArgs>();
  const observed = deferred<{ project: Project } | { error: unknown }>();
  const released = deferred<void>();
  const finished = deferred<void>();
  let armed = true;
  let matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== change) return original<T>(command, args);
    armed = false;
    matched = true;
    entered.resolve(structuredClone(args) as ApplyArgs);
    try {
      if (phase === "before") await released.promise;
      try {
        const project = await original<Project>(command, args);
        observed.resolve({ project });
        if (phase === "after") await released.promise;
        return project as T;
      } catch (error) {
        observed.resolve({ error });
        if (phase === "after") await released.promise;
        throw error;
      }
    } finally {
      finished.resolve();
    }
  };
  return {
    entered: entered.promise,
    observed: observed.promise,
    release: () => released.resolve(),
    async dispose() {
      service.invoke = original;
      released.resolve();
      if (matched) await finished.promise;
    },
  };
}

async function evidence(testInfo: TestInfo, services: ScopedChangeService[], initial: Project | undefined, final: Project | undefined, prepare: (() => Promise<unknown>)[] = []) {
  let finalization: StoreFinalization | undefined;
  try {
    finalization = await services[0].finalizeClosedStore(testInfo.outputPath("closed-store-1"), final, services.slice(1), prepare);
  } finally {
    const path = testInfo.outputPath("brief-real-service-evidence.json");
    await writeFile(path, JSON.stringify({
      fixture: "synthetic_test_fixture",
      evidenceScope: "browser_interaction_real_service_sqlite; excludes_tauri_model_dispatch_film_native_render_and_human_acceptance",
      initial, final, canonicalOracle: finalization?.oracle,
      finalizationPath: testInfo.outputPath("closed-store-1-outcomes.json"),
      services: services.map(service => ({
        databasePath: service.databasePath, binaryPath: service.binaryPath,
        stderr: service.stderr, transcript: service.transcript,
        processExits: service.processExits, lifecycleTaints: service.lifecycleTaints,
      })),
    }, null, 2));
    await testInfo.attach("brief-real-service-evidence", { path, contentType: "application/json" });
    await testInfo.attach("complete-store-finalization", { path: testInfo.outputPath("closed-store-1-outcomes.json"), contentType: "application/json" });
  }
}

test("saving the title or brief commits only that group and conserves the other raw inputs", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let initial: Project | undefined;
  let final: Project | undefined;
  try {
    initial = (await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page);
    await page.goto("/");
    await openBrief(page);
    const raw = ["  Synthetic authored title  ", "Keep the original editorial argument.\nMake its scope explicit.",
      "An audience chosen by the author.", "  Preserve the original take  \n\n  Keep rational timing  ",
      "  Unverified product claims  \n\n  Unrequested campaign copy  "];
    for (const [index, field] of briefFields.entries()) await input(page, field).fill(raw[index]);
    await fieldsEqual(page, raw);
    const playhead = await page.locator(".timeline-header .timecode").innerText();
    await expect(button(page, "Play design preview")).toBeVisible();
    await button(page, "Save title").focus();
    await page.keyboard.press("Space");
    await revision(page, initial.revision + 1);
    await expect(button(page, "Play design preview")).toBeVisible();
    await expect(button(page, "Pause design preview")).toHaveCount(0);
    await expect(page.locator(".timeline-header .timecode")).toHaveText(playhead);
    expect(applyRequests(service)).toHaveLength(1);
    const titled = await service.project(initial.id);
    expect(titled.title).toBe("Synthetic authored title");
    expect(titled.brief).toEqual(initial.brief);
    expect(outsideBrief(titled)).toEqual(outsideBrief(initial));
    for (const [index, field] of briefFields.entries()) {
      if (index > 0) await expect(input(page, field)).toHaveValue(raw[index]);
    }
    await page.screenshot({ path: testInfo.outputPath("title-saved-brief-still-pending.png"), fullPage: true });

    const pendingTitle = "  A separate title still being written  ";
    await input(page, "Project title").fill(pendingTitle);
    await button(page, "Commit brief").click();
    await revision(page, initial.revision + 2);
    final = await service.project(initial.id);
    expect(final.title).toBe(titled.title);
    expect(final.brief).toEqual({ ...initial.brief,
      objective: raw[1], audience: raw[2],
      constraints: ["Preserve the original take", "Keep rational timing"],
      exclusions: ["Unverified product claims", "Unrequested campaign copy"],
    });
    expect(outsideBrief(final)).toEqual(outsideBrief(initial));
    await expect(input(page, "Project title")).toHaveValue(pendingTitle);
    await expect(button(page, "Save title")).toBeEnabled();
    const journal = await service.history(initial.id, initial.revision);
    expect(journal.map(event => [event.revision, event.change.type])).toEqual([
      [initial.revision + 1, "rename_project"], [initial.revision + 2, "set_brief"],
    ]);
    expect(journal[0].change).toEqual({ type: "rename_project", title: titled.title });
    expect(journal[1].change).toEqual({ type: "set_brief", objective: raw[1], audience: raw[2],
      constraints: ["Preserve the original take", "Keep rational timing"],
      exclusions: ["Unverified product claims", "Unrequested campaign copy"],
    });
    expect(applyRequests(service)).toHaveLength(2);
    expect(await service.reopen(initial.id)).toEqual(final);
  } finally {
    await evidence(testInfo, [service], initial, final, [() => page.close()]);
  }
});

test("a late real reply preserves newer typing and reload never replays an already committed brief", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  const gates: ReturnType<typeof holdNextApply>[] = [];
  let initial: Project | undefined;
  let final: Project | undefined;
  try {
    initial = (await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page);
    await page.goto("/");
    await openBrief(page);
    await input(page, "Project title").fill("The title actually submitted");
    await input(page, "Production objective").fill("An objective pending during the title save.");
    const titleGate = holdNextApply(service, "rename_project", "after");
    gates.push(titleGate);
    await button(page, "Save title").click();
    const titleRequest = await titleGate.entered;
    const titleReply = await titleGate.observed;
    expect("project" in titleReply).toBe(true);
    expect(titleRequest.request.change).toEqual({ type: "rename_project", title: "The title actually submitted" });
    const titled = await service.project(initial.id);
    expect(titled.revision).toBe(initial.revision + 1);
    expect(titled.brief).toEqual(initial.brief);
    await input(page, "Project title").fill("  New typing after the title request  ");
    await input(page, "Audience").fill("An audience written while the reply is pending.");
    titleGate.release();
    await revision(page, initial.revision + 1);
    await expect(input(page, "Project title")).toHaveValue("  New typing after the title request  ");
    await expect(input(page, "Production objective")).toHaveValue("An objective pending during the title save.");
    await expect(input(page, "Audience")).toHaveValue("An audience written while the reply is pending.");
    await titleGate.dispose();

    const briefGate = holdNextApply(service, "set_brief", "after");
    gates.push(briefGate);
    await button(page, "Commit brief").click();
    const briefRequest = await briefGate.entered;
    const briefReply = await briefGate.observed;
    expect("project" in briefReply).toBe(true);
    expect(briefRequest.request.change).toEqual({ type: "set_brief",
      objective: "An objective pending during the title save.",
      audience: "An audience written while the reply is pending.",
      constraints: initial.brief.constraints, exclusions: initial.brief.exclusions,
    });
    final = await service.project(initial.id);
    expect(final.revision).toBe(initial.revision + 2);
    await input(page, "Production objective").fill("The next objective is not part of that request.");
    await input(page, "Constraints").fill("  A constraint outside the committed payload  \n\n");
    await paint(page);
    await page.screenshot({ path: testInfo.outputPath("typing-after-real-commit-before-reply.png"), fullPage: true });

    // The process has committed, but the old page has not received its reply.
    // Reload reads SQLite and recovers text; it must never resubmit authority.
    await page.reload();
    await openBrief(page);
    await revision(page, initial.revision + 2);
    await expect(input(page, "Project title")).toHaveValue("  New typing after the title request  ");
    await expect(input(page, "Production objective")).toHaveValue("The next objective is not part of that request.");
    await expect(input(page, "Constraints")).toHaveValue("  A constraint outside the committed payload  \n\n");
    briefGate.release();
    await briefGate.dispose();
    await paint(page);
    expect(await service.project(initial.id)).toEqual(final);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(2);
    expect(applyRequests(service)).toHaveLength(2);
    expect(outsideBrief(final)).toEqual(outsideBrief(initial));
    await page.screenshot({ path: testInfo.outputPath("reloaded-pending-input-without-replay.png"), fullPage: true });
  } finally {
    await evidence(testInfo, [service], initial, final, [...gates.map(gate => () => gate.dispose()), () => page.close()]);
  }
});

test("returning an in-flight field to its original value survives navigation and reload until explicitly saved", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let gate: ReturnType<typeof holdNextApply> | undefined;
  let initial: Project | undefined;
  let final: Project | undefined;
  try {
    const seed = (await service.fixtureBootstrap()).project;
    initial = await service.apply(seed, { type: "set_brief", objective: "Old",
      audience: seed.brief.audience, constraints: seed.brief.constraints, exclusions: seed.brief.exclusions,
    });
    const requestsBefore = applyRequests(service).length;
    await service.attachFixturePage(page);
    await page.goto("/");
    await openBrief(page);
    await expect(input(page, "Production objective")).toHaveValue("Old");
    await input(page, "Production objective").fill("Sent");
    gate = holdNextApply(service, "set_brief", "after");
    await button(page, "Commit brief").click();
    const request = await gate.entered;
    expect(request.request.change).toEqual({ type: "set_brief", objective: "Sent",
      audience: initial.brief.audience, constraints: initial.brief.constraints, exclusions: initial.brief.exclusions,
    });
    expect("project" in await gate.observed).toBe(true);
    const sent = await service.project(initial.id);
    expect(sent.brief).toEqual({ ...initial.brief, objective: "Sent" });
    expect(sent.revision).toBe(initial.revision + 1);

    // Remount first: the previous component's in-flight ref no longer exists.
    // The recovered editing session must still preserve a subsequent author
    // decision back to the original base until Sent becomes observable.
    await paint(page);
    await button(page, "Timeline").click();
    await openBrief(page);
    await expect(input(page, "Production objective")).toHaveValue("Sent");
    await input(page, "Production objective").fill("Old");
    await paint(page);
    await button(page, "Timeline").click();
    await openBrief(page);
    await expect(input(page, "Production objective")).toHaveValue("Old");
    await paint(page);
    await page.reload();
    await openBrief(page);
    await revision(page, sent.revision);
    await expect(input(page, "Production objective")).toHaveValue("Old");
    const conflicts = page.getByRole("region", { name: "Brief conflicts", exact: true });
    await expect(conflicts).toContainText("Old");
    await expect(conflicts).toContainText("Sent");
    await expect(button(page, "Commit brief")).toBeDisabled();
    expect(await service.project(initial.id)).toEqual(sent);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    expect(applyRequests(service)).toHaveLength(requestsBefore + 1);
    await page.screenshot({ path: testInfo.outputPath("original-value-kept-against-committed-inflight-value.png"), fullPage: true });

    gate.release();
    await gate.dispose();
    await paint(page);
    await expect(input(page, "Production objective")).toHaveValue("Old");
    await button(page, "Keep my draft Production objective").click();
    expect(await service.project(initial.id)).toEqual(sent);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    expect(applyRequests(service)).toHaveLength(requestsBefore + 1);
    await expect(button(page, "Commit brief")).toBeEnabled();
    await button(page, "Commit brief").click();
    await revision(page, initial.revision + 2);
    final = await service.project(initial.id);
    expect(final.brief).toEqual(initial.brief);
    expect(final.title).toBe(initial.title);
    expect(outsideBrief(final)).toEqual(outsideBrief(initial));
    expect((await service.history(initial.id, initial.revision)).map(event => event.change)).toEqual([
      { type: "set_brief", objective: "Sent", audience: initial.brief.audience,
        constraints: initial.brief.constraints, exclusions: initial.brief.exclusions },
      { type: "set_brief", objective: "Old", audience: initial.brief.audience,
        constraints: initial.brief.constraints, exclusions: initial.brief.exclusions },
    ]);
    expect(applyRequests(service)).toHaveLength(requestsBefore + 2);
    expect(await service.reopen(initial.id)).toEqual(final);
  } finally {
    await evidence(testInfo, [service], initial, final, [...(gate ? [() => gate!.dispose()] : []), () => page.close()]);
  }
});

test("a real brief validation failure preserves every input through reload and correction", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let initial: Project | undefined;
  let final: Project | undefined;
  try {
    initial = (await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page);
    await page.goto("/");
    await openBrief(page);
    const raw = ["  Do not silently save this separate title  ", "Keep this valid objective after the refusal.",
      "a".repeat(2_001), "  Preserve both uncommitted constraints  \n  Preserve line spacing too  ",
      "  Unrelated replacements  \n\n"];
    for (const [index, field] of briefFields.entries()) await input(page, field).fill(raw[index]);
    await button(page, "Commit brief").click();
    await expect(page.locator(".error-banner")).toContainText("brief audience is too long");
    await fieldsEqual(page, raw);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await expect(button(page, "Commit brief")).toBeEnabled();
    await page.screenshot({ path: testInfo.outputPath("real-domain-refusal-keeps-all-fields.png"), fullPage: true });
    await page.reload();
    await openBrief(page);
    await fieldsEqual(page, raw);
    expect(await service.project(initial.id)).toEqual(initial);

    await input(page, "Audience").fill("A corrected audience within the domain budget.");
    await button(page, "Commit brief").click();
    await revision(page, initial.revision + 1);
    final = await service.project(initial.id);
    expect(final.title).toBe(initial.title);
    expect(final.brief).toEqual({ ...initial.brief, objective: raw[1],
      audience: "A corrected audience within the domain budget.",
      constraints: ["Preserve both uncommitted constraints", "Preserve line spacing too"],
      exclusions: ["Unrelated replacements"],
    });
    await expect(input(page, "Project title")).toHaveValue(raw[0]);
    expect(outsideBrief(final)).toEqual(outsideBrief(initial));
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    expect(applyRequests(service)).toHaveLength(2);
    expect(await service.reopen(initial.id)).toEqual(final);
  } finally {
    await evidence(testInfo, [service], initial, final, [() => page.close()]);
  }
});

for (const resolution of ["saved", "draft"] as const) {
  test(`two real clients resolve only the overlapping brief field by keeping the ${resolution} value`, async ({ page, context }, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
    let peer: ScopedChangeService | undefined;
    let second: Page | undefined;
    let gate: ReturnType<typeof holdNextApply> | undefined;
    let initial: Project | undefined;
    let final: Project | undefined;
    try {
      peer = await service.peer();
      second = await context.newPage();
      initial = (await service.fixtureBootstrap()).project;
      await service.attachFixturePage(page);
      await peer.attachFixturePage(second);
      await page.goto("/");
      await second.goto("/");
      await openBrief(page);
      await openBrief(second);
      await input(page, "Production objective").fill("Client A's independently authored objective.");
      await input(page, "Constraints").fill("  Client A's disjoint constraint  \n\n");
      await input(second, "Production objective").fill("Client B's saved objective.");
      await input(second, "Audience").fill("Client B's disjoint audience.");
      gate = holdNextApply(service, "set_brief", "before");
      await button(page, "Commit brief").click();
      const original = await gate.entered;
      expect(original.request.revision).toBe(initial.revision);
      expect(original.request.effect_grant).toBeTruthy();
      await button(second, "Commit brief").click();
      await revision(second, initial.revision + 1);
      const winner = await peer.project(initial.id);
      gate.release();
      await expect(page.locator(".error-banner")).toContainText("stale");
      await expect(input(page, "Production objective")).toHaveValue("Client A's independently authored objective.");
      await expect(input(page, "Constraints")).toHaveValue("  Client A's disjoint constraint  \n\n");
      expect(await service.project(initial.id)).toEqual(winner);
      expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
      await gate.dispose();

      // JSONL's stale-stamp guard ran before Store.apply; this is not labelled
      // as a race between two SQLite transactions. Both processes are real.
      await page.reload();
      await openBrief(page);
      const conflicts = page.getByRole("region", { name: "Brief conflicts", exact: true });
      await expect(conflicts).toContainText("Client A's independently authored objective.");
      await expect(conflicts).toContainText("Client B's saved objective.");
      await expect(input(page, "Audience")).toHaveValue("Client B's disjoint audience.");
      await expect(input(page, "Constraints")).toHaveValue("  Client A's disjoint constraint  \n\n");
      await expect(button(page, "Keep saved Audience")).toHaveCount(0);
      await expect(button(page, "Keep saved Constraints")).toHaveCount(0);
      await expect(button(page, "Commit brief")).toBeDisabled();
      await conflicts.screenshot({ path: testInfo.outputPath(`real-brief-conflict-${resolution}.png`) });
      await button(page, resolution === "saved" ? "Keep saved Production objective" : "Keep my draft Production objective").click();
      expect(await service.project(initial.id)).toEqual(winner);
      expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
      await expect(button(page, "Commit brief")).toBeEnabled();
      await button(page, "Commit brief").click();
      await revision(page, initial.revision + 2);
      final = await service.project(initial.id);
      expect(final.brief).toEqual({ ...winner.brief,
        objective: resolution === "saved" ? "Client B's saved objective." : "Client A's independently authored objective.",
        audience: "Client B's disjoint audience.", constraints: ["Client A's disjoint constraint"],
      });
      expect(outsideBrief(final)).toEqual(outsideBrief(initial));
      const history = await service.history(initial.id, initial.revision);
      expect(history.map(event => [event.revision, event.change.type])).toEqual([
        [initial.revision + 1, "set_brief"], [initial.revision + 2, "set_brief"],
      ]);
      expect(await peer.reopen(initial.id)).toEqual(final);
      const processA = service.transcript.find(entry => entry.direction === "request")?.processId;
      const processB = peer.transcript.find(entry => entry.direction === "request")?.processId;
      expect(processA).toBeDefined();
      expect(processB).toBeDefined();
      expect(processA).not.toBe(processB);
    } finally {
      await evidence(testInfo, peer ? [service, peer] : [service], initial, final,
        [...(gate ? [() => gate!.dispose()] : []), () => page.close(), ...(second ? [() => second!.close()] : [])]);
    }
  });
}

test("reopening revokes a real grant without changing the revision or clearing the refused brief", async ({ page }, testInfo) => {
  const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
  let gate: ReturnType<typeof holdNextApply> | undefined;
  let initial: Project | undefined;
  let final: Project | undefined;
  try {
    initial = (await service.fixtureBootstrap()).project;
    await service.attachFixturePage(page);
    await page.goto("/");
    await openBrief(page);
    const raw = ["  This title is still separate  ", "Keep the brief after a permission refusal.",
      "The author can retry without retyping.", "  Do not replay the old grant  ", "  Silent auto-save  "];
    for (const [index, field] of briefFields.entries()) await input(page, field).fill(raw[index]);
    gate = holdNextApply(service, "set_brief", "before");
    await button(page, "Commit brief").click();
    const original = await gate.entered;
    expect(await service.reopen(initial.id)).toEqual(initial);
    gate.release();
    await expect(page.locator(".error-banner")).toContainText(/grant/i);
    await fieldsEqual(page, raw);
    expect(await service.project(initial.id)).toEqual(initial);
    expect(await service.history(initial.id, initial.revision)).toEqual([]);
    await gate.dispose();
    await expect(button(page, "Commit brief")).toBeEnabled();
    await button(page, "Commit brief").click();
    await revision(page, initial.revision + 1);
    final = await service.project(initial.id);
    expect(final.title).toBe(initial.title);
    expect(final.brief).toEqual({ ...initial.brief, objective: raw[1], audience: raw[2],
      constraints: ["Do not replay the old grant"], exclusions: ["Silent auto-save"],
    });
    await expect(input(page, "Project title")).toHaveValue(raw[0]);
    const requests = applyRequests(service);
    expect(requests).toHaveLength(2);
    expect(requests[1].request.change).toEqual(original.request.change);
    expect(requests[1].request.request_id).not.toBe(original.request.request_id);
    expect(requests[1].request.effect_grant).not.toBe(original.request.effect_grant);
    expect(await service.history(initial.id, initial.revision)).toHaveLength(1);
    expect(outsideBrief(final)).toEqual(outsideBrief(initial));
    expect(await service.reopen(initial.id)).toEqual(final);
  } finally {
    await evidence(testInfo, [service], initial, final, [...(gate ? [() => gate!.dispose()] : []), () => page.close()]);
  }
});
