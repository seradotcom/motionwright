import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import type { ProfileFixtureRequest } from "./fixtures/delivery-profile-authority";

// Actual DeliveryProfiles, browser API and canonical ProjectMutationGate.
// Controlled replies and browser storage are synthetic; no real service runs.
const field = (page: Page, name: string) => page.getByRole("textbox", { name, exact: true });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const review = (page: Page) => page.getByRole("region", { name: "Output profile draft review", exact: true });
const requests = (page: Page) => page.evaluate(() => window.__PROFILE_AUTHORITY__.requests());
async function submit(page: Page): Promise<ProfileFixtureRequest> {
  const count = (await requests(page)).length;
  await button(page, "Save profile").click(); await expect.poll(async () => (await requests(page)).length).toBe(count + 1);
  return (await requests(page))[count];
}
async function remount(page: Page) {
  await page.evaluate(() => window.__PROFILE_AUTHORITY__.unmount());
  await page.evaluate(() => window.__PROFILE_AUTHORITY__.mount());
}
test.beforeEach(async ({ page }) => {
  page.on("dialog", dialog => dialog.accept());
  await page.goto("/tests/fixtures/delivery-profile-authority.html");
  await expect(field(page, "Profile name")).toBeVisible();
});
test("causal: matching observed profile cannot consume the unresolved original capture", async ({ page }) => {
  await field(page, "Profile name").fill("Original pending profile");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.observeOriginal(id), request.id);
  await expect(review(page)).toContainText("Input sent");
  await expect(field(page, "Profile name")).toHaveValue("Original pending profile");
  expect(await requests(page)).toHaveLength(1);
});
test("causal: matching observation then rejected original cannot release a later save", async ({ page }) => {
  await field(page, "Profile name").fill("Original uncertain profile");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.observeOriginal(id), request.id);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.reject(id, "Original delivery is uncertain"), request.id);
  await field(page, "Profile name").fill("  Later profile intent  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  await expect(review(page)).toContainText("Input sent");
  expect(await requests(page)).toHaveLength(1);
});
test("causal: later edit back to the submitted spelling survives original acknowledgement", async ({ page }) => {
  await field(page, "Profile name").fill("Submitted spelling");
  const request = await submit(page);
  await field(page, "Profile name").fill("Different later spelling");
  await field(page, "Profile name").fill("Submitted spelling");
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolve(id), request.id);
  await expect(review(page)).toContainText("1 output field(s) have pending input.");
  await expect(field(page, "Profile name")).toHaveValue("Submitted spelling");
  expect(await requests(page)).toHaveLength(1);
});
test("causal: quota failure blocks metadata transport without losing raw input", async ({ page }) => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("motionwright.delivery-profile-draft/1:")) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, key, value);
    };
    window.__PROFILE_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
  });
  await field(page, "Profile name").fill("  Retained after quota failure  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  await remount(page);
  await expect(field(page, "Profile name")).toHaveValue("  Retained after quota failure  ");
  expect(await requests(page)).toEqual([]);
});
test("causal: silent metadata pointer failure blocks transport", async ({ page }) => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("motionwright.delivery-profile-writer.v1:")) return;
      return original.call(this, key, value);
    };
    window.__PROFILE_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
  });
  await field(page, "Profile name").fill("  Verify the tab pointer  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
});
test("causal: resolving one field cannot discard an UNKNOWN original attempt", async ({ page }) => {
  await field(page, "Profile name").fill("Unknown original still owns this capture");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.reject(id, "Uncertain original"), request.id);
  await expect(button(page, "Keep my input name")).toBeDisabled();
  await expect(button(page, "Keep saved name")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
});
test("control: an original valid upsert reply confirms submitted metadata", async ({ page }) => {
  await field(page, "Profile name").fill("Confirmed metadata");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolve(id), request.id);
  await expect(field(page, "Profile name")).toHaveValue("Confirmed metadata");
  await expect(button(page, "Save profile")).toBeDisabled();
  await expect(review(page)).toContainText("Showing saved output values");
});
test("control: invalid pixel spelling remains exact across remount and reload", async ({ page }) => {
  await field(page, "Width").fill("1e");
  await remount(page); await expect(field(page, "Width")).toHaveValue("1e");
  await page.reload(); await expect(field(page, "Width")).toHaveValue("1e");
  expect(await requests(page)).toEqual([]);
});

