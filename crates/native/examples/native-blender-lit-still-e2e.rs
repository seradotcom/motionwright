//! One-attempt producer for the existing canonical Blender lit-still adapter.
//! Four independent Host contexts are provisioned by the existing Python helper;
//! this caller does not start Blender, grant Broker authority or retry effects.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::production::{
    BlenderLitStillJournal, BlenderLitStillRequest, ProductionConnection, ProductionCoordinator,
    build_blender_lit_still_plan,
};
use motionwright_service::StudioService;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::OpenOptions,
    io::{Read, Write},
    path::{Path, PathBuf},
};
use uuid::Uuid;
type EvidenceResult<T> = Result<T, Box<dyn std::error::Error>>;
const CONTRACT_SHA256: &str = "3f2dbbed43ea58b3c37087e2e758888395083e4de175eec07507d8870355a495";

fn node(kind: &str, name: &str, bounds: [f64; 4], color: &str, z: i32) -> CanvasNode {
    CanvasNode {
        id: Uuid::now_v7(),
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x: bounds[0],
        y: bounds[1],
        width: bounds[2],
        height: bounds[3],
        rotation_deg: 0.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: z,
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

fn seed(database: &Path, output: &Path) -> EvidenceResult<Value> {
    if database.exists() || !output.is_dir() || output.read_dir()?.next().is_some() {
        return Err("Lit-still seed requires a new database and an empty private output".into());
    }
    // A killed seed is retained as an attempted seed, never silently repeated.
    write_new_json(
        &output.join("seed-intent.json"),
        &json!({
            "schema":"motionwright.blender.lit-still-seed-intent/1",
            "fixture_contract_sha256":CONTRACT_SHA256, "native_execution":false
        }),
    )?;
    let service = StudioService::open(database)?;
    if !service.projects(1)?.is_empty() {
        return Err("Lit-still database unexpectedly contains a project".into());
    }
    let mut project = service.create_project("Blender lit-still native fixture")?;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "lit-add-scene",
            &Change::AddScene {
                name: "Native lit cards".into(),
                objective: "Measure the predeclared editable Blender still fixture".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = project.scenes[0].id;
    project = service
        .apply(
            project.id,
            &project.stamp(),
            "lit-set-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            },
        )?
        .project;
    let specs = [
        (
            "edited_gold_card",
            node(
                "rectangle",
                "Lit gold card",
                [300., 200., 400., 300.],
                "#F5B84A",
                1,
            ),
        ),
        (
            "blue_anchor",
            node(
                "rectangle",
                "Lit blue anchor",
                [1000., 400., 240., 240.],
                "#527DCC",
                2,
            ),
        ),
        (
            "rose_disk",
            node(
                "circle",
                "Lit rose disk",
                [1300., 250., 180., 180.],
                "#D75B84",
                3,
            ),
        ),
    ];
    let mut roles = serde_json::Map::new();
    for (index, (role, node)) in specs.into_iter().enumerate() {
        roles.insert(role.into(), json!(node.id));
        project = service
            .apply(
                project.id,
                &project.stamp(),
                &format!("lit-add-node-{index}"),
                &Change::AddCanvasNode { scene_id, node },
            )?
            .project;
    }
    let source = serde_json::to_vec(&project)?;
    drop(service);
    let reopened = StudioService::open(database)?;
    let persisted = reopened.project(project.id)?;
    if serde_json::to_vec(&persisted)? != source {
        return Err("Lit-still seed differs after real SQLite reopen".into());
    }
    let plan = build_blender_lit_still_plan(&persisted, scene_id)?;
    if plan.source.camera.resolution != [1920, 1080]
        || plan.source.contribution.objects.len() != 3
        || plan.source.project_json_sha256 != format!("{:x}", Sha256::digest(&source))
    {
        return Err("Lit-still seeded source differs from the predeclared profile".into());
    }
    write_new_bytes(&output.join("source-project.json"), &source)?;
    write_new_json(
        &output.join("source-plan.json"),
        &serde_json::to_value(&plan)?,
    )?;
    let seeded = json!({
        "schema":"motionwright.blender.lit-still-seed/1",
        "fixture_kind":"first_party_synthetic", "fixture_contract_sha256":CONTRACT_SHA256,
        "project_id":persisted.id, "resource":persisted.resource_key(),
        "generation":persisted.generation,"revision":persisted.revision,
        "expected":persisted.stamp(),"scene_id":scene_id,"role_node_ids":roles,
        "project_json":{"path":"source-project.json","bytes":source.len(),
            "sha256":format!("{:x}",Sha256::digest(&source))},
        "plan_path":"source-plan.json","sqlite_reopened":true,
        "native_execution":"NOT_RUN","human_acceptance":false
    });
    write_new_json(&output.join("seed.json"), &seeded)?;
    Ok(seeded)
}

fn read_request(path: &Path) -> EvidenceResult<BlenderLitStillRequest> {
    let before = std::fs::symlink_metadata(path)?;
    if !before.is_file() || before.file_type().is_symlink() || before.len() > 16_384 {
        return Err("Lit-still request must be a bounded regular file".into());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if before.file_attributes() & 0x400 != 0 {
            return Err("Lit-still request cannot be a reparse point".into());
        }
    }
    let mut body = Vec::new();
    std::fs::File::open(path)?
        .take(16_385)
        .read_to_end(&mut body)?;
    if body.len() > 16_384 {
        return Err("Lit-still request exceeds its closed bound".into());
    }
    Ok(serde_json::from_slice(&body)?)
}

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

fn observe_receipt_page(
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

fn complete_receipts(
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
        observe_receipt_page(&page, project_id, &mut seen, &mut before)?;
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

fn capture_custody(
    database: &Path,
    project_before: &motionwright_domain::Project,
) -> EvidenceResult<serde_json::Value> {
    let reopened = StudioService::open(database)?;
    let project = reopened.project(project_before.id)?;
    if serde_json::to_vec(&project)? != serde_json::to_vec(project_before)? {
        return Err("original Blender native phase changed its canonical Project source".into());
    }
    let receipts = complete_receipts(&reopened, project.id)?;
    Ok(json!({
        "sqlite_reopened": true, "project_unchanged": true,
        "project": project, "production": receipts
    }))
}

fn finish_terminal<C, P, W>(
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

async fn phase(
    database: &Path,
    connection_path: &Path,
    attempt: &Path,
    evidence_path: &Path,
) -> EvidenceResult<Value> {
    if evidence_path.try_exists()? {
        return Err("Lit-still phase evidence already exists".into());
    }
    let request = read_request(attempt)?;
    let latch = evidence_path.with_extension("attempted.json");
    write_new_json(
        &latch,
        &json!({
            "schema":"motionwright.blender.lit-still-attempt/1",
            "request":request,"state":"ATTEMPTED_NO_TERMINAL_ASSUMED",
            "unknown_replay":false
        }),
    )?;
    let service = StudioService::open(database)?;
    let project = service.project(request.project_id)?;
    let connection = ProductionConnection::load(connection_path)?;
    let coordinator = ProductionCoordinator::new(service, connection)?;
    let mut journal = BlenderLitStillJournal::default();
    let terminal = coordinator
        .realize_blender_lit_still(&request, &mut journal)
        .await;
    let append_custody = coordinator.private_receipt_custody_snapshot();
    drop(coordinator);
    let evidence = json!({
        "schema":"motionwright.blender.lit-still-phase-evidence/1",
        "fixture_contract_sha256":CONTRACT_SHA256,
        "request":request,"journal":journal,
        "coordinator_append_custody":append_custody,
        "decoded_pixel_verification":false,"human_acceptance":false
    });
    let acknowledged = finish_terminal(
        terminal,
        evidence,
        || capture_custody(database, &project),
        |value| write_new_json(evidence_path, value),
        &mut std::io::stderr().lock(),
    )?;
    // CLI stdout is closed metadata. Journals and the complete native terminal
    // stay in the exact private evidence file; no recursive owner export.
    let bytes = std::fs::read(evidence_path)?;
    Ok(json!({
        "schema":"motionwright.blender.lit-still-phase-ack/1",
        "phase":request.phase,
        "phase_contract_verified":acknowledged["phase_contract_verified"],
        "evidence":{"bytes":bytes.len(),"sha256":format!("{:x}",Sha256::digest(&bytes))},
        "decoded_pixel_verification":false,"formal_acceptance":false
    }))
}

fn usage() -> ! {
    eprintln!(
        "usage: native-blender-lit-still-e2e <seed DATABASE OUTPUT | phase DATABASE CONNECTION ATTEMPT EVIDENCE>"
    );
    std::process::exit(2)
}
fn path_arg(args: &mut impl Iterator<Item = std::ffi::OsString>) -> PathBuf {
    args.next().map(PathBuf::from).unwrap_or_else(|| usage())
}
#[tokio::main(flavor = "current_thread")]
async fn main() -> EvidenceResult<()> {
    let mut args = std::env::args_os().skip(1);
    let command = args
        .next()
        .and_then(|v| v.into_string().ok())
        .unwrap_or_else(|| usage());
    let result = match command.as_str() {
        "seed" => {
            let db = path_arg(&mut args);
            let output = path_arg(&mut args);
            if args.next().is_some() {
                usage()
            }
            seed(&db, &output)?
        }
        "phase" => {
            let db = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let attempt = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage()
            }
            phase(&db, &connection, &attempt, &evidence).await?
        }
        _ => usage(),
    };
    serde_json::to_writer(std::io::stdout().lock(), &result)?;
    println!();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use semwright_native_sdk::{Error, ErrorCode};
    use std::cell::RefCell;

    #[test]
    fn fixture_has_exact_roles_source_geometry_and_reopened_project_bytes() {
        let tmp = tempfile::tempdir().unwrap();
        let output = tmp.path().join("seed");
        std::fs::create_dir(&output).unwrap();
        let db = tmp.path().join("project.sqlite3");
        let result = seed(&db, &output).unwrap();
        let bytes = std::fs::read(output.join("source-project.json")).unwrap();
        let project: motionwright_domain::Project = serde_json::from_slice(&bytes).unwrap();
        let actual = StudioService::open(&db)
            .unwrap()
            .project(project.id)
            .unwrap();
        assert_eq!(bytes, serde_json::to_vec(&actual).unwrap());
        assert_eq!(result["fixture_contract_sha256"], json!(CONTRACT_SHA256));
        let nodes = &actual.scenes[0].nodes;
        assert_eq!(nodes.len(), 3);
        for (role, kind, bounds, fill, z) in [
            (
                "edited_gold_card",
                "rectangle",
                [300., 200., 400., 300.],
                "#F5B84A",
                1,
            ),
            (
                "blue_anchor",
                "rectangle",
                [1000., 400., 240., 240.],
                "#527DCC",
                2,
            ),
            (
                "rose_disk",
                "circle",
                [1300., 250., 180., 180.],
                "#D75B84",
                3,
            ),
        ] {
            let id: Uuid = serde_json::from_value(result["role_node_ids"][role].clone()).unwrap();
            let n = nodes.iter().find(|n| n.id == id).unwrap();
            assert_eq!(n.kind, kind);
            assert_eq!([n.x, n.y, n.width, n.height], bounds);
            assert_eq!(n.style.fill.as_deref(), Some(fill));
            assert_eq!(n.z_index, z);
            assert_eq!(n.style.blend_mode, BlendMode::Normal);
            assert_eq!(n.opacity, 1.0);
            assert!(n.keyframes.is_empty());
        }
        assert_eq!(result["native_execution"], json!("NOT_RUN"));
    }

    #[test]
    fn interrupted_seed_latch_and_existing_database_are_never_replaced() {
        let tmp = tempfile::tempdir().unwrap();
        let output = tmp.path().join("seed");
        std::fs::create_dir(&output).unwrap();
        let db = tmp.path().join("project.sqlite3");
        let first = seed(&db, &output).unwrap();
        let original = std::fs::read(output.join("source-project.json")).unwrap();
        assert!(seed(&db, &output).is_err());
        assert_eq!(
            original,
            std::fs::read(output.join("source-project.json")).unwrap()
        );
        assert_eq!(
            first["project_json"]["sha256"],
            json!(format!("{:x}", Sha256::digest(&original)))
        );
    }

    #[test]
    fn unknown_native_terminal_is_preserved_when_later_custody_fails() {
        let saved = RefCell::new(None);
        let error =
            Error::new(ErrorCode::BackendFailed, "synthetic unknown after effect").uncertain();
        let expected = serde_json::to_value(&error).unwrap();
        let mut diagnostic = Vec::new();
        let returned = finish_terminal(
            Err(error),
            json!({"fixture_only":true}),
            || Err("synthetic reopen failure".into()),
            |e| {
                saved.replace(Some(e.clone()));
                Ok(())
            },
            &mut diagnostic,
        )
        .unwrap_err();
        assert_eq!(
            serde_json::to_value(returned.downcast_ref::<Error>().unwrap()).unwrap(),
            expected
        );
        let record = saved.into_inner().unwrap();
        assert_eq!(record["native_terminal"]["error"], expected);
        assert_eq!(record["phase_contract_verified"], json!(false));
    }

    #[test]
    fn successful_native_terminal_does_not_hide_a_failed_private_publication() {
        let mut diagnostic = Vec::new();
        let result = finish_terminal(
            Ok(json!({"actual_terminal":"synthetic"})),
            json!({}),
            || Ok(json!({"synthetic_reopen":true})),
            |_| Err("synthetic publication failure".into()),
            &mut diagnostic,
        );
        assert!(result.is_err());
        let record: Value = serde_json::from_slice(&diagnostic).unwrap();
        assert_eq!(record["native_terminal"]["status"], json!("ok"));
        assert_eq!(record["phase_contract_verified"], json!(false));
    }

    #[test]
    fn exclusive_phase_marker_blocks_replay_before_another_native_call() {
        let tmp = tempfile::tempdir().unwrap();
        let p = tmp.path().join("phase.attempted.json");
        write_new_json(&p, &json!({"state":"ATTEMPTED_NO_TERMINAL_ASSUMED"})).unwrap();
        let original = std::fs::read(&p).unwrap();
        assert!(write_new_json(&p, &json!({"state":"RETRY"})).is_err());
        assert_eq!(std::fs::read(&p).unwrap(), original);
    }
}
