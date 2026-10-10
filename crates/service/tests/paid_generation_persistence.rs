//! The existing StudioService/SQLite revision boundary is the only durable
//! owner of a paid job's logical idempotency, reservation and unknown outcome.
use motionwright_domain::{
    Change, PaidGenerationEdit, PaidGenerationEvent, PaidGenerationEventKind as Event,
    PaidGenerationKind, PaidGenerationScope, PaidGenerationSpec, PaidGenerationState, PaidIdentity,
    Project,
};
use motionwright_service::StudioService;
use uuid::Uuid;
fn id(n: u128) -> Uuid {
    Uuid::from_u128(n)
}
fn sha(c: char) -> String {
    c.to_string().repeat(64)
}
fn commit(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}
fn spec(project: &Project) -> PaidGenerationSpec {
    let mut result = PaidGenerationSpec {
        id: id(101),
        campaign_id: id(102),
        scene_id: project.scenes[0].id,
        variant_index: 0,
        kind: PaidGenerationKind::Video,
        scope: PaidGenerationScope::Pilot,
        provider_id: "synthetic_provider".into(),
        model_id: "original_video".into(),
        model_version: "v1".into(),
        capability_receipt_sha256: sha('a'),
        rights_policy_sha256: sha('b'),
        prompt_sha256: sha('c'),
        inputs: vec![],
        output_usage_terms: "Original synthetic footage study only".into(),
        identity: PaidIdentity::SyntheticOriginal {
            visible_synthetic_label: "ORIGINAL SYNTHETIC FOOTAGE / NOT PRODUCT EVIDENCE".into(),
        },
        max_charge_microusd: 2_000_000,
        logical_idempotency_sha256: String::new(),
    };
    result.logical_idempotency_sha256 = result.expected_idempotency(project.id).unwrap();
    result
}
#[test]
fn one_unknown_paid_task_survives_multiple_sqlite_reopens_without_double_reserving() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("real-creative-ledger.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut project = service
        .create_project("Original owned synthetic media")
        .unwrap();
    project = commit(
        &service,
        &project,
        "scene",
        Change::AddScene {
            name: "One source project".into(),
            objective: "Record paid API outcome safely".into(),
            duration_seconds: 4,
        },
    );
    let spec = spec(&project);
    project = commit(
        &service,
        &project,
        "paid:plan",
        Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Create { spec: spec.clone() },
        },
    );
    let stamp = project.stamp();
    let history = project.production_design.paid_generation.jobs[0]
        .history_sha256()
        .unwrap();
    project = commit(
        &service,
        &project,
        "paid:reserve",
        Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: spec.id,
                expected_history_sha256: history,
                event: PaidGenerationEvent {
                    id: id(200),
                    kind: Event::Reserve {
                        maximum_charge_microusd: 2_000_000,
                    },
                },
            },
        },
    );
    let reserved_history = project.production_design.paid_generation.jobs[0]
        .history_sha256()
        .unwrap();
    project = commit(
        &service,
        &project,
        "paid:dispatch:unknown",
        Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: spec.id,
                expected_history_sha256: reserved_history,
                event: PaidGenerationEvent {
                    id: id(201),
                    kind: Event::DispatchUnknown {
                        provider_task_id: Some("external.task_920".into()),
                        declared_receipt_sha256: Some(sha('d')),
                        reason: "The provider may have accepted this logical task before timeout"
                            .into(),
                    },
                },
            },
        },
    );
    assert_eq!(
        project.production_design.paid_generation.jobs[0].state,
        PaidGenerationState::UnknownOutcome
    );
    let prior = project.clone();
    drop(service);
    for _ in 0..3 {
        let reopened = StudioService::open(&db).unwrap().project(prior.id).unwrap();
        assert_eq!(
            reopened, prior,
            "Persisted source journal changed across reopen"
        );
        assert_eq!(
            reopened.production_design.paid_generation.jobs[0].reserved_microusd,
            2_000_000
        );
        let hint = reopened.production_design.paid_generation.jobs[0]
            .reconcile_query()
            .unwrap();
        assert_eq!(hint.known_task_id.as_deref(), Some("external.task_920"));
        assert_eq!(
            hint.logical_idempotency_sha256,
            spec.logical_idempotency_sha256
        );
        assert!(!hint.launches_new_task && !hint.billing_verified);
    }
    let service = StudioService::open(&db).unwrap();
    let stale = service.apply(
        project.id,
        &stamp,
        "paid:stale:reserve",
        &Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: spec.id,
                expected_history_sha256: sha('e'),
                event: PaidGenerationEvent {
                    id: id(202),
                    kind: Event::Reserve {
                        maximum_charge_microusd: 2_000_000,
                    },
                },
            },
        },
    );
    assert!(stale.is_err());
    let observed = service.project(project.id).unwrap();
    assert_eq!(observed, prior);
}
#[test]
fn legacy_schema_can_read_empty_ledger_but_cannot_silently_roundtrip_paid_state() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("paid-old-writer.sqlite3")).unwrap();
    let project = service.create_project("Source migration policy").unwrap();
    let mut legacy = serde_json::to_value(&project).unwrap();
    legacy["schema_version"] = serde_json::json!(2);
    let old: Project = serde_json::from_value(legacy).unwrap();
    old.validate().unwrap();
    assert!(old.production_design.paid_generation.is_empty());
}
