/**
 * Synthetic authoring scenarios over the real opt-in StudioService JSONL
 * transport, grants, SQLite and CAS. No Film projection, native render, PCM
 * audition, installed Tauri or human creative approval is simulated as evidence.
 * Captures contain every file present after verified EOF/exit0/signal-null;
 * the sealed ZIP still requires extraction and fresh-process readback later.
 */
import { createHash, randomUUID } from "node:crypto";
import { lstat, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import { defaultOriginalMix, emptyDeliveryRecipe, RECIPE_DRAFT_STORAGE_PREFIX, type DeliveryRecipe } from "../src/deliveryRecipe";
import type { Change, DeliverableProfile, MotionCanvasFilmOptions, Project } from "../src/types";
import { ScopedChangeService, type ClosedStoreCapture } from "./support/scopedChangeService";

test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Build studio_review_session from the declared composition and set its absolute path; there is no service fallback.");
test.describe.configure({ timeout: 90_000 });

const button = (page: Page | Locator, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page | Locator, name: string) => page.getByRole("textbox", { name, exact: true });
const digest = (value: Uint8Array | string) => createHash("sha256").update(value).digest("hex");
const recipeFor = (project: Project, profile: DeliverableProfile | string) => project.production_design?.delivery_recipes?.[typeof profile === "string" ? profile : profile.id] ?? null;
const stamp = (project: Project) => ({ project_id: project.id, generation: project.generation, revision: project.revision });
const explain = (error: unknown) => error instanceof Error ? error.message : String(error);

interface ApplyArgs extends Record<string, unknown> {
  request: { project_id: string; generation: string; revision: number; request_id: string; effect_grant: string; change: Change };
}
function requests(service: ScopedChangeService, command: string): Record<string, unknown>[] {
  return service.transcript.flatMap(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === command
    ? [entry.payload.args?.request as Record<string, unknown>] : []);
}
const recipeRequests = (service: ScopedChangeService) => requests(service, "apply_change").filter(request => (request.change as Change)?.type === "set_delivery_recipe");
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
/** Only transport timing is controlled. The actual grant/operation/result is Rust. */
function holdRecipe(service: ScopedChangeService, phase: "before" | "after", profileId?: string) {
  const original = service.invoke.bind(service);
  const entered = deferred<ApplyArgs>();
  const observed = deferred<{ project: Project } | { error: unknown }>();
  const released = deferred<void>();
  const finished = deferred<void>();
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyArgs["request"] | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "set_delivery_recipe"
      || profileId && request.change.deliverable_profile_id !== profileId) return original<T>(command, args);
    armed = false; matched = true; entered.resolve(structuredClone(args) as ApplyArgs);
    try {
      if (phase === "before") await released.promise;
      let outcome: { project: Project } | { error: unknown };
      try { outcome = { project: await original<Project>(command, args) }; }
      catch (error) { outcome = { error }; }
      observed.resolve(outcome);
      if (phase === "after") await released.promise;
      if ("error" in outcome) throw outcome.error;
      return outcome.project as T;
    } finally { finished.resolve(); }
  };
  return { entered: entered.promise, observed: observed.promise, release: () => released.resolve(), async dispose() {
    service.invoke = original; released.resolve(); if (matched) await finished.promise;
  } };
}

