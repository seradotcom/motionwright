import { expect, type Page } from "@playwright/test";
import type { CanvasKeyframe, CanvasNode, Project } from "../../src/types";

export const proposalButton = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
export async function openPaintMotionProposal(page: Page, sceneId: string) {
  await proposalButton(page, "Production").click();
  await page.getByLabel("Production working scene").selectOption(sceneId);
  await proposalButton(page, "Scoped changes").click();
}
export async function readStudioProject(page: Page): Promise<Project> {
  const revision = page.locator(".command-bar .revision-chip");
  await expect(revision).toHaveText(/^r\d+$/);
  const visible = await revision.innerText();
  const project = await page.evaluate(async () => {
    if ("__MOTIONWRIGHT_SERVICE_TEST_TRANSPORT__" in window) {
      const libraryPath = "/src/projectLibrary.ts";
      const library = await import(/* @vite-ignore */ libraryPath);
      const selected = library.readProjectSelection();
      if (selected.status !== "remembered") throw new Error("Actual Service project selection is unavailable.");
      return library.openProjectFromLibrary(selected.projectId);
    }
    const path = "/src/api.ts";
    const api = await import(/* @vite-ignore */ path);
    const project = (await api.bootstrap()).project;
    if (!project) throw new Error("No project returned by Studio bootstrap.");
    return project;
  });
  expect("r" + project.revision).toBe(visible);
  return project;
}
export async function queuePaintMotionProposal(page: Page, target: CanvasNode) {
  await page.getByLabel("Patch target object").selectOption(target.id);
  await page.getByLabel("Patch edit kind").selectOption("style");
  await page.getByLabel("Patch fill", { exact: true }).fill("#72D6BE");
  await page.getByLabel("Patch font_weight", { exact: true }).fill("700");
  await proposalButton(page, "Add to proposal").click();
  await page.getByLabel("Patch edit kind").selectOption("motion");
  if (await proposalButton(page, "Clear animation").isEnabled()) await proposalButton(page, "Clear animation").click();
  const keys: CanvasKeyframe[] = [];
  for (const at of [0, 1.5]) {
    for (const property of ["x", "y", "width", "height", "rotation_deg", "opacity"] as const) {
      const value = !at && property === "x" ? target.x - 48 : !at && property === "opacity" ? 0 : target[property];
      const interpolation = at && property !== "opacity" ? "ease_out_cubic" : "linear";
      const index = keys.length + 1;
      await proposalButton(page, "Add keyframe").click();
      await page.getByLabel(`Keyframe ${index} time`, { exact: true }).fill(at ? "3/2" : "0");
      await page.getByLabel(`Keyframe ${index} property`, { exact: true }).selectOption(property);
      await page.getByLabel(`Keyframe ${index} value`, { exact: true }).fill(String(value));
      await page.getByLabel(`Keyframe ${index} curve`, { exact: true }).selectOption(interpolation);
      keys.push({ at: { num: at ? "3" : "0", den: at ? "2" : "1" }, property, value, interpolation });
    }
  }
  await expect(proposalButton(page, "Add to proposal")).toBeEnabled();
  await proposalButton(page, "Add to proposal").click();
  await page.getByLabel("Patch rationale").fill("At the entrance, improve contrast and preserve the complete spatial pose while fading the label in.");
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true }).getByLabel(/Remove proposed edit/)).toHaveCount(2);
  return { keys, style: { ...target.style, fill: "#72D6BE", font_weight: 700 } };
}
export async function leaveIncompletePaintMotionInput(page: Page) {
  await page.getByLabel("Keyframe 1 time", { exact: true }).fill("1/");
  await expect(proposalButton(page, "Add to proposal")).toBeDisabled();
  await page.getByLabel("Patch edit kind").selectOption("style");
  await page.getByLabel("Patch font_size", { exact: true }).fill("1e");
  await expect(proposalButton(page, "Add to proposal")).toBeDisabled();
}
export async function verifyIncompletePaintMotionInput(page: Page) {
  await page.getByLabel("Patch edit kind").selectOption("style");
  await expect(page.getByLabel("Patch font_size", { exact: true })).toHaveValue("1e");
  await expect(proposalButton(page, "Add to proposal")).toBeDisabled();
  await page.getByLabel("Patch edit kind").selectOption("motion");
  await expect(page.getByLabel("Keyframe 1 time", { exact: true })).toHaveValue("1/");
  await expect(proposalButton(page, "Add to proposal")).toBeDisabled();
}
export async function inspectPaintMotionProposal(page: Page) {
  await proposalButton(page, "Preview scoped changes").click();
  await expect(page.getByText("Editorial diff prepared", { exact: true })).toBeVisible();
  await page.getByLabel("Comparison playhead", { exact: true }).fill("1");
  await expect(page.getByRole("img", { name: "Before scoped changes", exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "After scoped changes", exact: true })).toBeVisible();
}
