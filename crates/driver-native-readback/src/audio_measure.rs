//! Separate readonly measurement capability in the existing NativeReadback
//! Driver Host. No process/FFmpeg API outside that Host is exposed here.
//! Owner registration and native consumer admission are backend responsibilities.

use crate::{
    FFMPEG_TOOL, FFPROBE_TOOL, MEDIA_MOUNT, PYTHON_TOOL, READER_SHA256, READER_SOURCE, ToolPins,
    require_sha256, sha256,
};
use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use semwright_driver_sdk::{
    Capability, DriverExecutionContext, RuntimeToolArg, RuntimeToolCwd, RuntimeToolJob,
    ToolExecutionOutput, descriptor_digest,
};
use semwright_types::{CommandDescriptor, Error, ErrorCode, Idempotency, Result, Risk};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::time::Duration;
use uuid::Uuid;

pub const COMMAND: &str = "driver.native-readback.audio.measure";
pub const REQUEST_SCHEMA: &str = "motionwright.audio-measurement-request/1";
pub const RESULT_SCHEMA: &str = "motionwright.host-audio-measurement/1";
pub const METHOD: &str = "mw.ffmpeg-ebur128-f64-stereo48k/1";
pub const MEASURE_SOURCE: &str = include_str!("audio_measure.py");
pub const MAX_REQUEST_BYTES: usize = 16 * 1024;
pub const MAX_RESULT_BYTES: usize = 256 * 1024;
pub const HOST_TIMEOUT_MS: u64 = 180_000;
pub const REQUIRED_REQUEST_TIMEOUT_MS: u64 = 200_000;
const SCOPE: &str =
    "technical_measurement_only_no_export_listening_rights_or_editable_source_approval";

