use hyperframes_profile as h;
use motionwright_domain::*;
use uuid::Uuid;
fn fixture() -> (Project, NativeSceneDocument) {
    let mut project = Project::new("Native creative source").unwrap();
    project
        .apply_change(&Change::AddScene {
            name: "Native shot".into(),
            objective: "Preserve a native composition without executing it".into(),
            duration_seconds: 3,
        })
        .unwrap();
    let mut profile = project.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    project
        .apply_change(&Change::UpsertDeliverable {
            profile: profile.clone(),
        })
        .unwrap();
    let document = h::HyperframesDocument {
        version: 1,
        canvas: h::Canvas {
            width: 640,
            height: 360,
            rate: h::FrameRate { num: 30, den: 1 },
            frames: 90,
            background: Some("#14202B".into()),
        },
        camera: h::Camera::default(),
        nodes: vec![h::Node {
            id: Uuid::new_v4(),
            name: "Protected product surface".into(),
            parent_id: None,
            pose: h::Pose {
                x: 40.0,
                y: 40.0,
                width: 400.0,
                height: 220.0,
                ..Default::default()
            },
            content: h::Content::Rectangle {
                fill: "#ABCDEF".into(),
                stroke: None,
                stroke_width: 0.0,
                radius: 12.0,
            },
            blend: h::Blend::Normal,
            clip: h::Clip::None,
            effects: h::Effects::default(),
            keyframes: vec![],
            locked_properties: vec![],
            locked_fields: vec![],
        }],
        assets: vec![],
    };
    let native = NativeSceneDocument {
        id: Uuid::new_v4(),
        scene_id: project.scenes[0].id,
        profile_id: profile.id,
        label: "Editable native shot".into(),
        source: NativeSceneSource::Hyperframes(document),
        source_capsule_id: None,
    };
    (project, native)
}
#[test]
fn source_can_be_attached_without_installing_a_renderer_or_changing_a_scene() {
    let (mut project, native) = fixture();
    let before = project.clone();
    let edit = CreativeWorkspaceEdit::UpsertNativeScene {
        scene: native.clone(),
        expected_source_sha256: None,
    };
    let preview = project.preview_creative_workspace(&edit).unwrap();
    assert_eq!(project, before);
    assert_eq!(preview.added_nodes.len(), 1);
    assert!(preview.source_sha256.is_none());
    project
        .apply_change(&Change::EditCreativeWorkspace { edit })
        .unwrap();
    assert_eq!(project.scenes[0].renderer, RendererKind::MotionCanvas);
    assert!(project.extensions.is_empty());
    assert_eq!(project.production_design.workspace.native_scenes[0], native);
    let decoded: Project = serde_json::from_slice(&serde_json::to_vec(&project).unwrap()).unwrap();
    decoded.validate().unwrap();
    assert_eq!(decoded, project);
}
#[test]
fn native_replacement_requires_observed_source_and_does_not_silently_unlock_human_work() {
    let (mut project, native) = fixture();
    project
        .apply_change(&Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: native.clone(),
                expected_source_sha256: None,
            },
        })
        .unwrap();
    let node = match &native.source {
        NativeSceneSource::Hyperframes(doc) => doc.nodes[0].id,
    };
    let digest = native.source.source_digest().unwrap();
    project
        .apply_change(&Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::SetNativeProtection {
                id: native.id,
                node_id: node,
                protection: NativeProtection::Property {
                    property: h::Property::X,
                },
                locked: true,
                expected_source_sha256: digest.clone(),
                rationale: "Keep the manually aligned start position".into(),
            },
        })
        .unwrap();
    let before = project.clone();
    let current = project.production_design.workspace.native_scenes[0].clone();
    let mut changed = current.clone();
    let NativeSceneSource::Hyperframes(doc) = &mut changed.source;
    doc.nodes[0].pose.x += 20.0;
    assert!(
        project
            .apply_change(&Change::EditCreativeWorkspace {
                edit: CreativeWorkspaceEdit::UpsertNativeScene {
                    scene: changed,
                    expected_source_sha256: Some(current.source.source_digest().unwrap())
                }
            })
            .is_err()
    );
    assert_eq!(project, before);
    assert!(
        project
            .apply_change(&Change::EditCreativeWorkspace {
                edit: CreativeWorkspaceEdit::UpsertNativeScene {
                    scene: native,
                    expected_source_sha256: Some(digest)
                }
            })
            .is_err()
    );
    assert_eq!(project, before);
}
#[test]
fn native_content_protection_is_separate_from_source_edit_and_runtime_authority() {
    let (mut project, mut native) = fixture();
    let NativeSceneSource::Hyperframes(doc) = &mut native.source;
    doc.nodes[0].locked_fields = vec![h::NodeField::Content];
    project
        .apply_change(&Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: native.clone(),
                expected_source_sha256: None,
            },
        })
        .unwrap();
    let mut proposed = native.clone();
    let NativeSceneSource::Hyperframes(doc) = &mut proposed.source;
    doc.nodes[0].content = h::Content::Group;
    assert!(
        project
            .preview_creative_workspace(&CreativeWorkspaceEdit::UpsertNativeScene {
                scene: proposed,
                expected_source_sha256: Some(native.source.source_digest().unwrap())
            })
            .is_err()
    );
    assert!(
        project
            .apply_change(&Change::SetSceneRenderer {
                scene_id: native.scene_id,
                renderer: RendererKind::Hyperframes
            })
            .is_err()
    );
}
#[test]
fn editing_the_timeline_preserves_the_unmatched_native_source_for_explicit_reconciliation() {
    let (mut project, native) = fixture();
    project
        .apply_change(&Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: native.clone(),
                expected_source_sha256: None,
            },
        })
        .unwrap();
    project
        .apply_change(&Change::SetSceneDuration {
            scene_id: native.scene_id,
            duration: RationalTime::new(4, 1).unwrap(),
        })
        .unwrap();
    assert_eq!(project.production_design.workspace.native_scenes[0], native);
    project.validate().unwrap();
}
#[test]
fn prior_schema_reads_are_pure_but_renderer_native_state_cannot_be_mislabeled_as_v2() {
    let (mut project, native) = fixture();
    project.schema_version = 2;
    project.validate().unwrap();
    let before = project.clone();
    let edit = CreativeWorkspaceEdit::UpsertNativeScene {
        scene: native,
        expected_source_sha256: None,
    };
    project.preview_creative_workspace(&edit).unwrap();
    assert_eq!(project, before);
    project
        .apply_change(&Change::EditCreativeWorkspace { edit })
        .unwrap();
    assert_eq!(project.schema_version, PROJECT_SCHEMA_VERSION);
    project.schema_version = 2;
    assert!(project.validate().is_err());
}

