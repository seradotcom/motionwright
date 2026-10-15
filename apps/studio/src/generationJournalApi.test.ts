import { describe, expect, it } from "vitest";
import { fixtureProject } from "./fixture";
import { validateDraftReply, validateJournalReply, validateReservationReply } from "./generationJournalApi";
import type { PaidGenerationJob, PaidGenerationSpec } from "./paidGenerationTypes";

function fixture() {
  const project=structuredClone(fixtureProject);
  const spec:PaidGenerationSpec={
    id:"11111111-1111-4111-8111-111111111111",campaign_id:"22222222-2222-4222-8222-222222222222",
    scene_id:project.scenes[0].id,variant_index:0,kind:"image",scope:{kind:"pilot"},
    provider_id:"declared-provider",model_id:"declared-model",model_version:"v1",
    capability_receipt_sha256:"a".repeat(64),rights_policy_sha256:"b".repeat(64),prompt_sha256:"c".repeat(64),
    inputs:[],output_usage_terms:"Original synthetic study only",identity:{kind:"synthetic_original",visible_synthetic_label:"SYNTHETIC STUDY"},
    max_charge_microusd:1_500_001,logical_idempotency_sha256:"",
  };
  const normalized={...spec,logical_idempotency_sha256:"d".repeat(64)};
  const budget={schema:"motionwright.paid-generation-budget-preview/1",project_reserved_microusd:0,provider_reported_cost_microusd:0,unresolved_reserved_microusd:0,currency:"USD_micro",cost_reports_are_provider_verified:false,actual_payment_settled:false,may_retry_paid_submission:false};
  const envelope={project_id:project.id,generation:project.generation,revision:project.revision};
  const draft={...envelope,schema:"motionwright.paid-generation-draft-preview/1",specification:normalized,normalized_edit:{kind:"create",spec:normalized},budget,provider_capability_signature_verified:false,owner_execution_grant:false,provider_called:false,payment_charged:false,committed:false};
  const job:PaidGenerationJob={spec:normalized,events:[],state:"draft",reserved_microusd:0,provider_reported_spend_microusd:null,task_id:null,reported_output_sha256:null,admitted_output_asset_id:null};
  project.paid_generation={jobs:[job]};
  const reserve={...envelope,schema:"motionwright.paid-generation-budget-preflight/1",normalized_edit:{kind:"append",job_id:spec.id,expected_history_sha256:"e".repeat(64),event:{id:"33333333-3333-4333-8333-333333333333",kind:{kind:"reserve",maximum_charge_microusd:spec.max_charge_microusd}}},budget:{...budget,project_reserved_microusd:spec.max_charge_microusd,unresolved_reserved_microusd:spec.max_charge_microusd},committed:false,provider_called:false,payment_charged:false,owner_execution_grant:false};
  const row={job_id:spec.id,kind:spec.kind,scope:spec.scope,state:job.state,history_sha256:"e".repeat(64),provider_id:spec.provider_id,model_id:spec.model_id,model_version:spec.model_version,variant_index:0,max_charge_microusd:spec.max_charge_microusd,source_sha256:normalized.logical_idempotency_sha256,known_task_id:null as string|null,reconciliation_query:null as unknown,reported_spend_microusd:null,owner_approval_is_authenticated:false,provider_success_is_authenticated:false};
  const journal={...envelope,schema:"motionwright.paid-generation-ledger-observation/1",rows:[row],budget,read_only:true,remote_api_called:false,execution_authority:"NONE_GRANTED"};
  return {project,spec,draft,reserve,journal,job};
}

