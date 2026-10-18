//! Regressions across the Studio invoke JSON shape, real SQLite/CAS, and reopen.
//! All source content below is an explicitly synthetic test fixture. These are
//! service integration tests, not evidence of the installed desktop or renderer.

#[allow(dead_code)]
#[path = "../examples/studio_review_session.rs"]
mod transport;

use motionwright_domain::{
    CanvasNode, Change, CreativePatchPreview, NodeStyle, Project, hero_node_id,
};
use motionwright_service::{ProjectEvent, StudioService};
use motionwright_storage::StorageError;
use serde_json::{Value, json};
use transport::Session;
use uuid::Uuid;

fn invoke(session: &mut Session, command: &str, args: Value) -> Result<Value, String> {
    // Cross a serialization boundary; do not bypass the desktop-shaped request
    // deserializer with a preconstructed Rust Change.
    let wire =
        serde_json::to_string(&json!({"id": Uuid::now_v7(), "command": command, "args": args}))
            .unwrap();
    let response = session.handle(serde_json::from_str(&wire).unwrap());
    if response["ok"] == true {
        Ok(response["result"].clone())
    } else {
        Err(response["error"].as_str().unwrap().into())
    }
}

fn bootstrap(session: &mut Session) -> Project {
    let response = invoke(session, "bootstrap", json!({})).unwrap();
    assert_eq!(response["test_transport"], "service-test-jsonl");
    assert_eq!(response["fixture"]["label"], "synthetic_test_fixture");
    assert!(
        response["project"]["title"]
            .as_str()
            .unwrap()
            .contains("Synthetic")
    );
    serde_json::from_value(response["project"].clone()).unwrap()
}

fn targets(project: &Project) -> (Uuid, Uuid) {
    let hero = &project.production_design.heroes[0];
    (hero.scene_id, hero_node_id(hero.id, "body"))
}

fn node(project: &Project, scene_id: Uuid, node_id: Uuid) -> &CanvasNode {
    project
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .unwrap()
        .nodes
        .iter()
        .find(|node| node.id == node_id)
        .unwrap()
}

fn grant(session: &mut Session, project: &Project, request_id: &str) -> Value {
    invoke(
        session,
        "issue_effect_grant",
        json!({"request": {
            "effect": "project_edit", "project_id": project.id,
            "generation": project.generation, "revision": project.revision, "subject": request_id
        }}),
    )
    .unwrap()["token"]
        .clone()
}

fn apply_request(project: &Project, change: Value, request_id: &str, effect_grant: Value) -> Value {
    json!({"request": {"project_id": project.id, "generation": project.generation,
        "revision": project.revision, "request_id": request_id,
        "effect_grant": effect_grant, "change": change}})
}

fn edit_result(session: &mut Session, project: &Project, change: Value) -> Result<Project, String> {
    let request_id = Uuid::now_v7().to_string();
    let token = grant(session, project, &request_id);
    invoke(
        session,
        "apply_change",
        apply_request(project, change, &request_id, token),
    )
    .map(|value| serde_json::from_value(value).unwrap())
}

fn edit(session: &mut Session, project: &Project, change: Value) -> Project {
    edit_result(session, project, change).unwrap()
}

fn read(session: &mut Session, project_id: Uuid, command: &str) -> Project {
    serde_json::from_value(
        invoke(
            session,
            command,
            json!({"request": {"project_id": project_id}}),
        )
        .unwrap(),
    )
    .unwrap()
}

fn history(session: &mut Session, project: &Project, after_revision: u64) -> Vec<ProjectEvent> {
    serde_json::from_value(
        invoke(
            session,
            "project_history",
            json!({"request": {
                "project_id": project.id, "after_revision": after_revision, "limit": 100
            }}),
        )
        .unwrap(),
    )
    .unwrap()
}

fn patch(project: &Project, copy: &str, move_x: f64) -> Value {
    let (scene_id, node_id) = targets(project);
    let target = node(project, scene_id, node_id);
    json!({"scene_id": scene_id, "base_revision": project.revision,
    "rationale": "Synthetic scoped edit: change only the reviewed copy and x position",
    "edits": [
        {"kind": "text", "node_id": node_id, "text": copy},
        {"kind": "transform", "node_id": node_id, "transform": {
            "x": target.x + move_x, "y": target.y, "width": target.width,
            "height": target.height, "rotation_deg": target.rotation_deg, "opacity": target.opacity
        }}
    ]})
}

fn preview(
    session: &mut Session,
    project: &Project,
    patch: Value,
) -> Result<CreativePatchPreview, String> {
    invoke(
        session,
        "preview_creative_patch",
        json!({"request": {
            "project_id": project.id, "generation": project.generation,
            "revision": project.revision, "patch": patch
        }}),
    )
    .map(|value| serde_json::from_value(value).unwrap())
}

