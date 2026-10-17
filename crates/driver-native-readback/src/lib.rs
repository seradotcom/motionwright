//! Host-mediated, read-only inspection of an existing canonical mix and master.
//!
//! This driver does not create a mix, mux a master, grant access, approve an
//! export, or accept a client-supplied media path, executable, argv or verdict.
//! The native coordinator must bind the original receipts, current canonical
//! source and owner registration before and after accepting this observation.

pub mod registration;

use async_trait::async_trait;
use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use semwright_driver_sdk::{
    Capability, Driver, DriverExecutionContext, DriverInterfaces, RuntimeToolArg, RuntimeToolCwd,
    RuntimeToolJob, ToolExecutionOutput, descriptor_digest,
};
use semwright_types::{CommandDescriptor, Error, ErrorCode, Idempotency, Result, Risk};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use std::time::Duration;
use uuid::Uuid;

pub const DRIVER_ID: &str = "native-readback";
pub const PROVIDER_ID: &str = "driver:native-readback";
pub const INSPECT_COMMAND: &str = "driver.native-readback.original_mix.inspect";
pub const READER_SHA256: &str = "5c66976b10357c7e97294e62bccb6844fad4277344ccb7c948e421f2fc267abc";
pub const REQUEST_SCHEMA: &str = "motionwright.canonical-original-mix-inspection/1";
pub const RESULT_SCHEMA: &str = "motionwright.host-original-mix-readback/1";
pub const MEDIA_MOUNT: &str = "native-readback-media";
pub const RUNTIME_MOUNT: &str = "native-readback-runtime";
pub const PYTHON_TOOL: &str = "native-readback-python";
pub const FFMPEG_TOOL: &str = "native-readback-ffmpeg";
pub const FFPROBE_TOOL: &str = "native-readback-ffprobe";
pub const HOST_JOB_TIMEOUT_MS: u64 = 180_000;
pub const DRIVER_REQUEST_TIMEOUT_MS: u64 = 200_000;
pub const REQUIRED_PRODUCTION_DEADLINE_SECS: u64 = 210;
pub const SDK_REVISION: &str = "54d3459a051adbce4cae9f60c0dea8f60c824be6";

const MAX_REQUEST_BYTES: usize = 16 * 1024;
const MAX_STDIN_BYTES: usize = 64 * 1024;
const MAX_RESULT_BYTES: usize = 32 * 1024;
const READER_SOURCE: &str = include_str!("../../../tooling/native_mlt_mix_readback.py");
const WORKER_SOURCE: &str = include_str!("worker.py");
const SCOPE: &str = "technical_original_mix_pcm_and_av_clock_only; no_export_authority; no_listening_rights_loudness_pixel_fidelity_or_editable_reopen_approval";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CompiledInspectorSourcePins {
    pub reader_sha256: String,
    pub worker_sha256: String,
    pub sdk_revision: String,
}