async function paint(page: Page) {
  await page.evaluate(() => new Promise<void>(done => requestAnimationFrame(() => requestAnimationFrame(() => done()))));
}
async function revision(page: Page, expected: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText(`r${expected}`);
  await paint(page);
}
async function film(page: Page, profile?: DeliverableProfile) {
  await button(page, "Deliver").click();
  await expect(page.getByRole("region", { name: "Delivery profiles", exact: true })).toBeVisible();
  if (profile) await page.locator(".delivery-profile-row").filter({ hasText: profile.name }).click();
  return page.getByRole("region", { name: "Scene intent draft review", exact: true });
}
async function mix(page: Page, profile?: DeliverableProfile) {
  await button(page, "Audio").click();
  const panel = page.locator('details[aria-label="Original three-bus native PCM audition"]');
  if (await panel.getAttribute("open") === null) await panel.locator("summary").first().click();
  if (profile) await panel.getByRole("combobox", { name: "Delivery language / profile", exact: true }).selectOption(profile.id);
  await expect(panel.getByLabel("Original music asset", { exact: true })).toBeVisible();
  return panel;
}
async function authorFilm(page: Page, project: Project, profile: DeliverableProfile, firstRole = "hook") {
  await film(page, profile);
  for (const [index, scene] of project.scenes.filter(scene => scene.renderer === "motion-canvas").entries()) {
    await page.getByLabel(scene.name + " narrative role", { exact: true }).selectOption(index === 0 ? firstRole : "problem");
    await page.getByLabel(scene.name + " motion archetype", { exact: true }).selectOption(index === 0 ? "statement" : "diagram_build");
  }
}
async function save(page: Page, service: ScopedChangeService, projectId: string, name: string) {
  const before = await service.project(projectId);
  await button(page, name).click();
  await revision(page, before.revision + 1);
  await expect(page.locator(".error-banner")).toHaveCount(0);
  return service.project(projectId);
}
function outsideRecipes(project: Project) {
  const copy = structuredClone(project);
  copy.revision = 0; copy.updated_at = "";
  copy.branches = copy.branches.map(branch => ({ ...branch, head_revision: 0 }));
  if (copy.production_design) copy.production_design.delivery_recipes = {};
  return copy;
}
function makeProfile(id: string, name: string): DeliverableProfile {
  return { id, name, width: 1920, height: 1080, language: "en", captions: true, caption_format: "web_vtt",
    video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000, brand_profile: null, cut_label: null,
    parent_profile_id: null, source_revision: null, framing_strategy: "replan", crop_approved: false,
    timing_locked: false, voice_track_id: null, text_overrides: {}, included_scene_ids: [], protected_scene_ids: [],
    burn_in_captions: false, frame_rate: { num: "30", den: "1" }, color_space: "rec709", container: "mp4", adaptation_notes: [] };
}
function syntheticWav(amplitude: number, period: number) {
  // One second of non-silent synthetic stereo PCM. These are test source bytes,
  // never speech, a fabricated render result or evidence of listening quality.
  const frames = 48_000, bytes = Buffer.alloc(44 + frames * 4);
  bytes.write("RIFF", 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write("WAVEfmt ", 8);
  bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(2, 22);
  bytes.writeUInt32LE(48_000, 24); bytes.writeUInt32LE(192_000, 28); bytes.writeUInt16LE(4, 32); bytes.writeUInt16LE(16, 34);
  bytes.write("data", 36); bytes.writeUInt32LE(frames * 4, 40);
  for (let frame = 0; frame < frames; frame++) {
    const sample = frame % period < period / 2 ? amplitude : -amplitude;
    bytes.writeInt16LE(sample, 44 + frame * 4); bytes.writeInt16LE(-sample, 46 + frame * 4);
  }
  return bytes;
}
interface CaseProof {
  info: TestInfo; services: ScopedChangeService[]; pages: Page[]; gates: ReturnType<typeof holdRecipe>[];
  ids: Map<string, Set<string>>; observations: Record<string, unknown>;
  create(name: string, desktop?: boolean): Promise<ScopedChangeService>;
  peer(service: ScopedChangeService): Promise<ScopedChangeService>;
  attach(service: ScopedChangeService, page: Page): Promise<void>;
  track(service: ScopedChangeService, project: Project): void;
}
async function scenario(info: TestInfo, run: (proof: CaseProof) => Promise<void>) {
  const proof: CaseProof = { info, services: [], pages: [], gates: [], ids: new Map(), observations: { behaviorAssertionsCompleted: false },
    async create(name, desktop = false) {
      const service = await ScopedChangeService.create({ databasePath: info.outputPath(name, "studio.sqlite3"), desktopInvokeFixture: desktop });
      this.services.push(service); this.track(service, (await service.fixtureBootstrap()).project); return service;
    },
    async peer(service) { const peer = await service.peer(); this.services.push(peer); return peer; },
    async attach(service, page) { this.pages.push(page); await service.attachFixturePage(page); await page.goto("/"); },
    track(service, project) { const ids = this.ids.get(service.directory) ?? new Set<string>(); ids.add(project.id); this.ids.set(service.directory, ids); },
  };
  try { await run(proof); proof.observations.behaviorAssertionsCompleted = true; }
  catch (failure) { proof.observations.failure = explain(failure); throw failure; }
  finally { await closeAndSeal(proof); }
}
async function setup(proof: CaseProof, service: ScopedChangeService, withAudio = false) {
  let project = (await service.fixtureBootstrap()).project;
  for (const scene of project.scenes) if (scene.renderer !== "motion-canvas") {
    project = await service.apply(project, { type: "set_scene_renderer", scene_id: scene.id, renderer: "motion-canvas" });
  }
  expect(project.scenes.length).toBeGreaterThanOrEqual(2);
  let music: string | null = null, sfx: string | null = null;
  if (withAudio) {
    const sourceDirectory = proof.info.outputPath("source-files"); await mkdir(sourceDirectory, { recursive: true });
    for (const [index, name] of ["voice", "music", "sfx"].entries()) {
      const bytes = syntheticWav(2048 + index * 1024, 120 + index * 30);
      const path = join(sourceDirectory, name + ".wav"); await writeFile(path, bytes);
      project = name === "voice" ? await service.importVoice(project, path, "Synthetic measured stereo source")
        : await service.importAsset(project, path, "Synthetic original " + name, "audio/wav");
      const asset = project.assets.find(asset => asset.content_sha256 === digest(bytes))!;
      expect(asset).toBeTruthy();
      if (name === "music") music = asset.id;
      if (name === "sfx") sfx = asset.id;
      await rm(path); // Reopen and bundle must use CAS, not the external original.
    }
    const voice = project.audio.voice_tracks.at(-1)!;
    expect(voice).toMatchObject({ sample_rate_hz: 48_000, channels: 2, measured_duration: { num: "1", den: "1" } });
    project = await service.apply(project, { type: "set_active_voice_track", track_id: voice.id });
    project = await service.apply(project, { type: "upsert_transcript_segment", segment: {
      id: randomUUID(), voice_track_id: voice.id, start: { num: "0", den: "1" }, end: { num: "1", den: "1" },
      text: "Synthetic timing interval, not recorded speech.", speaker: null, alignment: { kind: "manual" },
    } });
  }
  const a = { ...(project.deliverables[0] ?? makeProfile(randomUUID(), "")), name: "Recipe profile A", frame_rate: { num: "30", den: "1" } };
  const b = { ...makeProfile(randomUUID(), "Recipe profile B"), frame_rate: { num: "24", den: "1" } };
  project = await service.apply(project, { type: "upsert_deliverable", profile: a });
  project = await service.apply(project, { type: "upsert_deliverable", profile: b });
  return { project, a, b, music, sfx };
}
async function seedRecipe(service: ScopedChangeService, project: Project, profile: DeliverableProfile, withMix = false, music: string | null = null, sfx: string | null = null) {
  const recipe: DeliveryRecipe = { ...emptyDeliveryRecipe(), scene_intents: project.scenes.filter(scene => scene.renderer === "motion-canvas").map((scene, index) => ({
    scene_id: scene.id, role: index === 0 ? "hook" : "problem", archetype: index === 0 ? "statement" : "diagram_build",
  })), original_mix: withMix ? { ...defaultOriginalMix(), music_asset_id: music, sfx_asset_id: sfx } : null };
  return service.apply(project, { type: "set_delivery_recipe", deliverable_profile_id: profile.id, recipe });
}
async function verifyCas(service: ScopedChangeService, project: Project) {
  const evidence = [];
  for (const asset of project.assets) {
    expect(asset.content_sha256).toMatch(/^[a-f0-9]{64}$/);
    const sha256 = asset.content_sha256!;
    const bytes = await readFile(join(service.directory, "blobs", "sha256", sha256.slice(0, 2), sha256));
    expect(digest(bytes)).toBe(sha256);
    evidence.push({ asset_id: asset.id, sha256, size_bytes: bytes.length });
  }
  return evidence;
}
async function reopenExact(service: ScopedChangeService, project: Project) {
  const history = await service.history(project.id, 0, 256);
  await service.close(); await service.start();
  expect(await service.project(project.id)).toEqual(project);
  expect(await service.history(project.id, 0, 256)).toEqual(history);
  await verifyCas(service, project);
}

// Bounded, dependency-free ZIP32 writer for test evidence only. A stored ZIP
// permits independent Python/Windows extraction; it is not product packaging.
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
async function archiveCapture(info: TestInfo, index: number, capture: ClosedStoreCapture, expected: unknown) {
  const files: { name: string; bytes: Buffer; directory?: boolean }[] = [];
  const entries: { path: string; kind: string; sizeBytes?: number; sha256?: string }[] = [];
  const visit = async (directory: string, name: string): Promise<void> => {
    const stat = await lstat(directory);
    if (stat.isSymbolicLink()) throw new Error("Evidence snapshot contains a symlink");
    if (stat.isDirectory()) {
      entries.push({ path: name, kind: "directory" }); files.push({ name: "store/" + (name ? name + "/" : ""), bytes: Buffer.alloc(0), directory: true });
      for (const child of (await readdir(directory)).sort()) await visit(join(directory, child), name ? name + "/" + child : child);
    } else if (stat.isFile()) {
      const bytes = await readFile(directory);
      entries.push({ path: name, kind: "file", sizeBytes: bytes.length, sha256: digest(bytes) }); files.push({ name: "store/" + name, bytes });
    } else throw new Error("Evidence snapshot contains a non-regular entry");
  };
  await visit(capture.snapshotDirectory, "");
  expect(entries).toEqual(capture.entries); // Includes every WAL/SHM that exists.
  files.push({ name: "manifest.json", bytes: Buffer.from(JSON.stringify(capture, null, 2)) },
    { name: "readback-expectations.json", bytes: Buffer.from(JSON.stringify(expected, null, 2)) });
  const bytes = zip32(files), path = info.outputPath(`closed-store-${index}.zip`);
  await writeFile(path, bytes, { flag: "wx" });
  return { path, sha256: digest(bytes), size_bytes: bytes.length, entries: files.length,
    evidence_scope: "complete_capture_archived_in_worker_before_tool_return; separate_extraction_and_readback_required" };
}
async function closeAndSeal(proof: CaseProof) {
  const errors: string[] = [];
  for (const gate of proof.gates) { try { await gate.dispose(); } catch (error) { errors.push(explain(error)); } }
  for (const page of proof.pages) { if (!page.isClosed()) { try { await page.close(); } catch (error) { errors.push(explain(error)); } } }
  const stores = proof.services.filter((service, index) => proof.services.findIndex(peer => peer.directory === service.directory) === index);
  const expectedStores: unknown[] = [];
  for (const service of stores) {
    try {
      const projects = [];
      for (const id of proof.ids.get(service.directory) ?? []) {
        const project = await service.project(id), history = await service.history(id, 0, 256);
        projects.push({ project, history, cas: await verifyCas(service, project) });
      }
      expectedStores.push({ databasePath: service.databasePath, projects });
    } catch (error) { errors.push(explain(error)); expectedStores.push({ databasePath: service.databasePath, error: explain(error) }); }
  }
  for (const result of await Promise.allSettled(proof.services.map(service => service.close()))) if (result.status === "rejected") errors.push(explain(result.reason));
  const captures = [], archives = [];
  if (!errors.length) {
    for (const [index, service] of stores.entries()) {
      try {
        const capture = await service.captureClosedStore(proof.info.outputPath(`closed-store-${index + 1}`), proof.services);
        captures.push(capture); archives.push(await archiveCapture(proof.info, index + 1, capture, expectedStores[index]));
      } catch (error) { errors.push(explain(error)); }
    }
  }
  const path = proof.info.outputPath("delivery-recipe-real-service-evidence.json");
  await writeFile(path, JSON.stringify({ schema: "motionwright.delivery-recipe-browser-proof/1",
    evidenceScope: "synthetic_authoring_browser_service_test_jsonl_real_grants_sqlite_cas; excludes_tauri_installer_native_projection_render_audio_quality_and_human_approval",
    observations: proof.observations, expectedStores, captures, archives, captureErrors: errors,
    services: proof.services.map(service => ({ databasePath: service.databasePath, binaryPath: service.binaryPath,
      processExits: service.processExits, stderr: service.stderr, transcript: service.transcript })),
  }, null, 2));
  await proof.info.attach("delivery-recipe-real-service", { path, contentType: "application/json" });
  if (errors.length) throw new Error("Closed-store evidence is incomplete: " + errors.join("; "));
}


test("two profiles retain both canonical groups through explicit saves, clear and fresh-process reopen", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const initial = await setup(proof, service, true);
  let project = initial.project;
  const { a, b, music, sfx } = initial;
  const unrelated = outsideRecipes(project);
  await proof.attach(service, page);
  await authorFilm(page, project, a);
  project = await save(page, service, project.id, "Save scene intent");
  const filmA = structuredClone(recipeFor(project, a)!.scene_intents);
  let panel = await mix(page, a);
  await panel.getByLabel("Original music asset", { exact: true }).selectOption(music!);
  await panel.getByLabel("Original SFX asset", { exact: true }).selectOption(sfx!);
  await field(panel, "SFX gain dB").fill("-9.25");
  project = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(project, a)!.scene_intents).toEqual(filmA);
  const recipeA = structuredClone(recipeFor(project, a));
  await authorFilm(page, project, b, "comparison");
  project = await save(page, service, project.id, "Save scene intent");
  panel = await mix(page, b);
  await panel.getByLabel("Original music asset", { exact: true }).selectOption(music!);
  await panel.getByLabel("Original SFX asset", { exact: true }).selectOption(sfx!);
  await field(panel, "SFX gain dB").fill("-11");
  await field(panel, "Attack (samples at 48 kHz)").fill("2401");
  project = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(project, a)).toEqual(recipeA);
  const recipeB = structuredClone(recipeFor(project, b));
  expect(outsideRecipes(project)).toEqual(unrelated);
  const authored = structuredClone(project);
  await reopenExact(service, project);
  await page.reload();
  await film(page, a);
  await expect(page.getByLabel(project.scenes[0].name + " narrative role", { exact: true })).toHaveValue("hook");
  panel = await mix(page, b);
  await expect(field(panel, "SFX gain dB")).toHaveValue("-11");
  await expect(field(panel, "Attack (samples at 48 kHz)")).toHaveValue("2401");
  await film(page, a);
  await button(page, "Clear saved scene intent").click();
  expect(await service.project(project.id)).toEqual(project);
  await expect(page.getByRole("region", { name: "Scene intent draft review", exact: true })).toContainText("Clearing is pending");
  project = await save(page, service, project.id, "Save scene intent");
  expect(recipeFor(project, a)).toEqual({ ...recipeA, scene_intents: [] });
  expect(recipeFor(project, b)).toEqual(recipeB);
  await mix(page, a);
  await button(page, "Clear saved original mix recipe").click();
  expect(await service.project(project.id)).toEqual(project);
  project = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(project, a)).toBeNull();
  expect(recipeFor(project, b)).toEqual(recipeB);
  expect(outsideRecipes(project)).toEqual(unrelated);
  expect((await service.history(project.id, initial.project.revision)).map(event => event.change.type)).toEqual(Array(6).fill("set_delivery_recipe"));
  await reopenExact(service, project);
  proof.observations.authoredBothGroups = authored;
  proof.observations.explicitlyClearedOnlyA = project;
  await page.screenshot({ path: info.outputPath("two-profile-recipes.png"), fullPage: true });
}));

