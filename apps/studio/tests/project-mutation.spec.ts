import { createHash, randomUUID } from "node:crypto";
import { cp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { expect, test, type BrowserContext, type Page, type TestInfo } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";
import { seedSyntheticProjectSelection } from "./support/selectedSyntheticProject";
import { applyChange as applySyntheticCanvasChange } from "../src/api";
import type { Change, Project } from "../src/types";
import { ScopedChangeService } from "./support/scopedChangeService";

// Reused from PR196 (0d90c0eba9ae646118954f79a29f15b290c21c14), composed with
// the current Brief and Canvas. Synthetic IPC proves UI request coordination,
// not an installed native import, real service CAS or SQLite persistence.
for (const kind of ["asset", "voice"] as const) {
  test(`pending ${kind} import shares the ordinary edit latch and publishes its revision`, async ({ page }) => {
    const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
    await seedSyntheticProjectSelection(page, initial.project.id);
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
        __TAURI_INTERNALS__: { invoke: (command: string, args?: { request?: {
          revision?: number; change?: { type: string; title?: string };
        } }) => Promise<unknown> };
      };
      host.__MUTATION_FIXTURE__ = state;
      host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
        state.calls.push(command);
        if (command === "bootstrap") {
          const syntheticResponse = structuredClone(boot);
          const requestedId = (args as {request?: {project_id?:string|null}} | undefined)?.request?.project_id;
          if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic fixture requires explicit selected project.");
          return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
        }
        if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
        if (command === "import_asset_file" || command === "import_voice_file") {
          return new Promise(resolve => { resolveImport = resolve; });
        }
        if (command === "apply_change") {
          state.revisions.push(args?.request?.revision ?? -1);
          const change = args?.request?.change;
          if (change?.type !== "rename_project" || typeof change.title !== "string") throw new Error("Unsupported synthetic change");
          project = { ...project, revision: project.revision + 1, title: change.title };
          return structuredClone(project);
        }
        throw new Error("Unimplemented synthetic IPC: " + command);
      } };
    }, initial);
    await page.goto("/");
    await expect(page.locator(".sdk-chip")).toContainText("desktop mode");
    await expect(page.locator(".sdk-chip")).not.toContainText("connected");
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r12");
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
    await page.getByRole("textbox", { name: "Project title", exact: true }).fill("Do not race an import");
    await page.getByRole("button", { name: "Save title", exact: true }).click();
    await expect(page.locator(".error-banner")).toContainText("Finish the current project change or import");
    expect(await page.evaluate(() => (window as unknown as {
      __MUTATION_FIXTURE__: { calls: string[] };
    }).__MUTATION_FIXTURE__.calls.filter(command => command === "apply_change").length)).toBe(0);
    await page.evaluate(() => (window as unknown as {
      __MUTATION_FIXTURE__: { complete: () => void };
    }).__MUTATION_FIXTURE__.complete());
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
    await expect(page.locator(".brand-copy")).toContainText("Synthetic import completed");
    await expect(page.locator(".saving-state")).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "Project title", exact: true })).toHaveValue("Do not race an import");
    await expect(page.getByRole("region", { name: "Project title conflict", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Save title", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "Keep my draft Project title", exact: true }).click();
    await page.getByRole("textbox", { name: "Project title", exact: true }).fill("Edit after import");
    await page.getByRole("button", { name: "Save title", exact: true }).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r14");
    await expect(page.locator(".brand-copy")).toContainText("Edit after import");
    await expect(page.getByRole("button", { name: "Save title", exact: true })).toBeDisabled();
    expect(await page.evaluate(() => (window as unknown as {
      __MUTATION_FIXTURE__: { revisions: number[] };
    }).__MUTATION_FIXTURE__.revisions)).toEqual([13]);
  });
}

test("a pending portable restore cannot race source edits or publish into another revision", async ({ page }) => {
  const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
  await seedSyntheticProjectSelection(page, initial.project.id);
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
      __TAURI_INTERNALS__: { invoke: (command: string, args?: {request?: {
        revision?: number; change?: { type: string; title?: string };
      }}) => Promise<unknown> };
    };
    host.__RESTORE_FIXTURE__ = state;
    host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      state.calls.push(command);
      if (command === "bootstrap") {
        const syntheticResponse = structuredClone(boot);
        const requestedId = (args as {request?: {project_id?:string|null}} | undefined)?.request?.project_id;
        if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic fixture requires explicit selected project.");
        return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
      }
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
        const change = args?.request?.change;
        if (change?.type !== "rename_project" || typeof change.title !== "string") throw new Error("Unsupported synthetic change");
        current = { ...current, revision: current.revision + 1, title: change.title };
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
  await page.getByRole("textbox", { name: "Project title", exact: true }).fill("Do not race portable restore");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".error-banner")).toContainText("Finish the current project change or import");
  expect(await page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {calls:string[]};
  }).__RESTORE_FIXTURE__.calls.filter(s => s === "apply_change").length)).toBe(0);

  await page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {complete:()=>void};
  }).__RESTORE_FIXTURE__.complete());
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r0");
  await expect(page.locator(".brand-copy")).toContainText("Portable restored project");
  await expect(page.locator(".saving-state")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "Project title", exact: true })).toHaveValue("Portable restored project");
  await page.getByRole("textbox", { name: "Project title", exact: true }).fill("Edit restored project");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r1");
  await expect(page.locator(".brand-copy")).toContainText("Edit restored project");
  await expect(page.getByRole("button", { name: "Save title", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => (window as unknown as {
    __RESTORE_FIXTURE__: {revisions:number[]};
  }).__RESTORE_FIXTURE__.revisions)).toEqual([0]);
});

for (const action of ["New profile", "Derive selected"] as const) {
  test(`${action}: failed native CAS does not select an unsaved delivery profile`, async ({ page }) => {
    const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
    await seedSyntheticProjectSelection(page, initial.project.id);
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
        if (name === "bootstrap") {
          const requestedId = (args as {request?: {project_id?:string|null}}|undefined)?.request?.project_id;
          if (requestedId !== boot.project.id) throw new Error("Synthetic profile CAS requires selected project.");
          return {...structuredClone(boot), project_selection:{status:"selected",project_id:boot.project.id}};
        }
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
    const selectedProfile = page.locator(".delivery-profile-row.selected");
    const initialSelected = await selectedProfile.textContent();
    const revision = await page.locator(".command-bar .revision-chip").innerText();
    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("Synthetic CAS conflict - no commit");
    await expect(profiles).toHaveCount(initialCount);
    await expect(page.locator(".command-bar .revision-chip")).toHaveText(revision);
    expect(await selectedProfile.textContent()).toBe(initialSelected);
    expect(await page.evaluate(() => (window as unknown as {
      __PROFILE_CAS_FIXTURE__: { attempts: number; successfulIds: string[] };
    }).__PROFILE_CAS_FIXTURE__)).toMatchObject({ attempts: 1, successfulIds: [] });

    await page.getByRole("button", { name: action, exact: true }).click();
    await expect(profiles).toHaveCount(initialCount + 1);
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
    const ids = await page.evaluate(() => (window as unknown as {
      __PROFILE_CAS_FIXTURE__: { revisions: number[]; successfulIds: string[] };
    }).__PROFILE_CAS_FIXTURE__);
    expect(ids.revisions).toEqual([12, 12]);
    expect(ids.successfulIds).toHaveLength(1);
    await expect(selectedProfile).toHaveCount(1);
    await expect.poll(() => selectedProfile.textContent()).not.toBe(initialSelected);
  });
}

