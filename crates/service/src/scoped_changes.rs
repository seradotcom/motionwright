use crate::{StorageError, StorageResult, StudioService};
use motionwright_domain::{Change, CreativePatchPreview, DomainError, Scene};
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyResultObservation, VerifiedApplyReceiptV1,
    apply_request_digest_v1,
};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const MAX_SCOPED_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedChangePreviewRequest {
    project_id: Uuid,
    generation: Uuid,
    branch_id: Uuid,
    revision: u64,
    scene_id: Uuid,
    change: Change,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScopedChangeReceiptRequest {
    project_id: Uuid,
    generation: Uuid,
    branch_id: Uuid,
    scene_id: Uuid,
    revision: u64,
    request_id: Uuid,
    change: Change,
}

#[derive(Debug, Serialize)]
pub struct ScopedChangeReceipt {
    binding: Box<VerifiedApplyReceiptV1>,
    scene: Scene,
}

impl StudioService {
    /// Validate the edit against the exact saved source without applying it.
    pub fn preview_scoped_change(
        &self,
        request: &ScopedChangePreviewRequest,
    ) -> StorageResult<CreativePatchPreview> {
        let source = self.project(request.project_id)?;
        if source.id != request.project_id
            || source.generation != request.generation
            || source.active_branch != request.branch_id
        {
            return Err(StorageError::GenerationConflict);
        }
        if source.revision != request.revision {
            return Err(StorageError::Conflict {
                expected: request.revision,
                actual: source.revision,
            });
        }
        let preview = match &request.change {
            Change::ApplyCreativePatch { patch } if patch.scene_id == request.scene_id => {
                source.preview_creative_patch(patch)?
            }
            Change::UndoCreativePatch { patch_id } => {
                source.preview_creative_patch_undo(*patch_id)?
            }
            _ => return Err(DomainError::Invalid("invalid scoped preview request".into()).into()),
        };
        if preview.scene_id != request.scene_id
            || serde_json::to_vec(&preview)?.len() > MAX_SCOPED_RESPONSE_BYTES
        {
            return Err(DomainError::Invalid(
                "the preview is outside the requested scene or size limit".into(),
            )
            .into());
        }
        Ok(preview)
    }

    /// Read the exact original scoped save or undo. Its historical scene is
    /// needed to preserve the preimages of unrelated draft inputs.
    pub fn observe_scoped_change_receipt(
        &self,
        request: &ScopedChangeReceiptRequest,
    ) -> StorageResult<Option<ScopedChangeReceipt>> {
        let expected_inverse = match &request.change {
            Change::ApplyCreativePatch { patch }
                if patch.scene_id == request.scene_id
                    && patch.base_revision == request.revision =>
            {
                None
            }
            Change::UndoCreativePatch { patch_id } => Some(*patch_id),
            _ => {
                return Err(
                    DomainError::Invalid("invalid scoped save receipt request".into()).into(),
                );
            }
        };
        let Some(result_revision) = request.revision.checked_add(1) else {
            return Err(DomainError::Invalid("invalid scoped save revision".into()).into());
        };
        if request.request_id.is_nil() {
            return Err(DomainError::Invalid("invalid scoped save request identity".into()).into());
        }
        let digest = apply_request_digest_v1(request.project_id, &request.change)?;
        let observation = self.store.lock().lookup_apply_result_v1(
            request.project_id,
            request.generation,
            &request.request_id.to_string(),
            &digest,
            APPLY_REQUEST_ENCODING_V1,
        )?;
        let unsupported = || {
            StorageError::InvalidApplyReceipt("the exact scoped save could not be verified".into())
        };
        let (binding, outcome) = match observation {
            ApplyResultObservation::KnownVerifiedV1 { binding, outcome } => (binding, outcome),
            ApplyResultObservation::NotFound => return Ok(None),
            _ => return Err(unsupported()),
        };
        let branch = request.branch_id.to_string();
        let project = outcome.project;
        if binding.base_branch != branch
            || binding.result_branch != branch
            || outcome.previous_revision != request.revision
            || project.revision != result_revision
            || outcome.replayed
            || !project
                .production_design
                .patches
                .last()
                .is_some_and(|record| {
                    record.scene_id == request.scene_id
                        && record.source_revision == request.revision
                        && record.reverts == expected_inverse
                })
        {
            return Err(unsupported());
        }

        let scene = project
            .scenes
            .into_iter()
            .find(|item| item.id == request.scene_id)
            .ok_or_else(unsupported)?;
        let receipt = ScopedChangeReceipt { binding, scene };
        if serde_json::to_vec(&receipt)?.len() > MAX_SCOPED_RESPONSE_BYTES {
            return Err(StorageError::InvalidApplyReceipt(
                "the saved scene exceeds the recovery response limit".into(),
            ));
        }
        Ok(Some(receipt))
    }
}
