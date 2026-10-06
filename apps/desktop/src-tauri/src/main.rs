use motionwright_domain::{
    AlignmentEvidence, CaptionFormat, Change, CueEvidence, Project, RevisionStamp, caption_sidecar,
};
use motionwright_native::{
    build_application,
    production::{ProductionClient, ProductionConnection},
};
use motionwright_service::{
    ModelRequestDraft, ModelRequestPreflight, ProductionJobProjection, ProjectEvent, StudioService,
    VoiceImportMetadata,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{fs::OpenOptions, io::Write, path::PathBuf};
use tauri::{Manager, State};
use uuid::Uuid;

const SEMWRIGHT_REVISION: &str = "4d291de26724810017ce7b6d185326514cb79fa6";

#[derive(Clone)]
struct AppState {
    service: StudioService,
}

#[derive(Debug, Serialize)]
struct NativeSdkInfo {
    application: &'static str,
    pinned_revision: &'static str,
    mode: &'static str,
}

#[derive(Debug, Serialize)]
struct BootstrapResponse {
    project: Project,
    native_sdk: NativeSdkInfo,
}

#[derive(Debug, Deserialize)]
struct ApplyRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    change: Change,
}

#[derive(Debug, Deserialize)]
struct HistoryRequest {
    project_id: Uuid,
    after_revision: u64,
    limit: usize,
}

#[derive(Debug, Deserialize)]
struct ProductionJobsRequest {
    project_id: Uuid,
    limit: usize,
}

#[derive(Debug, Deserialize)]
struct ModelRequestPreflightRequest {
    project_id: Uuid,
    draft: ModelRequestDraft,
}

#[derive(Debug, Deserialize)]
struct WorkflowOverviewRequest {
    project_id: Uuid,
}

#[derive(Debug, Serialize)]
struct WorkflowOverviewResponse {
    status: &'static str,
    reason: Option<String>,
    connection_identity: Option<String>,
    authority: Option<Value>,
    traces: Value,
    candidates: Value,
    patterns: Value,
    suggestions: Value,
    proposals: Value,
    promotions: Value,
}

#[derive(Debug, Deserialize)]
struct BundlePathRequest {
    path: String,
}

#[derive(Debug, Deserialize)]
struct ExportBundleRequest {
    project_id: Uuid,
    path: String,
}

#[derive(Debug, Deserialize)]
struct ExportCaptionRequest {
    project_id: Uuid,
    profile_id: Uuid,
    path: String,
}

#[derive(Debug, Serialize)]
struct CaptionExportResponse {
    path: String,
    cue_count: usize,
}

#[derive(Debug, Deserialize)]
struct ImportAssetRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    path: String,
    name: Option<String>,
    media_type: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ImportVoiceRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    path: String,
    name: Option<String>,
    media_type: Option<String>,
    label: Option<String>,
}

#[derive(Debug, Serialize)]
struct BundlePlanResponse {
    project_id: Uuid,
    title: String,
    source_generation: Uuid,
    revision: String,
    event_count: usize,
    blob_count: usize,
    total_blob_bytes: String,
    rotates_generation: bool,
}

#[derive(Debug, Serialize)]
struct BundleExportResponse {
    destination: String,
    blob_count: usize,
    total_blob_bytes: String,
}

#[tauri::command]
fn bootstrap(state: State<'_, AppState>) -> Result<BootstrapResponse, String> {
    let project = state
        .service
        .projects(1)
        .map_err(sanitized)?
        .into_iter()
        .next()
        .ok_or_else(|| "No Motionwright project is available".to_string())?;
    Ok(BootstrapResponse {
        project,
        native_sdk: NativeSdkInfo {
            application: motionwright_native::APP_ID,
            pinned_revision: SEMWRIGHT_REVISION,
            mode: "tauri",
        },
    })
}

#[tauri::command]
fn project_history(
    state: State<'_, AppState>,
    request: HistoryRequest,
) -> Result<Vec<ProjectEvent>, String> {
    state
        .service
        .history(request.project_id, request.after_revision, request.limit)
        .map_err(sanitized)
}

