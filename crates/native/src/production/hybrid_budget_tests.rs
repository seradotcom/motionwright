// These exercise the real StudioService, SQLite root and production dispatch
// boundary. The local CLI is only a transport witness. No test creates native
// render evidence or establishes Host/runtime/G2/human acceptance.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RationalTime,
};
use motionwright_service::VoiceImportMetadata;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_native_sdk::types::{Execution, InvocationProvenance};
use std::{
    collections::BTreeSet,
    os::unix::fs::PermissionsExt,
    sync::atomic::{AtomicBool, Ordering},
};

struct Fixture {
    directory: tempfile::TempDir,
    service: StudioService,
    project: Project,
    options: FilmBuildOptions,
    deliverable: Uuid,
    connection: ProductionConnection,
}

fn change(service: &StudioService, project: &mut Project, label: &str, change: Change) {
    *project = service
        .apply(project.id, &project.stamp(), label, &change)
        .unwrap()
        .project;
}

fn original_test_wave(path: &Path) {
    let samples = 288_000_u32;
    let mut bytes = Vec::new();
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(samples * 4 + 36).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48_000_u32.to_le_bytes());
    bytes.extend_from_slice(&192_000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&(samples * 4).to_le_bytes());
    bytes.resize(44 + samples as usize * 4, 0);
    fs::write(path, bytes).unwrap();
}

fn connection(directory: &Path, resource: String) -> ProductionConnection {
    let executable = directory.join("hybrid-transport-witness");
    let socket = directory.join("broker.sock");
    let session_file = directory.join("session.json");
    let output_root = directory.join("native-output");
    fs::create_dir(&output_root).unwrap();
    fs::write(&socket, b"").unwrap();
    fs::write(&session_file, b"{}").unwrap();
    let mut script = String::from("#!/bin/sh\ncase \"$7\" in\n");
    for (command, provider) in [
        ("driver.motion-canvas.render.start", "driver:motion-canvas"),
        (
            "driver.manim-community.static.render.start",
            "driver:manim-community",
        ),
        ("driver.mlt-video.frames.encode", "driver:mlt-video"),
    ] {
        let envelope = Envelope {
            ok: true,
            request_id: "budget-transport-witness".into(),
            command: command.into(),
            data: Some(json!({"job_ref":"test-only-native-job","state":"queued"})),
            error: None,
            execution: Execution {
                backend: provider.into(),
                duration_ms: 1,
                policy_decision: "allow".into(),
                fallbacks_attempted: vec![],
                dry_run: false,
                provenance: Some(InvocationProvenance {
                    provider: provider.into(),
                    source: SourceKind::Driver,
                    provider_version: "test".into(),
                    capability_version: "test".into(),
                    descriptor_sha256: "ad".repeat(32),
                    untrusted_metadata: false,
                    catalog_revision: 1,
                    execution_provider: Some(provider.into()),
                    provider_generation: Some(1),
                }),
            },
            warnings: vec![],
        };
        let encoded = serde_json::to_string(&envelope)
            .unwrap()
            .replace('\'', "'\\''");
        script.push_str(&format!("  {command}) printf '%s\\n' '{encoded}' ;;\n"));
    }
    script.push_str("  *) exit 2 ;;\nesac\n");
    fs::write(&executable, script).unwrap();
    for (path, mode) in [
        (&executable, 0o700),
        (&socket, 0o600),
        (&session_file, 0o600),
        (&output_root, 0o700),
    ] {
        fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
    }
    ProductionConnection {
        schema: CONNECTION_SCHEMA.into(),
        executable_sha256: sha256_file(&executable).unwrap(),
        executable,
        socket,
        session_file,
        output_root,
        resource,
    }
}

