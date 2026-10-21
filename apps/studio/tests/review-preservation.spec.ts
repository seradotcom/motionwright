import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { ReviewFixtureRequest } from "./fixtures/review-authority";

// Synthetic component regressions: the canonical Review UI and ProjectMutationGate
// are exercised, but no StudioService, SQLite or native application is invoked.
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const comment = (page: Page) => page.getByLabel("Review comment", { exact: true });
const requests = (page: Page) => page.evaluate(() => window.__REVIEW_AUTHORITY__.requests());
async function submit(page: Page, name = "Add anchored review"): Promise<ReviewFixtureRequest> {
  const before = (await requests(page)).length;
  await button(page, name).click();
  await expect.poll(async () => (await requests(page)).length).toBe(before + 1);
  return (await requests(page))[before];
}
async function remount(page: Page) {
  await page.evaluate(() => window.__REVIEW_AUTHORITY__.unmount());
  await page.evaluate(() => window.__REVIEW_AUTHORITY__.mount());
}
test.beforeEach(async ({ page }) => {
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/tests/fixtures/review-authority.html");
  await expect(page.getByRole("heading", { name: "Review", exact: true })).toBeVisible();
});
test("causal: raw comment and incomplete range survive remount and reload", async ({ page }) => {
  await comment(page).fill("  Author comment\n  Still incomplete  \n");
  await page.getByLabel("Review time anchor", { exact: true }).selectOption("range");
  await page.getByLabel("Review range start", { exact: true }).fill("1e");
  await page.getByLabel("Review range end", { exact: true }).fill("1001/");
  await remount(page);
  await expect(comment(page)).toHaveValue("  Author comment\n  Still incomplete  \n");
  await expect(page.getByLabel("Review range start", { exact: true })).toHaveValue("1e");
  await expect(page.getByLabel("Review range end", { exact: true })).toHaveValue("1001/");
  await page.reload();
  await expect(comment(page)).toHaveValue("  Author comment\n  Still incomplete  \n");
  await expect(page.getByLabel("Review range start", { exact: true })).toHaveValue("1e");
  await expect(page.getByLabel("Review range end", { exact: true })).toHaveValue("1001/");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
});
for (const field of ["id", "generation", "active_branch"] as const) {
  test("causal: raw comment belongs to its exact " + field + " across A-B-A", async ({ page }) => {
    const original = await page.evaluate(field => window.__REVIEW_AUTHORITY__.project()[field], field);
    await comment(page).fill("  Private review A  ");
    await page.evaluate(({ field, value }) => window.__REVIEW_AUTHORITY__.setIdentity(field, value),
      { field, value: "20000000-0000-4000-8000-000000000001" });
    await expect(comment(page)).toHaveValue("");
    await comment(page).fill("Separate review B");
    await page.evaluate(({ field, value }) => window.__REVIEW_AUTHORITY__.setIdentity(field, value), { field, value: original });
    await expect(comment(page)).toHaveValue("  Private review A  ");
    expect(await requests(page)).toEqual([]);
  });
}
test("causal: scene targets do not adopt each other's raw review", async ({ page }) => {
  const ids = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project().scenes.map(scene => scene.id));
  await comment(page).fill("Scene A raw comment");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.selectScene(id), ids[1]);
  await expect(comment(page)).toHaveValue("");
  await comment(page).fill("Scene B raw comment");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.selectScene(id), ids[0]);
  await expect(comment(page)).toHaveValue("Scene A raw comment");
  expect(await requests(page)).toEqual([]);
});
test("causal: original add acknowledgement preserves a later author edit", async ({ page }) => {
  await comment(page).fill("Submitted comment A");
  const request = await submit(page);
  await comment(page).fill("  Later comment B\n");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), request.id);
  await expect(comment(page)).toHaveValue("  Later comment B\n");
  await remount(page);
  await expect(comment(page)).toHaveValue("  Later comment B\n");
  expect(await requests(page)).toHaveLength(1);
});
test("causal: r+1 without the submitted review cannot acknowledge or erase the draft", async ({ page }) => {
  await comment(page).fill("Original comment needs exact review receipt");
  const request = await submit(page);
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolveWithoutOperation(id), request.id);
  await expect(comment(page)).toHaveValue("Original comment needs exact review receipt");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
});
test("causal: resolution raw text survives workspace remount and reload", async ({ page }) => {
  const id = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project().reviews[0].id);
  const field = page.getByLabel("Resolution for review " + id, { exact: true });
  await field.fill("  Original evidence reason  ");
  await remount(page);
  await expect(field).toHaveValue("  Original evidence reason  ");
  await page.reload();
  await expect(field).toHaveValue("  Original evidence reason  ");
  expect(await requests(page)).toEqual([]);
});
test("causal: alternative rationale and direction survive workspace remount and reload", async ({ page }) => {
  await button(page, "Negative study").click();
  await page.getByLabel("Alternative review rationale", { exact: true }).fill("  This specific alternative needs a clearer authored contrast.  ");
  await remount(page);
  await expect(page.getByLabel("Alternative review rationale", { exact: true })).toHaveValue("  This specific alternative needs a clearer authored contrast.  ");
  await expect(button(page, "Negative study")).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.getByLabel("Alternative review rationale", { exact: true })).toHaveValue("  This specific alternative needs a clearer authored contrast.  ");
  await expect(button(page, "Negative study")).toHaveAttribute("aria-pressed", "true");
  expect(await requests(page)).toEqual([]);
});
test("causal: uncertain rejection preserves input and blocks automatic or manual replay", async ({ page }) => {
  await comment(page).fill("Uncertain original review");
  const request = await submit(page);
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.reject(id, "Reply delivery is uncertain"), request.id);
  await expect(comment(page)).toHaveValue("Uncertain original review");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  await remount(page);
  await expect(button(page, "Add anchored review")).toBeDisabled();
  await page.reload();
  await expect(comment(page)).toHaveValue("Uncertain original review");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  expect(await requests(page)).toHaveLength(0);
});
test("causal: denied recovery storage blocks submission and retains current raw input", async ({ page }) => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("motionwright.review-recovery.")) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, key, value);
    };
    window.__REVIEW_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
  });
  await comment(page).fill("  Still here despite storage failure  ");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  await remount(page);
  await expect(comment(page)).toHaveValue("  Still here despite storage failure  ");
  expect(await requests(page)).toEqual([]);
});
test("control: a valid original add response clears only the submitted comment", async ({ page }) => {
  await comment(page).fill("Valid original review");
  const request = await submit(page);
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), request.id);
  await expect(comment(page)).toHaveValue("");
  const saved = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project());
  expect(saved.reviews.some(review => review.body === "Valid original review")).toBe(true);
  expect(saved.revision).toBe(request.source.revision + 1);
});
test("control: a matching observed Project alone leaves the original author input intact", async ({ page }) => {
  await comment(page).fill("Observed state is not original acknowledgement");
  const request = await submit(page);
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.observeOriginal(id), request.id);
  await expect(comment(page)).toHaveValue("Observed state is not original acknowledgement");
  expect(await requests(page)).toHaveLength(1);
});

