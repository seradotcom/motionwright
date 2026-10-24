mod av_delivery;
mod av_master;
mod caption_export;
mod effect_grants;
mod file_import;
mod generation_journal;
mod local_error;
mod media_integrity;
mod narration_native;
mod original_mix_native;
mod project_export;
mod project_library;
mod project_projection;
mod runtime_setup;
mod workflow_native;

use av_delivery::{
    MasterExportReceipt, MasterExportRequest, MasterReviewRequest, NativeMasterDeliveryRegistry,
};
use media_integrity::{
    PortableMediaVerification, VerifyPortableMediaRequest, verify_portable_media,
};
mod native_preview;

use av_master::{
    canonical_av_request_id, stage_measured_wav, stage_original_mix_wav,
    validate_master_voice_timing,
};
use caption_export::{CaptionExportResponse, ExportCaptionRequest};

use effect_grants::{EffectGrantReceipt, EffectGrantRegistry, EffectKind, EffectScope};
use local_error::sanitized;
use motionwright_domain::{AlignmentEvidence, Change, CueEvidence, Project, RevisionStamp};
use motionwright_native::{
    assembly::preflight_multi_segment_mlt,
    build_application,
    creative_doctor::{
        CliEvidence, CreativeRuntimeDoctorReport, inspect_creative_runtime, require_doctor_scope,
    },
    film::{FilmBuildOptions, build_motion_canvas_segments},
    owner_reconciliation::{
        OwnerMotionStatusObservation, authorize_original_motion_status,
        classify_original_motion_status,
    },
    production::{
        MltAvMasterEvidence, MltAvMasterRequest, MltMultisegmentAvMasterRequest,
        MotionCanvasRenderEvidence, ProductionClient, ProductionConnection, ProductionCoordinator,
        ProductionReceiptCustody,
    },
};
use motionwright_service::{
    AssetIntegrityPage, MltCleanupInventory, ModelRequestDraft, ModelRequestPreflight,
    ProductionJobProjection, ProjectCreationIntent, ProjectCreationOutcome, ProjectEvent,
    StudioService, WaveformPage,
};
use native_preview::{NativeFrameGrant, NativeFrameRequest, NativePreviewRegistry};
use project_export::{
    BundleExportResponse, ExportBundleRequest, ExportOtioRequest, OtioExportResponse,
    absolute_bundle_path,
};
use project_library::{BootstrapProjectSelection, BootstrapRequest};
use runtime_setup::RuntimeConnectionRegistry;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;
use workflow_native::{WorkflowActionFailure, WorkflowActionRequest, WorkflowActionResponse};

const SEMWRIGHT_REVISION: &str = "54d3459a051adbce4cae9f60c0dea8f60c824be6";
const SEMWRIGHT_VERSION: &str = "1.0.0";

#[derive(Clone)]
struct AppState {
    service: StudioService,
    /// This non-nil identity and Store are fixed for this AppState's lifetime.
    /// Creation grants bind the actual invoking window label in this session.
    workspace_session: Uuid,
    effect_grants: EffectGrantRegistry,
    production_receipt_custody: ProductionReceiptCustody,
    preview: NativePreviewRegistry,
    av_delivery: NativeMasterDeliveryRegistry,
    runtime_connections: RuntimeConnectionRegistry,
}

#[derive(Debug, Serialize)]
struct NativeSdkInfo {
    application: &'static str,
    pinned_revision: &'static str,
    version: &'static str,
    mode: &'static str,
}

#[derive(Debug, Serialize)]
struct BootstrapResponse {
    project: Option<Project>,
    project_selection: BootstrapProjectSelection,
    native_sdk: NativeSdkInfo,
}

#[derive(Debug, Deserialize)]
struct ApplyRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    effect_grant: Uuid,
    change: Change,
}

#[derive(Debug, Deserialize)]
struct IssueEffectGrantRequest {
    effect: EffectKind,
    project_id: Option<Uuid>,
    generation: Option<Uuid>,
    revision: Option<u64>,
    subject: String,
}

#[derive(Debug, Deserialize)]
struct HistoryRequest {
    project_id: Uuid,
    after_revision: u64,
    limit: usize,
}

#[derive(Debug, Deserialize)]
struct RecentHistoryRequest {
    project_id: Uuid,
    through_revision: u64,
    limit: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AssetIntegrityRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    offset: Option<usize>,
    limit: usize,
}

#[derive(Debug, Deserialize)]
struct ProductionJobsRequest {
    project_id: Uuid,
    limit: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductionJobsHistoryCursor {
    latest_id: Uuid,
    receipt_count: u64,
    offset: usize,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductionJobsHistoryRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    limit: usize,
    cursor: Option<ProductionJobsHistoryCursor>,
}

#[derive(Debug, Serialize)]
struct ProductionJobsHistoryPage {
    items: Vec<ProductionJobProjection>,
    total_jobs: usize,
    next: Option<ProductionJobsHistoryCursor>,
    complete: bool,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OwnerNativeStatusRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    job_ref: String,
}

#[derive(Debug, Deserialize)]
struct MotionCanvasRenderRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    effect_grant: Uuid,
    deliverable_id: Uuid,
    options: FilmBuildOptions,
}

#[derive(Debug, Serialize)]
struct NativeAvMasterResponse {
    #[serde(flatten)]
    evidence: MltAvMasterEvidence,
    /// Owner-minted session-only export handle; never a filesystem path.
    export_token: Option<Uuid>,
}

/// All private MLT receipts, temporary filesystem paths and opaque native
/// project references remain on the trusted side of the IPC boundary.
/// A verified master is delivered/reviewed only via a session token.
#[derive(Debug, Serialize)]
struct NativeMultisegmentAvResponse {
    project_resource: String,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    frame_count: u64,
    video_segments: usize,
    video_codec: &'static str,
    audio_codec: &'static str,
    audio_sample_rate: u32,
    audio_channels: u16,
    master_sha256: String,
    export_token: Uuid,
    /// Captioned video is owner-SHA-verified but NOT production-local
    /// decoded-glyph-verified. A token permits read-only review, not export.
    review_only: bool,
    /// A verified media output is not proof that destructive MLT cleanup
    /// was approved. Expose the exact deferred status, not hidden success.
    provider_project_cleanup: &'static str,
    evidence_scope: &'static str,
}

#[derive(Debug, Deserialize)]
struct FilmPreflightRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    options: FilmBuildOptions,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MultiSegmentReadinessRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    preview_token: Uuid,
}

#[derive(Debug, Serialize)]
struct MultiSegmentReadinessSegment {
    segment_id: String,
    scene_ids: Vec<Uuid>,
    start_frame: u64,
    frame_count: u64,
}

#[derive(Debug, Serialize)]
struct MultiSegmentReadinessResponse {
    project_resource: String,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    verdict: &'static str,
    mlt_profile: String,
    total_frames: u64,
    segments: Vec<MultiSegmentReadinessSegment>,
    evidence_scope: String,
}

/// No caller path, arbitrary render evidence or claimed audio SHA is accepted:
/// both source artifacts are resolved inside the owner desktop service.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AssembleAvMasterRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    effect_grant: Uuid,
    deliverable_id: Uuid,
    preview_token: Uuid,
    voice_track_id: Uuid,
}

#[derive(Debug, Serialize)]
struct FilmPreflightSegment {
    segment_id: String,
    scene_ids: Vec<Uuid>,
    frame_count: u64,
}

