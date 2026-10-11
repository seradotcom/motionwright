use motionwright_domain::{
    BlendMode, CanvasKeyframe, CanvasNode, Change, CoordinateSpace, MotionInterpolation,
    MotionProperty, NodeStyle, RationalTime, RevisionStamp,
};
use motionwright_native::{
    film::{FilmBuildOptions, SceneFilmIntent},
    production::{
        MltAudioArtifact, MltAvMasterRequest, MotionCanvasRenderEvidence, ProductionConnection,
        ProductionCoordinator,
    },
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use serde_json::json;
use std::{
    collections::BTreeSet,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn usage() -> ! {
    eprintln!(
        "usage: native-render-e2e <seed DATABASE | render DATABASE CONNECTION EVIDENCE_JSON | master DATABASE CONNECTION MOTION_EVIDENCE AUDIO_RELATIVE AUDIO_SHA256 EVIDENCE_JSON>"
    );
    std::process::exit(2);
}

fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

fn text_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> String {
    args.next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage())
}

fn fixed_group() -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Native production frame".into(),
        kind: "group".into(),
        parent_id: None,
        x: 260.0,
        y: 390.0,
        width: 1_400.0,
        height: 300.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle::default(),
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

fn text_node(parent_id: Uuid) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Native production title".into(),
        kind: "text".into(),
        parent_id: Some(parent_id),
        x: 480.0,
        y: 460.0,
        width: 960.0,
        height: 160.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Motionwright native render".into()),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some("#F5F5F2".into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: Some("Instrument Sans Variable".into()),
            font_size: Some(64.0),
            font_weight: Some(700),
            line_height: Some(1.05),
            blend_mode: BlendMode::Normal,
        },
        relations: vec![],
        property_locks: BTreeSet::new(),
        keyframes: vec![],
    }
}

/// Visible unparented rectangle: the native Film contract now maps this
/// exact paired 0→base position transition via a Semwright Settle primitive.
fn real_linear_motion_tile() -> CanvasNode {
    let mut node = CanvasNode {
        id: Uuid::now_v7(),
        name: "Native linear motion tile".into(),
        kind: "rectangle".into(),
        parent_id: None,
        x: 790.0,
        y: 750.0,
        width: 160.0,
        height: 90.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 2,
        style: NodeStyle {
            fill: Some("#F5F5F2".into()),
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
    for (at, x, y) in [
        (RationalTime::ZERO, 640.0, 750.0),
        (
            RationalTime::new(1, 1).expect("exact frame"),
            node.x,
            node.y,
        ),
    ] {
        node.keyframes.extend([
            CanvasKeyframe {
                at,
                property: MotionProperty::X,
                value: x,
                interpolation: MotionInterpolation::Linear,
            },
            CanvasKeyframe {
                at,
                property: MotionProperty::Y,
                value: y,
                interpolation: MotionInterpolation::Linear,
            },
        ]);
    }
    node
}

fn seed(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("native-render-e2e seed database must start empty".into());
    }

    let initial = service.create_project("Native render E2E")?;
    let with_scene = service
        .apply(
            initial.id,
            &RevisionStamp::from(&initial),
            "native-render-seed-scene",
            &Change::AddScene {
                name: "Verified Motion Canvas".into(),
                objective: "Exercise Motionwright through Semwright Driver Host".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = with_scene.scenes[0].id;
    let group = fixed_group();
    let group_id = group.id;
    let with_group = service
        .apply(
            with_scene.id,
            &RevisionStamp::from(&with_scene),
            "native-render-seed-group",
            &Change::AddCanvasNode {
                scene_id,
                node: group,
            },
        )?
        .project;
    let with_title = service
        .apply(
            with_group.id,
            &RevisionStamp::from(&with_group),
            "native-render-seed-title",
            &Change::AddCanvasNode {
                scene_id,
                node: text_node(group_id),
            },
        )?
        .project;
    let project = service
        .apply(
            with_title.id,
            &RevisionStamp::from(&with_title),
            "native-render-seed-linear-position",
            &Change::AddCanvasNode {
                scene_id,
                node: real_linear_motion_tile(),
            },
        )?
        .project;
    let deliverable = project
        .deliverables
        .first()
        .ok_or("seeded project has no deliverable")?;

    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id": project.id,
            "resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "scene_id": scene_id,
            "deliverable_id": deliverable.id,
            "width": deliverable.width,
            "height": deliverable.height
        }))?
    );
    Ok(())
}

