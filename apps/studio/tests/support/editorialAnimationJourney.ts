import {expect,type Page} from "@playwright/test";
import {proposalButton as button,readStudioProject} from "./scopedPaintMotionJourney";
import {createBeatTimelineScene,prepareBeatOperation,commitBeatOperation,reopenBeatTimeline} from "./sceneBeatTimelineJourney";
export const poseEditor=(page:Page)=>page.getByRole("region",{name:"Native pose animation",exact:true});
export async function createEditorialAnimation(page:Page){
  const context=await createBeatTimelineScene(page);
  await prepareBeatOperation(page,context.beatId,"split",{start:"2"},"Preserve the motion clock across editorial claims.");
  let project=await commitBeatOperation(page,context.base.revision);
  let scene=project.scenes.find(item=>item.id===context.sceneId)!,node=scene.nodes[0];
  const original=structuredClone(scene),pose=poseEditor(page);
  await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:context.name}).click();
  await page.locator(".canvas-tree-row").filter({hasText:node.name}).click();
  await expect(pose.getByLabel("Editorial animation continuity",{exact:true})).toBeVisible();
  for(const [label,value]of [
    ["Pose start x",node.x-80],["Pose start y",node.y-20],["Pose start width",node.width-60],
    ["Pose start height",node.height-40],["Pose start rotation",-18],["Pose end frame",120],
  ] as const)await pose.getByLabel(label,{exact:true}).fill(String(value));
  await pose.getByLabel("Pose geometry curve",{exact:true}).selectOption("ease_out_cubic");
  await pose.getByLabel("Pose opacity",{exact:true}).selectOption("in_out");
  await pose.getByLabel("Pose fade curve",{exact:true}).selectOption("linear");
  await pose.getByLabel("Pose fade end frame",{exact:true}).fill("30");
  await pose.getByLabel("Pose fade-out start frame",{exact:true}).fill("90");
  await pose.getByLabel("Pose fade-out end frame",{exact:true}).fill("150");
  await pose.getByLabel("Pose fade-out curve",{exact:true}).selectOption("ease_out_cubic");
  await pose.getByRole("button",{name:"Create native pose animation",exact:true}).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(project.revision+1));
  project=await readStudioProject(page);scene=project.scenes.find(item=>item.id===context.sceneId)!;node=scene.nodes[0];
  expect(node.keyframes).toHaveLength(14);expect(scene.beats).toEqual(original.beats);
  await button(page,"Fit scene").click();
  await page.locator('.timeline-beat[data-source-id="'+scene.beats[1].id+'"]').click();
  const object=page.getByRole("button",{name:"Canvas object "+node.name,exact:true});
  await expect(page.getByTestId("canvas-camera-layer")).toHaveAttribute("data-camera-time","2");
  const style=await object.evaluate(element=>({left:(element as HTMLElement).style.left,width:(element as HTMLElement).style.width,
    rotation:(element as HTMLElement).style.transform,opacity:(element as HTMLElement).style.opacity}));
  // CSSOM serializes percentages at its own precision. Compare the exact
  // browser representation of the independent scene-clock expectation;
  // converting that rounded string back into pixels invents extra precision.
  const expectedBox=await page.evaluate(({x,width})=>{
    const box=document.createElement("div");box.style.left=(x/1920*100)+"%";box.style.width=(width/1920*100)+"%";
    return {left:box.style.left,width:box.style.width};
  },{x:node.x-10,width:node.width-7.5});
  expect(style.left).toBe(expectedBox.left);expect(style.width).toBe(expectedBox.width);
  expect(style.rotation).toBe("rotate(-2.25deg)");expect(style.opacity).toBe("1");
  await button(page,"Production").click();
  await page.getByLabel("Production working scene",{exact:true}).selectOption(scene.id);
  await button(page,"Camera").click();
  await page.getByLabel("End center X",{exact:true}).fill(String(scene.camera.center_x+12));
  await page.getByLabel("Camera duration frames",{exact:true}).fill("90");
  await button(page,"Attach camera reframe").click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(project.revision+1));
  project=await readStudioProject(page);
  expect(project.scenes.find(item=>item.id===scene.id)!.nodes).toEqual(scene.nodes);
  await reopenBeatTimeline(page,context.name);
  await prepareBeatOperation(page,scene.beats[1].id,"split",{start:"4"},"Continue the same visual motion through the final beat.");
  const beforeEdit=project;project=await commitBeatOperation(page,project.revision);
  scene=project.scenes.find(item=>item.id===context.sceneId)!;
  expect(scene.nodes).toEqual(beforeEdit.scenes.find(item=>item.id===scene.id)!.nodes);
  expect(project.production_design).toEqual(beforeEdit.production_design);
  expect(scene.beats.map(beat=>beat.start)).toEqual([{num:"0",den:"1"},{num:"2",den:"1"},{num:"4",den:"1"}]);
  await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:context.name}).click();
  await page.locator(".canvas-tree-row").filter({hasText:node.name}).click();
  await expect(pose.getByLabel("Pose end frame",{exact:true})).toHaveValue("120");
  await expect(pose.getByLabel("Pose opacity",{exact:true})).toHaveValue("in_out");
  await page.locator('.timeline-beat[data-source-id="'+scene.beats[1].id+'"]').click();
  await expect(page.getByTestId("canvas-camera-layer")).toHaveAttribute("data-camera-center-x","968");
  return {...context,project,scene,node};
}
export async function continueEditorialAnimation(page:Page,context:Awaited<ReturnType<typeof createEditorialAnimation>>){
  await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:context.name}).click();
  await page.locator(".canvas-tree-row").filter({hasText:context.node.name}).click();
  const pose=poseEditor(page);
  await expect(pose.getByLabel("Pose end frame",{exact:true})).toHaveValue("120");
  await pose.getByLabel("Pose end frame",{exact:true}).fill("135");
  await pose.getByLabel("Pose geometry curve",{exact:true}).selectOption("hold");
  await pose.getByRole("button",{name:"Save native pose animation",exact:true}).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(context.project.revision+1));
  const project=await readStudioProject(page),scene=project.scenes.find(item=>item.id===context.sceneId)!;
  expect(scene.beats).toEqual(context.scene.beats);
  expect(project.production_design).toEqual(context.project.production_design);
  expect(scene.nodes[0].keyframes.filter(key=>key.property!=="opacity" && key.at.num!=="0")
    .every(key=>key.at.num==="9" && key.at.den==="2" && key.interpolation==="hold")).toBe(true);
  await button(page,"Fit scene").click();await page.locator('.timeline-beat[data-source-id="'+scene.beats[1].id+'"]').click();
  await expect(page.getByTestId("canvas-camera-layer")).toHaveAttribute("data-camera-time","2");
  const object=page.getByRole("button",{name:"Canvas object "+context.node.name,exact:true});
  expect(await object.evaluate(element=>(element as HTMLElement).style.transform)).toBe("rotate(-18deg)");
  return project;
}
