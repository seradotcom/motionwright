//! The same completion boundary used by ApplyHandler receives real service/SQLite
//! results. Known fixture commits are inspected; uncertain production requests
//! are never retried or repaired by these tests.
use super::{apply_completion, storage_error};
use motionwright_domain::{Change, Project};
use motionwright_service::StudioService;
use motionwright_storage::{ApplyOutcome, StorageError, Store};
use rusqlite::{Connection, params, types::Value};
use semwright_native_sdk::ErrorCode;
use semwright_native_sdk::cooperation::Completion;
use std::path::PathBuf;

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
        statement
            .query_map([], |row| {
                (0..columns)
                    .map(|column| row.get::<_, Value>(column))
                    .collect::<rusqlite::Result<Vec<_>>>()
            })
            .unwrap()
            .map(|row| row.unwrap())
            .collect()
    })
    .collect()
}

struct Fixture {
    service: StudioService,
    base: Project,
    path: PathBuf,
    _directory: tempfile::TempDir,
}

fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("owned.sqlite3");
    let mut store = Store::open(&path).unwrap();
    let base = store.create_named_project("Original title").unwrap();
    Fixture {
        service: StudioService::from_store(store),
        base,
        path,
        _directory: directory,
    }
}

fn intent() -> Change {
    Change::RenameProject {
        title: "Known committed title".into(),
    }
}

fn known_commit(f: &Fixture) -> ApplyOutcome {
    f.service
        .apply(f.base.id, &f.base.stamp(), "known-original", &intent())
        .unwrap()
}

fn applied(completion: Completion) -> ApplyOutcome {
    match completion {
        Completion::Applied(value) => {
            assert_eq!(value.as_object().unwrap().len(), 7);
            let project: Project = serde_json::from_value(value["project"].clone()).unwrap();
            assert_eq!(
                value["resource"].as_str(),
                Some(project.resource_key().as_str())
            );
            assert_eq!(
                value["generation"].as_str(),
                Some(project.generation.to_string().as_str())
            );
            assert_eq!(
                value["revision"].as_str(),
                Some(project.revision.to_string().as_str())
            );
            ApplyOutcome {
                project,
                previous_revision: value["previous_revision"]
                    .as_str()
                    .unwrap()
                    .parse()
                    .unwrap(),
                request_id: value["request_id"].as_str().unwrap().to_owned(),
                replayed: value["replayed"].as_bool().unwrap(),
            }
        }
        other => panic!("expected actual committed outcome, got {other:?}"),
    }
}

#[test]
fn invalid_receipt_error_is_constant_and_sanitized() {
    let marker = "synthetic-private-receipt-detail";
    let error = storage_error(StorageError::InvalidApplyReceipt(marker.into()));
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert_eq!(
        error.message,
        "Motionwright operation receipt validation failed"
    );
    assert!(!format!("{error:?}").contains(marker));
}

#[test]
fn new_commit_remains_applied_and_persisted() {
    let f = fixture();
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "new-known", &intent());
    let outcome = applied(apply_completion(result));
    assert!(!outcome.replayed);
    assert_eq!(outcome.project.revision, f.base.revision + 1);
    let reopened = Store::open(&f.path)
        .unwrap()
        .load_project(f.base.id)
        .unwrap();
    assert_eq!(reopened, outcome.project);
}

#[test]
fn verified_historical_result_keeps_original_revision_without_mutation() {
    let f = fixture();
    let original = known_commit(&f);
    let later = f
        .service
        .apply(
            f.base.id,
            &original.project.stamp(),
            "known-later",
            &Change::RenameProject {
                title: "Later owned edit".into(),
            },
        )
        .unwrap();
    let oracle = Connection::open(&f.path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "known-original", &intent());
    let outcome = applied(apply_completion(result));
    assert!(outcome.replayed);
    assert_eq!(outcome.project, original.project);
    assert_eq!(snapshot(&oracle), before);
    assert_eq!(
        Store::open(&f.path)
            .unwrap()
            .load_project(f.base.id)
            .unwrap(),
        later.project
    );
}

enum Damage {
    MalformedResult,
    WrongRequest,
    MalformedBinding,
}

