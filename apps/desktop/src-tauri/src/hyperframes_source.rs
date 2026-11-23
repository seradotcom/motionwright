//! Saved HyperFrames source inspection and inert native-file export.
//! No provider, runtime, process or editor is started by this module.
use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use motionwright_domain::Project;
use motionwright_native::hyperframes::compile_hyperframes_source;
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    io::{Read, Seek, SeekFrom, Write},
    path::Path,
};
use uuid::Uuid;

const SCHEMA: &str = "motionwright.hyperframes-source/1";
const STALE: &str =
    "The saved HyperFrames source changed; inspect the current project and profile.";

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SourceRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub active_branch: Uuid,
    pub deliverable_id: Uuid,
}

#[derive(Debug, Serialize)]
pub struct SourceSnapshot {
    pub schema: &'static str,
    pub project: Project,
    pub deliverable_id: Uuid,
    pub fingerprint: String,
    pub source_sha256: String,
    pub source_bytes: String,
    pub html_sha256: String,
    pub html_bytes: String,
    pub compiler: String,
    pub hyperframes_version: String,
    pub width: u32,
    pub height: u32,
    pub frame_count: u32,
    pub project_modified: bool,
    pub editor_launched: bool,
    pub native_execution_started: bool,
}

#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SourceFormat {
    Html,
    Json,
}

impl SourceFormat {
    fn label(self) -> &'static str {
        match self {
            Self::Html => "html",
            Self::Json => "json",
        }
    }
    fn filename(self) -> &'static str {
        match self {
            Self::Html => "index.html",
            Self::Json => "semwright-hyperframes.json",
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportRequest {
    pub source: SourceRequest,
    pub expected_fingerprint: String,
    pub format: SourceFormat,
    pub path: String,
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct ExportResponse {
    pub schema: &'static str,
    pub source: SourceRequest,
    pub fingerprint: String,
    pub format: SourceFormat,
    pub path: String,
    pub content_sha256: String,
    pub size_bytes: String,
    pub project_modified: bool,
    pub editor_launched: bool,
    pub native_execution_started: bool,
}

fn saved_project(service: &StudioService, request: &SourceRequest) -> Result<Project, String> {
    if request.project_id.is_nil()
        || request.generation.is_nil()
        || request.active_branch.is_nil()
        || request.deliverable_id.is_nil()
    {
        return Err("Select a saved project and delivery profile.".into());
    }
    let project = service
        .project(request.project_id)
        .map_err(|_| "The saved project is unavailable.".to_string())?;
    if project.generation != request.generation
        || project.revision != request.revision
        || project.active_branch != request.active_branch
    {
        return Err(STALE.into());
    }
    Ok(project)
}

fn require_original(service: &StudioService, project: &Project) -> Result<(), String> {
    if service.project(project.id).map_err(|_| STALE.to_string())? != *project {
        return Err(STALE.into());
    }
    Ok(())
}

pub fn inspect_source(
    service: &StudioService,
    request: SourceRequest,
) -> Result<SourceSnapshot, String> {
    let project = saved_project(service, &request)?;
    let compiled = compile_hyperframes_source(&project, request.deliverable_id)
        .map_err(|error| error.message)?;
    let inventory = compiled.inventory();
    require_original(service, &project)?;
    Ok(SourceSnapshot {
        schema: SCHEMA,
        project,
        deliverable_id: request.deliverable_id,
        fingerprint: inventory.fingerprint,
        source_sha256: inventory.source_sha256,
        source_bytes: inventory.source_bytes.to_string(),
        html_sha256: inventory.html_sha256,
        html_bytes: inventory.html_bytes.to_string(),
        compiler: inventory.compiler,
        hyperframes_version: inventory.hyperframes_version,
        width: inventory.composition.width,
        height: inventory.composition.height,
        frame_count: inventory.composition.frame_count,
        project_modified: false,
        editor_launched: false,
        native_execution_started: false,
    })
}

fn subject(request: &ExportRequest) -> String {
    format!(
        "hyperframes-source|{}|{}|{}|{}|{}",
        request.source.active_branch,
        request.source.deliverable_id,
        request.expected_fingerprint,
        request.format.label(),
        request.path.trim()
    )
}

pub fn export_source(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ExportRequest,
) -> Result<ExportResponse, String> {
    let project = saved_project(service, &request.source)?;
    let compiled = compile_hyperframes_source(&project, request.source.deliverable_id)
        .map_err(|error| error.message)?;
    if request.expected_fingerprint != compiled.fingerprint {
        return Err(STALE.into());
    }
    let path = request.path.trim();
    let destination = Path::new(path);
    if !destination.is_absolute()
        || path.chars().any(char::is_control)
        || destination.file_name().and_then(|name| name.to_str()) != Some(request.format.filename())
        || destination.parent().is_none_or(|parent| !parent.is_dir())
    {
        return Err(format!(
            "Choose {} in an existing absolute directory.",
            request.format.filename()
        ));
    }
    let bytes = match request.format {
        SourceFormat::Html => &compiled.html,
        SourceFormat::Json => &compiled.source,
    };
    let expected_hash = match request.format {
        SourceFormat::Html => &compiled.html_sha256,
        SourceFormat::Json => &compiled.source_sha256,
    };
    // The compiler bounds the source; independently bound the filesystem effect.
    if bytes.is_empty()
        || bytes.len() > 256 * 1024
        || format!("{:x}", Sha256::digest(bytes)) != *expected_hash
    {
        return Err("The generated native source failed its bounded integrity check.".into());
    }
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        EffectScope::project(project.id, project.generation, project.revision),
        &subject(&request),
    )?;
    require_original(service, &project)?;
    let parent = destination
        .parent()
        .ok_or("Native source destination has no parent.")?;
    let mut staging = tempfile::Builder::new()
        .prefix(".motionwright-hyperframes-")
        .tempfile_in(parent)
        .map_err(|_| "Could not prepare the native source export.".to_string())?;
    let file = staging.as_file_mut();
    file.write_all(bytes)
        .and_then(|()| file.sync_all())
        .map_err(|_| "Could not write the native source export.".to_string())?;
    file.seek(SeekFrom::Start(0))
        .map_err(|_| "Could not verify the staged native source.".to_string())?;
    let mut retained = Vec::new();
    file.take(256 * 1024 + 1)
        .read_to_end(&mut retained)
        .map_err(|_| "Could not read back the staged native source.".to_string())?;
    if retained != *bytes {
        return Err("Staged native source bytes changed before publication.".into());
    }
    require_original(service, &project)?;
    staging.persist_noclobber(destination).map_err(|error| {
        if error.error.kind() == std::io::ErrorKind::AlreadyExists {
            "Destination already exists; the original file was not overwritten.".to_string()
        } else { "The original source publication could not be confirmed; inspect the destination before another export.".to_string() }
    })?;
    Ok(ExportResponse {
        schema: "motionwright.hyperframes-source-export/1",
        source: request.source,
        fingerprint: compiled.fingerprint,
        format: request.format,
        path: path.into(),
        content_sha256: expected_hash.clone(),
        size_bytes: bytes.len().to_string(),
        project_modified: false,
        editor_launched: false,
        native_execution_started: false,
    })
}
