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
        "usage: native-manim-e2e <seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON | realize-camera DATABASE CONNECTION EVIDENCE_JSON PROJECT_ID SCENE_ID DELIVERABLE_ID REQUEST_ID>"
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

struct CameraTarget {
    project_id: Uuid,
    scene_id: Uuid,
    deliverable_id: Uuid,
    request_id: String,
}

fn text_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> String {
    args.next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage())
}

fn write_camera_evidence(
    path: &Path,
    evidence: &serde_json::Value,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(&serde_json::to_vec_pretty(evidence)?)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    Ok(())
}

fn retain_camera_error(
    evidence_path: &Path,
    evidence: &mut serde_json::Value,
    original_error: semwright_native_sdk::Error,
    completed_result: Option<serde_json::Value>,
) -> semwright_native_sdk::Error {
    if let Some(result) = completed_result {
        evidence["native_manim_camera_dispatch"] = json!("UNVERIFIED");
        evidence["failure_stage"] = json!("receipt_read");
        evidence["result"] = result;
    } else {
        evidence["native_manim_camera_dispatch"] = json!(if original_error.outcome_known {
            "FAIL"
        } else {
            "UNKNOWN"
        });
        evidence["failure_stage"] = json!("native_dispatch");
    }
    evidence["error"] = json!(&original_error);
    eprintln!("{evidence}");
    let diagnostic_path = evidence_path.with_extension("diagnostic.json");
    if let Err(custody_error) = write_camera_evidence(&diagnostic_path, evidence) {
        eprintln!(
            "{}",
            json!({
                "camera_diagnostic_custody_error": custody_error.to_string(),
                "diagnostic_path": diagnostic_path,
                "primary_error_preserved": true
            })
        );
    }
    original_error
}

async fn realize_camera(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    target: CameraTarget,
) -> Result<(), Box<dyn std::error::Error>> {
    let parent = evidence_path
        .parent()
        .ok_or("camera evidence has no parent")?;
    if !parent.is_dir() || evidence_path.exists() {
        return Err("camera evidence requires an existing parent and unused destination".into());
    }
    let service = StudioService::open(database)?;
    let project = service.project(target.project_id)?;
    let expected = RevisionStamp::from(&project);
    let start_id = format!("{}:start", target.request_id);
    if service
        .latest_production_receipt(project.id, &start_id)?
        .is_some()
    {
        return Err("camera attempt already exists; inspect its original outcome".into());
    }
    let connection = ProductionConnection::load(connection_path)?;
    if connection.resource != project.resource_key() {
        return Err("camera connection is not bound to the saved project".into());
    }
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let attempted = coordinator
        .realize_manim_camera_scene(
            project.id,
            &expected,
            &target.request_id,
            target.scene_id,
            target.deliverable_id,
        )
        .await;
    let receipt_result = coordinator.receipts(project.id, 256);
    let mut evidence = json!({
        "project_id": project.id,
        "resource": project.resource_key(),
        "generation": project.generation,
        "revision": project.revision,
        "scene_id": target.scene_id,
        "deliverable_id": target.deliverable_id,
        "request_id": target.request_id,
        "recent_receipts": receipt_result.as_ref().ok(),
        "receipt_read_error": receipt_result.as_ref().err(),
        "receipt_window_limit": 256,
        "decoded_frames_verified": false,
        "motion_acceptance": "NOT_ASSESSED"
    });
    let result = match attempted {
        Ok(result) => result,
        Err(error) => {
            return Err(retain_camera_error(evidence_path, &mut evidence, error, None).into());
        }
    };
    if let Err(error) = receipt_result {
        return Err(retain_camera_error(evidence_path, &mut evidence, error, Some(result)).into());
    }
    // The canonical coordinator already checked the saved plan, profile,
    // getter digest, native provenance and MP4 custody. Retain that result.
    drop(coordinator);
    let reopened = StudioService::open(database)?;
    if reopened.project(project.id)? != project {
        return Err("saved camera project changed during native realization".into());
    }
    let start = reopened
        .latest_production_receipt(project.id, &start_id)?
        .ok_or("original camera start receipt was not durable")?;
    let terminal = reopened
        .latest_production_receipt(project.id, &format!("{}:result", target.request_id))?
        .ok_or("original camera result receipt was not durable")?;
    if start.command != "driver.manim-community.camera.render.start"
        || terminal.command != "driver.manim-community.render.result"
        || start.stage != "completed"
        || terminal.stage != "completed"
    {
        return Err("camera start/result did not retain their original durable commands".into());
    }
    evidence["native_manim_camera_dispatch"] = json!("PASS");
    evidence["result"] = result;
    evidence["original_start_receipt"] = json!(start);
    evidence["original_result_receipt"] = json!(terminal);
    write_camera_evidence(evidence_path, &evidence)?;
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
        "realize-camera" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            let target = CameraTarget {
                project_id: text_arg(&mut args).parse()?,
                scene_id: text_arg(&mut args).parse()?,
                deliverable_id: text_arg(&mut args).parse()?,
                request_id: text_arg(&mut args),
            };
            if args.next().is_some() {
                usage();
            }
            realize_camera(&database, &connection, &evidence, target).await
        }
        _ => usage(),
    }
}

#[cfg(test)]
mod camera_custody_tests {
    use super::*;

    #[test]
    fn diagnostic_write_failure_preserves_original_native_unknown() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        let diagnostic_path = evidence_path.with_extension("diagnostic.json");
        std::fs::write(&diagnostic_path, b"existing custody evidence").unwrap();
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::Timeout,
            "original native outcome is unknown",
        )
        .uncertain();
        let expected = serde_json::to_value(&original).unwrap();
        let mut evidence = json!({"request_id": "original-attempt"});
        let returned = retain_camera_error(&evidence_path, &mut evidence, original, None);
        assert_eq!(serde_json::to_value(&returned).unwrap(), expected);
        assert_eq!(evidence["native_manim_camera_dispatch"], "UNKNOWN");
        assert_eq!(evidence["error"], expected);
        assert_eq!(
            std::fs::read(&diagnostic_path).unwrap(),
            b"existing custody evidence"
        );
    }

    #[test]
    fn receipt_read_failure_retains_opaque_result_without_publishing_pass() {
        let temporary = tempfile::tempdir().unwrap();
        let evidence_path = temporary.path().join("camera.json");
        let opaque_result = json!({"opaque_retention_marker": [3, 7, 11]});
        let original = semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::BackendFailed,
            "original receipt read failed",
        );
        let expected = serde_json::to_value(&original).unwrap();
        let mut evidence = json!({"request_id": "receipt-custody-attempt"});
        let returned = retain_camera_error(
            &evidence_path,
            &mut evidence,
            original,
            Some(opaque_result.clone()),
        );
        assert_eq!(serde_json::to_value(&returned).unwrap(), expected);
        let stored: serde_json::Value = serde_json::from_slice(
            &std::fs::read(evidence_path.with_extension("diagnostic.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(stored, evidence);
        assert_eq!(stored["result"], opaque_result);
        assert_eq!(stored["error"], expected);
        assert_eq!(stored["native_manim_camera_dispatch"], "UNVERIFIED");
        assert_eq!(stored["failure_stage"], "receipt_read");
        assert!(!evidence_path.exists());
    }
}