async fn render(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("native-render-e2e expected exactly one seeded project".into());
    }
    let project = projects.into_iter().next().expect("length checked");
    let scene = project
        .scenes
        .first()
        .ok_or("seeded project has no scene")?;
    let deliverable = project
        .deliverables
        .first()
        .ok_or("seeded project has no deliverable")?;

    let expected_frames = expected_fixture_frames(&project)?;
    if expected_frames == 60 && (deliverable.width, deliverable.height) != (1920, 1080) {
        return Err("native-render-e2e expected the canonical 1920x1080 master".into());
    }

    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("owner-provisioned connection is not bound to the seeded project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1)?,
        font_family: "Instrument Sans Variable".into(),
        mono_font_family: "IBM Plex Mono".into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: scene.id,
            role: if expected_frames == 180 {
                NarrativeRole::Reveal
            } else {
                NarrativeRole::Mechanism
            },
            archetype: if expected_frames == 180 {
                Archetype::ObjectSpotlight
            } else {
                Archetype::Statement
            },
        }],
    };
    let result = match coordinator
        .render_motion_canvas_segments(
            project.id,
            &RevisionStamp::from(&project),
            "native-render-real-driver-host",
            deliverable.id,
            &options,
        )
        .await
    {
        Ok(result) => result,
        Err(error) => {
            let diagnostic_path =
                evidence_path.with_file_name("motionwright-render-diagnostic.json");
            let receipts = coordinator.receipts(project.id, 128).unwrap_or_default();
            let diagnostic = json!({
                "native_render_e2e": "FAIL",
                "project_id": project.id,
                "generation": project.generation,
                "revision": project.revision,
                "error": {
                    "code": format!("{:?}", error.code),
                    "message": &error.message,
                    "outcome_known": error.outcome_known,
                    "candidates": &error.candidates,
                },
                "receipts": receipts,
            });
            let body = serde_json::to_vec_pretty(&diagnostic)?;
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&diagnostic_path)?;
            file.write_all(&body)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            eprintln!("{}", serde_json::to_string(&diagnostic)?);
            return Err(error.into());
        }
    };

    if result.generation != project.generation
        || result.revision != project.revision
        || result.segments.len() != 1
        || result.segments[0].frame_count != expected_frames
    {
        return Err("native render evidence is not bound to the expected project revision".into());
    }
    let segment = &result.segments[0];
    if segment
        .artifact
        .get("frame_count")
        .and_then(|value| value.as_u64())
        != Some(expected_frames)
    {
        return Err(
            "native Motion Canvas artifact did not report the fixture's exact expected frame count"
                .into(),
        );
    }
    if segment
        .verification
        .pointer("/report/support_level")
        .and_then(|value| value.as_str())
        != Some("native")
    {
        return Err("native Motion Canvas verification support level was not native".into());
    }

    let parent = evidence_path
        .parent()
        .ok_or("evidence destination has no parent")?;
    if !parent.is_dir() {
        return Err("evidence destination parent must already exist".into());
    }
    let body = serde_json::to_vec_pretty(&result)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(evidence_path)?;
    file.write_all(&body)?;
    file.write_all(b"\n")?;
    file.sync_all()?;

    println!(
        "{}",
        serde_json::to_string(&json!({
            "native_render_e2e": "PASS",
            "project_id": project.id,
            "generation": project.generation,
            "revision": project.revision,
            "segment_id": segment.segment_id,
            "frame_count": segment.frame_count,
            "job_ref": segment.job_ref,
            "artifact": segment.artifact,
            "evidence": evidence_path
        }))?
    );
    Ok(())
}

