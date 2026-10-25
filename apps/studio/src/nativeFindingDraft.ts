/** Recoverable native-frame review input. Retained source references and local
 * copies are comparison data; only StudioService grants authorize mutations. */
import type { Change, DeliverableProfile, Project, ProjectEvent, RationalTime, Scene } from "./types";
import { addFrameOffset, nativeFindingBody } from "./nativeInspection";
import type { ManualCreativeFinding, NativeInspectionPlan, NativeSampleEvidence } from "./nativeInspection";
import { sameValue } from "./creativeProduction";
import { sameProjectRevision } from "./projectMutation";
import { recoveryOrigin, matchesRecoveryOrigin, isRecoveryOrigin, type RecoveryOrigin } from "./recoveryOrigin";

export interface NativeFindingSource {
  projectId:string; generation:string; branchId:string; revision:number;
  sceneId:string; profileId:string; frameIndex:number; timelineTime:RationalTime;
  pngSha256:string; width:number; height:number; frameRate:NativeInspectionPlan["frameRate"];
}
export interface NativeFindingInput extends ManualCreativeFinding { source:NativeFindingSource|null }
export const NATIVE_FINDING_FIELDS=["severity","confidence","nodeId","violatedConstraint","observation","proposedRepair","source"] as const;
export type NativeFindingField=typeof NATIVE_FINDING_FIELDS[number];
export type NativeFindingChange=Extract<Change,{type:"add_review"}>;
export interface NativeFindingSnapshot { reviewIds:string[] }
export interface NativeFindingPending {
  /** Comparison identity only, never an IPC request ID or replay permission. */
  id:string; revision:number; input:NativeFindingInput; sequences:Record<NativeFindingField,number>;
  change:NativeFindingChange; outcome:"UNKNOWN"|"NOT_SENT"; diagnostic?:string;
}
export interface NativeFindingDraft {
  schema:"motionwright.native-finding-draft/1"; scope:string; baseRevision:number; base:NativeFindingSnapshot;
  input:NativeFindingInput; sequences:Record<NativeFindingField,number>; touched:NativeFindingField[]; pending:NativeFindingPending|null;
  reviewRetention?:NativeFindingReviewRetention;
}
export interface NativeFindingReviewRetention {schema:"motionwright.native-finding-review-retention/2";origins:RecoveryOrigin[]}
const clone=<T,>(value:T):T=>structuredClone(value);
const json=(value:unknown)=>JSON.stringify(value);
const bytes=(value:string)=>new TextEncoder().encode(value).length;
const integer=(value:unknown):value is number=>typeof value==="number"&&Number.isSafeInteger(value)&&value>=0;
const record=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==="object"&&!Array.isArray(value);
const exact=(value:Record<string,unknown>,keys:readonly string[],optional:readonly string[]=[])=>keys.every(key=>Object.hasOwn(value,key))&&Object.keys(value).every(key=>keys.includes(key)||optional.includes(key));
const uuid=(value:unknown):value is string=>typeof value==="string"&&/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);
const strings=(value:unknown):value is string[]=>Array.isArray(value)&&value.every(item=>typeof item==="string");
// Project times are decimal strings; native frame rates are numeric rationals.
const rational=(value:unknown):value is RationalTime=>record(value)&&exact(value,["num","den"])
  &&typeof value.num==="string"&&/^(0|[1-9][0-9]*)$/.test(value.num)
  &&typeof value.den==="string"&&/^[1-9][0-9]*$/.test(value.den);
const frameRate=(value:unknown):value is NativeInspectionPlan["frameRate"]=>record(value)&&exact(value,["num","den"])
  &&integer(value.num)&&integer(value.den)&&value.num>0&&value.den>0&&value.num/value.den>=1&&value.num/value.den<=120;
const sourceShape=(value:unknown):value is NativeFindingSource=>record(value)
  &&exact(value,["projectId","generation","branchId","revision","sceneId","profileId","frameIndex","timelineTime","pngSha256","width","height","frameRate"])
  &&[value.projectId,value.generation,value.branchId,value.sceneId,value.profileId].every(uuid)
  &&integer(value.revision)&&integer(value.frameIndex)&&rational(value.timelineTime)&&frameRate(value.frameRate)
  &&typeof value.pngSha256==="string"&&/^[a-f0-9]{64}$/.test(value.pngSha256)
  &&integer(value.width)&&value.width>0&&integer(value.height)&&value.height>0&&value.width*value.height<=16_777_216;
