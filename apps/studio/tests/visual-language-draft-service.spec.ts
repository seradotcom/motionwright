import { createHash } from "node:crypto";
import { lstat, readFile, readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { Change, Project } from "../src/types";
import type { VisualRecovery } from "../src/visualLanguageDraft";
import { ScopedChangeService, type StoreFinalization, type StoredProjectOracle } from "./support/scopedChangeService";

// Authored diagnostic input crosses the real Rust service, grants and SQLite.
// Transport gates delay actual replies; they never manufacture a Project.
// No installed Tauri, native realization, font-rights acceptance or human review.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Set the frozen studio_review_session binary; no simulated service fallback.");
test.setTimeout(90_000);

const digest = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const panel = (page: Page) => page.getByRole("region", { name: "Visual language", exact: true });
const field = (page: Page, name: string) => panel(page).getByLabel(name, { exact: true });
const library = (page: Page) => page.getByRole("dialog", { name: "Project library", exact: true });
const explain = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number;
    request_id: string; effect_grant: string; change: Change };
}
function applied(service: ScopedChangeService): ApplyArgs[] {
  return service.transcript.flatMap(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === "apply_change"
    ? [entry.payload.args as ApplyArgs] : []);
}
function issuedGrants(service: ScopedChangeService) {
  return service.transcript.filter(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === "issue_effect_grant");
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(complete => { resolve = complete; });
  return { promise, resolve };
}
function holdVisualApply(service: ScopedChangeService, phase: "before" | "after") {
  const original = service.invoke.bind(service);
  const entered = deferred<ApplyArgs>(), observed = deferred<{ project: Project } | { error: string }>();
  const released = deferred<void>(), finished = deferred<void>();
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "set_visual_language") {
      return original<T>(command, args);
    }
    armed = false; matched = true; entered.resolve(structuredClone(args) as ApplyArgs);
    try {
      if (phase === "before") await released.promise;
      try {
        const project = await original<Project>(command, args);
        observed.resolve({ project });
        if (phase === "after") await released.promise;
        return project as T;
      } catch (reason) {
        observed.resolve({ error: explain(reason) });
        if (phase === "after") await released.promise;
        throw reason;
      }
    } finally { finished.resolve(); }
  };
  return { entered: entered.promise, observed: observed.promise,
    release: () => released.resolve(),
    async dispose() {
      service.invoke = original; released.resolve();
      if (matched) await finished.promise;
    } };
}
type Gate = ReturnType<typeof holdVisualApply>;
interface Proof {
  page: Page; info: TestInfo; service: ScopedChangeService; services: ScopedChangeService[];
  extraPages: Page[];
  gates: Gate[]; initial: Project; current: Project;
  initialOracle: StoredProjectOracle;
  checkpoints: { label: string; oracle: StoredProjectOracle }[];
  intentions: Record<string, unknown>[];
  observations: Record<string, unknown>[];
}
const visualPrefix = "motionwright.visual-draft.v1:";
const visualSessionPrefix = "motionwright.visual-session.v1:";
const visualScope = (project: Project) => JSON.stringify([project.id, project.generation, project.active_branch]);
const backupWarning = (page: Page) => panel(page).getByRole("alert", { name: "Visual draft backup warning", exact: true });
const visualCommit = (page: Page) => panel(page).getByRole("button", { name: "Commit visual system", exact: true });
async function activeVisualCopy(page: Page, project: Project) {
  return page.evaluate(({ prefix, sessionPrefix, scope }) => {
    const writerId = sessionStorage.getItem(sessionPrefix + scope);
    if (!writerId) throw new Error("The active visual writer has no session pointer");
    const key = prefix + JSON.stringify([scope, writerId]);
    const raw = localStorage.getItem(key);
    if (raw === null) throw new Error("The active visual writer has no retained copy");
    return { key, raw, value: JSON.parse(raw) as VisualRecovery };
  }, { prefix: visualPrefix, sessionPrefix: visualSessionPrefix, scope: visualScope(project) });
}
async function storageFault(page: Page, mode: "off" | "all_visual_writes" | "clean_visual_writes") {
  await page.evaluate(({ prefix, mode }) => {
    const runtime = window as unknown as {
      visualDraftTestStorageFault?: { mode: typeof mode; original: typeof Storage.prototype.setItem; failures: number };
    };
    if (!runtime.visualDraftTestStorageFault) {
      runtime.visualDraftTestStorageFault = { mode, original: Storage.prototype.setItem, failures: 0 };
      Storage.prototype.setItem = function(key: string, value: string) {
        const state = runtime.visualDraftTestStorageFault!;
        const relevant = this === localStorage && key.startsWith(prefix);
        const clean = relevant && state.mode === "clean_visual_writes" && JSON.parse(value).draft === null;
        if (relevant && (state.mode === "all_visual_writes" || clean)) {
          state.failures += 1;
          throw new DOMException("Deterministic visual recovery quota failure", "QuotaExceededError");
        }
        return state.original.call(this, key, value);
      };
    } else runtime.visualDraftTestStorageFault.mode = mode;
  }, { prefix: visualPrefix, mode });
}
async function uiSources() {
  const names = ["VisualLanguageEditor.tsx", "visualLanguageDraft.ts", "CanvasWorkspace.tsx", "App.tsx",
    "CompositionStudy.tsx", "ExpandedStudy.tsx", "canvasInheritedInk.ts", "CameraReframeWorkbench.tsx",
    "CreativePatchWorkbench.tsx", "ProceduralFieldWorkbench.tsx", "ProductStageWorkbench.tsx",
    "ProductionDesignWorkspace.tsx"];
  return Promise.all(names.map(async name => ({ path: "apps/studio/src/" + name,
    sha256: digest(await readFile(new URL("../src/" + name, import.meta.url))) })));
}
async function paint(page: Page) {
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function revision(page: Page, expected: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + expected);
  await paint(page);
}
async function openCanvas(page: Page) {
  await button(page, "Canvas").click();
  await expect(panel(page)).toBeVisible();
  await paint(page);
}
async function checkpoint(proof: Proof, label: string, project = proof.current) {
  const oracle = await proof.service.observeStoredProject(project);
  proof.checkpoints.push({ label, oracle });
  proof.current = project;
  return oracle;
}
async function currentAfterUi(proof: Proof, expectedRevision: number, label: string) {
  await revision(proof.page, expectedRevision);
  const current = await proof.service.project(proof.initial.id);
  expect(current.revision).toBe(expectedRevision);
  await checkpoint(proof, label, current);
  return current;
}
async function reopenSaved(page: Page, project: Project) {
  await button(page, "Open project library").click();
  await expect(library(page)).toBeVisible();
  await library(page).getByRole("button", { name: "Reopen " + project.title, exact: true }).click();
  await expect(library(page)).not.toBeVisible();
  await revision(page, project.revision);
}

