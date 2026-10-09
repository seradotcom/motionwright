use motionwright_domain::{
    CanvasTransform, Change, CreativePatch, HeroConfig, NodeProperty, Project, ScopedCanvasEdit,
    hero_node_id,
};
use motionwright_service::StudioService;
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, change: Change) -> Project {
    service
        .apply(
            project.id,
            &project.stamp(),
            &Uuid::now_v7().to_string(),
            &change,
        )
        .unwrap()
        .project
}
fn seed(service: &StudioService) -> (Project, Uuid) {
    let mut project = service.create_project("Undoable creative work").unwrap();
    project = apply(
        service,
        &project,
        Change::AddScene {
            name: "Hero".into(),
            objective: "Review and reverse scoped changes".into(),
            duration_seconds: 6,
        },
    );
    let instance = Uuid::now_v7();
    project = apply(
        service,
        &project,
        Change::UpsertProductHero {
            instance_id: instance,
            scene_id: project.scenes[0].id,
            config: HeroConfig::default(),
        },
    );
    (project, hero_node_id(instance, "body"))
}
fn text_patch(service: &StudioService, project: &Project, node_id: Uuid, text: &str) -> Project {
    apply(
        service,
        project,
        Change::ApplyCreativePatch {
            patch: CreativePatch {
                scene_id: project.scenes[0].id,
                base_revision: project.revision,
                rationale: "Shorten the message, keeping everything else".into(),
                edits: vec![ScopedCanvasEdit::Text {
                    node_id,
                    text: text.into(),
                }],
            },
        },
    )
}

#[test]
fn undo_is_a_new_persisted_idempotent_revision_and_can_be_redone() {
    let temp = tempfile::tempdir().unwrap();
    let path = temp.path().join("undo.sqlite3");
    let service = StudioService::open(&path).unwrap();
    let (initial, node_id) = seed(&service);
    let patched = text_patch(&service, &initial, node_id, "A shorter sentence.");
    let record = patched.production_design.patches.last().unwrap();
    assert_eq!(record.source_revision, initial.revision);
    let preview = patched.preview_creative_patch_undo(record.id).unwrap();
    assert_eq!(preview.after[0].text, record.before[0].text);
    assert_eq!(service.project(patched.id).unwrap(), patched);
    let change = Change::UndoCreativePatch {
        patch_id: record.id,
    };
    let outcome = service
        .apply(patched.id, &patched.stamp(), "undo-one", &change)
        .unwrap();
    let restored = &outcome.project;
    assert_eq!(restored.revision, patched.revision + 1);
    assert_eq!(restored.generation, initial.generation);
    assert_eq!(restored.scenes[0].nodes, initial.scenes[0].nodes);
    assert_eq!(restored.production_design.patches.len(), 2);
    assert_eq!(
        restored.production_design.patches[1].reverts,
        Some(record.id)
    );
    assert!(
        service
            .apply(patched.id, &patched.stamp(), "undo-one", &change)
            .unwrap()
            .replayed
    );
    assert!(restored.preview_creative_patch_undo(record.id).is_err());
    let redone = apply(
        &service,
        restored,
        Change::UndoCreativePatch {
            patch_id: restored.production_design.patches[1].id,
        },
    );
    assert_eq!(redone.scenes[0].nodes, patched.scenes[0].nodes);
    assert_eq!(redone.revision, patched.revision + 2);
    drop(service);
    assert_eq!(
        StudioService::open(&path)
            .unwrap()
            .project(redone.id)
            .unwrap(),
        redone
    );
}

