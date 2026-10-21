//! Source-equivalent schema-3 fixtures and physical admission invariants.
//! These are controlled SQLite histories, not execution of an older binary.
use motionwright_domain::{Change, Project};
use motionwright_storage::{
    ApplyOutcome, ApplyReceiptObservation, ProjectCreationIntent, STORAGE_SCHEMA_VERSION, Store,
};
use rusqlite::{Connection, types::Value};
use std::{
    fs,
    path::{Path, PathBuf},
};

const VERSION: &str =
    "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1))";
const JSON: &str = "apply_binding_json TEXT";

fn receipt_values(conn: &Connection) -> Vec<Vec<Value>> {
    let mut statement = conn
        .prepare(
            "SELECT project_id,request_id,request_sha256,result_json,created_at
         FROM receipts ORDER BY project_id,request_id",
        )
        .unwrap();
    statement
        .query_map([], |row| (0..5).map(|column| row.get(column)).collect())
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

struct LegacyHistory {
    creation: ProjectCreationIntent,
    initial: Project,
    change: Change,
    outcome: ApplyOutcome,
    receipts: Vec<Vec<Value>>,
}

fn source_equivalent_schema3(path: &Path) -> LegacyHistory {
    let mut store = Store::open(path).unwrap();
    let creation = ProjectCreationIntent {
        request_id: uuid::Uuid::now_v7(),
        title: "Retained creation".into(),
    };
    let initial = store.create_project_with_intent(&creation).unwrap().project;
    let change = Change::RenameProject {
        title: "Retained apply".into(),
    };
    let outcome = store
        .apply(initial.id, &initial.stamp(), "retained-apply", &change)
        .unwrap();
    drop(store);
    let conn = Connection::open(path).unwrap();
    let receipts = receipt_values(&conn);
    // Reconstruct the old source representation. Result/request JSON and event
    // history remain byte-identical; neither new column existed in schema 3.
    conn.execute_batch(
        "ALTER TABLE receipts DROP COLUMN apply_binding_json;
         ALTER TABLE receipts DROP COLUMN apply_binding_version;
         ALTER TABLE receipts ADD COLUMN retained_note TEXT;
         UPDATE receipts SET retained_note='untouched receipt extension';
         CREATE TABLE retained_extension(note TEXT NOT NULL);
         INSERT INTO retained_extension VALUES('untouched table extension');
         PRAGMA user_version=3;
         PRAGMA wal_checkpoint(TRUNCATE);
         PRAGMA journal_mode=DELETE;",
    )
    .unwrap();
    assert_eq!(receipt_values(&conn), receipts);
    drop(conn);
    LegacyHistory {
        creation,
        initial,
        change,
        outcome,
        receipts,
    }
}

#[derive(Debug, PartialEq)]
struct FileState {
    path: PathBuf,
    directory: bool,
    bytes: Vec<u8>,
}

fn physical_state(root: &Path) -> Vec<FileState> {
    fn walk(root: &Path, path: &Path, output: &mut Vec<FileState>) {
        for entry in fs::read_dir(path).unwrap() {
            let path = entry.unwrap().path();
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            let directory = metadata.is_dir();
            output.push(FileState {
                path: path.strip_prefix(root).unwrap().to_owned(),
                directory,
                bytes: if directory {
                    Vec::new()
                } else {
                    fs::read(&path).unwrap()
                },
            });
            if directory {
                walk(root, &path, output);
            }
        }
    }
    let mut output = Vec::new();
    walk(root, root, &mut output);
    output.sort_by(|a, b| a.path.cmp(&b.path));
    output
}

fn add_columns(path: &Path, version: Option<&str>, json: Option<&str>, extra: Option<&str>) {
    let conn = Connection::open(path).unwrap();
    for definition in [version, json, extra].into_iter().flatten() {
        conn.execute(&format!("ALTER TABLE receipts ADD COLUMN {definition}"), [])
            .unwrap();
    }
}

fn reject_without_physical_changes(directory: &Path, path: &Path) {
    let before = physical_state(directory);
    let result = Store::open(path);
    assert!(
        result.is_err(),
        "inadmissible binding schema opened successfully"
    );
    drop(result);
    assert_eq!(
        physical_state(directory),
        before,
        "rejection changed database, sidecars or files"
    );
}

#[test]
fn schema3_upgrade_preserves_both_legacy_families_and_extensions() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("legacy.sqlite3");
    let history = source_equivalent_schema3(&path);
    let mut store = Store::open(&path).unwrap();
    let conn = Connection::open(&path).unwrap();
    assert_eq!(
        conn.pragma_query_value(None, "user_version", |r| r.get::<_, u32>(0))
            .unwrap(),
        STORAGE_SCHEMA_VERSION
    );
    assert_eq!(receipt_values(&conn), history.receipts);
    let markers: Vec<(i64, Option<String>)> =
        {
            let mut statement = conn.prepare(
            "SELECT apply_binding_version,apply_binding_json FROM receipts ORDER BY request_id",
        ).unwrap();
            statement
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
                .unwrap()
                .map(|row| row.unwrap())
                .collect()
        };
    assert_eq!(markers, vec![(0, None), (0, None)]);
    assert_eq!(
        conn.query_row("SELECT note FROM retained_extension", [], |r| r
            .get::<_, String>(0))
            .unwrap(),
        "untouched table extension"
    );
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM receipts WHERE retained_note='untouched receipt extension'",
            [],
            |r| r.get::<_, i64>(0),
        )
        .unwrap(),
        2
    );
    let created = store
        .recover_project_creation(&history.creation)
        .unwrap()
        .unwrap();
    assert!(created.replayed);
    assert_eq!(created.project, history.initial);
    assert_eq!(
        store
            .observe_apply_receipt_v1(
                history.initial.id,
                &history.initial.stamp(),
                "retained-apply",
                &history.change,
                1,
            )
            .unwrap(),
        ApplyReceiptObservation::LegacyUnverified
    );
    let next = Change::RenameProject {
        title: "First schema4 apply".into(),
    };
    let new = store
        .apply(
            history.initial.id,
            &history.outcome.project.stamp(),
            "new-v1",
            &next,
        )
        .unwrap();
    assert!(matches!(
        store
            .observe_apply_receipt_v1(history.initial.id, &new.project.stamp(), "new-v1", &next, 1,)
            .unwrap(),
        ApplyReceiptObservation::KnownVerifiedV1 { .. }
    ));
    let replay = store
        .apply(
            history.initial.id,
            &new.project.stamp(),
            "retained-apply",
            &history.change,
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, history.outcome.project);
    assert_eq!(
        store
            .observe_apply_receipt_v1(
                history.initial.id,
                &new.project.stamp(),
                "retained-apply",
                &history.change,
                1,
            )
            .unwrap(),
        ApplyReceiptObservation::LegacyUnverified
    );
    let retained: (i64,Option<String>) = conn.query_row(
        "SELECT apply_binding_version,apply_binding_json FROM receipts WHERE request_id='retained-apply'",
        [], |r| Ok((r.get(0)?,r.get(1)?)),
    ).unwrap();
    assert_eq!(retained, (0, None));
    assert_eq!(store.load_project(history.initial.id).unwrap(), new.project);
}

