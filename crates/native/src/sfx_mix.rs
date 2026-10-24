//! Native original SFX PCM16 mixer, backed by owner-local StudioService WAV bytes.
//! This prepares actual stereo samples for the existing Semwright MLT AV
//! mux; it is not an independent backend, a completed MP4, rights
//! clearance, LUFS master or proof of real customer audio.
use motionwright_domain::{Project, SFX_SAMPLES_PER_FRAME};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use uuid::Uuid;

pub const SFX_MAX_MASTER_FRAMES: u64 = 900;

#[derive(Clone)]
pub struct VerifiedSfxWav {
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub bytes: Vec<u8>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MixedSfxWav {
    pub wav_bytes: Vec<u8>,
    pub output_sha256: String,
    pub rendered_frames: u64,
    pub original_sources: usize,
    pub sample_peak_pcm16: u16,
}
fn invalid(s: &str) -> Error {
    Error::new(ErrorCode::InvalidArgument, s)
}
fn unsupported(s: &str) -> Error {
    Error::new(ErrorCode::Unsupported, s)
}
fn u16at(b: &[u8], i: usize) -> u16 {
    u16::from_le_bytes([b[i], b[i + 1]])
}
fn u32at(b: &[u8], i: usize) -> u32 {
    u32::from_le_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
}
fn verify_source(bytes: &[u8], frames: usize) -> NativeResult<()> {
    let size = frames
        .checked_mul(4)
        .ok_or_else(|| invalid("SFX source frame size overflow"))?;
    if bytes.len() != 44 + size
        || &bytes[0..4] != b"RIFF"
        || u32at(bytes, 4) != u32::try_from(bytes.len() - 8).unwrap_or(u32::MAX)
        || &bytes[8..16] != b"WAVEfmt "
        || u32at(bytes, 16) != 16
        || u16at(bytes, 20) != 1
        || u16at(bytes, 22) != 2
        || u32at(bytes, 24) != 48000
        || u32at(bytes, 28) != 192000
        || u16at(bytes, 32) != 4
        || u16at(bytes, 34) != 16
        || &bytes[36..40] != b"data"
        || usize::try_from(u32at(bytes, 40)).unwrap_or(usize::MAX) != size
    {
        return Err(unsupported(
            "SFX source must be one exact 48 kHz stereo PCM16 RIFF with no implicit resampling, trimming or codec conversion",
        ));
    }
    Ok(())
}
pub fn mix_source_bound_semantic_sfx(
    project: &Project,
    deliverable_id: Uuid,
    sources: &[VerifiedSfxWav],
    total_frames: u64,
) -> NativeResult<MixedSfxWav> {
    mix_source_bound_semantic_sfx_bounded(
        project,
        deliverable_id,
        sources,
        total_frames,
        SFX_MAX_MASTER_FRAMES,
    )
}

pub fn mix_source_bound_semantic_sfx_with_budget(
    project: &Project,
    deliverable_id: Uuid,
    sources: &[VerifiedSfxWav],
    total_frames: u64,
    budget: motionwright_service::OriginalMixDecodeBudget,
) -> NativeResult<MixedSfxWav> {
    let max_frames = match budget {
        motionwright_service::OriginalMixDecodeBudget::Audition15Seconds => 15 * 30,
        motionwright_service::OriginalMixDecodeBudget::FullCut180Seconds => 180 * 30,
    };
    mix_source_bound_semantic_sfx_bounded(
        project,
        deliverable_id,
        sources,
        total_frames,
        max_frames,
    )
}

fn mix_source_bound_semantic_sfx_bounded(
    project: &Project,
    deliverable_id: Uuid,
    sources: &[VerifiedSfxWav],
    total_frames: u64,
    max_frames: u64,
) -> NativeResult<MixedSfxWav> {
    if !(1..=max_frames).contains(&total_frames) {
        return Err(unsupported(
            "Native semantic SFX exceeds its explicit bounded source policy",
        ));
    }
    project
        .validate()
        .map_err(|_| invalid("Invalid SFX source project"))?;
    let profile = project
        .deliverables
        .iter()
        .find(|p| p.id == deliverable_id)
        .ok_or_else(|| invalid("Missing source-bound audio delivery profile"))?;
    if profile.frame_rate.num != 30
        || profile.frame_rate.den != 1
        || profile.audio_sample_rate_hz != 48000
    {
        return Err(unsupported(
            "SFX only supports 30 fps and 48 kHz without implicit conversion",
        ));
    }
    let expected_frames = project
        .sfx_cut_frames(deliverable_id)
        .map_err(|_| unsupported("SFX master cut must use exact 30 fps source times"))?;
    if total_frames != expected_frames {
        return Err(unsupported(
            "SFX master duration differs from the exact selected cut; no implicit padding or trim",
        ));
    }
    let schedule = project
        .validated_sfx_schedule_for_profile(deliverable_id)
        .map_err(|_| invalid("Invalid source SFX schedule"))?;
    if schedule.is_empty() || schedule.iter().any(|p| p.absolute_end_frame > total_frames) {
        return Err(unsupported(
            "SFX sources exceed selected native master or no cue was authored",
        ));
    }
    let mut ids = HashSet::new();
    for source in sources {
        if !ids.insert(source.asset_id)
            || hex::encode(Sha256::digest(&source.bytes)) != source.source_sha256
        {
            return Err(invalid(
                "Original WAV asset identity or contents differ from imported SHA-256",
            ));
        }
    }
    let expected_sources = project
        .audio
        .sfx_cues
        .iter()
        .filter(|cue| schedule.iter().any(|placement| placement.cue_id == cue.id))
        .map(|cue| cue.source_asset_id)
        .collect::<HashSet<_>>();
    if ids != expected_sources {
        return Err(invalid(
            "Verified SFX source inventory differs from the selected authored cues",
        ));
    }
    let total_samples = usize::try_from(total_frames)
        .ok()
        .and_then(|n| n.checked_mul(SFX_SAMPLES_PER_FRAME as usize))
        .ok_or_else(|| invalid("SFX output sample count overflow"))?;
    let mut mixed = vec![0_f64; total_samples * 2];
    for (cue, place) in project.audio.sfx_cues.iter().filter_map(|cue| {
        schedule
            .iter()
            .find(|p| p.cue_id == cue.id)
            .map(|p| (cue, p))
    }) {
        let source = sources
            .iter()
            .find(|source| {
                source.asset_id == cue.source_asset_id && source.source_sha256 == cue.source_sha256
            })
            .ok_or_else(|| invalid("No owner-verified WAV bytes for authored semantic cue"))?;
        let frame_count = usize::try_from(place.sample_count)
            .map_err(|_| invalid("Cue source frames exceed memory budget"))?;
        verify_source(&source.bytes, frame_count)?;
        let start = usize::try_from(place.first_sample)
            .map_err(|_| invalid("Cue offset exceeds native sample budget"))?;
        let attack = usize::from(cue.attack_frames) * SFX_SAMPLES_PER_FRAME as usize;
        let release = usize::from(cue.release_frames) * SFX_SAMPLES_PER_FRAME as usize;
        let gain = 10_f64.powf(f64::from(cue.gain_milli_db) / 20_000.0);
        for i in 0..frame_count {
            let incoming = if attack == 0 {
                1.0
            } else {
                (i as f64 / attack as f64).min(1.0)
            };
            let outgoing = if release == 0 {
                1.0
            } else {
                (frame_count.saturating_sub(i + 1) as f64 / release as f64).min(1.0)
            };
            let scale = gain * incoming.min(outgoing);
            for channel in 0..2 {
                let k = (i * 2 + channel) * 2 + 44;
                let sample = i16::from_le_bytes([source.bytes[k], source.bytes[k + 1]]);
                mixed[(start + i) * 2 + channel] += f64::from(sample) * scale;
            }
        }
    }
    let mut peak = 0_u16;
    let mut output = Vec::with_capacity(mixed.len());
    for value in mixed {
        if !value.is_finite() || value.round().abs() > 32767.0 {
            return Err(unsupported(
                "SFX source mix would clip; reduce gain or overlap, no hidden limiter",
            ));
        }
        let sample = value.round() as i16;
        peak = peak.max(sample.unsigned_abs());
        output.push(sample);
    }
    let data_bytes = u32::try_from(
        output
            .len()
            .checked_mul(2)
            .ok_or_else(|| invalid("WAV size overflow"))?,
    )
    .map_err(|_| invalid("WAV too large"))?;
    let mut wav = Vec::with_capacity(44 + data_bytes as usize);
    wav.extend_from_slice(b"RIFF");
    wav.extend_from_slice(&(data_bytes + 36).to_le_bytes());
    wav.extend_from_slice(b"WAVEfmt ");
    wav.extend_from_slice(&16_u32.to_le_bytes());
    wav.extend_from_slice(&1_u16.to_le_bytes());
    wav.extend_from_slice(&2_u16.to_le_bytes());
    wav.extend_from_slice(&48000_u32.to_le_bytes());
    wav.extend_from_slice(&192000_u32.to_le_bytes());
    wav.extend_from_slice(&4_u16.to_le_bytes());
    wav.extend_from_slice(&16_u16.to_le_bytes());
    wav.extend_from_slice(b"data");
    wav.extend_from_slice(&data_bytes.to_le_bytes());
    for sample in output {
        wav.extend_from_slice(&sample.to_le_bytes());
    }
    let sha = hex::encode(Sha256::digest(&wav));
    Ok(MixedSfxWav {
        wav_bytes: wav,
        output_sha256: sha,
        rendered_frames: total_frames,
        original_sources: ids.len(),
        sample_peak_pcm16: peak,
    })
}
