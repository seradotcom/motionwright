/** E13-02 bounded *data-only* experiment, not a skill sandbox or executable
 * package admission. Reuses the exact first-party evaluator interpreter.
 */
import type {Project,Scene} from "./types";
import {
  evaluatorDigest,observeCriterion,validateEvaluator,validateEvaluatorState,
  type CreativeEvaluatorBundle,type CriterionObservation,
} from "./creativeEvaluator";

export const EVALUATOR_EXPERIMENT_SCHEMA="motionwright.evaluator-data-experiment/1" as const;
const refuse=(why:string):never=>{throw new Error(why);};

export interface EvaluatorExperimentScene {
  sceneId:string;sceneName:string;
  baseline:CriterionObservation[];
  candidate:CriterionObservation[];
  baselinePass:number;baselineFail:number;baselineAbstain:number;
  candidatePass:number;candidateFail:number;candidateAbstain:number;
  matchingCriterionRegressions:number;
  matchingCriterionRecoveries:number;
}
export interface EvaluatorExperimentResult {
  schema:typeof EVALUATOR_EXPERIMENT_SCHEMA;
  projectId:string;generation:string;revision:number;
  baselineId:string;baselineVersion:number;baselineSha256:string;
  candidateVersion:number;candidateSha256:string;
  selectedSceneIds:string[];
  comparisons:EvaluatorExperimentScene[];
  removedCriterionIds:string[];
  addedCriterionIds:string[];
  status:"TECHNICAL_OBSERVATION_ONLY";
  humanCreativeApproval:"not_assessed";
  rightsAuthenticated:false;
  executableCodeRun:false;
  evaluatorInstalled:false;
  providerCalled:false;
  candidate:CreativeEvaluatorBundle;
}
function observed(scene:Scene,bundle:CreativeEvaluatorBundle):CriterionObservation[]{
  return bundle.criteria.map(criterion=>observeCriterion(scene,criterion));
}
const totals=(values:CriterionObservation[])=>({
  pass:values.filter(value=>value.outcome==="pass").length,
  fail:values.filter(value=>value.outcome==="fail").length,
  abstain:values.filter(value=>value.outcome==="not_assessed").length,
});
const sameBehavior=(a:CreativeEvaluatorBundle,b:CreativeEvaluatorBundle)=>
  JSON.stringify({label:a.label,purpose:a.purpose,playbook:a.playbook,criteria:a.criteria})===
  JSON.stringify({label:b.label,purpose:b.purpose,playbook:b.playbook,criteria:b.criteria});

/** At most three source-authored scenes; never mutate even a disposable project.
 * The registered bundle remains the source for actual owner CAS admission.
 */
