//! Real-process checks for managed CAS retention.
#![cfg(test)]

use super::*;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{BufRead, BufReader},
    process::{Child, Command, ExitStatus, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc,
    },
    thread,
    time::Instant,
};

const CHILD_MODE: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_MODE";
const CASE_PATH: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_CASE";
const TARGET_ROOT: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_ROOT";
const PROJECT: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_PROJECT";
const PHASE: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_PHASE";
const ACTION: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_ACTION";
const OBSERVATION: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_OBSERVATION";
const RETENTION_SECONDS: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_SECONDS";
const MAX_OBJECTS: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_MAX_OBJECTS";
const DATABASE: &str = "MOTIONWRIGHT_TEST_CAS_RETENTION_DATABASE";
const EXIT_AT_CHECKPOINT: i32 = 87;
const CHILD_TIMEOUT: Duration = Duration::from_secs(30);
static CHECKPOINT_USED: AtomicBool = AtomicBool::new(false);

pub(super) fn checkpoint(root: &Path, phase: &str) {
    if std::env::var_os(CHILD_MODE).is_none() || std::env::var(PHASE).as_deref() != Ok(phase) {
        return;
    }
    let expected = PathBuf::from(std::env::var_os(TARGET_ROOT).unwrap());
    if root.canonicalize().unwrap() != expected.canonicalize().unwrap()
        || CHECKPOINT_USED.swap(true, Ordering::SeqCst)
    {
        return;
    }
    let project_id = Uuid::parse_str(&std::env::var(PROJECT).unwrap()).unwrap();
    println!("CAS_RETENTION_CHECKPOINT:{project_id}:{phase}");
    std::io::stdout().flush().unwrap();
    match std::env::var(ACTION).unwrap().as_str() {
        "exit" => std::process::exit(EXIT_AT_CHECKPOINT),
        "hold" => {
            let mut line = String::new();
            assert!(std::io::stdin().lock().read_line(&mut line).unwrap() > 0);
            assert_eq!(line.trim_end(), "continue");
        }
        other => panic!("unexpected CAS checkpoint action: {other}"),
    }
}

#[derive(Serialize, Deserialize)]
struct RetentionCase {
    database: PathBuf,
    peer_database: PathBuf,
    blob_root: PathBuf,
    bundle: PathBuf,
    project_id: Uuid,
    digest: String,
    size_bytes: u64,
}

type SqlRows = BTreeMap<String, Vec<Vec<String>>>;

#[derive(Debug, Serialize, Deserialize)]
struct RecoveryCounts {
    root_managed: bool,
    inspected: usize,
    removed: usize,
    retained_by_pins: usize,
    retained_by_age: usize,
    unresolved: usize,
    truncated: bool,
    captures_removed: usize,
}

#[derive(Serialize, Deserialize)]
struct RetentionObservation {
    project: Option<ProjectBackup>,
    rows: SqlRows,
    blob: Option<Vec<u8>>,
    recovery: Option<RecoveryCounts>,
}

fn logical_rows(store: &Store) -> SqlRows {
    let names: Vec<String> = store.conn
        .prepare("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    let mut result = BTreeMap::new();
    for name in names {
        let sql = format!("SELECT * FROM \"{}\"", name.replace('"', "\"\""));
        let mut statement = store.conn.prepare(&sql).unwrap();
        let columns = statement.column_count();
        let mut rows: Vec<Vec<String>> = statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| {
                        row.get::<_, rusqlite::types::Value>(column)
                            .map(|value| format!("{value:?}"))
                    })
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .map(|row| row.unwrap())
            .collect();
        rows.sort();
        result.insert(name, rows);
    }
    result
}

fn physical_blob(case: &RetentionCase) -> Option<Vec<u8>> {
    let path = case
        .blob_root
        .join("sha256")
        .join(&case.digest[..2])
        .join(&case.digest);
    match fs::read(path) {
        Ok(bytes) => {
            assert_eq!(bytes.len() as u64, case.size_bytes);
            assert_eq!(hex::encode(Sha256::digest(&bytes)), case.digest);
            Some(bytes)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => panic!("cannot inspect retained CAS bytes: {error}"),
    }
}

fn observation(
    store: &mut Store,
    case: &RetentionCase,
    recovery: Option<RecoveryCounts>,
) -> RetentionObservation {
    let project = match store.export_project(case.project_id) {
        Ok(backup) => Some(backup),
        Err(StorageError::NotFound) => None,
        Err(error) => panic!("unexpected imported-project observation: {error}"),
    };
    RetentionObservation {
        project,
        rows: logical_rows(store),
        blob: physical_blob(case),
        recovery,
    }
}

struct Fixture {
    _directory: tempfile::TempDir,
    case: RetentionCase,
    config: PathBuf,
    before: SqlRows,
    manifest: ProjectBundleManifest,
    manifest_bytes: Vec<u8>,
    payload: Vec<u8>,
}

impl Fixture {
    fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let payload = b"Real-process retained CAS source\n".to_vec();
        let input = root.join("original-media.bin");
        fs::write(&input, &payload).unwrap();
        let mut source = Store::open(root.join("source/state.sqlite3")).unwrap();
        let descriptor = source.ingest_blob_file(&input).unwrap();
        let project = source.create_named_project("CAS retention source").unwrap();
        let project = source
            .apply(
                project.id,
                &project.stamp(),
                "retention-source-asset",
                &Change::AddAsset {
                    asset: motionwright_domain::Asset {
                        id: Uuid::now_v7(),
                        name: "retained original".into(),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(descriptor.sha256),
                        source_revision: Some("local-import".into()),
                    },
                },
            )
            .unwrap()
            .project;
        let bundle = root.join("portable");
        let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
        drop(source);
        assert_eq!(manifest.blobs.len(), 1);
        let manifest_bytes = fs::read(bundle.join("manifest.json")).unwrap();
        let case = RetentionCase {
            database: root.join("primary/state.sqlite3"),
            peer_database: root.join("peer/state.sqlite3"),
            blob_root: root.join("shared-cas"),
            bundle,
            project_id: project.id,
            digest: manifest.blobs[0].sha256.clone(),
            size_bytes: manifest.blobs[0].size_bytes,
        };
        let mut primary =
            Store::open_with_managed_blob_root(&case.database, &case.blob_root).unwrap();
        primary
            .create_named_project("Keep existing destination state")
            .unwrap();
        let before = logical_rows(&primary);
        drop(primary);
        drop(Store::open_with_managed_blob_root(&case.peer_database, &case.blob_root).unwrap());
        assert!(physical_blob(&case).is_none());
        let config = root.join("case.json");
        serde_json::to_writer(File::create_new(&config).unwrap(), &case).unwrap();
        Self {
            _directory: directory,
            case,
            config,
            before,
            manifest,
            manifest_bytes,
            payload,
        }
    }

    fn assert_not_applied(&self, seen: &RetentionObservation) {
        assert!(seen.project.is_none(), "uncommitted import became visible");
        assert_eq!(seen.rows, self.before, "SQLite rows changed");
    }

    fn assert_imported(&self, seen: &RetentionObservation) {
        let backup = seen.project.as_ref().expect("committed import missing");
        assert_eq!(backup.project.id, self.case.project_id);
        assert_ne!(
            backup.project.generation,
            self.manifest.backup.project.generation
        );
        assert_eq!(
            backup.project.revision,
            self.manifest.backup.project.revision
        );
        assert_eq!(backup.project.assets, self.manifest.backup.project.assets);
        assert_eq!(backup.events, self.manifest.backup.events);
        assert_eq!(seen.blob.as_deref(), Some(self.payload.as_slice()));
    }

    fn assert_sources_unchanged(&self) {
        assert_eq!(
            fs::read(self.case.bundle.join("manifest.json")).unwrap(),
            self.manifest_bytes
        );
        assert_eq!(
            fs::read(self.case.bundle.join(&self.manifest.blobs[0].relative_path)).unwrap(),
            self.payload,
        );
        assert_eq!(
            fs::read(self.config.with_file_name("original-media.bin")).unwrap(),
            self.payload
        );
    }
}

