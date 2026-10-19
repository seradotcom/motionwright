/**
 * Actual Studio UI -> shared desktop import module -> StudioService/SQLite/CAS.
 * Own PCM signals and authored words are test inputs, not recorded narration.
 * A lost reply, delay or damaged local copy is an explicit fault arrangement;
 * no Project, receipt, measurement or service response is manufactured.
 * These gates do not certify installed Tauri, native delivery or human quality.
 */
import { createHash, randomUUID } from "node:crypto";
import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test as base, type Page } from "@playwright/test";
import type { Project } from "../src/types";
import type { FileImportCopy, FileImportReceipt } from "../src/fileImport";
import { FILE_IMPORT_PREFIX } from "../src/fileImport";
import { ScopedChangeService } from "./support/scopedChangeService";

interface ImportCase {
  service: ScopedChangeService;
  peers: ScopedChangeService[];
  project: Project;
  cleanup: Array<() => Promise<unknown>>;
  observations: Record<string, unknown>;
}
const test = base.extend<{ imports: ImportCase }>({
  imports: async ({ page }, use, info) => {
    const service = await ScopedChangeService.create({ databasePath: info.outputPath("store", "studio.sqlite3"), desktopInvokeFixture: true });
    const peers: ScopedChangeService[] = [], cleanup: Array<() => Promise<unknown>> = [];
    const observations: Record<string, unknown> = { behaviorAssertionsCompleted: false };
    let project: Project | undefined;
    let finalization: unknown = null;
    let finalizationError: string | null = null;
    try {
      const boot = await service.fixtureBootstrap();
      expect(boot.test_transport).toBe("service-test-jsonl");
      project = boot.project;
      await service.attachPage(page, project.id);
      await use({ service, peers, project, cleanup, observations });
    } finally {
      try {
        const settled = await Promise.allSettled(cleanup.map(action => action()));
        for (const result of settled) if (result.status === "rejected") throw result.reason;
        const current = project ? await service.project(project.id) : undefined;
        finalization = await service.finalizeClosedStore(info.outputPath("closed-store"), current, peers, [() => page.close()]);
      } catch (error) {
        finalizationError = String(error);
        observations.fallbackClose = await Promise.allSettled([service, ...peers].map(peer => peer.close()));
        throw error;
      } finally {
        const path = info.outputPath("file-import-evidence.json");
        await writeFile(path, JSON.stringify({
          scope: "real_service_sqlite_cas_and_browser_import_recovery", inputs: "synthetic_pcm_and_authored_test_inputs",
          excludes: ["installed_tauri", "native_film_or_audio_delivery", "human_acceptance"],
          statusAtTeardown: info.status, observations, finalization, finalizationError,
          services: [service, ...peers].map(peer => ({ binary: peer.binaryPath, transcript: peer.transcript,
            exits: peer.processExits, taints: peer.lifecycleTaints, stderr: peer.stderr })),
        }, null, 2));
        await info.attach("file-import-evidence", { path, contentType: "application/json" });
      }
    }
  },
});
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build the declared studio_review_session composition and set its absolute path; no fallback service.");
test.describe.configure({ timeout: 90_000 });

const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByRole("textbox", { name, exact: true });
const recovery = (page: Page) => page.locator(".file-import-recovery");
const revision = (page: Page, value: number) => expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);

function pcm(frequency = 240): Buffer {
  const sampleRate = 48_000, frames = sampleRate * 2, channels = 2;
  const bytes = frames * channels * 2;
  const result = Buffer.alloc(44 + bytes);
  result.write("RIFF", 0); result.writeUInt32LE(36 + bytes, 4); result.write("WAVEfmt ", 8);
  result.writeUInt32LE(16, 16); result.writeUInt16LE(1, 20); result.writeUInt16LE(channels, 22);
  result.writeUInt32LE(sampleRate, 24); result.writeUInt32LE(sampleRate * channels * 2, 28);
  result.writeUInt16LE(channels * 2, 32); result.writeUInt16LE(16, 34); result.write("data", 36); result.writeUInt32LE(bytes, 40);
  for (let frame = 0; frame < frames; frame++) {
    result.writeInt16LE(Math.round(8_000 * Math.sin(2 * Math.PI * frequency * frame / sampleRate)), 44 + frame * 4);
    result.writeInt16LE(Math.round(5_000 * Math.sin(2 * Math.PI * (frequency + 41) * frame / sampleRate)), 46 + frame * 4);
  }
  return result;
}

async function copies(page: Page, projectId: string): Promise<Array<{ key: string; raw: string; copy: FileImportCopy | null }>> {
  return page.evaluate(({ prefix, projectId }) => Object.keys(localStorage).filter(key =>
    key === prefix + projectId || key.startsWith(prefix + projectId + ":")).sort().map(key => {
      const raw = localStorage.getItem(key)!;
      try { return { key, raw, copy: JSON.parse(raw) }; } catch { return { key, raw, copy: null }; }
    }), { prefix: FILE_IMPORT_PREFIX, projectId });
}

function requests(service: ScopedChangeService, command?: string) {
  return service.transcript.flatMap(row => row.direction === "request" && "command" in row.payload
    && (!command || row.payload.command === command) ? [row.payload] : []);
}
function importGrants(service: ScopedChangeService) {
  return requests(service, "issue_effect_grant").filter(row => (row.args?.request as { effect?: string })?.effect === "import_local");
}

