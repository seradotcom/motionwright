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

fn receipt_input(
    project: &motionwright_domain::Project,
    key: &str,
) -> motionwright_storage::ProductionReceiptInput {
    motionwright_storage::ProductionReceiptInput {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: key.into(),
        request_sha256: "a".repeat(64),
        command: "driver.motion-canvas.render.status".into(),
        stage: "completed".into(),
        payload: serde_json::json!({"preserved": key}),
    }
}

fn retained_v2_store(
    path: &std::path::Path,
) -> (
    motionwright_domain::Project,
    Vec<motionwright_storage::ProductionReceipt>,
) {
    let mut store = Store::open(path).unwrap();
    let project = store
        .create_named_project("Retained schema2 receipts")
        .unwrap();
    let first = store
        .append_production_receipt(receipt_input(&project, "first"))
        .unwrap();
    let second = store
        .append_production_receipt(receipt_input(&project, "second"))
        .unwrap();
    drop(store);
    let legacy = Connection::open(path).unwrap();
    legacy
        .execute_batch(
            "DROP TRIGGER production_receipt_order_append;
         DROP TABLE production_receipt_order;
         ALTER TABLE receipts DROP COLUMN apply_binding_json;
         ALTER TABLE receipts DROP COLUMN apply_binding_version;
         PRAGMA user_version=2;",
        )
        .unwrap();
    (project, vec![first, second])
}

