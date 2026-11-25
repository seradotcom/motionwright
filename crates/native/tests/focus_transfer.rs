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
