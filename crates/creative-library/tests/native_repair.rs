use motionwright_creative_library::{
    NativeRepairOperation, PoseRepairProperty, native, propose_native_repair,
};
use uuid::Uuid;
fn fixture() -> native::HyperframesDocument {
    native::HyperframesDocument {
        version: 1,
        canvas: native::Canvas {
            width: 640,
            height: 360,
            frames: 90,
            rate: native::FrameRate { num: 30, den: 1 },
            background: Some("#111922".into()),
        },
        camera: native::Camera::default(),
        assets: vec![],
        nodes: vec![
            native::Node {
                id: Uuid::from_u128(1),
                name: "Owner-adjusted caption".into(),
                parent_id: None,
                pose: native::Pose {
                    x: 30.0,
                    y: 100.0,
                    width: 450.0,
                    height: 130.0,
                    ..Default::default()
                },
                content: native::Content::Text {
                    runs: vec![native::TextRun {
                        text: "Human-approved original copy".into(),
                        color: "#F2F4F3".into(),
                        weight: 600,
                        italic: false,
                    }],
                    font: native::Font::Sans,
                    size: 28.0,
                    line_height: 1.2,
                    align: native::TextAlign::Left,
                },
                blend: native::Blend::Normal,
                clip: native::Clip::None,
                effects: native::Effects::default(),
                keyframes: vec![
                    native::Keyframe {
                        frame: 0,
                        subframe: None,
                        property: native::Property::Opacity,
                        value: 0.0,
                        curve: native::Curve::Hold,
                    },
                    native::Keyframe {
                        frame: 15,
                        subframe: Some(native::Subframe { num: 1, den: 2 }),
                        property: native::Property::Opacity,
                        value: 1.0,
                        curve: native::Curve::CubicBezier {
                            x1: 0.22,
                            y1: 0.0,
                            x2: 0.36,
                            y2: 1.0,
                        },
                    },
                ],
                locked_fields: vec![],
                locked_properties: vec![],
            },
            native::Node {
                id: Uuid::from_u128(2),
                name: "Original product evidence panel".into(),
                parent_id: None,
                pose: native::Pose {
                    x: 380.0,
                    y: 120.0,
                    width: 180.0,
                    height: 120.0,
                    ..Default::default()
                },
                content: native::Content::Rectangle {
                    fill: "#246580".into(),
                    stroke: None,
                    stroke_width: 0.0,
                    radius: 9.0,
                },
                blend: native::Blend::Normal,
                clip: native::Clip::None,
                effects: native::Effects::default(),
                keyframes: vec![],
                locked_fields: vec![native::NodeField::Content],
                locked_properties: vec![native::Property::X],
            },
        ],
    }
}
#[test]
fn one_bounded_source_repair_changes_only_the_requested_node_and_retains_native_properties() {
    let before = fixture();
    native::validate_document(&before).unwrap();
    let sha = native::source_digest(&before).unwrap();
    let repair = propose_native_repair(
        &before,
        &sha,
        "Preserve copy while adjusting legibility",
        &[NativeRepairOperation::SetPose {
            node_id: Uuid::from_u128(1),
            property: PoseRepairProperty::Y,
            expected: 100.0,
            next: 104.0,
        }],
    )
    .unwrap();
    assert_eq!(repair.schema, "motionwright.native-repair-proposal/1");
    assert_eq!(repair.changed_nodes, vec![Uuid::from_u128(1)]);
    assert_eq!(repair.document.nodes[1], before.nodes[1]);
    assert_eq!(repair.document.nodes[0].pose.y, 104.0);
    assert_eq!(repair.document.nodes[0].content, before.nodes[0].content);
    assert_eq!(
        repair.document.nodes[0].keyframes,
        before.nodes[0].keyframes
    );
    assert_eq!(repair.dirty_first_frame, 0);
    assert_eq!(repair.dirty_end_frame_exclusive, 90);
    assert!(!repair.committed && !repair.runtime_executed && !repair.renderer_equivalence_checked);
    assert_eq!(repair.creative_approval, "human_approval_required");
    assert_eq!(native::source_digest(&before).unwrap(), sha);
    assert_ne!(repair.proposed_source_sha256, sha);
}
#[test]
fn a_human_protected_object_remains_uneditable_by_programmatic_repair() {
    let doc = fixture();
    let sha = native::source_digest(&doc).unwrap();
    assert!(
        propose_native_repair(
            &doc,
            &sha,
            "No override of owner's X lock",
            &[NativeRepairOperation::SetPose {
                node_id: Uuid::from_u128(2),
                property: PoseRepairProperty::X,
                expected: 380.0,
                next: 390.0
            }]
        )
        .is_err()
    );
    let mut protected = doc.clone();
    protected.nodes[0]
        .locked_fields
        .push(native::NodeField::Content);
    let digest = native::source_digest(&protected).unwrap();
    assert!(
        propose_native_repair(
            &protected,
            &digest,
            "Do not rewrite the owner's caption",
            &[NativeRepairOperation::ReplaceTextRun {
                node_id: Uuid::from_u128(1),
                run_index: 0,
                expected_text: "Human-approved original copy".into(),
                next_text: "Agent changed this".into()
            }]
        )
        .is_err()
    );
}
#[test]
fn exact_fractional_keyframes_are_retained_without_any_frame_rounding() {
    let doc = fixture();
    let sha = native::source_digest(&doc).unwrap();
    let repaired = propose_native_repair(
        &doc,
        &sha,
        "Change the exact fractional-opacity endpoint",
        &[NativeRepairOperation::SetKeyframeValue {
            node_id: Uuid::from_u128(1),
            property: native::Property::Opacity,
            frame: 15,
            subframe: Some(native::Subframe { num: 1, den: 2 }),
            expected: 1.0,
            next: 0.92,
        }],
    )
    .unwrap();
    assert_eq!(
        repaired.document.nodes[0].keyframes[1].subframe,
        Some(native::Subframe { num: 1, den: 2 })
    );
    assert_eq!(repaired.document.nodes[0].keyframes[1].value, 0.92);
    assert!(
        propose_native_repair(
            &doc,
            &sha,
            "A rounded keyframe is a different source",
            &[NativeRepairOperation::SetKeyframeValue {
                node_id: Uuid::from_u128(1),
                property: native::Property::Opacity,
                frame: 15,
                subframe: None,
                expected: 1.0,
                next: 0.92
            }]
        )
        .is_err()
    );
}
#[test]
fn up_to_three_atomic_edits_produce_a_single_read_only_source_proposal() {
    let doc = fixture();
    let sha = native::source_digest(&doc).unwrap();
    let changes = [
        NativeRepairOperation::ReplaceTextRun {
            node_id: Uuid::from_u128(1),
            run_index: 0,
            expected_text: "Human-approved original copy".into(),
            next_text: "An expressly reviewed revised caption".into(),
        },
        NativeRepairOperation::SetBlur {
            node_id: Uuid::from_u128(1),
            expected: 0.0,
            next: 2.0,
        },
        NativeRepairOperation::SetPose {
            node_id: Uuid::from_u128(1),
            property: PoseRepairProperty::Opacity,
            expected: 1.0,
            next: 0.95,
        },
    ];
    let proposed = propose_native_repair(
        &doc,
        &sha,
        "Localized and reviewable accessibility repair",
        &changes,
    )
    .unwrap();
    assert_eq!(proposed.changed_properties.len(), 3);
    assert_eq!(proposed.document.nodes[1], doc.nodes[1]);
    assert_eq!(native::source_digest(&doc).unwrap(), sha);
    let mut duplicate = changes.to_vec();
    duplicate.push(changes[0].clone());
    assert!(propose_native_repair(&doc, &sha, "Unbounded four edits", &duplicate).is_err());
    assert!(
        propose_native_repair(
            &doc,
            &sha,
            "Duplicate key",
            &[changes[0].clone(), changes[0].clone()]
        )
        .is_err()
    );
}
#[test]
fn stale_preconditions_or_out_of_range_text_or_pose_are_never_coerced() {
    let doc = fixture();
    let sha = native::source_digest(&doc).unwrap();
    let op = NativeRepairOperation::SetPose {
        node_id: Uuid::from_u128(1),
        property: PoseRepairProperty::Y,
        expected: 100.0,
        next: 112.0,
    };
    assert!(
        propose_native_repair(
            &doc,
            &"a".repeat(64),
            "Stale agent proposal",
            std::slice::from_ref(&op)
        )
        .is_err()
    );
    assert!(
        propose_native_repair(
            &doc,
            &sha,
            "One stale value",
            &[NativeRepairOperation::SetPose {
                node_id: Uuid::from_u128(1),
                property: PoseRepairProperty::Y,
                expected: 99.0,
                next: 112.0
            }]
        )
        .is_err()
    );
    assert!(
        propose_native_repair(
            &doc,
            &sha,
            "Out of range source coordinate",
            &[NativeRepairOperation::SetPose {
                node_id: Uuid::from_u128(1),
                property: PoseRepairProperty::Y,
                expected: 100.0,
                next: f64::NAN
            }]
        )
        .is_err()
    );
    assert!(
        propose_native_repair(
            &doc,
            &sha,
            "Text needs owner review",
            &[NativeRepairOperation::ReplaceTextRun {
                node_id: Uuid::from_u128(1),
                run_index: 0,
                expected_text: "Stale caption".into(),
                next_text: "A changed caption".into()
            }]
        )
        .is_err()
    );
}
#[test]
fn arbitrary_or_unknown_repair_fields_cannot_create_new_source_execution_controls() {
    let json = serde_json::json!({
        "kind":"set_pose","node_id":Uuid::from_u128(1),
        "property":"x","expected":30.0,"next":32.0,"run_script":"rm -rf"
    });
    assert!(serde_json::from_value::<NativeRepairOperation>(json).is_err());
}
