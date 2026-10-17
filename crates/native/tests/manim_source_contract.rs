//! Actual Project/Store/CAS/portable-bundle checks. No fixture in this file is a
//! native provider, a video render, a permission grant, or a human acceptance.
use motionwright_domain::{
    Asset, BlendMode, CanvasNode, Change, CoordinateSpace, Fidelity, FidelityReport, NativeCapsule,
    NodeProperty, NodeStyle, Project, RationalTime, RendererKind, RevisionStamp,
};
use motionwright_manim_profile::{
    ManimSourcePackageV2, SOURCE_BUNDLE_MIME_V2, inspect_source_bundle_v2, source_bundle_v2,
};
use motionwright_native::{
    multi_renderer::build_manim_plan_v2,
    production::{
        apply_saved_manim_source_transform, inspect_saved_manim_source,
        preview_saved_manim_source_transform,
    },
};
use motionwright_service::StudioService;
use motionwright_storage::ProductionReceiptInput;
use semwright_native_sdk::ErrorCode;
use serde_json::json;
use std::{collections::BTreeSet, fs, path::Path};
use uuid::Uuid;

const GREEN: Uuid = Uuid::from_u128(0x10000000000040008000000000000001);
const RED: Uuid = Uuid::from_u128(0x10000000000040008000000000000002);
const BLUE: Uuid = Uuid::from_u128(0x10000000000040008000000000000003);

fn apply(service: &StudioService, p: &Project, change: Change) -> Project {
    service
        .apply(
            p.id,
            &RevisionStamp::from(p),
            &format!("source-fixture-change-{}", p.revision),
            &change,
        )
        .unwrap()
        .project
}

fn fixture(service: &StudioService) -> Project {
    let mut p = service
        .create_project("Editable original static study")
        .unwrap();
    p = apply(
        service,
        &p,
        Change::AddScene {
            name: "Original graphic study".into(),
            objective: "Verify content and editable source independently".into(),
            duration_seconds: 2,
        },
    );
    let scene_id = p.scenes[0].id;
    p = apply(
        service,
        &p,
        Change::SetSceneRenderer {
            scene_id,
            renderer: RendererKind::ManimCommunity,
        },
    );
    let mut profile = p.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    profile.frame_rate = RationalTime::new(12, 1).unwrap();
    p = apply(service, &p, Change::UpsertDeliverable { profile });
    let mut language = p.visual_language.clone();
    language
        .palette
        .iter_mut()
        .find(|token| token.name == "surface")
        .unwrap()
        .value = "#000000".into();
    p = apply(
        service,
        &p,
        Change::SetVisualLanguage {
            visual_language: language,
        },
    );
    for (id, kind, x, y, width, height, fill, z) in [
        (GREEN, "circle", 750.0, 390.0, 240.0, 240.0, "#20CC40", 20),
        (RED, "rectangle", 240.0, 180.0, 600.0, 360.0, "#E02040", 10),
        (BLUE, "rectangle", 1380.0, 120.0, 300.0, 150.0, "#2040E0", 0),
    ] {
        let node = CanvasNode {
            id,
            name: format!("Original {kind} {z}"),
            kind: kind.into(),
            parent_id: None,
            x,
            y,
            width,
            height,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: z,
            style: NodeStyle {
                fill: Some(fill.into()),
                stroke: None,
                stroke_width: 0.0,
                font_family: None,
                font_size: None,
                font_weight: None,
                line_height: None,
                blend_mode: BlendMode::Normal,
            },
            relations: vec![],
            property_locks: BTreeSet::new(),
            keyframes: vec![],
        };
        p = apply(service, &p, Change::AddCanvasNode { scene_id, node });
    }
    p
}

