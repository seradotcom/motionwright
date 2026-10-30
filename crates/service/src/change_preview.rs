//! Read-only domain validation of a proposed edit on one observed Store snapshot.
use crate::StudioService;
use motionwright_domain::{Change, RevisionStamp};
use motionwright_storage::{Result as StorageResult, StorageError};
use serde::Serialize;
use uuid::Uuid;

/// A bounded description of a proposal, not a write receipt or authorization.
/// Generated identifiers belong only to the disposable candidate and are not
/// exposed: the eventual mutation must obtain its own authority and exact CAS.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ChangePreview {
    pub base: RevisionStamp,
    pub candidate_title: String,
    pub candidate_scene_count: usize,
    pub committed: bool,
    pub exact_future_ids_proven: bool,
}

impl StudioService {
    /// Validate the same domain Change used by apply without opening a write
    /// transaction, creating a receipt, importing assets or invoking a provider.
    ///
    /// This only validates the domain proposal. Storage-owned source evidence,
    /// effect grants and the current revision are checked again by the real
    /// mutation. A concurrent writer cannot be authorized by this preview.
    pub fn preview_change(
        &self,
        id: Uuid,
        expected: &RevisionStamp,
        change: &Change,
    ) -> StorageResult<ChangePreview> {
        let mut candidate = self.project(id)?;
        if expected.resource != candidate.resource_key()
            || expected.generation != candidate.generation
        {
            return Err(StorageError::GenerationConflict);
        }
        if expected.revision != candidate.revision {
            return Err(StorageError::Conflict {
                expected: expected.revision,
                actual: candidate.revision,
            });
        }
        let base = RevisionStamp::from(&candidate);
        candidate.apply_change(change)?;
        candidate.validate()?;
        Ok(ChangePreview {
            base,
            candidate_title: candidate.title,
            candidate_scene_count: candidate.scenes.len(),
            committed: false,
            exact_future_ids_proven: false,
        })
    }
}
