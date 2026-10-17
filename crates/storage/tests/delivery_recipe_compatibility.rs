//! Compatibility oracles backed by a real pre-delivery-recipe exporter.
//! This fixture is an exported backup, not an old SQLite file or installed GUI capture.
use motionwright_domain::Project;
use motionwright_storage::{ProjectBackup, StorageError, Store};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};

const FIXTURE: &[u8] = include_bytes!("fixtures/pre_delivery_recipe_backup.json");
const PROVENANCE: &[u8] = include_bytes!("fixtures/pre_delivery_recipe_backup.provenance.json");
const FIXTURE_SHA: &str = "1f959cbe8cf5f5ee7dbed8c2596087ea710f6e064be0e35b47dbdcc5343b0a57";
const PAYLOAD_SHA: &str = "db077decfba681e1534b85280b4997229fb0f0c68e1ceeae3bc8bca395143d2a";
const ORIGINAL_SHA: &str = "d1ccea4e8d77015dafa44aeedc2f1e5c9ff416c04743a7e7137976f6788a9656";
const ORIGINAL: &[u8] = b"Synthetic original source for pre-recipe storage compatibility.\n";
const EXPORTER_BASE: &str = "2178e31c55f1d041e39fea23227536f4886c4873";
const GENERATOR: &str = "bc1daa6cbff28936d392af41ac7d337a1e6b796d";

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn historical_backup() -> ProjectBackup {
    serde_json::from_slice(FIXTURE).unwrap()
}
fn payload_digest(backup: &ProjectBackup) -> String {
    sha256(
        &serde_json::to_vec(&(
            backup.format_version,
            backup.source_storage_schema,
            &backup.project,
            &backup.events,
        ))
        .unwrap(),
    )
}
fn assert_same_payload(before: &ProjectBackup, after: &ProjectBackup) {
    // exported_at belongs to this export invocation, not the persisted payload.
    assert_eq!(after.format_version, before.format_version);
    assert_eq!(after.source_storage_schema, before.source_storage_schema);
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    assert_eq!(after.payload_sha256, before.payload_sha256);
}
fn assert_import_only_refreshes_authority(source: &Project, imported: &Project) {
    assert_ne!(imported.generation, source.generation);
    // Compare all serialized fields except the two explicitly renewed by import.
    // Neither the Project nor its digest is modified to manufacture a match.
    let mut source_fields = serde_json::to_value(source).unwrap();
    let mut imported_fields = serde_json::to_value(imported).unwrap();
    for field in ["generation", "updated_at"] {
        assert!(
            source_fields
                .as_object_mut()
                .unwrap()
                .remove(field)
                .is_some()
        );
        assert!(
            imported_fields
                .as_object_mut()
                .unwrap()
                .remove(field)
                .is_some()
        );
    }
    assert_eq!(imported_fields, source_fields);
}
fn assert_original_missing(store: &Store) {
    assert!(matches!(
        store.read_blob(ORIGINAL_SHA, 4096),
        Err(StorageError::BlobMissing { .. })
    ));
}
fn materialize_historical_source(directory: &Path) -> (Store, ProjectBackup, Project) {
    let backup = historical_backup();
    let mut store = Store::open(directory.join("source/state.sqlite3")).unwrap();
    let imported = store.import_project(&backup).unwrap();
    assert_import_only_refreshes_authority(&backup.project, &imported);
    assert_original_missing(&store);
    let source_path = directory.join("original-source.bin");
    fs::write(&source_path, ORIGINAL).unwrap();
    let blob = store.ingest_blob_file(&source_path).unwrap();
    assert_eq!(blob.sha256, ORIGINAL_SHA);
    assert_eq!(blob.size_bytes, ORIGINAL.len() as u64);
    assert_eq!(store.read_blob(ORIGINAL_SHA, 4096).unwrap(), ORIGINAL);
    (store, backup, imported)
}
fn assert_no_import_effects(store: &mut Store, control: &ProjectBackup, incoming: &ProjectBackup) {
    assert_eq!(store.list_projects(10).unwrap().len(), 1);
    assert_eq!(
        store.load_project(control.project.id).unwrap(),
        control.project
    );
    assert!(matches!(
        store.load_project(incoming.project.id),
        Err(StorageError::NotFound)
    ));
    assert_same_payload(control, &store.export_project(control.project.id).unwrap());
    assert_original_missing(store);
}

