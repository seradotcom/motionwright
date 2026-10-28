//! Pure DTO tests using the existing real Domain Graph fixture and reducer.
//! These fixtures assert no Native readiness, Host authority or physical PASS.

use super::*;
use semwright_native_sdk::{Error as NativeError, ErrorCode};
use serde_json::json;

#[path = "../../../../crates/domain/tests/support/semantic_graph_source.rs"]
mod source;

const REQUEST_ID: &str = "5C77EA19-C0B1-47F4-B03B-495D72406A1F";

struct Fixture {
    prepared: PreparedGraphRecovery,
    historical: Project,
    result: Value,
}

fn prepared(change: Change) -> PreparedGraphRecovery {
    let request: GraphRecoveryRequest = serde_json::from_value(json!({
        "request_id": REQUEST_ID,
        "change": change,
    }))
    .unwrap();
    request.prepare().unwrap()
}

fn applied_fixture(before: &Project, change: Change) -> Fixture {
    let prepared = prepared(change);
    let mut historical = before.clone();
    historical.apply_change(&prepared.change).unwrap();
    // The reducer is transaction-neutral. Use the same public Domain revision
    // finalizer as Store, without pretending this pure fixture was persisted.
    historical
        .commit_revision(before.revision + 1, &prepared.change)
        .unwrap();
    historical.validate().unwrap();
    let result = json!({
        "resource": historical.resource_key(),
        "generation": historical.generation.to_string(),
        "revision": historical.revision.to_string(),
        "previous_revision": before.revision.to_string(),
        "request_id": REQUEST_ID,
        "replayed": false,
        "project": historical,
    });
    assert_eq!(result.as_object().unwrap().len(), 7);
    Fixture {
        prepared,
        historical,
        result,
    }
}

fn upsert_fixture() -> Fixture {
    let before = source::seed_domain();
    let graph = source::graph_for(&before, false);
    applied_fixture(
        &before,
        Change::UpsertSemanticGraph {
            graph: Box::new(graph),
        },
    )
}

fn remove_fixture() -> Fixture {
    let upsert = upsert_fixture();
    let graph = upsert.historical.production_design.semantic_graphs[0].clone();
    let change = Change::RemoveSemanticGraph {
        graph_id: graph.id,
        scene_id: graph.scene_id,
        source_snapshot: GraphSourceSnapshot::from_project(&upsert.historical),
    };
    applied_fixture(&upsert.historical, change)
}

fn decode_recorded(
    prepared: &PreparedGraphRecovery,
    result: Value,
) -> Result<GraphRecoveryResponse, GraphRecoveryReadError> {
    let record = RecoveryRecord::Recorded {
        identity: prepared.identity().clone(),
        result,
    };
    prepared.decode_read::<()>(Ok(&record))
}

#[test]
fn graph_recovery_prepares_only_closed_original_graph_requests() {
    let fixture = upsert_fixture();
    let original = json!({
        "request_id": REQUEST_ID,
        "change": fixture.prepared.change,
    });
    assert!(
        serde_json::from_value::<GraphRecoveryRequest>(json!([
            REQUEST_ID,
            fixture.prepared.change,
        ]))
        .is_err()
    );
    let duplicate = format!(
        "{{\"request_id\":\"{REQUEST_ID}\",\"request_id\":\"{REQUEST_ID}\",\"change\":{}}}",
        serde_json::to_string(&fixture.prepared.change).unwrap()
    );
    assert!(serde_json::from_str::<GraphRecoveryRequest>(&duplicate).is_err());
    for extra in [
        "identity",
        "resource",
        "epoch",
        "digest",
        "recipe",
        "connection",
        "expected_store",
        "grant",
        "permit",
        "ready",
    ] {
        let mut changed = original.clone();
        changed[extra] = json!(true);
        assert!(serde_json::from_value::<GraphRecoveryRequest>(changed).is_err());
    }
    for missing in ["request_id", "change"] {
        let mut changed = original.clone();
        changed.as_object_mut().unwrap().remove(missing);
        assert!(serde_json::from_value::<GraphRecoveryRequest>(changed).is_err());
    }
    let ordinary: GraphRecoveryRequest = serde_json::from_value(json!({
        "request_id": REQUEST_ID,
        "change": Change::RenameProject { title: "ordinary edit".into() },
    }))
    .unwrap();
    assert_eq!(
        ordinary.prepare().unwrap_err(),
        GraphRecoveryReadError::InvalidRequest
    );
}