test("a committed reply held across navigation preserves Sent then Old until explicit conflict resolution", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const setupResult = await setup(proof, service);
  const { a } = setupResult;
  let project = await seedRecipe(service, setupResult.project, a);
  const roleLabel = project.scenes[0].name + " narrative role";
  await proof.attach(service, page);
  await film(page, a);
  await page.getByLabel(roleLabel, { exact: true }).selectOption("reveal");
  const held = holdRecipe(service, "after", a.id); proof.gates.push(held);
  await button(page, "Save scene intent").click();
  const outcome = await held.observed; if ("error" in outcome) throw outcome.error;
  expect(recipeFor(outcome.project, a)!.scene_intents[0].role).toBe("reveal");
  expect(outcome.project.revision).toBe(project.revision + 1);
  await button(page, "Timeline").click(); await film(page, a);
  await expect(page.getByLabel(roleLabel, { exact: true })).toHaveValue("reveal");
  await page.getByLabel(roleLabel, { exact: true }).selectOption("hook");
  await button(page, "Timeline").click(); await film(page, a);
  await expect(page.getByLabel(roleLabel, { exact: true })).toHaveValue("hook");
  await page.reload(); await film(page, a);
  await revision(page, outcome.project.revision);
  await expect(page.getByLabel(roleLabel, { exact: true })).toHaveValue("hook");
  const conflict = page.getByRole("region", { name: "Scene intent conflicts", exact: true });
  await expect(conflict).toContainText("hook"); await expect(conflict).toContainText("reveal");
  await expect(button(page, "Save scene intent")).toBeDisabled();
  held.release(); await held.dispose();
  expect(await service.project(project.id)).toEqual(outcome.project);
  await button(conflict, "Keep my draft " + roleLabel).click();
  await expect(page.getByLabel(roleLabel, { exact: true })).toBeFocused();
  expect(await service.project(project.id)).toEqual(outcome.project);
  project = await save(page, service, project.id, "Save scene intent");
  expect(recipeFor(project, a)!.scene_intents[0].role).toBe("hook");
  expect(project.revision).toBe(outcome.project.revision + 1);
  proof.observations.heldCanonical = outcome.project;
  proof.observations.finalExplicitSave = project;
}));