test("rejected scene creation keeps the editor's draft open until a confirmed CAS", async ({ page }) => {
  const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
  await seedSyntheticProjectSelection(page, initial.project.id);
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
      if (command === "bootstrap") {
        const syntheticResponse = structuredClone(boot);
        const requestedId = (args as {request?: {project_id?:string|null}} | undefined)?.request?.project_id;
        if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic fixture requires explicit selected project.");
        return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
      }
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
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r12");

  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.locator(".tree-row")).toHaveCount(before + 1);
  await expect(page.locator(".tree-list .tree-row").filter({ hasText: "Original source-approved scene" })).toHaveCount(1);
  await expect(page.locator(".add-scene-form")).toHaveCount(0);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
  expect(await page.evaluate(() => (window as unknown as {
    __SCENE_CAS_FIXTURE__: { requests: number; revisions: number[] };
  }).__SCENE_CAS_FIXTURE__)).toEqual({ requests: 2, revisions: [12, 12] });
});

test("rejected Canvas node creation never selects an object that was not committed", async ({ page }) => {
  const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
  await seedSyntheticProjectSelection(page, initial.project.id);
  await page.addInitScript(boot => {
    let project = structuredClone(boot.project);
    const state = { requests: 0, revisions: [] as number[] };
    const host = window as unknown as {
      __CANVAS_CAS_FIXTURE__: typeof state;
      __TAURI_INTERNALS__: { invoke: (command: string, args?: { request?: {
        revision?: number;
        change?: { type: string; scene_id?: string; node?: typeof project.scenes[number]["nodes"][number] };
      } }) => Promise<unknown> };
    };
    host.__CANVAS_CAS_FIXTURE__ = state;
    host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      if (command === "bootstrap") {
        const syntheticResponse = structuredClone(boot);
        const requestedId = (args as {request?: {project_id?:string|null}} | undefined)?.request?.project_id;
        if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic fixture requires explicit selected project.");
        return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
      }
      if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
      if (command === "apply_change") {
        state.requests += 1;
        state.revisions.push(args?.request?.revision ?? -1);
        if (state.requests === 1) throw new Error("Synthetic Canvas CAS conflict");
        const change = args?.request?.change;
        if (change?.type !== "add_canvas_node" || !change.scene_id || !change.node) {
          throw new Error("Unexpected synthetic Canvas operation");
        }
        project = { ...project, revision: project.revision + 1,
          scenes: project.scenes.map(scene => scene.id === change.scene_id
            ? { ...scene, nodes: [...scene.nodes, structuredClone(change.node!)] } : scene) };
        return structuredClone(project);
      }
      throw new Error("Unsupported synthetic Canvas command: " + command);
    } };
  }, initial);

  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const objects = page.locator(".canvas-tree-row");
  const initialCount = await objects.count();
  const initialSelected = await page.locator(".canvas-tree-row.selected").textContent();
  const createShape = page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true });
  await createShape.click();
  await expect(page.getByRole("alert")).toContainText("Synthetic Canvas CAS conflict");
  await expect(objects).toHaveCount(initialCount);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r12");
  await expect(page.locator(".canvas-tree-row.selected")).toContainText(initialSelected ?? "");
  expect(await page.evaluate(() => (window as unknown as {
    __CANVAS_CAS_FIXTURE__: { requests: number; revisions: number[] };
  }).__CANVAS_CAS_FIXTURE__)).toEqual({ requests: 1, revisions: [12] });

  await createShape.click();
  await expect(objects).toHaveCount(initialCount + 1);
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
  await expect(page.locator(".canvas-tree-row.selected")).toContainText("Shape");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as {
    __CANVAS_CAS_FIXTURE__: { requests: number; revisions: number[] };
  }).__CANVAS_CAS_FIXTURE__)).toEqual({ requests: 2, revisions: [12, 12] });
});

