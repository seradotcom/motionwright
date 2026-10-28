//! Pure DTO/recipe checks and an optional local CLI transport witness only.
//! No fixture is a Core authority, native profile, font proof or physical PASS.
use super::*;
use crate::semantic_graph_source_fixture as source;
use std::time::Duration;

// Pure recipe/decoder data only. The client constructor below always obtains
// its comparison value from a real SQLite Store, never from this fixture.
const FIXTURE_STORE_IDENTITY: &str = "unit-test-only-summary-identity";

// Positional struct sequences are valid to Serde's derived visitor, but are
// not the named object records promised by this Graph wire contract.
fn positional(value: &Value, fields: &[&str]) -> Value {
    Value::Array(fields.iter().map(|field| value[*field].clone()).collect())
}

fn selected_context() -> (SemanticGraphContextRequest, Value) {
    let project = source::seed_domain();
    let request = SemanticGraphContextRequest {
        source_snapshot: GraphSourceSnapshot::from_project(&project),
        scene_id: project.scenes[0].id,
        variant_profile_ref: project.deliverables[0].id,
    };
    let value = json!({
        "schema":CONTEXT_SCHEMA,"source_snapshot":request.source_snapshot,
        "scene_id":request.scene_id,"variant_profile_ref":request.variant_profile_ref,
        "component_version":SEMANTIC_GRAPH_COMPONENT_VERSION,"native_profile":SEMANTIC_GRAPH_PROFILE,
        "font_weight":SEMANTIC_GRAPH_FONT_WEIGHT,
        "font_selection":{"primary_family":"Instrument Sans Variable","mono_family":"IBM Plex Mono"},
        "availability":"blocked","blocking_reasons":["native_graph_profile_unavailable"],
        "dependencies":[{"kind":"scene","scene_id":request.scene_id}]
    });
    (request, value)
}

#[test]
fn graph_recipe_context_decodes_only_the_closed_bound_domain_dto() {
    let (request, value) = selected_context();
    assert_eq!(value.as_object().unwrap().len(), 11);
    let decoded = decode_context(&value, &request).unwrap();
    assert_eq!(decoded.availability, GraphAvailability::Blocked);
    assert_eq!(decoded.source_snapshot, request.source_snapshot);
    for (pointer, replacement) in [
        ("/component_version", json!("1")),
        ("/native_profile", json!("another-profile")),
        ("/font_weight", json!(700)),
        ("/font_selection/primary_family", json!("system-font")),
        ("/availability", json!("ready")),
        ("/blocking_reasons", json!([])),
        ("/scene_id", json!(Uuid::new_v4())),
        ("/variant_profile_ref", json!(Uuid::new_v4())),
        (
            "/source_snapshot/revision",
            json!(request.source_snapshot.revision + 1),
        ),
    ] {
        let mut changed = value.clone();
        *changed.pointer_mut(pointer).unwrap() = replacement;
        assert!(decode_context(&changed, &request).is_err(), "{pointer}");
    }
    let mut extra = value;
    extra["operation_permit"] = json!(true);
    assert!(decode_context(&extra, &request).is_err());
}

#[test]
fn graph_recipe_context_rejects_positional_context_fonts_and_source_objects() {
    let (request, original) = selected_context();
    for (pointer, fields) in [
        (
            "",
            &[
                "schema",
                "source_snapshot",
                "scene_id",
                "variant_profile_ref",
                "component_version",
                "native_profile",
                "font_weight",
                "font_selection",
                "availability",
                "blocking_reasons",
                "dependencies",
            ][..],
        ),
        ("/font_selection", &["primary_family", "mono_family"][..]),
        (
            "/source_snapshot",
            &["project_id", "generation", "branch_id", "revision"][..],
        ),
    ] {
        let mut changed = original.clone();
        let sequence = positional(changed.pointer(pointer).unwrap(), fields);
        *changed.pointer_mut(pointer).unwrap() = sequence;
        assert!(
            serde_json::from_value::<ContextWire>(changed.clone()).is_ok(),
            "{pointer}"
        );
        assert!(decode_context(&changed, &request).is_err(), "{pointer}");
    }
    assert_eq!(
        decode_context(&original, &request).unwrap().availability,
        GraphAvailability::Blocked
    );
}

#[test]
fn graph_recipe_observation_rejects_positional_page_and_version_objects() {
    let (request, _) = selected_context();
    let original = observation(&request.source_snapshot);
    for (pointer, fields) in [
        ("", &["page", "ref"][..]),
        (
            "/page",
            &["version", "scope", "items", "next", "complete"][..],
        ),
        ("/page/version", &["resource", "generation", "revision"][..]),
    ] {
        let mut changed = original.clone();
        let sequence = positional(changed.pointer(pointer).unwrap(), fields);
        *changed.pointer_mut(pointer).unwrap() = sequence;
        assert!(
            serde_json::from_value::<ObservedData>(changed.clone()).is_ok(),
            "{pointer}"
        );
        assert!(
            decode_observation(
                &changed,
                request.source_snapshot.project_id,
                FIXTURE_STORE_IDENTITY
            )
            .is_err(),
            "{pointer}"
        );
    }
    assert_eq!(
        decode_observation(
            &original,
            request.source_snapshot.project_id,
            FIXTURE_STORE_IDENTITY
        )
        .unwrap(),
        request.source_snapshot
    );
}

