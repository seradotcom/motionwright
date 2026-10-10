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
    // Projects start with three legitimate aspect profiles. Keep all of them;
    // mutate the existing master instead of silently changing the fixture model.
    let mut profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.name == "Master 16:9")
        .ok_or("Default master deliverable is missing")?
        .clone();
    profile.width = 1280;
    profile.height = 720;
    // Keep the pinned Motion Canvas browser at its proven native 30 fps.
    // Each of the 33 semantic scenes is one *exact* 1/30-second frame:
    // the 32 + 1 segment boundary stays exercised without 990 rendered PNGs.
    profile.frame_rate = RationalTime::new(30, 1)?;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-multisegment-720p-30fps-profile",
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
                &format!("native-multisegment-frame-duration-{number:02}"),
                &Change::SetSceneDuration {
                    scene_id,
                    duration: RationalTime::new(1, 30)?,
                },
            )?
            .project;
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
    let profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.name == "Master 16:9")
        .ok_or("Master deliverable was lost after source seeding")?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id":project.id,"generation":project.generation,
            "revision":project.revision,"resource":project.resource_key(),
            "width":profile.width,"height":profile.height,
            "fps_num":profile.frame_rate.num,"fps_den":profile.frame_rate.den,
            "scene_count":project.scenes.len(),
            "deliverable_count":project.deliverables.len(),
            "master_profile_id":profile.id,
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
    if project.scenes.len() != 33 || project.deliverables.len() != 3 {
        return Err(format!(
            "Native test project shape changed: expected 33 scenes and three default deliverables; found {} scenes and {} deliverables",
            project.scenes.len(),
            project.deliverables.len(),
        ).into());
    }
    let profile = project
        .deliverables
        .iter()
        .find(|candidate| candidate.name == "Master 16:9")
        .ok_or("Native test master profile was removed")?;
    if (
        profile.width,
        profile.height,
        profile.frame_rate.num,
        profile.frame_rate.den,
    ) != (1280, 720, 30, 1)
    {
        return Err("Native multi-segment project profile changed".into());
    }
    // Reopened SQLite must preserve exact one-frame timing across scenes.
    // The earlier 1 fps fixture failed at native observation before MLT.
    for (index, scene) in project.scenes.iter().enumerate() {
        let start = RationalTime::new(index as i64, 30)?;
        if scene.start != start || scene.duration != RationalTime::new(1, 30)? {
            return Err("Native two-segment fixture lost its exact 30 fps timebase".into());
        }
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("Semwright owner connection belongs to a foreign resource".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1)?,
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
    let video_only = std::env::var("MOTIONWRIGHT_MLT_MODE").as_deref() == Ok("video-only");
    let evidence = if video_only {
        coordinator
            .assemble_native_mlt_video_only_timeline(
                project.id,
                &RevisionStamp::from(&project),
                "real-mlt-video-only-33",
                profile.id,
                &options,
                &render,
            )
            .await?
    } else {
        coordinator
            .assemble_native_mlt_video_timeline(
                project.id,
                &RevisionStamp::from(&project),
                "real-mlt-timeline-33",
                profile.id,
                &options,
                &render,
            )
            .await?
    };
    let profile_expected = if video_only {
        "lossless-video-only"
    } else {
        "lossless"
    };
    let scope_expected = if video_only {
        "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
    } else {
        "actual-native-mlt-ffv1-pcm-intermediate-not-approved-sound-or-master"
    };
    if evidence.frame_count != 33
        || evidence.transport_pcm_audio == video_only
        || evidence.native_render_profile != profile_expected
        || evidence.provider_project_cleanup != "not_requested_requires_foreground_broker_consent"
        || (video_only && evidence.native_frame_count_observed != Some(33))
        || evidence
            .native_frame_count_observed
            .is_some_and(|observed| observed != 33)
        || evidence.source.verified_video_segments.len() != 2
        || evidence.evidence_scope != scope_expected
    {
        return Err(
            "Native MLT timeline did not return the two-segment lossless intermediary".into(),
        );
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
            "transport_pcm_audio":evidence.transport_pcm_audio,
            "native_render_profile":evidence.native_render_profile,
            "provider_project_cleanup":evidence.provider_project_cleanup,
            "native_frame_count_observed":evidence.native_frame_count_observed,
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
