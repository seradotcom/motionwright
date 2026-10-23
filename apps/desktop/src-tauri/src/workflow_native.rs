//! Desktop workflow dispatch through the existing durable production boundary.
//!
//! This module is also compiled by the native integration fixture so that the
//! tested route is the route called by Tauri. It does not create a second job
//! store, transport, retry loop, or grant authority.
use crate::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::RevisionStamp;
use motionwright_native::production::{
    ProductionConnection, ProductionCoordinator, ProductionReceiptCustody,
};
use motionwright_service::StudioService;
use semwright_native_sdk::ErrorCode;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;
use uuid::Uuid;

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowAction {
    RecordStart,
    RecordStop,
    Compile,
    SuggestionCompile,
    ProposalPlan,
    ProposalAccept,
    Verify,
    Replay,
    Promote,
}

impl WorkflowAction {
    pub fn command(self) -> &'static str {
        match self {
            Self::RecordStart => "workflow.record.start",
            Self::RecordStop => "workflow.record.stop",
            Self::Compile => "workflow.compile",
            Self::SuggestionCompile => "workflow.suggestion.compile",
            Self::ProposalPlan => "workflow.proposal.plan",
            Self::ProposalAccept => "workflow.proposal.accept",
            Self::Verify => "workflow.verify",
            Self::Replay => "workflow.replay",
            Self::Promote => "workflow.promote",
        }
    }

    pub fn mutation(self) -> bool {
        !matches!(self, Self::ProposalPlan)
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowActionRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    /// Created once by the caller before dispatch and retained across reopen.
    /// It is not the independently assigned Broker request identifier.
    pub request_id: String,
    pub action: WorkflowAction,
    pub effect_grant: Option<Uuid>,
    pub args: Value,
}

/// A projection of the existing local receipt, never its private payload.
/// Deserialization intentionally reads the header of a full canonical receipt;
/// serialization emits only these fields.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkflowReceiptIdentity {
    pub id: Uuid,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub request_id: String,
    pub request_sha256: String,
    pub command: String,
    pub stage: String,
}

#[derive(Debug, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum WorkflowReceiptObservation {
    NotChecked,
    /// An observation of absence is not permission to redispatch.
    NotObserved,
    /// Storage errors must not be represented as an absent receipt.
    Unavailable,
    Recorded {
        identity: Box<WorkflowReceiptIdentity>,
    },
}

#[derive(Debug, Serialize)]
pub struct WorkflowActionFailure {
    pub schema: &'static str,
    pub command: &'static str,
    pub request_id: Option<String>,
    /// Actual pinned SDK code, not a stringified provider diagnostic.
    pub code: ErrorCode,
    /// Describes this invocation. A known rejection of changed input does not
    /// resolve a prior UNKNOWN represented by the receipt observation.
    pub outcome_known: bool,
    pub message: &'static str,
    pub receipt: WorkflowReceiptObservation,
}

#[derive(Debug, Serialize)]
pub struct WorkflowActionResponse {
    pub schema: &'static str,
    pub command: &'static str,
    pub request_id: String,
    pub broker_request_id: String,
    pub replayed: bool,
    pub authority: Option<Value>,
    pub result: Value,
    pub receipt: WorkflowReceiptObservation,
}

fn valid_request_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.trim() == value
        && !value.chars().any(char::is_control)
}

fn failure(
    request: &WorkflowActionRequest,
    code: ErrorCode,
    outcome_known: bool,
    message: &'static str,
    receipt: WorkflowReceiptObservation,
) -> WorkflowActionFailure {
    WorkflowActionFailure {
        schema: "motionwright.workflow-action-failure/1",
        command: request.action.command(),
        request_id: valid_request_id(&request.request_id).then(|| request.request_id.clone()),
        code,
        outcome_known,
        message,
        receipt,
    }
}

fn observe_receipt(
    service: &StudioService,
    request: &WorkflowActionRequest,
) -> WorkflowReceiptObservation {
    match service.latest_production_receipt(request.project_id, &request.request_id) {
        Ok(Some(receipt)) => WorkflowReceiptObservation::Recorded {
            identity: Box::new(WorkflowReceiptIdentity {
                id: receipt.id,
                project_id: receipt.project_id,
                generation: receipt.generation,
                revision: receipt.revision,
                request_id: receipt.request_id,
                request_sha256: receipt.request_sha256,
                command: receipt.command,
                stage: receipt.stage,
            }),
        },
        Ok(None) => WorkflowReceiptObservation::NotObserved,
        Err(_) => WorkflowReceiptObservation::Unavailable,
    }
}

