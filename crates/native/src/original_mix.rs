//! Deterministic original PCM-bus mix tied to existing Project.audio and
//! DeliverableProfile, never a second audio timeline or a mastering claim.
//!
//! Sample-accurate, source-bound, explicit music ducking. No unlicensed media,
//! speech generation, implicit ASR authority, clipping repair or automatic
//! normalization. EBU R128 / intersample true peak need an independent decode.
//! This 3-bus PCM implementation remains inside motionwright-native and
//! uses the same pinned Semwright SDK authority as other production paths.
use motionwright_domain::{
    AlignmentEvidence, DeliverableProfile, OriginalMixRecipe, Project, RationalTime, RevisionStamp,
    VoiceTrack, variant_dependency_fingerprint,
};
use motionwright_service::StudioService;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::io::Write;
use uuid::Uuid;
#[derive(Debug)]
pub struct MixError(pub String);
impl std::fmt::Display for MixError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
impl std::error::Error for MixError {}
type Result<T> = std::result::Result<T, MixError>;
fn check(condition: bool, reason: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(MixError(reason.into()))
    }
}
fn valid_sha(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn canonical_digest<T: serde::Serialize>(value: &T) -> Result<String> {
    let raw = serde_json::to_vec(value)
        .map_err(|e| MixError(format!("Cannot encode exact source: {e}")))?;
    Ok(hex::encode(Sha256::digest(raw)))
}

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
        .map_err(|_| MixError("Voice sample index exceeded bounded source clock".into()))
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
            .ok_or_else(|| MixError("Mix has no current delivery profile".into()))?;
        check(
            profile.audio_sample_rate_hz == self.sample_rate
                && profile.language == self.language
                && matches!(self.language.as_str(), "en" | "es" | "de"),
            "Localized mix must use the exact existing delivery profile/language",
        )?;
        project
            .audio
            .validate()
            .map_err(|err| MixError(err.to_string()))?;
        let active = project
            .audio
            .active_voice_track_id
            .ok_or_else(|| MixError("No owner-imported measured active voice track".into()))?;
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|voice| voice.id == active)
            .ok_or_else(|| MixError("Measured voice track is no longer active".into()))?;
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
        if let Some(saved) = project
            .production_design
            .delivery_recipes
            .get(&self.deliverable_profile_id)
            .and_then(|recipe| recipe.original_mix.as_ref())
        {
            check(
                saved.music_asset_id == Some(self.original_music.asset_id)
                    && saved.sfx_asset_id == Some(self.original_sfx.asset_id)
                    && saved.sfx_gain_db == self.sfx_gain_db
                    && saved.duck_attenuation_db == self.duck_attenuation_db
                    && saved.duck_attack_samples == self.duck_attack_samples
                    && saved.duck_release_samples == self.duck_release_samples,
                "Mix sources or envelopes differ from the saved delivery recipe",
            )?;
        }
        let accepted = reviewed_voice_windows(project, track.id)?;
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
        let attack = u64::from(plan.duck_attack_samples);
        let release = u64::from(plan.duck_release_samples);
        let current = if at < start {
            1.0
        } else if at < end {
            if at - start < attack {
                let progress = (at - start) as f64 / f64::from(plan.duck_attack_samples);
                1.0 + (low - 1.0) * progress
            } else {
                low
            }
        } else if at - end < release {
            // A short voice interval can end before attack reaches `low`.
            // Release from its reached gain at that exact sample boundary;
            // continuing attack or jumping to `low` would create a click.
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
        .map_err(|_| MixError("RIFF output byte budget exceeded".into()))?;
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
        .map_err(|e| MixError(format!("RIFF header could not be written: {e}")))?;
    out.write_all(&pcm)
        .map_err(|e| MixError(format!("PCM source could not be written: {e}")))?;
    Ok(evidence)
}

