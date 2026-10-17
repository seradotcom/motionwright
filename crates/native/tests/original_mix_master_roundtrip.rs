//! Real CAS/bundle/reopen regression for the existing original three-bus mixer.
//!
//! The audio is generated test signal, not speech or an approved creative mix.
//! Custom ducking options remain an explicit retained request in these tests;
//! project-bundle success alone must not claim those options are persisted.
use motionwright_domain::{
    AlignmentEvidence, Asset, Change, Project, RationalTime, TranscriptSegment,
};
use motionwright_native::original_mix::{
    OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow, render_original_source_mix, write_mix_wav,
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use uuid::Uuid;

const SAMPLES: usize = 96_000;

fn sha(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn source_wav(path: &Path, bus: usize) -> Vec<u8> {
    let mut pcm = Vec::with_capacity(SAMPLES * 4);
    for at in 0..SAMPLES {
        for channel in 0..2 {
            let active = match bus {
                0 => (24_000..48_000).contains(&at),
                1 => true,
                _ => (72_000..95_000).contains(&at),
            };
            let value = if active {
                let frequency = [431.0, 317.0, 1091.0][bus] + channel as f64 * 137.0;
                let phase = at as f64 * frequency * std::f64::consts::TAU / 48_000.0;
                (phase.sin() * [3400.0, 10_000.0, 2100.0][bus]).round() as i16
            } else {
                0
            };
            pcm.extend_from_slice(&value.to_le_bytes());
        }
    }
    let bytes = pcm.len() as u32;
    let mut wav = Vec::with_capacity(pcm.len() + 44);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + bytes).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&48_000_u32.to_le_bytes());
    wav.extend_from_slice(&192_000_u32.to_le_bytes());
    wav.extend_from_slice(&4_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&bytes.to_le_bytes());
    wav.extend_from_slice(&pcm);
    fs::write(path, wav).unwrap();
    pcm
}

fn apply(service: &StudioService, project: &Project, key: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), key, &change)
        .unwrap()
        .project
}

fn create_fixture(root: &Path) -> (StudioService, Project, [Uuid; 3], [Vec<u8>; 3]) {
    let source_root = root.join("source");
    fs::create_dir_all(&source_root).unwrap();
    let service = StudioService::open(source_root.join("studio.sqlite3")).unwrap();
    let mut project = service
        .create_project("Portable original audio test signal")
        .unwrap();
    let files = ["voice.wav", "music.wav", "sfx.wav"].map(|name| source_root.join(name));
    let pcm = std::array::from_fn(|index| source_wav(&files[index], index));
    project = service
        .import_voice_file(
            project.id,
            &project.stamp(),
            "mix-reopen:import-voice",
            &files[0],
            VoiceImportMetadata {
                name: "Original stereo test signal".into(),
                label: "Measured test interval; not speech".into(),
                media_type: "audio/wav".into(),
            },
        )
        .unwrap()
        .project;
    let track = project.audio.voice_tracks[0].clone();
    let mut ids = [track.asset_id, Uuid::nil(), Uuid::nil()];
    for (index, file) in files.iter().enumerate().skip(1) {
        let descriptor = service.ingest_blob_file(file).unwrap();
        ids[index] = Uuid::new_v4();
        project = apply(
            &service,
            &project,
            &format!("mix-reopen:import-bus-{index}"),
            Change::AddAsset {
                asset: Asset {
                    id: ids[index],
                    name: [
                        "voice",
                        "original music test signal",
                        "original SFX test signal",
                    ][index]
                        .into(),
                    media_type: "audio/wav".into(),
                    content_sha256: Some(descriptor.sha256),
                    source_revision: Some("original-test-signal/1".into()),
                },
            },
        );
    }
    project = apply(
        &service,
        &project,
        "mix-reopen:source-interval",
        Change::AddTranscriptSegment {
            segment: TranscriptSegment {
                id: Uuid::new_v4(),
                voice_track_id: track.id,
                start: RationalTime { num: 1, den: 2 },
                end: RationalTime { num: 1, den: 1 },
                text: "Authored test interval, no speech-recognition claim".into(),
                speaker: None,
                alignment: AlignmentEvidence::Manual,
            },
        },
    );
    let mut profile = project.deliverables[0].clone();
    profile.language = "en".into();
    profile.audio_sample_rate_hz = 48_000;
    profile.voice_track_id = Some(track.id);
    project = apply(
        &service,
        &project,
        "mix-reopen:profile",
        Change::UpsertDeliverable { profile },
    );
    for file in files {
        fs::remove_file(file).unwrap();
    }
    (service, project, ids, pcm)
}

