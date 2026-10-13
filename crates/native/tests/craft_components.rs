//! Canonical Semwright Film projection of new original craft families.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, MotionEasing, NarrativeRole, SubjectContent, realize};
use uuid::Uuid;

fn project(layout: HeroLayout, motion: bool) -> (Project, Uuid) {
    let mut p = Project::new("Original craft native Film").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Creative comparison".into(),
        objective: "Original illustrative text, not verified real outcomes".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let id = Uuid::parse_str("00000000-0000-4000-8000-000000000005").unwrap();
    p.apply_change(&Change::UpsertProductHero {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: HeroConfig {
            layout,
            eyebrow: "EDITORIAL / ORIGINAL STUDY".into(),
            headline: "A deliberate original idea".into(),
            body: "An independently authored supporting idea with clear intent".into(),
            wordmark: "MW".into(),
            foreground: "#F2F4F3".into(),
            accent: "#86C8D1".into(),
            motion,
        },
    })
    .unwrap();
    (p, id)
}
fn options(p: &Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: p.scenes[0].id,
            role: NarrativeRole::Reveal,
            archetype: Archetype::ObjectSpotlight,
        }],
    }
}
#[test]
fn first_party_craft_layouts_project_to_real_editable_native_text_and_full_sequence() {
    for family in [HeroLayout::ContrastComparison, HeroLayout::EditorialQuote] {
        let (p, id) = project(family, true);
        for deliverable in &p.deliverables {
            let segments = build_motion_canvas_segments(&p, deliverable.id, &options(&p)).unwrap();
            assert_eq!(segments.len(), 1);
            let native = &segments[0];
            assert_eq!(native.frame_count, 180);
            assert_eq!(native.film.output.width, deliverable.width);
            assert_eq!(native.film.output.height, deliverable.height);
            let shot = &native.film.sequences[0].beats[0].shots[0];
            assert_eq!(shot.layers.len(), 6);
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
            let disclosure = hero_node_id(id, "disclosure");
            let parent = format!("node-{}", disclosure.simple());
            let text = shot
                .subjects
                .iter()
                .find(|s| s.id == format!("{parent}-line-0"))
                .unwrap();
            match &text.content {
                SubjectContent::Text { runs, .. } => {
                    assert!(
                        runs[0]
                            .text
                            .contains(if family == HeroLayout::EditorialQuote {
                                "ILLUSTRATIVE QUOTE"
                            } else {
                                "CONCEPTUAL COMPARISON"
                            })
                    );
                }
                _ => panic!("Craft safety disclosure must be actual editable Film text"),
            }
            assert!(!shot.motion.is_empty());
            assert!(
                shot.motion
                    .iter()
                    .all(|m| m.easing == MotionEasing::OutCubic)
            );
            assert!(realize(&native.film).is_ok());
        }
    }
}
#[test]
fn native_craft_static_realizations_do_not_fabricate_animation() {
    for family in [HeroLayout::ContrastComparison, HeroLayout::EditorialQuote] {
        let (p, _) = project(family, false);
        let film = build_motion_canvas_segments(&p, p.deliverables[0].id, &options(&p)).unwrap();
        let shot = &film[0].film.sequences[0].beats[0].shots[0];
        assert!(shot.motion.is_empty());
        assert!(realize(&film[0].film).unwrap().instructions.is_empty());
    }
}
