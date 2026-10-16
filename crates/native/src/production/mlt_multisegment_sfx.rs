//! Pre-dispatch source/clock admission for original semantic SFX over a
//! genuine 2–64-clip native MLT visual cut. No alternate renderer, mux, fake
//! voice-track admission, output artifact or human creative PASS is implied.
use crate::mlt_edit_plan::MltNativeVideoRecipe;
use motionwright_domain::{AudioCodec, OutputColorSpace, OutputContainer, Project, VideoCodec};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use uuid::Uuid;

use super::{MltAudioArtifact, SemanticSfxAvSourceProof};

fn stale(message: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, message)
}
fn unsupported(message: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

/// An owner-CAS source admission plus EXACT unchanged authored Film/MLT clocks.
/// This receipt is deliberately NOT evidence that a native mux ran.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltMultisegmentOriginalSfxReadiness {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub total_frames: u64,
    pub total_samples: u64,
    pub ordered_scene_ids: Vec<Uuid>,
    pub original_cue_ids: Vec<Uuid>,
    pub original_asset_sha256: Vec<String>,
    pub exact_mix_pcm_sha256: String,
    pub visual_recipe_sha256: String,
    pub evidence_scope: String,
}

fn exact_30fps_frame(time: motionwright_domain::RationalTime) -> NativeResult<u64> {
    let n = i128::from(time.num)
        .checked_mul(30)
        .ok_or_else(|| unsupported("Original SFX scene frame numerator overflow"))?;
    let d = i128::from(time.den);
    if d <= 0 || n < 0 || n % d != 0 {
        return Err(unsupported(
            "Original SFX scene time is not a nonnegative exact 30-fps frame",
        ));
    }
    u64::try_from(n / d).map_err(|_| unsupported("Original SFX frame address exceeds native bound"))
}

