use motionwright_domain::{Asset, Change, Project, RevisionStamp};
use motionwright_storage::{StorageError, Store};
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use uuid::Uuid;

type StorageResult<T> = std::result::Result<T, StorageError>;

// Baseline adapters: the existing APIs do not accept these retained
// preconditions. Do not hide the race with a separate client-side check.
mod adapter {
    use super::*;

    pub fn export(store: &mut Store, id: Uuid, expected: &RevisionStamp) -> StorageResult<Project> {
        let _ = expected;
        store.export_project(id).map(|backup| backup.project)
    }

    pub fn export_bundle(
        store: &mut Store,
        id: Uuid,
        expected: &RevisionStamp,
        destination: &Path,
    ) -> StorageResult<Project> {
        let _ = expected;
        store
            .export_project_bundle(id, destination)
            .map(|manifest| manifest.backup.project)
    }

    pub fn import(
        store: &mut Store,
        source: &Path,
        expected_fingerprint: &str,
    ) -> StorageResult<Project> {
        let _ = expected_fingerprint;
        store.import_project_bundle(source)
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
