//! Atomic immutable-source import through the real SQLite-backed service.
//! Synthetic authored data only: no native execution or provider grant.
use motionwright_domain::{Change, Fidelity, NativeCapsuleSourceSpec, Project};
use motionwright_service::StudioService;
use motionwright_storage::{ApplyOutcome, FileImportIntent, ProjectBackup, StorageError};
use rusqlite::Connection;
use std::{fs, path::PathBuf};
use uuid::Uuid;

struct Fixture {
    service: StudioService,
    project: Project,
    source: PathBuf,
    database: PathBuf,
    _directory: tempfile::TempDir,
}

fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("source.sqlite3");
    let source = directory.path().join("source.json");
    fs::write(
        &source,
        br#"{"version":1,"label":"original synthetic source"}"#,
    )
    .unwrap();
    let service = StudioService::open(&database).unwrap();
    let initial = service.create_project("Atomic source fixture").unwrap();
    let project = service
        .apply(
            initial.id,
            &initial.stamp(),
            "fixture-scene",
            &Change::AddScene {
                name: "Source scene".into(),
                objective: "Keep authored source and capsule bound".into(),
                duration_seconds: 3,
            },
        )
        .unwrap()
        .project;
    Fixture {
        service,
        project,
        source,
        database,
        _directory: directory,
    }
}

fn spec(project: &Project) -> NativeCapsuleSourceSpec {
    NativeCapsuleSourceSpec {
        name: "Authored source".into(),
        media_type: "application/json".into(),
        capsule_id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        label: "Editable source".into(),
        editable_parameters: vec!["label".into()],
        native_editor_hint: "Synthetic native editor".into(),
    }
}

fn save(
    f: &Fixture,
    project: &Project,
    key: &str,
    spec: &NativeCapsuleSourceSpec,
) -> Result<ApplyOutcome, StorageError> {
    f.service.import_native_capsule_source_file(
        project.id,
        &project.stamp(),
        key,
        &f.source,
        spec.clone(),
    )
}

fn snapshot(f: &Fixture) -> ProjectBackup {
    f.service.export_project(f.project.id).unwrap()
}

#[track_caller]
fn unchanged(f: &Fixture, before: &ProjectBackup) {
    let after = snapshot(f);
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
}