/// Accept only a lossless native MLT cut that preserves EVERY original scene,
/// frame and authorial SFX cue address in its existing global 30-fps timeline.
/// No edit reordering, trim, omitted scene, arbitrary music, voice or stretch
/// may silently appear in an SFX-only native master.
///
/// The caller MUST first call verify_semantic_sfx_av_source() with StudioService
/// and the actual owner output root. This function checks the typed receipt
/// against the native segment recipe; it never reads untrusted browser paths.
pub fn preflight_multisegment_original_sfx_clock(
    project: &Project,
    recipe: &MltNativeVideoRecipe,
    audio: &MltAudioArtifact,
    proof: &SemanticSfxAvSourceProof,
) -> NativeResult<MltMultisegmentOriginalSfxReadiness> {
    project
        .validate()
        .map_err(|_| unsupported("Original SFX project failed domain validation"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == recipe.deliverable_id)
        .ok_or_else(|| stale("Original SFX output profile no longer exists"))?;
    if recipe.project_resource != project.resource_key()
        || recipe.generation != project.generation
        || recipe.revision != project.revision
        || recipe.scope != "owner-source-bound-mlt-semantic-edit-recipe-not-rendered"
        || recipe.provider_profile.fps_num != 30
        || recipe.provider_profile.fps_den != 1
        || recipe.clips.len() < 2
        || recipe.clips.len() > 64
        || recipe.total_frames == 0
        // Preserve the SAME 6 MiB WAV bound enforced by the original
        // source-CAS preparer: 44-byte header plus 1600 stereo PCM16
        // samples (6400 bytes) per exact 30-fps output frame.
        || recipe.total_frames >
            (super::semantic_sfx_av::MAX_SFX_AUDIO_MASTER_BYTES - 44) / 6400
        || recipe.input_sha256.len() != 64
        || !recipe
            .input_sha256
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
        || !profile.included_scene_ids.is_empty()
        || profile.voice_track_id.is_some()
        || profile.frame_rate.num != 30
        || profile.frame_rate.den != 1
        || profile.audio_sample_rate_hz != 48_000
        || profile.video_codec != VideoCodec::H264
        || profile.audio_codec != AudioCodec::Aac
        || profile.container != OutputContainer::Mp4
        || profile.color_space != OutputColorSpace::Rec709
        || project.audio.active_voice_track_id.is_some()
        || !project.audio.cues.is_empty()
        || project.scenes.len() < 2
        || project.scenes.len() > 64
    {
        return Err(unsupported(
            "Original SFX multisegment cut requires full original 30fps H264/AAC scene order without voice or additional audio",
        ));
    }
    if proof.project_id != project.id
        || proof.generation != project.generation
        || proof.revision != project.revision
        || proof.deliverable_id != recipe.deliverable_id
        || proof.master_frames != recipe.total_frames
        || proof.exact_pcm_sha256 != audio.sha256
        || proof.sample_peak_pcm16 > 32767
        || audio.sample_rate != 48_000
        || audio.channels != 2
        || audio.sha256.len() != 64
        || !audio
            .sha256
            .bytes()
            .all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase())
    {
        return Err(stale(
            "The original SFX WAV CAS proof differs from the native edit recipe",
        ));
    }
    // The existing original-source mixer sorts by asset ID. Require the
    // complete same sequence of SHA-bound original WAVs, not only its count.
    let mut original_assets = BTreeMap::new();
    for cue in &project.audio.sfx_cues {
        match original_assets.insert(cue.source_asset_id, cue.source_sha256.as_str()) {
            Some(previous) if previous != cue.source_sha256 => {
                return Err(stale("Original SFX asset has conflicting source digests"));
            }
            _ => {}
        }
    }
    let ordered_source_hashes: Vec<String> =
        original_assets.into_values().map(str::to_owned).collect();
    if ordered_source_hashes.is_empty() || ordered_source_hashes != proof.original_asset_sha256 {
        return Err(stale(
            "Native multisegment source omits an original imported SFX WAV",
        ));
    }
    let schedule = project
        .validated_sfx_schedule()
        .map_err(|_| stale("Original SFX scene clock changed since the visual source"))?;
    if schedule.is_empty()
        || schedule.iter().any(|cue| {
            cue.absolute_end_frame > recipe.total_frames
                || cue.first_sample != cue.absolute_start_frame * 1600
                || cue.sample_count == 0
        })
    {
        return Err(stale(
            "Source-bound SFX cues do not match the exact native MLT frame clock",
        ));
    }
    // MLT's source-bound recipe can group scenes into native segments, but it
    // must not reorder, omit, trim, stretch or insert ANY original scene.
    let mut cursor = 0_u64;
    let mut scene_index = 0_usize;
    let mut ordered_scene_ids = Vec::with_capacity(project.scenes.len());
    for clip in &recipe.clips {
        if clip.start_frame != cursor
            || clip.source_in != 0
            || clip.source_out_exclusive == 0
            || clip.scene_ids.is_empty()
        {
            return Err(stale(
                "Original SFX MLT cut has a shifted/trimmed output clip",
            ));
        }
        let start = cursor;
        for id in &clip.scene_ids {
            let scene = project
                .scenes
                .get(scene_index)
                .ok_or_else(|| stale("MLT includes scenes not in this original source project"))?;
            if scene.id != *id || exact_30fps_frame(scene.start)? != cursor {
                return Err(stale(
                    "Original SFX visual cut reorders or shifts scene-owned cue addresses",
                ));
            }
            let frames = exact_30fps_frame(scene.duration)?;
            if frames == 0 {
                return Err(unsupported(
                    "Original semantic SFX cannot align a zero-frame source scene",
                ));
            }
            cursor = cursor
                .checked_add(frames)
                .ok_or_else(|| stale("Source-authored multisegment frame overflow"))?;
            ordered_scene_ids.push(*id);
            scene_index += 1;
        }
        if cursor - start != clip.source_out_exclusive {
            return Err(stale(
                "Original SFX MLT clip trims or stretches authored scene frames",
            ));
        }
    }
    if scene_index != project.scenes.len() || cursor != recipe.total_frames {
        return Err(stale(
            "Original multisegment SFX omitted or inserted authored frames",
        ));
    }
    Ok(MltMultisegmentOriginalSfxReadiness {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        deliverable_id: recipe.deliverable_id,
        total_frames: cursor,
        total_samples: cursor * 1600,
        ordered_scene_ids,
        original_cue_ids: schedule.into_iter().map(|c| c.cue_id).collect(),
        original_asset_sha256: ordered_source_hashes,
        exact_mix_pcm_sha256: audio.sha256.clone(),
        visual_recipe_sha256: recipe.input_sha256.clone(),
        evidence_scope:
            "original-owner-SFX-CAS-and-unaltered-multisegment-Film-clock-preflight-NOT-native-mux"
                .into(),
    })
}

