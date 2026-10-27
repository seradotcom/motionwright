import { emptyProductionDesign, sameValue } from "./creativeProduction";
type CreativePatchPreview = Awaited<ReturnType<typeof import("./api").previewCreativePatch>>;
import { inspectBranchCombination } from "./branchCombinationReview";
import type { BranchDraft, BranchPending } from "./branchDraft";
import type { BranchCombination, BranchCombinationField, BranchCombinationPick, BranchState, CanvasNode, Project } from "./types";

export interface BranchCombinationDraft {
  candidateId: string; donorBranchId: string; sceneId: string; rationale: string;
  picks: BranchCombinationPick[];
  completed: { revision: number; sequence: number } | null;
}
export type BranchCombinationCapability =
  | { status: "unavailable"; reason: string }
  | { status: "available"; preview(project: Project, input: BranchCombination): Promise<unknown> };
export const unavailableBranchCombination: BranchCombinationCapability = {
  status: "unavailable", reason: "This runtime has not admitted canonical branch combination previews.",
};
/** A live canonical response and its complete observed Project. Never restored as authority. */
export interface PreparedBranchCombination {
  source: Project; sequence: number; input: BranchCombination; preview: CreativePatchPreview;
}
const clone = <T,>(value: T): T => structuredClone(value);
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(v)
  && v !== "00000000-0000-0000-0000-000000000000";
