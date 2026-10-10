//! Closed native Film acceptance for SplitExplanation.
//! Real producer evidence still belongs to the pinned Broker/Driver Host.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole, realize};
use uuid::Uuid;

fn fixture(motion: bool) -> (Project, FilmBuildOptions, Uuid) {
    let mut project = Project::new("Split explanation native scene").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Two-part explanation".into(),
            objective: "Convey two original pieces of authored information".into(),
            duration_seconds: 6,
        })
        .unwrap();
    let id = Uuid::new_v4();
    project
        .apply_change(&Change::UpsertProductHero {
            instance_id: id,
            scene_id: project.scenes[0].id,
            config: HeroConfig {
                layout: HeroLayout::SplitExplanation,
                headline: "Where the problem starts".into(),
                body: "Explain what is changing and why it matters to the viewer.".into(),
                wordmark: "WHY".into(),
                motion,
                ..HeroConfig::default()
            },
        })
        .unwrap();
    let options = FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: project.scenes[0].id,
            role: NarrativeRole::Reveal,
            archetype: Archetype::ObjectSpotlight,
        }],
    };
    (project, options, id)
}

#[test]
fn semantic_split_realizes_with_original_object_ids_in_all_three_aspects() {
    let (project, options, id) = fixture(true);
    for profile in &project.deliverables {
        let segments = build_motion_canvas_segments(&project, profile.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        assert_eq!(segments[0].frame_count, 180);
        let film = &segments[0].film;
        assert_eq!(
            (film.output.width, film.output.height),
            (profile.width, profile.height)
        );
        let shot = &film.sequences[0].beats[0].shots[0];
        assert_eq!(
            shot.subjects
                .iter()
                .filter(|subject| subject.parent.is_none())
                .count(),
            6
        );
        // Five semantic text subjects are real Motion Canvas components with
        // deterministic native descendants, not flattening a screen bitmap.
        assert_eq!(
            shot.subjects
                .iter()
                .filter(|subject| subject.role == "component-text-container")
                .count(),
            5
        );
        let baseline = realize_product_hero(
            id,
            &project.production_design.heroes[0].config,
            profile.width,
            profile.height,
        )
        .unwrap();
        assert!(
            baseline
                .iter()
                .all(|node| node.name.starts_with("SplitExplanation / "))
        );
        assert_eq!(
            baseline.iter().map(|node| node.id).collect::<Vec<_>>(),
            project.production_design.heroes[0]
                .baseline
                .iter()
                .map(|node| node.id)
                .collect::<Vec<_>>()
        );
        assert!(
            shot.subjects
                .iter()
                .filter(|subject| subject.parent.is_none())
                .all(|subject| !subject.initially_visible)
        );
        let actual = realize(film).unwrap();
        assert!(!actual.instructions.is_empty());
        assert!(
            actual
                .instructions
                .iter()
                .all(|item| item.duration > RationalTime::ZERO)
        );
    }
}

#[test]
fn static_split_has_no_synthetic_motion_or_extra_native_instructions() {
    let (project, options, _) = fixture(false);
    for profile in &project.deliverables {
        let film = &build_motion_canvas_segments(&project, profile.id, &options).unwrap()[0].film;
        assert!(film.sequences[0].beats[0].shots[0].motion.is_empty());
        assert!(realize(film).unwrap().instructions.is_empty());
    }
}

#[test]
fn authored_change_of_family_preserves_identity_and_rejects_unsafe_reflow() {
    let (mut project, options, id) = fixture(true);
    let body = hero_node_id(id, "body");
    project.scenes[0]
        .nodes
        .iter_mut()
        .find(|node| node.id == body)
        .unwrap()
        .x += 16.0;
    let before = project.clone();
    // The edited coordinates are valid for the original cut but cannot be
    // silently translated into the other aspect family.
    assert!(build_motion_canvas_segments(&project, project.deliverables[0].id, &options).is_ok());
    assert!(build_motion_canvas_segments(&project, project.deliverables[1].id, &options).is_err());
    assert_eq!(before, project);
}