fn schema_objects(db: &Connection) -> Vec<(String, String, Option<String>)> {
    let mut statement = db.prepare(
        "SELECT type,name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name",
    ).unwrap();
    statement
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

fn raw_receipts(db: &Connection) -> Vec<Vec<rusqlite::types::Value>> {
    let mut statement = db
        .prepare(
            "SELECT rowid,receipt_id,project_id,generation,revision,request_id,request_sha256,
                command,stage,payload_json,created_at FROM production_receipts ORDER BY rowid",
        )
        .unwrap();
    statement
        .query_map([], |row| (0..11).map(|index| row.get(index)).collect())
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

fn sqlite_sequences(db: &Connection) -> Vec<(String, i64)> {
    let mut statement = db
        .prepare("SELECT name,seq FROM sqlite_sequence ORDER BY name")
        .unwrap();
    statement
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .unwrap()
        .map(|row| row.unwrap())
        .collect()
}

#[test]
fn production_order_migration_preserves_receipts_extension_foreign_keys_and_audit_triggers() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("extensions.sqlite3");
    let (project, mut receipts) = retained_v2_store(&path);
    let db = Connection::open(&path).unwrap();
    // Retain an earlier row whose UUID and date exceed those of the later row.
    let original_id = receipts[0].id;
    receipts[0].id = uuid::Uuid::from_u128(original_id.as_u128() + (86_400_000_u128 << 80));
    receipts[0].created_at = "2100-01-01T00:00:00Z".into();
    db.execute(
        "UPDATE production_receipts SET receipt_id=?1,created_at=?2 WHERE receipt_id=?3",
        rusqlite::params![
            receipts[0].id.to_string(),
            &receipts[0].created_at,
            original_id.to_string()
        ],
    )
    .unwrap();
    db.execute_batch(
        "PRAGMA foreign_keys=ON;
         CREATE TABLE extension_notes(
           receipt_id TEXT NOT NULL REFERENCES production_receipts(receipt_id), note TEXT NOT NULL);
         CREATE TABLE extension_audit(receipt_id TEXT NOT NULL,stage TEXT NOT NULL);
         CREATE TRIGGER extension_receipt_audit AFTER INSERT ON production_receipts
         BEGIN INSERT INTO extension_audit(receipt_id,stage) VALUES(NEW.receipt_id,NEW.stage); END;",
    ).unwrap();
    db.execute(
        "INSERT INTO extension_notes VALUES(?1,'retained note')",
        [receipts[0].id.to_string()],
    )
    .unwrap();
    db.execute(
        "INSERT INTO extension_audit VALUES(?1,'original audit')",
        [receipts[0].id.to_string()],
    )
    .unwrap();
    // The open migrates schema2 through both receipt-order v3 and binding v4.
    // Independently derive the only permitted original DDL change with SQLite;
    // every other object and the original receipt bytes remain exact or fail.
    // Do not call the product migrator to construct this contractual oracle.
    let original_objects: Vec<_> = schema_objects(&db)
        .into_iter()
        .map(|(kind, name, sql)| {
            if kind == "table" && name == "receipts" {
                let expected = Connection::open_in_memory().unwrap();
                expected.execute_batch(sql.as_deref().unwrap()).unwrap();
                expected
                    .execute_batch(
                        "ALTER TABLE receipts ADD COLUMN apply_binding_version INTEGER NOT NULL DEFAULT 0
         CHECK (apply_binding_version IN (0, 1));
                         ALTER TABLE receipts ADD COLUMN apply_binding_json TEXT;",
                    )
                    .unwrap();
                let expected_sql: String = expected
                    .query_row(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name='receipts'",
                        [],
                        |row| row.get(0),
                    )
                    .unwrap();
                (kind, name, Some(expected_sql))
            } else {
                (kind, name, sql)
            }
        })
        .collect();
    let original_rows = raw_receipts(&db);
    drop(db);

    let mut migrated = Store::open(&path).unwrap();
    assert_eq!(
        migrated.production_receipts(project.id, 10).unwrap(),
        vec![receipts[1].clone(), receipts[0].clone()]
    );
    assert_eq!(
        migrated
            .production_receipt_watermark(project.id)
            .unwrap()
            .latest_id,
        Some(receipts[1].id)
    );
    let reader = Connection::open(&path).unwrap();
    assert_eq!(raw_receipts(&reader), original_rows);
    for original in original_objects {
        assert!(
            schema_objects(&reader).contains(&original),
            "migration replaced an original object: {original:?}"
        );
    }
    let audit_count: i64 = reader
        .query_row("SELECT COUNT(*) FROM extension_audit", [], |row| row.get(0))
        .unwrap();
    assert_eq!(
        audit_count, 1,
        "backfill must not replay old INSERT audit triggers"
    );
    let note: String = reader
        .query_row("SELECT note FROM extension_notes", [], |row| row.get(0))
        .unwrap();
    assert_eq!(note, "retained note");
    let foreign_key_errors: i64 = reader
        .query_row("SELECT COUNT(*) FROM pragma_foreign_key_check", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(foreign_key_errors, 0);
    let third = migrated
        .append_production_receipt(receipt_input(&project, "third"))
        .unwrap();
    assert_eq!(
        reader
            .query_row("SELECT COUNT(*) FROM extension_audit", [], |row| row
                .get::<_, i64>(0))
            .unwrap(),
        2
    );
    let sequences = sqlite_sequences(&reader);
    drop(reader);
    drop(migrated);
    let raw = Connection::open(&path).unwrap();
    raw.execute_batch("VACUUM").unwrap();
    drop(raw);
    let reopened = Store::open(&path).unwrap();
    assert_eq!(
        reopened.production_receipts(project.id, 10).unwrap(),
        vec![third, receipts[1].clone(), receipts[0].clone()]
    );
    let verified = Connection::open(&path).unwrap();
    assert_eq!(
        sqlite_sequences(&verified),
        sequences,
        "reopen must not assign fresh ordinals"
    );
}

#[test]
fn production_order_backfill_failure_rolls_back_ddl_rows_and_sequence_counter() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("backfill-null.sqlite3");
    let (_, receipts) = retained_v2_store(&path);
    let before = Connection::open(&path).unwrap();
    // SQLite's legacy TEXT PRIMARY KEY admits NULL; the ordinal mapping cannot.
    before
        .execute(
            "INSERT INTO production_receipts(
           receipt_id,project_id,generation,revision,request_id,request_sha256,
           command,stage,payload_json,created_at)
         SELECT NULL,project_id,generation,revision,'legacy-null-id',request_sha256,
                command,stage,payload_json,created_at
         FROM production_receipts WHERE receipt_id=?1",
            [receipts[0].id.to_string()],
        )
        .unwrap();
    let objects = schema_objects(&before);
    let rows = raw_receipts(&before);
    let sequences = sqlite_sequences(&before);
    drop(before);
    assert!(
        Store::open(&path).is_err(),
        "real NOT NULL backfill failure must reject migration"
    );
    let after = Connection::open(&path).unwrap();
    assert_eq!(
        after
            .pragma_query_value(None, "user_version", |row| row.get::<_, u32>(0))
            .unwrap(),
        2
    );
    assert_eq!(
        schema_objects(&after),
        objects,
        "no partial table/index/trigger publication"
    );
    assert_eq!(
        raw_receipts(&after),
        rows,
        "migration cannot repair or delete legacy bytes"
    );
    assert_eq!(
        sqlite_sequences(&after),
        sequences,
        "failed backfill must roll back sequence allocation"
    );
}

