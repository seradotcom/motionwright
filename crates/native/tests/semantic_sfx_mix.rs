//! Actual source-audible PCM acceptance, not fabricated mix labels or MP4 proof.
use motionwright_domain::*;
use motionwright_native::sfx_mix::{VerifiedSfxWav, mix_source_bound_semantic_sfx};
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn make_wav(source_frames: u16, amplitude: i16) -> Vec<u8> {
    let frames = usize::from(source_frames) * 1600;
    let size = (frames * 4) as u32;
    let mut bytes = Vec::with_capacity(44 + size as usize);
    bytes.extend_from_slice(b"RIFF");
    bytes.extend_from_slice(&(size + 36).to_le_bytes());
    bytes.extend_from_slice(b"WAVEfmt ");
    bytes.extend_from_slice(&16_u32.to_le_bytes());
    bytes.extend_from_slice(&1_u16.to_le_bytes());
    bytes.extend_from_slice(&2_u16.to_le_bytes());
    bytes.extend_from_slice(&48000_u32.to_le_bytes());
    bytes.extend_from_slice(&192000_u32.to_le_bytes());
    bytes.extend_from_slice(&4_u16.to_le_bytes());
    bytes.extend_from_slice(&16_u16.to_le_bytes());
    bytes.extend_from_slice(b"data");
    bytes.extend_from_slice(&size.to_le_bytes());
    for sample in 0..frames {
        let left = if (sample / 48) % 2 == 0 {
            amplitude
        } else {
            -amplitude
        };
        let right = if (sample / 64) % 2 == 0 {
            -amplitude
        } else {
            amplitude
        };
        bytes.extend_from_slice(&left.to_le_bytes());
        bytes.extend_from_slice(&right.to_le_bytes());
    }
    bytes
}
fn project(amplitude: i16, gain: i32) -> (Project, VerifiedSfxWav) {
    let mut p = Project::new("Original source WAV with semantic cue").unwrap();
    p.apply_change(&Change::AddScene {
        name: "Original sound scene".into(),
        objective: "Observe exact stereo WAV events".into(),
        duration_seconds: 6,
    })
    .unwrap();
    let source = make_wav(30, amplitude);
    let sha = hex::encode(Sha256::digest(&source));
    let id = Uuid::new_v4();
    p.apply_change(&Change::AddAsset {
        asset: Asset {
            id,
            name: "Original generated PCM technical fixture".into(),
            media_type: "audio/wav".into(),
            content_sha256: Some(sha.clone()),
            source_revision: Some("synthetic original source for native test".into()),
        },
    })
    .unwrap();
    p.apply_change(&Change::UpsertSemanticSfx {
        cue: SemanticSfxCue {
            schema: SFX_CUE_SCHEMA.into(),
            id: Uuid::new_v4(),
            scene_id: p.scenes[0].id,
            event: SfxEvent::UiFocus,
            anchor: SfxAnchor::SceneStart,
            offset_frames: 5,
            source_asset_id: id,
            source_sha256: sha.clone(),
            source_frames: 30,
            gain_milli_db: gain,
            attack_frames: 3,
            release_frames: 10,
            original_source_attestation: "I created this exact original PCM sound fixture".into(),
        },
    })
    .unwrap();
    (
        p,
        VerifiedSfxWav {
            asset_id: id,
            source_sha256: sha,
            bytes: source,
        },
    )
}
fn sample(bytes: &[u8], index: usize, channel: usize) -> i16 {
    let at = 44 + (index * 2 + channel) * 2;
    i16::from_le_bytes([bytes[at], bytes[at + 1]])
}
#[test]
fn real_verified_pcm16_stereo_has_attack_release_and_exact_scene_sample_locations() {
    let (p, src) = project(8000, -6000);
    let result = mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, &[src], 180).unwrap();
    assert_eq!(result.wav_bytes.len(), 44 + 180 * 1600 * 4);
    assert_eq!(result.rendered_frames, 180);
    assert_eq!(result.original_sources, 1);
    assert!(result.sample_peak_pcm16 > 2000 && result.sample_peak_pcm16 < 8000);
    assert_eq!(
        hex::encode(Sha256::digest(&result.wav_bytes)),
        result.output_sha256
    );
    assert_eq!(sample(&result.wav_bytes, 4 * 1600, 0), 0);
    assert_eq!(sample(&result.wav_bytes, 5 * 1600, 0), 0); // attack at onset
    assert!(sample(&result.wav_bytes, 10 * 1600, 0).abs() > 1000);
    assert!(sample(&result.wav_bytes, 10 * 1600, 1).abs() > 1000);
    assert_eq!(sample(&result.wav_bytes, 35 * 1600, 0), 0); // sound ends cleanly
    assert_eq!(sample(&result.wav_bytes, 179 * 1600, 1), 0);
    // PCM release decays to zero at the last original source sample.
    assert_eq!(sample(&result.wav_bytes, 35 * 1600 - 1, 1), 0);
}
#[test]
fn rehashed_foreign_audio_wrong_riff_duration_and_duplicate_sources_fail_closed() {
    let (p, src) = project(5000, -12000);
    let mut forged = src.clone();
    forged.bytes[100] ^= 0x10;
    assert!(mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, &[forged], 180).is_err());
    let mut riff = src.clone();
    riff.bytes[24..28].copy_from_slice(&44100_u32.to_le_bytes());
    riff.source_sha256 = hex::encode(Sha256::digest(&riff.bytes));
    let mut cloned = p.clone();
    cloned.audio.sfx_cues[0].source_sha256 = riff.source_sha256.clone();
    cloned.assets[0].content_sha256 = Some(riff.source_sha256.clone());
    assert!(
        mix_source_bound_semantic_sfx(&cloned, cloned.deliverables[0].id, &[riff], 180).is_err()
    );
    assert!(
        mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, &[src.clone(), src], 180).is_err()
    );
    assert!(mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, &[], 180).is_err());
}
#[test]
fn native_mix_rejects_clipping_instead_of_silent_mastering() {
    let (mut p, src) = project(30000, 0);
    let mut second = p.audio.sfx_cues[0].clone();
    second.id = Uuid::new_v4();
    p.apply_change(&Change::UpsertSemanticSfx { cue: second })
        .unwrap();
    let error = mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, &[src], 180).unwrap_err();
    assert!(error.to_string().contains("clip"));
}
#[test]
fn unsupported_master_fps_or_length_cannot_be_silently_resampled() {
    let (mut p, src) = project(4000, -12000);
    assert!(
        mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, std::slice::from_ref(&src), 901)
            .is_err()
    );
    p.deliverables[0].frame_rate = RationalTime::new(24, 1).unwrap();
    assert!(mix_source_bound_semantic_sfx(&p, p.deliverables[0].id, &[src], 180).is_err());
}
