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
