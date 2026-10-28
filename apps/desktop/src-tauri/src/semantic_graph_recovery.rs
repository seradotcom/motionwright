//! Pure private DTO validation for a historical Graph operation read.
//!
//! The Desktop owner supplies the existing invocation lifetime, actual held
//! Store binding and authenticated Native read. This module opens no Store,
//! creates no connection or authority, and dispatches no mutation or retry.
//! A failed read never establishes the outcome of the original mutation.

use motionwright_domain::{Change, GraphSourceSnapshot, Project};
use motionwright_native::production::NativeGraphRecipeClient;
use semwright_native_sdk::cooperation::{MAX_SAFE_INTEGER, RecoveryRecord, RequestIdentity};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use uuid::Uuid;

const RESPONSE_SCHEMA: &str = "motionwright.semantic-graph-operation-recovery/1";

/// The caller retains the exact original key and typed Change. Recovery
/// identities, current authority and physical Store bindings are not IPC inputs.
#[derive(Debug)]
pub(crate) struct GraphRecoveryRequest {
    request_id: String,
    change: Change,
}

// A derived struct alone also accepts positional sequences. IPC requires an
// object, while this inner closed struct supplies duplicate/missing-key checks.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GraphRecoveryRequestFields {
    request_id: String,
    change: Change,
}

impl<'de> Deserialize<'de> for GraphRecoveryRequest {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct RequestObjectVisitor;

        impl<'de> serde::de::Visitor<'de> for RequestObjectVisitor {
            type Value = GraphRecoveryRequest;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a closed Graph recovery request object")
            }

            fn visit_map<M>(self, map: M) -> Result<Self::Value, M::Error>
            where
                M: serde::de::MapAccess<'de>,
            {
                let fields = GraphRecoveryRequestFields::deserialize(
                    serde::de::value::MapAccessDeserializer::new(map),
                )?;
                Ok(GraphRecoveryRequest {
                    request_id: fields.request_id,
                    change: fields.change,
                })
            }
        }

        deserializer.deserialize_map(RequestObjectVisitor)
    }
}

/// Constructible only by validating the original request. There is deliberately
/// no Deserialize implementation and no caller-supplied RequestIdentity.
#[derive(Debug)]
pub(crate) struct PreparedGraphRecovery {
    request_id: String,
    change: Change,
    source_snapshot: GraphSourceSnapshot,
    identity: RequestIdentity,
}

#[derive(Debug, Serialize)]
pub(crate) struct GraphRecoveryResponse {
    schema: &'static str,
    request_id: String,
    source_snapshot: GraphSourceSnapshot,
    record: GraphRecoveryState,
}

#[derive(Debug, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
enum GraphRecoveryState {
    Recorded {
        /// This is the original receipt's Project, never a current projection.
        historical_project: Box<Project>,
    },
    Pending,
    OutcomeUnknown,
    RetentionExpired {
        current_epoch: u64,
    },
}

/// These are read/request errors. In particular, they contain no mutation
/// outcome_known flag and must never be encoded as a Graph mutation failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum GraphRecoveryReadError {
    InvalidRequest,
    InvalidRecord,
    ReadFailed,
}

impl std::fmt::Display for GraphRecoveryReadError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(match self {
            Self::InvalidRequest => "Graph recovery request is invalid",
            Self::InvalidRecord => "Graph recovery record could not be validated",
            Self::ReadFailed => "Graph recovery read could not be completed",
        })
    }
}

impl std::error::Error for GraphRecoveryReadError {}

impl GraphRecoveryRequest {
    pub(crate) fn prepare(self) -> Result<PreparedGraphRecovery, GraphRecoveryReadError> {
        // Preserve the original spelling, including case; parsing is validation
        // only. Replacing this with Uuid::to_string would change the request key.
        let key = Uuid::parse_str(&self.request_id)
            .map_err(|_| GraphRecoveryReadError::InvalidRequest)?;
        if self.request_id.len() != 36 || key.is_nil() {
            return Err(GraphRecoveryReadError::InvalidRequest);
        }

        let source_snapshot = match &self.change {
            Change::UpsertSemanticGraph { graph } => &graph.authorship.source_snapshot,
            Change::RemoveSemanticGraph {
                source_snapshot, ..
            } => source_snapshot,
            _ => return Err(GraphRecoveryReadError::InvalidRequest),
        };
        if source_snapshot.project_id.is_nil()
            || source_snapshot.generation.is_nil()
            || source_snapshot.branch_id.is_nil()
            || source_snapshot.revision > MAX_SAFE_INTEGER
        {
            return Err(GraphRecoveryReadError::InvalidRequest);
        }
        let source_snapshot = source_snapshot.clone();

        // These public associated methods use the canonical Native/Store V1
        // digest. They require neither a client constructor nor any current
        // Graph existence, current revision, connection, grant or ready state.
        let identity = match &self.change {
            Change::UpsertSemanticGraph { graph } => {
                NativeGraphRecipeClient::prepare_upsert(&self.request_id, graph)
            }
            Change::RemoveSemanticGraph {
                graph_id,
                scene_id,
                source_snapshot,
            } => NativeGraphRecipeClient::prepare_remove(
                &self.request_id,
                *graph_id,
                *scene_id,
                source_snapshot,
            ),
            _ => return Err(GraphRecoveryReadError::InvalidRequest),
        }
        .map_err(|_| GraphRecoveryReadError::InvalidRequest)?;

        Ok(PreparedGraphRecovery {
            request_id: self.request_id,
            change: self.change,
            source_snapshot,
            identity,
        })
    }
}

