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
        "usage: native-manim-e2e <seed DATABASE | realize DATABASE CONNECTION EVIDENCE_JSON | seed-source DATABASE | seed-source-edge25 DATABASE | export-source DATABASE BUNDLE | import-source DATABASE BUNDLE | edit-source DATABASE | realize-source DATABASE CONNECTION EVIDENCE_JSON PHASE>"
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

fn source_asset(
    project: &motionwright_domain::Project,
) -> Result<(Uuid, String), Box<dyn std::error::Error>> {
    let scene = project
        .scenes
        .first()
        .ok_or("source fixture has no scene")?;
    let capsules = project
        .production_design
        .capsules
        .iter()
        .filter(|c| c.scene_id == scene.id)
        .collect::<Vec<_>>();
    if capsules.len() != 1 {
        return Err("source fixture needs exactly one scene capsule".into());
    }
    let capsule = capsules[0];
    let asset = project
        .assets
        .iter()
        .find(|a| a.id == capsule.source_asset_id)
        .ok_or("source asset is absent")?;
    if asset.media_type != motionwright_manim_profile::SOURCE_BUNDLE_MIME_V2
        || asset.content_sha256.as_deref() != Some(capsule.source_sha256.as_str())
    {
        return Err("source capsule lost its typed CAS binding".into());
    }
    Ok((asset.id, capsule.source_sha256.clone()))
}

fn write_source_file(path: &Path, bytes: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
    if !path.parent().is_some_and(Path::is_dir) {
        return Err("source destination parent must already exist".into());
    }
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn source_project(
    service: &StudioService,
) -> Result<motionwright_domain::Project, Box<dyn std::error::Error>> {
    let mut projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("source fixture expects exactly one Project".into());
    }
    Ok(projects.remove(0))
}

fn attach_source_fixture(
    service: &StudioService,
    project: &motionwright_domain::Project,
    path: &Path,
    bytes: &[u8],
    prior_capsule: Option<&motionwright_domain::NativeCapsule>,
) -> Result<(motionwright_domain::Project, Uuid), Box<dyn std::error::Error>> {
    use motionwright_domain::{Asset, Fidelity, FidelityReport, NativeCapsule};
    write_source_file(path, bytes)?;
    let blob = service.ingest_blob_file(path)?;
    let asset_id = Uuid::now_v7();
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(project),
            &format!("native-manim-source-asset-{}", project.revision),
            &Change::AddAsset {
                asset: Asset {
                    id: asset_id,
                    name: path
                        .file_name()
                        .ok_or("source filename missing")?
                        .to_string_lossy()
                        .into_owned(),
                    media_type: motionwright_manim_profile::SOURCE_BUNDLE_MIME_V2.into(),
                    content_sha256: Some(blob.sha256.clone()),
                    source_revision: Some(project.revision.to_string()),
                },
            },
        )?
        .project;
    if prior_capsule.is_some() {
        return Ok((project, asset_id));
    }
    let source = motionwright_manim_profile::inspect_source_bundle_v2(bytes)?;
    let capsule = NativeCapsule {
        id: Uuid::now_v7(),
        scene_id: source.plan.binding.scene_id,
        source_asset_id: asset_id,
        source_sha256: blob.sha256,
        label: "Manim static editable source".into(),
        fidelity: FidelityReport {
            renderer: "manim-community".into(),
            renderer_version: "0.21.0".into(),
            visual: Fidelity::Unavailable,
            temporal: Fidelity::Unavailable,
            structural: Fidelity::Translated,
            editable: Fidelity::Translated,
            losses: vec!["Native source and decoded media require independent verification".into()],
            evidence_sha256: None,
        },
        editable_parameters: vec!["node.position".into(), "node.size".into()],
        native_editor_hint:
            "Edit the typed source plan; regenerate with the trusted Manim source compiler".into(),
        property_fidelity: vec![],
    };
    let project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "native-manim-source-capsule",
            &Change::UpsertNativeCapsule { capsule },
        )?
        .project;
    Ok((project, asset_id))
}

