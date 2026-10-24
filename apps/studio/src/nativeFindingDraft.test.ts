import { describe, expect, it } from "vitest";
import { fixtureBootstrap } from "./fixture";
import type { Project, ProjectEvent } from "./types";
import {
  acknowledgeNativeFindingSubmission, assertNativeFindingReviewOrigins, createNativeFindingDraft,
  decodeNativeFindingCopy, dispatchNativeFindingSubmission, editNativeFindingField, encodeNativeFindingFile,
  inspectNativeFindingOutcome, nativeFindingCopyKey, nativeFindingCurrent, nativeFindingPointerKey,
  nativeFindingUnknown, prepareNativeFindingSubmission, readNativeFindingCopies, readNativeFindingRecoverySource, recoverNativeFindingSource, releaseNativeFindingOutcome,
  repairNativeFindingPointer, retainReviewedNativeFindingIntent, retryRetainedNativeFindingIntent,
  writeNativeFindingCopy, type NativeFindingCustody, type NativeFindingDraft, type NativeFindingSource, type NativeFindingRecoveryObservation,
} from "./nativeFindingDraft";

// Input-custody unit fixtures only. These digests are not renderer artifacts.
const clone=<T,>(value:T):T=>structuredClone(value);
function setup() {
  const project=clone(fixtureBootstrap.project),scene=project.scenes[0],profile=project.deliverables[0];
  const source:NativeFindingSource={projectId:project.id,generation:project.generation,branchId:project.active_branch,revision:project.revision,
    sceneId:scene.id,profileId:profile.id,frameIndex:0,timelineTime:clone(scene.start),pngSha256:"a".repeat(64),
    width:profile.width,height:profile.height,frameRate:{num:30,den:1}};
  let draft=createNativeFindingDraft(project);
  draft=editNativeFindingField(draft,"source",source);
  draft=editNativeFindingField(draft,"severity","blocking");
  draft=editNativeFindingField(draft,"confidence","uncertain");
  draft=editNativeFindingField(draft,"nodeId",scene.nodes[0].id);
  draft=editNativeFindingField(draft,"violatedConstraint","  Preserve the selected object's contrast.  ");
  draft=editNativeFindingField(draft,"observation","The authored text touches its neighbor.\nExact author input.");
  draft=editNativeFindingField(draft,"proposedRepair","Move only the selected object after preview.");
  return {project,source,draft};
}
class MemoryStorage {
  readonly values=new Map<string,string>();
  fail:((key:string,value:string)=>boolean)|null=null;
  get length(){return this.values.size;}
  key(index:number){return [...this.values.keys()][index]??null;}
  getItem(key:string){return this.values.get(key)??null;}
  setItem(key:string,value:string){if(!this.fail?.(key,value))this.values.set(key,value);}
  removeItem(key:string){this.values.delete(key);}
}
const custody=():NativeFindingCustody=>({writerId:crypto.randomUUID(),expectedRaw:null,expectedPointer:null});
function response(project:Project,submission:ReturnType<typeof prepareNativeFindingSubmission>):Project {
  const next=clone(project),change=submission.change;
  next.revision++;
  next.reviews.push({id:crypto.randomUUID(),kind:change.kind,anchor:{resource:change.resource,branch_id:project.active_branch,
    revision:project.revision,start:clone(change.start),end:clone(change.end),locale:change.locale,profile_id:change.profile_id},
    body:change.body,status:"open",resolution:null,created_at:"2026-10-23T00:00:00Z",resolved_at:null});
  return next;
}
async function reviewed(draft:NativeFindingDraft,project:Project,events:ProjectEvent[]=[]) {
  return inspectNativeFindingOutcome(draft,project,{readProject:async()=>clone(project),readHistory:async()=>clone(events)});
}

