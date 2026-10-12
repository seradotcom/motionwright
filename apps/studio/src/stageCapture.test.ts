import {describe,expect,it} from "vitest";
import {applyChange} from "./api";
import {emptyProductionDesign} from "./creativeProduction";
import {fixtureProject} from "./fixture";
import {defaultProductStageConfig} from "./productStage";
import type {Project} from "./types";
import type {CaptureSourceRecord} from "./captureSource";

const PNG="a".repeat(64), MANIFEST="b".repeat(64), STATE="c".repeat(64);
const STAGE="00000000-0000-4000-8000-000000000005";
const IMAGE="00000000-0000-4000-8000-000000000006";
const RECEIPT="00000000-0000-4000-8000-000000000007";
const FIXTURE="00000000-0000-4000-8000-000000000008";

async function withRegisteredSource():Promise<{p:Project,capture:string}>{
  let p:Project=structuredClone(fixtureProject);
  p.schema_version=2;p.locks=[];p.branch_workspaces=[];p.reviews=[];
  p.production_design=emptyProductionDesign();
  p.brief.claims=[];
  p.scenes=p.scenes.slice(0,1).map(s=>({...s,renderer:"motion-canvas" as const,
    status:"draft" as const,beats:[],nodes:[]}));
  p=await applyChange(p,{type:"upsert_product_stage",instance_id:STAGE,
    scene_id:p.scenes[0].id,config:{
      ...defaultProductStageConfig(),
      author_attestation:"I created these generic original graphics and own them.",
    }});
  for(const [id,mime,digest] of [
    [IMAGE,"image/png",PNG],[RECEIPT,"application/json",MANIFEST],
    [FIXTURE,"application/json",STATE],
  ] as const){
    p=await applyChange(p,{type:"add_asset",asset:{
      id,name:"Synthetic test-only source",media_type:mime,content_sha256:digest,
      source_revision:"test source, not an independently verified private application",
    }});
  }
  const observed=p.revision;
  p=await applyChange(p,{type:"add_claim",
    text:"The isolated local first-party Studio graphic exists for technical test purposes.",
    source:{kind:"project_revision",revision:observed},
    context:"synthetic-first-party-capture-validation",
    source_revision:"source-test",
  });
  const capture="00000000-0000-4000-8000-000000000009";
  const record:CaptureSourceRecord={
    schema:"motionwright.capture-source/1",id:capture,
    screenshot_asset_id:IMAGE,screenshot_sha256:PNG,
    manifest_asset_id:RECEIPT,manifest_sha256:MANIFEST,
    fixture_state_asset_id:FIXTURE,fixture_state_sha256:STATE,
    claim_id:p.brief.claims[0].id,source_project_id:p.id,
    source_generation:p.generation,source_revision:observed,
    app_id:"motionwright-studio",app_version:"0.1.0",
    source_commit:"d".repeat(40),scenario_id:"production-original-ui-local-v1",
    origin:"http://127.0.0.1:1420",route:"/",
    viewport_width:1440,viewport_height:940,device_scale_factor:1,
    screenshot_repeat_sha256:PNG,
    redaction:{selector:".revision-chip",x:100,y:80,width:96,height:30,mask_color:"#0B0B0B"},
    external_requests:0,credential_fields:0,declared_scope:"first-party-synthetic-fixture-only",
  };
  p=await applyChange(p,{type:"register_capture_source",record});
  return {p,capture};
}
describe("stage-bound first-party capture source",()=>{
  it("keeps original editable Canvas nodes and registered source provenance through bind and detach",async()=>{
    const {p:initial,capture}=await withRegisteredSource();
    const nodes=structuredClone(initial.scenes[0].nodes);
    const source=structuredClone(initial.production_design?.captures);
    const camera=structuredClone(initial.scenes[0].camera);
    let p=await applyChange(initial,{type:"bind_stage_capture",stage_id:STAGE,capture_id:capture});
    expect(p.production_design?.stage_capture_cards).toEqual([{
      stage_id:STAGE,capture_id:capture,version:1,
    }]);
    expect(p.scenes[0].nodes).toEqual(nodes);
    expect(p.scenes[0].camera).toEqual(camera);
    expect(p.production_design?.captures).toEqual(source);
    await expect(applyChange(p,{type:"detach_product_stage",instance_id:STAGE}))
      .rejects.toThrow(/Detach first-party source tile/);
    p=await applyChange(p,{type:"detach_stage_capture",stage_id:STAGE});
    expect(p.production_design?.stage_capture_cards).toEqual([]);
    expect(p.scenes[0].nodes).toEqual(nodes);
    expect(p.production_design?.captures).toEqual(source);
    await expect(applyChange(p,{type:"detach_stage_capture",stage_id:STAGE}))
      .rejects.toThrow(/does not exist/);
  });
  it("rejects missing stage, missing captured asset or arbitrary source identity",async()=>{
    const {p,capture}=await withRegisteredSource();
    await expect(applyChange(p,{type:"bind_stage_capture",stage_id:STAGE,
      capture_id:crypto.randomUUID()})).rejects.toThrow(/registered/);
    await expect(applyChange(p,{type:"bind_stage_capture",
      stage_id:crypto.randomUUID(),capture_id:capture})).rejects.toThrow(/must exist/);
    expect(p.production_design?.stage_capture_cards).toEqual([]);
    const forged=structuredClone(p);
    forged.production_design!.captures![0].declared_scope="external";
    await expect(applyChange(forged,{type:"bind_stage_capture",
      stage_id:STAGE,capture_id:capture})).rejects.toThrow();
    expect(p.production_design?.stage_capture_cards).toEqual([]);
  });
  it("retains a registered source link on independent author copy replacement",async()=>{
    const {p:original,capture}=await withRegisteredSource();
    let p=await applyChange(original,{type:"bind_stage_capture",stage_id:STAGE,capture_id:capture});
    const binding=structuredClone(p.production_design?.stage_capture_cards);
    const before=p.scenes[0].nodes.find(n=>n.name==="ProductStage / card");
    p=await applyChange(p,{type:"upsert_product_stage",instance_id:STAGE,
      scene_id:p.scenes[0].id,
      config:{...p.production_design!.stages![0].config,screen_heading:"Another original UI workflow"}});
    expect(p.production_design?.stage_capture_cards).toEqual(binding);
    expect(p.scenes[0].nodes.find(n=>n.name==="ProductStage / card")).toEqual(before);
  });
});
