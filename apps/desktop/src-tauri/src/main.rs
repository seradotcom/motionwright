mod asset_preview;
mod asset_preview_protocol;
mod av_delivery;
mod av_master;
mod blender_lit;
mod blender_product;
mod blender_product_run;
mod caption_export;
mod capture_source_preview;
mod desktop_owner;
mod effect_authority;
mod effect_grants;
mod file_import;
mod generation_journal;
mod hyperframes_render;
mod hyperframes_source;
mod local_error;
mod manim_editable_source;
mod media_integrity;
mod narration_native;
mod original_mix_native;
mod otio_bridge;
mod project_export;
mod project_library;
mod project_projection;
mod runtime_doctor;
mod runtime_setup;
mod semantic_graph_context_failure;
mod semantic_graph_custody;
mod semantic_graph_mutation_error;
mod semantic_graph_recovery;
mod workflow_native;

use av_delivery::{
    AudioQcUnavailableReason, MasterExportReceipt, MasterExportRequest, MasterReviewRequest,
    NativeAvAudioQcResponse, NativeMasterDeliveryRegistry, audio_targets_requested,
    require_audio_qc_host,
};
use media_integrity::{
    PortableMediaVerification, VerifyPortableMediaRequest, verify_portable_media,
};
mod native_hybrid;
mod native_preview;
#[cfg(any(
    all(target_os = "linux", target_arch = "x86_64"),
    all(target_os = "macos", target_arch = "aarch64")
))]
mod runtime_profile_bundle;
#[cfg(any(
    all(target_os = "linux", target_arch = "x86_64"),
    all(target_os = "macos", target_arch = "aarch64")
))]
mod runtime_profile_files;
#[cfg(any(
    all(target_os = "linux", target_arch = "x86_64"),
    all(target_os = "macos", target_arch = "aarch64")
))]
mod runtime_profile_platform;
mod runtime_profiles;

use av_master::{
    canonical_av_request_id, stage_measured_wav, stage_original_mix_wav, stage_voice_cut_wav,
    validate_master_voice_timing,
};
use caption_export::{CaptionExportResponse, ExportCaptionRequest};

use effect_authority::IssueEffectGrantRequest;
use effect_grants::{EffectGrantReceipt, EffectGrantRegistry, EffectKind, EffectScope};
use local_error::{portable_bundle_error, portable_import_error, sanitized};
use motionwright_domain::{Change, Project, RevisionStamp};
use motionwright_native::{
    assembly::preflight_multi_segment_mlt,
    build_application,
    creative_doctor::{CliEvidence, require_doctor_scope},
    film::{FilmBuildOptions, MotionCanvasAudioCueAlignment, build_motion_canvas_segments},
    owner_reconciliation::{OwnerMotionStatusObservation, authorize_original_motion_status},
    production::{
        MltAvMasterEvidence, MltAvMasterRequest, MltCloseDisposition,
        MltMultisegmentAvMasterRequest, MotionCanvasRenderEvidence, MotionCanvasWindowRequest,
        MotionCanvasWindowSummary, NativeGraphRecipeClient, ProductionClient, ProductionConnection,
        ProductionCoordinator, ProductionFontInvocationKind, ProductionFontStagingCustody,
        ProductionFontStagingOwner, ProductionFontStagingScope, ProductionReceiptCustody,
    },
};
use motionwright_service::{
    AssetIntegrityPage, BundleFingerprint, MltCleanupInventory, ModelRequestDraft,
    ModelRequestPreflight, ProductionJobProjection, ProjectCreationIntent, ProjectCreationOutcome,
    ProjectEvent, StudioService, WaveformPage,
};
use native_preview::{NativeFrameGrant, NativeFrameRequest, NativePreviewRegistry};
use project_export::{
    BundleExportResponse, ExportBundleRequest, ExportOtioRequest, ExportOtioSourceSnapshotRequest,
    NativeSourceExportRequest, NativeSourceExportResponse, OtioExportResponse,
    OtioSourceSnapshotResponse, absolute_bundle_path,
};
use project_library::{BootstrapProjectSelection, BootstrapRequest};
use runtime_setup::RuntimeConnectionRegistry;
use semantic_graph_context_failure::{GraphContextFailure, GraphContextStage};
use semantic_graph_custody::{GraphExchangeJournal, GraphExchangeRetention};
use semantic_graph_mutation_error::GraphInvokeFailure;
use semantic_graph_recovery::{GraphRecoveryRequest, GraphRecoveryResponse};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use tauri::{Manager, State};
use tauri_plugin_dialog::DialogExt;
use uuid::Uuid;
use workflow_native::{WorkflowActionFailure, WorkflowActionRequest, WorkflowActionResponse};

const SEMWRIGHT_REVISION: &str = "00ef6d6bb7d842827e8a5dcf00a417199aa72188";
const SEMWRIGHT_VERSION: &str = "1.0.0";

#[derive(Clone)]
struct AppState {
    service: StudioService,
    /// This non-nil identity and Store are fixed for this AppState's lifetime.
    /// Creation grants bind the actual invoking window label in this session.
    workspace_session: Uuid,
    effect_grants: EffectGrantRegistry,
    production_receipt_custody: ProductionReceiptCustody,
    production_font_staging_custody: ProductionFontStagingCustody,
    preview: NativePreviewRegistry,
    av_delivery: NativeMasterDeliveryRegistry,
    av_review: av_delivery::streaming::ReviewStreams,
    asset_previews: asset_preview::AssetPreviews,
    runtime_connections: RuntimeConnectionRegistry,
    runtime_profiles: runtime_profiles::RuntimeProfiles,
    native_renders: hyperframes_render::RenderRegistry,
    blender_cycle_plans: blender_product_run::CyclePlans,
    blender_lit_plans: blender_lit::LitPlans,
    graph_exchange_custody: GraphExchangeJournal,
    development_audition_custody: motionwright_native::original_mix::DevelopmentAuditionCustody,
    semantic_sfx_audition:
        std::sync::Arc<std::sync::Mutex<motionwright_native::sfx_mix::SemanticSfxAuditionCache>>,
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
    audio_qc: NativeAvAudioQcResponse,
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
    audio_qc: NativeAvAudioQcResponse,
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
    #[serde(skip_serializing_if = "Vec::is_empty")]
    audio_cue_alignment: Vec<MotionCanvasAudioCueAlignment>,
    #[serde(skip_serializing_if = "Option::is_none")]
    film_sha256: Option<String>,
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MotionCanvasWindowRenderRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    request_id: String,
    effect_grant: Uuid,
    window: MotionCanvasWindowRequest,
    /// Original session handle, never raw cached provenance or a file path.
    reuse_token: Option<Uuid>,
    /// The capture revision asserted by an explicit carry after an edit.
    carry_original_revision: Option<u64>,
}

#[derive(Debug, Serialize)]
struct NativeWindowRenderResponse {
    window: MotionCanvasWindowSummary,
    preview: NativeFrameGrant,
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
    fingerprint: BundleFingerprint,
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
    fingerprint: BundleFingerprint,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    migration_notes: Vec<String>,
}

#[tauri::command]
async fn bootstrap(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: Option<BootstrapRequest>,
) -> Result<BootstrapResponse, String> {
    desktop_owner::record_windows_startup("bootstrap-request-received");
    let service = state.service.clone();
    // The saved selection only identifies the project to read.
    let (project, project_selection) =
        desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            project_library::bootstrap_project(&service, request.unwrap_or_default())
        })
        .await
        .map_err(|error| error.to_string())?;
    desktop_owner::record_windows_startup("bootstrap-store-read-complete");
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
async fn list_projects(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: project_library::ListProjectsRequest,
) -> Result<project_library::ProjectLibraryPage, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_library::list_projects(&service, request)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn open_project(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: project_library::OpenProjectRequest,
) -> Result<Project, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_library::open_project(&service, request)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn project_projection_head(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: project_projection::Head,
) -> Result<motionwright_service::ProjectionHead, motionwright_service::ProjectionFailure> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_projection::head(&service, request)
    })
    .await
    .map_err(|_| motionwright_service::ProjectionFailure::Unavailable)?
}

#[tauri::command]
async fn project_scene_page(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: project_projection::Scenes,
) -> Result<motionwright_service::ScenePage, motionwright_service::ProjectionFailure> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_projection::scenes(&service, request)
    })
    .await
    .map_err(|_| motionwright_service::ProjectionFailure::Unavailable)?
}

#[tauri::command]
async fn semantic_graph_authoring_context(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: motionwright_service::SemanticGraphContextRequest,
) -> Result<motionwright_domain::GraphAuthoringContext, Value> {
    // The installed app must preserve the same closed error ABI as the
    // source-bound JSONL Native callback. A rejection is diagnostic-only:
    // it cannot grant Save/ProjectEdit authority or reveal raw SDK details.
    let service = state.service.clone();
    let connections = state.runtime_connections.clone();
    let source = request.source_snapshot.clone();
    let client = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        graph_client_for_source(&service, &connections, &source, true)
    })
    .await
    .map_err(|_| GraphContextFailure::without_code(GraphContextStage::Local))?
    .map_err(|_| GraphContextFailure::without_code(GraphContextStage::Binding))?;

    owner
        .begin_phase()
        .map_err(|_| GraphContextFailure::without_code(GraphContextStage::Admission))?;
    let attempt = state
        .graph_exchange_custody
        .begin("context", None)
        .map_err(|_| GraphContextFailure::without_code(GraphContextStage::Custody))?;
    let client = client.with_transport_custody(attempt.custody());
    let result = client.authoring_context(&request).await;
    // Snapshot the finished transport BEFORE retain_result consumes the guard.
    // Classification remains private until the actual result is durable.
    let transport_failed = attempt.custody().has_unconfirmed_or_failed_invocation();
    // The actual private result must be retained before public classification.
    // A custody failure overrides any Native outcome, just as in the JSONL path.
    if !matches!(
        attempt.retain_result(&result),
        GraphExchangeRetention::Published
    ) {
        return Err(GraphContextFailure::without_code(
            GraphContextStage::Custody,
        ));
    }
    let report = result
        .map_err(|error| GraphContextFailure::from_report(&error, false, transport_failed))?;
    let original_native = report.original_native_error().is_some();
    let context = report
        .outcome()
        .map_err(|error| {
            GraphContextFailure::from_report(error, original_native, transport_failed)
        })?
        .clone();
    GraphContextFailure::require_confirmed_transport(transport_failed)?;

    // A completed read supplies no later phase or write authority. Re-admit
    // the owner and check the original snapshot before exposing this context.
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let current = project_library::open_project(
            &service,
            project_library::OpenProjectRequest {
                project_id: request.source_snapshot.project_id,
            },
        )?;
        request
            .source_snapshot
            .require_current(&current)
            .map_err(sanitized)?;
        Ok::<_, String>(context)
    })
    .await
    .map_err(|_| GraphContextFailure::without_code(GraphContextStage::Local))?
    .map_err(|_| GraphContextFailure::without_code(GraphContextStage::Local))
}

/// Resolve a connection provisioned or configured by the trusted backend and
/// bind the actual held Store.
/// Historical writes/recovery retain their original revision and branch; a
/// context read additionally requires that exact source to remain current.
fn graph_client_for_source(
    service: &StudioService,
    connections: &RuntimeConnectionRegistry,
    source: &motionwright_domain::GraphSourceSnapshot,
    require_current: bool,
) -> Result<NativeGraphRecipeClient, String> {
    let project = project_library::open_project(
        service,
        project_library::OpenProjectRequest {
            project_id: source.project_id,
        },
    )?;
    if project.generation != source.generation {
        return Err("Graph source belongs to another project generation".into());
    }
    if require_current {
        source.require_current(&project).map_err(sanitized)?;
    }
    let path = connections
        .resolve(project.id, project.generation)?
        .ok_or_else(|| "Select an owner-provisioned native Graph connection".to_owned())?;
    let connection = ProductionConnection::load(&path)
        .map_err(|_| "Native Graph connection is unavailable".to_owned())?;
    NativeGraphRecipeClient::new(connection, service)
        .map_err(|_| "Native Graph connection is unavailable".to_owned())
}

/// Read the original operation through the same authenticated connection and
/// physical Store. This command consumes no edit grant and performs no retry.
#[tauri::command]
async fn semantic_graph_operation_recovery(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: GraphRecoveryRequest,
) -> Result<GraphRecoveryResponse, String> {
    let prepared = request.prepare().map_err(|error| error.to_string())?;
    let source = prepared.source_snapshot().clone();
    let service = state.service.clone();
    let connections = state.runtime_connections.clone();
    let client = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        graph_client_for_source(&service, &connections, &source, false)
    })
    .await
    .map_err(|_| "Graph recovery read is unavailable".to_owned())??;

    owner.begin_phase()?;
    // Resolve publication of retained originals only. This performs no Native
    // call and cannot replay the original mutation; recovery stays read-only.
    state.graph_exchange_custody.publish_retained()?;
    let attempt = state
        .graph_exchange_custody
        .begin("recovery", Some(prepared.request_id()))?;
    let client = client.with_transport_custody(attempt.custody());
    let result = client.operation_recovery(prepared.identity()).await;
    let transport_failed = attempt.custody().has_unconfirmed_or_failed_invocation();
    if !matches!(
        attempt.retain_result(&result),
        GraphExchangeRetention::Published
    ) {
        return Err("Graph recovery read could not retain its original evidence".into());
    }
    // Neither a returned historical receipt nor a local read error can
    // resolve the original mutation while this read transport is uncertain.
    semantic_graph_recovery::require_confirmed_recovery_transport(transport_failed)
        .map_err(|error| error.to_string())?;
    let report = result.map_err(|_| "Graph recovery read is unavailable".to_owned())?;
    // Read errors never classify the original mutation. Only the closed
    // historical receipt decoder can reconcile a Recorded response.
    prepared
        .decode_read(report.outcome())
        .map_err(|error| error.to_string())
}

/// Source-bound canonical inspection only; no grant, write or renderer authority.
#[tauri::command]
async fn preview_branch_combination(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: motionwright_service::BranchCombinationPreviewRequest,
) -> Result<motionwright_domain::CreativePatchPreview, String> {
    let service = state.service.clone();
    let preview = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .preview_branch_combination(&request)
            .map_err(sanitized)
    })
    .await
    .map_err(|_| "Branch combination inspection task failed.".to_owned())??;
    owner.begin_phase()?;
    Ok(preview)
}

/// Read-only authoring source from the canonical Store, without execution authority.
#[tauri::command]
async fn scene_boundary_authoring_context(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: motionwright_service::SceneBoundaryContextRequest,
) -> Result<motionwright_service::SceneBoundaryAuthoringContext, String> {
    let service = state.service.clone();
    let context = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .scene_boundary_authoring_context(&request)
            .map_err(sanitized)
    })
    .await
    .map_err(|_| "Scene boundary source inspection task failed.".to_owned())??;
    owner.begin_phase()?;
    Ok(context)
}

#[tauri::command]
async fn project_scene_nodes(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: project_projection::Nodes,
) -> Result<motionwright_service::NodePage, motionwright_service::ProjectionFailure> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_projection::nodes(&service, request)
    })
    .await
    .map_err(|_| motionwright_service::ProjectionFailure::Unavailable)?
}

#[tauri::command]
fn issue_project_creation_grant(
    _owner: desktop_owner::OwnerInvocation,
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
async fn create_project(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    window: tauri::WebviewWindow,
    request: project_library::CreateProjectRequest,
) -> Result<ProjectCreationOutcome, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    let session = state.workspace_session;
    let window_label = window.label().to_owned();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_library::create_project(&service, &grants, session, &window_label, request)
            .map_err(sanitized)
    })
    .await
    .map_err(|error| match error {
        desktop_owner::OwnerTaskFailure::OwnerUnavailable => error.to_string(),
        desktop_owner::OwnerTaskFailure::WorkerJoinFailure => {
            "The project creation outcome is unknown. Check the original creation.".to_string()
        }
    })?
}

