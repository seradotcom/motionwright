use motionwright_domain::Project;
use motionwright_service::{ProductionJobState, ProductionObservationState, StudioService};
use motionwright_storage::{ProductionReceipt, ProductionReceiptInput, StorageError};
use rusqlite::{Connection, params};
use serde_json::{Value, json};
use std::path::Path;
use uuid::Uuid;

fn append(
    service: &StudioService,
    project: &Project,
    key: &str,
    command: &str,
    stage: &str,
    payload: Value,
) -> ProductionReceipt {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: key.into(),
            request_sha256: "a".repeat(64),
            command: command.into(),
            stage: stage.into(),
            payload,
        })
        .unwrap()
}

fn observed_job(reference: &str, state: &str) -> Value {
    // Source-contract fixture only; no provider, Broker or real job is invoked.
    json!({
        "job_ref": reference,
        "result": {
            "data": {"job_ref": reference, "state": state},
            "execution": {"provenance": {
                "provider": "driver:motion-canvas", "provider_generation": 17
            }}
        }
    })
}

fn retain_advanced_clock(path: &Path, receipt: &mut ProductionReceipt) {
    let db = Connection::open(path).unwrap();
    db.execute_batch("PRAGMA foreign_keys=ON").unwrap();
    let ordinal: i64 = db
        .query_row(
            "SELECT sequence FROM production_receipt_order WHERE receipt_id=?1",
            [receipt.id.to_string()],
            |row| row.get(0),
        )
        .unwrap();
    let original_id = receipt.id;
    receipt.id = Uuid::from_u128(original_id.as_u128() + (86_400_000_u128 << 80));
    receipt.created_at = "2100-01-01T00:00:00Z".into();
    assert_eq!(
        db.execute(
            "UPDATE production_receipts SET receipt_id=?1,created_at=?2 WHERE receipt_id=?3",
            params![
                receipt.id.to_string(),
                &receipt.created_at,
                original_id.to_string()
            ],
        )
        .unwrap(),
        1
    );
    assert_eq!(
        db.query_row(
            "SELECT sequence FROM production_receipt_order WHERE receipt_id=?1",
            [receipt.id.to_string()],
            |row| row.get::<_, i64>(0),
        )
        .unwrap(),
        ordinal,
        "fixture clock change cannot assign a new ordinal"
    );
}

#[test]
fn service_job_read_failure_cannot_be_overwritten_by_an_earlier_future_clock_status() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("job-observation-order.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Committed observations").unwrap();
    append(
        &service,
        &project,
        "original-start",
        "driver.motion-canvas.render.start",
        "completed",
        observed_job("fixture-job", "queued"),
    );
    let mut running = append(
        &service,
        &project,
        "earlier-running",
        "driver.motion-canvas.render.status",
        "completed",
        observed_job("fixture-job", "running"),
    );
    drop(service);
    retain_advanced_clock(&path, &mut running);

    let service = StudioService::open(&path).unwrap();
    let failed = append(
        &service,
        &project,
        "later-read-failure",
        "driver.motion-canvas.render.status",
        "failed_known",
        json!({"job_ref":"fixture-job","outcome_known":true}),
    );
    assert!(failed.id < running.id);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    let head = reopened.production_receipt_watermark(project.id).unwrap();
    let jobs = reopened
        .production_jobs_snapshot(project.id, &head)
        .unwrap();
    assert_eq!(jobs.len(), 1);
    let job = &jobs[0];
    assert_eq!(
        job.state,
        ProductionJobState::OutcomeUnknown,
        "an older successful read must not restore a confirmed running label"
    );
    assert_eq!(
        job.last_observation,
        ProductionObservationState::FailedKnown
    );
    assert_eq!(job.last_command, "driver.motion-canvas.render.status");
    assert_eq!(job.last_observed_at, failed.created_at);
    assert_eq!(job.root_request_id, "original-start");
    assert_eq!(job.provider_generation, Some(17));
    assert_eq!(job.generation, project.generation);
    assert_eq!(job.revision, project.revision);
    assert_eq!(job.local_observations, 3);
}

