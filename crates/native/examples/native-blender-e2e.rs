use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::{
    multi_renderer::build_blender_original_scene_plan,
    production::{
        BLENDER_PRODUCT_RECIPE_MEDIA_TYPE, BlenderOriginalJournal, BlenderOriginalRequest,
        BlenderProductAttemptProjection, BlenderProductAttemptRequest, BlenderProductAttemptState,
        BlenderProductCycleJournal, BlenderProductCycleRequest, BlenderProductSourceScope,
        BlenderProductSourceSnapshot, ProductionConnection, ProductionCoordinator,
        decode_blender_product_recipe, read_blender_product_attempt, read_blender_product_source,
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
        "usage: native-blender-e2e <seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON | seed-original DATABASE OUTPUT_DIRECTORY | original DATABASE CONNECTION ATTEMPT_JSON EVIDENCE_JSON | recipe-product ORIGINAL_RECIPE_JSON NAMESPACE CAMERA_CURVE ANNOTATION_AFTER | seed-product DATABASE ORIGINAL_RECIPE_JSON OUTPUT_DIRECTORY | product-cycle DATABASE CREATE_CONNECTION EDIT_CONNECTION VERIFY_CONNECTION ATTEMPT_JSON EVIDENCE_JSON>"
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
    let scene = project
        .scenes
        .first()
        .ok_or("seeded Blender project has no Scene")?;
    print_static_json(&json!({
        "project_id": project.id,
        "resource": project.resource_key(),
        "generation": project.generation,
        "revision": project.revision,
        "scene_id": scene.id,
        "source_nodes": &scene.nodes,
        "mesh_count": 2
    }))?;
    Ok(())
}

const STATIC_PRIVATE_JSON_MAX_BYTES: usize = 8 * 1024 * 1024;
const STATIC_PROCESS_JSON_MAX_BYTES: usize = 4 * 1024 * 1024;

struct StaticJsonBuffer {
    bytes: Vec<u8>,
    maximum: usize,
}

impl Write for StaticJsonBuffer {
    fn write(&mut self, input: &[u8]) -> std::io::Result<usize> {
        if input.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other(
                "static Blender JSON byte budget exceeded",
            ));
        }
        self.bytes.extend_from_slice(input);
        Ok(input.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn bounded_static_json(
    value: &serde_json::Value,
    pretty: bool,
    maximum: usize,
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut output = StaticJsonBuffer {
        bytes: Vec::new(),
        maximum: maximum
            .checked_sub(1)
            .ok_or("static JSON budget cannot be zero")?,
    };
    if pretty {
        serde_json::to_writer_pretty(&mut output, value)?;
    } else {
        serde_json::to_writer(&mut output, value)?;
    }
    output.bytes.push(b'\n');
    Ok(output.bytes)
}

fn write_static_private_json(path: &Path, value: &serde_json::Value) -> EvidenceResult<()> {
    let body = bounded_static_json(value, true, STATIC_PRIVATE_JSON_MAX_BYTES)?;
    write_new_bytes(path, &body)
}

fn print_static_json(value: &serde_json::Value) -> EvidenceResult<()> {
    let body = bounded_static_json(value, false, STATIC_PROCESS_JSON_MAX_BYTES)?;
    std::io::stdout().lock().write_all(&body)?;
    Ok(())
}

fn retain_static_failure(
    error: semwright_native_sdk::Error,
    diagnostic: &serde_json::Value,
    evidence_path: &Path,
) -> semwright_native_sdk::Error {
    let path = evidence_path.with_file_name("motionwright-blender-diagnostic.json");
    if let Err(custody_error) = write_static_private_json(&path, diagnostic) {
        // This records a derived publication failure, never a replacement for
        // the native error or an invented complete receipt snapshot. SQLite
        // and its actual sidecars remain the original source of receipt bytes.
        let cause = json!({
            "diagnostic_publication": "INCOMPLETE",
            "diagnostic_is_derived": true,
            "diagnostic_byte_budget": STATIC_PRIVATE_JSON_MAX_BYTES,
            "native_error_replaced": false,
            "native_retry_executed": false,
            "receipt_originals": "existing SQLite database and actual sidecars",
            "cause": custody_error.to_string(),
        });
        let _ = write_static_private_json(
            &evidence_path.with_file_name("motionwright-blender-diagnostic-custody.json"),
            &cause,
        );
        let _ = writeln!(
            std::io::stderr().lock(),
            "Static Blender diagnostic publication incomplete; original native error retained"
        );
    }
    error
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
    // Retain raw authoring fields read from this actual persisted Project.
    // The independent Python oracle derives geometry from these CanvasNodes;
    // the native contribution or exported GLB is not its source of intent.
    let source_nodes = serde_json::to_value(&scene.nodes)?;

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
            let receipt_query = coordinator.receipts(project.id, 256);
            let receipt_query_succeeded = receipt_query.is_ok();
            let receipts = receipt_query.unwrap_or_default();
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
                "receipt_query_succeeded": receipt_query_succeeded,
            });
            return Err(retain_static_failure(error, &diagnostic, evidence_path).into());
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
        "source_nodes": source_nodes,
        "result": result,
        "receipt_count": receipts.len(),
        "completed_commands": completed_commands,
    });
    write_static_private_json(evidence_path, &evidence)?;
    print_static_json(&evidence)?;
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

