use motionwright_domain::*;
use std::collections::BTreeSet;
use uuid::Uuid;

fn commit(project: &mut Project, change: Change) {
    project.apply_change(&change).unwrap();
    project
        .commit_revision(project.revision + 1, &change)
        .unwrap();
}

fn setup() -> (Project, BranchCombination) {
    let mut p = Project::new("Two authored alternatives").unwrap();
    commit(
        &mut p,
        Change::AddScene {
            name: "Shared scene".into(),
            objective: "Show causality".into(),
            duration_seconds: 5,
        },
    );
    let scene_id = p.scenes[0].id;
    let node_id = Uuid::now_v7();
    commit(
        &mut p,
        Change::AddCanvasNode {
            scene_id,
            node: CanvasNode {
                id: node_id,
                name: "Persistent subject".into(),
                kind: "shape".into(),
                parent_id: None,
                x: 10.0,
                y: 20.0,
                width: 80.0,
                height: 60.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: None,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 0,
                style: NodeStyle::default(),
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            },
        },
    );
    let main = p.active_branch;
    commit(
        &mut p,
        Change::CreateBranch {
            name: "Motion alternative".into(),
        },
    );
    let donor = p.branches.last().unwrap().id;
    commit(&mut p, Change::CheckoutBranch { branch_id: donor });
    commit(
        &mut p,
        Change::TransformCanvasNode {
            scene_id,
            node_id,
            transform: CanvasTransform {
                x: 100.0,
                y: 20.0,
                width: 80.0,
                height: 60.0,
                rotation_deg: 0.0,
                opacity: 1.0,
            },
        },
    );
    commit(
        &mut p,
        Change::SetCanvasKeyframe {
            scene_id,
            node_id,
            keyframe: CanvasKeyframe {
                at: RationalTime::new(2, 1).unwrap(),
                property: MotionProperty::X,
                value: 250.0,
                interpolation: MotionInterpolation::Linear,
            },
        },
    );
    commit(&mut p, Change::CheckoutBranch { branch_id: main });
    let input = BranchCombination {
        source: p.stamp(),
        base_branch_id: main,
        base_head_revision: p
            .branches
            .iter()
            .find(|b| b.id == main)
            .unwrap()
            .head_revision,
        donor_branch_id: donor,
        donor_head_revision: p
            .branches
            .iter()
            .find(|b| b.id == donor)
            .unwrap()
            .head_revision,
        candidate_branch_id: Uuid::now_v7(),
        candidate_name: "Composition plus movement".into(),
        scene_id,
        picks: vec![BranchCombinationPick {
            node_id,
            field: BranchCombinationField::Motion,
        }],
        rationale: "Keep the current composition and use the other authored motion.".into(),
    };
    (p, input)
}

#[test]
fn combination_is_inactive_editable_distinct_and_preserves_both_sources() {
    let (mut p, input) = setup();
    let before = p.clone();
    let preview = p.preview_branch_combination(&input).unwrap();
    assert_eq!(p, before, "preview must be read only");
    assert_eq!(preview.after[0].x, 10.0);
    assert_eq!(preview.after[0].keyframes.len(), 1);
    let change = Change::CombineCreativeBranches {
        input: Box::new(input.clone()),
    };
    commit(&mut p, change);
    assert_eq!(p.active_branch, before.active_branch);
    assert_eq!(p.scenes, before.scenes);
    assert_eq!(p.audio, before.audio);
    assert_eq!(p.locks, before.locks);
    assert_eq!(p.proposal_sets, before.proposal_sets);
    assert_eq!(p.reviews, before.reviews);
    for old in &before.branch_workspaces {
        assert_eq!(
            p.branch_workspaces
                .iter()
                .find(|w| w.branch_id == old.branch_id),
            Some(old)
        );
    }
    let saved = p
        .branch_workspaces
        .iter()
        .find(|w| w.branch_id == input.candidate_branch_id)
        .unwrap();
    assert_eq!(
        saved.current_state.scenes[0].nodes[0].id,
        input.picks[0].node_id
    );
    assert_eq!(saved.current_state.scenes[0].nodes[0].x, 10.0);
    assert_eq!(saved.current_state.scenes[0].nodes[0].keyframes.len(), 1);
    assert_eq!(saved.current_state.scenes[0].status, SceneStatus::Draft);
    assert_eq!(saved.current_state.production_design.patches.len(), 1);
    assert!(
        !p.reviews
            .iter()
            .any(|r| r.anchor.branch_id == input.candidate_branch_id)
    );
    assert_eq!(
        p.branches
            .iter()
            .find(|b| b.id == input.candidate_branch_id)
            .unwrap()
            .head_revision,
        p.revision
    );
    let persisted: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    assert_eq!(persisted, p);
    commit(
        &mut p,
        Change::CheckoutBranch {
            branch_id: input.candidate_branch_id,
        },
    );
    let patch_id = p.production_design.patches[0].id;
    commit(&mut p, Change::UndoCreativePatch { patch_id });
    assert_eq!(p.scenes[0].nodes, before.scenes[0].nodes);
    assert_eq!(p.scenes[0].nodes[0].id, input.picks[0].node_id);
}

