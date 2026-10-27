//! Real Store/StudioService component controls for the new canonical intent.
//! Graph positives deliberately call Store in isolation; they are not native
//! admission, a Service Graph success, or evidence of UI UNKNOWN/ACK recovery.
//! No renderer, capability or ready context is fabricated by these controls.
use motionwright_domain::*;
use motionwright_service::StudioService;
use motionwright_storage::{
    APPLY_REQUEST_ENCODING_V1, ApplyOutcome, ApplyReceiptObservation, StorageError,
    VerifiedApplyReceiptV1,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use uuid::Uuid;

#[path = "../../domain/tests/support/semantic_graph_source.rs"]
mod source;

fn apply(
    service: &StudioService,
    project: &Project,
    request: &str,
    change: &Change,
) -> ApplyOutcome {
    service
        .apply(project.id, &project.stamp(), request, change)
        .unwrap()
}

fn store_apply(
    service: &StudioService,
    project: &Project,
    request: &str,
    change: &Change,
) -> ApplyOutcome {
    let store = service.store();
    let mut guard = store.lock();
    guard
        .apply(project.id, &project.stamp(), request, change)
        .unwrap()
}

fn observe(
    service: &StudioService,
    base: &Project,
    request: &str,
    change: &Change,
) -> ApplyReceiptObservation {
    let store = service.store();
    let mut guard = store.lock();
    guard
        .observe_apply_receipt_v1(
            base.id,
            &base.stamp(),
            request,
            change,
            APPLY_REQUEST_ENCODING_V1,
        )
        .unwrap()
}

fn verified(
    service: &StudioService,
    base: &Project,
    request: &str,
    change: &Change,
) -> VerifiedApplyReceiptV1 {
    match observe(service, base, request, change) {
        ApplyReceiptObservation::KnownVerifiedV1 { binding } => *binding,
        other => panic!("expected an actual verified V1 receipt, got {other:?}"),
    }
}

fn node(name: &str, x: f64, y: f64, width: f64, height: f64) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: "shape".into(),
        parent_id: None,
        x,
        y,
        width,
        height,
        rotation_deg: 0.0,
        opacity: 0.75,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle::default(),
        relations: Vec::new(),
        property_locks: BTreeSet::new(),
        keyframes: Vec::new(),
    }
}

fn seed_canvas(service: &StudioService) -> (Project, Uuid, Uuid, Uuid, Uuid) {
    let project = service
        .create_project("Canonical Canvas persistence")
        .unwrap();
    let mut project = apply(
        service,
        &project,
        "canvas-scene",
        &Change::AddScene {
            name: "Canvas".into(),
            objective: "Preserve explicit author intent".into(),
            duration_seconds: 5,
        },
    )
    .project;
    let scene_id = project.scenes[0].id;
    let old = node("Old parent", 100.0, 200.0, 80.0, 40.0);
    let new = node("New parent", 400.0, 100.0, 120.0, 60.0);
    let mut child = node("Child", 120.0, 230.0, 20.0, 10.0);
    child.parent_id = Some(old.id);
    child.rotation_deg = 15.0;
    let ids = (scene_id, old.id, new.id, child.id);
    for (index, node) in [old, new, child].into_iter().enumerate() {
        project = apply(
            service,
            &project,
            &format!("canvas-node-{index}"),
            &Change::AddCanvasNode { scene_id, node },
        )
        .project;
    }
    (project, ids.0, ids.1, ids.2, ids.3)
}

fn saved_node(project: &Project, id: Uuid) -> &CanvasNode {
    project.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == id)
        .unwrap()
}