describe("native finding input and source continuity",()=>{
  it("round-trips all six raw fields and exact source without native or effect tokens",()=>{
    const {draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    writeNativeFindingCopy(data,pointer,draft,owner);
    const raw=data.getItem(nativeFindingCopyKey(draft.scope,owner.writerId))!;
    expect(decodeNativeFindingCopy(raw,draft.scope,owner.writerId).draft.input).toEqual(draft.input);
    expect(raw).not.toMatch(/effect_grant|preview_token|request_id|auto_replay/);
  });
  it.each(["sceneId","profileId","pngSha256","frameIndex","revision"] as const)("refuses silent reassignment to a different %s",field=>{
    const {draft,project,source}=setup(),other=clone(source);
    if(field==="frameIndex"||field==="revision")other[field]++;
    else if(field==="pngSha256")other.pngSha256="b".repeat(64);else other[field]=crypto.randomUUID();
    expect(nativeFindingCurrent(draft,project,other)).toBe(false);
    expect(()=>prepareNativeFindingSubmission(draft,project,other,crypto.randomUUID())).toThrow();
    expect(draft.input.source).toEqual(source);
  });
  it.each(["violatedConstraint","observation","proposedRepair"] as const)("keeps invalid exact %s and refuses dispatch preparation",field=>{
    const {project,source,draft}=setup();
    for(const text of ["  ","\u0001","x".repeat(801)]){
      const invalid=editNativeFindingField(draft,field,text);
      expect(()=>prepareNativeFindingSubmission(invalid,project,source,crypto.randomUUID())).toThrow();
      expect(invalid.input[field]).toBe(text);
    }
  });
  it("allows scene-level findings in an empty scene but refuses a removed object",()=>{
    const {project,source,draft}=setup();project.scenes[0].nodes=[];
    expect(()=>prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID())).toThrow();
    const sceneLevel=editNativeFindingField(draft,"nodeId",null);
    expect(prepareNativeFindingSubmission(sceneLevel,project,source,crypto.randomUUID()).change.resource).toBe("scene:"+source.sceneId);
  });
  it("confirms only the original dispatched response while preserving all six fields and later text",()=>{
    const {project,source,draft}=setup(),submission=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID());
    expect(()=>acknowledgeNativeFindingSubmission(submission.draft,submission,response(project,submission))).toThrow();
    dispatchNativeFindingSubmission(submission,submission.draft,project);
    const later=editNativeFindingField(submission.draft,"observation","Later input while the original response was delayed.");
    const next=acknowledgeNativeFindingSubmission(later,submission,response(project,submission));
    expect(next.input).toEqual(later.input);expect(next.input.source).toEqual(source);expect(next.pending).toBeNull();
    expect(next.baseRevision).toBe(project.revision+1);
    expect(()=>acknowledgeNativeFindingSubmission(later,submission,response(project,submission))).toThrow();
  });
  it.each(["foreign","revision","body","status","anchor"] as const)("does not acknowledge an incompatible %s response",fault=>{
    const {project,source,draft}=setup(),submission=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID());
    dispatchNativeFindingSubmission(submission,submission.draft,project);const wrong=response(project,submission),review=wrong.reviews.at(-1)!;
    if(fault==="foreign")wrong.generation=crypto.randomUUID();
    if(fault==="revision")wrong.revision++;
    if(fault==="body")review.body="another review";
    if(fault==="status")review.status="resolved";
    if(fault==="anchor")review.anchor.profile_id=crypto.randomUUID();
    expect(()=>acknowledgeNativeFindingSubmission(submission.draft,submission,wrong)).toThrow();
    expect(nativeFindingUnknown(submission.draft)).toBe(true);
  });
  it("does not deserialize dispatch or ACK authority from a recovery copy",()=>{
    const {project,source,draft}=setup(),submission=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID());
    const data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    writeNativeFindingCopy(data,pointer,submission.draft,owner);
    const restored=decodeNativeFindingCopy(owner.expectedRaw!,draft.scope,owner.writerId).draft;
    const forged={draft:restored,change:clone(submission.change)};
    expect(()=>dispatchNativeFindingSubmission(forged,restored,project)).toThrow();
    expect(()=>acknowledgeNativeFindingSubmission(restored,forged,response(project,submission))).toThrow();
    expect(()=>prepareNativeFindingSubmission(restored,project,source,crypto.randomUUID())).toThrow();
  });
  it("fresh matching history is an observation, not acknowledgement or automatic replay",async()=>{
    const {project,source,draft}=setup(),submission=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID());
    dispatchNativeFindingSubmission(submission,submission.draft,project);const saved=response(project,submission);
    const review=await reviewed(submission.draft,saved,[{revision:saved.revision,change:clone(submission.change),created_at:"2026-10-23T00:00:00Z"}]);
    expect(nativeFindingUnknown(submission.draft)).toBe(true);expect(review.matchingValueRevisions).toEqual([saved.revision]);
    const separate=releaseNativeFindingOutcome(submission.draft,saved,review);
    expect(separate.pending).toBeNull();expect(separate.input).toEqual(draft.input);
    expect(()=>dispatchNativeFindingSubmission(clone(submission),separate,saved)).toThrow();
    expect(nativeFindingCurrent(separate,saved,source)).toBe(false);
  });
  it("rejects missing history and a changed read window without losing UNKNOWN",async()=>{
    const {project,source,draft}=setup(),submission=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID());
    const saved=response(project,submission);await expect(reviewed(submission.draft,saved)).rejects.toThrow(/history/);
    let reads=0;await expect(inspectNativeFindingOutcome(submission.draft,project,{readProject:async()=>{reads++;return reads===1?clone(project):saved;},readHistory:async()=>[]})).rejects.toThrow(/changed/);
    expect(nativeFindingUnknown(submission.draft)).toBe(true);
  });
  it("requires the same reviewed input before separating an uncertain attempt",async()=>{
    const {project,source,draft}=setup(),submission=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID());
    const review=await reviewed(submission.draft,project),later=editNativeFindingField(submission.draft,"proposedRepair","Later exact repair");
    expect(()=>releaseNativeFindingOutcome(later,project,review)).toThrow();
    expect(()=>editNativeFindingField(later,"source",{...source,frameIndex:1})).toThrow();
  });
});