fn acknowledgement_shape_fixture(remove: bool) -> (Value, GraphSourceSnapshot, RequestIdentity) {
    // Pure Domain history. No transport, Host, owner, native render or grant.
    let mut project = source::seed_domain();
    let graph = source::graph_for(&project, false);
    let upsert = Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    };
    let source = if remove {
        let revision = project.revision.checked_add(1).unwrap();
        project.apply_change(&upsert).unwrap();
        project.commit_revision(revision, &upsert).unwrap();
        GraphSourceSnapshot::from_project(&project)
    } else {
        GraphSourceSnapshot::from_project(&project)
    };
    let change = if remove {
        Change::RemoveSemanticGraph {
            graph_id: graph.id,
            scene_id: graph.scene_id,
            source_snapshot: source.clone(),
        }
    } else {
        upsert
    };
    let request_id = "5C77EA19-C0B1-47F4-B03B-495D72406A1F";
    let identity =
        crate::apply_recovery_identity(project.id, project.generation, request_id, &change)
            .unwrap();
    project.apply_change(&change).unwrap();
    project
        .commit_revision(source.revision.checked_add(1).unwrap(), &change)
        .unwrap();
    let value = json!({
        "resource":project.resource_key(), "generation":project.generation.to_string(),
        "revision":project.revision.to_string(), "previous_revision":source.revision.to_string(),
        "request_id":request_id, "replayed":false, "project":project, "recovery":identity,
    });
    (value, source, identity)
}

#[test]
fn graph_recipe_acknowledgement_rejects_positional_receipt_and_recovery_identity() {
    for remove in [false, true] {
        let (original, source, identity) = acknowledgement_shape_fixture(remove);
        let request_id = original["request_id"].as_str().unwrap();
        assert!(decode_apply_receipt(&original, request_id, &source, &identity).is_ok());
        for (pointer, fields) in [
            (
                "",
                &[
                    "resource",
                    "generation",
                    "revision",
                    "previous_revision",
                    "request_id",
                    "replayed",
                    "project",
                    "recovery",
                ][..],
            ),
            (
                "/recovery",
                &["resource", "epoch", "key", "request_sha256"][..],
            ),
        ] {
            let mut changed = original.clone();
            let sequence = positional(changed.pointer(pointer).unwrap(), fields);
            *changed.pointer_mut(pointer).unwrap() = sequence;
            assert!(
                serde_json::from_value::<SaveWire>(changed.clone()).is_ok(),
                "{pointer}"
            );
            let error = decode_apply_receipt(&changed, request_id, &source, &identity)
                .err()
                .unwrap();
            assert!(
                !after_dispatch(
                    if remove {
                        Action::Remove
                    } else {
                        Action::Upsert
                    },
                    error
                )
                .outcome_known
            );
        }
    }
}

#[test]
fn graph_recipe_acknowledgement_refuses_silent_project_field_loss_or_default_fill() {
    for remove in [false, true] {
        let (original, source, identity) = acknowledgement_shape_fixture(remove);
        let request_id = original["request_id"].as_str().unwrap();
        let accepted = decode_apply_receipt(&original, request_id, &source, &identity).unwrap();
        let original_bytes = serde_json::to_vec(&original).unwrap();
        let mut extra = original.clone();
        extra["project"]["future_component_source"] =
            json!({"author_intent":"must remain original"});
        let mut omitted = original.clone();
        assert_eq!(omitted["project"]["reviews"], json!([]));
        omitted["project"]
            .as_object_mut()
            .unwrap()
            .remove("reviews");
        for changed in [extra, omitted] {
            let normalized: SaveWire = serde_json::from_value(changed.clone()).unwrap();
            normalized.project.validate().unwrap();
            assert_eq!(&normalized.project, accepted.project());
            let error = decode_apply_receipt(&changed, request_id, &source, &identity)
                .err()
                .unwrap();
            assert!(
                !after_dispatch(
                    if remove {
                        Action::Remove
                    } else {
                        Action::Upsert
                    },
                    error
                )
                .outcome_known
            );
        }
        assert_eq!(serde_json::to_vec(&original).unwrap(), original_bytes);
    }
}

#[test]
fn graph_recipe_recovery_rejects_positional_record_container_and_identity() {
    let (_, _, identity) = acknowledgement_shape_fixture(false);
    let original = json!({"record":{"state":"outcome_unknown","identity":identity},
        "historical_only":true,"current_authority":false,"replay_allowed":false});
    for (pointer, fields) in [
        (
            "",
            &[
                "record",
                "historical_only",
                "current_authority",
                "replay_allowed",
            ][..],
        ),
        (
            "/record/identity",
            &["resource", "epoch", "key", "request_sha256"][..],
        ),
    ] {
        let mut changed = original.clone();
        let sequence = positional(changed.pointer(pointer).unwrap(), fields);
        *changed.pointer_mut(pointer).unwrap() = sequence;
        assert!(
            serde_json::from_value::<RecoveryWire>(changed.clone()).is_ok(),
            "{pointer}"
        );
        assert!(decode_recovery(&changed, &identity).is_err(), "{pointer}");
    }
    assert!(matches!(
        decode_recovery(&original, &identity).unwrap(),
        RecoveryRecord::OutcomeUnknown { .. }
    ));
}

