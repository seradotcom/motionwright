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
    fixture_with_seconds(2)
}

fn fixture_with_seconds(seconds: u32) -> (tempfile::TempDir, StudioService, Project) {
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
    if seconds != 2 {
        let scene_id = project.scenes[0].id;
        apply(
            &service,
            &mut project,
            "explicit-duration",
            Change::SetSceneDuration {
                scene_id,
                duration: RationalTime::new(i64::from(seconds), 1).unwrap(),
            },
        );
    }
    let path = root.path().join("synthetic-voice.wav");
    fs::write(&path, wav_with_frames(2_000, seconds * 48_000)).unwrap();
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
        fs::write(&path, wav_with_frames(level, seconds * 48_000)).unwrap();
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
                audio_targets: None,
                original_mix: Some(OriginalMixRecipe {
                    music_asset_id: Some(source_ids[0]),
                    sfx_asset_id: Some(source_ids[1]),
                    sfx_gain_db: -8.0,
                    duck_attenuation_db: 14.0,
                    duck_attack_samples: 2_400,
                    duck_release_samples: 4_800,
                    voice_envelope: Default::default(),
                    music_envelope: Default::default(),
                    sfx_envelope: Default::default(),
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
    let original_assets = project.assets.clone();
    let original_voice_tracks = project.audio.voice_tracks.clone();
    let original_cas: Vec<_> = original_assets
        .iter()
        .map(|asset| {
            let digest = asset.content_sha256.clone().unwrap();
            let bytes = service
                .store()
                .lock()
                .read_blob(&digest, 1024 * 1024)
                .unwrap();
            assert_eq!(hex::encode(Sha256::digest(&bytes)), digest);
            (asset.id, digest, bytes)
        })
        .collect();
    let assert_original_custody = |service: &StudioService, current: &Project| {
        assert_eq!(current.assets, original_assets);
        assert_eq!(current.audio.voice_tracks, original_voice_tracks);
        for (asset_id, digest, bytes) in &original_cas {
            let asset = current
                .assets
                .iter()
                .find(|asset| asset.id == *asset_id)
                .unwrap();
            assert_eq!(asset.content_sha256.as_deref(), Some(digest.as_str()));
            let current_bytes = service
                .store()
                .lock()
                .read_blob(digest, 1024 * 1024)
                .unwrap();
            assert_eq!(&current_bytes, bytes);
            assert_eq!(hex::encode(Sha256::digest(&current_bytes)), *digest);
        }
        for original in &original_voice_tracks {
            let voice = current
                .audio
                .voice_tracks
                .iter()
                .find(|voice| voice.id == original.id)
                .unwrap();
            assert_eq!(voice.source_sha256, original.source_sha256);
            let (_, _, original_bytes) = original_cas
                .iter()
                .find(|(_, digest, _)| digest == &original.source_sha256)
                .unwrap();
            let current_bytes = service
                .store()
                .lock()
                .read_blob(&voice.source_sha256, 1024 * 1024)
                .unwrap();
            assert_eq!(&current_bytes, original_bytes);
        }
    };
    assert_original_custody(&service, &project);
    let expected = RevisionStamp::from(&project);
    let first = prepare_saved_original_mix(&service, project.id, &expected, profile_id).unwrap();
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let second = prepare_saved_original_mix(&reopened, project.id, &expected, profile_id).unwrap();
    assert_eq!(first.wav_bytes(), second.wav_bytes());
    assert_eq!(first.audio_inputs_sha256(), second.audio_inputs_sha256());
    assert_original_custody(&reopened, &reopened.project(project.id).unwrap());
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
    assert_ne!(first.wav_bytes(), changed.wav_bytes());
    assert_ne!(first.audio_inputs_sha256(), changed.audio_inputs_sha256());
    assert_original_custody(&reopened, &project);
    assert_original_custody(&reopened, &reopened.project(project.id).unwrap());

    // Reconstruct the edited derived audio from the durable recipe after close/reopen.
    // The original Asset and VoiceTrack identities and their CAS bytes stay intact.
    drop(reopened);
    let edited_reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let persisted = edited_reopened.project(project.id).unwrap();
    assert_eq!(persisted, project);
    assert_original_custody(&edited_reopened, &persisted);
    let edited_mix = prepare_saved_original_mix(
        &edited_reopened,
        persisted.id,
        &persisted.stamp(),
        profile_id,
    )
    .unwrap();
    assert_eq!(edited_mix.wav_bytes(), changed.wav_bytes());
    assert_eq!(edited_mix.wav_sha256(), changed.wav_sha256());
    assert_ne!(edited_mix.wav_sha256(), first.wav_sha256());
    assert_original_custody(
        &edited_reopened,
        &edited_reopened.project(project.id).unwrap(),
    );
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

#[test]
fn saved_bus_mute_and_sample_fades_reopen_exactly_and_keep_original_source_custody() {
    let (root, service, mut project) = fixture();
    let profile_id = project.deliverables[0].id;
    let assets = project.assets.clone();
    let voice_tracks = project.audio.voice_tracks.clone();
    let mut recipe = project.production_design.delivery_recipes[&profile_id].clone();
    let mix = recipe.original_mix.as_mut().unwrap();
    mix.voice_envelope = motionwright_domain::OriginalMixBusEnvelope {
        muted: false,
        fade_in_samples: 4,
        fade_out_samples: 4,
    };
    mix.music_envelope.muted = true;
    mix.sfx_envelope.muted = true;
    apply(
        &service,
        &mut project,
        "save-explicit-bus-envelopes",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(recipe.clone()),
        },
    );
    let expected = RevisionStamp::from(&project);
    let first = prepare_saved_original_mix(&service, project.id, &expected, profile_id).unwrap();
    for (at, expected) in [
        (0, 0),
        (1, 500),
        (2, 1000),
        (4, 2000),
        (95_998, 500),
        (95_999, 0),
    ] {
        assert_eq!(sample(first.wav_bytes(), at, 0), expected);
        assert_eq!(sample(first.wav_bytes(), at, 1), expected);
    }
    assert_eq!(project.assets, assets);
    assert_eq!(project.audio.voice_tracks, voice_tracks);
    assert_eq!(
        first.evidence().input_source_checks,
        "ALL_THREE_FULL_PCM_SHA256_VERIFIED"
    );
    let mut forged = first.plan().clone();
    forged.music_envelope.muted = false;
    assert!(forged.validate(&project).is_err()); // local plan cannot override saved mute
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let persisted = reopened.project(project.id).unwrap();
    assert_eq!(persisted, project);
    assert_eq!(
        persisted.production_design.delivery_recipes[&profile_id],
        recipe
    );
    let second = prepare_saved_original_mix(&reopened, project.id, &expected, profile_id).unwrap();
    assert_eq!(first.wav_bytes(), second.wav_bytes());
    assert_eq!(first.plan(), second.plan());
    recipe.original_mix.as_mut().unwrap().voice_envelope.muted = true;
    apply(
        &reopened,
        &mut project,
        "mute-voice-explicitly",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(recipe),
        },
    );
    assert!(first.verify_project(&project, profile_id).is_err());
    let all_muted = prepare_saved_original_mix(
        &reopened,
        project.id,
        &RevisionStamp::from(&project),
        profile_id,
    )
    .unwrap();
    assert!(all_muted.wav_bytes()[44..].iter().all(|byte| *byte == 0));
    assert_eq!(project.assets, assets);
    assert_eq!(project.audio.voice_tracks, voice_tracks);
    assert!(!all_muted.evidence().independently_verified_lufs);
}

fn wav_with_frames(level: i16, frames: u32) -> Vec<u8> {
    let mut bytes = wav(level);
    bytes.truncate(44);
    let size = frames * 4;
    bytes[4..8].copy_from_slice(&(36 + size).to_le_bytes());
    bytes[40..44].copy_from_slice(&size.to_le_bytes());
    for _ in 0..frames * 2 {
        bytes.extend_from_slice(&level.to_le_bytes());
    }
    bytes
}

fn semantic_fixture(imported_base: bool) -> (tempfile::TempDir, StudioService, Project) {
    let (root, service, mut project) = fixture();
    let first = project.scenes[0].id;
    apply(
        &service,
        &mut project,
        "first-cut",
        Change::SetSceneDuration {
            scene_id: first,
            duration: RationalTime::new(1, 1).unwrap(),
        },
    );
    apply(
        &service,
        &mut project,
        "second-cut",
        Change::AddScene {
            name: "Tail continuation".into(),
            objective: "Original complete cue tail".into(),
            duration_seconds: 1,
        },
    );
    let source = wav_with_frames(1_000, 48_000);
    let path = root.path().join("source-cue.wav");
    fs::write(&path, &source).unwrap();
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "import-cue",
            &path,
            "source-cue.wav".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let source_asset_id = project
        .assets
        .iter()
        .find(|asset| asset.name == "source-cue.wav")
        .unwrap()
        .id;
    apply(
        &service,
        &mut project,
        "author-tail",
        Change::UpsertSemanticSfx {
            cue: motionwright_domain::SemanticSfxCue {
                schema: motionwright_domain::SFX_CUE_SCHEMA.into(),
                id: Uuid::now_v7(),
                scene_id: first,
                event: motionwright_domain::SfxEvent::Transition,
                anchor: motionwright_domain::SfxAnchor::SceneStart,
                offset_frames: 15,
                source_asset_id,
                source_sha256: hex::encode(Sha256::digest(&source)),
                source_frames: 30,
                gain_milli_db: 0,
                attack_frames: 0,
                release_frames: 0,
                tail_policy: motionwright_domain::SfxTailPolicy::ContinueAcrossCuts,
                original_source_attestation:
                    "Owned synthetic constant PCM, not real speech or measured loudness".into(),
            },
        },
    );
    let profile_id = project.deliverables[0].id;
    let mut recipe = project.production_design.delivery_recipes[&profile_id].clone();
    let mix = recipe.original_mix.as_mut().unwrap();
    if !imported_base {
        mix.sfx_asset_id = None;
    }
    mix.sfx_gain_db = 0.0;
    mix.voice_envelope.muted = true;
    mix.music_envelope.muted = true;
    apply(
        &service,
        &mut project,
        "isolate-sfx-bus",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(recipe),
        },
    );
    (root, service, project)
}