#[tauri::command]
async fn recover_project_creation(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ProjectCreationIntent,
) -> Result<Option<ProjectCreationOutcome>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_library::recover_project_creation(&service, &request).map_err(sanitized)
    })
    .await
    .map_err(|error| error.to_string())?
}

fn checked_effect_scope(
    state: &AppState,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
) -> Result<EffectScope, String> {
    effect_authority::checked_effect_scope(&state.service, project_id, generation, revision)
}

#[tauri::command]
async fn issue_effect_grant(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: IssueEffectGrantRequest,
) -> Result<EffectGrantReceipt, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        effect_authority::issue_effect_grant(&service, &grants, request)
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Explicit, read-only asset SHA-256 audit. No path/digest sources or broad
/// filesystem plugin inputs from WebView; verifies app-owned CAS bytes only.
#[tauri::command]
async fn asset_integrity_page(
    mut owner: desktop_owner::OwnerInvocation,
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
    let report = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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
    owner.begin_phase()?;
    let after = state.service.project(project_id).map_err(sanitized)?;
    if after.generation != generation || after.revision != revision {
        return Err("Project changed during its asset integrity inspection.".into());
    }
    Ok(report)
}

#[tauri::command]
async fn project_history(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: HistoryRequest,
) -> Result<Vec<ProjectEvent>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .history(request.project_id, request.after_revision, request.limit)
            .map_err(sanitized)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn project_history_recent(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: RecentHistoryRequest,
) -> Result<Vec<ProjectEvent>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .history_recent(request.project_id, request.through_revision, request.limit)
            .map_err(sanitized)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn model_request_preflight(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ModelRequestPreflightRequest,
) -> Result<ModelRequestPreflight, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .model_request_preflight(request.project_id, &request.draft)
            .map_err(sanitized)
    })
    .await
    .map_err(|error| error.to_string())?
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

#[tauri::command]
async fn review_signed_observer_evidence(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<Option<motionwright_service::ObserverReview>, String> {
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let config = app
        .path()
        .app_data_dir()
        .map_err(|_| "Motionwright app data is unavailable.")?
        .join("observer-policy");
    if !config.is_dir() {
        return Err(
            "Provision the owner observer policy in the app data folder before reviewing evidence."
                .into(),
        );
    }
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        app.dialog()
            .file()
            .set_title("Choose a signed frame evidence folder")
            .blocking_pick_folder()
    })
    .await
    .map_err(|_| "The native evidence folder picker failed.".to_owned())?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    owner.begin_phase()?;
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let evidence = selected
        .into_path()
        .map_err(|_| "Choose a local evidence folder.")?;
    let service = state.service.clone();
    let project = service.project(request.project_id).map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err(
            "The saved project changed. Open the current revision to review evidence.".into(),
        );
    }
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service.review_observer_evidence(project.id, &project.stamp(), &config, &evidence)
    })
    .await
    .map_err(|_| "The evidence inspection did not complete.".to_owned())?
    .map(Some)
    .map_err(|error| {
        if let motionwright_service::StorageError::InvalidBackup(message) = &error
            && let Some(reason) = message.strip_prefix("observer evidence: ")
        {
            return reason.to_owned();
        }
        "The evidence or the local owner policy could not be verified.".into()
    })
}

/// The OS native file dialog is the trusted owner gesture. JavaScript never
/// sees the selected path, connection JSON, private session ticket or Broker
/// authentication. The canonical Semwright client remains responsible for
/// digest/protocol checks; this does not install or bootstrap a provider.
#[tauri::command]
async fn choose_semwright_connection(
    mut owner: desktop_owner::OwnerInvocation,
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
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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
    owner.begin_phase()?;
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
    owner.begin_phase()?;
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

/// The existing native picker supplies the owner gesture for local no-replace
/// font staging. JavaScript supplies only project scope, never a filesystem path.
/// This does not create a Host mount or replace the provider's MediaRO admission.
#[tauri::command]
async fn choose_brand_font_media_root(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<bool, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Project changed; refresh before selecting a font staging folder.".into());
    }
    let path = selected_connection_path(&state, project.id)?.ok_or_else(|| {
        "Select a Semwright connection before its font staging folder.".to_owned()
    })?;
    let connection = ProductionConnection::load(&path)
        .map_err(|_| "Selected Semwright connection failed local validation".to_owned())?;
    if connection.resource != project.resource_key() {
        return Err("Selected Semwright connection belongs to another project resource.".into());
    }
    let handle = app.clone();
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        handle
            .dialog()
            .file()
            .set_title("Choose the configured Motion Canvas media folder for local font staging")
            .blocking_pick_folder()
    })
    .await
    .map_err(|_| "Native font staging folder picker failed".to_owned())?;
    let Some(selected) = selected else {
        return Ok(false);
    };
    let media_root = selected
        .into_path()
        .map_err(|_| "The selected font staging folder is not local".to_owned())?;
    owner.begin_phase()?;
    let current = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if current.generation != project.generation || current.revision != project.revision {
        return Err("Project changed during folder selection; no declaration was stored.".into());
    }
    state.runtime_connections.remember_font_staging(
        project.id,
        project.generation,
        &path,
        &connection,
        &media_root,
    )?;
    // Use the actual resolver immediately as a readback. A later render must
    // resolve again against its own current connection and fresh effect grant.
    let resolved: runtime_setup::ResolvedFontStaging = state
        .runtime_connections
        .resolve_font_staging(project.id, project.generation, &connection)?
        .ok_or_else(|| "Owner font staging declaration was not retained".to_owned())?;
    resolved.revalidate(&state.runtime_connections, &connection)?;
    Ok(true)
}

/// A local revocation removes only Motionwright's saved path reference.
/// It cannot cancel running native jobs, remove the owner's Semwright ticket,
/// disable a separately configured environment override, or delete artifacts.
#[tauri::command]
async fn clear_saved_semwright_connection(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<bool, String> {
    let service = state.service.clone();
    let connections = state.runtime_connections.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let project = service.project(request.project_id).map_err(sanitized)?;
        if project.generation != request.generation || project.revision != request.revision {
            return Err("Project changed; refresh before clearing a runtime selection.".into());
        }
        connections.clear_saved(project.id)
    })
    .await
    .map_err(|error| match error {
        desktop_owner::OwnerTaskFailure::OwnerUnavailable => error.to_string(),
        desktop_owner::OwnerTaskFailure::WorkerJoinFailure => {
            "The runtime selection change could not be confirmed. Refresh Integrations.".to_string()
        }
    })?
}

#[tauri::command]
async fn production_runtime_status(
    mut owner: desktop_owner::OwnerInvocation,
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
    owner.begin_phase()?;
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
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<runtime_doctor::RuntimeDoctorReport, String> {
    let service = state.service.clone();
    let runtime_connections = state.runtime_connections.clone();
    let initial = service.project(request.project_id).map_err(sanitized)?;
    require_doctor_scope(&initial, request.generation, request.revision).map_err(str::to_owned)?;
    let expected = initial.stamp();
    owner.finish_phase();
    let status = production_runtime_status(
        owner.renew()?,
        state,
        ProductionRuntimeStatusRequest {
            project_id: request.project_id,
            expected_generation: Some(request.generation),
            expected_revision: Some(request.revision),
        },
    )
    .await?;
    owner.begin_phase()?;
    let current = service.project(request.project_id).map_err(sanitized)?;
    require_doctor_scope(&current, request.generation, request.revision).map_err(str::to_owned)?;
    build_application(service.clone())
        .map_err(|_| "Native SDK creative contract validation failed".to_string())?;
    let owner_lifetime = owner.worker_owner();
    runtime_doctor::inspect(
        &owner_lifetime,
        &service,
        &runtime_connections,
        runtime_doctor::DoctorRequest {
            project_id: request.project_id,
            expected: &expected,
            source_pin: SEMWRIGHT_REVISION,
            cli: CliEvidence::from_runtime_status(status.status),
            connection_identity: status.connection_identity.as_deref(),
            executable_sha256: status.executable_sha256.as_deref(),
        },
    )
    .await
}

#[tauri::command]
async fn workflow_overview(
    mut owner: desktop_owner::OwnerInvocation,
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
    owner.begin_phase()?;
    let candidates = client
        .execute("workflow.candidates.list", serde_json::json!({}), false)
        .await
        .map_err(|_| "Canonical Semwright workflow query failed".to_string())?
        .value
        .get("data")
        .cloned()
        .unwrap_or(Value::Null);
    owner.begin_phase()?;
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
    owner.begin_phase()?;
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
    owner.begin_phase()?;
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
    owner.begin_phase()?;
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
async fn asset_preview_open(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: asset_preview::OpenRequest,
) -> Result<asset_preview::Descriptor, String> {
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let service = state.service.clone();
    let previews = state.asset_previews.clone();
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Original media read capacity is busy; try after current readers finish.")?;
    let descriptor = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        previews.prepare(&service, &request)
    })
    .await
    .map_err(|_| "Original media preparation failed.".to_owned())??;
    if let Err(error) = owner.begin_phase() {
        state.asset_previews.close(descriptor.stream_token);
        return Err(error);
    }
    let current = state.service.project(project_id);
    if !current
        .is_ok_and(|project| project.generation == generation && project.revision == revision)
    {
        state.asset_previews.close(descriptor.stream_token);
        return Err("Project changed during original media preparation.".into());
    }
    Ok(descriptor)
}

#[tauri::command]
fn asset_preview_close(
    _owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: asset_preview::CloseRequest,
) {
    state.asset_previews.close(request.stream_token);
}

/// Bounded immutable PNG source read on a worker, with no Native render call.
#[tauri::command]
async fn registered_capture_png(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: capture_source_preview::CapturePngRequest,
) -> Result<tauri::ipc::Response, String> {
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let service = state.service.clone();
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Original source read capacity is busy; try after current readers finish.")?;
    let bytes = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        capture_source_preview::read(&service, &request)
    })
    .await
    .map_err(|_| "Original capture read task failed.".to_owned())??;
    owner.begin_phase()?;
    let current = state.service.project(project_id).map_err(sanitized)?;
    if current.generation != generation || current.revision != revision {
        return Err("Project changed during the original capture read.".into());
    }
    Ok(tauri::ipc::Response::new(bytes))
}

/// Read a completed original workflow response; never invokes or retries Core.
#[tauri::command]
async fn workflow_stored_completion(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: workflow_native::StoredWorkflowRequest,
) -> Result<Option<WorkflowActionResponse>, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    let selected = selected_connection_path(&state, project.id)?
        .ok_or("Choose the original owner connection before reading this workflow result.")?;
    let expected_path = selected.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let native_owner = owner.worker_owner();
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Original result read capacity is busy; try after current readers finish.")?;
    let (response, identity) = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        workflow_native::read_stored_workflow_completion(
            &|| native_owner.phase(),
            &service,
            &custody,
            selected,
            request,
        )
    })
    .await
    .map_err(|_| "Original workflow read task failed.".to_owned())??;
    owner.begin_phase()?;
    let latest = state.service.project(project.id).map_err(sanitized)?;
    if latest.generation != project.generation
        || latest.revision != project.revision
        || selected_connection_path(&state, project.id)?.as_ref() != Some(&expected_path)
        || ProductionConnection::load(&expected_path)
            .and_then(|c| c.identity())
            .ok()
            .as_deref()
            != Some(identity.as_str())
    {
        return Err("Selected project or connection changed during workflow readback.".into());
    }
    Ok(response)
}

#[tauri::command]
async fn workflow_action(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: WorkflowActionRequest,
) -> Result<WorkflowActionResponse, WorkflowActionFailure> {
    let selected = selected_connection_path(&state, request.project_id);
    let owner_lifetime = owner.worker_owner();
    workflow_native::execute_workflow_action(
        &|| owner_lifetime.phase(),
        &state.service,
        &state.effect_grants,
        &state.production_receipt_custody,
        selected,
        request,
    )
    .await
}

#[tauri::command]
async fn production_jobs(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ProductionJobsRequest,
) -> Result<Vec<ProductionJobProjection>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .production_jobs(request.project_id, request.limit)
            .map_err(sanitized)
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Read-only inventory of native MLT project lifetime receipts.
#[tauri::command]
async fn mlt_cleanup_inventory(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<MltCleanupInventory, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CloseMltProjectRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    root_request_id: String,
    effect_grant: Uuid,
}

#[tauri::command]
async fn close_original_mlt_project(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: CloseMltProjectRequest,
) -> Result<MltCloseDisposition, String> {
    let project_id = request.project_id;
    let generation = request.generation;
    let revision = request.revision;
    let selected = request.root_request_id;
    let selector = selected.clone();
    let service = state.service.clone();
    let path = selected_connection_path(&state, project_id)?
        .ok_or_else(|| "Select the original Semwright production connection first.".to_owned())?;
    let original_path = path.clone();
    let (coordinator, prepared) =
        desktop_owner::spawn_blocking(owner.worker_owner(), move || -> Result<_, String> {
            let connection = ProductionConnection::load(&path).map_err(sanitized)?;
            let coordinator = ProductionCoordinator::new(service, connection).map_err(sanitized)?;
            let stamp = RevisionStamp {
                resource: format!("project:{project_id}"),
                generation,
                revision,
            };
            let prepared = coordinator
                .prepare_mlt_close(project_id, &stamp, &selector)
                .map_err(sanitized)?;
            Ok((coordinator, prepared))
        })
        .await
        .map_err(|_| "The original MLT job could not be read.".to_owned())??;

    owner.begin_phase()?;
    if selected_connection_path(&state, project_id)?.as_ref() != Some(&original_path) {
        return Err("The selected production connection changed. Reopen Jobs.".into());
    }
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::CloseMltProject,
        EffectScope::project(project_id, generation, revision),
        &selected,
    )?;
    coordinator
        .close_prepared_mlt(prepared)
        .await
        .map_err(sanitized)
}

/// A single explicit owner-initiated read-only status observation. The WebView
/// supplies only an opaque selector, never a Semwright command, connection path,
/// provider permission or a new job identity. No render.start or result/verify.
#[tauri::command]
async fn read_original_native_job_status(
    mut owner: desktop_owner::OwnerInvocation,
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
        desktop_owner::spawn_blocking(owner.worker_owner(), move || -> Result<_, String> {
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
                .original_production_receipt_pair(project_id, &job.root_request_id)
                .map_err(sanitized)?;
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
    owner.begin_phase()?;
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
        .observe_original_motion_status(&origin)
        .await
        .map_err(sanitized)?;

    owner.begin_phase()?;
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
    Ok(status)
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
    mut owner: desktop_owner::OwnerInvocation,
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
    let page = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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
    owner.begin_phase()?;
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
async fn native_blender_lit_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_lit::SourceScope,
) -> Result<blender_lit::SourceView, String> {
    let service = state.service.clone();
    let scope = request.clone();
    let view = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        blender_lit::inspect(&service, &scope)
    })
    .await
    .map_err(|_| "Blender source inspection did not complete.")??;
    owner.begin_phase()?;
    blender_lit::project(&state.service, &request)?;
    Ok(view)
}