#[test]
fn graph_recipe_controls_reject_positional_nested_records_before_interpretation() {
    // Deliberately incomplete recipe data: no success envelope or authority.
    let original = json!({"completed":false,"steps":[{"step":"mount","ok":false,"attempts":1}],
        "outputs":{"mount":{"binding_ref":"inert","provider":"inert","provider_generation":0,
            "connection":"inert","mount":{"root":"inert","path":"inert","read":false,"write":false,"execute":false},"admission_ttl_ms":0},
            "observe":{"binding_ref":"inert","result":null,"admission_current":false,"admission_state":"inert"},
            "operation":{"binding_ref":"inert","result":null,"admission_current":false,"admission_state":"inert"}}});
    assert!(!decode_recipe_data(&original).unwrap().completed);
    for (pointer, fields) in [
        ("", &["completed", "steps", "outputs"][..]),
        ("/steps/0", &["step", "ok", "attempts"][..]),
        ("/outputs", &["mount", "observe", "operation"][..]),
        (
            "/outputs/mount",
            &[
                "binding_ref",
                "provider",
                "provider_generation",
                "connection",
                "mount",
                "admission_ttl_ms",
            ][..],
        ),
        (
            "/outputs/mount/mount",
            &["root", "path", "read", "write", "execute"][..],
        ),
        (
            "/outputs/observe",
            &[
                "binding_ref",
                "result",
                "admission_current",
                "admission_state",
            ][..],
        ),
        (
            "/outputs/operation",
            &[
                "binding_ref",
                "result",
                "admission_current",
                "admission_state",
            ][..],
        ),
    ] {
        let mut changed = original.clone();
        let sequence = positional(changed.pointer(pointer).unwrap(), fields);
        *changed.pointer_mut(pointer).unwrap() = sequence;
        assert!(
            serde_json::from_value::<RecipeData>(changed.clone()).is_ok(),
            "{pointer}"
        );
        assert!(decode_recipe_data(&changed).is_err(), "{pointer}");
    }
}

#[test]
fn graph_recipe_failure_envelope_rejects_positional_error_execution_and_progress() {
    let mut error = Error::new(ErrorCode::Cancelled, "original private interruption")
        .uncertain()
        .recipe_progress(2, "operation");
    // Error omits empty candidates on serialization. Keep this sequence case
    // otherwise valid so its refusal proves object shape, not a null Vec.
    error.candidates.push("original private candidate".into());
    let original = serde_json::to_value(Envelope::finish(
        "original".into(),
        RECIPE_COMMAND.into(),
        "core".into(),
        Duration::ZERO,
        false,
        Err(error.clone()),
    ))
    .unwrap();
    for (pointer, fields) in [
        (
            "",
            &[
                "ok",
                "request_id",
                "command",
                "data",
                "error",
                "execution",
                "warnings",
            ][..],
        ),
        (
            "/error",
            &[
                "code",
                "message",
                "candidates",
                "outcome_known",
                "progress",
                "native_diagnostic",
            ][..],
        ),
        (
            "/error/progress",
            &["completed_steps", "failed_step", "state"][..],
        ),
        (
            "/execution",
            &[
                "backend",
                "duration_ms",
                "policy_decision",
                "fallbacks_attempted",
                "provenance",
                "dry_run",
            ][..],
        ),
    ] {
        let mut changed = original.clone();
        let sequence = positional(changed.pointer(pointer).unwrap(), fields);
        *changed.pointer_mut(pointer).unwrap() = sequence;
        assert!(
            serde_json::from_value::<Envelope>(changed.clone()).is_ok(),
            "{pointer}"
        );
        assert!(decode_envelope(&changed).is_err(), "{pointer}");
    }
    assert_eq!(
        serde_json::to_value(decode_envelope(&original).unwrap().error.unwrap()).unwrap(),
        serde_json::to_value(error).unwrap()
    );
}

#[test]
fn graph_recipe_context_preserves_all_six_dependency_variants_and_order() {
    let (request, mut value) = selected_context();
    let ids: Vec<Uuid> = (0..8).map(|_| Uuid::new_v4()).collect();
    let dependencies = json!([
        {"kind":"scene","scene_id":ids[0]},
        {"kind":"claim","claim_id":ids[1],"content_sha256":"a1".repeat(32)},
        {"kind":"asset","asset_id":ids[2],"content_sha256":"b2".repeat(32)},
        {"kind":"canvas_node","scene_id":ids[3],"node_id":ids[4],"content_sha256":"c3".repeat(32)},
        {"kind":"measured_datum","table_id":ids[5],"table_version":2,"datum_key":"original-row","source_sha256":"d4".repeat(32)},
        {"kind":"brand","id":ids[6],"version":3,"content_sha256":"e5".repeat(32)}
    ]);
    value["dependencies"] = dependencies.clone();
    let context = decode_context(&value, &request).unwrap();
    assert_eq!(
        serde_json::to_value(context.dependencies).unwrap(),
        dependencies
    );
    value["dependencies"][1]["content_sha256"] = json!("A1".repeat(32));
    assert!(decode_context(&value, &request).is_err());
}

