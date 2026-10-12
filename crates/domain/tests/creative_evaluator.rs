use motionwright_domain::*;
use serde_json::json;
use std::collections::BTreeSet;
use uuid::Uuid;

fn bundle(id: Uuid, version: u32) -> CreativeEvaluatorBundle {
    CreativeEvaluatorBundle {
        schema: CREATIVE_EVALUATOR_SCHEMA.into(),
        id,
        version,
        label: "Original composition quality checklist".into(),
        purpose: "Record observable editability and request explicit editorial review".into(),
        author_attestation: "I authored these source-bound criteria and retain their rights".into(),
        playbook: vec![
            "Inspect the original editable subjects in context".into(),
            "Evaluate technical evidence separately from editorial approval".into(),
        ],
        criteria: vec![
            CreativeCriterion::MinimumNodes {
                id: Uuid::new_v4(),
                minimum: 2,
            },
            CreativeCriterion::TextContains {
                id: Uuid::new_v4(),
                phrase: "Original claim".into(),
            },
            CreativeCriterion::ProjectPixelSafeArea {
                id: Uuid::new_v4(),
                margin_permille: 50,
            },
            CreativeCriterion::HumanQuestion {
                id: Uuid::new_v4(),
                question: "Does this original scene communicate the product's point clearly?"
                    .into(),
            },
        ],
    }
}
fn project() -> (Project, Uuid, Uuid) {
    let mut project = Project::new("Source-bound evaluator smoke test").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Original scene".into(),
            objective: "Original graphical study requiring human review".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    let mut node = CanvasNode {
        id: Uuid::new_v4(),
        name: "Original narrative".into(),
        kind: "text".into(),
        parent_id: None,
        x: 290.0,
        y: 220.0,
        width: 580.0,
        height: 90.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Original claim supports editable output".into()),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    };
    let text = node.id;
    project
        .apply_change(&Change::AddCanvasNode {
            scene_id,
            node: node.clone(),
        })
        .unwrap();
    node.id = Uuid::new_v4();
    node.name = "Bounded supporting label".into();
    node.x = 290.0;
    node.y = 420.0;
    node.text = Some("Supporting original graphics".into());
    project
        .apply_change(&Change::AddCanvasNode { scene_id, node })
        .unwrap();
    (project, scene_id, text)
}
#[test]
fn evaluator_is_versioned_immutable_and_results_are_source_revision_bound() {
    let (mut p, scene_id, text) = project();
    let id = Uuid::new_v4();
    let first = bundle(id, 1);
    let digest = first.content_digest().unwrap();
    p.apply_change(&Change::RegisterCreativeEvaluator {
        bundle: first.clone(),
    })
    .unwrap();
    let previous = p.revision;
    p.apply_change(&Change::EvaluateCreativeScene {
        evaluator_id: id,
        evaluator_version: 1,
        scene_id,
    })
    .unwrap();
    let report = p.production_design.evaluation_reports[0].clone();
    assert_eq!(report.source_revision, previous);
    assert_eq!(report.evaluator_sha256, digest);
    assert_eq!(report.conclusion, TechnicalConclusion::NeedsHumanReview);
    assert_eq!(report.creative_approval, "not_assessed");
    assert_eq!(
        report
            .observations
            .iter()
            .map(|o| o.outcome)
            .collect::<Vec<_>>(),
        vec![
            CriterionOutcome::Pass,
            CriterionOutcome::Pass,
            CriterionOutcome::Pass,
            CriterionOutcome::NotAssessed
        ]
    );

    let mut replacement = first.clone();
    replacement.version = 2;
    replacement.playbook[0] = "Inspect a differently authored source.".into();
    p.apply_change(&Change::RegisterCreativeEvaluator {
        bundle: replacement.clone(),
    })
    .unwrap();
    assert_eq!(p.production_design.evaluator_bundles.len(), 2);
    assert_eq!(p.production_design.evaluator_bundles[0], first);
    assert_eq!(p.production_design.evaluation_reports[0], report);
    assert_ne!(replacement.content_digest().unwrap(), digest);
    assert!(
        p.apply_change(&Change::RegisterCreativeEvaluator {
            bundle: replacement
        })
        .is_err()
    );
    let source = p.revision;
    p.apply_change(&Change::UpdateCanvasText {
        scene_id,
        node_id: text,
        text: Some("New unrelated copy".into()),
    })
    .unwrap();
    p.apply_change(&Change::EvaluateCreativeScene {
        evaluator_id: id,
        evaluator_version: 1,
        scene_id,
    })
    .unwrap();
    let newer = &p.production_design.evaluation_reports[1];
    assert!(newer.source_revision > source);
    assert_eq!(newer.conclusion, TechnicalConclusion::Fail);
    assert_eq!(newer.observations[1].outcome, CriterionOutcome::Fail);
    assert_eq!(p.production_design.evaluation_reports[0], report);
    p.validate().unwrap();
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(restored, p);
}
#[test]
fn missing_visual_subjects_fail_closed_and_creative_question_never_passes_automatically() {
    let (mut p, scene_id, _) = project();
    let id = Uuid::new_v4();
    let mut eval = bundle(id, 1);
    eval.criteria[0] = CreativeCriterion::MinimumNodes {
        id: eval.criteria[0].id(),
        minimum: 5,
    };
    p.apply_change(&Change::RegisterCreativeEvaluator { bundle: eval })
        .unwrap();
    p.apply_change(&Change::EvaluateCreativeScene {
        evaluator_id: id,
        evaluator_version: 1,
        scene_id,
    })
    .unwrap();
    let report = &p.production_design.evaluation_reports[0];
    assert_eq!(report.conclusion, TechnicalConclusion::Fail);
    assert_eq!(report.observations[0].outcome, CriterionOutcome::Fail);
    assert_eq!(
        report.observations[3].outcome,
        CriterionOutcome::NotAssessed
    );
    let mut forged = report.clone();
    forged.observations[3].outcome = CriterionOutcome::Pass;
    forged.conclusion = TechnicalConclusion::Fail;
    assert!(
        forged
            .validate(&p.production_design.evaluator_bundles)
            .is_err()
    );
    let mut forged = report.clone();
    forged.creative_approval = "approved".into();
    assert!(
        forged
            .validate(&p.production_design.evaluator_bundles)
            .is_err()
    );
}
#[test]
fn unknown_executable_fields_duplicate_ids_and_skipped_version_history_rejected() {
    let (mut p, _, _) = project();
    let id = Uuid::new_v4();
    let first = bundle(id, 1);
    let mut forged = serde_json::to_value(&first).unwrap();
    forged["execute_shell"] = json!("invoke arbitrary provider");
    assert!(serde_json::from_value::<CreativeEvaluatorBundle>(forged).is_err());
    let mut duplicate = first.clone();
    duplicate.criteria[1] = CreativeCriterion::TextContains {
        id: duplicate.criteria[0].id(),
        phrase: "Duplicate".into(),
    };
    assert!(duplicate.validate().is_err());
    let mut untrusted = first.clone();
    untrusted.author_attestation = "".into();
    assert!(untrusted.validate().is_err());
    p.apply_change(&Change::RegisterCreativeEvaluator {
        bundle: first.clone(),
    })
    .unwrap();
    let mut missing_version = first;
    missing_version.version = 3;
    assert!(
        p.apply_change(&Change::RegisterCreativeEvaluator {
            bundle: missing_version
        })
        .is_err()
    );
    assert_eq!(p.production_design.evaluator_bundles.len(), 1);
}
#[test]
fn empty_and_nonproject_coordinate_scenes_cannot_falsely_pass_safe_area_checks() {
    let (mut p, scene_id, _) = project();
    let id = Uuid::new_v4();
    let evaluator = CreativeEvaluatorBundle {
        criteria: vec![CreativeCriterion::ProjectPixelSafeArea {
            id: Uuid::new_v4(),
            margin_permille: 100,
        }],
        ..bundle(id, 1)
    };
    let scene = p.scenes.iter_mut().find(|s| s.id == scene_id).unwrap();
    scene.nodes[0].x = 0.0;
    p.apply_change(&Change::RegisterCreativeEvaluator { bundle: evaluator })
        .unwrap();
    p.apply_change(&Change::EvaluateCreativeScene {
        evaluator_id: id,
        evaluator_version: 1,
        scene_id,
    })
    .unwrap();
    assert_eq!(
        p.production_design.evaluation_reports[0].conclusion,
        TechnicalConclusion::Fail
    );
}
