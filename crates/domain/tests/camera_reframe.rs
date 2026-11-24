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

#[test]
fn contextual_follow_projects_complete_paths_and_own_visibility_across_source_reopen() {
    let (mut project, scene_id) = setup();
    let root = project.scenes[0].nodes[0].clone();
    let mut keys = Vec::new();
    for (at, x, curve) in [
        (RationalTime::ZERO, 300.0, MotionInterpolation::Linear),
        (RationalTime::ONE, 360.0, MotionInterpolation::EaseOutCubic),
        (
            RationalTime::new(2, 1).unwrap(),
            root.x,
            MotionInterpolation::Hold,
        ),
    ] {
        for (property, value) in [
            (MotionProperty::X, x),
            (MotionProperty::Y, root.y),
            (MotionProperty::Width, root.width),
            (MotionProperty::Height, root.height),
            (MotionProperty::RotationDeg, 0.0),
        ] {
            keys.push(CanvasKeyframe {
                at,
                property,
                value,
                interpolation: curve,
            });
        }
    }
    project
        .apply_change(&Change::SetCanvasMotionKeyframes {
            scene_id,
            node_id: root.id,
            keyframes: keys,
        })
        .unwrap();
    let mut label = node("Editable contextual label", 780.0, 560.0);
    label.kind = "text".into();
    label.text = Some("Authored context".into());
    label.width = 320.0;
    label.height = 96.0;
    label.style.font_family = Some("Instrument Sans Variable".into());
    label.style.font_size = Some(32.0);
    label.relations.push(NodeRelation {
        id: Uuid::new_v4(),
        target_id: root.id,
        kind: RelationKind::Follow,
    });
    label.keyframes = vec![
        CanvasKeyframe {
            at: RationalTime::ZERO,
            property: MotionProperty::Opacity,
            value: 0.0,
            interpolation: MotionInterpolation::Linear,
        },
        CanvasKeyframe {
            at: RationalTime::ONE,
            property: MotionProperty::Opacity,
            value: 1.0,
            interpolation: MotionInterpolation::Linear,
        },
    ];
    project
        .apply_change(&Change::AddCanvasNode {
            scene_id,
            node: label.clone(),
        })
        .unwrap();
    project
        .apply_change(&Change::UpsertCameraReframe {
            reframe: reframe(scene_id),
        })
        .unwrap();
    let serialized = serde_json::to_vec(&project).unwrap();
    let mut reopened: Project = serde_json::from_slice(&serialized).unwrap();
    let before = reopened.clone();
    let tracks = resolve_canvas_follow_tracks(
        &reopened.scenes[0],
        RationalTime::new(30, 1).unwrap(),
        false,
    )
    .unwrap();
    let derived = &tracks[&label.id];
    assert_eq!(derived.len(), 17);
    let mut projected_label = label.clone();
    projected_label.keyframes = derived.clone();
    projected_label.relations.clear();
    let points = exact_canvas_pose_waypoints(
        &projected_label,
        &reopened.scenes[0],
        RationalTime::new(30, 1).unwrap(),
    )
    .unwrap()
    .unwrap();
    assert_eq!(
        points.iter().map(|point| point.pose.x).collect::<Vec<_>>(),
        vec![640.0, 700.0, 780.0]
    );
    assert_eq!(points[1].curve, MotionInterpolation::EaseOutCubic);
    assert_eq!(points[2].curve, MotionInterpolation::Hold);
    assert!(points.iter().all(|point| point.pose.width == 320.0
        && point.pose.height == 96.0
        && point.pose.rotation_deg == 0.0));
    assert_eq!(
        derived
            .iter()
            .filter(|key| key.property == MotionProperty::Opacity)
            .cloned()
            .collect::<Vec<_>>(),
        label.keyframes
    );
    assert_eq!(reopened, before);
    let changed = vec![
        CanvasKeyframe {
            at: RationalTime::ZERO,
            property: MotionProperty::Opacity,
            value: 1.0,
            interpolation: MotionInterpolation::Linear,
        },
        CanvasKeyframe {
            at: RationalTime::ONE,
            property: MotionProperty::Opacity,
            value: 0.0,
            interpolation: MotionInterpolation::EaseOutCubic,
        },
    ];
    reopened
        .apply_change(&Change::SetCanvasMotionKeyframes {
            scene_id,
            node_id: label.id,
            keyframes: changed.clone(),
        })
        .unwrap();
    let saved = reopened.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == label.id)
        .unwrap();
    assert_eq!(saved.keyframes, changed);
    assert_eq!(saved.relations, label.relations);
    assert_eq!(saved.text, label.text);
    assert_eq!(reopened.scenes[0].nodes[0], before.scenes[0].nodes[0]);
}