#[derive(Default)]
struct ActorOptions {
    peer: bool,
    phase: &'static str,
    action: &'static str,
    retention_seconds: u64,
    max_objects: Option<usize>,
}

struct Actor {
    child: Option<Child>,
    reader: Option<thread::JoinHandle<()>>,
    lines: mpsc::Receiver<String>,
    stdout: Vec<String>,
    stderr: PathBuf,
    observation: PathBuf,
    phase: &'static str,
    project_id: Uuid,
}

fn spawn_actor(
    fixture: &Fixture,
    test: &str,
    label: &str,
    mode: &str,
    options: ActorOptions,
) -> Actor {
    let stderr = fixture.config.with_file_name(format!("{label}.stderr"));
    let observation = fixture.config.with_file_name(format!("{label}.json"));
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            &format!("cas_retention_process_tests::{test}"),
            "--nocapture",
            "--test-threads=1",
        ])
        .env(CHILD_MODE, mode)
        .env(CASE_PATH, &fixture.config)
        .env(TARGET_ROOT, &fixture.case.blob_root)
        .env(PROJECT, fixture.case.project_id.to_string())
        .env(DATABASE, if options.peer { "peer" } else { "primary" })
        .env(PHASE, options.phase)
        .env(ACTION, options.action)
        .env(RETENTION_SECONDS, options.retention_seconds.to_string())
        .env(MAX_OBJECTS, options.max_objects.unwrap_or(64).to_string())
        .env(OBSERVATION, &observation)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(File::create_new(&stderr).unwrap())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let (sender, lines) = mpsc::channel();
    let reader = thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            if sender.send(line.unwrap()).is_err() {
                break;
            }
        }
    });
    Actor {
        child: Some(child),
        reader: Some(reader),
        lines,
        stdout: Vec::new(),
        stderr,
        observation,
        phase: options.phase,
        project_id: fixture.case.project_id,
    }
}

impl Actor {
    fn wait_checkpoint(&mut self) {
        let marker = format!(
            "CAS_RETENTION_CHECKPOINT:{}:{}",
            self.project_id, self.phase
        );
        let deadline = Instant::now() + CHILD_TIMEOUT;
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            let line = self.lines.recv_timeout(remaining).unwrap_or_else(|error| {
                panic!(
                    "checkpoint {marker} not reached: {error}; stderr: {}",
                    fs::read_to_string(&self.stderr).unwrap()
                )
            });
            let reached = line.contains(&marker);
            self.stdout.push(line);
            if reached {
                return;
            }
        }
    }

    fn assert_alive(&mut self) {
        assert!(
            self.child.as_mut().unwrap().try_wait().unwrap().is_none(),
            "writer exited before explicit release"
        );
    }

    fn resume(&mut self) {
        self.assert_alive();
        let mut stdin = self.child.as_mut().unwrap().stdin.take().unwrap();
        stdin.write_all(b"continue\n").unwrap();
        stdin.flush().unwrap();
    }

    fn wait(&mut self) -> ExitStatus {
        let deadline = Instant::now() + CHILD_TIMEOUT;
        loop {
            if let Some(status) = self.child.as_mut().unwrap().try_wait().unwrap() {
                return status;
            }
            assert!(
                Instant::now() < deadline,
                "CAS subprocess timed out; stderr: {}",
                fs::read_to_string(&self.stderr).unwrap()
            );
            // Poll only process completion, never retention age or readiness.
            thread::sleep(Duration::from_millis(25));
        }
    }

    fn finish(mut self, expected: i32) -> Option<RetentionObservation> {
        let status = self.wait();
        self.child.take();
        self.reader.take().unwrap().join().unwrap();
        self.stdout.extend(self.lines.try_iter());
        let stdout = self.stdout.join("\n");
        let stderr = fs::read_to_string(&self.stderr).unwrap();
        assert_eq!(status.code(), Some(expected), "{stdout}\n{stderr}");
        if expected == EXIT_AT_CHECKPOINT {
            assert!(
                stdout.contains(&format!(
                    "CAS_RETENTION_CHECKPOINT:{}:{}",
                    self.project_id, self.phase
                )),
                "{stdout}\n{stderr}"
            );
            None
        } else {
            assert!(
                stdout.contains(&format!("CAS_RETENTION_OBSERVED:{}", self.project_id)),
                "{stdout}\n{stderr}"
            );
            Some(serde_json::from_slice(&fs::read(&self.observation).unwrap()).unwrap())
        }
    }
}

impl Drop for Actor {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}

