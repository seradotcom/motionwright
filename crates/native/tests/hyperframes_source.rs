//! Real Store/CAS/source carriers and revision checks. These tests do not run
//! HyperFrames, a browser, FFmpeg, a provider or an owner permission workflow.

use motionwright_domain::{
    Asset, Change, Fidelity, FidelityReport, NativeCapsule, Project, RationalTime, RendererKind,
    RevisionStamp,
};
use motionwright_hyperframes_profile::{
    LayerContent, LayerTransform, SourceClock, SourceDocument, SourceLayer, author_source,
    carrier_bytes, sha256,
};
use motionwright_native::production::hyperframes_source::{
    HYPERFRAMES_CANONICAL_SELECTION_PENDING, HYPERFRAMES_SOURCE_MIME, HyperframesSourceSelection,
    inspect_saved_hyperframes_source, prepare_native_hyperframes_request,
    prepare_saved_hyperframes_source, preview_saved_hyperframes_source_reopen,
};
use motionwright_service::StudioService;
use motionwright_storage::ProductionReceiptInput;
use semwright_native_sdk::ErrorCode;
use serde_json::json;
use std::{fs, path::Path};
use uuid::Uuid;

fn apply(service: &StudioService, project: &Project, change: Change) -> Project {
    service
        .apply(
            project.id,
            &RevisionStamp::from(project),
            &format!("hf-source-fixture-{}", project.revision),
            &change,
        )
        .unwrap()
        .project
}

fn document() -> SourceDocument {
    SourceDocument {
        clock: SourceClock {
            fps: RationalTime::new(30, 1).unwrap(),
            frame_count: 60,
        },
        width: 640,
        height: 360,
        background: "#101020".into(),
        layers: vec![
            SourceLayer {
                id: Uuid::from_u128(0x1001),
                start_frame: 0,
                end_frame: 30,
                width: 180,
                height: 120,
                transform: LayerTransform {
                    x: 40,
                    y: 50,
                    ..LayerTransform::default()
                },
                content: LayerContent::Rectangle {
                    color: "#E02040".into(),
                    radius: 0,
                },
            },
            SourceLayer {
                id: Uuid::from_u128(0x1002),
                start_frame: 30,
                end_frame: 60,
                width: 180,
                height: 120,
                transform: LayerTransform {
                    x: 300,
                    y: 130,
                    ..LayerTransform::default()
                },
                content: LayerContent::Rectangle {
                    color: "#20CC40".into(),
                    radius: 0,
                },
            },
        ],
    }
}

fn source() -> Vec<u8> {
    carrier_bytes(&author_source(document(), vec![]).unwrap()).unwrap()
}

fn add_asset(
    service: &StudioService,
    project: &Project,
    dir: &Path,
    name: &str,
    bytes: &[u8],
    media_type: &str,
) -> (Project, Uuid) {
    let path = dir.join(name);
    fs::write(&path, bytes).unwrap();
    let blob = service.ingest_blob_file(&path).unwrap();
    let asset = Asset {
        id: Uuid::now_v7(),
        name: name.into(),
        media_type: media_type.into(),
        content_sha256: Some(blob.sha256),
        source_revision: Some(project.revision.to_string()),
    };
    let id = asset.id;
    let project = apply(service, project, Change::AddAsset { asset });
    // Every subsequent source check must work through CAS, without its import path.
    fs::remove_file(path).unwrap();
    (project, id)
}

