use motionwright_domain::{CanvasNode, CoordinateSpace, Project, RendererKind, Scene};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use uuid::Uuid;

const PROJECT_WIDTH: f64 = 1920.0;
const PROJECT_HEIGHT: f64 = 1080.0;
const BLENDER_SCALE: f64 = 0.01;
const BLENDER_FLAT_DEPTH_SCALE: f64 = 0.005;
const MANIM_FRAME_WIDTH: f64 = 14.222_222_222_2;
const MANIM_FRAME_HEIGHT: f64 = 8.0;
const MAX_RENDERER_NODES: usize = 512;

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn unsupported(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}

fn renderer_scene<'a>(
    project: &'a Project,
    scene_id: Uuid,
    renderer: &RendererKind,
) -> NativeResult<&'a Scene> {
    project
        .validate()
        .map_err(|error| invalid(format!("Motionwright project is invalid: {error}")))?;
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .ok_or_else(|| invalid("Renderer scene not found"))?;
    if &scene.renderer != renderer {
        return Err(invalid(
            "Scene renderer does not match the requested projection",
        ));
    }
    if scene.nodes.is_empty() {
        return Err(unsupported("Renderer scene has no semantic canvas nodes"));
    }
    if scene.nodes.len() > MAX_RENDERER_NODES {
        return Err(unsupported(
            "Renderer scene exceeds the bounded node budget",
        ));
    }
    Ok(scene)
}

fn ensure_flat_semantics(node: &CanvasNode) -> NativeResult<()> {
    if !node.keyframes.is_empty() {
        return Err(unsupported(format!(
            "Canvas node {} has authored motion that requires an explicit renderer-time mapping",
            node.id
        )));
    }
    if node.coordinate_space != CoordinateSpace::ProjectPixels {
        return Err(unsupported(format!(
            "Canvas node {} does not use project-pixel coordinates",
            node.id
        )));
    }
    if node.parent_id.is_some() || !node.relations.is_empty() {
        return Err(unsupported(format!(
            "Canvas node {} has hierarchy/relations that need an explicit renderer mapping",
            node.id
        )));
    }
    if node.style.blend_mode != motionwright_domain::BlendMode::Normal {
        return Err(unsupported(format!(
            "Canvas node {} uses a blend mode that this native contribution cannot preserve",
            node.id
        )));
    }
    if node.opacity != 1.0 || node.style.stroke_width != 0.0 || node.style.stroke.is_some() {
        return Err(unsupported(format!(
            "Canvas node {} has opacity/stroke state that this native contribution cannot preserve",
            node.id
        )));
    }
    Ok(())
}

fn hex_color(value: &str) -> NativeResult<[f64; 4]> {
    let hex = value
        .strip_prefix('#')
        .ok_or_else(|| unsupported("Blender material fill must be an explicit hex color"))?;
    if (hex.len() != 6 && hex.len() != 8) || !hex.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(unsupported(
            "Blender material fill must use #RRGGBB or #RRGGBBAA",
        ));
    }
    let channel = |offset: usize| -> NativeResult<f64> {
        u8::from_str_radix(&hex[offset..offset + 2], 16)
            .map(|value| f64::from(value) / 255.0)
            .map_err(|_| unsupported("Blender material fill contains invalid hex digits"))
    };
    Ok([
        channel(0)?,
        channel(2)?,
        channel(4)?,
        if hex.len() == 8 { channel(6)? } else { 1.0 },
    ])
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum BlenderPrimitive {
    Plane,
    Cylinder,
}