#[test]
fn semantic_cues_and_imported_sfx_base_are_both_audible_across_the_exact_cut() {
    let (_root, service, project) = semantic_fixture(true);
    let mix = prepare_saved_original_mix(
        &service,
        project.id,
        &project.stamp(),
        project.deliverables[0].id,
    )
    .unwrap();
    for (frame, value) in [
        (0, 1_000),
        (23_999, 1_000),
        (24_000, 2_000),
        (47_999, 2_000),
        (48_000, 2_000),
        (48_001, 2_000),
        (71_999, 2_000),
        (72_000, 1_000),
        (95_999, 1_000),
    ] {
        for channel in 0..2 {
            assert_eq!(sample(mix.wav_bytes(), frame, channel), value);
        }
    }
    let plan = serde_json::to_value(mix.plan()).unwrap();
    assert_eq!(plan["schema"], "motionwright.original-audio-mix/2");
    assert_eq!(
        plan["original_sfx"]["kind"],
        "motionwright.semantic-sfx-bus/1"
    );
    assert_eq!(plan["original_sfx"]["sources"].as_array().unwrap().len(), 1);
    assert_eq!(
        plan["original_sfx"]["placements"][0]["first_sample"],
        24_000
    );
    assert_eq!(
        plan["original_sfx"]["placements"][0]["sample_count"],
        48_000
    );
    assert!(!plan["original_sfx"]["imported_base"].is_null());
    assert!(
        serde_json::from_value::<motionwright_native::original_mix::OriginalMixPlan>(plan).is_err()
    );
    assert_eq!(service.project(project.id).unwrap(), project);
}

