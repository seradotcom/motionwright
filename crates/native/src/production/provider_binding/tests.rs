use super::*;

const OUTER: &str = "11111111111111111111111111111111";
const INNER: &str = "22222222222222222222222222222222";
const OBSERVATION: &str = "33333333333333333333333333333333";
const INSPECT: &str = "driver.motion-canvas.composition.inspect";
const IMPORT: &str = "driver.motion-canvas.asset.import";

fn root() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from(r"E:\Projects\motionwright\fixture-font-root")
    } else {
        PathBuf::from("/motionwright-fixture-font-root")
    }
}

fn execution(provider: &str, source: SourceKind, generation: Option<u64>) -> Value {
    json!({
        "backend": if provider == "semwright-core" { "core" } else { provider },
        "duration_ms": 1,
        "policy_decision": "allow",
        "fallbacks_attempted": [],
        "dry_run": false,
        "provenance": {
            "provider": provider,
            "source": source,
            "provider_version": "1.0.0",
            "capability_version": "1",
            "descriptor_sha256": "a".repeat(64),
            // The registry marks non-Builtin descriptor metadata untrusted.
            // Its authentic Driver identity is still checked independently.
            "untrusted_metadata": provider != "semwright-core",
            "catalog_revision": 7,
            "execution_provider": generation.map(|_| provider),
            "provider_generation": generation,
        }
    })
}

fn observation() -> BrokerResult {
    let mut result = BrokerResult {
        request_id: OBSERVATION.into(),
        value: json!({
            "data": {
                "binding_ref": "fixture-live-reference",
                "provider": PROVIDER,
                "provider_generation": 9,
                "connection": "fixture-provider-connection",
                "mount": {
                    "root": "media",
                    "path": root(),
                    "read": true,
                    "write": false,
                    "execute": false
                },
                "admission_ttl_ms": 30000
            },
            "execution": execution("semwright-core", SourceKind::Builtin, None),
            "warnings": [],
            "provenance_checked": true
        }),
    };
    result.value["original_envelope"] = json!({
        "ok": true, "request_id": OBSERVATION, "command": OBSERVE,
        "data": result.value["data"],
        "execution": result.value["execution"],
        "warnings": result.value["warnings"],
    });
    result
}

fn sync_original(value: &mut Value) {
    value["original_envelope"]["data"] = value["data"].clone();
    value["original_envelope"]["execution"] = value["execution"].clone();
    value["original_envelope"]["warnings"] = value["warnings"].clone();
}

fn binding() -> FontMediaBinding {
    FontMediaBinding::from_observation("b".repeat(64), &root(), observation())
        .expect("the controlled authenticated observation matches")
}

fn completed(command: &str) -> Value {
    let mut outer = json!({
        "data": {
            "binding_ref": "fixture-live-reference",
            "admission_current": true,
            "admission_state": "current",
            "result": {
                "ok": true,
                "request_id": INNER,
                "command": command,
                "data": {"fingerprint": "c".repeat(64), "applied": true},
                "execution": execution(PROVIDER, SourceKind::Driver, Some(9)),
                "warnings": ["fixture-preserved-warning"]
            }
        },
        "execution": execution("semwright-core", SourceKind::Builtin, None),
        "warnings": [],
        "provenance_checked": true
    });
    outer["original_envelope"] = json!({
        "ok": true, "request_id": OUTER, "command": EXECUTE,
        "data": outer["data"], "execution": outer["execution"],
        "warnings": outer["warnings"],
    });
    json!({
        "replayed": false,
        "result": outer,
        "receipt": {
            "stage": "completed",
            "payload": {
                "result": outer,
                "broker_request_id": OUTER,
                "provider_bound_font": binding().receipt_context()
            }
        }
    })
}

#[test]
fn observation_retains_the_authentic_response_and_does_not_supply_write_authority() {
    let original = observation();
    let bound = binding();
    assert_eq!(bound.original_observation.value, original.value);
    assert_eq!(bound.original_observation.request_id, OBSERVATION);
    assert_eq!(bound.root, root());
    assert_eq!(
        bound.receipt_context()["observation"]["data"]["mount"]["write"],
        false
    );
}

#[test]
fn observation_rejects_mismatched_or_unavailable_provider_mounts() {
    let changes = [
        ("/data/provider", json!("driver:other")),
        ("/data/provider_generation", json!(0)),
        ("/data/connection", json!("")),
        ("/data/binding_ref", json!("")),
        ("/data/admission_ttl_ms", json!(0)),
        ("/data/mount/root", json!("outputs")),
        ("/data/mount/path", json!(root().join("different"))),
        ("/data/mount/path", json!("relative-fonts")),
        ("/data/mount/read", json!(false)),
        ("/data/mount/write", json!(true)),
        ("/data/mount/execute", json!(true)),
    ];
    for (pointer, replacement) in changes {
        let mut observed = observation();
        *observed.value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut observed.value);
        assert!(
            FontMediaBinding::from_observation("b".repeat(64), &root(), observed).is_err(),
            "{pointer}"
        );
    }
}

#[test]
fn observation_rejects_doctor_config_and_additional_self_issued_fields() {
    let mut observed = observation();
    observed.value["data"]["authorized"] = json!(true);
    sync_original(&mut observed.value);
    assert!(FontMediaBinding::from_observation("b".repeat(64), &root(), observed).is_err());
    let mut observed = observation();
    observed.value["data"] = json!({"doctor": true, "root": root()});
    sync_original(&mut observed.value);
    assert!(FontMediaBinding::from_observation("b".repeat(64), &root(), observed).is_err());
    assert!(
        FontMediaBinding::from_observation("not-a-connection".into(), &root(), observation())
            .is_err()
    );
}

#[test]
fn observation_requires_original_core_provenance_and_nondry_execution() {
    for (pointer, replacement) in [
        ("/execution/backend", json!(PROVIDER)),
        ("/execution/dry_run", json!(true)),
        ("/execution/policy_decision", json!("deny")),
        ("/execution/fallbacks_attempted", json!(["other"])),
        ("/execution/provenance/provider", json!("driver:impostor")),
        ("/execution/provenance/source", json!(SourceKind::Driver)),
        ("/execution/provenance/untrusted_metadata", json!(true)),
        ("/execution/provenance/descriptor_sha256", json!("invalid")),
        ("/provenance_checked", json!(false)),
    ] {
        let mut observed = observation();
        *observed.value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut observed.value);
        assert!(
            FontMediaBinding::from_observation("b".repeat(64), &root(), observed).is_err(),
            "{pointer}"
        );
    }
    let mut observed = observation();
    observed.request_id = "config-id".into();
    observed.value["original_envelope"]["request_id"] = json!("config-id");
    let error = FontMediaBinding::from_observation("b".repeat(64), &root(), observed)
        .err()
        .expect("invalid canonical request identity must fail");
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert_eq!(
        error.message,
        "Bound media response is not an authenticated Core result"
    );
    assert!(error.outcome_known);
}

