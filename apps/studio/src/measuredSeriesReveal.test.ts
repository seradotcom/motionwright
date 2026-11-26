import {describe,it,expect} from "vitest";
import {fixtureProject} from "./fixture";
import {emptyProductionDesign} from "./creativeProduction";
import {applyChange} from "./api";
import {applyMeasuredTable,applyMeasuredBinding,type MeasuredTable} from "./measuredData";
import {isMeasurementDate,measuredSeriesRevealEdits,type MeasuredSeriesRevealInput} from "./measuredSeriesReveal";
import {createScopedDraft,stageDraftMeasuredSeriesReveal,draftPatch,encodeScopedDraft,decodeScopedDraft,scopedDraftScope,rebaseScopedDraft} from "./scopedChangeDraft";
import {sampleProfileLayout} from "./profileComposition";
import {previewTransform} from "./canvasMotion";
import type {CanvasNode,Project} from "./types";

const uuid=(n:number)=>"00000000-0000-4000-8000-"+String(n).padStart(12,"0");
function source(){
  const project=structuredClone(fixtureProject);
  project.schema_version=2;project.locks=[];project.reviews=[];project.branch_workspaces=[];project.merges=[];
  project.production_design=emptyProductionDesign();
  const scene=project.scenes[0];scene.renderer="motion-canvas";scene.status="draft";scene.beats=[];
  scene.duration={num:"6",den:"1"};scene.camera={center_x:960,center_y:540,zoom:1,rotation_deg:0,safe_margin:.05};
  const template=scene.nodes[0];
  scene.nodes=Array.from({length:7},(_,i):CanvasNode=>({...template,id:uuid(900+i),name:"Series source "+i,
    kind:i%2===0&&i<6?"text":"rectangle",x:300+i*20,y:300+i*15,width:800,height:100,
    parent_id:null,coordinate_space:"project_pixels",rotation_deg:0,opacity:1,z_index:i,text:i%2===0&&i<6?"Placeholder":null,
    style:{...template.style,fill:i%2===0?"#F2F4F3":"#86C8D1",stroke:null,stroke_width:0,
      font_family:i%2===0&&i<6?"Instrument Sans Variable":null,font_size:i%2===0&&i<6?48:null,
      font_weight:i%2===0&&i<6?400:null,line_height:i%2===0&&i<6?1.12:null,blend_mode:"normal"},
    relations:[],property_locks:[],keyframes:[]}));
  project.scenes=[scene];
  project.deliverables=project.deliverables.map(profile=>({...profile,included_scene_ids:[],protected_scene_ids:[],text_overrides:{},frame_rate:{num:"30",den:"1"}}));
  const assets=["a","b"].map((hash,i)=>({id:uuid(920+i),name:"Synthetic dated measurements",media_type:"application/json",content_sha256:hash.repeat(64),source_revision:null}));
  project.assets=assets;
  const table:MeasuredTable={schema:"motionwright.measured-table/1",id:uuid(930),label:"Dated observations",version:1,
    source_asset_id:assets[0].id,source_sha256:assets[0].content_sha256!,rows:[
      {key:"late",label:"2026-11-19",milli:75000,unit:"percent"},
      {key:"early",label:"2026-10-01",milli:25000,unit:"percent"},
      {key:"middle",label:"2026-10-03",milli:60000,unit:"percent"},
    ]};
  applyMeasuredTable(project,table);
  for(let i=0;i<6;i++)applyMeasuredBinding(project,{binding_id:uuid(940+i),scene_id:scene.id,node_id:scene.nodes[i].id,
    table_id:table.id,datum_key:table.rows[Math.floor(i/2)].key,
    view:i%2===0?{kind:"label",decimals:1}:{kind:"bar",axis_min_milli:0,axis_max_milli:100000,max_width_px:800}});
  const input:MeasuredSeriesRevealInput={tableId:table.id,left:240,top:120,rowSpacing:210,labelGap:16,barHeight:40,
    startFrame:0,stepFrames:30,fadeFrames:15,curve:"linear"};
  return {project,scene:project.scenes[0],profile:project.deliverables[0],input,table,assets};
}
describe("source-bound dated series reveal",()=>{
  it("admits only real calendar dates",()=>{
    for(const date of ["2024-02-29","2026-10-01","2000-02-29"])expect(isMeasurementDate(date)).toBe(true);
    for(const date of ["2026-02-29","1900-02-29","2026-13-01","2026-04-31","0000-01-01","2026-1-01","Week 1"])
      expect(isMeasurementDate(date)).toBe(false);
  });
  it("reveals every original row in actual calendar order, at exact editorial intervals, and retains source, aspect ratios and undo",async()=>{
    const {project,scene,profile,input}=source(),original=structuredClone(project);
    const result=measuredSeriesRevealEdits(project,scene,profile,input);
    expect(result.dates).toEqual(["2026-10-01","2026-10-03","2026-11-19"]);
    expect(result.intervals.map(i=>[i.startFrame,i.endFrame])).toEqual([[0,15],[30,45],[60,75]]);
    expect(result.edits).toHaveLength(12);
    expect(result.rationale).toContain("not a continuous time axis");
    const draft=stageDraftMeasuredSeriesReveal(createScopedDraft(project,scene),project,scene,profile,input);
    expect(draft.origins).toHaveLength(6);
    expect(draft.recipeSource).toEqual({kind:"measured_series_reveal",revision:project.revision});
    const restored=decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(project,scene));
    expect(restored).toEqual(draft);
    const saved=await applyChange(project,{type:"apply_creative_patch",patch:draftPatch(restored,scene.id)});
    expect(saved.revision).toBe(project.revision+1);
    expect(saved.production_design?.measured_bindings).toEqual(project.production_design?.measured_bindings);
    expect(saved.production_design?.measured_tables).toEqual(project.production_design?.measured_tables);
    const nodes=saved.scenes[0].nodes;
    expect(nodes[6]).toEqual(scene.nodes[6]);
    expect(nodes.slice(0,6).map(n=>n.id)).toEqual(scene.nodes.slice(0,6).map(n=>n.id));
    for(const [i,start] of [[0,2],[2,0],[4,1]]){
      const pair=nodes.slice(i,i+2);
      expect(pair[0].y).toBe(120+210*start);
      expect(pair[1].y).toBe(236+210*start);
      expect(pair[0].text).toBe(scene.nodes[i].text);
      expect(pair[1].width).toBe(scene.nodes[i+1].width);
      for(const [time,alpha] of [[0,0],[start+.25,.5],[start+.5,1],[5,1]]){
        for(const node of pair)expect(previewTransform(node,time).opacity).toBeCloseTo(alpha);
      }
    }
    for(const profile of saved.deliverables){
      const sample=sampleProfileLayout(saved,saved.scenes[0],profile,3);
      const bars=[sample.nodes[3],sample.nodes[5],sample.nodes[1]];
      expect(bars.map(n=>n.x)).toEqual([bars[0].x,bars[0].x,bars[0].x]);
      expect(bars[1].width/bars[0].width).toBeCloseTo(2.4);
      expect(bars[2].width/bars[0].width).toBeCloseTo(3);
      expect(bars.map(n=>n.opacity)).toEqual([1,1,1]);
    }
    expect(project).toEqual(original);
    const reopened:Project=JSON.parse(JSON.stringify(saved));
    const record=reopened.production_design?.patches?.at(-1)!;
    const undone=await applyChange(reopened,{type:"undo_creative_patch",patch_id:record.id});
    expect(undone.scenes[0].nodes).toEqual(scene.nodes);
  });
  it("refuses omitted rows, missing/duplicate dates, mixed units, unrepresentable scale and unsupported clocks",async()=>{
    const {project,scene,profile,input,table,assets}=source();
    expect(()=>measuredSeriesRevealEdits(project,scene,profile,{...input,stepFrames:10})).toThrow(/non-overlapping/);
    expect(()=>measuredSeriesRevealEdits(project,scene,profile,{...input,startFrame:150})).toThrow(/strictly inside/);
    const changed=structuredClone(project);
    changed.deliverables[1].frame_rate={num:"25",den:"1"};
    expect(()=>measuredSeriesRevealEdits(changed,changed.scenes[0],changed.deliverables[0],input)).toThrow(/frame-aligned/);
    const update=async(rows:MeasuredTable["rows"])=>applyChange(project,{type:"upsert_measured_table",table:{...table,version:2,
      source_asset_id:assets[1].id,source_sha256:assets[1].content_sha256!,rows}});
    for(const label of ["2026-02-30",table.rows[1].label,"Unmeasured date"]){
      const next=await update([{...table.rows[0],label},...table.rows.slice(1)]);
      expect(()=>measuredSeriesRevealEdits(next,next.scenes[0],next.deliverables[0],input)).toThrow(/distinct valid ISO/);
    }
    const mixed=await update([{...table.rows[0],unit:"count"},...table.rows.slice(1)]);
    expect(()=>measuredSeriesRevealEdits(mixed,mixed.scenes[0],mixed.deliverables[0],input)).toThrow(/identical units/);
    const missing=structuredClone(project);
    missing.production_design!.measured_bindings=missing.production_design!.measured_bindings!.slice(0,5);
    expect(()=>measuredSeriesRevealEdits(missing,missing.scenes[0],missing.deliverables[0],input)).toThrow(/every dated source row/);
    const locked=structuredClone(project);locked.scenes[0].nodes[0].property_locks=["opacity"];
    expect(()=>measuredSeriesRevealEdits(locked,locked.scenes[0],locked.deliverables[0],input)).toThrow(/opacity is locked/);
  });
  it("binds recovery to the exact source revision and refuses overwriting pending proposals",async()=>{
    const {project,scene,profile,input,table,assets}=source();
    const draft=stageDraftMeasuredSeriesReveal(createScopedDraft(project,scene),project,scene,profile,input);
    expect(()=>stageDraftMeasuredSeriesReveal(draft,project,scene,profile,input)).toThrow(/Finish or discard/);
    const changed=await applyChange(project,{type:"upsert_measured_table",table:{...table,version:2,source_asset_id:assets[1].id,
      source_sha256:assets[1].content_sha256!,rows:table.rows}});
    const recovered=decodeScopedDraft(encodeScopedDraft(draft),scopedDraftScope(changed,changed.scenes[0]));
    expect(()=>rebaseScopedDraft(recovered,changed,changed.scenes[0])).toThrow(/restage the recipe/);
    expect(recovered.edits).toEqual(draft.edits);
  });
});
