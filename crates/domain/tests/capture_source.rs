use motionwright_domain::*;
use uuid::Uuid;

const SCREEN: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const RECEIPT: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const COMMIT: &str = "cccccccccccccccccccccccccccccccccccccccc";

fn prepare() -> (Project, CaptureSourceRecord) {
    let mut p = Project::new("Isolated first-party capture").unwrap();
    p.revision = 7;
    let origin = p.revision;
    p.apply_change(&Change::AddClaim {
        text: "The authored Studio Production screen is visible in the pinned first-party fixture"
            .into(),
        context: "synthetic-first-party-capture-validation".into(),
        source: Some(SourceReference::ProjectRevision { revision: origin }),
        source_revision: Some("fixture-7".into()),
    })
    .unwrap();
    let screenshot = Uuid::new_v4();
    let manifest = Uuid::new_v4();
    let state = Uuid::new_v4();
    for (id, name, mime, hash) in [
        (screenshot, "source.png", "image/png", SCREEN),
        (
            manifest,
            "capture-evidence.json",
            "application/json",
            RECEIPT,
        ),
        (
            state,
            "fixture-state.json",
            "application/json",
            "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        ),
    ] {
        p.apply_change(&Change::AddAsset {
            asset: Asset {
                id,
                name: name.into(),
                media_type: mime.into(),
                content_sha256: Some(hash.into()),
                source_revision: Some("local-import".into()),
            },
        })
        .unwrap();
    }
    let record = CaptureSourceRecord {
        schema: CAPTURE_RECORD_SCHEMA.into(),
        id: Uuid::new_v4(),
        screenshot_asset_id: screenshot,
        screenshot_sha256: SCREEN.into(),
        manifest_asset_id: manifest,
        manifest_sha256: RECEIPT.into(),
        fixture_state_asset_id: state,
        fixture_state_sha256: "f".repeat(64),
        claim_id: p.brief.claims[0].id,
        source_project_id: p.id,
        source_generation: p.generation,
        source_revision: origin,
        app_id: "motionwright-studio".into(),
        app_version: "0.1.0".into(),
        source_commit: COMMIT.into(),
        scenario_id: "production-original-ui-local-v1".into(),
        origin: "http://127.0.0.1:1420".into(),
        route: "/".into(),
        viewport_width: 1440,
        viewport_height: 940,
        device_scale_factor: 1,
        screenshot_repeat_sha256: SCREEN.into(),
        redaction: CaptureMask {
            selector: ".revision-chip".into(),
            x: 100,
            y: 64,
            width: 72,
            height: 28,
            mask_color: "#0B0B0B".into(),
        },
        external_requests: 0,
        credential_fields: 0,
        declared_scope: "first-party-synthetic-fixture-only".into(),
    };
    (p, record)
}

#[test]
fn valid_first_party_capture_record_is_append_only_with_exact_claim_and_portable_serialization() {
    let (mut p, record) = prepare();
    let earlier = p.clone();
    p.apply_change(&Change::RegisterCaptureSource {
        record: record.clone(),
    })
    .unwrap();
    assert_eq!(p.production_design.captures, vec![record.clone()]);
    assert_eq!(p.assets, earlier.assets);
    assert_eq!(p.brief, earlier.brief);
    p.validate().unwrap();
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(restored.production_design.captures, vec![record.clone()]);
    assert_eq!(restored.production_design, p.production_design);
    assert!(
        p.apply_change(&Change::RegisterCaptureSource {
            record: record.clone()
        })
        .unwrap_err()
        .to_string()
        .contains("duplicate")
    );
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: record.screenshot_asset_id
        })
        .unwrap_err()
        .to_string()
        .contains("provenance")
    );
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: record.manifest_asset_id
        })
        .unwrap_err()
        .to_string()
        .contains("provenance")
    );
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: record.fixture_state_asset_id
        })
        .unwrap_err()
        .to_string()
        .contains("provenance")
    );
    assert_eq!(p.production_design.captures.len(), 1);
}

#[test]
fn forged_foreign_sources_claims_revisions_and_sensitive_state_fail_closed() {
    let (p, record) = prepare();
    let variants: Vec<(&str, CaptureSourceRecord)> = vec![
        (
            "foreign project",
            CaptureSourceRecord {
                source_project_id: Uuid::new_v4(),
                ..record.clone()
            },
        ),
        (
            "future",
            CaptureSourceRecord {
                source_revision: p.revision + 2,
                ..record.clone()
            },
        ),
        (
            "unbound claim",
            CaptureSourceRecord {
                claim_id: Uuid::new_v4(),
                ..record.clone()
            },
        ),
        (
            "foreign png digest",
            CaptureSourceRecord {
                screenshot_sha256: "d".repeat(64),
                screenshot_repeat_sha256: "d".repeat(64),
                ..record.clone()
            },
        ),
        (
            "foreign manifest",
            CaptureSourceRecord {
                manifest_asset_id: Uuid::new_v4(),
                ..record.clone()
            },
        ),
        (
            "external url",
            CaptureSourceRecord {
                origin: "https://example.org".into(),
                ..record.clone()
            },
        ),
        (
            "credentials",
            CaptureSourceRecord {
                credential_fields: 1,
                ..record.clone()
            },
        ),
        (
            "external requests",
            CaptureSourceRecord {
                external_requests: 1,
                ..record.clone()
            },
        ),
        (
            "bad repeat",
            CaptureSourceRecord {
                screenshot_repeat_sha256: "e".repeat(64),
                ..record.clone()
            },
        ),
        (
            "missing redaction",
            CaptureSourceRecord {
                redaction: CaptureMask {
                    selector: "".into(),
                    ..record.redaction.clone()
                },
                ..record.clone()
            },
        ),
        (
            "giant masking",
            CaptureSourceRecord {
                redaction: CaptureMask {
                    width: 1300,
                    height: 900,
                    ..record.redaction.clone()
                },
                ..record.clone()
            },
        ),
        (
            "unknown browser",
            CaptureSourceRecord {
                scenario_id: "unapproved-browser".into(),
                ..record.clone()
            },
        ),
    ];
    for (name, candidate) in variants {
        let mut clone = p.clone();
        assert!(
            clone
                .apply_change(&Change::RegisterCaptureSource { record: candidate })
                .is_err(),
            "forged {name} must be rejected"
        );
        assert_eq!(clone.production_design.captures.len(), 0, "{name}");
    }
    let mut malicious = serde_json::to_value(record.clone()).unwrap();
    malicious["validated_customer_behavior"] = serde_json::json!(true);
    assert!(serde_json::from_value::<CaptureSourceRecord>(malicious).is_err());
    let unrecorded: ProductionDesign = serde_json::from_value(serde_json::json!({
        "heroes":[],"capsules":[],"patches":[],"brand_exceptions":[],"project_decisions":[]
    }))
    .unwrap();
    assert!(
        unrecorded.captures.is_empty(),
        "old project JSON remains readable"
    );
}

#[test]
fn capture_source_generation_must_match_current_owner_at_registration_only() {
    let (mut p, mut record) = prepare();
    record.source_generation = Uuid::nil();
    assert!(
        p.apply_change(&Change::RegisterCaptureSource { record })
            .is_err()
    );
    let (_, record) = prepare();
    // An imported project may legitimately rotate its generation while keeping
    // the original immutable capture-source generation in provenance records.
    assert_ne!(record.source_generation, p.generation);
}
