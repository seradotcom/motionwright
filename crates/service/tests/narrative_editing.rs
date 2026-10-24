//! Actual StudioService + SQLite. Synthetic authored scenario data is sent only
//! through ordinary Changes; no saved Project or feasibility result is mocked.
use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;

fn q(n: i64, d: i64) -> RationalTime {
    RationalTime::new(n, d).unwrap()
}
fn apply(service: &StudioService, p: &Project, change: Change) -> Project {
    service
        .apply(p.id, &p.stamp(), &Uuid::new_v4().to_string(), &change)
        .unwrap()
        .project
}
fn edit(service: &StudioService, p: &Project, action: NarrativeEdit) -> Project {
    apply(
        service,
        p,
        Change::EditNarrative {
            expected: NarrativeEditSource::from(p),
            edit: Box::new(action),
        },
    )
}
fn state(p: &Project) -> &NarrativeEditingState {
    p.narrative.editing.as_ref().unwrap()
}
fn seed(service: &StudioService) -> Project {
    let mut p = service.create_project("Editorial service oracle").unwrap();
    for name in ["First realization", "Second realization"] {
        p = apply(
            service,
            &p,
            Change::AddScene {
                name: name.into(),
                objective: "Existing authored realization".into(),
                duration_seconds: 6,
            },
        );
    }
    for label in ["Question", "Evidence", "Decision", "Aftercare"] {
        p = apply(
            service,
            &p,
            Change::AddNarrativeBeat {
                label: label.into(),
                objective: format!("Understanding {label}"),
                audience_takeaway: format!("Takeaway {label}"),
                claim_ids: vec![],
                preferred_duration: None,
            },
        );
    }
    p
}
fn set_script(service: &StudioService, p: &Project, raw: &str) -> Project {
    let current = p
        .narrative
        .editing
        .as_ref()
        .and_then(|value| value.script.as_ref());
    edit(
        service,
        p,
        NarrativeEdit::SetScript {
            script_id: current.map(|value| value.id).unwrap_or_else(Uuid::new_v4),
            expected_version: current.map(|value| value.version),
            text: raw.into(),
        },
    )
}
fn approve(service: &StudioService, p: &Project) -> Project {
    let script = state(p).script.as_ref().unwrap();
    edit(
        service,
        p,
        NarrativeEdit::ApproveScript {
            approval_id: Uuid::new_v4(),
            script_id: script.id,
            expected_version: script.version,
            expected_sha256: script.sha256.clone(),
            reviewer: "Synthetic scenario author".into(),
            reason: "Record exact content decision; not an artistic verdict".into(),
        },
    )
}
fn rejected_without_event(service: &StudioService, p: &Project, change: Change) {
    let journal = service.history(p.id, 0, 1024).unwrap();
    assert!(
        service
            .apply(p.id, &p.stamp(), &Uuid::new_v4().to_string(), &change)
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), *p);
    assert_eq!(service.history(p.id, 0, 1024).unwrap(), journal);
}

