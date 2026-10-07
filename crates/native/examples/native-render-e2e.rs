use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RevisionStamp,
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
    let project = service
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

    if (deliverable.width, deliverable.height) != (1920, 1080) {
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
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
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
        || result.segments[0].frame_count != 60
    {
        return Err("native render evidence is not bound to the expected project revision".into());
    }
    let segment = &result.segments[0];
    if segment
        .artifact
        .get("frame_count")
        .and_then(|value| value.as_u64())
        != Some(60)
    {
        return Err("native Motion Canvas artifact did not report exactly 60 frames".into());
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
        || result.frame_count != 60
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
