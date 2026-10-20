import { randomUUID } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { expect, test as base, type Page } from "@playwright/test";
import type { Change, Project } from "../src/types";
import { ScopedChangeService, type StoreFinalization } from "./support/scopedChangeService";

// Synthetic authored scenarios over actual StudioService, grants, SQLite and CAS.
// The JSONL invoke bridge does not certify Tauri, Film, mastering or human quality.
const test = base.extend<{ audio: { service: ScopedChangeService; peers: ScopedChangeService[]; initial: Project; segmentId: string; cueId: string } }>({
  audio: async ({ page }, use, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
    const peers: ScopedChangeService[] = [];
    let initial: Project | undefined;
    try {
      initial = (await service.fixtureBootstrap()).project;
      const source = testInfo.outputPath("synthetic-authored-pcm.wav"); await writeFile(source, pcmWav());
      initial = await service.importVoice(initial, source, "Synthetic audio preservation fixture");
      const track = initial.audio.voice_tracks.at(-1)!;
      if (initial.audio.active_voice_track_id !== track.id) initial = await service.apply(initial, { type: "set_active_voice_track", track_id: track.id });
      const segmentId = randomUUID(), cueId = randomUUID();
      initial = await service.apply(initial, { type: "upsert_transcript_segment", segment: {
        id: segmentId, voice_track_id: track.id, start: { num: "1", den: "3" }, end: { num: "5", den: "3" },
        text: "The author's original sentence.", speaker: "Original speaker", alignment: { kind: "manual" },
      } });
      initial = await service.apply(initial, { type: "upsert_audio_cue", cue: {
        id: cueId, label: "Original cue", at: { num: "1", den: "3" }, source_segment_id: segmentId, evidence: "manual",
      } });
      await service.attachFixturePage(page); await page.goto("/"); await button(page, "Audio").click();
      await expect(page.getByRole("region", { name: "Audio workspace", exact: true })).toBeVisible();
      await use({ service, peers, initial, segmentId, cueId });
    } finally {
      let final: Project | undefined;
      let observationFailure: unknown;
      let finalization: StoreFinalization | undefined;
      try { if (initial) final = await service.project(initial.id); }
      catch (error) { observationFailure = error; }
      try {
        finalization = await service.finalizeClosedStore(testInfo.outputPath("closed-store-1"), final, peers, [
          () => page.close(),
          async () => { if (observationFailure) throw observationFailure; },
        ]);
      } finally {
        const path = testInfo.outputPath("audio-real-service-evidence.json");
        await writeFile(path, JSON.stringify({ scope: "browser_interaction_real_service_sqlite_cas", excludes: ["installed_tauri", "native_audio_render", "human_acceptance"],
          status: testInfo.status, initial, final, canonicalOracle: finalization?.oracle,
          finalizationPath: testInfo.outputPath("closed-store-1-outcomes.json"),
          services: [service, ...peers].map(item => ({ binary: item.binaryPath, transcript: item.transcript,
            exits: item.processExits, lifecycleTaints: item.lifecycleTaints, stderr: item.stderr })),
        }, null, 2));
        await testInfo.attach("audio-real-service-evidence", { path, contentType: "application/json" });
        await testInfo.attach("complete-store-finalization", { path: testInfo.outputPath("closed-store-1-outcomes.json"), contentType: "application/json" });
      }
    }
  },
});
test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN, "Build the real studio_review_session executable; no simulated service fallback.");
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const field = (page: Page, name: string) => page.getByLabel(name, { exact: true });
const mixSection = (page: Page) => page.getByRole("region", { name: "Global mix intent", exact: true });
const transcriptRow = (page: Page, id: string) => page.locator(".audio-transcript-row").filter({ has: field(page, "Transcript " + id) });
const cueRow = (page: Page, id: string) => page.locator(".audio-cue-row").filter({ has: field(page, "Cue label " + id) });
async function revision(page: Page, expected: number) { await expect(page.locator(".revision-chip").first()).toHaveText("r" + expected); }
async function openAudioAfterReload(page: Page) { await page.reload(); await button(page, "Audio").click(); }
function pcmWav() {
  const rate = 48000, frames = rate * 2, buffer = Buffer.alloc(44 + frames * 2);
  buffer.write("RIFF"); buffer.writeUInt32LE(buffer.length - 8, 4); buffer.write("WAVEfmt ", 8); buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22); buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28);
  buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34); buffer.write("data", 36); buffer.writeUInt32LE(frames * 2, 40);
  for (let index = 0; index < frames; index++) buffer.writeInt16LE(index % 2 ? 8192 : -8192, 44 + index * 2);
  return buffer;
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; }
function holdApply(service: ScopedChangeService, type: Change["type"], phase: "before" | "after") {
  const original = service.invoke.bind(service), entered = deferred<void>(), release = deferred<void>(), observed = deferred<Project | Error>();
  let armed = true;
  service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
    if (!armed || command !== "apply_change" || (args.request as { change?: Change })?.change?.type !== type) return original<T>(command, args);
    armed = false; entered.resolve(); if (phase === "before") await release.promise;
    try { const result = await original<T>(command, args); observed.resolve(result as Project); if (phase === "after") await release.promise; return result; }
    catch (reason) { observed.resolve(reason instanceof Error ? reason : new Error(String(reason))); if (phase === "after") await release.promise; throw reason; }
  };
  return { entered: entered.promise, observed: observed.promise, release: () => release.resolve(), restore: () => { release.resolve(); service.invoke = original; } };
}
function mutationCount(service: ScopedChangeService) { return service.transcript.filter(entry => entry.direction === "request" && "command" in entry.payload && entry.payload.command === "apply_change").length; }

