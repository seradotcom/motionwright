//! Native audio derivatives through an explicit backend-owner Unix route.
//!
//! Reuses the existing R5 Core/Host binding, Common3 original capture and
//! proposed verified canonical importer. Owner roots do not mint a grant or a
//! ProviderLease from configuration/observations. No extra runtime or decoder.

use super::provider_binding::{
    AudioPairCaptureStage, AudioPairReadbacks, BoundAudioDerivativeCoordinator,
};
use super::{ProductionCoordinator, backend, invalid, is_sha256, storage_error};
use motionwright_domain::{Asset, Project, RevisionStamp};
use motionwright_service::StudioService;
use motionwright_storage::{
    ApplyOutcome, BlobDescriptor, CasBlobLease, FileImportIntent, ProductionReceipt,
    ProductionReceiptInput,
};
use semwright_audio_domain::wav::WaveReader;
use semwright_native_sdk::{Error, ErrorCode, Result as NativeResult, Value, json};
use semwright_platform_api::filesystem::{ScopedFilesystem, ScopedRoot};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use uuid::Uuid;

const AUDIO_DERIVE_COMMAND: &str = "driver.mlt-video.audio.derive";
const AUDIO_DERIVE_RESPONSE_SCHEMA: &str = "semwright.mlt-audio-derivative/1";
const AUDIO_DERIVE_RESPONSE_LIMIT: usize = 65_536;
const AUDIO_DERIVE_STREAM_LIMIT: u64 = 262_144;

/// Selects an existing closed SDK operation profile. It cannot choose a tool,
/// filter, arbitrary sample rate, path, provider or operation timeout.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AudioDerivativeProfile {
    #[serde(rename = "pcm16-stereo48-to-mono16-v1")]
    Stereo48ToMono16,
    #[serde(rename = "pcm16-mono16-to-dualmono48-v1")]
    Mono16ToDualMono48,
}

impl AudioDerivativeProfile {
    fn id(self) -> &'static str {
        match self {
            Self::Stereo48ToMono16 => "pcm16-stereo48-to-mono16-v1",
            Self::Mono16ToDualMono48 => "pcm16-mono16-to-dualmono48-v1",
        }
    }

    fn source_limit(self) -> u64 {
        match self {
            Self::Stereo48ToMono16 => 2 * 1024 * 1024,
            Self::Mono16ToDualMono48 => 6 * 1024 * 1024,
        }
    }

    fn output_limit(self) -> u64 {
        match self {
            Self::Stereo48ToMono16 => 1024 * 1024,
            Self::Mono16ToDualMono48 => 40 * 1024 * 1024,
        }
    }

    fn source_layout(self) -> (u32, u16) {
        match self {
            Self::Stereo48ToMono16 => (48_000, 2),
            Self::Mono16ToDualMono48 => (16_000, 1),
        }
    }

    fn target_layout(self) -> (u32, u16) {
        match self {
            Self::Stereo48ToMono16 => (16_000, 1),
            Self::Mono16ToDualMono48 => (48_000, 2),
        }
    }

    fn output_frames(self, source_frames: u64) -> NativeResult<u64> {
        match self {
            Self::Stereo48ToMono16
                if (1_200..=384_000).contains(&source_frames)
                    && source_frames.is_multiple_of(3) =>
            {
                Ok(source_frames / 3)
            }
            Self::Mono16ToDualMono48 if (16_000..=2_880_000).contains(&source_frames) => {
                source_frames.checked_mul(3).ok_or_else(|| {
                    invalid("Audio derivative target sample count exceeds its closed clock")
                })
            }
            _ => Err(Error::new(
                ErrorCode::Unsupported,
                "Audio derivative source duration or integral sample clock is outside its profile",
            )),
        }
    }
}

/// Source selection only. Original bytes and metadata are loaded from the
/// current project's asset registry and verified CAS, never from this DTO.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioDerivativeIntent {
    pub source_asset_id: Uuid,
    pub profile: AudioDerivativeProfile,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeAudioPcm {
    sample_rate: u32,
    channels: u16,
    channel_mask: Option<u32>,
    container_bits: u16,
    valid_bits: u16,
    ieee_float: bool,
    sample_frames: u64,
}

/// Observation of all samples returned by the public SDK WaveReader. There is
/// no Deserialize or public field constructor for this observation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AudioDerivativePcmObservation {
    layout: NativeAudioPcm,
    pcm16_interleaved_sha256: String,
    all_payload_frames_read: bool,
    channels_identical: Option<bool>,
}

impl AudioDerivativePcmObservation {
    pub fn sample_rate(&self) -> u32 {
        self.layout.sample_rate
    }

    pub fn channels(&self) -> u16 {
        self.layout.channels
    }

    pub fn sample_frames(&self) -> u64 {
        self.layout.sample_frames
    }

    pub fn pcm16_interleaved_sha256(&self) -> &str {
        &self.pcm16_interleaved_sha256
    }
}

fn inspect_pcm(
    profile: AudioDerivativeProfile,
    bytes: &[u8],
    source_frames: Option<u64>,
) -> NativeResult<AudioDerivativePcmObservation> {
    let maximum = if source_frames.is_some() {
        profile.output_limit()
    } else {
        profile.source_limit()
    };
    if bytes.len() < 44 || bytes.len() as u64 > maximum {
        return Err(invalid(
            "Audio derivative WAV exceeds its closed byte bound",
        ));
    }
    let mut reader = WaveReader::open(std::io::Cursor::new(bytes), maximum)
        .map_err(|_| invalid("Audio derivative requires an intact bounded RIFF/WAVE source"))?;
    let info = reader.info().clone();
    let (layout, expected_frames) = match source_frames {
        Some(source_frames) => (
            profile.target_layout(),
            profile.output_frames(source_frames)?,
        ),
        None => {
            profile.output_frames(info.frames)?;
            (profile.source_layout(), info.frames)
        }
    };
    if info.sample_rate.0 != layout.0
        || info.channels != layout.1
        || info.container_bits != 16
        || info.valid_bits != 16
        || info.ieee_float
        || info.frames != expected_frames
        || info
            .channel_mask
            .is_some_and(|mask| mask != if layout.1 == 1 { 4 } else { 3 })
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Audio derivative WAV bytes differ from the selected PCM16 layout or clock",
        ));
    }
    let channels = usize::from(info.channels);
    let mut observed = 0u64;
    let mut pcm = Sha256::new();
    let mut identical = (channels == 2).then_some(true);
    loop {
        let values = reader
            .read_frames(16_384)
            .map_err(|_| invalid("Audio derivative original PCM payload cannot be read"))?;
        if values.is_empty() {
            break;
        }
        if values.len() % channels != 0 {
            return Err(invalid("Audio derivative PCM frame is incomplete"));
        }
        observed = observed
            .checked_add((values.len() / channels) as u64)
            .filter(|count| *count <= expected_frames)
            .ok_or_else(|| invalid("Audio derivative decoded frame count exceeds its clock"))?;
        for frame in values.chunks_exact(channels) {
            for value in frame {
                let sample = *value * 32768.0;
                if !sample.is_finite()
                    || sample.fract() != 0.0
                    || !(-32768.0..=32767.0).contains(&sample)
                {
                    return Err(invalid("Audio derivative contains a sample outside PCM16"));
                }
                pcm.update((sample as i16).to_le_bytes());
            }
            if channels == 2 && frame[0] != frame[1] {
                identical = Some(false);
            }
        }
    }
    if observed != expected_frames {
        return Err(invalid(
            "Audio derivative payload does not contain its exact sample clock",
        ));
    }
    if source_frames.is_some()
        && profile == AudioDerivativeProfile::Mono16ToDualMono48
        && identical != Some(true)
    {
        return Err(backend(
            "Native derivative channels are not the declared dual mono",
        ));
    }
    Ok(AudioDerivativePcmObservation {
        layout: NativeAudioPcm {
            sample_rate: info.sample_rate.0,
            channels: info.channels,
            channel_mask: info.channel_mask,
            container_bits: info.container_bits,
            valid_bits: info.valid_bits,
            ieee_float: info.ieee_float,
            sample_frames: observed,
        },
        pcm16_interleaved_sha256: hex::encode(pcm.finalize()),
        all_payload_frames_read: true,
        channels_identical: identical,
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
struct AudioDerivativeSource {
    schema: &'static str,
    caller_request_id: String,
    project_id: Uuid,
    resource: String,
    generation: Uuid,
    revision: u64,
    source_asset_id: Uuid,
    source_asset_snapshot_sha256: String,
    source_sha256: String,
    source_bytes: u64,
    source_media_type: String,
    connection_identity: String,
    media_relative_path: String,
    profile: AudioDerivativeProfile,
    pcm: AudioDerivativePcmObservation,
}

impl AudioDerivativeSource {
    fn stamp(&self) -> RevisionStamp {
        RevisionStamp {
            resource: self.resource.clone(),
            generation: self.generation,
            revision: self.revision,
        }
    }

    fn digest(&self) -> NativeResult<String> {
        let bytes = serde_json::to_vec(self)
            .map_err(|_| invalid("Audio derivative source lineage cannot be encoded"))?;
        Ok(hex::encode(Sha256::digest(bytes)))
    }

    fn output_name(&self) -> NativeResult<String> {
        Ok(format!("mw-audio-derived-{}.wav", self.digest()?))
    }

    fn args(&self) -> NativeResult<Value> {
        Ok(json!({
            "profile":self.profile.id(),
            "source":{
                "root":"media","path":self.media_relative_path,
                "sha256":self.source_sha256,"size_bytes":self.source_bytes,
                "sample_rate":self.pcm.sample_rate(),"channels":self.pcm.channels(),
                "sample_frames":self.pcm.sample_frames()
            },
            "output_path":self.output_name()?
        }))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AudioDerivativeArtifact {
    root: String,
    path: String,
    sha256: String,
    bytes: u64,
}

impl AudioDerivativeArtifact {
    fn validate(&self, path: &str, minimum: u64, maximum: u64) -> NativeResult<()> {
        if self.root != "output"
            || self.path != path
            || !is_sha256(&self.sha256)
            || self.bytes < minimum
            || self.bytes > maximum
            || (self.bytes == 0 && self.sha256 != hex::encode(Sha256::digest(b"")))
        {
            return Err(backend(
                "Audio derivative Artifact identity or closed scope differs",
            ));
        }
        Ok(())
    }

    fn verify_bytes(&self, bytes: &[u8]) -> NativeResult<()> {
        if bytes.len() as u64 != self.bytes || hex::encode(Sha256::digest(bytes)) != self.sha256 {
            return Err(backend(
                "Audio derivative original bytes differ from their Artifact",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct NativeAudioDerivative {
    schema: String,
    profile: String,
    source_sha256: String,
    recipe_sha256: String,
    runtime_sha256: String,
    source: NativeAudioPcm,
    target: NativeAudioPcm,
    artifact: AudioDerivativeArtifact,
    original_source: AudioDerivativeArtifact,
    original_output: AudioDerivativeArtifact,
    process_originals: Vec<AudioDerivativeArtifact>,
    process_capture_complete: bool,
}

fn require_object_keys(value: &Value, keys: &[&str]) -> NativeResult<()> {
    if value.as_object().is_none_or(|object| {
        object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key))
    }) {
        return Err(backend(
            "Audio derivative native response shape differs from the closed contract",
        ));
    }
    Ok(())
}

fn parse_native_derivative(
    source: &AudioDerivativeSource,
    value: &Value,
) -> NativeResult<NativeAudioDerivative> {
    let encoded = serde_json::to_vec(value)
        .map_err(|_| backend("Audio derivative native response cannot be retained"))?;
    if encoded.len() > AUDIO_DERIVE_RESPONSE_LIMIT {
        return Err(backend(
            "Audio derivative native response exceeds its byte bound",
        ));
    }
    require_object_keys(
        value,
        &[
            "schema",
            "profile",
            "source_sha256",
            "recipe_sha256",
            "runtime_sha256",
            "source",
            "target",
            "artifact",
            "original_source",
            "original_output",
            "process_originals",
            "process_capture_complete",
        ],
    )?;
    for key in ["source", "target"] {
        require_object_keys(
            &value[key],
            &[
                "sample_rate",
                "channels",
                "channel_mask",
                "container_bits",
                "valid_bits",
                "ieee_float",
                "sample_frames",
            ],
        )?;
    }
    let result: NativeAudioDerivative = serde_json::from_slice(&encoded)
        .map_err(|_| backend("Audio derivative native response has invalid field types"))?;
    let (rate, channels) = source.profile.target_layout();
    if result.schema != AUDIO_DERIVE_RESPONSE_SCHEMA
        || result.profile != source.profile.id()
        || result.source_sha256 != source.source_sha256
        || !is_sha256(&result.recipe_sha256)
        || !is_sha256(&result.runtime_sha256)
        || result.source != source.pcm.layout
        || result.target.sample_rate != rate
        || result.target.channels != channels
        || result.target.sample_frames
            != source.profile.output_frames(source.pcm.sample_frames())?
        || result.target.container_bits != 16
        || result.target.valid_bits != 16
        || result.target.ieee_float
        || result
            .target
            .channel_mask
            .is_some_and(|mask| mask != if channels == 1 { 4 } else { 3 })
        || !result.process_capture_complete
    {
        return Err(backend(
            "Audio derivative native result does not identify the prepared source or target",
        ));
    }
    let name = source.output_name()?;
    result
        .artifact
        .validate(&name, 44, source.profile.output_limit())?;
    result.original_source.validate(
        &format!("{name}.audio-derive.source.original.wav"),
        44,
        source.profile.source_limit(),
    )?;
    result.original_output.validate(
        &format!("{name}.audio-derive.output.original.wav"),
        44,
        source.profile.output_limit(),
    )?;
    if result.original_source.sha256 != source.source_sha256
        || result.original_source.bytes != source.source_bytes
        || result.original_output.sha256 != result.artifact.sha256
        || result.original_output.bytes != result.artifact.bytes
        || result.process_originals.len() != 4
    {
        return Err(backend(
            "Audio derivative original source/candidate or process roster differs",
        ));
    }
    for (artifact, (suffix, minimum, maximum)) in result.process_originals.iter().zip([
        ("argv.original.json", 1, AUDIO_DERIVE_RESPONSE_LIMIT as u64),
        ("stdout.original.bin", 0, AUDIO_DERIVE_STREAM_LIMIT),
        ("stderr.original.bin", 0, AUDIO_DERIVE_STREAM_LIMIT),
        (
            "terminal.original.json",
            1,
            AUDIO_DERIVE_RESPONSE_LIMIT as u64,
        ),
    ]) {
        artifact.validate(
            &format!("{name}.audio-derive.ffmpeg.{suffix}"),
            minimum,
            maximum,
        )?;
    }
    Ok(result)
}

const AUDIO_DERIVE_LINEAGE_COMMAND: &str = "motionwright.audio-derivative.observe";
const AUDIO_DERIVE_LINEAGE_SCHEMA: &str = "motionwright.audio-derivative-lineage/1";
const AUDIO_DERIVE_LINEAGE_LIMIT: usize = 131_072;

fn derivative_request_id(request_id: &str) -> NativeResult<()> {
    if request_id.trim().is_empty()
        || request_id.len() > 96
        || request_id.chars().any(char::is_control)
    {
        return Err(invalid("Audio derivative request identity is invalid"));
    }
    Ok(())
}

fn derivative_digest(value: &impl Serialize) -> NativeResult<String> {
    let bytes = serde_json::to_vec(value)
        .map_err(|_| invalid("Audio derivative observation cannot be encoded"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

fn derivative_require_current(project: &Project, expected: &RevisionStamp) -> NativeResult<()> {
    if project.resource_key() != expected.resource
        || project.generation != expected.generation
        || project.revision != expected.revision
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Audio derivative requires the exact current project reference",
        ));
    }
    Ok(())
}

fn derivative_source_asset(project: &Project, source: Uuid) -> NativeResult<&Asset> {
    let asset = project
        .assets
        .iter()
        .find(|asset| asset.id == source)
        .ok_or_else(|| {
            Error::new(
                ErrorCode::NotFound,
                "Audio derivative source is absent from the current project",
            )
        })?;
    if !asset.media_type.starts_with("audio/")
        || !asset.content_sha256.as_deref().is_some_and(is_sha256)
    {
        return Err(Error::new(
            ErrorCode::Unsupported,
            "Audio derivative requires a project-owned audio asset in verified CAS",
        ));
    }
    Ok(asset)
}

fn derivative_check_asset(project: &Project, source: &AudioDerivativeSource) -> NativeResult<()> {
    derivative_require_current(project, &source.stamp())?;
    let asset = derivative_source_asset(project, source.source_asset_id)?;
    if derivative_digest(asset)? != source.source_asset_snapshot_sha256
        || asset.content_sha256.as_deref() != Some(source.source_sha256.as_str())
        || asset.media_type != source.source_media_type
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Audio derivative source asset metadata or original content changed",
        ));
    }
    Ok(())
}

fn derivative_require_fresh(prior: Option<&ProductionReceipt>) -> NativeResult<()> {
    if let Some(prior) = prior {
        let error = Error::new(
            ErrorCode::Conflict,
            "Audio derivative already has an observation; inspect its original receipts before any new operation",
        );
        return Err(
            if prior.stage == "outcome_unknown"
                || (prior.stage == "dispatching" && prior.payload["mutation"] == json!(true))
            {
                error.uncertain()
            } else {
                error
            },
        );
    }
    Ok(())
}

fn cas_relative_source(root: &Path, source: &Path) -> NativeResult<String> {
    let relative = source.strip_prefix(root).map_err(|_| {
        Error::new(
            ErrorCode::PermissionDenied,
            "Audio source is outside the admitted CAS root",
        )
    })?;
    let mut names = Vec::new();
    for component in relative.components() {
        let std::path::Component::Normal(name) = component else {
            return Err(invalid("Audio CAS source path is not normalized"));
        };
        let name = name
            .to_str()
            .ok_or_else(|| invalid("Audio CAS source is not UTF-8"))?;
        if name.is_empty() || name.contains(['\\', '\0']) {
            return Err(invalid("Audio CAS source component is invalid"));
        }
        names.push(name);
    }
    let relative = names.join("/");
    if relative.is_empty() || relative.len() > 4096 {
        return Err(invalid(
            "Audio CAS source relative path is outside its bound",
        ));
    }
    Ok(relative)
}

struct LeasedAudioDerivativeSource {
    source: AudioDerivativeSource,
    bytes: Vec<u8>,
    media_root: PathBuf,
    lease: Option<CasBlobLease>,
}

impl LeasedAudioDerivativeSource {
    // This explicit release is used only after the real native use is known to
    // have ended. A cancelled future or UNKNOWN retains the durable CAS lease;
    // dropping CasBlobLease intentionally does not release that protection.
    fn release(&mut self, service: &StudioService) -> NativeResult<()> {
        if let Some(lease) = self.lease.take() {
            service
                .store()
                .lock()
                .release_blob_lease(lease)
                .map_err(storage_error)?;
        }
        Ok(())
    }
}

impl ProductionCoordinator {
    fn prepare_audio_derivative_source(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        intent: AudioDerivativeIntent,
    ) -> NativeResult<LeasedAudioDerivativeSource> {
        derivative_request_id(request_id)?;
        let identity = self.client.connection().identity()?;
        let native_request_id = format!("{request_id}:audio-derive-native");
        let lineage_request_id = format!("{request_id}:audio-derive-lineage");
        let store = self.service.store();
        let (project, asset, bytes) = {
            let store = store.lock();
            let project = store.load_project(project_id).map_err(storage_error)?;
            derivative_require_current(&project, expected)?;
            if self.client.connection().resource != project.resource_key() {
                return Err(Error::new(
                    ErrorCode::PermissionDenied,
                    "Audio derivative connection belongs to another project",
                ));
            }
            // The canonical coordinator still performs its own atomic receipt
            // admission. This read avoids preparing a new lease for a known
            // historical/failed/UNKNOWN identity; it cannot authorize dispatch.
            derivative_require_fresh(
                store
                    .latest_production_receipt(project_id, &native_request_id)
                    .map_err(storage_error)?
                    .as_ref(),
            )?;
            derivative_require_fresh(
                store
                    .latest_production_receipt(project_id, &lineage_request_id)
                    .map_err(storage_error)?
                    .as_ref(),
            )?;
            let asset = derivative_source_asset(&project, intent.source_asset_id)?.clone();
            let digest = asset
                .content_sha256
                .as_deref()
                .ok_or_else(|| invalid("Audio source has no CAS identity"))?;
            let bytes = store
                .read_blob(digest, intent.profile.source_limit())
                .map_err(storage_error)?;
            (project, asset, bytes)
        };
        let pcm = inspect_pcm(intent.profile, &bytes, None)?;
        let source_sha256 = asset
            .content_sha256
            .as_deref()
            .ok_or_else(|| invalid("Audio source has no CAS identity"))?
            .to_owned();
        if hex::encode(Sha256::digest(&bytes)) != source_sha256 {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio source changed while its original bytes were inspected",
            ));
        }
        let mut source = AudioDerivativeSource {
            schema: "motionwright.audio-derivative-source/1",
            caller_request_id: request_id.into(),
            project_id,
            resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            source_asset_id: asset.id,
            source_asset_snapshot_sha256: derivative_digest(&asset)?,
            source_sha256,
            source_bytes: bytes.len() as u64,
            source_media_type: asset.media_type,
            connection_identity: identity,
            media_relative_path: String::new(),
            profile: intent.profile,
            pcm,
        };
        let store = store.lock();
        derivative_check_asset(
            &store.load_project(project_id).map_err(storage_error)?,
            &source,
        )?;
        // Use the existing verified path API rather than reconstructing CAS's
        // storage layout. Neither this path nor blob_root grants SDK media IO.
        let media_root = store.blob_root().to_owned();
        let original = store
            .verified_blob_path(&source.source_sha256)
            .map_err(storage_error)?;
        source.media_relative_path = cas_relative_source(&media_root, &original)?;
        let lease = store
            .lease_blob(&source.source_sha256)
            .map_err(storage_error)?;
        Ok(LeasedAudioDerivativeSource {
            source,
            bytes,
            media_root,
            lease: Some(lease),
        })
    }

    fn recheck_audio_derivative_source(&self, source: &AudioDerivativeSource) -> NativeResult<()> {
        if self.client.connection().identity()? != source.connection_identity
            || self.client.connection().resource != source.resource
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio derivative connection changed after source observation",
            ));
        }
        let store = self.service.store();
        let store = store.lock();
        derivative_check_asset(
            &store
                .load_project(source.project_id)
                .map_err(storage_error)?,
            source,
        )?;
        let bytes = store
            .read_blob(&source.source_sha256, source.profile.source_limit())
            .map_err(storage_error)?;
        if bytes.len() as u64 != source.source_bytes
            || hex::encode(Sha256::digest(&bytes)) != source.source_sha256
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio derivative CAS original no longer matches its admitted source",
            ));
        }
        Ok(())
    }
}

