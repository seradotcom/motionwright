/** Versioned, data-only creative playbooks and deterministic scene observations.
 * Never execute bundle text as code/instructions or equate structural PASS
 * with a human editor's aesthetic, legal, or creative approval.
 */
import { emptyProductionDesign } from "./creativeProduction";
import type { Project, Scene, Change, RationalTime } from "./types";

export const EVALUATOR_SCHEMA="motionwright.creative-evaluator/1";
export const REPORT_SCHEMA="motionwright.creative-evaluation/1";

export type CreativeCriterion =
  | {kind:"minimum_nodes";id:string;minimum:number}
  | {kind:"text_contains";id:string;phrase:string}
  | {kind:"project_pixel_safe_area";id:string;margin_permille:number}
  | {kind:"human_question";id:string;question:string};
export interface CreativeEvaluatorBundle {
  schema:string;id:string;version:number;label:string;purpose:string;
  author_attestation:string;playbook:string[];criteria:CreativeCriterion[];
}
export type CriterionOutcome="pass"|"fail"|"not_assessed";
export type TechnicalConclusion="pass_technical_only"|"fail"|"needs_human_review";
export interface CriterionObservation {
  criterion_id:string;outcome:CriterionOutcome;explanation:string;
}
export type CreativeStyleKind="repetition"|"density"|"motion_purpose";
export interface CreativeStyleAdvisory {
  kind:CreativeStyleKind;scene_id:string;
  /** Exact half-open interval relative to the saved scene. */
  interval_start:RationalTime;interval_end:RationalTime;
  source_node_ids:string[];rationale:string;suggested_action:string;
  human_review_required:boolean;
}
export interface CreativeEvaluationReport {
  schema:string;id:string;evaluator_id:string;evaluator_version:number;
  evaluator_sha256:string;scene_id:string;source_generation:string;
  source_revision:number;observations:CriterionObservation[];
  /** Historical source reports without this optional field remain unchanged. */
  style_advisories?:CreativeStyleAdvisory[];
  conclusion:TechnicalConclusion;creative_approval:string;
}
export type EvaluatorChange=Extract<Change,{type:"register_creative_evaluator"|"evaluate_creative_scene"}>;
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const digestPattern=/^[0-9a-f]{64}$/;
const badControl=/\p{Cc}/u;
const deny=(message:string):never=>{throw new Error(message);};
function strictKeys(value:object,expected:string[]):void {
  const keys=Object.keys(value).sort(),wanted=[...expected].sort();
  if(keys.length!==wanted.length || keys.some((key,index)=>key!==wanted[index]))
    deny("Evaluator schema has unknown or missing fields.");
}
function bounded(value:unknown,budget:number,label:string):void {
  if(typeof value!=="string" || !value.trim() ||
    new TextEncoder().encode(value).length>budget || badControl.test(value))
    deny("Invalid bounded "+label+".");
}
export function validateEvaluator(bundle:CreativeEvaluatorBundle):void {
  if(!bundle || typeof bundle!=="object" || Array.isArray(bundle))
    deny("Creative evaluator must be a data-only object.");
  strictKeys(bundle,["schema","id","version","label","purpose","author_attestation","playbook","criteria"]);
  if(bundle.schema!==EVALUATOR_SCHEMA || !uuid.test(bundle.id) ||
    !Number.isSafeInteger(bundle.version) || bundle.version<1 ||
    !Array.isArray(bundle.playbook) || bundle.playbook.length<1 || bundle.playbook.length>12 ||
    !Array.isArray(bundle.criteria) || bundle.criteria.length<1 || bundle.criteria.length>16)
    deny("Evaluator schema/version or criteria budget invalid.");
  bounded(bundle.label,100,"label");
  bounded(bundle.purpose,280,"purpose");
  bounded(bundle.author_attestation,240,"author declaration");
  for(const line of bundle.playbook)bounded(line,500,"data-only playbook");
  const seen=new Set<string>();
  for(const criterion of bundle.criteria){
    if(!criterion || typeof criterion!=="object" || Array.isArray(criterion))
      deny("Evaluator criterion must be a typed object.");
    const keys=criterion.kind==="minimum_nodes"?["kind","id","minimum"]:
      criterion.kind==="text_contains"?["kind","id","phrase"]:
      criterion.kind==="project_pixel_safe_area"?["kind","id","margin_permille"]:
      criterion.kind==="human_question"?["kind","id","question"]:null;
    if(keys===null)throw new Error("Unknown executable evaluator operation.");
    strictKeys(criterion,keys);
    if(!uuid.test(criterion.id) || seen.has(criterion.id))
      deny("Evaluator criterion IDs must be unique.");
    seen.add(criterion.id);
    switch(criterion.kind){
      case "minimum_nodes":
        if(!Number.isInteger(criterion.minimum)||criterion.minimum<1||criterion.minimum>256)
          deny("Evaluator minimum node count out of bounds.");
        break;
      case "text_contains":bounded(criterion.phrase,120,"text criterion");break;
      case "project_pixel_safe_area":
        if(!Number.isInteger(criterion.margin_permille) ||
          criterion.margin_permille<0 || criterion.margin_permille>150)
          deny("Evaluator safe-area margin is out of bounds.");
        break;
      case "human_question":bounded(criterion.question,250,"human-review question");break;
    }
  }
}
/** Canonical Rust serde struct and internally-tagged criterion serialization
 * both use insertion order. Compute digest from an explicitly ordered clone.
 */
