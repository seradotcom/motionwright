import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("a sampled native frame becomes a user-authored scoped repair, never an automatic edit", async ({ page }) => {
  // Synthetic Tauri transport only. Real Semwright frames and digests are
  // validated in separate source-pinned native E2E workflows.
  const boot = structuredClone(fixtureBootstrap);
  boot.native_sdk.mode = "tauri";
  const first = boot.project.scenes[0];
  const second = boot.project.scenes[1];
  const profile = boot.project.deliverables[0];
  const target = first.nodes.find(node => node.kind === "text");
  expect(target).toBeTruthy();

  const receipt = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: profile.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "synthetic-review-segment",
      scene_ids: [first.id, second.id],
      frame_count: 450,
      plan_ref: "synthetic-only", fingerprint: "synthetic-only",
      job_ref: "synthetic-only", artifact: {}, verification: {},
    }],
    preview: [{
      token: "synthetic-review-token", segment_id: "synthetic-review-segment",
      scene_ids: [first.id, second.id], frame_count: 450,
    }],
  };

  await page.addInitScript(({ initialBoot, nativeReceipt, nativeWidth, nativeHeight }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __REVIEW_TEST_COMMANDS__: string[];
    };
    runtime.__REVIEW_TEST_COMMANDS__ = [];
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        runtime.__REVIEW_TEST_COMMANDS__.push(command);
        if (command === "bootstrap") return structuredClone(initialBoot);
        if (command === "issue_effect_grant") return { token: "synthetic-test-grant" };
        if (command === "render_motion_canvas") return structuredClone(nativeReceipt);
        if (command === "preview_native_frame") {
          const request = args?.request as { token: string };
          if (request.token !== "synthetic-review-token") throw new Error("Unknown synthetic grant");
          // The fixture matches the declared output dimensions, but it is not
          // a renderer artifact or a source of product evidence.
          const canvas = document.createElement("canvas");
          canvas.width = nativeWidth; canvas.height = nativeHeight;
          const ctx = canvas.getContext("2d");
          if (!ctx) throw new Error("Browser fixture canvas unavailable");
          ctx.fillStyle = "#283441";
          ctx.fillRect(0, 0, nativeWidth, nativeHeight);
          const encoded = canvas.toDataURL("image/png").split(",")[1];
          return Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
        }
        throw new Error("Unexpected synthetic native operation: " + command);
      },
    };
  }, { initialBoot: boot, nativeReceipt: receipt, nativeWidth: profile.width, nativeHeight: profile.height });

  await page.goto("/");
  const revision = await page.locator(".revision-chip").innerText();
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Production", exact: true }).click();
  await page.getByRole("button", { name: "Native inspection", exact: true }).click();
  await page.getByRole("button", { name: "Inspect current native frames" }).click();
  await expect(page.getByRole("button", { name: /Inspect native frame 0/ })).toBeVisible();
  await page.getByLabel("Affected object").selectOption(target!.id);
  await page.getByLabel("Violated constraint").fill("Legibility and continuity of the selected text");
  await page.getByLabel("What is visible in this frame").fill("The current entry visually overlaps a neighboring element.");
  await page.getByLabel("Proposed localized repair").fill("Move the selected text only after human preview.");

  const prepare = page.getByRole("button", { name: "Draft scoped repair" });
  await expect(prepare).toBeEnabled();
  await prepare.click();
  await expect(page.getByRole("heading", { name: "Scoped changes", exact: true })).toBeVisible();
  await expect(page.getByLabel("Patch target object")).toHaveValue(target!.id);
  await expect(page.getByLabel("Patch target object")).toBeDisabled();
  await expect(page.getByText(/Native frame 0 · source r/)).toBeVisible();
  const rationale = page.getByLabel("Patch rationale");
  await expect(rationale).toHaveValue(/Manual native-frame observation; repair NOT verified/);
  await expect(rationale).toHaveValue(/Legibility and continuity/);
  await expect(rationale).toHaveValue(/PNG SHA-256 /);
  await expect(page.locator(".revision-chip")).toHaveText(revision);

  const add = page.getByRole("button", { name: "Add to proposal" });
  await expect(add).toBeDisabled();
  await page.getByLabel("Patch replacement text").fill("Human-entered replacement text");
  await expect(add).toBeEnabled();
  await add.click();
  await expect(page.getByRole("region", { name: "Proposed edits" })).toContainText(target!.name);
  await expect(page.locator(".revision-chip")).toHaveText(revision);

  const commands = await page.evaluate(() => (
    window as unknown as { __REVIEW_TEST_COMMANDS__: string[] }
  ).__REVIEW_TEST_COMMANDS__);
  expect(commands).not.toContain("apply_change");
  expect(commands).not.toContain("preview_creative_patch");
  await page.getByRole("button", { name: "Discard proposal" }).click();
  await expect(page.getByText(/Native frame 0 · source r/)).toHaveCount(0);
  await expect(page.locator(".revision-chip")).toHaveText(revision);
});
