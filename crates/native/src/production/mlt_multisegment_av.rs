//! Final measured-voice H.264/AAC for the exact *semantic* MLT multisegment
//! cut. All native work stays in the existing Semwright Driver Host.
//! No arbitrary media paths, unverified PCM, shell renderer, audio stretching,
//! source revision bypass or destructive project-close operation.
use super::*;
use crate::{
    assembly::preflight_multi_segment_mlt,
    mlt_av_audio::{
        MultiSegmentAudioReadiness, preflight_multi_segment_audio,
        preflight_multi_segment_saved_mix,
    },
    mlt_edit_plan::mlt_native_video_recipe,
    original_mix::prepare_saved_original_mix_with_budget,
};

/// Caller-owned, typed and non-deserializable; never accept a filesystem
/// path or codec selector directly from a browser or an LLM.
pub struct MltMultisegmentAvMasterRequest<'a> {
    pub request_id: &'a str,
    pub deliverable_id: Uuid,
    pub options: &'a FilmBuildOptions,
    pub rendered: &'a MotionCanvasRenderEvidence,
    /// Verified result of the *separate* new no-audio native MLT render.
    pub visual: &'a MltVerifiedLosslessTimeline,
    /// Already staged through trusted Studio CAS/owner-output authority.
    pub audio: &'a MltAudioArtifact,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltMultisegmentAvMasterEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub frame_count: u64,
    pub video: MltVerifiedLosslessTimeline,
    pub measured_voice: MultiSegmentAudioReadiness,
    pub staged_audio: MltAudioArtifact,
    pub master: Value,
    pub decoded_audio: Value,
    pub evidence_scope: String,
}

fn sha256_literal(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Trust only closed, source-derived export identity and clock.
pub(super) struct MuxExpected<'a> {
    pub(super) frames: u64,
    pub(super) width: u32,
    pub(super) height: u32,
    pub(super) fps_num: u32,
    pub(super) fps_den: u32,
    pub(super) source_video: &'a str,
    pub(super) source_audio: &'a str,
    pub(super) output_path: &'a str,
}