// The small ZIP32 writer follows the existing delivery-recipe evidence format.
// It includes the complete closed capture and original oracle before this worker
// returns. It never includes screenshots, traces or transport/grant transcripts.
function crc32(bytes: Uint8Array) {
  let crc = 0xffffffff;
  for (const byte of bytes) { crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
function zip32(files: { name: string; bytes: Buffer; directory?: boolean }[]) {
  if (files.length >= 65535) throw new Error("Evidence ZIP32 entry budget exceeded");
  const local: Buffer[] = [], central: Buffer[] = []; let offset = 0;
  for (const file of files) {
    const name = Buffer.from(file.name), size = file.bytes.length, crc = crc32(file.bytes);
    if (name.length > 65535 || size > 0xffffffff || offset > 0xffffffff) throw new Error("Evidence ZIP32 size budget exceeded");
    const header = Buffer.alloc(30); header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4); header.writeUInt16LE(0x800, 6);
    header.writeUInt16LE(33, 12); header.writeUInt32LE(crc, 14); header.writeUInt32LE(size, 18); header.writeUInt32LE(size, 22); header.writeUInt16LE(name.length, 26);
    local.push(header, name, file.bytes);
    const record = Buffer.alloc(46); record.writeUInt32LE(0x02014b50); record.writeUInt16LE(20, 4); record.writeUInt16LE(20, 6); record.writeUInt16LE(0x800, 8);
    record.writeUInt16LE(33, 14); record.writeUInt32LE(crc, 16); record.writeUInt32LE(size, 20); record.writeUInt32LE(size, 24); record.writeUInt16LE(name.length, 28);
    record.writeUInt32LE(file.directory ? 16 : 0, 38); record.writeUInt32LE(offset, 42); central.push(record, name);
    offset += header.length + name.length + size;
  }
  const centralSize = central.reduce((sum, item) => sum + item.length, 0);
  if (offset + centralSize > 0xffffffff) throw new Error("Evidence ZIP32 archive budget exceeded");
  const end = Buffer.alloc(22); end.writeUInt32LE(0x06054b50); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralSize, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, ...central, end]);
}
async function archiveCapture(info: TestInfo) {
  const root = info.outputPath("closed-store");
  const files: { name: string; bytes: Buffer; directory?: boolean }[] = [];
  const visit = async (path: string, name: string): Promise<void> => {
    if (name && (name.startsWith("/") || name.includes("\\") || name.split("/").includes(".."))) throw new Error("Unsafe evidence name");
    const stat = await lstat(path);
    if (stat.isSymbolicLink()) throw new Error("Evidence capture contains a symlink");
    if (stat.isDirectory()) {
      if (name) files.push({ name: name + "/", bytes: Buffer.alloc(0), directory: true });
      for (const child of (await readdir(path)).sort()) await visit(join(path, child), name ? name + "/" + child : child);
    } else if (stat.isFile()) files.push({ name, bytes: await readFile(path) });
    else throw new Error("Evidence capture contains a non-regular entry");
  };
  await visit(root, "");
  const bytes = zip32(files), path = info.outputPath("closed-store.zip");
  await writeFile(path, bytes, { flag: "wx" });
  return { path, sha256: digest(bytes), size_bytes: bytes.length, entries: files.length,
    evidence_scope: "complete_closed_capture_with_original_preclosure_oracle; independent_extraction_readback_not_yet_run" };
}

async function scenario(page: Page, info: TestInfo, body: (proof: Proof) => Promise<void>) {
  const sourceAtStart = await uiSources();
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true });
  let proof: Proof | undefined, caseFailure: string | null = null;
  let finalization: StoreFinalization | undefined, archive: Awaited<ReturnType<typeof archiveCapture>> | undefined;
  const cleanupErrors: string[] = [];
  try {
    const initial = (await service.fixtureBootstrap()).project;
    const initialOracle = await service.observeStoredProject(initial);
    proof = { page, info, service, services: [service], extraPages: [], gates: [], initial, current: initial,
      initialOracle, checkpoints: [{ label: "initial complete oracle before interaction", oracle: initialOracle }], intentions: [], observations: [] };
    await service.attachPage(page, initial.id);
    await page.goto("/");
    await revision(page, initial.revision);
    await openCanvas(page);
    await body(proof);
  } catch (reason) { caseFailure = explain(reason); throw reason; }
  finally {
    for (const gate of proof?.gates ?? []) {
      try { await gate.dispose(); } catch (reason) { cleanupErrors.push("gate: " + explain(reason)); }
    }
    if (!page.isClosed()) {
      try { await page.screenshot({ path: info.outputPath("internal-visual-state.png"), fullPage: true, timeout: 5000 }); }
      catch (reason) { cleanupErrors.push("screenshot: " + explain(reason)); }
      try { await page.close(); } catch (reason) { cleanupErrors.push("browser close: " + explain(reason)); }
    }
    for (const extra of proof?.extraPages ?? []) if (!extra.isClosed()) {
      try { await extra.close(); } catch (reason) { cleanupErrors.push("additional browser close: " + explain(reason)); }
    }
    // For REDs this is the observed failed-case store, not a newly invented
    // expected UI result. All intended inputs and earlier oracles stay separate.
    try {
      const observed = proof ? await service.project(proof.initial.id) : undefined;
      if (proof && observed) proof.checkpoints.push({ label: "observed final state before every process closes", oracle: await service.observeStoredProject(observed) });
      finalization = await service.finalizeClosedStore(info.outputPath("closed-store"), observed, proof?.services.slice(1) ?? []);
      archive = await archiveCapture(info);
    } catch (reason) { cleanupErrors.push("store finalization: " + explain(reason)); }
    finally {
      // A failed preparation/finalization must still attempt every process exit.
      const closes = await Promise.allSettled((proof?.services ?? [service]).map(item => item.close()));
      closes.forEach((result, index) => { if (result.status === "rejected") cleanupErrors.push("final close " + index + ": " + explain(result.reason)); });
    }
    const sourceAtEnd = await uiSources();
    const sourceUnchanged = JSON.stringify(sourceAtStart) === JSON.stringify(sourceAtEnd);
    if (!sourceUnchanged) cleanupErrors.push("UI source files changed while this case was running");
    const path = info.outputPath("visual-language-real-service-evidence.json");
    const evidence = {
      schema: "motionwright.visual-language-service-evidence/1", fixture: "synthetic_authored_content",
      test: info.title, case_result: caseFailure ? "failed" : "completed", failure: caseFailure,
      // The runner supplies the source identity from its actual service build receipt.
      runner_source_revision_operator_supplied: process.env.MOTIONWRIGHT_SERVICE_TEST_SOURCE_REVISION ?? null,
      ui_source_revision_operator_supplied: process.env.MOTIONWRIGHT_TEST_UI_REVISION ?? null,
      ui_sources: { start: sourceAtStart, end: sourceAtEnd, unchanged_during_case: sourceUnchanged },
      test_source_sha256: digest(await readFile(new URL("./visual-language-draft-service.spec.ts", import.meta.url))),
      inspected_source_sha256: digest(await readFile(new URL("../src/VisualLanguageEditor.tsx", import.meta.url))),
      runner_sha256: digest(await readFile(service.binaryPath)),
      evidence_scope: "real_service_sqlite_cas_and_browser_visual_input_continuity",
      excludes: ["installed_tauri", "native_realization", "font_rights_acceptance", "human_acceptance"],
      initialOracle: proof?.initialOracle, checkpoints: proof?.checkpoints ?? [],
      intendedInputs: proof?.intentions ?? [], observations: proof?.observations ?? [],
      finalization, archive, cleanupErrors,
      services: (proof?.services ?? [service]).map(item => ({ databasePath: item.databasePath,
        processExits: item.processExits, lifecycleTaints: item.lifecycleTaints, stderr: item.stderr, transcript: item.transcript })),
    };
    await writeFile(path, JSON.stringify(evidence, null, 2), { flag: "wx" });
    await info.attach("visual-language-real-service-evidence", { path, contentType: "application/json" });
    if (archive) await info.attach("complete-closed-store", { path: archive.path, contentType: "application/zip" });
    if (cleanupErrors.length) throw new Error("Evidence finalization failed: " + cleanupErrors.join("; "));
  }
}

