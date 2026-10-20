/**
 * Synthetic authoring inputs over the existing real StudioService JSONL,
 * grants, CAS and SQLite. Transport timing/storage corruption are explicit
 * test arrangements. No installed Tauri, native render or human acceptance.
 * finalizeClosedStore retains the complete closed tree; the existing runner
 * must archive it before returning and admit its later independent readback.
 */
import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test as base, type Page } from "@playwright/test";
import type { Change, DeliverableProfile, Project } from "../src/types";
import { PROFILE_DRAFT_STORAGE_PREFIX } from "../src/deliveryProfileDraft";
import { ScopedChangeService } from "./support/scopedChangeService";

interface ApplyRequest {
  project_id: string; generation: string; revision: number; request_id: string;
  effect_grant: string; change: Change;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
/** Delay one browser delivery, while the original service performs the request. */
function holdProfile(service: ScopedChangeService, phase: "before" | "after", profileId: string | null) {
  const invoke = service.invoke.bind(service), entered = deferred<ApplyRequest>();
  const observed = deferred<{ project: Project } | { error: unknown }>(), release = deferred<void>(), finished = deferred<void>();
  let armed = true, matched = false;
  service.invoke = async <T>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyRequest | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "upsert_deliverable"
      || profileId !== null && request.change.profile.id !== profileId) return invoke<T>(command, args);
    armed = false; matched = true; entered.resolve(structuredClone(request));
    try {
      if (phase === "before") await release.promise;
      let outcome: { project: Project } | { error: unknown };
      try { outcome = { project: await invoke<Project>(command, args) }; }
      catch (error) { outcome = { error }; }
      observed.resolve(outcome);
      if (phase === "after") await release.promise;
      if ("error" in outcome) throw outcome.error;
      return outcome.project as T;
    } finally { finished.resolve(); }
  };
  return {
    entered: entered.promise, observed: observed.promise, release: () => release.resolve(),
    async dispose() { release.resolve(); service.invoke = invoke; if (matched) await finished.promise; },
  };
}
interface ProfileCase {
  service: ScopedChangeService; peers: ScopedChangeService[]; gates: ReturnType<typeof holdProfile>[];
  project: Project; a: DeliverableProfile; b: DeliverableProfile;
  observations: Record<string, unknown>;
}
const test = base.extend<{ profileCase: ProfileCase }>({
  profileCase: async ({ page }, use, info) => {
    const service = await ScopedChangeService.create({ databasePath: info.outputPath("store", "studio.sqlite3"), desktopInvokeFixture: true });
    const peers: ScopedChangeService[] = [], gates: ReturnType<typeof holdProfile>[] = [];
    const observations: Record<string, unknown> = { behaviorAssertionsCompleted: false };
    let project: Project | undefined;
    try {
      project = (await service.fixtureBootstrap()).project;
      const a: DeliverableProfile = { ...structuredClone(project.deliverables[0]), name: "Output profile A",
        width: 1920, height: 1080, brand_profile: null, cut_label: null, adaptation_notes: ["Saved initial decision"],
        parent_profile_id: null, source_revision: null, frame_rate: { num: "30", den: "1" } };
      const b: DeliverableProfile = { ...structuredClone(a), id: randomUUID(), name: "Output profile B" };
      project = await service.apply(project, { type: "upsert_deliverable", profile: a });
      project = await service.apply(project, { type: "upsert_deliverable", profile: b });
      await service.attachPage(page, project.id);
      await use({ service, peers, gates, project, a, b, observations });
    } finally {
      let finalization: unknown = null, finalizationError: string | null = null;
      try {
        // The helper quiesces every held browser operation and each peer before
        // its final oracle and closure. No open original Store is inspected.
        const settled = await Promise.allSettled(gates.map(gate => gate.dispose()));
        for (const result of settled) if (result.status === "rejected") throw result.reason;
        const current = project ? await service.project(project.id) : undefined;
        finalization = await service.finalizeClosedStore(info.outputPath("closed-store"), current, peers, [() => page.close()]);
      } catch (error) {
        finalizationError = String(error);
        const closed = await Promise.allSettled([service, ...peers].map(peer => peer.close()));
        observations.fallbackClose = closed.map(item => item.status === "fulfilled" ? { status: item.status }
          : { status: item.status, error: String(item.reason) });
        throw error;
      } finally {
        const proof = { scope: "real_service_sqlite_and_browser_raw_metadata", fixture: "synthetic_authored_inputs",
          excludes: ["installed_tauri", "native_provider", "human_acceptance"],
          testStatusAtTeardown: info.status, observations, finalization, finalizationError,
          services: [service, ...peers].map(peer => ({ binary: peer.binaryPath, transcript: peer.transcript,
            exits: peer.processExits, taints: peer.lifecycleTaints, stderr: peer.stderr })) };
        const path = info.outputPath("delivery-profile-preservation-evidence.json");
        await writeFile(path, JSON.stringify(proof, null, 2));
        await info.attach("delivery-profile-preservation-evidence", { path, contentType: "application/json" });
      }
    }
  },
});
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session from the declared composition and set its absolute path; no service fallback.");
test.describe.configure({ timeout: 90_000 });

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const input = (page: Page, name: string) => page.getByRole("textbox", { name, exact: true });
const review = (page: Page) => page.getByRole("region", { name: "Output profile draft review", exact: true });
async function revision(page: Page, expected: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + expected);
}
async function delivery(page: Page, profile?: DeliverableProfile) {
  await button(page, "Deliver").click();
  await expect(page.getByRole("region", { name: "Delivery profiles", exact: true })).toBeVisible();
  if (profile) await page.locator('.delivery-profile-row[data-profile-id="' + profile.id + '"]').click();
  await expect(review(page)).toBeVisible();
}
async function selectProfile(page: Page, profile: DeliverableProfile) {
  await page.locator('.delivery-profile-row[data-profile-id="' + profile.id + '"]').click();
}
async function reopenInUi(page: Page, project: Project) {
  await button(page, "Open project library").click();
  const library = page.getByRole("dialog", { name: "Project library", exact: true });
  await expect(library).toBeVisible();
  await library.getByRole("button", { name: "Refresh", exact: true }).click();
  await library.getByRole("button", { name: "Reopen " + project.title, exact: true }).click();
  await revision(page, project.revision);
  if (await library.isVisible()) await button(page, "Close project library").click();
}
function profileRequests(service: ScopedChangeService, profileId: string): ApplyRequest[] {
  return service.transcript.flatMap(row => {
    if (row.direction !== "request" || !("command" in row.payload) || row.payload.command !== "apply_change") return [];
    const request = row.payload.args?.request as ApplyRequest | undefined;
    return request?.change.type === "upsert_deliverable" && request.change.profile.id === profileId ? [request] : [];
  });
}
function nativeRequests(service: ScopedChangeService): string[] {
  const commands = ["render_motion_canvas", "assemble_av_master", "assemble_multisegment_av_master", "export_native_av_master"];
  return service.transcript.flatMap(row => row.direction === "request" && "command" in row.payload
    && commands.includes(row.payload.command) ? [row.payload.command] : []);
}
async function recoveryCopies(page: Page) {
  return page.evaluate(prefix => Object.keys(localStorage).filter(key => key.startsWith(prefix))
    .map(key => ({ key, raw: localStorage.getItem(key)! })), PROFILE_DRAFT_STORAGE_PREFIX);
}

