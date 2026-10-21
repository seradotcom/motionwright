//! Real StudioService/CAS/SQLite source closure for the existing native mixer.
//! The constant PCM inputs are labelled synthetic transport fixtures, not speech.
use motionwright_domain::{
    AlignmentEvidence, Change, DeliveryRecipe, OriginalMixRecipe, Project, RationalTime,
    RevisionStamp, TranscriptSegment,
};
use motionwright_native::original_mix::prepare_saved_original_mix;
use motionwright_service::{StudioService, VoiceImportMetadata};
use sha2::{Digest, Sha256};
use std::fs;
use uuid::Uuid;

fn wav(sample: i16) -> Vec<u8> {
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

fn apply(service: &StudioService, project: &mut Project, key: &str, change: Change) {
    *project = service
        .apply(project.id, &RevisionStamp::from(&*project), key, &change)
        .unwrap()
        .project;
}

fn fixture() -> (tempfile::TempDir, StudioService, Project) {
    let root = tempfile::tempdir().unwrap();
    let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let mut project = service
        .create_project("Synthetic persisted original mix")
        .unwrap();
    apply(
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
    fs::write(&path, wav(2_000)).unwrap();
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
    apply(
        &service,
        &mut project,
        "active",
        Change::SetActiveVoiceTrack { track_id: voice_id },
    );
    let mut source_ids = Vec::new();
    for (label, level) in [("music", 10_000_i16), ("sfx", 1_000_i16)] {
        let path = root.path().join(format!("{label}.wav"));
        fs::write(&path, wav(level)).unwrap();
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
    apply(
        &service,
        &mut project,
        "profile",
        Change::UpsertDeliverable { profile },
    );
    apply(
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
    apply(
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

fn sample(bytes: &[u8], frame: usize, channel: usize) -> i16 {
    let at = 44 + frame * 4 + channel * 2;
    i16::from_le_bytes([bytes[at], bytes[at + 1]])
}

#[test]
fn saved_recipe_and_real_cas_sources_produce_one_exact_mix_without_a_new_revision() {
    let (_root, service, project) = fixture();
    let profile_id = project.deliverables[0].id;
    let mix = prepare_saved_original_mix(
        &service,
        project.id,
        &RevisionStamp::from(&project),
        profile_id,
    )
    .unwrap();
    assert_eq!(service.project(project.id).unwrap(), project);
    assert_eq!(mix.plan().sample_frames, 96_000);
    assert_eq!(mix.wav_bytes().len(), 384_044);
    assert_eq!(
        mix.wav_sha256(),
        hex::encode(Sha256::digest(mix.wav_bytes()))
    );
    for channel in 0..2 {
        assert_eq!(sample(mix.wav_bytes(), 1_000, channel), 4_910);
        assert_eq!(sample(mix.wav_bytes(), 36_000, channel), 2_899);
        assert_eq!(sample(mix.wav_bytes(), 70_000, channel), 4_910);
    }
    assert_ne!(
        mix.wav_sha256(),
        project.audio.voice_tracks[0].source_sha256
    );
    assert!(!mix.evidence().independently_verified_lufs);
    assert!(!mix.evidence().independently_verified_true_peak);
    assert!(!mix.evidence().owner_release_approved);
    assert!(!mix.evidence().listening_intelligibility_approved);
}

#[test]
fn reopened_sqlite_recipe_reproduces_the_bytes_and_a_real_edit_invalidates_the_old_mix() {
    let (root, service, mut project) = fixture();
    let profile_id = project.deliverables[0].id;
    let expected = RevisionStamp::from(&project);
    let first = prepare_saved_original_mix(&service, project.id, &expected, profile_id).unwrap();
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let second = prepare_saved_original_mix(&reopened, project.id, &expected, profile_id).unwrap();
    assert_eq!(first.wav_bytes(), second.wav_bytes());
    assert_eq!(first.audio_inputs_sha256(), second.audio_inputs_sha256());
    let mut recipe = project.production_design.delivery_recipes[&profile_id].clone();
    recipe.original_mix.as_mut().unwrap().duck_attenuation_db = 6.0;
    apply(
        &reopened,
        &mut project,
        "change-duck",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(recipe),
        },
    );
    assert!(first.verify_project(&project, profile_id).is_err());
    assert!(prepare_saved_original_mix(&reopened, project.id, &expected, profile_id).is_err());
    let changed = prepare_saved_original_mix(
        &reopened,
        project.id,
        &RevisionStamp::from(&project),
        profile_id,
    )
    .unwrap();
    assert_ne!(first.wav_sha256(), changed.wav_sha256());
    assert_ne!(first.audio_inputs_sha256(), changed.audio_inputs_sha256());
}

#[test]
fn changed_imported_wav_is_rejected_instead_of_becoming_a_new_mix_source() {
    let (_root, service, project) = fixture();
    let profile_id = project.deliverables[0].id;
    let recipe = project.production_design.delivery_recipes[&profile_id]
        .original_mix
        .as_ref()
        .unwrap();
    let asset_id = recipe.music_asset_id.unwrap();
    let sha = project
        .assets
        .iter()
        .find(|asset| asset.id == asset_id)
        .unwrap()
        .content_sha256
        .as_ref()
        .unwrap();
    let path = service.store().lock().verified_blob_path(sha).unwrap();
    fs::write(path, wav(9_000)).unwrap();
    assert!(
        prepare_saved_original_mix(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            profile_id
        )
        .is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn requested_loudness_target_remains_unsupported_without_a_verified_meter() {
    let (_root, service, mut project) = fixture();
    let profile_id = project.deliverables[0].id;
    let mut mix = project.audio.mix.clone();
    mix.target_lufs = Some(-16.0);
    apply(
        &service,
        &mut project,
        "set-loudness",
        Change::SetMixIntent { mix },
    );
    assert!(
        prepare_saved_original_mix(
            &service,
            project.id,
            &RevisionStamp::from(&project),
            profile_id
        )
        .is_err()
    );
}
