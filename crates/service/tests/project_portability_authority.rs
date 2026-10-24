use motionwright_domain::{Asset, Change, Project, RevisionStamp};
use motionwright_storage::{
    ApplyOutcome, ProjectBackup, ProjectBundleManifest, StorageError, Store,
};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    time::{Duration, Instant},
};
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

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct OperationalBackupFixture {
    bundle: std::path::PathBuf,
    project: Project,
    sources: Vec<(String, Vec<u8>)>,
    main_branch: Uuid,
    alternate_branch: Uuid,
    alternate_scenes: Vec<motionwright_domain::Scene>,
    events: Vec<motionwright_storage::ProjectEvent>,
    backup: ProjectBackup,
    manifest: ProjectBundleManifest,
    manifest_bytes: Vec<u8>,
    fingerprint: BundleFingerprint,
    committed_base: RevisionStamp,
    committed_change: Change,
    committed_outcome: ApplyOutcome,
}

fn apply_portable_change(
    service: &StudioService,
    project: &Project,
    request: &str,
    change: Change,
) -> Project {
    let outcome = service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap();
    assert!(!outcome.replayed);
    assert_eq!(outcome.previous_revision, project.revision);
    assert_eq!(outcome.project.id, project.id);
    assert_eq!(outcome.project.generation, project.generation);
    assert_eq!(outcome.project.revision, project.revision + 1);
    outcome.project
}

