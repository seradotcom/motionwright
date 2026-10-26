import { expect, test, type Page } from "@playwright/test";

const fixture = "/tests/fixtures/semantic-graph-authority.html";
const preview = (page: Page) => page.getByRole("img", { name: "Editorial semantic Graph vector study", exact: true });

test("Graph editorial text uses loaded local font faces for both declared families", async ({ page }) => {
  const fontRequests: string[] = [];
  page.on("request", request => { if (new URL(request.url()).pathname.endsWith(".woff2")) fontRequests.push(request.url()); });
  await page.goto(fixture);
  for (const family of ["Instrument Sans Variable", "IBM Plex Mono"]) {
    await page.getByLabel("Graph font family", { exact: true }).selectOption(family);
    await expect(preview(page)).toBeVisible();
    const licenseUrl = await page.getByRole("link", { name: "Preview font license", exact: true }).getAttribute("href");
    expect(licenseUrl).toBeTruthy();
    const license = await page.request.get(licenseUrl!);
    expect(license.ok()).toBe(true);
    expect(await license.text()).toContain("SIL OPEN FONT LICENSE");
    await expect.poll(async () => preview(page).locator("text").first().evaluate(async element => {
      const style = getComputedStyle(element);
      const faces = await document.fonts.load(style.fontWeight + " " + style.fontSize + " " + style.fontFamily, element.textContent ?? "");
      return faces.filter(face => face.status === "loaded").length;
    }), { message: family + " must use actual loaded font bytes instead of a browser fallback." }).toBeGreaterThan(0);
  }
  expect(fontRequests.length).toBeGreaterThan(0);
  expect(fontRequests.every(url => new URL(url).origin === new URL(page.url()).origin)).toBe(true);
  await expect(page.getByLabel("Vertex 1 label", { exact: true })).toHaveValue("Cause");
  await expect(page.getByLabel("Vertex 1 box x", { exact: true })).toHaveValue("153.6");
});

test("Missing Graph font bytes leave the draft editable and a retry restores the actual preview", async ({ page }) => {
  await page.route("**/*.woff2", route => route.abort("failed"));
  await page.goto(fixture);
  await expect(page.getByRole("alert")).toContainText("Preview unavailable");
  await expect(preview(page)).toHaveCount(0);
  await page.getByLabel("Vertex 1 label", { exact: true }).fill("Kept after font failure");
  await page.getByLabel("Vertex 1 box x", { exact: true }).fill("160");
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Preview unavailable");
  await expect(page.getByLabel("Vertex 1 label", { exact: true })).toHaveValue("Kept after font failure");
  await expect(page.getByLabel("Vertex 1 box x", { exact: true })).toHaveValue("160");
  await page.unroute("**/*.woff2");
  await page.getByRole("button", { name: "Retry Graph preview fonts", exact: true }).click();
  await expect(preview(page)).toBeVisible();
  await expect(preview(page)).toContainText("Kept after font failure");
  await expect(page.getByLabel("Vertex 1 box x", { exact: true })).toHaveValue("160");
});

test("A corrupted local font response cannot become a fallback preview", async ({ page }) => {
  await page.route("**/instrument-sans-latin-wght-normal.woff2", async route => {
    const response = await route.fetch(), bytes = await response.body();
    bytes[0] ^= 1;
    await route.fulfill({ response, body: bytes });
  });
  await page.goto(fixture);
  await expect(page.getByRole("alert")).toContainText("does not match its source");
  await expect(preview(page)).toHaveCount(0);
  await expect(page.getByLabel("Vertex 1 label", { exact: true })).toHaveValue("Cause");
});

test("Unsupported glyphs remain literal through reload and never use a system fallback", async ({ page }) => {
  await page.goto(fixture); await expect(preview(page)).toBeVisible();
  await page.getByLabel("Vertex 1 label", { exact: true }).fill("\u6f22\u5b57\u{1f642}");
  await expect(page.getByRole("status").filter({ hasText: "Preview paused" })).toContainText("U+6F22");
  await expect(preview(page)).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("Vertex 1 label", { exact: true })).toHaveValue("\u6f22\u5b57\u{1f642}");
  await expect(page.getByRole("status").filter({ hasText: "Preview paused" })).toContainText("U+1F642");
  await page.getByLabel("Vertex 1 label", { exact: true }).fill("Restored label");
  await expect(preview(page)).toBeVisible();
  await expect(preview(page)).toContainText("Restored label");
  await expect(page.getByLabel("Vertex 1 box x", { exact: true })).toHaveValue("153.6");
});

test("A late failure for the previous font cannot replace the current font preview", async ({ page }) => {
  let release = () => {}, began = () => {}, finished = () => {};
  const wait = new Promise<void>(resolve => { release = resolve; });
  const started = new Promise<void>(resolve => { began = resolve; });
  const completed = new Promise<void>(resolve => { finished = resolve; });
  await page.route("**/instrument-sans-latin-wght-normal.woff2", async route => {
    began(); await wait; await route.abort("failed"); finished();
  });
  try {
    await page.goto(fixture); await started;
    await page.getByLabel("Graph font family", { exact: true }).selectOption("IBM Plex Mono");
    await expect(preview(page)).toBeVisible();
    release(); await completed;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(preview(page)).toBeVisible();
    const family = await preview(page).locator("text").first().evaluate(element => getComputedStyle(element).fontFamily);
    expect(family).toContain("IBM Plex Mono");
    await expect(page.getByLabel("Graph font family", { exact: true })).toHaveValue("IBM Plex Mono");
  } finally { release(); }
});
