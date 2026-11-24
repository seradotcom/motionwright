use motionwright_domain::{
    Change, DeliverableProfile, DeliverySettingPropagation, DeliverySettingTarget, LockKind,
    Project, RationalTime, ReviewKind, SharedDeliverySetting,
};
use uuid::Uuid;

fn child(
    project: &mut Project,
    parent: &DeliverableProfile,
    name: &str,
    brand: &str,
) -> DeliverableProfile {
    let mut profile = parent.clone();
    profile.id = Uuid::new_v4();
    profile.name = name.into();
    profile.parent_profile_id = Some(parent.id);
    profile.source_revision = Some(project.revision);
    profile.adaptation_notes = vec!["Explicit derivative".into()];
    profile.brand_profile = Some(brand.into());
    profile.width = 1080;
    profile.height = 1920;
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: profile.clone(),
        })
        .unwrap();
    profile
}

fn fixture() -> (
    Project,
    DeliverableProfile,
    DeliverableProfile,
    DeliverableProfile,
    DeliverableProfile,
) {
    let mut project = Project::new("Delivery family").unwrap();
    let mut source = project.deliverables[0].clone();
    source.brand_profile = Some("Current campaign".into());
    source.frame_rate = RationalTime {
        num: 24000,
        den: 1001,
    };
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: source.clone(),
        })
        .unwrap();
    let first = child(&mut project, &source, "Vertical", "Original campaign");
    let mut second = child(&mut project, &first, "Locale", "Localized brand");
    second.language = "es".into();
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: second.clone(),
        })
        .unwrap();
    let keep = child(
        &mut project,
        &source,
        "Special treatment",
        "Preserve this override",
    );
    (project, source, first, second, keep)
}

fn proposal(
    project: &Project,
    source: &DeliverableProfile,
    targets: &[&DeliverableProfile],
) -> DeliverySettingPropagation {
    DeliverySettingPropagation {
        project_id: project.id,
        generation: project.generation,
        branch_id: project.active_branch,
        expected_revision: project.revision,
        source_profile_id: source.id,
        source: SharedDeliverySetting::BrandProfile(source.brand_profile.clone()),
        targets: targets
            .iter()
            .map(|profile| DeliverySettingTarget {
                profile_id: profile.id,
                expected: SharedDeliverySetting::BrandProfile(profile.brand_profile.clone()),
            })
            .collect(),
    }
}
#[track_caller]
fn reject_unchanged(project: &mut Project, propagation: DeliverySettingPropagation) {
    let before = serde_json::to_vec(project).unwrap();
    assert!(
        project
            .apply_change(&Change::PropagateDeliverySetting { propagation })
            .is_err()
    );
    assert_eq!(serde_json::to_vec(project).unwrap(), before);
}

#[test]
fn selected_multigeneration_descendants_keep_every_other_property_and_unselected_override() {
    let (mut project, source, first, second, keep) = fixture();
    let before = project.deliverables.clone();
    let propagation = proposal(&project, &source, &[&first, &second]);
    let change = Change::PropagateDeliverySetting { propagation };
    let wire = serde_json::to_string(&change).unwrap();
    let decoded: Change = serde_json::from_str(&wire).unwrap();
    project.apply_change(&decoded).unwrap();
    let mut updated_first = first;
    updated_first.brand_profile = source.brand_profile.clone();
    let mut updated_second = second;
    updated_second.brand_profile = source.brand_profile.clone();
    let expected: Vec<_> = before
        .into_iter()
        .map(|profile| {
            if profile.id == updated_first.id {
                updated_first.clone()
            } else if profile.id == updated_second.id {
                updated_second.clone()
            } else {
                profile
            }
        })
        .collect();
    assert_eq!(project.deliverables, expected);
    assert!(project.deliverables.contains(&keep));
    project.validate().unwrap();
}

#[test]
fn stale_tampered_duplicate_foreign_reviewed_and_locked_targets_are_atomic() {
    let (mut project, source, first, second, _) = fixture();
    let original = proposal(&project, &source, &[&first, &second]);
    for invalid in [
        DeliverySettingPropagation {
            generation: Uuid::new_v4(),
            ..original.clone()
        },
        DeliverySettingPropagation {
            project_id: Uuid::new_v4(),
            ..original.clone()
        },
        DeliverySettingPropagation {
            branch_id: Uuid::new_v4(),
            ..original.clone()
        },
        DeliverySettingPropagation {
            expected_revision: project.revision + 1,
            ..original.clone()
        },
        DeliverySettingPropagation {
            source: SharedDeliverySetting::BrandProfile(None),
            ..original.clone()
        },
        DeliverySettingPropagation {
            targets: vec![original.targets[0].clone(), original.targets[0].clone()],
            ..original.clone()
        },
        DeliverySettingPropagation {
            targets: vec![
                original.targets[0].clone(),
                DeliverySettingTarget {
                    profile_id: Uuid::new_v4(),
                    expected: original.targets[1].expected.clone(),
                },
            ],
            ..original.clone()
        },
        DeliverySettingPropagation {
            targets: vec![
                original.targets[0].clone(),
                DeliverySettingTarget {
                    profile_id: second.id,
                    expected: SharedDeliverySetting::FrameRate(second.frame_rate),
                },
            ],
            ..original.clone()
        },
    ] {
        reject_unchanged(&mut project, invalid);
    }
    let mut unrelated = source.clone();
    unrelated.id = Uuid::new_v4();
    unrelated.name = "Independent".into();
    unrelated.brand_profile = None;
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: unrelated.clone(),
        })
        .unwrap();
    let invalid = proposal(&project, &source, &[&first, &unrelated]);
    reject_unchanged(&mut project, invalid);
    project
        .apply_change(&Change::SetLock {
            resource: format!("deliverable:{}", second.id),
            kind: LockKind::Renderer,
            note: "Preserve the exact version".into(),
        })
        .unwrap();
    reject_unchanged(&mut project, original.clone());
    let lock_id = project.locks.last().unwrap().id;
    project
        .apply_change(&Change::RemoveLock { lock_id })
        .unwrap();
    project
        .apply_change(&Change::AddReview {
            kind: ReviewKind::Creative,
            resource: format!("deliverable:{}", second.id),
            body: "This version was reviewed".into(),
            start: None,
            end: None,
            locale: Some("es".into()),
            profile_id: Some(second.id),
        })
        .unwrap();
    reject_unchanged(&mut project, original);
}

#[test]
fn exact_frame_rate_updates_do_not_replace_brand_or_derivation() {
    let (mut project, source, mut first, _, _) = fixture();
    first.frame_rate = RationalTime { num: 30, den: 1 };
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: first.clone(),
        })
        .unwrap();
    let mut propagation = proposal(&project, &source, &[&first]);
    propagation.source = SharedDeliverySetting::FrameRate(source.frame_rate);
    propagation.targets[0].expected = SharedDeliverySetting::FrameRate(first.frame_rate);
    project
        .apply_change(&Change::PropagateDeliverySetting {
            propagation: propagation.clone(),
        })
        .unwrap();
    first.frame_rate = source.frame_rate;
    assert_eq!(
        project.deliverables.iter().find(|p| p.id == first.id),
        Some(&first)
    );
    reject_unchanged(&mut project, propagation);
}