fn fixture_with_media_type(
    service: &StudioService,
    dir: &Path,
    media_type: &str,
) -> (Project, HyperframesSourceSelection, Vec<u8>) {
    let mut project = service
        .create_project("Original finite HTML source")
        .unwrap();
    project = apply(
        service,
        &project,
        Change::AddScene {
            name: "Two original timed cards".into(),
            objective: "Verify source and timed pixels separately".into(),
            duration_seconds: 2,
        },
    );
    let mut profile = project.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    profile.frame_rate = RationalTime::new(30, 1).unwrap();
    profile.captions = false;
    project = apply(service, &project, Change::UpsertDeliverable { profile });
    let bytes = source();
    let (mut project, asset_id) = add_asset(
        service,
        &project,
        dir,
        "original.mwhf.json",
        &bytes,
        media_type,
    );
    let capsule = NativeCapsule {
        id: Uuid::now_v7(),
        scene_id: project.scenes[0].id,
        source_asset_id: asset_id,
        source_sha256: sha256(&bytes),
        label: "Original HTML awaiting independent native evidence".into(),
        fidelity: FidelityReport {
            renderer: "hyperframes".into(),
            renderer_version: "0.8.167".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Native,
            editable: Fidelity::Native,
            losses: vec![
                "Source-only observation; native pixels and native edited reopen are unverified"
                    .into(),
            ],
            evidence_sha256: None,
        },
        editable_parameters: vec!["source.layer.transform".into()],
        native_editor_hint: "Finite native HTML source".into(),
        property_fidelity: vec![],
    };
    let selection = HyperframesSourceSelection {
        scene_id: capsule.scene_id,
        capsule_id: capsule.id,
        deliverable_id: project.deliverables[0].id,
    };
    project = apply(service, &project, Change::UpsertNativeCapsule { capsule });
    (project, selection, bytes)
}

fn fixture(service: &StudioService, dir: &Path) -> (Project, HyperframesSourceSelection, Vec<u8>) {
    fixture_with_media_type(service, dir, HYPERFRAMES_SOURCE_MIME)
}

fn original_unknown(service: &StudioService, project: &Project) {
    service
        .append_production_receipt(ProductionReceiptInput {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            request_id: "original-not-reissued".into(),
            request_sha256: "a".repeat(64),
            command: "driver.hyperframes.source.render".into(),
            stage: "outcome_unknown".into(),
            payload: json!({"fixture":"stored evidence only, no native job","outcome_known":false}),
        })
        .unwrap();
}

#[test]
fn source_preparation_reads_actual_cas_without_the_import_path_and_never_claims_a_render() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, bytes) = fixture(&service, dir.path());
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let prepared = prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    assert_eq!(prepared.retained_source_bytes(&service).unwrap(), bytes);
    let observed = prepared.inspection();
    assert_eq!(observed.source_sha256, sha256(&bytes));
    assert_eq!(observed.source.clock.frame_count, 60);
    assert_eq!(observed.source.capture_pixels, 640 * 360 * 60);
    assert!(!observed.native_execution_performed);
    assert!(!observed.metadata_used_as_authority);
    assert!(!observed.native_render_verified);
    assert!(!observed.canonical_import_applied);
    assert!(!observed.export_authorized);
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
    assert!(!dir.path().join("original.mwhf.json").exists());
}

#[test]
fn capsule_metadata_cannot_select_hyperframes_or_reissue_an_original_unknown() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (mut project, selection, _) = fixture(&service, dir.path());
    let mut capsule = project.production_design.capsules[0].clone();
    capsule.fidelity = FidelityReport {
        renderer: "hyperframes".into(),
        renderer_version: "0.8.167".into(),
        visual: Fidelity::Native,
        temporal: Fidelity::Native,
        structural: Fidelity::Native,
        editable: Fidelity::Native,
        losses: vec![],
        evidence_sha256: Some("b".repeat(64)),
    };
    capsule.native_editor_hint = "driver.hyperframes.source.render".into();
    project = apply(&service, &project, Change::UpsertNativeCapsule { capsule });
    original_unknown(&service, &project);
    let original = service.production_receipts(project.id, 8).unwrap();
    for renderer in [
        RendererKind::MotionCanvas,
        RendererKind::Mlt,
        RendererKind::Blender,
        RendererKind::ManimCommunity,
        RendererKind::Remotion,
        RendererKind::ManimGl,
    ] {
        project = apply(
            &service,
            &project,
            Change::SetSceneRenderer {
                scene_id: selection.scene_id,
                renderer,
            },
        );
        let stamp = RevisionStamp::from(&project);
        // This proves the source, clock and geometry pass before the provider gate.
        let prepared =
            prepare_saved_hyperframes_source(&service, project.id, &stamp, &selection).unwrap();
        assert!(!prepared.inspection().metadata_used_as_authority);
        let error = prepare_native_hyperframes_request(
            &service,
            project.id,
            &stamp,
            &selection,
            Uuid::from_u128(0x9001),
            &"c".repeat(64),
        )
        .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert_eq!(error.message, HYPERFRAMES_CANONICAL_SELECTION_PENDING);
        assert!(error.outcome_known);
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service.production_receipts(project.id, 8).unwrap(),
            original
        );
        assert!(!dir.path().join("output.mp4").exists());
    }
}

