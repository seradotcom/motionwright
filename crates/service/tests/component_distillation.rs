//! Durable owner-local CAS and review acceptance for frozen semantic candidates.
use motionwright_domain::*;
use motionwright_service::StudioService;
use std::collections::BTreeSet;
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, request: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), request, &change)
        .unwrap()
        .project
}
fn node(kind: &str, text: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::new_v4(),
        name: format!("Original {kind}"),
        kind: kind.into(),
        parent_id: None,
        x: 300.0,
        y: 250.0,
        width: 450.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: if kind == "text" {
            Some(text.into())
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
fn seed(service: &StudioService) -> (Project, ComponentDistillationInput) {
    let mut project = service
        .create_project("Durable original source proposal")
        .unwrap();
    for (i, name) in ["Variation one", "Variation two", "Locked rejection"]
        .iter()
        .enumerate()
    {
        project = apply(
            service,
            &project,
            &format!("scene-{i}"),
            Change::AddScene {
                name: (*name).into(),
                objective: "Original semantic component source".into(),
                duration_seconds: 6,
            },
        );
    }
    let scenes = project
        .scenes
        .iter()
        .map(|scene| scene.id)
        .collect::<Vec<_>>();
    let mut positives = vec![];
    for i in 0..2 {
        let a = node("rectangle", "original backdrop");
        let b = node("text", if i == 0 { "Start" } else { "Continue" });
        let ids = vec![a.id, b.id];
        for (j, n) in [a, b].into_iter().enumerate() {
            project = apply(
                service,
                &project,
                &format!("node-{i}-{j}"),
                Change::AddCanvasNode {
                    scene_id: scenes[i],
                    node: n,
                },
            );
        }
        positives.push(DistillationSelector {
            scene_id: scenes[i],
            node_ids: ids,
        });
    }
    let mut negative = node("text", "Locked original");
    negative.property_locks.insert(NodeProperty::Text);
    let excluded = negative.id;
    project = apply(
        service,
        &project,
        "original-locked-node",
        Change::AddCanvasNode {
            scene_id: scenes[2],
            node: negative,
        },
    );
    let input = ComponentDistillationInput {
        id: Uuid::new_v4(),
        label: "Editable original semantic panel".into(),
        author: "Source UI creator".into(),
        original_rights_attestation: "All source panel elements were authored within this project."
            .into(),
        source_project_id: project.id,
        source_generation: project.generation,
        source_revision: project.revision,
        roles: vec![
            DistillationRole {
                key: "panel".into(),
                kind: "rectangle".into(),
            },
            DistillationRole {
                key: "headline".into(),
                kind: "text".into(),
            },
        ],
        positives,
        negatives: vec![DistillationNegative {
            scene_id: scenes[2],
            node_id: Some(excluded),
            rejection: DistillationRejection::LockedObject,
        }],
        requirements: vec!["Source-author control and human-editable Canvas typography".into()],
    };
    (project, input)
}
#[test]
fn sqlite_source_variations_replay_review_and_reopen_preserve_exact_originals() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("component-distill.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let (project, input) = seed(&service);
    let expected = project.stamp();
    let change = Change::ProposeComponentDistillation {
        input: input.clone(),
    };
    let result = service
        .apply(project.id, &expected, "source-proposal", &change)
        .unwrap();
    assert!(!result.replayed);
    let proposed = result.project.clone();
    assert_eq!(proposed.production_design.distillations.len(), 1);
    assert_eq!(
        proposed.production_design.distillations[0]
            .input
            .source_revision,
        input.source_revision
    );
    let replay = service
        .apply(project.id, &expected, "source-proposal", &change)
        .unwrap();
    assert!(
        replay.replayed,
        "repeat receipt must preserve original CAS authority"
    );
    assert_eq!(replay.project, proposed);
    assert!(
        service
            .apply(
                project.id,
                &expected,
                "stale-proposal",
                &Change::ProposeComponentDistillation {
                    input: ComponentDistillationInput {
                        id: Uuid::new_v4(),
                        ..input.clone()
                    }
                }
            )
            .is_err()
    );
    let review = DistillationReview {
        reviewer: "Independent compatible-craft reviewer".into(),
        rationale: "Structurally compatible and bounded, but not an admitted executable.".into(),
        verdict: DistillationVerdict::CompatibleProposal,
        source_revision: input.source_revision,
        compatibility: DISTILLATION_RENDERER.into(),
    };
    let reviewed = apply(
        &service,
        &proposed,
        "review-once",
        Change::ReviewComponentDistillation {
            id: input.id,
            review: review.clone(),
        },
    );
    assert_eq!(
        reviewed.production_design.distillations[0].review,
        Some(review)
    );
    assert!(reviewed.production_design.heroes.is_empty());
    assert!(reviewed.production_design.capsules.is_empty());
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), reviewed);
    let bundle = tmp.path().join("authored-candidate.motionwright");
    reopened
        .export_project_bundle(reviewed.id, &bundle)
        .unwrap();
    let imported_service = StudioService::open(tmp.path().join("fresh.sqlite3")).unwrap();
    let imported = imported_service.import_project_bundle(&bundle).unwrap();
    assert_ne!(imported.generation, reviewed.generation);
    assert_eq!(
        imported.production_design.distillations,
        reviewed.production_design.distillations
    );
    assert_eq!(imported.scenes, reviewed.scenes);
    imported.validate().unwrap();
}
