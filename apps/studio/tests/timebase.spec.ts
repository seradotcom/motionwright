import { expect, test, type Page } from "@playwright/test";

async function addAndReselectFirstFrameMarker(page: Page) {
  const timecode = page.locator(".timeline-header .timecode");
  const revision = page.locator(".revision-chip").first();
  const beforeRevision = Number((await revision.innerText()).slice(1));
  const markerCount = await page.locator(".timeline-marker").count();
  const marker = page.getByRole("button", { name: "Marker: Marker " + (markerCount + 1), exact: true });
  await expect(timecode).toHaveText("00:00:01");
  await page.getByRole("button", { name: "Marker", exact: true }).click();
  await expect(revision).toHaveText("r" + (beforeRevision + 1));
  await expect(page.locator(".timeline-marker")).toHaveCount(markerCount + 1);
  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect(timecode).toHaveText("00:00:02");
  await marker.click();
  await expect(marker).toHaveAttribute("aria-pressed", "true");
  await expect(timecode).toHaveText("00:00:01");
  await expect(revision).toHaveText("r" + (beforeRevision + 1));
}

test("stepping onto an exact fractional cut selects the incoming scene", async ({ page }) => {
  await page.route("**/src/fixture.ts*", async route => {
    const response = await route.fetch(), body = await response.text();
    await route.fulfill({ response, body: body
      + "\nfixtureProject.scenes[0].start={num:'1',den:'10'};fixtureProject.scenes[0].duration={num:'1',den:'5'};"
      + "\nfixtureProject.scenes[1].start={num:'3',den:'10'};"
      + "\nfor (const scene of fixtureProject.scenes.slice(0,2)) { scene.beats=[]; scene.nodes=[]; }"
      + "\nfixtureBootstrap.project=structuredClone(fixtureProject);\n" });
  });
  await page.goto("/");
  const timecode = page.locator(".timeline-header .timecode");
  const sceneName = page.locator(".preview-stage .frame-meta span").first();
  const forward = page.getByRole("button", { name: "Step forward one frame", exact: true });
  const back = page.getByRole("button", { name: "Step back one frame", exact: true });
  await page.locator(".scene-track button").filter({ hasText: "Reasoning is solved" }).press("Enter");
  await expect(timecode).toHaveText("00:00:03");
  for (let frame = 4; frame <= 8; frame++) await forward.click();
  await expect(timecode).toHaveText("00:00:08");
  await expect(sceneName).toHaveText("Reasoning is solved");

  await forward.click();
  await expect(timecode).toHaveText("00:00:09");
  await expect(sceneName).toHaveText("Pixels are brittle", { timeout: 2500 });
  await back.click();
  await expect(sceneName).toHaveText("Reasoning is solved");
});

test("the timeline and canvas use the selected delivery profile's frame duration", async ({ page }) => {
  await page.goto("/");
  const timecode = page.locator(".timeline-header .timecode");
  const timebase = page.getByLabel("Editor preview timebase");

  await expect(timebase.locator("option:checked")).toContainText("30 fps NDF");
  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect(timecode).toHaveText("00:00:01");
  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  await expect(page.locator(".motion-playhead .mono")).toHaveText("0.03s / 7.00s");
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await addAndReselectFirstFrameMarker(page);

  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByRole("button", { name: "New profile", exact: true }).click();
  await page.getByLabel("Profile name").fill("PAL editorial");
  await page.getByLabel("Frame rate", { exact: true }).selectOption("25/1");
  await page.getByRole("button", { name: "Save profile", exact: true }).click();
  await expect(timebase.locator("option").filter({ hasText: "PAL editorial" })).toHaveCount(1);

  const revision = await page.locator(".revision-chip").first().innerText();
  await timebase.selectOption({ label: "PAL editorial · 25 fps NDF" });
  await page.getByRole("button", { name: "Step back one frame" }).click();
  await expect(timecode).toHaveText("00:00:00");
  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect(timecode).toHaveText("00:00:01");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);

  await page.getByRole("button", { name: "Canvas", exact: true }).click();
  await expect(page.locator(".motion-playhead .mono")).toHaveText("0.04s / 7.00s");
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(page.locator(".preview-footer")).toContainText("25 fps NDF · editorial display");
  await expect(page.locator(".preview-footer")).toContainText("No render evidence attached");
});

test("drop-frame timecode is a deliberate display mode, not inferred by fractional fps", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Deliver", exact: true }).click();
  await page.getByRole("button", { name: "New profile", exact: true }).click();
  await page.getByLabel("Profile name").fill("NTSC review");
  await page.getByLabel("Frame rate", { exact: true }).selectOption("30000/1001");
  await page.getByRole("button", { name: "Save profile", exact: true }).click();

  await page.getByLabel("Editor preview timebase").selectOption({ label: "NTSC review · 29.97 fps NDF" });
  const mode = page.getByLabel("Timecode numbering");
  await expect(mode).toHaveValue("ndf");
  const revision = await page.locator(".revision-chip").first().innerText();
  await mode.selectOption("df");
  const timecode = page.locator(".timeline-header .timecode");
  await expect(timecode).toHaveText("00:00;00");
  await page.getByRole("button", { name: "Step forward one frame" }).click();
  await expect(timecode).toHaveText("00:00;01");
  await mode.selectOption("ndf");
  await expect(timecode).toHaveText("00:00:01");
  await expect(page.locator(".revision-chip").first()).toHaveText(revision);
  await addAndReselectFirstFrameMarker(page);
});
