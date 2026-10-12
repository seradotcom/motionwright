use motionwright_domain::{
    CanvasNode, Change, CoordinateSpace, MotionInterpolation, MotionProperty, NodeStyle,
    RationalTime, RevisionStamp,
};
use motionwright_service::StudioService;
use std::collections::BTreeSet;
use uuid::Uuid;

#[test]
fn exact_cubic_out_motion_is_one_cas_revision_and_survives_sqlite_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let file = directory.path().join("creative-source.sqlite3");
    let service = StudioService::open(&file).unwrap();
    let mut project = service
        .create_project("Source-bound native cubic easing")
        .unwrap();
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "create-frame-locked-scene",
            &Change::AddScene {
                name: "Camera and semantic subject".into(),
                objective: "Exact native easing, not rasterized browser movement".into(),
                duration_seconds: 3,
            },
        )
        .unwrap()
        .project;
    let scene_id = project.scenes[0].id;
    let node_id = Uuid::now_v7();
    let node = CanvasNode {
        id: node_id,
        name: "Original semantic object".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 480.0,
        y: 340.0,
        width: 180.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    };
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "admit-original-rectangle",
            &Change::AddCanvasNode { scene_id, node },
        )
        .unwrap()
        .project;
    let before = project.clone();
    let profile_id = project.deliverables[0].id;
    let command = Change::SetCanvasOutCubicPositionMotion {
        scene_id,
        node_id,
        deliverable_id: profile_id,
        start_x: 340.0,
        start_y: 310.0,
        end_frame: 30,
    };
    let updated = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-cubic-one-revision",
            &command,
        )
        .unwrap()
        .project;
    assert_eq!(updated.revision, before.revision + 1);
    assert_eq!(updated.scenes[0].nodes[0].id, node_id);
    assert_eq!(updated.scenes[0].nodes[0].x, 480.0);
    assert_eq!(updated.scenes[0].nodes[0].y, 340.0);
    let keys = &updated.scenes[0].nodes[0].keyframes;
    assert_eq!(keys.len(), 4);
    assert_eq!(keys[0].at, RationalTime::ZERO);
    assert_eq!(keys[1].interpolation, MotionInterpolation::Linear);
    assert_eq!(keys[2].at, RationalTime::new(1, 1).unwrap());
    assert_eq!(keys[2].property, MotionProperty::X);
    assert_eq!(keys[3].property, MotionProperty::Y);
    assert_eq!(keys[2].interpolation, MotionInterpolation::EaseOutCubic);
    assert_eq!(keys[3].interpolation, MotionInterpolation::EaseOutCubic);
    assert!(
        service
            .apply(
                project.id,
                &RevisionStamp::from(&before),
                "native-cubic-stale-snapshot",
                &command
            )
            .is_err(),
        "stale source revision must never replace the authored curve"
    );
    assert!(
        service
            .apply(
                project.id,
                &RevisionStamp::from(&updated),
                "native-cubic-no-overwrite",
                &command
            )
            .is_err(),
        "a replay under a new request cannot overwrite existing motion"
    );
    assert_eq!(service.project(project.id).unwrap(), updated);
    drop(service);
    let reopened = StudioService::open(&file)
        .unwrap()
        .project(project.id)
        .unwrap();
    assert_eq!(reopened, updated);
    assert_eq!(reopened.scenes[0].nodes[0].keyframes, *keys);
    reopened.validate().unwrap();
}
