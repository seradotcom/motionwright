import { expect,type Page } from "@playwright/test";
import { proposalButton as button,readStudioProject } from "./scopedPaintMotionJourney";
import type { BeatTimelineRaw } from "../../src/sceneBeatTimeline";
export const beatTimelineEditor=(page:Page)=>page.getByRole("region",{name:"Scene beat timeline editor",exact:true});
export async function createBeatTimelineScene(page:Page) {
  let revision=(await readStudioProject(page)).revision;
  const saved=async()=>{revision++;await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+revision);};
  const name="Editable beat timing";
  await button(page,"Add scene").click();await page.getByLabel("New scene name",{exact:true}).fill(name);await button(page,"Add").click();await saved();
  await button(page,"Canvas").click();await page.locator(".tree-row").filter({hasText:name}).click();
  await page.getByLabel("Create canvas object",{exact:true}).getByRole("button",{name:"shape",exact:true}).click();await saved();
  await button(page,"Storyboard").click();
  await button(page,"Fill timing gap").click();await saved();
  const base=await readStudioProject(page),scene=base.scenes.find(scene=>scene.name===name)!;
  expect(scene.beats).toHaveLength(1);expect(scene.beats[0].duration).toEqual({num:"6",den:"1"});
  await button(page,"Fit scene").click();
  const beat=page.locator('.timeline-beat[data-source-id="'+scene.beats[0].id+'"]');await expect(beat).toHaveCount(1);await beat.click();
  await button(page,"Edit scene beats").click();await expect(beatTimelineEditor(page)).toBeVisible();
  return {base,sceneId:scene.id,beatId:scene.beats[0].id,name};
}
export async function reopenBeatTimeline(page:Page,name:string) {
  await button(page,"Storyboard").click();await page.locator(".tree-row").filter({hasText:name}).click();
  const opener=button(page,"Edit beat timeline");if(await opener.count())await opener.click();
  await expect(beatTimelineEditor(page)).toBeVisible();
}
export async function prepareBeatOperation(page:Page,beatId:string,operation:BeatTimelineRaw["operation"],fields:Record<string,string>,reason:string) {
  const editor=beatTimelineEditor(page);
  await editor.getByLabel("Beat timing operation",{exact:true}).selectOption(operation);
  if(operation!=="undo")await editor.getByLabel("Timeline beat",{exact:true}).selectOption(beatId);
  for(const [name,value]of Object.entries(fields))await editor.getByLabel("Beat operation "+name,{exact:true}).fill(value);
  await editor.getByLabel("Beat timing rationale",{exact:true}).fill(reason);
  await editor.getByRole("button",{name:"Preview beat timing",exact:true}).click();
  await expect(editor.getByRole("region",{name:"Proposed beat timing",exact:true})).toBeVisible();
  await expect(editor.getByRole("button",{name:"Apply beat timing as one revision",exact:true})).toBeEnabled();
}
export async function commitBeatOperation(page:Page,revision:number) {
  await beatTimelineEditor(page).getByRole("button",{name:"Apply beat timing as one revision",exact:true}).click();
  await expect(page.locator(".command-bar .revision-chip")).toHaveText("r"+(revision+1));
  await expect(beatTimelineEditor(page).getByRole("button",{name:"Apply beat timing as one revision",exact:true})).toBeDisabled();
  return readStudioProject(page);
}
