//! Real CAS and WAV readback for original-mix source preparation.
//! Generated numerical test signals are not speech or approved creative
//! material. These tests do not dispatch MLT or claim canonical recipe,
//! caption pixels, an AAC master, loudness or human approval.
use motionwright_domain::{
    AlignmentEvidence, Asset, AudioCodec, Change, OutputColorSpace, OutputContainer, Project,
    RationalTime, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent, TranscriptSegment,
    VideoCodec,
};
use motionwright_native::{
    original_mix::{OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow},
    production::{
        OriginalMixSourceRequest, PreparedOriginalMixWav, original_mix_sample_clock,
        prepare_original_mix_av_audio, verify_original_mix_av_source,
    },
    sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx},
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use semwright_native_sdk::{ErrorCode, Result as NativeResult};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
};
use tempfile::TempDir;
use uuid::Uuid;

const SAMPLES: usize = 96_000;

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn sample(pcm: &[u8], frame: usize, channel: usize) -> i16 {
    let at = frame * 4 + channel * 2;
    i16::from_le_bytes([pcm[at], pcm[at + 1]])
}

fn wav_bytes(pcm: &[u8]) -> Vec<u8> {
    assert!(pcm.len().is_multiple_of(4));
    let size = pcm.len() as u32;
    let mut wav = Vec::with_capacity(44 + pcm.len());
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(36 + size).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&48_000_u32.to_le_bytes());
    wav.extend_from_slice(&192_000_u32.to_le_bytes());
    wav.extend_from_slice(&4_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&size.to_le_bytes());
    wav.extend_from_slice(pcm);
    wav
}

fn original_pcm(bus: usize) -> Vec<u8> {
    let mut pcm = Vec::with_capacity(SAMPLES * 4);
    for at in 0..SAMPLES {
        for channel in 0..2 {
            let active = match bus {
                0 => (24_000..48_000).contains(&at),
                1 => true,
                _ => (72_000..95_000).contains(&at),
            };
            let frequency = [431.0, 317.0, 1091.0][bus] + channel as f64 * 137.0;
            let value = if active {
                let phase = at as f64 * frequency * std::f64::consts::TAU / 48_000.0;
                (phase.sin() * [3400.0, 10_000.0, 2100.0][bus]).round() as i16
            } else {
                0
            };
            pcm.extend_from_slice(&value.to_le_bytes());
        }
    }
    pcm
}

fn apply(service: &StudioService, project: &Project, key: &str, change: Change) -> Project {
    service
        .apply(project.id, &project.stamp(), key, &change)
        .unwrap()
        .project
}

fn attach_asset(
    service: &StudioService,
    project: &Project,
    file: &Path,
    name: &str,
) -> (Project, Uuid) {
    let descriptor = service.ingest_blob_file(file).unwrap();
    let id = Uuid::new_v4();
    let next = apply(
        service,
        project,
        &format!("mix-source-asset-{}", id.simple()),
        Change::AddAsset {
            asset: Asset {
                id,
                name: name.into(),
                media_type: "audio/wav".into(),
                content_sha256: Some(descriptor.sha256),
                source_revision: Some("original-numerical-test-signal/1".into()),
            },
        },
    );
    (next, id)
}

struct Fixture {
    owner_root: PathBuf,
    service: StudioService,
    project: Project,
    ids: [Uuid; 3],
    pcm: [Vec<u8>; 3],
    // Drop the service and its SQLite handles before removing the directory.
    root: TempDir,
}

