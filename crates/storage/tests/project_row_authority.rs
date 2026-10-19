use motionwright_domain::Change;
use motionwright_domain::{Asset, DomainError, Project};
use motionwright_storage::Store;
use motionwright_storage::{FileImportIntent, ProductionReceiptInput, StorageError};
use rusqlite::{Connection, params, types::Value};
use std::{fs, path::Path};
use uuid::Uuid;

type Snapshot = Vec<Vec<Vec<Value>>>;

fn snapshot(connection: &Connection) -> Snapshot {
    [
        "SELECT * FROM projects ORDER BY id",
        "SELECT * FROM events ORDER BY project_id,revision",
        "SELECT * FROM receipts ORDER BY project_id,request_id",
        "SELECT * FROM production_receipts ORDER BY receipt_id",
        "SELECT * FROM render_window_cache_entries ORDER BY project_id,generation,revision,deliverable_id,source_start,source_end,fingerprint_sha256",
        "SELECT * FROM render_window_cache_evictions ORDER BY project_id,generation,revision,deliverable_id,source_start,source_end,fingerprint_sha256",
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

fn effect_counts(connection: &Connection, project_id: &str) -> (i64, i64) {
    connection
        .query_row(
            "SELECT
             (SELECT COUNT(*) FROM events WHERE project_id=?1),
             (SELECT COUNT(*) FROM receipts WHERE project_id=?1)",
            [project_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .unwrap()
}

#[test]
fn ordinary_project_commits_replay_and_reopen_keep_their_own_identity() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let a = store.create_named_project("Project A").unwrap();
    let b = store.create_named_project("Project B").unwrap();
    assert_ne!(a.id, b.id);
    assert_ne!(a.generation, b.generation);
    let oracle = Connection::open(&path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let change_a = Change::RenameProject {
        title: "Project A edited".into(),
    };
    let first = store
        .apply(a.id, &a.stamp(), "row-authority/a", &change_a)
        .unwrap();
    assert_eq!(first.project.id, a.id);
    assert_eq!(first.project.generation, a.generation);
    assert_eq!(first.project.revision, 1);
    assert_eq!(store.load_project(b.id).unwrap(), b);
    let second = store
        .apply(
            b.id,
            &b.stamp(),
            "row-authority/b",
            &Change::RenameProject {
                title: "Project B edited".into(),
            },
        )
        .unwrap();
    assert_eq!(second.project.id, b.id);
    assert_eq!(second.project.generation, b.generation);
    assert_eq!(second.project.revision, 1);
    assert_eq!(store.load_project(a.id).unwrap(), first.project);
    assert_eq!(effect_counts(&oracle, &a.id.to_string()), (1, 1));
    assert_eq!(effect_counts(&oracle, &b.id.to_string()), (1, 1));
    let before_replay = snapshot(&oracle);
    let replay = store
        .apply(a.id, &a.stamp(), "row-authority/a", &change_a)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(snapshot(&oracle), before_replay);
    drop(store);
    let reopened = Store::open(&path).unwrap();
    assert_eq!(reopened.load_project(a.id).unwrap(), first.project);
    assert_eq!(reopened.load_project(b.id).unwrap(), second.project);
    assert_eq!(snapshot(&oracle), before_replay);
}

#[test]
fn a_valid_document_in_the_wrong_row_cannot_redirect_a_commit() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let a = store.create_named_project("Requested project A").unwrap();
    let b = store.create_named_project("Unrelated project B").unwrap();
    assert_ne!(a.id, b.id);
    b.validate().unwrap();
    let oracle = Connection::open(&path).unwrap();
    assert_eq!(
        oracle
            .execute(
                "UPDATE projects SET document_json=?1 WHERE id=?2",
                params![serde_json::to_string(&b).unwrap(), a.id.to_string()],
            )
            .unwrap(),
        1
    );
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = store.apply(
        a.id,
        &b.stamp(),
        "row-authority/must-not-redirect",
        &Change::RenameProject {
            title: "Unexpected cross-project edit".into(),
        },
    );
    let after = snapshot(&oracle);
    let b_title: String = oracle
        .query_row(
            "SELECT title FROM projects WHERE id=?1",
            [b.id.to_string()],
            |row| row.get(0),
        )
        .unwrap();
    assert!(
        result.is_err(),
        "apply addressed to A succeeded; A (events,receipts)={:?}, B (events,receipts)={:?}, B title={b_title:?}",
        effect_counts(&oracle, &a.id.to_string()),
        effect_counts(&oracle, &b.id.to_string()),
    );
    assert_eq!(after, before, "rejection changed persisted rows or effects");
    assert_eq!(store.load_project(b.id).unwrap(), b);
    assert_eq!(effect_counts(&oracle, &a.id.to_string()), (0, 0));
    assert_eq!(effect_counts(&oracle, &b.id.to_string()), (0, 0));
}

fn fixture() -> (tempfile::TempDir, Store, Connection, Project, Project) {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let a = store
        .create_named_project("  Requested project A  ")
        .unwrap();
    let b = store.create_named_project("Unrelated project B").unwrap();
    let oracle = Connection::open(&path).unwrap();
    (directory, store, oracle, a, b)
}

fn corrupt_row(oracle: &Connection, a: &Project, b: &Project, field: &str) {
    let changed = match field {
        "document_id" => oracle.execute(
            "UPDATE projects SET document_json=?1 WHERE id=?2",
            params![serde_json::to_string(b).unwrap(), a.id.to_string()],
        ),
        "generation" => oracle.execute(
            "UPDATE projects SET generation=?1 WHERE id=?2",
            params![b.generation.to_string(), a.id.to_string()],
        ),
        "revision" => oracle.execute(
            "UPDATE projects SET revision=revision+1 WHERE id=?1",
            [a.id.to_string()],
        ),
        // A normalized equality check would miss this actual column mismatch.
        "title" => oracle.execute(
            "UPDATE projects SET title=?1 WHERE id=?2",
            params![a.title.trim(), a.id.to_string()],
        ),
        _ => panic!("unknown fixture field"),
    };
    assert_eq!(changed.unwrap(), 1);
}

fn assert_row_invalid<T: std::fmt::Debug>(result: motionwright_storage::Result<T>) {
    assert!(
        matches!(&result, Err(StorageError::Domain(DomainError::Invalid(_)))),
        "expected current row authority rejection, observed {result:?}",
    );
}

fn cas_inventory(root: &Path) -> Vec<(String, Option<Vec<u8>>)> {
    fn visit(root: &Path, here: &Path, output: &mut Vec<(String, Option<Vec<u8>>)>) {
        let mut entries = fs::read_dir(here)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect::<Vec<_>>();
        entries.sort();
        for path in entries {
            let metadata = fs::symlink_metadata(&path).unwrap();
            assert!(!metadata.file_type().is_symlink());
            let name = path
                .strip_prefix(root)
                .unwrap()
                .to_str()
                .unwrap()
                .to_owned();
            if metadata.is_dir() {
                output.push((name, None));
                visit(root, &path, output);
            } else {
                assert!(metadata.is_file());
                output.push((name, Some(fs::read(&path).unwrap())));
            }
        }
    }
    let mut result = Vec::new();
    visit(root, root, &mut result);
    result
}

fn production_input(project: &Project, key: &str) -> ProductionReceiptInput {
    ProductionReceiptInput {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        request_id: key.into(),
        request_sha256: "ab".repeat(32),
        command: "driver.motion-canvas.render.start".into(),
        stage: "completed".into(),
        payload: serde_json::json!({"synthetic_fixture": true}),
    }
}

#[test]
fn full_project_loads_reject_individually_corrupted_authority_columns() {
    for field in ["document_id", "generation", "revision", "title"] {
        let (_directory, store, oracle, a, b) = fixture();
        corrupt_row(&oracle, &a, &b, field);
        let before = snapshot(&oracle);
        assert_row_invalid(store.load_project(a.id));
        assert_eq!(snapshot(&oracle), before, "load repaired {field}");
        assert_eq!(store.load_project(b.id).unwrap(), b);
    }
}

#[test]
fn full_project_lists_reject_individually_corrupted_authority_columns() {
    for field in ["document_id", "generation", "revision", "title"] {
        let (_directory, store, oracle, a, b) = fixture();
        corrupt_row(&oracle, &a, &b, field);
        let before = snapshot(&oracle);
        assert_row_invalid(store.list_projects(100));
        assert_eq!(snapshot(&oracle), before, "list repaired {field}");
    }
}

#[test]
fn new_commits_reject_individually_corrupted_columns_without_effects() {
    for field in ["generation", "revision", "title"] {
        let (_directory, mut store, oracle, a, b) = fixture();
        corrupt_row(&oracle, &a, &b, field);
        let before = snapshot(&oracle);
        assert_row_invalid(store.apply(
            a.id,
            &a.stamp(),
            "row-authority/column-commit",
            &Change::RenameProject {
                title: "Must not replace a corrupt row".into(),
            },
        ));
        assert_eq!(snapshot(&oracle), before, "commit changed {field}");
    }
}

#[test]
fn corrupt_current_row_is_rejected_before_receipt_replay() {
    for field in ["generation", "revision", "title"] {
        let (_directory, mut store, oracle, a, b) = fixture();
        let change = Change::RenameProject {
            title: "  First result  ".into(),
        };
        let original = store
            .apply(a.id, &a.stamp(), "row-authority/replay", &change)
            .unwrap();
        corrupt_row(&oracle, &original.project, &b, field);
        let before = snapshot(&oracle);
        assert_row_invalid(store.apply(a.id, &a.stamp(), "row-authority/replay", &change));
        assert_eq!(snapshot(&oracle), before, "replay changed {field}");
    }
}

#[test]
fn corrupt_row_rejection_precedes_missing_cas_source_access() {
    let (_directory, mut store, oracle, a, b) = fixture();
    corrupt_row(&oracle, &a, &b, "document_id");
    let before = snapshot(&oracle);
    let cas_before = cas_inventory(store.blob_root());
    let missing = Change::AddAsset {
        asset: Asset {
            id: Uuid::now_v7(),
            name: "nonexistent-source.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some("cd".repeat(32)),
            source_revision: Some("local-import".into()),
        },
    };
    // The former implementation accepts B's JSON stamp, then reaches BlobMissing.
    assert_row_invalid(store.apply(a.id, &b.stamp(), "row-authority/no-source-io", &missing));
    assert_eq!(snapshot(&oracle), before);
    assert_eq!(cas_inventory(store.blob_root()), cas_before);
}

#[test]
fn cross_project_rejection_preserves_both_receipt_and_cache_streams() {
    let (_directory, mut store, oracle, a, b) = fixture();
    for (index, project) in [&a, &b].into_iter().enumerate() {
        store
            .append_production_receipt(production_input(project, "row-authority/prior"))
            .unwrap();
        // These are independent SQL fixtures of disposable registrations.
        // They do not claim successful rendering or native cache admission.
        for table in [
            "render_window_cache_entries",
            "render_window_cache_evictions",
        ] {
            oracle.execute(
                &format!(
                    "INSERT INTO {table}(project_id,generation,revision,deliverable_id,source_start,source_end,fingerprint_sha256)
                     VALUES(?1,?2,0,?3,0,1,?4)"
                ),
                params![
                    project.id.to_string(),
                    project.generation.to_string(),
                    project.deliverables[0].id.to_string(),
                    if index == 0 { "12".repeat(32) } else { "34".repeat(32) },
                ],
            ).unwrap();
        }
    }
    corrupt_row(&oracle, &a, &b, "document_id");
    let before = snapshot(&oracle);
    let cas_before = cas_inventory(store.blob_root());
    assert_row_invalid(store.apply(
        a.id,
        &b.stamp(),
        "row-authority/no-cross-owner-effects",
        &Change::RenameProject {
            title: "Must not invalidate B".into(),
        },
    ));
    assert_eq!(snapshot(&oracle), before);
    assert_eq!(cas_inventory(store.blob_root()), cas_before);
}

#[test]
fn production_append_rejects_corrupt_rows_before_recording_observations() {
    for field in ["document_id", "generation", "revision", "title"] {
        let (_directory, mut store, oracle, a, b) = fixture();
        corrupt_row(&oracle, &a, &b, field);
        let before = snapshot(&oracle);
        let mut input = production_input(&a, "row-authority/append");
        if field == "document_id" {
            // The former append method trusts B's valid generation in A's row.
            input.generation = b.generation;
        }
        assert_row_invalid(store.append_production_receipt(input));
        assert_eq!(snapshot(&oracle), before, "append changed {field}");
    }
}

#[test]
fn recovery_claim_rejects_corrupt_rows_before_allocating_an_attempt() {
    for field in ["document_id", "generation", "revision", "title"] {
        let (_directory, mut store, oracle, a, b) = fixture();
        corrupt_row(&oracle, &a, &b, field);
        let before = snapshot(&oracle);
        let mut input = production_input(&a, "row-authority/recovery");
        input.command = "motionwright.recovery.blender".into();
        input.stage = "dispatching".into();
        if field == "generation" {
            input.generation = b.generation;
        } else if field == "revision" {
            input.revision = a.revision + 1;
        }
        assert_row_invalid(store.claim_recovery_stage(input));
        assert_eq!(snapshot(&oracle), before, "claim changed {field}");
    }
}

#[test]
fn file_import_absence_does_not_bypass_current_row_validation() {
    for field in ["document_id", "generation", "revision", "title"] {
        let (_directory, mut store, oracle, a, b) = fixture();
        corrupt_row(&oracle, &a, &b, field);
        let before = snapshot(&oracle);
        let result = store.inspect_file_import(
            a.id,
            &a.stamp(),
            "row-authority/absent-import",
            &FileImportIntent::Asset {
                name: "original.bin".into(),
                media_type: "application/octet-stream".into(),
            },
            false,
        );
        assert!(
            matches!(&result, Err(StorageError::InvalidFileImportReceipt(_))),
            "expected explicit current-row import rejection for {field}: {result:?}",
        );
        assert_eq!(snapshot(&oracle), before);
    }
}

#[test]
fn historical_import_survives_later_edits_and_source_deletion_but_not_row_corruption() {
    let (directory, mut store, oracle, a, b) = fixture();
    let source = directory.path().join("original.bin");
    fs::write(&source, b"original synthetic file-import bytes").unwrap();
    let descriptor = store.ingest_blob_file(&source).unwrap();
    let stored_source = store.verified_blob_path(&descriptor.sha256).unwrap();
    let change = Change::AddAsset {
        asset: Asset {
            id: Uuid::now_v7(),
            name: "original.bin".into(),
            media_type: "application/octet-stream".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: Some("local-import".into()),
        },
    };
    let intent = FileImportIntent::Asset {
        name: "original.bin".into(),
        media_type: "application/octet-stream".into(),
    };
    let first = store
        .apply(a.id, &a.stamp(), "row-authority/import", &change)
        .unwrap();
    let latest = store
        .apply(
            a.id,
            &first.project.stamp(),
            "row-authority/later",
            &Change::RenameProject {
                title: "  Later exact title  ".into(),
            },
        )
        .unwrap();
    fs::remove_file(&source).unwrap();
    fs::remove_file(&stored_source).unwrap();
    drop(store);
    let mut store = Store::open(directory.path().join("state.sqlite3")).unwrap();
    let before = snapshot(&oracle);
    let cas_before = cas_inventory(store.blob_root());
    let observed = store
        .inspect_file_import(a.id, &a.stamp(), "row-authority/import", &intent, true)
        .unwrap()
        .expect("original typed import remains observable");
    assert!(observed.outcome.replayed);
    assert_eq!(observed.outcome.project, first.project);
    assert_eq!(observed.source_sha256, descriptor.sha256);
    let replay = store
        .apply(a.id, &a.stamp(), "row-authority/import", &change)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(store.load_project(a.id).unwrap(), latest.project);
    assert_eq!(snapshot(&oracle), before);
    assert_eq!(cas_inventory(store.blob_root()), cas_before);
    corrupt_row(&oracle, &latest.project, &b, "title");
    let corrupt_before = snapshot(&oracle);
    assert!(matches!(
        store.inspect_file_import(a.id, &a.stamp(), "row-authority/import", &intent, true),
        Err(StorageError::InvalidFileImportReceipt(_))
    ));
    assert_row_invalid(store.apply(a.id, &a.stamp(), "row-authority/import", &change));
    assert_eq!(snapshot(&oracle), corrupt_before);
    assert!(!source.exists());
    assert!(!stored_source.exists());
}

#[test]
fn historical_production_observation_and_exact_recovery_claim_keep_distinct_rules() {
    let (_directory, mut store, oracle, a, _b) = fixture();
    let latest = store
        .apply(
            a.id,
            &a.stamp(),
            "row-authority/advance",
            &Change::RenameProject {
                title: "Current revision one".into(),
            },
        )
        .unwrap()
        .project;
    let historical = store
        .append_production_receipt(production_input(&a, "row-authority/historical-observation"))
        .unwrap();
    assert_eq!(historical.revision, 0);
    assert_eq!(store.load_project(a.id).unwrap(), latest);

    let mut claim = production_input(&a, "row-authority/exact-claim");
    claim.command = "motionwright.recovery.blender".into();
    claim.stage = "dispatching".into();
    let before = snapshot(&oracle);
    assert!(matches!(
        store.claim_recovery_stage(claim.clone()),
        Err(StorageError::Conflict {
            expected: 0,
            actual: 1
        })
    ));
    assert_eq!(snapshot(&oracle), before);
    claim.revision = latest.revision;
    let original_claim = store.claim_recovery_stage(claim.clone()).unwrap();
    assert_eq!(original_claim.revision, latest.revision);
    assert_eq!(original_claim.payload["attempt"], serde_json::json!(1));
    let claimed = snapshot(&oracle);
    claim.request_id = "row-authority/new-key-cannot-bypass-unknown".into();
    assert!(matches!(
        store.claim_recovery_stage(claim),
        Err(StorageError::RequestReuse)
    ));
    assert_eq!(snapshot(&oracle), claimed);
}

#[test]
fn exact_title_and_column_only_summary_contract_remain_compatible() {
    let (_directory, mut store, oracle, a, b) = fixture();
    assert_eq!(
        store.load_project(a.id).unwrap().title,
        "  Requested project A  "
    );
    let listed = store.list_projects(100).unwrap();
    assert_eq!(listed.iter().find(|project| project.id == a.id), Some(&a));
    let changed = store
        .apply(
            a.id,
            &a.stamp(),
            "row-authority/exact-title",
            &Change::RenameProject {
                title: "  Unicode café title  ".into(),
            },
        )
        .unwrap()
        .project;
    assert_eq!(changed.title, "  Unicode café title  ");
    assert_eq!(store.load_project(a.id).unwrap(), changed);
    corrupt_row(&oracle, &changed, &b, "document_id");
    let before = snapshot(&oracle);
    let page = store.list_project_summaries(None, 100).unwrap();
    let summary = page.items.iter().find(|item| item.id == a.id).unwrap();
    assert_eq!(summary.id, a.id);
    assert_eq!(summary.generation, a.generation);
    assert_eq!(summary.revision, changed.revision);
    assert_eq!(summary.title, changed.title);
    assert_eq!(snapshot(&oracle), before);
    assert_row_invalid(store.load_project(a.id));
}
