//! Native acceptance/demo: Blender checkpoint -> process exit -> Motion Canvas
//! resume. Uses real Semwright Driver Host when supplied an owner connection.
//! The "stop" phase interrupts BETWEEN applications; the "fail" phase invokes
//! the real native renderer with a deliberately unavailable CI-only browser,
//! requires a typed terminal native failure and a durable retryable checkpoint.
//! "resume" runs with the restored browser and MUST NOT rerun native Blender.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::{
    film::{FilmBuildOptions, SceneFilmIntent},
    production::{ProductionConnection, ProductionCoordinator, StageDisposition},
};
use motionwright_service::StudioService;
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
use semwright_native_sdk::ErrorCode;
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
        "usage: native-cross-app-recovery-e2e <seed DATABASE | stop DATABASE CONNECTION EVIDENCE_JSON | fail DATABASE CONNECTION EVIDENCE_JSON | resume DATABASE CONNECTION EVIDENCE_JSON | repeat DATABASE CONNECTION EVIDENCE_JSON>"
    );
    std::process::exit(2);
}
fn arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

fn shape(name: &str, kind: &str, x: f64, color: &str) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x,
        y: 250.0,
        width: 280.0,
        height: 280.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(color.into()),
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

fn text_node() -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: "Motion Canvas continuation".into(),
        kind: "text".into(),
        parent_id: None,
        x: 180.0,
        y: 410.0,
        width: 1510.0,
        height: 170.0,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: Some("Blender stays done. Motion Canvas resumes.".into()),
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

fn apply(
    service: &StudioService,
    project: motionwright_domain::Project,
    id: &str,
    change: Change,
) -> Result<motionwright_domain::Project, Box<dyn std::error::Error>> {
    Ok(service
        .apply(project.id, &RevisionStamp::from(&project), id, &change)?
        .project)
}

fn seed(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("cross-app recovery seed database must be empty".into());
    }
    let initial = service.create_project("Cross-app selective recovery demo")?;
    let project = apply(
        &service,
        initial,
        "demo-blender-scene",
        Change::AddScene {
            name: "Reusable native Blender contribution".into(),
            objective: "Native 3D geometry survives downstream failures".into(),
            duration_seconds: 2,
        },
    )?;
    let blender_scene = project.scenes[0].id;
    let project = apply(
        &service,
        project,
        "demo-blender-kind",
        Change::SetSceneRenderer {
            scene_id: blender_scene,
            renderer: RendererKind::Blender,
        },
    )?;
    let project = apply(
        &service,
        project,
        "demo-blender-panel",
        Change::AddCanvasNode {
            scene_id: blender_scene,
            node: shape("Semantic Blender rectangle", "rectangle", 300.0, "#3E75A9"),
        },
    )?;
    let project = apply(
        &service,
        project,
        "demo-blender-circle",
        Change::AddCanvasNode {
            scene_id: blender_scene,
            node: shape("Semantic Blender circle", "circle", 950.0, "#EFB84E"),
        },
    )?;
    let project = apply(
        &service,
        project,
        "demo-motion-scene",
        Change::AddScene {
            name: "Motion Canvas after native Blender".into(),
            objective: "Resume semantic Motion Canvas without reexecuting Blender".into(),
            duration_seconds: 2,
        },
    )?;
    let motion_scene = project.scenes[1].id;
    let project = apply(
        &service,
        project,
        "demo-motion-title",
        Change::AddCanvasNode {
            scene_id: motion_scene,
            node: text_node(),
        },
    )?;
    let deliverable_id = project
        .deliverables
        .first()
        .ok_or("missing deliverable")?
        .id;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "demo":"cross-app-selective-recovery/1", "project_id":project.id,
            "resource":project.resource_key(), "generation":project.generation,
            "revision":project.revision, "blender_scene":blender_scene,
            "motion_scene":motion_scene, "deliverable_id":deliverable_id,
        }))?
    );
    Ok(())
}

fn private_evidence(
    path: &Path,
    value: &serde_json::Value,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut opts = OpenOptions::new();
    opts.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut file = opts.open(path)?;
    file.write_all(&serde_json::to_vec_pretty(value)?)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    Ok(())
}

