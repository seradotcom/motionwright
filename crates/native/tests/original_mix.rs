use motionwright_domain as domain;
use motionwright_native::original_mix::{
    OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow, original_mix_plan_from_canonical_recipe,
    render_original_source_mix, write_mix_wav,
};
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn id(value: u128) -> Uuid {
    Uuid::from_u128(value)
}
fn hex(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn time(num: i64, den: i64) -> domain::RationalTime {
    domain::RationalTime { num, den }
}
fn pcm(sample: i16, frames: usize) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(frames * 4);
    for _ in 0..frames {
        bytes.extend_from_slice(&sample.to_le_bytes());
        bytes.extend_from_slice(&sample.to_le_bytes());
    }
    bytes
}
fn fixture() -> (domain::Project, OriginalMixPlan, [Vec<u8>; 3]) {
    let mut p = domain::Project::new("Original unapproved owner mixing study").unwrap();
    p.id = id(1);
    p.generation = id(2);
    p.revision = 9;
    let n = 96_000_usize;
    let sources = [pcm(0, n), pcm(10_000, n), pcm(0, n)];
    let source_sha = sources.each_ref().map(|v| hex(v));
    for (ix, label) in [
        "Original voice surrogate",
        "Original owned music",
        "Original focus SFX",
    ]
    .iter()
    .enumerate()
    {
        p.assets.push(domain::Asset {
            id: id(20 + ix as u128),
            name: (*label).into(),
            media_type: "audio/x-raw".into(),
            content_sha256: Some(source_sha[ix].clone()),
            source_revision: Some("original-native-source/1".into()),
        });
    }
    p.audio.voice_tracks.push(domain::VoiceTrack {
        id: id(40),
        asset_id: id(20),
        label: "Owner imported 48-kHz stereo voice source".into(),
        sample_rate_hz: 48000,
        channels: 2,
        measured_duration: time(2, 1),
        source_sha256: source_sha[0].clone(),
        loudness_lufs: None,
        true_peak_dbfs: None,
    });
    p.audio.active_voice_track_id = Some(id(40));
    p.audio.transcript.push(domain::TranscriptSegment {
        id: id(50),
        voice_track_id: id(40),
        start: time(1, 2),
        end: time(1, 1),
        text: "Owner original timing interval, not a machine-guessed transcript".into(),
        speaker: None,
        alignment: domain::AlignmentEvidence::Manual,
    });
    p.deliverables[0].language = "en".into();
    p.deliverables[0].audio_sample_rate_hz = 48000;
    p.deliverables[0].voice_track_id = Some(id(40));
    let stem = |idx: usize| PcmStemReceipt {
        asset_id: id(20 + idx as u128),
        source_asset_sha256: source_sha[idx].clone(),
        decoded_pcm_sha256: source_sha[idx].clone(),
        sample_frames: n as u64,
    };
    let plan = OriginalMixPlan {
        schema: "motionwright.original-audio-mix/1".into(),
        project_id: p.id,
        generation: p.generation,
        project_revision: p.revision,
        deliverable_profile_id: p.deliverables[0].id,
        language: "en".into(),
        sample_rate: 48000,
        channels: 2,
        sample_frames: n as u64,
        measured_voice: stem(0),
        original_music: stem(1),
        original_sfx: stem(2),
        voice_gain_db: 0.0,
        music_gain_db: -12.0,
        sfx_gain_db: -8.0,
        duck_attenuation_db: 14.0,
        duck_attack_samples: 2400,
        duck_release_samples: 4800,
        voice_windows: vec![VoiceActivityWindow {
            start_sample: 24000,
            end_exclusive: 48000,
        }],
        sample_peak_guard_dbfs: -0.5,
        source_classification: "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED".into(),
        owner_mixing_approval_authenticated: false,
        is_mastered: false,
    };
    (p, plan, sources)
}
fn left_at(data: &[u8], sample: usize) -> i16 {
    i16::from_le_bytes([data[4 * sample], data[4 * sample + 1]])
}

