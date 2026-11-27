import { expect, test, type Page } from "@playwright/test";
import { applyChange } from "../src/api";
import type { Change, Project } from "../src/types";
async function open(page:Page, editedSource = "edited.mwedit.zip") {
  await page.exposeFunction("__MANIM_SIMULATE_CHANGE__",async(project:Project,change:Change)=>applyChange(project,change));
  await page.goto("/tests/fixtures/manim-controls.html");
  const original = page.getByRole("combobox",{name:"Original package",exact:true});
  await expect(original.getByRole("option",{name:"returned-plan.json",exact:true})).toHaveCount(0);
  await original.selectOption({label:"original.mwedit.zip"});
  await page.getByRole("combobox",{name:"Edited source",exact:true}).selectOption({label:editedSource});
  await page.getByRole("button",{name:"Read original attached Manim package"}).click();
  await expect(page.getByRole("combobox",{name:"Controls to reintegrate",exact:true})).toHaveValue("controls");
  await page.getByRole("button",{name:"Compare returned source"}).click();
}
async function snapshot(page:Page) {
  return page.evaluate(()=>(window as unknown as {__MANIM_COMPONENT__:{project:()=>Project;applies:()=>number}}).__MANIM_COMPONENT__.project());
}
test("complete returned controls are reviewed together, saved once, retained on reopen and undone together",async({page})=>{
  await open(page,"returned-plan.json");
  const before=await snapshot(page),table=page.getByRole("table",{name:"Manim control changes"});
  for(const field of ["x","y","rotation_deg","opacity","text","style.fill","style.font_size"])
    await expect(table.getByRole("rowheader",{name:field,exact:true})).toBeVisible();
  await expect(table).toContainText("Returned complete object");await expect(table).toContainText("#86C8D1");
  const save=page.getByRole("button",{name:"Save reviewed Manim edit"});
  await save.click();
  await expect(page.getByLabel("Source revision")).toHaveText("r"+(before.revision+1));
  const after=await snapshot(page);
  expect(after.assets).toEqual(before.assets);
  expect(after.scenes[0].nodes[0]).toMatchObject({x:before.scenes[0].nodes[0].x+12,
    y:before.scenes[0].nodes[0].y+20,rotation_deg:7,opacity:.8,text:"Returned complete object",style:{fill:"#86C8D1",font_size:40}});
  expect(after.production_design?.patches?.at(-1)?.before).toEqual([before.scenes[0].nodes[0]]);
  await page.getByRole("button",{name:"Close source review",exact:true}).click();
  await page.getByRole("button",{name:"Reopen source review",exact:true}).click();
  expect(await snapshot(page)).toEqual(after);
  await expect(save).toHaveCount(0);
  await page.getByRole("button",{name:"Undo saved object patch"}).click();
  await expect(page.getByLabel("Source revision")).toHaveText("r"+(before.revision+2));
  expect((await snapshot(page)).scenes[0].nodes).toEqual(before.scenes[0].nodes);
});
test("fresh compiler comparison refuses a changed object before any commit",async({page})=>{
  await open(page);
  const before=await snapshot(page);
  await page.getByRole("button",{name:"Return an unrelated source object"}).click();
  await page.getByRole("button",{name:"Save reviewed Manim edit"}).click();
  await expect(page.getByRole("alert")).toContainText("does not match");
  expect(await snapshot(page)).toEqual(before);
});
test("a lost reply retains the complete patch and reopening never resubmits it",async({page})=>{
  await open(page);
  const before=await snapshot(page);
  await page.getByRole("button",{name:"Lose the next commit reply"}).click();
  await page.getByRole("button",{name:"Save reviewed Manim edit"}).click();
  await expect(page.getByLabel("Unconfirmed Manim source operation")).toContainText("Never resubmit");
  await expect(page.getByLabel("Source revision")).toHaveText("r"+(before.revision+1));
  await page.getByRole("button",{name:"Close source review",exact:true}).click();
  await page.getByRole("button",{name:"Reopen source review",exact:true}).click();
  await expect(page.getByLabel("Unconfirmed Manim source operation")).toContainText("Never resubmit");
  await expect(page.getByRole("button",{name:"Inspect saved Manim source"})).toBeDisabled();
  expect(await page.evaluate(()=>(window as unknown as {__MANIM_COMPONENT__:{applies:()=>number}}).__MANIM_COMPONENT__.applies())).toBe(1);
});
