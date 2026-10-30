use motionwright_domain::{Change, NativeCapsuleSourceSpec, Project, RevisionStamp};
use motionwright_service::StudioService;
use serde_json::json;
use std::path::PathBuf;
use uuid::Uuid;

fn emit(project: &Project) {
    println!(
        "{}",
        json!({
            "id": project.id,
            "resource": project.resource_key(),
            "generation": project.generation,
            "revision": project.revision.to_string(),
            "title": project.title,
        })
    );
}

fn usage() -> ! {
    eprintln!(
        "usage: shared-state-actor <database> <init TITLE | show PROJECT_ID | rename PROJECT_ID REQUEST_ID TITLE | capsule-seed TITLE SOURCE_PATH | capsule-show PROJECT_ID CAPSULE_ID | capsule-import PROJECT_ID GENERATION REVISION CAPSULE_ID SCENE_ID REQUEST_ID SOURCE_PATH>"
    );
    std::process::exit(2);
}

fn argument(args: &mut impl Iterator<Item = std::ffi::OsString>) -> String {
    args.next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage())
}

fn canonical_uuid(raw: &str) -> Uuid {
    Uuid::parse_str(raw)
        .ok()
        .filter(|id| id.to_string() == raw && !id.is_nil())
        .unwrap_or_else(|| usage())
}

fn capsule_spec(capsule_id: Uuid, scene_id: Uuid) -> NativeCapsuleSourceSpec {
    NativeCapsuleSourceSpec {
        name: "Original GLB source".into(),
        media_type: "model/gltf-binary".into(),
        capsule_id,
        scene_id,
        label: "Retained Host source fixture".into(),
        editable_parameters: vec!["label".into()],
        native_editor_hint: "Blender".into(),
    }
}

// Fixture facts from an independent owner-local process only. This creates no
// Core project, NativeRef, grant or Host context. Original source bytes stay in CAS.
fn emit_capsule(
    service: &StudioService,
    project: &Project,
    capsule_id: Uuid,
) -> Result<(), Box<dyn std::error::Error>> {
    let source = service.verified_native_capsule_source(
        project.id,
        &RevisionStamp::from(project),
        capsule_id,
    )?;
    let capsule = project
        .production_design
        .capsules
        .iter()
        .find(|capsule| capsule.id == capsule_id)
        .ok_or("fixture capsule missing")?;
    let mut retained_sources = Vec::new();
    {
        let shared = service.store();
        let store = shared.lock();
        for asset in &project.assets {
            if asset.source_revision.as_deref() == Some("native-capsule-source-import") {
                let sha256 = asset
                    .content_sha256
                    .as_deref()
                    .ok_or("source digest missing")?;
                let bytes = store.read_blob(sha256, 32 * 1024 * 1024)?;
                retained_sources.push(json!({"asset_id": asset.id, "sha256": sha256,
                    "bytes": bytes.len()}));
            }
        }
    }
    if service.project(project.id)? != *project {
        return Err("fixture project changed during source readback".into());
    }
    println!(
        "{}",
        json!({
            "id": project.id, "resource": project.resource_key(),
            "generation": project.generation, "revision": project.revision.to_string(),
            "title": project.title, "capsule_id": source.capsule_id,
            "scene_id": source.scene_id, "source_asset_id": source.source_asset_id,
            "source_sha256": source.source_sha256, "source_bytes": source.byte_len(),
            "media_type": source.media_type, "capsule_fidelity": capsule.fidelity,
            "retained_sources": retained_sources, "owner_local_fixture_only": true,
            "native_authority": false,
        })
    );
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let database = args.next().map(PathBuf::from).unwrap_or_else(|| usage());
    let command = args
        .next()
        .and_then(|value| value.into_string().ok())
        .unwrap_or_else(|| usage());
    let service = StudioService::open(database)?;

    match command.as_str() {
        "capsule-seed" => {
            let title = argument(&mut args);
            let source = args.next().map(PathBuf::from).unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            let initial = service.create_project(&title)?;
            let scene = service
                .apply(
                    initial.id,
                    &RevisionStamp::from(&initial),
                    "host-e11-source-scene",
                    &Change::AddScene {
                        name: "Original GLB source".into(),
                        objective: "Observe retained source through the genuine Host".into(),
                        duration_seconds: 3,
                    },
                )?
                .project;
            let scene_id = scene.scenes.first().ok_or("fixture scene missing")?.id;
            let capsule_id = Uuid::new_v4();
            let imported = service
                .import_native_capsule_source_file(
                    scene.id,
                    &RevisionStamp::from(&scene),
                    "host-e11-original-source",
                    &source,
                    capsule_spec(capsule_id, scene_id),
                )?
                .project;
            emit_capsule(&service, &imported, capsule_id)?;
        }
        "capsule-show" => {
            let project_id = canonical_uuid(&argument(&mut args));
            let capsule_id = canonical_uuid(&argument(&mut args));
            if args.next().is_some() {
                usage();
            }
            emit_capsule(&service, &service.project(project_id)?, capsule_id)?;
        }
        "capsule-import" => {
            let project_id = canonical_uuid(&argument(&mut args));
            let generation = canonical_uuid(&argument(&mut args));
            let raw_revision = argument(&mut args);
            let revision = raw_revision
                .parse::<u64>()
                .ok()
                .filter(|revision| revision.to_string() == raw_revision)
                .unwrap_or_else(|| usage());
            let capsule_id = canonical_uuid(&argument(&mut args));
            let scene_id = canonical_uuid(&argument(&mut args));
            let request_id = argument(&mut args);
            let source = args.next().map(PathBuf::from).unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            let current = service.project(project_id)?;
            if current.generation != generation || current.revision != revision {
                return Err(
                    "explicit fixture source revision is stale; import was not attempted".into(),
                );
            }
            let current_capsule = current
                .production_design
                .capsules
                .iter()
                .find(|capsule| capsule.id == capsule_id)
                .ok_or("fixture capsule missing")?;
            if current_capsule.scene_id != scene_id {
                return Err("fixture source cannot be rebound to another scene".into());
            }
            let imported = service
                .import_native_capsule_source_file(
                    project_id,
                    &RevisionStamp::from(&current),
                    &request_id,
                    &source,
                    capsule_spec(capsule_id, scene_id),
                )?
                .project;
            emit_capsule(&service, &imported, capsule_id)?;
        }
        "init" => {
            let title = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            let project = service.create_project(&title)?;
            emit(&project);
        }
        "show" => {
            let project_id = args
                .next()
                .and_then(|value| value.into_string().ok())
                .and_then(|value| Uuid::parse_str(&value).ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            emit(&service.project(project_id)?);
        }
        "rename" => {
            let project_id = args
                .next()
                .and_then(|value| value.into_string().ok())
                .and_then(|value| Uuid::parse_str(&value).ok())
                .unwrap_or_else(|| usage());
            let request_id = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            let title = args
                .next()
                .and_then(|value| value.into_string().ok())
                .unwrap_or_else(|| usage());
            if args.next().is_some() {
                usage();
            }
            let current = service.project(project_id)?;
            let outcome = service.apply(
                project_id,
                &RevisionStamp::from(&current),
                &request_id,
                &Change::RenameProject { title },
            )?;
            emit(&outcome.project);
        }
        _ => usage(),
    }

    Ok(())
}
