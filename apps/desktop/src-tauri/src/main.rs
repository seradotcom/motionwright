use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_native::build_application;
use motionwright_service::{ProjectEvent, StudioService};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
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
struct BundlePathRequest {
    path: String,
}

#[derive(Debug, Deserialize)]
struct ExportBundleRequest {
    project_id: Uuid,
    path: String,
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
            export_project_bundle,
            inspect_project_bundle,
            import_project_bundle,
            apply_change
        ])
        .run(tauri::generate_context!())
        .expect("Motionwright desktop runtime failed");
}