describe("generation accounting is strict source data, never provider authority",()=>{
  it("admits exactly source-bound previews without changing project state",()=>{
    const {project,spec,draft,reserve,journal}=fixture();
    const before=structuredClone(project);
    expect(validateDraftReply(project,spec,draft).committed).toBe(false);
    expect(validateReservationReply(project,spec.id,reserve).owner_execution_grant).toBe(false);
    expect(validateJournalReply(project,journal).remote_api_called).toBe(false);
    expect(project).toEqual(before);
  });
  it.each(["project_id","generation","revision"])("rejects a different %s",key=>{
    const {project,spec,draft}=fixture();
    expect(()=>validateDraftReply(project,spec,{...draft,[key]:key==="revision"?project.revision+1:"other"})).toThrow();
  });
  it.each(["owner_execution_grant","provider_called","payment_charged","committed","provider_capability_signature_verified"])("requires %s to be explicitly false",flag=>{
    const {project,spec,draft}=fixture();
    for(const bad of [true,null,undefined,"false",0]) expect(()=>validateDraftReply(project,spec,{...draft,[flag]:bad})).toThrow();
  });
  it("cannot normalize a changed price, source, scene or provider behind the confirmation",()=>{
    const {project,spec,draft}=fixture();
    for(const changes of [{max_charge_microusd:8},{scene_id:"wrong-scene"},{prompt_sha256:"f".repeat(64)},{provider_id:"other"}]) {
      const normalized={...draft.specification,...changes};
      expect(()=>validateDraftReply(project,spec,{...draft,specification:normalized,normalized_edit:{kind:"create",spec:normalized}})).toThrow();
    }
    expect(()=>validateDraftReply(project,spec,{...draft,provider_url:"https://not-called.invalid"})).toThrow();
  });
  it("rejects missing keys, fractional money, ambiguous flags and totals outside the recorded cap",()=>{
    const {project,spec,draft}=fixture();
    for(const amount of [-1,1.5,Number.NaN,Infinity,"1",2**53,1_000_000_000_001]) {
      expect(()=>validateDraftReply(project,spec,{...draft,budget:{...draft.budget,project_reserved_microusd:amount}})).toThrow();
    }
    expect(()=>validateDraftReply(project,spec,{...draft,budget:{...draft.budget,provider_reported_cost_microusd:1}})).toThrow();
    expect(()=>validateDraftReply(project,spec,{...draft,budget:{...draft.budget,actual_payment_settled:true}})).toThrow();
  });
  it("rejects source-inconsistent monetary totals even with valid formatting and false authority flags",()=>{
    const {project,spec,draft,reserve,journal}=fixture();
    expect(()=>validateDraftReply(project,spec,{
      ...draft,budget:{...draft.budget,project_reserved_microusd:1}
    })).toThrow();
    expect(()=>validateReservationReply(project,spec.id,{
      ...reserve,budget:{...reserve.budget,project_reserved_microusd:reserve.budget.project_reserved_microusd-1}
    })).toThrow();
    expect(()=>validateReservationReply(project,spec.id,{
      ...reserve,budget:{...reserve.budget,unresolved_reserved_microusd:0}
    })).toThrow();
    expect(()=>validateJournalReply(project,{
      ...journal,budget:{...journal.budget,unresolved_reserved_microusd:1}
    })).toThrow();
    const committed=project.paid_generation!.jobs[0];
    committed.state="reserved";committed.reserved_microusd=committed.spec.max_charge_microusd;
    expect(()=>validateJournalReply(project,journal)).toThrow();
  });
  it("cannot replace a reservation with dispatch, another job or another amount",()=>{
    const {project,spec,reserve}=fixture();
    expect(()=>validateReservationReply(project,"other",reserve)).toThrow();
    for(const kind of [{kind:"reserve",maximum_charge_microusd:7},{kind:"dispatch_unknown",reason:"forged"}]) {
      expect(()=>validateReservationReply(project,spec.id,{...reserve,normalized_edit:{...reserve.normalized_edit,event:{...reserve.normalized_edit.event,kind}}})).toThrow();
    }
    project.paid_generation!.jobs[0].state="reserved";
    expect(()=>validateReservationReply(project,spec.id,reserve)).toThrow();
  });
  it("does not omit, repeat, or fabricate existing canonical journal rows",()=>{
    const {project,journal}=fixture();
    expect(()=>validateJournalReply(project,{...journal,rows:[]})).toThrow();
    expect(()=>validateJournalReply(project,{...journal,rows:[...journal.rows,...journal.rows]})).toThrow();
    expect(()=>validateJournalReply(project,{...journal,rows:[{...journal.rows[0],state:"pilot_approved"}]})).toThrow();
  });
  it("uncertain tasks can only retain the same query-existing-task hint",()=>{
    const {project,journal,job}=fixture();
    job.state="unknown_outcome";job.task_id="same.task";job.reserved_microusd=job.spec.max_charge_microusd;
    journal.rows[0].state="unknown_outcome";
    journal.rows[0].known_task_id=job.task_id;
    const query={schema:"motionwright.paid-generation-reconcile-query/1",job_id:job.spec.id,provider_id:job.spec.provider_id,logical_idempotency_sha256:job.spec.logical_idempotency_sha256,known_task_id:job.task_id,operation:"query_existing_task_only",launches_new_task:false,billing_verified:false,provider_observation_verified:false};
    journal.rows[0].reconciliation_query=query;
    journal.budget={...journal.budget,project_reserved_microusd:job.reserved_microusd,unresolved_reserved_microusd:job.reserved_microusd};
    expect(validateJournalReply(project,journal).rows[0].reconciliation_query?.launches_new_task).toBe(false);
    for(const patch of [{operation:"retry"},{launches_new_task:true},{known_task_id:"new.task"},{billing_verified:true},{provider_observation_verified:true}]) {
      expect(()=>validateJournalReply(project,{...journal,rows:[{...journal.rows[0],reconciliation_query:{...query,...patch}}]})).toThrow();
    }
  });
});
