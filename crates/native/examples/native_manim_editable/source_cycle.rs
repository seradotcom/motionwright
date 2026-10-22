//! Source-only edit/import/readback for the existing native Manim fixture.
//! The caller owns native dispatch; this module cannot start or retry a job.
use super::*;
use motionwright_domain::Project;
use motionwright_manim_profile::{SOURCE_BUNDLE_MIME_V2, editable_scene as profile};
use motionwright_native::manim_editable_projection::{
    EditableProperty, export_saved_manim_editable_source_bundle,
    inspect_saved_manim_editable_source, prepare_saved_manim_editable_source,
    preview_saved_manim_editable_edit, verify_rendered_manim_editable_source,
};
use std::{collections::BTreeMap, fs, io::Read};

type SourceResult<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const ORIGINAL_TEXT: &str = "x² + y² = r²";
const EDITED_TEXT: &str = "x² + y² = 2r²";

fn hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn text<'a>(value: &'a serde_json::Value, pointer: &str) -> SourceResult<&'a str> {
    value
        .pointer(pointer)
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("missing editable source field {pointer}").into())
}
fn read(path: &Path, limit: usize) -> SourceResult<Vec<u8>> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err("editable source input must be absolute and nontraversing".into());
    }
    let before = fs::symlink_metadata(path)?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.len() == 0
        || before.len() > limit as u64
    {
        return Err("editable source input is not a bounded original file".into());
    }
    let canonical = fs::canonicalize(path)?;
    let mut bytes = Vec::with_capacity(before.len() as usize);
    fs::File::open(&canonical)?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)?;
    let after = fs::symlink_metadata(path)?;
    if !after.is_file()
        || after.file_type().is_symlink()
        || after.len() != before.len()
        || bytes.len() as u64 != before.len()
        || fs::canonicalize(path)? != canonical
    {
        return Err("editable source input changed during readback".into());
    }
    Ok(bytes)
}
fn object(path: &Path) -> SourceResult<serde_json::Value> {
    Ok(serde_json::from_slice(&read(path, 4 * 1024 * 1024)?)?)
}
fn new_report(path: &Path, value: &serde_json::Value) -> SourceResult<()> {
    if !path.is_absolute() || !path.parent().is_some_and(Path::is_dir) || path.try_exists()? {
        return Err("editable report needs a new absolute owned destination".into());
    }
    write_camera_evidence(path, value)?;
    if object(path)? != *value {
        return Err("editable report readback differs".into());
    }
    Ok(())
}
fn fonts(package: &profile::Package) -> BTreeMap<String, Vec<u8>> {
    package
        .entries
        .iter()
        .filter(|(path, _)| path.starts_with("fonts/"))
        .map(|(path, bytes)| (path.clone(), bytes.clone()))
        .collect()
}
fn assert_original_receipts(
    service: &StudioService,
    project_id: Uuid,
    link: &serde_json::Value,
) -> SourceResult<()> {
    let request = text(link, "/request_id")?;
    for (field, suffix) in [
        ("original_start_receipt", ":start"),
        ("original_result_receipt", ":result"),
    ] {
        let actual = service
            .latest_production_receipt(project_id, &format!("{request}{suffix}"))?
            .ok_or("original native receipt disappeared")?;
        if serde_json::to_value(actual)? != link[field] {
            return Err("original native receipt changed during source-only work".into());
        }
    }
    Ok(())
}

