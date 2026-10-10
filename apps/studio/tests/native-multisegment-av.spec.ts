import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("desktop multisegment final AV is source-token-bound, measured and export-authorized", async ({ page }) => {
  // Synthetic IPC contract only. Real FFV1/AV mux and decoded audio gates are
  // independently executed in exact-SHA Semwright Broker CI, never faked here.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const profile = boot.project.deliverables[0];
  const scenes = boot.project.scenes.filter((scene) => scene.renderer === "motion-canvas");
  expect(scenes.length).toBeGreaterThanOrEqual(2);
  const wavAsset = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
  const wavTrack = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const wavDigest = "a".repeat(64);
  boot.project.assets.push({
    id: wavAsset, name: "measured-stereo.wav", media_type: "audio/wav",
    content_sha256: wavDigest, source_revision: "actual-import",
  });
  boot.project.audio.voice_tracks.push({
    id: wavTrack, asset_id: wavAsset, label: "Measured stereo",
    sample_rate_hz: 48000, channels: 2,
    measured_duration: { num: "16", den: "1" },
    source_sha256: wavDigest, loudness_lufs: null, true_peak_dbfs: null,
  });
  profile.voice_track_id = wavTrack;

  const token = "77777777-7777-4777-8777-777777777777";
  const native = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation, revision: boot.project.revision,
    deliverable_id: profile.id,
    frame_rate: { num: 30, den: 1 },
    segments: [
      { segment_id: "source-01", scene_ids: [scenes[0].id],
        frame_count: 240, plan_ref: "source-only-1",
        fingerprint: "synthetic-only-1", job_ref: "synthetic-only-1",
        artifact: {}, verification: {} },
      { segment_id: "source-02", scene_ids: [scenes[1].id],
        frame_count: 240, plan_ref: "source-only-2",
        fingerprint: "synthetic-only-2", job_ref: "synthetic-only-2",
        artifact: {}, verification: {} },
    ],
    preview: [{ token, segment_id: "source-01",
      scene_ids: [scenes[0].id], frame_count: 240 }],
  };
  const master = {
    project_resource: native.project_resource,
    generation: native.generation, revision: native.revision,
    deliverable_id: profile.id, frame_count: 480, video_segments: 2,
    video_codec: "h264", audio_codec: "aac",
    audio_sample_rate: 48000, audio_channels: 2,
    master_sha256: "b".repeat(64), export_token: "synthetic-export-token",
    provider_project_cleanup: "not_requested_requires_foreground_broker_consent",
    evidence_scope: "native-multisegment-av-verified-technical-output-not-human-approved",
  };
  await page.addInitScript(({ initial, original, previewToken, av }) => {
    const shim = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __MULTI_AV__: { sources: Array<Record<string, unknown>>;
        assemblies: Array<Record<string, unknown>>;
        exports: Array<Record<string, unknown>>; effects: number };
    };
    shim.__MULTI_AV__ = { sources: [], assemblies: [], exports: [], effects: 0 };
    shim.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initial);
        if (command === "issue_effect_grant") {
          shim.__MULTI_AV__.effects++;
          return { token: "synthetic-effect-" + shim.__MULTI_AV__.effects };
        }
        if (command === "render_motion_canvas") return structuredClone(original);
        if (command === "preflight_multi_segment_mlt_readiness") {
          const request = args?.request as Record<string, unknown>;
          shim.__MULTI_AV__.sources.push(request);
          if (request.preview_token !== previewToken) throw Error("opaque owner token mismatch");
          return {
            project_resource: original.project_resource,
            generation: original.generation, revision: original.revision,
            deliverable_id: original.deliverable_id,
            verdict: "source_manifest_ready",
            mlt_profile: "h264-1080p", total_frames: 480,
            segments: [
              { segment_id: "source-01", scene_ids: original.segments[0].scene_ids,
                start_frame: 0, frame_count: 240 },
              { segment_id: "source-02", scene_ids: original.segments[1].scene_ids,
                start_frame: 240, frame_count: 240 },
            ],
            evidence_scope: "manifest-digest-verified-not-composited-mp4",
          };
        }
        if (command === "assemble_multisegment_av_master") {
          const request = args?.request as Record<string, unknown>;
          if (request.preview_token !== previewToken) throw Error("owner token mismatch");
          shim.__MULTI_AV__.assemblies.push(request);
          return structuredClone(av);
        }
        if (command === "export_native_av_master") {
          const request = args?.request as Record<string, unknown>;
          shim.__MULTI_AV__.exports.push(request);
          return {
            destination: request.destination, size_bytes: 123456,
            sha256: av.master_sha256,
            revision: av.revision, deliverable_id: av.deliverable_id,
            source_current: true, integrity_manifest_path: null,
          };
        }
        throw Error("Unexpected synthetic desktop operation " + command);
      },
    };
  }, { initial: boot, original: native, previewToken: token, av: master });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const panel = page.getByRole("region", { name: "Canonical audiovisual mastering" });
  const assemble = panel.getByRole("button", { name: "Assemble multi-segment MP4" });
  await expect(assemble).toHaveCount(0);
  for (const scene of scenes) {
    await page.getByLabel(scene.name + " narrative role").selectOption("mechanism");
    await page.getByLabel(scene.name + " motion archetype").selectOption("statement");
  }
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(assemble).toBeVisible();
  await expect(panel.getByRole("button", { name: "Assemble native AV master" })).toHaveCount(0);
  await expect(assemble).toBeDisabled();
  await page.getByRole("button", { name: "Check native multi-segment sources" }).click();
  await expect(page.getByLabel("Multi-segment source preflight result"))
    .toContainText("480 exact frames");
  await expect(assemble).toBeEnabled();

  const revision = await page.locator(".revision-chip").first().innerText();
  await assemble.click();
  const receipt = page.getByLabel("Native multi-segment AV master evidence");
  await expect(receipt).toContainText("2 semantic MLT segments");
  await expect(receipt).toContainText("480");
  await expect(receipt).toContainText("H.264 · AAC · 48 kHz stereo");
  await expect(receipt).toContainText("Pending foreground Broker consent");
  await expect(receipt).toContainText("CURRENT · NATIVE");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
  const requests = await page.evaluate(() => (
    (window as unknown as { __MULTI_AV__: { sources: Record<string, unknown>[];
      assemblies: Record<string, unknown>[]; effects: number } }).__MULTI_AV__
  ));
  expect(requests.sources).toHaveLength(1);
  expect(requests.assemblies).toHaveLength(1);
  expect(requests.effects).toBe(2);
  expect(requests.assemblies[0]).toMatchObject({
    project_id: boot.project.id, generation: boot.project.generation,
    revision: boot.project.revision, deliverable_id: profile.id,
    voice_track_id: wavTrack, preview_token: token,
  });
  expect(JSON.stringify(requests.assemblies[0]))
    .not.toMatch(/(source_options|filepath|output_root|manifest|audio_sha256|video_sha256|render_evidence)/);

  const delivery = page.getByRole("region", { name: "Verified native MP4 delivery" });
  const exportButton = delivery.getByRole("button", { name: "Export verified MP4" });
  await expect(exportButton).toBeDisabled();
  await page.getByLabel("Verified MP4 export path").fill("/tmp/motionwright-test-multi.mp4");
  await expect(exportButton).toBeEnabled();
  await exportButton.click();
  await expect(page.getByLabel("Verified MP4 delivery receipt"))
    .toContainText("VERIFIED · CURRENT");
  const sent = await page.evaluate(() => (
    (window as unknown as { __MULTI_AV__: { exports: Record<string, unknown>[]; effects: number } }).__MULTI_AV__
  ));
  expect(sent.effects).toBe(3);
  expect(sent.exports).toHaveLength(1);
  expect(sent.exports[0]).toMatchObject({
    project_id: boot.project.id, export_token: "synthetic-export-token",
    destination: "/tmp/motionwright-test-multi.mp4",
  });
  expect(sent.exports[0]).not.toHaveProperty("source_path");
  expect(sent.exports[0]).not.toHaveProperty("sha256");
});