#[test]
fn stale_generation_revision_head_and_duplicate_id_are_atomic_rejections() {
    let (base, input) = setup();
    let mut alternatives = vec![];
    let mut bad = input.clone();
    bad.source.generation = Uuid::now_v7();
    alternatives.push(bad);
    let mut bad = input.clone();
    bad.source.revision += 1;
    alternatives.push(bad);
    let mut bad = input.clone();
    bad.source.resource = format!("project:{}", Uuid::now_v7());
    alternatives.push(bad);
    let mut bad = input.clone();
    bad.base_head_revision += 1;
    alternatives.push(bad);
    let mut bad = input.clone();
    bad.donor_head_revision += 1;
    alternatives.push(bad);
    let mut bad = input.clone();
    bad.base_branch_id = bad.donor_branch_id;
    alternatives.push(bad);
    let mut bad = input.clone();
    bad.candidate_branch_id = bad.base_branch_id;
    alternatives.push(bad);
    for bad in alternatives {
        let mut p = base.clone();
        assert!(
            p.apply_change(&Change::CombineCreativeBranches {
                input: Box::new(bad)
            })
            .is_err()
        );
        assert_eq!(p, base);
    }
}

#[test]
fn no_op_whole_donor_duplicate_picks_and_outside_scope_are_rejected() {
    let (base, input) = setup();
    let mut cases = vec![];
    let mut bad = input.clone();
    bad.picks.clear();
    cases.push(bad);
    let mut bad = input.clone();
    bad.picks.push(bad.picks[0].clone());
    cases.push(bad);
    let mut bad = input.clone();
    bad.picks[0].node_id = Uuid::now_v7();
    cases.push(bad);
    let mut bad = input.clone();
    bad.picks[0].field = BranchCombinationField::Style;
    cases.push(bad);
    let mut bad = input.clone();
    bad.picks.push(BranchCombinationPick {
        node_id: input.picks[0].node_id,
        field: BranchCombinationField::Transform,
    });
    cases.push(bad);
    for bad in cases {
        let mut p = base.clone();
        assert!(
            p.apply_change(&Change::CombineCreativeBranches {
                input: Box::new(bad)
            })
            .is_err()
        );
        assert_eq!(p, base);
    }
}

#[test]
fn matching_property_locks_are_not_bypassed_and_brief_changes_are_not_merged() {
    let (mut p, input) = setup();
    p.scenes[0].nodes[0]
        .property_locks
        .insert(NodeProperty::Position);
    p.branch_workspaces
        .iter_mut()
        .find(|w| w.branch_id == input.donor_branch_id)
        .unwrap()
        .current_state
        .scenes[0]
        .nodes[0]
        .property_locks
        .insert(NodeProperty::Position);
    let before = p.clone();
    assert!(
        p.apply_change(&Change::CombineCreativeBranches {
            input: Box::new(input.clone())
        })
        .is_err()
    );
    assert_eq!(p, before);
    let (mut p, input) = setup();
    p.branch_workspaces
        .iter_mut()
        .find(|w| w.branch_id == input.donor_branch_id)
        .unwrap()
        .current_state
        .brief
        .objective = "Different brief".into();
    let before = p.clone();
    assert!(
        p.apply_change(&Change::CombineCreativeBranches {
            input: Box::new(input)
        })
        .is_err()
    );
    assert_eq!(p, before);
}

#[test]
fn canonical_change_preserves_exact_parent_references_and_rejects_extra_fields() {
    let (_, input) = setup();
    let change = Change::CombineCreativeBranches {
        input: Box::new(input.clone()),
    };
    let mut wire = serde_json::to_value(&change).unwrap();
    assert_eq!(
        serde_json::from_value::<Change>(wire.clone()).unwrap(),
        change
    );
    for revision in [0, u64::MAX] {
        let mut historical_input = input.clone();
        historical_input.source.revision = revision;
        let historical = Change::CombineCreativeBranches {
            input: Box::new(historical_input),
        };
        let historical_wire = serde_json::to_string(&historical).unwrap();
        let decoded: Change = serde_json::from_str(&historical_wire).unwrap();
        assert_eq!(decoded, historical);
        assert_eq!(serde_json::to_string(&decoded).unwrap(), historical_wire);
    }
    let wire_text = serde_json::to_string(&change).unwrap();
    let duplicate_source_revision =
        wire_text.replacen("\"source\":{", "\"source\":{\"revision\":0,", 1);
    assert_ne!(duplicate_source_revision, wire_text);
    let error = serde_json::from_str::<Change>(&duplicate_source_revision).unwrap_err();
    assert!(error.to_string().contains("duplicate field `revision`"));
    let mut extra_source_field = wire.clone();
    extra_source_field["input"]["source"]["effect_grant"] = serde_json::json!(true);
    assert!(serde_json::from_value::<Change>(extra_source_field).is_err());
    wire["input"]["invented_pixels"] = serde_json::json!(true);
    assert!(serde_json::from_value::<Change>(wire).is_err());
}

