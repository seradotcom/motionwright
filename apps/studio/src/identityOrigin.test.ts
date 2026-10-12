import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { emptyProductionDesign } from "./creativeProduction";
import { fixtureProject } from "./fixture";
import {
  DISCLOSURE_FICTIONAL, DISCLOSURE_HUMAN, IDENTITY_SOURCE_SCHEMA,
  identityUseReview,validateIdentityOrigin,validateIdentityRegistry,
} from "./identityOrigin";
import type { IdentityOriginRecord } from "./identityOrigin";
import type { Project } from "./types";

const SHA="a".repeat(64), CONSENT="b".repeat(64);
const ARTIFACT="00000000-0000-4000-8000-0000000000a1";
const PERMISSION="00000000-0000-4000-8000-0000000000a2";
function fixture():{project:Project;record:IdentityOriginRecord}{
  const project=structuredClone(fixtureProject);
  project.schema_version=2;
  project.locks=[];project.branch_workspaces=[];
  project.production_design=emptyProductionDesign();
  project.assets=[{
    id:ARTIFACT,name:"Original fictional source declaration",
    media_type:"application/json",content_sha256:SHA,
    source_revision:"source-file-declared-not-verified",
  },{
    id:PERMISSION,name:"Human consent document",
    media_type:"application/json",content_sha256:CONSENT,
    source_revision:"signed-byte-provenance-unverified",
  }];
  const record:IdentityOriginRecord={
    schema:IDENTITY_SOURCE_SCHEMA,id:crypto.randomUUID(),
    source_project_id:project.id,source_generation:project.generation,
    origin:"original_fictional",display_label:"Original narrator",
    origin_description:"Designed as a new original fictional concept, no modeled person",
    created_by_declaration:"I authored this original fictional source and control the rights",
    created_at_utc:"2026-10-11T00:00:00Z",
    expires_at_utc:"2026-11-11T00:00:00Z",
    allowed_modalities:["voice","presenter"],
    allowed_purposes:["product_demonstration","education"],
    provenance_asset_id:ARTIFACT,provenance_sha256:SHA,
    consent_asset_id:null,consent_sha256:null,consent_subject_declaration:null,
    consent_scope_declaration:null,synthetic_disclosure:DISCLOSURE_FICTIONAL,
  };
  return {project,record};
}
describe("identity permission and revocation receipts",()=>{
  it("records original fictional source without granting synthesis rights",async()=>{
    const {project,record}=fixture();
    const registered=await applyChange(project,{type:"register_identity_origin",record});
    expect(registered.production_design?.identity_origins).toEqual([record]);
    expect(identityUseReview(registered,record.id,"2026-10-20T00:00:00Z","voice","education"))
      .toBe("fictional_source_needs_owner_approval");
    expect(identityUseReview(registered,record.id,"2026-10-20T00:00:00Z","face","education"))
      .toBe("outside_declared_scope");
    expect(identityUseReview(registered,record.id,"2026-12-01T00:00:00Z","voice","education"))
      .toBe("outside_valid_period");
    expect(()=>validateIdentityRegistry(registered)).not.toThrow();
    expect(JSON.parse(JSON.stringify(registered)).production_design.identity_origins).toEqual([record]);
    await expect(applyChange(registered,{type:"remove_asset",asset_id:ARTIFACT}))
      .rejects.toThrow(/Identity consent\/provenance/);
    expect(project.production_design?.identity_origins).toEqual([]);
  });
  it("requires separate human consent asset and never substitutes the declaration for independent review",async()=>{
    const {project,record}=fixture();
    const human:IdentityOriginRecord={
      ...record,origin:"human_likeness",
      consent_asset_id:PERMISSION,consent_sha256:CONSENT,
      consent_subject_declaration:"The named person in the original consent",
      consent_scope_declaration:"Only an original educational presenter study",
      allowed_modalities:["presenter"],allowed_purposes:["education"],
      synthetic_disclosure:DISCLOSURE_HUMAN,
    };
    await expect(applyChange(project,{type:"register_identity_origin",record:{
      ...human,consent_asset_id:null,
    }})).rejects.toThrow(/consent/);
    const registered=await applyChange(project,{type:"register_identity_origin",record:human});
    expect(identityUseReview(registered,human.id,"2026-10-20T00:00:00Z","presenter","education"))
      .toBe("human_consent_needs_independent_review");
    await expect(applyChange(registered,{type:"remove_asset",asset_id:PERMISSION}))
      .rejects.toThrow(/Identity consent\/provenance/);
  });
  it("blocks every use after append-only revocation and never erases evidence",async()=>{
    const {project,record}=fixture();
    const registered=await applyChange(project,{type:"register_identity_origin",record});
    const revocation={
      id:crypto.randomUUID(),identity_id:record.id,
      revoked_at_utc:"2026-10-20T00:00:00Z",
      reason:"The author withdrew the original creative license",
    };
    const revoked=await applyChange(registered,{type:"revoke_identity_origin",revocation});
    expect(revoked.production_design?.identity_revocations).toEqual([revocation]);
    expect(revoked.production_design?.identity_origins).toEqual([record]);
    expect(identityUseReview(revoked,record.id,"2026-10-21T00:00:00Z","voice","education"))
      .toBe("revoked");
    await expect(applyChange(revoked,{type:"revoke_identity_origin",revocation:{
      ...revocation,id:crypto.randomUUID(),
    }})).rejects.toThrow(/revocation|already/);
  });
  it("fails closed on forged origins, claims of verification, invalid dates and duplicate permissions",async()=>{
    const {project,record}=fixture();
    const attacks=[
      {...record,source_project_id:crypto.randomUUID()},
      {...record,source_generation:crypto.randomUUID()},
      {...record,provenance_sha256:"c".repeat(64)},
      {...record,synthetic_disclosure:"VERIFIED HUMAN CONSENT"},
      {...record,allowed_modalities:["voice","voice"] as ["voice","voice"]},
      {...record,allowed_purposes:[]},
      {...record,created_at_utc:"2026-10-11T01:00:00+01:00"},
      {...record,expires_at_utc:"2029-12-01T00:00:00Z"},
    ];
    for(const attempt of attacks)
      await expect(applyChange(project,{type:"register_identity_origin",record:attempt as IdentityOriginRecord}))
        .rejects.toThrow();
    expect(()=>validateIdentityOrigin(project,{
      ...record,bypass_license_verification:true,
    } as unknown as IdentityOriginRecord)).toThrow(/unknown or missing/);
    expect(project.production_design?.identity_origins).toHaveLength(0);
  });
  it("retains immutable historical origin across a project import generation rotation",async()=>{
    const {project,record}=fixture();
    const registered=await applyChange(project,{type:"register_identity_origin",record});
    const imported=structuredClone(registered);
    imported.generation=crypto.randomUUID();
    expect(()=>validateIdentityRegistry(imported)).not.toThrow();
    await expect(applyChange(imported,{type:"register_identity_origin",record:{
      ...record,id:crypto.randomUUID(),provenance_asset_id:PERMISSION,provenance_sha256:CONSENT,
    }})).rejects.toThrow(/current project generation/);
  });
});
