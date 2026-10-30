//! Physical CAS + SQLite contract for owner-local Native capsule inspection.
use motionwright_domain::{Change, NativeCapsuleSourceSpec, Project};
use motionwright_service::{ImportLimits, StudioService};
use motionwright_storage::StorageError;
use rusqlite::{Connection, OpenFlags};
use std::{fs, path::PathBuf};
use uuid::Uuid;

struct Fixture {
    _temp: tempfile::TempDir,
    database: PathBuf,
    source: PathBuf,
    source_bytes: Vec<u8>,
    service: StudioService,
    project: Project,
    capsule_id: Uuid,
}

fn fixture(bytes: Vec<u8>) -> Fixture {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("capsule-inspection.sqlite3");
    let source = temp.path().join("author-original.json");
    fs::write(&source, &bytes).unwrap();
    let service = StudioService::open(&database).unwrap();
    let first = service
        .create_project("Native capsule original source")
        .unwrap();
    let scene = service
        .apply(
            first.id,
            &first.stamp(),
            "add-authored-scene",
            &Change::AddScene {
                name: "Original scene".into(),
                objective: "Inspect imported bytes, not a synthetic source".into(),
                duration_seconds: 3,
            },
        )
        .unwrap()
        .project;
    let capsule_id = Uuid::new_v4();
    let spec = NativeCapsuleSourceSpec {
        name: "Original authored capsule source".into(),
        media_type: "application/json".into(),
        capsule_id,
        scene_id: scene.scenes[0].id,
        label: "Original import".into(),
        editable_parameters: vec!["label".into()],
        native_editor_hint: "Unverified source editor".into(),
    };
    let project = service
        .import_native_capsule_source_file(
            scene.id,
            &scene.stamp(),
            "import-original",
            &source,
            spec,
        )
        .unwrap()
        .project;
    Fixture {
        _temp: temp,
        database,
        source,
        source_bytes: bytes,
        service,
        project,
        capsule_id,
    }
}

fn monitor(path: &std::path::Path) -> Connection {
    Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap()
}
fn data_version(db: &Connection) -> i64 {
    db.query_row("PRAGMA data_version", [], |row| row.get(0))
        .unwrap()
}
fn receipts(db: &Connection, id: Uuid) -> i64 {
    db.query_row(
        "SELECT count(*) FROM receipts WHERE project_id=?1",
        [id.to_string()],
        |row| row.get(0),
    )
    .unwrap()
}

#[test]
fn source_bytes_are_real_bounded_cas_without_sqlite_event_or_receipt_side_effects() {
    let f = fixture(br#"{"author":"owner","version":1,"labels":["original"]}"#.to_vec());
    let project_before = f.project.clone();
    let backup_before = f.service.export_project(f.project.id).unwrap();
    let monitor = monitor(&f.database);
    let version_before = data_version(&monitor);
    let receipts_before = receipts(&monitor, f.project.id);

    let observed = f
        .service
        .verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id)
        .unwrap();
    let capsule = &f.project.production_design.capsules[0];
    assert_eq!(observed.stamp, project_before.stamp());
    assert_eq!(observed.capsule_id, capsule.id);
    assert_eq!(observed.scene_id, capsule.scene_id);
    assert_eq!(observed.source_asset_id, capsule.source_asset_id);
    assert_eq!(observed.source_sha256, capsule.source_sha256);
    assert_eq!(observed.media_type, "application/json");
    assert_eq!(observed.bytes(), f.source_bytes);
    assert_eq!(observed.byte_len(), f.source_bytes.len());
    // Merely observing original data cannot invent source fidelity.
    assert_eq!(f.service.project(f.project.id).unwrap(), project_before);
    assert_eq!(data_version(&monitor), version_before);
    assert_eq!(receipts(&monitor, f.project.id), receipts_before);
    assert_eq!(
        f.service.export_project(f.project.id).unwrap().events,
        backup_before.events
    );

    // Rename only the originally imported *test* file: CAS is independent of
    // its old pathname, and a reopened Store still owns these exact bytes.
    fs::rename(&f.source, f.source.with_extension("moved")).unwrap();
    drop(monitor);
    drop(f.service);
    let reopened = StudioService::open(&f.database).unwrap();
    let recovered = reopened
        .verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id)
        .unwrap();
    assert_eq!(recovered.bytes(), f.source_bytes);
    assert_eq!(recovered.stamp, f.project.stamp());
    assert_eq!(reopened.project(f.project.id).unwrap(), f.project);
}

