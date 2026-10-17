//! Canonical Project projection and source-only editing for Manim planar motion.
//! There is no provider dispatch here. The existing Manim driver routes this
//! version only after its source/runtime gates have been registered.
use motionwright_domain::{
    Asset, BlendMode, CameraState, CanvasKeyframe, CanvasTransform, Change, CoordinateSpace,
    DomainError, MotionInterpolation, MotionProperty, OutputColorSpace, OutputContainer, Project,
    RendererKind, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::{
    MAX_SOURCE_BUNDLE_BYTES_V2, ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2,
    ManimRenderProfile, ManimSceneBindingV2, ManimSourceClosureV1, ManimSourceShapeKindV2,
    SOURCE_BUNDLE_MIME_V2,
    planar_motion::{
        ManimMotionInterpolationV3, ManimMotionKeyframeV3, ManimMotionProfileIdV3,
        ManimMotionPropertyV3, ManimPlanarCameraV3, ManimPlanarNodeV3, ManimPlanarPoseV3,
        ManimScenePlanV3, ManimSourcePackageV3, inspect_source_bundle_v3, rebind_source_v3,
        source_bundle_v3, source_package_v3,
    },
};
use motionwright_service::StudioService;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use uuid::Uuid;

fn invalid(reason: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, reason)
}
fn unsupported(reason: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, reason)
}
fn stale() -> Error {
    Error::new(
        ErrorCode::StaleReference,
        "Manim motion requires the exact current Project stamp",
    )
}
fn digest(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn property(property: MotionProperty) -> ManimMotionPropertyV3 {
    match property {
        MotionProperty::X => ManimMotionPropertyV3::X,
        MotionProperty::Y => ManimMotionPropertyV3::Y,
        MotionProperty::Width => ManimMotionPropertyV3::Width,
        MotionProperty::Height => ManimMotionPropertyV3::Height,
        MotionProperty::RotationDeg => ManimMotionPropertyV3::RotationDeg,
        MotionProperty::Opacity => ManimMotionPropertyV3::Opacity,
    }
}
fn canonical_key(key: &ManimMotionKeyframeV3) -> CanvasKeyframe {
    CanvasKeyframe {
        at: key.at,
        value: key.value,
        property: match key.property {
            ManimMotionPropertyV3::X => MotionProperty::X,
            ManimMotionPropertyV3::Y => MotionProperty::Y,
            ManimMotionPropertyV3::Width => MotionProperty::Width,
            ManimMotionPropertyV3::Height => MotionProperty::Height,
            ManimMotionPropertyV3::RotationDeg => MotionProperty::RotationDeg,
            ManimMotionPropertyV3::Opacity => MotionProperty::Opacity,
        },
        interpolation: match key.interpolation {
            ManimMotionInterpolationV3::Hold => MotionInterpolation::Hold,
            ManimMotionInterpolationV3::Linear => MotionInterpolation::Linear,
            ManimMotionInterpolationV3::EaseOutCubic => MotionInterpolation::EaseOutCubic,
            ManimMotionInterpolationV3::EaseInOut => MotionInterpolation::EaseInOut,
        },
    }
}
fn canonical_camera(camera: &ManimPlanarCameraV3) -> CameraState {
    CameraState {
        center_x: camera.center_x,
        center_y: camera.center_y,
        zoom: camera.zoom,
        rotation_deg: camera.rotation_deg,
        safe_margin: camera.safe_margin,
    }
}

/// Pure preflight. No source file, grant, job or receipt is created by this call.
/// The caller supplies a saved Project and its full canonical revision stamp.
pub fn build_manim_plan_v3(
    project: &Project,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimScenePlanV3> {
    if RevisionStamp::from(project) != *expected {
        return Err(stale());
    }
    let scene = project
        .scenes
        .iter()
        .find(|s| s.id == scene_id)
        .ok_or_else(|| invalid("Manim motion scene is absent"))?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err(unsupported(
            "Manim motion requires the distinct Community renderer",
        ));
    }
    let end = scene
        .start
        .checked_add(scene.duration)
        .map_err(|e| invalid(e.to_string()))?;
    // Check feature obligations before domain validation can mask a deliberately
    // unsupported renderer binding with a generic malformed-document error.
    if !scene.beats.is_empty() {
        return Err(unsupported("Manim motion has no admitted beat realization"));
    }
    if project
        .audio
        .cues
        .iter()
        .any(|cue| cue.at >= scene.start && cue.at < end)
        || !project.audio.sfx_cues.is_empty()
    {
        return Err(unsupported(
            "Manim motion has no admitted cue/SFX synchronization mapping",
        ));
    }
    if scene.camera.rotation_deg != 0.0
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|c| c.scene_id == scene_id)
    {
        return Err(unsupported(
            "Manim motion does not realize rotated or animated camera obligations",
        ));
    }
    if project
        .production_design
        .measured_bindings
        .iter()
        .any(|b| b.scene_id == scene_id)
    {
        return Err(unsupported(
            "Manim motion does not realize a measured-data binding",
        ));
    }
    if project
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
        return Err(unsupported(
            "Manim motion does not realize hero/stage/procedural obligations",
        ));
    }
    project.validate().map_err(|e| invalid(e.to_string()))?;
    let delivery = project
        .deliverables
        .iter()
        .find(|d| d.id == deliverable_id)
        .ok_or_else(|| invalid("Manim motion deliverable is absent"))?;
    if !delivery.included_scene_ids.is_empty() && !delivery.included_scene_ids.contains(&scene_id) {
        return Err(invalid(
            "Manim motion scene is excluded from the selected deliverable",
        ));
    }
    if delivery.container != OutputContainer::Mp4
        || delivery.video_codec != VideoCodec::H264
        || delivery.color_space != OutputColorSpace::Rec709
    {
        return Err(unsupported(
            "Manim motion contributes opaque Rec.709-profile H.264 MP4 only",
        ));
    }
    let palette = &project.visual_language.palette;
    let matte = palette
        .iter()
        .find(|x| x.name == "background")
        .or_else(|| palette.iter().find(|x| x.name == "surface"));
    if matte.map(|x| x.value.as_str()) != Some("#000000") {
        return Err(unsupported(
            "Manim motion requires an authored opaque black matte",
        ));
    }
    let mut nodes = Vec::with_capacity(scene.nodes.len());
    for (ordinal, node) in scene.nodes.iter().enumerate() {
        if node.coordinate_space != CoordinateSpace::ProjectPixels
            || node.parent_id.is_some()
            || !node.relations.is_empty()
            || node.style.blend_mode != BlendMode::Normal
            || node.style.stroke.is_some()
            || node.style.stroke_width != 0.0
            || node.text.is_some()
            || node.style.font_family.is_some()
            || node.style.font_size.is_some()
            || node.style.font_weight.is_some()
            || node.style.line_height.is_some()
        {
            return Err(unsupported(
                "Manim motion does not realize hierarchy, text/fonts, stroke or non-Normal blend",
            ));
        }
        let source_kind = match node.kind.as_str() {
            "shape" => ManimSourceShapeKindV2::Shape,
            "rectangle" => ManimSourceShapeKindV2::Rectangle,
            "circle" => ManimSourceShapeKindV2::Circle,
            _ => {
                return Err(unsupported(
                    "Manim motion node kind has no admitted projection",
                ));
            }
        };
        nodes.push(ManimPlanarNodeV3 {
            node_id: node.id,
            display_name: node.name.clone(),
            source_kind,
            canonical_ordinal: u32::try_from(ordinal)
                .map_err(|_| unsupported("Manim motion node count"))?,
            z_index: node.z_index,
            base: ManimPlanarPoseV3 {
                x: node.x,
                y: node.y,
                width: node.width,
                height: node.height,
                rotation_deg: node.rotation_deg,
                opacity: node.opacity,
            },
            fill_hex: node
                .style
                .fill
                .clone()
                .ok_or_else(|| unsupported("Manim motion requires explicit #RRGGBB fill"))?,
            keyframes: node
                .keyframes
                .iter()
                .map(|key| ManimMotionKeyframeV3 {
                    at: key.at,
                    property: property(key.property),
                    value: key.value,
                    interpolation: match key.interpolation {
                        MotionInterpolation::Hold => ManimMotionInterpolationV3::Hold,
                        MotionInterpolation::Linear => ManimMotionInterpolationV3::Linear,
                        MotionInterpolation::EaseOutCubic => {
                            ManimMotionInterpolationV3::EaseOutCubic
                        }
                        MotionInterpolation::EaseInOut => ManimMotionInterpolationV3::EaseInOut,
                    },
                })
                .collect(),
        });
    }
    let plan = ManimScenePlanV3 {
        schema_version: 3,
        profile_id: ManimMotionProfileIdV3::PlanarMotion,
        output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
        binding: ManimSceneBindingV2 {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            deliverable_id,
        },
        clock: ManimFrameClockV2::from_canonical(scene.start, scene.duration, delivery.frame_rate)
            .map_err(|e| unsupported(e.to_string()))?,
        render_profile: ManimRenderProfile {
            width: delivery.width,
            height: delivery.height,
            frame_rate: u32::try_from(delivery.frame_rate.num)
                .map_err(|_| unsupported("Manim motion frame rate"))?,
        },
        canvas: ManimCanvasV2::default(),
        background: ManimMatteV2::default(),
        camera: ManimPlanarCameraV3 {
            center_x: scene.camera.center_x,
            center_y: scene.camera.center_y,
            zoom: scene.camera.zoom,
            rotation_deg: scene.camera.rotation_deg,
            safe_margin: scene.camera.safe_margin,
        },
        nodes,
    };
    // This compiles and checks exact source bytes, all planned poses and archive
    // budgets before the caller can construct a native start request.
    source_package_v3(&plan).map_err(|e| unsupported(e.to_string()))?;
    Ok(plan)
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> NativeResult<Project> {
    let p = service.project(project_id).map_err(crate::storage_error)?;
    if p.id != project_id || RevisionStamp::from(&p) != *expected {
        return Err(stale());
    }
    Ok(p)
}

