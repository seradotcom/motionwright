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
    await expect(page.locator(".sdk-chip")).toContainText("desktop mode");
    await expect(page.locator(".sdk-chip")).not.toContainText("connected");
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

test("a pending portable restore cannot race source edits or publish into another revision", async ({ page }) => {
  const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
  await page.addInitScript(boot => {
    let current = structuredClone(boot.project);
    let resolveRestore: ((project: typeof current) => void) | null = null;
    const state = {
      calls: [] as string[], revisions: [] as number[],
      complete: () => {
        current = { ...current, id: "01900000-0000-7000-8000-000000000021",
          generation: "01900000-0000-7000-8000-000000000022", revision: 0,
          title: "Portable restored project" };
        resolveRestore?.(structuredClone(current));
      },
    };
    const host = window as unknown as {
      __RESTORE_FIXTURE__: typeof state;
      __TAURI_INTERNALS__: { invoke: (command: string, args?: {request?: {revision?: number}}) => Promise<unknown> };
    };
    host.__RESTORE_FIXTURE__ = state;
    host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      state.calls.push(command);
      if (command === "bootstrap") return structuredClone(boot);
      if (command === "inspect_project_bundle") return {
        project_id: "01900000-0000-7000-8000-000000000021",
        title: "Portable restored project", source_generation: "old-import-generation",
        revision: "9", event_count: 9, blob_count: 1,
        total_blob_bytes: "1024", rotates_generation: true,
      };
      if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
      if (command === "import_project_bundle") {
        return new Promise(resolve => { resolveRestore = resolve; });
      }
      if (command === "apply_change") {
        state.revisions.push(args?.request?.revision ?? -1);
        current = { ...current, revision: current.revision + 1, title: "After portable import" };
        return structuredClone(current);
      }
      throw new Error("Unsupported synthetic command: " + command);
    } };
  }, initial);

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel("Portable import path").fill("/synthetic-fixture/portable.motionwright");
  await page.getByRole("button", { name: "Inspect bundle", exact: true }).click();
  await expect(page.getByLabel("Portable bundle preflight")).toContainText("Portable restored project");
  await page.getByRole("button", { name: "Import verified bundle", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {calls:string[]};
  }).__RESTORE_FIXTURE__.calls.includes("import_project_bundle"))).toBe(true);
  await expect(page.locator(".saving-state")).toContainText("Committing");

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByLabel("Project title").fill("Do not race portable restore");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".error-banner")).toContainText("Finish the current project change or import");
  expect(await page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {calls:string[]};
  }).__RESTORE_FIXTURE__.calls.filter(s => s === "apply_change").length)).toBe(0);

  await page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {complete:()=>void};
  }).__RESTORE_FIXTURE__.complete());
  await expect(page.locator(".revision-chip")).toHaveText("r0");
  await expect(page.locator(".brand-copy")).toContainText("Portable restored project");
  await expect(page.locator(".saving-state")).toHaveCount(0);
  await page.getByLabel("Project title").fill("Edit restored project");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".revision-chip")).toHaveText("r1");
  expect(await page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {revisions:number[]};
  }).__RESTORE_FIXTURE__.revisions)).toEqual([0]);
});

