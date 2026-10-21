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

// The first regression above is byte-preserved from the test-only baseline.
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyReceiptInvalid, ApplyReceiptObservation,
    MAX_APPLY_BINDING_BYTES, StorageError, VerifiedApplyReceiptV1,
};
use rusqlite::types::{Type, ValueRef};

type RawSnapshot = Vec<Vec<Vec<(Type, Vec<u8>)>>>;

// Keep invalid UTF-8 as raw bytes so the negative oracle cannot itself hide
// malformed SQLite TEXT. The type tag distinguishes TEXT from identical BLOB.
fn raw_snapshot(connection: &Connection) -> RawSnapshot {
    [
        "SELECT * FROM projects ORDER BY id",
        "SELECT * FROM events ORDER BY project_id,revision",
        "SELECT * FROM receipts ORDER BY project_id,request_id",
    ]
    .into_iter()
    .map(|sql| {
        let mut statement = connection.prepare(sql).unwrap();
        let columns = statement.column_count();
        statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| {
                        let value = row.get_ref(column)?;
                        let bytes = match value {
                            ValueRef::Null => Vec::new(),
                            ValueRef::Integer(number) => number.to_be_bytes().to_vec(),
                            ValueRef::Real(number) => number.to_bits().to_be_bytes().to_vec(),
                            ValueRef::Text(bytes) | ValueRef::Blob(bytes) => bytes.to_vec(),
                        };
                        Ok((value.data_type(), bytes))
                    })
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    })
    .collect()
}

fn unchanged<T>(store: &mut Store, operation: impl FnOnce(&mut Store) -> T) -> T {
    let oracle = Connection::open(store.path()).unwrap();
    oracle.pragma_update(None, "query_only", true).unwrap();
    let before = raw_snapshot(&oracle);
    let result = operation(store);
    assert_eq!(raw_snapshot(&oracle), before, "operation changed SQL rows");
    result
}

impl KnownRequest {
    fn reopen(self) -> Self {
        let Self {
            store,
            base,
            change,
            original,
            _directory,
        } = self;
        let path = store.path().to_owned();
        drop(store);
        Self {
            store: Store::open(path).unwrap(),
            base,
            change,
            original,
            _directory,
        }
    }

    fn observe(&mut self) -> ApplyReceiptObservation {
        let id = self.base.id;
        let stamp = self.base.stamp();
        let key = self.original.request_id.clone();
        let change = self.change.clone();
        unchanged(&mut self.store, |store| {
            store
                .observe_apply_receipt_v1(id, &stamp, &key, &change, APPLY_REQUEST_ENCODING_V1)
                .unwrap()
        })
    }

    fn binding_json(&self) -> String {
        Connection::open(self.store.path())
            .unwrap()
            .query_row(
                "SELECT apply_binding_json FROM receipts WHERE project_id=?1 AND request_id=?2",
                params![self.base.id.to_string(), &self.original.request_id],
                |row| row.get(0),
            )
            .unwrap()
    }

    fn replace_binding(&self, version: i64, json: Option<&str>) {
        let oracle = Connection::open(self.store.path()).unwrap();
        oracle
            .pragma_update(None, "ignore_check_constraints", true)
            .unwrap();
        assert_eq!(
            oracle
                .execute(
                    "UPDATE receipts SET apply_binding_version=?1,apply_binding_json=?2
             WHERE project_id=?3 AND request_id=?4",
                    params![
                        version,
                        json,
                        self.base.id.to_string(),
                        &self.original.request_id
                    ],
                )
                .unwrap(),
            1
        );
        oracle
            .pragma_update(None, "ignore_check_constraints", false)
            .unwrap();
    }
}

fn verified(observation: ApplyReceiptObservation) -> Box<VerifiedApplyReceiptV1> {
    match observation {
        ApplyReceiptObservation::KnownVerifiedV1 { binding } => binding,
        other => panic!("expected verified V1: {other:?}"),
    }
}

fn assert_integrity(observation: ApplyReceiptObservation) {
    assert_eq!(
        observation,
        ApplyReceiptObservation::Invalid {
            reason: ApplyReceiptInvalid::ReceiptIntegrity
        },
    );
}

