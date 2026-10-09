use motionwright_hyperframes_profile::*;
use uuid::Uuid;
fn document() -> HyperframesDocument {
    HyperframesDocument {
        version: 1,
        canvas: Canvas {
            width: 640,
            height: 360,
            rate: FrameRate { num: 30, den: 1 },
            frames: 90,
            background: Some("#111922".into()),
        },
        camera: Camera::default(),
        nodes: vec![Node {
            id: Uuid::from_u128(1),
            name: "Authored text".into(),
            parent_id: None,
            pose: Pose {
                x: 40.0,
                y: 40.0,
                width: 500.0,
                height: 80.0,
                ..Default::default()
            },
            content: Content::Text {
                runs: vec![TextRun {
                    text: "Original composition".into(),
                    color: "#FFFFFF".into(),
                    weight: 600,
                    italic: false,
                }],
                font: Font::Sans,
                size: 40.0,
                line_height: 1.2,
                align: TextAlign::Left,
            },
            blend: Blend::Normal,
            clip: Clip::None,
            effects: Effects::default(),
            keyframes: vec![],
            locked_properties: vec![],
        }],
        assets: vec![],
    }
}
#[test]
fn profile_roundtrip_is_exact_and_source_is_stable() {
    let doc = document();
    let json = serde_json::to_vec(&doc).unwrap();
    let back: HyperframesDocument = serde_json::from_slice(&json).unwrap();
    assert_eq!(doc, back);
    assert_eq!(source_digest(&doc).unwrap(), source_digest(&back).unwrap());
    assert!(
        compile_html(&doc)
            .unwrap()
            .contains("/runtime/hyperframes.js")
    );
}
#[test]
fn text_is_inert_in_html_and_never_becomes_a_program() {
    let mut doc = document();
    if let Content::Text { runs, .. } = &mut doc.nodes[0].content {
        runs[0].text = "</script><script>throw new Error('not code')</script>&".into();
    }
    let html = compile_html(&doc).unwrap();
    assert!(!html.contains("<script>throw"));
    assert!(html.contains("\\u003c/script\\u003e"));
    assert!(html.contains("connect-src 'none'"));
}
#[test]
fn only_native_numeric_properties_are_admitted() {
    let mut doc = document();
    doc.nodes[0].keyframes = vec![
        Keyframe {
            frame: 0,
            property: Property::Rotation,
            value: -12.0,
            curve: Curve::Hold,
        },
        Keyframe {
            frame: 30,
            property: Property::Rotation,
            value: 0.0,
            curve: Curve::CubicBezier {
                x1: 0.2,
                y1: 0.0,
                x2: 0.8,
                y2: 1.0,
            },
        },
    ];
    validate_document(&doc).unwrap();
    doc.nodes[0].keyframes[1].value = f64::NAN;
    assert!(validate_document(&doc).is_err());
}
#[test]
fn duplicate_channels_and_out_of_scope_frames_are_not_coerced() {
    let mut doc = document();
    let key = Keyframe {
        frame: 0,
        property: Property::Opacity,
        value: 0.0,
        curve: Curve::Hold,
    };
    doc.nodes[0].keyframes = vec![key.clone(), key];
    assert!(validate_document(&doc).is_err());
    doc.nodes[0].keyframes.truncate(1);
    doc.nodes[0].keyframes[0].frame = 90;
    assert!(validate_document(&doc).is_err());
}
#[test]
fn masks_and_effects_have_explicit_unambiguous_support() {
    let mut doc = document();
    doc.nodes[0].keyframes.push(Keyframe {
        frame: 30,
        property: Property::ClipRight,
        value: 30.0,
        curve: Curve::Linear,
    });
    assert!(validate_document(&doc).is_err());
    doc.nodes[0].clip = Clip::Inset {
        top: 0.0,
        right: 0.0,
        bottom: 0.0,
        left: 0.0,
        radius: 0.0,
    };
    validate_document(&doc).unwrap();
    doc.nodes[0].effects.blur = 65.0;
    assert!(validate_document(&doc).is_err());
}
#[test]
fn cyclic_or_unbounded_hierarchies_fail_closed() {
    let mut doc = document();
    doc.nodes[0].parent_id = Some(doc.nodes[0].id);
    assert!(validate_document(&doc).is_err());
    doc.nodes[0].parent_id = Some(Uuid::from_u128(33));
    assert!(validate_document(&doc).is_err());
}
#[test]
fn sources_and_network_locators_are_not_profile_fields() {
    let mut json = serde_json::to_value(document()).unwrap();
    json["source_program"] = serde_json::json!("unsafe");
    assert!(serde_json::from_value::<HyperframesDocument>(json).is_err());
    let mut doc = document();
    doc.canvas.background = Some("url(https://example.invalid)".into());
    assert!(compile_html(&doc).is_err());
}
#[test]
fn asset_identity_is_not_usage_authorization() {
    let mut doc = document();
    let id = Uuid::from_u128(10);
    doc.nodes[0].content = Content::Image {
        asset_id: id,
        fit: Fit::Contain,
    };
    assert!(validate_document(&doc).is_err());
    doc.assets.push(Asset {
        id,
        sha256: "ab".repeat(32),
        kind: AssetKind::Png,
        rights: AssetRights {
            owner: "Owner".into(),
            license: "Owned original".into(),
            attribution: String::new(),
            use_authorized: false,
            redistribute: false,
        },
    });
    assert!(validate_document(&doc).is_err());
    doc.assets[0].rights.use_authorized = true;
    validate_document(&doc).unwrap();
    doc.assets[0].kind = AssetKind::Woff2;
    assert!(validate_document(&doc).is_err());
}
#[test]
fn alpha_and_fractional_rates_remain_explicit() {
    let mut doc = document();
    doc.canvas.background = None;
    doc.canvas.rate = FrameRate {
        num: 30000,
        den: 1001,
    };
    validate_document(&doc).unwrap();
    assert!(
        compile_html(&doc)
            .unwrap()
            .contains("background:transparent")
    );
    assert!((doc.canvas.rate.seconds(30) - 1.001).abs() < 1e-12);
    doc.canvas.rate.den = 0;
    assert!(validate_document(&doc).is_err());
}
#[test]
fn exact_work_budgets_prevent_unbounded_native_jobs() {
    let mut doc = document();
    doc.canvas.frames = 3601;
    assert!(validate_document(&doc).is_err());
    doc.canvas.frames = 3600;
    doc.canvas.width = 4096;
    doc.canvas.height = 4096;
    assert!(validate_document(&doc).is_err());
}