fn run_child_role() -> bool {
    let mode = match std::env::var(CHILD_MODE) {
        Ok(mode) => mode,
        Err(std::env::VarError::NotPresent) => return false,
        Err(error) => panic!("invalid child mode: {error}"),
    };
    let case: RetentionCase =
        serde_json::from_slice(&fs::read(std::env::var_os(CASE_PATH).unwrap()).unwrap()).unwrap();
    assert_eq!(
        Uuid::parse_str(&std::env::var(PROJECT).unwrap()).unwrap(),
        case.project_id
    );
    assert_eq!(
        load_bundle_manifest(&case.bundle)
            .unwrap()
            .backup
            .project
            .id,
        case.project_id
    );
    let database = match std::env::var(DATABASE).unwrap().as_str() {
        "primary" => &case.database,
        "peer" => &case.peer_database,
        other => panic!("unexpected test database: {other}"),
    };
    let mut store = Store::open_with_managed_blob_root(database, &case.blob_root).unwrap();
    let recovery = match mode.as_str() {
        "writer" => {
            store.import_project_bundle(&case.bundle).unwrap();
            None
        }
        "observe" => None,
        "recover" => {
            let report = store
                .recover_cas(CasRetentionPolicy {
                    retention: Duration::from_secs(
                        std::env::var(RETENTION_SECONDS).unwrap().parse().unwrap(),
                    ),
                    max_objects: std::env::var(MAX_OBJECTS).unwrap().parse().unwrap(),
                })
                .unwrap();
            Some(RecoveryCounts {
                root_managed: report.root_managed,
                inspected: report.inspected,
                removed: report.removed,
                retained_by_pins: report.retained_by_pins,
                retained_by_age: report.retained_by_age,
                unresolved: report.unresolved,
                truncated: report.truncated,
                captures_removed: report.captures_removed,
            })
        }
        other => panic!("unexpected retention actor: {other}"),
    };
    let seen = observation(&mut store, &case, recovery);
    serde_json::to_writer(
        File::create_new(std::env::var_os(OBSERVATION).unwrap()).unwrap(),
        &seen,
    )
    .unwrap();
    println!("CAS_RETENTION_OBSERVED:{}", case.project_id);
    std::io::stdout().flush().unwrap();
    true
}

fn recover(
    fixture: &Fixture,
    test: &str,
    label: &str,
    options: ActorOptions,
) -> RetentionObservation {
    spawn_actor(fixture, test, label, "recover", options)
        .finish(0)
        .unwrap()
}

fn observe(fixture: &Fixture, test: &str, label: &str, peer: bool) -> RetentionObservation {
    spawn_actor(
        fixture,
        test,
        label,
        "observe",
        ActorOptions {
            peer,
            ..ActorOptions::default()
        },
    )
    .finish(0)
    .unwrap()
}

fn crash_writer(fixture: &Fixture, test: &str, phase: &'static str) {
    spawn_actor(
        fixture,
        test,
        "crashed-writer",
        "writer",
        ActorOptions {
            phase,
            action: "exit",
            ..ActorOptions::default()
        },
    )
    .finish(EXIT_AT_CHECKPOINT);
}

#[test]
fn real_live_bundle_writer_survives_peer_cas_recovery() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_live_bundle_writer_survives_peer_cas_recovery";
    let fixture = Fixture::new();
    // Admission must finish before the live writer owns SQLite's writer lock.
    let mut collector =
        Store::open_with_managed_blob_root(&fixture.case.database, &fixture.case.blob_root)
            .unwrap();
    let mut writer = spawn_actor(
        &fixture,
        TEST,
        "held-writer",
        "writer",
        ActorOptions {
            phase: "import_after_publication",
            action: "hold",
            ..ActorOptions::default()
        },
    );
    writer.wait_checkpoint();
    writer.assert_alive();
    assert_eq!(
        physical_blob(&fixture.case).as_deref(),
        Some(fixture.payload.as_slice())
    );
    let same_database = collector
        .recover_cas(CasRetentionPolicy {
            retention: Duration::ZERO,
            max_objects: 64,
        })
        .unwrap();
    assert!(same_database.root_managed && !same_database.truncated);
    assert_eq!(
        same_database.removed, 0,
        "same-database GC unlinked a live writer's publication"
    );
    assert!(
        same_database.unresolved >= 1,
        "live writer was not retained"
    );
    assert_eq!(
        physical_blob(&fixture.case).as_deref(),
        Some(fixture.payload.as_slice())
    );
    writer.assert_alive();
    let while_live = recover(
        &fixture,
        TEST,
        "peer-gc-while-live",
        ActorOptions {
            peer: true,
            ..ActorOptions::default()
        },
    );
    let report = while_live.recovery.as_ref().unwrap();
    assert!(report.root_managed && !report.truncated);
    assert_eq!(report.removed, 0, "GC unlinked a live writer's publication");
    assert!(
        report.unresolved + report.retained_by_pins >= 1,
        "live intent was not accounted for"
    );
    assert_eq!(while_live.blob.as_deref(), Some(fixture.payload.as_slice()));
    writer.assert_alive();
    writer.resume();
    let committed = writer.finish(0).unwrap();
    fixture.assert_imported(&committed);
    let reopened = recover(
        &fixture,
        TEST,
        "gc-after-writer-commit",
        ActorOptions::default(),
    );
    assert_eq!(reopened.recovery.as_ref().unwrap().removed, 0);
    fixture.assert_imported(&reopened);
    assert_eq!(
        reopened.rows, committed.rows,
        "GC changed committed SQLite state"
    );
    fixture.assert_sources_unchanged();
}

#[test]
fn real_precommit_exit_retains_then_reclaims_only_owned_orphan() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_precommit_exit_retains_then_reclaims_only_owned_orphan";
    let fixture = Fixture::new();
    crash_writer(&fixture, TEST, "import_before_commit");
    let interrupted = observe(&fixture, TEST, "observe-not-applied", false);
    fixture.assert_not_applied(&interrupted);
    assert_eq!(
        interrupted.blob.as_deref(),
        Some(fixture.payload.as_slice())
    );
    let retained = recover(
        &fixture,
        TEST,
        "gc-with-retention",
        ActorOptions {
            retention_seconds: 365 * 24 * 60 * 60,
            ..ActorOptions::default()
        },
    );
    let report = retained.recovery.as_ref().unwrap();
    assert!(report.root_managed && !report.truncated);
    assert_eq!(report.removed, 0);
    assert!(
        report.retained_by_age >= 1,
        "known orphan bypassed its retention interval"
    );
    assert_eq!(
        report.unresolved, 0,
        "dead NotApplied intent remained unknown"
    );
    fixture.assert_not_applied(&retained);
    assert_eq!(retained.blob.as_deref(), Some(fixture.payload.as_slice()));
    // ZERO selects immediate eligibility; no wall-clock aging or sleep.
    let reclaimed = recover(
        &fixture,
        TEST,
        "gc-eligible-orphan",
        ActorOptions::default(),
    );
    let report = reclaimed.recovery.as_ref().unwrap();
    assert!(report.root_managed && !report.truncated);
    assert_eq!(report.removed, 1);
    assert_eq!(report.unresolved, 0);
    assert!(
        reclaimed.blob.is_none(),
        "GC reported removal without unlinking the CAS name"
    );
    fixture.assert_not_applied(&reclaimed);
    let again = recover(
        &fixture,
        TEST,
        "gc-idempotent-reopen",
        ActorOptions::default(),
    );
    assert_eq!(again.recovery.as_ref().unwrap().removed, 0);
    assert_eq!(again.recovery.as_ref().unwrap().unresolved, 0);
    assert!(again.blob.is_none());
    fixture.assert_not_applied(&again);
    fixture.assert_sources_unchanged();
}

