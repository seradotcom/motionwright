//! Source-bound measurements through Semwright's existing offline audio meter.
//!
//! The only producer here is ProductionCoordinator. Measurements are opt-in,
//! read-only calls; they do not normalize audio or grant mastering. Explicit
//! profile targets use QC/1 against the final decoded PCM16 measurement only.
//! Opaque proofs are rebuilt from current source bytes and completed
//! local receipts, never accepted from a WebView JSON object.

use super::{
    MLT_MASTER_MAX_BYTES, MltAudioArtifact, MltAvMasterEvidence, MltAvMasterRequest,
    MltMultisegmentAvMasterEvidence, MltMultisegmentAvMasterRequest, ProductionCoordinator,
    ProductionReceipt, backend, invalid, is_link_or_reparse, is_sha256, request_digest,
    safe_metadata, storage_error, verify_output_artifact,
};
use crate::original_mix::{PreparedOriginalMix, prepare_saved_original_mix_with_budget};
use motionwright_domain::{DeliveryAudioTargets, Project, RevisionStamp};
use motionwright_service::OriginalMixDecodeBudget;
use semwright_audio_domain::{analysis::LoudnessAnalysis, signal_analysis::SignalStatistics};
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult, Value, json,
    types::{Execution, SourceKind},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    path::Path,
};
use uuid::Uuid;

const MEASURE_COMMAND: &str = "driver.audio-analysis.artifact.measure";
const MUX_COMMAND: &str = "driver.mlt-video.av.mux";
const MAX_ANALYSIS_BYTES: u64 = 512 * 1024 * 1024;

mod qc_report;
mod receipt_history;
pub use qc_report::VerifiedDeliveryAudioQcReport;

/// Bounded discovery references only. These fields cannot recreate a proof,
/// grant a native effect, or authorize delivery of an artifact.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AudioQcReceiptHint {
    pub decoded_measurement_receipt_id: Uuid,
    pub master_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AudioQcReceiptInventory {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub items: Vec<AudioQcReceiptHint>,
    /// Current-stamp decoded observations without original selector metadata.
    /// Their delivery profile cannot be inferred.
    pub legacy_unbound_count: u64,
    pub unavailable_count: u64,
}

/// Selects an existing producer; this is not a new renderer or codec profile.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum AudioAnalysisLane {
    SingleSegment,
    Multisegment,
}

