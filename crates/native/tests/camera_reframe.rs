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
        detail_return: None,
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

#[test]
fn detail_return_compiles_one_original_group_with_exact_zoom_hold_and_return_after_sqlite_reopen() {
    use motionwright_service::StudioService;
    use semwright_motion_authoring::{
        Channel, MotionEasing, Operand, Point, StartAnchor, SubjectContent,
    };
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("detail-return.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let mut project = service
        .create_project("Original camera detail and return")
        .unwrap();
    let save = |project: &Project, change: Change| {
        service
            .apply(
                project.id,
                &project.stamp(),
                &Uuid::now_v7().to_string(),
                &change,
            )
            .unwrap()
            .project
    };
    project = save(
        &project,
        Change::AddScene {
            name: "Original static objects".into(),
            objective: "Bounded planar zoom and return".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = project.scenes[0].id;
    let (template, _) = source();
    for node in &template.scenes[0].nodes {
        project = save(
            &project,
            Change::AddCanvasNode {
                scene_id,
                node: node.clone(),
            },
        );
    }
    let mut label = template.scenes[0].nodes[0].clone();
    label.id = Uuid::now_v7();
    label.name = "Original detail label".into();
    label.kind = "text".into();
    label.x = 710.0;
    label.y = 260.0;
    label.width = 500.0;
    label.height = 100.0;
    label.z_index = 2;
    label.text = Some("Original detail".into());
    label.style.font_family = Some(MOTION_CANVAS_FONT_FAMILY.into());
    label.style.font_size = Some(48.0);
    label.style.font_weight = Some(400);
    label.style.line_height = Some(1.12);
    project = save(
        &project,
        Change::AddCanvasNode {
            scene_id,
            node: label,
        },
    );
    let original = project.clone();
    let rig = CameraReframe {
        id: Uuid::now_v7(),
        scene_id,
        component_version: CAMERA_DETAIL_RETURN_VERSION,
        start_center_x: 960.0,
        start_center_y: 540.0,
        end_center_x: 960.0,
        end_center_y: 540.0,
        duration_frames: 90,
        detail_return: Some(CameraDetailReturn {
            center_x: 980.0,
            center_y: 548.0,
            zoom: 1.15,
            approach_frames: 30,
            hold_frames: 30,
        }),
    };
    project = save(
        &project,
        Change::UpsertCameraReframe {
            reframe: rig.clone(),
        },
    );
    assert_eq!(project.scenes[0].nodes, original.scenes[0].nodes);
    assert_eq!(project.scenes[0].camera, original.scenes[0].camera);
    let options = options(&project);
    let films=project.deliverables.iter().map(|profile| {
        let output=build_motion_canvas_segments(&project,profile.id,&options).unwrap();
        assert_eq!(output.len(),1);
        assert_eq!(output[0].frame_count,180);
        let film=&output[0].film;
        let shot=film.shots().next().unwrap();
        let frame_id=format!("camera-frame-{}",rig.id.simple());
        let group=shot.subjects.iter().find(|subject|subject.id==frame_id).unwrap();
        assert!(matches!(group.content,SubjectContent::Group));
        assert_eq!(group.parent,None);
        assert_eq!(shot.subjects.iter().filter(|subject|subject.parent.is_none()).count(),1);
        for node in &original.scenes[0].nodes{
            let id=format!("node-{}",node.id.simple());
            let subject=shot.subjects.iter().find(|s|s.id==id).unwrap();
            assert_eq!(subject.parent.as_deref(),Some(frame_id.as_str()));
        }
        assert_eq!(shot.motion.len(),4);
        let zoom=Point{x:1.15,y:1.15};
        let shift=Point{x:-20.0*f64::from(profile.width)/1920.0*1.15,
            y:-8.0*f64::from(profile.height)/1080.0*1.15};
        let origin=Point{x:0.0,y:0.0};let unit=Point{x:1.0,y:1.0};
        for (phase,start,from_pos,to_pos,from_scale,to_scale) in [
            ("approach",0,origin,shift,unit,zoom),("return",2,shift,origin,zoom,unit)
        ] {
            let span_id=format!("camera-detail-{phase}-{}",rig.id.simple());
            let span=film.timing.spans.iter().find(|s|s.id==span_id).unwrap();
            assert_eq!(span.anchor,StartAnchor::Absolute {time:RationalTime::new(start,1).unwrap()});
            assert_eq!(span.minimum,RationalTime::new(1,1).unwrap());
            assert_eq!(span.minimum,span.maximum);
            for (channel,from,to) in [(Channel::Position,from_pos,to_pos),(Channel::Scale,from_scale,to_scale)] {
                assert!(shot.motion.iter().any(|inv| inv.span_id==span_id&&inv.easing==MotionEasing::Linear&&matches!(&inv.primitive,
                    Primitive::GeometryTween {target,channel:actual,from:Operand::Vector(a),to:Operand::Vector(b)}
                    if target==&frame_id && *actual==channel && (a.x-from.x).abs()<1e-8 && (a.y-from.y).abs()<1e-8 &&
                    (b.x-to.x).abs()<1e-8 && (b.y-to.y).abs()<1e-8)));
            }
        }
        assert!(realize(film).is_ok());
        (*film).clone()
    }).collect::<Vec<_>>();
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    let saved = reopened.project(project.id).unwrap();
    assert_eq!(saved, project);
    for (profile, film) in saved.deliverables.iter().zip(films) {
        assert_eq!(
            build_motion_canvas_segments(&saved, profile.id, &options)
                .unwrap()
                .remove(0)
                .film,
            film
        );
    }
    let final_project = reopened
        .apply(
            saved.id,
            &saved.stamp(),
            "detach-detail-return",
            &Change::DetachCameraReframe { id: rig.id },
        )
        .unwrap()
        .project;
    assert_eq!(final_project.scenes[0].nodes, original.scenes[0].nodes);
    assert_eq!(final_project.scenes[0].camera, original.scenes[0].camera);
    assert!(final_project.production_design.camera_reframes.is_empty());
}
