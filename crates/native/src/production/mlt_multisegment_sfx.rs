//! Original semantic SFX over a source-bound 2–64-clip native MLT cut.
//! Source admission precedes the existing native mux; its measured output
//! remains separate from voice mastering and human creative approval.
use crate::mlt_edit_plan::MltNativeVideoRecipe;
use motionwright_domain::{
    AudioCodec, DeliverableProfile, OutputColorSpace, OutputContainer, Project, VideoCodec,
};
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

// This source-only receipt has no decoded-caption proof or caption review
// state. Refuse burn-in before source reads instead of minting an ambiguous
// SFX master receipt that a delivery consumer could treat as exportable.
fn original_sfx_deliverable(
    project: &Project,
    deliverable_id: Uuid,
) -> NativeResult<&DeliverableProfile> {
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == deliverable_id)
        .ok_or_else(|| stale("Original SFX output profile no longer exists"))?;
    if profile.burn_in_captions {
        return Err(unsupported(
            "Original SFX multisegment mastering does not support burned-in captions",
        ));
    }
    Ok(profile)
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
    let profile = original_sfx_deliverable(project, recipe.deliverable_id)?;
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

/// A typed source-only request, not a browser-provided set of paths.
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
        original_sfx_deliverable(&project, deliverable_id)?;
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

/// A stable, bounded native Broker id. Empty or filesystem/path-like IDs
/// are rejected before CAS staging or any mutating av.mux work can begin.
fn validate_multisegment_sfx_request_id(value: &str) -> NativeResult<()> {
    if value.is_empty()
        || value.len() > 54
        || !value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
    {
        return Err(unsupported(
            "Native source-only SFX mux requires bounded stable request identity",
        ));
    }
    Ok(())
}

/// Source-only SFX request reuses the exact existing lossless FFV1 native
/// video intermediary and pinned Semwright MLT mux. This cannot be
/// deserialized from an LLM or WebView-provided filesystem request.
pub struct MltMultisegmentSfxMasterRequest<'a> {
    pub request_id: &'a str,
    pub deliverable_id: Uuid,
    pub options: &'a crate::film::FilmBuildOptions,
    pub rendered: &'a super::MotionCanvasRenderEvidence,
    pub visual: &'a super::MltVerifiedLosslessTimeline,
    pub audio: &'a MltAudioArtifact,
}

/// Actual pinned-driver H264/AAC output evidence for SFX only, distinct from
/// any measured-voice or music master. Still NOT human creative approval.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltMultisegmentSfxMasterEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub frame_count: u64,
    pub video: super::MltVerifiedLosslessTimeline,
    pub original_sfx: MltMultisegmentOriginalSfxReadiness,
    pub staged_audio: MltAudioArtifact,
    pub master: serde_json::Value,
    pub decoded_audio: serde_json::Value,
    pub evidence_scope: String,
}

impl super::ProductionCoordinator {
    /// Final source-only original SFX AV master across 2+ real MLT clips.
    ///
    /// All source checks happen BEFORE any potentially mutating native mux.
    /// There is one shared mux verifier; this does not create a second
    /// back-end or launder authored SFX as measured voice.
    pub async fn assemble_native_mlt_multisegment_sfx_master(
        &self,
        project_id: Uuid,
        expected: &motionwright_domain::RevisionStamp,
        request: MltMultisegmentSfxMasterRequest<'_>,
    ) -> NativeResult<MltMultisegmentSfxMasterEvidence> {
        use super::mlt_multisegment_av::VerifiedMuxInputs;
        use crate::{
            assembly::preflight_multi_segment_mlt, mlt_edit_plan::mlt_native_video_recipe,
        };
        let MltMultisegmentSfxMasterRequest {
            request_id,
            deliverable_id,
            options,
            rendered,
            visual,
            audio,
        } = request;
        // The existing mux preserves the source mix; it does not realize
        // authored loudness or true-peak targets. Refuse those requests
        // before source staging, output writes or native receipt dispatch.
        // Keep the saved intent intact and the read-only source gate usable.
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
        if project.audio.mix.target_lufs.is_some()
            || project.audio.mix.target_true_peak_dbfs.is_some()
        {
            return Err(unsupported(
                "Original SFX multisegment mastering does not support LUFS or true-peak targets",
            ));
        }
        validate_multisegment_sfx_request_id(request_id)?;
        // All real FFV1 and original WAV CAS reads, SHA256 checks and
        // scene/sample clock admission come from the original read-only gate.
        let original_sfx = self.preflight_native_multisegment_original_sfx_source(
            project_id,
            expected,
            MltMultisegmentSfxSourceRequest {
                deliverable_id,
                options,
                rendered,
                visual,
                audio,
            },
        )?;
        let project = self
            .service
            .project(project_id)
            .map_err(super::storage_error)?;
        let checked = preflight_multi_segment_mlt(
            &project,
            deliverable_id,
            options,
            rendered,
            &self.client.connection().output_root,
        )?;
        let recipe = mlt_native_video_recipe(&checked)?;
        if recipe.input_sha256 != original_sfx.visual_recipe_sha256
            || recipe.total_frames != original_sfx.total_frames
            || original_sfx.exact_mix_pcm_sha256 != audio.sha256
            || original_sfx.revision != project.revision
        {
            return Err(stale(
                "Native SFX MLT edit changed after original source CAS admission",
            ));
        }
        let (master, decoded_audio) = self
            .mux_verified_multisegment_av_source(VerifiedMuxInputs {
                project_id,
                expected,
                request_id,
                project: &project,
                recipe: &recipe,
                visual,
                audio,
            })
            .await?;
        Ok(MltMultisegmentSfxMasterEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_count: recipe.total_frames,
            video: visual.clone(),
            original_sfx,
            staged_audio: audio.clone(),
            master,
            decoded_audio,
            evidence_scope:
                "actual-pinned-MLT-multisegment-original-source-SFX-only-H264-AAC-not-human-approved"
                    .into(),
        })
    }
}

