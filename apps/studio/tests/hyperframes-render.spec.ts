/** Browser-only interactions with synthetic IPC; this is not a Native render. */
import { expect, test } from "@playwright/test";
type Fixture = {
  records(): { command:string; request:Record<string,unknown> }[];
  loseNext():void; holdGrant():void; releaseGrant():void; revision(value:number):void;
};
declare global { interface Window { __hyperframesRenderFixture:Fixture; } }
test("lost render acknowledgement survives reload and reads original PNGs without another render", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByRole("textbox", { name:"Runtime lock SHA-256", exact:true }).fill("d".repeat(64));
  await page.evaluate(() => window.__hyperframesRenderFixture.loseNext());
  await panel.getByRole("button", { name:"Render saved HyperFrames source" }).click();
  await expect(panel.getByRole("alert")).toContainText("acknowledgement lost");
  await expect(panel.getByRole("button", { name:"Render saved HyperFrames source" })).toBeDisabled();
  const original = await page.evaluate(() => window.__hyperframesRenderFixture.records()
    .find(item => item.command === "render_hyperframes_source")!.request.request_id);
  await page.reload();
  await expect(panel.getByText("An original request is retained", { exact:false })).toBeVisible();
  await panel.getByRole("button", { name:"Read original HyperFrames result" }).click();
  await expect(panel.getByText("The provider completed the original 60-frame PNG sequence.", { exact:false })).toBeVisible();
  const image = panel.getByRole("img");
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBe(64);
  const slider = panel.getByRole("slider");
  await slider.focus(); await slider.press("End");
  await expect(panel.getByText("Native frame 60 of 60", { exact:true })).toBeVisible();
  await expect(panel.getByRole("link", { name:"Save displayed PNG" })).toBeVisible();
  const download = page.waitForEvent("download");
  await panel.getByRole("link", { name:"Save displayed PNG" }).click();
  expect((await download).suggestedFilename()).toBe("hyperframes-frame_000060.png");
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(1);
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(1);
  expect(calls.filter(item => item.command === "read_original_hyperframes_render")
    .map(item => item.request.request_id)).toEqual([original]);
  expect(calls.some(item => item.command === "preview_native_frame" && item.request.frame_index === 59)).toBe(true);
});
test("a retired source cannot dispatch when an old grant returns after revision ABA", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByRole("textbox", { name:"Runtime lock SHA-256", exact:true }).fill("d".repeat(64));
  await page.evaluate(() => window.__hyperframesRenderFixture.holdGrant());
  await panel.getByRole("button", { name:"Render saved HyperFrames source" }).click();
  await expect(panel.getByText("The original native operation is running", { exact:false })).toBeVisible();
  await page.evaluate(() => window.__hyperframesRenderFixture.revision(5));
  await page.evaluate(() => window.__hyperframesRenderFixture.revision(4));
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  await page.evaluate(() => window.__hyperframesRenderFixture.releaseGrant());
  await expect.poll(() => page.evaluate(() => window.__hyperframesRenderFixture.records()
    .filter(item => item.command === "render_hyperframes_source").length)).toBe(0);
  await panel.getByRole("button", { name:"Read original HyperFrames result" }).click();
  await expect(panel.getByText("The original outcome is unconfirmed", { exact:false })).toBeVisible();
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(1);
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(0);
});
