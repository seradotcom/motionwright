use motionwright_hyperframes_profile::*;
use uuid::Uuid;

fn document() -> SourceDocument {
    SourceDocument {
        clock: SourceClock {
            fps: Rational::new(30, 1).unwrap(),
            frame_count: 60,
        },
        width: 1280,
        height: 720,
        background: "#112233".into(),
        layers: vec![
            SourceLayer {
                id: Uuid::from_u128(1),
                start_frame: 0,
                end_frame: 30,
                width: 160,
                height: 80,
                transform: LayerTransform {
                    x: 100,
                    y: 120,
                    rotation_mdeg: 12_500,
                    opacity_milli: 750,
                    ..Default::default()
                },
                content: LayerContent::Rectangle {
                    color: "#E05020".into(),
                    radius: 8,
                },
            },
            SourceLayer {
                id: Uuid::from_u128(2),
                start_frame: 30,
                end_frame: 60,
                width: 220,
                height: 80,
                transform: LayerTransform {
                    x: 700,
                    y: 400,
                    ..Default::default()
                },
                content: LayerContent::Rectangle {
                    color: "#20B090".into(),
                    radius: 0,
                },
            },
        ],
    }
}

fn carrier() -> SourceCarrier {
    author_source(document(), vec![]).unwrap()
}

fn admit(value: &SourceCarrier) -> Result<AdmittedSource> {
    let bytes = serde_json::to_vec(value).unwrap();
    admit_source(&bytes, &sha256(&bytes))
}

#[test]
fn native_source_has_explicit_finite_timing_geometry_and_transforms_before_producer() {
    let value = carrier();
    assert!(value.html.contains("data-duration=\"2.000000000000\""));
    assert!(
        value
            .html
            .contains("data-width=\"1280\" data-height=\"720\"")
    );
    assert!(
        value
            .html
            .contains("data-start=\"1.000000000000\" data-duration=\"1.000000000000\"")
    );
    assert!(
        value
            .html
            .contains("translate(100px,120px) rotate(12.500deg) scale(1.000,1.000);opacity:0.750;")
    );
    assert!(!value.html.contains("<script"));
    assert!(!value.html.contains("http"));
    let source = admit(&value).unwrap();
    assert_eq!(source.readiness().capture_pixels, 55_296_000);
    assert_eq!(source.document(), &document());
    assert!(!source.readiness().native_render_verified);
    assert!(!source.readiness().editable_roundtrip_verified);
    assert!(!source.readiness().glyph_fidelity_verified);
}

#[test]
fn raw_source_export_preserves_bytes_and_reopen_revalidates_the_same_carrier() {
    let value = carrier();
    let mut original = serde_json::to_vec_pretty(&value).unwrap();
    original.extend_from_slice(b"\n\n");
    let expected = sha256(&original);
    let first = admit_source(&original, &expected).unwrap();
    assert_eq!(first.export_bytes(), original);
    let reopened = admit_source(first.export_bytes(), &expected).unwrap();
    assert_eq!(reopened.readiness(), first.readiness());
    assert_eq!(reopened.html(), first.html());
    assert_eq!(reopened.resources(), first.resources());
}

#[test]
fn admitted_source_edit_changes_content_fingerprint_but_is_not_a_native_roundtrip_pass() {
    let original = carrier();
    let first = admit(&original).unwrap();
    let mut edited_document = document();
    edited_document.layers[0].transform.rotation_mdeg = -33_000;
    edited_document.layers[0].transform.opacity_milli = 250;
    let edited = author_source(edited_document, vec![]).unwrap();
    let second = admit(&edited).unwrap();
    assert_ne!(
        first.readiness().carrier_sha256,
        second.readiness().carrier_sha256
    );
    assert_ne!(
        first.readiness().html_sha256,
        second.readiness().html_sha256
    );
    assert!(second.html().contains("rotate(-33.000deg)"));
    assert!(!second.readiness().editable_roundtrip_verified);
}

#[test]
fn caller_hash_or_metadata_does_not_authorize_an_html_substitution() {
    let mut value = carrier();
    value.html = value.html.replace("2.000000000000", "86400");
    assert!(matches!(admit(&value), Err(SourceError::Unsupported(_))));
    value = carrier();
    value.html = value.html.replace("#E05020", "#000000");
    assert!(matches!(admit(&value), Err(SourceError::Unsupported(_))));
    value = carrier();
    value
        .html
        .push_str("<script>fetch('https://example.invalid/')</script>");
    assert!(matches!(admit(&value), Err(SourceError::Unsupported(_))));
}

#[test]
fn same_ids_clock_and_layer_count_do_not_hide_an_intent_change() {
    let mut value = carrier();
    value.document.layers[0].transform.x += 1;
    assert!(matches!(admit(&value), Err(SourceError::Unsupported(_))));
    value = carrier();
    value.document.layers.swap(0, 1);
    assert!(matches!(admit(&value), Err(SourceError::Unsupported(_))));
}