fn observe_original_receipt_page(
    page: &[motionwright_storage::ProductionReceipt],
    project_id: Uuid,
    seen: &mut BTreeSet<Uuid>,
    before: &mut Option<Uuid>,
) -> EvidenceResult<()> {
    for receipt in page {
        // UUID identifies an opaque service cursor. Durable commit order is
        // supplied by the snapshot API, never reconstructed from UUID or time.
        if receipt.project_id != project_id || !seen.insert(receipt.id) {
            return Err(
                "original Blender receipt snapshot has a foreign or duplicate identity".into(),
            );
        }
        *before = Some(receipt.id);
    }
    Ok(())
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
    let mut seen = BTreeSet::new();
    while (receipts.len() as u64) < watermark.count {
        let page =
            service.production_receipts_snapshot_page(project_id, &watermark, before, 256)?;
        if page.is_empty() {
            return Err(
                "original Blender receipt snapshot ended before its watermark count".into(),
            );
        }
        if before.is_none() && page.first().map(|receipt| receipt.id) != watermark.latest_id {
            return Err(
                "original Blender first receipt differs from the durable snapshot head".into(),
            );
        }
        observe_original_receipt_page(&page, project_id, &mut seen, &mut before)?;
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

fn emit_product_recipe(
    output: &Path,
    namespace: &str,
    camera_curve: &str,
    annotation_after: &str,
) -> EvidenceResult<serde_json::Value> {
    use motionwright_native::blender_product_scene::{
        CameraCurve, PRODUCT_PROFILE, ProductEdit, ProductScene,
    };
    use sha2::{Digest, Sha256};
    let curve = match camera_curve {
        "linear" => CameraCurve::Linear,
        "bezier_auto_clamped" => CameraCurve::BezierAutoClamped,
        _ => return Err("product recipe requires an explicit supported camera curve".into()),
    };
    let source = ProductScene::vessel(namespace, curve);
    source.validate()?;
    let edited = source.edited(&ProductEdit::Annotation(annotation_after.into()))?;
    if edited.annotation == source.annotation {
        return Err("product recipe requires a nontrivial annotation edit".into());
    }
    product_destination_absent(output)?;
    let mut bytes = serde_json::to_vec_pretty(&source)?;
    bytes.push(b'\n');
    write_new_bytes(output, &bytes)?;
    Ok(json!({
        "schema": "motionwright.blender.product-recipe-original/1",
        "source_constructor": "ProductScene::vessel",
        "profile": PRODUCT_PROFILE,
        "product_scene": source,
        "annotation_after": edited.annotation,
        "recipe": {
            "path": output,
            "bytes": bytes.len(),
            "sha256": format!("{:x}", Sha256::digest(&bytes))
        },
        "native_execution": "NOT_RUN",
        "sqlite_opened": false,
        "source_acceptance_inherited": false,
        "execution_grant_inherited": false,
        "delivery_profile_selected": false,
        "full_1200_frame_render": false,
        "human_acceptance": false
    }))
}

const PRODUCT_CALLER_RECIPE_MAX_BYTES: u64 = 64 * 1024;

fn open_product_input_original(
    path: &Path,
    before: &std::fs::Metadata,
) -> EvidenceResult<(std::fs::File, std::fs::Metadata)> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        // The named input can change after symlink_metadata. Refuse a symlink
        // during the actual open and never wait for a FIFO writer. The retained
        // descriptor must still pass the original kind/identity checks below.
        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }
    let file = options.open(path)?;
    let opened = file.metadata()?;
    if !opened.is_file()
        || opened.len() != before.len()
        || opened.modified()? != before.modified()?
    {
        return Err("product input changed before its bounded read".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if before.dev() != opened.dev() || before.ino() != opened.ino() {
            return Err("product input identity changed before its bounded read".into());
        }
    }
    Ok((file, opened))
}

fn read_product_input(path: &Path, maximum: u64) -> EvidenceResult<Vec<u8>> {
    use std::io::Read;
    let before = std::fs::symlink_metadata(path)?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.len() == 0
        || before.len() > maximum
    {
        return Err("product input must be a nonempty bounded regular file".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if before.file_attributes() & 0x400 != 0 {
            return Err("product input cannot be a reparse point".into());
        }
    }
    let (mut file, opened) = open_product_input_original(path, &before)?;
    let mut bytes = Vec::new();
    Read::by_ref(&mut file)
        .take(maximum + 1)
        .read_to_end(&mut bytes)?;
    let after = file.metadata()?;
    let current = std::fs::symlink_metadata(path)?;
    if bytes.len() as u64 != before.len()
        || bytes.len() as u64 > maximum
        || !current.is_file()
        || current.file_type().is_symlink()
        || after.len() != before.len()
        || current.len() != before.len()
        || after.modified()? != before.modified()?
        || current.modified()? != before.modified()?
    {
        return Err("product input changed during its bounded read".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if current.dev() != opened.dev()
            || current.ino() != opened.ino()
            || after.dev() != opened.dev()
            || after.ino() != opened.ino()
        {
            return Err("product input identity changed during its bounded read".into());
        }
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if current.file_attributes() & 0x400 != 0 {
            return Err("product input became a reparse point during its read".into());
        }
    }
    Ok(bytes)
}

fn product_destination_absent(path: &Path) -> EvidenceResult<()> {
    match std::fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error.into()),
        Ok(_) => Err("product destination already exists and will not be reused".into()),
    }
}

fn read_product_request(path: &Path) -> EvidenceResult<BlenderProductCycleRequest> {
    Ok(serde_json::from_slice(&read_product_input(path, 16_384)?)?)
}

fn seed_product(
    database: &Path,
    recipe_path: &Path,
    output: &Path,
) -> EvidenceResult<serde_json::Value> {
    use motionwright_domain::{Fidelity, FidelityReport, NativeCapsule};
    use sha2::{Digest, Sha256};

    product_destination_absent(database)?;
    let directory = std::fs::symlink_metadata(output)?;
    if !directory.is_dir()
        || directory.file_type().is_symlink()
        || output.read_dir()?.next().is_some()
    {
        return Err("product seed output must start as an empty regular directory".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if directory.file_attributes() & 0x400 != 0 {
            return Err("product seed output cannot be a reparse point".into());
        }
    }
    let recipe_bytes = read_product_input(recipe_path, PRODUCT_CALLER_RECIPE_MAX_BYTES)?;
    let recipe = decode_blender_product_recipe(&recipe_bytes)?;
    let recipe_sha256 = format!("{:x}", Sha256::digest(&recipe_bytes));
    // Import the retained original bytes, not a serialization of the decoded recipe.
    let retained_recipe = output.join("source-recipe.json");
    write_new_bytes(&retained_recipe, &recipe_bytes)?;

    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("product seed database did not remain empty".into());
    }
    let mut project = service.create_project("Editable Blender product source")?;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "native-product-seed-scene",
            &Change::AddScene {
                name: "Editable product vessel".into(),
                objective: "Persist the supplied fixed Blender product recipe".into(),
                duration_seconds: 40,
            },
        )?
        .project;
    let scene_id = project.scenes[0].id;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "native-product-seed-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            },
        )?
        .project;
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "native-product-seed-recipe",
            &retained_recipe,
            "source-recipe.json".into(),
            BLENDER_PRODUCT_RECIPE_MEDIA_TYPE.into(),
        )?
        .project;
    if project.assets.len() != 1 {
        return Err("product seed must have exactly one imported original recipe".into());
    }
    let asset = project.assets[0].clone();
    if asset.content_sha256.as_deref() != Some(recipe_sha256.as_str())
        || asset.media_type != BLENDER_PRODUCT_RECIPE_MEDIA_TYPE
    {
        return Err("product seed import differs from the retained original recipe".into());
    }
    let capsule_id = Uuid::now_v7();
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "native-product-seed-capsule",
            &Change::UpsertNativeCapsule {
                capsule: NativeCapsule {
                    id: capsule_id,
                    scene_id,
                    source_asset_id: asset.id,
                    source_sha256: recipe_sha256.clone(),
                    label: "Editable Blender product recipe".into(),
                    fidelity: FidelityReport {
                        renderer: "Blender".into(),
                        renderer_version: "unobserved".into(),
                        visual: Fidelity::Unavailable,
                        temporal: Fidelity::Unavailable,
                        structural: Fidelity::Unavailable,
                        editable: Fidelity::Unavailable,
                        losses: vec!["Recipe source only; no native execution.".into()],
                        evidence_sha256: None,
                    },
                    editable_parameters: vec!["product.annotation.body".into()],
                    native_editor_hint: "Blender".into(),
                    property_fidelity: vec![],
                },
            },
        )?
        .project;
    drop(service);

    let reopened = StudioService::open(database)?;
    if reopened.project(project.id)? != project {
        return Err("product seed changed when its SQLite source was reopened".into());
    }
    let snapshot = read_blender_product_source(
        &reopened,
        &BlenderProductSourceScope {
            project_id: project.id,
            expected: project.stamp(),
            scene_id,
            capsule_id,
        },
    )?;
    if snapshot.project != project
        || snapshot.source_kind != "authored_recipe"
        || snapshot.current_source.asset_id != asset.id
        || snapshot.current_source.sha256 != recipe_sha256
        || snapshot.original_recipe != snapshot.current_source
        || snapshot.effective_recipe != recipe
        || !snapshot.source_cas_verified
        || snapshot.lineage.is_some()
        || snapshot.attempt.is_some()
        || snapshot.native_dispatch_performed
        || snapshot.execution_grant_inherited
        || snapshot.source_acceptance_inherited
    {
        return Err("product seed readback does not describe its saved original recipe".into());
    }
    let cas_bytes = {
        let handle = reopened.store();
        let store = handle.lock();
        store.read_blob(&recipe_sha256, PRODUCT_CALLER_RECIPE_MAX_BYTES)?
    };
    if cas_bytes != recipe_bytes {
        return Err("product seed CAS does not preserve the exact original recipe bytes".into());
    }
    let project_bytes = serde_json::to_vec(&project)?;
    write_new_bytes(&output.join("source-project.json"), &project_bytes)?;
    write_new_json(
        &output.join("source-readback.json"),
        &serde_json::to_value(&snapshot)?,
    )?;
    let seeded = json!({
        "schema": "motionwright.blender.product-scene-seed/1",
        "source_kind": "caller_supplied_fixed_product_recipe",
        "project_id": project.id,
        "resource": project.resource_key(),
        "generation": project.generation,
        "revision": project.revision,
        "expected": project.stamp(),
        "scene_id": scene_id,
        "capsule_id": capsule_id,
        "source_asset_id": asset.id,
        "source_sha256": recipe_sha256,
        "product_scene": recipe,
        "recipe": {
            "path": "source-recipe.json",
            "sha256": recipe_sha256,
            "bytes": recipe_bytes.len()
        },
        "project_json": {
            "path": "source-project.json",
            "sha256": format!("{:x}", Sha256::digest(&project_bytes)),
            "bytes": project_bytes.len()
        },
        "source_readback_path": "source-readback.json",
        "recipe_cas_verified": true,
        "sqlite_reopened": true,
        "native_execution": "NOT_RUN",
        "source_acceptance_inherited": false,
        "execution_grant_inherited": false,
        "delivery_profile_selected": false,
        "full_1200_frame_render": false,
        "decoded_pixel_difference_verified": false,
        "visual_acceptance": false,
        "human_acceptance": false
    });
    write_new_json(&output.join("seed.json"), &seeded)?;
    Ok(seeded)
}