test("raw fields survive A-B-A, an unrelated real revision, in-app reopen and browser reload without a profile write", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const count = profileRequests(c.service, c.a.id).length;
  const raw = { name: "  A raw title  ", width: "1e", height: "-", language: " es ",
    cut: " \t ", brand: "  raw brand  ", notes: "  first decision \n\n second decision \n" };
  await input(page, "Profile name").fill(raw.name);
  await input(page, "Width").fill(raw.width); await input(page, "Height").fill(raw.height);
  await input(page, "Language / locale").fill(raw.language); await input(page, "Cut label").fill(raw.cut);
  await input(page, "Brand profile").fill(raw.brand);
  await input(page, "Adaptation notes · one decision per line").fill(raw.notes);
  await page.getByRole("checkbox", { name: /^Lock source timing/ }).check();
  await expect(input(page, "Width")).toHaveValue(raw.width);
  await selectProfile(page, c.b); await input(page, "Profile name").fill(" B unfinished ");
  await selectProfile(page, c.a);
  await expect(input(page, "Width")).toHaveValue(raw.width);
  const peer = await c.service.peer(); c.peers.push(peer);
  const renamed = await peer.apply(await peer.project(c.project.id), { type: "rename_project", title: "Unrelated saved Project title" });
  await reopenInUi(page, renamed);
  const verify = async () => {
    await expect(input(page, "Profile name")).toHaveValue(raw.name);
    await expect(input(page, "Width")).toHaveValue(raw.width); await expect(input(page, "Height")).toHaveValue(raw.height);
    await expect(input(page, "Language / locale")).toHaveValue(raw.language);
    await expect(input(page, "Cut label")).toHaveValue(raw.cut); await expect(input(page, "Brand profile")).toHaveValue(raw.brand);
    await expect(input(page, "Adaptation notes · one decision per line")).toHaveValue(raw.notes);
    await expect(page.getByRole("checkbox", { name: /^Lock source timing/ })).toBeChecked();
  };
  await delivery(page, c.a); await verify();
  await page.reload(); await delivery(page, c.a); await verify();
  await selectProfile(page, c.b); await expect(input(page, "Profile name")).toHaveValue(" B unfinished ");
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count);
  expect(await c.service.project(c.project.id)).toEqual(renamed);
  expect((await c.service.project(c.project.id)).deliverables.find(item => item.id === c.a.id)).toEqual(c.a);
  expect(nativeRequests(c.service)).toEqual([]);
  c.observations.raw = raw; c.observations.copies = await recoveryCopies(page);
  c.observations.finalOracle = await c.service.observeStoredProject(renamed);
  c.observations.behaviorAssertionsCompleted = true;
});

test("a delayed real save preserves later A input and selected B, then only an explicit valid save reaches SQLite", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const beforeCount = profileRequests(c.service, c.a.id).length;
  await input(page, "Profile name").fill("A submitted");
  await input(page, "Adaptation notes · one decision per line").fill("  submitted decision \n\n second \n");
  const hold = holdProfile(c.service, "after", c.a.id); c.gates.push(hold);
  await button(page, "Save profile").click();
  const sent = await hold.entered, outcome = await hold.observed;
  expect("project" in outcome).toBe(true);
  if (!("project" in outcome)) throw outcome.error;
  expect(outcome.project.deliverables.find(item => item.id === c.a.id)).toEqual({
    ...c.a, name: "A submitted", adaptation_notes: ["submitted decision", "second"],
  });
  await input(page, "Profile name").fill("A typed after send");
  await input(page, "Width").fill("1e");
  await selectProfile(page, c.b); await input(page, "Profile name").fill("B remains selected");
  hold.release(); await revision(page, outcome.project.revision);
  await expect(input(page, "Profile name")).toHaveValue("B remains selected");
  await selectProfile(page, c.a);
  await expect(input(page, "Profile name")).toHaveValue("A typed after send");
  await expect(input(page, "Width")).toHaveValue("1e");
  await expect(review(page)).not.toContainText("saved value changed");
  await expect(button(page, "Save profile")).toBeEnabled();
  await button(page, "Save profile").click();
  await expect(review(page).getByRole("alert")).toContainText("complete decimal");
  expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 1);
  await expect(input(page, "Width")).toHaveValue("1e");
  await input(page, "Width").fill("001280");
  await button(page, "Save profile").click(); await revision(page, outcome.project.revision + 1);
  const final = await c.service.project(c.project.id);
  expect(final.deliverables.find(item => item.id === c.a.id)).toEqual({
    ...c.a, name: "A typed after send", width: 1280, adaptation_notes: ["submitted decision", "second"],
  });
  expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 2);
  await selectProfile(page, c.b); await expect(input(page, "Profile name")).toHaveValue("B remains selected");
  expect(final.deliverables.find(item => item.id === c.b.id)).toEqual(c.b);
  expect(nativeRequests(c.service)).toEqual([]);
  c.observations.originalSent = sent; c.observations.originalObserved = outcome.project;
  c.observations.copies = await recoveryCopies(page);
  const expected = await c.service.observeStoredProject(final);
  await c.service.close(); await c.service.start();
  expect(await c.service.project(final.id)).toEqual(final);
  expect((await c.service.observeStoredProject(final)).journal).toEqual(expected.journal);
  c.observations.finalOracle = expected;
  c.observations.behaviorAssertionsCompleted = true;
});

