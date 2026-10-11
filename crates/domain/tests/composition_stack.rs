//! Integrated composition contract: independent creative sources share one
//! project, one canonical change journal and deterministic original objects.
//! No synthetic data/bitmap here is evidence of real customer behaviour.
use motionwright_domain::*;
use std::collections::BTreeSet;
use uuid::Uuid;

fn add_scene(project: &mut Project, label: &str) -> Uuid {
    project
        .apply_change(&Change::AddScene {
            name: label.into(),
            objective: format!("An original controlled {label} technical sample"),
            duration_seconds: 6,
        })
        .unwrap();
    project.scenes.last().unwrap().id
}
fn typed_node() -> CanvasNode {
    CanvasNode {
        id: Uuid::new_v4(),
        name: "Source measured counter".into(),
        kind: "text".into(),
        parent_id: None,
        x: 250.0,
        y: 320.0,
        width: 650.0,
        height: 110.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Original source placeholder".into()),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 0,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}
fn stage_config() -> ProductStageConfig {
    ProductStageConfig {
        author_attestation: "I authored this original non-branded generic UI study.".into(),
        ..Default::default()
    }
}
struct Suite {
    project: Project,
    stage_scene: Uuid,
    procedural_scene: Uuid,
    measured_scene: Uuid,
    table: Uuid,
    node: Uuid,
    source: Asset,
    stage_instance: Uuid,
    procedural_instance: Uuid,
}
fn integrated() -> Suite {
    let mut p = Project::new("Multiple bounded original creative sources").unwrap();
    // Source revision is explicit in this in-memory domain model. Content
    // byte verification belongs to SQLite Store and its independent tests.
    p.revision = 7;
    let stage_scene = add_scene(&mut p, "ProductStage");
    let procedural_scene = add_scene(&mut p, "Procedural");
    let measured_scene = add_scene(&mut p, "Measured");
    let stage_instance = Uuid::parse_str("00000000-0000-4000-8000-000000000005").unwrap();
    let procedural_instance = Uuid::parse_str("00000000-0000-4000-8000-000000000095").unwrap();
    p.apply_change(&Change::UpsertProductStage {
        instance_id: stage_instance,
        scene_id: stage_scene,
        config: stage_config(),
    })
    .unwrap();
    p.apply_change(&Change::UpsertProceduralField {
        instance_id: procedural_instance,
        scene_id: procedural_scene,
        config: ProceduralConfig::default(),
    })
    .unwrap();
    let source = Asset {
        id: Uuid::new_v4(),
        name: "Owner-supplied synthetic fixed-point fixture".into(),
        media_type: "application/json".into(),
        content_sha256: Some("a".repeat(64)),
        source_revision: Some("synthetic source declaration not byte verified by domain".into()),
    };
    p.apply_change(&Change::AddAsset {
        asset: source.clone(),
    })
    .unwrap();
    let node = typed_node();
    let node_id = node.id;
    p.apply_change(&Change::AddCanvasNode {
        scene_id: measured_scene,
        node,
    })
    .unwrap();
    let table = Uuid::new_v4();
    p.apply_change(&Change::UpsertMeasuredTable {
        table: MeasuredTable {
            schema: MEASURED_DATA_SCHEMA.into(),
            id: table,
            label: "Synthetic source row".into(),
            version: 1,
            source_asset_id: source.id,
            source_sha256: source.content_sha256.clone().unwrap(),
            rows: vec![MeasuredDatum {
                key: "rate".into(),
                label: "Synthetic rate".into(),
                milli: 12500,
                unit: MeasuredUnit::Percent,
            }],
        },
    })
    .unwrap();
    p.apply_change(&Change::UpsertMeasuredBinding {
        binding_id: Uuid::new_v4(),
        scene_id: measured_scene,
        node_id,
        table_id: table,
        datum_key: "rate".into(),
        view: MeasuredView::Label { decimals: 1 },
    })
    .unwrap();
    // Capture is the fourth independent source domain in this project.
    // Domain tests use declared hashes only; SQLite tests verify actual blobs.
    let observed = p.revision;
    p.apply_change(&Change::AddClaim {
        text: "The original first-party Studio fixture contains a visible screen.".into(),
        context: "synthetic-first-party-capture-validation".into(),
        source: Some(SourceReference::ProjectRevision { revision: observed }),
        source_revision: Some("fixture-studio-v1".into()),
    })
    .unwrap();
    for (index, mime, digest) in [
        (1, "image/png", "c"),
        (2, "application/json", "d"),
        (3, "application/json", "e"),
    ] {
        p.apply_change(&Change::AddAsset {
            asset: Asset {
                id: Uuid::parse_str(&format!("00000000-0000-4000-8000-{index:012}")).unwrap(),
                name: format!("Declared synthetic capture source {index}"),
                media_type: mime.into(),
                content_sha256: Some(digest.repeat(64)),
                source_revision: Some("synthetic in-memory fixture, not byte validated".into()),
            },
        })
        .unwrap();
    }
    let capture = CaptureSourceRecord {
        schema: CAPTURE_RECORD_SCHEMA.into(),
        id: Uuid::new_v4(),
        screenshot_asset_id: Uuid::parse_str("00000000-0000-4000-8000-000000000001").unwrap(),
        screenshot_sha256: "c".repeat(64),
        manifest_asset_id: Uuid::parse_str("00000000-0000-4000-8000-000000000002").unwrap(),
        manifest_sha256: "d".repeat(64),
        fixture_state_asset_id: Uuid::parse_str("00000000-0000-4000-8000-000000000003").unwrap(),
        fixture_state_sha256: "e".repeat(64),
        claim_id: p.brief.claims.last().unwrap().id,
        source_project_id: p.id,
        source_generation: p.generation,
        source_revision: observed,
        app_id: "motionwright-studio".into(),
        app_version: "0.1.0".into(),
        source_commit: "f".repeat(40),
        scenario_id: "production-original-ui-local-v1".into(),
        origin: "http://127.0.0.1:1420".into(),
        route: "/".into(),
        viewport_width: 1440,
        viewport_height: 940,
        device_scale_factor: 1,
        screenshot_repeat_sha256: "c".repeat(64),
        redaction: CaptureMask {
            selector: ".revision-chip".into(),
            x: 100,
            y: 40,
            width: 80,
            height: 24,
            mask_color: "#0B0B0B".into(),
        },
        external_requests: 0,
        credential_fields: 0,
        declared_scope: "first-party-synthetic-fixture-only".into(),
    };
    p.apply_change(&Change::RegisterCaptureSource { record: capture })
        .unwrap();
    p.validate().unwrap();
    Suite {
        project: p,
        stage_scene,
        procedural_scene,
        measured_scene,
        table,
        node: node_id,
        source,
        stage_instance,
        procedural_instance,
    }
}
#[test]
fn source_bound_stage_measurement_and_procedural_objects_can_share_project_without_mutating_other_scenes()
 {
    let Suite {
        project: mut p,
        stage_scene,
        procedural_scene,
        measured_scene,
        table,
        node,
        source,
        stage_instance,
        procedural_instance,
    } = integrated();
    let stage_before = p
        .scenes
        .iter()
        .find(|s| s.id == stage_scene)
        .unwrap()
        .clone();
    let field_before = p
        .scenes
        .iter()
        .find(|s| s.id == procedural_scene)
        .unwrap()
        .clone();
    let measurement = p.scenes.iter().find(|s| s.id == measured_scene).unwrap();
    assert_eq!(stage_before.nodes.len(), STAGE_ROLES.len());
    assert_eq!(
        field_before.nodes.len(),
        usize::from(ProceduralConfig::default().count)
    );
    assert_eq!(measurement.nodes.len(), 1);
    assert!(
        measurement.nodes[0]
            .text
            .as_deref()
            .unwrap_or_default()
            .contains("12.5%")
    );
    assert_eq!(p.production_design.measured_bindings.len(), 1);
    let original_capture = p.production_design.captures[0].clone();
    let original_claim = p.brief.claims.last().unwrap().clone();
    assert_eq!(p.production_design.captures.len(), 1);
    assert_eq!(p.production_design.stages[0].id, stage_instance);
    assert_eq!(
        p.production_design.procedural_fields[0].id,
        procedural_instance
    );

    let source_two = Asset {
        id: Uuid::new_v4(),
        name: "Revised synthetic source declaration".into(),
        content_sha256: Some("b".repeat(64)),
        ..source
    };
    p.apply_change(&Change::AddAsset {
        asset: source_two.clone(),
    })
    .unwrap();
    p.apply_change(&Change::UpsertMeasuredTable {
        table: MeasuredTable {
            schema: MEASURED_DATA_SCHEMA.into(),
            id: table,
            label: "Synthetic source row".into(),
            version: 2,
            source_asset_id: source_two.id,
            source_sha256: source_two.content_sha256.clone().unwrap(),
            rows: vec![MeasuredDatum {
                key: "rate".into(),
                label: "Synthetic rate".into(),
                milli: 37500,
                unit: MeasuredUnit::Percent,
            }],
        },
    })
    .unwrap();
    assert_eq!(
        p.scenes.iter().find(|s| s.id == stage_scene).unwrap(),
        &stage_before
    );
    assert_eq!(
        p.scenes.iter().find(|s| s.id == procedural_scene).unwrap(),
        &field_before
    );
    let updated = p
        .scenes
        .iter()
        .find(|s| s.id == measured_scene)
        .unwrap()
        .clone();
    assert!(
        updated
            .nodes
            .iter()
            .find(|n| n.id == node)
            .unwrap()
            .text
            .as_deref()
            .unwrap_or_default()
            .contains("37.5%")
    );
    let impact = p.production_design.last_measured_impact.as_ref().unwrap();
    assert_eq!(impact.scene_ids, vec![measured_scene]);
    assert_eq!(impact.changed_keys, vec!["rate"]);
    assert_eq!(p.production_design.captures, vec![original_capture.clone()]);
    assert_eq!(p.brief.claims.last(), Some(&original_claim));

    let mut new_stage = stage_config();
    new_stage.screen_heading = "The new original workflow".into();
    p.apply_change(&Change::UpsertProductStage {
        instance_id: stage_instance,
        scene_id: stage_scene,
        config: new_stage,
    })
    .unwrap();
    assert_eq!(
        p.scenes.iter().find(|s| s.id == procedural_scene).unwrap(),
        &field_before
    );
    assert_eq!(
        p.scenes.iter().find(|s| s.id == measured_scene).unwrap(),
        &updated
    );
    assert_eq!(p.production_design.captures, vec![original_capture]);
    assert_eq!(p.brief.claims.last(), Some(&original_claim));
    p.validate().unwrap();
    let restored: Project = serde_json::from_value(serde_json::to_value(&p).unwrap()).unwrap();
    assert_eq!(restored, p);
}
#[test]
fn brand_governance_applies_to_original_stage_and_procedural_fill_with_independent_sources() {
    let Suite {
        project: mut p,
        stage_scene,
        procedural_scene,
        ..
    } = integrated();
    let brand = BrandProfile {
        id: Uuid::new_v4(),
        label: "Original composition palette".into(),
        version: 1,
        rules: vec![BrandRule::AllowedAccents {
            id: Uuid::new_v4(),
            colors: vec!["#86C8D1".into(), "#A5C8DF".into()],
        }],
    };
    p.apply_change(&Change::SetBrandGovernance {
        profile: Some(brand),
        exceptions: vec![],
    })
    .unwrap();
    p.validate().unwrap();
    let initial = p.clone();
    let mut forbidden_stage = stage_config();
    forbidden_stage.accent = "#F06020".into();
    let mut change = p.clone();
    assert!(
        change
            .apply_change(&Change::UpsertProductStage {
                instance_id: p.production_design.stages[0].id,
                scene_id: stage_scene,
                config: forbidden_stage,
            })
            .is_err()
    );
    let mut change = p.clone();
    let forbidden_field = ProceduralConfig {
        fill: "#F06020".into(),
        ..Default::default()
    };
    assert!(
        change
            .apply_change(&Change::UpsertProceduralField {
                instance_id: p.production_design.procedural_fields[0].id,
                scene_id: procedural_scene,
                config: forbidden_field,
            })
            .is_err()
    );
    assert_eq!(p, initial);
}
