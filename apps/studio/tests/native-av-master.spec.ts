import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("desktop AV master is linked only to session-native visual evidence and a measured saved voice", async ({ page }) => {
  // Synthetic Tauri transport; the native MLT driver and verified WAV bytes
  // have separate exact-SHA Rust/real-runtime CI evidence.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const master = boot.project.deliverables[0];
  const assetId = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
  const takeId = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb";
  const audioHash = "a".repeat(64);
  boot.project.assets.push({
    id: assetId,
    name: "measured-voice.wav",
    media_type: "audio/wav",
    content_sha256: audioHash,
    source_revision: "measured-audio-import",
  });
  boot.project.audio.voice_tracks.push({
    id: takeId,
    asset_id: assetId,
    label: "Native mastering voice take",
    sample_rate_hz: 48000,
    channels: 2,
    measured_duration: { num: "16", den: "1" },
    source_sha256: audioHash,
    loudness_lufs: null,
    true_peak_dbfs: null,
  });
  master.voice_track_id = takeId;

  const native = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: master.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "native-test-segment",
      scene_ids: [first.id, second.id],
      frame_count: 480,
      plan_ref: "native-test-plan",
      fingerprint: "native-test-fingerprint",
      job_ref: "native-test-job",
      artifact: { kind: "synthetic-only" },
      verification: { kind: "synthetic-only" },
    }],
    preview: [{
      token: "native-session-token",
      segment_id: "native-test-segment",
      scene_ids: [first.id, second.id],
      frame_count: 480,
    }],
  };
  const masterReceipt = {
    export_token: "synthetic-export-token",
    project_resource: native.project_resource,
    generation: native.generation,
    revision: native.revision,
    deliverable_id: master.id,
    motion_segment_id: "native-test-segment",
    frame_rate: native.frame_rate,
    frame_count: 480,
    mezzanine: { profile: "ffv1" },
    source_audio: { relative_path: "owner-only-staged.wav", sha256: audioHash, sample_rate: 48000, channels: 2 },
    master: { profile: "h264-aac-mp4", artifact: { sha256: "b".repeat(64) } },
    decoded_audio: { kind: "synthetic-only" },
    sync: null,
  };
  await page.addInitScript(({ initial, rendered, mastered }) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __SYNTHETIC_AV__: { requests: Array<Record<string, unknown>>; exports: Array<Record<string, unknown>>; reviews: Array<Record<string, unknown>>; renders: number; effects: number };
    };
    let project = structuredClone(initial.project);
    app.__SYNTHETIC_AV__ = { requests: [], exports: [], reviews: [], renders: 0, effects: 0 };
    app.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return { ...initial, project: structuredClone(project) };
        if (command === "issue_effect_grant") {
          app.__SYNTHETIC_AV__.effects++;
          return { token: "synthetic-only-effect" };
        }
        if (command === "render_motion_canvas") {
          app.__SYNTHETIC_AV__.renders++;
          return structuredClone(rendered);
        }
        if (command === "assemble_av_master") {
          const request = args?.request as Record<string, unknown>;
          app.__SYNTHETIC_AV__.requests.push(request);
          return structuredClone(mastered);
        }
        if (command === "review_native_av_master") {
          app.__SYNTHETIC_AV__.reviews.push(args?.request as Record<string, unknown>);
          // Synthetic header only: CI tests transport, not actual decoding.
          return Uint8Array.from([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109, 0, 0, 0, 0]);
        }
        if (command === "export_native_av_master") {
          const request = args?.request as Record<string, unknown>;
          app.__SYNTHETIC_AV__.exports.push(request);
          return {
            destination: request.destination,
            size_bytes: 8192,
            sha256: mastered.master.artifact.sha256,
            revision: mastered.revision,
            deliverable_id: mastered.deliverable_id,
            source_current: true,
            integrity_manifest_path: request.include_integrity_manifest
              ? String(request.destination) + ".motionwright-integrity.json"
              : null,
          };
        }
        if (command === "apply_change") {
          const request = args?.request as { change: { type: string; title?: string } };
          if (request.change.type !== "rename_project") throw new Error("Unrecognized synthetic creative operation");
          project = { ...project, title: request.change.title ?? "Changed", revision: project.revision + 1 };
          return structuredClone(project);
        }
        throw new Error("Unsupported synthetic operation: " + command);
      },
    };
  }, { initial: boot, rendered: native, mastered: masterReceipt });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const masterPanel = page.getByRole("region", { name: "Canonical audiovisual mastering" });
  const assemble = masterPanel.getByRole("button", { name: "Assemble native AV master" });
  await expect(assemble).toBeDisabled();
  await expect(masterPanel).toContainText("Complete a real native render");

  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");
  await expect(assemble).toBeEnabled();
  const revision = await page.locator(".revision-chip").first().innerText();

  await assemble.click();
  const receipt = page.getByLabel("Native AV master evidence");
  await expect(receipt).toContainText("480");
  await expect(receipt).toContainText("CURRENT · NATIVE");
  await expect(receipt).toContainText("SHA-256");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  const invocations = await page.evaluate(() => (
    window as unknown as { __SYNTHETIC_AV__: { requests: Array<Record<string, unknown>>; renders: number; effects: number } }
  ).__SYNTHETIC_AV__);
  expect(invocations.renders).toBe(1);
  expect(invocations.effects).toBe(2);
  expect(invocations.requests).toHaveLength(1);
  expect(invocations.requests[0]).toMatchObject({
    project_id: boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: master.id,
    voice_track_id: takeId,
    preview_token: "native-session-token",
  });
  expect(invocations.requests[0]).not.toHaveProperty("file");
  expect(invocations.requests[0]).not.toHaveProperty("path");
  expect(invocations.requests[0]).not.toHaveProperty("motion");
  expect(invocations.requests[0]).not.toHaveProperty("audio");

  const reviewAction = masterPanel.getByRole("button", { name: "Review native MP4" });
  await expect(reviewAction).toBeEnabled();
  await reviewAction.click();
  const review = masterPanel.getByRole("region", { name: "Native master playback review" });
  await expect(review).toBeVisible();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __SYNTHETIC_AV__: { reviews: Array<unknown> } }
  ).__SYNTHETIC_AV__.reviews.length)).toBe(1);
  const reviewArgs = await page.evaluate(() => (
    window as unknown as { __SYNTHETIC_AV__: { reviews: Array<Record<string, unknown>> } }
  ).__SYNTHETIC_AV__.reviews[0]);
  expect(reviewArgs).toMatchObject({
    project_id: boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    export_token: "synthetic-export-token",
  });
  expect(reviewArgs).not.toHaveProperty("path");
  expect(reviewArgs).not.toHaveProperty("sha256");
  expect(reviewArgs).not.toHaveProperty("effect_grant");
  await masterPanel.getByRole("button", { name: "Close native review" }).click();
  await expect(review).toHaveCount(0);

  const delivery = page.getByRole("region", { name: "Verified native MP4 delivery" });
  const exportAction = delivery.getByRole("button", { name: "Export verified MP4" });
  await expect(exportAction).toBeDisabled();
  await page.getByLabel("Verified MP4 export path").fill("/tmp/motionwright-delivered-master.mp4");
  const includeIntegrity = page.getByRole("checkbox", { name: "Write portable MP4 integrity receipt" });
  await expect(includeIntegrity).not.toBeChecked();
  await includeIntegrity.check();
  await expect(exportAction).toBeEnabled();
  await exportAction.click();
  const exportReceipt = page.getByLabel("Verified MP4 delivery receipt");
  await expect(exportReceipt).toContainText("/tmp/motionwright-delivered-master.mp4");
  await expect(exportReceipt).toContainText("VERIFIED · CURRENT");
  await expect(exportReceipt).toContainText("8192");
  await expect(exportReceipt).toContainText("motionwright-delivered-master.mp4.motionwright-integrity.json");
  await expect(delivery).toContainText("It is unsigned");
  const afterExport = await page.evaluate(() => (
    window as unknown as { __SYNTHETIC_AV__: { exports: Array<Record<string, unknown>>; effects: number } }
  ).__SYNTHETIC_AV__);
  expect(afterExport.effects).toBe(3);
  expect(afterExport.exports).toHaveLength(1);
  expect(afterExport.exports[0]).toMatchObject({
    project_id: boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    export_token: "synthetic-export-token",
    destination: "/tmp/motionwright-delivered-master.mp4",
    include_integrity_manifest: true,
  });
  expect(afterExport.exports[0]).not.toHaveProperty("source");
  expect(afterExport.exports[0]).not.toHaveProperty("sha256");
  expect(afterExport.exports[0]).not.toHaveProperty("master");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByLabel("Project title").fill("Changed after native AV master");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(receipt).toContainText("STALE · HISTORICAL");
  await expect(exportReceipt).toContainText("VERIFIED · HISTORICAL");
  await expect(assemble).toBeDisabled();
  await expect(page.getByRole("button", { name: "Export verified MP4" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Review native MP4" })).toBeDisabled();
});

test("UI refuses to offer native mastering for incompatible measured voice duration", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const profile = boot.project.deliverables[0];
  const takeId = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
  const assetId = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee";
  const hash = "f".repeat(64);
  boot.project.assets.push({ id: assetId, name: "voice.wav", media_type: "audio/wav", content_sha256: hash, source_revision: null });
  boot.project.audio.voice_tracks.push({
    id: takeId, asset_id: assetId, label: "Too short", sample_rate_hz: 48000,
    channels: 2, measured_duration: { num: "1", den: "1" },
    source_sha256: hash, loudness_lufs: null, true_peak_dbfs: null,
  });
  profile.voice_track_id = takeId;
  await page.addInitScript(({ initial, sceneIds }) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (cmd: string) => Promise<unknown> };
    };
    app.__TAURI_INTERNALS__ = { invoke: async (cmd) => {
      if (cmd === "bootstrap") return structuredClone(initial);
      if (cmd === "issue_effect_grant") return { token: "synthetic-only" };
      if (cmd === "render_motion_canvas") return {
        project_resource: "project:" + initial.project.id,
        generation: initial.project.generation,
        revision: initial.project.revision,
        deliverable_id: initial.project.deliverables[0].id,
        frame_rate: { num: 30, den: 1 },
        segments: [{ segment_id: "native-one", scene_ids: sceneIds, frame_count: 480,
          plan_ref: "test", fingerprint: "test", job_ref: "test", artifact: {}, verification: {} }],
        preview: [{ token: "synthetic", segment_id: "native-one", scene_ids: sceneIds, frame_count: 480 }],
      };
      throw new Error("Synthetic test must not dispatch AV master: " + cmd);
    }};
  }, { initial: boot, sceneIds: [first.id, second.id] });
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await page.getByRole("button", { name: "Render native segments" }).click();
  const panel = page.getByRole("region", { name: "Canonical audiovisual mastering" });
  await expect(panel.getByRole("button", { name: "Assemble native AV master" })).toBeDisabled();
  await expect(panel).toContainText("must match the exact native video cut");
});

test("browser demo never claims native AV master authority", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(page.getByRole("button", { name: "Assemble native AV master" })).toBeDisabled();
  await expect(page.getByLabel("Native AV master evidence")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Review native MP4" })).toBeDisabled();
  await expect(page.getByRole("checkbox", { name: "Write portable MP4 integrity receipt" })).toBeDisabled();
});
