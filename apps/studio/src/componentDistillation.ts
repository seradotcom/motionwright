import type { CanvasNode, Change, Project, Scene } from "./types";
import { emptyProductionDesign } from "./creativeProduction";

/** A source-backed proposal is not executable code or a component admission. */
export const DISTILLATION_SCHEMA="motionwright.component-distillation/1";
export const DISTILLATION_RENDERER="motion_canvas_original_nodes_v1";
export type DistillationRejection="non_motion_canvas"|"locked_object"|"unsupported_kind";
export type DistillationVerdict="compatible_proposal"|"rejected";
export interface DistillationRole {key:string;kind:string}
export interface DistillationSelector {scene_id:string;node_ids:string[]}
export interface DistillationNegative {
  scene_id:string;node_id:string|null;rejection:DistillationRejection
}
export interface ComponentDistillationInput {
  id:string;label:string;author:string;original_rights_attestation:string;
  source_project_id:string;source_generation:string;source_revision:number;
  roles:DistillationRole[];positives:DistillationSelector[];
  negatives:DistillationNegative[];requirements:string[];
}
export interface DistillationSample {scene_id:string;nodes:CanvasNode[]}
export interface DistillationReview {
  reviewer:string;rationale:string;verdict:DistillationVerdict;
  source_revision:number;compatibility:string;
}
export interface ComponentDistillationProposal {
  schema:string;input:ComponentDistillationInput;
  samples:DistillationSample[];review:DistillationReview|null;
}
export type DistillationChange=Extract<Change,{
  type:"propose_component_distillation"|"review_component_distillation"
}>;
const validKey=/^[a-z][a-z0-9_]{1,31}$/u;
const allowedKinds=new Set(["text","rectangle","circle"]);
function reject(message:string):never{throw new Error("Component distillation: "+message);}
function exact(value:object,keys:readonly string[]):void{
  if(Object.keys(value).sort().join("|")!==[...keys].sort().join("|"))
    reject("unrecognized or missing candidate schema fields; executable source is not admitted");
}
const bounded=(value:string,max:number):boolean=>
  typeof value==="string" && !!value.trim() && new TextEncoder().encode(value).length<=max &&
  !/\p{Cc}/u.test(value);