pub fn prepare_saved_manim_motion_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<ManimSourcePackageV3> {
    let p = current_project(service, project_id, expected)?;
    let plan = build_manim_plan_v3(&p, expected, scene_id, deliverable_id)?;
    let package = source_package_v3(&plan).map_err(|e| invalid(e.to_string()))?;
    if current_project(service, project_id, expected)? != p {
        return Err(stale());
    }
    Ok(package)
}

fn read_carrier(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
    require_capsule: bool,
) -> NativeResult<(Asset, ManimSourcePackageV3)> {
    let asset = project
        .assets
        .iter()
        .find(|a| a.id == asset_id)
        .ok_or_else(|| invalid("Manim motion source Asset is absent"))?
        .clone();
    let hash = asset
        .content_sha256
        .as_deref()
        .ok_or_else(|| invalid("Manim motion Asset has no CAS digest"))?;
    if asset.media_type != SOURCE_BUNDLE_MIME_V2 {
        return Err(invalid("Manim motion source media type"));
    }
    let handle = service.store();
    let store = handle.lock();
    if store
        .load_project(project.id)
        .map_err(crate::storage_error)?
        != *project
    {
        return Err(stale());
    }
    let bytes = store
        .read_blob(hash, MAX_SOURCE_BUNDLE_BYTES_V2 as u64)
        .map_err(crate::storage_error)?;
    drop(store);
    if digest(&bytes) != hash {
        return Err(invalid("Manim motion carrier digest mismatch"));
    }
    let package = inspect_source_bundle_v3(&bytes).map_err(|e| invalid(e.to_string()))?;
    if package.plan.binding.project_id != project.id {
        return Err(invalid("Manim motion carrier has foreign Project identity"));
    }
    if require_capsule
        && !project.production_design.capsules.iter().any(|c| {
            c.source_asset_id == asset_id
                && c.source_sha256 == hash
                && c.scene_id == package.plan.binding.scene_id
        })
    {
        return Err(invalid(
            "Manim motion origin lacks its current NativeCapsule association",
        ));
    }
    Ok((asset, package))
}

