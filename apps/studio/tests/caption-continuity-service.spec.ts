import { createHash, randomUUID } from "node:crypto";
import { access, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type Page, type TestInfo } from "@playwright/test";
import type { CaptionExportResult, Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

// Authored timing fixtures and measured first-party PCM, not recorded speech.
// The actual desktop caption exporter runs through JSONL over real SQLite/CAS.
// Its output is subtitle text; it does not certify native pixels or listening.
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Build studio_review_session; no simulated service fallback.");
test.use({ actionTimeout: 15_000 });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
const revision = (page: Page, value: number) => expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + value);

function timingPcm() {
  const rate = 48_000, frames = rate * 6, bytes = Buffer.alloc(44 + frames * 4);
  bytes.write("RIFF"); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write("WAVEfmt ", 8); bytes.writeUInt32LE(16, 16);
  bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(2, 22); bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * 4, 28);
  bytes.writeUInt16LE(4, 32); bytes.writeUInt16LE(16, 34); bytes.write("data", 36); bytes.writeUInt32LE(frames * 4, 40);
  for (let n = 0; n < frames; n++) {
    bytes.writeInt16LE(Math.round(4000 * Math.sin(n * 2 * Math.PI * 220 / rate)), 44 + n * 4);
    bytes.writeInt16LE(Math.round(3000 * Math.sin(n * 2 * Math.PI * 330 / rate)), 46 + n * 4);
  }
  return bytes;
}

async function finish(info: TestInfo, services: ScopedChangeService[], pages: Page[], observations: unknown) {
  const errors: unknown[] = [], stores: unknown[] = [];
  await Promise.allSettled(pages.map(page => page.close()));
  try {
    for (const service of [...new Map(services.map(item => [item.directory, item])).values()]) {
      try {
        const index = await service.invoke<{ items: { id: string }[]; truncated: boolean }>("list_projects", { request: { cursor: null, limit: 100 } });
        expect(index.truncated).toBe(false);
        const oracles = [];
        for (const row of index.items) oracles.push(await service.observeStoredProject(await service.project(row.id)));
        const peers = services.filter(item => item !== service && item.directory === service.directory);
        const destination = info.outputPath("closed-store-" + stores.length);
        if (oracles.length) stores.push({ oracles, finalization: await service.finalizeClosedStore(destination, oracles[0].project, peers) });
        else { await service.close(); stores.push({ empty: true, capture: await service.captureClosedStore(destination, peers) }); }
      } catch (reason) { errors.push(String(reason)); }
    }
  } finally {
    const closures = await Promise.allSettled(services.map(service => service.close()));
    errors.push(...closures.flatMap(outcome => outcome.status === "rejected" ? [String(outcome.reason)] : []));
    const path = info.outputPath("caption-continuity-evidence.json");
    await writeFile(path, JSON.stringify({ schema: "motionwright.caption-continuity-test/1",
      scope: "real_studio_service_sqlite_cas_and_shared_desktop_caption_export", excludes: ["installed_tauri", "native_burn_in_pixels", "human_acceptance"],
      status: info.status, observations, stores, errors,
      services: services.map(service => ({ binary: service.binaryPath, database: service.databasePath,
        transcript: service.transcript, exits: service.processExits, taints: service.lifecycleTaints, stderr: service.stderr })),
    }, null, 2));
    await info.attach("caption-continuity-evidence", { path, contentType: "application/json" });
    if (errors.length) throw new AggregateError(errors, "Caption continuity store finalization failed");
  }
}

async function exportSidecar(service: ScopedChangeService, project: Project, destination: string, profileId = project.deliverables[0].id) {
  const grant = await service.grant("deliver_local", project, destination);
  return service.invoke<CaptionExportResult>("export_caption_sidecar", { request: {
    project_id: project.id, generation: project.generation, revision: project.revision,
    profile_id: profileId, path: destination, effect_grant: grant.token,
  } });
}
async function addSegment(page: Page, start: string, end: string, text: string) {
  await field(page, "New transcript start").fill(start); await field(page, "New transcript end").fill(end);
  await field(page, "New transcript speaker").fill("Authored timing fixture"); await field(page, "New transcript text").fill(text);
  await button(page, "Add manual segment").click();
}

