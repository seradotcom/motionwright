//! Tests the exact helper called by the desktop Tauri workflow command.
//! The bounded CLI below is a transport/effect sentinel, not a real Broker,
//! installed WebView, native workflow recorder, or production permission proof.
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
mod effect_grants;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/workflow_native.rs"]
mod workflow_native;

#[test]
fn workflow_wire_requires_original_request_identity_and_rejects_unknown_fields() {
    use serde_json::json;
    use workflow_native::WorkflowActionRequest;
    let mut value = json!({
        "project_id": uuid::Uuid::now_v7(),
        "generation": uuid::Uuid::now_v7(),
        "revision": 0,
        "action": "record_start",
        "args": {}
    });
    assert!(serde_json::from_value::<WorkflowActionRequest>(value.clone()).is_err());
    value["request_id"] = json!("original-workflow-request");
    assert!(serde_json::from_value::<WorkflowActionRequest>(value.clone()).is_ok());
    value["actor"] = json!("caller-cannot-assert-authority");
    assert!(serde_json::from_value::<WorkflowActionRequest>(value).is_err());
}

#[cfg(unix)]
mod routed {
    use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
    use super::workflow_native::{
        WorkflowAction, WorkflowActionRequest, WorkflowReceiptObservation, execute_workflow_action,
    };
    use motionwright_domain::Project;
    use motionwright_native::production::{ProductionConnection, ProductionReceiptCustody};
    use motionwright_service::StudioService;
    use semwright_native_sdk::types::{Envelope, Execution, InvocationProvenance, SourceKind};
    use semwright_native_sdk::{Error, ErrorCode};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::{
        fs,
        os::unix::fs::PermissionsExt,
        path::{Path, PathBuf},
    };
    use tempfile::TempDir;

    enum Reply {
        Success,
        MalformedAfterEffect,
        KnownRejection,
        HoldAfterEffect,
        GuardRefusedSuccess,
        GuardRefusedKnown,
        GuardRefusedUnknown,
    }

    struct Fixture {
        _temp: TempDir,
        database: PathBuf,
        service: StudioService,
        project: Project,
        connection: PathBuf,
        calls: PathBuf,
        effects: PathBuf,
        response_file: PathBuf,
        custody: ProductionReceiptCustody,
        grants: EffectGrantRegistry,
    }

    fn quote(path: &Path) -> String {
        format!("'{}'", path.to_str().unwrap().replace('\'', "'\\''"))
    }

    // Finite-policy canary already used by the canonical guard's tests.
    // This is synthetic data, never a real account credential.
    fn synthetic_canary() -> String {
        format!("ghp_{}", "A".repeat(24))
    }

    fn mode(path: &Path, bits: u32) {
        fs::set_permissions(path, fs::Permissions::from_mode(bits)).unwrap();
    }

