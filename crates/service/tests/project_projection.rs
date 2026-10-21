use motionwright_domain::{
    CameraState, CanvasNode, CoordinateSpace, NodeStyle, Project, RationalTime, RendererKind,
    Scene, SceneStatus,
};
use motionwright_service::StudioService;
use motionwright_storage::Store;
use std::collections::BTreeSet;
use tempfile::tempdir;
use uuid::Uuid;

fn fixture(scene_count: usize, nodes_per_scene: usize) -> Project {
    let mut project = Project::new("Persisted projection fixture").unwrap();
    for index in 0..scene_count {
        let nodes = (0..nodes_per_scene)
            .map(|node_index| CanvasNode {
                id: Uuid::now_v7(),
                name: format!("Object {index}/{node_index}"),
                kind: "text".into(),
                parent_id: None,
                x: 10.0,
                y: 20.0,
                width: 100.0,
                height: 40.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: Some(format!("Authored object {index}/{node_index}")),
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 0,
                style: NodeStyle::default(),
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            })
            .collect();
        project.scenes.push(Scene {
            id: Uuid::now_v7(),
            name: format!("Scene {index}"),
            objective: format!("Inspect persisted objects from scene {index}"),
            start: RationalTime::new(i64::try_from(index).unwrap() * 72, 5).unwrap(),
            duration: RationalTime::new(72, 5).unwrap(),
            renderer: RendererKind::MotionCanvas,
            status: SceneStatus::Draft,
            beats: vec![],
            nodes,
            camera: CameraState::default(),
        });
    }
    project.validate().unwrap();
    project
}

#[test]
fn existing_full_loader_round_trips_all_500_scenes_and_15000_objects() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let project = fixture(500, 30);
    {
        let mut store = Store::open(&path).unwrap();
        store.create_project(&project).unwrap();
    }
    let service = StudioService::open(&path).unwrap();
    let loaded = service.project(project.id).unwrap();
    assert_eq!(loaded, project);
    assert_eq!(loaded.scenes.len(), 500);
    assert_eq!(
        loaded
            .scenes
            .iter()
            .map(|scene| scene.nodes.len())
            .sum::<usize>(),
        15_000
    );
    let last = loaded.scenes.last().unwrap();
    assert_eq!(
        last.start.checked_add(last.duration).unwrap(),
        RationalTime::new(7200, 1).unwrap()
    );
    let full_bytes = serde_json::to_vec(&loaded).unwrap().len();
    assert!(full_bytes > 1_048_576);
    println!(
        "projection-baseline: scenes=500 objects=15000 duration_seconds=7200 full_document_bytes={full_bytes}"
    );
}
