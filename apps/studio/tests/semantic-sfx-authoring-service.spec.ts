import { createHash, randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test, type Locator, type Page, type TestInfo } from "@playwright/test";
import type { Asset, Change, DeliverableProfile, Project, RationalTime, Scene } from "../src/types";
import { defaultOriginalMix, type DeliveryRecipe } from "../src/deliveryRecipe";
import { SFX_CUE_SCHEMA } from "../src/semanticSfx";
import {
  SFX_COPY_PREFIX, sfxCopyKey, sfxPointerKey, sfxScope,
  type SfxCopy, type SfxCue,
} from "../src/semanticSfxDraft";
import {
  ScopedChangeService, type ServiceRpcRequest, type StoreFinalization,
} from "./support/scopedChangeService";

// Actual App -> api.ts -> source-built StudioService -> SQLite/CAS/journal.
// Synthetic stereo PCM is imported through the existing real file-import harness.
// Faults alter browser custody or delivery of a real reply; no Project is fabricated.
// Includes explicit cross-cut tail persistence with the domain contract.
// These cases do not certify a native mix, PCM realization or audio quality.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
  "Requires the declared source-built StudioService executable; no simulated persistence fallback.");
test.setTimeout(120_000);

declare global {
  interface Window {
    __SFX_SERVICE_RESTORE__?: () => void;
    __SFX_SERVICE_DROPPED_WRITES__?: number;
    __SFX_SERVICE_BLOCKED_READS__?: number;
  }
}

const button = (scope: Page | Locator, name: string) => scope.getByRole("button", { name, exact: true });
const field = (scope: Page | Locator, name: string) => scope.getByLabel(name, { exact: true });
const workspace = (page: Page) => page.getByRole("region", { name: "Sound events", exact: true });
const digest = (value: string | Buffer) => createHash("sha256").update(value).digest("hex");
const detail = (reason: unknown) => reason instanceof Error
  ? { name: reason.name, message: reason.message, stack: reason.stack } : { message: String(reason) };

interface ApplyRequest {
  project_id: string; generation: string; revision: number;
  request_id: string; effect_grant: string; change: Change;
}
function applies(service: ScopedChangeService): { rpc: ServiceRpcRequest; request: ApplyRequest }[] {
  return service.transcript.flatMap(entry => {
    if (entry.direction !== "request" || !("command" in entry.payload) || entry.payload.command !== "apply_change") return [];
    const request = entry.payload.args?.request as ApplyRequest | undefined;
    if (!request) throw new Error("An actual apply_change request lacks its request envelope.");
    return [{ rpc: entry.payload, request }];
  });
}
function mutationCounts(service: ScopedChangeService) {
  return {
    apply: applies(service).length,
    projectEditGrants: service.transcript.filter(entry => entry.direction === "request" &&
      "command" in entry.payload && entry.payload.command === "issue_effect_grant" &&
      (entry.payload.args?.request as { effect?: string } | undefined)?.effect === "project_edit").length,
  };
}
async function revision(page: Page, value: number) {
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);
  await page.evaluate(() => new Promise<void>(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
async function openSound(page: Page, target = "new"): Promise<Locator> {
  await button(page, "Audio").click();
  const root = workspace(page);
  await expect(root).toBeVisible();
  if (target === "new") await button(root, "New sound event").click();
  else await root.locator(".sfx-target-button").filter({ has: page.getByText(target, { exact: true }) }).click();
  const editor = root.locator(".sfx-workspace .sfx-draft");
  await expect(field(editor, "SFX Offset frames")).toBeVisible();
  return editor;
}
async function ownCopy(page: Page, project: Project, target: string) {
  const scope = sfxScope(project, target);
  const writerId = await page.evaluate(key => sessionStorage.getItem(key), sfxPointerKey(scope));
  if (!writerId) throw new Error("The SFX draft has no confirmed writer pointer.");
  const key = sfxCopyKey(scope, writerId);
  const raw = await page.evaluate(key => localStorage.getItem(key), key);
  if (raw === null) throw new Error("The actual SFX writer has no retained recovery bytes.");
  const value = JSON.parse(raw) as SfxCopy;
  expect(value.schema).toBe("motionwright.semantic-sfx-recovery/1");
  expect(value.scope).toBe(scope);
  expect(value.writerId).toBe(writerId);
  expect(value.draft.scope).toBe(scope);
  expect(value.draft.target).toBe(target);
  return { key, raw, value, sha256: digest(raw) };
}
async function allCopies(page: Page, project: Project, target: string) {
  return page.evaluate(prefix => Object.keys(localStorage).filter(key => key.startsWith(prefix)).sort().map(key => {
    const raw = localStorage.getItem(key);
    if (raw === null) throw new Error("A retained SFX copy disappeared during observation.");
    return { key, raw, value: JSON.parse(raw) as SfxCopy };
  }), SFX_COPY_PREFIX + sfxScope(project, target) + ":");
}
async function unchangedRaw(page: Page, copy: { key: string; raw: string }) {
  expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).toBe(copy.raw);
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: unknown) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
async function bounded<T>(promise: Promise<T>, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([promise, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error(label + " exceeded its 15-second observation bound.")), 15_000);
    })]);
  } finally { clearTimeout(timer); }
}
function holdRemovalReply(service: ScopedChangeService) {
  const original = service.invoke.bind(service);
  const release = deferred<void>(), committed = deferred<Project>(), finished = deferred<void>();
  void committed.promise.catch(() => undefined);
  let armed = true, matched = false;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    const request = args.request as ApplyRequest | undefined;
    if (!armed || command !== "apply_change" || request?.change.type !== "remove_semantic_sfx") return original<T>(command, args);
    armed = false; matched = true;
    try {
      const actual = await original<Project>(command, args);
      committed.resolve(actual);
      await release.promise;
      return actual as T;
    } catch (reason) { committed.reject(reason); throw reason; }
    finally { finished.resolve(); }
  };
  return {
    committed: committed.promise,
    release: () => release.resolve(),
    async dispose() {
      service.invoke = original;
      release.resolve();
      if (matched) await bounded(finished.promise, "Completion of the held real removal reply");
    },
  };
}