#[test]
fn graph_recovery_keeps_uuid_spelling_and_canonical_native_identity() {
    let fixture = upsert_fixture();
    let Change::UpsertSemanticGraph { graph } = &fixture.prepared.change else {
        panic!("upsert fixture");
    };
    assert_eq!(fixture.prepared.request_id(), REQUEST_ID);
    assert_eq!(
        fixture.prepared.identity(),
        &NativeGraphRecipeClient::prepare_upsert(REQUEST_ID, graph).unwrap()
    );
    assert!(fixture.prepared.identity().key.ends_with(REQUEST_ID));
    for invalid in [
        "",
        "5c77ea19c0b147f4b03b495d72406a1f",
        "00000000-0000-0000-0000-000000000000",
        " 5C77EA19-C0B1-47F4-B03B-495D72406A1F",
        "5C77EA19-C0B1-47F4-B03B-495D72406A1F ",
        "not-an-original-uuid",
    ] {
        let request = GraphRecoveryRequest {
            request_id: invalid.into(),
            change: fixture.prepared.change.clone(),
        };
        assert_eq!(
            request.prepare().unwrap_err(),
            GraphRecoveryReadError::InvalidRequest,
            "{invalid:?}"
        );
    }
}

#[test]
fn graph_recovery_rejects_nonnative_source_bounds_before_preparation() {
    let fixture = remove_fixture();
    for invalid_field in ["project_id", "generation", "branch_id", "revision"] {
        let mut change = fixture.prepared.change.clone();
        let Change::RemoveSemanticGraph {
            source_snapshot, ..
        } = &mut change
        else {
            panic!("remove fixture");
        };
        match invalid_field {
            "project_id" => source_snapshot.project_id = Uuid::nil(),
            "generation" => source_snapshot.generation = Uuid::nil(),
            "branch_id" => source_snapshot.branch_id = Uuid::nil(),
            "revision" => source_snapshot.revision = MAX_SAFE_INTEGER + 1,
            _ => unreachable!(),
        }
        assert_eq!(
            GraphRecoveryRequest {
                request_id: REQUEST_ID.into(),
                change,
            }
            .prepare()
            .unwrap_err(),
            GraphRecoveryReadError::InvalidRequest,
            "{invalid_field}"
        );
    }
}

#[test]
fn graph_recovery_returns_only_closed_original_historical_project() {
    let fixture = upsert_fixture();
    let original_source = fixture.prepared.source_snapshot().clone();
    let response = decode_recorded(&fixture.prepared, fixture.result.clone()).unwrap();
    let value = serde_json::to_value(response).unwrap();
    assert_eq!(
        value,
        json!({
            "schema": RESPONSE_SCHEMA,
            "request_id": REQUEST_ID,
            "source_snapshot": original_source,
            "record": { "state": "recorded", "historical_project": fixture.historical },
        })
    );
    assert_eq!(value.as_object().unwrap().len(), 4);
    assert_eq!(value["record"].as_object().unwrap().len(), 2);
    assert_eq!(
        value["record"]["historical_project"]["revision"],
        json!(fixture.prepared.source_snapshot().revision + 1)
    );
    // The original boolean is validated, but neither value grants replay.
    let mut replayed = fixture.result;
    replayed["replayed"] = json!(true);
    assert!(decode_recorded(&fixture.prepared, replayed).is_ok());
}

#[test]
fn graph_recovery_refuses_newer_live_project_as_original_receipt() {
    let fixture = upsert_fixture();
    let mut later = fixture.historical.clone();
    let later_change = Change::RenameProject {
        title: "later independent edit".into(),
    };
    later.apply_change(&later_change).unwrap();
    later
        .commit_revision(fixture.historical.revision + 1, &later_change)
        .unwrap();
    later.validate().unwrap();
    let later_before = later.clone();
    let original = decode_recorded(&fixture.prepared, fixture.result.clone()).unwrap();
    assert_eq!(
        serde_json::to_value(original).unwrap()["record"]["historical_project"],
        serde_json::to_value(&fixture.historical).unwrap()
    );
    assert_eq!(
        later, later_before,
        "historical decoding does not replace live state"
    );
    let mut forged_current = fixture.result;
    forged_current["revision"] = json!(later.revision.to_string());
    forged_current["project"] = serde_json::to_value(later).unwrap();
    assert_eq!(
        decode_recorded(&fixture.prepared, forged_current).unwrap_err(),
        GraphRecoveryReadError::InvalidRecord
    );
}

