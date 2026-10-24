/** Recoverable author input for the existing descriptor and public-binding forms.
 * A descriptor records intent; it never grants an effect or proves a remote import. */
import type {Change,Project,ProjectEvent,ExtensionProfile,HandoffBinding} from "./types";
import {sameValue} from "./creativeProduction";
import {sameProjectRevision} from "./projectMutation";
import {recoveryOrigin,matchesRecoveryOrigin,isRecoveryOrigin,type RecoveryOrigin} from "./recoveryOrigin";

export interface IntegrationInput {
  extensionId:string; extensionName:string; extensionKind:ExtensionProfile["kind"];
  extensionVersion:string; extensionDigest:string; extensionLicense:string; extensionSource:string;
  extensionRights:ExtensionProfile["rights_status"]; extensionEnabled:boolean;
  extensionPermissions:ExtensionProfile["permissions"]; showExtensionForm:boolean;
  handoffId:string; handoffDirection:HandoffBinding["direction"]; handoffKind:HandoffBinding["external_kind"];
  handoffExternalId:string; handoffRevision:string; handoffLocalResource:string; handoffDigest:string;
}
export const EXTENSION_FIELDS=["extensionId","extensionName","extensionKind","extensionVersion","extensionDigest","extensionLicense","extensionSource","extensionRights","extensionEnabled","extensionPermissions"] as const;
export const HANDOFF_FIELDS=["handoffId","handoffDirection","handoffKind","handoffExternalId","handoffRevision","handoffLocalResource","handoffDigest"] as const;
export const INTEGRATION_FIELDS=[...EXTENSION_FIELDS,"showExtensionForm",...HANDOFF_FIELDS] as const;
export type IntegrationField=typeof INTEGRATION_FIELDS[number];
export type IntegrationAction="extension"|"handoff"|"toggle_extension"|"remove_extension"|"remove_handoff";
export type IntegrationChange=Extract<Change,{type:"upsert_extension"|"upsert_handoff"|"remove_extension"|"remove_handoff"}>;
export interface IntegrationSnapshot {
  extensions:ExtensionProfile[]; handoffs:HandoffBinding[]; resources:string[]; locks:string[];
}
export interface IntegrationPending {
  /** Comparison identity only. Never an IPC identity or permission to replay. */
  id:string; revision:number; input:IntegrationInput; sequences:Record<IntegrationField,number>;
  action:IntegrationAction; targetId:string|null; change:IntegrationChange;
  outcome:"UNKNOWN"|"NOT_SENT"; diagnostic?:string;
}
export interface IntegrationDraft {
  schema:"motionwright.integration-draft/1"; scope:string; baseRevision:number; base:IntegrationSnapshot;
  input:IntegrationInput; sequences:Record<IntegrationField,number>; touched:IntegrationField[]; pending:IntegrationPending|null;
  reviewRetention?:IntegrationReviewRetention;
}
export type IntegrationReviewRetention={
  schema:"motionwright.integration-review-retention/2"; origins:RecoveryOrigin[];
}|{
  /** Read-only compatibility for an earlier retained local origin. */
  schema:"motionwright.integration-review-retention/1";
  original:{writerId:string;raw:string};armed:{writerId:string;raw:string};
};
const clone=<T,>(value:T):T=>structuredClone(value);
const json=(value:unknown)=>JSON.stringify(value);
const bytes=(value:string)=>new TextEncoder().encode(value).length;
const integer=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0;
const record=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==="object"&&!Array.isArray(value);
const exact=(value:Record<string,unknown>,keys:readonly string[],optional:readonly string[]=[])=>keys.every(key=>Object.hasOwn(value,key))&&Object.keys(value).every(key=>keys.includes(key)||optional.includes(key));
const uuid=(value:unknown):value is string=>typeof value==="string"&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const strings=(value:unknown):value is string[]=>Array.isArray(value)&&value.every(item=>typeof item==="string");
const controls=(value:string)=>/[\u0000-\u001f\u007f-\u009f]/u.test(value);
const digest=(value:string)=>/^[0-9a-f]{64}$/.test(value);
const kindValues=["remotion-renderer","manim-gl-renderer","generative-assets","catalog-package"];
const directionValues=["context_input","context_output","artifact_output","evidence_output"];
const externalKindValues=["release","target","context","artifact","evidence"];
const permissionValues=["read_project","read_assets","write_artifacts","network"];
const rightsValues=["cleared","restricted","unknown"];
export const integrationScope=(project:Project)=>json([project.id,project.generation,project.active_branch,"integration-authoring"]);
export const integrationHasWork=(draft:IntegrationDraft)=>draft.touched.length>0||draft.pending!==null;
export const integrationUnknown=(draft:IntegrationDraft)=>draft.pending?.outcome==="UNKNOWN";
export function integrationEmptyInput(project:Project):IntegrationInput {
  return {extensionId:crypto.randomUUID(),extensionName:"Reviewed optional package",extensionKind:"remotion-renderer",
    extensionVersion:"",extensionDigest:"",extensionLicense:"",extensionSource:"",extensionRights:"unknown",
    extensionEnabled:false,extensionPermissions:["read_project"],showExtensionForm:false,
    handoffId:crypto.randomUUID(),handoffDirection:"context_input",handoffKind:"release",handoffExternalId:"",
    handoffRevision:"",handoffLocalResource:"project:"+project.id,handoffDigest:""};
}
export function integrationSnapshot(project:Project):IntegrationSnapshot {
  return clone({extensions:project.extensions,handoffs:project.handoffs,
    resources:["project:"+project.id,...project.scenes.map(value=>"scene:"+value.id),
      ...project.assets.map(value=>"asset:"+value.id),...project.deliverables.map(value=>"deliverable:"+value.id)],
    locks:project.locks.map(lock=>lock.resource+":"+lock.kind)});
}
function assertScope(draft:IntegrationDraft,project:Project) {
  if(draft.scope!==integrationScope(project))throw new Error("This integration input belongs to another project, generation or branch.");
}
export function createIntegrationDraft(project:Project):IntegrationDraft {
  return {schema:"motionwright.integration-draft/1",scope:integrationScope(project),baseRevision:project.revision,
    base:integrationSnapshot(project),input:integrationEmptyInput(project),
    sequences:Object.fromEntries(INTEGRATION_FIELDS.map(field=>[field,0])) as Record<IntegrationField,number>,touched:[],pending:null};
}
export function editIntegrationField<K extends IntegrationField>(draft:IntegrationDraft,field:K,value:IntegrationInput[K]):IntegrationDraft {
  if(!INTEGRATION_FIELDS.includes(field)||!integer(draft.sequences[field])||draft.sequences[field]===Number.MAX_SAFE_INTEGER)
    throw new Error("The integration input sequence is invalid or exhausted.");
  return {...draft,input:{...draft.input,[field]:clone(value)},sequences:{...draft.sequences,[field]:draft.sequences[field]+1},
    touched:draft.touched.includes(field)?draft.touched:[...draft.touched,field]};
}
export function editIntegrationFields(draft:IntegrationDraft,values:Partial<IntegrationInput>):IntegrationDraft {
  let next=draft;
  for(const field of Object.keys(values) as IntegrationField[]){
    if(!INTEGRATION_FIELDS.includes(field))throw new Error("Unrecognized integration input field.");
    const value=values[field];if(value!==undefined&&!sameValue(next.input[field],value))next=editIntegrationField(next,field,value);
  }
  return next;
}
export function integrationExtensionInput(extension:ExtensionProfile):Partial<IntegrationInput> {
  return {extensionId:extension.id,extensionName:extension.name,extensionKind:extension.kind,
    extensionVersion:extension.package_version,extensionDigest:extension.digest_sha256,extensionLicense:extension.license,
    extensionSource:extension.source,extensionRights:extension.rights_status,extensionEnabled:extension.enabled,
    extensionPermissions:clone(extension.permissions),showExtensionForm:true};
}
export function integrationHandoffInput(binding:HandoffBinding):Partial<IntegrationInput> {
  return {handoffId:binding.id,handoffDirection:binding.direction,handoffKind:binding.external_kind,
    handoffExternalId:binding.external_id,handoffRevision:binding.external_revision??"",
    handoffLocalResource:binding.local_resource,handoffDigest:binding.artifact_sha256??""};
}
export function reconcileIntegrationDraft(draft:IntegrationDraft,project:Project):IntegrationDraft {
  assertScope(draft,project);
  if(project.revision<draft.baseRevision||integrationHasWork(draft)||draft.baseRevision===project.revision&&sameValue(draft.base,integrationSnapshot(project)))return draft;
  return {...draft,baseRevision:project.revision,base:integrationSnapshot(project)};
}
export function integrationConflict(draft:IntegrationDraft,project:Project):boolean {
  assertScope(draft,project);
  return integrationHasWork(draft)&&(draft.baseRevision!==project.revision||!sameValue(draft.base,integrationSnapshot(project)));
}
export function resolveIntegrationDraft(draft:IntegrationDraft,project:Project,choice:"draft"|"empty"):IntegrationDraft {
  assertScope(draft,project);if(integrationUnknown(draft))throw new Error("Read the unknown integration outcome before choosing another base.");
  const reset=createIntegrationDraft(project);
  return {...draft,baseRevision:project.revision,base:integrationSnapshot(project),input:choice==="draft"?clone(draft.input):reset.input,
    touched:choice==="draft"?[...draft.touched]:[],pending:null};
}
export function recoverIntegrationSource(source:IntegrationDraft,later:IntegrationDraft,project:Project):IntegrationDraft {
  assertScope(source,project);assertScope(later,project);
  if(later.pending&&!sameValue(later.pending,source.pending))throw new Error("Two uncertain integration attempts must remain in separate copies.");
  const next=clone(source);
  if(later.reviewRetention){
    if(next.reviewRetention&&!sameValue(next.reviewRetention,later.reviewRetention))throw new Error("Separate integration origins cannot replace each other.");
    next.reviewRetention=clone(later.reviewRetention);
  }
  for(const field of later.touched){
    const sequence=Math.max(next.sequences[field],later.sequences[field])+1;
    if(!integer(sequence))throw new Error("Integration input sequence is exhausted; both copies were kept.");
    (next.input as unknown as Record<string,unknown>)[field]=clone(later.input[field]);next.sequences[field]=sequence;
  }
  next.touched=[...new Set([...next.touched,...later.touched])];return reconcileIntegrationDraft(next,project);
}
function bounded(value:string,label:string,maximum:number,noControls=false):void {
  if(!value.trim()||bytes(value)>maximum||noControls&&controls(value))throw new Error(label+" is empty, invalid or exceeds "+maximum+" UTF-8 bytes. Your exact input is retained.");
}
export function integrationExtension(input:IntegrationInput):ExtensionProfile {
  bounded(input.extensionName,"Descriptor name",160);bounded(input.extensionVersion,"Package version",96);
  bounded(input.extensionLicense,"License",128);bounded(input.extensionSource,"Package source",2048,true);
  if(!uuid(input.extensionId)||!kindValues.includes(input.extensionKind)||!rightsValues.includes(input.extensionRights)
    ||!digest(input.extensionDigest))throw new Error("Select a supported descriptor and enter exactly 64 lowercase digest characters; the editor preserves your raw input.");
  if(input.extensionEnabled&&input.extensionRights!=="cleared")throw new Error("Rights must be cleared before explicit opt-in.");
  if(input.extensionPermissions.length>4||new Set(input.extensionPermissions).size!==input.extensionPermissions.length
    ||!input.extensionPermissions.every(value=>permissionValues.includes(value)))throw new Error("Descriptor permissions are invalid or duplicated.");
  return {id:input.extensionId,name:input.extensionName,kind:input.extensionKind,package_version:input.extensionVersion,
    digest_sha256:input.extensionDigest,license:input.extensionLicense,source:input.extensionSource,
    rights_status:input.extensionRights,enabled:input.extensionEnabled,permissions:clone(input.extensionPermissions)};
}
export function integrationHandoff(input:IntegrationInput,resources:readonly string[]):HandoffBinding {
  bounded(input.handoffExternalId,"External resource ID",256,true);
  bounded(input.handoffLocalResource,"Local resource",512,true);
  if(input.handoffRevision!=="")bounded(input.handoffRevision,"External revision",256,true);
  if(!uuid(input.handoffId)||!directionValues.includes(input.handoffDirection)||!externalKindValues.includes(input.handoffKind))
    throw new Error("Select a supported public binding direction and resource kind.");
  if(!resources.includes(input.handoffLocalResource))throw new Error("The selected local binding is absent from this project. Your original choice remains retained.");
  const output=input.handoffDirection==="artifact_output"||input.handoffDirection==="evidence_output";
  if(output&&!digest(input.handoffDigest))throw new Error("An output binding requires exactly 64 lowercase digest characters. Your exact input is retained.");
  return {id:input.handoffId,system:"launchwright",direction:input.handoffDirection,external_kind:input.handoffKind,
    external_id:input.handoffExternalId,external_revision:input.handoffRevision===""?null:input.handoffRevision,
    local_resource:input.handoffLocalResource,artifact_sha256:output?input.handoffDigest:null};
}
function requestedChange(draft:IntegrationDraft,action:IntegrationAction,targetId:string|null):IntegrationChange {
  if(action==="extension")return {type:"upsert_extension",extension:integrationExtension(draft.input)};
  if(action==="handoff")return {type:"upsert_handoff",binding:integrationHandoff(draft.input,draft.base.resources)};
  if(!uuid(targetId))throw new Error("Select an existing descriptor or binding.");
  if(action==="remove_handoff"){
    if(draft.base.handoffs.filter(value=>value.id===targetId).length!==1)throw new Error("The binding is no longer present.");
    return {type:"remove_handoff",binding_id:targetId};
  }
  const matches=draft.base.extensions.filter(value=>value.id===targetId);
  if(matches.length!==1)throw new Error("The descriptor is no longer present.");
  if(action==="remove_extension")return {type:"remove_extension",extension_id:targetId};
  if(action!=="toggle_extension")throw new Error("Unsupported integration action.");
  const extension={...clone(matches[0]),enabled:!matches[0].enabled};
  integrationExtension({...draft.input,...integrationExtensionInput(extension)});
  return {type:"upsert_extension",extension};
}
function changeMatchesInput(pending:IntegrationPending,base:IntegrationSnapshot):boolean {
  const draft={input:pending.input,base} as IntegrationDraft;
  return sameValue(requestedChange(draft,pending.action,pending.targetId),pending.change);
}
export interface IntegrationSubmission {draft:IntegrationDraft;change:IntegrationChange}
const liveSubmissions=new WeakMap<IntegrationSubmission,{sealed:string;dispatched:boolean;settled:boolean}>();
export function prepareIntegrationSubmission(draft:IntegrationDraft,project:Project,action:IntegrationAction,targetId:string|null,id:string):IntegrationSubmission {
  assertScope(draft,project);
  if(!uuid(id)||integrationUnknown(draft)||integrationConflict(draft,project))throw new Error("Review the current revision and any unconfirmed attempt before saving separately.");
  if(project.locks.some(lock=>lock.kind==="content"&&lock.resource==="project:"+project.id))throw new Error("This project is content-locked.");
  const change=requestedChange(draft,action,targetId);
  if(change.type==="upsert_extension"&&project.extensions.some(value=>sameValue(value,change.extension))
    ||change.type==="upsert_handoff"&&project.handoffs.some(value=>sameValue(value,change.binding)))
    throw new Error("These exact integration fields are already saved. Edit them or start a separate record explicitly.");
  const next:IntegrationDraft={...draft,pending:{id,revision:project.revision,input:clone(draft.input),sequences:clone(draft.sequences),
    action,targetId,change:clone(change),outcome:"UNKNOWN"}};
  const submission={draft:next,change};liveSubmissions.set(submission,{sealed:json(submission),dispatched:false,settled:false});return submission;
}
export function integrationPreparationMatches(before:IntegrationDraft,source:Project,current:IntegrationDraft,project:Project):boolean {
  return sameProjectRevision(source,project)&&sameValue(integrationSnapshot(source),integrationSnapshot(project))
    &&before.scope===current.scope&&sameValue(before.input,current.input)&&sameValue(before.sequences,current.sequences)&&sameValue(before.pending,current.pending);
}
export function dispatchIntegrationSubmission(submission:IntegrationSubmission,draft:IntegrationDraft,project:Project):void {
  const proof=liveSubmissions.get(submission);
  if(!proof||proof.dispatched||proof.settled||proof.sealed!==json(submission)||draft.scope!==integrationScope(project)
    ||draft.baseRevision!==project.revision||!sameValue(submission.draft,draft)||!sameValue(draft.base,integrationSnapshot(project)))
    throw new Error("Only the prepared original integration request can be dispatched once.");
  proof.dispatched=true;
}
function integrationValuesMatch(draft:IntegrationDraft,project:Project):boolean {
  const pending=draft.pending;if(!pending)return false;
  const change=pending.change;
  switch(change.type){
    case "upsert_extension":return project.extensions.some(value=>sameValue(value,change.extension));
    case "upsert_handoff":return project.handoffs.some(value=>sameValue(value,change.binding));
    case "remove_extension":return draft.base.extensions.some(value=>value.id===change.extension_id)&&!project.extensions.some(value=>value.id===change.extension_id);
    case "remove_handoff":return draft.base.handoffs.some(value=>value.id===change.binding_id)&&!project.handoffs.some(value=>value.id===change.binding_id);
  }
}
export function acknowledgeIntegrationSubmission(draft:IntegrationDraft,submission:IntegrationSubmission,receipt:Project):IntegrationDraft {
  const proof=liveSubmissions.get(submission),pending=draft.pending;
  if(!proof||!proof.dispatched||proof.settled||proof.sealed!==json(submission)||!pending||pending.id!==submission.draft.pending?.id
    ||!sameValue(pending.change,submission.change)||!receipt||integrationScope(receipt)!==draft.scope
    ||receipt.revision!==pending.revision+1||!integrationValuesMatch(draft,receipt))
    throw new Error("The original integration response did not confirm this exact saved revision. Input and the uncertain attempt were retained.");
  proof.settled=true;
  const submittedFields:readonly IntegrationField[]=pending.action==="extension"?EXTENSION_FIELDS:pending.action==="handoff"?HANDOFF_FIELDS:[];
  // Saved records remain source-bound editable input. A peer revision must
  // not silently rebase retained fields and make them overwrite a newer record.
  const touched=[...new Set([...draft.touched,...submittedFields])],input=clone(draft.input);
  if(pending.action==="toggle_extension"&&pending.change.type==="upsert_extension"
    &&input.extensionId===pending.change.extension.id
    &&draft.sequences.extensionEnabled===pending.sequences.extensionEnabled
    &&draft.sequences.extensionKind===pending.sequences.extensionKind
    &&draft.sequences.extensionRights===pending.sequences.extensionRights){
    const old=draft.base.extensions.find(value=>value.id===input.extensionId);
    if(old&&pending.input.extensionEnabled===old.enabled)input.extensionEnabled=pending.change.extension.enabled;
  }
  return {...draft,baseRevision:receipt.revision,base:integrationSnapshot(receipt),input,touched,pending:null};
}
export function retainIntegrationFailure(draft:IntegrationDraft,id:string,diagnostic:string,notSent=false):IntegrationDraft {
  return draft.pending?.id===id?{...draft,pending:{...draft.pending,diagnostic,outcome:notSent?"NOT_SENT":"UNKNOWN"}}:draft;
}
export function settleIntegrationFailure(submission:IntegrationSubmission):void {const proof=liveSubmissions.get(submission);if(proof)proof.settled=true;}
export interface IntegrationOutcomeTransport {readProject(id:string):Promise<Project>;readHistory(project:Project,after:number,limit:number):Promise<ProjectEvent[]>}
export interface IntegrationOutcomeReview {scope:string;pending:IntegrationPending;input:IntegrationInput;sequences:Record<IntegrationField,number>;project:Project;events:ProjectEvent[];matchingValueRevisions:number[]}
const inspectedOutcomes=new WeakMap<IntegrationOutcomeReview,string>();
const sameObserved=(a:Project,b:Project)=>sameProjectRevision(a,b)&&integrationScope(a)===integrationScope(b)&&sameValue(a,b);
export function integrationOutcomeMatches(draft:IntegrationDraft,project:Project,review:IntegrationOutcomeReview|null):boolean {
  return !!review&&inspectedOutcomes.get(review)===json(review)&&review.scope===draft.scope&&integrationScope(project)===draft.scope
    &&sameObserved(project,review.project)&&sameValue(draft.pending,review.pending)&&sameValue(draft.input,review.input)&&sameValue(draft.sequences,review.sequences);
}
export async function inspectIntegrationOutcome(draft:IntegrationDraft,selected:Project,transport:IntegrationOutcomeTransport):Promise<IntegrationOutcomeReview> {
  assertScope(draft,selected);if(!integrationUnknown(draft)||!draft.pending)throw new Error("There is no unconfirmed integration attempt to inspect.");
  const sealed=json(draft),pending=clone(draft.pending),first=await transport.readProject(selected.id);
  if(integrationScope(first)!==draft.scope||first.revision<pending.revision||first.revision-pending.revision>2048)throw new Error("The saved integration revision is unavailable or outside the bounded history.");
  const events=await transport.readHistory(first,pending.revision,2048);
  if(events.length!==first.revision-pending.revision||events.some((event,index)=>event.revision!==pending.revision+index+1))
    throw new Error("The integration history is incomplete or not consecutive. The attempt remains unknown.");
  const last=await transport.readProject(selected.id);
  if(sealed!==json(draft)||!sameObserved(first,last)||!sameObserved(selected,last))throw new Error("Saved integration source changed during inspection. Reload and read it again.");
  const review:IntegrationOutcomeReview={scope:draft.scope,pending,input:clone(draft.input),sequences:clone(draft.sequences),project:clone(last),events:clone(events),
    matchingValueRevisions:events.filter(event=>sameValue(event.change,pending.change)).map(event=>event.revision)};
  inspectedOutcomes.set(review,json(review));return review;
}
export function releaseIntegrationOutcome(draft:IntegrationDraft,project:Project,review:IntegrationOutcomeReview):IntegrationDraft {
  if(!integrationOutcomeMatches(draft,project,review))throw new Error("Read this exact integration input and current source before separating the attempt.");
  return {...draft,baseRevision:project.revision,base:integrationSnapshot(project),input:clone(draft.input),touched:[...draft.touched],pending:null};
}

