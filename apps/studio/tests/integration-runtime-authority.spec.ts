import { expect, test, type Page } from "@playwright/test";

// Scheduling-only evidence for actual Integration UI/API. The IPC transport is
// explicit synthetic input; it cannot establish native chooser/runtime readiness.
type Call = { id: number; command: string; request: Record<string, unknown>; settled: boolean };
type Snapshot = { calls: Call[]; commits: number; unexpectedCommands: string[];
  projectsIntact: boolean; projectA: string; projectB: string };
type Control = { snapshot(): Snapshot; status(id: number, label: string): void;
  selection(id: number, changed: boolean): void; reject(id: number, label: string): void };
const snapshot = (page: Page) => page.evaluate(() =>
  (window as unknown as { __integrationRuntimeSchedule: Control }).__integrationRuntimeSchedule.snapshot());
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const boundary = (page: Page) => page.getByRole("region", { name: "Runtime boundaries", exact: true })
  .locator(".integration-boundary-row").filter({ hasText: "Semwright production runtime" });
const note = (page: Page) => page.getByLabel("Configure Semwright runtime", { exact: true }).getByRole("status");
async function count(page: Page, expected: number) {
  await expect.poll(async () => (await snapshot(page)).calls.length).toBe(expected);
}
async function settle(page: Page, id: number, kind: "status" | "selection" | "reject", value: string | boolean) {
  await page.evaluate(({ id, kind, value }) => {
    const control = (window as unknown as { __integrationRuntimeSchedule: Control }).__integrationRuntimeSchedule;
    if (kind === "status") control.status(id, String(value));
    else if (kind === "selection") control.selection(id, Boolean(value));
    else control.reject(id, String(value));
  }, { id, kind, value });
  await expect.poll(async () => (await snapshot(page)).calls[id].settled).toBe(true);
  await page.evaluate(() => new Promise<void>(resolve =>
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/fixtures/integration-runtime-authority.html");
  await expect(page.getByRole("heading", { name: "Synthetic Integration runtime scheduling fixture" })).toBeVisible();
  await count(page, 1);
  const initial = (await snapshot(page)).calls[0];
  expect(initial.command).toBe("production_runtime_status");
  expect(initial.request.project_id).toBe((await snapshot(page)).projectA);
});
test.afterEach(async ({ page }, testInfo) => {
  const observed = await snapshot(page);
  await testInfo.attach("synthetic-runtime-schedule.json", {
    body: JSON.stringify(observed, null, 2), contentType: "application/json",
  });
  expect(observed.commits).toBe(0);
  expect(observed.unexpectedCommands).toEqual([]);
  expect(observed.projectsIntact).toBe(true);
});
test("late initial success cannot replace a completed owner selection and clear invalidates its current view", async ({ page }) => {
  // Initial probe 0 is still pending when the actual UI starts owner action 1.
  await button(page, "Choose Semwright connection").click(); await count(page, 2);
  expect((await snapshot(page)).calls[1].command).toBe("choose_semwright_connection");
  await settle(page, 1, "selection", true); await count(page, 3);
  await settle(page, 2, "status", "CURRENT SELECTED CONNECTION");
  await expect(boundary(page)).toContainText("CURRENT SELECTED CONNECTION");
  await settle(page, 0, "status", "OBSOLETE INITIAL PROBE");
  await expect(boundary(page)).toContainText("CURRENT SELECTED CONNECTION");
  await expect(boundary(page)).not.toContainText("OBSOLETE INITIAL PROBE");
  await expect(note(page)).toContainText("Owner-selected runtime saved");

  await button(page, "Remove saved selection").click(); await count(page, 4);
  await expect(boundary(page)).toContainText("CHECKING");
  await expect(boundary(page)).not.toContainText("CURRENT SELECTED CONNECTION");
  expect((await snapshot(page)).calls[3].command).toBe("clear_saved_semwright_connection");
  await settle(page, 3, "selection", true); await count(page, 5);
  await settle(page, 4, "status", "CURRENT CLEARED CONNECTION");
  await expect(boundary(page)).toContainText("CURRENT CLEARED CONNECTION");
  await expect(note(page)).toContainText("Saved project connection reference removed");
  await expect(button(page, "Choose Semwright connection")).toBeEnabled();
});
test("late initial error cannot mark a newer clear result unavailable and current failures remain operable", async ({ page }) => {
  await button(page, "Remove saved selection").click(); await count(page, 2);
  expect((await snapshot(page)).calls[1].command).toBe("clear_saved_semwright_connection");
  await settle(page, 1, "selection", true); await count(page, 3);
  await settle(page, 2, "status", "CURRENT CLEAR RESULT");
  await expect(boundary(page)).toContainText("CURRENT CLEAR RESULT");
  await settle(page, 0, "reject", "OBSOLETE INITIAL ERROR");
  await expect(boundary(page)).toContainText("CURRENT CLEAR RESULT");
  await expect(boundary(page)).not.toContainText("UNAVAILABLE");

  await button(page, "Choose Semwright connection").click(); await count(page, 4);
  await expect(boundary(page)).toContainText("CHECKING");
  await settle(page, 3, "reject", "CURRENT OWNER SELECTION ERROR"); await count(page, 5);
  await settle(page, 4, "status", "REPROBED CURRENT CONNECTION");
  await expect(note(page)).toContainText("CURRENT OWNER SELECTION ERROR");
  await expect(boundary(page)).toContainText("REPROBED CURRENT CONNECTION");
  await expect(button(page, "Choose Semwright connection")).toBeEnabled();
});
test("an owner action from source A cannot publish or launch its follow-up probe after source B mounts", async ({ page }) => {
  await settle(page, 0, "status", "INITIAL SOURCE A");
  await button(page, "Choose Semwright connection").click(); await count(page, 2);
  await button(page, "Source B").click(); await count(page, 3);
  const before = await snapshot(page);
  expect(before.calls[2].request.project_id).toBe(before.projectB);
  await settle(page, 2, "status", "CURRENT SOURCE B");
  await expect(boundary(page)).toContainText("CURRENT SOURCE B");
  await settle(page, 1, "selection", true);
  // Baseline starts an obsolete A probe. Settle it to expose the actual stale
  // publication, then also assert that the fixed producer never starts it.
  const obsolete = (await snapshot(page)).calls.find(call => call.id > 2 &&
    call.command === "production_runtime_status" && call.request.project_id === before.projectA);
  if (obsolete) await settle(page, obsolete.id, "status", "OBSOLETE SOURCE A FOLLOW-UP");
  await expect(boundary(page)).toContainText("CURRENT SOURCE B");
  await expect(boundary(page)).not.toContainText("OBSOLETE SOURCE A FOLLOW-UP");
  await expect(note(page)).not.toContainText("Owner-selected runtime saved");
  expect(obsolete).toBeUndefined();
  await expect(button(page, "Choose Semwright connection")).toBeEnabled();

  await button(page, "Remove saved selection").click(); await count(page, 4);
  expect((await snapshot(page)).calls[3].request.project_id).toBe(before.projectB);
  await expect(boundary(page)).toContainText("CHECKING");
  await settle(page, 3, "selection", false); await count(page, 5);
  await settle(page, 4, "status", "CURRENT SOURCE B AFTER CLEAR");
  await expect(boundary(page)).toContainText("CURRENT SOURCE B AFTER CLEAR");
  await expect(note(page)).toContainText("No saved project connection was present");
});