/// Reconstruct receipts from the current service/CAS, retaining explicitly
/// supplied request options; a prior generation's plan is never reused as proof.
fn current_plan(
    service: &StudioService,
    project: &Project,
    ids: [Uuid; 3],
    retained_options: Option<&OriginalMixPlan>,
) -> (OriginalMixPlan, [Vec<u8>; 3]) {
    let decoded = ids.map(|id| {
        service
            .decoded_stem_for_original_mix(project.id, project.revision, id)
            .unwrap()
    });
    let stem = |index: usize| PcmStemReceipt {
        asset_id: ids[index],
        source_asset_sha256: decoded[index].original_asset_sha256.clone(),
        decoded_pcm_sha256: decoded[index].decoded_pcm_sha256.clone(),
        sample_frames: decoded[index].sample_frames,
    };
    let plan = OriginalMixPlan {
        schema: "motionwright.original-audio-mix/1".into(),
        project_id: project.id,
        generation: project.generation,
        project_revision: project.revision,
        deliverable_profile_id: project.deliverables[0].id,
        language: project.deliverables[0].language.clone(),
        sample_rate: 48_000,
        channels: 2,
        sample_frames: SAMPLES as u64,
        measured_voice: stem(0),
        original_music: stem(1),
        original_sfx: stem(2),
        voice_gain_db: f64::from(project.audio.mix.voice_gain_db),
        music_gain_db: f64::from(project.audio.mix.music_gain_db),
        sfx_gain_db: retained_options.map_or(-8.0, |value| value.sfx_gain_db),
        duck_attenuation_db: retained_options.map_or(14.0, |value| value.duck_attenuation_db),
        duck_attack_samples: retained_options.map_or(2400, |value| value.duck_attack_samples),
        duck_release_samples: retained_options.map_or(4800, |value| value.duck_release_samples),
        voice_windows: project
            .audio
            .transcript
            .iter()
            .map(|segment| VoiceActivityWindow {
                start_sample: (segment.start.num * 48_000 / segment.start.den) as u64,
                end_exclusive: (segment.end.num * 48_000 / segment.end.den) as u64,
            })
            .collect(),
        sample_peak_guard_dbfs: -0.5,
        source_classification: "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED".into(),
        owner_mixing_approval_authenticated: false,
        is_mastered: false,
    };
    plan.validate(project).unwrap();
    (plan, decoded.map(|item| item.interleaved_s16le))
}

fn mix(plan: &OriginalMixPlan, project: &Project, pcm: &[Vec<u8>; 3]) -> Vec<u8> {
    let mut wav = Vec::new();
    let evidence = write_mix_wav(plan, project, &pcm[0], &pcm[1], &pcm[2], &mut wav).unwrap();
    assert_eq!(evidence.rendered_pcm_sha256, sha(&wav[44..]));
    assert!(!evidence.owner_release_approved);
    assert!(!evidence.listening_intelligibility_approved);
    wav
}

#[test]
fn portable_original_assets_reopen_and_replay_with_an_explicit_retained_mix_request() {
    let temp = tempfile::tempdir().unwrap();
    let (service, project, ids, original_pcm) = create_fixture(temp.path());
    let (plan, pcm) = current_plan(&service, &project, ids, None);
    assert_eq!(pcm, original_pcm);
    let wav = mix(&plan, &project, &pcm);
    let bundle = temp.path().join("source.motionwright");
    let manifest = service.export_project_bundle(project.id, &bundle).unwrap();
    assert_eq!(manifest.blobs.len(), 3);
    for blob in &manifest.blobs {
        let bytes = fs::read(bundle.join(&blob.relative_path)).unwrap();
        assert_eq!(sha(&bytes), blob.sha256);
        assert_eq!(bytes.len() as u64, blob.size_bytes);
    }
    drop(service);
    // All files here were created by this isolated fixture. The destination
    // must succeed using the exported bundle after the original CAS is gone.
    fs::remove_dir_all(temp.path().join("source")).unwrap();
    let restored_root = temp.path().join("restored");
    fs::create_dir_all(&restored_root).unwrap();
    let db = restored_root.join("studio.sqlite3");
    let restored_service = StudioService::open(&db).unwrap();
    let imported = restored_service.import_project_bundle(&bundle).unwrap();
    assert_eq!(imported.id, project.id);
    assert_eq!(imported.revision, project.revision);
    assert_ne!(imported.generation, project.generation);
    assert_eq!(imported.assets, project.assets);
    assert_eq!(imported.audio, project.audio);
    assert_eq!(imported.deliverables, project.deliverables);
    assert!(plan.validate(&imported).is_err());
    assert!(
        restored_service
            .apply(
                imported.id,
                &project.stamp(),
                "mix-reopen:stale-generation",
                &Change::RenameProject {
                    title: "Stale authority must be refused".into()
                }
            )
            .is_err()
    );
    drop(restored_service);
    let reopened_service = StudioService::open(&db).unwrap();
    let reopened = reopened_service.project(imported.id).unwrap();
    let (fresh, replay_pcm) = current_plan(&reopened_service, &reopened, ids, Some(&plan));
    assert_eq!(replay_pcm, original_pcm);
    assert_eq!(mix(&fresh, &reopened, &replay_pcm), wav);
    let old_evidence = render_original_source_mix(&plan, &project, &pcm[0], &pcm[1], &pcm[2])
        .unwrap()
        .1;
    let new_evidence = render_original_source_mix(
        &fresh,
        &reopened,
        &replay_pcm[0],
        &replay_pcm[1],
        &replay_pcm[2],
    )
    .unwrap()
    .1;
    assert_ne!(
        new_evidence.source_plan_sha256,
        old_evidence.source_plan_sha256
    );
    assert_eq!(
        new_evidence.rendered_pcm_sha256,
        old_evidence.rendered_pcm_sha256
    );
}