#[derive(Debug, Clone, Serialize)]
pub struct ManimMotionSourceInspectionV3 {
    pub carrier_asset_id: Uuid,
    pub carrier_sha256: String,
    pub origin_binding: ManimSceneBindingV2,
    pub current_binding: ManimSceneBindingV2,
    pub prepared_source: ManimSourceClosureV1,
    pub prepared_bundle_sha256: String,
    pub native_execution_performed: bool,
    pub imported_receipts_used_as_authority: bool,
}

pub fn inspect_saved_manim_motion_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    asset_id: Uuid,
) -> NativeResult<ManimMotionSourceInspectionV3> {
    let p = current_project(service, project_id, expected)?;
    let (asset, origin) = read_carrier(service, &p, asset_id, true)?;
    let current = build_manim_plan_v3(
        &p,
        expected,
        origin.plan.binding.scene_id,
        origin.plan.binding.deliverable_id,
    )?;
    let prepared = rebind_source_v3(&origin.plan, &current).map_err(|e| invalid(e.to_string()))?;
    let bundle = source_bundle_v3(&current).map_err(|e| invalid(e.to_string()))?;
    if current_project(service, project_id, expected)? != p {
        return Err(stale());
    }
    Ok(ManimMotionSourceInspectionV3 {
        carrier_asset_id: asset.id,
        carrier_sha256: asset.content_sha256.expect("checked digest"),
        origin_binding: origin.plan.binding,
        current_binding: current.binding,
        prepared_source: prepared.closure,
        prepared_bundle_sha256: digest(&bundle),
        native_execution_performed: false,
        imported_receipts_used_as_authority: false,
    })
}

