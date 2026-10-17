//! Source-bound, constant-gain mastering through the existing MLT provider.
//!
//! Planning is not readiness. A plan, a meter report or an XML file cannot
//! authorize publication. The canonical premix proof remains immutable; an
//! additional chain must join the native gain result, whole-PCM transfer,
//! physical WAV/AAC measurements and current owner receipts before export.
//! No mixer, decoder, limiter or LUFS estimator is implemented here.

use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

pub const MASTERING_SCHEMA: &str = "motionwright.native-linear-mastering/1";
pub const MEASURE_SCHEMA: &str = "motionwright.audio-measurement/1";
pub const MEASURE_METHOD: &str = "mw.ffmpeg-ebur128-f64-stereo48k/1";
pub const MEASURE_COMMAND: &str = "driver.native-readback.audio.measure";
pub const SAMPLE_RATE: u64 = 48_000;
pub const MIN_SAMPLE_FRAMES: u64 = 19_200;
pub const MAX_SAMPLE_FRAMES: u64 = 720_000;
pub const LOUDNESS_TOLERANCE_LU: f64 = 0.5;
// Engineering uncertainty allowances, fixed before observing any medium.
// They are not a calibration result or a claim of conformance to a standard.
pub const INTEGRATED_UNCERTAINTY_LU: f64 = 0.101;
pub const TRUE_PEAK_UNCERTAINTY_DB: f64 = 0.1;
// Admission reserve only, not a universal AAC overshoot guarantee. The final
// AAC still has to meet its measured ceiling including uncertainty.
pub const CODEC_ADMISSION_RESERVE_DB: f64 = 0.5;
pub const MLT_GAIN_MIN_MILLI_DB: i64 = -60_000;
pub const MLT_GAIN_MAX_MILLI_DB: i64 = 24_000;

fn invalid(message: &str) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}
fn unsupported(message: &str) -> Error {
    Error::new(ErrorCode::Unsupported, message)
}
fn conflict(message: &str) -> Error {
    Error::new(ErrorCode::Conflict, message)
}
fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
fn digest<T: Serialize>(value: &T) -> NativeResult<String> {
    Ok(hex::encode(Sha256::digest(serde_json::to_vec(value)?)))
}