pub(super) fn verify_exact_native_mux(
    response: &Value,
    expected: &MuxExpected<'_>,
) -> NativeResult<()> {
    let media = response
        .get("media")
        .ok_or_else(|| backend("Native multisegment mux has no actual video/audio measurement"))?;
    if response.get("profile").and_then(Value::as_str) != Some("h264-aac-mp4")
        || response.get("frame_count").and_then(Value::as_u64) != Some(expected.frames)
        || response.get("fps_num").and_then(Value::as_u64) != Some(u64::from(expected.fps_num))
        || response.get("fps_den").and_then(Value::as_u64) != Some(u64::from(expected.fps_den))
        || response.get("sample_rate").and_then(Value::as_u64) != Some(48_000)
        || response.get("channels").and_then(Value::as_u64) != Some(2)
        || media.get("video").and_then(Value::as_bool) != Some(true)
        || media.get("audio").and_then(Value::as_bool) != Some(true)
        || media.get("width").and_then(Value::as_u64) != Some(u64::from(expected.width))
        || media.get("height").and_then(Value::as_u64) != Some(u64::from(expected.height))
        || media
            .get("frames")
            .and_then(Value::as_u64)
            .is_some_and(|n| n != expected.frames)
    {
        return Err(backend(
            "Native multisegment AV mux profile/frame/stream confirmation differs",
        ));
    }
    // Semwright's MediaInfo::json() exposes codec/geometry/stream presence
    // but intentionally DOES NOT serialize sample_rate or channels. They
    // are checked from av.mux's independently measured, bounded top-level
    // fields above. Require actual AAC/decoded-PCM frame measurements here,
    // never synthesize those fields inside the media object.
    if expected.fps_num == 0 || expected.fps_den == 0 || expected.frames == 0 {
        return Err(backend("Native mux frame clock is invalid"));
    }
    // Source-bound frame count and AAC packet tolerance alone do not prove
    // the measured duration agrees with the authored frame clock. The probe
    // reports the first timed stream, falling back to container duration;
    // allow at most 1 ms for that fallback's millisecond rounding.
    // Other streams, AAC packet presentation and channel content still need
    // independent checks on original MP4 bytes before physical acceptance.
    let duration_num = media
        .get("duration_num")
        .and_then(Value::as_u64)
        .filter(|n| *n > 0)
        .ok_or_else(|| backend("Native mux lacks a positive measured media duration"))?;
    let duration_den = media
        .get("duration_den")
        .and_then(Value::as_u64)
        .filter(|n| *n > 0)
        .ok_or_else(|| backend("Native mux lacks a positive media duration timebase"))?;
    let measured_ticks = u128::from(duration_num) * u128::from(expected.fps_num);
    let source_ticks = u128::from(expected.frames)
        .checked_mul(u128::from(expected.fps_den))
        .and_then(|n| n.checked_mul(u128::from(duration_den)))
        .ok_or_else(|| backend("Native mux measured frame clock overflow"))?;
    let one_millisecond_ticks = (u128::from(expected.fps_num) * u128::from(duration_den)) / 1_000;
    if measured_ticks.abs_diff(source_ticks) > one_millisecond_ticks {
        return Err(backend(
            "Native mux measured duration differs from the authored source frame clock",
        ));
    }
    let expected_samples = u128::from(expected.frames)
        .checked_mul(48_000)
        .and_then(|n| n.checked_mul(u128::from(expected.fps_den)))
        .ok_or_else(|| backend("Native multisegment PCM sample clock overflow"))?
        / u128::from(expected.fps_num);
    if expected_samples == 0 || expected_samples > 48_000 * 600 {
        return Err(backend(
            "Native mux PCM duration is outside bounded native profile",
        ));
    }
    // AAC represents audio in 1024-sample access units. Priming/trailing
    // packets may add up to one encoded unit while the source WAV remains
    // exactly measured to one PCM sample by preflight_multi_segment_audio.
    // Do not mistake AAC packet granularity for an approved creative mix.
    for key in ["audio_sample_frames", "decoded_audio_sample_frames"] {
        let observed = response
            .get(key)
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Native AAC mux lacks counted audio samples"))?;
        if expected_samples.abs_diff(u128::from(observed)) > 1_024 {
            return Err(backend(
                "Native AAC sample clock diverges from measured source",
            ));
        }
    }
    let decoded_media = response
        .get("decoded_audio_media")
        .ok_or_else(|| backend("Native mux omitted decoded WAV media evidence"))?;
    if decoded_media.get("audio").and_then(Value::as_bool) != Some(true)
        || decoded_media.get("video").and_then(Value::as_bool) != Some(false)
        || !decoded_media
            .get("codecs")
            .and_then(Value::as_array)
            .is_some_and(|codecs| codecs.len() == 1 && codecs[0].as_str() == Some("pcm_s16le"))
    {
        return Err(backend(
            "Native mux decoded audio is not one PCM s16le stream",
        ));
    }
    let codecs = media
        .get("codecs")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Native multisegment MP4 lacks measured codec metadata"))?;
    if codecs.len() != 2
        || !codecs.iter().any(|v| v.as_str() == Some("h264"))
        || !codecs.iter().any(|v| v.as_str() == Some("aac"))
    {
        return Err(backend(
            "Native multisegment MP4 codecs are not H.264 plus AAC",
        ));
    }
    let artifact = response
        .get("artifact")
        .ok_or_else(|| backend("Native MP4 artifact absent"))?;
    let decoded = response
        .get("decoded_audio")
        .ok_or_else(|| backend("Native MP4 decoded audio is not verified"))?;
    if artifact.get("root").and_then(Value::as_str) != Some("output")
        || artifact.get("path").and_then(Value::as_str) != Some(expected.output_path)
        || !artifact
            .get("sha256")
            .and_then(Value::as_str)
            .is_some_and(sha256_literal)
        || !artifact
            .get("bytes")
            .and_then(Value::as_u64)
            .is_some_and(|n| (1..=MLT_MASTER_MAX_BYTES).contains(&n))
        || decoded.get("root").and_then(Value::as_str) != Some("output")
        || !decoded
            .get("bytes")
            .and_then(Value::as_u64)
            .is_some_and(|n| (1..=MLT_MASTER_MAX_BYTES).contains(&n))
        || !decoded
            .get("sha256")
            .and_then(Value::as_str)
            .is_some_and(sha256_literal)
        || !decoded
            .get("path")
            .and_then(Value::as_str)
            .is_some_and(|p| !p.is_empty() && p.len() <= 4096)
    {
        return Err(backend(
            "Native MP4 master/decoded-audio artifact identity is incomplete",
        ));
    }
    // The *pinned* Semwright AV mux always returns exact input digests.
    // Require both provenance attestations; missing identifiers must not
    // silently qualify as source-bound success. The app also rehashes
    // the original owner-root media independently before dispatch.
    for (key, digest) in [
        ("video_sha256", expected.source_video),
        ("audio_sha256", expected.source_audio),
    ] {
        if response.get(key).and_then(Value::as_str) != Some(digest) {
            return Err(backend(
                "Native mux receipt contradicts the selected source bytes",
            ));
        }
    }
    Ok(())
}

