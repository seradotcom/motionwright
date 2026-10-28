//! Read-only desktop adapters over the revision-bound StudioService projections.
//! Request identities are read markers; no mutation or consent is inferred.
use motionwright_service::{
    NodePage, NodePageCursor, ProjectionFailure, ProjectionHead, ProjectionIdentity, ScenePage,
    ScenePageCursor, SceneRef, StudioService,
};
use serde::Deserialize;
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Head {
    pub project_id: Uuid,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Scenes {
    pub identity: ProjectionIdentity,
    #[serde(default)]
    pub cursor: Option<ScenePageCursor>,
    pub limit: u16,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Nodes {
    pub identity: ProjectionIdentity,
    pub scene: SceneRef,
    #[serde(default)]
    pub cursor: Option<NodePageCursor>,
    pub limit: u16,
}

pub fn head(service: &StudioService, request: Head) -> Result<ProjectionHead, ProjectionFailure> {
    service
        .project_projection_head(request.project_id)
        .map_err(ProjectionFailure::from)
}

pub fn scenes(service: &StudioService, request: Scenes) -> Result<ScenePage, ProjectionFailure> {
    service
        .project_scene_page(&request.identity, request.cursor.as_ref(), request.limit)
        .map_err(ProjectionFailure::from)
}

pub fn nodes(service: &StudioService, request: Nodes) -> Result<NodePage, ProjectionFailure> {
    service
        .project_scene_nodes(
            &request.identity,
            &request.scene,
            request.cursor.as_ref(),
            request.limit,
        )
        .map_err(ProjectionFailure::from)
}