fn extra_node(name: &str, kind: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x: 0.0,
        y: 0.0,
        width: 960.0,
        height: 540.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: Default::default(),
        z_index: 0,
        style: NodeStyle::default(),
        relations: Vec::new(),
        property_locks: Default::default(),
        keyframes: Vec::new(),
    }
}

#[test]
fn desktop_json_preview_is_read_only_then_text_and_transform_commit_once_and_survive_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("review.sqlite3");
    let mut session = Session::open(&path, true).unwrap();
    let initial = bootstrap(&mut session);
    let (scene_id, node_id) = targets(&initial);
    let proposal = patch(&initial, "A shorter, reviewed synthetic sentence.", 32.0);
    let inspection = preview(&mut session, &initial, proposal.clone()).unwrap();
    assert_eq!(inspection.source_revision, initial.revision);
    assert_eq!(
        inspection.before,
        vec![node(&initial, scene_id, node_id).clone()]
    );
    assert_eq!(inspection.after.len(), 1);
    assert_eq!(read(&mut session, initial.id, "get_project"), initial);
    assert!(history(&mut session, &initial, initial.revision).is_empty());

    let change = json!({"type": "apply_creative_patch", "patch": proposal});
    let applied = edit(&mut session, &initial, change.clone());
    assert_eq!(applied.revision, initial.revision + 1);
    assert_eq!(applied.generation, initial.generation);
    assert_eq!(node(&applied, scene_id, node_id), &inspection.after[0]);
    for scene in &initial.scenes {
        let current = applied
            .scenes
            .iter()
            .find(|candidate| candidate.id == scene.id)
            .unwrap();
        assert_eq!(current.start, scene.start);
        assert_eq!(current.duration, scene.duration);
        for unchanged in scene.nodes.iter().filter(|node| node.id != node_id) {
            assert_eq!(node(&applied, scene.id, unchanged.id), unchanged);
        }
    }
    assert_eq!(applied.assets, initial.assets);
    let events = history(&mut session, &applied, initial.revision);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].revision, applied.revision);
    assert_eq!(serde_json::to_value(&events[0].change).unwrap(), change);
    let record = &applied.production_design.patches[0];
    assert_eq!(record.before, inspection.before);
    assert_eq!(record.after, inspection.after);
    assert_eq!(record.source_revision, initial.revision);

    drop(session);
    let mut reopened = Session::open(&path, false).unwrap();
    assert_eq!(read(&mut reopened, applied.id, "get_project"), applied);
    assert_eq!(history(&mut reopened, &applied, initial.revision), events);
    let service = StudioService::open(&path).unwrap();
    let digest = applied.assets[0].content_sha256.as_deref().unwrap();
    let bytes = service.store().lock().read_blob(digest, 1024).unwrap();
    assert!(
        String::from_utf8(bytes)
            .unwrap()
            .contains("Synthetic test fixture")
    );
    assert!(path.is_file());
}

#[test]
fn independent_service_instances_reject_stale_json_and_preserve_the_newer_author_decision() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("concurrent.sqlite3");
    let mut editor_a = Session::open(&path, true).unwrap();
    let initial = bootstrap(&mut editor_a);
    let mut editor_b = Session::open(&path, false).unwrap();
    let (scene_id, node_id) = targets(&initial);
    let proposal = patch(&initial, "An older proposed sentence.", 8.0);
    let request_id = "review-before-concurrent-author-edit";
    let token = grant(&mut editor_a, &initial, request_id);
    let stale_change = json!({"type": "apply_creative_patch", "patch": proposal});
    let mut style = node(&initial, scene_id, node_id).style.clone();
    style.fill = Some("#E2B892".into());
    let current = edit(
        &mut editor_b,
        &initial,
        json!({"type": "update_canvas_style",
        "scene_id": scene_id, "node_id": node_id, "style": style}),
    );
    let error = invoke(
        &mut editor_a,
        "apply_change",
        apply_request(&initial, stale_change.clone(), request_id, token),
    )
    .unwrap_err();
    assert!(error.contains("stale"), "{error}");
    assert_eq!(read(&mut editor_a, initial.id, "get_project"), current);
    assert!(
        preview(&mut editor_a, &initial, proposal.clone())
            .unwrap_err()
            .contains("stale")
    );

    // Also exercise the storage CAS after deserializing precisely the same
    // Change. This proves the guard is backed by SQLite, not only by transport.
    let independent_service = StudioService::open(&path).unwrap();
    let change: Change = serde_json::from_value(stale_change.clone()).unwrap();
    assert!(
        matches!(independent_service.apply(initial.id, &initial.stamp(), request_id, &change),
        Err(StorageError::Conflict { expected, actual }) if expected == initial.revision && actual == current.revision)
    );
    let error = edit_result(&mut editor_a, &current, stale_change).unwrap_err();
    assert!(error.contains("stale"));

    let rebased = patch(&current, "An explicitly reviewed later sentence.", 8.0);
    let applied = edit(
        &mut editor_a,
        &current,
        json!({"type": "apply_creative_patch", "patch": rebased}),
    );
    assert_eq!(node(&applied, scene_id, node_id).style, style);
    assert_eq!(history(&mut editor_a, &applied, initial.revision).len(), 2);

    // A later edit of the SAME field must also survive stale proposals.
    let old_copy = patch(&applied, "Do not overwrite the newer author.", 0.0);
    let latest = edit(
        &mut editor_b,
        &applied,
        json!({"type": "update_canvas_text",
        "scene_id": scene_id, "node_id": node_id, "text": "Newer author decision"}),
    );
    assert!(
        edit_result(
            &mut editor_a,
            &latest,
            json!({"type": "apply_creative_patch", "patch": old_copy})
        )
        .unwrap_err()
        .contains("stale")
    );
    assert_eq!(read(&mut editor_a, latest.id, "reopen"), latest);
}