impl BlenderPrimitive {
    pub fn as_driver_name(self) -> &'static str {
        match self {
            Self::Plane => "plane",
            Self::Cylinder => "cylinder",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderPrimitiveContribution {
    pub node_id: Uuid,
    pub name: String,
    pub material_name: String,
    pub color_rgba: [f64; 4],
    pub primitive: BlenderPrimitive,
    pub location: [f64; 3],
    pub rotation: [f64; 3],
    pub scale: [f64; 3],
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderSceneContribution {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub collection_name: String,
    pub objects: Vec<BlenderPrimitiveContribution>,
}

fn blender_semantic_name(label: &str, id: Uuid, suffix: &str) -> String {
    // Blender 4.x data-block names are bounded to 63 bytes. Preserve a
    // human-readable fragment of Motionwright's semantic name while keeping a
    // complete UUID so nodes with the same temporal UUID prefix still retain
    // distinct editable identities after export/readback.
    let mut slug = String::with_capacity(label.len().min(40));
    let mut separator = false;
    for byte in label.bytes() {
        let ch = if byte.is_ascii_alphanumeric() {
            separator = false;
            Some(byte as char)
        } else if !separator && !slug.is_empty() {
            separator = true;
            Some('-')
        } else {
            None
        };
        if let Some(ch) = ch {
            slug.push(ch);
        }
    }
    while slug.ends_with('-') {
        slug.pop();
    }
    if slug.is_empty() {
        slug.push_str("node");
    }

    let uuid = id.simple().to_string();
    let identity = uuid.as_str();
    let suffix = suffix.trim_matches('-');
    let reserved = 1
        + identity.len()
        + if suffix.is_empty() {
            0
        } else {
            1 + suffix.len()
        };
    let max_slug = 63usize.saturating_sub(reserved);
    slug.truncate(max_slug);
    while slug.ends_with('-') {
        slug.pop();
    }
    if suffix.is_empty() {
        format!("{slug}-{identity}")
    } else {
        format!("{slug}-{identity}-{suffix}")
    }
}

fn blender_transform(
    node: &CanvasNode,
    primitive: BlenderPrimitive,
) -> ([f64; 3], [f64; 3], [f64; 3]) {
    let cx = node.x + node.width / 2.0;
    let cy = node.y + node.height / 2.0;
    let location = [
        (cx - PROJECT_WIDTH / 2.0) * BLENDER_SCALE,
        (PROJECT_HEIGHT / 2.0 - cy) * BLENDER_SCALE,
        f64::from(node.z_index) * 0.01,
    ];
    let rotation = [0.0, 0.0, -node.rotation_deg.to_radians()];
    let scale = match primitive {
        BlenderPrimitive::Plane => [
            node.width * BLENDER_SCALE / 2.0,
            node.height * BLENDER_SCALE / 2.0,
            1.0,
        ],
        BlenderPrimitive::Cylinder => [
            node.width * BLENDER_SCALE / 2.0,
            node.height * BLENDER_SCALE / 2.0,
            BLENDER_FLAT_DEPTH_SCALE,
        ],
    };
    (location, rotation, scale)
}

pub fn build_blender_contribution(
    project: &Project,
    scene_id: Uuid,
) -> NativeResult<BlenderSceneContribution> {
    let scene = renderer_scene(project, scene_id, &RendererKind::Blender)?;
    let mut objects = Vec::with_capacity(scene.nodes.len());
    for node in &scene.nodes {
        ensure_flat_semantics(node)?;
        let primitive = match node.kind.as_str() {
            "shape" | "rectangle" => BlenderPrimitive::Plane,
            "circle" => BlenderPrimitive::Cylinder,
            "text" => {
                return Err(unsupported(format!(
                    "Blender scene {} contains text node {}; text is not silently converted to geometry",
                    scene.id, node.id
                )));
            }
            _ => {
                return Err(unsupported(format!(
                    "Blender scene {} contains unsupported node kind {}",
                    scene.id, node.kind
                )));
            }
        };
        let name = blender_semantic_name(&node.name, node.id, "");
        let material_name = blender_semantic_name(&node.name, node.id, "material");
        let fill = node
            .style
            .fill
            .as_deref()
            .ok_or_else(|| unsupported("Blender geometry requires an explicit fill color"))?;
        let (location, rotation, scale) = blender_transform(node, primitive);
        objects.push(BlenderPrimitiveContribution {
            node_id: node.id,
            material_name,
            name,
            color_rgba: hex_color(fill)?,
            primitive,
            location,
            rotation,
            scale,
        });
    }
    Ok(BlenderSceneContribution {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        scene_id,
        collection_name: format!("mw-scene-{}", scene.id.simple()),
        objects,
    })
}

pub fn blender_export_path(plan: &BlenderSceneContribution) -> String {
    format!("motionwright-blender-scene-{}.glb", plan.scene_id.simple())
}

// Manim wire types and compilation have one implementation shared with the driver.
pub use motionwright_manim_profile::{ManimPrimitive, ManimScenePlan};

fn manim_position(node: &CanvasNode) -> NativeResult<(f64, f64)> {
    if node.rotation_deg != 0.0 {
        return Err(unsupported(format!(
            "Manim node {} rotation requires a dedicated transform mapping",
            node.id
        )));
    }
    let cx = node.x + node.width / 2.0;
    let cy = node.y + node.height / 2.0;
    Ok((
        (cx / PROJECT_WIDTH - 0.5) * MANIM_FRAME_WIDTH,
        (0.5 - cy / PROJECT_HEIGHT) * MANIM_FRAME_HEIGHT,
    ))
}

fn manim_color(node: &CanvasNode) -> String {
    node.style
        .fill
        .clone()
        .unwrap_or_else(|| "#FFFFFF".to_owned())
}

pub fn build_manim_plan(project: &Project, scene_id: Uuid) -> NativeResult<ManimScenePlan> {
    let scene = renderer_scene(project, scene_id, &RendererKind::ManimCommunity)?;
    let mut primitives = Vec::with_capacity(scene.nodes.len());
    for node in &scene.nodes {
        ensure_flat_semantics(node)?;
        let (x, y) = manim_position(node)?;
        let color = manim_color(node);
        let primitive = match node.kind.as_str() {
            "text" => ManimPrimitive::Text {
                node_id: node.id,
                text: node
                    .text
                    .clone()
                    .filter(|value| !value.is_empty())
                    .ok_or_else(|| invalid("Manim text node has no text"))?,
                x,
                y,
                font_size: node.style.font_size.unwrap_or(48.0).clamp(4.0, 512.0),
                color,
            },
            "shape" | "rectangle" => ManimPrimitive::Rectangle {
                node_id: node.id,
                x,
                y,
                width: (node.width / PROJECT_WIDTH) * MANIM_FRAME_WIDTH,
                height: (node.height / PROJECT_HEIGHT) * MANIM_FRAME_HEIGHT,
                color,
            },
            "circle" => {
                if (node.width - node.height).abs() > 0.001 {
                    return Err(unsupported(format!(
                        "Manim circle node {} is elliptical; ellipse mapping is not enabled",
                        node.id
                    )));
                }
                ManimPrimitive::Circle {
                    node_id: node.id,
                    x,
                    y,
                    radius: (node.width / PROJECT_WIDTH) * MANIM_FRAME_WIDTH / 2.0,
                    color,
                }
            }
            _ => {
                return Err(unsupported(format!(
                    "Manim scene {} contains unsupported node kind {}",
                    scene.id, node.kind
                )));
            }
        };
        primitives.push(primitive);
    }
    Ok(ManimScenePlan {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        scene_id,
        primitives,
    })
}

pub fn compile_manim_python(plan: &ManimScenePlan) -> NativeResult<String> {
    motionwright_manim_profile::compile_manim_python(plan)
        .map_err(|error| invalid(error.to_string()))
}

/// Project-derived static source contribution. Unsupported controls are refused
/// before any source artifact, native job, or permission request is constructed.
pub fn build_manim_plan_v2(
    project: &Project,
    scene_id: Uuid,
    deliverable_id: Uuid,
) -> NativeResult<motionwright_manim_profile::ManimScenePlanV2> {
    use motionwright_manim_profile::{
        ManimCanvasV2, ManimFrameClockV2, ManimMatteV2, ManimOutputRoleV2, ManimProfileIdV2,
        ManimRenderProfile, ManimSceneBindingV2, ManimScenePlanV2, ManimSourceShapeKindV2,
        ManimStaticShapeV2, validate_plan_v2,
    };
    let scene = renderer_scene(project, scene_id, &RendererKind::ManimCommunity)?;
    let deliverable = project
        .deliverables
        .iter()
        .find(|item| item.id == deliverable_id)
        .ok_or_else(|| invalid("Manim source deliverable is absent from the current Project"))?;
    if !deliverable.included_scene_ids.is_empty()
        && !deliverable.included_scene_ids.contains(&scene_id)
    {
        return Err(invalid(
            "Manim source scene is excluded by the selected deliverable",
        ));
    }
    if scene.camera != motionwright_domain::CameraState::default()
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|item| item.scene_id == scene_id)
    {
        return Err(unsupported(
            "Manim static shapes requires the explicit default camera; authored camera controls need a different profile",
        ));
    }
    let background = project
        .visual_language
        .palette
        .iter()
        .find(|token| token.name == "background")
        .or_else(|| {
            project
                .visual_language
                .palette
                .iter()
                .find(|token| token.name == "surface")
        });
    if background.map(|token| token.value.as_str()) != Some("#000000") {
        return Err(unsupported(
            "Manim static shapes requires an explicitly authored #000000 background/surface token",
        ));
    }
    if deliverable.container != motionwright_domain::OutputContainer::Mp4
        || deliverable.video_codec != motionwright_domain::VideoCodec::H264
        || deliverable.color_space != motionwright_domain::OutputColorSpace::Rec709
    {
        return Err(unsupported(
            "Manim static shapes supplies an opaque H.264 MP4 contribution in the Rec.709 profile only",
        ));
    }
    let clock =
        ManimFrameClockV2::from_canonical(scene.start, scene.duration, deliverable.frame_rate)
            .map_err(|error| unsupported(error.to_string()))?;
    let profile = ManimRenderProfile {
        width: deliverable.width,
        height: deliverable.height,
        frame_rate: u32::try_from(deliverable.frame_rate.num)
            .map_err(|_| unsupported("Manim static shapes frame rate is not representable"))?,
    };
    let mut nodes = Vec::with_capacity(scene.nodes.len());
    for (ordinal, node) in scene.nodes.iter().enumerate() {
        ensure_flat_semantics(node)?;
        if node.rotation_deg != 0.0
            || node.text.is_some()
            || node.style.font_family.is_some()
            || node.style.font_size.is_some()
            || node.style.font_weight.is_some()
            || node.style.line_height.is_some()
        {
            return Err(unsupported(format!(
                "Manim static shape {} contains unrepresented rotation or text controls",
                node.id
            )));
        }
        let source_kind = match node.kind.as_str() {
            "shape" => ManimSourceShapeKindV2::Shape,
            "rectangle" => ManimSourceShapeKindV2::Rectangle,
            "circle" => ManimSourceShapeKindV2::Circle,
            _ => {
                return Err(unsupported(format!(
                    "Manim static source does not support node kind {}",
                    node.kind
                )));
            }
        };
        nodes.push(ManimStaticShapeV2 {
            node_id: node.id,
            display_name: node.name.clone(),
            source_kind,
            canonical_ordinal: ordinal as u32,
            z_index: node.z_index,
            x_px: node.x,
            y_px: node.y,
            width_px: node.width,
            height_px: node.height,
            fill_hex: node.style.fill.clone().ok_or_else(|| {
                unsupported(
                    "Manim static source requires explicit fill; absent fill is not replaced",
                )
            })?,
        });
    }
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
        clock,
        render_profile: profile,
        canvas: ManimCanvasV2::default(),
        background: ManimMatteV2::default(),
        nodes,
    };
    validate_plan_v2(&plan).map_err(|error| unsupported(error.to_string()))?;
    Ok(plan)
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{CanvasNode, Change, NodeStyle};
    use std::collections::BTreeSet;

    fn node(kind: &str, text: Option<&str>, x: f64, y: f64) -> CanvasNode {
        CanvasNode {
            id: Uuid::now_v7(),
            name: format!("{kind} fixture"),
            kind: kind.into(),
            parent_id: None,
            x,
            y,
            width: if kind == "circle" { 180.0 } else { 360.0 },
            height: if kind == "circle" { 180.0 } else { 160.0 },
            rotation_deg: 0.0,
            opacity: 1.0,
            text: text.map(str::to_owned),
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 1,
            style: NodeStyle {
                fill: Some("#F5B84A".into()),
                stroke: None,
                stroke_width: 0.0,
                font_family: (kind == "text").then(|| "Instrument Sans Variable".into()),
                font_size: (kind == "text").then_some(48.0),
                font_weight: (kind == "text").then_some(600),
                line_height: (kind == "text").then_some(1.0),
                blend_mode: motionwright_domain::BlendMode::Normal,
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        }
    }

    fn project_with_renderer(renderer: RendererKind) -> Project {
        let mut project = Project::new("Renderer fixture").unwrap();
        project
            .apply_change(&Change::AddScene {
                name: "Contribution".into(),
                objective: "Show a bounded native renderer contribution".into(),
                duration_seconds: 2,
            })
            .unwrap();
        let scene_id = project.scenes[0].id;
        project
            .apply_change(&Change::SetSceneRenderer { scene_id, renderer })
            .unwrap();
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id,
                node: node("rectangle", None, 240.0, 180.0),
            })
            .unwrap();
        project
    }