fn attach_source(
    service: &StudioService,
    p: &Project,
    folder: &Path,
    name: &str,
    bytes: &[u8],
    capsule: bool,
) -> (Project, Uuid) {
    let path = folder.join(name);
    fs::write(&path, bytes).unwrap();
    let blob = service.ingest_blob_file(&path).unwrap();
    let asset = Asset {
        id: Uuid::now_v7(),
        name: name.into(),
        media_type: SOURCE_BUNDLE_MIME_V2.into(),
        content_sha256: Some(blob.sha256.clone()),
        source_revision: Some(p.revision.to_string()),
    };
    let asset_id = asset.id;
    let mut p = apply(service, p, Change::AddAsset { asset });
    if capsule {
        let source = inspect_source_bundle_v2(bytes).unwrap();
        let capsule = NativeCapsule {
            id: Uuid::now_v7(),
            scene_id: source.plan.binding.scene_id,
            source_asset_id: asset_id,
            source_sha256: blob.sha256,
            label: "Static source awaiting native evidence".into(),
            fidelity: FidelityReport {
                renderer: "manim-community".into(),
                renderer_version: "0.21.0".into(),
                visual: Fidelity::Unavailable,
                temporal: Fidelity::Unavailable,
                structural: Fidelity::Translated,
                editable: Fidelity::Translated,
                losses: vec![
                    "Native getter, decoded-media, and native reopen evidence are separate gates"
                        .into(),
                ],
                evidence_sha256: None,
            },
            editable_parameters: vec!["node.position".into(), "node.size".into()],
            native_editor_hint:
                "Edit the typed plan; generated source is regenerated before rendering".into(),
            property_fidelity: vec![],
        };
        p = apply(service, &p, Change::UpsertNativeCapsule { capsule });
    }
    (p, asset_id)
}

fn source(p: &Project) -> Vec<u8> {
    source_bundle_v2(&build_manim_plan_v2(p, p.scenes[0].id, p.deliverables[0].id).unwrap())
        .unwrap()
}

fn edited(source: &[u8], mutate: impl FnOnce(&mut ManimSourcePackageV2)) -> Vec<u8> {
    let mut package = inspect_source_bundle_v2(source).unwrap();
    mutate(&mut package);
    source_bundle_v2(&package.plan).unwrap()
}