for (const laterInput of [true, false]) {
  test("transition causal: inspected UNKNOWN retains its original copy before a separate " + (laterInput ? "later intent" : "blank review"), async ({ page }) => {
    await comment(page).fill("Original uncertain comment A");
    const first = await submit(page);
    await page.evaluate(id => window.__REVIEW_AUTHORITY__.observeOriginal(id), first.id);
    await page.evaluate(id => window.__REVIEW_AUTHORITY__.reject(id, "Original delivery remains uncertain"), first.id);
    if (laterInput) await comment(page).fill("  Separate later comment B  ");
    const original = await page.evaluate(() => {
      const project = window.__REVIEW_AUTHORITY__.project(), scope = JSON.stringify([
        project.id, project.generation, project.active_branch, "comment", "scene:" + window.__REVIEW_AUTHORITY__.sceneId(),
      ]);
      const writer = sessionStorage.getItem("motionwright.review-writer.v1:" + encodeURIComponent(scope));
      const key = "motionwright.review-recovery.v1:" + encodeURIComponent(scope) + ":" + writer;
      const raw = localStorage.getItem(key);
      if (!raw) throw new Error("No original review recovery copy");
      return { key, raw, value: JSON.parse(raw) };
    });
    expect(original.value.draft.pending.outcome).toBe("UNKNOWN");
    await expect(button(page, "Read saved review and history")).toBeVisible();
    await button(page, "Read saved review and history").click();
    const inspected = page.getByRole("region", { name: "Saved review outcome", exact: true });
    await expect(inspected).toContainText("do not identify or acknowledge the original request");
    await expect(inspected).toContainText("r" + (first.source.revision + 1));
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await requests(page)).toHaveLength(1);
    await button(page, "Keep original attempt and start a separate review").click();
    await expect(comment(page)).toHaveValue(laterInput ? "  Separate later comment B  " : "");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await requests(page)).toHaveLength(1);
    if (laterInput) {
      const second = await submit(page);
      const current = await page.evaluate(() => Object.keys(localStorage)
        .filter(key => key.startsWith("motionwright.review-recovery.v1:"))
        .map(key => JSON.parse(localStorage.getItem(key)!))
        .find(value => value.draft.pending && value.draft.pending.raw.body === "  Separate later comment B  "));
      expect(current.draft.pending.id).not.toBe(original.value.draft.pending.id);
      expect(second.id).not.toBe(first.id); expect(second.source.revision).toBe(first.source.revision + 1);
      await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), second.id);
      await expect(comment(page)).toHaveValue("");
      expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
      expect((await page.evaluate(() => window.__REVIEW_AUTHORITY__.project())).reviews
        .filter(review => review.body === "Original uncertain comment A" || review.body === "Separate later comment B")).toHaveLength(2);
    } else await expect(button(page, "Add anchored review")).toBeDisabled();
  });
}