    #[test]
    fn blender_projection_is_bounded_geometry_with_revision_identity() {
        let project = project_with_renderer(RendererKind::Blender);
        let scene = project.scenes[0].id;
        let plan = build_blender_contribution(&project, scene).unwrap();
        assert_eq!(plan.project_id, project.id);
        assert_eq!(plan.revision, project.revision);
        assert_eq!(plan.objects.len(), 1);
        assert_eq!(plan.objects[0].primitive, BlenderPrimitive::Plane);
        assert_eq!(plan.objects[0].scale, [1.8, 0.8, 1.0]);
        assert_eq!(plan.objects[0].color_rgba[3], 1.0);
        assert!(plan.objects[0].name.starts_with("rectangle-fixture-"));
        assert!(
            plan.objects[0]
                .material_name
                .starts_with("rectangle-fixture-")
        );
        assert!(plan.objects[0].material_name.ends_with("-material"));
        for name in [
            plan.collection_name.as_str(),
            plan.objects[0].name.as_str(),
            plan.objects[0].material_name.as_str(),
        ] {
            assert!(name.is_ascii());
            assert!(
                name.len() <= 63,
                "Blender 4.x identifier exceeds 63 bytes: {name}"
            );
        }
        let export_path = blender_export_path(&plan);
        assert!(export_path.ends_with(".glb"));
        assert!(!export_path.contains('/'));
    }

