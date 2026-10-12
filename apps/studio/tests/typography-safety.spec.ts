import { expect, test } from "@playwright/test";

test("Canvas source typography safety is read-only, inspectable and provenance-bound", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name:"Canvas", exact:true }).click();
  const revision=await page.locator(".revision-chip").first().innerText();
  const review=page.locator("details.canvas-type-preflight");
  await expect(review.locator("summary")).toContainText("Typography safety");
  await review.locator("summary").click();
  await expect(review).toContainText("Reasoning headline");
  await expect(review).toContainText("Sampled geometric bounds fit");
  await expect(review).toContainText("not native text shaping");
  await expect(review).toContainText("r");
  await review.getByRole("button",{name:"Inspect text Reasoning headline"}).click();
  await expect(page.getByLabel("Canvas text")).toHaveValue("AI agents can reason.");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
});

test("safe margin review follows committed camera changes and never fabricates a render PASS", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name:"Canvas", exact:true }).click();
  const review=page.locator("details.canvas-type-preflight");
  await review.locator("summary").click();
  await expect(review).toContainText("Geometry sampled");
  const before=await page.locator(".revision-chip").first().innerText();
  await page.getByLabel("Camera safe area").fill("0.3");
  await expect.poll(async ()=>page.locator(".revision-chip").first().innerText()).not.toBe(before);
  await expect(review).toContainText("Text box crosses the camera safe margin");
  await expect(review.locator("summary")).toContainText("flagged");
  await expect(review).toContainText("native frame evidence");
});
