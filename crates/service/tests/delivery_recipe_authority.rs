//! Authority and persistence oracles for the restored pre-delivery-recipe fixture.
//! Existing changes exercise the foundation contract; no recipe or provider consent is implied.
use motionwright_domain::{Change, MixIntent};
use motionwright_service::StudioService;
use motionwright_storage::{ApplyOutcome, ProjectBackup, StorageError};
use uuid::Uuid;

const LEGACY_REQUEST_ID: &str = "legacy-fixture-3";
fn legacy_backup() -> ProjectBackup {
    serde_json::from_slice(include_bytes!(
        "../../storage/tests/fixtures/pre_delivery_recipe_backup.json"
    ))
    .unwrap()
}
fn export(service: &StudioService, id: Uuid) -> ProjectBackup {
    service.export_project(id).unwrap()
}
#[track_caller]
fn assert_export_unchanged(service: &StudioService, expected: &ProjectBackup) {
    let actual = export(service, expected.project.id);
    // Only exported_at belongs to this invocation rather than persisted state.
    assert_eq!(actual.format_version, expected.format_version);
    assert_eq!(actual.source_storage_schema, expected.source_storage_schema);
    assert_eq!(actual.project, expected.project);
    assert_eq!(actual.events, expected.events);
    assert_eq!(actual.payload_sha256, expected.payload_sha256);
}
#[track_caller]
fn assert_replayed(original: &ApplyOutcome, replay: &ApplyOutcome) {
    assert!(replay.replayed);
    assert_eq!(replay.request_id, original.request_id);
    assert_eq!(replay.previous_revision, original.previous_revision);
    assert_eq!(replay.project, original.project);
}

#[test]
fn restored_fixture_rejects_previous_generation_and_foreign_resource_authority() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("authority.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let backup = legacy_backup();
    let previous_authority = backup.project.stamp();
    let restored = service.import_project(&backup).unwrap();
    assert_eq!(restored.id, backup.project.id);
    assert_eq!(restored.revision, backup.project.revision);
    assert_ne!(restored.generation, backup.project.generation);
    let restored_snapshot = export(&service, restored.id);
    assert_eq!(restored_snapshot.events, backup.events);
    let change = Change::RenameProject {
        title: "Restored fixture authority check".into(),
    };
    assert!(matches!(
        service.apply(restored.id, &previous_authority, LEGACY_REQUEST_ID, &change),
        Err(StorageError::GenerationConflict)
    ));
    assert_export_unchanged(&service, &restored_snapshot);

    // Import preserves journal events, not the source database's mutation receipts.
    let committed = service
        .apply(restored.id, &restored.stamp(), LEGACY_REQUEST_ID, &change)
        .unwrap();
    assert!(!committed.replayed);
    assert_eq!(committed.request_id, LEGACY_REQUEST_ID);
    assert_eq!(committed.previous_revision, restored.revision);
    assert_eq!(committed.project.generation, restored.generation);
    assert_eq!(committed.project.revision, restored.revision + 1);
    let unrelated = service
        .create_project("Unrelated authority control")
        .unwrap();
    let committed_snapshot = export(&service, restored.id);
    let unrelated_snapshot = export(&service, unrelated.id);
    // A matching durable receipt still cannot override resource or generation.
    assert!(matches!(
        service.apply(restored.id, &previous_authority, LEGACY_REQUEST_ID, &change),
        Err(StorageError::GenerationConflict)
    ));
    assert!(matches!(
        service.apply(
            unrelated.id,
            &committed.project.stamp(),
            LEGACY_REQUEST_ID,
            &change
        ),
        Err(StorageError::GenerationConflict)
    ));
    let missing_id = Uuid::from_u128(0xb54c_3827_40d9_4c07_86aa_c663_2da5_c9b7);
    assert_ne!(missing_id, restored.id);
    assert_ne!(missing_id, unrelated.id);
    assert!(matches!(
        service.apply(
            missing_id,
            &committed.project.stamp(),
            LEGACY_REQUEST_ID,
            &change
        ),
        Err(StorageError::NotFound)
    ));
    assert_export_unchanged(&service, &committed_snapshot);
    assert_export_unchanged(&service, &unrelated_snapshot);
    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert_export_unchanged(&reopened, &committed_snapshot);
    assert_export_unchanged(&reopened, &unrelated_snapshot);
}

#[test]
fn restored_fixture_preserves_cas_and_receipt_replay_across_connections_and_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("cas-replay.sqlite3");
    let first = StudioService::open(&database).unwrap();
    let backup = legacy_backup();
    let restored = first.import_project(&backup).unwrap();
    let second = StudioService::open(&database).unwrap();
    let stale_authority = second.project(restored.id).unwrap().stamp();
    assert_eq!(stale_authority, restored.stamp());
    let request_id = "restored-fixture-mix";
    let change = Change::SetMixIntent {
        mix: MixIntent {
            voice_gain_db: -3.0,
            music_gain_db: -17.0,
            target_lufs: Some(-18.0),
            target_true_peak_dbfs: Some(-1.0),
        },
    };
    let committed = first
        .apply(restored.id, &restored.stamp(), request_id, &change)
        .unwrap();
    assert!(!committed.replayed);
    assert_eq!(committed.previous_revision, restored.revision);
    assert_eq!(committed.project.generation, restored.generation);
    assert_eq!(committed.project.revision, restored.revision + 1);
    let stale_change = Change::RenameProject {
        title: "This stale mutation must not commit".into(),
    };
    assert!(matches!(
        second.apply(restored.id, &stale_authority, "restored-fixture-stale-write", &stale_change),
        Err(StorageError::Conflict { expected, actual })
            if expected == restored.revision && actual == committed.project.revision
    ));
    let replay = second
        .apply(restored.id, &stale_authority, request_id, &change)
        .unwrap();
    assert_replayed(&committed, &replay);
    let later_change = Change::RenameProject {
        title: "Later edit after the restored mix".into(),
    };
    let later = second
        .apply(
            restored.id,
            &committed.project.stamp(),
            "restored-fixture-later-edit",
            &later_change,
        )
        .unwrap();
    assert_eq!(later.previous_revision, committed.project.revision);
    assert_eq!(later.project.revision, committed.project.revision + 1);
    let current_snapshot = export(&second, restored.id);
    assert_eq!(current_snapshot.project, later.project);
    assert_eq!(current_snapshot.events.len(), backup.events.len() + 2);
    let changed_payload = Change::SetMixIntent {
        mix: MixIntent {
            voice_gain_db: -4.0,
            music_gain_db: -17.0,
            target_lufs: Some(-18.0),
            target_true_peak_dbfs: Some(-1.0),
        },
    };
    assert!(matches!(
        first.apply(restored.id, &stale_authority, request_id, &changed_payload),
        Err(StorageError::RequestReuse)
    ));
    assert_export_unchanged(&first, &current_snapshot);
    drop(second);
    drop(first);
    let reopened = StudioService::open(&database).unwrap();
    let replay = reopened
        .apply(restored.id, &stale_authority, request_id, &change)
        .unwrap();
    assert_replayed(&committed, &replay);
    assert_eq!(
        reopened.project(restored.id).unwrap(),
        later.project,
        "historical replay must not rewind the current project"
    );
    assert_export_unchanged(&reopened, &current_snapshot);
}