fn corrupt_known_receipt_stays_uncertain(damage: Damage) {
    let f = fixture();
    let original = known_commit(&f);
    assert!(!original.replayed);
    let oracle = Connection::open(&f.path).unwrap();
    let (column, value) = match damage {
        Damage::MalformedResult => ("result_json", "{".to_owned()),
        Damage::WrongRequest => {
            let mut replaced = original.clone();
            replaced.request_id = "different-known-request".into();
            ("result_json", serde_json::to_string(&replaced).unwrap())
        }
        Damage::MalformedBinding => ("apply_binding_json", "{}".to_owned()),
    };
    assert_eq!(
        oracle
            .execute(
                &format!("UPDATE receipts SET {column}=?1 WHERE project_id=?2 AND request_id=?3"),
                params![value, f.base.id.to_string(), "known-original"],
            )
            .unwrap(),
        1
    );
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "known-original", &intent());
    assert!(matches!(&result, Err(StorageError::InvalidApplyReceipt(_))));
    assert_eq!(snapshot(&oracle), before);
    let current = Store::open(&f.path)
        .unwrap()
        .load_project(f.base.id)
        .unwrap();
    assert_eq!(
        current, original.project,
        "the original operation is known to have committed in this fixture"
    );
    let completion = apply_completion(result);
    assert!(
        matches!(&completion, Completion::Uncertain(_)),
        "INVALID_RECEIPT_MUST_NOT_ASSERT_NOT_APPLIED: {completion:?}"
    );
    let error = completion.into_result().unwrap_err();
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert_eq!(
        error.message,
        "Motionwright operation receipt validation failed"
    );
    assert!(
        !error.outcome_known,
        "SDK serialization must preserve original request uncertainty"
    );
    assert_eq!(
        snapshot(&oracle),
        before,
        "completion conversion must not mutate or retry"
    );
}

#[test]
fn malformed_historical_result_preserves_uncertainty() {
    corrupt_known_receipt_stays_uncertain(Damage::MalformedResult);
}

#[test]
fn foreign_historical_request_preserves_uncertainty() {
    corrupt_known_receipt_stays_uncertain(Damage::WrongRequest);
}

#[test]
fn malformed_historical_binding_preserves_uncertainty() {
    corrupt_known_receipt_stays_uncertain(Damage::MalformedBinding);
}

