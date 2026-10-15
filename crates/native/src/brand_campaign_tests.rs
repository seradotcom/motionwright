//! The BrandCampaign rollout is a Semwright Native SDK observed-resource
//! mutation against existing StudioService CAS, never a free-running agent.
use super::*;

#[test]
fn brand_campaign_operation_is_target_bound_mutating_and_durable() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("brand-native.sqlite3")).unwrap();
    let app = build_application(service).unwrap();
    let contract = app
        .contracts()
        .into_iter()
        .find(|c| c.descriptor.name == "driver.motionwright.brand-campaign.owner-rollout")
        .expect("brand campaign needs one registered first-party native contract");
    contract.validate(APP_ID).unwrap();
    assert_eq!(contract.descriptor.risk, Risk::Mutating);
    assert_eq!(contract.descriptor.backends, vec!["driver:motionwright"]);
    assert_eq!(contract.target, TargetRequirement::ObservedResource);
    assert!(contract.atomic_revision_cas);
    assert_eq!(contract.retry, RetrySemantics::DurableRequestKey);
}
#[test]
fn typed_native_brand_rollout_refuses_unauthorized_executable_extensions() {
    for payload in [
        json!({"ref":"source","rollout":{"execute":"shell"}}),
        json!({"ref":"source","rollout":{"scene_ids":["outside"],"provider_key":"secret"}}),
        json!({"ref":"source","campaign":"owner-confirmed"}),
    ] {
        assert!(change_from_args(OperationKind::ApplyBrandCampaign, &payload).is_err());
    }
}
#[tokio::test]
async fn application_local_context_never_becomes_authorized_owner_brand_commit() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("brand-local.sqlite3")).unwrap();
    let project = service
        .create_project("No unobserved campaign mutations")
        .unwrap();
    let handler = ApplyHandler {
        service: service.clone(),
        operation: OperationKind::ApplyBrandCampaign,
    };
    let result = handler
        .invoke(
            json!({"ref":project.resource_key(),"rollout":{}}),
            &CallContext::application_local("owner-grant-not-issued").unwrap(),
        )
        .await;
    assert!(matches!(result, Completion::NotApplied(_)));
    assert_eq!(service.project(project.id).unwrap(), project);
}
