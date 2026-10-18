//! File import admission checks content when a filename and MIME label disagree.
//! Synthetic SVG remains file data; these tests do not render or execute it.

use motionwright_domain::{Asset, Project};
use motionwright_service::StudioService;
use motionwright_storage::{
    ApplyOutcome, FileImportIntent, PROJECT_BUNDLE_FORMAT_VERSION, PortableBlob, ProjectBackup,
    ProjectBundleManifest, StorageError,
};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::ErrorKind,
    path::{Path, PathBuf},
};

use uuid::Uuid;

const ASSET_NAME: &str = "Content type boundary asset";
const MISMATCHED_MEDIA_TYPE: &str = "image/png";
const SVG_MEDIA_TYPE: &str = "image/svg+xml";
const LEADING_WHITESPACE_BYTES: usize = 16 * 1024 + 1;

struct Files {
    _directory: tempfile::TempDir,
    database: PathBuf,
    source: PathBuf,
    bytes: Vec<u8>,
}

fn files(file_name: &str, svg: &[u8], prelude_bytes: usize) -> Files {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("imports.sqlite3");
    let source = directory.path().join(file_name);
    let mut bytes = vec![b' '; prelude_bytes];
    bytes.extend_from_slice(svg);
    fs::write(&source, &bytes).unwrap();
    Files {
        _directory: directory,
        database,
        source,
        bytes,
    }
}

fn same_backup_content(actual: &ProjectBackup, expected: &ProjectBackup) -> bool {
    actual.format_version == expected.format_version
        && actual.source_storage_schema == expected.source_storage_schema
        && actual.project == expected.project
        && actual.events == expected.events
        && actual.payload_sha256 == expected.payload_sha256
}

fn staging_is_empty(root: &Path) -> bool {
    match fs::read_dir(root.join(".staging")) {
        Ok(mut entries) => entries.next().transpose().unwrap().is_none(),
        Err(error) if error.kind() == ErrorKind::NotFound => true,
        Err(error) => panic!("cannot inspect import staging: {error}"),
    }
}

fn blob_and_staging_observations(service: &StudioService, digest: &str) -> (bool, bool, bool) {
    let shared_store = service.store();
    let store = shared_store.lock();
    let missing = matches!(
        store.verified_blob_path(digest),
        Err(StorageError::BlobMissing { digest: missing }) if missing == digest
    );
    let published = store
        .blob_root()
        .join("sha256")
        .join(&digest[..2])
        .join(digest);
    (
        missing,
        !published.try_exists().unwrap(),
        staging_is_empty(store.blob_root()),
    )
}

fn assert_rejected_without_effects(files: Files, request_id: &str, media_type: &str) {
    let service = StudioService::open(&files.database).unwrap();
    let project = service.create_project("SVG content admission").unwrap();
    let expected = service.export_project(project.id).unwrap();
    let intent = FileImportIntent::Asset {
        name: ASSET_NAME.into(),
        media_type: media_type.into(),
    };
    let digest = hex::encode(Sha256::digest(&files.bytes));

    let result = service.import_asset_file(
        project.id,
        &project.stamp(),
        request_id,
        &files.source,
        ASSET_NAME.into(),
        media_type.into(),
    );

    // Observe every durable effect before reporting the rejection assertion.
    let actual = service.export_project(project.id).unwrap();
    let receipt = service
        .recover_file_import(project.id, &project.stamp(), request_id, &intent)
        .unwrap();
    let (blob_missing, published_absent, staging_empty) =
        blob_and_staging_observations(&service, &digest);
    let source_intact = fs::read(&files.source).unwrap() == files.bytes;

    drop(service);
    let reopened = StudioService::open(&files.database).unwrap();
    let reopened_backup = reopened.export_project(project.id).unwrap();
    let reopened_receipt = reopened
        .recover_file_import(project.id, &project.stamp(), request_id, &intent)
        .unwrap();
    let (reopened_blob_missing, reopened_published_absent, reopened_staging_empty) =
        blob_and_staging_observations(&reopened, &digest);
    let reopened_source_intact = fs::read(&files.source).unwrap() == files.bytes;

    let checks = [
        (
            "active or external SVG is rejected",
            matches!(&result, Err(StorageError::Domain(_))),
        ),
        (
            "project and revision unchanged",
            actual.project == expected.project,
        ),
        ("journal unchanged", actual.events == expected.events),
        (
            "backup content unchanged",
            same_backup_content(&actual, &expected),
        ),
        ("no import receipt", receipt.is_none()),
        ("CAS lookup reports the exact missing digest", blob_missing),
        ("CAS destination absent", published_absent),
        ("private staging empty", staging_empty),
        ("source bytes intact", source_intact),
        (
            "backup content unchanged after reopen",
            same_backup_content(&reopened_backup, &expected),
        ),
        ("no import receipt after reopen", reopened_receipt.is_none()),
        ("CAS digest missing after reopen", reopened_blob_missing),
        (
            "CAS destination absent after reopen",
            reopened_published_absent,
        ),
        ("private staging empty after reopen", reopened_staging_empty),
        ("source bytes intact after reopen", reopened_source_intact),
    ];
    let failed: Vec<_> = checks
        .into_iter()
        .filter_map(|(name, passed)| (!passed).then_some(name))
        .collect();
    assert!(
        failed.is_empty(),
        "SVG admission checks failed: {failed:?}; original import result: {result:?}"
    );
}

