import { expect, test } from "@playwright/test";

async function openMusicPlan(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Production plan",exact:true}).click();
  await page.getByLabel("Production governing clock").selectOption("music");
  await expect(page.getByRole("heading",{name:"Musical edit clock"})).toBeVisible();
  await expect(page.getByLabel("Selected audio fingerprint")).toContainText("5f5f5f5f");
  await page.getByLabel("Music beat timestamps").fill("0.000\n6.000\n18.000\n26.000");
  await page.getByRole("button",{name:"Use marked times in plan draft"}).click();
  await expect(page.getByText("4 draft beats")).toBeVisible();
  await expect(page.getByRole("button",{name:"Apply this scoped musical cut"})).toBeDisabled();
  await page.getByRole("button",{name:"Save production plan"}).click();
  await expect(page.getByRole("heading",{name:"Snap one editorial cut"})).toBeVisible();
}

test("author-marked beat grid produces a revision-bound, acknowledged scene cut without moving cues", async ({page})=>{
  await openMusicPlan(page);
  await page.getByLabel("Music cut target scene").selectOption({label:"Pixels are brittle"});
  await page.getByLabel("Music cut target beat").selectOption({label:"003 · 18.000s"});
  const preview=page.getByLabel("Music cut impact preview");
  await expect(preview).toContainText("16.000s");
  await expect(preview).toContainText("18.000s");
  await expect(preview).toContainText("2.000s");
  await expect(page.getByLabel("Acknowledge fixed audio and marker times")).toBeVisible();
  const apply=page.getByRole("button",{name:"Apply this scoped musical cut"});
  await expect(apply).toBeDisabled();
  const prior=await page.locator(".revision-chip").first().innerText();
  await page.getByLabel("Acknowledge fixed audio and marker times").check();
  await expect(apply).toBeEnabled();
  await apply.click();
  await expect(page.locator(".revision-chip").first()).not.toHaveText(prior);
  await expect(page.getByText("Applied one scoped cut.",{exact:false})).toBeVisible();
  // The plan retains the same audio asset and authored timestamps. It does
  // not install a renderer, synthesize an audio file or move any cue.
  await expect(page.getByLabel("Selected audio fingerprint")).toContainText("5f5f5f5f");
  await expect(page.getByLabel("Music beat timestamps")).toHaveValue("0.000\n6.000\n18.000\n26.000");
});

test("a downstream timing lock prevents the beat cut even when source and plan are valid",async({page})=>{
  await openMusicPlan(page);
  await page.getByLabel("Music cut target scene").selectOption({label:"Reasoning is solved"});
  await page.getByLabel("Music cut target beat").selectOption({label:"003 · 18.000s"});
  await expect(page.getByText(/timing lock protects this cut/i)).toBeVisible();
  await expect(page.getByRole("button",{name:"Apply this scoped musical cut"})).toBeDisabled();
});