test("pending visual name and palette survive a real project style-lock revision", async ({ page }, info) => scenario(page, info, async proof => {
  const name = "Owned visual decisions retained through a style lock";
  const color = "#205A63";
  proof.intentions.push({ before_revision: proof.current.revision, name, palette_value_1: color, committed: false });
  await field(page, "Visual system name").fill(name);
  await field(page, "Palette tokens value 1").fill(color);
  await panel(page).getByRole("button", { name: "Protect style", exact: true }).click();
  const locked = await currentAfterUi(proof, proof.initial.revision + 1, "real style protection, visual content remains canonical");
  expect(locked.visual_language).toEqual(proof.initial.visual_language);
  expect(locked.locks.some(lock => lock.resource === "project:" + locked.id && lock.kind === "style")).toBe(true);
  await expect(field(page, "Visual system name")).toHaveValue(name);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(color);
  await expect(panel(page).getByRole("button", { name: "Commit visual system", exact: true })).toBeDisabled();
  await panel(page).getByRole("button", { name: "Unlock style", exact: true }).click();
  await currentAfterUi(proof, locked.revision + 1, "style unlocked without replacing pending inputs");
  await expect(field(page, "Visual system name")).toHaveValue(name);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(color);
  await panel(page).getByRole("button", { name: "Commit visual system", exact: true }).click();
  const saved = await currentAfterUi(proof, proof.current.revision + 1, "explicit visual save after unlocking");
  expect(saved.visual_language).toEqual({ ...proof.initial.visual_language, version: proof.initial.visual_language.version + 1,
    name, palette: proof.initial.visual_language.palette.map((token, index) => index ? token : { ...token, value: color }) });
}));

test("a real visual save acknowledgement preserves input authored after its dispatch", async ({ page }, info) => scenario(page, info, async proof => {
  const sent = "Visual system sent to the real service", later = "Later human visual decision remains pending";
  const laterColor = "#783D54";
  const gate = holdVisualApply(proof.service, "after"); proof.gates.push(gate);
  proof.intentions.push({ before_revision: proof.initial.revision, sent, later, later_palette_value_1: laterColor });
  await field(page, "Visual system name").fill(sent);
  await panel(page).getByRole("button", { name: "Commit visual system", exact: true }).click();
  const outcome = await gate.observed;
  if ("error" in outcome) throw new Error(outcome.error);
  const accepted = outcome.project;
  expect(accepted.visual_language.name).toBe(sent);
  await checkpoint(proof, "actual committed Project with reply still held", accepted);
  await field(page, "Visual system name").fill(later);
  await field(page, "Palette tokens value 1").fill(laterColor);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  gate.release();
  await revision(page, accepted.revision);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(laterColor);
  expect(await proof.service.project(accepted.id)).toEqual(accepted);
  await button(page, "Brief").click();
  await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await panel(page).getByRole("button", { name: "Commit visual system", exact: true }).click();
  const saved = await currentAfterUi(proof, accepted.revision + 1, "later decision explicitly saved as another revision");
  expect(saved.visual_language.name).toBe(later);
  expect(saved.visual_language.palette[0].value).toBe(laterColor);
  expect(applied(proof.service).filter(request => request.request.change.type === "set_visual_language")).toHaveLength(2);
}));

