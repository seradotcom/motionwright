import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("Canvas issues exactly one source-scoped native linear move and preserves base pose", async ({ page }) => {
  // Synthetic Tauri bridge: authoring UX/IPC only. Native output geometry,
  // Semwright rendering and atomic Rust semantics are tested separately.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const scene = boot.project.scenes[0];
  scene.beats = [];
  const node = scene.nodes[0];
  const profile = boot.project.deliverables[0];

  await page.addInitScript(({ initialBoot }) => {
    const mock = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __NATIVE_AUTHORING_REQUESTS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initialBoot.project);
    mock.__NATIVE_AUTHORING_REQUESTS__ = [];
    mock.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return { ...initialBoot, project: structuredClone(project) };
        if (command === "issue_effect_grant") return { token: "synthetic-only-authority" };
        if (command === "apply_change") {
          const request = args?.request as {
            project_id: string; generation: string; revision: number;
            effect_grant: string;
            change: { type: string; scene_id: string; node_id: string;
              deliverable_id: string; start_x: number; start_y: number; end_frame: number };
          };
          if (request.change.type !== "set_canvas_linear_position_motion") {
            throw Error("Unexpected synthetic authoring operation");
          }
          mock.__NATIVE_AUTHORING_REQUESTS__.push(request as unknown as Record<string, unknown>);
          if (request.revision !== project.revision ||
              request.generation !== project.generation) throw Error("Stale creative revision");
          const sourceScene = project.scenes.find((entry) => entry.id === request.change.scene_id);
          const source = sourceScene?.nodes.find((entry) => entry.id === request.change.node_id);
          if (!source || source.keyframes.length || sourceScene?.beats.length) {
            throw Error("Native move must never overwrite existing motion or authored beats");
          }
          source.keyframes = [
            { at: { num: "0", den: "1" }, property: "x", value: request.change.start_x, interpolation: "linear" },
            { at: { num: "0", den: "1" }, property: "y", value: request.change.start_y, interpolation: "linear" },
            { at: { num: "1", den: "1" }, property: "x", value: source.x, interpolation: "linear" },
            { at: { num: "1", den: "1" }, property: "y", value: source.y, interpolation: "linear" },
          ];
          project.revision += 1;
          return structuredClone(project);
        }
        throw Error("Synthetic authoring test cannot invoke " + command);
      },
    };
  }, { initialBoot: boot });

  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const native = page.getByRole("region", { name: "Native linear position motion" });
  const action = native.getByRole("button", { name: "Create native linear move" });
  const beforeRevision = await page.locator(".revision-chip").first().innerText();
  const beforeX = await page.getByLabel("Canvas X").inputValue();
  const beforeY = await page.getByLabel("Canvas Y").inputValue();

  await expect(native).toContainText(profile.name);
  await expect(action).toBeEnabled();
  await page.getByLabel("Native move start X").fill(String(node.x - 80));
  await page.getByLabel("Native move start Y").fill(String(node.y + 25));
  await page.getByLabel("Native move end frame").fill("0");
  await expect(action).toBeDisabled();
  await page.getByLabel("Native move end frame").fill("500");
  await expect(action).toBeDisabled();
  await page.getByLabel("Native move end frame").fill("30");
  await expect(action).toBeEnabled();
  await action.click();

  await expect(page.locator(".motion-keyframe-row")).toHaveCount(4);
  await expect(page.getByLabel("Canvas X")).toHaveValue(beforeX);
  await expect(page.getByLabel("Canvas Y")).toHaveValue(beforeY);
  await expect(page.locator(".revision-chip").first()).not.toHaveText(beforeRevision);
  await expect(action).toBeDisabled();
  const requests = await page.evaluate(() => (
    window as unknown as { __NATIVE_AUTHORING_REQUESTS__: Array<Record<string, unknown>> }
  ).__NATIVE_AUTHORING_REQUESTS__);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({
    project_id: boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    change: {
      type: "set_canvas_linear_position_motion",
      scene_id: scene.id,
      node_id: node.id,
      deliverable_id: profile.id,
      start_x: node.x - 80,
      start_y: node.y + 25,
      end_frame: 30,
    },
  });
  expect(requests[0]).not.toHaveProperty("path");
  expect(requests[0]).not.toHaveProperty("render");
});

test("native move UI blocks beat-constrained scenes and never claims native render in browser demo", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const panel = page.getByRole("region", { name: "Native linear position motion" });
  await expect(panel.getByRole("button", { name: "Create native linear move" })).toBeDisabled();
  await expect(panel).toContainText("Film preflight still verifies native framing");
  await expect(page.getByLabel("Auto-key position")).not.toBeChecked();
});
