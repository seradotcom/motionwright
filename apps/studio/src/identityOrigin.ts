/** Identity consent boundary. A declaration is never a provider execution grant.
 * This mirrors the Rust project CAS and does not verify human identity or
 * signatures. Real-person synthesis remains in "consent review required".
 */
import type { Project } from "./types";
import { emptyProductionDesign } from "./creativeProduction";

export type IdentityOrigin = "original_fictional"|"human_likeness";
export type IdentityModality = "voice"|"face"|"presenter";
export type IdentityPurpose = "product_demonstration"|"marketing"|"education"|"internal_review";
export interface IdentityOriginRecord {
  schema:string;id:string;source_project_id:string;source_generation:string;
  origin:IdentityOrigin;display_label:string;origin_description:string;
  created_by_declaration:string;created_at_utc:string;expires_at_utc:string;
  allowed_modalities:IdentityModality[];allowed_purposes:IdentityPurpose[];
  provenance_asset_id:string;provenance_sha256:string;
  consent_asset_id:string|null;consent_sha256:string|null;
  consent_subject_declaration:string|null;consent_scope_declaration:string|null;
  synthetic_disclosure:string;
}
export interface IdentityRevocation {
  id:string;identity_id:string;revoked_at_utc:string;reason:string;
}
export type IdentityUseReview =
  |"revoked"|"outside_valid_period"|"outside_declared_scope"
  |"human_consent_needs_independent_review"|"fictional_source_needs_owner_approval";

