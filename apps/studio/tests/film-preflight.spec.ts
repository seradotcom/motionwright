import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("native semantic Film preflight is read-only and cannot be mistaken for real render", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const sceneA = boot.project.scenes[0];
  const sceneB = boot.project.scenes[1];
  await page.addInitScript(({ initial }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __PREFLIGHT__: { calls: Array<{ project_id: string; options: unknown }>; renders: number };
    };
    runtime.__PREFLIGHT__ = { calls: [], renders: 0 };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initial);
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
  await page.addInitScript(({ initial }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initial);
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
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("Native Film preflight result")).toContainText("Unsupported camera zoom");
  await expect(page.getByRole("button", { name: "Render native segments" })).toBeDisabled();

  await page.getByLabel(boot.project.scenes[0].name + " narrative role").selectOption("reveal");
  await expect(page.getByLabel("Native Film preflight result")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Render native segments" })).toBeEnabled();
});

test("browser demo cannot invent native Film semantic projection readiness", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check Film projection" })).toBeDisabled();
  await expect(page.getByLabel("Native Film preflight result")).toHaveCount(0);
});