/// Read-only source metadata for the owner registration and native consumer.
/// This is not a registration receipt or evidence of a running interpreter.
pub fn compiled_inspector_source_pins() -> Result<CompiledInspectorSourcePins> {
    if sha256(READER_SOURCE.as_bytes()) != READER_SHA256 {
        return Err(Error::new(
            ErrorCode::Conflict,
            "The compiled whole-medium reader does not match its required pin",
        ));
    }
    Ok(CompiledInspectorSourcePins {
        reader_sha256: READER_SHA256.into(),
        worker_sha256: sha256(WORKER_SOURCE.as_bytes()),
        sdk_revision: SDK_REVISION.into(),
    })
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ToolPins {
    pub python_sha256: String,
    pub ffmpeg_sha256: String,
    pub ffprobe_sha256: String,
}

impl ToolPins {
    pub fn validate(&self) -> Result<()> {
        for value in [
            &self.python_sha256,
            &self.ffmpeg_sha256,
            &self.ffprobe_sha256,
        ] {
            require_sha256(value)?;
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InspectionClock {
    pub frames: u32,
    pub width: u32,
    pub height: u32,
    pub fps_num: u32,
    pub fps_den: u32,
}

impl InspectionClock {
    /// Admission arithmetic only. The pinned inspector independently checks
    /// the actual frame/packet clock and compares every logical PCM sample.
    pub fn sample_frames(&self) -> Result<u64> {
        if self.frames == 0
            || self.width == 0
            || self.height == 0
            || self.width > 4096
            || self.height > 4096
            || self.fps_num == 0
            || self.fps_den == 0
            || self.fps_num > 120_000
            || self.fps_den > 100_000
            || u64::from(self.fps_num) > 120 * u64::from(self.fps_den)
        {
            return Err(Error::invalid(
                "Original mix inspection clock exceeds the fixed profile",
            ));
        }
        let numerator = u64::from(self.frames)
            .checked_mul(48_000)
            .and_then(|value| value.checked_mul(u64::from(self.fps_den)))
            .ok_or_else(|| Error::invalid("Original mix inspection sample clock overflow"))?;
        let denominator = u64::from(self.fps_num);
        if !numerator.is_multiple_of(denominator) {
            return Err(Error::invalid(
                "Original mix inspection requires an exact sample clock",
            ));
        }
        let samples = numerator / denominator;
        if !(1024..=720_000).contains(&samples) {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Canonical original mix inspection is limited to fifteen seconds",
            ));
        }
        Ok(samples)
    }
}

/// Backend command data, never a grant or an independently verified source.
/// It contains only identities and pins obtained from current native custody.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CanonicalMixInspectionRequest {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub canonical_dependency_sha256: String,
    pub canonical_film_options_sha256: String,
    pub canonical_motion_source_sha256: String,
    pub source_plan_sha256: String,
    pub native_request_id: String,
    pub original_mux_receipt_id: Uuid,
    pub original_mux_request_sha256: String,
    pub reference_wav_sha256: String,
    pub master_sha256: String,
    pub clock: InspectionClock,
    pub tool_pins: ToolPins,
    /// Expected owner registration/profile digest. The worker retains this
    /// binding; only the existing owner/Host registration can authorize it.
    pub owner_runtime_profile_sha256: String,
}

impl CanonicalMixInspectionRequest {
    pub fn validate(&self) -> Result<()> {
        if self.schema != REQUEST_SCHEMA
            || [
                self.project_id,
                self.generation,
                self.deliverable_id,
                self.original_mux_receipt_id,
            ]
            .into_iter()
            .any(|id| id.is_nil())
            || self.native_request_id.len() != 41
            || !self.native_request_id.starts_with("mxav-")
            || !self.native_request_id.as_bytes()[5..]
                .iter()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
        {
            return Err(Error::invalid(
                "Canonical original mix inspection identity is invalid",
            ));
        }
        for value in [
            &self.canonical_dependency_sha256,
            &self.canonical_film_options_sha256,
            &self.canonical_motion_source_sha256,
            &self.source_plan_sha256,
            &self.original_mux_request_sha256,
            &self.reference_wav_sha256,
            &self.master_sha256,
            &self.owner_runtime_profile_sha256,
        ] {
            require_sha256(value)?;
        }
        self.tool_pins.validate()?;
        self.clock.sample_frames()?;
        Ok(())
    }

    pub fn binding_sha256(&self) -> Result<String> {
        self.validate()?;
        // Serialize this typed backend structure once. The worker receives
        // those exact bytes, avoiding map-order and float-format assumptions
        // between serializers. No caller JSON is accepted as a digest input.
        Ok(sha256(&serde_json::to_vec(self)?))
    }

    fn media_names(&self) -> Result<(String, String)> {
        self.validate()?;
        // These names are the existing native producer contract at 6633b90.
        // They are not request path fields and cannot select another root.
        let reference = format!(
            "mw-original-mix-{}-{}-r{}-p{}-{}-{}.wav",
            self.project_id.simple(),
            self.generation.simple(),
            self.revision,
            self.deliverable_id.simple(),
            &self.source_plan_sha256[..16],
            &self.reference_wav_sha256[..16]
        );
        let token = sha256(self.native_request_id.as_bytes());
        let master = format!(
            "mw-{}-r{}-{}-multisegment-master.mp4",
            self.project_id.simple(),
            self.revision,
            &token[..16]
        );
        Ok((reference, master))
    }
}

fn require_sha256(value: &str) -> Result<()> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(Error::invalid(
            "Inspection requires a lowercase SHA-256 digest",
        ));
    }
    Ok(())
}

