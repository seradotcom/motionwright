use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, MotionEasing, NarrativeRole, Primitive, realize};
use uuid::Uuid;

fn fixture() -> (Project, FilmBuildOptions, Uuid) {
    let mut p = Project::new("ProductHero native projection").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Reveal".into(),
        objective: "An editable graphic study".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let id = Uuid::new_v4();
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: HeroConfig::default(),
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
    (p, options, id)
}
#[test]
fn hero_entrance_is_real_canonical_motion_in_all_three_aspects() {
    let (p, options, _) = fixture();
    for profile in &p.deliverables {
        let segments = build_motion_canvas_segments(&p, profile.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        let film = &segments[0].film;
        assert_eq!(segments[0].frame_count, 180);
        assert_eq!(film.output.width, profile.width);
        assert_eq!(film.output.height, profile.height);
        let shot = &film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.subjects.len(), 6);
        assert_eq!(shot.motion.len(), 7);
        assert!(
            shot.motion
                .iter()
                .all(|motion| motion.easing == MotionEasing::OutCubic)
        );
        assert!(shot.motion.iter().any(|motion| matches!(motion.primitive, Primitive::Settle { rotation, .. } if rotation == -5.0)));
        assert!(
            shot.subjects
                .iter()
                .all(|subject| !subject.initially_visible)
        );
        let realization = realize(film).unwrap();
        assert_eq!(realization.instructions.len(), 13);
        assert!(
            realization
                .instructions
                .iter()
                .all(|instruction| instruction.duration > RationalTime::ZERO)
        );
    }
}
#[test]
fn easing_drift_and_extra_keys_are_rejected_not_approximated() {
    let (mut p, options, _) = fixture();
    let key = p.scenes[0].nodes[0].keyframes.last_mut().unwrap();
    key.interpolation = MotionInterpolation::EaseInOut;
    let error = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap_err();
    assert!(error.message.contains("motion keyframes"));
    let (mut p, options, _) = fixture();
    p.scenes[0].nodes[0].keyframes.push(CanvasKeyframe {
        at: RationalTime::new(2, 1).unwrap(),
        property: MotionProperty::Width,
        value: 900.0,
        interpolation: MotionInterpolation::Linear,
    });
    assert!(build_motion_canvas_segments(&p, p.deliverables[0].id, &options).is_err());
}
#[test]
fn human_position_override_blocks_ambiguous_reflow_but_landscape_still_works() {
    let (mut p, options, id) = fixture();
    let node = p.scenes[0]
        .nodes
        .iter_mut()
        .find(|n| n.id == hero_node_id(id, "headline"))
        .unwrap();
    node.x += 24.0;
    assert!(build_motion_canvas_segments(&p, p.deliverables[0].id, &options).is_ok());
    let error = build_motion_canvas_segments(&p, p.deliverables[1].id, &options).unwrap_err();
    assert!(error.message.contains("override conflict"));
}
#[test]
fn reduced_motion_is_static_and_does_not_fake_an_animated_result() {
    let (mut p, options, id) = fixture();
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: HeroConfig {
            motion: false,
            ..HeroConfig::default()
        },
    })
    .unwrap();
    let segments = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    assert!(
        segments[0].film.sequences[0].beats[0].shots[0]
            .motion
            .is_empty()
    );
    assert!(realize(&segments[0].film).unwrap().instructions.is_empty());
}
#[test]
fn motion_is_not_restarted_for_each_editorial_beat() {
    let (mut p, options, _) = fixture();
    p.scenes[0].beats.push(Beat {
        id: Uuid::new_v4(),
        label: "Beat".into(),
        objective: "Test explicit continuity boundary".into(),
        start: RationalTime::ZERO,
        duration: RationalTime::new(6, 1).unwrap(),
    });
    let error = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap_err();
    assert!(error.message.contains("continuity mapping"));
}

#[test]
fn unsupported_font_weight_is_rejected_before_an_expensive_native_render() {
    let (mut p, options, id) = fixture();
    p.scenes[0]
        .nodes
        .iter_mut()
        .find(|node| node.id == hero_node_id(id, "headline"))
        .unwrap()
        .style
        .font_weight = Some(650);
    let error = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap_err();
    assert!(error.message.contains("exact face evidence"));
    // The user-authored value is not coerced to the nearest loaded face.
    assert_eq!(
        p.scenes[0]
            .nodes
            .iter()
            .find(|node| node.id == hero_node_id(id, "headline"))
            .unwrap()
            .style
            .font_weight,
        Some(650)
    );
}
