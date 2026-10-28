//! Terminate real Store::apply calls, then inspect recovery in a new process.
//! The checkpoints exist only in the storage test binary.
#![cfg(test)]

use super::*;
use motionwright_domain::SourceFrameWindow;
use std::{
    process::{Command, ExitStatus, Stdio},
    thread,
    time::{Duration, Instant},
};

const CHILD_MODE: &str = "MOTIONWRIGHT_TEST_APPLY_CHILD";
const CASE_PATH: &str = "MOTIONWRIGHT_TEST_APPLY_CASE";
const CHECKPOINT: &str = "MOTIONWRIGHT_TEST_APPLY_CHECKPOINT";
const REQUEST: &str = "MOTIONWRIGHT_TEST_APPLY_REQUEST";
const SNAPSHOT_PATH: &str = "MOTIONWRIGHT_TEST_APPLY_SNAPSHOT";
const EXIT_AT_CHECKPOINT: i32 = 86;

const EVIDENCE_ROOT: &str = "MOTIONWRIGHT_TEST_APPLY_EVIDENCE_ROOT";
const EVIDENCE_PHASES: [&str; 5] = ["before", "interrupted", "cleaned", "after_retry", "final"];

struct Evidence {
    directory: PathBuf,
    test_name: String,
    checkpoint: String,
    captured: Vec<String>,
    errors: Vec<String>,
    complete: bool,
    published: bool,
}

impl Evidence {
    fn begin(name: &str, phase: &str) -> Option<Self> {
        let requested = PathBuf::from(std::env::var_os(EVIDENCE_ROOT)?);
        assert!(
            requested.is_absolute(),
            "evidence container must be absolute"
        );
        let root = requested
            .canonicalize()
            .expect("existing evidence container");
        assert!(root.is_dir(), "evidence container must be a directory");
        for ancestor in root.ancestors() {
            let git = ancestor.join(".git");
            if git.exists() {
                assert!(
                    git.is_dir() && root.starts_with(ancestor.join("knowledge").join("runtime")),
                    "evidence must be outside checkouts and worktrees"
                );
            }
        }
        let directory = root.join(phase);
        fs::create_dir(&directory).expect("evidence destination must be new");
        Some(Self {
            directory,
            test_name: name.to_owned(),
            checkpoint: phase.to_owned(),
            captured: Vec::new(),
            errors: Vec::new(),
            complete: false,
            published: false,
        })
    }

    fn fail(&mut self, message: String) -> ! {
        self.errors.push(message.clone());
        panic!("{message}");
    }

    fn bytes(&mut self, name: &str, bytes: &[u8]) {
        if let Err(error) =
            File::create_new(self.directory.join(name)).and_then(|mut file| file.write_all(bytes))
        {
            self.fail(format!("cannot retain {name}: {error}"));
        }
    }

    fn json(&mut self, name: &str, value: &impl Serialize) {
        match serde_json::to_vec_pretty(value) {
            Ok(bytes) => self.bytes(name, &bytes),
            Err(error) => self.fail(format!("cannot serialize {name}: {error}")),
        }
    }

    fn original(&mut self, name: &str, source: &Path) {
        match fs::read(source) {
            Ok(bytes) => self.bytes(name, &bytes),
            Err(error) => self.fail(format!("original {name} unavailable: {error}")),
        }
    }

    fn phase(&mut self, name: &str, snapshot: &Snapshot) {
        self.json(&format!("{name}.json"), snapshot);
        self.captured.push(name.to_owned());
    }

    fn publish(&mut self) -> std::io::Result<()> {
        self.published = true;
        let mut paths = fs::read_dir(&self.directory)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<std::io::Result<Vec<_>>>()?;
        paths.sort();
        let mut errors = self.errors.clone();
        let files = paths
            .into_iter()
            .map(|path| {
                let name = path.file_name().unwrap().to_string_lossy().into_owned();
                match fs::read(&path) {
                    Ok(bytes) => serde_json::json!({
                        "file": name,
                        "bytes": bytes.len(),
                        "sha256": hex::encode(Sha256::digest(&bytes)),
                    }),
                    Err(error) => {
                        let message = format!("cannot inventory {name}: {error}");
                        errors.push(message.clone());
                        serde_json::json!({"file": name, "unavailable": message})
                    }
                }
            })
            .collect::<Vec<_>>();
        let phases = EVIDENCE_PHASES
            .iter()
            .map(|name| {
                let captured = self.captured.iter().any(|value| value.as_str() == *name);
                serde_json::json!({
                    "phase": name,
                    "status": if captured { "captured" } else { "unavailable" },
                    "reason": if captured { None } else {
                        Some("Execution did not complete this capture; consult original parent output")
                    },
                })
            })
            .collect::<Vec<_>>();
        let unwinding = thread::panicking();
        let complete = self.complete
            && !unwinding
            && errors.is_empty()
            && self.captured.len() == EVIDENCE_PHASES.len();
        let manifest = serde_json::json!({
            "schema": "motionwright.apply-phase-custody/1",
            "test_name": self.test_name,
            "checkpoint": self.checkpoint,
            "status": if complete {
                "complete"
            } else {
                "incomplete"
            },
            "parent_unwinding": unwinding,
            "phases": phases,
            "files": files,
            "errors": errors,
            "case_identity": if self.directory.join("case.json").is_file() {
                Some("case.json")
            } else {
                None
            },
            "scope": "Observed SQL fields, manifest and immutable blob bytes; no raw database or full CAS archive",
            "physical_power_loss": "NOT_RUN",
        });
        serde_json::to_writer_pretty(
            File::create_new(self.directory.join("custody.json"))?,
            &manifest,
        )
        .map_err(std::io::Error::other)?;
        if self.complete && !complete {
            return Err(std::io::Error::other("phase evidence is incomplete"));
        }
        Ok(())
    }

    fn finish(&mut self) {
        self.complete = true;
        self.publish().expect("complete phase evidence manifest");
    }
}

impl Drop for Evidence {
    fn drop(&mut self) {
        if !self.published
            && let Err(error) = self.publish()
        {
            eprintln!("apply evidence manifest unavailable: {error}");
        }
    }
}

fn retain_phase(evidence: &mut Option<Evidence>, name: &str, snapshot: &Snapshot) {
    if let Some(evidence) = evidence {
        evidence.phase(name, snapshot);
    }
}

#[derive(Serialize, Deserialize)]
struct CrashCase {
    database: PathBuf,
    manifest: PathBuf,
    blob: PathBuf,
    original: Project,
    request_id: String,
}

#[derive(Debug, PartialEq, Serialize, Deserialize)]
struct Snapshot {
    project: Vec<String>,
    events: Vec<Vec<String>>,
    receipts: Vec<Vec<String>>,
    registrations: Vec<Vec<String>>,
    evictions: Vec<Vec<String>>,
    manifest: Option<Vec<u8>>,
    immutable_blob: Vec<u8>,
}

pub(super) fn checkpoint(request_id: &str, phase: &str) {
    concurrent_checkpoint(request_id, phase);
    if std::env::var(CHILD_MODE).as_deref() == Ok("writer")
        && std::env::var(CHECKPOINT).as_deref() == Ok(phase)
        && std::env::var(REQUEST).as_deref() == Ok(request_id)
    {
        println!("APPLY_CHECKPOINT:{request_id}:{phase}");
        std::io::stdout().flush().unwrap();
        // Bypass Rust/SQLite destructors. This models process termination,
        // not physical power loss or loss of acknowledged filesystem writes.
        std::process::exit(EXIT_AT_CHECKPOINT);
    }
}

fn change() -> Change {
    Change::RenameProject {
        title: "Committed interrupted project".into(),
    }
}

fn rows(conn: &Connection, sql: &str, id: Uuid) -> Vec<Vec<String>> {
    let mut statement = conn.prepare(sql).unwrap();
    let count = statement.column_count();
    statement
        .query_map([id.to_string()], |row| {
            (0..count).map(|index| row.get(index)).collect()
        })
        .unwrap()
        .collect::<std::result::Result<_, _>>()
        .unwrap()
}

fn snapshot(case: &CrashCase) -> Snapshot {
    // Let SQLite recover its own journal without running Store::open, which
    // would also drain the application's durable eviction outbox.
    let conn =
        Connection::open_with_flags(&case.database, OpenFlags::SQLITE_OPEN_READ_WRITE).unwrap();
    conn.pragma_update(None, "query_only", true).unwrap();
    assert_eq!(
        conn.query_row("PRAGMA integrity_check", [], |row| row.get::<_, String>(0))
            .unwrap(),
        "ok"
    );
    let id = case.original.id;
    let project = rows(
        &conn,
        "SELECT generation,CAST(revision AS TEXT),title,document_json
         FROM projects WHERE id=?1",
        id,
    );
    assert_eq!(project.len(), 1);
    let registered = "SELECT generation,CAST(revision AS TEXT),deliverable_id,
                      CAST(source_start AS TEXT),CAST(source_end AS TEXT),fingerprint_sha256";
    Snapshot {
        project: project.into_iter().next().unwrap(),
        events: rows(
            &conn,
            "SELECT CAST(revision AS TEXT),kind,payload_json,created_at
             FROM events WHERE project_id=?1 ORDER BY revision",
            id,
        ),
        receipts: rows(
            &conn,
            "SELECT request_id,request_sha256,result_json,created_at,
                    CAST(apply_binding_version AS TEXT),COALESCE(apply_binding_json,'')
             FROM receipts WHERE project_id=?1 ORDER BY request_id",
            id,
        ),
        registrations: rows(
            &conn,
            &format!("{registered} FROM render_window_cache_entries WHERE project_id=?1"),
            id,
        ),
        evictions: rows(
            &conn,
            &format!("{registered} FROM render_window_cache_evictions WHERE project_id=?1"),
            id,
        ),
        manifest: match fs::read(&case.manifest) {
            Ok(bytes) => Some(bytes),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => panic!("cannot inspect fixture manifest: {error}"),
        },
        immutable_blob: fs::read(&case.blob).unwrap(),
    }
}