#[derive(Debug, Serialize)]
struct FilmPreflightResponse {
    project_resource: String,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    verdict: &'static str,
    reason: Option<String>,
    segment_count: usize,
    total_frames: u64,
    segments: Vec<FilmPreflightSegment>,
}

#[derive(Debug, Serialize)]
struct NativePreviewRenderResponse {
    #[serde(flatten)]
    evidence: MotionCanvasRenderEvidence,
    /// Ephemeral read-only handles. Not Semwright evidence or source paths.
    preview: Vec<NativeFrameGrant>,
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProductionRuntimeStatusRequest {
    project_id: Uuid,
    /// Optional for older IPC consumers; current Studio always supplies
    /// both, so stale render readiness never inherits a newer observation.
    #[serde(default)]
    expected_generation: Option<Uuid>,
    #[serde(default)]
    expected_revision: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuntimeConnectionSelectionRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
}

fn runtime_status_scope_matches(
    project: &Project,
    request: &ProductionRuntimeStatusRequest,
) -> bool {
    project.id == request.project_id
        && request
            .expected_generation
            .is_none_or(|expected| expected == project.generation)
        && request
            .expected_revision
            .is_none_or(|expected| expected == project.revision)
}

#[derive(Debug, Serialize)]
struct ProductionRuntimeStatusResponse {
    status: &'static str,
    reason: Option<String>,
    expected_version: &'static str,
    observed_version: Option<String>,
    version_compatible: Option<bool>,
    pinned_revision: &'static str,
    connection_identity: Option<String>,
    executable_sha256: Option<String>,
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
struct ImportBundleRequest {
    path: String,
    effect_grant: Uuid,
}

#[derive(Debug, Deserialize)]
struct WaveformPageRequest {
    project_id: Uuid,
    track_id: Uuid,
    page_index: u64,
    page_size: usize,
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

#[tauri::command]
fn bootstrap(
    state: State<'_, AppState>,
    request: Option<BootstrapRequest>,
) -> Result<BootstrapResponse, String> {
    // The saved UI selection is a read hint, never a grant or permission to
    // create/replace a document. An unavailable id must not choose a fallback.
    let (project, mut project_selection) =
        project_library::bootstrap_project(&state.service, request.unwrap_or_default());
    if let BootstrapProjectSelection::Unavailable { message, .. } = &mut project_selection {
        *message = sanitized(&*message);
    }
    Ok(BootstrapResponse {
        project,
        project_selection,
        native_sdk: NativeSdkInfo {
            application: motionwright_native::APP_ID,
            pinned_revision: SEMWRIGHT_REVISION,
            version: SEMWRIGHT_VERSION,
            mode: "tauri",
        },
    })
}

#[tauri::command]
fn list_projects(
    state: State<'_, AppState>,
    request: project_library::ListProjectsRequest,
) -> Result<project_library::ProjectLibraryPage, String> {
    project_library::list_projects(&state.service, request).map_err(sanitized)
}

#[tauri::command]
fn open_project(
    state: State<'_, AppState>,
    request: project_library::OpenProjectRequest,
) -> Result<Project, String> {
    project_library::open_project(&state.service, request).map_err(sanitized)
}

#[tauri::command]
async fn project_projection_head(
    state: State<'_, AppState>,
    request: project_projection::Head,
) -> Result<motionwright_service::ProjectionHead, motionwright_service::ProjectionFailure> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || project_projection::head(&service, request))
        .await
        .map_err(|_| motionwright_service::ProjectionFailure::Unavailable)?
}

#[tauri::command]
async fn project_scene_page(
    state: State<'_, AppState>,
    request: project_projection::Scenes,
) -> Result<motionwright_service::ScenePage, motionwright_service::ProjectionFailure> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || project_projection::scenes(&service, request))
        .await
        .map_err(|_| motionwright_service::ProjectionFailure::Unavailable)?
}

#[tauri::command]
async fn semantic_graph_authoring_context(
    state: State<'_, AppState>,
    request: motionwright_service::SemanticGraphContextRequest,
) -> Result<motionwright_domain::GraphAuthoringContext, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        project_projection::semantic_graph_context(&service, request)
    })
    .await
    .map_err(|_| "Graph source inspection is unavailable".to_owned())?
    .map_err(sanitized)
}

#[tauri::command]
async fn project_scene_nodes(
    state: State<'_, AppState>,
    request: project_projection::Nodes,
) -> Result<motionwright_service::NodePage, motionwright_service::ProjectionFailure> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || project_projection::nodes(&service, request))
        .await
        .map_err(|_| motionwright_service::ProjectionFailure::Unavailable)?
}

#[tauri::command]
fn issue_project_creation_grant(
    state: State<'_, AppState>,
    window: tauri::WebviewWindow,
    request: ProjectCreationIntent,
) -> Result<EffectGrantReceipt, String> {
    project_library::issue_creation_grant(
        &state.effect_grants,
        state.workspace_session,
        window.label(),
        &request,
    )
    .map_err(sanitized)
}

#[tauri::command]
fn create_project(
    state: State<'_, AppState>,
    window: tauri::WebviewWindow,
    request: project_library::CreateProjectRequest,
) -> Result<ProjectCreationOutcome, String> {
    project_library::create_project(
        &state.service,
        &state.effect_grants,
        state.workspace_session,
        window.label(),
        request,
    )
    .map_err(sanitized)
}

#[tauri::command]
fn recover_project_creation(
    state: State<'_, AppState>,
    request: ProjectCreationIntent,
) -> Result<Option<ProjectCreationOutcome>, String> {
    project_library::recover_project_creation(&state.service, &request).map_err(sanitized)
}

fn checked_effect_scope(
    state: &AppState,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
) -> Result<EffectScope, String> {
    let project = project_library::open_project(
        &state.service,
        project_library::OpenProjectRequest { project_id },
    )
    .map_err(sanitized)?;
    if project.generation != generation || project.revision != revision {
        return Err("Effect grant scope is stale; refresh the project before retrying.".into());
    }
    Ok(EffectScope::project(project_id, generation, revision))
}

#[tauri::command]
fn issue_effect_grant(
    state: State<'_, AppState>,
    request: IssueEffectGrantRequest,
) -> Result<EffectGrantReceipt, String> {
    if request.effect == EffectKind::ProjectCreate {
        return Err(
            "Use the dedicated project creation command with its exact retained intent.".into(),
        );
    }
    let scope = match (request.project_id, request.generation, request.revision) {
        (Some(project_id), Some(generation), Some(revision)) => {
            checked_effect_scope(&state, project_id, generation, revision)?
        }
        (None, None, None) => EffectScope::unscoped_import(),
        _ => return Err("Effect grant project scope must be complete or absent.".into()),
    };
    state
        .effect_grants
        .issue(request.effect, scope, request.subject.trim())
}