export const nativeFindingScope=(project:Project)=>json([project.id,project.generation,project.active_branch,"native-finding"]);
export const nativeFindingHasWork=(draft:NativeFindingDraft)=>draft.touched.length>0||draft.pending!==null;
export const nativeFindingUnknown=(draft:NativeFindingDraft)=>draft.pending?.outcome==="UNKNOWN";
const emptyInput=():NativeFindingInput=>({severity:"important",confidence:"probable",nodeId:null,violatedConstraint:"",observation:"",proposedRepair:"",source:null});
export const nativeFindingSnapshot=(project:Project):NativeFindingSnapshot=>({reviewIds:project.reviews.map(review=>review.id)});
function assertScope(draft:NativeFindingDraft,project:Project) {
  if(draft.scope!==nativeFindingScope(project))throw new Error("This finding belongs to another project, generation or branch.");
}
export function createNativeFindingDraft(project:Project):NativeFindingDraft {
  return {schema:"motionwright.native-finding-draft/1",scope:nativeFindingScope(project),baseRevision:project.revision,
    base:nativeFindingSnapshot(project),input:emptyInput(),
    sequences:Object.fromEntries(NATIVE_FINDING_FIELDS.map(field=>[field,0])) as Record<NativeFindingField,number>,touched:[],pending:null};
}
/** Called with the already decoded native sample. No preview token is retained. */
export function nativeFindingSource(project:Project,scene:Scene|null,profile:DeliverableProfile|null,plan:NativeInspectionPlan|null,sample:NativeSampleEvidence|undefined):NativeFindingSource|null {
  if(!scene||!profile||!plan||!sample)return null;
  if(plan.projectId!==project.id||plan.generation!==project.generation||plan.revision!==project.revision
    ||plan.sceneId!==scene.id||plan.profileId!==profile.id||plan.width!==profile.width||plan.height!==profile.height
    ||!project.scenes.some(value=>value.id===scene.id)||!project.deliverables.some(value=>sameValue(value,profile)))return null;
  nativeFindingBody(plan,sample,{severity:"suggestion",confidence:"uncertain",nodeId:null,
    violatedConstraint:"Source reference validation",observation:"Source reference validation",proposedRepair:"Source reference validation"});
  const source={projectId:project.id,generation:project.generation,branchId:project.active_branch,revision:project.revision,
    sceneId:scene.id,profileId:profile.id,frameIndex:sample.frameIndex,timelineTime:clone(sample.timelineTime),pngSha256:sample.pngSha256,
    width:sample.width,height:sample.height,frameRate:clone(plan.frameRate)};
  if(!sourceShape(source))throw new Error("The inspected native source is malformed.");
  return source;
}
export function editNativeFindingField<K extends NativeFindingField>(draft:NativeFindingDraft,field:K,value:NativeFindingInput[K]):NativeFindingDraft {
  if(!NATIVE_FINDING_FIELDS.includes(field)||!integer(draft.sequences[field])||draft.sequences[field]===Number.MAX_SAFE_INTEGER)
    throw new Error("The finding input sequence is invalid or exhausted.");
  if(field==="source"&&nativeFindingUnknown(draft))throw new Error("Read the uncertain review outcome before selecting a separate source.");
  return {...draft,input:{...draft.input,[field]:clone(value)},sequences:{...draft.sequences,[field]:draft.sequences[field]+1},
    touched:draft.touched.includes(field)?draft.touched:[...draft.touched,field]};
}
export function reconcileNativeFindingDraft(draft:NativeFindingDraft,project:Project):NativeFindingDraft {
  assertScope(draft,project);
  if(project.revision<draft.baseRevision||nativeFindingHasWork(draft)||draft.baseRevision===project.revision&&sameValue(draft.base,nativeFindingSnapshot(project)))return draft;
  return {...draft,baseRevision:project.revision,base:nativeFindingSnapshot(project)};
}
export function nativeFindingConflict(draft:NativeFindingDraft,project:Project):boolean {
  assertScope(draft,project);return nativeFindingHasWork(draft)&&(draft.baseRevision!==project.revision||!sameValue(draft.base,nativeFindingSnapshot(project)));
}
export function resolveNativeFindingDraft(draft:NativeFindingDraft,project:Project,choice:"draft"|"empty"):NativeFindingDraft {
  assertScope(draft,project);if(nativeFindingUnknown(draft))throw new Error("Read the unknown review outcome before choosing another source.");
  return {...draft,baseRevision:project.revision,base:nativeFindingSnapshot(project),
    input:choice==="draft"?clone(draft.input):emptyInput(),touched:choice==="draft"?[...draft.touched]:[],pending:null};
}
export function recoverNativeFindingSource(source:NativeFindingDraft,later:NativeFindingDraft,project:Project):NativeFindingDraft {
  assertScope(source,project);assertScope(later,project);
  if(later.pending&&!sameValue(later.pending,source.pending))throw new Error("Two uncertain findings must remain in separate copies.");
  const next=clone(source);
  if(later.reviewRetention){
    if(next.reviewRetention&&!sameValue(next.reviewRetention,later.reviewRetention))throw new Error("Separate finding origins cannot replace each other.");
    next.reviewRetention=clone(later.reviewRetention);
  }
  for(const field of later.touched){
    if(field==="source"&&nativeFindingUnknown(source)&&!sameValue(later.input.source,source.input.source))
      throw new Error("The unreadable earlier attempt has another source. Download both inputs before choosing their custody.");
    const sequence=Math.max(next.sequences[field],later.sequences[field])+1;
    if(!integer(sequence))throw new Error("The finding input sequence is exhausted; both copies were kept.");
    Object.assign(next.input,{[field]:clone(later.input[field])});next.sequences[field]=sequence;
  }
  next.touched=[...new Set([...next.touched,...later.touched])];return reconcileNativeFindingDraft(next,project);
}
/** Serialize the same reference-only review schema as nativeFindingBody. */
function bodyFromInput(input:NativeFindingInput):string {
  const source=input.source;if(!source||!sourceShape(source))throw new Error("Select a current inspected frame for this finding.");
  const {source:_,...finding}=input;
  if(!["blocking","important","suggestion"].includes(finding.severity)||!["certain","probable","uncertain"].includes(finding.confidence)
    ||finding.nodeId!==null&&!uuid(finding.nodeId))throw new Error("The authored finding choices are invalid.");
  // nativeFindingBody consumes only frame/time from samples. Tokens are never
  // reconstructed or used for native access by an author-input recovery file.
  const plan={projectId:source.projectId,generation:source.generation,revision:source.revision,sceneId:source.sceneId,
    profileId:source.profileId,width:source.width,height:source.height,frameRate:source.frameRate,
    samples:[{frameIndex:source.frameIndex,timelineTime:source.timelineTime}]} as NativeInspectionPlan;
  return nativeFindingBody(plan,source,finding);
}
function changeFromInput(input:NativeFindingInput):NativeFindingChange {
  const body=bodyFromInput(input),source=input.source!;
  return {type:"add_review",kind:"creative",resource:"scene:"+source.sceneId,body,start:clone(source.timelineTime),
    end:addFrameOffset(source.timelineTime,1,source.frameRate),locale:null,profile_id:source.profileId};
}
export function nativeFindingCurrent(draft:NativeFindingDraft,project:Project,currentSource:NativeFindingSource|null):boolean {
  const source=draft.input.source;
  return !!source&&!!currentSource&&sourceShape(source)&&sameValue(source,currentSource)
    &&source.projectId===project.id&&source.generation===project.generation&&source.branchId===project.active_branch&&source.revision===project.revision
    &&project.scenes.some(scene=>scene.id===source.sceneId&&(draft.input.nodeId===null||scene.nodes.some(node=>node.id===draft.input.nodeId)));
}
export interface NativeFindingSubmission {draft:NativeFindingDraft;change:NativeFindingChange}
const liveSubmissions=new WeakMap<NativeFindingSubmission,{sealed:string;dispatched:boolean;settled:boolean}>();
export function prepareNativeFindingSubmission(draft:NativeFindingDraft,project:Project,currentSource:NativeFindingSource|null,id:string):NativeFindingSubmission {
  assertScope(draft,project);
  if(!uuid(id)||nativeFindingUnknown(draft)||nativeFindingConflict(draft,project)||!nativeFindingCurrent(draft,project,currentSource))
    throw new Error("Use the current inspected frame and review any changed source or uncertain attempt before saving separately.");
  const change=changeFromInput(draft.input);
  const next:NativeFindingDraft={...draft,pending:{id,revision:project.revision,input:clone(draft.input),
    sequences:clone(draft.sequences),change:clone(change),outcome:"UNKNOWN"}};
  const submission={draft:next,change};liveSubmissions.set(submission,{sealed:json(submission),dispatched:false,settled:false});return submission;
}
export function nativeFindingPreparationMatches(before:NativeFindingDraft,source:Project,current:NativeFindingDraft,project:Project):boolean {
  return sameProjectRevision(source,project)&&sameValue(nativeFindingSnapshot(source),nativeFindingSnapshot(project))
    &&before.scope===current.scope&&sameValue(before.input,current.input)&&sameValue(before.sequences,current.sequences)&&sameValue(before.pending,current.pending);
}
export function dispatchNativeFindingSubmission(submission:NativeFindingSubmission,draft:NativeFindingDraft,project:Project):void {
  const proof=liveSubmissions.get(submission);
  if(!proof||proof.dispatched||proof.settled||proof.sealed!==json(submission)||draft.scope!==nativeFindingScope(project)
    ||draft.baseRevision!==project.revision||!sameValue(submission.draft,draft)||!sameValue(draft.base,nativeFindingSnapshot(project)))
    throw new Error("Only the prepared original finding request can be dispatched once.");
  proof.dispatched=true;
}
function nativeFindingValuesMatch(draft:NativeFindingDraft,project:Project):boolean {
  const pending=draft.pending;if(!pending)return false;const change=pending.change;
  const reviews=project.reviews.filter(value=>!draft.base.reviewIds.includes(value.id));
  return reviews.length===1&&reviews.some(value=>value.kind==="creative"&&value.status==="open"&&value.resolution===null
    &&value.body===change.body&&value.anchor.resource===change.resource&&value.anchor.branch_id===project.active_branch
    &&value.anchor.revision===pending.revision&&sameValue(value.anchor.start,change.start)&&sameValue(value.anchor.end,change.end)
    &&value.anchor.locale===change.locale&&value.anchor.profile_id===change.profile_id);
}
export function acknowledgeNativeFindingSubmission(draft:NativeFindingDraft,submission:NativeFindingSubmission,receipt:Project):NativeFindingDraft {
  const proof=liveSubmissions.get(submission),pending=draft.pending;
  if(!proof||!proof.dispatched||proof.settled||proof.sealed!==json(submission)||!pending||pending.id!==submission.draft.pending?.id
    ||!sameValue(pending.change,submission.change)||!receipt||nativeFindingScope(receipt)!==draft.scope||receipt.revision!==pending.revision+1||!nativeFindingValuesMatch(draft,receipt))
    throw new Error("The original response did not confirm this exact source-anchored review. Input and the uncertain attempt were retained.");
  proof.settled=true;
  // Keep all six raw values and their exact source, including later edits.
  return {...draft,baseRevision:receipt.revision,base:nativeFindingSnapshot(receipt),input:clone(draft.input),touched:[...draft.touched],pending:null};
}
export function retainNativeFindingFailure(draft:NativeFindingDraft,id:string,diagnostic:string,notSent=false):NativeFindingDraft {
  return draft.pending?.id===id?{...draft,pending:{...draft.pending,diagnostic,outcome:notSent?"NOT_SENT":"UNKNOWN"}}:draft;
}
export function settleNativeFindingFailure(submission:NativeFindingSubmission):void {const proof=liveSubmissions.get(submission);if(proof)proof.settled=true;}
export interface NativeFindingOutcomeTransport {readProject(id:string):Promise<Project>;readHistory(project:Project,after:number,limit:number):Promise<ProjectEvent[]>}
export interface NativeFindingOutcomeReview {scope:string;pending:NativeFindingPending;input:NativeFindingInput;sequences:Record<NativeFindingField,number>;project:Project;events:ProjectEvent[];matchingValueRevisions:number[]}
const inspectedOutcomes=new WeakMap<NativeFindingOutcomeReview,string>();
const sameObserved=(a:Project,b:Project)=>sameProjectRevision(a,b)&&nativeFindingScope(a)===nativeFindingScope(b)&&sameValue(a,b);
export function nativeFindingOutcomeMatches(draft:NativeFindingDraft,project:Project,review:NativeFindingOutcomeReview|null):boolean {
  return !!review&&inspectedOutcomes.get(review)===json(review)&&review.scope===draft.scope&&nativeFindingScope(project)===draft.scope
    &&sameObserved(project,review.project)&&sameValue(draft.pending,review.pending)&&sameValue(draft.input,review.input)&&sameValue(draft.sequences,review.sequences);
}
export async function inspectNativeFindingOutcome(draft:NativeFindingDraft,selected:Project,transport:NativeFindingOutcomeTransport):Promise<NativeFindingOutcomeReview> {
  assertScope(draft,selected);if(!nativeFindingUnknown(draft)||!draft.pending)throw new Error("There is no uncertain finding attempt to inspect.");
  const sealed=json(draft),pending=clone(draft.pending),first=await transport.readProject(selected.id);
  if(nativeFindingScope(first)!==draft.scope||first.revision<pending.revision||first.revision-pending.revision>2048)throw new Error("The saved review revision is unavailable or outside bounded history.");
  const events=await transport.readHistory(first,pending.revision,2048);
  if(events.length!==first.revision-pending.revision||events.some((event,index)=>event.revision!==pending.revision+index+1))
    throw new Error("Review history is incomplete or not consecutive. The attempt remains unknown.");
  const last=await transport.readProject(selected.id);
  if(sealed!==json(draft)||!sameObserved(first,last)||!sameObserved(selected,last))throw new Error("The saved source changed during inspection. Reload and read it again.");
  const review:NativeFindingOutcomeReview={scope:draft.scope,pending,input:clone(draft.input),sequences:clone(draft.sequences),project:clone(last),events:clone(events),
    matchingValueRevisions:events.filter(event=>sameValue(event.change,pending.change)).map(event=>event.revision)};
  inspectedOutcomes.set(review,json(review));return review;
}
export function releaseNativeFindingOutcome(draft:NativeFindingDraft,project:Project,review:NativeFindingOutcomeReview):NativeFindingDraft {
  if(!nativeFindingOutcomeMatches(draft,project,review))throw new Error("Read this exact finding input and current source before separating the attempt.");
  return {...draft,baseRevision:project.revision,base:nativeFindingSnapshot(project),input:clone(draft.input),touched:[...draft.touched],pending:null};
}

