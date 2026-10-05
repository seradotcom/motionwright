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
  await expect(page.getByRole("button", { name: /Pixels are brittle/ })).toBeVisible();

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

  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  await expect(page.getByRole("region", { name: "Semantic canvas" })).toBeVisible();
  await page.getByRole("button", { name: "Reasoning headline", exact: true }).click();

  const x = page.getByLabel("Canvas X");
  await expect(x).toHaveValue("160");
  await x.fill("196");
  await page.getByRole("button", { name: "Commit transform" }).click();
  await expect(page.getByText("r13", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Storyboard", exact: true }).click();
  await page.getByRole("button", { name: /Pixels are brittle/ }).click();
  await page.getByRole("button", { name: "Alternatives", exact: true }).click();
  await expect(page.getByText("Candidate budget")).toBeVisible();
  await expect(page.getByText("Contrast cut", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Select for review" }).nth(1).click();
  await expect(page.getByText("Selected", { exact: true })).toBeVisible();
  await expect(page.getByText("r14", { exact: true }).first()).toBeVisible();
});