#[test]
fn font_route_has_four_commands_with_fixed_mutation_classification() {
    assert!(!mutation_for(INSPECT).unwrap());
    assert!(!mutation_for("driver.motion-canvas.asset.list").unwrap());
    assert!(mutation_for(IMPORT).unwrap());
    assert!(mutation_for("driver.motion-canvas.project.create").unwrap());
    for command in [
        "driver.motion-canvas.render.start",
        "driver.audio-analysis.voice.align",
        "providers.execute_bound",
        "driver.motion-canvas.arbitrary.exec",
        "doctor",
    ] {
        assert_eq!(
            mutation_for(command).unwrap_err().code,
            ErrorCode::Unsupported
        );
    }
    assert!(super::super::expected_authority(OBSERVE).is_none());
    assert!(super::super::expected_authority(EXECUTE).is_none());
}

#[test]
fn bound_request_retains_args_and_fixes_the_same_provider_and_nondry_route() {
    let args = json!({"expected_sha256": "d".repeat(64), "source": "brand.woff2"});
    let bound = binding();
    let actual = bound.request(IMPORT, args.clone()).unwrap();
    assert_eq!(
        actual,
        json!({
            "binding_ref":"fixture-live-reference",
            "request":{"command":IMPORT,"args":args,"backend":PROVIDER,"dry_run":false}
        })
    );
    assert!(
        bound
            .request("driver.audio-analysis.voice.align", json!({}))
            .is_err()
    );
}

#[test]
fn successful_bound_response_keeps_both_request_ids_and_original_envelopes() {
    let original = completed(IMPORT);
    let projected = binding()
        .normalize_completed(original.clone(), IMPORT, true)
        .unwrap();
    assert_eq!(
        projected["result"]["data"],
        original["result"]["data"]["result"]["data"]
    );
    assert_eq!(
        projected["result"]["execution"],
        original["result"]["data"]["result"]["execution"]
    );
    assert_eq!(
        projected["result"]["warnings"],
        json!(["fixture-preserved-warning"])
    );
    assert_eq!(
        projected["result"]["provider_bound"]["original"],
        original["result"]
    );
    assert_eq!(
        projected["result"]["provider_bound"]["outer_request_id"],
        OUTER
    );
    assert_eq!(
        projected["result"]["provider_bound"]["inner_request_id"],
        INNER
    );
    assert_eq!(projected["receipt"], original["receipt"]);
}

#[test]
fn bound_response_rejects_provider_substitution_and_generation_changes() {
    for (pointer, replacement) in [
        ("/result/data/binding_ref", json!("another-reference")),
        (
            "/result/data/result/command",
            json!("driver.motion-canvas.render.start"),
        ),
        ("/result/data/result/request_id", json!(OUTER)),
        (
            "/result/data/result/request_id",
            json!("missing-original-id"),
        ),
        (
            "/result/data/result/execution/backend",
            json!("driver:other"),
        ),
        (
            "/result/data/result/execution/provenance/provider",
            json!("driver:other"),
        ),
        (
            "/result/data/result/execution/provenance/source",
            json!(SourceKind::Builtin),
        ),
        (
            "/result/data/result/execution/provenance/execution_provider",
            json!("driver:other"),
        ),
        (
            "/result/data/result/execution/provenance/provider_generation",
            json!(10),
        ),
        (
            "/result/data/result/execution/provenance/descriptor_sha256",
            json!("invalid"),
        ),
    ] {
        let mut value = completed(IMPORT);
        *value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut value["result"]);
        let error = binding()
            .normalize_completed(value, IMPORT, true)
            .unwrap_err();
        assert!(!error.outcome_known, "{pointer}");
    }
}

#[test]
fn bound_response_rejects_inner_dry_run_denial_fallback_and_inconsistent_success() {
    for (pointer, replacement) in [
        ("/result/data/result/ok", json!(false)),
        ("/result/data/result/data", Value::Null),
        ("/result/data/result/execution/dry_run", json!(true)),
        (
            "/result/data/result/execution/policy_decision",
            json!("deny"),
        ),
        (
            "/result/data/result/execution/fallbacks_attempted",
            json!(["another-provider"]),
        ),
    ] {
        let mut value = completed(IMPORT);
        *value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut value["result"]);
        assert!(
            !binding()
                .normalize_completed(value, IMPORT, true)
                .unwrap_err()
                .outcome_known,
            "{pointer}"
        );
    }
    let mut value = completed(IMPORT);
    value["result"]["data"]["result"]["error"] =
        json!(Error::new(ErrorCode::Conflict, "conflicting typed result"));
    sync_original(&mut value["result"]);
    assert!(
        !binding()
            .normalize_completed(value, IMPORT, true)
            .unwrap_err()
            .outcome_known
    );
}

#[test]
fn bound_response_preserves_expired_history_without_current_admission() {
    let mut value = completed(IMPORT);
    value["result"]["data"]["admission_current"] = json!(false);
    value["result"]["data"]["admission_state"] = json!("expired");
    value["result"]["original_envelope"]["data"] = value["result"]["data"].clone();
    let error = binding()
        .normalize_completed(value, IMPORT, true)
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::StaleReference);
    assert!(error.outcome_known);
}

#[test]
fn bound_response_rejects_inconsistent_or_additional_wrapper_state() {
    let mut value = completed(INSPECT);
    value["result"]["data"]["admission_state"] = json!("expired");
    sync_original(&mut value["result"]);
    let error = binding()
        .normalize_completed(value, INSPECT, false)
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert_eq!(error.message, "Bound media admission state is inconsistent");
    assert!(error.outcome_known);
    let mut value = completed(INSPECT);
    value["result"]["data"]["admission_current"] = json!("true");
    sync_original(&mut value["result"]);
    assert!(
        binding()
            .normalize_completed(value, INSPECT, false)
            .is_err()
    );
    let mut value = completed(INSPECT);
    value["result"]["data"]["current"] = json!(true);
    sync_original(&mut value["result"]);
    assert!(
        binding()
            .normalize_completed(value, INSPECT, false)
            .is_err()
    );
}

