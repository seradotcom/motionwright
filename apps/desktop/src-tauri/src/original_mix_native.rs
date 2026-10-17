//! Explicit owner-triggered, read-only audition of the saved canonical mix.
//! The recipe selects the three CAS sources and every mix parameter. This
//! bounded PCM preview never grants native dispatch, mastering or release.
use crate::{AppState, sanitized};
use motionwright_domain::{
    Project, RevisionStamp, VariantDependencyFingerprint, variant_dependency_fingerprint,
};
use motionwright_native::{
    assembly::preflight_multi_segment_mlt,
    film::FilmBuildOptions,
    mlt_edit_plan::mlt_native_video_recipe,
    original_mix::{OriginalMixPlan, write_mix_wav},
    production::{
        CanonicalMotionCanvasFilmSource, CanonicalOriginalMixAttemptClassification,
        CanonicalOriginalMixAttemptReason, CanonicalOriginalMixAttemptState,
        MotionCanvasRenderEvidence, ProductionConnection, ProductionCoordinator,
        canonical_original_mix_film_options, original_mix_sample_clock,
        resolve_canonical_original_mix_plan,
    },
};
use motionwright_service::StudioService;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use tauri::State;
use uuid::Uuid;

const MAX_AUDITION_BYTES: usize = 3 * 1024 * 1024;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeOriginalMixPreviewRequest {
    project_id: Uuid,
    generation: Uuid,
    revision: u64,
    deliverable_profile_id: Uuid,
    owner_attests_preview_rights: bool,
}

/// Backend-only, transient preflight result. It is not serializable to IPC,
/// does not persist another recipe and carries no execution/delivery grant.
#[derive(Debug)]
pub(crate) struct CanonicalMixPcmPreview {
    pub(crate) wav_bytes: Vec<u8>,
    pub(crate) plan: OriginalMixPlan,
    pub(crate) dependency: VariantDependencyFingerprint,
}

fn current_project(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
) -> Result<Project, String> {
    let project = service.project(project_id).map_err(sanitized)?;
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err("Original audio mix source project changed before/after decode.".into());
    }
    Ok(project)
}

pub(crate) fn recheck_canonical_mix_source(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
    dependency: &VariantDependencyFingerprint,
) -> Result<Project, String> {
    let project = current_project(service, project_id, expected)?;
    if variant_dependency_fingerprint(&project, deliverable_id).map_err(sanitized)? != *dependency {
        return Err("Saved original mix delivery dependencies changed during decode.".into());
    }
    Ok(project)
}

fn require_original_bus_audio_scope(project: &Project) -> Result<(), String> {
    if !project.audio.sfx_cues.is_empty() {
        return Err("Canonical three-bus audio preparation does not yet include authored semantic SFX cues; their source-bound preparation must be joined before audition or master dispatch.".into());
    }
    Ok(())
}

/// Match the exact options attached to the owner-registered render. A
/// different role/archetype with the same counts must fail before RenderLocal.
/// Mastering targets remain valid audition intent but are unsupported here.
pub(crate) fn canonical_mix_master_options(
    project: &Project,
    deliverable_id: Uuid,
    registered: &FilmBuildOptions,
) -> Result<FilmBuildOptions, String> {
    require_original_bus_audio_scope(project)?;
    if project.audio.mix.target_lufs.is_some() || project.audio.mix.target_true_peak_dbfs.is_some()
    {
        return Err(
            "Canonical original mix master does not yet realize saved LUFS or true-peak targets."
                .into(),
        );
    }
    let canonical = canonical_original_mix_film_options(project, deliverable_id)
        .map_err(|error| error.message)?;
    if &canonical != registered {
        return Err(
            "Registered native render options differ from the saved canonical Film recipe.".into(),
        );
    }
    Ok(canonical)
}