/** The real operation completes; only delivery of its acknowledgement fails. */
function loseImportReply(imports: ImportCase, command: "import_asset_file" | "import_voice_file") {
  const original = imports.service.invoke.bind(imports.service);
  let armed = true;
  let observed: FileImportReceipt | undefined;
  imports.service.invoke = async <T>(name: string, args: Record<string, unknown> = {}): Promise<T> => {
    const value = await original<T>(name, args);
    if (name === command && armed) {
      armed = false;
      observed = structuredClone(value as FileImportReceipt);
      throw new Error("Injected test fault: import acknowledgement lost after commit");
    }
    return value;
  };
  imports.cleanup.push(async () => { imports.service.invoke = original; });
  return () => observed;
}

async function denyRecoveryCleanup(page: Page) {
  await page.addInitScript(prefix => {
    const remove = Storage.prototype.removeItem;
    const fault = window as typeof window & { __denyImportCleanup: boolean };
    fault.__denyImportCleanup = true;
    Storage.prototype.removeItem = function (key: string) {
      if (key.startsWith(prefix) && fault.__denyImportCleanup) throw new DOMException("Injected test fault: recovery cleanup denied", "SecurityError");
      return remove.call(this, key);
    };
  }, FILE_IMPORT_PREFIX);
}

/** Persist the real import, then delay only its acknowledgement to the view. */
function holdImportReply(imports: ImportCase) {
  const original = imports.service.invoke.bind(imports.service);
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  let observe!: (receipt: FileImportReceipt) => void;
  const committed = new Promise<FileImportReceipt>(resolve => { observe = resolve; });
  let armed = true;
  imports.service.invoke = async <T>(name: string, args: Record<string, unknown> = {}): Promise<T> => {
    const value = await original<T>(name, args);
    if (name === "import_voice_file" && armed) {
      armed = false; observe(structuredClone(value as FileImportReceipt)); await waiting;
    }
    return value;
  };
  imports.cleanup.push(async () => { release(); imports.service.invoke = original; });
  return { committed, release };
}

async function audio(page: Page) {
  await button(page, "Audio").click();
  await expect(page.getByRole("region", { name: "Audio workspace", exact: true })).toBeVisible();
}
async function importVoiceUi(page: Page, path: string, label: string) {
  await field(page, "Voice file absolute path").fill(path);
  await field(page, "Voice take label").fill(label);
  await button(page, "Import + measure").click();
}

test("lost voice acknowledgement preserves ten later edits, then two takes remain editable across reopen", async ({ page, imports }, info) => {
  const { service, project, peers, observations } = imports;
  const path = info.outputPath("owned-synthetic-pcm-take-A.wav"), bytes = pcm();
  await writeFile(path, bytes);
  const outcome = loseImportReply(imports, "import_voice_file");
  await page.goto("/"); await audio(page);
  await importVoiceUi(page, path, "Synthetic PCM take A");
  await expect(page.getByText("Injected test fault: import acknowledgement lost after commit", { exact: true })).toBeVisible();
  await expect(recovery(page)).toContainText("An import is awaiting confirmation");
  const [original] = await copies(page, project.id);
  expect(original.copy?.preparation.revision).toBe(project.revision);
  expect(original.copy?.input.path).toBe(path);
  expect(original.raw).not.toContain("effect_grant");
  const committed = await service.project(project.id);
  expect(committed.revision).toBe(project.revision + 1);
  const takeA = committed.audio.voice_tracks.find(track => track.label === "Synthetic PCM take A")!;
  expect(takeA).toMatchObject({ sample_rate_hz: 48_000, channels: 2, measured_duration: { num: "2", den: "1" },
    source_sha256: createHash("sha256").update(bytes).digest("hex") });
  expect(outcome()?.project).toEqual(committed);
  await rm(path); // Recovery cannot depend on the original file.
  const peer = await service.peer(); peers.push(peer);
  let later = committed;
  for (let index = 0; index < 10; index++) {
    later = await peer.apply(later, { type: "set_narrative_premise", premise: "Preserved editorial revision " + (index + 1) });
  }
  expect(later.revision).toBe(committed.revision + 10);
  await peer.close(); await service.close(); await service.start();
  await page.reload(); await audio(page); await revision(page, later.revision);
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  const beforeRecovery = { imports: requests(service, "import_voice_file").length, grants: importGrants(service).length };
  await button(page, "Check original import").focus();
  const playhead = await page.locator(".timeline-header .timecode").innerText();
  await button(page, "Check original import").press("Space");
  await expect(recovery(page)).toContainText("The latest saved project is open");
  await revision(page, later.revision);
  expect(await service.project(project.id)).toEqual(later);
  expect(await copies(page, project.id)).toEqual([]);
  expect(requests(service, "import_voice_file")).toHaveLength(beforeRecovery.imports);
  expect(importGrants(service)).toHaveLength(beforeRecovery.grants);
  const recoverRequest = requests(service, "recover_file_import").at(-1)?.args?.request as Record<string, unknown>;
  expect(recoverRequest).toMatchObject({ request_id: original.copy!.request_id, revision: project.revision,
    project_id: project.id, generation: project.generation, intent: original.copy!.preparation.intent });
  expect(Object.keys(recoverRequest).sort()).toEqual(["generation", "intent", "project_id", "request_id", "revision"]);
  expect(await page.locator(".timeline-header .timecode").innerText()).toBe(playhead);
  await expect(page.getByRole("img", { name: /Measured sample-peak waveform page/ })).toBeVisible();

  await field(page, "New transcript start").fill("0.125");
  await field(page, "New transcript end").fill("1.750");
  await field(page, "New transcript speaker").fill("Authored test label, no recorded speaker");
  await field(page, "New transcript text").fill("A manually reviewed creative line.");
  await button(page, "Add manual segment").click(); await revision(page, later.revision + 1);
  await field(page, "New cue time").fill("0.500");
  await field(page, "New cue label").fill("An independent editorial cue");
  await button(page, "Add cue").click(); await revision(page, later.revision + 2);
  const authored = await service.project(project.id);
  const segment = authored.audio.transcript.at(-1)!;
  expect(segment).toMatchObject({ text: "A manually reviewed creative line.", voice_track_id: takeA.id,
    start: { num: "1", den: "8" }, end: { num: "7", den: "4" }, alignment: { kind: "manual" } });

  const pathB = info.outputPath("owned-synthetic-pcm-take-B.wav"); await writeFile(pathB, pcm(390));
  await importVoiceUi(page, pathB, "Synthetic PCM take B"); await revision(page, authored.revision + 1);
  let final = await service.project(project.id);
  const takeB = final.audio.voice_tracks.find(track => track.label === "Synthetic PCM take B")!;
  expect(takeB.id).not.toBe(takeA.id); expect(takeB.source_sha256).not.toBe(takeA.source_sha256);
  expect(final.audio.voice_tracks.find(track => track.id === takeA.id)).toEqual(takeA);
  expect(final.audio.transcript).toEqual(authored.audio.transcript); expect(final.audio.cues).toEqual(authored.audio.cues);
  expect(await copies(page, project.id)).toEqual([]);
  const activeLane = page.locator(".timeline-panel .voice-track .audio-presence.measured");
  await expect(activeLane).toHaveCount(1);
  await expect(activeLane).toHaveAttribute("aria-label", "Measured voice track " + takeB.label);
  // Selecting another measured take is a real new project revision, not a local selection mock.
  const selectId = final.audio.active_voice_track_id === takeA.id ? takeB : takeA;
  await page.getByRole("button", { name: new RegExp(selectId.label) }).click();
  await revision(page, final.revision + 1); final = await service.project(project.id);
  expect(final.audio.active_voice_track_id).toBe(selectId.id);
  await expect(activeLane).toHaveCount(1);
  await expect(activeLane).toHaveAttribute("aria-label", "Measured voice track " + selectId.label);
  for (const next of [takeB, takeA]) {
    await page.getByRole("button", { name: new RegExp(next.label) }).click();
    await revision(page, final.revision + 1); final = await service.project(project.id);
    expect(final.audio.active_voice_track_id).toBe(next.id);
    await expect(activeLane).toHaveCount(1);
    await expect(activeLane).toHaveAttribute("aria-label", "Measured voice track " + next.label);
    expect(final.audio.transcript).toEqual(authored.audio.transcript);
    expect(final.audio.cues).toEqual(authored.audio.cues);
  }
  await service.close(); await service.start(); await page.reload(); await audio(page);
  expect(await service.project(project.id)).toEqual(final);
  await expect(page.getByRole("complementary", { name: "Voice takes" })).toContainText("Synthetic PCM take A");
  await expect(page.getByRole("complementary", { name: "Voice takes" })).toContainText("Synthetic PCM take B");
  await expect(activeLane).toHaveCount(1);
  await expect(activeLane).toHaveAttribute("aria-label", "Measured voice track " + takeA.label);
  await page.screenshot({ path: info.outputPath("two-measured-takes-and-preserved-authored-cues.png"), fullPage: true });
  observations.originalReceipt = outcome(); observations.originalCopy = original;
  observations.finalProject = final; observations.behaviorAssertionsCompleted = true;
});

