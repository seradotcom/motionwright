import { expect, test, type Page } from "@playwright/test";
import type { NarrativeFixtureRequest } from "./fixtures/narrative-authority";

// These regressions mount the actual exported Narrative component. Delayed
// browser API replies are fixture scheduling, not service-persistence evidence.
const premise = (page: Page) => page.getByLabel("Narrative premise", { exact: true });
const objective = (page: Page) => page.getByLabel("Scene objective", { exact: true });
const button = (page: Page, name: string) => page.getByRole("button", { name, exact: true });
const requests = (page: Page) => page.evaluate(() => window.__NARRATIVE_AUTHORITY__.requests());
async function commit(page: Page, field: "premise" | "objective"): Promise<NarrativeFixtureRequest> {
  const before = (await requests(page)).length;
  await button(page, "Commit " + field).click();
  await expect.poll(async () => (await requests(page)).length).toBe(before + 1);
  return (await requests(page))[before];
}
async function open(page: Page) {
  await page.goto("/tests/fixtures/narrative-authority.html");
  await expect(page.getByRole("heading", { name: "Narrative", exact: true })).toBeVisible();
}
test.beforeEach(async ({ page }) => open(page));

test("premise and objective raw inputs survive workspace remount and a full page reload", async ({ page }) => {
  const texts = ["  Unsent premise\n\nKeep author spacing  \n", "  Unsent scene objective\nSecond line  \n"];
  await premise(page).fill(texts[0]); await objective(page).fill(texts[1]);
  await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.unmount());
  await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.mount());
  await expect(premise(page)).toHaveValue(texts[0]); await expect(objective(page)).toHaveValue(texts[1]);
  await page.reload();
  await expect(premise(page)).toHaveValue(texts[0]); await expect(objective(page)).toHaveValue(texts[1]);
  expect(await requests(page)).toEqual([]);
});

for (const field of ["id", "generation", "active_branch"] as const) {
  test(`equal saved text under a different ${field} cannot adopt A's premise or objective`, async ({ page }) => {
    const initial = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
    const selected = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.sceneId());
    const savedObjective = initial.scenes.find(scene => scene.id === selected)!.objective;
    await premise(page).fill("Private premise draft A"); await objective(page).fill("Private objective draft A");
    await page.evaluate(({ field, value }) => window.__NARRATIVE_AUTHORITY__.setScope({ [field]: value }), {
      field, value: initial[field] + "-B",
    });
    await expect(premise(page)).toHaveValue(initial.narrative.premise);
    await expect(objective(page)).toHaveValue(savedObjective);
    await premise(page).fill("Separate premise draft B");
    await page.evaluate(({ field, value }) => window.__NARRATIVE_AUTHORITY__.setScope({ [field]: value }), { field, value: initial[field] });
    await expect(premise(page)).toHaveValue("Private premise draft A");
    await expect(objective(page)).toHaveValue("Private objective draft A");
    expect(await requests(page)).toEqual([]);
  });
}

test("scene selection keeps the project premise shared and restores each scene's separate objective", async ({ page }) => {
  const initial = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
  const first = initial.scenes[0], second = initial.scenes[1];
  await premise(page).fill("One project premise across scene selection");
  await objective(page).fill("Objective draft for scene A");
  await page.evaluate(sceneId => window.__NARRATIVE_AUTHORITY__.setScope({ sceneId }), second.id);
  await expect(premise(page)).toHaveValue("One project premise across scene selection");
  await expect(objective(page)).toHaveValue(second.objective);
  await objective(page).fill("Objective draft for scene B");
  await page.evaluate(sceneId => window.__NARRATIVE_AUTHORITY__.setScope({ sceneId }), first.id);
  await expect(objective(page)).toHaveValue("Objective draft for scene A");
  await page.evaluate(sceneId => window.__NARRATIVE_AUTHORITY__.setScope({ sceneId }), second.id);
  await expect(objective(page)).toHaveValue("Objective draft for scene B");
  expect(await requests(page)).toEqual([]);
});