#[test]
fn undo_redo_preserve_structural_edits_new_nodes_retiming_and_unrelated_keyframes_after_reopen() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("structure.sqlite3");
    let mut session = Session::open(&path, true).unwrap();
    let initial = bootstrap(&mut session);
    let (scene_id, node_id) = targets(&initial);
    let proposal = patch(&initial, "Copy with a reversible review record.", 16.0);
    let patched = edit(
        &mut session,
        &initial,
        json!({"type": "apply_creative_patch", "patch": proposal}),
    );
    let patch_id = patched.production_design.patches[0].id;
    let container = extra_node("Later authored group", "group");
    let container_id = container.id;
    let mut current = edit(
        &mut session,
        &patched,
        json!({"type": "add_canvas_node", "scene_id": scene_id, "node": container}),
    );
    current = edit(
        &mut session,
        &current,
        json!({"type": "reparent_canvas_node", "scene_id": scene_id,
        "node_id": node_id, "parent_id": container_id, "z_index": 17}),
    );
    current = edit(
        &mut session,
        &current,
        json!({"type": "set_canvas_keyframe", "scene_id": scene_id, "node_id": node_id,
        "keyframe": {"at": {"num": "1", "den": "1"}, "property": "opacity", "value": 0.75, "interpolation": "linear"}}),
    );
    current = edit(
        &mut session,
        &current,
        json!({"type": "set_scene_duration", "scene_id": scene_id,
        "duration": {"num": "8", "den": "1"}}),
    );
    current = edit(
        &mut session,
        &current,
        json!({"type": "move_scene", "scene_id": scene_id, "to_index": 1}),
    );
    let transient = extra_node("Independently deleted decoration", "rectangle");
    let transient_id = transient.id;
    current = edit(
        &mut session,
        &current,
        json!({"type": "add_canvas_node", "scene_id": scene_id, "node": transient}),
    );
    current = edit(
        &mut session,
        &current,
        json!({"type": "remove_canvas_node", "scene_id": scene_id, "node_id": transient_id}),
    );
    current = read(&mut session, current.id, "reopen");
    let preview: CreativePatchPreview = serde_json::from_value(invoke(&mut session, "preview_creative_patch_undo", json!({"request": {
        "project_id": current.id, "generation": current.generation, "revision": current.revision, "patch_id": patch_id
    }})).unwrap()).unwrap();
    let scene = current
        .scenes
        .iter()
        .find(|scene| scene.id == scene_id)
        .unwrap();
    assert_eq!(preview.dirty_start, scene.start);
    assert_eq!(
        preview.dirty_end,
        scene.start.checked_add(scene.duration).unwrap()
    );
    assert_eq!(read(&mut session, current.id, "get_project"), current);
    let undone = edit(
        &mut session,
        &current,
        json!({"type": "undo_creative_patch", "patch_id": patch_id}),
    );
    let undone_node = node(&undone, scene_id, node_id);
    assert_eq!(undone_node.text, node(&initial, scene_id, node_id).text);
    assert_eq!(undone_node.x, node(&initial, scene_id, node_id).x);
    assert_eq!(undone_node.parent_id, Some(container_id));
    assert_eq!(undone_node.z_index, 17);
    assert_eq!(
        undone_node.keyframes,
        node(&current, scene_id, node_id).keyframes
    );
    assert_eq!(
        node(&undone, scene_id, container_id),
        node(&current, scene_id, container_id)
    );
    let unaffected_scene = current
        .scenes
        .iter()
        .find(|scene| scene.id != scene_id)
        .unwrap();
    assert_eq!(
        undone
            .scenes
            .iter()
            .find(|scene| scene.id == unaffected_scene.id)
            .unwrap(),
        unaffected_scene
    );
    let mut expected_scenes = current.scenes.clone();
    *expected_scenes
        .iter_mut()
        .find(|scene| scene.id == scene_id)
        .unwrap()
        .nodes
        .iter_mut()
        .find(|node| node.id == node_id)
        .unwrap() = undone_node.clone();
    assert_eq!(undone.scenes, expected_scenes);
    let inverse_id = undone.production_design.patches.last().unwrap().id;
    assert_eq!(
        undone.production_design.patches.last().unwrap().reverts,
        Some(patch_id)
    );
    let redone = edit(
        &mut session,
        &undone,
        json!({"type": "undo_creative_patch", "patch_id": inverse_id}),
    );
    assert_eq!(redone.scenes, current.scenes);
    assert_eq!(redone.revision, current.revision + 2);
    assert_eq!(read(&mut session, redone.id, "reopen"), redone);
    let events = history(&mut session, &redone, current.revision);
    assert_eq!(events.len(), 2);
    assert!(
        events
            .iter()
            .all(|event| matches!(event.change, Change::UndoCreativePatch { .. }))
    );
}

