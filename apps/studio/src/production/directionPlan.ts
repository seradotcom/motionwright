import type {Project} from '../types';
import type {ProductionPlan} from '../creativeProduction';
import type {CreativeDirectionStudyReport,CreativeDirectionStudyRequest} from './directionTypes';

export function unapprovedPlanForSelectedDirection(
 project:Project,request:CreativeDirectionStudyRequest,
 report:CreativeDirectionStudyReport,conceptId:string
):ProductionPlan{
 if(request.project_id!==project.id||report.project_id!==project.id||
    request.generation!==project.generation||report.generation!==project.generation||
    request.revision!==project.revision||report.revision!==project.revision||
    request.project_sha256!==report.project_sha256||
    request.product_version!==report.product_version||
    !report.concept_differences_validated||
    report.renderer_executed||report.project_was_modified||
    report.winning_concept_id!==null||report.claim_truth_independently_verified||
    report.rights_independently_verified||report.reference_media_bytes_opened)
  throw new Error('The concept preview is stale, unsourced, or incorrectly claims owner/pixel approval.');
 const direction=request.alternatives.find(item=>item.id===conceptId);
 const observed=report.candidate_reviews.find(item=>item.concept_id===conceptId);
 if(!direction||!observed||observed.owner_selected||observed.human_creative_approved||
    observed.actual_native_pixels_reviewed||observed.real_product_evidence_approved)
  throw new Error('Choose only a distinct, unapproved, current-source concept.');
 const observedClaims=new Map(observed.claim_reviews.map(claim=>[claim.claim_id,claim]));
 const shots=direction.shot_studies.map(shot=>{
  if(!project.scenes.some(scene=>scene.id===shot.scene_id))
    throw new Error('Concept contains an unrecognized project scene.');
  if(shot.claim_ids.some(id=>{
      const found=observedClaims.get(id);
      const original=project.brief.claims.find(claim=>claim.id===id);
      const source=project.assets.find(asset=>asset.id===shot.evidence_asset_id);
      return !found||found.scene_id!==shot.scene_id||
        found.status!=='source_bound_needs_human_verification'||
        found.verified_product_behavior||found.independently_verified_rights||
        !original||original.source?.kind!=='asset'||
        original.source.asset_id!==shot.evidence_asset_id||
        original.source_revision!==request.product_version||
        !source||source.source_revision!==request.product_version||
        !source.content_sha256||source.content_sha256!==found.source_sha256;
     }))
    throw new Error('Illustration, unrelated footage, stale or unsupported claims cannot enter an evidence-bearing ProductionPlan.');
  if(shot.evidence_kind==='real_product_capture' && !shot.claim_ids.length)
    throw new Error('A real-capture plan must include explicitly referenced Brief claims.');
  const assetIds=shot.evidence_kind==='graphic_illustration'?[]:
    shot.evidence_asset_id?[shot.evidence_asset_id]:[];
  if(assetIds.some(id=>!project.assets.some(asset=>asset.id===id&&asset.content_sha256)))
    throw new Error('ProductionPlan must retain the original project source asset.');
  return {
   scene_id:shot.scene_id,
   purpose:shot.narrative_purpose+'. Audience takeaway: '+shot.audience_takeaway,
   claim_ids:shot.claim_ids,asset_ids:assetIds,
   evidence_kind:shot.evidence_kind==='graphic_illustration'?'graphic_study' as const:
     shot.evidence_kind==='real_product_capture'?'real_capture' as const:
     'licensed_footage' as const
  };
 });
 const refs=direction.reference_ids.map(id=>{
  const observedRef=request.references.find(ref=>ref.id===id);
  if(!observedRef)throw new Error('Chosen direction lost its original reference.');
  return 'Originality: '+observedRef.originality_constraint+
   '. Owner-observed hierarchy: '+observedRef.observed_hierarchy+
   '. No copy/claim-rights certification from this annotation.';
 });
 const exclusions=[...project.brief.exclusions,
   'Do not present a graphic, synthetic screen or licensed generic footage as verified product behavior.',
   'No product claims or media rights are approved by this draft alone.'];
 if(refs.length>64||exclusions.length>64||refs.some(value=>value.length>2000)||
    shots.some(shot=>shot.purpose.length>4000))
  throw new Error('Direction exceeds the canonical ProductionPlan source bounds.');
 return {
  objective:project.brief.objective,audience:project.brief.audience,
  concept:direction.title+': '+direction.metaphor+'. Structure: '+direction.structure+
    '; rhythm: '+direction.rhythm+'. '+direction.distinct_visual_argument+
    '. This is a human-selected draft, not approved source evidence.',
  reference_constraints:refs,exclusions,shots,
  clock:{kind:'timeline'},approval:null
 };
}