/// Explicit, read-only asset SHA-256 audit. No path/digest sources or broad
/// filesystem plugin inputs from WebView; verifies app-owned CAS bytes only.
#[tauri::command]
async fn asset_integrity_page(
    state: State<'_, AppState>,
    request: AssetIntegrityRequest,
) -> Result<AssetIntegrityPage, String> {
    if request.limit == 0 || request.limit > 16 {
        return Err("Asset integrity pages contain between 1 and 16 assets.".into());
    }
    let before = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if before.generation != request.generation || before.revision != request.revision {
        return Err("Project changed before its asset integrity inspection.".into());
    }
    let service = state.service.clone();
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let report = tauri::async_runtime::spawn_blocking(move || {
        service
            .asset_integrity_page(
                project_id,
                &RevisionStamp::from(&before),
                request.offset,
                request.limit,
            )
            .map_err(sanitized)
    })
    .await
    .map_err(|_| "Asset integrity inspection task failed.".to_string())??;
    let after = state.service.project(project_id).map_err(sanitized)?;
    if after.generation != generation || after.revision != revision {
        return Err("Project changed during its asset integrity inspection.".into());
    }
    Ok(report)
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
fn project_history_recent(
    state: State<'_, AppState>,
    request: RecentHistoryRequest,
) -> Result<Vec<ProjectEvent>, String> {
    state
        .service
        .history_recent(request.project_id, request.through_revision, request.limit)
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

/// Read an owner-selected per-project pointer or retain the legacy explicit
/// environment connection if no saved pointer exists. Never take a WebView
/// supplied filesystem path for production, preview or workflow commands.
fn selected_connection_path(state: &AppState, project_id: Uuid) -> Result<Option<PathBuf>, String> {
    let project = state.service.project(project_id).map_err(sanitized)?;
    state
        .runtime_connections
        .resolve(project.id, project.generation)
}

/// The OS native file dialog is the trusted owner gesture. JavaScript never
/// sees the selected path, connection JSON, private session ticket or Broker
/// authentication. The canonical Semwright client remains responsible for
/// digest/protocol checks; this does not install or bootstrap a provider.
#[tauri::command]
async fn choose_semwright_connection(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<bool, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Project changed; refresh before selecting a Semwright connection.".into());
    }
    let handle = app.clone();
    let selected = tauri::async_runtime::spawn_blocking(move || {
        handle
            .dialog()
            .file()
            .set_title("Select an owner-provisioned Semwright connection")
            .add_filter("JSON connection", &["json"])
            .blocking_pick_file()
    })
    .await
    .map_err(|_| "Native Semwright connection picker failed".to_string())?;
    let Some(selected) = selected else {
        return Ok(false); // cancel is not an error or a hidden connection change
    };
    let path = selected
        .into_path()
        .map_err(|_| "This native selection is not a local connection file".to_string())?;
    let connection = ProductionConnection::load(&path)
        .map_err(|_| "The selected Semwright connection failed local validation".to_string())?;
    if connection.resource != project.resource_key() {
        return Err("Selected Semwright connection belongs to another project resource.".into());
    }
    let client = ProductionClient::new(connection)
        .map_err(|_| "Selected Semwright connection was rejected".to_string())?;
    let probe = client.probe_runtime(SEMWRIGHT_VERSION).await.map_err(|_| {
        "Selected Semwright runtime is unavailable or its digest is invalid".to_string()
    })?;
    if !probe.version_compatible {
        return Err(
            "Selected Semwright runtime does not match the pinned supported version.".into(),
        );
    }
    let current = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if current.generation != project.generation || current.revision != project.revision {
        return Err("Project changed during runtime setup; no new selection was stored.".into());
    }
    state
        .runtime_connections
        .remember_verified(request.project_id, request.generation, &path)?;
    Ok(true)
}

/// A local revocation removes only Motionwright's saved path reference.
/// It cannot cancel running native jobs, remove the owner's Semwright ticket,
/// disable a separately configured environment override, or delete artifacts.
#[tauri::command]
fn clear_saved_semwright_connection(
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<bool, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Project changed; refresh before clearing a runtime selection.".into());
    }
    state.runtime_connections.clear_saved(project.id)
}

#[tauri::command]
async fn production_runtime_status(
    state: State<'_, AppState>,
    request: ProductionRuntimeStatusRequest,
) -> Result<ProductionRuntimeStatusResponse, String> {
    let initial = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if !runtime_status_scope_matches(&initial, &request) {
        return Err("Project changed before the Semwright CLI compatibility probe.".into());
    }
    let Some(connection_path) = selected_connection_path(&state, request.project_id)? else {
        return Ok(ProductionRuntimeStatusResponse {
            status: "unconfigured",
            reason: Some(
                "Choose an owner-provisioned connection in Integrations, or set the explicit MOTIONWRIGHT_SEMWRIGHT_CONNECTION environment path."
                    .into(),
            ),
            expected_version: SEMWRIGHT_VERSION,
            observed_version: None,
            version_compatible: None,
            pinned_revision: SEMWRIGHT_REVISION,
            connection_identity: None,
            executable_sha256: None,
        });
    };

    let connection = match ProductionConnection::load(connection_path) {
        Ok(connection) => connection,
        Err(_) => {
            return Ok(ProductionRuntimeStatusResponse {
                status: "invalid",
                reason: Some(
                    "The owner-provisioned Semwright connection failed local validation.".into(),
                ),
                expected_version: SEMWRIGHT_VERSION,
                observed_version: None,
                version_compatible: None,
                pinned_revision: SEMWRIGHT_REVISION,
                connection_identity: None,
                executable_sha256: None,
            });
        }
    };
    if connection.resource != initial.resource_key() {
        return Ok(ProductionRuntimeStatusResponse {
            status: "resource_mismatch",
            reason: Some(
                "The canonical Semwright connection is bound to another Motionwright resource."
                    .into(),
            ),
            expected_version: SEMWRIGHT_VERSION,
            observed_version: None,
            version_compatible: None,
            pinned_revision: SEMWRIGHT_REVISION,
            connection_identity: None,
            executable_sha256: Some(connection.executable_sha256.clone()),
        });
    }

    let client = ProductionClient::new(connection)
        .map_err(|_| "Canonical Semwright connection was rejected".to_string())?;
    let probe = client.probe_runtime(SEMWRIGHT_VERSION).await;
    // A CLI probe is asynchronous; a creative edit during the probe must not
    // attribute the result to the old source revision.
    let current = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if current.generation != initial.generation
        || current.revision != initial.revision
        || !runtime_status_scope_matches(&current, &request)
    {
        return Err("Project changed during the Semwright CLI compatibility probe.".into());
    }
    match probe {
        Ok(probe) => Ok(ProductionRuntimeStatusResponse {
            status: if probe.version_compatible {
                "ready"
            } else {
                "incompatible"
            },
            reason: if probe.version_compatible {
                None
            } else {
                Some(format!(
                    "Semwright CLI {} is present, but Motionwright currently supports {}.",
                    probe.observed_version, probe.expected_version
                ))
            },
            expected_version: SEMWRIGHT_VERSION,
            observed_version: Some(probe.observed_version),
            version_compatible: Some(probe.version_compatible),
            pinned_revision: SEMWRIGHT_REVISION,
            connection_identity: Some(probe.connection_identity),
            executable_sha256: Some(probe.executable_sha256),
        }),
        Err(_) => Ok(ProductionRuntimeStatusResponse {
            status: "unavailable",
            reason: Some(
                "The digest-pinned Semwright CLI could not complete a bounded read-only version probe."
                    .into(),
            ),
            expected_version: SEMWRIGHT_VERSION,
            observed_version: None,
            version_compatible: None,
            pinned_revision: SEMWRIGHT_REVISION,
            connection_identity: None,
            executable_sha256: Some(client.connection().executable_sha256.clone()),
        }),
    }
}