fn operational_backup_fixture(root: &Path) -> OperationalBackupFixture {
    let (_, initial, sources) = service_fixture_bundle(root, "source", "asset-seed");
    let source_path = root.join("source/state.sqlite3");
    let source = StudioService::open(&source_path).unwrap();
    let mut project = source.project(initial.id).unwrap();
    assert_eq!(project, initial);
    project = apply_portable_change(
        &source,
        &project,
        "add-opening",
        Change::AddScene {
            name: "Opening".into(),
            objective: "Main opening".into(),
            duration_seconds: 2,
        },
    );
    project = apply_portable_change(
        &source,
        &project,
        "add-closing",
        Change::AddScene {
            name: "Closing".into(),
            objective: "Main closing".into(),
            duration_seconds: 3,
        },
    );
    let main_branch = project.active_branch;
    let main_scenes = project.scenes.clone();
    let opening = main_scenes[0].id;
    let closing = main_scenes[1].id;
    project = apply_portable_change(
        &source,
        &project,
        "create-alternate",
        Change::CreateBranch {
            name: "Alternate take".into(),
        },
    );
    let alternate_branch = project
        .branches
        .iter()
        .find(|branch| branch.name == "Alternate take")
        .unwrap()
        .id;
    assert_ne!(alternate_branch, main_branch);
    project = apply_portable_change(
        &source,
        &project,
        "checkout-alternate",
        Change::CheckoutBranch {
            branch_id: alternate_branch,
        },
    );
    project = apply_portable_change(
        &source,
        &project,
        "alternate-narration",
        Change::UpdateSceneObjective {
            scene_id: closing,
            objective: "Alternate closing before opening".into(),
        },
    );
    project = apply_portable_change(
        &source,
        &project,
        "alternate-order",
        Change::MoveScene {
            scene_id: closing,
            to_index: 0,
        },
    );
    assert_eq!(project.active_branch, alternate_branch);
    assert_eq!(project.scenes[0].id, closing);
    assert_eq!(project.scenes[1].id, opening);
    assert_eq!(
        project.scenes[0].objective,
        "Alternate closing before opening"
    );
    let alternate_scenes = project.scenes.clone();
    let committed_base = project.stamp();
    let committed_change = Change::CheckoutBranch {
        branch_id: main_branch,
    };
    let committed_outcome = source
        .apply(
            project.id,
            &committed_base,
            "return-to-main",
            &committed_change,
        )
        .unwrap();
    assert!(!committed_outcome.replayed);
    assert_eq!(committed_outcome.previous_revision, project.revision);
    assert_eq!(committed_outcome.project.id, project.id);
    assert_eq!(committed_outcome.project.generation, project.generation);
    assert_eq!(committed_outcome.project.revision, project.revision + 1);
    assert_eq!(committed_outcome.request_id, "return-to-main");
    project = committed_outcome.project.clone();
    assert_eq!(project.active_branch, main_branch);
    assert_eq!(project.scenes, main_scenes);
    assert_eq!(project.assets, initial.assets);
    assert_eq!(project.branches.len(), 2);
    assert_eq!(project.revision, 9);

    // The export must use persisted state, including the inactive workspace.
    drop(source);
    let reopened = StudioService::open(&source_path).unwrap();
    let persisted = reopened.project(project.id).unwrap();
    assert_eq!(persisted, project);
    let backup = reopened
        .export_project_at(persisted.id, &persisted.stamp())
        .unwrap();
    assert_eq!(backup.project, persisted);
    assert_eq!(backup.events.len() as u64, persisted.revision);
    let bundle = root.join("portable");
    let manifest = reopened
        .export_project_bundle_at(persisted.id, &persisted.stamp(), &bundle)
        .unwrap();
    // Independent exports have their own exported_at, but the same snapshot.
    assert_eq!(manifest.backup.project, backup.project);
    assert_eq!(manifest.backup.events, backup.events);
    assert_eq!(manifest.backup.format_version, backup.format_version);
    assert_eq!(
        manifest.backup.source_storage_schema,
        backup.source_storage_schema
    );
    assert_eq!(manifest.backup.payload_sha256, backup.payload_sha256);
    // Inspection checks destination admission, so it needs an empty store.
    let inspector = StudioService::open(root.join("export-inspection/state.sqlite3")).unwrap();
    let inspected = inspector
        .inspect_project_bundle_with_fingerprint(&bundle)
        .unwrap();
    assert_eq!(inspected.plan.blob_count, sources.len());
    let fingerprint = inspected.fingerprint;
    let manifest_bytes = fs::read(bundle.join("manifest.json")).unwrap();
    assert_eq!(
        serde_json::from_slice::<ProjectBundleManifest>(&manifest_bytes).unwrap(),
        manifest
    );
    drop(inspector);
    let source_files = {
        let shared = reopened.store();
        let store = shared.lock();
        sources
            .iter()
            .map(|(digest, bytes)| {
                assert_eq!(store.read_blob(digest, 4096).unwrap(), *bytes);
                store.verified_blob_path(digest).unwrap()
            })
            .collect::<Vec<_>>()
    };
    drop(reopened);

    // Only the two verified CAS files of this isolated fixture are removed.
    // The original input files were already removed by service_fixture_bundle.
    let owned_source = root.join("source").canonicalize().unwrap();
    for path in source_files {
        assert!(path.canonicalize().unwrap().starts_with(&owned_source));
        fs::remove_file(&path).unwrap();
        assert!(!path.exists());
    }
    for index in 0..sources.len() {
        assert!(!root.join(format!("source-{index}.bin")).exists());
    }
    OperationalBackupFixture {
        bundle,
        project: persisted,
        sources,
        main_branch,
        alternate_branch,
        alternate_scenes,
        events: backup.events.clone(),
        backup,
        manifest,
        manifest_bytes,
        fingerprint,
        committed_base,
        committed_change,
        committed_outcome,
    }
}

const PORTABILITY_TEST: &str =
    "service_inspected_bundle_transfers_real_cas_and_reopens_without_operational_receipts";
const PORTABILITY_PHASE_ENV: &str = "MOTIONWRIGHT_TEST_PORTABILITY_PHASE";
const PORTABILITY_ROOT_ENV: &str = "MOTIONWRIGHT_TEST_PORTABILITY_ROOT";
const PORTABILITY_NONCE_ENV: &str = "MOTIONWRIGHT_TEST_PORTABILITY_NONCE";

#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct PortabilityCheckpoint {
    schema: String,
    nonce: Uuid,
    fixture: OperationalBackupFixture,
}

#[derive(Debug, PartialEq, serde::Serialize, serde::Deserialize)]
struct PortabilityPhaseReceipt {
    schema: String,
    selector: String,
    nonce: Uuid,
    phase: String,
    checkpoint_sha256: String,
    manifest_sha256: String,
}

