//! Desktop admission and read-only recovery for the existing HyperFrames producer.
//! Original SQLite receipts are authoritative; recovery cannot dispatch a provider.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use super::hyperframes_source::{SourceRequest, inspect_source};
use motionwright_domain::Project;
use motionwright_native::{
    hyperframes::compile_hyperframes_source, production::HyperframesSourceEvidence,
};
use motionwright_service::{ProductionReceipt, ProductionReceiptInput, StudioService};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    sync::{Arc, Mutex},
};
use uuid::Uuid;

const COMMAND: &str = "motionwright.hyperframes.desktop-render";
const SCHEMA: &str = "motionwright.hyperframes-desktop-intent/1";
const UNCONFIRMED: &str = "The original HyperFrames operation is unconfirmed. Read its retained result before another render.";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderRequest {
    pub source: SourceRequest,
    pub expected_fingerprint: String,
    pub runtime_lock_sha256: String,
    pub request_id: String,
    pub effect_grant: Uuid,
}
#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalIntent {
    pub schema: String,
    pub source: SourceRequest,
    pub fingerprint: String,
    pub runtime_lock_sha256: String,
    pub request_id: String,
    pub connection_identity: String,
    pub project: Project,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReadRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub request_id: String,
}
#[derive(Debug, Serialize)]
pub struct AttemptView {
    pub schema: &'static str,
    pub request_id: String,
    pub source: Option<SourceRequest>,
    pub fingerprint: Option<String>,
    pub runtime_lock_sha256: Option<String>,
    pub state: &'static str,
    pub source_current: bool,
    pub job_ref: Option<String>,
    pub frame_count: Option<u32>,
    pub project_modified: bool,
    pub automatic_retry: bool,
}
pub struct Recovery {
    pub view: AttemptView,
    pub evidence: Option<HyperframesSourceEvidence>,
    pub original_project: Option<Project>,
    pub connection_identity: Option<String>,
}
#[derive(Clone, Default)]
pub struct RenderRegistry(Arc<Mutex<BTreeSet<Uuid>>>);
pub struct RenderLease {
    registry: RenderRegistry,
    project_id: Uuid,
}
impl Drop for RenderLease {
    fn drop(&mut self) {
        if let Ok(mut active) = self.registry.0.lock() {
            active.remove(&self.project_id);
        }
    }
}
impl RenderRegistry {
    pub fn acquire(&self, project_id: Uuid) -> Result<RenderLease, String> {
        let mut active = self.0.lock().map_err(|_| UNCONFIRMED)?;
        if !active.insert(project_id) {
            return Err("A HyperFrames operation is already active for this project.".into());
        }
        Ok(RenderLease {
            registry: self.clone(),
            project_id,
        })
    }
}
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn is_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn request_identity(value: &str) -> Result<(), String> {
    let id = value
        .strip_prefix("hf-ui-")
        .and_then(|s| Uuid::parse_str(s).ok())
        .filter(|id| !id.is_nil() && format!("hf-ui-{id}") == value);
    if id.is_none() {
        return Err("HyperFrames request identity is invalid.".into());
    }
    Ok(())
}
pub fn subject(request: &RenderRequest) -> String {
    format!(
        "hyperframes-render|{}|{}|{}|{}|{}",
        request.source.active_branch,
        request.source.deliverable_id,
        request.expected_fingerprint,
        request.runtime_lock_sha256,
        request.request_id
    )
}
fn digest(intent: &OriginalIntent) -> Result<String, String> {
    serde_json::to_vec(intent)
        .map(|bytes| sha(&bytes))
        .map_err(|_| "HyperFrames intent cannot be encoded.".into())
}
fn append(
    service: &StudioService,
    intent: &OriginalIntent,
    stage: &str,
    outcome: Value,
) -> Result<(), String> {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: intent.source.project_id,
            generation: intent.source.generation,
            revision: intent.source.revision,
            request_id: intent.request_id.clone(),
            request_sha256: digest(intent)?,
            command: COMMAND.into(),
            stage: stage.into(),
            payload: json!({"intent":intent, "mutation":true, "outcome":outcome}),
        })
        .map(|_| ())
        .map_err(|_| UNCONFIRMED.into())
}
fn original_from_receipt(receipt: &ProductionReceipt) -> Result<OriginalIntent, String> {
    let intent: OriginalIntent =
        serde_json::from_value(receipt.payload.get("intent").cloned().ok_or(UNCONFIRMED)?)
            .map_err(|_| UNCONFIRMED)?;
    request_identity(&intent.request_id)?;
    if intent.schema != SCHEMA
        || receipt.command != COMMAND
        || receipt.request_id != intent.request_id
        || receipt.project_id != intent.source.project_id
        || receipt.generation != intent.source.generation
        || receipt.revision != intent.source.revision
        || receipt.request_sha256 != digest(&intent)?
        || !is_sha(&intent.runtime_lock_sha256)
        || !is_sha(&intent.connection_identity)
        || intent.project.id != intent.source.project_id
        || intent.project.generation != intent.source.generation
        || intent.project.revision != intent.source.revision
        || intent.project.active_branch != intent.source.active_branch
    {
        return Err(UNCONFIRMED.into());
    }
    let inventory = compile_hyperframes_source(&intent.project, intent.source.deliverable_id)
        .map_err(|_| UNCONFIRMED)?
        .inventory();
    if inventory.fingerprint != intent.fingerprint {
        return Err(UNCONFIRMED.into());
    }
    Ok(intent)
}
fn original_job(intent: &OriginalIntent) -> String {
    format!(
        "hf-{}",
        sha(format!(
            "{}\n{}\n{}",
            intent.request_id, intent.fingerprint, intent.runtime_lock_sha256
        )
        .as_bytes())
    )
}
fn wrapper_data(value: &Value) -> &Value {
    value.get("data").unwrap_or(value)
}
/// The actual producer retains connection identity on dispatch, not on the
/// completed envelope. Match both original intents to their immutable terminal
/// bindings, across the complete receipt stream rather than a recent suffix.
fn require_original_dispatches(
    service: &StudioService,
    intent: &OriginalIntent,
    start: &ProductionReceipt,
    result: &ProductionReceipt,
) -> Result<(), String> {
    let watermark = service
        .production_receipt_watermark(intent.project.id)
        .map_err(|_| UNCONFIRMED)?;
    let mut found = BTreeSet::new();
    let mut before = None;
    let mut inspected = 0_u64;
    while inspected < watermark.count && found.len() < 2 {
        let page = service
            .production_receipts_snapshot_page(intent.project.id, &watermark, before, 256)
            .map_err(|_| UNCONFIRMED)?;
        if page.is_empty() {
            return Err(UNCONFIRMED.into());
        }
        inspected += page.len() as u64;
        before = page.last().map(|row| row.id);
        for original in page {
            if original.stage != "dispatching" {
                continue;
            }
            for terminal in [start, result] {
                if original.request_id != terminal.request_id {
                    continue;
                }
                if !found.insert(original.request_id.clone())
                    || original.request_sha256 != terminal.request_sha256
                    || original.command != terminal.command
                    || original.generation != terminal.generation
                    || original.revision != terminal.revision
                    || original.project_id != terminal.project_id
                    || original.payload.get("connection").and_then(Value::as_str)
                        != Some(intent.connection_identity.as_str())
                    || original.payload.get("mutation").and_then(Value::as_bool)
                        != Some(terminal.command.ends_with(".start"))
                {
                    return Err(UNCONFIRMED.into());
                }
            }
        }
    }
    if found.len() != 2
        || service
            .production_receipt_watermark(intent.project.id)
            .map_err(|_| UNCONFIRMED)?
            != watermark
    {
        return Err(UNCONFIRMED.into());
    }
    Ok(())
}

