//! The same completion boundary used by ApplyHandler receives real service/SQLite
//! results. Known fixture commits are inspected; uncertain production requests
//! are never retried or repaired by these tests.
use super::{apply_completion, storage_error};
use motionwright_domain::{Change, Project};
use motionwright_service::StudioService;
use motionwright_storage::{ApplyOutcome, StorageError, Store};
use rusqlite::{Connection, params, types::Value};
use semwright_native_sdk::ErrorCode;
use semwright_native_sdk::cooperation::Completion;
use std::path::PathBuf;

type Snapshot = Vec<Vec<Vec<Value>>>;

fn snapshot(connection: &Connection) -> Snapshot {
    [
        "SELECT * FROM projects ORDER BY id",
        "SELECT * FROM events ORDER BY project_id,revision",
        "SELECT * FROM receipts ORDER BY project_id,request_id",
    ]
    .into_iter()
    .map(|sql| {
        let mut statement = connection.prepare(sql).unwrap();
        let columns = statement.column_count();
        statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| row.get::<_, Value>(column))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    })
    .collect()
}

struct Fixture {
    service: StudioService,
    base: Project,
    path: PathBuf,
    _directory: tempfile::TempDir,
}

fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("owned.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let base = store.create_named_project("Original title").unwrap();
    Fixture {
        service: StudioService::from_store(store),
        base,
        path,
        _directory: directory,
    }
}

fn intent() -> Change {
    Change::RenameProject {
        title: "Known committed title".into(),
    }
}

fn known_commit(f: &Fixture) -> ApplyOutcome {
    f.service
        .apply(f.base.id, &f.base.stamp(), "known-original", &intent())
        .unwrap()
}

fn applied(completion: Completion) -> ApplyOutcome {
    match completion {
        Completion::Applied(value) => {
            assert_eq!(value.as_object().unwrap().len(), 7);
            let project: Project = serde_json::from_value(value["project"].clone()).unwrap();
            assert_eq!(
                value["resource"].as_str(),
                Some(project.resource_key().as_str())
            );
            assert_eq!(
                value["generation"].as_str(),
                Some(project.generation.to_string().as_str())
            );
            assert_eq!(
                value["revision"].as_str(),
                Some(project.revision.to_string().as_str())
            );
            ApplyOutcome {
                project,
                previous_revision: value["previous_revision"]
                    .as_str()
                    .unwrap()
                    .parse()
                    .unwrap(),
                request_id: value["request_id"].as_str().unwrap().to_owned(),
                replayed: value["replayed"].as_bool().unwrap(),
            }
        }
        other => panic!("expected actual committed outcome, got {other:?}"),
    }
}

#[test]
fn invalid_receipt_error_is_constant_and_sanitized() {
    let marker = "synthetic-private-receipt-detail";
    let error = storage_error(StorageError::InvalidApplyReceipt(marker.into()));
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert_eq!(
        error.message,
        "Motionwright operation receipt validation failed"
    );
    assert!(!format!("{error:?}").contains(marker));
}

#[test]
fn new_commit_remains_applied_and_persisted() {
    let f = fixture();
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "new-known", &intent());
    let outcome = applied(apply_completion(result));
    assert!(!outcome.replayed);
    assert_eq!(outcome.project.revision, f.base.revision + 1);
    let reopened = Store::open(&f.path)
        .unwrap()
        .load_project(f.base.id)
        .unwrap();
    assert_eq!(reopened, outcome.project);
}

#[test]
fn verified_historical_result_keeps_original_revision_without_mutation() {
    let f = fixture();
    let original = known_commit(&f);
    let later = f
        .service
        .apply(
            f.base.id,
            &original.project.stamp(),
            "known-later",
            &Change::RenameProject {
                title: "Later owned edit".into(),
            },
        )
        .unwrap();
    let oracle = Connection::open(&f.path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "known-original", &intent());
    let outcome = applied(apply_completion(result));
    assert!(outcome.replayed);
    assert_eq!(outcome.project, original.project);
    assert_eq!(snapshot(&oracle), before);
    assert_eq!(
        Store::open(&f.path)
            .unwrap()
            .load_project(f.base.id)
            .unwrap(),
        later.project
    );
}

