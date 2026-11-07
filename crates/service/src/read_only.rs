//! Read-only service surface for an existing Store. No effects or grants are exposed.
use super::StudioService;
use motionwright_domain::Project;
use motionwright_storage::{ProjectEvent, Result, StoreReader};
use std::path::Path;
use uuid::Uuid;

/// Reuses the product's Project and journal reads through a retained read-only Store.
/// Reopening requires a new reader and never falls back to the writer constructor.
pub struct StudioServiceReader {
    store: StoreReader,
}

impl StudioService {
    /// Complete the legitimate writer's SQLite handoff before its final close.
    pub fn prepare_read_only_handoff(&self) -> Result<()> {
        self.store.lock().prepare_read_only_handoff()
    }
}

impl StudioServiceReader {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        Ok(Self {
            store: StoreReader::open(path)?,
        })
    }

    pub fn project(&self, id: Uuid) -> Result<Project> {
        self.store.project(id)
    }

    pub fn projects(&self, limit: usize) -> Result<Vec<Project>> {
        self.store.projects(limit)
    }

    pub fn project_history(
        &self,
        id: Uuid,
        after_revision: u64,
        limit: usize,
    ) -> Result<Vec<ProjectEvent>> {
        self.store.project_history(id, after_revision, limit)
    }

    pub fn database_file_identity(&self) -> Result<String> {
        self.store.database_file_identity()
    }
}