#[test]
fn persisted_receipt_cannot_qualify_as_a_fresh_bound_observation() {
    let mut value = completed(INSPECT);
    value["replayed"] = json!(true);
    let error = binding()
        .normalize_completed(value, INSPECT, false)
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(error.outcome_known);
}

#[test]
fn wrapper_validation_preserves_mutation_uncertainty_and_readonly_certainty() {
    let mut value = completed(IMPORT);
    value["receipt"]["payload"]["broker_request_id"] = Value::Null;
    assert!(
        !binding()
            .normalize_completed(value, IMPORT, true)
            .unwrap_err()
            .outcome_known
    );
    let mut value = completed(INSPECT);
    value["receipt"]["payload"]["broker_request_id"] = Value::Null;
    assert!(
        binding()
            .normalize_completed(value, INSPECT, false)
            .unwrap_err()
            .outcome_known
    );
}

// These controls exercise the real Coordinator and SQLite receipt authority.
// The executable is deliberately an inert regular file: no Host is started.
fn no_dispatch_fixture() -> (
    tempfile::TempDir,
    motionwright_service::StudioService,
    motionwright_domain::Project,
    ProductionCoordinator,
    FontMediaBinding,
) {
    use super::super::{CONNECTION_SCHEMA, ProductionConnection, sha256_file};
    use std::fs;

    let temp = tempfile::tempdir().unwrap();
    let directory = fs::canonicalize(temp.path()).unwrap();
    let service =
        motionwright_service::StudioService::open(directory.join("studio.sqlite3")).unwrap();
    let project = service
        .create_project("Provider-bound font receipt control")
        .unwrap();
    let executable = directory.join("inert-cli");
    let socket = directory.join("fixture-socket");
    let session_file = directory.join("fixture-session");
    let output_root = directory.join("outputs");
    let media_root = directory.join("media");
    fs::write(
        &executable,
        b"inert source-control fixture; must never be executed",
    )
    .unwrap();
    fs::write(&socket, b"fixture socket metadata").unwrap();
    fs::write(&session_file, b"fixture session metadata").unwrap();
    fs::create_dir(&output_root).unwrap();
    fs::create_dir(&media_root).unwrap();
    let connection = ProductionConnection {
        schema: CONNECTION_SCHEMA.into(),
        executable_sha256: sha256_file(&executable).unwrap(),
        executable,
        socket,
        session_file,
        output_root,
        resource: project.resource_key(),
    };
    let identity = connection.identity().unwrap();
    let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
    let mut observed = observation();
    observed.value["data"]["mount"]["path"] = json!(&media_root);
    sync_original(&mut observed.value);
    let bound = FontMediaBinding::from_observation(identity, &media_root, observed).unwrap();
    (temp, service, project, coordinator, bound)
}

#[tokio::test]
async fn a_new_binding_preserves_unknown_prior_mutations_without_redispatch() {
    use super::super::request_digest;
    use motionwright_storage::ProductionReceiptInput;
    use std::sync::atomic::Ordering;

    for (stage, prior_mutation, outcome_known) in [
        ("outcome_unknown", true, false),
        ("dispatching", true, false),
        ("failed_known", true, true),
        ("dispatching", false, true),
    ] {
        let (_temp, service, project, coordinator, binding) = no_dispatch_fixture();
        let args = json!({"source": "font.ttf", "expected_sha256": "d".repeat(64)});
        let mut old_context = binding.receipt_context();
        old_context["binding_ref"] = json!("previous-canonical-observation");
        let original_error = Error::new(
            ErrorCode::Unavailable,
            "original fixture terminal unavailable",
        )
        .uncertain();
        let receipt = service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: "same-font-import".into(),
                request_sha256: request_digest(
                    IMPORT,
                    &json!({"args": &args, "provider_bound_font": &old_context}),
                )
                .unwrap(),
                command: IMPORT.into(),
                stage: stage.into(),
                payload: json!({
                    "mutation": prior_mutation,
                    "provider_bound_font": old_context,
                    "error": original_error,
                }),
            })
            .unwrap();
        let bound = BoundFontCoordinator {
            coordinator: &coordinator,
            binding,
        };
        let error = bound
            .execute(
                project.id,
                &project.stamp(),
                "same-font-import",
                IMPORT,
                args,
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{stage}/{prior_mutation}");
        assert_eq!(
            error.outcome_known, outcome_known,
            "{stage}/{prior_mutation}"
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            service
                .latest_production_receipt(project.id, "same-font-import")
                .unwrap()
                .unwrap(),
            receipt
        );
        assert_eq!(
            service.production_receipts(project.id, 10).unwrap().len(),
            1
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }
}

