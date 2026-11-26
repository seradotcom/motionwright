import {expect,type Page} from "@playwright/test";
import type {CanvasNode,Project} from "../../src/types";
import {proposalButton as button} from "./scopedPaintMotionJourney";
import {scopedDraftScope,DRAFT_STORAGE_PREFIX,decodeScopedDraft} from "../../src/scopedChangeDraft";

export const comparisonSceneName="Source-bound comparison journey";
export const comparisonRows=(afterMilli=60000,unit:"percent"|"count"="percent")=>[
  {key:"before",label:"Before",milli:25000,unit:"percent" as const},
  {key:"after",label:"After",milli:afterMilli,unit},
];
export function comparisonNodes(template:CanvasNode,ids:readonly string[]):CanvasNode[]{
  if(ids.length!==4||new Set(ids).size!==4)throw new Error("Four unique source objects are required.");
  return ids.map((id,i)=>({...template,id,name:"Original comparison state "+i,
    kind:i%2===0?"text":"rectangle",x:320+i*20,y:300+i*20,width:800,height:100,parent_id:null,
    rotation_deg:0,opacity:1,text:i%2===0?"Placeholder":null,coordinate_space:"project_pixels",z_index:i,
    style:{...template.style,fill:i%2===0?"#F2F4F3":"#86C8D1",stroke:null,stroke_width:0,
      font_family:i%2===0?"Instrument Sans Variable":null,font_size:i%2===0?48:null,
      font_weight:i%2===0?400:null,line_height:i%2===0?1.12:null,blend_mode:"normal"},
    property_locks:[],relations:[],keyframes:[]}));
}
export async function openComparisonEditor(page:Page,sceneId:string,viaLibrary=false){
  await button(page,"Production").click();
  await page.getByLabel("Production working scene",{exact:true}).selectOption(sceneId);
  if(viaLibrary){
    await button(page,"Craft library").click();
    await page.getByLabel("Craft family",{exact:true}).selectOption("data");
    await page.getByRole("button",{name:/^Measured state comparison/}).click();
    await button(page,"Open authoring editor").click();
  }else await button(page,"Scoped changes").click();
  const editor=page.getByRole("region",{name:"Creative production workstation",exact:true});
  await expect(editor.getByRole("heading",{name:"Scoped changes",exact:true})).toBeVisible();
  await expect(editor.locator("summary:visible").filter({hasText:/^Focus transfer$/})).toHaveCount(1);
  await expect(editor.locator("summary:visible").filter({hasText:/^Measured state comparison$/})).toHaveCount(1);
  return editor;
}
export async function selectComparison(page:Page,sceneId:string,beforeId:string,afterId:string,viaLibrary=false){
  const editor=await openComparisonEditor(page,sceneId,viaLibrary);
  const panel=editor.locator("details").filter({has:page.locator("summary").filter({hasText:/^Measured state comparison$/})});
  if(await panel.getAttribute("open")===null)await panel.locator("summary").click();
  await panel.getByLabel("Before state",{exact:true}).selectOption(beforeId);
  await panel.getByLabel("After state",{exact:true}).selectOption(afterId);
  return {editor,panel};
}
export async function stageComparison(page:Page,sceneId:string,beforeId:string,afterId:string,viaLibrary=false){
  const {editor,panel}=await selectComparison(page,sceneId,beforeId,afterId,viaLibrary);
  const stage=panel.getByRole("button",{name:"Stage measured comparison",exact:true});
  await expect(stage).toBeEnabled();
  await expect(panel.getByText(/Common axis: 0 to 100 percent/)).toBeVisible();
  await stage.click();
  await expect(editor.getByRole("region",{name:"Proposed edits",exact:true}).getByLabel(/Remove proposed edit/)).toHaveCount(4);
  return editor;
}
export async function reviewComparison(page:Page,ids:readonly string[],barWidths:readonly [number,number]){
  const editor=page.getByRole("region",{name:"Creative production workstation",exact:true});
  await editor.getByRole("button",{name:"Preview scoped changes",exact:true}).click();
  await expect(editor.getByText("Editorial diff prepared",{exact:true})).toBeVisible();
  const after=editor.getByRole("img",{name:"After scoped changes",exact:true});
  for(const [i,width] of [[1,barWidths[0]],[3,barWidths[1]]] as const){
    const rect=after.locator('[data-canvas-node-id="'+ids[i]+'"] rect');
    await expect(rect).toHaveAttribute("x","240");
    await expect(rect).toHaveAttribute("width",String(width));
  }
}
export async function readComparisonDraft(page:Page,project:Project,sceneId:string){
  const scene=project.scenes.find(item=>item.id===sceneId);
  if(!scene)throw new Error("Missing measured source scene.");
  const scope=scopedDraftScope(project,scene), key=DRAFT_STORAGE_PREFIX+scope;
  // Recovery is the original browser draft, not a newly generated queue.
  await expect.poll(()=>page.evaluate(k=>sessionStorage.getItem(k),key)).not.toBeNull();
  const raw=await page.evaluate(k=>sessionStorage.getItem(k)!,key);
  return {key,draft:decodeScopedDraft(raw,scope)};
}
export async function refuseStaleComparison(page:Page,sceneId:string,beforeId:string,afterId:string){
  const editor=await openComparisonEditor(page,sceneId);
  await expect(editor.getByText("Generated recipe source changed.",{exact:true})).toBeVisible();
  await expect(editor.getByRole("region",{name:"Proposed edits",exact:true}).getByLabel(/Remove proposed edit/)).toHaveCount(4);
  await expect(editor.getByRole("button",{name:"Preview scoped changes",exact:true})).toBeDisabled();
  await expect(editor.getByRole("button",{name:"Apply as one revision",exact:true})).toBeDisabled();
  await editor.getByRole("button",{name:"Review on current revision",exact:true}).click();
  await expect(editor.getByText(/restage the recipe on the current saved source/i)).toBeVisible();
  return async()=>{
    await editor.getByRole("button",{name:"Discard proposal",exact:true}).click();
    const {panel}=await selectComparison(page,sceneId,beforeId,afterId);
    await expect(panel.getByText(/identical units/)).toBeVisible();
    await expect(panel.getByRole("button",{name:"Stage measured comparison",exact:true})).toBeDisabled();
  };
}