fn parse_original_json(bytes: &[u8]) -> NativeResult<Value> {
    if bytes.is_empty() || bytes.len() > AUDIO_DERIVE_RESPONSE_LIMIT {
        return Err(backend(
            "Audio derivative original metadata exceeds its closed byte budget",
        ));
    }
    serde_json::from_slice(bytes)
        .map_err(|_| backend("Audio derivative original metadata is malformed"))
}

fn verify_original_process_capture(
    source: &AudioDerivativeSource,
    result: &NativeAudioDerivative,
    originals: &[Vec<u8>; 4],
) -> NativeResult<Value> {
    for (artifact, bytes) in result.process_originals.iter().zip(originals) {
        artifact.verify_bytes(bytes)?;
    }
    let argv = parse_original_json(&originals[0])?;
    require_object_keys(
        &argv,
        &[
            "encoding",
            "executable",
            "argv",
            "cwd",
            "timeout_millis",
            "cpu_seconds",
            "address_space_bytes",
            "environment_included",
            "native_execution_retried",
        ],
    )?;
    let os_bytes = |value: &Value, maximum: usize| -> NativeResult<Vec<u8>> {
        let encoded = value
            .as_str()
            .ok_or_else(|| backend("Audio argv original is not encoded bytes"))?;
        if encoded.len() > maximum * 2 || encoded.len() % 2 != 0 {
            return Err(backend("Audio argv original exceeds its byte count"));
        }
        hex::decode(encoded)
            .map_err(|_| backend("Audio argv original contains invalid hexadecimal"))
    };
    let executable = os_bytes(&argv["executable"], 4096)?;
    let cwd = os_bytes(&argv["cwd"], 4096)?;
    let args = argv["argv"]
        .as_array()
        .filter(|args| args.len() <= 128)
        .ok_or_else(|| backend("Audio argv original has an invalid argument roster"))?;
    let mut total = 0usize;
    for value in args {
        total = total
            .checked_add(os_bytes(value, 4096)?.len())
            .filter(|count| *count <= 16_384)
            .ok_or_else(|| backend("Audio argv original exceeds its cumulative byte budget"))?;
    }
    if argv["encoding"] != json!("unix-os-bytes-hex")
        || executable.is_empty()
        || cwd.is_empty()
        || argv["environment_included"] != json!(false)
        || argv["native_execution_retried"] != json!(false)
        || argv["timeout_millis"]
            .as_u64()
            .is_none_or(|value| value == 0)
        || argv["cpu_seconds"].as_u64().is_none_or(|value| value == 0)
        || argv["address_space_bytes"]
            .as_u64()
            .is_none_or(|value| value == 0)
    {
        return Err(backend(
            "Audio argv original does not describe the existing bounded executor",
        ));
    }
    let terminal = parse_original_json(&originals[3])?;
    require_object_keys(
        &terminal,
        &[
            "schema",
            "profile",
            "source_sha256",
            "recipe_sha256",
            "ffmpeg_sha256",
            "process",
            "process_result_received",
            "retained_prefix_limit",
            "per_stream_total_limit",
            "custody_error",
            "capture_scope",
            "physical_acceptance",
        ],
    )?;
    if terminal["schema"] != json!("semwright.audio-derivative-process-original/1")
        || terminal["profile"] != json!(source.profile.id())
        || terminal["source_sha256"] != json!(source.source_sha256)
        || terminal["recipe_sha256"] != json!(result.recipe_sha256)
        || terminal["ffmpeg_sha256"] != json!(result.runtime_sha256)
        || terminal["process_result_received"] != json!(true)
        || terminal["retained_prefix_limit"] != json!(AUDIO_DERIVE_STREAM_LIMIT)
        || terminal["per_stream_total_limit"] != json!(AUDIO_DERIVE_STREAM_LIMIT)
        || !terminal["custody_error"].is_null()
        || terminal["capture_scope"] != json!("same-FFmpeg-spawn-before-checked-and-WAV-validation")
        || terminal["physical_acceptance"] != json!(false)
    {
        return Err(backend(
            "Audio process original differs from the authenticated derivative result",
        ));
    }
    let process = &terminal["process"];
    require_object_keys(
        process,
        &[
            "spawn_attempted",
            "pid",
            "terminal_observed",
            "exit_code",
            "term_signal",
            "cancelled",
            "timed_out",
            "output_exceeded",
            "drain_stop_reason",
            "stdout",
            "stderr",
            "supervisor_error",
            "capture_complete",
            "native_execution_retried",
        ],
    )?;
    if process["spawn_attempted"] != json!(true)
        || process["pid"]
            .as_u64()
            .is_none_or(|pid| pid == 0 || pid > u64::from(u32::MAX))
        || process["terminal_observed"] != json!(true)
        || process["exit_code"] != json!(0)
        || !process["term_signal"].is_null()
        || process["cancelled"] != json!(false)
        || process["timed_out"] != json!(false)
        || process["output_exceeded"] != json!(false)
        || !process["drain_stop_reason"].is_null()
        || !process["supervisor_error"].is_null()
        || process["capture_complete"] != json!(true)
        || process["native_execution_retried"] != json!(false)
    {
        return Err(backend(
            "Audio process original is incomplete or has no known successful terminal",
        ));
    }
    for (name, index) in [("stdout", 1), ("stderr", 2)] {
        let stream = &process[name];
        require_object_keys(
            stream,
            &[
                "observed_bytes",
                "retained_bytes",
                "retained_sha256",
                "eof_observed",
                "read_error",
                "reader_panicked",
                "drain_stopped",
                "truncated",
                "complete",
            ],
        )?;
        let artifact = &result.process_originals[index];
        if stream["observed_bytes"] != json!(artifact.bytes)
            || stream["retained_bytes"] != json!(artifact.bytes)
            || stream["retained_sha256"] != json!(artifact.sha256)
            || stream["eof_observed"] != json!(true)
            || !stream["read_error"].is_null()
            || stream["reader_panicked"] != json!(false)
            || stream["drain_stopped"] != json!(false)
            || stream["truncated"] != json!(false)
            || stream["complete"] != json!(true)
        {
            return Err(backend(
                "Audio process stream original does not establish complete same-spawn bytes",
            ));
        }
    }
    Ok(terminal)
}