#[test]
fn all_three_stamp_components_are_required() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let original = RevisionStamp::from(&project);
    let mut wrong_resource = original.clone();
    wrong_resource.resource.push_str("-foreign");
    let mut wrong_generation = original.clone();
    wrong_generation.generation = Uuid::now_v7();
    let mut wrong_revision = original;
    wrong_revision.revision += 1;
    for stamp in [wrong_resource, wrong_generation, wrong_revision] {
        let error =
            inspect_saved_hyperframes_source(&service, project.id, &stamp, &selection).unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert!(error.outcome_known);
    }
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(
        service
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn preparation_cannot_survive_a_saved_intent_change() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let prepared = prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    let changed = apply(
        &service,
        &project,
        Change::RenameProject {
            title: "Changed author intent".into(),
        },
    );
    assert_eq!(changed.scenes, project.scenes);
    assert_eq!(changed.assets, project.assets);
    assert_eq!(
        prepared.revalidate(&service).unwrap_err().code,
        ErrorCode::StaleReference
    );
    assert_eq!(
        prepared.retained_source_bytes(&service).unwrap_err().code,
        ErrorCode::StaleReference
    );
    let after = inspect_saved_hyperframes_source(
        &service,
        changed.id,
        &RevisionStamp::from(&changed),
        &selection,
    )
    .unwrap();
    assert_ne!(
        after.project_snapshot_sha256,
        prepared.inspection().project_snapshot_sha256
    );
    assert_ne!(
        after.canonical_dependency_sha256,
        prepared.inspection().canonical_dependency_sha256
    );
}