impl AudioAnalysisLane {
    fn mux_suffix(self) -> &'static str {
        match self {
            Self::SingleSegment => ":mlt:av-mux",
            Self::Multisegment => ":mlt:multisegment-av-mux",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum MeasurementPhase {
    PreparedOriginalMix,
    PreparedMeasuredVoice,
    DecodedMltMaster,
}

struct SourceMeasureRequest<'a> {
    request_id: &'a str,
    deliverable_id: Uuid,
    lane: AudioAnalysisLane,
    audio: &'a MltAudioArtifact,
    video_frames: u64,
    fps_num: u32,
    fps_den: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct MeasuredVoiceSource {
    voice_track_id: Uuid,
    asset_id: Uuid,
    decoded_pcm_sha256: String,
    source_cut_sha256: String,
}

/// Reuses the existing saved-mix factory or original imported voice bytes.
/// No silent music/SFX asset or alternate media decoder is introduced.
enum PreparedDeliveryAudio {
    OriginalMix(Box<PreparedOriginalMix>),
    MeasuredVoice(Vec<u8>),
}

impl PreparedDeliveryAudio {
    fn wav_bytes(&self) -> &[u8] {
        match self {
            Self::OriginalMix(mix) => mix.wav_bytes(),
            Self::MeasuredVoice(bytes) => bytes,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct SourceBinding {
    schema: &'static str,
    caller_request_id: String,
    lane: AudioAnalysisLane,
    project_id: Uuid,
    project_resource: String,
    generation: Uuid,
    revision: u64,
    deliverable_id: Uuid,
    connection_identity: String,
    audio_inputs_sha256: String,
    source_plan_sha256: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    measured_voice_source: Option<MeasuredVoiceSource>,
    wav_sha256: String,
    wav_bytes: u64,
    sample_frames: u64,
    sample_rate: u32,
    channels: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct ArtifactIdentity {
    relative_path: String,
    sha256: String,
    bytes: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct NativeParent {
    receipt_id: Uuid,
    request_id: String,
    request_sha256: String,
    result_sha256: String,
    master: ArtifactIdentity,
    decoded: ArtifactIdentity,
    decoded_sample_frames: u64,
    source_audio_sha256: String,
    source_video_sha256: String,
    video_frames: u64,
    fps_num: u32,
    fps_den: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct MeasurementContext {
    schema: &'static str,
    source: SourceBinding,
    phase: MeasurementPhase,
    native_parent: Option<NativeParent>,
}

impl MeasurementContext {
    fn file_name(&self) -> NativeResult<String> {
        let phase = match self.phase {
            MeasurementPhase::PreparedOriginalMix => "source",
            MeasurementPhase::PreparedMeasuredVoice => "voice-source",
            MeasurementPhase::DecodedMltMaster => "decoded",
        };
        Ok(format!("mw-analysis-{phase}-{}.wav", digest(self)?))
    }

    fn input(&self) -> NativeResult<(&str, u64, u64)> {
        match (&self.phase, &self.native_parent) {
            (
                MeasurementPhase::PreparedOriginalMix | MeasurementPhase::PreparedMeasuredVoice,
                None,
            ) => Ok((
                &self.source.wav_sha256,
                self.source.wav_bytes,
                self.source.sample_frames,
            )),
            (MeasurementPhase::DecodedMltMaster, Some(parent)) => Ok((
                &parent.decoded.sha256,
                parent.decoded.bytes,
                parent.decoded_sample_frames,
            )),
            _ => Err(backend(
                "Audio measurement phase has inconsistent native custody",
            )),
        }
    }

    fn args(&self) -> NativeResult<Value> {
        Ok(json!({
            "file_name": self.file_name()?,
            "expected_sha256": self.input()?.0,
            "layout": "stereo",
        }))
    }

    fn request_id(&self) -> NativeResult<String> {
        let phase = match self.phase {
            MeasurementPhase::PreparedOriginalMix => "source",
            MeasurementPhase::PreparedMeasuredVoice => "voice-source",
            MeasurementPhase::DecodedMltMaster => "decoded",
        };
        Ok(format!("{}:measure-{phase}", self.source.namespace()?))
    }
}

/// Private durable selector metadata. It is not a measurement or capability.
/// The only producer derives it from the original typed measurement context.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct AudioReceiptContext {
    schema: String,
    caller_request_id: String,
    deliverable_id: Uuid,
    lane: String,
    measurement_context_sha256: String,
}

impl AudioReceiptContext {
    fn from_context(context: &MeasurementContext) -> NativeResult<Self> {
        context.input()?;
        bounded_request_id(&context.source.caller_request_id)?;
        Ok(Self {
            schema: "motionwright.audio-receipt-context/1".into(),
            caller_request_id: context.source.caller_request_id.clone(),
            deliverable_id: context.source.deliverable_id,
            lane: match context.source.lane {
                AudioAnalysisLane::SingleSegment => "single_segment",
                AudioAnalysisLane::Multisegment => "multisegment",
            }
            .into(),
            measurement_context_sha256: digest(context)?,
        })
    }

    fn lane(&self) -> NativeResult<AudioAnalysisLane> {
        match self.lane.as_str() {
            "single_segment" => Ok(AudioAnalysisLane::SingleSegment),
            "multisegment" => Ok(AudioAnalysisLane::Multisegment),
            _ => Err(stale("Audio receipt selector has an unknown producer lane")),
        }
    }

    fn from_receipt(receipt: &ProductionReceipt) -> NativeResult<Option<Self>> {
        let Some(value) = receipt.payload.get("audio_reopen") else {
            return Ok(None);
        };
        let object = value
            .as_object()
            .ok_or_else(|| stale("Audio receipt selector metadata is not an object"))?;
        // Bound the private object before allocating typed strings. A present
        // null/invalid object is contradictory evidence, never legacy absence.
        if object.len() != 5
            || object
                .values()
                .any(|value| value.as_str().is_none_or(|text| text.len() > 96))
        {
            return Err(stale(
                "Audio receipt selector metadata exceeds its closed bounds",
            ));
        }
        let context = Self::deserialize(value)
            .map_err(|_| stale("Audio receipt selector metadata has invalid fields"))?;
        if context.schema != "motionwright.audio-receipt-context/1"
            || bounded_request_id(&context.caller_request_id).is_err()
            || !is_sha256(&context.measurement_context_sha256)
        {
            return Err(stale("Audio receipt selector metadata is invalid"));
        }
        context.lane()?;
        Ok(Some(context))
    }

    // Only primitive fields from the validated private type are serialized.
    // This is infallible and is prepared before the coordinator reserves custody.
    pub(super) fn payload(&self) -> Value {
        json!({
            "schema": self.schema,
            "caller_request_id": self.caller_request_id,
            "deliverable_id": self.deliverable_id,
            "lane": self.lane,
            "measurement_context_sha256": self.measurement_context_sha256,
        })
    }
}

impl SourceBinding {
    fn namespace(&self) -> NativeResult<String> {
        let bytes = serde_json::to_vec(self)
            .map_err(|_| invalid("Audio source context cannot be encoded"))?;
        // The existing multisegment route allows at most 54 ASCII token chars.
        // Base32 carries every SHA-256 bit in 52 chars; do not truncate authority.
        let hash = Sha256::digest(bytes);
        let alphabet = b"abcdefghijklmnopqrstuvwxyz234567";
        let mut token = String::from("ma");
        let mut accumulator = 0u16;
        let mut bits = 0u8;
        for byte in hash {
            accumulator = (accumulator << 8) | u16::from(byte);
            bits += 8;
            while bits >= 5 {
                bits -= 5;
                token.push(char::from(
                    alphabet[usize::from((accumulator >> bits) & 31)],
                ));
            }
            accumulator &= (1u16 << bits) - 1;
        }
        if bits != 0 {
            token.push(char::from(
                alphabet[usize::from((accumulator << (5 - bits)) & 31)],
            ));
        }
        Ok(token)
    }

    fn stamp(&self) -> RevisionStamp {
        RevisionStamp {
            resource: self.project_resource.clone(),
            generation: self.generation,
            revision: self.revision,
        }
    }

    fn source_context(&self) -> MeasurementContext {
        MeasurementContext {
            schema: "motionwright.audio-measurement-context/1",
            source: self.clone(),
            phase: if self.measured_voice_source.is_some() {
                MeasurementPhase::PreparedMeasuredVoice
            } else {
                MeasurementPhase::PreparedOriginalMix
            },
            native_parent: None,
        }
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AnalysisArtifact {
    file_name: String,
    sha256: String,
    bytes: u64,
    format: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AnalysisResponse {
    artifact: AnalysisArtifact,
    loudness: LoudnessAnalysis,
    pcm_statistics: Option<SignalStatistics>,
    exhaustive: bool,
}

#[derive(Debug, Clone, Serialize)]
struct MeasurementReceipt {
    local_id: Uuid,
    request_id: String,
    request_sha256: String,
    broker_request_id: String,
    provenance: Value,
}

/// App-owned proof of exactly one completed, source-bound measurement.
/// No public fields, constructor, or deserializer can grant this authority.
#[derive(Debug, Clone, Serialize)]
pub struct VerifiedAudioMeasurement {
    schema: &'static str,
    context: MeasurementContext,
    receipt: MeasurementReceipt,
    loudness: LoudnessAnalysis,
    pcm_statistics: SignalStatistics,
    exhaustive: bool,
}

impl VerifiedAudioMeasurement {
    pub fn loudness(&self) -> &LoudnessAnalysis {
        &self.loudness
    }

    pub fn pcm_statistics(&self) -> &SignalStatistics {
        &self.pcm_statistics
    }
}

/// A numeric check cannot grant intelligibility, listening or release approval.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryAudioQcStatus {
    NotRequested,
    WithinTargets,
    OutsideTargets,
    Unavailable,
}

/// QC/1 consumes only the authoritative integer-milli public meter fields.
/// All fields are private and there is no Deserialize or public constructor.
#[derive(Debug, Clone, Serialize)]
pub struct VerifiedDeliveryAudioQc {
    schema: &'static str,
    status: DeliveryAudioQcStatus,
    targets: Option<DeliveryAudioTargets>,
    target_lufs_milli: Option<i32>,
    target_true_peak_millidbtp: Option<i32>,
    measured_integrated_lufs_milli: Option<i32>,
    measured_true_peak_millidbtp: Option<i32>,
    lufs_delta_milli: Option<i64>,
    lufs_tolerance_milli: i32,
    lufs_status: DeliveryAudioQcStatus,
    true_peak_status: DeliveryAudioQcStatus,
    evaluated_phase: MeasurementPhase,
    decoded_wav_sha256: String,
    decoded_sample_frames: u64,
    measurement_receipt_id: Uuid,
    measurement_proof_sha256: String,
    evidence_scope: &'static str,
}

impl VerifiedDeliveryAudioQc {
    pub fn status(&self) -> DeliveryAudioQcStatus {
        self.status
    }
    pub fn lufs_status(&self) -> DeliveryAudioQcStatus {
        self.lufs_status
    }
    pub fn true_peak_status(&self) -> DeliveryAudioQcStatus {
        self.true_peak_status
    }
    pub fn targets(&self) -> Option<&DeliveryAudioTargets> {
        self.targets.as_ref()
    }
}

fn evaluate_delivery_audio_qc(
    targets: Option<&DeliveryAudioTargets>,
    decoded: &VerifiedAudioMeasurement,
) -> NativeResult<VerifiedDeliveryAudioQc> {
    use DeliveryAudioQcStatus::{NotRequested, OutsideTargets, Unavailable, WithinTargets};
    if decoded.context.phase != MeasurementPhase::DecodedMltMaster
        || decoded.context.native_parent.is_none()
    {
        return Err(stale(
            "Delivery audio QC requires the mux-associated final decoded PCM16 measurement",
        ));
    }
    let (lufs_target, peak_target) = targets
        .map(DeliveryAudioTargets::validated_milli_targets)
        .transpose()
        .map_err(|error| invalid(error.to_string()))?
        .unwrap_or((None, None));
    let loudness = &decoded.loudness;
    let lufs_delta = lufs_target
        .zip(loudness.integrated_lufs_milli)
        .map(|(target, actual)| i64::from(actual) - i64::from(target));
    let lufs_status = match (lufs_target, lufs_delta) {
        (None, _) => NotRequested,
        (Some(_), None) => Unavailable,
        (Some(_), Some(delta)) if delta.abs() <= 200 => WithinTargets,
        (Some(_), Some(_)) => OutsideTargets,
    };
    let peak_status = match (peak_target, loudness.true_peak_millidbtp) {
        (None, _) => NotRequested,
        (Some(_), None) => Unavailable,
        (Some(target), Some(actual)) if actual <= target => WithinTargets,
        (Some(_), Some(_)) => OutsideTargets,
    };
    let status = if targets.is_none() {
        NotRequested
    } else if [lufs_status, peak_status].contains(&Unavailable) {
        Unavailable
    } else if [lufs_status, peak_status].contains(&OutsideTargets) {
        OutsideTargets
    } else {
        WithinTargets
    };
    let (sha256, _, frames) = decoded.context.input()?;
    Ok(VerifiedDeliveryAudioQc {
        schema: "motionwright.delivery-audio-qc/1",
        status,
        targets: targets.cloned(),
        target_lufs_milli: lufs_target,
        target_true_peak_millidbtp: peak_target,
        measured_integrated_lufs_milli: loudness.integrated_lufs_milli,
        measured_true_peak_millidbtp: loudness.true_peak_millidbtp,
        lufs_delta_milli: lufs_delta,
        lufs_tolerance_milli: 200,
        lufs_status,
        true_peak_status: peak_status,
        evaluated_phase: decoded.context.phase,
        decoded_wav_sha256: sha256.to_owned(),
        decoded_sample_frames: frames,
        measurement_receipt_id: decoded.receipt.local_id,
        measurement_proof_sha256: digest(decoded)?,
        evidence_scope: "final-decoded-pcm16-public-milli-numeric-qc-not-mastered-not-intelligibility-not-human-approved",
    })
}

/// Both phases share one source context but have separate native custody.
/// Serializing this value is evidence export, not a way to reconstruct it.
#[derive(Debug, Clone, Serialize)]
pub struct VerifiedOriginalMixAudioMeasurements {
    schema: &'static str,
    source: VerifiedAudioMeasurement,
    decoded: VerifiedAudioMeasurement,
    delivery_audio_qc: VerifiedDeliveryAudioQc,
    evidence_scope: &'static str,
}

impl VerifiedOriginalMixAudioMeasurements {
    pub fn source(&self) -> &VerifiedAudioMeasurement {
        &self.source
    }

    pub fn decoded(&self) -> &VerifiedAudioMeasurement {
        &self.decoded
    }

    pub fn delivery_audio_qc(&self) -> &VerifiedDeliveryAudioQc {
        &self.delivery_audio_qc
    }

    /// Public method/window/unit metadata comes only from the exact current
    /// decoded proof and its native parent; this read has no native dispatcher.
    pub fn delivery_audio_qc_report(
        &self,
        coordinator: &ProductionCoordinator,
    ) -> NativeResult<VerifiedDeliveryAudioQcReport> {
        self.verify_current(coordinator)?;
        qc_report::build_report(&self.delivery_audio_qc, &self.decoded)
    }

    /// A current proof is re-derived from the store and exact current files.
    /// This method has no native calls, file writes, or receipt appends.
    pub fn verify_current(&self, coordinator: &ProductionCoordinator) -> NativeResult<()> {
        let binding = &self.source.context.source;
        let reopened = coordinator.reopen_original_mix_audio_measurements(
            binding.project_id,
            &binding.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )?;
        if digest(&reopened)? != digest(self)? {
            return Err(stale(
                "Current audio measurement chain differs from the supplied proof",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Serialize)]
pub struct MeasuredMltAvMasterEvidence {
    pub master: MltAvMasterEvidence,
    pub audio_measurements: VerifiedOriginalMixAudioMeasurements,
}

#[derive(Debug, Serialize)]
pub struct MeasuredMltMultisegmentAvMasterEvidence {
    pub master: MltMultisegmentAvMasterEvidence,
    pub audio_measurements: VerifiedOriginalMixAudioMeasurements,
}

fn digest(value: &impl Serialize) -> NativeResult<String> {
    let bytes = serde_json::to_vec(value)
        .map_err(|_| invalid("Audio measurement context cannot be encoded"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

fn stale(message: &'static str) -> Error {
    Error::new(ErrorCode::StaleReference, message)
}

fn bounded_request_id(request_id: &str) -> NativeResult<()> {
    if request_id.trim().is_empty()
        || request_id.len() > 96
        || request_id.chars().any(char::is_control)
    {
        return Err(invalid("Audio measurement request identity is invalid"));
    }
    Ok(())
}

fn require_current(project: &Project, expected: &RevisionStamp) -> NativeResult<()> {
    if expected.resource != project.resource_key()
        || expected.generation != project.generation
        || expected.revision != project.revision
    {
        return Err(stale(
            "Audio measurement source revision or generation changed",
        ));
    }
    Ok(())
}

fn source_binding(
    coordinator: &ProductionCoordinator,
    project: &Project,
    expected: &RevisionStamp,
    caller_request_id: &str,
    deliverable_id: Uuid,
    lane: AudioAnalysisLane,
    mix: &PreparedOriginalMix,
) -> NativeResult<SourceBinding> {
    bounded_request_id(caller_request_id)?;
    require_current(project, expected)?;
    if coordinator.client.connection().resource != project.resource_key() {
        return Err(Error::new(
            ErrorCode::PermissionDenied,
            "Audio analysis connection belongs to another project resource",
        ));
    }
    mix.verify_project(project, deliverable_id)
        .map_err(|_| stale("Audio analysis source mix is no longer current"))?;
    if mix.wav_bytes().len() as u64 > MAX_ANALYSIS_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Audio analysis source exceeds the public meter byte budget",
        ));
    }
    Ok(SourceBinding {
        schema: "motionwright.original-mix-analysis-source/1",
        caller_request_id: caller_request_id.to_owned(),
        lane,
        project_id: project.id,
        project_resource: project.resource_key(),
        generation: project.generation,
        revision: project.revision,
        deliverable_id,
        connection_identity: coordinator.client.connection().identity()?,
        audio_inputs_sha256: mix.audio_inputs_sha256().to_owned(),
        source_plan_sha256: mix.evidence().source_plan_sha256.clone(),
        measured_voice_source: None,
        wav_sha256: mix.wav_sha256().to_owned(),
        wav_bytes: mix.wav_bytes().len() as u64,
        sample_frames: mix.plan().sample_frames,
        sample_rate: 48_000,
        channels: 2,
    })
}

/// Legacy global MixIntent uses another representation/name and has no
/// profile-owned target authority. Never migrate or silently reinterpret it.
fn require_unmastered_measurement_scope(project: &Project) -> NativeResult<()> {
    if project.audio.mix.target_lufs.is_some() || project.audio.mix.target_true_peak_dbfs.is_some()
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Legacy global mix targets are not delivery-profile QC targets; persist explicit audio_targets in the selected recipe",
        ));
    }
    Ok(())
}

/// A target-bearing profile cannot go through an unmeasured producer. This
/// seam accepts only an opaque current source proof from the measured wrapper;
/// it is not a bool, a WebView claim or permission to waive the final phase.
pub(super) fn admit_profile_audio_targets(
    coordinator: &ProductionCoordinator,
    project: &Project,
    deliverable_id: Uuid,
    request_id: &str,
    lane: AudioAnalysisLane,
    proof: Option<&VerifiedAudioMeasurement>,
) -> NativeResult<()> {
    let targets = project
        .production_design
        .delivery_recipes
        .get(&deliverable_id)
        .and_then(|recipe| recipe.audio_targets.as_ref());
    let Some(targets) = targets else {
        return Ok(());
    };
    targets
        .validated_milli_targets()
        .map_err(|error| invalid(error.to_string()))?;
    let proof = proof.ok_or_else(|| Error::new(ErrorCode::Unsupported,
        "Delivery audio targets require the measured native master route and final decoded PCM16 QC"))?;
    let binding = &proof.context.source;
    require_current(project, &binding.stamp())?;
    if binding.project_id != project.id
        || binding.deliverable_id != deliverable_id
        || binding.lane != lane
        || binding.namespace()? != request_id
        || proof.context != binding.source_context()
    {
        return Err(stale(
            "Targeted producer proof belongs to another source, profile, lane or request",
        ));
    }
    coordinator.recheck_audio_source(binding)?;
    let current = coordinator.read_current_measurement(binding.source_context())?;
    if digest(&current)? != digest(proof)? {
        return Err(stale(
            "Targeted producer source measurement is not the exact current completed receipt",
        ));
    }
    Ok(())
}

fn require_linux_meter() -> NativeResult<()> {
    if !cfg!(target_os = "linux") {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "The pinned public audio meter runs only in the existing Linux host",
        ));
    }
    Ok(())
}

fn ensure_plain_open_file(file: &File) -> NativeResult<fs::Metadata> {
    let metadata = file
        .metadata()
        .map_err(|_| backend("Audio analysis file metadata is unavailable"))?;
    if !metadata.is_file() || is_link_or_reparse(&metadata) {
        return Err(invalid("Audio analysis requires a plain regular file"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if metadata.nlink() != 1 {
            return Err(invalid("Audio analysis files cannot have hard links"));
        }
    }
    Ok(metadata)
}

fn same_open_identity(expected: &fs::Metadata, actual: &fs::Metadata) -> NativeResult<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if expected.dev() != actual.dev() || expected.ino() != actual.ino() {
            return Err(stale("Audio analysis file identity changed while opening"));
        }
    }
    if expected.len() != actual.len() {
        return Err(stale("Audio analysis file length changed while opening"));
    }
    Ok(())
}

fn read_exact_artifact(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    expected_bytes: u64,
    maximum: u64,
) -> NativeResult<Vec<u8>> {
    if expected_bytes == 0 || expected_bytes > maximum {
        return Err(invalid(
            "Audio analysis artifact byte count is outside its budget",
        ));
    }
    let path = verify_output_artifact(root, relative, expected_sha256, maximum)?;
    let lexical = safe_metadata(&path)?;
    let mut file =
        File::open(&path).map_err(|_| backend("Audio analysis artifact cannot be opened"))?;
    let before = ensure_plain_open_file(&file)?;
    same_open_identity(&lexical, &before)?;
    if before.len() != expected_bytes {
        return Err(stale(
            "Audio analysis artifact byte count differs from its receipt",
        ));
    }
    let mut bytes = Vec::new();
    std::io::Read::by_ref(&mut file)
        .take(expected_bytes + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| backend("Audio analysis artifact cannot be read"))?;
    let after = ensure_plain_open_file(&file)?;
    same_open_identity(&before, &safe_metadata(&path)?)?;
    if after.len() != expected_bytes
        || bytes.len() as u64 != expected_bytes
        || hex::encode(Sha256::digest(&bytes)) != expected_sha256
    {
        return Err(stale("Audio analysis artifact changed while being read"));
    }
    Ok(bytes)
}

fn verify_artifact_bytes(
    root: &Path,
    artifact: &ArtifactIdentity,
    maximum: u64,
) -> NativeResult<()> {
    let path = verify_output_artifact(root, &artifact.relative_path, &artifact.sha256, maximum)?;
    let lexical = safe_metadata(&path)?;
    let mut file =
        File::open(&path).map_err(|_| backend("Native audio parent artifact cannot be opened"))?;
    let opened = ensure_plain_open_file(&file)?;
    same_open_identity(&lexical, &opened)?;
    if opened.len() != artifact.bytes {
        return Err(stale(
            "Native audio parent byte count differs from its receipt",
        ));
    }
    let mut hasher = Sha256::new();
    let mut read_bytes = 0u64;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|_| backend("Native audio parent cannot be read"))?;
        if count == 0 {
            break;
        }
        read_bytes += count as u64;
        if read_bytes > artifact.bytes || read_bytes > maximum {
            return Err(stale("Native audio parent grew while being verified"));
        }
        hasher.update(&buffer[..count]);
    }
    let after = ensure_plain_open_file(&file)?;
    same_open_identity(&opened, &after)?;
    same_open_identity(&opened, &safe_metadata(&path)?)?;
    if read_bytes != artifact.bytes || hex::encode(hasher.finalize()) != artifact.sha256 {
        return Err(stale(
            "Native audio parent bytes changed during verification",
        ));
    }
    Ok(())
}

fn stage_exact_input(root: &Path, context: &MeasurementContext, bytes: &[u8]) -> NativeResult<()> {
    let (sha256, expected_bytes, _) = context.input()?;
    if expected_bytes == 0
        || expected_bytes > MAX_ANALYSIS_BYTES
        || bytes.len() as u64 != expected_bytes
        || hex::encode(Sha256::digest(bytes)) != sha256
    {
        return Err(stale(
            "Audio analysis staging bytes differ from current source custody",
        ));
    }
    if !safe_metadata(root)?.is_dir() {
        return Err(invalid(
            "Audio analysis staging root must be a plain owner directory",
        ));
    }
    let name = context.file_name()?;
    // The generated name is a simple basename accepted by the public contract.
    let path = root.join(&name);
    match OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(mut file) => {
            ensure_plain_open_file(&file)?;
            file.write_all(bytes)
                .and_then(|_| file.sync_all())
                .map_err(|_| backend("Audio staging write was not confirmed; preserve and inspect the original file"))?;
        }
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            // A known immutable file may support a completed read-only receipt.
            // A partial/conflicting file is retained and fails; never overwrite.
        }
        Err(_) => return Err(backend("Audio analysis staging file cannot be created")),
    }
    read_exact_artifact(root, &name, sha256, expected_bytes, MAX_ANALYSIS_BYTES)?;
    Ok(())
}

fn artifact_identity(value: &Value, maximum: u64) -> NativeResult<ArtifactIdentity> {
    let relative_path = value
        .get("path")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.len() <= 4096)
        .ok_or_else(|| backend("Native audio artifact path is missing"))?;
    let sha256 = value
        .get("sha256")
        .and_then(Value::as_str)
        .filter(|s| is_sha256(s))
        .ok_or_else(|| backend("Native audio artifact digest is missing"))?;
    let bytes = value
        .get("bytes")
        .and_then(Value::as_u64)
        .filter(|n| *n > 0 && *n <= maximum)
        .ok_or_else(|| backend("Native audio artifact byte count is missing"))?;
    if value.get("root").and_then(Value::as_str) != Some("output") {
        return Err(backend(
            "Native audio artifact is outside the owner output root",
        ));
    }
    Ok(ArtifactIdentity {
        relative_path: relative_path.to_owned(),
        sha256: sha256.to_owned(),
        bytes,
    })
}

fn completed_receipt<'a>(
    receipt: &'a ProductionReceipt,
    source: &SourceBinding,
    request_id: &str,
    command: &str,
    expected_digest: Option<&str>,
    provider: &str,
) -> NativeResult<(&'a Value, MeasurementReceipt)> {
    if receipt.project_id != source.project_id
        || receipt.generation != source.generation
        || receipt.revision != source.revision
        || receipt.request_id != request_id
        || receipt.command != command
        || receipt.stage != "completed"
        || !is_sha256(&receipt.request_sha256)
        || expected_digest.is_some_and(|digest| receipt.request_sha256 != digest)
    {
        return Err(stale(
            "Audio measurement receipt belongs to another completed request or source",
        ));
    }
    let result = receipt
        .payload
        .get("result")
        .ok_or_else(|| backend("Completed audio receipt has no retained result"))?;
    if result.get("provenance_checked").and_then(Value::as_bool) != Some(true) {
        return Err(backend(
            "Completed audio receipt has no checked native provenance",
        ));
    }
    let execution: Execution = serde_json::from_value(
        result
            .get("execution")
            .cloned()
            .ok_or_else(|| backend("Completed audio receipt has no execution authority"))?,
    )
    .map_err(|_| backend("Completed audio execution authority is malformed"))?;
    let provenance = execution
        .provenance
        .as_ref()
        .ok_or_else(|| backend("Completed audio receipt has no provider provenance"))?;
    if execution.dry_run
        || provenance.provider != provider
        || provenance.source != SourceKind::Driver
        || provenance.provider_generation.is_none()
        || !is_sha256(&provenance.descriptor_sha256)
        || provenance.execution_provider.as_deref() != Some(provider)
    {
        return Err(backend(
            "Completed audio receipt does not identify its actual native provider",
        ));
    }
    let broker_request_id = receipt
        .payload
        .get("broker_request_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty() && s.len() <= 256 && !s.chars().any(char::is_control))
        .ok_or_else(|| backend("Completed audio receipt has no bounded Broker request identity"))?;
    let data = result
        .get("data")
        .ok_or_else(|| backend("Completed audio receipt has no provider data"))?;
    Ok((
        data,
        MeasurementReceipt {
            local_id: receipt.id,
            request_id: receipt.request_id.clone(),
            request_sha256: receipt.request_sha256.clone(),
            broker_request_id: broker_request_id.to_owned(),
            provenance: serde_json::to_value(provenance)
                .map_err(|_| backend("Audio provenance cannot be retained"))?,
        },
    ))
}

fn validate_analysis(
    context: &MeasurementContext,
    data: &Value,
) -> NativeResult<(LoudnessAnalysis, SignalStatistics)> {
    // Public Option fields serialize explicit NULL. Missing evidence is not
    // the same observation as an explicitly unavailable metric.
    for key in [
        "integrated_lufs_milli",
        "momentary_lufs_milli",
        "short_term_lufs_milli",
        "loudness_range_milli",
        "true_peak_millidbtp",
        "sample_peak_millidbfs",
        "unknown_reason",
    ] {
        if data
            .get("loudness")
            .and_then(|value| value.get(key))
            .is_none()
        {
            return Err(backend(
                "Public loudness receipt omitted a metric availability field",
            ));
        }
    }
    let response: AnalysisResponse = serde_json::from_value(data.clone())
        .map_err(|_| backend("Public audio analysis response violates its closed DTO contract"))?;
    let (sha256, expected_bytes, frames) = context.input()?;
    let loudness = response.loudness;
    loudness
        .validate()
        .map_err(|_| backend("Public loudness result violates the pinned meter contract"))?;
    if response.artifact.file_name != context.file_name()?
        || response.artifact.sha256 != sha256
        || response.artifact.bytes != expected_bytes
        || response.artifact.format != "wav"
        || !response.exhaustive
        || loudness.sample_rate != 48_000
        || loudness.channels != 2
        || loudness.layout != "stereo"
        || loudness.frames != frames
        || loudness.nonfinite_samples != 0
        || loudness.true_peak_oversample != 4
    {
        return Err(backend(
            "Public audio analysis does not describe the exact selected WAV",
        ));
    }
    if (frames < 19_200
        && (loudness.integrated_lufs_milli.is_some() || loudness.momentary_lufs_milli.is_some()))
        || (frames < 144_000
            && (loudness.short_term_lufs_milli.is_some()
                || loudness.loudness_range_milli.is_some()))
    {
        return Err(backend(
            "Public meter claimed a window longer than the exact input",
        ));
    }
    let statistics = response
        .pcm_statistics
        .ok_or_else(|| backend("WAV analysis omitted the public independent PCM decode"))?;
    // SignalStatistics currently has no validator. Keep its public DTO and
    // check custody/shape/count invariants; do not implement another analyzer.
    if statistics.schema_version != 2
        || statistics.method != "semwright-pcm-statistics-v2"
        || statistics.sample_rate.hz() != 48_000
        || statistics.frames != frames
        || statistics.channels.len() != 2
        || statistics.nonfinite_samples != 0
        || statistics.out_of_range_samples != 0
        || statistics.silence_threshold_millidbfs != -90_000
        || statistics.silence_ranges.len() > 4096
    {
        return Err(backend(
            "Independent PCM decode does not match the exact source clock",
        ));
    }
    for channel in &statistics.channels {
        if channel.finite_samples != frames
            || channel.nonfinite_samples != 0
            || channel.out_of_range_samples != 0
            || channel
                .dc_offset
                .is_none_or(|v| !v.is_finite() || !(-1.0..=1.0).contains(&v))
            || channel.peak_millidbfs.is_some_and(|v| v > 0)
            || channel.rms_millidbfs.is_some_and(|v| v > 0)
            || channel.peak_millidbfs.is_some() != channel.rms_millidbfs.is_some()
            || matches!((channel.peak_millidbfs, channel.rms_millidbfs), (Some(p), Some(r)) if r > p)
        {
            return Err(backend(
                "Independent PCM16 channel counters or finite statistics disagree",
            ));
        }
    }
    let mut previous_end = 0;
    for range in &statistics.silence_ranges {
        if range.start_frame < previous_end
            || range.start_frame >= range.end_frame
            || range.end_frame > frames
        {
            return Err(backend(
                "Independent PCM silence intervals exceed the selected waveform",
            ));
        }
        previous_end = range.end_frame;
    }
    let all_silent = statistics
        .channels
        .iter()
        .all(|channel| channel.peak_millidbfs.is_none());
    if statistics.entirely_silent != all_silent
        || statistics.peak_millidbfs.is_some() == all_silent
        || statistics.rms_millidbfs.is_some() == all_silent
        || statistics.peak_millidbfs.is_some_and(|v| v > 0)
        || statistics.rms_millidbfs.is_some_and(|v| v > 0)
    {
        return Err(backend(
            "Independent PCM silence and level availability disagree",
        ));
    }
    // NULL remains unavailable/undefined. In particular, one-second sources
    // legitimately have no short-term or LRA measurement. Do not replace NULL,
    // require unknown_reason for every NULL, or claim targets from its absence.
    Ok((loudness, statistics))
}

fn proof_from_receipt(
    context: MeasurementContext,
    receipt: &ProductionReceipt,
) -> NativeResult<VerifiedAudioMeasurement> {
    if let Some(recorded) = AudioReceiptContext::from_receipt(receipt)?
        && recorded != AudioReceiptContext::from_context(&context)?
    {
        return Err(stale(
            "Audio receipt selector differs from its verified measurement context",
        ));
    }
    let args = context.args()?;
    let request_id = context.request_id()?;
    let request_sha256 = request_digest(MEASURE_COMMAND, &args)?;
    let (data, link) = completed_receipt(
        receipt,
        &context.source,
        &request_id,
        MEASURE_COMMAND,
        Some(&request_sha256),
        "driver:audio-analysis",
    )?;
    let (loudness, pcm_statistics) = validate_analysis(&context, data)?;
    Ok(VerifiedAudioMeasurement {
        schema: "motionwright.verified-audio-measurement/1",
        context,
        receipt: link,
        loudness,
        pcm_statistics,
        exhaustive: true,
    })
}

/// Same one-source-sample boundary already used by the AV source admission.
/// The final decoded clock is checked separately by the existing mux verifier.
pub(super) fn verify_source_clock(
    sample_frames: u64,
    video_frames: u64,
    fps_num: u32,
    fps_den: u32,
) -> NativeResult<()> {
    if sample_frames == 0 || video_frames == 0 || fps_num == 0 || fps_den == 0 {
        return Err(invalid(
            "Native audio source has an invalid sample/frame clock",
        ));
    }
    let audio_clock = u128::from(sample_frames) * u128::from(fps_num);
    let video_clock = u128::from(video_frames) * u128::from(fps_den) * 48_000;
    if audio_clock.abs_diff(video_clock) > u128::from(fps_num) {
        return Err(stale(
            "Saved mix and native video differ by more than one source sample",
        ));
    }
    Ok(())
}

fn expected_master_path(source: &SourceBinding) -> NativeResult<String> {
    let namespace = source.namespace()?;
    let token = hex::encode(Sha256::digest(namespace.as_bytes()));
    let suffix = match source.lane {
        AudioAnalysisLane::SingleSegment => "master",
        AudioAnalysisLane::Multisegment => "multisegment-master",
    };
    Ok(format!(
        "mw-{}-r{}-{}-{suffix}.mp4",
        source.project_id.simple(),
        source.revision,
        &token[..16],
    ))
}

fn native_parent(
    coordinator: &ProductionCoordinator,
    project: &Project,
    source: &SourceBinding,
    receipt: &ProductionReceipt,
) -> NativeResult<NativeParent> {
    let request_id = format!("{}{}", source.namespace()?, source.lane.mux_suffix());
    let (data, _) = completed_receipt(
        receipt,
        source,
        &request_id,
        MUX_COMMAND,
        None,
        "driver:mlt-video",
    )?;
    let profile = project
        .deliverables
        .iter()
        .find(|profile| profile.id == source.deliverable_id)
        .ok_or_else(|| stale("Audio measurement output profile no longer exists"))?;
    let fps_num = u32::try_from(profile.frame_rate.num)
        .map_err(|_| invalid("Audio measurement output frame rate is invalid"))?;
    let fps_den = u32::try_from(profile.frame_rate.den)
        .map_err(|_| invalid("Audio measurement output frame rate is invalid"))?;
    let video_frames = data
        .get("frame_count")
        .and_then(Value::as_u64)
        .filter(|frames| *frames > 0)
        .ok_or_else(|| backend("Native audio parent omitted its video frame count"))?;
    verify_source_clock(source.sample_frames, video_frames, fps_num, fps_den)?;
    let video_sha256 = data
        .get("video_sha256")
        .and_then(Value::as_str)
        .filter(|sha| is_sha256(sha))
        .ok_or_else(|| backend("Native audio parent omitted its exact source video identity"))?;
    let output_path = expected_master_path(source)?;
    super::mlt_multisegment_av::verify_exact_native_mux(
        data,
        &super::mlt_multisegment_av::MuxExpected {
            frames: video_frames,
            width: profile.width,
            height: profile.height,
            fps_num,
            fps_den,
            source_video: video_sha256,
            source_audio: &source.wav_sha256,
            output_path: &output_path,
        },
    )?;
    let master = artifact_identity(
        data.get("artifact")
            .ok_or_else(|| backend("Native audio parent has no master"))?,
        MLT_MASTER_MAX_BYTES,
    )?;
    let decoded = artifact_identity(
        data.get("decoded_audio")
            .ok_or_else(|| backend("Native audio parent has no decoded WAV"))?,
        MAX_ANALYSIS_BYTES,
    )?;
    let decoded_sample_frames = data
        .get("decoded_audio_sample_frames")
        .and_then(Value::as_u64)
        .filter(|frames| *frames > 0)
        .ok_or_else(|| backend("Native audio parent has no actual decoded sample count"))?;
    let output_root = &coordinator.client.connection().output_root;
    verify_artifact_bytes(output_root, &master, MLT_MASTER_MAX_BYTES)?;
    let decoded_bytes = read_exact_artifact(
        output_root,
        &decoded.relative_path,
        &decoded.sha256,
        decoded.bytes,
        MAX_ANALYSIS_BYTES,
    )?;
    let actual_decoded_frames = crate::mlt_av_audio::decoded_pcm16_sample_frames(&decoded_bytes)?;
    if actual_decoded_frames != decoded_sample_frames {
        return Err(backend(
            "Native decoded PCM16 WAV frame count differs from its mux receipt",
        ));
    }
    Ok(NativeParent {
        receipt_id: receipt.id,
        request_id,
        request_sha256: receipt.request_sha256.clone(),
        result_sha256: digest(&receipt.payload)?,
        master,
        decoded,
        decoded_sample_frames,
        source_audio_sha256: source.wav_sha256.clone(),
        source_video_sha256: video_sha256.to_owned(),
        video_frames,
        fps_num,
        fps_den,
    })
}

fn joined_measurements(
    source: VerifiedAudioMeasurement,
    decoded: VerifiedAudioMeasurement,
    targets: Option<&DeliveryAudioTargets>,
) -> NativeResult<VerifiedOriginalMixAudioMeasurements> {
    if !matches!(
        source.context.phase,
        MeasurementPhase::PreparedOriginalMix | MeasurementPhase::PreparedMeasuredVoice
    ) || source.context.native_parent.is_some()
        || decoded.context.phase != MeasurementPhase::DecodedMltMaster
        || decoded.context.native_parent.is_none()
        || source.context.source != decoded.context.source
        || source.receipt.request_id == decoded.receipt.request_id
    {
        return Err(stale(
            "Source and decoded measurements do not form one current native audio chain",
        ));
    }
    let delivery_audio_qc = evaluate_delivery_audio_qc(targets, &decoded)?;
    Ok(VerifiedOriginalMixAudioMeasurements {
        schema: "motionwright.original-mix-audio-measurements/2",
        source,
        decoded,
        delivery_audio_qc,
        evidence_scope: "exact-source-and-final-decoded-measurements-profile-qc-not-mastered-not-intelligibility-not-human-approved",
    })
}

impl ProductionCoordinator {
    fn current_audio_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        caller_request_id: &str,
        deliverable_id: Uuid,
        lane: AudioAnalysisLane,
    ) -> NativeResult<(Project, PreparedDeliveryAudio, SourceBinding)> {
        self.client.connection().validate()?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        require_current(&project, expected)?;
        require_unmastered_measurement_scope(&project)?;
        if project
            .production_design
            .delivery_recipes
            .get(&deliverable_id)
            .and_then(|recipe| recipe.original_mix.as_ref())
            .is_some()
        {
            let mix = prepare_saved_original_mix_with_budget(
                &self.service,
                project_id,
                expected,
                deliverable_id,
                OriginalMixDecodeBudget::FullCut180Seconds,
            )
            .map_err(crate::original_mix::MixError::into_native)?;
            let binding = source_binding(
                self,
                &project,
                expected,
                caller_request_id,
                deliverable_id,
                lane,
                &mix,
            )?;
            Ok((
                project,
                PreparedDeliveryAudio::OriginalMix(Box::new(mix)),
                binding,
            ))
        } else {
            let (bytes, binding) = self.current_measured_voice_source(
                &project,
                expected,
                caller_request_id,
                deliverable_id,
                lane,
            )?;
            Ok((
                project,
                PreparedDeliveryAudio::MeasuredVoice(bytes),
                binding,
            ))
        }
    }

    fn current_measured_voice_source(
        &self,
        project: &Project,
        expected: &RevisionStamp,
        caller_request_id: &str,
        deliverable_id: Uuid,
        lane: AudioAnalysisLane,
    ) -> NativeResult<(Vec<u8>, SourceBinding)> {
        bounded_request_id(caller_request_id)?;
        require_current(project, expected)?;
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Audio analysis connection belongs to another project resource",
            ));
        }
        let fingerprint =
            motionwright_domain::variant_dependency_fingerprint(project, deliverable_id)
                .map_err(|error| invalid(error.to_string()))?;
        let profile = project
            .deliverables
            .iter()
            .find(|profile| profile.id == deliverable_id)
            .ok_or_else(|| invalid("Measured voice delivery profile is absent"))?;
        let scene_ids = project
            .scenes
            .iter()
            .map(|scene| scene.id)
            .collect::<Vec<_>>();
        if !profile.included_scene_ids.is_empty() && profile.included_scene_ids != scene_ids {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Measured voice analysis retains the complete original cut until a typed voice-cut binding exists",
            ));
        }
        let schedule = project
            .validated_sfx_schedule_for_profile(deliverable_id)
            .map_err(|error| invalid(error.to_string()))?;
        if project.audio.mix.voice_gain_db != 0.0 || !schedule.is_empty() {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Measured voice cannot omit authored gain or selected semantic SFX; save the source mix",
            ));
        }
        let track_id = profile
            .voice_track_id
            .or(project.audio.active_voice_track_id)
            .ok_or_else(|| invalid("Measured voice delivery has no selected imported track"))?;
        let track = project
            .audio
            .voice_tracks
            .iter()
            .find(|track| track.id == track_id)
            .ok_or_else(|| stale("Selected measured voice track is absent"))?;
        let verified = self
            .service
            .verified_master_voice(project.id, expected, track_id)
            .map_err(storage_error)?;
        let decoded = self
            .service
            .decoded_stem_for_original_mix_with_budget(
                project.id,
                expected,
                track.asset_id,
                OriginalMixDecodeBudget::FullCut180Seconds,
            )
            .map_err(|error| {
                let current = self.service.project(project.id).ok();
                if current
                    .as_ref()
                    .is_none_or(|current| current.stamp() != *expected)
                    || matches!(
                        error,
                        motionwright_storage::StorageError::GenerationConflict
                            | motionwright_storage::StorageError::Conflict { .. }
                            | motionwright_storage::StorageError::BlobMissing { .. }
                            | motionwright_storage::StorageError::InvalidBlobDigest
                            | motionwright_storage::StorageError::UnsafeSourcePath(_)
                    )
                {
                    stale("Measured voice source identity or CAS custody changed")
                } else {
                    Error::new(
                        ErrorCode::Unsupported,
                        "Measured voice failed the supported full-cut WAV decode policy",
                    )
                }
            })?;
        if track.sample_rate_hz != 48_000
            || track.channels != 2
            || profile.audio_sample_rate_hz != 48_000
            || track.measured_duration.num <= 0
            || track.measured_duration.den <= 0
            || u128::from(decoded.sample_frames) * track.measured_duration.den as u128
                != track.measured_duration.num as u128 * 48_000
            || decoded.original_asset_sha256 != track.source_sha256
            || verified.sha256 != track.source_sha256
        {
            return Err(stale(
                "Measured voice metadata differs from its exact source WAV and PCM clock",
            ));
        }
        // Reuse Storage's bounded verified CAS capture; no pathname from a
        // WebView or model can select the measured source.
        let bytes = {
            let store_ref = self.service.store();
            let store = store_ref.lock();
            let before = store.load_project(project.id).map_err(storage_error)?;
            require_current(&before, expected)?;
            let bytes = store
                .read_blob(&track.source_sha256, 64 * 1024 * 1024)
                .map_err(storage_error)?;
            let after = store.load_project(project.id).map_err(storage_error)?;
            require_current(&after, expected)?;
            bytes
        };
        if bytes.len() as u64 != verified.size_bytes {
            return Err(stale(
                "Measured voice source byte length changed during preparation",
            ));
        }
        let voice_source = MeasuredVoiceSource {
            voice_track_id: track_id,
            asset_id: track.asset_id,
            decoded_pcm_sha256: decoded.decoded_pcm_sha256,
            source_cut_sha256: digest(
                &project
                    .scenes
                    .iter()
                    .map(|scene| (scene.id, scene.start, scene.duration))
                    .collect::<Vec<_>>(),
            )?,
        };
        let binding = SourceBinding {
            schema: "motionwright.measured-voice-analysis-source/1",
            caller_request_id: caller_request_id.to_owned(),
            lane,
            project_id: project.id,
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            connection_identity: self.client.connection().identity()?,
            audio_inputs_sha256: fingerprint.audio_inputs_sha256,
            source_plan_sha256: digest(&voice_source)?,
            measured_voice_source: Some(voice_source),
            wav_sha256: verified.sha256,
            wav_bytes: bytes.len() as u64,
            sample_frames: decoded.sample_frames,
            sample_rate: 48_000,
            channels: 2,
        };
        require_current(
            &self.service.project(project.id).map_err(storage_error)?,
            expected,
        )?;
        Ok((bytes, binding))
    }

    fn recheck_audio_source(&self, source: &SourceBinding) -> NativeResult<Project> {
        let (project, _, current) = self.current_audio_source(
            source.project_id,
            &source.stamp(),
            &source.caller_request_id,
            source.deliverable_id,
            source.lane,
        )?;
        if current != *source {
            return Err(stale(
                "Audio source bytes, plan, profile or connection changed",
            ));
        }
        Ok(project)
    }

    fn completed_audio_receipt(
        &self,
        source: &SourceBinding,
        request_id: &str,
    ) -> NativeResult<ProductionReceipt> {
        receipt_history::completed_audio_receipt(&self.service, source, request_id)
    }

    fn read_current_measurement(
        &self,
        context: MeasurementContext,
    ) -> NativeResult<VerifiedAudioMeasurement> {
        let receipt = self.completed_audio_receipt(&context.source, &context.request_id()?)?;
        let (sha, bytes, _) = context.input()?;
        read_exact_artifact(
            &self.client.connection().output_root,
            &context.file_name()?,
            sha,
            bytes,
            MAX_ANALYSIS_BYTES,
        )?;
        proof_from_receipt(context, &receipt)
    }

    async fn measure_context(
        &self,
        context: MeasurementContext,
        bytes: &[u8],
    ) -> NativeResult<VerifiedAudioMeasurement> {
        require_linux_meter()?;
        self.recheck_audio_source(&context.source)?;
        let request_id = context.request_id()?;
        let args = context.args()?;
        let receipt_context = AudioReceiptContext::from_context(&context)?;
        let root = &self.client.connection().output_root;
        stage_exact_input(root, &context, bytes)?;
        // An existing completed result may be read. The shared coordinator
        // rejects nonterminal/failed/UNKNOWN identities; there is no retry loop.
        self.execute_with_receipt_context(
            context.source.project_id,
            &context.source.stamp(),
            &request_id,
            (MEASURE_COMMAND, false),
            args,
            Some(receipt_context),
        )
        .await?;
        self.recheck_audio_source(&context.source)?;
        self.read_current_measurement(context)
    }

    /// Measures the existing deterministic source mix with the public meter.
    /// This method grants neither a target pass nor permission to mutate media.
    pub async fn measure_original_mix_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        lane: AudioAnalysisLane,
    ) -> NativeResult<VerifiedAudioMeasurement> {
        require_linux_meter()?;
        let (_, mix, source) =
            self.current_audio_source(project_id, expected, request_id, deliverable_id, lane)?;
        self.measure_context(source.source_context(), mix.wav_bytes())
            .await
    }

    /// Measures the selected saved mix or the unchanged imported voice source.
    /// No music or SFX source is required for a voice-only delivery.
    pub async fn measure_delivery_audio_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        lane: AudioAnalysisLane,
    ) -> NativeResult<VerifiedAudioMeasurement> {
        self.measure_original_mix_source(project_id, expected, request_id, deliverable_id, lane)
            .await
    }

    async fn measure_completed_audio_parent(
        &self,
        source: VerifiedAudioMeasurement,
    ) -> NativeResult<VerifiedOriginalMixAudioMeasurements> {
        let binding = &source.context.source;
        let project = self.recheck_audio_source(binding)?;
        let parent_id = format!("{}{}", binding.namespace()?, binding.lane.mux_suffix());
        let receipt = self.completed_audio_receipt(binding, &parent_id)?;
        let parent = native_parent(self, &project, binding, &receipt)?;
        let bytes = read_exact_artifact(
            &self.client.connection().output_root,
            &parent.decoded.relative_path,
            &parent.decoded.sha256,
            parent.decoded.bytes,
            MAX_ANALYSIS_BYTES,
        )?;
        let context = MeasurementContext {
            schema: "motionwright.audio-measurement-context/1",
            source: binding.clone(),
            phase: MeasurementPhase::DecodedMltMaster,
            native_parent: Some(parent),
        };
        self.measure_context(context, &bytes).await?;
        // Reconstruct both phases from durable receipts and current files.
        // No opaque in-memory terminal can substitute for persisted custody.
        self.reopen_original_mix_audio_measurements(
            binding.project_id,
            &binding.stamp(),
            &binding.caller_request_id,
            binding.deliverable_id,
            binding.lane,
        )
    }

    async fn measure_source_for_master(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: SourceMeasureRequest<'_>,
    ) -> NativeResult<VerifiedAudioMeasurement> {
        require_linux_meter()?;
        let (project, mix, source) = self.current_audio_source(
            project_id,
            expected,
            request.request_id,
            request.deliverable_id,
            request.lane,
        )?;
        let profile = project
            .deliverables
            .iter()
            .find(|profile| profile.id == request.deliverable_id)
            .ok_or_else(|| stale("Measured source profile is absent"))?;
        if profile.frame_rate.num != i64::from(request.fps_num)
            || profile.frame_rate.den != i64::from(request.fps_den)
        {
            return Err(stale(
                "Measured native video clock differs from the selected delivery profile",
            ));
        }
        // Reject a mismatched source/video clock before even the read-only
        // meter dispatch; final decode keeps the actual AAC sample count later.
        verify_source_clock(
            source.sample_frames,
            request.video_frames,
            request.fps_num,
            request.fps_den,
        )?;
        let audio = request.audio;
        if audio.sha256 != source.wav_sha256 || audio.sample_rate != 48_000 || audio.channels != 2 {
            return Err(stale("Native AV input differs from the measured saved mix"));
        }
        read_exact_artifact(
            &self.client.connection().output_root,
            &audio.relative_path,
            &source.wav_sha256,
            source.wav_bytes,
            MAX_ANALYSIS_BYTES,
        )?;
        self.measure_context(source.source_context(), mix.wav_bytes())
            .await
    }

    /// Opt-in wrapper over the existing single-segment producer. Existing
    /// callers do not acquire a new runtime dependency or request field.
    pub async fn assemble_mlt_av_master_with_audio_analysis(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltAvMasterRequest<'_>,
    ) -> NativeResult<MeasuredMltAvMasterEvidence> {
        if request.motion.segments.len() != 1 {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Measured single-segment master requires one exact existing video segment",
            ));
        }
        let source = self
            .measure_source_for_master(
                project_id,
                expected,
                SourceMeasureRequest {
                    request_id: request.request_id,
                    deliverable_id: request.deliverable_id,
                    lane: AudioAnalysisLane::SingleSegment,
                    audio: request.audio,
                    video_frames: request.motion.segments[0].frame_count,
                    fps_num: request.motion.frame_rate.num,
                    fps_den: request.motion.frame_rate.den,
                },
            )
            .await?;
        let namespace = source.context.source.namespace()?;
        let master = self
            .assemble_mlt_av_master_checked(
                project_id,
                expected,
                MltAvMasterRequest {
                    request_id: &namespace,
                    ..request
                },
                Some(&source),
            )
            .await?;
        let audio_measurements = self.measure_completed_audio_parent(source).await?;
        Ok(MeasuredMltAvMasterEvidence {
            master,
            audio_measurements,
        })
    }

    /// Opt-in wrapper over the existing multisegment producer; no alternate
    /// MLT path or runtime is introduced.
    pub async fn assemble_native_mlt_multisegment_av_master_with_audio_analysis(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltMultisegmentAvMasterRequest<'_>,
    ) -> NativeResult<MeasuredMltMultisegmentAvMasterEvidence> {
        let source = self
            .measure_source_for_master(
                project_id,
                expected,
                SourceMeasureRequest {
                    request_id: request.request_id,
                    deliverable_id: request.deliverable_id,
                    lane: AudioAnalysisLane::Multisegment,
                    audio: request.audio,
                    video_frames: request.visual.frame_count,
                    fps_num: request.visual.source.fps_num,
                    fps_den: request.visual.source.fps_den,
                },
            )
            .await?;
        let namespace = source.context.source.namespace()?;
        let master = self
            .assemble_native_mlt_multisegment_av_master_checked(
                project_id,
                expected,
                MltMultisegmentAvMasterRequest {
                    request_id: &namespace,
                    deliverable_id: request.deliverable_id,
                    options: request.options,
                    rendered: request.rendered,
                    visual: request.visual,
                    audio: request.audio,
                },
                Some(&source),
            )
            .await?;
        let audio_measurements = self.measure_completed_audio_parent(source).await?;
        Ok(MeasuredMltMultisegmentAvMasterEvidence {
            master,
            audio_measurements,
        })
    }

    /// Discover small original receipt references, never measurement proofs.
    /// The stable ledger is traversed once; no media is read and no native
    /// operation, receipt append or per-candidate recovery is performed.
    pub fn original_mix_audio_qc_receipt_inventory(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        deliverable_id: Uuid,
    ) -> NativeResult<AudioQcReceiptInventory> {
        self.client.connection().validate()?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        require_current(&project, expected)?;
        if self.client.connection().resource != project.resource_key()
            || !project
                .deliverables
                .iter()
                .any(|profile| profile.id == deliverable_id)
        {
            return Err(stale(
                "Audio discovery source or output profile is unavailable",
            ));
        }
        let inventory = receipt_history::discovery_inventory(
            &self.service,
            project_id,
            expected,
            deliverable_id,
        )?;
        let latest = self.service.project(project_id).map_err(storage_error)?;
        require_current(&latest, expected)?;
        Ok(inventory)
    }

    /// Recover the private original selector from one durable decoded receipt.
    /// UUID, profile and master digest are untrusted lookup hints, not grants.
    /// Missing historical metadata is unavailable; no history is backfilled.
    /// This read reuses the existing source/proof authority without dispatch,
    /// staging, receipt appends, retry, or a second master registry.
    pub fn recover_original_mix_audio_measurements_from_receipt(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        decoded_measurement_receipt_id: Uuid,
        deliverable_id: Uuid,
        expected_master_sha256: &str,
    ) -> NativeResult<VerifiedOriginalMixAudioMeasurements> {
        require_linux_meter()?;
        if !is_sha256(expected_master_sha256) {
            return Err(invalid("Audio recovery master identity is invalid"));
        }
        self.client.connection().validate()?;
        let project = self.service.project(project_id).map_err(storage_error)?;
        require_current(&project, expected)?;
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Audio recovery connection belongs to another project resource",
            ));
        }
        let selected = receipt_history::selected_decoded_measurement(
            &self.service,
            project_id,
            expected,
            decoded_measurement_receipt_id,
        )?;
        let selector = AudioReceiptContext::from_receipt(&selected)?.ok_or_else(|| {
            Error::new(
                ErrorCode::Unsupported,
                "Original private audio selector metadata is unavailable; history was not rewritten",
            )
        })?;
        if selector.deliverable_id != deliverable_id {
            return Err(stale("Audio receipt belongs to another delivery profile"));
        }
        let proof = self.reopen_original_mix_audio_measurements(
            project_id,
            expected,
            &selector.caller_request_id,
            deliverable_id,
            selector.lane()?,
        )?;
        let decoded = &proof.decoded;
        let parent = decoded
            .context
            .native_parent
            .as_ref()
            .ok_or_else(|| stale("Recovered audio measurement has no native master"))?;
        if decoded.receipt.local_id != selected.id
            || parent.master.sha256 != expected_master_sha256
            || AudioReceiptContext::from_context(&decoded.context)? != selector
        {
            return Err(stale(
                "Recovered audio proof differs from the exact selected receipt, context or master",
            ));
        }
        // Recheck latest atomic custody after source/parent reconstruction.
        // A new completion or UNKNOWN cannot silently replace the selected UUID.
        if self.completed_audio_receipt(&decoded.context.source, &decoded.receipt.request_id)?
            != selected
        {
            return Err(stale("Selected audio receipt changed during recovery"));
        }
        Ok(proof)
    }

    /// Reopen a completed source/final measurement chain in a new process.
    /// It reads the current canonical mix and original durable receipts only.
    /// There are no native calls, writes, receipt appends, or UNKNOWN retries.
    pub fn reopen_original_mix_audio_measurements(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        lane: AudioAnalysisLane,
    ) -> NativeResult<VerifiedOriginalMixAudioMeasurements> {
        require_linux_meter()?;
        let (project, _, binding) =
            self.current_audio_source(project_id, expected, request_id, deliverable_id, lane)?;
        let source = self.read_current_measurement(binding.source_context())?;
        let parent_id = format!("{}{}", binding.namespace()?, lane.mux_suffix());
        let receipt = self.completed_audio_receipt(&binding, &parent_id)?;
        let parent = native_parent(self, &project, &binding, &receipt)?;
        let decoded = self.read_current_measurement(MeasurementContext {
            schema: "motionwright.audio-measurement-context/1",
            source: binding.clone(),
            phase: MeasurementPhase::DecodedMltMaster,
            native_parent: Some(parent),
        })?;
        let latest_project = self.recheck_audio_source(&binding)?;
        let latest_parent_receipt = self.completed_audio_receipt(&binding, &parent_id)?;
        let latest_parent = native_parent(self, &latest_project, &binding, &latest_parent_receipt)?;
        if decoded.context.native_parent.as_ref() != Some(&latest_parent) {
            return Err(stale(
                "Native audio parent changed during proof reconstruction",
            ));
        }
        let targets = latest_project
            .production_design
            .delivery_recipes
            .get(&deliverable_id)
            .and_then(|recipe| recipe.audio_targets.as_ref());
        joined_measurements(source, decoded, targets)
    }
}

#[cfg(test)]
mod tests;