#[tauri::command]
async fn prepare_blender_lit_still(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: blender_lit::PrepareRequest,
) -> Result<Option<blender_lit::PlanView>, String> {
    let service = state.service.clone();
    let check = request.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        blender_lit::checked(&service, &check).map(|_| ())
    })
    .await
    .map_err(|_| "Blender source inspection did not complete.")??;
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        app.dialog()
            .file()
            .set_title("Blender: select the lit-still owner connection")
            .add_filter("JSON connection", &["json"])
            .blocking_pick_file()
    })
    .await
    .map_err(|_| "The native Blender connection picker did not complete.")?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    let path = selected
        .into_path()
        .map_err(|_| "Choose a local owner connection file.")?;
    let connection = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        ProductionConnection::load(path)
            .map_err(|_| "The selected Blender connection failed validation.")
    })
    .await
    .map_err(|_| "Blender connection validation did not complete.")??;
    owner.begin_phase()?;
    let client = ProductionClient::new(connection.clone())
        .map_err(|_| "The selected Blender connection was rejected.")?;
    let probe = client
        .probe_runtime(SEMWRIGHT_VERSION)
        .await
        .map_err(|_| "The selected Blender runtime is unavailable or changed.")?;
    if !probe.version_compatible {
        return Err("Blender runtime does not match the pinned Semwright version.".into());
    }
    let plans = state.blender_lit_plans.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let source = request.source.clone();
    let view = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        plans.prepare(&service, &custody, request, connection)
    })
    .await
    .map_err(|_| "Blender still preparation did not complete.")??;
    owner.begin_phase()?;
    blender_lit::project(&state.service, &source)?;
    Ok(Some(view))
}

#[tauri::command]
async fn start_blender_lit_still(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_lit::StartRequest,
) -> Result<blender_lit::AttemptView, String> {
    let _lease = state
        .native_renders
        .acquire(request.intent.source.project_id)?;
    let service = state.service.clone();
    let plans = state.blender_lit_plans.clone();
    let grants = state.effect_grants.clone();
    let custody = state.production_receipt_custody.clone();
    let admitted = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        plans.admit(&service, &custody, &grants, request)
    })
    .await
    .map_err(|_| "Blender admission is unconfirmed; retain its original request.")??;
    let admitted = std::sync::Arc::new(admitted);
    let original = admitted.clone();
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || original.reserve(&service))
        .await
        .map_err(|_| "Blender source reservation is unconfirmed; read the original attempt.")??;
    let mut journal = motionwright_native::production::BlenderLitStillJournal::default();
    let native_outcome = match owner.begin_phase().and_then(|()| admitted.check_roots()) {
        Ok(()) => {
            admitted
                .coordinator
                .realize_blender_lit_still(&admitted.request, &mut journal)
                .await
        }
        Err(_) => Err(semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::PermissionDenied,
            "Desktop owner or prepared output root retired before Blender dispatch",
        )),
    };
    // Each later CAS adoption has a fresh owner phase and the exact current
    // project, hash/size and original file-import receipt. No Native repeat.
    let adoption = async {
        let native = native_outcome
            .as_ref()
            .map_err(|_| "No successful original native still is available.".to_string())?;
        owner.begin_phase()?;
        let measured = admitted.measure(native)?;
        let original = admitted.clone();
        let service = state.service.clone();
        let image_measure = measured.clone();
        let image = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            original.import_image(&service, &image_measure)
        })
        .await
        .map_err(|_| "The original Blender image import is unconfirmed.".to_string())??;
        owner.begin_phase()?;
        let original = admitted.clone();
        let service = state.service.clone();
        desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            original.import_source(&service, &measured, &image)
        })
        .await
        .map_err(|_| "The original Blender editable-source import is unconfirmed.".to_string())?
    }
    .await;
    let service = state.service.clone();
    // Retaining an already observed terminal must survive owner close. This
    // phase writes only the original receipt; no provider, source import or
    // new authorization can follow it.
    tauri::async_runtime::spawn_blocking(move || {
        admitted.finish(&service, &native_outcome, &journal, adoption)
    })
    .await
    .map_err(|_| "The original Blender terminal is unconfirmed; read its retained attempt.".into())
}

#[tauri::command]
async fn read_original_blender_lit_still(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_lit::ReadRequest,
) -> Result<Option<blender_lit::AttemptView>, String> {
    let service = state.service.clone();
    let original = request.clone();
    let view = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        blender_lit::read_attempt(&service, &original)
    })
    .await
    .map_err(|_| "The original Blender attempt could not be read.")??;
    owner.begin_phase()?;
    blender_lit::project(&state.service, &request.source)?;
    Ok(view)
}

#[tauri::command]
async fn preview_blender_lit_still(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_lit::ReadRequest,
) -> Result<tauri::ipc::Response, String> {
    let service = state.service.clone();
    let original = request.clone();
    let registry = state.preview.clone();
    let bytes = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        registry.read_retained_png(|| blender_lit::read_png(&service, &original))
    })
    .await
    .map_err(|_| "The original Blender PNG could not be read.")??;
    owner.begin_phase()?;
    blender_lit::project(&state.service, &request.source)?;
    Ok(tauri::ipc::Response::new(bytes))
}

/// Prepare only: the native picker selects three existing owner connections.
/// IPC never supplies executable, session, socket or output-root paths.
#[tauri::command]
async fn prepare_blender_product_cycle(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: blender_product_run::PrepareRequest,
) -> Result<Option<blender_product_run::PlanView>, String> {
    let service = state.service.clone();
    let check = request.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        blender_product_run::checked_source(&service, &check).map(|_| ())
    })
    .await
    .map_err(|_| "Blender source preparation did not complete.".to_string())??;
    let mut connections = Vec::with_capacity(3);
    for title in [
        "Blender: select the Create original owner connection",
        "Blender: select the Edit original owner connection",
        "Blender: select the Verify edited owner connection",
    ] {
        let handle = app.clone();
        let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            handle
                .dialog()
                .file()
                .set_title(title)
                .add_filter("JSON connection", &["json"])
                .blocking_pick_file()
        })
        .await
        .map_err(|_| "The native Blender connection picker did not complete.".to_string())?;
        let Some(selected) = selected else {
            return Ok(None);
        };
        let path = selected
            .into_path()
            .map_err(|_| "Choose a local owner connection file.")?;
        let connection = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            ProductionConnection::load(path)
                .map_err(|_| "The selected Blender connection failed local validation.".to_string())
        })
        .await
        .map_err(|_| "Blender connection validation did not complete.".to_string())??;
        owner.begin_phase()?;
        let client = ProductionClient::new(connection.clone())
            .map_err(|_| "The selected Blender connection was rejected.")?;
        let probe = client
            .probe_runtime(SEMWRIGHT_VERSION)
            .await
            .map_err(|_| "The selected Blender runtime is unavailable or its digest changed.")?;
        if !probe.version_compatible {
            return Err(
                "The selected Blender runtime does not match the pinned Semwright version.".into(),
            );
        }
        connections.push(connection);
    }
    let connections: [ProductionConnection; 3] = connections
        .try_into()
        .map_err(|_| "Blender requires three original owner connections.")?;
    let plans = state.blender_cycle_plans.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let view = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        plans.prepare(&service, &custody, request, connections)
    })
    .await
    .map_err(|_| "Blender preparation could not be confirmed.".to_string())??;
    owner.begin_phase()?;
    let current = state
        .service
        .project(view.source.project_id)
        .map_err(sanitized)?;
    if current.generation != view.source.generation || current.revision != view.source.revision {
        return Err("The project changed during Blender preparation.".into());
    }
    Ok(Some(view))
}

struct BlenderCycleOwnerHook<'a> {
    owner: &'a mut desktop_owner::OwnerInvocation,
    admitted: &'a blender_product_run::AdmittedCycle,
}
impl motionwright_native::production::BlenderProductCycleHook for BlenderCycleOwnerHook<'_> {
    fn enter(
        &mut self,
        _phase: motionwright_native::production::BlenderOriginalPhase,
    ) -> semwright_native_sdk::Result<()> {
        self.owner.begin_phase().map_err(|_| {
            semwright_native_sdk::Error::new(
                semwright_native_sdk::ErrorCode::PermissionDenied,
                "Desktop owner retired before the next Blender phase",
            )
        })?;
        self.admitted.check_roots().map_err(|_| {
            semwright_native_sdk::Error::new(
                semwright_native_sdk::ErrorCode::StaleReference,
                "A retained Blender output root changed before its next phase",
            )
        })
    }
    fn leave(
        &mut self,
        phase: motionwright_native::production::BlenderOriginalPhase,
    ) -> semwright_native_sdk::Result<()> {
        // A native reply cannot authorize later source writes after owner close.
        // The coordinator retains that original reply and any leave failure.
        self.enter(phase)
    }
}

#[tauri::command]
async fn start_blender_product_cycle(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_product_run::StartRequest,
) -> Result<blender_product_run::CycleResponse, String> {
    let _lease = state.native_renders.acquire(request.source.project_id)?;
    let service = state.service.clone();
    let plans = state.blender_cycle_plans.clone();
    let grants = state.effect_grants.clone();
    let custody = state.production_receipt_custody.clone();
    let admitted = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        plans.admit(&service, &custody, &grants, request)
    })
    .await
    .map_err(|_| {
        "Blender admission could not be confirmed; retain the original request.".to_string()
    })??;
    owner.begin_phase()?;
    let mut journal = motionwright_native::production::BlenderProductCycleJournal::default();
    let mut hook = BlenderCycleOwnerHook {
        owner: &mut owner,
        admitted: &admitted,
    };
    let outcome = admitted.coordinators[0]
        .realize_blender_product_cycle_with_hook(
            &admitted.coordinators[1],
            &admitted.coordinators[2],
            &admitted.request,
            &mut journal,
            &mut hook,
        )
        .await;
    // No new phase or effect follows the existing coordinator's terminal.
    // Its bounded summary survives an error or a retired owner; query the
    // original native_blender_product_attempt after reloading current scope.
    Ok(admitted.response(&journal, outcome))
}

#[tauri::command]
async fn native_blender_product_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_product::BlenderProductReadRequest,
) -> Result<
    motionwright_native::production::BlenderProductSourceSnapshot,
    blender_product::BlenderProductReadFailure,
> {
    let service = state.service.clone();
    let snapshot = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        blender_product::read_source(&service, &request)
    })
    .await
    .map_err(|_| blender_product::read_task_failure())??;
    owner
        .begin_phase()
        .map_err(|_| blender_product::read_task_failure())?;
    blender_product::ensure_read_current(&state.service, &snapshot.project)?;
    Ok(snapshot)
}

#[tauri::command]
async fn native_blender_product_attempt(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: blender_product::BlenderProductAttemptReadRequest,
) -> Result<
    motionwright_native::production::BlenderProductAttemptProjection,
    blender_product::BlenderProductReadFailure,
> {
    let service = state.service.clone();
    let snapshot = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        blender_product::read_attempt(&service, &request)
    })
    .await
    .map_err(|_| blender_product::read_task_failure())??;
    owner
        .begin_phase()
        .map_err(|_| blender_product::read_task_failure())?;
    blender_product::ensure_read_current(&state.service, &snapshot.project)?;
    Ok(snapshot)
}

#[tauri::command]
async fn native_hybrid_preflight(
    owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: native_hybrid::HybridPreflightRequest,
) -> Result<motionwright_native::production::HybridSourcePreflight, native_hybrid::HybridFailure> {
    native_hybrid::preflight(owner, &state, request).await
}

#[tauri::command]
async fn render_native_hybrid_master(
    owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: native_hybrid::HybridRenderRequest,
) -> Result<native_hybrid::HybridRenderResponse, native_hybrid::HybridFailure> {
    native_hybrid::render(owner, &state, request).await
}

#[tauri::command]
async fn motion_canvas_preflight(
    mut owner: desktop_owner::OwnerInvocation,
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
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let projected =
            build_motion_canvas_segments(&project, request.deliverable_id, &request.options)
                .and_then(|segments| {
                    segments
                        .into_iter()
                        .map(|segment| {
                            let film_sha256 = (!segment.audio_cue_alignment.is_empty())
                                .then(|| segment.film_sha256())
                                .transpose()?;
                            Ok(FilmPreflightSegment {
                                segment_id: segment.id,
                                scene_ids: segment.scene_ids,
                                frame_count: segment.frame_count,
                                audio_cue_alignment: segment.audio_cue_alignment,
                                film_sha256,
                            })
                        })
                        .collect::<Result<Vec<_>, _>>()
                });
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
                        segments,
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
    owner.begin_phase()?;
    let latest = state.service.project(project_id).map_err(sanitized)?;
    if latest.generation != result.generation || latest.revision != result.revision {
        return Err(
            "Project changed during semantic Film preflight; retry at current revision.".into(),
        );
    }
    Ok(result)
}

/// Accept the current owner selection before any native future can run. The
/// resolved object is moved once; its PathBuf is only the independent MediaRO
/// provenance. Registry selection changes do not revoke an accepted custody.
fn motion_canvas_production_coordinator(
    state: &AppState,
    project_id: Uuid,
    expected: &RevisionStamp,
    request_id: &str,
    connection: ProductionConnection,
    kind: ProductionFontInvocationKind,
) -> Result<(ProductionCoordinator, Option<ProductionFontStagingOwner>), String> {
    let retained = state
        .runtime_connections
        .resolve_font_staging(project_id, expected.generation, &connection)?
        .map(|resolved| resolved.into_retained_root(&state.runtime_connections, &connection))
        .transpose()?;
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "Canonical Semwright connection was rejected".to_string())?;
    if let Some((path, root)) = retained {
        let scope = ProductionFontStagingScope::new(project_id, expected.clone(), request_id)
            .map_err(|_| "Retained font invocation scope was rejected".to_string())?;
        coordinator
            .with_retained_brand_font_media_root(
                scope,
                path,
                root,
                &state.production_font_staging_custody,
                kind,
            )
            .map(|(coordinator, owner)| (coordinator, Some(owner)))
            .map_err(|_| "Retained font owner custody was rejected".to_string())
    } else {
        Ok((coordinator, None))
    }
}

#[tauri::command]
async fn render_motion_canvas(
    mut owner: desktop_owner::OwnerInvocation,
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
    let (coordinator, mut font_owner) = motion_canvas_production_coordinator(
        &state,
        request.project_id,
        &expected,
        &request.request_id,
        connection,
        ProductionFontInvocationKind::Full,
    )?;
    // Register the actual caller work before the native Future can run.
    let preview_work = font_owner
        .as_mut()
        .map(ProductionFontStagingOwner::accept_full_preview)
        .transpose()
        .map_err(|_| "Retained font preview admission was rejected".to_string())?;
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
    let preview = match desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let register = || {
            registry.register_with_options(
                &output_root,
                &owner_resource,
                &for_grants,
                &source_options,
            )
        };
        match preview_work {
            Some(work) => work.run(register),
            None => register(),
        }
    })
    .await
    {
        Ok(preview) => {
            // Only this invocation's unique owner can retire after the actual
            // Full render and preview worker returned. Refusal preserves the
            // known render result and retains its existing custody.
            // A closed desktop cannot start the later retirement phase.
            // Keep the completed native result and its retained font custody.
            if owner.begin_phase().is_ok()
                && let Some(font_owner) = font_owner
            {
                font_owner.retire_after_success(&coordinator);
            }
            preview
        }
        Err(_) => {
            // Preserve the existing empty preview response, but a failed task
            // cannot confirm the owner postcheck or retire its font custody.
            Vec::new()
        }
    };
    Ok(NativePreviewRenderResponse { evidence, preview })
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SavedMotionRecoverRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    receipt_id: Uuid,
}

#[derive(Serialize)]
struct SavedMotionRecoverResponse {
    receipt_id: Uuid,
    capture_sha256: String,
    #[serde(flatten)]
    native: NativePreviewRenderResponse,
}

