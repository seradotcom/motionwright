//! Explicit owner-triggered, read-only original 3-bus audition.
//! Uses canonical StudioService blob custody and the pinned native SDK
//! consumer's pure deterministic PCM mixing; never a release master.
use crate::{AppState, sanitized};
use motionwright_domain::Project;
use serde::Deserialize;
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
        return Err("Original audio mix source project changed before/after decode.".into());
    }
    Ok(project)
}
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeOriginalMixPreviewRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_profile_id: Uuid,
    music_asset_id: Uuid,
    sfx_asset_id: Uuid,
    sfx_gain_db: f64,
    duck_attenuation_db: f64,
    duck_attack_samples: u32,
    duck_release_samples: u32,
    owner_attests_preview_rights: bool,
}
/// Explicitly requested 15-second first-party mix audition from already
/// imported immutable WAV assets. This NEVER commits a project Change, records
/// owner approval, normalizes loudness or creates a deliverable master.
#[tauri::command]
pub async fn native_original_mix_audition(
    state: State<'_, AppState>,
    request: NativeOriginalMixPreviewRequest,
) -> Result<tauri::ipc::Response, String> {
    use motionwright_domain::AlignmentEvidence;
    use motionwright_native::original_mix::{
        OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow, write_mix_wav,
    };
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    if !request.owner_attests_preview_rights {
        return Err("Original music and SFX require explicit owner-provided preview-use rights attestation.".into());
    }
    let active_id = project
        .audio
        .active_voice_track_id
        .ok_or("Import and select an original measured voice before mixing.")?;
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == active_id)
        .ok_or("The selected measured voice is no longer in this project.")?;
    if request.music_asset_id == request.sfx_asset_id
        || request.music_asset_id == track.asset_id
        || request.sfx_asset_id == track.asset_id
    {
        return Err(
            "Original voice, music and SFX sources must be separate owner-imported assets.".into(),
        );
    }
    let voice_id = track.asset_id;
    let mut windows = Vec::new();
    for segment in project
        .audio
        .transcript
        .iter()
        .filter(|segment| segment.voice_track_id == active_id)
    {
        let admitted = match &segment.alignment {
            AlignmentEvidence::Manual => true,
            AlignmentEvidence::Measured {
                confidence_millis: Some(level),
                ..
            } if *level >= 800 => true,
            _ => false,
        };
        if !admitted {
            return Err(
                "Unreviewed/low-confidence ASR is not permission to duck an original voice.".into(),
            );
        }
        let sample = |time: motionwright_domain::RationalTime| -> Result<u64, String> {
            if time.den <= 0 || time.num < 0 {
                return Err("Narration sample time is outside the source clock.".into());
            }
            let numerator = i128::from(time.num) * 48000_i128;
            let denominator = i128::from(time.den);
            if numerator % denominator != 0 {
                return Err(
                    "Narration has a fractional sample; explicit owner retiming is required."
                        .into(),
                );
            }
            u64::try_from(numerator / denominator)
                .map_err(|_| "Narration sample offset exceeds the bounded source clock.".into())
        };
        windows.push(VoiceActivityWindow {
            start_sample: sample(segment.start)?,
            end_exclusive: sample(segment.end)?,
        });
    }
    windows.sort_by_key(|window| window.start_sample);
    if windows.is_empty() {
        return Err("At least one original, manually aligned voice interval is needed to preview music ducking.".into());
    }
    let service = state.service.clone();
    let revision = request.revision;
    let project_id = request.project_id;
    let bytes = tauri::async_runtime::spawn_blocking(move || -> Result<Vec<u8>, String> {
        let voice = service
            .decoded_stem_for_original_mix(project_id, revision, voice_id)
            .map_err(sanitized)?;
        let music = service
            .decoded_stem_for_original_mix(project_id, revision, request.music_asset_id)
            .map_err(sanitized)?;
        let sfx = service
            .decoded_stem_for_original_mix(project_id, revision, request.sfx_asset_id)
            .map_err(sanitized)?;
        let stem = |id: Uuid, pcm: &motionwright_service::OriginalMixDecodedPcm| PcmStemReceipt {
            asset_id: id,
            source_asset_sha256: pcm.original_asset_sha256.clone(),
            decoded_pcm_sha256: pcm.decoded_pcm_sha256.clone(),
            sample_frames: pcm.sample_frames,
        };
        let profile = project
            .deliverables
            .iter()
            .find(|profile| profile.id == request.deliverable_profile_id)
            .ok_or_else(|| "Mix preview references a missing delivery profile.".to_string())?;
        let plan = OriginalMixPlan {
            schema: "motionwright.original-audio-mix/1".into(),
            project_id: project.id,
            generation: project.generation,
            project_revision: project.revision,
            deliverable_profile_id: profile.id,
            language: profile.language.clone(),
            sample_rate: 48000,
            channels: 2,
            sample_frames: voice.sample_frames,
            measured_voice: stem(voice_id, &voice),
            original_music: stem(request.music_asset_id, &music),
            original_sfx: stem(request.sfx_asset_id, &sfx),
            voice_gain_db: f64::from(project.audio.mix.voice_gain_db),
            music_gain_db: f64::from(project.audio.mix.music_gain_db),
            sfx_gain_db: request.sfx_gain_db,
            duck_attenuation_db: request.duck_attenuation_db,
            duck_attack_samples: request.duck_attack_samples,
            duck_release_samples: request.duck_release_samples,
            voice_windows: windows,
            sample_peak_guard_dbfs: -1.0,
            source_classification: "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED".into(),
            owner_mixing_approval_authenticated: false,
            is_mastered: false,
        };
        let mut result = Vec::new();
        let evidence = write_mix_wav(
            &plan,
            &project,
            &voice.interleaved_s16le,
            &music.interleaved_s16le,
            &sfx.interleaved_s16le,
            &mut result,
        )
        .map_err(|error| error.to_string())?;
        if result.len() > 3 * 1024 * 1024
            || evidence.frames != voice.sample_frames
            || evidence.owner_release_approved
            || evidence.independently_verified_lufs
            || evidence.independently_verified_true_peak
        {
            return Err(
                "Unmastered local audition exceeds its explicit source/authority budget.".into(),
            );
        }
        Ok(result)
    })
    .await
    .map_err(|_| "Original PCM mixing failed inside the bounded native worker.".to_string())??;
    // The owner may have edited the project while the original WAVs decoded.
    // Never deliver bytes built against another revision.
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(tauri::ipc::Response::new(bytes))
}
