//! Real persisted creative decisions do not grant outgoing effect authority.
//! The executable witness below only records process invocation. It is not a
//! Broker, network server, publisher, or evidence of installed user consent.
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/effect_authority.rs"]
mod effect_authority;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/effect_grants.rs"]
mod effect_grants;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/local_error.rs"]
mod local_error;
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/project_library.rs"]
mod project_library;
#[cfg(unix)]
#[allow(dead_code)]
#[path = "../../../apps/desktop/src-tauri/src/workflow_native.rs"]
mod workflow_native;

use effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::{
    Change, NarrativeEvidenceKind, PlanApproval, PlannedShot, ProductionClock, ProductionPlan,
    Project, RevisionStamp,
};
use motionwright_service::StudioService;
use serde_json::Value;
use std::path::PathBuf;
use tempfile::TempDir;

struct ApprovedProject {
    _temp: TempDir,
    database: PathBuf,
    project: Project,
    journal: Value,
}

fn approved_project() -> ApprovedProject {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("creative-approval.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let project = service
        .create_project("Authored concept permission boundary")
        .unwrap();
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "create-authored-scene",
            &Change::AddScene {
                name: "Original concept".into(),
                objective: "Preserve a recorded creative decision without outgoing authority"
                    .into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let mut plan = ProductionPlan {
        objective: "Explain a locally authored concept".into(),
        audience: "A local editor".into(),
        concept: "Original typography represents the idea".into(),
        reference_constraints: vec![],
        exclusions: vec!["No external publication is authorized".into()],
        shots: vec![PlannedShot {
            scene_id: project.scenes[0].id,
            purpose: "Show the editable concept".into(),
            claim_ids: vec![],
            asset_ids: vec![],
            evidence_kind: NarrativeEvidenceKind::GraphicStudy,
        }],
        clock: ProductionClock::Timeline,
        approval: None,
    };
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "save-authored-concept",
            &Change::SetProductionPlan {
                plan: Some(plan.clone()),
            },
        )
        .unwrap()
        .project;
    assert!(
        project
            .production_design
            .plan
            .as_ref()
            .unwrap()
            .approval
            .is_none()
    );
    plan.approval = Some(PlanApproval {
        reviewer: "Synthetic local editor".into(),
        note: "Aesthetic decision about this concept, not permission to send it.".into(),
        content_sha256: plan.content_digest().unwrap(),
    });
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "record-concept-approval",
            &Change::SetProductionPlan { plan: Some(plan) },
        )
        .unwrap()
        .project;
    let journal = serde_json::to_value(service.history(project.id, 0, 16).unwrap()).unwrap();
    assert_eq!(journal.as_array().unwrap().len(), 3);
    assert_eq!(journal[1]["change"]["type"], "set_production_plan");
    assert_eq!(journal[2]["change"]["type"], "set_production_plan");
    drop(service);
    ApprovedProject {
        _temp: temp,
        database,
        project,
        journal,
    }
}

fn assert_approval_and_journal(service: &StudioService, fixture: &ApprovedProject) {
    let project = service.project(fixture.project.id).unwrap();
    assert_eq!(project, fixture.project);
    let plan = project.production_design.plan.as_ref().unwrap();
    let approval = plan.approval.as_ref().unwrap();
    assert_eq!(approval.content_sha256, plan.content_digest().unwrap());
    assert_eq!(
        serde_json::to_value(service.history(project.id, 0, 16).unwrap()).unwrap(),
        fixture.journal
    );
}

