//! Real semantic Film image-subject acceptance. The source is an explicitly
//! synthetic, first-party test receipt, not a customer capture or a certified
//! production renderer. The original editable Stage remains unflattened.
use motionwright_domain::*;
use motionwright_native::film::{
    FilmBuildOptions, MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY, SceneFilmIntent,
    build_motion_canvas_segments,
};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, MediaFit, NarrativeRole, SubjectContent, realize};
use uuid::Uuid;

const PNG: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
fn fixture() -> (Project, Uuid, Uuid, Uuid) {
    let mut p = Project::new("First-party source tile in editable Stage Film").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Test original UI".into(),
        objective: "One digest-bound image embedded in a semantic original screen".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let stage_id = Uuid::new_v4();
    let scene_id = p.scenes[0].id;
    p.apply_change(&Change::UpsertProductStage {
        instance_id: stage_id,
        scene_id,
        config: ProductStageConfig {
            author_attestation: "Test author owns this original graphic".into(),
            ..Default::default()
        },
    })
    .unwrap();
    let id_image = Uuid::new_v4();
    let id_manifest = Uuid::new_v4();
    let id_state = Uuid::new_v4();
    for (id, mime, hash) in [
        (id_image, "image/png", PNG),
        (
            id_manifest,
            "application/json",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
        (
            id_state,
            "application/json",
            "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        ),
    ] {
        p.apply_change(&Change::AddAsset {
            asset: Asset {
                id,
                name: "Owned synthetic source".into(),
                media_type: mime.into(),
                content_sha256: Some(hash.into()),
                source_revision: Some("first-party synthetic fixture".into()),
            },
        })
        .unwrap();
    }
    let revision = p.revision;
    p.apply_change(&Change::AddClaim {
        text: "An original first-party Studio screen was shown in a fixture.".into(),
        source: Some(SourceReference::ProjectRevision { revision }),
        context: "synthetic-first-party-capture-validation".into(),
        source_revision: Some("synthetic fixture".into()),
    })
    .unwrap();
    let capture_id = Uuid::new_v4();
    p.apply_change(&Change::RegisterCaptureSource {
        record: CaptureSourceRecord {
            schema: CAPTURE_RECORD_SCHEMA.into(),
            id: capture_id,
            screenshot_asset_id: id_image,
            screenshot_sha256: PNG.into(),
            manifest_asset_id: id_manifest,
            manifest_sha256: "b".repeat(64),
            fixture_state_asset_id: id_state,
            fixture_state_sha256: "c".repeat(64),
            claim_id: p.brief.claims[0].id,
            source_project_id: p.id,
            source_generation: p.generation,
            source_revision: revision,
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
                x: 100,
                y: 80,
                width: 96,
                height: 30,
                mask_color: "#0B0B0B".into(),
            },
            external_requests: 0,
            credential_fields: 0,
            declared_scope: "first-party-synthetic-fixture-only".into(),
        },
    })
    .unwrap();
    (p, stage_id, capture_id, id_image)
}
fn film_options(p: &Project) -> FilmBuildOptions {
    FilmBuildOptions {
        frame_rate: Rate { num: 30, den: 1 },
        font_family: MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: p.scenes[0].id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
    }
}
#[test]
fn original_editable_stage_projects_registered_first_party_png_as_uncropped_semantic_image() {
    let (mut p, stage, capture, image) = fixture();
    let options = film_options(&p);
    let before = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    assert!(before[0].film.assets.is_empty());
    let original_nodes = p.scenes[0].nodes.clone();
    p.apply_change(&Change::BindStageCapture {
        stage_id: stage,
        capture_id: capture,
    })
    .unwrap();
    assert_eq!(p.scenes[0].nodes, original_nodes);
    let segment = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    assert_eq!(segment.len(), 1);
    assert_eq!(segment[0].frame_count, 180);
    assert_eq!(segment[0].film.assets.len(), 1);
    assert_eq!(
        segment[0].film.assets[0].id,
        format!("asset-{}", image.simple())
    );
    assert_eq!(segment[0].film.assets[0].media_type, "image/png");
    let shot = &segment[0].film.sequences[0].beats[0].shots[0];
    let sources = shot
        .subjects
        .iter()
        .filter(|subject| matches!(subject.content, SubjectContent::Image { .. }))
        .collect::<Vec<_>>();
    assert_eq!(sources.len(), 1);
    let tile = sources[0];
    assert_eq!(tile.role, "first-party-synthetic-source-tile");
    assert_eq!(
        tile.id,
        format!(
            "node-{}",
            stage_node_id(stage, "source_capture_tile").simple()
        )
    );
    match &tile.content {
        SubjectContent::Image {
            asset_id,
            fit,
            ratio,
        } => {
            assert_eq!(asset_id, &format!("asset-{}", image.simple()));
            assert_eq!(*fit, MediaFit::Contain);
            assert!((*ratio - 1440.0 / 940.0).abs() < 1e-9);
        }
        _ => panic!("Expected a genuine native image source, not a synthesized rectangle"),
    }
    assert!(
        shot.subjects
            .iter()
            .any(|subject| subject.id == format!("node-{}", stage_node_id(stage, "card").simple()))
    );
    assert!(shot.subjects.iter().any(
        |subject| subject.id == format!("node-{}", stage_node_id(stage, "disclosure").simple())
    ));
    assert!(realize(&segment[0].film).is_ok());
    p.apply_change(&Change::DetachStageCapture { stage_id: stage })
        .unwrap();
    let detached = build_motion_canvas_segments(&p, p.deliverables[0].id, &options).unwrap();
    assert!(detached[0].film.assets.is_empty());
    assert_eq!(p.scenes[0].nodes, original_nodes);
}