#[tokio::test]
async fn an_old_completed_binding_cannot_mint_current_evidence_or_trigger_transport() {
    use super::super::request_digest;
    use motionwright_storage::ProductionReceiptInput;
    use std::sync::atomic::Ordering;

    let (_temp, service, project, coordinator, binding) = no_dispatch_fixture();
    let args = json!({});
    let context = binding.receipt_context();
    let receipt = service
        .append_production_receipt(ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "completed-font-inspect".into(),
            request_sha256: request_digest(
                INSPECT,
                &json!({"args": &args, "provider_bound_font": &context}),
            )
            .unwrap(),
            command: INSPECT.into(),
            stage: "completed".into(),
            payload: json!({
                "result": completed(INSPECT)["result"],
                "broker_request_id": OUTER,
                "provider_bound_font": context,
            }),
        })
        .unwrap();
    let bound = BoundFontCoordinator {
        coordinator: &coordinator,
        binding,
    };
    let error = bound
        .execute(
            project.id,
            &project.stamp(),
            "completed-font-inspect",
            INSPECT,
            args,
            false,
        )
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(error.outcome_known);
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        service
            .latest_production_receipt(project.id, "completed-font-inspect")
            .unwrap()
            .unwrap(),
        receipt
    );
    assert_eq!(
        service.production_receipts(project.id, 10).unwrap().len(),
        1
    );
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[cfg(unix)]
fn install_response_fixture(
    coordinator: &mut ProductionCoordinator,
    binding: &mut BoundMediaContext,
    original: &Value,
    gate: Option<&Path>,
) -> PathBuf {
    use super::super::sha256_file;
    use std::{fs, os::unix::fs::PermissionsExt};

    fn quote(value: &str) -> String {
        format!("'{}'", value.replace('\'', "'\\''"))
    }
    let directory = coordinator.client.connection.executable.parent().unwrap();
    let counter = directory.join("fixture-invocations");
    let wait = gate
        .map(|path| {
            format!(
                "while [ ! -f {} ]; do sleep 0.01; done\n",
                quote(&path.to_string_lossy())
            )
        })
        .unwrap_or_default();
    let script = format!(
        "#!/bin/sh\nprintf 'invoked\\n' >> {}\n{}printf '%s\\n' {}\n",
        quote(&counter.to_string_lossy()),
        wait,
        quote(&serde_json::to_string(original).unwrap())
    );
    fs::write(&coordinator.client.connection.executable, script).unwrap();
    fs::set_permissions(
        &coordinator.client.connection.executable,
        fs::Permissions::from_mode(0o700),
    )
    .unwrap();
    coordinator.client.connection.executable_sha256 =
        sha256_file(&coordinator.client.connection.executable).unwrap();
    binding.connection_identity = coordinator.client.connection.identity().unwrap();
    counter
}

#[cfg(unix)]
fn fresh_fixture_binding(
    coordinator: &ProductionCoordinator,
    media_root: &Path,
) -> FontMediaBinding {
    let mut observed = observation();
    observed.value["data"]["mount"]["path"] = json!(media_root);
    observed.value["data"]["binding_ref"] = json!("fresh-canonical-fixture-reference");
    sync_original(&mut observed.value);
    FontMediaBinding::from_observation(
        coordinator.client.connection.identity().unwrap(),
        media_root,
        observed,
    )
    .unwrap()
}

