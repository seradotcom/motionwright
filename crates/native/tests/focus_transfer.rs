//! Source-side save/reopen and exact typed Film transfer, not native pixels.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, MotionEasing, NarrativeRole, Primitive, realize};
use uuid::Uuid;

#[test]
fn focus_transfer_is_one_reopenable_patch_with_exact_native_fades() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("focus-transfer.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service.create_project("Original focus transfer").unwrap();
    let save = |p: &Project, change: Change| {
        service
            .apply(p.id, &p.stamp(), &Uuid::now_v7().to_string(), &change)
            .unwrap()
            .project
    };
    p = save(
        &p,
        Change::AddScene {
            name: "Focus".into(),
            objective: "Two original subjects".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    for (index, x) in [300.0, 700.0, 1100.0].into_iter().enumerate() {
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: format!("Original {index}"),
            kind: "rectangle".into(),
            parent_id: None,
            x,
            y: 300.0,
            width: 220.0,
            height: 100.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: index as i32,
            style: NodeStyle {
                fill: Some("#86C8D1".into()),
                stroke: None,
                stroke_width: 0.0,
                ..NodeStyle::default()
            },
            relations: vec![],
            property_locks: Default::default(),
            keyframes: vec![],
        };
        p = save(&p, Change::AddCanvasNode { scene_id, node });
    }
    let before = p.clone();
    let edits = p.scenes[0]
        .nodes
        .iter()
        .take(2)
        .enumerate()
        .map(|(i, node)| {
            let value = if i == 0 { 1.0 } else { 0.0 };
            ScopedCanvasEdit::Motion {
                node_id: node.id,
                keyframes: vec![
                    CanvasKeyframe {
                        at: RationalTime::ZERO,
                        property: MotionProperty::Opacity,
                        value,
                        interpolation: MotionInterpolation::Linear,
                    },
                    CanvasKeyframe {
                        at: RationalTime::new(1, 2).unwrap(),
                        property: MotionProperty::Opacity,
                        value,
                        interpolation: MotionInterpolation::Hold,
                    },
                    CanvasKeyframe {
                        at: RationalTime::new(3, 2).unwrap(),
                        property: MotionProperty::Opacity,
                        value: 1.0 - value,
                        interpolation: MotionInterpolation::Linear,
                    },
                ],
            }
        })
        .collect();
    let patch = CreativePatch {
        scene_id,
        base_revision: p.revision,
        rationale: "motionwright.transition.focus-transfer/v1".into(),
        edits,
    };
    assert_eq!(p.preview_creative_patch(&patch).unwrap().after.len(), 2);
    assert_eq!(service.project(p.id).unwrap(), p);
    p = save(&p, Change::ApplyCreativePatch { patch });
    assert_eq!(p.revision, before.revision + 1);
    assert_eq!(p.scenes[0].nodes[2], before.scenes[0].nodes[2]);
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1).unwrap(),
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
        }],
    };
    let profile_id = p.deliverables[0].id;
    let film = build_motion_canvas_segments(&p, profile_id, &options)
        .unwrap()
        .remove(0)
        .film;
    let motions = film
        .shots()
        .flat_map(|shot| &shot.motion)
        .collect::<Vec<_>>();
    assert_eq!(motions.len(), 2);
    assert!(
        motions
            .iter()
            .all(|motion| motion.easing == MotionEasing::Linear)
    );
    assert!(
        motions
            .iter()
            .any(|motion| matches!(motion.primitive, Primitive::FadeIn { .. }))
    );
    assert!(
        motions
            .iter()
            .any(|motion| matches!(motion.primitive, Primitive::FadeOut { .. }))
    );
    assert!(realize(&film).is_ok());
    let patch_id = p.production_design.patches.last().unwrap().id;
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let saved = reopened.project(p.id).unwrap();
    assert_eq!(saved, p);
    assert_eq!(
        build_motion_canvas_segments(&saved, profile_id, &options)
            .unwrap()
            .remove(0)
            .film,
        film
    );
    let undone = reopened
        .apply(
            saved.id,
            &saved.stamp(),
            "undo-focus",
            &Change::UndoCreativePatch { patch_id },
        )
        .unwrap()
        .project;
    assert_eq!(undone.scenes[0].nodes, before.scenes[0].nodes);
}

