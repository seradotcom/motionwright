import { expect,type Page } from "@playwright/test";
import { proposalButton as button,readStudioProject } from "./scopedPaintMotionJourney";
export async function authorBlenderCameraComposition(page:Page) {
  const base=await readStudioProject(page),name="Static Blender composition";
  let revision=base.revision;
  const saved=async()=>{await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(++revision));};
  await button(page,"Add scene").click();
  await page.getByLabel("New scene name",{exact:true}).fill(name);
  await page.locator(".add-scene-form").getByRole("button",{name:"Add",exact:true}).click();await saved();
  await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:name}).click();
  await page.getByLabel("Renderer",{exact:true}).selectOption("blender");
  await page.getByRole("button", { name: "Apply renderer change", exact: true }).click();
  await saved();
  const tools=page.getByLabel("Create canvas object",{exact:true});
  await tools.getByRole("button",{name:"rectangle",exact:true}).click();await saved();
  await tools.getByRole("button",{name:"circle",exact:true}).click();await saved();
  let project=await readStudioProject(page);
  const scene=project.scenes.find(s=>s.name===name)!;
  const circle=scene.nodes.find(n=>n.kind==="circle")!;
  expect(circle).toMatchObject({kind:"circle",parent_id:null,width:216,height:216,opacity:1,coordinate_space:"project_pixels",
    style:{fill:"#86C8D1",stroke:null,stroke_width:0}});
  const ellipse=page.locator('svg [data-canvas-node-id="'+circle.id+'"] ellipse').first();
  await expect(ellipse).toHaveAttribute("fill","#86C8D1");
  for(const [field,value] of [["X","800"],["Y","500"],["W","240"],["H","180"]]){
    await page.getByLabel("Canvas "+field,{exact:true}).fill(value);
  }
  await button(page,"Commit transform").click();await saved();
  await page.getByLabel("Canvas fill",{exact:true}).fill("#E0A940");
  await button(page,"Commit style").click();await saved();
  const beforeCamera=await readStudioProject(page),beforeScene=beforeCamera.scenes.find(s=>s.id===scene.id)!;
  for(const [field,value] of [["Center X","1000"],["Center Y","600"],["zoom","2"],["Rotation","30"]]){
    await page.getByLabel("Camera "+field,{exact:true}).fill(value);await saved();
  }
  project=await readStudioProject(page);
  const current=project.scenes.find(s=>s.id===scene.id)!;
  expect(current.nodes).toEqual(beforeScene.nodes);
  expect(current.camera).toMatchObject({center_x:1000,center_y:600,zoom:2,rotation_deg:30,safe_margin:0.05});
  expect(current.nodes.find(n=>n.id===circle.id)).toMatchObject({x:800,y:500,width:240,height:180,style:{fill:"#E0A940",stroke:null,stroke_width:0}});
  expect(current.nodes.map(n=>n.kind)).toEqual(["rectangle","circle"]);
  await expect(ellipse).toHaveAttribute("fill","#E0A940");
  await expect(page.getByTestId("canvas-camera-layer")).not.toHaveCSS("transform","matrix(1, 0, 0, 1, 0, 0)");
  expect(project.scenes.filter(s=>s.id!==scene.id)).toEqual(base.scenes);
  expect(project.assets).toEqual(base.assets);expect(project.audio).toEqual(base.audio);
  return {project,base,sceneId:scene.id,circleId:circle.id,name};
}
