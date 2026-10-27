import { expect, test } from "./support/selectedSyntheticProject";
import { fixtureBootstrap } from "../src/fixture";
import type { Page as RecipeFixturePage } from "@playwright/test";
import { applyChange as applySyntheticFixtureChange } from "../src/api";
import type { Change as RecipeFixtureChange, Project as RecipeFixtureProject } from "../src/types";

interface RecipeFixtureRequest {
  effect?: string; project_id: string; generation: string; revision: number;
  effect_grant: string; change?: RecipeFixtureChange;
}

// Explicit synthetic authoring fixture. The Node-side API uses its existing
// simulator branch, not SQLite, Tauri, native rendering or owner authority.
// The real UI still issues ProjectEdit/apply_change and observes the returned
// Project revision; no saved recipe is injected directly into the component.
async function installRecipeAuthoringFixture(page: RecipeFixturePage) {
  await page.exposeFunction("__MW_RECIPE_FIXTURE_APPLY__", async (source: RecipeFixtureProject, request: RecipeFixtureRequest) => {
    if (request.project_id !== source.id || request.generation !== source.generation || request.revision !== source.revision
        || request.effect_grant !== "synthetic-recipe-only" || request.change?.type !== "set_delivery_recipe") {
      throw new Error("Synthetic recipe fixture rejected a stale or unsupported authoring request.");
    }
    return applySyntheticFixtureChange(source, request.change);
  });
}

async function saveFixtureRecipe(page: RecipeFixturePage, group: "film" | "mix" = "film") {
  const revision = Number((await page.locator(".command-bar .revision-chip").innerText()).slice(1));
  const label = group === "film" ? "Save scene intent" : "Save original mix recipe";
  await page.getByRole("button", { name: label, exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (revision + 1));
  await expect(page.getByRole("region", { name: (group === "film" ? "Scene intent" : "Original mix") + " draft review", exact: true }).getByRole("status").first())
    .toContainText("Showing saved recipe values at revision r" + (revision + 1));
  return revision + 1;
}


test("sampled native PNG readback uses only a session grant and follows the editorial playhead", async ({ page }) => {
  // Synthetic Tauri transport. Real Semwright bytes/digests are checked in Rust and native E2E.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const master = boot.project.deliverables[0];
  const rendered = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision + 1,
    deliverable_id: master.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "mock-native-segment",
      scene_ids: [first.id, second.id],
      frame_count: 450,
      plan_ref: "mock-plan",
      fingerprint: "mock-digest",
      job_ref: "mock-job",
      artifact: { kind: "synthetic-only" },
      verification: { status: "synthetic-only" },
    }],
    preview: [{
      token: "mock-read-token",
      segment_id: "mock-native-segment",
      scene_ids: [first.id, second.id],
      frame_count: 450,
    }],
  };
  const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4XyX4HwAGkAKKXzfbLwAAAABJRU5ErkJggg==";
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initialBoot, receipt, fixturePng }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __SYNTHETIC_READS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initialBoot.project);
    runtime.__SYNTHETIC_READS__ = [];
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === project.id && recipeRequest.generation === project.generation
            && recipeRequest.revision === project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          project = await apply(project, recipeRequest);
          initialBoot.project = structuredClone(project);
          receipt.revision = project.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(project);
        }

        if (command === "production_runtime_status") return {
          status: "ready", reason: null, expected_version: "1.0.0",
          observed_version: "1.0.0", version_compatible: true,
          executable_sha256: "a".repeat(64), connection_identity: "b".repeat(64),
          pinned_revision: "c".repeat(40),
        };
        if (command === "motion_canvas_preflight") {
          const scope = args?.request as {
            project_id: string; generation: string; revision: number; deliverable_id: string;
          };
          return {
            project_resource: "project:" + scope.project_id,
            generation: scope.generation, revision: scope.revision,
            deliverable_id: scope.deliverable_id,
            verdict: "projection_ready", reason: null, segment_count: 1, total_frames: 60,
            segments: [{ segment_id: "synthetic-film-only", scene_ids: [], frame_count: 60 }],
          };
        }

        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, { ...initialBoot, project: structuredClone(project) });
        }
        if (command === "issue_effect_grant") return { token: "mock-grant" };
        if (command === "render_motion_canvas") return structuredClone(receipt);
        if (command === "preview_native_frame") {
          const request = args?.request as Record<string, unknown>;
          runtime.__SYNTHETIC_READS__.push(request);
          if (request.token !== "mock-read-token") throw new Error("Unrecognized preview grant");
          return Uint8Array.from(atob(fixturePng), (char) => char.charCodeAt(0));
        }
        if (command === "apply_change") {
          const request = args?.request as { change: { type: string; title?: string } };
          if (request.change.type !== "rename_project") throw new Error("Unsupported synthetic project edit");
          project = { ...project, title: request.change.title || "Edited", revision: project.revision + 1 };
          return structuredClone(project);
        }
        throw new Error("Unsupported synthetic Tauri command: " + command);
      },
    };
  }, { initialBoot: boot, receipt: rendered, fixturePng: png });

  await page.goto("/");
  const preview = page.getByRole("region", { name: "Program preview" });
  await expect(preview).toContainText("Design representation");
  await expect(page.getByRole("button", { name: "Show native frames" })).toHaveCount(0);

  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByRole("button", { name: "Show native frames" }).click();
  const frame = page.getByRole("region", { name: "Verified native PNG frame preview" });
  const image = frame.getByRole("img", { name: /Verified native rendered PNG/ });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBe(1);
  await expect(preview).toContainText("Verified native PNG · sampled frames");
  await expect(frame).toContainText("Settled exact source PNG · no synthesized frames");
  const revision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __SYNTHETIC_READS__: Array<{ frame_index: number }> }
  ).__SYNTHETIC_READS__.some((request) => request.frame_index === 1))).toBe(true);
  const requests = await page.evaluate(() => (
    window as unknown as { __SYNTHETIC_READS__: Array<Record<string, unknown>> }
  ).__SYNTHETIC_READS__);
  expect(requests.every((request) => request.project_id === boot.project.id
    && request.revision === boot.project.revision + 1
    && request.token === "mock-read-token"
    && !("path" in request)
    && !("sha256" in request))).toBe(true);
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.getByRole("button", { name: "Show design" }).click();
  await expect(frame).toHaveCount(0);
  await expect(preview).toContainText("Design representation");
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(page.getByRole("button", { name: "Show native frames" })).toHaveCount(0);

  await page.getByLabel("Editor preview timebase").selectOption(master.id);
  await page.getByRole("button", { name: "Show native frames" }).click();
  await expect(frame.getByRole("img")).toBeVisible();
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.locator("#project-title-rich").fill("New revision after native preview");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(preview).toContainText("STALE receipt");
  await expect(page.getByRole("region", { name: "Verified native PNG frame preview" })).toHaveCount(0);
});