export const IDENTITY_SOURCE_SCHEMA="motionwright.identity-origin/1";
export const DISCLOSURE_FICTIONAL="SYNTHETIC ORIGINAL CHARACTER / NOT A REAL PERSON";
export const DISCLOSURE_HUMAN="SYNTHETIC HUMAN LIKENESS / CONSENT REQUIRES INDEPENDENT REVIEW";
const origins:IdentityOrigin[]=["original_fictional","human_likeness"];
const modalities:IdentityModality[]=["voice","face","presenter"];
const purposes:IdentityPurpose[]=["product_demonstration","marketing","education","internal_review"];
const hex64=/^[a-f0-9]{64}$/;
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const nil="00000000-0000-0000-0000-000000000000";
const recordFields=[
  "schema","id","source_project_id","source_generation","origin","display_label",
  "origin_description","created_by_declaration","created_at_utc","expires_at_utc",
  "allowed_modalities","allowed_purposes","provenance_asset_id","provenance_sha256",
  "consent_asset_id","consent_sha256","consent_subject_declaration",
  "consent_scope_declaration","synthetic_disclosure",
];
const revokeFields=["id","identity_id","revoked_at_utc","reason"];
function invalid(message:string):never{throw new Error(message);}
function strict(value:object,expected:string[]):void {
  if(Object.keys(value).sort().join("|")!==[...expected].sort().join("|"))
    invalid("Identity source has unknown or missing schema fields.");
}
function bounded(value:string|null,limit:number):boolean {
  return typeof value==="string" && !!value.trim() &&
    Array.from(value).length<=limit && !/\p{Cc}/u.test(value);
}
function time(value:string):number {
  if(typeof value!=="string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/u.test(value))
    invalid("Identity event time must be canonical UTC.");
  const ms=Date.parse(value);
  if(!Number.isFinite(ms) || new Date(ms).toISOString().replace(".000Z","Z")!==value)
    invalid("Identity event time must be exact canonical UTC.");
  return ms;
}
function unique<T>(values:T[], options:readonly T[], max:number):boolean {
  return Array.isArray(values) && values.length>0 && values.length<=max &&
    values.every(value=>options.includes(value)) &&
    new Set(values).size===values.length;
}
export function validateIdentityOrigin(project:Project, record:IdentityOriginRecord):void {
  if(!record || typeof record!=="object" || Array.isArray(record))invalid("Invalid identity source.");
  strict(record,recordFields);
  if(record.schema!==IDENTITY_SOURCE_SCHEMA || !uuid.test(record.id) ||
    record.source_project_id!==project.id || !uuid.test(record.source_generation) ||
    record.source_generation===nil || !origins.includes(record.origin) ||
    !bounded(record.display_label,96) || !bounded(record.origin_description,300) ||
    !bounded(record.created_by_declaration,160))
    invalid("Identity declaration requires a bounded source and original owner statement.");
  const created=time(record.created_at_utc),expires=time(record.expires_at_utc);
  if(expires<=created || expires-created>366*86400*1000)
    invalid("Identity source expiry must fall within one year of its original statement.");
  if(!unique(record.allowed_modalities,modalities,3) ||
    !unique(record.allowed_purposes,purposes,4))
    invalid("Identity usage requires non-duplicated explicit modality and purpose.");
  if(!uuid.test(record.provenance_asset_id) || !hex64.test(record.provenance_sha256) ||
    !project.assets.some(asset=>asset.id===record.provenance_asset_id &&
      asset.media_type==="application/json" &&
      asset.content_sha256===record.provenance_sha256))
    invalid("Identity requires exact imported immutable JSON provenance.");
  if(record.origin==="original_fictional"){
    if(record.consent_asset_id!==null || record.consent_sha256!==null ||
      record.consent_subject_declaration!==null ||
      record.consent_scope_declaration!==null ||
      record.synthetic_disclosure!==DISCLOSURE_FICTIONAL)
      invalid("Original fictional identities must not impersonate human consent evidence.");
  }else{
    if(!record.consent_asset_id || !uuid.test(record.consent_asset_id) ||
      record.consent_asset_id===record.provenance_asset_id ||
      !record.consent_sha256 || !hex64.test(record.consent_sha256) ||
      !bounded(record.consent_subject_declaration,160) ||
      !bounded(record.consent_scope_declaration,300) ||
      record.synthetic_disclosure!==DISCLOSURE_HUMAN ||
      !project.assets.some(asset=>asset.id===record.consent_asset_id &&
        asset.media_type==="application/json" &&
        asset.content_sha256===record.consent_sha256))
      invalid("Human likeness requires separate, source-identified consent evidence and a named scope.");
  }
}
export function validateIdentityRegistry(project:Project):void {
  const registry=project.production_design?.identity_origins??[];
  const revoked=project.production_design?.identity_revocations??[];
  if(registry.length>32 || revoked.length>32)
    invalid("Identity source collection exceeds 32 entries.");
  const ids=new Set<string>(),manifests=new Set<string>(),consentDigests=new Set<string>();
  for(const source of registry){
    if(ids.has(source.id)||manifests.has(source.provenance_asset_id) ||
      (!!source.consent_sha256&&consentDigests.has(source.consent_sha256)))
      invalid("Duplicate identity source or reused consent evidence.");
    ids.add(source.id);manifests.add(source.provenance_asset_id);
    if(source.consent_sha256)consentDigests.add(source.consent_sha256);
    validateIdentityOrigin(project,source);
  }
  const events=new Set<string>(),subjects=new Set<string>();
  for(const item of revoked){
    if(!item || typeof item!=="object" || Array.isArray(item))
      invalid("Invalid identity revocation.");
    strict(item,revokeFields);
    const origin=registry.find(origin=>origin.id===item.identity_id);
    if(!uuid.test(item.id) || !origin || events.has(item.id) ||
      subjects.has(item.identity_id) || !bounded(item.reason,240) ||
      time(item.revoked_at_utc)<time(origin.created_at_utc))
      invalid("Invalid immutable identity revocation history.");
    events.add(item.id);subjects.add(item.identity_id);
  }
}
export function registerIdentityOrigin(project:Project, record:IdentityOriginRecord):void {
  project.production_design??=emptyProductionDesign();
  const registry=project.production_design.identity_origins??[];
  if(registry.length>=32 || registry.some(item=>item.id===record.id ||
      item.provenance_asset_id===record.provenance_asset_id ||
      (!!record.consent_sha256 && record.consent_sha256===item.consent_sha256)))
    invalid("Identity registration duplicate or immutable source reuse is prohibited.");
  validateIdentityOrigin(project,record);
  if(record.source_generation!==project.generation)
    invalid("Identity registration requires the current project generation.");
  project.production_design.identity_origins=[...registry,structuredClone(record)];
}
export function revokeIdentityOrigin(project:Project, item:IdentityRevocation):void {
  project.production_design??=emptyProductionDesign();
  const registry=project.production_design.identity_origins??[];
  const revoked=project.production_design.identity_revocations??[];
  const origin=registry.find(value=>value.id===item.identity_id);
  if(!origin || !uuid.test(item.id) || revoked.length>=32 ||
    revoked.some(old=>old.id===item.id || old.identity_id===item.identity_id) ||
    !bounded(item.reason,240) || time(item.revoked_at_utc)<time(origin.created_at_utc))
    invalid("Identity source revocation is invalid or already recorded.");
  project.production_design.identity_revocations=[...revoked,structuredClone(item)];
}
export function identityUseReview(
  project:Project,id:string,atUtc:string,modality:IdentityModality,purpose:IdentityPurpose
):IdentityUseReview {
  const now=time(atUtc),source=project.production_design?.identity_origins?.find(item=>item.id===id);
  if(!source)invalid("Identity source was not registered.");
  if(project.production_design?.identity_revocations?.some(item=>item.identity_id===id))
    return "revoked";
  if(now<time(source.created_at_utc)||now>=time(source.expires_at_utc))
    return "outside_valid_period";
  if(!source.allowed_modalities.includes(modality)||!source.allowed_purposes.includes(purpose))
    return "outside_declared_scope";
  return source.origin==="human_likeness"?
    "human_consent_needs_independent_review":"fictional_source_needs_owner_approval";
}