#[test]
fn real_peer_database_adoption_protects_shared_digest() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_peer_database_adoption_protects_shared_digest";
    let fixture = Fixture::new();
    crash_writer(&fixture, TEST, "import_after_publication");
    let interrupted = observe(&fixture, TEST, "observe-primary-not-applied", false);
    fixture.assert_not_applied(&interrupted);
    assert_eq!(
        interrupted.blob.as_deref(),
        Some(fixture.payload.as_slice())
    );
    // A separate database adopts an existing digest through the product import
    // path. No legacy ingest/verified-path call supplies an accidental test pin.
    let adopted = spawn_actor(
        &fixture,
        TEST,
        "peer-adoption",
        "writer",
        ActorOptions {
            peer: true,
            ..ActorOptions::default()
        },
    )
    .finish(0)
    .unwrap();
    fixture.assert_imported(&adopted);
    let collected = recover(
        &fixture,
        TEST,
        "primary-gc-after-adoption",
        ActorOptions::default(),
    );
    let report = collected.recovery.as_ref().unwrap();
    assert!(report.root_managed && !report.truncated);
    assert_eq!(
        report.removed, 0,
        "primary DB GC ignored another DB's committed adoption"
    );
    assert_eq!(report.unresolved, 0);
    fixture.assert_not_applied(&collected);
    assert_eq!(collected.blob.as_deref(), Some(fixture.payload.as_slice()));
    let peer = observe(&fixture, TEST, "reopened-peer", true);
    fixture.assert_imported(&peer);
    assert_eq!(
        peer.rows, adopted.rows,
        "shared CAS recovery altered the adopting DB"
    );
    fixture.assert_sources_unchanged();
}

#[test]
fn real_gc_exit_after_unlink_cannot_delete_republished_digest() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_gc_exit_after_unlink_cannot_delete_republished_digest";
    let fixture = Fixture::new();
    crash_writer(&fixture, TEST, "import_before_commit");
    let interrupted = observe(&fixture, TEST, "observe-before-gc-cut", false);
    fixture.assert_not_applied(&interrupted);
    assert_eq!(
        interrupted.blob.as_deref(),
        Some(fixture.payload.as_slice())
    );
    // Exit before the GC intent can be retired: recovery will see its old
    // digest again. The publication's bytes must actually have been unlinked.
    spawn_actor(
        &fixture,
        TEST,
        "gc-unlink-cut",
        "recover",
        ActorOptions {
            phase: "gc_after_unlink",
            action: "exit",
            ..ActorOptions::default()
        },
    )
    .finish(EXIT_AT_CHECKPOINT);
    assert!(physical_blob(&fixture.case).is_none());
    let after_cut = observe(&fixture, TEST, "observe-after-gc-cut", false);
    fixture.assert_not_applied(&after_cut);
    assert!(after_cut.blob.is_none());
    let republished = spawn_actor(
        &fixture,
        TEST,
        "peer-republication",
        "writer",
        ActorOptions {
            peer: true,
            ..ActorOptions::default()
        },
    )
    .finish(0)
    .unwrap();
    fixture.assert_imported(&republished);
    assert_eq!(
        physical_blob(&fixture.case).as_deref(),
        Some(fixture.payload.as_slice())
    );
    let recovered = recover(
        &fixture,
        TEST,
        "gc-after-republication",
        ActorOptions::default(),
    );
    let report = recovered.recovery.as_ref().unwrap();
    assert!(report.root_managed && !report.truncated);
    assert_eq!(
        report.removed, 0,
        "stale GC intent deleted a new protected publication"
    );
    assert_eq!(report.unresolved, 0);
    fixture.assert_not_applied(&recovered);
    assert_eq!(recovered.blob.as_deref(), Some(fixture.payload.as_slice()));
    let peer = observe(&fixture, TEST, "reopened-peer-after-gc-cut", true);
    fixture.assert_imported(&peer);
    assert_eq!(peer.rows, republished.rows);
    fixture.assert_sources_unchanged();
}

#[derive(Debug, Default)]
struct RootFootprint {
    cas: BTreeMap<PathBuf, u64>,
    cas_directories: BTreeSet<PathBuf>,
    staging: BTreeMap<PathBuf, u64>,
    staging_directories: BTreeSet<PathBuf>,
    metadata: BTreeMap<PathBuf, Vec<u8>>,
    metadata_directories: BTreeSet<PathBuf>,
}

#[derive(Debug, Clone, Copy, Serialize)]
struct FootprintCounts {
    cas_names: usize,
    cas_directories: usize,
    cas_bytes: u64,
    staging_names: usize,
    staging_directories: usize,
    staging_bytes: u64,
    metadata_names: usize,
    metadata_directories: usize,
    metadata_bytes: u64,
}

impl RootFootprint {
    fn counts(&self) -> FootprintCounts {
        FootprintCounts {
            cas_names: self.cas.len(),
            cas_directories: self.cas_directories.len(),
            cas_bytes: self.cas.values().sum(),
            staging_names: self.staging.len(),
            staging_directories: self.staging_directories.len(),
            staging_bytes: self.staging.values().sum(),
            metadata_names: self.metadata.len(),
            metadata_directories: self.metadata_directories.len(),
            metadata_bytes: self.metadata.values().map(|bytes| bytes.len() as u64).sum(),
        }
    }
}