/// The selected path comes from the backend registry. It is not accepted from
/// the WebView request. Current project scope and the connection are checked
/// before consuming the existing one-time mutation grant.
///
/// This fixes local desktop dispatch lineage. It does not claim authentic
/// Broker-session receipt binding; that remains the canonical actor/recovery
/// contract, and connection.identity() is never substituted for that actor.
pub async fn execute_workflow_action(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    custody: &ProductionReceiptCustody,
    selected_connection: Result<Option<PathBuf>, String>,
    request: WorkflowActionRequest,
) -> Result<WorkflowActionResponse, WorkflowActionFailure> {
    let rejected = |code, message| {
        failure(
            &request,
            code,
            true,
            message,
            WorkflowReceiptObservation::NotChecked,
        )
    };
    if !valid_request_id(&request.request_id) {
        return Err(rejected(
            ErrorCode::InvalidArgument,
            "Workflow request identity is invalid.",
        ));
    }
    let project = service.project(request.project_id).map_err(|_| {
        rejected(
            ErrorCode::Unavailable,
            "The current workflow project could not be read.",
        )
    })?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err(rejected(
            ErrorCode::StaleReference,
            "Workflow scope changed. Inspect the original request before preparing another action.",
        ));
    }
    // The current local project scope has been checked. A later known refusal
    // must still expose the original receipt header, including UNKNOWN.
    // This read is historical metadata, never permission to execute.
    let observed_rejection = |code, message| {
        failure(
            &request,
            code,
            true,
            message,
            observe_receipt(service, &request),
        )
    };
    let connection_path = selected_connection
        .map_err(|_| {
            observed_rejection(
                ErrorCode::Unavailable,
                "The canonical workflow connection could not be selected.",
            )
        })?
        .ok_or_else(|| {
            observed_rejection(
                ErrorCode::Unavailable,
                "Canonical workflow actions are not configured.",
            )
        })?;
    let connection = ProductionConnection::load(connection_path).map_err(|_| {
        observed_rejection(
            ErrorCode::Unavailable,
            "The canonical workflow connection could not be loaded.",
        )
    })?;
    if connection.resource != project.resource_key() {
        return Err(observed_rejection(
            ErrorCode::PermissionDenied,
            "The canonical workflow connection belongs to another resource.",
        ));
    }
    let coordinator =
        ProductionCoordinator::with_receipt_custody(service.clone(), connection, custody).map_err(
            |error| {
                observed_rejection(
                    error.code,
                    "The canonical workflow connection was rejected.",
                )
            },
        )?;
    let command = request.action.command();
    let mutation = request.action.mutation();
    if mutation {
        let token = request.effect_grant.ok_or_else(|| {
            observed_rejection(
                ErrorCode::PermissionDenied,
                "A workflow mutation requires its current one-time effect grant.",
            )
        })?;
        grants
            .consume(
                token,
                EffectKind::WorkflowMutation,
                EffectScope::project(request.project_id, request.generation, request.revision),
                command,
            )
            .map_err(|_| {
                observed_rejection(
                    ErrorCode::PermissionDenied,
                    "The workflow effect grant is missing, consumed, expired, or outside its scope.",
                )
            })?;
    } else if request.effect_grant.is_some() {
        return Err(observed_rejection(
            ErrorCode::InvalidArgument,
            "A read-only workflow action cannot consume a mutation grant.",
        ));
    }

    let expected = RevisionStamp::from(&project);
    // The canonical coordinator owns the digest, durable dispatch claim,
    // terminal append, and prior-key refusal. Never dispatch around its error.
    let reply = coordinator
        .execute(
            request.project_id,
            &expected,
            &request.request_id,
            command,
            request.args.clone(),
            mutation,
        )
        .await
        .map_err(|error| {
            failure(
                &request,
                error.code,
                error.outcome_known,
                if error.outcome_known {
                    "The workflow action did not complete successfully. Inspect its original receipt."
                } else {
                    "The workflow outcome is unknown. Preserve this request and inspect its receipt; do not repeat the mutation."
                },
                observe_receipt(service, &request),
            )
        })?;
    let malformed_reply = || {
        failure(
            &request,
            ErrorCode::BackendFailed,
            !mutation,
            "The workflow reply could not be projected. Preserve and inspect the original request.",
            observe_receipt(service, &request),
        )
    };
    let identity: WorkflowReceiptIdentity = reply
        .get("receipt")
        .cloned()
        .and_then(|value| serde_json::from_value(value).ok())
        .ok_or_else(malformed_reply)?;
    if identity.project_id != request.project_id
        || identity.generation != request.generation
        || identity.revision != request.revision
        || identity.request_id != request.request_id
        || identity.command != command
        || identity.stage != "completed"
    {
        return Err(malformed_reply());
    }
    let broker_request_id = reply
        .pointer("/receipt/payload/broker_request_id")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .ok_or_else(malformed_reply)?
        .to_owned();
    let replayed = reply
        .get("replayed")
        .and_then(Value::as_bool)
        .ok_or_else(malformed_reply)?;
    let result = reply
        .pointer("/result/data")
        .cloned()
        .ok_or_else(malformed_reply)?;
    Ok(WorkflowActionResponse {
        schema: "motionwright.workflow-action-response/1",
        command,
        request_id: request.request_id,
        broker_request_id,
        replayed,
        authority: reply.pointer("/result/execution/provenance").cloned(),
        result,
        receipt: WorkflowReceiptObservation::Recorded {
            identity: Box::new(identity),
        },
    })
}