test("reload and review recovers a real committed visual save whose acknowledgement stays held and never replays it", async ({ page }, info) => scenario(page, info, async proof => {
  const sent = "Visual snapshot actually committed before its acknowledgement was held";
  const later = "Human visual input authored after that dispatch and before reload";
  const laterColor = "#4C637A";
  const reviewed = "A further human visual edit made after reopening the actual saved revision";
  const grantsBefore = issuedGrants(proof.service).length;
  const gate = holdVisualApply(proof.service, "after"); proof.gates.push(gate);
  proof.intentions.push({ before_revision: proof.initial.revision, sent, later,
    later_palette_value_1: laterColor, explicit_post_reload_name: reviewed,
    transport_gate: "actual apply response held until scenario finalizer; no release in the test body" });
  await field(page, "Visual system name").fill(sent);
  await visualCommit(page).click();
  const originalRequest = await gate.entered;
  const outcome = await gate.observed;
  if ("error" in outcome) throw new Error(outcome.error);
  const accepted = outcome.project;
  expect(accepted.visual_language).toEqual({ ...proof.initial.visual_language,
    version: proof.initial.visual_language.version + 1, name: sent });
  const committedOracle = await checkpoint(proof, "actual saved Project and complete journal while its first browser reply remains held", accepted);
  await revision(page, proof.initial.revision);
  const pending = panel(page).getByRole("region", { name: "Pending visual submission", exact: true });
  await expect(pending).toBeVisible();
  await field(page, "Visual system name").fill(later);
  await field(page, "Palette tokens value 1").fill(laterColor);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(laterColor);
  const retained = await activeVisualCopy(page, proof.initial);
  expect(retained.value.draft?.fields.name.raw).toBe(later);
  expect(applied(proof.service)).toHaveLength(1);
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore + 1);
  const previousDocument = await page.evaluate(() => performance.timeOrigin);
  await Promise.all([
    page.waitForEvent("domcontentloaded"),
    pending.getByRole("button", { name: "Reload and review saved visual system", exact: true }).click(),
  ]);
  await expect.poll(() => page.evaluate(() => performance.timeOrigin)).not.toBe(previousDocument);
  await openCanvas(page); await revision(page, accepted.revision);
  await expect(pending).toHaveCount(0);
  await expect(panel(page).getByRole("region", { name: "Unconfirmed visual submission", exact: true })).toHaveCount(0);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(laterColor);
  await expect(visualCommit(page)).toBeEnabled();
  expect(await proof.service.observeStoredProject(accepted)).toMatchObject({ project: accepted, journal: committedOracle.journal });
  expect(applied(proof.service)).toHaveLength(1);
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore + 1);
  await button(page, "Brief").click(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(laterColor);
  expect(applied(proof.service)).toHaveLength(1);
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore + 1);
  proof.observations.push({ phase: "reloaded through the real pending-submission control before any gate release",
    actual_saved_revision: accepted.revision, apply_requests: applied(proof.service).length,
    new_grant_requests: issuedGrants(proof.service).length - grantsBefore,
    retained_raw_sha256: digest(Buffer.from(retained.raw)), first_request_id: originalRequest.request.request_id });

  // The original reply remains held. Only this deliberate new edit may obtain
  // another grant and create another service revision after the explicit reload.
  await field(page, "Visual system name").fill(reviewed);
  await visualCommit(page).click();
  const saved = await currentAfterUi(proof, accepted.revision + 1, "new explicit visual edit after reload, with the first acknowledgement still held");
  expect(saved.visual_language).toEqual({ ...accepted.visual_language, version: accepted.visual_language.version + 1, name: reviewed,
    palette: accepted.visual_language.palette.map((token, index) => index ? token : { ...token, value: laterColor }) });
  const changes = applied(proof.service);
  expect(changes).toHaveLength(2);
  expect(changes.filter(request => request.request.request_id === originalRequest.request.request_id)).toHaveLength(1);
  expect(changes[1].request.request_id).not.toBe(originalRequest.request.request_id);
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore + 2);
  const beforeClose = await checkpoint(proof, "complete oracle before process restart while the old browser reply remains held", saved);
  await proof.service.close(); await proof.service.start();
  expect(await proof.service.project(saved.id)).toEqual(beforeClose.project);
  expect(await proof.service.history(saved.id, 0, 256)).toEqual(beforeClose.journal);
  await page.reload(); await openCanvas(page); await revision(page, saved.revision);
  await expect(field(page, "Visual system name")).toHaveValue(reviewed);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(laterColor);
  await expect(visualCommit(page)).toBeDisabled();
  expect(applied(proof.service)).toHaveLength(2);
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore + 2);
  proof.observations.push({ phase: "explicit second save and real service reopen completed before scenario cleanup releases the old reply",
    final_revision: saved.revision, apply_requests: 2, new_grant_requests: issuedGrants(proof.service).length - grantsBefore });
}));

test("an empty motion-duration input stays raw and cannot silently become a saved zero", async ({ page }, info) => scenario(page, info, async proof => {
  const index = proof.initial.visual_language.motion_grammar.length + 1;
  const grantsBefore = issuedGrants(proof.service).length;
  proof.intentions.push({ before_revision: proof.initial.revision, new_motion_row: index, duration_raw: "", committed: false });
  await panel(page).getByRole("button", { name: "Add motion verb", exact: true }).click();
  await field(page, "Motion verb name " + index).fill("review_hold");
  await field(page, "Motion verb meaning " + index).fill("Hold the diagnostic comparison while its author reviews it.");
  await field(page, "Motion verb duration " + index).fill("");
  await expect(field(page, "Motion verb duration " + index)).toHaveValue("");
  await expect(panel(page).getByRole("button", { name: "Commit visual system", exact: true })).toBeDisabled();
  await button(page, "Brief").click();
  await openCanvas(page);
  await expect(field(page, "Motion verb duration " + index)).toHaveValue("");
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Motion verb duration " + index)).toHaveValue("");
  await expect(visualCommit(page)).toBeDisabled();
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore);
  proof.intentions.push({ before_revision: proof.initial.revision, new_motion_row: index, duration_raw: "1e", committed: false });
  await field(page, "Motion verb duration " + index).fill("1e");
  await expect(field(page, "Motion verb duration " + index)).toHaveValue("1e");
  await expect(visualCommit(page)).toBeDisabled();
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Motion verb duration " + index)).toHaveValue("1e");
  await expect(visualCommit(page)).toBeDisabled();
  expect(await proof.service.observeStoredProject(proof.initial)).toMatchObject({ project: proof.initial, journal: proof.initialOracle.journal });
  expect(applied(proof.service)).toHaveLength(0);
  expect(issuedGrants(proof.service)).toHaveLength(grantsBefore);
}));

test("visual input survives a real process restart and remains editable after a second reopen", async ({ page }, info) => scenario(page, info, async proof => {
  const name = "Visual draft surviving closed SQLite and browser reload";
  const color = "#365C46";
  proof.intentions.push({ before_revision: proof.initial.revision, name, palette_value_1: color, committed: false });
  await field(page, "Visual system name").fill(name);
  await field(page, "Palette tokens value 1").fill(color);
  const beforeClose = await checkpoint(proof, "complete oracle before first process close");
  await proof.service.close(); await proof.service.start();
  expect(await proof.service.project(proof.initial.id)).toEqual(beforeClose.project);
  expect(await proof.service.history(proof.initial.id, 0, 256)).toEqual(beforeClose.journal);
  proof.observations.push({ first_process_restart: true, project_id: proof.initial.id, revision: beforeClose.project.revision });
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(name);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(color);
  await panel(page).getByRole("button", { name: "Commit visual system", exact: true }).click();
  const saved = await currentAfterUi(proof, proof.initial.revision + 1, "new explicit edit after reopening");
  expect(saved.visual_language.name).toBe(name);
  expect(saved.visual_language.palette[0].value).toBe(color);
  const savedOracle = await checkpoint(proof, "complete oracle before second process close");
  await proof.service.close(); await proof.service.start();
  expect(await proof.service.project(saved.id)).toEqual(savedOracle.project);
  expect(await proof.service.history(saved.id, 0, 256)).toEqual(savedOracle.journal);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(name);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(color);
  proof.observations.push({ second_process_restart: true, project_id: saved.id, revision: saved.revision });
}));