#[tauri::command]
fn model_request_preflight(
    state: State<'_, AppState>,
    request: ModelRequestPreflightRequest,
) -> Result<ModelRequestPreflight, String> {
    state
        .service
        .model_request_preflight(request.project_id, &request.draft)
        .map_err(sanitized)
}

#[tauri::command]
async fn workflow_overview(
    state: State<'_, AppState>,
    request: WorkflowOverviewRequest,
) -> Result<WorkflowOverviewResponse, String> {
    let Some(connection_path) = std::env::var_os("MOTIONWRIGHT_SEMWRIGHT_CONNECTION") else {
        return Ok(WorkflowOverviewResponse {
            status: "unconfigured",
            reason: Some("Set MOTIONWRIGHT_SEMWRIGHT_CONNECTION to an owner-provisioned canonical Semwright connection file.".into()),
            connection_identity: None,
            authority: None,
            traces: serde_json::json!({"traces": []}),
            candidates: serde_json::json!({"candidates": []}),
            patterns: serde_json::json!({"patterns": []}),
            suggestions: serde_json::json!({"suggestions": []}),
            proposals: serde_json::json!({"proposals": []}),
            promotions: serde_json::json!({"promotions": []}),
        });
    };

    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    let connection = ProductionConnection::load(PathBuf::from(connection_path))
        .map_err(|_| "Canonical Semwright connection could not be loaded".to_string())?;
    if connection.resource != project.resource_key() {
        return Err(
            "The canonical Semwright connection is bound to another Motionwright resource.".into(),
        );
    }
    let connection_identity = connection
        .identity()
        .map_err(|_| "Canonical Semwright connection identity could not be verified".to_string())?;
    let client = ProductionClient::new(connection)
        .map_err(|_| "Canonical Semwright connection was rejected".to_string())?;

    let traces_result = client
        .execute("workflow.traces.list", serde_json::json!({}), false)
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?;
    let authority = traces_result
        .value
        .pointer("/execution/provenance")
        .cloned();
    let traces = traces_result
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);
    let candidates = client
        .execute("workflow.candidates.list", serde_json::json!({}), false)
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);
    let patterns = client
        .execute(
            "workflow.patterns.list",
            serde_json::json!({"min_occurrences": 2}),
            false,
        )
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);
    let suggestions = client
        .execute(
            "workflow.suggestions.list",
            serde_json::json!({"min_occurrences": 3, "include_dismissed": false}),
            false,
        )
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);
    let proposals = client
        .execute(
            "workflow.proposals.list",
            serde_json::json!({"min_occurrences": 3, "include_dismissed": false}),
            false,
        )
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);
    let promotions = client
        .execute("workflow.promotions.list", serde_json::json!({}), false)
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);

    Ok(WorkflowOverviewResponse {
        status: "available",
        reason: None,
        connection_identity: Some(connection_identity),
        authority,
        traces,
        candidates,
        patterns,
        suggestions,
        proposals,
        promotions,
    })
}

#[tauri::command]
fn production_jobs(
    state: State<'_, AppState>,
    request: ProductionJobsRequest,
) -> Result<Vec<ProductionJobProjection>, String> {
    state
        .service
        .production_jobs(request.project_id, request.limit)
        .map_err(sanitized)
}

#[tauri::command]
fn import_asset_file(
    state: State<'_, AppState>,
    request: ImportAssetRequest,
) -> Result<Project, String> {
    let source = absolute_asset_path(&request.path)?;
    if !source.is_file() {
        return Err("Asset source must be an existing local file.".into());
    }
    let name = request
        .name
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            source
                .file_name()
                .and_then(|value| value.to_str())
                .map(str::to_owned)
        })
        .ok_or_else(|| "Asset file name is unavailable.".to_string())?;
    let media_type = request
        .media_type
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| infer_media_type(&source).to_owned());
    let current = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    let expected = RevisionStamp {
        resource: current.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    state
        .service
        .import_asset_file(
            request.project_id,
            &expected,
            &Uuid::now_v7().to_string(),
            &source,
            name,
            media_type,
        )
        .map(|outcome| outcome.project)
        .map_err(sanitized)
}

