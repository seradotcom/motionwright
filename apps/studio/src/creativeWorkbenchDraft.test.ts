import { describe, expect, it } from "vitest";
import { applyChange } from "./api";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import type { Change, Project, ProjectEvent } from "./types";
import {
  acknowledgeWorkbenchDraft, authoredInteger, createWorkbenchDraft, decodeWorkbenchCopy, editWorkbenchDraft,
  encodeWorkbenchCopy, encodeWorkbenchFile, inspectWorkbenchOutcome, measuredProofMatches, measuredRaw, measuredTable,
  measuredView, prepareWorkbenchSubmission, readWorkbenchCopies, reconcileWorkbenchDraft, releaseWorkbenchOutcome,
  resolveWorkbenchDraft, retainReviewedWorkbenchIntent, retainWorkbenchFailure, selectMeasuredTable,
  workbenchConflict, workbenchCopyKey, workbenchOutcomeMatches, workbenchPointerKey, workbenchPreparationMatches,
  workbenchScope, workbenchUnconfirmed, writeWorkbenchCopy, WORKBENCH_RECOVERY_PREFIX,
} from "./creativeWorkbenchDraft";
import type { MeasuredByteProof, WorkbenchCustody, WorkbenchDraft, WorkbenchInput, WorkbenchKind, WorkbenchStorage } from "./creativeWorkbenchDraft";
const requestId="00000000-0000-4000-8000-000000000081", writer="00000000-0000-4000-8000-000000000082", fork="00000000-0000-4000-8000-000000000083";
const assetId="00000000-0000-4000-8000-000000000084", tableId="00000000-0000-4000-8000-000000000085";
const rows=[{key:"latency",label:"Observed latency",milli:12500,unit:"milliseconds" as const}], rowsText=JSON.stringify(rows,null,2), digest="ab".repeat(32);
function source():Project { const value=structuredClone(fixtureProject);value.locks=[];value.branch_workspaces=[];value.production_design=emptyProductionDesign();return value; }
function create(project:Project,kind:WorkbenchKind){return createWorkbenchDraft(project,{kind,sceneId:project.scenes[0].id});}
function edit(draft:WorkbenchDraft,text:string):WorkbenchDraft {
  const input=draft.input;
  return editWorkbenchDraft(draft,input.kind==="stage"?{...input,config:{...input.config,screen_heading:text,author_attestation:"Original author-owned fixture UI."}}
    :input.kind==="procedural"?{...input,config:{...input.config,fill:text}}
    :{...input,tables:{...input.tables,[input.selectedTable]:{...measuredRaw(input),label:text}}});
}
function measuredSource(project=source()):Project {
  const value=structuredClone(project);value.assets.push({id:assetId,name:"Observed fixture rows.json",media_type:"application/json",content_sha256:digest,source_revision:null});return value;
}
async function armed(kind:WorkbenchKind="stage"){
  const project=kind==="measured"?measuredSource():source();let draft=edit(create(project,kind),kind==="procedural"?"#aabbcc":"Clear workflow");let proof:MeasuredByteProof|null=null;
  if(draft.input.kind==="measured"){
    const input=draft.input;draft=editWorkbenchDraft(draft,{...input,tables:{...input.tables,new:{...measuredRaw(input),draftId:tableId,assetId,rowsText}}});
    proof={scope:draft.scope,tableKey:"new",assetId,digest,rowsText};
  }
  return {project,source:draft,proof,prepared:await prepareWorkbenchSubmission(draft,project,requestId,kind==="measured"?"table":"save",proof)};
}
class MemoryStorage implements WorkbenchStorage {
  values=new Map<string,string>();get length(){return this.values.size;}key(index:number){return [...this.values.keys()][index]??null;}
  getItem(key:string){return this.values.get(key)??null;}setItem(key:string,value:string){this.values.set(key,value);}removeItem(key:string){this.values.delete(key);}
}
const custody=():WorkbenchCustody=>({writerId:writer,expectedRaw:null,expectedPointer:null});
const journal=(project:Project,change:Change):ProjectEvent[]=>[{revision:project.revision,change,created_at:"2026-10-21T19:00:00Z"}];

