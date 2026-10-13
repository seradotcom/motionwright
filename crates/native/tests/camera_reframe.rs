//! Real canonical Semwright Film contract for bounded original 2D camera pan.
//! This does not claim 3D orbit, focal length, camera lighting or actual MP4.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole, Primitive, realize};
use std::collections::BTreeSet;
use uuid::Uuid;

fn source() -> (Project, CameraReframe) {
    let mut p = Project::new("Native original camera reframe").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Two source rectangles".into(),
        objective: "Native semantic pan without a second renderer".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scene_id = p.scenes[0].id;
    for (i, x) in [460.0_f64, 1050.0].into_iter().enumerate() {
        p.apply_change(&Change::AddCanvasNode {
            scene_id,
            node: CanvasNode {
                id: Uuid::new_v4(),
                name: format!("original shape {}", i),
                kind: "rectangle".into(),
                parent_id: None,
                x,
                y: 420.0,
                width: 175.0,
                height: 120.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: None,
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 0,
                style: NodeStyle {
                    fill: Some("#86C8D1".into()),
                    ..Default::default()
                },
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            },
        })
        .unwrap();
    }
    let rig = CameraReframe {
        id: Uuid::parse_str("00000000-0000-4000-8000-000000000203").unwrap(),
        scene_id,
        component_version: CAMERA_REFRAME_VERSION,
        start_center_x: 960.0,
        start_center_y: 540.0,
        end_center_x: 978.0,
        end_center_y: 548.0,
        duration_frames: 90,
    };
    p.apply_change(&Change::UpsertCameraReframe {
        reframe: rig.clone(),
    })
    .unwrap();
    (p, rig)
}
fn options(project: &Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: project.scenes[0].id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    }
}
#[test]
fn source_pan_is_an_exact_three_second_native_camera_displacement_over_all_semantic_subjects() {
    let (project, rig) = source();
    let output =
        build_motion_canvas_segments(&project, project.deliverables[0].id, &options(&project))
            .unwrap();
    assert_eq!(output.len(), 1);
    assert_eq!(output[0].frame_count, 180);
    let film = &output[0].film;
    let shot = &film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.subjects.len(), project.scenes[0].nodes.len());
    assert_eq!(shot.motion.len(), project.scenes[0].nodes.len());
    let targets = shot
        .motion
        .iter()
        .map(|inv| match &inv.primitive {
            Primitive::Settle {
                target,
                offset,
                rotation,
            } => {
                assert_eq!(*rotation, 0.0);
                assert!((offset.x - 18.0).abs() < 0.0001);
                assert!((offset.y - 8.0).abs() < 0.0001);
                assert_eq!(inv.span_id, format!("camera-reframe-{}", rig.id.simple()));
                target.clone()
            }
            unexpected => panic!("unexpected native camera primitive {unexpected:?}"),
        })
        .collect::<BTreeSet<_>>();
    assert_eq!(targets.len(), project.scenes[0].nodes.len());
    assert!(realize(film).is_ok());
}
#[test]
fn camera_motion_refuses_unverified_non30fps_and_crop_reprojection() {
    let (project, _) = source();
    let mut non30 = options(&project);
    non30.frame_rate = Rate { num: 60, den: 1 };
    assert!(build_motion_canvas_segments(&project, project.deliverables[0].id, &non30).is_err());
    let mut bad = project.clone();
    bad.scenes[0].nodes[0].x = 70.0;
    assert!(build_motion_canvas_segments(&bad, bad.deliverables[0].id, &options(&bad)).is_err());
}
