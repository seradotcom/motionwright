//! Closed source-only projection of saved CameraReframe V2 to Manim Community.
//! Reuses existing Project, revision stamp, Store and public Manim static shapes.
//! Compiled programs and imported packages remain inert until the existing
//! native controller admits a job; source inspection never grants execution.
//! The existing domain must admit the exact version-2/ManimCommunity pair first.
use motionwright_domain::{
    BlendMode, CameraReframe, Change, CoordinateSpace, OutputColorSpace, OutputContainer, Project,
    RendererKind, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2, ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2,
    ManimRenderProfile, ManimSceneBindingV2, ManimSourceClosureV1, ManimSourceShapeKindV2,
    ManimStaticShapeV2,
    planar_camera::{
        CAMERA_SCHEMA_V1, CAMERA_SOURCE_MIME_V1, CAMERA_VERSION_V2, CameraMoveV1,
        ManimCameraPoseV1, ManimCameraProfileIdV1, ManimCameraScenePlanV1,
        ManimCameraSourcePackageV1, PreparedCameraProgramV1, camera_source_bundle_v1,
        compile_camera_program_source_only, inspect_camera_source_bundle_v1,
        rebind_camera_source_v1,
    },
};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::Serialize;
use sha2::{Digest, Sha256};
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
        "Manim camera requires current full Project stamp",
    )
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let project = service.project(project_id).map_err(crate::storage_error)?;
    if project.id != project_id || RevisionStamp::from(&project) != *expected {
        return Err(stale());
    }
    Ok(project)
}

