use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, MotionEasing, NarrativeRole, Primitive, realize};
use uuid::Uuid;

fn project(config: ProceduralConfig) -> (Project, FilmBuildOptions) {
    let mut p = Project::new("Native procedural Canvas projection").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Field".into(),
        objective: "Native deterministic repetition".into(),
        duration_seconds: 6,
    })
    .unwrap();
    p.apply_change(&Change::UpsertProceduralField {
        instance_id: Uuid::parse_str("00000000-0000-4000-8000-000000000095").unwrap(),
        scene_id: p.scenes[0].id,
        config,
    })
    .unwrap();
    let options = FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: p.scenes[0].id,
            role: NarrativeRole::Reveal,
            archetype: Archetype::ObjectSpotlight,
        }],
    };
    (p, options)
}

#[test]
fn sixty_four_shapes_render_as_real_native_subjects_without_layer_explosion() {
    let (p, options) = project(ProceduralConfig {
        distribution: FieldDistribution::Grid,
        count: 64,
        columns: 16,
        ..Default::default()
    });
    let film = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    assert_eq!(film.len(), 1);
    assert_eq!(film[0].frame_count, 180);
    let shot = &film[0].film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.subjects.len(), 64);
    assert_eq!(shot.layers.len(), 1);
    assert!(shot.motion.is_empty());
    assert!(realize(&film[0].film).is_ok());
}

#[test]
fn staggered_entries_are_admitted_as_native_editable_y_opacity_motion() {
    let (p, options) = project(ProceduralConfig {
        count: 5,
        columns: 5,
        reveal_step_frames: 3,
        reveal_duration_frames: 12,
        ..Default::default()
    });
    let film = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    let shot = &film[0].film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.subjects.len(), 5);
    assert_eq!(shot.motion.len(), 5);
    assert!(
        shot.motion
            .iter()
            .all(|motion| motion.easing == MotionEasing::OutCubic
                && matches!(motion.primitive, Primitive::SlideIn { .. }))
    );
    assert!(
        shot.subjects
            .iter()
            .all(|subject| !subject.initially_visible)
    );
    assert!(realize(&film[0].film).is_ok());
}

#[test]
fn unsupported_partial_final_alpha_is_explicitly_rejected_not_baked() {
    let (p, options) = project(ProceduralConfig {
        opacity_percent: 75,
        ..Default::default()
    });
    let err = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap_err();
    assert!(err.message.contains("saved static opacity"));
}

#[test]
fn native_radial_circles_preserve_unique_semantic_subjects_and_motion() {
    for distribution in [FieldDistribution::Radial, FieldDistribution::Spiral] {
        let (p, options) = project(ProceduralConfig {
            shape: ProceduralShape::Circle,
            distribution,
            reveal_step_frames: 6,
            reveal_duration_frames: 18,
            ..Default::default()
        });
        let projected = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
        let shot = &projected[0].film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.subjects.len(), 12);
        assert_eq!(shot.layers.len(), 1);
        assert_eq!(shot.motion.len(), 12);
        assert!(
            shot.subjects
                .iter()
                .all(|item| item.role == "circle" && !item.initially_visible)
        );
        assert!(
            shot.motion
                .iter()
                .all(|motion| matches!(motion.primitive, Primitive::SlideIn { .. }))
        );
        assert!(realize(&projected[0].film).is_ok());
    }
}

#[test]
fn influence_geometry_and_color_remain_native_shapes_with_exact_static_rotations() {
    let (p, options) = project(ProceduralConfig {
        distribution: FieldDistribution::Grid,
        count: 3,
        columns: 3,
        origin_x: 200,
        origin_y: 200,
        area_width: 300,
        area_height: 100,
        size: 40,
        fill: "#000000".into(),
        influence: Some(ProceduralInfluence {
            shape: InfluenceShape::Radial,
            falloff: InfluenceFalloff::Linear,
            center_x: 350,
            center_y: 250,
            radius: 200,
            offset_x: 20,
            offset_y: -10,
            scale_percent: 150,
            rotation_deg: 30,
            fill: "#FFFFFF".into(),
        }),
        ..Default::default()
    });
    let source = p.clone();
    let film = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    let shot = &film[0].film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.subjects.len(), 3);
    assert_eq!(shot.layers.len(), 1);
    let mut rotations = shot
        .motion
        .iter()
        .filter_map(|motion| match &motion.primitive {
            Primitive::GeometrySet {
                channel: semwright_motion_authoring::Channel::Rotation,
                value: semwright_motion_authoring::Operand::Number(value),
                ..
            } => Some(*value as i32),
            _ => None,
        })
        .collect::<Vec<_>>();
    rotations.sort();
    assert_eq!(rotations, vec![15, 15, 30]);
    assert!(realize(&film[0].film).is_ok());
    assert_eq!(p, source);
}