/// The two predeclared engineering vectors. Saved Project targets select a
/// profile; no target, gain, tolerance or output path is accepted from UI data.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MasteringProfile {
    Speech16,
    Programme23,
}
impl MasteringProfile {
    pub const fn targets(self) -> (f64, f64) {
        match self {
            Self::Speech16 => (-16.0, -1.0),
            Self::Programme23 => (-23.0, -2.0),
        }
    }
    pub const fn id(self) -> &'static str {
        match self {
            Self::Speech16 => "mw-linear-lufs16-tp1/1",
            Self::Programme23 => "mw-linear-lufs23-tp2/1",
        }
    }

    /// Call with Project.audio.mix's actual stored fields. The legacy field
    /// target_true_peak_dbfs is interpreted as a dBTP ceiling in this profile.
    pub fn from_saved_targets(
        lufs: Option<f64>,
        true_peak: Option<f64>,
    ) -> NativeResult<Option<Self>> {
        match (lufs, true_peak) {
            (None, None) => Ok(None),
            (Some(lufs), Some(peak)) if lufs.is_finite() && peak.is_finite() => {
                [Self::Speech16, Self::Programme23]
                    .into_iter()
                    .find(|profile| profile.targets() == (lufs, peak))
                    .map(Some)
                    .ok_or_else(|| {
                        unsupported(
                            "Saved targets are outside the admitted linear mastering profiles",
                        )
                    })
            }
            _ => Err(unsupported(
                "Linear mastering requires both finite saved loudness and dBTP targets",
            )),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MasteringClock {
    pub video_frames: u64,
    pub fps_num: u32,
    pub fps_den: u32,
    pub sample_frames: u64,
}
impl MasteringClock {
    pub fn validate(&self) -> NativeResult<()> {
        if self.video_frames == 0
            || self.fps_num == 0
            || self.fps_den == 0
            || self.fps_num > 120_000
            || self.fps_den > 100_000
            || u64::from(self.fps_num) > 120 * u64::from(self.fps_den)
            || !(MIN_SAMPLE_FRAMES..=MAX_SAMPLE_FRAMES).contains(&self.sample_frames)
        {
            return Err(unsupported(
                "Mastering requires an exact 400 ms to 15 s stereo 48 kHz programme clock",
            ));
        }
        let samples =
            u128::from(self.video_frames) * u128::from(self.fps_den) * u128::from(SAMPLE_RATE);
        if samples != u128::from(self.sample_frames) * u128::from(self.fps_num) {
            return Err(unsupported(
                "Mastering cannot round or repair the source sample clock",
            ));
        }
        Ok(())
    }
}

/// Backend data only. These digests describe the existing canonical premix,
/// not an altered voice proof and not authorization supplied by a client.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MasteringBinding {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub canonical_dependency_sha256: String,
    pub source_plan_sha256: String,
    pub source_readiness_sha256: String,
    pub premix_wav_sha256: String,
    pub premix_pcm_sha256: String,
    pub clock: MasteringClock,
}
impl MasteringBinding {
    pub fn validate(&self) -> NativeResult<()> {
        if [self.project_id, self.generation, self.deliverable_id]
            .into_iter()
            .any(|id| id.is_nil())
        {
            return Err(invalid(
                "Mastering requires complete non-nil canonical identities",
            ));
        }
        for value in [
            &self.canonical_dependency_sha256,
            &self.source_plan_sha256,
            &self.source_readiness_sha256,
            &self.premix_wav_sha256,
            &self.premix_pcm_sha256,
        ] {
            if !is_sha256(value) {
                return Err(invalid(
                    "Mastering requires exact lowercase SHA-256 bindings",
                ));
            }
        }
        self.clock.validate()
    }
    pub fn sha256(&self) -> NativeResult<String> {
        self.validate()?;
        // This integer/string-only object uses sorted JSON keys in both the
        // native consumer and Host. No cross-language float hashing occurs.
        digest(&serde_json::to_value(self)?)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioMeasureRole {
    CanonicalPremix,
    MasteredWav,
    FinalAac,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioMeasurePins {
    pub worker_sha256: String,
    pub python_sha256: String,
    pub ffmpeg_sha256: String,
    pub ffprobe_sha256: String,
    pub owner_runtime_profile_sha256: String,
    /// Owner's retained meter validation against its predeclared vectors.
    /// A string is not the validation receipt; the coordinator must join it.
    pub meter_validation_sha256: String,
}
impl AudioMeasurePins {
    pub fn validate(&self) -> NativeResult<()> {
        if [
            &self.worker_sha256,
            &self.python_sha256,
            &self.ffmpeg_sha256,
            &self.ffprobe_sha256,
            &self.owner_runtime_profile_sha256,
            &self.meter_validation_sha256,
        ]
        .into_iter()
        .any(|value| !is_sha256(value))
        {
            return Err(invalid(
                "Independent audio measurement requires its complete owner-pinned method",
            ));
        }
        Ok(())
    }
}

/// Closed read-only observation, deliberately deserializable as data, never
/// as native readiness. The Host receipt and exact command binding must be
/// checked against storage before this is used to dispatch a transformation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioMeasurement {
    pub schema: String,
    pub method: String,
    pub role: AudioMeasureRole,
    pub source_binding_sha256: String,
    pub input_sha256: String,
    pub decoded_f64le_sha256: String,
    pub decoded_sample_frames: u64,
    pub logical_sample_frames: u64,
    pub declared_decoder_suffix_budget_samples: u32,
    pub sample_rate: u32,
    pub channels: u32,
    pub integrated_lufs: Option<f64>,
    /// Conservative upper value including printed linear-peak quantization
    /// and TRUE_PEAK_UNCERTAINTY_DB. This is not a PCM sample-peak value.
    pub programme_true_peak_dbtp_upper: Option<f64>,
    pub physical_true_peak_dbtp_upper: Option<f64>,
    pub integrated_uncertainty_lu: f64,
    pub true_peak_uncertainty_db: f64,
    pub programme_interval: [u64; 2],
    pub peak_flush_zero_samples: u32,
    pub complete_input_decoded: bool,
    pub pins: AudioMeasurePins,
}
impl AudioMeasurement {
    pub fn validate_for(
        &self,
        binding: &MasteringBinding,
        input_sha256: &str,
        role: AudioMeasureRole,
        expected_pins: &AudioMeasurePins,
    ) -> NativeResult<()> {
        binding.validate()?;
        expected_pins.validate()?;
        let count = binding.clock.sample_frames;
        if self.schema != MEASURE_SCHEMA
            || self.method != MEASURE_METHOD
            || self.role != role
            || self.source_binding_sha256 != binding.sha256()?
            || self.input_sha256 != input_sha256
            || !is_sha256(&self.input_sha256)
            || !is_sha256(&self.decoded_f64le_sha256)
            || self.pins != *expected_pins
            || self.sample_rate != 48_000
            || self.channels != 2
            || self.logical_sample_frames != count
            || self.programme_interval != [0, count]
            || !self.complete_input_decoded
            || self.peak_flush_zero_samples != 4_800
            || self.integrated_uncertainty_lu != INTEGRATED_UNCERTAINTY_LU
            || self.true_peak_uncertainty_db != TRUE_PEAK_UNCERTAINTY_DB
        {
            return Err(conflict(
                "Audio measurement does not match its exact source, window and pinned method",
            ));
        }
        let allowed_suffix = if role == AudioMeasureRole::FinalAac {
            1024
        } else {
            0
        };
        if self.declared_decoder_suffix_budget_samples > allowed_suffix
            || self.decoded_sample_frames < count
            || self.decoded_sample_frames - count
                > u64::from(self.declared_decoder_suffix_budget_samples)
        {
            return Err(conflict(
                "Audio measurement has an unexplained decoder suffix or changed duration",
            ));
        }
        for value in [
            self.integrated_lufs,
            self.programme_true_peak_dbtp_upper,
            self.physical_true_peak_dbtp_upper,
        ] {
            if value.is_some_and(|number| !number.is_finite()) {
                return Err(invalid(
                    "Audio measurements must contain finite values or an explicit undefined state",
                ));
            }
        }
        // Logical zero-extension and physical AAC suffix have different edge
        // conditions. Check both ceilings; do not assume their peaks are ordered.
        Ok(())
    }
}

/// Custody decision and receipt joins are provided by the backend owner.
/// Neither variant claims the generic SDK render pins an expected input SHA.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub enum MasteringSourceCustody {
    OwnerSealedReadOnly {
        owner_receipt_sha256: String,
        source_sha256: String,
    },
    SdkExpectedSha256 {
        capability_descriptor_sha256: String,
        source_sha256: String,
    },
}
impl MasteringSourceCustody {
    fn validate(&self, source_sha256: &str) -> NativeResult<()> {
        let (pin, actual) = match self {
            Self::OwnerSealedReadOnly {
                owner_receipt_sha256,
                source_sha256,
            } => (owner_receipt_sha256, source_sha256),
            Self::SdkExpectedSha256 {
                capability_descriptor_sha256,
                source_sha256,
            } => (capability_descriptor_sha256, source_sha256),
        };
        if !is_sha256(pin) || !is_sha256(actual) || actual != source_sha256 {
            return Err(conflict(
                "Mastering input custody does not pin the canonical premix bytes",
            ));
        }
        Ok(())
    }
}

/// A deterministic plan with private fields and no Deserialize. It is still
/// a plan, not an export token or a substitute for whole-medium verification.
#[derive(Debug, Clone, Serialize)]
pub struct AudioMasteringPlan {
    schema: &'static str,
    binding: MasteringBinding,
    profile: MasteringProfile,
    premix_measurement: AudioMeasurement,
    premix_measurement_receipt_sha256: String,
    custody: MasteringSourceCustody,
    gain_milli_db: i64,
    rounding: &'static str,
    predicted_peak_plus_reserve_dbtp: f64,
    loudness_tolerance_lu: f64,
    codec_admission_reserve_db: f64,
}
impl AudioMasteringPlan {
    pub fn binding(&self) -> &MasteringBinding {
        &self.binding
    }
    pub fn profile(&self) -> MasteringProfile {
        self.profile
    }
    pub fn gain_milli_db(&self) -> i64 {
        self.gain_milli_db
    }
    pub fn gain_db(&self) -> f64 {
        self.gain_milli_db as f64 / 1000.0
    }
    pub fn sha256(&self) -> NativeResult<String> {
        digest(self)
    }
    pub fn source_measurement(&self) -> &AudioMeasurement {
        &self.premix_measurement
    }
    pub fn custody(&self) -> &MasteringSourceCustody {
        &self.custody
    }
    pub fn output_name(&self) -> NativeResult<String> {
        Ok(format!("mw-audio-master-{}.wav", self.sha256()?))
    }
    pub fn source_name(&self) -> NativeResult<String> {
        Ok(format!("mw-audio-master-{}.mlt", self.sha256()?))
    }
    /// Read-only acceptance of independently measured values. Caller must
    /// separately verify the Host receipt and full PCM/clock content chain.
    pub fn verify_measured_target(
        &self,
        measurement: &AudioMeasurement,
        input_sha256: &str,
        role: AudioMeasureRole,
    ) -> NativeResult<()> {
        if role == AudioMeasureRole::CanonicalPremix {
            return Err(invalid(
                "A premix observation is not evidence of a realized master",
            ));
        }
        measurement.validate_for(
            &self.binding,
            input_sha256,
            role,
            &self.premix_measurement.pins,
        )?;
        let (target, ceiling) = self.profile.targets();
        let integrated = measurement
            .integrated_lufs
            .ok_or_else(|| unsupported("Master loudness is undefined"))?;
        let programme_peak = measurement
            .programme_true_peak_dbtp_upper
            .ok_or_else(|| unsupported("Master true peak is undefined"))?;
        let physical_peak = measurement
            .physical_true_peak_dbtp_upper
            .ok_or_else(|| unsupported("Physical master true peak is undefined"))?;
        if (integrated - target).abs() + measurement.integrated_uncertainty_lu
            > LOUDNESS_TOLERANCE_LU
        {
            return Err(conflict(
                "The complete measured master misses its saved integrated loudness target",
            ));
        }
        if programme_peak > ceiling || physical_peak > ceiling {
            return Err(conflict(
                "The measured master exceeds its saved dBTP ceiling including uncertainty",
            ));
        }
        Ok(())
    }
}

/// Data-only planning; call with backend-verified source/meter/custody joins.
/// This neither changes samples nor starts a job, and never relaxes a target.
pub fn plan_audio_mastering(
    binding: MasteringBinding,
    profile: MasteringProfile,
    premix_measurement: AudioMeasurement,
    premix_measurement_receipt_sha256: String,
    custody: MasteringSourceCustody,
) -> NativeResult<AudioMasteringPlan> {
    binding.validate()?;
    premix_measurement.validate_for(
        &binding,
        &binding.premix_wav_sha256,
        AudioMeasureRole::CanonicalPremix,
        &premix_measurement.pins,
    )?;
    custody.validate(&binding.premix_wav_sha256)?;
    if !is_sha256(&premix_measurement_receipt_sha256) {
        return Err(invalid(
            "Mastering plan requires its original measurement receipt digest",
        ));
    }
    let integrated = premix_measurement.integrated_lufs.ok_or_else(|| {
        unsupported(
            "Linear mastering cannot derive gain from undefined or silent integrated loudness",
        )
    })?;
    let physical_peak = premix_measurement
        .physical_true_peak_dbtp_upper
        .ok_or_else(|| {
            unsupported("Linear mastering requires a complete physical true-peak measurement")
        })?;
    let programme_peak = premix_measurement
        .programme_true_peak_dbtp_upper
        .ok_or_else(|| {
            unsupported("Linear mastering requires a complete programme true-peak measurement")
        })?;
    let peak = physical_peak.max(programme_peak);
    let (target, ceiling) = profile.targets();
    // Rust f64::round fixes half-way cases away from zero. Validate before
    // casting: no saturating cast or implicit gain clamp can grant admission.
    let rounded = ((target - integrated) * 1000.0).round();
    if !rounded.is_finite()
        || rounded < MLT_GAIN_MIN_MILLI_DB as f64
        || rounded > MLT_GAIN_MAX_MILLI_DB as f64
    {
        return Err(unsupported(
            "Required constant gain exceeds the native MLT profile",
        ));
    }
    let gain_milli_db = rounded as i64;
    let predicted_peak = peak + gain_milli_db as f64 / 1000.0 + CODEC_ADMISSION_RESERVE_DB;
    if predicted_peak > ceiling {
        return Err(unsupported(
            "Saved loudness and true-peak targets conflict for constant gain; dynamics is required",
        ));
    }
    Ok(AudioMasteringPlan {
        schema: MASTERING_SCHEMA,
        binding,
        profile,
        premix_measurement,
        premix_measurement_receipt_sha256,
        custody,
        gain_milli_db,
        rounding: "nearest_millidb_ties_away_from_zero",
        predicted_peak_plus_reserve_dbtp: predicted_peak,
        loudness_tolerance_lu: LOUDNESS_TOLERANCE_LU,
        codec_admission_reserve_db: CODEC_ADMISSION_RESERVE_DB,
    })
}

use super::{
    MltAudioArtifact, OriginalMixSourceRequest, ProductionCoordinator,
    read_verified_production_artifact, resolve_canonical_original_mix_plan, response_data,
    storage_error, verify_original_mix_av_source,
};
use motionwright_domain::{RevisionStamp, variant_dependency_fingerprint};
use motionwright_service::StudioService;
use serde_json::{Value, json};
use std::{path::Path, time::Duration};
use tokio::time::{Instant, sleep};

/// Reuse the canonical resolver and physical premix verifier. In particular,
/// this does not broaden the factory's current rejection of authored cues.
/// The low-level semantic-SFX proof checks the selected third bus; no fourth
/// bus is added here and no replacement mixer is introduced.
pub fn canonical_mastering_binding(
    service: &StudioService,
    project_id: Uuid,
    expected: &RevisionStamp,
    deliverable_id: Uuid,
    audio: &MltAudioArtifact,
    owner_output_root: &Path,
    clock: MasteringClock,
) -> NativeResult<(MasteringBinding, MasteringProfile)> {
    clock.validate()?;
    let project = service.project(project_id).map_err(storage_error)?;
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Mastering project stamp is no longer current",
        ));
    }
    let profile = MasteringProfile::from_saved_targets(
        project.audio.mix.target_lufs,
        project.audio.mix.target_true_peak_dbfs,
    )?
    .ok_or_else(|| unsupported("The selected Project has no saved mastering targets"))?;
    let plan = resolve_canonical_original_mix_plan(service, project_id, expected, deliverable_id)?;
    let proof = verify_original_mix_av_source(
        service,
        project_id,
        expected,
        OriginalMixSourceRequest {
            plan: &plan,
            frame_count: clock.video_frames,
            fps_num: clock.fps_num,
            fps_den: clock.fps_den,
        },
        audio,
        owner_output_root,
    )?;
    if proof.original_mix.frames != clock.sample_frames
        || proof.original_mix.sample_rate != 48_000
        || proof.original_mix.channels != 2
        || proof.original_mix.independently_verified_lufs
        || proof.original_mix.independently_verified_true_peak
    {
        return Err(conflict(
            "Canonical premix clock or original proof scope differs",
        ));
    }
    let dependency = variant_dependency_fingerprint(&project, deliverable_id)
        .map_err(|_| invalid("Mastering selected delivery dependencies are invalid"))?;
    let binding = MasteringBinding {
        project_id,
        generation: project.generation,
        revision: project.revision,
        deliverable_id,
        canonical_dependency_sha256: digest(&dependency)?,
        source_plan_sha256: proof.original_mix.source_plan_sha256.clone(),
        source_readiness_sha256: digest(&proof)?,
        premix_wav_sha256: proof.staged_wav_sha256.clone(),
        premix_pcm_sha256: proof.original_mix.rendered_pcm_sha256.clone(),
        clock,
    };
    binding.validate()?;
    let after = service.project(project_id).map_err(storage_error)?;
    let after_dependency = variant_dependency_fingerprint(&after, deliverable_id)
        .map_err(|_| invalid("Mastering selected dependencies became invalid"))?;
    if after.generation != binding.generation
        || after.revision != binding.revision
        || after_dependency != dependency
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Mastering source changed during physical preflight",
        ));
    }
    Ok((binding, profile))
}

