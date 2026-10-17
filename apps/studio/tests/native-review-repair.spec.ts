import { expect, test } from "@playwright/test";
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


test("a sampled native frame becomes a user-authored scoped repair, never an automatic edit", async ({ page }) => {
  // Synthetic Tauri transport only. Real Semwright frames and digests are
  // validated in separate source-pinned native E2E workflows.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const profile = boot.project.deliverables[0];
  const target = first.nodes.find(node => node.kind === "text");
  expect(target).toBeTruthy();

  const receipt = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: profile.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "synthetic-review-segment",
      scene_ids: [first.id, second.id],
      frame_count: 450,
      plan_ref: "synthetic-only", fingerprint: "synthetic-only",
      job_ref: "synthetic-only", artifact: {}, verification: {},
    }],
    preview: [{
      token: "synthetic-review-token", segment_id: "synthetic-review-segment",
      scene_ids: [first.id, second.id], frame_count: 450,
    }],
  };

  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initialBoot, nativeReceipt, nativeWidth, nativeHeight }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __REVIEW_TEST_COMMANDS__: string[];
    };
    runtime.__REVIEW_TEST_COMMANDS__ = [];
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === initialBoot.project.id && recipeRequest.generation === initialBoot.project.generation
            && recipeRequest.revision === initialBoot.project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          initialBoot.project = await apply(initialBoot.project, recipeRequest);
          nativeReceipt.revision = initialBoot.project.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(initialBoot.project);
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

        runtime.__REVIEW_TEST_COMMANDS__.push(command);
        if (command === "bootstrap") return structuredClone(initialBoot);
        if (command === "issue_effect_grant") return { token: "synthetic-test-grant" };
        if (command === "render_motion_canvas") return structuredClone(nativeReceipt);
        if (command === "preview_native_frame") {
          const request = args?.request as { token: string };
          if (request.token !== "synthetic-review-token") throw new Error("Unknown synthetic grant");
          // The fixture matches the declared output dimensions, but it is not
          // a renderer artifact or a source of product evidence.
          const canvas = document.createElement("canvas");
          canvas.width = nativeWidth; canvas.height = nativeHeight;
          const ctx = canvas.getContext("2d");
          if (!ctx) throw new Error("Browser fixture canvas unavailable");
          ctx.fillStyle = "#283441";
          ctx.fillRect(0, 0, nativeWidth, nativeHeight);
          const encoded = canvas.toDataURL("image/png").split(",")[1];
          return Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
        }
        throw new Error("Unexpected synthetic native operation: " + command);
      },
    };
  }, { initialBoot: boot, nativeReceipt: receipt, nativeWidth: profile.width, nativeHeight: profile.height });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await saveFixtureRecipe(page);
  const revision = await page.locator(".revision-chip").innerText();
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("button", { name: "Native inspection", exact: true }).click();
  await page.getByRole("button", { name: "Inspect current native frames" }).click();
  await expect(page.getByRole("button", { name: /Inspect native frame 0/ })).toBeVisible();
  await page.getByLabel("Affected object").selectOption(target!.id);
  await page.getByLabel("Violated constraint").fill("Legibility and continuity of the selected text");
  await page.getByLabel("What is visible in this frame").fill("The current entry visually overlaps a neighboring element.");
  await page.getByLabel("Proposed localized repair").fill("Move the selected text only after human preview.");

  const prepare = page.getByRole("button", { name: "Draft scoped repair" });
  await expect(prepare).toBeEnabled();
  await prepare.click();
  await expect(page.getByRole("heading", { name: "Scoped changes", exact: true })).toBeVisible();
  await expect(page.getByLabel("Patch target object")).toHaveValue(target!.id);
  await expect(page.getByLabel("Patch target object")).toBeDisabled();
  await expect(page.getByText(/Native frame 0 · source r/)).toBeVisible();
  const rationale = page.getByLabel("Patch rationale");
  await expect(rationale).toHaveValue(/Manual native-frame observation; repair NOT verified/);
  await expect(rationale).toHaveValue(/Legibility and continuity/);
  await expect(rationale).toHaveValue(/PNG SHA-256 /);
  await expect(page.locator(".revision-chip")).toHaveText(revision);

  const add = page.getByRole("button", { name: "Add to proposal" });
  await expect(add).toBeDisabled();
  await page.getByLabel("Patch replacement text").fill("Human-entered replacement text");
  await expect(add).toBeEnabled();
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(page.locator(".production-patch [role=alert]")).toContainText("selected output profile");
  await expect(add).toBeDisabled();
  await page.getByLabel("Editor preview timebase").selectOption(profile.id);
  await expect(page.locator(".production-patch [role=alert]")).toHaveCount(0);
  await expect(add).toBeEnabled();
  await add.click();
  await expect(page.getByRole("region", { name: "Proposed edits" })).toContainText(target!.name);
  await expect(page.locator(".revision-chip")).toHaveText(revision);

  const commands = await page.evaluate(() => (
    window as unknown as { __REVIEW_TEST_COMMANDS__: string[] }
  ).__REVIEW_TEST_COMMANDS__);
  expect(commands).not.toContain("apply_change");
  expect(commands).not.toContain("preview_creative_patch");
  await page.getByRole("button", { name: "Discard proposal" }).click();
  await expect(page.getByText(/Native frame 0 · source r/)).toHaveCount(0);
  await expect(page.locator(".revision-chip")).toHaveText(revision);
});
