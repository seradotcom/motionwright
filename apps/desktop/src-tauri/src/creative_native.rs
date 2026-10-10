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
    let skill_audit = library::preflight_creative_skills(
        &request.component,
        &request.brand,
        &request.taste,
        &contribution,
    )
    .map_err(|e| e.to_string())?;
    let fidelity_reports = [
        library::RealizationTarget::Hyperframes,
        library::RealizationTarget::Blender,
        library::RealizationTarget::MotionCanvas,
        library::RealizationTarget::MltVideo,
        library::RealizationTarget::ManimCommunity,
        library::RealizationTarget::FframesExperimental,
        library::RealizationTarget::OriginalPcmWav,
    ]
    .into_iter()
    .map(|target| library::negotiate_realization(&contribution, target))
    .collect::<std::result::Result<Vec<_>, _>>()
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
        "fidelity_reports":fidelity_reports,
        "skill_audit":skill_audit,
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
        "recipes":library::RecipeId::ALL.into_iter().map(|recipe|recipe.definition()).collect::<Vec<_>>(),
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

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalSoundAuditionRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    plan: motionwright_creative_library::SoundPlan,
    expected_source_sha256: String,
}
/// A bounded offline audition of first-party original audio. This is not a master,
/// does not read the microphone/filesystem, and does not mutate project audio.
#[tauri::command]
pub async fn creative_sound_audition(
    state: State<'_, AppState>,
    request: OriginalSoundAuditionRequest,
) -> Result<tauri::ipc::Response, String> {
    use motionwright_creative_library::{canonical_digest, write_original_wav};
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    request.plan.validate().map_err(|error| error.to_string())?;
    if request.plan.sample_frames > 48_000 * 15 || request.plan.applies_to_existing_bus {
        return Err("Audition is restricted to 15 seconds of original synthesized PCM. Silence release needs an existing authorized bus.".into());
    }
    if canonical_digest(
        &motionwright_creative_library::CreativeRealization::AudioScore(request.plan.clone()),
    )
    .map_err(|error| error.to_string())?
        != request.expected_source_sha256
    {
        return Err("Audition source changed after it was proposed.".into());
    }
    let plan = request.plan;
    let bytes = tauri::async_runtime::spawn_blocking(move || -> Result<Vec<u8>, String> {
        let mut pcm = Vec::new();
        let receipt = write_original_wav(&plan, &mut pcm).map_err(|error| error.to_string())?;
        if pcm.len() > 3 * 1024 * 1024 || receipt.file_bytes != pcm.len() as u64 {
            return Err("Original PCM audition exceeds its byte budget.".into());
        }
        Ok(pcm)
    })
    .await
    .map_err(|_| "Original WAV synthesis task failed.".to_string())??;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(tauri::ipc::Response::new(bytes))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalizedNativeRepairRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    document_id: Uuid,
    expected_source_sha256: String,
    rationale: String,
    edits: Vec<motionwright_creative_library::NativeRepairOperation>,
}
/// A repair proposal is a pure source edit. It never renders or writes project
/// data and cannot bypass the existing CAS revision and human property locks.
#[tauri::command]
pub async fn native_localized_repair_preflight(
    state: State<'_, AppState>,
    request: LocalizedNativeRepairRequest,
) -> Result<Value, String> {
    use motionwright_creative_library::propose_native_repair;
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let saved = project
        .production_design
        .workspace
        .native_scenes
        .iter()
        .find(|entry| entry.id == request.document_id)
        .ok_or_else(|| {
            "Localized repair requires an existing editable source document.".to_string()
        })?;
    let NativeSceneSource::Hyperframes(source) = &saved.source;
    let proposal = propose_native_repair(
        source,
        &request.expected_source_sha256,
        &request.rationale,
        &request.edits,
    )
    .map_err(|e| e.to_string())?;
    let mut modified = saved.clone();
    modified.source = NativeSceneSource::Hyperframes(proposal.document.clone());
    let edit = CreativeWorkspaceEdit::UpsertNativeScene {
        scene: modified,
        expected_source_sha256: Some(request.expected_source_sha256.clone()),
    };
    let difference = project
        .preview_creative_workspace(&edit)
        .map_err(|e| e.to_string())?;
    if difference.changed_nodes != proposal.changed_nodes
        || difference.source_sha256.as_deref() != Some(proposal.expected_source_sha256.as_str())
        || difference.proposed_source_sha256.as_deref()
            != Some(proposal.proposed_source_sha256.as_str())
    {
        return Err("Localized repair/source-CAS difference was not preserved by the canonical project validation.".into());
    }
    // Do not hand a stale repair back to Studio if another writer committed
    // while the purely local source comparison was in progress.
    let _latest = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.native-localized-repair-preflight/1",
        "proposal":proposal,"difference":difference,"normalized_edit":edit,
        "committed":false,"rendered":false,"requires_owner_approval":true,
        "authority":"read_only_preflight_then_explicit_existing_Native_SDK_revision"
    }))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DistillationSourceExperimentRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    authoring_intent: String,
    blueprint: motionwright_creative_library::ComponentRequest,
    brand: motionwright_creative_library::BrandProfile,
    taste: motionwright_creative_library::TasteProfile,
    source_examples: Vec<motionwright_creative_library::DistillationReference>,
    variations: Vec<motionwright_creative_library::ComponentRequest>,
}
/// Pure, versioned source-only trial, never an executable-plugin installation.
/// The project revision and every referenced media/font digest are mandatory.
#[tauri::command]
pub async fn creative_distillation_source_experiment(
    state: State<'_, AppState>,
    request: DistillationSourceExperimentRequest,
) -> Result<Value, String> {
    use motionwright_creative_library::{self as craft, DistillationDraft, canonical_digest};
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    if request.variations.len() != 9
        || request.source_examples.len() < 4
        || request.source_examples.len() > 32
    {
        return Err("A source trial requires four to 32 independent examples and exactly nine design variants.".into());
    }
    for reference in std::iter::once(&request.blueprint).chain(request.variations.iter()) {
        for asset in [&reference.primary_asset, &reference.secondary_asset]
            .into_iter()
            .flatten()
        {
            if !asset.rights.use_authorized
                || !project.assets.iter().any(|entry| {
                    entry.id == asset.id
                        && entry.content_sha256.as_deref() == Some(asset.sha256.as_str())
                })
            {
                return Err("A creative distillation media source is not an authorized digest-bound project asset.".into());
            }
        }
    }
    for source in [&request.brand.font_asset, &request.brand.logo_asset]
        .into_iter()
        .flatten()
    {
        if !source.rights.use_authorized
            || !project.assets.iter().any(|entry| {
                entry.id == source.id
                    && entry.content_sha256.as_deref() == Some(source.sha256.as_str())
            })
        {
            return Err("Distillation's custom font or logo does not belong to the current project revision.".into());
        }
    }
    let hash = canonical_digest(&(&request.blueprint, &request.brand, &request.taste))
        .map_err(|error| error.to_string())?;
    let draft = DistillationDraft {
        schema: "motionwright.recipe-distillation-draft/1".into(),
        id: request.blueprint.instance_id,
        recipe: request.blueprint.recipe,
        authoring_intent: request.authoring_intent,
        proposed_version: request.blueprint.version,
        source_examples: request.source_examples,
        color_source: request.brand,
        taste_source: request.taste,
        original_template_sha256: hash,
        request_blueprint: request.blueprint,
        owner_approved: false,
        executable_install_authorized: false,
    };
    // Explicit compute isolation. No browser, arbitrary imports or effects.
    let trial = tauri::async_runtime::spawn_blocking(move || {
        craft::experiment_source_variants(&draft, &request.variations)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| "Source-only experimental validation failed.".to_string())??;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(
        json!({"schema":"motionwright.creative-source-distillation/1",
      "trial":trial,"committed":false,"rendered":false,"installed":false,
      "owner_review":"required","authority":"read_only_source_experiment"}),
    )
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrationTakeScope {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrationSourceComparisonRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    original: motionwright_creative_library::NarrationSourceSnapshot,
    expected_original_sha256: String,
}
/// Snapshot the measured source voice/cues of the exact canonical project
/// revision, without claiming an authenticated human lock or media decode.
#[tauri::command]
pub async fn native_narration_take_snapshot(
    state: State<'_, AppState>,
    request: NarrationTakeScope,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let evidence = motionwright_creative_library::narration_source_snapshot(&project)
        .map_err(|error| error.to_string())?;
    Ok(json!({
        "schema":"motionwright.narration-source-preview/1",
        "source":evidence,"project_revision":project.revision,
        "read_only":true,"lock_authenticated":false,
        "media_decoded":false,"creative_approval":"REQUIRES_HUMAN_REVIEW"
    }))
}
/// Compare a previously returned exact source snapshot to a newer persisted
/// revision. Never accepts a source-provided execution or approval flag.
#[tauri::command]
pub async fn native_narration_replacement_impact(
    state: State<'_, AppState>,
    request: NarrationSourceComparisonRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let original = request.original;
    let source_sha = request.expected_original_sha256;
    let candidate = tauri::async_runtime::spawn_blocking(move || {
        motionwright_creative_library::propose_narration_replacement(
            &original,
            &source_sha,
            &project,
        )
        .map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| "Narration source comparison is unavailable.".to_string())??;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.narration-impact-preview/1",
        "impact":candidate,
        "applied":false,"source_locked":false,
        "owner_approval":"REQUIRED",
        "media_or_captions_rendered":false
    }))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeAttachmentInspectionRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    capsule_id: Uuid,
}
/// Read-only attach-first inspection of an existing, content-addressed asset.
/// The existing StudioService is the authority; no import, project write,
/// executable plugin, renderer or user-selected arbitrary path is accepted.
#[tauri::command]
pub async fn native_attached_source_inspection(
    state: State<'_, AppState>,
    request: NativeAttachmentInspectionRequest,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let capsule = project
        .production_design
        .capsules
        .iter()
        .find(|capsule| capsule.id == request.capsule_id)
        .ok_or_else(|| {
            "The exact native source capsule is not attached to this project.".to_string()
        })?
        .clone();
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == capsule.source_asset_id)
        .ok_or_else(|| "The native source asset is absent from the current project.".to_string())?
        .clone();
    if asset.content_sha256.as_deref() != Some(capsule.source_sha256.as_str()) {
        return Err("Native source capsule does not match the current asset fingerprint.".into());
    }
    let service = state.service.clone();
    let report=tauri::async_runtime::spawn_blocking(move ||{
        let blob=service.store().lock()
            .read_blob(&capsule.source_sha256,
                motionwright_creative_library::MAX_ATTACHED_INSPECTION_BYTES as u64)
            .map_err(|_|"The original source is absent, corrupt or exceeds the bounded 32 MiB inspection budget. Its attached project record is unchanged.".to_string())?;
        motionwright_creative_library::inspect_attached_native_source(&capsule,&asset,&blob)
            .map_err(|error|error.to_string())
    }).await.map_err(|_|"Native source inspection was interrupted; no project edit was made.".to_string())??;
    // Another client's CAS commit cannot turn an old source observation into
    // current project truth after the bounded blob read.
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.native-attachment-inspection/1",
        "observation":report,
        "project_revision":request.revision,
        "owner_source_bytes_exported":false,
        "project_changed":false,
        "operation":"read_only_attach_first",
        "owner_runtime_granted":false,
        "source_semantically_imported":false
    }))
}