#[test]
fn portable_source_uses_independent_cas_and_keeps_origin_without_importing_native_authority() {
    let original = tempfile::tempdir().unwrap();
    let destination = tempfile::tempdir().unwrap();
    let service = StudioService::open(original.path().join("source.sqlite")).unwrap();
    let p = fixture(&service);
    let carrier = source(&p);
    let origin = inspect_source_bundle_v2(&carrier).unwrap();
    let (p, asset_id) = attach_source(
        &service,
        &p,
        original.path(),
        "original.mwmsrc.zip",
        &carrier,
        true,
    );
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: p.id,
            generation: p.generation,
            revision: p.revision,
            request_id: "preserved-original-unknown".into(),
            request_sha256: "a".repeat(64),
            command: "driver.manim-community.render.start".into(),
            stage: "outcome_unknown".into(),
            payload: json!({"fixture":"not a native job","outcome_known":false}),
        })
        .unwrap();
    let journal = service.production_receipts(p.id, 32).unwrap();
    let before =
        inspect_saved_manim_source(&service, p.id, &RevisionStamp::from(&p), asset_id).unwrap();
    assert_eq!(before.origin_binding, origin.plan.binding);
    assert_eq!(before.current_binding.revision, p.revision);
    assert!(before.origin_binding.revision < p.revision);
    assert!(!before.native_execution_performed);
    let bundle = original.path().join("portable");
    service.export_project_bundle(p.id, &bundle).unwrap();
    let imported_service = StudioService::open(destination.path().join("imported.sqlite")).unwrap();
    assert_ne!(
        service.store().lock().blob_root(),
        imported_service.store().lock().blob_root()
    );
    let imported = imported_service.import_project_bundle(&bundle).unwrap();
    assert_eq!(imported.id, p.id);
    assert_ne!(imported.generation, p.generation);
    assert_eq!(imported.revision, p.revision);
    assert_eq!(imported.scenes, p.scenes);
    assert_eq!(imported.deliverables, p.deliverables);
    assert_eq!(imported.assets, p.assets);
    assert_eq!(
        imported.production_design.capsules,
        p.production_design.capsules
    );
    assert_eq!(imported.visual_language, p.visual_language);
    assert_eq!(
        imported_service
            .store()
            .lock()
            .read_blob(&before.carrier_sha256, 2 * 1024 * 1024)
            .unwrap(),
        carrier
    );
    let after = inspect_saved_manim_source(
        &imported_service,
        imported.id,
        &RevisionStamp::from(&imported),
        asset_id,
    )
    .unwrap();
    assert_eq!(after.origin_binding, before.origin_binding);
    assert_eq!(
        after.prepared_source.source_sha256,
        before.prepared_source.source_sha256
    );
    assert_ne!(
        after.prepared_source.plan_sha256,
        before.prepared_source.plan_sha256
    );
    assert_ne!(after.prepared_bundle_sha256, before.prepared_bundle_sha256);
    assert_eq!(after.current_binding.generation, imported.generation);
    assert!(!after.imported_receipts_used_as_authority);
    assert!(
        imported_service
            .production_receipts(imported.id, 32)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        serde_json::to_value(service.production_receipts(p.id, 32).unwrap()).unwrap(),
        serde_json::to_value(journal).unwrap()
    );
    assert_eq!(
        inspect_saved_manim_source(
            &imported_service,
            imported.id,
            &RevisionStamp::from(&p),
            asset_id
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    drop(imported_service);
    let reopened = StudioService::open(destination.path().join("imported.sqlite")).unwrap();
    assert_eq!(
        inspect_saved_manim_source(
            &reopened,
            imported.id,
            &RevisionStamp::from(&imported),
            asset_id
        )
        .unwrap(),
        after
    );
}

#[test]
fn edited_native_source_becomes_one_canonical_transform_and_keeps_both_carriers() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let p = fixture(&service);
    let original_bytes = source(&p);
    let (p, original_id) = attach_source(
        &service,
        &p,
        folder.path(),
        "original.mwmsrc.zip",
        &original_bytes,
        true,
    );
    let edit = edited(&original_bytes, |p| {
        p.plan
            .nodes
            .iter_mut()
            .find(|n| n.node_id == RED)
            .unwrap()
            .x_px = 330.0
    });
    let (p, edited_id) = attach_source(
        &service,
        &p,
        folder.path(),
        "edited.mwmsrc.zip",
        &edit,
        false,
    );
    let expected = RevisionStamp::from(&p);
    let preview =
        preview_saved_manim_source_transform(&service, p.id, &expected, original_id, edited_id)
            .unwrap();
    assert_eq!(preview.node_id, RED);
    assert!(!preview.canonical_import_applied);
    assert_eq!(service.project(p.id).unwrap(), p);
    let imported = apply_saved_manim_source_transform(
        &service,
        p.id,
        &expected,
        "import-one-native-transform",
        original_id,
        edited_id,
    )
    .unwrap();
    assert_eq!(imported.revision, p.revision + 1);
    assert_eq!(
        imported.scenes[0]
            .nodes
            .iter()
            .find(|n| n.id == RED)
            .unwrap()
            .x,
        330.0
    );
    for id in [GREEN, BLUE] {
        assert_eq!(
            imported.scenes[0].nodes.iter().find(|n| n.id == id),
            p.scenes[0].nodes.iter().find(|n| n.id == id)
        );
    }
    assert_eq!(imported.assets, p.assets);
    assert_eq!(
        imported.production_design.capsules,
        p.production_design.capsules
    );
    assert!(
        inspect_saved_manim_source(&service, p.id, &RevisionStamp::from(&imported), original_id)
            .is_err()
    );
    assert!(service.production_receipts(p.id, 32).unwrap().is_empty());
    assert_eq!(
        apply_saved_manim_source_transform(
            &service,
            p.id,
            &expected,
            "different-request-cannot-replay-stale",
            original_id,
            edited_id
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
}

#[test]
fn source_import_respects_saved_property_locks_without_project_or_journal_effect() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let p = fixture(&service);
    let carrier = source(&p);
    let (p, origin) = attach_source(
        &service,
        &p,
        folder.path(),
        "original.mwmsrc.zip",
        &carrier,
        true,
    );
    let edit = edited(&carrier, |p| {
        p.plan
            .nodes
            .iter_mut()
            .find(|n| n.node_id == RED)
            .unwrap()
            .x_px = 330.0
    });
    let (p, candidate) = attach_source(
        &service,
        &p,
        folder.path(),
        "edited.mwmsrc.zip",
        &edit,
        false,
    );
    let p = apply(
        &service,
        &p,
        Change::SetNodePropertyLock {
            scene_id: p.scenes[0].id,
            node_id: RED,
            property: NodeProperty::Position,
            locked: true,
        },
    );
    let error = apply_saved_manim_source_transform(
        &service,
        p.id,
        &RevisionStamp::from(&p),
        "locked-native-edit",
        origin,
        candidate,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::PermissionDenied);
    assert!(error.outcome_known);
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 32).unwrap().is_empty());
}

