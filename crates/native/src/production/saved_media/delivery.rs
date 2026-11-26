//! Fresh qualification of a saved master for a separately authorized local copy.
//! No production effect, grant, budget, receipt or session capability is restored.
use super::*;

/// Only the Native original-history and original-byte reader can construct this.
/// It is deliberately neither serializable nor deserializable.
pub struct VerifiedSavedMasterDelivery {
    original: VerifiedOriginalMasterReview,
}

impl VerifiedSavedMasterDelivery {
    pub fn original(&self) -> &VerifiedOriginalMasterReview {
        &self.original
    }
}

fn check_profile(project: &Project, deliverable_id: Uuid) -> NativeResult<()> {
    use motionwright_domain::{AudioCodec, OutputColorSpace, OutputContainer, VideoCodec};
    let profile = project
        .deliverables
        .iter()
        .find(|p| p.id == deliverable_id)
        .ok_or_else(|| invalid("Saved delivery profile is unavailable"))?;
    if profile.burn_in_captions {
        return Err(invalid(
            "Saved captioned masters remain review-only until their actual decoded pixels are independently verified on this device",
        ));
    }
    if profile.video_codec != VideoCodec::H264
        || profile.audio_codec != AudioCodec::Aac
        || profile.audio_sample_rate_hz != 48_000
        || profile.color_space != OutputColorSpace::Rec709
        || profile.container != OutputContainer::Mp4
    {
        return Err(invalid(
            "Saved delivery requires the original H.264/AAC Rec.709 MP4 profile",
        ));
    }
    Ok(())
}

impl ProductionCoordinator {
    /// Reopen the original mux, its source video/WAV and decoded WAV. This is
    /// a read-only preflight for a NEW destination permission, not restoration
    /// of an old export token. A mere mux receipt or public QC JSON cannot pass.
    pub fn qualify_saved_master_delivery(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        mux_receipt_id: Uuid,
        deliverable_id: Uuid,
        expected_master_sha256: &str,
    ) -> NativeResult<VerifiedSavedMasterDelivery> {
        let project = current_project(self, project_id, expected)?;
        check_profile(&project, deliverable_id)?;
        let (original, receipt, context) = self.recover_saved_master_parts(
            project_id,
            expected,
            mux_receipt_id,
            deliverable_id,
            expected_master_sha256,
        )?;
        let data = context.original(&receipt)?;
        let root = &self.client.connection().output_root;
        verify_output_artifact(
            root,
            context.args["video_path"]
                .as_str()
                .ok_or_else(|| invalid("Original video is absent"))?,
            context.args["video_sha256"]
                .as_str()
                .ok_or_else(|| invalid("Original video identity is absent"))?,
            MLT_MEZZANINE_MAX_BYTES,
        )?;
        let source_sha = context.args["audio_sha256"]
            .as_str()
            .ok_or_else(|| invalid("Original audio identity is absent"))?;
        let source_path = verify_output_artifact(
            root,
            context.args["audio_path"]
                .as_str()
                .ok_or_else(|| invalid("Original audio is absent"))?,
            source_sha,
            MLT_MASTER_MAX_BYTES,
        )?;
        let decoded = data
            .get("decoded_audio")
            .ok_or_else(|| invalid("Original decoded audio is absent"))?;
        super::super::mlt_multisegment_av::verify_mux_artifact_file(root, decoded)?;
        let decoded_sha = required_string(
            decoded,
            "/sha256",
            "Original decoded audio identity is absent",
        )?;
        let decoded_path = verify_output_artifact(
            root,
            &required_string(decoded, "/path", "Original decoded audio path is absent")?,
            &decoded_sha,
            MLT_MASTER_MAX_BYTES,
        )?;
        let source =
            super::super::mlt_multisegment_av::open_verified_mux_wav(&source_path, source_sha)?;
        let decoded =
            super::super::mlt_multisegment_av::open_verified_mux_wav(&decoded_path, &decoded_sha)?;
        let samples = source.info().frames;
        let (num, den, frames) = original.clock();
        // Match the producer's <= one 48 kHz sample alignment policy exactly.
        if samples == 0
            || (i128::from(samples) * i128::from(num)
                - i128::from(frames) * 48_000 * i128::from(den))
            .abs()
                > i128::from(num)
            || data["decoded_audio_sample_frames"].as_u64() != Some(decoded.info().frames)
        {
            return Err(invalid(
                "Saved delivery audio does not cover the original video clock",
            ));
        }
        super::super::mlt_multisegment_av::require_native_mux_stereo_tail(
            source, decoded, samples,
        )?;
        if project
            .production_design
            .delivery_recipes
            .get(&deliverable_id)
            .is_some_and(|recipe| recipe.audio_targets.is_some())
        {
            self.verify_saved_master_delivery_audio(
                &project,
                &receipt,
                deliverable_id,
                expected_master_sha256,
            )?;
        }
        let latest = current_project(self, project_id, expected)?;
        check_profile(&latest, deliverable_id)?;
        context.check_current(self, &latest)?;
        audio_analysis::receipt_history::verify_selected_atomic_history(&self.service, &receipt)?;
        Ok(VerifiedSavedMasterDelivery { original })
    }
}