fn save_test_recipe(project: &mut domain::Project, plan: &OriginalMixPlan) {
    project.production_design.delivery_recipes.insert(
        plan.deliverable_profile_id,
        domain::DeliveryRecipe {
            schema: domain::DeliveryRecipeSchema::V1,
            scene_intents: vec![],
            original_mix: Some(domain::OriginalMixRecipe {
                music_asset_id: Some(plan.original_music.asset_id),
                sfx_asset_id: Some(plan.original_sfx.asset_id),
                sfx_gain_db: plan.sfx_gain_db,
                duck_attenuation_db: plan.duck_attenuation_db,
                duck_attack_samples: plan.duck_attack_samples,
                duck_release_samples: plan.duck_release_samples,
            }),
        },
    );
}

fn from_saved_recipe(project: &domain::Project, source: &OriginalMixPlan) -> OriginalMixPlan {
    original_mix_plan_from_canonical_recipe(
        project,
        source.deliverable_profile_id,
        source.measured_voice.clone(),
        source.original_music.clone(),
        source.original_sfx.clone(),
    )
    .unwrap()
}

#[test]
fn canonical_mix_plan_consumes_saved_source_ids_settings_and_global_gains() {
    let (mut project, mut expected, pcm) = fixture();
    expected.sample_peak_guard_dbfs = -1.0;
    save_test_recipe(&mut project, &expected);
    let source = serde_json::to_vec(&project).unwrap();
    let initial = from_saved_recipe(&project, &expected);
    assert_eq!(initial, expected);
    assert_eq!(serde_json::to_vec(&project).unwrap(), source);
    let (first_pcm, _) =
        render_original_source_mix(&initial, &project, &pcm[0], &pcm[1], &pcm[2]).unwrap();

    project.audio.mix.music_gain_db = -18.0;
    project.audio.mix.voice_gain_db = -3.0;
    let mix = project
        .production_design
        .delivery_recipes
        .get_mut(&expected.deliverable_profile_id)
        .unwrap()
        .original_mix
        .as_mut()
        .unwrap();
    mix.sfx_gain_db = -5.0;
    mix.duck_attenuation_db = 6.0;
    mix.duck_attack_samples = 1_200;
    mix.duck_release_samples = 2_400;
    project.revision += 1;
    let fresh = from_saved_recipe(&project, &expected);
    assert_eq!(fresh.voice_gain_db, -3.0);
    assert_eq!(fresh.music_gain_db, -18.0);
    assert_eq!(fresh.sfx_gain_db, -5.0);
    assert_eq!(fresh.duck_attenuation_db, 6.0);
    assert_eq!(fresh.duck_attack_samples, 1_200);
    assert_eq!(fresh.duck_release_samples, 2_400);
    assert_eq!(fresh.project_revision, project.revision);
    assert!(initial.validate(&project).is_err());
    let (next_pcm, _) =
        render_original_source_mix(&fresh, &project, &pcm[0], &pcm[1], &pcm[2]).unwrap();
    assert_ne!(first_pcm, next_pcm);
    let expected_gain = 10.0_f64.powf(-6.0 / 20.0);
    let observed_gain = f64::from(left_at(&next_pcm, 1000)) / f64::from(left_at(&first_pcm, 1000));
    assert!((observed_gain - expected_gain).abs() < 0.001);
}

#[test]
fn canonical_mix_plan_rejects_missing_recipe_bus_substitution_and_unaligned_voice() {
    let (mut project, source, _) = fixture();
    let try_plan = |p: &domain::Project,
                    voice: PcmStemReceipt,
                    music: PcmStemReceipt,
                    sfx: PcmStemReceipt| {
        original_mix_plan_from_canonical_recipe(p, source.deliverable_profile_id, voice, music, sfx)
    };
    assert!(
        try_plan(
            &project,
            source.measured_voice.clone(),
            source.original_music.clone(),
            source.original_sfx.clone()
        )
        .is_err()
    );
    save_test_recipe(&mut project, &source);
    assert!(
        try_plan(
            &project,
            source.measured_voice.clone(),
            source.original_sfx.clone(),
            source.original_music.clone()
        )
        .is_err()
    );
    let mut absent_bus = project.clone();
    absent_bus
        .production_design
        .delivery_recipes
        .get_mut(&source.deliverable_profile_id)
        .unwrap()
        .original_mix
        .as_mut()
        .unwrap()
        .sfx_asset_id = None;
    assert!(
        try_plan(
            &absent_bus,
            source.measured_voice.clone(),
            source.original_music.clone(),
            source.original_sfx.clone()
        )
        .is_err()
    );
    let mut no_selected_voice = project.clone();
    no_selected_voice.deliverables[0].voice_track_id = None;
    assert!(
        try_plan(
            &no_selected_voice,
            source.measured_voice.clone(),
            source.original_music.clone(),
            source.original_sfx.clone()
        )
        .is_err()
    );
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Unknown;
    assert!(
        try_plan(
            &project,
            source.measured_voice.clone(),
            source.original_music.clone(),
            source.original_sfx.clone()
        )
        .is_err()
    );
}

