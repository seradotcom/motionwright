//! Original master recovery for local reading. A durable receipt is only a
//! selector: the existing coordinator reconstructs its complete current proof.
use super::*;
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
            relative_path: parent.master.relative_path.clone(),
            sha256: parent.master.sha256.clone(),
            bytes: parent.master.bytes,
            clock: (parent.fps_num, parent.fps_den, parent.video_frames),
        })
    }
}
