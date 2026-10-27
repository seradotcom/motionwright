//! Read-only previews for the existing canonical project journal.
//! No provider credentials, remote API, payment or task submission lives here.
use crate::{AppState, sanitized};
use motionwright_domain::Project;
use serde::Deserialize;
use serde_json::{Value, json};
use tauri::State;
use uuid::Uuid;

fn current(state: &AppState, id: Uuid, generation: Uuid, revision: u64) -> Result<Project, String> {
    let project = state.service.project(id).map_err(sanitized)?;
    if project.generation != generation || project.revision != revision {
        return Err(
            "Generation journal source changed; reload the current project before reviewing."
                .into(),
        );
    }
    Ok(project)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationIntentRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    specification: motionwright_domain::PaidGenerationSpec,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationJournalScope {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PaidGenerationReservationRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    job_id: Uuid,
}
/// Preflight an owner-authored source-bound request, WITHOUT contacting a
/// provider or billing/charging anything. No capability proof is forged.
#[tauri::command]
pub async fn native_paid_generation_preflight(
    _owner: crate::desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: PaidGenerationIntentRequest,
) -> Result<Value, String> {
    use motionwright_domain::PaidGenerationEdit;
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let mut spec = request.specification;
    // Exact idempotency is always derived by Rust from semantic inputs.
    // Regenerating the client UUID can never launch/bill the same unit twice.
    spec.logical_idempotency_sha256 = spec
        .expected_idempotency(project.id)
        .map_err(|e| e.to_string())?;
    spec.validate(&project).map_err(|e| e.to_string())?;
    let edit = PaidGenerationEdit::Create {
        spec: Box::new(spec.clone()),
    };
    let mut candidate = project.paid_generation.clone();
    candidate
        .apply(&project, &edit)
        .map_err(|e| e.to_string())?;
    let budget = candidate
        .budget_preview(&project)
        .map_err(|e| e.to_string())?;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.paid-generation-draft-preview/1","revision":project.revision,
        "project_id":project.id,"generation":project.generation,
        "normalized_edit":edit,
        "specification":spec,
        "budget":budget,
        "provider_capability_signature_verified":false,
        "owner_execution_grant":false,
        "provider_called":false,"payment_charged":false,"committed":false
    }))
}
/// Owner UI can reserve the exact ledger amount through existing StudioService
/// CAS. This is an accounting record, not permission to contact a paid API.
#[tauri::command]
pub async fn native_paid_generation_reserve_preflight(
    _owner: crate::desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: PaidGenerationReservationRequest,
) -> Result<Value, String> {
    use motionwright_domain::{PaidGenerationEdit, PaidGenerationEvent, PaidGenerationEventKind};
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let ledger = &project.paid_generation;
    let job = ledger
        .jobs
        .iter()
        .find(|job| job.spec.id == request.job_id)
        .ok_or_else(|| "The paid draft no longer belongs to this project.".to_string())?;
    let before = job.history_sha256().map_err(|e| e.to_string())?;
    let event = PaidGenerationEvent {
        id: Uuid::new_v4(),
        kind: PaidGenerationEventKind::Reserve {
            maximum_charge_microusd: job.spec.max_charge_microusd,
        },
    };
    let edit = PaidGenerationEdit::Append {
        job_id: request.job_id,
        expected_history_sha256: before,
        event,
    };
    let mut candidate = ledger.clone();
    candidate
        .apply(&project, &edit)
        .map_err(|e| e.to_string())?;
    let budget = candidate
        .budget_preview(&project)
        .map_err(|e| e.to_string())?;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.paid-generation-budget-preflight/1","revision":project.revision,
        "project_id":project.id,"generation":project.generation,
        "normalized_edit":edit,"budget":budget,
        "committed":false,"provider_called":false,"payment_charged":false,
        "owner_execution_grant":false
    }))
}
#[tauri::command]
pub async fn native_paid_generation_journal(
    _owner: crate::desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: PaidGenerationJournalScope,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let ledger = &project.paid_generation;
    let budget = ledger.budget_preview(&project).map_err(|e| e.to_string())?;
    let mut rows = Vec::with_capacity(ledger.jobs.len());
    for job in &ledger.jobs {
        let reconcile = if matches!(
            job.state,
            motionwright_domain::PaidGenerationState::UnknownOutcome
        ) {
            Some(job.reconcile_query().map_err(|e| e.to_string())?)
        } else {
            None
        };
        rows.push(json!({
            "job_id":job.spec.id,"kind":job.spec.kind,"scope":job.spec.scope,
            "state":job.state,"history_sha256":job.history_sha256().map_err(|e|e.to_string())?,
            "provider_id":job.spec.provider_id,"model_id":job.spec.model_id,
            "model_version":job.spec.model_version,
            "variant_index":job.spec.variant_index,
            "max_charge_microusd":job.spec.max_charge_microusd,
            "source_sha256":job.spec.logical_idempotency_sha256,
            "known_task_id":job.task_id,
            "reconciliation_query":reconcile,
            "reported_spend_microusd":job.provider_reported_spend_microusd,
            "owner_approval_is_authenticated":false,
            "provider_success_is_authenticated":false,
        }));
    }
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.paid-generation-ledger-observation/1",
        "project_id":project.id,"generation":project.generation,
        "revision":project.revision,"rows":rows,"budget":budget,
        "read_only":true,"remote_api_called":false,
        "execution_authority":"NONE_GRANTED"
    }))
}
