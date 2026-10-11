use motionwright_domain::*;
use std::collections::BTreeSet;
use uuid::Uuid;

fn node(name: &str, x: f64, y: f64) -> CanvasNode {
    CanvasNode {
        id: Uuid::new_v4(),
        name: name.into(),
        kind: "rectangle".into(),
        parent_id: None,
        x,
        y,
        width: 200.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle {
            fill: Some("#86C8D1".into()),
            ..NodeStyle::default()
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn setup() -> (Project, Uuid) {
    let mut project = Project::new("Bounded native camera track").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Original visible composition".into(),
            objective: "An exact 2D semantic pan for native Film".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let scene_id = project.scenes[0].id;
    for (i, canvas) in [
        node("Card left", 440.0, 420.0),
        node("Card right", 1000.0, 460.0),
    ]
    .into_iter()
    .enumerate()
    {
        project
            .apply_change(&Change::AddCanvasNode {
                scene_id,
                node: canvas,
            })
            .unwrap();
        assert_eq!(project.scenes[0].nodes.len(), i + 1);
    }
    (project, scene_id)
}
fn reframe(scene_id: Uuid) -> CameraReframe {
    CameraReframe {
        id: Uuid::parse_str("00000000-0000-4000-8000-000000000203").unwrap(),
        scene_id,
        component_version: CAMERA_REFRAME_VERSION,
        start_center_x: 960.0,
        start_center_y: 540.0,
        end_center_x: 980.0,
        end_center_y: 546.0,
        duration_frames: 90,
    }
}
#[test]
fn authored_pan_is_editable_original_camera_without_flattening_subjects_or_losing_identity() {
    let (mut p, scene_id) = setup();
    let objects = p.scenes[0].nodes.clone();
    let rig = reframe(scene_id);
    p.apply_change(&Change::UpsertCameraReframe {
        reframe: rig.clone(),
    })
    .unwrap();
    assert_eq!(p.scenes[0].nodes, objects);
    assert_eq!(p.scenes[0].camera.center_x, 980.0);
    assert_eq!(p.scenes[0].camera.center_y, 546.0);
    assert_eq!(p.scenes[0].status, SceneStatus::Draft);
    assert_eq!(p.production_design.camera_reframes, vec![rig.clone()]);
    p.validate().unwrap();
    let json = serde_json::to_value(&p).unwrap();
    let restored: Project = serde_json::from_value(json).unwrap();
    assert_eq!(restored, p);
    p.apply_change(&Change::DetachCameraReframe { id: rig.id })
        .unwrap();
    assert_eq!(p.scenes[0].nodes, objects);
    assert_eq!(p.scenes[0].camera.center_x, 980.0);
    assert!(p.production_design.camera_reframes.is_empty());
    p.validate().unwrap();
}
#[test]
fn camera_pan_uses_bounded_source_frame_and_rejects_cropping_and_unadmitted_lens_motion() {
    let (mut p, scene_id) = setup();
    let mut invalid = reframe(scene_id);
    invalid.end_center_x = 1260.0;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe { reframe: invalid })
            .is_err()
    );
    invalid = reframe(scene_id);
    invalid.duration_frames = 0;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe { reframe: invalid })
            .is_err()
    );
    invalid = reframe(scene_id);
    invalid.end_center_x = 960.0;
    invalid.end_center_y = 540.0;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe { reframe: invalid })
            .is_err()
    );
    invalid = reframe(scene_id);
    invalid.end_center_x = 1100.0;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe { reframe: invalid })
            .is_err()
    );
    let first = p.clone();
    p.scenes[0].nodes[0].x = 86.0;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe {
            reframe: reframe(scene_id)
        })
        .unwrap_err()
        .to_string()
        .contains("crop")
    );
    p = first;
    p.scenes[0].camera.zoom = 1.2;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe {
            reframe: reframe(scene_id)
        })
        .unwrap_err()
        .to_string()
        .contains("optical zoom")
    );
}
#[test]
fn camera_pan_refuses_edit_conflicts_and_scene_motion_without_mutating_authority() {
    let (mut p, scene_id) = setup();
    let original = p.clone();
    let mut wrong = reframe(scene_id);
    wrong.component_version = 14;
    assert!(
        p.apply_change(&Change::UpsertCameraReframe { reframe: wrong })
            .is_err()
    );
    assert_eq!(p, original);
    let mut changed = p.clone();
    let rig = reframe(scene_id);
    changed
        .apply_change(&Change::UpsertCameraReframe {
            reframe: rig.clone(),
        })
        .unwrap();
    let snapshot = changed.clone();
    let mut duplicate = reframe(scene_id);
    duplicate.id = Uuid::new_v4();
    assert!(
        changed
            .apply_change(&Change::UpsertCameraReframe { reframe: duplicate })
            .is_err()
    );
    assert_eq!(changed, snapshot);

    // An independent position-keyframe trajectory must never be overwritten
    // by a uniform camera pan from the same authored frame origin.
    let mut with_motion = original.clone();
    let node_id = with_motion.scenes[0].nodes[0].id;
    with_motion
        .apply_change(&Change::SetCanvasKeyframe {
            scene_id,
            node_id,
            keyframe: CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::X,
                value: 440.0,
                interpolation: MotionInterpolation::Hold,
            },
        })
        .unwrap();
    assert!(
        with_motion
            .apply_change(&Change::UpsertCameraReframe { reframe: rig })
            .unwrap_err()
            .to_string()
            .contains("motion-free")
    );

    // Existing scene lock is project-authoritative even with valid metadata.
    let mut locked = original.clone();
    locked.locks.push(ProjectLock {
        id: Uuid::new_v4(),
        resource: format!("scene:{scene_id}"),
        kind: LockKind::Position,
        note: "Owned cinematography lock".into(),
    });
    assert!(
        locked
            .apply_change(&Change::UpsertCameraReframe {
                reframe: reframe(scene_id)
            })
            .unwrap_err()
            .to_string()
            .contains("locked")
    );
}
#[test]
fn old_serialized_production_designs_without_camera_moves_remain_readable() {
    let (p, _) = setup();
    let mut serialized = serde_json::to_value(&p).unwrap();
    serialized["production_design"]
        .as_object_mut()
        .unwrap()
        .remove("camera_reframes");
    let restored: Project = serde_json::from_value(serialized).unwrap();
    assert!(restored.production_design.camera_reframes.is_empty());
    assert_eq!(restored.scenes, p.scenes);
}
