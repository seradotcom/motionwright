//! Source-bound, read-only combination inspection. This grants no edit authority.
use crate::StudioService;
use motionwright_domain::{BranchCombination, CreativePatchPreview};
use motionwright_storage::{Result as StorageResult, StorageError};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BranchCombinationPreviewRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub input: BranchCombination,
}

impl StudioService {
    /// Inspect one canonical snapshot; the later mutation still needs its
    /// ordinary ProjectEdit grant and exact CAS. No receipt is minted here.
    pub fn preview_branch_combination(
        &self,
        request: &BranchCombinationPreviewRequest,
    ) -> StorageResult<CreativePatchPreview> {
        let project = self.project(request.project_id)?;
        if project.generation != request.generation {
            return Err(StorageError::GenerationConflict);
        }
        if project.revision != request.revision {
            return Err(StorageError::Conflict {
                expected: request.revision,
                actual: project.revision,
            });
        }
        // The pure domain function also verifies the resource, generation,
        // revision, active base and both exact saved branch heads.
        Ok(project.preview_branch_combination(&request.input)?)
    }
}