fn fixture() -> Fixture {
    let root = tempfile::tempdir().unwrap();
    let owner_root = root.path().join("owner-output");
    fs::create_dir(&owner_root).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&owner_root, fs::Permissions::from_mode(0o700)).unwrap();
    }
    let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
    let mut project = service
        .create_project("Original source-only mix fixture")
        .unwrap();
    for scene in 0..2 {
        project = apply(
            &service,
            &project,
            &format!("mix-source-scene-{scene}"),
            Change::AddScene {
                name: format!("Original one-second scene {scene}"),
                objective: "Exact source clock and editable original audio".into(),
                duration_seconds: 1,
            },
        );
    }
    let pcm: [Vec<u8>; 3] = std::array::from_fn(original_pcm);
    let files = ["voice.wav", "music.wav", "sfx.wav"].map(|name| root.path().join(name));
    for (file, bytes) in files.iter().zip(&pcm) {
        fs::write(file, wav_bytes(bytes)).unwrap();
    }
    project = service
        .import_voice_file(
            project.id,
            &project.stamp(),
            "mix-source-measured-voice",
            &files[0],
            VoiceImportMetadata {
                name: "Original stereo numerical source".into(),
                label: "Measured original test signal, not speech".into(),
                media_type: "audio/wav".into(),
            },
        )
        .unwrap()
        .project;
    let track = project.audio.voice_tracks[0].clone();
    let mut ids = [track.asset_id, Uuid::nil(), Uuid::nil()];
    for (bus, file) in files.iter().enumerate().skip(1) {
        (project, ids[bus]) = attach_asset(
            &service,
            &project,
            file,
            [
                "voice",
                "Original music test signal",
                "Original SFX test signal",
            ][bus],
        );
    }
    project = apply(
        &service,
        &project,
        "mix-source-reviewed-window",
        Change::AddTranscriptSegment {
            segment: TranscriptSegment {
                id: Uuid::new_v4(),
                voice_track_id: track.id,
                start: RationalTime { num: 1, den: 2 },
                end: RationalTime { num: 1, den: 1 },
                text: "Authored timing interval for a numerical test source".into(),
                speaker: None,
                alignment: AlignmentEvidence::Manual,
            },
        },
    );
    let mut profile = project.deliverables[0].clone();
    profile.language = "en".into();
    profile.audio_sample_rate_hz = 48_000;
    profile.frame_rate.num = 30;
    profile.frame_rate.den = 1;
    profile.voice_track_id = Some(track.id);
    profile.video_codec = VideoCodec::H264;
    profile.audio_codec = AudioCodec::Aac;
    profile.container = OutputContainer::Mp4;
    profile.color_space = OutputColorSpace::Rec709;
    profile.captions = false;
    profile.burn_in_captions = false;
    project = apply(
        &service,
        &project,
        "mix-source-profile",
        Change::UpsertDeliverable { profile },
    );
    for file in files {
        fs::remove_file(file).unwrap();
    }
    Fixture {
        root,
        owner_root,
        service,
        project,
        ids,
        pcm,
    }
}

fn source_plan(f: &Fixture) -> OriginalMixPlan {
    let decoded = f.ids.map(|id| {
        f.service
            .decoded_stem_for_original_mix(f.project.id, f.project.revision, id)
            .unwrap()
    });
    let stem = |bus: usize| PcmStemReceipt {
        asset_id: f.ids[bus],
        source_asset_sha256: decoded[bus].original_asset_sha256.clone(),
        decoded_pcm_sha256: decoded[bus].decoded_pcm_sha256.clone(),
        sample_frames: decoded[bus].sample_frames,
    };
    OriginalMixPlan {
        schema: "motionwright.original-audio-mix/1".into(),
        project_id: f.project.id,
        generation: f.project.generation,
        project_revision: f.project.revision,
        deliverable_profile_id: f.project.deliverables[0].id,
        language: "en".into(),
        sample_rate: 48_000,
        channels: 2,
        sample_frames: SAMPLES as u64,
        measured_voice: stem(0),
        original_music: stem(1),
        original_sfx: stem(2),
        voice_gain_db: f64::from(f.project.audio.mix.voice_gain_db),
        music_gain_db: f64::from(f.project.audio.mix.music_gain_db),
        sfx_gain_db: -8.0,
        duck_attenuation_db: 14.0,
        duck_attack_samples: 2400,
        duck_release_samples: 4800,
        voice_windows: vec![VoiceActivityWindow {
            start_sample: 24_000,
            end_exclusive: 48_000,
        }],
        sample_peak_guard_dbfs: -1.0,
        source_classification: "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED".into(),
        owner_mixing_approval_authenticated: false,
        is_mastered: false,
    }
}

