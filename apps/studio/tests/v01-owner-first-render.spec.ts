import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("v0.1 first owner render requires exact Film and rechecked CLI without faking a provider", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  await page.addInitScript(({ initial }) => {
    const simulated = window as unknown as {
      __OWNER_FIRST_RENDER__: { compatible: boolean; attempts: number; effects: number };
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    simulated.__OWNER_FIRST_RENDER__ = { compatible: true, attempts: 0, effects: 0 };
    simulated.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      if (command === "bootstrap") return structuredClone(initial);
      if (command === "production_runtime_status") {
        const request = args?.request as {
          project_id: string; expected_generation: string; expected_revision: number;
        };
        if (request.project_id !== initial.project.id
            || request.expected_generation !== initial.project.generation
            || request.expected_revision !== initial.project.revision) {
          throw new Error("Unscoped CLI compatibility probe denied");
        }
        simulated.__OWNER_FIRST_RENDER__.attempts++;
        return {
          status: simulated.__OWNER_FIRST_RENDER__.compatible ? "ready" : "unavailable",
          reason: simulated.__OWNER_FIRST_RENDER__.compatible ? null : "No compatible CLI",
          expected_version: "1.0.0", observed_version: simulated.__OWNER_FIRST_RENDER__.compatible ? "1.0.0" : null,
          version_compatible: simulated.__OWNER_FIRST_RENDER__.compatible,
          executable_sha256: "a".repeat(64),
          connection_identity: simulated.__OWNER_FIRST_RENDER__.compatible ? "b".repeat(64) : null,
          pinned_revision: "c".repeat(40),
        };
      }
      if (command === "motion_canvas_preflight") {
        const request = args?.request as {project_id: string; generation: string; revision: number; deliverable_id: string};
        return {
          project_resource: "project:" + request.project_id,
          generation: request.generation, revision: request.revision, deliverable_id: request.deliverable_id,
          verdict: "projection_ready", reason: null,
          segment_count: 1, total_frames: 60,
          segments: [{segment_id: "mock-film-source",scene_ids: [],frame_count: 60}],
        };
      }
      if (command === "issue_effect_grant") {
        simulated.__OWNER_FIRST_RENDER__.effects++;
        throw new Error("No owner effects in this UI contract fixture");
      }
      if (command === "render_motion_canvas") throw new Error("No synthetic native rendering allowed");
      throw new Error("Unsupported synthetic command " + command);
    }};
  }, { initial: boot });
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const render = page.getByRole("button", { name: "Render native segments" });
  await expect(render).toBeDisabled();
  for (const [scene, role, archetype] of [
    [boot.project.scenes[0], "hook", "statement"],
    [boot.project.scenes[1], "problem", "diagram_build"],
  ] as const) {
    await page.getByLabel(scene.name + " narrative role").selectOption(role);
    await page.getByLabel(scene.name + " motion archetype").selectOption(archetype);
  }
  await expect(render).toBeDisabled();
  await page.getByRole("button", { name: "Check Film projection" }).click();
  const gate = page.getByLabel("First native production readiness");
  await expect(gate).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await expect(gate).toContainText("NOT yet verified");
  await expect(render).toBeEnabled();
  // A connection may have been revoked between the UI observation and the
  // click. The handler must re-probe before requesting the native effect.
  await page.evaluate(() => {
    (window as unknown as { __OWNER_FIRST_RENDER__: { compatible: boolean } })
      .__OWNER_FIRST_RENDER__.compatible = false;
  });
  await render.click();
  await expect(gate).toContainText("RUNTIME NOT COMPATIBLE");
  await expect(render).toBeDisabled();
  const calls = await page.evaluate(() => (
    window as unknown as { __OWNER_FIRST_RENDER__: {attempts: number; effects: number} }
  ).__OWNER_FIRST_RENDER__);
  expect(calls.attempts).toBeGreaterThanOrEqual(2);
  expect(calls.effects).toBe(0);
});

test("browser demo explicitly rejects owner-native dispatch without any grants", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("BROWSER ONLY");
  await expect(page.getByRole("button", { name: "Render native segments" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Recheck Semwright CLI" })).toBeDisabled();
});
