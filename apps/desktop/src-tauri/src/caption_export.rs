use super::effect_grants::{EffectGrantRegistry, EffectKind, EffectScope};
use super::project_library::{OpenProjectRequest, open_project};
use motionwright_domain::{CaptionFormat, caption_sidecar};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs::OpenOptions, io::Write, path::PathBuf};
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportCaptionRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub profile_id: Uuid,
    pub path: String,
    pub effect_grant: Uuid,
}

#[derive(Debug, Serialize)]
pub struct CaptionExportResponse {
    pub path: String,
    pub cue_count: usize,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: String,
    pub profile_id: Uuid,
    pub sidecar_sha256: String,
    /// Observed after writing; false also covers an unavailable source read.
    pub source_current: bool,
}

/// One desktop effect boundary shared with the opt-in service test transport.
/// Derive the sidecar from the same immutable Project whose stamp was checked.
/// No second project load may substitute a newer caption decision after consent.
pub fn export_caption_sidecar(
    service: &StudioService,
    grants: &EffectGrantRegistry,
    request: ExportCaptionRequest,
) -> Result<CaptionExportResponse, String> {
    let project = open_project(
        service,
        OpenProjectRequest {
            project_id: request.project_id,
        },
    )?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err(
            "Caption export belongs to a previous creative revision. Reopen the project before exporting."
                .into(),
        );
    }
    grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        request.path.trim(),
    )?;
    let sidecar =
        caption_sidecar(&project, request.profile_id).map_err(|error| error.to_string())?;
    let destination = absolute_caption_path(&request.path, sidecar.format)?;
    let parent = destination
        .parent()
        .ok_or_else(|| "Caption export destination has no parent directory.".to_string())?;
    if !parent.is_dir() {
        return Err("Caption export directory must already exist.".into());
    }
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                "Caption export destination already exists; Motionwright will not overwrite it."
                    .to_string()
            } else {
                "Motionwright could not create the caption sidecar.".to_string()
            }
        })?;
    file.write_all(sidecar.body.as_bytes()).map_err(|_| {
        "The caption sidecar could not be fully written. A file may exist at the chosen destination; inspect it before another export."
            .to_string()
    })?;
    file.sync_all().map_err(|_| {
        "The caption sidecar could not be finalized. A file may exist at the chosen destination; inspect it before another export."
            .to_string()
    })?;
    // A later edit cannot invalidate an already written historical sidecar or
    // justify deleting it. Return its exact source even if rechecking fails.
    let source_current = open_project(
        service,
        OpenProjectRequest {
            project_id: request.project_id,
        },
    )
    .is_ok_and(|current| {
        current.generation == request.generation && current.revision == request.revision
    });
    Ok(CaptionExportResponse {
        path: destination.display().to_string(),
        cue_count: sidecar.cue_count,
        project_id: project.id,
        generation: project.generation,
        revision: project.revision.to_string(),
        profile_id: request.profile_id,
        sidecar_sha256: format!("{:x}", Sha256::digest(sidecar.body.as_bytes())),
        source_current,
    })
}

fn absolute_caption_path(value: &str, format: CaptionFormat) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Choose an absolute caption sidecar path.".into());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("Caption sidecar paths must be absolute.".into());
    }
    let expected = match format {
        CaptionFormat::WebVtt => "vtt",
        CaptionFormat::SubRip => "srt",
    };
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if extension != expected {
        return Err(format!(
            "Caption sidecar must use the .{expected} extension."
        ));
    }
    Ok(path)
}