#[test]
fn reserved_legacy_columns_are_rejected_before_wal_or_schema_changes() {
    for reserved in [
        "APPLY_BINDING_VERSION INTEGER",
        "APPLY_BINDING_JSON TEXT",
        "apply_binding_version INTEGER GENERATED ALWAYS AS (0) VIRTUAL",
        "apply_binding_json TEXT GENERATED ALWAYS AS ('{}') VIRTUAL",
    ] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("reserved.sqlite3");
        source_equivalent_schema3(&path);
        add_columns(&path, Some(reserved), None, None);
        reject_without_physical_changes(directory.path(), &path);
    }
}

#[test]
fn incomplete_or_misdeclared_schema4_columns_fail_before_wal() {
    for (version, json) in [
        (Some(VERSION), None),
        (None, Some(JSON)),
        (
            Some(
                "apply_binding_version TEXT NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1))",
            ),
            Some(JSON),
        ),
        (
            Some("apply_binding_version INTEGER DEFAULT 0 CHECK (apply_binding_version IN (0,1))"),
            Some(JSON),
        ),
        (
            Some(
                "apply_binding_version INTEGER NOT NULL DEFAULT '0' CHECK (apply_binding_version IN (0,1))",
            ),
            Some(JSON),
        ),
        (
            Some(
                "apply_binding_version INTEGER NOT NULL DEFAULT 1 CHECK (apply_binding_version IN (0,1))",
            ),
            Some(JSON),
        ),
        (Some(VERSION), Some("apply_binding_json BLOB")),
        (
            Some(VERSION),
            Some("apply_binding_json TEXT NOT NULL DEFAULT '{}'"),
        ),
        (
            Some(VERSION),
            Some("apply_binding_json TEXT DEFAULT 'NULL'"),
        ),
        (
            Some(VERSION),
            Some("apply_binding_json TEXT GENERATED ALWAYS AS ('{}') VIRTUAL"),
        ),
    ] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("shape.sqlite3");
        source_equivalent_schema3(&path);
        add_columns(&path, version, json, None);
        let conn = Connection::open(&path).unwrap();
        conn.pragma_update(None, "user_version", 4).unwrap();
        drop(conn);
        reject_without_physical_changes(directory.path(), &path);
    }
}

