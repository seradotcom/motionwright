use motionwright_domain::Change;
use motionwright_service::{ProductionReceiptInput, StudioService};
use motionwright_storage::StorageError;
use serde_json::json;

#[test]
fn dispatch_admission_rejects_a_preflight_revision_changed_by_an_independent_service() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("production-dispatch-current-head.sqlite3");
    let editor = StudioService::open(&path).unwrap();
    let prepared = editor.create_project("Prepared dispatch revision").unwrap();
    let dispatcher = StudioService::open(&path).unwrap();
    let input = ProductionReceiptInput {
        project_id: prepared.id,
        generation: prepared.generation,
        revision: prepared.revision,
        request_id: "preflight-only-request".into(),
        request_sha256: "a".repeat(64),
        command: "driver.motion-canvas.render.start".into(),
        stage: "dispatching".into(),
        payload: json!({"mutation": true}),
    };
    assert!(
        dispatcher
            .production_receipts(prepared.id, 10)
            .unwrap()
            .is_empty()
    );
    let edited = editor
        .apply(
            prepared.id,
            &prepared.stamp(),
            "peer-edit-before-dispatch-admission",
            &Change::AddScene {
                name: "Peer scene".into(),
                objective: "Exercise committed head authority during production admission".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    assert!(edited.revision > prepared.revision);

    let rejected = dispatcher.append_production_receipt(input.clone());
    assert!(
        matches!(
            rejected,
            Err(StorageError::Conflict { expected, actual })
                if expected == prepared.revision && actual == edited.revision
        ),
        "the service must reject a dispatch prepared before the peer commit: {rejected:?}"
    );
    assert!(
        dispatcher
            .production_receipts(prepared.id, 10)
            .unwrap()
            .is_empty()
    );
    assert!(
        dispatcher
            .latest_production_receipt(prepared.id, &input.request_id)
            .unwrap()
            .is_none()
    );
    drop(editor);
    drop(dispatcher);

    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(prepared.id).unwrap(), edited);
    assert!(
        reopened
            .production_receipts(prepared.id, 10)
            .unwrap()
            .is_empty()
    );
    let current = reopened
        .append_production_receipt(ProductionReceiptInput {
            revision: edited.revision,
            request_id: "independent-current-service-request".into(),
            ..input
        })
        .unwrap();
    assert_eq!(current.revision, edited.revision);
    drop(reopened);
    let final_read = StudioService::open(&path).unwrap();
    assert_eq!(final_read.project(prepared.id).unwrap(), edited);
    assert_eq!(
        final_read.production_receipts(prepared.id, 10).unwrap(),
        vec![current]
    );
}
