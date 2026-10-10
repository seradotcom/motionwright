import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("Dependencies audits real CAS identities in pages without inventing scene consumers", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  // More than one bounded 8-asset page.
  for (let i = 0; i < 7; i++) {
    boot.project.assets.push({
      id: "019d0000-0000-7000-8000-" + (i + 100).toString().padStart(12, "0"),
      name: "imported-extra-" + i + ".png",
      media_type: "image/png",
      content_sha256: (i + 1).toString(16).repeat(64),
      source_revision: "fixture",
    });
  }
  boot.project.audio.voice_tracks.push({
    id: "019d0000-0000-7000-8000-000000000987",
    asset_id: boot.project.assets[0].id,
    label: "Recorded narrator",
    sample_rate_hz: 48000,
    channels: 2,
    measured_duration: { num: "1", den: "1" },
    source_sha256: boot.project.assets[0].content_sha256!,
    loudness_lufs: null,
    true_peak_dbfs: null,
  });
  await page.addInitScript((initial) => {
    const app = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __ASSET_INSPECTIONS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initial.project);
    app.__ASSET_INSPECTIONS__ = [];
    app.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      if (command === "bootstrap") return { ...initial, project: structuredClone(project) };
      if (command === "asset_integrity_page") {
        const request = args?.request as {
          project_id: string; generation: string; revision: number;
          offset: number | null; limit: number;
        };
        app.__ASSET_INSPECTIONS__.push(request as unknown as Record<string, unknown>);
        if (request.project_id !== project.id
          || request.generation !== project.generation
          || request.revision !== project.revision) {
          throw new Error("Project revision changed during asset verification");
        }
        const offset = request.offset ?? 0;
        const end = Math.min(project.assets.length, offset + request.limit);
        return {
          project_id: request.project_id,
          generation: request.generation,
          revision: request.revision,
          total_assets: project.assets.length,
          items: project.assets.slice(offset, end).map((asset, i) => ({
            asset_id: asset.id,
            status: offset + i === 1 ? "corrupt"
              : offset + i === 2 ? "missing"
                : "verified",
            size_bytes: 2048,
          })),
          checked_bytes: (end - offset) * 2048,
          next: end < project.assets.length ? end : null,
          complete: end === project.assets.length,
        };
      }
      if (command === "apply_change") {
        const request = args?.request as {
          change: { type: string; title: string };
        };
        if (request.change.type !== "rename_project") {
          throw new Error("Unsupported synthetic creative change: " + request.change.type);
        }
        project = { ...project, title: request.change.title, revision: project.revision + 1 };
        return structuredClone(project);
      }
      throw new Error("Unexpected synthetic operation: " + command);
    } };
  }, boot);

  await page.goto("/");
  await page.getByRole("button", { name: "Dependencies", exact: true }).click();
  const table = page.getByRole("table", { name: "Project asset dependencies and integrity" });
  const audit = page.getByRole("region", { name: "Local asset SHA-256 integrity" });
  await expect(audit).toContainText("Not checked");
  await expect(table.getByRole("row")).toHaveCount(11);
  await expect(table.getByRole("row", { name: "Asset voiceover.wav" })).toContainText("Voice: Recorded narrator");
  await expect(table.getByRole("row", { name: "Asset native-demo.glb" })).toContainText("Not established (Graph needed)");
  await expect(table).not.toContainText("Scene One");
  const initialRevision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Check local assets" }).click();
  await expect(audit).toContainText("8 / 10 assets inspected");
  await expect(table.getByRole("row", { name: "Asset semwright-mark.svg" })).toContainText("SHA-256 mismatch");
  await expect(table.getByRole("row", { name: "Asset native-demo.glb" })).toContainText("Missing local blob");
  await expect(page.getByRole("button", { name: "Check next local asset page" })).toBeEnabled();
  await page.getByRole("button", { name: "Check next local asset page" }).click();
  await expect(audit).toContainText("10 / 10 assets inspected");
  await expect(audit).toContainText("all references examined");
  await expect(table.getByRole("row", { name: "Asset imported-extra-6.png" })).toContainText("SHA-256 verified");
  await expect(page.locator(".revision-chip").first()).toHaveText(initialRevision);

  const requests = await page.evaluate(() => (
    window as unknown as { __ASSET_INSPECTIONS__: Array<Record<string, unknown>> }
  ).__ASSET_INSPECTIONS__);
  expect(requests).toHaveLength(2);
  expect(requests.map((request) => request.offset)).toEqual([null, 8]);
  expect(requests.every((request) => request.project_id === boot.project.id
    && request.generation === boot.project.generation
    && request.revision === boot.project.revision
    && request.limit === 8
    && !("path" in request) && !("digest" in request) && !("effect_grant" in request)
  )).toBe(true);

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByLabel("Project title").fill("Changed after integrity readback");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Dependencies", exact: true }).click();
  await expect(audit).toContainText("Not checked");
  await expect(table.getByRole("row", { name: "Asset voiceover.wav" })).toContainText("Not checked");
});

test("browser demo cannot claim local CAS integrity or fabricate Graph associations", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Dependencies", exact: true }).click();
  const table = page.getByRole("table", { name: "Project asset dependencies and integrity" });
  const audit = page.getByRole("region", { name: "Local asset SHA-256 integrity" });
  await expect(page.getByRole("button", { name: "Check local assets" })).toBeDisabled();
  await expect(audit).toContainText("unavailable in browser demo mode");
  await expect(table).not.toContainText("SHA-256 verified");
  await expect(table).toContainText("Not established (Graph needed)");
  await expect(page.getByText("GRAPH · UNKNOWN")).toBeVisible();
});