test("lost asset acknowledgement recovers its original bytes and identity after later canvas and narrative revisions", async ({ page, imports }, info) => {
  const { service, project, peers, observations } = imports;
  const source = info.outputPath("owned-directorial-reference.svg");
  const bytes = '<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"><title>Owned test reference</title><rect width="160" height="90" fill="#213847"/><circle cx="80" cy="45" r="24" fill="#efc070"/></svg>';
  await writeFile(source, bytes); const observed = loseImportReply(imports, "import_asset_file");
  await page.goto("/"); await button(page, "Import local asset").click();
  await field(page, "Local asset path").fill(source); await button(page, "Import").click();
  await expect(recovery(page)).toContainText("owned-directorial-reference.svg");
  await expect(page.locator(".asset-import-error")).toContainText("acknowledgement lost");
  const [original] = await copies(page, project.id);
  const imported = await service.project(project.id);
  const asset = imported.assets.find(item => item.name === "owned-directorial-reference.svg")!;
  expect(asset.content_sha256).toBe(createHash("sha256").update(bytes).digest("hex"));
  await rm(source);
  const peer = await service.peer(); peers.push(peer);
  let current = await peer.apply(imported, { type: "set_narrative_premise", premise: "A later director's saved premise" });
  const scene = current.scenes[0], node = scene.nodes.find(item => item.kind === "text")!;
  current = await peer.apply(current, { type: "update_canvas_text", scene_id: scene.id, node_id: node.id, text: "A later human-authored title" });
  current = await peer.apply(current, { type: "set_node_property_lock", scene_id: scene.id, node_id: node.id, property: "text", locked: true });
  await page.reload(); await revision(page, current.revision);
  const grants = importGrants(service).length, dispatches = requests(service, "import_asset_file").length;
  await button(page, "Check original import").click(); await expect(recovery(page)).toContainText("confirmed");
  await expect(recovery(page).getByRole("status")).toBeInViewport({ ratio: 1 });
  expect(await service.project(project.id)).toEqual(current);
  expect(await copies(page, project.id)).toEqual([]);
  expect(requests(service, "import_asset_file")).toHaveLength(dispatches); expect(importGrants(service)).toHaveLength(grants);
  expect(observed()?.project.revision).toBe(imported.revision);
  await button(page, "Canvas").click();
  await expect(page.locator(".asset-list")).toContainText(asset.name);
  await expect(recovery(page).getByRole("heading")).toBeInViewport({ ratio: 1 });
  await expect(recovery(page).getByRole("status")).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: info.outputPath("asset-recovered-with-later-canvas-decisions.png"), fullPage: true });
  observations.originalCopy = original; observations.finalProject = current; observations.behaviorAssertionsCompleted = true;
});

