// Exercise the actual desktop read adapter over SQLite. Tauri transport and
// installed UI consumption are separate integration checks.
#[path = "../../../apps/desktop/src-tauri/src/project_projection.rs"]
mod project_projection;

use motionwright_domain::{
    CameraState, CanvasNode, Change, CoordinateSpace, NodeStyle, Project, RationalTime,
    RendererKind, RevisionStamp, Scene, SceneStatus,
};
use motionwright_service::{MAX_PROJECTION_RESPONSE_BYTES, ProjectionFailure, StudioService};
use motionwright_storage::Store;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};
use std::collections::BTreeSet;
use tempfile::tempdir;
use uuid::Uuid;

fn fixture(scene_count: usize, nodes_per_scene: usize) -> Project {
    let mut project = Project::new("Desktop projection fixture").unwrap();
    for index in 0..scene_count {
        project.scenes.push(Scene {
            id: Uuid::now_v7(),
            name: format!("Scene {index}"),
            objective: "Inspect the persisted selection".into(),
            start: RationalTime::new(i64::try_from(index).unwrap() * 10, 1).unwrap(),
            duration: RationalTime::new(10, 1).unwrap(),
            renderer: RendererKind::MotionCanvas,
            status: SceneStatus::Draft,
            beats: vec![],
            nodes: (0..nodes_per_scene)
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
                    text: Some(format!("Retained object {index}/{node_index}")),
                    coordinate_space: CoordinateSpace::ProjectPixels,
                    z_index: 0,
                    style: NodeStyle::default(),
                    relations: vec![],
                    property_locks: BTreeSet::new(),
                    keyframes: vec![],
                })
                .collect(),
            camera: CameraState::default(),
        });
    }
    project.validate().unwrap();
    project
}

fn request<T: DeserializeOwned>(value: Value) -> T {
    // Go through JSON bytes, as the desktop request boundary does.
    serde_json::from_slice(&serde_json::to_vec(&value).unwrap()).unwrap()
}

fn error_json(error: ProjectionFailure) -> Value {
    serde_json::to_value(error).unwrap()
}

fn persist(path: &std::path::Path, project: &Project) -> StudioService {
    let mut store = Store::open(path).unwrap();
    store.create_project(project).unwrap();
    drop(store);
    StudioService::open(path).unwrap()
}

