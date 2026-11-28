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
    for (i, scene_id) in scenes.iter().take(2).enumerate() {
        let a = node("rectangle", "original backdrop");
        let b = node("text", if i == 0 { "Start" } else { "Continue" });
        let ids = vec![a.id, b.id];
        for (j, n) in [a, b].into_iter().enumerate() {
            project = apply(
                service,
                &project,
                &format!("node-{i}-{j}"),
                Change::AddCanvasNode {
                    scene_id: *scene_id,
                    node: n,
                },
            );
        }
        positives.push(DistillationSelector {
            scene_id: *scene_id,
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

#[test]
fn reviewed_variant_creates_editable_instances_with_durable_lineage() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("instances.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let (base, input) = seed(&service);
    let project = apply(
        &service,
        &base,
        "propose",
        Change::ProposeComponentDistillation {
            input: input.clone(),
        },
    );
    let destination = project.scenes[2].id;
    let id = Uuid::new_v4();
    let panel = Uuid::new_v4();
    let headline = Uuid::new_v4();
    let data = serde_json::json!({
        "type": "instantiate_component_distillation",
        "instance": {
            "id": id,
            "proposal_id": input.id,
            "sample_scene_id": input.positives[1].scene_id,
            "scene_id": destination,
            "node_ids": [panel, headline],
            "text_overrides": {"headline": "From the retained second variant"}
        }
    });
    let change: Change = serde_json::from_value(data.clone()).unwrap();
    assert!(
        service
            .apply(project.id, &project.stamp(), "unreviewed", &change)
            .is_err()
    );
    let reviewed = apply(
        &service,
        &project,
        "review",
        Change::ReviewComponentDistillation {
            id: input.id,
            review: DistillationReview {
                reviewer: "Independent creative reviewer".into(),
                rationale: "Both originals preserve the panel and editable headline.".into(),
                verdict: DistillationVerdict::CompatibleProposal,
                source_revision: input.source_revision,
                compatibility: DISTILLATION_RENDERER.into(),
            },
        },
    );
    let mut invalid = data.clone();
    invalid["instance"]["text_overrides"] = serde_json::json!({"panel": "Not a text role"});
    let invalid: Change = serde_json::from_value(invalid).unwrap();
    assert!(
        service
            .apply(reviewed.id, &reviewed.stamp(), "invalid-role", &invalid)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), reviewed);

    let result = service
        .apply(reviewed.id, &reviewed.stamp(), "first-instance", &change)
        .unwrap();
    let saved = result.project;
    assert_eq!(
        saved.production_design.distillations,
        reviewed.production_design.distillations
    );
    let expected = &reviewed.production_design.distillations[0].samples[1].nodes;
    let mut wanted = expected.clone();
    wanted[0].id = panel;
    wanted[1].id = headline;
    wanted[1].text = Some("From the retained second variant".into());
    assert_eq!(&saved.scenes[2].nodes[1..], wanted.as_slice());
    assert!(
        service
            .apply(saved.id, &reviewed.stamp(), "first-instance", &change)
            .unwrap()
            .replayed
    );
    assert!(
        service
            .apply(saved.id, &saved.stamp(), "duplicate-instance", &change)
            .is_err()
    );

    let mut second = data.clone();
    second["instance"]["id"] = serde_json::json!(Uuid::new_v4());
    second["instance"]["node_ids"] = serde_json::json!([Uuid::new_v4(), Uuid::new_v4()]);
    second["instance"]
        .as_object_mut()
        .unwrap()
        .remove("text_overrides");
    let second: Change = serde_json::from_value(second).unwrap();
    let saved = apply(&service, &saved, "second-instance", second);
    assert_eq!(saved.scenes[2].nodes[4].text.as_deref(), Some("Continue"));
    let record = serde_json::to_value(&saved.production_design).unwrap();
    assert_eq!(record["distillation_instances"][0], data["instance"]);
    let edited = apply(
        &service,
        &saved,
        "remove-instantiated-node",
        Change::RemoveCanvasNode {
            scene_id: destination,
            node_id: headline,
        },
    );
    assert_eq!(
        serde_json::to_value(&edited.production_design).unwrap()["distillation_instances"],
        record["distillation_instances"],
    );
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    assert_eq!(reopened.project(saved.id).unwrap(), edited);
    let bundle = tmp.path().join("distilled-instances.motionwright");
    reopened.export_project_bundle(edited.id, &bundle).unwrap();
    let restored = StudioService::open(tmp.path().join("restored.sqlite3"))
        .unwrap()
        .import_project_bundle(&bundle)
        .unwrap();
    assert_eq!(restored.production_design, edited.production_design);
    assert_eq!(restored.scenes, edited.scenes);
}