test("invalid audio has no partial project mutation and a read-only missing receipt never triggers a retry", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  const source = info.outputPath("not-a-real-audio-stream.wav"); await writeFile(source, "This file is not an audio stream.");
  await page.goto("/"); await audio(page); await importVoiceUi(page, source, "Invalid test input");
  await expect(page.getByText("Voice import blocked.", { exact: true })).toBeVisible();
  await expect(recovery(page)).toContainText("awaiting confirmation");
  expect(await service.project(project.id)).toEqual(project);
  expect(await service.history(project.id, project.revision)).toEqual([]);
  const [original] = await copies(page, project.id);
  const grants = importGrants(service).length, dispatched = requests(service, "import_voice_file").length;
  await rm(source); await button(page, "Check original import").click();
  await expect(recovery(page)).toContainText("The original import was not found");
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  expect(importGrants(service)).toHaveLength(grants); expect(requests(service, "import_voice_file")).toHaveLength(dispatched);
  const downloadPromise = page.waitForEvent("download"); await button(page, "Download recovery copy").click();
  const download = await downloadPromise; const saved = info.outputPath("invalid-source-recovery-download.json"); await download.saveAs(saved);
  expect(JSON.parse(await readFile(saved, "utf8")).raw).toBe(original.raw);
  await button(page, "Remove copy…").click(); await expect(button(page, "Remove local recovery copy")).toBeFocused();
  // Escape is a cancellation, never authorization to discard the only recovery
  // copy. Keep keyboard focus on the invoking action so Space cannot leak to
  // the global timeline transport while the confirmation closes.
  await page.keyboard.press("Escape");
  await expect(button(page, "Remove local recovery copy")).toHaveCount(0);
  await expect(button(page, "Remove copy…")).toBeFocused();
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  await button(page, "Remove copy…").click();
  await button(page, "Keep copy").click(); expect(await copies(page, project.id)).toHaveLength(1);
  await expect(button(page, "Remove copy…")).toBeFocused();
  await button(page, "Remove copy…").click(); await button(page, "Remove local recovery copy").click();
  expect(await copies(page, project.id)).toEqual([]); expect(await service.project(project.id)).toEqual(project);
  await expect(recovery(page).getByRole("heading", { name: "Import recovery", exact: true })).toBeFocused();
  const time = await page.locator(".timeline-header .timecode").innerText();
  await page.keyboard.press("Space");
  expect(await page.locator(".timeline-header .timecode").innerText()).toBe(time);
  await button(page, "Dismiss").focus(); await button(page, "Dismiss").press("Space");
  await expect(recovery(page)).toHaveCount(0);
  await expect(button(page, "Audio")).toBeFocused();
  await page.keyboard.press("Space");
  await expect(button(page, "Play design preview")).toBeVisible();
  expect(await page.locator(".timeline-header .timecode").innerText()).toBe(time);
  // Space still activates an explicitly focused transport button once.
  await button(page, "Play design preview").focus(); await page.keyboard.press("Space");
  await expect(button(page, "Pause design preview")).toBeVisible();
  await page.keyboard.press("Space"); await expect(button(page, "Play design preview")).toBeVisible();
  observations.originalCopy = original; observations.behaviorAssertionsCompleted = true;
});

test("a failed local backup stops import before grants, while relative paths fail before any retention", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  await page.addInitScript(prefix => {
    const set = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key.startsWith(prefix)) throw new DOMException("Injected test fault: recovery storage quota exceeded", "QuotaExceededError");
      return set.call(this, key, value);
    };
  }, FILE_IMPORT_PREFIX);
  await page.goto("/"); await audio(page);
  await importVoiceUi(page, "relative-file.wav", "Relative test input");
  await expect(page.getByRole("region", { name: "Audio workspace", exact: true }).getByRole("alert"))
    .toContainText("Asset file paths must be absolute.");
  expect(await copies(page, project.id)).toEqual([]);
  const source = info.outputPath("owned-backup-quota-test.wav"); await writeFile(source, pcm());
  await importVoiceUi(page, source, "Quota test input");
  await expect(page.getByRole("region", { name: "Audio workspace", exact: true }).getByRole("alert"))
    .toContainText("recovery storage quota exceeded");
  expect(importGrants(service)).toEqual([]); expect(requests(service, "import_voice_file")).toEqual([]);
  expect(await service.project(project.id)).toEqual(project); expect(await copies(page, project.id)).toEqual([]);
  observations.behaviorAssertionsCompleted = true;
});