#[test]
fn mislabeled_svg_script_after_sniff_prefix_is_rejected_without_effects() {
    let files = files(
        "poster.png",
        br#"<svg xmlns="http://www.w3.org/2000/svg"><script>void 0;</script></svg>"#,
        LEADING_WHITESPACE_BYTES,
    );
    assert_rejected_without_effects(files, "reject-mislabeled-svg-script", MISMATCHED_MEDIA_TYPE);
}

#[test]
fn mislabeled_svg_external_reference_after_sniff_prefix_is_rejected_without_effects() {
    let files = files(
        "poster.png",
        br#"<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.invalid/static.png"/></svg>"#,
        LEADING_WHITESPACE_BYTES,
    );
    assert_rejected_without_effects(
        files,
        "reject-mislabeled-svg-reference",
        MISMATCHED_MEDIA_TYPE,
    );
}

#[test]
fn passive_svg_after_long_prelude_remains_an_immutable_asset() {
    let files = files(
        "mark.svg",
        br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><path d="M1 1h18v18H1z"/></svg>"#,
        LEADING_WHITESPACE_BYTES,
    );
    let service = StudioService::open(&files.database).unwrap();
    let project = service.create_project("Passive SVG admission").unwrap();
    let before = service.export_project(project.id).unwrap();
    let digest = hex::encode(Sha256::digest(&files.bytes));
    let request_id = "admit-passive-svg";
    let intent = FileImportIntent::Asset {
        name: ASSET_NAME.into(),
        media_type: SVG_MEDIA_TYPE.into(),
    };

    let outcome = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            request_id,
            &files.source,
            ASSET_NAME.into(),
            SVG_MEDIA_TYPE.into(),
        )
        .unwrap();

    assert!(!outcome.replayed);
    assert_eq!(outcome.request_id, request_id);
    assert_eq!(outcome.previous_revision, project.revision);
    assert_eq!(outcome.project.revision, project.revision + 1);
    assert_eq!(outcome.project.assets.len(), 1);
    let asset = &outcome.project.assets[0];
    assert_eq!(asset.media_type, SVG_MEDIA_TYPE);
    assert_eq!(asset.content_sha256.as_deref(), Some(digest.as_str()));
    let after = service.export_project(project.id).unwrap();
    assert_eq!(after.project, outcome.project);
    assert_eq!(after.events.len(), before.events.len() + 1);
    assert_eq!(fs::read(&files.source).unwrap(), files.bytes);
    assert!(staging_is_empty(service.store().lock().blob_root()));

    drop(service);
    let reopened = StudioService::open(&files.database).unwrap();
    let readback = reopened.export_project(project.id).unwrap();
    assert!(same_backup_content(&readback, &after));
    let recorded = reopened
        .recover_file_import(project.id, &project.stamp(), request_id, &intent)
        .unwrap()
        .expect("the admitted immutable asset retains its original receipt");
    assert_eq!(recorded.request_id, outcome.request_id);
    assert_eq!(recorded.previous_revision, outcome.previous_revision);
    assert_eq!(recorded.project, outcome.project);
    let shared_store = reopened.store();
    let store = shared_store.lock();
    assert_eq!(store.read_blob(&digest, 32_768).unwrap(), files.bytes);
    assert!(staging_is_empty(store.blob_root()));
    assert_eq!(fs::read(&files.source).unwrap(), files.bytes);
}