#[test]
fn contextual_follow_refuses_non_translation_and_never_rewrites_original_source() {
    let (mut project, _) = setup();
    let root = project.scenes[0].nodes[0].clone();
    let target_id = root.id;
    project.scenes[0].nodes[0].keyframes = vec![
        CanvasKeyframe {
            at: RationalTime::ZERO,
            property: MotionProperty::X,
            value: 300.0,
            interpolation: MotionInterpolation::Linear,
        },
        CanvasKeyframe {
            at: RationalTime::ZERO,
            property: MotionProperty::Y,
            value: root.y,
            interpolation: MotionInterpolation::Linear,
        },
        CanvasKeyframe {
            at: RationalTime::ONE,
            property: MotionProperty::X,
            value: root.x,
            interpolation: MotionInterpolation::Hold,
        },
        CanvasKeyframe {
            at: RationalTime::ONE,
            property: MotionProperty::Y,
            value: root.y,
            interpolation: MotionInterpolation::Hold,
        },
    ];
    project.scenes[0].nodes[1].relations = vec![NodeRelation {
        id: Uuid::new_v4(),
        target_id,
        kind: RelationKind::Follow,
    }];
    for issue in [
        "curve",
        "time",
        "size",
        "own_geometry",
        "opacity",
        "cycle",
        "locked",
    ] {
        let mut scene = project.scenes[0].clone();
        match issue {
            "curve" => scene.nodes[0].keyframes[2].interpolation = MotionInterpolation::EaseInOut,
            "time" => scene.nodes[0].keyframes[2].at = RationalTime::new(1, 31).unwrap(),
            "size" => scene.nodes[0].keyframes.push(CanvasKeyframe {
                at: RationalTime::ZERO,
                property: MotionProperty::Width,
                value: root.width + 1.0,
                interpolation: MotionInterpolation::Linear,
            }),
            "own_geometry" => scene.nodes[1].keyframes = scene.nodes[0].keyframes.clone(),
            "opacity" => {
                scene.nodes[1].keyframes = vec![CanvasKeyframe {
                    at: RationalTime::ZERO,
                    property: MotionProperty::Opacity,
                    value: 0.5,
                    interpolation: MotionInterpolation::Linear,
                }]
            }
            "cycle" => {
                scene.nodes[0].relations = vec![NodeRelation {
                    id: Uuid::new_v4(),
                    target_id: scene.nodes[1].id,
                    kind: RelationKind::Follow,
                }];
            }
            "locked" => {
                scene.nodes[1].property_locks.insert(NodeProperty::Position);
            }
            _ => unreachable!(),
        }
        let before = scene.clone();
        assert!(
            resolve_canvas_follow_tracks(&scene, RationalTime::new(30, 1).unwrap(), false).is_err(),
            "{issue}"
        );
        assert_eq!(scene, before);
    }
}