test("typing across Film profile and mix scopes during a real save retains every unsent input", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const initial = await setup(proof, service);
  const { a, b } = initial;
  let project = await seedRecipe(service, initial.project, a);
  const first = project.scenes[0];
  await proof.attach(service, page);
  await film(page, a);
  await page.getByLabel(first.name + " narrative role", { exact: true }).selectOption("payoff");
  const held = holdRecipe(service, "after", a.id); proof.gates.push(held);
  await button(page, "Save scene intent").click();
  const outcome = await held.observed; if ("error" in outcome) throw outcome.error;
  await authorFilm(page, project, b, "comparison");
  const panel = await mix(page, a);
  await field(panel, "SFX gain dB").fill("-12.5");
  await film(page, a);
  await page.getByLabel(first.name + " motion archetype", { exact: true }).selectOption("metric");
  held.release(); await held.dispose();
  await revision(page, outcome.project.revision);
  await expect(page.getByLabel(first.name + " narrative role", { exact: true })).toHaveValue("payoff");
  await expect(page.getByLabel(first.name + " motion archetype", { exact: true })).toHaveValue("metric");
  expect(recipeFor(await service.project(project.id), a)!.scene_intents[0].archetype).toBe("statement");
  project = await save(page, service, project.id, "Save scene intent");
  await film(page, b);
  await expect(page.getByLabel(first.name + " narrative role", { exact: true })).toHaveValue("comparison");
  project = await save(page, service, project.id, "Save scene intent");
  await mix(page, a);
  await expect(field(page, "SFX gain dB")).toHaveValue("-12.5");
  project = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(project, a)).toMatchObject({ scene_intents: [expect.objectContaining({ role: "payoff", archetype: "metric" }), expect.anything()], original_mix: { sfx_gain_db: -12.5 } });
  expect(recipeFor(project, b)!.scene_intents[0].role).toBe("comparison");
  expect(recipeFor(project, b)!.original_mix).toBeNull();
  proof.observations.heldRequest = await held.entered;
  proof.observations.final = project;
}));

