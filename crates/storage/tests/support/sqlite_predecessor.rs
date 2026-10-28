//! Test-only source-equivalent reconstruction of historical SQLite schemas.
//! Do not use as evidence of an actual historical binary migration.
use motionwright_storage::STORAGE_SCHEMA_VERSION;
use rusqlite::Connection;

pub(super) fn strip_schema_six_for_predecessor_fixture(db: &Connection) {
    let version: u32 = db
        .pragma_query_value(None, "user_version", |row| row.get(0))
        .unwrap();
    assert_eq!(version, STORAGE_SCHEMA_VERSION);
    for table in ["production_operation_roots", "production_operation_debits"] {
        let count: i64 = db
            .query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 0, "fixture cannot drop spent operation budget");
    }
    const TABLES: [&str; 9] = [
        "projects",
        "receipts",
        "events",
        "production_receipts",
        "production_receipt_order",
        "render_window_cache_entries",
        "render_window_cache_evictions",
        "cas_store_identity",
        "cas_import_commits",
    ];
    const BUDGET_TRIGGERS: [&str; 6] = [
        "production_operation_dispatch_gate",
        "production_operation_root_immutable",
        "production_operation_debit_immutable",
        "production_operation_root_nodelete",
        "production_operation_debit_nodelete",
        "production_operation_debit_budget_gate",
    ];
    let mut expected = std::collections::BTreeSet::new();
    let mut writer_fences = Vec::new();
    for table in TABLES {
        for event in ["INSERT", "UPDATE", "DELETE"] {
            let name = format!("mw_writer_v6_{table}_{}", event.to_ascii_lowercase());
            let expected_sql = format!(
                "CREATE TRIGGER {name} BEFORE {event} ON {table} BEGIN
                   SELECT RAISE(ABORT,'schema6 registered writer required')
                   WHERE motionwright_writer_epoch_guard_v6() IS NOT 6;
                 END"
            );
            let actual_sql: String = db
                .query_row(
                    "SELECT sql FROM sqlite_master WHERE name=?1 AND type='trigger'",
                    [&name],
                    |row| row.get(0),
                )
                .unwrap();
            let normalized = |sql: &str| {
                sql.trim_end_matches(';')
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ")
                    .to_ascii_lowercase()
            };
            assert_eq!(normalized(&actual_sql), normalized(&expected_sql));
            assert!(expected.insert(name.clone()));
            writer_fences.push(name);
        }
    }
    for name in BUDGET_TRIGGERS {
        assert!(expected.insert(name.into()));
    }
    let actual: std::collections::BTreeSet<String> = {
        let mut query = db
            .prepare(
                "SELECT name FROM sqlite_schema WHERE type='trigger'
                 AND (name LIKE 'mw_writer_v6_%' OR name LIKE 'production_operation_%')",
            )
            .unwrap();
        query
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<rusqlite::Result<_>>()
            .unwrap()
    };
    assert_eq!(expected.len(), 33);
    assert_eq!(
        actual, expected,
        "predecessor fixture must contain exact source-verified v6 triggers"
    );
    for name in expected {
        assert!(name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_'));
        db.execute_batch(&format!("DROP TRIGGER {name}")).unwrap();
    }
    db.execute_batch(
        "DROP TABLE production_operation_debits;
         DROP TABLE production_operation_roots;",
    )
    .unwrap();
}