    fn fixture(action: WorkflowAction, reply: Reply, foreign_resource: bool) -> Fixture {
        let temp = tempfile::tempdir().unwrap();
        let database = temp.path().join("workflow.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service.create_project("Workflow exact route").unwrap();
        let executable = temp.path().join("semwright-fixture");
        let socket = temp.path().join("broker.sock");
        let session_file = temp.path().join("session.json");
        let output_root = temp.path().join("output");
        let response_file = temp.path().join("response.json");
        let calls = temp.path().join("transport-calls");
        let effects = temp.path().join("effect-sentinel");
        fs::create_dir(&output_root).unwrap();
        mode(&output_root, 0o700);
        fs::write(&socket, []).unwrap();
        fs::write(&session_file, b"{}").unwrap();
        mode(&socket, 0o600);
        mode(&session_file, 0o600);
        let execution = Execution {
            backend: "semwright-core".into(),
            duration_ms: 1,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            provenance: Some(InvocationProvenance {
                provider: "semwright-core".into(),
                source: SourceKind::Builtin,
                provider_version: "fixture".into(),
                capability_version: "fixture".into(),
                descriptor_sha256: "ab".repeat(32),
                untrusted_metadata: false,
                catalog_revision: 1,
                execution_provider: None,
                provider_generation: None,
            }),
            dry_run: false,
        };
        let simulate_effect =
            action.mutation() && !matches!(reply, Reply::KnownRejection | Reply::GuardRefusedKnown);
        let hold_after_effect = matches!(reply, Reply::HoldAfterEffect);
        let payload = if matches!(reply, Reply::MalformedAfterEffect) {
            b"not-an-envelope-after-the-effect\n".to_vec()
        } else {
            let rejected = matches!(
                reply,
                Reply::KnownRejection | Reply::GuardRefusedKnown | Reply::GuardRefusedUnknown
            );
            serde_json::to_vec(&Envelope {
                ok: !rejected,
                request_id: "broker-assigned-request".into(),
                command: action.command().into(),
                data: (!rejected).then(|| {
                    if matches!(reply, Reply::GuardRefusedSuccess) {
                        json!({"accepted": true, "original": synthetic_canary()})
                    } else {
                        json!({"accepted": true})
                    }
                }),
                error: rejected.then(|| {
                    if matches!(reply, Reply::GuardRefusedUnknown) {
                        Error::new(ErrorCode::BackendFailed, synthetic_canary()).uncertain()
                    } else if matches!(reply, Reply::GuardRefusedKnown) {
                        Error::new(ErrorCode::ConsentRequired, synthetic_canary())
                    } else {
                        Error::new(ErrorCode::PermissionDenied, "private provider diagnostic")
                    }
                }),
                execution,
                warnings: vec![],
            })
            .unwrap()
        };
        fs::write(&response_file, payload).unwrap();
        let effect_line = if simulate_effect {
            format!("printf 'effect\\n' >> {}\n", quote(&effects))
        } else {
            String::new()
        };
        // exec keeps the waiting process at the CLI PID, so cancellation has
        // no background shell child whose pipe could outlive the route.
        let tail = if hold_after_effect {
            "exec sleep 30\n".to_owned()
        } else {
            format!("cat {}\n", quote(&response_file))
        };
        let script = format!(
            "#!/bin/sh\nprintf 'call\\n' >> {}\n{}{}",
            quote(&calls),
            effect_line,
            tail,
        );
        fs::write(&executable, &script).unwrap();
        mode(&executable, 0o700);
        let connection_value = ProductionConnection {
            schema: "motionwright-semwright-connection/1".into(),
            executable,
            executable_sha256: hex::encode(Sha256::digest(script.as_bytes())),
            socket,
            session_file,
            output_root,
            resource: if foreign_resource {
                format!("project:{}", uuid::Uuid::now_v7())
            } else {
                project.resource_key()
            },
        };
        let connection = temp.path().join("connection.json");
        fs::write(&connection, serde_json::to_vec(&connection_value).unwrap()).unwrap();
        mode(&connection, 0o600);
        Fixture {
            _temp: temp,
            database,
            service,
            project,
            connection,
            calls,
            effects,
            response_file,
            custody: ProductionReceiptCustody::default(),
            grants: EffectGrantRegistry::default(),
        }
    }

    fn request(
        project: &Project,
        grants: &EffectGrantRegistry,
        key: &str,
        action: WorkflowAction,
        with_grant: bool,
    ) -> WorkflowActionRequest {
        let effect_grant = with_grant.then(|| {
            grants
                .issue(
                    EffectKind::WorkflowMutation,
                    EffectScope::project(project.id, project.generation, project.revision),
                    action.command(),
                )
                .unwrap()
                .token
        });
        WorkflowActionRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: key.into(),
            action,
            effect_grant,
            args: json!({"capture_values": false}),
        }
    }

    fn count(path: &Path) -> usize {
        match fs::read_to_string(path) {
            Ok(text) => text.lines().count(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => 0,
            Err(error) => panic!("Counter read failed: {error}"),
        }
    }

    fn observed_stage(value: &WorkflowReceiptObservation) -> &str {
        match value {
            WorkflowReceiptObservation::Recorded { identity } => &identity.stage,
            other => panic!("Missing canonical receipt observation: {other:?}"),
        }
    }

    #[tokio::test]
    async fn ambiguous_route_reopens_and_refuses_same_key_without_second_effect() {
        let f = fixture(
            WorkflowAction::RecordStart,
            Reply::MalformedAfterEffect,
            false,
        );
        let key = "workflow-original-unknown";
        let original_request = request(
            &f.project,
            &f.grants,
            key,
            WorkflowAction::RecordStart,
            true,
        );
        let consumed_grant = original_request.effect_grant;
        let original = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            original_request,
        )
        .await
        .unwrap_err();
        assert_eq!(original.code, ErrorCode::BackendFailed);
        assert!(!original.outcome_known);
        assert_eq!(original.request_id.as_deref(), Some(key));
        assert_eq!(observed_stage(&original.receipt), "outcome_unknown");
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);
        let prior = f.service.production_receipts(f.project.id, 20).unwrap();
        assert_eq!(prior.len(), 2);
        assert_eq!(prior[0].request_id, key);
        assert!(
            !serde_json::to_string(&original)
                .unwrap()
                .contains("not-an-envelope")
        );

