/** Browser interaction checks with an explicitly synthetic IPC transport. */
import { expect, test } from "@playwright/test";
type Fixture = { records(): { command: string; request: Record<string, unknown> }[];
  holdGrant(): void; releaseGrant(): void; revision(value: number): void };
declare global { interface Window { __hyperframesFixture: Fixture; } }
test("exports both inspected native formats with one exact grant per file", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-source.html");
  const panel = page.getByRole("region", { name: "HyperFrames editable source" });
  await panel.getByRole("button", { name: "Inspect saved HyperFrames source" }).click();
  await expect(panel.getByText("r4", { exact: true })).toBeVisible();
  await panel.getByLabel("Full destination path ending in index.html", { exact: true }).fill("E:\\Exports\\index.html");
  await panel.getByRole("button", { name: "Export native HTML", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("Saved E:\\Exports\\index.html from revision r4 (600 bytes).");
  await panel.getByRole("combobox", { name: "File format", exact: true }).selectOption("json");
  await expect(panel.getByLabel("Full destination path ending in semwright-hyperframes.json", { exact: true }))
    .toHaveValue("E:\\Exports\\semwright-hyperframes.json");
  await panel.getByRole("button", { name: "Export editable JSON", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("Saved E:\\Exports\\semwright-hyperframes.json from revision r4 (300 bytes).");
  const calls = await page.evaluate(() => window.__hyperframesFixture.records());
  expect(calls.map(call => call.command)).toEqual(["native_hyperframes_source",
    "issue_effect_grant", "export_hyperframes_source_file", "issue_effect_grant", "export_hyperframes_source_file"]);
  expect(calls[1].request.subject).toContain("|html|E:\\Exports\\index.html");
  expect(calls[3].request.subject).toContain("|json|E:\\Exports\\semwright-hyperframes.json");
});
test("a retired grant cannot export after a revision changes and returns to the original value", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-source.html");
  const panel = page.getByRole("region", { name: "HyperFrames editable source" });
  await panel.getByRole("button", { name: "Inspect saved HyperFrames source" }).click();
  await panel.getByLabel("Full destination path ending in index.html", { exact: true }).fill("E:\\Exports\\index.html");
  await page.evaluate(() => window.__hyperframesFixture.holdGrant());
  await panel.getByRole("button", { name: "Export native HTML", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("Exporting");
  await page.evaluate(() => window.__hyperframesFixture.revision(5));
  await page.evaluate(() => window.__hyperframesFixture.revision(4));
  await panel.getByRole("button", { name: "Inspect saved HyperFrames source" }).click();
  await expect(panel.getByText("r4", { exact: true })).toBeVisible();
  await page.evaluate(() => window.__hyperframesFixture.releaseGrant());
  await expect.poll(async () => (await page.evaluate(() => window.__hyperframesFixture.records()))
    .filter(call => call.command === "export_hyperframes_source_file").length).toBe(0);
  await panel.getByRole("button", { name: "Export native HTML", exact: true }).click();
  await expect(panel.getByRole("status")).toContainText("Saved E:\\Exports\\index.html");
  const calls = await page.evaluate(() => window.__hyperframesFixture.records());
  expect(calls.filter(call => call.command === "issue_effect_grant")).toHaveLength(2);
  expect(calls.filter(call => call.command === "export_hyperframes_source_file")).toHaveLength(1);
});