fn current_plan(f: &Fixture) -> OriginalMixPlan {
    let plan = source_plan(f);
    plan.validate(&f.project).unwrap();
    plan
}

fn request(plan: &OriginalMixPlan) -> OriginalMixSourceRequest<'_> {
    OriginalMixSourceRequest {
        plan,
        frame_count: 60,
        fps_num: 30,
        fps_den: 1,
    }
}

fn prepare(f: &Fixture, plan: &OriginalMixPlan) -> NativeResult<PreparedOriginalMixWav> {
    prepare_original_mix_av_audio(
        &f.service,
        f.project.id,
        &f.project.stamp(),
        request(plan),
        &f.owner_root,
    )
}

fn read_wav(f: &Fixture, prepared: &PreparedOriginalMixWav) -> Vec<u8> {
    fs::read(f.owner_root.join(&prepared.audio.relative_path)).unwrap()
}

#[test]
fn actual_cas_pcm_and_whole_wav_have_distinct_checked_hashes() {
    let f = fixture();
    let plan = current_plan(&f);
    let prepared = prepare(&f, &plan).unwrap();
    let wav = read_wav(&f, &prepared);
    assert_eq!(wav.len(), 44 + SAMPLES * 4);
    assert_eq!(&wav[..4], b"RIFF");
    assert_eq!(&wav[8..16], b"WAVEfmt ");
    assert_eq!(&wav[36..40], b"data");
    assert_eq!(sha256(&wav), prepared.audio.sha256);
    assert_eq!(sha256(&wav), prepared.proof.staged_wav_sha256);
    assert_eq!(
        sha256(&wav[44..]),
        prepared.proof.original_mix.rendered_pcm_sha256
    );
    assert_ne!(
        prepared.audio.sha256,
        prepared.proof.original_mix.rendered_pcm_sha256
    );
    assert_eq!(prepared.proof.original_mix.frames, SAMPLES as u64);
    assert_eq!(prepared.proof.measured_voice, plan.measured_voice);
    assert_eq!(prepared.proof.original_music, plan.original_music);
    assert_eq!(prepared.proof.original_sfx, plan.original_sfx);
    // Independent sample values in three different source-activity regions.
    for (at, duck) in [
        (1000, 1.0),
        (30_000, 10_f64.powf(-14.0 / 20.0)),
        (80_000, 1.0),
    ] {
        for channel in 0..2 {
            let voice = f64::from(sample(&f.pcm[0], at, channel));
            let music = f64::from(sample(&f.pcm[1], at, channel));
            let sfx = f64::from(sample(&f.pcm[2], at, channel));
            let mixed = voice * 10_f64.powf(plan.voice_gain_db / 20.0)
                + music * 10_f64.powf(plan.music_gain_db / 20.0) * duck
                + sfx * 10_f64.powf(plan.sfx_gain_db / 20.0);
            let expected = (mixed * 32767.0 / 32768.0).round() as i16;
            assert!((i32::from(sample(&wav[44..], at, channel)) - i32::from(expected)).abs() <= 1);
        }
    }
    assert!(prepared.proof.semantic_sfx.is_none());
    assert!(!prepared.proof.original_mix.owner_release_approved);
    assert!(
        !prepared
            .proof
            .original_mix
            .listening_intelligibility_approved
    );
    assert!(!prepared.proof.original_mix.independently_verified_lufs);
    assert!(!prepared.proof.original_mix.independently_verified_true_peak);
    assert_eq!(
        prepared.proof.evidence_scope,
        "verified-owner-original-mix-source-only-not-canonical-delivery-or-native-master"
    );
    let observed = verify_original_mix_av_source(
        &f.service,
        f.project.id,
        &f.project.stamp(),
        request(&plan),
        &prepared.audio,
        &f.owner_root,
    )
    .unwrap();
    assert_eq!(observed, prepared.proof);
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

#[test]
fn repeated_preparation_reuses_only_unchanged_source_bytes() {
    let f = fixture();
    let plan = current_plan(&f);
    let first = prepare(&f, &plan).unwrap();
    let before = read_wav(&f, &first);
    let repeated = prepare(&f, &plan).unwrap();
    assert_eq!(first.audio.relative_path, repeated.audio.relative_path);
    assert_eq!(first.proof, repeated.proof);
    assert_eq!(read_wav(&f, &repeated), before);
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 1);
}