#[test]
fn noncanonical_marker_constraints_are_rejected_without_probing_writes() {
    for (version, extra) in [
        (
            "apply_binding_version INTEGER NOT NULL DEFAULT 0 /* CHECK (apply_binding_version IN (0,1)) */",
            None,
        ),
        (
            "apply_binding_version INTEGER NOT NULL DEFAULT 0",
            Some("note TEXT DEFAULT 'CHECK (apply_binding_version IN (0,1))'"),
        ),
        (
            "apply_binding_version INTEGER NOT NULL DEFAULT 0",
            Some("note INTEGER CHECK (apply_binding_version IN (0,1))"),
        ),
        (
            "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1) OR 1)",
            None,
        ),
        (
            "apply_binding_version INTEGER NOT NULL DEFAULT 0 CHECK (apply_binding_version IN (0,1,NULL))",
            None,
        ),
    ] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("constraint.sqlite3");
        source_equivalent_schema3(&path);
        add_columns(&path, Some(version), Some(JSON), extra);
        let conn = Connection::open(&path).unwrap();
        conn.pragma_update(None, "user_version", 4).unwrap();
        drop(conn);
        reject_without_physical_changes(directory.path(), &path);
    }
}

#[test]
fn real_quoted_constraint_and_harmless_extensions_remain_admissible() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("quoted.sqlite3");
    let history = source_equivalent_schema3(&path);
    add_columns(
        &path,
        Some(
            "\"APPLY_BINDING_VERSION\" INTEGER NOT NULL DEFAULT (0)
              CHECK ((\"APPLY_BINDING_VERSION\" /* actual constraint */ IN (1,0)))",
        ),
        Some("\"APPLY_BINDING_JSON\" TEXT DEFAULT NULL"),
        Some("note TEXT DEFAULT 'comma, parentheses() -- /* and ''quote'''"),
    );
    let conn = Connection::open(&path).unwrap();
    conn.pragma_update(None, "user_version", 4).unwrap();
    drop(conn);
    let store = Store::open(&path).unwrap();
    assert_eq!(
        store.load_project(history.initial.id).unwrap(),
        history.outcome.project
    );
    drop(store);
    let conn = Connection::open(&path).unwrap();
    assert_eq!(receipt_values(&conn), history.receipts);
}

#[test]
fn future_binding_schema_is_never_adopted_or_downgraded() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("future.sqlite3");
    source_equivalent_schema3(&path);
    add_columns(&path, Some(VERSION), Some(JSON), None);
    let conn = Connection::open(&path).unwrap();
    conn.pragma_update(None, "user_version", STORAGE_SCHEMA_VERSION + 1)
        .unwrap();
    drop(conn);
    reject_without_physical_changes(directory.path(), &path);
}