fn sha256(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}

fn interfaces() -> DriverInterfaces {
    DriverInterfaces {
        dynamic_capabilities: true,
        cooperative_cancellation: true,
        health: true,
        host_tools: true,
        ..Default::default()
    }
}

fn request_schema() -> Value {
    let mut properties = Map::new();
    properties.insert("schema".into(), json!({"const": REQUEST_SCHEMA}));
    for name in [
        "project_id",
        "generation",
        "deliverable_id",
        "original_mux_receipt_id",
    ] {
        properties.insert(name.into(), json!({"type":"string", "format":"uuid"}));
    }
    properties.insert("revision".into(), json!({"type":"integer", "minimum":0}));
    for name in [
        "canonical_dependency_sha256",
        "canonical_film_options_sha256",
        "canonical_motion_source_sha256",
        "source_plan_sha256",
        "original_mux_request_sha256",
        "reference_wav_sha256",
        "master_sha256",
        "owner_runtime_profile_sha256",
    ] {
        properties.insert(
            name.into(),
            json!({"type":"string", "pattern":"^[0-9a-f]{64}$"}),
        );
    }
    properties.insert(
        "native_request_id".into(),
        json!({"type":"string", "pattern":"^mxav-[0-9a-f]{36}$"}),
    );
    let mut clock = Map::new();
    for name in ["frames", "width", "height", "fps_num", "fps_den"] {
        clock.insert(name.into(), json!({"type":"integer", "minimum":1}));
    }
    properties.insert(
        "clock".into(),
        json!({"type":"object", "properties":clock,
        "required":["frames", "width", "height", "fps_num", "fps_den"],
        "additionalProperties":false}),
    );
    let mut pins = Map::new();
    for name in ["python_sha256", "ffmpeg_sha256", "ffprobe_sha256"] {
        pins.insert(
            name.into(),
            json!({"type":"string", "pattern":"^[0-9a-f]{64}$"}),
        );
    }
    properties.insert(
        "tool_pins".into(),
        json!({"type":"object", "properties":pins,
        "required":["python_sha256", "ffmpeg_sha256", "ffprobe_sha256"],
        "additionalProperties":false}),
    );
    let required: Vec<String> = properties.keys().cloned().collect();
    json!({"type":"object", "properties":properties, "required":required,
        "additionalProperties":false})
}

pub fn inspection_capability() -> Capability {
    Capability {
        descriptor: CommandDescriptor {
            name: INSPECT_COMMAND.into(),
            version: "1".into(),
            description: "Read an existing canonical original-mix WAV and MP4 through the owner-pinned whole-medium inspector".into(),
            input_schema: request_schema(),
            output_schema: json!({"type":"object", "properties":{
                "schema":{"const":RESULT_SCHEMA}, "binding_sha256":{"type":"string"},
                "reader_sha256":{"const":READER_SHA256}, "worker_sha256":{"type":"string"},
                "tool_pins":{"type":"object"}, "host_job":{"type":"object"},
                "host_request_id":{"type":"string"}, "status":{"const":"PASS"},
                "report":{"type":"object"}, "evidence_scope":{"const":SCOPE}},
                "required":["schema","binding_sha256","reader_sha256","worker_sha256",
                    "tool_pins","host_job","host_request_id","status","report","evidence_scope"],
                "additionalProperties":false}),
            requires: vec![PROVIDER_ID.into()],
            risk: Risk::ReadOnly,
            idempotency: Idempotency::ReadOnly,
            timeout_ms: DRIVER_REQUEST_TIMEOUT_MS,
            dry_run: false,
            interactive_consent: false,
            backends: vec![PROVIDER_ID.into()],
        },
        aliases: vec![],
        tags: vec!["audio".into(), "technical-readback".into()],
        object_types: vec![],
    }
}

