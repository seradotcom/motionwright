/** Deterministic source review of two ALREADY EDITABLE creative branches.
 *
 * The report does not infer a successful native render, visual taste,
 * authenticity, rights, distillation or branch-merge safety. Manual
 * positive/negative judgements remain ordinary revision-anchored reviews.
 */
import type { BranchState, CanvasNode, Claim, ClaimEditableFields, Project, Scene } from "./types";

export type CreativeDifferenceKind =
  | "scene_added" | "scene_omitted" | "scene_copy" | "scene_timing"
  | "scene_renderer" | "scene_beats" | "scene_camera"
  | "node_added" | "node_omitted" | "node_copy" | "node_geometry"
  | "node_style" | "node_motion" | "node_structure"
  | "brief" | "narrative" | "audio" | "visual_language"
  | "deliverables" | "production_design"
  | "claim_added" | "claim_omitted" | "claim_changed";
export interface CreativeDifference {
  kind: CreativeDifferenceKind;
  scene_id: string | null;
  node_id: string | null;
  summary: string;
  claim_id?: string;
  changed_fields?: Array<keyof ClaimEditableFields>;
  source_comparison?: {
    active: Pick<Claim, "source" | "source_revision">;
    candidate: Pick<Claim, "source" | "source_revision">;
  };
}
export interface CreativeAlternativeReport {
  schema: "motionwright.v05-creative-alternatives/1";
  project_id: string;
  generation: string;
  observed_revision: number;
  active_branch_id: string;
  active_head_revision: number;
  active_base_revision: number;
  candidate_branch_id: string;
  candidate_head_revision: number;
  candidate_base_revision: number;
  verdict: "DIFFERENCES_PRESENT" | "NO_AUTHORED_DIFFERENCE" | "NOT_ASSESSED";
  differences: CreativeDifference[];
  count_by_kind: Partial<Record<CreativeDifferenceKind,number>>;
  active_scene_count: number;
  candidate_scene_count: number;
  active_object_count: number;
  candidate_object_count: number;
  current_brief_vs_candidate: boolean;
  observed_rendered_pixels: false;
  human_approval_recorded: false;
  native_sdk_authority_changed: false;
}
const MAX_SCENES=512,MAX_NODES=16_384,MAX_DIFFS=4_096;
function same(left: unknown,right: unknown):boolean {
  if(left===right)return true;
  if(left==null||right==null||typeof left!=="object"||typeof right!=="object")
    return false;
  if(Array.isArray(left)||Array.isArray(right)) {
    return Array.isArray(left)&&Array.isArray(right)&&left.length===right.length&&
      left.every((value,i)=>same(value,right[i]));
  }
  const l=left as Record<string,unknown>, r=right as Record<string,unknown>;
  const keys=Object.keys(l);
  return keys.length===Object.keys(r).length &&
    keys.every(key=>Object.hasOwn(r,key)&&same(l[key],r[key]));
}
function nodeCounts(scenes:Scene[]):number {
  return scenes.reduce((total,scene)=>total+scene.nodes.length,0);
}
function sourceState(project:Project):BranchState {
  return {
    scenes:project.scenes,markers:project.markers,locks:project.locks,
    deliverables:project.deliverables,brief:project.brief,
    narrative:project.narrative,audio:project.audio,
    visual_language:project.visual_language,
    proposal_sets:project.proposal_sets,model_invocations:project.model_invocations,
    production_design:project.production_design,
  };
}
export function inspectCreativeAlternative(
  project:Project,candidateBranchId:string,
):CreativeAlternativeReport {
  const active=project.branches.find(b=>b.id===project.active_branch);
  if(!active)throw new Error("The active creative branch metadata is unavailable.");
  if(active.base_revision>project.revision || active.head_revision>project.revision)
    throw new Error("Active branch has a future revision; source cannot be trusted.");
  const candidate=project.branches.find(b=>b.id===candidateBranchId);
  if(!candidate||candidate.id===project.active_branch)
    throw new Error("Select a different saved creative branch.");
  const workspace=project.branch_workspaces.find(w=>w.branch_id===candidateBranchId);
  if(!workspace)throw new Error("The alternative branch has no preserved project state.");
  if(workspace.base_revision!==candidate.base_revision)
    throw new Error("Alternative branch metadata and saved base revision disagree.");
  if(workspace.base_revision>project.revision || candidate.head_revision>project.revision)
    throw new Error("Alternative branch has a future revision; source cannot be trusted.");
  const current=sourceState(project),other=workspace.current_state;
  if(current.scenes.length>MAX_SCENES || other.scenes.length>MAX_SCENES ||
     nodeCounts(current.scenes)>MAX_NODES || nodeCounts(other.scenes)>MAX_NODES)
    throw new Error("The creative alternative comparison exceeds its bounded source budget.");
  // BranchState is normally validated by the Rust project domain. Browser
  // preview must still fail closed for forged duplicate identities instead
  // of silently reducing a scene/object list through a Map overwrite.
  for(const state of [current,other]) {
    if(state.brief.claims.length>MAX_DIFFS)
      throw new Error("The creative claim comparison exceeds its bounded source budget.");
    const claims=new Set<string>();
    for(const claim of state.brief.claims) {
      if(claims.has(claim.id))throw new Error("Duplicate claim identity in saved branch state.");
      claims.add(claim.id);
    }
    const scenes=new Set<string>(),nodes=new Set<string>();
    for(const scene of state.scenes) {
      if(scenes.has(scene.id)) throw new Error("Duplicate scene identity in saved branch state.");
      scenes.add(scene.id);
      const localIds=new Set<string>();
      for(const node of scene.nodes) {
        if(localIds.has(node.id) || nodes.has(node.id))
          throw new Error("Duplicate editable object identity in saved branch state.");
        localIds.add(node.id);nodes.add(node.id);
      }
    }
  }
  const differences:CreativeDifference[]=[];
  const counts:Partial<Record<CreativeDifferenceKind,number>>={};
  const add=(kind:CreativeDifferenceKind,summary:string,scene_id:string|null=null,node_id:string|null=null,
    detail:Pick<CreativeDifference,"claim_id"|"changed_fields"|"source_comparison">={})=>{
    if(differences.length>=MAX_DIFFS)throw new Error("The branch edit delta exceeds the bounded review budget.");
    differences.push({kind,summary,scene_id,node_id,...detail});
    counts[kind]=(counts[kind]??0)+1;
  };
  const byId=(scenes:Scene[])=>new Map(scenes.map(scene=>[scene.id,scene]));
  const sourceScenes=byId(current.scenes),candidateScenes=byId(other.scenes);
  for(const [id,scene] of sourceScenes) {
    const variant=candidateScenes.get(id);
    if(!variant){add("scene_omitted","Source scene absent from alternative",id);continue;}
    if(!same([scene.name,scene.objective],[variant.name,variant.objective]))
      add("scene_copy","Authored scene title or editorial objective differs",id);
    if(!same([scene.start,scene.duration],[variant.start,variant.duration]))
      add("scene_timing","Exact rational scene timing differs",id);
    if(!same([scene.renderer,scene.status],[variant.renderer,variant.status]))
      add("scene_renderer","Renderer assignment or review status differs",id);
    if(!same(scene.beats,variant.beats))
      add("scene_beats","Scene-local beat structure or intent differs",id);
    if(!same(scene.camera,variant.camera))
      add("scene_camera","Scene camera pose/safe-margin differs",id);
    const originalNodes=new Map(scene.nodes.map(node=>[node.id,node]));
    const candidateNodes=new Map(variant.nodes.map(node=>[node.id,node]));
    for(const [nodeId,node] of originalNodes){
      const otherNode=candidateNodes.get(nodeId);
      if(!otherNode){add("node_omitted","An existing editable object is absent from this alternative",id,nodeId);continue;}
      inspectNode(id,nodeId,node,otherNode,add);
    }
    for(const [nodeId] of candidateNodes)
      if(!originalNodes.has(nodeId))
        add("node_added","An alternative-only editable object was created",id,nodeId);
  }
  for(const [id] of candidateScenes)
    if(!sourceScenes.has(id))add("scene_added","Alternative-only source scene was created",id);
  const currentClaims=new Map(current.brief.claims.map(claim=>[claim.id,claim]));
  const candidateClaims=new Map(other.brief.claims.map(claim=>[claim.id,claim]));
  const claimFields=["text","source","context","source_revision"] as const;
  for(const [id,claim] of currentClaims) {
    const alternative=candidateClaims.get(id);
    if(!alternative) {
      add("claim_omitted","An existing claim is absent from this alternative",null,null,{claim_id:id});
      continue;
    }
    const fields=claimFields.filter(field=>!same(claim[field],alternative[field]));
    if(!fields.length)continue;
    const sourceChanged=fields.includes("source")||fields.includes("source_revision");
    add("claim_changed","The identified claim has changed authored fields",null,null,{
      claim_id:id,changed_fields:fields,
      ...(sourceChanged?{source_comparison:{
        active:structuredClone({source:claim.source,source_revision:claim.source_revision}),
        candidate:structuredClone({source:alternative.source,source_revision:alternative.source_revision}),
      }}:{}),
    });
  }
  for(const [id] of candidateClaims)
    if(!currentClaims.has(id))add("claim_added","An alternative-only claim was created",null,null,{claim_id:id});
  const currentClaimOrder=current.brief.claims.filter(claim=>candidateClaims.has(claim.id)).map(claim=>claim.id);
  const candidateClaimOrder=other.brief.claims.filter(claim=>currentClaims.has(claim.id)).map(claim=>claim.id);
  if(!same(currentClaimOrder,candidateClaimOrder))
    add("brief","The order of existing claim identities differs between branches");
  const briefFields=["objective","audience","constraints","exclusions"] as const;
  if(briefFields.some(field=>!same(current.brief[field],other.brief[field])))
    add("brief","The editable brief state outside individual claims differs between branches");
  for(const field of ["narrative","audio","visual_language","deliverables","production_design"] as const)
    if(!same(current[field],other[field]))
      add(field, "The editable "+field.replaceAll("_"," ")+" state differs between branches");
  return {
    schema:"motionwright.v05-creative-alternatives/1",
    project_id:project.id,generation:project.generation,
    observed_revision:project.revision,
    active_branch_id:project.active_branch,candidate_branch_id: candidate.id,
    active_head_revision:active.head_revision,
    active_base_revision:active.base_revision,
    candidate_head_revision:candidate.head_revision,
    candidate_base_revision:workspace.base_revision,
    verdict:differences.length?"DIFFERENCES_PRESENT":"NO_AUTHORED_DIFFERENCE",
    differences,count_by_kind:counts,
    active_scene_count:current.scenes.length,
    candidate_scene_count:other.scenes.length,
    active_object_count:nodeCounts(current.scenes),
    candidate_object_count:nodeCounts(other.scenes),
    current_brief_vs_candidate:!same(current.brief,other.brief),
    observed_rendered_pixels:false,
    human_approval_recorded:false,
    native_sdk_authority_changed:false,
  };
}
function inspectNode(
  sceneId:string,nodeId:string,node:CanvasNode,other:CanvasNode,
  add:(kind:CreativeDifferenceKind,summary:string,scene_id:string|null,node_id:string|null)=>void,
){
  if(!same([node.name,node.text,node.kind],[other.name,other.text,other.kind]))
    add("node_copy","Editable object name/text/type differs",sceneId,nodeId);
  if(!same([node.x,node.y,node.width,node.height,node.rotation_deg,node.opacity],[other.x,other.y,other.width,other.height,other.rotation_deg,other.opacity]))
    add("node_geometry","Editable object position, bounds, rotation or opacity differs",sceneId,nodeId);
  if(!same(node.style,other.style))
    add("node_style","Editable object fill/stroke/typography/blend differs",sceneId,nodeId);
  if(!same(node.keyframes,other.keyframes))
    add("node_motion","Source keyframe timing/value/interpolation differs",sceneId,nodeId);
  if(!same([node.coordinate_space,node.parent_id,node.z_index,node.relations,node.property_locks],
           [other.coordinate_space,other.parent_id,other.z_index,other.relations,other.property_locks]))
    add("node_structure","Object identity graph, layer order, relations or locks differ",sceneId,nodeId);
}