async function ownProfileCopy(page: Page) {
  return page.evaluate(() => {
    const project = window.__PROFILE_AUTHORITY__.project(), profile = project.deliverables[0];
    const scope = JSON.stringify([project.id, project.generation, project.active_branch, profile.id]);
    const writer = sessionStorage.getItem("motionwright.delivery-profile-writer.v1:" + scope);
    if (!writer) throw new Error("No verified profile writer pointer.");
    const key = "motionwright.delivery-profile-draft/1:" + scope + ":" + writer, raw = localStorage.getItem(key);
    if (raw === null) throw new Error("No original profile recovery bytes.");
    return { key, raw, value: JSON.parse(raw) };
  });
}
async function inspectSavedProfile(page: Page) {
  await button(page, "Read saved profile and history").click();
  await expect(page.getByRole("region", { name: "Saved profile outcome", exact: true })).toBeVisible();
}
test("transition causal: a new later intent keeps the original UNKNOWN bytes and never replays it", async ({ page }) => {
  await field(page, "Profile name").fill("Uncertain original profile");
  const request = await submit(page);
  await field(page, "Profile name").fill("  Distinct later profile intent  ");
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.observeOriginal(id), request.id);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.reject(id, "Original reply delivery was lost"), request.id);
  const original = await ownProfileCopy(page);
  await expect(button(page, "Save profile")).toBeDisabled();
  await inspectSavedProfile(page);
  await button(page, "Keep original attempt and start a separate profile edit").click();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  await expect(field(page, "Profile name")).toHaveValue("  Distinct later profile intent  ");
  expect(await requests(page)).toHaveLength(1);
  const separate = await ownProfileCopy(page);
  expect(separate.key).not.toBe(original.key); expect(separate.value.awaiting).toBeUndefined();
  const later = await submit(page), pending = await ownProfileCopy(page);
  expect(pending.value.awaiting.original.id).not.toBe(original.value.awaiting.original.id);
  expect(later.source.revision).toBe(request.source.revision + 1);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolve(id), later.id);
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  await expect(field(page, "Profile name")).toHaveValue("  Distinct later profile intent  ");
  expect(await requests(page)).toHaveLength(2);
});
test("transition causal: inspection of an unchanged UNKNOWN starts clean without rewriting or resending its capture", async ({ page }) => {
  await field(page, "Profile name").fill("Saved but unacknowledged profile");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.observeOriginal(id), request.id);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.reject(id, "Lost original reply"), request.id);
  const original = await ownProfileCopy(page);
  await remount(page); await expect(button(page, "Save profile")).toBeDisabled();
  await inspectSavedProfile(page);
  await button(page, "Keep original attempt and start a separate profile edit").click();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  await expect(review(page)).toContainText("Showing saved output values");
  await expect(button(page, "Save profile")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
  await page.reload(); await expect(field(page, "Profile name")).toHaveValue("Saved but unacknowledged profile");
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  expect(await requests(page)).toEqual([]);
});