#[tauri::command]
async fn creative_runtime_doctor(
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<CreativeRuntimeDoctorReport, String> {
    let service = state.service.clone();
    let initial = service.project(request.project_id).map_err(sanitized)?;
    require_doctor_scope(&initial, request.generation, request.revision).map_err(str::to_owned)?;
    let status = production_runtime_status(
        state,
        ProductionRuntimeStatusRequest {
            project_id: request.project_id,
            expected_generation: Some(request.generation),
            expected_revision: Some(request.revision),
        },
    )
    .await?;
    let current = service.project(request.project_id).map_err(sanitized)?;
    require_doctor_scope(&current, request.generation, request.revision).map_err(str::to_owned)?;
    build_application(service)
        .map_err(|_| "Native SDK creative contract validation failed".to_string())?;
    Ok(inspect_creative_runtime(
        &current,
        SEMWRIGHT_REVISION,
        CliEvidence::from_runtime_status(status.status),
        true,
    ))
}

#[tauri::command]
async fn workflow_overview(
    state: State<'_, AppState>,
    request: WorkflowOverviewRequest,
) -> Result<WorkflowOverviewResponse, String> {
    let Some(connection_path) = selected_connection_path(&state, request.project_id)? else {
        return Ok(WorkflowOverviewResponse {
            status: "unconfigured",
            reason: Some("Choose an owner-provisioned connection in Integrations, or set the explicit MOTIONWRIGHT_SEMWRIGHT_CONNECTION environment path.".into()),
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
    let connection = ProductionConnection::load(connection_path)
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
async fn workflow_action(
    state: State<'_, AppState>,
    request: WorkflowActionRequest,
) -> Result<WorkflowActionResponse, WorkflowActionFailure> {
    let selected = selected_connection_path(&state, request.project_id);
    workflow_native::execute_workflow_action(
        &state.service,
        &state.effect_grants,
        &state.production_receipt_custody,
        selected,
        request,
    )
    .await
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

/// Read-only inventory of native MLT project lifetime receipts.
#[tauri::command]
async fn mlt_cleanup_inventory(
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<MltCleanupInventory, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        service
            .mlt_cleanup_inventory_snapshot(
                request.project_id,
                request.generation,
                request.revision,
            )
            .map_err(sanitized)
    })
    .await
    .map_err(|_| "Native MLT receipt inventory task could not complete.".to_owned())?
}

/// A single explicit owner-initiated read-only status observation. The WebView
/// supplies only an opaque selector, never a Semwright command, connection path,
/// provider permission or a new job identity. No render.start or result/verify.
#[tauri::command]
async fn read_original_native_job_status(
    state: State<'_, AppState>,
    request: OwnerNativeStatusRequest,
) -> Result<OwnerMotionStatusObservation, String> {
    if request.job_ref.is_empty()
        || request.job_ref.len() > 512
        || !request.job_ref.bytes().all(|byte| byte.is_ascii_graphic())
    {
        return Err("Native job selector is invalid.".into());
    }
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let selected = request.job_ref.clone();
    let service = state.service.clone();

    // Derive trust from a fixed receipt watermark, never from the WebView's
    // claim that some job exists. Do not block the Tauri main UI on a
    // potentially large bounded local SQLite projection.
    let (origin, watermark, resource) =
        tauri::async_runtime::spawn_blocking(move || -> Result<_, String> {
            let project = service.project(project_id).map_err(sanitized)?;
            if project.generation != generation || project.revision != revision {
                return Err("Native job observation belongs to a stale project revision.".into());
            }
            let watermark = service
                .production_receipt_watermark(project_id)
                .map_err(sanitized)?;
            let jobs = service
                .production_jobs_snapshot(project_id, &watermark)
                .map_err(|_| {
                    "Source-bound native job history could not be confirmed.".to_owned()
                })?;
            // A job_ref can recur under another provider or Project stamp.
            // Select the original Motion Canvas row from this trusted snapshot
            // before applying the existing native authority validator.
            let job = jobs
                .iter()
                .find(|job| {
                    job.job_ref == selected
                        && job.provider == "driver:motion-canvas"
                        && job.generation == generation
                        && job.revision == revision
                })
                .ok_or_else(|| {
                    "No acknowledged native render job matches the selection.".to_owned()
                })?;
            let start = service
                .latest_production_receipt(project_id, &job.root_request_id)
                .map_err(sanitized)?
                .ok_or_else(|| "Original native render start receipt is missing.".to_owned())?;
            let expected = RevisionStamp {
                resource: project.resource_key(),
                generation,
                revision,
            };
            let origin =
                authorize_original_motion_status(&project, &expected, &selected, job, &start)
                    .map_err(|_| "Native original start authority cannot be proven.".to_owned())?;
            if service
                .production_receipt_watermark(project_id)
                .map_err(sanitized)?
                != watermark
            {
                return Err(
                    "Native job receipts changed during source inspection; refresh.".into(),
                );
            }
            Ok((origin, watermark, project.resource_key()))
        })
        .await
        .map_err(|_| "Local native job inspection did not complete.".to_owned())??;

    // The connection is selected and verified by the trusted desktop backend,
    // not passed in from JavaScript. The pinned client itself checks the
    // Broker/Driver Host provenance and executable hash.
    let path = selected_connection_path(&state, project_id)?
        .ok_or_else(|| "Owner Semwright runtime connection is not configured.".to_owned())?;
    let connection = ProductionConnection::load(&path)
        .map_err(|_| "Owner Semwright runtime connection is invalid.".to_owned())?;
    if connection.resource != resource {
        return Err("Native job belongs to a different Semwright project resource.".into());
    }
    let client = ProductionClient::new(connection)
        .map_err(|_| "Owner Semwright production client could not be validated.".to_owned())?;

    // Exactly ONE read-only status query; never retry render.start, cancel,
    // result, or provider initialization after an unavailable generation.
    let status = client
        .execute(
            "driver.motion-canvas.render.status",
            serde_json::json!({"job_ref": origin.job_ref}),
            false,
        )
        .await;

    let current = state.service.project(project_id).map_err(sanitized)?;
    if current.generation != generation
        || current.revision != revision
        || state
            .service
            .production_receipt_watermark(project_id)
            .map_err(sanitized)?
            != watermark
    {
        return Err("Project or native job receipts changed during observation; refresh.".into());
    }
    // Return sanitized provider state only. The durable Jobs ledger is not
    // rewritten from this read, and a successful status is not an admitted
    // result, digest-verified frame set or finished audiovisual master.
    Ok(classify_original_motion_status(
        &origin,
        status.as_ref().ok().map(|reply| &reply.value),
    ))
}

/// Semantic authoring preflight: exactly the same project-owned Film projection
/// that canonical production uses, with NO Semwright host dispatch and no
/// mutation/effect grant. SUPPORTED never means the renderer has passed.
/// Explicit, read-only historical job browser. Unlike the lightweight
/// auto-refreshing latest-window view, this bounded on-demand operation
/// reconstructs a receipt-watermark-bound snapshot and fails stale when
/// status receipts arrive independently of the creative revision.
#[tauri::command]
async fn production_jobs_history(
    state: State<'_, AppState>,
    request: ProductionJobsHistoryRequest,
) -> Result<ProductionJobsHistoryPage, String> {
    if request.limit == 0 || request.limit > 64 {
        return Err("Production history page size must be 1-64 jobs.".into());
    }
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Production job history belongs to a stale creative revision.".into());
    }
    let service = state.service.clone();
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let page = tauri::async_runtime::spawn_blocking(move || {
        let watermark = service
            .production_receipt_watermark(project_id)
            .map_err(sanitized)?;
        let offset = match &request.cursor {
            Some(cursor) => {
                if watermark.latest_id != Some(cursor.latest_id)
                    || watermark.count != cursor.receipt_count
                    || cursor.offset == 0
                {
                    return Err(
                        "Production receipts changed during historical paging; restart the history snapshot."
                            .to_owned(),
                    );
                }
                cursor.offset
            }
            None => 0,
        };
        let jobs = service.production_jobs_snapshot(project_id, &watermark)
            .map_err(sanitized)?;
        if offset > 0 && offset >= jobs.len() {
            return Err("Historical job cursor does not make progress.".into());
        }
        let end = offset.saturating_add(request.limit).min(jobs.len());
        let next = if end < jobs.len() {
            Some(ProductionJobsHistoryCursor {
                latest_id: watermark.latest_id
                    .ok_or_else(|| "Incomplete history without a receipt watermark.".to_owned())?,
                receipt_count: watermark.count,
                offset: end,
            })
        } else {
            None
        };
        let page = ProductionJobsHistoryPage {
            items: jobs[offset..end].to_vec(),
            total_jobs: jobs.len(),
            complete: next.is_none(),
            next,
        };
        let after = service.production_receipt_watermark(project_id)
            .map_err(sanitized)?;
        if after != watermark {
            return Err(
                "Production receipts changed while computing the job history; restart."
                    .to_owned(),
            );
        }
        Ok::<_, String>((page, watermark))
    })
    .await
    .map_err(|_| "Production history task failed.".to_string())??;
    let current = state.service.project(project_id).map_err(sanitized)?;
    let latest_watermark = state
        .service
        .production_receipt_watermark(project_id)
        .map_err(sanitized)?;
    if current.generation != generation
        || current.revision != revision
        || latest_watermark != page.1
    {
        return Err("Project or production receipts changed during historical paging.".into());
    }
    Ok(page.0)
}

#[tauri::command]
async fn motion_canvas_preflight(
    state: State<'_, AppState>,
    request: FilmPreflightRequest,
) -> Result<FilmPreflightResponse, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Semantic Film preflight is stale; refresh project before retrying.".into());
    }
    let resource = project.resource_key();
    let project_id = request.project_id;
    let result = tauri::async_runtime::spawn_blocking(move || {
        let projected =
            build_motion_canvas_segments(&project, request.deliverable_id, &request.options);
        let unsupported = |reason: String| FilmPreflightResponse {
            project_resource: resource.clone(),
            generation: request.generation,
            revision: request.revision,
            deliverable_id: request.deliverable_id,
            verdict: "unsupported",
            reason: Some(reason.chars().take(480).collect()),
            segment_count: 0,
            total_frames: 0,
            segments: Vec::new(),
        };
        match projected {
            Ok(segments) if !segments.is_empty() => {
                let total_frames = segments
                    .iter()
                    .try_fold(0_u64, |sum, item| sum.checked_add(item.frame_count));
                match total_frames {
                    Some(total_frames) if total_frames > 0 => FilmPreflightResponse {
                        project_resource: resource.clone(),
                        generation: request.generation,
                        revision: request.revision,
                        deliverable_id: request.deliverable_id,
                        verdict: "projection_ready",
                        reason: None,
                        segment_count: segments.len(),
                        total_frames,
                        segments: segments
                            .into_iter()
                            .map(|segment| FilmPreflightSegment {
                                segment_id: segment.id,
                                scene_ids: segment.scene_ids,
                                frame_count: segment.frame_count,
                            })
                            .collect(),
                    },
                    _ => unsupported("Semantic Film has no bounded nonempty frame plan.".into()),
                }
            }
            Ok(_) => unsupported("This saved profile selects no Motion Canvas scenes.".into()),
            Err(error) => unsupported(error.message),
        }
    })
    .await
    .map_err(|_| "Canonical Film semantic preflight task failed.".to_string())?;
    // The result is a read-only projection, but cannot be labeled current
    // if any concurrent project edit occurred during the preflight.
    let latest = state.service.project(project_id).map_err(sanitized)?;
    if latest.generation != result.generation || latest.revision != result.revision {
        return Err(
            "Project changed during semantic Film preflight; retry at current revision.".into(),
        );
    }
    Ok(result)
}

#[tauri::command]
async fn render_motion_canvas(
    state: State<'_, AppState>,
    request: MotionCanvasRenderRequest,
) -> Result<NativePreviewRenderResponse, String> {
    let connection_path = selected_connection_path(&state, request.project_id)?
        .ok_or_else(|| "Canonical Semwright production is not configured.".to_string())?;
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Render grant scope is stale; refresh before retrying.".into());
    }
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        request.request_id.trim(),
    )?;
    let expected = RevisionStamp {
        resource: project.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "Canonical Semwright connection could not be loaded".to_string())?;
    let output_root = connection.output_root.clone();
    let owner_resource = connection.resource.clone();
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "Canonical Semwright connection was rejected".to_string())?;
    let evidence = coordinator
        .render_motion_canvas_segments(
            request.project_id,
            &expected,
            &request.request_id,
            request.deliverable_id,
            &request.options,
        )
        .await
        .map_err(|error| {
            format!(
                "Canonical Motion Canvas production was blocked: {}",
                error.message
            )
        })?;
    let registry = state.preview.clone();
    let for_grants = evidence.clone();
    let source_options = request.options.clone();
    let preview = tauri::async_runtime::spawn_blocking(move || {
        registry.register_with_options(&output_root, &owner_resource, &for_grants, &source_options)
    })
    .await
    .unwrap_or_default();
    Ok(NativePreviewRenderResponse { evidence, preview })
}

