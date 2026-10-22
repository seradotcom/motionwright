import { expect, test, type Page } from "@playwright/test";
import type {} from "./fixtures/preview-recovery";

// Genuine Chromium DOM/image decode and IndexedDB; explicitly synthetic native
// transport. These assertions must never be counted as desktop/native ACC-010.
const url = "/tests/fixtures/preview-recovery.html";
const native = (page: Page) => page.getByRole("region", { name: "Verified native PNG frame preview", exact: true });
const historic = (page: Page) => page.getByRole("region", { name: "Retained historical preview", exact: true });
async function openNative(page: Page) {
  await page.goto(url);
  await page.getByRole("button", { name: "Show native frames", exact: true }).click();
  await expect(native(page).getByRole("img")).toBeVisible();
  await expect.poll(() => native(page).getByRole("img").evaluate(image => (image as HTMLImageElement).naturalWidth)).toBe(1);
}
async function durable(page: Page) {
  await expect(page.getByLabel("Preview recovery status")).toContainText("Recovery copy confirmed for reopening");
}
async function recoveredPixels(page: Page, revision: number, frame = 1) {
  await expect(historic(page)).toBeVisible();
  await expect(historic(page)).toContainText(`source r${revision}`);
  await expect(historic(page)).toContainText("NOT CURRENT");
  const image = historic(page).getByRole("img", { name: `Retained preview from revision ${revision} · frame ${frame} of 450 · not current`, exact: true });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(value => (value as HTMLImageElement).naturalWidth)).toBe(1);
}

test("revision changes retain the actually decoded previous PNG and label it not current", async ({ page }, testInfo) => {
  await openNative(page);
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.project());
  const currentUrl = await native(page).getByRole("img").getAttribute("src");
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.revise());
  // This is the causal assertion. The pre-change App removes the prior image.
  await recoveredPixels(page, before.revision);
  await expect(native(page)).toHaveCount(0);
  await expect(page.getByLabel("Scene native render receipt")).toContainText("STALE receipt");
  expect(await historic(page).getByRole("img").getAttribute("src")).not.toBe(currentUrl);
  await testInfo.attach("historical-after-edit", { body: await page.screenshot(), contentType: "image/png" });
});

test("a real browser reload restores the stored Blob without reusing a native token", async ({ page }, testInfo) => {
  await openNative(page); await durable(page);
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.project());
  await page.evaluate(() => { window.__PREVIEW_RECOVERY__.disconnect(); return window.__PREVIEW_RECOVERY__.revise(); });
  await page.reload();
  await recoveredPixels(page, before.revision);
  await expect(native(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Show native frames", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.reads())).toEqual([]);
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(rows).toHaveLength(1);
  expect(rows[0].sourceRevision).toBe(before.revision);
  expect(Object.keys(rows[0]).some(key => /token|grant|path|receipt|evidence|recipe/i.test(key))).toBe(false);
  await page.getByRole("button", { name: "Inspect receipt in Deliver", exact: true }).click();
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.inspected())).toBe(1);
  await testInfo.attach("reopened-historical-blob", { body: await page.screenshot(), contentType: "image/png" });
});

test("same-revision reopen remains historical and workspace navigation keeps the copy", async ({ page }) => {
  await openNative(page); await durable(page);
  const revision = await page.evaluate(() => window.__PREVIEW_RECOVERY__.project().revision);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.unmount());
  await expect(page.getByRole("region", { name: "Program preview", exact: true })).toHaveCount(0);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.mount());
  await recoveredPixels(page, revision);
  await page.reload();
  await recoveredPixels(page, revision);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.reads())).toEqual([]);
});

test("a pending native read and concurrent revision change preserve the old PNG through disconnect and reopen", async ({ page }) => {
  await openNative(page); await durable(page);
  const revision = await page.evaluate(() => window.__PREVIEW_RECOVERY__.project().revision);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.hold());
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(3));
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.pending())).toBe(1);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.revise());
  await recoveredPixels(page, revision, 1);
  await page.evaluate(() => { window.__PREVIEW_RECOVERY__.disconnect(); window.__PREVIEW_RECOVERY__.release(); });
  await recoveredPixels(page, revision, 1);
  await page.reload();
  await recoveredPixels(page, revision, 1);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.reads())).toEqual([]);
  expect((await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows()))[0].frameIndex).toBe(0);
});

for (const field of ["id", "generation", "active_branch"] as const) {
  test(`another ${field} cannot display or delete the old scope's preview`, async ({ page }) => {
    await openNative(page); await durable(page);
    const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
    await page.evaluate(field => window.__PREVIEW_RECOVERY__.setIdentity(field, "other-scope"), field);
    await expect(historic(page)).toHaveCount(0);
    await expect(native(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Remove my retained preview" })).toHaveCount(0);
    expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(rows);
  });
}

test("profile and scene selection never borrow retained pixels from another scope", async ({ page }) => {
  await openNative(page); await durable(page);
  const revision = await page.evaluate(() => window.__PREVIEW_RECOVERY__.project().revision);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.profile(1));
  await expect(historic(page)).toHaveCount(0);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.profile(0));
  await recoveredPixels(page, revision);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.scene(1));
  await expect(historic(page)).toHaveCount(0);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.scene(0));
  await recoveredPixels(page, revision);
});