/** Same RIFF/PCM arrangement used by file-import-service.spec.ts; actual imported bytes. */
function syntheticStereoWav(frequency: number): Buffer {
  const sampleRate = 48_000, frames = sampleRate * 2, channels = 2, bytes = frames * channels * 2;
  const result = Buffer.alloc(44 + bytes);
  result.write("RIFF", 0); result.writeUInt32LE(36 + bytes, 4); result.write("WAVEfmt ", 8);
  result.writeUInt32LE(16, 16); result.writeUInt16LE(1, 20); result.writeUInt16LE(channels, 22);
  result.writeUInt32LE(sampleRate, 24); result.writeUInt32LE(sampleRate * channels * 2, 28);
  result.writeUInt16LE(channels * 2, 32); result.writeUInt16LE(16, 34);
  result.write("data", 36); result.writeUInt32LE(bytes, 40);
  for (let frame = 0; frame < frames; frame++) {
    result.writeInt16LE(Math.round(8_000 * Math.sin(2 * Math.PI * frequency * frame / sampleRate)), 44 + frame * 4);
    result.writeInt16LE(Math.round(5_000 * Math.sin(2 * Math.PI * (frequency + 41) * frame / sampleRate)), 46 + frame * 4);
  }
  return result;
}
function exactFrames(time: RationalTime): number {
  const numerator = BigInt(time.num), denominator = BigInt(time.den);
  if (denominator <= 0n || numerator < 0n || (numerator * 30n) % denominator !== 0n)
    throw new Error("Synthetic SFX arrangement needs an exact nonnegative 30-fps source interval.");
  const result = numerator * 30n / denominator;
  if (result > 36_000n) throw new Error("Synthetic SFX arrangement exceeds the source timeline bound.");
  return Number(result);
}
function fixtureCue(scene: Scene, asset: Asset, event: SfxCue["event"], offset: number): SfxCue {
  if (!asset.content_sha256) throw new Error("The real imported asset has no content digest.");
  return {
    schema: SFX_CUE_SCHEMA, id: randomUUID(), scene_id: scene.id, event,
    anchor: { kind: "scene_start" }, offset_frames: offset,
    source_asset_id: asset.id, source_sha256: asset.content_sha256, source_frames: 30,
    gain_milli_db: -12000, attack_frames: 3, release_frames: 6,
    original_source_attestation: "Synthetic stereo PCM created by this service test for persistence coverage.",
  };
}
function fallbackProfile(): DeliverableProfile {
  return {
    id: randomUUID(), name: "Synthetic SFX continuity profile", width: 1920, height: 1080,
    language: "en", captions: true, caption_format: "web_vtt", video_codec: "h264",
    audio_codec: "aac", audio_sample_rate_hz: 48000, brand_profile: null, cut_label: null,
    parent_profile_id: null, source_revision: null, framing_strategy: "replan", crop_approved: false,
    timing_locked: false, voice_track_id: null, text_overrides: {}, included_scene_ids: [],
    protected_scene_ids: [], burn_in_captions: false, frame_rate: { num: "30", den: "1" },
    color_space: "rec709", container: "mp4", adaptation_notes: [],
  };
}
function preservedNeighbours(before: Project, after: Project) {
  const { sfx_cues: _beforeCues, ...beforeAudio } = before.audio;
  const { sfx_cues: _afterCues, ...afterAudio } = after.audio;
  expect(afterAudio).toEqual(beforeAudio);
  expect(after.assets).toEqual(before.assets); // Includes identity, digest and array order.
  expect(after.scenes).toEqual(before.scenes);
  expect(after.deliverables).toEqual(before.deliverables);
  expect(after.production_design?.delivery_recipes).toEqual(before.production_design?.delivery_recipes);
}

