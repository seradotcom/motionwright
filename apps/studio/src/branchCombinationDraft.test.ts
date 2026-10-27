import { describe,expect,test } from "vitest";
import { fixtureProject } from "./fixture";
import { emptyProductionDesign } from "./creativeProduction";
import { acknowledgeBranchDraft,assertBranchOrigins,branchCopyKey,branchPointerKey,branchUnknown,
  createBranchDraft,decodeBranchCopy,editBranchDraft,inspectBranchOutcome,prepareBranchSubmission,
  recoverBranchAfterReadFailure,retainBranchFailure,retainBranchOrigin,separateBranchIntent,writeBranchCopy } from "./branchDraft";
import { beginBranchCombination,combinationInput,combinationPreviewMatches,editBranchCombination,
  prepareCombinationSubmission,retainCanonicalCombinationPreview } from "./branchCombinationDraft";
import type { BranchDraft,BranchCustody } from "./branchDraft";
import type { BranchState,Project } from "./types";

const candidate="20000000-0000-4000-8000-000000000001",attempt="20000000-0000-4000-8000-000000000002";
const second="20000000-0000-4000-8000-000000000003",patch="20000000-0000-4000-8000-000000000004";
class MemoryStorage {
  data=new Map<string,string>();get length(){return this.data.size;}
  key(i:number){return [...this.data.keys()][i]??null;}getItem(k:string){return this.data.get(k)??null;}
  setItem(k:string,v:string){this.data.set(k,v);}
}
function branchState(p:Project):BranchState{return structuredClone({scenes:p.scenes,markers:p.markers,locks:p.locks,
  deliverables:p.deliverables,brief:p.brief,narrative:p.narrative,audio:p.audio,visual_language:p.visual_language,
  proposal_sets:p.proposal_sets,model_invocations:p.model_invocations,production_design:p.production_design});}
function fixture(){
  const p=structuredClone(fixtureProject);p.production_design=emptyProductionDesign();
  const donor=p.branches.find(b=>b.id!==p.active_branch)!;
  const saved=p.branch_workspaces.find(w=>w.branch_id===donor.id)!.current_state;
  saved.production_design=emptyProductionDesign();
  const scene=p.scenes[0],node=scene.nodes[0],donorNode=saved.scenes.find(s=>s.id===scene.id)!.nodes.find(n=>n.id===node.id)!;
  donorNode.x+=100;donorNode.keyframes=[{at:{num:"1",den:"1"},property:"x",value:400,interpolation:"linear"}];
  let draft=beginBranchCombination(createBranchDraft(p),p,candidate);
  draft=editBranchDraft(draft,"  Two strategies  ");
  draft=editBranchCombination(draft,{donorBranchId:donor.id,sceneId:scene.id,rationale:"Keep the base arrangement and the donor's movement.",picks:[{node_id:node.id,field:"motion"}]});
  const input=combinationInput(draft,p);
  // Synthetic response shape for recovery/response validation only. This is
  // not a canonical service call, native render or persistence observation.
  const response={source_revision:p.revision,scene_id:scene.id,before:[structuredClone(node)],
    after:[{...structuredClone(node),keyframes:structuredClone(donorNode.keyframes)}],
    dirty_start:scene.start,dirty_end:scene.duration,kind:"semantic_diff_full_scene_invalidation_not_pixel_verification" as const};
  const preview=retainCanonicalCombinationPreview(draft,p,input,response);
  return {p,draft,input,response,preview};
}
function submitted(){const value=fixture();return {...value,...prepareCombinationSubmission(value.draft,value.p,attempt,value.preview)};}
function reply(value:ReturnType<typeof fixture>):Project{
  const result=structuredClone(value.p),base=branchState(value.p),state=structuredClone(base),scene=state.scenes.find(s=>s.id===value.input.scene_id)!;
  scene.nodes[0]=structuredClone(value.response.after[0]);scene.status="draft";
  state.production_design!.patches=[{id:patch,scene_id:scene.id,source_revision:value.p.revision,rationale:value.input.rationale,
    before:structuredClone(value.response.before),after:structuredClone(value.response.after),reverts:null}];
  result.revision++;result.updated_at="2026-10-27T00:00:00Z";
  result.branches.find(b=>b.id===result.active_branch)!.head_revision=result.revision;
  result.branches.push({id:candidate,name:value.input.candidate_name,parent_branch:result.active_branch,
    base_revision:value.p.revision,head_revision:result.revision,protected:false,created_at:"2026-10-27T00:00:00Z"});
  result.branch_workspaces.push({branch_id:candidate,base_revision:value.p.revision,base_state:base,current_state:state});
  return result;
}
const custody=(id=attempt,pointer:string|null=null):BranchCustody=>({writerId:id,expectedRaw:null,expectedPointer:pointer});
function retain(draft:BranchDraft){const store=new MemoryStorage(),tab=new MemoryStorage(),writer=custody();
  const raw=writeBranchCopy(store,tab,draft,writer);return {store,tab,writer,raw,key:branchCopyKey(draft.scope,attempt)};}

