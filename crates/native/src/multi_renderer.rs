use motionwright_domain::{BlendMode, CanvasNode, CoordinateSpace, Project, RendererKind, Scene};
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
    if node.opacity != 1.0 || node.style.stroke_width != 0.0 || node.style.stroke.is_some() {
        return Err(unsupported(format!(
            "Canvas node {} has opacity/stroke state that this native contribution cannot preserve",
            node.id
        )));
    }
    // Neither the typed Blender geometry/material contribution nor the
    // bounded Manim primitive format encodes compositing blend state.
    // Preserving authored Multiply/Screen/Add is mandatory: never silently
    // replace those semantics with a default Normal material before a
    // native effect is dispatched.
    if node.style.blend_mode != BlendMode::Normal {
        return Err(unsupported(format!(
            "Canvas node {} uses {:?} blend mode; this native contribution cannot preserve authored compositing",
            node.id, node.style.blend_mode
        )));
    }
    Ok(())
}

fn hex_color(value: &str) -> NativeResult<[f64; 4]> {
    let hex = value
        .strip_prefix('#')
        .ok_or_else(|| unsupported("Blender material fill must be an explicit hex color"))?;
    if hex.len() != 6 && hex.len() != 8 {
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
    // stable UUID-derived disambiguator so exact lookups survive export/readback.
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
    let short = &uuid[..12];
    let suffix = suffix.trim_matches('-');
    let reserved = 1
        + short.len()
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
        format!("{slug}-{short}")
    } else {
        format!("{slug}-{short}-{suffix}")
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

const BLENDER_ORIGINAL_SCENE_SCHEMA: &str = "motionwright.blender.original-scene-plan/1";

/// A static camera using the geometry contributor's exact world basis.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalSceneCamera {
    pub object_name: String,
    pub data_name: String,
    pub source: motionwright_domain::CameraState,
    pub location: [f64; 3],
    pub rotation_euler: [f64; 3],
    pub projection: String,
    pub sensor_fit: String,
    pub ortho_scale: f64,
    pub clip_start: f64,
    pub clip_end: f64,
    pub resolution: [u32; 2],
    pub pixel_aspect: [f64; 2],
}

/// Native identity properties preserve the complete source UUID.
/// A camera has a scene identity; the domain assigns it no Canvas node UUID.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalEntityIdentity {
    pub root: String,
    pub name: String,
    pub properties: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderOriginalScenePlan {
    pub schema: String,
    pub project_json_sha256: String,
    pub native_scene_name: String,
    pub contribution: BlenderSceneContribution,
    pub camera: BlenderOriginalSceneCamera,
    pub identities: Vec<BlenderOriginalEntityIdentity>,
}

fn original_identity(
    plan: &BlenderSceneContribution,
    root: &str,
    name: &str,
    role: &str,
    node_id: Option<Uuid>,
    project_json_sha256: &str,
) -> BlenderOriginalEntityIdentity {
    let mut properties = std::collections::BTreeMap::from([
        (
            "motionwright.project_id".into(),
            plan.project_id.to_string(),
        ),
        (
            "motionwright.generation".into(),
            plan.generation.to_string(),
        ),
        ("motionwright.scene_id".into(), plan.scene_id.to_string()),
        ("motionwright.revision".into(), plan.revision.to_string()),
        ("motionwright.role".into(), role.into()),
        (
            "motionwright.project_json_sha256".into(),
            project_json_sha256.into(),
        ),
    ]);
    if let Some(id) = node_id {
        properties.insert("motionwright.node_id".into(), id.to_string());
    }
    BlenderOriginalEntityIdentity {
        root: root.into(),
        name: name.into(),
        properties,
    }
}

/// Build a source plan only. This neither executes Blender nor attests a save,
/// reopen, native render, operator approval, or visual acceptance.
pub fn build_blender_original_scene_plan(
    project: &Project,
    scene_id: Uuid,
) -> NativeResult<BlenderOriginalScenePlan> {
    use sha2::{Digest, Sha256};
    use std::collections::BTreeSet;

    let contribution = build_blender_contribution(project, scene_id)?;
    let scene = renderer_scene(project, scene_id, &RendererKind::Blender)?;
    if !scene.beats.is_empty()
        || project
            .production_design
            .camera_reframes
            .iter()
            .any(|reframe| reframe.scene_id == scene_id)
    {
        return Err(unsupported(
            "Original Blender source supports a static scene without authored beats or camera transitions",
        ));
    }
    if scene.camera.zoom != 1.0 || scene.camera.rotation_deg != 0.0 {
        return Err(unsupported(
            "Original Blender source requires unit camera zoom and zero camera rotation; unsupported camera state is not discarded",
        ));
    }
    let mut object_names = BTreeSet::new();
    let mut material_names = BTreeSet::new();
    let mut node_ids = BTreeSet::new();
    let mut min_z = f64::INFINITY;
    let mut max_z = f64::NEG_INFINITY;
    for object in &contribution.objects {
        if !node_ids.insert(object.node_id)
            || !object_names.insert(object.name.as_str())
            || !material_names.insert(object.material_name.as_str())
        {
            return Err(unsupported(
                "Original Blender source requires unique full node UUIDs and unambiguous native object/material names",
            ));
        }
        if object
            .location
            .iter()
            .chain(&object.scale)
            .any(|value| !value.is_finite() || value.abs() > 100_000.0)
        {
            return Err(unsupported(
                "Original Blender source exceeds the bounded native transform profile",
            ));
        }
        min_z = min_z.min(object.location[2]);
        max_z = max_z.max(object.location[2]);
    }
    let camera_location = [
        (scene.camera.center_x - PROJECT_WIDTH / 2.0) * BLENDER_SCALE,
        (PROJECT_HEIGHT / 2.0 - scene.camera.center_y) * BLENDER_SCALE,
        max_z + 10.0,
    ];
    let clip_end = max_z - min_z + 20.0;
    if camera_location
        .iter()
        .any(|value| !value.is_finite() || value.abs() > 100_000.0)
        || !clip_end.is_finite()
        || !(20.0..=200_020.0).contains(&clip_end)
    {
        return Err(unsupported(
            "Original Blender camera exceeds the bounded position or clip profile",
        ));
    }
    let project_json = serde_json::to_vec(project)
        .map_err(|_| invalid("The current Project cannot be encoded for source identity"))?;
    let project_json_sha256 = format!("{:x}", Sha256::digest(&project_json));
    let camera = BlenderOriginalSceneCamera {
        object_name: format!("mw-camera-{}", scene_id.simple()),
        data_name: format!("mw-camera-data-{}", scene_id.simple()),
        source: scene.camera.clone(),
        location: camera_location,
        rotation_euler: [0.0; 3],
        projection: "ORTHO".into(),
        sensor_fit: "VERTICAL".into(),
        ortho_scale: PROJECT_HEIGHT * BLENDER_SCALE,
        clip_start: 0.1,
        clip_end,
        resolution: [PROJECT_WIDTH as u32, PROJECT_HEIGHT as u32],
        pixel_aspect: [1.0, 1.0],
    };
    if object_names.contains(camera.object_name.as_str()) {
        return Err(unsupported(
            "Original Blender camera name collides with source geometry",
        ));
    }
    let native_scene_name = format!("mw-native-scene-{}", scene_id.simple());
    let mut identities = vec![
        original_identity(
            &contribution,
            "scenes",
            &native_scene_name,
            "native_scene",
            None,
            &project_json_sha256,
        ),
        original_identity(
            &contribution,
            "collections",
            &contribution.collection_name,
            "scene_collection",
            None,
            &project_json_sha256,
        ),
        original_identity(
            &contribution,
            "objects",
            &camera.object_name,
            "scene_camera",
            None,
            &project_json_sha256,
        ),
        original_identity(
            &contribution,
            "cameras",
            &camera.data_name,
            "scene_camera_data",
            None,
            &project_json_sha256,
        ),
    ];
    for object in &contribution.objects {
        identities.push(original_identity(
            &contribution,
            "objects",
            &object.name,
            "canvas_object",
            Some(object.node_id),
            &project_json_sha256,
        ));
        identities.push(original_identity(
            &contribution,
            "materials",
            &object.material_name,
            "canvas_material",
            Some(object.node_id),
            &project_json_sha256,
        ));
    }
    Ok(BlenderOriginalScenePlan {
        schema: BLENDER_ORIGINAL_SCENE_SCHEMA.into(),
        project_json_sha256,
        native_scene_name,
        contribution,
        camera,
        identities,
    })
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ManimPrimitive {
    Text {
        node_id: Uuid,
        text: String,
        x: f64,
        y: f64,
        font_size: f64,
        color: String,
    },
    Rectangle {
        node_id: Uuid,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        color: String,
    },
    Circle {
        node_id: Uuid,
        x: f64,
        y: f64,
        radius: f64,
        color: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct ManimScenePlan {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub primitives: Vec<ManimPrimitive>,
}

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

// The compatibility V1 wire carries only font size and color. Its public
// Project projection must not erase authored typography or clamp it to fit
// the narrower renderer profile. The normal saved-source route uses V3.
fn legacy_manim_font_size(node: &CanvasNode) -> NativeResult<f64> {
    if node.style.font_family.is_some()
        || node.style.font_weight.is_some()
        || node.style.line_height.is_some()
    {
        return Err(unsupported(format!(
            "Manim V1 text node {} has authored font family, weight or line height that its wire profile cannot preserve; use the editable source profile",
            node.id
        )));
    }
    let size = node.style.font_size.unwrap_or(48.0);
    if !(4.0..=512.0).contains(&size) {
        return Err(unsupported(format!(
            "Manim V1 text node {} has font size {} outside its supported range 4..512; authored size was not changed",
            node.id, size
        )));
    }
    Ok(size)
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
                font_size: legacy_manim_font_size(node)?,
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

fn python_string(value: &str) -> NativeResult<String> {
    serde_json::to_string(value).map_err(|_| invalid("Manim string could not be encoded"))
}

pub fn compile_manim_python(plan: &ManimScenePlan) -> NativeResult<String> {
    if plan.primitives.is_empty() || plan.primitives.len() > MAX_RENDERER_NODES {
        return Err(invalid("Manim plan has an invalid primitive count"));
    }
    let mut body = String::from(
        "from manim import Circle, Create, Rectangle, Scene, Text\n\n\nclass MotionwrightScene(Scene):\n    def construct(self):\n",
    );
    for (index, primitive) in plan.primitives.iter().enumerate() {
        match primitive {
            ManimPrimitive::Text {
                text,
                x,
                y,
                font_size,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Text({}, font_size={font_size:.6}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    python_string(text)?,
                    python_string(color)?
                ));
            }
            ManimPrimitive::Rectangle {
                x,
                y,
                width,
                height,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Rectangle(width={width:.9}, height={height:.9}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    python_string(color)?
                ));
            }
            ManimPrimitive::Circle {
                x,
                y,
                radius,
                color,
                ..
            } => {
                body.push_str(&format!(
                    "        item_{index} = Circle(radius={radius:.9}, color={}).move_to([{x:.9}, {y:.9}, 0])\n",
                    python_string(color)?
                ));
            }
        }
        body.push_str(&format!("        self.play(Create(item_{index}))\n"));
    }
    Ok(body)
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
    fn blender_projection_negotiates_non_normal_blend_before_dispatch() {
        // MW05-E01-02: compositing semantics are authored content.
        // The typed Blender contribution has no blend-mode field, so it
        // must refuse Multiply/Screen/Add rather than silently output a
        // Normal material. No SDK, Broker or rendering work is involved.
        let original = project_with_renderer(RendererKind::Blender);
        let scene_id = original.scenes[0].id;
        let original_wire = serde_json::to_value(&original).unwrap();
        let normal_plan = build_blender_contribution(&original, scene_id).unwrap();
        assert_eq!(normal_plan.objects.len(), 1);
        assert_eq!(
            original.scenes[0].nodes[0].style.blend_mode,
            BlendMode::Normal
        );

        for mode in [BlendMode::Multiply, BlendMode::Screen, BlendMode::Add] {
            let mut edited = original.clone();
            edited.scenes[0].nodes[0].style.blend_mode = mode;
            edited.validate().unwrap();
            let authored_before = serde_json::to_value(&edited).unwrap();
            let error = build_blender_contribution(&edited, scene_id).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported, "{mode:?}");
            assert!(error.message.contains("blend mode"), "{mode:?}: {error}");
            assert!(
                error.message.contains(&format!("{mode:?}")),
                "the unsupported mode must be named for the author"
            );
            assert_eq!(serde_json::to_value(&edited).unwrap(), authored_before);
            assert_eq!(edited.scenes[0].nodes[0].style.blend_mode, mode);
            // A failed projected scene cannot be used to dispatch a
            // material.create or export operation; the Normal source and
            // its already accepted pure projection remain unchanged.
            assert_eq!(serde_json::to_value(&original).unwrap(), original_wire);
            assert_eq!(
                build_blender_contribution(&original, scene_id).unwrap(),
                normal_plan
            );
        }
    }

    #[test]
    fn manim_projection_rejects_non_normal_blend_before_compiling_scene() {
        // ManimPrimitive also has no compositing blend-mode member.
        // Sharing the flat semantics guard must not produce Python for
        // unsupported creative content either.
        let original = project_with_renderer(RendererKind::ManimCommunity);
        let scene_id = original.scenes[0].id;
        assert!(build_manim_plan(&original, scene_id).is_ok());

        for mode in [BlendMode::Multiply, BlendMode::Screen, BlendMode::Add] {
            let mut authored = original.clone();
            authored.scenes[0].nodes[0].style.blend_mode = mode;
            authored.validate().unwrap();
            let before = serde_json::to_value(&authored).unwrap();
            let error = build_manim_plan(&authored, scene_id).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported, "{mode:?}");
            assert!(error.message.contains("blend mode"));
            assert_eq!(serde_json::to_value(&authored).unwrap(), before);
        }
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
        assert!(material.starts_with("Motionwright-focus-export-"));
        assert!(material.ends_with("-material"));
        assert!(name.contains("0123456789ab"));
        assert!(material.contains("0123456789ab"));
        assert!(name.is_ascii() && material.is_ascii());
        assert!(name.len() <= 63 && material.len() <= 63);
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

    // A V1 compatibility fixture leaves fields absent only when their absence
    // is authored. It never clears a field on an incoming Project.
    fn project_with_legacy_manim_text() -> Project {
        let mut project = project_with_renderer(RendererKind::ManimCommunity);
        let scene_id = project.scenes[0].id;
        let mut text = node("text", Some("x = y + 1"), 640.0, 400.0);
        text.style.font_family = None;
        text.style.font_weight = None;
        text.style.line_height = None;
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id,
                node: text,
            })
            .unwrap();
        project
    }

    #[test]
    fn manim_legacy_projection_rejects_authored_typography_without_rewriting_source() {
        let original = project_with_legacy_manim_text();
        let scene_id = original.scenes[0].id;
        let original_wire = serde_json::to_value(&original).unwrap();
        assert!(build_manim_plan(&original, scene_id).is_ok());
        for field in ["font_family", "font_weight", "line_height"] {
            let mut authored = original.clone();
            let style = &mut authored.scenes[0].nodes[1].style;
            match field {
                "font_family" => style.font_family = Some("Instrument Sans Variable".into()),
                "font_weight" => style.font_weight = Some(600),
                "line_height" => style.line_height = Some(1.25),
                _ => unreachable!(),
            }
            authored.validate().unwrap();
            let before = serde_json::to_value(&authored).unwrap();
            let error = build_manim_plan(&authored, scene_id).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported, "{field}");
            assert!(error.message.contains("V1 text node"), "{field}");
            assert_eq!(serde_json::to_value(&authored).unwrap(), before, "{field}");
        }
        assert_eq!(serde_json::to_value(&original).unwrap(), original_wire);
        assert!(build_manim_plan(&original, scene_id).is_ok());
    }

    #[test]
    fn manim_legacy_projection_preserves_supported_sizes_and_refuses_clamping() {
        let original = project_with_legacy_manim_text();
        let scene_id = original.scenes[0].id;
        for size in [4.0, 48.0, 512.0] {
            let mut authored = original.clone();
            authored.scenes[0].nodes[1].style.font_size = Some(size);
            authored.validate().unwrap();
            let before = serde_json::to_value(&authored).unwrap();
            let plan = build_manim_plan(&authored, scene_id).unwrap();
            let ManimPrimitive::Text { font_size, .. } = &plan.primitives[1] else {
                panic!("the original text node must remain text");
            };
            assert_eq!(*font_size, size);
            let wire = serde_json::to_value(&plan).unwrap();
            let driver_plan: motionwright_manim_profile::ManimScenePlan =
                serde_json::from_value(wire).unwrap();
            motionwright_manim_profile::validate_plan(&driver_plan).unwrap();
            assert_eq!(serde_json::to_value(&authored).unwrap(), before);
        }
        for size in [1.0, 3.5, 512.5, 2048.0] {
            let mut authored = original.clone();
            authored.scenes[0].nodes[1].style.font_size = Some(size);
            authored.validate().unwrap();
            let before = serde_json::to_value(&authored).unwrap();
            let error = build_manim_plan(&authored, scene_id).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported, "{size}");
            assert!(error.message.contains("font size"), "{size}");
            assert_eq!(serde_json::to_value(&authored).unwrap(), before, "{size}");
        }
    }

    #[test]
    fn manim_compiler_emits_only_generated_declarative_scene_code() {
        let project = project_with_legacy_manim_text();
        let scene = project.scenes[0].id;
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
    #[test]
    fn original_blender_plan_preserves_the_existing_geometry_and_full_identity() {
        let project = project_with_renderer(RendererKind::Blender);
        let scene_id = project.scenes[0].id;
        let plan = build_blender_original_scene_plan(&project, scene_id).unwrap();
        assert_eq!(
            plan.contribution,
            build_blender_contribution(&project, scene_id).unwrap()
        );
        assert_eq!(plan.camera.source, project.scenes[0].camera);
        assert_eq!(plan.camera.resolution, [1920, 1080]);
        assert_eq!(plan.camera.pixel_aspect, [1.0, 1.0]);
        assert_eq!(plan.camera.sensor_fit, "VERTICAL");
        assert_eq!(plan.camera.projection, "ORTHO");
        assert_eq!(plan.camera.ortho_scale, 10.8);
        assert_eq!(plan.identities.len(), 6);
        let object = plan
            .identities
            .iter()
            .find(|identity| identity.properties["motionwright.role"] == "canvas_object")
            .unwrap();
        assert_eq!(
            object.properties["motionwright.node_id"],
            project.scenes[0].nodes[0].id.to_string()
        );
        assert_eq!(
            object.properties["motionwright.project_id"],
            project.id.to_string()
        );
        assert_eq!(
            object.properties["motionwright.scene_id"],
            scene_id.to_string()
        );
        assert_eq!(
            object.properties["motionwright.generation"],
            project.generation.to_string()
        );
        assert_eq!(
            object.properties["motionwright.revision"],
            project.revision.to_string()
        );
    }

    #[test]
    fn original_blender_camera_uses_geometry_basis_without_inventing_a_canvas_uuid() {
        let mut project = project_with_renderer(RendererKind::Blender);
        project.scenes[0].camera.center_x = 1000.0;
        project.scenes[0].camera.center_y = 600.0;
        project.scenes[0].camera.safe_margin = 0.08;
        let plan = build_blender_original_scene_plan(&project, project.scenes[0].id).unwrap();
        assert_eq!(plan.camera.location[0], 0.4);
        assert_eq!(plan.camera.location[1], -0.6);
        assert_eq!(plan.camera.rotation_euler, [0.0; 3]);
        assert_eq!(plan.camera.source.safe_margin, 0.08);
        assert!(plan.camera.location[2] > plan.contribution.objects[0].location[2]);
        for identity in plan
            .identities
            .iter()
            .filter(|identity| identity.properties["motionwright.role"].starts_with("scene_camera"))
        {
            assert!(!identity.properties.contains_key("motionwright.node_id"));
            assert_eq!(
                identity.properties["motionwright.scene_id"],
                project.scenes[0].id.to_string()
            );
        }
    }

    #[test]
    fn original_blender_plan_refuses_unsupported_zoom_and_rotation() {
        for (zoom, rotation) in [(1.1, 0.0), (1.0, 15.0)] {
            let mut project = project_with_renderer(RendererKind::Blender);
            project.scenes[0].camera.zoom = zoom;
            project.scenes[0].camera.rotation_deg = rotation;
            let error =
                build_blender_original_scene_plan(&project, project.scenes[0].id).unwrap_err();
            assert_eq!(error.code, ErrorCode::Unsupported);
        }
    }

    #[test]
    fn original_blender_plan_rejects_native_name_collision_between_distinct_full_uuids() {
        let mut project = project_with_renderer(RendererKind::Blender);
        project.scenes[0].nodes[0].id =
            Uuid::parse_str("01234567-89ab-4000-8000-000000000001").unwrap();
        let mut other = project.scenes[0].nodes[0].clone();
        other.id = Uuid::parse_str("01234567-89ab-4000-8000-000000000002").unwrap();
        other.x += 400.0;
        project.scenes[0].nodes.push(other);
        let error = build_blender_original_scene_plan(&project, project.scenes[0].id).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.message.contains("unambiguous"));
    }

    #[test]
    fn original_blender_plan_binds_content_beyond_the_revision_label() {
        let mut project = project_with_renderer(RendererKind::Blender);
        let scene_id = project.scenes[0].id;
        let original = build_blender_original_scene_plan(&project, scene_id).unwrap();
        project.scenes[0].nodes[0].style.fill = Some("#114477".into());
        let changed = build_blender_original_scene_plan(&project, scene_id).unwrap();
        assert_eq!(
            original.contribution.revision,
            changed.contribution.revision
        );
        assert_ne!(original.project_json_sha256, changed.project_json_sha256);
        for identity in &changed.identities {
            assert_eq!(
                identity.properties["motionwright.project_json_sha256"],
                changed.project_json_sha256,
            );
        }
    }

    #[test]
    fn original_blender_plan_rejects_unbounded_depth_and_camera_position() {
        let mut project = project_with_renderer(RendererKind::Blender);
        project.scenes[0].nodes[0].z_index = i32::MAX;
        let error = build_blender_original_scene_plan(&project, project.scenes[0].id).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        project.scenes[0].nodes[0].z_index = 1;
        project.scenes[0].camera.center_x = 1.0e12;
        let error = build_blender_original_scene_plan(&project, project.scenes[0].id).unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }

    #[test]
    fn original_blender_plan_rejects_non_finite_camera_and_foreign_renderer() {
        let mut project = project_with_renderer(RendererKind::Blender);
        project.scenes[0].camera.center_y = f64::NAN;
        assert!(build_blender_original_scene_plan(&project, project.scenes[0].id).is_err());
        let foreign = project_with_renderer(RendererKind::MotionCanvas);
        let error = build_blender_original_scene_plan(&foreign, foreign.scenes[0].id).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}