async fn master(
    database: &Path,
    connection_path: &Path,
    motion_evidence_path: &Path,
    audio_relative: String,
    audio_sha256: String,
    evidence_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("native AV master expected exactly one seeded project".into());
    }
    let project = projects.into_iter().next().expect("length checked");
    let deliverable = project
        .deliverables
        .first()
        .ok_or("seeded project has no deliverable")?;

    let expected_frames = expected_fixture_frames(&project)?;
    let motion_bytes = fs::read(motion_evidence_path)?;
    if motion_bytes.len() > 8 * 1024 * 1024 {
        return Err("Motion Canvas evidence exceeds the acceptance bound".into());
    }
    let motion: MotionCanvasRenderEvidence = serde_json::from_slice(&motion_bytes)?;
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("owner-provisioned connection is not bound to the seeded project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let audio = MltAudioArtifact {
        relative_path: audio_relative,
        sha256: audio_sha256,
        sample_rate: 48_000,
        channels: 2,
    };

    let result = match coordinator
        .assemble_mlt_av_master(
            project.id,
            &RevisionStamp::from(&project),
            MltAvMasterRequest {
                request_id: "native-av-master-real-driver-host",
                deliverable_id: deliverable.id,
                motion: &motion,
                audio: &audio,
                sync: None,
            },
        )
        .await
    {
        Ok(result) => result,
        Err(error) => {
            let diagnostic_path =
                evidence_path.with_file_name("motionwright-av-master-diagnostic.json");
            let receipts = coordinator.receipts(project.id, 256).unwrap_or_default();
            let diagnostic = json!({
                "native_av_master_e2e": "FAIL",
                "project_id": project.id,
                "generation": project.generation,
                "revision": project.revision,
                "error": {
                    "code": format!("{:?}", error.code),
                    "message": &error.message,
                    "outcome_known": error.outcome_known,
                    "candidates": &error.candidates,
                },
                "receipts": receipts,
            });
            let body = serde_json::to_vec_pretty(&diagnostic)?;
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&diagnostic_path)?;
            file.write_all(&body)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            eprintln!("{}", serde_json::to_string(&diagnostic)?);
            return Err(error.into());
        }
    };

    if result.generation != project.generation
        || result.revision != project.revision
        || result.deliverable_id != deliverable.id
        || result.frame_count != expected_frames
        || result.frame_rate != Rate::new(30, 1)?
        || result
            .master
            .get("profile")
            .and_then(|value| value.as_str())
            != Some("h264-aac-mp4")
        || result
            .master
            .pointer("/media/video")
            .and_then(|value| value.as_bool())
            != Some(true)
        || result
            .master
            .pointer("/media/audio")
            .and_then(|value| value.as_bool())
            != Some(true)
    {
        return Err("native AV master evidence does not match the certified profile".into());
    }

    let parent = evidence_path
        .parent()
        .ok_or("AV evidence destination has no parent")?;
    if !parent.is_dir() {
        return Err("AV evidence destination parent must already exist".into());
    }
    let body = serde_json::to_vec_pretty(&result)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(evidence_path)?;
    file.write_all(&body)?;
    file.write_all(b"\n")?;
    file.sync_all()?;

    println!(
        "{}",
        serde_json::to_string(&json!({
            "native_av_master_e2e": "PASS",
            "project_id": project.id,
            "generation": project.generation,
            "revision": project.revision,
            "frame_count": result.frame_count,
            "profile": result.master.get("profile"),
            "artifact": result.master.get("artifact"),
            "decoded_audio": result.decoded_audio,
            "evidence": evidence_path
        }))?
    );
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let command = args
        .next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage());

    match command.as_str() {
        "seed-procedural" => {
            let database = path_arg(&mut args);
            let mode = text_arg(&mut args);
            let project_json = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed_procedural(&database, &mode, &project_json)
        }
        "seed-hero" => {
            let database = path_arg(&mut args);
            let aspect = text_arg(&mut args);
            let project_json = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed_hero(&database, &aspect, &project_json)
        }
        "seed" => {
            let database = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed(&database)
        }
        "render" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            render(&database, &connection, &evidence).await
        }
        "master" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let motion_evidence = path_arg(&mut args);
            let audio_relative = text_arg(&mut args);
            let audio_sha256 = text_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            master(
                &database,
                &connection,
                &motion_evidence,
                audio_relative,
                audio_sha256,
                &evidence,
            )
            .await
        }
        _ => usage(),
    }
}