fn parse_request(args: Value) -> Result<CanonicalMixInspectionRequest> {
    if serde_json::to_vec(&args)?.len() > MAX_REQUEST_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Inspection request exceeds its byte budget",
        ));
    }
    let request: CanonicalMixInspectionRequest = serde_json::from_value(args)?;
    request.validate()?;
    Ok(request)
}

fn worker_stdin(request: &CanonicalMixInspectionRequest) -> Result<Vec<u8>> {
    request.validate()?;
    compiled_inspector_source_pins()?;
    let (reference_name, master_name) = request.media_names()?;
    let packet = json!({"binding_json":serde_json::to_string(request)?, "binding_sha256":request.binding_sha256()?,
        "reader_source":READER_SOURCE, "reference_name":reference_name, "master_name":master_name});
    let encoded = BASE64.encode(serde_json::to_vec(&packet)?);
    // Only the standard base64 alphabet is inserted into Python source. No
    // string/argv selected by a client is interpolated as Python code.
    let stdin = format!("{WORKER_SOURCE}\nraise SystemExit(main(\"{encoded}\"))\n").into_bytes();
    if stdin.len() > MAX_STDIN_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Pinned inspector program exceeds Host stdin bounds",
        ));
    }
    Ok(stdin)
}

fn worker_args() -> Vec<RuntimeToolArg> {
    vec![
        RuntimeToolArg::Literal { value: "-I".into() },
        RuntimeToolArg::Literal { value: "-B".into() },
        RuntimeToolArg::Literal { value: "-".into() },
        RuntimeToolArg::MountPath {
            mount: MEDIA_MOUNT.into(),
            relative: String::new(),
        },
        RuntimeToolArg::ToolPath {
            tool: FFMPEG_TOOL.into(),
        },
        RuntimeToolArg::ToolPath {
            tool: FFPROBE_TOOL.into(),
        },
    ]
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WorkerResult {
    schema: String,
    binding_sha256: String,
    reader_sha256: String,
    tool_pins: ToolPins,
    status: String,
    report: Value,
}

fn exact_keys(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
    })
}