/// Derive sample windows from the existing authored transcript. Measured
/// alignment belongs to the selected source bytes, not only a confidence score.
pub fn reviewed_voice_windows(
    project: &Project,
    voice_track_id: Uuid,
) -> Result<Vec<VoiceActivityWindow>> {
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == voice_track_id)
        .ok_or_else(|| MixError("Selected source voice is absent".into()))?;
    let mut windows = project
        .audio
        .transcript
        .iter()
        .filter(|segment| segment.voice_track_id == voice_track_id)
        .map(|segment| {
            let admitted = match &segment.alignment {
                AlignmentEvidence::Manual => true,
                AlignmentEvidence::Measured {
                    source_sha256,
                    confidence_millis: Some(confidence),
                    ..
                } => source_sha256 == &track.source_sha256 && *confidence >= 800,
                _ => false,
            };
            check(
                admitted,
                "Ducking requires known intervals bound to the selected voice SHA-256",
            )?;
            Ok(VoiceActivityWindow {
                start_sample: time_to_sample(segment.start)?,
                end_exclusive: time_to_sample(segment.end)?,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    windows.sort_by_key(|window| window.start_sample);
    Ok(windows)
}

fn saved_mix_inputs(
    project: &Project,
    profile_id: Uuid,
) -> Result<(&OriginalMixRecipe, &VoiceTrack, &DeliverableProfile)> {
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == profile_id)
        .ok_or_else(|| MixError("Saved mix has no current delivery profile".into()))?;
    let recipe = project
        .production_design
        .delivery_recipes
        .get(&profile_id)
        .and_then(|recipe| recipe.original_mix.as_ref())
        .ok_or_else(|| {
            MixError("Save an original mix in the delivery recipe before production".into())
        })?;
    recipe
        .validate(&project.assets)
        .map_err(|error| MixError(error.to_string()))?;
    check(
        project.audio.mix.target_lufs.is_none()
            && project.audio.mix.target_true_peak_dbfs.is_none(),
        "Saved mix has no native loudness/true-peak certification; measurement targets remain unsupported",
    )?;
    check(
        project.audio.sfx_cues.is_empty(),
        "A semantic SFX schedule requires its verified derived bus; an original WAV cannot silently replace those cues",
    )?;
    check(
        profile.included_scene_ids.is_empty()
            || profile.included_scene_ids
                == project
                    .scenes
                    .iter()
                    .map(|scene| scene.id)
                    .collect::<Vec<_>>(),
        "Saved original mix requires the full original cut; no implicit audio trim, reorder or padding",
    )?;
    let voice_id = profile.voice_track_id.ok_or_else(|| {
        MixError("Bind one measured voice to the saved mix delivery profile".into())
    })?;
    check(
        project.audio.active_voice_track_id == Some(voice_id),
        "Saved mix delivery voice differs from the active measured voice",
    )?;
    let track = project
        .audio
        .voice_tracks
        .iter()
        .find(|track| track.id == voice_id)
        .ok_or_else(|| MixError("Saved mix voice is no longer present".into()))?;
    Ok((recipe, track, profile))
}

/// An application-owned result of the existing deterministic mixer. Private
/// fields and no deserializer prevent a WebView receipt from authorizing PCM.
/// The source voice remains a VoiceTrack; the mixed WAV never becomes one.
pub struct PreparedOriginalMix {
    plan: OriginalMixPlan,
    evidence: OriginalMixEvidence,
    wav_bytes: Vec<u8>,
    wav_sha256: String,
    audio_inputs_sha256: String,
}

impl PreparedOriginalMix {
    pub fn plan(&self) -> &OriginalMixPlan {
        &self.plan
    }

    pub fn evidence(&self) -> &OriginalMixEvidence {
        &self.evidence
    }

    pub fn wav_bytes(&self) -> &[u8] {
        &self.wav_bytes
    }

    pub fn wav_sha256(&self) -> &str {
        &self.wav_sha256
    }

    pub fn audio_inputs_sha256(&self) -> &str {
        &self.audio_inputs_sha256
    }

    pub fn verify_project(&self, project: &Project, profile_id: Uuid) -> Result<()> {
        check(
            self.plan.deliverable_profile_id == profile_id,
            "Prepared mix belongs to another output profile",
        )?;
        saved_mix_inputs(project, profile_id)?;
        self.plan.validate(project)?;
        let fingerprint = variant_dependency_fingerprint(project, profile_id)
            .map_err(|error| MixError(error.to_string()))?;
        check(
            fingerprint.audio_inputs_sha256 == self.audio_inputs_sha256
                && canonical_digest(&self.plan)? == self.evidence.source_plan_sha256
                && self.wav_bytes.len() as u64 == 44 + self.plan.sample_frames * 4
                && hex::encode(Sha256::digest(&self.wav_bytes)) == self.wav_sha256,
            "Prepared mix source, plan or exact WAV bytes changed",
        )
    }
}

/// Read the existing canonical recipe and source custody, then use the same
/// PCM DSP as audition. This has no filesystem effects and grants no release,
/// human-listening or loudness authority. Staging and native mux remain in
/// their existing owner-scoped production operations.
pub fn prepare_saved_original_mix(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    profile_id: Uuid,
) -> Result<PreparedOriginalMix> {
    let project = service
        .project(project_id)
        .map_err(|_| MixError("Saved mix source project is unavailable".into()))?;
    check(
        expected.resource == project.resource_key()
            && expected.generation == project.generation
            && expected.revision == project.revision,
        "Saved mix source revision or generation changed",
    )?;
    project
        .validate()
        .map_err(|error| MixError(error.to_string()))?;
    let (recipe, track, profile) = saved_mix_inputs(&project, profile_id)?;
    let music_id = recipe.music_asset_id.ok_or_else(|| {
        MixError("Saved original mix has no selected music asset; no invented silent source".into())
    })?;
    let sfx_id = recipe.sfx_asset_id.ok_or_else(|| {
        MixError("Saved original mix has no selected SFX asset; no invented silent source".into())
    })?;
    let decode = |asset_id| {
        service
            .decoded_stem_for_original_mix(project.id, project.revision, asset_id)
            .map_err(|_| {
                MixError("Saved mix PCM source failed current project/CAS verification".into())
            })
    };
    let voice = decode(track.asset_id)?;
    let music = decode(music_id)?;
    let sfx = decode(sfx_id)?;
    let receipt = |asset_id, pcm: &motionwright_service::OriginalMixDecodedPcm| PcmStemReceipt {
        asset_id,
        source_asset_sha256: pcm.original_asset_sha256.clone(),
        decoded_pcm_sha256: pcm.decoded_pcm_sha256.clone(),
        sample_frames: pcm.sample_frames,
    };
    let plan = OriginalMixPlan {
        schema: "motionwright.original-audio-mix/1".into(),
        project_id: project.id,
        generation: project.generation,
        project_revision: project.revision,
        deliverable_profile_id: profile.id,
        language: profile.language.clone(),
        sample_rate: 48_000,
        channels: 2,
        sample_frames: voice.sample_frames,
        measured_voice: receipt(track.asset_id, &voice),
        original_music: receipt(music_id, &music),
        original_sfx: receipt(sfx_id, &sfx),
        voice_gain_db: f64::from(project.audio.mix.voice_gain_db),
        music_gain_db: f64::from(project.audio.mix.music_gain_db),
        sfx_gain_db: recipe.sfx_gain_db,
        duck_attenuation_db: recipe.duck_attenuation_db,
        duck_attack_samples: recipe.duck_attack_samples,
        duck_release_samples: recipe.duck_release_samples,
        voice_windows: reviewed_voice_windows(&project, track.id)?,
        sample_peak_guard_dbfs: -1.0,
        source_classification: "ORIGINAL_LICENSED_SOURCE_REQUIRED_NOT_SPEECH_VERIFIED".into(),
        owner_mixing_approval_authenticated: false,
        is_mastered: false,
    };
    let mut wav_bytes = Vec::new();
    let evidence = write_mix_wav(
        &plan,
        &project,
        &voice.interleaved_s16le,
        &music.interleaved_s16le,
        &sfx.interleaved_s16le,
        &mut wav_bytes,
    )?;
    let fingerprint = variant_dependency_fingerprint(&project, profile_id)
        .map_err(|error| MixError(error.to_string()))?;
    let result = PreparedOriginalMix {
        plan,
        evidence,
        wav_sha256: hex::encode(Sha256::digest(&wav_bytes)),
        wav_bytes,
        audio_inputs_sha256: fingerprint.audio_inputs_sha256,
    };
    // All three reads and DSP may have overlapped a real edit or a generation
    // replacement. Never return an artifact from the stale source snapshot.
    let latest = service
        .project(project_id)
        .map_err(|_| MixError("Saved mix project became unavailable during preparation".into()))?;
    result.verify_project(&latest, profile_id)?;
    Ok(result)
}
