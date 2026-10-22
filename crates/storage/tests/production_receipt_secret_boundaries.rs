//! Credential-shaped canaries exercise the real receipt admission/read APIs.
//! They are synthetic fixture data, never host credentials. No native effect runs.
use motionwright_domain::Project;
use motionwright_storage::{ProductionReceiptInput, StorageError, Store};
use rusqlite::{Connection, OpenFlags, params, types::Value as SqlValue};
use serde_json::{Map, Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::{Path, PathBuf},
};

const ADMISSION_ERROR: &str = "production receipt contains credential-like material and cannot enter project storage or export";
const LEGACY_ERROR: &str = "stored production receipt contains credential-like material and cannot enter project storage or export";

fn canary() -> String {
    format!("ghp_{}", "A".repeat(24))
}

struct Fixture {
    // Close SQLite before TempDir removes the fixture, including on Windows.
    store: Option<Store>,
    project: Project,
    path: PathBuf,
    blob_root: PathBuf,
    _temp: tempfile::TempDir,
}

impl Fixture {
    fn new() -> Self {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("receipt-boundaries.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let project = Project::new("Receipt secret boundary").unwrap();
        store.create_project(&project).unwrap();
        let source = temp.path().join("existing-safe-blob.bin");
        fs::write(&source, b"existing immutable fixture bytes").unwrap();
        store.ingest_blob_file(&source).unwrap();
        let blob_root = store.blob_root().to_path_buf();
        Self {
            store: Some(store),
            project,
            path,
            blob_root,
            _temp: temp,
        }
    }

    fn store(&self) -> &Store {
        self.store.as_ref().unwrap()
    }

    fn store_mut(&mut self) -> &mut Store {
        self.store.as_mut().unwrap()
    }

    fn reopen(&mut self) {
        drop(self.store.take());
        self.store = Some(Store::open(&self.path).unwrap());
    }

    fn snapshot(&self) -> Snapshot {
        let conn =
            Connection::open_with_flags(&self.path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        let tx = conn.unchecked_transaction().unwrap();
        let schema = rows(
            &tx,
            "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_schema ORDER BY type,name",
        );
        let user_version = tx
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap();
        let names = {
            let mut statement = tx
                .prepare("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")
                .unwrap();
            statement
                .query_map([], |row| row.get::<_, String>(0))
                .unwrap()
                .map(|row| row.unwrap())
                .collect::<Vec<_>>()
        };
        let mut tables = BTreeMap::new();
        for name in names {
            let select = format!("SELECT * FROM \"{}\"", name.replace('"', "\"\""));
            let columns = tx.prepare(&select).unwrap().column_count();
            let order = (1..=columns)
                .map(|column| column.to_string())
                .collect::<Vec<_>>()
                .join(",");
            tables.insert(name, rows(&tx, &format!("{select} ORDER BY {order}")));
        }
        tx.commit().unwrap();
        Snapshot {
            schema,
            user_version,
            tables,
            blobs: files(&self.blob_root),
        }
    }

    fn assert_unchanged(&self, before: &Snapshot) {
        // Do not format a snapshot: legacy fixtures deliberately contain canaries.
        assert!(
            self.snapshot() == *before,
            "RECEIPT_BOUNDARY_CHANGED_SQL_OR_CAS"
        );
    }
}

#[derive(PartialEq)]
struct Snapshot {
    schema: Vec<Vec<SqlValue>>,
    user_version: i64,
    // Includes sqlite_sequence as well as all application tables.
    tables: BTreeMap<String, Vec<Vec<SqlValue>>>,
    blobs: BTreeMap<PathBuf, Vec<u8>>,
}

fn rows(conn: &Connection, sql: &str) -> Vec<Vec<SqlValue>> {
    let mut statement = conn.prepare(sql).unwrap();
    let columns = statement.column_count();
    statement
        .query_map([], |row| {
            (0..columns)
                .map(|column| row.get::<_, SqlValue>(column))
                .collect::<rusqlite::Result<Vec<_>>>()
        })
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

fn files(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
    fn visit(root: &Path, current: &Path, result: &mut BTreeMap<PathBuf, Vec<u8>>) {
        let mut children = fs::read_dir(current)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect::<Vec<_>>();
        children.sort();
        for path in children {
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(
                !metadata.file_type().is_symlink(),
                "fixture CAS must not contain links"
            );
            if metadata.is_dir() {
                visit(root, &path, result);
            } else {
                assert!(
                    metadata.is_file(),
                    "fixture CAS must contain ordinary files"
                );
                result.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    fs::read(path).unwrap(),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(root, root, &mut result);
    assert!(
        !result.is_empty(),
        "fixture must protect an existing CAS blob"
    );
    result
}

fn input(project: &Project, key: &str) -> ProductionReceiptInput {
    ProductionReceiptInput {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: key.into(),
        request_sha256: "a".repeat(64),
        command: "driver.motion-canvas.render.start".into(),
        stage: "dispatching".into(),
        payload: json!({
            "mutation": true,
            "connection": "b".repeat(64),
            "job_ref": "fixture-job"
        }),
    }
}

fn recovery_input(project: &Project) -> ProductionReceiptInput {
    ProductionReceiptInput {
        command: "motionwright.recovery.motion-canvas".into(),
        payload: json!({
            "schema": "motionwright-cross-app-recovery/1",
            "retryable": false
        }),
        ..input(project, "recovery-secret-boundary")
    }
}

fn secret_rejected<T>(result: Result<T, StorageError>, expected: &str, witness: &'static str) {
    let message = match result {
        Ok(_) => panic!("{witness}"),
        Err(StorageError::InvalidBackup(message)) => message,
        Err(_) => panic!("RECEIPT_SECRET_UNEXPECTED_ERROR_CLASS"),
    };
    assert!(
        message == expected,
        "RECEIPT_SECRET_ERROR_MUST_BE_STATIC_AND_NON_DISCLOSING"
    );
    assert!(
        !message.contains(&canary()),
        "RECEIPT_SECRET_ERROR_EXPOSED_CANARY"
    );
}

fn rejected_new_input(alter: impl FnOnce(&mut ProductionReceiptInput), witness: &'static str) {
    let mut fixture = Fixture::new();
    let clean = input(&fixture.project, "new-secret-boundary");
    let mut sensitive = clean.clone();
    alter(&mut sensitive);
    let before = fixture.snapshot();
    secret_rejected(
        fixture.store_mut().append_production_receipt(sensitive),
        ADMISSION_ERROR,
        witness,
    );
    fixture.assert_unchanged(&before);
    fixture.reopen();
    fixture.assert_unchanged(&before);
    let head = fixture
        .store()
        .production_receipt_watermark(fixture.project.id)
        .unwrap();
    assert!(head.latest_id.is_none() && head.count == 0);
    let accepted = fixture
        .store_mut()
        .append_production_receipt(clean)
        .unwrap();
    assert!(accepted.stage == "dispatching");
}

#[test]
fn nested_payload_credentials_are_rejected_before_any_receipt_write() {
    rejected_new_input(
        |entry| {
            entry.payload = json!({"diagnostic": {"lines": ["ordinary", canary()]}});
        },
        "RECEIPT_SECRET_NESTED_PAYLOAD_ADMITTED",
    );
}

#[test]
fn json_key_credentials_are_rejected_before_any_receipt_write() {
    rejected_new_input(
        |entry| {
            let mut object = Map::new();
            object.insert(canary(), json!({"ordinary": true}));
            entry.payload = Value::Object(object);
        },
        "RECEIPT_SECRET_JSON_KEY_ADMITTED",
    );
}

#[test]
fn request_id_credentials_are_rejected_before_any_receipt_write() {
    rejected_new_input(
        |entry| entry.request_id = canary(),
        "RECEIPT_SECRET_REQUEST_ID_ADMITTED",
    );
}

#[test]
fn command_credentials_are_rejected_before_any_receipt_write() {
    rejected_new_input(
        |entry| entry.command = canary(),
        "RECEIPT_SECRET_COMMAND_ADMITTED",
    );
}

#[test]
fn stage_credentials_are_rejected_before_any_receipt_write() {
    rejected_new_input(
        |entry| entry.stage = canary(),
        "RECEIPT_SECRET_STAGE_ADMITTED",
    );
}

fn rejected_sensitive_terminal(stage: &str, witness: &'static str) {
    let mut fixture = Fixture::new();
    let initial = input(&fixture.project, "terminal-secret-boundary");
    let dispatch = fixture
        .store_mut()
        .append_production_receipt(initial.clone())
        .unwrap();
    let before = fixture.snapshot();
    let payload = if stage == "completed" {
        json!({"result": {"data": {"diagnostic": canary()}}})
    } else {
        json!({
            "error": {"message": canary(), "outcome_known": stage == "failed_known"},
            "mutation": true
        })
    };
    let terminal = ProductionReceiptInput {
        stage: stage.into(),
        payload,
        ..initial.clone()
    };
    secret_rejected(
        fixture.store_mut().append_production_receipt(terminal),
        ADMISSION_ERROR,
        witness,
    );
    fixture.assert_unchanged(&before);
    fixture.reopen();
    fixture.assert_unchanged(&before);
    let retained = fixture
        .store()
        .production_receipts(fixture.project.id, 10)
        .unwrap();
    assert!(
        retained == vec![dispatch],
        "rejected terminal must leave the original dispatch unresolved"
    );
    assert!(
        matches!(
            fixture.store_mut().append_production_receipt(initial),
            Err(StorageError::RequestReuse)
        ),
        "rejected terminal must not release the consumed dispatch key"
    );
    fixture.assert_unchanged(&before);
}

#[test]
fn sensitive_completed_terminal_preserves_dispatch_and_blocks_replay() {
    rejected_sensitive_terminal("completed", "RECEIPT_SECRET_COMPLETED_TERMINAL_ADMITTED");
}

#[test]
fn sensitive_failed_known_terminal_preserves_dispatch_and_blocks_replay() {
    rejected_sensitive_terminal(
        "failed_known",
        "RECEIPT_SECRET_FAILED_KNOWN_TERMINAL_ADMITTED",
    );
}

#[test]
fn sensitive_unknown_terminal_preserves_dispatch_and_blocks_replay() {
    rejected_sensitive_terminal(
        "outcome_unknown",
        "RECEIPT_SECRET_UNKNOWN_TERMINAL_ADMITTED",
    );
}

#[test]
fn sensitive_recovery_claim_does_not_consume_the_first_attempt() {
    let mut fixture = Fixture::new();
    let clean = recovery_input(&fixture.project);
    let mut sensitive = clean.clone();
    sensitive.payload["diagnostic"] = json!({"message": canary()});
    let before = fixture.snapshot();
    secret_rejected(
        fixture.store_mut().claim_recovery_stage(sensitive),
        ADMISSION_ERROR,
        "RECEIPT_SECRET_RECOVERY_CLAIM_ADMITTED",
    );
    fixture.assert_unchanged(&before);
    fixture.reopen();
    fixture.assert_unchanged(&before);
    let admitted = fixture.store_mut().claim_recovery_stage(clean).unwrap();
    assert!(admitted.stage == "dispatching");
    assert!(admitted.payload["attempt"] == 1);
}

#[test]
fn sensitive_retryable_checkpoint_cannot_release_an_unresolved_recovery_claim() {
    let mut fixture = Fixture::new();
    let claim = recovery_input(&fixture.project);
    let dispatch = fixture
        .store_mut()
        .claim_recovery_stage(claim.clone())
        .unwrap();
    let before = fixture.snapshot();
    secret_rejected(
        fixture
            .store_mut()
            .append_production_receipt(ProductionReceiptInput {
                stage: "failed_known".into(),
                payload: json!({
                    "attempt": 1,
                    "retryable": true,
                    "diagnostic": {"message": canary()}
                }),
                ..claim.clone()
            }),
        ADMISSION_ERROR,
        "RECEIPT_SECRET_RETRYABLE_CHECKPOINT_ADMITTED",
    );
    fixture.assert_unchanged(&before);
    fixture.reopen();
    fixture.assert_unchanged(&before);
    assert!(
        fixture
            .store()
            .production_receipts(fixture.project.id, 10)
            .unwrap()
            == vec![dispatch],
        "a refused retryable checkpoint cannot turn dispatching into failed_known"
    );
    for (key, digest) in [
        (claim.request_id.as_str(), "a".repeat(64)),
        ("renamed-recovery", "a".repeat(64)),
        (claim.request_id.as_str(), "c".repeat(64)),
        ("renamed-and-changed-recovery", "c".repeat(64)),
    ] {
        let retry = ProductionReceiptInput {
            request_id: key.into(),
            request_sha256: digest,
            ..claim.clone()
        };
        assert!(
            matches!(
                fixture.store_mut().claim_recovery_stage(retry),
                Err(StorageError::RequestReuse)
            ),
            "pending recovery must veto same, renamed and changed-fingerprint claims"
        );
        fixture.assert_unchanged(&before);
    }
}

fn reject_legacy_read_apis(
    fixture: &Fixture,
    key: &str,
    receipt_id: uuid::Uuid,
    witness: &'static str,
) {
    let before = fixture.snapshot();
    secret_rejected(
        fixture
            .store()
            .latest_production_receipt(fixture.project.id, key),
        LEGACY_ERROR,
        witness,
    );
    fixture.assert_unchanged(&before);
    secret_rejected(
        fixture.store().production_receipts(fixture.project.id, 10),
        LEGACY_ERROR,
        witness,
    );
    fixture.assert_unchanged(&before);
    secret_rejected(
        fixture
            .store()
            .production_receipts_before(fixture.project.id, receipt_id, None, 10),
        LEGACY_ERROR,
        witness,
    );
    fixture.assert_unchanged(&before);
}

#[test]
fn escaped_legacy_payload_is_not_exposed_by_any_receipt_read_api() {
    let mut fixture = Fixture::new();
    let clean = input(&fixture.project, "legacy-escaped-boundary");
    let accepted = fixture
        .store_mut()
        .append_production_receipt(clean.clone())
        .unwrap();
    // The raw SQL text deliberately lacks the recognized prefix. Validation
    // must cover the decoded value, not merely the serialized legacy JSON.
    let encoded = format!(
        r#"{{"mutation":true,"diagnostic":"\u0067hp_{}"}}"#,
        "A".repeat(24)
    );
    assert!(!encoded.contains(&canary()));
    let decoded: Value = serde_json::from_str(&encoded).unwrap();
    assert!(decoded["diagnostic"] == canary());
    {
        let writer = Connection::open(&fixture.path).unwrap();
        assert_eq!(
            writer
                .execute(
                    "UPDATE production_receipts SET payload_json=?1 WHERE receipt_id=?2",
                    params![encoded, accepted.id.to_string()],
                )
                .unwrap(),
            1
        );
    }
    fixture.reopen();
    reject_legacy_read_apis(
        &fixture,
        &clean.request_id,
        accepted.id,
        "RECEIPT_SECRET_LEGACY_ESCAPED_EXPOSED",
    );
}

fn rejected_legacy_text_field(sql: &str, changed_request_id: bool, witness: &'static str) {
    let mut fixture = Fixture::new();
    let clean = input(&fixture.project, "legacy-text-boundary");
    let accepted = fixture
        .store_mut()
        .append_production_receipt(clean.clone())
        .unwrap();
    let marker = canary();
    {
        let writer = Connection::open(&fixture.path).unwrap();
        assert_eq!(
            writer
                .execute(sql, params![&marker, accepted.id.to_string()])
                .unwrap(),
            1
        );
    }
    fixture.reopen();
    let key = if changed_request_id {
        marker.as_str()
    } else {
        clean.request_id.as_str()
    };
    reject_legacy_read_apis(&fixture, key, accepted.id, witness);
}

#[test]
fn legacy_request_id_is_not_exposed_or_rewritten() {
    rejected_legacy_text_field(
        "UPDATE production_receipts SET request_id=?1 WHERE receipt_id=?2",
        true,
        "RECEIPT_SECRET_LEGACY_REQUEST_ID_EXPOSED",
    );
}

#[test]
fn legacy_command_is_not_exposed_or_rewritten() {
    rejected_legacy_text_field(
        "UPDATE production_receipts SET command=?1 WHERE receipt_id=?2",
        false,
        "RECEIPT_SECRET_LEGACY_COMMAND_EXPOSED",
    );
}

#[test]
fn legacy_stage_is_not_exposed_or_rewritten() {
    rejected_legacy_text_field(
        "UPDATE production_receipts SET stage=?1 WHERE receipt_id=?2",
        false,
        "RECEIPT_SECRET_LEGACY_STAGE_EXPOSED",
    );
}

#[test]
fn legacy_created_at_is_not_exposed_or_rewritten() {
    rejected_legacy_text_field(
        "UPDATE production_receipts SET created_at=?1 WHERE receipt_id=?2",
        false,
        "RECEIPT_SECRET_LEGACY_CREATED_AT_EXPOSED",
    );
}

#[test]
fn ordinary_receipts_roundtrip_with_exact_payload_order_and_watermark() {
    let mut fixture = Fixture::new();
    let mut first = input(&fixture.project, "ordinary-first");
    first.payload["ordinary"] = json!({
        "text": "ghp_short",
        "array": [1, true, null, {"key": "unchanged"}]
    });
    let dispatch = fixture
        .store_mut()
        .append_production_receipt(first.clone())
        .unwrap();
    let terminal_payload = json!({
        "result": {"data": first.payload.clone(), "warnings": []},
        "broker_request_id": "ordinary-broker-request",
        "job_ref": "fixture-job"
    });
    let terminal = fixture
        .store_mut()
        .append_production_receipt(ProductionReceiptInput {
            stage: "completed".into(),
            payload: terminal_payload.clone(),
            ..first.clone()
        })
        .unwrap();
    let second = input(&fixture.project, "ordinary-second");
    let newest = fixture
        .store_mut()
        .append_production_receipt(second)
        .unwrap();
    let head = fixture
        .store()
        .production_receipt_watermark(fixture.project.id)
        .unwrap();
    assert!(head.count == 3 && head.latest_id == Some(newest.id));
    let before_reopen = fixture.snapshot();
    fixture.reopen();
    fixture.assert_unchanged(&before_reopen);
    assert!(
        fixture
            .store()
            .production_receipt_watermark(fixture.project.id)
            .unwrap()
            == head
    );
    let page = fixture
        .store()
        .production_receipts_before(fixture.project.id, newest.id, None, 2)
        .unwrap();
    assert!(page == vec![newest, terminal.clone()]);
    let older = fixture
        .store()
        .production_receipts_before(
            fixture.project.id,
            head.latest_id.unwrap(),
            Some(terminal.id),
            2,
        )
        .unwrap();
    assert!(older == vec![dispatch]);
    let latest = fixture
        .store()
        .latest_production_receipt(fixture.project.id, &first.request_id)
        .unwrap()
        .unwrap();
    assert!(latest == terminal && latest.payload == terminal_payload);
    assert!(
        fixture.store().load_project(fixture.project.id).unwrap() == fixture.project,
        "receipt observations cannot edit the creative project"
    );
    fixture.assert_unchanged(&before_reopen);
}

#[test]
fn clean_retryable_recovery_advances_only_after_an_explicit_new_claim() {
    let mut fixture = Fixture::new();
    let claim = recovery_input(&fixture.project);
    let first = fixture
        .store_mut()
        .claim_recovery_stage(claim.clone())
        .unwrap();
    assert!(first.payload["attempt"] == 1);
    assert!(matches!(
        fixture.store_mut().claim_recovery_stage(claim.clone()),
        Err(StorageError::RequestReuse)
    ));
    fixture
        .store_mut()
        .append_production_receipt(ProductionReceiptInput {
            stage: "failed_known".into(),
            payload: json!({"attempt": 1, "retryable": true, "proof": null}),
            ..claim.clone()
        })
        .unwrap();
    assert!(
        fixture
            .store()
            .production_receipt_watermark(fixture.project.id)
            .unwrap()
            .count
            == 2
    );
    let second = fixture
        .store_mut()
        .claim_recovery_stage(claim.clone())
        .unwrap();
    assert!(second.stage == "dispatching" && second.payload["attempt"] == 2);
    let proof = json!({"artifact": "safe-output.glb", "sha256": "d".repeat(64)});
    let completed = fixture
        .store_mut()
        .append_production_receipt(ProductionReceiptInput {
            stage: "completed".into(),
            payload: json!({"attempt": 2, "retryable": false, "proof": proof.clone()}),
            ..claim.clone()
        })
        .unwrap();
    let before_reopen = fixture.snapshot();
    fixture.reopen();
    fixture.assert_unchanged(&before_reopen);
    let latest = fixture
        .store()
        .latest_production_receipt(fixture.project.id, &claim.request_id)
        .unwrap()
        .unwrap();
    assert!(latest == completed);
    assert!(latest.payload["attempt"] == 2 && latest.payload["proof"] == proof);
    assert!(
        matches!(
            fixture.store_mut().claim_recovery_stage(claim),
            Err(StorageError::RequestReuse)
        ),
        "completed recovery cannot authorize another attempt"
    );
    fixture.assert_unchanged(&before_reopen);
}

#[test]
fn rejected_clean_future_revision_does_not_consume_the_dispatch_key() {
    let mut fixture = Fixture::new();
    let clean = input(&fixture.project, "clean-rejected-binding");
    let before = fixture.snapshot();
    let future = ProductionReceiptInput {
        revision: clean.revision + 1,
        ..clean.clone()
    };
    assert!(
        matches!(
            fixture.store_mut().append_production_receipt(future),
            Err(StorageError::Conflict { .. })
        ),
        "future revision must fail before admitting a dispatch"
    );
    fixture.assert_unchanged(&before);
    fixture.reopen();
    fixture.assert_unchanged(&before);
    let admitted = fixture
        .store_mut()
        .append_production_receipt(clean.clone())
        .unwrap();
    assert!(admitted.request_id == clean.request_id);
    assert!(
        fixture
            .store()
            .production_receipt_watermark(fixture.project.id)
            .unwrap()
            .count
            == 1
    );
}
