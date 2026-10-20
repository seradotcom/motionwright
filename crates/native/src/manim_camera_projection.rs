//! Closed source-only projection of saved CameraReframe V2 to Manim Community.
//! Reuses existing Project, revision stamp, Store and public Manim static shapes.
//! This module does NOT compile Python, perform native dispatch or grant rights.
//! The existing domain must admit the exact version-2/ManimCommunity pair first.
use motionwright_domain::{
    BlendMode, CameraReframe, Change, CoordinateSpace, OutputColorSpace, OutputContainer, Project,
    RendererKind, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::{
    ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2, ManimRenderProfile,
    ManimSceneBindingV2, ManimSourceShapeKindV2, ManimStaticShapeV2,
    planar_camera::{
        CAMERA_SCHEMA_V1, CAMERA_VERSION_V2, CameraMoveV1, ManimCameraPoseV1,
        ManimCameraProfileIdV1, ManimCameraScenePlanV1, PreparedCameraProgramV1,
        compile_camera_program_source_only,
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
