import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { emptyProductionDesign } from "./creativeProduction";
import { fixtureProject } from "./fixture";
import { validateCaptureRecord } from "./captureSource";
import type { CaptureSourceRecord } from "./captureSource";
import type { Project } from "./types";

const SCREEN="a".repeat(64),MANIFEST="b".repeat(64);
const captureId="00000000-0000-4000-8000-0000000000f1";
const screenshotId="00000000-0000-4000-8000-0000000000f2";
const manifestId="00000000-0000-4000-8000-0000000000f3";
const claimId="00000000-0000-4000-8000-0000000000f4";
function source():{project:Project,record:CaptureSourceRecord}{
  const project=structuredClone(fixtureProject);
  project.schema_version=2;
  project.revision=7;
  project.locks=[];
  project.branch_workspaces=[];
  project.production_design=emptyProductionDesign();
  project.assets=[
    {id:screenshotId,name:"Approved image.png",media_type:"image/png",
      content_sha256:SCREEN,source_revision:"local-import"},
    {id:manifestId,name:"Capture manifest.json",media_type:"application/json",
      content_sha256:MANIFEST,source_revision:"local-import"},
  ];
  project.brief.claims=[{
    id:claimId,
    text:"A local first-party Studio screen is visible in this synthetic source",
    context:"synthetic-first-party-capture-validation",
    source:{kind:"project_revision",revision:project.revision},
    source_revision:"fixture-7",
  }];
  const record:CaptureSourceRecord={
    schema:"motionwright.capture-source/1",id:captureId,
    screenshot_asset_id:screenshotId,screenshot_sha256:SCREEN,
    manifest_asset_id:manifestId,manifest_sha256:MANIFEST,
    fixture_state_sha256:"f".repeat(64),
    claim_id:claimId,source_project_id:project.id,
    source_generation:project.generation,source_revision:7,
    app_id:"motionwright-studio",app_version:"0.1.0",source_commit:"c".repeat(40),
    scenario_id:"production-original-ui-local-v1",
    origin:"http://127.0.0.1:1420",route:"/",
    viewport_width:1440,viewport_height:940,device_scale_factor:1,
    screenshot_repeat_sha256:SCREEN,redaction:{
      selector:".revision-chip",x:100,y:64,width:80,height:28,mask_color:"#0B0B0B",
    },
    external_requests:0,credential_fields:0,
    declared_scope:"first-party-synthetic-fixture-only",
  };
  return {project,record};
}

describe("first-party capture source registry",()=>{
  it("stores a content-bound capture receipt without modifying its imported source assets or claim",async()=>{
    const {project,record}=source();
    const beforeAssets=structuredClone(project.assets);
    const beforeClaim=structuredClone(project.brief.claims);
    const admitted=await applyChange(project,{type:"register_capture_source",record});
    expect(admitted.production_design?.captures).toEqual([record]);
    expect(admitted.assets).toEqual(beforeAssets);
    expect(admitted.brief.claims).toEqual(beforeClaim);
    expect(admitted.revision).toBe(project.revision+1);
    expect(project.production_design?.captures).toEqual([]);
    await expect(applyChange(admitted,{type:"register_capture_source",record}))
      .rejects.toThrow(/already registered/);
    await expect(applyChange(admitted,{type:"remove_asset",asset_id:screenshotId}))
      .rejects.toThrow(/capture provenance/);
    await expect(applyChange(admitted,{type:"remove_asset",asset_id:manifestId}))
      .rejects.toThrow(/capture provenance/);
  });
  it("rejects cross-project source ID, mismatched source generation and future project revision",async()=>{
    const {project,record}=source();
    for(const bad of [
      {...record,source_project_id:crypto.randomUUID()},
      {...record,source_generation:crypto.randomUUID()},
      {...record,source_generation:"00000000-0000-0000-0000-000000000000"},
      {...record,source_revision:project.revision+1},
    ]) {
      await expect(applyChange(project,{type:"register_capture_source",record:bad}))
        .rejects.toThrow(/source|generation|Capture/);
    }
    expect(project.production_design?.captures).toEqual([]);
  });
  it("rejects unauthorized sources, foreign origin, hidden input, redaction removal and forged proof",async()=>{
    const {project,record}=source();
    const variants=[
      {...record,screenshot_sha256:"c".repeat(64),screenshot_repeat_sha256:"c".repeat(64)},
      {...record,manifest_asset_id:crypto.randomUUID()},
      {...record,claim_id:crypto.randomUUID()},
      {...record,origin:"https://example.org"},
      {...record,external_requests:1},
      {...record,credential_fields:1},
      {...record,redaction:{...record.redaction,selector:""}},
      {...record,redaction:{...record.redaction,width:1380,height:900}},
      {...record,screenshot_repeat_sha256:"d".repeat(64)},
    ];
    for(const forged of variants){
      await expect(applyChange(project,{type:"register_capture_source",record:forged}))
        .rejects.toThrow();
    }
  });
  it("rejects arbitrary source metadata fields rather than claiming independent verification",()=>{
    const {project,record}=source();
    expect(()=>validateCaptureRecord(project,{
      ...record,verified_product_behavior:true,
    } as unknown as CaptureSourceRecord)).toThrow(/Unrecognized/);
    expect(()=>validateCaptureRecord(project,{
      ...record,redaction:{...record.redaction,hidden_passwords_cleared:true},
    } as unknown as CaptureSourceRecord)).toThrow(/Unrecognized/);
  });
});
