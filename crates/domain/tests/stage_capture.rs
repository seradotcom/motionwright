use motionwright_domain::*;
use uuid::Uuid;

const PNG: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const MANIFEST: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const STATE: &str = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc";

fn attached() -> (Project, Uuid, Uuid, Uuid) {
    let mut p = Project::new("Owned original stage with source-bound tile").unwrap();
    p.apply_change(&Change::AddScene {
        name: "First-party original ProductStage".into(),
        objective: "Editable stage with one synthetic first-party capture card".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let stage_scene = p.scenes[0].id;
    let stage_id = Uuid::new_v4();
    p.apply_change(&Change::UpsertProductStage {
        instance_id: stage_id,
        scene_id: stage_scene,
        config: ProductStageConfig {
            author_attestation: "Original generic UI stage created by its author".into(),
            ..Default::default()
        },
    })
    .unwrap();
    let screenshot = Uuid::new_v4();
    let manifest = Uuid::new_v4();
    let fixture = Uuid::new_v4();
    for (id, name, media_type, hash) in [
        (screenshot, "synthetic-first-party.png", "image/png", PNG),
        (
            manifest,
            "source-capture.json",
            "application/json",
            MANIFEST,
        ),
        (fixture, "fixture-state.json", "application/json", STATE),
    ] {
        p.apply_change(&Change::AddAsset {
            asset: Asset {
                id,
                name: name.into(),
                media_type: media_type.into(),
                content_sha256: Some(hash.into()),
                source_revision: Some("synthetic unit fixture not pixel-verified".into()),
            },
        })
        .unwrap();
    }
    // A real capture references a previously committed, nonzero revision.
    // Domain apply_change does not advance the outer SQLite CAS revision;
    // seed an explicit synthetic historical revision like capture_source.rs.
    p.revision = 7;
    let observed = p.revision;
    p.apply_change(&Change::AddClaim {
        text: "The original Studio Production workstation was visible in a test-only browser."
            .into(),
        source: Some(SourceReference::ProjectRevision { revision: observed }),
        context: "synthetic-first-party-capture-validation".into(),
        source_revision: Some("fixture-1".into()),
    })
    .unwrap();
    let capture_id = Uuid::new_v4();
    p.apply_change(&Change::RegisterCaptureSource {
        record: CaptureSourceRecord {
            schema: CAPTURE_RECORD_SCHEMA.into(),
            id: capture_id,
            screenshot_asset_id: screenshot,
            screenshot_sha256: PNG.into(),
            manifest_asset_id: manifest,
            manifest_sha256: MANIFEST.into(),
            fixture_state_asset_id: fixture,
            fixture_state_sha256: STATE.into(),
            claim_id: p.brief.claims[0].id,
            source_project_id: p.id,
            source_generation: p.generation,
            source_revision: observed,
            app_id: "motionwright-studio".into(),
            app_version: "0.1.0".into(),
            source_commit: "d".repeat(40),
            scenario_id: "production-original-ui-local-v1".into(),
            origin: "http://127.0.0.1:1420".into(),
            route: "/".into(),
            viewport_width: 1440,
            viewport_height: 940,
            device_scale_factor: 1,
            screenshot_repeat_sha256: PNG.into(),
            redaction: CaptureMask {
                selector: ".revision-chip".into(),
                x: 88,
                y: 44,
                width: 68,
                height: 28,
                mask_color: "#0B0B0B".into(),
            },
            external_requests: 0,
            credential_fields: 0,
            declared_scope: "first-party-synthetic-fixture-only".into(),
        },
    })
    .unwrap();
    (p, stage_id, capture_id, screenshot)
}
#[test]
fn bound_stage_image_uses_registered_immutable_source_without_changing_editable_stage() {
    let (mut p, stage, capture, _) = attached();
    let original = p.scenes[0].nodes.clone();
    let camera = p.scenes[0].camera.clone();
    p.apply_change(&Change::BindStageCapture {
        stage_id: stage,
        capture_id: capture,
    })
    .unwrap();
    assert_eq!(
        p.production_design.stage_capture_cards,
        vec![StageCaptureCard {
            stage_id: stage,
            capture_id: capture,
            version: 1
        }]
    );
    assert_eq!(p.scenes[0].nodes, original);
    assert_eq!(p.scenes[0].camera, camera);
    assert_eq!(p.production_design.captures.len(), 1);
    assert_eq!(p.production_design.stages.len(), 1);
    p.validate().unwrap();
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(
        restored.production_design.stage_capture_cards,
        p.production_design.stage_capture_cards
    );
    let forbidden = p
        .apply_change(&Change::DetachProductStage { instance_id: stage })
        .unwrap_err()
        .to_string();
    assert!(forbidden.contains("Detach the first-party source card"));
    p.apply_change(&Change::DetachStageCapture { stage_id: stage })
        .unwrap();
    assert!(p.production_design.stage_capture_cards.is_empty());
    assert_eq!(p.scenes[0].nodes, original);
    assert_eq!(p.production_design.captures.len(), 1);
    assert!(
        p.apply_change(&Change::DetachStageCapture { stage_id: stage })
            .is_err()
    );
    p.validate().unwrap();
}
#[test]
fn source_links_cannot_be_forged_or_moved_to_another_stage() {
    let (mut p, stage, capture, _) = attached();
    let existing = p.clone();
    // Preserve the exact source-revision boundary instead of accepting zero
    // just to make a synthetic test fixture pass.
    let mut unpinned = p.production_design.captures[0].clone();
    unpinned.source_revision = 0;
    assert!(unpinned.validate(&p).is_err());
    assert!(
        p.apply_change(&Change::BindStageCapture {
            stage_id: stage,
            capture_id: Uuid::new_v4()
        })
        .unwrap_err()
        .to_string()
        .contains("registered")
    );
    assert_eq!(p, existing);
    assert!(
        p.apply_change(&Change::BindStageCapture {
            stage_id: Uuid::new_v4(),
            capture_id: capture
        })
        .is_err()
    );
    assert_eq!(p, existing);
    p.apply_change(&Change::BindStageCapture {
        stage_id: stage,
        capture_id: capture,
    })
    .unwrap();
    let stable = p.clone();
    let record = StageCaptureCard {
        stage_id: stage,
        capture_id: capture,
        version: 2,
    };
    p.production_design.stage_capture_cards[0] = record;
    assert!(p.validate().unwrap_err().to_string().contains("version"));
    p = stable;
    assert!(
        p.apply_change(&Change::RemoveAsset {
            asset_id: p.production_design.captures[0].screenshot_asset_id,
        })
        .unwrap_err()
        .to_string()
        .contains("provenance")
    );
}
#[test]
fn original_source_capture_tile_retains_stable_card_anchor_across_content_replacement() {
    let (mut p, stage, capture, _) = attached();
    p.apply_change(&Change::BindStageCapture {
        stage_id: stage,
        capture_id: capture,
    })
    .unwrap();
    let card_id = stage_node_id(stage, "card");
    let original = p.scenes[0]
        .nodes
        .iter()
        .find(|n| n.id == card_id)
        .unwrap()
        .clone();
    let mut authored = p.production_design.stages[0].config.clone();
    authored.screen_heading = "Another original safe UI concept".into();
    p.apply_change(&Change::UpsertProductStage {
        instance_id: stage,
        scene_id: p.scenes[0].id,
        config: authored,
    })
    .unwrap();
    assert_eq!(
        p.production_design.stage_capture_cards[0].capture_id,
        capture
    );
    assert_eq!(
        p.scenes[0].nodes.iter().find(|n| n.id == card_id).unwrap(),
        &original
    );
    p.validate().unwrap();
}