#[test]
fn graph_recovery_rejects_any_request_identity_difference() {
    let fixture = upsert_fixture();
    for field in ["resource", "epoch", "key", "request_sha256"] {
        let mut identity = fixture.prepared.identity().clone();
        match field {
            "resource" => identity.resource = format!("project:{}", Uuid::new_v4()),
            "epoch" => identity.epoch += 1,
            "key" => identity.key.push('x'),
            "request_sha256" => {
                let replacement = if identity.request_sha256.starts_with('0') {
                    "1"
                } else {
                    "0"
                };
                identity.request_sha256.replace_range(..1, replacement);
            }
            _ => unreachable!(),
        }
        let record = RecoveryRecord::Recorded {
            identity,
            result: fixture.result.clone(),
        };
        assert_eq!(
            fixture.prepared.decode_read::<()>(Ok(&record)).unwrap_err(),
            GraphRecoveryReadError::InvalidRecord,
            "{field}"
        );
    }
}

#[test]
fn graph_recovery_binds_the_entire_original_change_not_only_key() {
    let fixture = upsert_fixture();
    let mut changed = fixture.prepared.change.clone();
    let Change::UpsertSemanticGraph { graph } = &mut changed else {
        panic!("upsert fixture");
    };
    graph
        .authorship
        .rationale
        .push_str(" Changed original intention.");
    let other = prepared(changed);
    assert_eq!(other.identity().key, fixture.prepared.identity().key);
    assert_ne!(
        other.identity().request_sha256,
        fixture.prepared.identity().request_sha256
    );
    let original_record = RecoveryRecord::Recorded {
        identity: fixture.prepared.identity().clone(),
        result: fixture.result,
    };
    assert_eq!(
        other.decode_read::<()>(Ok(&original_record)).unwrap_err(),
        GraphRecoveryReadError::InvalidRecord
    );
}

#[test]
fn graph_recovery_requires_exact_seven_field_historical_projection() {
    let fixture = upsert_fixture();
    let positional = json!([
        fixture.result["resource"],
        fixture.result["generation"],
        fixture.result["revision"],
        fixture.result["previous_revision"],
        fixture.result["request_id"],
        fixture.result["replayed"],
        fixture.result["project"],
    ]);
    assert!(decode_recorded(&fixture.prepared, positional).is_err());
    for missing in [
        "resource",
        "generation",
        "revision",
        "previous_revision",
        "request_id",
        "replayed",
        "project",
    ] {
        let mut changed = fixture.result.clone();
        changed.as_object_mut().unwrap().remove(missing);
        assert!(
            decode_recorded(&fixture.prepared, changed).is_err(),
            "{missing}"
        );
    }
    for extra in ["recovery", "current_authority", "replay_allowed", "permit"] {
        let mut changed = fixture.result.clone();
        changed[extra] = json!(true);
        assert!(
            decode_recorded(&fixture.prepared, changed).is_err(),
            "{extra}"
        );
    }
    for (field, invalid) in [
        ("replayed", json!("false")),
        ("revision", json!(fixture.historical.revision)),
        ("request_id", Value::Null),
        ("project", json!([])),
    ] {
        let mut changed = fixture.result.clone();
        changed[field] = invalid;
        assert!(
            decode_recorded(&fixture.prepared, changed).is_err(),
            "{field}"
        );
    }
}

#[test]
fn graph_recovery_refuses_silent_project_normalization() {
    let fixture = upsert_fixture();
    let mut extra = fixture.result.clone();
    extra["project"]["future_component_source"] = json!({ "author_intent": "retain me" });
    // Ordinary Domain loading would silently ignore this neighbor and the
    // resulting Project remains valid, but it is not the complete projection.
    let normalized: Project = serde_json::from_value(extra["project"].clone()).unwrap();
    normalized.validate().unwrap();
    assert_eq!(normalized, fixture.historical);
    assert!(decode_recorded(&fixture.prepared, extra).is_err());

    let mut omitted = fixture.result;
    assert_eq!(omitted["project"]["reviews"], json!([]));
    omitted["project"]
        .as_object_mut()
        .unwrap()
        .remove("reviews");
    let normalized: Project = serde_json::from_value(omitted["project"].clone()).unwrap();
    normalized.validate().unwrap();
    assert_eq!(normalized, fixture.historical);
    assert!(decode_recorded(&fixture.prepared, omitted).is_err());
}

