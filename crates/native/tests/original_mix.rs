use motionwright_domain as domain;
use motionwright_native::original_mix::{
    OriginalMixPlan, OriginalSfxSource, PcmStemReceipt, VoiceActivityWindow,
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
        original_sfx: stem(2).into(),
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
        voice_envelope: Default::default(),
        music_envelope: Default::default(),
        sfx_envelope: Default::default(),
    };
    (p, plan, sources)
}
fn left_at(data: &[u8], sample: usize) -> i16 {
    i16::from_le_bytes([data[4 * sample], data[4 * sample + 1]])
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
fn mute_or_zero_attenuation_cannot_claim_or_apply_music_ducking() {
    for mode in ["voice-muted", "music-muted", "zero-attenuation"] {
        let (project, mut plan, [voice, music, sfx]) = fixture();
        match mode {
            "voice-muted" => plan.voice_envelope.muted = true,
            "music-muted" => plan.music_envelope.muted = true,
            "zero-attenuation" => plan.duck_attenuation_db = 0.0,
            _ => unreachable!(),
        }
        // The reviewed source transcript stays intact even when this output
        // has no audible voice, no music, or no requested sidechain effect.
        assert_eq!(plan.voice_windows.len(), 1);
        let (mixed, evidence) =
            render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
        assert_eq!(left_at(&mixed, 1_000), left_at(&mixed, 36_000), "{mode}");
        assert_eq!(evidence.minimum_music_duck_gain, 1.0, "{mode}");
        assert_eq!(evidence.measured_music_duck_windows, 0, "{mode}");
        assert_eq!(evidence.rendered_pcm_sha256, hex(&mixed), "{mode}");
        assert!(!evidence.owner_release_approved);
    }
    let (project, plan, [voice, music, sfx]) = fixture();
    let (mixed, evidence) =
        render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    assert!(left_at(&mixed, 1_000) > left_at(&mixed, 36_000));
    assert_eq!(evidence.measured_music_duck_windows, 1);
    assert!(evidence.minimum_music_duck_gain < 1.0);
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
    let OriginalSfxSource::Imported(sfx_receipt) = &mut plan.original_sfx else {
        panic!("This PCM-bus fixture must retain its imported SFX source");
    };
    for (index, stem) in [
        &mut plan.measured_voice,
        &mut plan.original_music,
        sfx_receipt,
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
fn bounded_duck_scan_matches_full_window_reference_with_overlapping_releases() {
    // The 64 source-bound voice intervals end before their 4,800-sample
    // releases. Several releases overlap while a new attack begins.
    // Compare every emitted stereo frame with the original full-scan
    // expression; this is source-only PCM, never mastering/G2 authority.
    let (mut project, mut plan, [voice, music, sfx]) = fixture();
    plan.duck_attack_samples = 2_400;
    plan.duck_release_samples = 4_800;
    plan.voice_windows = (0_u64..64)
        .map(|i| VoiceActivityWindow {
            start_sample: 3_000 + i * 1_000,
            end_exclusive: 3_500 + i * 1_000,
        })
        .collect();
    project.audio.transcript = plan
        .voice_windows
        .iter()
        .enumerate()
        .map(|(i, window)| domain::TranscriptSegment {
            id: id(1_000 + i as u128),
            voice_track_id: id(40),
            start: domain::RationalTime::new(window.start_sample as i64, 48_000).unwrap(),
            end: domain::RationalTime::new(window.end_exclusive as i64, 48_000).unwrap(),
            text: "Reviewed original voice activity".into(),
            speaker: None,
            alignment: domain::AlignmentEvidence::Manual,
        })
        .collect();

    let (rendered, evidence) =
        render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
    let low = 10.0_f64.powf(-plan.duck_attenuation_db / 20.0);
    let music_gain = 10.0_f64.powf(plan.music_gain_db / 20.0);
    let attack = u64::from(plan.duck_attack_samples);
    let release = u64::from(plan.duck_release_samples);
    let mut lowest_reference = 1.0_f64;
    for at in 0..plan.sample_frames {
        // This intentionally retains the old O(frames * windows) truth oracle,
        // independent of the optimized Native source window cursor.
        let mut reference = 1.0_f64;
        for window in &plan.voice_windows {
            let start = window.start_sample;
            let end = window.end_exclusive;
            let value = if at < start {
                1.0
            } else if at < end {
                if at - start < attack {
                    let progress = (at - start) as f64 / f64::from(plan.duck_attack_samples);
                    1.0 + (low - 1.0) * progress
                } else {
                    low
                }
            } else if at - end < release {
                let reached = if end - start < attack {
                    let progress = (end - start) as f64 / f64::from(plan.duck_attack_samples);
                    1.0 + (low - 1.0) * progress
                } else {
                    low
                };
                let progress = (at - end) as f64 / f64::from(plan.duck_release_samples);
                reached + (1.0 - reached) * progress
            } else {
                1.0
            };
            reference = reference.min(value);
        }
        lowest_reference = lowest_reference.min(reference);
        let mixed = (10_000.0_f64 / 32768.0) * music_gain * reference;
        let expected = (mixed * 32768.0).round().clamp(-32768.0, 32767.0) as i16;
        let frame = at as usize;
        assert_eq!(left_at(&rendered, frame), expected, "left frame {at}");
        let right = i16::from_le_bytes([rendered[4 * frame + 2], rendered[4 * frame + 3]]);
        assert_eq!(right, expected, "right frame {at}");
    }
    assert_eq!(evidence.measured_music_duck_windows, 64);
    assert_eq!(evidence.minimum_music_duck_gain, lowest_reference);
    assert_eq!(evidence.rendered_pcm_sha256, hex(&rendered));
    assert!(!evidence.independently_verified_true_peak);
    assert!(!evidence.owner_release_approved);
}

#[test]
fn unity_gain_original_pcm16_source_retains_exact_quantized_samples() {
    // The source is already valid stereo s16le and does not require a DSP
    // change. Quantization must not lose an LSB merely by passing through.
    for source_sample in [-30_000_i16, 30_000_i16] {
        let (mut project, mut plan, _) = fixture();
        let frames = plan.sample_frames as usize;
        let voice = pcm(source_sample, frames);
        let music = pcm(0, frames);
        let sfx = pcm(0, frames);

        let voice_sha = hex(&voice);
        project.assets[0].content_sha256 = Some(voice_sha.clone());
        project.audio.voice_tracks[0].source_sha256 = voice_sha.clone();
        plan.measured_voice.source_asset_sha256 = voice_sha.clone();
        plan.measured_voice.decoded_pcm_sha256 = voice_sha;

        let music_sha = hex(&music);
        project.assets[1].content_sha256 = Some(music_sha.clone());
        plan.original_music.source_asset_sha256 = music_sha.clone();
        plan.original_music.decoded_pcm_sha256 = music_sha;

        plan.duck_attenuation_db = 0.0;
        let (rendered, evidence) =
            render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
        assert_eq!(rendered, voice, "Unity PCM16 must not lose an LSB");
        assert_eq!(evidence.rendered_pcm_sha256, hex(&voice));
        assert_eq!(evidence.measured_music_duck_windows, 0);
        assert!(!evidence.independently_verified_true_peak);
        assert!(!evidence.owner_release_approved);
    }
}

#[test]
fn emitted_pcm16_peak_and_rms_receipts_follow_actual_quantized_samples() {
    // A -80 dB gain makes a one-LSB source audible in the floating-point
    // accumulator but silent after PCM16 quantization. The receipt must
    // measure the emitted bytes, not claim unrendered sub-LSB energy.
    // A -6 dB gain rounds one LSB back to one LSB; check both polarities.
    for (input, gain_db, emitted_sample) in [
        (1_i16, -80.0_f64, 0_i16),
        (-1_i16, -80.0_f64, 0_i16),
        (1_i16, -6.0_f64, 1_i16),
        (-1_i16, -6.0_f64, -1_i16),
    ] {
        let (mut project, mut plan, [voice, _, sfx]) = fixture();
        let music = pcm(input, plan.sample_frames as usize);
        let digest = hex(&music);
        project.assets[1].content_sha256 = Some(digest.clone());
        plan.original_music.source_asset_sha256 = digest.clone();
        plan.original_music.decoded_pcm_sha256 = digest;
        project.audio.mix.music_gain_db = gain_db as f32;
        plan.music_gain_db = gain_db;
        plan.duck_attenuation_db = 0.0;

        let (rendered, evidence) =
            render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
        for frame in [0_usize, 24_000, 48_000, 95_999] {
            assert_eq!(left_at(&rendered, frame), emitted_sample);
            let right = i16::from_le_bytes([rendered[4 * frame + 2], rendered[4 * frame + 3]]);
            assert_eq!(right, emitted_sample);
        }
        let expected_level = if emitted_sample == 0 {
            -120.0
        } else {
            20.0 * (f64::from(emitted_sample).abs() / 32768.0).log10()
        };
        assert!(
            (evidence.measured_pcm_sample_peak_dbfs - expected_level).abs() < 1e-6,
            "PCM16 output peak must match the emitted samples for {input} at {gain_db} dB"
        );
        assert!(
            (evidence.measured_pcm_rms_dbfs - expected_level).abs() < 1e-6,
            "PCM16 output RMS must match the emitted samples for {input} at {gain_db} dB"
        );
        assert_eq!(evidence.rendered_pcm_sha256, hex(&rendered));
        assert!(!evidence.independently_verified_true_peak);
        assert!(!evidence.owner_release_approved);
    }
}

#[test]
fn emitted_pcm16_sample_peak_guard_rejects_rounding_above_authorized_limit() {
    // This source sample is fully owned and exactly at the signed PCM16 floor.
    // A gain just below -6 dB passes the pre-quantized floating-point guard,
    // but rounds to -16423 PCM16, whose emitted amplitude exceeds -6 dBFS.
    for (gain, expected_rejection) in [(-6.0001_f32, true), (-6.01_f32, false)] {
        let (mut project, mut plan, [_, music, sfx]) = fixture();
        let voice = pcm(i16::MIN, plan.sample_frames as usize);
        let digest = hex(&voice);
        project.assets[0].content_sha256 = Some(digest.clone());
        project.audio.voice_tracks[0].source_sha256 = digest.clone();
        plan.measured_voice.source_asset_sha256 = digest.clone();
        plan.measured_voice.decoded_pcm_sha256 = digest;

        project.audio.mix.voice_gain_db = gain;
        plan.voice_gain_db = f64::from(gain);
        plan.music_envelope.muted = true;
        plan.sample_peak_guard_dbfs = -6.0;
        let result = render_original_source_mix(&plan, &project, &voice, &music, &sfx);
        if expected_rejection {
            assert!(
                result.is_err(),
                "The emitted PCM16 sample must not exceed the exact -6 dBFS guard"
            );
        } else {
            let (_, receipt) = result.unwrap();
            assert!(receipt.measured_pcm_sample_peak_dbfs < -6.0);
            assert!(!receipt.independently_verified_true_peak);
            assert!(!receipt.owner_release_approved);
        }
    }
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
    project.deliverables[0].voice_track_id = None;
    assert!(
        render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err(),
        "A source mix cannot borrow an active voice absent from its delivery profile"
    );
    project.deliverables[0].voice_track_id = Some(id(40));
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_ok());
}
#[test]
fn existing_delivery_profile_regional_locales_keep_source_voice_binding() {
    for language in ["en-US", "es-MX", "es-ES", "de-DE"] {
        let (mut project, mut plan, [voice, music, sfx]) = fixture();
        project.deliverables[0].language = language.into();
        plan.language = language.into();
        let (_, receipt) =
            render_original_source_mix(&plan, &project, &voice, &music, &sfx).unwrap();
        assert_eq!(receipt.language, language);
        assert!(!receipt.listening_intelligibility_approved);
        assert!(!receipt.owner_release_approved);
    }
}

#[test]
fn regional_language_allowlist_never_infers_voice_rights_or_unknown_locales() {
    for language in ["fr-FR", "es-mx", "en_US", "de-XX"] {
        let (mut project, mut plan, [voice, music, sfx]) = fixture();
        project.deliverables[0].language = language.into();
        plan.language = language.into();
        assert!(
            render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err(),
            "Unsupported source mix locale must not be silently relabeled: {language}"
        );
    }
    let (project, mut plan, [voice, music, sfx]) = fixture();
    plan.language = "es-MX".into();
    assert!(
        render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err(),
        "Source mix language may not differ from the saved delivery profile"
    );
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

#[test]
fn measured_ducking_intervals_reject_a_different_voice_source_sha256() {
    let (mut project, plan, [voice, music, sfx]) = fixture();
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Measured {
        engine: "fixture-original-alignment".into(),
        source_sha256: "ab".repeat(32),
        confidence_millis: Some(999),
    };
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
    project.audio.transcript[0].alignment = domain::AlignmentEvidence::Measured {
        engine: "fixture-original-alignment".into(),
        source_sha256: project.audio.voice_tracks[0].source_sha256.clone(),
        confidence_millis: Some(999),
    };
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_ok());
}

#[test]
fn persisted_mix_recipe_rejects_an_unsaved_duck_or_source_override() {
    let (mut project, mut plan, [voice, music, sfx]) = fixture();
    project.production_design.delivery_recipes.insert(
        plan.deliverable_profile_id,
        domain::DeliveryRecipe {
            audio_targets: None,
            original_mix: Some(domain::OriginalMixRecipe {
                music_asset_id: Some(plan.original_music.asset_id),
                sfx_asset_id: plan.original_sfx.imported_asset_id(),
                sfx_gain_db: plan.sfx_gain_db,
                duck_attenuation_db: plan.duck_attenuation_db,
                duck_attack_samples: plan.duck_attack_samples,
                duck_release_samples: plan.duck_release_samples,
                voice_envelope: Default::default(),
                music_envelope: Default::default(),
                sfx_envelope: Default::default(),
            }),
            ..Default::default()
        },
    );
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_ok());
    plan.duck_attenuation_db = 0.0;
    assert!(render_original_source_mix(&plan, &project, &voice, &music, &sfx).is_err());
}