#[cfg(unix)]
#[tokio::test]
async fn rejected_original_font_result_preserves_certainty_after_sqlite_reopen_and_new_binding() {
    use std::{fs, sync::atomic::Ordering};
    for cause in [
        "inner-generation",
        "expired-admission",
        "outer-conflicting-error",
    ] {
        let (temp, service, project, mut coordinator, mut binding) = no_dispatch_fixture();
        let media_root = binding.root.clone();
        let mut original = completed(IMPORT)["result"]["original_envelope"].clone();
        match cause {
            "inner-generation" => {
                original["data"]["result"]["execution"]["provenance"]["provider_generation"] =
                    json!(10);
            }
            "expired-admission" => {
                original["data"]["admission_current"] = json!(false);
                original["data"]["admission_state"] = json!("expired");
            }
            _ => {
                original["error"] = json!(Error::new(ErrorCode::Conflict, "contradictory fixture"));
            }
        }
        let counter = install_response_fixture(&mut coordinator, &mut binding.0, &original, None);
        let connection = coordinator.client.connection.clone();
        let bound = BoundFontCoordinator {
            coordinator: &coordinator,
            binding,
        };
        let first = bound
            .execute(
                project.id,
                &project.stamp(),
                "rejected-original-font",
                IMPORT,
                json!({}),
                false,
            )
            .await
            .unwrap_err();
        let historical = cause == "expired-admission";
        assert_eq!(first.outcome_known, historical, "{cause}");
        let receipts = service.production_receipts(project.id, 10).unwrap();
        assert_eq!(receipts.len(), 2);
        assert!(!receipts.iter().any(|receipt| receipt.stage == "completed"));
        let terminal = service
            .latest_production_receipt(project.id, "rejected-original-font")
            .unwrap()
            .unwrap();
        assert_eq!(
            terminal.stage,
            if historical {
                "failed_known"
            } else {
                "outcome_unknown"
            }
        );
        assert_eq!(
            terminal.payload["font_qualification"],
            if historical { "historical" } else { "rejected" }
        );
        if historical {
            assert_eq!(terminal.payload["native_completion"], "confirmed");
            assert_eq!(terminal.payload["rejected_scope"], "current_admission");
        }
        assert_eq!(terminal.payload["result"]["original_envelope"], original);
        assert_eq!(terminal.payload["error"], json!(first));
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        drop(bound);
        drop(coordinator);
        drop(service);

        let reopened =
            motionwright_service::StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let fresh = fresh_fixture_binding(&recovered, &media_root);
        let bound = BoundFontCoordinator {
            coordinator: &recovered,
            binding: fresh,
        };
        let repeated = bound
            .execute(
                project.id,
                &project.stamp(),
                "rejected-original-font",
                IMPORT,
                json!({}),
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(repeated.code, ErrorCode::Conflict);
        assert_eq!(repeated.outcome_known, historical, "{cause}");
        assert_eq!(recovered.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }
}

#[cfg(unix)]
#[tokio::test]
async fn successful_font_transport_keeps_original_core_fields_and_admitted_replay_known() {
    use std::{fs, sync::atomic::Ordering};
    let (_temp, service, project, mut coordinator, mut binding) = no_dispatch_fixture();
    let mut original = completed(IMPORT)["result"]["original_envelope"].clone();
    original["opaque_fixture_extension"] = json!({"retained": [1, "original", null]});
    let counter = install_response_fixture(&mut coordinator, &mut binding.0, &original, None);
    let bound = BoundFontCoordinator {
        coordinator: &coordinator,
        binding,
    };
    let result = bound
        .execute(
            project.id,
            &project.stamp(),
            "admitted-original-font",
            IMPORT,
            json!({}),
            false,
        )
        .await
        .unwrap();
    assert_eq!(result["result"]["data"], original["data"]["result"]["data"]);
    assert_eq!(
        result["result"]["provider_bound"]["original"]["original_envelope"],
        original
    );
    let receipt = service
        .latest_production_receipt(project.id, "admitted-original-font")
        .unwrap()
        .unwrap();
    assert_eq!(receipt.stage, "completed");
    assert_eq!(receipt.payload["font_qualification"], "admitted");
    assert_eq!(receipt.payload["result"]["original_envelope"], original);
    assert_eq!(
        receipt.payload["result"]["data"]["result"],
        original["data"]["result"]
    );
    let repeated = bound
        .execute(
            project.id,
            &project.stamp(),
            "admitted-original-font",
            IMPORT,
            json!({}),
            false,
        )
        .await
        .unwrap_err();
    assert_eq!(repeated.code, ErrorCode::Conflict);
    assert!(repeated.outcome_known);
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
    assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
    assert_eq!(
        service.production_receipts(project.id, 10).unwrap().len(),
        2
    );
}

#[cfg(unix)]
#[tokio::test]
async fn failed_font_qualification_and_original_survive_terminal_append_failure() {
    use motionwright_storage::Store;
    use std::{fs, sync::atomic::Ordering, time::Duration};
    for mutation in [true, false] {
        let (temp, service, project, mut coordinator, mut binding) = no_dispatch_fixture();
        let media_root = binding.root.clone();
        let command = if mutation { IMPORT } else { INSPECT };
        let mut original = completed(command)["result"]["original_envelope"].clone();
        original["data"]["result"]["execution"]["provenance"]["provider_generation"] = json!(10);
        let gate = temp.path().join("release-terminal");
        let counter =
            install_response_fixture(&mut coordinator, &mut binding.0, &original, Some(&gate));
        let connection = coordinator.client.connection.clone();
        let bound = BoundFontCoordinator {
            coordinator: &coordinator,
            binding,
        };
        let stamp = project.stamp();
        let pending = bound.execute(
            project.id,
            &stamp,
            "rejected-font-append-failure",
            command,
            json!({}),
            mutation,
        );
        let replace_store = async {
            tokio::time::timeout(Duration::from_secs(5), async {
                loop {
                    if fs::read(&counter).ok().as_deref() == Some(&b"invoked\n"[..]) {
                        break;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            })
            .await
            .expect("original fixture did not reach its reply gate");
            let receipts = service.production_receipts(project.id, 10).unwrap();
            assert_eq!(receipts.len(), 1);
            assert_eq!(receipts[0].stage, "dispatching");
            let replacement = Store::open(temp.path().join("empty-store.sqlite3")).unwrap();
            *service.store().lock() = replacement;
            fs::write(&gate, b"release-original-terminal\n").unwrap();
            receipts
        };
        let (result, dispatch_receipts) = tokio::join!(pending, replace_store);
        let error = result.unwrap_err();
        assert_eq!(
            error.message,
            "Bound media Driver result does not match its authenticated execution"
        );
        assert_eq!(error.outcome_known, !mutation);
        let snapshot = coordinator.private_receipt_custody_snapshot();
        assert_eq!(snapshot["entries"].as_array().unwrap().len(), 1);
        let entry = &snapshot["entries"][0];
        assert_eq!(entry["persistence"], "unconfirmed");
        let attempted = &entry["attempted_receipt"];
        assert_eq!(
            attempted["stage"],
            if mutation {
                "outcome_unknown"
            } else {
                "failed_known"
            }
        );
        assert_eq!(attempted["payload"]["error"], json!(error));
        assert_eq!(
            attempted["payload"]["result"]["original_envelope"],
            original
        );
        assert_eq!(attempted["payload"]["font_qualification"], "rejected");
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        drop(bound);
        drop(coordinator);
        drop(service);

        let reopened =
            motionwright_service::StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            dispatch_receipts
        );
        let recovered = ProductionCoordinator::new(reopened, connection).unwrap();
        let fresh = fresh_fixture_binding(&recovered, &media_root);
        let bound = BoundFontCoordinator {
            coordinator: &recovered,
            binding: fresh,
        };
        let repeated = bound
            .execute(
                project.id,
                &project.stamp(),
                "rejected-font-append-failure",
                command,
                json!({}),
                mutation,
            )
            .await
            .unwrap_err();
        assert_eq!(repeated.code, ErrorCode::Conflict);
        assert_eq!(repeated.outcome_known, !mutation);
        assert_eq!(recovered.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
    }
}

// Closed audio transport controls; no PCM decoding, resampling, or native Driver.
fn audio_observation_fixture(media_root: &Path, reference: &str) -> BrokerResult {
    let mut observed = observation();
    observed.value["data"]["provider"] = json!(AUDIO_PROVIDER);
    observed.value["data"]["mount"]["path"] = json!(media_root);
    observed.value["data"]["binding_ref"] = json!(reference);
    observed.value["data"]["pair"] = json!({
        "version":1,"name":"media-ro-output-rw",
        "media":{"root":"media","path":media_root,"read":true,"write":false,"execute":false,
            "instance_identity":"source-only-media-instance"},
        "output":{"root":"output","path":media_root.with_file_name("output"),
            "read":true,"write":true,"execute":false,
            "instance_identity":"source-only-output-instance"}
    });
    sync_original(&mut observed.value);
    observed
}
fn audio_binding_fixture() -> AudioMediaBinding {
    AudioMediaBinding::from_observation(
        "b".repeat(64),
        &root(),
        audio_observation_fixture(&root(), "fixture-live-reference"),
    )
    .expect("controlled authenticated audio mount matches")
}
fn audio_binding_for(
    coordinator: &ProductionCoordinator,
    media_root: &Path,
    reference: &str,
) -> AudioMediaBinding {
    let mut observed = audio_observation_fixture(media_root, reference);
    observed.value["data"]["pair"]["output"]["path"] =
        json!(coordinator.client.connection().output_root);
    sync_original(&mut observed.value);
    AudioMediaBinding::from_observation(
        coordinator.client.connection.identity().unwrap(),
        media_root,
        observed,
    )
    .expect("controlled audio mount belongs to this fixture connection")
}
fn completed_audio_fixture() -> Value {
    let mut value = completed(AUDIO_DERIVE);
    value["result"]["data"]["result"]["execution"] =
        execution(AUDIO_PROVIDER, SourceKind::Driver, Some(9));
    value["result"]["data"]["pair"] =
        audio_observation_fixture(&root(), "fixture-live-reference").value["data"]["pair"].clone();
    sync_original(&mut value["result"]);
    let binding = audio_binding_fixture();
    let payload = value["receipt"]["payload"].as_object_mut().unwrap();
    payload.remove("provider_bound_font");
    payload.insert(binding.receipt_key().to_owned(), binding.receipt_context());
    payload.insert(binding.qualification_key().to_owned(), json!("admitted"));
    payload.insert("mutation".to_owned(), json!(true));
    value
}

#[test]
fn audio_mount_binding_rejects_cross_profile_and_mutable_media_mounts() {
    let binding = audio_binding_fixture();
    assert_eq!(binding.receipt_key(), "provider_bound_audio_derive");
    assert_eq!(binding.qualification_key(), "audio_derive_qualification");
    assert_eq!(
        binding.receipt_context()["schema"],
        "motionwright.provider-bound-audio-derive/1"
    );
    assert!(AudioMediaBinding::from_observation("b".repeat(64), &root(), observation()).is_err());
    assert!(
        FontMediaBinding::from_observation(
            "b".repeat(64),
            &root(),
            audio_observation_fixture(&root(), "fixture-live-reference")
        )
        .is_err()
    );
    for (pointer, replacement) in [
        ("/data/provider", json!("driver:other")),
        ("/data/mount/root", json!("outputs")),
        ("/data/mount/read", json!(false)),
        ("/data/mount/write", json!(true)),
        ("/data/mount/execute", json!(true)),
    ] {
        let mut observed = audio_observation_fixture(&root(), "fixture-live-reference");
        *observed.value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut observed.value);
        assert!(
            AudioMediaBinding::from_observation("b".repeat(64), &root(), observed).is_err(),
            "{pointer}"
        );
    }
}

#[test]
fn audio_derive_profile_is_mutating_closed_and_has_its_typed_budget() {
    let audio = audio_binding_fixture();
    let font = binding();
    assert!(audio.mutation_for(AUDIO_DERIVE).unwrap());
    for command in [
        INSPECT,
        IMPORT,
        "driver.mlt-video.render.start",
        "driver.audio-analysis.voice.align",
        "providers.execute_bound",
        "doctor",
    ] {
        assert_eq!(
            audio.mutation_for(command).unwrap_err().code,
            ErrorCode::Unsupported,
            "{command}"
        );
        assert!(audio.request(command, json!({})).is_err(), "{command}");
        let original = BrokerResult {
            request_id: OUTER.into(),
            value: completed_audio_fixture()["result"].clone(),
        };
        assert_eq!(
            audio
                .qualify_result(&original, command, true)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported,
            "a true mutation flag cannot bypass profile closure: {command}"
        );
    }
    assert_eq!(
        font.mutation_for(AUDIO_DERIVE).unwrap_err().code,
        ErrorCode::Unsupported
    );
    assert!(font.request(AUDIO_DERIVE, json!({})).is_err());
    let request = audio.request(AUDIO_DERIVE, json!({})).unwrap();
    assert_eq!(request["request"]["command"], AUDIO_DERIVE);
    assert_eq!(request["request"]["backend"], AUDIO_PROVIDER);
    assert_eq!(request["request"]["dry_run"], false);
    assert_eq!(
        audio
            .transport_budget_secs(EXECUTE, &request, true)
            .unwrap(),
        330
    );
    assert!(
        audio
            .transport_budget_secs(EXECUTE, &request, false)
            .is_err(),
        "a mutating profile cannot be downgraded"
    );
    let font_request = font.request(IMPORT, json!({})).unwrap();
    assert_eq!(
        font.transport_budget_secs(EXECUTE, &font_request, true)
            .unwrap(),
        45
    );
    assert!(
        audio
            .transport_budget_secs("doctor", &request, true)
            .is_err()
    );
    for (pointer, replacement) in [
        ("/binding_ref", json!("foreign-reference")),
        ("/request/backend", json!(PROVIDER)),
        ("/request/command", json!(INSPECT)),
        ("/request/dry_run", json!(true)),
        ("/request", json!([])),
    ] {
        let mut malformed = request.clone();
        *malformed.pointer_mut(pointer).unwrap() = replacement;
        assert!(
            audio
                .transport_budget_secs(EXECUTE, &malformed, true)
                .is_err(),
            "{pointer}"
        );
    }
    let mut outer_extra = request.clone();
    outer_extra["timeout_ms"] = json!(999999);
    assert!(
        audio
            .transport_budget_secs(EXECUTE, &outer_extra, true)
            .is_err()
    );
    let mut inner_extra = request;
    inner_extra["request"]["timeout_ms"] = json!(999999);
    assert!(
        audio
            .transport_budget_secs(EXECUTE, &inner_extra, true)
            .is_err()
    );
}

#[test]
fn audio_result_requires_its_authenticated_driver_and_generation() {
    let binding = audio_binding_fixture();
    let original = completed_audio_fixture();
    let admitted = binding
        .normalize_completed(original.clone(), AUDIO_DERIVE, true)
        .unwrap();
    assert_eq!(
        admitted["result"]["execution"]["provenance"]["provider"],
        AUDIO_PROVIDER
    );
    assert_eq!(
        admitted["result"]["execution"]["provenance"]["untrusted_metadata"],
        true
    );
    assert_eq!(
        admitted["result"]["provider_bound"]["original"]["original_envelope"],
        original["result"]["original_envelope"]
    );
    for (pointer, replacement) in [
        (
            "/result/data/result/execution/provenance/provider_generation",
            json!(10),
        ),
        (
            "/result/data/result/execution/provenance/provider",
            json!(PROVIDER),
        ),
        (
            "/result/data/result/execution/provenance/execution_provider",
            json!(PROVIDER),
        ),
        (
            "/result/data/result/execution/provenance/source",
            json!(SourceKind::Builtin),
        ),
        ("/result/data/result/execution/backend", json!(PROVIDER)),
        ("/result/data/result/command", json!(IMPORT)),
    ] {
        let mut rejected = original.clone();
        *rejected.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut rejected["result"]);
        let error = binding
            .normalize_completed(rejected, AUDIO_DERIVE, true)
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::BackendFailed, "{pointer}");
        assert!(!error.outcome_known, "{pointer}");
    }
}

#[test]
fn audio_historical_completion_and_replay_do_not_mint_current_admission() {
    let binding = audio_binding_fixture();
    let mut historical = completed_audio_fixture();
    historical["result"]["data"]["admission_current"] = json!(false);
    historical["result"]["data"]["admission_state"] = json!("expired");
    sync_original(&mut historical["result"]);
    let expired = binding
        .normalize_completed(historical, AUDIO_DERIVE, true)
        .unwrap_err();
    assert_eq!(expired.code, ErrorCode::StaleReference);
    assert!(expired.outcome_known);
    let mut replay = completed_audio_fixture();
    replay["replayed"] = json!(true);
    let reused = binding
        .normalize_completed(replay, AUDIO_DERIVE, true)
        .unwrap_err();
    assert_eq!(reused.code, ErrorCode::Conflict);
    assert!(reused.outcome_known);
}

#[tokio::test]
async fn audio_prior_unknown_survives_new_binding_without_redispatch() {
    use super::super::request_digest;
    use motionwright_storage::ProductionReceiptInput;
    use std::sync::atomic::Ordering;
    for (stage, expected_known) in [
        ("outcome_unknown", false),
        ("dispatching", false),
        ("failed_known", true),
    ] {
        let (_temp, service, project, coordinator, font_binding) = no_dispatch_fixture();
        let media_root = font_binding.root.clone();
        let previous = audio_binding_for(
            &coordinator,
            &media_root,
            "previous-audio-fixture-reference",
        );
        let fresh = audio_binding_for(&coordinator, &media_root, "fresh-audio-fixture-reference");
        let args = json!({});
        let previous_context = previous.receipt_context();
        let mut payload =
            json!({"mutation": true, "provider_bound_audio_derive": previous_context});
        if stage != "dispatching" {
            let error = Error::new(ErrorCode::Unavailable, "controlled original audio terminal");
            payload["error"] = json!(if expected_known {
                error
            } else {
                error.uncertain()
            });
        }
        let receipt = service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: "same-audio-derive".into(),
                request_sha256: request_digest(
                    AUDIO_DERIVE,
                    &json!({"args": &args, "provider_bound_audio_derive": &previous_context}),
                )
                .unwrap(),
                command: AUDIO_DERIVE.into(),
                stage: stage.into(),
                payload,
            })
            .unwrap();
        let bound = BoundAudioDerivativeCoordinator {
            coordinator: &coordinator,
            binding: fresh,
        };
        let error = bound
            .derive(project.id, &project.stamp(), "same-audio-derive", args)
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict, "{stage}");
        assert_eq!(error.outcome_known, expected_known, "{stage}");
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(
            service
                .latest_production_receipt(project.id, "same-audio-derive")
                .unwrap()
                .unwrap(),
            receipt
        );
        assert_eq!(
            service.production_receipts(project.id, 10).unwrap().len(),
            1
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }
}

