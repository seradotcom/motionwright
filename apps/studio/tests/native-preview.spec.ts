import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

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
    revision: boot.project.revision,
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
  await page.addInitScript(({ initialBoot, receipt, fixturePng }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown> };
      __SYNTHETIC_READS__: Array<Record<string, unknown>>;
    };
    let project = structuredClone(initialBoot.project);
    runtime.__SYNTHETIC_READS__ = [];
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command, args) => {
        if (command === "bootstrap") return { ...initialBoot, project: structuredClone(project) };
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
  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByRole("button", { name: "Show native frames" }).click();
  const frame = page.getByRole("region", { name: "Verified native PNG frame preview" });
  const image = frame.getByRole("img", { name: /Verified native rendered PNG/ });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBe(1);
  await expect(preview).toContainText("Verified native PNG · sampled frames");
  await expect(frame).toContainText("SHA-256 checked · sampled PNG, not real-time playback");
  const revision = await page.locator(".revision-chip").first().innerText();

  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect.poll(async () => page.evaluate(() => (
    window as unknown as { __SYNTHETIC_READS__: Array<{ frame_index: number }> }
  ).__SYNTHETIC_READS__.some((request) => request.frame_index === 1))).toBe(true);
  const requests = await page.evaluate(() => (
    window as unknown as { __SYNTHETIC_READS__: Array<Record<string, unknown>> }
  ).__SYNTHETIC_READS__);
  expect(requests.every((request) => request.project_id === boot.project.id
    && request.revision === boot.project.revision
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
  await page.getByLabel("Project title").fill("New revision after native preview");
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
    revision: boot.project.revision,
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
  await page.addInitScript(({ initialBoot, initialReceipt }) => {
    const runtime = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (command: string) => Promise<unknown> };
    };
    runtime.__TAURI_INTERNALS__ = {
      invoke: async (command) => {
        if (command === "bootstrap") return structuredClone(initialBoot);
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
  await page.getByRole("button", { name: "Render native segments" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await page.getByRole("button", { name: "Show native frames" }).click();
  await expect(page.getByRole("alert")).toContainText("Native frame readback unavailable");
  await expect(page.getByRole("alert")).toContainText("valid PNG");
  await expect(page.getByRole("button", { name: "Retry frame" })).toBeVisible();
});