test("invalid synthetic frame bytes display a truthful readback error and a retry", async ({ page }) => {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const receipt = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision + 1,
    deliverable_id: boot.project.deliverables[0].id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "bad-frame",
      scene_ids: [first.id],
      frame_count: 210,
      plan_ref: "mock",
      fingerprint: "mock",
      job_ref: "mock",
      artifact: {},
      verification: {},
    }],
    preview: [{ token: "bad-token", segment_id: "bad-frame", scene_ids: [first.id], frame_count: 210 }],
  };
  await installRecipeAuthoringFixture(page);
  await page.addInitScript(({ initialBoot, initialReceipt }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        // Authoring is counted separately from the native-only operations below.
        const recipeRequest = args?.request as RecipeFixtureRequest | undefined;
        if (command === "issue_effect_grant" && recipeRequest?.effect === "project_edit"
            && recipeRequest.project_id === initialBoot.project.id && recipeRequest.generation === initialBoot.project.generation
            && recipeRequest.revision === initialBoot.project.revision) return { token: "synthetic-recipe-only" };
        if (command === "apply_change" && recipeRequest?.change?.type === "set_delivery_recipe") {
          const apply = (window as unknown as { __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject> }).__MW_RECIPE_FIXTURE_APPLY__;
          initialBoot.project = await apply(initialBoot.project, recipeRequest);
          initialReceipt.revision = initialBoot.project.revision; // Future synthetic native reply uses the saved source.
          return structuredClone(initialBoot.project);
        }

        if (command === "production_runtime_status") return {
          status: "ready", reason: null, expected_version: "1.0.0",
          observed_version: "1.0.0", version_compatible: true,
          executable_sha256: "a".repeat(64), connection_identity: "b".repeat(64),
          pinned_revision: "c".repeat(40),
        };
        if (command === "motion_canvas_preflight") {
          const scope = args?.request as {
            project_id: string; generation: string; revision: number; deliverable_id: string;
          };
          return {
            project_resource: "project:" + scope.project_id,
            generation: scope.generation, revision: scope.revision,
            deliverable_id: scope.deliverable_id,
            verdict: "projection_ready", reason: null, segment_count: 1, total_frames: 60,
            segments: [{ segment_id: "synthetic-film-only", scene_ids: [], frame_count: 60 }],
          };
        }

        if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
          return window.__SYNTHETIC_PROJECT_READ__(command, args, structuredClone(initialBoot));
        }
        if (command === "issue_effect_grant") return { token: "mock-grant" };
        if (command === "render_motion_canvas") return structuredClone(initialReceipt);
        if (command === "preview_native_frame") return Uint8Array.from([1, 2, 3, 4, 5]);
        throw new Error("Unsupported mock operation: " + command);
      },
    };
  }, { initialBoot: boot, initialReceipt: receipt });

  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  for (const [scene, role, archetype] of [
    [first, "hook", "statement"],
    [second, "problem", "diagram_build"],
  ] as const) {
    await page.getByLabel(scene.name + " narrative role").selectOption(role);
    await page.getByLabel(scene.name + " motion archetype").selectOption(archetype);
  }
  await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Check Film projection" }).click();
  await expect(page.getByLabel("First native production readiness")).toContainText("ELIGIBLE FOR AUTHORIZED DISPATCH");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByRole("button", { name: "Show native frames" }).click();
  await expect(page.getByRole("alert")).toContainText("Native frame readback unavailable");
  await expect(page.getByRole("alert")).toContainText("valid PNG");
  await expect(page.getByRole("button", { name: "Retry this frame" })).toBeVisible();
});