fn product_completed_readback(
    request: &BlenderProductCycleRequest,
    project_before: &motionwright_domain::Project,
    source: &BlenderProductSourceSnapshot,
    attempt: &BlenderProductAttemptProjection,
    native: &serde_json::Value,
) -> EvidenceResult<()> {
    let original_revision = request
        .expected
        .revision
        .checked_add(1)
        .ok_or("product original revision overflow")?;
    let edited_revision = original_revision
        .checked_add(1)
        .ok_or("product edited revision overflow")?;
    let original_stamp = RevisionStamp {
        resource: request.expected.resource.clone(),
        generation: request.expected.generation,
        revision: original_revision,
    };
    let edited_stamp = RevisionStamp {
        resource: request.expected.resource.clone(),
        generation: request.expected.generation,
        revision: edited_revision,
    };
    let lineage = source
        .lineage
        .as_ref()
        .ok_or("completed product source has no durable lineage")?;
    let capsule_before = project_before
        .production_design
        .capsules
        .iter()
        .find(|capsule| capsule.id == request.capsule_id)
        .ok_or("product source was not present in the original Project")?;
    if project_before.id != request.project_id
        || project_before.stamp() != request.expected
        || capsule_before.scene_id != request.scene_id
        || capsule_before.source_asset_id != lineage.recipe.asset_id
        || capsule_before.source_sha256 != lineage.recipe.sha256
        || source.project.id != request.project_id
        || source.project.stamp() != edited_stamp
        || source.source_kind != "edited_native_source"
        || !source.source_cas_verified
        || lineage.original_request_id != request.request_id
        || lineage.expected_before != request.expected
        || lineage.original_attached != original_stamp
        || lineage.expected_after != edited_stamp
        || lineage.annotation.after != request.annotation_body
        || source.effective_recipe.annotation != request.annotation_body
        || source.original_recipe != lineage.recipe
        || source.current_source.asset_id != lineage.edited.asset_id
        || source.current_source.sha256 != lineage.edited.sha256
        || attempt.project != source.project
        || !attempt.current_source_matches
        || !attempt.source_cas_verified
        || attempt.effective_recipe.as_ref() != Some(&source.effective_recipe)
        || attempt.attempt.state != BlenderProductAttemptState::Completed
        || !attempt.attempt.native_outcome_known
        || attempt.attempt.original_request_id != request.request_id
        || attempt.attempt.expected_before != request.expected
        || serde_json::to_value(&attempt.lineage)? != serde_json::to_value(&source.lineage)?
        || native["schema"] != json!("motionwright.blender.product-cycle-caller/2")
        || native["project_id"] != json!(request.project_id)
        || native["scene_id"] != json!(request.scene_id)
        || native["capsule_id"] != json!(request.capsule_id)
        || native["expected_before"] != json!(request.expected)
        || native["expected_after"] != json!(edited_stamp)
        || native["recipe_asset_id"] != json!(lineage.recipe.asset_id)
        || native["recipe_sha256"] != json!(lineage.recipe.sha256)
        || native["original_source"]["sha256"] != json!(lineage.original.sha256)
        || native["original_source"]["bytes"] != json!(lineage.original.bytes)
        || native["edited_source"]["sha256"] != json!(lineage.edited.sha256)
        || native["edited_source"]["bytes"] != json!(lineage.edited.bytes)
    {
        return Err(
            "completed product terminal differs from its reopened CAS/SQLite lineage".into(),
        );
    }
    Ok(())
}

