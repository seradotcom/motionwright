import { describe,expect,it } from "vitest";
import { defaultMotionRangeInput,retimeMotionRows } from "./timelineMotionRange";
import { parseScopedMotion,scopedMotionRows,SCOPED_MOTION_PROPERTIES } from "./scopedPaintMotion";
import { applyDraftMotionRange,createScopedDraft,decodeScopedDraft,draftMotionInput,draftPatch,encodeScopedDraft,
  queueDraftEdit,retainUnqueuedInputs,scopedDraftScope,selectDraftNode,unqueuedInputCount,updateDraftMotionRangeInput } from "./scopedChangeDraft";
import { fixtureProject } from "./fixture";
import { applyChange,previewCreativePatch } from "./api";
import type { CanvasKeyframe } from "./types";

const duration={num:"6",den:"1"};
const keys:CanvasKeyframe[]=[0,.5,1.5].flatMap((at,index)=>SCOPED_MOTION_PROPERTIES.map((property):CanvasKeyframe=>({
  at:{num:String(at*2),den:"2"},property,value:property==="opacity"?(index===1?1:0):property==="rotation_deg"?index*30:100+index*20,
  interpolation:index===1?"hold":index===2?"ease_out_cubic":"linear",
}))).map(key=>({...key,at:key.at.num==="0"?{num:"0",den:"1"}:key.at}));
const rows=scopedMotionRows(keys);

describe("complete animation range editing",()=>{
  it("moves every property together and retains the exact relative intervals, values, arrival curves and holds",()=>{
    const result=parseScopedMotion(retimeMotionRows(rows,{mode:"move",start:"1/30",end:"ignored"},duration));
    expect([...new Set(result.map(key=>key.at.num+"/"+key.at.den))]).toEqual(["1/30","8/15","23/15"]);
    expect(result.map(({at:_,...key})=>key)).toEqual(keys.map(({at:_,...key})=>key));
    expect(rows).toEqual(scopedMotionRows(keys));
  });
  it("fits all channels affinely without resampling or inventing endpoint keys",()=>{
    const result=parseScopedMotion(retimeMotionRows(rows,{mode:"fit",start:"1/30",end:"91/30"},duration));
    expect([...new Set(result.map(key=>key.at.num+"/"+key.at.den))]).toEqual(["1/30","31/30","91/30"]);
    expect(result.map(({at:_,...key})=>key)).toEqual(keys.map(({at:_,...key})=>key));
  });
  it("retains values above IEEE-754 integer precision through the exact rational time transform",()=>{
    const denominator="9007199254740995";
    const source=[{at:"0",property:"x",value:"10",interpolation:"linear"},
      {at:"9007199254740993/"+denominator,property:"x",value:"20",interpolation:"hold"}] as const;
    const result=parseScopedMotion(retimeMotionRows([...source],{mode:"move",start:"1/"+denominator,end:""},duration));
    expect(result.map(key=>key.at)).toEqual([{num:"1",den:denominator},{num:"9007199254740994",den:denominator}]);
  });
  it.each([
    {mode:"move",start:"-1",end:""}, {mode:"move",start:"4.5",end:""},
    {mode:"fit",start:"2",end:"2"}, {mode:"fit",start:"2",end:"1"},
    {mode:"fit",start:"0",end:"6"}, {mode:"fit",start:"1/",end:"3"},
  ] as const)("refuses an incomplete, reversed, negative or half-open boundary range: %j",input=>{
    const before=structuredClone(rows);
    expect(()=>retimeMotionRows(rows,input,duration)).toThrow();
    expect(rows).toEqual(before);
  });
  it("moves a single instant, refuses stretching a zero-length source and rejects no-op ranges",()=>{
    const single=[rows[0]];
    expect(parseScopedMotion(retimeMotionRows(single,{mode:"move",start:"1",end:""},duration))[0].at).toEqual({num:"1",den:"1"});
    expect(()=>retimeMotionRows(single,{mode:"fit",start:"1",end:"2"},duration)).toThrow(/distinct/);
    expect(()=>retimeMotionRows(rows,defaultMotionRangeInput(rows),duration)).toThrow(/already/);
  });
  it("preserves incomplete range inputs across object changes, recovery and unrelated receipts; applies only after explicit range staging",async()=>{
    const project=structuredClone(fixtureProject); project.schema_version=2;project.locks=[];project.branch_workspaces=[];
    project.production_design={plan:null,heroes:[],capsules:[],patches:[]};
    const scene=project.scenes[0];scene.duration=duration;scene.status="draft";
    const node=scene.nodes[0];node.keyframes=structuredClone(keys);node.property_locks=[];
    scene.nodes.push({...structuredClone(node),id:"40000000-0000-4000-8000-000000000001",keyframes:[]});
    let draft=createScopedDraft(project,scene);draft={...draft,editor:{...draft.editor,kind:"motion"}};
    draft=updateDraftMotionRangeInput(draft,{mode:"fit",start:"1/",end:"3"});
    expect(unqueuedInputCount(draft)).toBe(1);
    const frozen=structuredClone(draft);
    expect(()=>applyDraftMotionRange(draft,duration)).toThrow();
    expect(draft).toEqual(frozen);
    draft=selectDraftNode(draft,scene.nodes.at(-1));
    draft=decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(project,scene));
    draft=selectDraftNode(draft,node);
    expect(draft.editor.motionRangeRaw).toEqual({mode:"fit",start:"1/",end:"3"});
    const receipt=structuredClone(project);receipt.revision++;
    draft=retainUnqueuedInputs(draft,receipt,receipt.scenes[0]);
    expect(draft.editor.motionRangeRaw?.start).toBe("1/");
    draft=updateDraftMotionRangeInput(draft,{mode:"fit",start:"0",end:"3"});
    draft=applyDraftMotionRange(draft,duration);
    expect(draft.editor.motionRangeRaw).toBeUndefined();
    expect(parseScopedMotion(draftMotionInput(draft)).at(-1)!.at).toEqual({num:"3",den:"1"});
    draft=queueDraftEdit({...draft,rationale:"Keep the full pose and lengthen its held interval."});
    const preview=await previewCreativePatch(receipt,draftPatch(draft,scene.id));
    const saved=await applyChange(receipt,{type:"apply_creative_patch",patch:draftPatch(draft,scene.id)});
    expect(saved.scenes[0].nodes.find(item=>item.id===node.id)).toEqual(preview.after[0]);
    expect(saved.revision).toBe(receipt.revision+1);
    const reopened=JSON.parse(JSON.stringify(saved));
    const undone=await applyChange(reopened,{type:"undo_creative_patch",patch_id:saved.production_design!.patches!.at(-1)!.id});
    expect(undone.scenes[0].nodes).toEqual(project.scenes[0].nodes);
  });
});