#[cfg(unix)]
#[tokio::test]
async fn rejected_audio_original_remains_unknown_after_reopen_without_redispatch() {
    use std::{fs, sync::atomic::Ordering};
    for cause in ["inner-generation", "outer-conflicting-error"] {
        let (temp, service, project, mut coordinator, font_binding) = no_dispatch_fixture();
        let media_root = font_binding.root.clone();
        let mut binding = audio_binding_for(&coordinator, &media_root, "fixture-live-reference");
        let mut original = completed_audio_fixture()["result"]["original_envelope"].clone();
        original["data"]["pair"] = json!(&binding.audio_pair);
        match cause {
            "inner-generation" => {
                original["data"]["result"]["execution"]["provenance"]["provider_generation"] =
                    json!(10);
            }
            _ => {
                original["error"] = json!(Error::new(
                    ErrorCode::Conflict,
                    "controlled contradictory Core terminal"
                ));
            }
        }
        let counter = install_response_fixture(&mut coordinator, &mut binding.0, &original, None);
        let connection = coordinator.client.connection.clone();
        let bound = BoundAudioDerivativeCoordinator {
            coordinator: &coordinator,
            binding,
        };
        let first = bound
            .derive(
                project.id,
                &project.stamp(),
                "rejected-original-audio",
                json!({}),
            )
            .await
            .unwrap_err();
        assert_eq!(first.code, ErrorCode::BackendFailed, "{cause}");
        assert!(!first.outcome_known, "{cause}");
        let receipts = service.production_receipts(project.id, 10).unwrap();
        assert_eq!(receipts.len(), 2);
        assert!(!receipts.iter().any(|receipt| receipt.stage == "completed"));
        let terminal = service
            .latest_production_receipt(project.id, "rejected-original-audio")
            .unwrap()
            .unwrap();
        assert_eq!(terminal.command, AUDIO_DERIVE);
        assert_eq!(terminal.stage, "outcome_unknown");
        assert_eq!(terminal.payload["audio_derive_qualification"], "rejected");
        assert_eq!(terminal.payload["mutation"], true);
        assert_eq!(terminal.payload["result"]["original_envelope"], original);
        assert_eq!(terminal.payload["error"], json!(first));
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        drop(bound);
        drop(coordinator);
        drop(service);
        let reopened =
            motionwright_service::StudioService::open(temp.path().join("studio.sqlite3")).unwrap();
        let recovered = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let fresh = audio_binding_for(&recovered, &media_root, "fresh-audio-after-reopen");
        let bound = BoundAudioDerivativeCoordinator {
            coordinator: &recovered,
            binding: fresh,
        };
        let repeated = bound
            .derive(
                project.id,
                &project.stamp(),
                "rejected-original-audio",
                json!({}),
            )
            .await
            .unwrap_err();
        assert_eq!(repeated.code, ErrorCode::Conflict);
        assert!(!repeated.outcome_known, "{cause}");
        assert_eq!(recovered.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
        assert_eq!(
            reopened.production_receipts(project.id, 10).unwrap(),
            receipts
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }
}

#[test]
fn audio_pair_admission_requires_both_roles_paths_and_distinct_native_instances() {
    let original = audio_observation_fixture(&root(), "fixture-live-reference");
    let binding = audio_binding_fixture();
    assert_eq!(
        binding
            .owner_pair_identities(&root(), &root().with_file_name("output"))
            .unwrap(),
        ("source-only-media-instance", "source-only-output-instance"),
    );
    assert!(
        binding
            .owner_pair_identities(&root(), &root().with_file_name("foreign"))
            .is_err()
    );
    for (pointer, replacement) in [
        ("/data/pair/version", json!(2)),
        ("/data/pair/name", json!("another-pair")),
        ("/data/pair/media/root", json!("output")),
        (
            "/data/pair/media/path",
            json!(root().with_file_name("foreign")),
        ),
        ("/data/pair/media/read", json!(false)),
        ("/data/pair/media/write", json!(true)),
        ("/data/pair/media/execute", json!(true)),
        ("/data/pair/output/root", json!("media")),
        ("/data/pair/output/path", json!(root())),
        ("/data/pair/output/read", json!(false)),
        ("/data/pair/output/write", json!(false)),
        ("/data/pair/output/execute", json!(true)),
        ("/data/pair/media/instance_identity", json!("")),
        (
            "/data/pair/output/instance_identity",
            json!("source-only-media-instance"),
        ),
        (
            "/data/pair/output/instance_identity",
            json!("bad\nidentity"),
        ),
    ] {
        let mut changed = BrokerResult {
            request_id: original.request_id.clone(),
            value: original.value.clone(),
        };
        *changed.value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut changed.value);
        assert!(
            AudioMediaBinding::from_observation("b".repeat(64), &root(), changed).is_err(),
            "{pointer}"
        );
    }
    let mut missing = audio_observation_fixture(&root(), "fixture-live-reference");
    missing.value["data"]
        .as_object_mut()
        .unwrap()
        .remove("pair");
    sync_original(&mut missing.value);
    assert!(AudioMediaBinding::from_observation("b".repeat(64), &root(), missing).is_err());
    let mut extra = audio_observation_fixture(&root(), "fixture-live-reference");
    extra.value["data"]["pair"]["output"]["borrowed_grant"] = json!(true);
    sync_original(&mut extra.value);
    assert!(AudioMediaBinding::from_observation("b".repeat(64), &root(), extra).is_err());
}

#[test]
fn audio_pair_revalidation_keeps_original_reference_instances_and_expiry() {
    let binding = audio_binding_fixture();
    let request = binding.revalidation_request();
    assert_eq!(
        request,
        json!({
            "provider":AUDIO_PROVIDER,"root":"media","binding_ref":"fixture-live-reference",
        })
    );
    assert!(request.get("pair").is_none());
    assert!(request.get("profile").is_none());
    let mut current = audio_observation_fixture(&root(), "fixture-live-reference");
    current.value["data"]["admission_ttl_ms"] = json!(1);
    sync_original(&mut current.value);
    binding.qualify_revalidation(current).unwrap();
    let ttl = binding.original_observation.value["data"]["admission_ttl_ms"]
        .as_u64()
        .unwrap();
    for (pointer, replacement) in [
        (
            "/data/binding_ref",
            json!("new-reference-is-not-revalidation"),
        ),
        ("/data/provider_generation", json!(10)),
        ("/data/connection", json!("different-provider-connection")),
        ("/data/admission_ttl_ms", json!(0)),
        ("/data/admission_ttl_ms", json!(ttl + 1)),
        (
            "/data/pair/media/instance_identity",
            json!("replaced-media-instance"),
        ),
        (
            "/data/pair/output/instance_identity",
            json!("replaced-output-instance"),
        ),
        (
            "/data/pair/output/path",
            json!(root().with_file_name("foreign-output")),
        ),
    ] {
        let mut changed = audio_observation_fixture(&root(), "fixture-live-reference");
        *changed.value.pointer_mut(pointer).unwrap() = replacement;
        sync_original(&mut changed.value);
        assert!(binding.qualify_revalidation(changed).is_err(), "{pointer}");
    }
    assert_eq!(binding.reference, "fixture-live-reference");
    assert_eq!(
        binding.original_observation.value["data"]["admission_ttl_ms"],
        json!(ttl)
    );
}

#[test]
fn audio_completed_pair_closing_is_known_history_and_crossed_pair_is_unknown() {
    let binding = audio_binding_fixture();
    let mut closing = completed_audio_fixture();
    closing["result"]["data"]["admission_current"] = json!(false);
    closing["result"]["data"]["admission_state"] = json!("closing");
    sync_original(&mut closing["result"]);
    let error = binding
        .normalize_completed(closing, AUDIO_DERIVE, true)
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::StaleReference);
    assert!(error.outcome_known);

    let mut crossed = completed_audio_fixture();
    crossed["result"]["data"]["pair"]["output"]["instance_identity"] = json!("foreign-instance");
    sync_original(&mut crossed["result"]);
    let error = binding
        .normalize_completed(crossed, AUDIO_DERIVE, true)
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert!(!error.outcome_known);

    let font = super::tests::binding();
    let mut single = completed(IMPORT);
    single["result"]["data"]["admission_current"] = json!(false);
    single["result"]["data"]["admission_state"] = json!("closing");
    sync_original(&mut single["result"]);
    let error = font.normalize_completed(single, IMPORT, true).unwrap_err();
    assert_eq!(error.code, ErrorCode::BackendFailed);
    assert!(!error.outcome_known);
}