#[test]
fn semantic_only_sfx_bus_creates_no_asset_or_voice_and_empty_bus_is_not_fabricated() {
    let (_root, service, mut project) = semantic_fixture(false);
    let profile_id = project.deliverables[0].id;
    let before = project.clone();
    let mix =
        prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id).unwrap();
    for (frame, value) in [
        (0, 0),
        (23_999, 0),
        (24_000, 1_000),
        (48_000, 1_000),
        (71_999, 1_000),
        (72_000, 0),
    ] {
        for channel in 0..2 {
            assert_eq!(sample(mix.wav_bytes(), frame, channel), value);
        }
    }
    assert_eq!(service.project(project.id).unwrap(), before);
    assert_eq!(mix.plan().original_sfx.imported_asset_id(), None);
    let cue_id = project.audio.sfx_cues[0].id;
    apply(
        &service,
        &mut project,
        "remove-only-sfx-source",
        Change::RemoveSemanticSfx { cue_id },
    );
    let error = prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id)
        .err()
        .unwrap();
    assert!(error.to_string().contains("no invented silent source"));
}

#[test]
fn semantic_bus_sample_fades_and_mute_keep_all_selected_source_checks() {
    let (_root, service, mut project) = semantic_fixture(true);
    let profile_id = project.deliverables[0].id;
    let mut recipe = project.production_design.delivery_recipes[&profile_id].clone();
    let envelope = &mut recipe.original_mix.as_mut().unwrap().sfx_envelope;
    envelope.fade_in_samples = 4;
    envelope.fade_out_samples = 4;
    apply(
        &service,
        &mut project,
        "fade-complete-sfx-bus",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(recipe.clone()),
        },
    );
    let mix =
        prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id).unwrap();
    for (frame, value) in [
        (0, 0),
        (1, 250),
        (2, 500),
        (4, 1_000),
        (48_000, 2_000),
        (95_998, 250),
        (95_999, 0),
    ] {
        for channel in 0..2 {
            assert_eq!(sample(mix.wav_bytes(), frame, channel), value);
        }
    }
    recipe.original_mix.as_mut().unwrap().sfx_envelope.muted = true;
    apply(
        &service,
        &mut project,
        "mute-complete-sfx-bus",
        Change::SetDeliveryRecipe {
            deliverable_profile_id: profile_id,
            recipe: Some(recipe),
        },
    );
    let muted =
        prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id).unwrap();
    assert!(muted.wav_bytes()[44..].iter().all(|byte| *byte == 0));
    let cue = &project.audio.sfx_cues[0];
    let path = service
        .store()
        .lock()
        .verified_blob_path(&cue.source_sha256)
        .unwrap();
    fs::write(
        path,
        b"changed selected source even though its bus is muted",
    )
    .unwrap();
    assert!(matches!(
        prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id),
        Err(motionwright_native::original_mix::MixError::StaleReference(
            _
        ))
    ));
}