/// Closed backend request. The owner_media_root must be the actual read-only
/// MLT `media` mount admitted by the owner, containing the sealed canonical filename.
/// A path or this request is not a mount grant. The generic SDK does not take
/// expected_sha256; the explicit custody record and post-render whole-PCM gate
/// remain necessary and this operation alone never returns native readiness.
pub struct NativeAudioMasteringRequest<'a> {
    pub request_id: &'a str,
    pub plan: &'a AudioMasteringPlan,
    pub canonical_audio: &'a MltAudioArtifact,
    pub owner_media_root: &'a Path,
    pub premix_measurement_request_id: &'a str,
}

/// Original native observations, pending physical WAV/transfer/meter joins.
/// It is not deserializable as authority and is not accepted by av.mux.
#[derive(Debug, Clone, Serialize)]
pub struct NativeAudioGainObservation {
    plan_sha256: String,
    binding: MasteringBinding,
    native_request_id: String,
    native_job_ref: String,
    native_project_revision: String,
    artifact_path: String,
    artifact_sha256: String,
    artifact_bytes: u64,
    source_path: String,
    source_sha256: String,
    source_semantics: Value,
    source_reopened: bool,
    source_media_portability: &'static str,
    native_input_sha256_enforced_by_sdk: bool,
    provider_project_cleanup: &'static str,
}
impl NativeAudioGainObservation {
    pub fn artifact_path(&self) -> &str {
        &self.artifact_path
    }
    pub fn artifact_sha256(&self) -> &str {
        &self.artifact_sha256
    }
    pub fn plan_sha256(&self) -> &str {
        &self.plan_sha256
    }
    pub fn binding(&self) -> &MasteringBinding {
        &self.binding
    }
}

#[derive(Clone)]
struct GainSession {
    project: String,
    revision: String,
}
fn native_reference(value: &Value, key: &str) -> NativeResult<String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty() && text.len() <= 80 && !text.chars().any(char::is_control))
        .map(str::to_owned)
        .ok_or_else(|| conflict("Native mastering returned an invalid current reference"))
}
fn gain_session(value: &Value) -> NativeResult<GainSession> {
    let revision = native_reference(value, "revision")?;
    if !is_sha256(&revision) {
        return Err(conflict("MLT mastering revision is malformed"));
    }
    Ok(GainSession {
        project: native_reference(value, "project")?,
        revision,
    })
}
fn created_reference(value: &Value, kind: &str) -> NativeResult<String> {
    let items = value
        .get("created_refs")
        .and_then(Value::as_array)
        .ok_or_else(|| conflict("Native mastering did not return its created reference"))?;
    let matches: Vec<_> = items
        .iter()
        .filter(|item| item.get("kind").and_then(Value::as_str) == Some(kind))
        .collect();
    if matches.len() != 1 {
        return Err(conflict("Native mastering created reference is ambiguous"));
    }
    native_reference(matches[0], "reference")
}
fn closed_page<'a>(value: &'a Value, revision: &str) -> NativeResult<&'a [Value]> {
    let items = value
        .get("items")
        .and_then(Value::as_array)
        .ok_or_else(|| conflict("Native mastering entity page is missing"))?;
    if value.get("revision").and_then(Value::as_str) != Some(revision)
        || value.get("total").and_then(Value::as_u64) != Some(items.len() as u64)
        || items.len() > 100
        || !value.get("cursor").is_some_and(Value::is_null)
    {
        return Err(conflict(
            "Native mastering requires a complete current entity page",
        ));
    }
    Ok(items)
}
fn unique_named(value: &Value, revision: &str, name: &str) -> NativeResult<String> {
    let matches: Vec<_> = closed_page(value, revision)?
        .iter()
        .filter(|item| item.get("name").and_then(Value::as_str) == Some(name))
        .collect();
    if matches.len() != 1 {
        return Err(conflict(
            "Native mastering source name is missing or ambiguous",
        ));
    }
    native_reference(matches[0], "reference")
}

