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


test("sampled native PNG readback uses only a session grant and follows the editorial playhead", async ({ page }) => {
  // Synthetic Tauri transport. Real Semwright bytes/digests are checked in Rust and native E2E.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const master = boot.project.deliverables[0];
  const rendered = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision + 1,
    deliverable_id: master.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "mock-native-segment",
      scene_ids: [first.id, second.id],
      frame_count: 450,
      plan_ref: "mock-plan",
      fingerprint: "mock-digest",
      job_ref: "mock-job",
      artifact: { kind: "synthetic-only" },
      verification: { status: "synthetic-only" },
    }],
    preview: [{
      token: "mock-read-token",
      segment_id: "mock-native-segment",
      scene_ids: [first.id, second.id],
      frame_count: 450,
    }],
  };
  const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4XyX4HwAGkAKKXzfbLwAAAABJRU5ErkJggg==";
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initialBoot, receipt, fixturePng }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __SYNTHETIC_READS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initialBoot.project);
    runtime.__SYNTHETIC_READS__ = [];
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === project.id && recipeRequest.generation === project.generation
            && recipeRequest.revision === project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          project = await apply(project, recipeRequest);
          initialBoot.project = structuredClone(project);
          receipt.revision = project.revision; // Future synthetic native reply uses the saved source.
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

        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, { ...initialBoot, project: structuredClone(project) });
        }
        if (command === "issue_effect_grant") return { token: "mock-grant" };
        if (command === "render_motion_canvas") return structuredClone(receipt);
        if (command === "preview_native_frame") {
          const request = args?.request as Record<string, unknown>;
          runtime.__SYNTHETIC_READS__.push(request);
          if (request.token !== "mock-read-token") throw new Error("Unrecognized preview grant");
          return Uint8Array.from(atob(fixturePng), (char) => char.charCodeAt(0));
        }
        if (command === "apply_change") {
          const request = args?.request as { change: { type: string; title?: string } };
          if (request.change.type !== "rename_project") throw new Error("Unsupported synthetic project edit");
          project = { ...project, title: request.change.title || "Edited", revision: project.revision + 1 };
          return structuredClone(project);
        }
        throw new Error("Unsupported synthetic Tauri command: " + command);
      },
    };
  }, { initialBoot: boot, receipt: rendered, fixturePng: png });

  await page.goto("/");
  const preview = page.getByRole("region", { name: "Program preview" });
  await expect(preview).toContainText("Design representation");
  await expect(page.getByRole("button", { name: "Show native frames" })).toHaveCount(0);

  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByRole("button", { name: "Show native frames" }).click();
  const frame = page.getByRole("region", { name: "Verified native PNG frame preview" });
  const image = frame.getByRole("img", { name: /Verified native rendered PNG/ });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBe(1);
  await expect(preview).toContainText("Verified native PNG · sampled frames");
  await expect(frame).toContainText("Settled exact source PNG · no synthesized frames");
  const revision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __SYNTHETIC_READS__: Array<{ frame_index: number }> }
  ).__SYNTHETIC_READS__.some((request) => request.frame_index === 1))).toBe(true);
  const requests = await page.evaluate(() => (
    window as unknown as { __SYNTHETIC_READS__: Array<Record<string, unknown>> }
  ).__SYNTHETIC_READS__);
  expect(requests.every((request) => request.project_id === boot.project.id
    && request.revision === boot.project.revision + 1
    && request.token === "mock-read-token"
    && !("path" in request)
    && !("sha256" in request))).toBe(true);
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.getByRole("button", { name: "Show design" }).click();
  await expect(frame).toHaveCount(0);
  await expect(preview).toContainText("Design representation");
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(page.getByRole("button", { name: "Show native frames" })).toHaveCount(0);

  await page.getByLabel("Editor preview timebase").selectOption(master.id);
  await page.getByRole("button", { name: "Show native frames" }).click();
  await expect(frame.getByRole("img")).toBeVisible();
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.locator("#project-title-rich").fill("New revision after native preview");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(preview).toContainText("STALE receipt");
  await expect(page.getByRole("region", { name: "Verified native PNG frame preview" })).toHaveCount(0);
});

test("invalid synthetic frame bytes display a truthful readback error and a retry", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const receipt = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision + 1,
    deliverable_id: boot.project.deliverables[0].id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "bad-frame",
      scene_ids: [first.id],
      frame_count: 210,
      plan_ref: "mock",
      fingerprint: "mock",
      job_ref: "mock",
      artifact: {},
      verification: {},
    }],
    preview: [{ token: "bad-token", segment_id: "bad-frame", scene_ids: [first.id], frame_count: 210 }],
  };
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initialBoot, initialReceipt }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
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
          initialReceipt.revision = initialBoot.project.revision; // Future synthetic native reply uses the saved source.
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

        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(initialBoot));
        }
        if (command === "issue_effect_grant") return { token: "mock-grant" };
        if (command === "render_motion_canvas") return structuredClone(initialReceipt);
        if (command === "preview_native_frame") return Uint8Array.from([1, 2, 3, 4, 5]);
        throw new Error("Unsupported mock operation: " + command);
      },
    };
  }, { initialBoot: boot, initialReceipt: receipt });

  await page.goto("/");
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
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByRole("button", { name: "Show native frames" }).click();
  await expect(page.getByRole("alert")).toContainText("Native frame readback unavailable");
  await expect(page.getByRole("alert")).toContainText("valid PNG");
  await expect(page.getByRole("button", { name: "Retry this frame" })).toBeVisible();
});
