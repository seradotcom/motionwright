use motionwright_storage::{
    ProductionReceiptInput, RootBudgetUnits, RootDispatchBudgetInput, RootDispatchBudgetOutcome,
    RootOperationBudgetInput, STORAGE_SCHEMA_VERSION, StorageError, Store,
};
use rusqlite::{Connection, params};
use serde_json::json;
use std::sync::{Arc, Barrier};
use std::thread;
use uuid::Uuid;

fn ceilings(attempts: u64) -> RootBudgetUnits {
    RootBudgetUnits {
        attempts,
        model_calls: 2,
        render_frames: 20,
        output_bytes: 200,
    }
}
fn setup() -> (
    tempfile::TempDir,
    std::path::PathBuf,
    Store,
    motionwright_domain::Project,
) {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("project.sqlite3");
    let mut store = Store::open(&path).unwrap();
    assert_eq!(STORAGE_SCHEMA_VERSION, 6);
    let project = store.create_named_project("Budgeted project").unwrap();
    (temp, path, store, project)
}
fn start_root(
    store: &mut Store,
    project: &motionwright_domain::Project,
    operation_key: &str,
    limits: RootBudgetUnits,
) -> Uuid {
    store
        .create_root_operation_budget(RootOperationBudgetInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            operation_key: operation_key.into(),
            limits,
        })
        .unwrap()
        .root_id
}
fn dispatch(
    project: &motionwright_domain::Project,
    root_id: Uuid,
    request: &str,
    units: RootBudgetUnits,
) -> RootDispatchBudgetInput {
    RootDispatchBudgetInput {
        root_id,
        receipt: ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: request.into(),
            request_sha256: "a".repeat(64),
            command: "driver.motion-canvas.render.start".into(),
            stage: "dispatching".into(),
            payload: json!({"profile":"fixture"}),
        },
        units,
    }
}
fn one() -> RootBudgetUnits {
    RootBudgetUnits {
        attempts: 1,
        model_calls: 1,
        render_frames: 10,
        output_bytes: 100,
    }
}
fn debit_count(path: &std::path::Path) -> i64 {
    let conn = Connection::open(path).unwrap();
    conn.query_row(
        "SELECT COUNT(*) FROM production_operation_debits",
        [],
        |row| row.get(0),
    )
    .unwrap()
}
#[test]
fn root_is_explicit_and_same_operation_key_cannot_mint_credit() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "root-key", ceilings(2));
    assert_eq!(
        store
            .root_operation_budget(project.id, root)
            .unwrap()
            .spent
            .attempts,
        0
    );
    assert!(matches!(
        store.create_root_operation_budget(RootOperationBudgetInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            operation_key: "root-key".into(),
            limits: ceilings(999),
        }),
        Err(StorageError::RequestReuse)
    ));
    let reopened = Store::open(path).unwrap();
    assert_eq!(
        reopened
            .root_operation_budget(project.id, root)
            .unwrap()
            .limits,
        ceilings(2)
    );
}
#[test]
fn one_dispatch_debits_and_original_receipt_commit_atomically() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "original", ceilings(2));
    let admitted = store
        .admit_root_operation_dispatch(dispatch(&project, root, "first", one()))
        .unwrap();
    assert!(
        matches!(admitted, RootDispatchBudgetOutcome::Admitted(ref receipt)
        if receipt.stage == "dispatching" && receipt.request_id == "first")
    );
    assert_eq!(debit_count(&path), 1);
    let state = Store::open(path)
        .unwrap()
        .root_operation_budget(project.id, root)
        .unwrap();
    assert_eq!(state.spent, one());
}
#[test]
fn unknown_outcome_spends_capacity_and_cannot_be_retried_or_relabelled() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "original", ceilings(1));
    let original = dispatch(&project, root, "request-1", one());
    store
        .admit_root_operation_dispatch(original.clone())
        .unwrap();
    store
        .append_production_receipt(ProductionReceiptInput {
            stage: "outcome_unknown".into(),
            payload: json!({"outcome_known":false}),
            ..original.receipt.clone()
        })
        .unwrap();
    drop(store);
    let mut reopened = Store::open(&path).unwrap();
    assert!(matches!(
        reopened.admit_root_operation_dispatch(original),
        Err(StorageError::RequestReuse)
    ));
    assert!(
        reopened
            .admit_root_operation_dispatch(dispatch(
                &project,
                root,
                "different-child-or-retry-label",
                one()
            ))
            .is_err()
    );
    assert_eq!(debit_count(&path), 1);
    assert_eq!(
        reopened
            .root_operation_budget(project.id, root)
            .unwrap()
            .spent
            .attempts,
        1
    );
}
#[test]
fn completed_exact_replay_is_read_only_and_never_charged_twice() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "original", ceilings(1));
    let request = dispatch(&project, root, "original-request", one());
    store
        .admit_root_operation_dispatch(request.clone())
        .unwrap();
    store
        .append_production_receipt(ProductionReceiptInput {
            stage: "completed".into(),
            payload: json!({"native":"original result"}),
            ..request.receipt.clone()
        })
        .unwrap();
    let before = store.production_receipt_watermark(project.id).unwrap();
    let replay = store.admit_root_operation_dispatch(request).unwrap();
    assert!(
        matches!(replay, RootDispatchBudgetOutcome::AlreadyCompleted(ref receipt)
        if receipt.stage == "completed")
    );
    assert_eq!(
        store.production_receipt_watermark(project.id).unwrap(),
        before
    );
    assert_eq!(debit_count(&path), 1);
}
#[test]
fn all_budget_dimensions_are_enforced_not_only_attempt_count() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(
        &mut store,
        &project,
        "root",
        RootBudgetUnits {
            attempts: 8,
            model_calls: 1,
            render_frames: 11,
            output_bytes: 101,
        },
    );
    store
        .admit_root_operation_dispatch(dispatch(&project, root, "first", one()))
        .unwrap();
    for (index, units) in [
        RootBudgetUnits {
            attempts: 1,
            model_calls: 1,
            render_frames: 0,
            output_bytes: 0,
        },
        RootBudgetUnits {
            attempts: 1,
            model_calls: 0,
            render_frames: 2,
            output_bytes: 0,
        },
        RootBudgetUnits {
            attempts: 1,
            model_calls: 0,
            render_frames: 0,
            output_bytes: 2,
        },
    ]
    .into_iter()
    .enumerate()
    {
        assert!(
            store
                .admit_root_operation_dispatch(dispatch(
                    &project,
                    root,
                    &format!("exhausted-{index}"),
                    units
                ))
                .is_err()
        );
    }
    assert_eq!(debit_count(&path), 1);
    assert_eq!(
        store
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        1
    );
}
#[test]
fn legacy_writer_with_existing_open_connection_cannot_dispatch_to_enrolled_project() {
    let (_tmp, path, mut store, project) = setup();
    let old_writer = Connection::open(&path).unwrap();
    old_writer
        .busy_timeout(std::time::Duration::from_secs(3))
        .unwrap();
    let root = start_root(&mut store, &project, "root", ceilings(2));
    assert_eq!(
        store
            .root_operation_budget(project.id, root)
            .unwrap()
            .spent
            .attempts,
        0
    );
    let outcome = old_writer.execute(
        "INSERT INTO production_receipts(
          receipt_id,project_id,generation,revision,request_id,request_sha256,
          command,stage,payload_json,created_at)
         VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
        params![
            Uuid::now_v7().to_string(),
            project.id.to_string(),
            project.generation.to_string(),
            project.revision as i64,
            "unbudgeted-legacy-request",
            "a".repeat(64),
            "driver.motion-canvas.render.start",
            "dispatching",
            "{}",
            "now"
        ],
    );
    assert!(outcome.is_err(), "old open connection bypassed trigger");
    assert_eq!(debit_count(&path), 0);
    assert_eq!(
        store
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}
#[test]
fn failed_receipt_insert_rolls_back_debit_without_refund_or_unknown_replay() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "root", ceilings(1));
    let independent = Connection::open(&path).unwrap();
    independent
        .execute_batch(
            "CREATE TRIGGER force_receipt_failure AFTER INSERT ON production_receipts
         BEGIN SELECT RAISE(ABORT,'fixture failure after debit'); END;",
        )
        .unwrap();
    assert!(
        store
            .admit_root_operation_dispatch(dispatch(&project, root, "original", one()))
            .is_err()
    );
    assert_eq!(debit_count(&path), 0);
    assert_eq!(
        store
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}
#[test]
fn two_connections_compete_for_final_budget_only_one_admits() {
    let (_tmp, path, mut owner, project) = setup();
    let root = start_root(&mut owner, &project, "root", ceilings(1));
    let barrier = Arc::new(Barrier::new(2));
    let workers: Vec<_> = (0..2)
        .map(|i| {
            let path = path.clone();
            let project = project.clone();
            let barrier = barrier.clone();
            thread::spawn(move || {
                let mut store = Store::open(&path).unwrap();
                barrier.wait();
                store
                    .admit_root_operation_dispatch(dispatch(
                        &project,
                        root,
                        &format!("compete-{i}"),
                        one(),
                    ))
                    .is_ok()
            })
        })
        .collect();
    // Join each original attempt exactly once; never retry a losing mutation.
    let wins = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .filter(|won| *won)
        .count();
    assert_eq!(wins, 1);
    assert_eq!(debit_count(&path), 1);
    assert_eq!(
        owner
            .root_operation_budget(project.id, root)
            .unwrap()
            .spent
            .attempts,
        1
    );
}
#[test]
fn another_database_cannot_adopt_copied_root_id_as_new_authority() {
    let (_tmp, _path, mut original, project) = setup();
    let root = start_root(&mut original, &project, "owner-root", ceilings(1));
    let second_dir = tempfile::tempdir().unwrap();
    let mut second = Store::open(second_dir.path().join("separate.sqlite3")).unwrap();
    let other = second.create_named_project("Another project").unwrap();
    assert!(
        second
            .admit_root_operation_dispatch(dispatch(&other, root, "forged", one()))
            .is_err()
    );
}