/// Both AV routes must compare actual bytes as well as the provider digest.
/// This checks existing owner-root output; it does not decode or render media.
pub(super) fn verify_mux_artifact_file(
    output_root: &std::path::Path,
    artifact: &Value,
) -> NativeResult<()> {
    if artifact.get("root").and_then(Value::as_str) != Some("output") {
        return Err(backend(
            "Native mux artifact is outside the owner output root",
        ));
    }
    let relative = required_string(artifact, "/path", "Native mux artifact path is missing")?;
    let sha256 = required_string(artifact, "/sha256", "Native mux artifact digest is missing")?;
    let bytes = artifact
        .get("bytes")
        .and_then(Value::as_u64)
        .filter(|n| (1..=MLT_MASTER_MAX_BYTES).contains(n))
        .ok_or_else(|| backend("Native mux artifact byte count is missing"))?;
    let path = verify_output_artifact(output_root, &relative, &sha256, MLT_MASTER_MAX_BYTES)?;
    if fs::metadata(path)
        .map_err(|_| backend("Native mux artifact became inaccessible"))?
        .len()
        != bytes
    {
        return Err(backend(
            "Native mux artifact actual byte count differs from its receipt",
        ));
    }
    Ok(())
}

/// Bind the same opened, bounded owner output file to its preflight digest.
/// A later path lookup or caller-supplied sample counter cannot stand in for
/// these media bytes. Keeping the original handle also avoids a second path
/// lookup while checking decoded PCM content.
fn open_verified_mux_wav(
    path: &std::path::Path,
    expected_sha256: &str,
) -> NativeResult<semwright_audio_domain::wav::WaveReader<std::fs::File>> {
    use std::io::{Read as _, Seek as _};

    if !sha256_literal(expected_sha256) {
        return Err(backend(
            "Native AV source/decoded WAV SHA-256 is not bounded",
        ));
    }
    let mut file = std::fs::File::open(path)
        .map_err(|_| backend("Native AV WAV could not be opened by its original path"))?;
    let size = file
        .metadata()
        .map_err(|_| backend("Native AV WAV descriptor has no original size"))?
        .len();
    if !(44..=MLT_MASTER_MAX_BYTES).contains(&size) {
        return Err(backend(
            "Native AV source/decoded WAV exceeds the exact file budget",
        ));
    }
    let mut count = 0_u64;
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; 65_536];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|_| backend("Native AV WAV descriptor read failed"))?;
        if read == 0 {
            break;
        }
        count = count
            .checked_add(read as u64)
            .filter(|value| *value <= size)
            .ok_or_else(|| backend("Native AV WAV changed while hashing"))?;
        hash.update(&buffer[..read]);
    }
    if count != size || hex::encode(hash.finalize()) != expected_sha256 {
        return Err(backend(
            "Native AV WAV bytes differ from the owned original digest",
        ));
    }
    file.seek(std::io::SeekFrom::Start(0))
        .map_err(|_| backend("Native AV WAV original descriptor could not rewind"))?;
    semwright_audio_domain::wav::WaveReader::open(file, MLT_MASTER_MAX_BYTES)
        .map_err(|_| backend("Native AV WAV is not a complete bounded PCM source"))
}

/// Supplemental Native-side source-to-output guard. The CI/G2 oracles still
/// check *all* authored windows and AAC packet presentation independently.
/// A lost terminal right (or left) channel must not count as a successful
/// product Native mux merely because the file digest and sample count exist.
fn require_native_mux_stereo_tail<
    R1: std::io::Read + std::io::Seek,
    R2: std::io::Read + std::io::Seek,
