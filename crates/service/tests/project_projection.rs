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

#[test]
fn large_project_opens_in_bounded_pages_and_matches_persisted_content() {
    use motionwright_service::{MAX_PROJECTION_RESPONSE_BYTES, MAX_SCENE_PAGE_ITEMS};
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let project = fixture(500, 30);
    {
        let mut store = Store::open(&path).unwrap();
        store.create_project(&project).unwrap();
    }
    // The paginated open starts on a fresh connection without loading Project.
    let service = StudioService::open(&path).unwrap();
    let head = service.project_projection_head(project.id).unwrap();
    assert_eq!(head.scene_count, 500);
    assert_eq!(head.identity.revision, "0");
    assert_eq!(head.identity.generation, project.generation);
    let mut cursor = None;
    let mut seen = 0_usize;
    let mut page_count = 0;
    loop {
        let page = service
            .project_scene_page(&head.identity, cursor.as_ref(), MAX_SCENE_PAGE_ITEMS)
            .unwrap();
        assert_eq!(page.start_ordinal as usize, seen);
        assert_eq!(page.total_scenes, 500);
        assert!(page.items.len() <= usize::from(MAX_SCENE_PAGE_ITEMS));
        assert!(serde_json::to_vec(&page).unwrap().len() <= MAX_PROJECTION_RESPONSE_BYTES);
        for summary in &page.items {
            let expected = &project.scenes[seen];
            assert_eq!(summary.ordinal as usize, seen);
            assert_eq!(summary.id, expected.id);
            assert_eq!(summary.name, expected.name);
            assert_eq!(summary.start, expected.start);
            assert_eq!(summary.duration, expected.duration);
            assert_eq!(summary.renderer, expected.renderer);
            assert_eq!(summary.status, expected.status);
            assert_eq!(summary.beat_count as usize, expected.beats.len());
            assert_eq!(summary.node_count as usize, expected.nodes.len());
            if [0, 249, 499].contains(&seen) {
                let nodes = service
                    .project_scene_nodes(&head.identity, &summary.scene_ref(), None, 128)
                    .unwrap();
                assert_eq!(nodes.items, expected.nodes);
                assert_eq!(nodes.total_nodes, 30);
                assert!(nodes.next_cursor.is_none());
                assert!(serde_json::to_vec(&nodes).unwrap().len() <= MAX_PROJECTION_RESPONSE_BYTES);
            }
            seen += 1;
        }
        page_count += 1;
        cursor = page.next_cursor;
        if cursor.is_none() {
            break;
        }
    }
    assert_eq!(seen, 500);
    assert_eq!(page_count, 8);
    println!(
        "projection-large: scenes=500 summary_pages=8 selected_scene_node_pages=3 selected_nodes=90"
    );
}

fn logical_rows(
    path: &std::path::Path,
) -> std::collections::BTreeMap<String, Vec<Vec<rusqlite::types::Value>>> {
    let connection =
        rusqlite::Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .unwrap();
    let names: Vec<String> = connection.prepare(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).unwrap().query_map([], |row| row.get(0)).unwrap().map(Result::unwrap).collect();
    let mut snapshot = std::collections::BTreeMap::new();
    for name in names {
        let sql = format!(
            "SELECT * FROM \"{}\" ORDER BY rowid",
            name.replace('"', "\"\"")
        );
        let mut statement = connection.prepare(&sql).unwrap();
        let count = statement.column_count();
        let mut rows = statement.query([]).unwrap();
        let mut values = vec![];
        while let Some(row) = rows.next().unwrap() {
            values.push((0..count).map(|index| row.get(index).unwrap()).collect());
        }
        snapshot.insert(name, values);
    }
    snapshot
}