function freshCanvasNode(node:CanvasNode):boolean{
  return !node.parent_id && !node.relations.length &&
    !node.property_locks.length && node.keyframes.length<=8;
}
function roleSignature(node:CanvasNode):string{
  return JSON.stringify([node.kind,node.text,node.x,node.y,node.width,
    node.height,node.opacity,node.style,node.keyframes]);
}
function sceneNode(project:Project,sceneId:string,nodeId:string):CanvasNode|undefined{
  return project.scenes.find(s=>s.id===sceneId)?.nodes.find(node=>node.id===nodeId);
}
export function validateDistillationProposal(value:ComponentDistillationProposal):void{
  exact(value,["schema","input","samples","review"]);
  const {input,samples,review}=value;
  exact(input,["id","label","author","original_rights_attestation",
    "source_project_id","source_generation","source_revision","roles",
    "positives","negatives","requirements"]);
  for(const role of input.roles)exact(role,["key","kind"]);
  for(const selector of input.positives)exact(selector,["scene_id","node_ids"]);
  for(const negative of input.negatives)exact(negative,["scene_id","node_id","rejection"]);
  for(const sample of samples)exact(sample,["scene_id","nodes"]);
  if(review)exact(review,["reviewer","rationale","verdict","source_revision","compatibility"]);
  if(value.schema!==DISTILLATION_SCHEMA || !input.id ||
    !input.source_project_id || !input.source_generation ||
    !Number.isSafeInteger(input.source_revision) || input.source_revision<0 ||
    !bounded(input.label,120) || !bounded(input.author,120) ||
    !bounded(input.original_rights_attestation,480) ||
    input.roles.length<2 || input.roles.length>12 ||
    input.positives.length<2 || input.positives.length>4 ||
    input.negatives.length<1 || input.negatives.length>4 ||
    input.requirements.length<1 || input.requirements.length>12 ||
    samples.length!==input.positives.length)
    reject("invalid original-content evidence budgets or authority");
  const roles=new Set<string>();
  for(const role of input.roles){
    if(!validKey.test(role.key) || !allowedKinds.has(role.kind) || roles.has(role.key))
      reject("roles must be distinct, supported original Canvas semantics");
    roles.add(role.key);
  }
  if(new Set(input.requirements).size!==input.requirements.length ||
    input.requirements.some(req=>!bounded(req,240)))
    reject("requirements must be distinct original-owner declarations");
  const scenes=new Set<string>(),variations=new Set<string>();
  for(let i=0;i<samples.length;i++){
    const selector=input.positives[i],sample=samples[i];
    if(scenes.has(selector.scene_id) || sample.scene_id!==selector.scene_id ||
      selector.node_ids.length!==input.roles.length || sample.nodes.length!==input.roles.length)
      reject("positive source samples need distinct scenes and all declared roles");
    scenes.add(selector.scene_id);
    const ids=new Set<string>();
    const normalized:string[]=[];
    for(let n=0;n<input.roles.length;n++){
      const node=sample.nodes[n];
      if(!node || node.id!==selector.node_ids[n] || ids.has(node.id) ||
        node.kind!==input.roles[n].kind || !freshCanvasNode(node))
        reject("original source contains locked, related or incompatible Canvas objects");
      ids.add(node.id);
      normalized.push(roleSignature(node));
    }
    const semantic=JSON.stringify(normalized);
    if(variations.has(semantic))reject("positive source variation requires genuinely different authored examples");
    variations.add(semantic);
  }
  const negativeIds=new Set<string>();
  for(const example of input.negatives){
    const key=example.scene_id+":"+(example.node_id??"");
    if(scenes.has(example.scene_id) || negativeIds.has(key) ||
      (example.rejection==="non_motion_canvas")!==(example.node_id===null))
      reject("negative examples must come from independent rejected source scenes");
    negativeIds.add(key);
  }
  if(review && (!bounded(review.reviewer,120) ||
    !bounded(review.rationale,1600) ||
    review.reviewer.toLowerCase()===input.author.toLowerCase() ||
    review.source_revision!==input.source_revision ||
    review.compatibility!==DISTILLATION_RENDERER ||
    !["compatible_proposal","rejected"].includes(review.verdict)))
    reject("review must be authored independently and bound to frozen source revision");
}
export function validateDistillations(project:Project):void{
  const proposals=project.production_design?.distillations??[];
  if(proposals.length>32)reject("proposal collection budget exceeded");
  const ids=new Set<string>();
  for(const proposal of proposals){
    validateDistillationProposal(proposal);
    if(ids.has(proposal.input.id) ||
      proposal.input.source_project_id!==project.id ||
      proposal.input.source_revision>project.revision)
      reject("candidate identity or project source mismatch");
    ids.add(proposal.input.id);
  }
}
function assertContentUnlocked(project:Project,sceneId?:string):void{
  if(project.locks.some(lock=>
    (lock.resource==="project:"+project.id ||
      (sceneId!==undefined && lock.resource==="scene:"+sceneId)) &&
    lock.kind==="content"))
    reject("source content is locked");
}
function compatibleScene(scene:Scene|undefined):scene is Scene{
  return !!scene && scene.renderer==="motion-canvas";
}
export function applyDistillationChange(project:Project,change:DistillationChange):void{
  project.production_design??=emptyProductionDesign();
  const proposals=project.production_design.distillations??[];
  assertContentUnlocked(project);
  if(change.type==="propose_component_distillation"){
    const input=change.input;
    if(proposals.length>=32 || proposals.some(p=>p.input.id===input.id))
      reject("proposal capacity exceeded or repeated immutable ID");
    if(input.source_project_id!==project.id ||
      input.source_generation!==project.generation ||
      input.source_revision!==project.revision)
      reject("source must match current project generation and revision");
    const samples=input.positives.map(selector=>{
      assertContentUnlocked(project,selector.scene_id);
      const scene=project.scenes.find(s=>s.id===selector.scene_id);
      if(!compatibleScene(scene))reject("positive sources require Motion Canvas scenes");
      const nodes=selector.node_ids.map(id=>{
        const node=sceneNode(project,selector.scene_id,id);
        if(!node)reject("positive source node missing");
        return structuredClone(node);
      });
      return {scene_id:selector.scene_id,nodes};
    });
    for(const negative of input.negatives){
      const scene=project.scenes.find(s=>s.id===negative.scene_id);
      if(!scene)reject("negative source scene missing");
      const node=negative.node_id?sceneNode(project,negative.scene_id,negative.node_id):undefined;
      const actuallyRejected=negative.rejection==="non_motion_canvas"?
        negative.node_id===null && scene.renderer!=="motion-canvas":
        negative.rejection==="locked_object"?
        !!node && node.property_locks.length>0:
        negative.rejection==="unsupported_kind"?!!node && !allowedKinds.has(node.kind):false;
      if(!actuallyRejected)reject("negative case is not actually rejected by the source grammar");
    }
    const proposal:ComponentDistillationProposal={
      schema:DISTILLATION_SCHEMA,input:structuredClone(input),samples,review:null,
    };
    validateDistillationProposal(proposal);
    project.production_design.distillations=[...proposals,proposal];
  }else{
    const candidate=proposals.find(item=>item.input.id===change.id);
    if(!candidate)reject("unknown source candidate");
    if(candidate.review)reject("frozen proposal can be reviewed only once");
    candidate.review=structuredClone(change.review);
    validateDistillationProposal(candidate);
  }
}
