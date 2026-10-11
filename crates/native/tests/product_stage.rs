//! A source-owned original ProductStage projects to editable canonical Film,
//! including real fixed native glyph subjects. This is not screenshot/3D proof.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole, SubjectContent, realize};
use uuid::Uuid;

fn project(frame: StageFrame, animated: bool) -> (Project, Uuid) {
    let mut p = Project::new("Owned generic UI / not a real capture").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Original stage".into(),
        objective: "Screen anchor native test".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let id = Uuid::parse_str("00000000-0000-4000-8000-000000000005").unwrap();
    p.apply_change(&Change::UpsertProductStage {
        instance_id: id,
        scene_id: p.scenes[0].id,
        config: ProductStageConfig {
            frame,
            animate_focus: animated,
            author_attestation: "Test author declares these generic graphics original".into(),
            ..Default::default()
        },
    })
    .unwrap();
    (p, id)
}
fn native_options(p: &Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: p.scenes[0].id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    }
}
#[test]
fn stage_native_film_supports_all_three_generic_forms_and_real_fixed_glyph_subjects() {
    for frame in [
        StageFrame::Browser,
        StageFrame::FlatPanel,
        StageFrame::GenericHandset,
    ] {
        let (p, id) = project(frame, false);
        let segment =
            build_motion_canvas_segments(&p, p.deliverables[0].id, &native_options(&p)).unwrap();
        let native = &segment[0];
        assert_eq!(native.frame_count, 180);
        assert_eq!(native.film.sequences.len(), 1);
        let shot = &native.film.sequences[0].beats[0].shots[0];
        assert_eq!(shot.layers.len(), STAGE_ROLES.len());
        assert_eq!(shot.subjects.len(), STAGE_ROLES.len() + 6); // 6 text roles -> fixed group + line
        let disclosure = stage_node_id(id, "disclosure");
        let subject_prefix = format!("node-{}", disclosure.simple());
        assert!(
            shot.subjects
                .iter()
                .any(|s| s.id == subject_prefix && s.role == "component-text-container")
        );
        let disclosure_line = shot
            .subjects
            .iter()
            .find(|s| s.id == format!("{subject_prefix}-line-0"))
            .unwrap();
        match &disclosure_line.content {
            SubjectContent::Text { runs, .. } => {
                assert_eq!(runs.len(), 1);
                assert_eq!(runs[0].text, PRODUCT_STAGE_DISCLOSURE);
            }
            _ => panic!("Source disclosure did not generate real native editable text"),
        }
        assert!(shot.subjects.iter().any(|s| s.id
            == "node-".to_owned() + &stage_node_id(id, "screen").simple().to_string()
            && s.role == "rectangle"));
        assert!(realize(&native.film).is_ok());
    }
}
#[test]
fn stage_native_focus_animation_keeps_exact_editable_bounds_and_frame_timing() {
    let (p, id) = project(StageFrame::Browser, true);
    let projected =
        build_motion_canvas_segments(&p, p.deliverables[0].id, &native_options(&p)).unwrap();
    let shot = &projected[0].film.sequences[0].beats[0].shots[0];
    assert_eq!(shot.layers.len(), STAGE_ROLES.len());
    assert!(shot.motion.iter().any(|motion| {
        format!("{:?}", motion.primitive)
            .contains(&stage_node_id(id, "focus_marker").simple().to_string())
    }));
    assert_eq!(projected[0].frame_count, 180);
    assert!(realize(&projected[0].film).is_ok());
}