describe("creative workbench raw input and original response authority",()=>{
  it("keeps raw integer grammar distinct from numeric coercion",()=>{
    for(const raw of [""," ","1e","1e2","0x10","1.5","Infinity","9007199254740993"])expect(()=>authoredInteger(raw,"Input",0,0xffffffff)).toThrow();
    expect(authoredInteger("  00041  ","Seed",0,0xffffffff)).toBe(41);expect(authoredInteger("-8","Axis",-100,100)).toBe(-8);
  });
  it("scopes all workbenches by project, generation, branch, scene and editor kind",()=>{
    const project=source(),scope=workbenchScope(project,{kind:"stage",sceneId:project.scenes[0].id});
    for(const identity of ["id","generation","active_branch"]as const)expect(workbenchScope({...project,[identity]:project[identity]+"-other"},{kind:"stage",sceneId:project.scenes[0].id})).not.toBe(scope);
    expect(workbenchScope(project,{kind:"procedural",sceneId:project.scenes[0].id})).not.toBe(scope);
    expect(workbenchScope(project,{kind:"stage",sceneId:project.scenes[1].id})).not.toBe(scope);
  });
  for(const kind of ["stage","procedural","measured"]as const){
    it(`${kind} observed matching values never acknowledge an original pending attempt`,async()=>{
      const {project,prepared}=await armed(kind),observed=await applyChange(project,prepared.change);
      expect(reconcileWorkbenchDraft(prepared.draft,observed)).toBe(prepared.draft);expect(workbenchUnconfirmed(prepared.draft)).toBe(true);expect(workbenchConflict(prepared.draft,observed)).toBe(true);
    });
    it(`${kind} the original ACK preserves later raw input and independent table buffers`,async()=>{
      const {project,prepared}=await armed(kind),later=edit(prepared.draft,kind==="procedural"?"#1e":"  Later input\n ");
      const receipt=await applyChange(project,prepared.change),ack=acknowledgeWorkbenchDraft(later,requestId,receipt);
      if(ack.input.kind==="measured"&&later.input.kind==="measured")expect(measuredRaw(ack.input)).toEqual(measuredRaw(later.input));else expect(ack.input).toEqual(later.input);
      expect(ack.intent).toBe(true);expect(ack.pending).toBeNull();expect(workbenchConflict(ack,receipt)).toBe(false);
    });
    it(`${kind} raw copy roundtrip does not carry execution grants or byte verification`,async()=>{
      const {prepared}=await armed(kind),raw=encodeWorkbenchCopy({schema:"motionwright.creative-workbench-recovery/1",scope:prepared.draft.scope,writerId:writer,updatedAt:1,draft:prepared.draft});
      const restored=decodeWorkbenchCopy(raw,prepared.draft.scope,writer).draft;expect(restored).toEqual(prepared.draft);
      const file=JSON.parse(encodeWorkbenchFile(prepared.draft));expect(Object.keys(file)).toEqual(["schema","exportedAt","draft"]);
      expect(file).not.toHaveProperty("effect_grant");expect(file.draft).not.toHaveProperty("byteProof");
    });
  }
  for(const field of ["id","generation","active_branch","revision","value"]as const){
    it(`rejects the original ACK with wrong ${field} without consuming UNKNOWN`,async()=>{
      const {project,prepared}=await armed(),receipt=await applyChange(project,prepared.change);
      if(field==="revision")receipt.revision++;else if(field==="value")receipt.production_design!.stages![0].config.screen_heading="Other values";else receipt[field]+="-other";
      expect(()=>acknowledgeWorkbenchDraft(prepared.draft,requestId,receipt)).toThrow();expect(prepared.draft.pending?.id).toBe(requestId);
    });
  }
  for(const kind of ["stage","procedural"]as const){
    it(`${kind} detach requires the original receipt to preserve every editable node`,async()=>{
      const {project,prepared}=await armed(kind),saved=await applyChange(project,prepared.change),draft=create(saved,kind),detached=await prepareWorkbenchSubmission(draft,saved,fork,"detach");
      const receipt=await applyChange(saved,detached.change),broken=structuredClone(receipt);broken.scenes[0].nodes.pop();
      expect(()=>acknowledgeWorkbenchDraft(detached.draft,fork,broken)).toThrow("preserve");expect(acknowledgeWorkbenchDraft(detached.draft,fork,receipt).pending).toBeNull();
      expect(receipt.scenes[0].nodes).toEqual(saved.scenes[0].nodes);
    });
  }
  it("an external revision needs an explicit base choice and never clears pending intent",async()=>{
    const {project,source:draft}=await armed(),external={...project,revision:project.revision+1};
    expect(reconcileWorkbenchDraft(draft,external)).toBe(draft);expect(workbenchConflict(draft,external)).toBe(true);
    const kept=resolveWorkbenchDraft(draft,external,"draft");expect(kept.input).toEqual(draft.input);expect(kept.baseRevision).toBe(external.revision);
  });
  it("invalidates asynchronous preparation when owner, revision, input or attempt changes",async()=>{
    const {project,source:draft,prepared}=await armed();expect(workbenchPreparationMatches(draft,project,draft,project)).toBe(true);
    expect(workbenchPreparationMatches(draft,project,edit(draft,"Later input"),project)).toBe(false);expect(workbenchPreparationMatches(draft,project,prepared.draft,project)).toBe(false);
    for(const identity of ["id","generation","active_branch"]as const)expect(workbenchPreparationMatches(draft,project,draft,{...project,[identity]:project[identity]+"-other"})).toBe(false);
    expect(workbenchPreparationMatches(draft,project,draft,{...project,revision:project.revision+1})).toBe(false);
  });
  it("Measured keeps exact independent table, node, precision and axis buffers when selecting another table",async()=>{
    const {project,prepared}=await armed("measured"),saved=await applyChange(project,prepared.change),draft=create(saved,"measured"),input=draft.input as Extract<WorkbenchInput,{kind:"measured"}>;
    const authored={...input,tables:{...input.tables,new:{...measuredRaw(input),label:"  New rows  ",decimals:"1e",axisMin:"-",axisMax:" 10000 "}}};
    const other=selectMeasuredTable(authored,tableId,saved),returned=selectMeasuredTable(other,"new",saved);
    expect(measuredRaw(returned)).toEqual(measuredRaw(authored));expect(measuredRaw(other).draftId).toBe(tableId);expect(()=>measuredView(measuredRaw(returned))).toThrow();
  });
  it("Measured ACK for one table cannot mark a different hidden author buffer as saved",async()=>{
    const {project,source:draft,proof}=await armed("measured"),input=draft.input as Extract<WorkbenchInput,{kind:"measured"}>;
    const hiddenId="00000000-0000-4000-8000-000000000086";
    const authored=editWorkbenchDraft(draft,{...input,tables:{...input.tables,[hiddenId]:{...measuredRaw(input),draftId:hiddenId,label:"Hidden unsaved source direction",selectedNode:"",selectedKey:"",decimals:"1e"}}});
    const prepared=await prepareWorkbenchSubmission(authored,project,requestId,"table",proof),saved=await applyChange(project,prepared.change),ack=acknowledgeWorkbenchDraft(prepared.draft,requestId,saved);
    expect(ack.intent).toBe(true);expect(reconcileWorkbenchDraft(ack,{...saved,revision:saved.revision+1})).toBe(ack);
    if(ack.input.kind!=="measured")throw new Error("Missing measured input");expect(ack.input.tables[hiddenId]).toEqual(authored.input.kind==="measured"?authored.input.tables[hiddenId]:null);
  });
  it("Measured reviewed new alias cannot overwrite a separately authored buffer at its saved table ID",async()=>{
    const {project,prepared}=await armed("measured"),secondAsset="00000000-0000-4000-8000-000000000087",secondDigest="cd".repeat(32);
    const secondRows=[{...rows[0],milli:18000}],secondRowsText=JSON.stringify(secondRows,null,2);
    project.assets.push({id:secondAsset,name:"Actual fixture revision two.json",media_type:"application/json",content_sha256:secondDigest,source_revision:null});
    const saved=await applyChange(project,prepared.change);
    // The first command committed, but its original response was lost. Review
    // permits a separate intent; it is deliberately not an ACK or alias merge.
    const review=await inspectWorkbenchOutcome(prepared.draft,saved,{readProject:async()=>saved,readHistory:async()=>journal(saved,prepared.change)});
    let next=releaseWorkbenchOutcome(prepared.draft,saved,review);
    if(next.input.kind!=="measured")throw new Error("Missing measured input");
    let input=selectMeasuredTable(next.input,tableId,saved);
    input={...input,tables:{...input.tables,[tableId]:{...input.tables[tableId],label:"  Independent saved-table direction  ",decimals:"1e"}}};
    input=selectMeasuredTable(input,"new",saved);
    next=editWorkbenchDraft(next,{...input,tables:{...input.tables,new:{...input.tables.new,assetId:secondAsset,rowsText:secondRowsText}}});
    const expected=structuredClone(input.tables[tableId]);
    const update=await prepareWorkbenchSubmission(next,saved,fork,"table",{scope:next.scope,tableKey:"new",assetId:secondAsset,digest:secondDigest,rowsText:secondRowsText});
    const receipt=await applyChange(saved,update.change),ack=acknowledgeWorkbenchDraft(update.draft,fork,receipt);
    if(ack.input.kind!=="measured"||next.input.kind!=="measured")throw new Error("Missing measured input");
    expect(ack.pending).toBeNull();expect(ack.baseRevision).toBe(receipt.revision);expect(ack.intent).toBe(true);
    expect(ack.input.tables[tableId]).toEqual(expected);expect(ack.input.tables.new).toEqual(next.input.tables.new);
    expect(reconcileWorkbenchDraft(ack,{...receipt,revision:receipt.revision+1})).toBe(ack);
  });
  it("Measured exact byte proof is ephemeral, tolerates label changes and rejects another source or recovered authority",async()=>{
    const {project,source:draft,proof}=await armed("measured");expect(measuredProofMatches(edit(draft,"New label"),project,proof)).toBe(true);
    expect(measuredProofMatches(draft,project,null)).toBe(false);expect(measuredProofMatches(draft,{...project,assets:project.assets.map(asset=>asset.id===assetId?{...asset,content_sha256:"cd".repeat(32)}:asset)},proof)).toBe(false);
    const input=draft.input as Extract<WorkbenchInput,{kind:"measured"}>;const changed=editWorkbenchDraft(draft,{...input,tables:{...input.tables,new:{...measuredRaw(input),rowsText:"[]"}}});
    expect(measuredProofMatches(changed,project,proof)).toBe(false);await expect(prepareWorkbenchSubmission(draft,project,fork,"table",null)).rejects.toThrow("exact imported source");
  });
  it("Measured binding ACK checks the original table, datum, view and projected Canvas value",async()=>{
    const {project,prepared}=await armed("measured"),saved=await applyChange(project,prepared.change);let draft=create(saved,"measured");
    const input=selectMeasuredTable(draft.input as Extract<WorkbenchInput,{kind:"measured"}>,tableId,saved),raw=measuredRaw(input),node=saved.scenes[0].nodes.find(value=>value.kind==="text")!;
    draft=editWorkbenchDraft(draft,{...input,tables:{...input.tables,[tableId]:{...raw,selectedNode:node.id,selectedKey:"latency",decimals:"3"}}});
    const bound=await prepareWorkbenchSubmission(draft,saved,fork,"binding"),receipt=await applyChange(saved,bound.change),broken=structuredClone(receipt);
    broken.scenes[0].nodes.find(value=>value.id===node.id)!.text="Other value";expect(()=>acknowledgeWorkbenchDraft(bound.draft,fork,broken)).toThrow("exact submitted");
    expect(acknowledgeWorkbenchDraft(bound.draft,fork,receipt).pending).toBeNull();expect(measuredTable(raw,saved).version).toBe(2);
  });
});

