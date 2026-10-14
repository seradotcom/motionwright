import { expect, test } from "@playwright/test";

test("identity panel cannot silently mint likeness rights or media from an unverified source",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Identity & consent",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Identity and consent"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Register declared identity source"})).toBeDisabled();
  await expect(page.getByText(/No identity sources are registered/)).toBeVisible();
  await page.getByLabel("Identity origin category").selectOption("human_likeness");
  await expect(page.getByLabel("Identity consent evidence")).toBeVisible();
  await expect(page.getByText(/human likenesses always require|independent approval/i).first()).toBeVisible();
  await page.getByLabel("Identity display label").fill("Person A");
  await page.getByLabel("Identity origin description").fill("This person is represented only for a specific approved project.");
  await page.getByLabel("Identity owner declaration").fill("I assert permission but this statement is not itself verification.");
  await page.getByLabel("Identity consent subject").fill("The person named in the original consent");
  await page.getByLabel("Identity consent scope").fill("Limited educational synthetic presentation");
  await page.getByLabel("Identity modality presenter").check();
  await page.getByLabel("Identity purpose education").check();
  await expect(page.getByRole("button",{name:"Register declared identity source"})).toBeDisabled();
  await expect(page.getByText(/Import|source|identity declaration/i).first()).toBeVisible();
});
test("fictional identities carry a mandatory unambiguous synthetic disclosure",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Identity & consent",exact:true}).click();
  await page.getByLabel("Identity origin category").selectOption("original_fictional");
  await expect(page.getByText(/SYNTHETIC ORIGINAL CHARACTER/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Register declared identity source"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Register declared identity source"})).not.toHaveAttribute("aria-pressed","true");
});