/// Reuse the canonical resolver and the existing mixer in memory. Both an
/// explicitly attested audition and a pre-grant master source check use this
/// one source boundary. No output file or provider command is created here.
pub(crate) fn prepare_canonical_mix_pcm_preview(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
) -> Result<CanonicalMixPcmPreview, String> {
    let project = current_project(service, project_id, expected)?;
    require_original_bus_audio_scope(&project)?;
    let dependency = variant_dependency_fingerprint(&project, deliverable_id).map_err(sanitized)?;
    let plan = resolve_canonical_original_mix_plan(service, project_id, expected, deliverable_id)
        .map_err(|error| error.message)?;
    // These IDs come exclusively from the saved recipe resolved against CAS.
    // write_mix_wav checks all decoded bytes against the canonical receipts.
    let voice = service
        .decoded_stem_for_original_mix(project_id, expected.revision, plan.measured_voice.asset_id)
        .map_err(sanitized)?;
    let music = service
        .decoded_stem_for_original_mix(project_id, expected.revision, plan.original_music.asset_id)
        .map_err(sanitized)?;
    let sfx = service
        .decoded_stem_for_original_mix(project_id, expected.revision, plan.original_sfx.asset_id)
        .map_err(sanitized)?;
    let mut wav_bytes = Vec::new();
    let evidence = write_mix_wav(
        &plan,
        &project,
        &voice.interleaved_s16le,
        &music.interleaved_s16le,
        &sfx.interleaved_s16le,
        &mut wav_bytes,
    )
    .map_err(|error| error.to_string())?;
    if wav_bytes.len() > MAX_AUDITION_BYTES
        || evidence.frames != plan.sample_frames
        || evidence.sample_rate != 48_000
        || evidence.channels != 2
        || evidence.owner_release_approved
        || evidence.listening_intelligibility_approved
        || evidence.independently_verified_lufs
        || evidence.independently_verified_true_peak
    {
        return Err(
            "Unmastered local audition exceeds its explicit source/authority budget.".into(),
        );
    }
    recheck_canonical_mix_source(service, project_id, expected, deliverable_id, &dependency)?;
    Ok(CanonicalMixPcmPreview {
        wav_bytes,
        plan,
        dependency,
    })
}

/// Application-held render selection only. This type has no IPC/serde
/// surface, and cannot replace the saved recipe or a foreground grant.
pub(crate) struct CanonicalMixMasterSource<'a> {
    pub(crate) project_id: Uuid,
    pub(crate) expected: &'a RevisionStamp,
    pub(crate) deliverable_id: Uuid,
    pub(crate) registered_options: &'a FilmBuildOptions,
    pub(crate) rendered: &'a MotionCanvasRenderEvidence,
    pub(crate) connection: &'a ProductionConnection,
}

/// Temporary source admission, not a permission or a second saved plan.
/// Staging after RenderLocal must match these independently prepared PCM bytes.
#[derive(Debug, Clone)]
pub(crate) struct CanonicalMixMasterPreflight {
    pub(crate) options: FilmBuildOptions,
    pub(crate) dependency: VariantDependencyFingerprint,
    pub(crate) frame_count: u64,
    pub(crate) source_plan_sha256: String,
    pub(crate) staged_wav_sha256: String,
    pub(crate) motion_source: Vec<CanonicalMotionCanvasFilmSource>,
    pub(crate) native_request_id: String,
    /// Original backend observation only; never serialize its raw receipts to IPC.
    pub(crate) original_attempt: CanonicalOriginalMixAttemptClassification,
}

/// Join one result from the native readonly classifier to this exact source.
/// This does not parse or classify historical rows and cannot allocate a retry.
fn require_unstarted_canonical_mix_attempt(
    project: &Project,
    deliverable_id: Uuid,
    dependency: &VariantDependencyFingerprint,
    source_plan_sha256: &str,
    frame_count: u64,
    attempt: &CanonicalOriginalMixAttemptClassification,
) -> Result<(), String> {
    if attempt.project_resource != project.resource_key()
        || attempt.generation != project.generation
        || attempt.revision != project.revision
        || attempt.deliverable_id != deliverable_id
        || attempt.canonical_dependency != *dependency
        || attempt.source_plan_sha256 != source_plan_sha256
        || attempt.frame_count != frame_count
        || attempt.evidence_scope
            != "original-application-journal-disposition-only-no-effect-grant-no-retry-no-final-media-verdict"
    {
        return Err("Original mix attempt observation belongs to another canonical source.".into());
    }
    match attempt.state {
        CanonicalOriginalMixAttemptState::NotStarted
            if attempt.outcome_known
                && attempt.reason == CanonicalOriginalMixAttemptReason::NoAssemblyReceipts =>
        {
            Ok(())
        }
        CanonicalOriginalMixAttemptState::NotStarted => Err(
            "Original mix observation does not establish an unstarted canonical attempt.".into(),
        ),
        CanonicalOriginalMixAttemptState::Uncertain => Err(
            "Original mix attempt remains unresolved; reconcile the original request before requesting another effect.".into(),
        ),
        CanonicalOriginalMixAttemptState::Completed => Err(
            "Original mix attempt already completed; reopen and inspect its original master instead of rendering again.".into(),
        ),
        CanonicalOriginalMixAttemptState::KnownFailed => Err(
            "Original mix attempt has a known failure. This path cannot allocate a new authorized attempt or replay its request key.".into(),
        ),
    }
}

