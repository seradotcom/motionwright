use crate::{ApplyReceiptObservation, StorageError, StorageResult, StudioService};
use motionwright_domain::{Change, DomainError, RationalTime, RevisionStamp};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneSplitRequest {
    project_id: Uuid,
    generation: Uuid,
    branch_id: Uuid,
    revision: u64,
    scene_id: Uuid,
    right_scene_id: Uuid,
    at: RationalTime,
}

impl SceneSplitRequest {
    fn change(&self) -> Change {
        Change::SplitScene {
            scene_id: self.scene_id,
            right_scene_id: self.right_scene_id,
            at: self.at,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct SceneSplitPreview {
    scene_id: Uuid,
    right_scene_id: Uuid,
    revision: u64,
    at: RationalTime,
    left_duration: RationalTime,
    right_duration: RationalTime,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneSplitReceiptRequest {
    request_id: Uuid,
    split: SceneSplitRequest,
}

impl StudioService {
    pub fn preview_scene_split(
        &self,
        request: &SceneSplitRequest,
    ) -> StorageResult<SceneSplitPreview> {
        let mut candidate = self.project(request.project_id)?;
        if candidate.id != request.project_id
            || candidate.generation != request.generation
            || candidate.active_branch != request.branch_id
        {
            return Err(StorageError::GenerationConflict);
        }
        if candidate.revision != request.revision {
            return Err(StorageError::Conflict {
                expected: request.revision,
                actual: candidate.revision,
            });
        }
        candidate.apply_change(&request.change())?;
        let duration = |id| {
            candidate
                .scenes
                .iter()
                .find(|scene| scene.id == id)
                .map(|scene| scene.duration)
                .ok_or_else(|| DomainError::NotFound(format!("scene:{id}")))
        };
        Ok(SceneSplitPreview {
            scene_id: request.scene_id,
            right_scene_id: request.right_scene_id,
            revision: request.revision,
            at: request.at,
            left_duration: duration(request.scene_id)?,
            right_duration: duration(request.right_scene_id)?,
        })
    }

    pub fn observe_scene_split_receipt(
        &self,
        request: &SceneSplitReceiptRequest,
    ) -> StorageResult<ApplyReceiptObservation> {
        let split = &request.split;
        if request.request_id.is_nil() {
            return Err(DomainError::Invalid("invalid split request identity".into()).into());
        }
        let expected = RevisionStamp {
            resource: format!("project:{}", split.project_id),
            generation: split.generation,
            revision: split.revision,
        };
        let observed = self.store.lock().observe_apply_receipt_v1(
            split.project_id,
            &expected,
            &request.request_id.to_string(),
            &split.change(),
            motionwright_storage::APPLY_REQUEST_ENCODING_V1,
        )?;
        if let ApplyReceiptObservation::KnownVerifiedV1 { binding } = &observed {
            let branch = split.branch_id.to_string();
            if binding.base_branch != branch || binding.result_branch != branch {
                return Ok(ApplyReceiptObservation::AuthorityMismatch);
            }
        }
        Ok(observed)
    }
}