    #[test]
    fn blender_semantic_names_preserve_readable_identity_with_bounded_uniqueness() {
        let id = Uuid::parse_str("01234567-89ab-cdef-0123-456789abcdef").unwrap();
        let name = blender_semantic_name(
            "Motionwright focus / export — extremely long semantic label that exceeds Blender bounds",
            id,
            "",
        );
        let material = blender_semantic_name(
            "Motionwright focus / export — extremely long semantic label that exceeds Blender bounds",
            id,
            "material",
        );
        assert!(name.starts_with("Motionwright-focus-export-"));
        assert!(material.starts_with("Motionwright-focus-"));
        assert!(material.ends_with("-material"));
        assert!(name.contains("0123456789abcdef0123456789abcdef"));
        assert!(material.contains("0123456789abcdef0123456789abcdef"));
        assert!(name.is_ascii() && material.is_ascii());
        assert!(name.len() <= 63 && material.len() <= 63);
    }

    #[test]
    fn blender_projection_keeps_nodes_with_the_same_uuid_prefix_distinct() {
        let mut project = project_with_renderer(RendererKind::Blender);
        let first = Uuid::parse_str("019a0032-9000-7000-8000-000000000001").unwrap();
        let second = Uuid::parse_str("019a0032-9000-7000-8000-000000000002").unwrap();
        project.scenes[0].nodes[0].id = first;
        project.scenes[0].nodes[0].name = "Identical display label".into();
        let mut other = project.scenes[0].nodes[0].clone();
        other.id = second;
        project.scenes[0].nodes.push(other);
        let plan = build_blender_contribution(&project, project.scenes[0].id).unwrap();
        assert_ne!(plan.objects[0].name, plan.objects[1].name);
        assert_ne!(plan.objects[0].material_name, plan.objects[1].material_name);
        for (object, id) in plan.objects.iter().zip([first, second]) {
            let identity = id.simple().to_string();
            assert!(object.name.contains(&identity));
            assert!(object.material_name.contains(&identity));
            assert!(object.name.len() <= 63 && object.material_name.len() <= 63);
        }
    }