fn seed_hero(
    database: &Path,
    aspect: &str,
    project_json: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let split = aspect.starts_with("split-");
    let metric = aspect.starts_with("metric-");
    let shape = aspect
        .strip_prefix("split-")
        .or_else(|| aspect.strip_prefix("metric-"))
        .unwrap_or(aspect);
    let (width, height) = match shape {
        "landscape" => (1920, 1080),
        "portrait" => (1080, 1920),
        "square" => (1080, 1080),
        _ => return Err("native composition must have a bounded family/aspect".into()),
    };
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("hero seed database must start empty".into());
    }
    let label = if split {
        "SplitExplanation"
    } else if metric {
        "MetricEvidence"
    } else {
        "ProductHeroReveal"
    };
    let initial = service.create_project(&format!("{label} native acceptance"))?;
    let scene_project = service
        .apply(
            initial.id,
            &initial.stamp(),
            "hero-seed-scene",
            &Change::AddScene {
                name: label.into(),
                objective: "Evaluate native typography, staggered entrances, spatial layout and editable preservation".into(),
                duration_seconds: 6,
            },
        )?
        .project;
    let scene_id = scene_project.scenes[0].id;
    let hero_project = service
        .apply(
            scene_project.id,
            &scene_project.stamp(),
            "hero-seed-component",
            &Change::UpsertProductHero {
                instance_id: Uuid::parse_str("00000000-0000-4000-8000-000000000005")?,
                scene_id,
                config: if metric {
                    motionwright_domain::HeroConfig {
                        layout: motionwright_domain::HeroLayout::MetricEvidence,
                        eyebrow: "AUTHOR SUPPLIED / VALIDATION REQUIRED".into(),
                        headline: "42% growth".into(),
                        body: "An editorial statement is not measured evidence until its source is attached and verified.".into(),
                        wordmark: "DATA".into(),
                        accent: "#D9A46E".into(),
                        ..motionwright_domain::HeroConfig::default()
                    }
                } else if split {
                    motionwright_domain::HeroConfig {
                        layout: motionwright_domain::HeroLayout::SplitExplanation,
                        eyebrow: "MOTIONWRIGHT / EXPLANATION".into(),
                        headline: "The problem\nand the approach".into(),
                        body: "A clear second part explains what changes, without inventing software evidence.".into(),
                        wordmark: "WHY".into(),
                        ..motionwright_domain::HeroConfig::default()
                    }
                } else {
                    motionwright_domain::HeroConfig::default()
                },
            },
        )?
        .project;
    let mut profile = hero_project.deliverables[0].clone();
    profile.width = width;
    profile.height = height;
    profile.name = format!("{label} / {shape}");
    let project = service
        .apply(
            hero_project.id,
            &hero_project.stamp(),
            "hero-seed-aspect",
            &Change::UpsertDeliverable { profile },
        )?
        .project;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(project_json)?;
    output.write_all(&serde_json::to_vec_pretty(&project)?)?;
    output.write_all(b"\n")?;
    output.sync_all()?;
    let deliverable = &project.deliverables[0];
    let expected_layout = motionwright_domain::realize_product_hero(
        project.production_design.heroes[0].id,
        &project.production_design.heroes[0].config,
        width,
        height,
    )?;
    let mut layout_output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(project_json.with_file_name("expected-component-layout.json"))?;
    layout_output.write_all(&serde_json::to_vec_pretty(&json!({
        "schema": "motionwright.component-layout-regions/1",
        "project_id": project.id, "generation": project.generation, "revision": project.revision,
        "width": width, "height": height, "nodes": expected_layout
    }))?)?;
    layout_output.sync_all()?;
    let bundle_path = project_json.with_file_name("hero.motionwright");
    service.export_project_bundle(project.id, &bundle_path)?;
    let validation_root = tempfile::tempdir()?;
    let destination = StudioService::open(validation_root.path().join("import.sqlite3"))?;
    let inspected = destination.inspect_project_bundle(&bundle_path)?;
    let imported = destination.import_project_bundle(&bundle_path)?;
    if !inspected.project.rotates_generation
        || imported.generation == project.generation
        || imported.id != project.id
        || imported.revision != project.revision
        || imported.production_design != project.production_design
        || imported.scenes != project.scenes
    {
        return Err(
            "portable hero round-trip changed authored state or reused source authority".into(),
        );
    }
    if destination
        .apply(
            imported.id,
            &project.stamp(),
            "old-source-authority",
            &Change::RenameProject {
                title: "Must not apply".into(),
            },
        )
        .is_ok()
    {
        return Err("portable hero accepted a stale source generation".into());
    }
    let mut bundle_check = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(project_json.with_file_name("bundle-import-check.json"))?;
    bundle_check.write_all(&serde_json::to_vec_pretty(&json!({
        "schema": "motionwright.creative-bundle-roundtrip/1", "project_id": project.id,
        "source_generation": project.generation, "import_generation": imported.generation,
        "revision": project.revision, "project_schema": imported.schema_version,
        "semantic_state_preserved": true, "generation_rotated": true,
        "old_generation_write_rejected": true, "bundle": "hero.motionwright",
        "event_count": inspected.project.event_count, "blob_count": inspected.blob_count,
        "creative_approval": "required", "renderer_grants_imported": false
    }))?)?;
    bundle_check.sync_all()?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "fixture": if split { "split-explanation/1" } else if metric { "metric-evidence/1" } else { "product-hero-reveal/1" }, "project_id": project.id, "resource": project.resource_key(),
            "generation": project.generation, "revision": project.revision, "scene_id": scene_id,
            "deliverable_id": deliverable.id, "width": width, "height": height, "frame_count": 180,
            "creative_approval": "required"
        }))?
    );
    Ok(())
}