fn validate_worker_output(
    request: &CanonicalMixInspectionRequest,
    output: ToolExecutionOutput,
) -> Result<Value> {
    if output.stdout.len() > MAX_RESULT_BYTES || !output.stderr.is_empty() || output.exit_code != 0
    {
        return Err(Error::new(
            ErrorCode::BackendFailed,
            "Original mix Host readback was rejected",
        ));
    }
    let response: WorkerResult = serde_json::from_slice(&output.stdout).map_err(|_| {
        Error::new(
            ErrorCode::PluginProtocolError,
            "Host inspector returned an invalid closed result",
        )
    })?;
    if response.schema != RESULT_SCHEMA
        || response.status != "PASS"
        || response.binding_sha256 != request.binding_sha256()?
        || response.reader_sha256 != READER_SHA256
        || response.tool_pins != request.tool_pins
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Host inspector result does not match the current request binding",
        ));
    }
    let report = &response.report;
    let samples = request.clock.sample_frames()?;
    let fail = || {
        Error::new(
            ErrorCode::PluginProtocolError,
            "Host inspector returned inconsistent technical evidence",
        )
    };
    if !exact_keys(
        report,
        &[
            "schema",
            "technical_content_readback",
            "reference_wav_sha256",
            "final_mp4_sha256",
            "clock",
            "content",
            "listening_approval",
            "rights_approval",
            "editable_source_reopen_verified",
            "independent_loudness_verified",
            "complete_reader_report_sha256",
            "complete_reader_report_bytes",
            "complete_reader_report_retained",
        ],
    ) || !exact_keys(
        &report["content"],
        &[
            "reference_sample_frames",
            "decoded_sample_frames",
            "decoded_access_unit_suffix_samples",
            "reference_pcm_sha256",
            "decoded_pcm_sha256",
            "logical_interval",
            "all_logical_samples_compared",
            "alignment",
            "limits",
            "compared_windows",
            "compared_short_energy_windows",
        ],
    ) || !exact_keys(
        &report["clock"],
        &[
            "video_frames",
            "audio_frames",
            "decoded_audio_samples_from_frames",
            "audio_duration_samples",
            "first_video_pts",
            "first_audio_pts",
            "video_fps",
            "audio_timebase",
            "exact_contiguous_frame_timestamps",
            "video_packets",
            "audio_packets",
            "priming_skip_samples",
            "discard_padding_samples",
            "final_audio_packet_duration_samples",
            "audio_packet_presentation_end_samples",
            "declared_decoder_suffix_budget_samples",
            "packet_priming_and_presentation_checked",
        ],
    ) || report.get("schema") != Some(&json!("motionwright.native-original-mix-readback/1"))
        || report.get("technical_content_readback") != Some(&json!("PASS"))
        || report.get("reference_wav_sha256") != Some(&json!(request.reference_wav_sha256))
        || report.get("final_mp4_sha256") != Some(&json!(request.master_sha256))
        || report
            .pointer("/clock/video_frames")
            .and_then(Value::as_u64)
            != Some(u64::from(request.clock.frames))
        || report
            .pointer("/clock/audio_duration_samples")
            .and_then(Value::as_u64)
            != Some(samples)
        || report.pointer("/clock/exact_contiguous_frame_timestamps") != Some(&json!(true))
        || report.pointer("/clock/packet_priming_and_presentation_checked") != Some(&json!(true))
        || report
            .pointer("/content/reference_sample_frames")
            .and_then(Value::as_u64)
            != Some(samples)
        || report.pointer("/content/all_logical_samples_compared") != Some(&json!(true))
        || report.pointer("/content/logical_interval") != Some(&json!([0, samples]))
        || report.get("complete_reader_report_retained") != Some(&json!(false))
        || !report
            .get("complete_reader_report_bytes")
            .and_then(Value::as_u64)
            .is_some_and(|bytes| (1..=16 * 1024 * 1024).contains(&bytes))
    {
        return Err(fail());
    }
    for key in [
        "listening_approval",
        "rights_approval",
        "editable_source_reopen_verified",
        "independent_loudness_verified",
    ] {
        if report.get(key) != Some(&json!(false)) {
            return Err(fail());
        }
    }
    if !exact_keys(
        &report["content"]["limits"],
        &[
            "minimum_correlation",
            "maximum_relative_error",
            "minimum_gain",
            "maximum_gain",
            "maximum_silent_sample",
            "minimum_lag_margin",
            "block_samples",
            "diagnostic_lag_samples",
            "short_window_samples",
            "minimum_short_window_rms",
            "minimum_short_window_gain",
            "maximum_short_window_gain",
        ],
    ) || !report["content"]["limits"]
        .as_object()
        .is_some_and(|limits| {
            limits
                .values()
                .all(|value| value.as_f64().is_some_and(f64::is_finite))
        })
        || !report
            .pointer("/content/compared_windows")
            .and_then(Value::as_u64)
            .is_some_and(|count| count >= 2)
        || report
            .pointer("/content/compared_short_energy_windows")
            .and_then(Value::as_u64)
            .is_none()
    {
        return Err(fail());
    }
    for pointer in [
        "/content/reference_pcm_sha256",
        "/content/decoded_pcm_sha256",
        "/complete_reader_report_sha256",
    ] {
        require_sha256(
            report
                .pointer(pointer)
                .and_then(Value::as_str)
                .ok_or_else(fail)?,
        )?;
    }
    let decoded = report
        .pointer("/content/decoded_sample_frames")
        .and_then(Value::as_u64)
        .ok_or_else(fail)?;
    let suffix = report
        .pointer("/content/decoded_access_unit_suffix_samples")
        .and_then(Value::as_u64)
        .ok_or_else(fail)?;
    let budget = report
        .pointer("/clock/declared_decoder_suffix_budget_samples")
        .and_then(Value::as_u64)
        .ok_or_else(fail)?;
    if budget > 1024
        || suffix > budget
        || decoded != samples + suffix
        || report
            .pointer("/clock/decoded_audio_samples_from_frames")
            .and_then(Value::as_u64)
            != Some(decoded)
    {
        return Err(fail());
    }
    let alignments = report
        .pointer("/content/alignment")
        .and_then(Value::as_array)
        .ok_or_else(fail)?;
    if alignments.len() != 2
        || alignments.iter().enumerate().any(|(channel, alignment)| {
            !exact_keys(
                alignment,
                &[
                    "channel",
                    "best_lag_samples",
                    "zero_lag_margin",
                    "reference_zero_lag_margin",
                    "searched_lag_samples",
                    "fingerprint_positions",
                    "search_interval_samples",
                    "absolute_delay_absence_proven",
                    "aligned_or_shifted_output",
                ],
            ) || alignment.get("channel").and_then(Value::as_u64) != Some(channel as u64)
                || alignment.get("absolute_delay_absence_proven") != Some(&json!(false))
                || alignment.get("aligned_or_shifted_output") != Some(&json!(false))
        })
    {
        return Err(fail());
    }
    Ok(
        json!({"schema":response.schema, "binding_sha256":response.binding_sha256,
        "reader_sha256":response.reader_sha256, "worker_sha256":sha256(WORKER_SOURCE.as_bytes()),
        "tool_pins":response.tool_pins, "status":response.status,
        "report":response.report, "evidence_scope":SCOPE}),
    )
}

