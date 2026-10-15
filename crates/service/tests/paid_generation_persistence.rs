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
            edit: PaidGenerationEdit::Create {
                spec: Box::new(spec.clone()),
            },
        },
    );
    let stamp = project.stamp();
    let history = project.paid_generation.jobs[0].history_sha256().unwrap();
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
    let reserved_history = project.paid_generation.jobs[0].history_sha256().unwrap();
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
        project.paid_generation.jobs[0].state,
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
            reopened.paid_generation.jobs[0].reserved_microusd,
            2_000_000
        );
        let hint = reopened.paid_generation.jobs[0].reconcile_query().unwrap();
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
    assert!(old.paid_generation.is_empty());
}

fn saved_draft(service: &StudioService) -> Project {
    let p = service
        .create_project("Synthetic generation accounting only")
        .unwrap();
    let p = commit(
        service,
        &p,
        "source-scene",
        Change::AddScene {
            name: "Original".into(),
            objective: "No external dispatch".into(),
            duration_seconds: 4,
        },
    );
    let request = spec(&p);
    commit(
        service,
        &p,
        "source-intent",
        Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Create {
                spec: Box::new(request),
            },
        },
    )
}

#[test]
fn independent_sqlite_clients_cannot_both_reserve_the_same_revision() {
    use std::sync::{Arc, Barrier};
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("two-independent-owners.sqlite3");
    let initial = StudioService::open(&db).unwrap();
    let p = saved_draft(&initial);
    let job = &p.paid_generation.jobs[0];
    let history = job.history_sha256().unwrap();
    let barrier = Arc::new(Barrier::new(2));
    let mut workers = Vec::new();
    for index in 0..2_u128 {
        let client = StudioService::open(&db).unwrap();
        let barrier = barrier.clone();
        let project = p.clone();
        let history = history.clone();
        workers.push(std::thread::spawn(move || {
            barrier.wait();
            client
                .apply(
                    project.id,
                    &project.stamp(),
                    &format!("independent-reserve-{index}"),
                    &Change::EditPaidGeneration {
                        edit: PaidGenerationEdit::Append {
                            job_id: id(101),
                            expected_history_sha256: history,
                            event: PaidGenerationEvent {
                                id: id(810 + index),
                                kind: Event::Reserve {
                                    maximum_charge_microusd: 2_000_000,
                                },
                            },
                        },
                    },
                )
                .is_ok()
        }));
    }
    let successes = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .filter(|ok| *ok)
        .count();
    assert_eq!(
        successes, 1,
        "exactly one compare-and-swap may reserve this logical request"
    );
    let current = initial.project(p.id).unwrap();
    assert_eq!(current.revision, p.revision + 1);
    assert_eq!(current.paid_generation.jobs[0].events.len(), 1);
    assert_eq!(current.paid_generation.jobs[0].reserved_microusd, 2_000_000);
}

#[test]
fn portable_reimport_keeps_unknown_history_but_gets_a_new_cas_generation() {
    let tmp = tempfile::tempdir().unwrap();
    let source = StudioService::open(tmp.path().join("source.sqlite3")).unwrap();
    let mut p = saved_draft(&source);
    for (event_id, kind) in [
        (
            id(820),
            Event::Reserve {
                maximum_charge_microusd: 2_000_000,
            },
        ),
        (
            id(821),
            Event::DispatchUnknown {
                provider_task_id: Some("existing.original.task".into()),
                declared_receipt_sha256: None,
                reason: "Declared external timeout; not verified billing".into(),
            },
        ),
    ] {
        let edit = PaidGenerationEdit::Append {
            job_id: id(101),
            expected_history_sha256: p.paid_generation.jobs[0].history_sha256().unwrap(),
            event: PaidGenerationEvent { id: event_id, kind },
        };
        p = commit(
            &source,
            &p,
            &format!("event:{event_id}"),
            Change::EditPaidGeneration { edit },
        );
    }
    let backup = source.export_project(p.id).unwrap();
    let receiver = StudioService::open(tmp.path().join("receiver.sqlite3")).unwrap();
    let imported = receiver.import_project(&backup).unwrap();
    assert_ne!(imported.generation, p.generation);
    assert_eq!(imported.id, p.id);
    assert_eq!(imported.paid_generation, p.paid_generation);
    let query = imported.paid_generation.jobs[0].reconcile_query().unwrap();
    assert_eq!(query.operation, "query_existing_task_only");
    assert!(!query.launches_new_task && !query.billing_verified);
    let mut duplicate = imported.paid_generation.jobs[0].spec.clone();
    duplicate.id = Uuid::new_v4();
    duplicate.max_charge_microusd += 10;
    duplicate.logical_idempotency_sha256 = duplicate.expected_idempotency(imported.id).unwrap();
    assert!(
        receiver
            .apply(
                imported.id,
                &imported.stamp(),
                "duplicate-after-import",
                &Change::EditPaidGeneration {
                    edit: PaidGenerationEdit::Create {
                        spec: Box::new(duplicate)
                    }
                }
            )
            .is_err()
    );
    assert_eq!(receiver.project(imported.id).unwrap(), imported);
}