#[test]
fn canonical_source_plan_preserves_unrealized_mastering_intent_without_approving_it() {
    let (mut project, source, _) = fixture();
    save_test_recipe(&mut project, &source);
    project.audio.mix.target_lufs = Some(-16.0);
    project.audio.mix.target_true_peak_dbfs = Some(-1.0);
    let before = serde_json::to_vec(&project).unwrap();
    let plan = from_saved_recipe(&project, &source);
    assert!(!plan.is_mastered);
    assert!(!plan.owner_mixing_approval_authenticated);
    assert_eq!(plan.sample_peak_guard_dbfs, -1.0);
    assert_eq!(serde_json::to_vec(&project).unwrap(), before);
}

#[test]
fn true_native_music_ducking_is_sample_accurate_and_does_not_change_voice_source() {
    let (project, plan, [voice, music, sfx]) = fixture();
    let (mixed, receipt) =
        render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    assert_eq!(receipt.frames, 96_000);
    assert_eq!(receipt.measured_music_duck_windows, 1);
    assert_eq!(receipt.rendered_pcm_sha256, hex(&mixed));
    assert_eq!(
        receipt.input_source_checks,
        "ALL_THREE_FULL_PCM_SHA256_VERIFIED"
    );
    assert_eq!(receipt.rendered_media, "RAW_PCM_S16LE_ONLY");
    assert!(receipt.minimum_music_duck_gain < 0.22 && receipt.minimum_music_duck_gain > 0.19);
    let initial = left_at(&mixed, 1000).abs();
    let inside = left_at(&mixed, 36000).abs();
    let after = left_at(&mixed, 70000).abs();
    assert!(
        initial > inside * 4,
        "Music should be audibly attenuated during actual reviewed voice interval"
    );
    assert!(
        (initial - after).abs() <= 1,
        "Music should return to source gain after explicit release"
    );
    assert_eq!(voice.len(), 96_000 * 4);
    assert!(!receipt.independently_verified_lufs);
    assert!(!receipt.independently_verified_true_peak);
    assert!(!receipt.listening_intelligibility_approved);
    assert!(!receipt.owner_release_approved);
}

