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
    observation: (u64, bool),
) {
    let (generation, artifact) = observation;
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
        (17, false),
    );
    complete(
        &service,
        &project,
        "manim-1:status:0",
        &format!("{base}status"),
        "manim:stable",
        "rendering",
        (17, false),
    );
    complete(
        &service,
        &project,
        "manim-1:result",
        &format!("{base}result"),
        "manim:stable",
        "succeeded",
        (17, true),
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
        (11, false),
    );
    complete(
        &service,
        &project,
        "motion:start",
        "driver.motion-canvas.render.start",
        "shared-native-ref",
        "queued",
        (42, false),
    );
    complete(
        &service,
        &project,
        "manim:result",
        "driver.manim-community.render.result",
        "shared-native-ref",
        "succeeded",
        (11, true),
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
        (17, false),
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
        (17, false),
    );
    complete(
        &service,
        &project,
        "result",
        "driver.manim-community.render.result",
        "manim:original",
        "succeeded",
        (99, true),
    );
    let jobs = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(jobs.len(), 1);
    assert_eq!(jobs[0].state, ProductionJobState::OutcomeUnknown);
    assert_eq!(jobs[0].provider_generation, Some(17));
    assert!(!jobs[0].result_available && !jobs[0].artifact_available);
}

fn late_ack_payload(provider: &str, state: &str, observed: bool) -> Value {
    let mut data = json!({"job_ref": "original-job", "state": state});
    if observed {
        data["progress"] = json!({
            "completed": if state == "succeeded" { 24 } else { 7 },
            "total": 24,
            "message": "Original frame progress"
        });
        if state == "succeeded" {
            data["artifact"] = json!({
                "sha256": "ab".repeat(32),
                "relative_path": "media/original.mp4"
            });
        }
    } else {
        data["progress"] = json!({
            "completed": 0,
            "total": 24,
            "message": "Initial acknowledgement"
        });
    }
    json!({
        "job_ref": "original-job",
        "result": {
            "data": data,
            "execution": {
                "provenance": {
                    "provider": provider,
                    "provider_generation": 17
                }
            }
        }
    })
}

fn assert_original_start_anchor(provider: &str, late_ack: bool, terminal: bool) {
    let temporary = tempfile::tempdir().unwrap();
    let database = temporary.path().join("original-job.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let project = service
        .create_project("Original job acknowledgement")
        .unwrap();
    let prefix = if provider == "driver:manim-community" {
        "driver.manim-community.render"
    } else {
        "driver.motion-canvas.render"
    };
    let start_command = format!("{prefix}.start");
    let observation_command = format!("{prefix}.{}", if terminal { "result" } else { "status" });
    let observed_state = if terminal { "succeeded" } else { "running" };
    let start_payload = late_ack_payload(provider, "queued", false);
    let observation_payload = late_ack_payload(provider, observed_state, true);

    // Persist the original intent once. Only its acknowledgement is delayed;
    // no additional start or retry of an uncertain effect is dispatched.
    put(
        &service,
        &project,
        "original:start",
        &start_command,
        "dispatching",
        json!({"mutation": true}),
    );
    if !late_ack {
        put(
            &service,
            &project,
            "original:start",
            &start_command,
            "completed",
            start_payload.clone(),
        );
    }
    put(
        &service,
        &project,
        "original:observation",
        &observation_command,
        "dispatching",
        json!({"job_ref": "original-job", "mutation": false}),
    );
    put(
        &service,
        &project,
        "original:observation",
        &observation_command,
        "completed",
        observation_payload.clone(),
    );

    if late_ack {
        let before_ack = service.production_jobs(project.id, 16).unwrap();
        assert_eq!(before_ack.len(), 1);
        assert_eq!(before_ack[0].state, ProductionJobState::OutcomeUnknown);
        assert!(!before_ack[0].artifact_available && !before_ack[0].result_available);
        assert!(before_ack[0].progress.is_none());

        put(
            &service,
            &project,
            "original:start",
            &start_command,
            "completed",
            start_payload.clone(),
        );
    }

    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let projected = service.production_jobs(project.id, 16).unwrap();
    let original = service
        .latest_production_receipt(project.id, "original:start")
        .unwrap()
        .unwrap();
    let observation = service
        .latest_production_receipt(project.id, "original:observation")
        .unwrap()
        .unwrap();
    assert_eq!(watermark.count, 4, "two intents and two known observations");
    assert_eq!(original.request_id, "original:start");
    assert_eq!(original.command, start_command);
    assert_eq!(original.stage, "completed");
    assert_eq!(original.payload, start_payload);
    assert_eq!(observation.request_id, "original:observation");
    assert_eq!(observation.command, observation_command);
    assert_eq!(observation.stage, "completed");
    assert_eq!(observation.payload, observation_payload);
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    drop(service);

    let reopened = StudioService::open(&database).unwrap();
    let rebuilt = reopened
        .production_jobs_snapshot(project.id, &watermark)
        .unwrap();
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark,
        "projection and reopen must not append or replace original receipts"
    );
    assert_eq!(reopened.project(project.id).unwrap(), project);
    assert_eq!(rebuilt, projected);
    assert_eq!(rebuilt.len(), 1);
    let job = &rebuilt[0];
    assert_eq!(job.provider, provider);
    assert_eq!(job.job_ref, "original-job");
    assert_eq!(job.generation, project.generation);
    assert_eq!(job.revision, project.revision);
    assert_eq!(job.provider_generation, Some(17));
    assert_eq!(job.local_observations, 2);
    assert_eq!(
        job.last_command,
        if late_ack {
            &start_command
        } else {
            &observation_command
        }
        .as_str()
    );
    assert_eq!(
        job.root_request_id, "original:start",
        "a status/result request is not the root of the original mutation; state={:?}, progress={:?}",
        job.state, job.progress
    );
    assert_eq!(
        job.state,
        if terminal {
            ProductionJobState::Succeeded
        } else {
            ProductionJobState::Running
        },
        "a delayed queued acknowledgement must not reset a later job observation"
    );
    let progress = job.progress.as_ref().unwrap();
    assert_eq!(progress.completed, if terminal { 24 } else { 7 });
    assert_eq!(progress.total, Some(24));
    assert_eq!(progress.message.as_deref(), Some("Original frame progress"));
    assert_eq!(job.artifact_available, terminal);
    assert_eq!(job.result_available, terminal);
    assert!(!job.cancellation_requested);
    assert_eq!(job.last_observation, ProductionObservationState::Observed);
}