#[test]
fn ten_consecutive_source_revisions_preserve_human_protection_and_reject_stale_agents() {
    let (mut project, native) = fixture();
    project
        .apply_change(&Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: native.clone(),
                expected_source_sha256: None,
            },
        })
        .unwrap();
    let node_id = match &native.source {
        NativeSceneSource::Hyperframes(doc) => doc.nodes[0].id,
    };
    let initial = project.production_design.workspace.native_scenes[0]
        .source
        .source_digest()
        .unwrap();
    project
        .apply_change(&Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::SetNativeProtection {
                id: native.id,
                node_id,
                protection: NativeProtection::Field {
                    field: h::NodeField::Content,
                },
                locked: true,
                expected_source_sha256: initial,
                rationale: "Owner-protected original visual content across ten agent revisions"
                    .into(),
            },
        })
        .unwrap();
    let protected = project.production_design.workspace.native_scenes[0].clone();
    let start_revision = project.revision;
    for iteration in 0..10 {
        let before = project.clone();
        let current = before.production_design.workspace.native_scenes[0].clone();
        let expected = current.source.source_digest().unwrap();
        let mut proposed = current.clone();
        let NativeSceneSource::Hyperframes(doc) = &mut proposed.source;
        doc.nodes[0].pose.y = 41.0 + iteration as f64 * 3.0;
        doc.nodes[0].effects.blur = iteration as f64 / 4.0;
        let change = Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: proposed.clone(),
                expected_source_sha256: Some(expected.clone()),
            },
        };
        let diff = project
            .preview_creative_workspace(match &change {
                Change::EditCreativeWorkspace { edit } => edit,
                _ => unreachable!("only one native change"),
            })
            .unwrap();
        assert_eq!(project, before, "Preview must remain read-only");
        assert_eq!(diff.changed_nodes, vec![node_id]);
        assert_eq!(diff.source_sha256, Some(expected.clone()));
        project.apply_change(&change).unwrap();
        assert!(
            project.revision > before.revision,
            "Agent commit must create a revision"
        );
        assert_eq!(
            project.production_design.workspace.native_scenes[0],
            proposed
        );
        // Different clients may retain an old digest. It cannot be applied later,
        // even when the proposed scene happens to be structurally equivalent.
        let stale = Change::EditCreativeWorkspace {
            edit: CreativeWorkspaceEdit::UpsertNativeScene {
                scene: proposed.clone(),
                expected_source_sha256: Some(expected),
            },
        };
        assert!(
            project.apply_change(&stale).is_err(),
            "stale revision {iteration} was accepted"
        );
        assert_eq!(
            project.production_design.workspace.native_scenes[0],
            proposed
        );
        let decoded: Project =
            serde_json::from_slice(&serde_json::to_vec(&project).unwrap()).unwrap();
        decoded.validate().unwrap();
        assert_eq!(
            decoded.production_design.workspace.native_scenes[0],
            proposed
        );
    }
    assert!(project.revision >= start_revision + 10);
    let current = project.production_design.workspace.native_scenes[0].clone();
    let mut forbidden = current.clone();
    let NativeSceneSource::Hyperframes(document) = &mut forbidden.source;
    document.nodes[0].content = h::Content::Group;
    let before = project.clone();
    assert!(
        project
            .apply_change(&Change::EditCreativeWorkspace {
                edit: CreativeWorkspaceEdit::UpsertNativeScene {
                    scene: forbidden,
                    expected_source_sha256: Some(current.source.source_digest().unwrap())
                }
            })
            .is_err()
    );
    assert_eq!(project, before);
    let NativeSceneSource::Hyperframes(doc) = &current.source;
    assert_eq!(doc.nodes[0].locked_fields, vec![h::NodeField::Content]);
    assert_eq!(
        doc.nodes[0].content,
        match protected.source {
            NativeSceneSource::Hyperframes(ref doc) => doc.nodes[0].content.clone(),
        }
    );
}