test("changed intent and a foreign generation remain visible, downloadable and unable to import or recover", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  const source = info.outputPath("owned-still-unconfirmed.wav"); await writeFile(source, pcm());
  loseImportReply(imports, "import_voice_file");
  await page.goto("/"); await audio(page); await importVoiceUi(page, source, "Original retained label");
  await expect(recovery(page)).toContainText("awaiting confirmation");
  const [original] = await copies(page, project.id);
  const current = await service.project(project.id);
  const modified = structuredClone(original.copy!);
  if (modified.preparation.intent.kind !== "voice") throw new Error("Expected measured voice test intent");
  modified.preparation.intent.label = "A mismatched retained label";
  modified.input.label = "A mismatched retained label";
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: JSON.stringify(modified) });
  await page.reload(); await button(page, "Check original import").click();
  await expect(recovery(page).getByRole("alert")).toBeVisible();
  expect(await service.project(project.id)).toEqual(current);
  const foreign = structuredClone(original.copy!); foreign.preparation.generation = randomUUID();
  await page.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: JSON.stringify(foreign) });
  await page.reload();
  await expect(recovery(page)).toContainText("earlier restored version");
  await expect(button(page, "Check original import")).toBeDisabled();
  const calls = requests(service).length;
  await page.setViewportSize({ width: 1024, height: 800 });
  await expect(recovery(page).getByRole("heading")).toBeInViewport({ ratio: 1 });
  await expect(recovery(page).locator("strong")).toBeInViewport({ ratio: 1 });
  const downloadPromise = page.waitForEvent("download"); await button(page, "Download recovery copy").focus();
  await button(page, "Download recovery copy").press("Space");
  const download = await downloadPromise; const saved = info.outputPath("foreign-generation-local-copy.json"); await download.saveAs(saved);
  expect(JSON.parse(await readFile(saved, "utf8")).raw).toBe(JSON.stringify(foreign));
  // Download does not invoke the backend; asynchronous startup observations are drained first.
  expect(requests(service, "import_voice_file")).toHaveLength(1);
  expect(await service.project(project.id)).toEqual(current);
  await page.screenshot({ path: info.outputPath("retained-import-at-1024px.png"), fullPage: true });
  observations.originalCopy = original; observations.callsBeforeDownload = calls; observations.behaviorAssertionsCompleted = true;
});

test("peer recovery copies cannot change a pending removal identity and long details remain keyboard accessible", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  const source = info.outputPath("invalid-peer-arrangement.wav");
  await writeFile(source, "Invalid source used to retain one real rejected import request.");
  await page.goto("/"); await audio(page); await importVoiceUi(page, source, "Request A");
  await expect(page.getByText("Voice import blocked.", { exact: true })).toBeVisible();
  const [original] = await copies(page, project.id);
  expect(original.copy).not.toBeNull();
  await button(page, "Remove copy…").click();
  await expect(button(page, "Remove local recovery copy")).toBeFocused();

  // This second retained copy is an explicit local-storage fault arrangement,
  // modelling another window's pending operation. It is never dispatched and
  // does not manufacture a preparation response, receipt or Project mutation.
  const peerCopy = structuredClone(original.copy!);
  peerCopy.request_id = randomUUID(); peerCopy.created_at += 1000;
  peerCopy.preparation.intent.name = "Peer request with a long path.wav";
  peerCopy.input.name = peerCopy.preparation.intent.name;
  peerCopy.preparation.path = "/" + "editorial-reference-folder/".repeat(60) + "peer.wav";
  peerCopy.input.path = peerCopy.preparation.path;
  const peerKey = FILE_IMPORT_PREFIX + project.id + ":" + peerCopy.request_id;
  const peerRaw = JSON.stringify(peerCopy);
  const otherWindow = await page.context().newPage();
  await service.attachPage(otherWindow, project.id);
  await otherWindow.goto("/");
  imports.cleanup.push(async () => { await otherWindow.close(); });
  await otherWindow.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: peerKey, raw: peerRaw });
  const selector = page.getByRole("combobox", { name: "Retained import request", exact: true });
  await expect(selector).toHaveValue(original.key);
  await expect(recovery(page).locator("strong")).toHaveText(original.copy!.preparation.intent.name);
  await expect(button(page, "Remove local recovery copy")).toBeVisible();
  expect(await copies(page, project.id)).toHaveLength(2);
  await button(page, "Keep copy").click(); await expect(button(page, "Remove copy…")).toBeFocused();
  await button(page, "Remove copy…").press("Space");
  await expect(button(page, "Remove local recovery copy")).toBeFocused();

  // A byte change invalidates an already-open confirmation even when parsed
  // identity is unchanged. The new bytes and the other request both survive.
  const rewritten = original.raw + "\n";
  await otherWindow.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: rewritten });
  await expect(button(page, "Remove local recovery copy")).toHaveCount(0);
  await expect(recovery(page)).toContainText("The saved request changed");
  await expect(recovery(page).getByRole("heading")).toBeFocused();
  expect((await copies(page, project.id)).find(item => item.key === original.key)?.raw).toBe(rewritten);
  expect((await copies(page, project.id)).find(item => item.key === peerKey)?.raw).toBe(peerRaw);

  await selector.selectOption(peerKey);
  await page.setViewportSize({ width: 1024, height: 800 });
  await recovery(page).getByText("Original import details", { exact: true }).click();
  const bounds = await recovery(page).boundingBox();
  expect(bounds!.height).toBeGreaterThanOrEqual(160);
  expect(bounds!.height).toBeLessThanOrEqual(360);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("long-retained-path-at-1024px.png"), fullPage: true });
  const time = await page.locator(".timeline-header .timecode").innerText();
  const downloaded = page.waitForEvent("download");
  await button(page, "Download recovery copy").focus();
  await expect(button(page, "Download recovery copy")).toBeInViewport();
  await button(page, "Download recovery copy").press("Space");
  const download = await downloaded, saved = info.outputPath("peer-copy-download.json"); await download.saveAs(saved);
  expect(JSON.parse(await readFile(saved, "utf8")).raw).toBe(peerRaw);
  expect(await page.locator(".timeline-header .timecode").innerText()).toBe(time);
  await page.screenshot({ path: info.outputPath("long-retained-path-keyboard-actions-at-1024px.png"), fullPage: true });
  await button(page, "Remove copy…").click(); await button(page, "Remove local recovery copy").press("Space");
  await expect(recovery(page).getByRole("heading")).toBeFocused();
  expect((await copies(page, project.id)).map(item => ({ key: item.key, raw: item.raw }))).toEqual([{ key: original.key, raw: rewritten }]);
  await expect(recovery(page).locator("strong")).toHaveText(original.copy!.preparation.intent.name);
  expect(requests(service, "import_voice_file")).toHaveLength(1);
  expect(importGrants(service)).toHaveLength(1);
  expect(requests(service, "recover_file_import")).toEqual([]);
  expect(await service.project(project.id)).toEqual(project);
  observations.localStorageFaultArrangement = { original, peerKey, peerRaw, rewritten };
  observations.behaviorAssertionsCompleted = true;
});