#[test]
fn desktop_pages_round_trip_requests_and_returned_cursors_without_a_full_load() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let project = fixture(2, 2);
    let service = persist(&path, &project);
    let head =
        project_projection::head(&service, request(json!({"project_id": project.id}))).unwrap();
    assert_eq!(head.scene_count, 2);
    let identity = serde_json::to_value(&head.identity).unwrap();
    assert_eq!(identity["revision"], "0");
    let first =
        project_projection::scenes(&service, request(json!({"identity": identity, "limit": 1})))
            .unwrap();
    let first_wire = serde_json::to_value(&first).unwrap();
    let second = project_projection::scenes(
        &service,
        request(json!({
            "identity": identity, "cursor": first_wire["next_cursor"], "limit": 1
        })),
    )
    .unwrap();
    assert_eq!(first.start_ordinal, 0);
    assert_eq!(second.start_ordinal, 1);
    assert_eq!(first.items[0].id, project.scenes[0].id);
    assert_eq!(second.items[0].id, project.scenes[1].id);
    assert!(second.next_cursor.is_none());
    let selected = first.items[0].scene_ref();
    let nodes_first = project_projection::nodes(
        &service,
        request(json!({"identity": identity, "scene": selected, "limit": 1})),
    )
    .unwrap();
    let nodes_first_wire = serde_json::to_value(&nodes_first).unwrap();
    let nodes_second = project_projection::nodes(
        &service,
        request(json!({
            "identity": identity,
            "scene": selected,
            "cursor": nodes_first_wire["next_cursor"],
            "limit": 1
        })),
    )
    .unwrap();
    assert_eq!(nodes_first.items, project.scenes[0].nodes[..1]);
    assert_eq!(nodes_second.items, project.scenes[0].nodes[1..]);
    assert_eq!(nodes_second.start_ordinal, 1);
    assert_eq!(nodes_second.total_nodes, 2);
    assert!(nodes_second.next_cursor.is_none());
    // Full Project compatibility is checked only after the paged reads.
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn desktop_errors_preserve_identity_conflicts_and_reject_malformed_requests() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let project = fixture(2, 2);
    let service = persist(&path, &project);
    let missing =
        project_projection::head(&service, request(json!({"project_id": Uuid::now_v7()})))
            .unwrap_err();
    assert_eq!(error_json(missing), json!({"code": "not_found"}));
    let head =
        project_projection::head(&service, request(json!({"project_id": project.id}))).unwrap();
    let identity = serde_json::to_value(&head.identity).unwrap();
    let invalid =
        project_projection::scenes(&service, request(json!({"identity": identity, "limit": 0})))
            .unwrap_err();
    assert_eq!(
        error_json(invalid),
        json!({"code": "invalid_request_or_content"})
    );
    let mut noncanonical = identity.clone();
    noncanonical["revision"] = json!("00");
    let invalid = project_projection::scenes(
        &service,
        request(json!({"identity": noncanonical, "limit": 1})),
    )
    .unwrap_err();
    assert_eq!(
        error_json(invalid),
        json!({"code": "invalid_request_or_content"})
    );
    let mut wrong_generation = identity.clone();
    wrong_generation["generation"] = json!(Uuid::now_v7());
    let changed = project_projection::scenes(
        &service,
        request(json!({"identity": wrong_generation, "limit": 1})),
    )
    .unwrap_err();
    assert_eq!(error_json(changed), json!({"code": "generation_changed"}));

    // Shape errors are rejected by deserialization before a typed handler runs.
    assert!(
        serde_json::from_value::<project_projection::Head>(
            json!({"project_id": project.id, "unknown": true})
        )
        .is_err()
    );
    let mut numeric_revision = identity.clone();
    numeric_revision["revision"] = json!(0);
    assert!(
        serde_json::from_value::<project_projection::Scenes>(
            json!({"identity": numeric_revision, "limit": 1})
        )
        .is_err()
    );
    assert!(
        serde_json::from_value::<project_projection::Nodes>(
            json!({"identity": identity, "scene": {"ordinal": 0, "id": "invalid"}, "limit": 1})
        )
        .is_err()
    );

    let first =
        project_projection::scenes(&service, request(json!({"identity": identity, "limit": 1})))
            .unwrap();
    let mut peer = Store::open(&path).unwrap();
    peer.apply(
        project.id,
        &RevisionStamp::from(&project),
        "desktop-projection-peer-edit",
        &Change::RenameProject {
            title: "Peer title".into(),
        },
    )
    .unwrap();
    let stale = project_projection::scenes(
        &service,
        request(json!({"identity": identity, "cursor": first.next_cursor, "limit": 1})),
    )
    .unwrap_err();
    assert_eq!(
        error_json(stale),
        json!({"code": "stale_revision", "expected": "0", "actual": "1"})
    );
    let fresh =
        project_projection::head(&service, request(json!({"project_id": project.id}))).unwrap();
    assert_eq!(fresh.identity.revision, "1");
    assert_eq!(fresh.title, "Peer title");
}

#[test]
fn desktop_oversized_pages_return_a_bounded_error_without_truncating_content() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("project.sqlite");
    let mut project = fixture(1, 20);
    for node in &mut project.scenes[0].nodes {
        node.text = Some("\u{00e9}".repeat(48_000));
    }
    project.validate().unwrap();
    let service = persist(&path, &project);
    let head =
        project_projection::head(&service, request(json!({"project_id": project.id}))).unwrap();
    let scene = json!({"ordinal": 0, "id": project.scenes[0].id});
    let error = project_projection::nodes(
        &service,
        request(json!({"identity": head.identity, "scene": scene, "limit": 20})),
    )
    .unwrap_err();
    assert_eq!(
        error_json(error),
        json!({"code": "too_large", "limit": MAX_PROJECTION_RESPONSE_BYTES})
    );
    let one = project_projection::nodes(
        &service,
        request(json!({"identity": head.identity, "scene": scene, "limit": 1})),
    )
    .unwrap();
    assert_eq!(one.items, project.scenes[0].nodes[..1]);
    assert!(serde_json::to_vec(&one).unwrap().len() <= MAX_PROJECTION_RESPONSE_BYTES);
    assert!(one.next_cursor.is_some());
}