fn native_source(
    service: &StudioService,
    project: &Project,
    native_path: &Path,
    output_root: &Path,
) -> SourceResult<(Vec<u8>, profile::Package, serde_json::Value)> {
    let raw = read(native_path, 4 * 1024 * 1024)?;
    let native: serde_json::Value = serde_json::from_slice(&raw)?;
    if native["native_manim_e2e"] != "PASS"
        || !native["receipt_read_error"].is_null()
        || !native["custody_error"].is_null()
        || native["project_id"] != json!(project.id)
        || native["generation"] != json!(project.generation)
        || native["revision"] != json!(project.revision)
    {
        return Err("original native editable result is not bound to the saved project".into());
    }
    let scene_id: Uuid = serde_json::from_value(native["scene_id"].clone())?;
    let deliverable_id: Uuid = serde_json::from_value(native["deliverable_id"].clone())?;
    let package = prepare_saved_manim_editable_source(
        service,
        project.id,
        &RevisionStamp::from(project),
        scene_id,
        deliverable_id,
    )?;
    let request = text(&native, "/request_id")?;
    let start = service
        .latest_production_receipt(project.id, &format!("{request}:start"))?
        .ok_or("original editable start receipt absent")?;
    let terminal = service
        .latest_production_receipt(project.id, &format!("{request}:result"))?
        .ok_or("original editable terminal receipt absent")?;
    if start.command != "driver.manim-community.editable.render.start"
        || terminal.command != "driver.manim-community.render.result"
        || start.stage != "completed"
        || terminal.stage != "completed"
        || start.generation != project.generation
        || terminal.generation != project.generation
        || start.revision != project.revision
        || terminal.revision != project.revision
        || serde_json::to_value(&start)? != native["original_start_receipt"]
        || serde_json::to_value(&terminal)? != native["original_result_receipt"]
    {
        return Err("native editable command/stamp/receipt binding differs".into());
    }
    let result = &native["result"];
    let returned = &result["result"];
    let observed = &returned["result"];
    let data = &observed["data"];
    let artifact = &data["artifact"];
    if returned["replayed"] != false
        || returned["receipt"] != serde_json::to_value(&terminal)?
        || observed != &terminal.payload["result"]
        || observed["provenance_checked"] != true
        || observed["execution"]["backend"] != "driver:manim-community"
        || result["job_ref"] != data["job_ref"]
        || data["state"] != "succeeded"
        || !data["source_custody_error"].is_null()
        || !result["artifact"]["editable_source_custody_error"].is_null()
        || result["plan_sha256"] != package.closure.plan_sha256
    {
        return Err("source carrier is not the original acknowledged driver result".into());
    }
    let verified = verify_rendered_manim_editable_source(
        output_root,
        text(artifact, "/relative_path")?,
        &package.plan,
        artifact,
    )?;
    if result["artifact"]["editable_source"] != verified {
        return Err("common editable source admission differs from original native return".into());
    }
    let descriptor = &verified["source"];
    let basename = text(descriptor, "/relative_path")?;
    if basename.contains(['/', '\\']) || !basename.ends_with(".mwedit.zip") {
        return Err("original source is not the job-bound basename".into());
    }
    let root = fs::canonicalize(output_root)?;
    let path = root.join(basename);
    if fs::canonicalize(&path)?.parent() != Some(root.as_path()) {
        return Err("original source escaped the output mount".into());
    }
    let bytes = read(&path, profile::MAX_BUNDLE_BYTES)?;
    if descriptor["sha256"] != hash(&bytes)
        || descriptor["bytes"] != json!(bytes.len())
        || profile::inspect_source_bundle(&bytes)?.entries != package.entries
    {
        return Err("original native bundle changed before import".into());
    }
    Ok((
        bytes,
        package,
        json!({
            "native_evidence_sha256":hash(&raw), "request_id":request,
            "source":descriptor, "original_start_receipt":start, "original_result_receipt":terminal,
            "native_frame_content_verified":false,
        }),
    ))
}
fn attach(
    service: &StudioService,
    project: &Project,
    bytes: &[u8],
    dir: &Path,
    label: &str,
    request: &str,
) -> SourceResult<(Project, Uuid)> {
    let path = dir.join(format!("source-{}.mwedit.zip", Uuid::new_v4()));
    retain_seed_fixture(&path, bytes)?;
    let updated = service
        .import_asset_file(
            project.id,
            &RevisionStamp::from(project),
            request,
            &path,
            label.into(),
            SOURCE_BUNDLE_MIME_V2.into(),
        )?
        .project;
    let additions: Vec<_> = updated
        .assets
        .iter()
        .filter(|asset| !project.assets.iter().any(|before| before.id == asset.id))
        .collect();
    let expected = hash(bytes);
    if additions.len() != 1
        || additions[0].content_sha256.as_deref() != Some(expected.as_str())
        || additions[0].media_type != SOURCE_BUNDLE_MIME_V2
    {
        return Err("source import did not retain the exact original bytes once".into());
    }
    let id = additions[0].id;
    // Retain the original staging bytes as private evidence, including after failure.
    Ok((updated, id))
}
fn apply_pair(
    database: &Path,
    service: StudioService,
    project: Project,
    source: (Vec<u8>, profile::Package, serde_json::Value),
    output: &Path,
    request: &str,
) -> SourceResult<serde_json::Value> {
    let (original_bytes, package, link) = source;
    if request.is_empty() || request.len() > 64 || request.chars().any(char::is_control) {
        return Err("editable source mutation needs a bounded new request identity".into());
    }
    let scene_id = package.plan.binding.scene_id;
    let deliverable_id = package.plan.binding.deliverable_id;
    let mut edited_plan = package.plan.clone();
    let text_nodes: Vec<_> = edited_plan
        .nodes
        .iter()
        .enumerate()
        .filter(|(_, node)| node.typography.is_some())
        .map(|(index, _)| index)
        .collect();
    if text_nodes.len() != 1
        || edited_plan.nodes[text_nodes[0]]
            .typography
            .as_ref()
            .unwrap()
            .text
            != ORIGINAL_TEXT
    {
        return Err("editable source fixture must contain the original single title".into());
    }
    let node = &mut edited_plan.nodes[text_nodes[0]];
    let node_id = node.node_id;
    node.typography.as_mut().unwrap().text = EDITED_TEXT.into();
    let edited_bytes = profile::source_bundle(&edited_plan, &fonts(&package))?;
    let before_receipts = serde_json::to_value(service.production_receipts(project.id, 256)?)?;
    let original_stamp = RevisionStamp::from(&project);
    new_report(
        &output.with_extension("intent.json"),
        &json!({
            "schema":"motionwright.manim.editable.source-edit-intent/1",
            "project_id":project.id,"expected":original_stamp,"request_id":request,
            "original_source_sha256":hash(&original_bytes),"edited_source_sha256":hash(&edited_bytes),
            "automatic_retry":false,"provider_dispatch_performed":false,
        }),
    )?;
    let (attached, original_id) = attach(
        &service,
        &project,
        &original_bytes,
        output.parent().unwrap(),
        "Original native editable source",
        &format!("{request}:attach-original"),
    )?;
    let (attached, edited_id) = attach(
        &service,
        &attached,
        &edited_bytes,
        output.parent().unwrap(),
        "Edited native equation source",
        &format!("{request}:attach-edited"),
    )?;
    let attached_stamp = RevisionStamp::from(&attached);
    drop(service);
    let reopened = StudioService::open(database)?;
    if reopened.project(project.id)? != attached {
        return Err("attached original/edited sources did not survive SQLite reopen".into());
    }
    let inspected =
        inspect_saved_manim_editable_source(&reopened, project.id, &attached_stamp, original_id)?;
    let preview = preview_saved_manim_editable_edit(
        &reopened,
        project.id,
        &attached_stamp,
        original_id,
        edited_id,
        node_id,
        EditableProperty::Text,
    )?;
    if inspected.source_sha256 != hash(&original_bytes)
        || preview.original_source_sha256 != hash(&original_bytes)
        || preview.edited_source_sha256 != hash(&edited_bytes)
        || preview.expected != attached_stamp
        || !matches!(&preview.change, Change::UpdateCanvasText { .. })
        || preview.native_execution_performed
        || preview.imported_source_authority_used
    {
        return Err("source preview changed bytes or exceeds one typed text Change".into());
    }
    let updated = reopened
        .apply(
            project.id,
            &preview.expected,
            &format!("{request}:apply-text"),
            &preview.change,
        )?
        .project;
    let mut intended = attached.clone();
    intended.apply_change(&preview.change)?;
    intended.revision = updated.revision;
    if updated != intended
        || updated.revision
            != attached
                .revision
                .checked_add(1)
                .ok_or("revision overflow")?
    {
        return Err("source import changed content outside the one authorized text Change".into());
    }
    drop(reopened);
    let reopened = StudioService::open(database)?;
    if reopened.project(project.id)? != updated
        || serde_json::to_value(reopened.production_receipts(project.id, 256)?)? != before_receipts
    {
        return Err("source-only commit or original production receipts changed on reopen".into());
    }
    assert_original_receipts(&reopened, project.id, &link)?;
    let current = inspect_saved_manim_editable_source(
        &reopened,
        project.id,
        &RevisionStamp::from(&updated),
        edited_id,
    )?;
    let current_bytes = export_saved_manim_editable_source_bundle(
        &reopened,
        project.id,
        &RevisionStamp::from(&updated),
        scene_id,
        deliverable_id,
    )?;
    let current_package = profile::inspect_source_bundle(&current_bytes)?;
    retain_seed_fixture(&output.with_extension("current.mwedit.zip"), &current_bytes)?;
    let report = json!({
        "schema":"motionwright.manim.editable.source-cycle/1","phase":"edit-import",
        "project_id":updated.id,"resource":updated.resource_key(),"generation":updated.generation,
        "revision":updated.revision,"scene_id":scene_id,"deliverable_id":deliverable_id,
        "original_stamp":original_stamp,"attached_stamp":attached_stamp,
        "original_source_asset_id":original_id,"edited_source_asset_id":edited_id,
        "original_source_sha256":hash(&original_bytes),"edited_source_sha256":hash(&edited_bytes),
        "current_export_sha256":hash(&current_bytes),"current_export_bytes":current_bytes.len(),
        "original_native":link,"preview":preview,"current_source_inspection":current,
        "prepared_plan":current_package.plan,"closure":current_package.closure,
        "original_text":ORIGINAL_TEXT,"edited_text":EDITED_TEXT,"edited_node_id":node_id,
        "primitive_count":current_package.plan.nodes.len(),"frame_count":current_package.plan.clock.frame_count,
        "font_sha256":EDITABLE_FONT_SHA256,"font_license_sha256":hash(EDITABLE_FONT_LICENSE),
        "production_receipts_unchanged":true,"fresh_sqlite_readback":true,
        "native_execution_performed":false,"provider_dispatch_performed":false,
        "native_bundle_reopen_verified":false,"human_acceptance":false,"automatic_retry":false,
    });
    new_report(output, &report)?;
    Ok(report)
}
pub(super) fn edit(
    database: &Path,
    native_path: &Path,
    output_root: &Path,
    output: &Path,
    request: &str,
) -> SourceResult<serde_json::Value> {
    if output.try_exists()? || output.with_extension("intent.json").try_exists()? {
        return Err("source edit already has evidence or intent; do not repeat it".into());
    }
    let native = object(native_path)?;
    let project_id: Uuid = serde_json::from_value(native["project_id"].clone())?;
    let service = StudioService::open(database)?;
    let project = service.project(project_id)?;
    let source = native_source(&service, &project, native_path, output_root)?;
    apply_pair(database, service, project, source, output, request)
}
pub(super) fn readback(
    database: &Path,
    edit_path: &Path,
    native_path: &Path,
    output_root: &Path,
    output: &Path,
) -> SourceResult<serde_json::Value> {
    let edit_raw = read(edit_path, 4 * 1024 * 1024)?;
    let edit: serde_json::Value = serde_json::from_slice(&edit_raw)?;
    let project_id: Uuid = serde_json::from_value(edit["project_id"].clone())?;
    let service = StudioService::open(database)?;
    let project = service.project(project_id)?;
    if edit["schema"] != "motionwright.manim.editable.source-cycle/1"
        || edit["phase"] != "edit-import"
        || edit["generation"] != json!(project.generation)
        || edit["revision"] != json!(project.revision)
    {
        return Err("edited source receipt is stale or malformed".into());
    }
    let before_receipts = serde_json::to_value(service.production_receipts(project.id, 256)?)?;
    let edited_id: Uuid = serde_json::from_value(edit["edited_source_asset_id"].clone())?;
    let inspected = inspect_saved_manim_editable_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        edited_id,
    )?;
    if inspected.source_sha256 != text(&edit, "/edited_source_sha256")?
        || serde_json::to_value(&inspected.current_plan)? != edit["prepared_plan"]
    {
        return Err("edited CAS source differs from the committed semantic edit".into());
    }
    for (id_key, sha_key) in [
        ("original_source_asset_id", "original_source_sha256"),
        ("edited_source_asset_id", "edited_source_sha256"),
    ] {
        let id: Uuid = serde_json::from_value(edit[id_key].clone())?;
        let expected = text(&edit, &format!("/{sha_key}"))?;
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.id == id)
            .ok_or("source Asset disappeared")?;
        if asset.media_type != SOURCE_BUNDLE_MIME_V2
            || asset.content_sha256.as_deref() != Some(expected)
        {
            return Err("source Asset identity changed after native execution".into());
        }
        let handle = service.store();
        let bytes = handle
            .lock()
            .read_blob(expected, profile::MAX_BUNDLE_BYTES as u64)?;
        if hash(&bytes) != expected {
            return Err("source CAS bytes changed".into());
        }
        profile::inspect_source_bundle(&bytes)?;
    }
    assert_original_receipts(&service, project.id, &edit["original_native"])?;
    let (bytes, package, link) = native_source(&service, &project, native_path, output_root)?;
    if hash(&bytes) != text(&edit, "/current_export_sha256")?
        || serde_json::to_value(&package.plan)? != edit["prepared_plan"]
        || service.project(project.id)? != project
        || serde_json::to_value(service.production_receipts(project.id, 256)?)? != before_receipts
    {
        return Err("edited native carrier differs from the saved source export".into());
    }
    let report = json!({
        "schema":"motionwright.manim.editable.source-cycle/1","phase":"fresh-readback",
        "project_id":project.id,"canonical_stamp":RevisionStamp::from(&project),
        "edit_receipt_sha256":hash(&edit_raw),"original_native":edit["original_native"],
        "current_native":link,"source_sha256":hash(&bytes),"source_closure":package.closure,
        "source_assets_original_bytes_verified":true,"saved_export_matches_native_carrier":true,
        "fresh_sqlite_readback":true,"original_native_receipts_unchanged":true,
        "production_receipts_unchanged":true,"native_execution_performed":false,
        "provider_dispatch_performed":false,"native_bundle_reopen_verified":false,
        "human_acceptance":false,"automatic_retry":false,
    });
    new_report(output, &report)?;
    Ok(report)
}

