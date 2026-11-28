//! Immutable evaluator versions, source-bound observations and idempotent
//! replay use existing SQLite StudioService CAS, not a parallel review backend.
use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;

fn original_bundle() -> CreativeEvaluatorBundle {
    CreativeEvaluatorBundle {
        artifacts: None,
        schema: CREATIVE_EVALUATOR_SCHEMA.into(),
        id: Uuid::parse_str("00000000-0000-4000-8000-000000000007").unwrap(),
        version: 1,
        label: "Source-original evaluator".into(),
        purpose: "Verify authored Canvas without aesthetic scoring".into(),
        author_attestation: "I authored and may publish the original evaluation criteria".into(),
        playbook: vec![
            "Inspect native-editable source objects".into(),
            "Request independent human review".into(),
        ],
        criteria: vec![
            CreativeCriterion::MinimumNodes {
                id: Uuid::parse_str("00000000-0000-4000-8000-000000000008").unwrap(),
                minimum: 2,
            },
            CreativeCriterion::TextContains {
                id: Uuid::parse_str("00000000-0000-4000-8000-000000000010").unwrap(),
                phrase: "Original claim".into(),
            },
            CreativeCriterion::HumanQuestion {
                id: Uuid::parse_str("00000000-0000-4000-8000-000000000009").unwrap(),
                question: "Is this creative direction clear?".into(),
            },
        ],
    }
}
#[test]
fn rust_and_typescript_agree_on_exact_versioned_evaluator_source_digest() {
    assert_eq!(
        original_bundle().content_digest().unwrap(),
        "a060f3356f26c535f6bc14742ca7769541efc88827fb5443a54e033b22694056"
    );
}
#[test]
fn durable_journal_records_source_bound_technical_review_with_exact_idempotency() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("evaluator.sqlite3")).unwrap();
    let mut project = service
        .create_project("Original source evaluation")
        .unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "scene",
            &Change::AddScene {
                name: "Original content".into(),
                objective: "First-party noncaptured canvas source".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let scene_id = project.scenes[0].id;
    let bundle = original_bundle();
    let snapshot = project.stamp();
    let register = Change::RegisterCreativeEvaluator {
        bundle: bundle.clone(),
    };
    let accepted = service
        .apply(project.id, &snapshot, "evaluator-v1", &register)
        .unwrap();
    assert!(!accepted.replayed);
    assert_eq!(
        accepted.project.production_design.evaluator_bundles,
        vec![bundle.clone()]
    );
    let repeated = service
        .apply(project.id, &snapshot, "evaluator-v1", &register)
        .unwrap();
    assert!(repeated.replayed);
    assert_eq!(repeated.project, accepted.project);
    assert!(
        service
            .apply(project.id, &snapshot, "stale-evaluator", &register)
            .is_err()
    );

    let after = accepted.project;
    let run = Change::EvaluateCreativeScene {
        evaluator_id: bundle.id,
        evaluator_version: 1,
        scene_id,
    };
    let evaluation = service
        .apply(after.id, &after.stamp(), "scene-review", &run)
        .unwrap();
    assert_eq!(
        evaluation
            .project
            .production_design
            .evaluation_reports
            .len(),
        1
    );
    let report = &evaluation.project.production_design.evaluation_reports[0];
    assert_eq!(report.source_revision, after.revision);
    assert_eq!(report.evaluator_sha256, bundle.content_digest().unwrap());
    assert_eq!(report.conclusion, TechnicalConclusion::Fail);
    assert_eq!(
        report.observations[2].outcome,
        CriterionOutcome::NotAssessed
    );
    assert_eq!(report.creative_approval, "not_assessed");

    let rerun = service
        .apply(after.id, &after.stamp(), "scene-review", &run)
        .unwrap();
    assert!(rerun.replayed);
    assert_eq!(rerun.project, evaluation.project);
    drop(service);
    let restored = StudioService::open(temp.path().join("evaluator.sqlite3")).unwrap();
    assert_eq!(restored.project(project.id).unwrap(), evaluation.project);
}
#[test]
fn unknown_version_and_forged_report_cannot_mutate_atomic_source_project() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("denied.sqlite3")).unwrap();
    let mut project = service
        .create_project("Strict evaluator admission")
        .unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "scene",
            &Change::AddScene {
                name: "Original editable geometry".into(),
                objective: "Only authored native shapes".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let bundle = original_bundle();
    let stale = project.stamp();
    let attempt = Change::EvaluateCreativeScene {
        evaluator_id: bundle.id,
        evaluator_version: 1,
        scene_id: project.scenes[0].id,
    };
    assert!(
        service
            .apply(project.id, &stale, "missing-version", &attempt)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "register",
            &Change::RegisterCreativeEvaluator { bundle },
        )
        .unwrap()
        .project;
    assert!(
        service
            .apply(project.id, &stale, "stale-review", &attempt)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn actual_sqlite_creative_style_advisories_survive_fresh_service_reopen() {
    use std::collections::BTreeSet;
    let dir = tempfile::tempdir().unwrap();
    let database = dir.path().join("style06.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let mut project = service.create_project("Real source style review").unwrap();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "add-scene",
            &Change::AddScene {
                name: "Style diagnostics".into(),
                objective: "Test only actual editable authored objects".into(),
                duration_seconds: 6,
            },
        )
        .unwrap()
        .project;
    let scene_id = project.scenes[0].id;
    for index in 0..3 {
        let node = CanvasNode {
            id: Uuid::new_v4(),
            name: format!("Repeat {index}"),
            kind: "text".into(),
            parent_id: None,
            x: 150.0 + f64::from(index) * 70.0,
            y: 160.0,
            width: 440.0,
            height: 110.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: Some("The same original text".into()),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: index,
            style: NodeStyle::default(),
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: if index == 0 {
                vec![CanvasKeyframe {
                    at: RationalTime { num: 1, den: 1 },
                    property: MotionProperty::X,
                    value: 350.0,
                    interpolation: MotionInterpolation::Linear,
                }]
            } else {
                vec![]
            },
        };
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("node-{index}"),
                &Change::AddCanvasNode { scene_id, node },
            )
            .unwrap()
            .project;
    }
    let bundle = original_bundle();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "register-style",
            &Change::RegisterCreativeEvaluator {
                bundle: bundle.clone(),
            },
        )
        .unwrap()
        .project;
    let before = project.clone();
    let response = service
        .apply(
            project.id,
            &project.stamp(),
            "evaluate-style",
            &Change::EvaluateCreativeScene {
                evaluator_id: bundle.id,
                evaluator_version: 1,
                scene_id,
            },
        )
        .unwrap();
    let report = response
        .project
        .production_design
        .evaluation_reports
        .last()
        .unwrap();
    assert_eq!(report.source_revision, before.revision);
    assert_eq!(report.source_generation, before.generation);
    assert!(
        report
            .style_advisories
            .iter()
            .any(|a| a.kind == CreativeStyleKind::Repetition)
    );
    assert!(
        report
            .style_advisories
            .iter()
            .any(|a| a.kind == CreativeStyleKind::MotionPurpose)
    );
    assert!(
        report
            .style_advisories
            .iter()
            .all(|a| a.human_review_required && a.scene_id == scene_id)
    );
    assert_eq!(report.creative_approval, "not_assessed");
    let replay = service
        .apply(
            before.id,
            &before.stamp(),
            "evaluate-style",
            &Change::EvaluateCreativeScene {
                evaluator_id: bundle.id,
                evaluator_version: 1,
                scene_id,
            },
        )
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, response.project);
    drop(service);
    let reopened = StudioService::open(database).unwrap();
    assert_eq!(reopened.project(before.id).unwrap(), response.project);
    assert_eq!(
        reopened
            .history(before.id, before.revision, 10)
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn evaluator_artifacts_advance_only_for_changed_inputs_and_survive_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("evaluator-artifacts.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let project = service
        .create_project("Independent original evaluator artifacts")
        .unwrap();
    let seed = original_bundle();
    let mut wire = serde_json::to_value(&seed).unwrap();
    wire["artifacts"] = serde_json::json!({
        "instructions_version": 1,
        "examples": {"version": 1, "cases": [{
            "label": "Complete editable scene",
            "criterion_id": seed.criteria[0].id(),
            "expected": "pass"
        }]},
        "dependencies": {
            "version": 1,
            "interpreter": CREATIVE_EVALUATOR_SCHEMA,
            "inputs": ["canvas_node_roster", "canvas_text", "human_review"]
        }
    });
    let register = |current: &Project, request: &str, value: serde_json::Value| {
        service.apply(
            current.id,
            &current.stamp(),
            request,
            &Change::RegisterCreativeEvaluator {
                bundle: serde_json::from_value(value).unwrap(),
            },
        )
    };
    let v1 = register(&project, "register-v1", wire.clone())
        .unwrap()
        .project;
    let first = v1.production_design.evaluator_bundles[0].clone();
    let first_digest = first.content_digest().unwrap();

    wire["version"] = 2.into();
    wire["playbook"][0] = "Inspect original object geometry first".into();
    assert!(register(&v1, "instruction-version-reused", wire.clone()).is_err());
    wire["artifacts"]["instructions_version"] = 2.into();
    let v2 = register(&v1, "register-v2", wire.clone()).unwrap().project;

    wire["version"] = 3.into();
    wire["criteria"].as_array_mut().unwrap().remove(1);
    assert!(register(&v2, "dependency-set-changed", wire.clone()).is_err());
    wire["artifacts"]["dependencies"]["inputs"] =
        serde_json::json!(["canvas_node_roster", "human_review"]);
    assert!(register(&v2, "dependency-version-reused", wire.clone()).is_err());
    wire["artifacts"]["dependencies"]["version"] = 2.into();
    let v3 = register(&v2, "register-v3", wire.clone()).unwrap().project;
    wire["version"] = 4.into();
    let mut removed = wire.clone();
    removed.as_object_mut().unwrap().remove("artifacts");
    assert!(register(&v3, "removed-independent-artifacts", removed).is_err());
    wire["artifacts"]["examples"]["cases"][0]["label"] =
        "Readable authored title and background".into();
    assert!(register(&v3, "example-version-reused", wire.clone()).is_err());
    wire["artifacts"]["examples"]["version"] = 2.into();
    let v4 = register(&v3, "register-v4", wire).unwrap().project;
    drop(service);

    let reopened = StudioService::open(&db).unwrap().project(v4.id).unwrap();
    assert_eq!(reopened, v4);
    assert_eq!(reopened.production_design.evaluator_bundles[0], first);
    assert_eq!(
        reopened.production_design.evaluator_bundles[0]
            .content_digest()
            .unwrap(),
        first_digest
    );
}
