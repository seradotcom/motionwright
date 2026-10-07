use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_manim_profile::ManimRenderProfile;
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
        "usage: native-manim-e2e <seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON>"
    );
    std::process::exit(2);
}

fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}

struct NodeFixture<'a> {
    kind: &'a str,
    name: &'a str,
    text: Option<&'a str>,
    position: (f64, f64),
    size: (f64, f64),
    fill: &'a str,
}

fn node(fixture: NodeFixture<'_>) -> CanvasNode {
    let (x, y) = fixture.position;
    let (width, height) = fixture.size;
    CanvasNode {
        id: Uuid::now_v7(),
        name: fixture.name.into(),
        kind: fixture.kind.into(),
        parent_id: None,
        x,
        y,
        width,
        height,
        rotation_deg: 0.0,
        opacity: 1.0,
        text: fixture.text.map(str::to_owned),
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(fixture.fill.into()),
            stroke: None,
            stroke_width: 0.0,
            font_family: (fixture.kind == "text").then(|| "Sans".into()),
            font_size: (fixture.kind == "text").then_some(42.0),
            font_weight: (fixture.kind == "text").then_some(600),
            line_height: (fixture.kind == "text").then_some(1.0),
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
        return Err("native-manim-e2e seed database must start empty".into());
    }

    let initial = service.create_project("Native Manim Community round-trip E2E")?;
    let with_scene = service
        .apply(
            initial.id,
            &RevisionStamp::from(&initial),
            "native-manim-seed-scene",
            &Change::AddScene {
                name: "Verified mathematical explainer".into(),
                objective: "Render typed Motionwright primitives through Semwright Driver Host"
                    .into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = with_scene.scenes[0].id;
    let with_renderer = service
        .apply(
            with_scene.id,
            &RevisionStamp::from(&with_scene),
            "native-manim-set-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::ManimCommunity,
            },
        )?
        .project;

    let fixtures = [
        node(NodeFixture {
            kind: "rectangle",
            name: "Equation panel",
            text: None,
            position: (260.0, 180.0),
            size: (720.0, 300.0),
            fill: "#4F7CAC",
        }),
        node(NodeFixture {
            kind: "circle",
            name: "Focus point",
            text: None,
            position: (1120.0, 390.0),
            size: (200.0, 200.0),
            fill: "#F5B84A",
        }),
        node(NodeFixture {
            kind: "text",
            name: "Equation label",
            text: Some("x² + y² = r²"),
            position: (620.0, 720.0),
            size: (520.0, 120.0),
            fill: "#FFFFFF",
        }),
    ];
    let mut project = with_renderer;
    for (index, fixture) in fixtures.into_iter().enumerate() {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("native-manim-seed-node-{index}"),
                &Change::AddCanvasNode {
                    scene_id,
                    node: fixture,
                },
            )?
            .project;
    }

    println!(
        "{}",
        serde_json::to_string(&json!({
            "project_id": project.id,
            "resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision,
            "scene_id": scene_id,
            "primitive_count": 3
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
        return Err("native-manim-e2e expected exactly one seeded project".into());
    }
    let project = projects.into_iter().next().expect("length checked");
    let scene = project
        .scenes
        .first()
        .ok_or("seeded Manim project has no scene")?;
    if scene.renderer != RendererKind::ManimCommunity {
        return Err("seeded scene is not owned by the Manim Community renderer".into());
    }

    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("owner-provisioned connection is not bound to the seeded project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let expected = RevisionStamp::from(&project);
    let profile = ManimRenderProfile {
        width: 640,
        height: 360,
        frame_rate: 12,
    };

    let result = match coordinator
        .realize_manim_scene(
            project.id,
            &expected,
            "native-manim-real-driver-host",
            scene.id,
            profile,
        )
        .await
    {
        Ok(result) => result,
        Err(error) => {
            let diagnostic_path =
                evidence_path.with_file_name("motionwright-manim-diagnostic.json");
            let receipts = coordinator.receipts(project.id, 256).unwrap_or_default();
            let diagnostic = json!({
                "native_manim_e2e": "FAIL",
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
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&diagnostic_path)?;
            file.write_all(&serde_json::to_vec_pretty(&diagnostic)?)?;
            file.write_all(b"\n")?;
            file.sync_all()?;
            eprintln!("{}", serde_json::to_string(&diagnostic)?);
            return Err(error.into());
        }
    };

    if result.get("renderer").and_then(|value| value.as_str()) != Some("manim-community")
        || result.get("native_driver").and_then(|value| value.as_str())
            != Some("driver:manim-community")
        || result
            .get("runtime_contract")
            .and_then(|value| value.as_str())
            != Some("Manim Community 0.21.0")
        || result
            .get("project_revision")
            .and_then(|value| value.as_u64())
            != Some(project.revision)
        || result.get("scene_id") != Some(&json!(scene.id))
    {
        return Err("native Manim realization lost Motionwright revision/runtime identity".into());
    }

    let artifact = result
        .get("artifact")
        .ok_or("native Manim realization returned no verified artifact")?;
    let relative = artifact
        .get("relative_path")
        .and_then(|value| value.as_str())
        .ok_or("verified Manim artifact has no relative path")?;
    let sha256 = artifact
        .get("sha256")
        .and_then(|value| value.as_str())
        .ok_or("verified Manim artifact has no digest")?;
    let bytes = artifact
        .get("bytes")
        .and_then(|value| value.as_u64())
        .ok_or("verified Manim artifact has no size")?;
    if !relative.ends_with(".mp4") || sha256.len() != 64 || bytes == 0 || bytes > 512 * 1024 * 1024
    {
        return Err("verified Manim artifact metadata is outside certified bounds".into());
    }

    let receipts = coordinator.receipts(project.id, 256)?;
    let completed_commands: BTreeSet<_> = receipts
        .iter()
        .filter(|receipt| receipt.stage == "completed")
        .map(|receipt| receipt.command.as_str())
        .collect();
    for required in [
        "driver.manim-community.render.start",
        "driver.manim-community.render.status",
        "driver.manim-community.render.result",
    ] {
        if !completed_commands.contains(required) {
            return Err(format!("native Manim receipt ledger is missing {required}").into());
        }
    }

    let parent = evidence_path
        .parent()
        .ok_or("Manim evidence destination has no parent")?;
    if !parent.is_dir() {
        return Err("Manim evidence destination parent must already exist".into());
    }
    let evidence = json!({
        "native_manim_e2e": "PASS",
        "project_id": project.id,
        "resource": project.resource_key(),
        "generation": project.generation,
        "revision": project.revision,
        "scene_id": scene.id,
        "profile": profile,
        "result": result,
        "receipt_count": receipts.len(),
        "completed_commands": completed_commands,
    });
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(evidence_path)?;
    file.write_all(&serde_json::to_vec_pretty(&evidence)?)?;
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
