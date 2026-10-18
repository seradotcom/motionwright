use motionwright_storage::{STORAGE_SCHEMA_VERSION, Store};
use rusqlite::Connection;

fn table_names(db: &Connection) -> Vec<String> {
    let mut stmt = db
        .prepare(
            "SELECT name FROM sqlite_master \
             WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
        .unwrap();
    stmt.query_map([], |row| row.get::<_, String>(0))
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

#[test]
fn failed_schema_upgrade_never_publishes_partial_new_tables_or_version() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("invalid-legacy.sqlite3");

    // Old but independently stored data is immutable even if a later DDL
    // statement fails. The old events table does not contain the columns
    // required by Motionwright's new events_by_project_revision index.
    let legacy = Connection::open(&path).unwrap();
    legacy
        .execute_batch(
            "CREATE TABLE events(event_id TEXT PRIMARY KEY, preserved TEXT NOT NULL);
             INSERT INTO events(event_id, preserved) VALUES('historical-event','unchanged');
             PRAGMA user_version=0;",
        )
        .unwrap();
    assert_eq!(table_names(&legacy), vec!["events"]);
    drop(legacy);

    let failure = Store::open(&path);
    assert!(
        failure.is_err(),
        "malformed legacy database must not be admitted"
    );

    // This reader does not use Store::open: it inspects the actual SQLite
    // backing file after the failed product admission without repairing it.
    let after = Connection::open(&path).unwrap();
    let version: u32 = after
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(version, 0, "failed upgrade cannot advance its schema stamp");
    assert_eq!(
        table_names(&after),
        vec!["events"],
        "a rejected schema upgrade must roll back EVERY attempted DDL statement"
    );
    let preserved: String = after
        .query_row(
            "SELECT preserved FROM events WHERE event_id='historical-event'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(preserved, "unchanged");
}

#[test]
fn valid_fresh_open_and_reopen_keep_exact_schema_and_no_extra_objects() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("fresh.sqlite3");
    let initial = Store::open(&path).unwrap();
    drop(initial);
    let sqlite = Connection::open(&path).unwrap();
    let first_version: u32 = sqlite
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(first_version, STORAGE_SCHEMA_VERSION);
    let first_objects = table_names(&sqlite);
    assert!(first_objects.contains(&"projects".to_owned()));
    assert!(first_objects.contains(&"production_receipts".to_owned()));
    drop(sqlite);

    let reopened = Store::open(&path).unwrap();
    drop(reopened);
    let verified = Connection::open(&path).unwrap();
    assert_eq!(table_names(&verified), first_objects);
    let version: u32 = verified
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(version, STORAGE_SCHEMA_VERSION);
}

#[test]
fn too_new_version_fails_without_rewriting_original_schema() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("future.sqlite3");
    let before = Connection::open(&path).unwrap();
    before
        .execute_batch(&format!(
            "CREATE TABLE unrelated_original(value TEXT);
             INSERT INTO unrelated_original VALUES('keep');
             PRAGMA user_version={};",
            STORAGE_SCHEMA_VERSION + 1
        ))
        .unwrap();
    drop(before);
    assert!(Store::open(&path).is_err());
    let after = Connection::open(&path).unwrap();
    assert_eq!(table_names(&after), vec!["unrelated_original"]);
    let value: String = after
        .query_row("SELECT value FROM unrelated_original", [], |row| row.get(0))
        .unwrap();
    assert_eq!(value, "keep");
}