/// Source- and manifest-bound native MLT assembly readiness, without job
/// dispatch or any WebView access to the trusted output-root paths.
#[tauri::command]
async fn preflight_multi_segment_mlt_readiness(
    state: State<'_, AppState>,
    request: MultiSegmentReadinessRequest,
) -> Result<MultiSegmentReadinessResponse, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Multi-segment source belongs to a stale creative revision.".into());
    }
    let (evidence, options, root) = state.preview.multi_segment_source(
        &project,
        request.preview_token,
        request.deliverable_id,
    )?;
    let project_id = project.id;
    let source_generation = project.generation;
    let source_revision = project.revision;
    let preflight = tauri::async_runtime::spawn_blocking(move || {
        preflight_multi_segment_mlt(&project, request.deliverable_id, &options, &evidence, &root)
            .map_err(|error| {
                format!(
                    "Native multi-segment source preflight rejected the cut: {}",
                    error.message.chars().take(440).collect::<String>()
                )
            })
    })
    .await
    .map_err(|_| "Native multi-segment preflight failed to complete.".to_owned())??;
    let current = state.service.project(project_id).map_err(sanitized)?;
    if current.generation != source_generation || current.revision != source_revision {
        return Err("Creative revision changed during source preflight; retry.".into());
    }
    // Manifest paths, job refs, digests, and the private owner output root
    // never appear in the WebView; it receives only a semantic checklist.
    Ok(MultiSegmentReadinessResponse {
        project_resource: preflight.project_resource,
        generation: preflight.generation,
        revision: preflight.revision,
        deliverable_id: preflight.deliverable_id,
        verdict: "source_manifest_ready",
        mlt_profile: preflight.mlt_profile,
        total_frames: preflight.total_frames,
        segments: preflight
            .segments
            .into_iter()
            .map(|segment| MultiSegmentReadinessSegment {
                segment_id: segment.segment_id,
                scene_ids: segment.scene_ids,
                start_frame: segment.output_start_frame,
                frame_count: segment.frame_count,
            })
            .collect(),
        evidence_scope: preflight.evidence_scope,
    })
}

