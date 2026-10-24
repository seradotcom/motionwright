import {describe,expect,it} from "vitest";
import {fixtureProject} from "./fixture";
import {matchesRecoveryOrigin} from "./recoveryOrigin";
import type {Project,ProjectEvent} from "./types";
import {
  acknowledgeIntegrationSubmission,createIntegrationDraft,decodeIntegrationCopy,dispatchIntegrationSubmission,
  editIntegrationField,editIntegrationFields,encodeIntegrationCopy,encodeIntegrationFile,integrationExtension,integrationHandoff,
  integrationConflict,integrationCopyKey,integrationOutcomeMatches,integrationPointerKey,integrationReviewOrigins,
  integrationScope,integrationUnknown,integrationExtensionInput,integrationSnapshot,
  inspectIntegrationOutcome,prepareIntegrationSubmission,reconcileIntegrationDraft,recoverIntegrationSource,
  releaseIntegrationOutcome,repairIntegrationPointer,resolveIntegrationDraft,retainIntegrationFailure,
  retryRetainedIntegrationIntent,hasInterruptedIntegrationIntent,assertIntegrationReviewOrigins,
  retainReviewedIntegrationIntent,settleIntegrationFailure,writeIntegrationCopy,
  type IntegrationCustody,type IntegrationDraft,type IntegrationSubmission,
} from "./integrationAuthoringDraft";
const clone=<T,>(value:T):T=>structuredClone(value);
function project():Project {const value=clone(fixtureProject);value.extensions=[];value.handoffs=[];value.locks=[];return value;}
function ready(value=project()){return editIntegrationFields(createIntegrationDraft(value),{
  extensionName:"Original descriptor",extensionVersion:"1.0.0",extensionDigest:"ab".repeat(32),
  extensionLicense:"MIT",extensionSource:"https://example.invalid/original-test-package"});}
function binding(value=project()){return editIntegrationFields(ready(value),{
  handoffExternalId:"public-release-original",handoffRevision:"r17",handoffLocalResource:"project:"+value.id});}
function prepared(value=project()){const draft=ready(value);return {value,submission:prepareIntegrationSubmission(draft,value,"extension",null,crypto.randomUUID())};}
function saved(value:Project,submission:IntegrationSubmission):Project {
  const next=clone(value);next.revision++;const change=submission.change;
  switch(change.type){
    case "upsert_extension":next.extensions=next.extensions.filter(value=>value.id!==change.extension.id);next.extensions.push(clone(change.extension));break;
    case "upsert_handoff":next.handoffs=next.handoffs.filter(value=>value.id!==change.binding.id);next.handoffs.push(clone(change.binding));break;
    case "remove_extension":next.extensions=next.extensions.filter(value=>value.id!==change.extension_id);break;
    case "remove_handoff":next.handoffs=next.handoffs.filter(value=>value.id!==change.binding_id);break;
  }
  return next;
}
class Memory {
  entries=new Map<string,string>();drop=false;onSet:((key:string,value:string)=>void)|null=null;
  get length(){return this.entries.size;}
  key(index:number){return [...this.entries.keys()][index]??null;}
  getItem(key:string){return this.entries.get(key)??null;}
  setItem(key:string,value:string){this.onSet?.(key,value);if(!this.drop)this.entries.set(key,value);}
  removeItem(key:string){this.entries.delete(key);}
}
function custody():IntegrationCustody{return {writerId:crypto.randomUUID(),expectedRaw:null,expectedPointer:null};}
function copy(draft:IntegrationDraft,writer=crypto.randomUUID()){return encodeIntegrationCopy({schema:"motionwright.integration-recovery/1",
  scope:draft.scope,writerId:writer,updatedAt:1,draft});}