#[test]
fn graph_recovery_rejects_scope_revision_key_or_invalid_domain_project() {
    let fixture = upsert_fixture();
    for (pointer, invalid) in [
        ("/resource", json!(format!("project:{}", Uuid::new_v4()))),
        ("/generation", json!(Uuid::new_v4().to_string())),
        ("/request_id", json!(REQUEST_ID.to_lowercase())),
        (
            "/previous_revision",
            json!((fixture.prepared.source_snapshot().revision + 1).to_string()),
        ),
        (
            "/revision",
            json!((fixture.historical.revision + 1).to_string()),
        ),
        ("/project/id", json!(Uuid::new_v4())),
        ("/project/generation", json!(Uuid::new_v4())),
        ("/project/active_branch", json!(Uuid::new_v4())),
        ("/project/revision", json!(fixture.historical.revision + 1)),
        ("/project/title", json!("")),
    ] {
        let mut changed = fixture.result.clone();
        *changed.pointer_mut(pointer).unwrap() = invalid;
        assert_eq!(
            decode_recorded(&fixture.prepared, changed).unwrap_err(),
            GraphRecoveryReadError::InvalidRecord,
            "{pointer}"
        );
    }
}

#[test]
fn graph_recovery_revisions_are_canonical_safe_decimals_and_exact_successor() {
    assert_eq!(canonical_revision("0").unwrap(), 0);
    assert_eq!(
        canonical_revision(&MAX_SAFE_INTEGER.to_string()).unwrap(),
        MAX_SAFE_INTEGER
    );
    for invalid in [
        "",
        "+1",
        "01",
        "1 ",
        " 1",
        "1.0",
        "1e0",
        "-1",
        "9007199254740992",
        "18446744073709551616",
    ] {
        assert_eq!(
            canonical_revision(invalid).unwrap_err(),
            GraphRecoveryReadError::InvalidRecord
        );
    }
    let fixture = remove_fixture();
    for field in ["revision", "previous_revision"] {
        let mut changed = fixture.result.clone();
        let original = changed[field].as_str().unwrap();
        changed[field] = json!(format!("0{original}"));
        assert!(decode_recorded(&fixture.prepared, changed).is_err());
    }
    let mut no_advance = fixture.result.clone();
    no_advance["revision"] = json!(fixture.prepared.source_snapshot().revision.to_string());
    no_advance["project"]["revision"] = json!(fixture.prepared.source_snapshot().revision);
    assert!(decode_recorded(&fixture.prepared, no_advance).is_err());

    // The request remains a historical lookup at the upper source bound; it
    // cannot manufacture a Recorded Project at an unsafe successor revision.
    let mut change = fixture.prepared.change.clone();
    let Change::RemoveSemanticGraph {
        source_snapshot, ..
    } = &mut change
    else {
        panic!("remove fixture");
    };
    source_snapshot.revision = MAX_SAFE_INTEGER;
    let max_source = prepared(change);
    let mut impossible = fixture.result;
    impossible["previous_revision"] = json!(MAX_SAFE_INTEGER.to_string());
    impossible["revision"] = json!((MAX_SAFE_INTEGER + 1).to_string());
    impossible["project"]["revision"] = json!(MAX_SAFE_INTEGER + 1);
    assert!(decode_recorded(&max_source, impossible).is_err());
}

#[test]
fn graph_recovery_upsert_requires_exact_submitted_graph() {
    let fixture = upsert_fixture();
    let mut altered_project = fixture.historical.clone();
    altered_project.production_design.semantic_graphs[0]
        .authorship
        .rationale
        .push_str(" Different receipt content.");
    altered_project.validate().unwrap();
    let mut altered = fixture.result;
    altered["project"] = serde_json::to_value(altered_project).unwrap();
    assert_eq!(
        decode_recorded(&fixture.prepared, altered).unwrap_err(),
        GraphRecoveryReadError::InvalidRecord
    );
}