#[test]
fn declared_svg_namespace_script_is_rejected_without_effects() {
    let files = files(
        "active.svg",
        br#"<s:svg xmlns:s="http://www.w3.org/2000/svg"><s:script>void 0;</s:script></s:svg>"#,
        0,
    );
    assert_rejected_without_effects(files, "reject-namespaced-svg-script", SVG_MEDIA_TYPE);
}

const PASSIVE_NAMESPACED_SVG: &[u8] =
    br##"<s:svg xmlns:s="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><s:defs><s:path id="shape" d="M1 1h18v18H1z"/></s:defs><s:use href="#shape"/></s:svg>"##;

struct ExternalBundle {
    _directory: tempfile::TempDir,
    path: PathBuf,
    manifest: ProjectBundleManifest,
}

fn external_svg_bundle(svg: &[u8]) -> ExternalBundle {
    let directory = tempfile::tempdir().unwrap();
    let digest = hex::encode(Sha256::digest(svg));
    let mut project = Project::new("External SVG bundle").unwrap();
    project.assets.push(Asset {
        id: Uuid::now_v7(),
        name: "external.svg".into(),
        media_type: SVG_MEDIA_TYPE.into(),
        content_sha256: Some(digest.clone()),
        source_revision: None,
    });

    // Build valid backup metadata through the real Store exporter. The source
    // bytes are external bundle data, never admitted through an active import.
    let metadata = StudioService::open(directory.path().join("metadata.sqlite3")).unwrap();
    metadata.store().lock().create_project(&project).unwrap();
    let backup = metadata.export_project(project.id).unwrap();
    let relative_path = format!("blobs/sha256/{}/{}", &digest[..2], digest);
    let blob = PortableBlob {
        sha256: digest,
        size_bytes: svg.len() as u64,
        relative_path,
    };
    let manifest = ProjectBundleManifest {
        format_version: PROJECT_BUNDLE_FORMAT_VERSION,
        created_at: backup.exported_at.clone(),
        backup,
        blobs: vec![blob],
    };
    let path = directory.path().join("external-bundle");
    let member = path.join(&manifest.blobs[0].relative_path);
    fs::create_dir_all(member.parent().unwrap()).unwrap();
    fs::write(&member, svg).unwrap();
    fs::write(
        path.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest).unwrap(),
    )
    .unwrap();
    ExternalBundle {
        _directory: directory,
        path,
        manifest,
    }
}

fn file_tree_bytes(root: &Path) -> Vec<(PathBuf, Vec<u8>)> {
    fn collect(root: &Path, directory: &Path, files: &mut Vec<(PathBuf, Vec<u8>)>) {
        for entry in fs::read_dir(directory).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            let kind = entry.file_type().unwrap();
            if kind.is_dir() {
                collect(root, &path, files);
            } else {
                assert!(
                    kind.is_file(),
                    "fixture tree contains an unexpected file type"
                );
                files.push((
                    path.strip_prefix(root).unwrap().to_owned(),
                    fs::read(path).unwrap(),
                ));
            }
        }
    }
    let mut files = Vec::new();
    collect(root, root, &mut files);
    files.sort_by(|left, right| left.0.cmp(&right.0));
    files
}

fn same_original_receipt(actual: &Option<ApplyOutcome>, expected: &ApplyOutcome) -> bool {
    actual.as_ref().is_some_and(|actual| {
        actual.request_id == expected.request_id
            && actual.previous_revision == expected.previous_revision
            && actual.project == expected.project
    })
}