#[test]
fn foreign_scene_capsule_and_profile_ids_are_not_inferred_from_metadata() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    for field in ["scene_id", "capsule_id", "deliverable_id"] {
        let mut json = serde_json::to_value(&selection).unwrap();
        json[field] = json!(Uuid::now_v7());
        let altered = serde_json::from_value(json).unwrap();
        assert!(
            inspect_saved_hyperframes_source(
                &service,
                project.id,
                &RevisionStamp::from(&project),
                &altered
            )
            .is_err()
        );
    }
    assert_eq!(
        service
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn a_digest_bound_generic_json_asset_is_not_a_hyperframes_carrier_selection() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture_with_media_type(&service, dir.path(), "application/json");
    assert!(
        inspect_saved_hyperframes_source(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            &selection
        )
        .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn changed_cas_payload_cannot_be_hidden_by_unchanged_asset_or_capsule_metadata() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, bytes) = fixture(&service, dir.path());
    let prepared = prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    let path = service
        .store()
        .lock()
        .verified_blob_path(&sha256(&bytes))
        .unwrap();
    let mut changed = bytes;
    changed[0] = b'[';
    fs::write(path, changed).unwrap();
    assert!(prepared.revalidate(&service).is_err());
    assert!(
        inspect_saved_hyperframes_source(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            &selection
        )
        .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(
        service
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn source_observation_does_not_silently_override_saved_geometry_or_clock() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (mut project, selection, _) = fixture(&service, dir.path());
    for change_fps in [false, true] {
        let mut profile = project.deliverables[0].clone();
        profile.width = if change_fps { 640 } else { 1280 };
        profile.frame_rate = RationalTime::new(if change_fps { 25 } else { 30 }, 1).unwrap();
        project = apply(&service, &project, Change::UpsertDeliverable { profile });
        let stamp = RevisionStamp::from(&project);
        assert!(inspect_saved_hyperframes_source(&service, project.id, &stamp, &selection).is_ok());
        assert_eq!(
            prepare_saved_hyperframes_source(&service, project.id, &stamp, &selection)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
    }
    assert_eq!(
        service
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn source_contribution_does_not_claim_saved_captions_are_realized() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (mut project, selection, _) = fixture(&service, dir.path());
    let mut profile = project.deliverables[0].clone();
    profile.captions = true;
    project = apply(&service, &project, Change::UpsertDeliverable { profile });
    let error = prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    assert!(error.message.contains("captions"));
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn exact_source_bytes_can_be_reopened_as_a_distinct_cas_asset_without_importing_authority() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, bytes) = fixture(&service, dir.path());
    let (project, reopened_asset) = add_asset(
        &service,
        &project,
        dir.path(),
        "reopened.mwhf.json",
        &bytes,
        HYPERFRAMES_SOURCE_MIME,
    );
    original_unknown(&service, &project);
    let original_journal = service.production_receipts(project.id, 8).unwrap();
    let preview = preview_saved_hyperframes_source_reopen(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
        reopened_asset,
    )
    .unwrap();
    assert!(preview.exact_carrier_bytes_reopened);
    assert!(
        preview.document_unchanged
            && preview.native_html_unchanged
            && preview.resource_bytes_unchanged
    );
    assert!(preview.finite_source_contract_verified);
    assert!(!preview.canonical_import_applied && !preview.native_rerender_verified);
    assert!(!preview.imported_receipts_used_as_authority && !preview.export_authorized);
    assert_eq!(
        service.production_receipts(project.id, 8).unwrap(),
        original_journal
    );
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn a_real_finite_source_edit_is_detected_and_stays_a_preview() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let mut edited = document();
    edited.layers[1].transform.x += 40;
    let bytes = carrier_bytes(&author_source(edited, vec![]).unwrap()).unwrap();
    let (project, reopened_asset) = add_asset(
        &service,
        &project,
        dir.path(),
        "edited.mwhf.json",
        &bytes,
        HYPERFRAMES_SOURCE_MIME,
    );
    let preview = preview_saved_hyperframes_source_reopen(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
        reopened_asset,
    )
    .unwrap();
    assert!(!preview.exact_carrier_bytes_reopened);
    assert!(!preview.document_unchanged && !preview.native_html_unchanged);
    assert!(preview.resource_bytes_unchanged);
    assert!(
        !preview.canonical_import_applied
            && !preview.native_rerender_verified
            && !preview.export_authorized
    );
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(
        service
            .production_receipt_watermark(project.id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn reopened_html_outside_the_admission_witness_is_rejected_even_with_new_correct_cas_hash() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, bytes) = fixture(&service, dir.path());
    let mut carrier: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    carrier["html"] = json!("<html><script>fetch('https://invalid.example')</script></html>");
    let bytes = serde_json::to_vec(&carrier).unwrap();
    let (project, reopened_asset) = add_asset(
        &service,
        &project,
        dir.path(),
        "outside-grammar.mwhf.json",
        &bytes,
        HYPERFRAMES_SOURCE_MIME,
    );
    let error = preview_saved_hyperframes_source_reopen(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
        reopened_asset,
    )
    .unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn portable_project_reopen_uses_an_independent_store_and_a_new_generation() {
    let origin = tempfile::tempdir().unwrap();
    let destination = tempfile::tempdir().unwrap();
    let service = StudioService::open(origin.path().join("source.sqlite")).unwrap();
    let (project, selection, bytes) = fixture(&service, origin.path());
    let before = inspect_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    let bundle = origin.path().join("portable");
    service.export_project_bundle(project.id, &bundle).unwrap();
    let imported_service = StudioService::open(destination.path().join("imported.sqlite")).unwrap();
    let imported = imported_service.import_project_bundle(&bundle).unwrap();
    assert_eq!(imported.id, project.id);
    assert_ne!(imported.generation, project.generation);
    assert_eq!(imported.scenes, project.scenes);
    assert_eq!(
        imported.production_design.capsules,
        project.production_design.capsules
    );
    assert_ne!(
        service.store().lock().blob_root(),
        imported_service.store().lock().blob_root()
    );
    assert_eq!(
        inspect_saved_hyperframes_source(
            &imported_service,
            imported.id,
            &RevisionStamp::from(&project),
            &selection
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    let reopened = prepare_saved_hyperframes_source(
        &imported_service,
        imported.id,
        &RevisionStamp::from(&imported),
        &selection,
    )
    .unwrap();
    assert_eq!(
        reopened.retained_source_bytes(&imported_service).unwrap(),
        bytes
    );
    assert_eq!(reopened.inspection().source_sha256, before.source_sha256);
    assert_ne!(
        reopened.inspection().canonical_dependency_sha256,
        before.canonical_dependency_sha256
    );
    assert!(!reopened.inspection().native_render_verified);
    assert_eq!(
        imported_service
            .production_receipt_watermark(imported.id)
            .unwrap()
            .count,
        0
    );
}

#[test]
fn editor_selection_rejects_paths_plans_permissions_and_imported_verdicts() {
    let selection = HyperframesSourceSelection {
        scene_id: Uuid::from_u128(1),
        capsule_id: Uuid::from_u128(2),
        deliverable_id: Uuid::from_u128(3),
    };
    for (key, value) in [
        ("path", json!("private.html")),
        ("plan", json!({})),
        ("authorized", json!(true)),
        ("renderer", json!("hyperframes")),
        ("verified", json!(true)),
        ("source_sha256", json!("a".repeat(64))),
    ] {
        let mut request = serde_json::to_value(&selection).unwrap();
        request[key] = value;
        assert!(
            serde_json::from_value::<HyperframesSourceSelection>(request).is_err(),
            "accepted {key}"
        );
    }
}

fn assert_known_unrealized(
    service: &StudioService,
    project: &Project,
    selection: &HyperframesSourceSelection,
    message: &str,
) {
    // Valid saved state and exact geometry/source are essential: neither a
    // malformed fixture nor the closed renderer enum may mask this rejection.
    project.validate().unwrap();
    let stamp = RevisionStamp::from(project);
    let watermark = service.production_receipt_watermark(project.id).unwrap();
    let observed =
        inspect_saved_hyperframes_source(service, project.id, &stamp, selection).unwrap();
    assert!(!observed.native_execution_performed && !observed.export_authorized);
    for error in [
        prepare_saved_hyperframes_source(service, project.id, &stamp, selection).unwrap_err(),
        prepare_native_hyperframes_request(
            service,
            project.id,
            &stamp,
            selection,
            Uuid::from_u128(0xA401),
            &"c".repeat(64),
        )
        .unwrap_err(),
    ] {
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert!(error.outcome_known);
        assert_eq!(error.message, message);
        assert_ne!(error.message, HYPERFRAMES_CANONICAL_SELECTION_PENDING);
    }
    assert_eq!(service.project(project.id).unwrap(), *project);
    assert_eq!(
        service.production_receipt_watermark(project.id).unwrap(),
        watermark
    );
}

#[test]
fn authored_scene_beats_are_rejected_before_the_renderer_enum_or_any_effect() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let project = apply(
        &service,
        &project,
        Change::UpsertSceneBeat {
            scene_id: selection.scene_id,
            beat: motionwright_domain::Beat {
                id: Uuid::from_u128(0xB401),
                label: "Saved timing".into(),
                objective: "Explicit beat must remain visible".into(),
                start: RationalTime::new(1, 2).unwrap(),
                duration: RationalTime::new(1, 2).unwrap(),
            },
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize saved scene beats",
    );
}

#[test]
fn saved_mix_gains_and_each_mastering_target_cannot_be_dropped_by_a_silent_source() {
    for field in 0..4 {
        let dir = tempfile::tempdir().unwrap();
        let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
        let (project, selection, _) = fixture(&service, dir.path());
        let mut mix = project.audio.mix.clone();
        match field {
            0 => mix.voice_gain_db = -3.0,
            1 => mix.music_gain_db = -18.0,
            2 => mix.target_lufs = Some(-16.0),
            _ => mix.target_true_peak_dbfs = Some(-1.0),
        }
        let project = apply(&service, &project, Change::SetMixIntent { mix });
        assert_known_unrealized(
            &service,
            &project,
            &selection,
            "HyperFrames source cannot realize selected audio, scene audio timing or mix controls",
        );
    }
}

fn manual_cue(at: RationalTime) -> motionwright_domain::AudioCue {
    motionwright_domain::AudioCue {
        id: Uuid::now_v7(),
        label: "Original timing cue".into(),
        at,
        source_segment_id: None,
        evidence: motionwright_domain::CueEvidence::Manual,
    }
}

#[test]
fn only_audio_cues_in_the_half_open_selected_scene_are_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let project = apply(
        &service,
        &project,
        Change::AddScene {
            name: "Unrelated later scene".into(),
            objective: "Keep scope exact".into(),
            duration_seconds: 2,
        },
    );
    let project = apply(
        &service,
        &project,
        Change::AddAudioCue {
            cue: manual_cue(RationalTime::new(2, 1).unwrap()),
        },
    );
    prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    let project = apply(
        &service,
        &project,
        Change::AddAudioCue {
            cue: manual_cue(RationalTime::new(1, 1).unwrap()),
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize selected audio, scene audio timing or mix controls",
    );
}

fn one_frame_silent_wav() -> Vec<u8> {
    // Original PCM source fixture only; no loudness, mixer or native claim.
    let data_size = 1_600_u32 * 4;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(36 + data_size).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&192_000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&data_size.to_le_bytes());
    bytes.resize(44 + data_size as usize, 0);
    bytes
}

#[test]
fn an_unused_voice_catalog_entry_is_observable_but_selected_voice_is_not_silenced() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let wav = one_frame_silent_wav();
    let (project, asset_id) = add_asset(
        &service,
        &project,
        dir.path(),
        "voice.wav",
        &wav,
        "audio/wav",
    );
    let track = motionwright_domain::VoiceTrack {
        id: Uuid::from_u128(0xB402),
        asset_id,
        label: "Unused voice source".into(),
        sample_rate_hz: 48_000,
        channels: 2,
        measured_duration: RationalTime::new(1, 30).unwrap(),
        source_sha256: sha256(&wav),
        loudness_lufs: None,
        true_peak_dbfs: None,
    };
    let track_id = track.id;
    let project = apply(&service, &project, Change::AddVoiceTrack { track });
    assert!(project.audio.active_voice_track_id.is_none());
    prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    let project = apply(&service, &project, Change::SetActiveVoiceTrack { track_id });
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize selected audio, scene audio timing or mix controls",
    );
}

#[test]
fn a_scene_bound_semantic_sfx_cannot_be_ignored_by_the_source_carrier() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let wav = one_frame_silent_wav();
    let (project, source_asset_id) =
        add_asset(&service, &project, dir.path(), "sfx.wav", &wav, "audio/wav");
    let project = apply(
        &service,
        &project,
        Change::UpsertSemanticSfx {
            cue: motionwright_domain::SemanticSfxCue {
                schema: motionwright_domain::SFX_CUE_SCHEMA.into(),
                id: Uuid::from_u128(0xB403),
                scene_id: selection.scene_id,
                event: motionwright_domain::SfxEvent::SceneReveal,
                anchor: motionwright_domain::SfxAnchor::SceneStart,
                offset_frames: 0,
                source_asset_id,
                source_sha256: sha256(&wav),
                source_frames: 1,
                gain_milli_db: 0,
                attack_frames: 0,
                release_frames: 0,
                original_source_attestation: "Original source test declaration only".into(),
            },
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize selected audio, scene audio timing or mix controls",
    );
}

#[test]
fn a_saved_shot_plan_is_not_replaced_by_an_unrelated_native_source() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let project = apply(
        &service,
        &project,
        Change::SetProductionPlan {
            plan: Some(motionwright_domain::ProductionPlan {
                objective: "Original scene plan".into(),
                audience: "Source reviewer".into(),
                concept: "Show the planned objects".into(),
                reference_constraints: vec![],
                exclusions: vec![],
                shots: vec![motionwright_domain::PlannedShot {
                    scene_id: selection.scene_id,
                    purpose: "An explicit planned shot".into(),
                    claim_ids: vec![],
                    asset_ids: vec![],
                    evidence_kind: motionwright_domain::NarrativeEvidenceKind::GraphicStudy,
                }],
                clock: motionwright_domain::ProductionClock::Timeline,
                approval: None,
            }),
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize the saved production shot and clock",
    );
}

#[test]
fn selected_recipe_scene_intent_and_original_mix_are_separate_unsupported_inputs() {
    for original_mix in [false, true] {
        let dir = tempfile::tempdir().unwrap();
        let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
        let (project, selection, _) = fixture(&service, dir.path());
        let recipe = motionwright_domain::DeliveryRecipe {
            schema: motionwright_domain::DeliveryRecipeSchema::V1,
            scene_intents: if original_mix {
                vec![]
            } else {
                vec![motionwright_domain::DeliverySceneIntent {
                    scene_id: selection.scene_id,
                    role: Some(motionwright_domain::DeliveryNarrativeRole::Reveal),
                    archetype: Some(motionwright_domain::DeliveryArchetype::ObjectSpotlight),
                }]
            },
            original_mix: original_mix.then_some(motionwright_domain::OriginalMixRecipe {
                music_asset_id: None,
                sfx_asset_id: None,
                sfx_gain_db: -3.0,
                duck_attenuation_db: 12.0,
                duck_attack_samples: 2_400,
                duck_release_samples: 4_800,
            }),
        };
        let project = apply(
            &service,
            &project,
            Change::SetDeliveryRecipe {
                deliverable_profile_id: selection.deliverable_id,
                recipe: Some(recipe),
            },
        );
        assert_known_unrealized(
            &service,
            &project,
            &selection,
            "HyperFrames source cannot realize the saved delivery recipe for this contribution",
        );
    }
}

#[test]
fn saved_palette_changes_are_not_mistaken_for_an_unchanged_source_digest() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let mut language = project.visual_language.clone();
    language.palette[0].value = "#111122".into();
    let project = apply(
        &service,
        &project,
        Change::SetVisualLanguage {
            visual_language: language,
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize saved visual language or brand controls",
    );
}

#[test]
fn an_approved_crop_still_needs_realization_before_native_source_render() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let mut profile = project.deliverables[0].clone();
    profile.framing_strategy = motionwright_domain::FramingStrategy::Crop;
    profile.crop_approved = true;
    let project = apply(&service, &project, Change::UpsertDeliverable { profile });
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize the saved crop strategy",
    );
}

#[test]
fn a_second_capsule_for_the_selected_scene_is_not_silently_omitted() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let mut capsule = project.production_design.capsules[0].clone();
    capsule.id = Uuid::from_u128(0xB404);
    let project = apply(&service, &project, Change::UpsertNativeCapsule { capsule });
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source requires an unambiguous native capsule for this scene",
    );
}

