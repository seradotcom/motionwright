import { invoke } from "@tauri-apps/api/core";
import { sameValue } from "./creativeProduction";
import type { Project } from "./types";
export interface RuntimeScope { project_id:string; generation:string; revision:number }
type Profile = "motion_canvas" | "hyperframes";
type RuntimePlatform = "linux-x86_64" | "macos-aarch64";
export interface RuntimePlan {
  schema:"motionwright.runtime-profile-plan/1"; scope:RuntimeScope; plan_id:string;
  profile:Profile; label:string; version:string; platform:RuntimePlatform;
  manifest_sha256:string; total_bytes:number; required_free_bytes:number; available_bytes:number; file_count:number;
  destination:string; node_path:string; entry_path:string; lock_path:string; lock_sha256:string;
  declared_node_version:"22.22.0"; effect_subject:string; expires_in_seconds:900; execution_performed:false;
}
export interface RuntimeInstallation {
  schema:"motionwright.runtime-profile-installation/1"; scope:RuntimeScope; install_id:string;
  profile:Profile; label:string; version:string; platform:RuntimePlatform; manifest_sha256:string;
  total_bytes:number; file_count:number; destination:string; disabled_destination:string;
  node_path:string; entry_path:string; lock_path:string; lock_sha256:string;
  state:"installed"|"copy_completed"|"outcome_unknown"|"partial_copy"|"files_unverified"|"missing_files"|"rolled_back";
  content_verified:boolean; rollback_available:boolean; rollback_subject:string; execution_performed:false; automatic_retry:false;
}
export interface RuntimeInventory {
  schema:"motionwright.runtime-profile-inventory/1"; scope:RuntimeScope; supported:boolean;
  platform:RuntimePlatform|"unsupported"; profiles:RuntimeInstallation[]; alternatives:string;
}
export interface RuntimeRetention {
  install_id:string; scope:RuntimeScope; manifest_sha256:string; profile:Profile;
  kind:"install"|"rollback"; state:"prepared"|"dispatched"|"not_sent"|"acknowledged";
}
export const RUNTIME_RETENTION_EVENT="motionwright:runtime-profile-retention";
export const RUNTIME_RETENTION_PREFIX="motionwright:runtime-profile:v1:";
const uuid=(value:unknown):value is string=>typeof value==="string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value)
  && value!=="00000000-0000-0000-0000-000000000000";