#[derive(Debug, Clone, Serialize)]
pub struct ManimMotionImportPreviewV3 {
    pub project_stamp: RevisionStamp,
    pub origin_asset_id: Uuid,
    pub edited_asset_id: Uuid,
    pub origin_sha256: String,
    pub edited_sha256: String,
    pub change: Change,
    pub canonical_import_applied: bool,
    pub native_execution_performed: bool,
    pub losses: Vec<String>,
}

fn single_edit(current: &ManimScenePlanV3, proposed: &ManimScenePlanV3) -> NativeResult<Change> {
    if current == proposed {
        return Err(invalid("Manim motion import is a no-op"));
    }
    let scene_id = current.binding.scene_id;
    if current.camera != proposed.camera {
        let mut allowed = current.clone();
        allowed.camera = proposed.camera.clone();
        if allowed != *proposed {
            return Err(unsupported(
                "Manim motion import needs multiple canonical Changes",
            ));
        }
        return Ok(Change::SetCamera {
            scene_id,
            camera: canonical_camera(&proposed.camera),
        });
    }
    if current.nodes.len() != proposed.nodes.len() {
        return Err(unsupported("Manim motion import cannot add/remove nodes"));
    }
    let changed = current
        .nodes
        .iter()
        .zip(&proposed.nodes)
        .enumerate()
        .filter(|(_, (a, b))| a != b)
        .map(|(i, _)| i)
        .collect::<Vec<_>>();
    if changed.len() != 1 {
        return Err(unsupported(
            "Manim motion import needs exactly one node edit",
        ));
    }
    let i = changed[0];
    let before = &current.nodes[i];
    let after = &proposed.nodes[i];
    let mut allowed = current.clone();
    allowed.nodes[i] = after.clone();
    if allowed != *proposed {
        return Err(unsupported(
            "Manim motion import changed clock/profile/identity",
        ));
    }
    let mut identity = before.clone();
    identity.base = after.base;
    identity.keyframes = after.keyframes.clone();
    if identity != *after {
        return Err(unsupported(
            "Manim motion import changed node identity/style/order",
        ));
    }
    if before.base != after.base {
        if before.keyframes != after.keyframes {
            return Err(unsupported(
                "Manim motion import combines base and key edits",
            ));
        }
        let p = after.base;
        return Ok(Change::TransformCanvasNode {
            scene_id,
            node_id: after.node_id,
            transform: CanvasTransform {
                x: p.x,
                y: p.y,
                width: p.width,
                height: p.height,
                rotation_deg: p.rotation_deg,
                opacity: p.opacity,
            },
        });
    }
    let key_map = |n: &ManimPlanarNodeV3| {
        n.keyframes
            .iter()
            .map(|k| ((k.property, k.at), k.clone()))
            .collect::<BTreeMap<_, _>>()
    };
    let from = key_map(before);
    let to = key_map(after);
    if from.keys().any(|key| !to.contains_key(key)) {
        return Err(unsupported(
            "Manim motion import cannot delete or move a key in this profile",
        ));
    }
    let edits = to
        .iter()
        .filter(|(key, value)| from.get(*key) != Some(*value))
        .collect::<Vec<_>>();
    if edits.len() != 1 {
        return Err(unsupported("Manim motion import needs one key upsert"));
    }
    Ok(Change::SetCanvasKeyframe {
        scene_id,
        node_id: after.node_id,
        keyframe: canonical_key(edits[0].1),
    })
}