for (const keep of ["saved", "draft"] as const) {
  test(`two real clients reject a stale save and resolve only the overlapping field with keep ${keep}`, async ({ page, context }, info) => scenario(info, async proof => {
    const service = await proof.create("service");
    const initial = await setup(proof, service);
    const { a } = initial;
    let project = await seedRecipe(service, initial.project, a);
    const before = project;
    const peer = await proof.peer(service), second = await context.newPage();
    await proof.attach(service, page); await proof.attach(peer, second);
    const first = project.scenes[0], other = project.scenes[1];
    const roleLabel = first.name + " narrative role";
    await film(page, a); await film(second, a);
    await page.getByLabel(roleLabel, { exact: true }).selectOption("comparison");
    await page.getByLabel(other.name + " motion archetype", { exact: true }).selectOption("evidence_frame");
    await second.getByLabel(roleLabel, { exact: true }).selectOption("mechanism");
    await second.getByLabel(first.name + " motion archetype", { exact: true }).selectOption("metric");
    const held = holdRecipe(service, "before", a.id); proof.gates.push(held);
    await button(page, "Save scene intent").click(); await held.entered;
    const winner = await save(second, peer, project.id, "Save scene intent");
    held.release();
    const rejection = await held.observed;
    expect("error" in rejection).toBe(true);
    if ("error" in rejection) expect(explain(rejection.error)).toMatch(/stale|revision|conflict/i);
    await held.dispose();
    await expect(page.getByRole("region", { name: "Scene intent draft review", exact: true }).getByRole("alert")).toContainText(/stale|revision|conflict/i);
    expect(await service.project(project.id)).toEqual(winner);
    await page.reload(); await film(page, a);
    const conflicts = page.getByRole("region", { name: "Scene intent conflicts", exact: true });
    await expect(conflicts.getByRole("group")).toHaveCount(1);
    await expect(conflicts).toContainText(roleLabel);
    await expect(page.getByLabel(first.name + " motion archetype", { exact: true })).toHaveValue("metric");
    await expect(page.getByLabel(other.name + " motion archetype", { exact: true })).toHaveValue("evidence_frame");
    await expect(button(page, "Save scene intent")).toBeDisabled();
    await button(conflicts, (keep === "saved" ? "Keep saved " : "Keep my draft ") + roleLabel).click();
    await expect(page.getByLabel(roleLabel, { exact: true })).toBeFocused();
    expect(await service.project(project.id)).toEqual(winner);
    const time = await page.locator(".timeline-header .timecode").textContent();
    await button(page, "Save scene intent").focus(); await page.keyboard.press("Space");
    await revision(page, winner.revision + 1);
    await expect(page.getByRole("button", { name: "Play design preview", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause design preview", exact: true })).toHaveCount(0);
    await expect(page.locator(".timeline-header .timecode")).toHaveText(time!);
    project = await service.project(project.id);
    expect(recipeFor(project, a)!.scene_intents).toEqual([
      { scene_id: first.id, role: keep === "saved" ? "mechanism" : "comparison", archetype: "metric" },
      { scene_id: other.id, role: "problem", archetype: "evidence_frame" },
    ]);
    expect((await service.history(project.id, before.revision)).map(event => event.change.type)).toEqual(["set_delivery_recipe", "set_delivery_recipe"]);
    expect(outsideRecipes(project)).toEqual(outsideRecipes(before));
    proof.observations.winner = winner; proof.observations.final = project;
    proof.observations.rejectedStaleSave = "error" in rejection ? explain(rejection.error) : null;
  }));
}

test("invalid numeric raw survives a real reload and only corrected values reach the service", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const { project, a } = await setup(proof, service);
  await proof.attach(service, page);
  let panel = await mix(page, a);
  await field(panel, "SFX gain dB").fill("1e");
  await field(panel, "Attack (samples at 48 kHz)").fill("2.5");
  const grantCount = requests(service, "issue_effect_grant").length;
  const recipeCount = recipeRequests(service).length;
  await button(page, "Save original mix recipe").click();
  await expect(panel.getByRole("alert")).toContainText(/SFX gain|finite|number/i);
  expect(await service.project(project.id)).toEqual(project);
  expect(requests(service, "issue_effect_grant")).toHaveLength(grantCount);
  expect(recipeRequests(service)).toHaveLength(recipeCount);
  await page.reload(); panel = await mix(page, a);
  await expect(field(panel, "SFX gain dB")).toHaveValue("1e");
  await expect(field(panel, "Attack (samples at 48 kHz)")).toHaveValue("2.5");
  await field(panel, "SFX gain dB").fill("-6.75");
  await button(page, "Save original mix recipe").click();
  await expect(panel.getByRole("alert")).toContainText(/whole|integer|samples/i);
  expect(await service.project(project.id)).toEqual(project);
  await field(panel, "Attack (samples at 48 kHz)").fill("2401");
  const final = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(final, a)!.original_mix).toEqual({ ...defaultOriginalMix(), sfx_gain_db: -6.75, duck_attack_samples: 2401 });
  expect(recipeRequests(service)).toHaveLength(recipeCount + 1);
  await reopenExact(service, final);
  proof.observations.invalidRawRejectedBeforeGrant = { gain: "1e", attack: "2.5" };
}));

test("a full recovery budget warns and preserves memory while an explicit service save remains available", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const { project, a } = await setup(proof, service);
  await proof.attach(service, page);
  await page.evaluate(prefix => {
    for (let index = 0; index < 64; index++) localStorage.setItem(prefix + "unrelated-budget-" + index, "{}");
  }, RECIPE_DRAFT_STORAGE_PREFIX);
  let panel = await mix(page, a);
  await field(panel, "SFX gain dB").fill("-10.5");
  await expect(panel.getByRole("alert")).toContainText(/budget|64|backup|recovery/i);
  await button(page, "Timeline").click(); panel = await mix(page, a);
  await expect(field(panel, "SFX gain dB")).toHaveValue("-10.5");
  await expect(panel.getByRole("alert")).toContainText(/budget|backup|recovery/i);
  const final = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(final, a)!.original_mix!.sfx_gain_db).toBe(-10.5);
  expect(await page.evaluate(prefix => Object.keys(localStorage).filter(key => key.startsWith(prefix + "unrelated-budget-")).length, RECIPE_DRAFT_STORAGE_PREFIX)).toBe(64);
  // No pending input remains after canonical acknowledgement. A new input
  // must warn again because the unrelated budget entries were not pruned.
  await field(panel, "SFX gain dB").fill("-10.75");
  await expect(panel.getByRole("alert")).toContainText(/budget|backup|recovery/i);
  expect(await service.project(project.id)).toEqual(final);
  const pending = panel.locator('details[aria-label="Original mix pending fields"]');
  await pending.locator("summary").click();
  await button(pending, "Keep saved SFX gain dB").click();
  await reopenExact(service, final);
  proof.observations.recoveryLimit = "64 unrelated namespace entries remain untouched; pending input lived in memory. This does not claim a failed backup survives reload.";
}));