fn seed_source(database: &Path, edge_25: bool) -> Result<(), Box<dyn std::error::Error>> {
    use motionwright_domain::RationalTime;
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Manim source seed needs a new empty database".into());
    }
    let mut project = service.create_project("Native Manim editable static source")?;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "source-add-scene",
            &Change::AddScene {
                name: "Asymmetric colored native shapes".into(),
                objective: "Verify all frames and a source edit across fresh processes".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = project.scenes[0].id;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "source-select-manim",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::ManimCommunity,
            },
        )?
        .project;
    let mut profile = project.deliverables[0].clone();
    profile.width = 640;
    profile.height = 360;
    profile.frame_rate = RationalTime::new(if edge_25 { 25 } else { 12 }, 1)?;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "source-deliverable",
            &Change::UpsertDeliverable { profile },
        )?
        .project;
    if edge_25 {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                "source-edge-clock",
                &Change::SetSceneDuration {
                    scene_id,
                    duration: RationalTime::new(29, 25)?,
                },
            )?
            .project;
    }
    let mut language = project.visual_language.clone();
    language
        .palette
        .iter_mut()
        .find(|t| t.name == "surface")
        .ok_or("fixture has no surface token")?
        .value = "#000000".into();
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "source-black-matte",
            &Change::SetVisualLanguage {
                visual_language: language,
            },
        )?
        .project;
    let fixtures = [
        (
            "10000000-0000-4000-8000-000000000001",
            "circle",
            (750.0, 390.0),
            (240.0, 240.0),
            "#20CC40",
            20,
        ),
        (
            "10000000-0000-4000-8000-000000000002",
            "rectangle",
            (240.0, 180.0),
            (600.0, 360.0),
            "#E02040",
            10,
        ),
        (
            "10000000-0000-4000-8000-000000000003",
            "rectangle",
            (1380.0, 120.0),
            (300.0, 150.0),
            "#2040E0",
            0,
        ),
    ];
    for (index, (id, kind, position, size, fill, z)) in fixtures.into_iter().enumerate() {
        let mut shape = node(NodeFixture {
            kind,
            name: "Original static source shape",
            text: None,
            position,
            size,
            fill,
        });
        shape.id = Uuid::parse_str(id)?;
        shape.z_index = z;
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("source-shape-{index}"),
                &Change::AddCanvasNode {
                    scene_id,
                    node: shape,
                },
            )?
            .project;
    }
    let plan = motionwright_native::multi_renderer::build_manim_plan_v2(
        &project,
        scene_id,
        project.deliverables[0].id,
    )?;
    let bytes = motionwright_manim_profile::source_bundle_v2(&plan)?;
    let path = database
        .parent()
        .ok_or("database has no parent")?
        .join("native-manim-original.mwmsrc.zip");
    let (project, asset_id) = attach_source_fixture(&service, &project, &path, &bytes, None)?;
    println!(
        "{}",
        json!({"project_id":project.id,"resource":project.resource_key(),"generation":project.generation,
        "revision":project.revision,"scene_id":scene_id,"deliverable_id":project.deliverables[0].id,
        "source_asset_id":asset_id,"frame_count":plan.clock.frame_count,"native_execution_performed":false})
    );
    Ok(())
}

fn export_source(database: &Path, destination: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let project = source_project(&service)?;
    let (asset_id, _) = source_asset(&project)?;
    let source = motionwright_native::production::inspect_saved_manim_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        asset_id,
    )?;
    let manifest = service.export_project_bundle(project.id, destination)?;
    println!(
        "{}",
        json!({"project_id":project.id,"source":source,"bundle":manifest,"native_execution_performed":false})
    );
    Ok(())
}

fn import_source(database: &Path, source: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Manim source import needs an empty destination database".into());
    }
    let project = service.import_project_bundle(source)?;
    let (asset_id, _) = source_asset(&project)?;
    let inspection = motionwright_native::production::inspect_saved_manim_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        asset_id,
    )?;
    if inspection.origin_binding.generation == project.generation
        || !service.production_receipts(project.id, 32)?.is_empty()
    {
        return Err("Imported source did not rotate context or retained native authority".into());
    }
    println!(
        "{}",
        json!({"project_id":project.id,"resource":project.resource_key(),"generation":project.generation,
        "revision":project.revision,"source":inspection,"native_execution_performed":false})
    );
    Ok(())
}

fn edit_source(database: &Path) -> Result<(), Box<dyn std::error::Error>> {
    let service = StudioService::open(database)?;
    let project = source_project(&service)?;
    let (origin_id, digest) = source_asset(&project)?;
    motionwright_native::production::inspect_saved_manim_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        origin_id,
    )?;
    let bytes = service.store().lock().read_blob(
        &digest,
        motionwright_manim_profile::MAX_SOURCE_BUNDLE_BYTES_V2 as u64,
    )?;
    let mut source = motionwright_manim_profile::inspect_source_bundle_v2(&bytes)?;
    let red = Uuid::parse_str("10000000-0000-4000-8000-000000000002")?;
    let item = source
        .plan
        .nodes
        .iter_mut()
        .find(|n| n.node_id == red)
        .ok_or("red source identity is absent")?;
    if item.x_px != 240.0 {
        return Err("The original source edit has already occurred or the fixture changed".into());
    }
    item.x_px = 330.0;
    let edited = motionwright_manim_profile::source_bundle_v2(&source.plan)?;
    let capsule = project
        .production_design
        .capsules
        .iter()
        .find(|c| c.source_asset_id == origin_id)
        .ok_or("origin capsule is absent")?
        .clone();
    let path = database
        .parent()
        .ok_or("database has no parent")?
        .join("native-manim-edited.mwmsrc.zip");
    let (project, edited_id) =
        attach_source_fixture(&service, &project, &path, &edited, Some(&capsule))?;
    let preview = motionwright_native::production::preview_saved_manim_source_transform(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        origin_id,
        edited_id,
    )?;
    let imported = motionwright_native::production::apply_saved_manim_source_transform(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        "native-manim-import-original-source-transform",
        origin_id,
        edited_id,
    )?;
    let mut capsule = capsule;
    capsule.source_asset_id = edited_id;
    capsule.source_sha256 = imported
        .assets
        .iter()
        .find(|a| a.id == edited_id)
        .and_then(|a| a.content_sha256.clone())
        .ok_or("edited source digest absent")?;
    let current = service
        .apply(
            imported.id,
            &RevisionStamp::from(&imported),
            "native-manim-associate-edited-source",
            &Change::UpsertNativeCapsule { capsule },
        )?
        .project;
    let rebound = motionwright_native::production::inspect_saved_manim_source(
        &service,
        current.id,
        &RevisionStamp::from(&current),
        edited_id,
    )?;
    println!(
        "{}",
        json!({"project_id":current.id,"generation":current.generation,"revision":current.revision,
        "source_import_preview":preview,"source":rebound,"canonical_transform_import_applied":true,"native_execution_performed":false})
    );
    Ok(())
}

