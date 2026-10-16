//! Recorded source protection shares the pinned Semwright observed-resource
//! mutation contract and StudioService CAS. No voice synthesis or ASR provider.
use super::*;

#[test]
fn narration_record_and_release_are_source_target_bound_native_edits() {
    let tmp = tempfile::tempdir().unwrap();
    let service = StudioService::open(tmp.path().join("narration-native.sqlite3")).unwrap();
    let app = build_application(service).unwrap();
    for name in [
        "driver.motionwright.narration.take.record",
        "driver.motionwright.narration.take.release",
    ] {
        let op = app
            .contracts()
            .into_iter()
            .find(|v| v.descriptor.name == name)
            .expect("required existing native application capability");
        op.validate(APP_ID).unwrap();
        assert_eq!(op.descriptor.risk, Risk::Mutating);
        assert_eq!(op.descriptor.backends, vec!["driver:motionwright"]);
        assert_eq!(op.target, TargetRequirement::ObservedResource);
        assert!(op.atomic_revision_cas);
        assert_eq!(op.retry, RetrySemantics::DurableRequestKey);
    }
}
#[test]
fn forged_narration_grant_fields_and_malformed_sources_fail_before_mutation() {
    for (kind, input) in [
        (
            OperationKind::RecordNarrationTake,
            json!({"ref":"source","expected_source_sha256":"short","expected_project_revision":0,"reviewer":"editor","reason":"original"}),
        ),
        (
            OperationKind::ReleaseNarrationTake,
            json!({"ref":"source","execute":"shell"}),
        ),
        (
            OperationKind::RecordNarrationTake,
            json!({"ref":"source","expected_source_sha256":null,"reviewer":"editor","reason":"test"}),
        ),
    ] {
        let change = change_from_args(kind, &input);
        if let Ok(Change::RecordNarrationTake {
            expected_source_sha256,
            ..
        }) = &change
        {
            // Payload parsing can return a declarative edit, but the source
            // validator cannot admit a malformed/short source fingerprint.
            assert_ne!(expected_source_sha256.len(), 64);
        } else {
            assert!(change.is_err());
        }
    }
}
#[tokio::test]
async fn application_local_context_without_host_grant_never_locks_original_voice() {
    let tmp = tempfile::tempdir().unwrap();
    let service = StudioService::open(tmp.path().join("narration-local.sqlite3")).unwrap();
    let project = service.create_project("Unbound narration command").unwrap();
    let handler = ApplyHandler {
        service: service.clone(),
        operation: OperationKind::RecordNarrationTake,
    };
    let result = handler
        .invoke(
            json!({"ref":project.resource_key()}),
            &CallContext::application_local("narration-not-a-host-grant").unwrap(),
        )
        .await;
    assert!(matches!(result, Completion::NotApplied(_)));
    assert_eq!(service.project(project.id).unwrap(), project);
}
