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

// Source-pinned schema-five controls. The SQL fixture proves layout/migration
// structure only. The separately selected real-import case requires the actual
// v5 Store producer and the owner lane's build/execution evidence.
mod original_v5_causal_controls {
    use super::*;
    use rusqlite::{functions::FunctionFlags, types::ValueRef};
    use serde_json::Value;
    use sha2::{Digest, Sha256};
    use std::{
        fs,
        path::{Component, Path, PathBuf},
        time::Duration,
    };

    const V5_SOURCE: &str = "246873f2d908d0f801c990963a2f570934f29b83";
    const V5_SQL_SHA256: &str = "835f31f8aad263a43f65ec873e73c7b730abc006b820ff41b180c30c18137e62";
    const ORIGINAL: &[u8] =
        b"Actual schema-five public import fixture: retain these exact CAS bytes.\n";

    // Extracted from the original lib.rs base DDL + receipt-order schema,
    // apply_receipt_binding.rs ALTER statements and cas_retention.rs v5 DDL.
    // Source blobs: 3e690325e203ea2876432156d969e87ac11fdbb7,
    // 5dcc1da33077844afc72290a01d1510a68648529,
    // 2291f815227523a900e7591521dfc2f9b2af53b8.
    const V5_SQL: &str = r###"
            CREATE TABLE IF NOT EXISTS projects(
              id TEXT PRIMARY KEY,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              title TEXT NOT NULL,
              document_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS receipts(
              project_id TEXT NOT NULL,
              request_id TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              result_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(project_id, request_id),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS events(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_id TEXT NOT NULL,
              revision INTEGER NOT NULL,
              kind TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(project_id, revision),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS production_receipts(
              receipt_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              request_id TEXT NOT NULL,
              request_sha256 TEXT NOT NULL,
              command TEXT NOT NULL,
              stage TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            -- Derived proxy metadata is disposable, and never grants render authority.
            CREATE TABLE IF NOT EXISTS render_window_cache_entries(
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              deliverable_id TEXT NOT NULL,
              source_start INTEGER NOT NULL CHECK(source_start >= 0),
              source_end INTEGER NOT NULL CHECK(source_end > source_start),
              fingerprint_sha256 TEXT NOT NULL,
              PRIMARY KEY(project_id, generation, revision, deliverable_id,
                          source_start, source_end, fingerprint_sha256),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS render_window_cache_by_owner
              ON render_window_cache_entries(project_id, generation, deliverable_id);

            -- A commit atomically revokes cache registrations. Recoverable
            -- eviction records retain only derived-artifact keys, never source.
            CREATE TABLE IF NOT EXISTS render_window_cache_evictions(
              project_id TEXT NOT NULL,
              generation TEXT NOT NULL,
              revision INTEGER NOT NULL CHECK(revision >= 0),
              deliverable_id TEXT NOT NULL,
              source_start INTEGER NOT NULL CHECK(source_start >= 0),
              source_end INTEGER NOT NULL CHECK(source_end > source_start),
              fingerprint_sha256 TEXT NOT NULL,
              PRIMARY KEY(project_id, generation, revision, deliverable_id,
                          source_start, source_end, fingerprint_sha256),
              FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TRIGGER IF NOT EXISTS render_window_cache_invalidate_revision
            AFTER UPDATE OF generation, revision ON projects
            WHEN OLD.generation<>NEW.generation OR OLD.revision<>NEW.revision
            BEGIN
              INSERT OR IGNORE INTO render_window_cache_evictions(
                project_id,generation,revision,deliverable_id,
                source_start,source_end,fingerprint_sha256)
                SELECT project_id,generation,revision,deliverable_id,
                       source_start,source_end,fingerprint_sha256
                FROM render_window_cache_entries
                WHERE project_id=NEW.id
                  AND (generation<>NEW.generation OR revision<>NEW.revision);
              DELETE FROM render_window_cache_entries
              WHERE project_id=NEW.id
                AND (generation<>NEW.generation OR revision<>NEW.revision);
            END;

            CREATE INDEX IF NOT EXISTS events_by_project_revision
              ON events(project_id, revision);
            CREATE INDEX IF NOT EXISTS production_receipts_by_project
              ON production_receipts(project_id, receipt_id DESC);
            CREATE INDEX IF NOT EXISTS production_receipts_by_request
              ON production_receipts(project_id, request_id, receipt_id DESC);

CREATE TABLE production_receipt_order(
           sequence INTEGER PRIMARY KEY AUTOINCREMENT,
           receipt_id TEXT NOT NULL UNIQUE,
           project_id TEXT NOT NULL,
           FOREIGN KEY(receipt_id) REFERENCES production_receipts(receipt_id)
             ON UPDATE CASCADE ON DELETE CASCADE,
           FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
         );

CREATE INDEX production_receipt_order_by_project
           ON production_receipt_order(project_id, sequence DESC);

CREATE TRIGGER production_receipt_order_append
         AFTER INSERT ON production_receipts
         BEGIN
           INSERT INTO production_receipt_order(receipt_id,project_id)
             VALUES(NEW.receipt_id,NEW.project_id);
         END;

ALTER TABLE receipts ADD COLUMN apply_binding_version INTEGER NOT NULL DEFAULT 0
         CHECK (apply_binding_version IN (0, 1));

ALTER TABLE receipts ADD COLUMN apply_binding_json TEXT;

CREATE TABLE cas_store_identity (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            store_id TEXT NOT NULL UNIQUE
        );
        CREATE TABLE cas_import_commits (
            operation_id TEXT PRIMARY KEY NOT NULL,
            root_id TEXT NOT NULL,
            store_id TEXT NOT NULL,
            request_sha256 TEXT NOT NULL,
            project_id TEXT NOT NULL,
            generation TEXT NOT NULL
        );
"###;

    // Mechanical materialization only: one test-only example overlay on the
    // exact v5 source. Do not compile this producer against the current Store,
    // rebase CAS anchors, or treat the resulting manifest as self-authentication.
    const LEGACY_PRODUCER_RS: &str = r####"// Test-only overlay for source 246873f2d908d0f801c990963a2f570934f29b83.
// Materialize exactly as crates/storage/examples/a2_v5_budget_fixture.rs.
// Run on the same isolated host and physical path as the later v6 consumer.
// The owner lane supplies build/source provenance; this JSON is not an authority.
use motionwright_domain::{Asset, Change};
use motionwright_storage::{ProductionReceiptInput, STORAGE_SCHEMA_VERSION, Store};
use rusqlite::{Connection, types::ValueRef};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{fs, io::Write, path::Path};
use uuid::Uuid;

const SOURCE: &str = "246873f2d908d0f801c990963a2f570934f29b83";
const ORIGINAL: &[u8] =
    b"Actual schema-five public import fixture: retain these exact CAS bytes.\n";

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn rows(conn: &Connection, sql: &str) -> Value {
    let mut statement = conn.prepare(sql).unwrap();
    let columns = statement.column_count();
    let values = statement
        .query_map([], |row| {
            let mut values = Vec::new();
            for index in 0..columns {
                values.push(match row.get_ref(index)? {
                    ValueRef::Null => Value::Null,
                    ValueRef::Integer(value) => json!(value),
                    ValueRef::Real(_) | ValueRef::Blob(_) => {
                        panic!("unexpected fixture column type")
                    }
                    ValueRef::Text(value) => json!(std::str::from_utf8(value).unwrap()),
                });
            }
            Ok(Value::Array(values))
        })
        .unwrap()
        .collect::<rusqlite::Result<Vec<_>>>()
        .unwrap();
    Value::Array(values)
}

fn snapshot(conn: &Connection) -> Value {
    json!({
        "projects":rows(conn,"SELECT * FROM projects ORDER BY id"),
        "receipts":rows(conn,"SELECT * FROM receipts ORDER BY project_id,request_id"),
        "events":rows(conn,"SELECT * FROM events ORDER BY event_id"),
        "production_receipts":rows(conn,"SELECT * FROM production_receipts ORDER BY receipt_id"),
        "production_receipt_order":rows(conn,"SELECT * FROM production_receipt_order ORDER BY sequence"),
        "cas_store_identity":rows(conn,"SELECT * FROM cas_store_identity ORDER BY singleton"),
        "cas_import_commits":rows(conn,"SELECT * FROM cas_import_commits ORDER BY operation_id")
    })
}

fn inventory(root: &Path, directory: &Path, result: &mut Vec<Value>, visited: &mut usize) {
    assert!(directory.strip_prefix(root).unwrap().components().count() <= 16);
    let directory_metadata = fs::symlink_metadata(directory).unwrap();
    assert!(directory_metadata.is_dir() && !directory_metadata.file_type().is_symlink());
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        assert_eq!(
            directory_metadata.file_attributes() & 0x400,
            0,
            "reparse directory"
        );
    }
    let mut entries = fs::read_dir(directory)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .collect::<Vec<_>>();
    entries.sort();
    for path in entries {
        *visited += 1;
        assert!(*visited <= 1024);
        let metadata = fs::symlink_metadata(&path).unwrap();
        assert!(!metadata.file_type().is_symlink());
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            assert_eq!(
                metadata.file_attributes() & 0x400,
                0,
                "reparse inventory entry"
            );
        }
        assert!(fs::canonicalize(&path).unwrap().starts_with(root));
        if metadata.is_dir() {
            inventory(root, &path, result, visited);
        } else {
            assert!(metadata.is_file() && metadata.len() <= 16 * 1024 * 1024);
            // Only the root manifest is outside its own original inventory.
            if path == root.join("fixture.json") {
                continue;
            }
            let bytes = fs::read(&path).unwrap();
            let relative = path
                .strip_prefix(root)
                .unwrap()
                .to_str()
                .unwrap()
                .replace('\\', "/");
            result.push(json!({"path":relative,"bytes":bytes.len(),"sha256":sha256(&bytes)}));
            assert!(result.len() <= 512);
        }
    }
}

fn columns(conn: &Connection) -> Value {
    let mut result = serde_json::Map::new();
    for table in [
        "projects",
        "receipts",
        "events",
        "production_receipts",
        "production_receipt_order",
        "cas_store_identity",
        "cas_import_commits",
    ] {
        let statement = conn
            .prepare(&format!("SELECT * FROM {table} LIMIT 0"))
            .unwrap();
        result.insert(table.into(), json!(statement.column_names()));
    }
    assert_eq!(
        result["cas_store_identity"],
        json!(["singleton", "store_id"])
    );
    assert_eq!(
        result["cas_import_commits"],
        json!([
            "operation_id",
            "root_id",
            "store_id",
            "request_sha256",
            "project_id",
            "generation"
        ])
    );
    Value::Object(result)
}

fn assert_destination_fixture_history(
    original_columns: &Value,
    snapshot: &Value,
    history: &Value,
    imported: &Value,
) {
    fn text(value: &Value) -> &str {
        value.as_str().expect("expected original SQLite TEXT")
    }
    fn field<'a>(headers: &[Value], row: &'a Value, name: &str) -> &'a Value {
        let index = headers
            .iter()
            .position(|header| header.as_str() == Some(name))
            .unwrap_or_else(|| panic!("missing original column {name}"));
        &row.as_array().expect("original row array")[index]
    }
    fn table<'a>(
        columns: &'a Value,
        rows: &'a Value,
        name: &str,
        count: usize,
    ) -> (&'a [Value], &'a [Value]) {
        let headers = columns[name].as_array().expect("original columns");
        let rows = rows[name].as_array().expect("original rows");
        assert!(!headers.is_empty());
        assert_eq!(rows.len(), count, "nonempty destination history for {name}");
        let mut names = std::collections::BTreeSet::new();
        for header in headers {
            assert!(!text(header).is_empty() && names.insert(text(header)));
        }
        for row in rows {
            let cells = row.as_array().expect("original row array");
            assert_eq!(cells.len(), headers.len());
            assert!(
                cells
                    .iter()
                    .all(|cell| cell.is_null() || cell.is_string() || cell.as_i64().is_some())
            );
        }
        (headers, rows)
    }
    fn digest(value: &str) {
        assert_eq!(value.len(), 64);
        assert!(
            value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        );
    }
    assert_eq!(history["scope"], "v5-storage-fixture-only");
    assert_eq!(history["native_execution"], "NOT_RUN");
    assert_eq!(history["apply_request_id"], "v5-fixture-destination-rename");
    assert_eq!(
        history["production_request_id"],
        "v5-fixture-destination-production"
    );
    assert_eq!(history["command"], "fixture.storage.only");
    let request_original = text(&history["request_original_utf8"]);
    let request_digest = text(&history["request_sha256"]);
    digest(request_digest);
    assert_eq!(sha256(request_original.as_bytes()), request_digest);
    let request: Value = serde_json::from_str(request_original).unwrap();
    assert_eq!(
        request,
        json!({
            "scope":"v5-storage-fixture-only","native_execution":"NOT_RUN",
            "project_id":imported["id"],"generation":imported["generation"],
            "revision":imported["revision"],"request_id":history["production_request_id"],
            "command":history["command"]
        })
    );
    let (headers, rows) = table(original_columns, snapshot, "receipts", 1);
    let row = &rows[0];
    assert_eq!(field(headers, row, "project_id"), &imported["id"]);
    assert_eq!(
        field(headers, row, "request_id"),
        &history["apply_request_id"]
    );
    digest(text(field(headers, row, "request_sha256")));
    assert_eq!(field(headers, row, "apply_binding_version"), &json!(1));
    let binding: Value =
        serde_json::from_str(text(field(headers, row, "apply_binding_json"))).unwrap();
    assert!(binding.is_object());
    let outcome: Value = serde_json::from_str(text(field(headers, row, "result_json"))).unwrap();
    assert_eq!(&outcome["project"], imported);
    assert_eq!(outcome["request_id"], history["apply_request_id"]);
    assert_eq!(outcome["replayed"], false);
    assert_eq!(
        outcome["previous_revision"].as_u64().unwrap() + 1,
        imported["revision"].as_u64().unwrap()
    );
    let (headers, rows) = table(original_columns, snapshot, "production_receipts", 2);
    let mut by_id = std::collections::BTreeMap::new();
    for row in rows {
        let id = text(field(headers, row, "receipt_id"));
        Uuid::parse_str(id).unwrap();
        assert!(by_id.insert(id, row).is_none());
        for (column, expected) in [
            ("project_id", &imported["id"]),
            ("generation", &imported["generation"]),
            ("revision", &imported["revision"]),
            ("request_id", &history["production_request_id"]),
            ("request_sha256", &history["request_sha256"]),
            ("command", &history["command"]),
        ] {
            assert_eq!(field(headers, row, column), expected, "{column}");
        }
        assert!(field(headers, row, "revision").as_i64().is_some());
        let payload: Value =
            serde_json::from_str(text(field(headers, row, "payload_json"))).unwrap();
        assert_eq!(payload["scope"], "v5-storage-fixture-only");
        assert_eq!(payload["native_execution"], "NOT_RUN");
    }
    let dispatch_id = text(&history["dispatch_receipt_id"]);
    let unknown_id = text(&history["unknown_receipt_id"]);
    assert_ne!(dispatch_id, unknown_id);
    assert_eq!(
        field(headers, by_id[dispatch_id], "stage"),
        &json!("dispatching")
    );
    assert_eq!(
        field(headers, by_id[unknown_id], "stage"),
        &json!("outcome_unknown")
    );
    let (headers, rows) = table(original_columns, snapshot, "production_receipt_order", 2);
    for row in rows {
        assert_eq!(field(headers, row, "project_id"), &imported["id"]);
    }
    let first = field(headers, &rows[0], "sequence").as_i64().unwrap();
    let second = field(headers, &rows[1], "sequence").as_i64().unwrap();
    assert!(first < second);
    assert_eq!(text(field(headers, &rows[0], "receipt_id")), dispatch_id);
    assert_eq!(text(field(headers, &rows[1], "receipt_id")), unknown_id);
}

fn main() {
    assert_eq!(
        STORAGE_SCHEMA_VERSION, 5,
        "must link the genuine schema-five Store"
    );
    let arguments = std::env::args_os().skip(1).collect::<Vec<_>>();
    assert_eq!(arguments.len(), 1, "one fresh output directory is required");
    let output = std::path::PathBuf::from(&arguments[0]);
    assert!(output.is_absolute() && !output.exists());
    let crate_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let repository = crate_dir.parent().unwrap().parent().unwrap();
    let expected = [
        (
            "crates/storage/src/lib.rs",
            "99c3a483a469366e7f23f103182a94ee6a000c491f60b71f5dcaadde09c419d6",
        ),
        (
            "crates/storage/src/apply_receipt_binding.rs",
            "6e7cdbbb12bc3e5d46bf80666f8ca43f042580fac0783027e9fedfc4f419b330",
        ),
        (
            "crates/storage/src/cas_retention.rs",
            "83c957a9e3da2bf1da37f772c8785201bd19cb4e732e6d29b56e93858de8229c",
        ),
        (
            "crates/storage/src/production.rs",
            "7e7369c74d6afeed429b59d4ade462d8ad5beaa0a4b3e724ad02cdf8b40d899b",
        ),
        (
            "Cargo.lock",
            "484d551f035d6756fa2d7f4c62a4f7f6d09f4ffa6affd3a9521ac8fb33c799f9",
        ),
        (
            "rust-toolchain.toml",
            "887f9be066a15585a2c583578e84b0fcb541126d81546276bad3d2ff00d61167",
        ),
        (
            "crates/storage/Cargo.toml",
            "e7cf62c71c8a5aeec22f86ccb6fd7b7c7f2b41258f3906f88fe15af215f56f4e",
        ),
    ];
    let mut sources = serde_json::Map::new();
    for (relative, digest) in expected {
        let bytes = fs::read(repository.join(relative)).unwrap();
        assert_eq!(
            sha256(&bytes),
            digest,
            "legacy source input changed: {relative}"
        );
        sources.insert(
            relative.into(),
            json!({"bytes":bytes.len(),"sha256":digest}),
        );
    }
    let overlay = fs::read(crate_dir.join("examples/a2_v5_budget_fixture.rs")).unwrap();
    fs::create_dir_all(&output).unwrap();
    let output = fs::canonicalize(output).unwrap();
    let original = output.join("original.bin");
    fs::write(&original, ORIGINAL).unwrap();
    let mut source = Store::open(output.join("source/state.sqlite3")).unwrap();
    let project = source
        .create_named_project("Genuine v5 import source")
        .unwrap();
    let blob = source.ingest_blob_file(&original).unwrap();
    let project = source
        .apply(
            project.id,
            &project.stamp(),
            "v5-fixture-add-asset",
            &Change::AddAsset {
                asset: Asset {
                    id: Uuid::new_v4(),
                    name: "Actual imported v5 bytes".into(),
                    media_type: "application/octet-stream".into(),
                    content_sha256: Some(blob.sha256.clone()),
                    source_revision: Some(SOURCE.into()),
                },
            },
        )
        .unwrap()
        .project;
    let bundle = output.join("bundle");
    source.export_project_bundle(project.id, &bundle).unwrap();
    let database = output.join("destination/state.sqlite3");
    let mut destination = Store::open(&database).unwrap();
    let imported = destination.import_project_bundle(&bundle).unwrap();
    assert_eq!(destination.read_blob(&blob.sha256, 1024).unwrap(), ORIGINAL);
    assert_eq!(imported.id, project.id);
    let imported_generation = imported.generation;
    let imported_revision = imported.revision;
    let imported = destination
        .apply(
            imported.id,
            &imported.stamp(),
            "v5-fixture-destination-rename",
            &Change::RenameProject {
                title: "v5 destination receipt anchor".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(imported.id, project.id);
    assert_eq!(imported.generation, imported_generation);
    assert_eq!(imported.revision, imported_revision + 1);
    let request_original = serde_json::to_string(&json!({
        "scope":"v5-storage-fixture-only","native_execution":"NOT_RUN",
        "project_id":imported.id,"generation":imported.generation,
        "revision":imported.revision,"request_id":"v5-fixture-destination-production",
        "command":"fixture.storage.only"
    }))
    .unwrap();
    let request_digest = sha256(request_original.as_bytes());
    let mut append = |stage: &str| {
        destination
            .append_production_receipt(ProductionReceiptInput {
                project_id: imported.id,
                generation: imported.generation,
                revision: imported.revision,
                request_id: "v5-fixture-destination-production".into(),
                request_sha256: request_digest.clone(),
                command: "fixture.storage.only".into(),
                stage: stage.into(),
                payload: json!({
                    "scope":"v5-storage-fixture-only","native_execution":"NOT_RUN",
                    "observation":"public storage fixture append; no tool was dispatched"
                }),
            })
            .unwrap()
    };
    let dispatch = append("dispatching");
    let unknown = append("outcome_unknown");
    let destination_history = json!({
        "scope":"v5-storage-fixture-only","native_execution":"NOT_RUN",
        "apply_request_id":"v5-fixture-destination-rename",
        "production_request_id":"v5-fixture-destination-production",
        "command":"fixture.storage.only","request_original_utf8":request_original,
        "request_sha256":request_digest,"dispatch_receipt_id":dispatch.id,
        "unknown_receipt_id":unknown.id
    });
    drop(destination);
    drop(source);

    let connection = Connection::open(&database).unwrap();
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        5
    );
    let original_columns = columns(&connection);
    let original_rows = snapshot(&connection);
    assert_destination_fixture_history(
        &original_columns,
        &original_rows,
        &destination_history,
        &serde_json::to_value(&imported).unwrap(),
    );
    assert_eq!(
        original_rows["cas_import_commits"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
    let marker = &original_rows["cas_import_commits"][0];
    let store_id = &original_rows["cas_store_identity"][0][1];
    assert_eq!(&marker[2], store_id);
    assert_eq!(marker[4], json!(imported.id.to_string()));
    assert_eq!(marker[5], json!(imported.generation.to_string()));
    let anchor: Value = serde_json::from_slice(
        &fs::read(output.join("destination/blobs/.motionwright-cas-root-v1.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(marker[1], anchor["root_id"]);
    let checkpoint: (i64, i64, i64) = connection
        .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |r| {
            Ok((r.get(0)?, r.get(1)?, r.get(2)?))
        })
        .unwrap();
    assert_eq!(checkpoint.0, 0, "fixture WAL checkpoint is incomplete");
    drop(connection);

    let mut files = Vec::new();
    inventory(&output, &output, &mut files, &mut 0);
    let manifest = json!({
        "schema":"motionwright.actual-v5-budget-fixture/2",
        "source_revision":SOURCE,
        "source_tree":"6f93eb6f774da4aa5dbb35ada99888f00ab3a373",
        "source_inputs":sources,
        "producer_overlay_sha256":sha256(&overlay),
        "canonical_root":output,
        "database":"destination/state.sqlite3",
        "storage_schema":5,
        "project":imported,
        "blob":{"sha256":blob.sha256,"bytes":ORIGINAL.len()},
        "root_anchor":anchor,
        "original_columns":original_columns,
        "original_rows":original_rows,
        "destination_history":destination_history,
        "checkpoint":checkpoint,
        "files":files,
        "provenance":"Requires the owner lane's exact old-source build and execution originals; this document cannot authenticate itself."
    });
    let mut bytes = serde_json::to_vec_pretty(&manifest).unwrap();
    bytes.push(b'\n');
    let path = output.join("fixture.json");
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)
        .unwrap();
    file.write_all(&bytes).unwrap();
    file.sync_all().unwrap();
    drop(file);
    assert_eq!(fs::read(&path).unwrap(), bytes);
    println!(
        "{}",
        json!({"fixture_root":output,"manifest_sha256":sha256(&bytes),
        "producer_overlay_sha256":sha256(&overlay),"storage_schema":5})
    );
}
"####;

    fn sha256(bytes: &[u8]) -> String {
        hex::encode(Sha256::digest(bytes))
    }

    fn assert_destination_fixture_history(
        original_columns: &Value,
        snapshot: &Value,
        history: &Value,
        imported: &Value,
    ) {
        fn text(value: &Value) -> &str {
            value.as_str().expect("expected original SQLite TEXT")
        }
        fn field<'a>(headers: &[Value], row: &'a Value, name: &str) -> &'a Value {
            let index = headers
                .iter()
                .position(|header| header.as_str() == Some(name))
                .unwrap_or_else(|| panic!("missing original column {name}"));
            &row.as_array().expect("original row array")[index]
        }
        fn table<'a>(
            columns: &'a Value,
            rows: &'a Value,
            name: &str,
            count: usize,
        ) -> (&'a [Value], &'a [Value]) {
            let headers = columns[name].as_array().expect("original columns");
            let rows = rows[name].as_array().expect("original rows");
            assert!(!headers.is_empty());
            assert_eq!(rows.len(), count, "nonempty destination history for {name}");
            let mut names = std::collections::BTreeSet::new();
            for header in headers {
                assert!(!text(header).is_empty() && names.insert(text(header)));
            }
            for row in rows {
                let cells = row.as_array().expect("original row array");
                assert_eq!(cells.len(), headers.len());
                assert!(
                    cells
                        .iter()
                        .all(|cell| cell.is_null() || cell.is_string() || cell.as_i64().is_some())
                );
            }
            (headers, rows)
        }
        fn digest(value: &str) {
            assert_eq!(value.len(), 64);
            assert!(
                value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            );
        }
        assert_eq!(history["scope"], "v5-storage-fixture-only");
        assert_eq!(history["native_execution"], "NOT_RUN");
        assert_eq!(history["apply_request_id"], "v5-fixture-destination-rename");
        assert_eq!(
            history["production_request_id"],
            "v5-fixture-destination-production"
        );
        assert_eq!(history["command"], "fixture.storage.only");
        let request_original = text(&history["request_original_utf8"]);
        let request_digest = text(&history["request_sha256"]);
        digest(request_digest);
        assert_eq!(sha256(request_original.as_bytes()), request_digest);
        let request: Value = serde_json::from_str(request_original).unwrap();
        assert_eq!(
            request,
            json!({
                "scope":"v5-storage-fixture-only","native_execution":"NOT_RUN",
                "project_id":imported["id"],"generation":imported["generation"],
                "revision":imported["revision"],"request_id":history["production_request_id"],
                "command":history["command"]
            })
        );
        let (headers, rows) = table(original_columns, snapshot, "receipts", 1);
        let row = &rows[0];
        assert_eq!(field(headers, row, "project_id"), &imported["id"]);
        assert_eq!(
            field(headers, row, "request_id"),
            &history["apply_request_id"]
        );
        digest(text(field(headers, row, "request_sha256")));
        assert_eq!(field(headers, row, "apply_binding_version"), &json!(1));
        let binding: Value =
            serde_json::from_str(text(field(headers, row, "apply_binding_json"))).unwrap();
        assert!(binding.is_object());
        let outcome: Value =
            serde_json::from_str(text(field(headers, row, "result_json"))).unwrap();
        assert_eq!(&outcome["project"], imported);
        assert_eq!(outcome["request_id"], history["apply_request_id"]);
        assert_eq!(outcome["replayed"], false);
        assert_eq!(
            outcome["previous_revision"].as_u64().unwrap() + 1,
            imported["revision"].as_u64().unwrap()
        );
        let (headers, rows) = table(original_columns, snapshot, "production_receipts", 2);
        let mut by_id = std::collections::BTreeMap::new();
        for row in rows {
            let id = text(field(headers, row, "receipt_id"));
            Uuid::parse_str(id).unwrap();
            assert!(by_id.insert(id, row).is_none());
            for (column, expected) in [
                ("project_id", &imported["id"]),
                ("generation", &imported["generation"]),
                ("revision", &imported["revision"]),
                ("request_id", &history["production_request_id"]),
                ("request_sha256", &history["request_sha256"]),
                ("command", &history["command"]),
            ] {
                assert_eq!(field(headers, row, column), expected, "{column}");
            }
            assert!(field(headers, row, "revision").as_i64().is_some());
            let payload: Value =
                serde_json::from_str(text(field(headers, row, "payload_json"))).unwrap();
            assert_eq!(payload["scope"], "v5-storage-fixture-only");
            assert_eq!(payload["native_execution"], "NOT_RUN");
        }
        let dispatch_id = text(&history["dispatch_receipt_id"]);
        let unknown_id = text(&history["unknown_receipt_id"]);
        assert_ne!(dispatch_id, unknown_id);
        assert_eq!(
            field(headers, by_id[dispatch_id], "stage"),
            &json!("dispatching")
        );
        assert_eq!(
            field(headers, by_id[unknown_id], "stage"),
            &json!("outcome_unknown")
        );
        let (headers, rows) = table(original_columns, snapshot, "production_receipt_order", 2);
        for row in rows {
            assert_eq!(field(headers, row, "project_id"), &imported["id"]);
        }
        let first = field(headers, &rows[0], "sequence").as_i64().unwrap();
        let second = field(headers, &rows[1], "sequence").as_i64().unwrap();
        assert!(first < second);
        assert_eq!(text(field(headers, &rows[0], "receipt_id")), dispatch_id);
        assert_eq!(text(field(headers, &rows[1], "receipt_id")), unknown_id);
    }

    fn raw_v5_fixture() -> (tempfile::TempDir, PathBuf, motionwright_domain::Project) {
        assert_eq!(sha256(V5_SQL.as_bytes()), V5_SQL_SHA256);
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("original-v5.sqlite3");
        let conn = Connection::open(&database).unwrap();
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")
            .unwrap();
        conn.execute_batch(V5_SQL).unwrap();
        conn.execute(
            "INSERT INTO cas_store_identity(singleton,store_id) VALUES(1,?1)",
            [Uuid::new_v4().to_string()],
        )
        .unwrap();
        let project = motionwright_domain::Project::new("Original v5 SQL fixture").unwrap();
        conn.execute(
            "INSERT INTO projects(id,generation,revision,title,document_json,updated_at)
             VALUES(?1,?2,?3,?4,?5,?6)",
            params![
                project.id.to_string(),
                project.generation.to_string(),
                project.revision as i64,
                project.title,
                serde_json::to_string(&project).unwrap(),
                project.updated_at.to_rfc3339()
            ],
        )
        .unwrap();
        conn.pragma_update(None, "user_version", 5_i64).unwrap();
        assert_eq!(
            conn.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            5
        );
        assert_eq!(
            conn.query_row(
                "SELECT count(*) FROM sqlite_schema
             WHERE name GLOB 'production_operation_*' OR name GLOB 'mw_writer_v6_*'",
                [],
                |r| r.get::<_, i64>(0)
            )
            .unwrap(),
            0
        );
        let checkpoint: (i64, i64, i64) = conn
            .query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |r| {
                Ok((r.get(0)?, r.get(1)?, r.get(2)?))
            })
            .unwrap();
        assert_eq!(checkpoint.0, 0);
        drop(conn);
        (temp, database, project)
    }

    fn schema_objects(conn: &Connection) -> Vec<(String, String, String, Option<String>)> {
        let mut statement = conn
            .prepare("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")
            .unwrap();
        statement
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap()
    }

    fn require_old_writer_rejected(error: rusqlite::Error) {
        match error {
            rusqlite::Error::SqliteFailure(_, Some(message)) => assert!(
                message.contains("motionwright_writer_epoch_guard_v6"),
                "unexpected rejection instead of the writer fence: {message}"
            ),
            other => panic!("unexpected old-writer result: {other}"),
        }
    }

    #[test]
    fn wal_snapshot_before_v5_upgrade_blocks_write_then_current_view_is_still_fenced() {
        let (_temp, path, project) = raw_v5_fixture();
        let legacy = Connection::open(&path).unwrap();
        legacy.busy_timeout(Duration::ZERO).unwrap();
        assert_eq!(
            legacy
                .query_row("PRAGMA journal_mode", [], |r| r.get::<_, String>(0))
                .unwrap(),
            "wal"
        );
        legacy.execute_batch("BEGIN").unwrap();
        // BEGIN alone is not a WAL snapshot: force a real read before the upgrade.
        let before: String = legacy
            .query_row(
                "SELECT document_json FROM projects WHERE id=?1",
                [project.id.to_string()],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(
            legacy
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            5
        );
        let upgraded = Store::open(&path).unwrap();
        assert_eq!(
            legacy
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            5
        );
        let stale = legacy
            .execute(
                "UPDATE projects SET title=title WHERE id=?1",
                [project.id.to_string()],
            )
            .unwrap_err();
        match stale {
            rusqlite::Error::SqliteFailure(code, _) => {
                assert_eq!(code.extended_code, rusqlite::ffi::SQLITE_BUSY_SNAPSHOT)
            }
            other => panic!("expected a stale WAL snapshot, observed {other}"),
        }
        assert_eq!(
            legacy
                .query_row(
                    "SELECT document_json FROM projects WHERE id=?1",
                    [project.id.to_string()],
                    |r| r.get::<_, String>(0)
                )
                .unwrap(),
            before
        );
        legacy.execute_batch("ROLLBACK").unwrap();
        assert_eq!(
            legacy
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            6
        );
        // This second rejection is independent of the stale snapshot and proves
        // that the same already-open connection cannot bypass the new fence.
        require_old_writer_rejected(
            legacy
                .execute(
                    "UPDATE projects SET title=title WHERE id=?1",
                    [project.id.to_string()],
                )
                .unwrap_err(),
        );
        assert_eq!(upgraded.load_project(project.id).unwrap(), project);
    }

    #[test]
    fn trusted_schema_off_requires_an_innocuous_six_for_the_same_valid_write() {
        let (_temp, path, project) = raw_v5_fixture();
        drop(Store::open(&path).unwrap());
        for (name, marker, innocuous, allowed) in [
            ("absent", None, true, false),
            ("null", Some(None), true, false),
            ("five", Some(Some(5_i64)), true, false),
            ("unsafe-six", Some(Some(6_i64)), false, false),
            ("six", Some(Some(6_i64)), true, true),
        ] {
            let writer = Connection::open(&path).unwrap();
            writer.pragma_update(None, "trusted_schema", "OFF").unwrap();
            assert_eq!(
                writer
                    .query_row("PRAGMA trusted_schema", [], |r| r.get::<_, i64>(0))
                    .unwrap(),
                0
            );
            if let Some(marker) = marker {
                let mut flags = FunctionFlags::SQLITE_UTF8;
                if innocuous {
                    flags |= FunctionFlags::SQLITE_INNOCUOUS;
                }
                writer
                    .create_scalar_function(
                        "motionwright_writer_epoch_guard_v6",
                        0,
                        flags,
                        move |_| Ok(marker),
                    )
                    .unwrap();
            }
            let result = writer.execute(
                "UPDATE projects SET title=title WHERE id=?1",
                [project.id.to_string()],
            );
            if allowed {
                assert_eq!(
                    result.unwrap(),
                    1,
                    "same valid row must admit the innocuous six"
                );
            } else {
                let message = result.unwrap_err().to_string();
                match name {
                    "null" | "five" => assert!(
                        message.contains("schema6 registered writer required"),
                        "{message}"
                    ),
                    "unsafe-six" => assert!(
                        message.contains("unsafe")
                            && message.contains("motionwright_writer_epoch_guard_v6"),
                        "{message}"
                    ),
                    "absent" => assert!(
                        message.contains("motionwright_writer_epoch_guard_v6"),
                        "{message}"
                    ),
                    _ => unreachable!(),
                }
                assert_eq!(
                    writer
                        .query_row("SELECT total_changes()", [], |r| r.get::<_, i64>(0))
                        .unwrap(),
                    0,
                    "a rejected fixture write changed data"
                );
            }
            assert_eq!(
                writer
                    .query_row(
                        "SELECT title FROM projects WHERE id=?1",
                        [project.id.to_string()],
                        |r| r.get::<_, String>(0)
                    )
                    .unwrap(),
                project.title
            );
        }
        assert_eq!(
            Store::open(&path)
                .unwrap()
                .load_project(project.id)
                .unwrap(),
            project
        );
    }

    #[test]
    fn original_v5_immediate_writer_prevents_any_partial_budget_ddl() {
        let (_temp, path, project) = raw_v5_fixture();
        let legacy = Connection::open(&path).unwrap();
        let before = schema_objects(&legacy);
        legacy.execute_batch("BEGIN IMMEDIATE").unwrap();
        legacy
            .execute(
                "UPDATE projects SET title=title WHERE id=?1",
                [project.id.to_string()],
            )
            .unwrap();
        let error = Store::open(&path)
            .err()
            .expect("v5 writer must exclude the schema migration");
        assert!(
            matches!(error, StorageError::Sql(rusqlite::Error::SqliteFailure(code,_))
            if matches!(code.code,rusqlite::ErrorCode::DatabaseBusy|rusqlite::ErrorCode::DatabaseLocked))
        );
        assert_eq!(schema_objects(&legacy), before);
        assert_eq!(
            legacy
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            5
        );
        legacy.execute_batch("ROLLBACK").unwrap();
        drop(legacy);
        let upgraded = Store::open(&path).unwrap();
        assert_eq!(upgraded.load_project(project.id).unwrap(), project);
        let readback = Connection::open(&path).unwrap();
        assert_eq!(
            readback
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            6
        );
        assert_eq!(readback.query_row(
            "SELECT count(*) FROM sqlite_schema WHERE type='trigger' AND name GLOB 'mw_writer_v6_*'",
            [],|r|r.get::<_,i64>(0)).unwrap(),27);
    }

    fn inventory(root: &Path, directory: &Path, result: &mut Vec<Value>, visited: &mut usize) {
        assert!(directory.strip_prefix(root).unwrap().components().count() <= 16);
        let directory_metadata = fs::symlink_metadata(directory).unwrap();
        assert!(directory_metadata.is_dir() && !directory_metadata.file_type().is_symlink());
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            assert_eq!(
                directory_metadata.file_attributes() & 0x400,
                0,
                "reparse directory"
            );
        }
        let mut entries = fs::read_dir(directory)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect::<Vec<_>>();
        entries.sort();
        for path in entries {
            *visited += 1;
            assert!(*visited <= 1024);
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                assert_eq!(
                    metadata.file_attributes() & 0x400,
                    0,
                    "reparse inventory entry"
                );
            }
            assert!(fs::canonicalize(&path).unwrap().starts_with(root));
            if metadata.is_dir() {
                inventory(root, &path, result, visited);
            } else {
                assert!(metadata.is_file() && metadata.len() <= 16 * 1024 * 1024);
                // Only the root manifest is outside its own original inventory.
                if path == root.join("fixture.json") {
                    continue;
                }
                let bytes = fs::read(&path).unwrap();
                let relative = path
                    .strip_prefix(root)
                    .unwrap()
                    .to_str()
                    .unwrap()
                    .replace('\\', "/");
                result.push(json!({"path":relative,"bytes":bytes.len(),"sha256":sha256(&bytes)}));
                assert!(result.len() <= 512);
            }
        }
    }

