//! Read-only, owner-scoped final A/V source readiness for a native
//! multi-segment MLT video. Does not claim a video timeline or mux exists.
use crate::{
    mlt_edit_plan::MltNativeVideoRecipe,
    production::{MltPreparedMezzanines, verified_native_media_path},
};
use motionwright_domain::{AudioCodec, OutputColorSpace, OutputContainer, Project, VideoCodec};
use motionwright_service::VerifiedMasterVoice;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs::{self, File},
    io::Read,
    path::Path,
};
use uuid::Uuid;

const MAX_FFV1_SEGMENTS: usize = 64;
const MAX_FFV1_BYTES: u64 = 512 * 1024 * 1024;
const MAX_FFV1_TOTAL_BYTES: u64 = 1024 * 1024 * 1024;
const MAX_WAV_BYTES: u64 = 256 * 1024 * 1024;
const AUDIO_RATE: u64 = 48_000;

fn invalid(reason: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, reason)
}
fn stale(reason: impl Into<String>) -> Error {
    Error::new(ErrorCode::StaleReference, reason)
}
fn unsupported(reason: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, reason)
}
fn digest_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The exact positive-rational equality test uses cross multiplication;
/// no float FPS, no rounded milliseconds, and no hidden resampling/padding.
fn measured_voice_matches_cut(
    time_num: i64,
    time_den: i64,
    frame_count: u64,
    fps_num: u32,
    fps_den: u32,
) -> NativeResult<()> {
    if time_num <= 0 || time_den <= 0 || frame_count == 0 || fps_num == 0 || fps_den == 0 {
        return Err(invalid("Native AV source has invalid rational timing"));
    }
    let voice = i128::from(time_num)
        .checked_mul(i128::from(fps_num))
        .ok_or_else(|| invalid("Voice time numerator overflow"))?;
    let video = i128::from(frame_count)
        .checked_mul(i128::from(time_den))
        .and_then(|value| value.checked_mul(i128::from(fps_den)))
        .ok_or_else(|| invalid("Video time numerator overflow"))?;
    let tolerance = i128::from(time_den)
        .checked_mul(i128::from(fps_num))
        .ok_or_else(|| invalid("Audio alignment tolerance overflow"))?;
    let sample_delta = voice
        .abs_diff(video)
        .checked_mul(u128::from(AUDIO_RATE))
        .ok_or_else(|| invalid("Audio alignment sample count overflow"))?;
    if sample_delta > tolerance as u128 {
        return Err(unsupported(
            "Measured voice and verified multi-segment video cut differ by more than one 48 kHz sample; no silent time-stretch, trim or padding",
        ));
    }
    Ok(())
}

