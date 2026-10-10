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

fn verify_exact_native_mux(
    response: &Value,
    frame_count: u64,
    width: u32,
    height: u32,
    source_video: &str,
    source_audio: &str,
    expected_output: &str,
) -> NativeResult<()> {
    let media = response
        .get("media")
        .ok_or_else(|| backend("Native multisegment mux has no actual video/audio measurement"))?;
    if response.get("profile").and_then(Value::as_str) != Some("h264-aac-mp4")
        || response.get("frame_count").and_then(Value::as_u64) != Some(frame_count)
        || response.get("sample_rate").and_then(Value::as_u64) != Some(48_000)
        || response.get("channels").and_then(Value::as_u64) != Some(2)
        || media.get("video").and_then(Value::as_bool) != Some(true)
        || media.get("audio").and_then(Value::as_bool) != Some(true)
        || media.get("width").and_then(Value::as_u64) != Some(u64::from(width))
        || media.get("height").and_then(Value::as_u64) != Some(u64::from(height))
        || media.get("sample_rate").and_then(Value::as_u64) != Some(48_000)
        || media.get("channels").and_then(Value::as_u64) != Some(2)
    {
        return Err(backend(
            "Native multisegment AV mux profile/frame/stream confirmation differs",
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
        || artifact.get("path").and_then(Value::as_str) != Some(expected_output)
        || !artifact
            .get("sha256")
            .and_then(Value::as_str)
            .is_some_and(sha256_literal)
        || !artifact
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
    // The pinned Semwright AV mux validates exact incoming SHA-256s itself.
    // Recheck its optional provenance identifiers if its result exposes them;
    // only our original verified owner-root inputs are supplied as arguments.
    for (key, digest) in [
        ("video_sha256", source_video),
        ("audio_sha256", source_audio),
    ] {
        if response
            .get(key)
            .is_some_and(|v| v.as_str() != Some(digest))
        {
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
            recipe.total_frames,
            recipe.provider_profile.width,
            recipe.provider_profile.height,
            &visual.artifact_sha256,
            &audio.sha256,
            &output_path,
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
            "sample_rate":48000,"channels":2,
            "media":{"width":1280,"height":720,"video":true,
                     "audio":true,"sample_rate":48000,"channels":2,
                     "codecs":["h264","aac"]},
            "artifact":{"root":"output","path":"owner-master.mp4",
                        "sha256":"a".repeat(64),"bytes":10240},
            "decoded_audio":{"path":"owner-decoded.wav","sha256":"b".repeat(64)}
        });
        let check = |v: &Value| {
            verify_exact_native_mux(
                v,
                33,
                1280,
                720,
                &"c".repeat(64),
                &"d".repeat(64),
                "owner-master.mp4",
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
    }
}