#[test]
fn absent_foreign_stale_revision_or_generation_never_reads_an_unowned_source() {
    let f = fixture(br#"{"owner":"original"}"#.to_vec());
    let second = f
        .service
        .create_project("Independent foreign owner")
        .unwrap();
    let observer = monitor(&f.database);
    let before_version = data_version(&observer);
    let before_receipts = receipts(&observer, f.project.id);
    let mut wrong = f.project.stamp();
    wrong.generation = Uuid::new_v4();
    assert!(matches!(
        f.service
            .verified_native_capsule_source(f.project.id, &wrong, f.capsule_id),
        Err(StorageError::GenerationConflict)
    ));
    let mut wrong = f.project.stamp();
    wrong.resource = format!("project:{}", Uuid::new_v4());
    assert!(matches!(
        f.service
            .verified_native_capsule_source(f.project.id, &wrong, f.capsule_id),
        Err(StorageError::GenerationConflict)
    ));
    let mut wrong = f.project.stamp();
    wrong.revision += 1;
    assert!(matches!(
        f.service
            .verified_native_capsule_source(f.project.id, &wrong, f.capsule_id),
        Err(StorageError::Conflict { .. })
    ));
    assert!(matches!(
        f.service
            .verified_native_capsule_source(f.project.id, &f.project.stamp(), Uuid::new_v4()),
        Err(StorageError::NotFound)
    ));
    assert!(matches!(
        f.service
            .verified_native_capsule_source(second.id, &second.stamp(), f.capsule_id),
        Err(StorageError::NotFound)
    ));
    assert!(matches!(
        f.service
            .verified_native_capsule_source(Uuid::new_v4(), &f.project.stamp(), f.capsule_id),
        Err(StorageError::NotFound)
    ));
    assert_eq!(data_version(&observer), before_version);
    assert_eq!(receipts(&observer, f.project.id), before_receipts);
}

#[test]
fn service_owner_limit_cannot_be_raised_by_requested_capsule_or_source() {
    let f = fixture(br#"{"large":"longer than the selected small host read budget"}"#.to_vec());
    let limited = StudioService::open_with_import_limits(
        &f.database,
        ImportLimits::default().with_source_bytes(16).unwrap(),
    )
    .unwrap();
    let observer = monitor(&f.database);
    let old_version = data_version(&observer);
    let old_receipts = receipts(&observer, f.project.id);
    assert!(matches!(
        limited.verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id),
        Err(StorageError::BlobTooLarge { limit: 16, .. })
    ));
    assert_eq!(limited.project(f.project.id).unwrap(), f.project);
    assert_eq!(data_version(&observer), old_version);
    assert_eq!(receipts(&observer, f.project.id), old_receipts);
    assert_eq!(
        f.service
            .verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id)
            .unwrap()
            .bytes(),
        f.source_bytes
    );
}

#[test]
fn concurrently_advanced_project_stamp_cannot_reuse_previous_capsule_observation() {
    let f = fixture(br#"{"revision":"original"}"#.to_vec());
    let other_connection = StudioService::open(&f.database).unwrap();
    let original = f
        .service
        .verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id)
        .unwrap();
    let current = other_connection
        .apply(
            f.project.id,
            &f.project.stamp(),
            "independent-rename",
            &Change::RenameProject {
                title: "Changed by another connection".into(),
            },
        )
        .unwrap()
        .project;
    assert!(matches!(
        f.service
            .verified_native_capsule_source(f.project.id, &original.stamp, f.capsule_id),
        Err(StorageError::Conflict { .. })
    ));
    let fresh = f
        .service
        .verified_native_capsule_source(current.id, &current.stamp(), f.capsule_id)
        .unwrap();
    assert_eq!(fresh.bytes(), original.bytes());
    assert_eq!(fresh.stamp, current.stamp());
    assert_eq!(f.service.project(current.id).unwrap(), current);
}

#[test]
fn missing_physical_cas_original_cannot_be_replaced_by_metadata_or_a_path() {
    let f = fixture(br#"{"source":"only real CAS readback counts"}"#.to_vec());
    let digest = f.project.production_design.capsules[0]
        .source_sha256
        .clone();
    let original_path = f
        .service
        .store()
        .lock()
        .verified_blob_path(&digest)
        .unwrap();
    let separated_path = f._temp.path().join("temporarily-separated-original");
    fs::rename(&original_path, &separated_path).unwrap();
    let observed =
        f.service
            .verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id);
    // Restore the exact synthetic original before assertions or exit.
    fs::rename(&separated_path, &original_path).unwrap();
    assert!(observed.is_err(), "missing original must fail closed");
    let restored = f
        .service
        .verified_native_capsule_source(f.project.id, &f.project.stamp(), f.capsule_id)
        .unwrap();
    assert_eq!(restored.bytes(), f.source_bytes);
}