#[test]
fn graph_recipe_context_uses_the_fixed_scope_original_request_cursor_and_page_version() {
    let (request, context) = selected_context();
    let token = context_cursor_token(&request).unwrap();
    assert!(token.len() <= 1024);
    assert_eq!(token, serde_json::to_string(&request).unwrap());
    assert_eq!(
        serde_json::from_str::<SemanticGraphContextRequest>(&token).unwrap(),
        request
    );
    let args = fixed_recipe(
        Action::Context,
        json!({
            "resource":format!("project:{}",request.source_snapshot.project_id),
            "project_id":request.source_snapshot.project_id.to_string(),
            "generation":request.source_snapshot.generation.to_string(),
            "expected_store":FIXTURE_STORE_IDENTITY,
            "branch_id":request.source_snapshot.branch_id.to_string(),
            "revision":request.source_snapshot.revision.to_string(),"context_token":token,
        }),
    );
    let final_request = &args["recipe"]["steps"][2]["args"]["request"];
    assert_eq!(final_request["command"], OBSERVE);
    assert_eq!(final_request["args"]["scope"], CONTEXT_SCOPE);
    assert_eq!(final_request["args"]["resource"], var("/inputs/resource"));
    assert_eq!(final_request["args"]["limit"], 1);
    assert_eq!(
        final_request["args"]["cursor"],
        json!({
            "version":var("/steps/observe/result/data/page/version"),
            "scope":CONTEXT_SCOPE,"token":var("/inputs/context_token"),
        })
    );
    assert_eq!(final_request["args"].as_object().unwrap().len(), 5);
    assert!(final_request["args"].get("request").is_none());
    assert!(final_request["args"].get("expected_store").is_none());
    assert!(
        args["recipe"]["steps"][1]["assertions"]
            .as_array()
            .unwrap()
            .contains(&assertion(
                var("/steps/observe/result/data/page/items/0/store_identity"),
                "equals",
                var("/inputs/expected_store"),
            ))
    );
    let summary: ObservedData = decode(&observation(&request.source_snapshot)).unwrap();
    let original = json!({
        "ref":"original-authoring-observation-ref",
        "page":{
            "version":{"resource":format!("project:{}",request.source_snapshot.project_id),
                "generation":request.source_snapshot.generation.to_string(),
                "revision":request.source_snapshot.revision.to_string()},
            "scope":CONTEXT_SCOPE,"items":[context],"next":null,"complete":true
        }
    });
    let decoded = decode_authoring_observation(&original, &request, &summary.page.version).unwrap();
    assert_eq!(decoded.source_snapshot, request.source_snapshot);
    for (pointer, replacement) in [
        ("/page/scope", json!("summary")),
        (
            "/page/scope",
            json!(format!(
                "graph-authoring:{}:{}",
                request.scene_id, request.variant_profile_ref
            )),
        ),
        ("/page/items", json!([])),
        ("/page/complete", json!(false)),
        (
            "/page/version/revision",
            json!((request.source_snapshot.revision + 1).to_string()),
        ),
        (
            "/page/items/0/source_snapshot/branch_id",
            json!(Uuid::new_v4()),
        ),
        ("/page/items/0/scene_id", json!(Uuid::new_v4())),
    ] {
        let mut changed = original.clone();
        *changed.pointer_mut(pointer).unwrap() = replacement;
        assert!(
            decode_authoring_observation(&changed, &request, &summary.page.version).is_err(),
            "{pointer}"
        );
    }
    let mut changed_summary = observation(&request.source_snapshot);
    changed_summary["page"]["version"]["revision"] =
        json!((request.source_snapshot.revision + 1).to_string());
    let changed_summary: ObservedData = decode(&changed_summary).unwrap();
    assert!(
        decode_authoring_observation(&original, &request, &changed_summary.page.version).is_err()
    );
    let mut invalid_request = request.clone();
    invalid_request.source_snapshot.branch_id = Uuid::nil();
    assert!(context_cursor_token(&invalid_request).is_err());
    invalid_request = request.clone();
    invalid_request.scene_id = Uuid::nil();
    assert!(context_cursor_token(&invalid_request).is_err());
}

fn observation(snapshot: &GraphSourceSnapshot) -> Value {
    json!({
        "ref":"opaque-original-core-ref",
        "page":{
            "version":{"resource":format!("project:{}",snapshot.project_id),
                "generation":snapshot.generation.to_string(),"revision":snapshot.revision.to_string()},
            "scope":"summary","items":[{"id":snapshot.project_id.to_string(),
                "revision":snapshot.revision.to_string(),"active_branch":snapshot.branch_id.to_string(),
                "store_identity":FIXTURE_STORE_IDENTITY}],
            "next":null,"complete":true
        }
    })
}

#[test]
fn graph_recipe_snapshot_uses_full_page_revision_and_active_branch_not_native_target() {
    let (request, _) = selected_context();
    let mut snapshot = request.source_snapshot;
    snapshot.revision = 9_007_199_254_740_991;
    let original = observation(&snapshot);
    assert_eq!(
        decode_observation(&original, snapshot.project_id, FIXTURE_STORE_IDENTITY).unwrap(),
        snapshot
    );
    for (pointer, replacement) in [
        (
            "/ref",
            json!({"backend":"driver:motionwright","identity":"local-minted","revision":1}),
        ),
        (
            "/page/version/resource",
            json!(format!("project:{}", Uuid::new_v4())),
        ),
        ("/page/items/0/revision", json!("1")),
        ("/page/items/0/active_branch", json!("not-a-uuid")),
        (
            "/page/items/0/store_identity",
            json!("different-physical-store"),
        ),
        ("/page/items/0/store_identity", Value::Null),
        ("/page/complete", json!(false)),
    ] {
        let mut changed = original.clone();
        *changed.pointer_mut(pointer).unwrap() = replacement;
        assert!(
            decode_observation(&changed, snapshot.project_id, FIXTURE_STORE_IDENTITY).is_err(),
            "{pointer}"
        );
    }
}