#[test]
fn editorial_reorder_is_one_durable_revision_with_original_ids_and_scoped_relation_impact() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("editorial-reorder.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = seed(&service);
    let order: Vec<_> = p.narrative.beats.iter().map(|beat| beat.id).collect();
    let changed = NarrativeRelation {
        id: Uuid::new_v4(),
        from_beat_id: order[0],
        to_beat_id: order[1],
        kind: NarrativeRelationKind::Adjacent,
        reason: "Question prepares Evidence".into(),
    };
    let unchanged = NarrativeRelation {
        id: Uuid::new_v4(),
        from_beat_id: order[2],
        to_beat_id: order[3],
        kind: NarrativeRelationKind::Precedes,
        reason: "Independent Decision before Aftercare".into(),
    };
    for relation in [changed.clone(), unchanged.clone()] {
        p = edit(&service, &p, NarrativeEdit::UpsertRelation { relation });
    }
    for (beat_id, scene_id) in [(order[0], p.scenes[0].id), (order[1], p.scenes[1].id)] {
        p = edit(
            &service,
            &p,
            NarrativeEdit::UpsertBinding {
                binding: NarrativeRealizationBinding {
                    id: Uuid::new_v4(),
                    beat_id,
                    target: NarrativeRealizationTarget::Scene { scene_id },
                },
            },
        );
    }
    let before = p.clone();
    let after = vec![order[1], order[0], order[2], order[3]];
    let command = Change::EditNarrative {
        expected: NarrativeEditSource::from(&p),
        edit: Box::new(NarrativeEdit::Reorder {
            expected_order: order,
            ordered_ids: after.clone(),
        }),
    };
    p = apply(&service, &p, command.clone());
    assert_eq!(p.revision, before.revision + 1);
    assert_eq!(
        p.narrative
            .beats
            .iter()
            .map(|beat| beat.id)
            .collect::<Vec<_>>(),
        after
    );
    for original in &before.narrative.beats {
        assert_eq!(
            p.narrative.beats.iter().find(|beat| beat.id == original.id),
            Some(original)
        );
    }
    assert_eq!(state(&p).relations, state(&before).relations);
    assert_eq!(state(&p).bindings, state(&before).bindings);
    let impact = state(&p).last_reorder.as_ref().unwrap();
    assert_eq!(impact.affected_relations.len(), 1);
    assert_eq!(impact.affected_relations[0].relation_id, changed.id);
    assert_eq!(impact.conflicting_relation_ids, vec![changed.id]);
    assert!(
        !impact
            .affected_relations
            .iter()
            .any(|value| value.relation_id == unchanged.id)
    );
    assert_eq!(impact.affected_binding_ids.len(), 2);
    assert_eq!(impact.affected_scene_ids.len(), 2);
    assert_eq!(p.scenes, before.scenes);
    assert_eq!(p.audio, before.audio);
    assert_eq!(p.assets, before.assets);
    let events = service.history(p.id, before.revision, 20).unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].change, command);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    assert_eq!(reopened.history(p.id, before.revision, 20).unwrap(), events);
    drop(reopened);
}