export const NATIVE_FINDING_COPY_PREFIX = "motionwright.native-finding-recovery.v1:";
export const NATIVE_FINDING_POINTER_PREFIX = "motionwright.native-finding-writer.v1:";
export const NATIVE_FINDING_MAX_COPY = 1024 * 1024, NATIVE_FINDING_MAX_TOTAL = 4 * 1024 * 1024, NATIVE_FINDING_MAX_COPIES = 64;
export const nativeFindingCopyKey = (scope: string, writer: string) => NATIVE_FINDING_COPY_PREFIX + encodeURIComponent(scope) + ":" + writer;
export const nativeFindingPointerKey = (scope: string) => NATIVE_FINDING_POINTER_PREFIX + encodeURIComponent(scope);
export type NativeFindingStorage = Pick<Storage, "length" | "key" | "getItem" | "setItem" | "removeItem">;
export interface NativeFindingCustody {
  writerId: string; expectedRaw: string | null; expectedPointer: string | null;
  /** Live proof of this writer's attempted pointer publication, never restored from a copy. */
  pointerAttempt?: { previous: string | null; target: string; raw: string };
}
export interface NativeFindingCopy { schema: "motionwright.native-finding-recovery/1"; scope: string; writerId: string; updatedAt: number; draft: NativeFindingDraft }
export interface NativeFindingRecoveryEntry { key: string; writerId: string; raw: string; value: NativeFindingCopy | null }
/** Live observation of the earlier recovery source, including failed reads.
 * Listing a known writer can supply its first bytes, never replace them. */
