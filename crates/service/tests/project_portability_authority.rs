use motionwright_domain::{Asset, Change, Project, RevisionStamp};
use motionwright_storage::{StorageError, Store};
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use uuid::Uuid;

type StorageResult<T> = std::result::Result<T, StorageError>;

// Each adapter forwards the retained precondition to the actual backend.
// The regression bodies below are unchanged from the failing baseline.
mod adapter {
    use super::*;
    use motionwright_storage::BundleFingerprint;

    pub fn export(store: &mut Store, id: Uuid, expected: &RevisionStamp) -> StorageResult<Project> {
        store
            .export_project_at(id, expected)
            .map(|backup| backup.project)
    }

    pub fn export_bundle(
        store: &mut Store,
        id: Uuid,
        expected: &RevisionStamp,
        destination: &Path,
    ) -> StorageResult<Project> {
        store
            .export_project_bundle_at(id, expected, destination)
            .map(|manifest| manifest.backup.project)
    }

    pub fn import(
        store: &mut Store,
        source: &Path,
        expected_fingerprint: &str,
    ) -> StorageResult<Project> {
        let fingerprint = BundleFingerprint::from_sha256(expected_fingerprint)?;
        store.import_project_bundle_inspected(source, &fingerprint)
    }
}

fn fixture_store(root: &Path, name: &str) -> Store {
    let data = root.join(name);
    fs::create_dir_all(&data).unwrap();
    Store::open(data.join("state.sqlite3")).unwrap()
}

// Oracle data only: the old inspector does not return this fingerprint.
// This fixture is unchanged between its inspection and this controlled read.
fn fixture_fingerprint(bundle: &Path) -> String {
    let bytes = fs::read(bundle.join("manifest.json")).unwrap();
    hex::encode(
        Sha256::new()
            .chain_update(b"motionwright.bundle-inspection.v1\0")
            .chain_update(bytes)
            .finalize(),
    )
}

fn add_asset(
    store: &mut Store,
    project: &Project,
    source: &Path,
    request: &str,
) -> (Project, String) {
    let blob = store.ingest_blob_file(source).unwrap();
    let outcome = store
        .apply(
            project.id,
            &project.stamp(),
            request,
            &Change::AddAsset {
                asset: Asset {
                    id: Uuid::now_v7(),
                    name: format!("{request}.bin"),
                    media_type: "application/octet-stream".into(),
                    content_sha256: Some(blob.sha256.clone()),
                    source_revision: Some("local-import".into()),
                },
            },
        )
        .unwrap();
    (outcome.project, blob.sha256)
}

#[test]
fn export_rejects_peer_revision_committed_after_precheck() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("state.sqlite3");
    let mut requester = Store::open(&database).unwrap();
    let created = requester
        .create_named_project("Inspected snapshot")
        .unwrap();
    let prechecked = requester.load_project(created.id).unwrap();
    let expected = prechecked.stamp();
    let matching = adapter::export(&mut requester, created.id, &expected)
        .expect("CONTROL: current stamp must export");
    assert_eq!(matching, prechecked);
    let mut peer = Store::open(&database).unwrap();
    let advanced = peer
        .apply(
            created.id,
            &expected,
            "peer-advance",
            &Change::RenameProject {
                title: "Peer changed the project".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(advanced.generation, expected.generation);
    assert_eq!(advanced.revision, expected.revision + 1);
    let attempted = adapter::export(&mut requester, created.id, &expected);
    if let Ok(exported) = &attempted {
        assert_eq!(exported, &advanced);
        eprintln!(
            "L4_OBSERVED_LATER_SNAPSHOT inspected_revision={} exported_revision={}",
            expected.revision, exported.revision,
        );
    }
    assert!(
        matches!(
            &attempted, Err(StorageError::Conflict { expected: rejected, actual })
            if *rejected == expected.revision && *actual == advanced.revision
        ),
        "stale export must reject the later snapshot: {attempted:?}"
    );
}

#[test]
fn bundle_export_rejects_stale_snapshot_before_creating_destination() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("source/state.sqlite3");
    let mut requester = Store::open(&database).unwrap();
    let original = requester.create_named_project("Inspected bundle").unwrap();
    let expected = requester.load_project(original.id).unwrap().stamp();
    let control = directory.path().join("current-control");
    assert_eq!(
        adapter::export_bundle(&mut requester, original.id, &expected, &control).unwrap(),
        original,
    );
    assert!(control.join("manifest.json").is_file());
    let mut peer = Store::open(&database).unwrap();
    let advanced = peer
        .apply(
            original.id,
            &expected,
            "peer-bundle-advance",
            &Change::RenameProject {
                title: "Later bundle content".into(),
            },
        )
        .unwrap()
        .project;
    let destination = directory.path().join("stale-export");
    let attempted = adapter::export_bundle(&mut requester, original.id, &expected, &destination);
    if let Ok(exported) = &attempted {
        assert_eq!(exported, &advanced);
        assert!(destination.join("manifest.json").is_file());
        eprintln!(
            "L4_OBSERVED_LATER_BUNDLE inspected_revision={} exported_revision={}",
            expected.revision, exported.revision,
        );
    }
    assert!(
        matches!(
            &attempted, Err(StorageError::Conflict { expected: rejected, actual })
            if *rejected == expected.revision && *actual == advanced.revision
        ),
        "stale bundle export must reject: {attempted:?}"
    );
    assert!(
        !destination.exists(),
        "no destination may be published on rejection"
    );
}

