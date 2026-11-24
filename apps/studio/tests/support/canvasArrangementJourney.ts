import { expect, type Page } from "@playwright/test";
import { proposalButton as button, readStudioProject } from "./scopedPaintMotionJourney";
import type { Project } from "../../src/types";

export async function createArrangementScene(page: Page) {
  const revision = page.locator(".command-bar .revision-chip");
  let expected = Number((await revision.innerText()).replace("r", ""));
  const saved = async () => { expected++; await expect(revision).toHaveText("r" + expected); };
  const name = "Editable frame arrangement";
  await button(page, "Add scene").click();
  await page.getByLabel("New scene name", { exact: true }).fill(name);
  await button(page, "Add").click(); await saved();
  await button(page, "Canvas").click();
  await page.locator(".tree-row").filter({ hasText: name }).click();
  const ids: string[] = [];
  for (const [index, [x,y,w,h]] of [[300,320,100,60],[480,430,200,80],[800,300,80,100]].entries()) {
    await page.getByLabel("Create canvas object", { exact: true }).getByRole("button", { name: "shape", exact: true }).click();
    await saved();
    const inspector = page.locator(".canvas-draft-inspector");
    await expect(inspector).toHaveCount(1);
    const id = (await inspector.getByLabel("Canvas X", { exact: true }).getAttribute("id"))!.slice("canvas-draft-".length, -2);
    ids.push(id);
    await expect(inspector.locator('[id="canvas-draft-' + id + '-x"]')).toHaveCount(1);
    for (const [label, value] of [["Canvas X",x],["Canvas Y",y],["Canvas W",w],["Canvas H",h]] as const)
      await inspector.getByLabel(label, { exact: true }).fill(String(value));
    await inspector.getByRole("button", { name: "Commit transform", exact: true }).click(); await saved();
    if (index === 1) {
      const pose = page.getByRole("region", { name: "Native pose animation", exact: true });
      for (const [label,value] of [["Pose start x","440"],["Pose start y","430"],["Pose start width","200"],
        ["Pose start height","80"],["Pose start rotation","0"],["Pose end frame","30"]])
        await pose.getByLabel(label, { exact: true }).fill(value);
      await pose.getByLabel("Pose geometry curve", { exact: true }).selectOption("ease_out_cubic");
      await pose.getByRole("button", { name: "Create native pose animation", exact: true }).click(); await saved();
    }
  }
  const source = await readStudioProject(page), scene = source.scenes.find(item => item.name === name)!;
  await button(page, "Production").click();
  await page.getByLabel("Production working scene", { exact: true }).selectOption(scene.id);
  await button(page, "Camera").click();
  for (const [label,value] of [["Start center X","960"],["Start center Y","540"],["End center X","978"],
    ["End center Y","548"],["Camera duration frames","90"]])
    await page.getByLabel(label, { exact: true }).fill(value);
  await button(page, "Attach camera reframe").click(); await saved();
  await button(page, "Canvas").click();
  return { ids, sceneId: scene.id, name, revision: expected };
}

export async function selectArrangement(page: Page, ids: string[]) {
  await button(page, "Arrange objects").click();
  await page.getByLabel("Arrangement operation", { exact: true }).selectOption("gaps_x");
  for (const id of ids) await page.locator('[id="arrangement-' + id + '"]').check();
  await expect(button(page, "Stage arrangement")).toBeEnabled();
}
export async function verifyArrangementSelection(page: Page, ids: string[]) {
  await expect(page.getByLabel("Arrangement operation", { exact: true })).toHaveValue("gaps_x");
  for (const id of ids) await expect(page.locator('[id="arrangement-' + id + '"]')).toBeChecked();
}
export async function stageAndInspectArrangement(page: Page, targetId: string) {
  await button(page, "Stage arrangement").click();
  await expect(page.getByRole("region", { name: "Proposed edits", exact: true }).getByLabel(/Remove proposed edit/)).toHaveCount(2);
  await button(page, "Preview scoped changes").click();
  await expect(page.getByText("Editorial diff prepared", { exact: true })).toBeVisible();
  const before = page.getByRole("img", { name: "Before scoped changes", exact: true });
  const after = page.getByRole("img", { name: "After scoped changes", exact: true });
  for (const [time,x] of [["0",440],["0.5",475],["1",480]] as const) {
    await page.getByLabel("Comparison playhead", { exact: true }).fill(time);
    await expect(before.locator('[data-canvas-node-id="' + targetId + '"] rect')).toHaveAttribute("x", String(x));
    await expect(after.locator('[data-canvas-node-id="' + targetId + '"] rect')).toHaveAttribute("x", String(x + 20));
    await expect(after).toHaveAttribute("data-camera-center-x", await before.getAttribute("data-camera-center-x") ?? "");
  }
}
export function assertSavedArrangement(base: Project, saved: Project, sceneId: string, ids: string[]) {
  expect(saved.revision).toBe(base.revision + 1);
  const source = base.scenes.find(scene => scene.id === sceneId)!, actual = saved.scenes.find(scene => scene.id === sceneId)!;
  expect(actual.nodes).toEqual(source.nodes.map(node => node.id === ids[1] ? {
    ...node, x: 500, keyframes: node.keyframes.map(key => ({ ...key, value: key.value + (key.property === "x" ? 20 : 0) })),
  } : node));
  expect(saved.production_design!.camera_reframes).toEqual(base.production_design!.camera_reframes);
  expect(saved.assets).toEqual(base.assets);
  expect(saved.scenes.filter(scene => scene.id !== sceneId)).toEqual(base.scenes.filter(scene => scene.id !== sceneId));
  const record = saved.production_design!.patches!.at(-1)!;
  expect(record.source_revision).toBe(base.revision);
  expect(record.before).toEqual(source.nodes.filter(node => node.id === ids[1]));
  expect(record.after).toEqual(actual.nodes.filter(node => node.id === ids[1]));
}
export async function undoArrangement(page: Page, revision: number) {
  const history = page.getByRole("region", { name: "Reversible scoped changes", exact: true });
  await history.getByRole("button", { name: "Preview undo", exact: true }).click();
  await button(page, "Apply undo as new revision").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r" + revision);
}
