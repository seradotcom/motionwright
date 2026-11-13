//! Historical-schema setup for Service reader regressions.
//!
//! The closed v6 seed is read only. A separate, newly created database receives
//! the exact v5 schema and logical rows, then the legacy-only mutation BEFORE
//! its first Store admission. The real Store performs the v5-to-v6 migration.
//! This is not evidence from an old producer binary, Native execution or CAS
//! reopen; fixtures with assets/imports are deliberately unsupported.

use motionwright_domain::Project;
use rusqlite::{Connection, OpenFlags, params_from_iter, types::Value};
use sha2::{Digest, Sha256};
use std::{fs, path::Path};

const V5_SQL: &str = include_str!("../../../storage/tests/fixtures/schema_v5.sql");
const V5_SQL_SHA256: &str = "835f31f8aad263a43f65ec873e73c7b730abc006b820ff41b180c30c18137e62";

// Parent rows precede their children. The v5 append trigger recreates receipt
// ordinals from the seed's committed sequence, never from UUIDs or wall clocks.
const TABLES: &[(&str, &str)] = &[
    ("cas_store_identity", "singleton"),
    ("projects", "id"),
    ("receipts", "project_id,request_id"),
    ("events", "event_id"),
    (
        "production_receipts",
        "(SELECT sequence FROM production_receipt_order AS committed
          WHERE committed.receipt_id=production_receipts.receipt_id)",
    ),
    (
        "render_window_cache_entries",
        "project_id,generation,revision,deliverable_id,source_start,source_end,fingerprint_sha256",
    ),
    (
        "render_window_cache_evictions",
        "project_id,generation,revision,deliverable_id,source_start,source_end,fingerprint_sha256",
    ),
    ("cas_import_commits", "operation_id"),
];

fn rows(db: &Connection, sql: &str) -> (Vec<String>, Vec<Vec<Value>>) {
    let mut statement = db.prepare(sql).unwrap();
    let count = statement.column_count();
    let columns = (0..count)
        .map(|index| statement.column_name(index).unwrap().to_owned())
        .collect();
    let values = statement
        .query_map([], |row| {
            (0..count)
                .map(|index| row.get(index))
                .collect::<rusqlite::Result<Vec<Value>>>()
        })
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    (columns, values)
}

fn assert_unadmitted_v5(db: &Connection) {
    assert_eq!(
        db.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        5
    );
    assert_eq!(
        db.query_row(
            "SELECT count(*) FROM sqlite_schema
             WHERE name GLOB 'production_operation_*' OR name GLOB 'mw_writer_v6_*'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        0,
        "a legacy fixture must not have been admitted by a v6 writer"
    );
}

pub fn from_closed_seed(seed: &Path, destination: &Path, prepare: impl FnOnce(&Connection)) {
    assert_ne!(seed, destination);
    assert_eq!(
        hex::encode(Sha256::digest(V5_SQL.as_bytes())),
        V5_SQL_SHA256
    );
    let original = fs::read(seed).unwrap();
    let source = Connection::open_with_flags(seed, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    source.execute_batch("BEGIN").unwrap();
    assert_eq!(
        source
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        6
    );
    for table in [
        "cas_import_commits",
        "production_operation_roots",
        "production_operation_debits",
        "render_window_cache_entries",
        "render_window_cache_evictions",
    ] {
        assert_eq!(
            source
                .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                    row.get::<_, i64>(0)
                })
                .unwrap(),
            0,
            "this legacy SQL fixture cannot relocate CAS or discard v6 state in {table}"
        );
    }
    for row in rows(&source, "SELECT document_json FROM projects ORDER BY id").1 {
        let Value::Text(document) = &row[0] else {
            panic!("expected a current API project document");
        };
        let project: Project = serde_json::from_str(document).unwrap();
        assert!(
            project.assets.is_empty(),
            "CAS assets require their real Store"
        );
    }

    // A new directory also gives Store::open its own parent/blobs root.
    // Never overwrite or demote an admitted database, reuse a CAS directory,
    // or install/remove a writer guard function.
    let destination_dir = destination.parent().unwrap();
    assert_ne!(seed.parent().unwrap(), destination_dir);
    fs::create_dir(destination_dir).unwrap();
    drop(fs::File::create_new(destination).unwrap());
    let legacy = Connection::open(destination).unwrap();
    legacy
        .execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
        .unwrap();
    legacy.execute_batch(V5_SQL).unwrap();
    legacy.pragma_update(None, "user_version", 5_i64).unwrap();
    assert_unadmitted_v5(&legacy);
    legacy.execute_batch("BEGIN IMMEDIATE").unwrap();
    for (table, order) in TABLES {
        let query = format!("SELECT * FROM {table} ORDER BY {order}");
        let (columns, values) = rows(&source, &query);
        assert_eq!(rows(&legacy, &query).0, columns);
        let placeholders = vec!["?"; columns.len()].join(",");
        let sql = format!(
            "INSERT INTO {table}({}) VALUES({placeholders})",
            columns.join(",")
        );
        let mut insert = legacy.prepare(&sql).unwrap();
        for row in &values {
            assert_eq!(insert.execute(params_from_iter(row)).unwrap(), 1);
        }
        assert_eq!(rows(&legacy, &query).1, values, "copied table {table}");
    }
    for query in [
        "SELECT * FROM production_receipt_order ORDER BY sequence",
        "SELECT name,seq FROM sqlite_sequence
         WHERE name IN ('events','production_receipt_order') AND seq > 0 ORDER BY name",
    ] {
        assert_eq!(
            rows(&legacy, query),
            rows(&source, query),
            "legacy setup must preserve committed ordinals and high water marks"
        );
    }
    let schema_version: i64 = legacy
        .query_row("PRAGMA schema_version", [], |row| row.get(0))
        .unwrap();
    prepare(&legacy);
    assert_unadmitted_v5(&legacy);
    assert_eq!(
        legacy
            .query_row("PRAGMA schema_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        schema_version,
        "legacy mutations may change rows, not admission or schema"
    );
    assert!(rows(&legacy, "PRAGMA foreign_key_check").1.is_empty());
    legacy.execute_batch("COMMIT").unwrap();
    let checkpoint: (i64, i64, i64) = legacy
        .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        })
        .unwrap();
    assert_eq!(checkpoint.0, 0);
    legacy.close().unwrap();
    source.close().unwrap();
    assert_eq!(
        fs::read(seed).unwrap(),
        original,
        "the v6 seed is immutable"
    );
}