/// A byte/clock observation from one authenticated native operation. Its private
/// constructor cannot be reached by deserializing Doctor, configuration or a
/// stored JSON receipt. It is not a provider lease, grant, alignment or review.
#[derive(Debug, Clone, Serialize)]
pub struct ObservedAudioDerivative {
    schema: &'static str,
    source: AudioDerivativeSource,
    source_lineage_sha256: String,
    target: AudioDerivativePcmObservation,
    artifact: AudioDerivativeArtifact,
    recipe_sha256: String,
    runtime_sha256: String,
    original_source: AudioDerivativeArtifact,
    original_output: AudioDerivativeArtifact,
    process_originals: Vec<AudioDerivativeArtifact>,
    native_receipt_id: Uuid,
    native_receipt_sha256: String,
    lineage_receipt_id: Uuid,
    import_request_id: String,
    import_intent: FileImportIntent,
    native_execution_retried: bool,
    human_review_or_alignment: bool,
    resampling_fidelity_assessed: bool,
    #[serde(skip)]
    original_native_receipt: ProductionReceipt,
    #[serde(skip)]
    original_lineage_receipt: ProductionReceipt,
}

impl ObservedAudioDerivative {
    pub fn source_asset_id(&self) -> Uuid {
        self.source.source_asset_id
    }
    pub fn profile(&self) -> AudioDerivativeProfile {
        self.source.profile
    }
    pub fn target(&self) -> &AudioDerivativePcmObservation {
        &self.target
    }
    pub fn artifact_sha256(&self) -> &str {
        &self.artifact.sha256
    }
    pub fn native_receipt_id(&self) -> Uuid {
        self.native_receipt_id
    }
    pub fn lineage_receipt_id(&self) -> Uuid {
        self.lineage_receipt_id
    }
}

/// The canonical import outcome identifies the new Asset and its one Project
/// revision. The original asset and any existing VoiceTrack/transcript remain
/// separate authored objects; this observation never assigns a review method.
#[derive(Debug, Serialize)]
pub struct AdoptedAudioDerivative {
    schema: &'static str,
    observation: ObservedAudioDerivative,
    asset_id: Uuid,
    outcome: ApplyOutcome,
    source_lease_release_confirmed: bool,
    source_lease_release_error: Option<Error>,
    original_pair_observations: AudioPairReadbacks,
}

impl AdoptedAudioDerivative {
    pub fn asset_id(&self) -> Uuid {
        self.asset_id
    }
    pub fn project(&self) -> &Project {
        &self.outcome.project
    }
    pub fn import_outcome(&self) -> &ApplyOutcome {
        &self.outcome
    }
    pub fn observation(&self) -> &ObservedAudioDerivative {
        &self.observation
    }
    pub fn original_pair_observations(&self) -> Value {
        json!(&self.original_pair_observations)
    }
    /// Reports confirmation of the cleanup only; the Asset/import outcome is
    /// already complete even when cleanup was not confirmed.
    pub fn source_lease_release_confirmed(&self) -> bool {
        self.source_lease_release_confirmed
    }
    /// Original cleanup error, when observed. It never authorizes redispatch.
    pub fn source_lease_release_error(&self) -> Option<&Error> {
        self.source_lease_release_error.as_ref()
    }
}

/// An original owner-entrypoint error and any canonical import already returned
/// by StudioService. Confirmation of that import is not derivative adoption.
/// This value cannot be deserialized into execution authority or an observation.
#[derive(Debug, Serialize)]
pub struct AudioDerivativeFailure {
    original: Box<Error>,
    confirmed_import: Option<Box<ApplyOutcome>>,
    original_pair_observations: AudioPairReadbacks,
}

impl AudioDerivativeFailure {
    pub fn original(&self) -> &Error {
        self.original.as_ref()
    }

    pub fn original_pair_observations(&self) -> Value {
        json!(&self.original_pair_observations)
    }

    pub fn confirmed_import(&self) -> Option<&ApplyOutcome> {
        self.confirmed_import.as_deref()
    }

    fn after_confirmed_import(original: Error, outcome: ApplyOutcome) -> Self {
        Self {
            original: Box::new(original),
            confirmed_import: Some(Box::new(outcome)),
            original_pair_observations: AudioPairReadbacks::default(),
        }
    }
}

impl From<Error> for AudioDerivativeFailure {
    fn from(original: Error) -> Self {
        Self {
            original: Box::new(original),
            confirmed_import: None,
            original_pair_observations: AudioPairReadbacks::default(),
        }
    }
}

impl std::fmt::Display for AudioDerivativeFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        std::fmt::Display::fmt(self.original.as_ref(), formatter)
    }
}

impl std::error::Error for AudioDerivativeFailure {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        Some(self.original.as_ref())
    }
}

fn derivative_import_intent(source: &AudioDerivativeSource) -> NativeResult<FileImportIntent> {
    Ok(FileImportIntent::Asset {
        name: format!("derived-{}-{}.wav", source.profile.id(), source.digest()?),
        media_type: "audio/wav".into(),
    })
}

fn derivative_completed_receipt(
    source: &AudioDerivativeSource,
    returned: &Value,
) -> NativeResult<ProductionReceipt> {
    let receipt: ProductionReceipt = serde_json::from_value(returned["receipt"].clone())
        .map_err(|_| backend("Audio derivative has no original completed native receipt"))?;
    let native_request_id = format!("{}:audio-derive-native", source.caller_request_id);
    let context = receipt
        .payload
        .get("provider_bound_audio_derive")
        .ok_or_else(|| backend("Audio derivative receipt has no original provider binding"))?;
    let original = returned
        .pointer("/result/provider_bound/original")
        .ok_or_else(|| backend("Audio derivative omitted its original Core response"))?;
    let request_sha256 = super::request_digest(
        AUDIO_DERIVE_COMMAND,
        &json!({
            "args":source.args()?, "provider_bound_audio_derive":context,
        }),
    )?;
    if returned["replayed"] != json!(false)
        || receipt.stage != "completed"
        || receipt.project_id != source.project_id
        || receipt.generation != source.generation
        || receipt.revision != source.revision
        || receipt.command != AUDIO_DERIVE_COMMAND
        || receipt.request_id != native_request_id
        || receipt.request_sha256 != request_sha256
        || receipt.payload["audio_derive_qualification"] != json!("admitted")
        || receipt.payload["result"] != *original
        || context["connection_identity"] != json!(source.connection_identity)
    {
        return Err(backend(
            "Audio derivative receipt differs from its original source or bound execution",
        ));
    }
    // Authority and nested Driver provenance are checked by the existing R5
    // qualifier before this result is exposed. This function only binds the
    // original receipt to the source/args used by that same invocation.
    Ok(receipt)
}

struct ReadAudioDerivativeOriginals {
    artifact: Vec<u8>,
    source: Vec<u8>,
    candidate: Vec<u8>,
    process: [Vec<u8>; 4],
}

fn inspect_derivative_originals(
    source: &AudioDerivativeSource,
    result: &NativeAudioDerivative,
    bytes: &ReadAudioDerivativeOriginals,
) -> NativeResult<(AudioDerivativePcmObservation, Value)> {
    result.artifact.verify_bytes(&bytes.artifact)?;
    result.original_source.verify_bytes(&bytes.source)?;
    result.original_output.verify_bytes(&bytes.candidate)?;
    if bytes.candidate != bytes.artifact
        || inspect_pcm(source.profile, &bytes.source, None)? != source.pcm
    {
        return Err(backend(
            "Audio derivative source/candidate originals do not match the admitted WAV bytes",
        ));
    }
    let target = inspect_pcm(
        source.profile,
        &bytes.artifact,
        Some(source.pcm.sample_frames()),
    )?;
    if target.layout != result.target {
        return Err(backend(
            "Audio derivative decoded target differs from the native response clock/layout",
        ));
    }
    let terminal = verify_original_process_capture(source, result, &bytes.process)?;
    Ok((target, terminal))
}

impl ProductionCoordinator {
    fn retain_observed_audio_derivative(
        &self,
        source: &AudioDerivativeSource,
        returned: &Value,
        result: NativeAudioDerivative,
        bytes: &ReadAudioDerivativeOriginals,
    ) -> NativeResult<ObservedAudioDerivative> {
        let native_receipt = derivative_completed_receipt(source, returned)?;
        let stored = self
            .service
            .latest_production_receipt(source.project_id, &native_receipt.request_id)
            .map_err(storage_error)?;
        if stored.as_ref() != Some(&native_receipt) {
            return Err(backend(
                "Audio derivative original native receipt is no longer the retained terminal",
            ));
        }
        let (target, process_terminal) = inspect_derivative_originals(source, &result, bytes)?;
        self.recheck_audio_derivative_source(source)?;
        let source_lineage_sha256 = source.digest()?;
        let native_receipt_sha256 = derivative_digest(&native_receipt)?;
        let import_request_id = format!("{}:audio-derive-import", source.caller_request_id);
        let import_intent = derivative_import_intent(source)?;
        let payload = json!({
            "schema":AUDIO_DERIVE_LINEAGE_SCHEMA,
            "source":source,"source_lineage_sha256":source_lineage_sha256,
            "target":target,"artifact":result.artifact,
            "recipe_sha256":result.recipe_sha256,"runtime_sha256":result.runtime_sha256,
            "original_source":result.original_source,"original_output":result.original_output,
            "process_originals":result.process_originals,"process_terminal":process_terminal,
            "native_receipt_id":native_receipt.id,"native_receipt_sha256":native_receipt_sha256,
            "import_request_id":import_request_id,"import_intent":import_intent,
            "native_execution_retried":false,"human_review_or_alignment":false,
            "resampling_fidelity_assessed":false,
            "scope":"verified-original-WAV-and-same-process-clock-layout-only",
        });
        if serde_json::to_vec(&payload)
            .map_err(|_| backend("Audio derivative lineage cannot be encoded"))?
            .len()
            > AUDIO_DERIVE_LINEAGE_LIMIT
        {
            return Err(backend(
                "Audio derivative lineage exceeds its closed metadata budget",
            ));
        }
        let lineage = self
            .service
            .append_production_receipt(ProductionReceiptInput {
                project_id: source.project_id,
                generation: source.generation,
                revision: source.revision,
                request_id: format!("{}:audio-derive-lineage", source.caller_request_id),
                request_sha256: source_lineage_sha256.clone(),
                command: AUDIO_DERIVE_LINEAGE_COMMAND.into(),
                stage: "observed".into(),
                payload,
            })
            .map_err(storage_error)?;
        Ok(ObservedAudioDerivative {
            schema: AUDIO_DERIVE_LINEAGE_SCHEMA,
            source: source.clone(),
            source_lineage_sha256,
            target,
            artifact: result.artifact,
            recipe_sha256: result.recipe_sha256,
            runtime_sha256: result.runtime_sha256,
            original_source: result.original_source,
            original_output: result.original_output,
            process_originals: result.process_originals,
            native_receipt_id: native_receipt.id,
            native_receipt_sha256,
            lineage_receipt_id: lineage.id,
            import_request_id,
            import_intent,
            native_execution_retried: false,
            human_review_or_alignment: false,
            resampling_fidelity_assessed: false,
            original_native_receipt: native_receipt,
            original_lineage_receipt: lineage,
        })
    }

    fn adopt_observed_audio_derivative(
        &self,
        observed: ObservedAudioDerivative,
        captured_output: &[u8],
    ) -> Result<AdoptedAudioDerivative, AudioDerivativeFailure> {
        let FileImportIntent::Asset { .. } = &observed.import_intent else {
            return Err(backend("Audio derivative import intent changed after observation").into());
        };
        if observed.import_intent != derivative_import_intent(&observed.source)? {
            return Err(
                backend("Audio derivative import intent differs from its pinned lineage").into(),
            );
        }
        self.recheck_audio_derivative_source(&observed.source)?;
        let native_receipt = self
            .service
            .latest_production_receipt(
                observed.source.project_id,
                &observed.original_native_receipt.request_id,
            )
            .map_err(storage_error)?;
        let lineage_receipt = self
            .service
            .latest_production_receipt(
                observed.source.project_id,
                &observed.original_lineage_receipt.request_id,
            )
            .map_err(storage_error)?;
        if native_receipt.as_ref() != Some(&observed.original_native_receipt)
            || lineage_receipt.as_ref() != Some(&observed.original_lineage_receipt)
        {
            return Err(backend(
                "Audio derivative original receipts changed before canonical adoption",
            )
            .into());
        }
        // Known imports have a separate read-only recovery API. This effect path
        // never repeats an import or a native derivation after a prior outcome.
        let prior = self
            .service
            .recover_file_import(
                observed.source.project_id,
                &observed.source.stamp(),
                &observed.import_request_id,
                &observed.import_intent,
            )
            .map_err(storage_error)?;
        derivative_require_unimported(prior)?;
        let before = self
            .service
            .project(observed.source.project_id)
            .map_err(storage_error)?;
        derivative_check_asset(&before, &observed.source)?;
        let expected = BlobDescriptor {
            sha256: observed.artifact.sha256.clone(),
            size_bytes: observed.artifact.bytes,
        };
        // The provider boundary ended after both original-reference capture fences.
        // Immutable bytes and descriptor are data. The canonical importer checks
        // current independent local project authority before publication/apply.
        let (asset_id, outcome) = self.import_captured_audio_derivative(
            &before,
            &observed.source,
            &observed.import_request_id,
            &observed.import_intent,
            &expected,
            captured_output,
        )?;
        Ok(AdoptedAudioDerivative {
            schema: "motionwright.audio-derivative-adoption/2",
            observation: observed,
            asset_id,
            outcome,
            source_lease_release_confirmed: false,
            source_lease_release_error: None,
            original_pair_observations: AudioPairReadbacks::default(),
        })
    }

    fn import_captured_audio_derivative(
        &self,
        before: &Project,
        source: &AudioDerivativeSource,
        request_id: &str,
        intent: &FileImportIntent,
        expected: &BlobDescriptor,
        captured_output: &[u8],
    ) -> Result<(Uuid, ApplyOutcome), AudioDerivativeFailure> {
        let outcome = self
            .service
            .import_bytes_verified(
                source.project_id,
                &source.stamp(),
                request_id,
                captured_output,
                expected,
                intent.clone(),
            )
            .map_err(storage_error)?;
        qualify_confirmed_derivative_import(before, source, request_id, intent, expected, outcome)
    }
}

