use motionwright_domain::Change;
use motionwright_service::{ProductionReceiptInput, StudioService};
use motionwright_storage::StorageError;
use serde_json::json;

#[test]
fn independent_clients_cannot_redispatch_an_unknown_request_after_stale_preflight_or_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("dispatch-claim.sqlite3");
    let first = StudioService::open(&path).unwrap();
    let project = first
        .create_project("Production request ownership")
        .unwrap();
    let second = StudioService::open(&path).unwrap();
    let input = ProductionReceiptInput {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: "original-render-request".into(),
        request_sha256: "a".repeat(64),
        command: "driver.motion-canvas.render.start".into(),
        stage: "dispatching".into(),
        payload: json!({"mutation": true}),
    };

    // Both independently opened services have already observed absence,
    // exactly the stale preflight possible before the coordinator dispatches.
    for service in [&first, &second] {
        assert!(
            service
                .latest_production_receipt(project.id, &input.request_id)
                .unwrap()
                .is_none()
        );
    }
    let claimed = first.append_production_receipt(input.clone()).unwrap();
    assert!(matches!(
        second.append_production_receipt(input.clone()),
        Err(StorageError::RequestReuse)
    ));
    assert_eq!(
        second.production_receipts(project.id, 10).unwrap(),
        vec![claimed]
    );

    let unknown = second
        .append_production_receipt(ProductionReceiptInput {
            stage: "outcome_unknown".into(),
            payload: json!({"error": {"code": "Timeout", "outcome_known": false}}),
            ..input.clone()
        })
        .unwrap();
    drop(first);
    drop(second);

    let reopened = StudioService::open(&path).unwrap();
    assert!(matches!(
        reopened.append_production_receipt(input.clone()),
        Err(StorageError::RequestReuse)
    ));
    assert_eq!(
        reopened
            .latest_production_receipt(project.id, &input.request_id)
            .unwrap(),
        Some(unknown)
    );
    assert_eq!(
        reopened.production_receipts(project.id, 10).unwrap().len(),
        2
    );
    assert_eq!(
        reopened.project(project.id).unwrap().stamp(),
        project.stamp()
    );
}

#[test]
fn completed_and_failed_receipts_block_new_dispatch_without_losing_the_original_result() {
    for stage in ["completed", "failed_known"] {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("terminal-dispatch.sqlite3");
        let service = StudioService::open(&path).unwrap();
        let project = service
            .create_project("Terminal receipt preservation")
            .unwrap();
        let input = ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "one-production-request".into(),
            request_sha256: "a".repeat(64),
            command: "driver.motion-canvas.render.start".into(),
            stage: "dispatching".into(),
            payload: json!({"mutation": true}),
        };
        service.append_production_receipt(input.clone()).unwrap();
        let terminal = service
            .append_production_receipt(ProductionReceiptInput {
                stage: stage.into(),
                payload: if stage == "completed" {
                    json!({"result": {"job_ref": "original-job", "state": "succeeded"}})
                } else {
                    json!({"error": {"code": "BackendFailed", "outcome_known": true}})
                },
                ..input.clone()
            })
            .unwrap();
        drop(service);
        let reopened = StudioService::open(&path).unwrap();
        assert!(matches!(
            reopened.append_production_receipt(input.clone()),
            Err(StorageError::RequestReuse)
        ));
        assert_eq!(
            reopened
                .latest_production_receipt(project.id, &input.request_id)
                .unwrap(),
            Some(terminal)
        );
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap().len(),
            2
        );
    }
}

#[test]
fn a_late_original_result_survives_a_new_project_revision_and_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("late-production-result.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Late result history").unwrap();
    let input = ProductionReceiptInput {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: "old-revision-request".into(),
        request_sha256: "a".repeat(64),
        command: "driver.motion-canvas.render.start".into(),
        stage: "dispatching".into(),
        payload: json!({"mutation": true}),
    };
    service.append_production_receipt(input.clone()).unwrap();
    let edited = service
        .apply(
            project.id,
            &project.stamp(),
            "edit-during-render",
            &Change::AddScene {
                name: "A later scene".into(),
                objective: "Keep the original result historical".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let result = service
        .append_production_receipt(ProductionReceiptInput {
            stage: "completed".into(),
            payload: json!({"result": {"job_ref": "original-job", "state": "succeeded"}}),
            ..input.clone()
        })
        .unwrap();
    assert_eq!(result.revision, project.revision);
    assert!(edited.revision > result.revision);
    drop(service);

    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(
        reopened.project(project.id).unwrap().stamp(),
        edited.stamp()
    );
    assert_eq!(
        reopened
            .latest_production_receipt(project.id, &input.request_id)
            .unwrap(),
        Some(result)
    );
    assert_eq!(
        reopened.production_receipts(project.id, 10).unwrap().len(),
        2
    );
}