export interface NativeFindingRecoveryObservation {
  pointerObserved: boolean; pointer: string | null; rawObserved: boolean; raw: string | null;
}
function inputShape(value:unknown):value is NativeFindingInput {
  return record(value)&&exact(value,NATIVE_FINDING_FIELDS)
    &&["blocking","important","suggestion"].includes(String(value.severity))
    &&["certain","probable","uncertain"].includes(String(value.confidence))
    &&(value.nodeId===null||uuid(value.nodeId))&&(value.source===null||sourceShape(value.source))
    &&["violatedConstraint","observation","proposedRepair"].every(field=>typeof value[field]==="string"&&bytes(value[field] as string)<=64*1024);
}
function snapshotShape(value:unknown):value is NativeFindingSnapshot {
  return record(value)&&exact(value,["reviewIds"])&&strings(value.reviewIds)&&value.reviewIds.length<=4096
    &&value.reviewIds.every(uuid)&&new Set(value.reviewIds).size===value.reviewIds.length;
}
const fieldsShape=(value:unknown):value is NativeFindingField[]=>strings(value)&&new Set(value).size===value.length&&value.every(field=>NATIVE_FINDING_FIELDS.includes(field as NativeFindingField));
const sequencesShape=(value:unknown):value is Record<NativeFindingField,number>=>record(value)&&exact(value,NATIVE_FINDING_FIELDS)&&NATIVE_FINDING_FIELDS.every(field=>integer(value[field]));
function changeShape(value:unknown,input:NativeFindingInput):value is NativeFindingChange {
  try{return record(value)&&exact(value,["type","kind","resource","body","start","end","locale","profile_id"])&&sameValue(value,changeFromInput(input));}
  catch{return false;}
}
function cleanDraft(value:unknown,scope:string,depth=0):NativeFindingDraft {
  const identity:unknown=JSON.parse(scope);
  if(!Array.isArray(identity)||identity.length!==4||!identity.slice(0,3).every(uuid)||identity[3]!=="native-finding"
    ||!record(value)||!exact(value,["schema","scope","baseRevision","base","input","sequences","touched","pending"],["reviewRetention"])
    ||value.schema!=="motionwright.native-finding-draft/1"||value.scope!==scope||!integer(value.baseRevision)||!snapshotShape(value.base)
    ||!inputShape(value.input)||!sequencesShape(value.sequences)||!fieldsShape(value.touched))
    throw new Error("The native finding recovery has an unsupported shape or owner.");
  const source=value.input.source;
  if(source&&(source.projectId!==identity[0]||source.generation!==identity[1]||source.branchId!==identity[2]||source.revision>value.baseRevision))
    throw new Error("The finding source belongs to another project, generation, branch or future revision.");
  let pending:NativeFindingPending|null=null;
  if(value.pending!==null){
    const sent=value.pending;
    if(!record(sent)||!exact(sent,["id","revision","input","sequences","change","outcome"],["diagnostic"])
      ||!uuid(sent.id)||!integer(sent.revision)||sent.revision!==value.baseRevision||!inputShape(sent.input)||!sequencesShape(sent.sequences)
      ||!NATIVE_FINDING_FIELDS.every(field=>(sent.sequences as Record<NativeFindingField,number>)[field]<=(value.sequences as Record<NativeFindingField,number>)[field])
      ||!sent.input.source||sent.input.source.revision!==sent.revision||sent.input.source.projectId!==identity[0]
      ||sent.input.source.generation!==identity[1]||sent.input.source.branchId!==identity[2]
      ||!changeShape(sent.change,sent.input)||!["UNKNOWN","NOT_SENT"].includes(String(sent.outcome))
      ||sent.diagnostic!==undefined&&typeof sent.diagnostic!=="string")
      throw new Error("The pending finding comparison is unreadable.");
    pending={...clone(sent),outcome:"UNKNOWN"} as unknown as NativeFindingPending;
  }
  const reviewRetention=value.reviewRetention===undefined?undefined:cleanReviewRetention(value.reviewRetention,scope,depth);
  return {schema:"motionwright.native-finding-draft/1",scope,baseRevision:value.baseRevision,base:clone(value.base),
    input:clone(value.input),sequences:clone(value.sequences),touched:[...value.touched],pending,...(reviewRetention?{reviewRetention}:{})};
}