fn qualify_derivative_import(
    before: &Project,
    source: &AudioDerivativeSource,
    request_id: &str,
    intent: &FileImportIntent,
    expected: &BlobDescriptor,
    outcome: &ApplyOutcome,
) -> NativeResult<Uuid> {
    let FileImportIntent::Asset { name, media_type } = intent else {
        return Err(backend(
            "Audio derivative import outcome has a non-Asset intent",
        ));
    };
    let mut added = outcome
        .project
        .assets
        .iter()
        .filter(|asset| !before.assets.iter().any(|original| original.id == asset.id));
    let asset = added
        .next()
        .ok_or_else(|| backend("Audio derivative import returned no new Asset"))?;
    if added.next().is_some()
        || outcome.replayed
        || outcome.request_id != request_id
        || outcome.project.id != source.project_id
        || outcome.previous_revision != before.revision
        || outcome.project.generation != before.generation
        || outcome.project.revision
            != before
                .revision
                .checked_add(1)
                .ok_or_else(|| backend("Audio derivative import revision overflow"))?
        || asset.name != *name
        || asset.media_type != *media_type
        || asset.content_sha256.as_deref() != Some(expected.sha256.as_str())
        || !before
            .assets
            .iter()
            .all(|original| outcome.project.assets.iter().any(|asset| asset == original))
        || outcome.project.audio != before.audio
    {
        return Err(backend(
            "Audio derivative canonical import outcome differs from its pinned lineage",
        ));
    }
    Ok(asset.id)
}

// A real canonical recovery is already an observed outcome. Preserve it
// with the same Conflict; it never authorizes a second import or adoption.
fn derivative_require_unimported(
    prior: Option<ApplyOutcome>,
) -> Result<(), AudioDerivativeFailure> {
    if let Some(outcome) = prior {
        return Err(AudioDerivativeFailure::after_confirmed_import(
            Error::new(
                ErrorCode::Conflict,
                "Audio derivative import already has a canonical receipt; inspect that historical outcome",
            ),
            outcome,
        ));
    }
    Ok(())
}

// The importer has already returned this exact outcome. A subsequent rejection
// retains it with the original error, without constructing an Adopted value.
fn qualify_confirmed_derivative_import(
    before: &Project,
    source: &AudioDerivativeSource,
    request_id: &str,
    intent: &FileImportIntent,
    expected: &BlobDescriptor,
    outcome: ApplyOutcome,
) -> Result<(Uuid, ApplyOutcome), AudioDerivativeFailure> {
    match qualify_derivative_import(before, source, request_id, intent, expected, &outcome) {
        Ok(asset_id) => Ok((asset_id, outcome)),
        Err(original) => Err(AudioDerivativeFailure::after_confirmed_import(
            original, outcome,
        )),
    }
}

fn derivative_secondary(label: &'static str) {
    use std::io::Write;
    // A diagnostic stream failure must not replace an original native error.
    // No private path, native text or user content is written to this channel.
    let _ = writeln!(
        std::io::stderr().lock(),
        "audio derivative secondary: {label}"
    );
}

#[cfg(test)]
fn finish_derivative_use<T>(
    original: NativeResult<T>,
    released: NativeResult<()>,
) -> NativeResult<T> {
    match original {
        Err(original) => {
            if released.is_err() {
                derivative_secondary(
                    "source CAS lease release was not confirmed; durable custody requires inspection",
                );
            }
            Err(original)
        }
        Ok(value) => {
            released?;
            Ok(value)
        }
    }
}

// This context records the already-authorized owner process's directory instances.
// It is not deserializable, does not mint an SDK grant, and is never built from
// a mount observation or a WebView request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct AudioOwnerDirectoryIdentity {
    device: u64,
    inode: u64,
    owner: u32,
    mode: u32,
}

fn audio_owner_directory_identity(path: &Path) -> NativeResult<AudioOwnerDirectoryIdentity> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = super::safe_metadata(path)?;
        if !metadata.is_dir()
            || std::fs::canonicalize(path)
                .map_err(|_| backend("Audio owner directory cannot be resolved"))?
                != path
        {
            return Err(invalid(
                "Audio owner root must be an existing canonical directory",
            ));
        }
        // These fields detect replacement of the owner-declared directory.
        // They are observations, not a grant factory. The existing owner caller
        // authorizes local IO; Core independently checks the real Host grants.
        Ok(AudioOwnerDirectoryIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
            owner: metadata.uid(),
            mode: metadata.mode(),
        })
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Err(Error::new(
            ErrorCode::Unsupported,
            "Audio derivative owner directory capture requires the existing Unix route",
        ))
    }
}

#[derive(Clone)]
struct AudioDerivativeOwnerPaths {
    media: PathBuf,
    output: PathBuf,
    media_identity: AudioOwnerDirectoryIdentity,
    output_identity: AudioOwnerDirectoryIdentity,
}

impl AudioDerivativeOwnerPaths {
    fn admit(
        coordinator: &ProductionCoordinator,
        owner_media_root: &Path,
        owner_output_root: &Path,
    ) -> NativeResult<Self> {
        let actual_cas = coordinator.service.store().lock().blob_root().to_path_buf();
        if owner_media_root != actual_cas
            || owner_output_root != coordinator.client.connection().output_root
            || owner_media_root.starts_with(owner_output_root)
            || owner_output_root.starts_with(owner_media_root)
        {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Audio owner roots must select the exact Store CAS and separate connection output",
            ));
        }
        Ok(Self {
            media: owner_media_root.to_owned(),
            output: owner_output_root.to_owned(),
            media_identity: audio_owner_directory_identity(owner_media_root)?,
            output_identity: audio_owner_directory_identity(owner_output_root)?,
        })
    }

    fn check_current(&self) -> NativeResult<()> {
        if audio_owner_directory_identity(&self.media)? != self.media_identity
            || audio_owner_directory_identity(&self.output)? != self.output_identity
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio owner directory instance changed during the original operation",
            ));
        }
        Ok(())
    }
}

struct AudioDerivativeOwnerRoots {
    paths: AudioDerivativeOwnerPaths,
    media: Box<dyn ScopedRoot>,
    output: Box<dyn ScopedRoot>,
    media_identity: String,
    output_identity: String,
}

impl AudioDerivativeOwnerRoots {
    fn open(
        paths: &AudioDerivativeOwnerPaths,
        binding: &BoundAudioDerivativeCoordinator<'_>,
    ) -> NativeResult<Self> {
        let (media_identity, output_identity) =
            binding.owner_pair_identities(&paths.media, &paths.output)?;
        Self::open_verified_paths(paths, media_identity, output_identity)
    }

    fn open_verified_paths(
        paths: &AudioDerivativeOwnerPaths,
        media_identity: &str,
        output_identity: &str,
    ) -> NativeResult<Self> {
        paths.check_current()?;
        let filesystem = semwright_platform_services::filesystem();
        // These are owner-authorized local reads. The authentic Core identities
        // constrain the actual opened objects; they grant no local permission.
        // open_root_with_identity returns the same checked Box, never a pathname.
        let media =
            filesystem.open_root_with_identity(&paths.media, true, false, media_identity)?;
        let output =
            filesystem.open_root_with_identity(&paths.output, true, false, output_identity)?;
        let roots = Self {
            paths: paths.clone(),
            media,
            output,
            media_identity: media_identity.to_owned(),
            output_identity: output_identity.to_owned(),
        };
        roots.check_current()?;
        Ok(roots)
    }

    fn check_current(&self) -> NativeResult<()> {
        // The pathname check is an additional refusal fence; every capture uses
        // the retained SDK objects below, not this metadata as an IO authority.
        self.paths.check_current()?;
        if self.media.root_instance_identity()? != self.media_identity
            || self.output.root_instance_identity()? != self.output_identity
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Retained audio owner root instance changed",
            ));
        }
        Ok(())
    }

    fn verify_leased_source(&self, leased: &LeasedAudioDerivativeSource) -> NativeResult<()> {
        let captured = self.media.observe_file(
            Path::new(&leased.source.media_relative_path),
            usize::try_from(leased.source.profile.source_limit())
                .map_err(|_| invalid("Audio source capture budget does not fit this platform"))?,
        )?;
        if captured.bytes != leased.bytes {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Retained MediaRO source differs from the leased CAS bytes",
            ));
        }
        Ok(())
    }
}

impl ReadAudioDerivativeOriginals {
    fn capture(
        output: &dyn ScopedRoot,
        result: &NativeAudioDerivative,
        profile: AudioDerivativeProfile,
    ) -> NativeResult<Self> {
        let read = |artifact: &AudioDerivativeArtifact, maximum: u64| {
            if artifact.bytes > maximum {
                return Err(backend("Audio original exceeds its closed capture budget"));
            }
            let captured = output.observe_file(
                Path::new(&artifact.path),
                usize::try_from(maximum)
                    .map_err(|_| invalid("Audio capture budget does not fit this platform"))?,
            )?;
            artifact.verify_bytes(&captured.bytes)?;
            Ok(captured.bytes)
        };
        let [argv, stdout, stderr, terminal] = result.process_originals.as_slice() else {
            return Err(backend(
                "Audio derivative original process roster is incomplete",
            ));
        };
        Ok(Self {
            artifact: read(&result.artifact, profile.output_limit())?,
            source: read(&result.original_source, profile.source_limit())?,
            candidate: read(&result.original_output, profile.output_limit())?,
            process: [
                read(argv, AUDIO_DERIVE_RESPONSE_LIMIT as u64)?,
                read(stdout, AUDIO_DERIVE_STREAM_LIMIT)?,
                read(stderr, AUDIO_DERIVE_STREAM_LIMIT)?,
                read(terminal, AUDIO_DERIVE_RESPONSE_LIMIT as u64)?,
            ],
        })
    }
}

impl ProductionCoordinator {
    /// Derive one saved audio Asset through the existing MLT Host, retain its
    /// originals and adopt a separate verified Asset through StudioService.
    ///
    /// The backend caller must already own and authorize both directory roots
    /// and retain its configured daemon/Host for this operation. In the Linux
    /// fixture the owner creates the directories, grants the exact Store CAS as
    /// MLT MediaRO and its separate output as OutputRW, then invokes this method.
    /// Root arguments are owner declarations, not WebView inputs or SDK grants.
    /// Desktop/Windows bootstrap must supply its own admitted owner boundary.
    /// Core must authenticate the explicit MediaRO/OutputRW pair on one lease.
    /// The local owner independently authorizes opening the exact matched SDK
    /// roots; both retained objects survive the original-reference capture fences.
    /// Captured bytes then cross to current independent local import authority.
    /// No observation, identity string or old receipt grants a new operation.
    pub async fn derive_audio_asset_with_owner_roots(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        intent: AudioDerivativeIntent,
        owner_media_root: &Path,
        owner_output_root: &Path,
    ) -> Result<AdoptedAudioDerivative, AudioDerivativeFailure> {
        let paths = AudioDerivativeOwnerPaths::admit(self, owner_media_root, owner_output_root)?;
        let mut leased =
            self.prepare_audio_derivative_source(project_id, expected, request_id, intent)?;
        // The durable CAS lease intentionally survives cancellation of this
        // future. Only a known-ended native use reaches explicit release below.
        let mut native_may_still_use_source = false;
        let mut pair_readbacks = AudioPairReadbacks::default();
        let original = async {
            if leased.media_root != paths.media {
                return Err(Error::new(
                    ErrorCode::StaleReference,
                    "Prepared audio CAS differs from the original owner MediaRO",
                )
                .into());
            }
            paths.check_current()?;
            let bound = self
                .bind_audio_derivative_media(&paths.media, &paths.output, &mut pair_readbacks)
                .await?;
            let roots = AudioDerivativeOwnerRoots::open(&paths, &bound)?;
            roots.verify_leased_source(&leased)?;
            roots.check_current()?;
            self.recheck_audio_derivative_source(&leased.source)?;
            let args = leased.source.args()?;
            let native_request = format!("{request_id}:audio-derive-native");
            native_may_still_use_source = true;
            // This is the only native effect. R5 fixes command/provider/mutation,
            // retains the original receipt, and rejects replay or UNKNOWN reuse.
            let returned = bound
                .derive(project_id, expected, &native_request, args)
                .await?;
            native_may_still_use_source = false;
            roots.check_current()?;
            self.recheck_audio_derivative_source(&leased.source)?;
            let result = parse_native_derivative(&leased.source, &returned["result"]["data"])?;
            bound
                .revalidate_capture_pair(AudioPairCaptureStage::BeforeCapture, &mut pair_readbacks)
                .await?;
            roots.check_current()?;
            let originals = ReadAudioDerivativeOriginals::capture(
                roots.output.as_ref(),
                &result,
                intent.profile,
            )?;
            if originals.source != leased.bytes {
                return Err(backend(
                    "Native audio original differs from the exact leased CAS snapshot",
                )
                .into());
            }
            roots.check_current()?;
            bound
                .revalidate_capture_pair(AudioPairCaptureStage::AfterCapture, &mut pair_readbacks)
                .await?;
            roots.check_current()?;
            self.recheck_audio_derivative_source(&leased.source)?;
            let observed = self.retain_observed_audio_derivative(
                &leased.source,
                &returned,
                result,
                &originals,
            )?;
            // This is the explicit post-capture local adoption boundary. There is
            // no later provider path read or promise of a remote lease through SQLite.
            self.adopt_observed_audio_derivative(observed, &originals.artifact)
        }
        .await;
        let original = match original {
            Ok(mut adopted) => {
                adopted.original_pair_observations = pair_readbacks;
                Ok(adopted)
            }
            Err(mut failure) => {
                failure.original_pair_observations = pair_readbacks;
                Err(failure)
            }
        };
        if native_may_still_use_source
            && original
                .as_ref()
                .is_err_and(|error| !error.original().outcome_known)
        {
            // Preserve the complete original error and durable source lease.
            // A later owner inspection is separate from this one-shot method.
            return original;
        }
        let released = leased.release(&self.service);
        match original {
            Ok(adopted) => Ok(finish_confirmed_audio_adoption(adopted, released)),
            Err(original) => Err(finish_failed_audio_entrypoint(original, released)),
        }
    }
}

fn finish_failed_audio_entrypoint(
    original: AudioDerivativeFailure,
    released: NativeResult<()>,
) -> AudioDerivativeFailure {
    if released.is_err() {
        derivative_secondary(
            "source CAS lease release was not confirmed; the original failure and any confirmed import remain retained",
        );
    }
    original
}

fn finish_confirmed_audio_adoption(
    mut adopted: AdoptedAudioDerivative,
    released: NativeResult<()>,
) -> AdoptedAudioDerivative {
    match released {
        Ok(()) => adopted.source_lease_release_confirmed = true,
        Err(original) => {
            derivative_secondary(
                "adoption completed; source CAS lease release requires separate owner inspection",
            );
            adopted.source_lease_release_error = Some(original);
        }
    }
    // Canonical adoption already produced this exact Asset/import outcome.
    // A cleanup failure cannot erase it or turn it into a redispatch request.
    adopted
}

