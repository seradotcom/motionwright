/** Persistent Rust-domain source/owner records, NOT provider execution receipts. */
export type PaidGenerationKind = "image" | "video" | "tts" | "presenter";
export type PaidGenerationState =
  "draft" | "reserved" | "unknown_outcome" | "provider_reported_success" |
  "provider_reported_failure" | "output_admitted" | "pilot_approved";
export type PaidGenerationScope =
  {kind:"pilot"} | {kind:"batch";pilot_job_id:string;approved_pilot_sha256:string};
export interface PaidGenerationInput {asset_id:string;sha256:string;owner_rights_note:string}
export type PaidIdentity =
  {kind:"synthetic_original";visible_synthetic_label:string} |
  {kind:"consented_person";person_asset_id:string;person_asset_sha256:string;
    purpose:string;exact_consent_sha256:string;owner_declares_consent:boolean};
export interface PaidGenerationSpec {
  id:string;campaign_id:string;scene_id:string;variant_index:number;
  kind:PaidGenerationKind;scope:PaidGenerationScope;
  provider_id:string;model_id:string;model_version:string;
  capability_receipt_sha256:string;rights_policy_sha256:string;prompt_sha256:string;
  inputs:PaidGenerationInput[];output_usage_terms:string;identity:PaidIdentity;
  max_charge_microusd:number;logical_idempotency_sha256:string;
}
export type PaidGenerationEventKind =
  {kind:"reserve";maximum_charge_microusd:number} |
  {kind:"dispatch_unknown";provider_task_id:string|null;declared_receipt_sha256:string|null;reason:string} |
  {kind:"provider_poll";provider_task_id:string|null;observed_receipt_sha256:string;outcome:unknown} |
  {kind:"admit_output";asset_id:string;output_sha256:string;rights_note:string;reviewer:string} |
  {kind:"approve_pilot";output_sha256:string;reviewer:string;rationale:string};
export interface PaidGenerationEvent {id:string;kind:PaidGenerationEventKind}
export type PaidGenerationEdit =
  {kind:"create";spec:PaidGenerationSpec} |
  {kind:"append";job_id:string;expected_history_sha256:string;event:PaidGenerationEvent};
export interface PaidGenerationJob {
  spec:PaidGenerationSpec;events:PaidGenerationEvent[];state:PaidGenerationState;
  reserved_microusd:number;provider_reported_spend_microusd:number|null;
  task_id:string|null;reported_output_sha256:string|null;admitted_output_asset_id:string|null;
}
export interface PaidGenerationLedger {jobs:PaidGenerationJob[]}
export interface PaidGenerationBudgetPreview {
  schema:"motionwright.paid-generation-budget-preview/1";
  project_reserved_microusd:number;provider_reported_cost_microusd:number;
  unresolved_reserved_microusd:number;currency:"USD_micro";
  cost_reports_are_provider_verified:false;actual_payment_settled:false;
  may_retry_paid_submission:false;
}
export interface PaidReconcileHint {
  schema:"motionwright.paid-generation-reconcile-query/1";
  job_id:string;provider_id:string;logical_idempotency_sha256:string;
  known_task_id:string|null;operation:"query_existing_task_only";
  launches_new_task:false;billing_verified:false;provider_observation_verified:false;
}
export interface PaidGenerationJournalRow {
  job_id:string;kind:PaidGenerationKind;scope:PaidGenerationScope;
  state:PaidGenerationState;history_sha256:string;
  provider_id:string;model_id:string;model_version:string;variant_index:number;
  max_charge_microusd:number;source_sha256:string;known_task_id:string|null;
  reconciliation_query:PaidReconcileHint|null;reported_spend_microusd:number|null;
  owner_approval_is_authenticated:false;provider_success_is_authenticated:false;
}
export interface PaidGenerationJournalObservation {
  schema:"motionwright.paid-generation-ledger-observation/1";revision:number;
  rows:PaidGenerationJournalRow[];budget:PaidGenerationBudgetPreview;
  read_only:true;remote_api_called:false;execution_authority:"NONE_GRANTED";
}
export interface PaidGenerationDraftPreview {
  schema:"motionwright.paid-generation-draft-preview/1";
  normalized_edit:PaidGenerationEdit;specification:PaidGenerationSpec;
  budget:PaidGenerationBudgetPreview;
  provider_capability_signature_verified:false;owner_execution_grant:false;
  provider_called:false;payment_charged:false;committed:false;
}
export interface PaidGenerationReservationPreview {
  schema:"motionwright.paid-generation-budget-preflight/1";
  normalized_edit:PaidGenerationEdit;budget:PaidGenerationBudgetPreview;
  committed:false;provider_called:false;payment_charged:false;owner_execution_grant:false;
}
