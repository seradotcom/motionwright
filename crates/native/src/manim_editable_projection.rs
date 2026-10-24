//! Canonical editable Manim source using the existing Project, Asset/CAS and
//! single domain Change contracts. Imported Python has no execution authority.
use crate::NativeResult;
use motionwright_domain::{
    BlendMode, CanvasTransform, Change, CoordinateSpace, FramingStrategy, OutputColorSpace,
    OutputContainer, Project, RendererKind, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::{
    ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2, ManimRenderProfile,
    ManimSceneBindingV2, SOURCE_BUNDLE_MIME_V2, editable_scene as profile,
};
use motionwright_service::{StudioService, brand_font_source};
use semwright_native_sdk::{Error, ErrorCode};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::OpenOptions,
    io::{Read, Write},
    path::Path,
};
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
        "Editable Manim requires the current full Project stamp",
    )
}
fn current(service: &StudioService, id: Uuid, expected: &RevisionStamp) -> NativeResult<Project> {
    let project = service.project(id).map_err(crate::storage_error)?;
    if project.id != id || RevisionStamp::from(&project) != *expected {
        return Err(stale());
    }
    Ok(project)
}
fn matte(project: &Project) -> Option<&str> {
    project
        .visual_language
        .palette
        .iter()
        .find(|row| row.name.eq_ignore_ascii_case("background"))
        .or_else(|| {
            project
                .visual_language
                .palette
                .iter()
                .find(|row| row.name.eq_ignore_ascii_case("surface"))
        })
        .map(|row| row.value.as_str())
}
/// Deliberate typed routing, never an error-triggered fallback to legacy V1.
pub fn requires_editable_profile(project: &Project, scene_id: Uuid, deliverable_id: Uuid) -> bool {
    project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .is_some_and(|scene| {
            scene
                .nodes
                .iter()
                .any(|node| node.kind == "text" || node.rotation_deg != 0.0 || node.opacity != 1.0)
        })
        || matte(project) != Some("#000000")
        || project
            .deliverables
            .iter()
            .find(|row| row.id == deliverable_id)
            .is_some_and(|output| u64::from(output.width) * 9 != u64::from(output.height) * 16)
}

