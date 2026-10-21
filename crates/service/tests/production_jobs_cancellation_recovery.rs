//! Partial CR-JOBS-04 / ACC-036 regression over persisted public service receipts.
//! No provider is contacted and no native cancellation or retry is performed.
use motionwright_domain::Project;
use motionwright_service::{
    ProductionJobState, ProductionObservationState, ProductionReceiptInput, StudioService,
};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

const JOB_REF: &str = "motion:cancel-recovery";
const ORIGINAL_START: &str = "original-start";
const PROVIDER: &str = "driver:motion-canvas";
const START: &str = "driver.motion-canvas.render.start";
const STATUS: &str = "driver.motion-canvas.render.status";
const CANCEL: &str = "driver.motion-canvas.render.cancel";

fn put(
    service: &StudioService,
    project: &Project,
    request: &str,
    command: &str,
    stage: &str,
    payload: Value,
) {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: request.into(),
            request_sha256: "ab".repeat(32),
            command: command.into(),
            stage: stage.into(),
            payload,
        })
        .unwrap();
}

fn completed(
    service: &StudioService,
    project: &Project,
    request: &str,
    command: &str,
    state: &str,
    cancellation_requested: bool,
) {
    put(
        service,
        project,
        request,
        command,
        "dispatching",
        json!({"job_ref": JOB_REF, "mutation": command == START || command == CANCEL}),
    );
    put(
        service,
        project,
        request,
        command,
        "completed",
        json!({
            "job_ref": JOB_REF,
            "result": {
                "data": {"job_ref": JOB_REF, "state": state,
                    "cancellation_requested": cancellation_requested},
                "execution": {"provenance": {
                    "provider": PROVIDER, "provider_generation": 7
                }}
            }
        }),
    );
}

fn unavailable_status(service: &StudioService, project: &Project, stage: &str) {
    assert!(matches!(stage, "failed_known" | "outcome_unknown"));
    put(
        service,
        project,
        "unavailable-status",
        STATUS,
        "dispatching",
        json!({"job_ref": JOB_REF, "mutation": false}),
    );
    put(
        service,
        project,
        "unavailable-status",
        STATUS,
        stage,
        json!({"job_ref": JOB_REF}),
    );
}

fn assert_job(
    service: &StudioService,
    project: &Project,
    expected_state: ProductionJobState,
    expected_cancel: bool,
    observation: ProductionObservationState,
) {
    let before = service.production_receipt_watermark(project.id).unwrap();
    let jobs = service.production_jobs(project.id, 16).unwrap();
    let snapshot = service
        .production_jobs_snapshot(project.id, &before)
        .unwrap();
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        before,
        "read-only projection must not alter the receipt stream"
    );
    assert_eq!(snapshot, jobs);
    assert_eq!(jobs.len(), 1);
    let job = &jobs[0];
    assert_eq!(job.job_ref, JOB_REF);
    assert_eq!(job.root_request_id, ORIGINAL_START);
    assert_eq!(job.generation, project.generation);
    assert_eq!(job.revision, project.revision);
    assert_eq!(job.provider, PROVIDER);
    assert_eq!(job.provider_generation, Some(7));
    assert_eq!(job.cancellation_requested, expected_cancel);
    assert_eq!(job.last_observation, observation);
    assert!(
        !job.artifact_available && !job.result_available,
        "status observations do not manufacture native outputs"
    );
    assert_eq!(job.state, expected_state);
}