test("an actual stale CAS refusal preserves sent and later raw input without retry before explicit conflict review", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const beforeCount = profileRequests(c.service, c.a.id).length;
  await input(page, "Profile name").fill("A originally sent");
  const hold = holdProfile(c.service, "before", c.a.id); c.gates.push(hold);
  await button(page, "Save profile").click(); const original = await hold.entered;
  await input(page, "Profile name").fill("A still being edited");
  const peer = await c.service.peer(); c.peers.push(peer);
  const other = await peer.apply(await peer.project(c.project.id), { type: "upsert_deliverable",
    profile: { ...c.a, name: "A saved by a peer", brand_profile: "Peer brand preserved" } });
  hold.release(); const result = await hold.observed;
  expect("error" in result).toBe(true);
  await expect(review(page).getByRole("alert")).toContainText("apply_change");
  await expect(input(page, "Profile name")).toHaveValue("A still being edited");
  expect(await c.service.project(c.project.id)).toEqual(other);
  expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 1);
  await reopenInUi(page, other); await delivery(page, c.a);
  await expect(review(page)).toContainText("saved value changed");
  await expect(button(page, "Save profile")).toBeDisabled();
  const pending = review(page).getByRole("group", { name: "name pending input", exact: true });
  await expect(pending).toContainText("A originally sent");
  await expect(pending).toContainText("A still being edited");
  await pending.getByRole("button", { name: "Keep my input name", exact: true }).click();
  expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 1);
  await button(page, "Save profile").click(); await revision(page, other.revision + 1);
  const final = await c.service.project(c.project.id);
  expect(final.deliverables.find(item => item.id === c.a.id)).toEqual({
    ...c.a, name: "A still being edited", brand_profile: "Peer brand preserved",
  });
  const requests = profileRequests(c.service, c.a.id).slice(beforeCount);
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(original);
  expect(requests[1].revision).toBe(other.revision);
  expect(requests[1].request_id).not.toBe(original.request_id);
  expect(requests[1].effect_grant).not.toBe(original.effect_grant);
  expect(nativeRequests(c.service)).toEqual([]);
  c.observations.originalRejected = { request: original, error: "error" in result ? String(result.error) : null };
  c.observations.peerProject = other; c.observations.finalOracle = await c.service.observeStoredProject(final);
  c.observations.behaviorAssertionsCompleted = true;
});

test("an unreadable local recovery copy stays intact while later input is kept and only explicit discard permits saving", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const beforeCount = profileRequests(c.service, c.a.id).length;
  await input(page, "Width").fill("1e");
  const stored = (await recoveryCopies(page)).find(item => item.key.includes(c.a.id));
  expect(stored).toBeDefined();
  const originalRaw = '{"version":1,"scope":"deliberately malformed synthetic recovery"';
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: stored!.key, raw: originalRaw });
  await page.reload(); await delivery(page, c.a);
  await expect(review(page).getByRole("alert")).toContainText("original bytes are retained");
  await input(page, "Profile name").fill("New input while original stays unreadable");
  await expect(button(page, "Save profile")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), stored!.key)).toBe(originalRaw);
  expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount);
  expect((await c.service.project(c.project.id)).deliverables.find(item => item.id === c.a.id)).toEqual(c.a);
  await review(page).getByRole("button", { name: "Discard blocked profile recovery copy", exact: true }).click();
  await expect(input(page, "Profile name")).toHaveValue("New input while original stays unreadable");
  await button(page, "Save profile").click(); await revision(page, c.project.revision + 1);
  const final = await c.service.project(c.project.id);
  expect(final.deliverables.find(item => item.id === c.a.id)).toEqual({ ...c.a, name: "New input while original stays unreadable" });
  expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 1);
  expect(nativeRequests(c.service)).toEqual([]);
  c.observations.originalCorruptCopy = { key: stored!.key, raw: originalRaw };
  c.observations.finalOracle = await c.service.observeStoredProject(final);
  c.observations.behaviorAssertionsCompleted = true;
});