fn root_footprint(blob_root: &Path, sidecar_root: &Path) -> RootFootprint {
    fn visit(root: &Path, directory: &Path, sidecar: bool, found: &mut RootFootprint) {
        for entry in fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            let info = fs::symlink_metadata(&path).unwrap();
            assert!(
                !info.file_type().is_symlink(),
                "unexpected link in test-owned storage: {}",
                path.display()
            );
            let relative = path.strip_prefix(root).unwrap().to_path_buf();
            let is_cas = !sidecar && relative.starts_with("sha256");
            let is_staging = !sidecar && relative.starts_with(".staging");
            let metadata_name =
                Path::new(if sidecar { "sidecar" } else { "cas-root" }).join(&relative);
            if info.is_dir() {
                if is_cas {
                    found.cas_directories.insert(relative);
                } else if is_staging {
                    found.staging_directories.insert(relative);
                } else {
                    found.metadata_directories.insert(metadata_name);
                }
                visit(root, &path, sidecar, found);
                continue;
            }
            assert!(
                info.is_file(),
                "unexpected special file: {}",
                path.display()
            );
            if is_cas {
                assert!(found.cas.insert(relative, info.len()).is_none());
            } else if is_staging {
                assert!(found.staging.insert(relative, info.len()).is_none());
            } else {
                let bytes = fs::read(&path).unwrap();
                assert_eq!(bytes.len() as u64, info.len());
                assert!(found.metadata.insert(metadata_name, bytes).is_none());
            }
        }
    }
    assert!(blob_root.is_dir() && sidecar_root.is_dir());
    assert_ne!(blob_root, sidecar_root);
    assert!(
        !sidecar_root.starts_with(blob_root),
        "control must be inventoried separately from CAS"
    );
    let mut found = RootFootprint::default();
    found.metadata_directories.insert(PathBuf::from("sidecar"));
    visit(blob_root, blob_root, false, &mut found);
    visit(sidecar_root, sidecar_root, true, &mut found);
    found
}

#[test]
fn real_hundred_crash_recover_cycles_do_not_accumulate_cas_or_staging_media() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_hundred_crash_recover_cycles_do_not_accumulate_cas_or_staging_media";
    let fixture = Fixture::new();
    let blob_root = fixture.case.blob_root.canonicalize().unwrap();
    let sidecar_root = cas_retention::test_metadata_root(&blob_root)
        .unwrap()
        .canonicalize()
        .unwrap();
    let test_root = fixture.config.parent().unwrap().canonicalize().unwrap();
    assert!(
        sidecar_root.starts_with(&test_root),
        "sidecar escaped isolated test data"
    );
    let initial = root_footprint(&blob_root, &sidecar_root);
    assert!(initial.cas.is_empty() && initial.staging.is_empty());
    assert!(
        initial
            .metadata
            .contains_key(&Path::new("sidecar").join("catalog.json")),
        "actual control catalog was not inventoried"
    );
    assert!(
        initial
            .metadata
            .contains_key(&Path::new("sidecar").join("root.lock")),
        "actual root lock was not inventoried"
    );
    assert!(
        initial
            .metadata_directories
            .contains(&Path::new("sidecar").join("locks")),
        "actual intent-lock directory was not inventoried"
    );
    let allowed_cas_directories = BTreeSet::from([
        PathBuf::from("sha256"),
        Path::new("sha256").join(&fixture.case.digest[..2]),
    ]);
    let allowed_staging_directories = BTreeSet::from([PathBuf::from(".staging")]);
    let mut measurements = Vec::with_capacity(100);
    let mut last_counts = initial.counts();
    let mut max_metadata_names = last_counts.metadata_names;
    let mut max_metadata_directories = last_counts.metadata_directories;
    let mut max_metadata_bytes = last_counts.metadata_bytes;
    println!(
        "CAS_RETENTION_FOOTPRINT_INITIAL:{}",
        serde_json::to_string(&last_counts).unwrap()
    );
    for cycle in 0..100 {
        // Each completed recovery proves NotApplied before the next intentional
        // import. An UNKNOWN operation is never blindly replayed.
        spawn_actor(
            &fixture,
            TEST,
            &format!("cycle-{cycle:03}-writer"),
            "writer",
            ActorOptions {
                phase: "import_before_commit",
                action: "exit",
                ..ActorOptions::default()
            },
        )
        .finish(EXIT_AT_CHECKPOINT);
        let interrupted = root_footprint(&blob_root, &sidecar_root);
        let interrupted_counts = interrupted.counts();
        assert_eq!(
            interrupted.cas.len(),
            1,
            "cycle {cycle}: expected one new CAS publication"
        );
        assert_eq!(interrupted_counts.cas_bytes, fixture.case.size_bytes);
        assert_eq!(
            physical_blob(&fixture.case).as_deref(),
            Some(fixture.payload.as_slice())
        );
        let recovered = recover(
            &fixture,
            TEST,
            &format!("cycle-{cycle:03}-gc"),
            ActorOptions::default(),
        );
        let report = recovered.recovery.as_ref().unwrap();
        assert!(report.root_managed && !report.truncated, "cycle {cycle}");
        assert_eq!(report.removed, 1, "cycle {cycle}");
        assert_eq!(report.unresolved, 0, "cycle {cycle}");
        fixture.assert_not_applied(&recovered);
        assert!(
            recovered.blob.is_none(),
            "cycle {cycle}: CAS name survived recovery"
        );
        let current_sidecar = cas_retention::test_metadata_root(&blob_root)
            .unwrap()
            .canonicalize()
            .unwrap();
        assert_eq!(
            current_sidecar, sidecar_root,
            "cycle {cycle}: control root changed"
        );
        let cleaned = root_footprint(&blob_root, &sidecar_root);
        last_counts = cleaned.counts();
        max_metadata_names = max_metadata_names
            .max(interrupted_counts.metadata_names)
            .max(last_counts.metadata_names);
        max_metadata_directories = max_metadata_directories
            .max(interrupted_counts.metadata_directories)
            .max(last_counts.metadata_directories);
        max_metadata_bytes = max_metadata_bytes
            .max(interrupted_counts.metadata_bytes)
            .max(last_counts.metadata_bytes);
        let measurement = serde_json::json!({
            "cycle": cycle + 1,
            "after_crash": interrupted_counts,
            "after_recovery": last_counts,
        });
        println!("CAS_RETENTION_FOOTPRINT_CYCLE:{measurement}");
        measurements.push(measurement);
        assert!(
            cleaned.cas.is_empty(),
            "cycle {cycle}: CAS names remained: {:?}",
            cleaned.cas
        );
        assert!(
            cleaned.staging.is_empty(),
            "cycle {cycle}: owned staging media remained: {:?}",
            cleaned.staging
        );
        assert!(
            cleaned.cas_directories.is_subset(&allowed_cas_directories),
            "cycle {cycle}: CAS directories accumulated: {:?}",
            cleaned.cas_directories
        );
        assert!(
            cleaned
                .staging_directories
                .is_subset(&allowed_staging_directories),
            "cycle {cycle}: staging directories accumulated: {:?}",
            cleaned.staging_directories
        );
        assert_eq!(
            cleaned.metadata_directories, initial.metadata_directories,
            "cycle {cycle}: metadata directories accumulated or disappeared"
        );
        assert_eq!(
            cleaned.metadata, initial.metadata,
            "cycle {cycle}: control files, bytes, locks or operation history did not return to their initial state"
        );
    }
    fixture.assert_sources_unchanged();
    println!(
        "CAS_RETENTION_FOOTPRINT_SERIES:{}",
        serde_json::json!({
            "completed_cycles": 100,
            "initial": initial.counts(),
            "final": last_counts,
            "max_metadata_names": max_metadata_names,
            "max_metadata_directories": max_metadata_directories,
            "max_metadata_bytes": max_metadata_bytes,
            "measurements": measurements,
        })
    );
}