for (const field of ["premise", "objective"] as const) {
  test(`the original ${field} reply keeps later typing through remount and a second explicit save works`, async ({ page }) => {
    const input = field === "premise" ? premise(page) : objective(page);
    await input.fill("Original submitted " + field);
    const first = await commit(page, field);
    await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.unmount());
    await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.mount());
    const later = "  Later " + field + " decision\nKept exactly  \n";
    await input.fill(later);
    await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), first.id);
    await expect(input).toHaveValue(later);
    await expect(page.getByRole("region", { name: "Unconfirmed " + field + " submission", exact: true })).toHaveCount(0);
    await expect(button(page, "Commit " + field)).toBeEnabled();
    const second = await commit(page, field);
    expect(second.source.revision).toBe(first.source.revision + 1);
    expect(second.change).toEqual(field === "premise" ? { type: "set_narrative_premise", premise: later }
      : { type: "update_scene_objective", scene_id: first.source.scenes[0].id, objective: later.trim() });
    await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), second.id);
    await expect(input).toHaveValue(field === "premise" ? later : later.trim());
    await expect(button(page, "Commit " + field)).toBeDisabled();
    expect(await requests(page)).toHaveLength(2);
  });

  test(`returning ${field} to its old saved value during an original save remains a new author decision`, async ({ page }) => {
    const input = field === "premise" ? premise(page) : objective(page), original = await input.inputValue();
    await input.fill("Submitted replacement " + field);
    const request = await commit(page, field);
    await input.fill(original);
    await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), request.id);
    await expect(input).toHaveValue(original);
    await expect(button(page, "Commit " + field)).toBeEnabled();
    await page.reload();
    await expect(input).toHaveValue(original);
    expect(await requests(page)).toEqual([]);
  });
}

test("an uncertain save retains submitted and later input across reload without automatic resend", async ({ page }) => {
  await premise(page).fill("Submitted value whose reply is unavailable");
  const request = await commit(page, "premise");
  await premise(page).fill("  New author input after submission\n");
  await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.reject(id, "Original reply was lost"), request.id);
  await expect(page.getByRole("region", { name: "Unconfirmed premise submission", exact: true })).toContainText("Original reply was lost");
  await expect(button(page, "Commit premise")).toBeDisabled();
  await page.reload();
  await expect(premise(page)).toHaveValue("  New author input after submission\n");
  await expect(page.getByRole("region", { name: "Unconfirmed premise submission", exact: true })).toContainText("Original reply was lost");
  await expect(button(page, "Commit premise")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  const pending = await page.evaluate(() => {
    for (let index = 0; index < localStorage.length; index++) {
      const key = localStorage.key(index);
      if (key?.startsWith("motionwright.narrative-recovery.v1:")) {
        const value = JSON.parse(localStorage.getItem(key)!);
        if (value.draft.pending?.raw === "Submitted value whose reply is unavailable") return value.draft;
      }
    }
    return null;
  });
  expect(pending?.pending.outcome).toBe("UNKNOWN");
  expect(pending?.raw).toBe("  New author input after submission\n");
});

test("a silently failed writer pointer blocks dispatch; an explicit readable-copy restore remains usable", async ({ page }) => {
  const raw = "Recovery survives a silent browser pointer failure";
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key: string, value: string) {
      if (this === window.sessionStorage && key.startsWith("motionwright.narrative-writer.v1:")) return;
      original.call(this, key, value);
    };
  });
  await premise(page).fill(raw);
  await expect(page.getByRole("alert", { name: "Narrative premise backup warning", exact: true })).toContainText("pointer was not confirmed");
  await expect(button(page, "Commit premise")).toBeDisabled();
  expect(await requests(page)).toEqual([]);
  await page.reload();
  await expect(premise(page)).not.toHaveValue(raw);
  const recovery = page.getByRole("region", { name: "Narrative premise recovery", exact: true });
  await recovery.locator("summary").click();
  await expect(recovery).toContainText(raw);
  await button(page, "Restore Narrative premise copy 1").click();
  await expect(premise(page)).toHaveValue(raw);
  await expect(button(page, "Commit premise")).toBeEnabled();
  const request = await commit(page, "premise");
  await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), request.id);
  await expect(premise(page)).toHaveValue(raw);
  await expect(button(page, "Commit premise")).toBeDisabled();
  expect(await requests(page)).toHaveLength(1);
});