test("a newly created output profile does not replace the active draft when its real response arrives late", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const originalIds = (await c.service.project(c.project.id)).deliverables.map(profile => profile.id);
  const hold = holdProfile(c.service, "after", null); c.gates.push(hold);
  await button(page, "New profile").click();
  const sent = await hold.entered, outcome = await hold.observed;
  expect(sent.change.type).toBe("upsert_deliverable");
  if (sent.change.type !== "upsert_deliverable") throw new Error("The held request did not create an output profile.");
  const newProfile = sent.change.profile;
  expect(originalIds).not.toContain(newProfile.id);
  expect("project" in outcome).toBe(true);
  if (!("project" in outcome)) throw outcome.error;
  await input(page, "Profile name").fill("A input typed while another profile was created");
  hold.release(); await revision(page, outcome.project.revision);
  await expect(input(page, "Profile name")).toHaveValue("A input typed while another profile was created");
  await selectProfile(page, newProfile);
  await expect(input(page, "Profile name")).toHaveValue(newProfile.name);
  await selectProfile(page, c.a);
  await expect(input(page, "Profile name")).toHaveValue("A input typed while another profile was created");
  const final = await c.service.project(c.project.id);
  expect(final.deliverables).toHaveLength(originalIds.length + 1);
  expect(final.deliverables.find(profile => profile.id === c.a.id)).toEqual(c.a);
  expect(final.deliverables.find(profile => profile.id === newProfile.id)).toEqual(newProfile);
  expect(profileRequests(c.service, newProfile.id)).toHaveLength(1);
  expect(nativeRequests(c.service)).toEqual([]);
  c.observations.originalNewProfileRequest = sent;
  c.observations.copies = await recoveryCopies(page);
  c.observations.finalOracle = await c.service.observeStoredProject(final);
  c.observations.behaviorAssertionsCompleted = true;
});

function profileGrantCount(service: ScopedChangeService, projectId: string): number {
  return service.transcript.filter(row => {
    if (row.direction !== "request" || !("command" in row.payload) || row.payload.command !== "issue_effect_grant") return false;
    const request = row.payload.args?.request as { effect?: string; project_id?: string } | undefined;
    return request?.effect === "project_edit" && request.project_id === projectId;
  }).length;
}

for (const fault of ["changed-before-save", "readback-differs"] as const) {
  test("a real profile Save stops before its grant when recovery becomes blocked: " + fault, async ({ page, profileCase: c }) => {
    await page.goto("/"); await delivery(page, c.a);
    const rawName = "Raw kept after " + fault + "  ";
    await input(page, "Profile name").fill(rawName);
    const beforeCount = profileRequests(c.service, c.a.id).length, beforeGrants = profileGrantCount(c.service, c.project.id);
    const stored = (await recoveryCopies(page)).find(copy => copy.key.includes(c.a.id));
    expect(stored).toBeDefined();
    const replacement = '{"synthetic":"separate recovery copy","fault":"' + fault + '"}';
    await page.evaluate(({ key, replacement, mode }) => {
      const root = window as unknown as Record<string, unknown>;
      const originalSet = Storage.prototype.setItem;
      const state = { key, replacement, mode, intercepted: 0, originalSet };
      root.__mwProfileSaveStorageFault = state;
      if (mode === "changed-before-save") originalSet.call(localStorage, key, replacement);
      else Storage.prototype.setItem = function (target, value) {
        if (this === localStorage && target === key) {
          const parsed: unknown = JSON.parse(value);
          if (parsed !== null && typeof parsed === "object" && Object.hasOwn(parsed, "awaiting")) {
            state.intercepted++;
            originalSet.call(this, target, replacement);
            return;
          }
        }
        originalSet.call(this, target, value);
      };
    }, { key: stored!.key, replacement, mode: fault });
    try {
      await expect(button(page, "Save profile")).toBeEnabled();
      await button(page, "Save profile").click();
      await expect(review(page).getByRole("alert")).toContainText("The save was not sent");
      await expect(input(page, "Profile name")).toHaveValue(rawName);
      await expect(page.locator(".delivery-profile-editor-head")).not.toContainText("SAVE UNCONFIRMED");
      await expect(review(page)).not.toContainText("Input sent");
      await expect(button(page, "Save profile")).toBeDisabled();
      expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount);
      expect(profileGrantCount(c.service, c.project.id)).toBe(beforeGrants);
      expect(await page.evaluate(key => localStorage.getItem(key), stored!.key)).toBe(replacement);
      const observed = await c.service.project(c.project.id);
      expect(observed).toEqual(c.project);
      expect(observed.deliverables.find(profile => profile.id === c.a.id)).toEqual(c.a);
      expect(nativeRequests(c.service)).toEqual([]);
      const control = await page.evaluate(() => {
        const state = (window as unknown as Record<string, unknown>).__mwProfileSaveStorageFault as { intercepted: number };
        return { intercepted: state.intercepted };
      });
      expect(control.intercepted).toBe(fault === "readback-differs" ? 1 : 0);
      c.observations.recoveryAdmission = { fault, originalCopy: stored, replacement, rawName, control,
        grantsBefore: beforeGrants, grantsAfter: profileGrantCount(c.service, c.project.id), profileWritesBefore: beforeCount };
      c.observations.finalOracle = await c.service.observeStoredProject(observed);
      c.observations.behaviorAssertionsCompleted = true;
    } finally {
      await page.evaluate(() => {
        const root = window as unknown as Record<string, unknown>;
        const state = root.__mwProfileSaveStorageFault as { originalSet: typeof Storage.prototype.setItem } | undefined;
        if (state) Storage.prototype.setItem = state.originalSet;
        delete root.__mwProfileSaveStorageFault;
      });
    }
  });
}

