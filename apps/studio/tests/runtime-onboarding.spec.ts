import { expect, test } from "@playwright/test";

test("browser demo cannot silently grant or configure a native Semwright runtime", async ({ page }) => {
  await page.goto("/");
  const revision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Integrations", exact: true }).click();
  const runtimeSection = page.getByRole("region", { name: "Runtime boundaries" });
  const setup = page.getByLabel("Configure Semwright runtime");

  await expect(runtimeSection.getByText("Semwright production runtime", { exact: true })).toBeVisible();
  await expect(runtimeSection.getByText("BROWSER DEMO").last()).toBeVisible();
  await expect(setup.getByText("Connect an existing Semwright runtime")).toBeVisible();
  await expect(setup.getByRole("button", { name: "Choose Semwright connection" })).toBeDisabled();
  await expect(setup.getByRole("button", { name: "Remove saved selection" })).toBeDisabled();
  await expect(setup.getByRole("status")).toContainText(
    "available only in the installed desktop app",
  );
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.reload();
  await page.getByRole("button", { name: "Integrations", exact: true }).click();
  await expect(page.getByLabel("Configure Semwright runtime").getByRole(
    "button", { name: "Choose Semwright connection" },
  )).toBeDisabled();
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});