#[test]
fn node_pages_preserve_cross_page_parents_and_all_logical_storage_rows() {
    use motionwright_service::{MAX_NODE_PAGE_ITEMS, SceneRef};
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let mut project = fixture(1, 300);
    project.scenes[0].nodes[0].parent_id = Some(project.scenes[0].nodes[299].id);
    project.validate().unwrap();
    {
        let mut store = Store::open(&path).unwrap();
        store.create_project(&project).unwrap();
    }
    let source = directory.path().join("immutable-source.bin");
    std::fs::write(&source, b"Original immutable blob for projection readback").unwrap();
    let blob_path = {
        let store = Store::open(&path).unwrap();
        let descriptor = store.ingest_blob_file(&source).unwrap();
        store.verified_blob_path(&descriptor.sha256).unwrap()
    };
    let original_blob = std::fs::read(&blob_path).unwrap();
    let before = logical_rows(&path);
    let service = StudioService::open(&path).unwrap();
    let head = service.project_projection_head(project.id).unwrap();
    let scene = SceneRef {
        ordinal: 0,
        id: project.scenes[0].id,
    };
    let mut cursor = None;
    let mut nodes = vec![];
    let mut page_count = 0;
    loop {
        let page = service
            .project_scene_nodes(&head.identity, &scene, cursor.as_ref(), MAX_NODE_PAGE_ITEMS)
            .unwrap();
        assert_eq!(page.start_ordinal as usize, nodes.len());
        assert_eq!(page.total_nodes, 300);
        nodes.extend(page.items);
        page_count += 1;
        cursor = page.next_cursor;
        if cursor.is_none() {
            break;
        }
    }
    assert_eq!(page_count, 3);
    assert_eq!(nodes, project.scenes[0].nodes);
    assert_eq!(service.project(project.id).unwrap(), project);
    drop(service);
    assert_eq!(logical_rows(&path), before);
    assert_eq!(std::fs::read(&blob_path).unwrap(), original_blob);
    let reopened = StudioService::open(&path).unwrap();
    let head_again = reopened.project_projection_head(project.id).unwrap();
    assert_eq!(head_again.identity, head.identity);
    assert_eq!(
        reopened
            .project_scene_nodes(&head_again.identity, &scene, None, 1)
            .unwrap()
            .items[0],
        project.scenes[0].nodes[0]
    );
}

#[test]
fn pages_reject_stale_and_mis_scoped_cursors_after_real_peer_commit() {
    use motionwright_domain::{Change, RevisionStamp};
    use motionwright_service::{NodePageCursor, ProjectionError, SceneRef};
    use motionwright_storage::StorageError;
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let project = fixture(2, 2);
    let mut writer = Store::open(&path).unwrap();
    writer.create_project(&project).unwrap();
    let service = StudioService::open(&path).unwrap();
    let head = service.project_projection_head(project.id).unwrap();
    let page = service.project_scene_page(&head.identity, None, 1).unwrap();
    let cursor = page.next_cursor.unwrap();
    let scene = SceneRef {
        ordinal: 0,
        id: project.scenes[0].id,
    };
    let node_cursor = NodePageCursor {
        identity: head.identity.clone(),
        scene: scene.clone(),
        next_ordinal: 1,
    };
    let wrong_scene = SceneRef {
        ordinal: 1,
        id: project.scenes[1].id,
    };
    assert!(
        service
            .project_scene_nodes(&head.identity, &wrong_scene, Some(&node_cursor), 1)
            .is_err()
    );
    let mut wrong_project_cursor = cursor.clone();
    wrong_project_cursor.identity.project_id = Uuid::now_v7();
    assert!(
        service
            .project_scene_page(&head.identity, Some(&wrong_project_cursor), 1)
            .is_err()
    );
    let mut wrong_generation = head.identity.clone();
    wrong_generation.generation = Uuid::now_v7();
    assert!(matches!(
        service.project_scene_page(&wrong_generation, None, 1),
        Err(ProjectionError::Storage(StorageError::GenerationConflict))
    ));
    writer
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "projection-peer-edit",
            &Change::RenameProject {
                title: "Changed by peer".into(),
            },
        )
        .unwrap();
    assert!(matches!(
        service.project_scene_page(&head.identity, Some(&cursor), 1),
        Err(ProjectionError::Storage(StorageError::Conflict {
            expected: 0,
            actual: 1
        }))
    ));
    assert!(matches!(
        service.project_scene_nodes(&head.identity, &scene, Some(&node_cursor), 1),
        Err(ProjectionError::Storage(StorageError::Conflict {
            expected: 0,
            actual: 1
        }))
    ));
    let fresh = service.project_projection_head(project.id).unwrap();
    assert_eq!(fresh.title, "Changed by peer");
    assert_eq!(fresh.identity.revision, "1");
    assert_eq!(
        service
            .project_scene_page(&fresh.identity, None, 1)
            .unwrap()
            .items[0]
            .id,
        scene.id
    );
    assert_eq!(
        service.project(project.id).unwrap().title,
        "Changed by peer"
    );
}