#[test]
fn audio_pair_readback_preserves_full_original_error_and_successful_core_data() {
    let original = Error::new(ErrorCode::Timeout, "first\nsecond\toriginal")
        .recipe_progress(2, "same-step")
        .uncertain();
    let expected = json!(&original);
    let failed: NativeResult<BrokerResult> = Err(original);
    assert_eq!(
        audio_pair_readback(&failed),
        json!({"original_error":expected})
    );
    let observed = audio_observation_fixture(&root(), "fixture-live-reference");
    let expected = json!({
        "request_id":observed.request_id,"original_result":observed.value,
    });
    let returned = Ok(observed);
    assert_eq!(audio_pair_readback(&returned), expected);
    assert_eq!(returned.unwrap().value, expected["original_result"]);
}

#[tokio::test]
async fn audio_pair_completed_readback_slot_cannot_overwrite_or_repeat_observation() {
    use std::sync::atomic::Ordering;
    let (_temp, _service, _project, coordinator, font) = no_dispatch_fixture();
    let binding = audio_binding_for(&coordinator, &font.root, "fixture-live-reference");
    let bound = BoundAudioDerivativeCoordinator {
        coordinator: &coordinator,
        binding,
    };
    let original = audio_pair_readback(&Ok(audio_observation_fixture(
        &font.root,
        "fixture-live-reference",
    )));
    let mut readbacks = AudioPairReadbacks {
        admission: None,
        before_capture: Some(original.clone()),
        after_capture: None,
    };
    let error = bound
        .revalidate_capture_pair(AudioPairCaptureStage::BeforeCapture, &mut readbacks)
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::Conflict);
    assert!(error.outcome_known);
    assert_eq!(readbacks.before_capture.as_ref(), Some(&original));
    assert!(readbacks.after_capture.is_none());
    assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
}