#[test]
fn graph_recipe_keeps_its_command_roster_bindings_and_inputs_closed() {
    let project = source::seed_domain();
    let mut graph = source::graph_for(&project, false);
    graph.authorship.rationale = r#"{"$var":"/steps/mount"}"#.into();
    let args = fixed_recipe(
        Action::Upsert,
        json!({
            "resource":project.resource_key(),"project_id":project.id.to_string(),
            "generation":project.generation.to_string(),"request_id":"stable-app-key","graph":graph,
            "expected_store":FIXTURE_STORE_IDENTITY,
        }),
    );
    let steps = args["recipe"]["steps"].as_array().unwrap();
    assert_eq!(steps.len(), 3);
    assert_eq!(steps[0]["command"], "providers.mount.observe");
    assert_eq!(steps[0]["args"], json!({"provider":PROVIDER,"root":ROOT}));
    assert_eq!(steps[1]["command"], "providers.execute_bound");
    assert_eq!(steps[2]["command"], "providers.execute_bound");
    assert_eq!(steps[1]["args"]["request"]["command"], OBSERVE);
    assert_eq!(steps[2]["args"]["request"]["command"], UPSERT);
    assert_eq!(
        steps[2]["args"]["binding_ref"],
        var("/steps/mount/binding_ref")
    );
    assert_eq!(
        steps[2]["args"]["request"]["args"]["ref"],
        var("/steps/observe/result/data/ref")
    );
    assert_eq!(
        steps[2]["args"]["request"]["args"]["request_id"],
        var("/inputs/request_id")
    );
    assert_eq!(
        steps[2]["args"]["request"]["args"]["graph"],
        var("/inputs/graph")
    );
    assert_eq!(
        steps[2]["args"]["request"]["args"]
            .as_object()
            .unwrap()
            .len(),
        5
    );
    assert_eq!(
        steps[2]["args"]["request"]["args"]["expected_store"],
        var("/inputs/expected_store")
    );
    assert_eq!(args["inputs"]["expected_store"], FIXTURE_STORE_IDENTITY);
    assert_eq!(
        args["recipe"]["inputs"]["expected_store"],
        json!({"kind":"string"})
    );
    assert!(
        steps[1]["assertions"]
            .as_array()
            .unwrap()
            .contains(&assertion(
                var("/steps/observe/result/data/page/items/0/store_identity"),
                "equals",
                var("/inputs/expected_store"),
            ))
    );
    assert_eq!(
        args["inputs"]["graph"]["authorship"]["rationale"],
        graph.authorship.rationale
    );
    assert!(steps.iter().all(|step| step["retry"]["attempts"] == 1));
    assert_eq!(
        steps
            .iter()
            .map(|step| step["timeout_ms"].as_u64().unwrap())
            .sum::<u64>(),
        40_000
    );
    assert!(
        steps[2].get("assertions").is_none(),
        "no new known rejection after a committed write"
    );
}

#[test]
fn graph_recipe_save_receipt_keeps_application_key_separate_from_wire_identity() {
    let project = source::seed_domain();
    let graph = source::graph_for(&project, false);
    let change = Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    };
    let identity =
        crate::apply_recovery_identity(project.id, project.generation, "stable-app-key", &change)
            .unwrap();
    let mut candidate = project.clone();
    candidate.apply_change(&change).unwrap();
    candidate
        .commit_revision(project.revision.checked_add(1).unwrap(), &change)
        .unwrap();
    let original = json!({
        "resource":candidate.resource_key(),"generation":candidate.generation.to_string(),
        "revision":candidate.revision.to_string(),"previous_revision":project.revision.to_string(),
        "request_id":"stable-app-key","replayed":false,"project":candidate,"recovery":identity
    });
    for replayed in [false, true] {
        let mut original = original.clone();
        original["replayed"] = json!(replayed);
        let receipt = decode_save(&original, "stable-app-key", &graph, &identity).unwrap();
        assert_eq!(receipt.app_request_id(), "stable-app-key");
        assert_eq!(receipt.recovery_identity(), &identity);
        assert_eq!(receipt.project(), &candidate);
        assert_eq!(receipt.previous_revision(), project.revision);
        assert_eq!(receipt.revision(), project.revision.checked_add(1).unwrap());
        assert_eq!(receipt.replayed(), replayed);
        let mut wrong_request = original.clone();
        wrong_request["request_id"] = json!("fresh-broker-wire-id");
        let mut wrong_previous = original.clone();
        wrong_previous["previous_revision"] = json!(candidate.revision.to_string());
        let mut later_project = original.clone();
        later_project["revision"] = json!((candidate.revision + 1).to_string());
        later_project["project"]["revision"] = json!(candidate.revision + 1);
        let mut wrong_branch = original.clone();
        wrong_branch["project"]["active_branch"] = json!(Uuid::new_v4());
        let mut changed_source = candidate.clone();
        changed_source
            .production_design
            .semantic_graphs
            .iter_mut()
            .find(|saved| saved.id == graph.id)
            .unwrap()
            .authorship
            .rationale
            .push_str(" Different retained source");
        let mut wrong_source = original;
        wrong_source["project"] = serde_json::to_value(changed_source).unwrap();
        for changed in [
            wrong_request,
            wrong_previous,
            later_project,
            wrong_branch,
            wrong_source,
        ] {
            let error = decode_save(&changed, "stable-app-key", &graph, &identity)
                .err()
                .unwrap();
            assert!(!after_dispatch(Action::Upsert, error).outcome_known);
        }
    }
    let mut overflowed = graph.authorship.source_snapshot.clone();
    overflowed.revision = u64::MAX;
    assert!(decode_apply_receipt(&original, "stable-app-key", &overflowed, &identity).is_err());
}