/// CI-only design evidence: author a real, portable project and render it using
/// the unchanged owner-owned Semwright Driver Host and Motionwright coordinator.
fn seed_procedural(
    database: &Path,
    mode: &str,
    project_json: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let animated = match mode {
        "static" => false,
        "motion" => true,
        _ => return Err("procedural fixture mode must be static or motion".into()),
    };
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("procedural native fixture requires empty database".into());
    }
    let initial = service.create_project("Procedural native design acceptance")?;
    let added = service
        .apply(
            initial.id,
            &initial.stamp(),
            "procedural-acceptance-scene",
            &Change::AddScene {
                name: "Bounded geometric motion".into(),
                objective: "Original generated graphic study, not real software footage".into(),
                duration_seconds: 6,
            },
        )?
        .project;
    let scene_id = added.scenes[0].id;
    let config = motionwright_domain::ProceduralConfig {
        seed: 41,
        count: 12,
        columns: 4,
        distribution: motionwright_domain::FieldDistribution::Scatter,
        origin_x: 260,
        origin_y: 180,
        area_width: 1240,
        area_height: 660,
        size: 88,
        opacity_percent: 100,
        fill: "#A5C8DF".into(),
        reveal_step_frames: if animated { 6 } else { 0 },
        reveal_duration_frames: 18,
    };
    let project = service
        .apply(
            added.id,
            &added.stamp(),
            "procedural-acceptance-field",
            &Change::UpsertProceduralField {
                instance_id: Uuid::parse_str("00000000-0000-4000-8000-000000000095")?,
                scene_id,
                config: config.clone(),
            },
        )?
        .project;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(project_json)?;
    file.write_all(&serde_json::to_vec_pretty(&project)?)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    let bundle = project_json.with_file_name("procedural.motionwright");
    service.export_project_bundle(project.id, &bundle)?;
    let temp = tempfile::tempdir()?;
    let destination = StudioService::open(temp.path().join("import.sqlite3"))?;
    let inspected = destination.inspect_project_bundle(&bundle)?;
    let imported = destination.import_project_bundle(&bundle)?;
    if !inspected.project.rotates_generation
        || imported.generation == project.generation
        || imported.id != project.id
        || imported.revision != project.revision
        || imported.production_design != project.production_design
        || imported.scenes != project.scenes
    {
        return Err("procedural portable round-trip changed authored nodes or authority".into());
    }
    if destination
        .apply(
            imported.id,
            &project.stamp(),
            "procedural-stale-authority",
            &Change::RenameProject {
                title: "Forbidden stale write".into(),
            },
        )
        .is_ok()
    {
        return Err("procedural portable import reused source-generation authority".into());
    }
    let deliverable = &project.deliverables[0];
    println!(
        "{}",
        serde_json::to_string(&json!({
            "fixture": if animated { "procedural-field-motion/1" } else { "procedural-field-static/1" },
            "project_id": project.id, "resource": project.resource_key(),
            "generation": project.generation, "revision": project.revision,
            "scene_id": scene_id, "deliverable_id": deliverable.id,
            "width": deliverable.width, "height": deliverable.height,
            "frame_count": 180, "procedural_config": config,
            "generator_version": 1,
            "portable_bundle": "procedural.motionwright",
            "source_generation_rotated": true,
            "creative_approval": "required",
        }))?
    );
    Ok(())
}

fn expected_fixture_frames(
    project: &motionwright_domain::Project,
) -> Result<u64, Box<dyn std::error::Error>> {
    if let [field] = project.production_design.procedural_fields.as_slice() {
        if project.scenes.len() != 1
            || project.scenes[0].duration != RationalTime::new(6, 1)?
            || project.scenes[0].nodes.len() != usize::from(field.config.count)
            || !project.production_design.heroes.is_empty()
            || (
                project.deliverables[0].width,
                project.deliverables[0].height,
            ) != (1920, 1080)
        {
            return Err(
                "procedural native E2E fixture no longer matches its six-second project contract"
                    .into(),
            );
        }
        field.validate()?;
        return Ok(180);
    }
    if project.production_design.heroes.is_empty() {
        return Ok(60);
    }
    if project.production_design.heroes.len() != 1
        || project.scenes.len() != 1
        || project.scenes[0].duration != motionwright_domain::RationalTime::new(6, 1)?
        || project.scenes[0].nodes.len() != 6
        || !matches!(
            (
                project.deliverables[0].width,
                project.deliverables[0].height
            ),
            (1920, 1080) | (1080, 1920) | (1080, 1080)
        )
    {
        return Err(
            "hero fixture no longer matches its explicit native acceptance contract".into(),
        );
    }
    Ok(180)
}
