//! Explicit observation of an acknowledged original job. This route has only
//! status/result commands; it cannot plan, apply, start, cancel or retry a render.
use super::*;
use motionwright_domain::Project;
use motionwright_service::{ProductionReceipt, StudioService};
use std::collections::BTreeSet;

const OBSERVATION_SCHEMA: &str = "motionwright.hyperframes-job-observation/1";
const MAX_RECEIPTS: u64 = 20_000;

#[derive(Debug, Clone)]
pub struct HyperframesOriginalJob {
    pub project: Project,
    pub request_id: String,
    pub deliverable_id: Uuid,
    pub runtime_lock_sha256: String,
    pub connection_identity: String,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HyperframesJobState {
    Running,
    Cancelling,
    Succeeded,
    Failed,
    Cancelled,
}
impl HyperframesJobState {
    pub fn terminal(self) -> bool {
        matches!(self, Self::Succeeded | Self::Failed | Self::Cancelled)
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HyperframesJobObservation {
    pub schema: String,
    pub observation_id: Uuid,
    pub original_request_id: String,
    pub generation: Uuid,
    pub observed_revision: u64,
    pub job_ref: String,
    pub provider_generation: u64,
    pub state: HyperframesJobState,
}
pub struct HyperframesJobReadback {
    pub observation: HyperframesJobObservation,
    pub evidence: Option<HyperframesSourceEvidence>,
}
fn uncertain() -> Error {
    backend(
        "The original HyperFrames job observation cannot be authenticated; no render was restarted",
    )
    .uncertain()
}
fn observed_state(data: &Value) -> NativeResult<HyperframesJobState> {
    serde_json::from_value(data.get("state").cloned().ok_or_else(uncertain)?)
        .map_err(|_| uncertain())
}
fn original_job_id(original: &HyperframesOriginalJob, source: &SourceInventory) -> String {
    format!(
        "hf-{}",
        model::sha(
            format!(
                "{}\n{}\n{}",
                original.request_id, source.fingerprint, original.runtime_lock_sha256
            )
            .as_bytes()
        )
    )
}
fn receipt_generation(receipt: &ProductionReceipt) -> NativeResult<u64> {
    let wrapper = receipt.payload.get("result").ok_or_else(uncertain)?;
    if wrapper
        .pointer("/execution/provenance/provider")
        .and_then(Value::as_str)
        != Some("driver:hyperframes")
    {
        return Err(uncertain());
    }
    wrapper
        .pointer("/execution/provenance/provider_generation")
        .and_then(Value::as_u64)
        .ok_or_else(uncertain)
}
fn receipt_data(receipt: &ProductionReceipt) -> NativeResult<&Value> {
    response_data(receipt.payload.get("result").ok_or_else(uncertain)?)
}
fn completed(
    service: &StudioService,
    original: &HyperframesOriginalJob,
    revision: u64,
    request: &str,
    command: &str,
    args: Value,
) -> NativeResult<ProductionReceipt> {
    let receipt = service
        .latest_production_receipt(original.project.id, request)
        .map_err(storage_error)?
        .ok_or_else(uncertain)?;
    if receipt.stage != "completed"
        || receipt.command != command
        || receipt.request_id != request
        || receipt.project_id != original.project.id
        || receipt.generation != original.project.generation
        || receipt.revision != revision
        || receipt.request_sha256 != request_digest(command, &args)?
    {
        return Err(uncertain());
    }
    Ok(receipt)
}
fn original_start(
    service: &StudioService,
    original: &HyperframesOriginalJob,
) -> NativeResult<(SourceInventory, String, ProductionReceipt)> {
    let current = service
        .project(original.project.id)
        .map_err(storage_error)?;
    if original.request_id.is_empty()
        || original.request_id.len() > 96
        || original.request_id.chars().any(char::is_control)
        || !model::sha_valid(&original.runtime_lock_sha256)
        || !model::sha_valid(&original.connection_identity)
        || current.generation != original.project.generation
        || current.revision < original.project.revision
    {
        return Err(uncertain());
    }
    let source =
        crate::hyperframes::compile_hyperframes_source(&original.project, original.deliverable_id)?
            .inventory();
    let job = original_job_id(original, &source);
    let start = completed(
        service,
        original,
        original.project.revision,
        &format!("{}:render-start", original.request_id),
        "driver.hyperframes.render.start",
        json!({"fingerprint":source.fingerprint, "runtime_lock_sha256":original.runtime_lock_sha256, "attempt_id":job}),
    )?;
    if receipt_data(&start)?.get("job_ref").and_then(Value::as_str) != Some(job.as_str()) {
        return Err(uncertain());
    }
    receipt_generation(&start)?;
    Ok((source, job, start))
}
fn require_dispatches(
    service: &StudioService,
    original: &HyperframesOriginalJob,
    job: &str,
    terminals: &[&ProductionReceipt],
) -> NativeResult<()> {
    let watermark = service
        .production_receipt_watermark(original.project.id)
        .map_err(storage_error)?;
    if watermark.count > MAX_RECEIPTS {
        return Err(uncertain());
    }
    let mut found = BTreeSet::new();
    let mut before = None;
    let mut inspected = 0_u64;
    while inspected < watermark.count && found.len() < terminals.len() {
        let page = service
            .production_receipts_snapshot_page(original.project.id, &watermark, before, 256)
            .map_err(storage_error)?;
        if page.is_empty() {
            return Err(uncertain());
        }
        inspected += page.len() as u64;
        before = page.last().map(|row| row.id);
        for row in page {
            if row.stage != "dispatching" {
                continue;
            }
            for terminal in terminals {
                if row.request_id != terminal.request_id {
                    continue;
                }
                let mutation = terminal.command == "driver.hyperframes.render.start";
                if !found.insert(row.request_id.clone())
                    || row.command != terminal.command
                    || row.request_sha256 != terminal.request_sha256
                    || row.project_id != terminal.project_id
                    || row.generation != terminal.generation
                    || row.revision != terminal.revision
                    || row.payload.get("connection").and_then(Value::as_str)
                        != Some(original.connection_identity.as_str())
                    || row.payload.get("mutation").and_then(Value::as_bool) != Some(mutation)
                    || row.payload.get("job_ref")
                        != Some(&if mutation { Value::Null } else { json!(job) })
                {
                    return Err(uncertain());
                }
            }
        }
    }
    if found.len() != terminals.len()
        || service
            .production_receipt_watermark(original.project.id)
            .map_err(storage_error)?
            != watermark
    {
        return Err(uncertain());
    }
    Ok(())
}
/// Reads original SDK receipts only. No process, connection, permission or
/// provider command is created by reopening a recorded observation.
pub fn read_recorded_hyperframes_job_observation(
    service: &StudioService,
    original: &HyperframesOriginalJob,
    observation: &HyperframesJobObservation,
) -> NativeResult<HyperframesJobReadback> {
    let watermark = service
        .production_receipt_watermark(original.project.id)
        .map_err(storage_error)?;
    let (source, job, start) = original_start(service, original)?;
    let provider_generation = receipt_generation(&start)?;
    if observation.schema != OBSERVATION_SCHEMA
        || observation.observation_id.is_nil()
        || observation.original_request_id != original.request_id
        || observation.generation != original.project.generation
        || observation.observed_revision < original.project.revision
        || observation.observed_revision
            > service
                .project(original.project.id)
                .map_err(storage_error)?
                .revision
        || observation.job_ref != job
        || observation.provider_generation != provider_generation
    {
        return Err(uncertain());
    }
    let prefix = format!("hf-observe-{}", observation.observation_id);
    let status = completed(
        service,
        original,
        observation.observed_revision,
        &format!("{prefix}:status"),
        "driver.hyperframes.render.status",
        json!({"job_ref":job}),
    )?;
    let data = receipt_data(&status)?;
    if receipt_generation(&status)? != provider_generation
        || observed_state(data)? != observation.state
        || data.get("job_ref").and_then(Value::as_str) != Some(job.as_str())
        || data.get("fingerprint").and_then(Value::as_str) != Some(source.fingerprint.as_str())
    {
        return Err(uncertain());
    }
    let result = if observation.state == HyperframesJobState::Succeeded {
        Some(completed(
            service,
            original,
            observation.observed_revision,
            &format!("{prefix}:result"),
            "driver.hyperframes.render.result",
            json!({"job_ref":job}),
        )?)
    } else {
        None
    };
    let mut terminals = vec![&start, &status];
    if let Some(result) = &result {
        terminals.push(result);
    }
    require_dispatches(service, original, &job, &terminals)?;
    let evidence = if let Some(result) = &result {
        let data = receipt_data(result)?;
        let receipt = data.get("receipt").ok_or_else(uncertain)?;
        if receipt_generation(result)? != provider_generation
            || observed_state(data)? != HyperframesJobState::Succeeded
            || data.get("job_ref").and_then(Value::as_str) != Some(job.as_str())
            || data.get("directory").and_then(Value::as_str)
                != Some(format!("render-{job}").as_str())
            || data.get("runtime_lock_sha256").and_then(Value::as_str)
                != Some(original.runtime_lock_sha256.as_str())
            || receipt.get("fingerprint").and_then(Value::as_str)
                != Some(source.fingerprint.as_str())
            || receipt.get("source_sha256").and_then(Value::as_str)
                != Some(source.source_sha256.as_str())
            || receipt.get("html_sha256").and_then(Value::as_str)
                != Some(source.html_sha256.as_str())
            || receipt.get("attempt_id").and_then(Value::as_str) != Some(job.as_str())
            || receipt.get("runtime_lock_sha256").and_then(Value::as_str)
                != Some(original.runtime_lock_sha256.as_str())
            || receipt.get("frame_count").and_then(Value::as_u64)
                != Some(u64::from(source.composition.frame_count))
            || receipt.get("source_unchanged") != Some(&Value::Bool(true))
            || receipt.get("automatic_retry") != Some(&Value::Bool(false))
        {
            return Err(uncertain());
        }
        Some(HyperframesSourceEvidence {
            project_resource: original.project.resource_key(),
            generation: original.project.generation,
            revision: original.project.revision,
            deliverable_id: original.deliverable_id,
            source,
            runtime_lock_sha256: original.runtime_lock_sha256.clone(),
            job_ref: job,
            result: data.clone(),
            output_kind: "native_png_sequence_intermediate".into(),
            human_acceptance: false,
        })
    } else {
        None
    };
    if service
        .production_receipt_watermark(original.project.id)
        .map_err(storage_error)?
        != watermark
    {
        return Err(uncertain());
    }
    Ok(HyperframesJobReadback {
        observation: observation.clone(),
        evidence,
    })
}
impl ProductionCoordinator {
    /// One explicit observation, bounded to one status and (only after success)
    /// one result read. An unacknowledged start or changed provider stays unknown.
    pub async fn observe_original_hyperframes_job(
        &self,
        expected: &RevisionStamp,
        original: &HyperframesOriginalJob,
    ) -> NativeResult<HyperframesJobReadback> {
        let (source, job, start) = original_start(&self.service, original)?;
        if expected.resource != original.project.resource_key()
            || expected.generation != original.project.generation
            || self.client.connection().identity()? != original.connection_identity
        {
            return Err(uncertain());
        }
        require_dispatches(&self.service, original, &job, &[&start])?;
        let provider_generation = receipt_generation(&start)?;
        let id = Uuid::new_v4();
        let prefix = format!("hf-observe-{id}");
        let observed = self
            .execute(
                original.project.id,
                expected,
                &format!("{prefix}:status"),
                "driver.hyperframes.render.status",
                json!({"job_ref":job}),
                false,
            )
            .await
            .map_err(Error::uncertain)?;
        let data = response_data(&observed).map_err(Error::uncertain)?;
        if data.get("job_ref").and_then(Value::as_str) != Some(job.as_str())
            || data.get("fingerprint").and_then(Value::as_str) != Some(source.fingerprint.as_str())
        {
            return Err(uncertain());
        }
        let state = observed_state(data)?;
        // Validate the retained status provider before asking even for a result.
        let status = completed(
            &self.service,
            original,
            expected.revision,
            &format!("{prefix}:status"),
            "driver.hyperframes.render.status",
            json!({"job_ref":job}),
        )?;
        if receipt_generation(&status)? != provider_generation {
            return Err(uncertain());
        }
        if state == HyperframesJobState::Succeeded {
            self.execute(
                original.project.id,
                expected,
                &format!("{prefix}:result"),
                "driver.hyperframes.render.result",
                json!({"job_ref":job}),
                false,
            )
            .await
            .map_err(Error::uncertain)?;
        }
        read_recorded_hyperframes_job_observation(
            &self.service,
            original,
            &HyperframesJobObservation {
                schema: OBSERVATION_SCHEMA.into(),
                observation_id: id,
                original_request_id: original.request_id.clone(),
                generation: original.project.generation,
                observed_revision: expected.revision,
                job_ref: job,
                provider_generation,
                state,
            },
        )
    }
}
