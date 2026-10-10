use motionwright_domain::{
    self as d, Asset, Change, PaidGenerationEdit, PaidGenerationEvent,
    PaidGenerationEventKind as Ev, PaidGenerationKind as Kind, PaidGenerationScope as Scope,
    PaidGenerationSpec, PaidGenerationState as Stage, PaidIdentity, PaidProviderOutcome as Outcome,
    Project,
};
use uuid::Uuid;
fn uid(n: u128) -> Uuid {
    Uuid::from_u128(n)
}
fn sha(ch: char) -> String {
    ch.to_string().repeat(64)
}
fn original() -> Project {
    let mut project = Project::new("Original source paid-generation work").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "One original authored scene".into(),
            objective: "Preserve source/owner controls".into(),
            duration_seconds: 4,
        })
        .unwrap();
    project
        .apply_change(&Change::AddAsset {
            asset: Asset {
                id: uid(700),
                name: "Owner-approved original reference.png".into(),
                media_type: "image/png".into(),
                content_sha256: Some(sha('a')),
                source_revision: Some("owner-upload-rev-1".into()),
            },
        })
        .unwrap();
    project
}
fn spec(project: &Project, id: u128, scope: Scope, kind: Kind, variant: u16) -> PaidGenerationSpec {
    let mut request = PaidGenerationSpec {
        id: uid(id),
        campaign_id: uid(800),
        scene_id: project.scenes[0].id,
        variant_index: variant,
        kind,
        scope,
        provider_id: "example_provider".into(),
        model_id: "original-gen".into(),
        model_version: "2026.10".into(),
        capability_receipt_sha256: sha('b'),
        rights_policy_sha256: sha('c'),
        prompt_sha256: sha('d'),
        inputs: vec![],
        output_usage_terms: "An original synthetic art direction study".into(),
        identity: PaidIdentity::SyntheticOriginal {
            visible_synthetic_label: "GRAPHIC STUDY / SYNTHETIC MEDIA".into(),
        },
        max_charge_microusd: 4_000_000,
        logical_idempotency_sha256: String::new(),
    };
    request.logical_idempotency_sha256 = request.expected_idempotency(project.id).unwrap();
    request
}
fn create(project: &mut Project, spec: PaidGenerationSpec) {
    project
        .apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Create { spec },
        })
        .unwrap();
}
fn job(project: &Project, id: u128) -> &d::PaidGenerationJob {
    project
        .production_design
        .paid_generation
        .jobs
        .iter()
        .find(|job| job.spec.id == uid(id))
        .unwrap()
}
fn event(project: &mut Project, id: u128, event_id: u128, kind: Ev) {
    let stamp = job(project, id).history_sha256().unwrap();
    project
        .apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(id),
                expected_history_sha256: stamp,
                event: PaidGenerationEvent {
                    id: uid(event_id),
                    kind,
                },
            },
        })
        .unwrap();
}
fn reserve(project: &mut Project, id: u128) {
    event(
        project,
        id,
        id + 100,
        Ev::Reserve {
            maximum_charge_microusd: 4_000_000,
        },
    );
}
fn dispatch(project: &mut Project, id: u128, task: Option<&str>) {
    event(
        project,
        id,
        id + 200,
        Ev::DispatchUnknown {
            provider_task_id: task.map(str::to_owned),
            declared_receipt_sha256: Some(sha('e')),
            reason: "The provider submission outcome cannot be established from this process."
                .into(),
        },
    );
}
fn report(project: &mut Project, id: u128, event_id: u128, task: Option<&str>, outcome: Outcome) {
    event(
        project,
        id,
        event_id,
        Ev::ProviderPoll {
            provider_task_id: task.map(str::to_owned),
            observed_receipt_sha256: format!("{event_id:064x}"),
            outcome,
        },
    );
}
#[test]
fn pilot_is_reserved_once_and_timeout_remains_unknown_without_an_automatic_retry() {
    let mut p = original();
    let request = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut p, request);
    reserve(&mut p, 1000);
    dispatch(&mut p, 1000, None);
    assert_eq!(job(&p, 1000).state, Stage::UnknownOutcome);
    let hint = job(&p, 1000).reconcile_query().unwrap();
    assert_eq!(hint.operation, "query_existing_task_only");
    assert!(
        !hint.launches_new_task && !hint.billing_verified && !hint.provider_observation_verified
    );
    assert_eq!(
        hint.logical_idempotency_sha256,
        job(&p, 1000).spec.logical_idempotency_sha256
    );
    let previous = p.clone();
    let key = job(&p, 1000).history_sha256().unwrap();
    let retry = Change::EditPaidGeneration {
        edit: PaidGenerationEdit::Append {
            job_id: uid(1000),
            expected_history_sha256: key,
            event: PaidGenerationEvent {
                id: uid(9001),
                kind: Ev::Reserve {
                    maximum_charge_microusd: 4_000_000,
                },
            },
        },
    };
    assert!(p.apply_change(&retry).is_err());
    assert_eq!(p, previous);
    report(
        &mut p,
        1000,
        9002,
        Some("provider.task_001"),
        Outcome::StillRunning,
    );
    assert_eq!(job(&p, 1000).state, Stage::UnknownOutcome);
    assert_eq!(job(&p, 1000).task_id.as_deref(), Some("provider.task_001"));
    assert!(p.validate().is_ok());
}
#[test]
fn provider_result_is_only_reported_until_actual_media_is_imported_and_reviewed() {
    let mut p = original();
    let request = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut p, request);
    reserve(&mut p, 1000);
    dispatch(&mut p, 1000, Some("owner.task_1"));
    report(
        &mut p,
        1000,
        9002,
        Some("owner.task_1"),
        Outcome::Succeeded {
            output_sha256: sha('f'),
            reported_cost_microusd: 3_000_000,
        },
    );
    assert_eq!(job(&p, 1000).state, Stage::ProviderReportedSuccess);
    assert_eq!(
        job(&p, 1000).provider_reported_spend_microusd,
        Some(3_000_000)
    );
    let before = p.clone();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1000),
                expected_history_sha256: job(&p, 1000).history_sha256().unwrap(),
                event: PaidGenerationEvent {
                    id: uid(9003),
                    kind: Ev::AdmitOutput {
                        asset_id: uid(701),
                        output_sha256: sha('f'),
                        rights_note: "Original media owner checked usage terms".into(),
                        reviewer: "Owner".into()
                    }
                }
            }
        })
        .is_err()
    );
    assert_eq!(p, before);
    p.apply_change(&Change::AddAsset {
        asset: Asset {
            id: uid(701),
            name: "Original generated pilot".into(),
            media_type: "image/png".into(),
            content_sha256: Some(sha('f')),
            source_revision: Some("import-original-output".into()),
        },
    })
    .unwrap();
    event(
        &mut p,
        1000,
        9003,
        Ev::AdmitOutput {
            asset_id: uid(701),
            output_sha256: sha('f'),
            rights_note: "Original usage rights reviewed".into(),
            reviewer: "Owner record, not authenticated".into(),
        },
    );
    assert_eq!(job(&p, 1000).state, Stage::OutputAdmitted);
    event(
        &mut p,
        1000,
        9004,
        Ev::ApprovePilot {
            output_sha256: sha('f'),
            reviewer: "Owner record".into(),
            rationale: "The bounded original sample meets the selected art direction".into(),
        },
    );
    assert_eq!(job(&p, 1000).state, Stage::PilotApproved);
    assert!(job(&p, 1000).reconcile_query().is_err());
}
#[test]
fn a_batch_cannot_reserve_spend_before_a_same_model_approved_pilot() {
    let mut p = original();
    let pilot = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut p, pilot);
    let batch = spec(
        &p,
        1001,
        Scope::Batch {
            pilot_job_id: uid(1000),
            approved_pilot_sha256: sha('f'),
        },
        Kind::Image,
        1,
    );
    create(&mut p, batch);
    let before = p.clone();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1001),
                expected_history_sha256: job(&p, 1001).history_sha256().unwrap(),
                event: PaidGenerationEvent {
                    id: uid(9001),
                    kind: Ev::Reserve {
                        maximum_charge_microusd: 4_000_000
                    }
                }
            }
        })
        .is_err()
    );
    assert_eq!(p, before);
    reserve(&mut p, 1000);
    dispatch(&mut p, 1000, Some("pilot.task"));
    report(
        &mut p,
        1000,
        9002,
        Some("pilot.task"),
        Outcome::Succeeded {
            output_sha256: sha('f'),
            reported_cost_microusd: 2_000_000,
        },
    );
    p.apply_change(&Change::AddAsset {
        asset: Asset {
            id: uid(701),
            name: "Pilot approved source media".into(),
            media_type: "image/png".into(),
            content_sha256: Some(sha('f')),
            source_revision: Some("reviewed".into()),
        },
    })
    .unwrap();
    event(
        &mut p,
        1000,
        9003,
        Ev::AdmitOutput {
            asset_id: uid(701),
            output_sha256: sha('f'),
            rights_note: "Owner approved".into(),
            reviewer: "Human".into(),
        },
    );
    event(
        &mut p,
        1000,
        9004,
        Ev::ApprovePilot {
            output_sha256: sha('f'),
            reviewer: "Human".into(),
            rationale: "Pilot sample accepted".into(),
        },
    );
    reserve(&mut p, 1001);
    assert_eq!(job(&p, 1001).state, Stage::Reserved);
    assert_eq!(job(&p, 1001).reserved_microusd, 4_000_000);
}