for (const accepted of [true, false]) {
  test(`a ${accepted ? "successful" : "rejected"} node request in scene A preserves a later scene B selection`, async ({ page }) => {
    const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
    const [sceneA, sceneB] = initial.project.scenes;
    expect(sceneB.nodes.length).toBeGreaterThan(0);
    await seedSyntheticProjectSelection(page, initial.project.id);
  await page.addInitScript(boot => {
      let project = structuredClone(boot.project);
      type Node = typeof project.scenes[number]["nodes"][number];
      type Pending = { sceneId: string; node: Node; resolve: (value: typeof project) => void; reject: (reason: Error) => void };
      let pending: Pending | null = null;
      const state = {
        requests: [] as Array<{ sceneId: string; revision: number }>,
        committedNodeId: null as string | null,
        snapshot: () => structuredClone(project),
        complete: (accept: boolean) => {
          const request = pending;
          if (!request) throw new Error("No synthetic Canvas request is pending");
          pending = null;
          if (!accept) { request.reject(new Error("Synthetic scene A CAS conflict")); return; }
          project = { ...project, revision: project.revision + 1, scenes: project.scenes.map(scene =>
            scene.id === request.sceneId ? { ...scene, nodes: [...scene.nodes, request.node] } : scene) };
          state.committedNodeId = request.node.id;
          request.resolve(structuredClone(project));
        },
      };
      const host = window as unknown as {
        __CANVAS_NAVIGATION_FIXTURE__: typeof state;
        __TAURI_INTERNALS__: { invoke: (command: string, args?: { request?: {
          revision?: number; change?: { type: string; scene_id?: string; node?: Node };
        } }) => Promise<unknown> };
      };
      host.__CANVAS_NAVIGATION_FIXTURE__ = state;
      host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
        if (command === "bootstrap") {
          const syntheticResponse = structuredClone(boot);
          const requestedId = (args as {request?: {project_id?:string|null}} | undefined)?.request?.project_id;
          if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic fixture requires explicit selected project.");
          return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
        }
        if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
        if (command === "apply_change") {
          const change = args?.request?.change;
          if (change?.type !== "add_canvas_node" || !change.scene_id || !change.node || pending) {
            throw new Error("Unexpected synthetic Canvas operation");
          }
          const sceneId = change.scene_id, node = structuredClone(change.node);
          state.requests.push({ sceneId, revision: args?.request?.revision ?? -1 });
          return new Promise<typeof project>((resolve, reject) => { pending = { sceneId, node, resolve, reject }; });
        }
        throw new Error("Unsupported synthetic Canvas command: " + command);
      } };
    }, initial);

    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
    await expect(page.locator(".saving-state")).toBeVisible();
    const sceneRows = page.locator(".project-rail .tree-list .tree-row");
    await sceneRows.filter({ hasText: sceneB.name }).click();
    await expect(page.locator(".canvas-tree-row")).toHaveCount(sceneB.nodes.length);
    const selectedInB = page.locator(".canvas-tree-row.selected");
    await expect(selectedInB).toContainText(sceneB.nodes[0].name);
    const selectionBeforeReply = await selectedInB.textContent();

    await page.evaluate(accept => (window as unknown as {
      __CANVAS_NAVIGATION_FIXTURE__: { complete: (accepted: boolean) => void };
    }).__CANVAS_NAVIGATION_FIXTURE__.complete(accept), accepted);
    await expect(page.locator(".saving-state")).toHaveCount(0);
    await expect(page.locator(".command-bar .revision-chip")).toHaveText(accepted ? "r13" : "r12");
    await expect(page.locator(".project-rail .tree-row.selected")).toContainText(sceneB.name);
    await expect(page.locator(".canvas-tree-row")).toHaveCount(sceneB.nodes.length);
    await expect(selectedInB).toHaveText(selectionBeforeReply ?? "");
    const observed = await page.evaluate(() => {
      const state = (window as unknown as { __CANVAS_NAVIGATION_FIXTURE__: {
        requests: Array<{ sceneId: string; revision: number }>;
        committedNodeId: string | null;
        snapshot: () => { scenes: Array<{ id: string; nodes: Array<{ id: string }> }> };
      } }).__CANVAS_NAVIGATION_FIXTURE__;
      return { requests: state.requests, committedNodeId: state.committedNodeId, project: state.snapshot() };
    });
    expect(observed.requests).toEqual([{ sceneId: sceneA.id, revision: initial.project.revision }]);
    const savedA = observed.project.scenes.find(scene => scene.id === sceneA.id)!;
    const savedB = observed.project.scenes.find(scene => scene.id === sceneB.id)!;
    expect(savedA.nodes).toHaveLength(sceneA.nodes.length + (accepted ? 1 : 0));
    expect(savedB).toEqual(sceneB);
    if (accepted) {
      expect(savedA.nodes.at(-1)?.id).toBe(observed.committedNodeId);
      await expect(page.locator(".canvas-workspace").getByRole("alert")).toHaveCount(0);
    } else {
      expect(observed.committedNodeId).toBeNull();
      await expect(page.getByRole("alert")).toContainText("Creating shape in “" + sceneA.name + "” needs attention");
      await expect(page.getByRole("alert")).toContainText("Synthetic scene A CAS conflict");
    }
  });
}