test("a late native response after selection changed never becomes a recovery copy", async ({ page }) => {
  await page.goto(url);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.hold());
  await page.getByRole("button", { name: "Show native frames", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.pending())).toBe(1);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.setIdentity("generation", "different-generation"));
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.release());
  await expect(historic(page)).toHaveCount(0);
  await expect(native(page)).toHaveCount(0);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual([]);
  await page.reload();
  await expect(historic(page)).toHaveCount(0);
});

test("PNG-shaped bytes that the browser cannot decode are never retained", async ({ page }) => {
  await page.goto(url);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.hold());
  await page.getByRole("button", { name: "Show native frames", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.pending())).toBe(1);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.release(true));
  await expect(native(page)).toContainText("The decoded PNG cannot be displayed");
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual([]);
});

test("a quota fault preserves the earlier durable copy and keeps the new image recoverably honest", async ({ page }) => {
  await openNative(page); await durable(page);
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  const revision = before[0].sourceRevision;
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.quotaFail());
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(1));
  await expect(native(page).getByRole("img", { name: /Frame 2 \/ 450/ })).toBeVisible();
  await expect(page.getByLabel("Preview recovery status")).toContainText("recovery copy is not confirmed");
  await expect(page.getByLabel("Preview recovery status")).toContainText("Existing copies were preserved");
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(before);
  await page.reload();
  await recoveredPixels(page, revision, 1);
});

test("retrying a failed recovery write confirms the latest decoded frame and preserves a peer", async ({ page }) => {
  await openNative(page); await durable(page);
  const peerId = await page.evaluate(() => window.__PREVIEW_RECOVERY__.peerCopy(4));
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.quotaFail());
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(1));
  await expect(page.getByLabel("Preview recovery status")).toContainText("recovery copy is not confirmed");
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.quotaRestore());
  await page.getByRole("button", { name: "Retry preview recovery", exact: true }).click();
  await durable(page);
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(rows).toHaveLength(2);
  expect(rows.some(row => row.id === peerId)).toBe(true);
  expect(rows.some(row => row.frameIndex === 1)).toBe(true);
  await page.getByRole("button", { name: "Remove my retained preview", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(rows.filter(row => row.id === peerId));
});

test("corrupt pixels fail readback, preserve raw rows and restore an earlier valid copy", async ({ page }) => {
  await openNative(page); await durable(page);
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  const peerId = await page.evaluate(() => window.__PREVIEW_RECOVERY__.peerCopy(4));
  await page.evaluate(id => window.__PREVIEW_RECOVERY__.corrupt(id), peerId);
  await page.reload();
  await recoveredPixels(page, before[0].sourceRevision, 1);
  await expect(page.getByLabel("Preview recovery status")).toContainText("failed its local SHA-256 check");
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(rows).toHaveLength(2);
  expect(rows.some(row => row.id === peerId)).toBe(true);
  await expect(page.getByRole("button", { name: "Remove my damaged recovery copy", exact: true })).toHaveCount(0);
});

test("a damaged own copy stays until explicit removal and a new decoded preview can be retained", async ({ page }) => {
  await openNative(page); await durable(page);
  const [original] = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  await page.evaluate(id => window.__PREVIEW_RECOVERY__.corrupt(id), original.id);
  await page.reload();
  await expect(historic(page)).toHaveCount(0);
  await expect(page.getByLabel("Preview recovery status")).toContainText("failed its local SHA-256 check");
  expect((await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows()))[0].id).toBe(original.id);
  await page.getByRole("button", { name: "Remove my damaged recovery copy", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual([]);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.renderNew());
  await page.getByRole("button", { name: "Show native frames", exact: true }).click();
  await durable(page);
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(rows).toHaveLength(1); expect(rows[0].id).not.toBe(original.id);
});

test("a late persistence acknowledgement cannot replace a newer decoded image", async ({ page }) => {
  await page.goto(url);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.holdNextHash());
  await page.getByRole("button", { name: "Show native frames", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.hashPending())).toBe(true);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(1));
  await expect(native(page).getByRole("img", { name: /Frame 2 \/ 450/ })).toBeVisible();
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.revise());
  const revision = (await page.evaluate(() => window.__PREVIEW_RECOVERY__.project().revision)) - 1;
  await recoveredPixels(page, revision, 2);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.releaseHash());
  await durable(page);
  await recoveredPixels(page, revision, 2);
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(rows).toHaveLength(1); expect(rows[0].frameIndex).toBe(1);
  await page.reload(); await recoveredPixels(page, revision, 2);
});