>(
    mut source: semwright_audio_domain::wav::WaveReader<R1>,
    mut decoded: semwright_audio_domain::wav::WaveReader<R2>,
    expected_frames: u64,
) -> NativeResult<()> {
    let source_info = source.info().clone();
    let decoded_info = decoded.info().clone();
    // The existing measured-voice admission supports PCM16/24/32 and
    // IEEE-float32 sources. Its actual decoded AAC output is always PCM16.
    // This supplemental content screen must not narrow that source policy.
    let source_stereo = source_info.sample_rate.0 == 48_000
        && source_info.channels == 2
        && ((source_info.ieee_float
            && source_info.container_bits == 32
            && source_info.valid_bits == 32)
            || (!source_info.ieee_float
                && matches!(source_info.container_bits, 16 | 24 | 32)
                && source_info.valid_bits == source_info.container_bits))
        && source_info.channel_mask.is_none_or(|mask| mask == 3);
    let decoded_stereo = decoded_info.sample_rate.0 == 48_000
        && decoded_info.channels == 2
        && decoded_info.container_bits == 16
        && decoded_info.valid_bits == 16
        && !decoded_info.ieee_float
        && decoded_info.channel_mask.is_none_or(|mask| mask == 3);
    if !source_stereo
        || !decoded_stereo
        || source_info.frames != expected_frames
        || expected_frames == 0
        || decoded_info.frames < expected_frames
        || decoded_info.frames > expected_frames.saturating_add(1_024)
    {
        return Err(backend(
            "Native mux source/decoded stereo PCM profile or clock differs",
        ));
    }
    let start = expected_frames.saturating_sub(256);
    let tail_frames = expected_frames - start;
    let mut source_l1 = [0_f64; 2];
    let mut decoded_l1 = [0_f64; 2];
    let mut offset = 0_u64;
    while offset < expected_frames {
        let take = (expected_frames - offset).min(16_384) as usize;
        let original = source
            .read_frames(take)
            .map_err(|_| backend("Native AV source WAV lost original sample frames"))?;
        let output = decoded
            .read_frames(take)
            .map_err(|_| backend("Native AV decoded WAV lost original sample frames"))?;
        if original.len() != take * 2 || output.len() != original.len() {
            return Err(backend(
                "Native AV stereo source and output PCM frames differ",
            ));
        }
        for (index, (before, after)) in original
            .as_chunks::<2>()
            .0
            .iter()
            .zip(output.as_chunks::<2>().0.iter())
            .enumerate()
        {
            for channel in 0..2 {
                if !before[channel].is_finite() || !after[channel].is_finite() {
                    return Err(backend(
                        "Native AV contains nonfinite authored or decoded PCM",
                    ));
                }
                if offset + index as u64 >= start {
                    // WaveReader samples are normalized to [-1, 1].
                    // Retain the same authored PCM16 amplitude units as the
                    // 256-frame source-content oracle: 64 means 64 / 32768.
                    source_l1[channel] += before[channel].abs() * 32768.0;
                    decoded_l1[channel] += after[channel].abs() * 32768.0;
                }
            }
        }
        offset += take as u64;
    }
    if !source
        .read_frames(1)
        .map_err(|_| backend("Native AV original source terminal frame is unreadable"))?
        .is_empty()
    {
        return Err(backend("Native AV source contains undeclared terminal PCM"));
    }
    let extra = (decoded_info.frames - expected_frames) as usize;
    let padding = decoded
        .read_frames(extra.saturating_add(1))
        .map_err(|_| backend("Native AV AAC decoded padding frames are unreadable"))?;
    if padding.len() != extra * 2 {
        return Err(backend(
            "Native AV decoded AAC padding differs from its original clock",
        ));
    }
    for channel in 0..2 {
        let mean = source_l1[channel] / tail_frames as f64;
        if mean >= 64.0 && decoded_l1[channel] * 5.0 < source_l1[channel] {
            return Err(backend(
                "Native AV mux lost authored stereo channel content before source end",
            ));
        }
    }
    Ok(())
}

impl ProductionCoordinator {
    /// Finalize ONLY the existing verified no-audio MLT semantic cut against
    /// a project-selected measured voice WAV. This never assembles/shadows
    /// another visual timeline. Real AV media must pass the pinned av.mux Host.
    pub async fn assemble_native_mlt_multisegment_av_master(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltMultisegmentAvMasterRequest<'_>,
    ) -> NativeResult<MltMultisegmentAvMasterEvidence> {
        self.assemble_native_mlt_multisegment_av_master_checked(project_id, expected, request, None)
            .await
    }