#[test]
fn approved_editorial_script_and_conflicting_duration_proposal_reopen_without_changing_voice_or_text()
 {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("editorial-approved-conflict.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = seed(&service);
    let raw = "  Approved editorial wording.\nA deliberate pause stays written.  \n";
    p = set_script(&service, &p, raw);
    p = approve(&service, &p);
    let scene_id = p.scenes[0].id;
    p = apply(
        &service,
        &p,
        Change::SetLock {
            resource: format!("scene:{scene_id}"),
            kind: LockKind::Timing,
            note: "Fixed authored duration".into(),
        },
    );
    let before = p.clone();
    let proposal_id = Uuid::new_v4();
    p = edit(
        &service,
        &p,
        NarrativeEdit::ProposeDuration {
            proposal_id,
            scene_id,
            requested_duration: q(4, 1),
            reason: "Retain the timing conflict without rewriting words".into(),
        },
    );
    let proposal = &state(&p).timing_proposals[0];
    assert!(!proposal.feasibility.command_allowed);
    assert!(
        proposal
            .feasibility
            .rejection
            .as_ref()
            .unwrap()
            .contains(&scene_id.to_string())
    );
    assert_eq!(proposal.script_snapshot, state(&before).script);
    assert!(proposal.decision.is_none());
    assert_eq!(state(&p).script, state(&before).script);
    assert_eq!(state(&p).script.as_ref().unwrap().text, raw);
    assert_eq!(p.audio, before.audio);
    assert_eq!(p.scenes, before.scenes);
    assert_eq!(
        p.production_design.narration_take_lock,
        before.production_design.narration_take_lock
    );
    let script = state(&p).script.as_ref().unwrap();
    rejected_without_event(
        &service,
        &p,
        Change::EditNarrative {
            expected: NarrativeEditSource::from(&p),
            edit: Box::new(NarrativeEdit::SetScript {
                script_id: script.id,
                expected_version: Some(script.version),
                text: "Silent shortening".into(),
            }),
        },
    );
    rejected_without_event(
        &service,
        &p,
        Change::EditNarrative {
            expected: NarrativeEditSource::from(&p),
            edit: Box::new(NarrativeEdit::DecideDuration {
                proposal_id,
                decision: NarrativeTimingDecision::ApplyDuration,
                reason: "Cannot override the hard lock".into(),
            }),
        },
    );
    let events = service.history(p.id, 0, 1024).unwrap();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    assert_eq!(reopened.history(p.id, 0, 1024).unwrap(), events);
    drop(reopened);
}

#[test]
fn fresh_explicit_duration_application_preserves_script_and_stale_retained_proposal_cannot_replay()
{
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("editorial-duration-decision.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = seed(&service);
    p = set_script(&service, &p, "Words remain exact through timing decisions");
    p = approve(&service, &p);
    let script = state(&p).script.clone();
    let audio = p.audio.clone();
    let scene_id = p.scenes[0].id;
    let first = Uuid::new_v4();
    p = edit(
        &service,
        &p,
        NarrativeEdit::ProposeDuration {
            proposal_id: first,
            scene_id,
            requested_duration: q(13, 2),
            reason: "Explicit additional dwell".into(),
        },
    );
    assert!(state(&p).timing_proposals[0].feasibility.command_allowed);
    assert_eq!(p.scenes[0].duration, q(6, 1));
    p = edit(
        &service,
        &p,
        NarrativeEdit::DecideDuration {
            proposal_id: first,
            decision: NarrativeTimingDecision::ApplyDuration,
            reason: "Apply this exact reviewed candidate".into(),
        },
    );
    assert_eq!(p.scenes[0].duration, q(13, 2));
    assert_eq!(p.scenes[1].start, q(13, 2));
    assert_eq!(state(&p).script, script);
    assert_eq!(p.audio, audio);
    let second = Uuid::new_v4();
    p = edit(
        &service,
        &p,
        NarrativeEdit::ProposeDuration {
            proposal_id: second,
            scene_id,
            requested_duration: q(7, 1),
            reason: "Candidate that becomes stale".into(),
        },
    );
    p = apply(
        &service,
        &p,
        Change::SetNarrativePremise {
            premise: "An intervening explicit edit".into(),
        },
    );
    rejected_without_event(
        &service,
        &p,
        Change::EditNarrative {
            expected: NarrativeEditSource::from(&p),
            edit: Box::new(NarrativeEdit::DecideDuration {
                proposal_id: second,
                decision: NarrativeTimingDecision::ApplyDuration,
                reason: "Old proposal is not a current grant".into(),
            }),
        },
    );
    assert_eq!(state(&p).timing_proposals.len(), 2);
    assert!(state(&p).timing_proposals[1].decision.is_none());
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    drop(reopened);
}

#[test]
fn rational_hold_and_preference_remain_editable_under_timing_lock_with_explicit_unknown_and_shortfall()
 {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("editorial-preferences.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = seed(&service);
    let beat_id = p.narrative.beats[0].id;
    let scene_id = p.scenes[0].id;
    p = edit(
        &service,
        &p,
        NarrativeEdit::UpsertBinding {
            binding: NarrativeRealizationBinding {
                id: Uuid::new_v4(),
                beat_id,
                target: NarrativeRealizationTarget::Scene { scene_id },
            },
        },
    );
    p = apply(
        &service,
        &p,
        Change::SetLock {
            resource: p.resource_key(),
            kind: LockKind::Timing,
            note: "No timeline movement".into(),
        },
    );
    let before = p.clone();
    let mut beat = p.narrative.beats[0].clone();
    beat.preferred_duration = Some(q(11, 2));
    p = edit(&service, &p, NarrativeEdit::UpdateBeat { beat });
    p = edit(
        &service,
        &p,
        NarrativeEdit::SetMinimumHold {
            beat_id,
            duration: Some(q(13, 2)),
        },
    );
    let unbound = p.narrative.beats[2].id;
    p = edit(
        &service,
        &p,
        NarrativeEdit::SetMinimumHold {
            beat_id: unbound,
            duration: Some(q(1001, 30000)),
        },
    );
    let assessments = assess_narrative_preferences(&p.narrative, &p.scenes).unwrap();
    assert_eq!(assessments[0].allocated_duration, Some(q(6, 1)));
    assert_eq!(assessments[0].minimum_shortfall, Some(q(1, 2)));
    assert_eq!(assessments[0].preferred_delta, Some(q(1, 2)));
    assert!(assessments[0].contradictory_preferences);
    assert_eq!(assessments[2].allocated_duration, None);
    assert_eq!(assessments[2].minimum_shortfall, None);
    assert_eq!(p.scenes, before.scenes);
    assert_eq!(p.locks, before.locks);
    let proposal_id = Uuid::new_v4();
    p = edit(
        &service,
        &p,
        NarrativeEdit::ProposeDuration {
            proposal_id,
            scene_id,
            requested_duration: q(7, 1),
            reason: "Preference does not unlock fixed timing".into(),
        },
    );
    assert!(!state(&p).timing_proposals[0].feasibility.command_allowed);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    drop(reopened);
}

#[test]
fn editorial_foreign_source_and_stale_service_stamp_append_no_events_or_partial_fields() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("editorial-authority.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let p = seed(&service);
    let source = NarrativeEditSource::from(&p);
    let order: Vec<_> = p.narrative.beats.iter().map(|beat| beat.id).collect();
    for dimension in 0..4 {
        let mut wrong = source.clone();
        match dimension {
            0 => wrong.project_id = Uuid::new_v4(),
            1 => wrong.generation = Uuid::new_v4(),
            2 => wrong.branch_id = Uuid::new_v4(),
            _ => wrong.base_revision += 1,
        }
        rejected_without_event(
            &service,
            &p,
            Change::EditNarrative {
                expected: wrong,
                edit: Box::new(NarrativeEdit::Reorder {
                    expected_order: order.clone(),
                    ordered_ids: order.iter().copied().rev().collect(),
                }),
            },
        );
    }
    let current = apply(
        &service,
        &p,
        Change::SetNarrativePremise {
            premise: "A concurrent owner committed first".into(),
        },
    );
    let journal = service.history(p.id, 0, 1024).unwrap();
    assert!(
        service
            .apply(
                p.id,
                &p.stamp(),
                "stale-editorial-cas",
                &Change::EditNarrative {
                    expected: source,
                    edit: Box::new(NarrativeEdit::SetScript {
                        script_id: Uuid::new_v4(),
                        expected_version: None,
                        text: "Stale replacement".into()
                    })
                }
            )
            .is_err()
    );
    assert_eq!(service.project(p.id).unwrap(), current);
    assert_eq!(service.history(p.id, 0, 1024).unwrap(), journal);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), current);
    drop(reopened);
}

#[test]
fn editorial_metadata_follows_existing_branch_snapshots_and_reopens_each_explicit_version() {
    let folder = tempfile::tempdir().unwrap();
    let path = folder.path().join("editorial-branches.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut p = seed(&service);
    p = set_script(&service, &p, "Original branch author wording");
    p = edit(
        &service,
        &p,
        NarrativeEdit::SetMinimumHold {
            beat_id: p.narrative.beats[0].id,
            duration: Some(q(1001, 30000)),
        },
    );
    let main = p.active_branch;
    let main_narrative = p.narrative.clone();
    p = apply(
        &service,
        &p,
        Change::CreateBranch {
            name: "Explicit editorial variant".into(),
        },
    );
    let branch = p.branches.last().unwrap().id;
    p = apply(&service, &p, Change::CheckoutBranch { branch_id: branch });
    assert_eq!(p.narrative, main_narrative);
    p = set_script(&service, &p, "A distinct unapproved branch wording");
    let alternate = p.narrative.clone();
    p = apply(&service, &p, Change::CheckoutBranch { branch_id: main });
    assert_eq!(p.narrative, main_narrative);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(p.id).unwrap(), p);
    p = apply(&reopened, &p, Change::CheckoutBranch { branch_id: branch });
    assert_eq!(p.narrative, alternate);
    drop(reopened);
    let final_store = StudioService::open(&path).unwrap();
    assert_eq!(final_store.project(p.id).unwrap(), p);
    drop(final_store);
}