    #[test]
    fn native_projection_rejects_unpreserved_blending_before_a_plan_exists() {
        for mut project in [
            project_with_renderer(RendererKind::Blender),
            project_with_renderer(RendererKind::ManimCommunity),
        ] {
            project.scenes[0].nodes[0].style.blend_mode = motionwright_domain::BlendMode::Multiply;
            let scene_id = project.scenes[0].id;
            let error = if project.scenes[0].renderer == RendererKind::Blender {
                build_blender_contribution(&project, scene_id)
                    .map(|_| ())
                    .unwrap_err()
            } else {
                build_manim_plan(&project, scene_id)
                    .map(|_| ())
                    .unwrap_err()
            };
            assert_eq!(error.code, ErrorCode::Unsupported);
        }
    }

    #[test]
    fn blender_color_parser_rejects_non_ascii_boundaries_without_panicking() {
        for invalid in ["#FéFFF", "#FFFéFFF", "#GG0000", "#00000", "#123456789"] {
            let error = hex_color(invalid).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
        }
        assert_eq!(
            hex_color("#FF8040").unwrap(),
            [1.0, 128.0 / 255.0, 64.0 / 255.0, 1.0]
        );
        assert_eq!(hex_color("#ff804080").unwrap()[3], 128.0 / 255.0);
    }

