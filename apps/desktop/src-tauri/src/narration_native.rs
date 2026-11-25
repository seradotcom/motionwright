//! Original measured narration snapshots and conservative impact review.
//! Existing StudioService is sole source; no ASR, renderer or approval is run.
use crate::{AppState, sanitized};
use motionwright_domain::{
    NarrationSourceSnapshot, Project, assess_narration_timeline_dependents,
    measured_narration_content, narration_source_snapshot, propose_narration_replacement,
};
use serde::Deserialize;
use serde_json::{Value, json};
use tauri::State;
use uuid::Uuid;

fn current(
    state: &AppState,
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
) -> Result<Project, String> {
    let project = state.service.project(project_id).map_err(sanitized)?;
    if project.generation != generation || project.revision != revision {
        return Err(
            "Original narration source changed; inspect the current project revision again".into(),
        );
    }
    Ok(project)
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrationSourceScope {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiceAuditionScope {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    track_id: Uuid,
    source_sha256: String,
    owner_attests_preview_rights: bool,
    #[serde(default, deserialize_with = "present_voice_cut_profile")]
    deliverable_profile_id: Option<Uuid>,
}

fn present_voice_cut_profile<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<Uuid>, D::Error> {
    Uuid::deserialize(deserializer).map(Some)
}

#[tauri::command]
pub async fn read_original_voice_audition(
    mut owner: crate::desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: VoiceAuditionScope,
) -> Result<tauri::ipc::Response, String> {
    if !request.owner_attests_preview_rights {
        return Err("Confirm permission to listen to this exact original take locally.".into());
    }
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let expected = project.stamp();
    let service = state.service.clone();
    let project_id = request.project_id;
    let track_id = request.track_id;
    let source_sha256 = request.source_sha256;
    let profile_id = request.deliverable_profile_id;
    let bytes = crate::desktop_owner::spawn_blocking(owner.worker_owner(), move || {
        if let Some(profile_id) = profile_id {
            let cut = motionwright_native::mlt_av_audio::prepare_saved_voice_cut(
                &service,
                project_id,
                &expected,
                profile_id,
                motionwright_service::OriginalMixDecodeBudget::Audition15Seconds,
            )
            .map_err(|e| e.to_string())?;
            if cut.track_id() != track_id || cut.source_sha256() != source_sha256 {
                return Err(
                    "Voice-only audition source differs from the explicitly selected take".into(),
                );
            }
            return Ok(cut.wav_bytes().to_vec());
        }
        motionwright_native::mlt_av_audio::read_measured_voice_audition(
            &service,
            project_id,
            &expected,
            track_id,
            &source_sha256,
        )
        .map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| "Original voice listening read failed.".to_string())??;
    owner.begin_phase()?;
    current(&state, project_id, request.generation, request.revision)?;
    Ok(tauri::ipc::Response::new(bytes))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NarrationCompareScope {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    original: NarrationSourceSnapshot,
    expected_original_sha256: String,
}
#[tauri::command]
pub async fn native_narration_take_snapshot(
    _owner: crate::desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: NarrationSourceScope,
) -> Result<Value, String> {
    let p = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let source = narration_source_snapshot(&p).map_err(|e| e.to_string())?;
    let recordable = measured_narration_content(&p.audio, &p.assets).map(|(_, _, _, sha)| sha);
    // The read-only receipt never declares a legal signature or verified ASR.
    let result = json!({
        "schema":"motionwright.narration-source-preview/1",
        "source":source,"project_revision":p.revision,
        "recordable_content_sha256":recordable.as_ref().ok(),
        "recordable_source_verified":recordable.is_ok(),
        "recorded_take":p.production_design.narration_take_lock,
        "read_only":true,"lock_authenticated":false,
        "media_decoded":false,"creative_approval":"REQUIRES_HUMAN_REVIEW"
    });
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(result)
}
#[tauri::command]
pub async fn native_narration_replacement_impact(
    mut owner: crate::desktop_owner::OwnerInvocation,
    state: State<'_, AppState>,
    request: NarrationCompareScope,
) -> Result<Value, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    let original = request.original;
    let expected_sha = request.expected_original_sha256;
    let (impact, timeline) =
        crate::desktop_owner::spawn_blocking(owner.worker_owner(), move || {
            let impact = propose_narration_replacement(&original, &expected_sha, &project)
                .map_err(|e| e.to_string())?;
            let timeline = assess_narration_timeline_dependents(&original, &expected_sha, &project)
                .map_err(|e| e.to_string())?;
            Ok::<_, String>((impact, timeline))
        })
        .await
        .map_err(|_| "Narration impact calculation failed".to_string())??;
    owner.begin_phase()?;
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(json!({
        "schema":"motionwright.narration-impact-preview/1",
        "impact":impact,"timeline_review":timeline,
        "applied":false,"source_locked":false,
        "owner_approval":"REQUIRED","media_or_captions_rendered":false
    }))
}