#[test]
fn semantic_bus_reopens_from_sqlite_and_bundle_with_same_pcm_and_new_generation_authority() {
    let (root, service, project) = semantic_fixture(true);
    let profile_id = project.deliverables[0].id;
    let first =
        prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id).unwrap();
    let original_events = service.export_project(project.id).unwrap().events;
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    assert_eq!(reopened.project(project.id).unwrap(), project);
    let second =
        prepare_saved_original_mix(&reopened, project.id, &project.stamp(), profile_id).unwrap();
    assert_eq!(first.wav_bytes(), second.wav_bytes());
    assert_eq!(first.plan(), second.plan());
    assert_eq!(
        reopened.export_project(project.id).unwrap().events,
        original_events
    );
    let bundle = root.path().join("semantic-bus-bundle");
    reopened.export_project_bundle(project.id, &bundle).unwrap();
    let destination = tempfile::tempdir().unwrap();
    let database = destination.path().join("restored.sqlite3");
    let restored = StudioService::open(&database).unwrap();
    restored.inspect_project_bundle(&bundle).unwrap();
    let imported = restored.import_project_bundle(&bundle).unwrap();
    assert_ne!(imported.generation, project.generation);
    assert_eq!(imported.audio, project.audio);
    assert_eq!(imported.assets, project.assets);
    assert_eq!(imported.production_design, project.production_design);
    assert!(matches!(
        first.verify_project(&imported, profile_id),
        Err(motionwright_native::original_mix::MixError::StaleReference(
            _
        ))
    ));
    let remixed =
        prepare_saved_original_mix(&restored, imported.id, &imported.stamp(), profile_id).unwrap();
    assert_eq!(first.wav_bytes(), remixed.wav_bytes());
    assert_ne!(
        first.evidence().source_plan_sha256,
        remixed.evidence().source_plan_sha256
    );
    drop(restored);
    assert_eq!(
        StudioService::open(database)
            .unwrap()
            .project(imported.id)
            .unwrap(),
        imported
    );
}

#[test]
fn imported_only_plan_wire_remains_legacy_and_cannot_drop_authored_cues() {
    let (_root, service, mut project) = fixture();
    let profile_id = project.deliverables[0].id;
    let mix =
        prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id).unwrap();
    let json = serde_json::to_value(mix.plan()).unwrap();
    assert_eq!(json["schema"], "motionwright.original-audio-mix/1");
    assert!(json["original_sfx"].get("kind").is_none());
    assert_eq!(json["original_sfx"].as_object().unwrap().len(), 4);
    let read: motionwright_native::original_mix::OriginalMixPlan =
        serde_json::from_value(json.clone()).unwrap();
    assert_eq!(serde_json::to_value(read).unwrap(), json);
    let mut fake = mix.plan().clone();
    let source = project.audio.voice_tracks[0].clone();
    let scene_id = project.scenes[0].id;
    apply(
        &service,
        &mut project,
        "author-existing-owned-audio-cue",
        Change::UpsertSemanticSfx {
            cue: motionwright_domain::SemanticSfxCue {
                schema: motionwright_domain::SFX_CUE_SCHEMA.into(),
                id: Uuid::now_v7(),
                scene_id,
                event: motionwright_domain::SfxEvent::SceneReveal,
                anchor: motionwright_domain::SfxAnchor::SceneStart,
                offset_frames: 0,
                source_asset_id: source.asset_id,
                source_sha256: source.source_sha256,
                source_frames: 60,
                gain_milli_db: -36_000,
                attack_frames: 0,
                release_frames: 0,
                tail_policy: Default::default(),
                original_source_attestation: "Owned synthetic test source".into(),
            },
        },
    );
    fake.project_revision = project.revision;
    assert!(
        fake.validate(&project)
            .unwrap_err()
            .to_string()
            .contains("cannot replace semantic cues")
    );
}