test("three audio groups retain one another's drafts, rational timing and canonical revisions through reopen", async ({ page, audio }, testInfo) => {
  const { service, initial, segmentId, cueId } = audio;
  await field(page, "Voice gain dB").fill("-4.5"); await field(page, "Transcript " + segmentId).fill("The revised authored sentence.");
  await field(page, "Cue label " + cueId).fill("The director's new cue");
  await button(page, "Save mix intent").click(); await revision(page, initial.revision + 1);
  await expect(field(page, "Transcript " + segmentId)).toHaveValue("The revised authored sentence.");
  await expect(field(page, "Cue label " + cueId)).toHaveValue("The director's new cue");
  await transcriptRow(page, segmentId).getByTitle("Save transcript segment as explicit manual alignment", { exact: true }).click(); await revision(page, initial.revision + 2);
  await expect(field(page, "Cue label " + cueId)).toHaveValue("The director's new cue");
  await cueRow(page, cueId).getByTitle("Save cue", { exact: true }).focus(); const playhead = await page.locator(".timeline-header .timecode").innerText();
  await page.keyboard.press("Space"); await revision(page, initial.revision + 3);
  await expect(page.locator(".timeline-header .timecode")).toHaveText(playhead); await expect(button(page, "Play design preview")).toBeVisible();
  const saved = await service.project(initial.id), segment = saved.audio.transcript.find(item => item.id === segmentId)!, cue = saved.audio.cues.find(item => item.id === cueId)!;
  expect(segment).toMatchObject({ text: "The revised authored sentence.", start: { num: "1", den: "3" }, end: { num: "5", den: "3" }, alignment: { kind: "manual" } });
  expect(cue).toMatchObject({ label: "The director's new cue", at: { num: "1", den: "3" }, source_segment_id: segmentId, evidence: "manual" });
  expect(saved.scenes).toEqual(initial.scenes); expect(saved.audio.voice_tracks).toEqual(initial.audio.voice_tracks);
  expect(await service.reopen(saved.id)).toEqual(saved); await openAudioAfterReload(page);
  await expect(field(page, "Voice gain dB")).toHaveValue("-4.5"); await expect(field(page, "Transcript " + segmentId)).toHaveValue(segment.text);
  await mixSection(page).scrollIntoViewIfNeeded(); await page.screenshot({ path: testInfo.outputPath("audio-mix-saved.png") });
});

for (const kind of ["mix", "transcript", "cue"] as const) test(`${kind}: a late real reply retains Old → Sent → Old, then the next revision saves that intent`, async ({ page, audio }) => {
  const { service, initial, segmentId, cueId } = audio;
  const input = field(page, kind === "mix" ? "Voice gain dB" : kind === "transcript" ? "Transcript " + segmentId : "Cue label " + cueId);
  const save = kind === "mix" ? button(page, "Save mix intent") : kind === "transcript" ? transcriptRow(page, segmentId).getByTitle("Save transcript segment as explicit manual alignment", { exact: true }) : cueRow(page, cueId).getByTitle("Save cue", { exact: true });
  const old = await input.inputValue(), sent = kind === "mix" ? "-7" : "The immutable submitted value";
  await input.fill(sent); const gate = holdApply(service, kind === "mix" ? "set_mix_intent" : kind === "transcript" ? "upsert_transcript_segment" : "upsert_audio_cue", "after");
  try {
    await save.click(); const result = await gate.observed; expect(result).not.toBeInstanceOf(Error);
    await input.fill(old); await expect(input).toHaveValue(old); gate.release(); await revision(page, initial.revision + 1);
    await expect(input).toHaveValue(old); await expect(save).toBeEnabled(); await save.click(); await revision(page, initial.revision + 2);
    await expect(input).toHaveValue(old); const final = await service.project(initial.id); expect(await service.reopen(final.id)).toEqual(final);
  } finally { gate.restore(); }
});