#[test]
fn service_reparent_absent_wire_replays_existing_v1_receipt_with_the_legacy_digest() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("canvas-legacy.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (base, scene_id, _, _, child_id) = seed_canvas(&service);
    // Literal pre-extension member order is also the original canonical Change
    // byte order. The unrelated parent_id:null remains its old wire meaning.
    let old_wire = format!(
        "{{\"type\":\"reparent_canvas_node\",\"scene_id\":\"{scene_id}\",\"node_id\":\"{child_id}\",\"parent_id\":null,\"z_index\":7}}"
    );
    let old_change: Change = serde_json::from_str(&old_wire).unwrap();
    let explicit_none = Change::ReparentCanvasNode {
        scene_id,
        node_id: child_id,
        parent_id: None,
        z_index: 7,
        preserve: None,
    };
    assert_eq!(old_change, explicit_none);
    assert_eq!(serde_json::to_string(&explicit_none).unwrap(), old_wire);
    let request = "original-absent-reparent";
    let first = apply(&service, &base, request, &old_change);
    assert!(!first.replayed);
    assert_eq!(first.previous_revision, base.revision);
    assert_eq!(first.project.revision, base.revision + 1);
    let binding = verified(&service, &base, request, &old_change);
    // Independent original tuple bytes, not the new optional-field serializer.
    let original_tuple = format!("[\"{}\",{old_wire}]", base.id);
    assert_eq!(
        binding.request_sha256,
        format!("{:x}", Sha256::digest(original_tuple.as_bytes()))
    );
    assert_eq!(binding.base_revision, base.revision.to_string());
    assert_eq!(binding.result_revision, first.project.revision.to_string());
    let replay = apply(&service, &base, request, &explicit_none);
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(verified(&service, &base, request, &explicit_none), binding);
    let events = service.history(base.id, base.revision, 16).unwrap();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].change, old_change);
    let different_intent = Change::ReparentCanvasNode {
        scene_id,
        node_id: child_id,
        parent_id: None,
        z_index: 7,
        preserve: Some(ReparentCoordinatePolicy::World),
    };
    assert!(matches!(
        service.apply(base.id, &base.stamp(), request, &different_intent),
        Err(StorageError::RequestReuse)
    ));
    let mut malformed: serde_json::Value = serde_json::from_str(&old_wire).unwrap();
    malformed["preserve"] = serde_json::Value::Null;
    assert!(serde_json::from_value::<Change>(malformed).is_err());
    assert_eq!(service.project(base.id).unwrap(), first.project);
    assert_eq!(service.history(base.id, base.revision, 16).unwrap(), events);
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(verified(&reopened, &base, request, &old_change), binding);
    let replay = apply(&reopened, &base, request, &explicit_none);
    assert!(replay.replayed);
    assert_eq!(replay.project, first.project);
    assert_eq!(reopened.project(base.id).unwrap(), first.project);
    assert_eq!(
        reopened.history(base.id, base.revision, 16).unwrap(),
        events
    );
}