test("an unreadable own copy is retained until explicit discard without losing newer typed input", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const { project, a } = await setup(proof, service);
  await proof.attach(service, page);
  let panel = await mix(page, a);
  await field(panel, "SFX gain dB").fill("-10");
  const corruptedKey = await page.evaluate(({ prefix, projectId, profileId }) => {
    const key = Object.keys(localStorage).find(key => key.startsWith(prefix) && key.includes(projectId) && key.includes(profileId) && key.includes('"mix"'));
    if (!key) throw new Error("Expected the actual own mix recovery copy before corrupting it");
    localStorage.setItem(key, "{unreadable-test-copy"); return key;
  }, { prefix: RECIPE_DRAFT_STORAGE_PREFIX, projectId: project.id, profileId: a.id });
  await page.reload(); panel = await mix(page, a);
  await expect(button(panel, "Discard unreadable original mix copy")).toBeVisible();
  await field(panel, "SFX gain dB").fill("-11.25");
  await button(page, "Timeline").click(); panel = await mix(page, a);
  await expect(field(panel, "SFX gain dB")).toHaveValue("-11.25");
  await expect(button(panel, "Save original mix recipe")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), corruptedKey)).toBe("{unreadable-test-copy");
  expect(await service.project(project.id)).toEqual(project);
  await button(panel, "Discard unreadable original mix copy").click();
  await expect(field(panel, "SFX gain dB")).toHaveValue("-11.25");
  await expect(button(panel, "Save original mix recipe")).toBeEnabled();
  const final = await save(page, service, project.id, "Save original mix recipe");
  expect(recipeFor(final, a)!.original_mix!.sfx_gain_db).toBe(-11.25);
  proof.observations.explicitDiscardedKey = corruptedKey;
}));

test("a new window with copied session storage offers another window's draft only by explicit recovery", async ({ page, context }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const initial = await setup(proof, service);
  const { a } = initial;
  const project = await seedRecipe(service, initial.project, a);
  await proof.attach(service, page); await film(page, a);
  const label = project.scenes[0].name + " narrative role";
  await page.getByLabel(label, { exact: true }).selectOption("comparison");
  const pendingPopup = context.waitForEvent("page");
  await page.evaluate(() => { window.open("about:blank", "recipe-recovery-test-window"); });
  const popup = await pendingPopup;
  const peer = await proof.peer(service); await proof.attach(peer, popup); await film(popup, a);
  await expect(popup.getByLabel(label, { exact: true })).toHaveValue("hook");
  const copies = popup.locator('details[aria-label="Scene intent recovery copies"]');
  await copies.locator("summary").click();
  await button(copies, "Refresh scene intent copies").click();
  await expect(button(copies, "Recover scene intent copy 1")).toBeEnabled();
  expect(await service.project(project.id)).toEqual(project);
  await button(copies, "Recover scene intent copy 1").click();
  await expect(popup.getByLabel(label, { exact: true })).toHaveValue("comparison");
  expect(await service.project(project.id)).toEqual(project);
  await expect(page.getByLabel(label, { exact: true })).toHaveValue("comparison");
  const final = await save(popup, peer, project.id, "Save scene intent");
  expect(recipeFor(final, a)!.scene_intents[0].role).toBe("comparison");
  proof.observations.noAutomaticCopyApplication = true;
}));

test("project content locks and a real revoked grant reject writes while preserving retryable inputs", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service");
  const initial = await setup(proof, service);
  const { a } = initial;
  let project = await seedRecipe(service, initial.project, a);
  await proof.attach(service, page); await film(page, a);
  const label = project.scenes[0].name + " narrative role";
  await page.getByLabel(label, { exact: true }).selectOption("evidence");
  project = await service.apply(project, { type: "set_lock", resource: "project:" + project.id, kind: "content", note: "Synthetic test content lock" });
  await page.reload(); await film(page, a);
  await expect(page.getByLabel(label, { exact: true })).toHaveValue("evidence");
  await expect(page.getByLabel(label, { exact: true })).toBeDisabled();
  await expect(button(page, "Save scene intent")).toBeDisabled();
  const locked = project;
  await expect(service.apply(project, { type: "set_delivery_recipe", deliverable_profile_id: a.id, recipe: null })).rejects.toThrow(/lock/i);
  expect(await service.project(project.id)).toEqual(locked);
  expect(await service.history(project.id, locked.revision)).toEqual([]);
  const lock = project.locks.find(lock => lock.resource === "project:" + project.id && lock.kind === "content")!;
  project = await service.apply(project, { type: "remove_lock", lock_id: lock.id });
  await page.reload(); await film(page, a);
  await expect(page.getByLabel(label, { exact: true })).toHaveValue("evidence");
  const held = holdRecipe(service, "before", a.id); proof.gates.push(held);
  await button(page, "Save scene intent").click(); await held.entered;
  expect(await service.reopen(project.id)).toEqual(project); // New real EffectGrantRegistry.
  held.release(); const outcome = await held.observed; await held.dispose();
  expect("error" in outcome).toBe(true);
  if ("error" in outcome) expect(explain(outcome.error)).toContain("Effect grant");
  await expect(page.getByRole("region", { name: "Scene intent draft review", exact: true }).getByRole("alert")).toContainText("Effect grant");
  expect(await service.project(project.id)).toEqual(project);
  expect(await service.history(project.id, project.revision)).toEqual([]);
  await expect(page.getByLabel(label, { exact: true })).toHaveValue("evidence");
  await expect(button(page, "Save scene intent")).toBeEnabled();
  const final = await save(page, service, project.id, "Save scene intent");
  expect(recipeFor(final, a)!.scene_intents[0].role).toBe("evidence");
  proof.observations.revokedGrantFailure = "error" in outcome ? explain(outcome.error) : null;
}));

