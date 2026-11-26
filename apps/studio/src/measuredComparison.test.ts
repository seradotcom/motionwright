import {describe,it,expect} from "vitest";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import {applyChange} from "./api";
import {applyMeasuredBinding,applyMeasuredTable,type MeasuredTable,type MeasuredView} from "./measuredData";
import {measuredComparisonEdits,measuredComparisonRows} from "./measuredComparison";
import {createScopedDraft,stageDraftMeasuredComparison,draftPatch,encodeScopedDraft,decodeScopedDraft,scopedDraftScope,hydrateScopedDraft,rebaseScopedDraft,scopedDraftConflicts} from "./scopedChangeDraft";
import {sampleProfileLayout} from "./profileComposition";
import type {Asset,CanvasNode,Project} from "./types";

const uuid=(n:number)=>"00000000-0000-4000-8000-"+String(n).padStart(12,"0");
export function source(){
  const p=structuredClone(fixtureProject);
  p.schema_version=2;p.locks=[];p.reviews=[];p.branch_workspaces=[];p.merges=[];p.production_design=emptyProductionDesign();
  const scene=p.scenes[0];scene.renderer="motion-canvas";scene.status="draft";scene.beats=[];
  scene.duration={num:"6",den:"1"};scene.camera={center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  const template=scene.nodes[0];
  scene.nodes=Array.from({length:5},(_,i):CanvasNode=>({...template,id:uuid(800+i),name:"Original "+i,
    kind:i%2===0&&i<4?"text":"rectangle",x:300+i*30,y:300+i*20,width:800,height:100,
    rotation_deg:0,opacity:1,parent_id:null,text:i%2===0&&i<4?"Placeholder":null,coordinate_space:"project_pixels",z_index:i,
    style:{...template.style,fill:i%2===0?"#F2F4F3":"#86C8D1",stroke:null,stroke_width:0,font_family:i%2===0&&i<4?"Instrument Sans Variable":null,
      font_size:i%2===0&&i<4?48:null,font_weight:i%2===0&&i<4?400:null,line_height:i%2===0&&i<4?1.12:null,blend_mode:"normal"},
    relations:[],property_locks:[],keyframes:[]}));
  p.scenes=[scene];p.deliverables=p.deliverables.map(profile=>({...profile,included_scene_ids:[],protected_scene_ids:[],text_overrides:{}}));
  const asset=(id:number,hash:string):Asset=>({id:uuid(id),name:"Fixture source",media_type:"application/json",content_sha256:hash.repeat(64),source_revision:null});
  const assets=[asset(810,"a"),asset(811,"b")];p.assets=assets;
  const table:MeasuredTable={schema:"motionwright.measured-table/1",id:uuid(820),label:"Imported state measurements",version:1,
    source_asset_id:assets[0].id,source_sha256:assets[0].content_sha256!,
    rows:[{key:"before",label:"Before",milli:25000,unit:"percent"},{key:"after",label:"After",milli:60000,unit:"percent"}]};
  applyMeasuredTable(p,table);
  const bar:MeasuredView={kind:"bar",axis_min_milli:0,axis_max_milli:100000,max_width_px:800};
  for(let i=0;i<4;i++)applyMeasuredBinding(p,{binding_id:uuid(830+i),scene_id:scene.id,node_id:scene.nodes[i].id,
    table_id:table.id,datum_key:i<2?"before":"after",view:i%2===0?{kind:"label",decimals:1}:bar});
  return {p,scene:p.scenes[0],assets,table,input:{beforeId:uuid(830),afterId:uuid(832),left:240,top:180,rowSpacing:280,labelGap:20,barHeight:48}};
}

describe("measured state comparison authoring",()=>{
  it("saves a four-object reversible layout and preserves exact measured bindings across all three output aspects",async()=>{
    const {p,scene,input}=source(),before=structuredClone(p);
    expect(measuredComparisonRows(p,scene)).toHaveLength(2);
    const result=measuredComparisonEdits(p,scene,input);
    expect(result.edits.map(edit=>[edit.transform.x,edit.transform.y,edit.transform.width,edit.transform.height])).toEqual([
      [240,180,800,100],[240,300,200,48],[240,460,800,100],[240,580,480,48]]);
    const draft=stageDraftMeasuredComparison(createScopedDraft(p,scene),p,scene,input);
    expect(draft.edits).toHaveLength(4);
    const restored=hydrateScopedDraft(decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(p,scene)),p,scene);
    const saved=await applyChange(p,{type:"apply_creative_patch",patch:draftPatch(restored,scene.id)});
    expect(saved.revision).toBe(p.revision+1);
    expect(saved.production_design?.measured_bindings).toEqual(p.production_design?.measured_bindings);
    expect(saved.production_design?.measured_tables).toEqual(p.production_design?.measured_tables);
    expect(saved.scenes[0].nodes[4]).toEqual(scene.nodes[4]);
    for(const profile of saved.deliverables){
      const sample=sampleProfileLayout(saved,saved.scenes[0],profile,2);
      const [a,b,c,d]=sample.nodes;
      expect(a.x).toBeCloseTo(b.x);expect(c.x).toBeCloseTo(d.x);expect(b.x).toBeCloseTo(d.x);
      expect(d.width/b.width).toBeCloseTo(2.4);
      expect(a.text).toBe("Before \u00b7 25.0%");expect(c.text).toBe("After \u00b7 60.0%");
    }
    expect(p).toEqual(before);
    const reopened:Project=JSON.parse(JSON.stringify(saved));
    expect(reopened.production_design?.measured_bindings).toEqual(saved.production_design?.measured_bindings);
    const record=saved.production_design?.patches?.at(-1)!;
    const undone=await applyChange(reopened,{type:"undo_creative_patch",patch_id:record.id});
    expect(undone.scenes[0].nodes).toEqual(scene.nodes);
  });
  it("keeps the ordinary measured dependency path active after a saved comparison",async()=>{
    const {p,scene,input,assets,table}=source();
    const draft=stageDraftMeasuredComparison(createScopedDraft(p,scene),p,scene,input);
    const saved=await applyChange(p,{type:"apply_creative_patch",patch:draftPatch(draft,scene.id)});
    const next=await applyChange(saved,{type:"upsert_measured_table",table:{...table,version:2,
      source_asset_id:assets[1].id,source_sha256:assets[1].content_sha256!,rows:[table.rows[0],{...table.rows[1],milli:75000}]}});
    expect(next.scenes[0].nodes[3].width).toBe(600);
    expect(next.scenes[0].nodes[2].text).toBe("After \u00b7 75.0%");
    expect(next.scenes[0].nodes[3].x).toBe(240);
    expect(next.production_design?.last_measured_impact?.deliverable_ids).toHaveLength(3);
  });
  it("refuses incompatible unit, scale, precision, bounds, locks and unrelated draft input",()=>{
    const {p,scene,input}=source();
    expect(()=>measuredComparisonEdits(p,scene,{...input,afterId:input.beforeId})).toThrow(/distinct/);
    expect(()=>measuredComparisonEdits(p,scene,{...input,left:1400})).toThrow(/safe area/);
    expect(()=>measuredComparisonEdits(p,scene,{...input,rowSpacing:100})).toThrow(/overlap/);
    scene.nodes[1].property_locks=["position"];
    expect(()=>measuredComparisonEdits(p,scene,input)).toThrow(/unlocked/);
    scene.nodes[1].property_locks=[];
    const bindings=p.production_design!.measured_bindings!;
    applyMeasuredBinding(p,{...bindings[3],binding_id:bindings[3].id,view:{kind:"bar",axis_min_milli:0,axis_max_milli:200000,max_width_px:800}});
    expect(()=>measuredComparisonEdits(p,p.scenes[0],input)).toThrow(/same explicit/);
  });
  it("requires matching units and refuses stale explicit delivery copy",async()=>{
    const {p,scene,input,assets,table}=source();
    const changed=await applyChange(p,{type:"upsert_measured_table",table:{...table,version:2,
      source_asset_id:assets[1].id,source_sha256:assets[1].content_sha256!,rows:[table.rows[0],{...table.rows[1],unit:"count"}]}});
    expect(()=>measuredComparisonEdits(changed,changed.scenes[0],input)).toThrow(/identical units/);
    p.deliverables[0].text_overrides[scene.nodes[0].id]="A forged figure";
    expect(()=>measuredComparisonEdits(p,scene,input)).toThrow(/Measured text/);
  });
});

it("retains a generated comparison's revision binding even when a new measured source leaves all geometry unchanged",async()=>{
  const {p,scene,input,assets,table}=source();
  const draft=stageDraftMeasuredComparison(createScopedDraft(p,scene),p,scene,input);
  const retained=encodeScopedDraft(draft);
  const next=await applyChange(p,{type:"upsert_measured_table",table:{...table,version:2,
    source_asset_id:assets[1].id,source_sha256:assets[1].content_sha256!,rows:[table.rows[0],{...table.rows[1],unit:"count"}]}});
  const current=next.scenes[0];
  expect(scopedDraftConflicts(draft,current)).toEqual([]);
  const recovered=decodeScopedDraft(retained,scopedDraftScope(next,current));
  const recoveryBytes=encodeScopedDraft(recovered);
  expect(recovered).toEqual(draft);
  expect(()=>rebaseScopedDraft(recovered,next,current)).toThrow(/restage.*recipe|recipe.*restage/i);
  expect(encodeScopedDraft(recovered)).toBe(recoveryBytes);
  expect(()=>stageDraftMeasuredComparison(createScopedDraft(next,current),next,current,input)).toThrow(/identical units/);
});