pub(super) fn input_descriptor(
    path: &Path,
) -> SourceResult<motionwright_native::manim_editable_projection::OriginalEditableBundleInput> {
    Ok(serde_json::from_slice(&read(path, 4096)?)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forged_native_pass_without_original_store_receipts_cannot_import_or_edit() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("source.sqlite3");
        seed(&database).unwrap();
        let service = StudioService::open(&database).unwrap();
        let project = service.projects(2).unwrap().remove(0);
        let native_path = temporary.path().join("forged-native.json");
        let output = temporary.path().join("edit.json");
        let native = json!({
            "native_manim_e2e":"PASS", "receipt_read_error":null, "custody_error":null,
            "project_id":project.id, "generation":project.generation, "revision":project.revision,
            "scene_id":project.scenes[0].id, "deliverable_id":project.deliverables[0].id,
            "request_id":"forged-pass-without-receipts"
        });
        write_camera_evidence(&native_path, &native).unwrap();
        let error = edit(
            &database,
            &native_path,
            temporary.path(),
            &output,
            "not-dispatched",
        )
        .unwrap_err()
        .to_string();
        assert!(
            error.contains("original editable start receipt absent"),
            "{error}"
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert!(
            service
                .production_receipts(project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert!(!output.exists());
        assert!(!output.with_extension("intent.json").exists());
    }

    #[test]
    fn preexisting_source_edit_intent_is_never_replayed() {
        let temporary = tempfile::tempdir().unwrap();
        let database = temporary.path().join("source.sqlite3");
        seed(&database).unwrap();
        let service = StudioService::open(&database).unwrap();
        let project = service.projects(2).unwrap().remove(0);
        let output = temporary.path().join("edit.json");
        let intent = output.with_extension("intent.json");
        let original = json!({"outcome":"UNKNOWN","automatic_retry":false});
        write_camera_evidence(&intent, &original).unwrap();
        let error = edit(
            &database,
            &temporary.path().join("absent-native.json"),
            temporary.path(),
            &output,
            "new-identity-does-not-resolve-unknown",
        )
        .unwrap_err()
        .to_string();
        assert!(error.contains("do not repeat"), "{error}");
        assert_eq!(object(&intent).unwrap(), original);
        assert!(!output.exists());
        assert_eq!(service.project(project.id).unwrap(), project);
        assert!(
            service
                .production_receipts(project.id, 256)
                .unwrap()
                .is_empty()
        );
    }
}