/** The real exporter finishes; only delivery of its response to the UI is held. */
function holdSidecarReply(service: ScopedChangeService) {
  const invoke = service.invoke.bind(service);
  let entered = false, release!: () => void, markWritten!: (value: CaptionExportResult) => void, markFailed!: (reason: unknown) => void, markFinished!: () => void;
  const wait = new Promise<void>(resolve => { release = resolve; });
  const written = new Promise<CaptionExportResult>((resolve, reject) => { markWritten = resolve; markFailed = reject; });
  const finished = new Promise<void>(resolve => { markFinished = resolve; });
  service.invoke = async <T>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    if (entered || command !== "export_caption_sidecar") return invoke<T>(command, args);
    entered = true;
    try {
      const result = await invoke<CaptionExportResult>(command, args); markWritten(result);
      await wait; return result as T;
    } catch (reason) { markFailed(reason); throw reason; }
    finally { markFinished(); }
  };
  return { written, release, async dispose() { release(); service.invoke = invoke; if (entered) await finished; } };
}

test("fourteen authored revisions retain multi-frame caption spans, output variants and exact sidecars through portable reopen", async ({ page, context }, info) => {
  test.setTimeout(120_000);
  const source = await ScopedChangeService.create({ databasePath: info.outputPath("original", "studio.sqlite3"), seedFixture: false, desktopInvokeFixture: true });
  const services = [source], pages = [page];
  const heldReplies: ReturnType<typeof holdSidecarReply>[] = [];
  const observations: Record<string, unknown> = {};
  try {
    await source.attachPage(page, null); await page.goto("/");
    const library = page.getByRole("dialog", { name: "Project library", exact: true });
    await library.getByLabel("Project title", { exact: true }).fill("Authored caption continuity — estudio");
    await library.getByRole("button", { name: "Create project", exact: true }).click(); await revision(page, 0);
    await button(page, "Add scene").click(); await field(page, "New scene name").fill("Caption timing sequence");
    await page.locator(".add-scene-form").getByRole("button", { name: "Add", exact: true }).click(); await revision(page, 1);
    await button(page, "Audio").click();
    const sourcePath = info.outputPath("authored-timing-pcm.wav"), bytes = timingPcm(); await writeFile(sourcePath, bytes);
    await field(page, "Voice file absolute path").fill(sourcePath); await field(page, "Voice take label").fill("Authored timing PCM — not speech");
    await button(page, "Import + measure").click(); await revision(page, 2);
    let project = (await source.fixtureBootstrap()).project;
    expect(project.audio.voice_tracks).toHaveLength(1);
    const track = project.audio.voice_tracks[0];
    expect(track).toMatchObject({ sample_rate_hz: 48_000, channels: 2, measured_duration: { num: "6", den: "1" }, source_sha256: hash(bytes) });
    await rm(sourcePath); // Every later measurement, bundle and reopen must use retained CAS.
    await addSegment(page, "0.2", "0.8", "Primera decisión"); await revision(page, 3);
    await addSegment(page, "1.1", "2.4", "Segunda decisión\ncontinua durante varios fotogramas"); await revision(page, 4);
    await field(page, "New cue time").fill("0.2"); await field(page, "New cue label").fill("Caption entry");
    await button(page, "Add cue").click(); await revision(page, 5);
    project = await source.project(project.id);
    const first = project.audio.transcript[0], second = project.audio.transcript[1], cue = project.audio.cues[0];
    const row = page.locator(".audio-transcript-row").filter({ has: field(page, "Transcript " + first.id) });
    for (const [index, text] of ["Segunda revisión de la primera frase", "Tercera revisión — todavía editable", "Corrección revisada — capítulo A"].entries()) {
      await field(page, "Transcript " + first.id).fill(text);
      await row.getByTitle("Save transcript segment as explicit manual alignment", { exact: true }).click(); await revision(page, 6 + index);
    }
    await field(page, "End " + first.id).fill("0.9");
    await row.getByTitle("Save transcript segment as explicit manual alignment", { exact: true }).click(); await revision(page, 9);
    await field(page, "Cue time " + cue.id).fill("0.3"); await field(page, "Cue label " + cue.id).fill("Reviewed caption entry");
    await page.locator(".audio-cue-row").filter({ has: field(page, "Cue label " + cue.id) }).getByTitle("Save cue", { exact: true }).click(); await revision(page, 10);
    await button(page, "Deliver").click();
    await field(page, "Language / locale").fill("es-MX"); await page.getByRole("combobox", { name: "Voice take", exact: true }).selectOption(track.id); await field(page, "Frame rate").selectOption("30/1");
    await button(page, "Save profile").click(); await revision(page, 11);
    project = await source.project(project.id); const originalProfile = structuredClone(project.deliverables[0]);
    const panel = page.getByRole("region", { name: "Caption sidecar export", exact: true });
    const vttPath = info.outputPath("reviewed-captions.vtt");
    await panel.getByLabel("Destination · absolute path", { exact: true }).fill(vttPath);
    await panel.getByRole("button", { name: "Export sidecar", exact: true }).click();
    await expect(page.getByText(/Caption sidecar written.*r11/)).toBeVisible();
    const vtt = await readFile(vttPath, "utf8");
    expect(vtt).toBe("WEBVTT\n\n00:00:00.200 --> 00:00:00.900\nCorrección revisada — capítulo A\n\n00:00:01.100 --> 00:00:02.400\nSegunda decisión\ncontinua durante varios fotogramas\n\n");
    expect((await source.project(project.id)).revision).toBe(11);
    await panel.getByRole("combobox", { name: "Format", exact: true }).selectOption("srt");
    await button(page, "Save profile").click(); await revision(page, 12);
    const srtPath = info.outputPath("reviewed-captions.srt"); await panel.getByLabel("Destination · absolute path", { exact: true }).fill(srtPath);
    await panel.getByRole("button", { name: "Export sidecar", exact: true }).click(); await expect(page.getByText(/Caption sidecar written.*r12/)).toBeVisible();
    const srt = await readFile(srtPath, "utf8");
    expect(srt).toBe("1\n00:00:00,200 --> 00:00:00,900\nCorrección revisada — capítulo A\n\n2\n00:00:01,100 --> 00:00:02,400\nSegunda decisión\ncontinua durante varios fotogramas\n\n");
    await button(page, "Derive selected").click(); await revision(page, 13);
    await field(page, "Profile name").fill("Caption vertical variant"); await field(page, "Width").fill("1080"); await field(page, "Height").fill("1920");
    await button(page, "Save profile").click(); await revision(page, 14);
    project = await source.project(project.id);
    const original = project.deliverables.find(item => item.id === originalProfile.id)!;
    expect(original).toEqual({ ...originalProfile, caption_format: "srt" });
    const variant = project.deliverables.find(item => item.parent_profile_id === original.id)!;
    expect(variant).toMatchObject({ width: 1080, height: 1920, language: "es-MX", voice_track_id: track.id, source_revision: 12 });
    await page.getByRole("list", { name: "Delivery profile index", exact: true }).getByText(original.name, { exact: true }).click();
    const held = holdSidecarReply(source); heldReplies.push(held);
    const latePath = info.outputPath("original-profile-late-reply.srt");
    await panel.getByLabel("Destination · absolute path", { exact: true }).fill(latePath);
    await panel.getByRole("button", { name: "Export sidecar", exact: true }).click();
    const lateResult = await held.written;
    expect(lateResult).toMatchObject({ project_id: project.id, revision: "14", profile_id: original.id });
    await page.getByRole("list", { name: "Delivery profile index", exact: true }).getByText(variant.name, { exact: true }).click();
    await field(page, "Profile name").fill("Later variant input kept while original export returns");
    held.release();
    const lateNotice = page.getByText(/Caption sidecar written for a previous selection/);
    await expect(lateNotice).toContainText(original.id);
    await expect(field(page, "Profile name")).toHaveValue("Later variant input kept while original export returns");
    expect(await readFile(latePath, "utf8")).toBe(srt);
    expect(await source.project(project.id)).toEqual(project);
    await held.dispose();
    const descriptor = { project_id: project.id, generation: project.generation, revision: project.revision,
      profile_id: original.id, language: original.language, frame_rate: original.frame_rate,
      source_pcm_sha256: track.source_sha256, voice_track_id: track.id, manual_alignment: true,
      caption_spans: [{ segment_id: first.id, start_frame: 6, end_frame_exclusive: 27 }, { segment_id: second.id, start_frame: 33, end_frame_exclusive: 72 }],
      cue_id: cue.id, cue_at: { num: "3", den: "10" }, expected_native_pixels: "not_evaluated",
    };
    const descriptorPath = info.outputPath("persisted-caption-source-spans.json"); await writeFile(descriptorPath, JSON.stringify(descriptor, null, 2));
    await info.attach("persisted-caption-source-spans", { path: descriptorPath, contentType: "application/json" });
    await source.close(); await source.start(); await page.reload(); await revision(page, 14);
    expect(await source.project(project.id)).toEqual(project);
    const bundlePath = info.outputPath("portable-caption-project"); await source.exportBundle(project, bundlePath);
    const target = await ScopedChangeService.create({ databasePath: info.outputPath("reimported", "studio.sqlite3"), seedFixture: false, desktopInvokeFixture: true }); services.push(target);
    const inspectedBundle = await target.inspectBundle(bundlePath);
    const imported = await target.importBundle(bundlePath, inspectedBundle.fingerprint);
    expect(imported.id).toBe(project.id); expect(imported.generation).not.toBe(project.generation); expect(imported.revision).toBe(14);
    expect(imported.audio).toEqual(project.audio); expect(imported.deliverables).toEqual(project.deliverables); expect(imported.scenes).toEqual(project.scenes);
    const casPath = join(target.directory, "blobs", "sha256", track.source_sha256.slice(0, 2), track.source_sha256);
    expect(hash(await readFile(casPath))).toBe(hash(bytes));
    const restoredPath = info.outputPath("reimported-captions.srt"), result = await exportSidecar(target, imported, restoredPath, original.id);
    expect(await readFile(restoredPath, "utf8")).toBe(srt);
    expect(result).toMatchObject({ project_id: imported.id, generation: imported.generation, revision: "14", profile_id: original.id,
      sidecar_sha256: hash(Buffer.from(srt)), source_current: true, cue_count: 2 });
    const restoredPage = await context.newPage(); pages.push(restoredPage); await target.attachPage(restoredPage, imported.id); await restoredPage.goto("/");
    await button(restoredPage, "Audio").click(); await field(restoredPage, "Transcript " + first.id).fill("Nueva edición después de importar");
    await restoredPage.locator(".audio-transcript-row").filter({ has: field(restoredPage, "Transcript " + first.id) }).getByTitle("Save transcript segment as explicit manual alignment", { exact: true }).click();
    await revision(restoredPage, 15);
    const final = await target.project(imported.id); expect(final.audio.transcript.find(item => item.id === first.id)?.text).toBe("Nueva edición después de importar");
    expect(await target.reopen(final.id)).toEqual(final); expect(await source.project(project.id)).toEqual(project);
    observations.source = descriptor; observations.original = project; observations.imported = imported; observations.final = final;
    observations.sidecars = { vtt_sha256: hash(Buffer.from(vtt)), srt_sha256: hash(Buffer.from(srt)), reimported: result };
    await restoredPage.screenshot({ path: info.outputPath("editable-caption-after-import.png") });
  } finally {
    try {
      const disposed = await Promise.allSettled(heldReplies.map(reply => reply.dispose()));
      const failures = disposed.flatMap(result => result.status === "rejected" ? [result.reason] : []);
      if (failures.length) throw new AggregateError(failures, "Held caption replies did not quiesce");
    } finally { await finish(info, services, pages, observations); }
  }
});

