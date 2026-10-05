use motionwright_domain::{Change, Project, ReviewKind, ReviewStatus};

fn apply(project: &mut Project, change: Change) {
    let next = project.revision + 1;
    project.apply_change(&change).unwrap();
    project.commit_revision(next, &change).unwrap();
}

#[test]
fn creative_branch_checkout_isolates_state_and_three_way_merge_applies_child_change() {
    let mut project = Project::new("Branching").unwrap();
    apply(
        &mut project,
        Change::AddScene {
            name: "Opening".into(),
            objective: "Original objective".into(),
            duration_seconds: 5,
        },
    );
    let scene_id = project.scenes[0].id;
    let main_id = project.active_branch;

    apply(
        &mut project,
        Change::CreateBranch {
            name: "alternate-opening".into(),
        },
    );
    let alternate_id = project
        .branches
        .iter()
        .find(|branch| branch.name == "alternate-opening")
        .unwrap()
        .id;

    apply(
        &mut project,
        Change::CheckoutBranch {
            branch_id: alternate_id,
        },
    );
    apply(
        &mut project,
        Change::UpdateSceneObjective {
            scene_id,
            objective: "Alternate objective".into(),
        },
    );
    assert_eq!(project.scenes[0].objective, "Alternate objective");

    apply(&mut project, Change::CheckoutBranch { branch_id: main_id });
    assert_eq!(project.scenes[0].objective, "Original objective");

    apply(
        &mut project,
        Change::MergeBranch {
            source_branch_id: alternate_id,
        },
    );
    assert_eq!(project.scenes[0].objective, "Alternate objective");
    assert_eq!(project.merges.len(), 1);
    assert_eq!(project.merges[0].source_branch, alternate_id);
    assert_eq!(project.merges[0].target_branch, main_id);
    assert_eq!(project.merges[0].committed_revision, Some(project.revision));
}

#[test]
fn three_way_merge_rejects_same_field_divergence_without_overwriting_target() {
    let mut project = Project::new("Conflict").unwrap();
    apply(
        &mut project,
        Change::AddScene {
            name: "Opening".into(),
            objective: "Base".into(),
            duration_seconds: 5,
        },
    );
    let scene_id = project.scenes[0].id;
    let main_id = project.active_branch;

    apply(
        &mut project,
        Change::CreateBranch {
            name: "variant".into(),
        },
    );
    let variant_id = project
        .branches
        .iter()
        .find(|branch| branch.name == "variant")
        .unwrap()
        .id;

    apply(
        &mut project,
        Change::CheckoutBranch {
            branch_id: variant_id,
        },
    );
    apply(
        &mut project,
        Change::UpdateSceneObjective {
            scene_id,
            objective: "Variant".into(),
        },
    );
    apply(&mut project, Change::CheckoutBranch { branch_id: main_id });
    apply(
        &mut project,
        Change::UpdateSceneObjective {
            scene_id,
            objective: "Main edit".into(),
        },
    );

    let merge = Change::MergeBranch {
        source_branch_id: variant_id,
    };
    let error = project.apply_change(&merge).unwrap_err();
    assert!(error.to_string().contains("semantic merge conflict"));
    assert_eq!(project.scenes[0].objective, "Main edit");
    assert!(project.merges.is_empty());
}

#[test]
fn resolved_review_becomes_needs_recheck_after_later_content_revision() {
    let mut project = Project::new("Review").unwrap();
    apply(
        &mut project,
        Change::AddScene {
            name: "Scene".into(),
            objective: "Initial".into(),
            duration_seconds: 5,
        },
    );
    let scene_id = project.scenes[0].id;

    apply(
        &mut project,
        Change::AddReview {
            kind: ReviewKind::Creative,
            resource: format!("scene:{scene_id}"),
            body: "Composition is approved for this revision.".into(),
            start: None,
            end: None,
            locale: Some("en-US".into()),
            profile_id: None,
        },
    );
    let review_id = project.reviews[0].id;
    apply(
        &mut project,
        Change::ResolveReview {
            review_id,
            resolution: "Approved after review.".into(),
        },
    );
    assert_eq!(project.reviews[0].status, ReviewStatus::Resolved);

    apply(
        &mut project,
        Change::UpdateSceneObjective {
            scene_id,
            objective: "Changed after approval".into(),
        },
    );
    assert_eq!(project.reviews[0].status, ReviewStatus::NeedsRecheck);
    assert!(project.reviews[0].resolution.is_none());
    assert!(project.reviews[0].resolved_at.is_none());
}
