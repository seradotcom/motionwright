import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";
import type { Change, Project, VoiceTrack } from "../src/types";

/** Synthetic Tauri transport exercises the actual Studio screens and existing
 * review requests. It is NOT evidence from Semwright/native renderers or a WAV decoder.
 */
async function install(page: Page, options: { audio?: boolean; lateFrames?: boolean; lateWaveform?: boolean; wrongWaveform?: boolean } = {}) {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const profile = boot.project.deliverables[0];
  if (options.audio) {
    const tracks: VoiceTrack[] = ["A", "B"].map((label, index) => ({
      id: `018f0000-0000-7000-8000-00000000099${index}`,
      asset_id: boot.project.assets[0].id, label: "Synthetic take " + label,
      sample_rate_hz: 8000, channels: 2, measured_duration: { num: "192", den: "125" },
      source_sha256: (index === 0 ? "ab" : "cd").repeat(32), loudness_lufs: null, true_peak_dbfs: null,
    }));
    boot.project.audio.voice_tracks = tracks;
    boot.project.audio.active_voice_track_id = tracks[0].id;
    boot.project.audio.transcript = [];
  }
  const receipt = {
    project_resource: "project:" + boot.project.id, generation: boot.project.generation,
    revision: boot.project.revision, deliverable_id: profile.id, frame_rate: { num: 30, den: 1 },
    segments: [{ segment_id: "synthetic-analysis-segment", scene_ids: boot.project.scenes.slice(0, 2).map(s => s.id),
      frame_count: 450, plan_ref: "synthetic-only", fingerprint: "synthetic-only", job_ref: "synthetic-only", artifact: {}, verification: {} }],
    preview: [{ token: "SYNTHETIC-READ-GRANT-NEVER-RECORD", segment_id: "synthetic-analysis-segment",
      scene_ids: boot.project.scenes.slice(0, 2).map(s => s.id), frame_count: 450 }],
  };
  await page.addInitScript(({ initial, nativeReceipt, settings, width, height }) => {
    let current = structuredClone(initial.project);
    const commands: string[] = [], changes: unknown[] = [], frameRequests: number[] = [], waveRequests: string[] = [];
    const pendingFrames: Array<() => void> = [], pendingWaves: Array<() => void> = [];
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __MULTIMODAL_TEST__: { commands: string[]; changes: unknown[]; frameRequests: number[]; waveRequests: string[];
        releaseFrames: () => void; releaseWaves: () => void; current: () => typeof current };
    };
    runtime.__MULTIMODAL_TEST__ = { commands, changes, frameRequests, waveRequests,
      releaseFrames: () => pendingFrames.splice(0).forEach(resolve => resolve()),
      releaseWaves: () => pendingWaves.splice(0).forEach(resolve => resolve()), current: () => structuredClone(current) };
    runtime.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      commands.push(command);
      if (command === "bootstrap") return { ...structuredClone(initial), project: structuredClone(current) };
      if (command === "production_runtime_status") return { status: "ready", reason: null,
        expected_version: "1.0.0", observed_version: "1.0.0", version_compatible: true,
        executable_sha256: "a".repeat(64), connection_identity: "b".repeat(64), pinned_revision: "c".repeat(40) };
      if (command === "motion_canvas_preflight") {
        const request = args?.request as { project_id: string; generation: string; revision: number; deliverable_id: string };
        return { project_resource: "project:" + request.project_id, generation: request.generation,
          revision: request.revision, deliverable_id: request.deliverable_id, verdict: "projection_ready",
          reason: null, segment_count: 1, total_frames: 60,
          segments: [{ segment_id: "synthetic-film-only", scene_ids: [], frame_count: 60 }] };
      }
      if (command === "issue_effect_grant") return { token: "synthetic-test-effect-grant" };
      if (command === "render_motion_canvas") return structuredClone(nativeReceipt);
      if (command === "preview_native_frame") {
        const request = args?.request as { token: string; frame_index: number; revision: number };
        if (request.token !== "SYNTHETIC-READ-GRANT-NEVER-RECORD" || request.revision !== current.revision) {
          throw new Error("Synthetic stale native read rejected");
        }
        frameRequests.push(request.frame_index);
        const canvas = document.createElement("canvas");canvas.width = width;canvas.height = height;
        const context = canvas.getContext("2d");if (!context) throw new Error("Synthetic fixture canvas unavailable");
        context.fillStyle = request.frame_index === 1 ? "#ffffff" : "#202830";
        context.fillRect(0, 0, width, height);
        const encoded = canvas.toDataURL("image/png").split(",")[1];
        const bytes = Uint8Array.from(atob(encoded), value => value.charCodeAt(0));
        if (settings.lateFrames) return new Promise(resolve => pendingFrames.push(() => resolve(bytes)));
        return bytes;
      }
      if (command === "waveform_page") {
        const request = args?.request as { track_id: string; page_index: number; page_size: number };
        waveRequests.push(request.track_id);
        const track = current.audio.voice_tracks.find(t => t.id === request.track_id);
        if (!track) throw new Error("Unknown synthetic take");
        const response = { schema: "motionwright.waveform-page.v1", algorithm: "sample-peak-max-abs-v1",
          source_sha256: settings.wrongWaveform ? "ef".repeat(32) : track.source_sha256,
          sample_rate_hz: 8000, channels: 2, frames_per_peak: 2048, total_frames: 12288,
          peak_count: 6, page_index: request.page_index, page_size: request.page_size, start_peak: 0,
          peaks: track.label.endsWith("A") ? [.2, 1, 1, 0, 0, 0] : [.2, .2, .2, .2, .2, .2],
          has_previous: false, has_next: false };
        if (settings.lateWaveform && track.label.endsWith("A")) {
          return new Promise(resolve => pendingWaves.push(() => resolve(response)));
        }
        return response;
      }
      if (command === "apply_change") {
        const request = args?.request as { project_id: string; generation: string; revision: number; change: Change };
        if (request.project_id !== current.id || request.generation !== current.generation || request.revision !== current.revision) {
          throw new Error("Synthetic CAS conflict");
        }
        const change = request.change; changes.push(structuredClone(change));
        if (change.type === "add_review") {
          current.reviews.push({ id: crypto.randomUUID(), kind: change.kind, body: change.body, status: "open", resolution: null,
            created_at: new Date().toISOString(), resolved_at: null,
            anchor: { resource: change.resource, branch_id: current.active_branch, revision: current.revision,
              start: change.start, end: change.end, locale: change.locale, profile_id: change.profile_id } });
        } else if (change.type === "set_active_voice_track") {
          current.audio.active_voice_track_id = change.track_id;
        } else { throw new Error("Unexpected synthetic mutation: " + change.type); }
        current.revision += 1;
        const branch = current.branches.find(b => b.id === current.active_branch);
        if (branch) branch.head_revision = current.revision;
        return structuredClone(current);
      }
      throw new Error("Unexpected synthetic native command: " + command);
    } };
  }, { initial: boot, nativeReceipt: receipt, settings: options, width: profile.width, height: profile.height });
  return boot;
}