test("an unknown own copy blocks actual Save until explicit reading proves absence and preserves the later raw", async ({ page, profileCase: c }) => {
  await page.addInitScript(prefix => {
    const root = window as unknown as Record<string, unknown>, originalGet = Storage.prototype.getItem;
    const state = { denied: true, keys: [] as string[], originalGet };
    root.__mwProfileReadFault = state;
    Storage.prototype.getItem = function (key) {
      if (this === localStorage && key.startsWith(prefix) && state.denied) {
        if (!state.keys.includes(key)) state.keys.push(key);
        throw new Error("Synthetic own profile read refusal");
      }
      return originalGet.call(this, key);
    };
  }, PROFILE_DRAFT_STORAGE_PREFIX);
  await page.goto("/"); await delivery(page, c.a);
  const beforeCount = profileRequests(c.service, c.a.id).length, beforeGrants = profileGrantCount(c.service, c.project.id);
  try {
    await expect(review(page).getByRole("alert")).toContainText("could not be read");
    await expect(page.locator(".delivery-profile-editor-head")).toContainText("DRAFT");
    await expect(button(page, "Discard blocked profile recovery copy")).toHaveCount(0);
    const rawName = "Raw typed while the original read was unknown  ";
    await input(page, "Profile name").fill(rawName);
    await expect(input(page, "Profile name")).toHaveValue(rawName);
    await expect(button(page, "Save profile")).toBeDisabled();
    expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount);
    expect(profileGrantCount(c.service, c.project.id)).toBe(beforeGrants);
    const observations = await page.evaluate(() => {
      const state = (window as unknown as Record<string, unknown>).__mwProfileReadFault as {
        denied: boolean; keys: string[]; originalGet: typeof Storage.prototype.getItem;
      };
      const copies = state.keys.map(key => ({ key, raw: state.originalGet.call(localStorage, key) }));
      state.denied = false;
      return copies;
    });
    expect(observations.length).toBeGreaterThan(0);
    expect(observations.every(copy => copy.raw === null)).toBe(true);
    await button(page, "Read profile recovery copy again").click();
    await expect(input(page, "Profile name")).toHaveValue(rawName);
    await expect(button(page, "Read profile recovery copy again")).toHaveCount(0);
    await expect(button(page, "Save profile")).toBeEnabled();
    expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount);
    expect(profileGrantCount(c.service, c.project.id)).toBe(beforeGrants);
    await button(page, "Save profile").click(); await revision(page, c.project.revision + 1);
    const final = await c.service.project(c.project.id);
    expect(final.deliverables.find(profile => profile.id === c.a.id)).toEqual({ ...c.a, name: rawName });
    expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 1);
    expect(profileGrantCount(c.service, c.project.id)).toBe(beforeGrants + 1);
    expect(nativeRequests(c.service)).toEqual([]);
    c.observations.recoveryAdmission = { previouslyUnknownCopies: observations, rawName, actualSaveCount: 1 };
    c.observations.finalOracle = await c.service.observeStoredProject(final);
    c.observations.behaviorAssertionsCompleted = true;
  } finally {
    await page.evaluate(() => {
      const root = window as unknown as Record<string, unknown>;
      const state = root.__mwProfileReadFault as { originalGet: typeof Storage.prototype.getItem } | undefined;
      if (state) Storage.prototype.getItem = state.originalGet;
      delete root.__mwProfileReadFault;
    });
  }
});

test("a known quota warning still permits one explicit real Save without changing the authored raw", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const beforeCount = profileRequests(c.service, c.a.id).length, beforeGrants = profileGrantCount(c.service, c.project.id);
  await page.evaluate(({ prefix, profileId }) => {
    const root = window as unknown as Record<string, unknown>, originalSet = Storage.prototype.setItem;
    root.__mwProfileQuotaFault = { originalSet };
    Storage.prototype.setItem = function (key, value) {
      if (this === localStorage && key.startsWith(prefix) && key.includes(profileId)) throw new Error("Synthetic known quota refusal");
      originalSet.call(this, key, value);
    };
  }, { prefix: PROFILE_DRAFT_STORAGE_PREFIX, profileId: c.a.id });
  try {
    const rawName = "  Explicit Save with a known quota warning  ";
    await input(page, "Profile name").fill(rawName);
    await expect(review(page).getByRole("alert")).toContainText("quota");
    await expect(input(page, "Profile name")).toHaveValue(rawName);
    await expect(button(page, "Save profile")).toBeEnabled();
    await expect(button(page, "Read profile recovery copy again")).toHaveCount(0);
    await button(page, "Save profile").click(); await revision(page, c.project.revision + 1);
    const final = await c.service.project(c.project.id);
    expect(final.deliverables.find(profile => profile.id === c.a.id)).toEqual({ ...c.a, name: rawName });
    expect(profileRequests(c.service, c.a.id)).toHaveLength(beforeCount + 1);
    expect(profileGrantCount(c.service, c.project.id)).toBe(beforeGrants + 1);
    expect(nativeRequests(c.service)).toEqual([]);
    c.observations.recoveryAdmission = { quotaIsKnownWriteWarning: true, rawName, actualSaveCount: 1 };
    c.observations.finalOracle = await c.service.observeStoredProject(final);
    c.observations.behaviorAssertionsCompleted = true;
  } finally {
    await page.evaluate(() => {
      const root = window as unknown as Record<string, unknown>;
      const state = root.__mwProfileQuotaFault as { originalSet: typeof Storage.prototype.setItem } | undefined;
      if (state) Storage.prototype.setItem = state.originalSet;
      delete root.__mwProfileQuotaFault;
    });
  }
});