interface SfxCase {
  service: ScopedChangeService; baseline: Project; scene: Scene;
  cue: SfxCue; other: SfxCue; assetA: Asset; assetB: Asset; editor: Locator;
  initialCounts: ReturnType<typeof mutationCounts>; observations: Record<string, unknown>;
  cleanup: (() => Promise<unknown>)[];
  observe(label: string): Promise<Project>;
  restart(expected: Project, target?: string): Promise<Locator>;
}
async function withSfx(page: Page, info: TestInfo, run: (value: SfxCase) => Promise<void>) {
  const service = await ScopedChangeService.create({
    databasePath: info.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
  });
  const observations: Record<string, unknown> = {};
  const cleanup: (() => Promise<unknown>)[] = [], failures: unknown[] = [];
  const browserLogs: { kind: string; text: string; truncated: boolean }[] = [];
  let browserLogCount = 0, final: Project | undefined, projectId: string | undefined;
  let primary: { reason: unknown } | undefined, custody: StoreFinalization | undefined;
  const recordLog = (kind: string, text: string) => {
    browserLogCount++;
    browserLogs.push({ kind, text: text.slice(0, 2000), truncated: text.length > 2000 });
    if (browserLogs.length > 128) browserLogs.shift();
  };
  page.on("console", entry => recordLog(entry.type(), entry.text()));
  page.on("pageerror", error => recordLog("pageerror", error.message));
  try {
    const boot = await service.fixtureBootstrap();
    expect(boot.test_transport).toBe("service-test-jsonl");
    expect(boot.fixture.label).toBe("synthetic_test_fixture");
    let project = boot.project;
    projectId = project.id; final = project;
    for (const scene of project.scenes) {
      if (scene.renderer !== "motion-canvas") {
        project = await service.apply(project, { type: "set_scene_renderer", scene_id: scene.id, renderer: "motion-canvas" });
        final = project;
      }
    }
    const scene = project.scenes.find(value => {
      try { return exactFrames(value.duration) >= 90 && exactFrames(value.start) + 60 <= 36_000; }
      catch { return false; }
    });
    if (!scene) throw new Error("The real service fixture has no exact source scene suitable for two bounded SFX cues.");
    const assets: Asset[] = [], inputs: { path: string; sha256: string; bytes: number }[] = [];
    for (const [index, frequency] of [240, 390].entries()) {
      const path = info.outputPath("synthetic-sfx-source-" + (index + 1) + ".wav");
      const bytes = syntheticStereoWav(frequency), sha256 = digest(bytes);
      await writeFile(path, bytes);
      const existing = new Set(project.assets.map(asset => asset.id));
      project = await service.importAsset(project, path, "Synthetic SFX source " + (index + 1), "audio/wav");
      final = project;
      const added = project.assets.filter(asset => !existing.has(asset.id));
      expect(added).toHaveLength(1);
      expect(added[0].content_sha256).toBe(sha256);
      expect(added[0].media_type).toBe("audio/wav");
      assets.push(added[0]); inputs.push({ path, sha256, bytes: bytes.length });
    }
    const profile = project.deliverables[0] ?? fallbackProfile();
    if (!project.deliverables.length) {
      project = await service.apply(project, { type: "upsert_deliverable", profile }); final = project;
    }
    const recipe: DeliveryRecipe = {
      schema: "motionwright.delivery-recipe/1", original_mix: defaultOriginalMix(),
      scene_intents: project.scenes.map((item, index) => ({
        scene_id: item.id, role: index === 0 ? "hook" : "problem",
        archetype: index === 0 ? "statement" : "diagram_build",
      })),
    };
    project = await service.apply(project, { type: "set_delivery_recipe", deliverable_profile_id: profile.id, recipe });
    final = project;
    const first = fixtureCue(scene, assets[0], "scene_reveal", 0);
    const second = fixtureCue(scene, assets[1], "impact", 24);
    for (const cue of [first, second]) {
      project = await service.apply(project, { type: "upsert_semantic_sfx", cue }); final = project;
    }
    const baseline = project;
    const cue = baseline.audio.sfx_cues?.find(value => value.id === first.id);
    const other = baseline.audio.sfx_cues?.find(value => value.id === second.id);
    if (!cue || !other) throw new Error("The real service did not retain both arranged sound identities.");
    const retainedRecipe = baseline.production_design?.delivery_recipes?.[profile.id];
    expect(retainedRecipe?.scene_intents).toHaveLength(baseline.scenes.length);
    expect(retainedRecipe?.original_mix).toBeDefined();
    observations.arrangement = {
      label: "synthetic_stereo_pcm_and_real_service_authored_events", inputs,
      sampleRateHz: 48000, channels: 2, durationSeconds: 2,
      sceneId: scene.id, cueIds: [cue.id, other.id], recipeProfileId: profile.id,
    };
    observations.baseline = baseline;
    const checkpoints: unknown[] = [], restarts: unknown[] = [];
    observations.checkpoints = checkpoints; observations.restarts = restarts;
    const observe = async (label: string) => {
      if (checkpoints.length >= 16) throw new Error("This bounded service scenario exceeded 16 observation checkpoints.");
      final = await service.project(baseline.id);
      checkpoints.push({ label, project: final, journal: await service.history(baseline.id, baseline.revision) });
      return final;
    };
    const restart = async (expected: Project, target = cue.id) => {
      const before = await service.observeStoredProject(expected);
      const exitsBefore = service.processExits.length;
      await service.close();
      expect(service.processExits).toHaveLength(exitsBefore + 1);
      expect(service.processExits.at(-1)).toMatchObject({ code: 0, signal: null });
      await service.start();
      const reopened = await service.observeStoredProject(expected);
      expect(reopened.project).toEqual(before.project);
      expect(reopened.journal).toEqual(before.journal);
      restarts.push({
        expectedRevision: expected.revision, observedExit: service.processExits.at(-1),
        beforeJournalSha256: digest(JSON.stringify(before.journal)),
        reopenedJournalSha256: digest(JSON.stringify(reopened.journal)),
      });
      await page.reload(); await revision(page, expected.revision);
      const next = await openSound(page, target);
      final = reopened.project;
      return next;
    };
    await service.attachFixturePage(page, baseline.id);
    await page.goto("/"); await revision(page, baseline.revision);
    const editor = await openSound(page, cue.id);
    await run({
      service, baseline, scene, cue, other, assetA: assets[0], assetB: assets[1], editor,
      initialCounts: mutationCounts(service), observations, cleanup, observe, restart,
    });
  } catch (reason) { primary = { reason }; failures.push(reason); }
  finally {
    for (const action of cleanup) { try { await action(); } catch (reason) { failures.push(reason); } }
    if (projectId) { try { final = await service.project(projectId); } catch (reason) { failures.push(reason); } }
    try { custody = await service.finalizeClosedStore(info.outputPath("closed-store"), final, [], [() => page.close()]); }
    catch (reason) { failures.push(reason); }
    // Bounded diagnostic excerpts are explicitly labelled. The finalizer retains
    // the complete Project, contiguous journal and closed SQLite/CAS separately.
    try {
      const transcript = service.transcript.slice(-256).map(entry => {
        const raw = JSON.stringify(entry.payload);
        return {
          at: entry.at, processId: entry.processId, direction: entry.direction,
          sha256: digest(raw), payloadCharacters: raw.length,
          excerpt: raw.slice(0, 4096), truncated: raw.length > 4096,
        };
      });
      const path = info.outputPath("semantic-sfx-authoring-service-evidence.json");
      await writeFile(path, JSON.stringify({
        scope: "actual App + api.ts + source-built StudioService + grants + SQLite/CAS + journal",
        inputs: "synthetic stereo PCM; within_scene and explicit cross-cut tail persistence",
        exclusions: ["installed Tauri", "native mix or audio quality", "native cross-cut tail PCM realization", "human creative acceptance"],
        statusAtTeardown: info.status, expectedStatus: info.expectedStatus,
        observations, final, primaryFailure: primary ? detail(primary.reason) : null,
        failures: failures.map(detail), closedStoreOracle: custody?.oracle ?? null,
        service: {
          databasePath: service.databasePath, binaryPath: service.binaryPath,
          processExits: service.processExits, lifecycleTaints: service.lifecycleTaints,
          transcriptCount: service.transcript.length, transcriptRetained: transcript.length,
          transcriptEntryLimit: 256, transcriptExcerptCharacterLimit: 4096, transcript,
          stderrCharacters: service.stderr.length, stderrTailCharacterLimit: 32768, stderrTail: service.stderr.slice(-32768),
        },
        browser: { totalEntries: browserLogCount, entryLimit: 128, characterLimit: 2000, entries: browserLogs },
      }, null, 2));
      await info.attach("semantic-sfx-authoring-service-evidence", { path, contentType: "application/json" });
    } catch (reason) { failures.push(reason); }
    try {
      await info.attach("closed-store-outcomes", {
        path: info.outputPath("closed-store-outcomes.json"), contentType: "application/json",
      });
    } catch (reason) { failures.push(reason); }
    if (failures.length === 1) throw failures[0];
    if (failures.length > 1) throw new AggregateError(failures,
      "SFX behavior and closed-store custody retained separate failures", { cause: primary?.reason });
  }
}

test("SFX edits preserve the stable ID, other cues, imported asset order and a nonempty recipe through service restart", async ({ page }, info) => {
  await withSfx(page, info, async ({ service, baseline, cue, other, assetB, editor, initialCounts, observe, restart }) => {
    await expect(field(editor, "SFX source scene")).toBeDisabled();
    await field(editor, "SFX semantic event").selectOption("confirmation");
    await field(editor, "SFX imported WAV").selectOption(assetB.id);
    await expect(field(editor, "SFX Source asset ID")).toHaveValue(assetB.id);
    await expect(field(editor, "SFX Source SHA-256")).toHaveValue(assetB.content_sha256!);
    await field(editor, "SFX Gain milli dB").fill("-9000");
    await button(editor, "Save sound event").click(); await revision(page, baseline.revision + 1);
    const first = await observe("explicit same-ID source and event edit");
    expect(first.audio.sfx_cues?.map(value => value.id)).toEqual(baseline.audio.sfx_cues?.map(value => value.id));
    expect(first.audio.sfx_cues?.find(value => value.id === cue.id)).toEqual({
      ...cue, event: "confirmation", source_asset_id: assetB.id,
      source_sha256: assetB.content_sha256, gain_milli_db: -9000,
    });
    expect(first.audio.sfx_cues?.find(value => value.id === other.id)).toEqual(other);
    preservedNeighbours(baseline, first);
    await button(page, "Brief").click(); await openSound(page, cue.id);
    await expect(field(editor, "SFX Gain milli dB")).toHaveValue("-9000");
    await field(editor, "SFX Release frames").fill("9");
    await button(editor, "Save sound event").click(); await revision(page, baseline.revision + 2);
    const saved = await observe("second explicit edit keeps the same identity and array position");
    expect(saved.audio.sfx_cues?.map(value => value.id)).toEqual(baseline.audio.sfx_cues?.map(value => value.id));
    expect(saved.audio.sfx_cues?.find(value => value.id === cue.id)).toEqual({
      ...first.audio.sfx_cues!.find(value => value.id === cue.id)!, release_frames: 9,
    });
    expect(saved.audio.sfx_cues?.filter(value => value.id !== cue.id))
      .toEqual(baseline.audio.sfx_cues?.filter(value => value.id !== cue.id));
    preservedNeighbours(baseline, saved);
    const requests = applies(service).slice(initialCounts.apply);
    expect(requests.map(value => value.request.change.type)).toEqual(["upsert_semantic_sfx", "upsert_semantic_sfx"]);
    expect(requests[0].request.request_id).not.toBe(requests[1].request.request_id);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type))
      .toEqual(["upsert_semantic_sfx", "upsert_semantic_sfx"]);
    const reopened = await restart(saved);
    await expect(field(reopened, "SFX Release frames")).toHaveValue("9");
    expect(applies(service)).toHaveLength(initialCounts.apply + 2);
  });
});