#[test]
fn source_import_refuses_rehashed_color_identity_clock_and_multi_node_edits() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let p = fixture(&service);
    let bytes = source(&p);
    let (mut p, origin) = attach_source(
        &service,
        &p,
        folder.path(),
        "original.mwmsrc.zip",
        &bytes,
        true,
    );
    for case in 0..5 {
        let edited = edited(&bytes, |p| {
            p.plan.nodes[1].x_px = 330.0;
            match case {
                0 => p.plan.nodes[1].fill_hex = "#FFFFFF".into(),
                1 => p.plan.nodes[1].node_id = Uuid::now_v7(),
                2 => {
                    p.plan.clock = motionwright_manim_profile::ManimFrameClockV2::from_canonical(
                        RationalTime::ZERO,
                        RationalTime::new(3, 1).unwrap(),
                        RationalTime::new(12, 1).unwrap(),
                    )
                    .unwrap()
                }
                3 => p.plan.nodes[0].x_px += 30.0,
                _ => p.plan.binding.revision += 999,
            }
        });
        let (with_asset, candidate) = attach_source(
            &service,
            &p,
            folder.path(),
            &format!("candidate-{case}.mwmsrc.zip"),
            &edited,
            false,
        );
        p = with_asset;
        let error = apply_saved_manim_source_transform(
            &service,
            p.id,
            &RevisionStamp::from(&p),
            &format!("bad-native-import-{case}"),
            origin,
            candidate,
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::InvalidArgument);
        assert_eq!(service.project(p.id).unwrap(), p);
    }
    assert!(service.production_receipts(p.id, 32).unwrap().is_empty());
}

#[test]
fn builder_refuses_unrealized_controls_and_uses_exact_deliverable_clock_without_rounding() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let p = fixture(&service);
    let scene = p.scenes[0].id;
    let delivery = p.deliverables[0].id;
    let plan = build_manim_plan_v2(&p, scene, delivery).unwrap();
    assert_eq!(plan.clock.frame_count, 24);
    assert_eq!(
        plan.nodes.iter().map(|n| n.node_id).collect::<Vec<_>>(),
        vec![GREEN, RED, BLUE]
    );
    for case in 0..7 {
        let mut bad = p.clone();
        match case {
            0 => bad.scenes[0].camera.zoom = 2.0,
            1 => bad.scenes[0].nodes[0].opacity = 0.5,
            2 => bad.scenes[0].nodes[0].style.fill = None,
            3 => bad.scenes[0].nodes[0].style.font_family = Some("Unmapped font".into()),
            4 => bad.scenes[0].nodes[0].rotation_deg = 15.0,
            5 => bad.deliverables[0].frame_rate = RationalTime::new(30000, 1001).unwrap(),
            _ => {
                bad.visual_language
                    .palette
                    .iter_mut()
                    .find(|t| t.name == "surface")
                    .unwrap()
                    .value = "#123456".into()
            }
        }
        assert!(build_manim_plan_v2(&bad, scene, delivery).is_err());
    }
    let mut edge = p;
    edge.scenes[0].duration = RationalTime::new(29, 25).unwrap();
    edge.deliverables[0].frame_rate = RationalTime::new(25, 1).unwrap();
    let edge = build_manim_plan_v2(&edge, scene, delivery).unwrap();
    assert_eq!(edge.clock.frame_count, 29);
    assert_eq!(edge.clock.duration, RationalTime::new(29, 25).unwrap());
}

#[test]
fn carrier_requires_current_scene_association_before_reopen() {
    let folder = tempfile::tempdir().unwrap();
    let service = StudioService::open(folder.path().join("source.sqlite")).unwrap();
    let p = fixture(&service);
    let bytes = source(&p);
    let (p, id) = attach_source(
        &service,
        &p,
        folder.path(),
        "unassociated.mwmsrc.zip",
        &bytes,
        false,
    );
    assert!(
        inspect_saved_manim_source(&service, p.id, &RevisionStamp::from(&p), id)
            .unwrap_err()
            .message
            .contains("NativeCapsule")
    );
    assert_eq!(service.project(p.id).unwrap(), p);
    assert!(service.production_receipts(p.id, 32).unwrap().is_empty());
}