#[test]
fn identical_paid_semantic_request_cannot_be_billed_twice_with_a_new_uuid() {
    let mut p = original();
    let first = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    let mut duplicate = spec(&p, 1001, Scope::Pilot, Kind::Image, 0);
    assert_eq!(
        first.logical_idempotency_sha256,
        duplicate.logical_idempotency_sha256
    );
    create(&mut p, first);
    let snapshot = p.clone();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Create {
                spec: duplicate.clone()
            }
        })
        .is_err()
    );
    assert_eq!(p, snapshot);
    duplicate.variant_index = 1;
    assert!(
        duplicate.validate(&p).is_err(),
        "Pilots cannot allocate fake variant budgets"
    );
}
#[test]
fn duplicate_stale_or_cross_task_poll_does_not_change_cost_history() {
    let mut p = original();
    let request = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut p, request);
    reserve(&mut p, 1000);
    dispatch(&mut p, 1000, Some("stable.task_1"));
    report(
        &mut p,
        1000,
        9002,
        Some("stable.task_1"),
        Outcome::StillRunning,
    );
    let before = p.clone();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1000),
                expected_history_sha256: sha('a'),
                event: PaidGenerationEvent {
                    id: uid(9003),
                    kind: Ev::ProviderPoll {
                        provider_task_id: Some("stable.task_1".into()),
                        observed_receipt_sha256: sha('b'),
                        outcome: Outcome::StillRunning
                    }
                }
            }
        })
        .is_err()
    );
    assert_eq!(p, before);
    let digest = job(&p, 1000).history_sha256().unwrap();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1000),
                expected_history_sha256: digest.clone(),
                event: PaidGenerationEvent {
                    id: uid(9004),
                    kind: Ev::ProviderPoll {
                        provider_task_id: Some("different.task_2".into()),
                        observed_receipt_sha256: sha('d'),
                        outcome: Outcome::StillRunning
                    }
                }
            }
        })
        .is_err()
    );
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1000),
                expected_history_sha256: digest,
                event: PaidGenerationEvent {
                    id: uid(9005),
                    kind: Ev::ProviderPoll {
                        provider_task_id: Some("stable.task_1".into()),
                        observed_receipt_sha256: format!("{:064x}", 9002),
                        outcome: Outcome::StillRunning
                    }
                }
            }
        })
        .is_err()
    );
    assert_eq!(p, before);
}
#[test]
fn misleading_identifiers_unapproved_identity_and_wrong_usage_fail_closed() {
    let mut p = original();
    let mut candidate = spec(&p, 1000, Scope::Pilot, Kind::Presenter, 0);
    candidate.provider_id = "https://unsafe.example/endpoint".into();
    candidate.logical_idempotency_sha256 = candidate.expected_idempotency(p.id).unwrap();
    assert!(candidate.validate(&p).is_err());
    let mut person = spec(&p, 1000, Scope::Pilot, Kind::Presenter, 0);
    person.identity = PaidIdentity::ConsentedPerson {
        person_asset_id: uid(700),
        person_asset_sha256: sha('a'),
        purpose: "An unrelated identity-clone advertisement".into(),
        exact_consent_sha256: sha('f'),
        owner_declares_consent: false,
    };
    person.logical_idempotency_sha256 = person.expected_idempotency(p.id).unwrap();
    assert!(person.validate(&p).is_err());
    person.inputs = vec![d::PaidGenerationInput {
        asset_id: uid(700),
        sha256: sha('a'),
        owner_rights_note: "Owner supplied likeness solely for original project".into(),
    }];
    person.identity = PaidIdentity::ConsentedPerson {
        person_asset_id: uid(700),
        person_asset_sha256: sha('a'),
        purpose: "An unrelated identity-clone advertisement".into(),
        exact_consent_sha256: sha('f'),
        owner_declares_consent: true,
    };
    person.logical_idempotency_sha256 = person.expected_idempotency(p.id).unwrap();
    assert!(person.validate(&p).is_err());
    if let PaidIdentity::ConsentedPerson { purpose, .. } = &mut person.identity {
        *purpose = person.output_usage_terms.clone();
    }
    person.logical_idempotency_sha256 = person.expected_idempotency(p.id).unwrap();
    assert!(
        person.validate(&p).is_ok(),
        "Declared consent is retained as source metadata, not authenticated"
    );
    create(&mut p, person);
    assert_eq!(job(&p, 1000).state, Stage::Draft);
}
#[test]
fn reported_costs_over_budget_or_final_failure_without_task_identity_are_rejected() {
    let mut p = original();
    let request = spec(&p, 1000, Scope::Pilot, Kind::Tts, 0);
    create(&mut p, request);
    reserve(&mut p, 1000);
    dispatch(&mut p, 1000, None);
    let initial = p.clone();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1000),
                expected_history_sha256: job(&p, 1000).history_sha256().unwrap(),
                event: PaidGenerationEvent {
                    id: uid(9002),
                    kind: Ev::ProviderPoll {
                        provider_task_id: None,
                        observed_receipt_sha256: sha('e'),
                        outcome: Outcome::Failed {
                            reason: "Timeout is not provider failure".into()
                        }
                    }
                }
            }
        })
        .is_err()
    );
    assert_eq!(p, initial);
    report(
        &mut p,
        1000,
        9003,
        Some("tts.task.9"),
        Outcome::StillRunning,
    );
    let before = p.clone();
    assert!(
        p.apply_change(&Change::EditPaidGeneration {
            edit: PaidGenerationEdit::Append {
                job_id: uid(1000),
                expected_history_sha256: job(&p, 1000).history_sha256().unwrap(),
                event: PaidGenerationEvent {
                    id: uid(9004),
                    kind: Ev::ProviderPoll {
                        provider_task_id: Some("tts.task.9".into()),
                        observed_receipt_sha256: sha('c'),
                        outcome: Outcome::Succeeded {
                            output_sha256: sha('f'),
                            reported_cost_microusd: 5_000_000
                        }
                    }
                }
            }
        })
        .is_err()
    );
    assert_eq!(p, before);
}
#[test]
fn stored_job_state_cannot_be_forged_outside_immutable_events() {
    let mut p = original();
    let req = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut p, req);
    p.production_design.paid_generation.jobs[0].state = Stage::PilotApproved;
    assert!(p.validate().is_err());
    p.production_design.paid_generation.jobs[0].state = Stage::Draft;
    p.production_design.paid_generation.jobs[0].reserved_microusd = 9;
    assert!(p.validate().is_err());
}
#[test]
fn older_project_json_reads_with_empty_ledger_and_v2_writer_cannot_accept_paid_job() {
    let p = original();
    let mut old = serde_json::to_value(&p).unwrap();
    old["production_design"]
        .as_object_mut()
        .unwrap()
        .remove("paid_generation");
    let mut restored: Project = serde_json::from_value(old).unwrap();
    restored.validate().unwrap();
    assert!(restored.production_design.paid_generation.is_empty());
    let req = spec(&restored, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut restored, req);
    let mut downgraded = restored.clone();
    downgraded.schema_version = 2;
    assert!(
        downgraded.validate().is_err(),
        "Old writers cannot discard paid authoring records"
    );
}
#[test]
fn failed_or_unknown_jobs_cannot_be_declared_budget_released_by_journal_mutation() {
    let mut p = original();
    let req = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    create(&mut p, req);
    reserve(&mut p, 1000);
    dispatch(&mut p, 1000, Some("one.task"));
    let mut forged = p.clone();
    forged.production_design.paid_generation.jobs[0].reserved_microusd = 0;
    assert!(forged.validate().is_err());
    assert_eq!(job(&p, 1000).reserved_microusd, 4_000_000);
}