async fn realize_source(
    database: &Path,
    connection_path: &Path,
    evidence_path: &Path,
    phase: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let edit = matches!(phase, "edit" | "edited-reopen");
    if !matches!(
        phase,
        "original" | "reopen" | "edit" | "edited-reopen" | "edge25"
    ) {
        return Err("source phase is not a closed supported fixture case".into());
    }
    let service = StudioService::open(database)?;
    let project = source_project(&service)?;
    let (asset_id, _) = source_asset(&project)?;
    let scene = project.scenes.first().ok_or("source scene missing")?;
    let red = Uuid::parse_str("10000000-0000-4000-8000-000000000002")?;
    if scene
        .nodes
        .iter()
        .find(|n| n.id == red)
        .is_none_or(|n| n.x != if edit { 330.0 } else { 240.0 })
    {
        return Err(
            "canonical source geometry does not match the explicitly selected verification phase"
                .into(),
        );
    }
    let inspection = motionwright_native::production::inspect_saved_manim_source(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        asset_id,
    )?;
    if matches!(phase, "reopen" | "edited-reopen")
        && inspection.origin_binding.generation == project.generation
    {
        return Err("native reopen phase must use the imported Project generation".into());
    }
    let plan = motionwright_native::multi_renderer::build_manim_plan_v2(
        &project,
        scene.id,
        project.deliverables[0].id,
    )?;
    if (phase == "edge25") != (plan.clock.frame_count == 29 && plan.render_profile.frame_rate == 25)
    {
        return Err("source clock does not match the closed verification phase".into());
    }
    let coordinator =
        ProductionCoordinator::new(service, ProductionConnection::load(connection_path)?)?;
    let request = format!("native-manim-source-v2-{phase}");
    let selection = motionwright_native::production::ManimSourceSelectionV2 {
        scene_id: scene.id,
        deliverable_id: project.deliverables[0].id,
        source_asset_id: Some(asset_id),
    };
    let result = match coordinator
        .realize_manim_scene_v2(
            project.id,
            &RevisionStamp::from(&project),
            &request,
            &selection,
        )
        .await
    {
        Ok(result) => result,
        Err(error) => {
            let diagnostic = json!({"schema":"motionwright.manim.source-attempt/2","phase":phase,
                "project":project,"request_id":request,"error":{"code":format!("{:?}",error.code),
                "message":&error.message,"outcome_known":error.outcome_known},
                "original_receipts":coordinator.receipts(project.id,256)?,
                "native_job_retry_performed":false});
            write_source_file(
                &evidence_path.with_extension("diagnostic.json"),
                &serde_json::to_vec_pretty(&diagnostic)?,
            )?;
            return Err(error.into());
        }
    };
    let evidence = json!({"schema":"motionwright.manim.native-source-producer/2","phase":phase,
        "project":project,"request_id":request,"source_origin":inspection,"result":result,
        "native_source_getters_verified":true,"decoded_media_fidelity_verified":false,
        "human_acceptance":false});
    write_source_file(evidence_path, &serde_json::to_vec_pretty(&evidence)?)?;
    println!(
        "{}",
        json!({"phase":phase,"project_id":project.id,"source_job_completed":true,
        "evidence_path":evidence_path,"decoded_media_fidelity_verified":false})
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
        "realize" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            realize(&database, &connection, &evidence).await
        }
        "seed-source" | "seed-source-edge25" => {
            let database = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            seed_source(&database, command == "seed-source-edge25")
        }
        "export-source" => {
            let database = path_arg(&mut args);
            let destination = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            export_source(&database, &destination)
        }
        "import-source" => {
            let database = path_arg(&mut args);
            let source = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            import_source(&database, &source)
        }
        "edit-source" => {
            let database = path_arg(&mut args);
            if args.next().is_some() {
                usage();
            }
            edit_source(&database)
        }
        "realize-source" => {
            let database = path_arg(&mut args);
            let connection = path_arg(&mut args);
            let evidence = path_arg(&mut args);
            let phase = args
                .next()
                .and_then(|s| s.into_string().ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            realize_source(&database, &connection, &evidence, &phase).await
        }
        _ => usage(),
    }
}