#[test]
fn short_voice_releases_from_the_reached_gain_without_a_pcm_discontinuity() {
    for (duration, attack, release) in [
        (1_u64, 4_800_u32, 4_800_u32),
        (1_200, 4_800, 4_800),
        (1_200, 4_800, 600),
        (4_800, 4_800, 4_800),
        (9_600, 4_800, 4_800),
    ] {
        let (mut project, mut plan, [voice, music, sfx]) = fixture();
        let start = plan.voice_windows[0].start_sample;
        let end = start + duration;
        project.audio.transcript[0].end = domain::RationalTime::new(end as i64, 48_000).unwrap();
        plan.voice_windows[0].end_exclusive = end;
        plan.duck_attack_samples = attack;
        plan.duck_release_samples = release;

        let (mixed, receipt) =
            render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
        let before = left_at(&mixed, start as usize - 1);
        assert_eq!(left_at(&mixed, start as usize), before);
        if duration == 1_200 {
            // Fixed PCM anchors for 10,000-source units, -12 dB music,
            // and a 14 dB duck over a quarter of the 100 ms attack.
            // Shape alone would also accept an entirely disabled duck.
            for (frame, expected) in [
                (start, 2_512_i32),
                (end, 2_009),
                (end + u64::from(release) / 2, 2_260),
                (end + u64::from(release), 2_512),
            ] {
                let actual = i32::from(left_at(&mixed, frame as usize));
                assert!(
                    (actual - expected).abs() <= 1,
                    "Short-voice attenuation at {frame}: {actual}, expected {expected}"
                );
            }
        }
        // These constant-source profiles slew by at most one PCM16 unit
        // per sample. A switch of envelope phase must not create a click.
        for frame in start..end {
            let current = i32::from(left_at(&mixed, frame as usize));
            let next = i32::from(left_at(&mixed, frame as usize + 1));
            assert!(
                (0..=1).contains(&(current - next)),
                "Attack discontinuity at {frame}: {current} -> {next}, duration {duration}"
            );
        }
        let release_end = end + u64::from(release);
        for frame in end..release_end {
            let current = i32::from(left_at(&mixed, frame as usize));
            let next = i32::from(left_at(&mixed, frame as usize + 1));
            assert!(
                (0..=1).contains(&(next - current)),
                "Music must recover after voice ends: {frame}: {current} -> {next}, duration {duration}, release {release}"
            );
        }
        // Also cover the former attack boundary when release has already
        // finished. It must not re-attenuate or cause a delayed jump.
        let check_end = release_end.max(start + u64::from(attack)) + 1;
        for frame in release_end..=check_end {
            assert_eq!(left_at(&mixed, frame as usize), before);
        }
        assert_eq!(receipt.frames, plan.sample_frames);
        assert_eq!(receipt.rendered_pcm_sha256, hex(&mixed));
        assert_eq!(receipt.measured_music_duck_windows, 1);
        assert!(!receipt.listening_intelligibility_approved);
    }
}

#[test]
fn short_voice_envelopes_do_not_attenuate_voice_or_sfx_buses() {
    let (mut project, mut plan, _) = fixture();
    let sources = [pcm(2_000, 96_000), pcm(0, 96_000), pcm(-1_000, 96_000)];
    for (index, stem) in [
        &mut plan.measured_voice,
        &mut plan.original_music,
        &mut plan.original_sfx,
    ]
    .into_iter()
    .enumerate()
    {
        let digest = hex(&sources[index]);
        stem.source_asset_sha256 = digest.clone();
        stem.decoded_pcm_sha256 = digest.clone();
        project.assets[index].content_sha256 = Some(digest);
    }
    project.audio.voice_tracks[0].source_sha256 = plan.measured_voice.source_asset_sha256.clone();
    let [voice, music, sfx] = sources;
    let (before, _) = render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    project.audio.transcript[0].end = time(21, 40);
    plan.voice_windows[0].end_exclusive = 25_200;
    plan.duck_attenuation_db = 30.0;
    plan.duck_attack_samples = 4_800;
    plan.duck_release_samples = 600;
    let (after, _) = render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    assert!(
        left_at(&before, 24_000) > 0,
        "Voice and SFX must contribute"
    );
    assert_eq!(after, before, "Only the music bus may be ducked");
}