fn replace_source(
    service: &StudioService,
    project: &Project,
    dir: &Path,
    document: SourceDocument,
    resources: Vec<motionwright_hyperframes_profile::SourceResource>,
) -> Project {
    let bytes = carrier_bytes(&author_source(document, resources).unwrap()).unwrap();
    let (project, asset_id) = add_asset(
        service,
        project,
        dir,
        "resource.mwhf.json",
        &bytes,
        HYPERFRAMES_SOURCE_MIME,
    );
    let mut capsule = project.production_design.capsules[0].clone();
    capsule.source_asset_id = asset_id;
    capsule.source_sha256 = sha256(&bytes);
    apply(service, &project, Change::UpsertNativeCapsule { capsule })
}

#[test]
fn source_font_bytes_and_a_cleared_declaration_do_not_establish_glyph_or_rights_admission() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    // A syntactic WOFF2 header intentionally cannot prove an actual font/glyph.
    // Its acceptance by the source parser must never open the native route.
    let mut bytes = vec![0_u8; 48];
    bytes[..4].copy_from_slice(b"wOF2");
    bytes[8..12].copy_from_slice(&48_u32.to_be_bytes());
    bytes[12..14].copy_from_slice(&1_u16.to_be_bytes());
    bytes[16..20].copy_from_slice(&1_u32.to_be_bytes());
    let resource = motionwright_hyperframes_profile::SourceResource::from_bytes(
        motionwright_hyperframes_profile::ResourceKind::Woff2,
        &bytes,
    )
    .unwrap();
    let mut doc = document();
    doc.layers[0].content = LayerContent::Text {
        text: "Source text".into(),
        color: "#FFFFFF".into(),
        font_sha256: resource.sha256.clone(),
        font_size: 32,
    };
    let project = replace_source(&service, &project, dir.path(), doc, vec![resource]);
    let mut language = project.visual_language.clone();
    language.fonts.push(motionwright_domain::FontRight {
        family: "Claimed font".into(),
        source: "Owner declaration only".into(),
        rights_status: motionwright_domain::RightsStatus::Cleared,
    });
    let project = apply(
        &service,
        &project,
        Change::SetVisualLanguage {
            visual_language: language,
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames text requires canonical font rights and glyph admission before render",
    );
}