test("Film and PCM guards use saved options and expose unsupported combinations without pretending a native result", async ({ page }, info) => scenario(info, async proof => {
  const service = await proof.create("service", true); // Opt-in desktop invoke routing; not installed Tauri.
  const initial = await setup(proof, service, true);
  const { a, b, music, sfx } = initial;
  let project = await service.apply(initial.project, { type: "set_visual_language", visual_language: {
    ...initial.project.visual_language,
    palette: [{ name: "text", value: "#f7f7f7" }, { name: "background", value: "#101010" }],
  } });
  await proof.attach(service, page); await authorFilm(page, project, a);
  await expect(button(page, "Check Film projection")).toBeDisabled();
  expect(requests(service, "motion_canvas_preflight")).toHaveLength(0);
  project = await save(page, service, project.id, "Save scene intent");
  await expect(button(page, "Check Film projection")).toBeEnabled();
  const options: MotionCanvasFilmOptions = { frame_rate: { num: 30, den: 1 },
    font_family: "Instrument Sans Variable", mono_font_family: "IBM Plex Mono",
    scene_intents: recipeFor(project, a)!.scene_intents.map(intent => ({ scene_id: intent.scene_id, role: intent.role!, archetype: intent.archetype! })),
  };
  await button(page, "Check Film projection").click();
  await expect(page.locator(".delivery-profile-workbench .portable-message.error")).toContainText("Unsupported test transport command: motion_canvas_preflight");
  expect(requests(service, "motion_canvas_preflight")).toEqual([{ ...stamp(project), deliverable_id: a.id, options }]);
  expect(requests(service, "render_motion_canvas")).toHaveLength(0);
  // No projection-ready fixture is returned. We can still prove the API refuses
  // caller-supplied divergence before asking for any render authority.
  const grantsBefore = requests(service, "issue_effect_grant").length;
  const apiGuards = await page.evaluate(async ({ project, profileId, options }) => {
    const modulePath = "/src/api.ts", api = await import(modulePath);
    const failures: string[] = [];
    for (const [name, changed] of [
      ["preflightMotionCanvas", { ...options, frame_rate: { num: 24, den: 1 } }],
      ["renderMotionCanvas", { ...options, font_family: "Unsubmitted family" }],
    ] as const) {
      try { await api[name](project, profileId, changed); failures.push("UNEXPECTED_SUCCESS"); }
      catch (error) { failures.push(error instanceof Error ? error.message : String(error)); }
    }
    return failures;
  }, { project, profileId: a.id, options });
  expect(apiGuards).toHaveLength(2);
  for (const failure of apiGuards) expect(failure).toContain("Film options differ");
  expect(requests(service, "issue_effect_grant")).toHaveLength(grantsBefore);
  expect(requests(service, "motion_canvas_preflight")).toHaveLength(1);
  let panel = await mix(page, a);
  await panel.getByLabel("Original music asset", { exact: true }).selectOption(music!);
  await panel.getByLabel("Original SFX asset", { exact: true }).selectOption(sfx!);
  project = await save(page, service, project.id, "Save original mix recipe");
  const rights = panel.getByRole("checkbox", { name: /^I confirm I have permission to use these exact original sources/ });
  await expect(rights).toBeEnabled(); await rights.check();
  await expect(button(panel, "Create local original PCM audition")).toBeEnabled();
  await expect(panel.getByLabel("Saved global mix and audition policy", { exact: true })).toContainText("-1 dBFS · fixed");
  await field(panel, "SFX gain dB").fill("-7");
  await expect(rights).not.toBeChecked();
  await expect(button(panel, "Create local original PCM audition")).toBeDisabled();
  await expect(panel.getByRole("region", { name: "Original mix audition requirements", exact: true })).toContainText("only saved values");
  const mixGuard = await page.evaluate(async ({ project, profileId }) => {
    const modulePath = "/src/api.ts", api = await import(modulePath);
    const recipe = project.production_design!.delivery_recipes![profileId];
    try { await api.nativeOriginalMixAudition(project, { ...recipe.original_mix, deliverable_profile_id: profileId, sfx_gain_db: -7, owner_attests_preview_rights: true }); return "UNEXPECTED_SUCCESS"; }
    catch (error) { return error instanceof Error ? error.message : String(error); }
  }, { project, profileId: a.id });
  expect(mixGuard).toContain("differ from the saved original mix recipe");
  expect(requests(service, "native_original_mix_audition")).toHaveLength(0);
  // Explicitly discard the pending field; the saved recipe and confirmation
  // remain separate. Confirmations are never recovered with recipe data.
  const pending = panel.locator('details[aria-label="Original mix pending fields"]');
  await pending.locator("summary").click();
  await button(pending, "Keep saved SFX gain dB").click();
  await expect(rights).not.toBeChecked(); await rights.check();
  await mix(page, b); await mix(page, a);
  await expect(rights).not.toBeChecked();
  await page.reload(); panel = await mix(page, a);
  await expect(panel.getByRole("checkbox", { name: /^I confirm I have permission to use these exact original sources/ })).not.toBeChecked();
  const beforeUnsupported = project;
  const sourcePath = info.outputPath("second-measured-voice.wav");
  await writeFile(sourcePath, syntheticWav(4096, 190));
  project = await service.importVoice(project, sourcePath, "Second synthetic measured take"); await rm(sourcePath);
  const originalVoice = beforeUnsupported.audio.active_voice_track_id!, otherVoice = project.audio.voice_tracks.at(-1)!.id;
  project = await service.apply(project, { type: "set_active_voice_track", track_id: originalVoice });
  project = await service.apply(project, { type: "upsert_deliverable", profile: { ...a, voice_track_id: otherVoice } });
  await page.reload(); panel = await mix(page, a);
  await expect(panel.getByRole("region", { name: "Original mix audition requirements", exact: true })).toContainText("bound to a different voice take");
  await expect(button(panel, "Create local original PCM audition")).toBeDisabled();
  project = await service.apply(project, { type: "upsert_deliverable", profile: { ...a, voice_track_id: originalVoice } });
  const sfxAsset = project.assets.find(asset => asset.id === sfx)!;
  project = await service.apply(project, { type: "upsert_semantic_sfx", cue: {
    schema: "motionwright.semantic-sfx/1", id: randomUUID(), scene_id: project.scenes[0].id,
    event: "scene_reveal", anchor: { kind: "scene_start" }, offset_frames: 0,
    source_asset_id: sfx!, source_sha256: sfxAsset.content_sha256!, source_frames: 30,
    gain_milli_db: -8000, attack_frames: 1, release_frames: 2,
    original_source_attestation: "Synthetic author declaration for this test source, not independent rights verification.",
  } });
  await page.reload(); panel = await mix(page, a);
  await expect(panel.getByRole("region", { name: "Original mix audition requirements", exact: true })).toContainText("cannot apply the project's semantic SFX cue schedule");
  await expect(button(panel, "Create local original PCM audition")).toBeDisabled();
  expect(recipeFor(project, a)).toEqual(recipeFor(beforeUnsupported, a));
  expect(requests(service, "native_original_mix_audition")).toHaveLength(0);
  expect(requests(service, "render_motion_canvas")).toHaveLength(0);
  proof.observations.nativeBoundary = { exactFilmIntentRequest: options, apiGuards, mixGuard,
    projection: "actual JSONL unsupported-command rejection; no native projection or renderer invoked",
    audition: "pre-dispatch controls and canonical guard only; no PCM returned or listening quality claimed" };
  await page.screenshot({ path: info.outputPath("saved-mix-policy-and-unsupported-cues.png"), fullPage: true });
}));