#[test]
fn persisted_aesthetic_approval_never_issues_or_converts_external_effect_grants() {
    let fixture = approved_project();
    let service = StudioService::open(&fixture.database).unwrap();
    assert_approval_and_journal(&service, &fixture);
    let project = service.project(fixture.project.id).unwrap();
    let plan = project.production_design.plan.as_ref().unwrap();
    let approval = plan.approval.as_ref().unwrap();
    let scope = EffectScope::project(project.id, project.generation, project.revision);
    let subject = format!("approved-concept:{}", approval.content_sha256);
    let grants = EffectGrantRegistry::default();
    let request = |effect| effect_authority::IssueEffectGrantRequest {
        effect,
        project_id: Some(project.id),
        generation: Some(project.generation),
        revision: Some(project.revision),
        subject: subject.clone(),
    };

    // This exercises the service-backed command boundary used by desktop,
    // including the durable identity/revision read, rather than only a registry.
    let mut stale = request(EffectKind::ProjectEdit);
    stale.revision = Some(project.revision + 1);
    assert!(
        effect_authority::issue_effect_grant(&service, &grants, stale)
            .unwrap_err()
            .contains("scope is stale")
    );

    for external in [
        EffectKind::RemoteEgress,
        EffectKind::UploadExternal,
        EffectKind::PublishExternal,
        EffectKind::InstallRuntime,
    ] {
        let refusal =
            effect_authority::issue_effect_grant(&service, &grants, request(external)).unwrap_err();
        assert!(refusal.contains("does not expose grants"));
        for local in [
            EffectKind::ProjectEdit,
            EffectKind::RenderLocal,
            EffectKind::DeliverLocal,
        ] {
            // Exact current scope deliberately removes stale-revision refusal
            // as an alternative explanation for this effect-kind rejection.
            let issued =
                effect_authority::issue_effect_grant(&service, &grants, request(local)).unwrap();
            assert!(
                grants
                    .consume(issued.token, external, scope, &subject)
                    .is_err()
            );
            assert!(
                grants
                    .consume(issued.token, local, scope, &subject)
                    .is_err()
            );
        }
    }

    let edit =
        effect_authority::issue_effect_grant(&service, &grants, request(EffectKind::ProjectEdit))
            .unwrap();
    grants
        .consume(edit.token, EffectKind::ProjectEdit, scope, &subject)
        .unwrap();
    assert_approval_and_journal(&service, &fixture);
    drop(service);
    assert_approval_and_journal(&StudioService::open(&fixture.database).unwrap(), &fixture);
}

#[cfg(unix)]
mod outgoing {
    use super::*;
    use motionwright_native::production::{
        ProductionClient, ProductionConnection, ProductionCoordinator, ProductionReceiptCustody,
    };
    use semwright_native_sdk::ErrorCode;
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::{fs, os::unix::fs::PermissionsExt, path::Path};
    use workflow_native::{
        WorkflowAction, WorkflowActionRequest, WorkflowReceiptObservation, execute_workflow_action,
    };

    struct Witness {
        connection: ProductionConnection,
        path: PathBuf,
        calls: PathBuf,
    }

    fn private(path: &Path, mode: u32) {
        fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
    }

    fn witness(fixture: &ApprovedProject) -> Witness {
        let root = fixture.database.parent().unwrap();
        let executable = root.join("invocation-witness");
        let calls = root.join("invocations");
        let quoted = format!(
            "'{}'",
            calls.to_str().unwrap().replace(char::from(39), "'\\''")
        );
        // There is no network or publication implementation in this canary.
        // An accidentally admitted command still leaves an observable call
        // before returning a deliberately invalid Broker envelope.
        let script = format!("#!/bin/sh\nprintf 'invoked\\n' >> {quoted}\nprintf '{{}}\\n'\n");
        fs::write(&executable, &script).unwrap();
        private(&executable, 0o700);
        let socket = root.join("broker.sock");
        let session_file = root.join("session.json");
        let output_root = root.join("output");
        fs::write(&socket, []).unwrap();
        fs::write(&session_file, b"{}").unwrap();
        fs::create_dir(&output_root).unwrap();
        private(&socket, 0o600);
        private(&session_file, 0o600);
        private(&output_root, 0o700);
        let connection = ProductionConnection {
            schema: "motionwright-semwright-connection/1".into(),
            executable,
            executable_sha256: hex::encode(Sha256::digest(script.as_bytes())),
            socket,
            session_file,
            output_root,
            resource: fixture.project.resource_key(),
        };
        connection.validate().unwrap();
        let path = root.join("connection.json");
        fs::write(&path, serde_json::to_vec(&connection).unwrap()).unwrap();
        private(&path, 0o600);
        Witness {
            connection,
            path,
            calls,
        }
    }