#[test]
fn editorial_gain_edit_after_bundle_import_changes_pcm_and_invalidates_previous_plan() {
    let temp = tempfile::tempdir().unwrap();
    let (service, project, ids, _) = create_fixture(temp.path());
    let (plan, pcm) = current_plan(&service, &project, ids, None);
    let before_wav = mix(&plan, &project, &pcm);
    let bundle = temp.path().join("editable.motionwright");
    service.export_project_bundle(project.id, &bundle).unwrap();
    let target = temp.path().join("edited");
    fs::create_dir_all(&target).unwrap();
    let destination = StudioService::open(target.join("studio.sqlite3")).unwrap();
    let imported = destination.import_project_bundle(&bundle).unwrap();
    let (imported_plan, imported_pcm) = current_plan(&destination, &imported, ids, Some(&plan));
    assert!(imported_plan.validate(&imported).is_ok());
    assert_eq!(imported_pcm, pcm);
    assert_eq!(mix(&imported_plan, &imported, &imported_pcm), before_wav);
    let mut intent = imported.audio.mix.clone();
    intent.music_gain_db -= 6.0;
    let edited = apply(
        &destination,
        &imported,
        "mix-reopen:edit-music-gain",
        Change::SetMixIntent { mix: intent },
    );
    assert_eq!(edited.revision, imported.revision + 1);
    assert_eq!(edited.assets, imported.assets);
    assert_eq!(edited.audio.voice_tracks, imported.audio.voice_tracks);
    assert_eq!(edited.audio.transcript, imported.audio.transcript);
    assert_eq!(edited.generation, imported.generation);
    let (changed_plan, source_pcm) = current_plan(&destination, &edited, ids, Some(&imported_plan));
    assert_eq!(source_pcm, pcm);
    assert!(imported_plan.validate(&edited).is_err());
    let after_wav = mix(&changed_plan, &edited, &source_pcm);
    assert_ne!(sha(&before_wav), sha(&after_wav));
    let energy = |wav: &[u8]| -> f64 {
        wav[44..44 + 20_000 * 4]
            .as_chunks::<2>()
            .0
            .iter()
            .map(|sample| f64::from(i16::from_le_bytes([sample[0], sample[1]])).powi(2))
            .sum()
    };
    let measured_gain_ratio = (energy(&after_wav) / energy(&before_wav)).sqrt();
    assert!((0.49..0.52).contains(&measured_gain_ratio));
    drop(destination);
    let reopened_service = StudioService::open(target.join("studio.sqlite3")).unwrap();
    let reopened = reopened_service.project(edited.id).unwrap();
    assert_eq!(reopened.stamp(), edited.stamp());
    assert_eq!(reopened.audio, edited.audio);
    assert_eq!(reopened.assets, edited.assets);
    let (persisted_plan, persisted_pcm) =
        current_plan(&reopened_service, &reopened, ids, Some(&imported_plan));
    assert!(imported_plan.validate(&reopened).is_err());
    assert_eq!(persisted_pcm, source_pcm);
    assert_eq!(mix(&persisted_plan, &reopened, &persisted_pcm), after_wav);
}

#[test]
fn corrupted_or_missing_bundle_audio_is_rejected_before_project_admission() {
    for missing in [false, true] {
        let temp = tempfile::tempdir().unwrap();
        let (service, project, _, _) = create_fixture(temp.path());
        let bundle = temp.path().join("invalid.motionwright");
        let manifest = service.export_project_bundle(project.id, &bundle).unwrap();
        let blob_path = bundle.join(&manifest.blobs[0].relative_path);
        if missing {
            fs::remove_file(blob_path).unwrap();
        } else {
            let mut bytes = fs::read(&blob_path).unwrap();
            let last = bytes.len() - 1;
            bytes[last] ^= 1;
            fs::write(blob_path, bytes).unwrap();
        }
        let target = temp.path().join("rejected");
        fs::create_dir_all(&target).unwrap();
        let destination = StudioService::open(target.join("studio.sqlite3")).unwrap();
        assert!(destination.inspect_project_bundle(&bundle).is_err());
        assert!(destination.import_project_bundle(&bundle).is_err());
        assert!(destination.project(project.id).is_err());
    }
}