#[test]
fn scene_locks_matching_undo_refuse_preview_and_apply_without_any_mutation() {
    for kind in [
        LockKind::Content,
        LockKind::Style,
        LockKind::Position,
        LockKind::Timing,
    ] {
        let (mut p, input) = setup();
        let lock = ProjectLock {
            id: Uuid::now_v7(),
            resource: format!("scene:{}", input.scene_id),
            kind,
            note: "Preserve this scene until explicit lock release".into(),
        };
        p.locks.push(lock.clone());
        p.branch_workspaces
            .iter_mut()
            .find(|w| w.branch_id == input.donor_branch_id)
            .unwrap()
            .current_state
            .locks
            .push(lock);
        p.validate().unwrap();
        let before = p.clone();
        assert!(matches!(
            p.preview_branch_combination(&input),
            Err(DomainError::Locked(_))
        ));
        assert_eq!(p, before, "refused preview preserves the complete project");
        assert!(matches!(
            p.apply_change(&Change::CombineCreativeBranches {
                input: Box::new(input)
            }),
            Err(DomainError::Locked(_))
        ));
        assert_eq!(p, before, "refused apply preserves the complete project");
    }
}

#[test]
fn non_main_active_base_retains_third_branch_heads_and_negative_review_evidence() {
    let (mut p, mut input) = setup();
    let third_branch = p.active_branch;
    commit(
        &mut p,
        Change::AddReview {
            kind: ReviewKind::Creative,
            resource: format!("scene:{}", input.scene_id),
            body:
                "Rejected as the final treatment; retain this original composition for comparison."
                    .into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
    let discarded_review = p.reviews.last().unwrap().id;
    commit(
        &mut p,
        Change::ResolveReview {
            review_id: discarded_review,
            resolution:
                "Keep the rejected source and its reason; no candidate approval is implied.".into(),
        },
    );
    commit(
        &mut p,
        Change::CreateBranch {
            name: "A separate active composition".into(),
        },
    );
    let active_base = p.branches.last().unwrap().id;
    commit(
        &mut p,
        Change::CheckoutBranch {
            branch_id: active_base,
        },
    );
    commit(
        &mut p,
        Change::AddReview {
            kind: ReviewKind::Creative,
            resource: format!("scene:{}", input.scene_id),
            body: "This base composition is ready to compare with the saved motion source.".into(),
            start: None,
            end: None,
            locale: None,
            profile_id: None,
        },
    );
    let base_review = p.reviews.last().unwrap().id;
    commit(
        &mut p,
        Change::ResolveReview {
            review_id: base_review,
            resolution: "Source comparison recorded, without approving a future derivative.".into(),
        },
    );
    input.source = p.stamp();
    input.base_branch_id = active_base;
    input.base_head_revision = p
        .branches
        .iter()
        .find(|b| b.id == active_base)
        .unwrap()
        .head_revision;
    input.donor_head_revision = p
        .branches
        .iter()
        .find(|b| b.id == input.donor_branch_id)
        .unwrap()
        .head_revision;
    let before = p.clone();
    commit(
        &mut p,
        Change::CombineCreativeBranches {
            input: Box::new(input.clone()),
        },
    );

    assert_ne!(active_base, third_branch);
    assert_eq!(p.active_branch, active_base);
    assert_eq!(p.reviews, before.reviews);
    assert_eq!(p.reviews.len(), 2);
    assert!(p.reviews.iter().all(|r| r.status == ReviewStatus::Resolved));
    assert!(
        p.reviews
            .iter()
            .all(|r| r.anchor.branch_id != input.candidate_branch_id)
    );
    for old in &before.branches {
        let mut expected = old.clone();
        if old.id == active_base {
            // The ordinary global revision commits on the active branch;
            // the donor and unrelated third branch must keep their heads.
            expected.head_revision = p.revision;
        }
        assert_eq!(p.branches.iter().find(|b| b.id == old.id), Some(&expected));
    }
    for old in &before.branch_workspaces {
        assert_eq!(
            p.branch_workspaces
                .iter()
                .find(|w| w.branch_id == old.branch_id),
            Some(old)
        );
    }
    let candidate = p
        .branches
        .iter()
        .find(|b| b.id == input.candidate_branch_id)
        .unwrap();
    assert_eq!(candidate.parent_branch, Some(active_base));
    assert_eq!(candidate.base_revision, before.revision);
    assert_eq!(candidate.head_revision, p.revision);
    let mut expected = before.clone();
    expected.revision = p.revision;
    expected.updated_at = p.updated_at;
    expected.branches = p.branches.clone();
    expected.branch_workspaces = p.branch_workspaces.clone();
    assert_eq!(
        p, expected,
        "all active authoring state and global evidence are conserved"
    );
}