async function openNativeInspection(page: Page, boot: typeof fixtureBootstrap) {
  const [first, second] = boot.project.scenes;
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS"); // synthetic transport text only
  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("button", { name: "Native inspection", exact: true }).click();
}
async function control(page: Page) {
  return page.evaluate(() => {
    const c = (window as unknown as { __MULTIMODAL_TEST__: { commands: string[]; changes: Change[]; frameRequests: number[]; waveRequests: string[]; current: () => Project } }).__MULTIMODAL_TEST__;
    return { commands: c.commands, changes: c.changes, frameRequests: c.frameRequests, waveRequests: c.waveRequests, project: c.current() };
  });
}

test("consecutive native observations become an explicit review, never an automatic scene edit", async ({ page }) => {
  const boot = await install(page);
  await openNativeInspection(page, boot);
  await page.getByLabel("Native inspection sampling").selectOption("window");
  await page.getByRole("button", { name: "Inspect current native frames" }).click();
  await expect(page.getByLabel("Native analysis coverage")).toContainText("8 source frames · 7 adjacent pairs · 6 adjacent triples · 0 temporal gaps");
  const isolated = page.getByLabel("Native analysis candidates").locator("article").filter({ hasText: "Isolated thumbnail change" });
  await expect(isolated).toHaveCount(1);
  await expect(page.getByText("Expected motion: unchanged thumbnails", { exact: true })).toHaveCount(0);
  await page.getByLabel("I expect continuous motion in the inspected interval").check();
  await expect(page.getByText("Expected motion: unchanged thumbnails", { exact: true })).toBeVisible();
  expect((await control(page)).changes).toEqual([]);
  await isolated.getByRole("button", { name: "Inspect candidate frames" }).click();
  await expect(page.locator(".native-inspection-grid button.selected")).toHaveAttribute("aria-label", "Inspect native frame 1");
  await isolated.getByRole("button", { name: "Record analytical review candidate" }).click();
  await expect(page.getByRole("heading", { name: "No current native frame grant" })).toBeVisible();
  const state = await control(page);
  expect(state.frameRequests).toEqual([0,1,2,3,4,5,6,7]);
  expect(state.changes).toHaveLength(1);
  const change = state.changes[0]; expect(change.type).toBe("add_review");
  if (change.type !== "add_review") throw new Error("Unexpected mutation");
  const body = JSON.parse(change.body);
  expect(body.interpretation).toBe("automated_candidate_recorded_for_human_review");
  expect(body.candidate.code).toBe("isolated_thumbnail_change");
  expect(body.evidence).toHaveLength(3);
  expect(body.creative_approval).toBe("not_assessed"); expect(body.effects_verdict).toBe("not_issued");
  expect(change.body).not.toContain("SYNTHETIC-READ-GRANT-NEVER-RECORD");
  expect(state.project.scenes).toEqual(boot.project.scenes);
  expect(state.project.revision).toBe(boot.project.revision + 1);
});

