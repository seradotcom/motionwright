//! CR-PROJECT-08: complete project pages remain available without all media.
//! This checks content and persistence, not a latency or installed-UI claim.
use motionwright_domain::{
    CameraState, CanvasNode, CoordinateSpace, NodeStyle, Project, RationalTime, RendererKind,
    Scene, SceneStatus,
};
use motionwright_service::{
    MAX_PROJECTION_RESPONSE_BYTES, MAX_SCENE_PAGE_ITEMS, StudioService, VoiceImportMetadata,
};
use motionwright_storage::Store;
use rusqlite::{Connection, OpenFlags, types::Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    path::{Path, PathBuf},
};
use tempfile::tempdir;
use uuid::Uuid;

const SCENES: usize = 500;
const NODES_PER_SCENE: usize = 30;
const NODE_PAGE_SIZE: u16 = 17;

fn large_project() -> Project {
    let mut project = Project::new("Large project with retained and unavailable media").unwrap();
    for scene_index in 0..SCENES {
        let mut nodes: Vec<CanvasNode> = (0..NODES_PER_SCENE)
            .map(|node_index| CanvasNode {
                id: Uuid::now_v7(),
                name: format!("Object {scene_index}/{node_index}"),
                kind: "text".into(),
                parent_id: None,
                x: 10.0,
                y: 20.0,
                width: 100.0,
                height: 40.0,
                rotation_deg: 0.0,
                opacity: 1.0,
                text: Some(format!("Persisted object {scene_index}/{node_index}")),
                coordinate_space: CoordinateSpace::ProjectPixels,
                z_index: 0,
                style: NodeStyle::default(),
                relations: vec![],
                property_locks: BTreeSet::new(),
                keyframes: vec![],
            })
            .collect();
        // The parent is on the second page; neither page may discard the link.
        nodes[0].parent_id = Some(nodes[NODES_PER_SCENE - 1].id);
        project.scenes.push(Scene {
            id: Uuid::now_v7(),
            name: format!("Scene {scene_index}"),
            objective: format!("Inspect every persisted object in scene {scene_index}"),
            start: RationalTime::new(i64::try_from(scene_index).unwrap() * 72, 5).unwrap(),
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

fn pcm_wav(sample: i16) -> Vec<u8> {
    let sample_rate = 8_000_u32;
    let frames = 80_u32;
    let data_len = frames * 2;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_len).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&sample_rate.to_le_bytes());
    bytes.extend_from_slice(&(sample_rate * 2).to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_len.to_le_bytes());
    for _ in 0..frames {
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    bytes
}

fn logical_rows(path: &Path) -> BTreeMap<String, Vec<Vec<Value>>> {
    let connection = Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    let names: Vec<String> = connection
        .prepare("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    let mut snapshot = BTreeMap::new();
    for name in names {
        let quoted = name.replace('"', "\"\"");
        let columns = connection
            .prepare(&format!("SELECT * FROM \"{quoted}\""))
            .unwrap()
            .column_count();
        let ordering = (1..=columns)
            .map(|index| index.to_string())
            .collect::<Vec<_>>()
            .join(",");
        let mut statement = connection
            .prepare(&format!("SELECT * FROM \"{quoted}\" ORDER BY {ordering}"))
            .unwrap();
        let mut rows = statement.query([]).unwrap();
        let mut values = vec![];
        while let Some(row) = rows.next().unwrap() {
            values.push((0..columns).map(|index| row.get(index).unwrap()).collect());
        }
        snapshot.insert(name, values);
    }
    snapshot
}

fn cas_files(root: &Path) -> BTreeMap<PathBuf, String> {
    fn visit(root: &Path, directory: &Path, result: &mut BTreeMap<PathBuf, String>) {
        for entry in fs::read_dir(directory).unwrap() {
            let entry = entry.unwrap();
            let path = entry.path();
            let kind = entry.file_type().unwrap();
            assert!(!kind.is_symlink());
            if kind.is_dir() {
                visit(root, &path, result);
            } else {
                assert!(kind.is_file());
                result.insert(
                    path.strip_prefix(root).unwrap().to_path_buf(),
                    hex::encode(Sha256::digest(fs::read(path).unwrap())),
                );
            }
        }
    }
    let mut result = BTreeMap::new();
    visit(root, root, &mut result);
    result
}

#[test]
fn all_large_project_pages_reopen_without_selected_imported_blobs() {
    let directory = tempdir().unwrap();
    let database = directory.path().join("project.sqlite3");
    let initial = large_project();
    {
        let mut store = Store::open(&database).unwrap();
        store.create_project(&initial).unwrap();
    }
    let service = StudioService::open(&database).unwrap();
    let other_first = service.create_project("Other project one").unwrap();
    let other_second = service.create_project("Other project two").unwrap();
    let mut expected = initial;
    let mut imports = vec![];
    for index in 0..3 {
        let source = directory.path().join(format!("source-{index}.wav"));
        let bytes = pcm_wav(500 + i16::try_from(index).unwrap());
        fs::write(&source, &bytes).unwrap();
        let outcome = if index == 0 {
            service.import_voice_file(
                expected.id,
                &expected.stamp(),
                "missing-media-measured-voice",
                &source,
                VoiceImportMetadata {
                    name: "Measured voice whose CAS will be unavailable".into(),
                    media_type: "audio/wav".into(),
                    label: "Imported measured voice".into(),
                },
            )
        } else {
            service.import_asset_file(
                expected.id,
                &expected.stamp(),
                &format!("missing-media-asset-{index}"),
                &source,
                format!("Imported audio {index}"),
                "audio/wav".into(),
            )
        }
        .unwrap();
        assert!(!outcome.replayed);
        expected = outcome.project;
        let digest = hex::encode(Sha256::digest(&bytes));
        assert!(
            expected
                .assets
                .iter()
                .any(|asset| asset.content_sha256.as_deref() == Some(digest.as_str()))
        );
        let blob = {
            let shared = service.store();
            let store = shared.lock();
            store.verified_blob_path(&digest).unwrap()
        };
        assert_eq!(fs::read(&blob).unwrap(), bytes);
        imports.push((source, bytes, digest, blob));
    }
    assert_eq!(expected.assets.len(), 3);
    assert_eq!(expected.audio.voice_tracks.len(), 1);
    let blob_root = service.store().lock().blob_root().to_path_buf();
    assert_eq!(cas_files(&blob_root).len(), 3);
    drop(service);

    // Remove only this test's first two admitted media objects and their inputs.
    // The third object is a positive custody control, not required for page reads.
    for (source, _, _, blob) in imports.iter().take(2) {
        fs::remove_file(source).unwrap();
        fs::remove_file(blob).unwrap();
    }
    let assert_missing = || {
        for (source, _, _, blob) in imports.iter().take(2) {
            assert!(!source.exists() && !blob.exists());
        }
    };
    assert_missing();
    let cas_before = cas_files(&blob_root);
    assert_eq!(cas_before.len(), 1);
    let sql_before = logical_rows(&database);
    for table in ["projects", "events", "receipts"] {
        assert!(
            !sql_before[table].is_empty(),
            "non-vacuous SQL custody: {table}"
        );
    }

    let service = StudioService::open(&database).unwrap();
    let expected_library: BTreeMap<_, _> = [&expected, &other_first, &other_second]
        .into_iter()
        .map(|project| {
            (
                project.id,
                (project.generation, project.revision, project.title.clone()),
            )
        })
        .collect();
    let mut library_cursor = None;
    let mut listed = BTreeSet::new();
    let mut library_pages = 0;
    loop {
        let page = service
            .project_summaries(library_cursor.as_ref(), 1)
            .unwrap();
        assert_eq!(page.items.len(), 1);
        assert_eq!(page.truncated, page.next_cursor.is_some());
        for item in page.items {
            assert!(
                listed.insert(item.id),
                "duplicate project across continuation"
            );
            assert_eq!(
                (item.generation, item.revision, item.title),
                expected_library[&item.id]
            );
        }
        library_pages += 1;
        library_cursor = page.next_cursor;
        assert_eq!(
            library_cursor.is_some(),
            listed.len() < expected_library.len()
        );
        if library_cursor.is_none() {
            break;
        }
    }
    assert_eq!(listed, expected_library.keys().copied().collect());
    assert_eq!(library_pages, expected_library.len());
    assert_missing();

    let head = service.project_projection_head(expected.id).unwrap();
    assert_eq!(head.scene_count as usize, SCENES);
    assert_eq!(head.identity.checked_stamp().unwrap(), expected.stamp());
    let mut scene_cursor = None;
    let mut scene_count = 0;
    let mut scene_pages = 0;
    let mut node_count = 0;
    let mut node_pages = 0;
    loop {
        let page = service
            .project_scene_page(&head.identity, scene_cursor.as_ref(), MAX_SCENE_PAGE_ITEMS)
            .unwrap();
        assert_eq!(page.start_ordinal as usize, scene_count);
        assert_eq!(page.total_scenes as usize, SCENES);
        assert!(!page.items.is_empty());
        assert!(page.items.len() <= usize::from(MAX_SCENE_PAGE_ITEMS));
        assert!(serde_json::to_vec(&page).unwrap().len() <= MAX_PROJECTION_RESPONSE_BYTES);
        for summary in page.items {
            let scene = &expected.scenes[scene_count];
            assert_eq!(summary.ordinal as usize, scene_count);
            assert_eq!(summary.id, scene.id);
            assert_eq!(summary.name, scene.name);
            assert_eq!(summary.start, scene.start);
            assert_eq!(summary.duration, scene.duration);
            assert_eq!(summary.renderer, scene.renderer);
            assert_eq!(summary.status, scene.status);
            assert_eq!(summary.beat_count as usize, scene.beats.len());
            assert_eq!(summary.node_count as usize, scene.nodes.len());
            let mut node_cursor = None;
            let mut nodes = vec![];
            loop {
                let nodes_page = service
                    .project_scene_nodes(
                        &head.identity,
                        &summary.scene_ref(),
                        node_cursor.as_ref(),
                        NODE_PAGE_SIZE,
                    )
                    .unwrap();
                assert_eq!(nodes_page.start_ordinal as usize, nodes.len());
                assert_eq!(nodes_page.total_nodes as usize, NODES_PER_SCENE);
                assert!(!nodes_page.items.is_empty());
                assert!(nodes_page.items.len() <= usize::from(NODE_PAGE_SIZE));
                assert!(
                    serde_json::to_vec(&nodes_page).unwrap().len() <= MAX_PROJECTION_RESPONSE_BYTES
                );
                nodes.extend(nodes_page.items);
                node_pages += 1;
                node_cursor = nodes_page.next_cursor;
                assert_eq!(node_cursor.is_some(), nodes.len() < scene.nodes.len());
                if node_cursor.is_none() {
                    break;
                }
            }
            assert_eq!(&nodes, &scene.nodes);
            node_count += nodes.len();
            scene_count += 1;
        }
        scene_pages += 1;
        scene_cursor = page.next_cursor;
        assert_eq!(scene_cursor.is_some(), scene_count < SCENES);
        assert_missing();
        if scene_cursor.is_none() {
            break;
        }
    }
    assert_eq!(scene_count, SCENES);
    assert_eq!(node_count, SCENES * NODES_PER_SCENE);
    assert_eq!(
        scene_pages,
        SCENES.div_ceil(usize::from(MAX_SCENE_PAGE_ITEMS))
    );
    assert_eq!(
        node_pages,
        SCENES * NODES_PER_SCENE.div_ceil(usize::from(NODE_PAGE_SIZE))
    );
    drop(service);
    assert_eq!(logical_rows(&database), sql_before);
    assert_eq!(cas_files(&blob_root), cas_before);
    assert_missing();

    let reopened = StudioService::open(&database).unwrap();
    assert_eq!(reopened.project(expected.id).unwrap(), expected);
    assert_eq!(
        reopened
            .project_projection_head(expected.id)
            .unwrap()
            .identity,
        head.identity
    );
    drop(reopened);
    assert_eq!(logical_rows(&database), sql_before);
    assert_eq!(cas_files(&blob_root), cas_before);
    let (retained_source, retained_bytes, retained_digest, retained_blob) = &imports[2];
    assert_eq!(fs::read(retained_source).unwrap(), *retained_bytes);
    assert_eq!(fs::read(retained_blob).unwrap(), *retained_bytes);
    assert_eq!(
        hex::encode(Sha256::digest(fs::read(retained_blob).unwrap())),
        *retained_digest
    );
    assert_missing();
    println!(
        "project08-missing-media: projects={} project_pages={} scenes={} scene_pages={} nodes={} node_pages={} imported_assets=3 missing_blobs=2 retained_blobs=1 sql_unchanged=true",
        expected_library.len(),
        library_pages,
        scene_count,
        scene_pages,
        node_count,
        node_pages
    );
}