test("custom cut stays empty until an explicit full-sequence choice across reload and real revisions", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const count = profileRequests(c.service, c.a.id).length;
  const cut = page.getByRole("region", { name: "Variant content", exact: true });
  const include = cut.getByRole("checkbox", { name: "Include", exact: true });
  expect(c.project.scenes.length).toBeGreaterThan(1);
  await button(page, "Customize cut").click();
  await expect(include).toHaveCount(c.project.scenes.length);
  for (let index = 0; index < c.project.scenes.length; index++) await include.nth(index).uncheck();
  const verifyEmpty = async () => {
    await expect(button(page, "Use full sequence")).toBeVisible();
    for (let index = 0; index < c.project.scenes.length; index++) {
      await expect(include.nth(index)).not.toBeChecked();
      await expect(include.nth(index)).toBeEnabled();
    }
    await expect(cut.getByRole("status")).toContainText("Choose at least one scene");
    await expect(button(page, "Save profile")).toBeDisabled();
    await expect(button(page, "Check Film projection")).toBeDisabled();
  };
  await verifyEmpty();
  await input(page, "Width").fill("1e");
  await selectProfile(page, c.b); await selectProfile(page, c.a);
  await verifyEmpty(); await expect(input(page, "Width")).toHaveValue("1e");
  await page.reload(); await delivery(page, c.a); await verifyEmpty();
  await expect(input(page, "Width")).toHaveValue("1e");
  const peer = await c.service.peer(); c.peers.push(peer);
  const renamed = await peer.apply(await peer.project(c.project.id), { type: "rename_project", title: "Unrelated revision with empty local cut" });
  await reopenInUi(page, renamed); await delivery(page, c.a); await verifyEmpty();
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count);
  expect((await c.service.project(c.project.id)).deliverables.find(item => item.id === c.a.id)).toEqual(c.a);
  c.observations.emptyCutCopies = await recoveryCopies(page);
  await cut.screenshot({ path: test.info().outputPath("empty-custom-cut.png") });
  await button(page, "Use full sequence").focus();
  await page.keyboard.press("Space");
  await expect(button(page, "Customize cut")).toBeFocused();
  for (let index = 0; index < c.project.scenes.length; index++) {
    await expect(include.nth(index)).toBeChecked(); await expect(include.nth(index)).toBeDisabled();
  }
  await expect(input(page, "Width")).toHaveValue("1e");
  await input(page, "Width").fill(String(c.a.width));
  await expect(button(page, "Save profile")).toBeDisabled();
  await button(page, "Customize cut").click();
  for (let index = 1; index < c.project.scenes.length; index++) await include.nth(index).uncheck();
  await button(page, "Save profile").click(); await revision(page, renamed.revision + 1);
  const saved = await c.service.project(c.project.id);
  expect(saved.deliverables.find(item => item.id === c.a.id)?.included_scene_ids).toEqual([c.project.scenes[0].id]);
  expect(saved.deliverables.find(item => item.id === c.b.id)).toEqual(c.b);
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count + 1);
  expect(nativeRequests(c.service)).toEqual([]);
  await c.service.close(); await c.service.start();
  expect(await c.service.project(saved.id)).toEqual(saved);
  await page.reload(); await delivery(page, c.a);
  await expect(include.nth(0)).toBeChecked();
  for (let index = 1; index < c.project.scenes.length; index++) await expect(include.nth(index)).not.toBeChecked();
  c.observations.finalOracle = await c.service.observeStoredProject(saved);
  c.observations.behaviorAssertionsCompleted = true;
});


test("a custom cut typed empty after a real save stays empty through ACK, reload and a peer full-sequence conflict", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  const count = profileRequests(c.service, c.a.id).length;
  const cut = page.getByRole("region", { name: "Variant content", exact: true });
  const include = cut.getByRole("checkbox", { name: "Include", exact: true });
  await button(page, "Customize cut").click();
  for (let index = 1; index < c.project.scenes.length; index++) await include.nth(index).uncheck();
  const hold = holdProfile(c.service, "after", c.a.id); c.gates.push(hold);
  await button(page, "Save profile").click();
  const sent = await hold.entered, outcome = await hold.observed;
  if (!("project" in outcome)) throw outcome.error;
  expect(outcome.project.deliverables.find(item => item.id === c.a.id)?.included_scene_ids).toEqual([c.project.scenes[0].id]);
  await include.nth(0).uncheck();
  await expect(cut.getByRole("status")).toContainText("Choose at least one scene");
  hold.release(); await revision(page, outcome.project.revision);
  await expect(button(page, "Save profile")).toBeDisabled();
  for (let index = 0; index < c.project.scenes.length; index++) await expect(include.nth(index)).not.toBeChecked();
  await page.reload(); await delivery(page, c.a);
  await expect(button(page, "Use full sequence")).toBeVisible();
  await expect(cut.getByRole("status")).toContainText("Choose at least one scene");
  const peer = await c.service.peer(); c.peers.push(peer);
  const baseProject = await peer.project(c.project.id);
  const peerProfile = baseProject.deliverables.find(item => item.id === c.a.id)!;
  const changed = await peer.apply(baseProject, { type: "upsert_deliverable", profile: { ...peerProfile, included_scene_ids: [] } });
  await reopenInUi(page, changed); await delivery(page, c.a);
  const modeReview = review(page).getByRole("group", { name: "cut mode pending input", exact: true });
  await expect(modeReview).toContainText("saved value changed");
  await expect(modeReview).toContainText("custom");
  await expect(button(page, "Save profile")).toBeDisabled();
  await modeReview.getByRole("button", { name: "Keep my input cut mode", exact: true }).click();
  await expect(cut.getByRole("status")).toContainText("Choose at least one scene");
  await expect(button(page, "Save profile")).toBeDisabled();
  await include.nth(1).check();
  await button(page, "Save profile").click(); await revision(page, changed.revision + 1);
  const saved = await c.service.project(c.project.id);
  expect(saved.deliverables.find(item => item.id === c.a.id)?.included_scene_ids).toEqual([c.project.scenes[1].id]);
  expect(saved.deliverables.find(item => item.id === c.b.id)).toEqual(c.b);
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count + 2);
  expect(nativeRequests(c.service)).toEqual([]);
  await c.service.close(); await c.service.start();
  expect(await c.service.project(saved.id)).toEqual(saved);
  c.observations.originalSent = sent; c.observations.originalObserved = outcome.project;
  c.observations.peerFullSequence = changed;
  c.observations.finalOracle = await c.service.observeStoredProject(saved);
  c.observations.behaviorAssertionsCompleted = true;
});

