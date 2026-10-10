import { expect, test } from "@playwright/test";

test("palette navigates workspaces without mutating the shared project", async ({ page }) => {
  await page.goto("/");
  const revision = await page.locator(".revision-chip").innerText();
  await page.getByRole("button", { name: "Open command palette" }).click();
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  const search = dialog.getByRole("combobox", { name: "Search editor commands" });
  await expect(search).toBeFocused();
  await search.fill("deliver");
  await expect(dialog.getByRole("option", { name: /Go to Deliver/ })).toBeVisible();
  await search.press("Enter");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Deliver", exact: true })).toHaveClass(/active/);
  await expect(page.locator(".revision-chip")).toHaveText(revision);
});

test("global shortcut works in focused fields, and Escape restores focus", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  const title = page.getByLabel("Project title");
  await title.focus();
  const initial = await title.inputValue();
  const revision = await page.locator(".revision-chip").innerText();
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(title).toBeFocused();
  await page.keyboard.type("x");
  await expect(title).toHaveValue(initial + "x");
  await expect(page.locator(".revision-chip")).toHaveText(revision);
});

test("palette consumes transport keys and executes exactly one frame-step action", async ({ page }) => {
  await page.goto("/");
  const clock = page.locator(".timeline-header .timecode");
  const original = await clock.innerText();
  const revision = await page.locator(".revision-chip").innerText();
  await page.keyboard.press("Control+Shift+p");
  const search = page.getByRole("combobox", { name: "Search editor commands" });
  await search.fill("step forward");
  await expect(page.getByRole("option", { name: /Step forward one frame/ })).toBeVisible();
  await search.press("ArrowRight");
  await expect(clock).toHaveText(original);
  await search.press("Enter");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(clock).not.toHaveText(original);
  await expect(page.locator(".revision-chip")).toHaveText(revision);
});

test("keyboard guide, zero results, focus trap, and reopen keep the palette usable", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "Command palette" });
  await dialog.getByRole("button", { name: "Keyboard shortcuts" }).click();
  await expect(dialog.getByLabel("Keyboard shortcut reference")).toContainText("Space");
  await dialog.getByRole("button", { name: "Back to commands" }).click();
  const search = dialog.getByRole("combobox", { name: "Search editor commands" });
  await expect(search).toBeFocused();
  await search.fill("not-a-real-workspace");
  await expect(dialog.getByText("No matching command")).toBeVisible();
  await search.press("Enter");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Control+k");
  await expect(dialog).toHaveCount(0);
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("combobox", { name: "Search editor commands" })).toHaveValue("");
  await page.keyboard.press("Escape");
});

test("palette view actions can toggle light theme without a content edit", async ({ page }) => {
  await page.goto("/");
  const revision = await page.locator(".revision-chip").innerText();
  const previous = await page.locator("html").getAttribute("data-theme");
  await page.keyboard.press("Control+k");
  const search = page.getByRole("combobox", { name: "Search editor commands" });
  await search.fill("theme");
  await search.press("Enter");
  await expect(page.locator("html")).toHaveAttribute("data-theme", previous === "dark" ? "light" : "dark");
  await expect(page.locator(".revision-chip")).toHaveText(revision);
});