test("an explicit cross-cut SFX tail survives UI save, reload and editing with the real project service", async ({ page }, info) => {
  const requiredComposition = "Service support for SfxTailPolicy, source identity and selected-delivery-cut admission is required.";
  info.annotations.push({ type: "requires", description: requiredComposition });
  await withSfx(page, info, async ({ service, baseline, assetA, initialCounts, observations, observe, restart }) => {
    observations.requiredComposition = requiredComposition;
    // Author both sides of a bounded cut through the actual service. Existing
    // explicit profiles exclude these new IDs; complete profiles include both.
    let arranged = baseline;
    const addedScenes: Scene[] = [];
    for (const name of ["Synthetic SFX tail source", "Synthetic SFX tail receiving scene"]) {
      const priorIds = new Set(arranged.scenes.map(scene => scene.id));
      arranged = await service.apply(arranged, {
        type: "add_scene", name, objective: "Persist an explicit sound tail across this authored cut.", duration_seconds: 1,
      });
      const added = arranged.scenes.filter(scene => !priorIds.has(scene.id));
      expect(added).toHaveLength(1);
      expect(exactFrames(added[0].duration)).toBe(30);
      expect(added[0].renderer).toBe("motion-canvas");
      addedScenes.push(added[0]);
    }
    const [sourceScene, receivingScene] = addedScenes;
    const sourceStart = exactFrames(sourceScene.start), sourceFrames = exactFrames(sourceScene.duration);
    expect(exactFrames(receivingScene.start)).toBe(sourceStart + sourceFrames);
    expect(arranged.locks.filter(lock => ["content", "timing"].includes(lock.kind) &&
      ["project:" + arranged.id, "scene:" + sourceScene.id].includes(lock.resource))).toEqual([]);
    for (const profile of arranged.deliverables) {
      expect(profile.included_scene_ids).not.toContain(sourceScene.id);
      expect(profile.included_scene_ids).not.toContain(receivingScene.id);
    }
    const profile: DeliverableProfile = {
      ...fallbackProfile(), name: "Synthetic two-scene SFX tail cut",
      included_scene_ids: [sourceScene.id, receivingScene.id],
    };
    arranged = await service.apply(arranged, { type: "upsert_deliverable", profile });
    expect(arranged.deliverables.find(value => value.id === profile.id)).toEqual(profile);
    expect(arranged.revision).toBe(baseline.revision + 3);
    expect(arranged.audio).toEqual(baseline.audio);
    expect(arranged.assets).toEqual(baseline.assets);
    expect(arranged.production_design?.delivery_recipes).toEqual(baseline.production_design?.delivery_recipes);
    const selectedFrames = arranged.scenes.filter(scene => profile.included_scene_ids.includes(scene.id))
      .reduce((total, scene) => total + exactFrames(scene.duration), 0);
    const onsetInCut = 15, tailFrames = 30, endInCut = onsetInCut + tailFrames;
    expect(selectedFrames).toBe(60);
    expect(onsetInCut).toBeLessThan(sourceFrames);
    expect(endInCut).toBeGreaterThan(sourceFrames);
    expect(endInCut).toBeLessThanOrEqual(selectedFrames);
    expect(sourceStart + endInCut).toBeLessThanOrEqual(36_000);
    expect(sourceStart + endInCut).toBeLessThanOrEqual(
      arranged.scenes.reduce((total, scene) => total + exactFrames(scene.duration), 0));
    const counts = mutationCounts(service);
    expect(counts.apply).toBe(initialCounts.apply + 3);
    expect(counts.projectEditGrants).toBe(initialCounts.projectEditGrants + 3);
    observations.crossCutArrangement = {
      project: arranged, sourceSceneId: sourceScene.id, receivingSceneId: receivingScene.id,
      profileId: profile.id, sourceStartFrame: sourceStart, sourceSceneFrames: sourceFrames,
      selectedCutFrames: selectedFrames, onsetInSelectedCut: onsetInCut, endInSelectedCut: endInCut,
      importedWavFramesAt30Fps: 60, requestedSourceFrames: tailFrames,
      scope: "Authored placement and persistence only; no native PCM render or readback.",
    };
    await page.reload(); await revision(page, arranged.revision);
    const editor = await openSound(page, "new");
    const attestation = "Synthetic stereo PCM imported by this test; explicit tail persistence coverage only.";
    await field(editor, "SFX source scene").selectOption(sourceScene.id);
    await field(editor, "SFX semantic event").selectOption("transition");
    await field(editor, "SFX event anchor").selectOption("scene_start");
    await field(editor, "SFX imported WAV").selectOption(assetA.id);
    await field(editor, "SFX Offset frames").fill(String(onsetInCut));
    await field(editor, "SFX Source frames").fill(String(tailFrames));
    await field(editor, "SFX Gain milli dB").fill("-12000");
    await field(editor, "SFX Attack frames").fill("3");
    await field(editor, "SFX Release frames").fill("6");
    await field(editor, "SFX Original source attestation").fill(attestation);
    await expect(field(editor, "SFX Tail policy")).toHaveValue("within_scene");
    await expect(editor.getByRole("status").filter({ hasText: "SFX source tail leaves its authored scene interval." })).toBeVisible();
    await expect(button(editor, "Add sound event")).toBeDisabled();
    expect(mutationCounts(service)).toEqual(counts);
    expect(await observe("default scene-local policy did not admit the cross-cut tail")).toEqual(arranged);
    await field(editor, "SFX Tail policy").selectOption("continue_across_cuts");
    await expect(button(editor, "Add sound event"), requiredComposition).toBeEnabled();
    const rawBeforeAdd = await ownCopy(page, arranged, "new");
    expect(rawBeforeAdd.value.draft.raw.tailPolicy).toBe("continue_across_cuts");
    observations.crossCutBeforeAdd = rawBeforeAdd;
    await button(editor, "Add sound event").click();
    await expect(page.locator(".command-bar .revision-chip"), requiredComposition).toHaveText("r" + (arranged.revision + 1));
    const saved = await observe("explicit cross-cut tail admitted by the domain service");
    const added = (saved.audio.sfx_cues ?? []).filter(cue => !(arranged.audio.sfx_cues ?? []).some(prior => prior.id === cue.id));
    expect(added).toHaveLength(1);
    const savedCue: SfxCue = added[0];
    expect(savedCue).toEqual({
      schema: SFX_CUE_SCHEMA, id: savedCue.id, scene_id: sourceScene.id, event: "transition",
      anchor: { kind: "scene_start" }, offset_frames: onsetInCut,
      source_asset_id: assetA.id, source_sha256: assetA.content_sha256, source_frames: tailFrames,
      gain_milli_db: -12000, attack_frames: 3, release_frames: 6,
      tail_policy: "continue_across_cuts", original_source_attestation: attestation,
    });
    expect(saved.audio.sfx_cues?.filter(cue => cue.id !== savedCue.id)).toEqual(arranged.audio.sfx_cues);
    preservedNeighbours(arranged, saved);
    const afterAddCounts = mutationCounts(service);
    expect(afterAddCounts.apply).toBe(counts.apply + 1);
    expect(afterAddCounts.projectEditGrants).toBe(counts.projectEditGrants + 1);
    await page.reload(); await revision(page, saved.revision); await openSound(page, savedCue.id);
    await expect(field(editor, "SFX Tail policy")).toHaveValue("continue_across_cuts");
    await expect(field(editor, "SFX Offset frames")).toHaveValue("15");
    await expect(field(editor, "SFX Source frames")).toHaveValue("30");
    expect(await observe("reload retained the cross-cut policy without repeating Add")).toEqual(saved);
    expect(mutationCounts(service)).toEqual(afterAddCounts);
    await field(editor, "SFX Gain milli dB").fill("-9000");
    await expect(field(editor, "SFX Tail policy")).toHaveValue("continue_across_cuts");
    const rawBeforeEdit = await ownCopy(page, saved, savedCue.id);
    expect(rawBeforeEdit.value.draft.raw.tailPolicy).toBe("continue_across_cuts");
    observations.crossCutBeforeEdit = rawBeforeEdit;
    await expect(button(editor, "Save sound event")).toBeEnabled();
    await button(editor, "Save sound event").click(); await revision(page, saved.revision + 1);
    const edited = await observe("one explicit gain edit preserves the complete saved cross-cut cue");
    const editedCue: SfxCue = { ...savedCue, gain_milli_db: -9000 };
    expect(edited.audio.sfx_cues?.find(cue => cue.id === savedCue.id)).toEqual(editedCue);
    expect(edited.audio.sfx_cues?.filter(cue => cue.id !== savedCue.id)).toEqual(arranged.audio.sfx_cues);
    preservedNeighbours(arranged, edited);
    const requests = applies(service).slice(counts.apply);
    expect(requests.map(value => value.request.change)).toEqual([
      { type: "upsert_semantic_sfx", cue: savedCue },
      { type: "upsert_semantic_sfx", cue: editedCue },
    ]);
    expect(requests[1].request.request_id).not.toBe(requests[0].request.request_id);
    expect(requests[1].request.effect_grant).not.toBe(requests[0].request.effect_grant);
    expect((await service.history(baseline.id, arranged.revision)).map(event => event.change)).toEqual(
      requests.map(value => value.request.change));
    const countsBeforeRestart = mutationCounts(service);
    expect(countsBeforeRestart).toEqual({ apply: counts.apply + 2, projectEditGrants: counts.projectEditGrants + 2 });
    expect(edited.audio.sfx_cues).toEqual(saved.audio.sfx_cues?.map(cue => cue.id === savedCue.id ? editedCue : cue));
    const reopened = await restart(edited, savedCue.id);
    await expect(field(reopened, "SFX Tail policy")).toHaveValue("continue_across_cuts");
    await expect(field(reopened, "SFX Gain milli dB")).toHaveValue("-9000");
    expect((await service.project(baseline.id)).audio.sfx_cues?.find(cue => cue.id === savedCue.id)).toEqual(editedCue);
    expect(mutationCounts(service)).toEqual(countsBeforeRestart);
  });
});