test("real portable handoff preserves authored audio, isolates an old import request and admits a new take after explicit review", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  const source = info.outputPath("portable-owned-pcm-A.wav"); await writeFile(source, pcm());
  loseImportReply(imports, "import_voice_file");
  await page.goto("/"); await audio(page); await importVoiceUi(page, source, "Portable measured take A");
  await expect(recovery(page)).toContainText("awaiting confirmation");
  const [originalCopy] = await copies(page, project.id);
  await rm(source); await page.reload(); await audio(page);
  let donor = await service.project(project.id);
  const takeA = donor.audio.voice_tracks.find(track => track.label === "Portable measured take A")!;
  await field(page, "New transcript start").fill("0.125");
  await field(page, "New transcript end").fill("1.750");
  await field(page, "New transcript text").fill("An authored line that travels with its measured take.");
  await button(page, "Add manual segment").click(); await revision(page, donor.revision + 1);
  await field(page, "New cue time").fill("0.500");
  await field(page, "New cue label").fill("Preserve this editorial cue through handoff");
  await button(page, "Add cue").click(); await revision(page, donor.revision + 2);
  donor = await service.project(project.id);
  const donorHistory = await service.observeStoredProject(donor);
  const bundle = info.outputPath("portable-import-continuity.motionwright");
  await button(page, "Deliver").click(); await field(page, "Portable export path").fill(bundle);
  await button(page, "Export bundle").click();
  await expect(page.locator(".portable-project .portable-message")).toContainText("Portable bundle written to");
  const manifestBytes = await readFile(join(bundle, "manifest.json"));
  const manifest = JSON.parse(manifestBytes.toString()) as {
    backup: { project: Project }; blobs: { relative_path: string; sha256: string; size_bytes: number }[];
  };
  expect(manifest.backup.project).toEqual(donor);
  for (const blob of manifest.blobs) {
    const bytes = await readFile(join(bundle, blob.relative_path));
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(blob.sha256);
    expect(bytes.length).toBe(blob.size_bytes);
  }
  expect((await copies(page, project.id))[0].raw).toBe(originalCopy.raw);
  await page.close();

  // A fresh canonical store is opened with the same local browser recovery
  // data, modelling a store handoff while local recovery remains. Both stores
  // and all original/imported projects are captured separately and unchanged.
  const target = await ScopedChangeService.create({ databasePath: info.outputPath("target-store", "studio.sqlite3"), desktopInvokeFixture: true });
  const targetOriginal = (await target.fixtureBootstrap()).project;
  const targetOriginalOracle = await target.observeStoredProject(targetOriginal);
  const targetPage = await page.context().newPage();
  let restored: Project | undefined;
  try {
    await target.attachPage(targetPage, targetOriginal.id);
    await targetPage.goto("/");
    // The remembered source project is absent from this store. Select the
    // actual target project through Library without erasing recovery data.
    await expect(targetPage.getByRole("alert", { name: "Project open error" })).toContainText("project not found");
    await expect(targetPage.getByRole("heading", { name: "Project library", exact: true })).toBeVisible();
    await button(targetPage, "Open " + targetOriginal.title).click();
    await revision(targetPage, targetOriginal.revision);
    await button(targetPage, "Deliver").click();
    expect((await copies(targetPage, project.id))[0].raw).toBe(originalCopy.raw);
    await field(targetPage, "Portable import path").fill(bundle);
    await button(targetPage, "Inspect bundle").click();
    await expect(targetPage.getByLabel("Portable bundle preflight", { exact: true })).toContainText(donor.title);
    await button(targetPage, "Import verified bundle").click();
    await expect(recovery(targetPage)).toContainText("earlier restored version");
    restored = await target.project(donor.id);
    expect(restored.generation).not.toBe(donor.generation);
    expect(restored).toEqual({ ...donor, generation: restored.generation, updated_at: restored.updated_at });
    expect((await target.observeStoredProject(restored)).journal).toEqual(donorHistory.journal);
    await expect(button(targetPage, "Check original import")).toBeDisabled();
    expect((await copies(targetPage, project.id))[0].raw).toBe(originalCopy.raw);
    const downloadPromise = targetPage.waitForEvent("download");
    await button(targetPage, "Download recovery copy").click();
    const saved = info.outputPath("real-old-generation-recovery-copy.json");
    await (await downloadPromise).saveAs(saved);
    expect(JSON.parse(await readFile(saved, "utf8")).raw).toBe(originalCopy.raw);
    await button(targetPage, "Brief").click();
    await field(targetPage, "Project title").fill("Edited after real portable handoff");
    await button(targetPage, "Save title").click(); await revision(targetPage, restored.revision + 1);
    restored = await target.project(donor.id);
    expect(restored.audio).toEqual(donor.audio);
    await target.close(); await target.start(); await targetPage.reload(); await audio(targetPage);
    await expect(recovery(targetPage)).toContainText("earlier restored version");
    await expect(button(targetPage, "Check original import")).toBeDisabled();
    const secondPath = info.outputPath("portable-owned-pcm-B.wav"); await writeFile(secondPath, pcm(390));
    await importVoiceUi(targetPage, secondPath, "Portable measured take B");
    await expect(targetPage.getByText("Voice import blocked.", { exact: true })).toBeVisible();
    expect(requests(target, "import_voice_file")).toEqual([]);
    expect(await target.project(donor.id)).toEqual(restored);
    expect((await copies(targetPage, project.id))[0].raw).toBe(originalCopy.raw);
    await button(targetPage, "Remove copy…").click(); await button(targetPage, "Remove local recovery copy").click();
    expect(await copies(targetPage, project.id)).toEqual([]);
    await importVoiceUi(targetPage, secondPath, "Portable measured take B"); await revision(targetPage, restored.revision + 1);
    restored = await target.project(donor.id);
    expect(restored.audio.voice_tracks.find(track => track.id === takeA.id)).toEqual(takeA);
    expect(restored.audio.transcript).toEqual(donor.audio.transcript);
    expect(restored.audio.cues).toEqual(donor.audio.cues);
    expect(restored.audio.voice_tracks).toHaveLength(2);
    expect(requests(target, "recover_file_import")).toEqual([]);
    const [request] = requests(target, "import_voice_file").map(row => row.args!.request as Record<string, unknown>);
    expect(request.generation).toBe(restored.generation);
    expect(request.request_id).not.toBe(originalCopy.copy!.request_id);
    await target.close(); await target.start(); await targetPage.reload(); await audio(targetPage);
    expect(await target.project(restored.id)).toEqual(restored);
    await expect(targetPage.getByRole("complementary", { name: "Voice takes" })).toContainText("Portable measured take A");
    await expect(targetPage.getByRole("complementary", { name: "Voice takes" })).toContainText("Portable measured take B");
    await expect(targetPage.getByRole("img", { name: /Measured sample-peak waveform page/ })).toBeVisible();
    await targetPage.screenshot({ path: info.outputPath("portable-two-takes-authored-cues-reopened.png"), fullPage: true });
    const unchangedTarget = await target.observeStoredProject(targetOriginal);
    expect(unchangedTarget.project).toEqual(targetOriginalOracle.project);
    expect(unchangedTarget.journal).toEqual(targetOriginalOracle.journal);
    const unchangedDonor = await service.observeStoredProject(donor);
    expect(unchangedDonor.project).toEqual(donorHistory.project);
    expect(unchangedDonor.journal).toEqual(donorHistory.journal);
    observations.portable = { donor, donorHistory, originalCopy, restored,
      manifestSha256: createHash("sha256").update(manifestBytes).digest("hex"), blobs: manifest.blobs };
  } finally {
    const current = await target.project(restored?.id ?? targetOriginal.id);
    const otherOracle = await target.observeStoredProject(targetOriginal);
    observations.targetFinalization = await target.finalizeClosedStore(info.outputPath("closed-target-store"), current, [], [() => targetPage.close()]);
    await writeFile(info.outputPath("closed-target-store", "other-project-oracles.json"), JSON.stringify([otherOracle], null, 2));
    observations.targetService = { transcript: target.transcript, exits: target.processExits, taints: target.lifecycleTaints, stderr: target.stderr };
  }
  observations.behaviorAssertionsCompleted = true;
});