describe("creative workbench verified custody and uncertain outcome review",()=>{
  for(const fault of ["data","pointer"]as const){
    it(`requires verified ${fault} readback before a recovery write succeeds`,async()=>{
      const {prepared}=await armed(),store=new MemoryStorage(),pointers=new MemoryStorage(),owner=custody(),affected=fault==="data"?store:pointers;
      const original=affected.setItem.bind(affected);affected.setItem=()=>{};expect(()=>writeWorkbenchCopy(store,pointers,prepared.draft,owner)).toThrow("confirmed");
      affected.setItem=original;writeWorkbenchCopy(store,pointers,prepared.draft,owner);expect(pointers.getItem(workbenchPointerKey(prepared.draft.scope))).toBe(writer);
    });
  }
  for(const race of ["data","pointer"]as const){
    it(`rechecks ${race} custody after the recovery budget scan`,async()=>{
      const {prepared}=await armed(),store=new MemoryStorage(),pointers=new MemoryStorage(),owner=custody();writeWorkbenchCopy(store,pointers,prepared.draft,owner);
      const key=workbenchCopyKey(prepared.draft.scope,writer),pointer=workbenchPointerKey(prepared.draft.scope),original=store.key.bind(store);let raceArmed=true;
      store.key=index=>{if(raceArmed){raceArmed=false;if(race==="data")store.values.set(key,"Other author's bytes");else pointers.values.set(pointer,fork);}return original(index);};
      expect(()=>writeWorkbenchCopy(store,pointers,edit(prepared.draft,"Later input"),owner)).toThrow("changed");
      if(race==="data")expect(store.getItem(key)).toBe("Other author's bytes");else expect(pointers.getItem(pointer)).toBe(fork);
    });
  }
  it("rereads data after pointer publication and rejects an interleaved replacement",async()=>{
    const {prepared}=await armed(),store=new MemoryStorage(),pointers=new MemoryStorage(),owner=custody(),key=workbenchCopyKey(prepared.draft.scope,writer),set=pointers.setItem.bind(pointers);
    pointers.setItem=(name,value)=>{set(name,value);store.values.set(key,"Foreign replacement");};expect(()=>writeWorkbenchCopy(store,pointers,prepared.draft,owner)).toThrow("pointer was published");
    expect(store.getItem(key)).toBe("Foreign replacement");expect(owner.expectedRaw).not.toBe("Foreign replacement");
  });
  it("preserves corrupt bytes, rejects foreign scope and refuses clean checkpoint replacement",()=>{
    const draft=create(source(),"procedural"),store=new MemoryStorage(),pointers=new MemoryStorage(),owner=custody();writeWorkbenchCopy(store,pointers,draft,owner);
    expect(()=>writeWorkbenchCopy(store,pointers,edit(draft,"#aabbcc"),owner)).toThrow("immutable");
    const key=workbenchCopyKey(draft.scope,fork);store.setItem(key,"{corrupt");expect(readWorkbenchCopies(store,draft.scope).entries.find(value=>value.key===key)?.value).toBeNull();expect(store.getItem(key)).toBe("{corrupt");
    expect(()=>decodeWorkbenchCopy(owner.expectedRaw!,draft.scope+"-other",writer)).toThrow();
  });
  it("a full recovery budget blocks saving without deleting any previous author bytes",async()=>{
    const {prepared}=await armed(),store=new MemoryStorage(),pointers=new MemoryStorage();for(let index=0;index<64;index++)store.setItem(WORKBENCH_RECOVERY_PREFIX+"older-"+index,"Retained bytes");
    expect(()=>writeWorkbenchCopy(store,pointers,prepared.draft,custody())).toThrow("full");expect(store.length).toBe(64);expect(pointers.length).toBe(0);
  });
  it("recovering a NOT_SENT checkpoint conservatively retains UNKNOWN and rejects extra authority fields",async()=>{
    const {prepared}=await armed(),draft=retainWorkbenchFailure(prepared.draft,requestId,"Unconfirmed local write",true),value={schema:"motionwright.creative-workbench-recovery/1" as const,scope:draft.scope,writerId:writer,updatedAt:1,draft};
    expect(decodeWorkbenchCopy(encodeWorkbenchCopy(value),draft.scope,writer).draft.pending?.outcome).toBe("UNKNOWN");
    expect(()=>decodeWorkbenchCopy(JSON.stringify({...value,effect_grant:"forged"}),draft.scope,writer)).toThrow();
    expect(()=>decodeWorkbenchCopy(JSON.stringify({...value,draft:{...draft,input:{...draft.input,verified:true}}}),draft.scope,writer)).toThrow();
  });
  it("requires fresh read-history-read and retains matching values as comparison rather than ACK",async()=>{
    const {project,prepared}=await armed(),saved=await applyChange(project,prepared.change);let reads=0;
    const review=await inspectWorkbenchOutcome(prepared.draft,saved,{readProject:async()=>{reads++;return saved;},readHistory:async()=>journal(saved,prepared.change)});
    expect(reads).toBe(2);expect(review.matchingValueRevisions).toEqual([saved.revision]);expect(workbenchUnconfirmed(prepared.draft)).toBe(true);expect(workbenchOutcomeMatches(prepared.draft,saved,review)).toBe(true);
    const next=releaseWorkbenchOutcome(prepared.draft,saved,review);expect(next.pending).toBeNull();expect(next.input).toEqual(prepared.draft.input);expect(next.intent).toBe(true);
  });
  it("rejects incomplete history and a project changed during outcome inspection",async()=>{
    const {project,prepared}=await armed(),saved=await applyChange(project,prepared.change);
    await expect(inspectWorkbenchOutcome(prepared.draft,saved,{readProject:async()=>saved,readHistory:async()=>[]})).rejects.toThrow("incomplete");let reads=0;
    await expect(inspectWorkbenchOutcome(prepared.draft,saved,{readProject:async()=>++reads===1?saved:{...saved,revision:saved.revision+1},readHistory:async()=>journal(saved,prepared.change)})).rejects.toThrow("changed");
  });
  it("a separate reviewed intent verifies both records and preserves the exact earlier UNKNOWN",async()=>{
    const {project,prepared}=await armed(),saved=await applyChange(project,prepared.change),store=new MemoryStorage(),pointers=new MemoryStorage(),owner=custody();
    writeWorkbenchCopy(store,pointers,prepared.draft,owner);const original=owner.expectedRaw;
    const review=await inspectWorkbenchOutcome(prepared.draft,saved,{readProject:async()=>saved,readHistory:async()=>journal(saved,prepared.change)}),next=releaseWorkbenchOutcome(prepared.draft,saved,review);
    const newer=retainReviewedWorkbenchIntent(store,pointers,prepared.draft,next,owner,fork);expect(store.getItem(workbenchCopyKey(prepared.draft.scope,writer))).toBe(original);
    expect(decodeWorkbenchCopy(newer.expectedRaw!,next.scope,fork).draft.pending).toBeNull();expect(pointers.getItem(workbenchPointerKey(next.scope))).toBe(fork);
  });
});