#[test]
fn a_finite_original_png_stays_inspectable_without_a_canonical_resource_rights_join() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    // One original RGBA pixel, with real PNG chunks, zlib data and CRCs.
    let png = [
        137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1, 8, 6,
        0, 0, 0, 31, 21, 196, 137, 0, 0, 0, 13, 73, 68, 65, 84, 120, 156, 99, 120, 160, 224, 240,
        31, 0, 5, 100, 2, 64, 42, 29, 134, 29, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130,
    ];
    let resource = motionwright_hyperframes_profile::SourceResource::from_bytes(
        motionwright_hyperframes_profile::ResourceKind::Png,
        &png,
    )
    .unwrap();
    let mut doc = document();
    doc.layers[0].content = LayerContent::Image {
        asset_sha256: resource.sha256.clone(),
    };
    let project = replace_source(&service, &project, dir.path(), doc, vec![resource]);
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames images require canonical resource rights and image admission before render",
    );
}

fn original_canvas_node() -> motionwright_domain::CanvasNode {
    motionwright_domain::CanvasNode {
        id: Uuid::from_u128(0xB405),
        name: "Canonical original object".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 850.0,
        y: 440.0,
        width: 120.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: Default::default(),
        z_index: 0,
        style: Default::default(),
        relations: vec![],
        property_locks: Default::default(),
        keyframes: vec![],
    }
}

