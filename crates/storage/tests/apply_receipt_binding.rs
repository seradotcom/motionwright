//! Owned SQLite regressions for durable known-request result integrity.
//! These cases never retry an UNKNOWN operation or invoke a provider.

use motionwright_domain::{Change, Project};
use motionwright_storage::{ApplyOutcome, Store};
use rusqlite::{Connection, params, types::Value};

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
        let rows = statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| row.get::<_, Value>(column))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap();
        rows.map(|row| row.unwrap()).collect()
    })
    .collect()
}

struct KnownRequest {
    // Drop the store before the owned temporary directory.
    store: Store,
    base: Project,
    change: Change,
    original: ApplyOutcome,
    _directory: tempfile::TempDir,
}

fn known_request() -> KnownRequest {
    let directory = tempfile::tempdir().unwrap();
    let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
    let base = store.create_named_project("Original project").unwrap();
    let change = Change::RenameProject {
        title: "First committed title".into(),
    };
    let original = store
        .apply(base.id, &base.stamp(), "known-first", &change)
        .unwrap();
    assert!(!original.replayed);
    KnownRequest {
        store,
        base,
        change,
        original,
        _directory: directory,
    }
}

fn reject_replaced_result(edit: impl FnOnce(&mut ApplyOutcome, &mut Store)) {
    let mut fixture = known_request();
    let mut replaced = fixture.original.clone();
    edit(&mut replaced, &mut fixture.store);
    let oracle = Connection::open(fixture.store.path()).unwrap();
    assert_eq!(
        oracle
            .execute(
                "UPDATE receipts SET result_json=?1 WHERE project_id=?2 AND request_id=?3",
                params![
                    serde_json::to_string(&replaced).unwrap(),
                    fixture.base.id.to_string(),
                    &fixture.original.request_id,
                ],
            )
            .unwrap(),
        1
    );
    // The request key and digest stay original. Compare all logical SQL rows
    // after deliberate fixture corruption, including the corrupt stored JSON.
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = fixture.store.apply(
        fixture.base.id,
        &fixture.base.stamp(),
        &fixture.original.request_id,
        &fixture.change,
    );
    assert_eq!(snapshot(&oracle), before);
    assert!(result.is_err(), "accepted corrupt known result: {result:?}");
}

#[test]
fn versioned_binding_rejects_same_change_from_a_later_execution() {
    reject_replaced_result(|outcome, store| {
        let original_key = outcome.request_id.clone();
        let same_change = Change::RenameProject {
            title: outcome.project.title.clone(),
        };
        let later = store
            .apply(
                outcome.project.id,
                &outcome.project.stamp(),
                "known-second-same-change",
                &same_change,
            )
            .unwrap();
        assert!(!later.replayed);
        assert_ne!(later.request_id, original_key);
        assert!(later.project.revision > outcome.project.revision);
        assert_eq!(later.project.id, outcome.project.id);
        assert_eq!(later.project.generation, outcome.project.generation);

        let oracle = Connection::open(store.path()).unwrap();
        let digests: (String, String) = oracle
            .query_row(
                "SELECT a.request_sha256,b.request_sha256
                 FROM receipts AS a JOIN receipts AS b ON a.project_id=b.project_id
                 WHERE a.project_id=?1 AND a.request_id=?2 AND b.request_id=?3",
                params![
                    outcome.project.id.to_string(),
                    &original_key,
                    &later.request_id
                ],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .unwrap();
        assert_eq!(
            digests.0, digests.1,
            "two known executions preserve the same canonical Change digest"
        );
        *outcome = later;
        outcome.request_id = original_key;
        outcome.project.validate().unwrap();
    });
}