#[test]
fn real_replaced_live_intent_lock_keeps_unknown_and_preserves_foreign_lock() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_replaced_live_intent_lock_keeps_unknown_and_preserves_foreign_lock";
    let fixture = Fixture::new();
    // Admission must finish before the live writer owns SQLite's writer lock.
    let mut collector =
        Store::open_with_managed_blob_root(&fixture.case.database, &fixture.case.blob_root)
            .unwrap();
    let control = cas_retention::test_metadata_root(&fixture.case.blob_root).unwrap();
    let mut writer = spawn_actor(
        &fixture,
        TEST,
        "held-writer-lock-substitution",
        "writer",
        ActorOptions {
            phase: "import_after_publication",
            action: "hold",
            ..ActorOptions::default()
        },
    );
    writer.wait_checkpoint();
    writer.assert_alive();
    let catalog_path = control.join("catalog.json");
    let catalog_before = fs::read(&catalog_path).unwrap();
    let envelope: serde_json::Value = serde_json::from_slice(&catalog_before).unwrap();
    let intents = envelope["catalog"]["intents"].as_object().unwrap();
    assert_eq!(intents.len(), 1, "fixture must have one live import intent");
    let (intent_name, intent) = intents.iter().next().unwrap();
    assert_eq!(
        intent["project_id"].as_str().unwrap(),
        fixture.case.project_id.to_string()
    );
    let intent_id = Uuid::parse_str(intent_name).unwrap();
    let lock_path = control.join("locks").join(format!("{intent_id}.lock"));
    let displaced = fixture.config.with_file_name("displaced-live-intent.lock");
    let original_identity = cas_retention::test_file_identity(&lock_path).unwrap();
    let original_len = fs::metadata(&lock_path).unwrap().len();
    fs::rename(&lock_path, &displaced).unwrap();
    assert_eq!(
        cas_retention::test_file_identity(&displaced).unwrap(),
        original_identity
    );
    let replacement = File::create_new(&lock_path).unwrap();
    replacement.sync_all().unwrap();
    let replacement_identity = cas_retention::test_file_identity(&lock_path).unwrap();
    assert_ne!(
        replacement_identity, original_identity,
        "lock substitution did not change the physical file"
    );
    assert!(fs::read(&lock_path).unwrap().is_empty());

    let report = collector
        .recover_cas(CasRetentionPolicy {
            retention: Duration::ZERO,
            max_objects: 64,
        })
        .unwrap();
    assert!(report.root_managed && !report.truncated);
    assert_eq!(
        report.removed, 0,
        "a substituted lock authorized CAS deletion"
    );
    assert!(
        report.unresolved >= 1,
        "lock identity mismatch was not kept UNKNOWN"
    );
    assert_eq!(
        physical_blob(&fixture.case).as_deref(),
        Some(fixture.payload.as_slice())
    );
    assert_eq!(
        fs::read(&catalog_path).unwrap(),
        catalog_before,
        "UNKNOWN intent was retired or rewritten"
    );
    assert_eq!(
        cas_retention::test_file_identity(&lock_path).unwrap(),
        replacement_identity,
        "GC deleted or replaced the foreign lock file"
    );
    assert!(fs::read(&lock_path).unwrap().is_empty());
    writer.assert_alive();
    writer.resume();
    let committed = writer.finish(0).unwrap();
    fixture.assert_imported(&committed);
    assert_eq!(
        cas_retention::test_file_identity(&lock_path).unwrap(),
        replacement_identity,
        "writer cleanup deleted the foreign lock file"
    );

    let reopened = recover(
        &fixture,
        TEST,
        "gc-after-substituted-lock-commit",
        ActorOptions::default(),
    );
    assert_eq!(reopened.recovery.as_ref().unwrap().removed, 0);
    fixture.assert_imported(&reopened);
    assert_eq!(reopened.rows, committed.rows);
    assert_eq!(
        cas_retention::test_file_identity(&lock_path).unwrap(),
        replacement_identity,
        "later recovery deleted or recreated the foreign lock file"
    );
    assert!(fs::read(&lock_path).unwrap().is_empty());
    assert_eq!(
        cas_retention::test_file_identity(&displaced).unwrap(),
        original_identity
    );
    assert_eq!(fs::metadata(&displaced).unwrap().len(), original_len);
    fixture.assert_sources_unchanged();
    drop(replacement);
}

#[cfg(unix)]
#[derive(Debug, Serialize, Deserialize)]
struct FifoRejection {
    project_id: Uuid,
    mode: String,
    error: String,
    blob: Vec<u8>,
}

