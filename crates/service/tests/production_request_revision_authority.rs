use motionwright_domain::Change;
use motionwright_service::{ProductionReceiptInput, StudioService};
use motionwright_storage::StorageError;
use serde_json::json;

#[test]
fn an_independent_client_cannot_reattribute_a_late_result_to_the_current_revision() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("production-original-revision.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let original = service
        .create_project("Original production revision")
        .unwrap();
    let observer = StudioService::open(&path).unwrap();
    let input = ProductionReceiptInput {
        project_id: original.id,
        generation: original.generation,
        revision: original.revision,
        request_id: "original-render-request".into(),
        request_sha256: "a".repeat(64),
        command: "driver.motion-canvas.render.start".into(),
        stage: "dispatching".into(),
        payload: json!({"mutation": true}),
    };
    let dispatch = service.append_production_receipt(input.clone()).unwrap();
    let edited = service
        .apply(
            original.id,
            &original.stamp(),
            "edit-during-production",
            &Change::AddScene {
                name: "New scene".into(),
                objective: "Preserve the original result base".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    assert!(edited.revision > original.revision);

    let rejected = observer.append_production_receipt(ProductionReceiptInput {
        revision: edited.revision,
        stage: "completed".into(),
        payload: json!({"result": {"job_ref": "original-job", "state": "succeeded"}}),
        ..input.clone()
    });
    assert!(
        matches!(rejected, Err(StorageError::RequestReuse)),
        "same key/digest/command must not grant a different revision: {rejected:?}"
    );
    drop(service);
    drop(observer);

    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(original.id).unwrap(), edited);
    assert_eq!(
        reopened.production_receipts(original.id, 10).unwrap(),
        vec![dispatch.clone()]
    );
    let legitimate = reopened
        .append_production_receipt(ProductionReceiptInput {
            stage: "completed".into(),
            payload: json!({"result": {"job_ref": "original-job", "state": "succeeded"}}),
            ..input.clone()
        })
        .unwrap();
    assert_eq!(legitimate.revision, original.revision);
    drop(reopened);

    let final_read = StudioService::open(&path).unwrap();
    assert_eq!(final_read.project(original.id).unwrap(), edited);
    let rows = final_read.production_receipts(original.id, 10).unwrap();
    assert_eq!(rows.len(), 2);
    assert!(rows.contains(&dispatch) && rows.contains(&legitimate));
    assert!(rows.iter().all(|row| row.revision == original.revision));
    assert_eq!(
        final_read
            .latest_production_receipt(original.id, &input.request_id)
            .unwrap(),
        Some(legitimate)
    );
}
