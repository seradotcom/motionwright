import { expect, test } from "@playwright/test";
import { fixtureBootstrap } from "../src/fixture";

test("synthetic native receipt remains scene/revision scoped across Deliver and Timeline", async ({ page }) => {
  // This is a UI wiring test, NOT evidence that Semwright actually rendered frames.
  const boot = {
    ...fixtureBootstrap,
    native_sdk: { ...fixtureBootstrap.native_sdk, mode: "tauri" },
  };
  const first = boot.project.scenes[0];
  const output = boot.project.deliverables[0];
  const receipt = {
    project_resource: "project:" + boot.project.id,
    generation: boot.project.generation,
    revision: boot.project.revision,
    deliverable_id: output.id,
    frame_rate: { num: 30, den: 1 },
    segments: [{
      segment_id: "synthetic-segment",
      scene_ids: [first.id],
      frame_count: 210,
      plan_ref: "synthetic-plan",
      fingerprint: "synthetic-fingerprint",
      job_ref: "synthetic-job",
      artifact: { kind: "synthetic-test-only" },
      verification: { kind: "synthetic-test-only" },
    }],
  };

  await page.addInitScript(({ initialBoot, initialReceipt }) => {
    const mock = window as unknown as {
      __TAURI_INTERNALS__: { invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown> };
    };
    let savedProject = structuredClone(initialBoot.project);
    mock.__TAURI_INTERNALS__ = {
      invoke: async (cmd, args) => {
        if (cmd === "bootstrap") return { ...initialBoot, project: structuredClone(savedProject) };
        if (cmd === "issue_effect_grant") return { token: "synthetic-grant" };
        if (cmd === "render_motion_canvas") return structuredClone(initialReceipt);
        if (cmd === "apply_change") {
          const request = args?.request as { change?: { type?: string; title?: string } } | undefined;
          const change = request?.change;
          if (change?.type !== "rename_project" || !change.title) {
            throw new Error("Unsupported synthetic creative edit");
          }
          savedProject = { ...savedProject, title: change.title, revision: savedProject.revision + 1 };
          return structuredClone(savedProject);
        }
        throw new Error("Unsupported synthetic Tauri command: " + cmd);
      },
    };
  }, { initialBoot: boot, initialReceipt: receipt });

  await page.goto("/");
  const preview = page.getByRole("region", { name: "Program preview" });
  await expect(preview).toContainText("No render evidence attached");

  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByLabel(first.name + " narrative role").selectOption("hook");
  await page.getByLabel(first.name + " motion archetype").selectOption("statement");
  const second = boot.project.scenes[1];
  await page.getByLabel(second.name + " narrative role").selectOption("problem");
  await page.getByLabel(second.name + " motion archetype").selectOption("diagram_build");

  await page.getByRole("button", { name: "Render native segments" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(preview).toContainText("Native segment receipt · 210 frames · r" + boot.project.revision + " · metadata only");
  await expect(preview).toContainText("Design representation");

  await page.locator(".tree-row").filter({ hasText: "Semwright acts natively" }).click();
  await expect(preview).toContainText("No render evidence attached");
  await page.locator(".tree-row").filter({ hasText: first.name }).click();
  await page.getByLabel("Editor preview timebase").selectOption(boot.project.deliverables[1].id);
  await expect(preview).toContainText("Receipt for " + output.name + " · different display profile");

  await page.getByRole("button", { name: "Inspect receipt in Deliver" }).click();
  await expect(page.getByLabel("Native render evidence")).toContainText("NATIVE · PASS");

  await page.getByRole("button", { name: "Brief", exact: true }).click();
  await page.getByLabel("Project title").fill("Post-render revision");
  await page.getByRole("button", { name: "Save title" }).click();
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(preview).toContainText("STALE receipt r" + boot.project.revision);
  await expect(preview).toContainText("project is r" + (boot.project.revision + 1));
});
