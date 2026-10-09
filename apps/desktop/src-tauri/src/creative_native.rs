//! Creative native IPC uses existing revision, effect and preview registries.
use super::{AppState, EffectKind, EffectScope, NativeFrameGrant, sanitized};
use motionwright_domain::{CreativeWorkspaceEdit, NativeSceneDocument, NativeSceneSource, Project};
use motionwright_native::{
    native_html::project_canvas_to_hyperframes,
    production::{
        HyperframesJobAction, HyperframesRenderEvidence, ProductionConnection,
        ProductionCoordinator,
    },
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::path::PathBuf;
use tauri::State;
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub document_id: Uuid,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanvasProposalRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub profile_id: Uuid,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditPreviewRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub edit: CreativeWorkspaceEdit,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub document_id: Uuid,
    pub attempt_id: Uuid,
    pub request_id: String,
    pub effect_grant: Uuid,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub attempt_id: Uuid,
    pub request_id: String,
    pub action: JobAction,
    pub effect_grant: Option<Uuid>,
}
#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobAction {
    Status,
    Cancel,
    Result,
}
#[derive(Debug, Serialize)]
pub struct RenderResponse {
    #[serde(flatten)]
    pub evidence: HyperframesRenderEvidence,
    pub preview: Vec<NativeFrameGrant>,
}
fn current(state: &AppState, id: Uuid, generation: Uuid, revision: u64) -> Result<Project, String> {
    let project = state.service.project(id).map_err(sanitized)?;
    if project.generation != generation || project.revision != revision {
        return Err("Native creative operation belongs to another project revision.".into());
    }
    Ok(project)
}
fn coordinator(
    state: &AppState,
    project: &Project,
) -> Result<(ProductionCoordinator, PathBuf), String> {
    let path=std::env::var_os("MOTIONWRIGHT_SEMWRIGHT_CONNECTION").ok_or("Configure an owner-provisioned canonical Semwright connection. Opening native source never installs a runtime.")?;
    let connection = ProductionConnection::load(PathBuf::from(path))
        .map_err(|_| "Canonical connection is unavailable or untrusted.")?;
    if connection.resource != project.resource_key() {
        return Err("Canonical connection belongs to a different project.".into());
    }
    let root = connection.output_root.clone();
    Ok((
        ProductionCoordinator::new(state.service.clone(), connection).map_err(|e| e.message)?,
        root,
    ))
}
#[tauri::command]
pub async fn native_document_state(
    state: State<'_, AppState>,
    request: SourceRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let doc = project
        .production_design
        .workspace
        .native_scenes
        .iter()
        .find(|doc| doc.id == request.document_id)
        .ok_or("Native document is not attached.")?
        .clone();
    let source_sha256 = doc.source.source_digest().map_err(|e| e.to_string())?;
    Ok(
        json!({"document":doc,"source_sha256":source_sha256,"revision":project.revision,"authority":"data_only_no_runtime_grant"}),
    )
}
#[tauri::command]
pub async fn native_canvas_proposal(
    state: State<'_, AppState>,
    request: CanvasProposalRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let source = project_canvas_to_hyperframes(&project, request.scene_id, request.profile_id)
        .map_err(|e| e.message)?;
    let native = NativeSceneDocument {
        id: Uuid::new_v4(),
        scene_id: request.scene_id,
        profile_id: request.profile_id,
        label: "Native HTML realization".into(),
        source: NativeSceneSource::Hyperframes(source),
        source_capsule_id: None,
    };
    let edit = CreativeWorkspaceEdit::UpsertNativeScene {
        scene: native.clone(),
        expected_source_sha256: None,
    };
    let difference = project
        .preview_creative_workspace(&edit)
        .map_err(|e| e.to_string())?;
    Ok(
        json!({"document":native,"difference":difference,"source_preserved":true,"layout_policy":"contained_output_projection_not_semantic_reflow","committed":false}),
    )
}
#[tauri::command]
pub async fn native_workspace_preflight(
    state: State<'_, AppState>,
    request: EditPreviewRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let result = project
        .preview_creative_workspace(&request.edit)
        .map_err(|e| e.to_string())?;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({"difference":result,"normalized_edit":request.edit}))
}
#[tauri::command]
pub async fn render_hyperframes(
    state: State<'_, AppState>,
    request: RenderRequest,
) -> Result<RenderResponse, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let (coordinator, root) = coordinator(&state, &project)?;
    state.effect_grants.consume(
        request.effect_grant,
        EffectKind::RenderLocal,
        EffectScope::project(project.id, project.generation, project.revision),
        request.request_id.trim(),
    )?;
    let evidence = coordinator
        .render_hyperframes_scene(
            project.id,
            &project.stamp(),
            request.document_id,
            request.attempt_id,
        )
        .await
        .map_err(|e| e.message)?;
    let latest = current(&state, project.id, project.generation, project.revision)?;
    let registry = state.preview.clone();
    let copy = evidence.clone();
    let preview = tauri::async_runtime::spawn_blocking(move || {
        registry
            .register_hyperframes(&root, &latest, &copy)
            .into_iter()
            .collect()
    })
    .await
    .map_err(|_| "Native preview registration failed.")?;
    Ok(RenderResponse { evidence, preview })
}
#[tauri::command]
pub async fn hyperframes_job_observe(
    state: State<'_, AppState>,
    request: JobRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let (coordinator, _) = coordinator(&state, &project)?;
    if matches!(request.action, JobAction::Cancel) {
        state.effect_grants.consume(
            request
                .effect_grant
                .ok_or("Cancellation requires an explicit local production grant.")?,
            EffectKind::RenderLocal,
            EffectScope::project(project.id, project.generation, project.revision),
            request.request_id.trim(),
        )?;
    }
    let action = match request.action {
        JobAction::Status => HyperframesJobAction::Status,
        JobAction::Cancel => HyperframesJobAction::Cancel,
        JobAction::Result => HyperframesJobAction::Result,
    };
    coordinator
        .query_hyperframes_job(project.id, request.attempt_id, &request.request_id, action)
        .await
        .map_err(|e| e.message)
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RecoveryRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub document_id: Uuid,
    pub attempt_id: Uuid,
    pub request_id: String,
}
/// Read back an already completed logical attempt. This cannot submit a new render.
#[tauri::command]
pub async fn recover_hyperframes_preview(
    state: State<'_, AppState>,
    request: RecoveryRequest,
) -> Result<RenderResponse, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let (coordinator, root) = coordinator(&state, &project)?;
    let result = coordinator
        .query_hyperframes_job(
            project.id,
            request.attempt_id,
            &request.request_id,
            HyperframesJobAction::Result,
        )
        .await
        .map_err(|e| e.message)?;
    let evidence = coordinator
        .validate_hyperframes_result(project.id, request.document_id, &result)
        .map_err(|e| e.message)?;
    let latest = current(&state, project.id, project.generation, project.revision)?;
    let registry = state.preview.clone();
    let copy = evidence.clone();
    let observed = latest.clone();
    let grant = tauri::async_runtime::spawn_blocking(move || {
        registry.register_hyperframes(&root, &observed, &copy)
    })
    .await
    .map_err(|_| "Native recovery readback failed.")?
    .ok_or("Native preview evidence could not be re-registered.")?;
    let recorded = state.preview.hyperframes_source(&latest, grant.token)?;
    if recorded.source_sha256 != evidence.source_sha256 {
        return Err("Recovered native source identity changed during registration.".into());
    }
    Ok(RenderResponse {
        evidence: recorded,
        preview: vec![grant],
    })
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeProbeRequest {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub request_id: String,
}
#[tauri::command]
pub async fn hyperframes_runtime_probe(
    state: State<'_, AppState>,
    request: RuntimeProbeRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let (coordinator, _) = coordinator(&state, &project)?;
    coordinator
        .execute(
            project.id,
            &project.stamp(),
            &request.request_id,
            "driver.hyperframes.doctor",
            json!({}),
            false,
        )
        .await
        .map_err(|e| e.message)
}