#[cfg(test)]
mod tests {
    use super::*;

    // Source bytes for parser/contract controls only; no native derivation or
    // authority is supplied by this fixture constructor.
    fn wav(rate: u32, channels: u16, frames: u64, mask: Option<u32>) -> Vec<u8> {
        let mut format = Vec::new();
        format.extend_from_slice(&(if mask.is_some() { 0xfffeu16 } else { 1u16 }).to_le_bytes());
        format.extend_from_slice(&channels.to_le_bytes());
        format.extend_from_slice(&rate.to_le_bytes());
        format.extend_from_slice(&(rate * u32::from(channels) * 2).to_le_bytes());
        format.extend_from_slice(&(channels * 2).to_le_bytes());
        format.extend_from_slice(&16u16.to_le_bytes());
        if let Some(mask) = mask {
            format.extend_from_slice(&22u16.to_le_bytes());
            format.extend_from_slice(&16u16.to_le_bytes());
            format.extend_from_slice(&mask.to_le_bytes());
            format.extend_from_slice(&[1, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113]);
        }
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"RIFF");
        bytes.extend_from_slice(
            &(20 + format.len() as u32 + frames as u32 * u32::from(channels) * 2).to_le_bytes(),
        );
        bytes.extend_from_slice(b"WAVEfmt ");
        bytes.extend_from_slice(&(format.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&format);
        bytes.extend_from_slice(b"data");
        bytes.extend_from_slice(&(frames as u32 * u32::from(channels) * 2).to_le_bytes());
        for index in 0..frames {
            let value = if index % 2 == 0 { 1234i16 } else { -2345i16 };
            for _ in 0..channels {
                bytes.extend_from_slice(&value.to_le_bytes());
            }
        }
        bytes
    }

    fn source() -> (AudioDerivativeSource, Vec<u8>) {
        let profile = AudioDerivativeProfile::Stereo48ToMono16;
        let bytes = wav(48_000, 2, 1_200, None);
        let source = AudioDerivativeSource {
            schema: "motionwright.audio-derivative-source/1",
            caller_request_id: "audio-source-contract-control".into(),
            project_id: Uuid::from_u128(1),
            resource: "project:test".into(),
            generation: Uuid::from_u128(2),
            revision: 7,
            source_asset_id: Uuid::from_u128(3),
            source_asset_snapshot_sha256: "a".repeat(64),
            source_sha256: hex::encode(Sha256::digest(&bytes)),
            source_bytes: bytes.len() as u64,
            source_media_type: "audio/wav".into(),
            connection_identity: "b".repeat(64),
            media_relative_path: "sha256/ab/contract-source".into(),
            profile,
            pcm: inspect_pcm(profile, &bytes, None).unwrap(),
        };
        (source, bytes)
    }

    fn response(source: &AudioDerivativeSource) -> Value {
        let name = source.output_name().unwrap();
        let target_bytes = wav(16_000, 1, 400, None);
        let target = inspect_pcm(source.profile, &target_bytes, Some(1_200)).unwrap();
        let artifact = |path: String, bytes: u64, digest: String| json!({"root":"output","path":path,"sha256":digest,"bytes":bytes});
        let target_digest = hex::encode(Sha256::digest(&target_bytes));
        json!({
            "schema":AUDIO_DERIVE_RESPONSE_SCHEMA,"profile":source.profile.id(),
            "source_sha256":source.source_sha256,"recipe_sha256":"c".repeat(64),
            "runtime_sha256":"d".repeat(64),"source":source.pcm.layout,"target":target.layout,
            "artifact":artifact(name.clone(),target_bytes.len() as u64,target_digest.clone()),
            "original_source":artifact(format!("{name}.audio-derive.source.original.wav"),source.source_bytes,source.source_sha256.clone()),
            "original_output":artifact(format!("{name}.audio-derive.output.original.wav"),target_bytes.len() as u64,target_digest),
            "process_originals":[
                artifact(format!("{name}.audio-derive.ffmpeg.argv.original.json"),2,"e".repeat(64)),
                artifact(format!("{name}.audio-derive.ffmpeg.stdout.original.bin"),0,hex::encode(Sha256::digest(b""))),
                artifact(format!("{name}.audio-derive.ffmpeg.stderr.original.bin"),0,hex::encode(Sha256::digest(b""))),
                artifact(format!("{name}.audio-derive.ffmpeg.terminal.original.json"),2,"f".repeat(64))
            ],"process_capture_complete":true
        })
    }

    #[test]
    fn audio_derivative_intent_exposes_only_two_profiles_and_an_asset_identity() {
        let intent =
            json!({"source_asset_id":Uuid::from_u128(3),"profile":"pcm16-stereo48-to-mono16-v1"});
        let parsed: AudioDerivativeIntent = serde_json::from_value(intent.clone()).unwrap();
        assert_eq!(parsed.profile, AudioDerivativeProfile::Stereo48ToMono16);
        let mut arbitrary_filter = intent.clone();
        arbitrary_filter["filter"] = json!("volume=0");
        assert!(serde_json::from_value::<AudioDerivativeIntent>(arbitrary_filter).is_err());
        let mut arbitrary_profile = intent;
        arbitrary_profile["profile"] = json!("other-sample-rate");
        assert!(serde_json::from_value::<AudioDerivativeIntent>(arbitrary_profile).is_err());
    }

    #[test]
    fn audio_derivative_profile_clocks_preserve_integral_bounds_without_trim_or_padding() {
        let down = AudioDerivativeProfile::Stereo48ToMono16;
        assert_eq!(down.output_frames(1_200).unwrap(), 400);
        assert_eq!(down.output_frames(384_000).unwrap(), 128_000);
        for frames in [0, 1_199, 1_201, 384_001, 384_003, u64::MAX] {
            assert!(down.output_frames(frames).is_err());
        }
        let up = AudioDerivativeProfile::Mono16ToDualMono48;
        assert_eq!(up.output_frames(16_000).unwrap(), 48_000);
        assert_eq!(up.output_frames(2_880_000).unwrap(), 8_640_000);
        for frames in [0, 15_999, 2_880_001, u64::MAX] {
            assert!(up.output_frames(frames).is_err());
        }
    }

    #[test]
    fn audio_derivative_source_reads_every_pcm_sample_with_the_public_wave_reader() {
        let (source, bytes) = source();
        assert_eq!(source.pcm.sample_frames(), 1_200);
        assert_eq!(source.pcm.sample_rate(), 48_000);
        assert_eq!(source.pcm.channels(), 2);
        assert!(source.pcm.all_payload_frames_read);
        assert_eq!(
            source.pcm.pcm16_interleaved_sha256(),
            hex::encode(Sha256::digest(&bytes[44..]))
        );
        assert_eq!(source.pcm.channels_identical, Some(true));
    }

    #[test]
    fn audio_derivative_layout_masks_are_observed_and_unassigned_masks_are_rejected() {
        let profile = AudioDerivativeProfile::Stereo48ToMono16;
        let conventional = inspect_pcm(profile, &wav(48_000, 2, 1_200, None), None).unwrap();
        let assigned = inspect_pcm(profile, &wav(48_000, 2, 1_200, Some(3)), None).unwrap();
        assert_eq!(
            conventional.pcm16_interleaved_sha256,
            assigned.pcm16_interleaved_sha256
        );
        assert_ne!(
            conventional.layout.channel_mask,
            assigned.layout.channel_mask
        );
        for bytes in [
            wav(48_000, 2, 1_200, Some(0)),
            wav(48_000, 2, 1_200, Some(4)),
            wav(16_000, 2, 1_200, None),
            wav(48_000, 1, 1_200, None),
        ] {
            assert!(inspect_pcm(profile, &bytes, None).is_err());
        }
    }

    #[test]
    fn audio_derivative_truncated_or_unexpected_pcm_payload_never_becomes_a_source() {
        let (source, mut bytes) = source();
        bytes.pop();
        assert!(inspect_pcm(source.profile, &bytes, None).is_err());
        let mut extra = wav(48_000, 2, 1_200, None);
        extra.extend_from_slice(&[0, 0]);
        assert!(inspect_pcm(source.profile, &extra, None).is_err());
        assert!(inspect_pcm(source.profile, &wav(48_000, 2, 1_201, None), None).is_err());
    }

    #[test]
    fn audio_derivative_dual_mono_requires_equal_channels_in_all_frames() {
        let profile = AudioDerivativeProfile::Mono16ToDualMono48;
        let mut bytes = wav(48_000, 2, 48_000, Some(3));
        let observed = inspect_pcm(profile, &bytes, Some(16_000)).unwrap();
        assert_eq!(observed.channels_identical, Some(true));
        let last = bytes.len() - 2;
        bytes[last..].copy_from_slice(&42i16.to_le_bytes());
        assert!(inspect_pcm(profile, &bytes, Some(16_000)).is_err());
    }

    #[test]
    fn audio_derivative_names_bind_request_asset_revision_generation_and_cas() {
        let (source, _) = source();
        let name = source.output_name().unwrap();
        let mut variants = Vec::new();
        let mut changed = source.clone();
        changed.caller_request_id.push('x');
        variants.push(changed);
        let mut changed = source.clone();
        changed.source_asset_id = Uuid::from_u128(4);
        variants.push(changed);
        let mut changed = source.clone();
        changed.generation = Uuid::from_u128(5);
        variants.push(changed);
        let mut changed = source.clone();
        changed.revision += 1;
        variants.push(changed);
        let mut changed = source.clone();
        changed.source_sha256 = "e".repeat(64);
        variants.push(changed);
        let mut changed = source.clone();
        changed.connection_identity = "f".repeat(64);
        variants.push(changed);
        for changed in variants {
            assert_ne!(changed.output_name().unwrap(), name);
        }
        let args = source.args().unwrap();
        assert_eq!(args["source"]["root"], "media");
        assert_eq!(args["source"]["sample_frames"], 1_200);
        assert_eq!(args["output_path"], name);
        assert_eq!(source.stamp().revision, 7);
    }

    #[test]
    fn audio_derivative_native_dto_rejects_forged_source_profile_clock_and_roster() {
        let (source, _) = source();
        let good = response(&source);
        parse_native_derivative(&source, &good).unwrap();
        let mut cases = Vec::new();
        let mut bad = good.clone();
        bad["source_sha256"] = json!("f".repeat(64));
        cases.push(bad);
        let mut bad = good.clone();
        bad["profile"] = json!("pcm16-mono16-to-dualmono48-v1");
        cases.push(bad);
        let mut bad = good.clone();
        bad["target"]["sample_frames"] = json!(401);
        cases.push(bad);
        let mut bad = good.clone();
        bad["target"]["channel_mask"] = json!(0);
        cases.push(bad);
        let mut bad = good.clone();
        bad["artifact"]["root"] = json!("media");
        cases.push(bad);
        let mut bad = good.clone();
        bad["artifact"]["path"] = json!("../elsewhere.wav");
        cases.push(bad);
        let mut bad = good.clone();
        bad["process_capture_complete"] = json!(false);
        cases.push(bad);
        let mut bad = good.clone();
        bad["process_originals"].as_array_mut().unwrap().pop();
        cases.push(bad);
        let mut bad = good;
        bad["unexpected"] = json!(true);
        cases.push(bad);
        for bad in cases {
            assert!(parse_native_derivative(&source, &bad).is_err());
        }
    }

    #[test]
    fn audio_derivative_missing_mask_is_not_equivalent_to_an_observed_null_mask() {
        let (source, _) = source();
        let mut value = response(&source);
        parse_native_derivative(&source, &value).unwrap();
        value["target"]
            .as_object_mut()
            .unwrap()
            .remove("channel_mask");
        assert!(parse_native_derivative(&source, &value).is_err());
    }

    #[test]
    fn audio_derivative_original_candidate_must_equal_the_published_artifact() {
        let (source, _) = source();
        let mut value = response(&source);
        value["original_output"]["sha256"] = json!("a".repeat(64));
        assert!(parse_native_derivative(&source, &value).is_err());
        let mut value = response(&source);
        value["original_source"]["bytes"] = json!(source.source_bytes + 1);
        assert!(parse_native_derivative(&source, &value).is_err());
    }

    #[test]
    fn audio_derivative_zero_byte_streams_require_the_real_empty_sha256() {
        let (source, _) = source();
        let mut value = response(&source);
        parse_native_derivative(&source, &value).unwrap();
        value["process_originals"][1]["sha256"] = json!("a".repeat(64));
        assert!(parse_native_derivative(&source, &value).is_err());
    }

    #[test]
    fn audio_derivative_actual_file_bytes_must_match_the_native_artifact() {
        let (source, _) = source();
        let result = parse_native_derivative(&source, &response(&source)).unwrap();
        let mut bytes = wav(16_000, 1, 400, None);
        result.artifact.verify_bytes(&bytes).unwrap();
        *bytes.last_mut().unwrap() ^= 1;
        assert!(result.artifact.verify_bytes(&bytes).is_err());
    }
    fn process_originals(source: &AudioDerivativeSource) -> (NativeAudioDerivative, [Vec<u8>; 4]) {
        let mut native = parse_native_derivative(source, &response(source)).unwrap();
        let stdout = Vec::new();
        let stderr = vec![b'd', 0, 0xfe];
        let stream = |bytes: &[u8]| {
            json!({
                "observed_bytes":bytes.len(),"retained_bytes":bytes.len(),
                "retained_sha256":hex::encode(Sha256::digest(bytes)),"eof_observed":true,
                "read_error":null,"reader_panicked":false,"drain_stopped":false,
                "truncated":false,"complete":true,
            })
        };
        let argv = serde_json::to_vec(&json!({
            "encoding":"unix-os-bytes-hex","executable":hex::encode(b"/pinned/ffmpeg"),
            "argv":[hex::encode(b"-nostdin")],"cwd":hex::encode(b"/owned/work"),
            "timeout_millis":300000,"cpu_seconds":300,"address_space_bytes":536870912,
            "environment_included":false,"native_execution_retried":false,
        }))
        .unwrap();
        let terminal = serde_json::to_vec(&json!({
            "schema":"semwright.audio-derivative-process-original/1",
            "profile":source.profile.id(),"source_sha256":source.source_sha256,
            "recipe_sha256":native.recipe_sha256,"ffmpeg_sha256":native.runtime_sha256,
            "process_result_received":true,"retained_prefix_limit":262144,
            "per_stream_total_limit":262144,"custody_error":null,
            "capture_scope":"same-FFmpeg-spawn-before-checked-and-WAV-validation",
            "physical_acceptance":false,
            "process":{
                "spawn_attempted":true,"pid":123,"terminal_observed":true,
                "exit_code":0,"term_signal":null,"cancelled":false,"timed_out":false,
                "output_exceeded":false,"drain_stop_reason":null,"stdout":stream(&stdout),
                "stderr":stream(&stderr),"supervisor_error":null,"capture_complete":true,
                "native_execution_retried":false,
            },
        }))
        .unwrap();
        let files = [argv, stdout, stderr, terminal];
        for (artifact, bytes) in native.process_originals.iter_mut().zip(&files) {
            artifact.bytes = bytes.len() as u64;
            artifact.sha256 = hex::encode(Sha256::digest(bytes));
        }
        (native, files)
    }