fn write_portability_json_new(path: &Path, value: &impl serde::Serialize) {
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .unwrap();
    file.write_all(&serde_json::to_vec_pretty(value).unwrap())
        .unwrap();
    file.sync_all().unwrap();
}

fn read_portability_checkpoint(root: &Path, nonce: Uuid) -> PortabilityCheckpoint {
    let checkpoint: PortabilityCheckpoint =
        serde_json::from_slice(&fs::read(root.join("checkpoint.json")).unwrap()).unwrap();
    assert_eq!(
        checkpoint.schema,
        "motionwright.test.portability-checkpoint/1"
    );
    assert_eq!(checkpoint.nonce, nonce);
    assert_eq!(checkpoint.fixture.bundle, root.join("portable"));
    assert_eq!(checkpoint.fixture.project.scenes.len(), 2);
    assert_eq!(checkpoint.fixture.sources.len(), 2);
    assert_eq!(checkpoint.fixture.project.branches.len(), 2);
    checkpoint
}

fn portability_phase_receipt(root: &Path, nonce: Uuid, phase: &str) -> PortabilityPhaseReceipt {
    let checkpoint = read_portability_checkpoint(root, nonce);
    PortabilityPhaseReceipt {
        schema: "motionwright.test.portability-phase/1".into(),
        selector: PORTABILITY_TEST.into(),
        nonce,
        phase: phase.into(),
        checkpoint_sha256: hex::encode(Sha256::digest(
            fs::read(root.join("checkpoint.json")).unwrap(),
        )),
        manifest_sha256: hex::encode(Sha256::digest(&checkpoint.fixture.manifest_bytes)),
    }
}

fn assert_original_portability_bundle(fixture: &OperationalBackupFixture) {
    let bytes = fs::read(fixture.bundle.join("manifest.json")).unwrap();
    assert_eq!(bytes, fixture.manifest_bytes);
    assert_eq!(
        serde_json::from_slice::<ProjectBundleManifest>(&bytes).unwrap(),
        fixture.manifest
    );
    assert_eq!(
        fixture_fingerprint(&fixture.bundle),
        fixture.fingerprint.as_str()
    );
    assert_eq!(fixture.manifest.blobs.len(), 2);
    for (digest, original) in &fixture.sources {
        let member = fixture
            .manifest
            .blobs
            .iter()
            .find(|member| &member.sha256 == digest)
            .unwrap();
        let member_bytes = fs::read(fixture.bundle.join(&member.relative_path)).unwrap();
        assert_eq!(member.size_bytes, original.len() as u64);
        assert_eq!(member_bytes, *original);
        assert_eq!(hex::encode(Sha256::digest(&member_bytes)), *digest);
    }
}

fn assert_same_portability_snapshot(actual: &ProjectBackup, retained: &ProjectBackup) {
    // This read takes a new exported_at; all persisted content must be identical.
    let mut expected = retained.clone();
    expected.exported_at = actual.exported_at.clone();
    assert_eq!(*actual, expected);
}

fn assert_reopened_committed_source(root: &Path, fixture: &OperationalBackupFixture) {
    let source = StudioService::open(root.join("source/state.sqlite3")).unwrap();
    assert_eq!(source.project(fixture.project.id).unwrap(), fixture.project);
    let before = source
        .export_project_at(fixture.project.id, &fixture.project.stamp())
        .unwrap();
    assert_same_portability_snapshot(&before, &fixture.backup);
    assert_eq!(before.events, fixture.events);
    {
        let shared = source.store();
        let store = shared.lock();
        for (index, (digest, _)) in fixture.sources.iter().enumerate() {
            assert!(!root.join(format!("source-{index}.bin")).exists());
            assert!(
                !store
                    .blob_root()
                    .join("sha256")
                    .join(&digest[..2])
                    .join(digest)
                    .exists()
            );
            assert!(store.read_blob(digest, 4096).is_err());
        }
    }

    // Only an outcome received as COMMITTED before process exit is replayed.
    // A missing phase receipt or an unknown child outcome never reaches here.
    assert!(!fixture.committed_outcome.replayed);
    assert_eq!(fixture.committed_outcome.request_id, "return-to-main");
    assert_eq!(fixture.committed_outcome.project, fixture.project);
    assert_eq!(
        fixture.committed_outcome.previous_revision,
        fixture.committed_base.revision
    );
    assert_eq!(
        fixture.committed_change,
        Change::CheckoutBranch {
            branch_id: fixture.main_branch
        }
    );
    let replay = source
        .apply(
            fixture.project.id,
            &fixture.committed_base,
            &fixture.committed_outcome.request_id,
            &fixture.committed_change,
        )
        .unwrap();
    let mut expected = fixture.committed_outcome.clone();
    expected.replayed = true;
    assert_eq!(replay, expected);
    assert_eq!(source.project(fixture.project.id).unwrap(), fixture.project);
    let after = source
        .export_project_at(fixture.project.id, &fixture.project.stamp())
        .unwrap();
    assert_same_portability_snapshot(&after, &before);
    assert_eq!(after.events, fixture.events);
}

