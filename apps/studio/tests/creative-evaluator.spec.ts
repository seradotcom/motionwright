import { expect,test } from "@playwright/test";

test("versioned data-only evaluator records source-bound technical evidence but never passes human approval",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Evaluators",exact:true}).click();
  await expect(page.getByRole("heading",{name:"Creative evaluation"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Register data-only evaluator"})).toBeDisabled();
  await expect(page.getByLabel("Evaluator human question"))
    .toHaveValue(/Does this scene communicate/);
  await page.getByLabel("Evaluator author declaration")
    .fill("I authored this original source-bound creative review checklist.");
  await page.getByLabel("Evaluator label").fill("Original storyboard review");
  await expect(page.getByRole("button",{name:"Register data-only evaluator"})).toBeEnabled();
  await page.getByRole("button",{name:"Register data-only evaluator"}).click();
  await expect(page.getByLabel("Evaluator source version").locator("option")).toHaveCount(2);
  await expect(page.getByRole("button",{name:"Evaluate authored scene"})).toBeEnabled();
  await page.getByRole("button",{name:"Evaluate authored scene"}).click();
  await expect(page.getByRole("heading",{name:"Source-bound observations"})).toBeVisible();
  await expect(page.getByText(/human creative approval: not_assessed/i)).toBeVisible();
  await expect(page.getByText(/Requires a human creative reviewer/i)).toBeVisible();
  await expect(page.getByText(/Source revision/)).toBeVisible();
});
test("evaluator cannot register executable source without owner declaration",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Evaluators",exact:true}).click();
  await page.getByLabel("Evaluator author declaration").fill(" ");
  await expect(page.getByRole("button",{name:"Register data-only evaluator"})).toBeDisabled();
  await page.getByLabel("Evaluator minimum nodes").fill("0");
  await expect(page.getByText(/Recipe validation/)).toContainText(/node count/i);
  await expect(page.getByRole("button",{name:"Register data-only evaluator"})).toBeDisabled();
});