/// Discovery and recovery read the existing owner-selected SQLite/output roots.
/// No provider is invoked and no effect grant is issued or restored.
#[tauri::command]
async fn saved_motion_capture_inventory(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::AudioQcInventoryRequest,
) -> Result<motionwright_native::production::SavedMotionCaptureInventory, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Saved capture source changed; reopen the current project.".into());
    }
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Saved capture read capacity is busy; retry after current readers finish.")?;
    let path = selected_connection_path(&state, project.id)?
        .ok_or("Select the original Semwright owner connection to find saved captures.")?;
    let initial_path = path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let (result, identity) = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        let connection = ProductionConnection::load(path)
            .map_err(|_| "Original capture connection is unavailable.".to_string())?;
        let identity = connection
            .identity()
            .map_err(|_| "Original capture connection identity is unavailable.".to_string())?;
        let coordinator =
            ProductionCoordinator::with_receipt_custody(service, connection, &custody)
                .map_err(|_| "Original capture connection was rejected.".to_string())?;
        let result = coordinator
            .saved_motion_capture_inventory(project.id, &project.stamp(), request.deliverable_id)
            .map_err(|error| format!("Saved capture discovery was blocked: {}", error.message))?;
        Ok::<_, String>((result, identity))
    })
    .await
    .map_err(|_| "Saved capture discovery task failed.".to_string())??;
    owner.begin_phase()?;
    saved_motion_read_still_current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
        &initial_path,
        &identity,
    )?;
    Ok(result)
}

fn saved_motion_read_still_current(
    state: &AppState,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    path: &std::path::Path,
    identity: &str,
) -> Result<(), String> {
    audio_read_still_current(
        state,
        project_id,
        generation,
        revision,
        Some(path.to_path_buf()),
    )
    .map_err(|_| "Saved capture source changed while reading.".to_string())?;
    let current = ProductionConnection::load(path)
        .and_then(|connection| connection.identity())
        .map_err(|_| "Original capture connection changed while reading.".to_string())?;
    if current != identity {
        return Err("Original capture connection changed while reading.".into());
    }
    Ok(())
}

#[tauri::command]
async fn recover_saved_motion_capture(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: SavedMotionRecoverRequest,
) -> Result<SavedMotionRecoverResponse, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Saved capture source changed; reopen the current project.".into());
    }
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Saved capture read capacity is busy; retry after current readers finish.")?;
    let path = selected_connection_path(&state, project.id)?
        .ok_or("Select the original Semwright owner connection to reopen this capture.")?;
    let initial_path = path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let registry = state.preview.clone();
    let (result, identity) = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        let connection = ProductionConnection::load(path)
            .map_err(|_| "Original capture connection is unavailable.".to_string())?;
        let identity = connection
            .identity()
            .map_err(|_| "Original capture connection identity is unavailable.".to_string())?;
        let coordinator =
            ProductionCoordinator::with_receipt_custody(service, connection, &custody)
                .map_err(|_| "Original capture connection was rejected.".to_string())?;
        let original = coordinator
            .recover_saved_motion_capture(
                project.id,
                &project.stamp(),
                request.deliverable_id,
                request.receipt_id,
            )
            .map_err(|error| format!("Saved capture recovery was blocked: {}", error.message))?;
        let preview = registry.register_with_options(
            original.owner_root(),
            &project.resource_key(),
            original.evidence(),
            original.options(),
        );
        if preview.len() != original.evidence().segments.len() {
            return Err("Original capture frame handles could not be recovered completely.".into());
        }
        let result = SavedMotionRecoverResponse {
            receipt_id: original.receipt_id(),
            capture_sha256: original.capture_sha256().into(),
            native: NativePreviewRenderResponse {
                evidence: original.evidence().clone(),
                preview,
            },
        };
        Ok::<_, String>((result, identity))
    })
    .await
    .map_err(|_| "Saved capture recovery task failed.".to_string())??;
    owner.begin_phase()?;
    saved_motion_read_still_current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
        &initial_path,
        &identity,
    )?;
    Ok(result)
}

/// Explicit native prefix capture or read-only reuse of an original window.
/// Both retain the existing RenderLocal scope and one-use request grant.
#[tauri::command]
async fn render_motion_canvas_window(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: MotionCanvasWindowRenderRequest,
) -> Result<NativeWindowRenderResponse, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Native window scope is stale; reopen the saved revision.".into());
    }
    if request.request_id.trim().is_empty()
        || request.request_id.len() > 96
        || request.request_id.chars().any(char::is_control)
    {
        return Err("Native window request identity is invalid.".into());
    }
    if request.carry_original_revision.is_some()
        && (request.reuse_token.is_none()
            || request
                .carry_original_revision
                .is_some_and(|original| original >= request.revision))
    {
        return Err("An earlier native window and its original revision are required.".into());
    }
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(request.project_id, request.generation, request.revision),
        request.request_id.trim(),
    )?;
    let expected = RevisionStamp::from(&project);
    let registry = state.preview.clone();
    let mut retained_font_owner = None;
    let mut currency_work = None;
    let mut carried_connection = None;
    let (evidence, preview) = if let (Some(token), Some(original_revision)) =
        (request.reuse_token, request.carry_original_revision)
    {
        let path = selected_connection_path(&state, request.project_id)?.ok_or_else(|| {
            "Select the original Semwright connection for window reuse.".to_owned()
        })?;
        let original_path = path.clone();
        let window = request.window.clone();
        let (result, identity) = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            let connection = ProductionConnection::load(path)
                .map_err(|_| "Original window connection is unavailable.".to_owned())?;
            let identity = connection.identity().map_err(sanitized)?;
            let result =
                registry.carry_window(&project, token, &window, &connection, original_revision)?;
            Ok::<_, String>((result, identity))
        })
        .await
        .map_err(|_| "Original native window read task failed.".to_owned())??;
        carried_connection = Some((original_path, identity));
        result
    } else if let Some(token) = request.reuse_token {
        let original_project = project.clone();
        let window = request.window.clone();
        desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            registry.reuse_window(&original_project, token, &window)
        })
        .await
        .map_err(|_| "Original native window read task failed.".to_string())??
    } else {
        let connection_path = selected_connection_path(&state, request.project_id)?
            .ok_or_else(|| "Canonical Semwright production is not configured.".to_string())?;
        let connection = ProductionConnection::load(connection_path).map_err(|_| {
            "Canonical Semwright window connection could not be loaded.".to_string()
        })?;
        let owner_resource = connection.resource.clone();
        let (coordinator, mut font_owner) = motion_canvas_production_coordinator(
            &state,
            request.project_id,
            &expected,
            &request.request_id,
            connection,
            ProductionFontInvocationKind::Window,
        )?;
        let registry_work = font_owner
            .as_mut()
            .map(ProductionFontStagingOwner::accept_window_registry)
            .transpose()
            .map_err(|_| "Retained font window registry admission was rejected".to_string())?;
        currency_work = font_owner
            .as_mut()
            .map(ProductionFontStagingOwner::accept_window_currency)
            .transpose()
            .map_err(|_| "Retained font window currency admission was rejected".to_string())?;
        let evidence = coordinator.render_motion_canvas_window(request.project_id,&expected,
            &request.request_id,&request.window).await
            .map_err(|error| format!("Native window capture did not complete ({:?}, outcome_known={}); inspect the original operation before another capture.",error.code,error.outcome_known))?;
        let service = state.service.clone();
        let for_registry = evidence.clone();
        let preview = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            let register = || registry.register_window(&service, &owner_resource, &for_registry);
            match registry_work {
                Some(work) => work.run_result(register),
                None => register(),
            }
        })
        .await
        .map_err(|_| "Native window capture completed and its original result is retained, but registration could not be confirmed. Inspect the original request; do not repeat this capture automatically.".to_string())??;
        if let Some(owner) = font_owner {
            retained_font_owner = Some((coordinator, owner));
        }
        (evidence, preview)
    };
    owner.begin_phase().map_err(|_|
        "The native window result is retained, but the desktop closed before its current-source check. Inspect the original request before another capture; do not repeat it automatically.".to_owned()
    )?;
    if let Some((path, identity)) = carried_connection {
        saved_motion_read_still_current(
            &state,
            request.project_id,
            request.generation,
            request.revision,
            &path,
            &identity,
        )?;
    }
    let check_currency = || -> Result<(), String> {
        let latest = state
            .service
            .project(request.project_id)
            .map_err(sanitized)?;
        if latest.generation != request.generation || latest.revision != request.revision {
            return Err("Project changed during native window capture or reuse.".into());
        }
        Ok(())
    };
    match currency_work {
        Some(work) => work.run_result(check_currency)?,
        None => check_currency()?,
    }
    if let Some((coordinator, owner)) = retained_font_owner {
        // Reuse creates no owner and cannot retire an earlier invocation.
        owner.retire_after_success(&coordinator);
    }
    Ok(NativeWindowRenderResponse {
        window: evidence.summary(),
        preview,
    })
}

/// Source- and manifest-bound native MLT assembly readiness, without job
/// dispatch or any WebView access to the trusted output-root paths.
#[tauri::command]
async fn preflight_multi_segment_mlt_readiness(
    mut owner: desktop_owner::OwnerInvocation,
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
    let preflight = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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
    owner.begin_phase()?;
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
    mut owner: desktop_owner::OwnerInvocation,
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
    let targeted_audio = audio_targets_requested(&project, request.deliverable_id);
    require_audio_qc_host(targeted_audio).map_err(str::to_owned)?;
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
        original_mix_native::delivery_voice_output_duration(
            &project,
            profile,
            selected_voice.measured_duration,
        )?,
        segment.frame_count,
        motion.frame_rate.num,
        motion.frame_rate.den,
    )?;

    let saved_mix = original_mix_native::saved_mix_for_master(
        owner.worker_owner(),
        &state.service,
        &project,
        request.deliverable_id,
    )
    .await?;
    let voice_cut = original_mix_native::saved_voice_cut_for_master(
        owner.worker_owner(),
        &state.service,
        &project,
        request.deliverable_id,
    )
    .await?;

    // This is a production mutation and consumes only the corresponding
    // one-time revision/request-scoped local render effect capability.
    owner.begin_phase()?;
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
    let audio_connection_identity = if targeted_audio {
        Some(
            connection
                .identity()
                .map_err(|_| "Native audio QC connection identity is unavailable.".to_string())?,
        )
    } else {
        None
    };
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
            .map(|mix| mix.audio_inputs_sha256())
            .or_else(|| voice_cut.as_ref().map(|cut| cut.audio_inputs_sha256()))
            .unwrap_or(selected_voice.source_sha256.as_str()),
    );
    let output_root = connection.output_root.clone();
    let export_root = output_root.clone();
    let service = state.service.clone();
    let id = request.project_id;
    let track = request.voice_track_id;
    let base = expected.clone();
    let stable_identity = canonical_request_id.clone();
    let audio = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        if let Some(mix) = saved_mix {
            let latest = service.project(id).map_err(sanitized)?;
            mix.verify_project(&latest, mix.plan().deliverable_profile_id)
                .map_err(|error| error.to_string())?;
            stage_original_mix_wav(&output_root, &mix, &stable_identity)
        } else if let Some(cut) = voice_cut {
            let latest = service.project(id).map_err(sanitized)?;
            cut.verify_project(&latest, cut.profile_id())
                .map_err(|e| e.to_string())?;
            stage_voice_cut_wav(&output_root, &cut, &stable_identity)
        } else {
            let source = service
                .verified_master_voice(id, &base, track)
                .map_err(sanitized)?;
            stage_measured_wav(&output_root, &source, &stable_identity)
        }
    })
    .await
    .map_err(|_| "Verified WAV preparation task failed.".to_string())??;
    owner.begin_phase()?;
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "Canonical Semwright connection was rejected".to_string())?;
    let master_request = MltAvMasterRequest {
        request_id: &canonical_request_id,
        deliverable_id: request.deliverable_id,
        motion: &motion,
        audio: &audio,
        sync: None,
    };
    let (evidence, audio_qc) = if targeted_audio {
        // The measured wrapper itself calls the checked original producer.
        // Never run the unmeasured producer first or as an error fallback.
        let measured = coordinator
            .assemble_mlt_av_master_with_audio_analysis(
                request.project_id,
                &expected,
                master_request,
            )
            .await
            .map_err(|_| "Measured AV mastering did not return a complete current result. Original receipts and any produced master are retained; this attempt must not be repeated automatically.".to_string())?;
        if owner.begin_phase().is_err() {
            return Ok(NativeAvMasterResponse {
                evidence: measured.master,
                export_token: None,
                audio_qc: NativeAvAudioQcResponse::unavailable(
                    AudioQcUnavailableReason::SessionUnavailable,
                ),
            });
        }
        let audio_qc = NativeAvAudioQcResponse::from_measurements(
            &measured.audio_measurements,
            &coordinator,
            measured
                .master
                .master
                .pointer("/artifact/sha256")
                .and_then(Value::as_str),
        );
        // A later read failure does not discard a completed master or invent
        // a QC result. The session selector permits only a fresh proof read.
        (measured.master, audio_qc)
    } else {
        let evidence = coordinator
            .assemble_mlt_av_master(request.project_id, &expected, master_request)
            .await
            .map_err(|error| format!("Canonical AV mastering failed: {}", error.message))?;
        (evidence, NativeAvAudioQcResponse::NotRequested)
    };
    if owner.begin_phase().is_err() {
        // The producer completed. Preserve that known outcome without a new
        // Store read, registry write, effect grant or automatic native retry.
        return Ok(NativeAvMasterResponse {
            evidence,
            export_token: None,
            audio_qc,
        });
    }
    let token = if let Some(identity) = audio_connection_identity.as_deref() {
        state.av_delivery.register_measured(
            &export_root,
            &evidence,
            &canonical_request_id,
            identity,
        )
    } else {
        state.av_delivery.register(&export_root, &evidence)
    };
    Ok(NativeAvMasterResponse {
        evidence,
        export_token: token,
        audio_qc,
    })
}