pub fn build_manim_editable_plan(
    project: &Project,
    expected: &RevisionStamp,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<profile::Plan> {
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
        .ok_or_else(|| invalid("Editable Manim scene is absent"))?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err(reject("Editable V3 requires Manim Community"));
    }
    if scene.camera.center_x != 960.0
        || scene.camera.center_y != 540.0
        || scene.camera.zoom != 1.0
        || scene.camera.rotation_deg != 0.0
        || !scene.beats.is_empty()
        || scene.nodes.is_empty()
        || scene.nodes.len() > 128
    {
        return Err(reject(
            "Editable V3 requires a fixed centered camera and one bounded unbroken scene",
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
            "Editable V3 cannot discard creative components, camera motion or measured bindings",
        ));
    }
    let end = scene
        .start
        .checked_add(scene.duration)
        .map_err(|_| invalid("Editable scene clock overflow"))?;
    if !project.audio.sfx_cues.is_empty()
        || project
            .audio
            .cues
            .iter()
            .any(|cue| cue.at >= scene.start && cue.at < end)
    {
        return Err(reject(
            "Editable V3 is a video contribution without audio or captions",
        ));
    }
    let output = project
        .deliverables
        .iter()
        .find(|row| row.id == deliverable_id)
        .ok_or_else(|| invalid("Editable deliverable is absent"))?;
    if !output.included_scene_ids.is_empty() && !output.included_scene_ids.contains(&scene_id) {
        return Err(invalid("Editable scene is excluded from deliverable"));
    }
    if output.frame_rate.den != 1
        || output.container != OutputContainer::Mp4
        || output.video_codec != VideoCodec::H264
        || output.color_space != OutputColorSpace::Rec709
        || output.burn_in_captions
    {
        return Err(reject(
            "Editable V3 requires integral fps and Rec.709 H264 MP4 without burned captions",
        ));
    }
    let mut fonts = BTreeMap::new();
    let mut nodes = Vec::with_capacity(scene.nodes.len());
    for (ordinal, node) in scene.nodes.iter().enumerate() {
        if node.coordinate_space != CoordinateSpace::ProjectPixels
            || node.parent_id.is_some()
            || !node.relations.is_empty()
            || !node.keyframes.is_empty()
            || node.style.blend_mode != BlendMode::Normal
            || node.style.stroke.is_some()
            || node.style.stroke_width != 0.0
        {
            return Err(reject(
                "Editable V3 cannot discard hierarchy, relations, animation, blend or stroke",
            ));
        }
        let kind = match node.kind.as_str() {
            "shape" => profile::NodeKind::Shape,
            "rectangle" => profile::NodeKind::Rectangle,
            "circle" => profile::NodeKind::Circle,
            "text" => profile::NodeKind::Text,
            _ => return Err(reject("Node kind is outside closed editable V3")),
        };
        let typography = if kind == profile::NodeKind::Text {
            let family =
                node.style.font_family.as_deref().ok_or_else(|| {
                    reject("Editable text requires an explicit verified font family")
                })?;
            let font = brand_font_source(project, family).map_err(|_| {
                reject("Editable font needs one cleared attached Asset/CAS identity")
            })?;
            let format = match font.media_type.as_str() {
                "font/ttf" => profile::FontFormat::Ttf,
                "font/otf" => profile::FontFormat::Otf,
                _ => return Err(reject("Editable font media type is unsupported")),
            };
            let source = profile::FontSource {
                asset_id: font.asset_id,
                family: font.family,
                sha256: font.sha256,
                format,
            };
            if let Some(before) = fonts.insert(source.asset_id, source.clone())
                && before != source
            {
                return Err(reject("Conflicting font identity"));
            }
            Some(profile::Typography {
                text: output
                    .text_overrides
                    .get(&node.id)
                    .or(node.text.as_ref())
                    .cloned()
                    .ok_or_else(|| reject("Editable text is absent"))?,
                font_asset_id: source.asset_id,
                font_size_px: node
                    .style
                    .font_size
                    .ok_or_else(|| reject("Editable text requires explicit font size"))?,
                weight: node
                    .style
                    .font_weight
                    .ok_or_else(|| reject("Editable text requires explicit font weight"))?,
                line_height: node
                    .style
                    .line_height
                    .ok_or_else(|| reject("Editable text requires explicit line height"))?,
            })
        } else {
            if node.text.is_some()
                || node.style.font_family.is_some()
                || node.style.font_size.is_some()
                || node.style.font_weight.is_some()
                || node.style.line_height.is_some()
            {
                return Err(reject("Editable shapes cannot discard text or typography"));
            }
            None
        };
        nodes.push(profile::Node {
            node_id: node.id,
            display_name: node.name.clone(),
            kind,
            canonical_ordinal: ordinal as u32,
            z_index: node.z_index,
            x_px: node.x,
            y_px: node.y,
            width_px: node.width,
            height_px: node.height,
            rotation_deg: node.rotation_deg,
            opacity: node.opacity,
            fill_hex: node
                .style
                .fill
                .clone()
                .ok_or_else(|| reject("Editable node requires authored fill"))?,
            typography,
        });
    }
    let plan = profile::Plan {
        schema_version: 3,
        profile_id: profile::ProfileId::EditableText,
        output_role: ManimOutputRoleV2::SceneVideoContributionWithoutAudioOrCaptions,
        binding: ManimSceneBindingV2 {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            scene_id,
            deliverable_id,
        },
        clock: ManimFrameClockV2::from_canonical(scene.start, scene.duration, output.frame_rate)
            .map_err(|_| reject("Editable source has a nonintegral or out-of-budget clock"))?,
        render_profile: ManimRenderProfile {
            width: output.width,
            height: output.height,
            frame_rate: u32::try_from(output.frame_rate.num)
                .map_err(|_| reject("Editable fps out of bounds"))?,
        },
        canvas: ManimCanvasV2::default(),
        framing: match output.framing_strategy {
            FramingStrategy::Replan => profile::Framing::Replan,
            FramingStrategy::Crop => profile::Framing::Crop,
        },
        safe_margin: scene.camera.safe_margin,
        background: ManimMatteV2 {
            color: matte(project)
                .ok_or_else(|| reject("Editable source requires an explicitly authored matte"))?
                .into(),
            ..ManimMatteV2::default()
        },
        fonts: fonts.into_values().collect(),
        nodes,
    };
    profile::validate_plan(&plan)
        .map_err(|_| reject("Canonical source exceeds closed editable V3 bounds"))?;
    Ok(plan)
}
fn verified_fonts(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    plan: &profile::Plan,
) -> NativeResult<BTreeMap<String, Vec<u8>>> {
    let mut result = BTreeMap::new();
    for font in &plan.fonts {
        let verified = service
            .verified_brand_font(project_id, expected, &font.family)
            .map_err(crate::storage_error)?;
        if verified.source.asset_id != font.asset_id || verified.source.sha256 != font.sha256 {
            return Err(stale());
        }
        profile::inspect_font(plan,font,&verified.bytes).map_err(|_|reject("Actual OpenType font lacks required family, weight, glyphs or embedding permission"))?;
        result.insert(font.archive_path(), verified.bytes);
    }
    Ok(result)
}
pub fn prepare_saved_manim_editable_source(
    service: &StudioService,
    id: Uuid,
    expected: &RevisionStamp,
    scene: Uuid,
    deliverable: Uuid,
) -> NativeResult<profile::Package> {
    let project = current(service, id, expected)?;
    let plan = build_manim_editable_plan(&project, expected, scene, deliverable)?;
    let fonts = verified_fonts(service, id, expected, &plan)?;
    let package = profile::source_package(&plan, &fonts)
        .map_err(|_| reject("Editable compiler refused canonical source"))?;
    if current(service, id, expected)? != project {
        return Err(stale());
    }
    Ok(package)
}
pub fn export_saved_manim_editable_source_bundle(
    service: &StudioService,
    id: Uuid,
    expected: &RevisionStamp,
    scene: Uuid,
    deliverable: Uuid,
) -> NativeResult<Vec<u8>> {
    let package = prepare_saved_manim_editable_source(service, id, expected, scene, deliverable)?;
    let fonts = package
        .entries
        .iter()
        .filter(|(name, _)| name.starts_with("fonts/"))
        .map(|(name, data)| (name.clone(), data.clone()))
        .collect();
    profile::source_bundle(&package.plan, &fonts)
        .map_err(|_| reject("Editable source cannot be packaged"))
}