fn fixture() -> Fixture {
    let directory = tempfile::tempdir().unwrap();
    let service = StudioService::open(directory.path().join("hybrid.sqlite3")).unwrap();
    let mut project = service
        .create_project("Hybrid root accounting fixture")
        .unwrap();
    let mut visual_language = project.visual_language.clone();
    for token in &mut visual_language.palette {
        if token.name.eq_ignore_ascii_case("background")
            || token.name.eq_ignore_ascii_case("surface")
        {
            token.name.make_ascii_lowercase();
            token.value = "#000000".into();
        }
    }
    change(
        &service,
        &mut project,
        "fixture-matte",
        Change::SetVisualLanguage { visual_language },
    );
    for index in 0..3 {
        change(
            &service,
            &mut project,
            &format!("fixture-scene-{index}"),
            Change::AddScene {
                name: format!("Contribution {index}"),
                objective: "Budget-bound native accounting fixture".into(),
                duration_seconds: 2,
            },
        );
        let scene_id = project.scenes.last().unwrap().id;
        if index == 1 {
            change(
                &service,
                &mut project,
                "fixture-static-manim",
                Change::SetSceneRenderer {
                    scene_id,
                    renderer: RendererKind::ManimCommunity,
                },
            );
        }
        let node = CanvasNode {
            id: Uuid::now_v7(),
            name: format!("Rectangle {index}"),
            kind: "rectangle".into(),
            parent_id: None,
            x: 400.0 + index as f64 * 350.0,
            y: 420.0,
            width: 180.0,
            height: 140.0,
            rotation_deg: 0.0,
            opacity: 1.0,
            text: None,
            coordinate_space: CoordinateSpace::ProjectPixels,
            z_index: 0,
            style: NodeStyle {
                fill: Some(["#FF0000", "#00FF00", "#0000FF"][index].into()),
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
        change(
            &service,
            &mut project,
            &format!("fixture-node-{index}"),
            Change::AddCanvasNode { scene_id, node },
        );
    }
    let wav = directory.path().join("synthetic-test-only-original.wav");
    original_test_wave(&wav);
    project = service
        .import_voice_file(
            project.id,
            &project.stamp(),
            "fixture-voice",
            &wav,
            VoiceImportMetadata {
                name: "test-only.wav".into(),
                media_type: "audio/wav".into(),
                label: "Synthetic test input; no production acceptance".into(),
            },
        )
        .unwrap()
        .project;
    let mut profile = project
        .deliverables
        .iter()
        .find(|row| row.name == "Master 16:9")
        .unwrap()
        .clone();
    let deliverable = profile.id;
    profile.width = 1920;
    profile.height = 1080;
    profile.frame_rate = RationalTime::new(30, 1).unwrap();
    profile.voice_track_id = Some(project.audio.voice_tracks[0].id);
    profile.captions = false;
    profile.burn_in_captions = false;
    change(
        &service,
        &mut project,
        "fixture-master",
        Change::UpsertDeliverable { profile },
    );
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1).unwrap(),
        font_family: crate::film::MOTION_CANVAS_FONT_FAMILY.into(),
        mono_font_family: crate::film::MOTION_CANVAS_MONO_FONT_FAMILY.into(),
        scene_intents: project
            .scenes
            .iter()
            .map(|scene| crate::film::SceneFilmIntent {
                scene_id: scene.id,
                role: NarrativeRole::Evidence,
                archetype: Archetype::Statement,
            })
            .collect(),
    };
    let connection = connection(directory.path(), project.resource_key());
    Fixture {
        directory,
        service,
        project,
        options,
        deliverable,
        connection,
    }
}

fn motion_args() -> Value {
    json!({"expected_fingerprint":"a".repeat(64),"profile":{
        "first_frame":0,"end_frame_exclusive":60,"scale":"full","transparent":false,"timeout_ms":300_000}})
}
fn original_scope(f: &Fixture) -> (ProductionCoordinator, ProductionCoordinator) {
    let owner = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    let scoped = owner
        .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
        .unwrap();
    (owner, scoped)
}
fn test_mlt_scope(scoped: &ProductionCoordinator) -> ProductionCoordinator {
    let mut continuation = scoped.clone();
    let mut encodes = BTreeMap::new();
    for index in [0, 2] {
        encodes.insert(
            format!("test-only-{index}/artifact-manifest.json"),
            MltEncodeBudget {
                manifest_sha256: "a".repeat(64),
                frames: 60,
                output_path: format!(
                    "{}-mc-{index}.mkv",
                    scoped.active_hybrid_budget.as_ref().unwrap().plan.namespace
                ),
                max_bytes: MEDIA_MAX_BYTES,
            },
        );
    }
    continuation.active_hybrid_budget.as_mut().unwrap().phase =
        HybridPhase::Mlt(HybridMltBinding {
            source_paths: encodes
                .values()
                .map(|row| row.output_path.clone())
                .collect(),
            encodes,
        });
    continuation
}

