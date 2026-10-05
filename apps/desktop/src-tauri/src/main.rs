use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_native::build_application;
use motionwright_service::{ProjectEvent, StudioService};
use serde::{Deserialize, Serialize};
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

fn sanitized(error: impl std::fmt::Display) -> String {
    let text = error.to_string();
    if text.contains("stale project base")
        || text.contains("generation differs")
        || text.contains("request id was reused")
    {
        return text;
    }
    if text.contains("locked") || text.contains("invalid project") || text.contains("not found") {
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
            apply_change
        ])
        .run(tauri::generate_context!())
        .expect("Motionwright desktop runtime failed");
}
