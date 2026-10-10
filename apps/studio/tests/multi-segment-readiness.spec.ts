import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("Deliver checks multi-segment source readiness with only an owner preview token", async ({ page }) => {
  // Explicitly synthetic Tauri bridge for UI/IPC; this does NOT fabricate a
  // real native render or prove that a two-segment final MP4 exists.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const profile = boot.project.deliverables[0];
  const scenes = boot.project.scenes.filter((item) => item.renderer === "motion-canvas");
  const previewToken = "77777777-7777-4777-8777-777777777777";
  const source = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: profile.id,
    frame_rate: { num: 30, den: 1 },
    segments: scenes.map((scene, i) => ({
      segment_id: "segment-" + i,
      scene_ids: [scene.id],
      frame_count: i === 0 ? 210 : 270,
      plan_ref: "synthetic-plan-" + i,
      fingerprint: "synthetic-fingerprint-" + i,
      job_ref: "synthetic-job-" + i,
      artifact: {},
      verification: {},
    })),
    preview: [{
      token: previewToken,
      segment_id: "segment-0",
      scene_ids: [scenes[0].id],
      frame_count: 210,
    }],
  };
  await page.addInitScript(({ initial, evidence, token }) => {
    const shim = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __SOURCE_READS__: Array<Record<string, unknown>>;
    };
    shim.__SOURCE_READS__ = [];
    shim.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return { ...initial, project: structuredClone(initial.project) };
        if (command === "issue_effect_grant") return { token: "synthetic-grant" };
        if (command === "render_motion_canvas") return structuredClone(evidence);
        if (command === "preflight_multi_segment_mlt_readiness") {
          const request = args?.request as Record<string, unknown>;
          shim.__SOURCE_READS__.push(request);
          if (request.preview_token !== token) throw Error("Owner token is required");
          return {
            project_resource: evidence.project_resource,
            generation: evidence.generation,
            revision: evidence.revision,
            deliverable_id: evidence.deliverable_id,
            verdict: "source_manifest_ready",
            mlt_profile: "h264-1080p",
            total_frames: 480,
            segments: [
              { segment_id: "segment-0", scene_ids: [evidence.segments[0].scene_ids[0]], start_frame: 0, frame_count: 210 },
              { segment_id: "segment-1", scene_ids: [evidence.segments[1].scene_ids[0]], start_frame: 210, frame_count: 270 },
            ],
            evidence_scope: "manifest-digest-verified-not-composited-mp4",
          };
        }
        throw Error("UI synthetic source-readiness test cannot run command " + command);
      },
    };
  }, { initial: boot, evidence: source, token: previewToken });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const region = page.getByRole("region", { name: "Multi-segment native MLT source readiness" });
  await expect(region).toHaveCount(0);
  for (const scene of scenes) {
    await page.getByLabel(scene.name + " narrative role").selectOption("mechanism");
    await page.getByLabel(scene.name + " motion archetype").selectOption("statement");
  }
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(region).toBeVisible();
  const check = region.getByRole("button", { name: "Check native multi-segment sources" });
  await expect(check).toBeEnabled();
  await check.click();
  const report = page.getByLabel("Multi-segment source preflight result");
  await expect(report).toContainText("2 segments");
  await expect(report).toContainText("480 exact frames");
  await expect(report).toContainText("NOT been assembled");
  const requests = await page.evaluate(() => (
    window as unknown as { __SOURCE_READS__: Array<Record<string, unknown>> }
  ).__SOURCE_READS__);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toEqual({
    project_id: boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: profile.id,
    preview_token: previewToken,
  });
  expect(JSON.stringify(requests[0])).not.toMatch(/manifest|output_root|source_options|path|sha256|render_evidence/);

  await page.getByRole("list", { name: "Delivery profile index" }).getByRole("listitem").nth(1).click();
  await expect(region).toHaveCount(0);
  await expect(report).toHaveCount(0);
});

test("Browser demo has no native multi-segment readiness source", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(page.getByRole("region", { name: "Multi-segment native MLT source readiness" })).toHaveCount(0);
});