test("a locally empty cut does not adopt a peer custom list and reviews repeated saved cuts without losing other raw input", async ({ page, profileCase: c }) => {
  await page.setViewportSize({ width: 1024, height: 900 });
  await page.goto("/"); await delivery(page, c.a);
  const count = profileRequests(c.service, c.a.id).length;
  const cut = page.getByRole("region", { name: "Variant content", exact: true });
  const include = cut.getByRole("checkbox", { name: "Include", exact: true });
  await button(page, "Customize cut").click();
  for (let index = 0; index < c.project.scenes.length; index++) await include.nth(index).uncheck();
  await input(page, "Cut label").fill("  My independent decision  ");
  const peer = await c.service.peer(); c.peers.push(peer);
  const peerProject = await peer.project(c.project.id);
  const first = await peer.apply(peerProject, { type: "upsert_deliverable",
    profile: { ...c.a, included_scene_ids: [c.project.scenes[0].id] } });
  await reopenInUi(page, first); await delivery(page, c.a);
  const listReview = review(page).getByRole("group", { name: "included scene ids pending input", exact: true });
  await expect(listReview).toContainText("saved value changed");
  for (let index = 0; index < c.project.scenes.length; index++) await expect(include.nth(index)).not.toBeChecked();
  await expect(cut.getByRole("status")).toContainText("Choose at least one scene");
  await expect(button(page, "Save profile")).toBeDisabled();
  await expect(button(page, "Check Film projection")).toBeDisabled();
  await page.reload(); await delivery(page, c.a);
  await expect(listReview).toContainText("saved value changed");
  await listReview.getByRole("button", { name: "Keep my input included scene ids", exact: true }).click();
  await expect(cut.getByRole("status")).toContainText("Choose at least one scene");
  const second = await peer.apply(await peer.project(c.project.id), { type: "upsert_deliverable",
    profile: { ...c.a, included_scene_ids: [c.project.scenes[1].id] } });
  await reopenInUi(page, second); await delivery(page, c.a);
  await expect(listReview).toContainText("saved value changed");
  await expect(input(page, "Cut label")).toHaveValue("  My independent decision  ");
  c.observations.repeatedCutCopies = await recoveryCopies(page);
  // Global text-input sizing must not separate a native checkbox from its label
  // when the profile workbench switches to the 1024px composition.
  for (const checkbox of await cut.getByRole("checkbox").all()) {
    const bounds = await checkbox.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.width).toBeLessThanOrEqual(24);
    expect(bounds!.height).toBeLessThanOrEqual(24);
  }
  await cut.screenshot({ path: test.info().outputPath("empty-cut-at-1024px.png") });
  await listReview.getByRole("button", { name: "Keep saved included scene ids", exact: true }).click();
  await expect(button(page, "Use full sequence")).toBeVisible();
  await expect(include.nth(0)).not.toBeChecked(); await expect(include.nth(1)).toBeChecked();
  await expect(input(page, "Cut label")).toHaveValue("  My independent decision  ");
  await button(page, "Save profile").click(); await revision(page, second.revision + 1);
  const saved = await c.service.project(c.project.id);
  expect(saved.deliverables.find(item => item.id === c.a.id)).toEqual({
    ...c.a, included_scene_ids: [c.project.scenes[1].id], cut_label: "  My independent decision  ",
  });
  expect(saved.deliverables.find(item => item.id === c.b.id)).toEqual(c.b);
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count + 1);
  expect(nativeRequests(c.service)).toEqual([]);
  await c.service.close(); await c.service.start();
  expect(await c.service.project(saved.id)).toEqual(saved);
  c.observations.peerCuts = [first, second];
  c.observations.finalOracle = await c.service.observeStoredProject(saved);
  c.observations.behaviorAssertionsCompleted = true;
});

test("a draft whose profile was removed remains readable and downloadable, then editable after repeated revisions restore that exact profile", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.b);
  await input(page, "Width").fill("1e");
  await selectProfile(page, c.a);
  await input(page, "Profile name").fill("  Retained authored profile  ");
  await input(page, "Width").fill(""); await input(page, "Height").fill("-");
  const originals = await recoveryCopies(page);
  const original = originals.find(copy => JSON.parse(JSON.parse(copy.raw).scope)[3] === c.a.id)!;
  expect(original).toBeDefined();
  const count = profileRequests(c.service, c.a.id).length;
  const peer = await c.service.peer(); c.peers.push(peer);
  const removed = await peer.apply(await peer.project(c.project.id), { type: "remove_deliverable", profile_id: c.a.id });
  await reopenInUi(page, removed); await delivery(page, c.b);
  const retained = page.getByRole("region", { name: "Retained output profile drafts", exact: true });
  await expect(retained).toBeVisible({ timeout: 4000 });
  await expect(page.locator('[data-retained-profile-id="' + c.b.id + '"]')).toHaveCount(0);
  const copy = retained.locator('[data-retained-profile-id="' + c.a.id + '"]');
  await expect(copy).toHaveCount(1);
  await copy.locator("summary").click();
  await expect(copy.getByRole("textbox", { name: "name retained input", exact: true })).toHaveValue("  Retained authored profile  ");
  await expect(copy.getByRole("textbox", { name: "width retained input", exact: true })).toHaveValue("");
  await expect(copy.getByRole("textbox", { name: "height retained input", exact: true })).toHaveValue("-");
  await expect(input(page, "Width")).toHaveValue("1e");
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count);
  const downloadPromise = page.waitForEvent("download");
  await copy.getByRole("button", { name: "Download retained profile copy", exact: true }).click();
  const download = await downloadPromise, downloaded = test.info().outputPath("retained-profile-copy.json");
  await download.saveAs(downloaded);
  expect(await readFile(downloaded, "utf-8")).toBe(original.raw);
  const renamed = await peer.apply(await peer.project(c.project.id), { type: "rename_project", title: "Repeated review with a retained removed profile" });
  await reopenInUi(page, renamed); await delivery(page, c.b);
  await page.reload(); await delivery(page, c.b);
  await expect(copy).toHaveCount(1); await copy.locator("summary").click();
  await expect(copy.getByRole("textbox", { name: "width retained input", exact: true })).toHaveValue("");
  await expect(copy.getByRole("textbox", { name: "height retained input", exact: true })).toHaveValue("-");
  expect(await recoveryCopies(page)).toContainEqual(original);
  await retained.screenshot({ path: test.info().outputPath("retained-removed-profile.png") });
  const restored = await peer.apply(await peer.project(c.project.id), { type: "upsert_deliverable", profile: c.a });
  await reopenInUi(page, restored); await delivery(page, c.a);
  await expect(retained).toHaveCount(0);
  await expect(input(page, "Profile name")).toHaveValue("  Retained authored profile  ");
  await expect(input(page, "Width")).toHaveValue(""); await expect(input(page, "Height")).toHaveValue("-");
  await input(page, "Width").fill("1280"); await input(page, "Height").fill("720");
  await button(page, "Save profile").click(); await revision(page, restored.revision + 1);
  const saved = await c.service.project(c.project.id);
  expect(saved.deliverables.find(item => item.id === c.a.id)).toEqual({ ...c.a, name: "  Retained authored profile  ", width: 1280, height: 720 });
  expect(saved.deliverables.find(item => item.id === c.b.id)).toEqual(c.b);
  await selectProfile(page, c.b); await expect(input(page, "Width")).toHaveValue("1e");
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count + 1);
  expect(nativeRequests(c.service)).toEqual([]);
  await c.service.close(); await c.service.start();
  expect(await c.service.project(saved.id)).toEqual(saved);
  c.observations.removedRevision = removed; c.observations.restoredRevision = restored;
  c.observations.retainedOriginalCopy = original; c.observations.downloadedSnapshot = downloaded;
  c.observations.finalOracle = await c.service.observeStoredProject(saved);
  c.observations.behaviorAssertionsCompleted = true;
});