#[test]
fn graph_recovery_remove_uses_original_change_after_graph_is_already_absent() {
    let fixture = remove_fixture();
    let Change::RemoveSemanticGraph {
        graph_id,
        scene_id,
        source_snapshot,
    } = &fixture.prepared.change
    else {
        panic!("remove fixture");
    };
    assert!(
        fixture
            .historical
            .production_design
            .semantic_graphs
            .iter()
            .all(|graph| graph.id != *graph_id)
    );
    assert_eq!(
        fixture.prepared.identity(),
        &NativeGraphRecipeClient::prepare_remove(
            REQUEST_ID,
            *graph_id,
            *scene_id,
            source_snapshot,
        )
        .unwrap()
    );
    assert!(decode_recorded(&fixture.prepared, fixture.result.clone()).is_ok());

    let mut invalid_project = fixture.historical;
    let mut returned_graph = source::graph_for(&invalid_project, false);
    returned_graph.id = *graph_id;
    invalid_project
        .production_design
        .semantic_graphs
        .push(returned_graph);
    invalid_project.validate().unwrap();
    let mut altered = fixture.result;
    altered["project"] = serde_json::to_value(invalid_project).unwrap();
    assert_eq!(
        decode_recorded(&fixture.prepared, altered).unwrap_err(),
        GraphRecoveryReadError::InvalidRecord
    );
}

#[test]
fn graph_recovery_preserves_all_nonrecorded_sdk_states_without_authority() {
    let fixture = upsert_fixture();
    for (record, expected) in [
        (
            RecoveryRecord::Pending {
                identity: fixture.prepared.identity().clone(),
            },
            json!({ "state": "pending" }),
        ),
        (
            RecoveryRecord::OutcomeUnknown {
                identity: fixture.prepared.identity().clone(),
            },
            json!({ "state": "outcome_unknown" }),
        ),
        (
            RecoveryRecord::RetentionExpired {
                identity: fixture.prepared.identity().clone(),
                current_epoch: fixture.prepared.identity().epoch + 1,
            },
            json!({
                "state": "retention_expired",
                "current_epoch": fixture.prepared.identity().epoch + 1,
            }),
        ),
    ] {
        let response = fixture.prepared.decode_read::<()>(Ok(&record)).unwrap();
        let value = serde_json::to_value(response).unwrap();
        assert_eq!(value["record"], expected);
        assert_eq!(value["request_id"], json!(REQUEST_ID));
        assert_eq!(
            value["source_snapshot"],
            serde_json::to_value(fixture.prepared.source_snapshot()).unwrap()
        );
        assert_eq!(value.as_object().unwrap().len(), 4);
    }
    for current_epoch in [fixture.prepared.identity().epoch, MAX_SAFE_INTEGER + 1] {
        let record = RecoveryRecord::RetentionExpired {
            identity: fixture.prepared.identity().clone(),
            current_epoch,
        };
        assert_eq!(
            fixture.prepared.decode_read::<()>(Ok(&record)).unwrap_err(),
            GraphRecoveryReadError::InvalidRecord
        );
    }
}

#[test]
fn graph_recovery_known_or_unknown_read_failures_never_reclassify_original_mutation() {
    let fixture = upsert_fixture();
    let identity_before = fixture.prepared.identity().clone();
    let change_before = fixture.prepared.change.clone();
    for code in [
        ErrorCode::PolicyDenied,
        ErrorCode::StaleReference,
        ErrorCode::NotFound,
        ErrorCode::BackendFailed,
    ] {
        for known in [false, true] {
            let mut error = NativeError::new(code, "PRIVATE_READ_DIAGNOSTIC");
            error.outcome_known = known;
            let failure = fixture
                .prepared
                .decode_read(Err::<&RecoveryRecord, _>(error))
                .unwrap_err();
            assert_eq!(failure, GraphRecoveryReadError::ReadFailed);
            assert!(!failure.to_string().contains("PRIVATE_READ_DIAGNOSTIC"));
            assert!(!failure.to_string().contains("mutation"));
            assert_eq!(fixture.prepared.identity(), &identity_before);
            assert_eq!(fixture.prepared.change, change_before);
        }
    }
}