#[test]
fn clock_is_exact_and_never_rounds_or_extends_the_sources() {
    assert_eq!(original_mix_sample_clock(60, 30, 1).unwrap(), 96_000);
    assert_eq!(original_mix_sample_clock(30, 30_000, 1001).unwrap(), 48_048);
    assert_eq!(original_mix_sample_clock(450, 30, 1).unwrap(), 720_000);
    for (frames, num, den) in [
        (0, 30, 1),
        (60, 0, 1),
        (60, 30, 0),
        (31, 30_000, 1001),
        (1, 30, 1),
        (451, 30, 1),
        (u64::MAX, 1, u32::MAX),
    ] {
        assert!(original_mix_sample_clock(frames, num, den).is_err());
    }
    let f = fixture();
    let plan = current_plan(&f);
    for changed in [
        OriginalMixSourceRequest {
            frame_count: 59,
            ..request(&plan)
        },
        OriginalMixSourceRequest {
            frame_count: 61,
            ..request(&plan)
        },
        OriginalMixSourceRequest {
            fps_num: 24,
            ..request(&plan)
        },
    ] {
        assert!(
            prepare_original_mix_av_audio(
                &f.service,
                f.project.id,
                &f.project.stamp(),
                changed,
                &f.owner_root,
            )
            .is_err()
        );
    }
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
}

