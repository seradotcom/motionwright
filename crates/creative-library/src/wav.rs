//! Deterministic, bounded PCM output for original audio designs. No ambient media path access.
use crate::{
    CraftError, GainPoint, Result, SoundPlan, canonical_digest, check, gain_at, render_sound_block,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Write;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OriginalWavEvidence {
    pub schema: String,
    pub source_plan_sha256: String,
    pub source_classification: String,
    pub wav_sha256: String,
    pub sample_rate: u32,
    pub channels: u8,
    pub frames: u64,
    pub bits_per_sample: u16,
    pub data_bytes: u64,
    pub file_bytes: u64,
    pub measured_sample_peak_dbfs: f64,
    pub mastering_status: String,
}
fn write_and_hash<W: Write>(writer: &mut W, hasher: &mut Sha256, bytes: &[u8]) -> Result<()> {
    writer
        .write_all(bytes)
        .map_err(|error| CraftError(format!("PCM output could not be written: {error}")))?;
    hasher.update(bytes);
    Ok(())
}
pub fn write_original_wav<W: Write>(
    plan: &SoundPlan,
    writer: &mut W,
) -> Result<OriginalWavEvidence> {
    plan.validate()?;
    check(
        !plan.applies_to_existing_bus,
        "Silence-release is gain automation for another authorized bus, not generated audible source",
    )?;
    let data_bytes = plan
        .sample_frames
        .checked_mul(4)
        .ok_or_else(|| CraftError("PCM audio byte budget overflow".into()))?;
    let data_size = u32::try_from(data_bytes)
        .map_err(|_| CraftError("RIFF PCM data exceeds the 4 GiB format limit".into()))?;
    let riff_size = data_size
        .checked_add(36)
        .ok_or_else(|| CraftError("RIFF size overflow".into()))?;
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    header.extend_from_slice(&riff_size.to_le_bytes());
    header.extend_from_slice(b"WAVEfmt ");
    header.extend_from_slice(&16_u32.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&2_u16.to_le_bytes());
    header.extend_from_slice(&plan.sample_rate.to_le_bytes());
    header.extend_from_slice(&(plan.sample_rate * 4).to_le_bytes());
    header.extend_from_slice(&4_u16.to_le_bytes());
    header.extend_from_slice(&16_u16.to_le_bytes());
    header.extend_from_slice(b"data");
    header.extend_from_slice(&data_size.to_le_bytes());
    let mut hash = Sha256::new();
    write_and_hash(writer, &mut hash, &header)?;
    let mut offset = 0u64;
    let mut peak = 0.0_f64;
    while offset < plan.sample_frames {
        let take = (plan.sample_frames - offset).min(16_384) as usize;
        let frames = render_sound_block(plan, offset, take)?;
        let mut buf = Vec::with_capacity(take * 4);
        for frame in frames {
            for channel in frame {
                let sample = f64::from(channel);
                peak = peak.max(sample.abs());
                let quantized = (sample * 32767.0).round().clamp(-32768.0, 32767.0) as i16;
                buf.extend_from_slice(&quantized.to_le_bytes());
            }
        }
        write_and_hash(writer, &mut hash, &buf)?;
        offset += take as u64;
    }
    Ok(OriginalWavEvidence {
        schema: "motionwright.original-sound-pcm/1".into(),
        source_plan_sha256: canonical_digest(plan)?,
        source_classification: plan.source_classification.clone(),
        wav_sha256: hex::encode(hash.finalize()),
        sample_rate: plan.sample_rate,
        channels: 2,
        frames: plan.sample_frames,
        bits_per_sample: 16,
        data_bytes,
        file_bytes: data_bytes + 44,
        measured_sample_peak_dbfs: if peak == 0.0 {
            -120.0
        } else {
            (20.0 * peak.log10() * 1000.0).round() / 1000.0
        },
        mastering_status: "native_generated_source_unmastered_review_required".into(),
    })
}
/// Explicitly apply a versioned gain envelope to a previously authorized stereo PCM bus.
/// Caller owns provenance, file access, mixing and final mastering; this function cannot fabricate a bus.
pub fn apply_existing_bus_envelope(
    samples: &[[f32; 2]],
    absolute_start: u64,
    points: &[GainPoint],
) -> Result<Vec<[f32; 2]>> {
    check(
        points.len() >= 2
            && points.len() <= 256
            && points[0].sample == 0
            && points
                .windows(2)
                .all(|pair| pair[0].sample < pair[1].sample),
        "Gain envelope must have bounded ordered sample keys",
    )?;
    check(
        samples.len() <= 65536,
        "Existing bus processing must be chunked",
    )?;
    let mut output = Vec::with_capacity(samples.len());
    for (offset, frame) in samples.iter().enumerate() {
        let index = absolute_start
            .checked_add(offset as u64)
            .ok_or_else(|| CraftError("Gain envelope sample counter overflow".into()))?;
        let g = gain_at(points, index);
        check(
            g.is_finite() && (0.0..=1.0).contains(&g),
            "Gain automation must remain within [0,1]",
        )?;
        let channels = frame.map(|sample| sample * g as f32);
        check(
            channels
                .iter()
                .all(|value| value.is_finite() && value.abs() <= 1.0),
            "Existing PCM bus contains non-finite or clipping values",
        )?;
        output.push(channels);
    }
    Ok(output)
}
