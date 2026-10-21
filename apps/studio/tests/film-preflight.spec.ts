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


test("native semantic Film preflight is read-only and cannot be mistaken for real render", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const sceneA = boot.project.scenes[0];
  const sceneB = boot.project.scenes[1];
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initial }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __PREFLIGHT__: { calls: Array<{ project_id: string; options: unknown }>; renders: number };
    };
    runtime.__PREFLIGHT__ = { calls: [], renders: 0 };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === initial.project.id && recipeRequest.generation === initial.project.generation
            && recipeRequest.revision === initial.project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          initial.project = await apply(initial.project, recipeRequest);
          return structuredClone(initial.project);
        }

        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(initial));
        }
        if (command === "motion_canvas_preflight") {
          const request = args?.request as {
            project_id: string; generation: string; revision: number;
            deliverable_id: string; options: { scene_intents: unknown[] };
          };
          runtime.__PREFLIGHT__.calls.push({
            project_id: request.project_id, options: request.options,
          });
          return {
            project_resource: "project:" + request.project_id,
            generation: request.generation,
            revision: request.revision,
            deliverable_id: request.deliverable_id,
            verdict: "projection_ready",
            reason: null,
            segment_count: 1,
            total_frames: 450,
            segments: [{ segment_id: "semantic-only", scene_ids: [
              initial.project.scenes[0].id, initial.project.scenes[1].id,
            ], frame_count: 450 }],
          };
        }
        if (command === "render_motion_canvas") {
          runtime.__PREFLIGHT__.renders++;
          throw new Error("The synthetic bridge must not run a native render.");
        }
        throw new Error("Unsupported synthetic command " + command);
      },
    };
  }, { initial: boot });
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(sceneA.name + " narrative role").selectOption("hook");
  await page.getByLabel(sceneA.name + " motion archetype").selectOption("statement");
  await page.getByLabel(sceneB.name + " narrative role").selectOption("problem");
  await page.getByLabel(sceneB.name + " motion archetype").selectOption("diagram_build");
  await saveFixtureRecipe(page);
  const originalRevision = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Check Film projection" }).click();
  const report = page.getByLabel("Native Film preflight result");
  await expect(report).toContainText("Film projection supported: 1 segment(s), 450 planned frames");
  await expect(report).toContainText("Renderer execution, actual pixels and AV mastering are not yet proven");
  await expect(page.locator(".revision-chip").first()).toHaveText(originalRevision);
  const result = await page.evaluate(() => (
    window as unknown as {
      __PREFLIGHT__: { calls: Array<{ project_id: string; options: { scene_intents: unknown[] } }>; renders: number };
    }
  ).__PREFLIGHT__);
  expect(result.calls).toHaveLength(1);
  expect(result.calls[0].project_id).toBe(boot.project.id);
  expect(result.calls[0].options.scene_intents).toHaveLength(2);
  expect(result.renders).toBe(0);

  // Changing authored intent invalidates the visible semantic preflight.
  await page.getByLabel(sceneA.name + " narrative role").selectOption("comparison");
  await expect(report).toHaveCount(0);
});

test("unsupported Film projection prevents native dispatch for unchanged inputs", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initial }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === initial.project.id && recipeRequest.generation === initial.project.generation
            && recipeRequest.revision === initial.project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          initial.project = await apply(initial.project, recipeRequest);
          return structuredClone(initial.project);
        }

        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(initial));
        }
        if (command === "motion_canvas_preflight") {
          const request = args?.request as {
            project_id: string; generation: string; revision: number; deliverable_id: string;
          };
          return {
            project_resource: "project:" + request.project_id,
            generation: request.generation,
            revision: request.revision,
            deliverable_id: request.deliverable_id,
            verdict: "unsupported",
            reason: "Unsupported camera zoom would lose native semantics.",
            segment_count: 0,
            total_frames: 0,
            segments: [],
          };
        }
        throw new Error("Native render must not be dispatched by preflight");
      },
    };
  }, { initial: boot });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  for (const [scene, role, archetype] of [
    [boot.project.scenes[0], "hook", "statement"],
    [boot.project.scenes[1], "problem", "diagram_build"],
  ] as const) {
    await page.getByLabel(scene.name + " narrative role").selectOption(role);
    await page.getByLabel(scene.name + " motion archetype").selectOption(archetype);
  }
  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("Native Film preflight result")).toContainText("Unsupported camera zoom");
  await expect(page.getByRole("button", { name: "Render native segments" })).toBeDisabled();

  await page.getByLabel(boot.project.scenes[0].name + " narrative role").selectOption("reveal");
  await expect(page.getByLabel("Native Film preflight result")).toHaveCount(0);
  // Changing an authored intent invalidates its preflight; never permit a
  // native effect request using evidence from an earlier composition.
  await expect(page.getByRole("button", { name: "Render native segments" })).toBeDisabled();
  await expect(page.getByLabel("First native production readiness")).toContainText("FILM PREFLIGHT REQUIRED");
});

test("browser demo cannot invent native Film semantic projection readiness", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check Film projection" })).toBeDisabled();
  await expect(page.getByLabel("Native Film preflight result")).toHaveCount(0);
});
