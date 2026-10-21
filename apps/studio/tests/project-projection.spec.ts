import { expect, test, type Page } from "@playwright/test";
import type { ProjectionFixtureHarness } from "./fixtures/project-projection";

const fixture = "/tests/fixtures/project-projection.html";
const trace = (page: Page) => page.evaluate(() => window.__PROJECTION_FIXTURE__.requests());
const fullReads = (requests: Awaited<ReturnType<typeof trace>>) => requests.filter(entry =>
  entry.command === "open_project" || entry.command === "get_project"
  || (entry.command === "bootstrap" && (entry.args.request as { project_id: string | null })?.project_id !== null));

test("causal: remembered large project obtains shell and pages without a complete read", async ({ page }) => {
  await page.goto(fixture + "?initial=large&exact=1");
  await expect.poll(async () => (await trace(page)).length).toBeGreaterThan(0);
  await expect.poll(async () => (await trace(page)).map(item => item.command)).toContain("project_scene_page");
  const requests = await trace(page);
  expect(fullReads(requests)).toEqual([]);
  expect(requests.slice(0, 3).map(item => item.command)).toEqual(["bootstrap", "project_projection_head", "project_scene_page"]);
  expect(requests[0].args).toEqual({ request: { project_id: null } });
  await expect(page.getByRole("heading", { name: "Large saved project", exact: true })).toBeVisible();
  await expect(page.getByText("Revision 18446744073709551615", { exact: true })).toBeVisible();
});

test("causal: a small auto open observes its head before keeping the complete editor", async ({ page }) => {
  await page.goto(fixture + "?initial=small");
  await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
  const requests = await trace(page);
  expect(requests.slice(0, 3).map(item => item.command)).toEqual(["bootstrap", "project_projection_head", "open_project"]);
  expect(requests[0].args).toEqual({ request: { project_id: null } });
  expect(fullReads(requests).map(item => item.command)).toEqual(["open_project"]);
});

test("causal: failed head admission preserves the prior editor and remembered identity without fallback", async ({ page }) => {
  await page.goto(fixture + "?initial=small");
  await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
  const before = await page.evaluate(() => ({
    selection: localStorage.getItem("motionwright.project-library.selection.v1"),
    count: window.__PROJECTION_FIXTURE__.requests().length,
  }));
  await page.evaluate(() => window.__PROJECTION_FIXTURE__.failure("project_projection_head", { code: "unavailable" }));
  await page.getByRole("button", { name: "Open project library", exact: true }).click();
  await page.getByRole("button", { name: "Open Large saved project", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Couldn’t open" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("motionwright.project-library.selection.v1"))).toBe(before.selection);
  expect(fullReads((await trace(page)).slice(before.count))).toEqual([]);
});

test("causal: the library offers explicit saved-page browsing for a small project", async ({ page }) => {
  await page.goto(fixture + "?initial=none");
  await expect(page.getByRole("dialog")).toBeVisible();
  const before = (await trace(page)).length;
  await page.getByRole("button", { name: "Browse pages of Small saved project", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Small saved project", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Open editor", exact: true })).toBeVisible();
  expect(fullReads((await trace(page)).slice(before))).toEqual([]);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("motionwright.project-library.selection.v1")!).project_id))
    .toBe(await page.evaluate(() => window.__PROJECTION_FIXTURE__.ids.small));
});

test("control: the browser demo remains explicitly identified without native projection calls", async ({ page }) => {
  await page.goto(fixture + "?demo=1");
  await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
  await expect(page.getByText("Native SDK · browser demo", { exact: true })).toBeVisible();
  expect(await trace(page)).toEqual([]);
});


test("scene navigation replaces each rendered page without prefetching the remaining project", async ({ page }) => {
  await page.goto(fixture + "?initial=large");
  const scenes = page.getByRole("list", { name: "Loaded scene summaries" });
  await expect(scenes.getByRole("listitem")).toHaveCount(64);
  expect((await trace(page)).filter(item => item.command === "project_scene_page")).toHaveLength(1);
  await page.getByRole("button", { name: "Next scenes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Scenes 65–128 of 130", exact: true })).toBeVisible();
  await expect(scenes.getByRole("listitem")).toHaveCount(64);
  await expect(page.getByRole("button", { name: "View nodes of Saved scene 1", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Next scenes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Scenes 129–130 of 130", exact: true })).toBeVisible();
  await expect(scenes.getByRole("listitem")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Next scenes", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Previous scenes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Scenes 65–128 of 130", exact: true })).toBeVisible();
  expect((await trace(page)).filter(item => item.command === "project_scene_page")).toHaveLength(4);
  expect(fullReads(await trace(page))).toEqual([]);
});

