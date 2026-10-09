import { describe, expect, it } from "vitest";
import { applyChange, bootstrap } from "./api";
import { defaultHeroConfig, heroNodeId } from "./creativeProduction";
import { previewCreativePatchUndo, validateCreativePatchRecord } from "./creativeUndo";
import { fixtureProject } from "./fixture";
import { rationalSeconds } from "./types";
import type { Project } from "./types";
const instance="00000000-0000-4000-8000-000000000005";
async function seed() {
  let project:Project=structuredClone(fixtureProject);project.locks=[];project.reviews=[];project.branch_workspaces=[];
  project.scenes=[{...project.scenes[0],nodes:[],beats:[],duration:rationalSeconds(6),start:rationalSeconds(0)}];
  project.production_design={plan:null,heroes:[],capsules:[],patches:[]};
  project=await applyChange(project,{type:"upsert_product_hero",instance_id:instance,scene_id:project.scenes[0].id,config:defaultHeroConfig()});
  return {project,nodeId:await heroNodeId(instance,"body")};
}
async function patch(project:Project,nodeId:string,text:string) {
  return applyChange(project,{type:"apply_creative_patch",patch:{scene_id:project.scenes[0].id,base_revision:project.revision,rationale:"Rewrite only this line",edits:[{kind:"text",node_id:nodeId,text}]}});
}

describe("creative undo is a reviewed forward edit",()=>{
  it("stores preimages, previews without writes, and applies undo/redo as separate new revisions",async()=>{
    const {project:initial,nodeId}=await seed();const changed=await patch(initial,nodeId,"A shorter message.");
    const record=changed.production_design!.patches![0];
    expect(record.source_revision).toBe(initial.revision);
    const before=await bootstrap();const preview=previewCreativePatchUndo(changed,record.id);
    expect(await bootstrap()).toEqual(before);expect(preview.after[0].text).toBe(record.before[0].text);
    const undone=await applyChange(changed,{type:"undo_creative_patch",patch_id:record.id});
    expect(undone.scenes[0].nodes).toEqual(initial.scenes[0].nodes);
    expect(undone.revision).toBe(changed.revision+1);expect(undone.generation).toBe(initial.generation);
    expect(undone.production_design!.patches).toHaveLength(2);
    expect(()=>previewCreativePatchUndo(undone,record.id)).toThrow("already");
    const redone=await applyChange(undone,{type:"undo_creative_patch",patch_id:undone.production_design!.patches![1].id});
    expect(redone.scenes[0].nodes).toEqual(changed.scenes[0].nodes);
    expect(redone.revision).toBe(changed.revision+2);
  });
  it("retains a later human style change while undoing earlier text",async()=>{
    const {project,nodeId}=await seed();let changed=await patch(project,nodeId,"New copy");
    const record=changed.production_design!.patches![0];
    const style={...changed.scenes[0].nodes.find(n=>n.id===nodeId)!.style,fill:"#AABBCC"};
    changed=await applyChange(changed,{type:"update_canvas_style",scene_id:changed.scenes[0].id,node_id:nodeId,style});
    const undone=await applyChange(changed,{type:"undo_creative_patch",patch_id:record.id});
    const node=undone.scenes[0].nodes.find(n=>n.id===nodeId)!;
    expect(node.text).toBe(project.scenes[0].nodes.find(n=>n.id===nodeId)!.text);expect(node.style).toEqual(style);
  });
  it.each(["conflict","lock","missing"])("rejects %s atomically",async mode=>{
    const {project,nodeId}=await seed();let changed=await patch(project,nodeId,"New copy");const id=changed.production_design!.patches![0].id;
    changed=await applyChange(changed,mode==="conflict"?{type:"update_canvas_text",scene_id:changed.scenes[0].id,node_id:nodeId,text:"Later human decision"}:mode==="lock"?{type:"set_node_property_lock",scene_id:changed.scenes[0].id,node_id:nodeId,property:"text",locked:true}:{type:"remove_canvas_node",scene_id:changed.scenes[0].id,node_id:nodeId});
    const before=await bootstrap();await expect(applyChange(changed,{type:"undo_creative_patch",patch_id:id})).rejects.toThrow();expect(await bootstrap()).toEqual(before);
  });
  it("keeps a bounded reversible window and rejects forged structural preimages",async()=>{
    const {project,nodeId}=await seed();let current=await patch(project,nodeId,"Revision 0");const first=current.production_design!.patches![0].id;
    for(let index=1;index<=66;index++) current=await patch(current,nodeId,"Revision "+index);
    expect(current.production_design!.patches).toHaveLength(64);
    expect(()=>previewCreativePatchUndo(current,first)).toThrow("window");
    const forged=structuredClone(current.production_design!.patches![63]);forged.after[0].parent_id=crypto.randomUUID();
    expect(()=>validateCreativePatchRecord(forged)).toThrow("outside");
    await expect(patch(current,nodeId,"Revision 66")).rejects.toThrow("unchanged");
  });
});