#[cfg(unix)]
fn run_fifo_child_role() -> bool {
    let mode = match std::env::var(CHILD_MODE) {
        Ok(mode) if mode == "reject_fifo_anchor" || mode == "reject_fifo_catalog" => mode,
        _ => return false,
    };
    let case: RetentionCase =
        serde_json::from_slice(&fs::read(std::env::var_os(CASE_PATH).unwrap()).unwrap()).unwrap();
    assert_eq!(
        Uuid::parse_str(&std::env::var(PROJECT).unwrap()).unwrap(),
        case.project_id
    );
    assert_eq!(
        PathBuf::from(std::env::var_os(TARGET_ROOT).unwrap())
            .canonicalize()
            .unwrap(),
        case.blob_root.canonicalize().unwrap(),
    );
    assert_eq!(
        load_bundle_manifest(&case.bundle)
            .unwrap()
            .backup
            .project
            .id,
        case.project_id
    );
    let result = Store::open_with_managed_blob_root(&case.database, &case.blob_root).and_then(
        |mut store| {
            store.recover_cas(CasRetentionPolicy {
                retention: Duration::ZERO,
                max_objects: 64,
            })
        },
    );
    let error = match result {
        Err(StorageError::InvalidBackup(message))
            if message == "CAS retention: control entry has an unexpected filesystem type" =>
        {
            message
        }
        Err(error) => {
            panic!("FIFO returned a different error instead of rejecting its type: {error}")
        }
        Ok(_) => panic!("FIFO control entry was accepted"),
    };
    let seen = FifoRejection {
        project_id: case.project_id,
        mode: mode.clone(),
        error,
        blob: physical_blob(&case).expect("FIFO rejection lost committed CAS bytes"),
    };
    serde_json::to_writer(
        File::create_new(std::env::var_os(OBSERVATION).unwrap()).unwrap(),
        &seen,
    )
    .unwrap();
    println!("CAS_RETENTION_FIFO_REJECTED:{}:{mode}", case.project_id);
    std::io::stdout().flush().unwrap();
    true
}

#[cfg(unix)]
impl Actor {
    fn finish_fifo(mut self, project_id: Uuid, mode: &str) -> FifoRejection {
        let deadline = Instant::now() + Duration::from_secs(5);
        let status = loop {
            if let Some(status) = self.child.as_mut().unwrap().try_wait().unwrap() {
                break status;
            }
            assert!(
                Instant::now() < deadline,
                "FIFO probe blocked beyond five seconds; stderr: {}",
                fs::read_to_string(&self.stderr).unwrap()
            );
            thread::sleep(Duration::from_millis(25));
        };
        self.child.take();
        self.reader.take().unwrap().join().unwrap();
        self.stdout.extend(self.lines.try_iter());
        let stdout = self.stdout.join("\n");
        let stderr = fs::read_to_string(&self.stderr).unwrap();
        assert_eq!(status.code(), Some(0), "{stdout}\n{stderr}");
        assert!(
            stdout.contains(&format!("CAS_RETENTION_FIFO_REJECTED:{project_id}:{mode}")),
            "{stdout}\n{stderr}"
        );
        let seen: FifoRejection =
            serde_json::from_slice(&fs::read(&self.observation).unwrap()).unwrap();
        assert_eq!(seen.project_id, project_id);
        assert_eq!(seen.mode, mode);
        assert_eq!(
            seen.error,
            "CAS retention: control entry has an unexpected filesystem type"
        );
        seen
    }
}

#[cfg(unix)]
#[test]
fn real_unix_fifo_control_entries_reject_without_blocking() {
    if run_fifo_child_role() {
        return;
    }
    if run_child_role() {
        return;
    }
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    const TEST: &str = "real_unix_fifo_control_entries_reject_without_blocking";
    for entry in ["anchor", "catalog"] {
        let fixture = Fixture::new();
        let committed = spawn_actor(
            &fixture,
            TEST,
            "fifo-baseline-writer",
            "writer",
            ActorOptions::default(),
        )
        .finish(0)
        .unwrap();
        fixture.assert_imported(&committed);
        let control = cas_retention::test_metadata_root(&fixture.case.blob_root).unwrap();
        let path = if entry == "anchor" {
            fixture
                .case
                .blob_root
                .join(".motionwright-cas-root-v1.json")
        } else {
            control.join("catalog.json")
        };
        let saved = fixture
            .config
            .with_file_name(format!("saved-{entry}-before-fifo.json"));
        let original_bytes = fs::read(&path).unwrap();
        let original_identity = cas_retention::test_file_identity(&path).unwrap();
        fs::rename(&path, &saved).unwrap();
        assert_eq!(
            cas_retention::test_file_identity(&saved).unwrap(),
            original_identity
        );
        let created = Command::new("mkfifo")
            .arg(&path)
            .stdin(Stdio::null())
            .output()
            .expect("mkfifo must be available on the Unix test runner");
        assert!(
            created.status.success(),
            "mkfifo failed: {}",
            String::from_utf8_lossy(&created.stderr)
        );
        let fifo_before = fs::symlink_metadata(&path).unwrap();
        assert!(fifo_before.file_type().is_fifo());
        let fifo_identity = (fifo_before.dev(), fifo_before.ino());
        let mode = format!("reject_fifo_{entry}");
        let rejected = spawn_actor(
            &fixture,
            TEST,
            "fifo-rejection-probe",
            &mode,
            ActorOptions::default(),
        )
        .finish_fifo(fixture.case.project_id, &mode);
        assert_eq!(rejected.blob, fixture.payload);
        let fifo_after = fs::symlink_metadata(&path).unwrap();
        assert!(
            fifo_after.file_type().is_fifo(),
            "rejection replaced the FIFO"
        );
        assert_eq!(
            (fifo_after.dev(), fifo_after.ino()),
            fifo_identity,
            "rejection unlinked or recreated the FIFO"
        );
        assert_eq!(
            physical_blob(&fixture.case).as_deref(),
            Some(fixture.payload.as_slice())
        );
        assert_eq!(fs::read(&saved).unwrap(), original_bytes);
        assert_eq!(
            cas_retention::test_file_identity(&saved).unwrap(),
            original_identity
        );

        fs::remove_file(&path).unwrap();
        fs::rename(&saved, &path).unwrap();
        assert_eq!(
            cas_retention::test_file_identity(&path).unwrap(),
            original_identity
        );
        let reopened = observe(&fixture, TEST, "fifo-restored-observer", false);
        fixture.assert_imported(&reopened);
        assert_eq!(
            reopened.rows, committed.rows,
            "FIFO rejection changed SQLite state"
        );
        fixture.assert_sources_unchanged();
    }
}

