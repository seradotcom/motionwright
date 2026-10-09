//! Ten durable authoring revisions over the same canonical StudioService.
use motionwright_domain::hyperframes_profile as h;
use motionwright_domain::{
    self as d, Change, CreativeWorkspaceEdit, NativeProtection, NativeSceneDocument,
    NativeSceneSource, RevisionStamp,
};
use motionwright_service::StudioService;
use uuid::Uuid;

fn apply(service: &StudioService, project: &d::Project, key: &str, change: Change) -> d::Project {
    service
        .apply(project.id, &RevisionStamp::from(project), key, &change)
        .unwrap()
        .project
}
fn native(doc_id: Uuid, scene_id: Uuid, profile_id: Uuid, node_id: Uuid) -> NativeSceneDocument {
    NativeSceneDocument {
        id: doc_id,
        scene_id,
        profile_id,
        label: "Owner-modifiable native artwork".into(),
        source: NativeSceneSource::Hyperframes(h::HyperframesDocument {
            version: 1,
            canvas: h::Canvas {
                width: 640,
                height: 360,
                rate: h::FrameRate { num: 30, den: 1 },
                frames: 90,
                background: Some("#111922".into()),
            },
            camera: h::Camera::default(),
            nodes: vec![h::Node {
                id: node_id,
                name: "Protected human-created hero".into(),
                parent_id: None,
                pose: h::Pose {
                    x: 20.0,
                    y: 40.0,
                    width: 320.0,
                    height: 180.0,
                    ..Default::default()
                },
                content: h::Content::Rectangle {
                    fill: "#80B8CF".into(),
                    stroke: None,
                    stroke_width: 0.0,
                    radius: 12.0,
                },
                blend: h::Blend::Normal,
                clip: h::Clip::None,
                effects: h::Effects::default(),
                keyframes: vec![],
                locked_fields: vec![],
                locked_properties: vec![],
            }],
            assets: vec![],
        }),
        source_capsule_id: None,
    }
}
#[test]
fn ten_distinct_agent_edits_are_durable_and_cannot_undo_a_human_lock() {
    let temp = tempfile::tempdir().unwrap();
    let db = temp.path().join("native-revisions.sqlite3");
    let service = StudioService::open(&db).unwrap();
    let mut project = service.create_project("Ten preserved revisions").unwrap();
    project = apply(
        &service,
        &project,
        "native-sequence:scene",
        Change::AddScene {
            name: "One source scene".into(),
            objective: "Retain a human's original creative work".into(),
            duration_seconds: 3,
        },
    );
    let scene = project.scenes[0].id;
    let mut profile = project.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    project = apply(
        &service,
        &project,
        "native-sequence:output",
        Change::UpsertDeliverable {
            profile: profile.clone(),
        },
    );
    let node_id = Uuid::from_u128(100);
    let doc_id = Uuid::from_u128(101);
    let document = native(doc_id, scene, profile.id, node_id);
    project = apply(
        &service,
        &project,
        "native-sequence:attach",
        Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: document.clone(),
                expected_source_sha256: None,
            },
        },
    );
    project = apply(
        &service,
        &project,
        "native-sequence:human-protection",
        Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::SetNativeProtection {
                id: doc_id,
                node_id,
                protection: NativeProtection::Field {
                    field: h::NodeField::Content,
                },
                locked: true,
                expected_source_sha256: document.source.source_digest().unwrap(),
                rationale: "Retain approved original message and paint across agent revisions"
                    .into(),
            },
        },
    );
    let start_revision = project.revision;
    for iteration in 0..10_u64 {
        let previous = project.clone();
        let current = previous.production_design.workspace.native_scenes[0].clone();
        let source_sha = current.source.source_digest().unwrap();
        let mut proposed = current.clone();
        let NativeSceneSource::Hyperframes(source) = &mut proposed.source;
        source.nodes[0].pose.y = 52.0 + (iteration as f64 * 8.0);
        source.nodes[0].effects.blur = (iteration as f64 + 1.0) / 5.0;
        let key = format!("native-sequence:agent:{iteration}");
        project = apply(
            &service,
            &project,
            &key,
            Change::EditCreativeWorkspace {
                edit: CreativeWorkspaceEdit::UpsertNativeScene {
                    scene: proposed.clone(),
                    expected_source_sha256: Some(source_sha.clone()),
                },
            },
        );
        assert_eq!(
            project.revision,
            previous.revision + 1,
            "Each accepted edit is one revision"
        );
        assert_eq!(
            project.production_design.workspace.native_scenes[0],
            proposed
        );
        // An independent client with an older project stamp must be rejected.
        let stale = service.apply(
            previous.id,
            &RevisionStamp::from(&previous),
            &format!("native-sequence:stale:{iteration}"),
            &Change::EditCreativeWorkspace {
                edit: CreativeWorkspaceEdit::UpsertNativeScene {
                    scene: proposed.clone(),
                    expected_source_sha256: Some(source_sha),
                },
            },
        );
        assert!(
            stale.is_err(),
            "Stale concurrent client was able to replace current source"
        );
        // Reopening the same SQLite database is independent of this process's
        // in-memory project. Use a fresh service session on each iteration.
        let reopened = StudioService::open(&db)
            .unwrap()
            .project(project.id)
            .unwrap();
        assert_eq!(reopened.revision, project.revision);
        assert_eq!(
            reopened.production_design.workspace.native_scenes[0],
            proposed
        );
        reopened.validate().unwrap();
    }
    assert_eq!(project.revision, start_revision + 10);
    let current = project.production_design.workspace.native_scenes[0].clone();
    let NativeSceneSource::Hyperframes(source) = &current.source;
    assert_eq!(source.nodes[0].locked_fields, vec![h::NodeField::Content]);
    let mut overwritten = current.clone();
    let NativeSceneSource::Hyperframes(next) = &mut overwritten.source;
    next.nodes[0].content = h::Content::Group;
    let rejected = service.apply(
        project.id,
        &project.stamp(),
        "native-sequence:unauthorized-human-override",
        &Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: overwritten,
                expected_source_sha256: Some(current.source.source_digest().unwrap()),
            },
        },
    );
    assert!(rejected.is_err());
    let final_project = service.project(project.id).unwrap();
    assert_eq!(final_project.revision, project.revision);
    assert_eq!(
        final_project.production_design.workspace.native_scenes[0],
        current
    );
}