test("an exact resolution reply retains later raw reason through reopen and a distinct save", async ({ page }) => {
  const id = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project().reviews[0].id);
  const input = page.getByLabel("Resolution for review " + id, { exact: true });
  await input.fill("Submitted resolution A");
  const first = await submit(page, "Resolve");
  await input.fill("  Later resolution B  ");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), first.id);
  await expect(input).toHaveValue("  Later resolution B  ");
  const reopen = await submit(page, "Reopen for recheck");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), reopen.id);
  await expect(input).toHaveValue("  Later resolution B  ");
  const second = await submit(page, "Resolve");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), second.id);
  await expect(input).toHaveCount(0);
  const saved = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project());
  expect(saved.reviews.find(review => review.id === id)?.resolution).toBe("Later resolution B");
  expect(second.source.revision).toBe(first.source.revision + 2);
});
test("an original alternative receipt retains a later raw editorial rationale", async ({ page }) => {
  const input = page.getByLabel("Alternative review rationale", { exact: true });
  await input.fill("This submitted alternative has a useful authored contrast.");
  const first = await submit(page, "Record manual positive observation");
  await input.fill("  A later distinct reason must remain available to the author.  ");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), first.id);
  await expect(input).toHaveValue("  A later distinct reason must remain available to the author.  ");
  await remount(page);
  await expect(input).toHaveValue("  A later distinct reason must remain available to the author.  ");
  expect(await requests(page)).toHaveLength(1);
});
test("resolution and alternative reasons remain isolated by generation across A-B-A", async ({ page }) => {
  const initial = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project());
  const input = page.getByLabel("Resolution for review " + initial.reviews[0].id, { exact: true });
  await input.fill("  Resolution A  ");
  await page.getByLabel("Alternative review rationale", { exact: true }).fill("  Alternative reason belongs to generation A.  ");
  await page.evaluate(value => window.__REVIEW_AUTHORITY__.setIdentity("generation", value), "20000000-0000-4000-8000-000000000002");
  await expect(input).toHaveValue("");
  await expect(page.getByLabel("Alternative review rationale", { exact: true })).toHaveValue("");
  await input.fill("Resolution B");
  await page.getByLabel("Alternative review rationale", { exact: true }).fill("Alternative reason belongs to generation B.");
  await page.evaluate(value => window.__REVIEW_AUTHORITY__.setIdentity("generation", value), initial.generation);
  await expect(input).toHaveValue("  Resolution A  ");
  await expect(page.getByLabel("Alternative review rationale", { exact: true })).toHaveValue("  Alternative reason belongs to generation A.  ");
  expect(await requests(page)).toEqual([]);
});
test("scene and whole-project review buffers stay separate when the author changes scope", async ({ page }) => {
  await comment(page).fill("Scene-only author input");
  await page.getByLabel("Review resource scope", { exact: true }).selectOption("project");
  await expect(comment(page)).toHaveValue("");
  await comment(page).fill("Whole-project author input");
  await page.getByLabel("Review resource scope", { exact: true }).selectOption("selection");
  await expect(comment(page)).toHaveValue("Scene-only author input");
  await page.getByLabel("Review resource scope", { exact: true }).selectOption("project");
  await expect(comment(page)).toHaveValue("Whole-project author input");
  expect(await requests(page)).toEqual([]);
});
for (const loss of ["bytes", "pointer"] as const) {
  test("silent " + loss + " write loss blocks mutation until a verified retry keeps the raw input", async ({ page }) => {
    await page.evaluate(loss => {
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = function(key, value) {
        if (key.startsWith(loss === "bytes" ? "motionwright.review-recovery.v1:" : "motionwright.review-writer.v1:")) return;
        return original.call(this, key, value);
      };
      window.__REVIEW_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
    }, loss);
    await comment(page).fill("  Raw input through silent storage loss  ");
    await expect(button(page, "Add anchored review")).toBeDisabled();
    await expect(page.getByRole("alert").filter({ hasText: "not confirmed" })).toHaveCount(1);
    expect(await requests(page)).toEqual([]);
    await page.evaluate(() => window.__REVIEW_TEST_RESTORE__!());
    await button(page, "Retry review recovery copy").click();
    await expect(comment(page)).toHaveValue("  Raw input through silent storage loss  ");
    const request = await submit(page);
    await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), request.id);
    await expect(comment(page)).toHaveValue("");
  });
}
test("a full recovery catalogue retains prior copies and permits faithful export of blocked raw input", async ({ page }, info) => {
  await page.evaluate(() => {
    for (let index = 0; index < 256; index++) localStorage.setItem("motionwright.review-recovery.v1:retained-" + index, "prior-" + index);
  });
  await comment(page).fill("  Input kept while the recovery catalogue is full\n");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  const original = await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.review-recovery.v1:"))
    .sort().map(key => [key, localStorage.getItem(key)]));
  expect(original).toHaveLength(256);
  await remount(page);
  await expect(comment(page)).toHaveValue("  Input kept while the recovery catalogue is full\n");
  const downloaded = page.waitForEvent("download");
  await button(page, "Export review recovery copy").click();
  const download = await downloaded, path = info.outputPath("blocked-review-author-input.json");
  await download.saveAs(path);
  const record = JSON.parse(await readFile(path, "utf8"));
  expect(record.draft.raw.body).toBe("  Input kept while the recovery catalogue is full\n");
  expect(record.draft.pending).toBeNull();
  expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.startsWith("motionwright.review-recovery.v1:"))
    .sort().map(key => [key, localStorage.getItem(key)]))).toEqual(original);
});
test("a late original response cannot erase another project or silently clear its original UNKNOWN", async ({ page }) => {
  const original = await page.evaluate(() => window.__REVIEW_AUTHORITY__.project().id);
  await comment(page).fill("Original project pending comment");
  const first = await submit(page);
  await page.evaluate(value => window.__REVIEW_AUTHORITY__.setIdentity("id", value), "20000000-0000-4000-8000-000000000002");
  await comment(page).fill("Different project current input");
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), first.id);
  await expect(comment(page)).toHaveValue("Different project current input");
  await page.evaluate(value => window.__REVIEW_AUTHORITY__.setIdentity("id", value), original);
  await expect(comment(page)).toHaveValue("Original project pending comment");
  await expect(button(page, "Add anchored review")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
});
test("a later saved revision requires explicit comparison while retaining its earlier raw copy", async ({ page }) => {
  await comment(page).fill("  Authored against the earlier source  ");
  const original = await page.evaluate(() => {
    const key = Object.keys(localStorage).find(key => key.startsWith("motionwright.review-recovery.v1:"))!;
    return { key, raw: localStorage.getItem(key) };
  });
  await page.evaluate(() => window.__REVIEW_AUTHORITY__.externalChange({ type: "rename_project", title: "Independent later source" }));
  await expect(button(page, "Add anchored review")).toBeDisabled();
  await expect(comment(page)).toHaveValue("  Authored against the earlier source  ");
  await button(page, "Review draft on current revision").click();
  await expect(comment(page)).toHaveValue("  Authored against the earlier source  ");
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  const request = await submit(page);
  await page.evaluate(id => window.__REVIEW_AUTHORITY__.resolve(id), request.id);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
});
test("native UTF-8 comment limits reject an unsaveable request without truncation or transport", async ({ page }) => {
  const raw = "\u{1F642}".repeat(2001);
  await comment(page).fill(raw);
  await button(page, "Add anchored review").click();
  await expect(comment(page)).toHaveValue(raw);
  await expect(page.getByRole("alert").filter({ hasText: "domain contract" })).toHaveCount(1);
  expect(await requests(page)).toEqual([]);
});
