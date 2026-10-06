use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RevisionStamp,
};
use motionwright_native::{
    film::{FilmBuildOptions, SceneFilmIntent},
    production::{ProductionConnection, ProductionCoordinator},
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use serde_json::json;
use std::{
    collections::BTreeSet,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn usage() -> ! {
    eprintln!(
        "usage: native-render-e2e <seed DATABASE | render DATABASE CONNECTION EVIDENCE_JSON>"
    );
    std::process::exit(2);
}

fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

fn text_node() -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Native production title".into(),
        kind: "text".into(),
        parent_id: None,
        x: 240.0,
        y: 320.0,
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
            font_family: Some("system-ui".into()),
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
    let project = service
        .apply(
            with_scene.id,
            &RevisionStamp::from(&with_scene),
            "native-render-seed-node",
            &Change::AddCanvasNode {
                scene_id,
                node: text_node(),
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
        font_family: "system-ui".into(),
        mono_font_family: "monospace".into(),
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
        _ => usage(),
    }
}