#[test]
fn capability_routing_checks_kind_model_input_policy_and_consent_without_granting_execution() {
    let p = original();
    let mut request = spec(&p, 1000, Scope::Pilot, Kind::Presenter, 0);
    request.inputs = vec![d::PaidGenerationInput {
        asset_id: uid(700),
        sha256: sha('a'),
        owner_rights_note: "Owner original nonidentifying image input".into(),
    }];
    request.logical_idempotency_sha256 = request.expected_idempotency(p.id).unwrap();
    let declaration = d::DeclaredProviderGenerationCapability {
        provider_id: request.provider_id.clone(),
        model_id: request.model_id.clone(),
        model_version: request.model_version.clone(),
        capability_receipt_sha256: request.capability_receipt_sha256.clone(),
        rights_policy_sha256: request.rights_policy_sha256.clone(),
        supported_kinds: vec![Kind::Presenter, Kind::Image],
        accepted_input_media_prefixes: vec!["image/".into()],
        supports_consented_identity: false,
        maximum_declared_charge_microusd: 4_000_000,
    };
    let proposal = d::preview_paid_provider_route(&p, &request, &[declaration.clone()]).unwrap();
    assert!(proposal.matched_declared_provider);
    assert!(proposal.rights_and_input_requirements_checked);
    assert!(!proposal.provider_capability_signature_verified && !proposal.owner_execution_grant);
    assert!(!proposal.remote_generation_performed);
    let mut no = declaration.clone();
    no.accepted_input_media_prefixes = vec!["audio/".into()];
    assert!(
        !d::preview_paid_provider_route(&p, &request, &[no])
            .unwrap()
            .matched_declared_provider
    );
    let mut no = declaration.clone();
    no.rights_policy_sha256 = sha('f');
    assert!(
        !d::preview_paid_provider_route(&p, &request, &[no])
            .unwrap()
            .matched_declared_provider
    );
    let mut no = declaration.clone();
    no.model_version = "old".into();
    assert!(
        !d::preview_paid_provider_route(&p, &request, &[no])
            .unwrap()
            .matched_declared_provider
    );
    let mut person = request.clone();
    person.identity = PaidIdentity::ConsentedPerson {
        person_asset_id: uid(700),
        person_asset_sha256: sha('a'),
        purpose: person.output_usage_terms.clone(),
        exact_consent_sha256: sha('f'),
        owner_declares_consent: true,
    };
    person.logical_idempotency_sha256 = person.expected_idempotency(p.id).unwrap();
    assert!(
        !d::preview_paid_provider_route(&p, &person, &[declaration])
            .unwrap()
            .matched_declared_provider,
        "A route declaring no identity capability must not select consented presenter tasks"
    );
}
#[test]
fn reported_provider_costs_and_reserved_unknown_budget_remain_separate_from_verified_billing() {
    let mut p = original();
    let request = spec(&p, 1000, Scope::Pilot, Kind::Video, 0);
    create(&mut p, request);
    reserve(&mut p, 1000);
    let view = p
        .production_design
        .paid_generation
        .budget_preview(&p)
        .unwrap();
    assert_eq!(view.project_reserved_microusd, 4_000_000);
    assert_eq!(view.provider_reported_cost_microusd, 0);
    assert_eq!(view.unresolved_reserved_microusd, 4_000_000);
    assert!(!view.actual_payment_settled && !view.cost_reports_are_provider_verified);
    assert!(!view.may_retry_paid_submission);
    dispatch(&mut p, 1000, Some("source.task"));
    report(
        &mut p,
        1000,
        9002,
        Some("source.task"),
        Outcome::Succeeded {
            output_sha256: sha('f'),
            reported_cost_microusd: 2_000_000,
        },
    );
    let settled = p
        .production_design
        .paid_generation
        .budget_preview(&p)
        .unwrap();
    assert_eq!(settled.project_reserved_microusd, 4_000_000);
    assert_eq!(settled.provider_reported_cost_microusd, 2_000_000);
    assert_eq!(settled.unresolved_reserved_microusd, 0);
    assert!(!settled.actual_payment_settled && !settled.cost_reports_are_provider_verified);
}
#[test]
fn unknown_json_fields_cannot_install_unapproved_paid_provider_execution() {
    let p = original();
    let req = spec(&p, 1000, Scope::Pilot, Kind::Image, 0);
    let mut json = serde_json::to_value(req).unwrap();
    json["execution_grant"] = serde_json::json!("install_paid_provider_and_charge");
    assert!(serde_json::from_value::<PaidGenerationSpec>(json).is_err());
    let mut json = serde_json::to_value(PaidGenerationEvent {
        id: uid(100),
        kind: Ev::Reserve {
            maximum_charge_microusd: 4_000_000,
        },
    })
    .unwrap();
    json["auto_launch"] = serde_json::json!(true);
    assert!(serde_json::from_value::<PaidGenerationEvent>(json).is_err());
}
