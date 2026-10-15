//! Exact native operation contract and canonical service integration.
//! This does not simulate or authorize a paid provider, consent, or billing.
use super::*;
use motionwright_domain::{PaidGenerationEdit, PaidGenerationKind, PaidGenerationScope, PaidGenerationSpec, PaidIdentity};

fn source() -> (tempfile::TempDir, StudioService, Project) {
    let tmp=tempfile::tempdir().unwrap();
    let service=StudioService::open(tmp.path().join("native-accounting.sqlite3")).unwrap();
    let p=service.create_project("Native SDK generation accounting test").unwrap();
    let p=service.apply(p.id,&p.stamp(),"source-scene",&Change::AddScene{name:"Original".into(),objective:"No external generation".into(),duration_seconds:4}).unwrap().project;
    (tmp,service,p)
}
fn input(p:&Project)->Value {
    let mut spec=PaidGenerationSpec{
        id:Uuid::new_v4(),campaign_id:Uuid::new_v4(),scene_id:p.scenes[0].id,variant_index:0,
        kind:PaidGenerationKind::Image,scope:PaidGenerationScope::Pilot,
        provider_id:"declared.synthetic".into(),model_id:"original".into(),model_version:"v1".into(),
        capability_receipt_sha256:"a".repeat(64),rights_policy_sha256:"b".repeat(64),prompt_sha256:"c".repeat(64),
        inputs:vec![],output_usage_terms:"Original synthetic study, no external call".into(),
        identity:PaidIdentity::SyntheticOriginal{visible_synthetic_label:"SYNTHETIC / NOT VERIFIED EVIDENCE".into()},
        max_charge_microusd:1_500_001,logical_idempotency_sha256:String::new(),
    };
    spec.logical_idempotency_sha256=spec.expected_idempotency(p.id).unwrap();
    json!({"ref":"opaque-sdk-reference","edit":PaidGenerationEdit::Create{spec}})
}

#[test]
fn registered_generation_contract_keeps_native_revision_and_retry_authority() {
    let (_tmp,service,_)=source();
    let app=build_application(service).unwrap();
    let contract=app.contracts().find(|c|c.descriptor.name=="driver.motionwright.paid-generation.edit").unwrap();
    contract.validate(APP_ID).unwrap();
    assert!(contract.atomic_revision_cas);
    assert_eq!(contract.target,TargetRequirement::ObservedResource);
    assert_eq!(contract.retry,RetrySemantics::DurableRequestKey);
    assert_eq!(contract.descriptor.backends,vec!["driver:motionwright"]);
    assert_eq!(contract.descriptor.risk,Risk::Mutating);
}

#[test]
fn strict_native_journal_payload_maps_to_the_existing_service_cas() {
    let (_tmp,service,p)=source();
    let args=input(&p);
    let change=change_from_args(OperationKind::EditPaidGeneration,&args).unwrap();
    let result=service.apply(p.id,&p.stamp(),"native-journal-intent",&change).unwrap();
    assert_eq!(result.project.revision,p.revision+1);
    assert_eq!(result.project.paid_generation.jobs.len(),1);
    assert_eq!(result.project.paid_generation.jobs[0].reserved_microusd,0);
    let replay=service.apply(p.id,&p.stamp(),"native-journal-intent",&change).unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project.paid_generation,result.project.paid_generation);
    assert!(service.apply(p.id,&p.stamp(),"stale-other-request",&change).is_err());
    for field in ["execute","api_key","payment_authorized"] {
        let mut forged=args.clone();
        forged["edit"][field]=json!(true);
        assert!(change_from_args(OperationKind::EditPaidGeneration,&forged).is_err());
    }
    for value in [json!(-1),json!(1.5),json!("1500001")] {
        let mut forged=args.clone();
        forged["edit"]["spec"]["max_charge_microusd"]=value;
        assert!(change_from_args(OperationKind::EditPaidGeneration,&forged).is_err());
    }
    assert_eq!(service.project(p.id).unwrap(),result.project);
}

#[tokio::test]
async fn an_application_local_read_context_cannot_authorize_a_generation_mutation() {
    let (_tmp,service,p)=source();
    let handler=ApplyHandler{service:service.clone(),operation:OperationKind::EditPaidGeneration};
    let result=handler.invoke(input(&p),&CallContext::application_local("not-a-host-grant").unwrap()).await;
    assert!(matches!(result,Completion::NotApplied(_)));
    assert_eq!(service.project(p.id).unwrap(),p);
}
