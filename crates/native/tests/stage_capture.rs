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
    // In-memory domain mutations do not advance the external SQLite CAS.
    // A native film test must explicitly pin a previously observed, positive
    // synthetic source revision; production validation still rejects zero.
    p.revision = 7;
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

#[test]
fn registered_reference_board_and_ordered_flow_keep_exact_sources_in_film() {
    use semwright_motion_authoring::{MotionEasing, Primitive, SpatialIntent, StartAnchor};

    let (mut p, _, _, _) = fixture();
    let original_stage = p.scenes[0].nodes.clone();
    let first = p.production_design.captures[0].clone();
    p.revision += 1;
    let revision = p.revision;
    p.apply_change(&Change::AddClaim {
        text: "A second explicitly synthetic first-party state.".into(),
        source: Some(SourceReference::ProjectRevision { revision }),
        context: "synthetic-first-party-capture-validation".into(),
        source_revision: Some("synthetic fixture two".into()),
    })
    .unwrap();
    let mut second = first.clone();
    second.id = Uuid::new_v4();
    second.claim_id = p.brief.claims.last().unwrap().id;
    second.source_revision = revision;
    second.screenshot_asset_id = Uuid::new_v4();
    second.manifest_asset_id = Uuid::new_v4();
    second.fixture_state_asset_id = Uuid::new_v4();
    second.screenshot_sha256 = "e".repeat(64);
    second.screenshot_repeat_sha256 = second.screenshot_sha256.clone();
    second.manifest_sha256 = "f".repeat(64);
    second.fixture_state_sha256 = "1".repeat(64);
    for (id, mime, hash) in [
        (
            second.screenshot_asset_id,
            "image/png",
            &second.screenshot_sha256,
        ),
        (
            second.manifest_asset_id,
            "application/json",
            &second.manifest_sha256,
        ),
        (
            second.fixture_state_asset_id,
            "application/json",
            &second.fixture_state_sha256,
        ),
    ] {
        p.apply_change(&Change::AddAsset {
            asset: Asset {
                id,
                name: "Owned synthetic state two".into(),
                media_type: mime.into(),
                content_sha256: Some(hash.clone()),
                source_revision: Some("synthetic fixture two".into()),
            },
        })
        .unwrap();
    }
    p.apply_change(&Change::RegisterCaptureSource {
        record: second.clone(),
    })
    .unwrap();
    p.apply_change(&Change::AddScene {
        name: "Two original reference sources".into(),
        objective: "Authored synthetic state comparison, not execution evidence".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let scene_id = p.scenes.last().unwrap().id;
    for mut profile in p.deliverables.clone() {
        profile.included_scene_ids = vec![scene_id];
        profile.protected_scene_ids.clear();
        p.apply_change(&Change::UpsertDeliverable { profile })
            .unwrap();
    }
    let entry = |source: &CaptureSourceRecord, role, label: &str| CaptureBoardEntry {
        capture_id: source.id,
        screenshot_sha256: source.screenshot_sha256.clone(),
        manifest_sha256: source.manifest_sha256.clone(),
        fixture_state_sha256: source.fixture_state_sha256.clone(),
        role,
        label: label.into(),
        permission_note: "Test author permits this original synthetic reference.".into(),
    };
    let mut board = CaptureBoard {
        id: Uuid::new_v4(),
        scene_id,
        component_version: CAPTURE_BOARD_VERSION,
        mode: CaptureBoardMode::ReferenceBoard,
        entries: [
            entry(&first, CaptureBoardRole::Context, "Initial state"),
            entry(&second, CaptureBoardRole::Evidence, "Later state"),
        ],
        reveal_start_frame: 0,
        reveal_duration_frames: 0,
    };
    let assets = p.assets.clone();
    let captures = p.production_design.captures.clone();
    let claims = p.brief.claims.clone();
    let options = FilmBuildOptions {
        scene_intents: vec![SceneFilmIntent {
            scene_id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::ObjectSpotlight,
        }],
        ..film_options(&p)
    };
    p.apply_change(&Change::UpsertCaptureBoard {
        board: board.clone(),
    })
    .unwrap();
    let ids = p
        .scenes
        .last()
        .unwrap()
        .nodes
        .iter()
        .map(|n| n.id)
        .collect::<Vec<_>>();
    assert_eq!(ids.len(), 7);
    assert_eq!(p.scenes[0].nodes, original_stage);
    for profile in &p.deliverables {
        let segments = build_motion_canvas_segments(&p, profile.id, &options).unwrap();
        assert_eq!(segments.len(), 1);
        let film = &segments[0].film;
        assert_eq!(film.assets.len(), 2);
        let shot = &film.sequences[0].beats[0].shots[0];
        let mut positions = Vec::new();
        for (i, source) in [&first, &second].into_iter().enumerate() {
            let role = format!("image-{}", i + 1);
            let id = format!("node-{}", capture_board_node_id(board.id, &role).simple());
            let tile = shot.subjects.iter().find(|s| s.id == id).unwrap();
            assert!(tile.initially_visible);
            match &tile.content {
                SubjectContent::Image {
                    asset_id,
                    fit,
                    ratio,
                } => {
                    assert_eq!(
                        asset_id,
                        &format!("asset-{}", source.screenshot_asset_id.simple())
                    );
                    assert_eq!(*fit, MediaFit::Contain);
                    assert!((*ratio - 1440.0 / 940.0).abs() < 1e-9);
                }
                _ => panic!("Expected exact registered PNG, not substitute artwork"),
            }
            match &tile.layout {
                SpatialIntent::Fixed { position, .. } => positions.push(*position),
                _ => panic!("Expected fixed board layout"),
            }
        }
        if f64::from(profile.width) / f64::from(profile.height) >= 1.35 {
            assert!(positions[0].x < positions[1].x);
        } else {
            assert!(positions[0].y < positions[1].y);
        }
        assert!(realize(film).is_ok());
    }

    board.mode = CaptureBoardMode::ProductFlowBridge;
    board.entries[0].role = CaptureBoardRole::Before;
    board.entries[1].role = CaptureBoardRole::After;
    board.reveal_start_frame = 30;
    board.reveal_duration_frames = 15;
    let mut bad = board.clone();
    bad.entries.swap(0, 1);
    bad.entries[0].role = CaptureBoardRole::Before;
    bad.entries[1].role = CaptureBoardRole::After;
    assert!(
        p.apply_change(&Change::UpsertCaptureBoard { board: bad })
            .is_err()
    );
    let mut bad = board.clone();
    bad.entries[1].permission_note.clear();
    assert!(
        p.apply_change(&Change::UpsertCaptureBoard { board: bad })
            .is_err()
    );
    let mut bad = board.clone();
    bad.entries[1].manifest_sha256 = "9".repeat(64);
    assert!(
        p.apply_change(&Change::UpsertCaptureBoard { board: bad })
            .is_err()
    );

    p.apply_change(&Change::UpsertCaptureBoard {
        board: board.clone(),
    })
    .unwrap();
    assert_eq!(
        p.scenes
            .last()
            .unwrap()
            .nodes
            .iter()
            .map(|n| n.id)
            .collect::<Vec<_>>(),
        ids
    );
    for profile in &p.deliverables {
        let segments = build_motion_canvas_segments(&p, profile.id, &options).unwrap();
        let film = &segments[0].film;
        let shot = &film.sequences[0].beats[0].shots[0];
        let id = format!(
            "node-{}",
            capture_board_node_id(board.id, "image-2").simple()
        );
        assert!(
            !shot
                .subjects
                .iter()
                .find(|s| s.id == id)
                .unwrap()
                .initially_visible
        );
        for role in ["label-2", "image-2"] {
            let target = format!("node-{}", capture_board_node_id(board.id, role).simple());
            let fade = shot
                .motion
                .iter()
                .find(|m| matches!(&m.primitive,Primitive::FadeIn {target:t} if t==&target))
                .unwrap();
            assert_eq!(fade.easing, MotionEasing::Linear);
            let span = film
                .timing
                .spans
                .iter()
                .find(|s| s.id == fade.span_id)
                .unwrap();
            assert_eq!(span.minimum, RationalTime::new(1, 2).unwrap());
            assert_eq!(span.minimum, span.maximum);
            assert_eq!(
                span.anchor,
                StartAnchor::Absolute {
                    time: RationalTime::new(1, 1).unwrap()
                }
            );
        }
        assert!(realize(film).is_ok());
    }
    let reopened: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
    reopened.validate().unwrap();
    assert_eq!(
        reopened.production_design.capture_boards,
        vec![board.clone()]
    );
    p.apply_change(&Change::RemoveCaptureBoard {
        scene_id,
        board_id: board.id,
    })
    .unwrap();
    assert!(p.scenes.last().unwrap().nodes.is_empty());
    assert_eq!(p.scenes[0].nodes, original_stage);
    assert_eq!(p.assets, assets);
    assert_eq!(p.production_design.captures, captures);
    assert_eq!(p.brief.claims, claims);
}