#[test]
fn desktop_storage_failures_expose_no_sql_paths_or_document_text() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("private-project.sqlite");
    let project = fixture(1, 1);
    let service = persist(&path, &project);
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute_batch("ALTER TABLE projects RENAME TO private_fixture_table")
        .unwrap();
    let error =
        project_projection::head(&service, request(json!({"project_id": project.id}))).unwrap_err();
    assert_eq!(error_json(error), json!({"code": "unavailable"}));
    connection
        .execute_batch("ALTER TABLE private_fixture_table RENAME TO projects")
        .unwrap();
    let head =
        project_projection::head(&service, request(json!({"project_id": project.id}))).unwrap();
    assert_eq!(head.title, project.title);
}

#[test]
fn desktop_graph_context_is_read_only_blocked_and_rejects_stale_source_after_peer_edit() {
    use motionwright_domain::{GraphAvailability, GraphBlockReason, GraphSourceSnapshot};
    use motionwright_service::SemanticGraphContextRequest;

    let directory = tempdir().unwrap();
    let path = directory.path().join("desktop-graph-context.sqlite");
    let project = fixture(1, 1);
    let service = persist(&path, &project);
    let journal = service.history(project.id, 0, 1024).unwrap();
    let snapshot = GraphSourceSnapshot::from_project(&project);
    let scene_id = project.scenes[0].id;
    let profile_id = project.deliverables[0].id;
    let wire = json!({
        "source_snapshot": snapshot,
        "scene_id": scene_id,
        "variant_profile_ref": profile_id
    });

    let context = project_projection::semantic_graph_context(
        &service,
        request::<SemanticGraphContextRequest>(wire.clone()),
    )
    .unwrap();
    assert_eq!(context.source_snapshot, snapshot);
    assert_eq!(context.scene_id, scene_id);
    assert_eq!(context.variant_profile_ref, profile_id);
    assert_eq!(context.availability, GraphAvailability::Blocked);
    assert_eq!(
        context.blocking_reasons,
        vec![
            GraphBlockReason::GraphWriteUnavailable,
            GraphBlockReason::NativeGraphProfileUnavailable,
            GraphBlockReason::UnsupportedRendererGuardMissing,
            GraphBlockReason::ProfileUnsupported,
        ]
    );
    assert_eq!(
        context.dependencies,
        project.semantic_graph_dependencies().unwrap()
    );
    let response = serde_json::to_value(&context).unwrap();
    assert_eq!(response["source_snapshot"], wire["source_snapshot"]);
    assert_eq!(response["availability"], json!("blocked"));
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(service.history(project.id, 0, 1024).unwrap(), journal);

    let mut peer = Store::open(&path).unwrap();
    let change = Change::RenameProject {
        title: "Peer edit after Graph inspection".into(),
    };
    let edited = peer
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "desktop-graph-context-peer-edit",
            &change,
        )
        .unwrap()
        .project;
    assert_eq!(edited.revision, project.revision + 1);
    let edited_journal = service.history(project.id, 0, 1024).unwrap();
    assert_eq!(edited_journal.len(), journal.len() + 1);
    assert_eq!(edited_journal.last().unwrap().change, change);
    assert!(
        project_projection::semantic_graph_context(
            &service,
            request::<SemanticGraphContextRequest>(wire),
        )
        .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), edited);
    assert_eq!(
        service.history(project.id, 0, 1024).unwrap(),
        edited_journal
    );

    drop(peer);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    let retained = reopened.project(project.id).unwrap();
    assert_eq!(retained, edited);
    let fresh_snapshot = GraphSourceSnapshot::from_project(&retained);
    let fresh = project_projection::semantic_graph_context(
        &reopened,
        request::<SemanticGraphContextRequest>(json!({
            "source_snapshot": fresh_snapshot,
            "scene_id": scene_id,
            "variant_profile_ref": profile_id
        })),
    )
    .unwrap();
    assert_eq!(fresh.source_snapshot, fresh_snapshot);
    assert_eq!(fresh.scene_id, scene_id);
    assert_eq!(fresh.variant_profile_ref, profile_id);
    assert_eq!(fresh.availability, GraphAvailability::Blocked);
    assert_eq!(fresh.blocking_reasons, context.blocking_reasons);
    assert_eq!(
        fresh.dependencies,
        retained.semantic_graph_dependencies().unwrap()
    );
    assert_eq!(reopened.project(project.id).unwrap(), retained);
    assert_eq!(
        reopened.history(project.id, 0, 1024).unwrap(),
        edited_journal
    );
}