#[test]
fn authentic_pre_recipe_backup_retains_its_serialization_contract() {
    assert_eq!(FIXTURE.len(), 5173);
    assert_eq!(sha256(FIXTURE), FIXTURE_SHA);
    assert_eq!(sha256(ORIGINAL), ORIGINAL_SHA);
    let provenance: Value = serde_json::from_slice(PROVENANCE).unwrap();
    assert_eq!(
        provenance["schema"],
        "motionwright.pre-recipe-backup-provenance/1"
    );
    assert_eq!(provenance["exporter_base_sha"], EXPORTER_BASE);
    assert_eq!(provenance["generator_commit"], GENERATOR);
    assert_eq!(
        provenance["capture_verification"]["verified_actual_checkout"],
        GENERATOR
    );
    assert_eq!(provenance["fixture_file_sha256"], FIXTURE_SHA);
    assert_eq!(provenance["fixture_file_size_bytes"].as_u64(), Some(5173));
    assert_eq!(provenance["payload_sha256"], PAYLOAD_SHA);
    assert_eq!(provenance["original_source_sha256"], ORIGINAL_SHA);
    assert_eq!(
        provenance["original_source_utf8"].as_str(),
        Some(std::str::from_utf8(ORIGINAL).unwrap())
    );
    assert_eq!(provenance["project_revision"].as_u64(), Some(4));
    assert_eq!(provenance["event_count"].as_u64(), Some(4));
    let backup = historical_backup();
    assert_eq!(backup.format_version, 1);
    assert_eq!(backup.source_storage_schema, 2);
    assert_eq!(backup.project.revision, 4);
    assert_eq!(backup.events.len(), 4);
    assert_eq!(backup.payload_sha256, PAYLOAD_SHA);
    assert_eq!(payload_digest(&backup), PAYLOAD_SHA);
    let directory = tempfile::tempdir().unwrap();
    let store = Store::open(directory.path().join("inspect.sqlite3")).unwrap();
    // Real validation rejects a serialization change that adds an empty field
    // to a historical payload, even if new types can deserialize the fixture.
    store.inspect_import(&backup).unwrap();
    assert!(store.list_projects(10).unwrap().is_empty());
    assert_original_missing(&store);
}

#[test]
fn historical_backup_survives_real_bundle_transfer_and_reopening() {
    let directory = tempfile::tempdir().unwrap();
    let (mut source, historical, first_import) = materialize_historical_source(directory.path());
    let bundle_path = directory.path().join("portable-project");
    let manifest = source
        .export_project_bundle(first_import.id, &bundle_path)
        .unwrap();
    assert_eq!(manifest.backup.project, first_import);
    assert_eq!(manifest.backup.events, historical.events);
    assert_eq!(manifest.blobs.len(), 1);
    assert_eq!(manifest.blobs[0].sha256, ORIGINAL_SHA);
    assert_eq!(manifest.blobs[0].size_bytes, ORIGINAL.len() as u64);
    drop(source);
    let mut source_reopened = Store::open(directory.path().join("source/state.sqlite3")).unwrap();
    assert_same_payload(
        &manifest.backup,
        &source_reopened.export_project(first_import.id).unwrap(),
    );
    assert_eq!(
        source_reopened.read_blob(ORIGINAL_SHA, 4096).unwrap(),
        ORIGINAL
    );
    drop(source_reopened);
    let path = directory.path().join("destination/state.sqlite3");
    let mut destination = Store::open(&path).unwrap();
    assert_original_missing(&destination);
    let plan = destination.inspect_project_bundle(&bundle_path).unwrap();
    assert!(plan.project.rotates_generation);
    assert_eq!(plan.blob_count, 1);
    let second_import = destination.import_project_bundle(&bundle_path).unwrap();
    assert_import_only_refreshes_authority(&first_import, &second_import);
    assert_eq!(destination.read_blob(ORIGINAL_SHA, 4096).unwrap(), ORIGINAL);
    let after_import = destination.export_project(second_import.id).unwrap();
    assert_eq!(after_import.events, historical.events);
    assert_ne!(after_import.payload_sha256, historical.payload_sha256);
    drop(destination);
    let mut reopened = Store::open(&path).unwrap();
    assert_eq!(reopened.list_projects(10).unwrap().len(), 1);
    assert_eq!(
        reopened.load_project(second_import.id).unwrap(),
        second_import
    );
    assert_eq!(reopened.read_blob(ORIGINAL_SHA, 4096).unwrap(), ORIGINAL);
    assert_same_payload(
        &after_import,
        &reopened.export_project(second_import.id).unwrap(),
    );
}