/// Read actual current CAS and owner manifests, validate the exact sample
/// clock and canonical Film's original journal. This calls no native command,
/// creates no output file and consumes no effect grant.
pub(crate) fn preflight_canonical_mix_master(
    service: &StudioService,
    source: CanonicalMixMasterSource<'_>,
) -> Result<CanonicalMixMasterPreflight, String> {
    let CanonicalMixMasterSource {
        project_id,
        expected,
        deliverable_id,
        registered_options,
        rendered,
        connection,
    } = source;
    let project = current_project(service, project_id, expected)?;
    if connection.resource != project.resource_key() {
        return Err("Canonical original mix source belongs to another owner connection.".into());
    }
    let options = canonical_mix_master_options(&project, deliverable_id, registered_options)?;
    let checked = preflight_multi_segment_mlt(
        &project,
        deliverable_id,
        &options,
        rendered,
        &connection.output_root,
    )
    .map_err(|error| error.message)?;
    let recipe = mlt_native_video_recipe(&checked).map_err(|error| error.message)?;
    let samples = original_mix_sample_clock(
        recipe.total_frames,
        recipe.provider_profile.fps_num,
        recipe.provider_profile.fps_den,
    )
    .map_err(|error| error.message)?;
    let preview = prepare_canonical_mix_pcm_preview(service, project_id, expected, deliverable_id)?;
    if samples != preview.plan.sample_frames {
        return Err(
            "Canonical mix PCM and the original Film require different exact clocks.".into(),
        );
    }
    let coordinator = ProductionCoordinator::new(service.clone(), connection.clone())
        .map_err(|error| error.message)?;
    let original_attempt = coordinator
        .classify_native_canonical_original_mix_master(
            project_id,
            expected,
            deliverable_id,
            rendered,
        )
        .map_err(|error| error.message)?;
    let source_plan_sha256 = hex::encode(Sha256::digest(
        serde_json::to_vec(&preview.plan)
            .map_err(|_| "Canonical original mix plan cannot be fingerprinted.".to_string())?,
    ));
    let staged_wav_sha256 = hex::encode(Sha256::digest(&preview.wav_bytes));
    require_unstarted_canonical_mix_attempt(
        &project,
        deliverable_id,
        &preview.dependency,
        &source_plan_sha256,
        recipe.total_frames,
        &original_attempt,
    )?;
    // The classifier alone derives the native request identity and authenticates
    // the original Film chain. A caller label never resets an uncertain attempt.
    let native_request_id = original_attempt.native_request_id.clone();
    let motion_source = original_attempt.canonical_motion_source.clone();
    recheck_canonical_mix_source(
        service,
        project_id,
        expected,
        deliverable_id,
        &preview.dependency,
    )?;
    Ok(CanonicalMixMasterPreflight {
        options,
        dependency: preview.dependency,
        frame_count: recipe.total_frames,
        source_plan_sha256,
        staged_wav_sha256,
        motion_source,
        native_request_id,
        original_attempt,
    })
}

/// Call again after the real effect grant has returned and before any staging
/// or native dispatch. Reuses the same source preflight and readonly classifier;
/// a grant does not preserve a stale project, source selection or journal view.
pub(crate) fn recheck_canonical_mix_master_before_dispatch(
    service: &StudioService,
    source: CanonicalMixMasterSource<'_>,
    before_grant: &CanonicalMixMasterPreflight,
) -> Result<CanonicalMixMasterPreflight, String> {
    let current = preflight_canonical_mix_master(service, source)?;
    if !same_canonical_mix_preflight(before_grant, &current) {
        return Err("Canonical mix source or original attempt journal changed while effect authorization was pending.".into());
    }
    Ok(current)
}

fn same_canonical_mix_preflight(
    before: &CanonicalMixMasterPreflight,
    after: &CanonicalMixMasterPreflight,
) -> bool {
    before.options == after.options
        && before.dependency == after.dependency
        && before.frame_count == after.frame_count
        && before.source_plan_sha256 == after.source_plan_sha256
        && before.staged_wav_sha256 == after.staged_wav_sha256
        && before.motion_source == after.motion_source
        && before.native_request_id == after.native_request_id
        && before.original_attempt.journal_latest_receipt_id
            == after.original_attempt.journal_latest_receipt_id
        && before.original_attempt.journal_receipt_count
            == after.original_attempt.journal_receipt_count
}