/// Check the semantic asset DTO emitted by SDK54 after a confined audio probe.
/// `asset.import` takes kind=file, while `asset.list` reports the probed kind
/// audio and service avformat. This validates an observation only: callers must
/// obtain the current page from the original provider/journal, not imported JSON.
pub fn validate_mastering_asset_page(
    value: &Value,
    revision: &str,
    clock: &MasteringClock,
    premix_name: &str,
) -> NativeResult<String> {
    clock.validate()?;
    if !is_sha256(revision) {
        return Err(conflict("MLT mastering revision is malformed"));
    }
    let rows = closed_page(value, revision)?;
    let [asset] = rows else {
        return Err(conflict(
            "Mastering source requires exactly one canonical audio asset",
        ));
    };
    let expected_resource = format!("media:{premix_name}");
    if asset.get("name").and_then(Value::as_str) != Some("Canonical premix")
        || asset.get("kind").and_then(Value::as_str) != Some("audio")
        || asset.get("service").and_then(Value::as_str) != Some("avformat")
        || asset.get("frames").and_then(Value::as_u64) != Some(clock.video_frames)
        || asset.get("resource").and_then(Value::as_str) != Some(expected_resource.as_str())
        || asset.get("resource_status").and_then(Value::as_str) != Some("scoped_unopened")
        || asset.get("opaque").and_then(Value::as_bool) != Some(false)
        || !asset.get("original").is_some_and(Value::is_null)
        || !asset.get("proxy").is_some_and(Value::is_null)
    {
        return Err(conflict(
            "Mastering source asset does not retain the sealed canonical audio and clock",
        ));
    }
    native_reference(asset, "reference")?;
    native_reference(asset, "id")
}

/// A lost read after acknowledged render.start leaves the original job's
/// outcome uncertain. Preserve its typed error/candidates; never start again.
fn unconfirmed_gain(mut error: Error) -> Error {
    error.outcome_known = false;
    error.message = "The original MLT audio-gain job is unconfirmed; reconcile its original receipt without redispatch".into();
    if error.progress.is_none() {
        error = error.recipe_progress(1, "driver.mlt-video.render.start");
    }
    error
}
fn gain_job_state(value: &Value, job: &str, revision: &str, output: &str) -> NativeResult<bool> {
    let malformed = || unconfirmed_gain(conflict("MLT audio job observation does not match"));
    if value.get("job").and_then(Value::as_str) != Some(job)
        || value.get("project_revision").and_then(Value::as_str) != Some(revision)
        || value.get("profile").and_then(Value::as_str) != Some("audio-wav")
        || value.get("output").and_then(Value::as_str) != Some(output)
        || value.get("created_at_ms").and_then(Value::as_u64).is_none()
        || !value
            .get("started_at_ms")
            .is_some_and(|x| x.is_null() || x.as_u64().is_some())
        || !value.get("progress").is_some_and(Value::is_null)
        || value
            .get("cancellation_requested")
            .and_then(Value::as_bool)
            .is_none()
    {
        return Err(malformed());
    }
    match value.get("state").and_then(Value::as_str) {
        Some("queued" | "starting" | "running") => {
            if ["completed_at_ms", "error", "artifact", "media"]
                .into_iter()
                .any(|key| !value.get(key).is_some_and(Value::is_null))
            {
                return Err(malformed());
            }
            Ok(false)
        }
        Some("succeeded") => {
            if value
                .get("completed_at_ms")
                .and_then(Value::as_u64)
                .is_none()
                || !value.get("error").is_some_and(Value::is_null)
                || value.get("cancellation_requested").and_then(Value::as_bool) != Some(false)
                || !value.get("artifact").is_some_and(Value::is_object)
                || !value.get("media").is_some_and(Value::is_object)
            {
                return Err(malformed());
            }
            Ok(true)
        }
        Some(state @ ("failed" | "cancelled")) => {
            if value
                .get("completed_at_ms")
                .and_then(Value::as_u64)
                .is_none()
                || !value.get("artifact").is_some_and(Value::is_null)
                || !value.get("media").is_some_and(Value::is_null)
                || value
                    .pointer("/error/outcome_known")
                    .and_then(Value::as_bool)
                    .is_none()
            {
                return Err(malformed());
            }
            let mut reported: Error =
                serde_json::from_value(value.get("error").cloned().ok_or_else(malformed)?)
                    .map_err(|_| malformed())?;
            if !reported.outcome_known {
                return Err(unconfirmed_gain(reported));
            }
            if state == "cancelled" && reported.code != ErrorCode::Cancelled {
                return Err(malformed());
            }
            reported.message =
                "The original MLT audio-gain job has a confirmed terminal failure".into();
            Err(reported)
        }
        Some("unknown") => {
            let reported = value
                .get("error")
                .cloned()
                .and_then(|value| serde_json::from_value::<Error>(value).ok());
            Err(reported.map(unconfirmed_gain).unwrap_or_else(malformed))
        }
        _ => Err(malformed()),
    }
}

