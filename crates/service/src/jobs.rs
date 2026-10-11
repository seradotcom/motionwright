use motionwright_storage::ProductionReceipt;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProductionJobState {
    Queued,
    Running,
    CancelRequested,
    Succeeded,
    Failed,
    Cancelled,
    OutcomeUnknown,
}

impl ProductionJobState {
    fn definitive_terminal(self) -> bool {
        matches!(self, Self::Succeeded | Self::Failed | Self::Cancelled)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProductionJobApplicability {
    Current,
    Stale,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProductionObservationState {
    Observed,
    FailedKnown,
    OutcomeUnknown,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductionJobProgress {
    pub completed: u64,
    pub total: Option<u64>,
    pub message: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductionJobProjection {
    pub job_ref: String,
    pub provider: String,
    pub root_request_id: String,
    pub generation: Uuid,
    pub revision: u64,
    pub state: ProductionJobState,
    pub cancellation_requested: bool,
    pub applicability: ProductionJobApplicability,
    pub last_command: String,
    pub created_at: String,
    pub last_observed_at: String,
    pub local_observations: u64,
    pub provider_generation: Option<u64>,
    pub progress: Option<ProductionJobProgress>,
    pub artifact_available: bool,
    pub result_available: bool,
    pub last_observation: ProductionObservationState,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RenderCommand {
    Start,
    Status,
    Cancel,
    Result,
}

fn classify(command: &str) -> Option<(&'static str, RenderCommand)> {
    let provider = if command.starts_with("driver.motion-canvas.render.") {
        "driver:motion-canvas"
    } else if command.starts_with("driver.mlt-video.render.") {
        "driver:mlt-video"
    } else {
        return None;
    };
    let kind = if command.ends_with(".render.start") {
        RenderCommand::Start
    } else if command.ends_with(".render.status") {
        RenderCommand::Status
    } else if command.ends_with(".render.cancel") {
        RenderCommand::Cancel
    } else if command.ends_with(".render.result") {
        RenderCommand::Result
    } else {
        return None;
    };
    Some((provider, kind))
}

fn completed_wrapper(receipt: &ProductionReceipt) -> Option<&Value> {
    if receipt.stage != "completed" {
        return None;
    }
    receipt.payload.get("result")
}

fn driver_data(wrapper: &Value) -> &Value {
    wrapper.get("data").unwrap_or(wrapper)
}

fn job_ref_from_data(value: &Value) -> Option<&str> {
    value
        .get("job_ref")
        .and_then(Value::as_str)
        .or_else(|| value.get("job").and_then(Value::as_str))
        .or_else(|| value.pointer("/job/id").and_then(Value::as_str))
}

fn job_ref_from_receipt(receipt: &ProductionReceipt) -> Option<&str> {
    receipt
        .payload
        .get("job_ref")
        .and_then(Value::as_str)
        .or_else(|| {
            completed_wrapper(receipt)
                .map(driver_data)
                .and_then(job_ref_from_data)
        })
}

fn state_from_data(value: &Value) -> Option<ProductionJobState> {
    let state = value
        .get("state")
        .and_then(Value::as_str)
        .or_else(|| value.pointer("/job/state").and_then(Value::as_str))?;
    match state {
        "queued" => Some(ProductionJobState::Queued),
        "starting" | "rendering" | "running" => Some(ProductionJobState::Running),
        "succeeded" | "completed" => Some(ProductionJobState::Succeeded),
        "failed" => Some(ProductionJobState::Failed),
        "cancelled" | "canceled" => Some(ProductionJobState::Cancelled),
        "unknown" | "outcome_unknown" => Some(ProductionJobState::OutcomeUnknown),
        _ => None,
    }
}

fn progress_from_data(value: &Value) -> Option<ProductionJobProgress> {
    let progress = value.get("progress")?;
    if progress.is_null() {
        return None;
    }
    let completed = progress.get("completed")?.as_u64()?;
    let total = progress.get("total").and_then(Value::as_u64);
    let message = progress
        .get("message")
        .and_then(Value::as_str)
        .map(str::to_owned);
    Some(ProductionJobProgress {
        completed,
        total,
        message,
    })
}

fn artifact_available(value: &Value) -> bool {
    value
        .get("artifact")
        .is_some_and(|artifact| !artifact.is_null())
        || value
            .get("artifacts")
            .and_then(Value::as_array)
            .is_some_and(|artifacts| !artifacts.is_empty())
}

fn provider_generation(wrapper: &Value) -> Option<u64> {
    wrapper
        .pointer("/execution/provenance/provider_generation")
        .and_then(Value::as_u64)
}

fn apply_observed_state(
    current: ProductionJobState,
    observed: ProductionJobState,
    cancellation_requested: bool,
) -> ProductionJobState {
    if current.definitive_terminal() {
        return current;
    }
    if observed.definitive_terminal() {
        return observed;
    }
    if observed == ProductionJobState::OutcomeUnknown {
        return ProductionJobState::OutcomeUnknown;
    }
    if current == ProductionJobState::OutcomeUnknown {
        return observed;
    }
    if cancellation_requested || current == ProductionJobState::CancelRequested {
        ProductionJobState::CancelRequested
    } else {
        observed
    }
}

pub(crate) fn derive_production_jobs(
    receipts: &[ProductionReceipt],
    current_generation: Uuid,
    current_revision: u64,
    limit: usize,
) -> Vec<ProductionJobProjection> {
    let mut jobs = derive_production_jobs_all(receipts, current_generation, current_revision);
    jobs.truncate(limit.clamp(1, 64));
    jobs
}

/// Full locally persisted projection for snapshot-consistent Native SDK
/// enumeration, separate from the unchanged bounded latest-window UI API.
pub(crate) fn derive_production_jobs_all(
    receipts: &[ProductionReceipt],
    current_generation: Uuid,
    current_revision: u64,
) -> Vec<ProductionJobProjection> {
    let mut ordered = receipts.to_vec();
    ordered.sort_by(|left, right| {
        left.created_at
            .cmp(&right.created_at)
            .then_with(|| left.id.cmp(&right.id))
    });

    // Only an acknowledged native render.start receipt can establish the
    // existence/identity of a started Motion Canvas job. A partial receipt
    // window or orphan result is still observable, never proof of success.
    let acknowledged_motion_starts: BTreeSet<(String, Uuid, u64)> = ordered
        .iter()
        .filter(|receipt| {
            receipt.stage == "completed" && receipt.command == "driver.motion-canvas.render.start"
        })
        .filter_map(|receipt| {
            let ref_id = completed_wrapper(receipt)
                .map(driver_data)
                .and_then(|data| data.get("job_ref"))
                .and_then(Value::as_str)?;
            if ref_id.is_empty() {
                return None;
            }
            Some((ref_id.to_owned(), receipt.generation, receipt.revision))
        })
        .collect();

    let mut jobs: BTreeMap<String, ProductionJobProjection> = BTreeMap::new();
    for receipt in &ordered {
        let Some((provider, kind)) = classify(&receipt.command) else {
            continue;
        };
        if receipt.stage == "dispatching" {
            continue;
        }
        let Some(job_ref) = job_ref_from_receipt(receipt).map(str::to_owned) else {
            continue;
        };

        let wrapper = completed_wrapper(receipt);
        let data = wrapper.map(driver_data);
        // render.status/result are reads of an acknowledged job, not a new
        // source of render authority. The current Motionwright receipt carries
        // the request's job_ref independently of the returned native JobView.
        // A foreign/missing response must not make the *original* job appear
        // succeeded (or advertise its unverified artifact) in Studio/SDK.
        let orphan_motion_observation = provider == "driver:motion-canvas"
            && !matches!(kind, RenderCommand::Start)
            && !acknowledged_motion_starts.contains(&(
                job_ref.clone(),
                receipt.generation,
                receipt.revision,
            ));
        let unbound_motion_observation = provider == "driver:motion-canvas"
            && receipt.stage == "completed"
            && matches!(kind, RenderCommand::Status | RenderCommand::Result)
            && !matches!(
                (
                    receipt.payload.get("job_ref").and_then(Value::as_str),
                    data.and_then(|view| view.get("job_ref")).and_then(Value::as_str)
                ),
                (Some(expected), Some(observed)) if expected == observed
            );
        let unbound_motion_observation = unbound_motion_observation || orphan_motion_observation;
        let cancellation_requested = !unbound_motion_observation
            && data
                .and_then(|value| value.get("cancellation_requested"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
        let observed_state = if unbound_motion_observation {
            Some(ProductionJobState::OutcomeUnknown)
        } else {
            data.and_then(state_from_data).or_else(|| {
                if receipt.stage == "outcome_unknown" {
                    Some(ProductionJobState::OutcomeUnknown)
                } else {
                    None
                }
            })
        };

        let entry = jobs.entry(job_ref.clone()).or_insert_with(|| {
            let initial_state = observed_state.unwrap_or(ProductionJobState::OutcomeUnknown);
            ProductionJobProjection {
                job_ref: job_ref.clone(),
                provider: provider.to_owned(),
                root_request_id: receipt.request_id.clone(),
                generation: receipt.generation,
                revision: receipt.revision,
                state: initial_state,
                cancellation_requested,
                applicability: ProductionJobApplicability::Current,
                last_command: receipt.command.clone(),
                created_at: receipt.created_at.clone(),
                last_observed_at: receipt.created_at.clone(),
                local_observations: 0,
                provider_generation: None,
                progress: None,
                artifact_available: false,
                result_available: false,
                last_observation: ProductionObservationState::Observed,
            }
        });

        if matches!(kind, RenderCommand::Start) {
            entry.root_request_id = receipt.request_id.clone();
            entry.generation = receipt.generation;
            entry.revision = receipt.revision;
            entry.created_at = receipt.created_at.clone();
        }
        entry.last_command = receipt.command.clone();
        entry.last_observed_at = receipt.created_at.clone();
        entry.local_observations = entry.local_observations.saturating_add(1);
        if unbound_motion_observation {
            entry.last_observation = ProductionObservationState::OutcomeUnknown;
            if !entry.state.definitive_terminal() {
                entry.state = ProductionJobState::OutcomeUnknown;
            }
            // Do not transfer the forged status, progress, generation,
            // cancellation or artifact flags into a verified job projection.
            continue;
        }

        match receipt.stage.as_str() {
            "completed" => {
                entry.last_observation = ProductionObservationState::Observed;
                entry.cancellation_requested |= cancellation_requested;
                if let Some(state) = observed_state {
                    let state = if matches!(kind, RenderCommand::Cancel)
                        && cancellation_requested
                        && !state.definitive_terminal()
                    {
                        ProductionJobState::CancelRequested
                    } else {
                        state
                    };
                    entry.state =
                        apply_observed_state(entry.state, state, entry.cancellation_requested);
                }
                if let Some(wrapper) = wrapper {
                    entry.provider_generation =
                        provider_generation(wrapper).or(entry.provider_generation);
                }
                if let Some(data) = data {
                    entry.progress = progress_from_data(data).or_else(|| entry.progress.clone());
                    entry.artifact_available |= artifact_available(data);
                }
                if matches!(kind, RenderCommand::Result) {
                    entry.result_available = true;
                }
            }
            "failed_known" => {
                entry.last_observation = ProductionObservationState::FailedKnown;
                // A failed read-only status/result call proves only that the
                // *observation* failed. It cannot prove a previously started
                // render has stopped. In particular, an inactive provider
                // generation must never leave a stale "running" label as
                // a confirmed current job state, or invent FAILED/SUCCESS.
                if matches!(kind, RenderCommand::Status | RenderCommand::Result)
                    && !entry.state.definitive_terminal()
                {
                    entry.state = ProductionJobState::OutcomeUnknown;
                }
            }
            "outcome_unknown" => {
                entry.last_observation = ProductionObservationState::OutcomeUnknown;
                entry.state = if entry.state.definitive_terminal() {
                    entry.state
                } else {
                    ProductionJobState::OutcomeUnknown
                };
            }
            _ => continue,
        }
    }

    for job in jobs.values_mut() {
        job.applicability =
            if job.generation == current_generation && job.revision == current_revision {
                ProductionJobApplicability::Current
            } else {
                ProductionJobApplicability::Stale
            };
    }

    let mut jobs: Vec<_> = jobs.into_values().collect();
    jobs.sort_by(|left, right| {
        right
            .last_observed_at
            .cmp(&left.last_observed_at)
            .then_with(|| right.job_ref.cmp(&left.job_ref))
    });
    jobs
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn receipt(
        generation: Uuid,
        revision: u64,
        request_id: &str,
        command: &str,
        stage: &str,
        payload: Value,
        created_at: &str,
    ) -> ProductionReceipt {
        ProductionReceipt {
            id: Uuid::now_v7(),
            project_id: Uuid::nil(),
            generation,
            revision,
            request_id: request_id.into(),
            request_sha256: "ab".repeat(32),
            command: command.into(),
            stage: stage.into(),
            payload,
            created_at: created_at.into(),
        }
    }

    fn wrapped_job(job: &str, state: &str, cancellation_requested: bool) -> Value {
        json!({
            "result": {
                "data": {
                    "job": job,
                    "state": state,
                    "cancellation_requested": cancellation_requested
                },
                "execution": {
                    "provenance": {
                        "provider_generation": 7
                    }
                }
            }
        })
    }

    #[test]
    fn provider_generation_loss_after_timeout_must_not_report_running_failed_or_success() {
        let generation = Uuid::now_v7();
        let receipts = vec![
            receipt(
                generation,
                7,
                "original-render-start",
                "driver.motion-canvas.render.start",
                "completed",
                json!({"result": {"data": {"job_ref":"mc:original","state":"queued"}}}),
                "2026-10-09T01:00:00Z",
            ),
            receipt(
                generation,
                7,
                "status-observed-running",
                "driver.motion-canvas.render.status",
                "completed",
                json!({"job_ref":"mc:original","result": {"data": {"job_ref":"mc:original","state":"rendering"}}}),
                "2026-10-09T01:00:02Z",
            ),
            receipt(
                generation,
                7,
                "status-timeout",
                "driver.motion-canvas.render.status",
                "outcome_unknown",
                json!({"job_ref":"mc:original","error":{"code":"Timeout","outcome_known":false}}),
                "2026-10-09T01:00:04Z",
            ),
            receipt(
                generation,
                7,
                "status-provider-inactive",
                "driver.motion-canvas.render.status",
                "failed_known",
                json!({"job_ref":"mc:original","error":{"code":"Unavailable","message":"Provider capability generation is inactive","outcome_known":true}}),
                "2026-10-09T01:00:05Z",
            ),
        ];
        let jobs = derive_production_jobs(&receipts, generation, 7, 10);
        assert_eq!(jobs.len(), 1);
        let job = &jobs[0];
        assert_eq!(job.job_ref, "mc:original");
        assert_eq!(job.state, ProductionJobState::OutcomeUnknown);
        assert_eq!(
            job.last_observation,
            ProductionObservationState::FailedKnown
        );
        assert!(!job.artifact_available);
        assert!(!job.result_available);
        assert_eq!(job.local_observations, 4);
        assert_eq!(job.root_request_id, "original-render-start");

        let no_timeout = derive_production_jobs(
            &[
                receipts[0].clone(),
                receipts[1].clone(),
                receipts[3].clone(),
            ],
            generation,
            7,
            10,
        );
        assert_eq!(no_timeout[0].state, ProductionJobState::OutcomeUnknown);

        // Only an actual, later canonical job-result observation can prove
        // native SUCCESS; a status timeout/provider crash never can.
        let mut recovered = receipts;
        recovered.push(receipt(
            generation,
            7,
            "original-render-result",
            "driver.motion-canvas.render.result",
            "completed",
            json!({"job_ref":"mc:original","result": {"data": {"job_ref":"mc:original","state":"succeeded",
                "artifact":{"manifest":"verified-canonical-frame-manifest"}}}}),
            "2026-10-09T01:00:06Z",
        ));
        let reconciled = derive_production_jobs(&recovered, generation, 7, 10);
        assert_eq!(reconciled[0].state, ProductionJobState::Succeeded);
        assert_eq!(
            reconciled[0].last_observation,
            ProductionObservationState::Observed
        );
        assert!(reconciled[0].result_available);
    }

    #[test]
    fn cancellation_request_is_not_reported_as_confirmed_cancellation() {
        let generation = Uuid::now_v7();
        let receipts = vec![
            receipt(
                generation,
                4,
                "start",
                "driver.mlt-video.render.start",
                "completed",
                wrapped_job("job-1", "running", false),
                "2026-10-05T01:00:00Z",
            ),
            receipt(
                generation,
                4,
                "cancel",
                "driver.mlt-video.render.cancel",
                "completed",
                wrapped_job("job-1", "running", true),
                "2026-10-05T01:00:01Z",
            ),
        ];
        let jobs = derive_production_jobs(&receipts, generation, 4, 16);
        assert_eq!(jobs[0].state, ProductionJobState::CancelRequested);
        assert!(jobs[0].cancellation_requested);
        assert_eq!(jobs[0].provider_generation, Some(7));
        assert_eq!(jobs[0].applicability, ProductionJobApplicability::Current);
    }

    #[test]
    fn definitive_terminal_state_cannot_be_resurrected_by_late_running_observation() {
        let generation = Uuid::now_v7();
        let receipts = vec![
            receipt(
                generation,
                3,
                "start",
                "driver.motion-canvas.render.start",
                "completed",
                json!({"result":{"data":{"job_ref":"mc-1","state":"running"}}}),
                "2026-10-05T01:00:00Z",
            ),
            receipt(
                generation,
                3,
                "result",
                "driver.motion-canvas.render.result",
                "completed",
                json!({"job_ref":"mc-1","result":{"data":{"job_ref":"mc-1","state":"succeeded","artifact":{"manifest":"frames.json"}}}}),
                "2026-10-05T01:00:02Z",
            ),
            receipt(
                generation,
                3,
                "late-status",
                "driver.motion-canvas.render.status",
                "completed",
                json!({"job_ref":"mc-1","result":{"data":{"job_ref":"mc-1","state":"running"}}}),
                "2026-10-05T01:00:03Z",
            ),
        ];
        let jobs = derive_production_jobs(&receipts, generation, 4, 16);
        assert_eq!(jobs[0].state, ProductionJobState::Succeeded);
        assert_eq!(jobs[0].applicability, ProductionJobApplicability::Stale);
        assert!(jobs[0].result_available);
        assert!(jobs[0].artifact_available);
    }

    #[test]
    fn orphan_motion_result_cannot_prove_success_without_acknowledged_start() {
        let generation = Uuid::now_v7();
        let result = receipt(
            generation,
            8,
            "orphan-result",
            "driver.motion-canvas.render.result",
            "completed",
            json!({"job_ref":"mc:orphan","result":{"data":{
                "job_ref":"mc:orphan","state":"succeeded",
                "artifact":{"manifest":"unbound-source"},
                "progress":{"completed":60,"total":60}
            }}}),
            "2026-10-09T01:00:03Z",
        );
        let orphan = derive_production_jobs(&[result.clone()], generation, 8, 10);
        assert_eq!(orphan.len(), 1, "retain visibility of partial history");
        assert_eq!(orphan[0].state, ProductionJobState::OutcomeUnknown);
        assert_eq!(
            orphan[0].last_observation,
            ProductionObservationState::OutcomeUnknown
        );
        assert_eq!(orphan[0].local_observations, 1);
        assert!(!orphan[0].artifact_available);
        assert!(!orphan[0].result_available);
        assert!(orphan[0].progress.is_none());

        for unrelated in [
            receipt(
                generation,
                9,
                "wrong-revision",
                "driver.motion-canvas.render.start",
                "completed",
                json!({"result":{"data":{"job_ref":"mc:orphan","state":"queued"}}}),
                "2026-10-09T01:00:00Z",
            ),
            receipt(
                Uuid::now_v7(),
                8,
                "wrong-generation",
                "driver.motion-canvas.render.start",
                "completed",
                json!({"result":{"data":{"job_ref":"mc:orphan","state":"queued"}}}),
                "2026-10-09T01:00:00Z",
            ),
            receipt(
                generation,
                8,
                "never-acknowledged",
                "driver.motion-canvas.render.start",
                "outcome_unknown",
                json!({"job_ref":"mc:orphan"}),
                "2026-10-09T01:00:00Z",
            ),
            receipt(
                generation,
                8,
                "different-job",
                "driver.motion-canvas.render.start",
                "completed",
                json!({"result":{"data":{"job_ref":"mc:other","state":"queued"}}}),
                "2026-10-09T01:00:00Z",
            ),
        ] {
            let jobs = derive_production_jobs(&[unrelated, result.clone()], generation, 8, 10);
            let orphan = jobs.iter().find(|job| job.job_ref == "mc:orphan").unwrap();
            assert_eq!(orphan.state, ProductionJobState::OutcomeUnknown);
            assert!(!orphan.artifact_available);
            assert!(!orphan.result_available);
        }

        let start = receipt(
            generation,
            8,
            "real-start",
            "driver.motion-canvas.render.start",
            "completed",
            json!({"result":{"data":{"job_ref":"mc:orphan","state":"queued"}}}),
            "2026-10-09T01:00:00Z",
        );
        let valid = derive_production_jobs(&[start, result], generation, 8, 10);
        assert_eq!(valid.len(), 1);
        assert_eq!(valid[0].state, ProductionJobState::Succeeded);
        assert!(valid[0].artifact_available);
        assert!(valid[0].result_available);
    }

    #[test]
    fn foreign_motion_status_or_result_is_unknown_in_studio_and_sdk_job_projection() {
        let generation = Uuid::now_v7();
        let start = receipt(
            generation,
            8,
            "render-start",
            "driver.motion-canvas.render.start",
            "completed",
            json!({"result":{"data":{"job_ref":"mc:original","state":"queued"}}}),
            "2026-10-09T01:00:00Z",
        );
        for (command, returned_id, include_request) in [
            (
                "driver.motion-canvas.render.status",
                Some("mc:foreign"),
                true,
            ),
            ("driver.motion-canvas.render.status", None, true),
            (
                "driver.motion-canvas.render.result",
                Some("mc:foreign"),
                true,
            ),
            ("driver.motion-canvas.render.result", None, true),
            (
                "driver.motion-canvas.render.result",
                Some("mc:original"),
                false,
            ),
        ] {
            let response = receipt(
                generation,
                8,
                "untrusted-readback",
                command,
                "completed",
                json!({
                    "job_ref": if include_request { Some("mc:original") } else { None },
                    "result": {"data": {
                        "job_ref": returned_id,
                        "state": "succeeded",
                        "artifact": {"manifest":"must-never-appear-as-verified"},
                        "progress": {"completed": 60,"total":60}
                    },
                    "execution": {"provenance":{"provider_generation":900}}}
                }),
                "2026-10-09T01:00:01Z",
            );
            let jobs = derive_production_jobs(&[start.clone(), response], generation, 8, 10);
            assert_eq!(
                jobs.len(),
                1,
                "foreign/missing data may not create another source job"
            );
            assert_eq!(jobs[0].job_ref, "mc:original");
            assert_eq!(jobs[0].state, ProductionJobState::OutcomeUnknown);
            assert_eq!(
                jobs[0].last_observation,
                ProductionObservationState::OutcomeUnknown
            );
            assert!(!jobs[0].artifact_available);
            assert!(!jobs[0].result_available);
            assert!(jobs[0].progress.is_none());
            assert!(jobs[0].provider_generation.is_none());
        }

        // Legacy/foreign aliases are not authoritative Motion Canvas JobView
        // fields, even if an alias happens to equal the requested opaque ref.
        // The native coordinator itself requires the exact canonical job_ref.
        for alias in [
            json!({"job":"mc:original","state":"succeeded",
                "artifact":{"manifest":"alias-must-not-grant-success"}}),
            json!({"job":{"id":"mc:original"},"state":"succeeded",
                "artifact":{"manifest":"alias-must-not-grant-success"}}),
        ] {
            let aliased = receipt(
                generation,
                8,
                "alias-result",
                "driver.motion-canvas.render.result",
                "completed",
                json!({"job_ref":"mc:original","result":{"data":alias}}),
                "2026-10-09T01:00:01Z",
            );
            let jobs = derive_production_jobs(&[start.clone(), aliased], generation, 8, 10);
            assert_eq!(jobs.len(), 1);
            assert_eq!(jobs[0].state, ProductionJobState::OutcomeUnknown);
            assert!(!jobs[0].artifact_available);
            assert!(!jobs[0].result_available);
        }

        // A later canonical result with the *matching* original job is the
        // only receipt allowed to reconcile a previous status identity fault.
        let forged = receipt(
            generation,
            8,
            "bad-status",
            "driver.motion-canvas.render.status",
            "completed",
            json!({"job_ref":"mc:original","result":{"data":{
                "job_ref":"mc:foreign","state":"succeeded","artifact":{"manifest":"untrusted"}
            }}}),
            "2026-10-09T01:00:01Z",
        );
        let later = receipt(
            generation,
            8,
            "good-result",
            "driver.motion-canvas.render.result",
            "completed",
            json!({"job_ref":"mc:original","result":{"data":{
                "job_ref":"mc:original","state":"succeeded",
                "artifact":{"manifest":"bound-to-original-job"}
            }}}),
            "2026-10-09T01:00:02Z",
        );
        let jobs = derive_production_jobs(&[start, forged, later], generation, 8, 10);
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].state, ProductionJobState::Succeeded);
        assert!(jobs[0].artifact_available);
        assert!(jobs[0].result_available);
    }

    #[test]
    fn uncertain_cancel_can_be_reconciled_by_later_canonical_observation() {
        let generation = Uuid::now_v7();
        let receipts = vec![
            receipt(
                generation,
                8,
                "start",
                "driver.mlt-video.render.start",
                "completed",
                wrapped_job("job-9", "running", false),
                "2026-10-05T01:00:00Z",
            ),
            receipt(
                generation,
                8,
                "cancel",
                "driver.mlt-video.render.cancel",
                "outcome_unknown",
                json!({"job_ref":"job-9"}),
                "2026-10-05T01:00:01Z",
            ),
            receipt(
                generation,
                8,
                "status",
                "driver.mlt-video.render.status",
                "completed",
                wrapped_job("job-9", "cancelled", true),
                "2026-10-05T01:00:02Z",
            ),
        ];
        let jobs = derive_production_jobs(&receipts, generation, 8, 16);
        assert_eq!(jobs[0].state, ProductionJobState::Cancelled);
        assert_eq!(
            jobs[0].last_observation,
            ProductionObservationState::Observed
        );
    }
}