#[test]
fn rendered_wav_has_exact_pcm_bytes_and_cannot_claim_an_audio_master() {
    let (project, plan, [voice, music, sfx]) = fixture();
    let (pcm, receipt) = render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    let mut wav = Vec::new();
    let written = write_mix_wav(&plan, &project, &voice, &music, &sfx, &mut wav).unwrap();
    assert_eq!(written, receipt);
    assert_eq!(&wav[0..4], b"RIFF");
    assert_eq!(&wav[8..16], b"WAVEfmt ");
    assert_eq!(&wav[36..40], b"data");
    assert_eq!(&wav[44..], pcm);
    assert_eq!(wav.len(), pcm.len() + 44);
}
#[test]
fn container_asset_sha_and_actual_decoded_pcm_sha_remain_separate_original_identities() {
    let (mut project, mut plan, [voice, music, sfx]) = fixture();
    // A real original WAV file hashes differently from the decoded s16le
    // data. Project.assets and VoiceTrack must keep the container's SHA,
    // while the mixer independently validates the decoded PCM bytes.
    let container = "ef".repeat(32);
    project.assets[0].content_sha256 = Some(container.clone());
    project.audio.voice_tracks[0].source_sha256 = container.clone();
    plan.measured_voice.source_asset_sha256 = container;
    let output = render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    assert_eq!(
        output.1.input_source_checks,
        "ALL_THREE_FULL_PCM_SHA256_VERIFIED"
    );
    plan.measured_voice.source_asset_sha256 = "dd".repeat(32);
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
}
#[test]
fn identical_inputs_repeat_without_randomness_or_unapproved_resampling() {
    let (project, plan, [voice, music, sfx]) = fixture();
    let a = render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    let b = render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    assert_eq!(a, b);
    assert_eq!(a.1.sample_rate, 48000);
}
#[test]
fn source_digest_or_project_revision_changes_fail_closed() {
    let (project, mut plan, [voice, music, sfx]) = fixture();
    plan.project_revision = 10;
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    plan.project_revision = 9;
    let mut corrupted = music.clone();
    corrupted[20] ^= 1;
    assert!(render_original_source_mix(&plan, &project, &voice, &corrupted, &sfx).is_err());
    let mut project_changed = project.clone();
    project_changed.assets[0].content_sha256 = Some("ba".repeat(32));
    assert!(render_original_source_mix(&plan, &project_changed, &voice, &music, &sfx).is_err());
}
#[test]
fn locales_require_explicit_profile_and_correct_voice_take() {
    let (mut project, mut plan, [voice, music, sfx]) = fixture();
    plan.language = "es".into();
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    project.deliverables[0].language = "es".into();
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_ok());
    project.deliverables[0].voice_track_id = Some(id(88));
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
}
#[test]
fn low_confidence_asr_never_drives_mixer_ducking_automation() {
    let (mut project, plan, [voice, music, sfx]) = fixture();
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Unknown;
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Measured {
        engine: "Non-attested preliminary ASR".into(),
        source_sha256: "ab".repeat(32),
        confidence_millis: Some(200),
    };
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
}
#[test]
fn measured_ducking_alignment_must_belong_to_the_exact_selected_voice_source() {
    let (mut project, plan, [voice, music, sfx]) = fixture();
    save_test_recipe(&mut project, &plan);
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Measured {
        engine: "Measured source alignment fixture".into(),
        source_sha256: project.audio.voice_tracks[0].source_sha256.clone(),
        confidence_millis: Some(800),
    };
    project.validate().unwrap();
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_ok());
    assert_eq!(
        from_saved_recipe(&project, &plan).voice_windows,
        plan.voice_windows
    );

    if let domain::AlignmentEvidence::Measured { source_sha256, .. } =
        &mut project.audio.transcript[0].alignment
    {
        *source_sha256 = "ab".repeat(32);
    }
    // The domain admits this well-shaped historical alignment. The native
    // consumer must reject it because it belongs to another physical take.
    project.validate().unwrap();
    let before = serde_json::to_vec(&project).unwrap();
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    assert!(
        original_mix_plan_from_canonical_recipe(
            &project,
            plan.deliverable_profile_id,
            plan.measured_voice.clone(),
            plan.original_music.clone(),
            plan.original_sfx.clone(),
        )
        .is_err()
    );
    assert_eq!(serde_json::to_vec(&project).unwrap(), before);
}

#[test]
fn no_clip_guard_or_unbounded_normalization_automatically_hides_clipping() {
    let (project, mut plan, [voice, music, sfx]) = fixture();
    plan.music_gain_db = 12.0;
    let mut project = project;
    project.audio.mix.music_gain_db = 12.0;
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    plan.is_mastered = true;
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
}
#[test]
fn overlapping_or_cross_timeline_duck_windows_are_rejected() {
    let (mut project, mut plan, [voice, music, sfx]) = fixture();
    plan.voice_windows.push(VoiceActivityWindow {
        start_sample: 42000,
        end_exclusive: 65000,
    });
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    plan.voice_windows.truncate(1);
    project.audio.transcript[0].end = time(5, 2);
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
}
#[test]
fn unknown_fields_cannot_forge_a_live_listening_or_publication_grant() {
    let (_project, plan, _input) = fixture();
    let mut value = serde_json::to_value(plan).unwrap();
    value["publish_now"] = serde_json::json!(true);
    assert!(serde_json::from_value::<OriginalMixPlan>(value).is_err());
}
