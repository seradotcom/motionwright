use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::{
    multi_renderer::build_blender_original_scene_plan,
    production::{
        BlenderOriginalJournal, BlenderOriginalRequest, ProductionConnection, ProductionCoordinator,
    },
};
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
        "usage: native-blender-e2e <seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON | seed-original DATABASE OUTPUT_DIRECTORY | original DATABASE CONNECTION ATTEMPT_JSON EVIDENCE_JSON>"
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

fn seed_project(
    database: &Path,
) -> Result<motionwright_domain::Project, Box<dyn std::error::Error>> {
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

    Ok(project)
}

fn seed(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let project = seed_project(database)?;
    let scene_id = project
        .scenes
        .first()
        .ok_or("seeded Blender project has no Scene")?
        .id;
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
        "driver.blender.object.create",
        "driver.blender.object.transform",
        "driver.blender.collection.link",
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

type EvidenceResult<T> = Result<T, Box<dyn std::error::Error>>;

fn write_new_bytes(path: &Path, body: &[u8]) -> EvidenceResult<()> {
    let parent = path
        .parent()
        .ok_or("original Blender evidence has no parent")?;
    if !parent.is_dir() {
        return Err("original Blender evidence parent must already exist".into());
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(body)?;
    file.sync_all()?;
    Ok(())
}

fn write_new_json(path: &Path, value: &serde_json::Value) -> EvidenceResult<()> {
    let mut bytes = serde_json::to_vec_pretty(value)?;
    bytes.push(b'\n');
    write_new_bytes(path, &bytes)
}

fn read_attempt(path: &Path) -> EvidenceResult<BlenderOriginalRequest> {
    use std::io::Read;
    const MAX_ATTEMPT_BYTES: u64 = 16_384;
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("original Blender attempt must be a regular file".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err("original Blender attempt cannot be a reparse point".into());
        }
    }
    let file = std::fs::File::open(path)?;
    if !file.metadata()?.is_file() {
        return Err("original Blender attempt descriptor is not a regular file".into());
    }
    let mut bytes = Vec::new();
    file.take(MAX_ATTEMPT_BYTES + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_ATTEMPT_BYTES {
        return Err("original Blender attempt exceeds its bounded profile".into());
    }
    Ok(serde_json::from_slice(&bytes)?)
}

fn seed_original(database: &Path, output: &Path) -> EvidenceResult<serde_json::Value> {
    use sha2::{Digest, Sha256};
    if !output.is_dir() || output.read_dir()?.next().is_some() {
        return Err("original Blender seed output must start as an empty directory".into());
    }
    let written = seed_project(database)?;
    let service = StudioService::open(database)?;
    let project = service.project(written.id)?;
    let source = serde_json::to_vec(&project)?;
    if source != serde_json::to_vec(&written)? {
        return Err("original Blender seed changed when SQLite was reopened".into());
    }
    let scene = project
        .scenes
        .first()
        .ok_or("original Blender seed has no Scene")?;
    let candidates = scene
        .nodes
        .iter()
        .filter(|node| node.kind == "circle")
        .collect::<Vec<_>>();
    if candidates.len() != 1 {
        return Err(
            "original Blender fixture requires exactly one explicit circle edit target".into(),
        );
    }
    let plan = build_blender_original_scene_plan(&project, scene.id)?;
    let source_sha256 = format!("{:x}", Sha256::digest(&source));
    if source_sha256 != plan.project_json_sha256 {
        return Err("original Blender source bytes disagree with the canonical plan".into());
    }
    write_new_bytes(&output.join("source-project.json"), &source)?;
    write_new_json(
        &output.join("source-plan.json"),
        &serde_json::to_value(&plan)?,
    )?;
    let seeded = json!({
        "schema": "motionwright.blender.original-scene-seed/1",
        "fixture_kind": "synthetic",
        "project_id": project.id, "resource": project.resource_key(),
        "generation": project.generation, "revision": project.revision,
        "expected": project.stamp(), "scene_id": scene.id,
        "mesh_count": plan.contribution.objects.len(),
        "edit_node_id": candidates[0].id,
        "project_json": {"path": "source-project.json", "sha256": source_sha256, "bytes": source.len()},
        "plan_path": "source-plan.json", "sqlite_reopened": true,
        "native_execution": "NOT_RUN", "human_acceptance": false
    });
    write_new_json(&output.join("seed.json"), &seeded)?;
    Ok(seeded)
}

fn complete_original_receipts(
    service: &StudioService,
    project_id: Uuid,
) -> EvidenceResult<serde_json::Value> {
    const MAX_RECEIPTS: u64 = 20_000;
    let watermark = service.production_receipt_watermark(project_id)?;
    if watermark.count > MAX_RECEIPTS {
        return Err("original Blender receipt snapshot exceeds its explicit bound".into());
    }
    let mut receipts = Vec::new();
    let mut before = None;
    while (receipts.len() as u64) < watermark.count {
        let page =
            service.production_receipts_snapshot_page(project_id, &watermark, before, 256)?;
        if page.is_empty() {
            return Err(
                "original Blender receipt snapshot ended before its watermark count".into(),
            );
        }
        for receipt in &page {
            if receipt.project_id != project_id || before.is_some_and(|prior| receipt.id >= prior) {
                return Err(
                    "original Blender receipt snapshot is not a strictly descending keyset".into(),
                );
            }
            before = Some(receipt.id);
        }
        receipts.extend(page);
        if receipts.len() as u64 > watermark.count {
            return Err("original Blender receipt snapshot exceeded its watermark count".into());
        }
    }
    if service.production_receipt_watermark(project_id)? != watermark {
        return Err("original Blender receipt snapshot changed after its final page".into());
    }
    Ok(json!({
        "watermark": {
            "latest_id": watermark.latest_id,
            "count": watermark.count,
        },
        "receipts": receipts,
        "complete": true,
    }))
}

fn capture_original_custody(
    database: &Path,
    project_before: &motionwright_domain::Project,
) -> EvidenceResult<serde_json::Value> {
    let reopened = StudioService::open(database)?;
    let project = reopened.project(project_before.id)?;
    if serde_json::to_vec(&project)? != serde_json::to_vec(project_before)? {
        return Err("original Blender native phase changed its canonical Project source".into());
    }
    let receipts = complete_original_receipts(&reopened, project.id)?;
    Ok(json!({
        "sqlite_reopened": true, "project_unchanged": true,
        "project": project, "production": receipts
    }))
}

fn private_original_phase_snapshot(
    evidence: &serde_json::Value,
    append_custody: &serde_json::Value,
) -> serde_json::Value {
    // This copy is written only to the existing private evidence destination.
    // The returned CLI value and the diagnostic stream keep their prior shape.
    let mut private = evidence.clone();
    private["coordinator_append_custody"] = append_custody.clone();
    private
}

fn finish_original_terminal<C, P, W>(
    terminal: semwright_native_sdk::Result<serde_json::Value>,
    mut evidence: serde_json::Value,
    capture: C,
    publish: P,
    diagnostic: &mut W,
) -> EvidenceResult<serde_json::Value>
where
    C: FnOnce() -> EvidenceResult<serde_json::Value>,
    P: FnOnce(&serde_json::Value) -> EvidenceResult<()>,
    W: Write,
{
    // Keep the exact native terminal before invoking any later custody operation.
    evidence["native_terminal"] = match &terminal {
        Ok(value) => json!({"status": "ok", "value": value}),
        Err(error) => json!({"status": "error", "error": error}),
    };
    evidence["phase_contract_verified"] = json!(false);
    evidence["custody_errors"] = json!([]);
    let mut custody_error = None;
    match capture() {
        Ok(value) => evidence["custody"] = value,
        Err(error) => {
            evidence["custody_errors"]
                .as_array_mut()
                .expect("array assigned")
                .push(json!({"stage": "capture", "message": error.to_string()}));
            custody_error = Some(error);
        }
    }
    // The persisted snapshot precedes the publication acknowledgement. Its
    // flag stays false; only the returned value may acknowledge successful sync.
    // create_new preserves an existing destination. A failed write may leave a
    // partial new file; that file is retained and never treated as verification.
    if let Err(error) = publish(&evidence) {
        evidence["phase_contract_verified"] = json!(false);
        evidence["custody_errors"]
            .as_array_mut()
            .expect("array assigned")
            .push(json!({"stage": "publish", "message": error.to_string()}));
        if custody_error.is_none() {
            custody_error = Some(error);
        }
    }
    if terminal.is_err() || custody_error.is_some() {
        let _ = serde_json::to_writer(&mut *diagnostic, &evidence);
        let _ = diagnostic.write_all(b"\n");
    }
    match terminal {
        Err(error) => Err(error.into()),
        Ok(_) => match custody_error {
            Some(error) => Err(error),
            None => {
                evidence["phase_contract_verified"] = json!(true);
                Ok(evidence)
            }
        },
    }
}

async fn original_phase(
    database: &Path,
    connection_path: &Path,
    attempt_path: &Path,
    evidence_path: &Path,
) -> EvidenceResult<serde_json::Value> {
    match std::fs::symlink_metadata(evidence_path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => (),
        Err(error) => return Err(error.into()),
        Ok(_) => return Err("original Blender evidence destination already exists".into()),
    }
    let request = read_attempt(attempt_path)?;
    let service = StudioService::open(database)?;
    let project = service.project(request.project_id)?;
    let connection = ProductionConnection::load(connection_path)?;
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let mut journal = BlenderOriginalJournal::default();
    let terminal = coordinator
        .realize_original_blender_scene(&request, &mut journal)
        .await;
    let append_custody = coordinator.private_receipt_custody_snapshot();
    // Drop the only service handle before checking the same SQLite source anew.
    drop(coordinator);
    let evidence = json!({
        "schema": "motionwright.blender.original-scene-phase-evidence/1",
        "request": request, "journal": journal,
        "geometry_content_verified": false, "human_acceptance": false
    });
    finish_original_terminal(
        terminal,
        evidence,
        || capture_original_custody(database, &project),
        |value| {
            write_new_json(
                evidence_path,
                &private_original_phase_snapshot(value, &append_custody),
            )
        },
        &mut std::io::stderr().lock(),
    )
}

fn print_json(value: &serde_json::Value) -> EvidenceResult<()> {
    let mut stdout = std::io::stdout().lock();
    serde_json::to_writer(&mut stdout, value)?;
    stdout.write_all(b"\n")?;
    stdout.flush()?;
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
        "seed-original" => {
            let database = path_arg(&mut args);
            let output = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            print_json(&seed_original(&database, &output)?)
        }
        "original" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let attempt = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let result = original_phase(&database, &connection, &attempt, &evidence).await?;
            print_json(&result)
        }
        _ => usage(),
    }
}

