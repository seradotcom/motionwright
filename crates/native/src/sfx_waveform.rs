//! Immutable original semantic SFX source waveform observation.
//! Source bytes come exclusively from StudioService owner CAS (not WebView
//! media paths, model-generated measurements or alternative decoders).
use motionwright_domain::{Project, SFX_SAMPLES_PER_FRAME};
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::sfx_mix::verify_source;

fn invalid(message: &'static str) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
fn stale(message: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, message)
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OriginalSfxWaveform {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub cue_id: Uuid,
    pub asset_id: Uuid,
    pub source_sha256: String,
    pub source_frames: u16,
    pub sample_rate_hz: u32,
    pub channels: u16,
    pub samples_per_peak: u32,
    /// Exact positive and negative sample extrema, not estimated RMS/LUFS.
    pub left_min_pcm16: Vec<i16>,
    pub left_max_pcm16: Vec<i16>,
    pub right_min_pcm16: Vec<i16>,
    pub right_max_pcm16: Vec<i16>,
    pub sample_peak_pcm16: u16,
    pub measured_sha256: String,
    pub evidence_scope: String,
}

pub fn inspect_owner_semantic_sfx_waveform(
    project: &Project,
    cue_id: Uuid,
    original_bytes: &[u8],
) -> NativeResult<OriginalSfxWaveform> {
    project
        .validate()
        .map_err(|_| invalid("Original SFX waveform project model is invalid"))?;
    let cue = project
        .audio
        .sfx_cues
        .iter()
        .find(|cue| cue.id == cue_id)
        .ok_or_else(|| stale("Original SFX waveform cue is not authored in this revision"))?;
    // Validate all authored source references, scene timing, beat anchors,
    // audio envelope and imported Asset digests against one exact revision.
    project
        .validated_sfx_schedule()
        .map_err(|_| stale("Original SFX waveform source project lost its valid scene schedule"))?;
    if !(1..=180).contains(&cue.source_frames)
        || cue.source_sha256.len() != 64
        || !cue
            .source_sha256
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        || hex::encode(Sha256::digest(original_bytes)) != cue.source_sha256
    {
        return Err(stale(
            "Original imported SFX waveform bytes do not match current CAS source",
        ));
    }
    let frames = usize::from(cue.source_frames);
    let samples_per_frame = usize::try_from(SFX_SAMPLES_PER_FRAME)
        .map_err(|_| invalid("Original SFX sample/frame constant overflow"))?;
    let sample_count = frames
        .checked_mul(samples_per_frame)
        .ok_or_else(|| invalid("Original SFX waveform sample count overflow"))?;
    // Same strict source WAV parser used by the actual pinned native mixer:
    // exact canonical RIFF header and full source duration; never trim,
    // resample, decode compressed files or ignore trailing extra bytes.
    verify_source(original_bytes, sample_count)?;
    let mut left_min_pcm16 = Vec::with_capacity(frames);
    let mut left_max_pcm16 = Vec::with_capacity(frames);
    let mut right_min_pcm16 = Vec::with_capacity(frames);
    let mut right_max_pcm16 = Vec::with_capacity(frames);
    let mut peak = 0_u16;
    for index in 0..frames {
        let mut min = [i16::MAX, i16::MAX];
        let mut max = [i16::MIN, i16::MIN];
        for sample in 0..samples_per_frame {
            let byte_index = 44 + (index * samples_per_frame + sample) * 4;
            for channel in 0..2 {
                let i = byte_index + channel * 2;
                let signed = i16::from_le_bytes([original_bytes[i], original_bytes[i + 1]]);
                min[channel] = min[channel].min(signed);
                max[channel] = max[channel].max(signed);
                peak = peak.max(signed.unsigned_abs());
            }
        }
        left_min_pcm16.push(min[0]);
        left_max_pcm16.push(max[0]);
        right_min_pcm16.push(min[1]);
        right_max_pcm16.push(max[1]);
    }
    let digest_bytes = serde_json::to_vec(&(
        cue.id,
        cue.source_asset_id,
        &cue.source_sha256,
        cue.source_frames,
        &left_min_pcm16,
        &left_max_pcm16,
        &right_min_pcm16,
        &right_max_pcm16,
    ))
    .map_err(|_| invalid("Original SFX waveform evidence could not be encoded"))?;
    Ok(OriginalSfxWaveform {
        schema: "motionwright.original-sfx-waveform/1".into(),
        project_id: project.id,
        generation: project.generation,
        revision: project.revision,
        cue_id,
        asset_id: cue.source_asset_id,
        source_sha256: cue.source_sha256.clone(),
        source_frames: cue.source_frames,
        sample_rate_hz: 48_000,
        channels: 2,
        samples_per_peak: SFX_SAMPLES_PER_FRAME as u32,
        left_min_pcm16,
        left_max_pcm16,
        right_min_pcm16,
        right_max_pcm16,
        sample_peak_pcm16: peak,
        measured_sha256: hex::encode(Sha256::digest(&digest_bytes)),
        evidence_scope:
            "owner-CAS-verified-original-PCM16-per-frame-extrema-NOT-listening-or-master".into(),
    })
}
