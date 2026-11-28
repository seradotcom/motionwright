import { expect, test } from "@playwright/test";
import { fixtureProject } from "../src/fixture";

test("Deliver shows the exact declared Native output contract for its selected profile", async ({ page }) => {
  const project = structuredClone(fixtureProject);
  const [first, second] = project.deliverables;
  Object.assign(first, { name: "Native Rec.709", frame_rate: { num: "30000", den: "1001" },
    color_space: "rec709", video_codec: "h264", audio_codec: "aac", audio_sample_rate_hz: 48000,
    container: "mp4", captions: true, caption_format: "web_vtt", burn_in_captions: false });
  Object.assign(second, { name: "P3 variant", color_space: "display_p3" });
  await page.route("**/src/fixture.ts", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, body: (await response.text()) + "\nfixtureBootstrap.project = " + JSON.stringify(project) + ";\n" });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  const index = page.getByRole("list", { name: "Delivery profile index" });
  await index.getByRole("listitem").filter({ hasText: "Native Rec.709" }).click();
  const contract = page.locator('[aria-label="Expected Native master format"]');
  await expect(contract).toContainText("30000/1001 fps");
  await expect(contract).toContainText("BT.709 primaries · BT.709 transfer · BT.709 matrix · limited range");
  await expect(contract).toContainText("yuv420p · opaque");
  await expect(contract).toContainText("AAC · 48 kHz · stereo");
  await expect(contract).toContainText("Separate WebVTT sidecar");
  await index.getByRole("listitem").filter({ hasText: "P3 variant" }).click();
  await expect(contract).toHaveCount(0);
});
