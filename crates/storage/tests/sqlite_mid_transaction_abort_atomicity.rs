//! Product-backed fault injection at the final receipt write of Store::apply.
//! SQLite must roll back the earlier document UPDATE and journal INSERT together.
//! This tests a real in-process SQL ABORT, not a simulated OS crash or outbox.
use motionwright_domain::Change;
use motionwright_storage::{StorageError, Store};
use rusqlite::Connection;

const BLOCKED_REQUEST: &str = "transaction-abort-before-receipt";
const COMMITTED_REQUEST: &str = "fresh-authorized-commit-after-failure";

fn row_counts(database: &std::path::Path) -> (i64, i64, i64) {
    let conn = Connection::open(database).unwrap();
    conn.query_row(
        "SELECT
           (SELECT COUNT(*) FROM projects),
           (SELECT COUNT(*) FROM events),
           (SELECT COUNT(*) FROM receipts)",
        [],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    )
    .unwrap()
}

#[test]
fn receipt_insert_failure_rolls_back_project_and_journal_after_reopen() {
    let root = tempfile::tempdir().unwrap();
    let database = root.path().join("store.sqlite3");
    let mut store = Store::open(&database).unwrap();
    let project = store
        .create_named_project("Original durable content")
        .unwrap();
    let before = store.export_project(project.id).unwrap();
    assert_eq!(row_counts(&database), (1, 0, 0));
    drop(store);

    // A purpose-scoped SQLite trigger fails specifically at the LAST step
    // of Store::apply, after its project row and journal event were written
    // inside the same transaction.
    let conn = Connection::open(&database).unwrap();
    conn.execute_batch(
        "CREATE TRIGGER reject_test_receipt
         BEFORE INSERT ON receipts
         WHEN NEW.request_id='transaction-abort-before-receipt'
         BEGIN
            SELECT RAISE(ABORT, 'injected receipt write failure');
         END;",
    )
    .unwrap();
    drop(conn);

    let mut backend = Store::open(&database).unwrap();
    let change = Change::RenameProject {
        title: "Never partially publish this title".into(),
    };
    assert!(
        matches!(
            backend.apply(project.id, &project.stamp(), BLOCKED_REQUEST, &change),
            Err(StorageError::Sql(_))
        ),
        "the receipt failure was not propagated"
    );
    drop(backend);

    let mut reopened = Store::open(&database).unwrap();
    assert_eq!(reopened.load_project(project.id).unwrap(), project);
    assert!(
        reopened
            .event_records_since(project.id, 0, 256)
            .unwrap()
            .is_empty()
    );
    assert_eq!(row_counts(&database), (1, 0, 0));
    let observed = reopened.export_project(project.id).unwrap();
    assert_eq!(observed.project, before.project);
    assert_eq!(observed.events, before.events);
    assert_eq!(observed.payload_sha256, before.payload_sha256);
    drop(reopened);

    // The failed dispatch has a known error and observed rollback. Even here
    // do NOT automatically replay its original request or declare an UNKNOWN
    // operation safe to retry. A separate deliberate request exercises the
    // ordinary committed path after the trigger has been removed.
    let conn = Connection::open(&database).unwrap();
    conn.execute_batch("DROP TRIGGER reject_test_receipt;")
        .unwrap();
    drop(conn);

    let mut store = Store::open(&database).unwrap();
    let committed = store
        .apply(
            project.id,
            &project.stamp(),
            COMMITTED_REQUEST,
            &Change::RenameProject {
                title: "Committed replacement".into(),
            },
        )
        .unwrap();
    assert!(!committed.replayed);
    assert_eq!(committed.project.revision, 1);
    assert_eq!(row_counts(&database), (1, 1, 1));
    drop(store);

    let mut reopened = Store::open(&database).unwrap();
    assert_eq!(
        reopened.load_project(project.id).unwrap(),
        committed.project
    );
    let journal = reopened.event_records_since(project.id, 0, 256).unwrap();
    assert_eq!(journal.len(), 1);
    assert_eq!(journal[0].revision, 1);
    let original_receipt = reopened
        .apply(
            project.id,
            &project.stamp(),
            COMMITTED_REQUEST,
            &Change::RenameProject {
                title: "Committed replacement".into(),
            },
        )
        .unwrap();
    assert!(original_receipt.replayed);
    assert_eq!(original_receipt.project, committed.project);
    assert_eq!(row_counts(&database), (1, 1, 1));
}