test.describe("Canvas interaction during delayed creation — synthetic UI contract", () => {
  // The callback runs the existing API simulator in Node, where no Tauri
  // runtime is installed. Only its response is held. These cases exercise
  // React selection/draft continuity and the App mutation gate; they do not
  // certify service CAS, a durable store, native rendering or real grants.
  async function installDelayedCreation(page: Page, accepted = true) {
    const initial = structuredClone(fixtureBootstrap);
    initial.native_sdk.mode = "tauri";
    let project = structuredClone(initial.project);
    const requests: Change[] = [];
    const grants = new Map<string, { revision: number; subject: string }>();
    let releaseReply: (() => void) | null = null;
    let creationHeld = false;
    type Request = {
      effect?: string; project_id?: string; generation?: string; revision?: number;
      subject?: string; request_id?: string; effect_grant?: string; change?: Change;
    };
    await seedSyntheticProjectSelection(page, initial.project.id);
    await page.exposeFunction("__canvasInteractionContractInvoke", async (
      command: string, args?: { request?: Request },
    ) => {
      if (command === "bootstrap") {
        const syntheticResponse = { ...initial, project: structuredClone(project) };
        const requestedId = (args as {request?: {project_id?:string|null}} | undefined)?.request?.project_id;
        if (!syntheticResponse.project || requestedId !== syntheticResponse.project.id) throw new Error("Synthetic fixture requires explicit selected project.");
        return {...syntheticResponse, project_selection: {status:"selected",project_id:syntheticResponse.project.id}};
      }
      const request = args?.request;
      if (command !== "issue_effect_grant" && command !== "apply_change") {
        throw new Error("Unsupported synthetic Canvas contract command: " + command);
      }
      if (!request || request.project_id !== project.id || request.generation !== project.generation
        || request.revision !== project.revision) {
        throw new Error("Synthetic Canvas source scope changed");
      }
      if (command === "issue_effect_grant") {
        if (request.effect !== "project_edit" || !request.subject) {
          throw new Error("Unexpected synthetic Canvas effect");
        }
        const token = "synthetic-ui-only-" + randomUUID();
        grants.set(token, { revision: project.revision, subject: request.subject });
        return { token };
      }
      const grant = grants.get(request.effect_grant ?? "");
      if (!grant || grant.revision !== project.revision || grant.subject !== request.request_id) {
        throw new Error("Synthetic Canvas request does not match its token");
      }
      grants.delete(request.effect_grant!);
      const change = request.change;
      if (!change || !["add_canvas_node", "update_canvas_text", "transform_canvas_node"].includes(change.type)) {
        throw new Error("Unexpected synthetic Canvas change");
      }
      requests.push(structuredClone(change));
      if (change.type === "add_canvas_node") {
        if (creationHeld) throw new Error("A synthetic Canvas create was already sent");
        creationHeld = true;
        if (accepted) project = await applySyntheticCanvasChange(project, change);
        await new Promise<void>(resolve => { releaseReply = resolve; });
        releaseReply = null;
        if (!accepted) throw new Error("Synthetic delayed Canvas CAS conflict");
      } else {
        project = await applySyntheticCanvasChange(project, change);
      }
      return structuredClone(project);
    });
    await page.addInitScript(() => {
      const host = window as unknown as {
        __canvasInteractionContractInvoke: (command: string, args?: unknown) => Promise<unknown>;
        __TAURI_INTERNALS__: { invoke: (command: string, args?: unknown) => Promise<unknown> };
      };
      host.__TAURI_INTERNALS__ = { invoke: (command, args) => host.__canvasInteractionContractInvoke(command, args) };
    });
    return {
      initial: initial.project,
      requests,
      snapshot: () => structuredClone(project),
      replyHeld: () => releaseReply !== null,
      release: () => releaseReply?.(),
    };
  }

  for (const accepted of [true, false]) {
    for (const changeSelection of [true, false]) {
      test(`a ${accepted ? "successful" : "rejected"} create preserves later ${changeSelection ? "selection and text" : "text on the same selected node"}`, async ({ page }) => {
        const fixture = await installDelayedCreation(page, accepted);
        const scene = fixture.initial.scenes[0];
        const editedNode = scene.nodes[changeSelection ? 1 : 0];
        const laterText = "Authored after the create request — keep this exact text.";
        await page.goto("/");
        await page.getByRole("button", { name: "Canvas", exact: true }).click();
        await expect(page.locator(".canvas-selection-title strong")).toHaveText(scene.nodes[0].name);
        await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
        await expect.poll(fixture.replyHeld).toBe(true);
        await expect(page.locator(".saving-state")).toBeVisible();
        if (changeSelection) {
          const laterSelection = page.locator(".canvas-tree-row").filter({ hasText: editedNode.name });
          await expect(laterSelection).toBeEnabled();
          await laterSelection.click();
        }
        const text = page.getByRole("textbox", { name: "Canvas text", exact: true });
        await expect(text).toBeEditable();
        await text.fill(laterText);
        await expect(text).toHaveValue(laterText);
        fixture.release();
        await expect(page.locator(".saving-state")).toHaveCount(0);
        await expect(page.locator(".command-bar .revision-chip")).toHaveText(accepted ? "r13" : "r12");
        await expect(page.locator(".canvas-tree-row")).toHaveCount(scene.nodes.length + (accepted ? 1 : 0));
        await expect(page.locator(".canvas-selection-title strong")).toHaveText(editedNode.name);
        await expect(text).toHaveValue(laterText);
        const afterCreate = fixture.snapshot();
        expect(afterCreate.scenes[0].nodes.slice(0, scene.nodes.length)).toEqual(scene.nodes);
        expect(afterCreate.scenes.slice(1)).toEqual(fixture.initial.scenes.slice(1));
        expect(fixture.requests).toHaveLength(1);
        if (!accepted) {
          await expect(page.locator(".canvas-workspace").getByRole("alert")).toContainText("Synthetic delayed Canvas CAS conflict");
        }
        // The surviving text is an unsent edit. Only the author's explicit
        // follow-up commit changes that original object in the next revision.
        await page.getByRole("button", { name: "Commit text", exact: true }).click();
        await expect(page.locator(".command-bar .revision-chip")).toHaveText(accepted ? "r14" : "r13");
        expect(fixture.requests).toHaveLength(2);
        expect(fixture.requests[1]).toEqual({
          type: "update_canvas_text", scene_id: scene.id, node_id: editedNode.id, text: laterText,
        });
        expect(fixture.snapshot().scenes[0].nodes.find(node => node.id === editedNode.id)?.text).toBe(laterText);
        await expect(text).toHaveValue(laterText);
      });
    }
  }

  test("a successful create preserves a later transform on the same selected node", async ({ page }) => {
    const fixture = await installDelayedCreation(page);
    const node = fixture.initial.scenes[0].nodes[0];
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
    await expect.poll(fixture.replyHeld).toBe(true);
    const x = page.getByRole("spinbutton", { name: "Canvas X", exact: true });
    await expect(x).toBeEditable();
    await x.fill("423");
    fixture.release();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
    await expect(page.locator(".canvas-selection-title strong")).toHaveText(node.name);
    await expect(x).toHaveValue("423");
    expect(fixture.snapshot().scenes[0].nodes[0]).toEqual(node);
    await page.getByRole("button", { name: "Commit transform", exact: true }).click();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r14");
    expect(fixture.snapshot().scenes[0].nodes[0]).toEqual({ ...node, x: 423 });
  });

  test("a successful create preserves a later motion value on the same selected node", async ({ page }) => {
    const fixture = await installDelayedCreation(page);
    const node = fixture.initial.scenes[0].nodes[0];
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-create-row").getByRole("button", { name: "shape", exact: true }).click();
    await expect.poll(fixture.replyHeld).toBe(true);
    const motion = page.getByRole("spinbutton", { name: "Motion keyframe value", exact: true });
    await expect(motion).toBeEditable();
    await motion.fill("0.35");
    fixture.release();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
    await expect(page.locator(".canvas-selection-title strong")).toHaveText(node.name);
    await expect(motion).toHaveValue("0.35");
    expect(fixture.snapshot().scenes[0].nodes[0]).toEqual(node);
    expect(fixture.requests).toHaveLength(1);
  });

  test("returning to the original selection still supersedes a delayed create", async ({ page }) => {
    const fixture = await installDelayedCreation(page);
    const [original, other] = fixture.initial.scenes[0].nodes;
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    await expect.poll(fixture.replyHeld).toBe(true);
    await page.locator(".canvas-tree-row").filter({ hasText: other.name }).click();
    await page.locator(".canvas-tree-row").filter({ hasText: original.name }).click();
    fixture.release();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
    await expect(page.locator(".canvas-selection-title strong")).toHaveText(original.name);
    await expect(page.getByRole("textbox", { name: "Canvas text", exact: true })).toHaveValue(original.text ?? "");
    expect(fixture.requests).toHaveLength(1);
  });

  test("a successful delayed create selects its saved object when the inspector was untouched", async ({ page }) => {
    const fixture = await installDelayedCreation(page);
    const scene = fixture.initial.scenes[0];
    await page.goto("/");
    await page.getByRole("button", { name: "Canvas", exact: true }).click();
    await page.locator(".canvas-create-row").getByRole("button", { name: "text", exact: true }).click();
    await expect.poll(fixture.replyHeld).toBe(true);
    await expect(page.locator(".canvas-tree-row")).toHaveCount(scene.nodes.length);
    fixture.release();
    await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
    const saved = fixture.snapshot().scenes[0].nodes.at(-1)!;
    expect(scene.nodes.some(node => node.id === saved.id)).toBe(false);
    await expect(page.locator(".canvas-tree-row")).toHaveCount(scene.nodes.length + 1);
    await expect(page.locator(".canvas-selection-title strong")).toHaveText(saved.name);
    await expect(page.getByRole("textbox", { name: "Canvas text", exact: true })).toHaveValue(saved.text ?? "");
    await expect(page.getByRole("spinbutton", { name: "Canvas X", exact: true })).toHaveValue(String(saved.x));
    expect(fixture.requests).toHaveLength(1);
  });
});

