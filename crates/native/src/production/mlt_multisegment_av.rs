//! Final measured-voice H.264/AAC for the exact *semantic* MLT multisegment
//! cut. All native work stays in the existing Semwright Driver Host.
//! No arbitrary media paths, unverified PCM, shell renderer, audio stretching,
//! source revision bypass or destructive project-close operation.
use super::*;
use crate::{
    assembly::preflight_multi_segment_mlt,
    mlt_av_audio::{MultiSegmentAudioReadiness, preflight_multi_segment_audio},
    mlt_edit_plan::mlt_native_video_recipe,
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
struct MuxExpected<'a> {
    frames: u64,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
    source_video: &'a str,
    source_audio: &'a str,
    output_path: &'a str,
}

fn verify_exact_native_mux(response: &Value, expected: &MuxExpected<'_>) -> NativeResult<()> {
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
        let checked = preflight_multi_segment_mlt(
            &project,
            deliverable_id,
            options,
            rendered,
            &self.client.connection().output_root,
        )?;
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
        let audio_readiness = preflight_multi_segment_audio(
            &project,
            deliverable_id,
            &recipe,
            &visual.source,
            &voice,
            output_root,
        )?;
        if !sha256_literal(&audio.sha256)
            || voice.sha256 != audio.sha256
            || audio.sample_rate != 48_000
            || audio.channels != 2
            || audio_readiness.audio_sha256 != audio.sha256
            || audio_readiness.total_frames != visual.frame_count
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native staged 48k stereo voice does not match the measured CAS source",
            ));
        }
        verify_output_artifact(
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
        let encoded = self
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
        verify_output_artifact(
            output_root,
            &decoded_path,
            &decoded_sha256,
            MLT_MASTER_MAX_BYTES,
        )?;
        let latest = self.service.project(project_id).map_err(storage_error)?;
        if latest.generation != project.generation || latest.revision != project.revision {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Project revision changed during native multisegment AV mux",
            ));
        }
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
            evidence_scope:
                "actual-native-source-bound-multisegment-h264-aac-measured-voice-not-human-approved"
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
                     "audio":true,"frames":33,"codecs":["h264","aac"]},
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
        r.as_object_mut().unwrap().remove("video_sha256");
        assert!(check(&r).is_err());
        r["video_sha256"] = json!("c".repeat(64));
        r["audio_sha256"] = json!("a".repeat(64));
        assert!(check(&r).is_err());
        r["audio_sha256"] = json!("d".repeat(64));
        r["decoded_audio"]["root"] = json!("project");
        assert!(check(&r).is_err());
    }
}
