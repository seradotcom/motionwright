#[path = "hyperframes_job_observation.rs"]
mod hyperframes_job_observation;

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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mlt_closure: Option<crate::MltClosureProjection>,
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
    } else if command.starts_with("driver.manim-community.render.")
        || command == "driver.manim-community.camera.render.start"
    {
        "driver:manim-community"
    } else if command.starts_with("driver.hyperframes.render.") {
        "driver:hyperframes"
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
    // A recovered read does not withdraw an acknowledged pending cancellation.
    if cancellation_requested || current == ProductionJobState::CancelRequested {
        ProductionJobState::CancelRequested
    } else {
        observed
    }
}

#[cfg(test)]
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
/// The caller supplies the complete snapshot in ascending durable commit order.
/// UUIDs and display timestamps never establish observation authority.
pub(crate) fn derive_production_jobs_all(
    receipts_in_commit_order: &[ProductionReceipt],
    current_generation: Uuid,
    current_revision: u64,
) -> Vec<ProductionJobProjection> {
    // The original acknowledged start is an authority boundary. Each
    // provider/stamp/reference gets one original; a second start makes that
    // lineage ambiguous. Old 256-row UI windows are not authority.
    type JobScope = (String, String, Uuid, u64);
    let mut acknowledged_starts: BTreeMap<JobScope, Vec<(Option<u64>, &ProductionReceipt)>> =
        BTreeMap::new();
    for receipt in receipts_in_commit_order {
        let Some((provider, RenderCommand::Start)) = classify(&receipt.command) else {
            continue;
        };
        if receipt.stage != "completed"
            || !matches!(
                provider,
                "driver:motion-canvas" | "driver:manim-community" | "driver:hyperframes"
            )
        {
            continue;
        }
        let Some(wrapper) = completed_wrapper(receipt) else {
            continue;
        };
        let Some(job_ref) = driver_data(wrapper)
            .get("job_ref")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        else {
            continue;
        };
        // Manim and HyperFrames admissions carry SDK provenance on the original
        // start. Missing/foreign generation may be observed but grants no
        // completed lineage. Legacy Motion Canvas records remain compatible.
        if matches!(provider, "driver:manim-community" | "driver:hyperframes")
            && (wrapper
                .pointer("/execution/provenance/provider")
                .and_then(Value::as_str)
                != Some(provider)
                || provider_generation(wrapper).is_none())
        {
            continue;
        }
        acknowledged_starts
            .entry((
                provider.to_owned(),
                job_ref.to_owned(),
                receipt.generation,
                receipt.revision,
            ))
            .or_default()
            .push((provider_generation(wrapper), receipt));
    }

    let original_observations =
        hyperframes_job_observation::OriginalObservations::new(receipts_in_commit_order);
    let mut jobs: BTreeMap<(String, Uuid, u64, String), (usize, ProductionJobProjection)> =
        BTreeMap::new();
    let mut observed_starts = BTreeSet::new();
    for (position, receipt) in receipts_in_commit_order.iter().enumerate() {
        let Some((provider, kind)) = classify(&receipt.command) else {
            continue;
        };
        if receipt.stage == "dispatching" {
            continue;
        }
        let Some(job_ref) = job_ref_from_receipt(receipt).map(str::to_owned) else {
            continue;
        };

        let observation_source = original_observations.source_revision(receipt, position);
        let source_revision = match observation_source {
            Some(Ok(revision)) => revision,
            _ => receipt.revision,
        };
        let wrapper = completed_wrapper(receipt);
        let data = wrapper.map(driver_data);
        // An explicitly bound read keeps its original source revision. Other
        // observations still require the exact original provider and stamp.
        let guarded_provider = matches!(
            provider,
            "driver:motion-canvas" | "driver:manim-community" | "driver:hyperframes"
        );
        let original = acknowledged_starts.get(&(
            provider.to_owned(),
            job_ref.clone(),
            receipt.generation,
            source_revision,
        ));
        let ambiguous_original = original.is_none_or(|roots| roots.len() != 1);
        let original_start = original
            .filter(|roots| roots.len() == 1)
            .and_then(|roots| roots.first())
            .map(|(_, start)| *start);
        let foreign_returned_job = receipt.stage == "completed"
            && matches!(
                kind,
                RenderCommand::Status | RenderCommand::Cancel | RenderCommand::Result
            )
            && !matches!(
                (
                    receipt.payload.get("job_ref").and_then(Value::as_str),
                    data.and_then(|view| view.get("job_ref")).and_then(Value::as_str)
                ),
                (Some(requested), Some(returned)) if requested == returned && requested == job_ref
            );
        let foreign_provider = receipt.stage == "completed"
            && matches!(provider, "driver:manim-community" | "driver:hyperframes")
            && wrapper
                .and_then(|value| value.pointer("/execution/provenance/provider"))
                .and_then(Value::as_str)
                != Some(provider);
        let provider_generation_drift = receipt.stage == "completed"
            && original
                .and_then(|roots| roots.first())
                .and_then(|(generation, _)| *generation)
                .is_some_and(|expected| wrapper.and_then(provider_generation) != Some(expected));
        let unbound_observation = guarded_provider
            && (matches!(observation_source, Some(Err(())))
                || ambiguous_original
                || foreign_returned_job
                || foreign_provider
                || provider_generation_drift);
        let cancellation_requested = !unbound_observation
            && data
                .and_then(|value| value.get("cancellation_requested"))
                .and_then(Value::as_bool)
                .unwrap_or(false);
        let observed_state = if unbound_observation {
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

        // A status/result can be persisted before the original start ACK.
        // Its request must never become the root of the admitted mutation.
        let root_receipt = original_start.unwrap_or(receipt);
        let late_start_ack = matches!(kind, RenderCommand::Start)
            && original_start.is_some_and(|start| observed_starts.contains(&start.id));
        if !matches!(kind, RenderCommand::Start)
            && let Some(start) = original_start
        {
            observed_starts.insert(start.id);
        }

        let (last_position, entry) = jobs
            .entry((
                provider.to_owned(),
                receipt.generation,
                source_revision,
                job_ref.clone(),
            ))
            .or_insert_with(|| {
                let initial_state = observed_state.unwrap_or(ProductionJobState::OutcomeUnknown);
                (
                    position,
                    ProductionJobProjection {
                        job_ref: job_ref.clone(),
                        provider: provider.to_owned(),
                        root_request_id: root_receipt.request_id.clone(),
                        generation: receipt.generation,
                        revision: source_revision,
                        state: initial_state,
                        cancellation_requested,
                        applicability: ProductionJobApplicability::Current,
                        last_command: receipt.command.clone(),
                        created_at: root_receipt.created_at.clone(),
                        last_observed_at: receipt.created_at.clone(),
                        local_observations: 0,
                        provider_generation: None,
                        progress: None,
                        artifact_available: false,
                        result_available: false,
                        last_observation: ProductionObservationState::Observed,
                        mlt_closure: None,
                    },
                )
            });

        // Rank by the same accepted observation that updates metadata below,
        // including observations deliberately recorded as uncertain.
        *last_position = position;
        entry.last_command = receipt.command.clone();
        entry.last_observed_at = receipt.created_at.clone();
        entry.local_observations = entry.local_observations.saturating_add(1);
        if unbound_observation {
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
                    // A delayed admission ACK is not a fresh status read.
                    // Valid terminal evidence may close an active/unknown job.
                    if !late_start_ack || state.definitive_terminal() {
                        entry.state =
                            apply_observed_state(entry.state, state, entry.cancellation_requested);
                    }
                }
                if let Some(wrapper) = wrapper {
                    entry.provider_generation =
                        provider_generation(wrapper).or(entry.provider_generation);
                }
                if let Some(data) = data {
                    if !late_start_ack || entry.progress.is_none() {
                        entry.progress =
                            progress_from_data(data).or_else(|| entry.progress.clone());
                    }
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

    let mlt_closures = crate::mlt_cleanup::MltClosures::new(receipts_in_commit_order);
    for (_, job) in jobs.values_mut() {
        job.mlt_closure = mlt_closures.for_job(job);
        job.applicability =
            if job.generation == current_generation && job.revision == current_revision {
                ProductionJobApplicability::Current
            } else {
                ProductionJobApplicability::Stale
            };
    }

    let mut jobs: Vec<_> = jobs.into_values().collect();
    jobs.sort_by_key(|(position, _)| std::cmp::Reverse(*position));
    jobs.into_iter().map(|(_, job)| job).collect()
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
    fn mlt_closure_restores_only_the_original_attempt_in_commit_order() {
        use crate::MltCloseDisposition;
        use sha2::{Digest, Sha256};
        let generation = Uuid::now_v7();
        let revision = 7;
        let time = "2026-10-12T18:00:00Z";
        let start_request = "take:mlt:render-start";
        let result_request = "take:mlt:render-result";
        let start_command = "driver.mlt-video.render.start";
        let result_command = "driver.mlt-video.render.result";
        let source = json!({
            "schema":"motionwright.mlt-timeline-source/1",
            "connection_identity":"d".repeat(64),"project_ref":format!("video:{}", "a".repeat(32)),
            "job":"render:take","provider_generation":7,"profile":"lossless-video-only",
            "semantic_revision":"a".repeat(64), "output":"original.mkv",
        });
        let start_intent = receipt(
            generation,
            revision,
            start_request,
            start_command,
            "dispatching",
            json!({"connection":source["connection_identity"],"mutation":true}),
            time,
        );
        let mut response = wrapped_job("render:take", "running", false);
        response["result"]["data"]["project_revision"] = source["semantic_revision"].clone();
        response["result"]["data"]["output"] = source["output"].clone();
        response["result"]["data"]["profile"] = source["profile"].clone();
        response["result"]["provenance_checked"] = json!(true);
        response["result"]["execution"] = json!({
            "dry_run":false,"backend":"driver:mlt-video","provenance":{
                "provider":"driver:mlt-video","execution_provider":"driver:mlt-video",
                "source":"driver","untrusted_metadata":true,"provider_generation":7,"descriptor_sha256":"b".repeat(64),
            },
        });
        let start = receipt(
            generation,
            revision,
            start_request,
            start_command,
            "completed",
            response.clone(),
            time,
        );
        let mut result_intent = receipt(
            generation,
            revision,
            result_request,
            result_command,
            "dispatching",
            json!({"connection":source["connection_identity"],"job_ref":"render:take",
                "mlt_timeline_source":source,"mutation":false}),
            time,
        );
        result_intent.request_sha256 = hex::encode(Sha256::digest(
            serde_json::to_vec(&(result_command, json!({"job":"render:take"}))).unwrap(),
        ));
        let mut result = result_intent.clone();
        result.id = Uuid::now_v7();
        result.stage = "completed".into();
        response["result"]["data"]["state"] = json!("succeeded");
        response["result"]["data"]["error"] = Value::Null;
        response["result"]["data"]["artifact"] = json!({
            "root":"output","path":"original.mkv","sha256":"c".repeat(64),"bytes":14,
        });
        result.payload["result"] = response["result"].clone();

        let stamp = motionwright_domain::RevisionStamp {
            resource: format!("project:{}", Uuid::nil()),
            generation,
            revision,
        };
        let canonical = crate::retained_mlt_close(
            Uuid::nil(),
            &stamp,
            start_request,
            &[&start_intent, &start],
            &[&result_intent, &result],
        )
        .unwrap();
        let mut intent = receipt(
            generation,
            revision,
            &canonical.request_id,
            "driver.mlt-video.project.close",
            "dispatching",
            json!({
                "mutation":true,"result_receipt":result.id,"start_request":start_request,"native_source":source,
            }),
            time,
        );
        intent.request_sha256 = canonical.request_sha256;
        intent.payload["provider_binding"] =
            json!({"provider_generation":7,"binding_ref":"fixture-binding"});
        let mut done = intent.clone();
        done.id = Uuid::nil(); // The terminal follows the intent only in SQLite order.
        done.stage = "completed".into();
        let core_exec = json!({"policy_decision":"allow_after_confirmation","dry_run":false});
        let driver_exec = json!({"policy_decision":"allow_after_confirmation","dry_run":false,
            "provenance":{"provider":"driver:mlt-video","provider_generation":7}});
        let outer_id = "a".repeat(32);
        let inner_id = "b".repeat(32);
        let core_data = json!({"binding_ref":"fixture-binding","result":{
            "request_id":inner_id,"ok":true,"command":"driver.mlt-video.project.close",
            "data":{"closed":true},"execution":driver_exec,
        }});
        done.payload["result"] = json!({
            "data":{"closed":true},"execution":driver_exec,"provenance_checked":true,
            "provider_bound":{"outer_request_id":outer_id,"inner_request_id":inner_id,"original":{
                "data":core_data,"execution":core_exec,"provenance_checked":true,
                "original_envelope":{"request_id":outer_id,"command":"providers.execute_bound","ok":true,
                    "data":core_data,"execution":core_exec}
            }},
        });

        let incomplete =
            derive_production_jobs_all(&[start.clone(), result.clone()], generation, revision);
        assert!(incomplete[0].mlt_closure.is_none());
        let prefix = [start_intent, start, result_intent, result];
        let status = |tail: &[ProductionReceipt]| {
            let log = prefix.iter().chain(tail).cloned().collect::<Vec<_>>();
            derive_production_jobs_all(&log, generation, revision)[0]
                .mlt_closure
                .unwrap()
                .disposition
        };
        assert_eq!(status(&[]), None);
        assert_eq!(
            status(std::slice::from_ref(&intent)),
            Some(MltCloseDisposition::OutcomeUnknown)
        );
        assert_eq!(
            status(std::slice::from_ref(&done)),
            Some(MltCloseDisposition::OutcomeUnknown)
        );
        assert_eq!(
            status(&[intent.clone(), done.clone()]),
            Some(MltCloseDisposition::Closed)
        );
        let mut contradictory = done.clone();
        contradictory.payload["result"]["provider_bound"]["original"]["data"]["result"]["ok"] =
            json!(false);
        assert_eq!(
            status(&[intent.clone(), contradictory]),
            Some(MltCloseDisposition::OutcomeUnknown)
        );
        let mut duplicate = done.clone();
        duplicate.id = Uuid::now_v7();
        assert_eq!(
            status(&[intent.clone(), done.clone(), duplicate]),
            Some(MltCloseDisposition::OutcomeUnknown)
        );
        let mut refused = done.clone();
        refused.stage = "failed_known".into();
        refused.payload["error"] = json!({"code":"PolicyDenied","operator_refused":true});
        let mut unauthorized_retry = intent.clone();
        unauthorized_retry.request_id = format!("{}:after:{}", intent.request_id, refused.id);
        assert_eq!(
            status(&[intent.clone(), refused, unauthorized_retry]),
            Some(MltCloseDisposition::Refused)
        );
        let mut arbitrary = done.clone();
        arbitrary.request_id = "foreign-close-request".into();
        assert_eq!(status(std::slice::from_ref(&arbitrary)), None);
        let mut foreign = done.clone();
        foreign.payload["native_source"]["job"] = json!("render:foreign");
        assert_eq!(
            status(&[intent.clone(), foreign]),
            Some(MltCloseDisposition::OutcomeUnknown)
        );
        done.payload["result"]["provider_bound"]["original"]["execution"]["policy_decision"] =
            json!("allow");
        assert_eq!(
            status(&[intent, done]),
            Some(MltCloseDisposition::OutcomeUnknown)
        );
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
        let orphan = derive_production_jobs(std::slice::from_ref(&result), generation, 8, 10);
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
    #[test]
    fn camera_start_uses_manim_provider_without_admitting_other_camera_commands() {
        assert_eq!(
            classify("driver.manim-community.camera.render.start"),
            Some(("driver:manim-community", RenderCommand::Start))
        );
        for (command, kind) in [
            ("driver.manim-community.render.start", RenderCommand::Start),
            (
                "driver.manim-community.render.status",
                RenderCommand::Status,
            ),
            (
                "driver.manim-community.render.cancel",
                RenderCommand::Cancel,
            ),
            (
                "driver.manim-community.render.result",
                RenderCommand::Result,
            ),
        ] {
            assert_eq!(classify(command), Some(("driver:manim-community", kind)));
        }
        for command in [
            "driver.manim-community.camera.render.status",
            "driver.manim-community.camera.render.cancel",
            "driver.manim-community.camera.render.result",
            "driver.manim-community.camera.other.render.start",
            "driver.manim-community.camera.render.start.extra",
        ] {
            assert_eq!(classify(command), None);
        }
    }

    fn camera_job_payload(job: &str, state: &str) -> Value {
        json!({
            "job_ref": job,
            "result": {
                "data": {"job_ref": job, "state": state},
                "execution": {"provenance": {
                    "provider": "driver:manim-community",
                    "provider_generation": 17
                }}
            }
        })
    }

    #[test]
    fn camera_start_retains_original_lineage_through_legacy_result_command() {
        let generation = Uuid::now_v7();
        let start = receipt(
            generation,
            7,
            "camera-original:start",
            "driver.manim-community.camera.render.start",
            "completed",
            camera_job_payload("manim-original", "rendering"),
            "2026-10-20T01:00:00Z",
        );
        let mut completed = camera_job_payload("manim-original", "succeeded");
        completed["result"]["data"]["artifact"] = json!({"fixture_only": true});
        let result = receipt(
            generation,
            7,
            "camera-original:result",
            "driver.manim-community.render.result",
            "completed",
            completed,
            "2026-10-20T01:00:01Z",
        );
        let jobs = derive_production_jobs(&[start.clone(), result.clone()], generation, 7, 16);
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].provider, "driver:manim-community");
        assert_eq!(jobs[0].job_ref, "manim-original");
        assert_eq!(jobs[0].root_request_id, "camera-original:start");
        assert_eq!(jobs[0].provider_generation, Some(17));
        assert_eq!(jobs[0].state, ProductionJobState::Succeeded);
        assert!(jobs[0].artifact_available && jobs[0].result_available);

        let orphan = derive_production_jobs(std::slice::from_ref(&result), generation, 7, 16);
        assert_eq!(orphan[0].state, ProductionJobState::OutcomeUnknown);
        assert!(!orphan[0].artifact_available && !orphan[0].result_available);

        let mut duplicate = start.clone();
        duplicate.id = Uuid::now_v7();
        duplicate.request_id = "unrelated-second-start".into();
        let ambiguous = derive_production_jobs(&[start, duplicate, result], generation, 7, 16);
        assert_eq!(ambiguous[0].state, ProductionJobState::OutcomeUnknown);
        assert!(!ambiguous[0].artifact_available && !ambiguous[0].result_available);
    }

    #[test]
    fn camera_observations_keep_unknown_for_foreign_identity_and_generation() {
        let generation = Uuid::now_v7();
        let start = receipt(
            generation,
            7,
            "camera-original:start",
            "driver.manim-community.camera.render.start",
            "completed",
            camera_job_payload("manim-original", "rendering"),
            "2026-10-20T01:00:00Z",
        );
        for command in [
            "driver.manim-community.render.status",
            "driver.manim-community.render.result",
        ] {
            for (pointer, replacement) in [
                ("/result/data/job_ref", json!("manim-foreign")),
                ("/result/data/job_ref", Value::Null),
                (
                    "/result/execution/provenance/provider_generation",
                    json!(18),
                ),
                (
                    "/result/execution/provenance/provider_generation",
                    Value::Null,
                ),
                (
                    "/result/execution/provenance/provider",
                    json!("driver:other"),
                ),
            ] {
                let mut payload = camera_job_payload("manim-original", "succeeded");
                payload["result"]["data"]["artifact"] = json!({"fixture_only": true});
                *payload.pointer_mut(pointer).unwrap() = replacement;
                let observed = receipt(
                    generation,
                    7,
                    "camera-original:read",
                    command,
                    "completed",
                    payload,
                    "2026-10-20T01:00:01Z",
                );
                let jobs = derive_production_jobs(&[start.clone(), observed], generation, 7, 16);
                assert_eq!(jobs.len(), 1);
                assert_eq!(jobs[0].root_request_id, "camera-original:start");
                assert_eq!(jobs[0].state, ProductionJobState::OutcomeUnknown);
                assert!(!jobs[0].artifact_available && !jobs[0].result_available);
            }
        }
    }

    #[test]
    fn later_hyperframes_reads_require_the_original_request_and_connection() {
        use sha2::{Digest, Sha256};
        for altered in [
            "none",
            "origin",
            "connection",
            "digest",
            "missing_dispatch",
            "duplicate_start",
            "provider_generation",
            "job",
            "order",
        ] {
            let generation = Uuid::new_v4();
            let base = "original-hyperframes";
            let job = format!("hf-{}", "9".repeat(64));
            let payload = |state: &str| {
                json!({"job_ref":job,
                "result":{"data":{"job_ref":job,"state":state},
                    "execution":{"provenance":{"provider":"driver:hyperframes","provider_generation":3}}}})
            };
            let start = receipt(
                generation,
                7,
                &format!("{base}:render-start"),
                "driver.hyperframes.render.start",
                "completed",
                payload("running"),
                "2026-11-24T12:00:00Z",
            );
            let mut start_dispatch = start.clone();
            start_dispatch.id = Uuid::new_v4();
            start_dispatch.stage = "dispatching".into();
            start_dispatch.payload =
                json!({"connection":"2".repeat(64),"mutation":true,"job_ref":null});
            let origin = if altered == "origin" {
                "7".repeat(64)
            } else {
                hex::encode(Sha256::digest(base.as_bytes()))
            };
            let request = format!("hf-observe-{origin}-{}:result", Uuid::new_v4());
            let command = "driver.hyperframes.render.result";
            let mut observed = receipt(
                generation,
                8,
                &request,
                command,
                "completed",
                payload("succeeded"),
                "2026-11-24T11:00:00Z",
            );
            observed.request_sha256 = hex::encode(Sha256::digest(
                serde_json::to_vec(&(command, json!({"job_ref":job}))).unwrap(),
            ));
            if altered == "digest" {
                observed.request_sha256 = "6".repeat(64);
            }
            if altered == "provider_generation" {
                observed.payload["result"]["execution"]["provenance"]["provider_generation"] =
                    json!(4);
            }
            if altered == "job" {
                observed.payload["result"]["data"]["job_ref"] = json!("foreign");
            }
            let mut read_dispatch = observed.clone();
            read_dispatch.id = Uuid::new_v4();
            read_dispatch.stage = "dispatching".into();
            read_dispatch.payload = json!({"connection":if altered=="connection" {"3".repeat(64)} else {"2".repeat(64)},
                "mutation":false,"job_ref":job});
            let mut receipts = vec![start_dispatch, start.clone()];
            if altered == "duplicate_start" {
                receipts.push(start);
            }
            if altered != "missing_dispatch" {
                receipts.push(read_dispatch);
            }
            receipts.push(observed);
            if altered == "order" {
                receipts.swap(1, 2);
            }
            let before = receipts.clone();
            let jobs = derive_production_jobs(&receipts, generation, 8, 16);
            if altered == "none" {
                assert_eq!(jobs.len(), 1);
                assert_eq!(jobs[0].revision, 7);
                assert_eq!(jobs[0].root_request_id, format!("{base}:render-start"));
                assert_eq!(jobs[0].state, ProductionJobState::Succeeded);
            } else {
                assert!(
                    jobs.iter()
                        .all(|job| job.state != ProductionJobState::Succeeded),
                    "{altered}"
                );
            }
            assert_eq!(receipts, before);
        }
    }

    #[test]
    fn camera_known_observation_failure_does_not_establish_render_failure() {
        let generation = Uuid::now_v7();
        let start = receipt(
            generation,
            7,
            "camera-original:start",
            "driver.manim-community.camera.render.start",
            "completed",
            camera_job_payload("manim-original", "rendering"),
            "2026-10-20T01:00:00Z",
        );
        let failed_read = receipt(
            generation,
            7,
            "camera-original:status",
            "driver.manim-community.render.status",
            "failed_known",
            json!({"job_ref": "manim-original", "error": {
                "code": "Unavailable", "outcome_known": true
            }}),
            "2026-10-20T01:00:01Z",
        );
        let jobs = derive_production_jobs(&[start, failed_read], generation, 7, 16);
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].root_request_id, "camera-original:start");
        assert_eq!(
            jobs[0].last_observation,
            ProductionObservationState::FailedKnown
        );
        assert_eq!(jobs[0].state, ProductionJobState::OutcomeUnknown);
        assert!(!jobs[0].artifact_available && !jobs[0].result_available);
    }
}