#[cfg(test)]
mod native_sfx_multisegment_request_tests {
    use super::*;
    use crate::film::{
        FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY,
    };
    use crate::production::{
        MltPreparedMezzanines, MltVerifiedLosslessTimeline, MotionCanvasRenderEvidence,
        ProductionConnection, ProductionCoordinator,
    };
    use motionwright_domain::Change;
    use motionwright_service::StudioService;
    use motionwright_storage::Store;
    use semwright_media_time::Rate;
    use std::{fs, path::Path};

    // This executable records unexpected dispatch and fails. It never returns
    // a successful provider envelope or fabricates native source evidence.
    fn no_dispatch_connection(root: &Path, resource: String) -> ProductionConnection {
        let executable = root.join(if cfg!(windows) {
            "semwright-must-not-run.cmd"
        } else {
            "semwright-must-not-run"
        });
        let script: &[u8] = if cfg!(windows) {
            b"@echo off\r\n> \"%~dp0native-dispatch-marker\" echo unexpected\r\nexit /b 97\r\n"
        } else {
            b"#!/bin/sh\n: > \"${0%/*}/native-dispatch-marker\"\nexit 97\n"
        };
        let socket = root.join("broker.sock");
        let session = root.join("session.json");
        let output = root.join("output");
        fs::write(&executable, script).unwrap();
        fs::write(&socket, b"").unwrap();
        fs::write(&session, b"{}").unwrap();
        fs::create_dir(&output).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            for (path, mode) in [
                (&executable, 0o700),
                (&socket, 0o600),
                (&session, 0o600),
                (&output, 0o700),
            ] {
                fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
            }
        }
        ProductionConnection {
            schema: super::super::CONNECTION_SCHEMA.into(),
            executable_sha256: super::super::sha256_file(&executable).unwrap(),
            executable,
            socket,
            session_file: session,
            output_root: output,
            resource,
        }
    }

    enum MasteringTarget {
        Lufs,
        TruePeak,
    }

    async fn assert_mastering_target_rejected_without_effects(target: MasteringTarget) {
        // Keep all controllable test files within this checkout on every OS.
        let temp = tempfile::Builder::new()
            .prefix("sfx-mastering-guard-")
            .tempdir_in(env!("CARGO_MANIFEST_DIR"))
            .unwrap();
        let store = Store::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let service = StudioService::from_store(store);
        let initial = service
            .create_project("SFX mastering target guard")
            .unwrap();
        let mut mix = initial.audio.mix.clone();
        let (target_lufs, target_true_peak_dbfs) = match target {
            MasteringTarget::Lufs => (Some(-14.0), None),
            MasteringTarget::TruePeak => (None, Some(-1.0)),
        };
        mix.target_lufs = target_lufs;
        mix.target_true_peak_dbfs = target_true_peak_dbfs;
        let project = service
            .apply(
                initial.id,
                &initial.stamp(),
                "sfx-mastering-guard-intent",
                &Change::SetMixIntent { mix },
            )
            .unwrap()
            .project;
        let before = serde_json::to_value(&project).unwrap();
        let expected = project.stamp();
        let deliverable_id = project.deliverables[0].id;
        let connection = no_dispatch_connection(temp.path(), project.resource_key());
        let output = connection.output_root.clone();
        let marker = temp.path().join("native-dispatch-marker");
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        assert!(coordinator.receipts(project.id, 256).unwrap().is_empty());
        assert_eq!(fs::read_dir(&output).unwrap().count(), 0);

        // Deliberately absent media proves that the mastering guard precedes
        // source validation and CAS reads. These values are unverified test
        // placeholders, never successful render or import evidence.
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: vec![],
        };
        let rendered = MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_rate: Rate::new(30, 1).unwrap(),
            segments: vec![],
        };
        let visual = MltVerifiedLosslessTimeline {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            recipe_sha256: String::new(),
            frame_count: 0,
            native_frame_count_observed: None,
            transport_pcm_audio: false,
            native_render_profile: String::new(),
            provider_project_cleanup: String::new(),
            native_job_ref: String::new(),
            provider_revision: String::new(),
            artifact_path: "unread-visual.mkv".into(),
            artifact_sha256: String::new(),
            artifact_bytes: 0,
            source: MltPreparedMezzanines {
                project_resource: project.resource_key(),
                generation: project.generation,
                revision: project.revision,
                deliverable_id,
                input_sha256: String::new(),
                fps_num: 30,
                fps_den: 1,
                total_frames: 0,
                verified_video_segments: vec![],
                evidence_scope: "unverified-negative-fixture".into(),
            },
            evidence_scope: "unverified-negative-fixture".into(),
        };
        let audio = MltAudioArtifact {
            relative_path: "unread-original-sfx.wav".into(),
            sha256: String::new(),
            sample_rate: 48_000,
            channels: 2,
        };
        let error = coordinator
            .assemble_native_mlt_multisegment_sfx_master(
                project.id,
                &expected,
                MltMultisegmentSfxMasterRequest {
                    request_id: "sfx-mastering-guard",
                    deliverable_id,
                    options: &options,
                    rendered: &rendered,
                    visual: &visual,
                    audio: &audio,
                },
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert_eq!(
            error.message,
            "Original SFX multisegment mastering does not support LUFS or true-peak targets"
        );
        assert!(error.outcome_known);
        assert!(coordinator.receipts(project.id, 256).unwrap().is_empty());
        assert_eq!(fs::read_dir(&output).unwrap().count(), 0);
        assert_eq!(
            fs::symlink_metadata(&marker).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
        assert_eq!(
            serde_json::to_value(service.project(project.id).unwrap()).unwrap(),
            before,
            "rejection must preserve the entire saved Project and its mastering intent"
        );
    }

    #[tokio::test]
    async fn native_sfx_master_rejects_lufs_before_any_effect() {
        assert_mastering_target_rejected_without_effects(MasteringTarget::Lufs).await;
    }

    #[tokio::test]
    async fn native_sfx_master_rejects_true_peak_before_any_effect() {
        assert_mastering_target_rejected_without_effects(MasteringTarget::TruePeak).await;
    }

    #[test]
    fn source_only_native_mux_rejects_untrusted_output_and_job_identity() {
        assert!(validate_multisegment_sfx_request_id("mw-sfx-native-r7-4f1a8").is_ok());
        for invalid in [
            "",
            "../owner-file",
            "file:C:/owner.mp4",
            "owner/request id",
            "evil\\job",
            "bad:job",
            "⏰native",
        ] {
            assert!(
                validate_multisegment_sfx_request_id(invalid).is_err(),
                "must reject untrusted native mutation identity: {invalid}"
            );
        }
        assert!(validate_multisegment_sfx_request_id(&"x".repeat(55)).is_err());
        assert!(validate_multisegment_sfx_request_id(&"x".repeat(54)).is_ok());
    }

    #[test]
    fn original_sfx_profile_refuses_burn_in_and_preserves_sidecar_selection() {
        let mut project = Project::new("Original SFX caption profile").unwrap();
        project.deliverables[0].captions = true;
        project.deliverables[0].burn_in_captions = false;
        let sidecar_id = project.deliverables[0].id;
        let mut burn_in = project.deliverables[0].clone();
        burn_in.id = Uuid::new_v4();
        burn_in.burn_in_captions = true;
        let burn_in_id = burn_in.id;
        project.deliverables.push(burn_in);

        let admitted = original_sfx_deliverable(&project, sidecar_id).unwrap();
        assert_eq!(admitted.id, sidecar_id);
        assert!(admitted.captions);
        assert!(!admitted.burn_in_captions);
        let refused = original_sfx_deliverable(&project, burn_in_id).unwrap_err();
        assert_eq!(refused.code, ErrorCode::Unsupported);
        assert_eq!(
            refused.message,
            "Original SFX multisegment mastering does not support burned-in captions"
        );
    }

    #[test]
    fn original_sfx_profile_does_not_substitute_another_saved_deliverable() {
        let project = Project::new("Original SFX selected profile").unwrap();
        let refused = original_sfx_deliverable(&project, Uuid::new_v4()).unwrap_err();
        assert_eq!(refused.code, ErrorCode::StaleReference);
    }

    #[test]
    fn original_sfx_scene_clock_refuses_fractional_negative_and_overflow_frames() {
        use motionwright_domain::RationalTime;
        assert_eq!(
            exact_30fps_frame(RationalTime { num: 1, den: 30 }).unwrap(),
            1
        );
        for invalid in [
            RationalTime { num: 1, den: 60 },
            RationalTime { num: -1, den: 30 },
            RationalTime { num: 1, den: 0 },
            RationalTime {
                num: i64::MAX,
                den: 1,
            },
        ] {
            assert_eq!(
                exact_30fps_frame(invalid).unwrap_err().code,
                ErrorCode::Unsupported
            );
        }
    }
}