struct PortabilityEvidence {
    root: PathBuf,
    temporary: Option<tempfile::TempDir>,
    complete: bool,
}

impl PortabilityEvidence {
    fn new(nonce: Uuid) -> Self {
        let (base, temporary) = match std::env::var_os(
            "MOTIONWRIGHT_TEST_PORTABILITY_EVIDENCE_ROOT",
        ) {
            Some(path) => {
                let base = PathBuf::from(path);
                assert!(base.is_absolute(), "evidence root must be absolute");
                assert!(base.is_dir(), "evidence root must already be a directory");
                let base = base.canonicalize().unwrap();
                for ancestor in base.ancestors() {
                    match fs::symlink_metadata(ancestor.join(".git")) {
                        Ok(git) => {
                            assert!(
                                git.is_dir()
                                    && base.starts_with(ancestor.join("knowledge/runtime")),
                                "evidence must be outside checkouts or inside their private knowledge/runtime"
                            );
                        }
                        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                        Err(error) => panic!("cannot inspect evidence ancestor: {error}"),
                    }
                }
                (base, None)
            }
            None => {
                let temporary = tempfile::tempdir().unwrap();
                (temporary.path().to_path_buf(), Some(temporary))
            }
        };
        let root = base.canonicalize().unwrap().join(format!("acc003-{nonce}"));
        fs::create_dir(&root).unwrap();
        fs::create_dir(root.join("tmp")).unwrap();
        Self {
            root,
            temporary,
            complete: false,
        }
    }
}

impl Drop for PortabilityEvidence {
    fn drop(&mut self) {
        if !self.complete {
            if let Some(temporary) = self.temporary.take() {
                let _ = temporary.keep();
            }
            let _ = writeln!(
                std::io::stderr().lock(),
                "ACC003 evidence retained after failure: {}",
                self.root.display()
            );
        }
    }
}

struct PortabilityChild {
    child: Option<Child>,
    root: PathBuf,
    nonce: Uuid,
    phase: String,
    stdout_path: PathBuf,
    stderr_path: PathBuf,
    status_path: PathBuf,
    completion_path: PathBuf,
    executable: Option<String>,
    process_id: Option<u32>,
    spawned: bool,
    exit_status: Option<ExitStatus>,
    timed_out: bool,
    cleanup_attempted: bool,
    cleanup_timed_out: bool,
    phase_passed: bool,
    stage: &'static str,
    errors: Vec<String>,
    status_write_attempted: bool,
    status_write_error: Option<String>,
}

impl PortabilityChild {
    fn new(root: &Path, nonce: Uuid, phase: &str) -> Self {
        // Custody exists before log creation, current_exe, or spawn can fail.
        Self {
            child: None,
            root: root.to_path_buf(),
            nonce,
            phase: phase.into(),
            stdout_path: root.join(format!("{phase}.stdout.log")),
            stderr_path: root.join(format!("{phase}.stderr.log")),
            status_path: root.join(format!("{phase}.status.json")),
            completion_path: root.join(format!("{phase}.completed.json")),
            executable: None,
            process_id: None,
            spawned: false,
            exit_status: None,
            timed_out: false,
            cleanup_attempted: false,
            cleanup_timed_out: false,
            phase_passed: false,
            stage: "prepare",
            errors: Vec::new(),
            status_write_attempted: false,
            status_write_error: None,
        }
    }

