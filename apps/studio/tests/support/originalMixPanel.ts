import type { Page } from "@playwright/test";

export async function openOriginalMixPanel(page: Page) {
  const panel = page.getByRole("region", { name: "Original three-bus native PCM audition", exact: true });
  const toggle = panel.getByRole("button", { name: /original source mix/ });
  if (await toggle.getAttribute("aria-expanded") !== "true") await toggle.click();
  return panel;
}