    pub(super) async fn assemble_native_mlt_multisegment_av_master_checked(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltMultisegmentAvMasterRequest<'_>,
        measured_source: Option<&super::audio_analysis::VerifiedAudioMeasurement>,
    ) -> NativeResult<MltMultisegmentAvMasterEvidence> {
        let MltMultisegmentAvMasterRequest {
            request_id,
            deliverable_id,
            options,
            rendered,
            visual,
            audio,
        } = request;
        if request_id.is_empty()
            || request_id.len() > 54
            || !request_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(invalid(
                "Native multisegment AV requires bounded stable request identity",
            ));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
            || self.client.connection().resource != project.resource_key()
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native multisegment AV project or Broker owner root is stale",
            ));
        }
        super::audio_analysis::admit_profile_audio_targets(
            self,
            &project,
            deliverable_id,
            request_id,
            super::audio_analysis::AudioAnalysisLane::Multisegment,
            measured_source,
        )?;
        let checked = preflight_multi_segment_mlt(
            &project,
            deliverable_id,
            options,
            rendered,
            &self.client.connection().output_root,
        )?;
        let mut scoped =
            self.with_mlt_motion_scene_boundary_scope(&project, options, &checked, rendered)?;
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
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native MLT video-only intermediary is not exact source-verified content",
            ));
        }
        let output_root = &self.client.connection().output_root;
        let source_video = verify_output_artifact(
            output_root,
            &visual.artifact_path,
            &visual.artifact_sha256,
            MLT_MASTER_MAX_BYTES,
        )?;
        if fs::metadata(source_video)
            .map_err(|_| backend("Native no-audio FFV1 is inaccessible"))?
            .len()
            != visual.artifact_bytes
        {
            return Err(backend(
                "Native no-audio FFV1 bytes disagree with source receipt",
            ));
        }
        let voice_track_id = project
            .deliverables
            .iter()
            .find(|profile| profile.id == deliverable_id)
            .and_then(|profile| profile.voice_track_id)
            .ok_or_else(|| {
                Error::new(
                    ErrorCode::Unsupported,
                    "A measured voice take must be selected for this deliverable",
                )
            })?;
        let voice = self
            .service
            .verified_master_voice(project_id, expected, voice_track_id)
            .map_err(storage_error)?;
        let saved_mix = if project
            .production_design
            .delivery_recipes
            .get(&deliverable_id)
            .and_then(|recipe| recipe.original_mix.as_ref())
            .is_some()
        {
            Some(
                prepare_saved_original_mix_with_budget(
                    &self.service,
                    project_id,
                    expected,
                    deliverable_id,
                    motionwright_service::OriginalMixDecodeBudget::FullCut180Seconds,
                )
                .map_err(crate::original_mix::MixError::into_native)?,
            )
        } else {
            None
        };
        // Re-derive the bounded pure PCM result from canonical CAS/recipe. The
        // staged audio receipt alone never authorizes a different soundtrack.
        let audio_readiness = match &saved_mix {
            Some(mix) => preflight_multi_segment_saved_mix(
                &project,
                deliverable_id,
                &recipe,
                &visual.source,
                &voice,
                mix,
                output_root,
            )?,
            None => preflight_multi_segment_audio(
                &project,
                deliverable_id,
                &recipe,
                &visual.source,
                &voice,
                output_root,
            )?,
        };
        if !sha256_literal(&audio.sha256)
            || audio.sample_rate != 48_000
            || audio.channels != 2
            || audio_readiness.audio_sha256 != audio.sha256
            || audio_readiness.total_frames != visual.frame_count
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native staged 48k stereo audio does not match the exact selected voice or saved mix",
            ));
        }
        let owned_source_wav = verify_output_artifact(
            output_root,
            &audio.relative_path,
            &audio.sha256,
            MLT_MASTER_MAX_BYTES,
        )?;
        let token = hex::encode(Sha256::digest(request_id.as_bytes()));
        let output_path = format!(
            "mw-{}-r{}-{}-multisegment-master.mp4",
            project.id.simple(),
            project.revision,
            &token[..16],
        );
        // av.mux *requires* video with no audio and separate measured WAV.
        // Both input references originate from verified owner output receipts,
        // never arbitrary client filenames. Broker handles result uncertainty.
        scoped.bind_mlt_mux_output(&output_path)?;
        let encoded = scoped
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:multisegment-av-mux"),
                "driver.mlt-video.av.mux",
                json!({
                    "video_root":"output",
                    "video_path":visual.artifact_path,
                    "video_sha256":visual.artifact_sha256,
                    "audio_root":"output",
                    "audio_path":audio.relative_path,
                    "audio_sha256":audio.sha256,
                    "width":recipe.provider_profile.width,
                    "height":recipe.provider_profile.height,
                    "fps_num":recipe.provider_profile.fps_num,
                    "fps_den":recipe.provider_profile.fps_den,
                    "frame_count":recipe.total_frames,
                    "sample_rate":48_000,
                    "channels":2,
                    "profile":"h264-aac-mp4",
                    "output_path":output_path,
                    "max_bytes":MLT_MASTER_MAX_BYTES,
                }),
                true,
            )
            .await?;
        let master = response_data(&encoded)?.clone();
        verify_exact_native_mux(
            &master,
            &MuxExpected {
                frames: recipe.total_frames,
                width: recipe.provider_profile.width,
                height: recipe.provider_profile.height,
                fps_num: recipe.provider_profile.fps_num,
                fps_den: recipe.provider_profile.fps_den,
                source_video: &visual.artifact_sha256,
                source_audio: &audio.sha256,
                output_path: &output_path,
            },
        )?;
        let master_sha256 = required_string(
            &master,
            "/artifact/sha256",
            "Native multisegment AV master digest absent",
        )?;
        let master_path = required_string(
            &master,
            "/artifact/path",
            "Native multisegment AV master path absent",
        )?;
        let verified_mp4 = verify_output_artifact(
            output_root,
            &master_path,
            &master_sha256,
            MLT_MASTER_MAX_BYTES,
        )?;
        if fs::metadata(verified_mp4)
            .map_err(|_| backend("Native multisegment MP4 became inaccessible"))?
            .len()
            != master
                .pointer("/artifact/bytes")
                .and_then(Value::as_u64)
                .unwrap_or(0)
        {
            return Err(backend("Native multisegment MP4 byte count differs"));
        }
        let decoded_audio = master
            .get("decoded_audio")
            .cloned()
            .ok_or_else(|| backend("Native AV decoded audio evidence is missing"))?;
        let decoded_path = required_string(
            &master,
            "/decoded_audio/path",
            "Native AV decoded WAV output path is missing",
        )?;
        let decoded_sha256 = required_string(
            &master,
            "/decoded_audio/sha256",
            "Native AV decoded WAV SHA-256 is missing",
        )?;
        let owned_decoded_wav = verify_output_artifact(
            output_root,
            &decoded_path,
            &decoded_sha256,
            MLT_MASTER_MAX_BYTES,
        )?;
        // The native owner provided these exact source and decoded WAV
        // digests, but SHA and AAC frame counts alone cannot establish that
        // either authored channel survives the last source window. Consume
        // both already-verified files through their opened hashed handles
        // before accepting the Native mux, without replaying any operation.
        require_native_mux_stereo_tail(
            open_verified_mux_wav(&owned_source_wav, &audio.sha256)?,
            open_verified_mux_wav(&owned_decoded_wav, &decoded_sha256)?,
            audio_readiness.audio_pcm_frames,
        )?;
        let latest = self.service.project(project_id).map_err(storage_error)?;
        if latest.generation != project.generation || latest.revision != project.revision {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project revision changed during native multisegment AV mux",
            ));
        }
        let captioned = audio_readiness.caption_cue_count.is_some();
        Ok(MltMultisegmentAvMasterEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_count: recipe.total_frames,
            video: visual.clone(),
            measured_voice: audio_readiness,
            staged_audio: audio.clone(),
            master,
            decoded_audio,
            evidence_scope: if captioned {
                // Native Film already verified exact caption activity before
                // FFV1; MLT's mux binds actual source file SHA and frame count.
                // The lossy MP4 pixels still need independent decoded review.
                "actual-native-mlt-h264-aac-native-caption-source-not-decoded-pixel-or-human-approved"
            } else {
                "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
            }
            .into(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn closed_mux_verifier_refuses_wrong_output_audio_or_frame_count() {
        let mut r = json!({
            "profile":"h264-aac-mp4", "frame_count":33,
            "fps_num":30,"fps_den":1,
            "sample_rate":48000,"channels":2,
            "audio_sample_frames":52800,"decoded_audio_sample_frames":52800,
            "media":{"width":1280,"height":720,"video":true,
                     "audio":true,"frames":33,"duration_num":1100,
                     "duration_den":1000,"codecs":["h264","aac"]},
            "decoded_audio_media":{"video":false,"audio":true,"codecs":["pcm_s16le"]},
            "artifact":{"root":"output","path":"owner-master.mp4",
                        "sha256":"a".repeat(64),"bytes":10240},
            "decoded_audio":{"root":"output","path":"owner-decoded.wav","sha256":"b".repeat(64),"bytes":213070},
            "video_sha256":"c".repeat(64),
            "audio_sha256":"d".repeat(64)
        });
        let check = |v: &Value| {
            verify_exact_native_mux(
                v,
                &MuxExpected {
                    frames: 33,
                    width: 1280,
                    height: 720,
                    fps_num: 30,
                    fps_den: 1,
                    source_video: &"c".repeat(64),
                    source_audio: &"d".repeat(64),
                    output_path: "owner-master.mp4",
                },
            )
        };
        assert!(check(&r).is_ok());
        r["frame_count"] = json!(32);
        assert!(check(&r).is_err());
        r["frame_count"] = json!(33);
        r["media"]["codecs"] = json!(["h264", "pcm_s16le"]);
        assert!(check(&r).is_err());
        r["media"]["codecs"] = json!(["h264", "aac"]);
        r["media"]["audio"] = json!(false);
        assert!(check(&r).is_err());
        r["media"]["audio"] = json!(true);
        r["media"]["width"] = json!(1920);
        assert!(check(&r).is_err());
        r["media"]["width"] = json!(1280);
        r["artifact"]["path"] = json!("foreign-master.mp4");
        assert!(check(&r).is_err());
        r["artifact"]["path"] = json!("owner-master.mp4");
        r["decoded_audio"]["sha256"] = json!("broken");
        assert!(check(&r).is_err());
        r["decoded_audio"]["sha256"] = json!("b".repeat(64));
        r["sample_rate"] = json!(44100);
        assert!(check(&r).is_err());
        r["sample_rate"] = json!(48000);
        r["audio_sample_frames"] = json!(54000);
        assert!(check(&r).is_err());
        r["audio_sample_frames"] = json!(52800);
        r["decoded_audio_sample_frames"] = Value::Null;
        assert!(check(&r).is_err());
        r["decoded_audio_sample_frames"] = json!(52800);
        r["decoded_audio_media"]["codecs"] = json!(["aac"]);
        assert!(check(&r).is_err());
        r["decoded_audio_media"]["codecs"] = json!(["pcm_s16le"]);
        assert!(check(&r).is_ok());
        // Reject a measured 1.109-second duration for a 1.100-second cut
        // even when 53,248 decoded frames fit the AAC packet tolerance.
        // A first-stream duration alone does not verify other streams.
        r["decoded_audio_sample_frames"] = json!(53248);
        r["media"]["duration_num"] = json!(1109);
        assert!(check(&r).is_err());
        r["media"]["duration_num"] = json!(1101);
        assert!(check(&r).is_ok()); // one millisecond of probe rounding
        r["media"]["duration_num"] = json!(1098);
        assert!(check(&r).is_err());
        r["media"]["duration_num"] = json!(1100);
        r["media"]["duration_den"] = json!(0);
        assert!(check(&r).is_err());
        r["media"]["duration_den"] = json!(1000);
        r["media"].as_object_mut().unwrap().remove("duration_num");
        assert!(check(&r).is_err());
        r["media"]["duration_num"] = json!(1100);
        r["audio_sample_frames"] = json!(52800);
        r["decoded_audio_sample_frames"] = json!(52800);
        assert!(check(&r).is_ok());
        r.as_object_mut().unwrap().remove("video_sha256");
        assert!(check(&r).is_err());
        r["video_sha256"] = json!("c".repeat(64));
        r["audio_sha256"] = json!("a".repeat(64));
        assert!(check(&r).is_err());
        r["audio_sha256"] = json!("d".repeat(64));
        r["decoded_audio"]["root"] = json!("project");
        assert!(check(&r).is_err());
    }

    fn source_wav_fixture(stereo: &[[i16; 2]]) -> Vec<u8> {
        let payload = u32::try_from(stereo.len() * 4).unwrap();
        let mut wav = Vec::with_capacity(44 + payload as usize);
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36_u32 + payload).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16_u32.to_le_bytes());
        wav.extend_from_slice(&1_u16.to_le_bytes());
        wav.extend_from_slice(&2_u16.to_le_bytes());
        wav.extend_from_slice(&48_000_u32.to_le_bytes());
        wav.extend_from_slice(&(48_000_u32 * 4).to_le_bytes());
        wav.extend_from_slice(&4_u16.to_le_bytes());
        wav.extend_from_slice(&16_u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&payload.to_le_bytes());
        for &[left, right] in stereo {
            wav.extend_from_slice(&left.to_le_bytes());
            wav.extend_from_slice(&right.to_le_bytes());
        }
        wav
    }

    fn source_wav_24bit_fixture(stereo: &[[i16; 2]]) -> Vec<u8> {
        let payload = u32::try_from(stereo.len() * 6).unwrap();
        let mut wav = Vec::with_capacity(44 + payload as usize);
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36_u32 + payload).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16_u32.to_le_bytes());
        wav.extend_from_slice(&1_u16.to_le_bytes());
        wav.extend_from_slice(&2_u16.to_le_bytes());
        wav.extend_from_slice(&48_000_u32.to_le_bytes());
        wav.extend_from_slice(&(48_000_u32 * 6).to_le_bytes());
        wav.extend_from_slice(&6_u16.to_le_bytes());
        wav.extend_from_slice(&24_u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&payload.to_le_bytes());
        for frame in stereo {
            for &sample in frame {
                let expanded = (i32::from(sample) << 8).to_le_bytes();
                wav.extend_from_slice(&expanded[..3]);
            }
        }
        wav
    }

    fn source_wav_float32_fixture(stereo: &[[i16; 2]]) -> Vec<u8> {
        let payload = u32::try_from(stereo.len() * 8).unwrap();
        let mut wav = Vec::with_capacity(44 + payload as usize);
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36_u32 + payload).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16_u32.to_le_bytes());
        wav.extend_from_slice(&3_u16.to_le_bytes());
        wav.extend_from_slice(&2_u16.to_le_bytes());
        wav.extend_from_slice(&48_000_u32.to_le_bytes());
        wav.extend_from_slice(&(48_000_u32 * 8).to_le_bytes());
        wav.extend_from_slice(&8_u16.to_le_bytes());
        wav.extend_from_slice(&32_u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&payload.to_le_bytes());
        for frame in stereo {
            for &sample in frame {
                let normalized = f32::from(sample) / 32768.0;
                wav.extend_from_slice(&normalized.to_le_bytes());
            }
        }
        wav
    }

    fn source_wav_32bit_pcm_fixture(stereo: &[[i16; 2]]) -> Vec<u8> {
        let mut wav = source_wav_float32_fixture(stereo);
        // Same valid 32-bit interleaved RIFF layout; replace the numeric
        // sample representation with signed PCM32, preserving amplitude.
        wav[20..22].copy_from_slice(&1_u16.to_le_bytes());
        for (chunk, sample) in wav[44..]
            .as_chunks_mut::<4>()
            .0
            .iter_mut()
            .zip(stereo.iter().flatten())
        {
            chunk.copy_from_slice(&(i32::from(*sample) << 16).to_le_bytes());
        }
        wav
    }

    fn source_tail_fixture() -> Vec<[i16; 2]> {
        (0..512)
            .map(|i| [((i % 64) as i16 - 32) * 280, ((i % 31) as i16 - 15) * 500])
            .collect()
    }

    fn check_tail(source: &[u8], decoded: &[u8], count: u64) -> NativeResult<()> {
        let source = semwright_audio_domain::wav::WaveReader::open(
            std::io::Cursor::new(source),
            MLT_MASTER_MAX_BYTES,
        )
        .expect("bounded test source WAV");
        let decoded = semwright_audio_domain::wav::WaveReader::open(
            std::io::Cursor::new(decoded),
            MLT_MASTER_MAX_BYTES,
        )
        .expect("bounded test decoded WAV");
        require_native_mux_stereo_tail(source, decoded, count)
    }

    #[test]
    fn native_mux_checks_real_terminal_stereo_with_valid_aac_packet_padding() {
        let stereo = source_tail_fixture();
        let original = source_wav_fixture(&stereo);
        assert!(check_tail(&original, &original, 512).is_ok());
        let mut padded = stereo;
        padded.extend(std::iter::repeat_n([0, 0], 448));
        let decoded = source_wav_fixture(&padded);
        assert!(check_tail(&original, &decoded, 512).is_ok());
    }

    #[test]
    fn native_mux_retains_existing_24bit_and_float32_measured_voice_sources() {
        let source = source_tail_fixture();
        let decoded = source_wav_fixture(&source);
        assert!(check_tail(&source_wav_24bit_fixture(&source), &decoded, 512).is_ok());
        assert!(check_tail(&source_wav_32bit_pcm_fixture(&source), &decoded, 512).is_ok());
        assert!(check_tail(&source_wav_float32_fixture(&source), &decoded, 512).is_ok());
    }

    #[test]
    fn native_mux_rejects_lost_channel_from_24bit_or_float32_source() {
        let source = source_tail_fixture();
        let mut damaged = source.clone();
        for frame in &mut damaged[256..] {
            frame[1] = 0;
        }
        let decoded = source_wav_fixture(&damaged);
        assert!(check_tail(&source_wav_24bit_fixture(&source), &decoded, 512).is_err());
        assert!(check_tail(&source_wav_32bit_pcm_fixture(&source), &decoded, 512).is_err());
        assert!(check_tail(&source_wav_float32_fixture(&source), &decoded, 512).is_err());
    }

    #[test]
    fn native_mux_rejects_nonfinite_float32_source_samples() {
        let source = source_tail_fixture();
        let mut invalid = source_wav_float32_fixture(&source);
        invalid[44..48].copy_from_slice(&f32::NAN.to_le_bytes());
        let decoded = source_wav_fixture(&source);
        assert!(check_tail(&invalid, &decoded, 512).is_err());
    }

    #[test]
    fn native_mux_rejects_lost_right_tail_even_with_other_channel_intact() {
        let original = source_tail_fixture();
        let source = source_wav_fixture(&original);
        let mut damaged = original;
        for frame in &mut damaged[256..] {
            frame[1] = 0;
        }
        let output = source_wav_fixture(&damaged);
        assert!(check_tail(&source, &output, 512).is_err());
        // The first 256 source frames are identical, so this detects only the
        // actual lost authored terminal channel, not a fake whole-file error.
        assert_eq!(&source[44..44 + 256 * 4], &output[44..44 + 256 * 4]);
    }

    #[test]
    fn native_mux_rejects_lost_left_tail_independently_of_right() {
        let original = source_tail_fixture();
        let source = source_wav_fixture(&original);
        let mut damaged = original;
        for frame in &mut damaged[256..] {
            frame[0] = 0;
        }
        assert!(check_tail(&source, &source_wav_fixture(&damaged), 512).is_err());
    }

    #[test]
    fn native_mux_rejects_truncated_decoded_audio_and_unclaimed_padding() {
        let original = source_tail_fixture();
        let source = source_wav_fixture(&original);
        assert!(check_tail(&source, &source_wav_fixture(&original[..511]), 512).is_err());
        let mut too_long = original;
        too_long.extend(std::iter::repeat_n([0, 0], 1025));
        assert!(check_tail(&source, &source_wav_fixture(&too_long), 512).is_err());
    }

    #[test]
    fn native_mux_tail_guard_does_not_claim_full_duration_or_g2_acceptance() {
        let original = source_tail_fixture();
        let source = source_wav_fixture(&original);
        let mut altered_nonterminal = original;
        for frame in &mut altered_nonterminal[..256] {
            frame[1] = 0;
        }
        // This supplemental producer guard does NOT replace the independent
        // full-duration, zero-offset G2 source-content and AAC timing proofs.
        assert!(check_tail(&source, &source_wav_fixture(&altered_nonterminal), 512).is_ok());
    }
}
