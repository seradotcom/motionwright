use motionwright_domain::{CanvasNode, CoordinateSpace, Project, RendererKind, Scene};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use std::f64::consts::TAU;
use uuid::Uuid;

const PROJECT_WIDTH: f64 = 1920.0;
const PROJECT_HEIGHT: f64 = 1080.0;
const BLENDER_SCALE: f64 = 0.01;
const MANIM_FRAME_WIDTH: f64 = 14.222_222_222_2;
const MANIM_FRAME_HEIGHT: f64 = 8.0;
const CIRCLE_SEGMENTS: usize = 32;
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

fn project_xy(node: &CanvasNode, x: f64, y: f64) -> (f64, f64) {
    let cx = node.x + node.width / 2.0;
    let cy = node.y + node.height / 2.0;
    let radians = node.rotation_deg.to_radians();
    let dx = x - cx;
    let dy = y - cy;
    let rx = cx + dx * radians.cos() - dy * radians.sin();
    let ry = cy + dx * radians.sin() + dy * radians.cos();
    (rx, ry)
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderMeshContribution {
    pub node_id: Uuid,
    pub name: String,
    pub material_name: String,
    pub color_rgba: [f64; 4],
    pub vertices: Vec<[f64; 3]>,
    pub edges: Vec<[u32; 2]>,
    pub faces: Vec<Vec<u32>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct BlenderSceneContribution {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub collection_name: String,
    pub meshes: Vec<BlenderMeshContribution>,
}

fn blender_vertex(node: &CanvasNode, x: f64, y: f64) -> [f64; 3] {
    let (x, y) = project_xy(node, x, y);
    [
        (x - PROJECT_WIDTH / 2.0) * BLENDER_SCALE,
        (PROJECT_HEIGHT / 2.0 - y) * BLENDER_SCALE,
        f64::from(node.z_index) * 0.01,
    ]
}

fn blender_rect(node: &CanvasNode) -> (Vec<[f64; 3]>, Vec<Vec<u32>>) {
    let x0 = node.x;
    let y0 = node.y;
    let x1 = node.x + node.width;
    let y1 = node.y + node.height;
    (
        vec![
            blender_vertex(node, x0, y0),
            blender_vertex(node, x1, y0),
            blender_vertex(node, x1, y1),
            blender_vertex(node, x0, y1),
        ],
        vec![vec![0, 1, 2, 3]],
    )
}

fn blender_circle(node: &CanvasNode) -> (Vec<[f64; 3]>, Vec<Vec<u32>>) {
    let cx = node.x + node.width / 2.0;
    let cy = node.y + node.height / 2.0;
    let rx = node.width / 2.0;
    let ry = node.height / 2.0;
    let mut vertices = Vec::with_capacity(CIRCLE_SEGMENTS);
    for index in 0..CIRCLE_SEGMENTS {
        let angle = TAU * (index as f64) / (CIRCLE_SEGMENTS as f64);
        vertices.push(blender_vertex(
            node,
            cx + rx * angle.cos(),
            cy + ry * angle.sin(),
        ));
    }
    let face = (0..CIRCLE_SEGMENTS as u32).collect::<Vec<_>>();
    (vertices, vec![face])
}

pub fn build_blender_contribution(
    project: &Project,
    scene_id: Uuid,
) -> NativeResult<BlenderSceneContribution> {
    let scene = renderer_scene(project, scene_id, &RendererKind::Blender)?;
    let mut meshes = Vec::with_capacity(scene.nodes.len());
    for node in &scene.nodes {
        ensure_flat_semantics(node)?;
        let (vertices, faces) = match node.kind.as_str() {
            "shape" | "rectangle" => blender_rect(node),
            "circle" => blender_circle(node),
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
        // Blender 4.x data-block names are bounded to 63 bytes. Node UUIDs are
        // globally unique, so avoid concatenating scene + node UUIDs: Blender
        // would truncate that identifier and break exact semantic lookups.
        let name = format!("mw-node-{}", node.id.simple());
        let fill = node
            .style
            .fill
            .as_deref()
            .ok_or_else(|| unsupported("Blender geometry requires an explicit fill color"))?;
        meshes.push(BlenderMeshContribution {
            node_id: node.id,
            material_name: format!("{name}-material"),
            name,
            color_rgba: hex_color(fill)?,
            vertices,
            edges: vec![],
            faces,
        });
    }
    Ok(BlenderSceneContribution {
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        scene_id,
        collection_name: format!("mw-scene-{}", scene.id.simple()),
        meshes,
    })
}

pub fn blender_export_path(plan: &BlenderSceneContribution) -> String {
    format!("motionwright-blender-scene-{}.glb", plan.scene_id.simple())
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
        assert_eq!(plan.meshes.len(), 1);
        assert_eq!(plan.meshes[0].vertices.len(), 4);
        assert_eq!(plan.meshes[0].faces, vec![vec![0, 1, 2, 3]]);
        assert_eq!(plan.meshes[0].color_rgba[3], 1.0);
        assert!(plan.meshes[0].name.starts_with("mw-node-"));
        assert!(plan.meshes[0].material_name.ends_with("-material"));
        for name in [
            plan.collection_name.as_str(),
            plan.meshes[0].name.as_str(),
            plan.meshes[0].material_name.as_str(),
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
    }

    #[test]
    fn renderer_projection_fails_closed_on_foreign_renderer() {
        let project = project_with_renderer(RendererKind::MotionCanvas);
        let error = build_manim_plan(&project, project.scenes[0].id).unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}