export function decodeNativeFindingCopy(raw: string, scope: string, writer: string): NativeFindingCopy {
  return decodeNativeFindingCopyAtDepth(raw, scope, writer, 0);
}
function observeNativeFindingRecoveryRaw(observed: NativeFindingRecoveryObservation, writer: string, raw: string | null): void {
  if (!observed.pointerObserved || observed.pointer !== writer) return;
  if (observed.rawObserved && observed.raw !== raw)
    throw new Error("The earlier NativeFinding recovery bytes changed. Retry cannot adopt a replacement source; the observed original stays available in recovery copies.");
  observed.raw = raw; observed.rawObserved = true;
}
/** Read only. Keep every identity and byte observed before a read or validation fails. */
export function readNativeFindingRecoverySource(storage: Pick<NativeFindingStorage, "getItem">, pointers: Pick<Storage, "getItem">,
  scope: string, observed: NativeFindingRecoveryObservation): NativeFindingDraft | null {
  const pointer = pointers.getItem(nativeFindingPointerKey(scope));
  if (observed.pointerObserved && pointer !== observed.pointer)
    throw new Error("The earlier NativeFinding recovery pointer changed. Retry cannot adopt another writer.");
  observed.pointer = pointer; observed.pointerObserved = true;
  let draft: NativeFindingDraft | null = null;
  if (pointer !== null) {
    if (!uuid(pointer)) throw new Error("The earlier NativeFinding recovery pointer is unreadable; its original value was kept.");
    const raw = storage.getItem(nativeFindingCopyKey(scope, pointer));
    observeNativeFindingRecoveryRaw(observed, pointer, raw);
    if (raw === null) throw new Error("The earlier NativeFinding recovery copy is missing. Its pointer was kept.");
    draft = decodeNativeFindingCopy(raw, scope, pointer).draft;
    assertNativeFindingReviewOrigins(storage, draft);
  }
  assertNativeFindingRecoverySource(storage, pointers, scope, observed);
  return draft;
}
/** Confirm the same earlier source immediately before adopting its recovered input. */
export function assertNativeFindingRecoverySource(storage: Pick<NativeFindingStorage, "getItem">, pointers: Pick<Storage, "getItem">,
  scope: string, observed: NativeFindingRecoveryObservation): void {
  const pointerKey = nativeFindingPointerKey(scope);
  if (!observed.pointerObserved || pointers.getItem(pointerKey) !== observed.pointer)
    throw new Error("The earlier NativeFinding recovery pointer changed before adoption.");
  if (observed.pointer !== null && (!observed.rawObserved || observed.raw === null
    || storage.getItem(nativeFindingCopyKey(scope, observed.pointer)) !== observed.raw))
    throw new Error("The original NativeFinding recovery bytes are not confirmed. Both observations remain separate.");
  if (pointers.getItem(pointerKey) !== observed.pointer)
    throw new Error("The earlier NativeFinding pointer changed while its source was checked.");
}
function decodeNativeFindingCopyAtDepth(raw: string, scope: string, writer: string, depth: number): NativeFindingCopy {
  if (depth > NATIVE_FINDING_MAX_COPIES) throw new Error("The NativeFinding review origin chain exceeds its bound.");
  if (bytes(raw) > NATIVE_FINDING_MAX_COPY) throw new Error("NativeFinding recovery exceeds 1 MiB. Download the complete input before reducing it.");
  const value: unknown = JSON.parse(raw);
  if (!record(value) || !exact(value, ["schema", "scope", "writerId", "updatedAt", "draft"]) || value.schema !== "motionwright.native-finding-recovery/1"
    || value.scope !== scope || value.writerId !== writer || !uuid(writer) || !integer(value.updatedAt)) throw new Error("This NativeFinding copy is unreadable or belongs to another writer.");
  const draft = cleanDraft(value.draft, scope, depth);
  if (nativeFindingReviewOrigins(draft).has(writer)) throw new Error("An NativeFinding successor cannot be its own review origin.");
  return { schema: "motionwright.native-finding-recovery/1", scope, writerId: writer, updatedAt: value.updatedAt, draft };
}
function addNativeFindingOrigin(origins:Map<string,RecoveryOrigin>,origin:RecoveryOrigin):void {
  const previous=origins.get(origin.writerId);
  if(previous&&!sameValue(previous,origin))throw new Error("Different NativeFinding review observations refer to the same writer.");
  origins.set(origin.writerId,clone(origin));
  if(origins.size>NATIVE_FINDING_MAX_COPIES)throw new Error("The NativeFinding review origin inventory exceeds its bound.");
}
function cleanReviewRetention(value:unknown,_scope:string,_depth=0):NativeFindingReviewRetention {
  if(!record(value)||!exact(value,["schema","origins"])||value.schema!=="motionwright.native-finding-review-retention/2"
    ||!Array.isArray(value.origins)||value.origins.length<2||value.origins.length>NATIVE_FINDING_MAX_COPIES||!value.origins.every(isRecoveryOrigin))
    throw new Error("Invalid flat native finding review origin condition.");
  const origins=new Map<string,RecoveryOrigin>();
  for(const origin of value.origins){if(origins.has(origin.writerId))throw new Error("Duplicate finding review origin.");addNativeFindingOrigin(origins,origin);}
  return {schema:"motionwright.native-finding-review-retention/2",origins:[...origins.values()].sort((a,b)=>a.writerId.localeCompare(b.writerId))};
}
export function nativeFindingReviewOrigins(draft:NativeFindingDraft):Map<string,RecoveryOrigin> {
  if(!draft.reviewRetention)return new Map();
  const retained=cleanReviewRetention(draft.reviewRetention,draft.scope);
  if(retained.schema!=="motionwright.native-finding-review-retention/2")throw new Error("Invalid normalized NativeFinding origin condition.");
  return new Map(retained.origins.map(origin=>[origin.writerId,origin]));
}
export function assertNativeFindingReviewOrigins(storage:Pick<Storage,"getItem">,draft:NativeFindingDraft):void {
  const origins=nativeFindingReviewOrigins(draft);
  for(const origin of origins.values()){
    const key=nativeFindingCopyKey(draft.scope,origin.writerId),raw=storage.getItem(key);
    if(raw===null||!matchesRecoveryOrigin(origin,raw)||storage.getItem(key)!==raw)
      throw new Error("A reviewed NativeFinding origin changed. Its UNKNOWN copy and current input remain separate; verify their original custody before saving.");
    const observed=decodeNativeFindingCopy(raw,draft.scope,origin.writerId).draft;
    if(!nativeFindingUnknown(observed))throw new Error("An NativeFinding origin no longer retains its unknown attempt.");
    for(const inherited of nativeFindingReviewOrigins(observed).values())
      if(!sameValue(origins.get(inherited.writerId),inherited))throw new Error("An earlier NativeFinding review origin is missing or changed.");
    if(storage.getItem(key)!==raw)throw new Error("A reviewed NativeFinding origin changed during its custody check.");
  }
}
export function encodeNativeFindingCopy(copy: NativeFindingCopy): string { const raw = json(copy); decodeNativeFindingCopy(raw, copy.scope, copy.writerId); return raw; }
/** Complete author-input salvage, including invalid/oversized input. This file
 * cannot be decoded as a recovery copy, nativeFinding manifest or live submission. */