test("ten real peer revisions preserve pending visual choices and every unrelated saved decision", async ({ page }, info) => scenario(page, info, async proof => {
  const peer = await proof.service.peer(); proof.services.push(peer);
  const name = "Pending visual system across ten editorial decisions", color = "#4E507C";
  proof.intentions.push({ before_revision: proof.initial.revision, name, palette_value_1: color, committed: false });
  await field(page, "Visual system name").fill(name);
  await field(page, "Palette tokens value 1").fill(color);
  const briefChange = (current: Project, patch: Partial<Pick<Project["brief"], "objective" | "audience" | "constraints" | "exclusions">>): Change => ({
    type: "set_brief", objective: current.brief.objective, audience: current.brief.audience,
    constraints: current.brief.constraints, exclusions: current.brief.exclusions, ...patch,
  });
  const decisions: ((current: Project) => Change)[] = [
    current => briefChange(current, { objective: "Show an editable comparison with the source decisions preserved." }),
    current => briefChange(current, { audience: "An editor continuing the same project through multiple reviews." }),
    current => briefChange(current, { constraints: [...current.brief.constraints, "Keep both diagnostic objects independently editable."] }),
    current => briefChange(current, { exclusions: [...current.brief.exclusions, "Do not replace pending human visual input."] }),
    () => ({ type: "set_narrative_premise", premise: "A deliberate visual choice remains pending while the cut is revised." }),
    current => ({ type: "update_scene_objective", scene_id: current.scenes[0].id, objective: "Introduce the original editable comparison." }),
    current => ({ type: "update_scene_objective", scene_id: current.scenes[1].id, objective: "Review the consequences of the revised comparison." }),
    current => ({ type: "set_mix_intent", mix: { ...current.audio.mix, music_gain_db: -9 } }),
    () => ({ type: "add_marker", at: { num: "1", den: "1" }, label: "Human visual review checkpoint" }),
    () => ({ type: "rename_project", title: "Ten editorial revisions with visual intent retained" }),
  ];
  expect(proof.initial.scenes.length).toBeGreaterThanOrEqual(2);
  for (const [index, decision] of decisions.entries()) {
    const before = proof.current, change = decision(before);
    proof.intentions.push({ peer_decision: index + 1, before_revision: before.revision, change });
    const next = await peer.apply(before, change);
    expect(next.revision).toBe(before.revision + 1);
    expect(next.visual_language).toEqual(proof.initial.visual_language);
    await checkpoint(proof, "peer editorial decision " + (index + 1), next);
    await reopenSaved(page, next);
    proof.observations.push({ peer_decision: index + 1, revision: next.revision,
      observed_name_input: await field(page, "Visual system name").inputValue(),
      observed_palette_input: await field(page, "Palette tokens value 1").inputValue() });
  }
  const beforeVisual = proof.current;
  const journal = await proof.service.history(beforeVisual.id, 0, 256);
  expect(journal).toHaveLength(proof.initialOracle.journal.length + 10);
  expect(journal.slice(0, proof.initialOracle.journal.length)).toEqual(proof.initialOracle.journal);
  expect(journal.slice(-10).map(event => event.change)).toEqual(proof.intentions.filter(item => item.peer_decision).map(item => item.change));
  await expect(field(page, "Visual system name")).toHaveValue(name);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(color);
  await panel(page).getByRole("button", { name: "Commit visual system", exact: true }).click();
  const saved = await currentAfterUi(proof, beforeVisual.revision + 1, "pending visual choices committed after all ten peer revisions");
  expect(saved.brief).toEqual(beforeVisual.brief);
  expect(saved.narrative).toEqual(beforeVisual.narrative);
  expect(saved.scenes).toEqual(beforeVisual.scenes);
  expect(saved.audio).toEqual(beforeVisual.audio);
  expect(saved.markers).toEqual(beforeVisual.markers);
  expect(saved.visual_language.name).toBe(name);
  expect(saved.visual_language.palette[0].value).toBe(color);
}));

test("a real stale visual save rejects without overwriting the peer and retains its draft for conflict review", async ({ page }, info) => scenario(page, info, async proof => {
  const peer = await proof.service.peer(); proof.services.push(peer);
  const localName = "Local visual proposal awaiting explicit conflict review";
  const peerName = "Peer visual decision already saved";
  proof.intentions.push({ before_revision: proof.initial.revision, local_name: localName, peer_name: peerName });
  await field(page, "Visual system name").fill(localName);
  const gate = holdVisualApply(proof.service, "before"); proof.gates.push(gate);
  await panel(page).getByRole("button", { name: "Commit visual system", exact: true }).click();
  const originalRequest = await gate.entered;
  const winner = await peer.apply(proof.initial, { type: "set_visual_language", visual_language: {
    ...proof.initial.visual_language, version: proof.initial.visual_language.version + 1, name: peerName,
  } });
  await checkpoint(proof, "peer visual decision before releasing the stale request", winner);
  gate.release();
  const rejected = await gate.observed;
  if ("project" in rejected) throw new Error("The real service incorrectly accepted the stale visual proposal");
  expect(rejected.error).toMatch(/stale|revision|conflict/i);
  proof.observations.push({ rejected_request: originalRequest, native_rejection: rejected.error });
  await expect(panel(page).getByRole("alert").filter({ hasText: /stale|revision|conflict/i })).toBeVisible();
  expect(await proof.service.project(winner.id)).toEqual(winner);
  await expect(field(page, "Visual system name")).toHaveValue(localName);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(localName);
  await expect(panel(page).getByRole("button", { name: "Commit visual system", exact: true })).toBeDisabled();
  expect(await proof.service.project(winner.id)).toEqual(winner);
  expect(applied(proof.service)).toHaveLength(1);
  expect((await proof.service.history(winner.id, 0, 256)).slice(-1)[0].change).toMatchObject({
    type: "set_visual_language", visual_language: { name: peerName },
  });
}));