#[tauri::command]
fn import_voice_file(
    state: State<'_, AppState>,
    request: ImportVoiceRequest,
) -> Result<Project, String> {
    let source = absolute_asset_path(&request.path)?;
    if !source.is_file() {
        return Err("Voice source must be an existing local file.".into());
    }
    let name = request
        .name
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            source
                .file_name()
                .and_then(|value| value.to_str())
                .map(str::to_owned)
        })
        .ok_or_else(|| "Voice file name is unavailable.".to_string())?;
    let label = request
        .label
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            source
                .file_stem()
                .and_then(|value| value.to_str())
                .map(str::to_owned)
        })
        .unwrap_or_else(|| "Voice take".into());
    let media_type = request
        .media_type
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .unwrap_or_else(|| infer_media_type(&source).to_owned());
    if !media_type.starts_with("audio/") {
        return Err("Voice import requires a supported audio file.".into());
    }
    let current = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    let expected = RevisionStamp {
        resource: current.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    state
        .service
        .import_voice_file(
            request.project_id,
            &expected,
            &Uuid::now_v7().to_string(),
            &source,
            VoiceImportMetadata {
                name,
                media_type,
                label,
            },
        )
        .map(|outcome| outcome.project)
        .map_err(sanitized)
}

#[tauri::command]
fn export_project_bundle(
    state: State<'_, AppState>,
    request: ExportBundleRequest,
) -> Result<BundleExportResponse, String> {
    let destination = absolute_bundle_path(&request.path)?;
    let manifest = state
        .service
        .export_project_bundle(request.project_id, &destination)
        .map_err(sanitized)?;
    let total_blob_bytes = manifest
        .blobs
        .iter()
        .map(|blob| blob.size_bytes)
        .sum::<u64>();
    Ok(BundleExportResponse {
        destination: destination.display().to_string(),
        blob_count: manifest.blobs.len(),
        total_blob_bytes: total_blob_bytes.to_string(),
    })
}

#[tauri::command]
fn export_caption_sidecar(
    state: State<'_, AppState>,
    request: ExportCaptionRequest,
) -> Result<CaptionExportResponse, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    let sidecar = caption_sidecar(&project, request.profile_id).map_err(sanitized)?;
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
    file.write_all(sidecar.body.as_bytes())
        .map_err(|_| "Motionwright could not write the caption sidecar.".to_string())?;
    file.sync_all()
        .map_err(|_| "Motionwright could not finalize the caption sidecar.".to_string())?;
    Ok(CaptionExportResponse {
        path: destination.display().to_string(),
        cue_count: sidecar.cue_count,
    })
}

#[tauri::command]
fn inspect_project_bundle(
    state: State<'_, AppState>,
    request: BundlePathRequest,
) -> Result<BundlePlanResponse, String> {
    let source = absolute_bundle_path(&request.path)?;
    let plan = state
        .service
        .inspect_project_bundle(&source)
        .map_err(sanitized)?;
    Ok(BundlePlanResponse {
        project_id: plan.project.project_id,
        title: plan.project.title,
        source_generation: plan.project.source_generation,
        revision: plan.project.revision.to_string(),
        event_count: plan.project.event_count,
        blob_count: plan.blob_count,
        total_blob_bytes: plan.total_blob_bytes.to_string(),
        rotates_generation: plan.project.rotates_generation,
    })
}

#[tauri::command]
fn import_project_bundle(
    state: State<'_, AppState>,
    request: BundlePathRequest,
) -> Result<Project, String> {
    let source = absolute_bundle_path(&request.path)?;
    state
        .service
        .import_project_bundle(&source)
        .map_err(sanitized)
}