    fn error(&mut self, stage: &str, error: impl std::fmt::Display) -> String {
        let error = format!("{stage}: {error}");
        self.errors.push(error.clone());
        error
    }

    fn observed_exit(&mut self, status: ExitStatus) {
        // try_wait has reaped this exact child; no PID comparison is an oracle.
        self.exit_status = Some(status);
        drop(self.child.take());
    }

    fn execute(&mut self) -> Result<(), String> {
        self.stage = "stdout";
        let stdout = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&self.stdout_path)
            .map_err(|error| self.error("stdout", error))?;
        self.stage = "stderr";
        let stderr = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&self.stderr_path)
            .map_err(|error| self.error("stderr", error))?;
        self.stage = "current_exe";
        let executable =
            std::env::current_exe().map_err(|error| self.error("current_exe", error))?;
        self.executable = Some(executable.display().to_string());
        self.stage = "spawn";
        self.child = Some(
            Command::new(&executable)
                .args([
                    "--exact",
                    PORTABILITY_TEST,
                    "--nocapture",
                    "--test-threads=1",
                ])
                .current_dir(&self.root)
                .env(PORTABILITY_PHASE_ENV, &self.phase)
                .env(PORTABILITY_ROOT_ENV, &self.root)
                .env(PORTABILITY_NONCE_ENV, self.nonce.to_string())
                .env("TEMP", self.root.join("tmp"))
                .env("TMP", self.root.join("tmp"))
                .env("TMPDIR", self.root.join("tmp"))
                .stdin(Stdio::null())
                .stdout(Stdio::from(stdout))
                .stderr(Stdio::from(stderr))
                .spawn()
                .map_err(|error| self.error("spawn", error))?,
        );
        self.spawned = true;
        self.process_id = self.child.as_ref().map(Child::id);
        self.stage = "try_wait";
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            let poll = self.child.as_mut().expect("owned spawned child").try_wait();
            match poll {
                Ok(Some(status)) => {
                    let success = status.success();
                    self.observed_exit(status);
                    return if success {
                        Ok(())
                    } else {
                        Err(self.error("child exit", format!("{status:?}")))
                    };
                }
                Ok(None) => {}
                Err(error) => return Err(self.error("try_wait", error)),
            }
            if Instant::now() >= deadline {
                self.timed_out = true;
                return Err(self.error("deadline", "child did not exit within 30 seconds"));
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    fn cleanup_bounded(&mut self) {
        if self.cleanup_attempted || self.child.is_none() {
            return;
        }
        self.cleanup_attempted = true;
        let deadline = Instant::now() + Duration::from_secs(2);
        // An exit may have raced the error/deadline. Never signal an unrelated PID.
        match self.child.as_mut().unwrap().try_wait() {
            Ok(Some(status)) => {
                self.observed_exit(status);
                return;
            }
            Ok(None) => {}
            Err(error) => {
                self.error("cleanup initial try_wait", error);
            }
        }
        if let Err(error) = self.child.as_mut().unwrap().kill() {
            self.error("cleanup kill", error);
        }
        loop {
            if Instant::now() >= deadline {
                self.cleanup_timed_out = true;
                self.error("cleanup deadline", "child outcome remains UNKNOWN");
                return;
            }
            match self.child.as_mut().unwrap().try_wait() {
                Ok(Some(status)) => {
                    self.observed_exit(status);
                    return;
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(10)),
                Err(error) => {
                    self.error("cleanup try_wait", error);
                    return;
                }
            }
        }
    }

    fn publish_status_once(&mut self) -> Result<(), String> {
        if self.status_write_attempted {
            return self.status_write_error.clone().map_or(Ok(()), Err);
        }
        // Set this before opening. A partial write is evidence, never overwritten
        // or retried from Drop, including after a write/sync failure.
        self.status_write_attempted = true;
        let state = if self.exit_status.is_some() {
            "EXITED"
        } else if self.spawned {
            "UNKNOWN"
        } else {
            "NOT_STARTED"
        };
        let value = serde_json::json!({
            "schema": "motionwright.test.portability-child-status/2",
            "selector": PORTABILITY_TEST,
            "nonce": self.nonce,
            "phase": self.phase,
            "stage": self.stage,
            "root": self.root.display().to_string(),
            "stdout_path": self.stdout_path.display().to_string(),
            "stderr_path": self.stderr_path.display().to_string(),
            "status_path": self.status_path.display().to_string(),
            "completion_receipt_path": self.completion_path.display().to_string(),
            "executable": self.executable,
            "process_id_diagnostic_only": self.process_id,
            "process_state": state,
            "exit_status": self.exit_status.as_ref().map(|status| format!("{status:?}")),
            "exit_code": self.exit_status.as_ref().and_then(ExitStatus::code),
            "success": self.exit_status.as_ref().is_some_and(ExitStatus::success),
            "timed_out": self.timed_out,
            "cleanup_attempted": self.cleanup_attempted,
            "cleanup_timed_out": self.cleanup_timed_out,
            "completion_receipt_verified": self.phase_passed,
            "errors": self.errors,
        });
        let published = (|| {
            let bytes = serde_json::to_vec_pretty(&value).map_err(|error| error.to_string())?;
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&self.status_path)
                .map_err(|error| error.to_string())?;
            file.write_all(&bytes).map_err(|error| error.to_string())?;
            file.sync_all().map_err(|error| error.to_string())?;
            Ok::<(), String>(())
        })();
        if let Err(error) = &published {
            self.status_write_error = Some(error.clone());
        }
        published
    }

    fn finalize(&mut self) -> Result<(), String> {
        self.cleanup_bounded();
        self.publish_status_once()
    }
}

