//! Closed static V2 source on the original StudioService and Manim Driver Host.
//! Source inspection has no native execution authority. Existing Store/CAS,
//! asset import, production receipts and runtime grants remain authoritative.
use motionwright_domain::{
    BlendMode, CameraState, CanvasTransform, Change, CoordinateSpace, OutputColorSpace,
    OutputContainer, Project, RendererKind, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2, MAX_SOURCE_ENTRY_BYTES_V2, ManimCanvasV2, ManimFrameClockV2,
    ManimMatteV2, ManimOutputRoleV2, ManimProfileIdV2, ManimRenderProfile, ManimSceneBindingV2,
    ManimScenePlanV2, ManimSourcePackageV2, ManimSourceShapeKindV2, ManimStaticShapeV2,
    SOURCE_BUNDLE_MIME_V2, inspect_native_source_readback_v2, inspect_source_bundle_v2,
    rebind_source_v2, source_bundle_v2, source_package_v2, validate_plan_v2,
};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{io::Read, path::Path};
use uuid::Uuid;

fn reject(message: &'static str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}
fn invalid(message: &'static str) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
fn stale() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Manim static source requires the current full Project stamp",
    )
}
fn current(service: &StudioService, id: Uuid, expected: &RevisionStamp) -> NativeResult<Project> {
    let project = service.project(id).map_err(crate::storage_error)?;
    if project.id != id || RevisionStamp::from(&project) != *expected {
        return Err(stale());
    }
    Ok(project)
}

/// The normal production entrypoint must use a saved deliverable. It cannot
/// invent a clock or select an ambiguous source/output association from a bare
/// render profile. Explicit scene/deliverable entrypoints remain available.
pub fn select_saved_manim_deliverable_for_profile(
    project: &Project,
    expected: &RevisionStamp,
    scene_id: Uuid,
    profile: &ManimRenderProfile,
) -> NativeResult<Uuid> {
    if RevisionStamp::from(project) != *expected {
        return Err(stale());
    }
    project
        .validate()
        .map_err(|_| invalid("Canonical Project is invalid"))?;
    if !project.scenes.iter().any(|row| row.id == scene_id) {
        return Err(invalid("Manim scene is absent"));
    }
    profile
        .validate()
        .map_err(|_| invalid("Manim render profile is invalid"))?;
    let mut matches = project.deliverables.iter().filter(|output| {
        (output.included_scene_ids.is_empty() || output.included_scene_ids.contains(&scene_id))
            && output.width == profile.width
            && output.height == profile.height
            && output.frame_rate.den == 1
            && output.frame_rate.num == i64::from(profile.frame_rate)
    });
    let id = matches
        .next()
        .map(|output| output.id)
        .ok_or_else(|| reject("Normal Manim production requires a matching saved deliverable"))?;
    if matches.next().is_some() {
        return Err(reject(
            "Normal Manim production requires one unambiguous saved deliverable",
        ));
    }
    Ok(id)
}