        drop(f.service);
        let reopened = StudioService::open(&f.database).unwrap();
        assert_eq!(
            reopened.production_receipts(f.project.id, 20).unwrap(),
            prior
        );
        let mut consumed = request(
            &f.project,
            &f.grants,
            key,
            WorkflowAction::RecordStart,
            false,
        );
        consumed.effect_grant = consumed_grant;
        let consumed = execute_workflow_action(
            &reopened,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            consumed,
        )
        .await
        .unwrap_err();
        assert_eq!(consumed.code, ErrorCode::PermissionDenied);
        assert!(consumed.outcome_known);
        assert_eq!(
            observed_stage(&consumed.receipt),
            "outcome_unknown",
            "A consumed-grant refusal must not hide the original UNKNOWN header"
        );
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);
        // Explicit fresh test grant; no UI retry or new mutation identity.
        let refused = execute_workflow_action(
            &reopened,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                key,
                WorkflowAction::RecordStart,
                true,
            ),
        )
        .await
        .unwrap_err();
        assert_eq!(refused.code, ErrorCode::Conflict);
        assert!(!refused.outcome_known);
        assert_eq!(observed_stage(&refused.receipt), "outcome_unknown");
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);

        let mut changed = request(
            &f.project,
            &f.grants,
            key,
            WorkflowAction::RecordStart,
            true,
        );
        changed.args = json!({"capture_values": true});
        let rejected = execute_workflow_action(
            &reopened,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            changed,
        )
        .await
        .unwrap_err();
        assert_eq!(rejected.code, ErrorCode::Conflict);
        assert!(
            rejected.outcome_known,
            "Changed-input refusal is a known rejection of this call"
        );
        assert_eq!(
            observed_stage(&rejected.receipt),
            "outcome_unknown",
            "A known refusal cannot resolve the original ambiguous operation"
        );
        assert_eq!(
            reopened.production_receipts(f.project.id, 20).unwrap(),
            prior
        );
        assert_eq!(reopened.project(f.project.id).unwrap(), f.project);
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);
    }

    #[tokio::test]
    async fn completed_route_replays_local_identity_and_retains_separate_broker_id() {
        let f = fixture(WorkflowAction::RecordStart, Reply::Success, false);
        let key = "workflow-original-completed";
        let first = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                key,
                WorkflowAction::RecordStart,
                true,
            ),
        )
        .await
        .unwrap();
        assert_eq!(first.request_id, key);
        assert_eq!(first.broker_request_id, "broker-assigned-request");
        assert!(!first.replayed);
        assert_eq!(observed_stage(&first.receipt), "completed");
        let before = f.service.production_receipts(f.project.id, 20).unwrap();
        drop(f.service);
        let reopened = StudioService::open(&f.database).unwrap();
        let second = execute_workflow_action(
            &reopened,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                key,
                WorkflowAction::RecordStart,
                true,
            ),
        )
        .await
        .unwrap();
        assert!(second.replayed);
        assert_eq!(second.request_id, key);
        assert_eq!(second.broker_request_id, first.broker_request_id);
        assert_eq!(second.result, first.result);
        assert_eq!(second.authority, first.authority);
        assert_eq!(
            reopened.production_receipts(f.project.id, 20).unwrap(),
            before
        );
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);
    }

    #[tokio::test]
    async fn known_native_rejection_preserves_code_without_provider_message() {
        let f = fixture(WorkflowAction::RecordStart, Reply::KnownRejection, false);
        let failure = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                "known-refusal",
                WorkflowAction::RecordStart,
                true,
            ),
        )
        .await
        .unwrap_err();
        assert_eq!(failure.code, ErrorCode::PermissionDenied);
        assert!(failure.outcome_known);
        assert_eq!(observed_stage(&failure.receipt), "failed_known");
        assert!(
            !serde_json::to_string(&failure)
                .unwrap()
                .contains("private provider diagnostic")
        );
        assert!(
            f.service
                .production_receipts(f.project.id, 20)
                .unwrap()
                .iter()
                .any(|r| r.payload.pointer("/error/message")
                    == Some(&json!("private provider diagnostic")))
        );
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 0);
    }

    #[tokio::test]
    async fn invalid_grant_and_stale_scope_are_rejected_before_dispatch() {
        let f = fixture(WorkflowAction::RecordStart, Reply::Success, false);
        let missing = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                "missing-grant",
                WorkflowAction::RecordStart,
                false,
            ),
        )
        .await
        .unwrap_err();
        assert_eq!(missing.code, ErrorCode::PermissionDenied);
        assert!(missing.outcome_known);

        let mut wrong = request(
            &f.project,
            &f.grants,
            "wrong-grant",
            WorkflowAction::RecordStop,
            true,
        );
        wrong.action = WorkflowAction::RecordStart;
        let wrong = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            wrong,
        )
        .await
        .unwrap_err();
        assert_eq!(wrong.code, ErrorCode::PermissionDenied);

        for generation_stale in [false, true] {
            let mut stale = request(
                &f.project,
                &f.grants,
                "stale-scope",
                WorkflowAction::RecordStart,
                true,
            );
            if generation_stale {
                stale.generation = uuid::Uuid::now_v7();
            } else {
                stale.revision += 1;
            }
            let stale = execute_workflow_action(
                &f.service,
                &f.grants,
                &f.custody,
                Ok(Some(f.connection.clone())),
                stale,
            )
            .await
            .unwrap_err();
            assert_eq!(stale.code, ErrorCode::StaleReference);
            assert!(stale.outcome_known);
        }
        assert_eq!(count(&f.calls), 0);
        assert_eq!(count(&f.effects), 0);
        assert!(
            f.service
                .production_receipts(f.project.id, 20)
                .unwrap()
                .is_empty()
        );
    }

    #[tokio::test]
    async fn foreign_connection_and_invalid_identity_never_dispatch() {
        let f = fixture(WorkflowAction::RecordStart, Reply::Success, true);
        let foreign = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                "foreign-resource",
                WorkflowAction::RecordStart,
                true,
            ),
        )
        .await
        .unwrap_err();
        assert_eq!(foreign.code, ErrorCode::PermissionDenied);
        for key in [
            "".to_owned(),
            " padded".into(),
            "line\nbreak".into(),
            "x".repeat(129),
        ] {
            let invalid = execute_workflow_action(
                &f.service,
                &f.grants,
                &f.custody,
                Ok(Some(f.connection.clone())),
                request(
                    &f.project,
                    &f.grants,
                    &key,
                    WorkflowAction::RecordStart,
                    false,
                ),
            )
            .await
            .unwrap_err();
            assert_eq!(invalid.code, ErrorCode::InvalidArgument);
            assert!(invalid.request_id.is_none());
        }
        assert_eq!(count(&f.calls), 0);
        assert_eq!(count(&f.effects), 0);
        assert!(
            f.service
                .production_receipts(f.project.id, 20)
                .unwrap()
                .is_empty()
        );
    }

    #[tokio::test]
    async fn cancelled_route_retains_dispatch_intent_and_refuses_after_reopen() {
        use std::time::Duration;
        let f = fixture(WorkflowAction::RecordStart, Reply::HoldAfterEffect, false);
        let key = "workflow-cancelled-original";
        let original = request(
            &f.project,
            &f.grants,
            key,
            WorkflowAction::RecordStart,
            true,
        );
        let service = f.service.clone();
        let grants = f.grants.clone();
        let custody = f.custody.clone();
        let connection = f.connection.clone();
        let task = tokio::spawn(async move {
            execute_workflow_action(&service, &grants, &custody, Ok(Some(connection)), original)
                .await
        });
        tokio::time::timeout(Duration::from_secs(5), async {
            while count(&f.effects) == 0 {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("The isolated fake effect did not execute within its budget");
        task.abort();
        assert!(task.await.unwrap_err().is_cancelled());
        let before = f.service.production_receipts(f.project.id, 20).unwrap();
        assert_eq!(before.len(), 1);
        assert_eq!(before[0].stage, "dispatching");
        assert_eq!(before[0].payload.get("mutation"), Some(&Value::Bool(true)));
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);
        drop(f.service);
        let reopened = StudioService::open(&f.database).unwrap();
        let refused = execute_workflow_action(
            &reopened,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                key,
                WorkflowAction::RecordStart,
                true,
            ),
        )
        .await
        .unwrap_err();
        assert_eq!(refused.code, ErrorCode::Conflict);
        assert!(!refused.outcome_known);
        assert_eq!(observed_stage(&refused.receipt), "dispatching");
        assert_eq!(
            reopened.production_receipts(f.project.id, 20).unwrap(),
            before
        );
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 1);
    }

    #[tokio::test]
    async fn terminal_append_refusal_keeps_original_after_helper_return_without_public_disclosure()
    {
        for reply in [
            Reply::GuardRefusedSuccess,
            Reply::GuardRefusedKnown,
            Reply::GuardRefusedUnknown,
        ] {
            let f = fixture(WorkflowAction::RecordStart, reply, false);
            let original: Envelope =
                serde_json::from_slice(&fs::read(&f.response_file).unwrap()).unwrap();
            let canary = synthetic_canary();
            let key = "workflow-terminal-guard-original";
            let failure = execute_workflow_action(
                &f.service,
                &f.grants,
                &f.custody,
                Ok(Some(f.connection.clone())),
                request(
                    &f.project,
                    &f.grants,
                    key,
                    WorkflowAction::RecordStart,
                    true,
                ),
            )
            .await
            .unwrap_err();
            assert!(!serde_json::to_string(&failure).unwrap().contains(&canary));
            assert_eq!(observed_stage(&failure.receipt), "dispatching");
            // The helper and its local coordinator have already returned and
            // dropped. Only the AppState-equivalent shared handle remains.
            let snapshot = f.custody.private_snapshot();
            assert_eq!(snapshot, f.custody.clone().private_snapshot());
            assert_eq!(snapshot["durable"], false);
            assert_eq!(snapshot["entries"].as_array().unwrap().len(), 1);
            let attempt = &snapshot["entries"][0]["attempted_receipt"];
            assert_eq!(attempt["request_id"], key);
            assert_eq!(attempt["project_id"], json!(f.project.id));
            if let Some(error) = &original.error {
                assert_eq!(failure.code, error.code);
                assert_eq!(failure.outcome_known, error.outcome_known);
                assert_eq!(
                    attempt["payload"]["error"],
                    serde_json::to_value(error).unwrap()
                );
            } else {
                assert_eq!(failure.code, ErrorCode::BackendFailed);
                assert!(!failure.outcome_known);
                assert_eq!(
                    attempt["payload"]["result"],
                    json!({
                        "data": original.data.clone().unwrap(),
                        "execution": original.execution,
                        "warnings": original.warnings,
                        "provenance_checked": true,
                    })
                );
                assert_eq!(
                    attempt["payload"]["broker_request_id"],
                    "broker-assigned-request"
                );
            }
            let before = f.service.production_receipts(f.project.id, 20).unwrap();
            assert_eq!(before.len(), 1);
            assert!(!serde_json::to_string(&before).unwrap().contains(&canary));
            let effect_count = count(&f.effects);
            assert_eq!(count(&f.calls), 1);
            drop(f.service);
            let reopened = StudioService::open(&f.database).unwrap();
            // A fresh handle models losing non-durable memory on restart.
            // The original durable intent must independently forbid a retry.
            let fresh_custody = ProductionReceiptCustody::default();
            let refused = execute_workflow_action(
                &reopened,
                &f.grants,
                &fresh_custody,
                Ok(Some(f.connection.clone())),
                request(
                    &f.project,
                    &f.grants,
                    key,
                    WorkflowAction::RecordStart,
                    true,
                ),
            )
            .await
            .unwrap_err();
            assert_eq!(refused.code, ErrorCode::Conflict);
            assert!(!refused.outcome_known);
            assert_eq!(observed_stage(&refused.receipt), "dispatching");
            assert_eq!(
                reopened.production_receipts(f.project.id, 20).unwrap(),
                before
            );
            assert_eq!(fresh_custody.private_snapshot()["entries"], json!([]));
            assert_eq!(f.custody.private_snapshot(), snapshot);
            assert_eq!(count(&f.calls), 1);
            assert_eq!(count(&f.effects), effect_count);
        }
    }

    #[tokio::test]
    async fn readonly_plan_uses_current_scope_without_a_mutation_grant() {
        let f = fixture(WorkflowAction::ProposalPlan, Reply::Success, false);
        let refused = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                "plan-has-grant",
                WorkflowAction::ProposalPlan,
                true,
            ),
        )
        .await
        .unwrap_err();
        assert_eq!(refused.code, ErrorCode::InvalidArgument);
        assert_eq!(count(&f.calls), 0);
        let response = execute_workflow_action(
            &f.service,
            &f.grants,
            &f.custody,
            Ok(Some(f.connection.clone())),
            request(
                &f.project,
                &f.grants,
                "plan-original",
                WorkflowAction::ProposalPlan,
                false,
            ),
        )
        .await
        .unwrap();
        assert_eq!(response.command, "workflow.proposal.plan");
        assert_eq!(observed_stage(&response.receipt), "completed");
        assert_eq!(count(&f.calls), 1);
        assert_eq!(count(&f.effects), 0);
        assert!(
            f.service
                .production_receipts(f.project.id, 20)
                .unwrap()
                .iter()
                .any(|r| r.stage == "dispatching"
                    && r.payload.get("mutation") == Some(&Value::Bool(false)))
        );
    }
}
