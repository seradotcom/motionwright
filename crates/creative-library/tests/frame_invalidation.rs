use motionwright_creative_library::{self as craft, native, propose_native_frame_invalidation};
use uuid::Uuid;
fn rect(id: u128, alpha: f64, parent: Option<Uuid>) -> native::Node {
    native::Node {
        id: Uuid::from_u128(id),
        name: format!("Owner layer {id}"),
        parent_id: parent,
        pose: native::Pose {
            x: 65.0,
            y: 115.0,
            width: 225.0,
            height: 110.0,
            opacity: alpha,
            ..Default::default()
        },
        content: native::Content::Rectangle {
            fill: "#31556F".into(),
            stroke: None,
            stroke_width: 0.0,
            radius: 10.0,
        },
        blend: native::Blend::Normal,
        clip: native::Clip::None,
        effects: native::Effects::default(),
        keyframes: vec![],
        locked_properties: vec![],
        locked_fields: vec![],
    }
}
fn hold(frame: u32, opacity: f64) -> native::Keyframe {
    native::Keyframe {
        frame,
        subframe: None,
        property: native::Property::Opacity,
        value: opacity,
        curve: native::Curve::Hold,
    }
}
fn document() -> native::HyperframesDocument {
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
        nodes: vec![rect(1, 1.0, None), rect(2, 0.0, None)],
        assets: vec![],
    }
}
fn recolor(doc: &mut native::HyperframesDocument) {
    let native::Content::Rectangle { fill, .. } = &mut doc.nodes[1].content else {
        panic!("Expected rectangle")
    };
    *fill = "#EA5A48".into();
}
fn plan(
    doc: &native::HyperframesDocument,
    mutator: impl FnOnce(&mut native::HyperframesDocument),
) -> craft::NativeFrameInvalidation {
    let mut changed = doc.clone();
    mutator(&mut changed);
    propose_native_frame_invalidation(doc, &changed).unwrap()
}
#[test]
fn hidden_source_can_only_be_reused_as_unverified_pixel_custody() {
    let before = document();
    let result = plan(&before, recolor);
    assert_ne!(result.before_source_sha256, result.after_source_sha256);
    assert_eq!(result.dirty_frame_count, 0);
    assert_eq!(
        result.reusable_intervals,
        vec![craft::FrameInterval {
            start: 0,
            end_exclusive: 90
        }]
    );
    assert!(!result.observation_readback_reusable && !result.audio_samples_reusable);
    assert!(!result.encoder_output_reusable && !result.rendered_pixel_equivalence_verified);
    assert_eq!(result.actual_native_frames_avoided, 0);
    assert!(!result.owner_granted_execution);
}
#[test]
fn hold_visibility_changes_only_frames_where_new_paint_can_be_seen() {
    let mut before = document();
    before.nodes[1].keyframes = vec![hold(0, 0.0), hold(30, 1.0)];
    let result = plan(&before, recolor);
    assert_eq!(
        result.reusable_intervals,
        vec![craft::FrameInterval {
            start: 0,
            end_exclusive: 30
        }]
    );
    assert_eq!(
        result.dirty_intervals,
        vec![craft::FrameInterval {
            start: 30,
            end_exclusive: 90
        }]
    );
    assert_eq!(result.dirty_frame_count, 60);
}
#[test]
fn a_late_hide_can_reuse_the_remaining_tail_after_its_hold_key() {
    let mut before = document();
    before.nodes[1].pose.opacity = 1.0;
    before.nodes[1].keyframes = vec![hold(45, 0.0)];
    let result = plan(&before, recolor);
    assert_eq!(
        result.dirty_intervals,
        vec![craft::FrameInterval {
            start: 0,
            end_exclusive: 45
        }]
    );
    assert_eq!(
        result.reusable_intervals,
        vec![craft::FrameInterval {
            start: 45,
            end_exclusive: 90
        }]
    );
}
#[test]
fn rational_subframe_hold_cannot_be_applied_early() {
    let mut before = document();
    before.nodes[1].keyframes = vec![
        hold(0, 0.0),
        native::Keyframe {
            frame: 30,
            subframe: Some(native::Subframe { num: 1, den: 2 }),
            property: native::Property::Opacity,
            value: 1.0,
            curve: native::Curve::Hold,
        },
    ];
    let result = plan(&before, recolor);
    assert_eq!(
        result.reusable_intervals,
        vec![craft::FrameInterval {
            start: 0,
            end_exclusive: 31
        }]
    );
    assert_eq!(
        result.dirty_intervals,
        vec![craft::FrameInterval {
            start: 31,
            end_exclusive: 90
        }]
    );
}
#[test]
fn interpolated_opacity_forbids_source_only_partial_caching() {
    let mut before = document();
    before.nodes[1].keyframes = vec![
        hold(0, 0.0),
        native::Keyframe {
            frame: 30,
            subframe: None,
            property: native::Property::Opacity,
            value: 1.0,
            curve: native::Curve::Linear,
        },
    ];
    assert_eq!(plan(&before, recolor).dirty_frame_count, 90);
}
#[test]
fn global_camera_asset_canvas_and_node_order_changes_invalidate_everything() {
    let base = document();
    assert_eq!(
        plan(&base, |after| after.camera.x = 10.0).dirty_frame_count,
        90
    );
    assert_eq!(
        plan(&base, |after| after.nodes.swap(0, 1)).dirty_frame_count,
        90
    );
    assert_eq!(
        plan(&base, |after| after.canvas.background = None).dirty_frame_count,
        90
    );
    assert_eq!(
        plan(&base, |after| after.assets.push(native::Asset {
            id: Uuid::from_u128(50),
            sha256: "ab".repeat(32),
            kind: native::AssetKind::Png,
            rights: native::AssetRights {
                owner: "Owner".into(),
                license: "Original".into(),
                attribution: "Fixture".into(),
                use_authorized: true,
                redistribute: false
            }
        }))
        .dirty_frame_count,
        90
    );
}
#[test]
fn human_metadata_preserves_pixel_proposal_but_not_native_readback() {
    let result = plan(&document(), |after| {
        after.nodes[0]
            .locked_fields
            .push(native::NodeField::Content);
        after.nodes[1].name = "Owner renamed source".into();
    });
    assert_eq!(result.reusable_frame_count, 90);
    assert!(!result.observation_readback_reusable);
}
#[test]
fn a_changed_child_is_hidden_inside_an_unmodified_transparent_parent() {
    let mut before = document();
    before.nodes[1].pose.opacity = 1.0;
    let mut parent = rect(50, 0.0, None);
    parent.content = native::Content::Group;
    before.nodes.push(parent);
    before.nodes[1].parent_id = Some(Uuid::from_u128(50));
    assert_eq!(plan(&before, recolor).dirty_frame_count, 0);
}
#[test]
fn differing_hold_visibility_intervals_invalidate_the_union() {
    let mut before = document();
    before.nodes[1].keyframes = vec![hold(0, 0.0), hold(40, 1.0)];
    let result = plan(&before, |after| {
        after.nodes[1].keyframes[1].frame = 60;
        recolor(after);
    });
    assert_eq!(
        result.reusable_intervals,
        vec![craft::FrameInterval {
            start: 0,
            end_exclusive: 40
        }]
    );
    assert_eq!(
        result.dirty_intervals,
        vec![craft::FrameInterval {
            start: 40,
            end_exclusive: 90
        }]
    );
}
