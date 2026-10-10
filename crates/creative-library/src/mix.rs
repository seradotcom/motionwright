//! Deterministic original PCM-bus mix tied to existing Project.audio and
//! DeliverableProfile, never a second audio timeline or a mastering claim.
//!
//! Sample-accurate, source-bound, explicit music ducking. No unlicensed media,
//! speech generation, implicit ASR authority, clipping repair or automatic
//! normalization. EBU R128 / intersample true peak need an independent decode.
use crate::*;
use motionwright_domain::{AlignmentEvidence, Project, RationalTime};
use sha2::{Digest, Sha256};
use std::io::Write;

const SAMPLE_RATE: u64 = 48_000;
const MAX_SAMPLES: u64 = 48_000 * 15;
const MAX_PCM_BYTES: usize = (MAX_SAMPLES * 4) as usize;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct PcmStemReceipt {
    pub asset_id: Uuid,
    /// Exact SHA-256 of the immutable source file owned by Project.assets.
    /// Usually a WAV/other admitted audio container, NOT its decoded PCM.
    pub source_asset_sha256: String,
    /// Exact independently decoded stereo PCM hash used by the mixer itself.
    /// Containers and PCM must have separate fingerprints.
    pub decoded_pcm_sha256: String,
    pub sample_frames: u64,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct VoiceActivityWindow {
    pub start_sample: u64,
    pub end_exclusive: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixPlan {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub project_revision: u64,
    pub deliverable_profile_id: Uuid,
    pub language: String,
    pub sample_rate: u32,
    pub channels: u8,
    pub sample_frames: u64,
    pub measured_voice: PcmStemReceipt,
    pub original_music: PcmStemReceipt,
    pub original_sfx: PcmStemReceipt,
    pub voice_gain_db: f64,
    pub music_gain_db: f64,
    pub sfx_gain_db: f64,
    pub duck_attenuation_db: f64,
    pub duck_attack_samples: u32,
    pub duck_release_samples: u32,
    pub voice_windows: Vec<VoiceActivityWindow>,
    pub sample_peak_guard_dbfs: f64,
    pub source_classification: String,
    pub owner_mixing_approval_authenticated: bool,
    pub is_mastered: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct OriginalMixEvidence {
    pub schema: String,
    pub source_plan_sha256: String,
    pub rendered_pcm_sha256: String,
    pub language: String,
    pub sample_rate: u32,
    pub channels: u8,
    pub frames: u64,
    pub measured_pcm_sample_peak_dbfs: f64,
    pub measured_pcm_rms_dbfs: f64,
    pub minimum_music_duck_gain: f64,
    pub measured_music_duck_windows: u32,
    pub input_source_checks: String,
    pub rendered_media: String,
    pub independently_verified_lufs: bool,
    pub independently_verified_true_peak: bool,
    pub listening_intelligibility_approved: bool,
    pub owner_release_approved: bool,
}
fn time_to_sample(r: RationalTime) -> Result<u64> {
    check(
        r.den > 0 && r.num >= 0,
        "Voice timing must be nonnegative rational",
    )?;
    let numerator = i128::from(r.num) * i128::from(SAMPLE_RATE);
    let denominator = i128::from(r.den);
    check(
        numerator % denominator == 0,
        "Narration time crosses a fractional source sample; explicit retime/resampling needed",
    )?;
    u64::try_from(numerator / denominator)
        .map_err(|_| CraftError("Voice sample index exceeded bounded source clock".into()))
}
fn check_stem(asset: &PcmStemReceipt, project: &Project, n: u64) -> Result<()> {
    check(
        asset.sample_frames == n
            && valid_sha(&asset.source_asset_sha256)
            && valid_sha(&asset.decoded_pcm_sha256),
        "Mix stems must be exact 48-kHz source-length and PCM digest",
    )?;
    check(
        project.assets.iter().any(|registered| {
            registered.id == asset.asset_id
                && registered.media_type.starts_with("audio/")
                && registered.content_sha256.as_deref() == Some(asset.source_asset_sha256.as_str())
        }),
        "Mix stem is not a current project-owned source asset",
    )?;
    Ok(())
}
impl OriginalMixPlan {
    pub fn validate(&self, project: &Project) -> Result<()> {
        check(
            self.schema == "motionwright.original-audio-mix/1"
                && self.project_id == project.id
                && self.generation == project.generation
                && self.project_revision == project.revision
                && self.sample_rate == 48000
                && self.channels == 2
                && (SAMPLE_RATE..=MAX_SAMPLES).contains(&self.sample_frames),
            "Mix cannot proceed on a stale project or unsupported PCM timebase",
        )?;
        let profile = project
            .deliverables
            .iter()
            .find(|item| item.id == self.deliverable_profile_id)
            .ok_or_else(|| CraftError("Mix has no current delivery profile".into()))?;
        check(
            profile.audio_sample_rate_hz == self.sample_rate
                && profile.language == self.language
                && matches!(self.language.as_str(), "en" | "es" | "de"),
            "Localized mix must use the exact existing delivery profile/language",
        )?;
        project
            .audio
            .validate()
            .map_err(|err| CraftError(err.to_string()))?;
        let active = project
            .audio
            .active_voice_track_id
            .ok_or_else(|| CraftError("No owner-imported measured active voice track".into()))?;
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|voice| voice.id == active)
            .ok_or_else(|| CraftError("Measured voice track is no longer active".into()))?;
        check(
            track.sample_rate_hz == 48000
                && track.channels == 2
                && track.asset_id == self.measured_voice.asset_id
                && track.source_sha256 == self.measured_voice.source_asset_sha256
                && time_to_sample(track.measured_duration)? == self.sample_frames
                && project.assets.iter().any(|asset| {
                    asset.id == track.asset_id
                        && asset.content_sha256.as_deref() == Some(track.source_sha256.as_str())
                }),
            "Mix must retain the exact imported measured voice asset identity",
        )?;
        if let Some(voice) = profile.voice_track_id {
            check(
                voice == track.id,
                "Localized delivery profile refers to another voice take",
            )?;
        }
        check_stem(&self.measured_voice, project, self.sample_frames)?;
        check_stem(&self.original_music, project, self.sample_frames)?;
        check_stem(&self.original_sfx, project, self.sample_frames)?;
        check(
            self.original_music.asset_id != self.measured_voice.asset_id
                && self.original_sfx.asset_id != self.measured_voice.asset_id
                && self.original_sfx.asset_id != self.original_music.asset_id,
            "Voice, music and SFX require three distinct source assets",
        )?;
        for value in [self.voice_gain_db, self.music_gain_db, self.sfx_gain_db] {
            check(
                value.is_finite() && (-80.0..=12.0).contains(&value),
                "Mix source gain outside bounded dB profile",
            )?;
        }
        check(
            self.voice_gain_db == f64::from(project.audio.mix.voice_gain_db)
                && self.music_gain_db == f64::from(project.audio.mix.music_gain_db),
            "Voice/music mix gain differs from existing editorial domain state",
        )?;
        check(
            self.duck_attenuation_db.is_finite()
                && (0.0..=30.0).contains(&self.duck_attenuation_db)
                && (1..=48000).contains(&self.duck_attack_samples)
                && (1..=48000).contains(&self.duck_release_samples),
            "Sidechain ducking must have explicit bounded attenuation/attack/release",
        )?;
        check(
            self.sample_peak_guard_dbfs.is_finite()
                && (-6.0..=-0.1).contains(&self.sample_peak_guard_dbfs),
            "Mix needs a sample peak guard before independent true-peak measurement",
        )?;
        check(
            !self.owner_mixing_approval_authenticated
                && !self.is_mastered
                && self.source_classification
                    == "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED",
            "Mix cannot self-grant human approval or mastering authority",
        )?;
        check(
            (1..=64).contains(&self.voice_windows.len()),
            "Ducking requires explicitly reviewed nonempty source speech intervals",
        )?;
        let mut prev = 0u64;
        for (i, w) in self.voice_windows.iter().enumerate() {
            check(
                (i == 0 || w.start_sample >= prev)
                    && w.start_sample < w.end_exclusive
                    && w.end_exclusive <= self.sample_frames,
                "Voice duck windows are overlapping, unsorted or out of source bounds",
            )?;
            prev = w.end_exclusive;
        }
        // Windows are taken from the existing, manually/reliably aligned
        // measured voice transcript. There is no amplitude-based voice activity
        // guess and no template guess based on word count.
        let mut accepted = project
            .audio
            .transcript
            .iter()
            .filter(|s| s.voice_track_id == track.id)
            .map(|s| {
                let source_is_measured = match &s.alignment {
                    AlignmentEvidence::Manual => true,
                    AlignmentEvidence::Measured {
                        confidence_millis: Some(conf),
                        ..
                    } if *conf >= 800 => true,
                    _ => false,
                };
                if !source_is_measured {
                    return Err(CraftError(
                        "Unreviewed ASR timing cannot drive approved ducking".into(),
                    ));
                }
                Ok(VoiceActivityWindow {
                    start_sample: time_to_sample(s.start)?,
                    end_exclusive: time_to_sample(s.end)?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        accepted.sort_by_key(|w| w.start_sample);
        check(
            accepted == self.voice_windows,
            "Mix duck windows differ from the project's source-bound reviewed transcript",
        )?;
        Ok(())
    }
}
fn db_to_gain(value: f64) -> f64 {
    10.0_f64.powf(value / 20.0)
}
fn duck_gain(plan: &OriginalMixPlan, at: u64) -> f64 {
    let low = db_to_gain(-plan.duck_attenuation_db);
    let mut result = 1.0_f64;
    for interval in &plan.voice_windows {
        let start = interval.start_sample;
        let end = interval.end_exclusive;
        let current = if at < start {
            1.0
        } else if at < start + u64::from(plan.duck_attack_samples) {
            let progress = (at - start) as f64 / f64::from(plan.duck_attack_samples);
            1.0 + (low - 1.0) * progress
        } else if at < end {
            low
        } else if at < end + u64::from(plan.duck_release_samples) {
            let progress = (at - end) as f64 / f64::from(plan.duck_release_samples);
            low + (1.0 - low) * progress
        } else {
            1.0
        };
        result = result.min(current);
    }
    result
}
fn digest_pcm(samples: &[u8], receipt: &PcmStemReceipt) -> Result<()> {
    check(
        samples.len() <= MAX_PCM_BYTES && samples.len() == receipt.sample_frames as usize * 4,
        "Mix PCM input size differs from admitted exact 16-bit stereo source",
    )?;
    let digest = hex::encode(Sha256::digest(samples));
    check(
        digest == receipt.decoded_pcm_sha256,
        "Mix cannot reuse an unverified/stale audio input digest",
    )
}
fn sample(bytes: &[u8], frame: usize, ch: usize) -> f64 {
    let idx = frame * 4 + ch * 2;
    f64::from(i16::from_le_bytes([bytes[idx], bytes[idx + 1]])) / 32768.0
}
/// Verified, sample-accurate source-only PCM rendering. The caller must
/// independently decode original files, retain source rights and measure
/// resulting R128 + intersample true peak before real delivery.
pub fn render_original_source_mix(
    plan: &OriginalMixPlan,
    project: &Project,
    voice: &[u8],
    music: &[u8],
    sfx: &[u8],
) -> Result<(Vec<u8>, OriginalMixEvidence)> {
    plan.validate(project)?;
    digest_pcm(voice, &plan.measured_voice)?;
    digest_pcm(music, &plan.original_music)?;
    digest_pcm(sfx, &plan.original_sfx)?;
    let gains = [plan.voice_gain_db, plan.music_gain_db, plan.sfx_gain_db].map(db_to_gain);
    let limit = db_to_gain(plan.sample_peak_guard_dbfs);
    let mut out = Vec::with_capacity(plan.sample_frames as usize * 4);
    let mut peak = 0.0_f64;
    let mut sum_squared = 0.0_f64;
    let mut lowest_duck = 1.0_f64;
    for index in 0..plan.sample_frames as usize {
        let duck = duck_gain(plan, index as u64);
        lowest_duck = lowest_duck.min(duck);
        for channel in 0..2 {
            let mixed = sample(voice, index, channel) * gains[0]
                + sample(music, index, channel) * gains[1] * duck
                + sample(sfx, index, channel) * gains[2];
            check(
                mixed.is_finite() && mixed.abs() < limit,
                "Original PCM mix would clip or violate guard: review gains instead of silently applying a limiter",
            )?;
            peak = peak.max(mixed.abs());
            sum_squared += mixed * mixed;
            let quantized = (mixed * 32767.0).round().clamp(-32768.0, 32767.0) as i16;
            out.extend_from_slice(&quantized.to_le_bytes());
        }
    }
    let rms = (sum_squared / (plan.sample_frames as f64 * 2.0)).sqrt();
    let level = |x: f64| if x == 0.0 { -120.0 } else { 20.0 * x.log10() };
    let evidence = OriginalMixEvidence {
        schema: "motionwright.original-mix-source-pcm/1".into(),
        source_plan_sha256: canonical_digest(plan)?,
        rendered_pcm_sha256: hex::encode(Sha256::digest(&out)),
        language: plan.language.clone(),
        sample_rate: 48000,
        channels: 2,
        frames: plan.sample_frames,
        measured_pcm_sample_peak_dbfs: level(peak),
        measured_pcm_rms_dbfs: level(rms),
        minimum_music_duck_gain: lowest_duck,
        measured_music_duck_windows: plan.voice_windows.len() as u32,
        input_source_checks: "ALL_THREE_FULL_PCM_SHA256_VERIFIED".into(),
        rendered_media: "RAW_PCM_S16LE_ONLY".into(),
        independently_verified_lufs: false,
        independently_verified_true_peak: false,
        listening_intelligibility_approved: false,
        owner_release_approved: false,
    };
    Ok((out, evidence))
}
pub fn write_mix_wav<W: Write>(
    plan: &OriginalMixPlan,
    project: &Project,
    voice: &[u8],
    music: &[u8],
    sfx: &[u8],
    out: &mut W,
) -> Result<OriginalMixEvidence> {
    let (pcm, evidence) = render_original_source_mix(plan, project, voice, music, sfx)?;
    let n = u32::try_from(pcm.len())
        .map_err(|_| CraftError("RIFF output byte budget exceeded".into()))?;
    let mut header = Vec::with_capacity(44);
    header.extend_from_slice(b"RIFF");
    header.extend_from_slice(&(36 + n).to_le_bytes());
    header.extend_from_slice(b"WAVEfmt ");
    header.extend_from_slice(&16_u32.to_le_bytes());
    header.extend_from_slice(&1_u16.to_le_bytes());
    header.extend_from_slice(&2_u16.to_le_bytes());
    header.extend_from_slice(&48000_u32.to_le_bytes());
    header.extend_from_slice(&192000_u32.to_le_bytes());
    header.extend_from_slice(&4_u16.to_le_bytes());
    header.extend_from_slice(&16_u16.to_le_bytes());
    header.extend_from_slice(b"data");
    header.extend_from_slice(&n.to_le_bytes());
    out.write_all(&header)
        .map_err(|e| CraftError(format!("RIFF header could not be written: {e}")))?;
    out.write_all(&pcm)
        .map_err(|e| CraftError(format!("PCM source could not be written: {e}")))?;
    Ok(evidence)
}