test("explicit visual conflict resolution keeps disjoint peer decisions and permits another save after reopen", async ({ page }, info) => scenario(page, info, async proof => {
  const peer = await proof.service.peer(); proof.services.push(peer);
  const name = "Human visual choice after an explicit overlap review", color = "#584165";
  const peerName = "Peer saved visual name requiring review";
  const peerRule = "Keep the second diagnostic object independently editable through every revision.";
  proof.intentions.push({ before_revision: proof.initial.revision, pending_name: name, pending_palette: color,
    peer_name: peerName, peer_disjoint_rule: peerRule });
  await field(page, "Visual system name").fill(name);
  await field(page, "Palette tokens value 1").fill(color);
  let winner = await peer.apply(proof.initial, { type: "set_visual_language", visual_language: {
    ...proof.initial.visual_language, version: proof.initial.visual_language.version + 1,
    name: peerName, anti_slop_rules: [...proof.initial.visual_language.anti_slop_rules, peerRule],
  } });
  await checkpoint(proof, "peer visual name and disjoint rule saved before conflict review", winner);
  winner = await peer.apply(winner, { type: "set_brief", objective: "Review the consequences while preserving each contributor's decisions.",
    audience: winner.brief.audience, constraints: winner.brief.constraints, exclusions: winner.brief.exclusions });
  await checkpoint(proof, "another real peer revision before resolving the visual overlap", winner);
  await reopenSaved(page, winner);
  const conflict = panel(page).getByRole("region", { name: "Visual conflict: Visual language name", exact: true });
  await expect(conflict).toContainText(name); await expect(conflict).toContainText(peerName);
  await expect(field(page, "Palette tokens value 1")).toHaveValue(color);
  await expect(field(page, "Anti-slop rule " + winner.visual_language.anti_slop_rules.length)).toHaveValue(peerRule);
  await expect(visualCommit(page)).toBeDisabled();
  expect(applied(proof.service)).toHaveLength(0);
  expect(await proof.service.project(winner.id)).toEqual(winner);
  const keep = conflict.getByRole("button", { name: "Keep my Visual language name", exact: true });
  await keep.focus(); await keep.press("Enter");
  await expect(conflict).toHaveCount(0);
  await expect(field(page, "Visual system name")).toBeFocused();
  await expect(visualCommit(page)).toBeEnabled();
  await visualCommit(page).click();
  const saved = await currentAfterUi(proof, winner.revision + 1, "human overlap choice committed with peer groups retained");
  expect(saved.visual_language).toEqual({ ...winner.visual_language, version: winner.visual_language.version + 1, name,
    palette: winner.visual_language.palette.map((token, index) => index ? token : { ...token, value: color }) });
  expect(saved.brief).toEqual(winner.brief);
  expect(saved.scenes).toEqual(winner.scenes);
  expect(saved.audio).toEqual(winner.audio);
  await peer.close();
  const beforeClose = await checkpoint(proof, "complete resolved oracle before all service processes close", saved);
  await proof.service.close(); await proof.service.start();
  expect(await proof.service.project(saved.id)).toEqual(beforeClose.project);
  expect(await proof.service.history(saved.id, 0, 256)).toEqual(beforeClose.journal);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(name);
  const later = "A further editable visual decision after reopening the resolved project";
  proof.intentions.push({ before_revision: saved.revision, explicit_post_reopen_name: later });
  await field(page, "Visual system name").fill(later); await visualCommit(page).click();
  const final = await currentAfterUi(proof, saved.revision + 1, "new explicit visual edit after conflict resolution and process reopen");
  expect(final.visual_language).toEqual({ ...saved.visual_language, version: saved.visual_language.version + 1, name: later });
  expect(final.brief).toEqual(winner.brief);
  expect(applied(proof.service)).toHaveLength(2);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(visualCommit(page)).toBeDisabled();
}));

