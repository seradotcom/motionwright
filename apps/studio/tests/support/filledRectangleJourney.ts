import {expect,type Page} from "@playwright/test";
import {proposalButton as button,readStudioProject} from "./scopedPaintMotionJourney";

export async function authorFilledRectangles(page:Page) {
  const base=await readStudioProject(page),name="Editable rectangular composition";
  let revision=base.revision;
  const saved=async()=>{await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(++revision));};
  await button(page,"Add scene").click();
  await page.getByLabel("New scene name",{exact:true}).fill(name);
  await page.locator(".add-scene-form").getByRole("button",{name:"Add",exact:true}).click();await saved();
  await button(page,"Canvas").click();
  await page.locator(".tree-row").filter({hasText:name}).click();
  const tools=page.getByLabel("Create canvas object",{exact:true});
  await tools.getByRole("button",{name:"rectangle",exact:true}).click();await saved();
  let project=await readStudioProject(page);
  const scene=project.scenes.find(scene=>scene.name===name)!;
  const rectangle=scene.nodes.find(node=>node.kind==="rectangle")!;
  expect(rectangle).toMatchObject({kind:"rectangle",parent_id:null,x:276,y:252,width:360,height:216,opacity:1,
    coordinate_space:"project_pixels",style:{fill:"#86C8D1",stroke:null,stroke_width:0}});
  const firstId=rectangle.id;
  const authoredRect=page.locator('svg [data-canvas-node-id="'+firstId+'"] rect');
  await expect(authoredRect.first()).toHaveAttribute("fill","#86C8D1");
  await expect(page.getByLabel("Canvas stroke",{exact:true})).toHaveValue("");
  await expect(page.getByLabel("Canvas stroke width",{exact:true})).toHaveValue("0");
  for(const [field,value] of [["X","480"],["Y","360"],["W","420"],["H","240"],["Opacity","0.625"]]){
    await page.getByLabel("Canvas "+field,{exact:true}).fill(value);
  }
  await button(page,"Commit transform").click();await saved();
  await page.getByLabel("Canvas fill",{exact:true}).fill("#E0A940");
  await button(page,"Commit style").click();await saved();
  await expect(authoredRect.first()).toHaveAttribute("fill","#E0A940");
  await tools.getByRole("button",{name:"rectangle",exact:true}).click();await saved();
  project=await readStudioProject(page);
  const current=project.scenes.find(item=>item.id===scene.id)!;
  const edited=current.nodes.find(node=>node.id===firstId)!;
  expect(edited).toMatchObject({x:480,y:360,width:420,height:240,opacity:.625,style:{fill:"#E0A940",stroke:null,stroke_width:0}});
  const second=current.nodes.filter(node=>node.kind==="rectangle").find(node=>node.id!==firstId)!;
  expect(second).toMatchObject({x:312,y:288,width:360,height:216,style:{fill:"#86C8D1",stroke:null,stroke_width:0}});
  expect(second.z_index).toBeGreaterThan(edited.z_index);
  expect(project.scenes.filter(item=>item.id!==scene.id)).toEqual(base.scenes);
  expect(project.assets).toEqual(base.assets);expect(project.audio).toEqual(base.audio);
  return {base,project,sceneId:scene.id,name,firstId,secondId:second.id};
}