function history(value:Project,submission:IntegrationSubmission):ProjectEvent[]{return [{revision:value.revision+1,change:clone(submission.change),created_at:"2026-10-23T00:00:00Z"}];}
describe("integration author input and original response authority",()=>{
  it.each([""," ","1e","AB".repeat(32)," ab"+"cd".repeat(31),"a".repeat(65)])("retains raw digest %j without truncating or normalizing",raw=>{
    const draft=editIntegrationField(ready(),"extensionDigest",raw),writer=crypto.randomUUID();
    expect(draft.input.extensionDigest).toBe(raw);expect(decodeIntegrationCopy(copy(draft,writer),draft.scope,writer).draft.input.extensionDigest).toBe(raw);
    expect(()=>integrationExtension(draft.input)).toThrow();
  });
  it("retains a whitespace revision rather than silently replacing it by null",()=>{
    const value=project(),draft=editIntegrationField(binding(value),"handoffRevision","   "),writer=crypto.randomUUID();
    expect(decodeIntegrationCopy(copy(draft,writer),draft.scope,writer).draft.input.handoffRevision).toBe("   ");
    expect(()=>integrationHandoff(draft.input,integrationSnapshot(value).resources)).toThrow(/revision/i);
  });
  it("retains hidden digest and external kind while projecting a context binding",()=>{
    const value=project(),draft=editIntegrationFields(binding(value),{handoffDirection:"artifact_output",handoffKind:"artifact",handoffDigest:"cd".repeat(32)});
    const context=editIntegrationField(draft,"handoffDirection","context_output");
    expect(integrationHandoff(context.input,integrationSnapshot(value).resources).artifact_sha256).toBeNull();
    expect(context.input.handoffDigest).toBe(draft.input.handoffDigest);expect(context.input.handoffKind).toBe("artifact");
    const output=editIntegrationField(context,"handoffDirection","artifact_output");
    expect(integrationHandoff(output.input,integrationSnapshot(value).resources).artifact_sha256).toBe(draft.input.handoffDigest);
  });
  it("checks UTF-8 byte limits rather than JavaScript code units",()=>{
    const value=project();
    expect(()=>integrationExtension(editIntegrationField(ready(value),"extensionName","é".repeat(81)).input)).toThrow(/160 UTF-8/);
    expect(()=>integrationHandoff(editIntegrationField(binding(value),"handoffExternalId","é".repeat(129)).input,integrationSnapshot(value).resources)).toThrow(/256 UTF-8/);
    expect(()=>integrationExtension(editIntegrationField(ready(value),"extensionName","é".repeat(80)).input)).not.toThrow();
  });
  it("refuses absent local bindings while retaining the exact resource identity",()=>{
    const value=project(),draft=editIntegrationField(binding(value),"handoffLocalResource","project:"+crypto.randomUUID());
    expect(()=>prepareIntegrationSubmission(draft,value,"handoff",null,crypto.randomUUID())).toThrow(/absent/i);
    expect(draft.input.handoffLocalResource).not.toBe("project:"+value.id);
  });
  it("rejects foreign project, generation, branch and corrupt copy ownership",()=>{
    const draft=ready(),writer=crypto.randomUUID(),raw=copy(draft,writer);
    expect(()=>decodeIntegrationCopy(raw,draft.scope,crypto.randomUUID())).toThrow();
    for(const key of ["id","generation","active_branch"] as const){
      const other=project();other[key]=crypto.randomUUID();expect(()=>decodeIntegrationCopy(raw,integrationScope(other),writer)).toThrow();
    }
    expect(()=>decodeIntegrationCopy(raw.slice(0,-1),draft.scope,writer)).toThrow();
  });
  it("matching peer values never acknowledge the original pending attempt",()=>{
    const {value,submission}=prepared(),peer=saved(value,submission);
    const observed=reconcileIntegrationDraft(submission.draft,peer);
    expect(observed.pending).toEqual(submission.draft.pending);expect(integrationUnknown(observed)).toBe(true);
    expect(integrationConflict(observed,peer)).toBe(true);
  });
  it("cannot dispatch or acknowledge a serialized copy of a preparation",()=>{
    const {value,submission}=prepared(),forged=clone(submission);
    expect(()=>dispatchIntegrationSubmission(forged,forged.draft,value)).toThrow();
    expect(()=>acknowledgeIntegrationSubmission(forged.draft,forged,saved(value,submission))).toThrow();
  });
  it("dispatches an original submission once and requires its exact returned revision and values",()=>{
    const {value,submission}=prepared();dispatchIntegrationSubmission(submission,submission.draft,value);
    expect(()=>dispatchIntegrationSubmission(submission,submission.draft,value)).toThrow();
    const wrong=saved(value,submission);wrong.revision++;
    expect(()=>acknowledgeIntegrationSubmission(submission.draft,submission,wrong)).toThrow();
    const modified=saved(value,submission);modified.extensions[0].permissions=["network"];
    expect(()=>acknowledgeIntegrationSubmission(submission.draft,submission,modified)).toThrow();
    const accepted=acknowledgeIntegrationSubmission(submission.draft,submission,saved(value,submission));
    expect(accepted.pending).toBeNull();expect(accepted.input).toEqual(submission.draft.input);
    expect(()=>acknowledgeIntegrationSubmission(submission.draft,submission,saved(value,submission))).toThrow();
  });
  it("keeps saved editable fields bound to their earlier source instead of silently rebasing over a peer",()=>{
    const {value,submission}=prepared();dispatchIntegrationSubmission(submission,submission.draft,value);
    const canonical=saved(value,submission),accepted=acknowledgeIntegrationSubmission(submission.draft,submission,canonical);
    const peer=clone(canonical);peer.revision++;peer.extensions[0].source="https://example.invalid/peer-review";
    expect(integrationConflict(reconcileIntegrationDraft(accepted,peer),peer)).toBe(true);
    expect(accepted.input.extensionSource).toBe(submission.draft.input.extensionSource);
  });
  it("the original acknowledgement preserves later ABA input and the other form",()=>{
    const {value,submission}=prepared();dispatchIntegrationSubmission(submission,submission.draft,value);
    let later=editIntegrationField(submission.draft,"extensionName","Changed after send");
    later=editIntegrationField(later,"extensionName",submission.draft.input.extensionName);
    later=editIntegrationField(later,"handoffExternalId","later independent public reference");
    const accepted=acknowledgeIntegrationSubmission(later,submission,saved(value,submission));
    expect(accepted.touched).toContain("extensionName");expect(accepted.touched).toContain("handoffExternalId");
    expect(accepted.sequences.extensionName).toBe(later.sequences.extensionName);expect(accepted.input).toEqual(later.input);
  });
  it("a rejected or missing original response remains unknown through reload and is never replayable",()=>{
    const {value,submission}=prepared();dispatchIntegrationSubmission(submission,submission.draft,value);settleIntegrationFailure(submission);
    const unknown=retainIntegrationFailure(submission.draft,submission.draft.pending!.id,"Lost terminal response");
    expect(()=>acknowledgeIntegrationSubmission(unknown,submission,saved(value,submission))).toThrow();
    const writer=crypto.randomUUID(),recovered=decodeIntegrationCopy(copy(unknown,writer),unknown.scope,writer).draft;
    expect(integrationUnknown(recovered)).toBe(true);
    for(const action of ["extension","handoff","toggle_extension","remove_extension","remove_handoff"] as const)
      expect(()=>prepareIntegrationSubmission(recovered,value,action,null,crypto.randomUUID())).toThrow();
  });
  it("decoding NOT_SENT never fabricates a live authority token",()=>{
    const {submission}=prepared(),draft=retainIntegrationFailure(submission.draft,submission.draft.pending!.id,"copy unavailable",true);
    const writer=crypto.randomUUID();expect(decodeIntegrationCopy(copy(draft,writer),draft.scope,writer).draft.pending?.outcome).toBe("UNKNOWN");
  });
  it("handoff acknowledgement preserves exact revision, digest and the independent descriptor",()=>{
    const value=project(),draft=editIntegrationFields(binding(value),{handoffRevision:" exact-r17 ",handoffDirection:"artifact_output",handoffDigest:"cd".repeat(32)});
    const submission=prepareIntegrationSubmission(draft,value,"handoff",null,crypto.randomUUID());
    dispatchIntegrationSubmission(submission,submission.draft,value);
    const receipt=saved(value,submission),wrong=clone(receipt);wrong.handoffs[0].external_revision="exact-r17";
    expect(()=>acknowledgeIntegrationSubmission(submission.draft,submission,wrong)).toThrow();
    const accepted=acknowledgeIntegrationSubmission(submission.draft,submission,receipt);
    expect(accepted.input).toEqual(draft.input);expect(accepted.touched).toContain("extensionName");
    expect(accepted.touched).toContain("handoffRevision");
  });
  it("toggle records its own unknown and preserves later opt-in input",()=>{
    const value=project(),draft=editIntegrationField(ready(value),"extensionRights","cleared");
    const extension=integrationExtension(draft.input);value.extensions=[extension];
    const base=editIntegrationFields(createIntegrationDraft(value),integrationExtensionInput(extension));
    const submission=prepareIntegrationSubmission(base,value,"toggle_extension",extension.id,crypto.randomUUID());
    dispatchIntegrationSubmission(submission,submission.draft,value);
    const later=editIntegrationField(editIntegrationField(submission.draft,"extensionEnabled",true),"extensionEnabled",false);
    const acknowledged=acknowledgeIntegrationSubmission(later,submission,saved(value,submission));
    expect(acknowledged.input.extensionEnabled).toBe(false);expect(acknowledged.pending).toBeNull();
    const owner=crypto.randomUUID(),raw=copy(submission.draft,owner);
    expect(integrationUnknown(decodeIntegrationCopy(raw,base.scope,owner).draft)).toBe(true);
  });
  it("a whole-form setter keeps a later name without treating unchanged opt-in as a later edit",()=>{
    const value=project(),input=editIntegrationField(ready(value),"extensionRights","cleared");
    const extension=integrationExtension(input.input);value.extensions=[extension];
    const draft=editIntegrationFields(createIntegrationDraft(value),integrationExtensionInput(extension));
    const submission=prepareIntegrationSubmission(draft,value,"toggle_extension",extension.id,crypto.randomUUID());
    dispatchIntegrationSubmission(submission,submission.draft,value);
    const later=editIntegrationFields(submission.draft,integrationExtensionInput({...extension,name:"Later name only"}));
    expect(later.sequences.extensionEnabled).toBe(submission.draft.sequences.extensionEnabled);
    const accepted=acknowledgeIntegrationSubmission(later,submission,saved(value,submission));
    expect(accepted.input.extensionName).toBe("Later name only");expect(accepted.input.extensionEnabled).toBe(true);
  });
  it.each(["rights","kind"] as const)("later %s opt-out survives a confirmed earlier activation",field=>{
    const value=project(),input=editIntegrationField(ready(value),"extensionRights","cleared");
    const extension=integrationExtension(input.input);value.extensions=[extension];
    const draft=editIntegrationFields(createIntegrationDraft(value),integrationExtensionInput(extension));
    const submission=prepareIntegrationSubmission(draft,value,"toggle_extension",extension.id,crypto.randomUUID());
    dispatchIntegrationSubmission(submission,submission.draft,value);
    const later=editIntegrationFields(submission.draft,integrationExtensionInput(field==="rights"
      ?{...extension,rights_status:"restricted",enabled:false}:{...extension,kind:"catalog-package",enabled:false}));
    const accepted=acknowledgeIntegrationSubmission(later,submission,saved(value,submission));
    expect(accepted.input.extensionEnabled).toBe(false);
    expect(accepted.input.extensionRights).toBe(later.input.extensionRights);
    expect(accepted.input.extensionKind).toBe(later.input.extensionKind);
    expect(()=>integrationExtension(accepted.input)).not.toThrow();
  });
  it("a whole-form setter during a failed recovery read carries only the actual later fields",()=>{
    const {value,submission}=prepared(),empty=createIntegrationDraft(value);
    const incomplete={...integrationExtensionInput({
      id:empty.input.extensionId,name:"Later name during unavailable storage",kind:empty.input.extensionKind,
      package_version:empty.input.extensionVersion,digest_sha256:empty.input.extensionDigest,
      license:empty.input.extensionLicense,source:empty.input.extensionSource,rights_status:empty.input.extensionRights,
      enabled:empty.input.extensionEnabled,permissions:empty.input.extensionPermissions})};
    const later=editIntegrationFields(empty,incomplete);
    expect(later.touched).not.toContain("extensionId");expect(later.touched).not.toContain("extensionDigest");
    const recovered=recoverIntegrationSource(submission.draft,later,value);
    expect(recovered.input.extensionName).toBe("Later name during unavailable storage");
    expect(recovered.input.extensionDigest).toBe(submission.draft.input.extensionDigest);
    expect(recovered.input.extensionId).toBe(submission.draft.input.extensionId);
  });
  it.each(["remove_extension","remove_handoff"] as const)("requires original %s absence with the correct source identity",action=>{
    const value=project(),input=binding(value),extension=integrationExtension(input.input),handoff=integrationHandoff(input.input,integrationSnapshot(value).resources);
    value.extensions=[extension];value.handoffs=[handoff];const draft=createIntegrationDraft(value);
    const submission=prepareIntegrationSubmission(draft,value,action,action==="remove_extension"?extension.id:handoff.id,crypto.randomUUID());
    dispatchIntegrationSubmission(submission,submission.draft,value);
    const wrong=clone(value);wrong.revision++;
    expect(()=>acknowledgeIntegrationSubmission(submission.draft,submission,wrong)).toThrow();
    expect(acknowledgeIntegrationSubmission(submission.draft,submission,saved(value,submission)).pending).toBeNull();
  });
  it("registration never implicitly opts in a descriptor or grants requested permissions",()=>{
    const draft=ready(),value=integrationExtension(draft.input);expect(value.enabled).toBe(false);expect(value.rights_status).toBe("unknown");
    expect(()=>integrationExtension({...draft.input,extensionEnabled:true})).toThrow(/Rights/);
    expect(()=>integrationExtension({...draft.input,extensionPermissions:["network","network"]})).toThrow(/duplicated/);
  });
  it("retains later input when recovering a previously unreadable UNKNOWN",()=>{
    const {value,submission}=prepared(),later=editIntegrationField(createIntegrationDraft(value),"handoffExternalId","Separately authored public resource");
    const recovered=recoverIntegrationSource(submission.draft,later,value);
    expect(recovered.pending?.id).toBe(submission.draft.pending?.id);
    expect(recovered.input.handoffExternalId).toBe(later.input.handoffExternalId);
  });
  it("rejects stale and locked preparation through existing source constraints",()=>{
    const value=project(),draft=ready(value),newer=clone(value);newer.revision++;
    expect(()=>prepareIntegrationSubmission(draft,newer,"extension",null,crypto.randomUUID())).toThrow();
    value.locks.push({id:crypto.randomUUID(),resource:"project:"+value.id,kind:"content",note:"Preserve"} as Project["locks"][number]);
    expect(()=>prepareIntegrationSubmission(ready(value),value,"extension",null,crypto.randomUUID())).toThrow();
  });
  it("rejects no-op records rather than adding an identical revision",()=>{
    const value=project(),draft=ready(value);value.extensions=[integrationExtension(draft.input)];
    const current=editIntegrationFields(createIntegrationDraft(value),integrationExtensionInput(value.extensions[0]));
    expect(()=>prepareIntegrationSubmission(current,value,"extension",null,crypto.randomUUID())).toThrow(/already saved/);
  });
});
describe("integration read-only outcome inspection and original custody",()=>{
  it("reads Project, consecutive journal, Project and permits only a separate explicit intent",async()=>{
    const {value,submission}=prepared(),peer=saved(value,submission),calls:string[]=[];
    const review=await inspectIntegrationOutcome(submission.draft,peer,{
      readProject:async()=>{calls.push("project");return clone(peer);},
      readHistory:async()=>{calls.push("history");return history(value,submission);},
    });
    expect(calls).toEqual(["project","history","project"]);expect(review.matchingValueRevisions).toEqual([peer.revision]);
    expect(integrationUnknown(submission.draft)).toBe(true);
    const next=releaseIntegrationOutcome(submission.draft,peer,review);
    expect(next.pending).toBeNull();expect(next.input).toEqual(submission.draft.input);
    expect(integrationOutcomeMatches(submission.draft,peer,clone(review))).toBe(false);
  });
  it("refuses gaps, inconsistent rereads and changed input during review",async()=>{
    const {value,submission}=prepared(),peer=saved(value,submission);
    await expect(inspectIntegrationOutcome(submission.draft,peer,{readProject:async()=>peer,readHistory:async()=>[]})).rejects.toThrow(/history/i);
    let reads=0;
    await expect(inspectIntegrationOutcome(submission.draft,peer,{readProject:async()=>{const p=clone(peer);if(reads++)p.revision++;return p;},
      readHistory:async()=>history(value,submission)})).rejects.toThrow(/changed/i);
    const review=await inspectIntegrationOutcome(submission.draft,peer,{readProject:async()=>peer,readHistory:async()=>history(value,submission)});
    expect(()=>releaseIntegrationOutcome(editIntegrationField(submission.draft,"extensionName","Later"),peer,review)).toThrow();
  });
  it("silently lost local data cannot become a confirmed recovery copy",()=>{
    const local=new Memory(),session=new Memory(),draft=ready(),owner=custody();local.drop=true;
    expect(()=>writeIntegrationCopy(local,session,draft,owner)).toThrow(/not confirmed/i);
    expect(session.getItem(integrationPointerKey(draft.scope))).toBeNull();
  });
  it("keeps a foreign pointer that arrives during data publication",()=>{
    const local=new Memory(),session=new Memory(),draft=ready(),owner=custody(),foreign=crypto.randomUUID();
    local.onSet=()=>session.setItem(integrationPointerKey(draft.scope),foreign);
    expect(()=>writeIntegrationCopy(local,session,draft,owner)).toThrow(/pointer changed/i);
    expect(session.getItem(integrationPointerKey(draft.scope))).toBe(foreign);
  });
  it("repairs a failed own pointer publication without adopting a third writer",()=>{
    const local=new Memory(),session=new Memory(),draft=ready(),owner=custody();session.drop=true;
    expect(()=>writeIntegrationCopy(local,session,draft,owner)).toThrow(/not confirmed/i);
    expect(owner.pointerAttempt).toBeDefined();session.drop=false;
    const foreign=crypto.randomUUID();session.setItem(integrationPointerKey(draft.scope),foreign);
    expect(()=>repairIntegrationPointer(local,session,draft,owner)).toThrow(/Another Integration writer/i);
    expect(session.getItem(integrationPointerKey(draft.scope))).toBe(foreign);
    session.removeItem(integrationPointerKey(draft.scope));
    repairIntegrationPointer(local,session,draft,owner);writeIntegrationCopy(local,session,draft,owner);
    expect(session.getItem(integrationPointerKey(draft.scope))).toBe(owner.writerId);
  });
  it("separates an unknown attempt without altering its bytes and guards its retained origins",async()=>{
    const {value,submission}=prepared(),peer=saved(value,submission),local=new Memory(),session=new Memory(),owner=custody();
    writeIntegrationCopy(local,session,submission.draft,owner);const original=owner.expectedRaw;
    const review=await inspectIntegrationOutcome(submission.draft,peer,{readProject:async()=>peer,readHistory:async()=>history(value,submission)});
    const next=releaseIntegrationOutcome(submission.draft,peer,review);
    const retained=retainReviewedIntegrationIntent(local,session,submission.draft,next,owner,crypto.randomUUID());
    expect(local.getItem(integrationCopyKey(next.scope,owner.writerId))).toBe(original);
    expect(matchesRecoveryOrigin(integrationReviewOrigins(retained.draft).get(owner.writerId)!,original!)).toBe(true);
    const changed=editIntegrationField(retained.draft,"extensionName","Another explicit intent");
    local.setItem(integrationCopyKey(next.scope,owner.writerId),original+" ");
    expect(()=>writeIntegrationCopy(local,session,changed,retained)).toThrow(/origin changed/i);
  });
  it("explicit empty input cannot erase a pending unknown",()=>{
    const {value,submission}=prepared();expect(()=>resolveIntegrationDraft(submission.draft,value,"empty")).toThrow(/unknown/i);
  });
});


