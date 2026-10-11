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
    assert!(err.message.contains("rotation/opacity state"));
}
