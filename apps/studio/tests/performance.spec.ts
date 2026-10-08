import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";

type UiSelectionReport = {
  schema: "motionwright.ui-selection.v1";
  source_sha: string;
  dataset: {
    scenes: number;
    samples: number;
    surface: string;
  };
  budget_ms: number;
  p95_ms: number;
  max_ms: number;
  samples_ms: number[];
};

function p95(samples: number[]) {
  const sorted = [...samples].sort((a, b) => a - b);
  const rank = Math.max(1, Math.ceil(sorted.length * 0.95));
  return sorted[rank - 1];
}

test("S-profile scene selection updates the inspector within the interactive budget", async ({ page }, testInfo) => {
  test.skip(process.env.MOTIONWRIGHT_PERF_ACCEPTANCE !== "1", "dedicated performance evidence lane");

  await page.goto("/");
  await expect(page.locator(".tree-row")).toHaveCount(4);

  for (let index = 5; index <= 12; index += 1) {
    await page.getByRole("button", { name: "Add scene" }).click();
    await page
      .getByLabel("New scene name")
      .fill("Performance scene " + String(index).padStart(2, "0"));
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(page.locator(".tree-row")).toHaveCount(index);
  }

  const measurement = await page.evaluate(async () => {
    const rows = Array.from(document.querySelectorAll<HTMLButtonElement>(".tree-row"));
    if (rows.length !== 12) throw new Error("expected 12 scenes, got " + rows.length);

    const waitForInspector = (expected: string, started: number) =>
      new Promise<number>((resolve, reject) => {
        let frames = 0;
        const check = () => {
          const value = document
            .querySelector<HTMLElement>(".inspector .read-field")
            ?.textContent?.trim();
          if (value === expected) {
            requestAnimationFrame(() => resolve(performance.now() - started));
            return;
          }
          frames += 1;
          if (frames > 120) {
            reject(new Error("inspector did not converge to " + expected));
            return;
          }
          requestAnimationFrame(check);
        };
        requestAnimationFrame(check);
      });

    for (let index = 0; index < 6; index += 1) {
      const row = rows[(index + 1) % rows.length];
      const expected = row.querySelector<HTMLElement>(".tree-name")?.textContent?.trim();
      if (!expected) throw new Error("scene row is missing a name");
      const started = performance.now();
      row.click();
      await waitForInspector(expected, started);
    }

    const samples: number[] = [];
    for (let index = 0; index < 60; index += 1) {
      const row = rows[(index + 3) % rows.length];
      const expected = row.querySelector<HTMLElement>(".tree-name")?.textContent?.trim();
      if (!expected) throw new Error("scene row is missing a name");
      const started = performance.now();
      row.click();
      samples.push(await waitForInspector(expected, started));
    }
    return samples;
  });

  const report: UiSelectionReport = {
    schema: "motionwright.ui-selection.v1",
    source_sha: process.env.GITHUB_SHA ?? "local-unattributed",
    dataset: {
      scenes: 12,
      samples: measurement.length,
      surface: "project rail scene selection -> inspector scene name -> next animation frame",
    },
    budget_ms: 100,
    p95_ms: p95(measurement),
    max_ms: Math.max(...measurement),
    samples_ms: measurement,
  };

  const reportPath = process.env.MOTIONWRIGHT_UI_PERF_REPORT;
  if (reportPath) {
    mkdirSync(dirname(reportPath), { recursive: true });
    writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n", "utf8");
  }
  await testInfo.attach("ui-selection-evidence", {
    body: Buffer.from(JSON.stringify(report, null, 2)),
    contentType: "application/json",
  });

  expect(report.dataset.samples).toBe(60);
  expect(report.p95_ms).toBeLessThan(report.budget_ms);
});