#[test]
fn lost_ack_after_later_replay_observes_the_original_commit_after_reopen() {
    let mut fixture = known_request();
    let later = fixture
        .store
        .apply(
            fixture.base.id,
            &fixture.original.project.stamp(),
            "later-title",
            &Change::RenameProject {
                title: "Later title".into(),
            },
        )
        .unwrap();
    // This later replay attempt, not the first execution, loses its response.
    let lost_ack_expected = later.project.stamp();
    let replay = unchanged(&mut fixture.store, |store| {
        store
            .apply(
                fixture.base.id,
                &lost_ack_expected,
                &fixture.original.request_id,
                &fixture.change,
            )
            .unwrap()
    });
    assert!(replay.replayed);
    assert_eq!(replay.project, fixture.original.project);
    fixture = fixture.reopen();
    let observation = unchanged(&mut fixture.store, |store| {
        store
            .observe_apply_receipt_v1(
                fixture.base.id,
                &lost_ack_expected,
                &fixture.original.request_id,
                &fixture.change,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
    });
    let binding = verified(observation);
    assert_eq!(binding.base_revision, fixture.base.revision.to_string());
    assert_eq!(binding.base_branch, fixture.base.active_branch.to_string());
    assert_eq!(
        binding.result_revision,
        fixture.original.project.revision.to_string()
    );
    assert_eq!(
        binding.result_branch,
        fixture.original.project.active_branch.to_string()
    );
    assert_eq!(
        fixture
            .store
            .export_project(fixture.base.id)
            .unwrap()
            .project,
        later.project
    );
}

#[test]
fn legacy_representation_remains_unverified_without_backfill_or_reapplication() {
    let mut fixture = known_request();
    fixture.replace_binding(0, None);
    let later = fixture
        .store
        .apply(
            fixture.base.id,
            &fixture.original.project.stamp(),
            "later-legacy",
            &Change::RenameProject {
                title: "Later legacy head".into(),
            },
        )
        .unwrap();
    fixture = fixture.reopen();
    assert_eq!(fixture.observe(), ApplyReceiptObservation::LegacyUnverified);
    let replay = unchanged(&mut fixture.store, |store| {
        store
            .apply(
                fixture.base.id,
                &later.project.stamp(),
                &fixture.original.request_id,
                &fixture.change,
            )
            .unwrap()
    });
    assert!(replay.replayed);
    assert_eq!(replay.project, fixture.original.project);
    assert_eq!(fixture.observe(), ApplyReceiptObservation::LegacyUnverified);
    assert_eq!(
        fixture
            .store
            .export_project(fixture.base.id)
            .unwrap()
            .project,
        later.project
    );
}

#[test]
fn observer_distinguishes_mismatch_absence_and_authority_without_writes() {
    let mut fixture = known_request();
    let different = Change::RenameProject {
        title: "Different intent".into(),
    };
    let mismatch = unchanged(&mut fixture.store, |store| {
        store
            .observe_apply_receipt_v1(
                fixture.base.id,
                &fixture.base.stamp(),
                &fixture.original.request_id,
                &different,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
    });
    assert_eq!(
        mismatch,
        ApplyReceiptObservation::Invalid {
            reason: ApplyReceiptInvalid::RequestMismatch,
        }
    );
    let missing = unchanged(&mut fixture.store, |store| {
        store
            .observe_apply_receipt_v1(
                fixture.base.id,
                &fixture.base.stamp(),
                "never-submitted",
                &fixture.change,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
    });
    assert_eq!(missing, ApplyReceiptObservation::NotFound);
    let mut foreign = fixture.base.stamp();
    foreign.generation = uuid::Uuid::now_v7();
    let authority = unchanged(&mut fixture.store, |store| {
        store
            .observe_apply_receipt_v1(
                fixture.base.id,
                &foreign,
                &fixture.original.request_id,
                &fixture.change,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
    });
    assert_eq!(authority, ApplyReceiptObservation::AuthorityMismatch);
    verified(fixture.observe());
}

#[test]
fn unsupported_contract_is_explicit_and_never_becomes_absence_or_legacy() {
    for case in ["request-encoding", "row-marker", "internal-format"] {
        let mut fixture = known_request();
        let mut encoding = APPLY_REQUEST_ENCODING_V1;
        match case {
            "request-encoding" => encoding = 99,
            "row-marker" => fixture.replace_binding(99, Some(&fixture.binding_json())),
            "internal-format" => {
                let mut binding: serde_json::Value =
                    serde_json::from_str(&fixture.binding_json()).unwrap();
                binding["format"] = serde_json::json!("unsupported-test-format/99");
                fixture.replace_binding(1, Some(&serde_json::to_string(&binding).unwrap()));
            }
            _ => unreachable!(),
        }
        fixture = fixture.reopen();
        let observation = unchanged(&mut fixture.store, |store| {
            store
                .observe_apply_receipt_v1(
                    fixture.base.id,
                    &fixture.base.stamp(),
                    &fixture.original.request_id,
                    &fixture.change,
                    encoding,
                )
                .unwrap()
        });
        assert_eq!(
            observation,
            ApplyReceiptObservation::Invalid {
                reason: ApplyReceiptInvalid::UnsupportedContract,
            },
            "{case}"
        );
    }
}

#[test]
fn malformed_or_oversized_binding_never_downgrades_or_repairs() {
    for case in [
        "duplicate",
        "unknown-field",
        "noncanonical-revision",
        "missing-binding",
        "empty-binding",
        "oversized-binding",
        "v0-nonnull",
        "v0-oversized",
        "changed-result",
        "result-whitespace",
    ] {
        let mut fixture = known_request();
        let raw = fixture.binding_json();
        let mut binding: serde_json::Value = serde_json::from_str(&raw).unwrap();
        match case {
            "duplicate" => {
                let rest = raw.strip_prefix('{').unwrap();
                fixture.replace_binding(
                    1,
                    Some(&format!(
                        "{{\"base_revision\":{},{}",
                        binding["base_revision"], rest,
                    )),
                );
            }
            "unknown-field" => {
                binding["unknown"] = serde_json::json!(true);
                fixture.replace_binding(1, Some(&serde_json::to_string(&binding).unwrap()));
            }
            "noncanonical-revision" => {
                binding["base_revision"] = serde_json::json!("00");
                fixture.replace_binding(1, Some(&serde_json::to_string(&binding).unwrap()));
            }
            "missing-binding" => fixture.replace_binding(1, None),
            "empty-binding" => fixture.replace_binding(1, Some("")),
            "oversized-binding" | "v0-oversized" => {
                fixture.replace_binding(
                    if case == "v0-oversized" { 0 } else { 1 },
                    Some(&" ".repeat(MAX_APPLY_BINDING_BYTES + 1)),
                );
            }
            "v0-nonnull" => fixture.replace_binding(0, Some("")),
            "changed-result" | "result-whitespace" => {
                let oracle = Connection::open(fixture.store.path()).unwrap();
                let result = if case == "changed-result" {
                    let mut changed = fixture.original.clone();
                    changed.project.title = "Valid but uncommitted title".into();
                    changed.project.validate().unwrap();
                    serde_json::to_string(&changed).unwrap()
                } else {
                    format!(" {}", serde_json::to_string(&fixture.original).unwrap())
                };
                assert_eq!(oracle.execute(
                    "UPDATE receipts SET result_json=?1 WHERE project_id=?2 AND request_id=?3",
                    params![result, fixture.base.id.to_string(), &fixture.original.request_id],
                ).unwrap(), 1);
            }
            _ => unreachable!(),
        }
        fixture = fixture.reopen();
        assert_integrity(fixture.observe());
        let replay = unchanged(&mut fixture.store, |store| {
            store.apply(
                fixture.base.id,
                &fixture.base.stamp(),
                &fixture.original.request_id,
                &fixture.change,
            )
        });
        assert!(
            matches!(replay, Err(StorageError::InvalidApplyReceipt(_))),
            "{case}"
        );
    }
}

#[test]
fn invalid_sqlite_receipt_types_and_utf8_are_content_failures() {
    for assignment in [
        "apply_binding_version='not-an-integer'",
        "apply_binding_version=0.5",
        "apply_binding_json=CAST(apply_binding_json AS BLOB)",
        "apply_binding_json=CAST(x'80' AS TEXT)",
        "apply_binding_version=0,apply_binding_json=x'7b7d'",
        "request_sha256=CAST(request_sha256 AS BLOB)",
        "result_json=CAST(result_json AS BLOB)",
        "result_json=CAST(x'80' AS TEXT)",
    ] {
        let mut fixture = known_request();
        let oracle = Connection::open(fixture.store.path()).unwrap();
        oracle
            .pragma_update(None, "ignore_check_constraints", true)
            .unwrap();
        assert_eq!(
            oracle
                .execute(
                    &format!(
                        "UPDATE receipts SET {assignment} WHERE project_id=?1 AND request_id=?2"
                    ),
                    params![fixture.base.id.to_string(), &fixture.original.request_id],
                )
                .unwrap(),
            1
        );
        oracle
            .pragma_update(None, "ignore_check_constraints", false)
            .unwrap();
        drop(oracle);
        fixture = fixture.reopen();
        assert_integrity(fixture.observe());
        let replay = unchanged(&mut fixture.store, |store| {
            store.apply(
                fixture.base.id,
                &fixture.base.stamp(),
                &fixture.original.request_id,
                &fixture.change,
            )
        });
        assert!(
            matches!(replay, Err(StorageError::InvalidApplyReceipt(_))),
            "{assignment}"
        );
    }
}

#[test]
fn historical_branch_switch_is_not_reconstructed_from_the_current_head() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("branch.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let original = store.create_named_project("Branch authority").unwrap();
    let created = store
        .apply(
            original.id,
            &original.stamp(),
            "create-branch",
            &Change::CreateBranch {
                name: "Review branch".into(),
            },
        )
        .unwrap();
    let branch = created
        .project
        .branches
        .iter()
        .find(|branch| branch.name == "Review branch")
        .unwrap()
        .id;
    assert_eq!(created.project.active_branch, original.active_branch);
    let switch = Change::CheckoutBranch { branch_id: branch };
    let switched = store
        .apply(
            original.id,
            &created.project.stamp(),
            "switch-branch",
            &switch,
        )
        .unwrap();
    assert_ne!(
        switched.project.active_branch,
        created.project.active_branch
    );
    let later = store
        .apply(
            original.id,
            &switched.project.stamp(),
            "switch-back",
            &Change::CheckoutBranch {
                branch_id: original.active_branch,
            },
        )
        .unwrap();
    drop(store);
    let mut store = Store::open(&path).unwrap();
    let observation = unchanged(&mut store, |store| {
        store
            .observe_apply_receipt_v1(
                original.id,
                &later.project.stamp(),
                "switch-branch",
                &switch,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
    });
    let binding = verified(observation);
    assert_eq!(
        binding.base_branch,
        created.project.active_branch.to_string()
    );
    assert_eq!(
        binding.result_branch,
        switched.project.active_branch.to_string()
    );
    assert_ne!(binding.base_branch, binding.result_branch);
    assert_eq!(binding.base_revision, created.project.revision.to_string());
    assert_eq!(
        binding.result_revision,
        switched.project.revision.to_string()
    );
    let replay = unchanged(&mut store, |store| {
        store
            .apply(
                original.id,
                &later.project.stamp(),
                "switch-branch",
                &switch,
            )
            .unwrap()
    });
    assert!(replay.replayed);
    assert_eq!(replay.project, switched.project);
    assert_eq!(
        store.export_project(original.id).unwrap().project,
        later.project
    );
}

#[test]
fn exact_revisions_survive_above_javascript_integer_range_and_reject_sqlite_overflow() {
    for revision in [9_007_199_254_740_993_u64, i64::MAX as u64] {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("revision.sqlite3")).unwrap();
        let mut original = store.create_named_project("Exact revision").unwrap();
        original.revision = revision;
        original.validate().unwrap();
        let oracle = Connection::open(store.path()).unwrap();
        assert_eq!(
            oracle
                .execute(
                    "UPDATE projects SET revision=?1,document_json=?2 WHERE id=?3",
                    params![
                        i64::try_from(revision).unwrap(),
                        serde_json::to_string(&original).unwrap(),
                        original.id.to_string()
                    ],
                )
                .unwrap(),
            1
        );
        drop(oracle);
        let change = Change::RenameProject {
            title: "Next exact revision".into(),
        };
        if revision == i64::MAX as u64 {
            let outcome = unchanged(&mut store, |store| {
                store.apply(original.id, &original.stamp(), "overflow", &change)
            });
            assert!(matches!(outcome, Err(StorageError::Domain(_))));
            continue;
        }
        let outcome = store
            .apply(original.id, &original.stamp(), "exact", &change)
            .unwrap();
        let binding = verified(unchanged(&mut store, |store| {
            store
                .observe_apply_receipt_v1(
                    original.id,
                    &original.stamp(),
                    "exact",
                    &change,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap()
        }));
        assert_eq!(binding.base_revision, "9007199254740993");
        assert_eq!(binding.result_revision, "9007199254740994");
        assert_eq!(outcome.project.revision, revision + 1);
        let serialized = serde_json::to_value(&*binding).unwrap();
        assert!(serialized["base_revision"].is_string());
        assert!(serialized["result_revision"].is_string());
    }
}

#[test]
fn complete_observation_wire_fits_budget_for_maximum_request_keys() {
    for key in ["\\".repeat(256), "\"".repeat(256), "\u{00e9}".repeat(128)] {
        assert_eq!(key.len(), 256);
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("key.sqlite3")).unwrap();
        let project = store.create_named_project("Maximum request key").unwrap();
        let change = Change::RenameProject {
            title: "Committed with maximum key".into(),
        };
        store
            .apply(project.id, &project.stamp(), &key, &change)
            .unwrap();
        let observation = unchanged(&mut store, |store| {
            store
                .observe_apply_receipt_v1(
                    project.id,
                    &project.stamp(),
                    &key,
                    &change,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap()
        });
        assert_eq!(verified(observation.clone()).request_id, key);
        assert!(serde_json::to_vec(&observation).unwrap().len() <= MAX_APPLY_BINDING_BYTES);
    }
}

struct ImportedBindingFixture {
    store: Store,
    initial: Project,
    applied: ApplyOutcome,
    change: Change,
    intent: motionwright_storage::FileImportIntent,
    source: std::path::PathBuf,
    sha256: String,
    _directory: tempfile::TempDir,
}

fn imported_binding_fixture() -> ImportedBindingFixture {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("original.wav");
    // Storage integrity fixture; no claim of audio decoding.
    std::fs::write(&source, b"synthetic storage import bytes").unwrap();
    let mut store = Store::open(directory.path().join("import.sqlite3")).unwrap();
    let initial = store
        .create_named_project("Imported receipt history")
        .unwrap();
    let descriptor = store.ingest_blob_file(&source).unwrap();
    let change = Change::AddAsset {
        asset: motionwright_domain::Asset {
            id: uuid::Uuid::now_v7(),
            name: "original.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(descriptor.sha256.clone()),
            source_revision: Some("local-import".into()),
        },
    };
    let intent = motionwright_storage::FileImportIntent::Asset {
        name: "original.wav".into(),
        media_type: "audio/wav".into(),
    };
    let applied = store
        .apply(initial.id, &initial.stamp(), "import-v1", &change)
        .unwrap();
    assert!(
        store
            .inspect_file_import(initial.id, &initial.stamp(), "import-v1", &intent, true,)
            .unwrap()
            .is_some()
    );
    ImportedBindingFixture {
        store,
        initial,
        applied,
        change,
        intent,
        source,
        sha256: descriptor.sha256,
        _directory: directory,
    }
}

#[test]
fn imported_v1_result_and_event_kind_share_the_canonical_verifier() {
    for case in ["changed-result-title", "wrong-event-kind"] {
        let mut fixture = imported_binding_fixture();
        let oracle = Connection::open(fixture.store.path()).unwrap();
        match case {
            "changed-result-title" => {
                let mut changed = fixture.applied.clone();
                changed.project.title = "Valid but uncommitted import title".into();
                changed.project.validate().unwrap();
                assert_eq!(oracle.execute(
                    "UPDATE receipts SET result_json=?1 WHERE project_id=?2 AND request_id='import-v1'",
                    params![serde_json::to_string(&changed).unwrap(), fixture.initial.id.to_string()],
                ).unwrap(), 1);
            }
            "wrong-event-kind" => {
                assert_eq!(oracle.execute(
                    "UPDATE events SET kind='wrong-fixture-kind' WHERE project_id=?1 AND revision=?2",
                    params![fixture.initial.id.to_string(),
                        i64::try_from(fixture.applied.project.revision).unwrap()],
                ).unwrap(), 1);
            }
            _ => unreachable!(),
        }
        drop(oracle);
        let inspected = unchanged(&mut fixture.store, |store| {
            store.inspect_file_import(
                fixture.initial.id,
                &fixture.initial.stamp(),
                "import-v1",
                &fixture.intent,
                true,
            )
        });
        assert!(
            matches!(inspected, Err(StorageError::InvalidFileImportReceipt(_))),
            "{case}"
        );
        let replay = unchanged(&mut fixture.store, |store| {
            store.apply(
                fixture.initial.id,
                &fixture.initial.stamp(),
                "import-v1",
                &fixture.change,
            )
        });
        assert!(
            matches!(replay, Err(StorageError::InvalidApplyReceipt(_))),
            "{case}"
        );
        assert_integrity(unchanged(&mut fixture.store, |store| {
            store
                .observe_apply_receipt_v1(
                    fixture.initial.id,
                    &fixture.initial.stamp(),
                    "import-v1",
                    &fixture.change,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap()
        }));
    }
}

#[test]
fn imported_v1_observation_and_recovery_need_no_source_or_cas_after_reopen() {
    let fixture = imported_binding_fixture();
    let ImportedBindingFixture {
        mut store,
        initial,
        applied,
        change,
        intent,
        source,
        sha256,
        _directory,
    } = fixture;
    let later = store
        .apply(
            initial.id,
            &applied.project.stamp(),
            "later-import-title",
            &Change::RenameProject {
                title: "Later import head".into(),
            },
        )
        .unwrap();
    let cas = store.verified_blob_path(&sha256).unwrap();
    let database = store.path().to_owned();
    drop(store);
    std::fs::remove_file(&source).unwrap();
    std::fs::remove_file(&cas).unwrap();
    let mut store = Store::open(database).unwrap();
    let binding = verified(unchanged(&mut store, |store| {
        store
            .observe_apply_receipt_v1(
                initial.id,
                &later.project.stamp(),
                "import-v1",
                &change,
                APPLY_REQUEST_ENCODING_V1,
            )
            .unwrap()
    }));
    assert_eq!(binding.base_revision, initial.revision.to_string());
    assert_eq!(
        binding.result_revision,
        applied.project.revision.to_string()
    );
    let recorded = unchanged(&mut store, |store| {
        store
            .inspect_file_import(
                initial.id,
                &later.project.stamp(),
                "import-v1",
                &intent,
                true,
            )
            .unwrap()
            .unwrap()
    });
    assert!(recorded.outcome.replayed);
    assert_eq!(recorded.outcome.project, applied.project);
    assert_eq!(recorded.source_sha256, sha256);
    assert_eq!(
        store.export_project(initial.id).unwrap().project,
        later.project
    );
    assert!(!source.exists());
    assert!(!cas.exists());
    // Keep the fixture alive until after the reopened Store drops.
    drop(store);
    drop(_directory);
}

#[test]
fn typed_creation_rejects_generic_apply_binding_markers_without_repair() {
    for marker in [0_i64, 1_i64] {
        let directory = tempfile::tempdir().unwrap();
        let mut store = Store::open(directory.path().join("creation.sqlite3")).unwrap();
        let intent = motionwright_storage::ProjectCreationIntent {
            request_id: uuid::Uuid::now_v7(),
            title: "Typed creation".into(),
        };
        let original = store.create_project_with_intent(&intent).unwrap();
        assert!(store.recover_project_creation(&intent).unwrap().is_some());
        let oracle = Connection::open(store.path()).unwrap();
        let (key, version, binding): (String, i64, Option<String>) = oracle.query_row(
            "SELECT request_id,apply_binding_version,apply_binding_json FROM receipts WHERE project_id=?1",
            [original.project.id.to_string()],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        ).unwrap();
        assert_eq!((version, binding), (0, None));
        assert_eq!(
            oracle
                .execute(
                    "UPDATE receipts SET apply_binding_version=?1,apply_binding_json='{}'
             WHERE project_id=?2 AND request_id=?3",
                    params![marker, original.project.id.to_string(), key],
                )
                .unwrap(),
            1
        );
        drop(oracle);
        let recovery = unchanged(&mut store, |store| store.recover_project_creation(&intent));
        assert!(matches!(
            recovery,
            Err(StorageError::InvalidProjectCreationReceipt(_))
        ));
        let replay = unchanged(&mut store, |store| {
            store.create_project_with_intent(&intent)
        });
        assert!(matches!(
            replay,
            Err(StorageError::InvalidProjectCreationReceipt(_))
        ));
    }
}