test("a delayed initial bootstrap cannot roll back a project already published by a mutation", async ({ page }) => {
  const initial = { ...fixtureBootstrap, native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" as const } };
  await seedSyntheticProjectSelection(page, initial.project.id);
  await page.addInitScript(boot => {
    let project = structuredClone(boot.project);
    let releaseInitial: (() => void) | null = null;
    const state = {
      bootstrapCalls: 0,
      revisions: [] as number[],
      release: () => {
        if (!releaseInitial) throw new Error("No initial bootstrap is waiting");
        releaseInitial(); releaseInitial = null;
      },
    };
    const host = window as unknown as {
      __BOOTSTRAP_MUTATION_FIXTURE__: typeof state;
      __TAURI_INTERNALS__: { invoke: (command: string, args?: { request?: {
        revision?: number; change?: { type: string; title?: string };
      } }) => Promise<unknown> };
    };
    host.__BOOTSTRAP_MUTATION_FIXTURE__ = state;
    host.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      if (command === "bootstrap") {
        const requestedId = (args?.request as {project_id?:string|null}|undefined)?.project_id;
        if (requestedId !== project.id) throw new Error("Delayed bootstrap mock requires selected project.");
        state.bootstrapCalls++;
        const captured = { ...boot, project: structuredClone(project),
          project_selection: {status:"selected",project_id:project.id} };
        // The dev acceptance server renders App under StrictMode. Return the
        // second opening first, then release the old first read after a save.
        if (state.bootstrapCalls === 1) return new Promise(resolve => { releaseInitial = () => resolve(captured); });
        return captured;
      }
      if (command === "issue_effect_grant") return { token: "synthetic-no-native-authority" };
      if (command === "apply_change") {
        const change = args?.request?.change;
        if (change?.type !== "rename_project" || typeof change.title !== "string") throw new Error("Unexpected synthetic change");
        state.revisions.push(args?.request?.revision ?? -1);
        if (args?.request?.revision !== project.revision) throw new Error("Synthetic source revision changed");
        project = { ...project, revision: project.revision + 1, title: change.title };
        return structuredClone(project);
      }
      throw new Error("Unsupported synthetic bootstrap command: " + command);
    } };
  }, initial);

  await page.goto("/");
  await expect.poll(() => page.evaluate(() => (window as unknown as {
    __BOOTSTRAP_MUTATION_FIXTURE__: { bootstrapCalls: number };
  }).__BOOTSTRAP_MUTATION_FIXTURE__.bootstrapCalls)).toBeGreaterThanOrEqual(2);
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByRole("textbox", { name: "Project title", exact: true }).fill("Saved before late opening");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r13");
  await page.evaluate(() => (window as unknown as {
    __BOOTSTRAP_MUTATION_FIXTURE__: { release: () => void };
  }).__BOOTSTRAP_MUTATION_FIXTURE__.release());

  await expect(page.locator(".brand-copy")).toContainText("Saved before late opening");
  await page.getByRole("textbox", { name: "Project title", exact: true }).fill("Saved after late opening");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r14");
  await expect(page.locator(".brand-copy")).toContainText("Saved after late opening");
  expect(await page.evaluate(() => (window as unknown as {
    __BOOTSTRAP_MUTATION_FIXTURE__: { revisions: number[] };
  }).__BOOTSTRAP_MUTATION_FIXTURE__.revisions)).toEqual([12, 13]);
});