#[test]
fn import_rejects_complete_valid_bundle_replaced_after_inspection() {
    let directory = tempfile::tempdir().unwrap();
    let mut source = fixture_store(directory.path(), "source");
    let mut target = fixture_store(directory.path(), "target");
    let mut control = fixture_store(directory.path(), "control");
    let active = directory.path().join("active");
    let prepared_b = directory.path().join("prepared-b");
    let retired_a = directory.path().join("retired-a");
    let input_a = directory.path().join("input-a.bin");
    let input_b = directory.path().join("input-b.bin");
    fs::write(&input_a, b"fixture-content-A").unwrap();
    fs::write(&input_b, b"fixture-content-B").unwrap();
    let initial = source.create_named_project("Bundle A").unwrap();
    let (project_a, digest_a) = add_asset(&mut source, &initial, &input_a, "asset-a");
    source.export_project_bundle(project_a.id, &active).unwrap();
    let renamed = source
        .apply(
            project_a.id,
            &project_a.stamp(),
            "rename-to-b",
            &Change::RenameProject {
                title: "Bundle B".into(),
            },
        )
        .unwrap()
        .project;
    let (project_b, digest_b) = add_asset(&mut source, &renamed, &input_b, "asset-b");
    source
        .export_project_bundle(project_b.id, &prepared_b)
        .unwrap();
    let inspected_a = target.inspect_project_bundle(&active).unwrap();
    assert_eq!(inspected_a.project.title, "Bundle A");
    assert_eq!(inspected_a.project.revision, project_a.revision);
    let fingerprint_a = fixture_fingerprint(&active);

    // B must pass both actual old APIs before it can reproduce substitution.
    let inspected_b = control
        .inspect_project_bundle(&prepared_b)
        .expect("CONTROL: complete B passes legacy inspector");
    assert_eq!(inspected_b.project.title, "Bundle B");
    assert!(inspected_b.project.revision > inspected_a.project.revision);
    let imported_b = control
        .import_project_bundle(&prepared_b)
        .expect("CONTROL: complete B passes legacy importer");
    assert!(
        imported_b
            .assets
            .iter()
            .any(|asset| asset.content_sha256.as_deref() == Some(digest_b.as_str()))
    );
    assert_eq!(
        control.read_blob(&digest_b, 1024).unwrap(),
        b"fixture-content-B"
    );
    assert!(matches!(
        target.export_project(project_a.id),
        Err(StorageError::NotFound)
    ));

    fs::rename(&active, &retired_a).unwrap();
    fs::rename(&prepared_b, &active).unwrap();
    let active_b = target
        .inspect_project_bundle(&active)
        .expect("CONTROL: B remains valid after moving to the inspected path");
    assert_eq!(active_b.project.title, "Bundle B");
    let fingerprint_b = fixture_fingerprint(&active);
    assert_ne!(fingerprint_a, fingerprint_b);
    let attempted = adapter::import(&mut target, &active, &fingerprint_a);
    if let Ok(imported) = &attempted {
        assert!(
            imported
                .assets
                .iter()
                .any(|asset| asset.content_sha256.as_deref() == Some(digest_b.as_str()))
        );
        assert_eq!(
            target.read_blob(&digest_b, 1024).unwrap(),
            b"fixture-content-B"
        );
        eprintln!(
            "L5_OBSERVED_ACCEPTED_REPLACEMENT inspected_revision={} replacement_revision={} inspected_fingerprint={} replacement_fingerprint={}",
            inspected_a.project.revision,
            inspected_b.project.revision,
            fingerprint_a,
            fingerprint_b,
        );
    }
    assert!(
        matches!(&attempted, Err(StorageError::InvalidBackup(_))),
        "a different inspected manifest must be rejected: {attempted:?}"
    );
    assert!(matches!(
        target.export_project(project_a.id),
        Err(StorageError::NotFound)
    ));
    for digest in [&digest_a, &digest_b] {
        assert!(
            target.read_blob(digest, 1024).is_err(),
            "rejected bundle must not publish blob {digest}"
        );
    }
}