#[test]
fn a_valid_canonical_camera_reframe_is_rejected_by_its_own_guard() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let project = apply(
        &service,
        &project,
        Change::AddCanvasNode {
            scene_id: selection.scene_id,
            node: original_canvas_node(),
        },
    );
    let project = apply(
        &service,
        &project,
        Change::UpsertCameraReframe {
            reframe: motionwright_domain::CameraReframe {
                id: Uuid::from_u128(0xB406),
                scene_id: selection.scene_id,
                component_version: motionwright_domain::CAMERA_REFRAME_VERSION,
                start_center_x: 960.0,
                start_center_y: 540.0,
                end_center_x: 1040.0,
                end_center_y: 540.0,
                duration_frames: 30,
            },
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize saved camera reframes",
    );
}

#[test]
fn measured_tables_without_bindings_are_passive_but_bound_scene_values_are_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let service = StudioService::open(dir.path().join("source.sqlite")).unwrap();
    let (project, selection, _) = fixture(&service, dir.path());
    let (project, source_asset_id) = add_asset(
        &service,
        &project,
        dir.path(),
        "original-data.json",
        b"{\"count\":5}",
        "application/json",
    );
    let table = motionwright_domain::MeasuredTable {
        schema: motionwright_domain::MEASURED_DATA_SCHEMA.into(),
        id: Uuid::from_u128(0xB407),
        label: "Original source table".into(),
        version: 1,
        source_asset_id,
        source_sha256: sha256(b"{\"count\":5}"),
        rows: vec![motionwright_domain::MeasuredDatum {
            key: "count".into(),
            label: "Count".into(),
            milli: 5_000,
            unit: motionwright_domain::MeasuredUnit::Count,
        }],
    };
    let table_id = table.id;
    let project = apply(&service, &project, Change::UpsertMeasuredTable { table });
    prepare_saved_hyperframes_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        &selection,
    )
    .unwrap();
    let mut node = original_canvas_node();
    node.kind = "text".into();
    node.text = Some("5".into());
    let node_id = node.id;
    let project = apply(
        &service,
        &project,
        Change::AddCanvasNode {
            scene_id: selection.scene_id,
            node,
        },
    );
    let project = apply(
        &service,
        &project,
        Change::UpsertMeasuredBinding {
            binding_id: Uuid::from_u128(0xB408),
            scene_id: selection.scene_id,
            node_id,
            table_id,
            datum_key: "count".into(),
            view: motionwright_domain::MeasuredView::Counter { decimals: 0 },
        },
    );
    assert_known_unrealized(
        &service,
        &project,
        &selection,
        "HyperFrames source cannot realize saved measured data bindings",
    );
}