#[test]
fn service_jobs_are_ranked_by_last_committed_observation_not_display_date() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("job-ranking.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Recent committed jobs").unwrap();
    let mut older = append(
        &service,
        &project,
        "older-start",
        "driver.motion-canvas.render.start",
        "completed",
        observed_job("older-job", "running"),
    );
    drop(service);
    retain_advanced_clock(&path, &mut older);
    let service = StudioService::open(&path).unwrap();
    let newer = append(
        &service,
        &project,
        "newer-start",
        "driver.motion-canvas.render.start",
        "completed",
        observed_job("newer-job", "queued"),
    );
    assert!(newer.id < older.id);
    let head = service.production_receipt_watermark(project.id).unwrap();
    let jobs = service.production_jobs_snapshot(project.id, &head).unwrap();
    assert_eq!(
        jobs.iter()
            .map(|job| job.job_ref.as_str())
            .collect::<Vec<_>>(),
        vec!["newer-job", "older-job"]
    );
    assert_eq!(
        jobs[1].created_at, older.created_at,
        "display metadata remains unchanged"
    );
}

#[test]
fn service_full_receipt_pages_keep_commit_order_and_reject_stale_snapshots() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("full-clock-pages.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let project = service.create_project("Complete receipt pages").unwrap();
    let mut committed = Vec::new();
    for index in 0..270 {
        committed.push(append(
            &service,
            &project,
            &format!("observation-{index}"),
            "driver.motion-canvas.composition.inspect",
            "completed",
            json!({"ordinal":index}),
        ));
    }
    let other = service.create_project("Foreign cursor owner").unwrap();
    let foreign = append(
        &service,
        &other,
        "foreign-observation",
        "driver.motion-canvas.composition.inspect",
        "completed",
        json!({}),
    );
    let empty = service.create_project("Empty history").unwrap();
    drop(service);
    retain_advanced_clock(&path, &mut committed[268]);
    let reopened = StudioService::open(&path).unwrap();
    let head = reopened.production_receipt_watermark(project.id).unwrap();
    assert_eq!(head.count, 270);
    let mut before = None;
    let mut observed = Vec::new();
    for _ in 0..20 {
        let page = reopened
            .production_receipts_snapshot_page(project.id, &head, before, 17)
            .unwrap();
        if page.is_empty() {
            break;
        }
        before = page.last().map(|receipt| receipt.id);
        observed.extend(page);
    }
    committed.reverse();
    assert_eq!(
        observed, committed,
        "full pages cannot omit a future-clock prior receipt"
    );
    assert!(
        reopened
            .production_receipts_snapshot_page(project.id, &head, Some(foreign.id), 17)
            .is_err()
    );
    assert!(
        reopened
            .production_receipts_snapshot_page(project.id, &head, Some(Uuid::now_v7()), 17)
            .is_err()
    );
    let empty_head = reopened.production_receipt_watermark(empty.id).unwrap();
    assert!(
        reopened
            .production_receipts_snapshot_page(empty.id, &empty_head, None, 17)
            .unwrap()
            .is_empty()
    );
    append(
        &reopened,
        &project,
        "new-observation",
        "driver.motion-canvas.composition.inspect",
        "completed",
        json!({}),
    );
    assert!(matches!(
        reopened.production_receipts_snapshot_page(project.id, &head, None, 17),
        Err(StorageError::ReceiptStreamChanged)
    ));
    assert!(matches!(
        reopened.production_jobs_snapshot(project.id, &head),
        Err(StorageError::ReceiptStreamChanged)
    ));
    assert_eq!(reopened.project(project.id).unwrap(), project);
}

#[test]
fn service_empty_receipt_history_rejects_an_unresolvable_cursor() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("empty-cursor.sqlite3")).unwrap();
    let project = service.create_project("Empty scoped history").unwrap();
    let head = service.production_receipt_watermark(project.id).unwrap();
    assert_eq!(head.count, 0);
    assert!(
        service
            .production_receipts_snapshot_page(project.id, &head, Some(Uuid::now_v7()), 1)
            .is_err(),
        "an empty stream cannot resolve any before UUID"
    );
    assert!(
        service
            .production_receipts_snapshot_page(project.id, &head, None, 1)
            .unwrap()
            .is_empty()
    );
}
