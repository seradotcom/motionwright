import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { defaultProductStageConfig } from "./productStage";
import { defaultProceduralConfig } from "./proceduralField";
import type { Project, CanvasNode } from "./types";
import type { MeasuredTable } from "./measuredData";

const STAGE="00000000-0000-4000-8000-000000000005";
const FIELD="00000000-0000-4000-8000-000000000095";
const TABLE="00000000-0000-4000-8000-000000000406";
const hashA="a".repeat(64),hashB="b".repeat(64);

async function integrated() {
  let p:Project=structuredClone(fixtureProject);
  p.schema_version=2;
  p.branch_workspaces=[];
  p.locks=[];
  p.reviews=[];
  p.merges=[];
  p.production_design=emptyProductionDesign();
  p.scenes=p.scenes.slice(0,3).map(scene=>({
    ...scene,renderer:"motion-canvas" as const,status:"draft" as const,
    beats:[],nodes:[],
  }));
  const [stageScene,fieldScene,metricScene]=p.scenes.map(scene=>scene.id);
  const config={...defaultProductStageConfig(),
    author_attestation:"I authored this generic original UI fixture."};
  p=await applyChange(p,{type:"upsert_product_stage",
    instance_id:STAGE,scene_id:stageScene,config});
  p=await applyChange(p,{type:"upsert_procedural_field",
    instance_id:FIELD,scene_id:fieldScene,config:defaultProceduralConfig()});
  const sourceIds=[crypto.randomUUID(),crypto.randomUUID()];
  p=await applyChange(p,{type:"add_asset",asset:{
    id:sourceIds[0],name:"Original synthetic measurements",
    media_type:"application/json",content_sha256:hashA,
    source_revision:"unverified fixture declared by owner",
  }});
  p=await applyChange(p,{type:"add_asset",asset:{
    id:sourceIds[1],name:"Revised synthetic measurements",
    media_type:"application/json",content_sha256:hashB,
    source_revision:"unverified fixture declared by owner",
  }});
  const node:CanvasNode={
    ...structuredClone(fixtureProject.scenes[0].nodes[0]),
    id:crypto.randomUUID(),name:"Source linked metric",
    kind:"text",text:"Original metric",
    x:250,y:320,width:650,height:110,opacity:1,
    keyframes:[],relations:[],property_locks:[],
  };
  p=await applyChange(p,{type:"add_canvas_node",scene_id:metricScene,node});
  const table=(version:number,source_asset_id:string,sha:string,milli:number):MeasuredTable=>({
    schema:"motionwright.measured-table/1",
    id:TABLE,label:"Synthetic value",version,
    source_asset_id,source_sha256:sha,
    rows:[{key:"rate",label:"Synthetic rate",milli,unit:"percent"}],
  });
  p=await applyChange(p,{type:"upsert_measured_table",
    table:table(1,sourceIds[0],hashA,12500)});
  p=await applyChange(p,{type:"upsert_measured_binding",
    binding_id:crypto.randomUUID(),scene_id:metricScene,node_id:node.id,
    table_id:TABLE,datum_key:"rate",view:{kind:"label",decimals:1}});
  const captureIds=[crypto.randomUUID(),crypto.randomUUID(),crypto.randomUUID()];
  const captureHashes=["c".repeat(64),"d".repeat(64),"e".repeat(64)];
  for(let index=0;index<3;index++){
    p=await applyChange(p,{type:"add_asset",asset:{
      id:captureIds[index],name:"First-party synthetic capture source "+index,
      media_type:index===0?"image/png":"application/json",
      content_sha256:captureHashes[index],
      source_revision:"synthetic metadata-only fixture; CAS bytes verified separately",
    }});
  }
  const observed=p.revision;
  p=await applyChange(p,{type:"add_claim",
    text:"An original first-party Studio fixture contains a visible screen.",
    source:{kind:"project_revision",revision:observed},
    context:"synthetic-first-party-capture-validation",
    source_revision:"fixture-studio-v1"});
  const claim=p.brief.claims.at(-1)!;
  p=await applyChange(p,{type:"register_capture_source",record:{
    schema:"motionwright.capture-source/1",
    id:crypto.randomUUID(),screenshot_asset_id:captureIds[0],
    screenshot_sha256:captureHashes[0],manifest_asset_id:captureIds[1],
    manifest_sha256:captureHashes[1],fixture_state_asset_id:captureIds[2],
    fixture_state_sha256:captureHashes[2],claim_id:claim.id,
    source_project_id:p.id,source_generation:p.generation,source_revision:observed,
    app_id:"motionwright-studio",app_version:"0.1.0",source_commit:"f".repeat(40),
    scenario_id:"production-original-ui-local-v1",
    origin:"http://127.0.0.1:1420",route:"/",
    viewport_width:1440,viewport_height:940,device_scale_factor:1,
    screenshot_repeat_sha256:captureHashes[0],
    redaction:{selector:".revision-chip",x:100,y:40,width:80,height:24,mask_color:"#0B0B0B"},
    external_requests:0,credential_fields:0,
    declared_scope:"first-party-synthetic-fixture-only",
  }});
  return {p,config,nodeId:node.id,sourceIds,stageScene,fieldScene,metricScene,table};
}
describe("coexisting first-party compositional source contracts",()=>{
  it("updates only a measured scene without destroying stage, procedural objects or camera",async()=>{
    const {p:initial,sourceIds,stageScene,fieldScene,metricScene,nodeId,table,config}=await integrated();
    let p=initial;
    expect(p.production_design?.stages).toHaveLength(1);
    expect(p.production_design?.procedural_fields).toHaveLength(1);
    expect(p.production_design?.measured_tables).toHaveLength(1);
    expect(p.production_design?.captures).toHaveLength(1);
    const capture=structuredClone(p.production_design?.captures?.[0]);
    const claim=structuredClone(p.brief.claims.at(-1));
    const stage=structuredClone(p.scenes.find(scene=>scene.id===stageScene));
    const field=structuredClone(p.scenes.find(scene=>scene.id===fieldScene));
    p=await applyChange(p,{type:"upsert_measured_table",
      table:table(2,sourceIds[1],hashB,37500)});
    expect(p.scenes.find(scene=>scene.id===stageScene)).toEqual(stage);
    expect(p.scenes.find(scene=>scene.id===fieldScene)).toEqual(field);
    expect(p.scenes.find(scene=>scene.id===metricScene)?.nodes.find(n=>n.id===nodeId)?.text)
      .toContain("37.5%");
    expect(p.production_design?.last_measured_impact?.scene_ids).toEqual([metricScene]);
    expect(p.production_design?.captures?.[0]).toEqual(capture);
    expect(p.brief.claims.at(-1)).toEqual(claim);
    const metric=structuredClone(p.scenes.find(scene=>scene.id===metricScene));
    p=await applyChange(p,{type:"upsert_product_stage",
      instance_id:STAGE,scene_id:stageScene,
      config:{...config,screen_heading:"Another original workflow"}});
    expect(p.scenes.find(scene=>scene.id===fieldScene)).toEqual(field);
    expect(p.scenes.find(scene=>scene.id===metricScene)).toEqual(metric);
    expect(p.production_design?.captures?.[0]).toEqual(capture);
    expect(p.brief.claims.at(-1)).toEqual(claim);
    expect(JSON.parse(JSON.stringify(p)).production_design).toMatchObject({
      stages:[{id:STAGE}],procedural_fields:[{id:FIELD}],
      measured_tables:[{id:TABLE}],captures:[{id:capture?.id}],
    });
  });
  it("applies one mandatory brand palette to both independently editable components",async()=>{
    const {p:original,config,stageScene,fieldScene}=await integrated();
    const brand={
      id:crypto.randomUUID(),label:"Original creative palette",version:1,
      rules:[{id:crypto.randomUUID(),kind:"allowed_accents" as const,
        colors:["#86C8D1","#A5C8DF"]}],
    };
    const governed=await applyChange(original,{type:"set_brand_governance",
      profile:brand,exceptions:[]});
    const before=structuredClone(governed);
    await expect(applyChange(governed,{type:"upsert_product_stage",
      instance_id:STAGE,scene_id:stageScene,
      config:{...config,accent:"#F06020"}})).rejects.toThrow(/Brand rule/);
    await expect(applyChange(governed,{type:"upsert_procedural_field",
      instance_id:FIELD,scene_id:fieldScene,
      config:{...defaultProceduralConfig(),fill:"#F06020"}})).rejects.toThrow(/Brand rule/);
    expect(governed).toEqual(before);
  });
});
