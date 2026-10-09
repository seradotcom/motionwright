import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("history enumerates all local jobs beyond recent-window limit with no driver mutation", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const job = {
    job_ref: "local-job-000",
    provider: "driver:motion-canvas",
    root_request_id: "source-001",
    generation: boot.project.generation,
    revision: boot.project.revision,
    state: "succeeded",
    cancellation_requested: false,
    applicability: "current",
    last_command: "driver.motion-canvas.render.result",
    created_at: "2026-10-08T01:00:00Z",
    last_observed_at: "2026-10-08T01:00:02Z",
    local_observations: 4,
    provider_generation: 7,
    progress: { completed: 60, total: 60, message: null },
    artifact_available: true,
    result_available: true,
    last_observation: "observed",
  };
  const jobs = Array.from({ length: 42 }, (_, i) => ({
    ...job,
    job_ref: "local-job-" + String(i).padStart(3, "0"),
    root_request_id: "request-" + String(i).padStart(3, "0"),
  }));

  await page.addInitScript(({ initialBoot, historical }) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
      __HISTORY_JOBS__: {
        requests: Array<Record<string, unknown>>;
        receiptChanged: boolean;
        head: string;
        stamp: string;
      };
    };
    app.__HISTORY_JOBS__ = {
      requests: [],
      receiptChanged: false,
      head: "019deda0-0000-7000-8000-000000000001",
      stamp: "original",
    };
    app.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initialBoot);
        if (command === "production_jobs") return structuredClone(historical.slice(0, 1));
        if (command === "production_jobs_history") {
          const request = args?.request as {
            project_id: string; generation: string; revision: number;
            limit: number; cursor: { latest_id: string; receipt_count: number; offset: number } | null;
          };
          app.__HISTORY_JOBS__.requests.push(request as unknown as Record<string, unknown>);
          if (request.cursor && app.__HISTORY_JOBS__.receiptChanged) {
            throw new Error("Production receipts changed during historical paging; restart the history snapshot.");
          }
          const start = request.cursor?.offset ?? 0;
          const end = Math.min(historical.length, start + request.limit);
          return {
            items: structuredClone(historical.slice(start, end)),
            total_jobs: historical.length,
            complete: end >= historical.length,
            next: end < historical.length
              ? { latest_id: app.__HISTORY_JOBS__.head, receipt_count: 168, offset: end }
              : null,
          };
        }
        throw new Error("Historical synthetic bridge cannot dispatch command " + command);
      },
    };
  }, { initialBoot: boot, historical: jobs });

  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  const recent = page.getByRole("table", { name: "Canonical production job receipts" });
  await expect(recent.getByRole("row")).toHaveCount(2);
  const revision = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Browse full production history" }).click();
  const history = page.getByRole("table", { name: "Historical production job receipts" });
  await expect(history.getByRole("row")).toHaveCount(17);
  await expect(page.getByText("16 / 42 historical jobs")).toBeVisible();
  await expect(page.getByLabel("Historical production snapshot")).toContainText("Read-only historical snapshot");
  await page.getByRole("button", { name: "Load older production jobs" }).click();
  await expect(history.getByRole("row")).toHaveCount(33);
  await page.getByRole("button", { name: "Load older production jobs" }).click();
  await expect(history.getByRole("row")).toHaveCount(43);
  await expect(page.getByText("42 / 42 historical jobs")).toBeVisible();
  await expect(page.getByRole("button", { name: "Load older production jobs" })).toBeDisabled();
  await expect(page.getByText("complete at verified snapshot")).toBeVisible();

  const snapshotRequests = await page.evaluate(() => (
    window as unknown as { __HISTORY_JOBS__: { requests: Array<Record<string, unknown>> } }
  ).__HISTORY_JOBS__.requests);
  expect(snapshotRequests).toHaveLength(3);
  expect(snapshotRequests.map((request) => (
    request.cursor as { offset: number } | null
  )?.offset ?? 0)).toEqual([0, 16, 32]);
  expect(snapshotRequests.every((request) =>
    request.project_id === boot.project.id
    && request.generation === boot.project.generation
    && request.revision === boot.project.revision
    && request.limit === 16
    && !("path" in request)
    && !("sha256" in request)
    && !("effect_grant" in request))).toBe(true);
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.getByRole("button", { name: "Back to recent jobs" }).click();
  await expect(recent.getByRole("row")).toHaveCount(2);
});

test("independent status append invalidates history cursor and requires explicit restart", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const job = {
    job_ref: "historic-job",
    provider: "driver:mlt-video",
    root_request_id: "historic-request",
    generation: boot.project.generation,
    revision: boot.project.revision,
    state: "cancel_requested",
    cancellation_requested: true,
    applicability: "current",
    last_command: "driver.mlt-video.render.cancel",
    created_at: "2026-10-08T01:00:00Z",
    last_observed_at: "2026-10-08T01:00:02Z",
    local_observations: 4,
    provider_generation: 1,
    progress: null,
    artifact_available: false,
    result_available: false,
    last_observation: "observed",
  };
  await page.addInitScript(({ initialBoot, initialJob }) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __APPEND_STATUS__: { changed: boolean; initialReads: number };
    };
    app.__APPEND_STATUS__ = { changed: false, initialReads: 0 };
    app.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initialBoot);
        if (command === "production_jobs") return [structuredClone(initialJob)];
        if (command === "production_jobs_history") {
          const request = args?.request as { cursor: { offset: number } | null };
          if (request.cursor && app.__APPEND_STATUS__.changed) {
            throw new Error("Production receipt stream changed during enumeration");
          }
          if (!request.cursor) app.__APPEND_STATUS__.initialReads++;
          return {
            items: Array.from({ length: 16 }, (_, i) => ({
              ...initialJob, job_ref: "historic-" + String((request.cursor?.offset ?? 0) + i),
            })),
            total_jobs: 35,
            next: { latest_id: "019deda0-0000-7000-8000-000000000001",
              receipt_count: 140, offset: (request.cursor?.offset ?? 0) + 16 },
            complete: false,
          };
        }
        throw Error("Synthetic historical test cannot perform " + command);
      },
    };
  }, { initialBoot: boot, initialJob: job });

  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  await page.getByRole("button", { name: "Browse full production history" }).click();
  await expect(page.getByRole("table", { name: "Historical production job receipts" })
    .getByRole("row")).toHaveCount(17);
  const revision = await page.locator(".revision-chip").first().innerText();
  await page.evaluate(() => {
    (window as unknown as { __APPEND_STATUS__: { changed: boolean } }).__APPEND_STATUS__.changed = true;
  });
  await page.getByRole("button", { name: "Load older production jobs" }).click();
  await expect(page.getByRole("alert")).toContainText("Production receipt stream changed");
  await expect(page.getByRole("button", { name: "Load older production jobs" })).toBeDisabled();
  await expect(page.getByRole("table", { name: "Historical production job receipts" })
    .getByRole("row")).toHaveCount(17);
  await page.getByRole("button", { name: "Restart history" }).click();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __APPEND_STATUS__: { initialReads: number } }
  ).__APPEND_STATUS__.initialReads)).toBe(2);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("browser demo cannot synthesize historical jobs or a mutable backend", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  await expect(page.getByRole("button", { name: "Browse full production history" })).toBeDisabled();
  await expect(page.getByRole("table", { name: "Historical production job receipts" })).toHaveCount(0);
});