impl PreparedGraphRecovery {
    pub(crate) fn request_id(&self) -> &str {
        &self.request_id
    }

    pub(crate) fn source_snapshot(&self) -> &GraphSourceSnapshot {
        &self.source_snapshot
    }

    pub(crate) fn identity(&self) -> &RequestIdentity {
        &self.identity
    }

    /// Call only with the outcome of the actual bound Native recovery read.
    /// The owner must retain the original exchange/custody separately. Neither
    /// a freshly loaded Project nor project/history equality is such a read.
    ///
    /// All transport/policy/decoder errors describe this read alone, even when
    /// their own outcome is known. Deliberately do not inspect or forward them
    /// as evidence that the original Graph mutation did not apply.
    pub(crate) fn decode_read<E>(
        &self,
        outcome: Result<&RecoveryRecord, E>,
    ) -> Result<GraphRecoveryResponse, GraphRecoveryReadError> {
        let record = outcome.map_err(|_| GraphRecoveryReadError::ReadFailed)?;
        record
            .validate_for(&self.identity)
            .map_err(|_| GraphRecoveryReadError::InvalidRecord)?;
        let record = match record {
            RecoveryRecord::Recorded { result, .. } => GraphRecoveryState::Recorded {
                historical_project: Box::new(self.decode_recorded_project(result)?),
            },
            RecoveryRecord::Pending { .. } => GraphRecoveryState::Pending,
            RecoveryRecord::OutcomeUnknown { .. } => GraphRecoveryState::OutcomeUnknown,
            RecoveryRecord::RetentionExpired { current_epoch, .. } => {
                GraphRecoveryState::RetentionExpired {
                    current_epoch: *current_epoch,
                }
            }
        };
        Ok(GraphRecoveryResponse {
            schema: RESPONSE_SCHEMA,
            request_id: self.request_id.clone(),
            source_snapshot: self.source_snapshot.clone(),
            record,
        })
    }

    fn decode_recorded_project(&self, result: &Value) -> Result<Project, GraphRecoveryReadError> {
        // The durable historical projection has seven fields. The eight-field
        // immediate-save DTO additionally carries `recovery` and is not this
        // format; never synthesize metadata to make that decoder accept it.
        if !result.is_object() {
            return Err(GraphRecoveryReadError::InvalidRecord);
        }
        let receipt: HistoricalGraphReceipt = serde_json::from_value(result.clone())
            .map_err(|_| GraphRecoveryReadError::InvalidRecord)?;
        // Project supports legacy/default fields for ordinary project loading.
        // A current typed Store projection already has its serialized shape;
        // recovery must not silently discard unknown fields or fill omissions.
        let project_projection = serde_json::to_value(&receipt.project)
            .map_err(|_| GraphRecoveryReadError::InvalidRecord)?;
        if result.get("project") != Some(&project_projection) {
            return Err(GraphRecoveryReadError::InvalidRecord);
        }
        let previous_revision = canonical_revision(&receipt.previous_revision)?;
        let revision = canonical_revision(&receipt.revision)?;
        let expected_revision = self
            .source_snapshot
            .revision
            .checked_add(1)
            .filter(|next| *next <= MAX_SAFE_INTEGER)
            .ok_or(GraphRecoveryReadError::InvalidRecord)?;
        let source = &self.source_snapshot;
        if receipt.resource != self.identity.resource
            || receipt.resource != format!("project:{}", source.project_id)
            || receipt.generation != source.generation.to_string()
            || receipt.request_id != self.request_id
            || previous_revision != source.revision
            || revision != expected_revision
            || receipt.project.id != source.project_id
            || receipt.project.resource_key() != receipt.resource
            || receipt.project.generation != source.generation
            || receipt.project.active_branch != source.branch_id
            || receipt.project.revision != revision
        {
            return Err(GraphRecoveryReadError::InvalidRecord);
        }
        receipt
            .project
            .validate()
            .map_err(|_| GraphRecoveryReadError::InvalidRecord)?;

        match &self.change {
            Change::UpsertSemanticGraph { graph }
                if receipt
                    .project
                    .production_design
                    .semantic_graphs
                    .iter()
                    .any(|saved| saved == graph.as_ref()) => {}
            Change::RemoveSemanticGraph { graph_id, .. }
                if !receipt
                    .project
                    .production_design
                    .semantic_graphs
                    .iter()
                    .any(|saved| saved.id == *graph_id) => {}
            _ => return Err(GraphRecoveryReadError::InvalidRecord),
        }
        Ok(receipt.project)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct HistoricalGraphReceipt {
    resource: String,
    generation: String,
    revision: String,
    previous_revision: String,
    request_id: String,
    // Validate the original boolean but infer no new authority from its value.
    #[serde(rename = "replayed")]
    _replayed: bool,
    project: Project,
}

fn canonical_revision(value: &str) -> Result<u64, GraphRecoveryReadError> {
    let revision = value
        .parse::<u64>()
        .map_err(|_| GraphRecoveryReadError::InvalidRecord)?;
    if revision > MAX_SAFE_INTEGER || revision.to_string() != value {
        return Err(GraphRecoveryReadError::InvalidRecord);
    }
    Ok(revision)
}

#[cfg(test)]
#[path = "semantic_graph_recovery_tests.rs"]
mod tests;