#[test]
fn graph_recipe_prepares_exact_storage_recovery_identity_before_any_client_exists() {
    let project = source::seed_domain();
    let graph = source::graph_for(&project, false);
    let before = graph.clone();
    let identity = NativeGraphRecipeClient::prepare_upsert("stable-app-key", &graph).unwrap();
    let change = Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    };
    assert_eq!(
        identity.request_sha256,
        motionwright_storage::apply_request_digest_v1(project.id, &change).unwrap()
    );
    assert_eq!(
        identity,
        crate::apply_recovery_identity(project.id, project.generation, "stable-app-key", &change)
            .unwrap()
    );
    assert_eq!(graph, before);
    assert!(NativeGraphRecipeClient::prepare_upsert(&"x".repeat(88), &graph).is_err());
    let mut changed = graph;
    changed
        .authorship
        .rationale
        .push_str(" Exact changed source");
    let later = NativeGraphRecipeClient::prepare_upsert("stable-app-key", &changed).unwrap();
    assert_ne!(later.request_sha256, identity.request_sha256);
    assert_eq!(later.key, identity.key);
}

#[test]
fn graph_recipe_removal_preparation_uses_canonical_change_without_a_current_graph() {
    let project = source::seed_domain();
    let snapshot = GraphSourceSnapshot::from_project(&project);
    let graph_id = Uuid::new_v4();
    let scene_id = project.scenes[0].id;
    assert!(project.production_design.semantic_graphs.is_empty());
    let identity =
        NativeGraphRecipeClient::prepare_remove("remove-app-key", graph_id, scene_id, &snapshot)
            .unwrap();
    let change = Change::RemoveSemanticGraph {
        graph_id,
        scene_id,
        source_snapshot: snapshot.clone(),
    };
    assert_eq!(
        identity.request_sha256,
        motionwright_storage::apply_request_digest_v1(project.id, &change).unwrap()
    );
    let args = fixed_recipe(
        Action::Remove,
        json!({
            "resource":project.resource_key(),"project_id":project.id.to_string(),
            "generation":project.generation.to_string(),"request_id":"remove-app-key",
            "expected_store":FIXTURE_STORE_IDENTITY,
            "graph_id":graph_id.to_string(),"scene_id":scene_id.to_string(),"source_snapshot":snapshot,
        }),
    );
    let final_request = &args["recipe"]["steps"][2]["args"]["request"];
    assert_eq!(final_request["command"], REMOVE);
    assert_eq!(final_request["args"].as_object().unwrap().len(), 7);
    assert_eq!(final_request["args"]["graph_id"], var("/inputs/graph_id"));
    assert_eq!(final_request["args"]["scene_id"], var("/inputs/scene_id"));
    assert_eq!(
        final_request["args"]["source_snapshot"],
        var("/inputs/source_snapshot")
    );
    assert_eq!(
        final_request["args"]["request_id"],
        var("/inputs/request_id")
    );
    assert_eq!(final_request["args"]["include_recovery"], true);
    assert_eq!(
        final_request["args"]["expected_store"],
        var("/inputs/expected_store")
    );
    assert!(final_request["args"].get("graph").is_none());
    assert!(final_request["args"].get("variant_profile_ref").is_none());
}