fn original_result(
    service: &StudioService,
    intent: &OriginalIntent,
) -> Result<Option<HyperframesSourceEvidence>, String> {
    let Some(result) = service
        .latest_production_receipt(
            intent.project.id,
            &format!("{}:render-result", intent.request_id),
        )
        .map_err(|_| UNCONFIRMED)?
    else {
        return Ok(None);
    };
    if result.stage != "completed" {
        return Ok(None);
    }
    let start = service
        .latest_production_receipt(
            intent.project.id,
            &format!("{}:render-start", intent.request_id),
        )
        .map_err(|_| UNCONFIRMED)?
        .ok_or(UNCONFIRMED)?;
    require_original_dispatches(service, intent, &start, &result)?;
    let mut generation = None;
    for (receipt, command) in [
        (&start, "driver.hyperframes.render.start"),
        (&result, "driver.hyperframes.render.result"),
    ] {
        if receipt.stage != "completed"
            || receipt.command != command
            || receipt.generation != intent.source.generation
            || receipt.revision != intent.source.revision
            || receipt.project_id != intent.project.id
        {
            return Err(UNCONFIRMED.into());
        }
        let wrapper = receipt.payload.get("result").ok_or(UNCONFIRMED)?;
        if wrapper
            .pointer("/execution/provenance/provider")
            .and_then(Value::as_str)
            != Some("driver:hyperframes")
        {
            return Err(UNCONFIRMED.into());
        }
        let observed = wrapper
            .pointer("/execution/provenance/provider_generation")
            .and_then(Value::as_u64)
            .ok_or(UNCONFIRMED)?;
        if generation.is_some_and(|prior| prior != observed) {
            return Err(UNCONFIRMED.into());
        }
        generation = Some(observed);
        let data = wrapper_data(wrapper);
        if data.get("job_ref").and_then(Value::as_str) != Some(original_job(intent).as_str()) {
            return Err(UNCONFIRMED.into());
        }
    }
    let data = wrapper_data(result.payload.get("result").ok_or(UNCONFIRMED)?);
    let source = compile_hyperframes_source(&intent.project, intent.source.deliverable_id)
        .map_err(|_| UNCONFIRMED)?
        .inventory();
    let evidence = HyperframesSourceEvidence {
        project_resource: intent.project.resource_key(),
        generation: intent.source.generation,
        revision: intent.source.revision,
        deliverable_id: intent.source.deliverable_id,
        source,
        runtime_lock_sha256: intent.runtime_lock_sha256.clone(),
        job_ref: original_job(intent),
        result: data.clone(),
        output_kind: "native_png_sequence_intermediate".into(),
        human_acceptance: false,
    };
    validate_evidence(intent, &evidence)?;
    Ok(Some(evidence))
}
pub fn validate_evidence(
    intent: &OriginalIntent,
    evidence: &HyperframesSourceEvidence,
) -> Result<(), String> {
    let inventory = compile_hyperframes_source(&intent.project, intent.source.deliverable_id)
        .map_err(|_| UNCONFIRMED)?
        .inventory();
    let result = &evidence.result;
    let receipt = result.get("receipt").ok_or(UNCONFIRMED)?;
    if evidence.project_resource != intent.project.resource_key()
        || evidence.generation != intent.source.generation
        || evidence.revision != intent.source.revision
        || evidence.deliverable_id != intent.source.deliverable_id
        || serde_json::to_value(&evidence.source).map_err(|_| UNCONFIRMED)?
            != serde_json::to_value(&inventory).map_err(|_| UNCONFIRMED)?
        || evidence.runtime_lock_sha256 != intent.runtime_lock_sha256
        || evidence.job_ref != original_job(intent)
        || evidence.human_acceptance
        || evidence.output_kind != "native_png_sequence_intermediate"
        || result.get("state").and_then(Value::as_str) != Some("succeeded")
        || result.get("job_ref").and_then(Value::as_str) != Some(evidence.job_ref.as_str())
        || result.get("directory").and_then(Value::as_str)
            != Some(format!("render-{}", evidence.job_ref).as_str())
        || result.get("runtime_lock_sha256").and_then(Value::as_str)
            != Some(intent.runtime_lock_sha256.as_str())
        || receipt.get("fingerprint").and_then(Value::as_str) != Some(intent.fingerprint.as_str())
        || receipt.get("source_sha256").and_then(Value::as_str)
            != Some(inventory.source_sha256.as_str())
        || receipt.get("html_sha256").and_then(Value::as_str)
            != Some(inventory.html_sha256.as_str())
        || receipt.get("attempt_id").and_then(Value::as_str) != Some(evidence.job_ref.as_str())
        || receipt.get("runtime_lock_sha256").and_then(Value::as_str)
            != Some(intent.runtime_lock_sha256.as_str())
        || receipt.get("frame_count").and_then(Value::as_u64)
            != Some(u64::from(inventory.composition.frame_count))
        || receipt.get("source_unchanged") != Some(&Value::Bool(true))
        || receipt.get("automatic_retry") != Some(&Value::Bool(false))
    {
        return Err(UNCONFIRMED.into());
    }
    Ok(())
}
/// Exact saved source, one grant and one durable intent precede all Native work.
pub fn begin(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: &RenderRequest,
    connection_identity: &str,
) -> Result<OriginalIntent, String> {
    request_identity(&request.request_id)?;
    if !is_sha(&request.expected_fingerprint)
        || !is_sha(&request.runtime_lock_sha256)
        || !is_sha(connection_identity)
    {
        return Err("Inspect the saved source and choose its exact runtime lock.".into());
    }
    let snapshot = inspect_source(service, request.source.clone())?;
    if snapshot.fingerprint != request.expected_fingerprint {
        return Err("The inspected HyperFrames source changed.".into());
    }
    if service
        .latest_production_receipt(request.source.project_id, &request.request_id)
        .map_err(|_| UNCONFIRMED)?
        .is_some()
    {
        return Err(
            "This original HyperFrames request already exists. Read it without replay.".into(),
        );
    }
    // Complete, watermark-bound scan; an old UNKNOWN cannot fall out of a UI window.
    let watermark = service
        .production_receipt_watermark(request.source.project_id)
        .map_err(|_| UNCONFIRMED)?;
    let mut before = None;
    let mut inspected = 0_u64;
    let mut seen = BTreeSet::new();
    while inspected < watermark.count {
        let page = service
            .production_receipts_snapshot_page(request.source.project_id, &watermark, before, 256)
            .map_err(|_| UNCONFIRMED)?;
        if page.is_empty() {
            return Err(UNCONFIRMED.into());
        }
        inspected += page.len() as u64;
        before = page.last().map(|receipt| receipt.id);
        for receipt in page {
            if receipt.command == COMMAND
                && seen.insert(receipt.request_id.clone())
                && matches!(receipt.stage.as_str(), "dispatching" | "outcome_unknown")
            {
                let original = original_from_receipt(&receipt)?;
                if original_result(service, &original)?.is_none() {
                    return Err(UNCONFIRMED.into());
                }
            }
        }
    }
    if service
        .production_receipt_watermark(request.source.project_id)
        .map_err(|_| UNCONFIRMED)?
        != watermark
        || service
            .project(snapshot.project.id)
            .map_err(|_| UNCONFIRMED)?
            != snapshot.project
    {
        return Err("Saved source or operation history changed; inspect before rendering.".into());
    }
    grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(
            request.source.project_id,
            request.source.generation,
            request.source.revision,
        ),
        &subject(request),
    )?;
    let intent = OriginalIntent {
        schema: SCHEMA.into(),
        source: request.source.clone(),
        fingerprint: request.expected_fingerprint.clone(),
        runtime_lock_sha256: request.runtime_lock_sha256.clone(),
        request_id: request.request_id.clone(),
        connection_identity: connection_identity.into(),
        project: snapshot.project,
    };
    append(service, &intent, "dispatching", Value::Null)?;
    Ok(intent)
}
pub fn finish(
    service: &StudioService,
    intent: &OriginalIntent,
    result: &Result<HyperframesSourceEvidence, semwright_native_sdk::Error>,
) -> Result<(), String> {
    match result {
        Ok(evidence) => {
            validate_evidence(intent, evidence)?;
            append(
                service,
                intent,
                "completed",
                json!({"native_result_retained":true}),
            )
        }
        Err(error) => append(
            service,
            intent,
            if error.outcome_known {
                "failed_known"
            } else {
                "outcome_unknown"
            },
            json!({"code":format!("{:?}", error.code), "outcome_known":error.outcome_known}),
        ),
    }
}
pub fn read_attempt(service: &StudioService, request: &ReadRequest) -> Result<Recovery, String> {
    request_identity(&request.request_id)?;
    let current = service
        .project(request.project_id)
        .map_err(|_| UNCONFIRMED)?;
    if current.generation != request.generation {
        return Err("The original project generation is unavailable.".into());
    }
    let mut view = AttemptView {
        schema: "motionwright.hyperframes-desktop-attempt/1",
        request_id: request.request_id.clone(),
        source: None,
        fingerprint: None,
        runtime_lock_sha256: None,
        state: "not_found",
        source_current: false,
        job_ref: None,
        frame_count: None,
        project_modified: false,
        automatic_retry: false,
    };
    let watermark = service
        .production_receipt_watermark(request.project_id)
        .map_err(|_| UNCONFIRMED)?;
    let Some(receipt) = service
        .latest_production_receipt(request.project_id, &request.request_id)
        .map_err(|_| UNCONFIRMED)?
    else {
        return Ok(Recovery {
            view,
            evidence: None,
            original_project: None,
            connection_identity: None,
        });
    };
    let intent = original_from_receipt(&receipt)?;
    if intent.source.generation != request.generation {
        return Err(UNCONFIRMED.into());
    }
    let evidence = original_result(service, &intent)?;
    view.state = if evidence.is_some() {
        "completed"
    } else if receipt.stage == "failed_known" {
        "failed_known"
    } else {
        "outcome_unknown"
    };
    view.source_current = current == intent.project;
    view.source = Some(intent.source.clone());
    view.fingerprint = Some(intent.fingerprint);
    view.runtime_lock_sha256 = Some(intent.runtime_lock_sha256);
    if let Some(evidence) = &evidence {
        view.job_ref = Some(evidence.job_ref.clone());
        view.frame_count = Some(evidence.source.composition.frame_count);
    }
    if service
        .production_receipt_watermark(request.project_id)
        .map_err(|_| UNCONFIRMED)?
        != watermark
    {
        return Err("Original operation history changed during recovery; read it again.".into());
    }
    Ok(Recovery {
        view,
        evidence,
        original_project: Some(intent.project),
        connection_identity: Some(intent.connection_identity),
    })
}
