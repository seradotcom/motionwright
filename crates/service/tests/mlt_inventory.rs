use motionwright_service::StudioService;

#[test]
fn persisted_inventory_is_empty_until_native_project_receipts_exist() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("mlt-inventory.sqlite3")).unwrap();
    let project = service.create_project("MLT inventory fixture").unwrap();
    let report = service
        .mlt_cleanup_inventory_snapshot(project.id, project.generation, project.revision)
        .unwrap();
    assert_eq!(report.source_receipts_scanned, 0);
    assert_eq!(report.confirmed_native_creations_current_revision, 0);
    assert!(!report.verified_native_project_cleanup);
}

#[test]
fn persisted_mlt_lifetime_survives_reopen_without_claiming_native_cleanup() {
    use motionwright_service::ProductionReceiptInput;
    use serde_json::json;
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("mlt-persisted.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Owner source").unwrap();
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "mlt-original-creation".into(),
            request_sha256: "a".repeat(64),
            command: "driver.mlt-video.project.create".into(),
            stage: "completed".into(),
            payload: json!({"result":{
                "provenance_checked":true,
                "data":{"project":"opaque-provider-project","revision":"b".repeat(64)},
                "execution":{"provenance":{
                    "provider":"driver:mlt-video","provider_generation":7
                }}
            }}),
        })
        .unwrap();
    let first = service
        .mlt_cleanup_inventory_snapshot(project.id, project.generation, project.revision)
        .unwrap();
    assert_eq!(first.confirmed_native_creations_current_revision, 1);
    assert!(!first.automatic_destructive_cleanup_allowed);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(
        first,
        reopened
            .mlt_cleanup_inventory_snapshot(project.id, project.generation, project.revision,)
            .unwrap()
    );
    assert!(
        reopened
            .mlt_cleanup_inventory_snapshot(project.id, project.generation, project.revision + 1,)
            .is_err()
    );
}