test("a visual recovery quota failure prevents dispatch and the latest human input can be backed up reopened and saved", async ({ page }, info) => scenario(page, info, async proof => {
  const first = "Visual input backed up before a quota failure", later = "Latest visual input retained while the backup is unavailable";
  proof.intentions.push({ before_revision: proof.initial.revision, first, later, fault: "localStorage visual writes throw QuotaExceededError" });
  await field(page, "Visual system name").fill(first);
  const originalCopy = await activeVisualCopy(page, proof.initial);
  const initialGrants = proof.service.transcript.filter(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === "issue_effect_grant").length;
  await storageFault(page, "all_visual_writes");
  await visualCommit(page).click();
  await expect(backupWarning(page)).toContainText("quota failure");
  await expect(panel(page).getByRole("alert").filter({ hasText: "Nothing was sent" })).toBeVisible();
  expect(applied(proof.service)).toHaveLength(0);
  expect(proof.service.transcript.filter(entry => entry.direction === "request" &&
    "command" in entry.payload && entry.payload.command === "issue_effect_grant")).toHaveLength(initialGrants);
  expect(await proof.service.project(proof.initial.id)).toEqual(proof.initial);
  expect(await page.evaluate(key => localStorage.getItem(key), originalCopy.key)).toBe(originalCopy.raw);
  await field(page, "Visual system name").fill(later);
  await button(page, "Brief").click(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(backupWarning(page)).toBeVisible();
  await expect(visualCommit(page)).toBeDisabled();
  await storageFault(page, "off");
  await panel(page).getByRole("button", { name: "Retry visual draft backup", exact: true }).click();
  await expect(backupWarning(page)).toHaveCount(0);
  const repaired = await activeVisualCopy(page, proof.initial);
  expect(repaired.value.draft?.fields.name.raw).toBe(later);
  expect(repaired.value.draft?.awaiting).toBeNull();
  expect(applied(proof.service)).toHaveLength(0);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await visualCommit(page).click();
  const saved = await currentAfterUi(proof, proof.initial.revision + 1, "recovered visual input deliberately saved after quota recovery and reload");
  expect(saved.visual_language).toEqual({ ...proof.initial.visual_language,
    version: proof.initial.visual_language.version + 1, name: later });
  expect(applied(proof.service)).toHaveLength(1);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(visualCommit(page)).toBeDisabled();
  proof.observations.push({ quota_prevented_dispatch: true, original_copy_sha256: digest(Buffer.from(originalCopy.raw)),
    repaired_copy_sha256: digest(Buffer.from(repaired.raw)), saved_revision: saved.revision });
}));

test("changed peer recovery bytes cannot be overwritten or removed and each window retains its own editable intent", async ({ page, context }, info) => scenario(page, info, async proof => {
  const peer = await proof.service.peer(); proof.services.push(peer);
  const second = await context.newPage(); proof.extraPages.push(second);
  const originalName = "First window visual input kept as its own recovery copy";
  const externalName = "A peer recovery update whose exact bytes must remain intact";
  const later = "Later first-window intent retained after its old copy changed";
  proof.intentions.push({ before_revision: proof.initial.revision, originalName, externalName, later,
    fault: "valid recovery bytes changed externally after both windows observed the original copy; no Project is fabricated" });
  await field(page, "Visual system name").fill(originalName);
  const original = await activeVisualCopy(page, proof.initial);
  await peer.attachPage(second, proof.initial.id); await second.goto("/"); await openCanvas(second);
  await expect(field(second, "Visual system name")).toHaveValue(proof.initial.visual_language.name);
  await expect(visualCommit(second)).toBeDisabled();
  await panel(second).locator("summary").filter({ hasText: "Local visual recovery copies" }).click();
  await panel(second).getByRole("button", { name: "Restore visual draft " + original.value.writerId, exact: true }).click();
  await expect(field(second, "Visual system name")).toHaveValue(originalName);
  const restored = await activeVisualCopy(second, proof.initial);
  expect(restored.value.writerId).not.toBe(original.value.writerId);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  const externalRaw = await second.evaluate(({ key, previous, name }) => {
    if (localStorage.getItem(key) !== previous) throw new Error("The arranged original recovery copy changed too early");
    const copy = JSON.parse(previous) as VisualRecovery;
    if (!copy.draft || copy.draft.awaiting) throw new Error("This negative case requires an idle valid recovery copy");
    copy.draft.fields.name.raw = name; copy.draft.fields.name.sequence += 1; copy.updatedAt += 1;
    const raw = JSON.stringify(copy); localStorage.setItem(key, raw);
    if (localStorage.getItem(key) !== raw) throw new Error("The external recovery update could not be arranged");
    return raw;
  }, { key: original.key, previous: original.raw, name: externalName });
  await field(page, "Visual system name").fill(later);
  await expect(backupWarning(page)).toContainText("changed in another window");
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(visualCommit(page)).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(externalRaw);
  expect(applied(proof.service)).toHaveLength(0); expect(applied(peer)).toHaveLength(0);
  const remove = panel(second).getByRole("button", { name: "Remove visual draft " + original.value.writerId, exact: true });
  await remove.focus(); await remove.press("Enter");
  const removal = panel(second).getByRole("group", { name: "Remove local visual copy", exact: true });
  await expect(removal.getByRole("button", { name: "Keep visual copy", exact: true })).toBeFocused();
  await second.keyboard.press("Tab");
  await expect(removal.getByRole("button", { name: "Confirm remove visual copy", exact: true })).toBeFocused();
  await second.keyboard.press("Enter");
  await expect(panel(second).getByRole("alert").filter({ hasText: "changed after it was shown" })).toBeVisible();
  expect(await second.evaluate(key => localStorage.getItem(key), original.key)).toBe(externalRaw);
  await second.keyboard.press("Escape");
  await expect(removal).toHaveCount(0); await expect(remove).toBeFocused();
  await panel(page).getByRole("button", { name: "Keep visual input in a new recovery copy", exact: true }).click();
  await expect(backupWarning(page)).toHaveCount(0);
  const forked = await activeVisualCopy(page, proof.initial);
  expect(forked.value.writerId).not.toBe(original.value.writerId);
  expect(forked.value.writerId).not.toBe(restored.value.writerId);
  expect(forked.value.draft?.fields.name.raw).toBe(later);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(externalRaw);
  await visualCommit(second).click(); await revision(second, proof.initial.revision + 1);
  const saved = await peer.project(proof.initial.id);
  await checkpoint(proof, "separately restored peer input deliberately saved without replacing either pending copy", saved);
  expect(saved.visual_language.name).toBe(originalName);
  await reopenSaved(page, saved);
  await expect(field(page, "Visual system name")).toHaveValue(later);
  await expect(panel(page).getByRole("region", { name: "Visual conflict: Visual language name", exact: true })).toBeVisible();
  await expect(visualCommit(page)).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(externalRaw);
  expect(applied(proof.service)).toHaveLength(0); expect(applied(peer)).toHaveLength(1);
  proof.observations.push({ protected_original_key: original.key, protected_raw_sha256: digest(Buffer.from(externalRaw)),
    independently_restored_writer: restored.value.writerId, first_window_forked_writer: forked.value.writerId });
}));

test("keyboard Reset and Keep preserve the old copy and a failed clean checkpoint can recover before a clean reopen", async ({ page }, info) => scenario(page, info, async proof => {
  const name = "Visual draft retained for recovery when the author resets the working input";
  proof.intentions.push({ before_revision: proof.initial.revision, name, fault: "only new clean recovery checkpoints fail with quota" });
  await field(page, "Visual system name").fill(name);
  const original = await activeVisualCopy(page, proof.initial);
  const reset = panel(page).getByRole("button", { name: "Reset", exact: true });
  const confirmation = panel(page).getByRole("group", { name: "Reset local visual input", exact: true });
  await reset.focus(); await reset.press("Enter");
  await expect(confirmation.getByRole("button", { name: "Keep visual input", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(confirmation).toHaveCount(0); await expect(reset).toBeFocused();
  await expect(field(page, "Visual system name")).toHaveValue(name);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  await reset.press("Enter"); await page.keyboard.press("Escape");
  await expect(confirmation).toHaveCount(0); await expect(reset).toBeFocused();
  await storageFault(page, "clean_visual_writes");
  await reset.press("Enter"); await page.keyboard.press("Tab");
  await expect(confirmation.getByRole("button", { name: "Reset to saved visual system", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(confirmation).toHaveCount(0);
  await expect(field(page, "Visual system name")).toBeFocused();
  await expect(field(page, "Visual system name")).toHaveValue(proof.initial.visual_language.name);
  await expect(backupWarning(page)).toContainText("quota failure");
  const protectedRaw = await page.evaluate(key => localStorage.getItem(key), original.key);
  expect(protectedRaw).not.toBeNull();
  expect((JSON.parse(protectedRaw!) as VisualRecovery).draft?.fields).toEqual(original.value.draft?.fields);
  expect(applied(proof.service)).toHaveLength(0);
  expect(await proof.service.project(proof.initial.id)).toEqual(proof.initial);
  await storageFault(page, "off");
  await panel(page).getByRole("button", { name: "Retry visual draft backup", exact: true }).click();
  await expect(backupWarning(page)).toHaveCount(0);
  const clean = await activeVisualCopy(page, proof.initial);
  expect(clean.value.draft).toBeNull(); expect(clean.value.writerId).not.toBe(original.value.writerId);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(protectedRaw);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(proof.initial.visual_language.name);
  await expect(visualCommit(page)).toBeDisabled();
  const recoverySummary = panel(page).locator("summary").filter({ hasText: "Local visual recovery copies" });
  await recoverySummary.focus(); await recoverySummary.press("Enter");
  const remove = panel(page).getByRole("button", { name: "Remove visual draft " + original.value.writerId, exact: true });
  await remove.focus(); await remove.press("Enter");
  const removal = panel(page).getByRole("group", { name: "Remove local visual copy", exact: true });
  await expect(removal.getByRole("button", { name: "Keep visual copy", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(removal).toHaveCount(0); await expect(remove).toBeFocused();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(protectedRaw);
  const afterReset = "New editable visual decision after the clean reset reopened";
  proof.intentions.push({ before_revision: proof.initial.revision, explicit_name_after_clean_reopen: afterReset });
  await field(page, "Visual system name").fill(afterReset); await visualCommit(page).click();
  const saved = await currentAfterUi(proof, proof.initial.revision + 1, "new explicit visual decision after clean reset recovery");
  expect(saved.visual_language.name).toBe(afterReset);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(protectedRaw);
  await page.reload(); await openCanvas(page);
  await expect(field(page, "Visual system name")).toHaveValue(afterReset);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(protectedRaw);
  expect(applied(proof.service)).toHaveLength(1);
  proof.observations.push({ retained_old_copy_sha256: digest(Buffer.from(protectedRaw!)), clean_checkpoint_writer: clean.value.writerId,
    keyboard_cancel_focus_restored: true, reset_recovery_then_reopen_clean: true, saved_revision: saved.revision });
}));

test("a saved ink token changes the actual SVG text paint across revisions and reopen", async ({ page }, info) =>
  scenario(page, info, async proof => {
    const authoredScene = proof.initial.scenes.find(scene =>
      scene.nodes.some(node => node.kind === "text" && !node.parent_id));
    if (!authoredScene) throw new Error("No original root text in this real service Project.");
    const authoredText = authoredScene.nodes.find(node => node.kind === "text" && !node.parent_id)!;
    proof.intentions.push({ concept: "real_project_palette_inherited_ink", sourceNodeId: authoredText.id,
      explicitOriginalFill: authoredText.style.fill, authorInk1: "#F7EAD7", authorInk2: "#D8C2A0",
      authorInkAfterObjectOverride: "#B6D4E2", objectOverride: "#A1B2C3" });
    const textFill = page.getByLabel("Canvas fill", { exact: true });
    await textFill.fill("");
    await button(page, "Commit style").click();
    const noFill = await currentAfterUi(proof, proof.initial.revision + 1, "original text explicit fill removed");
    expect(noFill.scenes.find(scene => scene.id === authoredScene.id)?.nodes.find(node => node.id === authoredText.id)?.style.fill)
      .toBeNull();

    await field(page, "Palette tokens value 1").fill("#F7EAD7");
    await visualCommit(page).click();
    const firstInk = await currentAfterUi(proof, noFill.revision + 1, "authored warm ink committed");
    expect(firstInk.visual_language.palette[0]).toMatchObject({ name: "ink", value: "#F7EAD7" });

    async function beforeTextSvgFill(): Promise<string | null> {
      await button(page, "Production").click();
      await page.getByLabel("Production working scene", { exact: true })
        .selectOption({ value: authoredScene.id });
      await button(page, "Scoped changes").click();
      const svg = page.getByRole("img", { name: "Before scoped changes", exact: true });
      await expect(svg).toBeVisible();
      const text = svg.locator("text").first();
      await expect(text).toBeVisible();
      return text.getAttribute("fill");
    }
    expect(await beforeTextSvgFill()).toBe("#F7EAD7");

    await openCanvas(page);
    await field(page, "Palette tokens value 1").fill("#D8C2A0");
    await visualCommit(page).click();
    const secondInk = await currentAfterUi(proof, firstInk.revision + 1, "second authored ink is saved");
    expect(secondInk.visual_language.palette[0].value).toBe("#D8C2A0");
    expect(await beforeTextSvgFill()).toBe("#D8C2A0");

    await openCanvas(page);
    await page.getByLabel("Canvas fill", { exact: true }).fill("#A1B2C3");
    await button(page, "Commit style").click();
    const overridden = await currentAfterUi(proof, secondInk.revision + 1, "explicit object ink overrides project palette");
    expect(overridden.scenes.find(scene => scene.id === authoredScene.id)?.nodes.find(node => node.id === authoredText.id)?.style.fill)
      .toBe("#A1B2C3");
    expect(await beforeTextSvgFill()).toBe("#A1B2C3");

    await openCanvas(page);
    await field(page, "Palette tokens value 1").fill("#B6D4E2");
    await visualCommit(page).click();
    const final = await currentAfterUi(proof, overridden.revision + 1, "later global palette edit preserves the explicit object ink");
    const expectedVisual = structuredClone(overridden.visual_language);
    expectedVisual.version = overridden.visual_language.version + 1;
    expectedVisual.palette[0].value = "#B6D4E2";
    expect(final).toEqual({ ...overridden, revision: overridden.revision + 1,
      branches: overridden.branches.map(branch => branch.id === overridden.active_branch
        ? { ...branch, head_revision: overridden.revision + 1 } : branch),
      updated_at: final.updated_at, visual_language: expectedVisual });
    expect(final.scenes.find(scene => scene.id === authoredScene.id)?.nodes.find(node => node.id === authoredText.id)?.style.fill)
      .toBe("#A1B2C3");
    expect(await beforeTextSvgFill()).toBe("#A1B2C3");

    const journal = await proof.service.history(proof.initial.id, proof.initial.revision);
    expect(journal.map(event => event.change.type)).toEqual([
      "update_canvas_style", "set_visual_language", "set_visual_language", "update_canvas_style", "set_visual_language",
    ]);
    expect(applied(proof.service)).toHaveLength(5);
    await proof.service.close();
    await proof.service.start();
    await page.reload();
    await reopenSaved(page, final);
    expect(await beforeTextSvgFill()).toBe("#A1B2C3");
    expect(await proof.service.project(proof.initial.id)).toEqual(final);
    expect(await proof.service.history(proof.initial.id, proof.initial.revision)).toEqual(journal);
    expect(applied(proof.service)).toHaveLength(5);
    proof.observations.push({ label: "real_saved_ink_affects_actual_SVG_before_composition",
      project: final.id, revision: final.revision, textNode: authoredText.id,
      projectPalette: final.visual_language.palette[0].value, renderedExplicitInk: "#A1B2C3",
      earlierRenderedInheritedInk: ["#F7EAD7", "#D8C2A0"],
      laterGlobalPalettePreservesObjectOverrideAndAllOtherProjectFields: true,
      postRestartProjectAndJournalEqual: true,
      excludes: ["native_Film_pixels", "installed_fonts", "human_approval"],
    });
  }));