#[test]
fn empty_and_terminal_pages_are_distinct_from_invalid_requests() {
    use motionwright_service::{NodePageCursor, ScenePageCursor, SceneRef};
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let empty = fixture(0, 0);
    let one = fixture(1, 0);
    let mut store = Store::open(&path).unwrap();
    store.create_project(&empty).unwrap();
    store.create_project(&one).unwrap();
    let service = StudioService::from_store(store);
    let empty_head = service.project_projection_head(empty.id).unwrap();
    let empty_page = service
        .project_scene_page(&empty_head.identity, None, 64)
        .unwrap();
    assert!(empty_page.items.is_empty() && empty_page.next_cursor.is_none());
    let head = service.project_projection_head(one.id).unwrap();
    let scene = SceneRef {
        ordinal: 0,
        id: one.scenes[0].id,
    };
    let empty_nodes = service
        .project_scene_nodes(&head.identity, &scene, None, 128)
        .unwrap();
    assert!(empty_nodes.items.is_empty() && empty_nodes.next_cursor.is_none());
    for limit in [0, 65, u16::MAX] {
        assert!(
            service
                .project_scene_page(&head.identity, None, limit)
                .is_err()
        );
    }
    for limit in [0, 129, u16::MAX] {
        assert!(
            service
                .project_scene_nodes(&head.identity, &scene, None, limit)
                .is_err()
        );
    }
    let terminal = ScenePageCursor {
        identity: head.identity.clone(),
        next_ordinal: 1,
    };
    assert!(
        service
            .project_scene_page(&head.identity, Some(&terminal), 1)
            .unwrap()
            .items
            .is_empty()
    );
    let outside = ScenePageCursor {
        identity: head.identity.clone(),
        next_ordinal: u32::MAX,
    };
    assert!(
        service
            .project_scene_page(&head.identity, Some(&outside), 1)
            .is_err()
    );
    let outside_nodes = NodePageCursor {
        identity: head.identity.clone(),
        scene: scene.clone(),
        next_ordinal: u32::MAX,
    };
    assert!(
        service
            .project_scene_nodes(&head.identity, &scene, Some(&outside_nodes), 1)
            .is_err()
    );
    let wrong_id = SceneRef {
        ordinal: 0,
        id: Uuid::now_v7(),
    };
    assert!(
        service
            .project_scene_nodes(&head.identity, &wrong_id, None, 1)
            .is_err()
    );
}

#[test]
fn oversized_pages_fail_without_truncating_valid_unicode_nodes() {
    use motionwright_service::{ProjectionError, SceneRef};
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let mut project = fixture(1, 20);
    for node in &mut project.scenes[0].nodes {
        node.text = Some("é".repeat(48_000));
    }
    project.validate().unwrap();
    {
        let mut store = Store::open(&path).unwrap();
        store.create_project(&project).unwrap();
    }
    let before = logical_rows(&path);
    let service = StudioService::open(&path).unwrap();
    let head = service.project_projection_head(project.id).unwrap();
    let scene = SceneRef {
        ordinal: 0,
        id: project.scenes[0].id,
    };
    assert!(matches!(
        service.project_scene_nodes(&head.identity, &scene, None, 20),
        Err(ProjectionError::TooLarge { .. })
    ));
    let smaller = service
        .project_scene_nodes(&head.identity, &scene, None, 1)
        .unwrap();
    assert_eq!(smaller.items, project.scenes[0].nodes[..1]);
    assert_eq!(smaller.next_cursor.unwrap().next_ordinal, 1);
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(logical_rows(&path), before);
}

