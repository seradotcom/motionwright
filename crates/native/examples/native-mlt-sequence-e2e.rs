//! Real pinned Broker/Driver Host two-segment timeline smoke test.
//! Never run on the developer workstation; the workflow provisions the
//! Semwright and Motion Canvas/MLT sandbox in a disposable CI runner.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RationalTime, RevisionStamp,
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
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

fn arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| {
        eprintln!(
            "usage: native-mlt-sequence-e2e seed DATABASE | assemble DATABASE CONNECTION EVIDENCE"
        );
        std::process::exit(2);
    })
}
fn node(scene_number: usize) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: format!("Native segment tile {scene_number:02}"),
        kind: "rectangle".into(),
        parent_id: None,
        x: 420.0,
        y: 320.0,
        width: 200.0,
        height: 100.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(if scene_number.is_multiple_of(2) {
                "#F5F5F2".into()
            } else {
                "#8FCEDB".into()
            }),
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
    }
}
fn seeded_database(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(path)?;
    if !service.projects(2)?.is_empty() {
        return Err("Native multi-segment test DB must start empty".into());
    }
    let mut project = service.create_project("Source-bound two-segment actual MLT E2E")?;
    let mut profile = project.deliverables[0].clone();
    profile.width = 1280;
    profile.height = 720;
    // One frame per second allows exact canonical Film 32+1 partition
    // with 33 *actual* PNGs, avoiding costly 990-frame video renders.
    profile.frame_rate = RationalTime::new(1, 1)?;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-multisegment-720p-1fps-profile",
            &Change::UpsertDeliverable { profile },
        )?
        .project;
    for number in 0..33 {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-multisegment-scene-{number:02}"),
                &Change::AddScene {
                    name: format!("Native source scene {number:02}"),
                    objective: format!("Pinned Motion Canvas scene {}", number + 1),
                    duration_seconds: 1,
                },
            )?
            .project;
        let scene_id = project.scenes.last().ok_or("new scene was missing")?.id;
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-multisegment-object-{number:02}"),
                &Change::AddCanvasNode {
                    scene_id,
                    node: node(number),
                },
            )?
            .project;
    }
    let profile = &project.deliverables[0];
    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id":project.id,"generation":project.generation,
            "revision":project.revision,"resource":project.resource_key(),
            "width":profile.width,"height":profile.height,
            "fps_num":profile.frame_rate.num,"fps_den":profile.frame_rate.den,
            "scene_count":project.scenes.len(),
        }))?
    );
    Ok(())
}
async fn assemble(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("Expected one application-owned source project".into());
    }
    let project = projects.into_iter().next().ok_or("project absent")?;
    if project.scenes.len() != 33 || project.deliverables.len() != 1 {
        return Err("Native test project was altered".into());
    }
    let profile = &project.deliverables[0];
    if (
        profile.width,
        profile.height,
        profile.frame_rate.num,
        profile.frame_rate.den,
    ) != (1280, 720, 1, 1)
    {
        return Err("Native multi-segment project profile changed".into());
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("Semwright owner connection belongs to a foreign resource".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let options = FilmBuildOptions {
        frame_rate: Rate::new(1, 1)?,
        font_family: "Instrument Sans Variable".into(),
        mono_font_family: "IBM Plex Mono".into(),
        scene_intents: project
            .scenes
            .iter()
            .map(|scene| SceneFilmIntent {
                scene_id: scene.id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            })
            .collect(),
    };
    let render = coordinator
        .render_motion_canvas_segments(
            project.id,
            &RevisionStamp::from(&project),
            "real-native-cut-33-scenes",
            profile.id,
            &options,
        )
        .await?;
    if render.segments.len() != 2
        || render.segments[0].frame_count != 32
        || render.segments[1].frame_count != 1
    {
        return Err("Actual native Film did not produce expected 32+1 partition".into());
    }
    let evidence = coordinator
        .assemble_native_mlt_video_timeline(
            project.id,
            &RevisionStamp::from(&project),
            "real-mlt-timeline-33",
            profile.id,
            &options,
            &render,
        )
        .await?;
    if evidence.frame_count != 33
        || evidence.source.verified_video_segments.len() != 2
        || evidence.evidence_scope != "actual-native-mlt-ffv1-video-only-no-audio-master"
    {
        return Err("Native MLT timeline did not return the expected two-segment video".into());
    }
    let body = serde_json::to_vec_pretty(&evidence)?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(evidence_path)?;
    output.write_all(&body)?;
    output.sync_all()?;
    if !fs::metadata(evidence_path)?.is_file() {
        return Err("Native MLT evidence was not saved".into());
    }
    println!(
        "{}",
        serde_json::to_string(&json!({
            "native_mlt_multisegment_e2e":"PASS",
            "project_id":project.id,
            "frame_count":evidence.frame_count,
            "segment_count":evidence.source.verified_video_segments.len(),
            "artifact_path":evidence.artifact_path,
            "artifact_sha256":evidence.artifact_sha256,
            "artifact_bytes":evidence.artifact_bytes,
            "evidence_scope":evidence.evidence_scope,
        }))?
    );
    Ok(())
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let action = args.next().ok_or("Missing command")?;
    let database = arg(&mut args);
    match action.to_str().ok_or("Non UTF-8 action")? {
        "seed" => seeded_database(&database),
        "assemble" => {
            let connection = arg(&mut args);
            let output = arg(&mut args);
            assemble(&database, &connection, &output).await
        }
        _ => Err("Unknown native MLT E2E command".into()),
    }
}