fn fixture() -> (tempfile::TempDir, PathBuf, StudioService, Project) {
    let tmp = tempfile::tempdir().unwrap();
    let db = tmp.path().join("jobs.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let project = service.create_project("Original job project").unwrap();
    completed(&service, &project, ORIGINAL_START, START, "running", false);
    assert_job(
        &service,
        &project,
        ProductionJobState::Running,
        false,
        ProductionObservationState::Observed,
    );
    (tmp, db, service, project)
}

fn record_cancel_request_ack(service: &StudioService, project: &Project) {
    // A request is acknowledged while the original job remains nonterminal.
    completed(
        service,
        project,
        "cancel-request-ack",
        CANCEL,
        "running",
        true,
    );
    assert_job(
        service,
        project,
        ProductionJobState::CancelRequested,
        true,
        ProductionObservationState::Observed,
    );
}

fn reopen(service: StudioService, db: &Path, project: &Project) -> StudioService {
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let snapshot = service
        .production_jobs_snapshot(project.id, &watermark)
        .unwrap();
    drop(service);
    let reopened = StudioService::open(db).unwrap();
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(
        reopened
            .production_jobs_snapshot(project.id, &watermark)
            .unwrap(),
        snapshot
    );
    reopened
}

#[test]
fn cancel_request_survives_failed_status_read_and_reopen() {
    let (_tmp, db, service, project) = fixture();
    record_cancel_request_ack(&service, &project);
    unavailable_status(&service, &project, "failed_known");
    assert_job(
        &service,
        &project,
        ProductionJobState::OutcomeUnknown,
        true,
        ProductionObservationState::FailedKnown,
    );
    let service = reopen(service, &db, &project);
    assert_job(
        &service,
        &project,
        ProductionJobState::OutcomeUnknown,
        true,
        ProductionObservationState::FailedKnown,
    );
    // Read-only recovery cannot withdraw the recorded pending cancellation.
    completed(
        &service,
        &project,
        "recovered-running",
        STATUS,
        "running",
        false,
    );
    assert_job(
        &service,
        &project,
        ProductionJobState::CancelRequested,
        true,
        ProductionObservationState::Observed,
    );
}

#[test]
fn cancel_request_survives_unknown_status_and_queued_observation() {
    let (_tmp, db, service, project) = fixture();
    record_cancel_request_ack(&service, &project);
    unavailable_status(&service, &project, "outcome_unknown");
    assert_job(
        &service,
        &project,
        ProductionJobState::OutcomeUnknown,
        true,
        ProductionObservationState::OutcomeUnknown,
    );
    let service = reopen(service, &db, &project);
    assert_job(
        &service,
        &project,
        ProductionJobState::OutcomeUnknown,
        true,
        ProductionObservationState::OutcomeUnknown,
    );
    completed(
        &service,
        &project,
        "recovered-queued",
        STATUS,
        "queued",
        false,
    );
    assert_job(
        &service,
        &project,
        ProductionJobState::CancelRequested,
        true,
        ProductionObservationState::Observed,
    );
}

#[test]
fn terminal_observations_win_after_pending_cancel_request_and_unknown() {
    for (observed, expected) in [
        ("cancelled", ProductionJobState::Cancelled),
        ("succeeded", ProductionJobState::Succeeded),
        ("failed", ProductionJobState::Failed),
    ] {
        let (_tmp, db, service, project) = fixture();
        record_cancel_request_ack(&service, &project);
        unavailable_status(&service, &project, "outcome_unknown");
        assert_job(
            &service,
            &project,
            ProductionJobState::OutcomeUnknown,
            true,
            ProductionObservationState::OutcomeUnknown,
        );
        let service = reopen(service, &db, &project);
        assert_job(
            &service,
            &project,
            ProductionJobState::OutcomeUnknown,
            true,
            ProductionObservationState::OutcomeUnknown,
        );
        completed(
            &service,
            &project,
            "terminal-status",
            STATUS,
            observed,
            false,
        );
        assert_job(
            &service,
            &project,
            expected,
            true,
            ProductionObservationState::Observed,
        );
    }
}

#[test]
fn running_observation_recovers_unknown_without_inventing_cancellation() {
    let (_tmp, db, service, project) = fixture();
    unavailable_status(&service, &project, "failed_known");
    assert_job(
        &service,
        &project,
        ProductionJobState::OutcomeUnknown,
        false,
        ProductionObservationState::FailedKnown,
    );
    let service = reopen(service, &db, &project);
    assert_job(
        &service,
        &project,
        ProductionJobState::OutcomeUnknown,
        false,
        ProductionObservationState::FailedKnown,
    );
    completed(
        &service,
        &project,
        "recovered-running",
        STATUS,
        "running",
        false,
    );
    assert_job(
        &service,
        &project,
        ProductionJobState::Running,
        false,
        ProductionObservationState::Observed,
    );
}