/// Finalize the existing *owner-registered* multisegment native render via
/// the Semwright MLT semantic editor and curated AV mux. The WebView supplies
/// only expected project/profile IDs, one-time RenderLocal permission and a
/// prior opaque preview token, never media paths, render recipes or digests.
/// This is distinct from single-segment assemble_av_master above.
#[tauri::command]
async fn assemble_multisegment_av_master(
    mut owner: desktop_owner::OwnerInvocation,
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
    let targeted_audio = audio_targets_requested(&project, request.deliverable_id);
    require_audio_qc_host(targeted_audio).map_err(str::to_owned)?;
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
        original_mix_native::delivery_voice_output_duration(
            &project,
            profile,
            selected_voice.measured_duration,
        )?,
        total_frames,
        render.frame_rate.num,
        render.frame_rate.den,
    )?;
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "Canonical Semwright connection could not be loaded.".to_string())?;
    if connection.resource != project.resource_key() || connection.output_root != preview_root {
        return Err("Native multisegment source belongs to a different owner output root.".into());
    }
    let audio_connection_identity = if targeted_audio {
        Some(
            connection
                .identity()
                .map_err(|_| "Native audio QC connection identity is unavailable.".to_string())?,
        )
    } else {
        None
    };
    // Only application-held Film authoring options and verified manifests are
    // used; do not derive any new render input from a WebView JSON object.
    let preflight_root = preview_root.clone();
    let preflight_project = project.clone();
    let preflight_render = render.clone();
    let preflight_options = film_options.clone();
    let profile_id = request.deliverable_id;
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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

    let saved_mix = original_mix_native::saved_mix_for_master(
        owner.worker_owner(),
        &state.service,
        &project,
        request.deliverable_id,
    )
    .await?;
    let voice_cut = original_mix_native::saved_voice_cut_for_master(
        owner.worker_owner(),
        &state.service,
        &project,
        request.deliverable_id,
    )
    .await?;

    // This is a mutating native production request. Authorization is a
    // scoped, short-lived, single-use desktop effect grant.
    owner.begin_phase()?;
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
            .map(|mix| mix.audio_inputs_sha256())
            .or_else(|| voice_cut.as_ref().map(|cut| cut.audio_inputs_sha256()))
            .unwrap_or(selected_voice.source_sha256.as_str()),
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
    let staged = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        if let Some(mix) = saved_mix {
            let latest = service.project(id).map_err(sanitized)?;
            mix.verify_project(&latest, mix.plan().deliverable_profile_id)
                .map_err(|error| error.to_string())?;
            stage_original_mix_wav(&root, &mix, &stable)
        } else if let Some(cut) = voice_cut {
            let latest = service.project(id).map_err(sanitized)?;
            cut.verify_project(&latest, cut.profile_id())
                .map_err(|e| e.to_string())?;
            stage_voice_cut_wav(&root, &cut, &stable)
        } else {
            let original = service
                .verified_master_voice(id, &source, voice_track)
                .map_err(sanitized)?;
            stage_measured_wav(&root, &original, &stable)
        }
    })
    .await
    .map_err(|_| "Native multisegment source WAV staging failed.".to_string())??;

    owner.begin_phase()?;
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
    owner.begin_phase().map_err(|_|
        "Visual assembly completed, but the desktop closed before AV mastering. Preserve the original request and inspect its retained result; do not retry automatically.".to_owned()
    )?;
    let master_request = MltMultisegmentAvMasterRequest {
        request_id: &native_request_id,
        deliverable_id: request.deliverable_id,
        options: &film_options,
        rendered: &render,
        visual: &visual,
        audio: &staged,
    };
    let (master, audio_qc) = if targeted_audio {
        let measured = coordinator
            .assemble_native_mlt_multisegment_av_master_with_audio_analysis(
                project.id,
                &expected,
                master_request,
            )
            .await
            .map_err(|_| "Measured multisegment mastering did not return a complete current result. Original receipts and any produced master are retained; this attempt must not be repeated automatically.".to_string())?;
        owner.begin_phase().map_err(|_|
            "Native mastering completed, but the desktop closed before QC and delivery registration. The original result is retained; no new token was issued. Do not repeat this operation automatically.".to_owned()
        )?;
        let audio_qc = NativeAvAudioQcResponse::from_measurements(
            &measured.audio_measurements,
            &coordinator,
            measured
                .master
                .master
                .pointer("/artifact/sha256")
                .and_then(Value::as_str),
        );
        (measured.master, audio_qc)
    } else {
        let master = coordinator
            .assemble_native_mlt_multisegment_av_master(project.id, &expected, master_request)
            .await
            .map_err(|error| {
                format!(
                    "Canonical multisegment AV mastering failed: {}",
                    error.message
                )
            })?;
        (master, NativeAvAudioQcResponse::NotRequested)
    };
    owner.begin_phase().map_err(|_|
        "Native mastering completed, but the desktop closed before delivery registration. The original result is retained; no new token was issued. Do not repeat this operation automatically.".to_owned()
    )?;
    let export_token = if let Some(identity) = audio_connection_identity.as_deref() {
        state.av_delivery.register_measured_multisegment(
            &output_root,
            &master,
            &native_request_id,
            identity,
        )
    } else {
        state.av_delivery.register_multisegment(&output_root, &master)
    }
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
        audio_qc,
    })
}

/// Deliver a previous verified canonical master to an explicitly named
/// destination. The source is resolved from a scoped session handle rather
/// than any WebView-provided path, and files are created without overwrite.
#[tauri::command]
async fn export_native_av_master(
    mut owner: desktop_owner::OwnerInvocation,
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
    let destination = av_delivery::PreparedMasterDestination::open(&request.destination)?;
    let scope = checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    destination.check_current()?;
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        scope,
        request.destination.trim(),
    )?;
    let write_integrity = request.include_integrity_manifest;
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    let receipt = desktop_owner::spawn_blocking(worker, move || {
        source.publish_prepared(&destination, write_integrity, SEMWRIGHT_REVISION, || {
            publisher.phase()
        })
    })
    .await
    .map_err(|_| "Native master export task failed.".to_string())??;
    // Delivery already completed. A closed owner cannot read a new Store
    // phase, but the known file result must not become an unknown mutation.
    if owner.begin_phase().is_err() {
        return Ok(MasterExportReceipt {
            source_current: false,
            ..receipt
        });
    }
    let current = state
        .service
        .project(request.project_id)
        .is_ok_and(|project| {
            project.generation == request.generation && project.revision == request.revision
        });
    Ok(MasterExportReceipt {
        source_current: current,
        ..receipt
    })
}

/// Qualify the original after a restart, then consume a NEW scoped local
/// delivery grant. Old session tokens, QC JSON and receipt selectors never
/// authorize a copy. No Native render, measurement or receipt write occurs.
#[tauri::command]
async fn export_saved_native_av_master(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::SavedMasterExportRequest,
) -> Result<MasterExportReceipt, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Saved MP4 delivery belongs to a previous project revision.".into());
    }
    let connection_path = selected_connection_path(&state, project.id)?
        .ok_or("Choose the original owner connection before delivering a saved MP4.")?;
    let destination = av_delivery::PreparedMasterDestination::open(&request.destination)?;
    let expected_path = connection_path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let selection = request.selection();
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Original media read capacity is busy; retry after current readers finish.")?;
    let (source, identity, preparation) =
        desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            let (source, identity) = av_delivery::prepare_saved_delivery(
                &project,
                &selection,
                service,
                &connection_path,
                &custody,
            )?;
            Ok::<_, String>((source, identity, preparation))
        })
        .await
        .map_err(|_| "Saved MP4 qualification task failed.".to_owned())??;
    owner.begin_phase()?;
    let latest = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if latest.generation != request.generation
        || latest.revision != request.revision
        || selected_connection_path(&state, request.project_id)?.as_ref() != Some(&expected_path)
        || ProductionConnection::load(&expected_path)
            .and_then(|c| c.identity())
            .ok()
            .as_deref()
            != Some(identity.as_str())
    {
        return Err("Saved MP4 source or owner connection changed before delivery.".into());
    }
    let scope = checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    destination.check_current()?;
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::DeliverLocal,
        scope,
        request.destination.trim(),
    )?;
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    let receipt = desktop_owner::spawn_blocking(worker, move || {
        let _preparation = preparation;
        source.publish_prepared(
            &destination,
            request.include_integrity_manifest,
            SEMWRIGHT_REVISION,
            || publisher.phase(),
        )
    })
    .await
    .map_err(|_| {
        "Saved MP4 delivery task failed; inspect the destination before retrying.".to_owned()
    })??;
    if owner.begin_phase().is_err() {
        return Ok(MasterExportReceipt {
            source_current: false,
            ..receipt
        });
    }
    let current = state
        .service
        .project(request.project_id)
        .is_ok_and(|project| {
            project.generation == request.generation && project.revision == request.revision
        });
    Ok(MasterExportReceipt {
        source_current: current,
        ..receipt
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PrivateDeliverySlotRequest {
    project_id: Uuid,
    generation: Uuid,
    deliverable_id: Uuid,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CommitPrivateDeliveryRequest {
    plan: motionwright_service::PrivateDeliveryPlan,
    effect_grant: Uuid,
}

#[derive(Serialize)]
struct PreparedPrivateDelivery {
    plan: motionwright_service::PrivateDeliveryPlan,
    subject: String,
}

#[tauri::command]
async fn inspect_private_delivery(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: PrivateDeliverySlotRequest,
) -> Result<Option<motionwright_service::PrivateDeliveryManifest>, String> {
    let reader = state
        .av_review
        .admit_preparation()
        .ok_or("Original media read capacity is busy.")?;
    let store = state.service.store();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _reader = reader;
        let verify = store
            .lock()
            .prepare_private_delivery_inspection(
                request.project_id,
                request.generation,
                request.deliverable_id,
            )
            .map_err(sanitized)?;
        verify().map_err(|_| {
            "The saved private delivery could not be verified in the local media store.".into()
        })
    })
    .await
    .map_err(|_| "Private delivery inspection did not complete.".to_string())?
}

#[tauri::command]
async fn prepare_private_delivery(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::SavedMasterRecoveryRequest,
) -> Result<PreparedPrivateDelivery, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let project = service.project(request.project_id).map_err(sanitized)?;
        if project.generation != request.generation || project.revision != request.revision {
            return Err("The private delivery source is no longer current.".into());
        }
        let plan = service
            .store()
            .lock()
            .prepare_private_delivery(
                request.project_id,
                &project.stamp(),
                request.deliverable_id,
                request.mux_receipt_id,
                &request.expected_master_sha256,
            )
            .map_err(sanitized)?;
        let subject = format!("private-delivery:{}", plan.identity().map_err(sanitized)?);
        Ok(PreparedPrivateDelivery { plan, subject })
    })
    .await
    .map_err(|_| "Private delivery preparation did not complete.".to_string())?
}

#[tauri::command]
async fn commit_private_delivery(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: CommitPrivateDeliveryRequest,
) -> Result<motionwright_service::PrivateDeliveryManifest, String> {
    let plan = request.plan;
    let project = state.service.project(plan.project_id).map_err(sanitized)?;
    if project.generation != plan.generation || project.revision != plan.revision {
        return Err("The private delivery source is no longer current.".into());
    }
    let store = state.service.store();
    let target = {
        let store = store.lock();
        let current = store
            .prepare_private_delivery(
                plan.project_id,
                &project.stamp(),
                plan.deliverable_id,
                plan.mux_receipt_id,
                &plan.artifact.sha256,
            )
            .map_err(sanitized)?;
        if current != plan {
            return Err(
                "The private delivery slot changed. Inspect it before approving again.".into(),
            );
        }
        store.file_import_capture_target()
    };
    let connection_path = selected_connection_path(&state, project.id)?
        .ok_or("Choose the original owner connection before private delivery.")?;
    let retained_connection_path = connection_path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let reader = state
        .av_review
        .admit_preparation()
        .ok_or("Original media read capacity is busy.")?;
    let subject = format!("private-delivery:{}", plan.identity().map_err(sanitized)?);
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::PromotePrivateDelivery,
        EffectScope::project(plan.project_id, plan.generation, plan.revision),
        &subject,
    )?;
    let selection = av_delivery::SavedMasterRecoveryRequest {
        project_id: plan.project_id,
        generation: plan.generation,
        revision: plan.revision,
        deliverable_id: plan.deliverable_id,
        mux_receipt_id: plan.mux_receipt_id,
        expected_master_sha256: plan.artifact.sha256.clone(),
    };
    let (plan, target, staged, identity, reader) =
        desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            let (source, identity) = av_delivery::prepare_saved_delivery(
                &project,
                &selection,
                service,
                &connection_path,
                &custody,
            )?;
            let staged = source.stage_private_delivery(&target, &plan)?;
            Ok::<_, String>((plan, target, staged, identity, reader))
        })
        .await
        .map_err(|_| "Private delivery capture did not complete.".to_string())??;
    owner.begin_phase()?;
    let connections = state.runtime_connections.clone();
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    desktop_owner::spawn_blocking(worker, move || {
        let _reader = reader;
        let mut store = store.lock();
        target.check_store(&store).map_err(sanitized)?;
        store
            .commit_private_delivery(&plan, &staged, || {
                let permit = publisher
                    .phase()
                    .map_err(|_| std::io::ErrorKind::PermissionDenied)?;
                if connections
                    .resolve(plan.project_id, plan.generation)
                    .ok()
                    .flatten()
                    .as_ref()
                    != Some(&retained_connection_path)
                    || ProductionConnection::load(&retained_connection_path)
                        .and_then(|connection| connection.identity())
                        .ok()
                        .as_deref()
                        != Some(identity.as_str())
                {
                    return Err(std::io::ErrorKind::PermissionDenied.into());
                }
                Ok(permit)
            })
            .map_err(sanitized)
    })
    .await
    .map_err(|_| {
        "Private delivery outcome is unknown. Inspect the slot before trying another delivery."
            .to_string()
    })?
}

/// Verify a user-selected local MP4 and its unsigned portable JSON receipt.
/// Pure read-only content comparison: this grants no delivery or publisher authority.
#[tauri::command]
async fn verify_local_media_integrity(
    mut owner: desktop_owner::OwnerInvocation,
    request: VerifyPortableMediaRequest,
) -> Result<PortableMediaVerification, String> {
    desktop_owner::spawn_blocking(owner.worker_owner(), move || verify_portable_media(request))
        .await
        .map_err(|_| "Portable media verification task failed.".to_string())?
}

/// Prepare bounded playback of the exact completed native master. Legacy
/// callers may still request the original small-file binary IPC response.
#[tauri::command]
async fn review_native_av_master(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::streaming::PlaybackSelection,
) -> Result<tauri::ipc::Response, String> {
    let (project_id, generation, revision) = request.source();
    let project = state.service.project(project_id).map_err(sanitized)?;
    if project.generation != generation || project.revision != revision {
        return Err("Native AV preview belongs to a previous project revision.".into());
    }
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Original media read capacity is busy; retry after current readers finish.")?;
    let mut expected_connection = None;
    let response = match request {
        av_delivery::streaming::PlaybackSelection::Session(request) => {
            let source = state.av_delivery.resolve(&project, request.export_token)?;
            if request.stream {
                let streams = state.av_review.clone();
                let token = request.export_token;
                let descriptor = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
                    let _preparation = preparation;
                    streams.prepare(&source, token)
                })
                .await
                .map_err(|_| "Native AV playback preparation task failed.".to_string())??;
                tauri::ipc::Response::new(
                    serde_json::to_string(&descriptor)
                        .map_err(|_| "Native AV playback response is unavailable.".to_string())?,
                )
            } else {
                let data = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
                    let _preparation = preparation;
                    source.review_bytes()
                })
                .await
                .map_err(|_| "Native AV preview read task failed.".to_string())??;
                tauri::ipc::Response::new(data)
            }
        }
        av_delivery::streaming::PlaybackSelection::Receipt(request) => {
            let path = selected_connection_path(&state, project.id)?;
            expected_connection = Some(path.clone());
            let service = state.service.clone();
            let custody = state.production_receipt_custody.clone();
            let deliveries = state.av_delivery.clone();
            let streams = state.av_review.clone();
            let descriptor = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
                let _preparation = preparation;
                av_delivery::recover_master_review(
                    &project,
                    &request,
                    service,
                    path.as_deref(),
                    &custody,
                    (&deliveries, &streams),
                )
            })
            .await
            .map_err(|_| "Original master recovery task failed.".to_string())??;
            tauri::ipc::Response::new(
                serde_json::to_string(&descriptor)
                    .map_err(|_| "Original master recovery response is unavailable.".to_string())?,
            )
        }
        av_delivery::streaming::PlaybackSelection::SavedMux(request) => {
            let path = selected_connection_path(&state, project.id)?;
            expected_connection = Some(path.clone());
            let service = state.service.clone();
            let custody = state.production_receipt_custody.clone();
            let deliveries = state.av_delivery.clone();
            let streams = state.av_review.clone();
            let descriptor = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
                let _preparation = preparation;
                av_delivery::recover_saved_mux_review(
                    &project,
                    &request,
                    service,
                    path.as_deref(),
                    &custody,
                    (&deliveries, &streams),
                )
            })
            .await
            .map_err(|_| "Original master recovery task failed.".to_string())??;
            tauri::ipc::Response::new(
                serde_json::to_string(&descriptor)
                    .map_err(|_| "Original master recovery response is unavailable.".to_string())?,
            )
        }
    };
    owner.begin_phase()?;
    let latest = state.service.project(project_id).map_err(sanitized)?;
    if latest.generation != generation || latest.revision != revision {
        return Err("Project changed during native AV preview readback.".into());
    }
    if let Some(original) = expected_connection
        && selected_connection_path(&state, project_id)? != original
    {
        return Err("Original master recovery connection changed.".into());
    }
    Ok(response)
}