test("a removed profile keeps corrupt recovery bytes and later window input as separate downloadable snapshots without a profile write", async ({ page, profileCase: c }) => {
  await page.goto("/"); await delivery(page, c.a);
  await input(page, "Width").fill("1e");
  const original = (await recoveryCopies(page)).find(copy => JSON.parse(JSON.parse(copy.raw).scope)[3] === c.a.id)!;
  const corrupt = "{ corrupt original bytes retained";
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: corrupt });
  await input(page, "Profile name").fill("  Later input only in this window  ");
  await expect(review(page)).toContainText("recovery copy changed outside this view");
  const count = profileRequests(c.service, c.a.id).length;
  const peer = await c.service.peer(); c.peers.push(peer);
  const removed = await peer.apply(await peer.project(c.project.id), { type: "remove_deliverable", profile_id: c.a.id });
  await reopenInUi(page, removed); await delivery(page, c.b);
  const retained = page.getByRole("region", { name: "Retained output profile drafts", exact: true });
  const stored = retained.locator('[data-retained-profile-id="' + c.a.id + '"][data-retained-origin="storage"]');
  const windowCopy = retained.locator('[data-retained-profile-id="' + c.a.id + '"][data-retained-origin="window"]');
  await expect(stored).toHaveCount(1); await expect(windowCopy).toHaveCount(1);
  await stored.locator("summary").click(); await windowCopy.locator("summary").click();
  await expect(stored.getByRole("textbox", { name: "Unreadable retained profile copy", exact: true })).toHaveValue(corrupt);
  await expect(windowCopy.getByRole("textbox", { name: "width retained input", exact: true })).toHaveValue("1e");
  await expect(windowCopy.getByRole("textbox", { name: "name retained input", exact: true })).toHaveValue("  Later input only in this window  ");
  const downloadStored = page.waitForEvent("download");
  await stored.getByRole("button", { name: "Download retained profile copy", exact: true }).click();
  const storedDownload = await downloadStored, storedPath = test.info().outputPath("corrupt-profile-copy.json");
  await storedDownload.saveAs(storedPath);
  expect(await readFile(storedPath, "utf-8")).toBe(corrupt);
  const downloadWindow = page.waitForEvent("download");
  await windowCopy.getByRole("button", { name: "Download retained profile copy", exact: true }).click();
  const windowDownload = await downloadWindow, windowPath = test.info().outputPath("later-window-profile-copy.json");
  await windowDownload.saveAs(windowPath);
  const windowRaw = await readFile(windowPath, "utf-8"), snapshot = JSON.parse(windowRaw);
  expect(snapshot.scope).toBe(JSON.parse(original.raw).scope);
  expect(snapshot.edits.width.value).toBe("1e");
  expect(snapshot.edits.name.value).toBe("  Later input only in this window  ");
  const changedBytes = corrupt + "\nA different external copy";
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: changedBytes });
  await retained.getByRole("button", { name: "Refresh retained profile copies", exact: true }).click();
  await expect(stored.getByRole("textbox", { name: "Unreadable retained profile copy", exact: true })).toHaveValue(changedBytes);
  await expect(windowCopy.getByRole("textbox", { name: "name retained input", exact: true })).toHaveValue(snapshot.edits.name.value);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(changedBytes);
  expect(profileRequests(c.service, c.a.id)).toHaveLength(count);
  expect((await c.service.project(c.project.id)).deliverables.some(item => item.id === c.a.id)).toBe(false);
  expect(nativeRequests(c.service)).toEqual([]);
  c.observations.retainedSnapshots = { originalStored: corrupt, currentStored: changedBytes, windowRaw };
  c.observations.finalOracle = await c.service.observeStoredProject(await c.service.project(c.project.id));
  c.observations.behaviorAssertionsCompleted = true;
});