test("raw SFX 1e survives target navigation, Audio navigation and reload before one explicit Save", async ({ page }, info) => {
  await withSfx(page, info, async ({ service, baseline, cue, other, editor, initialCounts, observations, observe, restart }) => {
    await field(editor, "SFX Offset frames").fill("1e");
    await expect(button(editor, "Save sound event")).toBeDisabled();
    const original = await ownCopy(page, baseline, cue.id);
    observations.rawBeforeNavigation = original;
    await openSound(page, other.id);
    await expect(field(editor, "SFX Offset frames")).toHaveValue(String(other.offset_frames));
    await openSound(page, cue.id);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    await button(page, "Brief").click(); await openSound(page, cue.id);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    await page.reload(); await revision(page, baseline.revision); await openSound(page, cue.id);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    await expect(button(editor, "Save sound event")).toBeDisabled();
    expect((await ownCopy(page, baseline, cue.id)).value.draft.raw.offsetFrames).toBe("1e");
    expect(await observe("incomplete raw draft has no Project effect")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await field(editor, "SFX Offset frames").fill("7");
    await button(editor, "Save sound event").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicit save after completing the raw number");
    expect(saved.audio.sfx_cues?.find(value => value.id === cue.id)).toEqual({ ...cue, offset_frames: 7 });
    expect(saved.audio.sfx_cues?.find(value => value.id === other.id)).toEqual(other);
    preservedNeighbours(baseline, saved);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    await restart(saved);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("7");
  });
});

test("a real Remove with a held reply preserves the later raw draft as a discoverable orphan after reload", async ({ page }, info) => {
  await withSfx(page, info, async ({ service, baseline, cue, editor, initialCounts, observations, cleanup, observe, restart }) => {
    // The unfinished upsert is deliberately invalid; removing an existing cue
    // must still preserve that input and obey removal authority, not upsert parsing.
    await field(editor, "SFX Release frames").fill("1e");
    await expect(button(editor, "Save sound event")).toBeDisabled();
    await expect(button(editor, "Remove sound event")).toBeEnabled();
    const held = holdRemovalReply(service); cleanup.push(held.dispose);
    await button(editor, "Remove sound event").click();
    const removed = await bounded(held.committed, "Actual SQLite removal");
    expect(removed.audio.sfx_cues?.some(value => value.id === cue.id)).toBe(false);
    await field(editor, "SFX Offset frames").fill("1e");
    await field(editor, "SFX Original source attestation").fill("Synthetic draft authored after the actual removal committed.");
    const laterPending = await ownCopy(page, baseline, cue.id);
    observations.laterDraftBeforeRemovalReply = laterPending;
    expect(laterPending.value.draft.pending?.change).toEqual({ type: "remove_semantic_sfx", cue_id: cue.id });
    expect(laterPending.value.draft.sequence).toBeGreaterThan(laterPending.value.draft.pending!.sequence);
    held.release(); await revision(page, removed.revision);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    await expect(field(editor, "SFX Release frames")).toHaveValue("1e");
    await expect(button(editor, "Save sound event")).toBeDisabled();
    await expect(button(editor, "Remove sound event")).toBeDisabled();
    const absent = await ownCopy(page, baseline, cue.id);
    observations.acknowledgedRemovalWithLaterDraft = absent;
    expect(absent.value.draft.pending).toBeNull();
    expect(absent.value.draft.intent).toBe(true);
    expect(absent.value.draft.raw).toEqual(laterPending.value.draft.raw);
    await unchangedRaw(page, laterPending);
    await page.reload(); await revision(page, removed.revision);
    await openSound(page, "new");
    const retainedTarget = workspace(page).locator(".sfx-target-button").filter({ has: page.getByText(cue.id, { exact: true }) });
    const rawLabel = "Raw sound recovery copy " + absent.value.writerId;
    const snapshotRow = editor.locator(".sfx-copy").filter({ has: field(page, rawLabel) });
    await expect(retainedTarget).toContainText("No saved event with this ID");
    await expect(button(workspace(page), "New sound event")).toHaveAttribute("aria-pressed", "true");
    await expect(snapshotRow).toHaveCount(1);
    await expect(field(snapshotRow, rawLabel)).toHaveValue(absent.raw);
    const countsBeforeOpening = mutationCounts(service);
    const journalBeforeOpening = await service.history(baseline.id, baseline.revision);
    // Catalogue reads have completed. Keep only this real copy unreadable until
    // explicitly restored: a one-shot failure could disappear in React's render retry.
    await page.evaluate(target => {
      const original = Storage.prototype.getItem;
      window.__SFX_SERVICE_BLOCKED_READS__ = 0;
      Storage.prototype.getItem = function(key) {
        if (this === window.localStorage && key === target) {
          window.__SFX_SERVICE_BLOCKED_READS__ = (window.__SFX_SERVICE_BLOCKED_READS__ ?? 0) + 1;
          throw new Error("Injected retained SFX copy read failure after catalogue.");
        }
        return original.call(this, key);
      };
      window.__SFX_SERVICE_RESTORE__ = () => { Storage.prototype.getItem = original; };
    }, absent.key);
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__SFX_SERVICE_RESTORE__?.()); });
    await retainedTarget.click();
    await expect(retainedTarget).toHaveAttribute("aria-pressed", "true");
    await expect(editor).toContainText("Injected retained SFX copy read failure after catalogue.");
    await expect(field(editor, "SFX Offset frames")).toHaveCount(0);
    await expect(snapshotRow).toHaveCount(1);
    await expect(field(snapshotRow, rawLabel)).toHaveValue(absent.raw);
    await expect(field(snapshotRow, rawLabel)).toHaveJSProperty("readOnly", true);
    const openingReadFailures = await page.evaluate(() => window.__SFX_SERVICE_BLOCKED_READS__ ?? 0);
    expect(openingReadFailures).toBeGreaterThan(0);
    await button(editor, "Refresh sound recovery copies").click();
    await expect(editor).toContainText("Injected retained SFX copy read failure after catalogue.");
    await expect(field(editor, "SFX Offset frames")).toHaveCount(0);
    await expect(field(snapshotRow, rawLabel)).toHaveValue(absent.raw);
    const blockedReads = await page.evaluate(() => window.__SFX_SERVICE_BLOCKED_READS__ ?? 0);
    expect(blockedReads).toBeGreaterThan(openingReadFailures);
    const recoveryDownload = page.waitForEvent("download");
    await button(snapshotRow, "Export sound recovery copy").click();
    const downloadedCopy = await recoveryDownload;
    const exportPath = info.outputPath("orphan-opening-recovery-copy.json");
    await downloadedCopy.saveAs(exportPath);
    expect(await downloadedCopy.failure()).toBeNull();
    const exportedBytes = await readFile(exportPath);
    expect(exportedBytes.equals(Buffer.from(absent.raw, "utf8"))).toBe(true);
    expect(digest(exportedBytes)).toBe(absent.sha256);
    await info.attach("orphan-opening-recovery-copy", { path: exportPath, contentType: "application/json" });
    observations.orphanOpeningReadFault = {
      key: absent.key, writerId: absent.value.writerId, sourceSha256: absent.sha256,
      openingReadFailures, blockedReads, countsBeforeOpening,
      export: { path: exportPath, bytes: exportedBytes.length, sha256: digest(exportedBytes) },
    };
    expect(await observe("failed orphan opening and Refresh preserve the saved project")).toEqual(removed);
    expect(await service.history(baseline.id, baseline.revision)).toEqual(journalBeforeOpening);
    expect(mutationCounts(service)).toEqual(countsBeforeOpening);
    await page.evaluate(() => window.__SFX_SERVICE_RESTORE__!());
    // Removing the fault alone cannot reopen or replace the selected draft.
    await expect(field(editor, "SFX Offset frames")).toHaveCount(0);
    await expect(retainedTarget).toHaveAttribute("aria-pressed", "true");
    await button(editor, "Refresh sound recovery copies").click();
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    await expect(field(editor, "SFX Release frames")).toHaveValue("1e");
    await expect(field(editor, "SFX Original source attestation")).toHaveValue("Synthetic draft authored after the actual removal committed.");
    const recovered = await ownCopy(page, baseline, cue.id);
    expect(recovered.key).toBe(absent.key);
    expect(recovered.raw).toBe(absent.raw);
    expect(recovered.value.draft.raw).toEqual(absent.value.draft.raw);
    expect(recovered.value.draft.sequence).toBe(absent.value.draft.sequence);
    expect(recovered.value.draft.pending).toEqual(absent.value.draft.pending);
    observations.orphanAfterExplicitRefresh = recovered;
    await unchangedRaw(page, laterPending);
    await expect(button(editor, "Use saved sound values")).toBeDisabled();
    await expect(button(editor, "Save sound event")).toBeDisabled();
    await expect(button(editor, "Remove sound event")).toBeDisabled();
    expect(await observe("orphan recovery never recreates the removed identity")).toEqual(removed);
    expect(mutationCounts(service)).toEqual(countsBeforeOpening);
    preservedNeighbours(baseline, removed);
    expect(removed.audio.sfx_cues).toEqual(baseline.audio.sfx_cues?.filter(value => value.id !== cue.id));
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["remove_semantic_sfx"]);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    await restart(removed, cue.id);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    await unchangedRaw(page, laterPending);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
  });
});