#[test]
fn hybrid_root_reserves_all_real_frame_passes_and_original_observations_before_effects() {
    let f = fixture();
    let (_, scoped) = original_scope(&f);
    let root = scoped.active_hybrid_budget.as_ref().unwrap();
    let stored = f
        .service
        .root_operation_budget(f.project.id, root.root.root_id)
        .unwrap();
    assert_eq!(stored.spent, RootBudgetUnits::default());
    assert_eq!(stored.limits.render_frames, 660); // 120+60+120+180+180, no fake third encode.
    assert_eq!(stored.limits.model_calls, 0);
    assert!(
        f.service
            .production_receipts(f.project.id, 10)
            .unwrap()
            .is_empty()
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);
    let mut bytes = serde_json::to_vec_pretty(&f.project).unwrap();
    bytes.push(b'\n');
    assert_eq!(root.plan.project_sha256, hex::encode(Sha256::digest(bytes)));
    assert_eq!(
        root.plan.source_plan["contributions"][1]["renderer"],
        "manim_static"
    );
    assert_eq!(
        root.plan.source_plan["contributions"][2]["output_start_frame"],
        120
    );

    let mc = root
        .units_for("driver.motion-canvas.render.start", &motion_args(), true)
        .unwrap();
    let manim_args = json!({"plan":root.plan.manim_plan,"profile":root.plan.manim_profile});
    let manim = root
        .units_for(
            "driver.manim-community.static.render.start",
            &manim_args,
            true,
        )
        .unwrap();
    let continued = test_mlt_scope(&scoped);
    let mlt = continued.active_hybrid_budget.as_ref().unwrap();
    let encode = mlt.units_for("driver.mlt-video.frames.encode", &json!({
        "root":"output","manifest_path":"test-only-0/artifact-manifest.json",
        "expected_manifest_sha256":"a".repeat(64),"output_path":format!("{}-mc-0.mkv", root.plan.namespace),
        "max_bytes":MEDIA_MAX_BYTES}), true).unwrap();
    let timeline = mlt
        .units_for(
            "driver.mlt-video.render.start",
            &json!({
        "profile":"lossless-video-only","output":format!("{}-visual.mkv", root.plan.namespace)}),
            true,
        )
        .unwrap();
    let mux = mlt.units_for("driver.mlt-video.av.mux", &json!({
        "video_root":"output","video_path":format!("{}-visual.mkv", root.plan.namespace),
        "audio_root":"output","audio_path":format!("{}-source.wav", root.plan.namespace),
        "audio_sha256":root.plan.source_audio_sha256,"output_path":format!("{}-master.mp4", root.plan.namespace),
        "frame_count":180,"width":1920,"height":1080,"fps_num":30,"fps_den":1,
        "sample_rate":48_000,"channels":2,"profile":"h264-aac-mp4","max_bytes":MASTER_MAX_BYTES}), true).unwrap();
    assert!(
        encode.output_bytes > MEDIA_MAX_BYTES,
        "FFV1 includes its complete original PNG observation"
    );
    assert!(timeline.output_bytes > MLT_MASTER_MAX_BYTES);
    assert!(
        mux.output_bytes > MASTER_MAX_BYTES * 2,
        "MP4, decoded PCM and original PNG observation all count"
    );
    assert_eq!(
        2 * mc.render_frames
            + manim.render_frames
            + 2 * encode.render_frames
            + timeline.render_frames
            + mux.render_frames,
        stored.limits.render_frames
    );
    assert!(
        2 * mc.output_bytes
            + manim.output_bytes
            + 2 * encode.output_bytes
            + timeline.output_bytes
            + mux.output_bytes
            <= stored.limits.output_bytes
    );
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

#[tokio::test]
async fn hybrid_reuses_one_sqlite_root_across_actual_mc_manim_and_mlt_dispatch_boundaries() {
    let f = fixture();
    let (owner, scoped) = original_scope(&f);
    let root_id = scoped.active_hybrid_budget.as_ref().unwrap().root.root_id;
    let motion_scope = scoped
        .with_motion_scene_boundary_scope(
            &f.project,
            f.deliverable,
            &f.options,
            &[f.project.scenes[0].id, f.project.scenes[2].id],
        )
        .unwrap();
    assert!(motion_scope.active_motion_budget.is_none());
    assert_eq!(
        motion_scope
            .active_hybrid_budget
            .as_ref()
            .unwrap()
            .root
            .root_id,
        root_id
    );
    let first = motion_scope
        .execute(
            f.project.id,
            &f.project.stamp(),
            "test-hybrid-mc-start",
            "driver.motion-canvas.render.start",
            motion_args(),
            true,
        )
        .await
        .unwrap();
    assert_eq!(first["replayed"], false);
    let original = f
        .service
        .root_operation_budget(f.project.id, root_id)
        .unwrap();
    assert_eq!(original.spent.render_frames, 60);
    assert_eq!(original.spent.attempts, 1);
    let replay = motion_scope
        .execute(
            f.project.id,
            &f.project.stamp(),
            "test-hybrid-mc-start",
            "driver.motion-canvas.render.start",
            motion_args(),
            true,
        )
        .await
        .unwrap();
    assert_eq!(replay["replayed"], true);
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, root_id)
            .unwrap(),
        original
    );

    let manim_scope = scoped
        .with_scene_without_film_options(
            &f.project,
            f.project.scenes[1].id,
            RendererKind::ManimCommunity,
        )
        .unwrap();
    let original_plan = &scoped.active_hybrid_budget.as_ref().unwrap().plan;
    manim_scope
        .execute(
            f.project.id,
            &f.project.stamp(),
            "test-hybrid-manim-start",
            "driver.manim-community.static.render.start",
            json!({"plan":original_plan.manim_plan,"profile":original_plan.manim_profile}),
            true,
        )
        .await
        .unwrap();
    let continued = test_mlt_scope(&scoped);
    continued.execute(f.project.id, &f.project.stamp(), "test-hybrid-encode",
        "driver.mlt-video.frames.encode", json!({"root":"output",
            "manifest_path":"test-only-0/artifact-manifest.json","expected_manifest_sha256":"a".repeat(64),
            "output_path":format!("{}-mc-0.mkv", original_plan.namespace),"max_bytes":MEDIA_MAX_BYTES}), true).await.unwrap();
    let debited = f
        .service
        .root_operation_budget(f.project.id, root_id)
        .unwrap();
    assert_eq!(debited.spent.attempts, 3);
    assert_eq!(debited.spent.render_frames, 180);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 3);
    let receipts = f.service.production_receipts(f.project.id, 20).unwrap();
    assert_eq!(receipts.len(), 6);
    assert!(
        owner
            .execute(
                f.project.id,
                &f.project.stamp(),
                "unscoped-native-side-door",
                "driver.motion-canvas.render.start",
                motion_args(),
                true
            )
            .await
            .is_err()
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 3);
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, root_id)
            .unwrap(),
        debited
    );
    assert_eq!(
        f.service.production_receipts(f.project.id, 20).unwrap(),
        receipts
    );
}

