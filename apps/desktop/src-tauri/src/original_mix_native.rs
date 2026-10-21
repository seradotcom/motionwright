//! Owner-triggered audition from the persisted delivery recipe. The same
//! native PCM source factory feeds AV mastering; neither route invents speech,
//! loudness certification or human approval.
use crate::{AppState, sanitized};
use motionwright_domain::{Project, RevisionStamp};
use motionwright_native::original_mix::{PreparedOriginalMix, prepare_saved_original_mix};
use motionwright_service::StudioService;
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

/// Shared read-only preparation, before a render effect grant is consumed.
/// Existing public requests retain their shape; production intent is saved by
/// the existing SetDeliveryRecipe operation, never by an audition side effect.
pub(crate) async fn saved_mix_for_master(
    service: &StudioService,
    project: &Project,
    profile_id: Uuid,
) -> Result<Option<PreparedOriginalMix>, String> {
    let enabled = project
        .production_design
        .delivery_recipes
        .get(&profile_id)
        .and_then(|recipe| recipe.original_mix.as_ref())
        .is_some();
    if !enabled {
        if project.audio.mix.voice_gain_db != 0.0
            || project.audio.mix.target_lufs.is_some()
            || project.audio.mix.target_true_peak_dbfs.is_some()
            || !project.audio.sfx_cues.is_empty()
        {
            return Err("Save a supported source mix before mastering authored gains/SFX; unmeasured loudness targets remain unsupported.".into());
        }
        return Ok(None);
    }
    let service = service.clone();
    let project_id = project.id;
    let expected = RevisionStamp::from(project);
    tauri::async_runtime::spawn_blocking(move || {
        prepare_saved_original_mix(&service, project_id, &expected, profile_id)
            .map(Some)
            .map_err(|error| error.to_string())
    })
    .await
    .map_err(|_| {
        "Saved original mix preparation failed in its bounded native worker.".to_string()
    })?
}

/// Validate the closed audition request against the persisted recipe before
/// any PCM preparation. The command and its source tests share this boundary.
fn validate_saved_audition_request(
    project: &Project,
    request: &NativeOriginalMixPreviewRequest,
) -> Result<(), String> {
    if !request.owner_attests_preview_rights {
        return Err("Original music and SFX require explicit owner-provided preview-use rights attestation.".into());
    }
    let saved = project
        .production_design
        .delivery_recipes
        .get(&request.deliverable_profile_id)
        .and_then(|recipe| recipe.original_mix.as_ref())
        .ok_or("Save the original mix delivery recipe before auditioning.")?;
    if saved.music_asset_id != Some(request.music_asset_id)
        || saved.sfx_asset_id != Some(request.sfx_asset_id)
        || saved.sfx_gain_db != request.sfx_gain_db
        || saved.duck_attenuation_db != request.duck_attenuation_db
        || saved.duck_attack_samples != request.duck_attack_samples
        || saved.duck_release_samples != request.duck_release_samples
    {
        return Err(
            "Audition choices changed; save the delivery recipe before requesting these samples."
                .into(),
        );
    }
    Ok(())
}

