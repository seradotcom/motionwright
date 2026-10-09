import { expect, test } from "@playwright/test";

test("Canvas camera projection uses source camera center, zoom and rotation without baking objects", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  const canvas = page.getByRole("region", { name: "Semantic canvas" });
  const camera = canvas.getByTestId("canvas-camera-layer");
  const scene = page.getByRole("button", { name: "Canvas object Reasoning headline" });
  const original = {
    x: await page.getByLabel("Canvas X").inputValue(),
    y: await page.getByLabel("Canvas Y").inputValue(),
  };
  await expect(camera).toHaveCSS("transform", "matrix(1, 0, 0, 1, 0, 0)");
  const initial = await page.locator(".revision-chip").first().innerText();
  // Pan to the center of the selected object and zoom, preserving authored
  // node pose. The canvas safe area remains fixed to the output frame.
  await page.getByLabel("Camera Center X").fill("620");
  await expect(page.getByLabel("Camera Center X")).toHaveValue("620");
  await page.getByLabel("Camera Center Y").fill("220");
  await expect(page.getByLabel("Camera Center Y")).toHaveValue("220");
  await page.getByLabel("Camera zoom").fill("2");
  await expect(page.getByLabel("Camera zoom")).toHaveValue("2");
  await expect(camera).not.toHaveCSS("transform", "matrix(1, 0, 0, 1, 0, 0)");
  await expect(page.getByLabel("Canvas X")).toHaveValue(original.x);
  await expect(page.getByLabel("Canvas Y")).toHaveValue(original.y);
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(initial);

  await page.getByLabel("Camera Rotation").fill("90");
  await expect(page.getByLabel("Camera Rotation")).toHaveValue("90");
  const stageRect = await canvas.locator(".canvas-stage").boundingBox();
  const nodeRect = await scene.boundingBox();
  if (!stageRect || !nodeRect) throw new Error("Expected an actual camera-projected semantic object");
  // Camera center is the object center (620,220); a 90deg rotation still
  // keeps that center at the stage viewport center.
  expect(nodeRect.x + nodeRect.width / 2).toBeCloseTo(stageRect.x + stageRect.width / 2, -1);
  expect(nodeRect.y + nodeRect.height / 2).toBeCloseTo(stageRect.y + stageRect.height / 2, -1);

  await page.getByLabel("Camera safe area").fill("0.2");
  await expect(canvas.locator(".canvas-safe-area")).toHaveCSS("inset", "20%");
  await expect(page.getByLabel("Canvas X")).toHaveValue(original.x);
  await expect(page.getByLabel("Canvas Y")).toHaveValue(original.y);
});

test("dragging a zoomed and rotated camera view updates world-space pose, not screen-space pose", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  await page.getByLabel("Camera Center X").fill("620");
  await expect(page.getByLabel("Camera Center X")).toHaveValue("620");
  await page.getByLabel("Camera Center Y").fill("220");
  await expect(page.getByLabel("Camera Center Y")).toHaveValue("220");
  await page.getByLabel("Camera zoom").fill("2");
  await expect(page.getByLabel("Camera zoom")).toHaveValue("2");
  await page.getByLabel("Camera Rotation").fill("90");
  await expect(page.getByLabel("Camera Rotation")).toHaveValue("90");
  const target = page.getByRole("button", { name: "Canvas object Reasoning headline" });
  const rect = await target.boundingBox();
  const stage = await page.locator(".canvas-stage").boundingBox();
  if (!rect || !stage) throw new Error("Canvas stage or selected node is not visible");
  const beforeX = Number(await page.getByLabel("Canvas X").inputValue());
  const beforeY = Number(await page.getByLabel("Canvas Y").inputValue());
  const beforeRev = await page.locator(".revision-chip").first().innerText();

  const fromX = rect.x + rect.width / 2;
  const fromY = rect.y + rect.height / 2;
  await page.mouse.move(fromX, fromY);
  await page.mouse.down();
  await page.mouse.move(fromX + 32, fromY, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => page.locator(".revision-chip").first().innerText()).not.toBe(beforeRev);

  const afterX = Number(await page.getByLabel("Canvas X").inputValue());
  const afterY = Number(await page.getByLabel("Canvas Y").inputValue());
  const expectedWorldY = 32 * 1920 / stage.width / 2;
  expect(afterX - beforeX).toBeCloseTo(0, 3);
  expect(afterY - beforeY).toBeCloseTo(expectedWorldY, 1);
  await expect(page.locator(".motion-keyframe-row")).toHaveCount(0);
});