// The following cases are separate from the synthetic IPC cases above. Files
// are explicitly synthetic source bytes, but every grant, measurement, import,
// bundle, revision and journal result crosses the real StudioService JSONL
// process. The opt-in desktop-invoke routing is a browser test seam, never an
// installed Tauri/native-renderer or creative-quality certification.
test.describe("real service imports and portable restore", () => {
  test.skip(!process.env.MOTIONWRIGHT_SERVICE_TEST_BIN,
    "Build studio_review_session and set MOTIONWRIGHT_SERVICE_TEST_BIN; no simulated service fallback.");
  test.describe.configure({ timeout: 60_000 });

  const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
  const field = (page: Page, name: string) => name === "Project title"
    ? page.locator("#project-title-rich") : page.getByRole("textbox", { name, exact: true });
  const hash = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
  const stamp = (project: Project) => ({ project_id: project.id, generation: project.generation, revision: project.revision });
  const svgBytes = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="96" height="64"><title>Synthetic local import file</title><rect width="96" height="64" fill="#276A78"/></svg>');

  interface InvokeRequest extends Record<string, unknown> {
    project_id?: string; generation?: string; revision?: number;
    effect_grant?: string; path?: string; change?: Change;
  }

  function requests(service: ScopedChangeService, command: string): InvokeRequest[] {
    return service.transcript.flatMap(entry => entry.direction === "request" &&
      "command" in entry.payload && entry.payload.command === command
      ? [entry.payload.args?.request as InvokeRequest] : []);
  }

  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>(complete => { resolve = complete; });
    return { promise, resolve };
  }

  /** Only the reply is delayed: the observed result has already come from Rust. */
  function holdRealReply(service: ScopedChangeService, heldCommand: string) {
    const original = service.invoke.bind(service);
    const observed = deferred<{ project: Project } | { error: unknown }>();
    const release = deferred<void>();
    const finished = deferred<void>();
    let armed = true;
    let matched = false;
    service.invoke = async <T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> => {
      if (!armed || command !== heldCommand) return original<T>(command, args);
      armed = false;
      matched = true;
      try {
        let outcome: { project: Project } | { error: unknown };
        try { outcome = { project: await original<Project>(command, args) }; }
        catch (error) { outcome = { error }; }
        observed.resolve(outcome);
        await release.promise;
        if ("error" in outcome) throw outcome.error;
        return outcome.project as T;
      } finally { finished.resolve(); }
    };
    return {
      observed: observed.promise,
      release: () => release.resolve(),
      async dispose() {
        service.invoke = original;
        release.resolve();
        if (matched) await finished.promise;
      },
    };
  }

  function pcm16Wav() {
    // One second of alternating, non-silent PCM samples; not recorded speech.
    // Rust must decode these bytes to establish duration/rate/channels/peaks.
    const sampleRate = 48_000;
    const frames = 48_000;
    const bytes = Buffer.alloc(44 + frames * 2);
    bytes.write("RIFF", 0); bytes.writeUInt32LE(bytes.length - 8, 4);
    bytes.write("WAVEfmt ", 8); bytes.writeUInt32LE(16, 16);
    bytes.writeUInt16LE(1, 20); bytes.writeUInt16LE(1, 22);
    bytes.writeUInt32LE(sampleRate, 24); bytes.writeUInt32LE(sampleRate * 2, 28);
    bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34);
    bytes.write("data", 36); bytes.writeUInt32LE(frames * 2, 40);
    for (let frame = 0; frame < frames; frame++) bytes.writeInt16LE(frame % 2 ? 8192 : -8192, 44 + frame * 2);
    return bytes;
  }

  async function verifyCas(service: ScopedChangeService, project: Project) {
    const evidence: { asset_id: string; sha256: string; size_bytes: number }[] = [];
    for (const asset of project.assets) {
      expect(asset.content_sha256).toMatch(/^[a-f0-9]{64}$/);
      const digest = asset.content_sha256!;
      const bytes = await readFile(join(service.directory, "blobs", "sha256", digest.slice(0, 2), digest));
      expect(hash(bytes)).toBe(digest);
      evidence.push({ asset_id: asset.id, sha256: digest, size_bytes: bytes.length });
    }
    return evidence;
  }

  async function reopenExact(service: ScopedChangeService, expected: Project) {
    const history = await service.history(expected.id, 0, 256);
    // close() alone is not a clean-exit claim. Require an actual new process to
    // read the same unmodified SQLite/WAL/CAS and compare document + journal.
    // Do not remove sidecars or normalize the original directory to make it pass.
    await service.close();
    await service.start();
    expect(await service.project(expected.id)).toEqual(expected);
    expect(await service.history(expected.id, 0, 256)).toEqual(history);
    const cas = await verifyCas(service, expected);
    return { project: expected, history, cas, verification: "new_process_reopen_of_unmodified_complete_store" };
  }

  async function evidence(testInfo: TestInfo, services: ScopedChangeService[], observations: unknown) {
    const path = testInfo.outputPath("canonical-mutation-real-service-evidence.json");
    await writeFile(path, JSON.stringify({
      fixture: "synthetic_source_bytes_real_service",
      evidenceScope: "browser_service_test_jsonl_real_grants_sqlite_cas_measurement_and_bundle; excludes_installed_tauri_native_render_and_human_creative_acceptance",
      lifecycleLimit: "Reopen observations are explicit below; await close alone does not prove a clean exit or certify a later packaged copy.",
      observations,
      services: services.map(service => ({
        databasePath: service.databasePath, binaryPath: service.binaryPath,
        stderr: service.stderr, processExits: service.processExits, transcript: service.transcript,
      })),
    }, null, 2));
    await testInfo.attach("canonical-mutation-real-service", { path, contentType: "application/json" });
  }

  async function finishRealEvidence(testInfo: TestInfo, services: ScopedChangeService[], observations: Record<string, unknown>) {
    // Every peer gets a close attempt even when another process failed. Only a
    // fully quiescent set with actual code 0 / signal null can be captured.
    const closed = await Promise.allSettled(services.map(service => service.close()));
    const failed = closed.flatMap(result => result.status === "rejected" ? [String(result.reason)] : []);
    let captureFailure: unknown;
    if (failed.length === 0) {
      try {
        const stores = services.filter((service, index) => services.findIndex(peer => peer.directory === service.directory) === index);
        const captures = [];
        for (const [index, service] of stores.entries()) {
          captures.push(await service.captureClosedStore(testInfo.outputPath(`closed-store-${index + 1}`), services));
        }
        observations.closedStoreCaptures = captures;
      } catch (error) { captureFailure = error; }
    }
    if (failed.length) observations.closeFailures = failed;
    if (captureFailure) observations.captureFailure = String(captureFailure);
    await evidence(testInfo, services, observations);
    if (failed.length) throw new Error(`Real service evidence requires clean exits: ${failed.join("; ")}`);
    if (captureFailure) throw captureFailure;
  }

  for (const kind of ["asset", "voice"] as const) {
    test(`real ${kind} import already persisted holds the Brief latch until its real reply is published`, async ({ page }, testInfo) => {
      const service = await ScopedChangeService.create({
        databasePath: testInfo.outputPath("service", "studio.sqlite3"), desktopInvokeFixture: true,
      });
      let held: ReturnType<typeof holdRealReply> | undefined;
      const observations: Record<string, unknown> = {};
      try {
        const boot = await service.fixtureBootstrap();
        const initial = boot.project;
        observations.initial = initial;
        expect(boot.test_transport).toBe("service-test-jsonl");
        expect(boot.desktop_invoke_fixture).toBe(true);
        const sourceBytes = kind === "asset" ? svgBytes : pcm16Wav();
        const sourcePath = testInfo.outputPath(`actual-synthetic-source.${kind === "asset" ? "svg" : "wav"}`);
        await writeFile(sourcePath, sourceBytes);
        await service.attachFixturePage(page);
        await page.goto("/");
        await expect(page.locator(".command-bar .revision-chip")).toHaveText(`r${initial.revision}`);
        if (kind === "asset") {
          await button(page, "Import local asset").click();
          await field(page, "Local asset path").fill(sourcePath);
        } else {
          await button(page, "Audio").click();
          const corruptPath = testInfo.outputPath("corrupt-synthetic.wav");
          await writeFile(corruptPath, Buffer.from("Explicit synthetic corrupt WAV: not decodable audio."));
          const historyBefore = await service.history(initial.id);
          await field(page, "Voice file absolute path").fill(corruptPath);
          await field(page, "Voice take label").fill("Synthetic PCM measurement fixture");
          await button(page, "Import + measure").click();
          await expect(page.locator(".audio-workspace .portable-message.error")).toContainText("Voice import blocked.");
          await expect(field(page, "Voice file absolute path")).toHaveValue(corruptPath);
          await expect(field(page, "Voice take label")).toHaveValue("Synthetic PCM measurement fixture");
          expect(await service.project(initial.id)).toEqual(initial);
          expect(await service.history(initial.id)).toEqual(historyBefore);
          // The service may retain ingested corrupt bytes as an unreferenced CAS
          // blob. No measured track, asset reference or journal event is admitted.
          observations.corruptVoiceRejectedWithoutDocumentChange = true;
          await field(page, "Voice file absolute path").fill(sourcePath);
        }
        held = holdRealReply(service, `import_${kind}_file`);
        await button(page, kind === "asset" ? "Import" : "Import + measure").click();
        const result = await held.observed;
        if ("error" in result) throw result.error;
        const imported = result.project;
        observations.imported = imported;
        expect(imported.revision).toBe(initial.revision + 1);
        expect(await service.project(initial.id)).toEqual(imported);
        expect(imported.assets).toHaveLength(initial.assets.length + 1);
        const importedAsset = imported.assets.find(asset => !initial.assets.some(before => before.id === asset.id))!;
        expect(importedAsset.content_sha256).toBe(hash(sourceBytes));
        expect(importedAsset.media_type).toBe(kind === "asset" ? "image/svg+xml" : "audio/wav");
        expect(imported.scenes).toEqual(initial.scenes);
        expect(imported.production_design).toEqual(initial.production_design);
        expect(imported.brief).toEqual(initial.brief);
        if (kind === "voice") {
          const track = imported.audio.voice_tracks.at(-1)!;
          expect(track).toMatchObject({ sample_rate_hz: 48_000, channels: 1,
            measured_duration: { num: "1", den: "1" }, source_sha256: hash(sourceBytes),
            label: "Synthetic PCM measurement fixture", loudness_lufs: null, true_peak_dbfs: null });
          expect(track.asset_id).toBe(importedAsset.id);
        } else expect(imported.audio).toEqual(initial.audio);

        await expect(page.locator(".command-bar .revision-chip")).toHaveText(`r${initial.revision}`);
        await expect(page.locator(".saving-state")).toContainText("Committing");
        await button(page, "Brief").click();
        const authoredTitle = `Authored after the real ${kind} import`;
        await field(page, "Project title").fill(authoredTitle);
        await field(page, "Constraints").fill("Unsubmitted editorial constraint must remain editable.");
        await button(page, "Save title").click();
        await expect(page.locator(".error-banner")).toContainText("Finish the current project change or import");
        expect(requests(service, "apply_change")).toHaveLength(0);
        expect(await service.project(initial.id)).toEqual(imported);

        held.release();
        await expect(page.locator(".command-bar .revision-chip")).toHaveText(`r${imported.revision}`);
        await expect(page.locator(".saving-state")).toHaveCount(0);
        await expect(field(page, "Project title")).toHaveValue(authoredTitle);
        await expect(button(page, "Save title")).toBeEnabled();
        await button(page, "Save title").click();
        await expect(page.locator(".command-bar .revision-chip")).toHaveText(`r${imported.revision + 1}`);
        const final = await service.project(initial.id);
        observations.final = final;
        expect(final).toEqual({ ...imported, title: authoredTitle, revision: imported.revision + 1,
          branches: imported.branches.map(branch => branch.id === imported.active_branch
            ? { ...branch, head_revision: imported.revision + 1 } : branch), updated_at: final.updated_at });
        expect(requests(service, "apply_change")).toEqual([expect.objectContaining({
          ...stamp(imported), change: { type: "rename_project", title: authoredTitle },
        })]);
        await expect(field(page, "Constraints")).toHaveValue("Unsubmitted editorial constraint must remain editable.");
        if (kind === "voice") {
          await button(page, "Audio").click();
          await expect(page.getByRole("img", { name: "Measured sample-peak waveform page 1 of 1", exact: true })).toBeVisible();
          const waveform = await service.waveform(final.id, final.audio.voice_tracks.at(-1)!.id);
          expect(waveform).toMatchObject({ source_sha256: hash(sourceBytes), sample_rate_hz: 48_000,
            channels: 1, total_frames: 48_000, algorithm: "sample-peak-max-abs-v1" });
          expect(waveform.peaks.length).toBeGreaterThan(0);
          for (const peak of waveform.peaks) expect(peak).toBeCloseTo(0.25, 5);
          observations.waveform = waveform;
        }
        await page.screenshot({ path: testInfo.outputPath(`real-${kind}-import-and-edit.png`), fullPage: true });
        await rm(sourcePath);
        observations.source = { sha256: hash(sourceBytes), size_bytes: sourceBytes.length, removedBeforeReopen: true };
        observations.reopen = await reopenExact(service, final);
        await page.reload();
        await button(page, "Brief").click();
        await expect(field(page, "Project title")).toHaveValue(authoredTitle);
        await expect(field(page, "Constraints")).toHaveValue("Unsubmitted editorial constraint must remain editable.");
        expect(requests(service, "apply_change")).toHaveLength(1);
      } finally {
        await held?.dispose();
        await finishRealEvidence(testInfo, [service], observations);
      }
    });
  }

  test("real import grants reject missing authority, path/effect/scope/generation mismatches, stale sources and reopen replay", async ({}, testInfo) => {
    const service = await ScopedChangeService.create({ databasePath: testInfo.outputPath("service", "studio.sqlite3") });
    let peer: ScopedChangeService | undefined;
    const observations: Record<string, unknown> = {};
    try {
      const boot = await service.fixtureBootstrap();
      expect(boot.desktop_invoke_fixture).toBe(false);
      expect(boot.native_sdk.mode).toBe("browser-demo");
      const initial = boot.project;
      observations.initial = initial;
      const source = testInfo.outputPath("scope-source.svg");
      const otherPath = testInfo.outputPath("other-scope-source.svg");
      await writeFile(source, svgBytes); await writeFile(otherPath, svgBytes);
      const importRequest = (project: Project, token: string, path = source) => ({ request: {
        ...stamp(project), effect_grant: token, path, name: null, media_type: null,
      } });
      await expect(service.invoke("import_asset_file", importRequest(initial, randomUUID()))).rejects.toThrow("Effect grant");
      await expect(service.grant("import_local", { ...initial, generation: randomUUID() }, source)).rejects.toThrow("scope is stale");
      await expect(service.invoke("issue_effect_grant", { request: {
        effect: "import_local", project_id: initial.id, generation: null, revision: initial.revision, subject: source,
      } })).rejects.toThrow("complete or absent");
      await expect(service.grant("deliver_local", null, source)).rejects.toThrow("requires an exact project");
      for (const effect of ["render_local", "remote_egress", "install_runtime"] as const) {
        await expect(service.grant(effect, initial, source)).rejects.toThrow("no render or external authority");
      }
      const wrongScope = await service.grant("import_local", null, source);
      await expect(service.invoke("import_asset_file", importRequest(initial, wrongScope.token))).rejects.toThrow("Effect grant");
      const generationBound = await service.grant("import_local", initial, source);
      await expect(service.invoke("import_asset_file", importRequest(
        { ...initial, generation: randomUUID() }, generationBound.token,
      ))).rejects.toThrow("scope is stale");
      const wrongEffect = await service.grant("deliver_local", initial, source);
      await expect(service.invoke("import_asset_file", importRequest(initial, wrongEffect.token))).rejects.toThrow("Effect grant");
      const boundPath = await service.grant("import_local", initial, source);
      await expect(service.invoke("import_asset_file", importRequest(initial, boundPath.token, otherPath))).rejects.toThrow("Effect grant");
      await expect(service.invoke("import_asset_file", importRequest(initial, boundPath.token))).rejects.toThrow("Effect grant");
      expect(await service.project(initial.id)).toEqual(initial);
      expect(await service.history(initial.id, initial.revision)).toEqual([]);

      const stale = await service.grant("import_local", initial, source);
      peer = await service.peer();
      const concurrent = await peer.apply(initial, { type: "rename_project", title: "Concurrent service author" });
      await expect(service.invoke("import_asset_file", importRequest(initial, stale.token))).rejects.toThrow("scope is stale");
      expect(await service.project(initial.id)).toEqual(concurrent);
      const imported = await service.importAsset(concurrent, source);
      expect(imported.revision).toBe(concurrent.revision + 1);
      expect(imported.assets.at(-1)!.content_sha256).toBe(hash(svgBytes));
      const revokedByReopen = await service.grant("import_local", imported, otherPath);
      expect(await service.reopen(imported.id)).toEqual(imported);
      await expect(service.invoke("import_asset_file", importRequest(imported, revokedByReopen.token, otherPath))).rejects.toThrow("Effect grant");
      expect(await service.project(imported.id)).toEqual(imported);
      expect((await service.history(imported.id, initial.revision)).map(event => event.change.type)).toEqual(["rename_project", "add_asset"]);
      await peer.close();
      observations.final = imported;
      observations.reopen = await reopenExact(service, imported);
    } finally {
      await finishRealEvidence(testInfo, peer ? [service, peer] : [service], observations);
    }
  });

  test("real portable export and verified restore rotate generation, preserve CAS and serialize the next Brief edit", async ({ page, browser }, testInfo) => {
    const source = await ScopedChangeService.create({
      databasePath: testInfo.outputPath("source-service", "studio.sqlite3"), desktopInvokeFixture: true,
    });
    let target: ScopedChangeService | undefined;
    let held: ReturnType<typeof holdRealReply> | undefined;
    let isolatedTargetContext: BrowserContext | null = null;
    const observations: Record<string, unknown> = {};
    try {
      target = await ScopedChangeService.create({
        databasePath: testInfo.outputPath("target-service", "studio.sqlite3"), desktopInvokeFixture: true,
      });
      const sourceFile = testInfo.outputPath("portable-authored-source.svg");
      await writeFile(sourceFile, svgBytes);
      let donor = (await source.fixtureBootstrap()).project;
      donor = await source.importAsset(donor, sourceFile, "Actual portable source bytes");
      donor = await source.apply(donor, { type: "rename_project", title: "Portable source from a real service" });
      const original = (await target.fixtureBootstrap()).project;
      const donorHistory = await source.history(donor.id, 0, 256);
      observations.source = donor; observations.targetBefore = original;
      expect(donor.id).not.toBe(original.id);
      const bundlePath = testInfo.outputPath("portable-export.motionwright");
      await source.attachFixturePage(page);
      await page.goto("/");
      await button(page, "Deliver").click();
      await field(page, "Portable export path").fill(bundlePath);
      await button(page, "Export bundle").click();
      await expect(page.locator(".portable-project .portable-message")).toContainText("Portable bundle written to");
      const manifestBytes = await readFile(join(bundlePath, "manifest.json"));
      const manifest = JSON.parse(manifestBytes.toString()) as {
        backup: { project: Project }; blobs: { relative_path: string; sha256: string; size_bytes: number }[];
      };
      expect(manifest.backup.project).toEqual(donor);
      expect(manifest.blobs).toHaveLength(donor.assets.length);
      for (const blob of manifest.blobs) {
        const bytes = await readFile(join(bundlePath, blob.relative_path));
        expect(hash(bytes)).toBe(blob.sha256); expect(bytes.length).toBe(blob.size_bytes);
      }
      const exportRequest = requests(source, "export_project_bundle")[0];
      await expect(source.invoke("export_project_bundle", { request: exportRequest })).rejects.toThrow("Effect grant");
      await button(page, "Export bundle").click();
      await expect(page.locator(".portable-project .portable-message.error")).toContainText("export destination already exists");
      expect(await readFile(join(bundlePath, "manifest.json"))).toEqual(manifestBytes);
      expect(await source.project(donor.id)).toEqual(donor);
      expect(await source.history(donor.id, 0, 256)).toEqual(donorHistory);

      // Different canonical stores must not share a browser localStorage selection.
      isolatedTargetContext = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
      const targetPage = await isolatedTargetContext.newPage();
      await target.attachFixturePage(targetPage);
      await targetPage.goto("/");
      await button(targetPage, "Deliver").click();
      const corruptBundle = testInfo.outputPath("corrupt-bundle.motionwright");
      await cp(bundlePath, corruptBundle, { recursive: true });
      const corruptBlob = join(corruptBundle, manifest.blobs[0].relative_path);
      const corrupted = await readFile(corruptBlob); corrupted[corrupted.length - 1] ^= 1;
      await writeFile(corruptBlob, corrupted);
      await field(targetPage, "Portable import path").fill(corruptBundle);
      await button(targetPage, "Inspect bundle").click();
      await expect(targetPage.locator(".portable-project .portable-message.error")).toContainText("digest");
      await expect(button(targetPage, "Import verified bundle")).toBeDisabled();
      expect(await target.project(original.id)).toEqual(original);
      expect(requests(target, "issue_effect_grant")).toHaveLength(0);
      await field(targetPage, "Portable import path").fill(bundlePath);
      await button(targetPage, "Inspect bundle").click();
      await expect(targetPage.getByLabel("Portable bundle preflight", { exact: true })).toContainText(donor.title);
      const plan = await target.inspectBundle(bundlePath);
      expect(plan).toMatchObject({ project_id: donor.id, source_generation: donor.generation,
        revision: String(donor.revision), event_count: donorHistory.length, blob_count: donor.assets.length, rotates_generation: true });
      const wronglyScoped = await target.grant("import_local", original, bundlePath);
      await expect(target.invoke("import_project_bundle", { request: { path: bundlePath, effect_grant: wronglyScoped.token } })).rejects.toThrow("Effect grant");
      expect(await target.project(original.id)).toEqual(original);

      held = holdRealReply(target, "import_project_bundle");
      await button(targetPage, "Import verified bundle").click();
      const result = await held.observed;
      if ("error" in result) throw result.error;
      const restored = result.project;
      observations.restored = restored;
      expect(restored.id).toBe(donor.id);
      expect(restored.generation).not.toBe(donor.generation);
      expect(restored.generation).not.toBe(original.generation);
      expect(restored).toEqual({ ...donor, generation: restored.generation, updated_at: restored.updated_at });
      expect(await target.project(restored.id)).toEqual(restored);
      expect(await target.history(restored.id, 0, 256)).toEqual(donorHistory);
      expect(await target.project(original.id)).toEqual(original);
      await expect(targetPage.locator(".command-bar .revision-chip")).toHaveText(`r${original.revision}`);
      await button(targetPage, "Brief").click();
      await field(targetPage, "Project title").fill("Old project draft must not race restore");
      await button(targetPage, "Save title").click();
      await expect(targetPage.locator(".error-banner")).toContainText("Finish the current project change or import");
      expect(requests(target, "apply_change")).toHaveLength(0);
      held.release();
      await expect(targetPage.locator(".brand-copy")).toContainText(donor.title);
      await expect(targetPage.locator(".command-bar .revision-chip")).toHaveText(`r${donor.revision}`);
      await expect(field(targetPage, "Project title")).toHaveValue(donor.title);
      await field(targetPage, "Project title").fill("Explicit edit after the real portable restore");
      await button(targetPage, "Save title").click();
      await expect(targetPage.locator(".command-bar .revision-chip")).toHaveText(`r${donor.revision + 1}`);
      const final = await target.project(restored.id);
      expect(final).toEqual({ ...restored, title: "Explicit edit after the real portable restore",
        revision: restored.revision + 1, branches: restored.branches.map(branch => branch.id === restored.active_branch
          ? { ...branch, head_revision: restored.revision + 1 } : branch), updated_at: final.updated_at });
      expect(requests(target, "apply_change")).toEqual([expect.objectContaining({ ...stamp(restored),
        change: { type: "rename_project", title: final.title },
      })]);
      expect(await target.project(original.id)).toEqual(original);
      await expect(target.importBundle(bundlePath)).rejects.toThrow("project already exists");
      expect(await target.project(restored.id)).toEqual(final);
      await rm(sourceFile);
      await source.close();
      observations.manifest = { sha256: hash(manifestBytes), blobs: manifest.blobs };
      observations.final = final;
      observations.reopen = await reopenExact(target, final);
      expect(await target.project(original.id)).toEqual(original);
      await targetPage.reload();
      await button(targetPage, "Brief").click();
      await expect(field(targetPage, "Project title")).toHaveValue(final.title);
      await targetPage.screenshot({ path: testInfo.outputPath("real-portable-restored-edited-reopened.png"), fullPage: true });
      expect(requests(target, "apply_change")).toHaveLength(1);
    } finally {
      try {
        await held?.dispose();
        await isolatedTargetContext?.close();
      } finally {
        await finishRealEvidence(testInfo, target ? [source, target] : [source], observations);
      }
    }
  });
});