#[test]
fn active_svg_bundle_import_is_rejected_before_publishing_or_restoring_state() {
    let bundle = external_svg_bundle(
        br#"<svg xmlns="http://www.w3.org/2000/svg"><script>void 0;</script></svg>"#,
    );
    let original_bundle = file_tree_bytes(&bundle.path);
    let incoming_id = bundle.manifest.backup.project.id;
    let incoming_digest = &bundle.manifest.blobs[0].sha256;

    let target = files("existing.svg", PASSIVE_NAMESPACED_SVG, 0);
    let service = StudioService::open(&target.database).unwrap();
    let existing = service
        .create_project("Preserved destination project")
        .unwrap();
    let existing_key = "preserved-passive-import";
    let existing_intent = FileImportIntent::Asset {
        name: ASSET_NAME.into(),
        media_type: SVG_MEDIA_TYPE.into(),
    };
    let existing_import = service
        .import_asset_file(
            existing.id,
            &existing.stamp(),
            existing_key,
            &target.source,
            ASSET_NAME.into(),
            SVG_MEDIA_TYPE.into(),
        )
        .unwrap();
    let expected = service.export_project(existing.id).unwrap();
    let expected_projects = service.projects(16).unwrap();
    assert_eq!(expected_projects.len(), 1);
    assert_ne!(incoming_id, existing.id);
    let existing_digest = hex::encode(Sha256::digest(&target.bytes));

    let inspection = service.inspect_project_bundle(&bundle.path);
    // One import attempt. A failed inspection is not used to skip the direct
    // API boundary, whose own validation must also refuse these same bytes.
    let result = service.import_project_bundle(&bundle.path);

    let actual_projects = service.projects(16).unwrap();
    let actual = service.export_project(existing.id).unwrap();
    let incoming_absent = matches!(service.project(incoming_id), Err(StorageError::NotFound));
    let receipt = service
        .recover_file_import(
            existing.id,
            &existing.stamp(),
            existing_key,
            &existing_intent,
        )
        .unwrap();
    let (blob_missing, published_absent, staging_empty) =
        blob_and_staging_observations(&service, incoming_digest);
    let existing_blob = service
        .store()
        .lock()
        .read_blob(&existing_digest, 32_768)
        .unwrap();
    let bundle_unchanged = file_tree_bytes(&bundle.path) == original_bundle;
    let source_intact = fs::read(&target.source).unwrap() == target.bytes;

    drop(service);
    let reopened = StudioService::open(&target.database).unwrap();
    let reopened_projects = reopened.projects(16).unwrap();
    let reopened_backup = reopened.export_project(existing.id).unwrap();
    let reopened_incoming_absent =
        matches!(reopened.project(incoming_id), Err(StorageError::NotFound));
    let reopened_receipt = reopened
        .recover_file_import(
            existing.id,
            &existing.stamp(),
            existing_key,
            &existing_intent,
        )
        .unwrap();
    let (reopened_missing, reopened_published_absent, reopened_staging_empty) =
        blob_and_staging_observations(&reopened, incoming_digest);
    let reopened_existing_blob = reopened
        .store()
        .lock()
        .read_blob(&existing_digest, 32_768)
        .unwrap();
    let reopened_bundle_unchanged = file_tree_bytes(&bundle.path) == original_bundle;
    let reopened_source_intact = fs::read(&target.source).unwrap() == target.bytes;

    let checks = [
        ("inspection rejects active SVG content", inspection.is_err()),
        (
            "direct bundle import rejects active SVG content",
            result.is_err(),
        ),
        (
            "destination project set unchanged",
            actual_projects == expected_projects,
        ),
        ("incoming project absent", incoming_absent),
        (
            "destination project/revision/journal unchanged",
            same_backup_content(&actual, &expected),
        ),
        (
            "original import receipt preserved",
            same_original_receipt(&receipt, &existing_import),
        ),
        ("incoming CAS digest missing", blob_missing),
        ("incoming CAS destination absent", published_absent),
        ("private staging empty", staging_empty),
        (
            "existing immutable CAS bytes preserved",
            existing_blob == target.bytes,
        ),
        (
            "original bundle bytes and membership intact",
            bundle_unchanged,
        ),
        ("original local source intact", source_intact),
        (
            "destination project set unchanged after reopen",
            reopened_projects == expected_projects,
        ),
        (
            "incoming project absent after reopen",
            reopened_incoming_absent,
        ),
        (
            "destination backup unchanged after reopen",
            same_backup_content(&reopened_backup, &expected),
        ),
        (
            "original receipt preserved after reopen",
            same_original_receipt(&reopened_receipt, &existing_import),
        ),
        ("incoming CAS digest missing after reopen", reopened_missing),
        (
            "incoming CAS destination absent after reopen",
            reopened_published_absent,
        ),
        ("private staging empty after reopen", reopened_staging_empty),
        (
            "existing immutable CAS bytes preserved after reopen",
            reopened_existing_blob == target.bytes,
        ),
        (
            "original bundle intact after reopen",
            reopened_bundle_unchanged,
        ),
        (
            "original local source intact after reopen",
            reopened_source_intact,
        ),
    ];
    let failed: Vec<_> = checks
        .into_iter()
        .filter_map(|(name, passed)| (!passed).then_some(name))
        .collect();
    assert!(
        failed.is_empty(),
        "bundle SVG admission checks failed: {failed:?}; inspection: {inspection:?}; import: {result:?}"
    );
}

