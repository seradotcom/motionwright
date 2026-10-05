import { expect, test } from "@playwright/test";

test("editor exposes real workspaces and browser-demo mutations", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Motionwright", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Timeline", exact: true })).toHaveAttribute("class", /active/);
  await expect(page.getByText("Reasoning is solved", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  const title = page.getByLabel("Project title");
  await title.fill("Semwright Native Film");
  await page.getByRole("button", { name: "Save title" }).click();
  await expect(page.getByText("Semwright Native Film", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("r13", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Storyboard", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Storyboard" })).toBeVisible();
  await expect(page.locator(".story-card").filter({ hasText: "Pixels are brittle" })).toBeVisible();

  await page.screenshot({ path: "test-results/motionwright-editor.png", fullPage: true });
});

test("renderer and locks mutate the same visible project revision", async ({ page }) => {
  await page.goto("/");
  const renderer = page.getByLabel("Renderer");
  await expect(renderer).toHaveValue("motion-canvas");
  await renderer.selectOption("blender");
  await expect(page.getByText("r13", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Content", exact: true }).click();
  await expect(page.getByText("content", { exact: true })).toBeVisible();
  await expect(page.getByText("r14", { exact: true }).first()).toBeVisible();
});

test("keyboard focus is visible and workspace navigation remains operable", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  const active = page.locator(":focus");
  await expect(active).toBeVisible();

  await page.getByRole("button", { name: "Dependencies", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Dependencies" })).toBeVisible();
  await expect(page.getByText("NOT ADMITTED").first()).toBeVisible();
});


test("canvas edits and stored alternatives use project revisions rather than local-only UI state", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Storyboard", exact: true }).click();
  await page.locator(".story-card").filter({ hasText: "Pixels are brittle" }).click();
  await page.getByRole("button", { name: "Alternatives", exact: true }).click();
  await expect(page.getByText("Candidate budget")).toBeVisible();
  await expect(page.getByText("Contrast cut", { exact: true })).toBeVisible();

  const beforeSelection = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Select for review" }).nth(1).click();
  await expect(page.getByRole("button", { name: "Selected", exact: true })).toBeVisible();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(beforeSelection);

  const beforeTransform = await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  await expect(page.getByRole("region", { name: "Semantic canvas" })).toBeVisible();

  const x = page.getByLabel("Canvas X");
  await x.fill("796");
  await page.getByRole("button", { name: "Commit transform" }).click();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(beforeTransform);

  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await expect(page.getByRole("table", { name: "Project event journal" })).toBeVisible();
  await expect(page.getByText("select proposal", { exact: true })).toBeVisible();
  await expect(page.getByText("transform canvas node", { exact: true })).toBeVisible();
});

test("scene duration ripple reflows the shared timeline and journals one change", async ({ page }) => {
  await page.goto("/");

  await page.locator(".tree-row").filter({ hasText: "Pixels are brittle" }).click();

  const secondClip = page.locator(".timeline-clip").nth(1);
  const before = await secondClip.getAttribute("style");

  const duration = page.getByLabel("Scene duration seconds");
  await expect(duration).toBeEnabled();
  await duration.fill("8");
  await page.getByRole("button", { name: "Ripple", exact: true }).click();

  await expect.poll(async () => secondClip.getAttribute("style")).not.toBe(before);

  await page.getByRole("button", { name: "Changes", exact: true }).click();
  await expect(page.getByText("set scene duration", { exact: true })).toBeVisible();
});

test("visual system commits a new version and project style lock is enforceable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();

  const systemName = page.getByLabel("Visual system name");
  await expect(systemName).toHaveValue("Cut Room Ledger");
  await systemName.fill("Cut Room Ledger Review");
  await page.getByRole("button", { name: "Commit visual system", exact: true }).click();

  await expect(page.getByText("v2", { exact: true })).toBeVisible();
  await expect(systemName).toHaveValue("Cut Room Ledger Review");

  await page.getByRole("button", { name: "Protect style", exact: true }).click();
  await expect(page.getByRole("button", { name: "Unlock style", exact: true })).toBeVisible();
  await expect(systemName).toBeDisabled();

  await page.getByRole("button", { name: "Unlock style", exact: true }).click();
  await expect(page.getByRole("button", { name: "Protect style", exact: true })).toBeVisible();
  await expect(systemName).toBeEnabled();
});

test("canvas hierarchy style relations and safe removal are versioned", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();

  await page.getByRole("button", { name: "group", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Group 1" })).toBeVisible();

  await page.getByRole("button", { name: "shape", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Shape 2" })).toBeVisible();

  await page.getByLabel("Canvas parent").selectOption({ label: "Group 1" });
  await page.getByLabel("Canvas z order").fill("7");
  await page.getByRole("button", { name: "Commit hierarchy", exact: true }).click();

  await page.getByLabel("Canvas fill").fill("#38424d");
  await page.getByRole("button", { name: "Commit style", exact: true }).click();

  await page.getByLabel("Canvas relation target").selectOption({ label: "Group 1" });
  await page.getByRole("button", { name: "Add canvas relation", exact: true }).click();
  await expect(page.locator(".relation-row").filter({ hasText: "Group 1" })).toBeVisible();

  await page.locator(".canvas-tree-row").filter({ hasText: "Group 1" }).click();
  await page.getByRole("button", { name: "Remove object", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("children");

  await page.locator(".canvas-tree-row").filter({ hasText: "Shape 2" }).click();
  await page.getByRole("button", { name: "Remove object", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Shape 2" })).toHaveCount(0);

  await page.locator(".canvas-tree-row").filter({ hasText: "Group 1" }).click();
  await page.getByRole("button", { name: "Remove object", exact: true }).click();
  await expect(page.locator(".canvas-tree-row").filter({ hasText: "Group 1" })).toHaveCount(0);
});

test("portable project delivery is truthful in browser demo mode", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Import local asset" })).toBeDisabled();
  await expect(page.getByText("Local asset import is available in the desktop runtime.")).toBeVisible();

  await page.getByRole("button", { name: "Deliver", exact: true }).click();

  await expect(page.getByRole("region", { name: "Portable project" })).toBeVisible();
  await expect(page.getByLabel("Portable export path")).toBeDisabled();
  await expect(page.getByLabel("Portable import path")).toBeDisabled();
  await expect(page.getByRole("button", { name: "Export bundle", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Inspect bundle", exact: true })).toBeDisabled();
  await expect(page.getByText(/Desktop filesystem capability is required/)).toBeVisible();
});