describe("verified native finding recovery custody",()=>{
  it.each(["data","pointer"] as const)("blocks a silent %s write before any operation and repairs only its own publication",fault=>{
    const {draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    (fault==="data"?data:pointer).fail=()=>true;
    expect(()=>writeNativeFindingCopy(data,pointer,draft,owner)).toThrow(/not confirmed/);
    expect(pointer.getItem(nativeFindingPointerKey(draft.scope))).toBeNull();
    data.fail=null;pointer.fail=null;repairNativeFindingPointer(data,pointer,draft,owner);
    writeNativeFindingCopy(data,pointer,draft,owner);
    expect(pointer.getItem(nativeFindingPointerKey(draft.scope))).toBe(owner.writerId);
    expect(decodeNativeFindingCopy(owner.expectedRaw!,draft.scope,owner.writerId).draft.input).toEqual(draft.input);
  });
  it.each(["data","pointer"] as const)("preserves a foreign %s change instead of overwriting it",fault=>{
    const {draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    writeNativeFindingCopy(data,pointer,draft,owner);
    const key=nativeFindingCopyKey(draft.scope,owner.writerId),pointerKey=nativeFindingPointerKey(draft.scope),before=data.getItem(key)!;
    if(fault==="data")data.setItem(key,"foreign original bytes");else pointer.setItem(pointerKey,crypto.randomUUID());
    const observed=fault==="data"?data.getItem(key):pointer.getItem(pointerKey);
    expect(()=>writeNativeFindingCopy(data,pointer,editNativeFindingField(draft,"observation","new raw"),owner)).toThrow(/another/i);
    expect(fault==="data"?data.getItem(key):pointer.getItem(pointerKey)).toBe(observed);
    if(fault==="pointer")expect(data.getItem(key)).toBe(before);
  });
  it("keeps exact corrupt bytes downloadable and refuses a foreign source shape",()=>{
    const {draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    writeNativeFindingCopy(data,pointer,draft,owner);const key=nativeFindingCopyKey(draft.scope,owner.writerId);
    data.setItem(key,'{"broken":');
    expect(readNativeFindingCopies(data,draft.scope).entries[0]).toMatchObject({raw:'{"broken":',value:null});
    const foreign=JSON.parse(owner.expectedRaw!);foreign.draft.input.source.generation=crypto.randomUUID();
    expect(()=>decodeNativeFindingCopy(JSON.stringify(foreign),draft.scope,owner.writerId)).toThrow(/another/i);
  });
  it("retains twelve independent unknown attempts with flat bounded digest anchors",async()=>{
    const {project,source,draft:first}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage();
    let draft=first,owner=custody();const originals=new Map<string,string>();
    for(let i=0;i<12;i++){
      draft=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID()).draft;
      writeNativeFindingCopy(data,pointer,draft,owner);originals.set(nativeFindingCopyKey(draft.scope,owner.writerId),owner.expectedRaw!);
      const review=await reviewed(draft,project),next=releaseNativeFindingOutcome(draft,project,review);
      const retained=retainReviewedNativeFindingIntent(data,pointer,draft,next,owner,crypto.randomUUID());
      draft=retained.draft;owner=retained;
      for(const [key,raw] of originals)expect(data.getItem(key)).toBe(raw);
      expect(owner.expectedRaw!.length).toBeLessThan(24_000);
    }
    expect(draft.reviewRetention?.origins).toHaveLength(24);
    assertNativeFindingReviewOrigins(data,draft);
  });
  it.each(["successor","pointer"] as const)("an interrupted %s publication retries locally and preserves later author input",async fault=>{
    const {project,source,draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    const unknown=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID()).draft;
    writeNativeFindingCopy(data,pointer,unknown,owner);const key=nativeFindingCopyKey(draft.scope,owner.writerId),original=data.getItem(key)!;
    const review=await reviewed(unknown,project),next=releaseNativeFindingOutcome(unknown,project,review);let writes=0;
    if(fault==="successor")data.fail=()=>++writes===2;else pointer.fail=()=>true;
    expect(()=>retainReviewedNativeFindingIntent(data,pointer,unknown,next,owner,crypto.randomUUID())).toThrow(/confirmed/);
    expect(data.getItem(key)).toBe(original);
    const later=editNativeFindingField(unknown,"observation","Later text after local publication failed.");
    data.fail=null;pointer.fail=null;
    const retried=retryRetainedNativeFindingIntent(data,pointer,later,owner)!;
    expect(retried.draft.pending).toBeNull();expect(retried.draft.input.observation).toBe(later.input.observation);
    expect(data.getItem(key)).toBe(original);expect(pointer.getItem(nativeFindingPointerKey(draft.scope))).toBe(retried.writerId);
    assertNativeFindingReviewOrigins(data,retried.draft);
  });
  it("refuses interrupted publication retry after a foreign writer acquires the pointer",async()=>{
    const {project,source,draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    const unknown=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID()).draft;writeNativeFindingCopy(data,pointer,unknown,owner);
    const next=releaseNativeFindingOutcome(unknown,project,await reviewed(unknown,project));pointer.fail=()=>true;
    expect(()=>retainReviewedNativeFindingIntent(data,pointer,unknown,next,owner,crypto.randomUUID())).toThrow();
    pointer.fail=null;const foreign=crypto.randomUUID();pointer.setItem(nativeFindingPointerKey(draft.scope),foreign);
    expect(()=>retryRetainedNativeFindingIntent(data,pointer,unknown,owner)).toThrow(/Another/);
    expect(pointer.getItem(nativeFindingPointerKey(draft.scope))).toBe(foreign);
  });
  it("keeps salvage complete even when invalid oversized input cannot be a recovery copy",()=>{
    const {draft}=setup(),raw="unfinished ".repeat(20_000),later=editNativeFindingField(draft,"observation",raw);
    expect(JSON.parse(encodeNativeFindingFile(later)).draft.input.observation).toBe(raw);
    expect(()=>writeNativeFindingCopy(new MemoryStorage(),new MemoryStorage(),later,custody())).toThrow();
  });
});

describe("native finding failed-read source authority",()=>{
  it.each(["complete","later index failure"] as const)("retains a listed UNKNOWN through %s and rejects replacement under the same writer",listing=>{
    const {project,source,draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    const unknown=prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID()).draft;
    writeNativeFindingCopy(data,pointer,unknown,owner);
    const key=nativeFindingCopyKey(draft.scope,owner.writerId),raw=data.getItem(key)!,pointerKey=nativeFindingPointerKey(draft.scope);
    data.setItem(nativeFindingCopyKey(draft.scope,crypto.randomUUID()),"unreadable later entry");
    let failFirstRead=true,failLaterIndex=listing==="later index failure";
    const reads={
      get length(){return data.length;},
      key:(index:number)=>{if(index===1&&failLaterIndex)throw new Error("later index unavailable");return data.key(index);},
      getItem:(candidate:string)=>{if(candidate===key&&failFirstRead){failFirstRead=false;throw new Error("first finding read unavailable");}return data.getItem(candidate);},
      setItem:data.setItem.bind(data),removeItem:data.removeItem.bind(data),
    };
    const observed:NativeFindingRecoveryObservation={pointerObserved:false,pointer:null,rawObserved:false,raw:null};
    expect(()=>readNativeFindingRecoverySource(reads,pointer,draft.scope,observed)).toThrow(/first finding read/);
    expect(observed).toEqual({pointerObserved:true,pointer:owner.writerId,rawObserved:false,raw:null});
    if(failLaterIndex)expect(()=>readNativeFindingCopies(reads,draft.scope,observed)).toThrow(/later index/);
    else expect(nativeFindingUnknown(readNativeFindingCopies(reads,draft.scope,observed).entries[0].value!.draft)).toBe(true);
    expect(observed.rawObserved).toBe(true);expect(observed.raw).toBe(raw);
    const replacement=JSON.parse(raw);replacement.draft.pending=null;
    const replacementRaw=JSON.stringify(replacement);data.setItem(key,replacementRaw);
    expect(()=>readNativeFindingRecoverySource(reads,pointer,draft.scope,observed)).toThrow(/bytes changed/);
    expect(()=>readNativeFindingCopies(reads,draft.scope,observed)).toThrow(/bytes changed/);
    expect(observed.raw).toBe(raw);expect(data.getItem(key)).toBe(replacementRaw);expect(pointer.getItem(pointerKey)).toBe(owner.writerId);
    // Restoring the exact original permits recovery, but cannot acknowledge or
    // repeat its request. Later raw edits, including an empty field, survive.
    data.setItem(key,raw);failLaterIndex=false;
    const recovered=readNativeFindingRecoverySource(reads,pointer,draft.scope,observed)!;
    let later=editNativeFindingField(createNativeFindingDraft(project),"observation","Later exact input 1e\n");
    later=editNativeFindingField(later,"violatedConstraint","");
    const retained=recoverNativeFindingSource(recovered,later,project);
    expect(retained.pending).toEqual(unknown.pending);expect(nativeFindingUnknown(retained)).toBe(true);
    expect(retained.input.observation).toBe("Later exact input 1e\n");expect(retained.input.violatedConstraint).toBe("");
    expect(retained.input.source).toEqual(source);expect(data.getItem(key)).toBe(raw);
    expect(()=>prepareNativeFindingSubmission(retained,project,source,crypto.randomUUID())).toThrow();
  });
  it("retains the known writer when its first data read failed and another writer takes the pointer",()=>{
    const {project,source,draft}=setup(),data=new MemoryStorage(),pointer=new MemoryStorage(),owner=custody();
    writeNativeFindingCopy(data,pointer,prepareNativeFindingSubmission(draft,project,source,crypto.randomUUID()).draft,owner);
    const key=nativeFindingCopyKey(draft.scope,owner.writerId),raw=data.getItem(key)!,observed:NativeFindingRecoveryObservation={pointerObserved:false,pointer:null,rawObserved:false,raw:null};
    expect(()=>readNativeFindingRecoverySource({getItem:()=>{throw new Error("first finding read unavailable");}},pointer,draft.scope,observed)).toThrow(/first finding read/);
    const foreign=crypto.randomUUID();pointer.setItem(nativeFindingPointerKey(draft.scope),foreign);
    expect(()=>readNativeFindingRecoverySource(data,pointer,draft.scope,observed)).toThrow(/pointer changed/);
    expect(observed.pointer).toBe(owner.writerId);expect(observed.rawObserved).toBe(false);
    expect(pointer.getItem(nativeFindingPointerKey(draft.scope))).toBe(foreign);expect(data.getItem(key)).toBe(raw);
  });
});