#[test]
fn real_budget_one_reopens_reclaim_orphan_behind_live_capture() {
    if run_child_role() {
        return;
    }
    const TEST: &str = "real_budget_one_reopens_reclaim_orphan_behind_live_capture";
    let fixture = Fixture::new();
    let control = cas_retention::test_metadata_root(&fixture.case.blob_root).unwrap();
    let catalog_path = control.join("catalog.json");
    let retained_source = fixture.config.with_file_name("retained-prefix-source.bin");
    let retained_bytes = b"Retained bundle capture before orphan recovery\n";
    fs::write(&retained_source, retained_bytes).unwrap();
    let retained_store =
        Store::open_with_managed_blob_root(&fixture.case.peer_database, &fixture.case.blob_root)
            .unwrap();
    // Use the actual bundle capture path. Legacy captures are deliberately
    // excluded from GC and would not demonstrate cursor progress.
    let retained = retained_store
        .stage_bundle_file_import_with_limit(&retained_source, retained_bytes.len() as u64)
        .unwrap();
    assert_ne!(
        retained.descriptor().sha256.as_str(),
        fixture.case.digest.as_str()
    );
    let retained_name = retained
        .path()
        .file_name()
        .unwrap()
        .to_str()
        .unwrap()
        .to_owned();
    let retained_identity = cas_retention::test_file_identity(retained.path()).unwrap();
    let retained_reader = File::open(retained.path()).unwrap();
    let before_catalog = fs::read(&catalog_path).unwrap();
    let before_envelope: serde_json::Value = serde_json::from_slice(&before_catalog).unwrap();
    let before_captures = before_envelope["catalog"]["captures"].as_object().unwrap();
    assert_eq!(before_captures.len(), 1);
    let (retained_id_text, retained_record) = before_captures.iter().next().unwrap();
    let retained_id = Uuid::parse_str(retained_id_text).unwrap();
    assert_eq!(retained_record["purpose"].as_str(), Some("bundle_import"));
    assert_eq!(
        retained_record["name"].as_str(),
        Some(retained_name.as_str())
    );
    let retained_lock = control.join("locks").join(format!("{retained_id}.lock"));
    let retained_lock_identity = cas_retention::test_file_identity(&retained_lock).unwrap();
    let before_lock_names: BTreeSet<_> = fs::read_dir(control.join("locks"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    assert_eq!(before_lock_names.len(), 1);

    crash_writer(&fixture, TEST, "import_before_commit");
    let interrupted = observe(&fixture, TEST, "budget-one-before-recovery", false);
    fixture.assert_not_applied(&interrupted);
    assert_eq!(
        interrupted.blob.as_deref(),
        Some(fixture.payload.as_slice())
    );
    let initial_envelope: serde_json::Value =
        serde_json::from_slice(&fs::read(&catalog_path).unwrap()).unwrap();
    let catalog = &initial_envelope["catalog"];
    let captures = catalog["captures"].as_object().unwrap();
    let mut capture_ids: Vec<Uuid> = captures
        .keys()
        .map(|id| Uuid::parse_str(id).unwrap())
        .collect();
    capture_ids.sort();
    assert_eq!(
        capture_ids.first(),
        Some(&retained_id),
        "live capture is not the actual cursor prefix"
    );
    assert_eq!(
        captures.get(retained_id_text.as_str()).unwrap()["purpose"].as_str(),
        Some("bundle_import")
    );
    let publications = catalog["publications"].as_object().unwrap();
    assert_eq!(publications.len(), 1);
    assert!(publications.contains_key(&fixture.case.digest));
    let intents = catalog["intents"].as_object().unwrap();
    assert_eq!(intents.len(), 1);
    assert_eq!(
        intents.values().next().unwrap()["project_id"]
            .as_str()
            .unwrap(),
        fixture.case.project_id.to_string()
    );
    let retired = catalog["retired_locks"].as_object().unwrap();
    let initial_candidates = captures.len() + publications.len() + intents.len() + retired.len();
    let possible_new_retirements = captures.len() + intents.len();
    let passes = 2 * (initial_candidates + possible_new_retirements);
    let mut total_removed = 0;
    for pass in 0..passes {
        let seen = recover(
            &fixture,
            TEST,
            &format!("budget-one-pass-{pass:02}"),
            ActorOptions {
                max_objects: Some(1),
                ..ActorOptions::default()
            },
        );
        let report = seen.recovery.as_ref().unwrap();
        assert!(report.root_managed);
        assert!(
            report.inspected <= 1,
            "pass {pass}: recovery exceeded its one-object budget"
        );
        total_removed += report.removed;
        if pass == 0 {
            assert_eq!(report.inspected, 1);
            assert_eq!(
                report.removed, 0,
                "first pass bypassed the retained candidate prefix"
            );
            assert!(seen.blob.is_some());
        }
        fixture.assert_not_applied(&seen);
        assert_eq!(
            fs::read(retained.path()).unwrap().as_slice(),
            retained_bytes.as_slice(),
            "pass {pass}: live capture bytes changed"
        );
        assert_eq!(
            cas_retention::test_file_identity(retained.path()).unwrap(),
            retained_identity,
            "pass {pass}: live capture was unlinked or replaced"
        );
        assert_eq!(
            cas_retention::test_file_identity(&retained_lock).unwrap(),
            retained_lock_identity,
            "pass {pass}: live capture lock changed"
        );
        println!(
            "CAS_RETENTION_BUDGET_PASS:{}",
            serde_json::json!({
                "pass": pass + 1,
                "limit": passes,
                "initial_candidates": initial_candidates,
                "possible_new_retirements": possible_new_retirements,
                "inspected": report.inspected,
                "removed": report.removed,
                "unresolved": report.unresolved,
                "truncated": report.truncated,
            })
        );
    }
    assert_eq!(
        total_removed, 1,
        "bounded reopen sequence did not reclaim the orphan exactly once"
    );
    assert!(physical_blob(&fixture.case).is_none());
    let remaining_stage_names: BTreeSet<_> = fs::read_dir(fixture.case.blob_root.join(".staging"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    assert_eq!(
        remaining_stage_names,
        BTreeSet::from([retained.path().file_name().unwrap().to_os_string()]),
        "orphan staging entries survived the finite recovery sequence"
    );
    let after_lock_names: BTreeSet<_> = fs::read_dir(control.join("locks"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    assert_eq!(
        after_lock_names, before_lock_names,
        "retired operation locks accumulated"
    );
    assert_eq!(
        fs::read(&catalog_path).unwrap(),
        before_catalog,
        "orphan metadata or recovery cursor did not return to the retained-prefix baseline"
    );
    assert_eq!(
        fs::read(&retained_source).unwrap().as_slice(),
        retained_bytes.as_slice()
    );
    fixture.assert_sources_unchanged();
    drop(retained_reader);
    drop(retained);
    drop(retained_store);
}