for (const lock of [
  { kind: "content", scope: "project" },
  { kind: "timing", scope: "scene" },
] as const) {
  test("a real " + lock.scope + " " + lock.kind + " lock keeps SFX raw editable and blocks Save and Remove through restart", async ({ page }, info) => {
    await withSfx(page, info, async ({ service, baseline, scene, cue, initialCounts, observe, restart }) => {
      const locked = await service.apply(baseline, {
        type: "set_lock", resource: lock.scope + ":" + (lock.scope === "project" ? baseline.id : scene.id),
        kind: lock.kind, note: "Synthetic SFX continuity lock.",
      });
      const counts = mutationCounts(service);
      expect(counts.apply).toBe(initialCounts.apply + 1);
      await page.reload(); await revision(page, locked.revision);
      const editor = await openSound(page, cue.id);
      await expect(field(editor, "SFX Gain milli dB")).toBeEditable();
      await field(editor, "SFX Gain milli dB").fill("-7000");
      await expect(button(editor, "Save sound event")).toBeDisabled();
      await expect(button(editor, "Remove sound event")).toBeDisabled();
      await field(editor, "SFX Offset frames").fill("1e");
      await button(page, "Brief").click(); await openSound(page, cue.id);
      await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
      await restart(locked);
      await expect(field(editor, "SFX Gain milli dB")).toHaveValue("-7000");
      await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
      await expect(button(editor, "Save sound event")).toBeDisabled();
      await expect(button(editor, "Remove sound event")).toBeDisabled();
      expect((await ownCopy(page, baseline, cue.id)).value.draft.raw.offsetFrames).toBe("1e");
      expect(await observe("locked author input remains local")).toEqual(locked);
      expect(mutationCounts(service)).toEqual(counts);
      expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["set_lock"]);
    });
  });
}