#[test]
fn compatible_human_style_and_position_survive_undo_of_a_text_patch() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("undo.sqlite3")).unwrap();
    let (initial, node_id) = seed(&service);
    let mut project = text_patch(&service, &initial, node_id, "New copy");
    let patch_id = project.production_design.patches[0].id;
    let mut style = project.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == node_id)
        .unwrap()
        .style
        .clone();
    style.fill = Some("#FFEEDD".into());
    project = apply(
        &service,
        &project,
        Change::UpdateCanvasStyle {
            scene_id: project.scenes[0].id,
            node_id,
            style: style.clone(),
        },
    );
    let node = project.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == node_id)
        .unwrap();
    let transform = CanvasTransform {
        x: node.x + 12.0,
        y: node.y,
        width: node.width,
        height: node.height,
        rotation_deg: node.rotation_deg,
        opacity: node.opacity,
    };
    project = apply(
        &service,
        &project,
        Change::TransformCanvasNode {
            scene_id: project.scenes[0].id,
            node_id,
            transform,
        },
    );
    let restored = apply(&service, &project, Change::UndoCreativePatch { patch_id });
    let node = restored.scenes[0]
        .nodes
        .iter()
        .find(|node| node.id == node_id)
        .unwrap();
    assert_eq!(
        node.text,
        initial.scenes[0]
            .nodes
            .iter()
            .find(|node| node.id == node_id)
            .unwrap()
            .text
    );
    assert_eq!(node.style, style);
    assert_eq!(node.x, transform.x);
}

#[test]
fn undo_rejects_later_conflicting_copy_locks_missing_nodes_and_stale_authority() {
    for mode in ["copy", "lock", "delete", "stale"] {
        let temp = tempfile::tempdir().unwrap();
        let service = StudioService::open(temp.path().join("undo.sqlite3")).unwrap();
        let (initial, node_id) = seed(&service);
        let patched = text_patch(&service, &initial, node_id, "Patched copy");
        let patch_id = patched.production_design.patches[0].id;
        let change = match mode {
            "copy" => Change::UpdateCanvasText {
                scene_id: patched.scenes[0].id,
                node_id,
                text: Some("Later human decision".into()),
            },
            "lock" => Change::SetNodePropertyLock {
                scene_id: patched.scenes[0].id,
                node_id,
                property: NodeProperty::Text,
                locked: true,
            },
            "delete" => Change::RemoveCanvasNode {
                scene_id: patched.scenes[0].id,
                node_id,
            },
            _ => Change::RenameProject {
                title: "A newer observed revision".into(),
            },
        };
        let current = apply(&service, &patched, change);
        let stamp = if mode == "stale" {
            patched.stamp()
        } else {
            current.stamp()
        };
        assert!(
            service
                .apply(
                    current.id,
                    &stamp,
                    "cannot-overwrite-human",
                    &Change::UndoCreativePatch { patch_id }
                )
                .is_err(),
            "{mode}"
        );
        assert_eq!(service.project(current.id).unwrap(), current, "{mode}");
    }
}

#[test]
fn reversible_window_is_bounded_and_noop_or_forged_records_are_not_admitted() {
    let temp = tempfile::tempdir().unwrap();
    let service = StudioService::open(temp.path().join("undo.sqlite3")).unwrap();
    let (initial, node_id) = seed(&service);
    let mut project = text_patch(&service, &initial, node_id, "Revision 0");
    let first = project.production_design.patches[0].id;
    for index in 1..66 {
        project = text_patch(&service, &project, node_id, &format!("Revision {index}"));
    }
    assert_eq!(project.production_design.patches.len(), 64);
    assert!(project.preview_creative_patch_undo(first).is_err());
    let before = project.clone();
    let mut record = project.production_design.patches.last().unwrap().clone();
    record.after[0].parent_id = Some(Uuid::now_v7());
    assert!(record.validate().is_err());
    let noop = Change::ApplyCreativePatch {
        patch: CreativePatch {
            scene_id: project.scenes[0].id,
            base_revision: project.revision,
            rationale: "Unchanged proposal".into(),
            edits: vec![ScopedCanvasEdit::Text {
                node_id,
                text: "Revision 65".into(),
            }],
        },
    };
    assert!(
        service
            .apply(project.id, &project.stamp(), "noop", &noop)
            .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), before);
}