test("portable export and verified import preserve both recipes and CAS while isolating the new generation's drafts", async ({ page, browser }, info) => scenario(info, async proof => {
  const source = await proof.create("source-service", true);
  const initial = await setup(proof, source, true);
  const { a, b, music, sfx } = initial;
  let donor = await seedRecipe(source, initial.project, a, true, music, sfx);
  donor = await seedRecipe(source, donor, b, true, music, sfx);
  await proof.attach(source, page); await mix(page, a);
  await field(page, "SFX gain dB").fill("-6.25");
  donor = await save(page, source, donor.id, "Save original mix recipe");
  const donorHistory = await source.history(donor.id, 0, 256);
  const bundlePath = info.outputPath("canonical-recipes.motionwright");
  await film(page, a);
  await field(page, "Portable export path").fill(bundlePath);
  await button(page, "Export bundle").click();
  await expect(page.locator(".portable-project .portable-message")).toContainText("Portable bundle written to");
  const manifest = JSON.parse(await readFile(join(bundlePath, "manifest.json"), "utf8")) as {
    backup: { project: Project }; blobs: { relative_path: string; sha256: string; size_bytes: number }[];
  };
  expect(manifest.backup.project).toEqual(donor);
  expect(manifest.blobs).toHaveLength(donor.assets.length);
  for (const blob of manifest.blobs) {
    const bytes = await readFile(join(bundlePath, blob.relative_path));
    expect(digest(bytes)).toBe(blob.sha256); expect(bytes.length).toBe(blob.size_bytes);
  }
  await mix(page, a); await field(page, "SFX gain dB").fill("-13"); // Old generation's unsaved input.
  const target = await proof.create("target-service", true);
  // Source and destination stores require independent remembered project selections.
  const targetContext = await browser.newContext({ baseURL: info.project.use.baseURL });
  const targetPage = await targetContext.newPage();
  const targetOriginal = (await target.fixtureBootstrap()).project;
  expect(targetOriginal.id).not.toBe(donor.id);
  await proof.attach(target, targetPage); await button(targetPage, "Deliver").click();
  await field(targetPage, "Portable import path").fill(bundlePath);
  await button(targetPage, "Inspect bundle").click();
  await expect(targetPage.getByLabel("Portable bundle preflight", { exact: true })).toContainText(donor.title);
  await button(targetPage, "Import verified bundle").click();
  await revision(targetPage, donor.revision);
  let imported = await target.project(donor.id); proof.track(target, imported);
  expect(imported.generation).not.toBe(donor.generation);
  expect(imported).toEqual({ ...donor, generation: imported.generation, updated_at: imported.updated_at });
  expect(await target.history(imported.id, 0, 256)).toEqual(donorHistory);
  expect(await target.project(targetOriginal.id)).toEqual(targetOriginal);
  await verifyCas(target, imported);
  await mix(targetPage, a);
  await expect(field(targetPage, "SFX gain dB")).toHaveValue("-6.25");
  await expect(field(page, "SFX gain dB")).toHaveValue("-13");
  await field(targetPage, "SFX gain dB").fill("-3");
  imported = await save(targetPage, target, imported.id, "Save original mix recipe");
  expect(recipeFor(imported, a)).toEqual({ ...recipeFor(donor, a), original_mix: { ...recipeFor(donor, a)!.original_mix, sfx_gain_db: -3 } });
  expect(recipeFor(imported, b)).toEqual(recipeFor(donor, b));
  expect(outsideRecipes(imported)).toEqual(outsideRecipes({ ...donor, generation: imported.generation }));
  expect(await source.project(donor.id)).toEqual(donor);
  await expect(target.importBundle(bundlePath)).rejects.toThrow(/exists|already/i);
  expect(await target.project(imported.id)).toEqual(imported);
  await reopenExact(target, imported);
  await targetPage.reload(); await mix(targetPage, a);
  await expect(field(targetPage, "SFX gain dB")).toHaveValue("-3");
  expect(await target.project(targetOriginal.id)).toEqual(targetOriginal);
  proof.observations.portable = { donor, imported, sourceDraft: "-13", targetCanonicalGain: -3,
    sourceOriginalsRemoved: true, bundleBlobs: manifest.blobs };
  await targetContext.close();
}));
