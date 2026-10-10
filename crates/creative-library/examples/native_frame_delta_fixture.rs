//! Retained original HTML source pair for a real 90-frame native PNG
//! incremental-reassembly oracle. Uses only first-party typed data.
use motionwright_creative_library::{native, propose_native_frame_invalidation};
use serde_json::json;
use uuid::Uuid;
fn node(id: u128, name: &str, content: native::Content, pose: native::Pose) -> native::Node {
    native::Node {
        id: Uuid::from_u128(id),
        name: name.into(),
        parent_id: None,
        pose,
        content,
        blend: native::Blend::Normal,
        clip: native::Clip::None,
        effects: native::Effects::default(),
        keyframes: vec![],
        locked_fields: vec![],
        locked_properties: vec![],
    }
}
fn document() -> native::HyperframesDocument {
    let title = node(
        11,
        "Original retained statement",
        native::Content::Text {
            runs: vec![native::TextRun {
                text: "Source remains editable.".into(),
                color: "#F2F4F3".into(),
                weight: 600,
                italic: false,
            }],
            font: native::Font::Sans,
            size: 34.0,
            line_height: 1.15,
            align: native::TextAlign::Left,
        },
        native::Pose {
            x: 42.0,
            y: 50.0,
            width: 510.0,
            height: 85.0,
            ..Default::default()
        },
    );
    let mut panel = node(
        12,
        "Owner-edited delayed original panel",
        native::Content::Rectangle {
            fill: "#31556F".into(),
            stroke: Some("#90C9DF".into()),
            stroke_width: 2.0,
            radius: 15.0,
        },
        native::Pose {
            x: 170.0,
            y: 175.0,
            width: 310.0,
            height: 130.0,
            opacity: 0.0,
            ..Default::default()
        },
    );
    panel.keyframes = [
        native::Keyframe {
            frame: 0,
            subframe: None,
            property: native::Property::Opacity,
            value: 0.0,
            curve: native::Curve::Hold,
        },
        native::Keyframe {
            frame: 30,
            subframe: None,
            property: native::Property::Opacity,
            value: 1.0,
            curve: native::Curve::Hold,
        },
    ]
    .to_vec();
    native::HyperframesDocument {
        version: 1,
        canvas: native::Canvas {
            width: 640,
            height: 360,
            rate: native::FrameRate { num: 30, den: 1 },
            frames: 90,
            background: Some("#111922".into()),
        },
        camera: native::Camera::default(),
        nodes: vec![title, panel],
        assets: vec![],
    }
}
fn sample(
    document: native::HyperframesDocument,
    revision: u64,
    runtime_sha: &str,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let plan = native::HyperframesPlan {
        runtime_receipt_sha256: runtime_sha.to_owned(),
        project_id: Uuid::from_u128(1),
        generation: Uuid::from_u128(2),
        revision,
        scene_id: Uuid::from_u128(3),
        document,
    };
    native::validate_plan(&plan)?;
    let original_html = native::compile_html(&plan.document)?;
    Ok(json!({
        "plan_json":serde_json::to_string(&plan)?,
        "source_html":original_html,
        "source_sha256":native::source_digest(&plan.document)?,
    }))
}
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let sha = std::env::args()
        .nth(1)
        .ok_or("Expected exact runtime receipt SHA")?;
    if sha.len() != 64
        || !sha
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("Invalid pinned native runtime SHA-256".into());
    }
    let before = document();
    let mut after = before.clone();
    let native::Content::Rectangle { fill, .. } = &mut after.nodes[1].content else {
        return Err("Expected original rectangle".into());
    };
    *fill = "#D86943".into();
    let audit = propose_native_frame_invalidation(&before, &after)?;
    println!(
        "{}",
        json!({
            "schema":"motionwright.original-native-delta-e2e/1",
            "before":sample(before,7,&sha)?,
            "after":sample(after,8,&sha)?,
            "source_invalidation":audit,
            "comparison":"not_yet_renderer_proven",
            "human_creative_approval":false
        })
    );
    Ok(())
}