#[test]
fn graph_recipe_removal_receipt_validates_original_absence_without_replaying() {
    let mut project = source::seed_domain();
    let graph = source::graph_for(&project, false);
    let upsert = Change::UpsertSemanticGraph {
        graph: Box::new(graph.clone()),
    };
    let upsert_revision = project.revision.checked_add(1).unwrap();
    project.apply_change(&upsert).unwrap();
    project.commit_revision(upsert_revision, &upsert).unwrap();
    let snapshot = GraphSourceSnapshot::from_project(&project);
    let identity = NativeGraphRecipeClient::prepare_remove(
        "remove-app-key",
        graph.id,
        graph.scene_id,
        &snapshot,
    )
    .unwrap();
    let before = project.clone();
    let removal = Change::RemoveSemanticGraph {
        graph_id: graph.id,
        scene_id: graph.scene_id,
        source_snapshot: snapshot.clone(),
    };
    project.apply_change(&removal).unwrap();
    project
        .commit_revision(before.revision.checked_add(1).unwrap(), &removal)
        .unwrap();
    assert!(project.production_design.semantic_graphs.is_empty());
    // The same pure identity remains available after the Graph is absent.
    assert_eq!(
        NativeGraphRecipeClient::prepare_remove(
            "remove-app-key",
            graph.id,
            graph.scene_id,
            &snapshot,
        )
        .unwrap(),
        identity
    );
    let original = json!({
        "resource":project.resource_key(),"generation":project.generation.to_string(),
        "revision":project.revision.to_string(),"previous_revision":before.revision.to_string(),
        "request_id":"remove-app-key","replayed":true,"project":project,"recovery":identity,
    });
    for replayed in [false, true] {
        let mut original = original.clone();
        original["replayed"] = json!(replayed);
        let receipt =
            decode_remove(&original, "remove-app-key", graph.id, &snapshot, &identity).unwrap();
        assert_eq!(receipt.replayed(), replayed);
        assert_eq!(receipt.project(), &project);
        assert!(
            receipt
                .project()
                .production_design
                .semantic_graphs
                .is_empty()
        );
        let mut wrong_previous = original.clone();
        wrong_previous["previous_revision"] = json!(project.revision.to_string());
        let mut later_project = original.clone();
        later_project["revision"] = json!((project.revision + 1).to_string());
        later_project["project"]["revision"] = json!(project.revision + 1);
        let mut wrong_branch = original.clone();
        wrong_branch["project"]["active_branch"] = json!(Uuid::new_v4());
        let mut still_present = original;
        still_present["project"]["production_design"]["semantic_graphs"] = json!([graph]);
        for wrong in [wrong_previous, later_project, wrong_branch, still_present] {
            let error = decode_remove(&wrong, "remove-app-key", graph.id, &snapshot, &identity)
                .err()
                .unwrap();
            assert!(!after_dispatch(Action::Remove, error).outcome_known);
        }
    }
}

#[test]
fn graph_recipe_recovery_preserves_unknown_and_never_promotes_historical_authority() {
    let project = source::seed_domain();
    let change = Change::UpsertSemanticGraph {
        graph: Box::new(source::graph_for(&project, false)),
    };
    let identity =
        crate::apply_recovery_identity(project.id, project.generation, "original-key", &change)
            .unwrap();
    let args = fixed_recipe(
        Action::Recovery,
        json!({
            "resource":identity.resource,"project_id":project.id.to_string(),
            "generation":project.generation.to_string(),"request":identity,
            "expected_store":FIXTURE_STORE_IDENTITY,
        }),
    );
    assert_eq!(
        args["inputs"]["request"],
        serde_json::to_value(&identity).unwrap()
    );
    assert_eq!(
        args["recipe"]["steps"][2]["args"]["request"]["args"],
        json!({"ref":var("/steps/observe/result/data/ref"),"request":var("/inputs/request")})
    );
    assert!(
        args["recipe"]["steps"][1]["assertions"]
            .as_array()
            .unwrap()
            .contains(&assertion(
                var("/steps/observe/result/data/page/items/0/store_identity"),
                "equals",
                var("/inputs/expected_store"),
            ))
    );
    let original = json!({
        "record":{"state":"outcome_unknown","identity":identity},
        "historical_only":true,"current_authority":false,"replay_allowed":false
    });
    assert!(matches!(
        decode_recovery(&original, &identity).unwrap(),
        RecoveryRecord::OutcomeUnknown { .. }
    ));
    for key in ["current_authority", "replay_allowed"] {
        let mut changed = original.clone();
        changed[key] = json!(true);
        assert!(decode_recovery(&changed, &identity).is_err());
    }
    let mut changed = original;
    changed["record"]["identity"]["request_sha256"] = json!("00".repeat(32));
    assert!(decode_recovery(&changed, &identity).is_err());
}

#[test]
fn graph_recipe_original_unknown_error_is_not_reclassified_by_decode() {
    let error = Error::new(ErrorCode::Cancelled, "original interrupted operation")
        .uncertain()
        .recipe_progress(2, "operation");
    let envelope = Envelope::finish(
        "original-core-id".into(),
        RECIPE_COMMAND.into(),
        "core".into(),
        Duration::ZERO,
        false,
        Err(error.clone()),
    );
    let value = serde_json::to_value(envelope).unwrap();
    let original = BrokerResult {
        request_id: "original-core-id".into(),
        value: json!({"original_envelope":value,"transport_status_success":false}),
    };
    let Outer::Failed(retained) = decode_outer(&original, &value).unwrap() else {
        panic!("UNKNOWN cannot become success")
    };
    assert_eq!(
        serde_json::to_value(retained).unwrap(),
        serde_json::to_value(error).unwrap()
    );
}