impl Drop for PortabilityChild {
    fn drop(&mut self) {
        if self.status_write_attempted {
            return;
        }
        if std::thread::panicking() {
            self.error("unwind", format!("phase interrupted at {}", self.stage));
        } else if !self.phase_passed && self.errors.is_empty() {
            self.error("guard", "phase ended without a verified completion receipt");
        }
        // Never call blocking wait. Failed cleanup remains UNKNOWN and the
        // parent's evidence guard retains all files instead of removing TempDir.
        if let Err(error) = self.finalize() {
            let _ = writeln!(
                std::io::stderr().lock(),
                "ACC003 could not publish status at {}: {error}; evidence retained at {}",
                self.status_path.display(),
                self.root.display()
            );
        }
    }
}

fn run_portability_phase(root: &Path, nonce: Uuid, phase: &str) {
    let mut child = PortabilityChild::new(root, nonce, phase);
    let executed = child.execute();
    if executed.is_ok() {
        child.stage = "completion receipt";
        let receipt: PortabilityPhaseReceipt = serde_json::from_slice(
            &fs::read(&child.completion_path)
                .expect("exit 0 alone cannot prove that the selected test ran"),
        )
        .unwrap();
        assert_eq!(receipt, portability_phase_receipt(root, nonce, phase));
        child.phase_passed = true;
    }
    let published = child.finalize();
    if let Err(error) = executed {
        panic!(
            "ACC003 phase {phase} failed: {error}; status={published:?}; stdout={}; stderr={}",
            fs::read_to_string(&child.stdout_path).unwrap_or_else(|error| error.to_string()),
            fs::read_to_string(&child.stderr_path).unwrap_or_else(|error| error.to_string()),
        );
    }
    published.expect("status publication must succeed before the next phase");
    assert!(child.phase_passed);
}