async function interruptedSetup(){
  const {value,submission}=prepared(),local=new Memory(),session=new Memory(),owner=custody();
  writeIntegrationCopy(local,session,submission.draft,owner);
  const review=await inspectIntegrationOutcome(submission.draft,value,{readProject:async()=>clone(value),readHistory:async()=>[]});
  const next=releaseIntegrationOutcome(submission.draft,value,review);
  return {value,before:submission.draft,next,local,session,owner,original:owner.expectedRaw!,armed:crypto.randomUUID()};
}
describe("flat Integration UNKNOWN origins and interrupted local publication",()=>{
  it("retains eight distinct UNKNOWN attempts without embedding prior copies recursively",async()=>{
    const value=project(),local=new Memory(),session=new Memory(),originals=new Map<string,string>();
    let draft=ready(value),owner=custody();
    for(let cycle=0;cycle<8;cycle++){
      const prepared=prepareIntegrationSubmission(draft,value,"extension",null,crypto.randomUUID());
      draft=prepared.draft;writeIntegrationCopy(local,session,draft,owner);
      originals.set(integrationCopyKey(draft.scope,owner.writerId),owner.expectedRaw!);
      const review=await inspectIntegrationOutcome(draft,value,{readProject:async()=>clone(value),readHistory:async()=>[]});
      const next=releaseIntegrationOutcome(draft,value,review);
      const retained=retainReviewedIntegrationIntent(local,session,draft,next,owner,crypto.randomUUID());
      draft=retained.draft;owner={writerId:retained.writerId,expectedRaw:retained.expectedRaw,expectedPointer:retained.expectedPointer};
      expect(draft.reviewRetention?.schema).toBe("motionwright.integration-review-retention/2");
      expect(integrationReviewOrigins(draft).size).toBe((cycle+1)*2);
      expect(new TextEncoder().encode(retained.expectedRaw!).length).toBeLessThan(25_000);
      for(const [key,bytes] of originals)expect(local.getItem(key)).toBe(bytes);
      assertIntegrationReviewOrigins(local,draft);
    }
    expect(local.length).toBe(17);
  });
  it("reads the old nested format into flat conditions without modifying original bytes",async()=>{
    const state=await interruptedSetup(),armedRaw=copy(state.before,state.armed);
    state.local.setItem(integrationCopyKey(state.before.scope,state.armed),armedRaw);
    const writer=crypto.randomUUID(),legacy={...state.next,reviewRetention:{schema:"motionwright.integration-review-retention/1",
      original:{writerId:state.owner.writerId,raw:state.original},armed:{writerId:state.armed,raw:armedRaw}}};
    const raw=JSON.stringify({schema:"motionwright.integration-recovery/1",scope:legacy.scope,writerId:writer,updatedAt:1,draft:legacy});
    const migrated=decodeIntegrationCopy(raw,legacy.scope,writer).draft;
    expect(migrated.reviewRetention?.schema).toBe("motionwright.integration-review-retention/2");
    expect(integrationReviewOrigins(migrated).size).toBe(2);assertIntegrationReviewOrigins(state.local,migrated);
    expect(state.local.getItem(integrationCopyKey(legacy.scope,state.owner.writerId))).toBe(state.original);
    expect(state.local.getItem(integrationCopyKey(legacy.scope,state.armed))).toBe(armedRaw);
  });
  it.each(["data-quota","data-drop","data-after-write","pointer-quota","pointer-drop","pointer-after-write"])("retries its own interrupted %s without replacing original bytes or replaying IPC",async mode=>{
    const state=await interruptedSetup(),{local,session,before,next,owner,armed,original}=state;
    if(mode.startsWith("data"))local.onSet=(key,raw)=>{
      if(JSON.parse(raw).draft.pending!==null)return;
      if(mode==="data-drop"){local.drop=true;return;}
      if(mode==="data-after-write")local.entries.set(key,raw);
      throw new Error("Unit storage quota");
    };
    else{
      if(mode==="pointer-drop")session.drop=true;
      else session.onSet=(key,raw)=>{if(mode==="pointer-after-write")session.entries.set(key,raw);throw new Error("Unit pointer quota");};
    }
    expect(()=>retainReviewedIntegrationIntent(local,session,before,next,owner,armed)).toThrow();
    expect(hasInterruptedIntegrationIntent(owner)).toBe(true);
    const pointer=session.getItem(integrationPointerKey(before.scope));
    expect(pointer).not.toBe(armed);
    expect(local.getItem(integrationCopyKey(before.scope,owner.writerId))).toBe(original);
    const later=editIntegrationField(editIntegrationField(before,"extensionDigest","1e"),"handoffExternalId",'{\n "later": ');
    expect(()=>writeIntegrationCopy(local,session,later,owner)).toThrow(/incomplete/i);
    expect(local.getItem(integrationCopyKey(before.scope,owner.writerId))).toBe(original);
    local.onSet=null;local.drop=false;session.onSet=null;session.drop=false;
    const retained=retryRetainedIntegrationIntent(local,session,later,owner)!;
    expect(retained).not.toBeNull();expect(retained.draft.pending).toBeNull();
    expect(retained.draft.input.extensionDigest).toBe("1e");expect(retained.draft.input.handoffExternalId).toBe('{\n "later": ');
    expect(retained.draft.sequences).toEqual(later.sequences);
    expect(retained.draft.touched).toContain("extensionDigest");expect(retained.draft.touched).toContain("handoffExternalId");
    expect(session.getItem(integrationPointerKey(before.scope))).toBe(retained.writerId);
    expect(local.getItem(integrationCopyKey(before.scope,owner.writerId))).toBe(original);
    expect(integrationUnknown(decodeIntegrationCopy(local.getItem(integrationCopyKey(before.scope,armed))!,before.scope,armed).draft)).toBe(true);
    expect(hasInterruptedIntegrationIntent(owner)).toBe(false);assertIntegrationReviewOrigins(local,retained.draft);
    expect(()=>integrationExtension(retained.draft.input)).toThrow(/digest/i);
  });
  it.each(["during-data","during-pointer"])("preserves a foreign pointer arriving %s and refuses to adopt it on Retry",async phase=>{
    const {local,session,before,next,owner,armed,original}=await interruptedSetup(),foreign=crypto.randomUUID();
    if(phase==="during-data")local.onSet=(_key,raw)=>{if(JSON.parse(raw).draft.pending===null)session.entries.set(integrationPointerKey(before.scope),foreign);};
    else session.onSet=(key)=>{session.entries.set(key,foreign);session.drop=true;};
    expect(()=>retainReviewedIntegrationIntent(local,session,before,next,owner,armed)).toThrow();
    local.onSet=null;session.onSet=null;session.drop=false;
    expect(()=>retryRetainedIntegrationIntent(local,session,before,owner)).toThrow(/Another writer/i);
    expect(session.getItem(integrationPointerKey(before.scope))).toBe(foreign);
    expect(local.getItem(integrationCopyKey(before.scope,owner.writerId))).toBe(original);
  });
  it("never overwrites an original modified during successor publication, even if JSON values still match",async()=>{
    const {local,session,before,next,owner,armed,original}=await interruptedSetup();
    local.onSet=(_key,raw)=>{if(JSON.parse(raw).draft.pending===null)local.entries.set(integrationCopyKey(before.scope,owner.writerId),original+" ");};
    expect(()=>retainReviewedIntegrationIntent(local,session,before,next,owner,armed)).toThrow(/origin changed/i);
    local.onSet=null;
    expect(()=>retryRetainedIntegrationIntent(local,session,before,owner)).toThrow(/original.*changed/i);
    expect(local.getItem(integrationCopyKey(before.scope,owner.writerId))).toBe(original+" ");
  });
  it("cannot recover an interrupted publication using cloned custody or regressed input sequences",async()=>{
    const {local,session,before,next,owner,armed}=await interruptedSetup();session.drop=true;
    expect(()=>retainReviewedIntegrationIntent(local,session,before,next,owner,armed)).toThrow();
    session.drop=false;
    expect(retryRetainedIntegrationIntent(local,session,before,clone(owner))).toBeNull();
    const altered=clone(before);altered.input.extensionName="Unsequenced replacement";
    expect(()=>retryRetainedIntegrationIntent(local,session,altered,owner)).toThrow(/sequence/i);
    expect(retryRetainedIntegrationIntent(local,session,before,owner)?.draft.pending).toBeNull();
  });
  it("does not permit a flat successor to omit an origin inherited by an earlier UNKNOWN",async()=>{
    const state=await interruptedSetup();
    const retained=retainReviewedIntegrationIntent(state.local,state.session,state.before,state.next,state.owner,state.armed);
    const submission=prepareIntegrationSubmission(retained.draft,state.value,"extension",null,crypto.randomUUID());
    writeIntegrationCopy(state.local,state.session,submission.draft,retained);
    const review=await inspectIntegrationOutcome(submission.draft,state.value,{readProject:async()=>clone(state.value),readHistory:async()=>[]});
    const next=releaseIntegrationOutcome(submission.draft,state.value,review);
    const twice=retainReviewedIntegrationIntent(state.local,state.session,submission.draft,next,retained,crypto.randomUUID());
    const forged=clone(twice.draft);
    if(forged.reviewRetention?.schema!=="motionwright.integration-review-retention/2")throw new Error("Expected flat conditions");
    forged.reviewRetention.origins=forged.reviewRetention.origins.filter(origin=>origin.writerId!==state.owner.writerId);
    expect(()=>assertIntegrationReviewOrigins(state.local,forged)).toThrow(/missing or changed/i);
  });
});


