/**
 * Explicit development probe only; never selected by standard Studio tests,
 * or Graph R74 acceptance. The explicit development workflow selector alone
 * chooses this configuration; its results cannot grant release acceptance.
 */
import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const port = process.env.MOTIONWRIGHT_PLAYWRIGHT_PORT ?? "14389";
if (!/^[1-9][0-9]{3,4}$/.test(port) || Number(port) > 65535) {
  throw new Error("GRAPH_DEV_INVALID_PORT");
}
const studio = fileURLToPath(new URL("../", import.meta.url));
const vite = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const command = '"' + process.execPath + '" "' + vite
  + '" --host 127.0.0.1 --port ' + port + " --strictPort";

export default defineConfig({
  testDir: ".",
  testMatch: "graph_dev_first_save.spec.ts",
  timeout: 300_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  fullyParallel: false,
  outputDir: process.env.MOTIONWRIGHT_GRAPH_DEV_RESULTS_DIR
    ?? fileURLToPath(new URL("./.graph-dev-results/", import.meta.url)),
  use: {
    baseURL: "http://127.0.0.1:" + port,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command,
    cwd: studio,
    url: "http://127.0.0.1:" + port,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{
    name: "chromium",
    use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 940 } },
  }],
});