/// Exact bytes of one verified PNG from the current project revision. No
/// caller-provided paths, no general-purpose filesystem or network capability.
#[tauri::command]
async fn assemble_av_master(
    state: State<'_, AppState>,
    request: AssembleAvMasterRequest,
) -> Result<NativeAvMasterResponse, String> {
    let connection_path = selected_connection_path(&state, request.project_id)?
        .ok_or_else(|| "Canonical Semwright AV mastering is not configured.".to_string())?;
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("AV master input was invalidated by a project revision.".into());
    }
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == request.deliverable_id)
        .ok_or_else(|| "AV mastering requires a saved output profile.".to_string())?;
    if profile.voice_track_id != Some(request.voice_track_id) {
        return Err(
            "Bind the selected measured voice take to this exact saved delivery profile.".into(),
        );
    }
    let motion =
        state
            .preview
            .master_source(&project, request.preview_token, request.deliverable_id)?;
    let selected_voice = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == request.voice_track_id)
        .ok_or_else(|| "The measured voice take is unavailable for AV mastering.".to_string())?;
    let segment = motion
        .segments
        .first()
        .ok_or_else(|| "Native AV mastering requires one verified visual segment.".to_string())?;
    validate_master_voice_timing(
        selected_voice.measured_duration,
        segment.frame_count,
        motion.frame_rate.num,
        motion.frame_rate.den,
    )?;

    let saved_mix =
        original_mix_native::saved_mix_for_master(&state.service, &project, request.deliverable_id)
            .await?;

    // This is a production mutation and consumes only the corresponding
    // one-time revision/request-scoped local render effect capability.
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        request.request_id.trim(),
    )?;
    let expected = RevisionStamp {
        resource: project.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "Canonical Semwright connection could not be loaded".to_string())?;
    if connection.resource != project.resource_key() {
        return Err("Canonical Semwright output root belongs to another project.".into());
    }
    // Source-bound native request identity is calculated *before* the WAV
    // handoff. Otherwise a fresh staging name could accidentally alter the
    // canonical Broker payload for a retry of the same logical master.
    let canonical_request_id = canonical_av_request_id(
        project.id,
        project.generation,
        project.revision,
        request.deliverable_id,
        &segment.fingerprint,
        saved_mix
            .as_ref()
            .map_or(selected_voice.source_sha256.as_str(), |mix| {
                mix.audio_inputs_sha256()
            }),
    );
    let output_root = connection.output_root.clone();
    let export_root = output_root.clone();
    let service = state.service.clone();
    let id = request.project_id;
    let track = request.voice_track_id;
    let base = expected.clone();
    let stable_identity = canonical_request_id.clone();
    let audio = tauri::async_runtime::spawn_blocking(move || {
        if let Some(mix) = saved_mix {
            let latest = service.project(id).map_err(sanitized)?;
            mix.verify_project(&latest, mix.plan().deliverable_profile_id)
                .map_err(|error| error.to_string())?;
            stage_original_mix_wav(&output_root, &mix, &stable_identity)
        } else {
            let source = service
                .verified_master_voice(id, &base, track)
                .map_err(sanitized)?;
            stage_measured_wav(&output_root, &source, &stable_identity)
        }
    })
    .await
    .map_err(|_| "Verified WAV preparation task failed.".to_string())??;
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "Canonical Semwright connection was rejected".to_string())?;
    let evidence = coordinator
        .assemble_mlt_av_master(
            request.project_id,
            &expected,
            MltAvMasterRequest {
                request_id: &canonical_request_id,
                deliverable_id: request.deliverable_id,
                motion: &motion,
                audio: &audio,
                sync: None,
            },
        )
        .await
        .map_err(|error| format!("Canonical AV mastering failed: {}", error.message))?;
    let token = state.av_delivery.register(&export_root, &evidence);
    Ok(NativeAvMasterResponse {
        evidence,
        export_token: token,
    })
}

/// Finalize the existing *owner-registered* multisegment native render via
/// the Semwright MLT semantic editor and curated AV mux. The WebView supplies
/// only expected project/profile IDs, one-time RenderLocal permission and a
/// prior opaque preview token, never media paths, render recipes or digests.
/// This is distinct from single-segment assemble_av_master above.
#[tauri::command]
async fn assemble_multisegment_av_master(
    state: State<'_, AppState>,
    request: AssembleAvMasterRequest,
) -> Result<NativeMultisegmentAvResponse, String> {
    let connection_path =
        selected_connection_path(&state, request.project_id)?.ok_or_else(|| {
            "Canonical Semwright multisegment mastering is not configured.".to_string()
        })?;
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Multisegment master input was invalidated by a creative revision.".into());
    }
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == request.deliverable_id)
        .ok_or_else(|| "Multisegment mastering requires a saved export profile.".to_string())?;
    if profile.voice_track_id != Some(request.voice_track_id) {
        return Err("Bind the selected measured voice to this exact saved profile.".into());
    }
    let (render, film_options, preview_root) = state.preview.multi_segment_source(
        &project,
        request.preview_token,
        request.deliverable_id,
    )?;
    // Bound overflows, empty sources and unverified speaker durations before
    // consuming a one-time effect grant.
    let total_frames = render.segments.iter().try_fold(0u64, |sum, segment| {
        sum.checked_add(segment.frame_count)
            .ok_or("Native multisegment total frame count overflow.")
    })?;
    if render.segments.len() < 2 || !(2..=36_000).contains(&total_frames) {
        return Err("Multisegment native master source frame count is unsupported.".into());
    }
    let selected_voice = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == request.voice_track_id)
        .ok_or_else(|| "Selected measured voice take is missing.".to_string())?;
    validate_master_voice_timing(
        selected_voice.measured_duration,
        total_frames,
        render.frame_rate.num,
        render.frame_rate.den,
    )?;
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "Canonical Semwright connection could not be loaded.".to_string())?;
    if connection.resource != project.resource_key() || connection.output_root != preview_root {
        return Err("Native multisegment source belongs to a different owner output root.".into());
    }
    // Only application-held Film authoring options and verified manifests are
    // used; do not derive any new render input from a WebView JSON object.
    let preflight_root = preview_root.clone();
    let preflight_project = project.clone();
    let preflight_render = render.clone();
    let preflight_options = film_options.clone();
    let profile_id = request.deliverable_id;
    tauri::async_runtime::spawn_blocking(move || {
        preflight_multi_segment_mlt(
            &preflight_project,
            profile_id,
            &preflight_options,
            &preflight_render,
            &preflight_root,
        )
        .map_err(|_| "Native multisegment source failed owner-bound preflight.".to_string())
    })
    .await
    .map_err(|_| "Multisegment source preflight task failed.".to_string())??;

    let saved_mix =
        original_mix_native::saved_mix_for_master(&state.service, &project, request.deliverable_id)
            .await?;

    // This is a mutating native production request. Authorization is a
    // scoped, short-lived, single-use desktop effect grant.
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        request.request_id.trim(),
    )?;
    let expected = RevisionStamp {
        resource: project.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    let mut source_digest = Sha256::new();
    source_digest.update(b"motionwright/multisegment-av-visual/v1");
    for segment in &render.segments {
        source_digest.update((segment.fingerprint.len() as u64).to_be_bytes());
        source_digest.update(segment.fingerprint.as_bytes());
        source_digest.update(segment.frame_count.to_be_bytes());
        source_digest.update((segment.scene_ids.len() as u64).to_be_bytes());
        for id in &segment.scene_ids {
            source_digest.update(id.as_bytes());
        }
    }
    let canonical = canonical_av_request_id(
        project.id,
        project.generation,
        project.revision,
        request.deliverable_id,
        &hex::encode(source_digest.finalize()),
        saved_mix
            .as_ref()
            .map_or(selected_voice.source_sha256.as_str(), |mix| {
                mix.audio_inputs_sha256()
            }),
    );
    // Source + speaker identity are 256-bit hashed into the stage name.
    // The native MLT subrequest is still short enough to accommodate typed
    // suffixes without violating the existing Broker request ID budget.
    let native_request_id = format!("mav-{}", &canonical[5..29]);
    let root = connection.output_root.clone();
    let service = state.service.clone();
    let voice_track = request.voice_track_id;
    let source = expected.clone();
    let id = project.id;
    let stable = canonical.clone();
    let staged = tauri::async_runtime::spawn_blocking(move || {
        if let Some(mix) = saved_mix {
            let latest = service.project(id).map_err(sanitized)?;
            mix.verify_project(&latest, mix.plan().deliverable_profile_id)
                .map_err(|error| error.to_string())?;
            stage_original_mix_wav(&root, &mix, &stable)
        } else {
            let original = service
                .verified_master_voice(id, &source, voice_track)
                .map_err(sanitized)?;
            stage_measured_wav(&root, &original, &stable)
        }
    })
    .await
    .map_err(|_| "Native multisegment source WAV staging failed.".to_string())??;

    let output_root = connection.output_root.clone();
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "Canonical Semwright Native SDK connection was rejected.".to_string())?;
    let visual = coordinator
        .assemble_native_mlt_video_only_timeline(
            project.id,
            &expected,
            &format!("{native_request_id}-visual"),
            request.deliverable_id,
            &film_options,
            &render,
        )
        .await
        .map_err(|error| {
            format!(
                "Canonical multisegment visual assembly failed: {}",
                error.message
            )
        })?;
    let master = coordinator
        .assemble_native_mlt_multisegment_av_master(
            project.id,
            &expected,
            MltMultisegmentAvMasterRequest {
                request_id: &native_request_id,
                deliverable_id: request.deliverable_id,
                options: &film_options,
                rendered: &render,
                visual: &visual,
                audio: &staged,
            },
        )
        .await
        .map_err(|error| {
            format!(
                "Canonical multisegment AV mastering failed: {}",
                error.message
            )
        })?;
    let export_token = state
        .av_delivery
        .register_multisegment(&output_root, &master)
        .ok_or_else(|| {
            "Original native master was retained but no safe revision-scoped review or delivery token could be issued."
                .to_string()
        })?;
    let master_sha256 = master
        .master
        .pointer("/artifact/sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| "Native multisegment MP4 lost its verified content digest.".to_string())?;
    let review_only = master.measured_voice.caption_cue_count.is_some();
    Ok(NativeMultisegmentAvResponse {
        project_resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
        deliverable_id: request.deliverable_id,
        frame_count: master.frame_count,
        video_segments: render.segments.len(),
        video_codec: "h264",
        audio_codec: "aac",
        audio_sample_rate: 48_000,
        audio_channels: 2,
        master_sha256: master_sha256.to_owned(),
        export_token,
        review_only,
        provider_project_cleanup: "not_requested_requires_foreground_broker_consent",
        evidence_scope: if review_only {
            "native-captioned-multisegment-av-review-only-not-decoded-pixel-approved"
        } else {
            "native-multisegment-av-verified-technical-output-not-human-approved"
        },
    })
}

