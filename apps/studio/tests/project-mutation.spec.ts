import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

// Synthetic IPC proves UI request coordination, not an installed native import.
for (const kind of ["asset", "voice"] as const) {
  test(`pending ${kind} import shares the ordinary edit latch and publishes its revision`, async ({ page }) => {
    const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
    await page.addInitScript(boot => {
      let project = structuredClone(boot.project);
      let resolveImport: ((value: typeof project) => void) | null = null;
      const state = {
        calls: [] as string[], revisions: [] as number[],
        complete: () => {
          project = { ...project, revision: project.revision + 1, title: "Synthetic import completed" };
          resolveImport?.(structuredClone(project));
        },
      };
      const host = window as unknown as {
        __MUTATION_FIXTURE__: typeof state;
        __TAURI_INTERNALS__: { invoke: (command: string, args?: { request?: { revision?: number } }) => Promise<unknown> };
      };
      host.__MUTATION_FIXTURE__ = state;
      host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
        state.calls.push(command);
        if (command === "bootstrap") return structuredClone(boot);
        if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
        if (command === "import_asset_file" || command === "import_voice_file") {
          return new Promise(resolve => { resolveImport = resolve; });
        }
        if (command === "apply_change") {
          state.revisions.push(args?.request?.revision ?? -1);
          project = { ...project, revision: project.revision + 1, title: "Synthetic subsequent edit" };
          return structuredClone(project);
        }
        throw new Error("Unimplemented synthetic IPC: " + command);
      } };
    }, initial);
    await page.goto("/");
    await expect(page.locator(".revision-chip")).toHaveText("r12");
    if (kind === "asset") {
      await page.getByRole("button", { name: "Import local asset", exact: true }).click();
      await page.getByLabel("Local asset path").fill("/synthetic-fixture/asset.png");
      await page.getByRole("button", { name: "Import", exact: true }).click();
    } else {
      await page.getByRole("button", { name: "Audio", exact: true }).click();
      await page.getByLabel("Voice file absolute path").fill("/synthetic-fixture/voice.wav");
      await page.getByRole("button", { name: "Import + measure", exact: true }).click();
    }
    await expect.poll(() => page.evaluate((importKind) => (window as unknown as {
      __MUTATION_FIXTURE__: { calls: string[] };
    }).__MUTATION_FIXTURE__.calls.includes("import_" + importKind + "_file"), kind)).toBe(true);
    await expect(page.locator(".saving-state")).toContainText("Committing");
    await page.getByRole("button", { name: "Brief", exact: true }).click();
    await page.getByLabel("Project title").fill("Do not race an import");
    await page.getByRole("button", { name: "Save title", exact: true }).click();
    await expect(page.locator(".error-banner")).toContainText("Finish the current project change or import");
    expect(await page.evaluate(() => (window as unknown as {
      __MUTATION_FIXTURE__: { calls: string[] };
    }).__MUTATION_FIXTURE__.calls.filter(command => command === "apply_change").length)).toBe(0);
    await page.evaluate(() => (window as unknown as {
      __MUTATION_FIXTURE__: { complete: () => void };
    }).__MUTATION_FIXTURE__.complete());
    await expect(page.locator(".revision-chip")).toHaveText("r13");
    await expect(page.locator(".brand-copy")).toContainText("Synthetic import completed");
    await expect(page.locator(".saving-state")).toHaveCount(0);
    await page.getByLabel("Project title").fill("Edit after import");
    await page.getByRole("button", { name: "Save title", exact: true }).click();
    await expect(page.locator(".revision-chip")).toHaveText("r14");
    expect(await page.evaluate(() => (window as unknown as {
      __MUTATION_FIXTURE__: { revisions: number[] };
    }).__MUTATION_FIXTURE__.revisions)).toEqual([13]);
  });
}
