import { expect, test } from "@playwright/test";

test("Distillation requires genuine source variations and negative evidence before proposing",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Distillation",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Component distillation"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Propose original component"})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Record review only"})).toBeDisabled();
  await expect(page.getByText(/no automatic installation/i)).toBeVisible();
  await expect(page.getByLabel("Distillation source A")).toBeVisible();
  await expect(page.getByLabel("Distillation negative scene")).toBeVisible();
  await page.getByLabel("Distillation label").fill("Proposed original editable panel");
  await page.getByLabel("Distillation author").fill("Original designer");
  await page.getByLabel("Distillation rights").fill("The original roles belong to this project.");
  await page.getByLabel("Distillation requirements").fill("Two varied source scenes");
  await expect(page.getByRole("button",{name:"Propose original component"})).toBeDisabled();
  await expect(page.getByText(/No proposals exist/)).toBeVisible();
});
