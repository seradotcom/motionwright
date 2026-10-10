use motionwright_creative_library::{
    OriginalMixPlan, PcmStemReceipt, VoiceActivityWindow, render_original_source_mix, write_mix_wav,
};
use motionwright_domain as domain;
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
