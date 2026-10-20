//! Source-only native-camera inspection and explicit CAS editing fixture.
//!
//! This program never starts Manim or launches an existing/uncertain Job.
//! A caller must supply an existing DB, a full RevisionStamp and IDs from the
//! saved Project. Each process is a fresh StudioService observation. Native
//! rendered media, source reopen, and rights require separate verification.
use motionwright_domain::{Project, RevisionStamp};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2,
    planar_camera::{
        CAMERA_SOURCE_MIME_V1, ManimCameraScenePlanV1, camera_source_bundle_v1,
        inspect_camera_source_bundle_v1,
    },
};
use motionwright_native::manim_camera_projection::{
    author_one_camera_endpoint_edit, export_saved_manim_camera_source_bundle,
    inspect_saved_manim_camera_source, prepare_saved_manim_camera_program_source_only,
    prepare_saved_manim_camera_source, preview_saved_manim_camera_endpoint_edit,
    verify_rendered_manim_camera_source,
};
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
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

fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn text_at<'a>(value: &'a serde_json::Value, pointer: &str) -> Result<&'a str> {
    value
        .pointer(pointer)
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("Missing source-cycle field: {pointer}").into())
}

fn attached_source_bytes(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
    expected_sha: &str,
) -> Result<Vec<u8>> {
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .ok_or("Original source asset is no longer attached")?;
    if asset.media_type != CAMERA_SOURCE_MIME_V1
        || asset.content_sha256.as_deref() != Some(expected_sha)
    {
        return Err("Original source asset identity changed".into());
    }
    let handle = service.store();
    let bytes = handle
        .lock()
        .read_blob(expected_sha, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)
        .map_err(|error| format!("Source CAS read failed: {error:?}"))?;
    if digest(&bytes) != expected_sha {
        return Err("Original CAS source digest changed".into());
    }
    Ok(bytes)
}