/// Source inventory only, not an installed/certified meter or grant.
pub fn compiled_measurement_worker_sha256() -> String {
    sha256(MEASURE_SOURCE.as_bytes())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MeasurementPins {
    pub worker_sha256: String,
    pub python_sha256: String,
    pub ffmpeg_sha256: String,
    pub ffprobe_sha256: String,
    pub owner_runtime_profile_sha256: String,
    pub meter_validation_sha256: String,
}
impl MeasurementPins {
    fn validate(&self) -> Result<()> {
        ToolPins {
            python_sha256: self.python_sha256.clone(),
            ffmpeg_sha256: self.ffmpeg_sha256.clone(),
            ffprobe_sha256: self.ffprobe_sha256.clone(),
        }
        .validate()?;
        for pin in [
            &self.worker_sha256,
            &self.owner_runtime_profile_sha256,
            &self.meter_validation_sha256,
        ] {
            require_sha256(pin)?;
        }
        if self.worker_sha256 != compiled_measurement_worker_sha256() {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Audio meter worker pin differs from compiled source",
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceClock {
    pub video_frames: u64,
    pub fps_num: u32,
    pub fps_den: u32,
    pub sample_frames: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceBinding {
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub canonical_dependency_sha256: String,
    pub source_plan_sha256: String,
    pub source_readiness_sha256: String,
    pub premix_wav_sha256: String,
    pub premix_pcm_sha256: String,
    pub clock: SourceClock,
}
impl SourceBinding {
    fn validate(&self) -> Result<()> {
        if [self.project_id, self.generation, self.deliverable_id]
            .into_iter()
            .any(|id| id.is_nil())
        {
            return Err(Error::invalid(
                "Audio measurement requires complete canonical identities",
            ));
        }
        for pin in [
            &self.canonical_dependency_sha256,
            &self.source_plan_sha256,
            &self.source_readiness_sha256,
            &self.premix_wav_sha256,
            &self.premix_pcm_sha256,
        ] {
            require_sha256(pin)?;
        }
        let clock = &self.clock;
        if clock.video_frames == 0
            || clock.fps_num == 0
            || clock.fps_den == 0
            || clock.fps_num > 120_000
            || clock.fps_den > 100_000
            || u64::from(clock.fps_num) > 120 * u64::from(clock.fps_den)
            || !(19_200..=720_000).contains(&clock.sample_frames)
            || u128::from(clock.video_frames) * u128::from(clock.fps_den) * 48_000
                != u128::from(clock.sample_frames) * u128::from(clock.fps_num)
        {
            return Err(Error::invalid(
                "Audio measurement requires an exact bounded canonical sample clock",
            ));
        }
        Ok(())
    }
    pub fn sha256(&self) -> Result<String> {
        self.validate()?;
        Ok(sha256(&serde_json::to_vec(&serde_json::to_value(self)?)?))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MeasurementRole {
    CanonicalPremix,
    MasteredWav,
    FinalAac,
}

/// Backend command data, not a client-supplied media path or readiness.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AudioMeasurementRequest {
    pub schema: String,
    pub binding: SourceBinding,
    pub source_binding_sha256: String,
    pub role: MeasurementRole,
    pub input_sha256: String,
    pub mastering_plan_sha256: Option<String>,
    pub gain_milli_db: Option<i64>,
    pub native_request_id: Option<String>,
    pub width: u32,
    pub height: u32,
    pub pins: MeasurementPins,
}
impl AudioMeasurementRequest {
    pub fn validate(&self) -> Result<()> {
        self.binding.validate()?;
        self.pins.validate()?;
        require_sha256(&self.input_sha256)?;
        if self.schema != REQUEST_SCHEMA
            || self.source_binding_sha256 != self.binding.sha256()?
            || self.width == 0
            || self.height == 0
            || self.width > 4096
            || self.height > 4096
        {
            return Err(Error::invalid(
                "Audio measurement request does not match its source profile",
            ));
        }
        match self.role {
            MeasurementRole::CanonicalPremix => {
                if self.input_sha256 != self.binding.premix_wav_sha256
                    || self.mastering_plan_sha256.is_some()
                    || self.gain_milli_db.is_some()
                    || self.native_request_id.is_some()
                {
                    return Err(Error::invalid(
                        "Premix observation cannot claim mastered content",
                    ));
                }
            }
            MeasurementRole::MasteredWav | MeasurementRole::FinalAac => {
                require_sha256(
                    self.mastering_plan_sha256
                        .as_deref()
                        .ok_or_else(|| Error::invalid("Mastering plan pin is required"))?,
                )?;
                if !self
                    .gain_milli_db
                    .is_some_and(|gain| (-60_000..=24_000).contains(&gain))
                {
                    return Err(Error::invalid(
                        "Audio measurement scalar expectation is outside its closed range",
                    ));
                }
                if self.role == MeasurementRole::MasteredWav {
                    if self.native_request_id.is_some() {
                        return Err(Error::invalid("WAV measurement cannot select a final mux"));
                    }
                } else {
                    let id = self.native_request_id.as_deref().ok_or_else(|| {
                        Error::invalid("Original mux request identity is required")
                    })?;
                    if id.len() != 41
                        || !id.starts_with("mxav-")
                        || !id.as_bytes()[5..]
                            .iter()
                            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(byte))
                    {
                        return Err(Error::invalid("Original mux identity is invalid"));
                    }
                }
            }
        }
        Ok(())
    }
    pub fn binding_sha256(&self) -> Result<String> {
        self.validate()?;
        Ok(sha256(&serde_json::to_vec(self)?))
    }
}

fn request_schema() -> Value {
    let sha = json!({"type":"string","pattern":"^[0-9a-f]{64}$"});
    let id = json!({"type":"string","format":"uuid"});
    let clock = json!({"type":"object","properties":{
        "video_frames":{"type":"integer","minimum":1},"fps_num":{"type":"integer","minimum":1,"maximum":120000},
        "fps_den":{"type":"integer","minimum":1,"maximum":100000},"sample_frames":{"type":"integer","minimum":19200,"maximum":720000}},
        "required":["video_frames","fps_num","fps_den","sample_frames"],"additionalProperties":false});
    let binding = json!({"type":"object","properties":{
        "project_id":id,"generation":id,"revision":{"type":"integer","minimum":0},"deliverable_id":id,
        "canonical_dependency_sha256":sha,"source_plan_sha256":sha,"source_readiness_sha256":sha,
        "premix_wav_sha256":sha,"premix_pcm_sha256":sha,"clock":clock},
        "required":["project_id","generation","revision","deliverable_id","canonical_dependency_sha256",
            "source_plan_sha256","source_readiness_sha256","premix_wav_sha256","premix_pcm_sha256","clock"],"additionalProperties":false});
    let pins = json!({"type":"object","properties":{"worker_sha256":sha,"python_sha256":sha,"ffmpeg_sha256":sha,
        "ffprobe_sha256":sha,"owner_runtime_profile_sha256":sha,"meter_validation_sha256":sha},
        "required":["worker_sha256","python_sha256","ffmpeg_sha256","ffprobe_sha256","owner_runtime_profile_sha256","meter_validation_sha256"],
        "additionalProperties":false});
    json!({"type":"object","properties":{"schema":{"const":REQUEST_SCHEMA},"binding":binding,
        "source_binding_sha256":sha,"role":{"enum":["canonical_premix","mastered_wav","final_aac"]},"input_sha256":sha,
        "mastering_plan_sha256":{"anyOf":[sha,{"type":"null"}]},
        "gain_milli_db":{"anyOf":[{"type":"integer","minimum":-60000,"maximum":24000},{"type":"null"}]},
        "native_request_id":{"anyOf":[{"type":"string","pattern":"^mxav-[0-9a-f]{36}$"},{"type":"null"}]},
        "width":{"type":"integer","minimum":1,"maximum":4096},"height":{"type":"integer","minimum":1,"maximum":4096},"pins":pins},
        "required":["schema","binding","source_binding_sha256","role","input_sha256","mastering_plan_sha256",
            "gain_milli_db","native_request_id","width","height","pins"],"additionalProperties":false})
}

pub fn measurement_capability() -> Capability {
    Capability { descriptor: CommandDescriptor {
        name: COMMAND.into(), version: "1".into(),
        description: "Measure the sealed canonical premix, native mastered WAV or final AAC with the fixed readonly method".into(),
        input_schema: request_schema(),
        output_schema: json!({"type":"object","properties":{"schema":{"const":RESULT_SCHEMA},"binding_sha256":{"type":"string"},
            "reader_sha256":{"const":READER_SHA256},"worker_sha256":{"type":"string"},"status":{"const":"MEASURED"},
            "report":{"type":"object"},"transfer":{"anyOf":[{"type":"object"},{"type":"null"}]},
            "clock":{"anyOf":[{"type":"object"},{"type":"null"}]},"raw_metadata":{"type":"object"},
            "host_job":{"type":"object"},"host_request_id":{"type":"string"},"scope":{"const":SCOPE}},
            "required":["schema","binding_sha256","reader_sha256","worker_sha256","status","report","transfer","clock",
                "raw_metadata","host_job","host_request_id","scope"],"additionalProperties":false}),
        requires: vec![crate::PROVIDER_ID.into()], risk: Risk::ReadOnly, idempotency: Idempotency::ReadOnly,
        timeout_ms: REQUIRED_REQUEST_TIMEOUT_MS, dry_run: false, interactive_consent: false,
        backends: vec![crate::PROVIDER_ID.into()],
    }, aliases: vec![], tags: vec!["audio".into(), "measurement".into()], object_types: vec![] }
}

fn exact(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|object| {
        object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
    })
}
fn finite_or_null(value: &Value) -> bool {
    value.is_null() || value.as_f64().is_some_and(f64::is_finite)
}

fn validate_output(
    request: &AudioMeasurementRequest,
    output: ToolExecutionOutput,
) -> Result<Value> {
    if output.stdout.len() > MAX_RESULT_BYTES || !output.stderr.is_empty() || output.exit_code != 0
    {
        return Err(Error::new(
            ErrorCode::BackendFailed,
            "Original Host audio measurement was rejected",
        ));
    }
    let result: Value = serde_json::from_slice(&output.stdout).map_err(|_| {
        Error::new(
            ErrorCode::PluginProtocolError,
            "Host audio meter returned invalid JSON",
        )
    })?;
    let fail = || {
        Error::new(
            ErrorCode::PluginProtocolError,
            "Host audio meter returned inconsistent closed evidence",
        )
    };
    if !exact(
        &result,
        &[
            "schema",
            "binding_sha256",
            "reader_sha256",
            "worker_sha256",
            "status",
            "report",
            "transfer",
            "clock",
            "raw_metadata",
            "scope",
        ],
    ) || result["schema"] != RESULT_SCHEMA
        || result["binding_sha256"] != request.binding_sha256()?
        || result["reader_sha256"] != READER_SHA256
        || result["worker_sha256"] != compiled_measurement_worker_sha256()
        || result["status"] != "MEASURED"
        || result["scope"] != SCOPE
    {
        return Err(fail());
    }
    let report = &result["report"];
    if !exact(
        report,
        &[
            "schema",
            "method",
            "role",
            "source_binding_sha256",
            "input_sha256",
            "decoded_f64le_sha256",
            "decoded_sample_frames",
            "logical_sample_frames",
            "declared_decoder_suffix_budget_samples",
            "sample_rate",
            "channels",
            "integrated_lufs",
            "programme_true_peak_dbtp_upper",
            "physical_true_peak_dbtp_upper",
            "integrated_uncertainty_lu",
            "true_peak_uncertainty_db",
            "programme_interval",
            "peak_flush_zero_samples",
            "complete_input_decoded",
            "pins",
        ],
    ) || report["schema"] != "motionwright.audio-measurement/1"
        || report["method"] != METHOD
        || report["role"] != serde_json::to_value(request.role)?
        || report["source_binding_sha256"] != request.source_binding_sha256
        || report["input_sha256"] != request.input_sha256
        || report["pins"] != serde_json::to_value(&request.pins)?
        || report["sample_rate"] != 48000
        || report["channels"] != 2
        || report["complete_input_decoded"] != true
        || report["logical_sample_frames"] != request.binding.clock.sample_frames
        || report["programme_interval"] != json!([0, request.binding.clock.sample_frames])
        || report["integrated_uncertainty_lu"] != json!(0.101)
        || report["true_peak_uncertainty_db"] != json!(0.1)
        || report["peak_flush_zero_samples"] != 4800
        || [
            "integrated_lufs",
            "programme_true_peak_dbtp_upper",
            "physical_true_peak_dbtp_upper",
        ]
        .into_iter()
        .any(|key| !finite_or_null(&report[key]))
    {
        return Err(fail());
    }
    require_sha256(report["decoded_f64le_sha256"].as_str().ok_or_else(fail)?)?;
    let samples = request.binding.clock.sample_frames;
    let decoded = report["decoded_sample_frames"].as_u64().ok_or_else(fail)?;
    let suffix = report["declared_decoder_suffix_budget_samples"]
        .as_u64()
        .ok_or_else(fail)?;
    if decoded < samples
        || decoded - samples > suffix
        || suffix
            > if request.role == MeasurementRole::FinalAac {
                1024
            } else {
                0
            }
    {
        return Err(fail());
    }
    if request.role == MeasurementRole::FinalAac {
        if result["clock"]["audio_duration_samples"] != samples
            || result["clock"]["video_frames"] != request.binding.clock.video_frames
            || result["clock"]["decoded_audio_samples_from_frames"] != decoded
            || result["clock"]["packet_priming_and_presentation_checked"] != true
            || result["clock"]["exact_contiguous_frame_timestamps"] != true
        {
            return Err(fail());
        }
    } else if !result["clock"].is_null() {
        return Err(fail());
    }
    if request.role == MeasurementRole::MasteredWav {
        let transfer = &result["transfer"];
        if !exact(
            transfer,
            &[
                "reference_kind",
                "premix_pcm_sha256",
                "expected_pcm_sha256",
                "actual_pcm_sha256",
                "gain_milli_db",
                "all_samples_compared",
                "reader_sha256",
                "content_report_sha256",
                "content_report_retained",
                "content",
            ],
        ) || transfer["reference_kind"] != "analytic_scalar_expectation_not_producer_media"
            || transfer["premix_pcm_sha256"] != request.binding.premix_pcm_sha256
            || transfer["gain_milli_db"].as_i64() != request.gain_milli_db
            || transfer["all_samples_compared"] != true
            || transfer["reader_sha256"] != READER_SHA256
            || transfer["content_report_retained"] != false
            || transfer["content"]["logical_interval"] != json!([0, samples])
            || transfer["content"]["all_logical_samples_compared"] != true
            || transfer["content"]["reference_sample_frames"] != samples
            || transfer["content"]["decoded_sample_frames"] != samples
            || transfer["content"]["decoded_access_unit_suffix_samples"] != 0
        {
            return Err(fail());
        }
        for key in [
            "expected_pcm_sha256",
            "actual_pcm_sha256",
            "content_report_sha256",
        ] {
            require_sha256(transfer[key].as_str().ok_or_else(fail)?)?;
        }
        if transfer["content"]["reference_pcm_sha256"] != transfer["expected_pcm_sha256"]
            || transfer["content"]["decoded_pcm_sha256"] != transfer["actual_pcm_sha256"]
        {
            return Err(fail());
        }
    } else if !result["transfer"].is_null() {
        return Err(fail());
    }
    if !exact(
        &result["raw_metadata"],
        &["integrated", "programme_peak", "physical_peak"],
    ) {
        return Err(fail());
    }
    for key in ["integrated", "programme_peak", "physical_peak"] {
        let raw = &result["raw_metadata"][key];
        if !exact(raw, &["sha256", "bytes", "base64"]) {
            return Err(fail());
        }
        let bytes = BASE64
            .decode(raw["base64"].as_str().ok_or_else(fail)?)
            .map_err(|_| fail())?;
        if bytes.is_empty()
            || bytes.len() > 128 * 1024
            || raw["bytes"].as_u64() != Some(bytes.len() as u64)
            || raw["sha256"] != sha256(&bytes)
        {
            return Err(fail());
        }
    }
    Ok(result)
}

fn retain_job(
    mut error: Error,
    request: &AudioMeasurementRequest,
    job: Option<&RuntimeToolJob>,
) -> Error {
    if let Some(job) = job {
        error
            .candidates
            .push(format!("motionwright-audio-measure-host-job:{}", job.id));
    }
    if let Ok(binding) = request.binding_sha256() {
        error
            .candidates
            .push(format!("motionwright-audio-measure-binding:{binding}"));
    }
    error
}

fn measurement_stdin(request: &AudioMeasurementRequest) -> Result<Vec<u8>> {
    request.validate()?;
    if sha256(READER_SOURCE.as_bytes()) != READER_SHA256 {
        return Err(Error::new(
            ErrorCode::Conflict,
            "Compiled canonical reader does not match its frozen pin",
        ));
    }
    let packet = json!({"binding_json":serde_json::to_string(request)?,"binding_sha256":request.binding_sha256()?,
        "worker_sha256":compiled_measurement_worker_sha256()});
    let encoded = BASE64.encode(serde_json::to_vec(&packet)?);
    // This literal contains compile-time pinned source only. JSON string
    // escaping is valid for this Python string literal; no request text is
    // inserted as code. Keep the frozen reader single-sourced via include_str
    // and avoid base64-expanding its 35 KiB beyond SDK54d's 64 KiB stdin limit.
    let reader_literal = serde_json::to_string(READER_SOURCE)?;
    let stdin =
        format!("{MEASURE_SOURCE}\nraise SystemExit(main(\"{encoded}\",{reader_literal}))\n")
            .into_bytes();
    if stdin.len() > 64 * 1024 {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Compiled audio meter exceeds its Host program budget",
        ));
    }
    Ok(stdin)
}

/// Called only by NativeReadbackDriver::execute_with_context after its owner
/// registration. The ordinary execute() remains PermissionDenied in lib.rs.
pub async fn execute_audio_measurement(
    expected_descriptor: &str,
    args: Value,
    context: DriverExecutionContext,
) -> Result<Value> {
    context.check_cancelled()?;
    if expected_descriptor != descriptor_digest(&measurement_capability().descriptor)? {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Audio measurement capability descriptor changed",
        ));
    }
    if serde_json::to_vec(&args)?.len() > MAX_REQUEST_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "Audio measurement request exceeds its byte budget",
        ));
    }
    let request: AudioMeasurementRequest = serde_json::from_value(args)?;
    let stdin = measurement_stdin(&request)?;
    let argv = vec![
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
    ];
    let job = context
        .start_runtime_tool_job_args(
            PYTHON_TOOL,
            argv,
            stdin,
            Duration::from_millis(HOST_TIMEOUT_MS),
            Some(RuntimeToolCwd {
                mount: MEDIA_MOUNT.into(),
                relative: String::new(),
            }),
        )
        .await
        .map_err(|error| retain_job(error, &request, None))?;
    // Supervise this exact original Host job. No replacement or implicit retry.
    let output = context
        .wait_runtime_tool_job(&job)
        .await
        .map_err(|error| retain_job(error, &request, Some(&job)))?;
    let mut result = validate_output(&request, output)
        .map_err(|error| retain_job(error, &request, Some(&job)))?;
    result["host_job"] = serde_json::to_value(&job)?;
    result["host_request_id"] = json!(context.request_id());
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> AudioMeasurementRequest {
        let binding = SourceBinding {
            project_id: Uuid::from_u128(1),
            generation: Uuid::from_u128(2),
            revision: 7,
            deliverable_id: Uuid::from_u128(3),
            canonical_dependency_sha256: "a".repeat(64),
            source_plan_sha256: "b".repeat(64),
            source_readiness_sha256: "c".repeat(64),
            premix_wav_sha256: "d".repeat(64),
            premix_pcm_sha256: "e".repeat(64),
            clock: SourceClock {
                video_frames: 60,
                fps_num: 30,
                fps_den: 1,
                sample_frames: 96_000,
            },
        };
        AudioMeasurementRequest {
            schema: REQUEST_SCHEMA.into(),
            source_binding_sha256: binding.sha256().unwrap(),
            binding,
            role: MeasurementRole::CanonicalPremix,
            input_sha256: "d".repeat(64),
            mastering_plan_sha256: None,
            gain_milli_db: None,
            native_request_id: None,
            width: 320,
            height: 180,
            pins: MeasurementPins {
                worker_sha256: compiled_measurement_worker_sha256(),
                python_sha256: "1".repeat(64),
                ffmpeg_sha256: "2".repeat(64),
                ffprobe_sha256: "3".repeat(64),
                owner_runtime_profile_sha256: "4".repeat(64),
                meter_validation_sha256: "5".repeat(64),
            },
        }
    }

    // Authored transport-shape fixture only. It is not an executed worker,
    // physical meter result, calibration, Host receipt or grant.
    fn response_shape(input: &AudioMeasurementRequest) -> Value {
        let raw = b"authored protocol-shape fixture; no physical measurement";
        let metadata = json!({"sha256":sha256(raw),"bytes":raw.len(),"base64":BASE64.encode(raw)});
        json!({"schema":RESULT_SCHEMA,"binding_sha256":input.binding_sha256().unwrap(),
            "reader_sha256":READER_SHA256,"worker_sha256":compiled_measurement_worker_sha256(),"status":"MEASURED",
            "report":{"schema":"motionwright.audio-measurement/1","method":METHOD,"role":"canonical_premix",
                "source_binding_sha256":input.source_binding_sha256,"input_sha256":input.input_sha256,
                "decoded_f64le_sha256":"f".repeat(64),"decoded_sample_frames":96000,"logical_sample_frames":96000,
                "declared_decoder_suffix_budget_samples":0,"sample_rate":48000,"channels":2,"integrated_lufs":-20.0,
                "programme_true_peak_dbtp_upper":-8.0,"physical_true_peak_dbtp_upper":-8.0,
                "integrated_uncertainty_lu":0.101,"true_peak_uncertainty_db":0.1,"programme_interval":[0,96000],
                "peak_flush_zero_samples":4800,"complete_input_decoded":true,"pins":input.pins},
            "transfer":null,"clock":null,"raw_metadata":{"integrated":metadata,"programme_peak":metadata,"physical_peak":metadata},"scope":SCOPE})
    }
    fn output(value: &Value) -> ToolExecutionOutput {
        ToolExecutionOutput {
            exit_code: 0,
            stdout: serde_json::to_vec(value).unwrap(),
            stderr: vec![],
        }
    }

    #[test]
    fn audio_measurement_descriptor_is_readonly_and_has_no_caller_process_controls() {
        let descriptor = measurement_capability().descriptor;
        assert!(matches!(descriptor.risk, Risk::ReadOnly));
        assert!(matches!(descriptor.idempotency, Idempotency::ReadOnly));
        let original = serde_json::to_value(request()).unwrap();
        for key in [
            "argv",
            "path",
            "ffmpeg",
            "normalizer",
            "limits",
            "exportable",
            "host_job",
        ] {
            let mut changed = original.clone();
            changed[key] = json!("injected");
            assert!(
                serde_json::from_value::<AudioMeasurementRequest>(changed).is_err(),
                "{key}"
            );
        }
    }

    #[test]
    fn changed_clock_identity_or_worker_pin_rejects_before_host_stdin_exists() {
        for key in ["sample_frames", "fps_den"] {
            let mut value = serde_json::to_value(request()).unwrap();
            value["binding"]["clock"][key] = json!(0);
            let changed = serde_json::from_value(value).unwrap();
            assert!(measurement_stdin(&changed).is_err());
        }
        let mut changed = request();
        changed.binding.generation = Uuid::from_u128(77);
        assert!(measurement_stdin(&changed).is_err());
        let mut changed = request();
        changed.pins.worker_sha256 = "0".repeat(64);
        assert_eq!(
            measurement_stdin(&changed).unwrap_err().code,
            ErrorCode::StaleReference
        );
        let mut value = serde_json::to_value(request()).unwrap();
        value["binding"]["clock"]["video_frames"] = json!(true);
        assert!(serde_json::from_value::<AudioMeasurementRequest>(value).is_err());
    }

    #[test]
    fn each_closed_role_fits_the_real_sdk64_stdin_budget_without_decoder_execution() {
        for role in [
            MeasurementRole::CanonicalPremix,
            MeasurementRole::MasteredWav,
            MeasurementRole::FinalAac,
        ] {
            let mut input = request();
            input.role = role;
            if role != MeasurementRole::CanonicalPremix {
                input.mastering_plan_sha256 = Some("6".repeat(64));
                input.gain_milli_db = Some(-60_000);
                input.input_sha256 = "7".repeat(64);
            }
            if role == MeasurementRole::FinalAac {
                input.native_request_id = Some(format!("mxav-{}", "8".repeat(36)));
            }
            let program = measurement_stdin(&input).unwrap();
            assert!(program.len() <= 64 * 1024);
            assert!(program.starts_with(MEASURE_SOURCE.as_bytes()));
            assert!(serde_json::to_vec(&input).unwrap().len() <= MAX_REQUEST_BYTES);
        }
    }

    #[test]
    fn wrong_scope_partial_decode_boolean_measurement_or_foreign_hash_rejects() {
        let input = request();
        let original = response_shape(&input);
        assert!(validate_output(&input, output(&original)).is_ok());
        for (pointer, replacement) in [
            ("/scope", json!("export_approved")),
            ("/report/decoded_sample_frames", json!(95999)),
            ("/report/integrated_lufs", json!(true)),
            ("/report/physical_true_peak_dbtp_upper", json!("-8.0")),
            ("/report/complete_input_decoded", json!(false)),
            ("/report/input_sha256", json!("0".repeat(64))),
            ("/report/declared_decoder_suffix_budget_samples", json!(448)),
            ("/report/integrated_uncertainty_lu", json!(0.0)),
        ] {
            let mut changed = original.clone();
            *changed.pointer_mut(pointer).unwrap() = replacement;
            assert!(
                validate_output(&input, output(&changed)).is_err(),
                "{pointer}"
            );
        }
    }

    #[test]
    fn truncated_corrupt_metadata_stderr_and_nonzero_exit_do_not_become_measurement() {
        let input = request();
        let original = response_shape(&input);
        let mut changed = original.clone();
        changed["raw_metadata"]["integrated"]["sha256"] = json!("0".repeat(64));
        assert!(validate_output(&input, output(&changed)).is_err());
        let mut changed = original.clone();
        changed["raw_metadata"]["integrated"]["bytes"] = json!(1);
        assert!(validate_output(&input, output(&changed)).is_err());
        let mut bad = output(&original);
        bad.stdout.pop();
        assert!(validate_output(&input, bad).is_err());
        let mut bad = output(&original);
        bad.stderr = b"partial diagnostics".to_vec();
        assert!(validate_output(&input, bad).is_err());
        let mut bad = output(&original);
        bad.exit_code = 2;
        assert!(validate_output(&input, bad).is_err());
    }

    #[test]
    fn unknown_host_error_keeps_code_uncertainty_and_original_progress() {
        let mut error =
            Error::new(ErrorCode::Timeout, "original").recipe_progress(1, "original-stage");
        error.outcome_known = false;
        error.candidates.push("original-reference".into());
        let progress = serde_json::to_value(&error.progress).unwrap();
        let retained = retain_job(error, &request(), None);
        assert_eq!(retained.code, ErrorCode::Timeout);
        assert!(!retained.outcome_known);
        assert_eq!(serde_json::to_value(&retained.progress).unwrap(), progress);
        assert_eq!(retained.candidates[0], "original-reference");
    }
}