#[cfg(test)]
mod original_tests {
    use super::*;
    use semwright_native_sdk::{Error, ErrorCode};
    use std::cell::RefCell;

    struct BrokenDiagnostic;
    impl Write for BrokenDiagnostic {
        fn write(&mut self, _buffer: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("synthetic diagnostic sink failure"))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Err(std::io::Error::other("synthetic diagnostic flush failure"))
        }
    }

    #[test]
    fn original_seed_uses_reopened_project_bytes_and_the_canonical_plan() {
        use sha2::{Digest, Sha256};
        let root = tempfile::tempdir().unwrap();
        let output = root.path().join("seed");
        std::fs::create_dir(&output).unwrap();
        let database = root.path().join("source.sqlite3");
        let seeded = seed_original(&database, &output).unwrap();
        let body = std::fs::read(output.join("source-project.json")).unwrap();
        let project: motionwright_domain::Project = serde_json::from_slice(&body).unwrap();
        let reopened = StudioService::open(database)
            .unwrap()
            .project(project.id)
            .unwrap();
        assert_eq!(body, serde_json::to_vec(&reopened).unwrap());
        assert_ne!(body.last(), Some(&b'\n'));
        assert_eq!(
            seeded["project_json"]["sha256"],
            json!(format!("{:x}", Sha256::digest(&body)))
        );
        let plan = build_blender_original_scene_plan(&reopened, reopened.scenes[0].id).unwrap();
        let saved: serde_json::Value =
            serde_json::from_slice(&std::fs::read(output.join("source-plan.json")).unwrap())
                .unwrap();
        assert_eq!(saved, serde_json::to_value(plan).unwrap());
        assert_eq!(
            seeded["expected"],
            serde_json::to_value(reopened.stamp()).unwrap()
        );
        assert_eq!(seeded["fixture_kind"], json!("synthetic"));
        assert_eq!(seeded["native_execution"], json!("NOT_RUN"));
    }

    #[test]
    fn original_receipt_snapshot_keeps_rows_beyond_the_latest_window() {
        use motionwright_storage::ProductionReceiptInput;
        let root = tempfile::tempdir().unwrap();
        let database = root.path().join("source.sqlite3");
        let service = StudioService::open(&database).unwrap();
        let project = service
            .create_project("Synthetic receipt snapshot")
            .unwrap();
        let empty = complete_original_receipts(&service, project.id).unwrap();
        assert_eq!(
            empty,
            json!({
                "watermark": {"latest_id": null, "count": 0},
                "receipts": [],
                "complete": true,
            })
        );
        let mut expected_ids = BTreeSet::new();
        for index in 0..270 {
            let receipt = service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: project.id,
                    generation: project.generation,
                    revision: project.revision,
                    request_id: format!("synthetic-{index}"),
                    request_sha256: "ab".repeat(32),
                    command: "driver.blender.scene.inspect".into(),
                    stage: "completed".into(),
                    payload: json!({"synthetic_contract_only": true, "index": index}),
                })
                .unwrap();
            expected_ids.insert(receipt.id);
        }
        assert_eq!(
            service.production_receipts(project.id, 9999).unwrap().len(),
            256
        );
        drop(service);
        let reopened = StudioService::open(database).unwrap();
        let captured = complete_original_receipts(&reopened, project.id).unwrap();
        assert_eq!(captured["receipts"].as_array().unwrap().len(), 270);
        assert_eq!(captured["watermark"]["count"], json!(270));
        assert_eq!(
            captured["watermark"]["latest_id"],
            json!(expected_ids.last().copied())
        );
        assert_eq!(captured["complete"], json!(true));
        let indices = captured["receipts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|receipt| receipt["payload"]["index"].as_u64().unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(indices, (0..270).collect());
    }

    #[test]
    fn original_typed_error_survives_capture_publication_and_diagnostic_failures() {
        for original in [
            Error::new(
                ErrorCode::ConsentRequired,
                "synthetic typed consent response",
            ),
            Error::new(
                ErrorCode::BackendFailed,
                "synthetic uncertain original outcome",
            )
            .uncertain(),
        ] {
            let saved = RefCell::new(None);
            let expected = serde_json::to_value(&original).unwrap();
            let returned = finish_original_terminal(
                Err(original),
                json!({"fixture_only": true}),
                || Err("synthetic SQLite custody failure".into()),
                |value| {
                    saved.replace(Some(value.clone()));
                    Err("synthetic publication failure".into())
                },
                &mut BrokenDiagnostic,
            )
            .unwrap_err();
            let typed = returned.downcast_ref::<Error>().unwrap();
            assert_eq!(serde_json::to_value(typed).unwrap(), expected);
            let published = saved.into_inner().unwrap();
            assert_eq!(published["native_terminal"]["error"], expected);
            assert_eq!(published["phase_contract_verified"], json!(false));
            assert_eq!(published["custody_errors"][0]["stage"], json!("capture"));
        }
    }

    #[test]
    fn original_ok_is_retained_when_reopen_or_publication_fails() {
        let original =
            json!({"synthetic_native_ack": "retained", "geometry_content_verified": false});
        for capture_fails in [true, false] {
            let saved = RefCell::new(None);
            let mut diagnostics = Vec::new();
            let error = finish_original_terminal(
                Ok(original.clone()),
                json!({"fixture_only": true}),
                || {
                    if capture_fails {
                        Err("original custody failure".into())
                    } else {
                        Ok(json!({"synthetic_snapshot": true}))
                    }
                },
                |value| {
                    saved.replace(Some(value.clone()));
                    Err("later publication failure".into())
                },
                &mut diagnostics,
            )
            .unwrap_err();
            assert_eq!(
                error.to_string(),
                if capture_fails {
                    "original custody failure"
                } else {
                    "later publication failure"
                }
            );
            let published = saved.into_inner().unwrap();
            assert_eq!(published["native_terminal"]["value"], original);
            assert_eq!(published["phase_contract_verified"], json!(false));
            let fallback: serde_json::Value = serde_json::from_slice(&diagnostics).unwrap();
            assert_eq!(fallback["native_terminal"]["value"], original);
            assert_eq!(fallback["phase_contract_verified"], json!(false));
            assert!(
                fallback["custody_errors"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|item| item["stage"] == "publish")
            );
        }
    }

    #[test]
    fn original_publication_ack_is_returned_only_after_the_publisher_finishes() {
        let persisted = RefCell::new(None);
        let result = finish_original_terminal(
            Ok(json!({"synthetic_contract_only": true})),
            json!({}),
            || Ok(json!({"synthetic_snapshot": true})),
            |value| {
                persisted.replace(Some(value.clone()));
                Ok(())
            },
            &mut BrokenDiagnostic,
        )
        .unwrap();
        assert_eq!(
            persisted.into_inner().unwrap()["phase_contract_verified"],
            json!(false)
        );
        assert_eq!(result["phase_contract_verified"], json!(true));
    }

    #[test]
    fn original_evidence_writer_preserves_an_existing_attempt() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("attempt.json");
        std::fs::write(&path, b"retained prior bytes").unwrap();
        assert!(write_new_json(&path, &json!({"replacement": true})).is_err());
        assert_eq!(std::fs::read(&path).unwrap(), b"retained prior bytes");
    }

    #[test]
    fn original_append_custody_is_written_only_to_the_private_snapshot() {
        for success in [true, false] {
            let append_custody = json!({
                "schema": "motionwright.production.private-receipt-custody/1",
                "durable": false,
                "entries": if success { json!([]) } else {
                    json!([{"broker_request_id": "private-append-terminal-sentinel"}])
                },
            });
            let persisted = RefCell::new(None);
            let mut diagnostic = Vec::new();
            let terminal = if success {
                Ok(json!({"synthetic_original": true}))
            } else {
                Err(Error::new(ErrorCode::BackendFailed, "original native failure").uncertain())
            };
            let result = finish_original_terminal(
                terminal,
                json!({"schema": "synthetic-private-publication-test"}),
                || Ok(json!({"synthetic_capture": true})),
                |value| {
                    *persisted.borrow_mut() =
                        Some(private_original_phase_snapshot(value, &append_custody));
                    Ok(())
                },
                &mut diagnostic,
            );
            let saved = persisted.into_inner().unwrap();
            assert_eq!(saved["coordinator_append_custody"], append_custody);
            assert_eq!(saved["phase_contract_verified"], false);
            assert!(
                !String::from_utf8(diagnostic)
                    .unwrap()
                    .contains("private-append-terminal-sentinel")
            );
            if success {
                let returned = result.unwrap();
                assert!(returned.get("coordinator_append_custody").is_none());
                assert_eq!(returned["phase_contract_verified"], true);
                let mut expected = saved;
                expected
                    .as_object_mut()
                    .unwrap()
                    .remove("coordinator_append_custody");
                expected["phase_contract_verified"] = json!(true);
                assert_eq!(returned, expected);
            } else {
                assert_eq!(
                    result.unwrap_err().to_string(),
                    "BackendFailed: original native failure"
                );
            }
        }
    }
}