#[test]
fn unknown_controls_are_rejected_instead_of_becoming_source_metadata() {
    let mut value = serde_json::to_value(carrier()).unwrap();
    for (field, data) in [
        ("camera", serde_json::json!({"x": 3})),
        ("audio", serde_json::json!("voice.wav")),
        ("keyframes", serde_json::json!([])),
        ("script", serde_json::json!("while(true){}")),
    ] {
        value["document"][field] = data;
        let bytes = serde_json::to_vec(&value).unwrap();
        assert!(matches!(
            admit_source(&bytes, &sha256(&bytes)),
            Err(SourceError::Invalid(_))
        ));
        value["document"].as_object_mut().unwrap().remove(field);
    }
}

#[test]
fn source_bounds_reject_excess_frames_duration_pixels_and_css_before_render() {
    for (width, height, frames) in [
        (1920, 1080, 60),
        (1280, 720, 181),
        (1921, 720, 1),
        (1280, 719, 1),
    ] {
        let mut doc = document();
        doc.width = width;
        doc.height = height;
        doc.clock.frame_count = frames;
        assert!(author_source(doc, vec![]).is_err());
    }
    let mut doc = document();
    doc.clock.fps = Rational::new(24, 1).unwrap();
    doc.clock.frame_count = 145;
    assert_eq!(
        doc.clock.validate(),
        Err(SourceError::Limit("six second duration"))
    );
    let mut doc = document();
    doc.background = "url(https://example.invalid/a.png)".into();
    assert!(author_source(doc, vec![]).is_err());
    let mut doc = document();
    doc.layers[0].transform.scale_x_milli = 0;
    assert!(author_source(doc, vec![]).is_err());
}

#[test]
fn rational_clock_remains_exact_and_does_not_coerce_to_integer_fps() {
    let clock = SourceClock {
        fps: Rational::new(30000, 1001).unwrap(),
        frame_count: 60,
    };
    assert_eq!(clock.duration().unwrap(), Rational::new(1001, 500).unwrap());
    let mut doc = document();
    doc.clock = clock;
    let value = author_source(doc, vec![]).unwrap();
    assert!(value.html.contains("data-duration=\"2.002000000000\""));
    assert_eq!(admit(&value).unwrap().readiness().clock, clock);
    assert!(
        SourceClock {
            fps: Rational { num: 60, den: 2 },
            frame_count: 60
        }
        .validate()
        .is_err()
    );
    assert!(
        SourceClock {
            fps: Rational::new(30, 1).unwrap(),
            frame_count: 0
        }
        .validate()
        .is_err()
    );
}

#[test]
fn layer_ranges_are_half_open_and_identity_is_unique() {
    let mut doc = document();
    doc.layers[0].start_frame = doc.layers[0].end_frame;
    assert!(author_source(doc, vec![]).is_err());
    let mut doc = document();
    doc.layers[0].end_frame = 61;
    assert!(author_source(doc, vec![]).is_err());
    let mut doc = document();
    doc.layers[1].id = doc.layers[0].id;
    assert!(author_source(doc, vec![]).is_err());
    let mut doc = document();
    doc.layers[0].id = Uuid::nil();
    assert!(author_source(doc, vec![]).is_err());
}

#[test]
fn missing_font_and_asset_resources_cannot_be_looked_up_on_the_host() {
    for content in [
        LayerContent::Text {
            text: "Bound source".into(),
            color: "#FFFFFF".into(),
            font_sha256: "a".repeat(64),
            font_size: 32,
        },
        LayerContent::Image {
            asset_sha256: "a".repeat(64),
        },
    ] {
        let mut doc = document();
        doc.layers[0].content = content;
        assert!(matches!(
            author_source(doc, vec![]),
            Err(SourceError::Binding(_))
        ));
    }
}

#[test]
fn resource_paths_are_derived_from_canonical_hashes_not_caller_paths() {
    let mut resource = SourceResource {
        kind: ResourceKind::Png,
        sha256: "a".repeat(64),
        bytes_hex: "00".into(),
    };
    assert_eq!(
        resource.relative_path().unwrap(),
        format!("assets/{}.png", "a".repeat(64))
    );
    for hash in [
        "../escape".into(),
        "A".repeat(64),
        "a".repeat(63),
        "file:///C:/a".into(),
    ] {
        resource.sha256 = hash;
        assert!(resource.relative_path().is_err());
    }
    let mut wire = serde_json::to_value(&resource).unwrap();
    wire["path"] = serde_json::json!("../escape");
    assert!(serde_json::from_value::<SourceResource>(wire).is_err());
}

#[test]
fn malformed_resources_and_wrong_carrier_bytes_are_rejected() {
    assert!(SourceResource::from_bytes(ResourceKind::Png, b"<script>alert(1)</script>").is_err());
    assert!(SourceResource::from_bytes(ResourceKind::Woff2, b"wOF2").is_err());
    let bytes = carrier_bytes(&carrier()).unwrap();
    assert_eq!(
        admit_source(&bytes, &"0".repeat(64)).unwrap_err(),
        SourceError::Binding("carrier digest")
    );
    let mut wrong_pin = carrier();
    wrong_pin.producer_revision = "0".repeat(40);
    assert!(matches!(
        admit(&wrong_pin),
        Err(SourceError::Unsupported(_))
    ));
    let mut tiny_source = carrier();
    tiny_source.html = "x".repeat(MAX_HTML_BYTES + 1);
    assert!(matches!(admit(&tiny_source), Err(SourceError::Limit(_))));
}
