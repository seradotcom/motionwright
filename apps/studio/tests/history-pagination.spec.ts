import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("recent-first committed history exposes all 260 revisions with bounded older pages", async ({ page }) => {
  // Tauri bridge is synthetic; Rust SQLite keyset semantics have a separate native test.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  boot.project.revision = 260;
  boot.project.branches[0].head_revision = 260;
  const journal = Array.from({ length: 260 }, (_, index) => {
    const revision = index + 1;
    return {
      revision,
      change: { type: "rename_project", title: "Revision " + revision },
      created_at: "2026-10-08T12:00:00Z",
    };
  });

  await page.addInitScript(({ initial, rows }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
      __TEST_HISTORY__: { cursors: number[] };
    };
    runtime.__TEST_HISTORY__ = { cursors: [] };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initial);
        if (command === "project_history_recent") {
          const request = args?.request as { through_revision: number; limit: number };
          runtime.__TEST_HISTORY__.cursors.push(request.through_revision);
          return rows.filter((event) => event.revision <= request.through_revision)
            .sort((a, b) => b.revision - a.revision)
            .slice(0, request.limit);
        }
        throw new Error("Unsupported mock operation: " + command);
      },
    };
  }, { initial: boot, rows: journal });

  await page.goto("/");
  await page.getByRole("button", { name: "Changes", exact: true }).click();
  const table = page.getByRole("table", { name: "Project event journal" });
  const rows = table.locator(".event-row:not(.data-head)");
  const revision = await page.locator(".revision-chip").first().innerText();

  await expect(rows).toHaveCount(100);
  await expect(rows.first()).toContainText("r260");
  await expect(rows.last()).toContainText("r161");
  await expect(table.getByText("r1", { exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Load earlier changes" }).click();
  await expect(rows).toHaveCount(200);
  await expect(rows.last()).toContainText("r61");

  await page.getByRole("button", { name: "Load earlier changes" }).click();
  await expect(rows).toHaveCount(260);
  await expect(rows.last()).toContainText("r1");
  await expect(page.getByRole("button", { name: "Load earlier changes" })).toHaveCount(0);
  await expect(page.getByText("Beginning of recorded history")).toBeVisible();
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  const seen = await rows.locator("span:first-child").allTextContents();
  expect(seen).toEqual(Array.from({ length: 260 }, (_, i) => "r" + (260 - i)));
  const cursors = await page.evaluate(() => (window as unknown as {
    __TEST_HISTORY__: { cursors: number[] };
  }).__TEST_HISTORY__.cursors);
  // StrictMode may repeat the initial read, but later pages remain keyset-only.
  expect(cursors).toContain(260);
  expect(cursors).toContain(160);
  expect(cursors).toContain(60);
  expect(cursors.every((cursor) => [260, 160, 60].includes(cursor))).toBe(true);
});

test("an earlier-history read error keeps the recent page and allows retry", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  boot.project.revision = 108;
  boot.project.branches[0].head_revision = 108;
  const journal = Array.from({ length: 108 }, (_, index) => ({
    revision: index + 1,
    change: { type: "rename_project", title: "Changed " + (index + 1) },
    created_at: "2026-10-08T12:00:00Z",
  }));
  await page.addInitScript(({ initial, rows }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
    };
    let failOnce = true;
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return structuredClone(initial);
        if (command === "project_history_recent") {
          const request = args?.request as { through_revision: number; limit: number };
          if (request.through_revision < 108 && failOnce) {
            failOnce = false;
            throw new Error("Synthetic transient history read failure");
          }
          return rows.filter((event) => event.revision <= request.through_revision)
            .sort((a, b) => b.revision - a.revision)
            .slice(0, request.limit);
        }
        throw new Error("Unsupported synthetic command: " + command);
      },
    };
  }, { initial: boot, rows: journal });

  await page.goto("/");
  await page.getByRole("button", { name: "Changes", exact: true }).click();
  const rows = page.getByRole("table", { name: "Project event journal" })
    .locator(".event-row:not(.data-head)");
  await expect(rows).toHaveCount(100);
  await page.getByRole("button", { name: "Load earlier changes" }).click();
  await expect(page.getByRole("alert")).toContainText("Synthetic transient history read failure");
  await expect(rows).toHaveCount(100);
  await page.getByRole("button", { name: "Retry history read" }).click();
  await expect(rows).toHaveCount(108);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