// The following Window cases use an explicitly synthetic Tauri transport and
// one fixed 1x1 PNG. They observe browser state, request custody and decoding;
// they are not native renderer, SQLite, quality or master-delivery evidence.
type SyntheticWindowRequest = {
  project_id: string; generation: string; revision: number; request_id: string;
  effect_grant: string; reuse_token: string | null;
  window: { deliverable_id: string; source_scene_id: string;
    source_window: { start: number; end: number }; options: import("../src/types").MotionCanvasFilmOptions };
};
type SyntheticWindowTrace = { command: string; request: Record<string, unknown> };
interface SyntheticWindowHarness {
  project: RecipeFixtureProject;
  trace: SyntheticWindowTrace[];
  holdWindows: boolean;
  holdFrames: boolean;
  holdRenderGrant: boolean;
  rejectWarm: boolean;
  invalidDecode: boolean;
  pendingWindows: string[];
  pendingFrames: number;
  settledWindows: string[];
  recoveryWrites: number;
  pendingRenderGrants: number;
  settleWindow: (requestId: string, outcome: "resolve" | "reject") => void;
  releaseFrames: () => void;
  releaseRenderGrants: () => void;
}

async function installNativeWindowFixture(page: RecipeFixturePage) {
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const [first, preceding] = boot.project.scenes;
  boot.project.deliverables[0].included_scene_ids = [preceding.id, first.id];
  const png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4XyX4HwAGkAKKXzfbLwAAAABJRU5ErkJggg==";
  await installRecipeAuthoringFixture(page);
  await page.exposeFunction("__MW_WINDOW_FIXTURE_RENAME__", async (source: RecipeFixtureProject, request: RecipeFixtureRequest) => {
    if (request.project_id !== source.id || request.generation !== source.generation || request.revision !== source.revision
        || request.effect_grant !== "synthetic-recipe-only" || request.change?.type !== "rename_project") {
      throw new Error("Synthetic Window fixture rejected a stale or unsupported rename.");
    }
    return applySyntheticFixtureChange(source, request.change);
  });
  await page.addInitScript(({ initialBoot, fixturePng }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __MW_WINDOW_FIXTURE__: SyntheticWindowHarness;
      __MW_RECIPE_FIXTURE_APPLY__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject>;
      __MW_WINDOW_FIXTURE_RENAME__: (source: RecipeFixtureProject, request: RecipeFixtureRequest) => Promise<RecipeFixtureProject>;
    };
    const pending = new Map<string, { resolve: () => void; reject: () => void }>();
    const pendingFrames: Array<() => void> = [];
    const pendingRenderGrants: Array<() => void> = [];
    const issued = new Map<string, { request: SyntheticWindowRequest; count: number }>();
    let ordinal = 0;
    const state: SyntheticWindowHarness = {
      project: structuredClone(initialBoot.project), trace: [], holdWindows: false, holdFrames: false,
      holdRenderGrant: false, pendingRenderGrants: 0,
      rejectWarm: false, invalidDecode: false, pendingWindows: [], pendingFrames: 0, settledWindows: [], recoveryWrites: 0,
      settleWindow: (requestId, outcome) => {
        const job = pending.get(requestId);
        if (!job) throw new Error("No matching synthetic pending Window request.");
        pending.delete(requestId); state.pendingWindows = [...pending.keys()];
        state.settledWindows.push(requestId); job[outcome]();
      },
      releaseFrames: () => {
        const held = pendingFrames.splice(0);
        state.pendingFrames = 0;
        for (const release of held) release();
      },
      releaseRenderGrants: () => {
        const held = pendingRenderGrants.splice(0);
        state.pendingRenderGrants = 0;
        for (const release of held) release();
      },
    };
    runtime.__MW_WINDOW_FIXTURE__ = state;
    // Observe actual IndexedDB write attempts without substituting its results.
    // Recovery v1 stores pixels in IndexedDB, so inspecting localStorage alone
    // would not detect accidental Full-origin custody of a decoded Window PNG.
    const originalAdd = IDBObjectStore.prototype.add;
    const originalPut = IDBObjectStore.prototype.put;
    IDBObjectStore.prototype.add = function(value: unknown, key?: IDBValidKey) {
      if (this.name === "decoded-frames") state.recoveryWrites++;
      return key === undefined ? originalAdd.call(this, value) : originalAdd.call(this, value, key);
    };
    IDBObjectStore.prototype.put = function(value: unknown, key?: IDBValidKey) {
      if (this.name === "decoded-frames") state.recoveryWrites++;
      return key === undefined ? originalPut.call(this, value) : originalPut.call(this, value, key);
    };
    const same = (left: unknown, right: unknown) => JSON.stringify(left) === JSON.stringify(right);
    const current = (request: { project_id?: unknown; generation?: unknown; revision?: unknown }) =>
      request.project_id === state.project.id && request.generation === state.project.generation && request.revision === state.project.revision;
    const bytes = () => {
      if (!state.invalidDecode) return Uint8Array.from(atob(fixturePng), char => char.charCodeAt(0));
      // Both API and bounded-PNG guards admit this signature and length. The
      // actual browser decoder rejects the missing PNG chunks, invoking onError.
      const malformed = new Uint8Array(40);
      malformed.set([137, 80, 78, 71, 13, 10, 26, 10]);
      return malformed;
    };
    runtime.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
      const request = (args?.request ?? {}) as Record<string, unknown>;
      state.trace.push({ command, request: structuredClone(request) });
      if (command === "bootstrap" || command === "project_projection_head" || command === "open_project") {
        return window.__SYNTHETIC_PROJECT_READ__(command, args, { ...initialBoot, project: structuredClone(state.project) });
      }
      if (command === "issue_effect_grant") {
        if (!current(request)) throw new Error("Synthetic Window grant requires the current saved source.");
        if (request.effect === "project_edit") return { token: "synthetic-recipe-only" };
        if (request.effect === "render_local") {
          if (state.holdRenderGrant) return new Promise(resolve => {
            pendingRenderGrants.push(() => resolve({ token: "synthetic-window-only" }));
            state.pendingRenderGrants = pendingRenderGrants.length;
          });
          return { token: "synthetic-window-only" };
        }
        throw new Error("No synthetic master or delivery grant is available.");
      }
      if (command === "apply_change") {
        const change = request as unknown as RecipeFixtureRequest;
        const apply = change.change?.type === "set_delivery_recipe" ? runtime.__MW_RECIPE_FIXTURE_APPLY__ : runtime.__MW_WINDOW_FIXTURE_RENAME__;
        state.project = await apply(state.project, change);
        const active = state.project.branches.find(branch => branch.id === state.project.active_branch);
        if (active?.head_revision !== state.project.revision) throw new Error("Synthetic authoring lost the active branch head.");
        return structuredClone(state.project);
      }
      if (command === "production_runtime_status") return {
        status: "ready", reason: null, expected_version: "1.0.0", observed_version: "1.0.0", version_compatible: true,
        executable_sha256: "a".repeat(64), connection_identity: "b".repeat(64), pinned_revision: "c".repeat(40),
      };
      if (command === "motion_canvas_preflight") return {
        project_resource: "project:" + state.project.id, generation: state.project.generation, revision: state.project.revision,
        deliverable_id: request.deliverable_id, verdict: "projection_ready", reason: null, segment_count: 1, total_frames: 480,
        segments: [{ segment_id: "synthetic-window-run", scene_ids: state.project.scenes.slice(0, 2).map(scene => scene.id), frame_count: 480 }],
      };
      if (command === "render_motion_canvas_window") {
        const original = structuredClone(request) as unknown as SyntheticWindowRequest;
        if (!current(original) || original.effect_grant !== "synthetic-window-only"
            || original.window.source_scene_id !== initialBoot.project.scenes[0].id
            || original.window.deliverable_id !== initialBoot.project.deliverables[0].id) {
          throw new Error("Synthetic Window dispatch has a foreign scope.");
        }
        const source = original.window.source_window;
        if (!Number.isInteger(source.start) || !Number.isInteger(source.end) || source.start < 0 || source.end > 210 || source.start >= source.end) {
          throw new Error("Synthetic Window source must lie in its original first scene.");
        }
        const warm = original.reuse_token !== null;
        const prior = warm ? issued.get(original.reuse_token!) : null;
        if (warm && (state.rejectWarm || !prior || prior.request.project_id !== original.project_id
            || prior.request.generation !== original.generation || prior.request.revision !== original.revision
            || !same(prior.request.window, original.window))) {
          throw new Error("Synthetic original window payload is unavailable; no replacement is rendered.");
        }
        const count = source.end - source.start;
        const token = original.reuse_token ?? "cb060ec7-a16b-4f38-bae2-" + String(++ordinal).padStart(12, "0");
        // The original second scene lasts 9 seconds. The selected cut is B,A;
        // source A[1,3) therefore maps to segment[271,273), token-local[0,2).
        const segment = { start: 270 + source.start, end: 270 + source.end };
        const response: import("../src/types").NativeWindowRenderResponse = {
          window: {
            generation: original.generation, revision: original.revision, deliverable_id: original.window.deliverable_id,
            source_scene_id: original.window.source_scene_id, source_window: structuredClone(source), segment_window: segment,
            original_prefix: { start: 0, end: segment.end }, frame_rate: structuredClone(original.window.options.frame_rate),
            preview_only: true, master_eligible: false,
            validation: { verdict: "UNKNOWN", native_frame_coverage: "UNKNOWN", pass_checks: 3, unknown_checks: 1, unknown_findings: 0 },
            cost: { operation: warm ? "explicit_original_reuse" : "native_prefix", original_prefix_pngs: segment.end,
              selected_pngs: count, reused_original_pngs: warm ? count : 0, start_calls_observed: warm ? 0 : 1,
              cached_png_bytes_returned: warm ? count * 70 : 0, cache_payload_bytes_admitted: warm ? 0 : count * 70,
              cache_micros: 10, internal_simulation_steps: null, internal_simulation_observation: "not_observed" },
          },
          preview: { token, segment_id: "synthetic-window-run", scene_ids: [original.window.source_scene_id], frame_count: count },
        };
        const result = () => { issued.set(token, { request: original, count }); return structuredClone(response); };
        if (!warm && state.holdWindows) return new Promise((resolve, reject) => {
          pending.set(original.request_id, { resolve: () => resolve(result()), reject: () => reject(new Error("Synthetic late original outcome is unknown.")) });
          state.pendingWindows = [...pending.keys()];
        });
        return result();
      }
      if (command === "preview_native_frame") {
        const grant = issued.get(String(request.token));
        if (!grant || !current(request) || grant.request.revision !== request.revision
            || !Number.isInteger(request.frame_index) || Number(request.frame_index) < 0 || Number(request.frame_index) >= grant.count
            || Object.keys(request).sort().join(",") !== "frame_index,generation,project_id,revision,token") {
          throw new Error("Synthetic PNG read requires only its current grant and token-local frame.");
        }
        if (state.holdFrames) return new Promise(resolve => {
          pendingFrames.push(() => resolve(bytes())); state.pendingFrames = pendingFrames.length;
        });
        return bytes();
      }
      throw new Error("Unsupported synthetic Window command; no Full/master fallback: " + command);
    } };
  }, { initialBoot: boot, fixturePng: png });
  return boot;
}

