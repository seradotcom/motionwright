/** Browser-only interactions with synthetic IPC; this is not a Native render. */
import { expect, test } from "@playwright/test";
type Fixture = {
  records(): { command:string; request:Record<string,unknown> }[];
  pendingNext():void; jobState(state:"running"|"cancelling"|"completed"|"failed_known"):void;
  holdObservation():void; releaseObservation():void;
  loseNext():void; holdGrant():void; releaseGrant():void; revision(value:number):void;
  holdRuntime():void; releaseRuntime():void; runtimeState(state:"files_unverified"|"installed"):void; retainRollback():void;
};
declare global { interface Window { __hyperframesRenderFixture:Fixture; } }
test("lost render acknowledgement survives reload and reads original PNGs without another render", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByRole("button", { name:"Read installed HyperFrames profiles", exact:true }).click();
  await panel.getByRole("button", { name:"Use selected HyperFrames profile", exact:true }).click();
  await expect(panel.getByText("Installed HyperFrames files verified for this selection.", { exact:false })).toBeVisible();
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
  expect(calls.filter(item => item.command === "runtime_profile_inventory")).toHaveLength(1);
  expect(calls.filter(item => item.command === "read_original_runtime_profile")).toHaveLength(1);
  expect(calls.some(item => item.command === "install_runtime_profile" || item.command === "rollback_runtime_profile")).toBe(false);
  expect(calls.find(item => item.command === "render_hyperframes_source")!.request.runtime_lock_sha256).toBe("d".repeat(64));
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(1);
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(1);
  expect(calls.filter(item => item.command === "read_original_hyperframes_render")
    .map(item => item.request.request_id)).toEqual([original]);
  expect(calls.some(item => item.command === "preview_native_frame" && item.request.frame_index === 59)).toBe(true);
});
test("lost browser selectors can reopen a completed HyperFrames original from saved history", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  await panel.getByRole("button", { name:"Read installed HyperFrames profiles", exact:true }).click();
  await panel.getByRole("button", { name:"Use selected HyperFrames profile", exact:true }).click();
  await panel.getByRole("button", { name:"Render saved HyperFrames source" }).click();
  await expect(panel.getByText("The provider completed the original 60-frame PNG sequence.", { exact:false })).toBeVisible();
  await page.evaluate(() => {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith("motionwright:hyperframes-render:v1:")) localStorage.removeItem(key);
    }
  });
  await page.reload();
  await expect(panel.getByRole("button", { name:"Read original HyperFrames result" })).toHaveCount(0);
  await panel.getByRole("button", { name:"Find saved HyperFrames renders" }).click();
  await panel.getByRole("button", { name:"Read original HyperFrames result" }).click();
  await expect(panel.getByText("The provider completed the original 60-frame PNG sequence.", { exact:false })).toBeVisible();
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "hyperframes_render_history")).toHaveLength(1);
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(1);
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(1);
  expect(calls.find(item => item.command === "read_original_hyperframes_render")!.request.request_id)
    .toBe(calls.find(item => item.command === "render_hyperframes_source")!.request.request_id);
});

test("a retired source cannot dispatch when an old grant returns after revision ABA", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByText("Use a separately provisioned runtime", { exact:true }).click();
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

test("an unverified installed runtime cannot enable rendering or grant any effect", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await page.evaluate(() => window.__hyperframesRenderFixture.runtimeState("files_unverified"));
  await panel.getByRole("button", { name:"Read installed HyperFrames profiles", exact:true }).click();
  await panel.getByRole("button", { name:"Use selected HyperFrames profile", exact:true }).click();
  await expect(panel.getByRole("alert")).toContainText("files could not be verified");
  await expect(panel.getByRole("button", { name:"Render saved HyperFrames source" })).toBeDisabled();
  await expect(panel.getByLabel("Runtime lock SHA-256", { exact:true })).toHaveValue("");
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "read_original_runtime_profile")).toHaveLength(1);
  expect(calls.some(item => ["issue_effect_grant", "render_hyperframes_source", "install_runtime_profile"].includes(item.command))).toBe(false);
});

test("a late runtime read cannot select a profile after source revision ABA", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByRole("button", { name:"Read installed HyperFrames profiles", exact:true }).click();
  await page.evaluate(() => window.__hyperframesRenderFixture.holdRuntime());
  await panel.getByRole("button", { name:"Use selected HyperFrames profile", exact:true }).click();
  await expect(panel.getByText("Checking the original installed HyperFrames files", { exact:false })).toBeVisible();
  await page.evaluate(() => window.__hyperframesRenderFixture.revision(5));
  await page.evaluate(() => window.__hyperframesRenderFixture.revision(4));
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  await page.evaluate(() => window.__hyperframesRenderFixture.releaseRuntime());
  await expect(panel.getByLabel("Runtime lock SHA-256", { exact:true })).toHaveValue("");
  await expect(panel.getByRole("button", { name:"Render saved HyperFrames source" })).toBeDisabled();
  await expect(panel.getByText("Installed HyperFrames files verified for this selection.", { exact:false })).toHaveCount(0);
  expect(await page.evaluate(() => window.__hyperframesRenderFixture.records()
    .some(item => item.command === "issue_effect_grant" || item.command === "render_hyperframes_source"))).toBe(false);
});