#[test]
fn production_order_reserved_object_collisions_are_rejected_without_adoption() {
    for (index, collision) in [
        "CREATE VIEW PrOdUcTiOn_ReCeIpT_OrDeR AS SELECT 'retained' AS value",
        "CREATE TABLE production_receipt_order_by_project(value TEXT)",
        "CREATE INDEX production_receipt_order_append ON production_receipts(stage)",
        "CREATE TRIGGER production_receipt_order BEFORE INSERT ON production_receipts BEGIN SELECT 1; END",
    ].into_iter().enumerate() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join(format!("collision-{index}.sqlite3"));
        retained_v2_store(&path);
        let before = Connection::open(&path).unwrap();
        before.execute_batch(collision).unwrap();
        let objects = schema_objects(&before);
        let rows = raw_receipts(&before);
        drop(before);
        assert!(Store::open(&path).is_err(), "must not adopt reserved foreign object: {collision}");
        let after = Connection::open(&path).unwrap();
        assert_eq!(schema_objects(&after), objects);
        assert_eq!(raw_receipts(&after), rows);
        assert_eq!(after.pragma_query_value(None, "user_version", |row| row.get::<_, u32>(0)).unwrap(), 2);
    }
}

#[test]
fn production_order_concurrent_migrations_publish_one_mapping_per_receipt() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("concurrent-migration.sqlite3");
    let (project, receipts) = retained_v2_store(&path);
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(3));
    let mut handles = Vec::new();
    for _ in 0..2 {
        let path = path.clone();
        let barrier = barrier.clone();
        handles.push(std::thread::spawn(move || {
            barrier.wait();
            let store = Store::open(&path).unwrap();
            store.production_receipts(project.id, 10).unwrap()
        }));
    }
    barrier.wait();
    for handle in handles {
        assert_eq!(
            handle.join().unwrap(),
            vec![receipts[1].clone(), receipts[0].clone()]
        );
    }
    let after = Connection::open(&path).unwrap();
    assert_eq!(
        after
            .pragma_query_value(None, "user_version", |row| row.get::<_, u32>(0))
            .unwrap(),
        STORAGE_SCHEMA_VERSION
    );
    assert_eq!(
        after
            .query_row("SELECT COUNT(*) FROM production_receipt_order", [], |row| {
                row.get::<_, i64>(0)
            })
            .unwrap(),
        2
    );
}