test("a real committed SFX reply lost at the bridge remains UNKNOWN after reload and needs Read plus a separate explicit Save", async ({ page }, info) => {
  await withSfx(page, info, async ({ service, baseline, cue, editor, initialCounts, observations, cleanup, observe, restart }) => {
    const original = service.invoke.bind(service);
    let armed = true;
    cleanup.push(async () => { service.invoke = original; });
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      const request = args.request as ApplyRequest | undefined;
      if (!armed || command !== "apply_change" || request?.change.type !== "upsert_semantic_sfx") return original<T>(command, args);
      armed = false;
      observations.actualOriginalResponse = await original<Project>(command, args);
      throw new Error("Injected SFX reply loss after the actual SQLite commit.");
    };
    await field(editor, "SFX Gain milli dB").fill("-8000");
    await button(editor, "Save sound event").click();
    await expect(editor.getByRole("heading", { name: "Review the original sound attempt", exact: true })).toBeVisible();
    await expect(button(editor, "Save sound event")).toBeDisabled();
    await field(editor, "SFX Gain milli dB").fill("-7000");
    const originalCopy = await ownCopy(page, baseline, cue.id);
    observations.originalUnknown = originalCopy;
    expect(originalCopy.value.draft.pending?.outcome).toBe("UNKNOWN");
    expect(originalCopy.value.draft.pending?.revision).toBe(baseline.revision);
    expect(originalCopy.value.draft.pending?.raw.gainMilliDb).toBe("-8000");
    expect(originalCopy.value.draft.raw.gainMilliDb).toBe("-7000");
    const committed = await observe("real commit precedes the deliberately rejected bridge reply");
    expect(committed.audio.sfx_cues?.find(value => value.id === cue.id)?.gain_milli_db).toBe(-8000);
    expect(committed.revision).toBe(baseline.revision + 1);
    const originalRequest = applies(service).slice(initialCounts.apply)[0];
    expect(originalRequest.request.change).toEqual(originalCopy.value.draft.pending?.change);
    const realReplies = service.transcript.filter(entry => entry.direction === "response" &&
      "ok" in entry.payload && entry.payload.id === originalRequest.rpc.id);
    expect(realReplies).toHaveLength(1);
    expect("ok" in realReplies[0].payload && realReplies[0].payload.ok).toBe(true);
    await button(page, "Brief").click(); await openSound(page, cue.id);
    await page.reload(); await revision(page, committed.revision); await openSound(page, cue.id);
    await expect(field(editor, "SFX Gain milli dB")).toHaveValue("-7000");
    await expect(editor.getByRole("heading", { name: "Review the original sound attempt", exact: true })).toBeVisible();
    await expect(button(editor, "Save sound event")).toBeDisabled();
    await expect(button(editor, "Remove sound event")).toBeDisabled();
    await expect(button(editor, "Keep original attempt and start a separate sound edit")).toBeDisabled();
    expect((await ownCopy(page, baseline, cue.id)).value.draft.pending).toEqual(originalCopy.value.draft.pending);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    await button(editor, "Read saved sound and history").click();
    await expect(editor).toContainText("The original attempt remains unresolved.");
    await expect(button(editor, "Keep original attempt and start a separate sound edit")).toBeEnabled();
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    await button(editor, "Keep original attempt and start a separate sound edit").click();
    await unchangedRaw(page, originalCopy);
    expect(await service.project(baseline.id)).toEqual(committed);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    await expect(button(editor, "Save sound event")).toBeEnabled();
    await button(editor, "Save sound event").click(); await revision(page, committed.revision + 1);
    const saved = await observe("only a separate explicit save commits the later draft");
    expect(saved.audio.sfx_cues?.find(value => value.id === cue.id)).toEqual({ ...cue, gain_milli_db: -7000 });
    preservedNeighbours(baseline, saved);
    const requests = applies(service).slice(initialCounts.apply);
    expect(requests).toHaveLength(2);
    expect(requests[1].request.revision).toBe(committed.revision);
    expect(requests[1].request.request_id).not.toBe(requests[0].request.request_id);
    expect(requests[1].request.effect_grant).not.toBe(requests[0].request.effect_grant);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type))
      .toEqual(["upsert_semantic_sfx", "upsert_semantic_sfx"]);
    await restart(saved);
    await expect(field(editor, "SFX Gain milli dB")).toHaveValue("-7000");
    await unchangedRaw(page, originalCopy);
    expect(applies(service)).toHaveLength(initialCounts.apply + 2);
  });
});