test("node navigation is bounded and off-page references remain explicit through page replacement", async ({ page }) => {
  await page.goto(fixture + "?initial=large");
  await page.getByRole("button", { name: "View nodes of Saved scene 1", exact: true }).click();
  const nodes = page.getByRole("list", { name: "Loaded saved nodes" });
  await expect(nodes.getByRole("listitem")).toHaveCount(128);
  await expect(nodes.getByRole("listitem").first()).toContainText("Parent: f1000000-0000-4000-8000-0000000027d8 — not loaded on this page");
  await expect(nodes.getByRole("listitem").first()).toContainText("follow: f1000000-0000-4000-8000-0000000027d9 — not loaded on this page");
  await page.getByRole("button", { name: "Next nodes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Nodes 129–256 of 260", exact: true })).toBeVisible();
  await expect(nodes.getByRole("listitem")).toHaveCount(128);
  await page.getByRole("button", { name: "Next nodes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Nodes 257–260 of 260", exact: true })).toBeVisible();
  await expect(nodes.getByRole("listitem")).toHaveCount(4);
  await page.getByRole("button", { name: "Previous nodes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Nodes 129–256 of 260", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "View nodes of Saved scene 2", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Nodes 1–3 of 3", exact: true })).toBeVisible();
  await expect(nodes.getByRole("listitem")).toHaveCount(3);
  expect(fullReads(await trace(page))).toEqual([]);
});

for (const generation of [false, true]) {
  test("a changed " + (generation ? "generation" : "revision") + " preserves the admitted page until an explicit reload", async ({ page }) => {
    await page.goto(fixture + "?initial=large");
    await expect(page.getByRole("heading", { name: "Scenes 1–64 of 130", exact: true })).toBeVisible();
    const before = await page.evaluate(() => localStorage.getItem("motionwright.project-library.selection.v1"));
    await page.evaluate(generation => window.__PROJECTION_FIXTURE__.advance("large", generation), generation);
    await page.getByRole("button", { name: "Next scenes", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText(generation ? "restored or replaced" : "saved project changed");
    await expect(page.getByRole("heading", { name: "Scenes 1–64 of 130", exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("motionwright.project-library.selection.v1"))).toBe(before);
    expect((await trace(page)).filter(item => item.command === "project_scene_page")).toHaveLength(2);
    await page.getByRole("button", { name: "Reload saved pages", exact: true }).click();
    await expect(page.getByText("Revision 13", { exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await page.getByRole("button", { name: "Next scenes", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Scenes 65–128 of 130", exact: true })).toBeVisible();
    expect(fullReads(await trace(page))).toEqual([]);
  });
}

test("an unknown page error is safe and never causes an automatic retry or complete load", async ({ page }) => {
  await page.goto(fixture + "?initial=large");
  await expect(page.getByRole("heading", { name: "Scenes 1–64 of 130", exact: true })).toBeVisible();
  await page.evaluate(() => window.__PROJECTION_FIXTURE__.failure("project_scene_page", { code: "unavailable", detail: "DO_NOT_DISPLAY" }));
  await page.getByRole("button", { name: "Next scenes", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("could not be verified");
  await expect(page.locator("body")).not.toContainText("DO_NOT_DISPLAY");
  await expect(page.getByRole("heading", { name: "Scenes 1–64 of 130", exact: true })).toBeVisible();
  expect((await trace(page)).filter(item => item.command === "project_scene_page")).toHaveLength(2);
  expect(fullReads(await trace(page))).toEqual([]);
  await page.evaluate(() => window.__PROJECTION_FIXTURE__.failure("project_scene_page", null));
  await page.getByRole("button", { name: "Next scenes", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Scenes 65–128 of 130", exact: true })).toBeVisible();
  expect((await trace(page)).filter(item => item.command === "project_scene_page")).toHaveLength(3);
});

test("Open editor is a separate explicit complete read after bounded navigation", async ({ page }) => {
  await page.goto(fixture + "?initial=large");
  await expect(page.getByRole("heading", { name: "Large saved project", exact: true })).toBeVisible();
  expect(fullReads(await trace(page))).toEqual([]);
  const before = (await trace(page)).length;
  await page.getByRole("button", { name: "Open editor", exact: true }).click();
  await expect(page.getByRole("navigation", { name: "Workspaces" })).toBeVisible();
  expect((await trace(page)).slice(before).map(item => item.command)).toEqual(["bootstrap", "project_projection_head", "open_project"]);
});

test("unsubmitted Review text survives a full editor to saved-pages to explicit editor round trip", async ({ page }) => {
  await page.goto(fixture + "?initial=small");
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.getByLabel("Review comment", { exact: true }).fill("  Authored review\n  still incomplete  ");
  const before = await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.review-recovery.v1:"))
    .map(key => ({ key, raw: localStorage.getItem(key) })));
  expect(before.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Open project library", exact: true }).click();
  await page.getByRole("button", { name: "Browse pages of Small saved project", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Small saved project", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Open editor", exact: true }).click();
  await page.getByRole("button", { name: "Review", exact: true }).click();
  await expect(page.getByLabel("Review comment", { exact: true })).toHaveValue("  Authored review\n  still incomplete  ");
  expect((await trace(page)).filter(item => item.command === "apply_change")).toEqual([]);
  for (const copy of before) expect(await page.evaluate(key => localStorage.getItem(key), copy.key)).not.toBeNull();
});