#[test]
fn production_order_missing_mapping_is_never_silently_backfilled_or_hidden() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("missing-mapping.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let project = store.create_named_project("Missing mapping").unwrap();
    let receipt = store
        .append_production_receipt(receipt_input(&project, "original"))
        .unwrap();
    let raw = Connection::open(&path).unwrap();
    raw.execute(
        "DELETE FROM production_receipt_order WHERE receipt_id=?1",
        [receipt.id.to_string()],
    )
    .unwrap();
    let before = raw_receipts(&raw);
    assert!(store.production_receipt_watermark(project.id).is_err());
    assert!(
        store
            .latest_production_receipt(project.id, "original")
            .is_err()
    );
    assert!(
        store
            .production_receipts_before(project.id, receipt.id, None, 10)
            .is_err()
    );
    assert!(
        store
            .append_production_receipt(receipt_input(&project, "must-not-append"))
            .is_err()
    );
    assert_eq!(raw_receipts(&raw), before);
    drop(store);
    assert!(Store::open(&path).is_err());
    assert_eq!(
        raw.query_row("SELECT COUNT(*) FROM production_receipt_order", [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap(),
        0
    );
    assert_eq!(raw_receipts(&raw), before);
}

#[test]
fn production_order_project_mismatch_is_rejected_from_both_scopes() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("wrong-project-mapping.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let first = store.create_named_project("Actual owner").unwrap();
    let other = store.create_named_project("Wrong mapping owner").unwrap();
    let receipt = store
        .append_production_receipt(receipt_input(&first, "original"))
        .unwrap();
    let raw = Connection::open(&path).unwrap();
    raw.execute(
        "UPDATE production_receipt_order SET project_id=?1 WHERE receipt_id=?2",
        rusqlite::params![other.id.to_string(), receipt.id.to_string()],
    )
    .unwrap();
    let before = raw_receipts(&raw);
    assert!(store.production_receipt_watermark(first.id).is_err());
    assert!(store.production_receipt_watermark(other.id).is_err());
    drop(store);
    assert!(Store::open(&path).is_err());
    assert_eq!(raw_receipts(&raw), before);
}

#[test]
fn production_order_allocation_failure_cannot_publish_a_partial_append_or_claim() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("ordinal-overflow.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let project = store
        .create_named_project("Ordinal allocation failure")
        .unwrap();
    store
        .append_production_receipt(receipt_input(&project, "original"))
        .unwrap();
    let raw = Connection::open(&path).unwrap();
    let before = raw_receipts(&raw);
    let watermark = store.production_receipt_watermark(project.id).unwrap();
    raw.execute(
        "UPDATE sqlite_sequence SET seq=?1 WHERE name='production_receipt_order'",
        [i64::MAX],
    )
    .unwrap();
    assert!(
        store
            .append_production_receipt(receipt_input(&project, "new-append"))
            .is_err()
    );
    assert_eq!(raw_receipts(&raw), before);
    let input = motionwright_storage::ProductionReceiptInput {
        command: "motionwright.recovery.motion-canvas".into(),
        stage: "dispatching".into(),
        ..receipt_input(&project, "new-claim")
    };
    assert!(store.claim_recovery_stage(input).is_err());
    assert_eq!(raw_receipts(&raw), before);
    assert_eq!(
        store.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(
        raw.query_row("SELECT COUNT(*) FROM production_receipt_order", [], |row| {
            row.get::<_, i64>(0)
        })
        .unwrap(),
        1
    );
}

#[test]
fn production_order_extension_trigger_failure_rolls_back_every_receipt_effect() {
    for rejection in ["IGNORE", "FAIL"] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("extension-interruption.sqlite3");
        let mut store = Store::open(&path).unwrap();
        let project = store.create_named_project("Extension failure").unwrap();
        store
            .append_production_receipt(receipt_input(&project, "original"))
            .unwrap();
        let raw = Connection::open(&path).unwrap();
        let before = raw_receipts(&raw);
        let watermark = store.production_receipt_watermark(project.id).unwrap();
        let raise = if rejection == "IGNORE" {
            "RAISE(IGNORE)"
        } else {
            "RAISE(FAIL,'extension rejected')"
        };
        raw.execute_batch(&format!(
            "CREATE TRIGGER extension_interrupt AFTER INSERT ON production_receipts
             BEGIN DELETE FROM production_receipt_order WHERE receipt_id=NEW.receipt_id;
               SELECT {raise}; END;"
        ))
        .unwrap();
        assert!(
            store
                .append_production_receipt(receipt_input(&project, "rejected"))
                .is_err(),
            "{rejection} must not leave a parent without an ordinal"
        );
        assert_eq!(raw_receipts(&raw), before);
        assert_eq!(
            store.production_receipt_watermark(project.id).unwrap(),
            watermark
        );
        raw.execute_batch("DROP TRIGGER extension_interrupt")
            .unwrap();
        let accepted = store
            .append_production_receipt(receipt_input(&project, "rejected"))
            .unwrap();
        assert_eq!(
            store
                .production_receipt_watermark(project.id)
                .unwrap()
                .latest_id,
            Some(accepted.id)
        );
    }
}
