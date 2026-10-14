//! Immutable evaluator versions, source-bound observations and idempotent
//! replay use existing SQLite StudioService CAS, not a parallel review backend.
use motionwright_domain::*;
use motionwright_service::StudioService;
use uuid::Uuid;

fn original_bundle() -> CreativeEvaluatorBundle {
    CreativeEvaluatorBundle {
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
