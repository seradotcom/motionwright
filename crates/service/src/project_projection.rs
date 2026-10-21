//! Read-only project pages share the Store snapshot and validation contract.
use crate::StudioService;
use uuid::Uuid;

pub use motionwright_storage::{
    MAX_NODE_PAGE_ITEMS, MAX_PROJECTION_RESPONSE_BYTES, MAX_SCENE_PAGE_ITEMS, NodePage,
    NodePageCursor, ProjectionError, ProjectionHead, ProjectionIdentity, ProjectionResult,
    ScenePage, ScenePageCursor, SceneRef, SceneSummary,
};

use motionwright_storage::StorageError;
use serde::Serialize;

/// Bounded public failure for read-only project projection commands.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "code", rename_all = "snake_case")]
pub enum ProjectionFailure {
    InvalidRequestOrContent,
    TooLarge { limit: usize },
    NotFound,
    StaleRevision { expected: String, actual: String },
    GenerationChanged,
    Unavailable,
}

impl From<ProjectionError> for ProjectionFailure {
    fn from(error: ProjectionError) -> Self {
        match error {
            ProjectionError::Invalid(_) => Self::InvalidRequestOrContent,
            ProjectionError::TooLarge { limit } => Self::TooLarge { limit },
            ProjectionError::Storage(error) => match error {
                StorageError::NotFound => Self::NotFound,
                StorageError::Conflict { expected, actual } => Self::StaleRevision {
                    expected: expected.to_string(),
                    actual: actual.to_string(),
                },
                StorageError::GenerationConflict => Self::GenerationChanged,
                _ => Self::Unavailable,
            },
        }
    }
}

impl StudioService {
    pub fn project_projection_head(&self, project_id: Uuid) -> ProjectionResult<ProjectionHead> {
        self.store.lock().project_projection_head(project_id)
    }

    pub fn project_scene_page(
        &self,
        identity: &ProjectionIdentity,
        cursor: Option<&ScenePageCursor>,
        limit: u16,
    ) -> ProjectionResult<ScenePage> {
        self.store
            .lock()
            .project_scene_page(identity, cursor, limit)
    }

    pub fn project_scene_nodes(
        &self,
        identity: &ProjectionIdentity,
        scene: &SceneRef,
        cursor: Option<&NodePageCursor>,
        limit: u16,
    ) -> ProjectionResult<NodePage> {
        self.store
            .lock()
            .project_scene_nodes(identity, scene, cursor, limit)
    }
}
