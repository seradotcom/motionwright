/** Append-only, declared first-party capture binding in the project CAS.
 * This mirrors domain CaptureSourceRecord. A metadata binding to imported
 * PNG/JSON assets does NOT certify privacy, authenticity or permission.
 */
import type { Project } from "./types";

export interface CaptureMask {
  selector:string; x:number; y:number; width:number; height:number; mask_color:string;
}
export interface CaptureSourceRecord {
  schema:string; id:string; screenshot_asset_id:string; screenshot_sha256:string;
  manifest_asset_id:string; manifest_sha256:string; fixture_state_sha256:string; claim_id:string;
  source_project_id:string; source_generation:string; source_revision:number;
  app_id:string; app_version:string; source_commit:string;
  scenario_id:string; origin:string; route:string;
  viewport_width:number; viewport_height:number; device_scale_factor:number;
  screenshot_repeat_sha256:string; redaction:CaptureMask;
  external_requests:number; credential_fields:number; declared_scope:string;
}
const HEX64=/^[0-9a-f]{64}$/;
const HEX40=/^[0-9a-f]{40}$/;
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FIELDS=[
  "schema","id","screenshot_asset_id","screenshot_sha256","manifest_asset_id",
  "manifest_sha256","fixture_state_sha256","claim_id","source_project_id","source_generation",
  "source_revision","app_id","app_version","source_commit","scenario_id",
  "origin","route","viewport_width","viewport_height","device_scale_factor",
  "screenshot_repeat_sha256","redaction","external_requests","credential_fields",
  "declared_scope",
];
const MASK_FIELDS=["selector","x","y","width","height","mask_color"];
function strict(value:object,keys:string[]):void{
  if(Object.keys(value).sort().join("|")!==[...keys].sort().join("|"))
    throw new Error("Unrecognized or missing first-party capture metadata fields.");
}
export function validateCaptureRecord(project:Project,record:CaptureSourceRecord):void{
  if(!record || typeof record!=="object" || Array.isArray(record))
    throw new Error("Capture record is not an object.");
  strict(record,FIELDS);
  const viewport=record.viewport_width===1440 && record.viewport_height===940;
  const ids=[record.id,record.screenshot_asset_id,record.manifest_asset_id,
    record.claim_id,record.source_project_id,record.source_generation];
  if(record.schema!=="motionwright.capture-source/1" ||
    ids.some(value=>typeof value!=="string" || !UUID.test(value)) ||
    record.source_project_id!==project.id ||
    record.source_generation==="00000000-0000-0000-0000-000000000000" ||
    !Number.isSafeInteger(record.source_revision) ||
    record.source_revision<1 || record.source_revision>project.revision ||
    record.app_id!=="motionwright-studio" ||
    !/^[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}$/.test(record.app_version) ||
    !HEX40.test(record.source_commit) ||
    record.scenario_id!=="production-original-ui-local-v1" ||
    record.origin!=="http://127.0.0.1:1420" || record.route!=="/" ||
    !viewport || record.device_scale_factor!==1 ||
    record.declared_scope!=="first-party-synthetic-fixture-only" ||
    record.external_requests!==0 || record.credential_fields!==0 ||
    !HEX64.test(record.screenshot_sha256) || !HEX64.test(record.manifest_sha256) ||
    !HEX64.test(record.fixture_state_sha256) ||
    record.screenshot_repeat_sha256!==record.screenshot_sha256){
    throw new Error("Capture record is not tied to an admitted first-party project source.");
  }
  const mask=record.redaction;
  if(!mask || typeof mask!=="object" || Array.isArray(mask))
    throw new Error("Capture redaction missing.");
  strict(mask,MASK_FIELDS);
  for(const value of [mask.x,mask.y,mask.width,mask.height])
    if(!Number.isSafeInteger(value))throw new Error("Capture redaction requires integer pixels.");
  const area=mask.width*mask.height;
  if(mask.selector!==".revision-chip" || mask.mask_color!=="#0B0B0B" ||
    mask.x<0 || mask.y<0 || mask.width<12 || mask.height<12 ||
    mask.x+mask.width>1440 || mask.y+mask.height>940 ||
    area>=1440*940/10)
    throw new Error("Capture redaction is not a bounded reviewed region.");
  if(record.screenshot_asset_id===record.manifest_asset_id ||
    !project.assets.some(asset=>asset.id===record.screenshot_asset_id &&
      asset.media_type==="image/png" && asset.content_sha256===record.screenshot_sha256) ||
    !project.assets.some(asset=>asset.id===record.manifest_asset_id &&
      asset.media_type==="application/json" && asset.content_sha256===record.manifest_sha256))
    throw new Error("Capture requires matching immutable PNG and JSON source assets.");
  if(!project.brief.claims.some(claim=>claim.id===record.claim_id &&
      claim.context==="synthetic-first-party-capture-validation" &&
      claim.source?.kind==="project_revision" &&
      claim.source.revision===record.source_revision))
    throw new Error("Capture claim is not linked to the authored project revision.");
}
export function validateCaptureRecords(project:Project):void{
  const captures=project.production_design?.captures??[];
  if(captures.length>32)throw new Error("Too many capture source records.");
  const seen=new Set<string>();
  for(const record of captures){
    if(seen.has(record.id))throw new Error("Capture record identity already recorded.");
    seen.add(record.id);
    validateCaptureRecord(project,record);
  }
}
export function registerCaptureRecord(project:Project,record:CaptureSourceRecord):void{
  project.production_design ??= {plan:null,heroes:[],capsules:[]};
  const captures=project.production_design.captures??[];
  if(captures.length>=32 || captures.some(entry=>entry.id===record.id))
    throw new Error("Capture is already registered or collection capacity exceeded.");
  validateCaptureRecord(project,record);
  if(record.source_generation!==project.generation)
    throw new Error("New capture must match the current project generation.");
  project.production_design.captures=[...captures,structuredClone(record)];
}