    fn columns(conn: &Connection) -> Value {
        let mut result = serde_json::Map::new();
        for table in [
            "projects",
            "receipts",
            "events",
            "production_receipts",
            "production_receipt_order",
            "cas_store_identity",
            "cas_import_commits",
        ] {
            let statement = conn
                .prepare(&format!("SELECT * FROM {table} LIMIT 0"))
                .unwrap();
            result.insert(table.into(), json!(statement.column_names()));
        }
        assert_eq!(
            result["cas_store_identity"],
            json!(["singleton", "store_id"])
        );
        assert_eq!(
            result["cas_import_commits"],
            json!([
                "operation_id",
                "root_id",
                "store_id",
                "request_sha256",
                "project_id",
                "generation"
            ])
        );
        Value::Object(result)
    }

    fn rows(conn: &Connection, sql: &str) -> Value {
        let mut statement = conn.prepare(sql).unwrap();
        let columns = statement.column_count();
        let rows = statement
            .query_map([], |row| {
                let mut values = Vec::new();
                for index in 0..columns {
                    values.push(match row.get_ref(index)? {
                        ValueRef::Null => Value::Null,
                        ValueRef::Integer(value) => json!(value),
                        ValueRef::Text(value) => json!(std::str::from_utf8(value).unwrap()),
                        ValueRef::Real(_) | ValueRef::Blob(_) => {
                            panic!("unexpected fixture column type")
                        }
                    });
                }
                Ok(Value::Array(values))
            })
            .unwrap()
            .collect::<rusqlite::Result<Vec<_>>>()
            .unwrap();
        Value::Array(rows)
    }

