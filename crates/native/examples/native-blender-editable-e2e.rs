//! Isolated Collection editability exercise. Owner connections come from A6;
//! this binary never starts another Blender runtime or provisions policy grants.
use motionwright_domain::{
    BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle, RendererKind, RevisionStamp,
};
use motionwright_native::production::{
    ProductionConnection, ProductionCoordinator,
    blender_editable::{BlenderEditableEvidence, BlenderEditableTransformDelta},
    read_verified_production_artifact,
};
use motionwright_service::StudioService;
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeSet,
    fs::OpenOptions,
    io::Write,
    path::{Path, PathBuf},
};
use uuid::Uuid;

type RunResult<T> = Result<T, Box<dyn std::error::Error>>;

fn usage() -> ! {
    eprintln!(
        "usage: native-blender-editable-e2e seed DATABASE | create DATABASE CONNECTION OUTPUT_NAME | reopen|edit DATABASE CONNECTION INPUT_NAME INPUT_SHA256 OUTPUT_NAME"
    );
    std::process::exit(2);
}

fn shape(kind: &str, id: Uuid, name: &str, x: f64, rgba: &str) -> CanvasNode {
    CanvasNode {
        id,
        name: name.into(),
        kind: kind.into(),
        parent_id: None,
        x,
        y: 280.0,
        width: 240.0,
        height: 180.0,
        rotation_deg: 12.0,
        opacity: 1.0,
        text: None,
        coordinate_space: CoordinateSpace::ProjectPixels,
        z_index: 1,
        style: NodeStyle {
            fill: Some(rgba.into()),
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

fn seed(database: &Path) -> RunResult<()> {
    let service = StudioService::open(database)?;
    if !service.projects(2)?.is_empty() {
        return Err("Blender editable seed database must be empty".into());
    }
    let initial = service.create_project("Blender native editable Collection")?;
    let mut project = service
        .apply(
            initial.id,
            &RevisionStamp::from(&initial),
            "blender-editable-seed-scene",
            &Change::AddScene {
                name: "Bounded editable source".into(),
                objective: "Persist, reopen and edit native source".into(),
                duration_seconds: 2,
            },
        )?
        .project;
    let scene_id = project.scenes[0].id;
    project = service
        .apply(
            project.id,
            &RevisionStamp::from(&project),
            "blender-editable-seed-renderer",
            &Change::SetSceneRenderer {
                scene_id,
                renderer: RendererKind::Blender,
            },
        )?
        .project;
    for (index, node) in [
        shape(
            "rectangle",
            Uuid::parse_str("0198aabb-ccdd-7000-8000-000000000001")?,
            "Motionwright editable panel",
            360.0,
            "#4F7CAC",
        ),
        shape(
            "circle",
            Uuid::parse_str("0198aabb-ccdd-7000-8000-000000000002")?,
            "Motionwright editable focus",
            1040.0,
            "#F5B84A80",
        ),
    ]
    .into_iter()
    .enumerate()
    {
        project = service
            .apply(
                project.id,
                &RevisionStamp::from(&project),
                &format!("blender-editable-seed-node-{index}"),
                &Change::AddCanvasNode { scene_id, node },
            )?
            .project;
    }
    println!(
        "{}",
        serde_json::to_string(
            &json!({"project_id":project.id, "resource":project.resource_key(),
        "generation":project.generation, "revision":project.revision, "scene_id":scene_id, "profile":"static-collection-source-only"})
        )?
    );
    Ok(())
}

fn filename(value: &str) -> RunResult<&str> {
    if value.is_empty()
        || value.len() > 180
        || !value.ends_with(".json")
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
        || value.starts_with('.')
    {
        return Err(
            "Evidence must use one bounded JSON filename inside the connection output root".into(),
        );
    }
    Ok(value)
}

fn write_new(root: &Path, name: &str, value: &impl serde::Serialize) -> RunResult<String> {
    let path = root.join(filename(name)?);
    let mut body = serde_json::to_vec_pretty(value)?;
    body.push(b'\n');
    if body.len() > 16 * 1024 * 1024 {
        return Err("Private source evidence exceeds 16 MiB".into());
    }
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path)?;
    file.write_all(&body)?;
    file.sync_all()?;
    Ok(format!("{:x}", Sha256::digest(&body)))
}

async fn run_phase(
    phase: &str,
    database: &Path,
    connection_file: &Path,
    input: Option<(&str, &str)>,
    output_name: &str,
) -> RunResult<()> {
    let service = StudioService::open(database)?;
    let projects = service.projects(2)?;
    if projects.len() != 1 {
        return Err("Expected exactly one isolated seeded Project".into());
    }
    let project = &projects[0];
    if project.scenes.len() != 1 || project.scenes[0].renderer != RendererKind::Blender {
        return Err("Expected exactly one canonical Blender scene".into());
    }
    let connection = ProductionConnection::load(connection_file)?;
    if connection.resource != project.resource_key() {
        return Err("Blender connection is bound to another Project".into());
    }
    let root = connection.output_root.clone();
    let output_name = filename(output_name)?;
    // Fail before any native call if this phase has already produced its evidence file.
    if root.join(output_name).try_exists()? {
        return Err("Evidence destination already exists; reconcile the original phase".into());
    }
    let prior = if let Some((name, sha256)) = input {
        let bytes =
            read_verified_production_artifact(&root, filename(name)?, sha256, 16 * 1024 * 1024)?;
        Some(serde_json::from_slice::<BlenderEditableEvidence>(&bytes)?)
    } else {
        None
    };
    let coordinator = ProductionCoordinator::new(service.clone(), connection)?;
    let expected = RevisionStamp::from(project);
    let result = match phase {
        "create" => {
            coordinator
                .create_blender_editable_collection(
                    project.id,
                    &expected,
                    "native-blender-editable-create",
                    project.scenes[0].id,
                )
                .await
        }
        "reopen" => {
            let prior = prior
                .as_ref()
                .ok_or("Reopen requires original evidence and its SHA256")?;
            let prefix = if prior.source.native_edits.is_empty() {
                "native-blender-editable-reopen"
            } else {
                "native-blender-editable-reopen-edited"
            };
            coordinator
                .reopen_blender_editable_collection(&expected, prefix, prior)
                .await
        }
        "edit" => {
            let prior = prior
                .as_ref()
                .ok_or("Edit requires verified reopen evidence and its SHA256")?;
            let original = prior
                .source
                .canonical_contribution
                .objects
                .first()
                .ok_or("Editable source has no target")?;
            let entity = prior
                .source
                .spec
                .entities
                .iter()
                .find(|entity| entity.id == format!("node_{}", original.node_id.simple()))
                .ok_or("Logical edit target is missing")?;
            let mut transform = entity.transform.clone();
            transform.translation[0] += 0.625;
            transform.rotation[2] += 0.125;
            coordinator
                .edit_blender_reopened_transform(
                    &expected,
                    "native-blender-editable-transform",
                    prior,
                    BlenderEditableTransformDelta {
                        node_id: original.node_id,
                        transform,
                    },
                )
                .await
        }
        _ => usage(),
    };
    let evidence = match result {
        Ok(evidence) => evidence,
        Err(error) => {
            let diagnostic = json!({"scope":"private-original-attempt", "phase":phase,
                "project_id":project.id, "generation":project.generation, "revision":project.revision,
                "error":{"code":format!("{:?}",error.code),"message":&error.message,"outcome_known":error.outcome_known},
                "receipts":coordinator.receipts(project.id,256).unwrap_or_default()});
            let diagnostic_name =
                format!("{}-diagnostic.json", output_name.trim_end_matches(".json"));
            let _ = write_new(&root, &diagnostic_name, &diagnostic);
            return Err(error.into());
        }
    };
    let after = service.project(project.id)?;
    if serde_json::to_value(&after)? != serde_json::to_value(project)? {
        return Err(
            "Native editability exercise unexpectedly changed canonical Project state".into(),
        );
    }
    let evidence_sha256 = write_new(&root, output_name, &evidence)?;
    println!(
        "{}",
        serde_json::to_string(&json!({
            "profile":"static-collection-source-only", "phase":evidence.phase,
            "evidence_name":output_name,"evidence_sha256":evidence_sha256,
            "artifact_name":evidence.artifact.relative_path,"artifact_sha256":evidence.artifact.sha256,
            "source_parameter_readback":evidence.source_parameter_readback,
            "fresh_process_reopened":evidence.fresh_process_reopened,
            "native_transform_edit_persisted":evidence.native_transform_edit_persisted,
            "independent_file_readback_verified":false,"raster_verified":false,"audio_verified":false,
            "full_scene_verified":false,"canonical_edit_imported":false
        }))?
    );
    Ok(())
}

#[tokio::main]
async fn main() -> RunResult<()> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.as_slice() {
        [phase, database] if phase == "seed" => seed(&PathBuf::from(database)),
        [phase, database, connection, output] if phase == "create" => {
            run_phase(
                phase,
                Path::new(database),
                Path::new(connection),
                None,
                output,
            )
            .await
        }
        [phase, database, connection, input, sha256, output]
            if phase == "reopen" || phase == "edit" =>
        {
            run_phase(
                phase,
                Path::new(database),
                Path::new(connection),
                Some((input, sha256)),
                output,
            )
            .await
        }
        _ => usage(),
    }
}