const integer = (v: unknown): v is number => Number.isSafeInteger(v) && Number(v) >= 0;
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === "object" && !Array.isArray(v);
const exact = (v: Record<string, unknown>, fields: string[]) => Object.keys(v).length === fields.length && fields.every(k => Object.hasOwn(v,k));
const fields: BranchCombinationField[] = ["transform", "style", "motion"];
const scope = (p: Project) => JSON.stringify([p.id,p.generation,p.active_branch]);
function nextSequence(draft: BranchDraft) {
  if (!integer(draft.sequence) || draft.sequence === Number.MAX_SAFE_INTEGER) throw new Error("The branch edit sequence cannot advance safely.");
  return draft.sequence + 1;
}
function picks(value: unknown, maximum: number): value is BranchCombinationPick[] {
  return Array.isArray(value) && value.length <= maximum && value.every(pick => object(pick)
    && exact(pick,["node_id","field"]) && uuid(pick.node_id) && fields.includes(pick.field as BranchCombinationField));
}
export function validateCombinationDraft(value: unknown): asserts value is BranchCombinationDraft {
  if (!object(value) || !exact(value,["candidateId","donorBranchId","sceneId","rationale","picks","completed"])
    || !uuid(value.candidateId) || typeof value.donorBranchId !== "string" || typeof value.sceneId !== "string"
    || typeof value.rationale !== "string" || !picks(value.picks,512)
    || (value.completed !== null && (!object(value.completed) || !exact(value.completed,["revision","sequence"])
      || !integer(value.completed.revision) || !integer(value.completed.sequence))))
    throw new Error("The retained combination input is invalid.");
}
export function validateCombinationPending(value: unknown, draft: BranchDraft, pending: BranchPending): asserts value is BranchCombination {
  const owner: unknown = JSON.parse(draft.scope);
  if (!draft.combination || !Array.isArray(owner) || !object(value)
    || !exact(value,["source","base_branch_id","base_head_revision","donor_branch_id","donor_head_revision","candidate_branch_id","candidate_name","scene_id","picks","rationale"])
    || !object(value.source) || !exact(value.source,["resource","generation","revision"])
    || value.source.resource !== "project:"+owner[0] || value.source.generation !== owner[1]
    || value.source.revision !== pending.revision || value.base_branch_id !== owner[2]
    || !integer(value.base_head_revision) || value.base_head_revision > pending.revision
    || !uuid(value.donor_branch_id) || value.donor_branch_id === value.base_branch_id
    || !integer(value.donor_head_revision) || value.donor_head_revision > pending.revision
    || value.candidate_branch_id !== draft.combination.candidateId || value.candidate_name !== pending.name
    || !uuid(value.scene_id) || !picks(value.picks,64) || value.picks.length === 0
    || new Set(value.picks.map(pick=>pick.node_id+":"+pick.field)).size !== value.picks.length
    || typeof value.rationale !== "string" || !value.rationale.trim())
    throw new Error("The retained combination attempt is invalid.");
}
export function beginBranchCombination(draft: BranchDraft, project: Project, candidateId: string): BranchDraft {
  if (draft.scope !== scope(project) || draft.pending?.outcome === "UNKNOWN") throw new Error("Keep the original branch attempt before starting another.");
  if (!uuid(candidateId) || project.branches.some(branch=>branch.id===candidateId)
    || candidateId === draft.combination?.candidateId) throw new Error("An unused candidate identity is required.");
  const combination: BranchCombinationDraft = draft.combination
    ? { ...clone(draft.combination), candidateId, completed:null }
    : { candidateId,donorBranchId:"",sceneId:"",rationale:"",picks:[],completed:null };
  return { ...draft,baseRevision:project.revision,sequence:nextSequence(draft),
    name:draft.combination?draft.name:"",pending:null,combination };
}
export function editBranchCombination(draft: BranchDraft, update: Partial<Pick<BranchCombinationDraft,"donorBranchId"|"sceneId"|"rationale"|"picks">>): BranchDraft {
  if (!draft.combination) throw new Error("Start a combination before choosing its fields.");
  const combination={...draft.combination,...clone(update)}; validateCombinationDraft(combination);
  return {...draft,sequence:nextSequence(draft),combination};
}
export function combinationInput(draft: BranchDraft, project: Project): BranchCombination {
  if (project.state !== "current") throw new Error("Read a current saved project before combining alternatives.");
  if (draft.scope!==scope(project) || draft.baseRevision!==project.revision) throw new Error("Review the current source before combining alternatives.");
  if (!draft.combination || draft.combination.completed) throw new Error("Start a separate candidate before another combination.");
  if (draft.pending?.outcome === "UNKNOWN") throw new Error("The original combination outcome remains UNKNOWN.");
  return inspectBranchCombination(project,project,{...draft.combination,name:draft.name}).change.input;
}
function transfer(node: CanvasNode, donor: CanvasNode, choice: BranchCombinationField): CanvasNode {
  if (choice==="style") return {...node,style:clone(donor.style)};
  if (choice==="motion") return {...node,keyframes:clone(donor.keyframes)};
  return {...node,x:donor.x,y:donor.y,width:donor.width,height:donor.height,rotation_deg:donor.rotation_deg,opacity:donor.opacity};
}
function sumTime(a:{num:string;den:string},b:{num:string;den:string}) {
  let n=BigInt(a.num)*BigInt(b.den)+BigInt(b.num)*BigInt(a.den),d=BigInt(a.den)*BigInt(b.den);
  let x=n<0n?-n:n,y=d; while(y){const next=x%y;x=y;y=next;} n/=x||1n;d/=x||1n;
  return {num:String(n),den:String(d)};
}
/** Verifies a real server response; this function neither obtains nor authorizes it. */
export function retainCanonicalCombinationPreview(draft: BranchDraft, project: Project, input: BranchCombination, response: unknown): PreparedBranchCombination {
  if (!sameValue(input,combinationInput(draft,project))) throw new Error("The choices changed while the source preview was prepared.");
  const scene=project.scenes.find(s=>s.id===input.scene_id)!;
  const donor=project.branch_workspaces.find(w=>w.branch_id===input.donor_branch_id)!.current_state.scenes.find(s=>s.id===input.scene_id)!;
  const ids=new Set(input.picks.map(p=>p.node_id));
  const before=scene.nodes.filter(node=>ids.has(node.id));
  const after=before.map(node=>input.picks.filter(p=>p.node_id===node.id).reduce((result,pick)=>
    transfer(result,donor.nodes.find(n=>n.id===node.id)!,pick.field),clone(node)));
  const expected:CreativePatchPreview={source_revision:project.revision,scene_id:scene.id,before,after,
    dirty_start:scene.start,dirty_end:sumTime(scene.start,scene.duration),kind:"semantic_diff_full_scene_invalidation_not_pixel_verification"};
  if (!sameValue(response,expected)) throw new Error("The canonical source preview differs from the exact chosen fields. Keep the draft and inspect the source again.");
  return {source:clone(project),sequence:draft.sequence,input:clone(input),preview:clone(expected)};
}
export function combinationPreviewMatches(draft: BranchDraft, project: Project, prepared: PreparedBranchCombination | null): boolean {
  if (!prepared || prepared.sequence!==draft.sequence || !sameValue(prepared.source,project)) return false;
  try {return sameValue(prepared.input,combinationInput(draft,project));} catch {return false;}
}
export function prepareCombinationSubmission(draft: BranchDraft, project: Project, attemptId: string, prepared: PreparedBranchCombination) {
  if (!uuid(attemptId) || !combinationPreviewMatches(draft,project,prepared)) throw new Error("Read the canonical preview for these exact choices before saving.");
  const input=clone(prepared.input);
  const pending:BranchPending={id:attemptId,revision:project.revision,sequence:draft.sequence,name:input.candidate_name,
    priorIds:project.branches.map(branch=>branch.id),outcome:"UNKNOWN",diagnostic:null,combination:input};
  return {draft:{...draft,pending},change:{type:"combine_creative_branches" as const,input}};
}
function branchState(project: Project): BranchState {
  return clone({scenes:project.scenes,markers:project.markers,locks:project.locks,deliverables:project.deliverables,
    brief:project.brief,narrative:project.narrative,audio:project.audio,visual_language:project.visual_language,
    proposal_sets:project.proposal_sets,model_invocations:project.model_invocations,
    production_design:project.production_design??emptyProductionDesign()});
}
export function acknowledgeCombinedBranchDraft(draft: BranchDraft, project: Project, prepared: PreparedBranchCombination | undefined): BranchDraft {
  const pending=draft.pending, input=pending?.combination;
  if (!pending || !input || !draft.combination || !prepared || prepared.sequence!==pending.sequence
    || !sameValue(prepared.input,input) || prepared.source.revision!==pending.revision || scope(project)!==draft.scope
    || project.revision!==pending.revision+1) throw new Error("The original combination reply was not confirmed. Its input remains UNKNOWN.");
  const source=prepared.source, added=project.branches.filter(branch=>!pending.priorIds.includes(branch.id)), candidate=added[0];
  if (added.length!==1 || !object(candidate)
    || !exact(candidate,["id","name","parent_branch","base_revision","head_revision","protected","created_at"])
    || candidate.id!==input.candidate_branch_id || candidate.name!==input.candidate_name
    || candidate.parent_branch!==source.active_branch || candidate.base_revision!==source.revision
    || candidate.head_revision!==project.revision || candidate.protected || !candidate.created_at)
    throw new Error("The original reply did not identify the exact derived branch.");
  const workspace=project.branch_workspaces.find(w=>w.branch_id===candidate.id),base=branchState(source),expected=clone(base);
  const scene=expected.scenes.find(s=>s.id===input.scene_id)!;
  for(const node of prepared.preview.after){const index=scene.nodes.findIndex(n=>n.id===node.id);scene.nodes[index]=clone(node);}
  scene.status="draft";
  const record=workspace?.current_state.production_design?.patches?.at(-1);
  const previous=base.production_design?.patches??[];
  if (!record || !object(record)
    || !exact(record,["id","scene_id","source_revision","rationale","before","after","reverts"])
    || !uuid(record.id) || previous.some(item=>item.id===record.id)
    || record.scene_id!==input.scene_id || record.source_revision!==source.revision || record.rationale!==input.rationale
    || record.reverts!==null || !sameValue(record.before,prepared.preview.before) || !sameValue(record.after,prepared.preview.after))
    throw new Error("The derived branch did not retain its exact reversible change.");
  expected.production_design!.patches=[...previous,clone(record)].slice(-64);
  if (!workspace || !object(workspace)
    || !exact(workspace,["branch_id","base_revision","base_state","current_state"])
    || workspace.base_revision!==source.revision || !sameValue(workspace.base_state,base)
    || !sameValue(workspace.current_state,expected)) throw new Error("The derived branch differs outside the reviewed fields.");
  const normalized=clone(project);
  normalized.revision=source.revision;normalized.updated_at=source.updated_at;
  if (source.schema_version===1 && normalized.schema_version===2) normalized.schema_version=1;
  normalized.branches=normalized.branches.filter(branch=>branch.id!==candidate.id);
  const oldHead=source.branches.find(branch=>branch.id===source.active_branch)!.head_revision;
  const active=normalized.branches.find(branch=>branch.id===source.active_branch)!;
  if(active.head_revision!==project.revision) throw new Error("The source branch revision was not committed as expected.");
  active.head_revision=oldHead;
  normalized.branch_workspaces=normalized.branch_workspaces.filter(w=>w.branch_id!==candidate.id);
  if (!sameValue(normalized,source)) throw new Error("The original reply changed a parent or unrelated project state.");
  // The stable identity remains consumed. A subsequent candidate requires the
  // explicit start-another action; later raw names, reasons and choices survive.
  return {...draft,baseRevision:project.revision,pending:null,combination:{...draft.combination,
    completed:{revision:project.revision,sequence:pending.sequence}}};
}
export function separateCombinedBranchDraft(draft: BranchDraft, newId: string | undefined): Pick<BranchDraft,"name"|"combination"> {
  if (!draft.combination || !draft.pending?.combination || !uuid(newId) || newId===draft.combination.candidateId)
    throw new Error("The separate combination needs its own stable identity.");
  const later=draft.sequence!==draft.pending.sequence;
  return {name:later?draft.name:"",combination:{...draft.combination,candidateId:newId,completed:null,
    picks:later?clone(draft.combination.picks):[],rationale:later?draft.combination.rationale:""}};
}