    #[test]
    fn blender_projection_refuses_text_instead_of_rasterizing_it_implicitly() {
        let mut project = project_with_renderer(RendererKind::Blender);
        let scene = project.scenes[0].id;
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id: scene,
                node: node("text", Some("Do not flatten me"), 400.0, 300.0),
            })
            .unwrap();
        let error = build_blender_contribution(&project, scene).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }

    #[test]
    fn manim_compiler_emits_only_generated_declarative_scene_code() {
        let mut project = project_with_renderer(RendererKind::ManimCommunity);
        let scene = project.scenes[0].id;
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id: scene,
                node: node("text", Some("x = y + 1"), 640.0, 400.0),
            })
            .unwrap();
        let plan = build_manim_plan(&project, scene).unwrap();
        let source = compile_manim_python(&plan).unwrap();
        assert!(source.contains("class MotionwrightScene(Scene):"));
        assert!(source.contains("Text("));
        assert!(source.contains("Rectangle("));
        assert!(!source.contains("subprocess"));
        assert!(!source.contains("eval("));
        assert!(!source.contains("exec("));
        assert!(!source.contains("__import__"));

        let wire = serde_json::to_value(&plan).unwrap();
        let driver_plan: motionwright_manim_profile::ManimScenePlan =
            serde_json::from_value(wire).unwrap();
        let driver_source = motionwright_manim_profile::compile_manim_python(&driver_plan).unwrap();
        assert_eq!(source, driver_source);
    }

    #[test]
    fn renderer_projection_fails_closed_on_foreign_renderer() {
        let project = project_with_renderer(RendererKind::MotionCanvas);
        let error = build_manim_plan(&project, project.scenes[0].id).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}