// This shell fixture requires A2's Linux retained-connection identity successor.
// Unsupported identity is a test failure, never a skip or a fabricated value.
#[cfg(target_os = "linux")]
#[tokio::test]
async fn graph_recipe_transport_sends_exact_bounded_arguments_through_stdin_once() {
    use super::super::tests::fake_transport_connection;
    use std::fs;
    let temp = tempfile::tempdir().unwrap();
    let project = source::seed_domain();
    let graph = source::graph_for(&project, false);
    let mut store =
        motionwright_storage::Store::open(temp.path().join("actual/store.sqlite3")).unwrap();
    store.create_project(&project).unwrap();
    let expected_store = store.database_file_identity().unwrap();
    let service = StudioService::from_store(store);
    let error = Error::new(ErrorCode::Cancelled, "original Core UNKNOWN").uncertain();
    let envelope = Envelope::finish(
        "original-core-id".into(),
        RECIPE_COMMAND.into(),
        "core".into(),
        Duration::ZERO,
        false,
        Err(error.clone()),
    );
    let stdout = serde_json::to_string(&envelope).unwrap();
    let (mut connection, counter) =
        fake_transport_connection(&temp, project.resource_key(), &stdout, "", 1);
    let args_path = temp.path().join("original-stdin.json");
    let argv_path = temp.path().join("original-argv.txt");
    let quoted = |value: &str| value.replace('\'', "'\\''");
    fs::write(&connection.executable, format!(
        "#!/bin/sh\nprintf 'invoked\\n' >> '{}'\nprintf '%s\\n' \"$@\" > '{}'\ncat > '{}'\nprintf '%s' '{}'\nexit 1\n",
        quoted(&counter.to_string_lossy()), quoted(&argv_path.to_string_lossy()),
        quoted(&args_path.to_string_lossy()), quoted(&stdout),
    )).unwrap();
    connection.executable_sha256 = super::super::sha256_file(&connection.executable).unwrap();
    let custody = ProductionTransportCustody::default();
    let client = NativeGraphRecipeClient::new(connection, &service)
        .unwrap()
        .with_transport_custody(&custody);
    assert!(std::sync::Arc::ptr_eq(
        &client.service.store(),
        &service.store()
    ));
    assert_eq!(client.expected_store, expected_store);
    let report = client.upsert("stable-app-key", &graph).await.unwrap();
    assert_eq!(
        report.request_args()["inputs"]["expected_store"],
        expected_store
    );
    assert_eq!(
        NativeGraphRecipeClient::prepare_upsert("stable-app-key", &graph)
            .unwrap()
            .request_sha256,
        motionwright_storage::apply_request_digest_v1(
            project.id,
            &Change::UpsertSemanticGraph {
                graph: Box::new(graph.clone())
            },
        )
        .unwrap()
    );
    assert_eq!(
        serde_json::to_value(report.outcome().err().unwrap()).unwrap(),
        serde_json::to_value(error).unwrap()
    );
    assert!(std::ptr::eq(
        report.original_native_error().unwrap(),
        report.outcome().err().unwrap(),
    ));
    assert_eq!(
        report.original_envelope(),
        &serde_json::to_value(envelope).unwrap()
    );
    assert!(report.observation_envelope().is_none());
    assert!(report.operation_envelope().is_none());
    let retained = fs::read(args_path).unwrap();
    assert_eq!(retained, serde_json::to_vec(report.request_args()).unwrap());
    assert_eq!(
        hex::encode(Sha256::digest(&retained)),
        report.request_sha256()
    );
    let argv = fs::read_to_string(argv_path).unwrap();
    assert!(argv.contains("--args-file\n-\n"));
    assert!(!argv.contains("--args-json"));
    assert!(!argv.contains("stable-app-key"));
    assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
    let snapshot = custody.private_snapshot();
    let entry = &snapshot["entries"][0];
    assert_eq!(entry["stdin_mode"], "piped_graph_recipe_json");
    assert_eq!(entry["stdin"]["intended"]["value"], hex::encode(&retained));
    assert_eq!(
        entry["stdin"]["accepted_by_pipe_prefix"]["value"],
        hex::encode(&retained)
    );
    assert_eq!(entry["stdin"]["accepted_by_pipe_bytes"], retained.len());
    assert_eq!(entry["stdin"]["writer_returned"], true);
    assert_eq!(entry["stdin"]["shutdown_completed"], true);
    assert_eq!(entry["stdin"]["child_input_consumption_observed"], false);
    assert_eq!(entry["stdin"]["child_eof_read_observed"], false);
    assert_eq!(
        entry["transport_terminal"]["original_error"]["outcome_known"],
        false
    );
    // A different real SQLite file with the same logical Project cannot replace
    // the callback Store and receive another dispatch from this bound client.
    let mut replacement =
        motionwright_storage::Store::open(temp.path().join("replacement/store.sqlite3")).unwrap();
    replacement.create_project(&project).unwrap();
    assert_ne!(
        replacement.database_file_identity().unwrap(),
        expected_store
    );
    *service.store().lock() = replacement;
    let rejected = client.upsert("stable-app-key", &graph).await.err().unwrap();
    assert_eq!(rejected.code, ErrorCode::StaleReference);
    assert!(rejected.outcome_known);
    let recovery = NativeGraphRecipeClient::prepare_upsert("stable-app-key", &graph).unwrap();
    let original_recovery = serde_json::to_value(&recovery).unwrap();
    let rejected_read = client.operation_recovery(&recovery).await.err().unwrap();
    assert_eq!(rejected_read.code, ErrorCode::StaleReference);
    assert!(rejected_read.outcome_known);
    assert_eq!(serde_json::to_value(&recovery).unwrap(), original_recovery);
    assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
    assert_eq!(
        custody.private_snapshot()["entries"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
