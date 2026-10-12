import { expect, test } from "@playwright/test";

test("Deliver comparison shows exactly the saved profile and never mutates the project",async ({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const before=await page.locator(".revision-chip").first().innerText();
  const review=page.getByRole("region",{name:"Delivery variant impact comparison"});
  await expect(review.getByRole("heading",{name:"Variant change impact"})).toBeVisible();
  const saved=review.locator("details.variant-impact-set").first();
  await saved.locator("summary").click();
  await expect(saved).toContainText("No differences among the explicitly compared profile fields");
  await expect(saved).toContainText("not a cache invalidation oracle");
  await expect(saved).toContainText("No changed-field count is a verification PASS");
  await expect(page.locator(".revision-chip").first()).toHaveText(before);
});

test("unsaved cut changes reveal affected source stages without committing",async ({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  const before=await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button",{name:"Customize cut",exact:true}).click();
  const removed=page.locator(".variant-scene-row").filter({hasText:"Pixels are brittle"});
  await removed.locator('input[type="checkbox"]').first().uncheck();
  const review=page.getByRole("region",{name:"Delivery variant impact comparison"});
  const saved=review.locator("details.variant-impact-set").first();
  await expect(saved.locator("summary")).toContainText("changed fields");
  await saved.locator("summary").click();
  await expect(saved).toContainText("Source scene selection");
  await expect(saved).toContainText("Picture / native layout");
  await expect(saved).toContainText("Narration / mix");
  await expect(saved).toContainText("Caption file / burn-in");
  await expect(saved).toContainText("AV mastering");
  await expect(saved).toContainText("scene timing");
  await expect(page.locator(".revision-chip").first()).toHaveText(before);
});

test("real user-created derivative compares with its parent without inventing native equivalence",async ({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Deliver",exact:true}).click();
  await page.getByRole("button",{name:"Derive selected"}).click();
  const review=page.getByRole("region",{name:"Delivery variant impact comparison"});
  const parent=review.locator("details.variant-impact-set").nth(1);
  await expect(parent.locator("summary")).toContainText("Variant vs parent profile");
  await parent.locator("summary").click();
  await expect(parent).toContainText("Parent lineage");
  await expect(parent).toContainText("Recorded source revision");
  await expect(parent).toContainText("Narration timing lock");
  await expect(parent).toContainText("not a cache invalidation oracle");
  await expect(parent).not.toContainText("REAL RENDER PASSED");
});
