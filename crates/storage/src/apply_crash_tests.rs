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
            "SELECT request_id,request_sha256,result_json,created_at
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
    let owner = root
        .join("derived-cache/sha256")
        .join(&record.scope_sha256[..2])
        .join(&record.scope_sha256);
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
