import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { emptyProductionDesign } from "./creativeProduction";
import { fixtureProject } from "./fixture";
import { measuredOutput, validateMeasuredTable } from "./measuredData";
import type { MeasuredTable, MeasuredView } from "./measuredData";
import type { Asset, Project } from "./types";

const shaA="a".repeat(64),shaB="b".repeat(64),shaC="c".repeat(64);
const tableId="00000000-0000-4000-8000-000000000401";
const counterId="00000000-0000-4000-8000-000000000402";
const barId="00000000-0000-4000-8000-000000000403";
const source=(content_sha256:string):Asset=>({
  id:crypto.randomUUID(),name:"Imported measured report",media_type:"application/json",
  content_sha256,source_revision:"owner-imported-unreviewed",
});
const report=(asset:Asset,version:number,conversion:number):MeasuredTable=>({
  schema:"motionwright.measured-table/1",id:tableId,label:"Imported campaign report",
  version,source_asset_id:asset.id,source_sha256:asset.content_sha256!,
  rows:[
    {key:"conversion",label:"Conversion",milli:conversion,unit:"percent"},
    {key:"orders",label:"Orders",milli:128000,unit:"count"},
  ],
});
function seeded(){
  const project:Project=structuredClone(fixtureProject);
  project.schema_version=2;
  project.production_design=emptyProductionDesign();
  project.locks=[];project.branch_workspaces=[];project.reviews=[];project.merges=[];
  project.scenes=project.scenes.slice(0,2).map((scene,i)=>({
    ...scene,renderer:"motion-canvas" as const,status:"approved" as const,beats:[],
    nodes:i===0?[
      {...scene.nodes[0],kind:"text",text:"An authored placeholder"},
      {...scene.nodes[1],kind:"rectangle",width:800,text:null,opacity:1},
    ]:[],
  }));
  project.deliverables=project.deliverables.slice(0,2).map((profile,i)=>({
    ...profile,included_scene_ids:[project.scenes[i].id],
  }));
  const assets=[source(shaA),source(shaB),source(shaC)];
  project.assets=assets;
  const [sceneA,sceneB]=project.scenes;
  return {project,assets,sceneA,sceneB,nodeIds:[sceneA.nodes[0].id,sceneA.nodes[1].id]};
}
const barView:MeasuredView={kind:"bar",axis_min_milli:0,axis_max_milli:100000,max_width_px:800};
describe("measured data and explicit units",()=>{
  it("formats count, units and negative integer fixed-point without float inference",()=>{
    const value={key:"growth",label:"Measured growth",milli:-12505,unit:"percent" as const};
    expect(measuredOutput(value,{kind:"counter",decimals:2})).toEqual({kind:"text",value:"-12.51%"});
    expect(measuredOutput(value,{kind:"label",decimals:1})).toEqual({kind:"text",value:"Measured growth · -12.5%"});
    expect(measuredOutput(value,{kind:"counter",decimals:0})).toEqual({kind:"text",value:"-13%"});
    expect(measuredOutput({...value,milli:25000},barView)).toEqual({kind:"bar",width_px:200});
    expect(()=>measuredOutput({...value,milli:25000},{...barView,axis_min_milli:30000})).toThrow(/axis/);
  });
  it("revises only dependent objects, scenes and explicitly included variants",async()=>{
    const {project,assets,sceneA,sceneB,nodeIds}=seeded();
    let p=await applyChange(project,{type:"upsert_measured_table",table:report(assets[0],1,12500)});
    p=await applyChange(p,{type:"upsert_measured_binding",binding_id:counterId,
      scene_id:sceneA.id,node_id:nodeIds[0],table_id:tableId,datum_key:"conversion",
      view:{kind:"label",decimals:2}});
    p=await applyChange(p,{type:"upsert_measured_binding",binding_id:barId,
      scene_id:sceneA.id,node_id:nodeIds[1],table_id:tableId,datum_key:"conversion",view:barView});
    expect(p.scenes[0].nodes[0].text).toBe("Conversion · 12.50%");
    expect(p.scenes[0].nodes[1].width).toBe(100);
    p.scenes[0].status="approved";p.scenes[1].status="approved";
    const untouched=structuredClone(p.scenes[1]),style=structuredClone(p.scenes[0].nodes[0].style);
    p=await applyChange(p,{type:"upsert_measured_table",table:report(assets[1],2,37500)});
    expect(p.scenes[0].nodes[0].text).toBe("Conversion · 37.50%");
    expect(p.scenes[0].nodes[1].width).toBe(300);
    expect(p.scenes[0].nodes[0].style).toEqual(style);
    expect(p.scenes[0].status).toBe("draft");
    expect(p.scenes[1]).toEqual(untouched);
    expect(p.production_design?.last_measured_impact).toEqual({
      table_id:tableId,version:2,changed_keys:["conversion"],
      scene_ids:[sceneA.id],deliverable_ids:[p.deliverables[0].id],
    });
    p.scenes[0].status="approved";
    p=await applyChange(p,{type:"upsert_measured_table",table:report(assets[2],3,37500)});
    expect(p.scenes[0].status).toBe("approved");
    expect(p.production_design?.last_measured_impact?.scene_ids).toEqual([]);
    expect(p.production_design?.measured_bindings?.[0].view).toEqual({kind:"label",decimals:2});
  });
  it("refuses unbound sources, wrong schema, forged hashes, duplicate keys or absent values",async()=>{
    const {project,assets,sceneA,nodeIds}=seeded();
    const t=report(assets[0],1,12500);
    expect(()=>validateMeasuredTable({...t,source_sha256:shaB},project)).toThrow(/SHA-256/);
    expect(()=>validateMeasuredTable({...t,schema:"motionwright.measured-table/2"},project)).toThrow(/schema/);
    expect(()=>validateMeasuredTable({...t,rows:[t.rows[0],t.rows[0]]},project)).toThrow(/Duplicate|duplicate/);
    let p=await applyChange(project,{type:"upsert_measured_table",table:t});
    p=await applyChange(p,{type:"upsert_measured_binding",binding_id:counterId,
      scene_id:sceneA.id,node_id:nodeIds[0],table_id:tableId,datum_key:"conversion",
      view:{kind:"counter",decimals:1}});
    const snapshot=structuredClone(p);
    const missing=report(assets[1],2,22000);missing.rows=missing.rows.filter(row=>row.key!=="conversion");
    await expect(applyChange(p,{type:"upsert_measured_table",table:missing})).rejects.toThrow(/datum/);
    await expect(applyChange(p,{type:"upsert_measured_table",table:report(assets[1],1,22000)})).rejects.toThrow(/version/);
    await expect(applyChange(p,{type:"remove_asset",asset_id:assets[0].id})).rejects.toThrow(/measured table/);
    await expect(applyChange(p,{type:"upsert_measured_binding",binding_id:barId,
      scene_id:sceneA.id,node_id:nodeIds[1],table_id:tableId,datum_key:"conversion",
      view:{...barView,axis_min_milli:30000}})).rejects.toThrow(/axis/);
    expect(p).toEqual(snapshot);
    const edited=structuredClone(p);edited.scenes[0].nodes[0].text="Human changed the label";
    await expect(applyChange(edited,{type:"upsert_measured_table",table:report(assets[1],2,22000)})).rejects.toThrow(/detach/);
    const locked=structuredClone(p);locked.scenes[0].nodes[0].property_locks.push("text");
    await expect(applyChange(locked,{type:"upsert_measured_table",table:report(assets[1],2,22000)})).rejects.toThrow(/locked/);
  });
  it("detach retains prior content and old projects remain valid without data fields",async()=>{
    const {project,assets,sceneA,nodeIds}=seeded();
    let p=await applyChange(project,{type:"upsert_measured_table",table:report(assets[0],1,55000)});
    p=await applyChange(p,{type:"upsert_measured_binding",binding_id:counterId,
      scene_id:sceneA.id,node_id:nodeIds[0],table_id:tableId,datum_key:"conversion",
      view:{kind:"counter",decimals:1}});
    p=await applyChange(p,{type:"detach_measured_binding",binding_id:counterId});
    expect(p.scenes[0].nodes[0].text).toBe("55.0%");
    expect(p.production_design?.measured_bindings).toEqual([]);
    p=await applyChange(p,{type:"update_canvas_text",scene_id:sceneA.id,node_id:nodeIds[0],text:"Editorial independent"});
    expect(p.scenes[0].nodes[0].text).toBe("Editorial independent");
    const legacy=structuredClone(project);
    delete legacy.production_design?.measured_tables;
    delete legacy.production_design?.measured_bindings;
    delete legacy.production_design?.last_measured_impact;
    expect((await applyChange(legacy,{type:"rename_project",title:"Old project remains editable"})).title)
      .toBe("Old project remains editable");
  });
});