async fn phase(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    phase: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("expected one seeded project".into());
    }
    let project = &projects[0];
    let blender = project
        .scenes
        .iter()
        .find(|s| s.renderer == RendererKind::Blender)
        .ok_or("missing Blender scene")?;
    let motion = project
        .scenes
        .iter()
        .find(|s| s.renderer == RendererKind::MotionCanvas)
        .ok_or("missing Motion Canvas scene")?;
    let deliverable = project.deliverables.first().ok_or("missing deliverable")?;
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("wrong Semwright resource".into());
    }
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let expected = RevisionStamp::from(project);
    let before = coordinator.receipts(project.id, 256)?;
    let blender_calls_before = before
        .iter()
        .filter(|r| r.command.starts_with("driver.blender.") && r.stage == "completed")
        .count();
    let options = FilmBuildOptions {
        frame_rate: Rate::new(30, 1)?,
        font_family: "Instrument Sans Variable".into(),
        mono_font_family: "IBM Plex Mono".into(),
        scene_intents: vec![SceneFilmIntent {
            scene_id: motion.id,
            role: NarrativeRole::Mechanism,
            archetype: Archetype::Statement,
        }],
    };
    let report = if phase == "stop" {
        let (disposition, proof) = coordinator
            .checkpoint_blender_for_cross_app(project.id, &expected, "reddit-demo", blender.id)
            .await?;
        let after = coordinator.receipts(project.id, 256)?;
        if after
            .iter()
            .any(|r| r.command.starts_with("driver.motion-canvas."))
        {
            return Err("stop phase must not dispatch Motion Canvas".into());
        }
        json!({
            "phase":"stop", "fault":"controlled process interruption before Motion Canvas dispatch",
            "blender":disposition, "blender_proof":proof,
            "project":project.resource_key(), "revision":project.revision,
        })
    } else if phase == "fail" {
        let failed = coordinator
            .recover_blender_motion(
                project.id,
                &expected,
                "reddit-demo",
                blender.id,
                deliverable.id,
                &options,
            )
            .await;
        match failed {
            Err(error)
                if error.outcome_known
                    && error.code == ErrorCode::BackendFailed
                    && error.message
                        == "Motion Canvas segment 1 render failed (category: font_evidence)" =>
            {
                let after = coordinator.receipts(project.id, 256)?;
                let blender_calls_after = after
                    .iter()
                    .filter(|r| r.command.starts_with("driver.blender.") && r.stage == "completed")
                    .count();
                if blender_calls_after != blender_calls_before {
                    return Err("native failure reexecuted Blender commands".into());
                }
                let failed_stage = after.iter().find(|r| {
                    r.command == "motionwright.recovery.motion-canvas"
                        && r.stage == "failed_known"
                        && r.payload.get("retryable").and_then(|v| v.as_bool()) == Some(true)
                        && r.payload.get("attempt").and_then(|v| v.as_u64()) == Some(1)
                });
                if failed_stage.is_none() {
                    return Err("real native failure was not durably marked retryable".into());
                }
                json!({
                    "phase":"fail",
                    "expected_native_failure": true,
                    "native_failure_class":"font_evidence",
                    "native_status":"failed",
                    "outcome_known":true,
                    "retryable":true,
                    "motion_canvas_attempt":1,
                    "blender_reexecution_count":blender_calls_after - blender_calls_before,
                    "project":project.resource_key()
                })
            }
            Ok(_) => {
                return Err(
                    "native renderer unexpectedly succeeded during failure injection".into(),
                );
            }
            Err(error) => {
                return Err(format!(
                    "native failure was not a known terminal font_evidence result: {error:?}"
                )
                .into());
            }
        }
    } else {
        let result = coordinator
            .recover_blender_motion(
                project.id,
                &expected,
                "reddit-demo",
                blender.id,
                deliverable.id,
                &options,
            )
            .await?;
        let afterwards = coordinator.receipts(project.id, 256)?;
        let blender_calls_after = afterwards
            .iter()
            .filter(|r| r.command.starts_with("driver.blender.") && r.stage == "completed")
            .count();
        if blender_calls_after != blender_calls_before {
            return Err("cross-app recovery reexecuted a Blender driver command".into());
        }
        if !matches!(result.blender, StageDisposition::Reused)
            || !matches!(
                result.motion_canvas,
                StageDisposition::Executed | StageDisposition::Reused
            )
        {
            return Err("recovery did not reuse native Blender".into());
        }
        if phase == "repeat" && !matches!(result.motion_canvas, StageDisposition::Reused) {
            return Err("repeat did not reuse the verified Motion Canvas frames".into());
        }
        json!({
            "phase":phase, "recovery":result,
            "blender_command_calls_before":blender_calls_before,
            "blender_command_calls_after":blender_calls_after,
            "blender_reexecution_count":blender_calls_after - blender_calls_before,
            "project":project.resource_key(),
        })
    };
    private_evidence(evidence_path, &report)?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "native_cross_app_recovery_e2e":
                if phase == "fail" { "EXPECTED_NATIVE_FAILURE" } else { "PASS" },
            "phase":phase,
            "blender_reexecution_count":report.get("blender_reexecution_count"),
            "evidence":evidence_path,
        }))?
    );
    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let command = args
        .next()
        .and_then(|os| os.into_string().ok())
        .unwrap_or_else(|| usage());
    let database = arg(&mut args);
    if command == "seed" {
        if args.next().is_some() {
            usage();
        }
        return seed(&database);
    }
    if !matches!(command.as_str(), "stop" | "fail" | "resume" | "repeat") {
        usage();
    }
    let connection = arg(&mut args);
    let evidence = arg(&mut args);
    if args.next().is_some() {
        usage();
    }
    phase(&database, &connection, &evidence, &command).await
}