test("caption export refuses stale authority, replay, unknown alignment and existing destinations without a project mutation", async ({}, info) => {
  const service = await ScopedChangeService.create({ databasePath: info.outputPath("source", "studio.sqlite3"), desktopInvokeFixture: true });
  const services = [service], observations: Record<string, unknown> = {};
  try {
    let project = (await service.fixtureBootstrap()).project;
    const pcmPath = info.outputPath("timing.wav"); await writeFile(pcmPath, timingPcm());
    project = await service.importVoice(project, pcmPath, "Authored timing PCM — not speech");
    const track = project.audio.voice_tracks.at(-1)!;
    const segment = { id: randomUUID(), voice_track_id: track.id, start: { num: "1", den: "5" }, end: { num: "9", den: "10" },
      text: "Editable authored caption", speaker: null, alignment: { kind: "manual" as const } };
    project = await service.apply(project, { type: "upsert_transcript_segment", segment });
    project = await service.apply(project, { type: "upsert_deliverable", profile: { ...project.deliverables[0], captions: true, caption_format: "web_vtt", voice_track_id: track.id } });
    const scope = { project_id: project.id, generation: project.generation, revision: project.revision, profile_id: project.deliverables[0].id };
    const deniedPath = info.outputPath("denied.vtt");
    await expect(service.invoke("export_caption_sidecar", { request: { ...scope, path: deniedPath, effect_grant: randomUUID() } })).rejects.toThrow();
    await expect(access(deniedPath)).rejects.toThrow();
    const stale = await service.grant("deliver_local", project, deniedPath);
    const peer = await service.peer(); services.push(peer);
    project = await peer.apply(project, { type: "rename_project", title: "Later caption source decision" });
    await expect(service.invoke("export_caption_sidecar", { request: { ...scope, path: deniedPath, effect_grant: stale.token } })).rejects.toThrow("previous creative revision");
    await expect(access(deniedPath)).rejects.toThrow();
    const old = project;
    const wrongEffect = await service.grant("import_local", project, deniedPath);
    await expect(service.invoke("export_caption_sidecar", { request: { ...scope, revision: project.revision, path: deniedPath, effect_grant: wrongEffect.token } })).rejects.toThrow();
    const existing = info.outputPath("existing.vtt"), originalBytes = Buffer.from("Foreign existing file\n"); await writeFile(existing, originalBytes);
    await expect(exportSidecar(service, project, existing)).rejects.toThrow("will not overwrite"); expect(await readFile(existing)).toEqual(originalBytes);
    const goodPath = info.outputPath("confirmed.vtt"), good = await service.grant("deliver_local", project, goodPath);
    const request = { ...scope, revision: project.revision, path: goodPath, effect_grant: good.token };
    const written = await service.invoke<CaptionExportResult>("export_caption_sidecar", { request });
    const writtenBytes = await readFile(goodPath); expect(written.sidecar_sha256).toBe(hash(writtenBytes));
    await expect(service.invoke("export_caption_sidecar", { request })).rejects.toThrow(); expect(await readFile(goodPath)).toEqual(writtenBytes);
    expect(await service.project(project.id)).toEqual(old);
    project = await service.apply(project, { type: "upsert_transcript_segment", segment: { ...segment, alignment: { kind: "unknown" } } });
    await expect(exportSidecar(service, project, info.outputPath("unknown.vtt"))).rejects.toThrow("known transcript timing evidence");
    await expect(access(info.outputPath("unknown.vtt"))).rejects.toThrow(); expect(await service.project(project.id)).toEqual(project);
    observations.confirmed = written; observations.final = project; observations.existing_file_sha256 = hash(originalBytes);
  } finally { await finish(info, services, [], observations); }
});