impl ProductionCoordinator {
    fn check_mastering_source(
        &self,
        expected: &RevisionStamp,
        request: &NativeAudioMasteringRequest<'_>,
    ) -> NativeResult<()> {
        let source = request.plan.binding();
        let (actual, profile) = canonical_mastering_binding(
            &self.service,
            source.project_id,
            expected,
            source.deliverable_id,
            request.canonical_audio,
            &self.client.connection().output_root,
            source.clock.clone(),
        )?;
        if actual != *source
            || profile != request.plan.profile
            || self.client.connection().resource != expected.resource
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Mastering plan no longer matches the saved source and targets",
            ));
        }
        // Generic SDK54d cannot honor an expected source SHA in render.start.
        // Do not silently convert a future explicit-pin profile to RO custody.
        if !matches!(
            &request.plan.custody,
            MasteringSourceCustody::OwnerSealedReadOnly { .. }
        ) {
            return Err(unsupported(
                "The current public MLT render requires the separately admitted sealed-read-only source profile",
            ));
        }
        request.plan.custody.validate(&source.premix_wav_sha256)?;
        let receipt = self
            .service
            .latest_production_receipt(source.project_id, request.premix_measurement_request_id)
            .map_err(storage_error)?
            .ok_or_else(|| conflict("Original premix measurement receipt is absent"))?;
        if receipt.generation != source.generation
            || receipt.revision != source.revision
            || receipt.stage != "completed"
            || receipt.command != MEASURE_COMMAND
            || digest(&receipt)? != request.plan.premix_measurement_receipt_sha256
            || receipt
                .payload
                .pointer("/result/provenance_checked")
                .and_then(Value::as_bool)
                != Some(true)
        {
            return Err(conflict(
                "Mastering requires the original completed owner-mediated measurement receipt",
            ));
        }
        let measured: AudioMeasurement = serde_json::from_value(
            receipt
                .payload
                .pointer("/result/data/report")
                .cloned()
                .ok_or_else(|| conflict("Original measurement receipt has no typed report"))?,
        )?;
        if measured != request.plan.premix_measurement {
            return Err(conflict(
                "Mastering measurement differs from its original receipt",
            ));
        }
        // Compare the actual sealed input copy to the canonical RIFF bytes.
        // Read-only mount authority is checked by the owning Host/policy glue;
        // filesystem equality by itself is never claimed to prove that grant.
        let canonical = read_verified_production_artifact(
            &self.client.connection().output_root,
            &request.canonical_audio.relative_path,
            &source.premix_wav_sha256,
            4 * 1024 * 1024,
        )?;
        let sealed = read_verified_production_artifact(
            request.owner_media_root,
            &request.canonical_audio.relative_path,
            &source.premix_wav_sha256,
            4 * 1024 * 1024,
        )?;
        if canonical != sealed {
            return Err(conflict(
                "Sealed MLT input differs from canonical premix bytes",
            ));
        }
        Ok(())
    }

    async fn mastering_edit(
        &self,
        source: &MasteringBinding,
        stamp: &RevisionStamp,
        id: &str,
        command: &str,
        session: &GainSession,
        extra: Value,
    ) -> NativeResult<(GainSession, Value)> {
        let mut args = extra
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("Mastering edit must be a closed object"))?;
        args.insert("project".into(), json!(session.project));
        args.insert("expected_revision".into(), json!(session.revision));
        let response = self
            .execute(
                source.project_id,
                stamp,
                id,
                command,
                Value::Object(args),
                true,
            )
            .await?;
        let data = response_data(&response)?.clone();
        let revision = native_reference(&data, "resulting_revision")?;
        if data.get("applied").and_then(Value::as_bool) != Some(true)
            || data.get("expected_revision").and_then(Value::as_str)
                != Some(session.revision.as_str())
            || data.get("support").and_then(Value::as_str) != Some("SAFE_ROUNDTRIP")
            || !is_sha256(&revision)
            || revision == session.revision
        {
            return Err(conflict(
                "Native mastering edit did not realize its exact expected revision",
            ));
        }
        Ok((
            GainSession {
                project: native_reference(&data, "project")?,
                revision,
            },
            data,
        ))
    }

    async fn mastering_list(
        &self,
        source: &MasteringBinding,
        stamp: &RevisionStamp,
        id: &str,
        command: &str,
        session: &GainSession,
        extra: Value,
    ) -> NativeResult<Value> {
        let mut args = extra
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("Mastering query must be a closed object"))?;
        args.insert("project".into(), json!(session.project));
        args.insert("limit".into(), json!(100));
        let response = self
            .execute(
                source.project_id,
                stamp,
                id,
                command,
                Value::Object(args),
                false,
            )
            .await?;
        let data = response_data(&response)?.clone();
        closed_page(&data, &session.revision)?;
        Ok(data)
    }

    async fn mastering_source_semantics(
        &self,
        source: &MasteringBinding,
        stamp: &RevisionStamp,
        id: &str,
        session: &GainSession,
        gain: i64,
        premix_name: &str,
    ) -> NativeResult<(String, Value)> {
        let sequences = self
            .mastering_list(
                source,
                stamp,
                &format!("{id}:seq"),
                "driver.mlt-video.sequence.list",
                session,
                json!({}),
            )
            .await?;
        let sequence = unique_named(
            &sequences,
            &session.revision,
            "Motionwright linear audio master",
        )?;
        let sequence_row = closed_page(&sequences, &session.revision)?
            .iter()
            .find(|row| row["reference"] == sequence)
            .ok_or_else(|| conflict("Mastering sequence identity disappeared"))?;
        let sequence_id = native_reference(sequence_row, "id")?;
        if sequence_row["opaque"] != false
            || sequence_row["duration"] != source.clock.video_frames
            || !sequence_row["nested_sequences"]
                .as_array()
                .is_some_and(Vec::is_empty)
        {
            return Err(conflict(
                "Mastering source sequence has unsupported nested or opaque content",
            ));
        }
        let assets = self
            .mastering_list(
                source,
                stamp,
                &format!("{id}:assets"),
                "driver.mlt-video.asset.list",
                session,
                json!({}),
            )
            .await?;
        let asset_id =
            validate_mastering_asset_page(&assets, &session.revision, &source.clock, premix_name)?;
        let tracks = self
            .mastering_list(
                source,
                stamp,
                &format!("{id}:tracks"),
                "driver.mlt-video.track.list",
                session,
                json!({"sequence":sequence}),
            )
            .await?;
        let track_rows = closed_page(&tracks, &session.revision)?;
        if track_rows.len() != 1
            || track_rows[0]["kind"] != "audio"
            || track_rows[0]["muted"] != false
            || track_rows[0]["hidden"] != false
            || track_rows[0]["opaque"] != false
            || track_rows[0]["clip_count"] != 1
            || track_rows[0]["lane_count"] != 1
        {
            return Err(conflict(
                "Mastering source track topology is not one unmuted audio lane",
            ));
        }
        let track_id = native_reference(&track_rows[0], "id")?;
        let clips = self
            .mastering_list(
                source,
                stamp,
                &format!("{id}:clips"),
                "driver.mlt-video.clip.list",
                session,
                json!({"sequence":sequence}),
            )
            .await?;
        let rows = closed_page(&clips, &session.revision)?;
        if rows.len() != 1
            || rows[0]["name"] != "Canonical premix"
            || rows[0]["asset"] != asset_id
            || rows[0]["start"] != 0
            || rows[0]["source_in"] != 0
            || rows[0]["source_out"] != source.clock.video_frames
            || rows[0]["duration"] != source.clock.video_frames
            || rows[0]["speed_num"] != 1
            || rows[0]["speed_den"] != 1
            || rows[0]["effect_count"] != 1
        {
            return Err(conflict(
                "Mastering source clip changed range, speed, content or effects",
            ));
        }
        let clip_id = native_reference(&rows[0], "id")?;
        let clip = native_reference(&rows[0], "reference")?;
        let effects = self
            .mastering_list(
                source,
                stamp,
                &format!("{id}:effects"),
                "driver.mlt-video.effect.list",
                session,
                json!({"sequence":sequence,"clip":clip}),
            )
            .await?;
        let rows = closed_page(&effects, &session.revision)?;
        if rows.len() != 1
            || rows[0]["service"] != "volume"
            || rows[0]["property"] != "level"
            || rows[0]["value_milli"] != gain
            || rows[0]["enabled"] != true
            || rows[0]["keyframe_count"] != 0
            || rows[0]["opaque"] != false
        {
            return Err(conflict(
                "Reopened mastering source does not retain its one constant native gain",
            ));
        }
        let effect_id = native_reference(&rows[0], "id")?;
        let duration = self
            .execute(
                source.project_id,
                stamp,
                &format!("{id}:duration"),
                "driver.mlt-video.sequence.duration",
                json!({"project":session.project,"sequence":sequence}),
                false,
            )
            .await?;
        let duration = response_data(&duration)?;
        if duration["frames"] != source.clock.video_frames
            || duration["fps_num"] != source.clock.fps_num
            || duration["fps_den"] != source.clock.fps_den
        {
            return Err(conflict(
                "Native audio-master source does not preserve its canonical frame clock",
            ));
        }
        Ok((
            sequence,
            json!({"track_count":1,"clip_count":1,"range":[0,source.clock.video_frames],
            "speed":[1,1],"gain_milli_db":gain,"effect_count":1,"keyframe_count":0,
            "sequence_id":sequence_id,"track_id":track_id,"clip_id":clip_id,"asset_id":asset_id,
            "effect_id":effect_id,"resource":format!("media:{premix_name}"),
            "fps_num":source.clock.fps_num,"fps_den":source.clock.fps_den}),
        ))
    }

    /// Actual native transformation, using only the existing coordinator and
    /// MLT commands. No local FFmpeg or alternate runtime is launched. The
    /// coordinator's command/provenance allowlist and owner custody remain gates.
    pub async fn realize_native_audio_mastering(
        &self,
        expected: &RevisionStamp,
        request: NativeAudioMasteringRequest<'_>,
    ) -> NativeResult<NativeAudioGainObservation> {
        if request.request_id.is_empty()
            || request.request_id.len() > 44
            || !request
                .request_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(invalid(
                "Mastering requires a bounded original request identity",
            ));
        }
        self.check_mastering_source(expected, &request)?;
        let source = request.plan.binding();
        let id = request.request_id;
        let output = request.plan.output_name()?;
        let source_name = request.plan.source_name()?;
        for name in [&output, &source_name] {
            match self
                .client
                .connection()
                .output_root
                .join(name)
                .symlink_metadata()
            {
                Ok(_) => {
                    return Err(conflict(
                        "Mastering output already exists; reconcile the original operation",
                    ));
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => {
                    return Err(Error::new(
                        ErrorCode::BackendFailed,
                        "Mastering output absence could not be established",
                    ));
                }
            }
        }
        let create = self.execute(source.project_id, expected, &format!("{id}:create"), "driver.mlt-video.project.create",
            json!({"profile":{"width":320,"height":180,"fps_num":source.clock.fps_num,"fps_den":source.clock.fps_den,
                "progressive":true,"sample_aspect_num":1,"sample_aspect_den":1,
                "display_aspect_num":16,"display_aspect_den":9,"colorspace":709,"audio_channels":2}}), true).await?;
        let mut session = gain_session(response_data(&create)?)?;
        let (next, data) = self
            .mastering_edit(
                source,
                expected,
                &format!("{id}:seq-create"),
                "driver.mlt-video.sequence.create",
                &session,
                json!({"name":"Motionwright linear audio master"}),
            )
            .await?;
        session = next;
        let sequence = created_reference(&data, "sequence")?;
        let (next, _) = self
            .mastering_edit(
                source,
                expected,
                &format!("{id}:track-create"),
                "driver.mlt-video.track.create",
                &session,
                json!({"sequence":sequence,"name":"Canonical premix","kind":"audio"}),
            )
            .await?;
        session = next;
        let (next, data) = self.mastering_edit(source, expected, &format!("{id}:import"), "driver.mlt-video.asset.import",
            &session, json!({"kind":"file","name":"Canonical premix","root":"media","path":request.canonical_audio.relative_path})).await?;
        session = next;
        let asset = created_reference(&data, "asset")?;
        let sequences = self
            .mastering_list(
                source,
                expected,
                &format!("{id}:insert-seq"),
                "driver.mlt-video.sequence.list",
                &session,
                json!({}),
            )
            .await?;
        let sequence = unique_named(
            &sequences,
            &session.revision,
            "Motionwright linear audio master",
        )?;
        let tracks = self
            .mastering_list(
                source,
                expected,
                &format!("{id}:insert-track"),
                "driver.mlt-video.track.list",
                &session,
                json!({"sequence":sequence}),
            )
            .await?;
        let track = unique_named(&tracks, &session.revision, "Canonical premix")?;
        let (next, data) = self.mastering_edit(source, expected, &format!("{id}:insert"), "driver.mlt-video.clip.insert", &session,
            json!({"sequence":sequence,"track":track,"asset":asset,"name":"Canonical premix","start":0,
                "source_in":0,"source_out":source.clock.video_frames,"ripple":false})).await?;
        session = next;
        let clip = created_reference(&data, "clip")?;
        let sequences = self
            .mastering_list(
                source,
                expected,
                &format!("{id}:gain-seq"),
                "driver.mlt-video.sequence.list",
                &session,
                json!({}),
            )
            .await?;
        let sequence = unique_named(
            &sequences,
            &session.revision,
            "Motionwright linear audio master",
        )?;
        let (next, _) = self
            .mastering_edit(
                source,
                expected,
                &format!("{id}:gain"),
                "driver.mlt-video.audio.volume.set",
                &session,
                json!({"sequence":sequence,"clip":clip,"value_milli":request.plan.gain_milli_db()}),
            )
            .await?;
        session = next;
        let (_, before) = self
            .mastering_source_semantics(
                source,
                expected,
                &format!("{id}:before-save"),
                &session,
                request.plan.gain_milli_db(),
                &request.canonical_audio.relative_path,
            )
            .await?;
        let saved = self.execute(source.project_id, expected, &format!("{id}:save"), "driver.mlt-video.project.save_as",
            json!({"project":session.project,"expected_revision":session.revision,"path":source_name,"preview":false}), true).await?;
        let saved = response_data(&saved)?;
        if saved["written"] != true
            || saved["project_revision"] != session.revision
            || saved.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
            || saved.pointer("/artifact/path").and_then(Value::as_str) != Some(source_name.as_str())
        {
            return Err(conflict(
                "MLT did not publish the exact editable mastering source",
            ));
        }
        let xml_sha = saved
            .pointer("/artifact/sha256")
            .and_then(Value::as_str)
            .filter(|value| is_sha256(value))
            .ok_or_else(|| conflict("Mastering source lacks a physical digest"))?
            .to_owned();
        read_verified_production_artifact(
            &self.client.connection().output_root,
            &source_name,
            &xml_sha,
            8 * 1024 * 1024,
        )?;
        let reopened = self
            .execute(
                source.project_id,
                expected,
                &format!("{id}:reopen"),
                "driver.mlt-video.project.open",
                json!({"root":"output","path":source_name}),
                true,
            )
            .await?;
        session = gain_session(response_data(&reopened)?)?;
        let (sequence, after) = self
            .mastering_source_semantics(
                source,
                expected,
                &format!("{id}:after-reopen"),
                &session,
                request.plan.gain_milli_db(),
                &request.canonical_audio.relative_path,
            )
            .await?;
        if after != before {
            return Err(conflict(
                "Editable mastering source changed semantics during reopen",
            ));
        }
        self.check_mastering_source(expected, &request)?;
        let render_args = json!({"project":session.project,"sequence":sequence,"profile":"audio-wav","output":output});
        let planned = self
            .execute(
                source.project_id,
                expected,
                &format!("{id}:plan"),
                "driver.mlt-video.render.plan",
                render_args.clone(),
                false,
            )
            .await?;
        let planned = response_data(&planned)?;
        if planned["runnable"] != true
            || planned["frames"] != source.clock.video_frames
            || planned["project_revision"] != session.revision
            || planned["output_root"] != "output"
            || planned["output_path"] != output
            || !planned
                .get("missing_services")
                .and_then(Value::as_array)
                .is_some_and(Vec::is_empty)
        {
            return Err(conflict(
                "MLT audio render plan differs from the closed source profile",
            ));
        }
        let mut started_args = render_args
            .as_object()
            .cloned()
            .ok_or_else(|| invalid("Closed mastering render args are absent"))?;
        started_args.insert("expected_revision".into(), json!(session.revision));
        let started = self
            .execute(
                source.project_id,
                expected,
                &format!("{id}:render-start"),
                "driver.mlt-video.render.start",
                Value::Object(started_args),
                true,
            )
            .await?;
        let started = response_data(&started).map_err(unconfirmed_gain)?;
        let job = native_reference(started, "job").map_err(unconfirmed_gain)?;
        let deadline = Instant::now() + Duration::from_secs(330);
        let mut succeeded = gain_job_state(started, &job, &session.revision, &output)?;
        let mut poll = 0_u32;
        while !succeeded {
            if Instant::now() >= deadline {
                return Err(unconfirmed_gain(Error::new(
                    ErrorCode::Timeout,
                    "Native audio-gain observation timed out",
                )));
            }
            sleep(Duration::from_millis(1000)).await;
            poll += 1;
            let status = self
                .execute(
                    source.project_id,
                    expected,
                    &format!("{id}:status:{poll}"),
                    "driver.mlt-video.render.status",
                    json!({"job":job}),
                    false,
                )
                .await
                .map_err(unconfirmed_gain)?;
            succeeded = gain_job_state(
                response_data(&status).map_err(unconfirmed_gain)?,
                &job,
                &session.revision,
                &output,
            )?;
        }
        let result = self
            .execute(
                source.project_id,
                expected,
                &format!("{id}:result"),
                "driver.mlt-video.render.result",
                json!({"job":job}),
                false,
            )
            .await
            .map_err(unconfirmed_gain)?;
        let data = response_data(&result).map_err(unconfirmed_gain)?;
        if !gain_job_state(data, &job, &session.revision, &output)? {
            return Err(unconfirmed_gain(conflict(
                "MLT final audio result was not terminal",
            )));
        }
        let artifact_sha = data
            .pointer("/artifact/sha256")
            .and_then(Value::as_str)
            .filter(|value| is_sha256(value))
            .ok_or_else(|| unconfirmed_gain(conflict("MLT audio artifact lacks a valid SHA")))?
            .to_owned();
        if data.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
            || data.pointer("/artifact/path").and_then(Value::as_str) != Some(output.as_str())
            || data.pointer("/media/audio").and_then(Value::as_bool) != Some(true)
            || data.pointer("/media/video").and_then(Value::as_bool) != Some(false)
            || data.pointer("/media/codecs") != Some(&json!(["pcm_s16le"]))
        {
            return Err(conflict(
                "MLT audio artifact is not the admitted WAV profile",
            ));
        }
        let bytes = read_verified_production_artifact(
            &self.client.connection().output_root,
            &output,
            &artifact_sha,
            4 * 1024 * 1024,
        )?;
        if data.pointer("/artifact/bytes").and_then(Value::as_u64) != Some(bytes.len() as u64) {
            return Err(conflict(
                "Physical MLT audio byte count differs from its original receipt",
            ));
        }
        self.check_mastering_source(expected, &request)?;
        Ok(NativeAudioGainObservation {
            plan_sha256: request.plan.sha256()?,
            binding: source.clone(),
            native_request_id: id.into(),
            native_job_ref: job,
            native_project_revision: session.revision,
            artifact_path: output,
            artifact_sha256: artifact_sha,
            artifact_bytes: bytes.len() as u64,
            source_path: source_name,
            source_sha256: xml_sha,
            source_semantics: after,
            source_reopened: true,
            source_media_portability: "same_authorized_mounts_reopened; portable_bundle_and_gain_edit_pending",
            native_input_sha256_enforced_by_sdk: false,
            provider_project_cleanup: "owner_consent_required_for_project_close; no_automatic_destructive_cleanup",
        })
    }
}

