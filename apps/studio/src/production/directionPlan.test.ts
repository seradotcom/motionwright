import {expect,it} from 'vitest';
import {fixtureProject} from '../fixture';
import {unapprovedPlanForSelectedDirection} from './directionPlan';
import type {CreativeDirectionStudyReport,CreativeDirectionStudyRequest} from './directionTypes';

function fixture(){
 const p=structuredClone(fixtureProject);
 if(!p.scenes.length)throw new Error('Canonical fixture must have at least one scene');
 p.brief.objective='An exact project problem to solve';
 p.brief.audience='Owner creative director';
 p.brief.exclusions=['Do not invent performance claims'];
 p.assets.push({id:'018f0000-0000-7000-8000-000000000011',name:'Owner real screenshot',
   media_type:'image/png',content_sha256:'ab'.repeat(32),source_revision:'build-1'});
 const chosenId='018f0000-0000-7000-8000-000000000012';
 const req:CreativeDirectionStudyRequest={
   schema:'motionwright.creative-direction-study/1',
   project_id:p.id,generation:p.generation,revision:p.revision,
   project_sha256:'cd'.repeat(32),product_version:'build-1',
   references:[{id:'018f0000-0000-7000-8000-000000000013',
     asset_id:p.assets[p.assets.length-1].id,content_sha256:'ab'.repeat(32),
     source_revision:'build-1',owner_usage_note:'Owner provided',
     owner_attests_licensed_use:true,
     observed_hierarchy:'Original headline is visible',
     observed_framing:'Source crop is fixed',observed_transition:'Short hold',
     observed_rhythm:'Deliberate',originality_constraint:'Do not clone original layout'}],
   alternatives:[{id:chosenId,title:'Deliberate source explanation',
     metaphor:'A journey to the verified original source',
     structure:'problem_action_outcome',rhythm:'accelerating',
     distinct_visual_argument:'Original composition and visible source bounds',
     reference_ids:['018f0000-0000-7000-8000-000000000013'],
     shot_studies:[{scene_id:p.scenes[0].id,narrative_purpose:'Demonstrate the source boundary',
       audience_takeaway:'Learn whether a product claim is merely illustrative',
       claim_ids:[],evidence_kind:'graphic_illustration',evidence_asset_id:null}]}]
 };
 const result:CreativeDirectionStudyReport={
   schema:'motionwright.creative-direction-study-report/1',
   project_id:p.id,generation:p.generation,revision:p.revision,
   project_sha256:req.project_sha256,study_sha256:'ef'.repeat(32),
   product_version:'build-1',
   candidate_reviews:[{
     concept_id:chosenId,concept_sha256:'aa'.repeat(32),
     reference_analysis_sha256:'bb'.repeat(32),claim_reviews:[],
     owner_selected:false,human_creative_approved:false,
     actual_native_pixels_reviewed:false,real_product_evidence_approved:false
   }],
   concept_differences_validated:true,
   reference_media_bytes_opened:false,
   rights_independently_verified:false,
   claim_truth_independently_verified:false,
   winning_concept_id:null,project_was_modified:false,
   renderer_executed:false,
   production_approval:'PENDING_INDEPENDENT_OWNER_REVIEW'
 };
 return {p,req,result,chosenId};
}
it('user selection produces only an unapproved, existing-revision production plan',()=>{
 const {p,req,result,chosenId}=fixture();
 const plan=unapprovedPlanForSelectedDirection(p,req,result,chosenId);
 expect(plan.approval).toBeNull();
 expect(plan.clock).toEqual({kind:'timeline'});
 expect(plan.shots).toHaveLength(1);
 expect(plan.shots[0].evidence_kind).toBe('graphic_study');
 expect(plan.shots[0].claim_ids).toEqual([]);
 expect(plan.reference_constraints[0]).toContain('Do not clone');
 expect(plan.exclusions).toContain('Do not invent performance claims');
 expect(plan.concept).toContain('human-selected draft');
});
it('generic footage or illustration cannot launder source claims into a plan',()=>{
 const {p,req,result,chosenId}=fixture();
 const claimId='018f0000-0000-7000-8000-000000000014';
 req.alternatives[0].shot_studies[0].claim_ids=[claimId];
 result.candidate_reviews[0].claim_reviews=[{
  scene_id:p.scenes[0].id,claim_id:claimId,source_asset_id:null,
  source_sha256:null,source_version:null,current_product_version:'build-1',
  status:'illustration_is_not_evidence',
  verified_product_behavior:false,independently_verified_rights:false
 }];
 expect(()=>unapprovedPlanForSelectedDirection(p,req,result,chosenId)).toThrow('cannot enter');
});
it('real source claims can enter an unapproved plan but never gain truth approval',()=>{
 const {p,req,result,chosenId}=fixture();
 const claimId='018f0000-0000-7000-8000-000000000014';
 p.brief.claims.push({
   id:claimId,text:'This is a source-bound product action, pending manual fact-check',
   source:{kind:'asset',asset_id:p.assets[p.assets.length-1].id},
   context:'One owner-supplied screenshot, not independently verified',
   source_revision:'build-1'
 });
 const shot=req.alternatives[0].shot_studies[0];
 shot.evidence_kind='real_product_capture';
 shot.evidence_asset_id=p.assets[p.assets.length-1].id;
 shot.claim_ids=[claimId];
 result.candidate_reviews[0].claim_reviews=[{
   scene_id:p.scenes[0].id,claim_id:claimId,source_asset_id:shot.evidence_asset_id,
   source_sha256:'ab'.repeat(32),source_version:'build-1',current_product_version:'build-1',
   status:'source_bound_needs_human_verification',
   verified_product_behavior:false,independently_verified_rights:false
 }];
 const plan=unapprovedPlanForSelectedDirection(p,req,result,chosenId);
 expect(plan.approval).toBeNull();
 expect(plan.shots[0].asset_ids).toEqual([shot.evidence_asset_id]);
 expect(plan.shots[0].evidence_kind).toBe('real_capture');
});
it('different project revision or forged renderer/owner approvals fail closed',()=>{
 const {p,req,result,chosenId}=fixture();
 p.revision++;
 expect(()=>unapprovedPlanForSelectedDirection(p,req,result,chosenId)).toThrow('stale');
 p.revision--;
 const forged={...result,winning_concept_id:chosenId};
 expect(()=>unapprovedPlanForSelectedDirection(p,req,forged,chosenId)).toThrow('approval');
});