#[test]
fn conflict_or_missing_patch_target_never_commits_a_partial_undo() {
    for mode in ["newer-copy", "missing-target", "text-lock"] {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("refusal.sqlite3");
        let mut session = Session::open(&path, true).unwrap();
        let initial = bootstrap(&mut session);
        let (scene_id, node_id) = targets(&initial);
        let headline_id = hero_node_id(initial.production_design.heroes[0].id, "headline");
        let proposal = json!({"scene_id": scene_id, "base_revision": initial.revision,
        "rationale": "Review two independent synthetic text nodes in one transaction", "edits": [
            {"kind": "text", "node_id": node_id, "text": "Reviewed body"},
            {"kind": "text", "node_id": headline_id, "text": "Reviewed headline"}
        ]});
        let patched = edit(
            &mut session,
            &initial,
            json!({"type": "apply_creative_patch", "patch": proposal}),
        );
        let patch_id = patched.production_design.patches[0].id;
        let later = match mode {
            "newer-copy" => {
                json!({"type": "update_canvas_text", "scene_id": scene_id, "node_id": node_id, "text": "Later author copy"})
            }
            "missing-target" => {
                json!({"type": "remove_canvas_node", "scene_id": scene_id, "node_id": node_id})
            }
            _ => {
                json!({"type": "set_node_property_lock", "scene_id": scene_id, "node_id": node_id, "property": "text", "locked": true})
            }
        };
        let current = edit(&mut session, &patched, later);
        assert!(
            edit_result(
                &mut session,
                &current,
                json!({"type": "undo_creative_patch", "patch_id": patch_id})
            )
            .is_err(),
            "{mode}"
        );
        assert_eq!(read(&mut session, current.id, "reopen"), current, "{mode}");
        assert_eq!(
            node(&current, scene_id, headline_id).text.as_deref(),
            Some("Reviewed headline")
        );
        assert!(
            history(&mut session, &current, current.revision).is_empty(),
            "{mode}"
        );
    }
}

#[test]
fn reopen_discards_grants_and_refused_requests_leave_no_event_or_revision() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("session.sqlite3");
    let mut session = Session::open(&path, true).unwrap();
    let initial = bootstrap(&mut session);
    let proposal = patch(&initial, "A draft requires its live scoped grant.", 0.0);
    let request_id = "grant-does-not-survive-reopen";
    let token = grant(&mut session, &initial, request_id);
    assert_eq!(read(&mut session, initial.id, "reopen"), initial);
    let change = json!({"type": "apply_creative_patch", "patch": proposal});
    let error = invoke(
        &mut session,
        "apply_change",
        apply_request(&initial, change.clone(), request_id, token),
    )
    .unwrap_err();
    assert!(error.contains("grant"));
    assert_eq!(read(&mut session, initial.id, "get_project"), initial);
    assert!(history(&mut session, &initial, initial.revision).is_empty());
    let token = grant(&mut session, &initial, request_id);
    let request = apply_request(&initial, change, request_id, token);
    let applied: Project =
        serde_json::from_value(invoke(&mut session, "apply_change", request.clone()).unwrap())
            .unwrap();
    assert!(invoke(&mut session, "apply_change", request).is_err());
    assert_eq!(read(&mut session, initial.id, "get_project"), applied);
    assert_eq!(history(&mut session, &applied, initial.revision).len(), 1);
}