#[test]
fn service_local_reparent_and_explicit_inverse_are_one_revision_each_and_reopen_exactly() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("canvas-local.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (base, scene_id, old_id, new_id, child_id) = seed_canvas(&service);
    let original = saved_node(&base, child_id).clone();
    let frozen = Change::ReparentCanvasNode {
        scene_id,
        node_id: child_id,
        parent_id: Some(new_id),
        z_index: 7,
        preserve: Some(ReparentCoordinatePolicy::Local),
    };
    let frozen_bytes = serde_json::to_vec(&frozen).unwrap();
    let moved = apply(&service, &base, "local-reparent", &frozen);
    assert!(!moved.replayed);
    assert_eq!(moved.project.revision, base.revision + 1);
    let child = saved_node(&moved.project, child_id);
    assert_eq!((child.x, child.y, child.rotation_deg), (420.0, 130.0, 15.0));
    assert_eq!(
        (child.width, child.height, child.opacity),
        (original.width, original.height, original.opacity)
    );
    assert_eq!(child.parent_id, Some(new_id));
    assert_eq!(child.z_index, 7);
    for parent_id in [old_id, new_id] {
        assert_eq!(
            saved_node(&moved.project, parent_id),
            saved_node(&base, parent_id)
        );
    }
    let first_binding = verified(&service, &base, "local-reparent", &frozen);
    let inverse = Change::ReparentCanvasNode {
        scene_id,
        node_id: child_id,
        parent_id: Some(old_id),
        z_index: original.z_index,
        preserve: Some(ReparentCoordinatePolicy::Local),
    };
    // This is one explicit inverse Change; it does not model the UI undo stack.
    let restored = apply(&service, &moved.project, "local-explicit-inverse", &inverse);
    assert_eq!(restored.project.revision, moved.project.revision + 1);
    assert_eq!(saved_node(&restored.project, child_id), &original);
    let events = service.history(base.id, base.revision, 16).unwrap();
    assert_eq!(events.len(), 2);
    assert_eq!(events[0].change, frozen);
    assert_eq!(events[1].change, inverse);
    assert_eq!(serde_json::to_vec(&frozen).unwrap(), frozen_bytes);
    let replay = apply(&service, &base, "local-reparent", &frozen);
    assert!(replay.replayed);
    assert_eq!(replay.project, moved.project);
    assert_eq!(service.project(base.id).unwrap(), restored.project);
    assert_eq!(
        verified(&service, &base, "local-reparent", &frozen),
        first_binding
    );
    drop(service);
    let reopened = StudioService::open(&path).unwrap();
    assert_eq!(reopened.project(base.id).unwrap(), restored.project);
    assert_eq!(
        reopened.history(base.id, base.revision, 16).unwrap(),
        events
    );
    assert_eq!(
        verified(&reopened, &base, "local-reparent", &frozen),
        first_binding
    );
}

#[test]
fn service_reparent_cas_rejects_either_parent_drift_without_reinterpreting_frozen_intent() {
    let directory = tempfile::tempdir().unwrap();
    let service =
        StudioService::open(directory.path().join("canvas-parent-drift.sqlite3")).unwrap();
    for change_old_parent in [true, false] {
        let (base, scene_id, old_id, new_id, child_id) = seed_canvas(&service);
        let frozen = Change::ReparentCanvasNode {
            scene_id,
            node_id: child_id,
            parent_id: Some(new_id),
            z_index: 7,
            preserve: Some(ReparentCoordinatePolicy::Local),
        };
        let bytes = serde_json::to_vec(&frozen).unwrap();
        let parent_id = if change_old_parent { old_id } else { new_id };
        let parent = saved_node(&base, parent_id);
        let drifted = apply(
            &service,
            &base,
            "actual-parent-drift",
            &Change::TransformCanvasNode {
                scene_id,
                node_id: parent_id,
                transform: CanvasTransform {
                    x: parent.x + 10.0,
                    y: parent.y,
                    width: parent.width,
                    height: parent.height,
                    rotation_deg: parent.rotation_deg + 15.0,
                    opacity: parent.opacity,
                },
            },
        )
        .project;
        let events = service.history(base.id, 0, 1024).unwrap();
        assert!(matches!(
            service.apply(base.id, &base.stamp(), "frozen-local-intent", &frozen),
            Err(StorageError::Conflict { expected, actual }) if expected == base.revision && actual == drifted.revision
        ));
        assert_eq!(
            observe(&service, &base, "frozen-local-intent", &frozen),
            ApplyReceiptObservation::NotFound
        );
        assert_eq!(serde_json::to_vec(&frozen).unwrap(), bytes);
        assert_eq!(service.project(base.id).unwrap(), drifted);
        assert_eq!(service.history(base.id, 0, 1024).unwrap(), events);
        assert_eq!(saved_node(&drifted, child_id), saved_node(&base, child_id));
    }
}

fn set_placement(graph: &mut SemanticGraph, placement: Option<GraphLabelPlacement>) {
    match &mut graph.content {
        GraphContent::CausalDiagram { edges, .. } => edges[0].label_placement = placement,
        GraphContent::SystemFlow { edges, .. } => edges[0].label_placement = placement,
    }
}

fn resolve(graph: &SemanticGraph) -> GraphBox {
    match &graph.content {
        GraphContent::CausalDiagram { edges, .. } => edges[0].resolved_label_box(graph).unwrap(),
        GraphContent::SystemFlow { edges, .. } => edges[0].resolved_label_box(graph).unwrap(),
    }
}