fn audition_expected(
    service: &StudioService,
    request: &NativeOriginalMixPreviewRequest,
) -> Result<RevisionStamp, String> {
    if !request.owner_attests_preview_rights {
        return Err("Original music and SFX require explicit owner-provided preview-use rights attestation.".into());
    }
    let project = service.project(request.project_id).map_err(sanitized)?;
    let expected = RevisionStamp {
        resource: project.resource_key(),
        generation: request.generation,
        revision: request.revision,
    };
    current_project(service, request.project_id, &expected)?;
    Ok(expected)
}

/// An explicitly requested, at-most-15-second audition from immutable owner
/// WAV assets. The owner must save mix controls in the canonical recipe first.
/// This never commits a Change, normalizes loudness or creates a master file.
#[tauri::command]
pub async fn native_original_mix_audition(
    state: State<'_, AppState>,
    request: NativeOriginalMixPreviewRequest,
) -> Result<tauri::ipc::Response, String> {
    let expected = audition_expected(&state.service, &request)?;
    let service = state.service.clone();
    let worker_expected = expected.clone();
    let project_id = request.project_id;
    let deliverable_id = request.deliverable_profile_id;
    let preview = tauri::async_runtime::spawn_blocking(move || {
        prepare_canonical_mix_pcm_preview(&service, project_id, &worker_expected, deliverable_id)
    })
    .await
    .map_err(|_| "Original PCM mixing failed inside the bounded native worker.".to_string())??;
    // Recheck on the command boundary after the worker returns. The UI cannot
    // consume bytes built against a different generation, revision or variant.
    recheck_canonical_mix_source(
        &state.service,
        project_id,
        &expected,
        deliverable_id,
        &preview.dependency,
    )?;
    if preview.plan.project_id != project_id
        || preview.plan.generation != expected.generation
        || preview.plan.project_revision != expected.revision
        || preview.plan.deliverable_profile_id != deliverable_id
    {
        return Err("Original mix preview returned a foreign canonical source binding.".into());
    }
    Ok(tauri::ipc::Response::new(preview.wav_bytes))
}

#[cfg(test)]
pub(crate) mod test_support {
    use super::*;
    use motionwright_domain::{
        AlignmentEvidence, Change, DeliveryArchetype, DeliveryNarrativeRole, DeliveryRecipe,
        DeliveryRecipeSchema, DeliverySceneIntent, OriginalMixRecipe, RationalTime,
        TranscriptSegment,
    };
    use motionwright_service::VoiceImportMetadata;
    use std::fs;

    pub(crate) const SAMPLES: u64 = 96_000;

    pub(crate) struct MixFixture {
        pub(crate) service: StudioService,
        pub(crate) project: Project,
        pub(crate) ids: [Uuid; 3],
        pub(crate) owner_root: std::path::PathBuf,
        // SQLite is dropped before its private directory on all platforms.
        pub(crate) temporary: tempfile::TempDir,
    }

    pub(crate) fn apply(
        service: &StudioService,
        project: &Project,
        key: &str,
        change: Change,
    ) -> Project {
        service
            .apply(project.id, &project.stamp(), key, &change)
            .unwrap()
            .project
    }

    fn wave(bus: usize) -> Vec<u8> {
        // Numerical source fixtures, not speech, music quality or a native render.
        let mut pcm = Vec::with_capacity(SAMPLES as usize * 4);
        for at in 0..SAMPLES {
            let pair: [i16; 2] = match bus {
                0 if (24_000..48_000).contains(&at) => [1000, 700],
                0 => [0, 0],
                1 => [200, 100],
                _ if (72_000..96_000).contains(&at) => [900, 300],
                _ => [0, 0],
            };
            for value in pair {
                pcm.extend_from_slice(&value.to_le_bytes());
            }
        }
        let length = pcm.len() as u32;
        let mut out = Vec::with_capacity(pcm.len() + 44);
        out.extend_from_slice(b"RIFF");
        out.extend_from_slice(&(36 + length).to_le_bytes());
        out.extend_from_slice(b"WAVEfmt ");
        out.extend_from_slice(&16_u32.to_le_bytes());
        out.extend_from_slice(&1_u16.to_le_bytes());
        out.extend_from_slice(&2_u16.to_le_bytes());
        out.extend_from_slice(&48_000_u32.to_le_bytes());
        out.extend_from_slice(&192_000_u32.to_le_bytes());
        out.extend_from_slice(&4_u16.to_le_bytes());
        out.extend_from_slice(&16_u16.to_le_bytes());
        out.extend_from_slice(b"data");
        out.extend_from_slice(&length.to_le_bytes());
        out.extend_from_slice(&pcm);
        out
    }

