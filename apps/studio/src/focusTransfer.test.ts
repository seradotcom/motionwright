import {describe,it,expect} from "vitest";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import {applyChange} from "./api";
import {focusTransferEdits} from "./focusTransfer";
import {createScopedDraft,stageDraftFocusTransfer,draftPatch,encodeScopedDraft,decodeScopedDraft,scopedDraftScope,hydrateScopedDraft,rebaseScopedDraft,scopedDraftConflicts} from "./scopedChangeDraft";
import {previewTransform} from "./canvasMotion";
import type {CanvasNode,Project} from "./types";

function source(){
  const p=structuredClone(fixtureProject);
  p.locks=[];p.reviews=[];p.branch_workspaces=[];p.production_design=emptyProductionDesign();
  const scene=p.scenes[0];scene.renderer="motion-canvas";scene.status="draft";scene.beats=[];
  scene.duration={num:"6",den:"1"};
  const shape=(id:string,x:number):CanvasNode=>({...scene.nodes[0],id,kind:"rectangle",name:"Original "+id.slice(-1),
    x,y:300,width:220,height:100,rotation_deg:0,opacity:1,text:null,parent_id:null,coordinate_space:"project_pixels",
    style:{...scene.nodes[0].style,fill:"#86C8D1",stroke:null,stroke_width:0,font_family:null,font_size:null,font_weight:null,line_height:null,blend_mode:"normal"},
    keyframes:[],relations:[],property_locks:[]});
  const fromId="00000000-0000-4000-8000-000000000011",toId="00000000-0000-4000-8000-000000000012";
  scene.nodes=[shape(fromId,300),shape(toId,700),shape("00000000-0000-4000-8000-000000000013",1100)];
  p.scenes=[scene];p.deliverables=p.deliverables.map(profile=>({...profile,included_scene_ids:[],protected_scene_ids:[],text_overrides:{},
    frame_rate:{num:"30",den:"1"}}));
  const profile=p.deliverables[0];
  return {p,scene,profile,input:{fromId,toId,startFrame:15,endFrame:45,curve:"linear" as const}};
}
describe("versioned focus transfer pattern",()=>{
  it("creates two complementary exact original tracks, saves one patch and preserves all other bytes",async()=>{
    const {p,scene,profile,input}=source(),before=structuredClone(p);
    const draft=stageDraftFocusTransfer(createScopedDraft(p,scene),p,scene,profile,input);
    expect(draft.edits).toHaveLength(2);expect(draft.origins).toHaveLength(2);
    const reopened=hydrateScopedDraft(decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(p,scene)),p,scene);
    expect(draftPatch(reopened,scene.id)).toEqual(draftPatch(draft,scene.id));
    const next=await applyChange(p,{type:"apply_creative_patch",patch:draftPatch(reopened,scene.id)});
    expect(next.revision).toBe(p.revision+1);
    const nodes=next.scenes[0].nodes;
    expect(nodes.map(node=>node.id)).toEqual(scene.nodes.map(node=>node.id));
    for(const [time,a,b] of [[0,1,0],[.5,1,0],[1,.5,.5],[1.5,0,1],[2,0,1]]){
      expect(previewTransform(nodes[0],time).opacity).toBeCloseTo(a);
      expect(previewTransform(nodes[1],time).opacity).toBeCloseTo(b);
    }
    expect(nodes.map(node=>({...node,keyframes:[]}))).toEqual(scene.nodes);
    expect(next.production_design?.patches).toHaveLength(1);
    expect(p).toEqual(before);
    const persisted:Project=JSON.parse(JSON.stringify(next));
    expect(persisted.scenes[0].nodes).toEqual(nodes);
  });
  it("refuses ambiguous, locked, already moving, managed, or differently clocked source before staging",()=>{
    const {p,scene,profile,input}=source();
    for(const invalid of [{...input,toId:input.fromId},{...input,endFrame:180},{...input,startFrame:-1},{...input,startFrame:45}]){
      expect(()=>focusTransferEdits(p,scene,profile,invalid)).toThrow();
    }
    scene.nodes[0].property_locks=["opacity"];
    expect(()=>focusTransferEdits(p,scene,profile,input)).toThrow(/unlocked/);
    scene.nodes[0].property_locks=[];
    scene.nodes[0].keyframes=[{at:{num:"0",den:"1"},property:"x",value:300,interpolation:"linear"}];
    expect(()=>focusTransferEdits(p,scene,profile,input)).toThrow(/unanimated/);
    scene.nodes[0].keyframes=[];
    p.deliverables[1].frame_rate={num:"25",den:"1"};
    expect(()=>focusTransferEdits(p,scene,profile,input)).toThrow(/frame-aligned/);
  });
  it("does not overwrite a retained proposal or stage against another revision",()=>{
    const {p,scene,profile,input}=source();
    const draft=stageDraftFocusTransfer(createScopedDraft(p,scene),p,scene,profile,input);
    expect(()=>stageDraftFocusTransfer(draft,p,scene,profile,input)).toThrow(/Finish or discard/);
    const stale={...createScopedDraft(p,scene),baseRevision:p.revision-1};
    expect(()=>stageDraftFocusTransfer(stale,p,scene,profile,input)).toThrow(/current scene/);
  });
});

it("never reuses a recovered generated transfer at a new output clock",async()=>{
  const {p,scene,profile,input}=source();
  const draft=stageDraftFocusTransfer(createScopedDraft(p,scene),p,scene,profile,input);
  const next=await applyChange(p,{type:"upsert_deliverable",profile:{...p.deliverables[1],frame_rate:{num:"25",den:"1"}}});
  const current=next.scenes[0],raw=encodeScopedDraft(draft);
  expect(scopedDraftConflicts(draft,current)).toEqual([]);
  const recovered=decodeScopedDraft(raw,scopedDraftScope(next,current));
  const recoveryBytes=encodeScopedDraft(recovered);
  expect(recovered).toEqual(draft);
  expect(()=>rebaseScopedDraft(recovered,next,current)).toThrow(/restage.*recipe|recipe.*restage/i);
  expect(encodeScopedDraft(recovered)).toBe(recoveryBytes);
  expect(()=>stageDraftFocusTransfer(createScopedDraft(next,current),next,current,next.deliverables[0],input)).toThrow(/frame-aligned/);
});

it("recovery rejects malformed or relabeled recipe source without changing the original draft",()=>{
  const {p,scene,profile,input}=source();
  const draft=stageDraftFocusTransfer(createScopedDraft(p,scene),p,scene,profile,input);
  for(const recipeSource of [
    {kind:"focus_transfer",revision:p.revision+1},
    {kind:"unrecognized",revision:p.revision},
    {kind:["focus_transfer"],revision:p.revision},
    {kind:"focus_transfer",revision:p.revision,extra:true},
    null,
  ]){
    expect(()=>decodeScopedDraft(JSON.stringify({...draft,recipeSource}),draft.scope)).toThrow(/unsupported shape/);
  }
  const legacy={...draft};delete legacy.recipeSource;
  expect(decodeScopedDraft(encodeScopedDraft(legacy),draft.scope).recipeSource).toBeUndefined();
  expect(decodeScopedDraft(encodeScopedDraft(draft),draft.scope).recipeSource).toEqual(draft.recipeSource);
});