async function prepareWindowThroughUi(page: RecipeFixturePage, boot: typeof fixtureBootstrap) {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const [first, preceding] = boot.project.scenes;
  for (const [scene, role, archetype] of [[first, "hook", "statement"], [preceding, "problem", "diagram_build"]] as const) {
    await page.getByLabel(scene.name + " narrative role").selectOption(role);
    await page.getByLabel(scene.name + " motion archetype").selectOption(archetype);
  }
  const revision = await saveFixtureRecipe(page);
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByText("Native window preview", { exact: true }).click();
  await page.getByLabel("Source window start frame", { exact: true }).fill("1");
  await page.getByLabel("Source window end frame (exclusive)", { exact: true }).fill("3");
  return revision;
}

async function nativeWindowTrace(page: RecipeFixturePage, command: string) {
  return page.evaluate(name => (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness })
    .__MW_WINDOW_FIXTURE__.trace.filter(call => call.command === name).map(call => call.request), command);
}

async function expectNoWindowPromotion(page: RecipeFixturePage) {
  const observations = await page.evaluate(async () => {
    const state = (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__;
    let retained = 0;
    const databases = await indexedDB.databases();
    if (databases.some(database => database.name === "motionwright.preview-recovery.v1")) {
      retained = await new Promise<number>((resolve, reject) => {
        const opening = indexedDB.open("motionwright.preview-recovery.v1");
        opening.onerror = () => reject(opening.error);
        opening.onsuccess = () => {
          const db = opening.result;
          if (!db.objectStoreNames.contains("decoded-frames")) { db.close(); reject(new Error("Recovery database lacks its store.")); return; }
          const tx = db.transaction("decoded-frames", "readonly");
          const count = tx.objectStore("decoded-frames").count();
          tx.oncomplete = () => { db.close(); resolve(count.result); };
          tx.onabort = tx.onerror = () => { db.close(); reject(tx.error); };
        };
      });
    }
    return {
      recoveryWrites: state.recoveryWrites, retained,
      forbidden: state.trace.filter(call => call.command === "render_motion_canvas" || call.command.includes("master")
        || call.command === "issue_effect_grant" && call.request.effect === "deliver_local"),
    };
  });
  expect(observations).toEqual({ recoveryWrites: 0, retained: 0, forbidden: [] });
  await expect(page.getByRole("button", { name: "Show native frames", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Review final AV", exact: true })).toHaveCount(0);
}

test("Window PNG opt-in keeps source, reordered cut and local indices distinct without promoting recovery or master authority", async ({ page }, info) => {
  await page.setViewportSize({ width: 720, height: 900 });
  const boot = await installNativeWindowFixture(page);
  const revision = await prepareWindowThroughUi(page, boot);
  const startInput = page.getByLabel("Source window start frame", { exact: true });
  const endInput = page.getByLabel("Source window end frame (exclusive)", { exact: true });
  await startInput.focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("1e");
  await expect(startInput).toHaveValue("1e");
  await expect(page.getByRole("button", { name: "Capture native window", exact: true })).toBeDisabled();
  expect(await nativeWindowTrace(page, "render_motion_canvas_window")).toEqual([]);
  expect((await nativeWindowTrace(page, "issue_effect_grant")).filter(request => request.effect === "render_local")).toEqual([]);
  await page.keyboard.press("Tab");
  await expect(endInput).toBeFocused();
  await startInput.fill("0");
  await endInput.fill("30");
  await expect(page.getByRole("button", { name: "Capture native window", exact: true })).toBeEnabled();
  // The clock oracle below deliberately narrows the valid original default.
  await startInput.fill("1");
  await endInput.fill("3");
  await expect(page.getByLabel("Selected native window coordinates")).toContainText("Source frames 1 to 3 (end excluded); selected cut");
  const layout = await page.getByRole("region", { name: "Native window controls", exact: true }).evaluate(element => {
    const bounds = element.getBoundingClientRect();
    return {
      evidenceScope: "synthetic browser controls geometry only; no native render observation",
      viewport: { width: innerWidth, documentClient: document.documentElement.clientWidth, documentScroll: document.documentElement.scrollWidth },
      controls: { left: bounds.left, right: bounds.right, width: bounds.width, client: element.clientWidth,
        scroll: element.scrollWidth, overflowX: getComputedStyle(element).overflowX },
      fields: [...element.querySelectorAll("input")].map(input => ({ label: input.getAttribute("aria-label"),
        value: input.value, left: input.getBoundingClientRect().left, right: input.getBoundingClientRect().right })),
    };
  });
  expect(layout.viewport.width).toBe(720);
  expect(layout.controls.client).toBeGreaterThan(0);
  expect(layout.controls.scroll).toBeLessThanOrEqual(layout.controls.client + 1);
  expect(["hidden", "clip"]).not.toContain(layout.controls.overflowX);
  await info.attach("native-window-reflow-720", { body: Buffer.from(JSON.stringify(layout, null, 2)), contentType: "application/json" });
  await page.getByRole("button", { name: "Capture native window", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Native window evidence")).toContainText("WINDOW · UNKNOWN · PREVIEW ONLY · NOT A MASTER");
  await page.getByText("Capture details", { exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Exact selected-cut seconds [271/30, 91/10).", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Native window evidence")).toContainText("segment [271, 273)");
  expect(await nativeWindowTrace(page, "preview_native_frame")).toEqual([]);
  await expect(page.getByRole("button", { name: "Show window frames", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Step forward one frame", exact: true }).click();
  await page.getByRole("button", { name: "Show window frames", exact: true }).click();
  const frame = page.getByRole("region", { name: "Native window PNG frame preview", exact: true });
  await expect.poll(() => frame.getByRole("img").evaluate(image => (image as HTMLImageElement).naturalWidth)).toBe(1);
  await expect(frame).toContainText("Source frame 1; segment frame 271");
  expect((await nativeWindowTrace(page, "preview_native_frame")).map(request => request.frame_index)).toEqual([0]);

  // A later PNG read cannot revive a consumed opt-in after profile A -> B -> A.
  await page.evaluate(() => { (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.holdFrames = true; });
  await page.getByRole("button", { name: "Step forward one frame", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.pendingFrames)).toBe(1);
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[0].id);
  await page.evaluate(() => {
    const state = (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__;
    state.holdFrames = false; state.releaseFrames();
  });
  await expect(frame).toHaveCount(0);
  expect((await nativeWindowTrace(page, "preview_native_frame")).map(request => request.frame_index)).toEqual([0, 1]);
  await page.getByRole("button", { name: "Show window frames", exact: true }).click();
  await expect.poll(() => frame.getByRole("img").evaluate(image => (image as HTMLImageElement).naturalWidth)).toBe(1);
  await expect(frame).toContainText("Source frame 2; segment frame 272");
  const readCount = (await nativeWindowTrace(page, "preview_native_frame")).length;
  await page.getByRole("button", { name: "Step forward one frame", exact: true }).click();
  await expect(frame).toHaveCount(0);
  await expect(page.getByLabel("Native window evidence")).toContainText("outside this window");
  expect((await nativeWindowTrace(page, "preview_native_frame")).length).toBe(readCount);
  for (const request of await nativeWindowTrace(page, "preview_native_frame")) {
    expect(request).toMatchObject({ project_id: boot.project.id, generation: boot.project.generation, revision });
    expect(Object.keys(request).sort()).toEqual(["frame_index", "generation", "project_id", "revision", "token"]);
  }
  await expectNoWindowPromotion(page);
});

test("a late Window outcome cannot clear a newer pending intent or overwrite later incomplete input", async ({ page }) => {
  const boot = await installNativeWindowFixture(page);
  const revision = await prepareWindowThroughUi(page, boot);
  await page.evaluate(() => { (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.holdWindows = true; });
  await page.getByRole("button", { name: "Capture native window", exact: true }).click();
  await expect.poll(async () => (await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(1);
  const earlier = (await nativeWindowTrace(page, "render_motion_canvas_window"))[0] as unknown as SyntheticWindowRequest;
  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.locator("#project-title-rich").fill("Explicit revision after the pending Window capture");
  await page.getByRole("button", { name: "Save title", exact: true }).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + (revision + 1));
  const nextSource = await page.evaluate(() => (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.project);
  expect(nextSource.branches.find(branch => branch.id === nextSource.active_branch)?.head_revision).toBe(revision + 1);
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByText("Native window preview", { exact: true }).click();
  await page.getByLabel("Source window start frame", { exact: true }).fill("2");
  await page.getByLabel("Source window end frame (exclusive)", { exact: true }).fill("4");
  await page.getByRole("button", { name: "Capture native window", exact: true }).click();
  await expect.poll(async () => (await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(2);
  const later = (await nativeWindowTrace(page, "render_motion_canvas_window"))[1] as unknown as SyntheticWindowRequest;
  expect(later.request_id).not.toBe(earlier.request_id);
  expect(later.revision).toBe(revision + 1);
  await page.getByLabel("Source window start frame", { exact: true }).focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("2e");
  await page.evaluate(async id => {
    (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.settleWindow(id, "reject");
    // Let the rejected RPC's chained handlers and React commit finish before
    // checking that the *newer* waiting state remains, rather than sampling it
    // while the old rejection is merely queued.
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  }, earlier.request_id);
  const controls = page.getByRole("region", { name: "Native window controls", exact: true });
  await expect(controls).toContainText("Waiting for the original window request. No native request will be retried.");
  await expect(controls).not.toContainText(earlier.request_id);
  await expect(page.getByLabel("Source window start frame", { exact: true })).toHaveValue("2e");
  await expect(page.getByLabel("Native window evidence")).toHaveCount(0);
  expect((await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(2);
  expect(await nativeWindowTrace(page, "preview_native_frame")).toEqual([]);
  // Finish the still-owned intent; the edit made after dispatch remains raw and
  // its now-stale response cannot silently supply current pixels.
  await page.evaluate(id => (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.settleWindow(id, "resolve"), later.request_id);
  await expect(controls).not.toContainText("Waiting for the original window request.");
  await expect(page.getByLabel("Source window start frame", { exact: true })).toHaveValue("2e");
  await expect(page.getByLabel("Native window evidence")).toHaveCount(0);
  expect((await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(2);
  // A grant is not dispatch authority after selection changes during its await.
  // The original intent remains distinct from the raw edit made while waiting.
  await page.getByLabel("Source window start frame", { exact: true }).fill("3");
  await page.getByLabel("Source window end frame (exclusive)", { exact: true }).fill("5");
  await page.evaluate(() => { (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.holdRenderGrant = true; });
  await page.getByRole("button", { name: "Capture native window", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.pendingRenderGrants)).toBe(1);
  await page.getByLabel("Source window start frame", { exact: true }).fill("3e");
  await page.evaluate(() => {
    const state = (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__;
    state.holdRenderGrant = false; state.releaseRenderGrants();
  });
  await expect(controls).toContainText("Window selection changed while preparing its grant; no native command was dispatched.");
  await expect(controls).not.toContainText("Capture outcome UNKNOWN");
  await expect(page.getByLabel("Source window start frame", { exact: true })).toHaveValue("3e");
  expect((await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(2);
  await expectNoWindowPromotion(page);
});

test("explicit Window reuse failure never dispatches a cold replacement or promotes its token", async ({ page }) => {
  const boot = await installNativeWindowFixture(page);
  await prepareWindowThroughUi(page, boot);
  await page.getByRole("button", { name: "Capture native window", exact: true }).click();
  await expect(page.getByLabel("Native window evidence")).toContainText("PREVIEW ONLY");
  await page.evaluate(() => { (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.rejectWarm = true; });
  await page.getByRole("button", { name: "Reuse original window", exact: true }).click();
  await expect(page.getByRole("region", { name: "Native window controls", exact: true })).toContainText("Original window reuse failed; no fallback capture was dispatched.");
  const requests = await nativeWindowTrace(page, "render_motion_canvas_window") as unknown as SyntheticWindowRequest[];
  expect(requests).toHaveLength(2);
  expect(requests[0].reuse_token).toBeNull();
  expect(requests[1].reuse_token).toBe("cb060ec7-a16b-4f38-bae2-000000000001");
  expect(requests[1].request_id).not.toBe(requests[0].request_id);
  expect(requests[1].window).toEqual(requests[0].window);
  expect(requests[1].revision).toBe(requests[0].revision);
  await page.getByLabel("Source window start frame", { exact: true }).fill("2");
  await expect(page.getByRole("button", { name: "Reuse original window", exact: true })).toBeDisabled();
  expect((await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(2);
  expect(await nativeWindowTrace(page, "preview_native_frame")).toEqual([]);
  await expectNoWindowPromotion(page);
});

test("Window PNG browser decode failure retries only the selected frame on an explicit gesture", async ({ page }) => {
  const boot = await installNativeWindowFixture(page);
  await prepareWindowThroughUi(page, boot);
  await page.evaluate(() => { (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.invalidDecode = true; });
  await page.getByRole("button", { name: "Capture native window", exact: true }).click();
  await expect(page.getByLabel("Native window evidence")).toContainText("PREVIEW ONLY");
  await page.getByRole("button", { name: "Step forward one frame", exact: true }).click();
  await page.getByRole("button", { name: "Show window frames", exact: true }).click();
  const frame = page.getByRole("region", { name: "Native window PNG frame preview", exact: true });
  await expect(frame.getByRole("alert")).toContainText("The decoded PNG cannot be displayed by this WebView.");
  await expect(frame.getByRole("img")).toHaveCount(0);
  const before = await nativeWindowTrace(page, "preview_native_frame");
  expect(before).toHaveLength(1);
  expect(before[0].frame_index).toBe(0);
  await page.evaluate(() => { (window as unknown as { __MW_WINDOW_FIXTURE__: SyntheticWindowHarness }).__MW_WINDOW_FIXTURE__.invalidDecode = false; });
  await frame.getByRole("button", { name: "Retry this frame", exact: true }).click();
  await expect.poll(() => frame.getByRole("img").evaluate(image => (image as HTMLImageElement).naturalWidth)).toBe(1);
  expect(await nativeWindowTrace(page, "preview_native_frame")).toEqual([before[0], before[0]]);
  expect((await nativeWindowTrace(page, "render_motion_canvas_window")).length).toBe(1);
  await expectNoWindowPromotion(page);
});
