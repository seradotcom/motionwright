use crate::{ApplyReceiptObservation, StorageError, StorageResult, StudioService};
use motionwright_domain::{Change, DomainError, Project, RationalTime, RevisionStamp};
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
    animated_objects: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneSplitReceiptRequest {
    request_id: Uuid,
    split: SceneSplitRequest,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneTrimRequest {
    project_id: Uuid,
    generation: Uuid,
    branch_id: Uuid,
    revision: u64,
    scene_id: Uuid,
    at: RationalTime,
}

impl SceneTrimRequest {
    fn change(&self) -> Change {
        Change::TrimSceneEnd {
            scene_id: self.scene_id,
            at: self.at,
        }
    }
}

#[derive(Debug, Serialize)]
pub struct SceneTrimPreview {
    scene_id: Uuid,
    revision: u64,
    at: RationalTime,
    old_duration: RationalTime,
    new_duration: RationalTime,
    shifted_scenes: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SceneTrimReceiptRequest {
    request_id: Uuid,
    trim: SceneTrimRequest,
}

impl StudioService {
    pub fn preview_scene_trim(
        &self,
        request: &SceneTrimRequest,
    ) -> StorageResult<SceneTrimPreview> {
        let mut candidate = self.scene_timing_source(
            request.project_id,
            request.generation,
            request.branch_id,
            request.revision,
        )?;
        let index = candidate
            .scenes
            .iter()
            .position(|scene| scene.id == request.scene_id)
            .ok_or_else(|| DomainError::NotFound(format!("scene:{}", request.scene_id)))?;
        let old_duration = candidate.scenes[index].duration;
        let following: Vec<_> = candidate.scenes[index + 1..]
            .iter()
            .map(|scene| scene.start)
            .collect();
        candidate.apply_change(&request.change())?;
        Ok(SceneTrimPreview {
            scene_id: request.scene_id,
            revision: request.revision,
            at: request.at,
            old_duration,
            new_duration: candidate.scenes[index].duration,
            shifted_scenes: candidate.scenes[index + 1..]
                .iter()
                .zip(following)
                .filter(|(scene, old_start)| scene.start != *old_start)
                .count(),
        })
    }

    pub fn observe_scene_trim_receipt(
        &self,
        request: &SceneTrimReceiptRequest,
    ) -> StorageResult<ApplyReceiptObservation> {
        let trim = &request.trim;
        self.observe_scene_timing_receipt(
            trim.project_id,
            RevisionStamp {
                resource: format!("project:{}", trim.project_id),
                generation: trim.generation,
                revision: trim.revision,
            },
            trim.branch_id,
            request.request_id,
            &trim.change(),
        )
    }
    pub fn preview_scene_split(
        &self,
        request: &SceneSplitRequest,
    ) -> StorageResult<SceneSplitPreview> {
        let mut candidate = self.scene_timing_source(
            request.project_id,
            request.generation,
            request.branch_id,
            request.revision,
        )?;
        let animated_objects = candidate
            .scenes
            .iter()
            .find(|scene| scene.id == request.scene_id)
            .map_or(0, |scene| {
                scene
                    .nodes
                    .iter()
                    .filter(|node| !node.keyframes.is_empty())
                    .count()
            });
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
            animated_objects,
        })
    }

    pub fn observe_scene_split_receipt(
        &self,
        request: &SceneSplitReceiptRequest,
    ) -> StorageResult<ApplyReceiptObservation> {
        let split = &request.split;
        self.observe_scene_timing_receipt(
            split.project_id,
            RevisionStamp {
                resource: format!("project:{}", split.project_id),
                generation: split.generation,
                revision: split.revision,
            },
            split.branch_id,
            request.request_id,
            &split.change(),
        )
    }

    fn scene_timing_source(
        &self,
        project_id: Uuid,
        generation: Uuid,
        branch_id: Uuid,
        revision: u64,
    ) -> StorageResult<Project> {
        let project = self.project(project_id)?;
        if project.id != project_id
            || project.generation != generation
            || project.active_branch != branch_id
        {
            return Err(StorageError::GenerationConflict);
        }
        if project.revision != revision {
            return Err(StorageError::Conflict {
                expected: revision,
                actual: project.revision,
            });
        }
        Ok(project)
    }

    fn observe_scene_timing_receipt(
        &self,
        project_id: Uuid,
        expected: RevisionStamp,
        branch_id: Uuid,
        request_id: Uuid,
        change: &Change,
    ) -> StorageResult<ApplyReceiptObservation> {
        if request_id.is_nil() {
            return Err(DomainError::Invalid("invalid scene edit request identity".into()).into());
        }
        let observed = self.store.lock().observe_apply_receipt_v1(
            project_id,
            &expected,
            &request_id.to_string(),
            change,
            motionwright_storage::APPLY_REQUEST_ENCODING_V1,
        )?;
        if let ApplyReceiptObservation::KnownVerifiedV1 { binding } = &observed {
            let branch = branch_id.to_string();
            if binding.base_branch != branch || binding.result_branch != branch {
                return Ok(ApplyReceiptObservation::AuthorityMismatch);
            }
        }
        Ok(observed)
    }
}
