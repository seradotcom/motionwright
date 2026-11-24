import { expect, type Page, type Download } from "@playwright/test";
import { proposalButton as button } from "./scopedPaintMotionJourney";
export const recipePanel=(page:Page)=>page.getByRole("region",{name:"Author Blender product recipe",exact:true});
export const sourcePanel=(page:Page)=>page.locator(".native-source-editor");
export async function openBlenderRecipe(page:Page,sceneId:string){
  await button(page,"Production").click();
  await page.getByRole("combobox",{name:"Production working scene",exact:true}).selectOption(sceneId);
  await button(page,"Native sources").click();
  await expect(recipePanel(page)).toBeVisible();
}
export async function authorBlenderRecipe(page:Page){
  const panel=recipePanel(page);
  await panel.getByLabel("Product namespace",{exact:true}).fill("StudioVessel");
  await panel.getByLabel("Product coating",{exact:true}).selectOption("graphite");
  await panel.getByLabel("Surface roughness",{exact:true}).fill("0.37");
  await panel.getByLabel("Surface metallic",{exact:true}).fill("0.72");
  await panel.getByLabel("Render samples",{exact:true}).fill("96");
  await panel.getByLabel("Product annotation",{exact:true}).fill("AUTHOR SOURCE\nGRAPHITE");
  await panel.getByLabel("Native camera curve",{exact:true}).selectOption("linear");
}
export async function downloadBlenderRecipe(page:Page):Promise<Download>{
  const pending=page.waitForEvent("download");
  await recipePanel(page).getByRole("button",{name:"Download recipe and prepare source input",exact:true}).click();
  return pending;
}