#[tauri::command]
fn apply_change(state: State<'_, AppState>, request: ApplyRequest) -> Result<Project, String> {
    let trusted_audio_evidence = match &request.change {
        Change::ImportMeasuredVoice { .. } | Change::AddVoiceTrack { .. } => true,
        Change::AddTranscriptSegment { segment } | Change::UpsertTranscriptSegment { segment } => {
            matches!(segment.alignment, AlignmentEvidence::Measured { .. })
        }
        Change::AddAudioCue { cue } | Change::UpsertAudioCue { cue } => matches!(
            cue.evidence,
            CueEvidence::Measured | CueEvidence::TranscriptAligned
        ),
        _ => false,
    };
    if trusted_audio_evidence {
        return Err(
            "Measured audio evidence can only enter through the qualified audio import/alignment boundary."
                .into(),
        );
    }
    let current = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    let expected = RevisionStamp {
        resource: current.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    state
        .service
        .apply(
            request.project_id,
            &expected,
            &request.request_id,
            &request.change,
        )
        .map(|outcome| outcome.project)
        .map_err(sanitized)
}

fn absolute_asset_path(value: &str) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Choose an absolute asset file path.".into());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("Asset file paths must be absolute.".into());
    }
    Ok(path)
}

fn infer_media_type(path: &std::path::Path) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "wav" => "audio/wav",
        "mp3" => "audio/mpeg",
        "flac" => "audio/flac",
        "ogg" | "oga" => "audio/ogg",
        "m4a" | "aac" => "audio/mp4",
        "aif" | "aiff" => "audio/aiff",
        "caf" => "audio/x-caf",
        "mp4" => "video/mp4",
        "mov" => "video/quicktime",
        "webm" => "video/webm",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "svg" => "image/svg+xml",
        "glb" => "model/gltf-binary",
        "gltf" => "model/gltf+json",
        "json" => "application/json",
        "srt" => "application/x-subrip",
        "vtt" => "text/vtt",
        "txt" => "text/plain",
        _ => "application/octet-stream",
    }
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

fn absolute_bundle_path(value: &str) -> Result<PathBuf, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("Choose an absolute portable bundle path.".into());
    }
    let path = PathBuf::from(trimmed);
    if !path.is_absolute() {
        return Err("Portable bundle paths must be absolute.".into());
    }
    Ok(path)
}

fn sanitized(error: impl std::fmt::Display) -> String {
    let text = error.to_string();
    if text.contains("stale project base")
        || text.contains("generation differs")
        || text.contains("request id was reused")
    {
        return text;
    }
    if text.contains("locked")
        || text.contains("invalid project")
        || text.contains("not found")
        || text.contains("project already exists")
        || text.contains("invalid project backup")
        || text.contains("invalid blob digest")
        || text.contains("blob ")
        || text.contains("export destination already exists")
        || text.contains("storage schema")
    {
        return text;
    }
    "Motionwright could not complete the local operation".into()
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|error| format!("app data path unavailable: {error}"))?;
            std::fs::create_dir_all(&data_dir)?;
            let service = StudioService::open(data_dir.join("motionwright.sqlite3"))
                .map_err(|error| format!("local project store unavailable: {error}"))?;
            if service
                .projects(1)
                .map_err(|error| format!("project index unavailable: {error}"))?
                .is_empty()
            {
                service
                    .create_project("Untitled Motion Project")
                    .map_err(|error| format!("initial project unavailable: {error}"))?;
            }

            // Build the public Native SDK cooperation surface during application setup.
            // Driver Host runs the same surface through the standalone provider executable.
            build_application(service.clone())
                .map_err(|error| format!("Native SDK contract invalid: {error}"))?;

            app.manage(AppState { service });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bootstrap,
            project_history,
            model_request_preflight,
            workflow_overview,
            production_jobs,
            import_asset_file,
            import_voice_file,
            export_project_bundle,
            export_caption_sidecar,
            inspect_project_bundle,
            import_project_bundle,
            apply_change
        ])
        .run(tauri::generate_context!())
        .expect("Motionwright desktop runtime failed");
}
