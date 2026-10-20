import { expect, test } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";
import type { Page as RecipeFixturePage } from "@playwright/test";
import { applyChange as applySyntheticFixtureChange } from "../src/api";
import type { Change as RecipeFixtureChange, Project as RecipeFixtureProject } from "../src/types";

interface RecipeFixtureRequest {
  effect?: string; project_id: string; generation: string; revision: number;
  effect_grant: string; change?: RecipeFixtureChange;
}

// Explicit synthetic authoring fixture. The Node-side API uses its existing
// simulator branch, not SQLite, Tauri, native rendering or owner authority.
// The real UI still issues ProjectEdit/apply_change and observes the returned
// Project revision; no saved recipe is injected directly into the component.
async function installRecipeAuthoringFixture(page: RecipeFixturePage) {
  await page.exposeFunction("__MW_RECIPE_FIXTURE_APPLY__", async (source: RecipeFixtureProject, request: RecipeFixtureRequest) => {
    if (request.project_id !== source.id || request.generation !== source.generation || request.revision !== source.revision
        || request.effect_grant !== "synthetic-recipe-only" || request.change?.type !== "set_delivery_recipe") {
      throw new Error("Synthetic recipe fixture rejected a stale or unsupported authoring request.");
    }
    return applySyntheticFixtureChange(source, request.change);
  });
}

async function saveFixtureRecipe(page: RecipeFixturePage, group: "film" | "mix" = "film") {
  const revision = Number((await page.locator(".command-bar .revision-chip").innerText()).slice(1));
  const label = group === "film" ? "Save scene intent" : "Save original mix recipe";
  await page.getByRole("button", { name: label, exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (revision + 1));
  await expect(page.getByRole("region", { name: (group === "film" ? "Scene intent" : "Original mix") + " draft review", exact: true }).getByRole("status").first())
    .toContainText("Showing saved recipe values at revision r" + (revision + 1));
  return revision + 1;
}


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
    revision: boot.project.revision + 1,
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

  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initial, visual, av }) => {
    const mock = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __AV_MONITOR_READS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initial.project);
    mock.__AV_MONITOR_READS__ = [];
    mock.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === project.id && recipeRequest.generation === project.generation
            && recipeRequest.revision === project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          project = await apply(project, recipeRequest);
          initial.project = structuredClone(project);
          visual.revision = project.revision; // Future synthetic native reply uses the saved source.
          av.revision = project.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(project);
        }

        if (command === "production_runtime_status") return {
          status: "ready", reason: null, expected_version: "1.0.0",
          observed_version: "1.0.0", version_compatible: true,
          executable_sha256: "a".repeat(64), connection_identity: "b".repeat(64),
          pinned_revision: "c".repeat(40),
        };
        if (command === "motion_canvas_preflight") {
          const scope = args?.request as {
            project_id: string; generation: string; revision: number; deliverable_id: string;
          };
          return {
            project_resource: "project:" + scope.project_id,
            generation: scope.generation, revision: scope.revision,
            deliverable_id: scope.deliverable_id,
            verdict: "projection_ready", reason: null, segment_count: 1, total_frames: 60,
            segments: [{ segment_id: "synthetic-film-only", scene_ids: [], frame_count: 60 }],
          };
        }

        if (command === "bootstrap") {
          const syntheticResponse = { ...initial, project: structuredClone(project) };
          const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
          if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
            throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
          }
          return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
        }
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
  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
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
    && request.revision === boot.project.revision + 1
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
  await page.locator("#project-title-rich").fill("Changed after mastering");
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
