use motionwright_domain::{Change, RendererKind};
use motionwright_service::StudioService;

#[test]
fn manim_renderer_review_is_revision_bound_and_survives_sqlite_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let database = temp.path().join("manim-review.sqlite3");
    let service = StudioService::open(&database).unwrap();
    let initial = service.create_project("Editable renderer review").unwrap();
    let scene = service
        .apply(
            initial.id,
            &initial.stamp(),
            "new-scene",
            &Change::AddScene {
                name: "Editable timing".into(),
                objective: "Review a renderer change with source timing intact".into(),
                duration_seconds: 2,
            },
        )
        .unwrap()
        .project;
    let scene_id = scene.scenes[0].id;
    let mut camera = scene.scenes[0].camera.clone();
    camera.center_x = 800.0;
    let authored = service
        .apply(
            scene.id,
            &scene.stamp(),
            "frame-camera",
            &Change::SetCamera { scene_id, camera },
        )
        .unwrap()
        .project;
    let unreviewed = Change::SetSceneRenderer {
        scene_id,
        renderer: RendererKind::ManimCommunity,
    };
    let rejected = service
        .apply(authored.id, &authored.stamp(), "unreviewed", &unreviewed)
        .unwrap_err();
    assert!(
        rejected
            .to_string()
            .contains("review the scene renderer fidelity")
    );
    assert_eq!(service.project(authored.id).unwrap(), authored);

    let reviewed = Change::SetReviewedSceneRenderer {
        scene_id,
        renderer: RendererKind::ManimCommunity,
        previous_renderer: RendererKind::MotionCanvas,
        expected: authored.stamp(),
    };
    let moved = service
        .apply(
            authored.id,
            &authored.stamp(),
            "intervening-change",
            &Change::RenameProject {
                title: "A later review".into(),
            },
        )
        .unwrap()
        .project;
    let stale = service
        .apply(moved.id, &moved.stamp(), "stale-review", &reviewed)
        .unwrap_err();
    assert!(
        stale
            .to_string()
            .contains("renderer fidelity review is stale")
    );
    assert_eq!(service.project(moved.id).unwrap(), moved);

    let reviewed = Change::SetReviewedSceneRenderer {
        scene_id,
        renderer: RendererKind::ManimCommunity,
        previous_renderer: RendererKind::MotionCanvas,
        expected: moved.stamp(),
    };
    let saved = service
        .apply(moved.id, &moved.stamp(), "reviewed", &reviewed)
        .unwrap()
        .project;
    let mut expected_scene = moved.scenes[0].clone();
    expected_scene.renderer = RendererKind::ManimCommunity;
    assert_eq!(saved.scenes, vec![expected_scene]);
    assert_eq!(saved.revision, moved.revision + 1);

    drop(service);
    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(saved.id).unwrap(), saved);
    let events = reopened.history(saved.id, moved.revision, 4).unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].revision, saved.revision);
    assert_eq!(events[0].change, reviewed);
    let replay = reopened
        .apply(moved.id, &moved.stamp(), "reviewed", &reviewed)
        .unwrap();
    assert!(replay.replayed);
    assert_eq!(replay.project, saved);
    let reverse = Change::SetSceneRenderer {
        scene_id,
        renderer: RendererKind::MotionCanvas,
    };
    assert!(
        reopened
            .apply(saved.id, &saved.stamp(), "unreviewed-back", &reverse)
            .is_err()
    );
    assert_eq!(reopened.project(saved.id).unwrap(), saved);
}