#[test]
fn forged_pcm_receipt_foreign_generation_and_sample_guard_create_no_wav() {
    let f = fixture();
    let plan = current_plan(&f);
    let mut changed = plan.clone();
    changed.original_music.decoded_pcm_sha256 = "b".repeat(64);
    // The old pure-plan validator accepts a well-formed digest. The new
    // custody boundary must compare it against samples decoded from CAS.
    changed.validate(&f.project).unwrap();
    assert_eq!(
        prepare(&f, &changed).unwrap_err().code,
        ErrorCode::StaleReference
    );
    let mut wrong_generation = f.project.stamp();
    wrong_generation.generation = Uuid::new_v4();
    assert_eq!(
        prepare_original_mix_av_audio(
            &f.service,
            f.project.id,
            &wrong_generation,
            request(&plan),
            &f.owner_root,
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    let mut changed = plan;
    changed.sample_peak_guard_dbfs = -0.5;
    assert_eq!(
        prepare(&f, &changed).unwrap_err().code,
        ErrorCode::Unsupported
    );
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

#[test]
fn actual_short_cas_audio_cannot_be_promoted_by_a_full_length_receipt() {
    let mut f = fixture();
    let file = f.root.path().join("one-second-music.wav");
    let short = wav_bytes(&f.pcm[1][..48_000 * 4]);
    fs::write(&file, &short).unwrap();
    (f.project, f.ids[1]) = attach_asset(&f.service, &f.project, &file, "Short original music");
    fs::remove_file(file).unwrap();
    // Build the plan explicitly because truthful current_plan rejects a
    // short stem before this test forges its claimed sample count.
    let mut plan = source_plan(&f);
    plan.original_music.sample_frames = SAMPLES as u64;
    plan.validate(&f.project).unwrap();
    assert_eq!(
        prepare(&f, &plan).unwrap_err().code,
        ErrorCode::StaleReference
    );
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
}

#[test]
fn a_saved_gain_edit_changes_pcm_and_invalidates_prior_source_authority() {
    let mut f = fixture();
    let before_plan = current_plan(&f);
    let old_stamp = f.project.stamp();
    let before = prepare(&f, &before_plan).unwrap();
    let before_wav = read_wav(&f, &before);
    let mut mix = f.project.audio.mix.clone();
    mix.music_gain_db -= 6.0;
    f.project = apply(
        &f.service,
        &f.project,
        "mix-source-lower-music",
        Change::SetMixIntent { mix },
    );
    assert_eq!(
        verify_original_mix_av_source(
            &f.service,
            f.project.id,
            &old_stamp,
            request(&before_plan),
            &before.audio,
            &f.owner_root,
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    assert!(prepare(&f, &before_plan).is_err());
    let after_plan = current_plan(&f);
    let after = prepare(&f, &after_plan).unwrap();
    let after_wav = read_wav(&f, &after);
    assert_ne!(before.audio.sha256, after.audio.sha256);
    assert_ne!(
        before.proof.original_mix.source_plan_sha256,
        after.proof.original_mix.source_plan_sha256
    );
    let energy = |wav: &[u8]| -> f64 {
        let (samples, remainder) = wav[44..44 + 20_000 * 4].as_chunks::<2>();
        assert!(remainder.is_empty());
        samples
            .iter()
            .map(|bytes| f64::from(i16::from_le_bytes(*bytes)).powi(2))
            .sum()
    };
    assert!((0.49..0.52).contains(&(energy(&after_wav) / energy(&before_wav)).sqrt()));
    assert_eq!(read_wav(&f, &before), before_wav);
}

#[test]
fn staged_pcm_tamper_is_rejected_without_overwriting_or_repairing_the_file() {
    let f = fixture();
    let plan = current_plan(&f);
    let prepared = prepare(&f, &plan).unwrap();
    let path = f.owner_root.join(&prepared.audio.relative_path);
    let mut tampered = read_wav(&f, &prepared);
    tampered[104] ^= 1;
    fs::write(&path, &tampered).unwrap();
    assert!(
        verify_original_mix_av_source(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            request(&plan),
            &prepared.audio,
            &f.owner_root,
        )
        .is_err()
    );
    assert!(prepare(&f, &plan).is_err());
    assert_eq!(fs::read(path).unwrap(), tampered);
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 1);
}

#[test]
fn changed_actual_cas_source_invalidates_an_existing_staged_mix() {
    let f = fixture();
    let plan = current_plan(&f);
    let prepared = prepare(&f, &plan).unwrap();
    let before = read_wav(&f, &prepared);
    let store = f.service.store();
    let source_path = store
        .lock()
        .verified_blob_path(&plan.original_music.source_asset_sha256)
        .unwrap();
    // This is exclusively a file created by this isolated test fixture.
    let mut changed = fs::read(&source_path).unwrap();
    let last = changed.len() - 1;
    changed[last] ^= 1;
    fs::write(source_path, changed).unwrap();
    assert!(
        verify_original_mix_av_source(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            request(&plan),
            &prepared.audio,
            &f.owner_root,
        )
        .is_err()
    );
    assert_eq!(read_wav(&f, &prepared), before);
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

fn fixture_with_semantic_sfx() -> Fixture {
    let mut f = fixture();
    let mut verified = Vec::new();
    for index in 0..2 {
        let mut pcm = Vec::new();
        for at in 0..12 * 1600 {
            for channel in 0..2 {
                let phase = at as f64
                    * (673.0 + index as f64 * 311.0 + channel as f64 * 101.0)
                    * std::f64::consts::TAU
                    / 48_000.0;
                pcm.extend_from_slice(&((phase.sin() * 2300.0).round() as i16).to_le_bytes());
            }
        }
        let wav = wav_bytes(&pcm);
        let file = f.root.path().join(format!("authored-cue-{index}.wav"));
        fs::write(&file, &wav).unwrap();
        let (next, asset_id) =
            attach_asset(&f.service, &f.project, &file, "Original cue test signal");
        f.project = next;
        fs::remove_file(file).unwrap();
        let digest = sha256(&wav);
        let cue = SemanticSfxCue {
            schema: SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id: f.project.scenes[index].id,
            event: SfxEvent::UiFocus,
            anchor: SfxAnchor::SceneStart,
            offset_frames: if index == 0 { 5 } else { 3 },
            source_asset_id: asset_id,
            source_sha256: digest.clone(),
            source_frames: 12,
            gain_milli_db: -12_000,
            attack_frames: 1,
            release_frames: 2,
            original_source_attestation:
                "Original numerical source authored for this isolated fixture".into(),
        };
        f.project = apply(
            &f.service,
            &f.project,
            &format!("mix-source-cue-{index}"),
            Change::UpsertSemanticSfx { cue },
        );
        verified.push(VerifiedSfxWav {
            asset_id,
            source_sha256: digest,
            bytes: wav,
        });
    }
    let mixed =
        mix_source_bound_semantic_sfx(&f.project, f.project.deliverables[0].id, &verified, 60)
            .unwrap();
    let bus_file = f.root.path().join("authored-sfx-bus.wav");
    fs::write(&bus_file, &mixed.wav_bytes).unwrap();
    (f.project, f.ids[2]) =
        attach_asset(&f.service, &f.project, &bus_file, "Exact original cue bus");
    fs::remove_file(bus_file).unwrap();
    f.pcm[2] = mixed.wav_bytes[44..].to_vec();
    f
}

#[test]
fn semantic_sfx_is_reconstructed_from_cas_and_compared_with_the_selected_bus() {
    let f = fixture_with_semantic_sfx();
    let plan = current_plan(&f);
    let prepared = prepare(&f, &plan).unwrap();
    let proof = prepared.proof.semantic_sfx.as_ref().unwrap();
    assert_eq!(proof.cue_count, 2);
    assert_eq!(proof.sources.len(), 2);
    assert_eq!(
        proof.reconstructed_pcm_sha256,
        plan.original_sfx.decoded_pcm_sha256
    );
    assert_eq!(
        proof.reconstructed_wav_sha256,
        plan.original_sfx.source_asset_sha256
    );
    assert!(
        f.pcm[2][5 * 1600 * 4..17 * 1600 * 4]
            .iter()
            .any(|byte| *byte != 0)
    );
    assert!(
        f.pcm[2][17 * 1600 * 4..33 * 1600 * 4]
            .iter()
            .all(|byte| *byte == 0)
    );
    assert!(
        f.pcm[2][33 * 1600 * 4..45 * 1600 * 4]
            .iter()
            .any(|byte| *byte != 0)
    );
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

#[test]
fn moving_or_removing_one_of_multiple_cues_rejects_a_stale_sfx_bus() {
    for remove in [false, true] {
        let mut f = fixture_with_semantic_sfx();
        let original_plan = current_plan(&f);
        let prepared = prepare(&f, &original_plan).unwrap();
        let before = read_wav(&f, &prepared);
        let mut cue = f.project.audio.sfx_cues[0].clone();
        let change = if remove {
            Change::RemoveSemanticSfx { cue_id: cue.id }
        } else {
            cue.offset_frames += 1;
            Change::UpsertSemanticSfx { cue }
        };
        f.project = apply(&f.service, &f.project, "mix-source-change-cue", change);
        // A refreshed plan alone cannot launder the old bus: its real PCM
        // must still equal the newly saved cue schedule.
        let refreshed = current_plan(&f);
        assert_eq!(
            prepare(&f, &refreshed).unwrap_err().code,
            ErrorCode::Unsupported
        );
        assert_eq!(read_wav(&f, &prepared), before);
        assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 1);
    }
}

#[cfg(unix)]
#[test]
fn symlink_substitution_never_reuses_an_original_mix_source_grant() {
    let f = fixture();
    let plan = current_plan(&f);
    let prepared = prepare(&f, &plan).unwrap();
    let expected = f.owner_root.join(&prepared.audio.relative_path);
    let retained = f.root.path().join("retained-original.wav");
    fs::rename(&expected, &retained).unwrap();
    std::os::unix::fs::symlink(&retained, &expected).unwrap();
    assert!(prepare(&f, &plan).is_err());
    assert!(retained.is_file());
}
