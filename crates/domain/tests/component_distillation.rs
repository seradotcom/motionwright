use motionwright_domain::*;
use std::collections::BTreeSet;
use uuid::Uuid;

fn authored_node(kind: &str, label: &str, left: f64) -> CanvasNode {
    CanvasNode {
        id: Uuid::new_v4(),
        name: label.into(),
        kind: kind.into(),
        parent_id: None,
        x: left,
        y: 250.0,
        width: 480.0,
        height: 96.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: if kind == "text" {
            Some(label.into())
        } else {
            None
        },
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn sample() -> (Project, ComponentDistillationInput, Uuid) {
    let mut p = Project::new("Authored editable component examples").unwrap();
    for scene in ["Source A", "Source B", "Rejected: locked UI"] {
        p.apply_change(&Change::AddScene {
            name: scene.into(),
            objective: "An original editable component".into(),
            duration_seconds: 6,
        })
        .unwrap();
    }
    let scene_ids = p.scenes.iter().map(|s| s.id).collect::<Vec<_>>();
    let mut positives = Vec::new();
    for (i, scene_id) in scene_ids.iter().take(2).enumerate() {
        let rect = authored_node("rectangle", "Panel background", 220.0);
        let text = authored_node("text", if i == 0 { "Sign in" } else { "Continue" }, 270.0);
        let ids = vec![rect.id, text.id];
        for node in [rect, text] {
            p.apply_change(&Change::AddCanvasNode {
                scene_id: *scene_id,
                node,
            })
            .unwrap();
        }
        positives.push(DistillationSelector {
            scene_id: *scene_id,
            node_ids: ids,
        });
    }
    let mut excluded = authored_node("text", "Protected source", 380.0);
    excluded.property_locks.insert(NodeProperty::Text);
    let rejected_id = excluded.id;
    p.apply_change(&Change::AddCanvasNode {
        scene_id: scene_ids[2],
        node: excluded,
    })
    .unwrap();
    let input = ComponentDistillationInput {
        id: Uuid::new_v4(),
        label: "Original two-role panel".into(),
        author: "Originating design author".into(),
        original_rights_attestation: "This is original artwork I may propose for reuse.".into(),
        source_project_id: p.id,
        source_generation: p.generation,
        source_revision: p.revision,
        roles: vec![
            DistillationRole {
                key: "panel".into(),
                kind: "rectangle".into(),
            },
            DistillationRole {
                key: "heading".into(),
                kind: "text".into(),
            },
        ],
        positives,
        negatives: vec![DistillationNegative {
            scene_id: scene_ids[2],
            node_id: Some(rejected_id),
            rejection: DistillationRejection::LockedObject,
        }],
        requirements: vec![
            "Editable original Canvas text, no rasterized workaround".into(),
            "Preserve source author and reject locked source regions".into(),
        ],
    };
    (p, input, rejected_id)
}
#[test]
fn immutable_candidate_freezes_two_actual_variations_one_actual_negative_and_requirements() {
    let (mut p, input, _) = sample();
    let before = p.clone();
    p.apply_change(&Change::ProposeComponentDistillation {
        input: input.clone(),
    })
    .unwrap();
    let stored = &p.production_design.distillations[0];
    assert_eq!(stored.schema, DISTILLATION_SCHEMA);
    assert_eq!(stored.input, input);
    assert!(stored.review.is_none());
    assert_ne!(
        stored.samples[0].nodes[1].text,
        stored.samples[1].nodes[1].text
    );
    assert_eq!(
        stored.samples[0].nodes[0].id,
        input.positives[0].node_ids[0]
    );
    assert_eq!(stored.input.requirements.len(), 2);
    assert_eq!(p.scenes, before.scenes);
    assert_eq!(p.assets, before.assets);
    let serialized = serde_json::to_value(&p).unwrap();
    let restored: Project = serde_json::from_value(serialized).unwrap();
    assert_eq!(
        restored.production_design.distillations,
        p.production_design.distillations
    );
    restored.validate().unwrap();
}
#[test]
fn human_review_is_version_bound_and_never_installs_an_executable_component() {
    let (mut p, input, _) = sample();
    p.apply_change(&Change::ProposeComponentDistillation {
        input: input.clone(),
    })
    .unwrap();
    let snapshot = p.production_design.distillations[0].samples.clone();
    let review = DistillationReview {
        reviewer: "Independent design reviewer".into(),
        rationale: "Compatible for a future separate admission review; still original Canvas only."
            .into(),
        verdict: DistillationVerdict::CompatibleProposal,
        source_revision: input.source_revision,
        compatibility: DISTILLATION_RENDERER.into(),
    };
    p.apply_change(&Change::ReviewComponentDistillation {
        id: input.id,
        review: review.clone(),
    })
    .unwrap();
    assert_eq!(
        p.production_design.distillations[0].review,
        Some(review.clone())
    );
    assert_eq!(p.production_design.distillations[0].samples, snapshot);
    assert_eq!(p.production_design.heroes.len(), 0);
    assert!(p.production_design.capsules.is_empty());
    assert!(
        p.apply_change(&Change::ReviewComponentDistillation {
            id: input.id,
            review,
        })
        .is_err()
    );
    p.validate().unwrap();
}
#[test]
fn copying_scene_ids_without_real_source_variation_fails() {
    let (p, mut input, _) = sample();
    // Both real samples still contain the same shape IDs? The second one is
    // independent and has different text; forcing its content to match A
    // through a valid Canvas edit must make the samples non-distinct.
    let mut same = p.clone();
    let target = input.positives[1].node_ids[1];
    same.apply_change(&Change::UpdateCanvasText {
        scene_id: input.positives[1].scene_id,
        node_id: target,
        text: Some("Sign in".into()),
    })
    .unwrap();
    input.source_revision = same.revision;
    assert!(
        same.apply_change(&Change::ProposeComponentDistillation { input })
            .unwrap_err()
            .to_string()
            .contains("variation")
    );
}
#[test]
fn forged_source_generation_roles_locks_negative_fixtures_and_reviewers_are_rejected() {
    let (p, input, rejected) = sample();
    let variants = [
        ComponentDistillationInput {
            source_generation: Uuid::new_v4(),
            ..input.clone()
        },
        ComponentDistillationInput {
            source_revision: input.source_revision + 1,
            ..input.clone()
        },
        ComponentDistillationInput {
            author: "".into(),
            ..input.clone()
        },
        ComponentDistillationInput {
            original_rights_attestation: "".into(),
            ..input.clone()
        },
        ComponentDistillationInput {
            negatives: vec![DistillationNegative {
                scene_id: input.negatives[0].scene_id,
                node_id: Some(rejected),
                rejection: DistillationRejection::UnsupportedKind,
            }],
            ..input.clone()
        },
        ComponentDistillationInput {
            positives: vec![input.positives[0].clone(), input.positives[0].clone()],
            ..input.clone()
        },
        ComponentDistillationInput {
            roles: vec![input.roles[0].clone(), input.roles[0].clone()],
            ..input.clone()
        },
    ];
    for variant in variants {
        let mut project = p.clone();
        assert!(
            project
                .apply_change(&Change::ProposeComponentDistillation { input: variant })
                .is_err()
        );
        assert!(project.production_design.distillations.is_empty());
    }
    let mut source = p.clone();
    source
        .apply_change(&Change::ProposeComponentDistillation {
            input: input.clone(),
        })
        .unwrap();
    for review in [
        DistillationReview {
            reviewer: input.author.clone(),
            rationale: "Author self-approval".into(),
            verdict: DistillationVerdict::CompatibleProposal,
            source_revision: input.source_revision,
            compatibility: DISTILLATION_RENDERER.into(),
        },
        DistillationReview {
            reviewer: "Independent reviewer".into(),
            rationale: "Wrong source revision".into(),
            verdict: DistillationVerdict::CompatibleProposal,
            source_revision: input.source_revision + 1,
            compatibility: DISTILLATION_RENDERER.into(),
        },
        DistillationReview {
            reviewer: "Independent reviewer".into(),
            rationale: "Unrecognized provider permission".into(),
            verdict: DistillationVerdict::CompatibleProposal,
            source_revision: input.source_revision,
            compatibility: "privileged_host_execution".into(),
        },
    ] {
        let mut candidate = source.clone();
        assert!(
            candidate
                .apply_change(&Change::ReviewComponentDistillation {
                    id: input.id,
                    review
                })
                .is_err()
        );
    }
    let mut unknown = serde_json::to_value(&input).unwrap();
    unknown["post_approval_execute_code"] = serde_json::json!(true);
    assert!(serde_json::from_value::<ComponentDistillationInput>(unknown).is_err());
}