const sha=(value:unknown):value is string=>typeof value==="string" && /^[0-9a-f]{64}$/.test(value);
const integer=(value:unknown,min:number,max:number)=>Number.isSafeInteger(value)&&Number(value)>=min&&Number(value)<=max;
function record(value:unknown,fields:string[]):Record<string,unknown>{
  if (!value || typeof value!=="object" || Array.isArray(value)) throw new Error("The runtime operation response is malformed.");
  const result=value as Record<string,unknown>;
  if (Object.keys(result).length!==fields.length || fields.some(field=>!Object.hasOwn(result,field))) throw new Error("The runtime operation has an unsupported shape.");
  return result;
}
function scopeOf(project:Pick<Project,"id"|"generation"|"revision">):RuntimeScope{
  if (!uuid(project.id)||!uuid(project.generation)||!integer(project.revision,0,Number.MAX_SAFE_INTEGER)) throw new Error("Select a saved project before runtime setup.");
  return {project_id:project.id,generation:project.generation,revision:project.revision};
}
function parseScope(value:unknown):RuntimeScope{
  const scope=record(value,["project_id","generation","revision"]);
  if (!uuid(scope.project_id)||!uuid(scope.generation)||!integer(scope.revision,0,Number.MAX_SAFE_INTEGER)) throw new Error("Runtime operation scope is invalid.");
  return scope as unknown as RuntimeScope;
}
function desktop(){if(typeof window==="undefined"||!("__TAURI_INTERNALS__" in window))throw new Error("Offline runtime installation requires Motionwright Desktop.");}
function path(value:unknown):value is string{return typeof value==="string" && value.startsWith("/") && value.length<=4096 && !/[\u0000-\u001f\u007f]/.test(value) && !value.split("/").some(part=>part===".."||part===".");}
const platform=(value:unknown):value is RuntimePlatform=>value==="linux-x86_64"||value==="macos-aarch64";
function common(value:Record<string,unknown>,project:Project){
  if (!sameValue(parseScope(value.scope),scopeOf(project)) || !["motion_canvas","hyperframes"].includes(String(value.profile))
    || value.label!==(value.profile==="motion_canvas"?"Motion Canvas":"HyperFrames")
    || value.version!==(value.profile==="motion_canvas"?"3.17.2":"0.8.181") || !platform(value.platform)
    || (value.platform==="macos-aarch64"&&value.profile!=="motion_canvas")
    || !sha(value.manifest_sha256)||!sha(value.lock_sha256)||!integer(value.total_bytes,1,2*1024**3+8*1024**2)
    || !integer(value.file_count,1,30001)||![value.destination,value.node_path,value.entry_path,value.lock_path].every(path)
    || value.execution_performed!==false) throw new Error("The runtime package reply does not match this project or compatible profile.");
}
export function parseRuntimePlan(value:unknown,project:Project):RuntimePlan{
  const result=record(value,["schema","scope","plan_id","profile","label","version","platform","manifest_sha256","total_bytes","required_free_bytes","available_bytes","file_count",
    "destination","node_path","entry_path","lock_path","lock_sha256","declared_node_version","effect_subject","expires_in_seconds","execution_performed"]);
  common(result,project);
  if (result.schema!=="motionwright.runtime-profile-plan/1"||!uuid(result.plan_id)
    || !integer(result.required_free_bytes,Number(result.total_bytes),Number.MAX_SAFE_INTEGER)
    || !integer(result.available_bytes,Number(result.required_free_bytes),Number.MAX_SAFE_INTEGER)
    || result.declared_node_version!=="22.22.0"||result.expires_in_seconds!==900
    || typeof result.effect_subject!=="string"||!new RegExp("^runtime-profile-install\\|"+result.plan_id+"\\|[0-9a-f]{64}$").test(result.effect_subject)
    || !(result.destination as string).endsWith("/"+result.plan_id)) throw new Error("Runtime inspection is invalid, stale or exceeds its limits.");
  return result as unknown as RuntimePlan;
}
export function parseRuntimeInstallation(value:unknown,project:Project,expected?:{install_id:string;manifest_sha256:string}):RuntimeInstallation{
  const result=record(value,["schema","scope","install_id","profile","label","version","platform","manifest_sha256","total_bytes","file_count","destination","disabled_destination",
    "node_path","entry_path","lock_path","lock_sha256","state","content_verified","rollback_available","rollback_subject","execution_performed","automatic_retry"]);
  common(result,project);
  if (result.schema!=="motionwright.runtime-profile-installation/1"||!uuid(result.install_id)||!path(result.disabled_destination)
    || !["installed","copy_completed","outcome_unknown","partial_copy","files_unverified","missing_files","rolled_back"].includes(String(result.state))
    || typeof result.content_verified!=="boolean"||typeof result.rollback_available!=="boolean"||result.automatic_retry!==false
    || (result.state==="installed")!==result.content_verified
    || (result.state==="rolled_back"&&result.rollback_available!==false)
    || typeof result.rollback_subject!=="string"||!new RegExp("^runtime-profile-rollback\\|"+result.install_id+"\\|[0-9a-f]{64}$").test(result.rollback_subject)
    || !(result.destination as string).endsWith("/"+result.install_id)
    || !(result.disabled_destination as string).endsWith("/disabled-"+result.install_id)
    || (expected&&(expected.install_id!==result.install_id||expected.manifest_sha256!==result.manifest_sha256))) throw new Error("The retained runtime outcome belongs to another operation or has an invalid state.");
  return result as unknown as RuntimeInstallation;
}
function retentionKey(project:Pick<Project,"id"|"generation">){return RUNTIME_RETENTION_PREFIX+project.id+":"+project.generation;}
export function readRuntimeRetention(project:Project):RuntimeRetention[]{
  scopeOf(project);
  const raw=localStorage.getItem(retentionKey(project));
  if(raw===null)return [];
  if(raw.length>128*1024)throw new Error("Retained runtime operations exceed their local limit. Preserve the original copy.");
  const envelope=record(JSON.parse(raw),["schema","operations"]);
  if(envelope.schema!=="motionwright.runtime-profile-retention/1"||!Array.isArray(envelope.operations)||envelope.operations.length>128)throw new Error("Retained runtime operations cannot be read safely.");
  const rows=envelope.operations.map(item=>{
    const row=record(item,["install_id","scope","manifest_sha256","profile","kind","state"]);
    const scope=parseScope(row.scope);
    if(!uuid(row.install_id)||!sha(row.manifest_sha256)||scope.project_id!==project.id||scope.generation!==project.generation
      || !["motion_canvas","hyperframes"].includes(String(row.profile))||!["install","rollback"].includes(String(row.kind))
      || !["prepared","dispatched","not_sent","acknowledged"].includes(String(row.state)))throw new Error("A retained runtime identity is invalid.");
    return row as unknown as RuntimeRetention;
  });
  if(new Set(rows.map(row=>row.kind+":"+row.install_id)).size!==rows.length)throw new Error("Retained runtime operations contain duplicate identities.");
  return rows;
}
function save(project:Project,rows:RuntimeRetention[]){
  const raw=JSON.stringify({schema:"motionwright.runtime-profile-retention/1",operations:rows});
  localStorage.setItem(retentionKey(project),raw);
  if(localStorage.getItem(retentionKey(project))!==raw)throw new Error("Runtime intent could not be retained. No local effect was sent.");
  if(typeof window.dispatchEvent === "function")window.dispatchEvent(new Event(RUNTIME_RETENTION_EVENT));
}
export const runtimeUnconfirmed=(row:RuntimeRetention)=>row.state==="prepared"||row.state==="dispatched";
function prepare(project:Project,operation:RuntimeRetention){
  const rows=readRuntimeRetention(project);
  if(rows.some(runtimeUnconfirmed))throw new Error("Read the original unconfirmed runtime operation before another local effect.");
  const existing=rows.findIndex(row=>row.kind===operation.kind&&row.install_id===operation.install_id);
  if(existing>=0){if(rows[existing].state!=="not_sent")throw new Error("This runtime operation was already sent.");rows[existing]=operation;}
  else {if(rows.length>=128)throw new Error("The retained runtime operation budget is full.");rows.push(operation);}
  save(project,rows);
}
function transition(project:Project,operation:RuntimeRetention,state:RuntimeRetention["state"]){
  const rows=readRuntimeRetention(project);
  const index=rows.findIndex(row=>row.kind===operation.kind&&row.install_id===operation.install_id);
  if(index<0||!sameValue({...rows[index],state:operation.state},operation))throw new Error("The retained runtime operation changed; preserve its original state.");
  if((state==="dispatched"||state==="not_sent")&&rows[index].state!=="prepared")throw new Error("The runtime operation already advanced.");
  if(rows[index].state==="acknowledged"&&state!=="acknowledged")throw new Error("A confirmed runtime operation cannot be replayed.");
  rows[index]={...operation,state};save(project,rows);
}
export async function runtimeProfileInventory(project:Project):Promise<RuntimeInventory>{
  desktop();const original=structuredClone(project);
  const reply=record(await invoke<unknown>("runtime_profile_inventory",{request:scopeOf(original)}),["schema","scope","supported","platform","profiles","alternatives"]);
  if(reply.schema!=="motionwright.runtime-profile-inventory/1"||!sameValue(parseScope(reply.scope),scopeOf(original))
    || typeof reply.supported!=="boolean"||(reply.supported?!platform(reply.platform):reply.platform!=="unsupported")
    || !Array.isArray(reply.profiles)||reply.profiles.length>64||(!reply.supported&&reply.profiles.length>0)
    || typeof reply.alternatives!=="string"||reply.alternatives.length>2048)throw new Error("Runtime inventory is stale or malformed.");
  const profiles=reply.profiles.map(row=>parseRuntimeInstallation(row,original));
  if(new Set(profiles.map(row=>row.install_id)).size!==profiles.length)throw new Error("Runtime inventory contains duplicate originals.");
  if(profiles.some(row=>row.platform!==reply.platform))throw new Error("Runtime inventory contains an original from a different platform.");
  return {...reply,profiles} as unknown as RuntimeInventory;
}
export async function chooseRuntimeProfile(project:Project):Promise<RuntimePlan|null>{
  desktop();const original=structuredClone(project);
  const result=await invoke<unknown>("choose_runtime_profile_bundle",{request:scopeOf(original)});
  return result===null?null:parseRuntimePlan(result,original);
}
async function effect(project:Project,operation:RuntimeRetention,subject:string,beforeDispatch:()=>void,onPrepared:()=>void):Promise<string>{
  beforeDispatch();prepare(project,operation);onPrepared();
  const grant=await invoke<{token:string}>("issue_effect_grant",{request:{effect:"install_runtime",...scopeOf(project),subject}});
  if(!uuid(grant?.token))throw new Error("The exact local runtime permission was not returned.");
  return grant.token;
}
function operationFailure(value:unknown,operation:RuntimeRetention):{known:boolean;message:string}|null{
  try {
    const failure=record(value,["schema","operation","scope","install_id","manifest_sha256","certainty","message"]);
    if(failure.schema!=="motionwright.runtime-profile-failure/1"||failure.operation!==operation.kind
      || !sameValue(parseScope(failure.scope),operation.scope)||failure.install_id!==operation.install_id
      || failure.manifest_sha256!==operation.manifest_sha256||!["not_started","outcome_unknown"].includes(String(failure.certainty))
      || typeof failure.message!=="string"||failure.message.length>2048)return null;
    return {known:failure.certainty==="not_started",message:failure.message};
  } catch {return null;}
}
function refuseOriginal(project:Project,operation:RuntimeRetention){
  const rows=readRuntimeRetention(project);
  const index=rows.findIndex(row=>row.kind===operation.kind&&row.install_id===operation.install_id);
  if(index<0||!sameValue({...rows[index],state:operation.state},operation)||rows[index].state!=="dispatched")
    throw new Error("The retained runtime refusal cannot replace another operation.");
  rows[index]={...operation,state:"not_sent"};save(project,rows);
}
export async function installRuntimeProfile(project:Project,plan:RuntimePlan,beforeDispatch:()=>void):Promise<RuntimeInstallation>{
  desktop();const original=structuredClone(project),selected=parseRuntimePlan(structuredClone(plan),original);
  const operation:RuntimeRetention={install_id:selected.plan_id,scope:scopeOf(original),manifest_sha256:selected.manifest_sha256,profile:selected.profile,kind:"install",state:"prepared"};
  let sent=false,prepared=false;
  try{
    const token=await effect(original,operation,selected.effect_subject,beforeDispatch,()=>{prepared=true;});
    beforeDispatch();transition(original,operation,"dispatched");sent=true;
    const result=parseRuntimeInstallation(await invoke<unknown>("install_runtime_profile",{request:{scope:operation.scope,plan_id:selected.plan_id,manifest_sha256:selected.manifest_sha256,effect_grant:token}}),original,operation);
    if(result.state!=="installed"||result.platform!==selected.platform||result.profile!==selected.profile)throw new Error("The local runtime copy has not been confirmed. Read its original result.");
    transition(original,operation,"acknowledged");return result;
  }catch(error){
    const failure=sent?operationFailure(error,operation):null;
    if(failure?.known)refuseOriginal(original,operation);
    if(prepared&&!sent){const retained=readRuntimeRetention(original).find(row=>row.kind===operation.kind&&row.install_id===operation.install_id);if(retained?.state==="prepared")transition(original,operation,"not_sent");}
    throw failure?new Error(failure.message):error;
  }
}
export function acknowledgeRuntimeProfileResult(project:Project,result:RuntimeInstallation){
  if(!sameValue(result.scope,scopeOf(project)))throw new Error("The original runtime result is from another project revision.");
  if(result.state==="outcome_unknown")return;
  const originals=readRuntimeRetention(project).filter(row=>row.install_id===result.install_id&&runtimeUnconfirmed(row));
  if(originals.some(row=>row.manifest_sha256!==result.manifest_sha256||row.profile!==result.profile))
    throw new Error("The retained original runtime identity differs from its stored outcome.");
  for(const row of originals){
    if(row.kind==="install"||result.state==="rolled_back")transition(project,row,"acknowledged");
  }
}
export async function readOriginalRuntimeProfile(
  project:Project,original:{install_id:string;manifest_sha256:string},mode:"acknowledge"|"read_only"="acknowledge",
):Promise<RuntimeInstallation>{
  desktop();const current=structuredClone(project);
  if(!uuid(original.install_id)||!sha(original.manifest_sha256))throw new Error("Select an original runtime operation.");
  const result=parseRuntimeInstallation(await invoke<unknown>("read_original_runtime_profile",{request:{scope:scopeOf(current),install_id:original.install_id}}),current,original);
  if(mode==="acknowledge")acknowledgeRuntimeProfileResult(current,result);
  return result;
}
export async function rollbackRuntimeProfile(project:Project,installation:RuntimeInstallation,beforeDispatch:()=>void):Promise<RuntimeInstallation>{
  desktop();const original=structuredClone(project),selected=parseRuntimeInstallation(structuredClone(installation),original);
  if(!selected.rollback_available)throw new Error("This original runtime operation cannot be rolled back again.");
  const operation:RuntimeRetention={install_id:selected.install_id,scope:scopeOf(original),manifest_sha256:selected.manifest_sha256,profile:selected.profile,kind:"rollback",state:"prepared"};
  let sent=false,prepared=false;
  try{
    const token=await effect(original,operation,selected.rollback_subject,beforeDispatch,()=>{prepared=true;});
    beforeDispatch();transition(original,operation,"dispatched");sent=true;
    const result=parseRuntimeInstallation(await invoke<unknown>("rollback_runtime_profile",{request:{scope:operation.scope,install_id:selected.install_id,manifest_sha256:selected.manifest_sha256,effect_grant:token}}),original,operation);
    if(result.state!=="rolled_back"||result.platform!==selected.platform||result.profile!==selected.profile)throw new Error("Rollback was not confirmed. Read its original result before another operation.");
    transition(original,operation,"acknowledged");return result;
  }catch(error){
    const failure=sent?operationFailure(error,operation):null;
    if(failure?.known)refuseOriginal(original,operation);
    if(prepared&&!sent){const retained=readRuntimeRetention(original).find(row=>row.kind===operation.kind&&row.install_id===operation.install_id);if(retained?.state==="prepared")transition(original,operation,"not_sent");}
    throw failure?new Error(failure.message):error;
  }
}

export function discardUnsentRuntimePreparation(project:Project,operation:RuntimeRetention){
  if(operation.state!=="prepared")throw new Error("Only a preparation that was never dispatched can be discarded.");
  transition(project,operation,"not_sent");
}
