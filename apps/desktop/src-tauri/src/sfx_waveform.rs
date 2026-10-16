//! Read-only installed desktop original-SFX waveform from owner CAS.
//! IPC accepts ONLY project generation/revision + an authored cue ID.
//! No filesystem path, bytes, source substitution, decoder or approval.
use super::AppState;
use motionwright_native::sfx_waveform::{OriginalSfxWaveform, inspect_owner_semantic_sfx_waveform};
use serde::Deserialize;
use tauri::State;
use uuid::Uuid;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalSfxWaveformRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    cue_id: Uuid,
}

#[tauri::command]
pub async fn original_sfx_waveform(
    state: State<'_, AppState>,
    request: OriginalSfxWaveformRequest,
) -> Result<OriginalSfxWaveform, String> {
    let service = state.service.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let project = service
            .project(request.project_id)
            .map_err(|_| "Original SFX source project is unavailable.".to_owned())?;
        if project.generation != request.generation || project.revision != request.revision {
            return Err("Original SFX waveform refers to a stale creative revision.".to_owned());
        }
        let cue = project
            .audio
            .sfx_cues
            .iter()
            .find(|cue| cue.id == request.cue_id)
            .ok_or_else(|| {
                "Original SFX cue is not authored in this project revision.".to_owned()
            })?;
        let original_bytes = service
            .verified_semantic_sfx_wav(
                project.id,
                &project.stamp(),
                cue.source_asset_id,
                &cue.source_sha256,
            )
            .map_err(|_| {
                "Original SFX WAV bytes could not be verified against owner CAS.".to_owned()
            })?;
        inspect_owner_semantic_sfx_waveform(&project, request.cue_id, &original_bytes)
            .map_err(|_| "Original SFX source waveform is unsupported or unverifiable.".to_owned())
    })
    .await
    .map_err(|_| "Original SFX waveform worker was interrupted.".to_owned())?
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rejects_unknown_paths_blob_and_foreign_native_jobs_from_webview() {
        let source = json!({
            "project_id":Uuid::new_v4(),
            "generation":Uuid::new_v4(),
            "revision":11,
            "cue_id":Uuid::new_v4(),
        });
        assert!(serde_json::from_value::<OriginalSfxWaveformRequest>(source.clone()).is_ok());
        for field in [
            "path",
            "filename",
            "source_sha256",
            "bytes",
            "native_job_ref",
            "asset_id",
        ] {
            let mut foreign = source.clone();
            foreign[field] = json!("C:/private/source.wav");
            assert!(serde_json::from_value::<OriginalSfxWaveformRequest>(foreign).is_err());
        }
        let mut missing = source;
        missing.as_object_mut().unwrap().remove("cue_id");
        assert!(serde_json::from_value::<OriginalSfxWaveformRequest>(missing).is_err());
    }
}