/// Native observation retained in the application's original receipt journal.
/// The receipt digest is not an owner approval or a WebView boolean.
#[derive(Debug, Clone, Serialize)]
pub struct NativeAudioMeasurementObservation {
    pub report: AudioMeasurement,
    pub original_receipt_sha256: String,
    pub original_request_id: String,
    pub raw_observation: Value,
}

/// Verified physical WAV stage only. A later final AAC reader, final meter,
/// current dependency/authority joins and human acceptance remain separate.
#[derive(Debug, Clone, Serialize)]
pub struct MasteredAudioReadiness {
    plan_sha256: String,
    binding: MasteringBinding,
    audio: MltAudioArtifact,
    actual_pcm_sha256: String,
    native_gain: NativeAudioGainObservation,
    measurement: NativeAudioMeasurementObservation,
    scope: &'static str,
}
impl MasteredAudioReadiness {
    pub fn audio(&self) -> &MltAudioArtifact {
        &self.audio
    }
    pub fn binding(&self) -> &MasteringBinding {
        &self.binding
    }
    pub fn plan_sha256(&self) -> &str {
        &self.plan_sha256
    }
}

struct MeasurementDispatch<'a> {
    request_id: &'a str,
    binding: &'a MasteringBinding,
    role: AudioMeasureRole,
    input_sha256: &'a str,
    plan: Option<&'a AudioMasteringPlan>,
    native_mux_request_id: Option<&'a str>,
    pins: &'a AudioMeasurePins,
}

