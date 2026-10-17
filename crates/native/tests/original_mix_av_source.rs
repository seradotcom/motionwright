//! Real CAS and WAV readback for original-mix source preparation.
//! Generated numerical test signals are not speech or approved creative
//! material. These tests exercise saved canonical recipes and physical source
//! bytes. They do not produce a native success, caption pixels, an AAC master,
//! target loudness or human approval.
use motionwright_domain::{
    AlignmentEvidence, Asset, AudioCodec, Change, DeliveryArchetype, DeliveryNarrativeRole,
    DeliveryRecipe, DeliveryRecipeSchema, DeliverySceneIntent, OriginalMixRecipe, OutputColorSpace,
    OutputContainer, Project, RationalTime, SFX_CUE_SCHEMA, SemanticSfxCue, SfxAnchor, SfxEvent,
    TranscriptSegment, VideoCodec,
};
use motionwright_native::{
    original_mix::{OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow},
    production::{
        MltAudioArtifact, MltMultisegmentOriginalMixMasterRequest, MltPreparedMezzanines,
        MltVerifiedLosslessTimeline, MotionCanvasRenderEvidence, OriginalMixSourceRequest,
        PreparedOriginalMixWav, ProductionConnection, ProductionCoordinator,
        canonical_original_mix_film_options, original_mix_sample_clock,
        prepare_original_mix_av_audio, resolve_canonical_original_mix_plan,
        verify_original_mix_av_source,
    },
    sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx},
};
use motionwright_service::{StudioService, VoiceImportMetadata};
use semwright_media_time::Rate;
use semwright_motion_authoring::{Archetype, NarrativeRole};
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
    let root = tempfile::Builder::new()
        .prefix("canonical-mix-source-")
        .tempdir_in(env!("CARGO_MANIFEST_DIR"))
        .unwrap();
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

fn canonical_recipe(f: &Fixture) -> DeliveryRecipe {
    DeliveryRecipe {
        schema: DeliveryRecipeSchema::V1,
        scene_intents: f
            .project
            .scenes
            .iter()
            .map(|scene| DeliverySceneIntent {
                scene_id: scene.id,
                role: Some(DeliveryNarrativeRole::Mechanism),
                archetype: Some(DeliveryArchetype::Statement),
            })
            .collect(),
        original_mix: Some(OriginalMixRecipe {
            music_asset_id: Some(f.ids[1]),
            sfx_asset_id: Some(f.ids[2]),
            sfx_gain_db: -8.0,
            duck_attenuation_db: 14.0,
            duck_attack_samples: 2400,
            duck_release_samples: 4800,
        }),
    }
}

fn save_canonical_recipe(f: &mut Fixture, key: &str, recipe: Option<DeliveryRecipe>) {
    f.project = apply(
        &f.service,
        &f.project,
        key,
        Change::SetDeliveryRecipe {
            deliverable_profile_id: f.project.deliverables[0].id,
            recipe,
        },
    );
}

fn canonical_fixture() -> Fixture {
    let mut f = fixture();
    let recipe = canonical_recipe(&f);
    save_canonical_recipe(&mut f, "canonical-source-recipe", Some(recipe));
    f
}

fn canonical_plan(f: &Fixture) -> NativeResult<OriginalMixPlan> {
    resolve_canonical_original_mix_plan(
        &f.service,
        f.project.id,
        &f.project.stamp(),
        f.project.deliverables[0].id,
    )
}

// This executable writes an unexpected-dispatch marker and exits with failure.
// It cannot fabricate a successful provider envelope or native media receipt.
fn no_dispatch_connection(f: &Fixture) -> ProductionConnection {
    let executable = f.root.path().join(if cfg!(windows) {
        "canonical-mix-must-not-run.cmd"
    } else {
        "canonical-mix-must-not-run"
    });
    let script: &[u8] = if cfg!(windows) {
        b"@echo off\r\n> \"%~dp0native-dispatch-marker\" echo unexpected\r\nexit /b 97\r\n"
    } else {
        b"#!/bin/sh\n: > \"${0%/*}/native-dispatch-marker\"\nexit 97\n"
    };
    let socket = f.root.path().join("broker.sock");
    let session = f.root.path().join("session.json");
    fs::write(&executable, script).unwrap();
    fs::write(&socket, b"").unwrap();
    fs::write(&session, b"{}").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for (path, mode) in [(&executable, 0o700), (&socket, 0o600), (&session, 0o600)] {
            fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
        }
    }
    ProductionConnection {
        schema: "motionwright-semwright-connection/1".into(),
        executable_sha256: sha256(script),
        executable,
        socket,
        session_file: session,
        output_root: f.owner_root.clone(),
        resource: f.project.resource_key(),
    }
}