#[tauri::command]
pub async fn native_original_mix_audition(
    state: State<'_, AppState>,
    request: NativeOriginalMixPreviewRequest,
) -> Result<tauri::ipc::Response, String> {
    let project = current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    validate_saved_audition_request(&project, &request)?;
    let mix = saved_mix_for_master(&state.service, &project, request.deliverable_profile_id)
        .await?
        .ok_or("Saved mix was disabled during audition preparation.")?;
    if mix.wav_bytes().len() > 3 * 1024 * 1024
        || mix.evidence().owner_release_approved
        || mix.evidence().independently_verified_lufs
        || mix.evidence().independently_verified_true_peak
    {
        return Err("Unmastered local audition exceeded its source/authority budget.".into());
    }
    current(
        &state,
        request.project_id,
        request.generation,
        request.revision,
    )?;
    Ok(tauri::ipc::Response::new(mix.wav_bytes().to_vec()))
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use motionwright_domain::{
        AlignmentEvidence, Change, DeliveryRecipe, OriginalMixRecipe, RationalTime, SemanticSfxCue,
        SfxAnchor, SfxEvent, TranscriptSegment,
    };
    use motionwright_service::VoiceImportMetadata;
    use std::fs;

    // Actual SQLite/CAS imports from the canonical native fixture; no forged
    // VoiceTrack or deserialized PreparedOriginalMix enters these controls.
    fn fixture_wav(sample: i16) -> Vec<u8> {
        let frames = 96_000_u32;
        let data_bytes = frames * 4;
        let mut out = Vec::new();
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + data_bytes).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16_u32.to_le_bytes());
        out.extend_from_slice(&1_u16.to_le_bytes());
        out.extend_from_slice(&2_u16.to_le_bytes());
        out.extend_from_slice(&48_000_u32.to_le_bytes());
        out.extend_from_slice(&192_000_u32.to_le_bytes());
        out.extend_from_slice(&4_u16.to_le_bytes());
        out.extend_from_slice(&16_u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&data_bytes.to_le_bytes());
        for _ in 0..frames * 2 {
            out.extend_from_slice(&sample.to_le_bytes());
        }
        out
    }

    fn apply_fixture_change(
        service: &StudioService,
        project: &mut Project,
        key: &str,
        change: Change,
    ) {
        *project = service
            .apply(project.id, &RevisionStamp::from(&*project), key, &change)
            .unwrap()
            .project;
    }

    pub(crate) fn saved_mix_fixture() -> (tempfile::TempDir, StudioService, Project) {
        let root = tempfile::tempdir().unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let mut project = service
            .create_project("Synthetic persisted original mix")
            .unwrap();
        apply_fixture_change(
            &service,
            &mut project,
            "scene",
            Change::AddScene {
                name: "Synthetic audio transport".into(),
                objective: "Source closure".into(),
                duration_seconds: 2,
            },
        );
        let path = root.path().join("synthetic-voice.wav");
        fs::write(&path, fixture_wav(2_000)).unwrap();
        project = service
            .import_voice_file(
                project.id,
                &RevisionStamp::from(&project),
                "voice",
                &path,
                VoiceImportMetadata {
                    name: "synthetic-voice.wav".into(),
                    media_type: "audio/wav".into(),
                    label: "Synthetic PCM proxy; not speech".into(),
                },
            )
            .unwrap()
            .project;
        let voice_id = project.audio.voice_tracks[0].id;
        apply_fixture_change(
            &service,
            &mut project,
            "active",
            Change::SetActiveVoiceTrack { track_id: voice_id },
        );
        let mut source_ids = Vec::new();
        for (label, level) in [("music", 10_000_i16), ("sfx", 1_000_i16)] {
            let path = root.path().join(format!("{label}.wav"));
            fs::write(&path, fixture_wav(level)).unwrap();
            project = service
                .import_asset_file(
                    project.id,
                    &RevisionStamp::from(&project),
                    label,
                    &path,
                    format!("{label}.wav"),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            source_ids.push(
                project
                    .assets
                    .iter()
                    .find(|asset| asset.name == format!("{label}.wav"))
                    .unwrap()
                    .id,
            );
        }
        let mut profile = project.deliverables[0].clone();
        profile.voice_track_id = Some(voice_id);
        profile.language = "en".into();
        let profile_id = profile.id;
        apply_fixture_change(
            &service,
            &mut project,
            "profile",
            Change::UpsertDeliverable { profile },
        );
        apply_fixture_change(
            &service,
            &mut project,
            "manual-window",
            Change::AddTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::new_v4(),
                    voice_track_id: voice_id,
                    start: RationalTime::new(1, 2).unwrap(),
                    end: RationalTime::new(1, 1).unwrap(),
                    text: "Synthetic authored interval; no recognized speech".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            },
        );
        apply_fixture_change(
            &service,
            &mut project,
            "save-mix",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: Some(DeliveryRecipe {
                    original_mix: Some(OriginalMixRecipe {
                        music_asset_id: Some(source_ids[0]),
                        sfx_asset_id: Some(source_ids[1]),
                        sfx_gain_db: -8.0,
                        duck_attenuation_db: 14.0,
                        duck_attack_samples: 2_400,
                        duck_release_samples: 4_800,
                    }),
                    ..Default::default()
                }),
            },
        );
        (root, service, project)
    }

    fn preview_request(project: &Project) -> NativeOriginalMixPreviewRequest {
        let profile_id = project.deliverables[0].id;
        let recipe = project.production_design.delivery_recipes[&profile_id]
            .original_mix
            .as_ref()
            .unwrap();
        NativeOriginalMixPreviewRequest {
            project_id: project.id,
            generation: project.generation,
            revision: project.revision,
            deliverable_profile_id: profile_id,
            music_asset_id: recipe.music_asset_id.unwrap(),
            sfx_asset_id: recipe.sfx_asset_id.unwrap(),
            sfx_gain_db: recipe.sfx_gain_db,
            duck_attenuation_db: recipe.duck_attenuation_db,
            duck_attack_samples: recipe.duck_attack_samples,
            duck_release_samples: recipe.duck_release_samples,
            owner_attests_preview_rights: true,
        }
    }

    #[test]
    fn shared_master_preparation_returns_exact_saved_factory_mix_without_mutation() {
        let (root, service, project) = saved_mix_fixture();
        let profile_id = project.deliverables[0].id;
        let prepared =
            tauri::async_runtime::block_on(saved_mix_for_master(&service, &project, profile_id))
                .unwrap()
                .expect("The persisted recipe must use the shared opaque factory.");
        let direct = prepare_saved_original_mix(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            profile_id,
        )
        .unwrap();
        assert_eq!(prepared.wav_bytes(), direct.wav_bytes());
        assert_eq!(prepared.wav_sha256(), direct.wav_sha256());
        assert_eq!(prepared.audio_inputs_sha256(), direct.audio_inputs_sha256());
        assert_ne!(
            prepared.wav_sha256(),
            project.audio.voice_tracks[0].source_sha256
        );
        assert!(!prepared.evidence().owner_release_approved);
        assert!(!prepared.evidence().independently_verified_lufs);
        assert!(!prepared.evidence().independently_verified_true_peak);
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[test]
    fn no_saved_recipe_preserves_only_supported_voice_path() {
        let (root, service, mut project) = saved_mix_fixture();
        let profile_id = project.deliverables[0].id;
        apply_fixture_change(
            &service,
            &mut project,
            "remove-mix-for-voice-only",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: None,
            },
        );
        assert!(
            tauri::async_runtime::block_on(saved_mix_for_master(&service, &project, profile_id))
                .unwrap()
                .is_none()
        );
        let neutral = project.audio.mix.clone();
        for index in 0..3 {
            let mut mix = neutral.clone();
            match index {
                0 => mix.voice_gain_db = -3.0,
                1 => mix.target_lufs = Some(-16.0),
                2 => mix.target_true_peak_dbfs = Some(-1.0),
                _ => unreachable!(),
            }
            apply_fixture_change(
                &service,
                &mut project,
                &format!("unsupported-voice-only-{index}"),
                Change::SetMixIntent { mix },
            );
            let error = tauri::async_runtime::block_on(saved_mix_for_master(
                &service, &project, profile_id,
            ))
            .err()
            .expect("An authored gain or target cannot silently become voice-only audio.");
            assert_eq!(
                error,
                "Save a supported source mix before mastering authored gains/SFX; unmeasured loudness targets remain unsupported."
            );
            assert_eq!(service.project(project.id).unwrap(), project);
        }
        apply_fixture_change(
            &service,
            &mut project,
            "restore-neutral-voice-only",
            Change::SetMixIntent { mix: neutral },
        );
        let asset = project
            .assets
            .iter()
            .find(|asset| asset.name == "sfx.wav")
            .unwrap();
        let cue = SemanticSfxCue {
            schema: motionwright_domain::SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id: project.scenes[0].id,
            event: SfxEvent::SceneReveal,
            anchor: SfxAnchor::SceneStart,
            offset_frames: 0,
            source_asset_id: asset.id,
            source_sha256: asset.content_sha256.clone().unwrap(),
            source_frames: 60,
            gain_milli_db: -8_000,
            attack_frames: 0,
            release_frames: 0,
            original_source_attestation: "Synthetic fixture; not a rights approval.".into(),
        };
        apply_fixture_change(
            &service,
            &mut project,
            "persist-unsupported-voice-only-cue",
            Change::UpsertSemanticSfx { cue },
        );
        let error =
            tauri::async_runtime::block_on(saved_mix_for_master(&service, &project, profile_id))
                .err()
                .expect("A saved cue must not disappear through the voice-only path.");
        assert_eq!(
            error,
            "Save a supported source mix before mastering authored gains/SFX; unmeasured loudness targets remain unsupported."
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[test]
    fn shared_master_preparation_rejects_real_stale_revision() {
        let (root, service, mut project) = saved_mix_fixture();
        let profile_id = project.deliverables[0].id;
        let captured = project.clone();
        let mut recipe = project.production_design.delivery_recipes[&profile_id].clone();
        recipe.original_mix.as_mut().unwrap().duck_attenuation_db = 6.0;
        apply_fixture_change(
            &service,
            &mut project,
            "actual-revision-after-capture",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: Some(recipe),
            },
        );
        assert!(project.revision > captured.revision);
        let error =
            tauri::async_runtime::block_on(saved_mix_for_master(&service, &captured, profile_id))
                .err()
                .expect("A captured stale Project cannot select newly persisted mix inputs.");
        assert_eq!(error, "Saved mix source revision or generation changed");
        assert_eq!(service.project(project.id).unwrap(), project);
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }

    #[test]
    fn audition_request_must_equal_current_saved_recipe_before_mixing() {
        let (root, service, mut project) = saved_mix_fixture();
        let request = preview_request(&project);
        assert!(validate_saved_audition_request(&project, &request).is_ok());
        for field in 0..6 {
            let mut changed = preview_request(&project);
            match field {
                0 => changed.music_asset_id = project.audio.voice_tracks[0].asset_id,
                1 => changed.sfx_asset_id = project.audio.voice_tracks[0].asset_id,
                2 => changed.sfx_gain_db += 1.0,
                3 => changed.duck_attenuation_db += 1.0,
                4 => changed.duck_attack_samples += 1,
                5 => changed.duck_release_samples += 1,
                _ => unreachable!(),
            }
            assert_eq!(
                validate_saved_audition_request(&project, &changed).unwrap_err(),
                "Audition choices changed; save the delivery recipe before requesting these samples."
            );
        }
        let mut no_rights = preview_request(&project);
        no_rights.owner_attests_preview_rights = false;
        assert_eq!(
            validate_saved_audition_request(&project, &no_rights).unwrap_err(),
            "Original music and SFX require explicit owner-provided preview-use rights attestation."
        );
        let mut missing_profile = preview_request(&project);
        missing_profile.deliverable_profile_id = Uuid::new_v4();
        assert_eq!(
            validate_saved_audition_request(&project, &missing_profile).unwrap_err(),
            "Save the original mix delivery recipe before auditioning."
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        let profile_id = project.deliverables[0].id;
        let mut disabled_request = preview_request(&project);
        apply_fixture_change(
            &service,
            &mut project,
            "disable-recipe-before-audition",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: profile_id,
                recipe: None,
            },
        );
        disabled_request.revision = project.revision;
        assert_eq!(
            validate_saved_audition_request(&project, &disabled_request).unwrap_err(),
            "Save the original mix delivery recipe before auditioning."
        );
        drop(service);
        let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        assert_eq!(reopened.project(project.id).unwrap(), project);
    }
}