pub fn preview_saved_manim_motion_edit(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    origin_asset_id: Uuid,
    edited_asset_id: Uuid,
) -> NativeResult<ManimMotionImportPreviewV3> {
    if origin_asset_id == edited_asset_id {
        return Err(invalid(
            "Manim motion edit requires a distinct immutable carrier",
        ));
    }
    let p = current_project(service, project_id, expected)?;
    let (origin_asset, origin) = read_carrier(service, &p, origin_asset_id, true)?;
    let (edited_asset, edited) = read_carrier(service, &p, edited_asset_id, false)?;
    let current = build_manim_plan_v3(
        &p,
        expected,
        origin.plan.binding.scene_id,
        origin.plan.binding.deliverable_id,
    )?;
    rebind_source_v3(&origin.plan, &current).map_err(|e| invalid(e.to_string()))?;
    if edited.plan.binding != origin.plan.binding && edited.plan.binding != current.binding {
        return Err(invalid("Manim motion edited carrier has a foreign binding"));
    }
    let mut proposed = edited.plan;
    proposed.binding = current.binding.clone();
    let change = single_edit(&current, &proposed)?;
    let mut candidate = p.clone();
    candidate.apply_change(&change).map_err(|e| match e {
        DomainError::Locked(_) => Error::new(
            ErrorCode::PermissionDenied,
            "Canonical Project/node locks deny the Manim motion edit",
        ),
        e => invalid(e.to_string()),
    })?;
    let projected = build_manim_plan_v3(
        &candidate,
        &RevisionStamp::from(&candidate),
        current.binding.scene_id,
        current.binding.deliverable_id,
    )?;
    proposed.binding = projected.binding.clone();
    if projected != proposed {
        return Err(unsupported(
            "Manim motion edit is not one exact canonical Change",
        ));
    }
    if current_project(service, project_id, expected)? != p {
        return Err(stale());
    }
    Ok(ManimMotionImportPreviewV3 {
        project_stamp: expected.clone(),
        origin_asset_id,
        edited_asset_id,
        origin_sha256: origin_asset.content_sha256.expect("checked digest"),
        edited_sha256: edited_asset.content_sha256.expect("checked digest"),
        change,
        canonical_import_applied: false,
        native_execution_performed: false,
        losses: vec![
            "One existing canonical Change only; no hidden multi-change transaction".into(),
            "Generated Python is recompiled and never imported as executable source".into(),
            "NativeCapsule reassociation is a separate explicit canonical operation".into(),
        ],
    })
}

pub fn apply_saved_manim_motion_edit(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    request_id: &str,
    origin_asset_id: Uuid,
    edited_asset_id: Uuid,
) -> NativeResult<Project> {
    let preview = preview_saved_manim_motion_edit(
        service,
        project_id,
        expected,
        origin_asset_id,
        edited_asset_id,
    )?;
    // Recomputed from original immutable CAS and the current full stamp. No
    // client-supplied preview/Change or historical native receipt is authority.
    Ok(service
        .apply(project_id, expected, request_id, &preview.change)
        .map_err(crate::storage_error)?
        .project)
}
