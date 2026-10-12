//! Real canonical Native SDK Film construction, NOT a browser screenshot.
//! Independent provider pixels are accepted only by the Semwright CI runner.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, MotionEasing, NarrativeRole, realize};
use uuid::Uuid;

fn fixture(animated: bool) -> (Project, FilmBuildOptions, Uuid) {
    let mut project = Project::new("StepSequence native film").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Step 02 / inspect".into(),
            objective: "An authored process step with non-fabricated disclosure".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let instance_id = Uuid::new_v4();
    project.apply_change(&Change::UpsertProductHero {
        instance_id,
        scene_id: project.scenes[0].id,
        config: HeroConfig {
            layout: HeroLayout::StepSequence,
            eyebrow: "WORKFLOW / OPERATOR-AUTHORED".into(),
            headline: "Inspect the intended result".into(),
            body: "Read the actual source changes and confirm what happened before deciding the next step.".into(),
            wordmark: "02 / 04".into(),
            accent: "#D9A46E".into(),
            motion: animated,
            ..HeroConfig::default()
        },
    }).unwrap();
    let options = FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: project.scenes[0].id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    };
    (project, options, instance_id)
}

#[test]
fn native_film_preserves_six_stable_source_objects_and_disclosure_in_all_three_aspects() {
    let (project, options, instance_id) = fixture(true);
    for profile in &project.deliverables {
        let segments = build_motion_canvas_segments(&project, profile.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        let media = &segments[0];
        assert_eq!(media.frame_count, 180);
        assert_eq!(
            (media.film.output.width, media.film.output.height),
            (profile.width, profile.height)
        );
        let shot = &media.film.sequences[0].beats[0].shots[0];
        assert_eq!(
            shot.subjects.iter().filter(|s| s.parent.is_none()).count(),
            6
        );
        assert_eq!(
            shot.subjects
                .iter()
                .filter(|s| s.role == "component-text-container")
                .count(),
            5
        );
        assert!(shot.subjects.iter().any(|s| s.id.ends_with("-line-1")));
        assert!(
            shot.subjects
                .iter()
                .filter(|s| s.parent.is_none())
                .all(|s| !s.initially_visible)
        );
        assert!(
            shot.motion
                .iter()
                .all(|m| m.easing == MotionEasing::OutCubic)
        );
        assert!(!shot.motion.is_empty());
        let realized = realize(&media.film).unwrap();
        assert!(!realized.instructions.is_empty());
        assert!(
            realized
                .instructions
                .iter()
                .all(|inst| inst.duration > RationalTime::ZERO)
        );
        let source = realize_product_hero(
            instance_id,
            &project.production_design.heroes[0].config,
            profile.width,
            profile.height,
        )
        .unwrap();
        assert_eq!(source.len(), 6);
        assert!(source.iter().all(|n| n.name.starts_with("StepSequence / ")));
        assert_eq!(
            source.iter().map(|n| n.id).collect::<Vec<_>>(),
            project.production_design.heroes[0]
                .baseline
                .iter()
                .map(|n| n.id)
                .collect::<Vec<_>>()
        );
        assert_eq!(
            source
                .iter()
                .find(|n| n.name.ends_with("disclosure"))
                .unwrap()
                .text
                .as_deref(),
            Some("STEP SEQUENCE / EDITORIAL STUDY · NOT AN OBSERVED WORKFLOW")
        );
    }
}

#[test]
fn reduced_motion_is_still_true_native_static_content() {
    let (project, options, _) = fixture(false);
    for profile in &project.deliverables {
        let media = &build_motion_canvas_segments(&project, profile.id, &options).unwrap()[0].film;
        assert!(media.sequences[0].beats[0].shots[0].motion.is_empty());
        assert!(realize(media).unwrap().instructions.is_empty());
    }
}

#[test]
fn authored_native_coordinate_override_is_not_silently_reflowed() {
    let (mut project, options, instance_id) = fixture(true);
    let body_id = hero_node_id(instance_id, "body");
    project.scenes[0]
        .nodes
        .iter_mut()
        .find(|node| node.id == body_id)
        .unwrap()
        .x += 19.0;
    let unchanged = project.clone();
    assert!(build_motion_canvas_segments(&project, project.deliverables[0].id, &options).is_ok());
    let error =
        build_motion_canvas_segments(&project, project.deliverables[1].id, &options).unwrap_err();
    assert!(error.message.contains("override conflict"));
    assert_eq!(unchanged, project);
}

#[test]
fn unsupported_animation_drift_fails_before_external_provider_effect() {
    let (mut project, options, _) = fixture(true);
    let node = project.scenes[0]
        .nodes
        .iter_mut()
        .find(|n| n.name.ends_with("headline"))
        .unwrap();
    node.keyframes.last_mut().unwrap().interpolation = MotionInterpolation::EaseInOut;
    let error =
        build_motion_canvas_segments(&project, project.deliverables[0].id, &options).unwrap_err();
    assert!(error.message.contains("motion keyframes"));
}
