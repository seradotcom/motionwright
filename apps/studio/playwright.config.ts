import { defineConfig, devices } from "@playwright/test";

// Isolated worktrees can run browser acceptance without attaching to another
// checkout's localhost:1420 server. CI preserves the original default.
const port = process.env.MOTIONWRIGHT_PLAYWRIGHT_PORT ?? "1420";
if (!/^[1-9][0-9]{3,4}$/.test(port) || Number(port) > 65535) {
  throw new Error("Invalid bounded local Playwright port.");
}
const baseURL = "http://127.0.0.1:" + port;

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port " + port + " --strictPort",
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 940 } } },
  ],
});
