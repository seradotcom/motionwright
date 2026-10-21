//! Read-only project pages share the Store snapshot and validation contract.
use crate::StudioService;
use uuid::Uuid;

pub use motionwright_storage::{
    MAX_NODE_PAGE_ITEMS, MAX_PROJECTION_RESPONSE_BYTES, MAX_SCENE_PAGE_ITEMS, NodePage,
    NodePageCursor, ProjectionError, ProjectionHead, ProjectionIdentity, ProjectionResult,
    ScenePage, ScenePageCursor, SceneRef, SceneSummary,
};

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