#[test]
fn new_stale_request_remains_known_rejected_without_mutation() {
    let f = fixture();
    known_commit(&f);
    let oracle = Connection::open(&f.path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f
        .service
        .apply(f.base.id, &f.base.stamp(), "new-stale", &intent());
    assert!(matches!(&result, Err(StorageError::Conflict { .. })));
    let completion = apply_completion(result);
    assert!(matches!(&completion, Completion::NotApplied(_)));
    let error = completion.into_result().unwrap_err();
    assert_eq!(error.code, ErrorCode::StaleReference);
    assert!(error.outcome_known);
    assert_eq!(snapshot(&oracle), before);
}

#[test]
fn different_intent_under_known_key_remains_rejected_without_mutation() {
    let f = fixture();
    known_commit(&f);
    let oracle = Connection::open(&f.path).unwrap();
    oracle.execute_batch("PRAGMA query_only=ON").unwrap();
    let before = snapshot(&oracle);
    let result = f.service.apply(
        f.base.id,
        &f.base.stamp(),
        "known-original",
        &Change::RenameProject {
            title: "A different operation".into(),
        },
    );
    assert!(matches!(&result, Err(StorageError::RequestReuse)));
    let completion = apply_completion(result);
    assert!(matches!(&completion, Completion::NotApplied(_)));
    let error = completion.into_result().unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(error.outcome_known);
    assert_eq!(snapshot(&oracle), before);
}

// Product consumer controls. Authority and Driver postcommit behavior require
// the separately owned genuine Core/Host cases; these tests never mint a context.
mod recovery_consumer_tests {
    use super::super::{
        apply_completion_with_recovery, apply_recovery_identity, apply_recovery_record,
        apply_recovery_requested, apply_result_value, decode_apply_recovery_identity,
        prepare_apply_recovery,
    };
    use super::*;
    use motionwright_storage::{
        APPLY_REQUEST_ENCODING_V1, ApplyReceiptInvalid, ApplyResultObservation,
    };
    use semwright_native_sdk::cooperation::{
        MAX_VALUE_BYTES, RecoveryRecord, RequestIdentity, validate_value,
    };
    use serde_json::{Value as JsonValue, json};
    use uuid::Uuid;

    fn parser_identity() -> RequestIdentity {
        apply_recovery_identity(
            Uuid::parse_str("12345678-1234-4abc-8def-123456789abc").unwrap(),
            Uuid::parse_str("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap(),
            "original:key",
            &Change::RenameProject {
                title: "Typed identity input".into(),
            },
        )
        .unwrap()
    }

    fn public_record(result: JsonValue) -> (RequestIdentity, RecoveryRecord) {
        let identity = parser_identity();
        let record = RecoveryRecord::Recorded {
            identity: identity.clone(),
            result,
        };
        (identity, record)
    }

    #[test]
    fn recovery_parser_rejects_noncanonical_selectors_and_digest_aliases() {
        let original = parser_identity();
        let (id, generation, raw) = decode_apply_recovery_identity(&original).unwrap();
        assert_eq!(raw, "original:key");
        for resource in [
            String::new(),
            format!("project:{}", Uuid::nil()),
            format!("project:{}", id.simple()),
            format!("project:{}", id.to_string().to_uppercase()),
            format!("project:{{{id}}}"),
            format!("project:{id} "),
            format!("scene:{id}"),
        ] {
            let mut invalid = original.clone();
            invalid.resource = resource;
            assert!(decode_apply_recovery_identity(&invalid).is_err());
        }
        for key in [
            "mw1:".into(),
            format!("mw1:{generation}"),
            format!("mw1:{generation}:"),
            format!("mw1:{generation}:bad\0key"),
            format!("mw1:{generation}:bad\u{7f}key"),
            format!("mw1:{generation}:bad\u{85}key"),
            format!("mw1:{generation}:{}", "é".repeat(44)),
            "mw1:not-a-uuid:original:key".into(),
        ] {
            let mut invalid = original.clone();
            invalid.key = key;
            assert!(decode_apply_recovery_identity(&invalid).is_err());
        }
        for digest in [
            String::new(),
            "a".repeat(63),
            "a".repeat(65),
            "A".repeat(64),
            format!("{}g", "a".repeat(63)),
        ] {
            let mut invalid = original.clone();
            invalid.request_sha256 = digest;
            assert!(decode_apply_recovery_identity(&invalid).is_err());
        }
        let raw = format!(" {}:x ", "é".repeat(40));
        let exact = apply_recovery_identity(id, generation, &raw, &intent()).unwrap();
        assert_eq!(decode_apply_recovery_identity(&exact).unwrap().2, raw);
        assert!(exact.key.ends_with(&raw));
        assert_eq!(original.request_sha256.len(), 64);
    }

    #[test]
    fn recovery_opt_in_and_prepared_metadata_reject_invalid_input_before_local_effects() {
        let f = fixture();
        let oracle = Connection::open(&f.path).unwrap();
        oracle.execute_batch("PRAGMA query_only=ON").unwrap();
        let before = snapshot(&oracle);
        let legacy_key = "k".repeat(256);
        assert!(!apply_recovery_requested(&json!({}), &legacy_key).unwrap());
        assert!(
            !apply_recovery_requested(&json!({"include_recovery":false}), &legacy_key).unwrap()
        );
        for value in [
            JsonValue::Null,
            json!(0),
            json!(1),
            json!("true"),
            json!([]),
            json!({}),
        ] {
            assert!(apply_recovery_requested(&json!({"include_recovery":value}), "key").is_err());
        }
        for raw in [
            "".to_owned(),
            "k".repeat(88),
            "é".repeat(44),
            "bad\nkey".into(),
        ] {
            assert!(apply_recovery_requested(&json!({"include_recovery":true}), &raw).is_err());
            assert!(prepare_apply_recovery(f.base.id, f.base.generation, &raw, &intent()).is_err());
        }
        for (id, generation) in [(Uuid::nil(), f.base.generation), (f.base.id, Uuid::nil())] {
            assert!(prepare_apply_recovery(id, generation, "key", &intent()).is_err());
        }
        assert_eq!(snapshot(&oracle), before);
        // The real producer prepares this immutable value before the known local
        // effect. This component case does not claim entry into NativeDriver.
        let key = format!("{}x", "é".repeat(43));
        let identity =
            apply_recovery_identity(f.base.id, f.base.generation, &key, &intent()).unwrap();
        let prepared =
            prepare_apply_recovery(f.base.id, f.base.generation, &key, &intent()).unwrap();
        assert_eq!(snapshot(&oracle), before);
        let outcome = f
            .service
            .apply(f.base.id, &f.base.stamp(), &key, &intent())
            .unwrap();
        let mut emitted = apply_completion_with_recovery(Ok(outcome.clone()), Some(prepared))
            .into_result()
            .unwrap();
        assert_eq!(
            emitted.as_object_mut().unwrap().remove("recovery"),
            Some(serde_json::to_value(&identity).unwrap()),
        );
        assert_eq!(emitted, apply_result_value(&outcome));
        assert_eq!(emitted.as_object().unwrap().len(), 7);
        assert_eq!(f.service.project(f.base.id).unwrap(), outcome.project);
        assert!(!outcome.replayed);
    }

    #[test]
    fn recovery_missing_and_legacy_receipts_have_distinct_product_outcomes() {
        let f = fixture();
        let committed = known_commit(&f);
        let oracle = Connection::open(&f.path).unwrap();
        let missing =
            apply_recovery_identity(f.base.id, f.base.generation, "absent-key", &intent()).unwrap();
        let before_missing = snapshot(&oracle);
        let observation = {
            let shared = f.service.store();
            let mut store = shared.lock();
            store
                .lookup_apply_result_v1(
                    f.base.id,
                    f.base.generation,
                    "absent-key",
                    &missing.request_sha256,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap()
        };
        assert!(matches!(&observation, ApplyResultObservation::NotFound));
        let record = apply_recovery_record(&missing, observation).unwrap();
        assert!(
            matches!(&record, RecoveryRecord::OutcomeUnknown { identity } if identity == &missing)
        );
        let value = serde_json::to_value(&record).unwrap();
        assert_eq!(value["state"], "outcome_unknown");
        assert!(value.get("result").is_none());
        assert_eq!(snapshot(&oracle), before_missing);

        assert_eq!(oracle.execute(
            "UPDATE receipts SET apply_binding_version=0,apply_binding_json=NULL WHERE project_id=?1 AND request_id='known-original'",
            [f.base.id.to_string()],
        ).unwrap(), 1);
        oracle.execute_batch("PRAGMA query_only=ON").unwrap();
        let before_legacy = snapshot(&oracle);
        let identity =
            apply_recovery_identity(f.base.id, f.base.generation, "known-original", &intent())
                .unwrap();
        let observation = {
            let shared = f.service.store();
            let mut store = shared.lock();
            store
                .lookup_apply_result_v1(
                    f.base.id,
                    f.base.generation,
                    "known-original",
                    &identity.request_sha256,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap()
        };
        assert!(matches!(
            &observation,
            ApplyResultObservation::LegacyUnverified
        ));
        let error = apply_recovery_record(&identity, observation).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert_eq!(
            error.message,
            "Historical receipt lacks a verified V1 binding"
        );
        assert!(error.outcome_known, "the refusal of this read is known");
        assert!(
            !serde_json::to_string(&error)
                .unwrap()
                .contains("Known committed title")
        );
        assert_eq!(snapshot(&oracle), before_legacy);
        assert_eq!(f.service.project(f.base.id).unwrap(), committed.project);
        // The legacy fixture is known to have committed. Neither read outcome
        // claims that historical apply was NotApplied or permits replay.
    }

    #[test]
    fn recovery_actual_integrity_request_contract_and_authority_refusals_stay_distinct() {
        for scenario in ["digest", "integrity", "contract", "generation"] {
            let f = fixture();
            let committed = known_commit(&f);
            let oracle = Connection::open(&f.path).unwrap();
            let mut identity =
                apply_recovery_identity(f.base.id, f.base.generation, "known-original", &intent())
                    .unwrap();
            let mut generation = f.base.generation;
            let (expected, code, message) = match scenario {
                "digest" => {
                    let different = apply_recovery_identity(
                        f.base.id,
                        f.base.generation,
                        "known-original",
                        &Change::RenameProject {
                            title: "A different typed intent".into(),
                        },
                    )
                    .unwrap();
                    assert_ne!(different.request_sha256, identity.request_sha256);
                    identity = different;
                    (
                        ApplyResultObservation::Invalid {
                            reason: ApplyReceiptInvalid::RequestMismatch,
                        },
                        ErrorCode::Conflict,
                        "Historical request digest differs",
                    )
                }
                "integrity" => {
                    oracle.execute(
                        "UPDATE receipts SET result_json=?1 WHERE project_id=?2 AND request_id='known-original'",
                        params!["{\"private_marker\":\"never-return-this\"}", f.base.id.to_string()],
                    ).unwrap();
                    (
                        ApplyResultObservation::Invalid {
                            reason: ApplyReceiptInvalid::ReceiptIntegrity,
                        },
                        ErrorCode::BackendFailed,
                        "Historical receipt validation failed",
                    )
                }
                "contract" => {
                    oracle.execute(
                        "UPDATE receipts SET apply_binding_version=2 WHERE project_id=?1 AND request_id='known-original'",
                        [f.base.id.to_string()],
                    ).unwrap();
                    (
                        ApplyResultObservation::Invalid {
                            reason: ApplyReceiptInvalid::UnsupportedContract,
                        },
                        ErrorCode::Unsupported,
                        "Historical receipt contract is unsupported",
                    )
                }
                "generation" => {
                    generation = Uuid::now_v7();
                    assert_ne!(generation, f.base.generation);
                    identity =
                        apply_recovery_identity(f.base.id, generation, "known-original", &intent())
                            .unwrap();
                    (
                        ApplyResultObservation::AuthorityMismatch,
                        ErrorCode::StaleReference,
                        "Historical project generation is no longer current",
                    )
                }
                _ => unreachable!(),
            };
            oracle.execute_batch("PRAGMA query_only=ON").unwrap();
            let before = snapshot(&oracle);
            let observation = {
                let shared = f.service.store();
                let mut store = shared.lock();
                store
                    .lookup_apply_result_v1(
                        f.base.id,
                        generation,
                        "known-original",
                        &identity.request_sha256,
                        APPLY_REQUEST_ENCODING_V1,
                    )
                    .unwrap()
            };
            assert_eq!(observation, expected, "{scenario}");
            let error = apply_recovery_record(&identity, observation).unwrap_err();
            assert_eq!(error.code, code, "{scenario}");
            assert_eq!(error.message, message, "{scenario}");
            assert!(error.outcome_known);
            let serialized = serde_json::to_string(&error).unwrap();
            assert!(!serialized.contains("never-return-this"));
            assert!(!serialized.contains("Known committed title"));
            assert_eq!(snapshot(&oracle), before, "{scenario}");
            assert_eq!(f.service.project(f.base.id).unwrap(), committed.project);
        }
    }

    #[test]
    fn recovery_operational_sql_failure_is_not_absent_history() {
        let f = fixture();
        let committed = known_commit(&f);
        let identity =
            apply_recovery_identity(f.base.id, f.base.generation, "known-original", &intent())
                .unwrap();
        let oracle = Connection::open(&f.path).unwrap();
        oracle
            .execute_batch("DROP TABLE receipts; PRAGMA query_only=ON")
            .unwrap();
        let events_before: i64 = oracle
            .query_row("SELECT COUNT(*) FROM events", [], |row| row.get(0))
            .unwrap();
        let failure = {
            let shared = f.service.store();
            let mut store = shared.lock();
            store
                .lookup_apply_result_v1(
                    f.base.id,
                    f.base.generation,
                    "known-original",
                    &identity.request_sha256,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap_err()
        };
        assert!(matches!(&failure, StorageError::Sql(_)));
        let error = storage_error(failure);
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert_eq!(error.message, "Motionwright storage failed");
        assert!(error.outcome_known);
        assert_eq!(f.service.project(f.base.id).unwrap(), committed.project);
        let events_after: i64 = oracle
            .query_row("SELECT COUNT(*) FROM events", [], |row| row.get(0))
            .unwrap();
        assert_eq!(events_after, events_before);
        let receipt_tables: i64 = oracle
            .query_row(
                "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name='receipts'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(
            receipt_tables, 0,
            "lookup must not reopen, migrate or repair the table"
        );
    }

    #[test]
    fn sdk_recovery_full_record_and_driver_envelope_keep_the_byte_limit() {
        // Generic public SDK DTO boundary; this value is not presented as a
        // Motionwright project or evidence of a native commit.
        let (identity, mut record) = public_record(json!(""));
        let overhead = serde_json::to_vec(&record).unwrap().len();
        let RecoveryRecord::Recorded { result, .. } = &mut record else {
            unreachable!()
        };
        *result = json!("x".repeat(MAX_VALUE_BYTES - overhead));
        assert_eq!(serde_json::to_vec(&record).unwrap().len(), MAX_VALUE_BYTES);
        record.validate_for(&identity).unwrap();
        let envelope = json!({
            "record":record, "historical_only":true,
            "current_authority":false, "replay_allowed":false
        });
        assert!(serde_json::to_vec(&envelope).unwrap().len() > MAX_VALUE_BYTES);
        let error = validate_value(&envelope).unwrap_err();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert_eq!(error.message, "Cooperation value byte budget");

        let RecoveryRecord::Recorded { result, .. } = &mut record else {
            unreachable!()
        };
        let JsonValue::String(payload) = result else {
            unreachable!()
        };
        payload.push('x');
        validate_value(result).unwrap();
        let error = record.validate_for(&identity).unwrap_err();
        assert_eq!(error.code, ErrorCode::ResourceExhausted);
        assert_eq!(error.message, "Cooperation value byte budget");
    }

    #[test]
    fn sdk_recovery_record_depth_and_node_limits_include_identity_and_result() {
        let mut nested = JsonValue::Null;
        for _ in 0..31 {
            nested = json!([nested]);
        }
        let (identity, mut record) = public_record(nested);
        record.validate_for(&identity).unwrap();
        let RecoveryRecord::Recorded { result, .. } = &mut record else {
            unreachable!()
        };
        *result = json!([result.take()]);
        let depth_error = record.validate_for(&identity).unwrap_err();
        assert_eq!(depth_error.code, ErrorCode::ResourceExhausted);
        assert_eq!(depth_error.message, "Cooperation value structural budget");

        // Root + state + identity object/four values + result array = eight
        // nodes before its elements, so this complete record has 16,384 nodes.
        let (identity, mut record) = public_record(json!(vec![JsonValue::Null; 16_376]));
        assert!(serde_json::to_vec(&record).unwrap().len() < MAX_VALUE_BYTES);
        record.validate_for(&identity).unwrap();
        let RecoveryRecord::Recorded { result, .. } = &mut record else {
            unreachable!()
        };
        result.as_array_mut().unwrap().push(JsonValue::Null);
        let node_error = record.validate_for(&identity).unwrap_err();
        assert_eq!(node_error.code, ErrorCode::ResourceExhausted);
        assert_eq!(node_error.message, "Cooperation value structural budget");
    }

    #[test]
    fn sdk_recovery_numbers_use_the_existing_shared_exact_range() {
        for value in [
            json!(9_007_199_254_740_991_u64),
            json!(-9_007_199_254_740_991_i64),
            json!(0.25_f64),
        ] {
            let (identity, record) = public_record(value);
            record.validate_for(&identity).unwrap();
        }
        for value in [
            json!(9_007_199_254_740_992_u64),
            json!(-9_007_199_254_740_992_i64),
            json!(18_014_398_509_481_984_f64),
        ] {
            let (identity, record) = public_record(value);
            assert_eq!(
                record.validate_for(&identity).unwrap_err().code,
                ErrorCode::InvalidArgument
            );
        }
        // Nonfinite numbers are not JSON values; do not turn them into null and
        // then falsely claim the SDK rejected an original nonfinite number.
        assert!(serde_json::Number::from_f64(f64::INFINITY).is_none());
        assert!(serde_json::Number::from_f64(f64::NAN).is_none());
        assert!(serde_json::from_str::<JsonValue>("1e400").is_err());
    }
    #[test]
    fn large_confirmed_receipt_stays_complete_when_sdk_values_are_rejected() {
        let f = fixture();
        let mut current = f.base.clone();
        let mut large = None;
        // Each input is a real, valid bounded review. Stop at the first genuinely
        // committed historical result whose complete seven-field value is too large.
        // These are distinct known local commits, never retries of an uncertain call.
        for index in 0..34 {
            let key = format!("large-known-{index:02}");
            let change = Change::AddReview {
                kind: motionwright_domain::ReviewKind::Technical,
                resource: current.resource_key(),
                body: "x".repeat(8_000),
                start: None,
                end: None,
                locale: None,
                profile_id: None,
            };
            let identity =
                apply_recovery_identity(current.id, current.generation, &key, &change).unwrap();
            let prepared =
                prepare_apply_recovery(current.id, current.generation, &key, &change).unwrap();
            let outcome = f
                .service
                .apply(current.id, &current.stamp(), &key, &change)
                .unwrap();
            assert!(!outcome.replayed);
            let legacy = apply_result_value(&outcome);
            let emitted = apply_completion_with_recovery(Ok(outcome.clone()), Some(prepared))
                .into_result()
                .unwrap();
            let mut without_metadata = emitted.clone();
            assert_eq!(
                without_metadata.as_object_mut().unwrap().remove("recovery"),
                Some(serde_json::to_value(&identity).unwrap()),
            );
            assert_eq!(without_metadata, legacy);
            assert_eq!(legacy.as_object().unwrap().len(), 7);
            assert_eq!(applied(apply_completion(Ok(outcome.clone()))), outcome,);
            current = outcome.project.clone();
            if serde_json::to_vec(&legacy).unwrap().len() > MAX_VALUE_BYTES {
                large = Some((outcome, identity, legacy, emitted));
                break;
            }
        }
        let (outcome, identity, legacy, emitted) =
            large.expect("valid real reviews must produce a result above 256 KiB");
        outcome.project.validate().unwrap();
        assert_eq!(f.service.project(f.base.id).unwrap(), outcome.project);

        let oracle =
            Connection::open_with_flags(&f.path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
                .unwrap();
        oracle.execute_batch("PRAGMA query_only=ON").unwrap();
        let before = snapshot(&oracle);
        let observation = {
            let shared = f.service.store();
            let mut store = shared.lock();
            store
                .lookup_apply_result_v1(
                    outcome.project.id,
                    outcome.project.generation,
                    &outcome.request_id,
                    &identity.request_sha256,
                    APPLY_REQUEST_ENCODING_V1,
                )
                .unwrap()
        };
        let ApplyResultObservation::KnownVerifiedV1 {
            binding,
            outcome: historical,
        } = observation
        else {
            panic!("the real committed receipt must remain verified and complete");
        };
        assert_eq!(*historical, outcome);
        assert_eq!(binding.request_sha256, identity.request_sha256);
        assert_eq!(apply_result_value(&historical), legacy);
        assert!(!historical.replayed);

        let record = RecoveryRecord::Recorded {
            identity: identity.clone(),
            result: legacy.clone(),
        };
        let record_before = serde_json::to_value(&record).unwrap();
        for result in [
            validate_value(&legacy),
            validate_value(&emitted),
            record.validate_for(&identity),
        ] {
            let error = result.unwrap_err();
            assert_eq!(error.code, ErrorCode::ResourceExhausted);
            assert_eq!(error.message, "Cooperation value byte budget");
        }
        assert_eq!(serde_json::to_value(&record).unwrap(), record_before);
        assert_eq!(apply_result_value(&historical), legacy);
        assert_eq!(f.service.project(f.base.id).unwrap(), outcome.project);
        assert_eq!(
            snapshot(&oracle),
            before,
            "bounded serialization and read-only recovery must not apply or rewrite",
        );
        // Scope: actual StudioService/Store, actual completion helper and public SDK
        // value/record checks. This does not execute NativeDriver or claim Host
        // authority, postcommit outcome_known behavior, or native custody success.
    }
}