#[test]
fn sequenced_influence_uses_the_original_native_y_opacity_path_without_baking() {
    for shape in [
        InfluenceShape::Radial,
        InfluenceShape::Horizontal,
        InfluenceShape::Vertical,
    ] {
        let (p, options) = project(ProceduralConfig {
            count: 5,
            columns: 5,
            reveal_step_frames: 3,
            influence: Some(ProceduralInfluence {
                shape,
                falloff: InfluenceFalloff::Smoothstep,
                center_x: 630,
                center_y: 450,
                radius: 500,
                offset_x: 10,
                offset_y: -5,
                scale_percent: 150,
                rotation_deg: 0,
                fill: "#D0AA80".into(),
            }),
            ..Default::default()
        });
        let film = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
        let shot = &film[0].film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.subjects.len(), 5);
        assert_eq!(shot.motion.len(), 5);
        assert!(
            shot.motion
                .iter()
                .all(|motion| matches!(motion.primitive, Primitive::SlideIn { .. }))
        );
        assert!(
            shot.subjects
                .iter()
                .all(|subject| !subject.initially_visible)
        );
        assert!(realize(&film[0].film).is_ok());
    }
}

#[test]
fn oriented_influence_entrances_keep_literal_rotation_and_original_delayed_motion() {
    use semwright_motion_authoring::{Channel, NativeOp, Operand};
    let (p, options) = project(ProceduralConfig {
        count: 3,
        columns: 3,
        origin_x: 200,
        origin_y: 200,
        area_width: 300,
        area_height: 100,
        size: 40,
        reveal_step_frames: 3,
        influence: Some(ProceduralInfluence {
            shape: InfluenceShape::Radial,
            falloff: InfluenceFalloff::Linear,
            center_x: 350,
            center_y: 250,
            radius: 200,
            offset_x: 20,
            offset_y: -10,
            scale_percent: 150,
            rotation_deg: 30,
            fill: "#FFFFFF".into(),
        }),
        ..Default::default()
    });
    let original = p.clone();
    let segments = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    let shot = &segments[0].film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.subjects.len(), 3);
    assert_eq!(
        shot.motion
            .iter()
            .filter(|motion| matches!(motion.primitive, Primitive::SlideIn { .. }))
            .count(),
        3
    );
    let lowered = realize(&segments[0].film).unwrap();
    let rotations = lowered
        .instructions
        .iter()
        .filter_map(|instruction| match &instruction.operation {
            NativeOp::Set {
                target,
                channel: Channel::Rotation,
                value: Operand::Number(angle),
            } => {
                assert_eq!(instruction.start, RationalTime::ZERO);
                assert_eq!(instruction.duration, RationalTime::ZERO);
                Some((target.clone(), *angle))
            }
            NativeOp::Tween {
                channel: Channel::Rotation,
                ..
            } => panic!("An entrance must not overwrite constant orientation"),
            _ => None,
        })
        .collect::<Vec<_>>();
    assert_eq!(rotations.len(), 3);
    for (subject, node) in shot.subjects.iter().zip(&p.scenes[0].nodes) {
        assert_eq!(
            rotations
                .iter()
                .filter(|(target, _)| target == &subject.id)
                .map(|(_, angle)| *angle)
                .collect::<Vec<_>>(),
            vec![node.rotation_deg]
        );
    }
    assert_eq!(p, original);
}

#[test]
fn authored_paths_and_adaptive_grids_remain_editable_native_subjects_with_exact_entrances() {
    for distribution in [FieldDistribution::Path, FieldDistribution::AdaptiveGrid] {
        for step in [0, 3] {
            let (p, options) = project(ProceduralConfig {
                distribution,
                count: 4,
                origin_x: 100,
                origin_y: 100,
                area_width: 420,
                area_height: 220,
                size: 20,
                reveal_step_frames: step,
                path: (distribution == FieldDistribution::Path).then(|| ProceduralPath {
                    closed: false,
                    points: vec![
                        ProceduralPathPoint {
                            x_percent: 0,
                            y_percent: 0,
                        },
                        ProceduralPathPoint {
                            x_percent: 100,
                            y_percent: 0,
                        },
                        ProceduralPathPoint {
                            x_percent: 100,
                            y_percent: 100,
                        },
                    ],
                }),
                ..Default::default()
            });
            let original = p.clone();
            let segments =
                build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
            assert_eq!(segments.len(), 1);
            assert_eq!(segments[0].frame_count, 180);
            let shot = &segments[0].film.sequences[0].beats[0].shots[0];
            assert_eq!(shot.subjects.len(), 4);
            assert_eq!(shot.layers.len(), 1);
            assert_eq!(shot.motion.len(), if step == 0 { 0 } else { 4 });
            assert!(shot.motion.iter().all(|motion| matches!(
                motion.primitive,
                Primitive::SlideIn { .. }
            ) && motion.easing == MotionEasing::OutCubic));
            assert!(
                shot.subjects
                    .iter()
                    .all(|subject| subject.initially_visible == (step == 0))
            );
            assert!(realize(&segments[0].film).is_ok());
            assert_eq!(p, original);
            assert_eq!(
                p.production_design.procedural_fields[0].baseline,
                p.scenes[0].nodes
            );
        }
    }
}