test("real CAS rejection, reload and per-field conflict choices preserve other input and keyboard focus", async ({ page, audio }, testInfo) => {
  const { service, peers, initial } = audio;
  await field(page, "Voice gain dB").fill("-3"); await field(page, "Target LUFS").fill("-18");
  const gate = holdApply(service, "set_mix_intent", "before");
  try {
    await button(page, "Save mix intent").click(); await gate.entered;
    const peer = await service.peer(); peers.push(peer);
    const external = await peer.apply(initial, { type: "set_mix_intent", mix: { ...initial.audio.mix, voice_gain_db: -9, music_gain_db: -20 } });
    gate.release(); expect(await gate.observed).toBeInstanceOf(Error);
    await expect(mixSection(page).getByRole("alert")).toContainText("retained");
    await openAudioAfterReload(page); await revision(page, external.revision);
    await expect(field(page, "Voice gain dB")).toHaveValue("-3"); await expect(field(page, "Music gain dB")).toHaveValue("-20"); await expect(field(page, "Target LUFS")).toHaveValue("-18");
    await expect(mixSection(page).getByRole("region", { name: "Voice gain dB conflict", exact: true })).toBeVisible();
    for (const [theme, width] of [["dark", 1440], ["light", 1440], ["light", 1024]] as const) {
      await page.setViewportSize({ width, height: 940 });
      const changeTheme = button(page, theme === "dark" ? "Use dark theme" : "Use light theme");
      if (await changeTheme.isVisible()) await changeTheme.click();
      await mixSection(page).scrollIntoViewIfNeeded();
      const conflict = mixSection(page).getByRole("region", { name: "Voice gain dB conflict", exact: true });
      expect(await conflict.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      await expect(button(page, "Keep my draft Voice gain dB")).toBeVisible();
      await expect(button(page, "Keep saved Voice gain dB")).toBeVisible();
      await button(page, "Keep my draft Voice gain dB").click({ trial: true });
      await (width === 1024 ? conflict : mixSection(page)).screenshot({ path: testInfo.outputPath(`audio-conflict-${theme}-${width}.png`) });
    }
    await button(page, "Keep my draft Voice gain dB").focus(); await page.keyboard.press("Space");
    await expect(field(page, "Voice gain dB")).toBeFocused(); await expect(field(page, "Target LUFS")).toHaveValue("-18");
    await button(page, "Save mix intent").click(); await revision(page, external.revision + 1);
    expect((await service.project(initial.id)).audio.mix).toMatchObject({ voice_gain_db: -3, music_gain_db: -20, target_lufs: -18 });
    await mixSection(page).scrollIntoViewIfNeeded(); await page.screenshot({ path: testInfo.outputPath("audio-conflict-resolved.png") });
  } finally { gate.restore(); }
});

test("new transcript and cue forms clear only confirmed values while retaining later typing", async ({ page, audio }) => {
  const { service, initial } = audio;
  await field(page, "New transcript start").fill("0"); await field(page, "New transcript end").fill("0.5");
  await field(page, "New transcript text").fill("The new segment sent to the service."); await field(page, "New transcript speaker").fill("Author");
  await field(page, "New cue time").fill("1.5"); await field(page, "New cue label").fill("An independent new cue");
  const gate = holdApply(service, "upsert_transcript_segment", "after");
  try {
    await button(page, "Add manual segment").click(); expect(await gate.observed).not.toBeInstanceOf(Error);
    await expect(field(page, "New transcript text")).toHaveValue("The new segment sent to the service.");
    await field(page, "New transcript text").fill("A second segment still being written."); gate.release(); await revision(page, initial.revision + 1);
    await expect(field(page, "New transcript text")).toHaveValue("A second segment still being written.");
    await expect(field(page, "New transcript start")).toHaveValue(""); await expect(field(page, "New transcript speaker")).toHaveValue("");
    await expect(field(page, "New cue label")).toHaveValue("An independent new cue"); await button(page, "Add cue").click(); await revision(page, initial.revision + 2);
    await expect(field(page, "New cue label")).toHaveValue(""); await expect(field(page, "New transcript text")).toHaveValue("A second segment still being written.");
    const final = await service.project(initial.id); expect(final.audio.transcript).toHaveLength(initial.audio.transcript.length + 1); expect(final.audio.cues).toHaveLength(initial.audio.cues.length + 1);
    await openAudioAfterReload(page); await expect(field(page, "New transcript text")).toHaveValue("A second segment still being written.");
  } finally { gate.restore(); }
});

test("invalid numbers never dispatch and content locks permit recovery without mutation", async ({ page, audio }) => {
  const { service, peers, initial } = audio, before = mutationCount(service);
  await field(page, "Voice gain dB").fill(" "); await expect(button(page, "Save mix intent")).toBeDisabled();
  await expect(mixSection(page).getByRole("status")).toContainText("blank input is not zero");
  await field(page, "Voice gain dB").fill("1e100"); await expect(button(page, "Save mix intent")).toBeDisabled();
  await field(page, "New cue time").fill("Infinity"); await field(page, "New cue label").fill("Keep invalid timing raw"); await expect(button(page, "Add cue")).toBeDisabled();
  expect(mutationCount(service)).toBe(before);
  await field(page, "Voice gain dB").fill("-3"); const peer = await service.peer(); peers.push(peer);
  const locked = await peer.apply(initial, { type: "set_lock", resource: "project:" + initial.id, kind: "content", note: "Synthetic explicit lock fixture" });
  await openAudioAfterReload(page); await revision(page, locked.revision); await expect(field(page, "Voice gain dB")).toHaveValue("-3"); await expect(button(page, "Save mix intent")).toBeDisabled();
  await field(page, "Voice gain dB").fill("-4"); await expect(field(page, "New cue time")).toHaveValue("Infinity");
  expect((await service.project(initial.id)).audio.mix).toEqual(initial.audio.mix);
});

test("corrupt owned recovery is preserved until explicit discard; newer typing survives the discard", async ({ page, audio }) => {
  await field(page, "Voice gain dB").fill("-3");
  const copy = await page.evaluate(() => {
    const key = Object.keys(localStorage).find(key => key.startsWith("motionwright.audio-recovery.v1:") && decodeURIComponent(key).includes('"kind":"mix"'))!;
    if (!key) throw new Error("Expected this writer's actual mix recovery copy"); localStorage.setItem(key, "{explicitly-corrupt-audio-copy"); return key;
  });
  await openAudioAfterReload(page);
  await expect(mixSection(page).getByRole("alert")).toContainText("unreadable copy"); await field(page, "Voice gain dB").fill("-6");
  await expect(button(page, "Save mix intent")).toBeDisabled(); expect(await page.evaluate(key => localStorage.getItem(key), copy)).toBe("{explicitly-corrupt-audio-copy");
  await mixSection(page).locator("summary").click();
  await mixSection(page).getByRole("group").filter({ hasText: "Unreadable copy, original bytes preserved" })
    .getByRole("button", { name: "Discard copy", exact: true }).click();
  await expect(field(page, "Voice gain dB")).toHaveValue("-6"); await expect(button(page, "Save mix intent")).toBeEnabled();
  await button(page, "Save mix intent").click(); await revision(page, audio.initial.revision + 1); expect((await audio.service.project(audio.initial.id)).audio.mix.voice_gain_db).toBe(-6);
});

test("a removed cue retains its human draft as a readable recovery copy and never recreates itself", async ({ page, audio }, testInfo) => {
  const { service, peers, initial, cueId } = audio;
  await field(page, "Cue label " + cueId).fill("Human cue wording preserved after deletion.");
  const peer = await service.peer(); peers.push(peer); const removed = await peer.apply(initial, { type: "remove_audio_cue", cue_id: cueId });
  await openAudioAfterReload(page); await revision(page, removed.revision); await expect(field(page, "Cue label " + cueId)).toHaveCount(0);
  const retained = page.getByRole("region", { name: "Retained audio drafts", exact: true }); await expect(retained).toBeVisible();
  await retained.locator("summary").first().click(); await expect(retained).toContainText("Human cue wording preserved after deletion.");
  const download = page.waitForEvent("download"); await retained.getByRole("button", { name: "Download retained audio copy", exact: true }).first().click();
  const file = await download; const path = testInfo.outputPath("retained-audio-copy.json"); await file.saveAs(path);
  expect(JSON.parse(await readFile(path, "utf8")).draft.fields.label.raw).toBe("Human cue wording preserved after deletion.");
  expect((await service.project(initial.id)).audio.cues.some(item => item.id === cueId)).toBe(false); expect(await service.reopen(initial.id)).toEqual(removed);
});