fn run_child(
    config: &Path,
    case: &CrashCase,
    name: &str,
    phase: &str,
    mode: &str,
    output: Option<&Path>,
    evidence: &mut Option<Evidence>,
) -> (ExitStatus, String) {
    let root = config.parent().unwrap();
    let call_id = Uuid::now_v7();
    let stdout_path = root.join(format!("{call_id}.stdout"));
    let stderr_path = root.join(format!("{call_id}.stderr"));
    let mut command = Command::new(std::env::current_exe().unwrap());
    command
        .args([
            "--exact",
            &format!("apply_crash_tests::{name}"),
            "--nocapture",
        ])
        .env(CHILD_MODE, mode)
        .env(CASE_PATH, config)
        .env(CHECKPOINT, phase)
        .env(REQUEST, &case.request_id)
        .env_remove(SNAPSHOT_PATH)
        .env_remove(EVIDENCE_ROOT)
        .stdin(Stdio::null())
        .stdout(File::create_new(&stdout_path).unwrap())
        .stderr(File::create_new(&stderr_path).unwrap());
    if let Some(output) = output {
        command.env(SNAPSHOT_PATH, output);
    }
    let mut retain_terminal =
        |status: Option<&ExitStatus>, timed_out: bool, error: Option<String>| {
            if let Some(evidence) = evidence.as_mut() {
                evidence.json(
                    &format!("{call_id}.process.json"),
                    &serde_json::json!({
                        "test_name": name,
                        "checkpoint": phase,
                        "request_id": case.request_id,
                        "mode": mode,
                        "exit_code": status.and_then(ExitStatus::code),
                        "exit_status": status.map(ToString::to_string),
                        "success": status.map(ExitStatus::success),
                        "timed_out": timed_out,
                        "error": error,
                        "stdout": format!("{call_id}.stdout"),
                        "stderr": format!("{call_id}.stderr"),
                    }),
                );
                evidence.original(&format!("{call_id}.stdout"), &stdout_path);
                evidence.original(&format!("{call_id}.stderr"), &stderr_path);
            }
        };
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            retain_terminal(None, false, Some(format!("spawn failed: {error}")));
            panic!("fixture subprocess could not start: {error}");
        }
    };
    let deadline = Instant::now() + Duration::from_secs(30);
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) => {}
            Err(error) => {
                let killed = child.kill();
                let waited = child.wait();
                retain_terminal(
                    waited.as_ref().ok(),
                    false,
                    Some(format!("wait failed: {error}; kill result: {killed:?}")),
                );
                panic!("cannot observe owned fixture subprocess: {error}");
            }
        }
        if Instant::now() >= deadline {
            let killed = child.kill();
            let waited = child.wait();
            retain_terminal(
                waited.as_ref().ok(),
                true,
                Some(format!(
                    "deadline exceeded; kill: {killed:?}; wait: {waited:?}"
                )),
            );
            panic!(
                "owned fixture subprocess timed out: {}",
                fs::read_to_string(&stderr_path).unwrap()
            );
        }
        thread::sleep(Duration::from_millis(10));
    };
    retain_terminal(Some(&status), false, None);
    let stdout = fs::read_to_string(stdout_path).unwrap();
    let stderr = fs::read_to_string(stderr_path).unwrap();
    assert!(stderr.is_empty(), "fixture subprocess stderr: {stderr}");
    (status, stdout)
}

fn observe_in_fresh_process(
    root: &Path,
    config: &Path,
    case: &CrashCase,
    name: &str,
    phase: &str,
    evidence: &mut Option<Evidence>,
) -> Snapshot {
    let output = root.join(format!("snapshot-{}.json", Uuid::now_v7()));
    let (status, stdout) = run_child(
        config,
        case,
        name,
        phase,
        "observer",
        Some(&output),
        evidence,
    );
    assert!(status.success(), "observer failed: {stdout}");
    assert!(stdout.contains(&format!("APPLY_OBSERVED:{}:{phase}", case.request_id)));
    serde_json::from_slice(&fs::read(output).unwrap()).unwrap()
}

fn committed_outcome(case: &CrashCase, before: &Snapshot, after: &Snapshot) -> ApplyOutcome {
    let project: Project = serde_json::from_str(&after.project[3]).unwrap();
    assert_eq!(project.id, case.original.id);
    assert_eq!(project.generation, case.original.generation);
    assert_eq!(project.revision, case.original.revision + 1);
    assert_eq!(project.title, "Committed interrupted project");
    assert_eq!(after.project[0], project.generation.to_string());
    assert_eq!(after.project[1], project.revision.to_string());
    assert_eq!(after.project[2], project.title);
    assert_eq!(after.events.len(), before.events.len() + 1);
    assert_eq!(&after.events[..before.events.len()], &before.events);
    let event = after.events.last().unwrap();
    assert_eq!(event[0], project.revision.to_string());
    assert_eq!(event[1], "change");
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&event[2]).unwrap(),
        serde_json::to_value(change()).unwrap()
    );
    assert_eq!(after.receipts.len(), before.receipts.len() + 1);
    for receipt in &before.receipts {
        assert!(after.receipts.contains(receipt));
    }
    let receipt = after
        .receipts
        .iter()
        .find(|r| r[0] == case.request_id)
        .unwrap();
    assert_eq!(receipt[1], request_digest(project.id, &change()).unwrap());
    let outcome: ApplyOutcome = serde_json::from_str(&receipt[2]).unwrap();
    assert_eq!(outcome.project, project);
    assert_eq!(outcome.previous_revision, case.original.revision);
    assert_eq!(outcome.request_id, case.request_id);
    assert!(!outcome.replayed);
    assert_eq!(receipt.len(), 6);
    assert_eq!(
        receipt[4], "1",
        "committed result must publish its V1 marker"
    );
    let binding: VerifiedApplyReceiptV1 = serde_json::from_str(&receipt[5]).unwrap();
    assert_eq!(binding.format, "motionwright.apply-binding/1");
    assert_eq!(binding.project_id, case.original.id.to_string());
    assert_eq!(binding.generation, case.original.generation.to_string());
    assert_eq!(binding.resource, case.original.resource_key());
    assert_eq!(binding.request_id, case.request_id);
    assert_eq!(binding.request_sha256, receipt[1]);
    assert_eq!(binding.base_revision, case.original.revision.to_string());
    assert_eq!(binding.base_branch, case.original.active_branch.to_string());
    assert_eq!(
        binding.result_revision,
        outcome.project.revision.to_string()
    );
    assert_eq!(
        binding.result_branch,
        outcome.project.active_branch.to_string()
    );
    assert_eq!(
        binding.result_sha256,
        hex::encode(Sha256::digest(receipt[2].as_bytes()))
    );
    outcome
}