#[test]
fn original_mask_window_reopens_with_literal_clip_plane_and_exact_delayed_geometry() {
    use semwright_motion_authoring::{
        Channel, Operand, SpatialIntent, StartAnchor, SubjectContent,
    };
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("mask-window.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut p = service.create_project("Original mask window").unwrap();
    let save = |p: &Project, change: Change| {
        service
            .apply(p.id, &p.stamp(), &Uuid::now_v7().to_string(), &change)
            .unwrap()
            .project
    };
    p = save(
        &p,
        Change::AddScene {
            name: "Original Clip".into(),
            objective: "Reveal a saved shape through a fixed plane".into(),
            duration_seconds: 6,
        },
    );
    let scene_id = p.scenes[0].id;
    let clip_id = Uuid::now_v7();
    let child_id = Uuid::now_v7();
    let peer_id = Uuid::now_v7();
    for (id, parent, kind, name, x, y, width, height, z_index, fill) in [
        (
            clip_id,
            None,
            "clip_group",
            "Original Clip",
            800.0,
            340.0,
            320.0,
            240.0,
            0,
            None,
        ),
        (
            child_id,
            Some(clip_id),
            "rectangle",
            "Original clipped child",
            880.0,
            410.0,
            160.0,
            100.0,
            1,
            Some("#86C8D1"),
        ),
        (
            peer_id,
            None,
            "rectangle",
            "Original peer",
            1400.0,
            450.0,
            160.0,
            100.0,
            3,
            Some("#F2F4F3"),
        ),
    ] {
        p = save(
            &p,
            Change::AddCanvasNode {
                scene_id,
                node: CanvasNode {
                    id,
                    name: name.into(),
                    kind: kind.into(),
                    parent_id: parent,
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
                        fill: fill.map(str::to_owned),
                        stroke: None,
                        stroke_width: 0.0,
                        ..Default::default()
                    },
                    relations: Vec::new(),
                    property_locks: Default::default(),
                    keyframes: Vec::new(),
                },
            },
        );
    }
    let before = p.clone();
    let mut keyframes = Vec::new();
    for (at, x) in [
        (RationalTime::ZERO, 624.0),
        (RationalTime::new(1, 2).unwrap(), 624.0),
        (RationalTime::new(3, 2).unwrap(), 880.0),
    ] {
        for (property, value) in [
            (MotionProperty::X, x),
            (MotionProperty::Y, 410.0),
            (MotionProperty::Width, 160.0),
            (MotionProperty::Height, 100.0),
            (MotionProperty::RotationDeg, 0.0),
        ] {
            keyframes.push(CanvasKeyframe {
                at,
                property,
                value,
                interpolation: MotionInterpolation::Linear,
            });
        }
    }
    let patch = CreativePatch {
        scene_id,
        base_revision: p.revision,
        rationale: "motionwright.transition.mask-window/v1".into(),
        edits: vec![ScopedCanvasEdit::Motion {
            node_id: child_id,
            keyframes,
        }],
    };
    assert_eq!(p.preview_creative_patch(&patch).unwrap().after.len(), 1);
    assert_eq!(service.project(p.id).unwrap(), p);
    p = save(&p, Change::ApplyCreativePatch { patch });
    assert_eq!(p.revision, before.revision + 1);
    assert_eq!(p.scenes[0].nodes[0], before.scenes[0].nodes[0]);
    assert_eq!(p.scenes[0].nodes[2], before.scenes[0].nodes[2]);
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1).unwrap(),
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    };
    let films = p
        .deliverables
        .iter()
        .map(|profile| {
            let output = build_motion_canvas_segments(&p, profile.id, &options).unwrap();
            assert_eq!(output.len(), 1);
            assert_eq!(output[0].frame_count, 180);
            let film = &output[0].film;
            let shot = film.shots().next().unwrap();
            let clip = format!("node-{}", clip_id.simple());
            let child = format!("node-{}", child_id.simple());
            let group = shot
                .subjects
                .iter()
                .find(|subject| subject.id == clip)
                .unwrap();
            let moving = shot
                .subjects
                .iter()
                .find(|subject| subject.id == child)
                .unwrap();
            assert!(matches!(group.content, SubjectContent::Group));
            assert!(group.clip_intentional);
            assert!(!moving.clip_intentional);
            assert_eq!(moving.parent.as_deref(), Some(clip.as_str()));
            assert!(
                moving.initially_visible,
                "Visibility belongs to the Clip, not an invented fade"
            );
            let sx = f64::from(profile.width) / 1920.0;
            let scale = sx.min(f64::from(profile.height) / 1080.0);
            match (&group.layout, &moving.layout) {
                (
                    SpatialIntent::Fixed { size: outer, .. },
                    SpatialIntent::Fixed {
                        position,
                        size: inner,
                    },
                ) => {
                    assert!((outer.width - 320.0 * scale).abs() < 1e-6);
                    assert!((inner.width - 160.0 * scale).abs() < 1e-6);
                    assert!(position.x.abs() < 1e-6);
                    assert!(position.y.abs() < 1e-6);
                }
                _ => panic!("The original mask and child require fixed layout"),
            }
            assert!(shot.motion.iter().all(|motion| match &motion.primitive {
                Primitive::GeometryTween { target, .. } => target == &child,
                _ => false,
            }));
            for (index, start, length, from, to) in [
                (
                    0,
                    RationalTime::ZERO,
                    RationalTime::new(1, 2).unwrap(),
                    -256.0 * sx,
                    -256.0 * sx,
                ),
                (
                    1,
                    RationalTime::new(1, 2).unwrap(),
                    RationalTime::ONE,
                    -256.0 * sx,
                    0.0,
                ),
            ] {
                let span_id = format!("pose-track-{index}-{}", child_id.simple());
                let span = film
                    .timing
                    .spans
                    .iter()
                    .find(|span| span.id == span_id)
                    .unwrap();
                assert_eq!(span.anchor, StartAnchor::Absolute { time: start });
                assert_eq!(span.minimum, length);
                assert_eq!(span.minimum, span.maximum);
                let motion = shot
                    .motion
                    .iter()
                    .find(|motion| {
                        motion.span_id == span_id
                            && matches!(
                                &motion.primitive,
                                Primitive::GeometryTween {
                                    channel: Channel::Position,
                                    ..
                                }
                            )
                    })
                    .unwrap();
                assert_eq!(motion.easing, MotionEasing::Linear);
                match &motion.primitive {
                    Primitive::GeometryTween {
                        from: Operand::Vector(a),
                        to: Operand::Vector(b),
                        ..
                    } => {
                        assert!((a.x - from).abs() < 1e-6 && (b.x - to).abs() < 1e-6);
                        assert!(a.y.abs() < 1e-6 && b.y.abs() < 1e-6);
                    }
                    _ => panic!("Mask requires literal local-space position operands"),
                }
            }
            assert!(realize(film).is_ok());
            (*film).clone()
        })
        .collect::<Vec<_>>();
    let patch_id = p.production_design.patches.last().unwrap().id;
    drop(service);
    let reopened = StudioService::open(&db).unwrap();
    let saved = reopened.project(p.id).unwrap();
    assert_eq!(saved, p);
    for (profile, film) in saved.deliverables.iter().zip(films) {
        assert_eq!(
            build_motion_canvas_segments(&saved, profile.id, &options)
                .unwrap()
                .remove(0)
                .film,
            film
        );
    }
    let undone = reopened
        .apply(
            saved.id,
            &saved.stamp(),
            "undo-mask-window",
            &Change::UndoCreativePatch { patch_id },
        )
        .unwrap()
        .project;
    assert_eq!(undone.scenes[0].nodes, before.scenes[0].nodes);
}
