//! Source-bound profile-family save through the original SQLite-backed service.
use motionwright_domain::{
    Change, DeliverySettingPropagation, DeliverySettingTarget, SharedDeliverySetting,
};
use motionwright_service::StudioService;
use uuid::Uuid;

#[test]
fn selected_profiles_commit_as_one_revision_and_reopen_without_relabeling_other_versions() {
    let dir = tempfile::tempdir().unwrap();
    let database = dir.path().join("family.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let initial = service
        .create_project("Delivery family persistence")
        .unwrap();
    let mut source = initial.deliverables[0].clone();
    source.brand_profile = Some("New source brand".into());
    let mut project = service
        .apply(
            initial.id,
            &initial.stamp(),
            "root",
            &Change::UpsertDeliverable {
                profile: source.clone(),
            },
        )
        .unwrap()
        .project;
    let mut variants = Vec::new();
    for (name, width, height, brand) in [
        ("Vertical", 1080, 1920, "Old"),
        ("Square", 1080, 1080, "Different"),
        ("Separate", 720, 1280, "Local override"),
    ] {
        let mut profile = source.clone();
        profile.id = Uuid::new_v4();
        profile.name = name.into();
        profile.width = width;
        profile.height = height;
        profile.brand_profile = Some(brand.into());
        profile.parent_profile_id = Some(source.id);
        profile.source_revision = Some(project.revision);
        profile.adaptation_notes = vec!["Explicit aspect adaptation".into()];
        project = service
            .apply(
                project.id,
                &project.stamp(),
                name,
                &Change::UpsertDeliverable {
                    profile: profile.clone(),
                },
            )
            .unwrap()
            .project;
        variants.push(profile);
    }
    let propagation = DeliverySettingPropagation {
        project_id: project.id,
        generation: project.generation,
        branch_id: project.active_branch,
        expected_revision: project.revision,
        source_profile_id: source.id,
        source: SharedDeliverySetting::BrandProfile(source.brand_profile.clone()),
        targets: variants[..2]
            .iter()
            .map(|profile| DeliverySettingTarget {
                profile_id: profile.id,
                expected: SharedDeliverySetting::BrandProfile(profile.brand_profile.clone()),
            })
            .collect(),
    };
    let change = Change::PropagateDeliverySetting { propagation };
    let result = service
        .apply(project.id, &project.stamp(), "one-family-edit", &change)
        .unwrap()
        .project;
    assert_eq!(result.revision, project.revision + 1);
    let mut expected = project.deliverables.clone();
    for profile in &mut expected {
        if variants[..2].iter().any(|v| v.id == profile.id) {
            profile.brand_profile = source.brand_profile.clone();
        }
    }
    assert_eq!(result.deliverables, expected);
    assert!(
        service
            .apply(project.id, &result.stamp(), "new-stale-request", &change)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), result);
    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), result);
    let replay = reopened
        .apply(project.id, &project.stamp(), "one-family-edit", &change)
        .unwrap()
        .project;
    assert_eq!(replay, result);
    assert_eq!(
        reopened.project(project.id).unwrap().revision,
        result.revision
    );
}