/// Preserve canonical clock, full UUIDs, positions, fill and stacking. Any source
/// feature without a representation in the existing V2 compiler is refused.
pub fn build_manim_static_plan_v2(
    project: &Project,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimScenePlanV2> {
    if RevisionStamp::from(project) != *expected {
        return Err(stale());
    }
    project
        .validate()
        .map_err(|_| invalid("Canonical Project is invalid"))?;
    let scene = project
        .scenes
        .iter()
        .find(|row| row.id == scene_id)
        .ok_or_else(|| invalid("Manim static scene is absent"))?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err(reject("Static V2 requires the ManimCommunity renderer"));
    }
    if scene.camera != CameraState::default() {
        return Err(reject(
            "Static V2 does not realize an authored camera change; use the admitted camera profile",
        ));
    }
    if !scene.beats.is_empty() || scene.nodes.is_empty() || scene.nodes.len() > 512 {
        return Err(reject(
            "Static V2 does not realize beats or empty/oversized inventories",
        ));
    }
    if !project.production_design.measured_bindings.is_empty()
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|row| row.scene_id == scene_id)
        || project
            .production_design
            .heroes
            .iter()
            .any(|row| row.scene_id == scene_id)
        || project
            .production_design
            .stages
            .iter()
            .any(|row| row.scene_id == scene_id)
        || project
            .production_design
            .procedural_fields
            .iter()
            .any(|row| row.scene_id == scene_id)
    {
        return Err(reject(
            "Static V2 cannot discard creative components or measured bindings",
        ));
    }
    let end = scene
        .start
        .checked_add(scene.duration)
        .map_err(|_| invalid("Static scene clock overflow"))?;
    if !project.audio.sfx_cues.is_empty()
        || project
            .audio
            .cues
            .iter()
            .any(|cue| cue.at >= scene.start && cue.at < end)
    {
        return Err(reject(
            "Static V2 is a video contribution without audio or caption cues",
        ));
    }
    let output = project
        .deliverables
        .iter()
        .find(|row| row.id == deliverable_id)
        .ok_or_else(|| invalid("Manim static deliverable is absent"))?;
    if !output.included_scene_ids.is_empty() && !output.included_scene_ids.contains(&scene_id) {
        return Err(invalid(
            "Manim scene is excluded from the saved deliverable",
        ));
    }
    if output.frame_rate.den != 1
        || output.container != OutputContainer::Mp4
        || output.video_codec != VideoCodec::H264
        || output.color_space != OutputColorSpace::Rec709
        || output.burn_in_captions
    {
        return Err(reject(
            "Static V2 requires integral fps and saved Rec.709 H264 MP4 without burned captions",
        ));
    }
    let matte = project
        .visual_language
        .palette
        .iter()
        .find(|row| row.name == "background")
        .or_else(|| {
            project
                .visual_language
                .palette
                .iter()
                .find(|row| row.name == "surface")
        });
    if matte.map(|row| row.value.as_str()) != Some("#000000") {
        return Err(reject(
            "Static V2 requires an explicitly authored opaque black matte",
        ));
    }
    let mut nodes = Vec::with_capacity(scene.nodes.len());
    for (index, node) in scene.nodes.iter().enumerate() {
        let source_kind = match node.kind.as_str() {
            "shape" => ManimSourceShapeKindV2::Shape,
            "rectangle" => ManimSourceShapeKindV2::Rectangle,
            "circle" => ManimSourceShapeKindV2::Circle,
            _ => {
                return Err(reject(
                    "Static V2 realizes only closed shape, rectangle and circle sources",
                ));
            }
        };
        if node.coordinate_space != CoordinateSpace::ProjectPixels
            || node.parent_id.is_some()
            || !node.relations.is_empty()
            || !node.keyframes.is_empty()
            || node.opacity != 1.0
            || node.rotation_deg != 0.0
            || node.text.is_some()
            || node.style.blend_mode != BlendMode::Normal
            || node.style.stroke.is_some()
            || node.style.stroke_width != 0.0
            || node.style.font_family.is_some()
            || node.style.font_size.is_some()
            || node.style.font_weight.is_some()
            || node.style.line_height.is_some()
        {
            return Err(reject(
                "Static V2 cannot discard hierarchy, animation, opacity, rotation, blend, text, font or stroke",
            ));
        }
        nodes.push(ManimStaticShapeV2 {
            node_id: node.id,
            display_name: node.name.clone(),
            source_kind,
            canonical_ordinal: u32::try_from(index)
                .map_err(|_| invalid("Static ordinal overflow"))?,
            z_index: node.z_index,
            x_px: node.x,
            y_px: node.y,
            width_px: node.width,
            height_px: node.height,
            fill_hex: node
                .style
                .fill
                .clone()
                .ok_or_else(|| reject("Static V2 requires an authored fill"))?,
        });
    }
    let profile = ManimRenderProfile {
        width: output.width,
        height: output.height,
        frame_rate: u32::try_from(output.frame_rate.num)
            .map_err(|_| reject("Static fps is out of bounds"))?,
    };
    let plan = ManimScenePlanV2 {
        schema_version: 2,
        profile_id: ManimProfileIdV2::StaticShapes,
        output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
        binding: ManimSceneBindingV2 {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            deliverable_id,
        },
        clock: ManimFrameClockV2::from_canonical(scene.start, scene.duration, output.frame_rate)
            .map_err(|_| {
                reject("Static duration or project offset is outside the exact frame clock")
            })?,
        render_profile: profile,
        canvas: ManimCanvasV2::default(),
        background: ManimMatteV2::default(),
        nodes,
    };
    validate_plan_v2(&plan)
        .map_err(|_| reject("Static source exceeds the existing closed V2 profile"))?;
    Ok(plan)
}