#[test]
fn service_inspected_bundle_transfers_real_cas_and_reopens_without_operational_receipts() {
    if let Some(phase) = std::env::var_os(PORTABILITY_PHASE_ENV) {
        let phase = phase.to_str().expect("phase must be UTF-8");
        assert!(matches!(phase, "prepare" | "continue"));
        let root = PathBuf::from(std::env::var_os(PORTABILITY_ROOT_ENV).unwrap());
        assert!(root.is_absolute());
        let nonce = Uuid::parse_str(&std::env::var(PORTABILITY_NONCE_ENV).unwrap()).unwrap();
        assert!(!nonce.is_nil());
        match phase {
            "prepare" => {
                let fixture = operational_backup_fixture(&root);
                assert_original_portability_bundle(&fixture);
                write_portability_json_new(
                    &root.join("checkpoint.json"),
                    &PortabilityCheckpoint {
                        schema: "motionwright.test.portability-checkpoint/1".into(),
                        nonce,
                        fixture,
                    },
                );
            }
            "continue" => {
                let checkpoint = read_portability_checkpoint(&root, nonce);
                let prepared: PortabilityPhaseReceipt =
                    serde_json::from_slice(&fs::read(root.join("prepare.completed.json")).unwrap())
                        .unwrap();
                assert_eq!(prepared, portability_phase_receipt(&root, nonce, "prepare"));
                assert_original_portability_bundle(&checkpoint.fixture);
                continue_operational_backup(&root, checkpoint.fixture.clone());
                assert_original_portability_bundle(&checkpoint.fixture);
            }
            _ => unreachable!(),
        }
        // This is the final action, after every assertion and every Store drop.
        write_portability_json_new(
            &root.join(format!("{phase}.completed.json")),
            &portability_phase_receipt(&root, nonce, phase),
        );
        return;
    }

    let nonce = Uuid::now_v7();
    let mut evidence = PortabilityEvidence::new(nonce);
    run_portability_phase(&evidence.root, nonce, "prepare");
    run_portability_phase(&evidence.root, nonce, "continue");
    evidence.complete = true;
}

fn continue_operational_backup(root: &Path, fixture: OperationalBackupFixture) {
    assert_reopened_committed_source(root, &fixture);
    let OperationalBackupFixture {
        bundle,
        project: original,
        sources,
        main_branch,
        alternate_branch,
        alternate_scenes,
        events,
        ..
    } = fixture;
    let target_path = root.join("target/state.sqlite3");
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
    assert!(target.projects(10).unwrap().is_empty());
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
    assert!(!imported.generation.is_nil());
    assert_ne!(imported.generation, original.generation);
    let mut expected = original.clone();
    expected.generation = imported.generation;
    expected.updated_at = imported.updated_at;
    assert_eq!(imported, expected);
    assert_eq!(
        target
            .export_project_at(imported.id, &imported.stamp())
            .unwrap()
            .events,
        events
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
    {
        let shared = reopened.store();
        let store = shared.lock();
        for (digest, bytes) in &sources {
            assert_eq!(store.read_blob(digest, 4096).unwrap(), *bytes);
        }
    }
    let alternate = apply_portable_change(
        &reopened,
        &imported,
        "restored-checkout-alternate",
        Change::CheckoutBranch {
            branch_id: alternate_branch,
        },
    );
    assert_eq!(alternate.active_branch, alternate_branch);
    assert_eq!(alternate.scenes, alternate_scenes);
    assert_eq!(alternate.assets, original.assets);
    let closing = alternate_scenes[0].id;
    let edited = apply_portable_change(
        &reopened,
        &alternate,
        "edit-restored-alternate",
        Change::UpdateSceneObjective {
            scene_id: closing,
            objective: "Edited after backup restore".into(),
        },
    );
    assert_eq!(edited.scenes[0].id, closing);
    assert_eq!(edited.scenes[0].objective, "Edited after backup restore");
    drop(reopened);

    let restarted = StudioService::open(&target_path).unwrap();
    assert_eq!(restarted.project(edited.id).unwrap(), edited);
    let main = apply_portable_change(
        &restarted,
        &edited,
        "restored-checkout-main",
        Change::CheckoutBranch {
            branch_id: main_branch,
        },
    );
    assert_eq!(main.active_branch, main_branch);
    assert_eq!(main.scenes, original.scenes);
    let alternate_again = apply_portable_change(
        &restarted,
        &main,
        "restored-checkout-edited-alternate",
        Change::CheckoutBranch {
            branch_id: alternate_branch,
        },
    );
    assert_eq!(alternate_again.scenes, edited.scenes);
    assert_eq!(alternate_again.assets, original.assets);
    let shared = restarted.store();
    let store = shared.lock();
    for (digest, bytes) in &sources {
        assert_eq!(store.read_blob(digest, 4096).unwrap(), *bytes);
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