test("a retained runtime rollback prevents dispatch when the original render grant returns", async ({ page }) => {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByRole("button", { name:"Read installed HyperFrames profiles", exact:true }).click();
  await panel.getByRole("button", { name:"Use selected HyperFrames profile", exact:true }).click();
  await expect(panel.getByText("Installed HyperFrames files verified for this selection.", { exact:false })).toBeVisible();
  await page.evaluate(() => window.__hyperframesRenderFixture.holdGrant());
  await panel.getByRole("button", { name:"Render saved HyperFrames source" }).click();
  await expect(panel.getByText("The original native operation is running", { exact:false })).toBeVisible();
  await page.evaluate(() => window.__hyperframesRenderFixture.retainRollback());
  await page.evaluate(() => window.__hyperframesRenderFixture.releaseGrant());
  await expect(panel.getByRole("alert")).toContainText("selected runtime has a retained rollback");
  await expect(panel.getByRole("button", { name:"Render saved HyperFrames source" })).toBeDisabled();
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(1);
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(0);
  expect(calls.some(item => item.command === "install_runtime_profile" || item.command === "rollback_runtime_profile")).toBe(false);
});

async function pendingOriginal(page: import("@playwright/test").Page) {
  await page.goto("/tests/fixtures/hyperframes-render.html");
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  const panel = page.getByRole("region", { name:"HyperFrames native PNG rendering" });
  await panel.getByRole("button", { name:"Read installed HyperFrames profiles", exact:true }).click();
  await panel.getByRole("button", { name:"Use selected HyperFrames profile", exact:true }).click();
  await expect(panel.getByText("Installed HyperFrames files verified for this selection.", { exact:false })).toBeVisible();
  await page.evaluate(() => window.__hyperframesRenderFixture.pendingNext());
  await panel.getByRole("button", { name:"Render saved HyperFrames source" }).click();
  await expect(panel.getByRole("alert")).toContainText("remained pending");
  return panel;
}
test("a pending original job is explicitly checked and its finished PNGs reopen without another native operation", async ({ page }) => {
  const panel = await pendingOriginal(page);
  await panel.getByRole("button", { name:"Read original HyperFrames result" }).click();
  await expect(panel.getByText("The original outcome is unconfirmed", { exact:false })).toBeVisible();
  await panel.getByRole("button", { name:"Check original native job" }).click();
  await expect(panel.getByText("Last native check: the original job was running.", { exact:false })).toBeVisible();
  await expect(panel.getByRole("button", { name:"Render saved HyperFrames source" })).toBeDisabled();
  await expect(panel.getByRole("img")).toHaveCount(0);
  await page.evaluate(() => window.__hyperframesRenderFixture.jobState("completed"));
  await panel.getByRole("button", { name:"Check original native job" }).click();
  await expect(panel.getByText("The provider completed the original 60-frame PNG sequence.", { exact:false })).toBeVisible();
  await expect(panel.getByRole("button", { name:"Check original native job" })).toBeDisabled();
  await page.reload();
  await panel.getByRole("button", { name:"Read original HyperFrames result" }).click();
  const image = panel.getByRole("img");
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).naturalWidth)).toBe(64);
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(1);
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(1);
  expect(calls.filter(item => item.command === "observe_original_hyperframes_job")).toHaveLength(2);
  expect(calls.filter(item => item.command === "read_original_hyperframes_render")).toHaveLength(2);
  const id = calls.find(item => item.command === "render_hyperframes_source")!.request.request_id;
  expect(calls.filter(item => item.command === "observe_original_hyperframes_job").every(item => item.request.request_id === id)).toBe(true);
});
test("a late original job observation cannot activate a preview after revision ABA", async ({ page }) => {
  const panel = await pendingOriginal(page);
  await page.evaluate(() => {
    window.__hyperframesRenderFixture.jobState("completed");
    window.__hyperframesRenderFixture.holdObservation();
  });
  await panel.getByRole("button", { name:"Check original native job" }).click();
  await expect(panel.getByText("Checking the original native job without restarting it", { exact:false })).toBeVisible();
  await page.evaluate(() => window.__hyperframesRenderFixture.revision(5));
  await page.evaluate(() => window.__hyperframesRenderFixture.revision(4));
  await page.getByRole("button", { name:"Inspect saved HyperFrames source" }).click();
  await page.evaluate(() => window.__hyperframesRenderFixture.releaseObservation());
  await expect(panel.getByRole("img")).toHaveCount(0);
  await expect(panel.getByText("The provider completed the original", { exact:false })).toHaveCount(0);
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  expect(calls.filter(item => item.command === "observe_original_hyperframes_job")).toHaveLength(1);
  expect(calls.filter(item => item.command === "render_hyperframes_source")).toHaveLength(1);
});
test("an authenticated terminal failure permits a distinct explicit attempt without replay", async ({ page }) => {
  const panel = await pendingOriginal(page);
  await page.evaluate(() => window.__hyperframesRenderFixture.jobState("failed_known"));
  await panel.getByRole("button", { name:"Check original native job" }).click();
  await expect(panel.getByText("The original operation has a known failure.", { exact:false })).toBeVisible();
  await expect(panel.getByRole("button", { name:"Check original native job" })).toBeDisabled();
  await expect(panel.getByRole("button", { name:"Render saved HyperFrames source" })).toBeEnabled();
  await panel.getByRole("button", { name:"Render saved HyperFrames source" }).click();
  await expect(panel.getByText("The provider completed the original", { exact:false })).toBeVisible();
  const calls = await page.evaluate(() => window.__hyperframesRenderFixture.records());
  const ids = calls.filter(item => item.command === "render_hyperframes_source").map(item => item.request.request_id);
  expect(ids).toHaveLength(2);
  expect(new Set(ids).size).toBe(2);
  expect(calls.filter(item => item.command === "issue_effect_grant")).toHaveLength(2);
  expect(calls.filter(item => item.command === "observe_original_hyperframes_job")).toHaveLength(1);
});
