//! Read-only Graph context from the existing StudioService/SQLite authority.
//!
//! The ordinary context remains blocked. A trusted native admission adapter
//! may inspect authoring availability, but neither that observation nor caller
//! flags or DTOs can authorize a Graph write or mark native execution ready.
use crate::semantic_graph_admission::SemanticGraphAdmission;
use crate::{MAX_PROJECTION_RESPONSE_BYTES, StorageResult, StudioService};
use motionwright_domain::{
    Change, DomainError, GraphAuthoringContext, GraphAvailability, GraphBlockReason,
    GraphFontSelection, GraphSourceSnapshot, RendererKind, SEMANTIC_GRAPH_COMPONENT_VERSION,
    SEMANTIC_GRAPH_FONT_WEIGHT, SEMANTIC_GRAPH_PROFILE,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticGraphContextRequest {
    pub source_snapshot: GraphSourceSnapshot,
    pub scene_id: Uuid,
    pub variant_profile_ref: Uuid,
}

impl StudioService {
    /// This is an inspection of the selected persisted Project, never an
    /// approval endpoint or a capability. Without a trusted native adapter,
    /// authoring remains blocked and subsequent writes remain denied.
    pub fn semantic_graph_authoring_context(
        &self,
        request: &SemanticGraphContextRequest,
    ) -> StorageResult<GraphAuthoringContext> {
        self.inspect_semantic_graph_authoring_context(request, None)
    }

    /// Inspect authoring through the same trusted native adapter used by the
    /// admitted source-save boundary. Ready describes this source inspection,
    /// not a write permit, native execution readiness or render acceptance.
    /// Every save must still acquire and retain its own real operation lease.
    /// The caller must retain its authenticated Desktop owner or Host read
    /// boundary: even a blocked response contains project dependencies, so
    /// this inspection method does not replace that existing access check.
    pub fn semantic_graph_authoring_context_with_admission(
        &self,
        request: &SemanticGraphContextRequest,
        admission: &dyn SemanticGraphAdmission,
    ) -> StorageResult<GraphAuthoringContext> {
        self.inspect_semantic_graph_authoring_context(request, Some(admission))
    }

    fn inspect_semantic_graph_authoring_context(
        &self,
        request: &SemanticGraphContextRequest,
        admission: Option<&dyn SemanticGraphAdmission>,
    ) -> StorageResult<GraphAuthoringContext> {
        let store = self.store.lock();
        if let Some(admission) = admission {
            admission.inspect_store(&store)?;
        }
        let project = store.load_project(request.source_snapshot.project_id)?;
        request.source_snapshot.require_current(&project)?;
        let scene = project
            .scenes
            .iter()
            .find(|scene| scene.id == request.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", request.scene_id)))?;
        let profile = project
            .deliverables
            .iter()
            .find(|profile| profile.id == request.variant_profile_ref)
            .ok_or_else(|| {
                DomainError::NotFound(format!("deliverable:{}", request.variant_profile_ref))
            })?;
        let mut blocking_reasons = if admission.is_some() {
            Vec::new()
        } else {
            vec![
                GraphBlockReason::GraphWriteUnavailable,
                GraphBlockReason::NativeGraphProfileUnavailable,
                GraphBlockReason::UnsupportedRendererGuardMissing,
            ]
        };
        let dependencies = project.semantic_graph_dependencies()?;
        if scene.renderer != RendererKind::ManimCommunity
            || (!profile.included_scene_ids.is_empty()
                && !profile.included_scene_ids.contains(&scene.id))
        {
            blocking_reasons.push(GraphBlockReason::ProfileUnsupported);
        } else if let Some(admission) = admission {
            admission.inspect_authoring(&project, request)?;
        }
        let context = GraphAuthoringContext {
            schema: "motionwright.semantic-graph-authoring-context/1",
            source_snapshot: GraphSourceSnapshot::from_project(&project),
            scene_id: scene.id,
            variant_profile_ref: profile.id,
            component_version: SEMANTIC_GRAPH_COMPONENT_VERSION,
            native_profile: SEMANTIC_GRAPH_PROFILE,
            font_weight: SEMANTIC_GRAPH_FONT_WEIGHT,
            // Declared families required by this closed profile. Their names
            // alone do not assert that any font bytes/rights were admitted.
            font_selection: GraphFontSelection {
                primary_family: "Instrument Sans Variable".into(),
                mono_family: "IBM Plex Mono".into(),
            },
            availability: if blocking_reasons.is_empty() {
                GraphAvailability::Ready
            } else {
                GraphAvailability::Blocked
            },
            blocking_reasons,
            dependencies,
        };
        let encoded = serde_json::to_vec(&context)
            .map_err(|error| DomainError::Invalid(error.to_string()))?;
        if encoded.len() > MAX_PROJECTION_RESPONSE_BYTES {
            return Err(DomainError::Invalid(
                "Graph authoring context exceeds the existing projection response budget".into(),
            )
            .into());
        }
        // A second Store handle/process must not replace content under an
        // apparently identical stamp while dependency fingerprints are read.
        if store.load_project(project.id)? != project {
            return Err(DomainError::Invalid(
                "Graph source changed during authoring inspection".into(),
            )
            .into());
        }
        Ok(context)
    }

    pub(crate) fn require_semantic_graph_write_admission(
        &self,
        change: &Change,
    ) -> StorageResult<()> {
        if matches!(
            change,
            Change::UpsertSemanticGraph { .. } | Change::RemoveSemanticGraph { .. }
        ) {
            return Err(DomainError::Invalid(
                "Semantic Graph writes remain unavailable until the real native consumer, font admission and incompatible-renderer guards are composed".into(),
            ).into());
        }
        Ok(())
    }
}
