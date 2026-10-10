export type NarrativeStructure =
  'problem_action_outcome'|'before_after'|'causal_walkthrough'|'editorial_reveal';
export type CreativeRhythm = 'deliberate'|'accelerating'|'staccato'|'continuous';
export type CreativeEvidenceKind =
  'graphic_illustration'|'real_product_capture'|'licensed_footage';
export interface CreativeReferenceStudy {
  id:string;asset_id:string;content_sha256:string;source_revision:string;
  owner_usage_note:string;owner_attests_licensed_use:boolean;
  observed_hierarchy:string;observed_framing:string;
  observed_transition:string;observed_rhythm:string;
  originality_constraint:string;
}
export interface CreativeDirectionShot {
  scene_id:string;narrative_purpose:string;audience_takeaway:string;
  claim_ids:string[];evidence_asset_id:string|null;
  evidence_kind:CreativeEvidenceKind;
}
export interface CreativeDirection {
  id:string;title:string;metaphor:string;
  structure:NarrativeStructure;rhythm:CreativeRhythm;
  distinct_visual_argument:string;shot_studies:CreativeDirectionShot[];
  reference_ids:string[];
}
export interface CreativeDirectionStudyRequest {
  schema:'motionwright.creative-direction-study/1';
  project_id:string;generation:string;revision:number;
  project_sha256:string;product_version:string;
  references:CreativeReferenceStudy[];alternatives:CreativeDirection[];
}
export type ClaimReviewStatus=
 'illustration_is_not_evidence'|'unsourced_product_claim'|
 'unsupported_claim_source'|'source_bound_needs_human_verification'|
 'stale_product_revision';
export interface DirectionClaimReview {
  scene_id:string;claim_id:string;source_asset_id:string|null;
  source_sha256:string|null;source_version:string|null;
  current_product_version:string;status:ClaimReviewStatus;
  verified_product_behavior:false;independently_verified_rights:false;
}
export interface CreativeDirectionCandidateReview {
  concept_id:string;concept_sha256:string;reference_analysis_sha256:string;
  claim_reviews:DirectionClaimReview[];
  owner_selected:false;human_creative_approved:false;
  actual_native_pixels_reviewed:false;real_product_evidence_approved:false;
}
export interface CreativeDirectionStudyReport {
  schema:'motionwright.creative-direction-study-report/1';
  project_id:string;generation:string;revision:number;
  project_sha256:string;study_sha256:string;product_version:string;
  candidate_reviews:CreativeDirectionCandidateReview[];
  concept_differences_validated:boolean;reference_media_bytes_opened:false;
  rights_independently_verified:false;claim_truth_independently_verified:false;
  winning_concept_id:null;project_was_modified:false;renderer_executed:false;
  production_approval:'PENDING_INDEPENDENT_OWNER_REVIEW';
}
export interface CreativeDirectionSourceState {
  schema:'motionwright.creative-direction-source-state/1';
  project_id:string;generation:string;revision:number;
  project_sha256:string;read_only:true;
}
export interface CreativeDirectionStudyResponse {
  schema:'motionwright.creative-direction-trial/1';
  report:CreativeDirectionStudyReport;
  project_modified:false;source_media_opened:false;
  concept_selected:false;content_approval:'REQUIRES_OWNER_SELECTION';
  native_pixels_rendered:false;
}

export interface SelectedDirectionPlanPreflight {
  schema:'motionwright.source-checked-production-plan/1';
  plan:import('../creativeProduction').ProductionPlan;
  project_id:string;generation:string;revision:number;approval:null;
  renderer_executed:false;project_committed:false;
  independent_claim_review:'REQUIRED';owner_release_approval:'NOT_GRANTED';
}