#[test]
fn manim_status_before_start_ack_retains_original_root_and_running_progress_after_reopen() {
    assert_original_start_anchor("driver:manim-community", true, false);
}

#[test]
fn motion_status_before_start_ack_retains_original_root_and_running_progress_after_reopen() {
    assert_original_start_anchor("driver:motion-canvas", true, false);
}

#[test]
fn manim_result_before_start_ack_retains_original_root_and_success_after_reopen() {
    assert_original_start_anchor("driver:manim-community", true, true);
}

#[test]
fn motion_result_before_start_ack_retains_original_root_and_success_after_reopen() {
    assert_original_start_anchor("driver:motion-canvas", true, true);
}

#[test]
fn original_ack_before_observation_keeps_existing_active_and_terminal_projections() {
    for provider in ["driver:manim-community", "driver:motion-canvas"] {
        for terminal in [false, true] {
            assert_original_start_anchor(provider, false, terminal);
        }
    }
}

#[test]
fn duplicate_known_start_acknowledgements_remain_ambiguous_after_reopen() {
    for provider in ["driver:manim-community", "driver:motion-canvas"] {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("duplicate-ack.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service
            .create_project("Ambiguous start acknowledgements")
            .unwrap();
        let prefix = if provider == "driver:manim-community" {
            "driver.manim-community.render"
        } else {
            "driver.motion-canvas.render"
        };
        let start_command = format!("{prefix}.start");
        let result_command = format!("{prefix}.result");
        let acknowledgement = late_ack_payload(provider, "queued", false);
        for (key, command, payload) in [
            ("original:start", &start_command, acknowledgement.clone()),
            (
                "original:result",
                &result_command,
                late_ack_payload(provider, "succeeded", true),
            ),
        ] {
            put(
                &service,
                &project,
                key,
                command,
                "dispatching",
                json!({"job_ref": "original-job", "mutation": key == "original:start"}),
            );
            put(&service, &project, key, command, "completed", payload);
        }
        let verified = service.production_jobs(project.id, 16).unwrap();
        assert_eq!(verified.len(), 1);
        assert_eq!(verified[0].state, ProductionJobState::Succeeded);
        assert_eq!(verified[0].root_request_id, "original:start");

        // Preserve the documented ambiguity policy for repeated start acks.
        // This records a known receipt again, never another mutation dispatch.
        put(
            &service,
            &project,
            "original:start",
            &start_command,
            "completed",
            acknowledgement,
        );
        let watermark = service.production_receipt_watermark(project.id).unwrap();
        let projected = service.production_jobs(project.id, 16).unwrap();
        assert_eq!(watermark.count, 5);
        drop(service);
        let reopened = StudioService::open(&database).unwrap();
        let rebuilt = reopened
            .production_jobs_snapshot(project.id, &watermark)
            .unwrap();
        assert_eq!(rebuilt, projected);
        assert_eq!(rebuilt.len(), 1);
        assert_eq!(rebuilt[0].state, ProductionJobState::OutcomeUnknown);
        assert_eq!(
            rebuilt[0].last_observation,
            ProductionObservationState::OutcomeUnknown
        );
        assert_eq!(rebuilt[0].local_observations, 3);
        assert!(!rebuilt[0].artifact_available && !rebuilt[0].result_available);
        assert!(rebuilt[0].progress.is_none());
        assert_eq!(
            reopened.production_receipt_watermark(project.id).unwrap(),
            watermark
        );
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }
}

#[test]
fn camera_start_with_standard_observations_survives_sqlite_reopen() {
    let temporary = tempfile::tempdir().unwrap();
    let database = temporary.path().join("camera-jobs.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Saved camera render").unwrap();
    for (key, command, state, artifact) in [
        (
            "camera-1:start",
            "driver.manim-community.camera.render.start",
            "queued",
            false,
        ),
        (
            "camera-1:status:0",
            "driver.manim-community.render.status",
            "rendering",
            false,
        ),
        (
            "camera-1:result",
            "driver.manim-community.render.result",
            "succeeded",
            true,
        ),
    ] {
        complete(
            &service,
            &project,
            key,
            command,
            "manim:camera-stable",
            state,
            (17, artifact),
        );
    }
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let jobs = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(watermark.count, 6);
    assert_eq!(jobs.len(), 1);
    let job = &jobs[0];
    assert_eq!(job.provider, "driver:manim-community");
    assert_eq!(job.provider_generation, Some(17));
    assert_eq!(job.generation, project.generation);
    assert_eq!(job.revision, project.revision);
    assert_eq!(job.root_request_id, "camera-1:start");
    assert_eq!(job.job_ref, "manim:camera-stable");
    assert_eq!(job.local_observations, 3);
    assert_eq!(job.state, ProductionJobState::Succeeded);
    assert!(job.artifact_available && job.result_available);
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    drop(service);

    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(
        reopened
            .production_jobs_snapshot(project.id, &watermark)
            .unwrap(),
        jobs
    );
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(reopened.project(project.id).unwrap(), project);
}

#[test]
fn camera_early_observations_and_ambiguous_ack_preserve_unknown_without_new_start_intent() {
    let temporary = tempfile::tempdir().unwrap();
    let database = temporary.path().join("camera-ambiguous.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let project = service.create_project("Original camera intent").unwrap();
    let start = "driver.manim-community.camera.render.start";
    let acknowledgement = late_ack_payload("driver:manim-community", "queued", false);
    put(
        &service,
        &project,
        "camera-original:start",
        start,
        "dispatching",
        json!({"mutation": true}),
    );
    for (key, command, state, artifact) in [
        (
            "camera-original:status:0",
            "driver.manim-community.render.status",
            "running",
            false,
        ),
        (
            "camera-original:result",
            "driver.manim-community.render.result",
            "succeeded",
            true,
        ),
    ] {
        put(
            &service,
            &project,
            key,
            command,
            "dispatching",
            json!({"job_ref": "original-job", "mutation": false}),
        );
        put(
            &service,
            &project,
            key,
            command,
            "completed",
            late_ack_payload("driver:manim-community", state, artifact),
        );
    }
    let before_ack_watermark = service.production_receipt_watermark(project.id).unwrap();
    let before_ack = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(before_ack_watermark.count, 5);
    assert_eq!(before_ack.len(), 1);
    assert_eq!(before_ack[0].state, ProductionJobState::OutcomeUnknown);
    assert!(!before_ack[0].artifact_available && !before_ack[0].result_available);
    assert!(before_ack[0].progress.is_none());
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        before_ack_watermark
    );

    put(
        &service,
        &project,
        "camera-original:start",
        start,
        "completed",
        acknowledgement.clone(),
    );
    let acknowledged = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(acknowledged.len(), 1);
    assert_eq!(acknowledged[0].root_request_id, "camera-original:start");
    assert_eq!(acknowledged[0].state, ProductionJobState::Succeeded);
    assert_eq!(acknowledged[0].provider_generation, Some(17));

    // A repeated acknowledgement is evidence ambiguity, not permission to
    // create another start intent or promote the result to trusted success.
    put(
        &service,
        &project,
        "camera-original:start",
        start,
        "completed",
        acknowledgement,
    );
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let projected = service.production_jobs(project.id, 16).unwrap();
    assert_eq!(watermark.count, 7);
    assert_eq!(projected.len(), 1);
    assert_eq!(projected[0].state, ProductionJobState::OutcomeUnknown);
    assert_eq!(
        projected[0].last_observation,
        ProductionObservationState::OutcomeUnknown
    );
    assert_eq!(projected[0].local_observations, 4);
    assert!(!projected[0].artifact_available && !projected[0].result_available);
    assert!(projected[0].progress.is_none());
    let receipts = service.production_receipts(project.id, 32).unwrap();
    assert_eq!(
        receipts
            .iter()
            .filter(|receipt| receipt.command == start && receipt.stage == "dispatching")
            .count(),
        1,
        "projection must not append a retry of the original camera start"
    );
    drop(service);

    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(
        reopened
            .production_jobs_snapshot(project.id, &watermark)
            .unwrap(),
        projected
    );
    assert_eq!(
        serde_json::to_value(reopened.production_receipts(project.id, 32).unwrap()).unwrap(),
        serde_json::to_value(&receipts).unwrap()
    );
    assert_eq!(
        reopened.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert_eq!(reopened.project(project.id).unwrap(), project);
}