    fn snapshot(conn: &Connection) -> Value {
        json!({
            "projects":rows(conn,"SELECT * FROM projects ORDER BY id"),
            "receipts":rows(conn,"SELECT * FROM receipts ORDER BY project_id,request_id"),
            "events":rows(conn,"SELECT * FROM events ORDER BY event_id"),
            "production_receipts":rows(conn,"SELECT * FROM production_receipts ORDER BY receipt_id"),
            "production_receipt_order":rows(conn,"SELECT * FROM production_receipt_order ORDER BY sequence"),
            "cas_store_identity":rows(conn,"SELECT * FROM cas_store_identity ORDER BY singleton"),
            "cas_import_commits":rows(conn,"SELECT * FROM cas_import_commits ORDER BY operation_id")
        })
    }

    #[test]
    #[ignore = "requires the exact v5 producer overlay and a fresh same-path fixture; required A6 lane must select this case explicitly"]
    fn genuine_v5_public_import_preserves_cas_marker_blob_and_receipts_through_v6() {
        let root = PathBuf::from(
            std::env::var_os("MOTIONWRIGHT_BUDGET_V5_FIXTURE")
                .expect("required v5 fixture was not supplied; do not credit this test"),
        );
        assert!(root.is_absolute());
        let root = fs::canonicalize(root).unwrap();
        let pin = std::env::var("MOTIONWRIGHT_BUDGET_V5_FIXTURE_MANIFEST_SHA256")
            .expect("owner-retained original manifest pin is required");
        assert!(
            pin.len() == 64
                && pin
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        );
        let manifest_metadata = fs::symlink_metadata(root.join("fixture.json")).unwrap();
        assert!(
            manifest_metadata.is_file()
                && !manifest_metadata.file_type().is_symlink()
                && manifest_metadata.len() <= 256 * 1024
        );
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            assert_eq!(
                manifest_metadata.file_attributes() & 0x400,
                0,
                "reparse manifest"
            );
        }
        let raw = fs::read(root.join("fixture.json")).unwrap();
        assert!(raw.len() <= 256 * 1024);
        assert_eq!(sha256(&raw), pin);
        let manifest: Value = serde_json::from_slice(&raw).unwrap();
        assert_eq!(
            manifest["schema"],
            json!("motionwright.actual-v5-budget-fixture/2")
        );
        assert_eq!(manifest["source_revision"], json!(V5_SOURCE));
        assert_eq!(
            manifest["source_tree"],
            json!("6f93eb6f774da4aa5dbb35ada99888f00ab3a373")
        );
        assert_eq!(
            manifest["producer_overlay_sha256"],
            json!(sha256(LEGACY_PRODUCER_RS.as_bytes()))
        );
        assert_eq!(
            PathBuf::from(manifest["canonical_root"].as_str().unwrap()),
            root,
            "CAS anchors must stay at the producer's original physical path"
        );
        assert_eq!(manifest["storage_schema"], json!(5));
        assert_eq!(manifest["database"], json!("destination/state.sqlite3"));
        for (path, digest) in [
            (
                "crates/storage/src/lib.rs",
                "99c3a483a469366e7f23f103182a94ee6a000c491f60b71f5dcaadde09c419d6",
            ),
            (
                "crates/storage/src/apply_receipt_binding.rs",
                "6e7cdbbb12bc3e5d46bf80666f8ca43f042580fac0783027e9fedfc4f419b330",
            ),
            (
                "crates/storage/src/cas_retention.rs",
                "83c957a9e3da2bf1da37f772c8785201bd19cb4e732e6d29b56e93858de8229c",
            ),
            (
                "crates/storage/src/production.rs",
                "7e7369c74d6afeed429b59d4ade462d8ad5beaa0a4b3e724ad02cdf8b40d899b",
            ),
            (
                "Cargo.lock",
                "484d551f035d6756fa2d7f4c62a4f7f6d09f4ffa6affd3a9521ac8fb33c799f9",
            ),
            (
                "rust-toolchain.toml",
                "887f9be066a15585a2c583578e84b0fcb541126d81546276bad3d2ff00d61167",
            ),
            (
                "crates/storage/Cargo.toml",
                "e7cf62c71c8a5aeec22f86ccb6fd7b7c7f2b41258f3906f88fe15af215f56f4e",
            ),
        ] {
            assert_eq!(manifest["source_inputs"][path]["sha256"], json!(digest));
        }
        let files = manifest["files"].as_array().unwrap();
        assert!(!files.is_empty() && files.len() <= 512);
        let mut paths = std::collections::BTreeSet::new();
        for file in files {
            let relative = Path::new(file["path"].as_str().unwrap());
            assert!(
                !relative.as_os_str().is_empty()
                    && relative
                        .components()
                        .all(|p| matches!(p, Component::Normal(_)))
            );
            assert!(
                paths.insert(relative.to_path_buf()),
                "duplicate original inventory path"
            );
            // The recursive inventory below checks each ancestor before opening
            // any file and compares the complete file set, sizes and digests.
        }
        assert!(paths.contains(Path::new("destination/state.sqlite3")));
        assert!(paths.contains(Path::new(
            "destination/blobs/.motionwright-cas-root-v1.json"
        )));
        let mut actual_inventory = Vec::new();
        inventory(&root, &root, &mut actual_inventory, &mut 0);
        assert_eq!(
            json!(actual_inventory),
            manifest["files"],
            "unexpected, missing or changed original file before SQLite opens"
        );
        let database = root.join("destination/state.sqlite3");
        let legacy = Connection::open(&database).unwrap();
        assert_eq!(
            legacy
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            5,
            "each selected attempt needs a fresh actual v5 producer result"
        );
        assert!(legacy.is_autocommit());
        assert_eq!(columns(&legacy), manifest["original_columns"]);
        let observed_rows = snapshot(&legacy);
        assert_eq!(observed_rows, manifest["original_rows"]);
        assert_destination_fixture_history(
            &manifest["original_columns"],
            &observed_rows,
            &manifest["destination_history"],
            &manifest["project"],
        );
        let project: motionwright_domain::Project =
            serde_json::from_value(manifest["project"].clone()).unwrap();
        let digest = manifest["blob"]["sha256"].as_str().unwrap();
        assert_eq!(digest, sha256(ORIGINAL));
        assert_eq!(manifest["blob"]["bytes"], json!(ORIGINAL.len()));
        let blob_path = root
            .join("destination/blobs/sha256")
            .join(&digest[..2])
            .join(digest);
        assert!(paths.contains(blob_path.strip_prefix(&root).unwrap()));
        assert_eq!(fs::read(&blob_path).unwrap(), ORIGINAL);
        let anchor_path = root.join("destination/blobs/.motionwright-cas-root-v1.json");
        let anchor_bytes = fs::read(&anchor_path).unwrap();
        let anchor: Value = serde_json::from_slice(&anchor_bytes).unwrap();
        assert_eq!(anchor, manifest["root_anchor"]);
        let marker = &manifest["original_rows"]["cas_import_commits"][0];
        assert_eq!(marker[1], anchor["root_id"]);
        assert_eq!(
            marker[2],
            manifest["original_rows"]["cas_store_identity"][0][1]
        );
        assert_eq!(marker[4], json!(project.id.to_string()));
        assert_eq!(marker[5], json!(project.generation.to_string()));
        let mut prepared = legacy
            .prepare("UPDATE projects SET title=title WHERE id=?1")
            .unwrap();
        assert_eq!(prepared.execute([project.id.to_string()]).unwrap(), 1);
        assert!(legacy.is_autocommit());
        let upgraded =
            Store::open_with_managed_blob_root(&database, root.join("destination/blobs")).unwrap();
        require_old_writer_rejected(prepared.execute([project.id.to_string()]).unwrap_err());
        let observed_rows = snapshot(&legacy);
        assert_eq!(observed_rows, manifest["original_rows"]);
        assert_destination_fixture_history(
            &manifest["original_columns"],
            &observed_rows,
            &manifest["destination_history"],
            &manifest["project"],
        );
        assert_eq!(upgraded.load_project(project.id).unwrap(), project);
        assert_eq!(upgraded.read_blob(digest, 1024).unwrap(), ORIGINAL);
        assert_eq!(
            legacy
                .query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))
                .unwrap(),
            6
        );
        drop(prepared);
        drop(legacy);
        drop(upgraded);
        let reopened =
            Store::open_with_managed_blob_root(&database, root.join("destination/blobs")).unwrap();
        assert_eq!(reopened.load_project(project.id).unwrap(), project);
        assert_eq!(reopened.read_blob(digest, 1024).unwrap(), ORIGINAL);
        let final_read = Connection::open(&database).unwrap();
        let observed_rows = snapshot(&final_read);
        assert_eq!(observed_rows, manifest["original_rows"]);
        assert_destination_fixture_history(
            &manifest["original_columns"],
            &observed_rows,
            &manifest["destination_history"],
            &manifest["project"],
        );
        assert_eq!(fs::read(anchor_path).unwrap(), anchor_bytes);
    }
}
