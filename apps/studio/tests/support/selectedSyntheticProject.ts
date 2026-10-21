/** Explicit project selection for opt-in synthetic Tauri browser specs.
 *
 * This installs only a local selection preference and an explicit read adapter.
 * Each synthetic handler passes its own current Project and native SDK fixture
 * to that adapter for bootstrap, projection-head and full-project reads.
 * No global IPC interception, Project fallback, grant or native artifact is
 * supplied. Real StudioService tests use the ordinary Playwright test.
 */
import { test as playwrightTest, type Page } from "@playwright/test";
import { fixtureBootstrap } from "../../src/fixture";
import { installSyntheticProjectReads } from "./syntheticProjectReads";

export { expect } from "@playwright/test";
export type { Page, TestInfo } from "@playwright/test";

export const TEST_SELECTION_KEY = "motionwright.project-library.selection.v1";
export const TEST_SELECTION_SCHEMA = "motionwright.project-selection/1";

export async function seedSyntheticProjectSelection(page: Page, projectId: string): Promise<void> {
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(projectId)) {
    throw new Error("Synthetic project selection requires a UUID.");
  }
  await page.addInitScript(installSyntheticProjectReads);
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