describe("non-authoritative integration input salvage",()=>{
  it("keeps the complete raw input after a bounded recovery field is exceeded",()=>{
    const oversized="Original locator\n"+"x".repeat(65_537);
    const draft=editIntegrationField(ready(),"extensionSource",oversized),before=clone(draft),writer=crypto.randomUUID();
    expect(()=>copy(draft,writer)).toThrow();
    const salvage=JSON.parse(encodeIntegrationFile(draft));
    expect(salvage.schema).toBe("motionwright.integration-input-salvage/1");expect(salvage.authority).toBe("author_input_only");
    expect(salvage.draft).toEqual(draft);expect(salvage.draft.input.extensionSource).toBe(oversized);expect(draft).toEqual(before);
  });
  it("never revives mutation authority from a salvage file or oversized forged copy",()=>{
    const {value,submission}=prepared(),writer=crypto.randomUUID(),raw=encodeIntegrationFile(submission.draft);
    expect(()=>decodeIntegrationCopy(raw,submission.draft.scope,writer)).toThrow();
    const exported=JSON.parse(raw) as IntegrationSubmission;
    expect(()=>dispatchIntegrationSubmission(exported,submission.draft,value)).toThrow(/original.*request/i);
    expect(()=>acknowledgeIntegrationSubmission(submission.draft,exported,saved(value,submission))).toThrow(/original.*response/i);
    const oversized=editIntegrationField(submission.draft,"extensionSource","x".repeat(65_537));
    const forged=JSON.stringify({schema:"motionwright.integration-recovery/1",scope:oversized.scope,writerId:writer,updatedAt:1,draft:oversized});
    expect(()=>decodeIntegrationCopy(forged,oversized.scope,writer)).toThrow();
  });
});
