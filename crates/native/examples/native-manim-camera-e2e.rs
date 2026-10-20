//! Source-only, explicit-authority native-camera inspection fixture.
//!
//! This program never starts Manim or launches an existing/uncertain Job.
//! A caller must supply an existing DB, a full RevisionStamp and IDs from the
//! saved Project. Each process is a fresh StudioService observation. Native
//! rendered media, real source reopen and rights are separate A1/A2/A6 gates.
use motionwright_domain::RevisionStamp;
use motionwright_native::manim_camera_projection::{
    author_one_camera_endpoint_edit, prepare_saved_manim_camera_source,
};
use motionwright_service::StudioService;
use serde_json::json;
use std::{
    env,
    error::Error,
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;

type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn checked_existing(path: &Path, maximum: u64) -> Result<Vec<u8>> {
    if !path.is_absolute() {
        return Err("Source input must be an absolute caller-owned path".into());
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file()
        || metadata.file_type().is_symlink()
        || metadata.len() == 0
        || metadata.len() > maximum
    {
        return Err("Source input is not a bounded regular file".into());
    }
    let before = metadata.len();
    let mut file = fs::File::open(path)?;
    let mut data = Vec::new();
    std::io::Read::take(&mut file, maximum + 1).read_to_end(&mut data)?;
    if data.len() as u64 != before || fs::symlink_metadata(path)?.len() != before {
        return Err("Source input changed during bounded read".into());
    }
    Ok(data)
}

fn new_receipt(path: &Path, value: &serde_json::Value) -> Result<()> {
    if !path.is_absolute() || !path.parent().is_some_and(Path::is_dir) || path.try_exists()? {
        return Err(
            "Receipt must target a new absolute file within an existing owner directory".into(),
        );
    }
    let bytes = serde_json::to_vec_pretty(value)?;
    if bytes.len() > 1024 * 1024 {
        return Err("Native camera source-only receipt too large".into());
    }
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&bytes)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    if checked_existing(path, 1024 * 1024)? != [bytes, b"\n".to_vec()].concat() {
        return Err("Source-only receipt failed physical readback".into());
    }
    Ok(())
}

fn absolute_path(s: &str) -> Result<PathBuf> {
    let p = PathBuf::from(s);
    if !p.is_absolute()
        || p.components()
            .any(|c| matches!(c, std::path::Component::ParentDir))
    {
        return Err("All file paths must be absolute and nontraversing".into());
    }
    Ok(p)
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.len() != 8 {
        return Err("usage: native-manim-camera-e2e DB_ABSOLUTE STAMP_JSON_ABSOLUTE PROJECT_UUID SCENE_UUID DELIVERABLE_UUID PHASE OUTPUT_JSON_ABSOLUTE EDIT_END_X_OR_NONE".into());
    }
    let database = absolute_path(&args[0])?;
    if database.extension().and_then(|x| x.to_str()) != Some("sqlite3") {
        return Err("Use an existing owned source .sqlite3 database".into());
    }
    let stamp_path = absolute_path(&args[1])?;
    let project_id = Uuid::parse_str(&args[2])?;
    let scene_id = Uuid::parse_str(&args[3])?;
    let deliverable_id = Uuid::parse_str(&args[4])?;
    let phase = args[5].as_str();
    if !["original", "reopened", "edited", "edited_reopened"].contains(&phase) {
        return Err("Phase not within the closed four-stage source protocol".into());
    }
    let out = absolute_path(&args[6])?;
    if out.try_exists()? {
        return Err("Output already exists; preserve original receipt".into());
    }
    let expected: RevisionStamp = serde_json::from_slice(&checked_existing(&stamp_path, 4096)?)?;
    let database_metadata = fs::symlink_metadata(&database)?;
    if !database_metadata.is_file() || database_metadata.file_type().is_symlink() {
        return Err("Existing source database must be regular, not a symlink".into());
    }
    let service = StudioService::open(&database)?;
    let original = service.project(project_id)?;
    if RevisionStamp::from(&original) != expected {
        return Err("Source database no longer has the expected revision/generation".into());
    }
    let prepared = prepare_saved_manim_camera_source(
        &service,
        project_id,
        &expected,
        scene_id,
        deliverable_id,
    )?;
    if original != service.project(project_id)? {
        return Err("Source changed during observation".into());
    }
    let proposed_change = if args[7] == "none" {
        serde_json::Value::Null
    } else {
        let x: f64 = args[7].parse()?;
        let change = author_one_camera_endpoint_edit(&original, &expected, scene_id, x)?;
        serde_json::to_value(change)?
    };
    new_receipt(
        &out,
        &json!({
            "schema": "motionwright.manim.camera.source-example/1",
            "phase": phase,
            "project": project_id,
            "canonical_stamp": expected,
            "prepared": prepared,
            "one_unapplied_canonical_change_proposal": proposed_change,
            "source_database_readback_checked": true,
            "source_changed_by_example": false,
            "native_compiler_registered": false,
            "provider_dispatch_performed": false,
            "new_render_job_created": false,
            "fresh_registered_source_reopen_verified": false,
            "native_video_frame_content_verified": false,
            "human_acceptance": false,
            "losses": [
                "native_plan_only_not_a_native_render_or_bundle_reopen_receipt",
                "camera_source_requires_original_authorized_compiler_and_host",
                "scene_contribution_does_not_certify_voice_or_captions"
            ]
        }),
    )?;
    Ok(())
}
