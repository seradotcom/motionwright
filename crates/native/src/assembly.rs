//! Source-bound, read-only multi-segment MLT assembly preflight.
//! This validates native Film partition and already-produced manifest bytes;
//! no renderer is dispatched, and no finished MP4 is claimed.
use crate::film::{FilmBuildOptions, build_motion_canvas_segments};
use crate::production::{
    MotionCanvasRenderEvidence, ensure_native_motion_verification, verify_output_artifact,
};
use motionwright_domain::{
    AudioCodec, OutputColorSpace, OutputContainer, Project, RationalTime, RendererKind, VideoCodec,
};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{collections::HashSet, fs, path::Path};
use uuid::Uuid;

const MAX_SEGMENTS: usize = 64;
const MAX_TOTAL_FRAMES: u64 = 36_000;
const MAX_MANIFEST_BYTES: u64 = 8 * 1024 * 1024;
fn invalid(msg: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, msg)
}
fn unsupported(msg: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, msg)
}
fn stale(msg: impl Into<String>) -> Error {
    Error::new(ErrorCode::StaleReference, msg)
}
fn is_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn exact_frame(at: RationalTime, num: i64, den: i64) -> NativeResult<u64> {
    let n = i128::from(at.num)
        .checked_mul(i128::from(num))
        .ok_or_else(|| invalid("Frame numerator overflow"))?;
    let d = i128::from(at.den)
        .checked_mul(i128::from(den))
        .ok_or_else(|| invalid("Frame denominator overflow"))?;
    if d <= 0 || n < 0 || n % d != 0 {
        return Err(unsupported(
            "Native segment start does not align to an exact frame",
        ));
    }
    u64::try_from(n / d).map_err(|_| unsupported("Native segment frame offset is out of range"))
}
fn verify_manifest(
    bytes: &[u8],
    count: u64,
    width: u32,
    height: u32,
    fps_num: u32,
    fps_den: u32,
) -> NativeResult<()> {
    let value: Value =
        serde_json::from_slice(bytes).map_err(|_| invalid("Native frame manifest is not JSON"))?;
    let plan = value
        .get("plan")
        .ok_or_else(|| invalid("Native frame manifest is missing its plan"))?;
    let u = |key: &str| plan.get(key).and_then(Value::as_u64);
    if u("width") != Some(u64::from(width))
        || u("height") != Some(u64::from(height))
        || u("fps") != Some(u64::from(fps_num))
        || u("fps_denominator").unwrap_or(1) != u64::from(fps_den)
        || u("frame_count") != Some(count)
        || u("first_frame") != Some(0)
        || u("end_frame_exclusive") != Some(count)
    {
        return Err(stale(
            "Native frame manifest does not match the selected output frame plan",
        ));
    }
    let frames = value
        .get("frames")
        .and_then(Value::as_array)
        .ok_or_else(|| invalid("Native frame manifest has no frame index"))?;
    if frames.len() != count as usize {
        return Err(stale(
            "Native frame manifest count differs from its segment",
        ));
    }
    for (index, frame) in frames.iter().enumerate() {
        let file = format!("frames/{index:06}.png");
        if frame.get("index").and_then(Value::as_u64) != Some(index as u64)
            || frame.get("file").and_then(Value::as_str) != Some(file.as_str())
            || !frame
                .get("sha256")
                .and_then(Value::as_str)
                .is_some_and(is_sha)
            || !frame
                .get("bytes")
                .and_then(Value::as_u64)
                .is_some_and(|n| n > 8 && n <= 32 * 1024 * 1024)
        {
            return Err(invalid(
                "Native frame manifest has a malformed frame record",
            ));
        }
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SegmentForMlt {
    pub segment_id: String,
    pub scene_ids: Vec<Uuid>,
    pub output_start_frame: u64,
    pub frame_count: u64,
    pub manifest_path: String,
    pub manifest_sha256: String,
    pub native_job_ref: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MultiSegmentAssemblyPreflight {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub mlt_profile: String,
    pub fps_num: u32,
    pub fps_den: u32,
    pub total_frames: u64,
    pub segments: Vec<SegmentForMlt>,
    /// This result is not video or proof of a completed multi-segment mux.
    pub evidence_scope: String,
}

/// Plan exact MLT clip offsets from existing *versioned* Motion Canvas native
/// evidence; hash/check the actual manifests in the owner-provided output root.
/// The future MLT driver must independently re-hash every indexed PNG during
/// native frames.encode, before any output may be certified.
pub fn preflight_multi_segment_mlt(
    project: &Project,
    profile_id: Uuid,
    options: &FilmBuildOptions,
    evidence: &MotionCanvasRenderEvidence,
    owner_output_root: &Path,
) -> NativeResult<MultiSegmentAssemblyPreflight> {
    project
        .validate()
        .map_err(|e| invalid(format!("Invalid creative project: {e}")))?;
    let profile = project
        .deliverables
        .iter()
        .find(|p| p.id == profile_id)
        .ok_or_else(|| invalid("Deliverable profile does not exist"))?;
    let mlt_profile = match (profile.width, profile.height) {
        (1920, 1080) => "h264-1080p",
        (1280, 720) => "h264-720p",
        _ => {
            return Err(unsupported(
                "Pinned MLT semantic render supports only H.264 1080p and 720p; no silent aspect conversion",
            ));
        }
    };
    if profile.video_codec != VideoCodec::H264
        || profile.audio_codec != AudioCodec::Aac
        || profile.audio_sample_rate_hz != 48_000
        || profile.container != OutputContainer::Mp4
        || profile.color_space != OutputColorSpace::Rec709
    {
        return Err(unsupported(
            "Multi-segment MLT profile requires H.264/AAC 48 kHz Rec.709 MP4",
        ));
    }
    if profile.frame_rate.num != i64::from(options.frame_rate.num)
        || profile.frame_rate.den != i64::from(options.frame_rate.den)
        || options.frame_rate != evidence.frame_rate
    {
        return Err(stale(
            "Motion Canvas frame rate disagrees with saved delivery profile",
        ));
    }
    if evidence.project_resource != project.resource_key()
        || evidence.generation != project.generation
        || evidence.revision != project.revision
        || evidence.deliverable_id != profile_id
    {
        return Err(stale(
            "Native segment evidence belongs to a different creative source/profile",
        ));
    }
    let selected: Vec<_> = if profile.included_scene_ids.is_empty() {
        project.scenes.iter().collect()
    } else {
        profile
            .included_scene_ids
            .iter()
            .map(|id| {
                project
                    .scenes
                    .iter()
                    .find(|scene| scene.id == *id)
                    .ok_or_else(|| invalid("Custom cut references an unknown scene"))
            })
            .collect::<NativeResult<Vec<_>>>()?
    };
    if selected.is_empty()
        || selected
            .iter()
            .any(|scene| scene.renderer != RendererKind::MotionCanvas)
    {
        return Err(unsupported(
            "Mixed/missing renderer media cannot be silently mapped into an MLT Motion Canvas master",
        ));
    }
    let native = build_motion_canvas_segments(project, profile_id, options)?;
    if native.len() < 2 || native.len() > MAX_SEGMENTS || native.len() != evidence.segments.len() {
        return Err(unsupported(
            "Multi-segment MLT requires exactly 2–64 real canonical Motion Canvas segments",
        ));
    }
    let mut cursor = 0u64;
    let mut seen_ids = HashSet::new();
    let mut seen_jobs = HashSet::new();
    let mut segments = Vec::with_capacity(native.len());
    for (expected, actual) in native.iter().zip(&evidence.segments) {
        if actual.segment_id != expected.id
            || actual.scene_ids != expected.scene_ids
            || actual.frame_count != expected.frame_count
            || actual.job_ref.is_empty()
            || actual.plan_ref.is_empty()
            || actual.fingerprint.is_empty()
            || !seen_ids.insert(&actual.segment_id)
            || !seen_jobs.insert(&actual.job_ref)
        {
            return Err(stale(
                "Native render segments differ from the canonical versioned Film cut",
            ));
        }
        ensure_native_motion_verification(&actual.verification)?;
        let start = exact_frame(
            expected.global_start,
            i64::from(options.frame_rate.num),
            i64::from(options.frame_rate.den),
        )?;
        if start != cursor || expected.frame_count == 0 || expected.frame_count > MAX_TOTAL_FRAMES {
            return Err(unsupported(
                "Native segments contain a frame gap, overlap, or invalid duration",
            ));
        }
        cursor = cursor
            .checked_add(expected.frame_count)
            .ok_or_else(|| unsupported("Total native frame count overflow"))?;
        if cursor > MAX_TOTAL_FRAMES {
            return Err(unsupported(
                "Pinned MLT sequence frame budget exceeds 36,000 frames",
            ));
        }
        let path = actual
            .artifact
            .get("manifest")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("Native artifact omitted its frame manifest"))?;
        let sha = actual
            .artifact
            .get("manifest_sha256")
            .and_then(Value::as_str)
            .filter(|sha| is_sha(sha))
            .ok_or_else(|| invalid("Native artifact manifest digest is missing or malformed"))?;
        let directory = actual
            .artifact
            .get("directory")
            .and_then(Value::as_str)
            .ok_or_else(|| invalid("Native artifact frame directory is missing"))?;
        if directory.len() != 39
            || !directory.starts_with("render-")
            || !directory.as_bytes()[7..].iter().all(u8::is_ascii_hexdigit)
            || path != format!("{directory}/artifact-manifest.json")
            || actual.artifact.get("frame_count").and_then(Value::as_u64)
                != Some(expected.frame_count)
        {
            return Err(stale(
                "Native artifact lacks a canonical source-bound frame directory",
            ));
        }
        let checked = verify_output_artifact(owner_output_root, path, sha, MAX_MANIFEST_BYTES)?;
        let bytes =
            fs::read(checked).map_err(|_| stale("Native frame manifest became inaccessible"))?;
        verify_manifest(
            &bytes,
            expected.frame_count,
            profile.width,
            profile.height,
            options.frame_rate.num,
            options.frame_rate.den,
        )?;
        segments.push(SegmentForMlt {
            segment_id: actual.segment_id.clone(),
            scene_ids: actual.scene_ids.clone(),
            output_start_frame: start,
            frame_count: expected.frame_count,
            manifest_path: path.into(),
            manifest_sha256: sha.into(),
            native_job_ref: actual.job_ref.clone(),
        });
    }
    Ok(MultiSegmentAssemblyPreflight {
        project_resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
        deliverable_id: profile_id,
        mlt_profile: mlt_profile.into(),
        fps_num: options.frame_rate.num,
        fps_den: options.frame_rate.den,
        total_frames: cursor,
        segments,
        evidence_scope: "manifest-digest-verified-not-composited-mp4".into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::film::{MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent};
    use crate::production::MotionCanvasSegmentEvidence;
    use motionwright_domain::{BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle};
    use semwright_media_time::Rate;
    use semwright_motion_authoring::{Archetype, NarrativeRole};
    use serde_json::json;
    use sha2::{Digest, Sha256};
    use std::{collections::BTreeSet, fs};
    use tempfile::TempDir;

    fn seeded() -> (
        Project,
        FilmBuildOptions,
        MotionCanvasRenderEvidence,
        TempDir,
    ) {
        let mut project = Project::new("Full-native 33-scene cut").unwrap();
        for number in 0..33 {
            project
                .apply_change(&Change::AddScene {
                    name: format!("Canonical scene {number}"),
                    objective: "Source-bound compositor coverage".into(),
                    duration_seconds: 1,
                })
                .unwrap();
            let scene_id = project.scenes.last().unwrap().id;
            project
                .apply_change(&Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::now_v7(),
                        name: "Native rectangle".into(),
                        kind: "rectangle".into(),
                        parent_id: None,
                        x: 400.0,
                        y: 250.0,
                        width: 160.0,
                        height: 120.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: None,
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#F5F5F2".into()),
                            stroke: None,
                            stroke_width: 0.0,
                            font_family: None,
                            font_size: None,
                            font_weight: None,
                            line_height: None,
                            blend_mode: BlendMode::Normal,
                        },
                        relations: vec![],
                        property_locks: BTreeSet::new(),
                        keyframes: vec![],
                    },
                })
                .unwrap();
        }
        let profile_id = project.deliverables[0].id;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: MOTION_CANVAS_FONT_FAMILY.into(),
            mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| SceneFilmIntent {
                    scene_id: scene.id,
                    role: NarrativeRole::Mechanism,
                    archetype: Archetype::Statement,
                })
                .collect(),
        };
        let segments = build_motion_canvas_segments(&project, profile_id, &options).unwrap();
        assert_eq!(segments.len(), 2);
        let root = tempfile::tempdir().unwrap();
        let mut sources = Vec::new();
        for (index, segment) in segments.iter().enumerate() {
            let dir = format!("render-{index:032x}");
            let folder = root.path().join(&dir);
            fs::create_dir_all(&folder).unwrap();
            let manifest = json!({
                "plan": {
                    "width": 1920,
                    "height": 1080,
                    "fps": 30,
                    "fps_denominator": 1,
                    "frame_count": segment.frame_count,
                    "first_frame": 0,
                    "end_frame_exclusive": segment.frame_count,
                },
                "frames": (0..segment.frame_count).map(|frame| json!({
                    "index": frame,
                    "file": format!("frames/{frame:06}.png"),
                    "bytes": 128,
                    "sha256": "a".repeat(64),
                })).collect::<Vec<_>>(),
            });
            let bytes = serde_json::to_vec(&manifest).unwrap();
            let sha = hex::encode(Sha256::digest(&bytes));
            fs::write(folder.join("artifact-manifest.json"), bytes).unwrap();
            sources.push(MotionCanvasSegmentEvidence {
                segment_id: segment.id.clone(),
                scene_ids: segment.scene_ids.clone(),
                frame_count: segment.frame_count,
                plan_ref: format!("native-source-plan-{index}"),
                fingerprint: format!("native-fingerprint-{index}"),
                job_ref: format!("trusted-render-job-{index}"),
                artifact: json!({
                    "directory": dir,
                    "manifest": format!("{dir}/artifact-manifest.json"),
                    "manifest_sha256": sha,
                    "frame_count": segment.frame_count
                }),
                verification: json!({
                    "report": {"execution_status": "completed", "support_level": "native"},
                    "measurement": {"findings": [], "validation": {"checks": [{"verdict": "PASS"}]}}
                }),
            });
        }
        let evidence = MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id: profile_id,
            frame_rate: options.frame_rate,
            segments: sources,
        };
        (project, options, evidence, root)
    }

    #[test]
    fn partitions_33_native_scenes_into_two_exact_mlt_cuts() {
        let (project, options, evidence, root) = seeded();
        let plan = preflight_multi_segment_mlt(
            &project,
            project.deliverables[0].id,
            &options,
            &evidence,
            root.path(),
        )
        .unwrap();
        assert_eq!(plan.mlt_profile, "h264-1080p");
        assert_eq!(plan.segments.len(), 2);
        assert_eq!(plan.segments[0].frame_count, 960);
        assert_eq!(plan.segments[0].output_start_frame, 0);
        assert_eq!(plan.segments[1].frame_count, 30);
        assert_eq!(plan.segments[1].output_start_frame, 960);
        assert_eq!(plan.total_frames, 990);
        assert_eq!(
            plan.segments
                .iter()
                .map(|s| s.scene_ids.len())
                .sum::<usize>(),
            33
        );
        assert_eq!(
            plan.evidence_scope,
            "manifest-digest-verified-not-composited-mp4"
        );
        let value = serde_json::to_value(&plan).unwrap();
        assert!(value.get("master").is_none());
        assert!(value.get("finished_video").is_none());
    }

    #[test]
    fn rejects_stale_forged_and_mixed_renderer_inputs() {
        let (project, options, evidence, root) = seeded();
        let check = |source: &Project, render: &MotionCanvasRenderEvidence| {
            preflight_multi_segment_mlt(
                source,
                source.deliverables[0].id,
                &options,
                render,
                root.path(),
            )
        };
        let mut older = evidence.clone();
        older.revision += 1;
        assert_eq!(
            check(&project, &older).unwrap_err().code,
            ErrorCode::StaleReference
        );
        let mut reordered = evidence.clone();
        reordered.segments.swap(0, 1);
        assert_eq!(
            check(&project, &reordered).unwrap_err().code,
            ErrorCode::StaleReference
        );
        let mut wrong_job = evidence.clone();
        wrong_job.segments[1].job_ref = wrong_job.segments[0].job_ref.clone();
        assert_eq!(
            check(&project, &wrong_job).unwrap_err().code,
            ErrorCode::StaleReference
        );
        let mut fake_native = evidence.clone();
        fake_native.segments[0].verification["measurement"]["validation"]["checks"] =
            json!([{"verdict":"FAIL"}]);
        assert!(check(&project, &fake_native).is_err());

        let path = root
            .path()
            .join(evidence.segments[0].artifact["manifest"].as_str().unwrap());
        fs::write(&path, b"{\"tampered\":true}").unwrap();
        assert_eq!(
            check(&project, &evidence).unwrap_err().code,
            ErrorCode::StaleReference
        );

        let mut foreign = project.clone();
        foreign.scenes[32].renderer = RendererKind::Blender;
        assert_eq!(
            check(&foreign, &evidence).unwrap_err().code,
            ErrorCode::Unsupported
        );
        let mut portrait = project.clone();
        portrait.deliverables[0].width = 1080;
        portrait.deliverables[0].height = 1920;
        assert_eq!(
            check(&portrait, &evidence).unwrap_err().code,
            ErrorCode::Unsupported
        );
    }

    #[test]
    fn no_single_segment_claim_or_fake_mix_of_missing_media() {
        let (project, options, mut evidence, root) = seeded();
        evidence.segments.pop();
        assert_eq!(
            preflight_multi_segment_mlt(
                &project,
                project.deliverables[0].id,
                &options,
                &evidence,
                root.path()
            )
            .unwrap_err()
            .code,
            ErrorCode::Unsupported
        );
    }
}