fn product_read_observation<T: serde::Serialize>(
    result: semwright_native_sdk::Result<T>,
) -> serde_json::Value {
    match result {
        Ok(value) => json!({"status": "ok", "value": value}),
        Err(error) => json!({"status": "error", "error": error}),
    }
}

fn capture_product_custody(
    database: &Path,
    request: &BlenderProductCycleRequest,
    project_before: &motionwright_domain::Project,
    completed: Option<&serde_json::Value>,
) -> EvidenceResult<serde_json::Value> {
    let reopened = StudioService::open(database)?;
    let project = reopened.project(request.project_id)?;
    let watermark = reopened.production_receipt_watermark(project.id)?;
    let scope = BlenderProductSourceScope {
        project_id: project.id,
        expected: project.stamp(),
        scene_id: request.scene_id,
        capsule_id: request.capsule_id,
    };
    let source = read_blender_product_source(&reopened, &scope);
    let attempt = read_blender_product_attempt(
        &reopened,
        &BlenderProductAttemptRequest {
            scope,
            original_request_id: request.request_id.clone(),
            receipt_id: None,
        },
    );
    if let Some(native) = completed {
        product_completed_readback(
            request,
            project_before,
            source
                .as_ref()
                .map_err(|error| format!("product source readback: {error}"))?,
            attempt
                .as_ref()
                .map_err(|error| format!("product attempt readback: {error}"))?,
            native,
        )?;
    }
    let receipts = complete_original_receipts(&reopened, project.id)?;
    if reopened.project(project.id)? != project
        || reopened.production_receipt_watermark(project.id)? != watermark
    {
        return Err("product custody changed during its read-only SQLite capture".into());
    }
    Ok(json!({
        "sqlite_reopened": true,
        "project": project,
        "production": receipts,
        "source_readback": product_read_observation(source),
        "original_attempt_readback": product_read_observation(attempt),
        "saved_source_and_lineage_verified": completed.is_some(),
        "native_dispatch_performed_by_readback": false,
        "automatic_retry": false
    }))
}