fn retain_job_error(
    mut error: Error,
    request: &CanonicalMixInspectionRequest,
    job: Option<&RuntimeToolJob>,
) -> Error {
    // Preserve the Host's code, outcome_known, progress and native diagnostic.
    // These bounded opaque references are observations, never retry grants.
    if let Some(job) = job {
        error
            .candidates
            .push(format!("motionwright-readback-host-job:{}", job.id));
    }
    if let Ok(binding) = request.binding_sha256() {
        error
            .candidates
            .push(format!("motionwright-readback-binding:{binding}"));
    }
    error
}

pub struct NativeReadbackDriver;

#[async_trait]
impl Driver for NativeReadbackDriver {
    fn id(&self) -> &str {
        DRIVER_ID
    }
    fn version(&self) -> &str {
        env!("CARGO_PKG_VERSION")
    }
    fn interfaces(&self) -> DriverInterfaces {
        interfaces()
    }

    async fn capabilities(&mut self) -> Result<Vec<Capability>> {
        Ok(vec![inspection_capability()])
    }

    async fn execute(
        &mut self,
        _command: &str,
        _descriptor_sha256: &str,
        _args: Value,
    ) -> Result<Value> {
        Err(Error::new(
            ErrorCode::PermissionDenied,
            "Original mix inspection requires the existing owner-authorized Driver Host context",
        ))
    }

    async fn execute_with_context(
        &mut self,
        command: &str,
        expected_descriptor: &str,
        args: Value,
        context: DriverExecutionContext,
    ) -> Result<Value> {
        context.check_cancelled()?;
        if command != INSPECT_COMMAND {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Native readback command is not supported",
            ));
        }
        if expected_descriptor != descriptor_digest(&inspection_capability().descriptor)? {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Native readback capability descriptor changed",
            ));
        }
        let request = parse_request(args)?;
        let stdin = worker_stdin(&request)?;
        let job = context
            .start_runtime_tool_job_args(
                PYTHON_TOOL,
                worker_args(),
                stdin,
                Duration::from_millis(HOST_JOB_TIMEOUT_MS),
                Some(RuntimeToolCwd {
                    mount: MEDIA_MOUNT.into(),
                    relative: String::new(),
                }),
            )
            .await
            .map_err(|error| retain_job_error(error, &request, None))?;
        // Use the SDK's original-job supervision and cancellation/reaping.
        // Never start a replacement on timeout, disconnect or an unknown wait.
        let output = context
            .wait_runtime_tool_job(&job)
            .await
            .map_err(|error| retain_job_error(error, &request, Some(&job)))?;
        let mut result = validate_worker_output(&request, output)
            .map_err(|error| retain_job_error(error, &request, Some(&job)))?;
        result["host_job"] = serde_json::to_value(&job)?;
        result["host_request_id"] = json!(context.request_id());
        Ok(result)
    }
}

#[cfg(test)]
mod tests;
