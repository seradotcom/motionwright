//! Original master recovery for local reading. A durable receipt is only a
//! selector: the existing coordinator reconstructs its complete current proof.
use super::*;
use std::path::PathBuf;

/// Not serializable or constructible by callers. This permits the application
/// to prepare an original-file reader, never publication or a new Native effect.
pub struct VerifiedOriginalMasterReview {
    project_id: Uuid,
    stamp: RevisionStamp,
    deliverable_id: Uuid,
    owner_root: PathBuf,
    master: ArtifactIdentity,
    clock: (u32, u32, u64),
}

impl VerifiedOriginalMasterReview {
    pub fn project_id(&self) -> Uuid {
        self.project_id
    }
    pub fn stamp(&self) -> &RevisionStamp {
        &self.stamp
    }
    pub fn deliverable_id(&self) -> Uuid {
        self.deliverable_id
    }
    pub fn artifact(&self) -> (&Path, &str, &str, u64) {
        (
            &self.owner_root,
            &self.master.relative_path,
            &self.master.sha256,
            self.master.bytes,
        )
    }
    pub fn clock(&self) -> (u32, u32, u64) {
        self.clock
    }
}

impl ProductionCoordinator {
    /// Reuses current receipt/source/media reconstruction, without dispatch,
    /// staging, measurement, history changes or restoring an export permission.
    pub fn recover_original_master_review_from_receipt(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        decoded_measurement_receipt_id: Uuid,
        deliverable_id: Uuid,
        expected_master_sha256: &str,
    ) -> NativeResult<VerifiedOriginalMasterReview> {
        let proof = self.recover_original_mix_audio_measurements_from_receipt(
            project_id,
            expected,
            decoded_measurement_receipt_id,
            deliverable_id,
            expected_master_sha256,
        )?;
        let binding = &proof.decoded.context.source;
        let parent = proof
            .decoded
            .context
            .native_parent
            .as_ref()
            .ok_or_else(|| stale("Original master review has no verified native parent"))?;
        Ok(VerifiedOriginalMasterReview {
            project_id: binding.project_id,
            stamp: binding.stamp(),
            deliverable_id: binding.deliverable_id,
            owner_root: self.client.connection().output_root.clone(),
            master: parent.master.clone(),
            clock: (parent.fps_num, parent.fps_den, parent.video_frames),
        })
    }
}