#[test]
fn response_budget_includes_restored_defaults_and_wire_envelope() {
    use motionwright_service::{MAX_PROJECTION_RESPONSE_BYTES, ProjectionError, SceneRef};
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let mut project = fixture(1, 128);
    for node in &mut project.scenes[0].nodes {
        node.text = Some(String::new());
    }
    let mut document = serde_json::to_value(&project).unwrap();
    let nodes = document["scenes"][0]["nodes"].as_array_mut().unwrap();
    for node in nodes.iter_mut() {
        let object = node.as_object_mut().unwrap();
        for key in [
            "coordinate_space",
            "z_index",
            "style",
            "relations",
            "property_locks",
            "keyframes",
        ] {
            object.remove(key);
        }
    }
    let base_bytes: usize = nodes
        .iter()
        .map(|node| serde_json::to_vec(node).unwrap().len())
        .sum();
    let text_length = (MAX_PROJECTION_RESPONSE_BYTES - base_bytes - 512) / nodes.len();
    for node in nodes.iter_mut() {
        node["text"] = serde_json::json!("x".repeat(text_length));
    }
    let input_bytes: usize = nodes
        .iter()
        .map(|node| serde_json::to_vec(node).unwrap().len())
        .sum();
    assert!(input_bytes < MAX_PROJECTION_RESPONSE_BYTES);
    let expected: Project = serde_json::from_value(document.clone()).unwrap();
    expected.validate().unwrap();
    assert!(
        serde_json::to_vec(&expected.scenes[0].nodes).unwrap().len()
            > MAX_PROJECTION_RESPONSE_BYTES
    );
    {
        let mut store = Store::open(&path).unwrap();
        store.create_project(&expected).unwrap();
    }
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute(
            "UPDATE projects SET document_json=?2 WHERE id=?1",
            rusqlite::params![
                project.id.to_string(),
                serde_json::to_string(&document).unwrap()
            ],
        )
        .unwrap();
    drop(connection);
    let service = StudioService::open(&path).unwrap();
    assert_eq!(service.project(project.id).unwrap(), expected);
    let head = service.project_projection_head(project.id).unwrap();
    let scene = SceneRef {
        ordinal: 0,
        id: project.scenes[0].id,
    };
    assert!(matches!(
        service.project_scene_nodes(&head.identity, &scene, None, 128),
        Err(ProjectionError::TooLarge { .. })
    ));
    let page = service
        .project_scene_nodes(&head.identity, &scene, None, 1)
        .unwrap();
    assert_eq!(page.items[0], expected.scenes[0].nodes[0]);
}

#[test]
fn utf16_database_preserves_utf8_page_budgets_and_escaped_text() {
    use motionwright_service::{MAX_PROJECTION_RESPONSE_BYTES, ProjectionError, SceneRef};
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    {
        let connection = rusqlite::Connection::open(&path).unwrap();
        connection
            .execute_batch("PRAGMA encoding='UTF-16le'; VACUUM;")
            .unwrap();
    }
    let mut project = fixture(1, 2);
    for node in &mut project.scenes[0].nodes {
        node.text = Some("\u{0001}".repeat(100_000));
    }
    project.validate().unwrap();
    {
        let mut store = Store::open(&path).unwrap();
        store.create_project(&project).unwrap();
    }
    let connection = rusqlite::Connection::open(&path).unwrap();
    let encoding: String = connection
        .query_row("PRAGMA encoding", [], |row| row.get(0))
        .unwrap();
    assert_eq!(encoding.to_lowercase(), "utf-16le");
    let database_fragment_bytes: i64 = connection.query_row(
        "SELECT length(CAST(json_extract(document_json,'$.scenes[0].nodes[0]') AS BLOB)) FROM projects WHERE id=?1",
        [project.id.to_string()], |row| row.get(0)
    ).unwrap();
    assert!(usize::try_from(database_fragment_bytes).unwrap() > MAX_PROJECTION_RESPONSE_BYTES);
    assert!(
        serde_json::to_vec(&project.scenes[0].nodes[0])
            .unwrap()
            .len()
            < MAX_PROJECTION_RESPONSE_BYTES
    );
    drop(connection);
    let service = StudioService::open(&path).unwrap();
    assert_eq!(service.project(project.id).unwrap(), project);
    let head = service.project_projection_head(project.id).unwrap();
    let scene = SceneRef {
        ordinal: 0,
        id: project.scenes[0].id,
    };
    let first = service
        .project_scene_nodes(&head.identity, &scene, None, 1)
        .unwrap();
    assert_eq!(first.items[0], project.scenes[0].nodes[0]);
    assert!(serde_json::to_vec(&first).unwrap().len() < MAX_PROJECTION_RESPONSE_BYTES);
    assert!(matches!(
        service.project_scene_nodes(&head.identity, &scene, None, 2),
        Err(ProjectionError::TooLarge { .. })
    ));
}
