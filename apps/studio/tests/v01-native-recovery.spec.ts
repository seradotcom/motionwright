import { expect, test } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";

test("v0.1 owner sees explicit UNKNOWN recovery path without any automatic retry", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const projection = {
    job_ref: "owner-job-id-private",
    provider: "driver:motion-canvas",
    root_request_id: "owner-render-original",
    generation: boot.project.generation,
    revision: boot.project.revision,
    state: "outcome_unknown",
    cancellation_requested: false,
    applicability: "current",
    last_command: "driver.motion-canvas.render.status",
    created_at: "2026-10-12T01:00:00Z",
    last_observed_at: "2026-10-12T01:00:10Z",
    local_observations: 3,
    provider_generation: null,
    progress: null,
    artifact_available: false,
    result_available: false,
    last_observation: "outcome_unknown",
  };
  await page.addInitScript(({ boot, job }) => {
    const owner = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __OWNER_RECOVERY__: { reads: number; forbidden: string[] };
    };
    owner.__OWNER_RECOVERY__ = { reads: 0, forbidden: [] };
    owner.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(boot));
        }
        if (command === "production_jobs") {
          owner.__OWNER_RECOVERY__.reads++;
          const request = args?.request as {project_id: string};
          if (request.project_id !== boot.project.id) throw Error("Wrong project queried");
          return [structuredClone(job)];
        }
        if (command === "production_jobs_history") {
          return { items: [structuredClone(job)], total_jobs: 1, complete: true, next: null };
        }
        owner.__OWNER_RECOVERY__.forbidden.push(command);
        throw Error("Native operation was not authorized during local receipt inspection");
      },
    };
  }, { boot, job: projection });
  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  const warning = page.getByLabel("Unconfirmed native render recovery");
  await expect(warning).toContainText("Original native render outcome is unknown");
  await expect(warning).toContainText("Do not automatically start a replacement render");
  await expect(warning).toContainText("leave the original result UNKNOWN");
  const table = page.getByRole("table", { name: "Canonical production job receipts" });
  await expect(table).toContainText("OUTCOME UNKNOWN");
  await page.getByRole("button", { name: "Refresh production receipts" }).click();
  await expect(warning).toBeVisible();
  await page.getByRole("button", { name: "Browse full production history" }).click();
  await expect(page.getByLabel("Unconfirmed native render recovery")).toBeVisible();
  const calls = await page.evaluate(() => (
    window as unknown as {__OWNER_RECOVERY__: {reads: number; forbidden: string[]}}
  ).__OWNER_RECOVERY__);
  expect(calls.reads).toBeGreaterThanOrEqual(2);
  expect(calls.forbidden).toHaveLength(0);
});

test("browser-only owner Jobs has no fabricated recovery warning", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  await expect(page.getByLabel("Unconfirmed native render recovery")).toHaveCount(0);
});