    fn invocation_count(path: &Path) -> usize {
        match fs::read_to_string(path) {
            Ok(text) => text.lines().count(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => 0,
            Err(error) => panic!("Unable to read invocation witness: {error}"),
        }
    }

    #[tokio::test]
    async fn persisted_approval_cannot_dispatch_real_browser_egress_or_upload_commands() {
        let fixture = approved_project();
        let service = StudioService::open(&fixture.database).unwrap();
        assert_approval_and_journal(&service, &fixture);
        let witness = witness(&fixture);
        let coordinator =
            ProductionCoordinator::new(service.clone(), witness.connection.clone()).unwrap();

        // These are existing canonical browser commands, deliberately absent
        // from Motionwright's enabled production command set. Refusal occurs
        // before spawning the configured executable or sending any payload.
        for (key, command, args) in [
            (
                "approved-concept-egress",
                "browser.navigate",
                json!({"_target":"synthetic-target","url":"https://example.invalid/creative-approval"}),
            ),
            (
                "approved-concept-upload",
                "browser.element.upload",
                json!({"_target":"synthetic-target","root":"project","paths":["concept.json"]}),
            ),
        ] {
            let error = coordinator
                .execute(
                    fixture.project.id,
                    &RevisionStamp::from(&fixture.project),
                    key,
                    command,
                    args,
                    true,
                )
                .await
                .unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
            assert!(error.outcome_known);
            assert_eq!(invocation_count(&witness.calls), 0);
            let terminal = service
                .latest_production_receipt(fixture.project.id, key)
                .unwrap()
                .unwrap();
            assert_eq!(terminal.stage, "failed_known");
            assert_eq!(terminal.command, command);
            assert_eq!(terminal.generation, fixture.project.generation);
            assert_eq!(terminal.revision, fixture.project.revision);
            assert_eq!(terminal.payload["error"]["code"], "Unsupported");
            assert_eq!(terminal.payload["error"]["outcome_known"], true);
            assert_approval_and_journal(&service, &fixture);
        }
        drop(coordinator);
        drop(service);
        let reopened = StudioService::open(&fixture.database).unwrap();
        assert_approval_and_journal(&reopened, &fixture);
        for key in ["approved-concept-egress", "approved-concept-upload"] {
            assert_eq!(
                reopened
                    .latest_production_receipt(fixture.project.id, key)
                    .unwrap()
                    .unwrap()
                    .stage,
                "failed_known"
            );
        }
        assert_eq!(invocation_count(&witness.calls), 0);

        // A real supported read-only dispatch reaches the same invocation
        // witness. Its invalid envelope is not mistaken for Broker success.
        let client = ProductionClient::new(witness.connection).unwrap();
        let error = client
            .execute("workflow.traces.list", json!({}), false)
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::BackendFailed);
        assert!(error.outcome_known);
        assert_eq!(invocation_count(&witness.calls), 1);
    }

    #[tokio::test]
    async fn persisted_approval_is_not_a_desktop_workflow_mutation_grant() {
        let fixture = approved_project();
        let service = StudioService::open(&fixture.database).unwrap();
        assert_approval_and_journal(&service, &fixture);
        let witness = witness(&fixture);
        let grants = EffectGrantRegistry::default();
        let custody = ProductionReceiptCustody::default();
        let key = "approved-concept-without-effect-grant";
        let error = execute_workflow_action(
            &service,
            &grants,
            &custody,
            Ok(Some(witness.path)),
            WorkflowActionRequest {
                project_id: fixture.project.id,
                generation: fixture.project.generation,
                revision: fixture.project.revision,
                request_id: key.into(),
                action: WorkflowAction::RecordStart,
                effect_grant: None,
                args: json!({"capture_values":false}),
            },
        )
        .await
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::PermissionDenied);
        assert!(error.outcome_known);
        assert!(matches!(
            error.receipt,
            WorkflowReceiptObservation::NotObserved
        ));
        assert_eq!(invocation_count(&witness.calls), 0);
        assert!(
            service
                .latest_production_receipt(fixture.project.id, key)
                .unwrap()
                .is_none()
        );
        assert_approval_and_journal(&service, &fixture);
        drop(service);
        assert_approval_and_journal(&StudioService::open(&fixture.database).unwrap(), &fixture);
    }
}
