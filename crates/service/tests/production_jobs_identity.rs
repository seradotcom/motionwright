use motionwright_domain::Project;
use motionwright_service::{
    ProductionJobState, ProductionObservationState, ProductionReceiptInput, StudioService,
};
use serde_json::{Value, json};

fn put(
    service: &StudioService,
    project: &Project,
    key: &str,
    command: &str,
    stage: &str,
    payload: Value,
) {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: key.into(),
            request_sha256: "ab".repeat(32),
            command: command.into(),
            stage: stage.into(),
            payload,
        })
        .unwrap();
}

fn complete(
    service: &StudioService,
    project: &Project,
    key: &str,
    command: &str,
    reference: &str,
    state: &str,
    generation: u64,
    artifact: bool,
) {
    put(
        service,
        project,
        key,
        command,
        "dispatching",
        json!({"job_ref": reference, "mutation": command.ends_with(".start")}),
    );
    let mut data = json!({"job_ref": reference, "state": state});
    if artifact {
        data["artifact"] =
            json!({"sha256": "ab".repeat(32), "relative_path": "media/verified.mp4"});
    }
    put(
        service,
        project,
        key,
        command,
        "completed",
        json!({
            "job_ref": reference,
            "result": {
                "data": data,
                "execution": {
                    "provenance": {
                        "provider": if command.contains("manim-community") { "driver:manim-community" } else { "driver:motion-canvas" },
                        "provider_generation": generation
                    }
                }
            }
        }),
    );
}

#[test]
fn persisted_manim_start_status_result_reappears_after_service_reopen() {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("jobs.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let project = service.create_project("Original Manim project").unwrap();
    let base = "driver.manim-community.render.";
    complete(
        &service,
        &project,
        "manim-1:start",
        &format!("{base}start"),
        "manim:stable",
        "queued",
        17,
        false,
    );
    complete(
        &service,
        &project,
        "manim-1:status:0",
        &format!("{base}status"),
        "manim:stable",
        "rendering",
        17,
        false,
    );
    complete(
        &service,
        &project,
        "manim-1:result",
        &format!("{base}result"),
        "manim:stable",
        "succeeded",
        17,
        true,
    );
    let before = service.production_receipt_watermark(project.id).unwrap();
    assert_eq!(
        before.count, 6,
        "three original stages each have a dispatch and a completed receipt"
    );
    let jobs = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(
        jobs.len(),
        1,
        "Manim receipts are persisted but not visible in production jobs"
    );
    assert_eq!(jobs[0].provider, "driver:manim-community");
    assert_eq!(jobs[0].job_ref, "manim:stable");
    assert_eq!(jobs[0].root_request_id, "manim-1:start");
    assert_eq!(jobs[0].local_observations, 3);
    assert_eq!(jobs[0].provider_generation, Some(17));
    assert_eq!(jobs[0].state, ProductionJobState::Succeeded);
    assert!(jobs[0].artifact_available && jobs[0].result_available);
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let after = reopened.production_receipt_watermark(project.id).unwrap();
    assert_eq!(after, before);
    let snapshot = reopened
        .production_jobs_snapshot(project.id, &after)
        .unwrap();
    assert_eq!(snapshot, jobs);
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        before
    );
}

#[test]
fn identical_job_references_from_two_providers_do_not_merge() {
    let tmp = tempfile::tempdir().unwrap();
    let service = StudioService::open(tmp.path().join("jobs.sqlite3")).unwrap();
    let project = service
        .create_project("Provider reference collision")
        .unwrap();
    complete(
        &service,
        &project,
        "manim:start",
        "driver.manim-community.render.start",
        "shared-native-ref",
        "queued",
        11,
        false,
    );
    complete(
        &service,
        &project,
        "motion:start",
        "driver.motion-canvas.render.start",
        "shared-native-ref",
        "queued",
        42,
        false,
    );
    complete(
        &service,
        &project,
        "manim:result",
        "driver.manim-community.render.result",
        "shared-native-ref",
        "succeeded",
        11,
        true,
    );
    let jobs = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(
        jobs.len(),
        2,
        "same job_ref in different native providers is not one authority"
    );
    let manim = jobs
        .iter()
        .find(|j| j.provider == "driver:manim-community")
        .unwrap();
    let motion = jobs
        .iter()
        .find(|j| j.provider == "driver:motion-canvas")
        .unwrap();
    assert_eq!(manim.state, ProductionJobState::Succeeded);
    assert_eq!(manim.root_request_id, "manim:start");
    assert_eq!(manim.provider_generation, Some(11));
    assert!(manim.artifact_available && manim.result_available);
    assert_eq!(motion.state, ProductionJobState::Queued);
    assert_eq!(motion.root_request_id, "motion:start");
    assert_eq!(motion.provider_generation, Some(42));
    assert!(!motion.artifact_available && !motion.result_available);
}

#[test]
fn foreign_manim_result_reference_cannot_promote_original_job() {
    let tmp = tempfile::tempdir().unwrap();
    let service = StudioService::open(tmp.path().join("jobs.sqlite3")).unwrap();
    let project = service.create_project("Manim foreign result").unwrap();
    complete(
        &service,
        &project,
        "start",
        "driver.manim-community.render.start",
        "manim:original",
        "rendering",
        17,
        false,
    );
    put(
        &service,
        &project,
        "foreign-result",
        "driver.manim-community.render.result",
        "dispatching",
        json!({"job_ref": "manim:original"}),
    );
    put(
        &service,
        &project,
        "foreign-result",
        "driver.manim-community.render.result",
        "completed",
        json!({
            "job_ref": "manim:original",
            "result": {
                "data": {"job_ref": "manim:other", "state":"succeeded", "artifact": {"sha256": "ab".repeat(32)}},
                "execution": {"provenance":{"provider":"driver:manim-community","provider_generation":17}}
            }
        }),
    );
    let jobs = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(jobs.len(), 1);
    let original = &jobs[0];
    assert_eq!(original.job_ref, "manim:original");
    assert_eq!(original.state, ProductionJobState::OutcomeUnknown);
    assert_eq!(
        original.last_observation,
        ProductionObservationState::OutcomeUnknown
    );
    assert_eq!(original.local_observations, 2);
    assert!(!original.artifact_available);
    assert!(!original.result_available);
}

#[test]
fn mismatched_manim_provider_generation_does_not_create_terminal_success() {
    let tmp = tempfile::tempdir().unwrap();
    let service = StudioService::open(tmp.path().join("jobs.sqlite3")).unwrap();
    let project = service.create_project("Manim generation drift").unwrap();
    complete(
        &service,
        &project,
        "start",
        "driver.manim-community.render.start",
        "manim:original",
        "rendering",
        17,
        false,
    );
    complete(
        &service,
        &project,
        "result",
        "driver.manim-community.render.result",
        "manim:original",
        "succeeded",
        99,
        true,
    );
    let jobs = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(jobs.len(), 1);
    assert_eq!(jobs[0].state, ProductionJobState::OutcomeUnknown);
    assert_eq!(jobs[0].provider_generation, Some(17));
    assert!(!jobs[0].result_available && !jobs[0].artifact_available);
}