fn verify_measured_voice_file(voice: &VerifiedMasterVoice) -> NativeResult<()> {
    if !digest_valid(&voice.sha256) || !(44..=MAX_WAV_BYTES).contains(&voice.size_bytes) {
        return Err(invalid(
            "Measured voice has invalid SHA-256 or bounded size",
        ));
    }
    // VerifiedMasterVoice was resolved through StudioService's project-bound
    // CAS; do not take this filesystem path from WebView or model arguments.
    let metadata = fs::symlink_metadata(&voice.path)
        .map_err(|_| stale("Imported measured WAV is unavailable"))?;
    if metadata.file_type().is_symlink()
        || !metadata.is_file()
        || metadata.len() != voice.size_bytes
    {
        return Err(stale(
            "Imported measured WAV changed or is not a regular file",
        ));
    }
    let mut input =
        File::open(&voice.path).map_err(|_| stale("Imported measured WAV cannot be opened"))?;
    let mut signature = [0_u8; 12];
    input
        .read_exact(&mut signature)
        .map_err(|_| stale("Imported WAV lacks the RIFF header"))?;
    if &signature[0..4] != b"RIFF" || &signature[8..12] != b"WAVE" {
        return Err(unsupported(
            "Native AV only accepts an imported RIFF/WAVE source",
        ));
    }
    // Rehash the same source from the beginning, not only the header.
    let mut hash = Sha256::new();
    hash.update(signature);
    let mut consumed = signature.len() as u64;
    let mut block = [0_u8; 128 * 1024];
    loop {
        let n = input
            .read(&mut block)
            .map_err(|_| stale("Measured WAV changed while reading"))?;
        if n == 0 {
            break;
        }
        consumed = consumed
            .checked_add(n as u64)
            .ok_or_else(|| invalid("Measured WAV size overflow"))?;
        if consumed > voice.size_bytes || consumed > MAX_WAV_BYTES {
            return Err(stale("Measured WAV grew during verification"));
        }
        hash.update(&block[..n]);
    }
    if consumed != voice.size_bytes || hex::encode(hash.finalize()) != voice.sha256 {
        return Err(stale("Measured WAV no longer matches its imported SHA-256"));
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MultiSegmentAudioReadiness {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub voice_track_id: Uuid,
    pub audio_sha256: String,
    pub audio_size_bytes: u64,
    pub audio_sample_rate: u32,
    pub audio_channels: u16,
    pub video_input_sha256: String,
    pub video_segment_count: usize,
    pub total_frames: u64,
    pub fps_num: u32,
    pub fps_den: u32,
    pub final_profile: String,
    /// Deterministic source digest for a later, separately authorized mux.
    pub source_fingerprint: String,
    pub evidence_scope: String,
}

/// Exact audio + already-produced FFV1 input compatibility. A future owner
/// Coordinator must call this with its own private StudioService CAS source
/// and owner output root, then independently authorize and verify av.mux.
/// A successful readiness plan is not an assembled H.264/AAC master.
pub fn preflight_multi_segment_audio(
    project: &Project,
    deliverable_id: Uuid,
    recipe: &MltNativeVideoRecipe,
    prepared: &MltPreparedMezzanines,
    voice: &VerifiedMasterVoice,
    owner_output_root: &Path,
) -> NativeResult<MultiSegmentAudioReadiness> {
    project.validate().map_err(|error| {
        invalid(format!(
            "Project contains invalid versioned semantics: {error}",
        ))
    })?;
    let profile = project
        .deliverables
        .iter()
        .find(|item| item.id == deliverable_id)
        .ok_or_else(|| invalid("Multi-segment AV profile is not saved"))?;
    let track_id = profile.voice_track_id.ok_or_else(|| {
        unsupported("Bind one measured voice WAV to the selected delivery profile")
    })?;
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|item| item.id == track_id)
        .ok_or_else(|| invalid("Selected measured voice track is missing"))?;
    let asset = project
        .assets
        .iter()
        .find(|item| item.id == track.asset_id)
        .ok_or_else(|| invalid("Measured voice asset is absent from this project"))?;
    if !matches!(
        asset.media_type.as_str(),
        "audio/wav" | "audio/wave" | "audio/x-wav"
    ) || asset.content_sha256.as_deref() != Some(track.source_sha256.as_str())
        || !digest_valid(&track.source_sha256)
        || track.source_sha256 != voice.sha256
        || track.sample_rate_hz != 48_000
        || track.channels != 2
        || profile.audio_sample_rate_hz != 48_000
        || profile.audio_codec != AudioCodec::Aac
        || profile.video_codec != VideoCodec::H264
        || profile.container != OutputContainer::Mp4
        || profile.color_space != OutputColorSpace::Rec709
        || profile.burn_in_captions
    {
        return Err(unsupported(
            "Multi-segment AV requires exactly bound measured stereo 48 kHz WAV and H.264/AAC Rec.709 MP4 without unimplemented caption burn-in",
        ));
    }
    if project.audio.mix.voice_gain_db != 0.0
        || project.audio.mix.target_lufs.is_some()
        || project.audio.mix.target_true_peak_dbfs.is_some()
    {
        return Err(unsupported(
            "Multi-segment AV does not implement gain changes or claimed LUFS/true-peak mastering; clear them or author an independently verified mix",
        ));
    }
    for (resource, generation, revision, profile_id) in [
        (
            &recipe.project_resource,
            recipe.generation,
            recipe.revision,
            recipe.deliverable_id,
        ),
        (
            &prepared.project_resource,
            prepared.generation,
            prepared.revision,
            prepared.deliverable_id,
        ),
    ] {
        if *resource != project.resource_key()
            || generation != project.generation
            || revision != project.revision
            || profile_id != deliverable_id
        {
            return Err(stale(
                "MLT source data belongs to a different project generation, revision or output profile",
            ));
        }
    }
    if recipe.scope != "owner-source-bound-mlt-semantic-edit-recipe-not-rendered"
        || prepared.evidence_scope != "real-source-ffv1-segments-not-composited"
        || !digest_valid(&recipe.input_sha256)
        || recipe.input_sha256 != prepared.input_sha256
        || recipe.final_mux_profile != "h264-aac-mp4"
        || recipe.lossless_sequence_profile != "lossless"
        || recipe.total_frames != prepared.total_frames
        || recipe.provider_profile.fps_num != prepared.fps_num
        || recipe.provider_profile.fps_den != prepared.fps_den
        || recipe.provider_profile.fps_num == 0
        || recipe.provider_profile.fps_den == 0
        || i64::from(recipe.provider_profile.fps_num) != profile.frame_rate.num
        || i64::from(recipe.provider_profile.fps_den) != profile.frame_rate.den
        || recipe.provider_profile.width != profile.width
        || recipe.provider_profile.height != profile.height
        || recipe.provider_profile.colorspace != 709
        || recipe.provider_profile.audio_channels != 2
        || recipe.clips.len() != prepared.verified_video_segments.len()
        || !(2..=MAX_FFV1_SEGMENTS).contains(&recipe.clips.len())
        || recipe.total_frames == 0
        || recipe.total_frames > 36_000
    {
        return Err(stale(
            "MLT FFV1 preparation does not match the canonical future AV profile and source fingerprint",
        ));
    }
    measured_voice_matches_cut(
        track.measured_duration.num,
        track.measured_duration.den,
        recipe.total_frames,
        recipe.provider_profile.fps_num,
        recipe.provider_profile.fps_den,
    )?;
    // Source verification performs file SHA-256 recheck. An outcome-unknown
    // native operation cannot be promoted by a forged or stale metadata row.
    let mut total_bytes = 0_u64;
    let mut next_frame = 0_u64;
    let mut seen_segments = HashSet::new();
    let mut seen_paths = HashSet::new();
    for (clip, media) in recipe.clips.iter().zip(&prepared.verified_video_segments) {
        if clip.segment_id != media.segment_id
            || clip.scene_ids != media.scene_ids
            || clip.source_native_job_ref != media.native_render_job_ref
            || clip.start_frame != next_frame
            || media.output_start_frame != next_frame
            || clip.source_in != 0
            || clip.source_out_exclusive != media.frame_count
            || clip.source_manifest_sha256 != media.source_manifest_sha256
            || clip.ffv1_output_path != media.artifact_path
            || !digest_valid(&media.artifact_sha256)
            || !(1..=MAX_FFV1_BYTES).contains(&media.artifact_bytes)
            || !seen_segments.insert(&clip.segment_id)
            || !seen_paths.insert(&clip.ffv1_output_path)
        {
            return Err(stale(
                "Native FFV1 segment identity, frame range, job, manifest or digest differs from the canonical source recipe",
            ));
        }
        next_frame = next_frame
            .checked_add(media.frame_count)
            .ok_or_else(|| invalid("Native FFV1 frame range overflow"))?;
        total_bytes = total_bytes
            .checked_add(media.artifact_bytes)
            .ok_or_else(|| invalid("Native FFV1 aggregate byte count overflow"))?;
        if total_bytes > MAX_FFV1_TOTAL_BYTES || next_frame > recipe.total_frames {
            return Err(unsupported(
                "Prepared multisegment FFV1 sources exceed bounded media budgets",
            ));
        }
        let path = verified_native_media_path(
            owner_output_root,
            &clip.ffv1_output_path,
            &media.artifact_sha256,
            MAX_FFV1_BYTES,
        )?;
        if fs::metadata(path)
            .map_err(|_| stale("Native FFV1 file became unavailable"))?
            .len()
            != media.artifact_bytes
        {
            return Err(stale(
                "Native FFV1 file size differs from the trusted production receipt",
            ));
        }
    }
    if next_frame != recipe.total_frames {
        return Err(stale(
            "MLT FFV1 segments have an incomplete or overlapping exact cut",
        ));
    }
    verify_measured_voice_file(voice)?;

    // Stable, nonpath identity. No real media, absolute CAS paths or raw
    // source payloads are serialized to the result or available to WebView.
    let input = serde_json::to_vec(&(
        project.resource_key(),
        project.generation,
        project.revision,
        deliverable_id,
        track.id,
        &track.source_sha256,
        &recipe.input_sha256,
        recipe.total_frames,
        recipe.provider_profile.fps_num,
        recipe.provider_profile.fps_den,
        &prepared.verified_video_segments,
    ))
    .map_err(|_| invalid("Multi-segment AV readiness source could not be serialized"))?;
    let source_fingerprint = hex::encode(Sha256::digest(input));
    Ok(MultiSegmentAudioReadiness {
        project_resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
        deliverable_id,
        voice_track_id: track.id,
        audio_sha256: track.source_sha256.clone(),
        audio_size_bytes: voice.size_bytes,
        audio_sample_rate: 48_000,
        audio_channels: 2,
        video_input_sha256: recipe.input_sha256.clone(),
        video_segment_count: recipe.clips.len(),
        total_frames: recipe.total_frames,
        fps_num: recipe.provider_profile.fps_num,
        fps_den: recipe.provider_profile.fps_den,
        final_profile: recipe.final_mux_profile.clone(),
        source_fingerprint,
        evidence_scope: "verified-owner-audio-ffv1-inputs-not-muxed-mp4".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        assembly::{MultiSegmentAssemblyPreflight, SegmentForMlt},
        mlt_edit_plan::mlt_native_video_recipe,
        production::MltVerifiedMezzanine,
    };
    use motionwright_domain::{Asset, Change, RationalTime, VoiceTrack};
    use tempfile::TempDir;

    struct Fixture {
        project: Project,
        recipe: MltNativeVideoRecipe,
        prepared: MltPreparedMezzanines,
        voice: VerifiedMasterVoice,
        video_root: TempDir,
        _audio_root: TempDir,
    }

    fn synthetic_wav(frames: usize) -> Vec<u8> {
        let size = frames * 4; // 16-bit stereo PCM
        let mut bytes = Vec::with_capacity(44 + size);
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(&(36 + size as u32).to_le_bytes());
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&16_u32.to_le_bytes());
        bytes.extend_from_slice(&1_u16.to_le_bytes());
        bytes.extend_from_slice(&2_u16.to_le_bytes());
        bytes.extend_from_slice(&48_000_u32.to_le_bytes());
        bytes.extend_from_slice(&(48_000 * 4_u32).to_le_bytes());
        bytes.extend_from_slice(&4_u16.to_le_bytes());
        bytes.extend_from_slice(&16_u16.to_le_bytes());
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&(size as u32).to_le_bytes());
        bytes.resize(44 + size, 0);
        bytes
    }

    fn fixture() -> Fixture {
        let mut project = Project::new("Owner AV mix preflight").unwrap();
        for index in 0..2 {
            project
                .apply_change(&Change::AddScene {
                    name: format!("Actual native scene {index}"),
                    objective: "Preserve source cut and voice timing".into(),
                    duration_seconds: 2,
                })
                .unwrap();
        }
        let profile_id = project.deliverables[0].id;
        let audio_root = tempfile::tempdir().unwrap();
        let voice_path = audio_root.path().join("owner-import.wav");
        let data = synthetic_wav(48_000 * 4);
        let audio_digest = hex::encode(Sha256::digest(&data));
        fs::write(&voice_path, &data).unwrap();
        let voice = VerifiedMasterVoice {
            path: voice_path,
            sha256: audio_digest.clone(),
            size_bytes: data.len() as u64,
        };
        let track_id = Uuid::new_v4();
        let asset_id = Uuid::new_v4();
        project.assets.push(Asset {
            id: asset_id,
            name: "Measured, SHA-256-bound voice.wav".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(audio_digest.clone()),
            source_revision: None,
        });
        project.audio.voice_tracks.push(VoiceTrack {
            id: track_id,
            asset_id,
            label: "Verified voice, four seconds".into(),
            sample_rate_hz: 48_000,
            channels: 2,
            measured_duration: RationalTime::new(4, 1).unwrap(),
            source_sha256: audio_digest,
            loudness_lufs: None,
            true_peak_dbfs: None,
        });
        project.deliverables[0].voice_track_id = Some(track_id);
        project.validate().unwrap();
        let checked = MultiSegmentAssemblyPreflight {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id: profile_id,
            mlt_profile: "h264-1080p".into(),
            fps_num: 30,
            fps_den: 1,
            total_frames: 120,
            segments: [(0, 60, "1".repeat(32)), (60, 60, "2".repeat(32))]
                .into_iter()
                .enumerate()
                .map(|(i, (start, count, suffix))| SegmentForMlt {
                    segment_id: format!("canonical-segment-{i}"),
                    scene_ids: vec![project.scenes[i].id],
                    output_start_frame: start,
                    frame_count: count,
                    manifest_path: format!("render-{suffix}/artifact-manifest.json"),
                    manifest_sha256: "a".repeat(64),
                    native_job_ref: format!("motion-job-{i}"),
                })
                .collect(),
            evidence_scope: "manifest-digest-verified-not-composited-mp4".into(),
        };
        let recipe = mlt_native_video_recipe(&checked).unwrap();
        let root = tempfile::tempdir().unwrap();
        let media = recipe
            .clips
            .iter()
            .enumerate()
            .map(|(index, clip)| {
                // Synthetic file data: only owner-root/size/digest checks are
                // under test. This is not FFV1 decoder evidence or a real mux.
                let bytes = format!("synthetic-ffv1-segment-{index}").into_bytes();
                fs::write(root.path().join(&clip.ffv1_output_path), &bytes).unwrap();
                MltVerifiedMezzanine {
                    segment_id: clip.segment_id.clone(),
                    scene_ids: clip.scene_ids.clone(),
                    output_start_frame: clip.start_frame,
                    frame_count: clip.source_out_exclusive,
                    native_render_job_ref: clip.source_native_job_ref.clone(),
                    source_manifest_sha256: clip.source_manifest_sha256.clone(),
                    artifact_path: clip.ffv1_output_path.clone(),
                    artifact_sha256: hex::encode(Sha256::digest(&bytes)),
                    artifact_bytes: bytes.len() as u64,
                }
            })
            .collect::<Vec<_>>();
        let prepared = MltPreparedMezzanines {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id: profile_id,
            input_sha256: recipe.input_sha256.clone(),
            fps_num: 30,
            fps_den: 1,
            total_frames: 120,
            verified_video_segments: media,
            evidence_scope: "real-source-ffv1-segments-not-composited".into(),
        };
        Fixture {
            project,
            recipe,
            prepared,
            voice,
            video_root: root,
            _audio_root: audio_root,
        }
    }

    fn check(f: &Fixture) -> NativeResult<MultiSegmentAudioReadiness> {
        preflight_multi_segment_audio(
            &f.project,
            f.project.deliverables[0].id,
            &f.recipe,
            &f.prepared,
            &f.voice,
            f.video_root.path(),
        )
    }

    #[test]
    fn source_bound_multisegment_voice_and_video_have_one_deterministic_readiness_fingerprint() {
        let f = fixture();
        let a = check(&f).unwrap();
        let b = check(&f).unwrap();
        assert_eq!(a, b);
        assert_eq!(a.audio_sample_rate, 48_000);
        assert_eq!(a.audio_channels, 2);
        assert_eq!(a.video_segment_count, 2);
        assert_eq!(a.total_frames, 120);
        assert_eq!(a.final_profile, "h264-aac-mp4");
        assert_eq!(
            a.evidence_scope,
            "verified-owner-audio-ffv1-inputs-not-muxed-mp4"
        );
        assert!(digest_valid(&a.source_fingerprint));
        let result = serde_json::to_value(a).unwrap();
        for forbidden in [
            "path",
            "wav_path",
            "source_root",
            "master",
            "mux",
            "video_bytes",
        ] {
            assert!(
                result.get(forbidden).is_none(),
                "No raw media or path should be returned"
            );
        }
    }

    #[test]
    fn timing_uses_rational_fps_without_inventing_resampling_or_padding() {
        assert!(measured_voice_matches_cut(4, 1, 120, 30, 1).is_ok());
        assert!(measured_voice_matches_cut(1001, 1000, 30, 30_000, 1001).is_ok());
        assert!(measured_voice_matches_cut(3999, 1000, 120, 30, 1).is_err());
        assert!(measured_voice_matches_cut(0, 1, 120, 30, 1).is_err());
        assert!(measured_voice_matches_cut(4, 1, 120, 0, 1).is_err());
        // Less than 1/48000 second is accepted; multiple samples are not.
        assert!(measured_voice_matches_cut(192001, 48000, 120, 30, 1).is_ok());
        assert!(measured_voice_matches_cut(192003, 48000, 120, 30, 1).is_err());
    }

    #[test]
    fn source_version_cut_identity_and_semantic_profile_are_fail_closed() {
        let mut f = fixture();
        f.project.revision += 1;
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::StaleReference);
        f = fixture();
        f.prepared.verified_video_segments.swap(0, 1);
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::StaleReference);
        f = fixture();
        f.prepared.verified_video_segments[1].segment_id =
            f.prepared.verified_video_segments[0].segment_id.clone();
        assert!(check(&f).is_err());
        f = fixture();
        f.recipe.provider_profile.width = 1280;
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::StaleReference);
        f = fixture();
        f.prepared.evidence_scope = "assembled-h264-aac-master".into();
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::StaleReference);
        f = fixture();
        f.project.deliverables[0].burn_in_captions = true;
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::Unsupported);
        f = fixture();
        f.project.audio.mix.target_lufs = Some(-14.0);
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::Unsupported);
    }

    #[test]
    fn measured_track_binding_stale_audio_and_output_byte_tamper_are_rejected() {
        let mut f = fixture();
        f.project.deliverables[0].voice_track_id = None;
        assert!(check(&f).is_err());
        f = fixture();
        f.project.audio.voice_tracks[0].measured_duration = RationalTime::new(39, 10).unwrap();
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::Unsupported);
        f = fixture();
        f.voice.sha256 = "f".repeat(64);
        assert!(check(&f).is_err());
        f = fixture();
        let file = f
            .video_root
            .path()
            .join(&f.prepared.verified_video_segments[0].artifact_path);
        fs::write(&file, b"tampered-after-native-FFV1-receipt").unwrap();
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::StaleReference);
        f = fixture();
        fs::write(&f.voice.path, b"RIFFbadwav-but-different-digest").unwrap();
        assert_eq!(check(&f).unwrap_err().code, ErrorCode::StaleReference);
    }

    #[cfg(unix)]
    #[test]
    fn untrusted_symlinked_native_output_is_not_read_as_authoritative_audio_source() {
        use std::os::unix::fs::symlink;
        let f = fixture();
        let first = &f.prepared.verified_video_segments[0];
        let original = f.video_root.path().join(&first.artifact_path);
        let retained = f.video_root.path().join("retained.mkv");
        fs::rename(&original, &retained).unwrap();
        symlink(&retained, &original).unwrap();
        assert!(check(&f).is_err());
    }
}