test("a first SFX pointer read failure can confirm null without losing new raw input or sending an automatic Add", async ({ page }, info) => {
  await withSfx(page, info, async ({ service, baseline, scene, assetA, initialCounts, observations, cleanup, observe, restart }) => {
    const scope = sfxScope(baseline, "new"), pointerKey = sfxPointerKey(scope);
    const emptySlot = () => page.evaluate(({ pointerKey, prefix }) => ({
      pointer: sessionStorage.getItem(pointerKey),
      copies: Object.keys(localStorage).filter(key => key.startsWith(prefix)),
    }), { pointerKey, prefix: SFX_COPY_PREFIX + scope + ":" });
    expect(await emptySlot()).toEqual({ pointer: null, copies: [] });
    const marker = "motionwright.sfx-test.first-empty-read:" + pointerKey;
    await page.addInitScript(({ target, marker }) => {
      if (sessionStorage.getItem(marker)) return;
      sessionStorage.setItem(marker, "armed");
      const original = Storage.prototype.getItem;
      let armed = true;
      Storage.prototype.getItem = function(key) {
        if (armed && this === window.sessionStorage && key === target) {
          armed = false; sessionStorage.setItem(marker, "fired");
          throw new Error("Injected first SFX pointer read failure.");
        }
        return original.call(this, key);
      };
      window.__SFX_SERVICE_RESTORE__ = () => { Storage.prototype.getItem = original; };
    }, { target: pointerKey, marker });
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__SFX_SERVICE_RESTORE__?.()); });
    await page.reload(); await revision(page, baseline.revision);
    const editor = await openSound(page, "new");
    await expect(editor).toContainText("Injected first SFX pointer read failure.");
    expect(await page.evaluate(key => sessionStorage.getItem(key), marker)).toBe("fired");
    await field(editor, "SFX source scene").selectOption(scene.id);
    await field(editor, "SFX semantic event").selectOption("confirmation");
    await field(editor, "SFX event anchor").selectOption("scene_start");
    await field(editor, "SFX imported WAV").selectOption(assetA.id);
    await field(editor, "SFX Offset frames").fill("1e");
    await field(editor, "SFX Source frames").fill("30");
    await field(editor, "SFX Gain milli dB").fill("-12000");
    await field(editor, "SFX Attack frames").fill("3");
    await field(editor, "SFX Release frames").fill("6");
    await field(editor, "SFX Tail policy").selectOption("within_scene");
    await field(editor, "SFX Original source attestation").fill("Synthetic PCM author input retained after the first unavailable recovery read.");
    await expect(button(editor, "Add sound event")).toBeDisabled();
    expect(await emptySlot()).toEqual({ pointer: null, copies: [] });
    expect(await observe("unobserved pointer did not overwrite the empty recovery slot")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await button(editor, "Retry sound recovery copy").click();
    await expect(editor).toContainText("Confirmed the sound recovery copy.");
    const recovered = await ownCopy(page, baseline, "new"); observations.confirmedEmptySlot = recovered;
    expect(recovered.value.draft.raw.offsetFrames).toBe("1e");
    expect(recovered.value.draft.raw.sourceAssetId).toBe(assetA.id);
    expect(recovered.value.draft.raw.sourceSha256).toBe(assetA.content_sha256);
    expect(recovered.value.draft.pending).toBeNull();
    expect(recovered.value.draft.sequence).toBeGreaterThan(0);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await expect(button(editor, "Add sound event")).toBeDisabled(); // The raw number is still incomplete.
    await page.reload(); await revision(page, baseline.revision); await openSound(page, "new");
    await expect(field(editor, "SFX Offset frames")).toHaveValue("1e");
    expect((await ownCopy(page, baseline, "new")).value.draft).toEqual(recovered.value.draft);
    expect(await observe("confirmed raw input survives reload without automatic Add")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await field(editor, "SFX Offset frames").fill("6");
    await button(editor, "Add sound event").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicit Add after confirmed-empty recovery");
    const added = (saved.audio.sfx_cues ?? []).filter(value => !(baseline.audio.sfx_cues ?? []).some(prior => prior.id === value.id));
    expect(added).toHaveLength(1);
    expect(added[0]).toMatchObject({
      scene_id: scene.id, event: "confirmation", offset_frames: 6,
      source_asset_id: assetA.id, source_sha256: assetA.content_sha256,
    });
    expect(saved.audio.sfx_cues?.slice(0, baseline.audio.sfx_cues!.length)).toEqual(baseline.audio.sfx_cues);
    preservedNeighbours(baseline, saved);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["upsert_semantic_sfx"]);
    await restart(saved, added[0].id);
  });
});

test("a silent pending-copy write blocks SFX grant and dispatch; Retry confirms bytes before a separate explicit Save", async ({ page }, info) => {
  await withSfx(page, info, async ({ service, baseline, cue, editor, initialCounts, observations, cleanup, observe, restart }) => {
    await field(editor, "SFX Offset frames").fill("8");
    await expect(button(editor, "Save sound event")).toBeEnabled();
    const before = await ownCopy(page, baseline, cue.id);
    observations.beforeSilentWrite = before;
    await page.evaluate(prefix => {
      const original = Storage.prototype.setItem;
      window.__SFX_SERVICE_DROPPED_WRITES__ = 0;
      Storage.prototype.setItem = function(key, value) {
        if (this === window.localStorage && key.startsWith(prefix)) {
          let hasPending = false;
          try { hasPending = Boolean(JSON.parse(String(value))?.draft?.pending); } catch { /* Unrelated bytes use the real setter. */ }
          if (hasPending) {
            window.__SFX_SERVICE_DROPPED_WRITES__ = (window.__SFX_SERVICE_DROPPED_WRITES__ ?? 0) + 1;
            return; // Deliberate silent failure, including an attempted NOT_SENT backup.
          }
        }
        return original.call(this, key, value);
      };
      window.__SFX_SERVICE_RESTORE__ = () => { Storage.prototype.setItem = original; };
    }, SFX_COPY_PREFIX);
    cleanup.push(async () => { if (!page.isClosed()) await page.evaluate(() => window.__SFX_SERVICE_RESTORE__?.()); });
    await button(editor, "Save sound event").click();
    await expect(editor).toContainText("NOT_SENT");
    await expect(button(editor, "Save sound event")).toBeDisabled();
    await expect(button(editor, "Remove sound event")).toBeDisabled();
    expect(await page.evaluate(() => window.__SFX_SERVICE_DROPPED_WRITES__)).toBeGreaterThan(0);
    await unchangedRaw(page, before);
    expect(await observe("silent recovery failure prevented both grant and apply")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await button(editor, "Retry sound recovery copy").click();
    await expect(button(editor, "Save sound event")).toBeDisabled();
    expect(mutationCounts(service)).toEqual(initialCounts);
    await unchangedRaw(page, before);
    await page.evaluate(() => window.__SFX_SERVICE_RESTORE__!());
    await button(editor, "Retry sound recovery copy").click();
    await expect(editor).toContainText("Confirmed the sound recovery copy.");
    await expect(field(editor, "SFX Offset frames")).toHaveValue("8");
    await expect(button(editor, "Save sound event")).toBeEnabled();
    const confirmed = await ownCopy(page, baseline, cue.id);
    observations.confirmedAfterRetry = confirmed;
    expect(confirmed.value.draft.pending).toBeNull();
    expect(confirmed.value.draft.raw.offsetFrames).toBe("8");
    const retained = await allCopies(page, baseline, cue.id);
    const notSent = retained.find(copy => copy.value.draft.pending?.outcome === "NOT_SENT");
    expect(notSent).toBeDefined();
    expect(notSent!.key).not.toBe(confirmed.key);
    expect(notSent!.value.draft.pending?.change).toEqual({ type: "upsert_semantic_sfx", cue: { ...cue, offset_frames: 8 } });
    observations.retainedNotSent = notSent;
    expect(await observe("Retry only confirmed local bytes and separated the live NOT_SENT intent")).toEqual(baseline);
    expect(mutationCounts(service)).toEqual(initialCounts);
    await button(editor, "Save sound event").click(); await revision(page, baseline.revision + 1);
    const saved = await observe("one explicit Save after verified recovery");
    expect(saved.audio.sfx_cues?.find(value => value.id === cue.id)).toEqual({ ...cue, offset_frames: 8 });
    preservedNeighbours(baseline, saved);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
    expect((await service.history(baseline.id, baseline.revision)).map(event => event.change.type)).toEqual(["upsert_semantic_sfx"]);
    await restart(saved);
    await expect(field(editor, "SFX Offset frames")).toHaveValue("8");
    await unchangedRaw(page, notSent!);
    expect(applies(service)).toHaveLength(initialCounts.apply + 1);
  });
});
