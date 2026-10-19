import type { Page } from "@playwright/test";
import type { ProductionJobProjection } from "../src/types";
import { expect, test } from "./support/selectedSyntheticProject";
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
        if (command === "bootstrap") {
          const syntheticResponse = structuredClone(initialBoot);
          const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
          if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
            throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
          }
          return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
        }
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
        if (command === "bootstrap") {
          const syntheticResponse = structuredClone(initialBoot);
          const explicitlySelectedId = (args as { request?: { project_id?: string | null } } | undefined)?.request?.project_id;
          if (!syntheticResponse.project || explicitlySelectedId !== syntheticResponse.project.id) {
            throw new Error("Synthetic bootstrap requires the explicitly selected matching project identity.");
          }
          return { ...syntheticResponse, project_selection: { status: "selected", project_id: syntheticResponse.project.id } };
        }
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
  const historyError = page.getByRole("alert")
    .filter({ hasText: "Historical receipt snapshot could not be continued" });
  await expect(historyError).toContainText("Production receipt stream changed");
  // The owner safety warning about the original UNKNOWN native operation
  // remains separate from an unrelated expired read-only history cursor.
  await expect(page.getByLabel("Unconfirmed native render recovery")).toBeVisible();
  await expect(page.getByRole("button", { name: "Load older production jobs" })).toBeDisabled();
  await expect(page.getByRole("table", { name: "Historical production job receipts" })
    .getByRole("row")).toHaveCount(17);
  await page.getByRole("button", { name: "Restart history" }).click();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __APPEND_STATUS__: { initialReads: number } }
  ).__APPEND_STATUS__.initialReads)).toBe(2);
  await expect(historyError).toHaveCount(0);
  await expect(page.getByLabel("Unconfirmed native render recovery")).toBeVisible();
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("browser demo cannot synthesize historical jobs or a mutable backend", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  await expect(page.getByRole("button", { name: "Browse full production history" })).toBeDisabled();
  await expect(page.getByRole("table", { name: "Historical production job receipts" })).toHaveCount(0);
});

type IdentityBridge = {
  commands: string[];
  statusRequests: Array<Record<string, unknown>>;
  historyRequests: Array<Record<string, unknown>>;
};

function identityJob(boot: typeof fixtureBootstrap): ProductionJobProjection {
  return {
    job_ref: "shared-native-job",
    provider: "driver:motion-canvas",
    root_request_id: "original-motion-start",
    generation: boot.project.generation,
    revision: boot.project.revision,
    state: "succeeded",
    cancellation_requested: false,
    applicability: "current",
    last_command: "driver.motion-canvas.render.result",
    created_at: "2026-10-17T01:00:00Z",
    last_observed_at: "2026-10-17T01:00:02Z",
    local_observations: 3,
    provider_generation: 17,
    progress: { completed: 60, total: 60, message: null },
    artifact_available: true,
    result_available: true,
    last_observation: "observed",
  };
}