test("confirmed voice import stays successful when recovery cleanup is denied, including explicit readback after reopen", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  const source = info.outputPath("owned-confirmed-cleanup-denied.wav");
  const bytes = pcm(); await writeFile(source, bytes);
  await denyRecoveryCleanup(page);
  await page.goto("/"); await audio(page);
  await importVoiceUi(page, source, "Saved despite local cleanup failure");
  await revision(page, project.revision + 1);
  const saved = await service.project(project.id);
  const [original] = await copies(page, project.id);
  expect(saved.audio.voice_tracks.at(-1)).toMatchObject({ label: "Saved despite local cleanup failure",
    source_sha256: createHash("sha256").update(bytes).digest("hex") });
  expect(await service.history(project.id, project.revision)).toHaveLength(1);
  await expect(recovery(page)).toContainText("Import confirmed; recovery copy kept");
  await expect(recovery(page).getByRole("status")).toContainText("recovery cleanup denied");
  await expect(page.getByText("Voice import blocked.", { exact: true })).toHaveCount(0);
  await expect(field(page, "Voice file absolute path")).toHaveValue("");
  expect(requests(service, "import_voice_file")).toHaveLength(1);
  expect(importGrants(service)).toHaveLength(1);
  await rm(source); await service.close(); await service.start(); await page.reload(); await audio(page);
  expect(await service.project(project.id)).toEqual(saved);
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  await button(page, "Check original import").click();
  await expect(recovery(page)).toContainText("Import confirmed; recovery copy kept");
  await expect(recovery(page).getByRole("status")).toContainText("recovery cleanup denied");
  await expect(recovery(page).getByRole("alert")).toHaveCount(0);
  expect(await service.project(project.id)).toEqual(saved);
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  expect(requests(service, "import_voice_file")).toHaveLength(1);
  expect(importGrants(service)).toHaveLength(1);
  await page.screenshot({ path: info.outputPath("confirmed-import-with-local-cleanup-warning.png"), fullPage: true });
  // Once storage is available, explicit deletion clears only the local warning.
  // The original confirmed take and all saved project values remain intact.
  await page.evaluate(() => { (window as typeof window & { __denyImportCleanup: boolean }).__denyImportCleanup = false; });
  await button(page, "Remove copy…").click(); await button(page, "Remove local recovery copy").press("Space");
  await expect(recovery(page).getByRole("status")).toHaveText("The local recovery copy was removed. Any committed import remains in the project.");
  await expect(recovery(page).getByRole("heading", { name: "Import recovery", exact: true })).toBeFocused();
  expect(await copies(page, project.id)).toEqual([]); expect(await service.project(project.id)).toEqual(saved);
  expect(requests(service, "import_voice_file")).toHaveLength(1); expect(importGrants(service)).toHaveLength(1);
  observations.originalCopy = original; observations.finalProject = saved;
  observations.behaviorAssertionsCompleted = true;
});