fn read_owned_file(root: &Path, name: &str, maximum: usize) -> NativeResult<Vec<u8>> {
    let io_error = |_| {
        Error::new(
            ErrorCode::BackendFailed,
            "Editable native artifact cannot be read",
        )
    };
    if name.is_empty() || name.contains(['/', '\\']) || name == "." || name == ".." {
        return Err(invalid("Editable artifact name is not a basename"));
    }
    let root = std::fs::canonicalize(root).map_err(io_error)?;
    let path = root.join(name);
    let before = std::fs::symlink_metadata(&path).map_err(io_error)?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.len() == 0
        || before.len() > maximum as u64
    {
        return Err(invalid("Editable artifact must be a bounded regular file"));
    }
    let canonical = std::fs::canonicalize(&path).map_err(io_error)?;
    if canonical.parent() != Some(root.as_path()) {
        return Err(invalid("Editable artifact escaped output root"));
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    std::fs::File::open(&canonical)
        .map_err(io_error)?
        .take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(io_error)?;
    let after = std::fs::symlink_metadata(&path).map_err(io_error)?;
    if !after.is_file()
        || after.file_type().is_symlink()
        || after.len() != before.len()
        || bytes.len() as u64 != before.len()
        || std::fs::canonicalize(&path).map_err(io_error)? != canonical
    {
        return Err(invalid("Editable artifact changed during bounded readback"));
    }
    Ok(bytes)
}
/// Only canonical CAS-verified package data reaches this pre-effect staging.
/// A present identical font input is read, never rewritten; native requests are
/// not performed or retried here. The existing output mount supplies all files.
pub(crate) fn stage_font_inputs(root: &Path, package: &profile::Package) -> NativeResult<()> {
    let meta = std::fs::symlink_metadata(root)
        .map_err(|_| invalid("Editable font input root is absent"))?;
    if !meta.is_dir() || meta.file_type().is_symlink() {
        return Err(invalid(
            "Editable font input root is not an owner directory",
        ));
    }
    let root = std::fs::canonicalize(root)
        .map_err(|_| invalid("Editable font input root cannot be resolved"))?;
    for font in &package.plan.fonts {
        let bytes = package
            .entries
            .get(&font.archive_path())
            .ok_or_else(|| invalid("Verified editable font missing"))?;
        profile::inspect_font(&package.plan, font, bytes)
            .map_err(|_| reject("Editable font staging requires verified bytes"))?;
        let name = format!("mw_font_{}", font.basename());
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(root.join(&name))
        {
            Ok(mut file) => {
                file.write_all(bytes)
                    .and_then(|_| file.sync_all())
                    .map_err(|_| invalid("Editable font staging write failed"))?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err(invalid("Editable font staging cannot create owner input")),
        }
        if read_owned_file(&root, &name, profile::MAX_FONT_BYTES)? != *bytes {
            return Err(invalid(
                "Existing editable font input differs from canonical bytes",
            ));
        }
    }
    Ok(())
}
fn read_attached(
    service: &StudioService,
    project: &Project,
    asset_id: Uuid,
) -> NativeResult<(String, profile::Package)> {
    let asset = project
        .assets
        .iter()
        .find(|row| row.id == asset_id)
        .ok_or_else(|| invalid("Editable source is not attached to Project"))?;
    if asset.media_type != SOURCE_BUNDLE_MIME_V2 {
        return Err(reject("Attached source has unsupported media type"));
    }
    let digest = asset
        .content_sha256
        .as_ref()
        .ok_or_else(|| invalid("Editable source has no CAS digest"))?;
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
            .read_blob(digest, profile::MAX_BUNDLE_BYTES as u64)
            .map_err(crate::storage_error)?
    };
    if hex::encode(Sha256::digest(&bytes)) != *digest {
        return Err(invalid("Editable source differs from attached CAS digest"));
    }
    let package = profile::inspect_source_bundle(&bytes)
        .map_err(|_| reject("Imported editable source differs from closed compiler"))?;
    if package.plan.binding.project_id != project.id {
        return Err(reject("Editable source belongs to another Project"));
    }
    Ok((digest.clone(), package))
}
#[derive(Debug, Clone, Serialize)]
pub struct InspectedEditableSource {
    pub source_asset_id: Uuid,
    pub source_sha256: String,
    pub origin_binding: ManimSceneBindingV2,
    pub current_binding: ManimSceneBindingV2,
    pub original_closure: motionwright_manim_profile::ManimSourceClosureV1,
    pub current_closure: motionwright_manim_profile::ManimSourceClosureV1,
    pub current_plan: profile::Plan,
    pub revision_only_rebound: bool,
    pub editable_parameters: Vec<&'static str>,
    pub native_execution_performed: bool,
    pub imported_source_authority_used: bool,
}
pub fn inspect_saved_manim_editable_source(
    service: &StudioService,
    id: Uuid,
    expected: &RevisionStamp,
    asset_id: Uuid,
) -> NativeResult<InspectedEditableSource> {
    let project = current(service, id, expected)?;
    let (digest, origin) = read_attached(service, &project, asset_id)?;
    let plan = build_manim_editable_plan(
        &project,
        expected,
        origin.plan.binding.scene_id,
        origin.plan.binding.deliverable_id,
    )?;
    let fonts = verified_fonts(service, id, expected, &plan)?;
    let rebound = profile::rebind_source(&origin, &plan, &fonts)
        .map_err(|_| reject("Editable source differs from current saved controls"))?;
    if current(service, id, expected)? != project {
        return Err(stale());
    }
    Ok(InspectedEditableSource {
        source_asset_id: asset_id,
        source_sha256: digest,
        origin_binding: origin.plan.binding.clone(),
        current_binding: plan.binding.clone(),
        revision_only_rebound: origin.plan.binding != plan.binding,
        original_closure: origin.closure,
        current_closure: rebound.closure,
        current_plan: plan,
        editable_parameters: vec!["one_node.transform", "one_node.text", "one_node.style"],
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EditableProperty {
    Transform,
    Text,
    Style,
}
#[derive(Debug, Clone, Serialize)]
pub struct PreviewedEditableEdit {
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
/// Preview exactly one normal Change; the original service owns locks/CAS and
/// request idempotency. This function never applies or retries a mutation.
pub fn preview_saved_manim_editable_edit(
    service: &StudioService,
    id: Uuid,
    expected: &RevisionStamp,
    original_id: Uuid,
    edited_id: Uuid,
    node_id: Uuid,
    property: EditableProperty,
) -> NativeResult<PreviewedEditableEdit> {
    if original_id == edited_id {
        return Err(invalid("Edited source must be a separately attached Asset"));
    }
    let project = current(service, id, expected)?;
    let (original_digest, origin) = read_attached(service, &project, original_id)?;
    let plan = build_manim_editable_plan(
        &project,
        expected,
        origin.plan.binding.scene_id,
        origin.plan.binding.deliverable_id,
    )?;
    let fonts = verified_fonts(service, id, expected, &plan)?;
    let rebound = profile::rebind_source(&origin, &plan, &fonts)
        .map_err(|_| reject("Original editable source no longer describes saved controls"))?;
    let (edited_digest, edited) = read_attached(service, &project, edited_id)?;
    let node = edited
        .plan
        .nodes
        .iter()
        .find(|row| row.node_id == node_id)
        .ok_or_else(|| invalid("Edited source node absent"))?;
    let before = plan
        .nodes
        .iter()
        .find(|row| row.node_id == node_id)
        .ok_or_else(|| invalid("Original source node absent"))?;
    if node == before {
        return Err(invalid("Editable import is a no-op"));
    }
    let scene_id = plan.binding.scene_id;
    let change = match property {
        EditableProperty::Transform => Change::TransformCanvasNode {
            scene_id,
            node_id,
            transform: CanvasTransform {
                x: node.x_px,
                y: node.y_px,
                width: node.width_px,
                height: node.height_px,
                rotation_deg: node.rotation_deg,
                opacity: node.opacity,
            },
        },
        EditableProperty::Text => Change::UpdateCanvasText {
            scene_id,
            node_id,
            text: node.typography.as_ref().map(|row| row.text.clone()),
        },
        EditableProperty::Style => {
            let mut style = project
                .scenes
                .iter()
                .find(|row| row.id == scene_id)
                .and_then(|scene| scene.nodes.iter().find(|row| row.id == node_id))
                .ok_or_else(|| invalid("Canonical editable node absent"))?
                .style
                .clone();
            style.fill = Some(node.fill_hex.clone());
            if let Some(text) = &node.typography {
                let font = edited
                    .plan
                    .fonts
                    .iter()
                    .find(|row| row.asset_id == text.font_asset_id)
                    .ok_or_else(|| invalid("Edited font absent"))?;
                style.font_family = Some(font.family.clone());
                style.font_size = Some(text.font_size_px);
                style.font_weight = Some(text.weight);
                style.line_height = Some(text.line_height);
            }
            Change::UpdateCanvasStyle {
                scene_id,
                node_id,
                style,
            }
        }
    };
    let mut candidate = project.clone();
    candidate
        .apply_change(&change)
        .map_err(|error| Error::new(ErrorCode::InvalidArgument, error.to_string()))?;
    let proposed = build_manim_editable_plan(
        &candidate,
        &RevisionStamp::from(&candidate),
        scene_id,
        plan.binding.deliverable_id,
    )?;
    let proposed_fonts = verified_fonts(service, id, expected, &proposed)?;
    let verified = profile::rebind_source(&edited, &proposed, &proposed_fonts).map_err(|_| {
        reject("Editable import changes fields outside the selected one-node Change")
    })?;
    if current(service, id, expected)? != project {
        return Err(stale());
    }
    Ok(PreviewedEditableEdit {
        expected: expected.clone(),
        original_source_asset_id: original_id,
        edited_source_asset_id: edited_id,
        original_source_sha256: original_digest,
        edited_source_sha256: edited_digest,
        current_plan_sha256: rebound.closure.plan_sha256,
        proposed_plan_sha256: verified.closure.plan_sha256,
        change,
        native_execution_performed: false,
        imported_source_authority_used: false,
    })
}

pub fn verify_rendered_manim_editable_source(
    output_root: &Path,
    video_relative: &str,
    plan: &profile::Plan,
    artifact: &Value,
) -> NativeResult<Value> {
    let token = video_relative
        .rsplit('/')
        .next()
        .unwrap_or_default()
        .strip_prefix("mw_")
        .and_then(|name| name.strip_suffix(".mp4"))
        .filter(|value| {
            value.len() == 32
                && value
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        })
        .ok_or_else(|| invalid("Editable video is not bound to a native job"))?;
    if video_relative
        != format!(
            "videos/mw_{token}/{}/mw_{token}.mp4",
            plan.render_profile.quality_directory()
        )
    {
        return Err(invalid("Editable video path/profile binding differs"));
    }
    let source_name = format!("mw_{token}.mwedit.zip");
    let bytes = read_owned_file(output_root, &source_name, profile::MAX_BUNDLE_BYTES)?;
    let package = profile::inspect_source_bundle(&bytes)
        .map_err(|_| reject("Native editable carrier differs from closed compiler"))?;
    if package.plan != *plan {
        return Err(invalid(
            "Native editable carrier differs from original canonical plan",
        ));
    }
    let descriptor = json!({"relative_path":source_name,"sha256":hex::encode(Sha256::digest(&bytes)),"bytes":bytes.len(),
        "media_type":SOURCE_BUNDLE_MIME_V2,"closure":package.closure});
    if artifact.get("editable_source") != Some(&descriptor) {
        return Err(invalid("Native editable source descriptor changed"));
    }
    let getter_name = format!("mw_{token}.objects.json");
    let getters = read_owned_file(
        output_root,
        &getter_name,
        profile::MAX_NATIVE_READBACK_BYTES,
    )?;
    let getter_descriptor = json!({"relative_path":getter_name,"sha256":hex::encode(Sha256::digest(&getters)),"bytes":getters.len(),"media_type":"application/json"});
    if artifact.get("editable_getters") != Some(&getter_descriptor) {
        return Err(invalid("Native editable getter descriptor changed"));
    }
    let observed = profile::inspect_native_readback(plan, &getters)
        .map_err(|_| reject("Native editable properties differ from canonical source"))?;
    for (actual, font) in observed.fonts.iter().zip(&plan.fonts) {
        if actual.bytes != package.entries[&font.archive_path()].len() as u64 {
            return Err(invalid(
                "Native editable font readback differs from retained source",
            ));
        }
    }
    Ok(
        json!({"source":descriptor,"native_getters":getter_descriptor,"observed_object_count":observed.objects.len(),
        "observed_font_count":observed.fonts.len(),"frame_count_required":plan.clock.frame_count,
        "source_reopened":false,"source_bundle_inspected":true,"native_object_getters_verified":true,"decoded_video_pts_verified":false,"human_approval_asserted":false}),
    )
}

/// A private input reference to bytes already inside the original output mount.
/// This describes a bounded data carrier; it never authorizes an OS path or tool.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct OriginalEditableBundleInput {
    pub root: OriginalEditableBundleRoot,
    pub relative_path: String,
    pub sha256: String,
    pub bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum OriginalEditableBundleRoot {
    Output,
}

fn original_bundle(
    output_root: &Path,
    input: &OriginalEditableBundleInput,
) -> NativeResult<profile::Package> {
    let name = input.relative_path.as_str();
    if name.len() < 12
        || name.len() > 128
        || !name.as_bytes()[0].is_ascii_alphanumeric()
        || !name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
        || !name.ends_with(".mwedit.zip")
        || input.sha256.len() != 64
        || !input
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || input.bytes == 0
        || input.bytes > profile::MAX_BUNDLE_BYTES as u64
    {
        return Err(invalid(
            "Original editable input is outside the closed output-mount contract",
        ));
    }
    let bytes = read_owned_file(output_root, name, profile::MAX_BUNDLE_BYTES)?;
    if bytes.len() as u64 != input.bytes || hex::encode(Sha256::digest(&bytes)) != input.sha256 {
        return Err(invalid(
            "Original editable input changed before bounded inspection",
        ));
    }
    // The profile inspector regenerates the complete canonical package and
    // compares scene.py, all font/config bytes, entries and the exact ZIP encoding.
    profile::inspect_source_bundle(&bytes)
        .map_err(|_| reject("Original editable input is not a closed canonical source bundle"))
}

/// Prepare an independent source open from the original exported bytes, while
/// keeping the current persisted Project stamp as the only semantic authority.
/// No CAS fallback replaces any font/program/config entry in this carrier.
pub fn prepare_saved_manim_editable_bundle_input(
    service: &StudioService,
    id: Uuid,
    expected: &RevisionStamp,
    scene: Uuid,
    deliverable: Uuid,
    output_root: &Path,
    input: &OriginalEditableBundleInput,
) -> NativeResult<profile::Package> {
    let project = current(service, id, expected)?;
    let package = original_bundle(output_root, input)?;
    let plan = build_manim_editable_plan(&project, expected, scene, deliverable)?;
    if package.plan != plan {
        return Err(reject(
            "Original editable bundle differs from the complete saved plan",
        ));
    }
    if current(service, id, expected)? != project {
        return Err(stale());
    }
    Ok(package)
}

/// Reconcile the driver's observed source input with the original retained ZIP.
/// A failure is post-render custody failure; it must not erase or retry the job.
pub(crate) fn verify_rendered_manim_editable_bundle_input(
    output_root: &Path,
    plan: &profile::Plan,
    input: &OriginalEditableBundleInput,
    artifact: &Value,
) -> NativeResult<Value> {
    let package = original_bundle(output_root, input)?;
    if package.plan != *plan {
        return Err(invalid(
            "Original editable input plan changed during the native job",
        ));
    }
    let fonts = package
        .plan
        .fonts
        .iter()
        .map(|font| {
            json!({"sha256":font.sha256,
               "bytes":package.entries[&font.archive_path()].len()})
        })
        .collect::<Vec<_>>();
    let expected = json!({
        "input":input,
        "source_sha256":package.closure.source_sha256,
        "fontconfig_sha256":hex::encode(Sha256::digest(&package.entries["fonts.conf"])),
        "fonts":fonts,
    });
    if artifact.get("editable_input_source") != Some(&expected)
        || artifact.pointer("/editable_source/sha256") != Some(&json!(input.sha256))
        || artifact.pointer("/editable_source/bytes") != Some(&json!(input.bytes))
        || artifact.pointer("/editable_source/closure") != Some(&json!(package.closure))
    {
        return Err(invalid(
            "Native editable input or retained original source differs",
        ));
    }
    Ok(expected)
}