#[test]
fn source_and_capsule_commit_as_one_revision_and_one_event() {
    let f = fixture();
    let spec = spec(&f.project);
    let before = snapshot(&f);
    let outcome = save(&f, &f.project, "source-save", &spec).unwrap();
    assert!(!outcome.replayed);
    assert_eq!(outcome.previous_revision, f.project.revision);
    assert_eq!(outcome.project.revision, f.project.revision + 1);
    assert_eq!(outcome.project.assets.len(), f.project.assets.len() + 1);
    let capsule = &outcome.project.production_design.capsules[0];
    assert_eq!(capsule.id, spec.capsule_id);
    let asset = outcome
        .project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .unwrap();
    assert_eq!(
        asset.content_sha256.as_deref(),
        Some(capsule.source_sha256.as_str())
    );
    assert_eq!(snapshot(&f).events.len(), before.events.len() + 1);
    let observer = Connection::open(&f.database).unwrap();
    let receipts: i64 = observer
        .query_row(
            "SELECT count(*) FROM receipts WHERE project_id=?1 AND request_id='source-save'",
            [f.project.id.to_string()],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(receipts, 1);
}

#[test]
fn invalid_capsule_target_does_not_commit_an_unbound_asset() {
    let f = fixture();
    let mut spec = spec(&f.project);
    spec.scene_id = Uuid::new_v4();
    let before = snapshot(&f);
    assert!(matches!(
        save(&f, &f.project, "rejected-source", &spec),
        Err(StorageError::Domain(_))
    ));
    unchanged(&f, &before);
    assert!(
        f.service
            .recover_file_import(
                f.project.id,
                &f.project.stamp(),
                "rejected-source",
                &FileImportIntent::NativeCapsuleSource { spec }
            )
            .unwrap()
            .is_none()
    );
}

#[test]
fn invalid_capsule_metadata_does_not_commit_an_unbound_asset() {
    let f = fixture();
    let mut spec = spec(&f.project);
    spec.label.clear();
    let before = snapshot(&f);
    assert!(save(&f, &f.project, "bad-metadata", &spec).is_err());
    unchanged(&f, &before);
}

#[test]
fn receipt_insert_failure_rolls_back_asset_capsule_revision_and_event() {
    let f = fixture();
    let spec = spec(&f.project);
    let before = snapshot(&f);
    let observer = Connection::open(&f.database).unwrap();
    observer
        .execute_batch(
            "CREATE TRIGGER reject_source_receipt BEFORE INSERT ON receipts
         WHEN NEW.request_id='rollback-source'
         BEGIN SELECT RAISE(ABORT,'injected receipt write failure'); END;",
        )
        .unwrap();
    assert!(save(&f, &f.project, "rollback-source", &spec).is_err());
    unchanged(&f, &before);
    drop(observer);
    let reopened = StudioService::open(&f.database).unwrap();
    let retained = reopened.export_project(f.project.id).unwrap();
    assert_eq!(retained.project, before.project);
    assert_eq!(retained.events, before.events);
}

#[test]
fn event_insert_failure_rolls_back_the_updated_document() {
    let f = fixture();
    let spec = spec(&f.project);
    let before = snapshot(&f);
    let observer = Connection::open(&f.database).unwrap();
    observer
        .execute_batch(
            "CREATE TRIGGER reject_source_event BEFORE INSERT ON events
         BEGIN SELECT RAISE(ABORT,'injected event write failure'); END;",
        )
        .unwrap();
    assert!(save(&f, &f.project, "rollback-event", &spec).is_err());
    unchanged(&f, &before);
}

#[test]
fn committed_source_save_replays_after_database_reopen() {
    let f = fixture();
    let spec = spec(&f.project);
    let original = save(&f, &f.project, "known-source", &spec).unwrap();
    let before = snapshot(&f);
    let reopened = StudioService::open(&f.database).unwrap();
    let replay = reopened
        .import_native_capsule_source_file(
            f.project.id,
            &f.project.stamp(),
            "known-source",
            &f.source,
            spec,
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, original.project);
    assert_eq!(replay.previous_revision, original.previous_revision);
    unchanged(&f, &before);
}

#[test]
fn recovery_reads_original_result_without_source_after_later_edit() {
    let f = fixture();
    let spec = spec(&f.project);
    let original = save(&f, &f.project, "recover-original", &spec).unwrap();
    f.service
        .apply(
            f.project.id,
            &original.project.stamp(),
            "later-rename",
            &Change::RenameProject {
                title: "Newer canonical state".into(),
            },
        )
        .unwrap();
    fs::remove_file(&f.source).unwrap();
    let before = snapshot(&f);
    let recovered = f
        .service
        .recover_file_import(
            f.project.id,
            &f.project.stamp(),
            "recover-original",
            &FileImportIntent::NativeCapsuleSource { spec },
        )
        .unwrap()
        .unwrap();
    assert!(recovered.replayed);
    assert_eq!(recovered.project, original.project);
    assert_ne!(recovered.project.revision, before.project.revision);
    unchanged(&f, &before);
}

#[test]
fn changed_capsule_intent_is_request_reuse_before_source_io() {
    let f = fixture();
    let spec = spec(&f.project);
    save(&f, &f.project, "bound-intent", &spec).unwrap();
    fs::remove_file(&f.source).unwrap();
    let before = snapshot(&f);
    let mut changed = spec;
    changed.label = "Different operation".into();
    assert!(matches!(
        save(&f, &f.project, "bound-intent", &changed),
        Err(StorageError::RequestReuse)
    ));
    unchanged(&f, &before);
}

#[test]
fn changed_source_bytes_cannot_reuse_a_committed_request() {
    let f = fixture();
    let spec = spec(&f.project);
    save(&f, &f.project, "bound-source", &spec).unwrap();
    let before = snapshot(&f);
    fs::write(&f.source, br#"{"version":1,"label":"different source"}"#).unwrap();
    assert!(matches!(
        save(&f, &f.project, "bound-source", &spec),
        Err(StorageError::RequestReuse)
    ));
    unchanged(&f, &before);
}

#[test]
fn stale_revision_and_foreign_generation_precede_missing_source_io() {
    let f = fixture();
    let spec = spec(&f.project);
    let current = f
        .service
        .apply(
            f.project.id,
            &f.project.stamp(),
            "advance-first",
            &Change::RenameProject {
                title: "Advanced".into(),
            },
        )
        .unwrap();
    fs::remove_file(&f.source).unwrap();
    let before = snapshot(&f);
    assert!(matches!(
        save(&f, &f.project, "stale-save", &spec),
        Err(StorageError::Conflict { .. })
    ));
    let mut foreign = current.project.stamp();
    foreign.generation = Uuid::new_v4();
    assert!(matches!(
        f.service.import_native_capsule_source_file(
            f.project.id,
            &foreign,
            "foreign-save",
            &f.source,
            spec,
        ),
        Err(StorageError::GenerationConflict)
    ));
    unchanged(&f, &before);
}

#[test]
fn later_source_save_preserves_previous_source_and_clears_fidelity_claims() {
    let f = fixture();
    let spec = spec(&f.project);
    let first = save(&f, &f.project, "first-source", &spec).unwrap();
    let old_capsule = first.project.production_design.capsules[0].clone();
    let mut claimed = old_capsule.clone();
    claimed.fidelity.visual = Fidelity::Native;
    claimed.fidelity.temporal = Fidelity::Native;
    claimed.fidelity.structural = Fidelity::Native;
    claimed.fidelity.editable = Fidelity::Native;
    claimed.fidelity.losses.clear();
    claimed.fidelity.evidence_sha256 = Some("a".repeat(64));
    let prior = f
        .service
        .apply(
            f.project.id,
            &first.project.stamp(),
            "synthetic-old-claim",
            &Change::UpsertNativeCapsule { capsule: claimed },
        )
        .unwrap();
    fs::write(&f.source, br#"{"version":1,"label":"new authored source"}"#).unwrap();
    let changed = save(&f, &prior.project, "replacement-source", &spec).unwrap();
    let capsule = &changed.project.production_design.capsules[0];
    assert_eq!(capsule.id, old_capsule.id);
    assert_ne!(capsule.source_asset_id, old_capsule.source_asset_id);
    assert!(
        changed
            .project
            .assets
            .iter()
            .any(|asset| asset.id == old_capsule.source_asset_id)
    );
    assert_eq!(capsule.fidelity.visual, Fidelity::Unavailable);
    assert_eq!(capsule.fidelity.temporal, Fidelity::Unavailable);
    assert_eq!(capsule.fidelity.structural, Fidelity::Unavailable);
    assert_eq!(capsule.fidelity.editable, Fidelity::Unavailable);
    assert!(capsule.fidelity.evidence_sha256.is_none());
    assert!(capsule.property_fidelity.is_empty());
    assert!(!capsule.fidelity.losses.is_empty());
    let shared = f.service.store();
    assert!(
        shared
            .lock()
            .read_blob(&old_capsule.source_sha256, 4096)
            .is_ok()
    );
}

#[test]
fn asset_receipt_cannot_be_reinterpreted_as_a_capsule_save() {
    let f = fixture();
    f.service
        .import_asset_file(
            f.project.id,
            &f.project.stamp(),
            "asset-only",
            &f.source,
            "Authored source".into(),
            "application/json".into(),
        )
        .unwrap();
    let before = snapshot(&f);
    let spec = spec(&f.project);
    assert!(matches!(
        save(&f, &f.project, "asset-only", &spec),
        Err(StorageError::RequestReuse)
    ));
    unchanged(&f, &before);
}

#[test]
fn capsule_receipt_cannot_be_reinterpreted_as_a_plain_asset_import() {
    let f = fixture();
    let spec = spec(&f.project);
    save(&f, &f.project, "capsule-only", &spec).unwrap();
    let before = snapshot(&f);
    assert!(matches!(
        f.service.recover_file_import(
            f.project.id,
            &f.project.stamp(),
            "capsule-only",
            &FileImportIntent::Asset {
                name: spec.name,
                media_type: spec.media_type,
            }
        ),
        Err(StorageError::RequestReuse)
    ));
    unchanged(&f, &before);
}

#[test]
fn missing_receipt_observation_does_not_create_a_capsule_or_asset() {
    let f = fixture();
    let before = snapshot(&f);
    let spec = spec(&f.project);
    fs::remove_file(&f.source).unwrap();
    assert!(
        f.service
            .recover_file_import(
                f.project.id,
                &f.project.stamp(),
                "unobserved",
                &FileImportIntent::NativeCapsuleSource { spec }
            )
            .unwrap()
            .is_none()
    );
    unchanged(&f, &before);
}

#[test]
fn portable_source_save_reopens_with_new_generation_without_inheriting_receipts() {
    let f = fixture();
    let spec = spec(&f.project);
    let original_bytes = fs::read(&f.source).unwrap();
    let key = "portable-native-source";
    let project = save(&f, &f.project, key, &spec).unwrap().project;
    let digest = project.production_design.capsules[0].source_sha256.clone();
    let before = f
        .service
        .export_project_at(project.id, &project.stamp())
        .unwrap();
    let bundle = f._directory.path().join("native-source-bundle");
    let manifest = f
        .service
        .export_project_bundle_at(project.id, &project.stamp(), &bundle)
        .unwrap();
    assert_eq!(manifest.backup.project, project);
    assert_eq!(manifest.backup.events, before.events);
    let serialized: motionwright_storage::ProjectBundleManifest =
        serde_json::from_slice(&fs::read(bundle.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(serialized.backup.project, manifest.backup.project);
    assert_eq!(serialized.backup.events, manifest.backup.events);

    // A separate directory ensures that the destination has an independent CAS.
    let restored = f._directory.path().join("restored");
    fs::create_dir(&restored).unwrap();
    let target_path = restored.join("state.sqlite3");
    let target = StudioService::open(&target_path).unwrap();
    {
        let shared = target.store();
        assert!(shared.lock().read_blob(&digest, 4096).is_err());
    }
    let original_cas = {
        let shared = f.service.store();
        let store = shared.lock();
        store.verified_blob_path(&digest).unwrap()
    };
    // Remove only synthetic fixture files: restoration must use the bundle.
    fs::remove_file(&f.source).unwrap();
    fs::remove_file(&original_cas).unwrap();
    let inspected = target
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    assert_eq!(inspected.plan.blob_count, 1);
    let imported = target
        .import_project_bundle_inspected(&bundle, &inspected.fingerprint)
        .unwrap();
    assert!(!imported.generation.is_nil());
    assert_ne!(imported.generation, project.generation);
    let mut expected = project.clone();
    expected.generation = imported.generation;
    expected.updated_at = imported.updated_at;
    assert_eq!(imported, expected);
    drop(target);

    let reopened = StudioService::open(&target_path).unwrap();
    assert_eq!(reopened.project(imported.id).unwrap(), imported);
    let imported_backup = reopened
        .export_project_at(imported.id, &imported.stamp())
        .unwrap();
    assert_eq!(imported_backup.events, before.events);
    let intent = FileImportIntent::NativeCapsuleSource { spec };
    assert!(matches!(
        reopened.recover_file_import(imported.id, &project.stamp(), key, &intent),
        Err(StorageError::GenerationConflict)
    ));
    assert!(
        reopened
            .recover_file_import(imported.id, &imported.stamp(), key, &intent)
            .unwrap()
            .is_none()
    );
    {
        let shared = reopened.store();
        assert_eq!(
            shared.lock().read_blob(&digest, 4096).unwrap(),
            original_bytes
        );
    }
    let after = reopened
        .export_project_at(imported.id, &imported.stamp())
        .unwrap();
    assert_eq!(after.project, imported_backup.project);
    assert_eq!(after.events, imported_backup.events);
    assert_eq!(after.payload_sha256, imported_backup.payload_sha256);
}
