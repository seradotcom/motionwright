import {describe,it,expect} from "vitest";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import {applyChange} from "./api";
import {maskWindowEdits,type MaskWindowInput} from "./maskWindow";
import {createScopedDraft,stageDraftMaskWindow,draftPatch,encodeScopedDraft,decodeScopedDraft,
  scopedDraftScope,hydrateScopedDraft,rebaseScopedDraft,scopedDraftConflicts} from "./scopedChangeDraft";
import {previewTransform} from "./canvasMotion";
import {sampleProfileLayout} from "./profileComposition";
import type {CanvasNode,Project} from "./types";

const uuid=(n:number)=>"00000000-0000-4000-8000-"+String(n).padStart(12,"0");
function source(){
  const project=structuredClone(fixtureProject);
  project.schema_version=2;project.locks=[];project.reviews=[];project.branch_workspaces=[];project.merges=[];
  project.production_design=emptyProductionDesign();
  const scene=project.scenes[0];
  scene.renderer="motion-canvas";scene.status="draft";scene.beats=[];scene.start={num:"0",den:"1"};
  scene.duration={num:"6",den:"1"};scene.camera={center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  const original=scene.nodes[0],style={...original.style,fill:"#86C8D1",stroke:null,stroke_width:0,
    font_family:null,font_size:null,font_weight:null,line_height:null,blend_mode:"normal" as const};
  const clip:CanvasNode={...original,id:uuid(701),name:"Original Clip",kind:"clip_group",x:800,y:340,width:320,height:240,
    parent_id:null,rotation_deg:0,opacity:1,text:null,coordinate_space:"project_pixels",z_index:0,
    style:{...style,fill:null},relations:[],property_locks:[],keyframes:[]};
  const child:CanvasNode={...clip,id:uuid(702),name:"Original clipped rectangle",kind:"rectangle",parent_id:clip.id,
    x:880,y:410,width:160,height:100,z_index:1,style};
  const peer:CanvasNode={...child,id:uuid(703),name:"Unchanged peer",parent_id:null,x:1400,y:450,z_index:3};
  scene.nodes=[clip,child,peer];project.scenes=[scene];
  project.deliverables=project.deliverables.map(profile=>({...profile,included_scene_ids:[],protected_scene_ids:[],text_overrides:{},
    framing_strategy:"replan",frame_rate:{num:"30",den:"1"}}));
  const input:MaskWindowInput={childId:child.id,direction:"left",mode:"reveal",gap:16,startFrame:15,endFrame:45,curve:"linear"};
  return {project,scene,profile:project.deliverables[0],clip,child,peer,input};
}
function visibleWidth(child:{x:number;width:number},clip:{x:number;width:number}){
  return Math.max(0,Math.min(child.x+child.width,clip.x+clip.width)-Math.max(child.x,clip.x));
}
describe("exact source-derived mask window",()=>{
  it("holds a real child outside a static Clip, reveals it at full opacity and saves/reopens/undoes one original track",async()=>{
    const {project,scene,profile,clip,child,peer,input}=source(),before=structuredClone(project);
    const result=maskWindowEdits(project,scene,profile,input);
    expect(result.clipId).toBe(clip.id);expect(result.initial.x).toBe(624);expect(result.terminal.x).toBe(880);
    expect(result.edits[0].keyframes).toHaveLength(15);
    expect(result.edits[0].keyframes.some(key=>key.property==="opacity")).toBe(false);
    const draft=stageDraftMaskWindow(createScopedDraft(project,scene),project,scene,profile,input);
    expect(draft.edits).toHaveLength(1);
    expect(draft.recipeSource).toEqual({kind:"mask_window",revision:project.revision});
    const recovered=hydrateScopedDraft(decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(project,scene)),project,scene);
    const saved=await applyChange(project,{type:"apply_creative_patch",patch:draftPatch(recovered,scene.id)});
    expect(saved.revision).toBe(project.revision+1);expect(project).toEqual(before);
    const nodes=saved.scenes[0].nodes;
    expect(nodes[0]).toEqual(clip);expect(nodes[2]).toEqual(peer);expect({...nodes[1],keyframes:[]}).toEqual(child);
    for(const [time,x,width] of [[0,624,0],[.5,624,0],[1,752,112],[1.5,880,160],[5,880,160]]){
      const pose=previewTransform(nodes[1],time);
      expect(pose.x).toBeCloseTo(x);expect(pose.opacity).toBe(1);
      expect(visibleWidth(pose,clip)).toBeCloseTo(width);
    }
    for(const output of saved.deliverables){
      const a=sampleProfileLayout(saved,saved.scenes[0],output,.5).nodes;
      const b=sampleProfileLayout(saved,saved.scenes[0],output,1.5).nodes;
      expect(a[1].parent_id).toBe(clip.id);expect(a[0]).toEqual(b[0]);
      expect(visibleWidth(a[1],a[0])).toBeCloseTo(0);expect(visibleWidth(b[1],b[0])).toBeCloseTo(b[1].width);
    }
    const reopened:Project=JSON.parse(JSON.stringify(saved));
    const patchId=reopened.production_design?.patches?.at(-1)!.id!;
    const undone=await applyChange(reopened,{type:"undo_creative_patch",patch_id:patchId});
    expect(undone.scenes[0].nodes).toEqual(scene.nodes);
  });
  it("admits immediate reveal and exact conceal in each of four directions",()=>{
    const {project,scene,profile,child,input}=source();
    for(const [direction,x,y] of [["left",624,410],["right",1136,410],["top",880,224],["bottom",880,596]] as const){
      const enter=maskWindowEdits(project,scene,profile,{...input,direction,startFrame:0,curve:"ease_out_cubic"});
      expect(enter.edits[0].keyframes).toHaveLength(4);
      expect([enter.initial.x,enter.initial.y]).toEqual([x,y]);
      const hidden={...child,x,y};
      const candidate=structuredClone(project);candidate.scenes[0].nodes[1]=hidden;
      const exit=maskWindowEdits(candidate,candidate.scenes[0],candidate.deliverables[0],{...input,mode:"conceal",direction,startFrame:0});
      expect([exit.initial.x,exit.initial.y]).toEqual([880,410]);
      expect([exit.terminal.x,exit.terminal.y]).toEqual([x,y]);
      expect(exit.edits[0].keyframes).toHaveLength(4);
    }
  });
  it("fails closed for source conflicts, hidden or visible pose loss, crop, locks and incompatible clocks",()=>{
    const {project,scene,profile,input}=source();
    for(const invalid of [{...input,startFrame:-1},{...input,endFrame:180},{...input,mode:"conceal" as const},{...input,gap:0}])
      expect(()=>maskWindowEdits(project,scene,profile,invalid)).toThrow();
    const change=(edit:(p:Project)=>void)=>{
      const p=structuredClone(project);edit(p);return ()=>maskWindowEdits(p,p.scenes[0],p.deliverables[0],input);
    };
    expect(change(p=>{p.scenes[0].nodes[0].kind="group";})).toThrow(/root Clip/);
    expect(change(p=>{p.scenes[0].nodes[1].property_locks=["position"];})).toThrow(/unlocked/);
    expect(change(p=>{p.scenes[0].nodes[1].style.fill="#86C8D180";})).toThrow(/opaque filled/);
    expect(change(p=>{p.scenes[0].nodes[1].x=1120;})).toThrow(/fully visible/);
    expect(change(p=>{p.scenes[0].nodes[0].x=150;p.scenes[0].nodes[1].x=230;})).toThrow(/safe area/);
    expect(change(p=>{p.deliverables[1].framing_strategy="crop";})).toThrow(/Replan/);
    expect(change(p=>{p.deliverables[1].frame_rate={num:"25",den:"1"};})).toThrow(/frame-aligned/);
    // Wider effective Y separation can make a formerly contained child crop
    // after portrait Replan even when the source Canvas still contains it.
    expect(change(p=>{p.scenes[0].nodes[1].y=340;})).toThrow(/fully visible/);
  });
  it("binds recovery to the untouched Clip plane, not only the child's motion snapshot",async()=>{
    const {project,scene,profile,clip,input}=source();
    const draft=stageDraftMaskWindow(createScopedDraft(project,scene),project,scene,profile,input);
    expect(()=>stageDraftMaskWindow(draft,project,scene,profile,input)).toThrow(/Finish or discard/);
    const changed=await applyChange(project,{type:"transform_canvas_node",scene_id:scene.id,node_id:clip.id,
      transform:{x:clip.x+20,y:clip.y,width:clip.width,height:clip.height,rotation_deg:0,opacity:1}});
    const restored=decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(changed,changed.scenes[0]));
    expect(scopedDraftConflicts(restored,changed.scenes[0])).toEqual([]);
    expect(()=>rebaseScopedDraft(restored,changed,changed.scenes[0])).toThrow(/restage the recipe/);
    expect(restored.edits).toEqual(draft.edits);
  });
});