test("a reply without the submitted upsert stays UNKNOWN after reload", async ({ page }) => {
  await field(page, "Profile name").fill("Not in the reply");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolveWithoutOperation(id), request.id);
  await expect(review(page)).toContainText("exact profile upsert");
  await expect(button(page, "Save profile")).toBeDisabled();
  const original = await ownProfileCopy(page); await page.reload();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  await expect(field(page, "Profile name")).toHaveValue("Not in the reply");
  await expect(button(page, "Save profile")).toBeDisabled(); expect(await requests(page)).toEqual([]);
});
test("an original reply after editor unmount acknowledges only its own retained capture", async ({ page }) => {
  await field(page, "Profile name").fill("Submitted before leaving");
  const request = await submit(page);
  await field(page, "Profile name").fill("  Later before unmount  "); await field(page, "Width").fill("1e");
  await page.evaluate(() => window.__PROFILE_AUTHORITY__.unmount());
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolve(id), request.id);
  await page.evaluate(() => window.__PROFILE_AUTHORITY__.mount());
  await expect(field(page, "Profile name")).toHaveValue("  Later before unmount  ");
  await expect(field(page, "Width")).toHaveValue("1e");
  await expect(review(page)).not.toContainText("Input sent");
  expect((await ownProfileCopy(page)).value.awaiting).toBeUndefined(); expect(await requests(page)).toHaveLength(1);
});
test("the original A reply preserves selected profile B and both sets of later input", async ({ page }) => {
  const project = await page.evaluate(() => window.__PROFILE_AUTHORITY__.project());
  expect(project.deliverables.length).toBeGreaterThan(1);
  await field(page, "Profile name").fill("A submitted");
  const request = await submit(page); await field(page, "Profile name").fill("  A later  ");
  await page.locator('.delivery-profile-row[data-profile-id="' + project.deliverables[1].id + '"]').click();
  await field(page, "Profile name").fill("  B remains selected  ");
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolve(id), request.id);
  await expect(field(page, "Profile name")).toHaveValue("  B remains selected  ");
  await page.locator('.delivery-profile-row[data-profile-id="' + project.deliverables[0].id + '"]').click();
  await expect(field(page, "Profile name")).toHaveValue("  A later  ");
  await expect(review(page)).not.toContainText("Input sent"); expect(await requests(page)).toHaveLength(1);
});
for (const identity of ["id", "generation", "active_branch"] as const) {
  test("raw metadata stays separate across A-B-A " + identity + " selection", async ({ page }) => {
    const project = await page.evaluate(() => window.__PROFILE_AUTHORITY__.project());
    await field(page, "Profile name").fill("  First scope raw  "); await field(page, "Width").fill("1e");
    const original = await ownProfileCopy(page);
    await page.evaluate(field => window.__PROFILE_AUTHORITY__.setIdentity(field, crypto.randomUUID()), identity);
    await expect(field(page, "Width")).not.toHaveValue("1e"); await field(page, "Profile name").fill("  Second scope raw  ");
    await page.evaluate(({ field, value }) => window.__PROFILE_AUTHORITY__.setIdentity(field, value), { field: identity, value: project[identity] });
    await expect(field(page, "Profile name")).toHaveValue("  First scope raw  ");
    await expect(field(page, "Width")).toHaveValue("1e");
    expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
    expect(await requests(page)).toEqual([]);
  });
}
test("silent own-copy loss blocks Save and preserves the live exact input", async ({ page }) => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("motionwright.delivery-profile-draft/1:")) return;
      return original.call(this, key, value);
    };
    window.__PROFILE_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
  });
  await field(page, "Profile name").fill("  Silent write retained  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  await remount(page); await expect(field(page, "Profile name")).toHaveValue("  Silent write retained  ");
  expect(await requests(page)).toEqual([]);
});
test("explicit verified recovery after quota failure never sends until a separate Save", async ({ page }) => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("motionwright.delivery-profile-draft/1:")) throw new DOMException("quota", "QuotaExceededError");
      return original.call(this, key, value);
    };
    window.__PROFILE_TEST_RESTORE__ = () => { Storage.prototype.setItem = original; };
  });
  await field(page, "Profile name").fill("  Quota recovery input  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  await page.evaluate(() => window.__PROFILE_TEST_RESTORE__!());
  await button(page, "Verify profile recovery copy").click();
  expect(await requests(page)).toEqual([]);
  await expect(button(page, "Save profile")).toBeEnabled();
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.resolve(id), request.id);
  await expect(field(page, "Profile name")).toHaveValue("  Quota recovery input  ");
  expect(await requests(page)).toHaveLength(1);
});
test("a foreign writer pointer is preserved and blocks another profile save", async ({ page }) => {
  await field(page, "Profile name").fill("First writer raw");
  const before = await ownProfileCopy(page);
  const changed = await page.evaluate(() => {
    const project = window.__PROFILE_AUTHORITY__.project(), scope = JSON.stringify([project.id, project.generation, project.active_branch, project.deliverables[0].id]);
    const key = "motionwright.delivery-profile-writer.v1:" + scope, value = crypto.randomUUID();
    sessionStorage.setItem(key, value); return { key, value };
  });
  await field(page, "Profile name").fill("  Retained after pointer changed  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  expect(await page.evaluate(key => sessionStorage.getItem(key), changed.key)).toBe(changed.value);
  expect(JSON.parse((await page.evaluate(key => localStorage.getItem(key), before.key))!).edits.name.value).toBe("  Retained after pointer changed  ");
  expect(await requests(page)).toEqual([]);
});
test("the 64-copy budget blocks a new save without evicting retained originals", async ({ page }) => {
  const originals = await page.evaluate(() => {
    const rows: { key: string; raw: string }[] = [];
    for (let index = 0; index < 64; index++) {
      const scope = JSON.stringify([crypto.randomUUID(), crypto.randomUUID(), crypto.randomUUID(), crypto.randomUUID()]);
      const key = "motionwright.delivery-profile-draft/1:" + scope + ":" + crypto.randomUUID();
      const raw = JSON.stringify({ version: 1, scope, revision: 0, edits: { name: { base: '"Before"', value: "Keep " + index } } });
      localStorage.setItem(key, raw); rows.push({ key, raw });
    }
    return rows;
  });
  await field(page, "Profile name").fill("  Overflow stays in this window  ");
  await expect(button(page, "Save profile")).toBeDisabled();
  await expect(review(page)).toContainText("64-copy");
  await remount(page); await expect(field(page, "Profile name")).toHaveValue("  Overflow stays in this window  ");
  expect(await page.evaluate(rows => rows.map(row => ({ key: row.key, raw: localStorage.getItem(row.key) })), originals)).toEqual(originals);
  expect(await requests(page)).toEqual([]);
});
test("downloaded profile input preserves exact incomplete text without granting a save", async ({ page }, info) => {
  await field(page, "Width").fill("  1e  "); await field(page, "Profile name").fill("  Download raw  ");
  const current = await ownProfileCopy(page), pending = page.waitForEvent("download");
  await button(page, "Download current profile input").click();
  const download = await pending, path = info.outputPath("profile-exact-input.json"); await download.saveAs(path);
  const raw = await readFile(path, "utf8");
  expect(raw).toBe(current.raw); expect(JSON.parse(raw).edits.width.value).toBe("  1e  ");
  expect(await requests(page)).toEqual([]);
});
test("a changed saved source invalidates a prior UNKNOWN inspection before separation", async ({ page }) => {
  await field(page, "Profile name").fill("Uncertain saved profile");
  const request = await submit(page);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.observeOriginal(id), request.id);
  await page.evaluate(id => window.__PROFILE_AUTHORITY__.reject(id, "Lost reply"), request.id);
  await inspectSavedProfile(page); const original = await ownProfileCopy(page);
  await page.evaluate(() => window.__PROFILE_AUTHORITY__.externalChange({ type: "rename_project", title: "Source changed after inspection" }));
  await expect(button(page, "Keep original attempt and start a separate profile edit")).toHaveCount(0);
  await expect(button(page, "Save profile")).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), original.key)).toBe(original.raw);
  expect(await requests(page)).toHaveLength(1);
});