export const INTEGRATION_COPY_PREFIX = "motionwright.integration-recovery.v1:";
export const INTEGRATION_POINTER_PREFIX = "motionwright.integration-writer.v1:";
export const INTEGRATION_MAX_COPY = 1024 * 1024, INTEGRATION_MAX_TOTAL = 4 * 1024 * 1024, INTEGRATION_MAX_COPIES = 64;
export const integrationCopyKey = (scope: string, writer: string) => INTEGRATION_COPY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const integrationPointerKey = (scope: string) => INTEGRATION_POINTER_PREFIX + encodeURIComponent(scope);
export type IntegrationStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface IntegrationCustody {
  writerId: string; expectedRaw: string | null; expectedPointer: string | null;
  /** Live proof of this writer's attempted pointer publication, never restored from a copy. */
  pointerAttempt?: { previous: string | null; target: string; raw: string };
}
export interface IntegrationCopy { schema: "motionwright.integration-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: IntegrationDraft }
export interface IntegrationRecoveryEntry { key: string; writerId: string; raw: string; value: IntegrationCopy | null }
/** First successful local observations survive read failures and later replacements.
 * They are byte custody only; reading a copy never grants dispatch or ACK authority. */
export interface IntegrationRecoveryObservation {
  pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null;
}
function inputShape(value:unknown):value is IntegrationInput {
  if(!record(value)||!exact(value,INTEGRATION_FIELDS))return false;
  return INTEGRATION_FIELDS.every(field=>field==="extensionPermissions"
    ?strings(value[field])&&value[field].length<=4&&new Set(value[field]).size===value[field].length&&value[field].every(item=>permissionValues.includes(item))
    :field==="extensionEnabled"||field==="showExtensionForm"?typeof value[field]==="boolean"
    :typeof value[field]==="string"&&bytes(value[field])<=64*1024)
    &&uuid(value.extensionId)&&uuid(value.handoffId)&&kindValues.includes(String(value.extensionKind))
    &&rightsValues.includes(String(value.extensionRights))&&directionValues.includes(String(value.handoffDirection))
    &&externalKindValues.includes(String(value.handoffKind));
}
function extensionShape(value:unknown):value is ExtensionProfile {
  if(!record(value)||!exact(value,["id","name","kind","package_version","digest_sha256","license","source","rights_status","enabled","permissions"])
    ||!uuid(value.id)||!["name","kind","package_version","digest_sha256","license","source","rights_status"].every(key=>typeof value[key]==="string")
    ||typeof value.enabled!=="boolean"||!strings(value.permissions))return false;
  try{integrationExtension(integrationExtensionInput(value as unknown as ExtensionProfile) as IntegrationInput);return true;}catch{return false;}
}
function handoffShape(value:unknown):value is HandoffBinding {
  if(!record(value)||!exact(value,["id","system","direction","external_kind","external_id","external_revision","local_resource","artifact_sha256"])
    ||value.system!=="launchwright"||!uuid(value.id)
    ||!["direction","external_kind","external_id","local_resource"].every(key=>typeof value[key]==="string")
    ||value.external_revision!==null&&typeof value.external_revision!=="string"
    ||value.artifact_sha256!==null&&typeof value.artifact_sha256!=="string")return false;
  try{
    const binding=value as unknown as HandoffBinding;
    return sameValue(integrationHandoff(integrationHandoffInput(binding) as IntegrationInput,[binding.local_resource]),binding);
  }catch{return false;}
}
function snapshotShape(value:unknown):value is IntegrationSnapshot {
  if(!record(value)||!exact(value,["extensions","handoffs","resources","locks"])
    ||!Array.isArray(value.extensions)||value.extensions.length>4096||!value.extensions.every(extensionShape)
    ||!Array.isArray(value.handoffs)||value.handoffs.length>4096||!value.handoffs.every(handoffShape)
    ||!strings(value.resources)||value.resources.length>16384||!strings(value.locks)||value.locks.length>16384)return false;
  return new Set(value.extensions.map(value=>value.id)).size===value.extensions.length
    &&new Set(value.handoffs.map(value=>value.id)).size===value.handoffs.length;
}
const fieldsShape=(value:unknown):value is IntegrationField[]=>strings(value)&&new Set(value).size===value.length&&value.every(field=>INTEGRATION_FIELDS.includes(field as IntegrationField));
const sequencesShape=(value:unknown):value is Record<IntegrationField,number>=>record(value)&&exact(value,INTEGRATION_FIELDS)&&INTEGRATION_FIELDS.every(field=>integer(value[field]));
function changeShape(value:unknown):value is IntegrationChange {
  if(!record(value))return false;
  if(value.type==="upsert_extension")return exact(value,["type","extension"])&&extensionShape(value.extension);
  if(value.type==="upsert_handoff")return exact(value,["type","binding"])&&handoffShape(value.binding);
  if(value.type==="remove_extension")return exact(value,["type","extension_id"])&&uuid(value.extension_id);
  return value.type==="remove_handoff"&&exact(value,["type","binding_id"])&&uuid(value.binding_id);
}
function cleanDraft(value:unknown,scope:string,depth=0):IntegrationDraft {
  const identity:unknown=JSON.parse(scope);
  if(!Array.isArray(identity)||identity.length!==4||!identity.slice(0,3).every(uuid)||identity[3]!=="integration-authoring"
    ||!record(value)||!exact(value,["schema","scope","baseRevision","base","input","sequences","touched","pending"],["reviewRetention"])
    ||value.schema!=="motionwright.integration-draft/1"||value.scope!==scope||!integer(value.baseRevision)||!snapshotShape(value.base)
    ||!inputShape(value.input)||!sequencesShape(value.sequences)||!fieldsShape(value.touched))
    throw new Error("The integration recovery has an unsupported shape or owner.");
  let pending:IntegrationPending|null=null;
  if(value.pending!==null){
    const sent=value.pending;
    if(!record(sent)||!exact(sent,["id","revision","input","sequences","action","targetId","change","outcome"],["diagnostic"])
      ||!uuid(sent.id)||!integer(sent.revision)||sent.revision!==value.baseRevision||!inputShape(sent.input)||!sequencesShape(sent.sequences)
      ||!INTEGRATION_FIELDS.every(field=>(sent.sequences as Record<IntegrationField,number>)[field]<=(value.sequences as Record<IntegrationField,number>)[field])
      ||!["extension","handoff","toggle_extension","remove_extension","remove_handoff"].includes(String(sent.action))||sent.targetId!==null&&!uuid(sent.targetId)
      ||!changeShape(sent.change)||!["UNKNOWN","NOT_SENT"].includes(String(sent.outcome))||sent.diagnostic!==undefined&&typeof sent.diagnostic!=="string")
      throw new Error("The pending integration comparison is unreadable.");
    pending={...clone(sent),outcome:"UNKNOWN"} as unknown as IntegrationPending;
    if(!changeMatchesInput(pending,value.base))throw new Error("The pending integration comparison has a different submitted input or source.");
  }
  const reviewRetention=value.reviewRetention===undefined?undefined:cleanReviewRetention(value.reviewRetention,scope,depth);
  return {schema:"motionwright.integration-draft/1",scope,baseRevision:value.baseRevision,base:clone(value.base),
    input:clone(value.input),sequences:clone(value.sequences),touched:[...value.touched],pending,...(reviewRetention?{reviewRetention}:{})};
}

export function decodeIntegrationCopy(raw: string, scope: string, writer: string): IntegrationCopy {
  return decodeIntegrationCopyAtDepth(raw, scope, writer, 0);
}
function decodeIntegrationCopyAtDepth(raw: string, scope: string, writer: string, depth: number): IntegrationCopy {
  if (depth > INTEGRATION_MAX_COPIES) throw new Error("The Integration review origin chain exceeds its bound.");
  if (bytes(raw) > INTEGRATION_MAX_COPY) throw new Error("Integration recovery exceeds 1 MiB. Download the complete input before reducing it.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"]) || value.schema !== "motionwright.integration-recovery/1"
    || value.scope !== scope || value.writerId !== writer || !uuid(writer) || !integer(value.updatedAt)) throw new Error("This Integration copy is unreadable or belongs to another writer.");
  const draft = cleanDraft(value.draft, scope, depth);
  if (integrationReviewOrigins(draft).has(writer)) throw new Error("An Integration successor cannot be its own review origin.");
  return { schema: "motionwright.integration-recovery/1", scope, writerId: writer, updatedAt: value.updatedAt, draft };
}
function addIntegrationOrigin(origins:Map<string,RecoveryOrigin>,origin:RecoveryOrigin):void {
  const previous=origins.get(origin.writerId);
  if(previous&&!sameValue(previous,origin))throw new Error("Different Integration review observations refer to the same writer.");
  origins.set(origin.writerId,clone(origin));
  if(origins.size>INTEGRATION_MAX_COPIES)throw new Error("The Integration review origin inventory exceeds its bound.");
}
function cleanReviewRetention(value:unknown,scope:string,depth=0):IntegrationReviewRetention {
  if(!record(value))throw new Error("Invalid integration review origin condition.");
  const origins=new Map<string,RecoveryOrigin>();
  if(value.schema==="motionwright.integration-review-retention/2"){
    if(!exact(value,["schema","origins"])||!Array.isArray(value.origins)||value.origins.length<2
      ||value.origins.length>INTEGRATION_MAX_COPIES||!value.origins.every(isRecoveryOrigin))
      throw new Error("Invalid flat Integration review origin condition.");
    for(const origin of value.origins){if(origins.has(origin.writerId))throw new Error("Duplicate Integration review origin.");addIntegrationOrigin(origins,origin);}
  }else{
    if(!exact(value,["schema","original","armed"])||value.schema!=="motionwright.integration-review-retention/1")
      throw new Error("Invalid integration review origin condition.");
    for(const source of [value.original,value.armed]){
      if(!record(source)||!exact(source,["writerId","raw"])||!uuid(source.writerId)||typeof source.raw!=="string")
        throw new Error("Invalid integration review origin bytes.");
      const observed=decodeIntegrationCopyAtDepth(source.raw,scope,source.writerId,depth+1).draft;
      if(!integrationUnknown(observed))throw new Error("An integration review origin must retain its unknown attempt.");
      addIntegrationOrigin(origins,recoveryOrigin(source.writerId,source.raw));
      for(const origin of integrationReviewOrigins(observed).values())addIntegrationOrigin(origins,origin);
    }
    if((value.original as {writerId:string}).writerId===(value.armed as {writerId:string}).writerId)
      throw new Error("Integration review origins must remain separate.");
  }
  return {schema:"motionwright.integration-review-retention/2",origins:[...origins.values()].sort((a,b)=>a.writerId.localeCompare(b.writerId))};
}
export function integrationReviewOrigins(draft:IntegrationDraft):Map<string,RecoveryOrigin> {
  if(!draft.reviewRetention)return new Map();
  const retained=cleanReviewRetention(draft.reviewRetention,draft.scope);
  if(retained.schema!=="motionwright.integration-review-retention/2")throw new Error("Invalid normalized Integration origin condition.");
  return new Map(retained.origins.map(origin=>[origin.writerId,origin]));
}
export function assertIntegrationReviewOrigins(storage:Pick<Storage,"getItem">,draft:IntegrationDraft):void {
  const origins=integrationReviewOrigins(draft);
  for(const origin of origins.values()){
    const key=integrationCopyKey(draft.scope,origin.writerId),raw=storage.getItem(key);
    if(raw===null||!matchesRecoveryOrigin(origin,raw)||storage.getItem(key)!==raw)
      throw new Error("A reviewed Integration origin changed. Its UNKNOWN copy and current input remain separate; verify their original custody before saving.");
    const observed=decodeIntegrationCopy(raw,draft.scope,origin.writerId).draft;
    if(!integrationUnknown(observed))throw new Error("An Integration origin no longer retains its unknown attempt.");
    for(const inherited of integrationReviewOrigins(observed).values())
      if(!sameValue(origins.get(inherited.writerId),inherited))throw new Error("An earlier Integration review origin is missing or changed.");
    if(storage.getItem(key)!==raw)throw new Error("A reviewed Integration origin changed during its custody check.");
  }
}
export function encodeIntegrationCopy(copy: IntegrationCopy): string { const raw = json(copy); decodeIntegrationCopy(raw, copy.scope, copy.writerId); return raw; }
/** Complete author-input salvage, including invalid/oversized input. This file
 * cannot be decoded as a recovery copy, integration manifest or live submission. */