/// Deliver a previous verified canonical master to an explicitly named
/// destination. The source is resolved from a scoped session handle rather
/// than any WebView-provided path, and files are created without overwrite.
#[tauri::command]
async fn export_native_av_master(
    state: State<'_, AppState>,
    request: MasterExportRequest,
) -> Result<MasterExportReceipt, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Native master export belongs to a previous creative revision.".into());
    }
    let source = state.av_delivery.resolve(&project, request.export_token)?;
    // Fail BEFORE using a one-time DeliverLocal permission. Synthetic CI
    // pixel evidence never qualifies an installed owner captioned master
    // for export, even with a valid review session token.
    if !source.export_allowed() {
        return Err("Captioned native AV master is review-only until decoded pixels are independently verified on this owner device.".into());
    }
    let scope = checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        scope,
        request.destination.trim(),
    )?;
    let destination = request.destination;
    let write_integrity = request.include_integrity_manifest;
    let receipt = tauri::async_runtime::spawn_blocking(move || {
        source.copy_with_integrity(&destination, write_integrity, SEMWRIGHT_REVISION)
    })
    .await
    .map_err(|_| "Native master export task failed.".to_string())??;
    let latest = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if latest.generation != request.generation || latest.revision != request.revision {
        // The new file exists and is digest-verified, but is no longer the
        // current project revision. Preserve it and mark historical in UI.
        return Ok(MasterExportReceipt {
            source_current: false,
            ..receipt
        });
    }
    Ok(receipt)
}

/// Verify a user-selected local MP4 and its unsigned portable JSON receipt.
/// Pure read-only content comparison: this grants no delivery or publisher authority.
#[tauri::command]
async fn verify_local_media_integrity(
    request: VerifyPortableMediaRequest,
) -> Result<PortableMediaVerification, String> {
    tauri::async_runtime::spawn_blocking(move || verify_portable_media(request))
        .await
        .map_err(|_| "Portable media verification task failed.".to_string())?
}

/// Read a completed, owner-verified native AV master, bounded to 16 MiB.
#[tauri::command]
async fn review_native_av_master(
    state: State<'_, AppState>,
    request: MasterReviewRequest,
) -> Result<tauri::ipc::Response, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Native AV preview belongs to a previous project revision.".into());
    }
    let source = state.av_delivery.resolve(&project, request.export_token)?;
    let data = tauri::async_runtime::spawn_blocking(move || source.review_bytes())
        .await
        .map_err(|_| "Native AV preview read task failed.".to_string())??;
    let latest = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if latest.generation != request.generation || latest.revision != request.revision {
        return Err("Project changed during native AV preview readback.".into());
    }
    Ok(tauri::ipc::Response::new(data))
}

#[tauri::command]
async fn preview_native_frame(
    state: State<'_, AppState>,
    request: NativeFrameRequest,
) -> Result<tauri::ipc::Response, String> {
    let before = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if before.generation != request.generation || before.revision != request.revision {
        return Err("Native preview belongs to a previous project revision.".into());
    }
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let registry = state.preview.clone();
    let bytes =
        tauri::async_runtime::spawn_blocking(move || registry.read_frame(&before, &request))
            .await
            .map_err(|_| "Native preview read task failed.".to_string())??;
    let after = state.service.project(project_id).map_err(sanitized)?;
    if after.generation != generation || after.revision != revision {
        return Err("Project changed while the native preview was read.".into());
    }
    Ok(tauri::ipc::Response::new(bytes))
}

fn file_import_error(error: file_import::FileImportError) -> String {
    match error {
        file_import::FileImportError::Invalid(message) => message,
        file_import::FileImportError::Storage(message) => sanitized(message),
    }
}

#[tauri::command]
async fn prepare_file_import(
    state: State<'_, AppState>,
    request: file_import::FileImportPrepareRequest,
) -> Result<file_import::PreparedFileImport, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        file_import::prepare_file_import(&service, request).map_err(file_import_error)
    })
    .await
    .map_err(|_| "Import preparation task failed.".to_string())?
}