#[test]
fn an_existing_mc_root_cannot_be_replaced_by_a_hybrid_label_or_larger_limits() {
    let f = fixture();
    let owner = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    let existing = owner
        .with_motion_scene_boundary_scope(
            &f.project,
            f.deliverable,
            &f.options,
            &[f.project.scenes[0].id, f.project.scenes[2].id],
        )
        .unwrap();
    let scope = existing.active_motion_budget.as_ref().unwrap();
    assert_eq!(
        scope.mlt_route, None,
        "The existing all_motion guard remains intact"
    );
    let before = f
        .service
        .root_operation_budget(f.project.id, scope.root_id)
        .unwrap();
    assert!(
        owner
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    let fresh = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    assert!(
        fresh
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, scope.root_id)
            .unwrap(),
        before
    );
    assert!(
        f.service
            .production_receipts(f.project.id, 10)
            .unwrap()
            .is_empty()
    );
    assert_eq!(existing.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn hybrid_unknown_keeps_spent_capacity_and_original_receipts_after_process_reopen() {
    let mut f = fixture();
    fs::write(&f.connection.executable, "#!/bin/sh\nprintf '{not-json'\n").unwrap();
    f.connection.executable_sha256 = sha256_file(&f.connection.executable).unwrap();
    let (owner, scoped) = original_scope(&f);
    let root_id = scoped.active_hybrid_budget.as_ref().unwrap().root.root_id;
    let error = scoped
        .execute(
            f.project.id,
            &f.project.stamp(),
            "hybrid-unknown-original",
            "driver.motion-canvas.render.start",
            motion_args(),
            true,
        )
        .await
        .unwrap_err();
    assert!(!error.outcome_known);
    let spent = f
        .service
        .root_operation_budget(f.project.id, root_id)
        .unwrap();
    assert_eq!(spent.spent.render_frames, 60);
    let receipts = f.service.production_receipts(f.project.id, 20).unwrap();
    assert!(receipts.iter().any(|row| row.stage == "outcome_unknown"));
    let second = scoped
        .execute(
            f.project.id,
            &f.project.stamp(),
            "hybrid-unknown-original",
            "driver.motion-canvas.render.start",
            motion_args(),
            true,
        )
        .await
        .unwrap_err();
    assert!(!second.outcome_known);
    assert!(
        scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                "renamed-hybrid-unknown",
                "driver.motion-canvas.render.start",
                motion_args(),
                true
            )
            .await
            .is_err()
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    drop(scoped);
    drop(owner);
    drop(f.service);
    let reopened = StudioService::open(f.directory.path().join("hybrid.sqlite3")).unwrap();
    let fresh = ProductionCoordinator::new(reopened.clone(), f.connection).unwrap();
    assert!(
        fresh
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    assert_eq!(
        reopened
            .root_operation_budget(f.project.id, root_id)
            .unwrap(),
        spent
    );
    assert_eq!(
        reopened.production_receipts(f.project.id, 20).unwrap(),
        receipts
    );
    assert_eq!(fresh.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn hybrid_dispatch_rechecks_a_peer_commit_before_the_durable_debit() {
    let f = fixture();
    let (_, mut scoped) = original_scope(&f);
    let root_id = scoped.active_hybrid_budget.as_ref().unwrap().root.root_id;
    let peer = StudioService::open(f.directory.path().join("hybrid.sqlite3")).unwrap();
    let project = f.project.clone();
    let fired = Arc::new(AtomicBool::new(false));
    let observed = fired.clone();
    scoped.before_dispatch_admission = Some(Arc::new(move || {
        assert!(!observed.swap(true, Ordering::SeqCst));
        peer.apply(
            project.id,
            &project.stamp(),
            "peer-edit-during-hybrid-preflight",
            &Change::RenameProject {
                title: "Changed before native admission".into(),
            },
        )
        .unwrap();
    }));
    assert!(
        scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                "stale-hybrid-native",
                "driver.motion-canvas.render.start",
                motion_args(),
                true
            )
            .await
            .is_err()
    );
    assert!(fired.load(Ordering::SeqCst));
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, root_id)
            .unwrap()
            .spent,
        RootBudgetUnits::default()
    );
    assert!(
        f.service
            .production_receipts(f.project.id, 20)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn unsupported_hybrid_clock_and_static_source_are_refused_before_any_root_or_native_effect() {
    let mut f = fixture();
    let scene_id = f.project.scenes[1].id;
    change(
        &f.service,
        &mut f.project,
        "unsupported-short-manim",
        Change::SetSceneDuration {
            scene_id,
            duration: RationalTime::new(1, 1).unwrap(),
        },
    );
    let owner = ProductionCoordinator::new(f.service.clone(), f.connection).unwrap();
    assert!(
        owner
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    assert_eq!(owner.client.execute_calls.load(Ordering::SeqCst), 0);
    assert!(
        f.service
            .production_receipts(f.project.id, 20)
            .unwrap()
            .is_empty()
    );
    let db = rusqlite::Connection::open(f.directory.path().join("hybrid.sqlite3")).unwrap();
    let count: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM production_operation_roots",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}

#[test]
fn preexisting_blender_unknown_is_preserved_and_cannot_fund_a_fresh_hybrid_root() {
    let f = fixture();
    let command = "driver.blender.object.create";
    let args = json!({"name":"test-only-retained-product-original"});
    for stage in ["dispatching", "outcome_unknown"] {
        f.service
            .append_production_receipt(ProductionReceiptInput {
                project_id: f.project.id,
                generation: f.project.generation,
                revision: f.project.revision,
                request_id: "test-only-blender-original-unknown".into(),
                request_sha256: request_digest(command, &args).unwrap(),
                command: command.into(),
                stage: stage.into(),
                payload: json!({"mutation":true,
                "synthetic_receipt_fixture":true,"connection":f.connection.identity().unwrap()}),
            })
            .unwrap();
    }
    let receipts = f.service.production_receipts(f.project.id, 20).unwrap();
    let owner = ProductionCoordinator::new(f.service.clone(), f.connection).unwrap();
    assert!(
        owner
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    assert_eq!(owner.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        f.service.production_receipts(f.project.id, 20).unwrap(),
        receipts
    );
    assert!(receipts.iter().any(|row| row.stage == "outcome_unknown"));
    let db = rusqlite::Connection::open(f.directory.path().join("hybrid.sqlite3")).unwrap();
    let count: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM production_operation_roots",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}

#[tokio::test]
async fn closed_hybrid_scope_rejects_other_providers_and_altered_manim_plan_before_debit() {
    let f = fixture();
    let (_, scoped) = original_scope(&f);
    let scope = scoped.active_hybrid_budget.as_ref().unwrap();
    let before = f
        .service
        .root_operation_budget(f.project.id, scope.root.root_id)
        .unwrap();
    assert!(
        scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                "hybrid-arbitrary-provider",
                "driver.blender.object.create",
                json!({"name":"must-not-execute"}),
                true
            )
            .await
            .is_err()
    );
    let mut forged = json!({"plan":scope.plan.manim_plan,"profile":scope.plan.manim_profile});
    forged["plan"]["clock"]["frame_count"] = json!(600);
    assert!(
        scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                "hybrid-larger-manim-source",
                "driver.manim-community.static.render.start",
                forged,
                true
            )
            .await
            .is_err()
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, scope.root.root_id)
            .unwrap(),
        before
    );
    assert!(
        f.service
            .production_receipts(f.project.id, 20)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn deterministic_hybrid_ids_reuse_the_original_stamp_and_change_only_with_saved_source() {
    let mut f = fixture();
    let owner = ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
    let before = HybridBudgetPlan::build(
        &owner,
        f.project.id,
        &f.project.stamp(),
        f.deliverable,
        &f.options,
    )
    .unwrap();
    let repeated = HybridBudgetPlan::build(
        &owner,
        f.project.id,
        &f.project.stamp(),
        f.deliverable,
        &f.options,
    )
    .unwrap();
    assert_eq!(before.request_prefix, repeated.request_prefix);
    assert_eq!(before.source_plan, repeated.source_plan);
    assert_eq!(before.request_prefix.len(), 74);
    assert!(format!("{}-manim", before.request_prefix).len() <= 96);
    assert!(format!("{}-mc:motion:1:font-project-inspect", before.request_prefix).len() <= 256);
    let original = owner
        .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
        .unwrap();
    let original_root = original.active_hybrid_budget.as_ref().unwrap().root.clone();
    assert!(
        owner
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    change(
        &f.service,
        &mut f.project,
        "explicit-saved-revision-edit",
        Change::RenameProject {
            title: "New saved revision with an unchanged finite native profile".into(),
        },
    );
    let edited = owner
        .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
        .unwrap();
    let next = edited.active_hybrid_budget.as_ref().unwrap();
    assert_ne!(next.plan.request_prefix, before.request_prefix);
    assert_ne!(next.root.operation_key, original_root.operation_key);
    assert_ne!(next.root.root_id, original_root.root_id);
    assert_eq!(next.root.revision, f.project.revision);
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, original_root.root_id)
            .unwrap(),
        original_root
    );
    assert_eq!(owner.client.execute_calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn an_old_unknown_blocks_the_root_of_an_explicit_new_saved_revision() {
    let mut f = fixture();
    fs::write(&f.connection.executable, "#!/bin/sh\nprintf '{not-json'\n").unwrap();
    f.connection.executable_sha256 = sha256_file(&f.connection.executable).unwrap();
    let (owner, scoped) = original_scope(&f);
    let root = scoped.active_hybrid_budget.as_ref().unwrap();
    let root_id = root.root.root_id;
    let first_prefix = root.plan.request_prefix.clone();
    assert!(
        !scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                "old-revision-hybrid-unknown",
                "driver.motion-canvas.render.start",
                motion_args(),
                true
            )
            .await
            .unwrap_err()
            .outcome_known
    );
    let before = f
        .service
        .root_operation_budget(f.project.id, root_id)
        .unwrap();
    let receipts = f.service.production_receipts(f.project.id, 20).unwrap();
    change(
        &f.service,
        &mut f.project,
        "edit-with-original-unknown-retained",
        Change::RenameProject {
            title: "Saved revision does not resolve an older native UNKNOWN".into(),
        },
    );
    let plan = HybridBudgetPlan::build(
        &owner,
        f.project.id,
        &f.project.stamp(),
        f.deliverable,
        &f.options,
    )
    .unwrap();
    assert_ne!(plan.request_prefix, first_prefix);
    assert!(
        owner
            .with_native_hybrid_scope(f.project.id, &f.project.stamp(), f.deliverable, &f.options)
            .is_err()
    );
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, root_id)
            .unwrap(),
        before
    );
    assert_eq!(
        f.service.production_receipts(f.project.id, 20).unwrap(),
        receipts
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn original_wave_is_charged_before_staging_and_survives_the_first_native_unknown() {
    let mut f = fixture();
    fs::write(&f.connection.executable, "#!/bin/sh\nprintf '{not-json'\n").unwrap();
    f.connection.executable_sha256 = sha256_file(&f.connection.executable).unwrap();
    let (_, scoped) = original_scope(&f);
    let scope = scoped.active_hybrid_budget.as_ref().unwrap();
    let path = f
        .connection
        .output_root
        .join(format!("{}-source.wav", scope.plan.namespace));
    assert!(
        !path.exists(),
        "Creating a root must not copy input before a debit exists"
    );
    let request = format!("{}-mc:motion:0:render-start", scope.plan.request_prefix);
    let base = scope
        .units_for("driver.motion-canvas.render.start", &motion_args(), true)
        .unwrap();
    let error = scoped
        .execute(
            f.project.id,
            &f.project.stamp(),
            &request,
            "driver.motion-canvas.render.start",
            motion_args(),
            true,
        )
        .await
        .unwrap_err();
    assert!(!error.outcome_known);
    let spent = f
        .service
        .root_operation_budget(f.project.id, scope.root.root_id)
        .unwrap();
    assert_eq!(spent.spent.attempts, 1);
    assert_eq!(spent.spent.render_frames, base.render_frames);
    assert_eq!(
        spent.spent.output_bytes,
        base.output_bytes + scope.plan.source_audio_bytes
    );
    let copied = fs::read(&path).unwrap();
    assert_eq!(copied, fs::read(&scope.plan.source_audio_path).unwrap());
    assert_eq!(
        hex::encode(Sha256::digest(&copied)),
        scope.plan.source_audio_sha256
    );
    let receipts = f.service.production_receipts(f.project.id, 20).unwrap();
    assert!(receipts.iter().any(|row| row.stage == "outcome_unknown"));
    assert!(
        !scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                &request,
                "driver.motion-canvas.render.start",
                motion_args(),
                true
            )
            .await
            .unwrap_err()
            .outcome_known
    );
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 1);
    assert_eq!(fs::read(&path).unwrap(), copied);
    assert_eq!(
        f.service
            .root_operation_budget(f.project.id, scope.root.root_id)
            .unwrap(),
        spent
    );
    assert_eq!(
        f.service.production_receipts(f.project.id, 20).unwrap(),
        receipts
    );
}

#[tokio::test]
async fn conflicting_original_wave_is_preserved_and_never_enters_sdk_after_charged_staging() {
    let f = fixture();
    let (_, scoped) = original_scope(&f);
    let scope = scoped.active_hybrid_budget.as_ref().unwrap();
    let path = f
        .connection
        .output_root
        .join(format!("{}-source.wav", scope.plan.namespace));
    let original = b"preserve-this-original-even-if-it-is-not-the-admitted-wave";
    fs::write(&path, original).unwrap();
    let request = format!("{}-mc:motion:0:render-start", scope.plan.request_prefix);
    assert!(
        scoped
            .execute(
                f.project.id,
                &f.project.stamp(),
                &request,
                "driver.motion-canvas.render.start",
                motion_args(),
                true
            )
            .await
            .is_err()
    );
    assert_eq!(fs::read(&path).unwrap(), original);
    assert_eq!(scoped.client.execute_calls.load(Ordering::SeqCst), 0);
    let spent = f
        .service
        .root_operation_budget(f.project.id, scope.root.root_id)
        .unwrap()
        .spent;
    assert_eq!(spent.attempts, 1);
    assert_eq!(spent.render_frames, 60);
    assert!(spent.output_bytes >= scope.plan.source_audio_bytes);
    let receipts = f.service.production_receipts(f.project.id, 20).unwrap();
    assert!(receipts.iter().any(|row| row.stage == "dispatching"));
    assert!(receipts.iter().any(|row| row.stage == "failed_known"));
}