for (const action of ["New profile", "Derive selected"] as const) {
  test(`${action}: failed native CAS does not select an unsaved delivery profile`, async ({ page }) => {
    const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
    await page.addInitScript(boot => {
      let project = structuredClone(boot.project);
      const host = window as unknown as {
        __PROFILE_CAS_FIXTURE__: { attempts: number; revisions: number[]; successfulIds: string[] };
        __TAURI_INTERNALS__: {
          invoke: (name: string, args?: { request?: {
            revision?: number;
            change?: { type: string; profile?: typeof project.deliverables[number] };
          } }) => Promise<unknown>;
        };
      };
      const state = { attempts: 0, revisions: [] as number[], successfulIds: [] as string[] };
      host.__PROFILE_CAS_FIXTURE__ = state;
      host.__TAURI_INTERNALS__ = { invoke: async (name, args) => {
        if (name === "bootstrap") return structuredClone(boot);
        if (name === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
        if (name === "production_runtime_status") throw new Error("Synthetic runtime not configured");
        if (name === "apply_change") {
          state.attempts += 1;
          state.revisions.push(args?.request?.revision ?? -1);
          if (state.attempts === 1) throw new Error("Synthetic CAS conflict - no commit");
          const change = args?.request?.change;
          if (change?.type !== "upsert_deliverable" || !change.profile) {
            throw new Error("Unsupported synthetic delivery change");
          }
          state.successfulIds.push(change.profile.id);
          project = { ...project, revision: project.revision + 1,
            deliverables: [...project.deliverables, structuredClone(change.profile)] };
          return structuredClone(project);
        }
        throw new Error("Unsupported synthetic IPC command: " + name);
      } };
    }, initial);

    await page.goto("/");
    await page.getByRole("button", { name: "Deliver", exact: true }).click();
    const profiles = page.locator(".delivery-profile-row");
    const initialCount = await profiles.count();
    const initialSelected = await page.locator(".delivery-profile-row.selected").innerText();
    const revision = await page.locator(".revision-chip").innerText();
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Synthetic CAS conflict - no commit");
    await expect(profiles).toHaveCount(initialCount);
    await expect(page.locator(".revision-chip")).toHaveText(revision);
    await expect(page.locator(".delivery-profile-row.selected")).toContainText(initialSelected);
    expect(await page.evaluate(() => (window as unknown as {
      __PROFILE_CAS_FIXTURE__: { attempts: number; successfulIds: string[] };
    }).__PROFILE_CAS_FIXTURE__)).toMatchObject({ attempts: 1, successfulIds: [] });

    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(profiles).toHaveCount(initialCount + 1);
    await expect(page.locator(".revision-chip")).toHaveText("r13");
    const ids = await page.evaluate(() => (window as unknown as {
      __PROFILE_CAS_FIXTURE__: { revisions: number[]; successfulIds: string[] };
    }).__PROFILE_CAS_FIXTURE__);
    expect(ids.revisions).toEqual([12, 12]);
    expect(ids.successfulIds).toHaveLength(1);
    await expect(page.locator(".delivery-profile-row.selected")).toHaveCount(1);
    await expect(page.locator(".delivery-profile-row.selected")).toHaveAttribute("class", /selected/);
  });
}

test("rejected scene creation keeps the editor's draft open until a confirmed CAS", async ({ page }) => {
  const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
  await page.addInitScript(boot => {
    let project = structuredClone(boot.project);
    const state = { requests: 0, revisions: [] as number[] };
    const host = window as unknown as {
      __SCENE_CAS_FIXTURE__: typeof state;
      __TAURI_INTERNALS__: {
        invoke: (command: string, args?: { request?: {
          revision?: number;
          change?: { type: string; name?: string };
        } }) => Promise<unknown>;
      };
    };
    host.__SCENE_CAS_FIXTURE__ = state;
    host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      if (command === "bootstrap") return structuredClone(boot);
      if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
      if (command === "apply_change") {
        state.requests += 1;
        state.revisions.push(args?.request?.revision ?? -1);
        if (state.requests === 1) throw new Error("Synthetic scene CAS conflict");
        const change = args?.request?.change;
        if (change?.type !== "add_scene" || !change.name) throw new Error("Wrong synthetic scene operation");
        const original = project.scenes[0];
        project = { ...project, revision: project.revision + 1, scenes: [...project.scenes, {
          ...original, id: "01900000-0000-7000-8000-0000000000ce", name: change.name,
          beats: [], nodes: [], start: { num: "31", den: "1" }, duration: { num: "6", den: "1" },
        }] };
        return structuredClone(project);
      }
      throw new Error("Unsupported synthetic scene command: " + command);
    } };
  }, initial);

  await page.goto("/");
  const before = await page.locator(".tree-row").count();
  await page.getByRole("button", { name: "Add scene", exact: true }).click();
  await page.getByLabel("New scene name").fill("Original source-approved scene");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Synthetic scene CAS conflict");
  await expect(page.locator(".add-scene-form")).toBeVisible();
  await expect(page.getByLabel("New scene name")).toHaveValue("Original source-approved scene");
  await expect(page.locator(".tree-row")).toHaveCount(before);
  await expect(page.locator(".revision-chip")).toHaveText("r12");

  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.locator(".tree-row")).toHaveCount(before + 1);
  await expect(page.getByRole("button", { name: /Original source-approved scene/ })).toBeVisible();
  await expect(page.locator(".add-scene-form")).toHaveCount(0);
  await expect(page.locator(".revision-chip")).toHaveText("r13");
  expect(await page.evaluate(() => (window as unknown as {
    __SCENE_CAS_FIXTURE__: { requests: number; revisions: number[] };
  }).__SCENE_CAS_FIXTURE__)).toEqual({ requests: 2, revisions: [12, 12] });
});