#[test]
fn full_cut_policy_prepares_sixteen_seconds_while_default_audition_stays_bounded() {
    use motionwright_native::original_mix::prepare_saved_original_mix_with_budget;
    use motionwright_service::OriginalMixDecodeBudget;
    let (root, service, project) = fixture_with_seconds(16);
    let profile_id = project.deliverables[0].id;
    let before = service.export_project(project.id).unwrap();
    let error = prepare_saved_original_mix(&service, project.id, &project.stamp(), profile_id)
        .err()
        .unwrap();
    assert_eq!(
        error.into_native().code,
        semwright_native_sdk::ErrorCode::Unsupported
    );
    let full = prepare_saved_original_mix_with_budget(
        &service,
        project.id,
        &project.stamp(),
        profile_id,
        OriginalMixDecodeBudget::FullCut180Seconds,
    )
    .unwrap();
    assert_eq!(full.plan().sample_frames, 16 * 48_000);
    assert_eq!(full.wav_bytes().len(), 44 + 16 * 48_000 * 4);
    assert!(full.plan().validate(&project).is_err());
    full.plan()
        .validate_with_budget(&project, OriginalMixDecodeBudget::FullCut180Seconds)
        .unwrap();
    for channel in 0..2 {
        assert_eq!(sample(full.wav_bytes(), 1_000, channel), 4_910);
        assert_eq!(sample(full.wav_bytes(), 36_000, channel), 2_899);
        assert_eq!(sample(full.wav_bytes(), 16 * 48_000 - 1, channel), 4_910);
    }
    let after = service.export_project(project.id).unwrap();
    assert_eq!(after.project, before.project);
    assert_eq!(after.events, before.events);
    drop(service);
    let reopened = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let second = prepare_saved_original_mix_with_budget(
        &reopened,
        project.id,
        &project.stamp(),
        profile_id,
        OriginalMixDecodeBudget::FullCut180Seconds,
    )
    .unwrap();
    assert_eq!(second.wav_bytes(), full.wav_bytes());
    assert_eq!(second.plan(), full.plan());
    assert!(
        prepare_saved_original_mix(&reopened, project.id, &project.stamp(), profile_id).is_err()
    );
}

#[test]
fn additive_semantic_bus_clipping_is_rejected_without_dropping_base_or_cues() {
    let (root, service, mut project) = semantic_fixture(true);
    let source = wav_with_frames(32_000, 48_000);
    let path = root.path().join("high-cue.wav");
    fs::write(&path, &source).unwrap();
    project = service
        .import_asset_file(
            project.id,
            &project.stamp(),
            "high-original-cue",
            &path,
            "high-cue.wav".into(),
            "audio/wav".into(),
        )
        .unwrap()
        .project;
    let mut cue = project.audio.sfx_cues[0].clone();
    cue.source_asset_id = project
        .assets
        .iter()
        .find(|asset| asset.name == "high-cue.wav")
        .unwrap()
        .id;
    cue.source_sha256 = hex::encode(Sha256::digest(&source));
    apply(
        &service,
        &mut project,
        "replace-cue-source",
        Change::UpsertSemanticSfx { cue },
    );
    let before = project.clone();
    let error = prepare_saved_original_mix(
        &service,
        project.id,
        &project.stamp(),
        project.deliverables[0].id,
    )
    .err()
    .unwrap();
    assert!(
        error
            .to_string()
            .contains("base plus semantic cues would clip")
    );
    assert_eq!(
        error.into_native().code,
        semwright_native_sdk::ErrorCode::Unsupported
    );
    assert_eq!(service.project(project.id).unwrap(), before);
}