export async function previewEvaluatorExperiment(
  project:Project,baseId:string,baseVersion:number,
  candidateText:string,selectedSceneIds:readonly string[]
):Promise<EvaluatorExperimentResult>{
  if(!Number.isSafeInteger(project.revision)||project.revision<0||
      !Number.isSafeInteger(baseVersion)||baseVersion<1||
      typeof candidateText!=="string"||new TextEncoder().encode(candidateText).length>16*1024)
    return refuse("Evaluator experiment source or candidate JSON exceeds the bounded budget.");
  if(selectedSceneIds.length<1||selectedSceneIds.length>3 ||
      new Set(selectedSceneIds).size!==selectedSceneIds.length)
    return refuse("Compare one to three distinct original source scenes.");
  if(project.locks.some(lock=>lock.resource==="project:"+project.id&&lock.kind==="content"))
    return refuse("Project content is locked; no evaluator version can be admitted.");
  const projectScenes=selectedSceneIds.map(id=>project.scenes.find(scene=>scene.id===id));
  if(projectScenes.some(scene=>!scene))
    return refuse("Evaluator experiment cannot inspect an unknown or external scene.");
  await validateEvaluatorState(project);
  const bundles=project.production_design?.evaluator_bundles??[];
  const source=bundles.find(bundle=>bundle.id===baseId&&bundle.version===baseVersion);
  if(!source)return refuse("Source evaluator version does not exist.");
  const latestVersion=Math.max(...bundles.filter(bundle=>bundle.id===baseId).map(bundle=>bundle.version));
  if(latestVersion!==baseVersion)
    return refuse("Only the latest immutable source evaluator may start a new version experiment.");
  let candidate:CreativeEvaluatorBundle;
  try{candidate=JSON.parse(candidateText) as CreativeEvaluatorBundle;}
  catch{return refuse("Candidate evaluator is not valid JSON.");}
  validateEvaluator(candidate);
  if(candidate.id!==source.id || candidate.version!==source.version+1)
    return refuse("Candidate must reuse the same evaluator ID and increment its version exactly once.");
  if(sameBehavior(source,candidate))
    return refuse("The new version must change original criteria or editorial guidance, not just its version.");
  if(!candidate.criteria.some(criterion=>criterion.kind==="human_question"))
    return refuse("The candidate must retain an explicit question for human creative abstention.");
  const baselineSha256=await evaluatorDigest(source);
  const candidateSha256=await evaluatorDigest(candidate);
  if(baselineSha256===candidateSha256)return refuse("Candidate source has not changed.");
  const previous=new Set(source.criteria.map(criterion=>criterion.id));
  const incoming=new Set(candidate.criteria.map(criterion=>criterion.id));
  const removedCriterionIds=[...previous].filter(id=>!incoming.has(id));
  const addedCriterionIds=[...incoming].filter(id=>!previous.has(id));
  const comparisons=projectScenes.map(scene=>{
    const original=observed(scene!,source);
    const proposed=observed(scene!,candidate);
    const previousById=new Map(original.map(row=>[row.criterion_id,row]));
    const regressions=proposed.filter(row=>
      previousById.get(row.criterion_id)?.outcome==="pass" && row.outcome!=="pass").length;
    const recoveries=proposed.filter(row=>
      previousById.get(row.criterion_id)?.outcome==="fail" && row.outcome==="pass").length;
    const a=totals(original),b=totals(proposed);
    return {
      sceneId:scene!.id,sceneName:scene!.name,baseline:original,candidate:proposed,
      baselinePass:a.pass,baselineFail:a.fail,baselineAbstain:a.abstain,
      candidatePass:b.pass,candidateFail:b.fail,candidateAbstain:b.abstain,
      matchingCriterionRegressions:regressions,
      matchingCriterionRecoveries:recoveries,
    };
  });
  return {
    schema:EVALUATOR_EXPERIMENT_SCHEMA,projectId:project.id,
    generation:project.generation,revision:project.revision,
    baselineId:source.id,baselineVersion:source.version,baselineSha256,
    candidateVersion:candidate.version,candidateSha256,
    selectedSceneIds:[...selectedSceneIds],comparisons,removedCriterionIds,
    addedCriterionIds,status:"TECHNICAL_OBSERVATION_ONLY",
    humanCreativeApproval:"not_assessed",rightsAuthenticated:false,
    executableCodeRun:false,evaluatorInstalled:false,providerCalled:false,
    candidate:structuredClone(candidate),
  };
}
export async function revalidateEvaluatorExperiment(
  project:Project,receipt:EvaluatorExperimentResult
):Promise<CreativeEvaluatorBundle>{
  if(receipt.schema!==EVALUATOR_EXPERIMENT_SCHEMA ||
      receipt.projectId!==project.id || receipt.generation!==project.generation ||
      receipt.revision!==project.revision || receipt.humanCreativeApproval!=="not_assessed" ||
      receipt.evaluatorInstalled!==false || receipt.executableCodeRun!==false ||
      receipt.rightsAuthenticated!==false || receipt.providerCalled!==false ||
      receipt.status!=="TECHNICAL_OBSERVATION_ONLY")
    return refuse("Evaluator experiment is stale or claims ungranted authority.");
  const repeated=await previewEvaluatorExperiment(
    project,receipt.baselineId,receipt.baselineVersion,
    JSON.stringify(receipt.candidate),receipt.selectedSceneIds
  );
  if(repeated.baselineSha256!==receipt.baselineSha256 ||
      repeated.candidateSha256!==receipt.candidateSha256 ||
      JSON.stringify(repeated.comparisons)!==JSON.stringify(receipt.comparisons) ||
      JSON.stringify(repeated.removedCriterionIds)!==JSON.stringify(receipt.removedCriterionIds) ||
      JSON.stringify(repeated.addedCriterionIds)!==JSON.stringify(receipt.addedCriterionIds))
    return refuse("Source evaluator, selected scenes or experiment observations changed.");
  return structuredClone(repeated.candidate);
}