fn exercise(name: &str, phase: &str) {
    if let Ok(mode) = std::env::var(CHILD_MODE) {
        let case: CrashCase =
            serde_json::from_slice(&fs::read(std::env::var_os(CASE_PATH).unwrap()).unwrap())
                .unwrap();
        assert_eq!(std::env::var(CHECKPOINT).unwrap(), phase);
        assert_eq!(std::env::var(REQUEST).unwrap(), case.request_id);
        match mode.as_str() {
            "writer" => {
                let mut store = Store::open(&case.database).unwrap();
                store
                    .apply(
                        case.original.id,
                        &case.original.stamp(),
                        &case.request_id,
                        &change(),
                    )
                    .unwrap();
                panic!("real apply did not reach the requested checkpoint");
            }
            "observer" => {
                let output = std::env::var_os(SNAPSHOT_PATH).unwrap();
                serde_json::to_writer(File::create_new(output).unwrap(), &snapshot(&case)).unwrap();
                println!("APPLY_OBSERVED:{}:{phase}", case.request_id);
                return;
            }
            _ => panic!("unexpected fixture subprocess mode"),
        }
    }

    let mut evidence = Evidence::begin(name, phase);
    let temp = tempfile::tempdir().unwrap();
    let root = temp.path().canonicalize().unwrap();
    let database = root.join("project.sqlite3");
    let mut store = Store::open(&database).unwrap();
    let initial = store
        .create_named_project("Before interrupted edit")
        .unwrap();
    let original = store
        .apply(
            initial.id,
            &initial.stamp(),
            "prepare-real-scene",
            &Change::AddScene {
                name: "Source".into(),
                objective: "Durable proxy owner".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let key = RenderWindowCacheKey {
        project_id: original.id,
        expected: original.stamp(),
        deliverable_id: original.deliverables[0].id,
        source_window: SourceFrameWindow { start: 0, end: 60 },
        fingerprint_sha256: hex::encode(Sha256::digest(b"owned source before edit")),
    };
    let input = root.join("source.bin");
    fs::write(&input, b"immutable proxy content survives every crash").unwrap();
    let record = store.put_render_window_proxy(&key, &input, 60).unwrap();
    let blob = store
        .lookup_render_window_proxy(&key)
        .unwrap()
        .unwrap()
        .blob_path;
    let owner = store
        .derived_cache_manifest_path(
            &record.scope_sha256,
            &record.kind,
            &record.fingerprint_sha256,
        )
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    let manifests = fs::read_dir(owner)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect::<Vec<_>>();
    assert_eq!(manifests.len(), 1);
    let case = CrashCase {
        database,
        manifest: manifests[0].clone(),
        blob,
        original,
        request_id: format!("process-termination-{}", Uuid::now_v7()),
    };
    drop(store);
    let config = root.join("case.json");
    serde_json::to_writer(File::create_new(&config).unwrap(), &case).unwrap();
    if let Some(evidence) = evidence.as_mut() {
        evidence.original("case.json", &config);
    }
    let before = snapshot(&case);
    retain_phase(&mut evidence, "before", &before);
    assert_eq!(before.registrations.len(), 1);
    assert!(before.evictions.is_empty());
    assert!(before.manifest.is_some());

    let (status, stdout) = run_child(&config, &case, name, phase, "writer", None, &mut evidence);
    assert_eq!(status.code(), Some(EXIT_AT_CHECKPOINT), "{stdout}");
    assert!(stdout.contains(&format!("APPLY_CHECKPOINT:{}:{phase}", case.request_id)));

    let interrupted = observe_in_fresh_process(&root, &config, &case, name, phase, &mut evidence);
    retain_phase(&mut evidence, "interrupted", &interrupted);
    let committed = phase == "after_commit";
    if committed {
        committed_outcome(&case, &before, &interrupted);
        assert!(interrupted.registrations.is_empty());
        assert_eq!(interrupted.evictions, before.registrations);
        assert_eq!(interrupted.manifest, before.manifest);
        assert_eq!(interrupted.immutable_blob, before.immutable_blob);
    } else {
        // Includes the revision-trigger outbox and cache registration rollback.
        assert_eq!(interrupted, before);
    }

    let mut recovered = Store::open(&case.database).unwrap();
    let cleaned = snapshot(&case);
    retain_phase(&mut evidence, "cleaned", &cleaned);
    if committed {
        assert_eq!(cleaned.project, interrupted.project);
        assert_eq!(cleaned.events, interrupted.events);
        assert_eq!(cleaned.receipts, interrupted.receipts);
        assert!(cleaned.registrations.is_empty());
        assert!(cleaned.evictions.is_empty());
        assert!(cleaned.manifest.is_none());
    } else {
        assert_eq!(cleaned, before);
    }
    assert_eq!(cleaned.immutable_blob, before.immutable_blob);
    let accepted = recovered
        .apply(
            case.original.id,
            &case.original.stamp(),
            &case.request_id,
            &change(),
        )
        .unwrap();
    assert_eq!(accepted.replayed, committed);
    if committed {
        assert_eq!(
            accepted.project,
            committed_outcome(&case, &before, &interrupted).project
        );
    }
    drop(recovered);
    let after_retry = observe_in_fresh_process(&root, &config, &case, name, phase, &mut evidence);
    retain_phase(&mut evidence, "after_retry", &after_retry);
    committed_outcome(&case, &before, &after_retry);
    assert!(after_retry.registrations.is_empty());
    assert!(after_retry.evictions.is_empty());
    assert!(after_retry.manifest.is_none());
    assert_eq!(after_retry.immutable_blob, before.immutable_blob);

    let mut reopened = Store::open(&case.database).unwrap();
    let replay = reopened
        .apply(
            case.original.id,
            &case.original.stamp(),
            &case.request_id,
            &change(),
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, accepted.project);
    drop(reopened);
    let final_snapshot = snapshot(&case);
    retain_phase(&mut evidence, "final", &final_snapshot);
    assert_eq!(final_snapshot, after_retry);
    if let Some(evidence) = evidence.as_mut() {
        evidence.finish();
    }
}

#[test]
fn real_apply_exit_after_project_update_rolls_back_document_and_eviction_outbox() {
    exercise(
        "real_apply_exit_after_project_update_rolls_back_document_and_eviction_outbox",
        "after_project_update",
    );
}

#[test]
fn real_apply_exit_after_event_insert_rolls_back_the_full_mutation() {
    exercise(
        "real_apply_exit_after_event_insert_rolls_back_the_full_mutation",
        "after_event_insert",
    );
}

#[test]
fn real_apply_exit_before_commit_rolls_back_receipt_and_journal() {
    exercise(
        "real_apply_exit_before_commit_rolls_back_receipt_and_journal",
        "before_commit",
    );
}

#[test]
fn real_apply_exit_after_commit_recovers_outbox_and_replays_original_result() {
    exercise(
        "real_apply_exit_after_commit_recovers_outbox_and_replays_original_result",
        "after_commit",
    );
}

#[derive(Debug, PartialEq)]
struct MigrationSnapshot {
    version: u32,
    schema_version: u32,
    schema: Vec<(String, String, Option<String>)>,
    columns: Vec<String>,
    rows: Vec<Vec<Vec<rusqlite::types::Value>>>,
}

fn migration_snapshot(path: &std::path::Path) -> MigrationSnapshot {
    let conn =
        Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let schema = {
        let mut statement = conn
            .prepare("SELECT type,name,sql FROM sqlite_schema ORDER BY type,name")
            .unwrap();
        statement
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    };
    let columns = {
        let mut statement = conn.prepare("PRAGMA main.table_xinfo('receipts')").unwrap();
        statement
            .query_map([], |row| row.get(1))
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    };
    let rows = [
        "SELECT * FROM projects ORDER BY id",
        "SELECT * FROM events ORDER BY project_id,revision",
        "SELECT * FROM receipts ORDER BY project_id,request_id",
    ]
    .into_iter()
    .map(|sql| {
        let mut statement = conn.prepare(sql).unwrap();
        let count = statement.column_count();
        statement
            .query_map([], |row| {
                (0..count)
                    .map(|index| row.get(index))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    })
    .collect();
    MigrationSnapshot {
        version: conn
            .pragma_query_value(None, "user_version", |row| row.get(0))
            .unwrap(),
        schema_version: conn
            .pragma_query_value(None, "schema_version", |row| row.get(0))
            .unwrap(),
        schema,
        columns,
        rows,
    }
}

struct MigrationChild(Option<std::process::Child>);

impl Drop for MigrationChild {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn wait_migration_child(child: std::process::Child) -> std::process::ExitStatus {
    let mut child = MigrationChild(Some(child));
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
    loop {
        if let Some(status) = child.0.as_mut().unwrap().try_wait().unwrap() {
            child.0.take();
            return status;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "migration child timed out"
        );
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
}

#[test]
fn binding_migration_ddl_rolls_back_after_abrupt_exit() {
    const CHILD: &str = "MOTIONWRIGHT_TEST_BINDING_MIGRATION_CHILD";
    const DATABASE: &str = "MOTIONWRIGHT_TEST_BINDING_MIGRATION_DB";
    const PHASE: &str = "MOTIONWRIGHT_TEST_BINDING_MIGRATION_PHASE";
    if std::env::var(CHILD).as_deref() == Ok("1") {
        let path = std::path::PathBuf::from(std::env::var_os(DATABASE).unwrap());
        let _store = Store::open(path).unwrap();
        panic!("selected migration checkpoint was not reached");
    }
    for phase in ["after_version_column", "after_json_column"] {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("migration.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let project = store
            .create_named_project("Legacy migration history")
            .unwrap();
        let change = Change::RenameProject {
            title: "Legacy committed title".into(),
        };
        store
            .apply(project.id, &project.stamp(), "legacy", &change)
            .unwrap();
        // Source-equivalent schema3 representation; not an old binary.
        // Strip only source-verified future v6 fences and empty budget data.
        crate::root_operation_budget::strip_schema_six_for_predecessor_fixture(&store.conn);
        store
            .conn
            .execute_batch(
                "ALTER TABLE receipts DROP COLUMN apply_binding_json;
             ALTER TABLE receipts DROP COLUMN apply_binding_version;
             PRAGMA user_version=3;",
            )
            .unwrap();
        drop(store);
        let before = migration_snapshot(&path);
        assert_eq!(before.version, 3);
        assert!(
            !before
                .columns
                .iter()
                .any(|name| name.starts_with("apply_binding_"))
        );
        let output_path = dir.path().join("child.log");
        let output = std::fs::File::create_new(&output_path).unwrap();
        let child = std::process::Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "apply_crash_tests::binding_migration_ddl_rolls_back_after_abrupt_exit",
                "--nocapture",
                "--test-threads=1",
            ])
            .env(CHILD, "1")
            .env(DATABASE, &path)
            .env(PHASE, phase)
            .stdin(std::process::Stdio::null())
            .stdout(output.try_clone().unwrap())
            .stderr(output)
            .spawn()
            .unwrap();
        let status = wait_migration_child(child);
        let output = std::fs::read_to_string(output_path).unwrap();
        println!("{output}");
        assert_eq!(status.code(), Some(86), "wrong child exit for {phase}");
        assert!(output.contains(&format!("BINDING_MIGRATION_CHECKPOINT:{phase}")));
        // No Store::open, WAL removal or product recovery precedes this read.
        assert_eq!(
            migration_snapshot(&path),
            before,
            "partial migration at {phase}"
        );
        // Independent successful reopen control after rollback was observed.
        let mut reopened = Store::open(&path).unwrap();
        assert_eq!(
            reopened
                .observe_apply_receipt_v1(
                    project.id,
                    &project.stamp(),
                    "legacy",
                    &change,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap(),
            ApplyReceiptObservation::LegacyUnverified
        );
    }
}

// ACC-004 combines an actual competing BEGIN IMMEDIATE with abrupt writer
// termination. These controls exist only in this test module and never change
// process-global environment variables.
const CONCURRENT_MODE: &str = "MOTIONWRIGHT_TEST_APPLY_CONCURRENT";

#[derive(Serialize, Deserialize)]
struct ConcurrentCase {
    writer: CrashCase,
    base: RevisionStamp,
    contender_request_id: String,
    nonce: String,
    parent_pid: u32,
    phase: String,
    ready: PathBuf,
    release: PathBuf,
}

fn concurrent_identity(
    case: &ConcurrentCase,
    case_sha256: &str,
    stage: &str,
    pid: u32,
) -> serde_json::Value {
    serde_json::json!({
        "schema": "motionwright.concurrent-apply-test/1",
        "nonce": case.nonce,
        "case_sha256": case_sha256,
        "project_id": case.writer.original.id,
        "base": case.base,
        "writer_request_id": case.writer.request_id,
        "contender_request_id": case.contender_request_id,
        "checkpoint": case.phase,
        "stage": stage,
        "pid": pid,
    })
}

fn concurrent_config() -> (ConcurrentCase, String) {
    let bytes = fs::read(std::env::var_os(CASE_PATH).unwrap()).unwrap();
    let case: ConcurrentCase = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(case.base, case.writer.original.stamp());
    assert_ne!(case.writer.request_id, case.contender_request_id);
    assert!(matches!(
        case.phase.as_str(),
        "before_commit" | "after_commit"
    ));
    assert_eq!(std::env::var(CHECKPOINT).unwrap(), case.phase);
    assert_eq!(std::env::var(REQUEST).unwrap(), case.writer.request_id);
    (case, hex::encode(Sha256::digest(&bytes)))
}

fn publish_concurrent_token(path: &Path, token: &serde_json::Value) {
    let temporary = path.with_extension(format!("{}.pending", Uuid::now_v7()));
    let mut file = File::create_new(&temporary).unwrap();
    file.write_all(&serde_json::to_vec(token).unwrap()).unwrap();
    file.sync_all().unwrap();
    drop(file);
    assert!(
        !path.exists(),
        "a synchronization token must be published once"
    );
    fs::rename(temporary, path).unwrap();
}

fn read_concurrent_token(path: &Path) -> Option<serde_json::Value> {
    match fs::read(path) {
        Ok(bytes) => Some(serde_json::from_slice(&bytes).unwrap()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => panic!("cannot read concurrent fixture token: {error}"),
    }
}

fn concurrent_checkpoint(request_id: &str, phase: &str) {
    if std::env::var(CONCURRENT_MODE).as_deref() != Ok("1")
        || std::env::var(CHILD_MODE).as_deref() != Ok("writer")
        || (phase != "before_commit" && std::env::var(CHECKPOINT).as_deref() != Ok(phase))
    {
        return;
    }
    let (case, case_sha256) = concurrent_config();
    assert_eq!(request_id, case.writer.request_id);
    if phase == "before_commit" {
        publish_concurrent_token(
            &case.ready,
            &concurrent_identity(&case, &case_sha256, "writer-lock-held", std::process::id()),
        );
        let expected = concurrent_identity(&case, &case_sha256, "writer-release", case.parent_pid);
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            if let Some(released) = read_concurrent_token(&case.release) {
                assert_eq!(
                    released, expected,
                    "release must bind the exact case and full base"
                );
                break;
            }
            assert!(
                Instant::now() < deadline,
                "writer synchronization timed out"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }
    if phase == case.phase {
        println!(
            "APPLY_CONCURRENT_CHECKPOINT:{}:{}:{}:{phase}",
            case.nonce, case_sha256, request_id
        );
        std::io::stdout().flush().unwrap();
    }
}

struct ConcurrentDirectory {
    temp: Option<tempfile::TempDir>,
    retain: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl Drop for ConcurrentDirectory {
    fn drop(&mut self) {
        if (thread::panicking() || self.retain.load(std::sync::atomic::Ordering::SeqCst))
            && let Some(temp) = self.temp.take()
        {
            eprintln!(
                "failed concurrent fixture retained at {}",
                temp.keep().display()
            );
        }
    }
}

// Output is created in the evidence directory before spawn when custody is
// requested. A panic cannot delete the original child output before Drop
// reaps the owned child and records its terminal status.
struct ConcurrentChild {
    child: Option<std::process::Child>,
    status: Option<ExitStatus>,
    pid: Option<u32>,
    identity: serde_json::Value,
    stdout: PathBuf,
    stderr: PathBuf,
    receipt: PathBuf,
    deadline: Instant,
    timed_out: bool,
    error: Option<String>,
    recorded: bool,
}

impl ConcurrentChild {
    fn spawn(
        config: &Path,
        case: &ConcurrentCase,
        case_sha256: &str,
        name: &str,
        mode: &str,
        output: Option<&Path>,
        evidence: Option<&Evidence>,
    ) -> Self {
        let root = evidence
            .map(|value| value.directory.as_path())
            .unwrap_or_else(|| config.parent().unwrap());
        let call = format!("concurrent-{mode}-{}", Uuid::now_v7());
        let mut owned = Self {
            child: None,
            status: None,
            pid: None,
            identity: concurrent_identity(case, case_sha256, mode, std::process::id()),
            stdout: root.join(format!("{call}.stdout")),
            stderr: root.join(format!("{call}.stderr")),
            receipt: root.join(format!("{call}.process.json")),
            deadline: Instant::now() + Duration::from_secs(30),
            timed_out: false,
            error: None,
            recorded: false,
        };
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                &format!("apply_crash_tests::{name}"),
                "--nocapture",
                "--test-threads=1",
            ])
            .env(CONCURRENT_MODE, "1")
            .env(CHILD_MODE, mode)
            .env(CASE_PATH, config)
            .env(CHECKPOINT, &case.phase)
            .env(REQUEST, &case.writer.request_id)
            .env_remove(SNAPSHOT_PATH)
            .env_remove(EVIDENCE_ROOT)
            .stdin(Stdio::null())
            .stdout(File::create_new(&owned.stdout).unwrap())
            .stderr(File::create_new(&owned.stderr).unwrap());
        if let Some(output) = output {
            command.env(SNAPSHOT_PATH, output);
        }
        match command.spawn() {
            Ok(child) => {
                owned.pid = Some(child.id());
                owned.child = Some(child);
            }
            Err(error) => {
                owned.error = Some(format!("spawn failed: {error}"));
                panic!("cannot spawn concurrent fixture: {error}");
            }
        }
        owned
    }

    fn poll(&mut self) -> Option<ExitStatus> {
        if let Some(status) = self.status {
            return Some(status);
        }
        match self.child.as_mut().unwrap().try_wait() {
            Ok(Some(status)) => {
                self.status = Some(status);
                self.child.take();
                Some(status)
            }
            Ok(None) => None,
            Err(error) => {
                self.error = Some(format!("wait failed: {error}"));
                panic!("cannot observe concurrent fixture child: {error}");
            }
        }
    }

    fn wait(&mut self) -> ExitStatus {
        loop {
            if let Some(status) = self.poll() {
                self.record()
                    .expect("retain concurrent child terminal status");
                return status;
            }
            if Instant::now() >= self.deadline {
                self.timed_out = true;
                self.error = Some("owned concurrent child timed out".into());
                panic!("owned concurrent fixture child timed out");
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn record(&mut self) -> std::io::Result<()> {
        if self.recorded {
            return Ok(());
        }
        // Do not retry a partially written receipt during unwinding.
        self.recorded = true;
        let receipt = serde_json::json!({
            "identity": self.identity,
            "child_pid": self.pid,
            "exit_code": self.status.as_ref().and_then(ExitStatus::code),
            "exit_status": self.status.as_ref().map(ToString::to_string),
            "success": self.status.as_ref().map(ExitStatus::success),
            "timed_out": self.timed_out,
            "error": self.error,
            "parent_unwinding": thread::panicking(),
            "stdout": self.stdout.file_name().unwrap().to_string_lossy(),
            "stderr": self.stderr.file_name().unwrap().to_string_lossy(),
        });
        serde_json::to_writer_pretty(File::create_new(&self.receipt)?, &receipt)
            .map_err(std::io::Error::other)
    }
}

impl Drop for ConcurrentChild {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            match child.try_wait() {
                Ok(Some(status)) => self.status = Some(status),
                initial => {
                    let killed = child.kill();
                    let deadline = Instant::now() + Duration::from_secs(5);
                    let cleanup = loop {
                        match child.try_wait() {
                            Ok(Some(status)) => {
                                self.status = Some(status);
                                break "reaped after owned cleanup".to_owned();
                            }
                            Ok(None) if Instant::now() < deadline => {
                                thread::sleep(Duration::from_millis(10));
                            }
                            Ok(None) => {
                                self.timed_out = true;
                                break "UNKNOWN: owned child did not terminate within cleanup budget"
                                    .to_owned();
                            }
                            Err(error) => {
                                break format!("UNKNOWN: cannot observe owned child: {error}");
                            }
                        }
                    };
                    // All unfinished-child exits unwind the test. Its isolated
                    // directory is retained; no successor or mutation is replayed.
                    self.error = Some(format!(
                        "owned child cleanup; previous={:?}; initial={initial:?}; kill={killed:?}; {cleanup}",
                        self.error
                    ));
                }
            }
        }
        if let Err(error) = self.record() {
            eprintln!("concurrent child receipt unavailable: {error}");
        }
    }
}

struct ConcurrentBusyGate {
    observed: std::sync::atomic::AtomicBool,
    parked: std::sync::atomic::AtomicBool,
    release: std::sync::atomic::AtomicBool,
    cancel: std::sync::atomic::AtomicBool,
    timed_out: std::sync::atomic::AtomicBool,
    calls: std::sync::atomic::AtomicUsize,
    deadline: Instant,
}

std::thread_local! {
    static CONCURRENT_BUSY_GATE: std::cell::RefCell<
        Option<std::sync::Arc<ConcurrentBusyGate>>
    > = const { std::cell::RefCell::new(None) };
}

struct ConcurrentBusyContext;

impl ConcurrentBusyContext {
    fn install(gate: std::sync::Arc<ConcurrentBusyGate>) -> Self {
        CONCURRENT_BUSY_GATE.with(|slot| {
            assert!(
                slot.borrow().is_none(),
                "busy context is thread-local and exclusive"
            );
            *slot.borrow_mut() = Some(gate);
        });
        Self
    }
}

impl Drop for ConcurrentBusyContext {
    fn drop(&mut self) {
        let _ = CONCURRENT_BUSY_GATE.try_with(|slot| {
            if let Ok(mut value) = slot.try_borrow_mut() {
                value.take();
            }
        });
    }
}

struct ConcurrentBusyPark(std::sync::Arc<ConcurrentBusyGate>);

impl Drop for ConcurrentBusyPark {
    fn drop(&mut self) {
        self.0
            .parked
            .store(false, std::sync::atomic::Ordering::SeqCst);
    }
}

// rusqlite needs a function pointer. The callback's context belongs to this
// contender thread only; concurrent tests cannot replace each other's gate.
// No panic or filesystem operation crosses SQLite's callback boundary.
fn concurrent_busy_handler(_previous_calls: i32) -> bool {
    use std::sync::atomic::Ordering;
    let gate = CONCURRENT_BUSY_GATE
        .try_with(|slot| slot.try_borrow().ok().and_then(|value| value.clone()))
        .ok()
        .flatten();
    let Some(gate) = gate else {
        return false;
    };
    gate.calls.fetch_add(1, Ordering::SeqCst);
    gate.parked.store(true, Ordering::SeqCst);
    let _parked = ConcurrentBusyPark(gate.clone());
    gate.observed.store(true, Ordering::SeqCst);
    loop {
        if gate.cancel.load(Ordering::SeqCst) {
            return false;
        }
        if Instant::now() >= gate.deadline {
            gate.timed_out.store(true, Ordering::SeqCst);
            return false;
        }
        if gate.release.load(Ordering::SeqCst) {
            return true;
        }
        thread::sleep(Duration::from_millis(10));
    }
}

struct ConcurrentContender {
    worker: Option<thread::JoinHandle<Result<ApplyOutcome>>>,
    gate: std::sync::Arc<ConcurrentBusyGate>,
    identity: serde_json::Value,
    receipt: PathBuf,
    terminal: Option<serde_json::Value>,
    recorded: bool,
    retain_directory: std::sync::Arc<std::sync::atomic::AtomicBool>,
}

impl ConcurrentContender {
    fn start(
        mut store: Store,
        case: &ConcurrentCase,
        case_sha256: &str,
        root: &Path,
        evidence: Option<&Evidence>,
        retain_directory: std::sync::Arc<std::sync::atomic::AtomicBool>,
    ) -> Self {
        let gate = std::sync::Arc::new(ConcurrentBusyGate {
            observed: std::sync::atomic::AtomicBool::new(false),
            parked: std::sync::atomic::AtomicBool::new(false),
            release: std::sync::atomic::AtomicBool::new(false),
            cancel: std::sync::atomic::AtomicBool::new(false),
            timed_out: std::sync::atomic::AtomicBool::new(false),
            calls: std::sync::atomic::AtomicUsize::new(0),
            deadline: Instant::now() + Duration::from_secs(30),
        });
        let directory = evidence
            .map(|value| value.directory.as_path())
            .unwrap_or(root);
        let mut owned = Self {
            worker: None,
            gate: gate.clone(),
            identity: concurrent_identity(case, case_sha256, "contender", std::process::id()),
            receipt: directory.join(format!("concurrent-contender-{}.json", Uuid::now_v7())),
            terminal: None,
            recorded: false,
            retain_directory,
        };
        let id = case.writer.original.id;
        let base = case.base.clone();
        let request = case.contender_request_id.clone();
        owned.worker = Some(
            thread::Builder::new()
                .name("acc004-contender".into())
                .spawn(move || {
                    let context = ConcurrentBusyContext::install(gate);
                    store
                        .conn
                        .busy_handler(Some(concurrent_busy_handler))
                        .unwrap();
                    let result = store.apply(id, &base, &request, &change());
                    store.conn.busy_handler(None).unwrap();
                    drop(context);
                    result
                })
                .expect("spawn actual competing writer"),
        );
        owned
    }

    fn finished(&self) -> bool {
        self.worker
            .as_ref()
            .is_none_or(thread::JoinHandle::is_finished)
    }

    fn assert_parked(&self) {
        use std::sync::atomic::Ordering;
        assert!(self.gate.observed.load(Ordering::SeqCst));
        assert!(
            self.gate.parked.load(Ordering::SeqCst),
            "B left its busy callback"
        );
        assert!(!self.gate.release.load(Ordering::SeqCst));
        assert!(!self.gate.cancel.load(Ordering::SeqCst));
        assert!(!self.gate.timed_out.load(Ordering::SeqCst));
        assert!(
            Instant::now() < self.gate.deadline,
            "shared observation budget expired"
        );
        assert!(
            !self.finished(),
            "B returned during the frozen observation window"
        );
    }

    fn terminal_value(result: &Result<ApplyOutcome>) -> serde_json::Value {
        match result {
            Ok(outcome) => serde_json::json!({"status": "returned", "outcome": outcome}),
            Err(error) => serde_json::json!({"status": "error", "error": format!("{error:?}")}),
        }
    }

    fn wait(&mut self) -> Result<ApplyOutcome> {
        let deadline = Instant::now() + Duration::from_secs(30);
        while !self.finished() {
            assert!(Instant::now() < deadline, "contender completion timed out");
            thread::sleep(Duration::from_millis(10));
        }
        match self.worker.take().unwrap().join() {
            Ok(result) => {
                self.terminal = Some(Self::terminal_value(&result));
                self.record().expect("retain contender result");
                result
            }
            Err(_) => {
                self.terminal = Some(serde_json::json!({"status": "thread_panicked"}));
                self.record().expect("retain contender panic");
                panic!("actual competing writer panicked");
            }
        }
    }

    fn record(&mut self) -> std::io::Result<()> {
        use std::sync::atomic::Ordering;
        if self.recorded {
            return Ok(());
        }
        self.recorded = true;
        let receipt = serde_json::json!({
            "identity": self.identity,
            "busy_observed": self.gate.observed.load(Ordering::SeqCst),
            "busy_calls": self.gate.calls.load(Ordering::SeqCst),
            "busy_parked_at_terminal": self.gate.parked.load(Ordering::SeqCst),
            "retry_released": self.gate.release.load(Ordering::SeqCst),
            "cancelled": self.gate.cancel.load(Ordering::SeqCst),
            "busy_timed_out": self.gate.timed_out.load(Ordering::SeqCst),
            "terminal": self.terminal,
            "parent_unwinding": thread::panicking(),
        });
        serde_json::to_writer_pretty(File::create_new(&self.receipt)?, &receipt)
            .map_err(std::io::Error::other)
    }
}

impl Drop for ConcurrentContender {
    fn drop(&mut self) {
        use std::sync::atomic::Ordering;
        if let Some(worker) = self.worker.take() {
            self.gate.cancel.store(true, Ordering::SeqCst);
            self.gate.release.store(true, Ordering::SeqCst);
            let deadline = Instant::now() + Duration::from_secs(30);
            while !worker.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(10));
            }
            if worker.is_finished() {
                self.terminal = Some(match worker.join() {
                    Ok(result) => Self::terminal_value(&result),
                    Err(_) => serde_json::json!({"status": "thread_panicked"}),
                });
            } else {
                // Rust cannot kill a thread. Do not delete its owned database
                // or claim a terminal mutation result when it did not return.
                self.retain_directory.store(true, Ordering::SeqCst);
                self.terminal = Some(serde_json::json!({
                    "status": "unknown",
                    "reason": "contender did not finish within bounded cancellation",
                }));
                eprintln!("contender cancellation timed out; isolated data retained");
            }
        }
        if let Err(error) = self.record() {
            eprintln!("concurrent contender receipt unavailable: {error}");
        }
    }
}

fn concurrent_fixture(root: &Path, phase: &str) -> ConcurrentCase {
    let database = root.join("project.sqlite3");
    let mut store = Store::open(&database).unwrap();
    let initial = store
        .create_named_project("Before competing interrupted edit")
        .unwrap();
    let original = store
        .apply(
            initial.id,
            &initial.stamp(),
            "prepare-real-scene",
            &Change::AddScene {
                name: "Source".into(),
                objective: "Durable proxy owner".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let key = RenderWindowCacheKey {
        project_id: original.id,
        expected: original.stamp(),
        deliverable_id: original.deliverables[0].id,
        source_window: SourceFrameWindow { start: 0, end: 60 },
        fingerprint_sha256: hex::encode(Sha256::digest(b"owned source before edit")),
    };
    let input = root.join("source.bin");
    fs::write(&input, b"immutable proxy content survives every crash").unwrap();
    let record = store.put_render_window_proxy(&key, &input, 60).unwrap();
    let blob = store
        .lookup_render_window_proxy(&key)
        .unwrap()
        .unwrap()
        .blob_path;
    let expected_manifest = store
        .derived_cache_manifest_path(
            &record.scope_sha256,
            &record.kind,
            &record.fingerprint_sha256,
        )
        .unwrap();
    let owner = expected_manifest.parent().unwrap();
    let manifests = fs::read_dir(owner)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect::<Vec<_>>();
    assert_eq!(manifests.len(), 1);
    assert_eq!(manifests[0], expected_manifest);
    let base = original.stamp();
    drop(store);
    let nonce = Uuid::now_v7().to_string();
    ConcurrentCase {
        writer: CrashCase {
            database,
            manifest: manifests[0].clone(),
            blob,
            original,
            request_id: format!("interrupted-writer-{nonce}"),
        },
        base,
        contender_request_id: format!("competing-writer-{nonce}"),
        nonce,
        parent_pid: std::process::id(),
        phase: phase.to_owned(),
        ready: root.join("writer-ready.json"),
        release: root.join("writer-release.json"),
    }
}

fn observe_concurrent_in_fresh_process(
    root: &Path,
    config: &Path,
    case: &ConcurrentCase,
    case_sha256: &str,
    name: &str,
    parking_deadline: Option<Instant>,
    evidence: Option<&Evidence>,
) -> Snapshot {
    let output = root.join(format!("concurrent-snapshot-{}.json", Uuid::now_v7()));
    let mut observer = ConcurrentChild::spawn(
        config,
        case,
        case_sha256,
        name,
        "observer",
        Some(&output),
        evidence,
    );
    if let Some(deadline) = parking_deadline {
        observer.deadline = observer.deadline.min(deadline);
    }
    let status = observer.wait();
    let stdout = fs::read_to_string(&observer.stdout).unwrap();
    let stderr = fs::read_to_string(&observer.stderr).unwrap();
    assert!(
        status.success(),
        "fresh observer failed: {stderr}\n{stdout}"
    );
    assert!(stderr.is_empty(), "fresh observer stderr: {stderr}");
    assert!(
        stdout.contains(&format!(
            "APPLY_CONCURRENT_OBSERVED:{}:{}:{}:{}",
            case.nonce, case_sha256, case.writer.request_id, case.phase
        )),
        "zero tests or an unrelated observer cannot satisfy the case: {stdout}"
    );
    serde_json::from_slice(&fs::read(output).unwrap()).unwrap()
}

fn exercise_concurrent(name: &str, phase: &str) {
    use std::sync::atomic::Ordering;
    if std::env::var(CONCURRENT_MODE).as_deref() == Ok("1") {
        let (case, case_sha256) = concurrent_config();
        assert_eq!(case.phase, phase);
        match std::env::var(CHILD_MODE).unwrap().as_str() {
            "writer" => {
                let mut store = Store::open(&case.writer.database).unwrap();
                store
                    .apply(
                        case.writer.original.id,
                        &case.base,
                        &case.writer.request_id,
                        &change(),
                    )
                    .unwrap();
                panic!("real competing apply did not reach its selected checkpoint");
            }
            "observer" => {
                let output = std::env::var_os(SNAPSHOT_PATH).unwrap();
                serde_json::to_writer(File::create_new(output).unwrap(), &snapshot(&case.writer))
                    .unwrap();
                println!(
                    "APPLY_CONCURRENT_OBSERVED:{}:{}:{}:{}",
                    case.nonce, case_sha256, case.writer.request_id, case.phase
                );
                return;
            }
            _ => panic!("unexpected concurrent fixture subprocess mode"),
        }
    }

    // Separate containers from the four existing interruption selectors.
    let mut evidence = Evidence::begin(name, &format!("concurrent-{phase}-{}", Uuid::now_v7()));
    if let Some(evidence) = evidence.as_mut() {
        evidence.checkpoint = phase.to_owned();
    }
    let retained = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let temporary = ConcurrentDirectory {
        temp: Some(tempfile::tempdir().unwrap()),
        retain: retained.clone(),
    };
    let root = temporary
        .temp
        .as_ref()
        .unwrap()
        .path()
        .canonicalize()
        .unwrap();
    let case = concurrent_fixture(&root, phase);
    assert_eq!(case.base, case.writer.original.stamp());
    assert_ne!(case.writer.request_id, case.contender_request_id);
    let config = root.join("case.json");
    let case_bytes = serde_json::to_vec(&case).unwrap();
    File::create_new(&config)
        .unwrap()
        .write_all(&case_bytes)
        .unwrap();
    let case_sha256 = hex::encode(Sha256::digest(&case_bytes));
    if let Some(evidence) = evidence.as_mut() {
        evidence.original("case.json", &config);
    }

    // Opening B before A takes the writer lock prevents Store::open recovery
    // or setup from being mistaken for contention inside the actual apply.
    let contender_store = Store::open(&case.writer.database).unwrap();
    let before = snapshot(&case.writer);
    retain_phase(&mut evidence, "before", &before);
    assert_eq!(before.registrations.len(), 1);
    assert!(before.evictions.is_empty());
    assert!(before.manifest.is_some());

    let mut writer = ConcurrentChild::spawn(
        &config,
        &case,
        &case_sha256,
        name,
        "writer",
        None,
        evidence.as_ref(),
    );
    let ready = concurrent_identity(&case, &case_sha256, "writer-lock-held", writer.pid.unwrap());
    loop {
        if let Some(observed) = read_concurrent_token(&case.ready) {
            assert_eq!(observed, ready);
            break;
        }
        assert!(
            writer.poll().is_none(),
            "writer ended before publishing its lock-held checkpoint"
        );
        if Instant::now() >= writer.deadline {
            writer.timed_out = true;
            panic!("writer never reached the real before_commit checkpoint");
        }
        thread::sleep(Duration::from_millis(10));
    }
    let mut contender = ConcurrentContender::start(
        contender_store,
        &case,
        &case_sha256,
        &root,
        evidence.as_ref(),
        retained,
    );
    while !contender.gate.observed.load(Ordering::SeqCst) {
        assert!(
            !contender.finished(),
            "contender returned without SQLite busy contention"
        );
        assert!(
            writer.poll().is_none(),
            "writer exited before contention was observed"
        );
        assert!(
            Instant::now() < writer.deadline,
            "actual contention was not observed"
        );
        thread::sleep(Duration::from_millis(10));
    }
    assert!(contender.gate.calls.load(Ordering::SeqCst) > 0);
    assert!(!contender.gate.timed_out.load(Ordering::SeqCst));
    assert!(!contender.finished());
    if let Some(evidence) = evidence.as_mut() {
        evidence.json(
            "contention.json",
            &serde_json::json!({
                "identity": concurrent_identity(
                    &case, &case_sha256, "busy-before-writer-release", std::process::id()
                ),
                "sqlite_busy_calls": contender.gate.calls.load(Ordering::SeqCst),
                "contender_retry_released": false,
            }),
        );
        evidence.original("writer-ready.json", &case.ready);
    }
    publish_concurrent_token(
        &case.release,
        &concurrent_identity(&case, &case_sha256, "writer-release", case.parent_pid),
    );
    if let Some(evidence) = evidence.as_mut() {
        evidence.original("writer-release.json", &case.release);
    }
    let writer_status = writer.wait();
    let writer_stdout = fs::read_to_string(&writer.stdout).unwrap();
    let writer_stderr = fs::read_to_string(&writer.stderr).unwrap();
    assert_eq!(
        writer_status.code(),
        Some(EXIT_AT_CHECKPOINT),
        "{writer_stderr}\n{writer_stdout}"
    );
    assert!(writer_stderr.is_empty(), "{writer_stderr}");
    assert!(writer_stdout.contains(&format!(
        "APPLY_CONCURRENT_CHECKPOINT:{}:{}:{}:{phase}",
        case.nonce, case_sha256, case.writer.request_id
    )));
    assert!(writer_stdout.contains(&format!(
        "APPLY_CHECKPOINT:{}:{phase}",
        case.writer.request_id
    )));
    drop(writer);

    contender.assert_parked();
    let interrupted = observe_concurrent_in_fresh_process(
        &root,
        &config,
        &case,
        &case_sha256,
        name,
        Some(contender.gate.deadline),
        evidence.as_ref(),
    );
    contender.assert_parked();
    retain_phase(&mut evidence, "interrupted", &interrupted);
    let committed = phase == "after_commit";
    if committed {
        committed_outcome(&case.writer, &before, &interrupted);
        assert!(interrupted.registrations.is_empty());
        assert_eq!(interrupted.evictions, before.registrations);
        assert_eq!(interrupted.manifest, before.manifest);
        assert_eq!(interrupted.immutable_blob, before.immutable_blob);
    } else {
        assert_eq!(interrupted, before);
    }

    // B is still suspended before acquiring its write transaction. Recovery
    // is independently opened, then observed in another new process.
    let recovered = Store::open(&case.writer.database).unwrap();
    drop(recovered);
    contender.assert_parked();
    let cleaned = observe_concurrent_in_fresh_process(
        &root,
        &config,
        &case,
        &case_sha256,
        name,
        Some(contender.gate.deadline),
        evidence.as_ref(),
    );
    contender.assert_parked();
    retain_phase(&mut evidence, "cleaned", &cleaned);
    if committed {
        assert_eq!(cleaned.project, interrupted.project);
        assert_eq!(cleaned.events, interrupted.events);
        assert_eq!(cleaned.receipts, interrupted.receipts);
        assert!(cleaned.registrations.is_empty());
        assert!(cleaned.evictions.is_empty());
        assert!(cleaned.manifest.is_none());
    } else {
        assert_eq!(cleaned, before);
    }
    assert_eq!(cleaned.immutable_blob, before.immutable_blob);
    assert!(!contender.finished());
    assert!(!contender.gate.timed_out.load(Ordering::SeqCst));
    contender.gate.release.store(true, Ordering::SeqCst);
    let contender_result = contender.wait();
    if committed {
        assert!(
            matches!(&contender_result, Err(StorageError::Conflict { expected, actual })
                if *expected == case.base.revision && *actual == case.base.revision + 1),
            "committed A must make same-base B conflict: {contender_result:?}"
        );
    } else {
        let accepted = contender_result
            .as_ref()
            .expect("B wins after A rolls back");
        assert!(!accepted.replayed);
        assert_eq!(accepted.request_id, case.contender_request_id);
    }
    drop(contender);

    let winner = CrashCase {
        database: case.writer.database.clone(),
        manifest: case.writer.manifest.clone(),
        blob: case.writer.blob.clone(),
        original: case.writer.original.clone(),
        request_id: if committed {
            case.writer.request_id.clone()
        } else {
            case.contender_request_id.clone()
        },
    };
    let loser = if committed {
        &case.contender_request_id
    } else {
        &case.writer.request_id
    };
    let after_retry = observe_concurrent_in_fresh_process(
        &root,
        &config,
        &case,
        &case_sha256,
        name,
        None,
        evidence.as_ref(),
    );
    retain_phase(&mut evidence, "after_retry", &after_retry);
    let accepted = committed_outcome(&winner, &before, &after_retry);
    assert!(
        !after_retry
            .receipts
            .iter()
            .any(|receipt| receipt[0] == *loser)
    );
    assert!(after_retry.registrations.is_empty());
    assert!(after_retry.evictions.is_empty());
    assert!(after_retry.manifest.is_none());
    assert_eq!(after_retry.immutable_blob, before.immutable_blob);
    if !committed {
        assert_eq!(contender_result.as_ref().unwrap(), &accepted);
    }

    let mut reopened = Store::open(&case.writer.database).unwrap();
    let replay = reopened
        .apply(
            case.writer.original.id,
            &case.base,
            &winner.request_id,
            &change(),
        )
        .unwrap();
    let mut expected_replay = accepted.clone();
    expected_replay.replayed = true;
    assert_eq!(replay, expected_replay);
    let refused = reopened.apply(case.writer.original.id, &case.base, loser, &change());
    assert!(
        matches!(&refused, Err(StorageError::Conflict { expected, actual })
            if *expected == case.base.revision && *actual == accepted.project.revision),
        "loser must not publish an additional mutation: {refused:?}"
    );
    if let Some(evidence) = evidence.as_mut() {
        evidence.json(
            "terminal-results.json",
            &serde_json::json!({
                "identity": concurrent_identity(
                    &case, &case_sha256, "known-winner-replay-and-loser-refusal", std::process::id()
                ),
                "accepted": accepted,
                "replay": replay,
                "loser_request_id": loser,
                "loser_result": format!("{refused:?}"),
            }),
        );
    }
    drop(reopened);
    let final_snapshot = observe_concurrent_in_fresh_process(
        &root,
        &config,
        &case,
        &case_sha256,
        name,
        None,
        evidence.as_ref(),
    );
    retain_phase(&mut evidence, "final", &final_snapshot);
    assert_eq!(final_snapshot, after_retry);
    if let Some(evidence) = evidence.as_mut() {
        evidence.finish();
    }
}

#[test]
fn same_base_competing_writer_wins_after_real_before_commit_exit() {
    exercise_concurrent(
        "same_base_competing_writer_wins_after_real_before_commit_exit",
        "before_commit",
    );
}

#[test]
fn same_base_competing_writer_conflicts_after_real_after_commit_exit() {
    exercise_concurrent(
        "same_base_competing_writer_conflicts_after_real_after_commit_exit",
        "after_commit",
    );
}

// Portable import has a filesystem publication boundary separate from SQLite.
// These process cuts establish recovery semantics, not power-loss atomicity or
// permission to replay an operation whose durable result remains unknown.
const BUNDLE_CHILD_MODE: &str = "MOTIONWRIGHT_TEST_BUNDLE_CHILD_MODE";
const BUNDLE_CASE_PATH: &str = "MOTIONWRIGHT_TEST_BUNDLE_CASE_PATH";
const BUNDLE_PROJECT: &str = "MOTIONWRIGHT_TEST_BUNDLE_PROJECT";
const BUNDLE_PHASE: &str = "MOTIONWRIGHT_TEST_BUNDLE_PHASE";
const BUNDLE_OBSERVATION: &str = "MOTIONWRIGHT_TEST_BUNDLE_OBSERVATION";
const BUNDLE_EXIT: i32 = 87;

pub(super) fn bundle_checkpoint(project_id: Uuid, phase: &str) {
    if std::env::var(BUNDLE_PHASE).as_deref() == Ok(phase)
        && std::env::var(BUNDLE_PROJECT).as_deref() == Ok(project_id.to_string().as_str())
    {
        println!("BUNDLE_CHECKPOINT:{project_id}:{phase}");
        std::io::stdout().flush().unwrap();
        std::process::exit(BUNDLE_EXIT);
    }
}

#[derive(Serialize, Deserialize)]
struct BundleCrashCase {
    database: PathBuf,
    bundle: PathBuf,
    project_id: Uuid,
}

#[derive(Serialize, Deserialize)]
struct BundleCrashObservation {
    tables: std::collections::BTreeMap<String, Vec<Vec<String>>>,
    project: Option<ProjectBackup>,
    blobs: Vec<Option<String>>,
}

fn bundle_crash_observation(case: &BundleCrashCase) -> BundleCrashObservation {
    assert!(case.database.is_file());
    let mut store = Store::open(&case.database).unwrap();
    let manifest = load_bundle_manifest(&case.bundle).unwrap();
    let names: Vec<String> = store.conn
        .prepare("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .map(|row| row.unwrap())
        .collect();
    let mut tables = std::collections::BTreeMap::new();
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
        tables.insert(name, rows);
    }
    let project = match store.export_project(case.project_id) {
        Ok(backup) => Some(backup),
        Err(StorageError::NotFound) => None,
        Err(error) => panic!("unexpected recovered project result: {error}"),
    };
    let blobs = manifest
        .blobs
        .iter()
        .map(|blob| match store.read_blob(&blob.sha256, 4096) {
            Ok(bytes) => {
                assert_eq!(bytes.len() as u64, blob.size_bytes);
                let digest = hex::encode(Sha256::digest(&bytes));
                assert_eq!(digest, blob.sha256);
                Some(digest)
            }
            Err(StorageError::BlobMissing { .. }) => None,
            Err(error) => panic!("unexpected recovered CAS result: {error}"),
        })
        .collect();
    BundleCrashObservation {
        tables,
        project,
        blobs,
    }
}

fn run_bundle_crash_child(
    root: &Path,
    config: &Path,
    case: &BundleCrashCase,
    name: &str,
    phase: &str,
    mode: &str,
    label: &str,
) -> Option<BundleCrashObservation> {
    let output = root.join(format!("{label}.stdout"));
    let errors = root.join(format!("{label}.stderr"));
    let observation = root.join(format!("{label}.json"));
    let child = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            &format!("apply_crash_tests::{name}"),
            "--nocapture",
        ])
        .env(BUNDLE_CHILD_MODE, mode)
        .env(BUNDLE_CASE_PATH, config)
        .env(BUNDLE_PROJECT, case.project_id.to_string())
        .env(BUNDLE_PHASE, phase)
        .env(BUNDLE_OBSERVATION, &observation)
        .stdin(std::process::Stdio::null())
        .stdout(File::create_new(&output).unwrap())
        .stderr(File::create_new(&errors).unwrap())
        .spawn()
        .unwrap();
    let status = wait_migration_child(child);
    let stdout = fs::read_to_string(output).unwrap();
    let stderr = fs::read_to_string(errors).unwrap();
    if mode == "writer" {
        assert_eq!(status.code(), Some(BUNDLE_EXIT), "{stdout}\n{stderr}");
        assert!(stdout.contains(&format!("BUNDLE_CHECKPOINT:{}:{phase}", case.project_id)));
        None
    } else {
        assert!(status.success(), "{stdout}\n{stderr}");
        assert!(stdout.contains(&format!("BUNDLE_OBSERVED:{}", case.project_id)));
        Some(serde_json::from_slice(&fs::read(observation).unwrap()).unwrap())
    }
}

fn exercise_bundle_process_cut(name: &str, phase: &str) {
    if let Ok(mode) = std::env::var(BUNDLE_CHILD_MODE) {
        let case: BundleCrashCase =
            serde_json::from_slice(&fs::read(std::env::var_os(BUNDLE_CASE_PATH).unwrap()).unwrap())
                .unwrap();
        assert_eq!(std::env::var(BUNDLE_PHASE).unwrap(), phase);
        assert_eq!(
            std::env::var(BUNDLE_PROJECT).unwrap(),
            case.project_id.to_string()
        );
        if mode == "writer" {
            let mut target = Store::open(&case.database).unwrap();
            target.import_project_bundle(&case.bundle).unwrap();
            panic!("portable import did not reach its requested checkpoint");
        }
        assert_eq!(mode, "observer");
        let observed = bundle_crash_observation(&case);
        serde_json::to_writer(
            File::create_new(std::env::var_os(BUNDLE_OBSERVATION).unwrap()).unwrap(),
            &observed,
        )
        .unwrap();
        println!("BUNDLE_OBSERVED:{}", case.project_id);
        return;
    }

    let directory = tempfile::tempdir().unwrap();
    let root = directory.path().canonicalize().unwrap();
    let mut source = Store::open(root.join("source/state.sqlite3")).unwrap();
    let mut project = source
        .create_named_project("Portable process recovery")
        .unwrap();
    for (index, bytes) in [
        b"alpha portable content".as_slice(),
        b"beta portable content",
        b"gamma shared content",
    ]
    .into_iter()
    .enumerate()
    {
        let input = root.join(format!("source-{index}.bin"));
        fs::write(&input, bytes).unwrap();
        let descriptor = source.ingest_blob_file(&input).unwrap();
        project = source
            .apply(
                project.id,
                &project.stamp(),
                &format!("source-asset-{index}"),
                &Change::AddAsset {
                    asset: motionwright_domain::Asset {
                        id: Uuid::now_v7(),
                        name: format!("portable-{index}"),
                        media_type: "application/octet-stream".into(),
                        content_sha256: Some(descriptor.sha256),
                        source_revision: Some("local-import".into()),
                    },
                },
            )
            .unwrap()
            .project;
    }
    let bundle = root.join("portable");
    let manifest = source.export_project_bundle(project.id, &bundle).unwrap();
    drop(source);
    let original_manifest = fs::read(bundle.join("manifest.json")).unwrap();
    let original_members: Vec<_> = manifest
        .blobs
        .iter()
        .map(|blob| fs::read(bundle.join(&blob.relative_path)).unwrap())
        .collect();
    assert_eq!(manifest.blobs.len(), 3);

    let database = root.join("target/state.sqlite3");
    let mut target = Store::open(&database).unwrap();
    let sentinel = target
        .create_named_project("Existing shared asset owner")
        .unwrap();
    let shared = &manifest.blobs[2];
    let captured = target
        .ingest_blob_file(bundle.join(&shared.relative_path))
        .unwrap();
    assert_eq!(captured.sha256, shared.sha256);
    target
        .apply(
            sentinel.id,
            &sentinel.stamp(),
            "retain-shared-content",
            &Change::AddAsset {
                asset: motionwright_domain::Asset {
                    id: Uuid::now_v7(),
                    name: "shared existing content".into(),
                    media_type: "application/octet-stream".into(),
                    content_sha256: Some(shared.sha256.clone()),
                    source_revision: Some("local-import".into()),
                },
            },
        )
        .unwrap();
    drop(target);
    let case = BundleCrashCase {
        database,
        bundle,
        project_id: project.id,
    };
    let before = bundle_crash_observation(&case);
    assert!(before.project.is_none());
    assert_eq!(before.blobs, vec![None, None, Some(shared.sha256.clone())]);
    let config = root.join("case.json");
    serde_json::to_writer(File::create_new(&config).unwrap(), &case).unwrap();
    run_bundle_crash_child(
        &root,
        &config,
        &case,
        name,
        phase,
        "writer",
        "interrupted-writer",
    );
    let interrupted = run_bundle_crash_child(
        &root,
        &config,
        &case,
        name,
        phase,
        "observer",
        "fresh-observer",
    )
    .unwrap();

    if phase == "after_member_publication" {
        assert!(
            interrupted.project.is_none(),
            "uncommitted import became visible"
        );
        assert_eq!(
            interrupted.tables, before.tables,
            "partial SQLite import survived"
        );
        assert_eq!(
            interrupted.blobs,
            vec![
                Some(manifest.blobs[0].sha256.clone()),
                None,
                Some(shared.sha256.clone())
            ]
        );
        // The observer established NotApplied. This deliberately new import is
        // not an automatic retry of UNKNOWN. Existing immutable or shared CAS
        // is reused; no unowned cleanup or deletion is performed.
        let mut reopened = Store::open(&case.database).unwrap();
        reopened.import_project_bundle(&case.bundle).unwrap();
    } else {
        assert_eq!(phase, "after_bundle_commit");
        assert!(interrupted.project.is_some(), "committed import was lost");
        assert!(interrupted.blobs.iter().all(Option::is_some));
        // Read back the known committed result. Never reissue the interrupted
        // operation merely because its return value was not received.
    }

    let recovered = run_bundle_crash_child(
        &root,
        &config,
        &case,
        name,
        phase,
        "observer",
        "recovered-observer",
    )
    .unwrap();
    let restored = recovered.project.unwrap();
    assert_eq!(restored.project.id, project.id);
    assert_ne!(restored.project.generation, project.generation);
    assert_eq!(restored.project.revision, project.revision);
    assert_eq!(restored.project.assets, project.assets);
    assert_eq!(restored.events, manifest.backup.events);
    assert!(recovered.blobs.iter().all(Option::is_some));
    for (table, rows) in before.tables {
        for row in rows {
            assert!(
                recovered.tables[&table].contains(&row),
                "existing row changed in {table}"
            );
        }
    }
    assert_eq!(
        fs::read(case.bundle.join("manifest.json")).unwrap(),
        original_manifest
    );
    for (member, expected) in manifest.blobs.iter().zip(original_members) {
        assert_eq!(
            fs::read(case.bundle.join(&member.relative_path)).unwrap(),
            expected
        );
    }
}

#[test]
fn real_bundle_exit_after_first_cas_publication_recovers_without_partial_sqlite() {
    exercise_bundle_process_cut(
        "real_bundle_exit_after_first_cas_publication_recovers_without_partial_sqlite",
        "after_member_publication",
    );
}

#[test]
fn real_bundle_exit_after_commit_is_observed_without_reissuing_import() {
    exercise_bundle_process_cut(
        "real_bundle_exit_after_commit_is_observed_without_reissuing_import",
        "after_bundle_commit",
    );
}