/// A typed owner-only source gate request. The caller passes actual verified
/// native in-process source evidence; never deserializes browser file paths.
pub struct MltMultisegmentSfxSourceRequest<'a> {
    pub deliverable_id: Uuid,
    pub options: &'a crate::film::FilmBuildOptions,
    pub rendered: &'a super::MotionCanvasRenderEvidence,
    pub visual: &'a super::MltVerifiedLosslessTimeline,
    pub audio: &'a MltAudioArtifact,
}

impl super::ProductionCoordinator {
    /// Source-only, read-only native admissibility boundary for a separately
    /// verified 2+ segment FFV1 cut. This DOES NOT run av.mux, claim a
    /// finished MP4 or change owner project/source state.
    ///
    /// The caller must use this exact owner CAS and native visual receipt
    /// before future audio+video completion. A false result MUST NOT be
    /// "recovered" by treating an SFX PCM source as a measured voice.
    pub fn preflight_native_multisegment_original_sfx_source(
        &self,
        project_id: Uuid,
        expected: &motionwright_domain::RevisionStamp,
        request: MltMultisegmentSfxSourceRequest<'_>,
    ) -> NativeResult<MltMultisegmentOriginalSfxReadiness> {
        let MltMultisegmentSfxSourceRequest {
            deliverable_id,
            options,
            rendered,
            visual,
            audio,
        } = request;
        use crate::{
            assembly::preflight_multi_segment_mlt, mlt_edit_plan::mlt_native_video_recipe,
        };
        let project = self
            .service
            .project(project_id)
            .map_err(super::storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
            || self.client.connection().resource != project.resource_key()
        {
            return Err(stale(
                "Native source-only SFX MLT owner authority or revision is stale",
            ));
        }
        let root = &self.client.connection().output_root;
        let checked =
            preflight_multi_segment_mlt(&project, deliverable_id, options, rendered, root)?;
        let recipe = mlt_native_video_recipe(&checked)?;
        let expected_video_path = format!(
            "{}-sequence-video-only-ffv1.mkv",
            recipe.owner_output_namespace
        );
        if visual.project_resource != project.resource_key()
            || visual.generation != project.generation
            || visual.revision != project.revision
            || visual.deliverable_id != deliverable_id
            || visual.recipe_sha256 != recipe.input_sha256
            || visual.frame_count != recipe.total_frames
            || visual.native_frame_count_observed != Some(recipe.total_frames)
            || visual.native_render_profile != "lossless-video-only"
            || visual.transport_pcm_audio
            || visual.evidence_scope
                != "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
            || visual.artifact_path != expected_video_path
            || !visual.native_job_ref.starts_with("render:")
            || visual.provider_project_cleanup != "not_requested_requires_foreground_broker_consent"
        {
            return Err(stale(
                "Original SFX MLT source has no matching original verified FFV1 visual cut",
            ));
        }
        let file = super::verify_output_artifact(
            root,
            &visual.artifact_path,
            &visual.artifact_sha256,
            super::MLT_MASTER_MAX_BYTES,
        )?;
        if std::fs::metadata(file)
            .map_err(|_| stale("The owner FFV1 multisegment visual source disappeared"))?
            .len()
            != visual.artifact_bytes
        {
            return Err(stale(
                "Original multisegment visual source bytes differ from its native receipt",
            ));
        }
        let proof = super::verify_semantic_sfx_av_source(
            &self.service,
            project_id,
            expected,
            deliverable_id,
            recipe.total_frames,
            audio,
            root,
        )?;
        preflight_multisegment_original_sfx_clock(&project, &recipe, audio, &proof)
    }
}
