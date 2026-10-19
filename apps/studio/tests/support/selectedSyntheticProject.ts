/** Explicit project selection for opt-in *synthetic Tauri* browser specs.
 *
 * This is only a localStorage view preference installed before the mock loads.
 * It never supplies a Project, grant, native artifact, or production fallback.
 * Each synthetic "bootstrap" handler must independently validate the requested
 * UUID and return its matching selected project_selection envelope. Real
 * StudioService tests deliberately use the ordinary Playwright test instead.
 */
import { test as playwrightTest, type Page } from "@playwright/test";
import { fixtureBootstrap } from "../../src/fixture";

export { expect } from "@playwright/test";
export type { Page, TestInfo } from "@playwright/test";

export const TEST_SELECTION_KEY = "motionwright.project-library.selection.v1";
export const TEST_SELECTION_SCHEMA = "motionwright.project-selection/1";

export async function seedSyntheticProjectSelection(page: Page, projectId: string): Promise<void> {
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(projectId)) {
    throw new Error("Synthetic project selection requires a UUID.");
  }
  await page.addInitScript(({ id, key, schema }) => {
    window.localStorage.setItem(key, JSON.stringify({ schema, project_id: id }));
  }, { id: projectId, key: TEST_SELECTION_KEY, schema: TEST_SELECTION_SCHEMA });
}

export const test = playwrightTest.extend({
  page: async ({ page }, use) => {
    await seedSyntheticProjectSelection(page, fixtureBootstrap.project.id);
    await use(page);
  },
});