fn attach_source_bytes(
    service: &StudioService,
    project: &Project,
    bytes: &[u8],
    directory: &Path,
    name: &str,
    request_id: &str,
) -> Result<(Project, Uuid)> {
    let staging = directory.join(format!("source-import-{}.mwcam.zip", Uuid::new_v4()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&staging)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    drop(file);
    if checked_existing(&staging, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)?.as_slice() != bytes {
        return Err("Source import staging readback differs".into());
    }
    let updated = service
        .import_asset_file(
            project.id,
            &RevisionStamp::from(project),
            request_id,
            &staging,
            name.into(),
            CAMERA_SOURCE_MIME_V1.into(),
        )?
        .project;
    // Only this exclusively created copy is removed; the native original remains.
    fs::remove_file(staging)?;
    let source_sha = digest(bytes);
    let added: Vec<_> = updated
        .assets
        .iter()
        .filter(|asset| !project.assets.iter().any(|old| old.id == asset.id))
        .collect();
    if added.len() != 1
        || added[0].content_sha256.as_deref() != Some(source_sha.as_str())
        || added[0].media_type != CAMERA_SOURCE_MIME_V1
    {
        return Err("Import did not attach exactly the observed source bytes".into());
    }
    let asset_id = added[0].id;
    Ok((updated, asset_id))
}

fn observed_native_source(
    service: &StudioService,
    project: &Project,
    plan: &ManimCameraScenePlanV1,
    native_path: &Path,
    output_root: &Path,
) -> Result<(Vec<u8>, serde_json::Value)> {
    let raw = checked_existing(native_path, 4 * 1024 * 1024)?;
    let native: serde_json::Value = serde_json::from_slice(&raw)?;
    if native["native_manim_camera_dispatch"] != "PASS"
        || !native["receipt_read_error"].is_null()
        || !native["custody_error"].is_null()
        || native["project_id"] != json!(project.id)
        || native["generation"] != json!(project.generation)
        || native["revision"] != json!(project.revision)
        || native["scene_id"] != json!(plan.binding.scene_id)
        || native["deliverable_id"] != json!(plan.binding.deliverable_id)
    {
        return Err("Native original is not the current saved camera result".into());
    }
    let request = text_at(&native, "/request_id")?;
    for (field, suffix, command) in [
        (
            "original_start_receipt",
            ":start",
            "driver.manim-community.camera.render.start",
        ),
        (
            "original_result_receipt",
            ":result",
            "driver.manim-community.render.result",
        ),
    ] {
        let receipt = service
            .latest_production_receipt(project.id, &format!("{request}{suffix}"))?
            .ok_or("Native original receipt is not durable")?;
        if receipt.command != command
            || receipt.stage != "completed"
            || receipt.generation != project.generation
            || receipt.revision != project.revision
            || native[field] != serde_json::to_value(&receipt)?
        {
            return Err("Native original differs from its saved receipt".into());
        }
    }
    let result = &native["result"];
    let original_terminal = &native["original_result_receipt"];
    // execute() returns {replayed, result, receipt}; provider data lives inside
    // that result. Bind this source to the actual durable completion payload.
    if result["native_driver"] != "driver:manim-community"
        || result["renderer"] != "manim-community"
        || result["project_revision"] != json!(project.revision)
        || result["scene_id"] != json!(plan.binding.scene_id)
        || result["result"]["replayed"] != false
        || result["result"]["receipt"] != *original_terminal
        || result["result"]["result"] != original_terminal["payload"]["result"]
    {
        return Err("Native source is not linked to its durable terminal result".into());
    }
    let data = original_terminal
        .pointer("/payload/result/data")
        .ok_or("Durable native terminal result has no provider data")?;
    let artifact = data
        .get("artifact")
        .ok_or("Native terminal artifact is missing")?;
    let job_ref = text_at(result, "/job_ref")?;
    let token = job_ref
        .strip_prefix("manim-")
        .ok_or("Native source job reference is outside the camera profile")?;
    if token.len() != 32
        || !token
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || data["state"] != "succeeded"
        || data["job_ref"] != job_ref
        || original_terminal["payload"]["job_ref"] != job_ref
        || result["artifact"]["relative_path"] != artifact["relative_path"]
        || text_at(artifact, "/relative_path")?
            != format!(
                "videos/mw_{token}/{}/mw_{token}.mp4",
                plan.render_profile.quality_directory()
            )
        || !result["artifact"]["camera_source_custody_error"].is_null()
        || !data["source_custody_error"].is_null()
    {
        return Err("Native source custody is incomplete; preserve the original result".into());
    }
    let verified = verify_rendered_manim_camera_source(
        output_root,
        text_at(artifact, "/relative_path")?,
        plan,
        artifact,
    )?;
    if result["artifact"]["camera_source"] != verified
        || result["plan_sha256"] != verified["closure"]["plan_sha256"]
        || artifact["plan_sha256"] != result["plan_sha256"]
    {
        return Err("The common native consumer did not verify this source carrier".into());
    }
    let path = absolute_path(text_at(&verified, "/verified_path")?)?;
    let bytes = checked_existing(&path, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)?;
    if Some(bytes.len() as u64) != verified["bytes"].as_u64()
        || digest(&bytes) != text_at(&verified, "/sha256")?
    {
        return Err("The original native source bytes changed before import".into());
    }
    Ok((
        bytes,
        json!({
            "native_evidence_sha256": digest(&raw),
            "request_id": request,
            "camera_source": verified,
            "original_start_receipt": native["original_start_receipt"],
            "original_result_receipt": native["original_result_receipt"],
        }),
    ))
}

fn original_receipts_unchanged(
    service: &StudioService,
    project_id: Uuid,
    link: &serde_json::Value,
) -> Result<()> {
    let request = text_at(link, "/request_id")?;
    for (field, suffix) in [
        ("original_start_receipt", ":start"),
        ("original_result_receipt", ":result"),
    ] {
        let receipt = service
            .latest_production_receipt(project_id, &format!("{request}{suffix}"))?
            .ok_or("Original native receipt disappeared during source-only work")?;
        if serde_json::to_value(receipt)? != link[field] {
            return Err("Original native receipt changed during source-only work".into());
        }
    }
    Ok(())
}

fn source_cycle(args: &[String]) -> Result<()> {
    if args.len() != 10 {
        return Err("usage: source-cycle-edit DB STAMP_JSON PROJECT SCENE DELIVERABLE OUT_JSON ORIGINAL_NATIVE_JSON OUTPUT_ROOT REQUEST_ID | source-cycle-readback DB STAMP_JSON PROJECT SCENE DELIVERABLE OUT_JSON EDIT_RECEIPT_JSON EDITED_NATIVE_JSON OUTPUT_ROOT".into());
    }
    let editing = args[0] == "source-cycle-edit";
    let database = absolute_path(&args[1])?;
    let metadata = fs::symlink_metadata(&database)?;
    if database.extension().and_then(|x| x.to_str()) != Some("sqlite3")
        || !metadata.is_file()
        || metadata.file_type().is_symlink()
    {
        return Err("Source cycle requires an existing regular SQLite database".into());
    }
    let expected: RevisionStamp =
        serde_json::from_slice(&checked_existing(&absolute_path(&args[2])?, 4096)?)?;
    let project_id = Uuid::parse_str(&args[3])?;
    let scene_id = Uuid::parse_str(&args[4])?;
    let deliverable_id = Uuid::parse_str(&args[5])?;
    let out = absolute_path(&args[6])?;
    if out.try_exists()? || !out.parent().is_some_and(Path::is_dir) {
        return Err("Preserve the previous source receipt; use a new owned destination".into());
    }
    let service = StudioService::open(&database)?;
    let project = service.project(project_id)?;
    if RevisionStamp::from(&project) != expected {
        return Err("Source-cycle full stamp is stale".into());
    }
    let prepared = prepare_saved_manim_camera_source(
        &service,
        project_id,
        &expected,
        scene_id,
        deliverable_id,
    )?;
    let receipts_before = serde_json::to_value(service.production_receipts(project_id, 256)?)?;
    let mut report;

    if editing {
        let request = &args[9];
        if request.is_empty() || request.len() > 64 || request.chars().any(char::is_control) {
            return Err("Source-cycle request identity is empty or outside fixture bounds".into());
        }
        let (original_bytes, native_link) = observed_native_source(
            &service,
            &project,
            &prepared.source,
            &absolute_path(&args[7])?,
            &absolute_path(&args[8])?,
        )?;
        let original = inspect_camera_source_bundle_v1(&original_bytes)
            .map_err(|error| format!("Original source inspection failed: {error:?}"))?;
        if original.plan != prepared.source
            || original.plan.reframe.start_center_x != 960.0
            || original.plan.reframe.end_center_x != 1000.0
        {
            return Err("Source cycle requires the original 960 to 1000 camera".into());
        }
        let mut edited_plan = original.plan.clone();
        edited_plan.reframe.end_center_x = 1040.0;
        edited_plan.camera.center_x = 1040.0;
        let edited_bytes = camera_source_bundle_v1(&edited_plan)
            .map_err(|error| format!("Edited source compilation failed: {error:?}"))?;
        // This new intent blocks accidental replay even if a later source step fails.
        new_receipt(
            &out.with_extension("intent.json"),
            &json!({
                "schema": "motionwright.manim.camera.source-cycle-intent/1",
                "request_id": request, "project_id": project_id,
                "canonical_stamp": expected, "original_native": native_link,
                "original_source_sha256": digest(&original_bytes),
                "edited_source_sha256": digest(&edited_bytes),
                "automatic_retry": false, "provider_dispatch_performed": false,
            }),
        )?;
        let (attached, original_id) = attach_source_bytes(
            &service,
            &project,
            &original_bytes,
            out.parent().unwrap(),
            "Original native camera source",
            &format!("{request}:attach-original"),
        )?;
        let (attached, edited_id) = attach_source_bytes(
            &service,
            &attached,
            &edited_bytes,
            out.parent().unwrap(),
            "Edited camera endpoint 1040",
            &format!("{request}:attach-edited"),
        )?;
        let attached_stamp = RevisionStamp::from(&attached);
        drop(service);
        let reopened = StudioService::open(&database)?;
        if reopened.project(project_id)? != attached {
            return Err("Attached source state did not survive SQLite reopen".into());
        }
        let inspected_original =
            inspect_saved_manim_camera_source(&reopened, project_id, &attached_stamp, original_id)?;
        let preview = preview_saved_manim_camera_endpoint_edit(
            &reopened,
            project_id,
            &attached_stamp,
            original_id,
            edited_id,
        )?;
        if inspected_original.source_sha256 != digest(&original_bytes)
            || preview.original_source_sha256 != digest(&original_bytes)
            || preview.edited_source_sha256 != digest(&edited_bytes)
            || preview.expected != attached_stamp
            || preview.native_execution_performed
            || preview.imported_source_authority_used
            || reopened.project(project_id)? != attached
        {
            return Err("CAS source preview is not the observed immutable source pair".into());
        }
        let updated = reopened
            .apply(
                project_id,
                &preview.expected,
                &format!("{request}:apply1040"),
                &preview.change,
            )?
            .project;
        if updated.id != attached.id
            || updated.generation != attached.generation
            || updated.revision
                != attached
                    .revision
                    .checked_add(1)
                    .ok_or("Revision overflow")?
            || updated.assets != attached.assets
            || updated.deliverables != attached.deliverables
            || updated.visual_language != attached.visual_language
            || updated.scenes.len() != attached.scenes.len()
            || updated
                .scenes
                .iter()
                .zip(&attached.scenes)
                .any(|(after, before)| after.id != before.id || after.nodes != before.nodes)
        {
            return Err("Endpoint edit changed source identities or non-camera content".into());
        }
        drop(reopened);
        let reopened = StudioService::open(&database)?;
        if reopened.project(project_id)? != updated {
            return Err("Applied source did not survive its fresh SQLite reopen".into());
        }
        let stamp = RevisionStamp::from(&updated);
        let current = inspect_saved_manim_camera_source(&reopened, project_id, &stamp, edited_id)?;
        let exported = export_saved_manim_camera_source_bundle(
            &reopened,
            project_id,
            &stamp,
            scene_id,
            deliverable_id,
        )?;
        let next = inspect_camera_source_bundle_v1(&exported)
            .map_err(|error| format!("Reopened source inspection failed: {error:?}"))?;
        if current.current_plan != next.plan
            || current.current_closure != next.closure
            || current.current_plan.reframe.end_center_x != 1040.0
            || attached_source_bytes(&reopened, &updated, original_id, &digest(&original_bytes))?
                != original_bytes
            || serde_json::to_value(reopened.production_receipts(project_id, 256)?)?
                != receipts_before
        {
            return Err("Reopened source, historical CAS bytes or native ledger differ".into());
        }
        let program = prepare_saved_manim_camera_program_source_only(
            &reopened,
            project_id,
            &stamp,
            scene_id,
            deliverable_id,
        )?;
        original_receipts_unchanged(&reopened, project_id, &native_link)?;
        report = json!({
            "phase": "source-cycle-edit",
            "original_stamp": expected, "attached_stamp": attached_stamp,
            "canonical_stamp": stamp, "original_native": native_link,
            "source_project_sha256": digest(&serde_json::to_vec(&updated)?),
            "original_source_asset_id": original_id, "edited_source_asset_id": edited_id,
            "original_source_sha256": digest(&original_bytes),
            "edited_source_sha256": digest(&edited_bytes),
            "current_export_bundle_sha256": digest(&exported),
            "original_source_inspection": inspected_original,
            "preview": preview, "current_source_inspection": current,
            "prepared_program": program,
            "source_changed_by_example": true,
            "import_staging_files_removed": true,
        });
    } else {
        let edit_raw = checked_existing(&absolute_path(&args[7])?, 1024 * 1024)?;
        let edit: serde_json::Value = serde_json::from_slice(&edit_raw)?;
        if edit["schema"] != "motionwright.manim.camera.source-cycle/1"
            || edit["phase"] != "source-cycle-edit"
            || edit["project_id"] != json!(project_id)
            || edit["scene_id"] != json!(scene_id)
            || edit["deliverable_id"] != json!(deliverable_id)
            || edit["canonical_stamp"] != json!(expected)
            || edit["source_project_sha256"] != json!(digest(&serde_json::to_vec(&project)?))
        {
            return Err("Post-render source differs from the saved edit receipt".into());
        }
        let original_id = Uuid::parse_str(text_at(&edit, "/original_source_asset_id")?)?;
        let edited_id = Uuid::parse_str(text_at(&edit, "/edited_source_asset_id")?)?;
        let original_bytes = attached_source_bytes(
            &service,
            &project,
            original_id,
            text_at(&edit, "/original_source_sha256")?,
        )?;
        let edited_bytes = attached_source_bytes(
            &service,
            &project,
            edited_id,
            text_at(&edit, "/edited_source_sha256")?,
        )?;
        let current =
            inspect_saved_manim_camera_source(&service, project_id, &expected, edited_id)?;
        let (native_bytes, native_link) = observed_native_source(
            &service,
            &project,
            &prepared.source,
            &absolute_path(&args[8])?,
            &absolute_path(&args[9])?,
        )?;
        let exported = export_saved_manim_camera_source_bundle(
            &service,
            project_id,
            &expected,
            scene_id,
            deliverable_id,
        )?;
        let native_package = inspect_camera_source_bundle_v1(&native_bytes)
            .map_err(|error| format!("Edited native carrier inspection failed: {error:?}"))?;
        if native_link["request_id"] == edit["original_native"]["request_id"]
            || native_bytes != exported
            || current.current_plan != native_package.plan
            || current.current_closure != native_package.closure
            || current.current_plan.reframe.end_center_x != 1040.0
            || service.project(project_id)? != project
            || serde_json::to_value(service.production_receipts(project_id, 256)?)?
                != receipts_before
        {
            return Err("Saved editable source differs from the second native carrier".into());
        }
        original_receipts_unchanged(&service, project_id, &edit["original_native"])?;
        original_receipts_unchanged(&service, project_id, &native_link)?;
        report = json!({
            "phase": "source-cycle-readback", "canonical_stamp": expected,
            "edit_receipt_sha256": digest(&edit_raw), "edited_native": native_link,
            "source_project_sha256": digest(&serde_json::to_vec(&project)?),
            "original_source_asset_id": original_id, "edited_source_asset_id": edited_id,
            "original_source_sha256": digest(&original_bytes),
            "edited_source_sha256": digest(&edited_bytes),
            "current_export_bundle_sha256": digest(&exported),
            "current_source_inspection": current,
            "saved_export_matches_native_carrier": true,
            "source_changed_by_example": false,
        });
    }
    report["schema"] = json!("motionwright.manim.camera.source-cycle/1");
    report["project_id"] = json!(project_id);
    report["scene_id"] = json!(scene_id);
    report["deliverable_id"] = json!(deliverable_id);
    report["fresh_sqlite_readback"] = json!(true);
    report["original_native_receipts_unchanged"] = json!(true);
    report["production_receipt_window_unchanged"] = json!(true);
    report["production_receipt_window_limit"] = json!(256);
    report["native_execution_performed"] = json!(false);
    report["native_compiler_registered"] = json!(false);
    report["provider_dispatch_performed"] = json!(false);
    report["new_render_job_created"] = json!(false);
    report["imported_source_authority_used"] = json!(false);
    report["fresh_registered_source_reopen_verified"] = json!(false);
    report["native_video_frame_content_verified"] = json!(false);
    report["human_acceptance"] = json!(false);
    new_receipt(&out, &report)?;
    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}

fn main() -> Result<()> {
    let args: Vec<String> = env::args().skip(1).collect();
    if matches!(
        args.first().map(String::as_str),
        Some("source-cycle-edit" | "source-cycle-readback")
    ) {
        return source_cycle(&args);
    }
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