#[test]
fn unsupported_or_tampered_backups_leave_destination_unchanged() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("destination/state.sqlite3");
    let mut destination = Store::open(&path).unwrap();
    let control = destination
        .create_named_project("Preserved destination")
        .unwrap();
    let control_before = destination.export_project(control.id).unwrap();
    let historical = historical_backup();
    let mut future = historical.clone();
    future.format_version = u32::MAX;
    future.payload_sha256 = payload_digest(&future);
    let mut tampered = historical.clone();
    tampered.project.title = "Tampered legacy import".into();
    assert_ne!(payload_digest(&tampered), tampered.payload_sha256);
    for rejected in [&future, &tampered] {
        assert!(matches!(
            destination.inspect_import(rejected),
            Err(StorageError::InvalidBackup(_))
        ));
        assert_no_import_effects(&mut destination, &control_before, &historical);
        assert!(matches!(
            destination.import_project(rejected),
            Err(StorageError::InvalidBackup(_))
        ));
        assert_no_import_effects(&mut destination, &control_before, &historical);
    }
    drop(destination);
    assert_no_import_effects(
        &mut Store::open(&path).unwrap(),
        &control_before,
        &historical,
    );
}

#[test]
fn unknown_nested_fields_and_changed_source_bytes_are_rejected_before_bundle_ingestion() {
    let directory = tempfile::tempdir().unwrap();
    let (mut source, historical, imported) = materialize_historical_source(directory.path());
    let unknown_path = directory.path().join("unknown-field-bundle");
    let corrupt_path = directory.path().join("changed-bytes-bundle");
    source
        .export_project_bundle(imported.id, &unknown_path)
        .unwrap();
    let manifest = source
        .export_project_bundle(imported.id, &corrupt_path)
        .unwrap();
    let destination_path = directory.path().join("destination/state.sqlite3");
    let mut destination = Store::open(&destination_path).unwrap();
    destination.inspect_project_bundle(&unknown_path).unwrap();
    destination.inspect_project_bundle(&corrupt_path).unwrap();
    let control = destination
        .create_named_project("Untouched destination")
        .unwrap();
    let control_before = destination.export_project(control.id).unwrap();

    let manifest_path = unknown_path.join("manifest.json");
    let mut unknown: Value = serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    unknown["backup"]["project"]["production_design"]
        .as_object_mut()
        .expect("historical production design exists")
        .insert(
            "unrecognized_future_contract".into(),
            serde_json::json!({"version": 99}),
        );
    fs::write(&manifest_path, serde_json::to_vec(&unknown).unwrap()).unwrap();

    let blob = manifest
        .blobs
        .iter()
        .find(|blob| blob.sha256 == ORIGINAL_SHA)
        .unwrap();
    let blob_path = corrupt_path.join(&blob.relative_path);
    let mut changed = fs::read(&blob_path).unwrap();
    assert_eq!(changed, ORIGINAL);
    changed[0] ^= 1;
    assert_eq!(changed.len() as u64, blob.size_bytes);
    assert_ne!(sha256(&changed), blob.sha256);
    fs::write(blob_path, changed).unwrap();
    for bundle in [&unknown_path, &corrupt_path] {
        assert!(destination.inspect_project_bundle(bundle).is_err());
        assert_no_import_effects(&mut destination, &control_before, &historical);
        assert!(destination.import_project_bundle(bundle).is_err());
        assert_no_import_effects(&mut destination, &control_before, &historical);
    }
    drop(destination);
    assert_no_import_effects(
        &mut Store::open(&destination_path).unwrap(),
        &control_before,
        &historical,
    );
}
