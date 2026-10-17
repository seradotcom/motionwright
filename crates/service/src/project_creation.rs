use super::{ProjectCreationIntent, ProjectCreationOutcome, StorageResult, StudioService};

impl StudioService {
    /// The caller must bind its authorization to the trusted Store/window and
    /// this exact intent. Persist the intent before the first dispatch.
    pub fn create_project_with_intent(
        &self,
        intent: &ProjectCreationIntent,
    ) -> StorageResult<ProjectCreationOutcome> {
        self.store.lock().create_project_with_intent(intent)
    }

    /// Read only. None does not authorize another creation attempt.
    /// Refresh the current project separately from this historical result.
    pub fn recover_project_creation(
        &self,
        intent: &ProjectCreationIntent,
    ) -> StorageResult<Option<ProjectCreationOutcome>> {
        self.store.lock().recover_project_creation(intent)
    }
}