#[test]
fn preexisting_unresolved_dispatch_blocks_new_root_in_same_project() {
    let (_tmp, _path, mut store, project) = setup();
    let original = dispatch(&project, Uuid::nil(), "original-legacy", one()).receipt;
    // Prior to creating a budget root, the historical receipt remains intact.
    store.append_production_receipt(original.clone()).unwrap();
    store
        .append_production_receipt(ProductionReceiptInput {
            stage: "outcome_unknown".into(),
            payload: json!({"outcome_known":false}),
            ..original.clone()
        })
        .unwrap();
    assert!(
        store
            .create_root_operation_budget(RootOperationBudgetInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                operation_key: "new-after-unknown".into(),
                limits: ceilings(2),
            })
            .is_err()
    );
    // A distinct, genuinely recorded terminal can later resolve the old history.
    // The root API never synthesizes that outcome itself.
    store
        .append_production_receipt(ProductionReceiptInput {
            stage: "completed".into(),
            payload: json!({"actual":"known"}),
            ..original
        })
        .unwrap();
    start_root(&mut store, &project, "new-after-known", ceilings(2));
}
#[test]
fn terminal_without_prior_is_not_a_way_around_enrolled_budget() {
    let (_tmp, path, mut store, project) = setup();
    start_root(&mut store, &project, "root", ceilings(2));
    let original = dispatch(&project, Uuid::nil(), "fresh-forged-terminal", one()).receipt;
    assert!(
        store
            .append_production_receipt(ProductionReceiptInput {
                stage: "completed".into(),
                ..original
            })
            .is_err()
    );
    assert_eq!(debit_count(&path), 0);
    assert_eq!(
        store
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}
#[test]
fn direct_debit_deletion_or_increment_cannot_refund_spent_root() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "root", ceilings(1));
    store
        .admit_root_operation_dispatch(dispatch(&project, root, "original", one()))
        .unwrap();
    let connection = Connection::open(&path).unwrap();
    for statement in [
        "UPDATE production_operation_debits SET attempts=0",
        "DELETE FROM production_operation_debits",
        "UPDATE production_operation_roots SET limit_attempts=100",
        "DELETE FROM production_operation_roots",
    ] {
        assert!(connection.execute_batch(statement).is_err());
    }
    assert_eq!(debit_count(&path), 1);
    assert_eq!(
        store
            .root_operation_budget(project.id, root)
            .unwrap()
            .spent
            .attempts,
        1
    );
}