/// Read current QC for an existing session-authorized master. This command
/// never consumes a render grant, starts native work, writes receipts, or
/// accepts private request/lane/connection selectors from the WebView.
#[tauri::command]
async fn read_native_av_audio_qc(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: MasterReviewRequest,
) -> Result<NativeAvAudioQcResponse, String> {
    let project = match state.service.project(request.project_id) {
        Ok(project)
            if project.generation == request.generation && project.revision == request.revision =>
        {
            project
        }
        _ => {
            return Ok(NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::SourceChanged,
            ));
        }
    };
    let source = match state.av_delivery.resolve(&project, request.export_token) {
        Ok(source) => source,
        Err(_) => {
            return Ok(NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::SessionUnavailable,
            ));
        }
    };
    // The registry binds the profile privately. Connection selection remains
    // owner-held; a missing connection cannot trigger render or measurement.
    let connection_path = match selected_connection_path(&state, project.id) {
        Ok(path) => path,
        Err(_) => {
            return Ok(NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::ConnectionUnavailable,
            ));
        }
    };
    let initial_connection_path = connection_path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        source.read_audio_qc(&project, service, connection_path.as_deref(), &custody)
    })
    .await
    .unwrap_or_else(|_| {
        NativeAvAudioQcResponse::unavailable(AudioQcUnavailableReason::MeasurementUnavailable)
    });
    owner.begin_phase()?;
    match state.service.project(request.project_id) {
        Ok(latest)
            if latest.generation == request.generation && latest.revision == request.revision => {}
        _ => {
            return Ok(NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::SourceChanged,
            ));
        }
    }
    Ok(match selected_connection_path(&state, request.project_id) {
        Ok(path) if path == initial_connection_path => result,
        _ => NativeAvAudioQcResponse::unavailable(AudioQcUnavailableReason::ConnectionUnavailable),
    })
}

/// Read-only discovery for completed muxes, independent of optional audio QC.
#[tauri::command]
async fn native_av_saved_master_inventory(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::AudioQcInventoryRequest,
) -> Result<motionwright_native::production::SavedMasterReceiptInventory, String> {
    let project = state
        .service
        .project(request.project_id)
        .map_err(sanitized)?;
    if project.generation != request.generation || project.revision != request.revision {
        return Err("Saved original media belongs to a previous project revision.".into());
    }
    let preparation = state
        .av_review
        .admit_preparation()
        .ok_or("Original media read capacity is busy; retry after current readers finish.")?;
    let path = selected_connection_path(&state, project.id)?;
    let initial_path = path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let _preparation = preparation;
        av_delivery::read_saved_master_inventory(
            &project,
            request.deliverable_id,
            service,
            path.as_deref(),
            &custody,
        )
    })
    .await
    .map_err(|_| "Saved original media discovery task failed.".to_string())??;
    owner.begin_phase()?;
    audio_read_still_current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
        initial_path,
    )
    .map_err(|_| "Saved original media source changed during discovery.".to_string())?;
    Ok(result)
}

/// Current receipt references only; never a media or execution capability.
#[tauri::command]
async fn native_av_audio_qc_receipt_inventory(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::AudioQcInventoryRequest,
) -> Result<av_delivery::NativeAvAudioQcInventoryResponse, String> {
    use av_delivery::NativeAvAudioQcInventoryResponse as Response;
    let project = match state.service.project(request.project_id) {
        Ok(project)
            if project.generation == request.generation && project.revision == request.revision =>
        {
            project
        }
        _ => {
            return Ok(Response::Unavailable {
                reason: AudioQcUnavailableReason::SourceChanged,
            });
        }
    };
    let path = match selected_connection_path(&state, project.id) {
        Ok(path) => path,
        Err(_) => {
            return Ok(Response::Unavailable {
                reason: AudioQcUnavailableReason::ConnectionUnavailable,
            });
        }
    };
    let initial_path = path.clone();
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        av_delivery::read_audio_qc_inventory(
            &project,
            request.deliverable_id,
            service,
            path.as_deref(),
            &custody,
        )
    })
    .await
    .unwrap_or(Response::Unavailable {
        reason: AudioQcUnavailableReason::MeasurementUnavailable,
    });
    owner.begin_phase()?;
    Ok(
        match audio_read_still_current(
            &state,
            request.project_id,
            request.generation,
            request.revision,
            initial_path,
        ) {
            Ok(()) => result,
            Err(reason) => Response::Unavailable { reason },
        },
    )
}

/// The durable UUID and recorded digest are untrusted selectors. Native
/// recovery alone reconstructs the original proof; this path creates no token.
#[tauri::command]
async fn recover_native_av_audio_qc(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: av_delivery::AudioQcRecoveryRequest,
) -> Result<NativeAvAudioQcResponse, String> {
    let project = match state.service.project(request.project_id) {
        Ok(project)
            if project.generation == request.generation && project.revision == request.revision =>
        {
            project
        }
        _ => {
            return Ok(NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::SourceChanged,
            ));
        }
    };
    let path = match selected_connection_path(&state, project.id) {
        Ok(path) => path,
        Err(_) => {
            return Ok(NativeAvAudioQcResponse::unavailable(
                AudioQcUnavailableReason::ConnectionUnavailable,
            ));
        }
    };
    let initial_path = path.clone();
    let (project_id, generation, revision) =
        (request.project_id, request.generation, request.revision);
    let service = state.service.clone();
    let custody = state.production_receipt_custody.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        av_delivery::recover_audio_qc(&project, &request, service, path.as_deref(), &custody)
    })
    .await
    .unwrap_or_else(|_| {
        NativeAvAudioQcResponse::unavailable(AudioQcUnavailableReason::MeasurementUnavailable)
    });
    owner.begin_phase()?;
    Ok(
        match audio_read_still_current(&state, project_id, generation, revision, initial_path) {
            Ok(()) => result,
            Err(reason) => NativeAvAudioQcResponse::unavailable(reason),
        },
    )
}

fn audio_read_still_current(
    state: &AppState,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    initial_path: Option<PathBuf>,
) -> Result<(), AudioQcUnavailableReason> {
    match state.service.project(project_id) {
        Ok(project) if project.generation == generation && project.revision == revision => {}
        _ => return Err(AudioQcUnavailableReason::SourceChanged),
    }
    match selected_connection_path(state, project_id) {
        Ok(path) if path == initial_path => Ok(()),
        _ => Err(AudioQcUnavailableReason::ConnectionUnavailable),
    }
}

#[tauri::command]
async fn preview_native_frame(
    mut owner: desktop_owner::OwnerInvocation,
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
    let bytes = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        registry.read_frame(&before, &request)
    })
    .await
    .map_err(|_| "Native preview read task failed.".to_string())??;
    owner.begin_phase()?;
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
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::FileImportPrepareRequest,
) -> Result<file_import::PreparedFileImport, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        file_import::prepare_file_import(&service, request).map_err(file_import_error)
    })
    .await
    .map_err(|_| "Import preparation task failed.".to_string())?
}

#[tauri::command]
async fn import_asset_file(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::FileImportDispatchRequest,
) -> Result<file_import::FileImportReceipt, String> {
    let service = state.service.clone();
    // The clone shares the existing registry; it does not create new authority.
    let grants = state.effect_grants.clone();
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    desktop_owner::spawn_blocking(worker, move || {
        file_import::dispatch_file_import(
            &service,
            &grants,
            file_import::FileImportKind::Asset,
            request,
            || {
                publisher
                    .phase()
                    .map_err(|_| std::io::ErrorKind::PermissionDenied.into())
            },
        )
        .map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "File import task failed. Check the original import result before importing again."
            .to_string()
    })?
}

#[tauri::command]
async fn import_voice_file(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::FileImportDispatchRequest,
) -> Result<file_import::FileImportReceipt, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    desktop_owner::spawn_blocking(worker, move || {
        file_import::dispatch_file_import(
            &service,
            &grants,
            file_import::FileImportKind::Voice,
            request,
            || {
                publisher
                    .phase()
                    .map_err(|_| std::io::ErrorKind::PermissionDenied.into())
            },
        )
        .map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "Voice import task failed. Check the original import result before importing again."
            .to_string()
    })?
}

#[tauri::command]
async fn recover_file_import(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::FileImportRecoveryRequest,
) -> Result<Option<file_import::FileImportReceipt>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        file_import::recover_file_import(&service, request).map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "Import recovery read failed. The retained request is still available.".to_string()
    })?
}

#[tauri::command]
async fn prepare_native_capsule_source_file(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::NativeCapsuleSourcePrepareRequest,
) -> Result<file_import::PreparedFileImport, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        file_import::prepare_native_capsule_source_file(&service, request)
            .map_err(file_import_error)
    })
    .await
    .map_err(|_| "Native source preparation task failed.".to_string())?
}

#[tauri::command]
async fn import_native_capsule_source_file(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::FileImportDispatchRequest,
) -> Result<file_import::FileImportReceipt, String> {
    let service = state.service.clone();
    // Shares the existing one-time import grant registry and atomic service.
    let grants = state.effect_grants.clone();
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    desktop_owner::spawn_blocking(worker, move || {
        file_import::dispatch_native_capsule_source_file(&service, &grants, request, || {
            publisher
                .phase()
                .map_err(|_| std::io::ErrorKind::PermissionDenied.into())
        })
        .map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "Native source save task failed. Check the original import result before saving again."
            .to_string()
    })?
}

#[tauri::command]
async fn recover_native_capsule_source_file(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: file_import::FileImportRecoveryRequest,
) -> Result<Option<file_import::FileImportReceipt>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        file_import::recover_native_capsule_source_file(&service, request)
            .map_err(file_import_error)
    })
    .await
    .map_err(|_| {
        "Native source recovery read failed. The retained request is still available.".to_string()
    })?
}

#[tauri::command]
async fn waveform_page(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: WaveformPageRequest,
) -> Result<WaveformPage, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
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
async fn native_hyperframes_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: hyperframes_source::SourceRequest,
) -> Result<hyperframes_source::SourceSnapshot, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        hyperframes_source::inspect_source(&service, request)
    })
    .await
    .map_err(|error| error.to_string())?
}

fn export_worker_error(error: desktop_owner::OwnerTaskFailure) -> String {
    match error {
        desktop_owner::OwnerTaskFailure::OwnerUnavailable => error.to_string(),
        desktop_owner::OwnerTaskFailure::WorkerJoinFailure => {
            "The export outcome is unknown. Check the selected destination before trying again."
                .to_string()
        }
    }
}

#[tauri::command]
async fn export_hyperframes_source_file(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: hyperframes_source::ExportRequest,
) -> Result<hyperframes_source::ExportResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        hyperframes_source::export_source(&service, &grants, request)
    })
    .await
    .map_err(export_worker_error)?
}

#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
enum BundleExportDestination {
    PortableProject,
    OtioMedia,
}

#[tauri::command]
async fn choose_bundle_export_destination(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
    kind: BundleExportDestination,
) -> Result<Option<String>, String> {
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let (title, name) = match kind {
        BundleExportDestination::PortableProject => (
            "Choose a new portable project folder",
            "project.motionwright",
        ),
        BundleExportDestination::OtioMedia => {
            ("Choose a new OTIO media bundle folder", "cut.otio-bundle")
        }
    };
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        app.dialog()
            .file()
            .set_title(title)
            .set_file_name(name)
            .blocking_save_file()
    })
    .await
    .map_err(|_| "The native bundle destination picker did not finish.".to_string())?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    owner.begin_phase()?;
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let selected = selected
        .into_path()
        .map_err(|_| "Choose a local bundle destination.".to_string())?;
    let path = selected
        .to_str()
        .filter(|path| {
            path.trim() == *path && path.len() <= 1800 && !path.chars().any(char::is_control)
        })
        .ok_or("The selected bundle destination cannot be represented safely.")?;
    match kind {
        BundleExportDestination::PortableProject => {
            let destination = absolute_bundle_path(path)?;
            match std::fs::symlink_metadata(destination) {
                Ok(_) => {
                    return Err(
                        "The export destination already exists. Choose a new folder name.".into(),
                    );
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => {
                    return Err("The new portable project destination could not be checked.".into());
                }
            }
        }
        BundleExportDestination::OtioMedia => {
            otio_bridge::new_owner_bundle_destination(path)?;
        }
    }
    Ok(Some(path.to_owned()))
}

#[tauri::command]
async fn export_project_bundle(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ExportBundleRequest,
) -> Result<BundleExportResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    desktop_owner::spawn_blocking(worker, move || {
        project_export::export_project_bundle(&service, &grants, request, || {
            publisher
                .phase()
                .map_err(|_| std::io::ErrorKind::PermissionDenied.into())
        })
    })
    .await
    .map_err(|_| {
        "The bundle export outcome is unknown. Verify the original destination.".to_string()
    })?
}

#[tauri::command]
async fn verify_project_bundle_export(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: project_export::VerifyBundleExportRequest,
) -> Result<project_export::BundleExportVerification, String> {
    let service = state.service.clone();
    let scope = (request.project_id, request.generation, request.revision);
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_export::verify_bundle_export(&service, request)
    })
    .await
    .map_err(|_| "The existing bundle verification did not finish.".to_string())??;
    owner.begin_phase()?;
    checked_effect_scope(&state, scope.0, scope.1, scope.2)?;
    Ok(result)
}

#[tauri::command]
async fn inspect_manim_editable_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: manim_editable_source::SourceRequest,
) -> Result<manim_editable_source::SourceSnapshot, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        manim_editable_source::inspect_source(&service, request)
    })
    .await
    .map_err(|_| "Manim source inspection did not finish.".to_string())?
}

#[tauri::command]
async fn export_manim_editable_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: manim_editable_source::ExportRequest,
) -> Result<manim_editable_source::ExportResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move ||
        manim_editable_source::export_source(&service, &grants, request))
        .await.map_err(|_| "The original Manim source export worker did not finish. Inspect the destination; do not retry automatically.".to_string())?
}

#[tauri::command]
async fn inspect_attached_manim_editable_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: manim_editable_source::AttachedRequest,
) -> Result<manim_editable_source::AttachedResponse, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        manim_editable_source::inspect_attached(&service, request)
    })
    .await
    .map_err(|_| "The attached Manim source inspection did not finish.".to_string())?
}

#[tauri::command]
async fn preview_manim_editable_source_edit(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: manim_editable_source::EditRequest,
) -> Result<manim_editable_source::EditResponse, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        manim_editable_source::preview_edit(&service, request)
    })
    .await
    .map_err(|_| "The Manim source edit inspection did not finish.".to_string())?
}

#[tauri::command]
async fn export_native_capsule_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: NativeSourceExportRequest,
) -> Result<NativeSourceExportResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_export::export_native_capsule_source(&service, &grants, request)
    })
    .await
    .map_err(export_worker_error)?
}

#[tauri::command]
async fn export_caption_sidecar(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ExportCaptionRequest,
) -> Result<CaptionExportResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        caption_export::export_caption_sidecar(&service, &grants, request)
    })
    .await
    .map_err(export_worker_error)?
}

#[tauri::command]
async fn export_otio(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ExportOtioRequest,
) -> Result<OtioExportResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_export::export_otio(&service, &grants, request)
    })
    .await
    .map_err(export_worker_error)?
}