#[test]
fn scoped_pose_moves_base_and_complete_track_atomically_under_camera_in_either_order() {
    let (mut source, scene_id) = setup();
    let target = &source.scenes[0].nodes[0];
    let mut keys = Vec::new();
    for at in [RationalTime::ZERO, RationalTime::new(1, 1).unwrap()] {
        for (property, value) in [
            (MotionProperty::X, target.x),
            (MotionProperty::Y, target.y),
            (MotionProperty::Width, target.width),
            (MotionProperty::Height, target.height),
            (MotionProperty::RotationDeg, target.rotation_deg),
        ] {
            keys.push(CanvasKeyframe {
                at,
                property,
                value: value
                    - if at == RationalTime::ZERO && property == MotionProperty::X {
                        40.0
                    } else {
                        0.0
                    },
                interpolation: if at == RationalTime::ZERO {
                    MotionInterpolation::Linear
                } else {
                    MotionInterpolation::EaseOutCubic
                },
            });
        }
    }
    source.scenes[0].nodes[0].keyframes = keys;
    source
        .apply_change(&Change::UpsertCameraReframe {
            reframe: reframe(scene_id),
        })
        .unwrap();
    let frozen = source.clone();
    let original = &source.scenes[0].nodes[0];
    let target_id = original.id;
    let transform = CanvasTransform {
        x: original.x + 20.0,
        y: original.y,
        width: original.width,
        height: original.height,
        rotation_deg: original.rotation_deg,
        opacity: original.opacity,
    };
    let shifted: Vec<_> = original
        .keyframes
        .iter()
        .cloned()
        .map(|mut key| {
            if key.property == MotionProperty::X {
                key.value += 20.0;
            }
            key
        })
        .collect();
    let base_edits = vec![
        ScopedCanvasEdit::Transform {
            node_id: target_id,
            transform,
        },
        ScopedCanvasEdit::Motion {
            node_id: target_id,
            keyframes: shifted.clone(),
        },
    ];
    for reverse in [false, true] {
        let mut edits = base_edits.clone();
        if reverse {
            edits.reverse();
        }
        let patch = CreativePatch {
            scene_id,
            base_revision: source.revision,
            rationale: "Move the whole authored entrance while preserving its camera".into(),
            edits,
        };
        let preview = source.preview_creative_patch(&patch).unwrap();
        assert_eq!(source, frozen);
        assert_eq!(preview.before, vec![original.clone()]);
        assert_eq!(preview.after[0].x, original.x + 20.0);
        assert_eq!(preview.after[0].keyframes, shifted);
        let mut saved = source.clone();
        saved
            .apply_change(&Change::ApplyCreativePatch { patch })
            .unwrap();
        saved.validate().unwrap();
        assert_eq!(
            saved.production_design.camera_reframes,
            source.production_design.camera_reframes
        );
        let mut reopened: Project =
            serde_json::from_slice(&serde_json::to_vec(&saved).unwrap()).unwrap();
        assert_eq!(reopened, saved);
        let patch_id = reopened.production_design.patches.last().unwrap().id;
        reopened
            .apply_change(&Change::UndoCreativePatch { patch_id })
            .unwrap();
        assert_eq!(reopened.scenes, source.scenes);
    }
    for property in [
        NodeProperty::Position,
        NodeProperty::Size,
        NodeProperty::Rotation,
    ] {
        let mut locked = source.clone();
        locked.scenes[0].nodes[0].property_locks.insert(property);
        let before = locked.clone();
        let patch = CreativePatch {
            scene_id,
            base_revision: locked.revision,
            rationale: "A layout must preserve locks".into(),
            edits: base_edits.clone(),
        };
        assert!(
            locked
                .apply_change(&Change::ApplyCreativePatch { patch })
                .is_err()
        );
        assert_eq!(locked, before);
    }
    let mut invalid = shifted;
    invalid.push(invalid[0].clone());
    let patch = CreativePatch {
        scene_id,
        base_revision: source.revision,
        rationale: "Malformed paired tracks must preserve all source".into(),
        edits: vec![
            base_edits[0].clone(),
            ScopedCanvasEdit::Motion {
                node_id: target_id,
                keyframes: invalid,
            },
        ],
    };
    assert!(
        source
            .apply_change(&Change::ApplyCreativePatch { patch })
            .is_err()
    );
    assert_eq!(source, frozen);
}