impl ProductionCoordinator {
    async fn dispatch_audio_measurement(
        &self,
        expected: &RevisionStamp,
        dispatch: MeasurementDispatch<'_>,
    ) -> NativeResult<NativeAudioMeasurementObservation> {
        let MeasurementDispatch {
            request_id,
            binding,
            role,
            input_sha256,
            plan,
            native_mux_request_id,
            pins,
        } = dispatch;
        binding.validate()?;
        pins.validate()?;
        let project = self
            .service
            .project(binding.project_id)
            .map_err(storage_error)?;
        if project.stamp() != *expected
            || expected.generation != binding.generation
            || expected.revision != binding.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio measurement source is no longer current",
            ));
        }
        let profile = project
            .deliverables
            .iter()
            .find(|item| item.id == binding.deliverable_id)
            .ok_or_else(|| invalid("Audio measurement delivery profile is missing"))?;
        let response = self.execute(binding.project_id, expected, request_id, MEASURE_COMMAND,
            json!({"schema":"motionwright.audio-measurement-request/1","binding":binding,
                "source_binding_sha256":binding.sha256()?,"role":role,"input_sha256":input_sha256,
                "mastering_plan_sha256":plan.map(AudioMasteringPlan::sha256).transpose()?,
                "gain_milli_db":plan.map(AudioMasteringPlan::gain_milli_db),"native_request_id":native_mux_request_id,
                "width":profile.width,"height":profile.height,"pins":pins}), false).await?;
        let data = response_data(&response)?;
        if data["schema"] != "motionwright.host-audio-measurement/1"
            || data["status"] != "MEASURED"
            || data["worker_sha256"] != pins.worker_sha256
            || data["scope"]
                != "technical_measurement_only_no_export_listening_rights_or_editable_source_approval"
        {
            return Err(conflict(
                "Audio meter returned an unexpected observation contract",
            ));
        }
        let report: AudioMeasurement = serde_json::from_value(
            data.get("report")
                .cloned()
                .ok_or_else(|| conflict("Audio meter report is absent"))?,
        )?;
        report.validate_for(binding, input_sha256, role, pins)?;
        let receipt = self
            .service
            .latest_production_receipt(binding.project_id, request_id)
            .map_err(storage_error)?
            .ok_or_else(|| conflict("Completed audio measurement has no retained receipt"))?;
        if receipt.generation != binding.generation
            || receipt.revision != binding.revision
            || receipt.command != MEASURE_COMMAND
            || receipt.stage != "completed"
            || receipt.payload.pointer("/result/data") != Some(data)
        {
            return Err(conflict(
                "Audio meter receipt does not bind the completed original observation",
            ));
        }
        let after = self
            .service
            .project(binding.project_id)
            .map_err(storage_error)?;
        if after.stamp() != *expected
            || digest(
                &variant_dependency_fingerprint(&after, binding.deliverable_id)
                    .map_err(|_| invalid("Audio meter current dependencies became invalid"))?,
            )? != binding.canonical_dependency_sha256
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio source changed while it was being measured",
            ));
        }
        Ok(NativeAudioMeasurementObservation {
            report,
            original_receipt_sha256: digest(&receipt)?,
            original_request_id: request_id.into(),
            raw_observation: data.clone(),
        })
    }

    /// Read-only Host measurement of the already prepared canonical source.
    /// No MLT graph or render is started by this method.
    pub async fn measure_native_mastering_premix(
        &self,
        expected: &RevisionStamp,
        request_id: &str,
        binding: &MasteringBinding,
        canonical_audio: &MltAudioArtifact,
        pins: &AudioMeasurePins,
    ) -> NativeResult<NativeAudioMeasurementObservation> {
        let (actual, _) = canonical_mastering_binding(
            &self.service,
            binding.project_id,
            expected,
            binding.deliverable_id,
            canonical_audio,
            &self.client.connection().output_root,
            binding.clock.clone(),
        )?;
        if actual != *binding {
            return Err(conflict(
                "Premix measurement must use the current canonical bytes",
            ));
        }
        self.dispatch_audio_measurement(
            expected,
            MeasurementDispatch {
                request_id,
                binding,
                role: AudioMeasureRole::CanonicalPremix,
                input_sha256: &binding.premix_wav_sha256,
                plan: None,
                native_mux_request_id: None,
                pins,
            },
        )
        .await
    }

    /// Verify the actual gain WAV and all scalar-transfer samples with the
    /// frozen reader, then measure that physical WAV before admitting it to
    /// the later mux join. No second mix or caller report is accepted here.
    pub async fn verify_native_mastered_audio(
        &self,
        expected: &RevisionStamp,
        request_id: &str,
        plan: &AudioMasteringPlan,
        native: NativeAudioGainObservation,
        canonical_audio: &MltAudioArtifact,
    ) -> NativeResult<MasteredAudioReadiness> {
        if native.plan_sha256 != plan.sha256()?
            || native.binding != plan.binding
            || native.artifact_path != plan.output_name()?
        {
            return Err(conflict(
                "Mastered WAV observation belongs to another gain plan",
            ));
        }
        let (binding, profile) = canonical_mastering_binding(
            &self.service,
            plan.binding.project_id,
            expected,
            plan.binding.deliverable_id,
            canonical_audio,
            &self.client.connection().output_root,
            plan.binding.clock.clone(),
        )?;
        if binding != plan.binding || profile != plan.profile {
            return Err(conflict("Saved mastering source or targets changed"));
        }
        read_verified_production_artifact(
            &self.client.connection().output_root,
            &native.artifact_path,
            &native.artifact_sha256,
            4 * 1024 * 1024,
        )?;
        let measured = self
            .dispatch_audio_measurement(
                expected,
                MeasurementDispatch {
                    request_id,
                    binding: &binding,
                    role: AudioMeasureRole::MasteredWav,
                    input_sha256: &native.artifact_sha256,
                    plan: Some(plan),
                    native_mux_request_id: None,
                    pins: &plan.premix_measurement.pins,
                },
            )
            .await?;
        plan.verify_measured_target(
            &measured.report,
            &native.artifact_sha256,
            AudioMeasureRole::MasteredWav,
        )?;
        let transfer = &measured.raw_observation["transfer"];
        let pcm_sha = transfer["actual_pcm_sha256"]
            .as_str()
            .filter(|value| is_sha256(value))
            .ok_or_else(|| conflict("Mastered WAV has no complete physical PCM transfer proof"))?
            .to_owned();
        if transfer["all_samples_compared"] != true
            || transfer["gain_milli_db"] != plan.gain_milli_db
            || transfer["premix_pcm_sha256"] != binding.premix_pcm_sha256
            || transfer["reference_kind"] != "analytic_scalar_expectation_not_producer_media"
            || transfer["content"]["logical_interval"] != json!([0, binding.clock.sample_frames])
            || transfer["content"]["decoded_pcm_sha256"] != pcm_sha
        {
            return Err(conflict(
                "Mastered WAV transfer does not join the same complete premix and scalar plan",
            ));
        }
        Ok(MasteredAudioReadiness {
            plan_sha256: plan.sha256()?,
            binding,
            audio: MltAudioArtifact {
                relative_path: native.artifact_path.clone(),
                sha256: native.artifact_sha256.clone(),
                sample_rate: 48000,
                channels: 2,
            },
            actual_pcm_sha256: pcm_sha,
            native_gain: native,
            measurement: measured,
            scope: "physical_native_gain_wav_and_targets_only; final_aac_readback_authority_portability_and_human_acceptance_pending",
        })
    }

    /// Inspect an already completed original mux, never assemble it again.
    /// The existing whole-AAC comparator is a separate compulsory final gate;
    /// success here alone cannot promote the delivery registry.
    pub async fn measure_native_mastered_final_aac(
        &self,
        expected: &RevisionStamp,
        inspection_request_id: &str,
        original_mux_request_id: &str,
        plan: &AudioMasteringPlan,
        ready: &MasteredAudioReadiness,
    ) -> NativeResult<NativeAudioMeasurementObservation> {
        if ready.plan_sha256 != plan.sha256()? || ready.binding != plan.binding {
            return Err(conflict(
                "Final AAC measurement lacks the matching physical mastered-WAV stage",
            ));
        }
        let mux_id = format!("{original_mux_request_id}:mlt:multisegment-av-mux");
        let receipt = self
            .service
            .latest_production_receipt(ready.binding.project_id, &mux_id)
            .map_err(storage_error)?
            .ok_or_else(|| conflict("The original final mux receipt is absent"))?;
        if receipt.generation != ready.binding.generation
            || receipt.revision != ready.binding.revision
            || receipt.stage != "completed"
            || receipt.command != "driver.mlt-video.av.mux"
            || receipt
                .payload
                .pointer("/result/provenance_checked")
                .and_then(Value::as_bool)
                != Some(true)
        {
            return Err(conflict(
                "Final AAC measurement requires the original completed native mux",
            ));
        }
        let mux = receipt
            .payload
            .pointer("/result/data")
            .ok_or_else(|| conflict("Original mux has no native data"))?;
        let token = hex::encode(Sha256::digest(original_mux_request_id.as_bytes()));
        let path = format!(
            "mw-{}-r{}-{}-multisegment-master.mp4",
            ready.binding.project_id.simple(),
            ready.binding.revision,
            &token[..16]
        );
        let hash = mux
            .pointer("/artifact/sha256")
            .and_then(Value::as_str)
            .filter(|value| is_sha256(value))
            .ok_or_else(|| conflict("Original master has no valid physical SHA-256"))?;
        if mux["audio_sha256"] != ready.audio.sha256
            || mux["profile"] != "h264-aac-mp4"
            || mux.pointer("/artifact/root").and_then(Value::as_str) != Some("output")
            || mux.pointer("/artifact/path").and_then(Value::as_str) != Some(path.as_str())
            || mux["frame_count"] != ready.binding.clock.video_frames
            || mux["fps_num"] != ready.binding.clock.fps_num
            || mux["fps_den"] != ready.binding.clock.fps_den
            || mux["sample_rate"] != 48000
            || mux["channels"] != 2
            || mux["audio_sample_frames"] != ready.binding.clock.sample_frames
        {
            return Err(conflict(
                "Final mux does not bind the same mastered WAV and canonical clock",
            ));
        }
        read_verified_production_artifact(
            &self.client.connection().output_root,
            &path,
            hash,
            512 * 1024 * 1024,
        )?;
        let observation = self
            .dispatch_audio_measurement(
                expected,
                MeasurementDispatch {
                    request_id: inspection_request_id,
                    binding: &ready.binding,
                    role: AudioMeasureRole::FinalAac,
                    input_sha256: hash,
                    plan: Some(plan),
                    native_mux_request_id: Some(original_mux_request_id),
                    pins: &plan.premix_measurement.pins,
                },
            )
            .await?;
        plan.verify_measured_target(&observation.report, hash, AudioMeasureRole::FinalAac)?;
        Ok(observation)
    }
}