// Synthetic IPC proves UI correlation only; it neither authenticates a native
// receipt nor executes a provider. Unexpected commands remain visible failures.
async function installIdentityBridge(
  page: Page,
  boot: typeof fixtureBootstrap,
  jobs: ProductionJobProjection[],
  duplicateBoundaryRow = false,
) {
  await page.addInitScript(({ initial, historical, duplicate }) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
      __SCOPED_JOB_IDENTITY__: IdentityBridge;
    };
    app.__SCOPED_JOB_IDENTITY__ = {
      commands: [], statusRequests: [], historyRequests: [],
    };
    app.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        app.__SCOPED_JOB_IDENTITY__.commands.push(command);
        if (command === "bootstrap") {
          const requested = (args?.request as { project_id?: string | null } | undefined)?.project_id;
          if (!initial.project || requested !== initial.project.id) {
            throw new Error("Synthetic identity bridge requires explicitly selected project.");
          }
          return {
            ...structuredClone(initial),
            project_selection: { status: "selected", project_id: initial.project.id },
          };
        }
        if (command === "production_jobs") return structuredClone(historical.slice(0, 32));
        if (command === "production_jobs_history") {
          const request = args?.request as {
            project_id: string; generation: string; revision: number; limit: number;
            cursor: { offset: number } | null;
          };
          app.__SCOPED_JOB_IDENTITY__.historyRequests.push(
            request as unknown as Record<string, unknown>,
          );
          if (request.project_id !== initial.project.id
              || request.generation !== initial.project.generation
              || request.revision !== initial.project.revision || request.limit !== 16) {
            throw new Error("History request changed its Project scope.");
          }
          const start = request.cursor?.offset ?? 0;
          const end = Math.min(historical.length, start + request.limit);
          const items = historical.slice(start, end);
          // The known identity repeated at the page boundary must be ignored;
          // an equal job_ref from another provider or stamp must remain.
          if (duplicate && start > 0) items.unshift(historical[0]);
          return {
            items: structuredClone(items),
            total_jobs: historical.length,
            complete: end >= historical.length,
            next: end < historical.length ? {
              latest_id: "019e1d80-0000-7000-8000-000000000001",
              receipt_count: historical.length * 6,
              offset: end,
            } : null,
          };
        }
        if (command === "read_original_native_job_status") {
          const request = args?.request as Record<string, unknown>;
          app.__SCOPED_JOB_IDENTITY__.statusRequests.push(request);
          if (request.project_id !== initial.project.id
              || request.generation !== initial.project.generation
              || request.revision !== initial.project.revision
              || request.job_ref !== historical[0].job_ref
              || Object.keys(request).sort().join(",")
                !== "generation,job_ref,project_id,revision") {
            throw new Error("The original read-only IPC contract changed.");
          }
          return {
            schema: "motionwright.v01-owner-native-status/1",
            job_ref: historical[0].job_ref,
            provider_state: "succeeded_unverified",
            diagnostic: "original_native_job_status_read_only",
            provider_generation_matches_original_start: true,
            source_authority_checked: true,
            broker_observed: true,
            read_only: true,
            native_result_verified: false,
            output_bytes_verified: false,
            automatic_retry_permitted: false,
          };
        }
        throw new Error("Synthetic identity bridge cannot dispatch " + command);
      },
    };
  }, { initial: boot, historical: jobs, duplicate: duplicateBoundaryRow });
}

for (const difference of ["provider", "generation", "revision"] as const) {
  test("history keeps colliding job references across " + difference + " without duplicate identity rows",
    async ({ page }) => {
      const boot = structuredClone(fixtureBootstrap);
      boot.native_sdk.mode = "tauri";
      const original = identityJob(boot);
      const collision: ProductionJobProjection = {
        ...original, root_request_id: "other-original-start",
      };
      if (difference === "provider") {
        collision.provider = "driver:manim-community";
        collision.last_command = "driver.manim-community.render.result";
        collision.provider_generation = 31;
      } else if (difference === "generation") {
        collision.generation = "018f0000-0000-7000-8000-000000009999";
        collision.applicability = "stale";
      } else {
        expect(boot.project.revision).toBeGreaterThan(0);
        collision.revision = boot.project.revision - 1;
        collision.applicability = "stale";
      }
      const jobs = [
        original,
        ...Array.from({ length: 15 }, (_, i) => ({
          ...original, job_ref: "other-native-job-" + i, root_request_id: "other-start-" + i,
        })),
        collision,
      ];
      const duplicateKeyErrors: string[] = [];
      page.on("console", (message) => {
        if (/same key/.test(message.text())) duplicateKeyErrors.push(message.text());
      });
      await installIdentityBridge(page, boot, jobs, true);
      await page.goto("/");
      await page.getByRole("button", { name: "Jobs", exact: true }).click();
      const revision = await page.locator(".revision-chip").first().innerText();
      await page.getByRole("button", { name: "Browse full production history" }).click();
      const history = page.getByRole("table", { name: "Historical production job receipts" });
      await expect(history.getByRole("row")).toHaveCount(17);
      await page.getByRole("button", { name: "Load older production jobs" }).click();
      await expect(history.getByRole("row")).toHaveCount(18);
      await expect(page.getByText("17 / 17 historical jobs")).toBeVisible();
      await expect(page.getByRole("button", { name: "Load older production jobs" })).toBeDisabled();
      const originalRow = history.getByRole("row").nth(1);
      const collisionRow = history.getByRole("row").nth(17);
      for (const [row, expected] of [[originalRow, original], [collisionRow, collision]] as const) {
        await expect(row.locator(".job-ref-cell strong")).toHaveAttribute("title", expected.job_ref);
        await expect(row.getByText(expected.provider, { exact: true })).toBeVisible();
        await expect(row.getByRole("cell").nth(3).getByText("r" + expected.revision, { exact: true }))
          .toBeVisible();
        await expect(row.locator('span[title="' + expected.generation + '"]')).toHaveCount(1);
      }
      const bridge = await page.evaluate(() => (window as unknown as {
        __SCOPED_JOB_IDENTITY__: IdentityBridge;
      }).__SCOPED_JOB_IDENTITY__);
      expect(bridge.historyRequests.map((request) =>
        (request.cursor as { offset: number } | null)?.offset ?? 0)).toEqual([0, 16]);
      expect(bridge.statusRequests).toEqual([]);
      expect(bridge.commands.filter((command) => ![
        "bootstrap", "production_jobs", "production_jobs_history",
      ].includes(command))).toEqual([]);
      expect(duplicateKeyErrors).toEqual([]);
      await expect(page.locator(".revision-chip").first()).toHaveText(revision);
    });
}