test("a late native frame cannot repopulate inspection after the output profile changes", async ({ page }) => {
  const boot = await install(page, { lateFrames: true });
  await openNativeInspection(page, boot);
  await page.getByRole("button", { name: "Inspect current native frames" }).click();
  await expect.poll(async () => (await control(page)).frameRequests.length).toBe(1);
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(page.getByRole("heading", { name: "No current native frame grant" })).toBeVisible();
  await page.evaluate(() => (window as unknown as { __MULTIMODAL_TEST__: { releaseFrames: () => void } }).__MULTIMODAL_TEST__.releaseFrames());
  await expect(page.locator(".native-inspection-grid button")).toHaveCount(0);
  await expect(page.getByLabel("Native thumbnail analysis", { exact: true })).toHaveCount(0);
  expect((await control(page)).changes).toEqual([]);
});

test("measured audio proxy candidates retain take-relative scope in an existing review transaction", async ({ page }) => {
  const boot = await install(page, { audio: true });
  await page.goto("/");await page.getByRole("button", { name: "Audio", exact: true }).click();
  const panel = page.getByLabel("Measured waveform review candidates", { exact: true });
  await expect(panel.getByText("Near-full-scale proxy bins", { exact: true })).toBeVisible();
  await expect(panel.getByText("Expected sound: quiet proxy span", { exact: true })).toHaveCount(0);
  await panel.getByLabel("I expect audible content throughout this waveform page").check();
  const quiet = panel.locator("article").filter({ hasText: "Expected sound: quiet proxy span" });
  await expect(quiet).toContainText("[96/125, 192/125)");
  expect((await control(page)).changes).toEqual([]);
  const readsBeforeRecording = (await control(page)).waveRequests.length;
  await quiet.getByRole("button", { name: "Record waveform review candidate" }).click();
  await expect.poll(async () => (await control(page)).changes.length).toBe(1);
  await expect.poll(async () => (await control(page)).waveRequests.length).toBeGreaterThan(readsBeforeRecording);
  await expect(panel.getByLabel("I expect audible content throughout this waveform page")).not.toBeChecked();
  const state = await control(page), change = state.changes[0];
  expect(change.type).toBe("add_review");if (change.type !== "add_review") throw new Error("Unexpected mutation");
  const body = JSON.parse(change.body);
  expect(body.coverage.coordinate_space).toBe("voice_take_relative");
  expect(body.source.source_sha256).toBe(boot.project.audio.voice_tracks[0].source_sha256);
  expect(body.true_peak).toBe("not_measured");expect(body.repair_applied).toBe(false);
  expect(change.start).toBeNull();expect(change.end).toBeNull();
  expect(state.project.audio.mix).toEqual(boot.project.audio.mix);
  expect(state.project.audio.cues).toEqual(boot.project.audio.cues);
});

test("an old take response cannot replace the selected take's waveform review", async ({ page }) => {
  const boot = await install(page, { audio: true, lateWaveform: true });
  await page.goto("/");await page.getByRole("button", { name: "Audio", exact: true }).click();
  await expect.poll(async () => (await control(page)).waveRequests.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: /Synthetic take B/ }).click();
  const panel = page.getByLabel("Measured waveform review candidates", { exact: true });
  await expect(panel).toContainText("No candidate from this page");
  await page.evaluate(() => (window as unknown as { __MULTIMODAL_TEST__: { releaseWaves: () => void } }).__MULTIMODAL_TEST__.releaseWaves());
  await expect(panel).toContainText("No candidate from this page");
  await expect(panel.getByText("Near-full-scale proxy bins", { exact: true })).toHaveCount(0);
  const state = await control(page);
  expect(state.project.audio.active_voice_track_id).toBe(boot.project.audio.voice_tracks[1].id);
  expect(state.changes.map(c => c.type)).toEqual(["set_active_voice_track"]);
});

test("a waveform with a foreign source digest is unavailable, not relabeled as the current take", async ({ page }) => {
  await install(page, { audio: true, wrongWaveform: true });
  await page.goto("/");await page.getByRole("button", { name: "Audio", exact: true }).click();
  await expect(page.locator(".waveform-proxy-state[role=alert]")).toContainText("Waveform page identity");
  await expect(page.getByLabel("Measured waveform review candidates", { exact: true })).toHaveCount(0);
  await expect(page.locator(".waveform-proxy-bars")).toHaveCount(0);
  expect((await control(page)).changes).toEqual([]);
});