#[cfg(test)]
mod lifecycle_tests {
    use super::*;

    // Authored protocol values only: these tests do not create native receipts.
    fn job(state: &str) -> Value {
        json!({"job":"original-job","project_revision":"a".repeat(64),"profile":"audio-wav",
            "output":"original.wav","state":state,"progress":null,"created_at_ms":1,
            "started_at_ms":null,"completed_at_ms":null,"artifact":null,"media":null,
            "error":null,"cancellation_requested":false})
    }
    fn observe(value: &Value) -> NativeResult<bool> {
        gain_job_state(value, "original-job", &"a".repeat(64), "original.wav")
    }

    #[test]
    fn only_a_complete_bound_terminal_observation_finishes_the_original_job() {
        for state in ["queued", "starting", "running"] {
            assert!(!observe(&job(state)).unwrap());
        }
        let mut value = job("succeeded");
        assert!(!observe(&value).unwrap_err().outcome_known);
        value["started_at_ms"] = json!(2);
        value["completed_at_ms"] = json!(3);
        value["artifact"] = json!({"root":"output","path":"original.wav"});
        value["media"] = json!({"audio":true});
        assert!(observe(&value).unwrap());
        value["cancellation_requested"] = json!(true);
        assert!(!observe(&value).unwrap_err().outcome_known);
    }

    #[test]
    fn foreign_job_revision_profile_output_or_malformed_state_is_unconfirmed() {
        for key in [
            "job",
            "project_revision",
            "profile",
            "output",
            "created_at_ms",
            "progress",
            "cancellation_requested",
        ] {
            let mut value = job("running");
            value[key] = json!("foreign");
            let error = observe(&value).unwrap_err();
            assert!(!error.outcome_known, "{key}");
            assert_eq!(error.code, ErrorCode::Conflict);
        }
        let mut value = job("running");
        value["artifact"] = json!({});
        assert!(!observe(&value).unwrap_err().outcome_known);
    }

    #[test]
    fn native_unknown_keeps_original_typed_error_and_progress() {
        let mut original = Error::new(ErrorCode::Timeout, "original diagnostic")
            .recipe_progress(2, "original-stage");
        original.outcome_known = false;
        original.candidates.push("original-reference".into());
        let progress = serde_json::to_value(&original.progress).unwrap();
        let mut value = job("unknown");
        value["completed_at_ms"] = json!(3);
        value["error"] = serde_json::to_value(original).unwrap();
        let observed = observe(&value).unwrap_err();
        assert_eq!(observed.code, ErrorCode::Timeout);
        assert!(!observed.outcome_known);
        assert_eq!(observed.candidates, vec!["original-reference"]);
        assert_eq!(serde_json::to_value(&observed.progress).unwrap(), progress);
    }

    #[test]
    fn terminal_failure_needs_explicit_outcome_and_retains_its_code() {
        let mut value = job("failed");
        value["completed_at_ms"] = json!(3);
        value["error"] =
            serde_json::to_value(Error::new(ErrorCode::BackendFailed, "original")).unwrap();
        let error = observe(&value).unwrap_err();
        assert!(error.outcome_known);
        assert_eq!(error.code, ErrorCode::BackendFailed);
        value["error"]
            .as_object_mut()
            .unwrap()
            .remove("outcome_known");
        assert!(!observe(&value).unwrap_err().outcome_known);
        value["state"] = json!("cancelled");
        value["error"] =
            serde_json::to_value(Error::new(ErrorCode::BackendFailed, "not cancellation")).unwrap();
        assert!(!observe(&value).unwrap_err().outcome_known);
        value["error"] =
            serde_json::to_value(Error::new(ErrorCode::Cancelled, "original cancellation"))
                .unwrap();
        assert!(observe(&value).unwrap_err().outcome_known);
    }

    #[test]
    fn stale_partial_and_ambiguous_native_pages_cannot_define_reopened_source() {
        let revision = "a".repeat(64);
        let mut page = json!({"revision":revision,"items":[{"name":"Canonical premix","reference":"r1"}],"total":1,"cursor":null});
        assert_eq!(
            unique_named(&page, &revision, "Canonical premix").unwrap(),
            "r1"
        );
        page["cursor"] = json!("next");
        assert!(closed_page(&page, &revision).is_err());
        page["cursor"] = Value::Null;
        page["total"] = json!(2);
        assert!(closed_page(&page, &revision).is_err());
        page["items"]
            .as_array_mut()
            .unwrap()
            .push(json!({"name":"Canonical premix","reference":"r2"}));
        assert!(unique_named(&page, &revision, "Canonical premix").is_err());
        page["revision"] = json!("b".repeat(64));
        assert!(closed_page(&page, &revision).is_err());
    }
}