/// Construction is pure and *not* a substitute for the existing native
/// compiler/controller. A carrier or imported plan never becomes authority.
pub fn build_manim_camera_plan_v1(
    project: &Project,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimCameraScenePlanV1> {
    if RevisionStamp::from(project) != *expected {
        return Err(stale());
    }
    let scene = project
        .scenes
        .iter()
        .find(|row| row.id == scene_id)
        .ok_or_else(|| invalid("Manim camera scene not found"))?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err(reject("Planar camera V2 requires ManimCommunity renderer"));
    }
    let cameras = project
        .production_design
        .camera_reframes
        .iter()
        .filter(|move_| move_.scene_id == scene_id)
        .collect::<Vec<_>>();
    if cameras.len() != 1 || cameras[0].component_version != CAMERA_VERSION_V2 {
        return Err(reject(
            "Expected exactly one admitted Manim CameraReframe V2",
        ));
    }
    if !scene.beats.is_empty() || scene.nodes.is_empty() || scene.nodes.len() > 32 {
        return Err(reject(
            "Manim camera does not realize beats or missing/many objects",
        ));
    }
    if scene.camera.zoom != 1.0 || scene.camera.rotation_deg != 0.0 {
        return Err(reject(
            "Manim planar camera does not realize zoom or rotation",
        ));
    }
    if !project.audio.sfx_cues.is_empty()
        || !project.production_design.measured_bindings.is_empty()
        || project
            .production_design
            .heroes
            .iter()
            .any(|x| x.scene_id == scene_id)
        || project
            .production_design
            .stages
            .iter()
            .any(|x| x.scene_id == scene_id)
        || project
            .production_design
            .procedural_fields
            .iter()
            .any(|x| x.scene_id == scene_id)
    {
        return Err(reject(
            "Manim camera has no admitted other creative feature mapping",
        ));
    }
    let end = scene
        .start
        .checked_add(scene.duration)
        .map_err(|_| invalid("Manim camera scene clock overflow"))?;
    if project
        .audio
        .cues
        .iter()
        .any(|cue| cue.at >= scene.start && cue.at < end)
    {
        return Err(reject(
            "Manim camera contributes no voice or caption timing",
        ));
    }
    // The shared domain validates the exact renderer/version pair and retains
    // the legacy profile. Preserve its validator and reject unsupported source;
    // never sanitize the Project to manufacture admission.
    project.validate().map_err(|_| {
        reject("Canonical Manim V2 camera source is not yet admitted by the Project domain")
    })?;
    let reframe: &CameraReframe = cameras[0];
    reframe
        .validate(scene)
        .map_err(|_| reject("Canonical Manim V2 reframe does not pass current domain authority"))?;
    let output = project
        .deliverables
        .iter()
        .find(|p| p.id == deliverable_id)
        .ok_or_else(|| invalid("Manim camera deliverable absent"))?;
    if !output.included_scene_ids.is_empty() && !output.included_scene_ids.contains(&scene_id) {
        return Err(invalid(
            "Manim camera scene excluded from the delivery profile",
        ));
    }
    if output.width != 640
        || output.height != 360
        || output.frame_rate.num != 30
        || output.frame_rate.den != 1
        || output.container != OutputContainer::Mp4
        || output.video_codec != VideoCodec::H264
        || output.color_space != OutputColorSpace::Rec709
    {
        return Err(reject(
            "Manim camera requires saved 640x360 30fps Rec.709 H264 MP4",
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
    if matte.map(|color| color.value.as_str()) != Some("#000000") {
        return Err(reject(
            "Manim camera requires an authored opaque black matte",
        ));
    }
    let mut nodes = Vec::with_capacity(scene.nodes.len());
    for (index, node) in scene.nodes.iter().enumerate() {
        let source_kind = match node.kind.as_str() {
            "rectangle" => ManimSourceShapeKindV2::Rectangle,
            "circle" => ManimSourceShapeKindV2::Circle,
            _ => {
                return Err(reject(
                    "Only source rectangle/circle are admitted for planar camera",
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
                "Manim camera has no text, object animation, hierarchy or rotated geometry",
            ));
        }
        let fill = node
            .style
            .fill
            .clone()
            .ok_or_else(|| reject("Manim camera requires explicit source fill"))?;
        nodes.push(ManimStaticShapeV2 {
            node_id: node.id,
            display_name: node.name.clone(),
            source_kind,
            canonical_ordinal: u32::try_from(index)
                .map_err(|_| invalid("Manim camera node ordinal overflow"))?,
            z_index: node.z_index,
            x_px: node.x,
            y_px: node.y,
            width_px: node.width,
            height_px: node.height,
            fill_hex: fill,
        });
    }
    let profile = ManimRenderProfile {
        width: output.width,
        height: output.height,
        frame_rate: 30,
    };
    let plan = ManimCameraScenePlanV1 {
        schema_version: CAMERA_SCHEMA_V1,
        profile_id: ManimCameraProfileIdV1::PlanarCamera,
        output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
        binding: ManimSceneBindingV2 {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            deliverable_id,
        },
        clock: ManimFrameClockV2::from_canonical(scene.start, scene.duration, output.frame_rate)
            .map_err(|_| reject("Manim camera source duration is not frame aligned"))?,
        render_profile: profile,
        canvas: ManimCanvasV2::default(),
        background: ManimMatteV2::default(),
        camera: ManimCameraPoseV1 {
            center_x: scene.camera.center_x,
            center_y: scene.camera.center_y,
            zoom: scene.camera.zoom,
            rotation_deg: scene.camera.rotation_deg,
            safe_margin: scene.camera.safe_margin,
        },
        reframe: CameraMoveV1 {
            id: reframe.id,
            scene_id: reframe.scene_id,
            component_version: reframe.component_version,
            start_center_x: reframe.start_center_x,
            start_center_y: reframe.start_center_y,
            end_center_x: reframe.end_center_x,
            end_center_y: reframe.end_center_y,
            duration_frames: reframe.duration_frames,
        },
        nodes,
    };
    plan.validate_source_only()
        .map_err(|_| reject("Manim camera source exceeds closed profile constraints"))?;
    Ok(plan)
}

#[derive(Debug, Clone, Serialize)]
pub struct PreparedManimCameraSourceV1 {
    pub source: ManimCameraScenePlanV1,
    pub source_plan_sha256: String,
    pub source_plan_bytes: u64,
    pub native_execution_performed: bool,
    pub permissions_verified: bool,
    pub native_source_compiler_registered: bool,
    pub imported_source_authority_used: bool,
    pub losses: Vec<&'static str>,
}

/// Source compiler output tied to one canonical, already validated Project
/// stamp. This is not an executable Host ticket, source-import permission,
/// job receipt, backend readiness or evidence of physically decoded frames.
#[derive(Debug, Clone, Serialize)]
pub struct PreparedManimCameraProgramV1 {
    pub prepared: PreparedManimCameraSourceV1,
    pub trusted_compiler: PreparedCameraProgramV1,
    pub original_source_snapshot_checked: bool,
    pub program_semantically_bound_to_source: bool,
    pub native_execution_performed: bool,
    pub native_source_compiler_registered: bool,
    pub native_permissions_verified: bool,
}

/// Keep both hash domains explicit. The original Project projection stores
/// SHA256(serde_json compact Plan); the trusted compiler separately binds
/// SHA256(pretty Plan+LF) to its fixed Manim Python source. Mixing them would
/// falsely associate a source package with the wrong native camera source.
impl PreparedManimCameraSourceV1 {
    pub fn compile_trusted_program_source_only(self) -> NativeResult<PreparedManimCameraProgramV1> {
        if self.native_execution_performed
            || self.permissions_verified
            || self.native_source_compiler_registered
            || self.imported_source_authority_used
        {
            return Err(reject(
                "Untrusted externally asserted Manim camera effect or grant",
            ));
        }
        let compact = serde_json::to_vec(&self.source)
            .map_err(|_| invalid("Canonical Manim camera compact source serialization failed"))?;
        let compact_sha256 = hex::encode(Sha256::digest(&compact));
        if self.source_plan_sha256 != compact_sha256
            || self.source_plan_bytes != compact.len() as u64
        {
            return Err(invalid(
                "Canonical saved camera source hash or byte count changed before compilation",
            ));
        }
        let compiled = compile_camera_program_source_only(&self.source)
            .map_err(|_| reject("Closed Manim camera source compiler refused original plan"))?;
        let recovered: ManimCameraScenePlanV1 = serde_json::from_slice(&compiled.plan_document)
            .map_err(|_| invalid("Compiled Manim camera source plan cannot be reopened"))?;
        if recovered != self.source
            || compiled.plan_sha256 != hex::encode(Sha256::digest(&compiled.plan_document))
            || compiled.source_sha256
                != hex::encode(Sha256::digest(compiled.python_source.as_bytes()))
            || compiled.native_execution_performed
            || compiled.runtime_sample_pts_verified
        {
            return Err(invalid(
                "Manim camera compiler bytes or reopened editable plan differ from saved source",
            ));
        }
        Ok(PreparedManimCameraProgramV1 {
            prepared: self,
            trusted_compiler: compiled,
            original_source_snapshot_checked: true,
            program_semantically_bound_to_source: true,
            native_execution_performed: false,
            native_source_compiler_registered: false,
            native_permissions_verified: false,
        })
    }
}

/// Actual persisted source -> strictly compiled, inert Manim source bytes,
/// with a full Project reread after compilation to reject concurrent human
/// edits. The ORIGINAL service/Host/worker alone may schedule native effects.
pub fn prepare_saved_manim_camera_program_source_only(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<PreparedManimCameraProgramV1> {
    let before = current_project(service, project_id, expected)?;
    let prepared =
        prepare_saved_manim_camera_source(service, project_id, expected, scene_id, deliverable_id)?;
    let compiled = prepared.compile_trusted_program_source_only()?;
    if current_project(service, project_id, expected)? != before {
        return Err(stale());
    }
    Ok(compiled)
}

pub fn prepare_saved_manim_camera_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<PreparedManimCameraSourceV1> {
    let original = current_project(service, project_id, expected)?;
    let source = build_manim_camera_plan_v1(&original, expected, scene_id, deliverable_id)?;
    let serialized = serde_json::to_vec(&source)
        .map_err(|_| invalid("Manim camera plan could not be serialized"))?;
    if current_project(service, project_id, expected)? != original {
        return Err(stale());
    }
    Ok(PreparedManimCameraSourceV1 {
        source,
        source_plan_sha256: hex::encode(Sha256::digest(&serialized)),
        source_plan_bytes: serialized.len() as u64,
        native_execution_performed: false,
        permissions_verified: false,
        native_source_compiler_registered: false,
        imported_source_authority_used: false,
        losses: vec![
            "scene_video_contribution_without_voice_or_captions",
            "native_editable_source_requires_trusted_compiler_registration",
            "no_optical_zoom_3d_camera_text_graph_or_advanced_masks",
        ],
    })
}

/// The existing Store/Change alone owns a camera edit. This constructor is
/// an intended operation, not a new allocator, authority or apply shortcut.
pub fn author_one_camera_endpoint_edit(
    project: &Project,
    expected: &RevisionStamp,
    scene_id: Uuid,
    new_end_center_x: f64,
) -> NativeResult<Change> {
    if RevisionStamp::from(project) != *expected {
        return Err(stale());
    }
    let source = project
        .production_design
        .camera_reframes
        .iter()
        .find(|row| row.scene_id == scene_id && row.component_version == CAMERA_VERSION_V2)
        .ok_or_else(|| reject("No currently admitted Manim V2 camera source"))?;
    if new_end_center_x == source.end_center_x
        || !new_end_center_x.is_finite()
        || (new_end_center_x - source.start_center_x).abs() > 80.0
    {
        return Err(invalid(
            "Manim camera endpoint edit is no-op or out of bounds",
        ));
    }
    let mut edited = source.clone();
    edited.end_center_x = new_end_center_x;
    // UpsertCameraReframe atomically derives Scene.camera.center_x from edited
    // terminal center, after one CAS/grant check in the existing Store.
    Ok(Change::UpsertCameraReframe { reframe: edited })
}

/// Export the closed editable source from one saved Project revision. This
/// returns bytes only; the existing file importer owns attachment and custody.
pub fn export_saved_manim_camera_source_bundle(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<Vec<u8>> {
    let project = current_project(service, project_id, expected)?;
    let plan = build_manim_camera_plan_v1(&project, expected, scene_id, deliverable_id)?;
    let bytes = camera_source_bundle_v1(&plan)
        .map_err(|_| reject("Saved Manim camera cannot be exported by the closed compiler"))?;
    if current_project(service, project_id, expected)? != project {
        return Err(stale());
    }
    Ok(bytes)
}

fn read_saved_camera_asset_source(
    service: &StudioService,
    project: &Project,
    source_asset_id: Uuid,
) -> NativeResult<(String, ManimCameraSourcePackageV1)> {
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == source_asset_id)
        .ok_or_else(|| invalid("Manim camera source asset is not attached to this Project"))?;
    if asset.media_type != CAMERA_SOURCE_MIME_V1 {
        return Err(reject("Asset is not a closed Manim camera source package"));
    }
    let digest = asset
        .content_sha256
        .as_ref()
        .ok_or_else(|| invalid("Manim camera source asset has no content digest"))?;
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
            "Manim camera source asset bytes no longer match its digest",
        ));
    }
    let package = inspect_camera_source_bundle_v1(&bytes).map_err(|_| {
        reject("Imported Manim camera source is outside the closed compiler contract")
    })?;
    if package.plan.binding.project_id != project.id {
        return Err(reject("Manim camera source belongs to a different Project"));
    }
    Ok((digest.clone(), package))
}

/// Inspection records both historical source identity and the present Project
/// stamp. Rebinding only describes unchanged controls; it is not native grant
/// renewal, a job receipt, or authority carried by imported Python.
#[derive(Debug, Clone, Serialize)]
pub struct InspectedManimCameraSourceV1 {
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub origin_binding: ManimSceneBindingV2,
    pub current_binding: ManimSceneBindingV2,
    pub original_closure: ManimSourceClosureV1,
    pub current_closure: ManimSourceClosureV1,
    pub current_plan: ManimCameraScenePlanV1,
    pub revision_only_rebound: bool,
    pub editable_parameters: Vec<&'static str>,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
}

pub fn inspect_saved_manim_camera_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    source_asset_id: Uuid,
) -> NativeResult<InspectedManimCameraSourceV1> {
    let project = current_project(service, project_id, expected)?;
    let (digest, source) = read_saved_camera_asset_source(service, &project, source_asset_id)?;
    let current = build_manim_camera_plan_v1(
        &project,
        expected,
        source.plan.binding.scene_id,
        source.plan.binding.deliverable_id,
    )?;
    let rebound = rebind_camera_source_v1(&source, &current).map_err(|_| {
        reject("Saved Project controls differ from the attached Manim camera source")
    })?;
    if current_project(service, project_id, expected)? != project {
        return Err(stale());
    }
    Ok(InspectedManimCameraSourceV1 {
        source_asset_id,
        source_sha256: digest,
        origin_binding: source.plan.binding.clone(),
        current_binding: current.binding.clone(),
        revision_only_rebound: source.plan.binding != current.binding,
        original_closure: source.closure,
        current_closure: rebound.closure,
        current_plan: current,
        editable_parameters: vec!["reframe.end_center_x"],
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

/// A proposal for the existing service command, with its observed base. The
/// caller must apply the change using that full stamp and its existing request
/// identity; this module never creates a second mutation or dispatch path.
#[derive(Debug, Clone, Serialize)]
pub struct PreviewedManimCameraEndpointEditV1 {
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

pub fn preview_saved_manim_camera_endpoint_edit(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    original_source_asset_id: Uuid,
    edited_source_asset_id: Uuid,
) -> NativeResult<PreviewedManimCameraEndpointEditV1> {
    if original_source_asset_id == edited_source_asset_id {
        return Err(invalid(
            "An edited Manim camera source must be a separate attached asset",
        ));
    }
    let project = current_project(service, project_id, expected)?;
    let (original_digest, original) =
        read_saved_camera_asset_source(service, &project, original_source_asset_id)?;
    let current = build_manim_camera_plan_v1(
        &project,
        expected,
        original.plan.binding.scene_id,
        original.plan.binding.deliverable_id,
    )?;
    let rebound = rebind_camera_source_v1(&original, &current).map_err(|_| {
        reject("Original Manim source no longer describes the current camera controls")
    })?;
    let (edited_digest, edited) =
        read_saved_camera_asset_source(service, &project, edited_source_asset_id)?;
    let endpoint = edited.plan.reframe.end_center_x;
    let change =
        author_one_camera_endpoint_edit(&project, expected, current.binding.scene_id, endpoint)?;
    let mut candidate = project.clone();
    candidate
        .apply_change(&change)
        .map_err(|error| Error::new(ErrorCode::InvalidArgument, error.to_string()))?;
    let candidate_stamp = RevisionStamp::from(&candidate);
    let proposed = build_manim_camera_plan_v1(
        &candidate,
        &candidate_stamp,
        current.binding.scene_id,
        current.binding.deliverable_id,
    )?;
    // The candidate was constructed solely from the saved Project and one
    // admitted endpoint. Every other control, object, identity, clock, style
    // and compiler byte must match the imported closed package exactly.
    let verified_edit = rebind_camera_source_v1(&edited, &proposed).map_err(|_| {
        reject("Manim source import changes fields outside one admitted camera endpoint")
    })?;
    if current_project(service, project_id, expected)? != project {
        return Err(stale());
    }
    Ok(PreviewedManimCameraEndpointEditV1 {
        expected: expected.clone(),
        original_source_asset_id,
        edited_source_asset_id,
        original_source_sha256: original_digest,
        edited_source_sha256: edited_digest,
        current_plan_sha256: rebound.closure.plan_sha256,
        proposed_plan_sha256: verified_edit.closure.plan_sha256,
        change,
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

/// Check the editable carrier returned by an already acknowledged native job.
/// The caller still owns job/provenance and video verification. A source custody
/// error must be kept alongside the original native completion, never retried.
pub fn verify_rendered_manim_camera_source(
    output_root: &std::path::Path,
    video_relative_path: &str,
    original_plan: &ManimCameraScenePlanV1,
    native_artifact: &serde_json::Value,
) -> NativeResult<serde_json::Value> {
    use std::io::Read;

    let token = video_relative_path
        .strip_prefix("mw_")
        .and_then(|name| name.strip_suffix(".mp4"))
        .filter(|token| {
            token.len() == 32
                && token
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
        .ok_or_else(|| invalid("Manim video is not bound to one native job filename"))?;
    let package =
        motionwright_manim_profile::planar_camera::camera_source_package_v1(original_plan)
            .map_err(|_| {
                reject("Original Manim camera plan is outside the closed source profile")
            })?;
    let expected_bytes = camera_source_bundle_v1(original_plan)
        .map_err(|_| reject("Original Manim camera source could not be packaged"))?;
    let relative_path = format!("mw_{token}.mwcam.zip");
    let expected = serde_json::json!({
        "relative_path": relative_path,
        "sha256": hex::encode(Sha256::digest(&expected_bytes)),
        "bytes": expected_bytes.len(),
        "media_type": CAMERA_SOURCE_MIME_V1,
        "closure": package.closure,
    });
    if native_artifact.get("camera_source") != Some(&expected) {
        return Err(invalid(
            "Native camera source descriptor differs from the original compiled plan",
        ));
    }
    let io_error = |_| {
        Error::new(
            ErrorCode::BackendFailed,
            "Native camera source cannot be read",
        )
    };
    let root = std::fs::canonicalize(output_root).map_err(io_error)?;
    let candidate = root.join(&relative_path);
    let before = std::fs::symlink_metadata(&candidate).map_err(io_error)?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.len() != expected_bytes.len() as u64
    {
        return Err(invalid(
            "Native camera source is not the expected bounded regular file",
        ));
    }
    let verified_path = std::fs::canonicalize(&candidate).map_err(io_error)?;
    if verified_path.parent() != Some(root.as_path()) {
        return Err(invalid("Native camera source escaped its output directory"));
    }
    let file = std::fs::File::open(&verified_path).map_err(io_error)?;
    let mut bytes = Vec::with_capacity(expected_bytes.len());
    file.take(MAX_SOURCE_BUNDLE_BYTES_V2 as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(io_error)?;
    let after = std::fs::symlink_metadata(&candidate).map_err(io_error)?;
    if bytes != expected_bytes
        || !after.is_file()
        || after.file_type().is_symlink()
        || after.len() != before.len()
        || std::fs::canonicalize(&candidate).map_err(io_error)? != verified_path
    {
        return Err(invalid(
            "Native camera source changed or differs from the trusted compiler",
        ));
    }
    let mut verified = expected;
    verified["verified_path"] = serde_json::json!(verified_path);
    Ok(verified)
}
