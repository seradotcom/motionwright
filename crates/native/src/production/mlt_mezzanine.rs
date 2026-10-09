//! Real pinned Semwright MLT per-segment FFV1 preparation. This stage does
//! not assemble a timeline, encode a final MP4 or bypass Driver Host authority.
use super::*;
use crate::{assembly::preflight_multi_segment_mlt, mlt_edit_plan::mlt_native_video_recipe};

const MAX_TOTAL_FFV1_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltVerifiedMezzanine {
    pub segment_id: String,
    pub scene_ids: Vec<Uuid>,
    pub output_start_frame: u64,
    pub frame_count: u64,
    pub native_render_job_ref: String,
    pub source_manifest_sha256: String,
    pub artifact_path: String,
    pub artifact_sha256: String,
    pub artifact_bytes: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltPreparedMezzanines {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub input_sha256: String,
    pub fps_num: u32,
    pub fps_den: u32,
    pub total_frames: u64,
    pub verified_video_segments: Vec<MltVerifiedMezzanine>,
    pub evidence_scope: String,
}
fn validated_ffv1(
    value: &Value,
    expected_path: &str,
    expected_frames: u64,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
) -> NativeResult<(String, u64)> {
    if value.get("codec").and_then(Value::as_str) != Some("ffv1")
        || value.get("container").and_then(Value::as_str) != Some("matroska")
        || value.get("frame_count").and_then(Value::as_u64) != Some(expected_frames)
        || value.get("width").and_then(Value::as_u64) != Some(u64::from(width))
        || value.get("height").and_then(Value::as_u64) != Some(u64::from(height))
        || value.get("fps_num").and_then(Value::as_u64) != Some(u64::from(fps_num))
        || value.get("fps_den").and_then(Value::as_u64) != Some(u64::from(fps_den))
        || value.pointer("/media/video").and_then(Value::as_bool) != Some(true)
        || value.pointer("/media/audio").and_then(Value::as_bool) != Some(false)
        || value.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
        || value.pointer("/artifact/path").and_then(Value::as_str) != Some(expected_path)
    {
        return Err(backend(
            "Pinned MLT did not return the exact bounded audio-free FFV1 source",
        ));
    }
    let digest = required_string(value, "/artifact/sha256", "MLT FFV1 artifact lacks SHA-256")?;
    let size = value
        .pointer("/artifact/bytes")
        .and_then(Value::as_u64)
        .filter(|bytes| (1..=MLT_MEZZANINE_MAX_BYTES).contains(bytes))
        .ok_or_else(|| backend("MLT FFV1 intermediate byte count exceeded its limit"))?;
    Ok((digest, size))
}
impl ProductionCoordinator {
    /// Convert exact source-verified Motion Canvas segments via the existing
    /// Broker/Driver Host, checking the real FFV1 result for *each* segment.
    /// The caller must already hold owner-local render authority; this method
    /// is not registered as a raw WebView command.
    pub async fn prepare_multi_segment_ffv1(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        render: &MotionCanvasRenderEvidence,
    ) -> NativeResult<MltPreparedMezzanines> {
        if request_id.is_empty()
            || request_id.len() > 64
            || !request_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(invalid(
                "Multi-segment MLT request has an invalid bounded identity",
            ));
        }
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed before MLT FFV1 preparation",
            ));
        }
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "MLT owner connection belongs to another creative project",
            ));
        }
        // Rebuild/rehash input *inside* the authoritative owner output root,
        // never trust a WebView-provided manifest or fake preflight.
        let source = preflight_multi_segment_mlt(
            &project,
            deliverable_id,
            options,
            render,
            &self.client.connection().output_root,
        )?;
        let recipe = mlt_native_video_recipe(&source)?;
        let mut prepared = Vec::with_capacity(recipe.clips.len());
        let mut total_bytes = 0u64;
        for (index, clip) in recipe.clips.iter().enumerate() {
            let result = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:ffv1:{index:02}"),
                    "driver.mlt-video.frames.encode",
                    json!({
                        "root": "output",
                        "manifest_path": clip.source_manifest_path,
                        "expected_manifest_sha256": clip.source_manifest_sha256,
                        "output_path": clip.ffv1_output_path,
                        "max_bytes": clip.ffv1_max_bytes,
                    }),
                    true,
                )
                .await?;
            let data = response_data(&result)?;
            let (sha, byte_count) = validated_ffv1(
                data,
                &clip.ffv1_output_path,
                clip.source_out_exclusive,
                recipe.provider_profile.width,
                recipe.provider_profile.height,
                recipe.provider_profile.fps_num,
                recipe.provider_profile.fps_den,
            )?;
            let verified = verify_output_artifact(
                &self.client.connection().output_root,
                &clip.ffv1_output_path,
                &sha,
                clip.ffv1_max_bytes,
            )?;
            let actual_bytes = fs::metadata(verified)
                .map_err(|_| backend("MLT FFV1 artifact became inaccessible"))?
                .len();
            if actual_bytes != byte_count {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "MLT FFV1 output bytes differ from the native receipt",
                ));
            }
            total_bytes = total_bytes
                .checked_add(byte_count)
                .ok_or_else(|| invalid("FFV1 total byte budget overflow"))?;
            if total_bytes > MAX_TOTAL_FFV1_BYTES {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "MLT source intermediates exceed the 1 GiB aggregate budget",
                ));
            }
            prepared.push(MltVerifiedMezzanine {
                segment_id: clip.segment_id.clone(),
                scene_ids: clip.scene_ids.clone(),
                output_start_frame: clip.start_frame,
                frame_count: clip.source_out_exclusive,
                native_render_job_ref: clip.source_native_job_ref.clone(),
                source_manifest_sha256: clip.source_manifest_sha256.clone(),
                artifact_path: clip.ffv1_output_path.clone(),
                artifact_sha256: sha,
                artifact_bytes: byte_count,
            });
        }
        let current = self.service.project(project_id).map_err(storage_error)?;
        if current.generation != project.generation || current.revision != project.revision {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Creative revision changed during real MLT video preparation",
            ));
        }
        Ok(MltPreparedMezzanines {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            input_sha256: recipe.input_sha256,
            fps_num: recipe.provider_profile.fps_num,
            fps_den: recipe.provider_profile.fps_den,
            total_frames: recipe.total_frames,
            verified_video_segments: prepared,
            evidence_scope: "real-source-ffv1-segments-not-composited".into(),
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn evidence() -> Value {
        json!({
            "codec": "ffv1", "container": "matroska",
            "frame_count": 90, "width": 1920, "height": 1080,
            "fps_num": 30, "fps_den": 1,
            "media": {"video": true, "audio": false},
            "artifact": {
                "root": "output", "path": "verified-ffv1.mkv",
                "sha256": "a".repeat(64), "bytes": 8192
            }
        })
    }
    #[test]
    fn checked_real_mezzanine_must_match_every_source_and_codec_field() {
        let original = evidence();
        let validate =
            |value: &Value| validated_ffv1(value, "verified-ffv1.mkv", 90, 1920, 1080, 30, 1);
        assert_eq!(validate(&original).unwrap(), ("a".repeat(64), 8192));
        for (field, bad) in [
            ("codec", json!("h264")),
            ("container", json!("mp4")),
            ("frame_count", json!(91)),
            ("height", json!(720)),
            ("fps_den", json!(1001)),
        ] {
            let mut altered = original.clone();
            altered[field] = bad;
            assert!(validate(&altered).is_err());
        }
        for (field, bad) in [
            ("/media/video", json!(false)),
            ("/media/audio", json!(true)),
            ("/artifact/root", json!("media")),
            ("/artifact/path", json!("../../other.mkv")),
            ("/artifact/bytes", json!(0)),
        ] {
            let mut altered = original.clone();
            *altered.pointer_mut(field).unwrap() = bad;
            assert!(validate(&altered).is_err());
        }
        assert!(validated_ffv1(&original, "another.mkv", 90, 1920, 1080, 30, 1).is_err());
    }
}
