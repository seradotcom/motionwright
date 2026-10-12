import {expect,test} from "@playwright/test";

test("review compares two saved authored branches without a native render or mutation",async({page})=>{
  await page.goto("/");
  const revision=await page.locator(".revision-chip").first().innerText();
  await page.getByRole("button",{name:"Review",exact:true}).click();
  const panel=page.getByRole("region",{name:"Creative alternatives evidence"});
  await expect(panel.getByRole("heading",{name:"Compare editable alternatives"})).toBeVisible();
  await expect(panel.getByLabel("Creative alternative branch")).toContainText("vertical-cut");
  await expect(panel.getByLabel("Source alternative differences")).toContainText("scene copy");

  await expect(panel).toContainText("Render proof");
  await expect(panel).toContainText("NOT TESTED");
  await expect(panel.getByRole("button",{name:"Record manual positive observation"})).toBeDisabled();
  expect(await page.locator(".revision-chip").first().innerText()).toBe(revision);
});

test("explicit negative variation creates a source-revisioned open review, not automatic distillation",async({page})=>{
  await page.goto("/");
  await page.getByRole("button",{name:"Review",exact:true}).click();
  const panel=page.getByRole("region",{name:"Creative alternatives evidence"});

  const revision=await page.locator(".revision-chip").first().innerText();
  await panel.getByRole("button",{name:"Negative study"}).click();
  await panel.getByLabel("Alternative review rationale").fill(
    "Vertical cut loses the exact semantic-versus-pixel transition; keep the source purpose before adapting the shot."
  );
  const record=panel.getByRole("button",{name:"Record manual negative observation"});
  await expect(record).toBeEnabled();
  await record.click();
  await expect(page.locator(".revision-chip").first()).not.toHaveText(revision);
  await expect(page.getByText("MANUAL NEGATIVE CREATIVE ALTERNATIVE REVIEW",{exact:false})).toBeVisible();
  await expect(page.getByText("Vertical cut loses the exact semantic-versus-pixel transition",{exact:false})).toBeVisible();
  await expect(panel).toContainText("not an approved component");
  await expect(panel).toContainText("Source deltas");

});
