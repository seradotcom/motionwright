//! Deterministic, non-executing edit recipe for the pinned Semwright MLT
//! semantic timeline. It consumes only an already-verified canonical native
//! multi-segment preflight, never a model-authored render script or user path.
use crate::assembly::MultiSegmentAssemblyPreflight;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use uuid::Uuid;

const MAX_SEGMENTS: usize = 64;
const MAX_FRAMES: u64 = 36_000;
const MEZZANINE_MAX_BYTES: u64 = 512 * 1024 * 1024;

fn refuse(msg: &str) -> Error {
    Error::new(ErrorCode::InvalidArgument, msg)
}
fn sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltOwnedVideoProfile {
    pub width: u32,
    pub height: u32,
    pub fps_num: u32,
    pub fps_den: u32,
    pub progressive: bool,
    pub sample_aspect_num: u32,
    pub sample_aspect_den: u32,
    pub display_aspect_num: u32,
    pub display_aspect_den: u32,
    pub colorspace: u32,
    pub audio_channels: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltNativeSegmentEdit {
    pub segment_id: String,
    pub scene_ids: Vec<Uuid>,
    pub source_native_job_ref: String,
    pub source_manifest_path: String,
    pub source_manifest_sha256: String,
    pub ffv1_output_path: String,
    pub ffv1_max_bytes: u64,
    pub edit_asset_name: String,
    pub edit_clip_name: String,
    pub start_frame: u64,
    pub source_in: u64,
    /// Source interval is half-open, matching pinned Semwright FrameRange.
    pub source_out_exclusive: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltNativeVideoRecipe {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    /// Stable digest of the canonical input preflight, not a Broker token.
    pub input_sha256: String,
    /// Bounded filename namespace, owner output root only; not arbitrary IO.
    pub owner_output_namespace: String,
    pub provider_profile: MltOwnedVideoProfile,
    pub sequence_name: String,
    pub video_track_name: String,
    /// All clip insertion positions and ranges are exact output frames.
    pub clips: Vec<MltNativeSegmentEdit>,
    pub total_frames: u64,
    pub lossless_sequence_path: String,
    pub lossless_sequence_profile: String,
    pub final_mux_profile: String,
    pub scope: String,
}

/// Derive a closed, deterministic recipe. This method never executes a
/// driver, opens a filesystem, or claims any video has been assembled.
/// Only an owner-verified MultiSegmentAssemblyPreflight is admissible.
pub fn mlt_native_video_recipe(
    checked: &MultiSegmentAssemblyPreflight,
) -> NativeResult<MltNativeVideoRecipe> {
    if checked.evidence_scope != "manifest-digest-verified-not-composited-mp4"
        || checked.segments.len() < 2
        || checked.segments.len() > MAX_SEGMENTS
        || checked.total_frames == 0
        || checked.total_frames > MAX_FRAMES
    {
        return Err(refuse(
            "Native multi-segment source preflight is missing a canonical owner scope",
        ));
    }
    let project_id = checked
        .project_resource
        .strip_prefix("project:")
        .and_then(|value| Uuid::parse_str(value).ok())
        .ok_or_else(|| refuse("Native MLT recipe has an invalid Motionwright resource"))?;
    if checked.project_resource != format!("project:{project_id}") {
        return Err(refuse(
            "Native MLT recipe project resource is not canonical",
        ));
    }
    let (width, height) = match checked.mlt_profile.as_str() {
        "h264-1080p" => (1920, 1080),
        "h264-720p" => (1280, 720),
        _ => {
            return Err(refuse(
                "Native MLT timeline has an unsupported output profile",
            ));
        }
    };
    if checked.fps_num == 0
        || checked.fps_den == 0
        || checked.fps_num > 240_000
        || checked.fps_den > 100_000
        || u64::from(checked.fps_num) > u64::from(checked.fps_den) * 240
    {
        return Err(refuse(
            "Native MLT timeline has an invalid rational output frame rate",
        ));
    }
    // Only stable canonical input data contributes to the clip/output names.
    let serialized = serde_json::to_vec(checked)
        .map_err(|_| refuse("Native MLT source preflight could not be serialized"))?;
    let input_sha256 = hex::encode(Sha256::digest(&serialized));
    let namespace = format!("mw-mlt-{}", &input_sha256[..20]);
    let mut frame_cursor = 0;
    let mut seen_ids = HashSet::new();
    let mut seen_scenes = HashSet::new();
    let mut seen_jobs = HashSet::new();
    let mut seen_manifests = HashSet::new();
    let mut clips = Vec::with_capacity(checked.segments.len());
    for (index, segment) in checked.segments.iter().enumerate() {
        let prefix = segment
            .manifest_path
            .strip_suffix("/artifact-manifest.json")
            .ok_or_else(|| refuse("Native MLT source manifest has an invalid path"))?;
        let hashpart = prefix
            .strip_prefix("render-")
            .ok_or_else(|| refuse("Native MLT source is not an owner render artifact"))?;
        if hashpart.len() != 32
            || !hashpart
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || !sha256(&segment.manifest_sha256)
            || segment.frame_count == 0
            || segment.frame_count > MAX_FRAMES
            || segment.scene_ids.is_empty()
            || segment.segment_id.trim().is_empty()
            || segment.segment_id.len() > 256
            || segment.native_job_ref.trim().is_empty()
            || segment.native_job_ref.len() > 256
            || !seen_ids.insert(&segment.segment_id)
            || !seen_jobs.insert(&segment.native_job_ref)
            || !seen_manifests.insert(&segment.manifest_path)
            || segment
                .scene_ids
                .iter()
                .any(|scene| !seen_scenes.insert(*scene))
            || segment.output_start_frame != frame_cursor
        {
            return Err(refuse(
                "Native MLT clip order, scene identity, or manifest is invalid",
            ));
        }
        frame_cursor = frame_cursor
            .checked_add(segment.frame_count)
            .ok_or_else(|| refuse("Native MLT clip frame total overflow"))?;
        if frame_cursor > MAX_FRAMES {
            return Err(refuse(
                "Native MLT clip count exceeds bounded 36,000-frame budget",
            ));
        }
        clips.push(MltNativeSegmentEdit {
            segment_id: segment.segment_id.clone(),
            scene_ids: segment.scene_ids.clone(),
            source_native_job_ref: segment.native_job_ref.clone(),
            source_manifest_path: segment.manifest_path.clone(),
            source_manifest_sha256: segment.manifest_sha256.clone(),
            ffv1_output_path: format!("{namespace}-s{index:02}-ffv1.mkv"),
            ffv1_max_bytes: MEZZANINE_MAX_BYTES,
            edit_asset_name: format!("Motionwright verified native segment {:02}", index + 1),
            edit_clip_name: format!("Verified scene cut {:02}", index + 1),
            start_frame: segment.output_start_frame,
            source_in: 0,
            source_out_exclusive: segment.frame_count,
        });
    }
    if frame_cursor != checked.total_frames {
        return Err(refuse(
            "Native MLT source frame counts do not match output duration",
        ));
    }
    Ok(MltNativeVideoRecipe {
        project_resource: checked.project_resource.clone(),
        generation: checked.generation,
        revision: checked.revision,
        deliverable_id: checked.deliverable_id,
        input_sha256,
        owner_output_namespace: namespace.clone(),
        provider_profile: MltOwnedVideoProfile {
            width,
            height,
            fps_num: checked.fps_num,
            fps_den: checked.fps_den,
            progressive: true,
            sample_aspect_num: 1,
            sample_aspect_den: 1,
            display_aspect_num: 16,
            display_aspect_den: 9,
            colorspace: 709,
            audio_channels: 2,
        },
        sequence_name: "Motionwright verified multisegment cut".into(),
        video_track_name: "Motionwright native visual segments".into(),
        clips,
        total_frames: checked.total_frames,
        lossless_sequence_path: format!("{namespace}-sequence-ffv1.mkv"),
        lossless_sequence_profile: "lossless".into(),
        final_mux_profile: "h264-aac-mp4".into(),
        scope: "owner-source-bound-mlt-semantic-edit-recipe-not-rendered".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::assembly::SegmentForMlt;
    fn checked_fixture() -> MultiSegmentAssemblyPreflight {
        MultiSegmentAssemblyPreflight {
            project_resource: format!("project:{}", Uuid::new_v4()),
            generation: Uuid::new_v4(),
            revision: 15,
            deliverable_id: Uuid::new_v4(),
            mlt_profile: "h264-1080p".into(),
            fps_num: 30,
            fps_den: 1,
            total_frames: 990,
            segments: [(0, 960, "1".repeat(32)), (960, 30, "2".repeat(32))]
                .into_iter()
                .enumerate()
                .map(|(i, (start, count, directory))| SegmentForMlt {
                    segment_id: format!("native-segment-{i}"),
                    scene_ids: vec![Uuid::new_v4()],
                    output_start_frame: start,
                    frame_count: count,
                    manifest_path: format!("render-{directory}/artifact-manifest.json"),
                    manifest_sha256: "a".repeat(64),
                    native_job_ref: format!("verified-job-{i}"),
                })
                .collect(),
            evidence_scope: "manifest-digest-verified-not-composited-mp4".into(),
        }
    }
    #[test]
    fn source_bound_recipe_is_deterministic_executable_shape_and_nonmedia() {
        let fixture = checked_fixture();
        let first = mlt_native_video_recipe(&fixture).unwrap();
        let second = mlt_native_video_recipe(&fixture).unwrap();
        assert_eq!(first, second);
        assert_eq!(first.clips.len(), 2);
        assert_eq!(first.clips[0].start_frame, 0);
        assert_eq!(first.clips[0].source_out_exclusive, 960);
        assert_eq!(first.clips[1].start_frame, 960);
        assert_eq!(first.clips[1].source_out_exclusive, 30);
        assert_eq!(first.total_frames, 990);
        assert_eq!(first.lossless_sequence_profile, "lossless");
        assert_eq!(first.final_mux_profile, "h264-aac-mp4");
        assert_eq!(first.provider_profile.colorspace, 709);
        assert_eq!(first.provider_profile.audio_channels, 2);
        assert_ne!(
            first.clips[0].ffv1_output_path,
            first.clips[1].ffv1_output_path
        );
        let serialized = serde_json::to_value(&first).unwrap();
        assert!(serialized.get("master").is_none());
        assert!(serialized.get("artifact").is_none());
        assert_eq!(
            first.scope,
            "owner-source-bound-mlt-semantic-edit-recipe-not-rendered"
        );
    }
    #[test]
    fn rejects_tamper_overlap_missing_or_noncanonical_inputs() {
        let baseline = checked_fixture();
        let mut altered = baseline.clone();
        altered.segments.swap(0, 1);
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.segments[1].native_job_ref = altered.segments[0].native_job_ref.clone();
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.segments[1].scene_ids = altered.segments[0].scene_ids.clone();
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.segments[0].manifest_path = "../../tmp/arbitrary.json".into();
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.segments[0].manifest_sha256 = "INVALID".into();
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.mlt_profile = "other".into();
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.evidence_scope = "actual-h264-master".into();
        assert!(mlt_native_video_recipe(&altered).is_err());
        altered = baseline.clone();
        altered.total_frames += 1;
        assert!(mlt_native_video_recipe(&altered).is_err());
    }
    #[test]
    fn output_paths_are_hash_bound_to_input_digest_and_do_not_authorize_io() {
        let a = checked_fixture();
        let mut b = a.clone();
        b.segments[1].manifest_sha256 = "b".repeat(64);
        let plan_a = mlt_native_video_recipe(&a).unwrap();
        let plan_b = mlt_native_video_recipe(&b).unwrap();
        assert_ne!(plan_a.input_sha256, plan_b.input_sha256);
        assert_ne!(plan_a.lossless_sequence_path, plan_b.lossless_sequence_path);
        assert!(!plan_a.lossless_sequence_path.contains('/'));
        assert!(plan_a.lossless_sequence_path.ends_with(".mkv"));
    }
}