#[test]
fn store_component_graph_auto_manual_source_replays_reopens_and_preserves_service_refusal() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("graph-label-source.sqlite3");
    let mut service = StudioService::open(&path).unwrap();
    for flow in [false, true] {
        let base = service
            .create_project("Canonical Graph label persistence")
            .unwrap();
        let base = apply(
            &service,
            &base,
            "graph-scene",
            &Change::AddScene {
                name: "Explanatory graph".into(),
                objective: "Persist authored label intent".into(),
                duration_seconds: 6,
            },
        )
        .project;
        let base = apply(
            &service,
            &base,
            "graph-renderer",
            &Change::SetSceneRenderer {
                scene_id: base.scenes[0].id,
                renderer: RendererKind::ManimCommunity,
            },
        )
        .project;
        let manual = source::graph_for(&base, flow);
        let manual_box = resolve(&manual);
        let manual_change = Change::UpsertSemanticGraph {
            graph: Box::new(manual.clone()),
        };
        let legacy_wire = serde_json::to_value(&manual_change).unwrap();
        assert!(
            legacy_wire["graph"]["content"]["edges"][0]
                .get("label_placement")
                .is_none()
        );
        let journal = service.history(base.id, 0, 1024).unwrap();
        let error = service
            .apply(
                base.id,
                &base.stamp(),
                "no-service-admission",
                &manual_change,
            )
            .unwrap_err();
        assert!(
            error
                .to_string()
                .contains("Semantic Graph writes remain unavailable")
        );
        assert_eq!(service.project(base.id).unwrap(), base);
        assert_eq!(service.history(base.id, 0, 1024).unwrap(), journal);
        assert_eq!(
            observe(&service, &base, "no-service-admission", &manual_change),
            ApplyReceiptObservation::NotFound
        );

        let saved = store_apply(&service, &base, "legacy-graph-source", &manual_change);
        assert_eq!(saved.project.revision, base.revision + 1);
        let legacy_receipt = verified(&service, &base, "legacy-graph-source", &manual_change);
        let reparsed: Change = serde_json::from_value(legacy_wire).unwrap();
        let replay = store_apply(&service, &base, "legacy-graph-source", &reparsed);
        assert!(replay.replayed);
        assert_eq!(replay.project, saved.project);
        let mut automatic = manual.clone();
        set_placement(
            &mut automatic,
            Some(GraphLabelPlacement::PolylineMidpoint {
                offset: GraphPoint { x: -5.0, y: 10.0 },
            }),
        );
        match &mut automatic.content {
            GraphContent::CausalDiagram { vertices, .. }
            | GraphContent::SystemFlow { vertices, .. } => vertices[1].r#box.x = 540.0,
        }
        let stale = Change::UpsertSemanticGraph {
            graph: Box::new(automatic.clone()),
        };
        let journal = service.history(base.id, 0, 1024).unwrap();
        {
            let store = service.store();
            assert!(
                store
                    .lock()
                    .apply(
                        base.id,
                        &saved.project.stamp(),
                        "stale-graph-source",
                        &stale
                    )
                    .is_err()
            );
        }
        assert_eq!(service.project(base.id).unwrap(), saved.project);
        assert_eq!(service.history(base.id, 0, 1024).unwrap(), journal);
        assert_eq!(
            observe(&service, &saved.project, "stale-graph-source", &stale),
            ApplyReceiptObservation::NotFound
        );

        let locked = apply(
            &service,
            &saved.project,
            "lock-graph-edge",
            &Change::SetLock {
                resource: format!("graph-edge:{}:{}", manual.id, manual.content.edge_ids()[0]),
                kind: LockKind::Position,
                note: "Existing label position lock".into(),
            },
        )
        .project;
        automatic.authorship.source_snapshot = GraphSourceSnapshot::from_project(&locked);
        let locked_change = Change::UpsertSemanticGraph {
            graph: Box::new(automatic.clone()),
        };
        let journal = service.history(base.id, 0, 1024).unwrap();
        {
            let store = service.store();
            assert!(matches!(
                store.lock().apply(
                    base.id,
                    &locked.stamp(),
                    "locked-graph-label",
                    &locked_change
                ),
                Err(StorageError::Domain(DomainError::Locked(_)))
            ));
        }
        assert_eq!(service.project(base.id).unwrap(), locked);
        assert_eq!(service.history(base.id, 0, 1024).unwrap(), journal);
        assert_eq!(
            observe(&service, &locked, "locked-graph-label", &locked_change),
            ApplyReceiptObservation::NotFound
        );
        let unlocked = apply(
            &service,
            &locked,
            "unlock-graph-edge",
            &Change::RemoveLock {
                lock_id: locked.locks.last().unwrap().id,
            },
        )
        .project;
        automatic.authorship.source_snapshot = GraphSourceSnapshot::from_project(&unlocked);
        let auto_change = Change::UpsertSemanticGraph {
            graph: Box::new(automatic.clone()),
        };
        let auto = store_apply(&service, &unlocked, "explicit-auto-label", &auto_change);
        assert_eq!(auto.project.revision, unlocked.revision + 1);
        assert_eq!(
            resolve(&automatic),
            GraphBox {
                x: 305.0,
                y: 110.0,
                width: 120.0,
                height: 40.0
            }
        );
        let saved_graph = &auto.project.production_design.semantic_graphs[0];
        assert_eq!(saved_graph, &automatic);
        assert_eq!(
            serde_json::to_value(saved_graph).unwrap()["content"]["edges"][0]["label_box"],
            serde_json::to_value(&manual_box).unwrap()
        );
        assert_eq!(
            serde_json::to_value(saved_graph).unwrap()["content"]["edges"][0]["waypoints"],
            serde_json::to_value(&manual).unwrap()["content"]["edges"][0]["waypoints"]
        );
        let auto_receipt = verified(&service, &unlocked, "explicit-auto-label", &auto_change);
        let events = service.history(base.id, unlocked.revision, 16).unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].change, auto_change);
        let replay = store_apply(&service, &unlocked, "explicit-auto-label", &auto_change);
        assert!(replay.replayed);
        assert_eq!(replay.project, auto.project);
        drop(service);
        service = StudioService::open(&path).unwrap();
        assert_eq!(service.project(base.id).unwrap(), auto.project);
        assert_eq!(
            verified(&service, &unlocked, "explicit-auto-label", &auto_change),
            auto_receipt
        );
        assert_eq!(
            verified(&service, &base, "legacy-graph-source", &manual_change),
            legacy_receipt
        );
        assert_eq!(
            service.history(base.id, unlocked.revision, 16).unwrap(),
            events
        );

        let mut manual_again = automatic;
        set_placement(&mut manual_again, None);
        manual_again.authorship.source_snapshot = GraphSourceSnapshot::from_project(&auto.project);
        let manual_again_change = Change::UpsertSemanticGraph {
            graph: Box::new(manual_again.clone()),
        };
        let restored = store_apply(
            &service,
            &auto.project,
            "explicit-manual-label",
            &manual_again_change,
        );
        assert_eq!(restored.project.revision, auto.project.revision + 1);
        assert_eq!(resolve(&manual_again), manual_box);
        assert_eq!(
            restored.project.production_design.semantic_graphs[0],
            manual_again
        );
        let events = service.history(base.id, auto.project.revision, 16).unwrap();
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].change, manual_again_change);
        assert!(
            service
                .apply(
                    base.id,
                    &restored.project.stamp(),
                    "still-no-service-admission",
                    &manual_again_change
                )
                .is_err()
        );
        assert_eq!(service.project(base.id).unwrap(), restored.project);
        assert_eq!(
            service.history(base.id, auto.project.revision, 16).unwrap(),
            events
        );
    }
}
