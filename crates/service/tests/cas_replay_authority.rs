//! Durable CAS receipts must preserve the original result without transferring
//! resource or generation authority. These cases use public StudioService APIs
//! and independent SQLite reopenings, with disposable source assets.
use motionwright_domain::{Asset, Change, Project, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_storage::{ApplyOutcome, StorageError};
use std::fs;
use std::path::Path;
use uuid::Uuid;

fn committed_rename(path: &Path) -> (StudioService, Project, Change, ApplyOutcome) {
    let service = StudioService::open(path).unwrap();
    let project = service.create_project("Durable request scope").unwrap();
    let change = Change::RenameProject {
        title: "First committed title".into(),
    };
    let outcome = service
        .apply(project.id, &project.stamp(), "first-edit", &change)
        .unwrap();
    (service, project, change, outcome)
}

fn missing_asset() -> Change {
    Change::AddAsset {
        asset: Asset {
            id: Uuid::now_v7(),
            name: "unavailable-source.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some("ab".repeat(32)),
            source_revision: Some("synthetic-fixture".into()),
        },
    }
}

#[test]
fn durable_receipt_rejects_foreign_generation_after_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.sqlite3");
    let (service, original, change, first) = committed_rename(&path);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    let mut foreign = original.stamp();
    foreign.generation = Uuid::now_v7();

    let result = reopened.apply(original.id, &foreign, "first-edit", &change);
    assert!(
        matches!(result, Err(StorageError::GenerationConflict)),
        "a durable receipt must not return a project to a foreign generation: {result:?}"
    );
    assert_eq!(reopened.project(original.id).unwrap(), first.project);
    assert_eq!(
        reopened.export_project(original.id).unwrap().events.len(),
        1
    );
}

#[test]
fn durable_receipt_rejects_foreign_resource_after_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.sqlite3");
    let (service, original, change, first) = committed_rename(&path);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    let mut foreign = original.stamp();
    foreign.resource = format!("project:{}", Uuid::now_v7());

    let result = reopened.apply(original.id, &foreign, "first-edit", &change);
    assert!(
        matches!(result, Err(StorageError::GenerationConflict)),
        "a request key must not override its resource binding: {result:?}"
    );
    assert_eq!(reopened.project(original.id).unwrap(), first.project);
    assert_eq!(
        reopened.export_project(original.id).unwrap().events.len(),
        1
    );
}

#[test]
fn valid_stale_revision_replay_keeps_original_result_and_current_head() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.sqlite3");
    let (service, original, change, first) = committed_rename(&path);
    let latest = service
        .apply(
            original.id,
            &first.project.stamp(),
            "second-edit",
            &Change::RenameProject {
                title: "Later committed title".into(),
            },
        )
        .unwrap();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();

    let replay = reopened
        .apply(original.id, &original.stamp(), "first-edit", &change)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(replay.previous_revision, first.previous_revision);
    assert_eq!(replay.request_id, first.request_id);
    assert_eq!(reopened.project(original.id).unwrap(), latest.project);
    assert_eq!(
        reopened.export_project(original.id).unwrap().events.len(),
        2
    );

    let changed_payload = reopened.apply(
        original.id,
        &original.stamp(),
        "first-edit",
        &Change::RenameProject {
            title: "Different input for an existing key".into(),
        },
    );
    assert!(matches!(changed_payload, Err(StorageError::RequestReuse)));
    assert_eq!(reopened.project(original.id).unwrap(), latest.project);
}

#[test]
fn committed_asset_replays_after_source_blob_loss_without_recreating_bytes() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Durable asset receipt").unwrap();
    let source = dir.path().join("source.bin");
    fs::write(&source, b"disposable immutable asset").unwrap();
    let descriptor = service.ingest_blob_file(&source).unwrap();
    let change = Change::AddAsset {
        asset: Asset {
            id: Uuid::now_v7(),
            name: "source.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: Some("synthetic-fixture".into()),
        },
    };
    let first = service
        .apply(project.id, &project.stamp(), "asset-commit", &change)
        .unwrap();
    let stored = service
        .store()
        .lock()
        .verified_blob_path(&descriptor.sha256)
        .unwrap();
    fs::remove_file(&stored).unwrap();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();

    let replay = reopened
        .apply(project.id, &project.stamp(), "asset-commit", &change)
        .expect("a committed receipt remains readable when its source later becomes unavailable");
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(reopened.project(project.id).unwrap(), first.project);
    assert_eq!(reopened.export_project(project.id).unwrap().events.len(), 1);
    assert!(
        !stored.exists(),
        "receipt replay must not reimport source bytes"
    );
    assert!(matches!(
        reopened.store().lock().read_blob(&descriptor.sha256, 1024),
        Err(StorageError::BlobMissing { .. })
    ));
}

fn rejected_asset_attempt(
    service: &StudioService,
    project: &Project,
    expected: &RevisionStamp,
) -> Result<ApplyOutcome, StorageError> {
    let before = service.project(project.id).unwrap();
    let count = service.export_project(project.id).unwrap().events.len();
    let result = service.apply(project.id, expected, "unadmitted-asset", &missing_asset());
    assert_eq!(service.project(project.id).unwrap(), before);
    assert_eq!(
        service.export_project(project.id).unwrap().events.len(),
        count
    );
    result
}

#[test]
fn foreign_generation_is_rejected_before_missing_blob_lookup() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("state.sqlite3")).unwrap();
    let project = service.create_project("Generation scope").unwrap();
    let mut expected = project.stamp();
    expected.generation = Uuid::now_v7();
    let result = rejected_asset_attempt(&service, &project, &expected);
    assert!(
        matches!(result, Err(StorageError::GenerationConflict)),
        "scope rejection must precede source inspection: {result:?}"
    );
}

#[test]
fn foreign_resource_is_rejected_before_missing_blob_lookup() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("state.sqlite3")).unwrap();
    let project = service.create_project("Resource scope").unwrap();
    let mut expected = project.stamp();
    expected.resource = format!("project:{}", Uuid::now_v7());
    let result = rejected_asset_attempt(&service, &project, &expected);
    assert!(
        matches!(result, Err(StorageError::GenerationConflict)),
        "resource rejection must precede source inspection: {result:?}"
    );
}

#[test]
fn stale_new_mutation_is_rejected_before_missing_blob_lookup() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("state.sqlite3");
    let (service, original, _, first) = committed_rename(&path);
    let result = rejected_asset_attempt(&service, &original, &original.stamp());
    assert!(
        matches!(
            result,
            Err(StorageError::Conflict {
                expected: 0,
                actual: 1
            })
        ),
        "new mutations still require the current revision before reading sources: {result:?}"
    );
    assert_eq!(service.project(original.id).unwrap(), first.project);

    let allowed_scope = service.apply(
        original.id,
        &first.project.stamp(),
        "authorized-missing-asset",
        &missing_asset(),
    );
    assert!(
        matches!(allowed_scope, Err(StorageError::BlobMissing { .. })),
        "an authorized current mutation must still validate its source"
    );
}