describe("combination uses the branch recovery authority",()=>{
  test("ordinary copies remain schema1 and cannot dispatch combination input as an ordinary create",()=>{
    const {p,draft}=fixture(),ordinary=editBranchDraft(createBranchDraft(p),"Ordinary");
    expect(JSON.parse(retain(ordinary).raw).schema).toBe("motionwright.branch-recovery/1");
    expect(JSON.parse(retain(draft).raw).schema).toBe("motionwright.branch-recovery/2");
    expect(()=>prepareBranchSubmission(draft,p,attempt)).toThrow("combination");
  });
  test("unfinished names, source choices and reason survive roundtrip without acquiring a preview",()=>{
    const {p,draft}=fixture();let edited=editBranchDraft(draft,"  \u0001🙂");
    edited=editBranchCombination(edited,{rationale:" {unfinished",sceneId:"missing-selection",donorBranchId:""});
    const saved=retain(edited),decoded=decodeBranchCopy(saved.raw,edited.scope,attempt).draft;
    expect(decoded).toEqual(edited);expect(combinationPreviewMatches(decoded,p,null)).toBe(false);
    expect(()=>combinationInput(decoded,p)).toThrow();
  });
  test("stale or changed complete source and changed choices invalidate canonical preparation",()=>{
    const {p,draft,preview}=fixture();expect(combinationPreviewMatches(draft,p,preview)).toBe(true);
    expect(combinationPreviewMatches(draft,{...p,title:p.title+" changed"},preview)).toBe(false);
    expect(combinationPreviewMatches(editBranchCombination(draft,{rationale:"Later"}),p,preview)).toBe(false);
    expect(()=>prepareCombinationSubmission(draft,{...p,revision:p.revision+1},attempt,preview)).toThrow();
  });
  test("uncertain project truth cannot be prepared as a current source",()=>{
    const {p,draft}=fixture();p.state="unknown";
    expect(()=>combinationInput(draft,p)).toThrow("current saved project");
    expect(draft.combination?.candidateId).toBe(candidate);
  });
  test("only the existing schema1-to2 first-write upgrade is admitted in the exact reply",()=>{
    const value=fixture();value.p.schema_version=1;
    value.preview=retainCanonicalCombinationPreview(value.draft,value.p,value.input,value.response);
    const prepared=prepareCombinationSubmission(value.draft,value.p,attempt,value.preview),receipt=reply(value);
    receipt.schema_version=2;
    expect(acknowledgeBranchDraft(prepared.draft,attempt,receipt,value.preview).pending).toBeNull();
    receipt.schema_version=3;
    expect(()=>acknowledgeBranchDraft(prepared.draft,attempt,receipt,value.preview)).toThrow("unrelated project state");
  });
  test.each(["scope","before","after","unrelated_field","extra"])("rejects a corrupted canonical %s without mutating raw",mode=>{
    const {p,draft,input,response}=fixture(),bad=structuredClone(response),raw=structuredClone(draft);
    if(mode==="scope")bad.source_revision++;
    if(mode==="before")bad.before[0].text="forged";
    if(mode==="after")bad.after[0].keyframes[0].value=999;
    if(mode==="unrelated_field")bad.after[0].x+=1;
    if(mode==="extra")Object.assign(bad,{native_pass:true});
    expect(()=>retainCanonicalCombinationPreview(draft,p,input,bad)).toThrow();expect(draft).toEqual(raw);
  });
  test("only original callback plus exact reviewed source can acknowledge the candidate",()=>{
    const value=submitted(),receipt=reply(value);
    expect(acknowledgeBranchDraft(value.draft,second,receipt,value.preview)).toBe(value.draft);
    expect(()=>acknowledgeBranchDraft(value.draft,attempt,receipt)).toThrow("UNKNOWN");
    const next=acknowledgeBranchDraft(value.draft,attempt,receipt,value.preview);
    expect(next.pending).toBeNull();expect(next.name).toBe(value.draft.name);
    expect(next.combination?.candidateId).toBe(candidate);expect(next.combination?.completed?.revision).toBe(receipt.revision);
    expect(()=>combinationInput(next,receipt)).toThrow("separate candidate");
    const another=beginBranchCombination(next,receipt,second);
    expect(another.name).toBe(next.name);expect(another.combination?.rationale).toBe(next.combination?.rationale);
    expect(another.combination?.candidateId).toBe(second);expect(another.combination?.completed).toBeNull();
  });
  test.each(["parent","review","candidate","nodes","undo"])("an unrelated %s mutation in a reply preserves UNKNOWN",mode=>{
    const value=submitted(),receipt=reply(value);
    if(mode==="parent")receipt.branch_workspaces[0].current_state.scenes[0].objective="forged";
    if(mode==="review")receipt.reviews=[];
    if(mode==="candidate")receipt.branches.at(-1)!.head_revision--;
    if(mode==="nodes")receipt.branch_workspaces.at(-1)!.current_state.scenes[0].nodes[0].name="forged";
    if(mode==="undo")receipt.branch_workspaces.at(-1)!.current_state.production_design!.patches![0].before[0].x+=1;
    if(mode==="review")receipt.title+=" independent mutation";
    expect(()=>acknowledgeBranchDraft(value.draft,attempt,receipt,value.preview)).toThrow();
    expect(branchUnknown(value.draft)).toBe(true);
  });
  test("late name, reason, scene and field edits survive original ACK without new UUID allocation",()=>{
    const value=submitted();let later=editBranchDraft(value.draft,"  Later candidate  ");
    later=editBranchCombination(later,{rationale:"Later reason",sceneId:"not-yet-valid",picks:[]});
    const result=acknowledgeBranchDraft(later,attempt,reply(value),value.preview);
    expect(result.name).toBe(later.name);expect(result.combination?.picks).toEqual([]);
    expect(result.combination?.sceneId).toBe("not-yet-valid");expect(result.combination?.candidateId).toBe(candidate);
  });
  test.each(["branch","workspace","record"])("closed acknowledgement rejects an unexpected %s field",kind=>{
    const value=submitted(),receipt=reply(value),workspace=receipt.branch_workspaces.at(-1)!;
    Object.assign(kind==="branch"?receipt.branches.at(-1)!:kind==="workspace"?workspace:workspace.current_state.production_design!.patches![0],{unrecognized_authority:true});
    expect(()=>acknowledgeBranchDraft(value.draft,attempt,receipt,value.preview)).toThrow();
    expect(branchUnknown(value.draft)).toBe(true);
  });
  test.each(["revision","sequence","pending"])("corrupt completed %s cannot turn recovery into acknowledgement",kind=>{
    const value=submitted(),completed=acknowledgeBranchDraft(value.draft,attempt,reply(value),value.preview),saved=retain(completed),copy=JSON.parse(saved.raw);
    if(kind==="pending")copy.draft.pending=value.draft.pending;
    else copy.draft.combination.completed[kind]=Number.MAX_SAFE_INTEGER;
    expect(()=>decodeBranchCopy(JSON.stringify(copy),completed.scope,attempt)).toThrow("completion conflicts");
    expect(saved.store.getItem(saved.key)).toBe(saved.raw);
  });
  test("silent local copy failure blocks confirmation and preserves earlier peer bytes",()=>{
    const {draft}=submitted(),saved=retain(draft),copy=editBranchCombination(draft,{rationale:"Later"});
    saved.store.setItem=()=>undefined;
    expect(()=>writeBranchCopy(saved.store,saved.tab,copy,saved.writer)).toThrow("not confirmed");
    expect(saved.store.getItem(saved.key)).toBe(saved.raw);
  });
  test("reload retains UNKNOWN and exact input without treating matching project state as ACK",()=>{
    const value=submitted(),saved=retain(value.draft);
    const original=retainBranchOrigin(decodeBranchCopy(saved.raw,value.draft.scope,attempt).draft,{key:saved.key,raw:saved.raw});
    const recovered=recoverBranchAfterReadFailure(original,createBranchDraft(value.p),false,value.p);
    expect(branchUnknown(recovered)).toBe(true);expect(recovered.pending?.combination).toEqual(value.input);
    const raw=writeBranchCopy(saved.store,saved.tab,recovered,custody(second,attempt));
    expect(decodeBranchCopy(raw,recovered.scope,second).draft.combination?.candidateId).toBe(candidate);
    saved.store.data.set(saved.key,saved.raw+" ");expect(()=>assertBranchOrigins(saved.store,recovered)).toThrow();
  });
  test("NOT_SENT has no replay authority after reload",()=>{
    const value=submitted(),notSent=retainBranchFailure(value.draft,attempt,"backup failed",true),saved=retain(notSent);
    const recovered=recoverBranchAfterReadFailure(decodeBranchCopy(saved.raw,notSent.scope,attempt).draft,createBranchDraft(value.p),false,value.p);
    expect(branchUnknown(recovered)).toBe(true);expect(()=>combinationInput(recovered,value.p)).toThrow("UNKNOWN");
  });
  test("read history may identify matching values but cannot acknowledge or silently resend",async()=>{
    const value=submitted(),receipt=reply(value),saved=retain(value.draft);
    const outcome=await inspectBranchOutcome(value.draft,receipt,{readProject:async()=>structuredClone(receipt),
      readHistory:async()=>[{revision:receipt.revision,change:value.change,created_at:receipt.updated_at}]});
    expect(outcome.matchingRevisions).toEqual([receipt.revision]);expect(branchUnknown(value.draft)).toBe(true);
    expect(()=>separateBranchIntent(value.draft,receipt,outcome,{key:saved.key,raw:saved.raw})).toThrow("stable identity");
    const next=separateBranchIntent(value.draft,receipt,outcome,{key:saved.key,raw:saved.raw},second);
    expect(next.name).toBe("");expect(next.combination?.picks).toEqual([]);expect(next.combination?.candidateId).toBe(second);
    writeBranchCopy(saved.store,saved.tab,next,custody(second,attempt));expect(saved.store.getItem(saved.key)).toBe(saved.raw);
    expect(saved.tab.getItem(branchPointerKey(value.draft.scope))).toBe(second);
  });
});
