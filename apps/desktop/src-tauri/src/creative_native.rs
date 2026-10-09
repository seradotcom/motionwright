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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ComponentProposalRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    scene_id: Uuid,
    profile_id: Uuid,
    component: motionwright_creative_library::ComponentRequest,
    brand: motionwright_creative_library::BrandProfile,
    taste: motionwright_creative_library::TasteProfile,
}
/// Preview an authoring recipe through the same revisioned project boundary.
/// This function is pure: it neither installs a runtime nor starts a rendering job.
/// Commit, when supported, is still the existing Native SDK's EditCreativeWorkspace.
#[tauri::command]
pub async fn creative_component_proposal(
    state: State<'_, AppState>,
    request: ComponentProposalRequest,
) -> Result<Value, String> {
    use motionwright_creative_library::{self as library, CreativeRealization};
    use motionwright_domain::hyperframes_profile as hf;
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let scene = project
        .scenes
        .iter()
        .find(|scene| scene.id == request.scene_id)
        .ok_or("Creative recipe scene does not exist.")?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == request.profile_id)
        .ok_or("Creative recipe output profile does not exist.")?;
    let rate = hf::FrameRate {
        num: u32::try_from(profile.frame_rate.num).map_err(|_| "Invalid output rate numerator.")?,
        den: u32::try_from(profile.frame_rate.den)
            .map_err(|_| "Invalid output rate denominator.")?,
    };
    let frames = motionwright_native::native_html::native_frame_count(scene.duration, rate)
        .map_err(|error| error.message)?;
    let output = &request.component.output;
    if output.width != profile.width
        || output.height != profile.height
        || output.rate != rate
        || output.frames != frames
    {
        return Err("Creative recipe output does not match the exact selected scene duration, dimensions and frame rate. No silent speed change is allowed.".into());
    }
    // Original media and any custom fonts have to exist in this very project by
    // id and SHA. An owner declaration of rights is not independently verified.
    let assets = [
        request.component.primary_asset.as_ref(),
        request.component.secondary_asset.as_ref(),
        request.brand.font_asset.as_ref(),
        request.brand.logo_asset.as_ref(),
    ];
    for source in assets.into_iter().flatten() {
        if !source.rights.use_authorized
            || !project.assets.iter().any(|candidate| {
                candidate.id == source.id
                    && candidate.content_sha256.as_deref() == Some(source.sha256.as_str())
            })
        {
            return Err("Recipe media or font is not a digest-matched, explicitly authorized asset in this project.".into());
        }
    }
    let contribution = library::realize(&request.component, &request.brand, &request.taste)
        .map_err(|e| e.to_string())?;
    let mut reply = json!({
        "component_id":contribution.component_id,
        "recipe":contribution.recipe,
        "recipe_version":contribution.recipe_version,
        "input_sha256":contribution.input_sha256,
        "source_sha256":contribution.source_sha256,
        "brand_id":contribution.brand_id,
        "brand_revision":contribution.brand_revision,
        "taste_id":contribution.taste_id,
        "taste_revision":contribution.taste_revision,
        "source_classification":contribution.source_classification,
        "creative_approval":contribution.creative_approval,
        "renderer_plan":contribution.output,
        "committed":false,
        "authority":"read_only_source_proposal_not_renderer_execution"
    });
    if let CreativeRealization::NativeHtml(doc) = &contribution.output {
        let existing = project
            .production_design
            .workspace
            .native_scenes
            .iter()
            .find(|item| {
                item.scene_id == request.scene_id && item.profile_id == request.profile_id
            });
        if existing.is_some_and(|source| source.id != request.component.instance_id) {
            return Err("The selected scene/output already owns a different native source. Replace that source by an explicit reviewed edit, not by inventing a second competing realization.".into());
        }
        let scene = draft_scene(
            request.component.instance_id,
            request.scene_id,
            request.profile_id,
            contribution.recipe.definition().title.clone(),
            doc.clone(),
        );
        let edit = CreativeWorkspaceEdit::UpsertNativeScene {
            scene,
            expected_source_sha256: existing
                .map(|source| source.source.source_digest())
                .transpose()
                .map_err(|e| e.to_string())?,
        };
        let difference = project
            .preview_creative_workspace(&edit)
            .map_err(|error| error.to_string())?;
        reply["normalized_edit"] = serde_json::to_value(&edit)
            .map_err(|_| "Creative native edit serialization failed.")?;
        reply["difference"] = serde_json::to_value(difference)
            .map_err(|_| "Creative source difference serialization failed.")?;
    }
    let after = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    if project.stamp() != after.stamp() {
        return Err("Project changed while preparing the native creative recipe.".into());
    }
    Ok(reply)
}
fn draft_scene(
    id: Uuid,
    scene_id: Uuid,
    profile_id: Uuid,
    label: String,
    document: motionwright_domain::hyperframes_profile::HyperframesDocument,
) -> NativeSceneDocument {
    NativeSceneDocument {
        id,
        scene_id,
        profile_id,
        label,
        source: NativeSceneSource::Hyperframes(document),
        source_capsule_id: None,
    }
}

#[tauri::command]
pub fn creative_component_catalog() -> Value {
    use motionwright_creative_library as library;
    json!({
        "schema":"motionwright.creative-library-catalog/1",
        "recipes":library::RecipeId::ALL.map(|recipe|recipe.definition()),
        "kits":library::creative_kits(),
        "default_brand":library::BrandProfile::neutral(Uuid::from_u128(1)),
        "default_taste":library::TasteProfile::editorial(Uuid::from_u128(2)),
        "default_data":library::DataSeries::synthetic(Uuid::from_u128(66)),
        "default_copy":{
            "en":library::CopyPack::editorial(library::Locale::En),
            "es":library::CopyPack::editorial(library::Locale::Es),
            "de":library::CopyPack::editorial(library::Locale::De),
        },
        "source_authority":"pure_editable_proposal_only",
        "creative_approval":"required"
    })
}

#[tauri::command]
pub fn creative_data_normalize(
    mut data: motionwright_creative_library::DataSeries,
) -> Result<motionwright_creative_library::DataSeries, String> {
    data.content_sha256 = data.payload_digest().map_err(|error| error.to_string())?;
    data.validate().map_err(|error| error.to_string())?;
    Ok(data)
}
