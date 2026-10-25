// Included inside production::tests to reuse its exact original transport fixture.
// These are synthetic CLI/SQLite tests, not native renderer evidence.
#[cfg(unix)]
mod graph_coordinator_tests {
    use super::*;
    use crate::production::manim_graph::{START_COMMAND, tests::persisted};
    use motionwright_domain::{GraphContent, GraphSourceSnapshot};
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    fn args(plan: &motionwright_manim_profile::semantic_graph::ManimSemanticGraphPlan) -> Value {
        json!({"plan": plan, "profile": plan.render_profile})
    }

    fn append(
        service: &StudioService,
        project: &motionwright_domain::Project,
        request_id: &str,
        input: &Value,
        stage: &str,
        payload: Value,
    ) {
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: request_id.into(),
                request_sha256: request_digest(START_COMMAND, input).unwrap(),
                command: START_COMMAND.into(),
                stage: stage.into(),
                payload,
            })
            .unwrap();
    }

    fn unresolved_error() -> Value {
        serde_json::to_value(
            Error::new(
                ErrorCode::Conflict,
                "Production request already has a non-terminal or failed receipt; inspect before retrying",
            ).uncertain(),
        ).unwrap()
    }

    #[tokio::test]
    async fn graph_synthetic_cli_dispatch_and_sqlite_replay_use_one_original_invocation() {
        let (service, project, plan, temp) = persisted();
        let output = serde_json::to_string(&transport_reply(START_COMMAND)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let input = args(&plan);
        // A caller cannot label the artifact-producing Graph command read-only.
        let first = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "graph-synthetic-original",
                START_COMMAND,
                input.clone(),
                false,
            )
            .await
            .unwrap();
        assert_eq!(first["replayed"], false);
        assert_eq!(first["receipt"]["stage"], "completed");
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        let receipts = coordinator.receipts(project.id, 64).unwrap();
        assert_eq!(receipts.len(), 2);
        assert_eq!(
            receipts
                .iter()
                .find(|row| row.stage == "dispatching")
                .unwrap()
                .payload["mutation"],
            true,
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let coordinator = ProductionCoordinator::new(reopened.clone(), connection).unwrap();
        let replay = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "graph-synthetic-original",
                START_COMMAND,
                input,
                false,
            )
            .await
            .unwrap();
        assert_eq!(replay["replayed"], true);
        assert_eq!(replay["result"], first["result"]);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
        assert_eq!(coordinator.receipts(project.id, 64).unwrap(), receipts);
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn graph_forged_completed_receipt_cannot_replay_changed_text_or_branch() {
        for changed_branch in [false, true] {
            let (service, project, mut plan, temp) = persisted();
            if changed_branch {
                plan.execution_snapshot.branch_id = Uuid::new_v4();
            } else {
                let GraphContent::CausalDiagram { vertices, .. } = &mut plan.source.content else {
                    unreachable!()
                };
                vertices[0].label = "Not saved".into();
            }
            let input = args(&plan);
            let response = transport_reply(START_COMMAND);
            let output = serde_json::to_string(&response).unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
            append(
                &service,
                &project,
                "graph-forged-completed",
                &input,
                "completed",
                json!({"result": response, "broker_request_id": "transport-fixture-original",
                    "job_ref": null, "mutation": true}),
            );
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let before = coordinator.receipts(project.id, 64).unwrap();
            let error = coordinator
                .execute(
                    project.id,
                    &project.stamp(),
                    "graph-forged-completed",
                    START_COMMAND,
                    input,
                    true,
                )
                .await
                .unwrap_err();
            assert_eq!(
                error.code,
                if changed_branch {
                    ErrorCode::StaleReference
                } else {
                    ErrorCode::Conflict
                }
            );
            assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
            assert!(!counter.exists());
            assert_eq!(coordinator.receipts(project.id, 64).unwrap(), before);
            assert_eq!(service.project(project.id).unwrap(), project);
        }
    }

    #[tokio::test]
    async fn graph_original_unknown_precedes_later_source_diagnostic_and_never_retries() {
        for stage in ["outcome_unknown", "dispatching"] {
            let (service, project, mut plan, temp) = persisted();
            let GraphContent::CausalDiagram { vertices, .. } = &mut plan.source.content else {
                unreachable!()
            };
            vertices[0].label = "Unpersisted".into();
            let input = args(&plan);
            let output = serde_json::to_string(&transport_reply(START_COMMAND)).unwrap();
            let (connection, counter) =
                fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
            append(
                &service,
                &project,
                "graph-original-unknown",
                &input,
                stage,
                json!({"mutation": true, "job_ref": "retained-original-only"}),
            );
            let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
            let before = coordinator.receipts(project.id, 64).unwrap();
            let error = coordinator
                .execute(
                    project.id,
                    &project.stamp(),
                    "graph-original-unknown",
                    START_COMMAND,
                    input,
                    false,
                )
                .await
                .unwrap_err();
            assert_eq!(serde_json::to_value(&error).unwrap(), unresolved_error());
            assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
            assert!(!counter.exists());
            assert_eq!(coordinator.receipts(project.id, 64).unwrap(), before);
            assert_eq!(service.project(project.id).unwrap(), project);
        }
    }

    #[tokio::test]
    async fn graph_peer_commit_after_preflight_is_rejected_before_custody_or_dispatch() {
        let (service, project, plan, temp) = persisted();
        let output = serde_json::to_string(&transport_reply(START_COMMAND)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let mut coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let hook_calls = Arc::new(AtomicUsize::new(0));
        let count = Arc::clone(&hook_calls);
        let database = temp.path().join("motionwright.sqlite3");
        let id = project.id;
        let stamp = project.stamp();
        coordinator.before_dispatch_admission = Some(Arc::new(move || {
            assert_eq!(count.fetch_add(1, Ordering::SeqCst), 0);
            let peer = StudioService::open(&database).unwrap();
            peer.apply(
                id,
                &stamp,
                "graph-peer-edit",
                &motionwright_domain::Change::RenameProject {
                    title: "Peer edit before Graph dispatch".into(),
                },
            )
            .unwrap();
        }));
        let custody = coordinator.private_receipt_custody_snapshot();
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "graph-stale-after-preflight",
                START_COMMAND,
                args(&plan),
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert_eq!(hook_calls.load(Ordering::SeqCst), 1);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert!(!counter.exists());
        assert!(coordinator.receipts(project.id, 64).unwrap().is_empty());
        assert_eq!(coordinator.private_receipt_custody_snapshot(), custody);
        let after = service.project(project.id).unwrap();
        assert_eq!(after.revision, project.revision + 1);
        assert_eq!(
            after.production_design.semantic_graphs,
            project.production_design.semantic_graphs
        );
    }

    #[tokio::test]
    async fn graph_explicit_entrypoint_requires_literal_current_branch_before_any_effect() {
        let (service, project, plan, temp) = persisted();
        let output = serde_json::to_string(&transport_reply(START_COMMAND)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let mut expected = GraphSourceSnapshot::from_project(&project);
        expected.branch_id = Uuid::new_v4();
        let error = coordinator
            .realize_manim_semantic_graph(
                &expected,
                "graph-wrong-branch",
                plan.source.id,
                plan.source.variant_profile_ref,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert!(!counter.exists());
        assert!(coordinator.receipts(project.id, 64).unwrap().is_empty());
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn graph_normal_three_field_route_refuses_loss_before_fonts_or_native_start() {
        let (service, project, plan, temp) = persisted();
        let output = serde_json::to_string(&transport_reply(START_COMMAND)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let original_output = fs::read_dir(&connection.output_root)
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect::<BTreeSet<_>>();
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let error = coordinator
            .realize_manim_scene(
                project.id,
                &project.stamp(),
                "graph-needs-four-fields",
                plan.source.scene_id,
                plan.render_profile,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.to_string().contains("four-field Graph"));
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert!(!counter.exists());
        assert_eq!(
            fs::read_dir(&connection.output_root)
                .unwrap()
                .map(|entry| entry.unwrap().file_name())
                .collect::<BTreeSet<_>>(),
            original_output,
        );
        assert!(coordinator.receipts(project.id, 64).unwrap().is_empty());
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn graph_spawned_malformed_terminal_is_uncertain_even_when_caller_said_read_only() {
        let (service, project, plan, temp) = persisted();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), "{", "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let input = args(&plan);
        let original = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "graph-malformed-terminal",
                START_COMMAND,
                input.clone(),
                false,
            )
            .await
            .unwrap_err();
        let receipts = coordinator.receipts(project.id, 64).unwrap();
        assert_eq!(receipts.len(), 2);
        let terminal = receipts
            .iter()
            .find(|row| row.stage == "outcome_unknown")
            .unwrap();
        assert_eq!(
            terminal.payload["error"],
            serde_json::to_value(&original).unwrap()
        );
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(fs::read(&counter).unwrap(), b"invoked\n");
        let repeated = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "graph-malformed-terminal",
                START_COMMAND,
                input,
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(serde_json::to_value(&repeated).unwrap(), unresolved_error());
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
        assert_eq!(coordinator.receipts(project.id, 64).unwrap(), receipts);
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn graph_closed_request_failure_never_appends_or_invokes_a_native_command() {
        let (service, project, plan, temp) = persisted();
        let output = serde_json::to_string(&transport_reply(START_COMMAND)).unwrap();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), &output, "", 0);
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        let mut input = args(&plan);
        input["program"] = json!("not an admitted field");
        let error = coordinator
            .execute(
                project.id,
                &project.stamp(),
                "graph-invalid-request",
                START_COMMAND,
                input,
                true,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert_eq!(coordinator.client.execute_calls.load(Ordering::SeqCst), 0);
        assert!(!counter.exists());
        assert!(coordinator.receipts(project.id, 64).unwrap().is_empty());
        assert_eq!(service.project(project.id).unwrap(), project);
    }

    #[tokio::test]
    async fn graph_public_client_cannot_downgrade_post_spawn_transport_uncertainty() {
        let (service, project, plan, temp) = persisted();
        let (connection, counter) =
            fake_transport_connection(&temp, project.resource_key(), "{", "", 0);
        let client = ProductionClient::new(connection).unwrap();
        let error = match client.execute(START_COMMAND, args(&plan), false).await {
            Ok(_) => panic!("Malformed CLI bytes cannot be a successful Graph result"),
            Err(error) => error,
        };
        let expected = backend("Semwright CLI returned an invalid response envelope").uncertain();
        assert_eq!(
            serde_json::to_value(&error).unwrap(),
            serde_json::to_value(&expected).unwrap()
        );
        assert_eq!(client.execute_calls.load(Ordering::SeqCst), 1);
        assert_eq!(fs::read(counter).unwrap(), b"invoked\n");
        assert!(
            service
                .production_receipts(project.id, 64)
                .unwrap()
                .is_empty()
        );
        assert_eq!(service.project(project.id).unwrap(), project);
    }
}