#[tauri::command]
async fn export_otio_source_snapshot(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ExportOtioSourceSnapshotRequest,
) -> Result<OtioSourceSnapshotResponse, String> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        project_export::export_otio_source_snapshot(&service, &grants, request)
    })
    .await
    .map_err(export_worker_error)?
}

#[tauri::command]
async fn export_portable_otio_media_bundle(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: otio_bridge::OtioMediaBundleExportRequest,
) -> Result<otio_bridge::OtioMediaBundleExportResponse, otio_bridge::OtioBridgeError> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        otio_bridge::export_portable(&service, &grants, request)
    })
    .await
    .map_err(|_| "The original OTIO media export worker did not finish cleanly.".to_string())?
}

#[tauri::command]
async fn check_otio_media_runtime(mut owner: desktop_owner::OwnerInvocation) -> Result<(), String> {
    let result =
        desktop_owner::spawn_blocking(owner.worker_owner(), otio_bridge::check_runtime_files)
            .await
            .map_err(|_| "The OTIO runtime check did not finish.".to_string())?;
    owner.begin_phase()?;
    result
}

#[tauri::command]
async fn read_otio_bridge_status(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<Vec<otio_bridge::OtioBridgeUnsettled>, String> {
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let service = state.service.clone();
    let project_id = request.project_id;
    let generation = request.generation;
    let pending = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        otio_bridge::unsettled_bridge_operations(&service, project_id, generation)
    })
    .await
    .map_err(|_| "The retained OTIO status read did not finish.".to_string())??;
    owner.begin_phase()?;
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(pending)
}

#[tauri::command]
async fn choose_otio_media_bundle_folder(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: RuntimeConnectionSelectionRequest,
) -> Result<Option<String>, String> {
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        app.dialog()
            .file()
            .set_title("Choose the original OTIO media bundle folder")
            .blocking_pick_folder()
    })
    .await
    .map_err(|_| "The native OTIO bundle folder picker failed.".to_string())?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    owner.begin_phase()?;
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let path = selected
        .into_path()
        .map_err(|_| "Choose a local OTIO media bundle folder.".to_string())?;
    let path = path
        .to_str()
        .ok_or("The OTIO bundle folder path cannot be represented as text.")?;
    otio_bridge::canonical_owner_directory(path)?;
    // Preserve the selected spelling for the existing preview grant subject.
    Ok(Some(path.to_owned()))
}

#[tauri::command]
async fn preview_otio_media_cut(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: otio_bridge::OtioMediaPreviewRequest,
) -> Result<otio_bridge::OtioMediaPreviewResponse, otio_bridge::OtioBridgeError> {
    // The human-reviewed Change remains a separate, scoped operation.
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        otio_bridge::preview(&service, &grants, request)
    })
    .await
    .map_err(|_| "The original OTIO inspection worker did not finish cleanly.".to_string())?
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum PortableProjectSource {
    Folder,
    Archive,
}

#[tauri::command]
async fn choose_portable_project_source(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    kind: PortableProjectSource,
) -> Result<Option<String>, String> {
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let dialog = app.dialog().file();
        match kind {
            PortableProjectSource::Folder => dialog
                .set_title("Choose a Motionwright project folder")
                .blocking_pick_folder(),
            PortableProjectSource::Archive => dialog
                .set_title("Choose a project archive")
                .add_filter("JSON project archives", &["json"])
                .blocking_pick_file(),
        }
    })
    .await
    .map_err(|_| "The native portable project picker failed.".to_string())?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    owner.begin_phase()?;
    let path = selected
        .into_path()
        .map_err(|_| "Choose a local portable project source.".to_string())?;
    let path = path
        .to_str()
        .filter(|path| path.trim() == *path && !path.chars().any(char::is_control))
        .ok_or("The selected portable project path cannot be represented safely.")?;
    absolute_bundle_path(path)?;
    Ok(Some(path.to_owned()))
}

#[tauri::command]
async fn inspect_project_bundle(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: BundlePathRequest,
) -> Result<BundlePlanResponse, String> {
    let source = absolute_bundle_path(&request.path)?;
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let inspected = service
            .inspect_project_bundle_with_fingerprint(&source)
            .map_err(portable_bundle_error)?;
        let plan = inspected.plan;
        Ok(BundlePlanResponse {
            project_id: plan.project.project_id,
            title: plan.project.title,
            source_generation: plan.project.source_generation,
            revision: plan.project.revision.to_string(),
            event_count: plan.project.event_count,
            blob_count: plan.blob_count,
            total_blob_bytes: plan.total_blob_bytes.to_string(),
            rotates_generation: plan.project.rotates_generation,
            fingerprint: inspected.fingerprint,
            migration_notes: inspected.migration_notes,
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn recover_project_bundle_import(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: motionwright_service::BundleImportRecoveryRequest,
) -> Result<Option<motionwright_service::BundleImportCommit>, String> {
    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        service
            .recover_project_bundle_import(&request)
            .map_err(sanitized)
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn import_project_bundle(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ImportBundleRequest,
) -> Result<Project, Value> {
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    let worker = owner.worker_owner();
    let publisher = worker.clone();
    desktop_owner::spawn_blocking(worker, move || {
        grants.consume(
            request.effect_grant,
            EffectKind::ImportLocal,
            EffectScope::unscoped_import(),
            request.path.trim(),
        )?;
        let source = absolute_bundle_path(&request.path)?;
        service
            .import_project_bundle_inspected_checked(&source, &request.fingerprint, || {
                publisher
                    .phase()
                    .map_err(|_| std::io::ErrorKind::PermissionDenied.into())
            })
            .map_err(|error| portable_import_error(error, &request.fingerprint))
    })
    .await
    .map_err(|error| {
        Value::String(match error {
            desktop_owner::OwnerTaskFailure::OwnerUnavailable => error.to_string(),
            desktop_owner::OwnerTaskFailure::WorkerJoinFailure => {
                "The portable import outcome is unknown. Check the original import.".to_string()
            }
        })
    })?
}

#[tauri::command]
async fn apply_change(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ApplyRequest,
) -> Result<Project, Value> {
    if matches!(
        &request.change,
        Change::UpsertSemanticGraph { .. } | Change::RemoveSemanticGraph { .. }
    ) {
        return apply_semantic_graph_change(owner, state, request).await;
    }
    let scope = checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )
    .map_err(Value::String)?;
    state
        .effect_grants
        .consume(
            request.effect_grant,
            EffectKind::ProjectEdit,
            scope,
            request.request_id.trim(),
        )
        .map_err(Value::String)?;
    let trusted_audio_evidence =
        motionwright_native::requires_qualified_audio_boundary(&request.change);
    if trusted_audio_evidence {
        return Err(Value::String(
            "Measured audio evidence can only enter through the qualified audio import/alignment boundary."
                .into(),
        ));
    }

    let service = state.service.clone();
    desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let current = project_library::open_project(
            &service,
            project_library::OpenProjectRequest {
                project_id: request.project_id,
            },
        )?;
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
    .map_err(|_| Value::String("Motionwright project mutation task failed".to_owned()))?
    .map_err(Value::String)
}

fn graph_failure_value(
    failure: Result<GraphInvokeFailure, semantic_graph_mutation_error::InvalidGraphRequestId>,
) -> Value {
    failure
        .map(|failure| failure.to_value())
        .unwrap_or_else(|error| Value::String(error.to_string()))
}

async fn apply_semantic_graph_change(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: ApplyRequest,
) -> Result<Project, Value> {
    GraphInvokeFailure::validate_request_id(&request.request_id)
        .map_err(|error| Value::String(error.to_string()))?;
    let rejected = || graph_failure_value(GraphInvokeFailure::before_dispatch(&request.request_id));
    let source = match &request.change {
        Change::UpsertSemanticGraph { graph } => graph.authorship.source_snapshot.clone(),
        Change::RemoveSemanticGraph {
            source_snapshot, ..
        } => source_snapshot.clone(),
        _ => return Err(rejected()),
    };
    let scope = checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )
    .map_err(|_| rejected())?;
    state
        .effect_grants
        .consume(
            request.effect_grant,
            EffectKind::ProjectEdit,
            scope,
            &request.request_id,
        )
        .map_err(|_| rejected())?;
    if source.project_id != request.project_id || source.generation != request.generation {
        return Err(rejected());
    }

    let service = state.service.clone();
    let connections = state.runtime_connections.clone();
    let client = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        graph_client_for_source(&service, &connections, &source, false)
    })
    .await
    .map_err(|_| rejected())?
    .map_err(|_| rejected())?;
    owner.begin_phase().map_err(|_| rejected())?;
    let operation = match &request.change {
        Change::UpsertSemanticGraph { .. } => "upsert",
        Change::RemoveSemanticGraph { .. } => "remove",
        _ => return Err(rejected()),
    };
    let attempt = state
        .graph_exchange_custody
        .begin(operation, Some(&request.request_id))
        .map_err(|_| rejected())?;
    let client = client.with_transport_custody(attempt.custody());
    let result = match &request.change {
        Change::UpsertSemanticGraph { graph } => client.upsert(&request.request_id, graph).await,
        Change::RemoveSemanticGraph {
            graph_id,
            scene_id,
            source_snapshot,
        } => {
            client
                .remove(&request.request_id, *graph_id, *scene_id, source_snapshot)
                .await
        }
        _ => return Err(rejected()),
    };
    // Observe the original transport before its custody guard is consumed.
    // Neither a returned receipt nor a local error can override uncertainty.
    let transport_failed = attempt.custody().has_unconfirmed_or_failed_invocation();
    // Retain both outer failures and complete reports before interpreting any
    // result. A local custody failure cannot certify the original mutation.
    if !matches!(
        attempt.retain_result(&result),
        GraphExchangeRetention::Published
    ) {
        return Err(graph_failure_value(GraphInvokeFailure::unknown(
            &request.request_id,
        )));
    }
    // Only the original typed Native error with confirmed transport can
    // establish knownness. An unconfirmed transport outranks even that error.
    let report = result
        .map_err(|_| graph_failure_value(GraphInvokeFailure::unknown(&request.request_id)))?;
    let receipt = report.outcome().map_err(|_| {
        graph_failure_value(GraphInvokeFailure::from_original_native_or_unknown(
            &request.request_id,
            report.original_native_error(),
            transport_failed,
        ))
    })?;
    if transport_failed {
        return Err(graph_failure_value(GraphInvokeFailure::unknown(
            &request.request_id,
        )));
    }
    // The original receipt Project is the ACK, including replay, but only
    // after original transport custody confirms its return. No later Project
    // read or second owner/effect phase can replace this exact acknowledgement.
    Ok(receipt.project().clone())
}

#[derive(Serialize)]
struct DesktopHyperframesResponse {
    attempt: hyperframes_render::AttemptView,
    preview: Option<native_preview::NativeFrameGrant>,
    preview_problem: Option<String>,
}

/// Recover only original local receipts and read original output bytes.
/// No provider, query, effect grant, render start or retry is available here.
async fn present_original_hyperframes(
    owner: &mut desktop_owner::OwnerInvocation,
    state: &AppState,
    request: hyperframes_render::ReadRequest,
) -> Result<DesktopHyperframesResponse, String> {
    let service = state.service.clone();
    let recovered = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        hyperframes_render::read_attempt(&service, &request)
    })
    .await
    .map_err(|_| "Original HyperFrames history could not be read.".to_string())??;
    owner.begin_phase()?;
    let mut response = DesktopHyperframesResponse {
        attempt: recovered.view,
        preview: None,
        preview_problem: None,
    };
    if !response.attempt.source_current {
        return Ok(response);
    }
    let (Some(evidence), Some(project), Some(identity)) = (
        recovered.evidence,
        recovered.original_project,
        recovered.connection_identity,
    ) else {
        return Ok(response);
    };
    let path = match selected_connection_path(state, project.id) {
        Ok(Some(path)) => path,
        _ => {
            response.preview_problem = Some(
                "Select the original Semwright connection to read this completed sequence.".into(),
            );
            return Ok(response);
        }
    };
    let connection = ProductionConnection::load(path).ok().filter(|connection| {
        connection.resource == project.resource_key()
            && connection
                .identity()
                .is_ok_and(|current| current == identity)
    });
    let Some(connection) = connection else {
        response.preview_problem = Some("The selected connection differs from the original render. No output path was substituted.".into());
        return Ok(response);
    };
    let registry = state.preview.clone();
    let saved = project.clone();
    let preview = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        registry.register_hyperframes(
            &connection.output_root,
            &connection.resource,
            &saved,
            &evidence,
        )
    })
    .await;
    owner.begin_phase()?;
    if state.service.project(project.id).map_err(sanitized)? != project {
        response.attempt.source_current = false;
        return Ok(response);
    }
    match preview {
        Ok(Ok(grant)) => response.preview = Some(grant),
        _ => response.preview_problem = Some(
            "The original job completed, but its PNG sequence could not be verified. Keep the original operation; rendering was not repeated.".into()),
    }
    Ok(response)
}

#[tauri::command]
async fn render_hyperframes_source(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: hyperframes_render::RenderRequest,
) -> Result<DesktopHyperframesResponse, String> {
    let _lease = state.native_renders.acquire(request.source.project_id)?;
    let connection_path =
        selected_connection_path(&state, request.source.project_id)?.ok_or_else(|| {
            "Select an owner-provisioned Semwright connection in Integrations.".to_string()
        })?;
    let connection = ProductionConnection::load(connection_path)
        .map_err(|_| "The selected Semwright connection is unavailable.".to_string())?;
    if connection.resource != format!("project:{}", request.source.project_id) {
        return Err("The connection belongs to a different project.".into());
    }
    let identity = connection
        .identity()
        .map_err(|_| "The connection identity is unavailable.".to_string())?;
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "The original Semwright connection was rejected.".to_string())?;
    let service = state.service.clone();
    let grants = state.effect_grants.clone();
    let intent = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        hyperframes_render::begin(&service, &grants, &request, &identity)
    })
    .await
    .map_err(|_| {
        "The original render admission could not be confirmed; read its retained request."
            .to_string()
    })??;
    if owner.begin_phase().is_err() {
        let result = Err(semwright_native_sdk::Error::new(
            semwright_native_sdk::ErrorCode::PermissionDenied,
            "Desktop owner retired before Native dispatch",
        ));
        hyperframes_render::finish(&state.service, &intent, &result)?;
        return Err("Desktop owner retired before Native dispatch.".into());
    }
    let result = coordinator
        .render_hyperframes_source(
            intent.source.project_id,
            &RevisionStamp::from(&intent.project),
            &intent.request_id,
            intent.source.deliverable_id,
            &intent.runtime_lock_sha256,
        )
        .await;
    // Retain the result of the already accepted invocation even if the UI closed.
    // This append cannot start another native phase or manufacture a receipt.
    hyperframes_render::finish(&state.service, &intent, &result)?;
    if result.is_err() {
        return Err("The original HyperFrames operation did not complete. Read its retained outcome; no automatic retry was made.".into());
    }
    owner.begin_phase()?;
    present_original_hyperframes(
        &mut owner,
        &state,
        hyperframes_render::ReadRequest {
            project_id: intent.source.project_id,
            generation: intent.source.generation,
            request_id: intent.request_id,
        },
    )
    .await
}

#[tauri::command]
async fn hyperframes_render_history(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: hyperframes_render::HistoryRequest,
) -> Result<hyperframes_render::RenderHistory, String> {
    let service = state.service.clone();
    let history = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        hyperframes_render::read_render_history(&service, &request)
    })
    .await
    .map_err(|_| "Saved HyperFrames history could not be read.".to_string())??;
    owner.begin_phase()?;
    Ok(history)
}

