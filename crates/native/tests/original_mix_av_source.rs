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

/// Synthetic protocol data stored through the real application journal.
/// These tests prove source custody and rejection, not a native render, pixels,
/// an AAC master, or an externally executed provider operation.
mod canonical_film_source_custody {
    use super::*;
    use motionwright_domain::{BlendMode, CanvasNode, CoordinateSpace, NodeStyle};
    use motionwright_native::{
        assembly::preflight_multi_segment_mlt,
        film::{FilmBuildOptions, MotionCanvasSegment, build_motion_canvas_segments},
        production::MotionCanvasSegmentEvidence,
    };
    use motionwright_storage::{ProductionReceiptInput, ProductionReceiptWatermark};
    use semwright_native_sdk::{
        Value, json,
        types::{Execution, InvocationProvenance, SourceKind},
    };
    use semwright_semantic_composition::canonical_digest;
    use std::collections::{BTreeMap, BTreeSet};

    const SOURCE_ERROR: &str =
        "Canonical Motion Canvas Film is not bound to its original source receipts";
    const UNKNOWN_ERROR: &str = "Canonical Motion Canvas source has an UNKNOWN original receipt; reconcile without redispatch";

    fn canvas_fixture(scene_count: usize) -> Fixture {
        let mut f = canonical_fixture();
        for index in f.project.scenes.len()..scene_count {
            f.project = apply(
                &f.service,
                &f.project,
                &format!("film-source-extra-scene-{index}"),
                Change::AddScene {
                    name: format!("Film source scene {index}"),
                    objective: "Complete canonical Film source identity".into(),
                    duration_seconds: 1,
                },
            );
        }
        let scene_ids: Vec<_> = f.project.scenes.iter().map(|scene| scene.id).collect();
        for (index, scene_id) in scene_ids.into_iter().enumerate() {
            if scene_count == 33 {
                f.project = apply(
                    &f.service,
                    &f.project,
                    &format!("film-source-scene-clock-{index}"),
                    Change::SetSceneDuration {
                        scene_id,
                        duration: RationalTime::new(if index == 32 { 28 } else { 1 }, 30).unwrap(),
                    },
                );
            }
            f.project = apply(
                &f.service,
                &f.project,
                &format!("film-source-node-{index}"),
                Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::now_v7(),
                        name: "Original Film rectangle".into(),
                        kind: "rectangle".into(),
                        parent_id: None,
                        x: 400.0,
                        y: 250.0,
                        width: 160.0,
                        height: 120.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: None,
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#F5F5F2".into()),
                            stroke: None,
                            stroke_width: 0.0,
                            font_family: None,
                            font_size: None,
                            font_weight: None,
                            line_height: None,
                            blend_mode: BlendMode::Normal,
                        },
                        relations: vec![],
                        property_locks: BTreeSet::new(),
                        keyframes: vec![],
                    },
                },
            );
        }
        let recipe = canonical_recipe(&f);
        save_canonical_recipe(&mut f, "film-source-complete-recipe", Some(recipe));
        assert_eq!(canonical_plan(&f).unwrap().sample_frames, 96_000);
        f
    }

    fn films(
        f: &Fixture,
        project: &Project,
        options: &FilmBuildOptions,
    ) -> Vec<MotionCanvasSegment> {
        build_motion_canvas_segments(project, f.project.deliverables[0].id, options).unwrap()
    }

    fn options(f: &Fixture) -> FilmBuildOptions {
        canonical_original_mix_film_options(&f.project, f.project.deliverables[0].id).unwrap()
    }

    fn request_sha(command: &str, args: &Value) -> String {
        sha256(&serde_json::to_vec(&(command, args)).unwrap())
    }

    fn typed_execution(command: &str) -> Execution {
        Execution {
            backend: "synthetic-protocol-contract-fixture".into(),
            duration_ms: 0,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            provenance: Some(InvocationProvenance {
                provider: "driver:motion-canvas".into(),
                source: SourceKind::Driver,
                provider_version: "fixture/1".into(),
                capability_version: "1".into(),
                descriptor_sha256: sha256(command.as_bytes()),
                untrusted_metadata: true,
                catalog_revision: 1,
                execution_provider: Some("driver:motion-canvas".into()),
                provider_generation: Some(42),
            }),
            dry_run: false,
        }
    }

    struct SyntheticMotionSource {
        rendered: MotionCanvasRenderEvidence,
        rows: Vec<ProductionReceiptInput>,
        manifests: Vec<(String, Vec<u8>)>,
        film_digests: Vec<String>,
    }

    fn synthetic_source(f: &Fixture, source: &[MotionCanvasSegment]) -> SyntheticMotionSource {
        let connection = no_dispatch_connection(f).identity().unwrap();
        let profile = &f.project.deliverables[0];
        let mut segments = Vec::new();
        let mut rows = Vec::new();
        let mut manifests = Vec::new();
        let mut film_digests = Vec::new();
        for (index, film) in source.iter().enumerate() {
            let prefix = format!("source-contract-fixture:motion:{index}");
            let intent = serde_json::to_value(&film.film).unwrap();
            let intent_digest = canonical_digest(&film.film).unwrap().as_str().to_owned();
            let plan_ref = sha256(format!("synthetic-source-plan-{index}").as_bytes());
            // This synthetic token is not calculated by the native compiler.
            let fingerprint = sha256(format!("synthetic-native-fingerprint-{index}").as_bytes());
            let job_ref = format!("synthetic-source-job-{index}");
            let directory = format!("render-{index:032x}");
            let manifest_path = format!("{directory}/artifact-manifest.json");
            let manifest = serde_json::to_vec(&json!({
                "plan": {
                    "width": profile.width, "height": profile.height,
                    "fps": profile.frame_rate.num, "fps_denominator": profile.frame_rate.den,
                    "frame_count": film.frame_count, "first_frame": 0,
                    "end_frame_exclusive": film.frame_count,
                },
                "frames": (0..film.frame_count).map(|frame| json!({
                    "index": frame, "file": format!("frames/{frame:06}.png"),
                    "bytes": 128, "sha256": "a".repeat(64),
                })).collect::<Vec<_>>(),
            }))
            .unwrap();
            let artifact = json!({
                "directory": directory, "manifest": manifest_path,
                "manifest_sha256": sha256(&manifest), "frame_count": film.frame_count,
            });
            // Data shape deliberately passes the existing pure validator;
            // this fixture never claims that these checks executed natively.
            let verification = json!({
                "report": {"execution_status": "completed", "support_level": "native"},
                "measurement": {
                    "findings": [],
                    "validation": {"checks": [{"verdict": "PASS"}]},
                },
            });
            let stages = [
                (
                    "plan",
                    "driver.motion-canvas.composition.plan",
                    json!({
                        "film": &intent,
                        "budget": {"max_iterations": 8, "max_operations": 4096,
                            "max_findings": 1024, "max_observations": 128, "max_elapsed_ms": 120000},
                    }),
                    json!({
                        "plan_ref": &plan_ref, "resulting_fingerprint": &fingerprint, "repair": false,
                        "plan": {"digest": &plan_ref, "body": {
                            "intent": &intent, "intent_digest": &intent_digest,
                        }},
                    }),
                    false,
                ),
                (
                    "apply",
                    "driver.motion-canvas.composition.apply",
                    json!({"plan_ref": &plan_ref, "dry_run": false}),
                    json!({
                        "plan_ref": &plan_ref, "intent_digest": &intent_digest,
                        "fingerprint": &fingerprint, "applied": true, "execution_status": "completed",
                    }),
                    true,
                ),
                (
                    "render-start",
                    "driver.motion-canvas.render.start",
                    json!({
                        "expected_fingerprint": &fingerprint,
                        "profile": {"first_frame": 0, "end_frame_exclusive": film.frame_count,
                            "scale": "full", "transparent": false, "timeout_ms": 300000},
                    }),
                    json!({"job_ref": &job_ref, "state": "queued"}),
                    true,
                ),
                (
                    "render-result",
                    "driver.motion-canvas.render.result",
                    json!({"job_ref": &job_ref}),
                    json!({"job_ref": &job_ref, "state": "succeeded", "artifact": &artifact}),
                    false,
                ),
                (
                    "verify",
                    "driver.motion-canvas.composition.verify",
                    json!({"plan_ref": &plan_ref, "job_ref": &job_ref}),
                    verification.clone(),
                    false,
                ),
            ];
            for (stage, command, args, data, mutation) in stages {
                let request_id = format!("{prefix}:{stage}");
                let request_sha256 = request_sha(command, &args);
                let job = args.get("job_ref").cloned().unwrap_or(Value::Null);
                rows.push(ProductionReceiptInput {
                    project_id: f.project.id,
                    generation: f.project.generation,
                    revision: f.project.revision,
                    request_id: request_id.clone(),
                    request_sha256: request_sha256.clone(),
                    command: command.into(),
                    stage: "dispatching".into(),
                    payload: json!({"mutation": mutation, "connection": &connection, "job_ref": &job}),
                });
                rows.push(ProductionReceiptInput {
                    project_id: f.project.id,
                    generation: f.project.generation,
                    revision: f.project.revision,
                    request_id,
                    request_sha256,
                    command: command.into(),
                    stage: "completed".into(),
                    payload: json!({
                        "broker_request_id": format!("synthetic-broker-{index}-{stage}"),
                        "job_ref": &job,
                        "result": {
                            "data": data, "execution": typed_execution(command),
                            "warnings": [], "provenance_checked": true,
                        },
                    }),
                });
            }
            film_digests.push(intent_digest);
            manifests.push((manifest_path, manifest));
            segments.push(MotionCanvasSegmentEvidence {
                segment_id: film.id.clone(),
                scene_ids: film.scene_ids.clone(),
                frame_count: film.frame_count,
                plan_ref,
                fingerprint,
                job_ref,
                artifact,
                verification,
            });
        }
        SyntheticMotionSource {
            rendered: MotionCanvasRenderEvidence {
                project_resource: f.project.resource_key(),
                generation: f.project.generation,
                revision: f.project.revision,
                deliverable_id: profile.id,
                frame_rate: Rate::new(30, 1).unwrap(),
                segments,
            },
            rows,
            manifests,
            film_digests,
        }
    }

    fn append_rows(f: &Fixture, rows: &[ProductionReceiptInput]) -> Vec<Uuid> {
        rows.iter()
            .map(|row| f.service.append_production_receipt(row.clone()).unwrap().id)
            .collect()
    }

    fn append_unrelated_rows(f: &Fixture, count: usize) {
        for index in 0..count {
            f.service
                .append_production_receipt(ProductionReceiptInput {
                    project_id: f.project.id,
                    generation: f.project.generation,
                    revision: f.project.revision,
                    request_id: format!("unrelated-source-observation-{index}"),
                    request_sha256: sha256(b"unrelated fixture observation"),
                    command: "driver.motion-canvas.render.status".into(),
                    stage: "completed".into(),
                    payload: json!({"fixture": "unrelated synthetic observation"}),
                })
                .unwrap();
        }
    }

    fn inventory(root: &Path) -> BTreeMap<PathBuf, Vec<u8>> {
        let mut output = BTreeMap::new();
        for entry in fs::read_dir(root).unwrap() {
            let entry = entry.unwrap();
            if entry.file_type().unwrap().is_dir() {
                output.extend(inventory(&entry.path()));
            } else {
                output.insert(entry.path(), fs::read(entry.path()).unwrap());
            }
        }
        output
    }

    fn assert_read_only(
        f: &Fixture,
        watermark: &ProductionReceiptWatermark,
        output: &BTreeMap<PathBuf, Vec<u8>>,
    ) {
        assert_eq!(
            &f.service
                .production_receipt_watermark(f.project.id)
                .unwrap(),
            watermark
        );
        assert_eq!(&inventory(&f.owner_root), output);
        assert!(!f.root.path().join("native-dispatch-marker").exists());
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
    }

    fn reject_source(f: &Fixture, rendered: &MotionCanvasRenderEvidence, unknown: bool) {
        let coordinator = canonical_coordinator(f);
        let before = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let output = inventory(&f.owner_root);
        let error = coordinator
            .verify_native_canonical_original_mix_motion_source(
                f.project.id,
                &f.project.stamp(),
                f.project.deliverables[0].id,
                rendered,
            )
            .unwrap_err();
        assert_eq!(
            error.code,
            if unknown {
                ErrorCode::Conflict
            } else {
                ErrorCode::StaleReference
            }
        );
        assert_eq!(
            error.message,
            if unknown { UNKNOWN_ERROR } else { SOURCE_ERROR }
        );
        assert_eq!(error.outcome_known, !unknown);
        assert_read_only(f, &before, &output);
    }

    #[test]
    fn canonical_film_source_uses_full_original_journal_beyond_256_and_returns_original_ids() {
        let f = canvas_fixture(2);
        let source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
        let ids = append_rows(&f, &source.rows);
        append_unrelated_rows(&f, 300);
        let coordinator = canonical_coordinator(&f);
        let before = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let output = inventory(&f.owner_root);
        assert_eq!(before.count, 310);
        let bound = coordinator
            .verify_native_canonical_original_mix_motion_source(
                f.project.id,
                &f.project.stamp(),
                f.project.deliverables[0].id,
                &source.rendered,
            )
            .unwrap();
        assert_eq!(bound.len(), 1);
        assert_eq!(bound[0].film_sha256, source.film_digests[0]);
        assert_eq!(
            bound[0].completed_receipt_ids,
            [ids[1], ids[3], ids[5], ids[7], ids[9]]
        );
        assert_eq!(bound[0].provider_generation, 42);
        assert_eq!(bound[0].native_job_ref, source.rendered.segments[0].job_ref);
        let serialized = serde_json::to_value(&bound[0]).unwrap();
        assert!(serialized.get("master").is_none());
        assert!(serialized.get("pixels_verified").is_none());
        assert_read_only(&f, &before, &output);
    }

    #[test]
    fn canonical_film_source_rejects_changed_role_archetype_and_content_with_same_segment_tuple() {
        for variant in ["role", "archetype", "content"] {
            let f = canvas_fixture(2);
            let canonical_options = options(&f);
            let expected = films(&f, &f.project, &canonical_options);
            let mut other_options = canonical_options.clone();
            let mut other_project = f.project.clone();
            match variant {
                "role" => other_options.scene_intents[0].role = NarrativeRole::Problem,
                "archetype" => {
                    other_options.scene_intents[0].archetype = Archetype::ObjectSpotlight
                }
                "content" => other_project.scenes[0].nodes[0].style.fill = Some("#33CC99".into()),
                _ => unreachable!(),
            }
            other_project.validate().unwrap();
            assert_eq!(other_project.stamp(), f.project.stamp());
            let alternate = films(&f, &other_project, &other_options);
            assert_eq!(expected.len(), alternate.len());
            for (left, right) in expected.iter().zip(&alternate) {
                assert_eq!(left.id, right.id);
                assert_eq!(left.scene_ids, right.scene_ids);
                assert_eq!(left.frame_count, right.frame_count);
            }
            assert_ne!(
                canonical_digest(&expected[0].film).unwrap(),
                canonical_digest(&alternate[0].film).unwrap(),
                "Full typed Film must change for {variant}",
            );
            let source = synthetic_source(&f, &alternate);
            append_rows(&f, &source.rows);
            reject_source(&f, &source.rendered, false);
        }
    }

    #[test]
    fn canonical_film_source_rejects_alias_that_passes_existing_manifest_only_multisegment_preflight()
     {
        let f = canvas_fixture(33);
        let canonical_options = options(&f);
        let mut other_options = canonical_options.clone();
        other_options.scene_intents[0].role = NarrativeRole::Problem;
        let alternate = films(&f, &f.project, &other_options);
        assert_eq!(alternate.len(), 2);
        assert_eq!(
            alternate
                .iter()
                .map(|film| film.frame_count)
                .collect::<Vec<_>>(),
            [32, 28]
        );
        let source = synthetic_source(&f, &alternate);
        for (path, bytes) in &source.manifests {
            let path = f.owner_root.join(path);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, bytes).unwrap();
        }
        // Physical manifests exist and have matching SHA, but no native frames
        // exist. This demonstrates the old source-tuple alias without claiming
        // a render or treating the old manifest-only admission as pixel proof.
        let old_admission = preflight_multi_segment_mlt(
            &f.project,
            f.project.deliverables[0].id,
            &canonical_options,
            &source.rendered,
            &f.owner_root,
        )
        .unwrap();
        assert_eq!(old_admission.total_frames, 60);
        assert_eq!(
            old_admission.evidence_scope,
            "manifest-digest-verified-not-composited-mp4"
        );
        append_rows(&f, &source.rows);
        reject_source(&f, &source.rendered, false);
    }

    #[test]
    fn canonical_film_source_requires_returned_full_intent_even_with_canonical_request_digest() {
        let f = canvas_fixture(2);
        let canonical = films(&f, &f.project, &options(&f));
        let canonical_source = synthetic_source(&f, &canonical);
        let mut other_options = options(&f);
        other_options.scene_intents[0].role = NarrativeRole::Problem;
        let mut source = synthetic_source(&f, &films(&f, &f.project, &other_options));
        for (actual, expected) in source.rows.iter_mut().zip(&canonical_source.rows).take(2) {
            actual.request_sha256 = expected.request_sha256.clone();
        }
        append_rows(&f, &source.rows);
        reject_source(&f, &source.rendered, false);
    }

    #[test]
    fn canonical_film_source_rejects_missing_or_duplicate_original_stage_buried_beyond_256() {
        for duplicate in [false, true] {
            let f = canvas_fixture(2);
            let mut source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
            if duplicate {
                source.rows.insert(4, source.rows[3].clone());
            } else {
                source.rows.remove(3);
            }
            append_rows(&f, &source.rows);
            append_unrelated_rows(&f, 300);
            reject_source(&f, &source.rendered, false);
        }
    }

    #[test]
    fn canonical_film_source_preserves_unknown_original_disposition_without_retry() {
        let f = canvas_fixture(2);
        let mut source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
        let mut unknown = source.rows[3].clone();
        unknown.stage = "outcome_unknown".into();
        unknown.payload = json!({"job_ref": null, "error": {"outcome_known": false}});
        source.rows.insert(3, unknown);
        append_rows(&f, &source.rows);
        append_unrelated_rows(&f, 300);
        reject_source(&f, &source.rendered, true);
    }

    #[test]
    fn canonical_film_source_binds_actual_stage_order_and_exact_request_clock() {
        for different_clock in [false, true] {
            let f = canvas_fixture(2);
            let mut source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
            if different_clock {
                let wrong = request_sha(
                    "driver.motion-canvas.render.start",
                    &json!({
                        "expected_fingerprint": source.rendered.segments[0].fingerprint,
                        "profile": {"first_frame": 0, "end_frame_exclusive": 59,
                            "scale": "full", "transparent": false, "timeout_ms": 300000},
                    }),
                );
                source.rows[4].request_sha256 = wrong.clone();
                source.rows[5].request_sha256 = wrong;
            } else {
                // Store accepts independent request observations; custody must
                // reject an apply recorded before its own source plan.
                source.rows.swap(0, 2);
                source.rows.swap(1, 3);
            }
            append_rows(&f, &source.rows);
            reject_source(&f, &source.rendered, false);
        }
    }

    #[test]
    fn canonical_film_source_rejects_foreign_stage_authority_and_metadata_omissions() {
        for variant in [
            "connection",
            "generation",
            "provider",
            "source",
            "missing_generation",
            "invalid_descriptor",
            "missing_execution_provider",
            "foreign_execution_provider",
            "dry_run",
            "driver_claimed_builtin_metadata",
            "missing_dry_run",
            "missing_untrusted",
            "unchecked",
        ] {
            let f = canvas_fixture(2);
            let mut source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
            match variant {
                "connection" => source.rows[2].payload["connection"] = json!("e".repeat(64)),
                "generation" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["provider_generation"] =
                        json!(43)
                }
                "provider" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["provider"] =
                        json!("driver:blender")
                }
                "source" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["source"] =
                        serde_json::to_value(SourceKind::Builtin).unwrap()
                }
                "missing_generation" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["provider_generation"] =
                        Value::Null
                }
                "invalid_descriptor" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["descriptor_sha256"] =
                        json!("not-a-sha256")
                }
                "missing_execution_provider" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["execution_provider"] =
                        Value::Null
                }
                "foreign_execution_provider" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["execution_provider"] =
                        json!("driver:blender")
                }
                "dry_run" => source.rows[3].payload["result"]["execution"]["dry_run"] = json!(true),
                "driver_claimed_builtin_metadata" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]["untrusted_metadata"] =
                        json!(false)
                }
                "missing_dry_run" => {
                    source.rows[3].payload["result"]["execution"]
                        .as_object_mut()
                        .unwrap()
                        .remove("dry_run");
                }
                "missing_untrusted" => {
                    source.rows[3].payload["result"]["execution"]["provenance"]
                        .as_object_mut()
                        .unwrap()
                        .remove("untrusted_metadata");
                }
                "unchecked" => {
                    source.rows[3].payload["result"]["provenance_checked"] = json!(false)
                }
                _ => unreachable!(),
            }
            append_rows(&f, &source.rows);
            reject_source(&f, &source.rendered, false);
        }
    }

    #[test]
    fn canonical_film_source_keeps_caller_artifact_and_verification_bound_to_original_results() {
        let f = canvas_fixture(2);
        let source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
        append_rows(&f, &source.rows);
        for artifact in [false, true] {
            let mut altered = source.rendered.clone();
            if artifact {
                altered.segments[0].artifact["manifest_sha256"] = json!("f".repeat(64));
            } else {
                // Even additional caller PASS checks cannot replace the
                // original provider verification value.
                altered.segments[0].verification["measurement"]["validation"]["checks"]
                    .as_array_mut()
                    .unwrap()
                    .push(json!({"verdict": "PASS"}));
            }
            reject_source(&f, &altered, false);
        }
    }

    #[test]
    fn canonical_film_source_requires_full_stamp_profile_clock_and_current_original_rows() {
        let f = canvas_fixture(2);
        let source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
        append_rows(&f, &source.rows);
        for variant in ["resource", "generation", "revision", "profile", "rate"] {
            let mut altered = source.rendered.clone();
            match variant {
                "resource" => altered.project_resource = "motionwright:foreign-project".into(),
                "generation" => altered.generation = Uuid::new_v4(),
                "revision" => altered.revision -= 1,
                "profile" => altered.deliverable_id = Uuid::new_v4(),
                "rate" => altered.frame_rate = Rate::new(24, 1).unwrap(),
                _ => unreachable!(),
            }
            reject_source(&f, &altered, false);
        }
        let old = canvas_fixture(2);
        let mut old_source = synthetic_source(&old, &films(&old, &old.project, &options(&old)));
        for row in &mut old_source.rows {
            row.revision -= 1;
        }
        append_rows(&old, &old_source.rows);
        reject_source(&old, &old_source.rendered, false);
    }

    #[test]
    fn canonical_film_source_never_promotes_actual_foreign_project_journal_rows() {
        let f = canvas_fixture(2);
        let mut source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
        let foreign = f
            .service
            .create_project("Foreign Film source receipt owner")
            .unwrap();
        for row in &mut source.rows {
            row.project_id = foreign.id;
            row.generation = foreign.generation;
            row.revision = foreign.revision;
        }
        append_rows(&f, &source.rows);
        assert_eq!(
            f.service
                .production_receipt_watermark(foreign.id)
                .unwrap()
                .count,
            10
        );
        assert_eq!(
            f.service
                .production_receipt_watermark(f.project.id)
                .unwrap()
                .count,
            0
        );
        reject_source(&f, &source.rendered, false);
    }

    #[tokio::test]
    async fn canonical_film_source_master_entry_rejects_alias_before_downstream_media_or_effect() {
        let f = canvas_fixture(33);
        let mut other_options = options(&f);
        other_options.scene_intents[0].role = NarrativeRole::Problem;
        let source = synthetic_source(&f, &films(&f, &f.project, &other_options));
        assert_eq!(source.rendered.segments.len(), 2);
        append_rows(&f, &source.rows);
        let coordinator = canonical_coordinator(&f);
        let before = f
            .service
            .production_receipt_watermark(f.project.id)
            .unwrap();
        let output = inventory(&f.owner_root);
        // These downstream media handles deliberately remain unopened: this
        // test requires the exact source-custody error from the public master
        // entry. It is not a fixture of successful FFV1 or native mux output.
        let (_, visual, audio) = unverified_native_inputs(&f);
        let error = coordinator
            .assemble_native_mlt_multisegment_original_mix_master(
                f.project.id,
                &f.project.stamp(),
                MltMultisegmentOriginalMixMasterRequest {
                    request_id: "canonical-film-alias-guard",
                    deliverable_id: f.project.deliverables[0].id,
                    rendered: &source.rendered,
                    visual: &visual,
                    audio: &audio,
                },
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::StaleReference);
        assert_eq!(error.message, SOURCE_ERROR);
        assert!(error.outcome_known);
        assert_read_only(&f, &before, &output);
    }

    mod original_mix_attempt_classification {
        use super::*;
        use motionwright_native::{
            mlt_edit_plan::{MltNativeVideoRecipe, mlt_native_video_recipe},
            original_mix::write_mix_wav,
        };
        use semwright_native_sdk::Error;

        // Real Project/CAS/SQLite plus original synthetic protocol rows. No
        // fixture invokes a provider or claims FFV1/AAC/pixel verification.
        fn source_fixture() -> (Fixture, SyntheticMotionSource) {
            let f = canvas_fixture(33);
            let source = synthetic_source(&f, &films(&f, &f.project, &options(&f)));
            assert_eq!(source.rendered.segments.len(), 2);
            for (path, bytes) in &source.manifests {
                let path = f.owner_root.join(path);
                fs::create_dir_all(path.parent().unwrap()).unwrap();
                fs::write(path, bytes).unwrap();
            }
            (f, source)
        }

        fn observe(f: &Fixture, source: &SyntheticMotionSource) -> Value {
            let coordinator = canonical_coordinator(f);
            let watermark = f
                .service
                .production_receipt_watermark(f.project.id)
                .unwrap();
            let output = inventory(&f.owner_root);
            let result = coordinator
                .classify_native_canonical_original_mix_master(
                    f.project.id,
                    &f.project.stamp(),
                    f.project.deliverables[0].id,
                    &source.rendered,
                )
                .unwrap();
            assert_read_only(f, &watermark, &output);
            let value = serde_json::to_value(result).unwrap();
            assert!(value.get("export_token").is_none());
            assert!(value.get("can_dispatch").is_none());
            assert!(value.get("can_retry").is_none());
            assert_eq!(
                value["evidence_scope"],
                "original-application-journal-disposition-only-no-effect-grant-no-retry-no-final-media-verdict"
            );
            value
        }

        fn recipe(f: &Fixture, source: &SyntheticMotionSource) -> MltNativeVideoRecipe {
            mlt_native_video_recipe(
                &preflight_multi_segment_mlt(
                    &f.project,
                    f.project.deliverables[0].id,
                    &options(f),
                    &source.rendered,
                    &f.owner_root,
                )
                .unwrap(),
            )
            .unwrap()
        }

        fn mlt_pair(
            f: &Fixture,
            id: &str,
            command: &str,
            mutation: bool,
            args: Value,
            data: Value,
        ) -> [ProductionReceiptInput; 2] {
            let mut execution = typed_execution(command);
            let provenance = execution.provenance.as_mut().unwrap();
            provenance.provider = "driver:mlt-video".into();
            provenance.execution_provider = Some("driver:mlt-video".into());
            provenance.provider_generation = Some(57);
            let job = args
                .get("job_ref")
                .or_else(|| args.get("job"))
                .cloned()
                .unwrap_or(Value::Null);
            let digest = request_sha(command, &args);
            let dispatch = ProductionReceiptInput {
                project_id: f.project.id,
                generation: f.project.generation,
                revision: f.project.revision,
                request_id: id.into(),
                request_sha256: digest.clone(),
                command: command.into(),
                stage: "dispatching".into(),
                payload: json!({
                    "connection": no_dispatch_connection(f).identity().unwrap(),
                    "mutation": mutation, "job_ref": &job,
                }),
            };
            let completed = ProductionReceiptInput {
                request_sha256: digest,
                stage: "completed".into(),
                payload: json!({
                    "broker_request_id": format!("synthetic-broker-{id}"), "job_ref": &job,
                    "result": {"data": data, "execution": execution, "warnings": [], "provenance_checked": true},
                }),
                ..dispatch.clone()
            };
            [dispatch, completed]
        }

        fn ffv1_pair(
            f: &Fixture,
            source: &SyntheticMotionSource,
            identity: &str,
            index: usize,
        ) -> [ProductionReceiptInput; 2] {
            let recipe = recipe(f, source);
            let clip = &recipe.clips[index];
            mlt_pair(
                f,
                &format!("{identity}-visual-prep:ffv1:{index:02}"),
                "driver.mlt-video.frames.encode",
                true,
                json!({
                    "root": "output", "manifest_path": &clip.source_manifest_path,
                    "expected_manifest_sha256": &clip.source_manifest_sha256,
                    "output_path": &clip.ffv1_output_path, "max_bytes": clip.ffv1_max_bytes,
                }),
                json!({"scope": "synthetic-original-FFV1-disposition-no-media"}),
            )
        }

        fn failed_pair(
            f: &Fixture,
            source: &SyntheticMotionSource,
            identity: &str,
            unknown: bool,
        ) -> [ProductionReceiptInput; 2] {
            let mut pair = ffv1_pair(f, source, identity, 0);
            let error = Error::new(ErrorCode::PolicyDenied, "Synthetic known foreground denial");
            pair[1].stage = if unknown {
                "outcome_unknown"
            } else {
                "failed_known"
            }
            .into();
            pair[1].payload = json!({
                "error": if unknown { error.uncertain() } else { error },
                "mutation": true, "job_ref": null,
            });
            pair
        }

        #[test]
        fn original_mix_attempt_not_started_requires_and_retains_complete_original_motion_sources()
        {
            let (f, source) = source_fixture();
            let ids = append_rows(&f, &source.rows);
            let first = observe(&f, &source);
            assert_eq!(first["state"], "not_started");
            assert_eq!(first["reason"], "no_assembly_receipts");
            assert_eq!(first["outcome_known"], true);
            assert_eq!(first["frame_count"], 60);
            assert_eq!(
                first["canonical_motion_source"].as_array().unwrap().len(),
                2
            );
            assert_eq!(
                first["original_receipts"].as_array().unwrap().len(),
                ids.len()
            );
            assert_eq!(first["original_terminal_receipt_id"], Value::Null);
            append_unrelated_rows(&f, 300);
            let second = observe(&f, &source);
            assert_eq!(second["state"], "not_started");
            assert_eq!(second["native_request_id"], first["native_request_id"]);
            assert_eq!(second["source_plan_sha256"], first["source_plan_sha256"]);
            assert_eq!(
                second["selected_receipt_ids"],
                first["selected_receipt_ids"]
            );
            assert_eq!(second["journal_receipt_count"], 320);
        }

        #[test]
        fn original_mix_attempt_missing_or_unknown_motion_stage_never_looks_unstarted() {
            for unknown in [false, true] {
                let (f, mut source) = source_fixture();
                if unknown {
                    source.rows[3].stage = "outcome_unknown".into();
                    source.rows[3].payload = json!({"error": {
                        "code": "Timeout", "message": "Synthetic interrupted original apply", "outcome_known": false,
                    }, "mutation": true, "job_ref": null});
                } else {
                    source.rows.remove(3);
                }
                append_rows(&f, &source.rows);
                append_unrelated_rows(&f, 300);
                let observed = observe(&f, &source);
                assert_eq!(observed["state"], "uncertain");
                assert_eq!(observed["outcome_known"], false);
                assert_eq!(
                    observed["reason"],
                    if unknown {
                        "original_unknown"
                    } else {
                        "unresolved_motion_source"
                    }
                );
            }
        }

        #[test]
        fn original_mix_attempt_partial_ffv1_before_mux_cannot_be_restarted() {
            let (f, source) = source_fixture();
            append_rows(&f, &source.rows);
            let identity = observe(&f, &source)["native_request_id"]
                .as_str()
                .unwrap()
                .to_owned();
            let pair = ffv1_pair(&f, &source, &identity, 0);
            let dispatch = f
                .service
                .append_production_receipt(pair[0].clone())
                .unwrap();
            let started = observe(&f, &source);
            assert_eq!(started["state"], "uncertain");
            assert_eq!(started["reason"], "incomplete_original_stage");
            assert!(
                started["selected_receipt_ids"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(dispatch.id))
            );
            f.service
                .append_production_receipt(pair[1].clone())
                .unwrap();
            append_unrelated_rows(&f, 300);
            let one_completed = observe(&f, &source);
            assert_eq!(one_completed["state"], "uncertain");
            assert_eq!(one_completed["reason"], "incomplete_original_stage");
            assert_eq!(one_completed["outcome_known"], false);
        }

        #[test]
        fn original_mix_attempt_old_unknown_precedes_extra_completed_rows_and_window_limits() {
            let (f, source) = source_fixture();
            append_rows(&f, &source.rows);
            let identity = observe(&f, &source)["native_request_id"]
                .as_str()
                .unwrap()
                .to_owned();
            let pair = failed_pair(&f, &source, &identity, true);
            let ids = append_rows(&f, &pair);
            let completed = ffv1_pair(&f, &source, &identity, 0)[1].clone();
            // Storage permits terminal observations, never a second dispatch.
            f.service
                .append_production_receipt(completed.clone())
                .unwrap();
            f.service.append_production_receipt(completed).unwrap();
            append_unrelated_rows(&f, 300);
            let observed = observe(&f, &source);
            assert_eq!(observed["state"], "uncertain");
            assert_eq!(observed["reason"], "original_unknown");
            assert_eq!(observed["outcome_known"], false);
            assert!(
                observed["selected_receipt_ids"]
                    .as_array()
                    .unwrap()
                    .contains(&json!(ids[1]))
            );
            assert_eq!(observed["native_request_id"], identity);
        }

        #[test]
        fn original_mix_attempt_known_failure_retains_original_error_without_grant_or_retry() {
            let (f, source) = source_fixture();
            append_rows(&f, &source.rows);
            let identity = observe(&f, &source)["native_request_id"]
                .as_str()
                .unwrap()
                .to_owned();
            let pair = failed_pair(&f, &source, &identity, false);
            let ids = append_rows(&f, &pair);
            append_unrelated_rows(&f, 300);
            let observed = observe(&f, &source);
            assert_eq!(observed["state"], "known_failed");
            assert_eq!(observed["reason"], "original_known_failure");
            assert_eq!(observed["outcome_known"], true);
            assert_eq!(observed["original_terminal_receipt_id"], json!(ids[1]));
            let original = observed["original_receipts"]
                .as_array()
                .unwrap()
                .iter()
                .find(|row| row["id"] == json!(ids[1]))
                .unwrap();
            assert_eq!(original["payload"], pair[1].payload);
        }

        #[test]
        fn original_mix_attempt_malformed_known_failure_or_foreign_original_pair_stays_uncertain() {
            for variant in [
                "unknown_flag",
                "missing_flag",
                "revision",
                "command",
                "digest",
                "connection",
                "mutation",
                "missing_dispatch",
            ] {
                let (f, source) = source_fixture();
                append_rows(&f, &source.rows);
                let identity = observe(&f, &source)["native_request_id"]
                    .as_str()
                    .unwrap()
                    .to_owned();
                let mut pair = failed_pair(&f, &source, &identity, false);
                match variant {
                    "unknown_flag" => pair[1].payload["error"]["outcome_known"] = json!(false),
                    "missing_flag" => {
                        pair[1].payload["error"]
                            .as_object_mut()
                            .unwrap()
                            .remove("outcome_known");
                    }
                    "revision" => {
                        for row in &mut pair {
                            row.revision -= 1;
                        }
                    }
                    "command" => {
                        for row in &mut pair {
                            row.command = "driver.mlt-video.av.mux".into();
                        }
                    }
                    "digest" => {
                        for row in &mut pair {
                            row.request_sha256 = "a".repeat(64);
                        }
                    }
                    "connection" => pair[0].payload["connection"] = json!("f".repeat(64)),
                    "mutation" => pair[1].payload["mutation"] = json!(false),
                    "missing_dispatch" => {}
                    _ => unreachable!(),
                }
                append_rows(
                    &f,
                    if variant == "missing_dispatch" {
                        &pair[1..]
                    } else {
                        &pair
                    },
                );
                let observed = observe(&f, &source);
                assert_eq!(observed["state"], "uncertain", "{variant}");
                assert_eq!(observed["outcome_known"], false, "{variant}");
            }
        }

        #[test]
        fn original_mix_attempt_different_label_cannot_hide_existing_source_effects() {
            for unknown in [false, true] {
                let (f, source) = source_fixture();
                append_rows(&f, &source.rows);
                let canonical = observe(&f, &source);
                let pair = if unknown {
                    failed_pair(&f, &source, "older-owner-render", true)
                } else {
                    ffv1_pair(&f, &source, "older-owner-render", 0)
                };
                let ids = append_rows(&f, &pair);
                append_unrelated_rows(&f, 300);
                let observed = observe(&f, &source);
                assert_eq!(observed["state"], "uncertain");
                assert_eq!(
                    observed["reason"],
                    if unknown {
                        "original_unknown"
                    } else {
                        "alternate_original_identity"
                    }
                );
                assert_eq!(
                    observed["native_request_id"],
                    canonical["native_request_id"]
                );
                assert!(
                    observed["selected_receipt_ids"]
                        .as_array()
                        .unwrap()
                        .contains(&json!(ids[1]))
                );
            }
        }

        fn completed_mlt_rows(
            f: &Fixture,
            source: &SyntheticMotionSource,
            identity: &str,
        ) -> Vec<ProductionReceiptInput> {
            let recipe = recipe(f, source);
            let mut rows = Vec::new();
            for index in 0..recipe.clips.len() {
                rows.extend(ffv1_pair(f, source, identity, index));
            }
            let visual = format!("{identity}-visual");
            let mut push = |suffix: &str, command: &str, mutation, args, data| {
                rows.extend(mlt_pair(
                    f,
                    &format!("{visual}:mlt:{suffix}"),
                    command,
                    mutation,
                    args,
                    data,
                ));
            };
            push(
                "project-create",
                "driver.mlt-video.project.create",
                true,
                json!({"profile": &recipe.provider_profile}),
                json!({"project": "synthetic-project", "revision": "fixture-r0"}),
            );
            push(
                "sequence-create",
                "driver.mlt-video.sequence.create",
                true,
                json!({"project": "synthetic-project", "expected_revision": "fixture-r0", "name": &recipe.sequence_name}),
                json!({"created": {"sequence": "synthetic-sequence"}}),
            );
            push(
                "track-create",
                "driver.mlt-video.track.create",
                true,
                json!({"project": "synthetic-project", "expected_revision": "fixture-r1", "sequence": "synthetic-sequence", "name": &recipe.video_track_name, "kind": "video"}),
                json!({"created": {"track": "synthetic-track"}}),
            );
            for index in 0..recipe.clips.len() {
                let clip = &recipe.clips[index];
                push(
                    &format!("asset:{index:02}"),
                    "driver.mlt-video.asset.import",
                    true,
                    json!({"project": "synthetic-project", "expected_revision": format!("fixture-import-{index}"), "kind": "file", "name": &clip.edit_asset_name, "root": "output", "path": &clip.ffv1_output_path}),
                    json!({"created": {"asset": format!("synthetic-asset-{index}")}}),
                );
                push(
                    &format!("sequence-ref:{index:02}"),
                    "driver.mlt-video.sequence.list",
                    false,
                    json!({"project": "synthetic-project", "limit": 100}),
                    json!({"items": [{"name": &recipe.sequence_name, "reference": "synthetic-sequence"}], "total": 1, "cursor": null}),
                );
                push(
                    &format!("track-ref:{index:02}"),
                    "driver.mlt-video.track.list",
                    false,
                    json!({"project": "synthetic-project", "limit": 100, "sequence": "synthetic-sequence"}),
                    json!({"items": [{"name": &recipe.video_track_name, "reference": "synthetic-track"}], "total": 1, "cursor": null}),
                );
                push(
                    &format!("clip:{index:02}"),
                    "driver.mlt-video.clip.insert",
                    true,
                    json!({"project": "synthetic-project", "expected_revision": format!("fixture-clip-{index}"), "sequence": "synthetic-sequence", "track": "synthetic-track", "asset": format!("synthetic-asset-{index}"), "start": clip.start_frame, "source_in": clip.source_in, "source_out": clip.source_out_exclusive, "name": &clip.edit_clip_name, "ripple": false}),
                    json!({"created": {"clip": format!("synthetic-clip-{index}")}}),
                );
            }
            push(
                "final-sequence-ref",
                "driver.mlt-video.sequence.list",
                false,
                json!({"project": "synthetic-project", "limit": 100}),
                json!({"items": [{"name": &recipe.sequence_name, "reference": "synthetic-sequence"}], "total": 1, "cursor": null}),
            );
            push(
                "duration",
                "driver.mlt-video.sequence.duration",
                false,
                json!({"project": "synthetic-project", "sequence": "synthetic-sequence"}),
                json!({"frames": 60, "fps_num": 30, "fps_den": 1}),
            );
            let video_path = format!(
                "{}-sequence-video-only-ffv1.mkv",
                recipe.owner_output_namespace
            );
            let video_sha = sha256(b"synthetic-original-video-result-no-physical-video");
            let render_args = json!({"project": "synthetic-project", "sequence": "synthetic-sequence", "profile": "lossless-video-only", "output": &video_path});
            push(
                "render-plan",
                "driver.mlt-video.render.plan",
                false,
                render_args.clone(),
                json!({"runnable": true, "frames": 60}),
            );
            let mut start_args = render_args;
            start_args["expected_revision"] = json!("fixture-final-revision");
            push(
                "render-start",
                "driver.mlt-video.render.start",
                true,
                start_args,
                json!({"job": "synthetic-original-MLT-job", "state": "queued"}),
            );
            push(
                "status:1",
                "driver.mlt-video.render.status",
                false,
                json!({"job": "synthetic-original-MLT-job"}),
                json!({"job": "synthetic-original-MLT-job", "state": "succeeded"}),
            );
            push(
                "render-result",
                "driver.mlt-video.render.result",
                false,
                json!({"job": "synthetic-original-MLT-job"}),
                json!({
                    "job": "synthetic-original-MLT-job", "state": "succeeded", "profile": "lossless-video-only",
                    "artifact": {"root": "output", "path": &video_path, "sha256": &video_sha, "bytes": 128},
                }),
            );
            let plan = canonical_plan(f).unwrap();
            let mut wav = Vec::new();
            let mixed = write_mix_wav(&plan, &f.project, &f.pcm[0], &f.pcm[1], &f.pcm[2], &mut wav)
                .unwrap();
            let wav_sha = sha256(&wav);
            let audio_path = format!(
                "mw-original-mix-{}-{}-r{}-p{}-{}-{}.wav",
                f.project.id.simple(),
                f.project.generation.simple(),
                f.project.revision,
                f.project.deliverables[0].id.simple(),
                &mixed.source_plan_sha256[..16],
                &wav_sha[..16]
            );
            let token = sha256(identity.as_bytes());
            let master_path = format!(
                "mw-{}-r{}-{}-multisegment-master.mp4",
                f.project.id.simple(),
                f.project.revision,
                &token[..16]
            );
            rows.extend(mlt_pair(f, &format!("{identity}:mlt:multisegment-av-mux"),
                "driver.mlt-video.av.mux", true, json!({
                    "video_root": "output", "video_path": &video_path, "video_sha256": &video_sha,
                    "audio_root": "output", "audio_path": audio_path, "audio_sha256": &wav_sha,
                    "width": recipe.provider_profile.width, "height": recipe.provider_profile.height,
                    "fps_num": 30, "fps_den": 1, "frame_count": 60, "sample_rate": 48_000, "channels": 2,
                    "profile": "h264-aac-mp4", "output_path": &master_path, "max_bytes": 1024 * 1024 * 1024,
                }), json!({
                    "profile": "h264-aac-mp4", "video_sha256": &video_sha, "audio_sha256": &wav_sha,
                    "artifact": {"root": "output", "path": master_path, "sha256": sha256(b"synthetic-original-mux-result-no-physical-MP4"), "bytes": 128},
                    "scope": "synthetic-original-disposition-not-native-media-verification",
                })));
            rows
        }

        fn completed_attempt_fixture() -> (
            Fixture,
            SyntheticMotionSource,
            String,
            Vec<ProductionReceiptInput>,
        ) {
            let (f, source) = source_fixture();
            append_rows(&f, &source.rows);
            let original = observe(&f, &source);
            // A source/provenance failure must not mask the MLT condition.
            assert_eq!(original["state"], "not_started");
            let identity = original["native_request_id"].as_str().unwrap().to_owned();
            let rows = completed_mlt_rows(&f, &source, &identity);
            (f, source, identity, rows)
        }

        fn observe_original_rows(
            f: &Fixture,
            source: &SyntheticMotionSource,
            rows: &[ProductionReceiptInput],
        ) -> Value {
            let ids = append_rows(f, rows);
            let observed = observe(f, source);
            let retained = observed["original_receipts"].as_array().unwrap();
            for (row, id) in rows.iter().zip(&ids) {
                let original = retained
                    .iter()
                    .find(|item| item["id"] == json!(id))
                    .unwrap();
                assert_eq!(original["request_id"], row.request_id);
                assert_eq!(original["request_sha256"], row.request_sha256);
                assert_eq!(original["payload"], row.payload);
            }
            observed
        }

        fn rewrite_mlt_execution(
            rows: &mut [ProductionReceiptInput],
            mut rewrite: impl FnMut(&mut InvocationProvenance),
        ) {
            // Alter every selected MLT terminal coherently, then persist the
            // exact SDK-typed payloads as original Store rows. This is not a
            // caller-side report that disagrees with another retained copy.
            for row in rows.iter_mut().filter(|row| row.stage == "completed") {
                assert!(row.command.starts_with("driver.mlt-video."));
                let mut execution: Execution =
                    serde_json::from_value(row.payload["result"]["execution"].clone()).unwrap();
                rewrite(execution.provenance.as_mut().unwrap());
                row.payload["result"]["execution"] = serde_json::to_value(execution).unwrap();
            }
        }

        fn replace_original_polls(
            f: &Fixture,
            identity: &str,
            rows: &mut Vec<ProductionReceiptInput>,
            states: &[&str],
        ) {
            let job = rows
                .iter()
                .find(|row| {
                    row.command == "driver.mlt-video.render.start" && row.stage == "completed"
                })
                .unwrap()
                .payload["result"]["data"]["job"]
                .as_str()
                .unwrap()
                .to_owned();
            rows.retain(|row| row.command != "driver.mlt-video.render.status");
            let result_index = rows
                .iter()
                .position(|row| {
                    row.command == "driver.mlt-video.render.result" && row.stage == "dispatching"
                })
                .unwrap();
            let mut polls = Vec::new();
            for (index, state) in states.iter().enumerate() {
                polls.extend(mlt_pair(
                    f,
                    &format!("{identity}-visual:mlt:status:{}", index + 1),
                    "driver.mlt-video.render.status",
                    false,
                    json!({"job": &job}),
                    json!({"job": &job, "state": state}),
                ));
            }
            drop(rows.splice(result_index..result_index, polls));
        }

        #[test]
        fn original_mix_attempt_sdk_driver_provenance_completes_original_chain() {
            let (f, source, _, rows) = completed_attempt_fixture();
            for row in rows.iter().filter(|row| row.stage == "completed") {
                let execution: Execution =
                    serde_json::from_value(row.payload["result"]["execution"].clone()).unwrap();
                let provenance = execution.provenance.unwrap();
                assert!(!execution.dry_run);
                assert_eq!(provenance.source, SourceKind::Driver);
                assert!(provenance.untrusted_metadata);
                assert_eq!(provenance.provider, "driver:mlt-video");
                assert_eq!(
                    provenance.execution_provider.as_deref(),
                    Some("driver:mlt-video")
                );
            }
            let observed = observe_original_rows(&f, &source, &rows);
            assert_eq!(observed["state"], "completed");
            assert_eq!(observed["reason"], "original_mux_completed");
            assert_eq!(observed["outcome_known"], true);
            assert!(observed.get("decoded_pcm_pass").is_none());
        }

        #[test]
        fn original_mix_attempt_coherent_driver_metadata_false_is_not_authority() {
            let (f, source, _, mut rows) = completed_attempt_fixture();
            rewrite_mlt_execution(&mut rows, |provenance| {
                provenance.untrusted_metadata = false
            });
            let observed = observe_original_rows(&f, &source, &rows);
            assert_eq!(observed["state"], "uncertain");
            assert_eq!(observed["reason"], "foreign_original_stage");
            assert_eq!(observed["outcome_known"], false);
        }

        #[test]
        fn original_mix_attempt_coherent_missing_execution_provider_is_not_authority() {
            for explicit_null in [false, true] {
                let (f, source, _, mut rows) = completed_attempt_fixture();
                rewrite_mlt_execution(&mut rows, |provenance| provenance.execution_provider = None);
                for row in rows.iter_mut().filter(|row| row.stage == "completed") {
                    let provenance = row.payload["result"]["execution"]["provenance"]
                        .as_object_mut()
                        .unwrap();
                    if explicit_null {
                        provenance.insert("execution_provider".into(), Value::Null);
                    } else {
                        provenance.remove("execution_provider");
                    }
                }
                let observed = observe_original_rows(&f, &source, &rows);
                assert_eq!(
                    observed["state"], "uncertain",
                    "explicit_null={explicit_null}"
                );
                assert_eq!(observed["reason"], "foreign_original_stage");
                assert_eq!(observed["outcome_known"], false);
            }
        }

        #[test]
        fn original_mix_attempt_coherent_foreign_execution_provider_is_not_authority() {
            let (f, source, _, mut rows) = completed_attempt_fixture();
            rewrite_mlt_execution(&mut rows, |provenance| {
                provenance.execution_provider = Some("driver:blender".into());
            });
            let observed = observe_original_rows(&f, &source, &rows);
            assert_eq!(observed["state"], "uncertain");
            assert_eq!(observed["reason"], "foreign_original_stage");
            assert_eq!(observed["outcome_known"], false);
        }

        #[test]
        fn original_mix_attempt_queued_start_without_any_poll_stays_uncertain() {
            let (f, source, identity, mut rows) = completed_attempt_fixture();
            replace_original_polls(&f, &identity, &mut rows, &[]);
            let observed = observe_original_rows(&f, &source, &rows);
            assert_eq!(observed["state"], "uncertain");
            assert_eq!(observed["reason"], "incomplete_original_stage");
            assert_eq!(observed["outcome_known"], false);
        }

        #[test]
        fn original_mix_attempt_last_pending_poll_cannot_be_closed_by_result() {
            for state in ["queued", "starting", "running"] {
                let (f, source, identity, mut rows) = completed_attempt_fixture();
                replace_original_polls(&f, &identity, &mut rows, &[state]);
                let observed = observe_original_rows(&f, &source, &rows);
                assert_eq!(observed["state"], "uncertain", "{state}");
                assert_eq!(observed["reason"], "incomplete_original_stage", "{state}");
                assert_eq!(observed["outcome_known"], false, "{state}");
            }
        }

        #[test]
        fn original_mix_attempt_succeeded_start_without_polls_completes() {
            let (f, source, identity, mut rows) = completed_attempt_fixture();
            replace_original_polls(&f, &identity, &mut rows, &[]);
            let start = rows
                .iter_mut()
                .find(|row| {
                    row.command == "driver.mlt-video.render.start" && row.stage == "completed"
                })
                .unwrap();
            start.payload["result"]["data"]["state"] = json!("succeeded");
            let observed = observe_original_rows(&f, &source, &rows);
            assert_eq!(observed["state"], "completed");
            assert_eq!(observed["reason"], "original_mux_completed");
            assert_eq!(observed["outcome_known"], true);
        }

        #[test]
        fn original_mix_attempt_contiguous_pending_polls_then_success_completes() {
            let (f, source, identity, mut rows) = completed_attempt_fixture();
            replace_original_polls(
                &f,
                &identity,
                &mut rows,
                &["queued", "starting", "running", "succeeded"],
            );
            let observed = observe_original_rows(&f, &source, &rows);
            assert_eq!(observed["state"], "completed");
            assert_eq!(observed["reason"], "original_mux_completed");
            assert_eq!(observed["outcome_known"], true);
        }

        #[test]
        fn original_mix_attempt_poll_after_original_success_is_ambiguous() {
            for success_at_start in [false, true] {
                let (f, source, identity, mut rows) = completed_attempt_fixture();
                if success_at_start {
                    let start = rows
                        .iter_mut()
                        .find(|row| {
                            row.command == "driver.mlt-video.render.start"
                                && row.stage == "completed"
                        })
                        .unwrap();
                    start.payload["result"]["data"]["state"] = json!("succeeded");
                } else {
                    replace_original_polls(&f, &identity, &mut rows, &["succeeded", "queued"]);
                }
                let observed = observe_original_rows(&f, &source, &rows);
                assert_eq!(
                    observed["state"], "uncertain",
                    "success_at_start={success_at_start}"
                );
                assert_eq!(observed["reason"], "ambiguous_original_stage");
                assert_eq!(observed["outcome_known"], false);
            }
        }

        #[test]
        fn original_mix_attempt_completed_preserves_original_mux_result_without_physical_verdict() {
            let (f, source) = source_fixture();
            append_rows(&f, &source.rows);
            let identity = observe(&f, &source)["native_request_id"]
                .as_str()
                .unwrap()
                .to_owned();
            let rows = completed_mlt_rows(&f, &source, &identity);
            let ids = append_rows(&f, &rows);
            append_unrelated_rows(&f, 300);
            let observed = observe(&f, &source);
            assert_eq!(observed["state"], "completed");
            assert_eq!(observed["reason"], "original_mux_completed");
            assert_eq!(
                observed["original_terminal_receipt_id"],
                json!(ids.last().unwrap())
            );
            let original = observed["original_receipts"]
                .as_array()
                .unwrap()
                .iter()
                .find(|row| row["id"] == json!(ids.last().unwrap()))
                .unwrap();
            assert_eq!(original["payload"], rows.last().unwrap().payload);
            assert!(
                !f.owner_root
                    .join(
                        original["payload"]["result"]["data"]["artifact"]["path"]
                            .as_str()
                            .unwrap()
                    )
                    .exists()
            );
            assert!(observed.get("decoded_pcm_pass").is_none());
        }

        #[test]
        fn original_mix_attempt_completed_cannot_hide_missing_stage_foreign_provider_or_mux_source()
        {
            for variant in [
                "missing_edit",
                "missing_poll",
                "stage_order",
                "provider_generation",
                "dry_run",
                "omitted_false",
                "foreign_job",
                "mux_audio",
                "mux_digest",
            ] {
                let (f, source) = source_fixture();
                append_rows(&f, &source.rows);
                let identity = observe(&f, &source)["native_request_id"]
                    .as_str()
                    .unwrap()
                    .to_owned();
                let mut rows = completed_mlt_rows(&f, &source, &identity);
                match variant {
                    "missing_edit" => rows.retain(|row| !row.request_id.ends_with(":track-create")),
                    "missing_poll" => {
                        for row in &mut rows {
                            if row.request_id.ends_with(":status:1") {
                                row.request_id = row.request_id.replace(":status:1", ":status:2");
                            }
                        }
                    }
                    "stage_order" => {
                        rows.swap(0, 2);
                        rows.swap(1, 3);
                    }
                    "provider_generation" => {
                        rows[3].payload["result"]["execution"]["provenance"]["provider_generation"] =
                            json!(58)
                    }
                    "dry_run" => rows[1].payload["result"]["execution"]["dry_run"] = json!(true),
                    "omitted_false" => {
                        rows[1].payload["result"]["execution"]
                            .as_object_mut()
                            .unwrap()
                            .remove("dry_run");
                    }
                    "foreign_job" => {
                        for row in &mut rows {
                            if row.stage == "completed"
                                && row.command == "driver.mlt-video.render.result"
                            {
                                row.payload["result"]["data"]["job"] =
                                    json!("foreign-original-job");
                            }
                        }
                    }
                    "mux_audio" => {
                        rows.last_mut().unwrap().payload["result"]["data"]["audio_sha256"] =
                            json!("b".repeat(64))
                    }
                    "mux_digest" => {
                        for row in rows.iter_mut().rev().take(2) {
                            row.request_sha256 = "a".repeat(64);
                        }
                    }
                    _ => unreachable!(),
                }
                append_rows(&f, &rows);
                let observed = observe(&f, &source);
                assert_eq!(observed["state"], "uncertain", "{variant}");
                assert_eq!(observed["outcome_known"], false, "{variant}");
            }
        }
    }
}