    pub(crate) fn fixture() -> MixFixture {
        let temporary = tempfile::Builder::new()
            .prefix("desktop-canonical-mix-")
            .tempdir_in(env!("CARGO_MANIFEST_DIR"))
            .unwrap();
        let owner_root = temporary.path().join("owner-output");
        fs::create_dir(&owner_root).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&owner_root, fs::Permissions::from_mode(0o700)).unwrap();
        }
        let service = StudioService::open(temporary.path().join("studio.sqlite3")).unwrap();
        let mut project = service
            .create_project("Desktop canonical PCM fixture")
            .unwrap();
        for index in 0..2 {
            project = apply(
                &service,
                &project,
                &format!("scene-{index}"),
                Change::AddScene {
                    name: format!("Original test scene {index}"),
                    objective: "Numerical source custody and saved mix controls".into(),
                    duration_seconds: 1,
                },
            );
        }
        let files = ["voice.wav", "music.wav", "sfx.wav"].map(|name| temporary.path().join(name));
        for (bus, file) in files.iter().enumerate() {
            fs::write(file, wave(bus)).unwrap();
        }
        project = service
            .import_voice_file(
                project.id,
                &project.stamp(),
                "voice-import",
                &files[0],
                VoiceImportMetadata {
                    name: "Numerical measured voice".into(),
                    media_type: "audio/wav".into(),
                    label: "Source-only fixture, not speech".into(),
                },
            )
            .unwrap()
            .project;
        let voice = project.audio.voice_tracks[0].clone();
        let mut ids = [voice.asset_id, Uuid::nil(), Uuid::nil()];
        for bus in 1..3 {
            project = service
                .import_asset_file(
                    project.id,
                    &project.stamp(),
                    &format!("bus-{bus}"),
                    &files[bus],
                    format!("Original numerical bus {bus}"),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            ids[bus] = project.assets.last().unwrap().id;
        }
        project = apply(
            &service,
            &project,
            "reviewed-window",
            Change::AddTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::new_v4(),
                    voice_track_id: voice.id,
                    start: RationalTime { num: 1, den: 2 },
                    end: RationalTime { num: 1, den: 1 },
                    text: "Reviewed timing interval for a numerical fixture".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            },
        );
        let mut profile = project.deliverables[0].clone();
        profile.language = "en".into();
        profile.audio_sample_rate_hz = 48_000;
        profile.frame_rate = RationalTime { num: 30, den: 1 };
        profile.voice_track_id = Some(voice.id);
        profile.captions = false;
        profile.burn_in_captions = false;
        project = apply(
            &service,
            &project,
            "selected-profile",
            Change::UpsertDeliverable { profile },
        );
        let recipe = DeliveryRecipe {
            schema: DeliveryRecipeSchema::V1,
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| DeliverySceneIntent {
                    scene_id: scene.id,
                    role: Some(DeliveryNarrativeRole::Mechanism),
                    archetype: Some(DeliveryArchetype::Statement),
                })
                .collect(),
            original_mix: Some(OriginalMixRecipe {
                music_asset_id: Some(ids[1]),
                sfx_asset_id: Some(ids[2]),
                sfx_gain_db: -8.0,
                duck_attenuation_db: 14.0,
                duck_attack_samples: 2400,
                duck_release_samples: 4800,
            }),
        };
        project = apply(
            &service,
            &project,
            "saved-mix",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: project.deliverables[0].id,
                recipe: Some(recipe),
            },
        );
        // Preview must now use real CAS; the import paths no longer exist.
        for file in files {
            fs::remove_file(file).unwrap();
        }
        MixFixture {
            service,
            project,
            ids,
            owner_root,
            temporary,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::test_support::*;
    use super::*;
    use motionwright_domain::Change;
    use serde_json::json;

    fn request(project: &Project) -> serde_json::Value {
        json!({"project_id": project.id, "generation": project.generation,
            "revision": project.revision, "deliverable_profile_id": project.deliverables[0].id,
            "owner_attests_preview_rights": true})
    }

    #[test]
    fn audition_dto_rejects_parallel_mix_controls_paths_and_implicit_attestation() {
        let project = Project::new("DTO fixture").unwrap();
        let valid = request(&project);
        assert!(serde_json::from_value::<NativeOriginalMixPreviewRequest>(valid.clone()).is_ok());
        for key in [
            "music_asset_id",
            "sfx_asset_id",
            "sfx_gain_db",
            "duck_attenuation_db",
            "duck_attack_samples",
            "duck_release_samples",
            "mix_args",
            "source_path",
            "verified",
        ] {
            let mut forged = valid.clone();
            forged[key] = json!(0);
            assert!(
                serde_json::from_value::<NativeOriginalMixPreviewRequest>(forged).is_err(),
                "{key}"
            );
        }
        let mut missing = valid.clone();
        missing
            .as_object_mut()
            .unwrap()
            .remove("owner_attests_preview_rights");
        assert!(serde_json::from_value::<NativeOriginalMixPreviewRequest>(missing).is_err());
    }

    #[test]
    fn explicit_preview_attestation_and_full_stamp_are_checked_before_source_decode() {
        let f = fixture();
        let mut value = request(&f.project);
        value["owner_attests_preview_rights"] = json!(false);
        let req = serde_json::from_value::<NativeOriginalMixPreviewRequest>(value).unwrap();
        assert!(
            audition_expected(&f.service, &req)
                .unwrap_err()
                .contains("attestation")
        );
        let mut value = request(&f.project);
        value["generation"] = json!(Uuid::new_v4());
        let req = serde_json::from_value::<NativeOriginalMixPreviewRequest>(value).unwrap();
        assert!(audition_expected(&f.service, &req).is_err());
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }

    #[test]
    fn audition_reads_actual_cas_and_saved_sfx_gain_and_invalidates_the_old_stamp() {
        let mut f = fixture();
        let id = f.project.deliverables[0].id;
        let old_stamp = f.project.stamp();
        let original =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &old_stamp, id).unwrap();
        assert_eq!(&original.wav_bytes[..4], b"RIFF");
        assert_eq!(&original.wav_bytes[8..12], b"WAVE");
        assert_eq!(original.wav_bytes.len(), 44 + SAMPLES as usize * 4);
        assert_eq!(original.plan.original_music.asset_id, f.ids[1]);
        assert_eq!(original.plan.original_sfx.asset_id, f.ids[2]);
        assert_eq!(original.plan.sfx_gain_db, -8.0);
        let mut recipe = f.project.production_design.delivery_recipes[&id].clone();
        recipe.original_mix.as_mut().unwrap().sfx_gain_db = -20.0;
        f.project = apply(
            &f.service,
            &f.project,
            "saved-quieter-sfx",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: id,
                recipe: Some(recipe),
            },
        );
        assert!(
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &old_stamp, id).is_err()
        );
        assert!(
            recheck_canonical_mix_source(
                &f.service,
                f.project.id,
                &old_stamp,
                id,
                &original.dependency
            )
            .is_err()
        );
        let changed =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .unwrap();
        assert_eq!(changed.plan.sfx_gain_db, -20.0);
        let sample =
            |bytes: &[u8], at: usize| i16::from_le_bytes([bytes[44 + at * 4], bytes[45 + at * 4]]);
        let old_value = sample(&original.wav_bytes, 90_000);
        let new_value = sample(&changed.wav_bytes, 90_000);
        assert!(new_value < old_value);
        // The tail has only constant music+SFX, outside all duck attack/release
        // windows. This independent numeric expectation tests the saved gain
        // reaches actual PCM; it is not a speech/music quality measurement.
        let expected = (200.0 * 10_f64.powf(changed.plan.music_gain_db / 20.0)
            + 900.0 * 10_f64.powf(-20.0 / 20.0))
        .round() as i16;
        assert!((i32::from(new_value) - i32::from(expected)).abs() <= 1);
        assert_eq!(
            sample(&original.wav_bytes, 10_000),
            sample(&changed.wav_bytes, 10_000)
        );
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }

    #[test]
    fn master_admission_requires_exact_registered_options_and_realized_targets() {
        let f = fixture();
        let id = f.project.deliverables[0].id;
        let canonical = canonical_original_mix_film_options(&f.project, id).unwrap();
        assert!(canonical_mix_master_options(&f.project, id, &canonical).is_ok());
        let mut wrong_font = canonical.clone();
        wrong_font.font_family.push_str("-foreign");
        assert!(canonical_mix_master_options(&f.project, id, &wrong_font).is_err());
        let mut wrong_order = canonical.clone();
        wrong_order.scene_intents.reverse();
        assert!(canonical_mix_master_options(&f.project, id, &wrong_order).is_err());
        let mut wrong_role = serde_json::to_value(&canonical).unwrap();
        wrong_role["scene_intents"][0]["role"] = json!("hook");
        let wrong_role = serde_json::from_value::<FilmBuildOptions>(wrong_role).unwrap();
        assert!(canonical_mix_master_options(&f.project, id, &wrong_role).is_err());
        assert!(canonical_mix_master_options(&f.project, Uuid::new_v4(), &canonical).is_err());
        for true_peak in [false, true] {
            let mut unfinished = f.project.clone();
            if true_peak {
                unfinished.audio.mix.target_true_peak_dbfs = Some(-1.0);
            } else {
                unfinished.audio.mix.target_lufs = Some(-18.0);
            }
            assert!(canonical_mix_master_options(&unfinished, id, &canonical).is_err());
        }
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }

    fn boundary_attempt_fixture(
        f: &MixFixture,
        preview: &CanonicalMixPcmPreview,
    ) -> CanonicalOriginalMixAttemptClassification {
        // Protocol fixture for the desktop join only. It does not authenticate
        // an original journal or stand in for an actual Motion Canvas source.
        CanonicalOriginalMixAttemptClassification {
            project_resource: f.project.resource_key(),
            generation: f.project.generation,
            revision: f.project.revision,
            deliverable_id: f.project.deliverables[0].id,
            native_request_id: format!("mxav-{}", "a".repeat(36)),
            state: CanonicalOriginalMixAttemptState::NotStarted,
            reason: CanonicalOriginalMixAttemptReason::NoAssemblyReceipts,
            outcome_known: true,
            canonical_dependency: preview.dependency.clone(),
            canonical_motion_source: Vec::new(),
            source_plan_sha256: hex::encode(Sha256::digest(serde_json::to_vec(&preview.plan).unwrap())),
            frame_count: 60,
            journal_latest_receipt_id: None,
            journal_receipt_count: 0,
            selected_receipt_ids: Vec::new(),
            original_receipts: Vec::new(),
            original_terminal_receipt_id: None,
            evidence_scope: "original-application-journal-disposition-only-no-effect-grant-no-retry-no-final-media-verdict".into(),
        }
    }

    #[test]
    fn original_attempt_join_rejects_stale_source_and_all_recorded_attempt_outcomes() {
        let f = fixture();
        let id = f.project.deliverables[0].id;
        let preview =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .unwrap();
        let original = boundary_attempt_fixture(&f, &preview);
        let check = |candidate: &CanonicalOriginalMixAttemptClassification| {
            require_unstarted_canonical_mix_attempt(
                &f.project,
                id,
                &preview.dependency,
                &original.source_plan_sha256,
                60,
                candidate,
            )
        };
        assert!(check(&original).is_ok());
        for (state, reason, known, message) in [
            (
                CanonicalOriginalMixAttemptState::Uncertain,
                CanonicalOriginalMixAttemptReason::OriginalUnknown,
                false,
                "unresolved",
            ),
            (
                CanonicalOriginalMixAttemptState::Completed,
                CanonicalOriginalMixAttemptReason::OriginalMuxCompleted,
                true,
                "already completed",
            ),
            (
                CanonicalOriginalMixAttemptState::KnownFailed,
                CanonicalOriginalMixAttemptReason::OriginalKnownFailure,
                true,
                "known failure",
            ),
        ] {
            let mut observed = original.clone();
            observed.state = state;
            observed.reason = reason;
            observed.outcome_known = known;
            assert!(check(&observed).unwrap_err().contains(message));
            // A known failure remains known; the desktop does not rewrite the
            // classifier or make a new attempt identity to escape this result.
            assert_eq!(observed.state, state);
            assert_eq!(observed.outcome_known, known);
            assert_eq!(observed.native_request_id, original.native_request_id);
        }
        for field in [
            "resource",
            "generation",
            "revision",
            "deliverable",
            "plan",
            "frames",
            "scope",
            "known",
            "reason",
        ] {
            let mut changed = original.clone();
            match field {
                "resource" => changed.project_resource.push_str("-foreign"),
                "generation" => changed.generation = Uuid::new_v4(),
                "revision" => changed.revision += 1,
                "deliverable" => changed.deliverable_id = Uuid::new_v4(),
                "plan" => changed.source_plan_sha256 = "f".repeat(64),
                "frames" => changed.frame_count += 1,
                "scope" => changed.evidence_scope = "client-claimed-pass".into(),
                "known" => changed.outcome_known = false,
                "reason" => changed.reason = CanonicalOriginalMixAttemptReason::OriginalUnknown,
                _ => unreachable!(),
            }
            assert!(check(&changed).is_err(), "{field}");
        }
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }

    #[test]
    fn after_grant_join_rejects_journal_append_replacement_and_source_drift() {
        let f = fixture();
        let id = f.project.deliverables[0].id;
        let preview =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .unwrap();
        let observed = boundary_attempt_fixture(&f, &preview);
        let before = CanonicalMixMasterPreflight {
            options: canonical_original_mix_film_options(&f.project, id).unwrap(),
            dependency: preview.dependency.clone(),
            frame_count: 60,
            source_plan_sha256: observed.source_plan_sha256.clone(),
            staged_wav_sha256: hex::encode(Sha256::digest(&preview.wav_bytes)),
            motion_source: observed.canonical_motion_source.clone(),
            native_request_id: observed.native_request_id.clone(),
            original_attempt: observed,
        };
        assert!(same_canonical_mix_preflight(&before, &before));
        for field in [
            "append",
            "replacement",
            "PCM",
            "plan",
            "request",
            "frames",
            "options",
        ] {
            let mut after = before.clone();
            match field {
                "append" => after.original_attempt.journal_receipt_count += 1,
                "replacement" => {
                    after.original_attempt.journal_latest_receipt_id = Some(Uuid::new_v4())
                }
                "PCM" => after.staged_wav_sha256 = "f".repeat(64),
                "plan" => after.source_plan_sha256 = "f".repeat(64),
                "request" => after.native_request_id = format!("mxav-{}", "b".repeat(36)),
                "frames" => after.frame_count += 1,
                "options" => after.options.scene_intents.reverse(),
                _ => unreachable!(),
            }
            assert!(!same_canonical_mix_preflight(&before, &after), "{field}");
        }
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
    }

    #[test]
    fn saved_mastering_intent_remains_an_unmastered_source_audition() {
        let mut f = fixture();
        let id = f.project.deliverables[0].id;
        let initial =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .unwrap();
        let mut mix = f.project.audio.mix.clone();
        mix.target_lufs = Some(-18.0);
        mix.target_true_peak_dbfs = Some(-1.0);
        f.project = apply(
            &f.service,
            &f.project,
            "pending-mastering-targets",
            Change::SetMixIntent { mix },
        );
        let audition =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .unwrap();
        // Source PCM remains audible while the requested mastering operation
        // is explicitly unsupported. These equal bytes are not mastered media.
        assert_eq!(audition.wav_bytes, initial.wav_bytes);
        assert!(!audition.plan.is_mastered);
        assert!(!audition.plan.owner_mixing_approval_authenticated);
        let options = canonical_original_mix_film_options(&f.project, id).unwrap();
        assert!(canonical_mix_master_options(&f.project, id, &options).is_err());
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }

    #[test]
    fn authored_semantic_sfx_cannot_disappear_from_a_three_bus_audition_or_master() {
        use motionwright_domain::{SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent};
        let mut f = fixture();
        let id = f.project.deliverables[0].id;
        let original =
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .unwrap();
        let options = canonical_original_mix_film_options(&f.project, id).unwrap();
        let cue = SemanticSfxCue {
            schema: SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id: f.project.scenes[0].id,
            event: SfxEvent::SceneReveal,
            anchor: SfxAnchor::SceneStart,
            offset_frames: 0,
            source_asset_id: f.ids[2],
            source_sha256: original.plan.original_sfx.source_asset_sha256.clone(),
            source_frames: 6,
            gain_milli_db: -6_000,
            attack_frames: 0,
            release_frames: 0,
            original_source_attestation:
                "Original numerical test source; not external rights or listening acceptance."
                    .into(),
        };
        f.project = apply(
            &f.service,
            &f.project,
            "authored-semantic-cue",
            Change::UpsertSemanticSfx { cue },
        );
        f.project.validate().unwrap();
        assert!(
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id,)
                .unwrap_err()
                .contains("semantic SFX cues")
        );
        assert!(
            canonical_mix_master_options(&f.project, id, &options)
                .unwrap_err()
                .contains("semantic SFX cues")
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }

    #[test]
    fn missing_or_incomplete_saved_mix_never_uses_ui_defaults_or_voice_only() {
        let mut f = fixture();
        let id = f.project.deliverables[0].id;
        let saved = f.project.production_design.delivery_recipes[&id].clone();
        let mut incomplete = saved.clone();
        incomplete.original_mix.as_mut().unwrap().music_asset_id = None;
        f.project = apply(
            &f.service,
            &f.project,
            "incomplete-recipe",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: id,
                recipe: Some(incomplete),
            },
        );
        assert!(
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .is_err()
        );
        f.project = apply(
            &f.service,
            &f.project,
            "remove-recipe",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: id,
                recipe: None,
            },
        );
        assert!(
            prepare_canonical_mix_pcm_preview(&f.service, f.project.id, &f.project.stamp(), id)
                .is_err()
        );
        assert!(
            f.service
                .production_receipts(f.project.id, 256)
                .unwrap()
                .is_empty()
        );
        assert_eq!(std::fs::read_dir(&f.owner_root).unwrap().count(), 0);
    }
}