#[tauri::command]
async fn read_original_hyperframes_render(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: hyperframes_render::ReadRequest,
) -> Result<DesktopHyperframesResponse, String> {
    present_original_hyperframes(&mut owner, &state, request).await
}

#[tauri::command]
async fn observe_original_hyperframes_job(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: hyperframes_render::ReadRequest,
) -> Result<DesktopHyperframesResponse, String> {
    let _lease = state.native_renders.acquire(request.project_id)?;
    let service = state.service.clone();
    let original_request = hyperframes_render::ReadRequest {
        project_id: request.project_id,
        generation: request.generation,
        request_id: request.request_id.clone(),
    };
    let (intent, stamp) = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        let intent = hyperframes_render::prepare_job_observation(&service, &original_request)?;
        let current = service
            .project(original_request.project_id)
            .map_err(|_| "Original project is unavailable.".to_string())?;
        if current.generation != original_request.generation {
            return Err("The original project generation changed.".to_string());
        }
        Ok::<_, String>((intent, RevisionStamp::from(&current)))
    })
    .await
    .map_err(|_| "Original job preparation could not be confirmed.".to_string())??;
    owner.begin_phase()?;
    let Some(intent) = intent else {
        return present_original_hyperframes(&mut owner, &state, request).await;
    };
    let path = selected_connection_path(&state, request.project_id)?.ok_or_else(|| {
        "Select the original owner-provisioned Semwright connection to check this job.".to_string()
    })?;
    let connection = ProductionConnection::load(path)
        .map_err(|_| "The original Semwright connection is unavailable.".to_string())?;
    if connection.resource != intent.project.resource_key()
        || connection
            .identity()
            .map_err(|_| "The original connection identity is unavailable.".to_string())?
            != intent.connection_identity
    {
        return Err("The selected connection does not own the retained HyperFrames job.".into());
    }
    let coordinator = ProductionCoordinator::with_receipt_custody(
        state.service.clone(),
        connection,
        &state.production_receipt_custody,
    )
    .map_err(|_| "The original Semwright connection was rejected.".to_string())?;
    owner.begin_phase()?;
    let observed = coordinator.observe_original_hyperframes_job(&stamp, &hyperframes_render::job_binding(&intent))
        .await.map_err(|_| "The original Native job could not be authenticated or read. Its existing outcome is preserved; no render was restarted.".to_string())?;
    // Retain the authenticated observation of an already accepted invocation,
    // including when its window has since closed. This cannot dispatch Native.
    hyperframes_render::record_job_observation(&state.service, &intent, &observed)?;
    owner.begin_phase()?;
    present_original_hyperframes(&mut owner, &state, request).await
}

#[tauri::command]
async fn runtime_profile_inventory(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: runtime_profiles::Scope,
) -> Result<runtime_profiles::Inventory, String> {
    let service = state.service.clone();
    let profiles = state.runtime_profiles.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        profiles.inventory(&service, request, SEMWRIGHT_REVISION)
    })
    .await
    .map_err(|_| "Runtime inventory task failed.".to_string())??;
    owner.begin_phase()?;
    Ok(result)
}

#[tauri::command]
async fn choose_runtime_profile_bundle(
    mut owner: desktop_owner::OwnerInvocation,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    request: runtime_profiles::Scope,
) -> Result<Option<runtime_profiles::PlanView>, String> {
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    if !state.runtime_profiles.supported() {
        return Err("No offline runtime profile is admitted for this operating system and architecture. Local editing and existing supported connections remain available.".into());
    }
    let selected = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        app.dialog()
            .file()
            .set_title("Select an offline runtime package folder")
            .blocking_pick_folder()
    })
    .await
    .map_err(|_| "Runtime package picker failed.".to_string())?;
    let Some(selected) = selected else {
        return Ok(None);
    };
    owner.begin_phase()?;
    checked_effect_scope(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let path = selected
        .into_path()
        .map_err(|_| "Select a local runtime package folder.".to_string())?;
    let service = state.service.clone();
    let profiles = state.runtime_profiles.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        profiles.inspect(&service, request, &path, SEMWRIGHT_REVISION)
    })
    .await
    .map_err(|_| "Runtime package inspection task failed.".to_string())??;
    owner.begin_phase()?;
    Ok(Some(result))
}

#[tauri::command]
async fn install_runtime_profile(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: runtime_profiles::InstallRequest,
) -> Result<runtime_profiles::InstallView, runtime_profiles::RuntimeFailure> {
    let failure = runtime_profiles::RuntimeFailure::install(
        &request,
        "The runtime task reply was not confirmed. Read its original operation.",
    );
    let service = state.service.clone();
    let profiles = state.runtime_profiles.clone();
    let grants = state.effect_grants.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        profiles.install(&service, &grants, request, SEMWRIGHT_REVISION)
    })
    .await
    .map_err(|_| failure.clone())??;
    owner
        .begin_phase()
        .map_err(|message| runtime_profiles::RuntimeFailure { message, ..failure })?;
    Ok(result)
}

#[tauri::command]
async fn read_original_runtime_profile(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: runtime_profiles::ReadRequest,
) -> Result<runtime_profiles::InstallView, String> {
    let service = state.service.clone();
    let profiles = state.runtime_profiles.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        profiles.read(&service, request, SEMWRIGHT_REVISION)
    })
    .await
    .map_err(|_| "Original runtime inspection task failed.".to_string())??;
    owner.begin_phase()?;
    Ok(result)
}

#[tauri::command]
async fn rollback_runtime_profile(
    mut owner: desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: runtime_profiles::RollbackRequest,
) -> Result<runtime_profiles::InstallView, runtime_profiles::RuntimeFailure> {
    let failure = runtime_profiles::RuntimeFailure::rollback(
        &request,
        "The runtime task reply was not confirmed. Read its original operation.",
    );
    let service = state.service.clone();
    let profiles = state.runtime_profiles.clone();
    let grants = state.effect_grants.clone();
    let result = desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        profiles.rollback(&service, &grants, request, SEMWRIGHT_REVISION)
    })
    .await
    .map_err(|_| failure.clone())??;
    owner
        .begin_phase()
        .map_err(|message| runtime_profiles::RuntimeFailure { message, ..failure })?;
    Ok(result)
}

fn main() {
    tauri::Builder::default()
        .plugin(desktop_owner::lifecycle_plugin())
        .plugin(tauri_plugin_dialog::init())
        .register_asynchronous_uri_scheme_protocol(
            av_delivery::streaming::SCHEME,
            |context, request, responder| {
                let app = context.app_handle().clone();
                let native_webview_label = context.webview_label().to_owned();
                let Some(permit) = app
                    .try_state::<AppState>()
                    .and_then(|state| state.av_review.admit_read())
                else {
                    responder.respond(av_delivery::streaming::failure(
                        tauri::http::StatusCode::SERVICE_UNAVAILABLE,
                    ));
                    return;
                };
                // Admission bounds queued workers before any file IO. Native
                // owner authentication still happens inside the actual worker.
                tauri::async_runtime::spawn_blocking(move || {
                    let _permit = permit;
                    responder.respond(av_delivery::streaming::handle(
                        &app,
                        &native_webview_label,
                        request,
                    ));
                });
            },
        )
        .register_asynchronous_uri_scheme_protocol(
            asset_preview::SCHEME,
            |context, request, responder| {
                let app = context.app_handle().clone();
                let label = context.webview_label().to_owned();
                let Some(permit) = app
                    .try_state::<AppState>()
                    .and_then(|state| state.av_review.admit_read())
                else {
                    responder.respond(av_delivery::streaming::failure(
                        tauri::http::StatusCode::SERVICE_UNAVAILABLE,
                    ));
                    return;
                };
                tauri::async_runtime::spawn_blocking(move || {
                    let _permit = permit;
                    responder.respond(asset_preview_protocol::handle(&app, &label, request));
                });
            },
        )
        .setup(|app| {
            let owner = desktop_owner::DesktopOwner::new();
            app.manage(owner.clone());
            desktop_owner::validate_configuration(app)?;
            desktop_owner::record_windows_startup("config-validated");
            let data_dir = app
                .path()
                .app_data_dir()
                .map_err(|error| format!("app data path unavailable: {error}"))?;
            std::fs::create_dir_all(&data_dir)?;
            let service = StudioService::open(data_dir.join("motionwright.sqlite3"))
                .map_err(|error| format!("local project store unavailable: {error}"))?;
            desktop_owner::record_windows_startup("project-store-opened");
            // Register the existing store even when no project is selected or
            // its latest document is unreadable. The library reads its index;
            // opening or creating a project is a separate explicit operation.

            // Build the public Native SDK cooperation surface during application setup.
            // Driver Host runs the same surface through the standalone provider executable.
            build_application(service.clone())
                .map_err(|error| format!("Native SDK contract invalid: {error}"))?;
            desktop_owner::record_windows_startup("native-sdk-contract-built");

            let workspace_session = Uuid::now_v7();
            // A diagnostic build marker alone grants nothing. The optional
            // startup manifest admits custody only; all commands still require
            // the existing DesktopOwner and their ordinary effect authority.
            let development_audition_custody =
                motionwright_native::original_mix::DevelopmentAuditionCustody::from_startup(
                    option_env!("MOTIONWRIGHT_DEVELOPMENT_AUDITION_BUILD_SOURCE_SHA"),
                    SEMWRIGHT_REVISION,
                    &data_dir,
                    workspace_session,
                    &service,
                )?;
            app.manage(AppState {
                service,
                workspace_session,
                effect_grants: EffectGrantRegistry::default(),
                production_receipt_custody: ProductionReceiptCustody::default(),
                production_font_staging_custody: ProductionFontStagingCustody::default(),
                preview: NativePreviewRegistry::default(),
                av_delivery: NativeMasterDeliveryRegistry::default(),
                av_review: av_delivery::streaming::ReviewStreams::default(),
                asset_previews: asset_preview::AssetPreviews::default(),
                runtime_connections: RuntimeConnectionRegistry::new(&data_dir)?,
                runtime_profiles: runtime_profiles::RuntimeProfiles::new(&data_dir),
                native_renders: hyperframes_render::RenderRegistry::default(),
                blender_cycle_plans: blender_product_run::CyclePlans::default(),
                blender_lit_plans: blender_lit::LitPlans::default(),
                graph_exchange_custody: GraphExchangeJournal::new(&data_dir, workspace_session)?,
                development_audition_custody,
                semantic_sfx_audition: Default::default(),
            });
            desktop_owner::record_windows_startup("application-state-managed");
            desktop_owner::install_owner_window(app, &owner)?;
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
            semantic_graph_operation_recovery,
            scene_boundary_authoring_context,
            preview_branch_combination,
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
            review_signed_observer_evidence,
            runtime_profile_inventory,
            choose_runtime_profile_bundle,
            install_runtime_profile,
            read_original_runtime_profile,
            rollback_runtime_profile,
            choose_brand_font_media_root,
            clear_saved_semwright_connection,
            workflow_overview,
            workflow_action,
            workflow_stored_completion,
            registered_capture_png,
            asset_preview_open,
            asset_preview_close,
            production_jobs,
            mlt_cleanup_inventory,
            read_original_native_job_status,
            close_original_mlt_project,
            production_jobs_history,
            native_blender_product_source,
            prepare_blender_product_cycle,
            start_blender_product_cycle,
            native_blender_lit_source,
            prepare_blender_lit_still,
            start_blender_lit_still,
            read_original_blender_lit_still,
            preview_blender_lit_still,
            native_blender_product_attempt,
            native_hybrid_preflight,
            render_native_hybrid_master,
            motion_canvas_preflight,
            render_motion_canvas,
            saved_motion_capture_inventory,
            recover_saved_motion_capture,
            render_motion_canvas_window,
            preflight_multi_segment_mlt_readiness,
            assemble_av_master,
            assemble_multisegment_av_master,
            export_native_av_master,
            export_saved_native_av_master,
            inspect_private_delivery,
            prepare_private_delivery,
            commit_private_delivery,
            verify_local_media_integrity,
            review_native_av_master,
            read_native_av_audio_qc,
            native_av_audio_qc_receipt_inventory,
            native_av_saved_master_inventory,
            recover_native_av_audio_qc,
            preview_native_frame,
            prepare_file_import,
            recover_file_import,
            import_asset_file,
            import_voice_file,
            prepare_native_capsule_source_file,
            import_native_capsule_source_file,
            recover_native_capsule_source_file,
            waveform_page,
            choose_bundle_export_destination,
            export_project_bundle,
            verify_project_bundle_export,
            export_native_capsule_source,
            native_hyperframes_source,
            render_hyperframes_source,
            hyperframes_render_history,
            read_original_hyperframes_render,
            observe_original_hyperframes_job,
            export_hyperframes_source_file,
            inspect_manim_editable_source,
            export_manim_editable_source,
            inspect_attached_manim_editable_source,
            preview_manim_editable_source_edit,
            export_caption_sidecar,
            export_otio,
            export_otio_source_snapshot,
            export_portable_otio_media_bundle,
            check_otio_media_runtime,
            read_otio_bridge_status,
            choose_otio_media_bundle_folder,
            preview_otio_media_cut,
            choose_portable_project_source,
            inspect_project_bundle,
            recover_project_bundle_import,
            import_project_bundle,
            original_mix_native::native_original_mix_audition,
            narration_native::read_original_voice_audition,
            narration_native::native_narration_take_snapshot,
            narration_native::native_narration_replacement_impact,
            generation_journal::native_paid_generation_journal,
            generation_journal::native_paid_generation_preflight,
            generation_journal::native_paid_generation_reserve_preflight,
            apply_change
        ])
        .build(tauri::generate_context!())
        .expect("Motionwright desktop runtime failed")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) {
                // Repeat the existing idempotent retirement before cleanup;
                // do not depend on plugin callback ordering for the handoff.
                if let Some(owner) = app.try_state::<desktop_owner::DesktopOwner>() {
                    owner.close();
                }
                // Only an admitted, completed diagnostic audition prepares a
                // read-only Store handoff. An unmarked production build is inert.
                if let Some(state) = app.try_state::<AppState>()
                    && let Err(error) = state
                        .development_audition_custody
                        .prepare_exit_handoff(&state.service)
                {
                    eprintln!("{error}");
                }
            }
        });
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

#[cfg(test)]
mod portable_bundle_contract_tests {
    use super::ImportBundleRequest;
    use serde_json::json;
    use uuid::Uuid;

    #[test]
    fn import_requires_the_original_canonical_fingerprint() {
        let token = Uuid::new_v4();
        let base = json!({
            "path": "/synthetic-fixture/inspected.motionwright",
            "effect_grant": token,
        });
        // The invoke boundary must reject absence before its handler can
        // consume a grant or import. It never silently inspects on behalf of
        // an import that omitted its original inspected input.
        assert!(serde_json::from_value::<ImportBundleRequest>(base.clone()).is_err());
        for fingerprint in [
            json!(null),
            json!(17),
            json!("a".repeat(63)),
            json!("A".repeat(64)),
            json!(format!("{}\n", "a".repeat(64))),
        ] {
            let mut invalid = base.clone();
            invalid["fingerprint"] = fingerprint;
            assert!(serde_json::from_value::<ImportBundleRequest>(invalid).is_err());
        }
        let original = "b".repeat(64);
        let mut valid = base;
        valid["fingerprint"] = json!(original);
        let decoded: ImportBundleRequest = serde_json::from_value(valid).unwrap();
        assert_eq!(decoded.path, "/synthetic-fixture/inspected.motionwright");
        assert_eq!(decoded.effect_grant, token);
        assert_eq!(decoded.fingerprint.as_str(), original);
    }
}