#[test]
fn passive_namespaced_svg_bundle_restores_exact_bytes_and_rotates_generation() {
    let original = files("mark.svg", PASSIVE_NAMESPACED_SVG, 0);
    let service = StudioService::open(&original.database).unwrap();
    let initial = service
        .create_project("Passive SVG portable source")
        .unwrap();
    let request_id = "original-passive-svg";
    let intent = FileImportIntent::Asset {
        name: ASSET_NAME.into(),
        media_type: SVG_MEDIA_TYPE.into(),
    };
    let original_import = service
        .import_asset_file(
            initial.id,
            &initial.stamp(),
            request_id,
            &original.source,
            ASSET_NAME.into(),
            SVG_MEDIA_TYPE.into(),
        )
        .unwrap();
    let source_backup = service.export_project(initial.id).unwrap();
    let digest = hex::encode(Sha256::digest(&original.bytes));
    let bundle = original._directory.path().join("portable-bundle");
    let manifest = service.export_project_bundle(initial.id, &bundle).unwrap();
    assert_eq!(manifest.backup.project, original_import.project);
    assert_eq!(manifest.blobs.len(), 1);
    assert_eq!(manifest.blobs[0].sha256, digest);
    assert_eq!(
        fs::read(bundle.join(&manifest.blobs[0].relative_path)).unwrap(),
        original.bytes
    );
    let original_bundle = file_tree_bytes(&bundle);

    let destination = tempfile::tempdir().unwrap();
    let database = destination.path().join("restored.sqlite3");
    let restored = StudioService::open(&database).unwrap();
    assert!(restored.projects(16).unwrap().is_empty());
    let inspected = restored.inspect_project_bundle(&bundle).unwrap();
    assert_eq!(inspected.project.project_id, initial.id);
    assert!(inspected.project.rotates_generation);
    let imported = restored.import_project_bundle(&bundle).unwrap();

    assert_eq!(imported.id, original_import.project.id);
    assert_ne!(imported.generation, original_import.project.generation);
    assert_eq!(imported.revision, original_import.project.revision);
    assert_eq!(imported.assets, original_import.project.assets);
    assert_eq!(restored.projects(16).unwrap(), vec![imported.clone()]);
    let restored_backup = restored.export_project(imported.id).unwrap();
    assert_eq!(restored_backup.project, imported);
    assert_eq!(restored_backup.events, source_backup.events);
    assert!(
        restored
            .recover_file_import(imported.id, &imported.stamp(), request_id, &intent)
            .unwrap()
            .is_none(),
        "bundle restoration must not recreate the source generation's import receipt"
    );
    assert_eq!(
        restored.store().lock().read_blob(&digest, 32_768).unwrap(),
        original.bytes
    );
    assert!(staging_is_empty(restored.store().lock().blob_root()));
    assert_eq!(file_tree_bytes(&bundle), original_bundle);

    drop(restored);
    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.projects(16).unwrap(), vec![imported.clone()]);
    assert!(same_backup_content(
        &reopened.export_project(imported.id).unwrap(),
        &restored_backup,
    ));
    assert!(
        reopened
            .recover_file_import(imported.id, &imported.stamp(), request_id, &intent)
            .unwrap()
            .is_none()
    );
    assert_eq!(
        reopened.store().lock().read_blob(&digest, 32_768).unwrap(),
        original.bytes
    );
    assert!(staging_is_empty(reopened.store().lock().blob_root()));
    assert_eq!(file_tree_bytes(&bundle), original_bundle);
    assert_eq!(fs::read(&original.source).unwrap(), original.bytes);
    assert!(same_backup_content(
        &service.export_project(initial.id).unwrap(),
        &source_backup,
    ));
    let source_receipt = service
        .recover_file_import(initial.id, &initial.stamp(), request_id, &intent)
        .unwrap();
    assert!(same_original_receipt(&source_receipt, &original_import));
}