test("confirmed recovery keeps ten later revisions when local cleanup is denied and never repeats import", async ({ page, imports }, info) => {
  const { service, project, peers, observations } = imports;
  const source = info.outputPath("owned-ten-revisions-cleanup-denied.wav"); await writeFile(source, pcm(310));
  await denyRecoveryCleanup(page); loseImportReply(imports, "import_voice_file");
  await page.goto("/"); await audio(page); await importVoiceUi(page, source, "Recovered original take");
  await expect(page.getByText("Injected test fault: import acknowledgement lost after commit", { exact: true })).toBeVisible();
  const [original] = await copies(page, project.id);
  const imported = await service.project(project.id);
  const peer = await service.peer(); peers.push(peer);
  let current = imported;
  for (let index = 0; index < 10; index++) {
    current = await peer.apply(current, { type: "set_narrative_premise", premise: "Preserved director revision " + (index + 1) });
  }
  expect(current.revision).toBe(imported.revision + 10);
  await rm(source); await peer.close(); await service.close(); await service.start();
  await page.reload(); await audio(page); await revision(page, current.revision);
  const calls = { imports: requests(service, "import_voice_file").length, grants: importGrants(service).length };
  await button(page, "Check original import").focus(); await page.keyboard.press("Space");
  await expect(recovery(page)).toContainText("Import confirmed; recovery copy kept");
  await expect(recovery(page).getByRole("status")).toContainText("The latest saved project is open");
  await expect(recovery(page).getByRole("status")).toContainText("recovery cleanup denied");
  await expect(recovery(page).getByRole("alert")).toHaveCount(0);
  await revision(page, current.revision);
  expect(await service.project(project.id)).toEqual(current);
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  expect(requests(service, "import_voice_file")).toHaveLength(calls.imports);
  expect(importGrants(service)).toHaveLength(calls.grants);
  expect(await service.history(project.id, imported.revision)).toHaveLength(10);
  await service.close(); await service.start(); await page.reload(); await audio(page);
  expect(await service.project(project.id)).toEqual(current);
  expect((await copies(page, project.id))[0].raw).toBe(original.raw);
  observations.originalCopy = original; observations.finalProject = current;
  observations.behaviorAssertionsCompleted = true;
});

test("a different window's recovery bytes survive an acknowledged import without inheriting its confirmation", async ({ page, imports }, info) => {
  const { service, project, observations } = imports;
  const source = info.outputPath("owned-pending-confirmation.wav"); await writeFile(source, pcm(420));
  const pending = holdImportReply(imports);
  await page.goto("/"); await audio(page); await importVoiceUi(page, source, "Original acknowledged take");
  const receipt = await pending.committed;
  const [original] = await copies(page, project.id);
  const otherWindow = await page.context().newPage(); await service.attachPage(otherWindow, project.id);
  imports.cleanup.push(async () => { await otherWindow.close(); });
  await otherWindow.goto("/");
  // Only another window's retained bytes change; the service result is genuine.
  const changed = original.raw + "\n";
  await otherWindow.evaluate(({ key, raw }) => localStorage.setItem(key, raw), { key: original.key, raw: changed });
  await expect.poll(async () => (await copies(page, project.id))[0].raw).toBe(changed);
  pending.release(); await revision(page, receipt.project.revision);
  await expect(recovery(page).getByRole("status")).toContainText("The original import of owned-pending-confirmation.wav is confirmed");
  await expect(recovery(page).getByRole("status")).toContainText("This confirmation does not apply to the selected copy");
  await expect(recovery(page)).not.toContainText("Import confirmed; recovery copy kept");
  await expect(page.getByText("Voice import blocked.", { exact: true })).toHaveCount(0);
  await expect(field(page, "Voice file absolute path")).toHaveValue("");
  expect((await copies(page, project.id))[0].raw).toBe(changed);
  expect(await service.project(project.id)).toEqual(receipt.project);
  expect(requests(service, "import_voice_file")).toHaveLength(1); expect(importGrants(service)).toHaveLength(1);
  expect(requests(service, "recover_file_import")).toEqual([]);
  await page.screenshot({ path: info.outputPath("changed-copy-does-not-inherit-confirmation.png"), fullPage: true });
  await otherWindow.close(); await rm(source); await service.close(); await service.start(); await page.reload(); await audio(page);
  expect((await copies(page, project.id))[0].raw).toBe(changed);
  expect(await service.project(project.id)).toEqual(receipt.project);
  // An explicit check of the newly observed bytes can confirm and clear them.
  await button(page, "Check original import").click();
  await expect(recovery(page)).toContainText("The latest saved project is open");
  expect(await copies(page, project.id)).toEqual([]);
  expect(requests(service, "import_voice_file")).toHaveLength(1); expect(importGrants(service)).toHaveLength(1);
  expect(await service.project(project.id)).toEqual(receipt.project);
  observations.originalCopy = original; observations.changedCopy = changed; observations.originalReceipt = receipt;
  observations.behaviorAssertionsCompleted = true;
});