#[test]
fn adaptive_variants_keep_native_shapes_entrances_and_human_appearance_across_aspects() {
    use semwright_motion_authoring::{SpatialIntent, SubjectContent};
    use std::collections::BTreeSet;
    let (mut p, options) = project(ProceduralConfig {
        distribution: FieldDistribution::AdaptiveGrid,
        count: 24,
        reveal_step_frames: 3,
        ..Default::default()
    });
    let first = p.scenes[0].nodes[0].clone();
    let mut style = first.style.clone();
    style.fill = Some("#FFAA22".into());
    p.apply_change(&Change::UpdateCanvasStyle {
        scene_id: p.scenes[0].id,
        node_id: first.id,
        style,
    })
    .unwrap();
    let source_nodes = p.scenes[0].nodes.clone();
    let field = p.production_design.procedural_fields[0].clone();
    for (width, height, columns, rows) in
        [(1920, 1080, 6, 4), (1080, 1080, 5, 5), (1080, 1920, 4, 6)]
    {
        p.deliverables[0].width = width;
        p.deliverables[0].height = height;
        p.deliverables[0].framing_strategy = FramingStrategy::Replan;
        let source = p.clone();
        let segments = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
        let shot = &segments[0].film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.subjects.len(), 24);
        assert_eq!(shot.motion.len(), 24);
        assert_eq!(shot.layers.len(), 1);
        let (mut xs, mut ys) = (BTreeSet::new(), BTreeSet::new());
        for subject in &shot.subjects {
            match &subject.layout {
                SpatialIntent::Fixed { position, size } => {
                    xs.insert((position.x * 1000.0).round() as i64);
                    ys.insert((position.y * 1000.0).round() as i64);
                    assert_eq!(size.width, if width == 1920 { 32.0 } else { 18.0 });
                    assert_eq!(size.width, size.height);
                }
                _ => panic!("Every field item must retain its real native geometry"),
            }
        }
        assert_eq!((xs.len(), ys.len()), (columns, rows));
        let original = shot
            .subjects
            .iter()
            .find(|item| item.id == format!("node-{}", first.id.simple()))
            .unwrap();
        assert!(
            matches!(&original.content, SubjectContent::Rectangle { fill, .. } if fill == "#FFAA22")
        );
        assert!(realize(&segments[0].film).is_ok());
        assert_eq!(p, source);
    }
    assert_eq!(p.scenes[0].nodes, source_nodes);
    assert_eq!(p.production_design.procedural_fields[0], field);
}

#[test]
fn responsive_native_composition_keeps_both_hero_and_field_and_refuses_conflicting_overrides() {
    let (mut p, options) = project(ProceduralConfig {
        distribution: FieldDistribution::AdaptiveGrid,
        count: 24,
        ..Default::default()
    });
    let field_nodes = p.scenes[0].nodes.clone();
    p.apply_change(&Change::UpsertProductHero {
        instance_id: Uuid::parse_str("00000000-0000-4000-8000-000000000005").unwrap(),
        scene_id: p.scenes[0].id,
        config: HeroConfig {
            motion: false,
            ..Default::default()
        },
    })
    .unwrap();
    p.deliverables[0].width = 1080;
    p.deliverables[0].height = 1920;
    let source = p.clone();
    let film = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    let subjects = &film[0].film.sequences[0].beats[0].shots[0].subjects;
    for node in &field_nodes {
        assert!(
            subjects
                .iter()
                .any(|subject| subject.id == format!("node-{}", node.id.simple()))
        );
    }
    assert!(
        subjects
            .iter()
            .any(|subject| subject.role == "component-text-container")
    );
    assert!(realize(&film[0].film).is_ok());
    assert_eq!(p, source);
    p.scenes[0]
        .nodes
        .iter_mut()
        .find(|node| node.id == field_nodes[0].id)
        .unwrap()
        .x += 5.0;
    let source = p.clone();
    let error = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap_err();
    assert!(error.message.contains("override conflict"));
    assert_eq!(p, source);
}