export async function evaluatorDigest(bundle:CreativeEvaluatorBundle):Promise<string>{
  validateEvaluator(bundle);
  const fields= (criterion:CreativeCriterion):CreativeCriterion=>{
    switch(criterion.kind){
      case "minimum_nodes":return {kind:criterion.kind,id:criterion.id,minimum:criterion.minimum};
      case "text_contains":return {kind:criterion.kind,id:criterion.id,phrase:criterion.phrase};
      case "project_pixel_safe_area":return {kind:criterion.kind,id:criterion.id,margin_permille:criterion.margin_permille};
      case "human_question":return {kind:criterion.kind,id:criterion.id,question:criterion.question};
    }
  };
  const canonical={
    schema:bundle.schema,id:bundle.id,version:bundle.version,label:bundle.label,
    purpose:bundle.purpose,author_attestation:bundle.author_attestation,
    playbook:bundle.playbook,criteria:bundle.criteria.map(fields),
  };
  const source=new TextEncoder().encode(EVALUATOR_SCHEMA+"\0"+JSON.stringify(canonical));
  const bytes=new Uint8Array(await crypto.subtle.digest("SHA-256",source));
  return Array.from(bytes,x=>x.toString(16).padStart(2,"0")).join("");
}
export function observeCriterion(scene:Scene,criterion:CreativeCriterion):CriterionObservation{
  let outcome:CriterionOutcome;
  let explanation:string;
  switch(criterion.kind){
    case "minimum_nodes":{
      const passed=scene.nodes.length>=criterion.minimum;
      outcome=passed?"pass":"fail";
      explanation="Authored nodes: "+scene.nodes.length+" (minimum "+criterion.minimum+")";
      break;
    }
    case "text_contains":{
      const passed=scene.nodes.some(node=>node.kind==="text" && (node.text??"").includes(criterion.phrase));
      outcome=passed?"pass":"fail";
      explanation=passed?"Required authored Canvas text is present":"Required authored Canvas text is absent";
      break;
    }
    case "project_pixel_safe_area":{
      if(!scene.nodes.length || scene.nodes.some(node=>node.coordinate_space!=="project_pixels")){
        outcome="not_assessed";
        explanation="No measurable original project-pixel geometry";
        break;
      }
      const xmargin=1920*criterion.margin_permille/1000;
      const ymargin=1080*criterion.margin_permille/1000;
      const passed=scene.nodes.every(node=>
        Number.isFinite(node.x)&&Number.isFinite(node.y)&&
        Number.isFinite(node.width)&&Number.isFinite(node.height)&&
        node.x>=xmargin&&node.y>=ymargin&&
        node.x+node.width<=1920-xmargin&&
        node.y+node.height<=1080-ymargin);
      outcome=passed?"pass":"fail";
      explanation=passed?
        "Authored project-pixel bounding boxes fit the technical 1920x1080 safe area":
        "At least one authored bounding box extends beyond the technical safe area";
      break;
    }
    case "human_question":
      outcome="not_assessed";
      explanation="Requires a human creative reviewer; automated acceptance is prohibited";
      break;
  }
  return {criterion_id:criterion.id,outcome,explanation};
}

