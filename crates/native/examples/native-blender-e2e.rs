use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::production::{ProductionConnection, ProductionCoordinator};
use motionwright_service::StudioService;
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
        "usage: native-blender-e2e <seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON>"
    );
    std::process::exit(2);
}

fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

fn shape_node(
    kind: &str,
    name: &str,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    fill: &str,
) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x,
        y,
        width,
        height,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(fill.into()),
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

fn seed(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("native-blender-e2e seed database must start empty".into());
    }

    let initial = service.create_project("Native Blender round-trip E2E")?;
    let with_scene = service
        .apply(
            initial.id,
            &RevisionStamp::from(&initial),
            "native-blender-seed-scene",
            &Change::AddScene {
                name: "Verified Blender contribution".into(),
                objective: "Realize bounded Motionwright geometry through Semwright Blender".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = with_scene.scenes[0].id;
    let with_renderer = service
        .apply(
            with_scene.id,
            &RevisionStamp::from(&with_scene),
            "native-blender-set-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            },
        )?
        .project;
    let with_rectangle = service
        .apply(
            with_renderer.id,
            &RevisionStamp::from(&with_renderer),
            "native-blender-seed-rectangle",
            &Change::AddCanvasNode {
                scene_id,
                node: shape_node(
                    "rectangle",
                    "Motionwright panel",
                    260.0,
                    180.0,
                    520.0,
                    260.0,
                    "#4F7CAC",
                ),
            },
        )?
        .project;
    let project = service
        .apply(
            with_rectangle.id,
            &RevisionStamp::from(&with_rectangle),
            "native-blender-seed-circle",
            &Change::AddCanvasNode {
                scene_id,
                node: shape_node(
                    "circle",
                    "Motionwright focus",
                    920.0,
                    360.0,
                    220.0,
                    220.0,
                    "#F5B84A",
                ),
            },
        )?
        .project;

    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id": project.id,
            "resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "scene_id": scene_id,
            "mesh_count": 2
        }))?
    );
    Ok(())
}

async fn realize(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("native-blender-e2e expected exactly one seeded project".into());
    }
    let project = projects.into_iter().next().expect("length checked");
    let scene = project
        .scenes
        .first()
        .ok_or("seeded Blender project has no scene")?;
    if scene.renderer != RendererKind::Blender {
        return Err("seeded scene is not owned by the Blender renderer".into());
    }

    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("owner-provisioned connection is not bound to the seeded project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let expected = RevisionStamp::from(&project);
    let result = match coordinator
        .realize_blender_scene(
            project.id,
            &expected,
            "native-blender-real-driver-host",
            scene.id,
        )
        .await
    {
        Ok(result) => result,
        Err(error) => {
            let diagnostic_path =
                evidence_path.with_file_name("motionwright-blender-diagnostic.json");
            let receipts = coordinator.receipts(project.id, 256).unwrap_or_default();
            let diagnostic = json!({
                "native_blender_e2e": "FAIL",
                "project_id": project.id,
                "generation": project.generation,
                "revision": project.revision,
                "scene_id": scene.id,
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

    if result.get("renderer").and_then(|value| value.as_str()) != Some("blender")
        || result.get("native_driver").and_then(|value| value.as_str()) != Some("driver:blender")
        || result
            .get("project_revision")
            .and_then(|value| value.as_u64())
            != Some(project.revision)
        || result.get("scene_id") != Some(&json!(scene.id))
        || result
            .get("objects")
            .and_then(|value| value.as_array())
            .map(Vec::len)
            != Some(2)
    {
        return Err("native Blender realization lost Motionwright revision identity".into());
    }
    let artifact = result
        .get("artifact")
        .ok_or("native Blender realization returned no verified artifact")?;
    let relative = artifact
        .get("path")
        .and_then(|value| value.as_str())
        .ok_or("verified Blender artifact has no path")?;
    let sha256 = artifact
        .get("sha256")
        .and_then(|value| value.as_str())
        .ok_or("verified Blender artifact has no digest")?;
    let bytes = artifact
        .get("bytes")
        .and_then(|value| value.as_u64())
        .ok_or("verified Blender artifact has no size")?;
    if !relative.ends_with(".glb") || sha256.len() != 64 || bytes <= 20 || bytes > 256 * 1024 * 1024
    {
        return Err("verified Blender artifact metadata is outside certified bounds".into());
    }

    let receipts = coordinator.receipts(project.id, 256)?;
    let completed_commands: BTreeSet<_> = receipts
        .iter()
        .filter(|receipt| receipt.stage == "completed")
        .map(|receipt| receipt.command.as_str())
        .collect();
    for required in [
        "driver.blender.collection.create",
        "driver.blender.semantic.datablock.create",
        "driver.blender.semantic.objects",
        "driver.blender.mesh.geometry.replace",
        "driver.blender.semantic.object.create",
        "driver.blender.material.create",
        "driver.blender.material.assign",
        "driver.blender.export.glb",
    ] {
        if !completed_commands.contains(required) {
            return Err(format!("native Blender receipt ledger is missing {required}").into());
        }
    }

    let parent = evidence_path
        .parent()
        .ok_or("Blender evidence destination has no parent")?;
    if !parent.is_dir() {
        return Err("Blender evidence destination parent must already exist".into());
    }
    let evidence = json!({
        "native_blender_e2e": "PASS",
        "project_id": project.id,
        "resource": project.resource_key(),
        "generation": project.generation,
        "revision": project.revision,
        "scene_id": scene.id,
        "result": result,
        "receipt_count": receipts.len(),
        "completed_commands": completed_commands,
    });
    let body = serde_json::to_vec_pretty(&evidence)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(evidence_path)?;
    file.write_all(&body)?;
    file.write_all(b"\n")?;
    file.sync_all()?;

    println!("{}", serde_json::to_string(&evidence)?);
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
        "realize" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            realize(&database, &connection, &evidence).await
        }
        _ => usage(),
    }
}
