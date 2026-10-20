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


test("synthetic native receipt remains scene/revision scoped across Deliver and Timeline", async ({ page }) => {
  // This is a UI wiring test, NOT evidence that Semwright actually rendered frames.
  const boot = {
    ...fixtureBootstrap,
    native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" },
  };
  const first = boot.project.scenes[0];
  const output = boot.project.deliverables[0];
  const receipt = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: output.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "synthetic-segment",
      scene_ids: [first.id],
      frame_count: 210,
      plan_ref: "synthetic-plan",
      fingerprint: "synthetic-fingerprint",
      job_ref: "synthetic-job",
      artifact: { kind: "synthetic-test-only" },
      verification: { kind: "synthetic-test-only" },
    }],
  };

  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initialBoot, initialReceipt }) => {
    const mock = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    let savedProject = structuredClone(initialBoot.project);
    mock.__TAURI_INTERNALS__ = {
      invoke: async (cmd, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (cmd === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === savedProject.id && recipeRequest.generation === savedProject.generation
            && recipeRequest.revision === savedProject.revision) return { token: "synthetic-recipe-only" };
        if (cmd === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          savedProject = await apply(savedProject, recipeRequest);
          initialBoot.project = structuredClone(savedProject);
          initialReceipt.revision = savedProject.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(savedProject);
        }

        if (cmd === "production_runtime_status") return {
          status: "ready", reason: null, expected_version: "1.0.0",
          observed_version: "1.0.0", version_compatible: true,
          executable_sha256: "a".repeat(64), connection_identity: "b".repeat(64),
          pinned_revision: "c".repeat(40),
        };
        if (cmd === "motion_canvas_preflight") {
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

        if (cmd === "bootstrap") {
          const syntheticResponse = { ...initialBoot, project: structuredClone(savedProject) };
          const requestedId = (args as {request?: {project_id?:string|null}}|undefined)?.request?.project_id;
          if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic mock requires selected project identity.");
          return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
        }
        if (cmd === "issue_effect_grant") return { token: "synthetic-grant" };
        if (cmd === "render_motion_canvas") return structuredClone(initialReceipt);
        if (cmd === "apply_change") {
          const request = args?.request as { change?: { type?: string; title?: string } } | undefined;
          const change = request?.change;
          if (change?.type !== "rename_project" || !change.title) {
            throw new Error("Unsupported synthetic creative edit");
          }
          savedProject = { ...savedProject, title: change.title, revision: savedProject.revision + 1 };
          return structuredClone(savedProject);
        }
        throw new Error("Unsupported synthetic Tauri command: " + cmd);
      },
    };
  }, { initialBoot: boot, initialReceipt: receipt });

  await page.goto("/");
  const preview = page.getByRole("region", { name: "Program preview" });
  await expect(preview).toContainText("No render evidence attached");

  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  const second = boot.project.scenes[1];
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");

  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(preview).toContainText("Native segment receipt · 210 frames · r" + (boot.project.revision + 1) + " · metadata only");
  await expect(preview).toContainText("Design representation");

  await page.locator(".tree-row").filter({ hasText: "Semwright acts natively" }).click();
  await expect(preview).toContainText("No render evidence attached");
  await page.locator(".tree-row").filter({ hasText: first.name }).click();
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(preview).toContainText("Receipt for " + output.name + " · different display profile");

  await page.getByRole("button", { name: "Inspect receipt in Deliver" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.locator("#project-title-rich").fill("Post-render revision");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(preview).toContainText("STALE receipt r" + (boot.project.revision + 1));
  await expect(preview).toContainText("project is r" + (boot.project.revision + 2));
});
