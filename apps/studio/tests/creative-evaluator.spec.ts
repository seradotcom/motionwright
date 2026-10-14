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
  await expect(page.getByText(/Recipe validation/)).toContainText(/author declaration/i);
  // Fix the author declaration first, then independently test the minimum-node bound.
  // The validator correctly prioritizes missing author rights before other criteria.
  await page.getByLabel("Evaluator author declaration").fill("I authored this original data-only review checklist.");
  await page.getByLabel("Evaluator minimum nodes").fill("0");
  await expect(page.getByText(/Recipe validation/)).toContainText(/node count/i);
  await expect(page.getByRole("button",{name:"Register data-only evaluator"})).toBeDisabled();
});

test("data-only evaluator import requires explicit owner rights",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Production",exact:true}).click();
  await page.getByRole("button",{name:"Evaluators",exact:true}).click();
  const bundle={schema:"motionwright.creative-evaluator/1",
    id:"00000000-0000-4000-8000-000000000077",version:1,
    label:"Original imported scene assessment",purpose:"Review the authored original scene",
    author_attestation:"I own this reusable original checklist",
    playbook:["Inspect the editable scene without running executable skills"],
    criteria:[{kind:"human_question",id:"00000000-0000-4000-8000-000000000078",
      question:"Would an actual viewer understand this shot?"}],
  };
  await page.getByLabel("Import evaluator JSON").fill(JSON.stringify(bundle));
  await expect(page.getByRole("button",{name:"Import original evaluator JSON"})).toBeDisabled();
  await page.getByLabel("Confirm evaluator source rights").check();
  await expect(page.getByRole("button",{name:"Import original evaluator JSON"})).toBeEnabled();
  await page.getByRole("button",{name:"Import original evaluator JSON"}).click();
  await expect(page.getByLabel("Evaluator source version").locator("option")).toHaveCount(2);
  await page.getByRole("button",{name:"Evaluate authored scene"}).click();
  await expect(page.getByText(/human creative approval: not_assessed/i)).toBeVisible();
});