/** Conservative authored-geometry/source checks, not rendered pixels or an
 * automatic verdict about artistic quality. They mirror the Rust evaluator.
 * Scene-relative rational intervals never use rounded UI timecodes.
 */
export function observeStyleAdvisories(scene:Scene):CreativeStyleAdvisory[]{
  const result:CreativeStyleAdvisory[]=[];
  const start:RationalTime={num:"0",den:"1"};
  const make=(kind:CreativeStyleKind,ids:string[],rationale:string,suggested_action:string,end=scene.duration):CreativeStyleAdvisory=>({
    kind,scene_id:scene.id,interval_start:start,interval_end:end,
    source_node_ids:ids.slice(0,24),rationale,suggested_action,human_review_required:true,
  });
  const repeated=new Map<string,string[]>();
  for(const node of scene.nodes){
    if(node.kind!=="text"||node.opacity<=0.01||!node.text||
      new TextEncoder().encode(node.text).length>512)continue;
    const value=node.text.trim().split(/\s+/u).join(" ").toLowerCase();
    if(value&&new TextEncoder().encode(value).length<=240)
      repeated.set(value,[...(repeated.get(value)??[]),node.id]);
  }
  for(const [,ids] of [...repeated.entries()].sort(([a],[b])=>a<b?-1:a>b?1:0)
    .filter(([,ids])=>ids.length>=3).slice(0,12)){
    result.push(make("repetition",ids,
      ids.length+" visible editable text nodes repeat identical normalized wording; this is a candidate pattern, not an aesthetic verdict.",
      "Review whether repetition serves the scene; vary or remove redundant copies if not."));
  }
  const visible:string[]=[];
  let area=0;
  for(const node of scene.nodes){
    if(node.coordinate_space!=="project_pixels"||node.opacity<=0.01)continue;
    const clamp=(value:number,max:number)=>Math.max(0,Math.min(value,max));
    const width=clamp(node.x+node.width,1920)-clamp(node.x,1920);
    const height=clamp(node.y+node.height,1080)-clamp(node.y,1080);
    if(width>0&&height>0){visible.push(node.id);area+=width*height;}
  }
  const index=area/(1920*1080);
  if(visible.length>=4&&(index>=1.2||(visible.length>=12&&index>=0.5)))
    result.push(make("density",visible,
      visible.length+" visible project-pixel bounds have baseline overdraw index "+index.toFixed(2)+
        "; animation and rendered pixels were not measured.",
      "Inspect hierarchy and overlaps; simplify competing visible objects where appropriate."));
  const compare=(left:RationalTime,right:RationalTime)=>{
    const difference=BigInt(left.num)*BigInt(right.den)-BigInt(right.num)*BigInt(left.den);
    return difference<0n?-1:difference>0n?1:0;
  };
  for(const node of scene.nodes){
    if(result.length>=32)break;
    const original:Record<string,number>={
      x:node.x,y:node.y,width:node.width,height:node.height,
      rotation_deg:node.rotation_deg,opacity:node.opacity,
    };
    const changed=node.keyframes.filter(keyframe=>
      Math.abs(keyframe.value-original[keyframe.property])>0.0001);
    if(!changed.length)continue;
    const end=changed.map(keyframe=>keyframe.at).filter(at=>compare(at,start)>0)
      .sort(compare)[0]??scene.duration;
    result.push(make("motion_purpose",[node.id],
      "A saved node animates an authored property, but the creative purpose of that motion is not machine-verifiable.",
      "Review its timed narrative purpose; explain the motion or remove redundant keyframes.",end));
  }
  return result;
}
function validAdvisoryTime(time:unknown):time is RationalTime{
  if(!time||typeof time!=="object"||Array.isArray(time))return false;
  const value=time as RationalTime;
  const integer=/^-?\d{1,20}$/;
  if(Object.keys(value).sort().join(",")!=="den,num"||
    typeof value.num!=="string"||typeof value.den!=="string"||
    !integer.test(value.num)||!integer.test(value.den))return false;
  const max=9223372036854775807n,min=-9223372036854775808n;
  const n=BigInt(value.num),d=BigInt(value.den);
  return n>=min&&n<=max&&d>0n&&d<=max;
}
function beforeTime(left:RationalTime,right:RationalTime):boolean{
  return BigInt(left.num)*BigInt(right.den)<BigInt(right.num)*BigInt(left.den);
}
function summarize(results:CriterionObservation[],advisories:CreativeStyleAdvisory[]=[]):TechnicalConclusion{
  return results.some(result=>result.outcome==="fail")?"fail":
    (results.some(result=>result.outcome==="not_assessed")||advisories.length>0)?"needs_human_review":
    "pass_technical_only";
}
export async function validateEvaluatorState(project:Project):Promise<void>{
  const bundles=project.production_design?.evaluator_bundles??[];
  const reports=project.production_design?.evaluation_reports??[];
  if(bundles.length>64 || reports.length>256)deny("Evaluator state exceeds its budget.");
  const versions=new Map<string,number[]>();
  for(const bundle of bundles){
    validateEvaluator(bundle);
    versions.set(bundle.id,[...(versions.get(bundle.id)??[]),bundle.version]);
  }
  for(const revisions of versions.values()){
    revisions.sort((a,b)=>a-b);
    if(revisions.some((version,index)=>version!==index+1))
      deny("Versioned creative evaluator history is not append-only.");
  }
  const checksums=new Map<string,string>();
  for(const bundle of bundles)checksums.set(bundle.id+":"+bundle.version,await evaluatorDigest(bundle));
  const ids=new Set<string>();
  for(const report of reports){
    if(!report || typeof report!=="object" || Array.isArray(report))
      deny("Invalid evaluation report shape.");
    strictKeys(report,["schema","id","evaluator_id","evaluator_version","evaluator_sha256",
      "scene_id","source_generation","source_revision","observations","conclusion","creative_approval",
      ...("style_advisories" in report?["style_advisories"]:[])]);
    if(report.schema!==REPORT_SCHEMA || !uuid.test(report.id) || ids.has(report.id) ||
      !uuid.test(report.scene_id) || !uuid.test(report.source_generation) ||
      !Number.isSafeInteger(report.source_revision) || report.source_revision<0 ||
      report.source_revision>project.revision ||
      !digestPattern.test(report.evaluator_sha256) ||
      checksums.get(report.evaluator_id+":"+report.evaluator_version)!==report.evaluator_sha256 ||
      report.creative_approval!=="not_assessed")
      deny("Invalid source-bound evaluation report.");
    ids.add(report.id);
    const bundle=bundles.find(entry=>entry.id===report.evaluator_id && entry.version===report.evaluator_version)!;
    const advisories=report.style_advisories??[];
    if(!Array.isArray(advisories)||advisories.length>32)
      deny("Evaluator style advisory budget invalid.");
    for(const advisory of advisories){
      strictKeys(advisory,["kind","scene_id","interval_start","interval_end",
        "source_node_ids","rationale","suggested_action","human_review_required"]);
      if(!["repetition","density","motion_purpose"].includes(advisory.kind)||
        advisory.scene_id!==report.scene_id||!validAdvisoryTime(advisory.interval_start)||
        !validAdvisoryTime(advisory.interval_end)||
        beforeTime(advisory.interval_start,{num:"0",den:"1"})||
        !beforeTime(advisory.interval_start,advisory.interval_end)||
        advisory.human_review_required!==true||
        !Array.isArray(advisory.source_node_ids)||!advisory.source_node_ids.length||
        advisory.source_node_ids.length>24||
        advisory.source_node_ids.some(id=>!uuid.test(id))||
        new Set(advisory.source_node_ids).size!==advisory.source_node_ids.length)
        deny("Creative style advisory has foreign source, interval or reviewer state.");
      bounded(advisory.rationale,300,"style rationale");
      bounded(advisory.suggested_action,240,"suggested action");
    }
    if(report.observations.length!==bundle.criteria.length ||
      report.conclusion!==summarize(report.observations,advisories))
      deny("Evaluation report cannot fabricate a technical or creative PASS.");
    for(let index=0;index<bundle.criteria.length;index++){
      const observation=report.observations[index];
      strictKeys(observation,["criterion_id","outcome","explanation"]);
      bounded(observation.explanation,400,"observation explanation");
      if(observation.criterion_id!==bundle.criteria[index].id ||
        !["pass","fail","not_assessed"].includes(observation.outcome) ||
        (bundle.criteria[index].kind==="human_question" && observation.outcome!=="not_assessed"))
        deny("Human creative judgment cannot be asserted by technical evaluation.");
    }
  }
}
export async function applyEvaluatorChange(project:Project,change:EvaluatorChange):Promise<void>{
  if(project.locks.some(lock=>lock.resource==="project:"+project.id&&lock.kind==="content"))
    deny("Project content locked: cannot update creative evaluator.");
  const design=project.production_design??emptyProductionDesign();
  const bundles=design.evaluator_bundles??[];
  const reports=design.evaluation_reports??[];
  if(change.type==="register_creative_evaluator"){
    validateEvaluator(change.bundle);
    if(bundles.length>=64)deny("Evaluator bundle version budget exceeded.");
    const latest=Math.max(0,...bundles.filter(b=>b.id===change.bundle.id).map(b=>b.version));
    if(change.bundle.version!==latest+1)
      deny("Evaluator recipe versions are append-only; previous versions cannot change.");
    design.evaluator_bundles=[...bundles,structuredClone(change.bundle)];
  }else{
    if(project.locks.some(lock=>lock.resource==="scene:"+change.scene_id && lock.kind==="content"))
      deny("Scene content locked: cannot record evaluation.");
    if(reports.length>=256)deny("Creative evaluation report budget exceeded.");
    const bundle=bundles.find(b=>b.id===change.evaluator_id && b.version===change.evaluator_version);
    const scene=project.scenes.find(s=>s.id===change.scene_id);
    if(!bundle || !scene)deny("Creative evaluator/scene not found.");
    const observations=bundle!.criteria.map(criterion=>observeCriterion(scene!,criterion));
    const advisories=observeStyleAdvisories(scene!);
    const report:CreativeEvaluationReport={
      schema:REPORT_SCHEMA,id:crypto.randomUUID(),
      evaluator_id:bundle!.id,evaluator_version:bundle!.version,
      evaluator_sha256:await evaluatorDigest(bundle!),
      scene_id:change.scene_id,source_generation:project.generation,
      source_revision:project.revision,observations,
      ...(advisories.length?{style_advisories:advisories}:{}),
      conclusion:summarize(observations,advisories),creative_approval:"not_assessed",
    };
    design.evaluation_reports=[...reports,report];
  }
  project.production_design=design;
}
