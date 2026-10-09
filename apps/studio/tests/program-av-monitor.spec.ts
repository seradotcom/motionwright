import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("Program AV monitor reads exact source token only for matching scene, profile and revision", async ({ page }) => {
  // Synthetic Tauri contract: actual H.264/AAC decode and WebView playback
  // remain independently validated; no synthetic master is called native PASS.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const profile = boot.project.deliverables[0];
  const voiceId = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb";
  const assetId = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
  const audioHash = "a".repeat(64);
  boot.project.assets.push({
    id: assetId, name: "source.wav", media_type: "audio/wav",
    content_sha256: audioHash, source_revision: "mock",
  });
  boot.project.audio.voice_tracks.push({
    id: voiceId, asset_id: assetId, label: "Synthetic source",
    sample_rate_hz: 48000, channels: 2,
    measured_duration: { num: "16", den: "1" },
    source_sha256: audioHash,
    loudness_lufs: null, true_peak_dbfs: null,
  });
  profile.voice_track_id = voiceId;
  const render = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: profile.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "synthetic-segment", scene_ids: [first.id, second.id],
      frame_count: 480, plan_ref: "synthetic-plan", fingerprint: "synthetic-fingerprint",
      job_ref: "synthetic-job", artifact: {}, verification: {},
    }],
    preview: [{
      token: "synthetic-frame-token", segment_id: "synthetic-segment",
      scene_ids: [first.id, second.id], frame_count: 480,
    }],
  };
  const master = {
    export_token: "synthetic-master-token",
    project_resource: render.project_resource,
    generation: render.generation, revision: render.revision,
    deliverable_id: render.deliverable_id,
    motion_segment_id: "synthetic-segment",
    frame_rate: render.frame_rate, frame_count: 480,
    mezzanine: {},
    source_audio: { relative_path: "synthetic.wav", sha256: audioHash, sample_rate: 48000, channels: 2 },
    master: { profile: "h264-aac-mp4", artifact: { sha256: "b".repeat(64) } },
    decoded_audio: {}, sync: null,
  };

  await page.addInitScript(({ initial, visual, av }) => {
    const mock = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __AV_MONITOR_READS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initial.project);
    mock.__AV_MONITOR_READS__ = [];
    mock.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return { ...initial, project: structuredClone(project) };
        if (command === "issue_effect_grant") return { token: "synthetic-grant" };
        if (command === "render_motion_canvas") return structuredClone(visual);
        if (command === "assemble_av_master") return structuredClone(av);
        if (command === "review_native_av_master") {
          mock.__AV_MONITOR_READS__.push(args?.request as Record<string, unknown>);
          // MP4-shaped header only, intentionally not a fabricated valid clip.
          return Uint8Array.from([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109, 0, 0, 0, 0]);
        }
        if (command === "apply_change") {
          const request = args?.request as { change: { type: string; title?: string } };
          if (request?.change.type !== "rename_project") throw Error("Unsupported synthetic creative edit");
          project = { ...project, title: request.change.title ?? "Edited", revision: project.revision + 1 };
          return structuredClone(project);
        }
        throw Error("Synthetic Tauri fixture does not support command " + command);
      },
    };
  }, { initial: boot, visual: render, av: master });

  await page.goto("/");
  const program = page.getByRole("region", { name: "Program preview" });
  await expect(program.getByRole("button", { name: "Review final AV" })).toHaveCount(0);
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  for (const [scene, role, archetype] of [
    [first, "hook", "statement"],
    [second, "problem", "diagram_build"],
  ] as const) {
    await page.getByLabel(scene.name + " narrative role").selectOption(role);
    await page.getByLabel(scene.name + " motion archetype").selectOption(archetype);
  }
  await page.getByRole("button", { name: "Render native segments" }).click();
  await page.getByRole("button", { name: "Assemble native AV master" }).click();
  await expect(page.getByLabel("Native AV master evidence")).toContainText("CURRENT · NATIVE");

  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  const originalRevision = await page.locator(".revision-chip").first().innerText();
  await expect(program.getByRole("button", { name: "Review final AV" })).toBeVisible();
  await program.getByRole("button", { name: "Review final AV" }).click();
  await expect(program.getByRole("region", { name: "Native master playback review" })).toBeVisible();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __AV_MONITOR_READS__: Array<unknown> }
  ).__AV_MONITOR_READS__.length)).toBeGreaterThanOrEqual(1);
  const readings = await page.evaluate(() => (
    window as unknown as { __AV_MONITOR_READS__: Array<Record<string, unknown>> }
  ).__AV_MONITOR_READS__);
  expect(readings.every((request) => request.project_id === boot.project.id
    && request.revision === boot.project.revision
    && request.export_token === "synthetic-master-token"
    && !("path" in request)
    && !("sha256" in request)
    && !("effect_grant" in request))).toBe(true);
  await expect(page.locator(".revision-chip").first()).toHaveText(originalRevision);

  // A different profile invalidates the prior explicit opt-in. Returning
  // to the original profile must not automatically read the media again.
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(program.getByRole("region", { name: "Native master playback review" })).toHaveCount(0);
  await expect(program.getByRole("button", { name: "Review final AV" })).toHaveCount(0);
  await page.getByLabel("Editor preview timebase").selectOption(profile.id);
  await expect(program.getByRole("region", { name: "Native master playback review" })).toHaveCount(0);
  await program.getByRole("button", { name: "Review final AV" }).click();
  await expect(program.getByRole("region", { name: "Native master playback review" })).toBeVisible();
  await program.getByRole("button", { name: "Show design" }).click();
  await expect(program.getByRole("region", { name: "Native master playback review" })).toHaveCount(0);

  await page.locator(".tree-row").filter({ hasText: "Semwright acts natively" }).click();
  await expect(program.getByRole("button", { name: "Review final AV" })).toHaveCount(0);
  await page.locator(".tree-row").filter({ hasText: first.name }).click();
  await expect(program.getByRole("button", { name: "Review final AV" })).toBeVisible();

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByLabel("Project title").fill("Changed after mastering");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(program).toContainText("STALE receipt");
  await expect(program.getByRole("button", { name: "Review final AV" })).toHaveCount(0);
});

test("browser demo cannot invent final AV monitoring from a semantic project", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Program preview" })
    .getByRole("button", { name: "Review final AV" })).toHaveCount(0);
});
