//! Source-save authority and SQLite rollback through the public Store API.
use motionwright_domain::{Asset, Change, NativeCapsuleSourceSpec, Project};
use motionwright_storage::{FileImportIntent, StorageError, Store};
use rusqlite::Connection;
use std::{fs, path::PathBuf};
use uuid::Uuid;

struct Fixture {
    store: Store,
    project: Project,
    source: PathBuf,
    database: PathBuf,
    _directory: tempfile::TempDir,
}

fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("source.sqlite3");
    let source = directory.path().join("authored.json");
    fs::write(&source, br#"{"version":1,"value":"authored"}"#).unwrap();
    let mut store = Store::open(&database).unwrap();
    let initial = store.create_named_project("Store source fixture").unwrap();
    let project = store
        .apply(
            initial.id,
            &initial.stamp(),
            "fixture-scene",
            &Change::AddScene {
                name: "Native source".into(),
                objective: "Atomic binding".into(),
                duration_seconds: 3,
            },
        )
        .unwrap()
        .project;
    Fixture {
        store,
        project,
        source,
        database,
        _directory: directory,
    }
}

fn spec(project: &Project) -> NativeCapsuleSourceSpec {
    NativeCapsuleSourceSpec {
        name: "Source".into(),
        media_type: "application/json".into(),
        capsule_id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        label: "Source capsule".into(),
        editable_parameters: vec!["label".into()],
        native_editor_hint: "Synthetic editor".into(),
    }
}

fn change(spec: &NativeCapsuleSourceSpec, digest: &str) -> Change {
    let asset = Asset {
        id: Uuid::new_v4(),
        name: spec.name.clone(),
        media_type: spec.media_type.clone(),
        content_sha256: Some(digest.into()),
        source_revision: Some("native-capsule-source-import".into()),
    };
    let capsule = spec.bind_asset(&asset).unwrap();
    Change::ImportNativeCapsuleSource { asset, capsule }
}

fn counts(database: &std::path::Path) -> [i64; 3] {
    let connection = Connection::open(database).unwrap();
    ["projects", "events", "receipts"].map(|table| {
        connection
            .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .unwrap()
    })
}

#[test]
fn direct_source_change_requires_actual_content_addressed_bytes() {
    let mut f = fixture();
    let spec = spec(&f.project);
    let before = counts(&f.database);
    let attempt = change(&spec, &"a".repeat(64));
    assert!(
        f.store
            .apply(f.project.id, &f.project.stamp(), "missing-source", &attempt)
            .is_err()
    );
    assert_eq!(f.store.load_project(f.project.id).unwrap(), f.project);
    assert_eq!(counts(&f.database), before);
}

#[test]
fn source_digest_verification_rejects_a_corrupt_blob_before_commit() {
    let mut f = fixture();
    let spec = spec(&f.project);
    let descriptor = f.store.ingest_blob_file(&f.source).unwrap();
    let path = f.store.verified_blob_path(&descriptor.sha256).unwrap();
    fs::write(path, b"corrupt fixture bytes").unwrap();
    let before = counts(&f.database);
    let attempt = change(&spec, &descriptor.sha256);
    assert!(
        f.store
            .apply(f.project.id, &f.project.stamp(), "corrupt-source", &attempt)
            .is_err()
    );
    assert_eq!(f.store.load_project(f.project.id).unwrap(), f.project);
    assert_eq!(counts(&f.database), before);
}

#[test]
fn stale_generation_is_rejected_before_missing_blob_observation() {
    let mut f = fixture();
    let spec = spec(&f.project);
    let attempt = change(&spec, &"b".repeat(64));
    let mut foreign = f.project.stamp();
    foreign.generation = Uuid::new_v4();
    assert!(matches!(
        f.store
            .apply(f.project.id, &foreign, "foreign-source", &attempt),
        Err(StorageError::GenerationConflict)
    ));
    assert_eq!(f.store.load_project(f.project.id).unwrap(), f.project);
}

#[test]
fn independently_opened_writer_cannot_partially_publish_a_stale_capsule() {
    let mut f = fixture();
    let mut other = Store::open(&f.database).unwrap();
    let descriptor = f.store.ingest_blob_file(&f.source).unwrap();
    let first_spec = spec(&f.project);
    let second_spec = spec(&f.project);
    let first = change(&first_spec, &descriptor.sha256);
    let second = change(&second_spec, &descriptor.sha256);
    let winner = f
        .store
        .apply(f.project.id, &f.project.stamp(), "winner", &first)
        .unwrap();
    let before = counts(&f.database);
    assert!(matches!(
        other.apply(f.project.id, &f.project.stamp(), "loser", &second),
        Err(StorageError::Conflict { .. })
    ));
    assert_eq!(other.load_project(f.project.id).unwrap(), winner.project);
    assert_eq!(counts(&f.database), before);
    assert_eq!(winner.project.assets.len(), f.project.assets.len() + 1);
    assert_eq!(winner.project.production_design.capsules.len(), 1);
}

#[test]
fn altered_receipt_cannot_rebind_the_capsule_to_a_foreign_asset() {
    let mut f = fixture();
    let spec = spec(&f.project);
    let descriptor = f.store.ingest_blob_file(&f.source).unwrap();
    let mutation = change(&spec, &descriptor.sha256);
    let original = f
        .store
        .apply(f.project.id, &f.project.stamp(), "bound-receipt", &mutation)
        .unwrap();
    let mut forged = original.clone();
    forged.project.production_design.capsules[0].source_asset_id = Uuid::new_v4();
    let observer = Connection::open(&f.database).unwrap();
    observer
        .execute(
            "UPDATE receipts SET result_json=?1 WHERE project_id=?2 AND request_id=?3",
            rusqlite::params![
                serde_json::to_string(&forged).unwrap(),
                f.project.id.to_string(),
                "bound-receipt"
            ],
        )
        .unwrap();
    let before = counts(&f.database);
    assert!(
        f.store
            .inspect_file_import(
                f.project.id,
                &f.project.stamp(),
                "bound-receipt",
                &FileImportIntent::NativeCapsuleSource { spec },
                false
            )
            .is_err()
    );
    assert_eq!(
        f.store.load_project(f.project.id).unwrap(),
        original.project
    );
    assert_eq!(counts(&f.database), before);
}

#[test]
fn committed_receipt_recovery_does_not_reimport_a_missing_blob() {
    let mut f = fixture();
    let spec = spec(&f.project);
    let descriptor = f.store.ingest_blob_file(&f.source).unwrap();
    let mutation = change(&spec, &descriptor.sha256);
    let original = f
        .store
        .apply(f.project.id, &f.project.stamp(), "known-source", &mutation)
        .unwrap();
    let path = f.store.verified_blob_path(&descriptor.sha256).unwrap();
    fs::remove_file(path).unwrap();
    let before = counts(&f.database);
    let recovered = f
        .store
        .inspect_file_import(
            f.project.id,
            &f.project.stamp(),
            "known-source",
            &FileImportIntent::NativeCapsuleSource { spec },
            false,
        )
        .unwrap()
        .unwrap();
    assert!(recovered.outcome.replayed);
    assert_eq!(recovered.outcome.project, original.project);
    assert_eq!(counts(&f.database), before);
    assert!(f.store.verify_blob(&descriptor.sha256).is_err());
}