    fn rebind_terminal(
        native: &mut NativeAudioDerivative,
        files: &mut [Vec<u8>; 4],
        terminal: &Value,
    ) {
        files[3] = serde_json::to_vec(terminal).unwrap();
        native.process_originals[3].bytes = files[3].len() as u64;
        native.process_originals[3].sha256 = hex::encode(Sha256::digest(&files[3]));
    }

    #[test]
    fn audio_derivative_original_process_requires_the_complete_same_spawn_streams() {
        let (source, _) = source();
        let (native, originals) = process_originals(&source);
        let terminal = verify_original_process_capture(&source, &native, &originals).unwrap();
        assert_eq!(terminal["process"]["stdout"]["retained_bytes"], 0);
        assert_eq!(terminal["process"]["stderr"]["retained_bytes"], 3);
        for (pointer, value) in [
            ("/process/terminal_observed", json!(false)),
            ("/process/exit_code", json!(7)),
            ("/process/exit_code", Value::Null),
            ("/process/timed_out", json!(true)),
            ("/process/drain_stop_reason", json!("deadline")),
            ("/process/supervisor_error", json!({"code":"Interrupted"})),
            ("/process/stdout/eof_observed", json!(false)),
            ("/process/stderr/reader_panicked", json!(true)),
            ("/process/stderr/read_error", json!({"kind":"Other"})),
            ("/process/stderr/truncated", json!(true)),
            ("/process/stderr/drain_stopped", json!(true)),
            ("/process/stdout/observed_bytes", json!(1)),
        ] {
            let mut changed = terminal.clone();
            *changed.pointer_mut(pointer).unwrap() = value;
            let mut native = native.clone();
            let mut files = originals.clone();
            rebind_terminal(&mut native, &mut files, &changed);
            assert!(
                verify_original_process_capture(&source, &native, &files).is_err(),
                "{pointer}"
            );
        }
    }

    #[test]
    fn audio_derivative_original_capture_rejects_rehashing_a_foreign_clock_or_runtime() {
        let (source, _) = source();
        let (native, originals) = process_originals(&source);
        let terminal: Value = serde_json::from_slice(&originals[3]).unwrap();
        for (key, value) in [
            ("profile", json!("pcm16-mono16-to-dualmono48-v1")),
            ("source_sha256", json!("0".repeat(64))),
            ("recipe_sha256", json!("1".repeat(64))),
            ("ffmpeg_sha256", json!("2".repeat(64))),
            ("retained_prefix_limit", json!(262145)),
        ] {
            let mut changed = terminal.clone();
            changed[key] = value;
            let mut native = native.clone();
            let mut files = originals.clone();
            rebind_terminal(&mut native, &mut files, &changed);
            assert!(
                verify_original_process_capture(&source, &native, &files).is_err(),
                "{key}"
            );
        }
        let mut changed = originals;
        changed[2].push(b'!');
        assert!(verify_original_process_capture(&source, &native, &changed).is_err());
    }

    #[test]
    fn audio_derivative_original_metadata_is_bounded_closed_and_distinct_from_native_execution() {
        let (source, _) = source();
        let (native, originals) = process_originals(&source);
        let mut argv: Value = serde_json::from_slice(&originals[0]).unwrap();
        argv["environment"] = json!({"UNTRUSTED":"not part of this contract"});
        let mut files = originals.clone();
        files[0] = serde_json::to_vec(&argv).unwrap();
        let mut forged = native.clone();
        forged.process_originals[0].bytes = files[0].len() as u64;
        forged.process_originals[0].sha256 = hex::encode(Sha256::digest(&files[0]));
        assert!(verify_original_process_capture(&source, &forged, &files).is_err());
        assert!(parse_original_json(&vec![b' '; AUDIO_DERIVE_RESPONSE_LIMIT + 1]).is_err());
        assert!(parse_original_json(b"{}").is_ok());
        assert!(parse_original_json(b"{} trailing").is_err());
    }