#[test]
fn legacy_sqlite_writer_open_before_root_cannot_update_and_current_store_can_edit() {
    let (_tmp, path, mut current, project) = setup();
    let stale = Connection::open(&path).unwrap();
    // Read-only inspection remains possible for an earlier connection.
    assert_eq!(
        stale
            .query_row(
                "SELECT revision FROM projects WHERE id=?1",
                [project.id.to_string()],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        0
    );
    let root = start_root(&mut current, &project, "root", ceilings(2));
    assert!(
        stale
            .execute(
                "UPDATE projects SET revision=revision+1 WHERE id=?1 AND revision=0",
                [project.id.to_string()],
            )
            .is_err(),
        "A SQLite connection without the schema6 function bypassed migration"
    );
    // The current Store keeps the native render transaction closed, and normal
    // editorial work still makes progress under revision CAS.
    let saved = current
        .apply(
            project.id,
            &project.stamp(),
            "current-editor-still-live",
            &motionwright_domain::Change::AddScene {
                name: "Legitimate concurrent edit".into(),
                objective: "Prove no global writer lock".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    assert_eq!(saved.revision, project.revision + 1);
    assert!(
        current
            .admit_root_operation_dispatch(dispatch(&project, root, "now-stale-revision", one()))
            .is_err()
    );
    let reread = current.load_project(project.id).unwrap();
    assert_eq!(reread.revision, saved.revision);
}
#[test]
fn dropped_sqlite_connection_guard_blocks_store_reopen() {
    let (_tmp, path, store, project) = setup();
    drop(store);
    let raw = Connection::open(&path).unwrap();
    raw.execute_batch("DROP TRIGGER mw_writer_v6_projects_update;")
        .unwrap();
    drop(raw);
    assert!(matches!(
        Store::open(&path),
        Err(StorageError::InvalidBackup(_))
    ));
    // Failure to admit the malformed schema must not delete user data.
    let raw = Connection::open(&path).unwrap();
    assert_eq!(
        raw.query_row(
            "SELECT COUNT(*) FROM projects WHERE id=?1",
            [project.id.to_string()],
            |row| row.get::<_, i64>(0)
        )
        .unwrap(),
        1
    );
}
#[test]
fn connection_function_does_not_expose_broker_grant_or_permission() {
    let (_tmp, path, mut store, project) = setup();
    let root = start_root(&mut store, &project, "root", ceilings(1));
    // The UDF signifies Store-v6 compatibility only. Budget authority still
    // requires immutable root identity, matched debit and native consent.
    let wrong = Uuid::now_v7();
    assert_ne!(wrong, root);
    assert!(
        store
            .admit_root_operation_dispatch(dispatch(&project, wrong, "unbound", one()))
            .is_err()
    );
    assert_eq!(debit_count(&path), 0);
}

#[test]
fn schema6_cas_writer_fences_and_store_identity_survive_reopen_with_spent_debit() {
    let (_temp, path, mut store, project) = setup();
    let operation = start_root(
        &mut store,
        &project,
        "immutable-production-root",
        ceilings(2),
    );
    store
        .admit_root_operation_dispatch(dispatch(&project, operation, "original-render", one()))
        .unwrap();
    let connection = Connection::open(&path).unwrap();
    let identity_before: String = connection
        .query_row(
            "SELECT store_id FROM cas_store_identity WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    let triggers_before: i64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type='trigger'
             AND tbl_name IN ('cas_store_identity','cas_import_commits')",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(triggers_before, 6);
    drop(connection);
    drop(store);
    let restored = Store::open(&path).unwrap();
    assert_eq!(
        restored
            .root_operation_budget(project.id, operation)
            .unwrap()
            .spent
            .attempts,
        1
    );
    let post = Connection::open(&path).unwrap();
    let identity_after: String = post
        .query_row(
            "SELECT store_id FROM cas_store_identity WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(identity_before, identity_after);
    let triggers_after: i64 = post
        .query_row(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type='trigger'
             AND tbl_name IN ('cas_store_identity','cas_import_commits')",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(triggers_after, 6);
}

#[test]
fn missing_schema6_cas_fence_refuses_open_without_repairing_user_database() {
    let (_temp, path, store, project) = setup();
    drop(store);
    let raw = Connection::open(&path).unwrap();
    let identity_before: String = raw
        .query_row(
            "SELECT store_id FROM cas_store_identity WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    raw.execute_batch("DROP TRIGGER mw_writer_v6_cas_import_commits_delete;")
        .unwrap();
    assert_eq!(
        raw.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        6
    );
    drop(raw);
    assert!(Store::open(&path).is_err());
    let inspect = Connection::open(&path).unwrap();
    let identity_after: String = inspect
        .query_row(
            "SELECT store_id FROM cas_store_identity WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(identity_before, identity_after);
    assert_eq!(
        inspect
            .query_row(
                "SELECT COUNT(*) FROM projects WHERE id=?1",
                [project.id.to_string()],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        1
    );
    let missing: i64 = inspect
        .query_row("SELECT COUNT(*) FROM sqlite_schema WHERE name='mw_writer_v6_cas_import_commits_delete'",
            [], |row| row.get(0)).unwrap();
    assert_eq!(
        missing, 0,
        "failed Store::open must not synthesize missing authorization"
    );
}

#[test]
fn replacing_one_cas_fence_with_foreign_trigger_preserves_count_but_refuses_open() {
    let (_temp, path, store, _project) = setup();
    drop(store);
    let raw = Connection::open(&path).unwrap();
    raw.execute_batch(
        "DROP TRIGGER mw_writer_v6_cas_store_identity_update;
         CREATE TRIGGER foreign_cas_update BEFORE UPDATE ON cas_store_identity
         BEGIN SELECT 1; END;",
    )
    .unwrap();
    let still_six: i64 = raw
        .query_row(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type='trigger'
         AND tbl_name IN ('cas_store_identity','cas_import_commits')",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(still_six, 6);
    drop(raw);
    assert!(
        Store::open(&path).is_err(),
        "mere trigger count must not authorize CAS"
    );
}

#[test]
fn rewritten_canonical_cas_fence_body_refuses_open_without_repair() {
    let (_temp, path, store, _project) = setup();
    drop(store);
    let raw = Connection::open(&path).unwrap();
    raw.execute_batch(
        "DROP TRIGGER mw_writer_v6_cas_import_commits_update;
         CREATE TRIGGER mw_writer_v6_cas_import_commits_update
           BEFORE UPDATE ON cas_import_commits BEGIN SELECT 1; END;",
    )
    .unwrap();
    drop(raw);
    assert!(
        Store::open(&path).is_err(),
        "same name with wrong SQL is not the canonical fence"
    );
    let raw = Connection::open(&path).unwrap();
    let text: String = raw
        .query_row(
            "SELECT sql FROM sqlite_schema
         WHERE name='mw_writer_v6_cas_import_commits_update'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert!(
        text.contains("SELECT 1;"),
        "failure must not replace the foreign SQL"
    );
}

#[test]
fn invalid_or_null_writer_marker_rejects_a_valid_project_update_but_six_allows_it() {
    use rusqlite::functions::FunctionFlags;
    let (_temp, path, store, project) = setup();
    drop(store);
    let original = Connection::open(&path).unwrap();
    let timestamp_before: String = original
        .query_row(
            "SELECT updated_at FROM projects WHERE id=?1",
            [project.id.to_string()],
            |row| row.get(0),
        )
        .unwrap();
    drop(original);
    for marker in [Some(5_i64), None] {
        let writer = Connection::open(&path).unwrap();
        writer
            .create_scalar_function(
                "motionwright_writer_epoch_guard_v6",
                0,
                FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_INNOCUOUS,
                move |_| Ok(marker),
            )
            .unwrap();
        let result = writer.execute(
            "UPDATE projects SET updated_at='invalid-stamp'
             WHERE id=?1 AND revision=?2",
            params![project.id.to_string(), project.revision as i64],
        );
        assert!(
            result.is_err(),
            "5 or NULL must fail on an otherwise valid UPDATE"
        );
    }
    let writer = Connection::open(&path).unwrap();
    writer
        .create_scalar_function(
            "motionwright_writer_epoch_guard_v6",
            0,
            FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_INNOCUOUS,
            |_| Ok(6_i64),
        )
        .unwrap();
    let allowed = writer
        .execute(
            "UPDATE projects SET updated_at='writer-six'
         WHERE id=?1 AND revision=?2",
            params![project.id.to_string(), project.revision as i64],
        )
        .unwrap();
    assert_eq!(allowed, 1);
    let after: String = writer
        .query_row(
            "SELECT updated_at FROM projects WHERE id=?1",
            [project.id.to_string()],
            |row| row.get(0),
        )
        .unwrap();
    assert_ne!(timestamp_before, after);
    assert_eq!(after, "writer-six");
}

fn reconstruct_empty_budget_predecessor_schema5(path: &std::path::Path) {
    // Disposable fixture: preserve the real v5 common storage/CAS/receipt
    // objects, strip only the introduced v6 objects and set v5 user_version.
    // This is not evidence of an actual old executable or installed migration.
    let conn = Connection::open(path).unwrap();
    let names = {
        let mut query = conn
            .prepare(
                "SELECT name FROM sqlite_schema WHERE type='trigger'
                 AND (name LIKE 'mw_writer_v6_%' OR name LIKE 'production_operation_%')
                 ORDER BY name",
            )
            .unwrap();
        let found = query
            .query_map([], |row| row.get::<_, String>(0))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        found
    };
    assert_eq!(names.len(), 33, "27 writer fences plus six budget triggers");
    for name in names {
        assert!(name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'));
        conn.execute_batch(&format!("DROP TRIGGER {name}")).unwrap();
    }
    conn.execute_batch(
        "DROP TABLE production_operation_debits;
         DROP TABLE production_operation_roots;
         PRAGMA user_version=5;",
    )
    .unwrap();
    assert_eq!(
        conn.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        5
    );
}

#[test]
fn v5_prepared_statement_from_old_open_connection_is_fenced_after_actual_store_upgrade() {
    let (_temp, path, original_store, project) = setup();
    drop(original_store);
    reconstruct_empty_budget_predecessor_schema5(&path);
    let legacy = Connection::open(&path).unwrap();
    let mut prepared = legacy
        .prepare(
            "UPDATE projects SET updated_at='old-v5-writer'
             WHERE id=?1 AND revision=?2",
        )
        .unwrap();
    assert_eq!(prepared.execute(params!["nonexistent", 0_i64]).unwrap(), 0);
    let mut upgraded = Store::open(&path).unwrap();
    let old_attempt = prepared.execute(params![project.id.to_string(), project.revision as i64]);
    assert!(
        old_attempt.is_err(),
        "previously prepared v5 mutation crossed v6 barrier"
    );
    let id = start_root(&mut upgraded, &project, "upgraded-root", ceilings(2));
    assert_eq!(
        upgraded
            .root_operation_budget(project.id, id)
            .unwrap()
            .spent
            .attempts,
        0
    );
    assert_eq!(
        legacy
            .query_row(
                "SELECT revision FROM projects WHERE id=?1",
                [project.id.to_string()],
                |row| row.get::<_, i64>(0)
            )
            .unwrap(),
        project.revision as i64
    );
}

#[test]
fn active_predecessor_immediate_writer_prevents_partial_schema6_migration() {
    let (_temp, path, original, project) = setup();
    drop(original);
    reconstruct_empty_budget_predecessor_schema5(&path);
    let active = Connection::open(&path).unwrap();
    active.execute_batch("BEGIN IMMEDIATE").unwrap();
    // A new writer must not see an incomplete budget schema or changed
    // user_version while the legacy transaction still owns SQLite's lock.
    assert!(Store::open(&path).is_err());
    assert_eq!(
        active
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        5
    );
    let partial: i64 = active
        .query_row(
            "SELECT COUNT(*) FROM sqlite_schema
         WHERE name='production_operation_roots'
            OR name='mw_writer_v6_projects_update'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(partial, 0);
    active.execute_batch("ROLLBACK").unwrap();
    drop(active);
    let upgraded = Store::open(&path).unwrap();
    assert_eq!(
        upgraded.load_project(project.id).unwrap().revision,
        project.revision
    );
    assert_eq!(
        Connection::open(&path)
            .unwrap()
            .query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .unwrap(),
        6
    );
}

#[test]
fn real_cas_blob_and_import_marker_preserve_store_binding_after_reopen() {
    use motionwright_domain::{Asset, Change};
    let owned = tempfile::tempdir().unwrap();
    let original_bytes = b"retained source with a real blob, not an absent placeholder";
    let source_path = owned.path().join("owned-original.bin");
    std::fs::write(&source_path, original_bytes).unwrap();
    let mut source = Store::open(owned.path().join("source/state.sqlite3")).unwrap();
    let project = source
        .create_named_project("Source with retained CAS bytes")
        .unwrap();
    let blob = source.ingest_blob_file(&source_path).unwrap();
    let after_asset = source
        .apply(
            project.id,
            &project.stamp(),
            "add-original-blob",
            &Change::AddAsset {
                asset: Asset {
                    id: Uuid::new_v4(),
                    name: "Original owned fixture".into(),
                    media_type: "application/octet-stream".into(),
                    content_sha256: Some(blob.sha256.clone()),
                    source_revision: Some("owned-test-source".into()),
                },
            },
        )
        .unwrap()
        .project;
    let bundle = owned.path().join("bundle");
    source
        .export_project_bundle(after_asset.id, &bundle)
        .unwrap();
    let target_path = owned.path().join("target/state.sqlite3");
    let mut target = Store::open(&target_path).unwrap();
    let imported = target.import_project_bundle(&bundle).unwrap();
    assert_eq!(imported.id, after_asset.id);
    assert_eq!(
        target.read_blob(&blob.sha256, 1024).unwrap(),
        original_bytes
    );
    let connection = Connection::open(&target_path).unwrap();
    let marker: (String, String, String, String, String, String) = connection
        .query_row(
            "SELECT operation_id,root_id,store_id,request_sha256,project_id,generation
             FROM cas_import_commits",
            [],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .unwrap();
    let original_store_id: String = connection
        .query_row(
            "SELECT store_id FROM cas_store_identity WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(marker.2, original_store_id);
    assert_eq!(marker.4, imported.id.to_string());
    assert_eq!(marker.5, imported.generation.to_string());
    assert!(Uuid::parse_str(&marker.0).is_ok());
    assert!(Uuid::parse_str(&marker.1).is_ok());
    assert_eq!(marker.3.len(), 64);
    drop(connection);
    drop(target);
    let reopened = Store::open(&target_path).unwrap();
    assert_eq!(reopened.load_project(imported.id).unwrap(), imported);
    assert_eq!(
        reopened.read_blob(&blob.sha256, 1024).unwrap(),
        original_bytes
    );
    let readback = Connection::open(&target_path).unwrap();
    let later: (String, String, String, String, String, String) = readback
        .query_row(
            "SELECT operation_id,root_id,store_id,request_sha256,project_id,generation
         FROM cas_import_commits",
            [],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .unwrap();
    assert_eq!(
        marker, later,
        "reopening may not mint a different import marker"
    );
    let later_store_id: String = readback
        .query_row(
            "SELECT store_id FROM cas_store_identity WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(original_store_id, later_store_id);
}
