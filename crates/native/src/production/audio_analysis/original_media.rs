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
            saved_hybrid_timing: None,
        })
    }
}

impl ProductionCoordinator {
    /// Reconstruct the exact original final measurement for a saved mux.
    /// The receipt UUID and hash must both match, even for byte-identical jobs.
    pub(in crate::production) fn verify_saved_master_delivery_audio(
        &self,
        project: &Project,
        mux: &ProductionReceipt,
        deliverable_id: Uuid,
        master_sha256: &str,
    ) -> NativeResult<()> {
        require_linux_meter()?;
        let namespace = mux
            .request_id
            .strip_suffix(AudioAnalysisLane::SingleSegment.mux_suffix())
            .or_else(|| {
                mux.request_id
                    .strip_suffix(AudioAnalysisLane::Multisegment.mux_suffix())
            })
            .ok_or_else(|| stale("Saved mux has no original audio measurement namespace"))?;
        if namespace.len() != 54
            || !namespace.starts_with("ma")
            || !namespace[2..]
                .bytes()
                .all(|b| matches!(b, b'a'..=b'z' | b'2'..=b'7'))
        {
            return Err(stale(
                "Saved profile targets require the original measured production",
            ));
        }
        let request_id = format!("{namespace}:measure-decoded");
        let mut selected = None;
        receipt_history::visit_history(&self.service, project.id, true, |receipt| {
            if receipt.request_id == request_id && selected.is_none() {
                selected = Some(receipt.clone());
            }
            Ok(())
        })?;
        let selected =
            selected.ok_or_else(|| stale("Saved final audio measurement is unavailable"))?;
        let proof = self.recover_original_mix_audio_measurements_from_receipt(
            project.id,
            &project.stamp(),
            selected.id,
            deliverable_id,
            master_sha256,
        )?;
        let parent = proof
            .decoded
            .context
            .native_parent
            .as_ref()
            .ok_or_else(|| stale("Saved audio measurement has no original mux"))?;
        if parent.receipt_id != mux.id
            || parent.request_id != mux.request_id
            || proof.delivery_audio_qc().status() != DeliveryAudioQcStatus::WithinTargets
        {
            return Err(stale(
                "Saved final audio is not within this profile's original targets",
            ));
        }
        Ok(())
    }
}
