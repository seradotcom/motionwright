use motionwright_hyperframes_profile::*;
use uuid::Uuid;
fn node(id: u128, name: &str, pose: Pose, content: Content) -> Node {
    Node {
        id: Uuid::from_u128(id),
        name: name.into(),
        parent_id: None,
        pose,
        content,
        blend: Blend::Normal,
        clip: Clip::None,
        effects: Effects::default(),
        keyframes: vec![],
        locked_properties: vec![],
        locked_fields: vec![],
    }
}
fn key(frame: u32, property: Property, value: f64, curve: Curve) -> Keyframe {
    Keyframe {
        frame,
        subframe: None,
        property,
        value,
        curve,
    }
}
fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let mode = std::env::args().nth(1).unwrap_or_else(|| "opaque".into());
    let transparent = mode == "alpha";
    let ntsc = mode == "ntsc";
    let rate = if ntsc {
        FrameRate {
            num: 30000,
            den: 1001,
        }
    } else {
        FrameRate { num: 30, den: 1 }
    };
    let title = node(
        10,
        "Editable rich title",
        Pose {
            x: 44.0,
            y: 30.0,
            width: 550.0,
            height: 105.0,
            ..Default::default()
        },
        Content::Text {
            runs: vec![
                TextRun {
                    text: "Native motion.\n".into(),
                    color: "#F2F4F3".into(),
                    weight: 600,
                    italic: false,
                },
                TextRun {
                    text: "Still editable.".into(),
                    color: "#90C9DF".into(),
                    weight: 500,
                    italic: false,
                },
            ],
            font: Font::Sans,
            size: 34.0,
            line_height: 1.15,
            align: TextAlign::Left,
        },
    );
    let mut card = node(
        20,
        "Masked rotating product card",
        Pose {
            x: 350.0,
            y: 172.0,
            width: 225.0,
            height: 125.0,
            rotation: 0.0,
            ..Default::default()
        },
        Content::Rectangle {
            fill: "#31556F".into(),
            stroke: Some("#90C9DF".into()),
            stroke_width: 2.0,
            radius: 14.0,
        },
    );
    card.clip = Clip::Inset {
        top: 0.0,
        right: 0.0,
        bottom: 0.0,
        left: 0.0,
        radius: 14.0,
    };
    card.keyframes = vec![
        key(0, Property::X, 405.0, Curve::Hold),
        key(30, Property::X, 350.0, Curve::EaseOutCubic),
        key(0, Property::Rotation, -12.0, Curve::Hold),
        key(
            30,
            Property::Rotation,
            0.0,
            Curve::CubicBezier {
                x1: 0.22,
                y1: 0.0,
                x2: 0.36,
                y2: 1.0,
            },
        ),
        key(0, Property::Opacity, 0.0, Curve::Hold),
        key(30, Property::Opacity, 1.0, Curve::Linear),
        key(0, Property::ClipRight, 100.0, Curve::Hold),
        key(30, Property::ClipRight, 0.0, Curve::EaseInOut),
        key(0, Property::ScaleX, 0.85, Curve::Hold),
        key(30, Property::ScaleX, 1.0, Curve::EaseOutCubic),
        key(0, Property::Blur, 6.0, Curve::Hold),
        key(30, Property::Blur, 0.0, Curve::EaseOutCubic),
    ];
    let graph = node(
        30,
        "Numeric causal connection",
        Pose {
            x: 45.0,
            y: 168.0,
            width: 260.0,
            height: 130.0,
            ..Default::default()
        },
        Content::Path {
            points: vec![
                Point { x: 0.0, y: 105.0 },
                Point { x: 65.0, y: 105.0 },
                Point { x: 65.0, y: 35.0 },
                Point { x: 195.0, y: 35.0 },
                Point { x: 195.0, y: 70.0 },
                Point { x: 250.0, y: 70.0 },
            ],
            closed: false,
            fill: None,
            stroke: "#90C9DF".into(),
            stroke_width: 3.0,
        },
    );
    let mut marker = node(
        40,
        "State marker",
        Pose {
            x: 95.0,
            y: 188.0,
            width: 30.0,
            height: 30.0,
            z_index: 10,
            ..Default::default()
        },
        Content::Ellipse {
            fill: "#F2F4F3".into(),
            stroke: None,
            stroke_width: 0.0,
        },
    );
    marker.blend = Blend::Screen;
    marker.keyframes = vec![
        key(0, Property::Y, 258.0, Curve::Hold),
        key(45, Property::Y, 188.0, Curve::EaseInOut),
    ];
    let doc = HyperframesDocument {
        version: 1,
        canvas: Canvas {
            width: 640,
            height: 360,
            rate,
            frames: 90,
            background: if transparent {
                None
            } else {
                Some("#111922".into())
            },
        },
        camera: Camera {
            keyframes: vec![
                key(0, Property::ScaleX, 0.97, Curve::Hold),
                key(60, Property::ScaleX, 1.0, Curve::EaseInOut),
            ],
            ..Default::default()
        },
        nodes: vec![title, card, graph, marker],
        assets: vec![],
    };
    let plan = HyperframesPlan {
        runtime_receipt_sha256: std::env::args().nth(2).unwrap_or_else(|| "0".repeat(64)),
        project_id: Uuid::from_u128(1),
        generation: Uuid::from_u128(2),
        revision: 7,
        scene_id: Uuid::from_u128(3),
        document: doc,
    };
    validate_plan(&plan)?;
    if mode == "html" {
        print!("{}", compile_html(&plan.document)?);
    } else {
        println!(
            "{}",
            serde_json::to_string_pretty(
                &serde_json::json!({"plan":plan,"source_sha256":source_digest(&plan.document)?,"source_html":compile_html(&plan.document)?,"plan_json":serde_json::to_string(&plan)?})
            )?
        );
    }
    Ok(())
}
