//! Persisted authoring-to-native projection, without a Blender execution claim.
use motionwright_domain::{
    BlendMode, CameraState, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind,
};
use motionwright_native::{
    multi_renderer::{BlenderPrimitive, build_blender_original_scene_plan},
    production::build_blender_lit_still_plan,
};
use motionwright_service::StudioService;
use serde_json::json;
use std::collections::BTreeSet;
use uuid::Uuid;

#[test]
fn saved_static_camera_and_circle_survive_sqlite_and_project_into_original_and_lit_sources() {
    let root = tempfile::tempdir().unwrap();
    let db = root.path().join("scene.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut project = service.create_project("Static Blender scene").unwrap();
    let mut step = 0;
    let apply = |project: &motionwright_domain::Project, change: Change, step: &mut u32| {
        *step += 1;
        service
            .apply(
                project.id,
                &project.stamp(),
                &format!("author-{step}"),
                &change,
            )
            .unwrap()
            .project
    };
    project = apply(
        &project,
        Change::AddScene {
            name: "Static composition".into(),
            objective: "Preserve original geometry and camera framing".into(),
            duration_seconds: 4,
        },
        &mut step,
    );
    let scene_id = project.scenes[0].id;
    project = apply(
        &project,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::Blender,
        },
        &mut step,
    );
    let mut circle_id = Uuid::nil();
    for (kind, x, y, width, height, z_index, fill) in [
        ("rectangle", 276.0, 252.0, 360.0, 216.0, 1, "#86C8D1"),
        ("circle", 800.0, 500.0, 240.0, 180.0, 2, "#E0A940"),
    ] {
        let id = Uuid::now_v7();
        if kind == "circle" {
            circle_id = id;
        }
        project = apply(
            &project,
            Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id,
                    name: format!("{kind} source"),
                    kind: kind.into(),
                    parent_id: None,
                    x,
                    y,
                    width,
                    height,
                    rotation_deg: 0.0,
                    opacity: 1.0,
                    text: None,
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index,
                    style: NodeStyle {
                        fill: Some(fill.into()),
                        stroke: None,
                        stroke_width: 0.0,
                        font_family: None,
                        font_size: None,
                        font_weight: None,
                        line_height: None,
                        blend_mode: BlendMode::Normal,
                    },
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                },
            },
            &mut step,
        );
    }
    let source_nodes = project.scenes[0].nodes.clone();
    let camera = CameraState {
        center_x: 1000.0,
        center_y: 600.0,
        zoom: 2.0,
        rotation_deg: 30.0,
        safe_margin: 0.05,
    };
    project = apply(
        &project,
        Change::SetCamera {
            scene_id,
            camera: camera.clone(),
        },
        &mut step,
    );
    assert_eq!(project.scenes[0].nodes, source_nodes);
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let saved = reopened.project(project.id).unwrap();
    assert_eq!(saved, project);
    let original = build_blender_original_scene_plan(&saved, scene_id).unwrap();
    let lit = build_blender_lit_still_plan(&saved, scene_id).unwrap();
    assert_eq!(original.camera.source, camera);
    assert_eq!(original.camera.ortho_scale, 5.4);
    assert_eq!(
        original.camera.rotation_euler,
        [0.0, 0.0, -30.0_f64.to_radians()]
    );
    assert_eq!(lit.source.camera, original.camera);
    let circle = original
        .contribution
        .objects
        .iter()
        .find(|item| item.node_id == circle_id)
        .unwrap();
    assert_eq!(circle.primitive, BlenderPrimitive::Cylinder);
    assert_eq!(circle.scale, [1.2, 0.9, 0.005]);
    let spec = lit.composition_spec["entities"].as_array().unwrap();
    let native_circle = spec
        .iter()
        .find(|item| item["id"] == json!(format!("node-{}", circle_id.simple())))
        .unwrap();
    assert_eq!(native_circle["shape"]["kind"], "cylinder");
    assert_eq!(native_circle["transform"]["scale"], json!(circle.scale));
    let native_camera = spec
        .iter()
        .find(|item| item["id"] == json!(lit.camera_entity))
        .unwrap();
    assert_eq!(
        native_camera["transform"]["rotation"],
        json!(original.camera.rotation_euler)
    );
}