use motionwright_service::StudioService;
use motionwright_storage::{BundleFingerprint, FileImportIntent};

fn service_fixture_bundle(
    root: &Path,
    source_name: &str,
    bundle_name: &str,
) -> (std::path::PathBuf, Project, Vec<(String, Vec<u8>)>) {
    let mut source = fixture_store(root, source_name);
    let mut project = source
        .create_named_project("Portable service fixture")
        .unwrap();
    let mut originals = Vec::new();
    for (index, bytes) in [
        b"first immutable source".as_slice(),
        b"second immutable source",
    ]
    .into_iter()
    .enumerate()
    {
        let input = root.join(format!("{source_name}-{index}.bin"));
        fs::write(&input, bytes).unwrap();
        let (next, digest) = add_asset(&mut source, &project, &input, &format!("source-{index}"));
        project = next;
        originals.push((digest, bytes.to_vec()));
        fs::remove_file(input).unwrap();
    }
    let bundle = root.join(bundle_name);
    source
        .export_project_bundle_at(project.id, &project.stamp(), &bundle)
        .unwrap();
    (bundle, project, originals)
}

#[test]
fn service_inspected_bundle_transfers_real_cas_and_reopens_without_operational_receipts() {
    let directory = tempfile::tempdir().unwrap();
    let (bundle, original, sources) =
        service_fixture_bundle(directory.path(), "source", "portable");
    let target_path = directory.path().join("target/state.sqlite3");
    let target = StudioService::open(&target_path).unwrap();
    let inspected = target
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    assert_eq!(inspected.plan.project.project_id, original.id);
    assert_eq!(inspected.plan.project.revision, original.revision);
    assert_eq!(inspected.plan.blob_count, 2);
    assert_eq!(inspected.fingerprint.as_str(), fixture_fingerprint(&bundle));
    assert_eq!(
        serde_json::from_str::<BundleFingerprint>(
            &serde_json::to_string(&inspected.fingerprint).unwrap()
        )
        .unwrap(),
        inspected.fingerprint,
    );
    {
        let shared = target.store();
        let store = shared.lock();
        for (digest, _) in &sources {
            assert!(
                store.read_blob(digest, 4096).is_err(),
                "target CAS must start empty"
            );
        }
    }
    let imported = target
        .import_project_bundle_inspected(&bundle, &inspected.fingerprint)
        .unwrap();
    assert_eq!(imported.id, original.id);
    assert_ne!(imported.generation, original.generation);
    assert_eq!(imported.revision, original.revision);
    assert_eq!(imported.assets, original.assets);
    assert_eq!(imported.scenes, original.scenes);
    assert_eq!(imported.branches, original.branches);
    assert_eq!(
        target
            .export_project_at(imported.id, &imported.stamp())
            .unwrap()
            .events
            .len(),
        2
    );
    assert!(matches!(
        target.import_project_bundle_inspected(&bundle, &inspected.fingerprint),
        Err(StorageError::ProjectExists),
    ));
    assert_eq!(target.projects(10).unwrap().len(), 1);
    drop(target);
    let reopened = StudioService::open(&target_path).unwrap();
    assert_eq!(reopened.project(imported.id).unwrap(), imported);
    let intent = FileImportIntent::Asset {
        name: "source-0.bin".into(),
        media_type: "application/octet-stream".into(),
    };
    assert!(
        reopened
            .recover_file_import(imported.id, &imported.stamp(), "source-0", &intent)
            .unwrap()
            .is_none()
    );
    let shared = reopened.store();
    let store = shared.lock();
    for (digest, bytes) in sources {
        assert_eq!(store.read_blob(&digest, 4096).unwrap(), bytes);
    }
}