for (const interleaving of ["pointer publication", "budget scan"] as const) {
  test(`a Narrative copy replaced during ${interleaving} prevents actual save dispatch and preserves the editor`, async ({ page }) => {
    const raw = "  Author input retained despite a custody interleaving\n";
    const before = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
    await premise(page).fill(raw);
    await expect(button(page, "Commit premise")).toBeEnabled();
    const foreignRaw = "OTHER WRITER ORIGINAL BYTES";
    const ownKey = await page.evaluate(({ interleaving, foreignRaw }) => {
      const pointerPrefix = "motionwright.narrative-writer.v1:";
      const copyPrefix = "motionwright.narrative-recovery.v1:";
      const pointerKey = Object.keys(sessionStorage).find(key => key.startsWith(pointerPrefix) && key.includes("premise"))!;
      const writerId = sessionStorage.getItem(pointerKey)!;
      const ownKey = copyPrefix + pointerKey.slice(pointerPrefix.length) + ":" + writerId;
      const setItem = Storage.prototype.setItem, getItem = Storage.prototype.getItem;
      if (interleaving === "pointer publication") {
        Storage.prototype.setItem = function(key: string, value: string) {
          setItem.call(this, key, value);
          if (this !== sessionStorage || key !== pointerKey) return;
          const current = getItem.call(localStorage, ownKey);
          // The input's ordinary backup succeeded. Replace only the armed
          // UNKNOWN record while the actual save publishes its pointer.
          if (current && JSON.parse(current).draft.pending?.outcome === "UNKNOWN")
            setItem.call(localStorage, ownKey, foreignRaw);
        };
      } else {
        const unrelatedKey = copyPrefix + "unrelated-budget-probe-" + crypto.randomUUID();
        setItem.call(localStorage, unrelatedKey, "Unrelated budget entry");
        const keyAt = Storage.prototype.key;
        let changed = false;
        Storage.prototype.key = function(index: number) {
          const key = keyAt.call(this, index);
          if (!changed && this === localStorage && key === unrelatedKey) {
            changed = true; setItem.call(localStorage, ownKey, foreignRaw);
          }
          return key;
        };
      }
      return ownKey;
    }, { interleaving, foreignRaw });
    await button(page, "Commit premise").click();
    // These are calls from the real mounted editor, not a copied state model.
    // RED: the old helper returns success and the component dispatches once.
    expect(await requests(page)).toEqual([]);
    await expect(premise(page)).toHaveValue(raw);
    const warning = page.getByRole("alert", { name: "Narrative premise backup warning", exact: true });
    await expect(warning).toContainText(interleaving === "pointer publication"
      ? "copy changed while its writer pointer was published" : "copy changed in another window");
    await expect(button(page, "Commit premise")).toBeDisabled();
    await expect(page.getByText("This premise preparation was not sent. Confirm its backup before a separate explicit save.", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(before);
    expect(await page.evaluate(key => localStorage.getItem(key), ownKey)).toBe(foreignRaw);
    await button(page, "Retry premise backup").click();
    await expect(warning).toContainText("copy changed in another window");
    await expect(premise(page)).toHaveValue(raw);
    await expect(button(page, "Commit premise")).toBeDisabled();
    expect(await requests(page)).toEqual([]);
    expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(before);
    expect(await page.evaluate(key => localStorage.getItem(key), ownKey)).toBe(foreignRaw);
  });
}

for (const choice of ["saved", "draft"] as const) {
  test(`overlapping saved premise keeps input until explicit ${choice} choice`, async ({ page }) => {
    await premise(page).fill("Local unsent premise");
    await objective(page).fill("Unrelated local objective stays available");
    await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.externalChange({ type: "set_narrative_premise", premise: "Peer saved premise" }));
    const conflict = page.getByRole("region", { name: "Narrative premise conflict", exact: true });
    await expect(conflict).toContainText("Peer saved premise");
    await expect(premise(page)).toHaveValue("Local unsent premise");
    await expect(button(page, "Commit premise")).toBeDisabled();
    await button(page, choice === "saved" ? "Keep saved Narrative premise" : "Keep my draft Narrative premise").click();
    await expect(premise(page)).toHaveValue(choice === "saved" ? "Peer saved premise" : "Local unsent premise");
    await expect(objective(page)).toHaveValue("Unrelated local objective stays available");
    await expect(conflict).toHaveCount(0);
    expect(await requests(page)).toEqual([]);
  });
}

test("two windows keep separate writers and cannot adopt the other's more recent input on reload", async ({ page, context }) => {
  await premise(page).fill("Window A original author bytes");
  const second = await context.newPage();
  try {
    await open(second);
    await expect(premise(second)).not.toHaveValue("Window A original author bytes");
    await premise(second).fill("Window B independent author bytes");
    await page.reload(); await second.reload();
    await expect(premise(page)).toHaveValue("Window A original author bytes");
    await expect(premise(second)).toHaveValue("Window B independent author bytes");
    expect(await requests(page)).toEqual([]); expect(await requests(second)).toEqual([]);
  } finally { await second.close(); }
});

test("a corrupt own copy is retained and never replaced by another window's readable input", async ({ page, context }) => {
  await premise(page).fill("Original window A input");
  const second = await context.newPage();
  try {
    await open(second); await premise(second).fill("Another window's readable input");
    const key = await page.evaluate(() => {
      const pointerKey = Object.keys(sessionStorage).find(key => key.startsWith("motionwright.narrative-writer.v1:") && key.includes("premise"))!;
      const writer = sessionStorage.getItem(pointerKey)!;
      const copyKey = "motionwright.narrative-recovery.v1:" + pointerKey.slice("motionwright.narrative-writer.v1:".length) + ":" + writer;
      localStorage.setItem(copyKey, "CORRUPT ORIGINAL BYTES"); return copyKey;
    });
    await page.reload();
    await expect(premise(page)).not.toHaveValue("Another window's readable input");
    await expect(page.getByRole("alert", { name: "Narrative premise backup warning", exact: true })).toBeVisible();
    expect(await page.evaluate(key => localStorage.getItem(key), key)).toBe("CORRUPT ORIGINAL BYTES");
    expect(await requests(page)).toEqual([]);
  } finally { await second.close(); }
});


for (const field of ["premise", "objective"] as const) {
  test(`explicitly restoring an old clean ${field} checkpoint preserves its text for conflict review and never acknowledges an UNKNOWN copy`, async ({ page }) => {
    const label = field === "premise" ? "Narrative premise" : "Scene objective";
    const input = field === "premise" ? premise(page) : objective(page);
    const a = "Author checkpoint A for " + field, b = "Author checkpoint B for " + field;
    await input.fill(a);
    const first = await commit(page, field);
    await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), first.id);
    await expect(input).toHaveValue(a); await expect(button(page, "Commit " + field)).toBeDisabled();
    // Read a checkpoint produced by the actual original ACK; do not construct
    // a helper state or inject a recovery record to manufacture the regression.
    const cleanA = await page.evaluate(({ field, a }) => {
      for (const [key, raw] of Object.entries(localStorage)) {
        if (!key.startsWith("motionwright.narrative-recovery.v1:")) continue;
        const value = JSON.parse(raw);
        if (value.draft?.target.kind === field && value.draft.raw === a && !value.draft.intent && value.draft.pending === null)
          return { key, raw, value };
      }
      throw new Error("The actual original ACK did not retain clean checkpoint A");
    }, { field, a });
    expect(cleanA.value.draft.base).toBe(a); expect(cleanA.value.draft.baseRevision).toBe(first.source.revision + 1);
    await input.fill(b);
    const second = await commit(page, field);
    await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), second.id);
    await expect(input).toHaveValue(b); await expect(button(page, "Commit " + field)).toBeDisabled();
    const savedB = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
    const recovery = page.getByRole("region", { name: label + " recovery", exact: true });
    await recovery.locator("summary").first().click();
    const cleanGroup = recovery.getByRole("group").filter({ hasText: a }).filter({ hasText: "· Local input" });
    await expect(cleanGroup).toHaveCount(1);
    await cleanGroup.getByRole("button", { name: new RegExp("^Restore " + label + " copy ") }).click();
    await expect(input).toHaveValue(a); // RED: old code silently replaces the selected A with saved B.
    await expect(page.getByRole("region", { name: label + " conflict", exact: true })).toBeVisible();
    await expect(button(page, "Commit " + field)).toBeDisabled();
    expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(savedB);
    expect(await requests(page)).toHaveLength(2);
    expect(await page.evaluate(key => localStorage.getItem(key), cleanA.key)).toBe(cleanA.raw);
    await button(page, "Keep my draft " + label).click();
    expect(await requests(page)).toHaveLength(2);
    expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(savedB);
    const third = await commit(page, field);
    expect(third.source.revision).toBe(savedB.revision);
    expect(third.change).toEqual(field === "premise" ? { type: "set_narrative_premise", premise: a }
      : { type: "update_scene_objective", scene_id: first.source.scenes[0].id, objective: a });
    await page.evaluate(id => window.__NARRATIVE_AUTHORITY__.resolve(id), third.id);
    await expect(input).toHaveValue(a); await expect(button(page, "Commit " + field)).toBeDisabled();
    const savedA = await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project());
    const armedB = await page.evaluate(({ field, b }) => {
      for (const [key, raw] of Object.entries(localStorage)) {
        if (!key.startsWith("motionwright.narrative-recovery.v1:")) continue;
        const value = JSON.parse(raw);
        if (value.draft?.target.kind === field && value.draft.raw === b && value.draft.pending?.outcome === "UNKNOWN")
          return { key, raw, pending: value.draft.pending };
      }
      throw new Error("The earlier armed B comparison was not retained");
    }, { field, b });
    const unknownGroup = recovery.getByRole("group").filter({ hasText: b }).filter({ hasText: "· Unconfirmed earlier save" });
    await expect(unknownGroup).toHaveCount(1);
    await unknownGroup.getByRole("button", { name: new RegExp("^Restore " + label + " copy ") }).click();
    await expect(input).toHaveValue(b);
    await expect(page.getByRole("region", { name: "Unconfirmed " + field + " submission", exact: true })).toBeVisible();
    await expect(button(page, "Commit " + field)).toBeDisabled();
    expect(await requests(page)).toHaveLength(3);
    expect(await page.evaluate(() => window.__NARRATIVE_AUTHORITY__.project())).toEqual(savedA);
    expect(await page.evaluate(key => localStorage.getItem(key), armedB.key)).toBe(armedB.raw);
    const restoredPending = await page.evaluate(field => {
      const pointer = Object.keys(sessionStorage).find(key => key.startsWith("motionwright.narrative-writer.v1:") && key.includes(field));
      if (!pointer) throw new Error("No current Narrative pointer");
      const key = "motionwright.narrative-recovery.v1:" + pointer.slice("motionwright.narrative-writer.v1:".length) + ":" + sessionStorage.getItem(pointer);
      return JSON.parse(localStorage.getItem(key)!).draft.pending;
    }, field);
    expect(restoredPending).toEqual(armedB.pending);
  });
}