enum Damage {
    MalformedResult,
    WrongRequest,
    MalformedBinding,
}

fn corrupt_known_receipt_stays_uncertain(damage: Damage) {
    let f = fixture();
    let original = known_commit(&f);
    assert!(!original.replayed);
    let oracle = Connection::open(&f.path).unwrap();
    let (column, value) = match damage {
        Damage::MalformedResult => ("result_json", "{".to_owned()),
        Damage::WrongRequest => {
            let mut replaced = original.clone();
            replaced.request_id = "different-known-request".into();
            ("result_json", serde_json::to_string(&replaced).unwrap())
        }
        Damage::MalformedBinding => ("apply_binding_json", "{}".to_owned()),
    };
    assert_eq!(
        oracle
            .execute(
                &format!("UPDATE receipts SET {column}=?1 WHERE project_id=?2 AND request_id=?3"),
                params![value, f.base.id.to_string(), "known-original"],
            )
            .unwrap(),
        1
    );
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "known-original", &intent());
    assert!(matches!(&result, Err(StorageError::InvalidApplyReceipt(_))));
    assert_eq!(snapshot(&oracle), before);
    let current = Store::open(&f.path)
        .unwrap()
        .load_project(f.base.id)
        .unwrap();
    assert_eq!(
        current, original.project,
        "the original operation is known to have committed in this fixture"
    );
    let completion = apply_completion(result);
    assert!(
        matches!(&completion, Completion::Uncertain(_)),
        "INVALID_RECEIPT_MUST_NOT_ASSERT_NOT_APPLIED: {completion:?}"
    );
    let error = completion.into_result().unwrap_err();
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert_eq!(
        error.message,
        "Motionwright operation receipt validation failed"
    );
    assert!(
        !error.outcome_known,
        "SDK serialization must preserve original request uncertainty"
    );
    assert_eq!(
        snapshot(&oracle),
        before,
        "completion conversion must not mutate or retry"
    );
}

#[test]
fn malformed_historical_result_preserves_uncertainty() {
    corrupt_known_receipt_stays_uncertain(Damage::MalformedResult);
}

#[test]
fn foreign_historical_request_preserves_uncertainty() {
    corrupt_known_receipt_stays_uncertain(Damage::WrongRequest);
}

#[test]
fn malformed_historical_binding_preserves_uncertainty() {
    corrupt_known_receipt_stays_uncertain(Damage::MalformedBinding);
}

#[test]
fn new_stale_request_remains_known_rejected_without_mutation() {
    let f = fixture();
    known_commit(&f);
    let oracle = Connection::open(&f.path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "new-stale", &intent());
    assert!(matches!(&result, Err(StorageError::Conflict { .. })));
    let completion = apply_completion(result);
    assert!(matches!(&completion, Completion::NotApplied(_)));
    let error = completion.into_result().unwrap_err();
    assert_eq!(error.code, ErrorCode::StaleReference);
    assert!(error.outcome_known);
    assert_eq!(snapshot(&oracle), before);
}

#[test]
fn different_intent_under_known_key_remains_rejected_without_mutation() {
    let f = fixture();
    known_commit(&f);
    let oracle = Connection::open(&f.path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f.service.apply(
        f.base.id,
        &f.base.stamp(),
        "known-original",
        &Change::RenameProject {
            title: "A different operation".into(),
        },
    );
    assert!(matches!(&result, Err(StorageError::RequestReuse)));
    let completion = apply_completion(result);
    assert!(matches!(&completion, Completion::NotApplied(_)));
    let error = completion.into_result().unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(error.outcome_known);
    assert_eq!(snapshot(&oracle), before);
}