async fn product_cycle_phase(
    database: &Path,
    connections: [&Path; 3],
    attempt_path: &Path,
    evidence_path: &Path,
) -> EvidenceResult<serde_json::Value> {
    product_destination_absent(evidence_path)?;
    let request = read_product_request(attempt_path)?;
    let metadata = std::fs::symlink_metadata(database)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() {
        return Err("product cycle requires its existing regular SQLite source".into());
    }
    let service = StudioService::open(database)?;
    let project_before = service.project(request.project_id)?;
    let [create_path, edit_path, verify_path] = connections;
    let create_connection = ProductionConnection::load(create_path)?;
    let edit_connection = ProductionConnection::load(edit_path)?;
    let verify_connection = ProductionConnection::load(verify_path)?;
    // These coordinators use the three existing owner-created connections and
    // one shared StudioService. They do not construct a Host or recover authority.
    let create = ProductionCoordinator::new(service.clone(), create_connection)?;
    let edit = ProductionCoordinator::new(service.clone(), edit_connection)?;
    let verify = ProductionCoordinator::new(service.clone(), verify_connection)?;
    let mut journal = BlenderProductCycleJournal::default();
    let terminal = create
        .realize_blender_product_cycle(&edit, &verify, &request, &mut journal)
        .await;
    let append_custody = json!({
        "create": create.private_receipt_custody_snapshot(),
        "edit": edit.private_receipt_custody_snapshot(),
        "verify": verify.private_receipt_custody_snapshot()
    });
    drop((create, edit, verify, service));
    let completed = terminal.as_ref().ok().cloned();
    let evidence = json!({
        "schema": "motionwright.blender.product-cycle-evidence/1",
        "request": request,
        "expected_before": project_before.stamp(),
        "journal": journal,
        "source_acceptance_inherited": false,
        "execution_grant_inherited": false,
        "delivery_profile_selected": false,
        "full_1200_frame_render": false,
        "decoded_pixel_difference_verified": false,
        "visual_acceptance": false,
        "human_acceptance": false,
        "automatic_native_retry": false
    });
    // The shared publisher retains the whole original native Error before any
    // readback/publication failure and never repeats a native operation.
    finish_original_terminal(
        terminal,
        evidence,
        || capture_product_custody(database, &request, &project_before, completed.as_ref()),
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
        "recipe-product" => {
            let output = path_arg(&mut args);
            let namespace = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let curve = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let annotation = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            print_json(&emit_product_recipe(
                &output,
                &namespace,
                &curve,
                &annotation,
            )?)
        }
        "seed-product" => {
            let database = path_arg(&mut args);
            let recipe = path_arg(&mut args);
            let output = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            print_json(&seed_product(&database, &recipe, &output)?)
        }
        "product-cycle" => {
            let database = path_arg(&mut args);
            let create = path_arg(&mut args);
            let edit = path_arg(&mut args);
            let verify = path_arg(&mut args);
            let attempt = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            let result =
                product_cycle_phase(&database, [&create, &edit, &verify], &attempt, &evidence)
                    .await?;
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
        let mut expected_latest_id = None;
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
            expected_latest_id = Some(receipt.id);
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
            json!(expected_latest_id)
        );
        assert_eq!(captured["complete"], json!(true));
        let indices = captured["receipts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|receipt| receipt["payload"]["index"].as_u64().unwrap())
            .collect::<BTreeSet<_>>();
        assert_eq!(indices, (0..270).collect());
        assert_eq!(expected_ids.len(), 270);
        let ordered = captured["receipts"]
            .as_array()
            .unwrap()
            .iter()
            .map(|receipt| receipt["payload"]["index"].as_u64().unwrap())
            .collect::<Vec<_>>();
        assert_eq!(ordered, (0_u64..270).rev().collect::<Vec<_>>());

        // The service owns order. A caller must accept non-lexical UUIDs across
        // page boundaries and still reject duplicate/cyclic cursors.
        let mut opaque = reopened.production_receipts(project.id, 3).unwrap();
        opaque[0].id = Uuid::from_u128(8);
        opaque[1].id = Uuid::from_u128(1);
        opaque[2].id = Uuid::from_u128(7);
        let mut seen = BTreeSet::new();
        let mut before = None;
        observe_original_receipt_page(&opaque[..2], project.id, &mut seen, &mut before).unwrap();
        assert_eq!(before, Some(Uuid::from_u128(1)));
        observe_original_receipt_page(&opaque[2..], project.id, &mut seen, &mut before).unwrap();
        assert_eq!(before, Some(Uuid::from_u128(7)));
        assert_eq!(seen.len(), 3);
        assert!(
            observe_original_receipt_page(&opaque[..1], project.id, &mut seen, &mut before)
                .is_err()
        );
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

#[cfg(test)]
mod product_caller_tests {
    use super::*;
    use motionwright_native::blender_product_scene::{CameraCurve, ProductScene};

    struct Fixture {
        _root: tempfile::TempDir,
        database: PathBuf,
        recipe_path: PathBuf,
        output: PathBuf,
        original_bytes: Vec<u8>,
    }

    fn fixture() -> Fixture {
        let root = tempfile::tempdir().unwrap();
        let recipe = ProductScene::vessel("ProductCallerFixture", CameraCurve::BezierAutoClamped);
        let mut original_bytes = serde_json::to_vec_pretty(&recipe).unwrap();
        original_bytes.extend_from_slice(b"\n\n");
        let database = root.path().join("product.sqlite3");
        let recipe_path = root.path().join("original-recipe.json");
        let output = root.path().join("seed");
        std::fs::create_dir(&output).unwrap();
        std::fs::write(&recipe_path, &original_bytes).unwrap();
        Fixture {
            _root: root,
            database,
            recipe_path,
            output,
            original_bytes,
        }
    }

    #[test]
    fn product_seed_preserves_original_bytes_in_cas_after_sqlite_reopen() {
        let fixture = fixture();
        for curve in ["linear", "bezier_auto_clamped"] {
            let path = fixture
                .output
                .parent()
                .unwrap()
                .join(format!("{curve}.json"));
            let emitted =
                emit_product_recipe(&path, "SourceOnlyFixture", curve, "EDITED\nSURFACE").unwrap();
            let raw = std::fs::read(&path).unwrap();
            let decoded = decode_blender_product_recipe(&raw).unwrap();
            assert_eq!(emitted["source_constructor"], "ProductScene::vessel");
            assert_eq!(emitted["native_execution"], "NOT_RUN");
            assert_eq!(emitted["sqlite_opened"], false);
            assert_eq!(emitted["annotation_after"], "EDITED\nSURFACE");
            assert_eq!(
                emitted["product_scene"],
                serde_json::to_value(&decoded).unwrap()
            );
            assert_eq!(emitted["product_scene"]["camera_curve"], curve);
            assert!(!fixture.database.exists());
        }
        let refused = fixture.output.parent().unwrap().join("refused-recipe.json");
        assert!(emit_product_recipe(&refused, "Fixture", "native_default", "EDITED").is_err());
        assert!(!refused.exists());
        assert!(emit_product_recipe(&refused, "Fixture", "linear", "EDITABLE\nSURFACE").is_err());
        assert!(!refused.exists());
        let seeded =
            seed_product(&fixture.database, &fixture.recipe_path, &fixture.output).unwrap();
        assert_eq!(
            seeded["schema"],
            "motionwright.blender.product-scene-seed/1"
        );
        assert_eq!(seeded["native_execution"], "NOT_RUN");
        assert_eq!(seeded["recipe_cas_verified"], true);
        assert_eq!(seeded["sqlite_reopened"], true);
        assert_eq!(seeded["delivery_profile_selected"], false);
        assert_eq!(seeded["full_1200_frame_render"], false);
        assert_eq!(seeded["human_acceptance"], false);
        assert_eq!(
            std::fs::read(fixture.output.join("source-recipe.json")).unwrap(),
            fixture.original_bytes
        );
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(
                &std::fs::read(fixture.output.join("seed.json")).unwrap()
            )
            .unwrap(),
            seeded
        );
        let reopened = StudioService::open(&fixture.database).unwrap();
        let project_id: Uuid = serde_json::from_value(seeded["project_id"].clone()).unwrap();
        let project = reopened.project(project_id).unwrap();
        assert_eq!(project.scenes.len(), 1);
        assert!(project.scenes[0].nodes.is_empty());
        assert!(project.production_design.stages.is_empty());
        assert_eq!(project.production_design.capsules.len(), 1);
        assert_eq!(
            reopened
                .production_receipt_watermark(project_id)
                .unwrap()
                .count,
            0
        );
        let handle = reopened.store();
        let store = handle.lock();
        assert_eq!(
            store
                .read_blob(
                    seeded["source_sha256"].as_str().unwrap(),
                    PRODUCT_CALLER_RECIPE_MAX_BYTES
                )
                .unwrap(),
            fixture.original_bytes
        );
    }

    #[test]
    fn product_seed_rejects_unprojected_recipe_before_creating_canonical_source() {
        for (field, value) in [
            ("camera_curve", json!("native_default")),
            ("samples", json!(15)),
            ("execution_grant", json!({"allow": true})),
        ] {
            let fixture = fixture();
            let mut recipe: serde_json::Value =
                serde_json::from_slice(&fixture.original_bytes).unwrap();
            recipe[field] = value;
            std::fs::write(&fixture.recipe_path, serde_json::to_vec(&recipe).unwrap()).unwrap();
            assert!(
                seed_product(&fixture.database, &fixture.recipe_path, &fixture.output).is_err()
            );
            assert!(!fixture.database.exists());
            assert!(fixture.output.read_dir().unwrap().next().is_none());
        }
    }

    #[test]
    fn product_seed_does_not_reuse_an_existing_database_or_seed_destination() {
        let fixture = fixture();
        let original = b"Existing source must remain untouched";
        std::fs::write(&fixture.database, original).unwrap();
        assert!(seed_product(&fixture.database, &fixture.recipe_path, &fixture.output).is_err());
        assert_eq!(std::fs::read(&fixture.database).unwrap(), original);
        assert!(fixture.output.read_dir().unwrap().next().is_none());

        let second = self::fixture();
        let retained = second.output.join("already-retained.json");
        std::fs::write(&retained, original).unwrap();
        assert!(seed_product(&second.database, &second.recipe_path, &second.output).is_err());
        assert!(!second.database.exists());
        assert_eq!(std::fs::read(retained).unwrap(), original);
    }

    #[test]
    fn product_request_reader_is_closed_and_bounded_before_native_execution() {
        let fixture = fixture();
        let project = motionwright_domain::Project::new("Product request reader fixture").unwrap();
        let request = json!({
            "project_id": project.id,
            "expected": {
                "resource": project.resource_key(),
                "generation": project.generation,
                "revision": 7
            },
            "scene_id": Uuid::now_v7(),
            "capsule_id": Uuid::now_v7(),
            "request_id": "unit-reader",
            "annotation_body": "EDITED"
        });
        std::fs::write(&fixture.recipe_path, serde_json::to_vec(&request).unwrap()).unwrap();
        let parsed = read_product_request(&fixture.recipe_path).unwrap();
        assert_eq!(parsed.request_id, "unit-reader");
        assert_eq!(parsed.project_id, project.id);
        assert_eq!(parsed.expected.resource, project.resource_key());
        assert_eq!(parsed.expected.generation, project.generation);
        assert_eq!(parsed.expected.revision, 7);
        let mut missing_resource = request.clone();
        missing_resource["expected"]
            .as_object_mut()
            .unwrap()
            .remove("resource");
        std::fs::write(
            &fixture.recipe_path,
            serde_json::to_vec(&missing_resource).unwrap(),
        )
        .unwrap();
        assert!(read_product_request(&fixture.recipe_path).is_err());
        for invalid_resource in [json!(null), json!(true), json!(7), json!([]), json!({})] {
            let mut invalid = request.clone();
            invalid["expected"]["resource"] = invalid_resource;
            std::fs::write(&fixture.recipe_path, serde_json::to_vec(&invalid).unwrap()).unwrap();
            assert!(read_product_request(&fixture.recipe_path).is_err());
        }
        let mut extra = request;
        extra["retry"] = json!(true);
        std::fs::write(&fixture.recipe_path, serde_json::to_vec(&extra).unwrap()).unwrap();
        assert!(read_product_request(&fixture.recipe_path).is_err());
        std::fs::write(&fixture.recipe_path, vec![b' '; 16_385]).unwrap();
        assert!(read_product_request(&fixture.recipe_path).is_err());
        assert!(!fixture.database.exists());
        assert!(fixture.output.read_dir().unwrap().next().is_none());
    }

    #[cfg(unix)]
    #[test]
    fn product_seed_rejects_a_symlink_recipe_before_source_effects() {
        let fixture = fixture();
        let link = fixture.output.parent().unwrap().join("recipe-link.json");
        std::os::unix::fs::symlink(&fixture.recipe_path, &link).unwrap();
        assert!(seed_product(&fixture.database, &link, &fixture.output).is_err());
        assert!(!fixture.database.exists());
        assert!(fixture.output.read_dir().unwrap().next().is_none());
        assert_eq!(
            std::fs::read(&fixture.recipe_path).unwrap(),
            fixture.original_bytes
        );
    }

    #[cfg(unix)]
    #[test]
    fn product_input_replaced_by_fifo_is_rejected_without_a_writer_or_blocking_open() {
        use std::os::unix::{ffi::OsStrExt, fs::OpenOptionsExt};
        use std::sync::mpsc;
        use std::time::Duration;

        let fixture = fixture();
        let before = std::fs::symlink_metadata(&fixture.recipe_path).unwrap();
        assert!(before.is_file());
        let retired = fixture
            .recipe_path
            .with_file_name("retired-original-recipe.json");
        std::fs::rename(&fixture.recipe_path, &retired).unwrap();
        let path = fixture.recipe_path.clone();
        let c_path = std::ffi::CString::new(path.as_os_str().as_bytes()).unwrap();
        // SAFETY: this owned CString remains NUL-terminated and alive throughout
        // the call. The FIFO is created only inside this test's private TempDir.
        assert_eq!(
            unsafe { libc::mkfifo(c_path.as_ptr(), 0o600) },
            0,
            "{}",
            std::io::Error::last_os_error()
        );
        let (tx, rx) = mpsc::sync_channel(1);
        let reader_path = path.clone();
        let worker = std::thread::spawn(move || {
            // Exercise the same actual open/fstat as read_product_input, after
            // the original regular metadata has already been captured. Turning
            // the result into a String keeps only test data across the thread.
            let result = open_product_input_original(&reader_path, &before)
                .map(|_| ())
                .map_err(|error| error.to_string());
            let _ = tx.send(result);
        });
        let first = rx.recv_timeout(Duration::from_secs(5));
        if matches!(&first, Err(mpsc::RecvTimeoutError::Timeout)) {
            // Release a regressed blocking open before joining/failing. This
            // nonblocking private FIFO handle is neither a native dispatch nor
            // a writer used by the passing case.
            let unblock = OpenOptions::new()
                .read(true)
                .write(true)
                .custom_flags(libc::O_NONBLOCK | libc::O_NOFOLLOW)
                .open(&path);
            let recovered = match &unblock {
                Ok(_) => rx.recv_timeout(Duration::from_secs(5)),
                Err(_) => Err(mpsc::RecvTimeoutError::Timeout),
            };
            drop(unblock);
            if recovered.is_err() {
                // Do not turn a failed recovery into an unbounded join. The
                // failed test explicitly reports that this local waiter did not
                // finish; no application/provider process was ever launched.
                panic!("FIFO regression cleanup could not confirm the local reader terminal");
            }
        }
        worker.join().expect("product input reader thread panicked");
        let result = first.expect("product input open blocked on a FIFO without a writer");
        assert!(
            result.is_err(),
            "rebound FIFO was accepted as a regular input"
        );
        assert!(!fixture.database.exists());
        assert!(fixture.output.read_dir().unwrap().next().is_none());
        assert_eq!(std::fs::read(retired).unwrap(), fixture.original_bytes);
        std::fs::remove_file(path).unwrap();
    }
}

#[cfg(test)]
mod static_budget_tests {
    use super::*;
    use semwright_native_sdk::{Error, ErrorCode};

    #[test]
    fn static_budget_counts_the_terminal_newline_before_any_file_write() {
        let value = json!("a");
        assert_eq!(bounded_static_json(&value, false, 4).unwrap(), b"\"a\"\n");
        assert!(bounded_static_json(&value, false, 3).is_err());
        assert!(bounded_static_json(&value, false, 0).is_err());
    }

    #[test]
    fn static_budget_rejects_large_derived_diagnostic_without_replacing_native_error() {
        let root = tempfile::tempdir().unwrap();
        let evidence = root.path().join("motionwright-blender-evidence.json");
        let mut original = Error::new(ErrorCode::Timeout, "original native uncertainty");
        original.outcome_known = false;
        let expected = serde_json::to_value(&original).unwrap();
        let value = json!({"large_derived_diagnostic": "x".repeat(STATIC_PRIVATE_JSON_MAX_BYTES)});
        let observed = retain_static_failure(original, &value, &evidence);
        assert_eq!(serde_json::to_value(observed).unwrap(), expected);
        assert!(
            !root
                .path()
                .join("motionwright-blender-diagnostic.json")
                .exists()
        );
        let cause: serde_json::Value = serde_json::from_slice(
            &std::fs::read(
                root.path()
                    .join("motionwright-blender-diagnostic-custody.json"),
            )
            .unwrap(),
        )
        .unwrap();
        assert_eq!(cause["diagnostic_publication"], "INCOMPLETE");
        assert_eq!(cause["native_error_replaced"], false);
        assert_eq!(cause["diagnostic_is_derived"], true);
    }

    #[test]
    fn static_budget_existing_diagnostic_is_preserved_and_native_error_stays_primary() {
        let root = tempfile::tempdir().unwrap();
        let evidence = root.path().join("motionwright-blender-evidence.json");
        let diagnostic = root.path().join("motionwright-blender-diagnostic.json");
        let original_bytes = b"previous original diagnostic";
        std::fs::write(&diagnostic, original_bytes).unwrap();
        let original = Error::new(ErrorCode::Unsupported, "original native failure");
        let expected = serde_json::to_value(&original).unwrap();
        let observed = retain_static_failure(
            original,
            &json!({"diagnostic": "new derived view"}),
            &evidence,
        );
        assert_eq!(serde_json::to_value(observed).unwrap(), expected);
        assert_eq!(std::fs::read(diagnostic).unwrap(), original_bytes);
    }
}