#[test]
fn service_export_preconditions_reject_foreign_scope_before_missing_source_io() {
    let directory = tempfile::tempdir().unwrap();
    let (bundle, original, sources) =
        service_fixture_bundle(directory.path(), "source", "portable");
    let database = directory.path().join("source/state.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let correct = service
        .export_project_at(original.id, &original.stamp())
        .unwrap();
    assert_eq!(correct.project, original);
    let source_store = Store::open(&database).unwrap();
    let digest = &sources[0].0;
    fs::remove_file(
        source_store
            .blob_root()
            .join("sha256")
            .join(&digest[..2])
            .join(digest),
    )
    .unwrap();

    let mut foreign_resource = original.stamp();
    foreign_resource.resource = Project::new("Other project").unwrap().resource_key();
    let mut foreign_generation = original.stamp();
    foreign_generation.generation = Uuid::now_v7();
    let mut stale_revision = original.stamp();
    stale_revision.revision -= 1;
    for (index, expected) in [foreign_resource, foreign_generation, stale_revision]
        .iter()
        .enumerate()
    {
        let destination = directory.path().join(format!("rejected-{index}"));
        let raw = service.export_project_at(original.id, expected);
        let packaged = service.export_project_bundle_at(original.id, expected, &destination);
        if index < 2 {
            assert!(matches!(raw, Err(StorageError::GenerationConflict)));
            assert!(matches!(packaged, Err(StorageError::GenerationConflict)));
        } else {
            assert!(matches!(raw, Err(StorageError::Conflict { .. })));
            assert!(matches!(packaged, Err(StorageError::Conflict { .. })));
        }
        assert!(!destination.exists());
    }
    let missing_destination = directory.path().join("missing-project");
    assert!(matches!(
        service.export_project_bundle_at(Uuid::now_v7(), &original.stamp(), &missing_destination),
        Err(StorageError::NotFound),
    ));
    assert!(!missing_destination.exists());
    assert_eq!(service.project(original.id).unwrap(), original);
    assert!(bundle.join("manifest.json").is_file());
}

#[test]
fn service_exact_inspection_rejects_even_semantically_equal_manifest_reencoding() {
    let directory = tempfile::tempdir().unwrap();
    let (bundle, original, _) = service_fixture_bundle(directory.path(), "source", "portable");
    let target = StudioService::open(directory.path().join("target/state.sqlite3")).unwrap();
    let inspected = target
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    let manifest_path = bundle.join("manifest.json");
    let manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(&manifest_path).unwrap()).unwrap();
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    let changed = target
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    assert_eq!(changed.plan, inspected.plan);
    assert_ne!(changed.fingerprint, inspected.fingerprint);
    assert!(matches!(
        target.import_project_bundle_inspected(&bundle, &inspected.fingerprint),
        Err(StorageError::InvalidBackup(_)),
    ));
    assert!(matches!(
        target.project(original.id),
        Err(StorageError::NotFound)
    ));
    assert!(target.projects(10).unwrap().is_empty());
    // Observing a different fingerprint here does not retry the import.
}

#[test]
fn service_rejects_changed_blob_with_unchanged_manifest_and_publishes_no_member() {
    let directory = tempfile::tempdir().unwrap();
    let (bundle, original, sources) =
        service_fixture_bundle(directory.path(), "source", "portable");
    let target = StudioService::open(directory.path().join("target/state.sqlite3")).unwrap();
    let inspected = target
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    let manifest: motionwright_storage::ProjectBundleManifest =
        serde_json::from_slice(&fs::read(bundle.join("manifest.json")).unwrap()).unwrap();
    let member = manifest.blobs.last().unwrap();
    let changed = vec![b'X'; member.size_bytes as usize];
    let changed_digest = hex::encode(Sha256::digest(&changed));
    assert_ne!(changed_digest, member.sha256);
    fs::write(bundle.join(&member.relative_path), changed).unwrap();
    assert_eq!(fixture_fingerprint(&bundle), inspected.fingerprint.as_str());
    assert!(matches!(
        target.import_project_bundle_inspected(&bundle, &inspected.fingerprint),
        Err(StorageError::InvalidBackup(_)),
    ));
    assert!(matches!(
        target.project(original.id),
        Err(StorageError::NotFound)
    ));
    let shared = target.store();
    let store = shared.lock();
    for (digest, _) in &sources {
        assert!(store.read_blob(digest, 4096).is_err());
    }
    assert!(store.read_blob(&changed_digest, 4096).is_err());
    let staging = store.blob_root().join(".staging");
    assert!(!staging.exists() || fs::read_dir(staging).unwrap().next().is_none());
}