fn canonical_coordinator(f: &Fixture) -> ProductionCoordinator {
    ProductionCoordinator::new(f.service.clone(), no_dispatch_connection(f)).unwrap()
}

fn assert_no_native_dispatch(f: &Fixture, coordinator: &ProductionCoordinator) {
    assert!(coordinator.receipts(f.project.id, 256).unwrap().is_empty());
    assert_eq!(
        fs::symlink_metadata(f.root.path().join("native-dispatch-marker"))
            .unwrap_err()
            .kind(),
        std::io::ErrorKind::NotFound,
    );
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

#[test]
fn canonical_resolver_derives_every_audio_setting_from_saved_intent_and_actual_cas() {
    let f = canonical_fixture();
    let resolved = canonical_plan(&f).unwrap();
    assert_eq!(resolved, current_plan(&f));
    assert_eq!(
        resolved.measured_voice.decoded_pcm_sha256,
        sha256(&f.pcm[0])
    );
    assert_eq!(
        resolved.original_music.decoded_pcm_sha256,
        sha256(&f.pcm[1])
    );
    assert_eq!(resolved.original_sfx.decoded_pcm_sha256, sha256(&f.pcm[2]));
    assert_eq!(
        resolved.voice_windows,
        vec![VoiceActivityWindow {
            start_sample: 24_000,
            end_exclusive: 48_000,
        }]
    );
    assert_eq!(resolved.sample_peak_guard_dbfs, -1.0);
    assert!(!resolved.owner_mixing_approval_authenticated);
    assert!(!resolved.is_mastered);
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
    assert!(
        f.service
            .production_receipts(f.project.id, 256)
            .unwrap()
            .is_empty()
    );
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
}

#[test]
fn canonical_film_projection_maps_all_wire_variants_and_uses_only_saved_fps_and_bundled_fonts() {
    use motionwright_native::film::{MOTION_CANVAS_FONT_FAMILY, MOTION_CANVAS_MONO_FONT_FAMILY};
    let f = canonical_fixture();
    let profile_id = f.project.deliverables[0].id;
    let options = canonical_original_mix_film_options(&f.project, profile_id).unwrap();
    assert_eq!(options.frame_rate, Rate::new(30, 1).unwrap());
    assert_eq!(options.font_family, MOTION_CANVAS_FONT_FAMILY);
    assert_eq!(options.mono_font_family, MOTION_CANVAS_MONO_FONT_FAMILY);
    assert_eq!(
        options
            .scene_intents
            .iter()
            .map(|intent| intent.scene_id)
            .collect::<Vec<_>>(),
        f.project
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>()
    );
    for (wire, native) in [
        (DeliveryNarrativeRole::Hook, NarrativeRole::Hook),
        (DeliveryNarrativeRole::Problem, NarrativeRole::Problem),
        (DeliveryNarrativeRole::Mechanism, NarrativeRole::Mechanism),
        (DeliveryNarrativeRole::Evidence, NarrativeRole::Evidence),
        (DeliveryNarrativeRole::Comparison, NarrativeRole::Comparison),
        (DeliveryNarrativeRole::Reveal, NarrativeRole::Reveal),
        (DeliveryNarrativeRole::Payoff, NarrativeRole::Payoff),
        (DeliveryNarrativeRole::Cta, NarrativeRole::Cta),
    ] {
        let mut project = f.project.clone();
        project
            .production_design
            .delivery_recipes
            .get_mut(&profile_id)
            .unwrap()
            .scene_intents[0]
            .role = Some(wire);
        assert_eq!(
            canonical_original_mix_film_options(&project, profile_id)
                .unwrap()
                .scene_intents[0]
                .role,
            native
        );
    }
    for (wire, native) in [
        (DeliveryArchetype::Statement, Archetype::Statement),
        (
            DeliveryArchetype::SplitExplanation,
            Archetype::SplitExplanation,
        ),
        (
            DeliveryArchetype::ArchitectureReveal,
            Archetype::ArchitectureReveal,
        ),
        (DeliveryArchetype::Comparison, Archetype::Comparison),
        (DeliveryArchetype::Metric, Archetype::Metric),
        (DeliveryArchetype::Timeline, Archetype::Timeline),
        (DeliveryArchetype::CodeFocus, Archetype::CodeFocus),
        (DeliveryArchetype::DiagramBuild, Archetype::DiagramBuild),
        (
            DeliveryArchetype::ObjectSpotlight,
            Archetype::ObjectSpotlight,
        ),
        (DeliveryArchetype::EvidenceFrame, Archetype::EvidenceFrame),
        (DeliveryArchetype::ProductProof, Archetype::ProductProof),
        (DeliveryArchetype::Endcard, Archetype::Endcard),
    ] {
        let mut project = f.project.clone();
        project
            .production_design
            .delivery_recipes
            .get_mut(&profile_id)
            .unwrap()
            .scene_intents[0]
            .archetype = Some(wire);
        assert_eq!(
            canonical_original_mix_film_options(&project, profile_id)
                .unwrap()
                .scene_intents[0]
                .archetype,
            native
        );
    }
    let mut project = f.project.clone();
    project.deliverables[0].frame_rate.num = 24;
    project.deliverables[0].frame_rate.den = 1;
    assert_eq!(
        canonical_original_mix_film_options(&project, profile_id)
            .unwrap()
            .frame_rate,
        Rate::new(24, 1).unwrap()
    );
}

#[test]
fn canonical_film_requires_both_explicit_consumed_intents_and_ignores_excluded_scenes() {
    let f = canonical_fixture();
    let profile_id = f.project.deliverables[0].id;
    for missing_role in [true, false] {
        let mut project = f.project.clone();
        let intent = &mut project
            .production_design
            .delivery_recipes
            .get_mut(&profile_id)
            .unwrap()
            .scene_intents[0];
        if missing_role {
            intent.role = None;
        } else {
            intent.archetype = None;
        }
        assert_eq!(
            canonical_original_mix_film_options(&project, profile_id)
                .unwrap_err()
                .code,
            ErrorCode::Unsupported
        );
    }
    let mut project = f.project.clone();
    project
        .production_design
        .delivery_recipes
        .get_mut(&profile_id)
        .unwrap()
        .scene_intents
        .remove(0);
    assert_eq!(
        canonical_original_mix_film_options(&project, profile_id)
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
    let mut project = f.project.clone();
    project.deliverables[0].included_scene_ids = vec![project.scenes[0].id];
    let excluded = &mut project
        .production_design
        .delivery_recipes
        .get_mut(&profile_id)
        .unwrap()
        .scene_intents[1];
    excluded.role = None;
    excluded.archetype = None;
    let options = canonical_original_mix_film_options(&project, profile_id).unwrap();
    assert_eq!(options.scene_intents.len(), 1);
    assert_eq!(options.scene_intents[0].scene_id, project.scenes[0].id);
    let mut reversed = f.project.clone();
    reversed
        .production_design
        .delivery_recipes
        .get_mut(&profile_id)
        .unwrap()
        .scene_intents
        .reverse();
    assert_eq!(
        canonical_original_mix_film_options(&reversed, profile_id).unwrap(),
        canonical_original_mix_film_options(&f.project, profile_id).unwrap()
    );
}

#[test]
fn canonical_preparation_stages_real_pcm_without_dispatch_or_caller_mix_options() {
    let mut f = canonical_fixture();
    let mut mix = f.project.audio.mix.clone();
    mix.voice_gain_db = -3.0;
    mix.music_gain_db = -9.0;
    f.project = apply(
        &f.service,
        &f.project,
        "canonical-source-global-gains",
        Change::SetMixIntent { mix },
    );
    let coordinator = canonical_coordinator(&f);
    let plan = canonical_plan(&f).unwrap();
    assert_eq!(plan.voice_gain_db, -3.0);
    assert_eq!(plan.music_gain_db, -9.0);
    let prepared = coordinator
        .prepare_native_canonical_original_mix_av_audio(
            f.project.id,
            &f.project.stamp(),
            f.project.deliverables[0].id,
            60,
        )
        .unwrap();
    let wav = read_wav(&f, &prepared);
    assert_eq!(wav.len(), 44 + SAMPLES * 4);
    assert_eq!(sha256(&wav), prepared.audio.sha256);
    assert_eq!(
        sha256(&wav[44..]),
        prepared.proof.original_mix.rendered_pcm_sha256
    );
    assert_eq!(
        prepared.proof.original_mix.source_plan_sha256,
        sha256(&serde_json::to_vec(&plan).unwrap())
    );
    assert_ne!(
        prepared.audio.sha256,
        plan.measured_voice.source_asset_sha256
    );
    assert_eq!(
        prepared.proof.measured_voice.source_asset_sha256,
        plan.measured_voice.source_asset_sha256
    );
    for (at, duck) in [
        (1000, 1.0),
        (30_000, 10_f64.powf(-14.0 / 20.0)),
        (80_000, 1.0),
    ] {
        for channel in 0..2 {
            let mixed = f64::from(sample(&f.pcm[0], at, channel)) * 10_f64.powf(-3.0 / 20.0)
                + f64::from(sample(&f.pcm[1], at, channel)) * 10_f64.powf(-9.0 / 20.0) * duck
                + f64::from(sample(&f.pcm[2], at, channel)) * 10_f64.powf(-8.0 / 20.0);
            let expected = (mixed * 32767.0 / 32768.0).round() as i16;
            assert!((i32::from(sample(&wav[44..], at, channel)) - i32::from(expected)).abs() <= 1);
        }
    }
    assert!(!prepared.proof.original_mix.independently_verified_lufs);
    assert!(!prepared.proof.original_mix.independently_verified_true_peak);
    assert!(!prepared.proof.original_mix.owner_release_approved);
    let again = coordinator
        .prepare_native_canonical_original_mix_av_audio(
            f.project.id,
            &f.project.stamp(),
            f.project.deliverables[0].id,
            60,
        )
        .unwrap();
    assert_eq!(prepared.proof, again.proof);
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 1);
    assert_no_native_dispatch(&f, &coordinator);
}

#[test]
fn saved_canonical_mix_edits_change_actual_pcm_and_reject_the_old_staged_source() {
    let mut f = canonical_fixture();
    let coordinator = canonical_coordinator(&f);
    let old_stamp = f.project.stamp();
    let before = coordinator
        .prepare_native_canonical_original_mix_av_audio(
            f.project.id,
            &old_stamp,
            f.project.deliverables[0].id,
            60,
        )
        .unwrap();
    let before_wav = read_wav(&f, &before);
    let mut recipe = canonical_recipe(&f);
    let mix = recipe.original_mix.as_mut().unwrap();
    mix.sfx_gain_db = -20.0;
    mix.duck_attenuation_db = 20.0;
    mix.duck_attack_samples = 960;
    mix.duck_release_samples = 7200;
    save_canonical_recipe(&mut f, "canonical-source-edited-recipe", Some(recipe));
    let resolved = canonical_plan(&f).unwrap();
    assert_eq!(resolved.sfx_gain_db, -20.0);
    assert_eq!(resolved.duck_attenuation_db, 20.0);
    assert_eq!(resolved.duck_attack_samples, 960);
    assert_eq!(resolved.duck_release_samples, 7200);
    assert_eq!(
        resolve_canonical_original_mix_plan(
            &f.service,
            f.project.id,
            &old_stamp,
            f.project.deliverables[0].id
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    assert!(
        verify_original_mix_av_source(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            request(&resolved),
            &before.audio,
            &f.owner_root
        )
        .is_err()
    );
    let after = coordinator
        .prepare_native_canonical_original_mix_av_audio(
            f.project.id,
            &f.project.stamp(),
            f.project.deliverables[0].id,
            60,
        )
        .unwrap();
    let after_wav = read_wav(&f, &after);
    assert_ne!(before.audio.sha256, after.audio.sha256);
    assert_ne!(
        before.proof.original_mix.source_plan_sha256,
        after.proof.original_mix.source_plan_sha256
    );
    // Music before voice is unchanged; deep in the reviewed voice interval the
    // stronger duck reduces music. The independent original SFX tail changes.
    assert_eq!(
        &before_wav[44..44 + 20_000 * 4],
        &after_wav[44..44 + 20_000 * 4]
    );
    assert_ne!(
        &before_wav[44 + 30_000 * 4..44 + 40_000 * 4],
        &after_wav[44 + 30_000 * 4..44 + 40_000 * 4]
    );
    assert_ne!(
        &before_wav[44 + 80_000 * 4..44 + 90_000 * 4],
        &after_wav[44 + 80_000 * 4..44 + 90_000 * 4]
    );
    assert_eq!(read_wav(&f, &before), before_wav);
    assert_no_native_dispatch(&f, &coordinator);
}

#[test]
fn canonical_preparation_rejects_absent_partial_foreign_or_wrong_clock_intent_without_files() {
    let mut f = fixture();
    let coordinator = canonical_coordinator(&f);
    assert_eq!(
        coordinator
            .prepare_native_canonical_original_mix_av_audio(
                f.project.id,
                &f.project.stamp(),
                f.project.deliverables[0].id,
                60,
            )
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
    let mut recipe = canonical_recipe(&f);
    recipe.original_mix.as_mut().unwrap().music_asset_id = None;
    save_canonical_recipe(&mut f, "canonical-source-missing-music", Some(recipe));
    assert_eq!(canonical_plan(&f).unwrap_err().code, ErrorCode::Unsupported);
    let mut recipe = canonical_recipe(&f);
    recipe.scene_intents[0].role = None;
    save_canonical_recipe(&mut f, "canonical-source-missing-role", Some(recipe));
    assert_eq!(
        coordinator
            .prepare_native_canonical_original_mix_av_audio(
                f.project.id,
                &f.project.stamp(),
                f.project.deliverables[0].id,
                60,
            )
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
    let recipe = canonical_recipe(&f);
    save_canonical_recipe(&mut f, "canonical-source-complete-recipe", Some(recipe));
    assert_eq!(
        coordinator
            .prepare_native_canonical_original_mix_av_audio(
                f.project.id,
                &f.project.stamp(),
                f.project.deliverables[0].id,
                59,
            )
            .unwrap_err()
            .code,
        ErrorCode::Unsupported
    );
    for changed in 0..3 {
        let mut stamp = f.project.stamp();
        match changed {
            0 => stamp.resource = "foreign-project-resource".into(),
            1 => stamp.generation = Uuid::new_v4(),
            _ => stamp.revision += 1,
        }
        assert_eq!(
            resolve_canonical_original_mix_plan(
                &f.service,
                f.project.id,
                &stamp,
                f.project.deliverables[0].id
            )
            .unwrap_err()
            .code,
            ErrorCode::StaleReference
        );
    }
    assert!(
        resolve_canonical_original_mix_plan(
            &f.service,
            f.project.id,
            &f.project.stamp(),
            Uuid::new_v4()
        )
        .is_err()
    );
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
    assert_no_native_dispatch(&f, &coordinator);
}

#[test]
fn canonical_resolver_rechecks_actual_cas_bytes_and_rejects_separate_semantic_sfx() {
    let f = canonical_fixture();
    let before = canonical_plan(&f).unwrap();
    let store = f.service.store();
    let path = store
        .lock()
        .verified_blob_path(&before.original_music.source_asset_sha256)
        .unwrap();
    let mut bytes = fs::read(&path).unwrap();
    *bytes.last_mut().unwrap() ^= 1;
    fs::write(&path, bytes).unwrap();
    assert!(canonical_plan(&f).is_err());
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
    assert_eq!(f.service.project(f.project.id).unwrap(), f.project);

    let mut sfx = fixture_with_semantic_sfx();
    let recipe = canonical_recipe(&sfx);
    save_canonical_recipe(&mut sfx, "canonical-source-separate-sfx", Some(recipe));
    assert_eq!(
        canonical_plan(&sfx).unwrap_err().code,
        ErrorCode::Unsupported
    );
    assert_eq!(fs::read_dir(&sfx.owner_root).unwrap().count(), 0);
    assert_eq!(sfx.service.project(sfx.project.id).unwrap(), sfx.project);
}

#[test]
fn portable_bundle_reopens_the_canonical_recipe_and_regenerates_identical_actual_mix_pcm() {
    let f = canonical_fixture();
    let coordinator = canonical_coordinator(&f);
    let old_plan = canonical_plan(&f).unwrap();
    let before = coordinator
        .prepare_native_canonical_original_mix_av_audio(
            f.project.id,
            &f.project.stamp(),
            f.project.deliverables[0].id,
            60,
        )
        .unwrap();
    let before_wav = read_wav(&f, &before);
    let bundle = f.root.path().join("canonical-source-portable-bundle");
    let manifest = f
        .service
        .export_project_bundle(f.project.id, &bundle)
        .unwrap();
    for receipt in [
        &old_plan.measured_voice,
        &old_plan.original_music,
        &old_plan.original_sfx,
    ] {
        assert!(
            manifest
                .blobs
                .iter()
                .any(|blob| blob.sha256 == receipt.source_asset_sha256)
        );
    }
    let destination = f.root.path().join("separate-restored-store");
    fs::create_dir(&destination).unwrap();
    let database = destination.join("studio.sqlite3");
    let restored = StudioService::open(&database).unwrap();
    restored.inspect_project_bundle(&bundle).unwrap();
    let imported = restored.import_project_bundle(&bundle).unwrap();
    assert_eq!(
        imported.production_design.delivery_recipes,
        f.project.production_design.delivery_recipes
    );
    assert_eq!(imported.id, f.project.id);
    assert_eq!(imported.revision, f.project.revision);
    assert_ne!(imported.generation, f.project.generation);
    assert_eq!(
        resolve_canonical_original_mix_plan(
            &restored,
            imported.id,
            &f.project.stamp(),
            imported.deliverables[0].id
        )
        .unwrap_err()
        .code,
        ErrorCode::StaleReference
    );
    let plan = resolve_canonical_original_mix_plan(
        &restored,
        imported.id,
        &imported.stamp(),
        imported.deliverables[0].id,
    )
    .unwrap();
    assert_eq!(plan.measured_voice, old_plan.measured_voice);
    assert_eq!(plan.original_music, old_plan.original_music);
    assert_eq!(plan.original_sfx, old_plan.original_sfx);
    assert_eq!(plan.voice_windows, old_plan.voice_windows);
    let restored_coordinator =
        ProductionCoordinator::new(restored.clone(), no_dispatch_connection(&f)).unwrap();
    let after = restored_coordinator
        .prepare_native_canonical_original_mix_av_audio(
            imported.id,
            &imported.stamp(),
            imported.deliverables[0].id,
            60,
        )
        .unwrap();
    assert_eq!(read_wav(&f, &after), before_wav);
    assert_eq!(before.audio.sha256, after.audio.sha256);
    assert_ne!(before.audio.relative_path, after.audio.relative_path);
    assert_ne!(
        before.proof.original_mix.source_plan_sha256,
        after.proof.original_mix.source_plan_sha256
    );
    assert!(
        restored_coordinator
            .receipts(imported.id, 256)
            .unwrap()
            .is_empty()
    );
    drop(restored_coordinator);
    drop(restored);
    let reopened = StudioService::open(database).unwrap();
    assert_eq!(
        resolve_canonical_original_mix_plan(
            &reopened,
            imported.id,
            &imported.stamp(),
            imported.deliverables[0].id
        )
        .unwrap(),
        plan
    );
    assert_no_native_dispatch(&f, &coordinator);
}

fn unverified_native_inputs(
    f: &Fixture,
) -> (
    MotionCanvasRenderEvidence,
    MltVerifiedLosslessTimeline,
    MltAudioArtifact,
) {
    let project_resource = f.project.resource_key();
    let generation = f.project.generation;
    let revision = f.project.revision;
    let deliverable_id = f.project.deliverables[0].id;
    let rendered = MotionCanvasRenderEvidence {
        project_resource: project_resource.clone(),
        generation,
        revision,
        deliverable_id,
        frame_rate: Rate::new(30, 1).unwrap(),
        segments: vec![],
    };
    let visual = MltVerifiedLosslessTimeline {
        project_resource: project_resource.clone(),
        generation,
        revision,
        deliverable_id,
        recipe_sha256: String::new(),
        frame_count: 0,
        native_frame_count_observed: None,
        transport_pcm_audio: false,
        native_render_profile: String::new(),
        provider_project_cleanup: String::new(),
        native_job_ref: String::new(),
        provider_revision: String::new(),
        artifact_path: "unread-visual.mkv".into(),
        artifact_sha256: String::new(),
        artifact_bytes: 0,
        source: MltPreparedMezzanines {
            project_resource,
            generation,
            revision,
            deliverable_id,
            input_sha256: String::new(),
            fps_num: 30,
            fps_den: 1,
            total_frames: 0,
            verified_video_segments: vec![],
            evidence_scope: "unverified-negative-fixture".into(),
        },
        evidence_scope: "unverified-negative-fixture".into(),
    };
    let audio = MltAudioArtifact {
        relative_path: "unread-mixed.wav".into(),
        sha256: String::new(),
        sample_rate: 48_000,
        channels: 2,
    };
    (rendered, visual, audio)
}

#[tokio::test]
async fn canonical_native_master_rejects_mastering_targets_before_any_source_or_effect() {
    for lufs in [true, false] {
        let mut f = fixture();
        let mut mix = f.project.audio.mix.clone();
        if lufs {
            mix.target_lufs = Some(-14.0);
        } else {
            mix.target_true_peak_dbfs = Some(-1.0);
        }
        f.project = apply(
            &f.service,
            &f.project,
            "canonical-master-target",
            Change::SetMixIntent { mix },
        );
        let coordinator = canonical_coordinator(&f);
        // No saved recipe or verified native media exists: only the target guard can
        // produce this exact error. No successful native receipt is fabricated.
        let (rendered, visual, audio) = unverified_native_inputs(&f);
        let error = coordinator
            .assemble_native_mlt_multisegment_original_mix_master(
                f.project.id,
                &f.project.stamp(),
                MltMultisegmentOriginalMixMasterRequest {
                    request_id: "canonical-target-guard",
                    deliverable_id: f.project.deliverables[0].id,
                    rendered: &rendered,
                    visual: &visual,
                    audio: &audio,
                },
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert_eq!(
            error.message,
            "Canonical original mix mastering does not support LUFS or true-peak targets"
        );
        assert!(error.outcome_known);
        assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
        assert_no_native_dispatch(&f, &coordinator);
    }
}

#[tokio::test]
async fn canonical_native_master_requires_saved_recipe_and_refuses_unverified_inputs_without_dispatch()
 {
    let mut f = fixture();
    let coordinator = canonical_coordinator(&f);
    let (rendered, visual, audio) = unverified_native_inputs(&f);
    let error = coordinator
        .assemble_native_mlt_multisegment_original_mix_master(
            f.project.id,
            &f.project.stamp(),
            MltMultisegmentOriginalMixMasterRequest {
                request_id: "canonical-missing-recipe",
                deliverable_id: f.project.deliverables[0].id,
                rendered: &rendered,
                visual: &visual,
                audio: &audio,
            },
        )
        .await
        .unwrap_err();
    assert_eq!(error.code, ErrorCode::Unsupported);
    let recipe = canonical_recipe(&f);
    save_canonical_recipe(&mut f, "canonical-master-save-recipe", Some(recipe));
    let (rendered, visual, audio) = unverified_native_inputs(&f);
    assert!(
        coordinator
            .assemble_native_mlt_multisegment_original_mix_master(
                f.project.id,
                &f.project.stamp(),
                MltMultisegmentOriginalMixMasterRequest {
                    request_id: "canonical-unverified-visual",
                    deliverable_id: f.project.deliverables[0].id,
                    rendered: &rendered,
                    visual: &visual,
                    audio: &audio,
                },
            )
            .await
            .is_err()
    );
    assert_eq!(fs::read_dir(&f.owner_root).unwrap().count(), 0);
    assert_no_native_dispatch(&f, &coordinator);
}
