//! Read-only Graph context from the existing StudioService/SQLite authority.
//!
//! Real source descriptors remain blocked until a matching native consumer
//! and its dependency checks are available. Caller flags and DTOs cannot admit
//! Graph writes or mark native execution ready.
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
    /// approval endpoint or a capability. Subsequent writes still pass the
    /// ordinary effect grant, revision/request identity and service checks.
    pub fn semantic_graph_authoring_context(
        &self,
        request: &SemanticGraphContextRequest,
    ) -> StorageResult<GraphAuthoringContext> {
        let store = self.store.lock();
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
        let mut blocking_reasons = vec![
            GraphBlockReason::GraphWriteUnavailable,
            GraphBlockReason::NativeGraphProfileUnavailable,
            GraphBlockReason::UnsupportedRendererGuardMissing,
        ];
        if scene.renderer != RendererKind::ManimCommunity
            || (!profile.included_scene_ids.is_empty()
                && !profile.included_scene_ids.contains(&scene.id))
        {
            blocking_reasons.push(GraphBlockReason::ProfileUnsupported);
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
            availability: GraphAvailability::Blocked,
            blocking_reasons,
            dependencies: project.semantic_graph_dependencies()?,
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