export function encodeNativeFindingFile(draft: NativeFindingDraft): string {
  return JSON.stringify({schema:"motionwright.native-finding-input-salvage/1",exportedAt:Date.now(),
    authority:"author_input_only",draft:clone(draft)},null,2);
}
export function readNativeFindingCopies(storage: NativeFindingStorage, scope: string, observed?: NativeFindingRecoveryObservation): { entries: NativeFindingRecoveryEntry[]; limited: boolean } {
  if (storage.length > 4096) throw new Error("The local NativeFinding recovery index exceeds its inspection bound.");
  const prefix = NATIVE_FINDING_COPY_PREFIX + encodeURIComponent(scope) + ":", entries: NativeFindingRecoveryEntry[] = []; let limited = false;
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i); if (!key?.startsWith(prefix)) continue;
    if (entries.length >= NATIVE_FINDING_MAX_COPIES) { limited = true; continue; }
    const writerId = key.slice(prefix.length), raw = storage.getItem(key);
    // Pin this first read before decoding or inspecting any later entry.
    if (observed) observeNativeFindingRecoveryRaw(observed, writerId, raw);
    if (raw === null) continue;
    let value: NativeFindingCopy | null = null;
    try { value = decodeNativeFindingCopy(raw, scope, writerId); } catch { /* Preserve exact unreadable bytes. */ }
    entries.push({ key, writerId, raw, value });
  }
  return { entries, limited };
}
export function writeNativeFindingCopy(storage:NativeFindingStorage,session:Pick<Storage,"getItem"|"setItem">,draft:NativeFindingDraft,custody:NativeFindingCustody):NativeFindingCustody {
  if(interruptedNativeFindingIntents.has(custody))throw new Error("A separate NativeFinding copy publication is incomplete. Retry that local publication before replacing its original bytes.");
  return writeNativeFindingCopyPhase(storage,session,draft,custody,true);
}
function writeNativeFindingCopyPhase(storage: NativeFindingStorage, session: Pick<Storage, "getItem" | "setItem">, draft: NativeFindingDraft, custody: NativeFindingCustody,publishPointer:boolean,fixedRaw?:string): NativeFindingCustody {
  const key = nativeFindingCopyKey(draft.scope, custody.writerId), pointer = nativeFindingPointerKey(draft.scope);
  const check = () => {
    const observed = session.getItem(pointer);
    if (observed !== custody.expectedPointer && observed !== custody.writerId) throw new Error("Another writer changed the NativeFinding recovery pointer. Earlier copies were kept.");
    if (storage.getItem(key) !== custody.expectedRaw) throw new Error("The NativeFinding copy changed in another window. Original bytes were kept.");
    return observed;
  };
  const observedPointer = check(); assertNativeFindingReviewOrigins(storage, draft);
  if (custody.expectedRaw !== null && (!publishPointer || observedPointer === custody.writerId) && sameValue(decodeNativeFindingCopy(custody.expectedRaw, draft.scope, custody.writerId).draft, draft)) {
    if (check() !== observedPointer) throw new Error("The nativeFinding recovery pointer changed during verification.");
    assertNativeFindingReviewOrigins(storage, draft);
    if (check() !== observedPointer) throw new Error("The nativeFinding recovery changed during origin verification.");
    custody.expectedPointer = observedPointer; if(publishPointer)delete custody.pointerAttempt; return custody;
  }
  const raw = fixedRaw ?? encodeNativeFindingCopy({ schema: "motionwright.native-finding-recovery/1", scope: draft.scope, writerId: custody.writerId, updatedAt: Date.now(), draft });
  if(fixedRaw!==undefined&&!sameValue(decodeNativeFindingCopy(raw,draft.scope,custody.writerId).draft,draft))throw new Error("The prepared NativeFinding copy does not match its exact input.");
  if (custody.expectedRaw !== null) {
    const old = decodeNativeFindingCopy(custody.expectedRaw, draft.scope, custody.writerId).draft;
    if (!nativeFindingHasWork(old) && !sameValue(old, draft)) throw new Error("A clean NativeFinding checkpoint is immutable; use a separate writer.");
  }
  let total = bytes(key) + bytes(raw), count = 1;
  for (let i = 0; i < storage.length; i++) {
    const other = storage.key(i); if (!other?.startsWith(NATIVE_FINDING_COPY_PREFIX) || other === key) continue;
    count++; total += bytes(other) + bytes(storage.getItem(other) ?? "");
  }
  if (count > NATIVE_FINDING_MAX_COPIES || total > NATIVE_FINDING_MAX_TOTAL) throw new Error("NativeFinding recovery is full (64 copies / 4 MiB). Explicitly discard only copies no longer needed.");
  if (check() !== observedPointer) throw new Error("The NativeFinding recovery pointer changed during its inventory check.");
  assertNativeFindingReviewOrigins(storage, draft); storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) throw new Error("The NativeFinding recovery data write was not confirmed.");
  custody.expectedRaw = raw;
  if (session.getItem(pointer) !== observedPointer) throw new Error("The NativeFinding recovery pointer changed during data publication. The foreign pointer was kept.");
  assertNativeFindingReviewOrigins(storage, draft);
  if(!publishPointer){
    if(session.getItem(pointer)!==observedPointer||storage.getItem(key)!==raw)throw new Error("The staged NativeFinding copy changed before verification.");
    assertNativeFindingReviewOrigins(storage,draft);
    if(session.getItem(pointer)!==observedPointer||storage.getItem(key)!==raw)throw new Error("The staged NativeFinding custody changed during verification.");
    custody.expectedPointer=observedPointer;return custody;
  }
  custody.pointerAttempt = { previous: observedPointer, target: custody.writerId, raw };
  session.setItem(pointer, custody.writerId);
  if (session.getItem(pointer) !== custody.writerId || storage.getItem(key) !== raw || session.getItem(pointer) !== custody.writerId || storage.getItem(key) !== raw) throw new Error("The NativeFinding writer pointer or retained data was not confirmed.");
  assertNativeFindingReviewOrigins(storage, draft);
  if (session.getItem(pointer) !== custody.writerId || storage.getItem(key) !== raw) throw new Error("The guarded NativeFinding publication changed during its origin check.");
  assertNativeFindingReviewOrigins(storage, draft);
  custody.expectedPointer = custody.writerId; delete custody.pointerAttempt; return custody;
}
/** Explicit repair can recover a failed own publication; it cannot adopt a third writer. */
export function repairNativeFindingPointer(storage: Pick<Storage, "getItem">, session: Pick<Storage, "getItem">, draft: NativeFindingDraft, custody: NativeFindingCustody): void {
  const attempt = custody.pointerAttempt; if (!attempt) return;
  if (attempt.target !== custody.writerId || attempt.raw !== custody.expectedRaw) throw new Error("The failed NativeFinding pointer attempt no longer belongs to this exact writer.");
  const key = nativeFindingCopyKey(draft.scope, attempt.target), pointerKey = nativeFindingPointerKey(draft.scope);
  assertNativeFindingReviewOrigins(storage, draft);
  decodeNativeFindingCopy(attempt.raw, draft.scope, attempt.target);
  if (storage.getItem(key) !== attempt.raw) throw new Error("The NativeFinding copy from the failed pointer publication changed. Its original bytes remain required.");
  const pointer = session.getItem(pointerKey);
  if (pointer !== null && pointer !== attempt.previous && pointer !== attempt.target) throw new Error("Another NativeFinding writer acquired the pointer. Explicit repair preserves that writer.");
  if (storage.getItem(key) !== attempt.raw || session.getItem(pointerKey) !== pointer) throw new Error("The NativeFinding data or pointer changed during explicit pointer repair.");
  assertNativeFindingReviewOrigins(storage, draft);
  if (storage.getItem(key) !== attempt.raw || session.getItem(pointerKey) !== pointer) throw new Error("The guarded NativeFinding repair source changed before confirmation.");
  custody.expectedPointer = pointer; delete custody.pointerAttempt;
}
interface NativeFindingStagedCopy {custody:NativeFindingCustody;draft:NativeFindingDraft;raw:string;published?:boolean}
interface NativeFindingIntentAttempt {
  before:NativeFindingDraft;next:NativeFindingDraft;originalWriter:string;originalRaw:string;originalPointer:string|null;
  armed:NativeFindingStagedCopy;targets:NativeFindingStagedCopy[];
}
/** Live local publication proof only. It never grants IPC, acknowledgement or replay authority. */
const interruptedNativeFindingIntents=new WeakMap<NativeFindingCustody,NativeFindingIntentAttempt>();
export const hasInterruptedNativeFindingIntent=(custody:NativeFindingCustody)=>interruptedNativeFindingIntents.has(custody);
function stagedNativeFindingCopy(draft:NativeFindingDraft,writerId:string,pointer:string|null):NativeFindingStagedCopy {
  return {draft:clone(draft),custody:{writerId,expectedRaw:null,expectedPointer:pointer},
    raw:encodeNativeFindingCopy({schema:"motionwright.native-finding-recovery/1",scope:draft.scope,writerId,updatedAt:Date.now(),draft})};
}
function verifyNativeFindingIntentSource(storage:NativeFindingStorage,session:Pick<Storage,"getItem">,attempt:NativeFindingIntentAttempt):string|null {
  const key=nativeFindingCopyKey(attempt.before.scope,attempt.originalWriter);
  if(storage.getItem(key)!==attempt.originalRaw||storage.getItem(key)!==attempt.originalRaw)
    throw new Error("The original NativeFinding UNKNOWN copy changed. Retry preserves both observations.");
  assertNativeFindingReviewOrigins(storage,attempt.before);
  const pointer=session.getItem(nativeFindingPointerKey(attempt.before.scope));
  if(pointer!==attempt.originalPointer){
    const own=attempt.targets.find(target=>target.custody.writerId===pointer&&(target.published||target.custody.pointerAttempt?.target===pointer));
    if(!own||storage.getItem(nativeFindingCopyKey(attempt.before.scope,own.custody.writerId))!==own.raw)
      throw new Error("Another writer changed the NativeFinding recovery pointer. Retry cannot adopt that writer.");
  }
  if(session.getItem(nativeFindingPointerKey(attempt.before.scope))!==pointer||storage.getItem(key)!==attempt.originalRaw)
    throw new Error("NativeFinding source custody changed during Retry.");
  return pointer;
}
function laterNativeFindingIntent(attempt:NativeFindingIntentAttempt,current:NativeFindingDraft):NativeFindingDraft {
  const before=attempt.before;
  if(current.scope!==before.scope||current.baseRevision!==before.baseRevision||!sameValue(current.base,before.base)
    ||!sameValue(current.pending,before.pending)||!sameValue(current.reviewRetention,before.reviewRetention))
    throw new Error("The interrupted NativeFinding publication belongs to different source or pending input.");
  const next=clone(attempt.next);
  for(const field of NATIVE_FINDING_FIELDS){
    if(!integer(current.sequences[field])||current.sequences[field]<before.sequences[field]
      ||current.sequences[field]===before.sequences[field]&&!sameValue(current.input[field],before.input[field]))
      throw new Error("The interrupted NativeFinding input sequence changed without its authored edit.");
    if(current.sequences[field]>before.sequences[field]){
      Object.assign(next.input,{[field]:clone(current.input[field])});next.sequences[field]=current.sequences[field];
      if(!next.touched.includes(field))next.touched.push(field);
    }
  }
  const origins=nativeFindingReviewOrigins(before);
  addNativeFindingOrigin(origins,recoveryOrigin(attempt.originalWriter,attempt.originalRaw));
  addNativeFindingOrigin(origins,recoveryOrigin(attempt.armed.custody.writerId,attempt.armed.raw));
  next.reviewRetention={schema:"motionwright.native-finding-review-retention/2",origins:[...origins.values()].sort((a,b)=>a.writerId.localeCompare(b.writerId))};
  return next;
}
function verifyStagedNativeFindingCopy(storage:NativeFindingStorage,session:Pick<Storage,"getItem">,stage:NativeFindingStagedCopy,pointer:string|null):void {
  const key=nativeFindingCopyKey(stage.draft.scope,stage.custody.writerId),observed=storage.getItem(key);
  // An exact payload retained in the live proof can be recovered even when its setter threw after storing it.
  if(observed!==stage.custody.expectedRaw&&observed!==stage.raw)throw new Error("The staged NativeFinding copy changed in another window.");
  if(storage.getItem(key)!==observed||session.getItem(nativeFindingPointerKey(stage.draft.scope))!==pointer)
    throw new Error("The staged NativeFinding custody changed during Retry.");
  if(observed===stage.raw)stage.custody.expectedRaw=stage.raw;
  stage.custody.expectedPointer=pointer;
}
export function retryRetainedNativeFindingIntent(storage:NativeFindingStorage,session:Pick<Storage,"getItem"|"setItem">,current:NativeFindingDraft,original:NativeFindingCustody):(NativeFindingCustody&{draft:NativeFindingDraft})|null {
  const attempt=interruptedNativeFindingIntents.get(original);if(!attempt)return null;
  let pointer=verifyNativeFindingIntentSource(storage,session,attempt);
  const next=laterNativeFindingIntent(attempt,current);
  let target=attempt.targets[attempt.targets.length-1];
  if(!target||!sameValue(target.draft,next)){
    if(attempt.targets.length>=NATIVE_FINDING_MAX_COPIES)throw new Error("The interrupted NativeFinding copy inventory exceeds its bound.");
    target=stagedNativeFindingCopy(next,crypto.randomUUID(),pointer);attempt.targets.push(target);
  }
  verifyStagedNativeFindingCopy(storage,session,attempt.armed,pointer);
  writeNativeFindingCopyPhase(storage,session,attempt.armed.draft,attempt.armed.custody,false,attempt.armed.raw);
  pointer=verifyNativeFindingIntentSource(storage,session,attempt);
  assertNativeFindingReviewOrigins(storage,next);
  verifyStagedNativeFindingCopy(storage,session,target,pointer);
  writeNativeFindingCopyPhase(storage,session,target.draft,target.custody,true,target.raw);target.published=true;
  verifyNativeFindingIntentSource(storage,session,attempt);
  assertNativeFindingReviewOrigins(storage,target.draft);
  if(session.getItem(nativeFindingPointerKey(current.scope))!==target.custody.writerId
    ||storage.getItem(nativeFindingCopyKey(current.scope,target.custody.writerId))!==target.raw)
    throw new Error("The separate NativeFinding publication changed before adoption.");
  interruptedNativeFindingIntents.delete(original);
  return {...target.custody,draft:clone(target.draft)};
}
export function retainReviewedNativeFindingIntent(storage:NativeFindingStorage,session:Pick<Storage,"getItem"|"setItem">,before:NativeFindingDraft,next:NativeFindingDraft,original:NativeFindingCustody,writer:string):NativeFindingCustody&{draft:NativeFindingDraft} {
  if(interruptedNativeFindingIntents.has(original))throw new Error("Retry the interrupted NativeFinding local publication before separating another intent.");
  if(!nativeFindingUnknown(before)||next.pending||before.scope!==next.scope||!uuid(writer)||writer===original.writerId||original.expectedRaw===null)
    throw new Error("Invalid NativeFinding recovery fork.");
  const key=nativeFindingCopyKey(before.scope,original.writerId),raw=storage.getItem(key),pointer=session.getItem(nativeFindingPointerKey(before.scope));
  if(raw!==original.expectedRaw||raw===null||!sameValue(decodeNativeFindingCopy(raw,before.scope,original.writerId).draft,before)
    ||pointer!==original.expectedPointer&&pointer!==original.writerId)
    throw new Error("Keep the exact earlier NativeFinding attempt before continuing.");
  assertNativeFindingReviewOrigins(storage,before);
  const attempt:NativeFindingIntentAttempt={before:clone(before),next:clone(next),originalWriter:original.writerId,originalRaw:raw,originalPointer:pointer,
    armed:stagedNativeFindingCopy(before,writer,pointer),targets:[]};
  interruptedNativeFindingIntents.set(original,attempt);
  return retryRetainedNativeFindingIntent(storage,session,before,original)!;
}