/// This full snapshot read/compile/read is source preparation, not permission.
pub fn prepare_saved_manim_static_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimSourcePackageV2> {
    let before = current(service, project_id, expected)?;
    let plan = build_manim_static_plan_v2(&before, expected, scene_id, deliverable_id)?;
    let package = source_package_v2(&plan)
        .map_err(|_| reject("Original static source compiler refused the saved Project"))?;
    if current(service, project_id, expected)? != before {
        return Err(stale());
    }
    Ok(package)
}

pub fn export_saved_manim_static_source_bundle(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<Vec<u8>> {
    let before = current(service, project_id, expected)?;
    let plan = build_manim_static_plan_v2(&before, expected, scene_id, deliverable_id)?;
    let bytes =
        source_bundle_v2(&plan).map_err(|_| reject("Saved static source could not be packaged"))?;
    if current(service, project_id, expected)? != before {
        return Err(stale());
    }
    Ok(bytes)
}

fn read_attached(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
) -> NativeResult<(String, ManimSourcePackageV2)> {
    let asset = project
        .assets
        .iter()
        .find(|row| row.id == asset_id)
        .ok_or_else(|| invalid("Static source asset is not attached to this Project"))?;
    if asset.media_type != SOURCE_BUNDLE_MIME_V2 {
        return Err(reject("Asset is not a closed static Manim source package"));
    }
    let digest = asset
        .content_sha256
        .as_ref()
        .ok_or_else(|| invalid("Static source asset has no content digest"))?;
    let bytes = {
        let handle = service.store();
        let store = handle.lock();
        if store
            .load_project(project.id)
            .map_err(crate::storage_error)?
            != *project
        {
            return Err(stale());
        }
        store
            .read_blob(digest, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)
            .map_err(crate::storage_error)?
    };
    if hex::encode(Sha256::digest(&bytes)) != *digest {
        return Err(invalid(
            "Static source bytes differ from their attached digest",
        ));
    }
    let package = inspect_source_bundle_v2(&bytes)
        .map_err(|_| reject("Imported static source differs from the closed trusted compiler"))?;
    if package.plan.binding.project_id != project.id {
        return Err(reject(
            "Imported static source belongs to a different Project",
        ));
    }
    Ok((digest.clone(), package))
}

#[derive(Debug, Clone, Serialize)]
pub struct InspectedManimStaticSourceV2 {
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub origin_binding: ManimSceneBindingV2,
    pub current_binding: ManimSceneBindingV2,
    pub original_closure: motionwright_manim_profile::ManimSourceClosureV1,
    pub current_closure: motionwright_manim_profile::ManimSourceClosureV1,
    pub current_plan: ManimScenePlanV2,
    pub revision_only_rebound: bool,
    pub editable_parameters: Vec<&'static str>,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
}

pub fn inspect_saved_manim_static_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    asset_id: Uuid,
) -> NativeResult<InspectedManimStaticSourceV2> {
    let project = current(service, project_id, expected)?;
    let (digest, source) = read_attached(service, &project, asset_id)?;
    let plan = build_manim_static_plan_v2(
        &project,
        expected,
        source.plan.binding.scene_id,
        source.plan.binding.deliverable_id,
    )?;
    let rebound = rebind_source_v2(&source.plan, &plan)
        .map_err(|_| reject("Saved Project controls differ from the attached static source"))?;
    if current(service, project_id, expected)? != project {
        return Err(stale());
    }
    Ok(InspectedManimStaticSourceV2 {
        source_asset_id: asset_id,
        source_sha256: digest,
        origin_binding: source.plan.binding.clone(),
        current_binding: plan.binding.clone(),
        revision_only_rebound: source.plan.binding != plan.binding,
        original_closure: source.closure,
        current_closure: rebound.closure,
        current_plan: plan,
        editable_parameters: vec![
            "one_node.x_px",
            "one_node.y_px",
            "one_node.width_px",
            "one_node.height_px",
        ],
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

#[derive(Debug, Clone, Serialize)]
pub struct PreviewedManimStaticTransformEditV2 {
    pub expected: RevisionStamp,
    pub original_source_asset_id: Uuid,
    pub edited_source_asset_id: Uuid,
    pub original_source_sha256: String,
    pub edited_source_sha256: String,
    pub current_plan_sha256: String,
    pub proposed_plan_sha256: String,
    pub change: Change,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
}

/// Return exactly one ordinary domain Change. The original StudioService apply
/// owns CAS, locks, request idempotency and durable commit; this module never
/// performs or retries that mutation and never trusts imported consent.
pub fn preview_saved_manim_static_transform_edit(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    original_asset_id: Uuid,
    edited_asset_id: Uuid,
    node_id: Uuid,
) -> NativeResult<PreviewedManimStaticTransformEditV2> {
    if original_asset_id == edited_asset_id {
        return Err(invalid(
            "Edited static source must be a separately attached asset",
        ));
    }
    let project = current(service, project_id, expected)?;
    let (original_digest, original) = read_attached(service, &project, original_asset_id)?;
    let plan = build_manim_static_plan_v2(
        &project,
        expected,
        original.plan.binding.scene_id,
        original.plan.binding.deliverable_id,
    )?;
    let rebound = rebind_source_v2(&original.plan, &plan)
        .map_err(|_| reject("Original source no longer describes the saved static controls"))?;
    let (edited_digest, edited) = read_attached(service, &project, edited_asset_id)?;
    let node = edited
        .plan
        .nodes
        .iter()
        .find(|row| row.node_id == node_id)
        .ok_or_else(|| invalid("Edited static node is absent"))?;
    let before_node = plan
        .nodes
        .iter()
        .find(|row| row.node_id == node_id)
        .ok_or_else(|| invalid("Original static node is absent"))?;
    if node == before_node {
        return Err(invalid("Static transform edit is a no-op"));
    }
    let change = Change::TransformCanvasNode {
        scene_id: plan.binding.scene_id,
        node_id,
        transform: CanvasTransform {
            x: node.x_px,
            y: node.y_px,
            width: node.width_px,
            height: node.height_px,
            rotation_deg: 0.0,
            opacity: 1.0,
        },
    };
    let mut candidate = project.clone();
    candidate
        .apply_change(&change)
        .map_err(|error| Error::new(ErrorCode::InvalidArgument, error.to_string()))?;
    let proposed = build_manim_static_plan_v2(
        &candidate,
        &RevisionStamp::from(&candidate),
        plan.binding.scene_id,
        plan.binding.deliverable_id,
    )?;
    let verified = rebind_source_v2(&edited.plan, &proposed)
        .map_err(|_| reject("Static import changes fields outside one admitted node transform"))?;
    if current(service, project_id, expected)? != project {
        return Err(stale());
    }
    Ok(PreviewedManimStaticTransformEditV2 {
        expected: expected.clone(),
        original_source_asset_id: original_asset_id,
        edited_source_asset_id: edited_asset_id,
        original_source_sha256: original_digest,
        edited_source_sha256: edited_digest,
        current_plan_sha256: rebound.closure.plan_sha256,
        proposed_plan_sha256: verified.closure.plan_sha256,
        change,
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

fn read_owned_file(root: &Path, relative: &str, maximum: usize) -> NativeResult<Vec<u8>> {
    let io_error = |_| {
        Error::new(
            ErrorCode::BackendFailed,
            "Native static source artifact cannot be read",
        )
    };
    let root = std::fs::canonicalize(root).map_err(io_error)?;
    let candidate = root.join(relative);
    let before = std::fs::symlink_metadata(&candidate).map_err(io_error)?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.len() == 0
        || before.len() > maximum as u64
    {
        return Err(invalid(
            "Native static source artifact is not a bounded regular file",
        ));
    }
    let path = std::fs::canonicalize(&candidate).map_err(io_error)?;
    if path.parent() != Some(root.as_path()) {
        return Err(invalid(
            "Native static source artifact escaped its output root",
        ));
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    std::fs::File::open(&path)
        .map_err(io_error)?
        .take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(io_error)?;
    let after = std::fs::symlink_metadata(&candidate).map_err(io_error)?;
    if !after.is_file()
        || after.file_type().is_symlink()
        || after.len() != before.len()
        || bytes.len() as u64 != before.len()
        || std::fs::canonicalize(&candidate).map_err(io_error)? != path
    {
        return Err(invalid(
            "Native static artifact changed during bounded readback",
        ));
    }
    Ok(bytes)
}

/// Native job/provenance and video hash are checked by ProductionCoordinator.
/// Here the retained editable package and actual native sidecar are independently
/// reopened. Request values never stand in for measured object/camera getters.
pub fn verify_rendered_manim_static_source(
    output_root: &Path,
    video_relative_path: &str,
    original_plan: &ManimScenePlanV2,
    native_artifact: &Value,
) -> NativeResult<Value> {
    let filename = video_relative_path.rsplit('/').next().unwrap_or_default();
    let token = filename
        .strip_prefix("mw_")
        .and_then(|name| name.strip_suffix(".mp4"))
        .filter(|token| {
            token.len() == 32
                && token
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        .ok_or_else(|| invalid("Static video filename is not bound to a native job"))?;
    if video_relative_path
        != format!(
            "videos/mw_{token}/{}/mw_{token}.mp4",
            original_plan.render_profile.quality_directory()
        )
    {
        return Err(invalid(
            "Static video path differs from its job/profile binding",
        ));
    }
    let package = source_package_v2(original_plan)
        .map_err(|_| reject("Original static source is outside the closed profile"))?;
    let expected_bytes = source_bundle_v2(original_plan)
        .map_err(|_| reject("Original static source cannot be packaged"))?;
    let source_path = format!("mw_{token}.mwstatic.zip");
    let expected = json!({
        "relative_path": source_path, "sha256": hex::encode(Sha256::digest(&expected_bytes)),
        "bytes": expected_bytes.len(), "media_type": SOURCE_BUNDLE_MIME_V2,
        "closure": package.closure,
    });
    if native_artifact.get("static_source") != Some(&expected)
        || read_owned_file(output_root, &source_path, MAX_SOURCE_BUNDLE_BYTES_V2)? != expected_bytes
    {
        return Err(invalid(
            "Native static source carrier differs from the original compiled plan",
        ));
    }
    let getter_path = format!("mw_{token}.objects.json");
    let getters = read_owned_file(output_root, &getter_path, MAX_SOURCE_ENTRY_BYTES_V2)?;
    let expected_getters = json!({
        "relative_path": getter_path, "sha256": hex::encode(Sha256::digest(&getters)),
        "bytes": getters.len(), "media_type": "application/json",
    });
    if native_artifact.get("static_getters") != Some(&expected_getters) {
        return Err(invalid(
            "Native static getter descriptor differs from retained bytes",
        ));
    }
    let observed = inspect_native_source_readback_v2(original_plan, &getters).map_err(|_| {
        reject("Native static object/camera measurements differ from the canonical source")
    })?;
    Ok(json!({
        "source": expected, "native_getters": expected_getters,
        "observed_object_count": observed.objects.len(),
        "frame_count_required": original_plan.clock.frame_count,
        "source_reopened": true, "native_object_getters_verified": true,
        "decoded_video_pts_verified": false, "human_approval_asserted": false,
    }))
}