    #[cfg(unix)]
    #[test]
    fn audio_derivative_original_reader_accepts_only_real_empty_bytes_and_keeps_public_floor() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        std::fs::write(root.join("stdout.original.bin"), []).unwrap();
        let empty = hex::encode(Sha256::digest(b""));
        assert_eq!(
            super::super::read_verified_production_original(
                &root,
                "stdout.original.bin",
                &empty,
                0,
                AUDIO_DERIVE_STREAM_LIMIT,
            )
            .unwrap(),
            Vec::<u8>::new()
        );
        assert!(
            super::super::read_verified_production_artifact(
                &root,
                "stdout.original.bin",
                &empty,
                AUDIO_DERIVE_STREAM_LIMIT,
            )
            .is_err()
        );
        assert!(
            super::super::read_verified_production_original(
                &root,
                "absent.original.bin",
                &empty,
                0,
                AUDIO_DERIVE_STREAM_LIMIT,
            )
            .is_err()
        );
        assert!(
            super::super::read_verified_production_original(
                &root,
                "stdout.original.bin",
                &"0".repeat(64),
                0,
                AUDIO_DERIVE_STREAM_LIMIT,
            )
            .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn audio_derivative_original_reader_checks_expected_count_even_with_the_correct_hash() {
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let bytes = b"retained stderr";
        std::fs::write(root.join("stderr.original.bin"), bytes).unwrap();
        let sha = hex::encode(Sha256::digest(bytes));
        assert_eq!(
            super::super::read_verified_production_original(
                &root,
                "stderr.original.bin",
                &sha,
                bytes.len() as u64,
                AUDIO_DERIVE_STREAM_LIMIT,
            )
            .unwrap(),
            bytes
        );
        for count in [0, bytes.len() as u64 - 1, bytes.len() as u64 + 1] {
            assert!(
                super::super::read_verified_production_original(
                    &root,
                    "stderr.original.bin",
                    &sha,
                    count,
                    AUDIO_DERIVE_STREAM_LIMIT,
                )
                .is_err()
            );
        }
        assert_eq!(
            std::fs::read(root.join("stderr.original.bin")).unwrap(),
            bytes
        );
    }

    #[test]
    fn audio_derivative_cas_relative_source_preserves_the_verified_path_without_inventing_layout() {
        let root = Path::new("owned-cas");
        assert_eq!(
            cas_relative_source(root, &root.join("actual").join("pinned-original")).unwrap(),
            "actual/pinned-original"
        );
        for path in [
            Path::new("elsewhere/original"),
            Path::new("owned-cas/../escape"),
            root,
        ] {
            assert!(cas_relative_source(root, path).is_err());
        }
    }

    #[test]
    fn audio_derivative_existing_unknown_receipt_never_becomes_fresh_or_refunded() {
        let receipt = ProductionReceipt {
            id: Uuid::from_u128(1),
            project_id: Uuid::from_u128(2),
            generation: Uuid::from_u128(3),
            revision: 1,
            request_id: "already-dispatched".into(),
            request_sha256: "a".repeat(64),
            command: AUDIO_DERIVE_COMMAND.into(),
            stage: "outcome_unknown".into(),
            payload: json!({"mutation":true}),
            created_at: "2026-10-26T00:00:00Z".into(),
        };
        let error = derivative_require_fresh(Some(&receipt)).unwrap_err();
        assert_eq!(error.code, ErrorCode::Conflict);
        assert!(!error.outcome_known);
        let mut dispatching = receipt.clone();
        dispatching.stage = "dispatching".into();
        assert!(
            !derivative_require_fresh(Some(&dispatching))
                .unwrap_err()
                .outcome_known
        );
        let mut completed = receipt;
        completed.stage = "completed".into();
        assert!(
            derivative_require_fresh(Some(&completed))
                .unwrap_err()
                .outcome_known
        );
        assert!(derivative_require_fresh(None).is_ok());
    }
    #[cfg(unix)]
    fn source_only_coordinator() -> (
        tempfile::TempDir,
        StudioService,
        Project,
        ProductionCoordinator,
        AudioDerivativeIntent,
        Vec<u8>,
    ) {
        use std::os::unix::fs::PermissionsExt;
        let root = tempfile::tempdir().unwrap();
        let base = root.path().canonicalize().unwrap();
        let service = StudioService::open(base.join("studio.sqlite3")).unwrap();
        let project = service
            .create_project("Audio derivative source-only controls")
            .unwrap();
        let bytes = wav(48_000, 2, 1_200, None);
        let source = base.join("original.wav");
        std::fs::write(&source, &bytes).unwrap();
        let outcome = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                "original-asset",
                &source,
                &BlobDescriptor {
                    sha256: hex::encode(Sha256::digest(&bytes)),
                    size_bytes: bytes.len() as u64,
                },
                FileImportIntent::Asset {
                    name: "original.wav".into(),
                    media_type: "audio/wav".into(),
                },
            )
            .unwrap();
        let project = outcome.project;
        let intent = AudioDerivativeIntent {
            source_asset_id: project.assets[0].id,
            profile: AudioDerivativeProfile::Stereo48ToMono16,
        };
        // Inert bytes satisfy configuration shape only. This fixture never calls
        // bind/execute and contains no executable script or native issuer.
        let executable = base.join("never-execute-source-fixture");
        std::fs::write(&executable, b"source-only inert bytes").unwrap();
        let session = base.join("session.json");
        std::fs::write(&session, b"{}").unwrap();
        let socket = base.join("not-a-live-socket");
        std::fs::write(&socket, []).unwrap();
        let output = base.join("output");
        std::fs::create_dir(&output).unwrap();
        for path in [&executable, &session, &socket] {
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        std::fs::set_permissions(&output, std::fs::Permissions::from_mode(0o700)).unwrap();
        let connection = super::super::ProductionConnection {
            schema: super::super::CONNECTION_SCHEMA.into(),
            executable_sha256: hex::encode(Sha256::digest(b"source-only inert bytes")),
            executable,
            socket,
            session_file: session,
            output_root: output,
            resource: project.resource_key(),
        };
        let coordinator = ProductionCoordinator::new(service.clone(), connection).unwrap();
        (root, service, project, coordinator, intent, bytes)
    }

    #[cfg(unix)]
    #[test]
    fn audio_derivative_source_uses_the_existing_verified_cas_path_and_durable_lease() {
        let (_root, service, project, coordinator, intent, original) = source_only_coordinator();
        let before = service.project(project.id).unwrap();
        let mut leased = coordinator
            .prepare_audio_derivative_source(
                project.id,
                &project.stamp(),
                "derive-source-control",
                intent,
            )
            .unwrap();
        assert_eq!(leased.bytes, original);
        assert!(leased.lease.is_some());
        assert_eq!(leased.media_root, service.store().lock().blob_root());
        assert_eq!(
            leased.media_root.join(&leased.source.media_relative_path),
            service
                .store()
                .lock()
                .verified_blob_path(&leased.source.source_sha256)
                .unwrap()
        );
        assert_eq!(
            leased.source.args().unwrap()["source"]["path"],
            json!(leased.source.media_relative_path)
        );
        assert_eq!(service.project(project.id).unwrap(), before);
        assert!(
            service
                .production_receipts(project.id, 32)
                .unwrap()
                .is_empty()
        );
        leased.release(&service).unwrap();
        assert!(leased.lease.is_none());
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&leased.source.source_sha256, 2 * 1024 * 1024)
                .unwrap(),
            original
        );
    }

    #[cfg(unix)]
    #[test]
    fn audio_derivative_source_rejects_stale_scope_before_reading_a_missing_cas_blob() {
        let (_root, service, project, coordinator, intent, _) = source_only_coordinator();
        let path = service
            .store()
            .lock()
            .verified_blob_path(project.assets[0].content_sha256.as_deref().unwrap())
            .unwrap();
        std::fs::remove_file(path).unwrap();
        let mut references = Vec::new();
        let mut reference = project.stamp();
        reference.resource.push_str("-foreign");
        references.push(reference);
        let mut reference = project.stamp();
        reference.generation = Uuid::from_u128(991);
        references.push(reference);
        let mut reference = project.stamp();
        reference.revision += 1;
        references.push(reference);
        for reference in references {
            let error = coordinator
                .prepare_audio_derivative_source(
                    project.id,
                    &reference,
                    "wrong-source-scope",
                    intent,
                )
                .err()
                .unwrap();
            assert_eq!(error.code, ErrorCode::StaleReference);
        }
        assert!(
            service
                .production_receipts(project.id, 32)
                .unwrap()
                .is_empty()
        );
    }

    #[cfg(unix)]
    #[test]
    fn audio_derivative_source_recheck_detects_both_project_edits_and_asset_snapshot_changes() {
        let (_root, service, project, coordinator, intent, _) = source_only_coordinator();
        let mut leased = coordinator
            .prepare_audio_derivative_source(
                project.id,
                &project.stamp(),
                "source-edit-control",
                intent,
            )
            .unwrap();
        coordinator
            .recheck_audio_derivative_source(&leased.source)
            .unwrap();
        let mut changed_asset = project.clone();
        changed_asset.assets[0].name = "different authored identity".into();
        assert_eq!(
            derivative_check_asset(&changed_asset, &leased.source)
                .unwrap_err()
                .code,
            ErrorCode::StaleReference
        );
        service
            .apply(
                project.id,
                &project.stamp(),
                "authored-edit",
                &motionwright_domain::Change::RenameProject {
                    title: "Real project edit before dispatch".into(),
                },
            )
            .unwrap();
        assert_eq!(
            coordinator
                .recheck_audio_derivative_source(&leased.source)
                .unwrap_err()
                .code,
            ErrorCode::StaleReference
        );
        leased.release(&service).unwrap();
    }

    fn completed_return(source: &AudioDerivativeSource) -> Value {
        let context = json!({"connection_identity":source.connection_identity});
        let original = json!({"fixture":"source-only-scope-control-no-issuer"});
        let receipt = ProductionReceipt {
            id: Uuid::from_u128(901),
            project_id: source.project_id,
            generation: source.generation,
            revision: source.revision,
            request_id: format!("{}:audio-derive-native", source.caller_request_id),
            request_sha256: super::super::request_digest(
                AUDIO_DERIVE_COMMAND,
                &json!({
                    "args":source.args().unwrap(),"provider_bound_audio_derive":context,
                }),
            )
            .unwrap(),
            command: AUDIO_DERIVE_COMMAND.into(),
            stage: "completed".into(),
            payload: json!({"provider_bound_audio_derive":context,"audio_derive_qualification":"admitted","result":original}),
            created_at: "2026-10-26T00:00:00Z".into(),
        };
        json!({"replayed":false,"receipt":receipt,"result":{"provider_bound":{"original":original}}})
    }

    #[test]
    fn audio_derivative_receipt_scope_rejects_foreign_source_clock_and_stored_result_replay() {
        let (source, _) = source();
        let valid = completed_return(&source);
        // The private comparison checks receipt identity only. Authenticity is
        // supplied by R5's real callsite and is deliberately not mocked here.
        assert!(derivative_completed_receipt(&source, &valid).is_ok());
        for (pointer, value) in [
            ("/replayed", json!(true)),
            ("/receipt/project_id", json!(Uuid::from_u128(8))),
            ("/receipt/generation", json!(Uuid::from_u128(9))),
            ("/receipt/revision", json!(source.revision + 1)),
            ("/receipt/stage", json!("outcome_unknown")),
            ("/receipt/request_id", json!("foreign-request")),
            ("/receipt/request_sha256", json!("0".repeat(64))),
            ("/receipt/command", json!("driver.mlt-video.av.mux")),
            (
                "/receipt/payload/audio_derive_qualification",
                json!("historical"),
            ),
            ("/result/provider_bound/original", json!({"changed":true})),
        ] {
            let mut changed = valid.clone();
            *changed.pointer_mut(pointer).unwrap() = value;
            assert!(
                derivative_completed_receipt(&source, &changed).is_err(),
                "{pointer}"
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn audio_derivative_canonical_import_outcome_keeps_original_cas_and_exact_request_scope() {
        let (root, service, project, coordinator, intent, original) = source_only_coordinator();
        let mut leased = coordinator
            .prepare_audio_derivative_source(
                project.id,
                &project.stamp(),
                "local-import-control",
                intent,
            )
            .unwrap();
        // This is an importer control using synthetic WAV bytes, not a claim of
        // native resampling, an authenticated Driver result or physical G2.
        let bytes = wav(16_000, 1, 400, None);
        let path = root.path().join("synthetic-target.wav");
        std::fs::write(&path, &bytes).unwrap();
        let expected = BlobDescriptor {
            sha256: hex::encode(Sha256::digest(&bytes)),
            size_bytes: bytes.len() as u64,
        };
        let import = derivative_import_intent(&leased.source).unwrap();
        let request_id = "local-import-control:audio-derive-import";
        let outcome = service
            .import_file_verified(
                project.id,
                &project.stamp(),
                request_id,
                &path,
                &expected,
                import.clone(),
            )
            .unwrap();
        let id = qualify_derivative_import(
            &project,
            &leased.source,
            request_id,
            &import,
            &expected,
            &outcome,
        )
        .unwrap();
        assert_ne!(id, intent.source_asset_id);
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            bytes
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&leased.source.source_sha256, leased.source.source_bytes)
                .unwrap(),
            original
        );
        assert_eq!(outcome.project.audio, project.audio);
        let mut changed = outcome.clone();
        changed.request_id.push_str("-foreign");
        assert!(
            qualify_derivative_import(
                &project,
                &leased.source,
                request_id,
                &import,
                &expected,
                &changed
            )
            .is_err()
        );
        let mut changed = outcome.clone();
        changed.project.id = Uuid::from_u128(92);
        assert!(
            qualify_derivative_import(
                &project,
                &leased.source,
                request_id,
                &import,
                &expected,
                &changed
            )
            .is_err()
        );
        let mut changed = outcome.clone();
        changed.project.assets[0].name.push_str("-changed");
        assert!(
            qualify_derivative_import(
                &project,
                &leased.source,
                request_id,
                &import,
                &expected,
                &changed
            )
            .is_err()
        );
        // A later qualification failure cannot erase the real canonical import.
        let recovered = service
            .recover_file_import(project.id, &outcome.project.stamp(), request_id, &import)
            .unwrap()
            .unwrap();
        let mut historical = outcome;
        historical.replayed = true;
        assert_eq!(recovered, historical);
        leased.release(&service).unwrap();
    }
    #[test]
    fn audio_derivative_secondary_release_failure_preserves_the_whole_original_unknown() {
        let original = Error::new(
            ErrorCode::Timeout,
            "same original uncertain native operation",
        )
        .uncertain();
        let expected = serde_json::to_value(&original).unwrap();
        let result =
            finish_derivative_use::<()>(Err(original), Err(backend("secondary lease release")));
        assert_eq!(serde_json::to_value(result.unwrap_err()).unwrap(), expected);
        let primary = Error::new(ErrorCode::PermissionDenied, "same original known rejection");
        let expected = serde_json::to_value(&primary).unwrap();
        assert_eq!(
            serde_json::to_value(finish_derivative_use::<()>(Err(primary), Ok(())).unwrap_err())
                .unwrap(),
            expected
        );
        let release = backend("release failed after otherwise successful observation");
        let expected = serde_json::to_value(&release).unwrap();
        assert_eq!(
            serde_json::to_value(finish_derivative_use(Ok(7), Err(release)).unwrap_err()).unwrap(),
            expected
        );
        assert_eq!(finish_derivative_use(Ok(7), Ok(())).unwrap(), 7);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn audio_owner_entrypoint_rejects_a_foreign_cas_before_dispatch_or_import() {
        use std::os::unix::fs::PermissionsExt;
        let (root, service, project, coordinator, intent, _) = source_only_coordinator();
        let media = service.store().lock().blob_root().to_path_buf();
        let output = coordinator.client.connection().output_root.clone();
        AudioDerivativeOwnerPaths::admit(&coordinator, &media, &output)
            .unwrap()
            .check_current()
            .unwrap();
        let foreign = root.path().join("foreign-media");
        std::fs::create_dir(&foreign).unwrap();
        std::fs::set_permissions(&foreign, std::fs::Permissions::from_mode(0o700)).unwrap();
        let before = service.production_receipt_watermark(project.id).unwrap();
        let error = coordinator
            .derive_audio_asset_with_owner_roots(
                project.id,
                &project.stamp(),
                "foreign-owner-control",
                intent,
                &foreign,
                &output,
            )
            .await
            .unwrap_err();
        assert_eq!(error.original().code, ErrorCode::PermissionDenied);
        assert!(error.original().outcome_known);
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service.production_receipt_watermark(project.id).unwrap(),
            before
        );
        assert!(AudioDerivativeOwnerPaths::admit(&coordinator, &media, &media).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn audio_owner_directory_replacement_invalidates_the_same_declared_path() {
        use std::os::unix::fs::PermissionsExt;
        let (root, service, project, coordinator, _, _) = source_only_coordinator();
        let media = service.store().lock().blob_root().to_path_buf();
        let output = coordinator.client.connection().output_root.clone();
        let admitted = AudioDerivativeOwnerPaths::admit(&coordinator, &media, &output).unwrap();
        std::fs::rename(&output, root.path().join("original-output-instance")).unwrap();
        std::fs::create_dir(&output).unwrap();
        std::fs::set_permissions(&output, std::fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(
            admitted.check_current().unwrap_err().code,
            ErrorCode::StaleReference
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn audio_owner_common_capture_requires_original_zero_stream_and_exact_binary_bytes() {
        use std::os::unix::fs::PermissionsExt;
        let directory = tempfile::tempdir().unwrap();
        let root = directory.path().canonicalize().unwrap();
        let (source, source_bytes) = source();
        let (native, process) = process_originals(&source);
        let target = wav(16_000, 1, 400, None);
        for (artifact, bytes) in [
            (&native.artifact, &target),
            (&native.original_source, &source_bytes),
            (&native.original_output, &target),
        ]
        .into_iter()
        .chain(native.process_originals.iter().zip(&process))
        {
            let path = root.join(&artifact.path);
            std::fs::write(&path, bytes).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        let retained = semwright_platform_services::filesystem()
            .open_root(&root, true, false)
            .unwrap();
        let captured =
            ReadAudioDerivativeOriginals::capture(retained.as_ref(), &native, source.profile)
                .unwrap();
        assert_eq!(captured.source, source_bytes);
        assert_eq!(captured.artifact, target);
        assert_eq!(captured.process, process);
        inspect_derivative_originals(&source, &native, &captured).unwrap();
        let stdout = root.join(&native.process_originals[1].path);
        std::fs::remove_file(&stdout).unwrap();
        assert!(
            ReadAudioDerivativeOriginals::capture(retained.as_ref(), &native, source.profile)
                .is_err()
        );
        std::fs::write(&stdout, []).unwrap();
        std::fs::set_permissions(&stdout, std::fs::Permissions::from_mode(0o600)).unwrap();
        let stderr = root.join(&native.process_originals[2].path);
        std::fs::write(&stderr, b"changed").unwrap();
        assert!(
            ReadAudioDerivativeOriginals::capture(retained.as_ref(), &native, source.profile)
                .is_err()
        );
    }

    #[cfg(unix)]
    #[test]
    fn audio_owner_cleanup_failure_preserves_the_completed_canonical_import_without_replay() {
        for release_fails in [false, true] {
            let (root, service, before, coordinator, intent, source_bytes) =
                source_only_coordinator();
            let mut leased = coordinator
                .prepare_audio_derivative_source(
                    before.id,
                    &before.stamp(),
                    "cleanup-after-import-control",
                    intent,
                )
                .unwrap();
            let target = wav(16_000, 1, 400, None);
            let path = root.path().join("source-only-import-target.wav");
            std::fs::write(&path, &target).unwrap();
            let expected = BlobDescriptor {
                sha256: hex::encode(Sha256::digest(&target)),
                size_bytes: target.len() as u64,
            };
            let import_intent = derivative_import_intent(&leased.source).unwrap();
            let request_id = "cleanup-after-import-control:audio-derive-import";
            let outcome = service
                .import_file_verified(
                    before.id,
                    &before.stamp(),
                    request_id,
                    &path,
                    &expected,
                    import_intent.clone(),
                )
                .unwrap();
            let asset_id = qualify_derivative_import(
                &before,
                &leased.source,
                request_id,
                &import_intent,
                &expected,
                &outcome,
            )
            .unwrap();
            let expected_outcome = outcome.clone();
            let (native, _) = process_originals(&leased.source);
            // The actual canonical import above is the component under test.
            // These private observation fields are explicitly synthetic scope
            // fixtures, never appended as a native receipt and never dispatched.
            let fixture_receipt: ProductionReceipt =
                serde_json::from_value(completed_return(&leased.source)["receipt"].clone())
                    .unwrap();
            let observed = ObservedAudioDerivative {
                schema: AUDIO_DERIVE_LINEAGE_SCHEMA,
                source: leased.source.clone(),
                source_lineage_sha256: leased.source.digest().unwrap(),
                target: inspect_pcm(intent.profile, &target, Some(1_200)).unwrap(),
                artifact: native.artifact,
                recipe_sha256: native.recipe_sha256,
                runtime_sha256: native.runtime_sha256,
                original_source: native.original_source,
                original_output: native.original_output,
                process_originals: native.process_originals,
                native_receipt_id: fixture_receipt.id,
                native_receipt_sha256: derivative_digest(&fixture_receipt).unwrap(),
                lineage_receipt_id: Uuid::from_u128(902),
                import_request_id: request_id.into(),
                import_intent: import_intent.clone(),
                native_execution_retried: false,
                human_review_or_alignment: false,
                resampling_fidelity_assessed: false,
                original_native_receipt: fixture_receipt.clone(),
                original_lineage_receipt: fixture_receipt,
            };
            let adopted = AdoptedAudioDerivative {
                schema: "motionwright.audio-derivative-adoption/2",
                observation: observed,
                asset_id,
                outcome,
                source_lease_release_confirmed: false,
                source_lease_release_error: None,
                original_pair_observations: AudioPairReadbacks::default(),
            };
            let synthetic_release_error = backend("source-only injected cleanup error");
            let expected_error = serde_json::to_value(&synthetic_release_error).unwrap();
            let released = if release_fails {
                Err(synthetic_release_error)
            } else {
                leased.release(&service)
            };
            let retained = finish_confirmed_audio_adoption(adopted, released);
            assert_eq!(retained.asset_id(), asset_id);
            assert_eq!(retained.import_outcome(), &expected_outcome);
            assert_eq!(retained.project(), &service.project(before.id).unwrap());
            assert_eq!(retained.source_lease_release_confirmed(), !release_fails);
            if release_fails {
                assert_eq!(
                    serde_json::to_value(retained.source_lease_release_error()).unwrap(),
                    expected_error
                );
                // Test cleanup only: this branch did not execute a native effect.
                leased.release(&service).unwrap();
            } else {
                assert!(retained.source_lease_release_error().is_none());
            }
            assert_eq!(
                service
                    .production_receipt_watermark(before.id)
                    .unwrap()
                    .count,
                0
            );
            assert_eq!(
                service
                    .store()
                    .lock()
                    .read_blob(&leased.source.source_sha256, leased.source.source_bytes)
                    .unwrap(),
                source_bytes
            );
            assert_eq!(
                service
                    .store()
                    .lock()
                    .read_blob(&expected.sha256, expected.size_bytes)
                    .unwrap(),
                target
            );
            let recovered = service
                .recover_file_import(
                    before.id,
                    &expected_outcome.project.stamp(),
                    request_id,
                    &import_intent,
                )
                .unwrap()
                .unwrap();
            let mut historical = expected_outcome;
            historical.replayed = true;
            assert_eq!(recovered, historical);
        }
    }

    #[cfg(unix)]
    #[test]
    fn audio_owner_confirmed_replay_retains_import_after_qualification_and_cleanup_failure() {
        let (root, service, before, coordinator, intent, source_bytes) = source_only_coordinator();
        let mut leased = coordinator
            .prepare_audio_derivative_source(
                before.id,
                &before.stamp(),
                "confirmed-import-failure-control",
                intent,
            )
            .unwrap();
        let target = wav(16_000, 1, 400, None);
        let path = root.path().join("confirmed-import-target.wav");
        std::fs::write(&path, &target).unwrap();
        let expected = BlobDescriptor {
            sha256: hex::encode(Sha256::digest(&target)),
            size_bytes: target.len() as u64,
        };
        let import_intent = derivative_import_intent(&leased.source).unwrap();
        let request_id = "confirmed-import-failure-control:audio-derive-import";
        // One real canonical target import. The historical result below comes
        // from read-only recovery, not a second import or native execution.
        let imported = service
            .import_file_verified(
                before.id,
                &before.stamp(),
                request_id,
                &path,
                &expected,
                import_intent.clone(),
            )
            .unwrap();
        assert!(!imported.replayed);
        let current = service.project(before.id).unwrap();
        let history = serde_json::to_value(service.history(before.id, 0, 256).unwrap()).unwrap();
        let watermark = service.production_receipt_watermark(before.id).unwrap();
        let recovered = service
            .recover_file_import(
                before.id,
                &imported.project.stamp(),
                request_id,
                &import_intent,
            )
            .unwrap()
            .unwrap();
        assert!(recovered.replayed);
        let expected_outcome = recovered.clone();
        let failure = qualify_confirmed_derivative_import(
            &before,
            &leased.source,
            request_id,
            &import_intent,
            &expected,
            recovered,
        )
        .unwrap_err();
        assert!(failure.original().outcome_known);
        assert_eq!(failure.confirmed_import(), Some(&expected_outcome));
        let original_error = serde_json::to_value(failure.original()).unwrap();
        let retained = finish_failed_audio_entrypoint(
            failure,
            Err(backend("source-only injected secondary cleanup failure")),
        );
        assert_eq!(
            serde_json::to_value(retained.original()).unwrap(),
            original_error
        );
        assert_eq!(retained.confirmed_import(), Some(&expected_outcome));
        let serialized = serde_json::to_value(&retained).unwrap();
        assert_eq!(serialized["original"], original_error);
        assert_eq!(
            serialized["confirmed_import"],
            serde_json::to_value(&expected_outcome).unwrap()
        );
        assert_eq!(service.project(before.id).unwrap(), current);
        assert_eq!(
            serde_json::to_value(service.history(before.id, 0, 256).unwrap()).unwrap(),
            history
        );
        assert_eq!(
            service.production_receipt_watermark(before.id).unwrap(),
            watermark
        );
        assert_eq!(watermark.count, 0);
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            target
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&leased.source.source_sha256, leased.source.source_bytes)
                .unwrap(),
            source_bytes
        );
        leased.release(&service).unwrap();
    }

    #[test]
    fn audio_owner_typed_unknown_preserves_original_without_confirmed_import() {
        for uncertain in [false, true] {
            let original = Error::new(ErrorCode::Timeout, "source-only original owner error");
            let original = if uncertain {
                original.uncertain()
            } else {
                original
            };
            let expected = serde_json::to_value(&original).unwrap();
            let display = original.to_string();
            let failure = AudioDerivativeFailure::from(original);
            let retained = finish_failed_audio_entrypoint(
                failure,
                Err(backend("source-only secondary cleanup error")),
            );
            assert_eq!(serde_json::to_value(retained.original()).unwrap(), expected);
            assert_eq!(retained.original().outcome_known, !uncertain);
            assert!(retained.confirmed_import().is_none());
            assert_eq!(retained.to_string(), display);
            assert_eq!(
                std::error::Error::source(&retained).unwrap().to_string(),
                display
            );
            let serialized = serde_json::to_value(&retained).unwrap();
            assert_eq!(serialized["original"], expected);
            assert!(serialized["confirmed_import"].is_null());
        }
    }

    #[cfg(unix)]
    #[test]
    fn audio_owner_precheck_preserves_real_historical_import_without_retry_or_adoption() {
        let (root, service, before, coordinator, intent, source_bytes) = source_only_coordinator();
        let mut leased = coordinator
            .prepare_audio_derivative_source(
                before.id,
                &before.stamp(),
                "precheck-existing-import-control",
                intent,
            )
            .unwrap();
        let target = wav(16_000, 1, 400, None);
        let path = root.path().join("precheck-existing-target.wav");
        std::fs::write(&path, &target).unwrap();
        let expected = BlobDescriptor {
            sha256: hex::encode(Sha256::digest(&target)),
            size_bytes: target.len() as u64,
        };
        let import_intent = derivative_import_intent(&leased.source).unwrap();
        let request = "precheck-existing-import-control:audio-derive-import";
        // One real canonical target import. The guard receives its actual
        // read-only recovered outcome; no native observation is fabricated.
        let imported = service
            .import_file_verified(
                before.id,
                &before.stamp(),
                request,
                &path,
                &expected,
                import_intent.clone(),
            )
            .unwrap();
        assert!(!imported.replayed);
        let recovered = service
            .recover_file_import(
                before.id,
                &imported.project.stamp(),
                request,
                &import_intent,
            )
            .unwrap()
            .unwrap();
        assert!(recovered.replayed);
        let current = service.project(before.id).unwrap();
        let history = serde_json::to_value(service.history(before.id, 0, 256).unwrap()).unwrap();
        let watermark = service.production_receipt_watermark(before.id).unwrap();
        assert!(derivative_require_unimported(None).is_ok());
        let expected_outcome = recovered.clone();
        let failure = derivative_require_unimported(Some(recovered)).unwrap_err();
        let expected_error = serde_json::to_value(Error::new(
            ErrorCode::Conflict,
            "Audio derivative import already has a canonical receipt; inspect that historical outcome",
        ))
        .unwrap();
        assert_eq!(
            serde_json::to_value(failure.original()).unwrap(),
            expected_error
        );
        assert!(failure.original().outcome_known);
        assert_eq!(failure.confirmed_import(), Some(&expected_outcome));
        let retained = finish_failed_audio_entrypoint(
            failure,
            Err(backend("source-only precheck secondary cleanup failure")),
        );
        assert_eq!(
            serde_json::to_value(retained.original()).unwrap(),
            expected_error
        );
        assert_eq!(retained.confirmed_import(), Some(&expected_outcome));
        let record = serde_json::to_value(&retained).unwrap();
        assert_eq!(record["original"], expected_error);
        assert_eq!(
            record["confirmed_import"],
            serde_json::to_value(&expected_outcome).unwrap()
        );
        assert!(record.get("observation").is_none());
        assert!(record.get("asset_id").is_none());
        assert_eq!(service.project(before.id).unwrap(), current);
        assert_eq!(
            serde_json::to_value(service.history(before.id, 0, 256).unwrap()).unwrap(),
            history
        );
        assert_eq!(
            service.production_receipt_watermark(before.id).unwrap(),
            watermark
        );
        assert_eq!(watermark.count, 0);
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            target
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&leased.source.source_sha256, leased.source.source_bytes)
                .unwrap(),
            source_bytes
        );
        leased.release(&service).unwrap();
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn audio_retained_output_reads_original_instance_and_refuses_replaced_owner_path() {
        use std::os::unix::fs::PermissionsExt;
        let (directory, service, project, coordinator, _, _) = source_only_coordinator();
        let media = service.store().lock().blob_root().to_path_buf();
        let output = coordinator.client.connection().output_root.clone();
        let paths = AudioDerivativeOwnerPaths::admit(&coordinator, &media, &output).unwrap();
        let filesystem = semwright_platform_services::filesystem();
        let original_media = filesystem.open_root(&media, true, false).unwrap();
        let original_output = filesystem.open_root(&output, true, false).unwrap();
        let roots = AudioDerivativeOwnerRoots::open_verified_paths(
            &paths,
            &original_media.root_instance_identity().unwrap(),
            &original_output.root_instance_identity().unwrap(),
        )
        .unwrap();
        let (source, source_bytes) = source();
        let (native, process) = process_originals(&source);
        let target = wav(16_000, 1, 400, None);
        for (artifact, bytes) in [
            (&native.artifact, &target),
            (&native.original_source, &source_bytes),
            (&native.original_output, &target),
        ]
        .into_iter()
        .chain(native.process_originals.iter().zip(&process))
        {
            let path = output.join(&artifact.path);
            std::fs::write(&path, bytes).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        std::fs::rename(&output, directory.path().join("retained-output-instance")).unwrap();
        std::fs::create_dir(&output).unwrap();
        std::fs::set_permissions(&output, std::fs::Permissions::from_mode(0o700)).unwrap();
        std::fs::write(
            output.join(&native.artifact.path),
            b"replacement must never be captured",
        )
        .unwrap();
        // Read the component's retained root, not the replacement pathname.
        // The independent current-path fence still rejects adoption.
        let captured =
            ReadAudioDerivativeOriginals::capture(roots.output.as_ref(), &native, source.profile)
                .unwrap();
        assert_eq!(captured.artifact, target);
        assert_eq!(captured.source, source_bytes);
        assert_eq!(captured.process, process);
        assert_eq!(
            roots.check_current().unwrap_err().code,
            ErrorCode::StaleReference
        );
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn audio_owner_open_checks_both_actual_native_identities_before_capture() {
        let (_directory, service, project, coordinator, _, _) = source_only_coordinator();
        let media = service.store().lock().blob_root().to_path_buf();
        let output = coordinator.client.connection().output_root.clone();
        let paths = AudioDerivativeOwnerPaths::admit(&coordinator, &media, &output).unwrap();
        let filesystem = semwright_platform_services::filesystem();
        let original_media = filesystem.open_root(&media, true, false).unwrap();
        let original_output = filesystem.open_root(&output, true, false).unwrap();
        let media_id = original_media.root_instance_identity().unwrap();
        let output_id = original_output.root_instance_identity().unwrap();
        assert_ne!(media_id, output_id);
        for (expected_media, expected_output) in [
            (output_id.as_str(), output_id.as_str()),
            (media_id.as_str(), media_id.as_str()),
        ] {
            let refused = AudioDerivativeOwnerRoots::open_verified_paths(
                &paths,
                expected_media,
                expected_output,
            )
            .err()
            .expect("the actual SDK open must reject a different directory instance");
            assert_eq!(refused.code, ErrorCode::StaleReference);
            assert!(refused.outcome_known);
        }
        let retained =
            AudioDerivativeOwnerRoots::open_verified_paths(&paths, &media_id, &output_id).unwrap();
        assert_eq!(retained.media.root_instance_identity().unwrap(), media_id);
        assert_eq!(retained.output.root_instance_identity().unwrap(), output_id);
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service
                .production_receipt_watermark(project.id)
                .unwrap()
                .count,
            0
        );
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
    }

    #[cfg(unix)]
    #[test]
    fn audio_captured_bytes_import_keeps_known_outcome_after_source_path_and_qualification_change()
    {
        let (directory, service, before, coordinator, intent, source_bytes) =
            source_only_coordinator();
        let mut leased = coordinator
            .prepare_audio_derivative_source(
                before.id,
                &before.stamp(),
                "captured-byte-import-control",
                intent,
            )
            .unwrap();
        let target = wav(16_000, 1, 400, None);
        let path = directory.path().join("captured-only-target.wav");
        std::fs::write(&path, &target).unwrap();
        let captured = std::fs::read(&path).unwrap();
        let expected = BlobDescriptor {
            sha256: hex::encode(Sha256::digest(&captured)),
            size_bytes: captured.len() as u64,
        };
        // There is no pathname argument to the actual caller/importer seam.
        std::fs::write(
            &path,
            b"later replacement cannot become the captured source",
        )
        .unwrap();
        std::fs::remove_file(&path).unwrap();
        let import_intent = derivative_import_intent(&leased.source).unwrap();
        let request = "captured-byte-import-control:audio-derive-import";
        let mut stale_qualification = before.clone();
        stale_qualification.revision += 1;
        // One real canonical import, then a deliberately rejected qualification.
        // No native observation or AdoptedAudioDerivative is manufactured.
        let failure = coordinator
            .import_captured_audio_derivative(
                &stale_qualification,
                &leased.source,
                request,
                &import_intent,
                &expected,
                &captured,
            )
            .unwrap_err();
        let original_error = json!(failure.original());
        let outcome = failure.confirmed_import().unwrap().clone();
        assert!(failure.original().outcome_known);
        assert!(!outcome.replayed);
        assert_eq!(outcome.request_id, request);
        assert_eq!(outcome.previous_revision, before.revision);
        assert_eq!(outcome.project.revision, before.revision + 1);
        assert_eq!(service.project(before.id).unwrap(), outcome.project);
        let history = json!(service.history(before.id, 0, 256).unwrap());
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&expected.sha256, expected.size_bytes)
                .unwrap(),
            target
        );
        assert_eq!(
            service
                .store()
                .lock()
                .read_blob(&leased.source.source_sha256, leased.source.source_bytes,)
                .unwrap(),
            source_bytes
        );
        let retained = finish_failed_audio_entrypoint(
            failure,
            Err(backend("source-only secondary cleanup refusal")),
        );
        assert_eq!(json!(retained.original()), original_error);
        assert_eq!(retained.confirmed_import(), Some(&outcome));
        assert!(json!(&retained).get("asset_id").is_none());
        let recovered = service
            .recover_file_import(before.id, &outcome.project.stamp(), request, &import_intent)
            .unwrap()
            .unwrap();
        let mut historical = outcome.clone();
        historical.replayed = true;
        assert_eq!(recovered, historical);
        assert_eq!(service.project(before.id).unwrap(), outcome.project);
        assert_eq!(json!(service.history(before.id, 0, 256).unwrap()), history);
        assert_eq!(
            service
                .production_receipt_watermark(before.id)
                .unwrap()
                .count,
            0
        );
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
        leased.release(&service).unwrap();
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn audio_owner_unsupported_native_identity_has_no_capture_import_or_dispatch() {
        let (_directory, service, project, coordinator, _, _) = source_only_coordinator();
        let media = service.store().lock().blob_root().to_path_buf();
        let output = coordinator.client.connection().output_root.clone();
        let paths = AudioDerivativeOwnerPaths::admit(&coordinator, &media, &output).unwrap();
        let watermark = service.production_receipt_watermark(project.id).unwrap();
        let history = json!(service.history(project.id, 0, 256).unwrap());
        // Current MacFilesystem inherits Unsupported for root identity.
        // This is its real API, not a fake backend or a stat/path fallback.
        let refusal = AudioDerivativeOwnerRoots::open_verified_paths(
            &paths,
            "unavailable-media-identity",
            "unavailable-output-identity",
        )
        .err()
        .expect("unsupported identity must refuse the actual open");
        assert_eq!(refusal.code, ErrorCode::Unsupported);
        assert!(refusal.outcome_known);
        assert_eq!(service.project(project.id).unwrap(), project);
        assert_eq!(
            service.production_receipt_watermark(project.id).unwrap(),
            watermark
        );
        assert_eq!(json!(service.history(project.id, 0, 256).unwrap()), history);
        assert_eq!(
            coordinator
                .client
                .execute_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            0
        );
    }
}
