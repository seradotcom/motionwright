//! Existing foreign SQLite files must not be silently claimed by a project store.
//! The v1-positive fixture uses the historical three-table source layout,
//! not an executable-produced prior-binary fixture.
use motionwright_domain::Project;
use motionwright_storage::{STORAGE_SCHEMA_VERSION, StorageError, Store};
use rusqlite::{Connection, params};
use std::{fs, path::Path};

fn reject_foreign(path: &Path, ddl: &str, version: u32) {
    let db = Connection::open(path).unwrap();
    db.execute_batch(ddl).unwrap();
    db.pragma_update(None, "user_version", version).unwrap();
    drop(db);
    let before = fs::read(path).unwrap();
    let error = match Store::open(path) {
        Ok(_) => panic!("foreign database was adopted and modified"),
        Err(error) => error,
    };
    assert!(
        matches!(error, StorageError::InvalidBackup(_)),
        "foreign database should be identified before any DDL, got {error:?}"
    );
    assert_eq!(
        fs::read(path).unwrap(),
        before,
        "unrelated SQLite bytes changed"
    );
    assert!(!path.with_extension("sqlite3-wal").exists());
    assert!(!path.with_extension("sqlite3-shm").exists());
    let db = Connection::open(path).unwrap();
    let after_version: u32 = db
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(after_version, version);
    let product_tables: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master
             WHERE type='table' AND name IN ('projects','receipts','events','production_receipts')",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(
        product_tables,
        i64::from(ddl.contains("CREATE TABLE projects")),
        "new product tables were written into a foreign database"
    );
}

#[test]
fn foreign_unversioned_sqlite_is_not_replaced_or_extended() {
    let dir = tempfile::tempdir().unwrap();
    reject_foreign(
        &dir.path().join("foreign.sqlite3"),
        "CREATE TABLE accounting_records(record_id INTEGER PRIMARY KEY, payload TEXT);
         INSERT INTO accounting_records VALUES (1,'foreign user bytes');",
        0,
    );
}

#[test]
fn foreign_view_only_sqlite_is_not_mistaken_for_an_empty_database() {
    let dir = tempfile::tempdir().unwrap();
    reject_foreign(
        &dir.path().join("view-only.sqlite3"),
        "CREATE VIEW accounting_summary AS SELECT 42 AS revenue;",
        0,
    );
}

#[test]
fn existing_sqlite_with_no_user_schema_objects_can_initialize_normally() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("empty.sqlite3");
    let existing = Connection::open(&path).unwrap();
    existing.pragma_update(None, "user_version", 0).unwrap();
    drop(existing);

    let mut project_store = Store::open(&path).unwrap();
    let project = project_store
        .create_named_project("Genuine empty SQLite")
        .unwrap();
    drop(project_store);
    assert_eq!(
        Store::open(&path)
            .unwrap()
            .load_project(project.id)
            .unwrap(),
        project
    );
}

#[test]
fn foreign_versioned_sqlite_and_spoofed_projects_table_are_rejected() {
    for version in [1, STORAGE_SCHEMA_VERSION] {
        let dir = tempfile::tempdir().unwrap();
        reject_foreign(
            &dir.path().join("foreign.sqlite3"),
            "CREATE TABLE accounting_records(record_id INTEGER PRIMARY KEY, payload TEXT);
             INSERT INTO accounting_records VALUES (1,'do not change');",
            version,
        );
    }
    let dir = tempfile::tempdir().unwrap();
    reject_foreign(
        &dir.path().join("lookalike.sqlite3"),
        "CREATE TABLE projects(record_id INTEGER PRIMARY KEY, amount INTEGER NOT NULL);
         INSERT INTO projects VALUES(1, 42);",
        1,
    );
}

#[test]
fn real_v1_source_layout_still_upgrades_without_changing_project_identity() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("legacy.sqlite3");
    let project = Project::new("Existing editable project").unwrap();
    let original = serde_json::to_string(&project).unwrap();
    let db = Connection::open(&path).unwrap();
    // Source-equivalent v1 schema, derived from old public Motionwright
    // storage source; this is NOT evidence from a prior compiled executable.
    db.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE projects (
             id TEXT PRIMARY KEY, generation TEXT NOT NULL,
             revision INTEGER NOT NULL CHECK(revision >= 0),
             title TEXT NOT NULL, document_json TEXT NOT NULL, updated_at TEXT NOT NULL
         );
         CREATE TABLE receipts (
             project_id TEXT NOT NULL, request_id TEXT NOT NULL,
             request_sha256 TEXT NOT NULL, result_json TEXT NOT NULL,
             created_at TEXT NOT NULL, PRIMARY KEY(project_id,request_id),
             FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
         );
         CREATE TABLE events (
             event_id INTEGER PRIMARY KEY AUTOINCREMENT,
             project_id TEXT NOT NULL, revision INTEGER NOT NULL,
             kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
             UNIQUE(project_id,revision),
             FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
         );
         CREATE INDEX events_by_project_revision ON events(project_id,revision);
         CREATE TABLE user_owned_extension(note TEXT);
         INSERT INTO user_owned_extension VALUES('preserve custom note');
         PRAGMA user_version=1;",
    )
    .unwrap();
    db.execute(
        "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)
         VALUES(?1,?2,?3,?4,?5,?6)",
        params![
            project.id.to_string(),
            project.generation.to_string(),
            project.revision as i64,
            &project.title,
            original,
            project.updated_at.to_rfc3339()
        ],
    )
    .unwrap();
    drop(db);

    let store = Store::open(&path).unwrap();
    assert_eq!(store.load_project(project.id).unwrap(), project);
    drop(store);
    let db = Connection::open(&path).unwrap();
    let version: u32 = db
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(version, STORAGE_SCHEMA_VERSION);
    let preserved: String = db
        .query_row("SELECT note FROM user_owned_extension", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(preserved, "preserve custom note");
    let document: String = db
        .query_row(
            "SELECT document_json FROM projects WHERE id=?1",
            [project.id.to_string()],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(document, serde_json::to_string(&project).unwrap());
}