#[tauri::command]
async fn import_asset_file(
    state: State<'_, AppState>,
    request: file_import::FileImportDispatchRequest,
) -> Result<file_import::FileImportReceipt, String> {
    let service = state.service.clone();
    // The clone shares the existing registry; it does not create new authority.
    let grants = state.effect_grants.clone();
    tauri::async_runtime::spawn_blocking(move || {
        file_import::import_asset_file(&service, &grants, request).map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "File import task failed. Check the original import result before importing again."
            .to_string()
    })?
}

#[tauri::command]
async fn import_voice_file(
    state: State<'_, AppState>,
    request: file_import::FileImportDispatchRequest,
) -> Result<file_import::FileImportReceipt, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    tauri::async_runtime::spawn_blocking(move || {
        file_import::import_voice_file(&service, &grants, request).map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "Voice import task failed. Check the original import result before importing again."
            .to_string()
    })?
}

#[tauri::command]
async fn recover_file_import(
    state: State<'_, AppState>,
    request: file_import::FileImportRecoveryRequest,
) -> Result<Option<file_import::FileImportReceipt>, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        file_import::recover_file_import(&service, request).map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "Import recovery read failed. The retained request is still available.".to_string()
    })?
}

#[tauri::command]
async fn waveform_page(
    state: State<'_, AppState>,
    request: WaveformPageRequest,
) -> Result<WaveformPage, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        service
            .waveform_page(
                request.project_id,
                request.track_id,
                request.page_index,
                request.page_size,
            )
            .map_err(sanitized)
    })
    .await
    .map_err(|_| "Motionwright waveform analysis task failed".to_string())?
}

#[tauri::command]
fn export_project_bundle(
    state: State<'_, AppState>,
    request: ExportBundleRequest,
) -> Result<BundleExportResponse, String> {
    project_export::export_project_bundle(&state.service, &state.effect_grants, request)
}

#[tauri::command]
fn export_caption_sidecar(
    state: State<'_, AppState>,
    request: ExportCaptionRequest,
) -> Result<CaptionExportResponse, String> {
    caption_export::export_caption_sidecar(&state.service, &state.effect_grants, request)
}

#[tauri::command]
fn export_otio(
    state: State<'_, AppState>,
    request: ExportOtioRequest,
) -> Result<OtioExportResponse, String> {
    project_export::export_otio(&state.service, &state.effect_grants, request)
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
    request: ImportBundleRequest,
) -> Result<Project, String> {
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::ImportLocal,
        EffectScope::unscoped_import(),
        request.path.trim(),
    )?;
    let source = absolute_bundle_path(&request.path)?;
    state
        .service
        .import_project_bundle(&source)
        .map_err(sanitized)
}

#[tauri::command]
async fn apply_change(
    state: State<'_, AppState>,
    request: ApplyRequest,
) -> Result<Project, String> {
    let scope = checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::ProjectEdit,
        scope,
        request.request_id.trim(),
    )?;
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

    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let current = project_library::open_project(
            &service,
            project_library::OpenProjectRequest {
                project_id: request.project_id,
            },
        )
        .map_err(sanitized)?;
        let expected = RevisionStamp {
            resource: current.resource_key(),
            generation: request.generation,
            revision: request.revision,
        };
        service
            .apply(
                request.project_id,
                &expected,
                &request.request_id,
                &request.change,
            )
            .map(|outcome| outcome.project)
            .map_err(sanitized)
    })
    .await
    .map_err(|_| "Motionwright project mutation task failed".to_string())?
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|error| format!("app data path unavailable: {error}"))?;
            std::fs::create_dir_all(&data_dir)?;
            let service = StudioService::open(data_dir.join("motionwright.sqlite3"))
                .map_err(|error| format!("local project store unavailable: {error}"))?;
            // Register the existing store even when no project is selected or
            // its latest document is unreadable. The library reads its index;
            // opening or creating a project is a separate explicit operation.

            // Build the public Native SDK cooperation surface during application setup.
            // Driver Host runs the same surface through the standalone provider executable.
            build_application(service.clone())
                .map_err(|error| format!("Native SDK contract invalid: {error}"))?;

            app.manage(AppState {
                service,
                workspace_session: Uuid::now_v7(),
                effect_grants: EffectGrantRegistry::default(),
                production_receipt_custody: ProductionReceiptCustody::default(),
                preview: NativePreviewRegistry::default(),
                av_delivery: NativeMasterDeliveryRegistry::default(),
                runtime_connections: RuntimeConnectionRegistry::new(&data_dir)?,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            bootstrap,
            list_projects,
            open_project,
            project_projection_head,
            project_scene_page,
            project_scene_nodes,
            semantic_graph_authoring_context,
            issue_project_creation_grant,
            create_project,
            recover_project_creation,
            issue_effect_grant,
            project_history,
            project_history_recent,
            asset_integrity_page,
            model_request_preflight,
            production_runtime_status,
            creative_runtime_doctor,
            choose_semwright_connection,
            clear_saved_semwright_connection,
            workflow_overview,
            workflow_action,
            production_jobs,
            mlt_cleanup_inventory,
            read_original_native_job_status,
            production_jobs_history,
            motion_canvas_preflight,
            render_motion_canvas,
            preflight_multi_segment_mlt_readiness,
            assemble_av_master,
            assemble_multisegment_av_master,
            export_native_av_master,
            verify_local_media_integrity,
            review_native_av_master,
            preview_native_frame,
            prepare_file_import,
            recover_file_import,
            import_asset_file,
            import_voice_file,
            waveform_page,
            export_project_bundle,
            export_caption_sidecar,
            export_otio,
            inspect_project_bundle,
            import_project_bundle,
            original_mix_native::native_original_mix_audition,
            narration_native::native_narration_take_snapshot,
            narration_native::native_narration_replacement_impact,
            generation_journal::native_paid_generation_journal,
            generation_journal::native_paid_generation_preflight,
            generation_journal::native_paid_generation_reserve_preflight,
            apply_change
        ])
        .run(tauri::generate_context!())
        .expect("Motionwright desktop runtime failed");
}

#[cfg(test)]
mod v01_first_owner_runtime_probe_contracts {
    use super::*;

    #[test]
    fn scoped_runtime_probe_rejects_foreign_generation_revision_and_project() {
        let project = Project::new("First owner native render").unwrap();
        let mut request = ProductionRuntimeStatusRequest {
            project_id: project.id,
            expected_generation: Some(project.generation),
            expected_revision: Some(project.revision),
        };
        assert!(runtime_status_scope_matches(&project, &request));
        request.expected_generation = Some(Uuid::new_v4());
        assert!(!runtime_status_scope_matches(&project, &request));
        request.expected_generation = Some(project.generation);
        request.expected_revision = Some(project.revision + 1);
        assert!(!runtime_status_scope_matches(&project, &request));
        request.expected_revision = Some(project.revision);
        request.project_id = Uuid::new_v4();
        assert!(!runtime_status_scope_matches(&project, &request));
    }

    #[test]
    fn legacy_unscoped_status_requests_do_not_introduce_native_authority() {
        let project = Project::new("Read-only legacy runtime compatibility").unwrap();
        let request = ProductionRuntimeStatusRequest {
            project_id: project.id,
            expected_generation: None,
            expected_revision: None,
        };
        // Existing IPC-only status consumers remain read-only. The current
        // Studio always sends exact generation and revision.
        assert!(runtime_status_scope_matches(&project, &request));
        let invalid = serde_json::json!({
            "project_id": project.id,
            "expected_generation": project.generation,
            "expected_revision": project.revision,
            "effect_grant": "forged"
        });
        assert!(serde_json::from_value::<ProductionRuntimeStatusRequest>(invalid).is_err());
    }
}
