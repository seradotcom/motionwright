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
    revision: boot.project.revision + 1,
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
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initial, evidence, token }) => {
    const shim = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __SOURCE_READS__: Array<Record<string, unknown>>;
    };
    shim.__SOURCE_READS__ = [];
    shim.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === initial.project.id && recipeRequest.generation === initial.project.generation
            && recipeRequest.revision === initial.project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          initial.project = await apply(initial.project, recipeRequest);
          evidence.revision = initial.project.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(initial.project);
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
          const syntheticResponse = { ...initial, project: structuredClone(initial.project) };
          const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
          if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
            throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
          }
          return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
        }
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
  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
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
    revision: boot.project.revision + 1,
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