test("a duplicated writer with the same timestamp cannot lose its concurrent immutable copy", async ({ page }) => {
  await openNative(page); await durable(page);
  const [original] = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  await page.evaluate(() => { window.__PREVIEW_RECOVERY__.freezeCaptureTime(); window.__PREVIEW_RECOVERY__.holdNextHash(); });
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(1));
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.hashPending())).toBe(true);
  // Browsers may copy sessionStorage when duplicating a tab. A timestamp and
  // shared writer label must not turn a newly observed peer row into our old row.
  const peerId = await page.evaluate(writer => window.__PREVIEW_RECOVERY__.peerCopy(9, writer), original.writerId);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.releaseHash());
  await durable(page);
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(rows.some(row => row.id === peerId)).toBe(true);
  expect(rows.some(row => row.frameIndex === 1)).toBe(true);
  expect(rows).toHaveLength(2);
});

test("the real record bound refuses a new copy without deleting other writers", async ({ page }) => {
  await openNative(page); await durable(page);
  for (let count = 1; count < 16; count++) await page.evaluate(() => window.__PREVIEW_RECOVERY__.peerCopy(3));
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(before).toHaveLength(16);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(1));
  await expect(page.getByLabel("Preview recovery status")).toContainText("Preview recovery storage is full");
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(before);
  const own = before.find(row => row.sceneName !== "Peer saved scene")!;
  await page.getByRole("button", { name: "Remove my earlier recovery copy", exact: true }).click();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(before.filter(row => row.id !== own.id));
  await page.getByRole("button", { name: "Retry preview recovery", exact: true }).click();
  await durable(page);
  const after = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  expect(after).toHaveLength(16);
  expect(after.some(row => row.id === own.id)).toBe(false);
  expect(after.filter(row => row.sceneName === "Peer saved scene")).toEqual(before.filter(row => row.id !== own.id));
  expect(after.some(row => row.frameIndex === 1)).toBe(true);
});


// AV replies remain pending deliberately. These cases prove UI authority,
// component lifetime and read custody, not MP4 decode, playback fidelity or
// native production. The unchanged Program AV suite exercises the full App.
const avMonitor = (page: Page) => page.getByRole("region", { name: "Native master playback review", exact: true });
async function openMaster(page: Page) {
  await page.goto(url);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.withMaster());
  await page.getByRole("button", { name: "Review final AV", exact: true }).click();
  await expect(avMonitor(page)).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads().length)).toBe(1);
}
test("an explicit AV choice and its in-flight component survive a scene boundary within the same master", async ({ page }, testInfo) => {
  await openMaster(page);
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads());
  await avMonitor(page).evaluate(element => { (element as HTMLElement).dataset.continuity = "original-master-component"; });
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.masterScene(1));
  await expect(avMonitor(page)).toBeVisible();
  await expect(avMonitor(page)).toHaveAttribute("data-continuity", "original-master-component");
  await expect(page.getByRole("button", { name: "Show design", exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads())).toEqual(before);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.reads())).toEqual([]);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.masterScene(0));
  await expect(avMonitor(page)).toHaveAttribute("data-continuity", "original-master-component");
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads())).toEqual(before);
  await testInfo.attach("av-choice-across-scene-pending-synthetic-read", { body: await page.screenshot(), contentType: "image/png" });
});
test("an explicit AV choice suspends outside its cut without reads and resumes on return", async ({ page }) => {
  await openMaster(page);
  const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads());
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.masterScene(2));
  await expect(avMonitor(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Review final AV", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads())).toEqual(before);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.reads())).toEqual([]);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.masterScene(0));
  await expect(avMonitor(page)).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads().length)).toBe(2);
  const returned = await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads());
  expect(returned[1]).toEqual(before[0]);
});
for (const field of ["id", "generation", "active_branch", "revision", "profile", "frameToken", "exportToken"] as const) {
  test("AV authority " + field + " A-to-B-to-A never resurrects a previous explicit choice", async ({ page }) => {
    await openMaster(page);
    const before = await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads());
    await page.evaluate(field => window.__PREVIEW_RECOVERY__.changeMasterAuthority(field), field);
    await expect(avMonitor(page)).toHaveCount(0);
    expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads())).toEqual(before);
    await page.evaluate(() => window.__PREVIEW_RECOVERY__.restoreMasterAuthority());
    await expect(avMonitor(page)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Review final AV", exact: true })).toBeVisible();
    expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads())).toEqual(before);
    await page.getByRole("button", { name: "Review final AV", exact: true }).click();
    await expect(avMonitor(page)).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.avReads().length)).toBe(2);
  });
}
test("a late frame from the former scene never becomes the current scene's recovery copy", async ({ page }) => {
  await openNative(page); await durable(page);
  const before = await page.evaluate(() => ({ project: window.__PREVIEW_RECOVERY__.project() }));
  const rows = await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows());
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.hold());
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.seek(3));
  await expect.poll(() => page.evaluate(() => window.__PREVIEW_RECOVERY__.pending())).toBe(1);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.scene(1));
  await expect(historic(page)).toHaveCount(0);
  await expect(native(page)).toHaveCount(0);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.release());
  await expect(historic(page)).toHaveCount(0);
  await expect(native(page)).toHaveCount(0);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(rows);
  await page.evaluate(() => window.__PREVIEW_RECOVERY__.scene(0));
  await recoveredPixels(page, before.project.revision, 1);
  expect(await page.evaluate(() => window.__PREVIEW_RECOVERY__.rows())).toEqual(rows);
});