export function encodeIntegrationFile(draft: IntegrationDraft): string {
  return JSON.stringify({schema:"motionwright.integration-input-salvage/1",exportedAt:Date.now(),
    authority:"author_input_only",draft:clone(draft)},null,2);
}
export function assertIntegrationRecoverySource(scope: string, observation: IntegrationRecoveryObservation,
  storage: Pick<Storage, "getItem">, pointers: Pick<Storage, "getItem">, own?: { writerId: string; raw: string }): void {
  if (!observation.pointerObserved) throw new Error("Read the original Integration recovery pointer before making a new copy.");
  if (observation.pointer !== null) {
    if (!observation.rawObserved || observation.raw === null) throw new Error("The original Integration recovery copy is still missing.");
    if (storage.getItem(integrationCopyKey(scope, observation.pointer)) !== observation.raw)
      throw new Error("The original Integration recovery bytes changed. The first observation remains retained.");
    const draft = decodeIntegrationCopy(observation.raw, scope, observation.pointer).draft;
    assertIntegrationReviewOrigins(storage, draft);
  }
  const pointer = pointers.getItem(integrationPointerKey(scope));
  if (pointer !== observation.pointer && !(own && pointer === own.writerId && storage.getItem(integrationCopyKey(scope, own.writerId)) === own.raw))
    throw new Error("The original Integration recovery pointer changed. No different copy was adopted.");
  if (observation.pointer !== null && storage.getItem(integrationCopyKey(scope, observation.pointer)) !== observation.raw)
    throw new Error("The original Integration recovery bytes changed during confirmation. The first observation remains retained.");
}
export function readIntegrationRecoverySource(scope: string, observation: IntegrationRecoveryObservation,
  storage: Pick<Storage, "getItem">, pointers: Pick<Storage, "getItem">): IntegrationDraft | null {
  const pointer = pointers.getItem(integrationPointerKey(scope));
  if (observation.pointerObserved && pointer !== observation.pointer)
    throw new Error("The original Integration recovery pointer changed. No different copy was adopted.");
  if (!observation.pointerObserved) { observation.pointer = pointer; observation.pointerObserved = true; }
  if (pointer === null) { assertIntegrationRecoverySource(scope, observation, storage, pointers); return null; }
  const raw = storage.getItem(integrationCopyKey(scope, pointer));
  if (observation.rawObserved && raw !== observation.raw)
    throw new Error("The original Integration recovery bytes changed. The first observation remains retained.");
  if (!observation.rawObserved) { observation.raw = raw; observation.rawObserved = true; }
  if (raw === null) throw new Error("The original Integration recovery copy is still missing.");
  const recovered = decodeIntegrationCopy(raw, scope, pointer).draft;
  assertIntegrationRecoverySource(scope, observation, storage, pointers);
  return recovered;
}
export function originalIntegrationRecoveryCopy(scope: string,
  observation: IntegrationRecoveryObservation | null): IntegrationRecoveryEntry | null {
  if (!observation?.pointerObserved || observation.pointer === null || !observation.rawObserved || observation.raw === null) return null;
  let value: IntegrationCopy | null = null;
  try { value = decodeIntegrationCopy(observation.raw, scope, observation.pointer); } catch { /* Original unreadable bytes remain downloadable. */ }
  return { key: integrationCopyKey(scope, observation.pointer), writerId: observation.pointer, raw: observation.raw, value };
}
export function readIntegrationCopies(storage: IntegrationStorage, scope: string,
  observation?: IntegrationRecoveryObservation | null): { entries: IntegrationRecoveryEntry[]; limited: boolean } {
  if (storage.length > 4096) throw new Error("The local Integration recovery index exceeds its inspection bound.");
  const prefix = INTEGRATION_COPY_PREFIX + encodeURIComponent(scope) + ":", entries: IntegrationRecoveryEntry[] = []; let limited = false;
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i); if (!key?.startsWith(prefix)) continue;
    if (entries.length >= INTEGRATION_MAX_COPIES) { limited = true; continue; }
    const raw = storage.getItem(key);
    // Anchor the pointed source before a later key/read can fail, including an
    // observed absence. Retry cannot adopt newer bytes under that same writer.
    if (observation?.pointerObserved && observation.pointer !== null && !observation.rawObserved && key === integrationCopyKey(scope, observation.pointer)) {
      observation.raw = raw; observation.rawObserved = true;
    }
    if (raw === null) continue;
    const writerId = key.slice(prefix.length); let value: IntegrationCopy | null = null;
    try { value = decodeIntegrationCopy(raw, scope, writerId); } catch { /* Preserve exact unreadable bytes. */ }
    entries.push({ key, writerId, raw, value });
  }
  return { entries, limited };
}
export function writeIntegrationCopy(storage:IntegrationStorage,session:Pick<Storage,"getItem"|"setItem">,draft:IntegrationDraft,custody:IntegrationCustody):IntegrationCustody {
  if(interruptedIntegrationIntents.has(custody))throw new Error("A separate Integration copy publication is incomplete. Retry that local publication before replacing its original bytes.");
  return writeIntegrationCopyPhase(storage,session,draft,custody,true);
}
function writeIntegrationCopyPhase(storage: IntegrationStorage, session: Pick<Storage, "getItem" | "setItem">, draft: IntegrationDraft, custody: IntegrationCustody,publishPointer:boolean,fixedRaw?:string): IntegrationCustody {
  const key = integrationCopyKey(draft.scope, custody.writerId), pointer = integrationPointerKey(draft.scope);
  const check = () => {
    const observed = session.getItem(pointer);
    if (observed !== custody.expectedPointer && observed !== custody.writerId) throw new Error("Another writer changed the Integration recovery pointer. Earlier copies were kept.");
    if (storage.getItem(key) !== custody.expectedRaw) throw new Error("The Integration copy changed in another window. Original bytes were kept.");
    return observed;
  };
  const observedPointer = check(); assertIntegrationReviewOrigins(storage, draft);
  if (custody.expectedRaw !== null && (!publishPointer || observedPointer === custody.writerId) && sameValue(decodeIntegrationCopy(custody.expectedRaw, draft.scope, custody.writerId).draft, draft)) {
    if (check() !== observedPointer) throw new Error("The integration recovery pointer changed during verification.");
    assertIntegrationReviewOrigins(storage, draft);
    if (check() !== observedPointer) throw new Error("The integration recovery changed during origin verification.");
    custody.expectedPointer = observedPointer; if(publishPointer)delete custody.pointerAttempt; return custody;
  }
  const raw = fixedRaw ?? encodeIntegrationCopy({ schema: "motionwright.integration-recovery/1", scope: draft.scope, writerId: custody.writerId, updatedAt: Date.now(), draft });
  if(fixedRaw!==undefined&&!sameValue(decodeIntegrationCopy(raw,draft.scope,custody.writerId).draft,draft))throw new Error("The prepared Integration copy does not match its exact input.");
  if (custody.expectedRaw !== null) {
    const old = decodeIntegrationCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!integrationHasWork(old) && !sameValue(old, draft)) throw new Error("A clean Integration checkpoint is immutable; use a separate writer.");
  }
  let total = bytes(key) + bytes(raw), count = 1;
  for (let i = 0; i < storage.length; i++) {
    const other = storage.key(i); if (!other?.startsWith(INTEGRATION_COPY_PREFIX) || other === key) continue;
    count++; total += bytes(other) + bytes(storage.getItem(other) ?? "");
  }
  if (count > INTEGRATION_MAX_COPIES || total > INTEGRATION_MAX_TOTAL) throw new Error("Integration recovery is full (64 copies / 4 MiB). Explicitly discard only copies no longer needed.");
  if (check() !== observedPointer) throw new Error("The Integration recovery pointer changed during its inventory check.");
  assertIntegrationReviewOrigins(storage, draft); storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The Integration recovery data write was not confirmed.");
  custody.expectedRaw = raw;
  if (session.getItem(pointer) !== observedPointer) throw new Error("The Integration recovery pointer changed during data publication. The foreign pointer was kept.");
  assertIntegrationReviewOrigins(storage, draft);
  if(!publishPointer){
    if(session.getItem(pointer)!==observedPointer||storage.getItem(key)!==raw)throw new Error("The staged Integration copy changed before verification.");
    assertIntegrationReviewOrigins(storage,draft);
    if(session.getItem(pointer)!==observedPointer||storage.getItem(key)!==raw)throw new Error("The staged Integration custody changed during verification.");
    custody.expectedPointer=observedPointer;return custody;
  }
  custody.pointerAttempt = { previous: observedPointer, target: custody.writerId, raw };
  session.setItem(pointer, custody.writerId);
  if (session.getItem(pointer) !== custody.writerId || storage.getItem(key) !== raw || session.getItem(pointer) !== custody.writerId || storage.getItem(key) !== raw) throw new Error("The Integration writer pointer or retained data was not confirmed.");
  assertIntegrationReviewOrigins(storage, draft);
  if (session.getItem(pointer) !== custody.writerId || storage.getItem(key) !== raw) throw new Error("The guarded Integration publication changed during its origin check.");
  assertIntegrationReviewOrigins(storage, draft);
  custody.expectedPointer = custody.writerId; delete custody.pointerAttempt; return custody;
}
/** Explicit repair can recover a failed own publication; it cannot adopt a third writer. */
export function repairIntegrationPointer(storage: Pick<Storage, "getItem">, session: Pick<Storage, "getItem">, draft: IntegrationDraft, custody: IntegrationCustody): void {
  const attempt = custody.pointerAttempt; if (!attempt) return;
  if (attempt.target !== custody.writerId || attempt.raw !== custody.expectedRaw) throw new Error("The failed Integration pointer attempt no longer belongs to this exact writer.");
  const key = integrationCopyKey(draft.scope, attempt.target), pointerKey = integrationPointerKey(draft.scope);
  assertIntegrationReviewOrigins(storage, draft);
  decodeIntegrationCopy(attempt.raw, draft.scope, attempt.target);
  if (storage.getItem(key) !== attempt.raw) throw new Error("The Integration copy from the failed pointer publication changed. Its original bytes remain required.");
  const pointer = session.getItem(pointerKey);
  if (pointer !== null && pointer !== attempt.previous && pointer !== attempt.target) throw new Error("Another Integration writer acquired the pointer. Explicit repair preserves that writer.");
  if (storage.getItem(key) !== attempt.raw || session.getItem(pointerKey) !== pointer) throw new Error("The Integration data or pointer changed during explicit pointer repair.");
  assertIntegrationReviewOrigins(storage, draft);
  if (storage.getItem(key) !== attempt.raw || session.getItem(pointerKey) !== pointer) throw new Error("The guarded Integration repair source changed before confirmation.");
  custody.expectedPointer = pointer; delete custody.pointerAttempt;
}
interface IntegrationStagedCopy {custody:IntegrationCustody;draft:IntegrationDraft;raw:string;published?:boolean}
interface IntegrationIntentAttempt {
  before:IntegrationDraft;next:IntegrationDraft;originalWriter:string;originalRaw:string;originalPointer:string|null;
  armed:IntegrationStagedCopy;targets:IntegrationStagedCopy[];
}
/** Live local publication proof only. It never grants IPC, acknowledgement or replay authority. */
const interruptedIntegrationIntents=new WeakMap<IntegrationCustody,IntegrationIntentAttempt>();
export const hasInterruptedIntegrationIntent=(custody:IntegrationCustody)=>interruptedIntegrationIntents.has(custody);
function stagedIntegrationCopy(draft:IntegrationDraft,writerId:string,pointer:string|null):IntegrationStagedCopy {
  return {draft:clone(draft),custody:{writerId,expectedRaw:null,expectedPointer:pointer},
    raw:encodeIntegrationCopy({schema:"motionwright.integration-recovery/1",scope:draft.scope,writerId,updatedAt:Date.now(),draft})};
}
function verifyIntegrationIntentSource(storage:IntegrationStorage,session:Pick<Storage,"getItem">,attempt:IntegrationIntentAttempt):string|null {
  const key=integrationCopyKey(attempt.before.scope,attempt.originalWriter);
  if(storage.getItem(key)!==attempt.originalRaw||storage.getItem(key)!==attempt.originalRaw)
    throw new Error("The original Integration UNKNOWN copy changed. Retry preserves both observations.");
  assertIntegrationReviewOrigins(storage,attempt.before);
  const pointer=session.getItem(integrationPointerKey(attempt.before.scope));
  if(pointer!==attempt.originalPointer){
    const own=attempt.targets.find(target=>target.custody.writerId===pointer&&(target.published||target.custody.pointerAttempt?.target===pointer));
    if(!own||storage.getItem(integrationCopyKey(attempt.before.scope,own.custody.writerId))!==own.raw)
      throw new Error("Another writer changed the Integration recovery pointer. Retry cannot adopt that writer.");
  }
  if(session.getItem(integrationPointerKey(attempt.before.scope))!==pointer||storage.getItem(key)!==attempt.originalRaw)
    throw new Error("Integration source custody changed during Retry.");
  return pointer;
}
function laterIntegrationIntent(attempt:IntegrationIntentAttempt,current:IntegrationDraft):IntegrationDraft {
  const before=attempt.before;
  if(current.scope!==before.scope||current.baseRevision!==before.baseRevision||!sameValue(current.base,before.base)
    ||!sameValue(current.pending,before.pending)||!sameValue(current.reviewRetention,before.reviewRetention))
    throw new Error("The interrupted Integration publication belongs to different source or pending input.");
  const next=clone(attempt.next);
  for(const field of INTEGRATION_FIELDS){
    if(!integer(current.sequences[field])||current.sequences[field]<before.sequences[field]
      ||current.sequences[field]===before.sequences[field]&&!sameValue(current.input[field],before.input[field]))
      throw new Error("The interrupted Integration input sequence changed without its authored edit.");
    if(current.sequences[field]>before.sequences[field]){
      Object.assign(next.input,{[field]:clone(current.input[field])});next.sequences[field]=current.sequences[field];
      if(!next.touched.includes(field))next.touched.push(field);
    }
  }
  const origins=integrationReviewOrigins(before);
  addIntegrationOrigin(origins,recoveryOrigin(attempt.originalWriter,attempt.originalRaw));
  addIntegrationOrigin(origins,recoveryOrigin(attempt.armed.custody.writerId,attempt.armed.raw));
  next.reviewRetention={schema:"motionwright.integration-review-retention/2",origins:[...origins.values()].sort((a,b)=>a.writerId.localeCompare(b.writerId))};
  return next;
}
function verifyStagedIntegrationCopy(storage:IntegrationStorage,session:Pick<Storage,"getItem">,stage:IntegrationStagedCopy,pointer:string|null):void {
  const key=integrationCopyKey(stage.draft.scope,stage.custody.writerId),observed=storage.getItem(key);
  // An exact payload retained in the live proof can be recovered even when its setter threw after storing it.
  if(observed!==stage.custody.expectedRaw&&observed!==stage.raw)throw new Error("The staged Integration copy changed in another window.");
  if(storage.getItem(key)!==observed||session.getItem(integrationPointerKey(stage.draft.scope))!==pointer)
    throw new Error("The staged Integration custody changed during Retry.");
  if(observed===stage.raw)stage.custody.expectedRaw=stage.raw;
  stage.custody.expectedPointer=pointer;
}
export function retryRetainedIntegrationIntent(storage:IntegrationStorage,session:Pick<Storage,"getItem"|"setItem">,current:IntegrationDraft,original:IntegrationCustody):(IntegrationCustody&{draft:IntegrationDraft})|null {
  const attempt=interruptedIntegrationIntents.get(original);if(!attempt)return null;
  let pointer=verifyIntegrationIntentSource(storage,session,attempt);
  const next=laterIntegrationIntent(attempt,current);
  let target=attempt.targets[attempt.targets.length-1];
  if(!target||!sameValue(target.draft,next)){
    if(attempt.targets.length>=INTEGRATION_MAX_COPIES)throw new Error("The interrupted Integration copy inventory exceeds its bound.");
    target=stagedIntegrationCopy(next,crypto.randomUUID(),pointer);attempt.targets.push(target);
  }
  verifyStagedIntegrationCopy(storage,session,attempt.armed,pointer);
  writeIntegrationCopyPhase(storage,session,attempt.armed.draft,attempt.armed.custody,false,attempt.armed.raw);
  pointer=verifyIntegrationIntentSource(storage,session,attempt);
  assertIntegrationReviewOrigins(storage,next);
  verifyStagedIntegrationCopy(storage,session,target,pointer);
  writeIntegrationCopyPhase(storage,session,target.draft,target.custody,true,target.raw);target.published=true;
  verifyIntegrationIntentSource(storage,session,attempt);
  assertIntegrationReviewOrigins(storage,target.draft);
  if(session.getItem(integrationPointerKey(current.scope))!==target.custody.writerId
    ||storage.getItem(integrationCopyKey(current.scope,target.custody.writerId))!==target.raw)
    throw new Error("The separate Integration publication changed before adoption.");
  interruptedIntegrationIntents.delete(original);
  return {...target.custody,draft:clone(target.draft)};
}
export function retainReviewedIntegrationIntent(storage:IntegrationStorage,session:Pick<Storage,"getItem"|"setItem">,before:IntegrationDraft,next:IntegrationDraft,original:IntegrationCustody,writer:string):IntegrationCustody&{draft:IntegrationDraft} {
  if(interruptedIntegrationIntents.has(original))throw new Error("Retry the interrupted Integration local publication before separating another intent.");
  if(!integrationUnknown(before)||next.pending||before.scope!==next.scope||!uuid(writer)||writer===original.writerId||original.expectedRaw===null)
    throw new Error("Invalid Integration recovery fork.");
  const key=integrationCopyKey(before.scope,original.writerId),raw=storage.getItem(key),pointer=session.getItem(integrationPointerKey(before.scope));
  if(raw!==original.expectedRaw||raw===null||!sameValue(decodeIntegrationCopy(raw,before.scope,original.writerId).draft,before)
    ||pointer!==original.expectedPointer&&pointer!==original.writerId)
    throw new Error("Keep the exact earlier Integration attempt before continuing.");
  assertIntegrationReviewOrigins(storage,before);
  const attempt:IntegrationIntentAttempt={before:clone(before),next:clone(next),originalWriter:original.writerId,originalRaw:raw,originalPointer:pointer,
    armed:stagedIntegrationCopy(before,writer,pointer),targets:[]};
  interruptedIntegrationIntents.set(original,attempt);
  return retryRetainedIntegrationIntent(storage,session,before,original)!;
}