test("an original Motion Canvas observation never decorates another provider or historical stamp",
  async ({ page }) => {
    const boot = structuredClone(fixtureBootstrap);
    boot.native_sdk.mode = "tauri";
    const original: ProductionJobProjection = {
      ...identityJob(boot), state: "outcome_unknown",
      last_command: "driver.motion-canvas.render.status",
      progress: null, artifact_available: false, result_available: false,
      last_observation: "outcome_unknown",
    };
    const jobs: ProductionJobProjection[] = [
      original,
      { ...original, provider: "driver:manim-community", root_request_id: "manim-start",
        last_command: "driver.manim-community.render.status", provider_generation: 31 },
      { ...original, generation: "018f0000-0000-7000-8000-000000009999",
        root_request_id: "historical-generation-start", applicability: "stale" },
      { ...original, revision: boot.project.revision - 1,
        root_request_id: "historical-revision-start", applicability: "stale" },
    ];
    expect(boot.project.revision).toBeGreaterThan(0);
    await installIdentityBridge(page, boot, jobs);
    await page.goto("/");
    await page.getByRole("button", { name: "Jobs", exact: true }).click();
    const recent = page.getByRole("table", { name: "Canonical production job receipts" });
    await expect(recent.getByRole("row")).toHaveCount(5);
    const revision = await page.locator(".revision-chip").first().innerText();
    await expect(recent.getByRole("button", { name: "Check original native job status" })).toHaveCount(1);
    await recent.getByRole("button", { name: "Check original native job status" }).click();
    const evidenceName = "Original native job status observation";
    await expect(recent.getByRole("row").nth(1).getByRole("status", { name: evidenceName }))
      .toContainText("succeeded unverified");
    for (let index = 2; index <= 4; index++) {
      await expect(recent.getByRole("row").nth(index).getByRole("status", { name: evidenceName }))
        .toHaveCount(0);
    }
    await expect(recent.getByRole("status", { name: evidenceName })).toHaveCount(1);
    await expect(recent.getByRole("row").nth(1)).toContainText("OUTCOME UNKNOWN");

    await page.getByRole("button", { name: "Browse full production history" }).click();
    const history = page.getByRole("table", { name: "Historical production job receipts" });
    await expect(history.getByRole("row")).toHaveCount(5);
    await expect(history.getByRole("row").nth(1).getByRole("status", { name: evidenceName }))
      .toContainText("no result, verified pixels");
    for (let index = 2; index <= 4; index++) {
      await expect(history.getByRole("row").nth(index).getByRole("status", { name: evidenceName }))
        .toHaveCount(0);
    }
    const bridge = await page.evaluate(() => (window as unknown as {
      __SCOPED_JOB_IDENTITY__: IdentityBridge;
    }).__SCOPED_JOB_IDENTITY__);
    expect(bridge.statusRequests).toEqual([{
      project_id: boot.project.id, generation: boot.project.generation,
      revision: boot.project.revision, job_ref: original.job_ref,
    }]);
    expect(bridge.commands.filter((command) => ![
      "bootstrap", "production_jobs", "production_jobs_history", "read_original_native_job_status",
    ].includes(command))).toEqual([]);
    await expect(page.locator(".revision-chip").first()).toHaveText(revision);
  });
