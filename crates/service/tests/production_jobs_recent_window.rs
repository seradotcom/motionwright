use motionwright_domain::Project;
use motionwright_service::{ProductionJobState, ProductionReceiptInput, StudioService};
use serde_json::json;

fn add_observation(
    service: &StudioService,
    project: &Project,
    request_id: &str,
    command: &str,
    state: &str,
) {
    let payload = json!({
        "job_ref": "mc:durable-original",
        "result": {
            "data": {"job_ref": "mc:durable-original", "state": state},
            "execution": {"provenance": {"provider_generation": 13}}
        }
    });
    for (stage, data) in [
        ("dispatching", json!({"job_ref": "mc:durable-original"})),
        ("completed", payload),
    ] {
        service
            .append_production_receipt(ProductionReceiptInput {
                project_id: project.id,
                generation: project.generation,
                revision: project.revision,
                request_id: request_id.into(),
                request_sha256: "c1".repeat(32),
                command: command.into(),
                stage: stage.into(),
                payload: data,
            })
            .unwrap();
    }
}

#[test]
fn original_job_survives_status_history_beyond_recent_256_rows_after_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let database = directory.path().join("job-history.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Long lived render history").unwrap();

    add_observation(
        &service,
        &project,
        "original-start",
        "driver.motion-canvas.render.start",
        "running",
    );
    // Exactly the shape persisted by the coordinator: each status command
    // claims one dispatch and appends the acknowledged read-only result.
    for index in 0..130 {
        add_observation(
            &service,
            &project,
            &format!("original-status-{index}"),
            "driver.motion-canvas.render.status",
            "rendering",
        );
    }
    add_observation(
        &service,
        &project,
        "original-result",
        "driver.motion-canvas.render.result",
        "succeeded",
    );

    let watermark = service.production_receipt_watermark(project.id).unwrap();
    assert_eq!(watermark.count, 264);
    let complete = service
        .production_jobs_snapshot(project.id, &watermark)
        .unwrap();
    assert_eq!(complete.len(), 1);
    assert_eq!(complete[0].state, ProductionJobState::Succeeded);
    assert_eq!(complete[0].root_request_id, "original-start");
    assert_eq!(complete[0].local_observations, 132);
    assert!(complete[0].result_available);

    // The UI must not silently project an incomplete suffix as authoritative
    // merely because the 256-row read discarded the acknowledged start.
    let visible = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(
        visible, complete,
        "Studio must use the same original job identity as the complete Native SDK snapshot"
    );
    drop(service);

    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(
        reopened.production_jobs(project.id, 16).unwrap(),
        reopened
            .production_jobs_snapshot(project.id, &watermark)
            .unwrap()
    );
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
}

#[test]
fn job_window_preserves_limit_and_does_not_write_a_new_receipt() {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("job-limit.sqlite3")).unwrap();
    let project = service.create_project("Two native jobs").unwrap();
    for (reference, identity) in [("mc:first", "first"), ("mc:second", "second")] {
        for (stage, data) in [
            ("dispatching", json!({"job_ref": reference})),
            (
                "completed",
                json!({
                    "result": {
                        "data": {"job_ref": reference, "state": "queued"},
                        "execution": {"provenance": {"provider_generation": 3}}
                    }
                }),
            ),
        ] {
            service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: identity.into(),
                    request_sha256: "ab".repeat(32),
                    command: "driver.motion-canvas.render.start".into(),
                    stage: stage.into(),
                    payload: data,
                })
                .unwrap();
        }
    }
    let before = service.production_receipt_watermark(project.id).unwrap();
    assert_eq!(before.count, 4);
    let all = service
        .production_jobs_snapshot(project.id, &before)
        .unwrap();
    assert_eq!(all.len(), 2);
    assert_eq!(service.production_jobs(project.id, 1).unwrap(), all[..1]);
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        before
    );
}
