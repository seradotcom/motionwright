//! Native SDK operation is the same target-bound StudioService CAS, not an
//! external Python executable or a hidden second OTIO editing backend.
use super::*;

#[test]
fn owner_otio_cut_is_a_typed_native_sdk_mutation_with_existing_cas_authority() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("otio-native.sqlite3")).unwrap();
    let app = build_application(service).unwrap();
    let found = app
        .contracts()
        .into_iter()
        .find(|item| item.descriptor.name == "driver.motionwright.otio-cut.owner-commit")
        .expect("public native OTIO owner operation");
    found.validate(APP_ID).unwrap();
    assert_eq!(found.descriptor.risk, Risk::Mutating);
    assert_eq!(found.descriptor.backends, vec!["driver:motionwright"]);
    assert_eq!(found.target, TargetRequirement::ObservedResource);
    assert!(found.atomic_revision_cas);
    assert_eq!(found.retry, RetrySemantics::DurableRequestKey);
}
#[test]
fn native_otio_parser_cannot_admit_untyped_effects_or_shortcut_owner_review() {
    for malicious in [
        json!({"ref":"source","review":{"preview":{"schema":"arbitrary"},"execute":"os.system"}}),
        json!({"ref":"source","review":{"preview":{"schema":"motionwright.otio-edit-preview/1"},
           "reviewer_declaration":"me","rationale":"skip all checks",
           "acknowledges_property_losses":false,
           "acknowledges_absolute_audio_marker_positions":false,
           "confirms_single_revision_change":true}}),
        json!({"ref":"source","review":{"preview":null}}),
        json!({"ref":"source","effect":"download-and-run"}),
    ] {
        assert!(change_from_args(OperationKind::ApplyOtioOwnerCutReview, &malicious).is_err());
    }
}
#[tokio::test]
async fn local_call_context_without_host_observation_never_commits_otio_change() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("otio-native-local.sqlite3")).unwrap();
    let project = service.create_project("No unbound OTIO mutation").unwrap();
    let handler = ApplyHandler {
        service: service.clone(),
        operation: OperationKind::ApplyOtioOwnerCutReview,
    };
    let outcome = handler
        .invoke(
            json!({"ref":project.resource_key(),"review":{}}),
            &CallContext::application_local("otio-unbound").unwrap(),
        )
        .await;
    assert!(matches!(outcome, Completion::NotApplied(_)));
    assert_eq!(service.project(project.id).unwrap(), project);
}
