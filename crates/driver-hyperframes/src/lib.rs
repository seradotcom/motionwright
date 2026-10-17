//! One owner-authorized SDK Host job for a closed HyperFrames source.
//!
//! No subprocess, shell, scheduler or render loop lives in this driver. The
//! runner calls the pinned public Producer. Cancellation and reaping belong to
//! the existing Host. A completed renderer result is not a pixel-fidelity or
//! export-authorization proof.

pub mod registration;

use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Component, Path};
use std::time::Duration;

use async_trait::async_trait;
use motionwright_hyperframes_profile::{
    AdmittedSource, HYPERFRAMES_REVISION, HYPERFRAMES_VERSION, MAX_CARRIER_BYTES, PROFILE_ID,
    SourceClock, admit_source, sha256,
};
use semwright_driver_sdk::{
    Capability, Driver, DriverExecutionContext, DriverInterfaces, RuntimeToolArg, RuntimeToolCwd,
    RuntimeToolJob, ToolExecutionOutput, descriptor_digest, workspace_mount,
};
use semwright_types::{CommandDescriptor, Error, ErrorCode, Idempotency, Result, Risk};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use uuid::Uuid;

pub const DRIVER_ID: &str = "hyperframes";
pub const PROVIDER_ID: &str = "driver:hyperframes";
pub const RENDER_COMMAND: &str = "driver.hyperframes.source.render";
pub const REQUEST_SCHEMA: &str = "motionwright.hyperframes.source-render/1";
pub const RESULT_SCHEMA: &str = "motionwright.hyperframes.source-render-result/1";
pub const OWNER_SCHEMA: &str = "motionwright.hyperframes.owner-runtime/1";
pub const SOURCE_MOUNT: &str = "hyperframes-source";
pub const RUNTIME_MOUNT: &str = "hyperframes-runtime";
pub const OPERATION_MOUNT: &str = "hyperframes-operation";
pub const NODE_TOOL: &str = "hyperframes-node";
pub const CHROME_TOOL: &str = "hyperframes-chrome";
pub const FFMPEG_TOOL: &str = "hyperframes-ffmpeg";
pub const FFPROBE_TOOL: &str = "hyperframes-ffprobe";
pub const HOST_JOB_TIMEOUT_MS: u64 = 240_000;
pub const DRIVER_REQUEST_TIMEOUT_MS: u64 = 260_000;
pub const REQUIRED_PRODUCTION_DEADLINE_SECS: u64 = 270;
pub const MAX_OWNER_PROFILE_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_RUNTIME_FILES: usize = 20_000;
pub const MAX_RUNTIME_BYTES: u64 = 1024 * 1024 * 1024;
pub const MAX_MEDIA_BYTES: u64 = 512 * 1024 * 1024;
pub const MAX_RESULT_BYTES: usize = 32 * 1024;
const MAX_REQUEST_BYTES: usize = 8 * 1024;
const MAX_STDIN_BYTES: usize = 64 * 1024;
pub const RUNNER_SOURCE: &str = include_str!("runner.mjs");

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct RuntimeFile {
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ToolPins {
    pub node_sha256: String,
    pub chrome_sha256: String,
    pub ffmpeg_sha256: String,
    pub ffprobe_sha256: String,
}

impl ToolPins {
    pub fn validate(&self) -> Result<()> {
        for value in [
            &self.node_sha256,
            &self.chrome_sha256,
            &self.ffmpeg_sha256,
            &self.ffprobe_sha256,
        ] {
            digest(value)?;
        }
        Ok(())
    }
}

/// Installation data read only from the owner-granted runtime root. Its JSON
/// representation is not registration, access consent or a verified runtime.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct OwnerRuntimeProfile {
    pub schema: String,
    pub producer_revision: String,
    pub producer_version: String,
    pub runner_sha256: String,
    pub tool_pins: ToolPins,
    pub chromium_major: u32,
    pub files: Vec<RuntimeFile>,
}

impl OwnerRuntimeProfile {
    pub fn validate(&self) -> Result<()> {
        if self.schema != OWNER_SCHEMA
            || self.producer_revision != HYPERFRAMES_REVISION
            || self.producer_version != HYPERFRAMES_VERSION
            || self.runner_sha256 != sha256(RUNNER_SOURCE.as_bytes())
            || !(100..=999).contains(&self.chromium_major)
            || self.files.is_empty()
            || self.files.len() > MAX_RUNTIME_FILES
        {
            return Err(Error::invalid(
                "HyperFrames owner profile is outside the pinned contract",
            ));
        }
        self.tool_pins.validate()?;
        let mut paths = BTreeSet::new();
        let mut total = 0u64;
        for file in &self.files {
            relative_path(&file.path)?;
            digest(&file.sha256)?;
            if file.path == "owner-profile.json"
                || file.bytes == 0
                || file.bytes > 128 * 1024 * 1024
                || !paths.insert(file.path.to_ascii_lowercase())
            {
                return Err(Error::invalid(
                    "HyperFrames runtime file closure is invalid",
                ));
            }
            total = total
                .checked_add(file.bytes)
                .ok_or_else(|| Error::invalid("HyperFrames runtime byte budget overflow"))?;
            if total > MAX_RUNTIME_BYTES {
                return Err(Error::new(
                    ErrorCode::ResourceExhausted,
                    "HyperFrames runtime exceeds its byte budget",
                ));
            }
        }
        for required in [
            "node_modules/@hyperframes/producer/dist/index.js",
            "node_modules/@hyperframes/producer/package.json",
            "node_modules/@hyperframes/engine/dist/index.js",
            "node_modules/@hyperframes/engine/package.json",
            "node_modules/@hyperframes/core/package.json",
            "bun.lock",
        ] {
            if !paths.contains(required) {
                return Err(Error::invalid(
                    "HyperFrames runtime is missing a pinned package entry",
                ));
            }
        }
        Ok(())
    }

    pub fn bytes(&self) -> Result<Vec<u8>> {
        self.validate()?;
        let bytes = serde_json::to_vec(self)?;
        if bytes.len() > MAX_OWNER_PROFILE_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "HyperFrames owner profile is too large",
            ));
        }
        Ok(bytes)
    }
}

/// Backend-selected identity and source bindings. No paths, executable names,
/// argv, HTML, render options, permissions or accepted verdicts are caller data.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SourceRenderRequest {
    pub schema: String,
    pub project_id: Uuid,
    pub generation: Uuid,
    pub revision: u64,
    pub scene_id: Uuid,
    pub capsule_id: Uuid,
    pub operation_id: Uuid,
    pub source_sha256: String,
    pub canonical_dependency_sha256: String,
    pub owner_profile_sha256: String,
}

impl SourceRenderRequest {
    pub fn validate(&self) -> Result<()> {
        if self.schema != REQUEST_SCHEMA
            || self.revision > 9_007_199_254_740_991
            || [
                self.project_id,
                self.generation,
                self.scene_id,
                self.capsule_id,
                self.operation_id,
            ]
            .into_iter()
            .any(|id| id.is_nil())
        {
            return Err(Error::invalid("HyperFrames source identity is invalid"));
        }
        for value in [
            &self.source_sha256,
            &self.canonical_dependency_sha256,
            &self.owner_profile_sha256,
        ] {
            digest(value)?;
        }
        Ok(())
    }
    pub fn source_name(&self) -> Result<String> {
        self.validate()?;
        Ok(format!("mw-hf-{}.json", self.source_sha256))
    }
    pub fn operation_name(&self) -> Result<String> {
        self.validate()?;
        Ok(format!("hf-{}", self.operation_id.simple()))
    }
    pub fn binding_sha256(&self) -> Result<String> {
        self.validate()?;
        Ok(sha256(&serde_json::to_vec(self)?))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FileProof {
    pub name: String,
    pub bytes: u64,
    pub sha256: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ProducerResult {
    pub schema: String,
    pub request_sha256: String,
    pub carrier_sha256: String,
    pub html_sha256: String,
    pub runtime_profile_sha256: String,
    pub runtime_closure_verified: bool,
    pub runner_sha256: String,
    pub producer_revision: String,
    pub producer_version: String,
    pub producer_job_id: String,
    pub producer_status: String,
    pub operation_id: Uuid,
    pub clock: SourceClock,
    pub width: u32,
    pub height: u32,
    pub video: FileProof,
    pub retained_source: FileProof,
    pub diagnostic: FileProof,
    pub effective_config: FileProof,
    pub complete_report_retained: bool,
    pub pixel_fidelity_verified: bool,
    pub editable_roundtrip_verified: bool,
    pub export_authorized: bool,
}

pub fn interfaces() -> DriverInterfaces {
    DriverInterfaces {
        dynamic_capabilities: true,
        cooperative_cancellation: true,
        events: true,
        host_tools: true,
        health: true,
        ..Default::default()
    }
}

pub fn render_capability() -> Capability {
    let mut properties = serde_json::Map::new();
    properties.insert("schema".into(), json!({"const":REQUEST_SCHEMA}));
    for name in [
        "project_id",
        "generation",
        "scene_id",
        "capsule_id",
        "operation_id",
    ] {
        properties.insert(name.into(), json!({"type":"string", "format":"uuid"}));
    }
    properties.insert("revision".into(), json!({"type":"integer", "minimum":0}));
    for name in [
        "source_sha256",
        "canonical_dependency_sha256",
        "owner_profile_sha256",
    ] {
        properties.insert(
            name.into(),
            json!({"type":"string", "pattern":"^[0-9a-f]{64}$"}),
        );
    }
    let required: Vec<_> = properties.keys().cloned().collect();
    Capability {
        descriptor: CommandDescriptor {
            name: RENDER_COMMAND.into(), version: "1".into(),
            description: "Render one finite, resource-closed native HyperFrames source through the owner-authorized Host".into(),
            input_schema: json!({"type":"object", "properties":properties,"required":required,"additionalProperties":false}),
            output_schema: json!({"type":"object", "required":["schema","result","host_job","host_request_id","request_sha256","evidence_scope"],
                "properties":{"schema":{"const":"motionwright.hyperframes.host-result/1"},"result":{"type":"object"},"host_job":{"type":"object"},"host_request_id":{"type":"string"},"request_sha256":{"type":"string"},"evidence_scope":{"const":"NATIVE_PRODUCER_COMPLETION_AND_SOURCE_CUSTODY_ONLY"}},"additionalProperties":false}),
            requires: vec![PROVIDER_ID.into()], risk: Risk::CodeExecution,
            idempotency: Idempotency::NonIdempotent, timeout_ms: DRIVER_REQUEST_TIMEOUT_MS,
            dry_run: false, interactive_consent: true, backends: vec![PROVIDER_ID.into()],
        }, aliases: vec![], tags: vec!["native-source".into(),"video".into()], object_types: vec![],
    }
}

pub fn parse_request(args: Value) -> Result<SourceRenderRequest> {
    if serde_json::to_vec(&args)?.len() > MAX_REQUEST_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "HyperFrames request exceeds its byte budget",
        ));
    }
    let request: SourceRenderRequest = serde_json::from_value(args)?;
    request.validate()?;
    Ok(request)
}

pub fn runtime_args() -> Vec<RuntimeToolArg> {
    vec![
        RuntimeToolArg::Literal {
            value: "--input-type=module".into(),
        },
        RuntimeToolArg::Literal { value: "-".into() },
        RuntimeToolArg::MountPath {
            mount: SOURCE_MOUNT.into(),
            relative: String::new(),
        },
        RuntimeToolArg::MountPath {
            mount: RUNTIME_MOUNT.into(),
            relative: String::new(),
        },
        RuntimeToolArg::MountPath {
            mount: OPERATION_MOUNT.into(),
            relative: String::new(),
        },
        RuntimeToolArg::ToolPath {
            tool: CHROME_TOOL.into(),
        },
        RuntimeToolArg::ToolPath {
            tool: FFMPEG_TOOL.into(),
        },
        RuntimeToolArg::ToolPath {
            tool: FFPROBE_TOOL.into(),
        },
    ]
}

pub fn runtime_stdin(
    request: &SourceRenderRequest,
    source: &AdmittedSource,
    owner: &OwnerRuntimeProfile,
) -> Result<Vec<u8>> {
    request.validate()?;
    owner.validate()?;
    if request.source_sha256 != source.readiness().carrier_sha256 {
        return Err(Error::new(
            ErrorCode::Conflict,
            "HyperFrames source changed before Host dispatch",
        ));
    }
    let packet = json!({"request":request,"request_sha256":request.binding_sha256()?,"source_name":request.source_name()?,
        "operation_name":request.operation_name()?,"html_sha256":source.readiness().html_sha256,
        "source_profile":PROFILE_ID,"clock":source.document().clock,"width":source.document().width,"height":source.document().height,
        "resource_files":source.resources().iter().map(|(name,bytes)|FileProof{name:name.clone(),bytes:bytes.len() as u64,sha256:sha256(bytes)}).collect::<Vec<_>>(),
        "runner_sha256":sha256(RUNNER_SOURCE.as_bytes()),"tool_pins":owner.tool_pins});
    // JSON is encoded as a string literal, never interpolated as executable
    // syntax. The immutable runner is the sole program supplied to Node.
    let literal = serde_json::to_string(&serde_json::to_string(&packet)?)?;
    let bytes = format!("{RUNNER_SOURCE}\nawait main(JSON.parse({literal}));\n").into_bytes();
    if bytes.len() > MAX_STDIN_BYTES {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "HyperFrames runner exceeds Host stdin bounds",
        ));
    }
    Ok(bytes)
}

fn digest(value: &str) -> Result<()> {
    motionwright_hyperframes_profile::require_sha256(value)
        .map_err(|_| Error::invalid("HyperFrames requires a canonical SHA-256 digest"))
}

pub fn relative_path(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 512
        || value.split('/').count() > 24
        || value.contains('\\')
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/._-@+".contains(&b))
        || value.split('/').any(|part| {
            let stem = part
                .split('.')
                .next()
                .unwrap_or_default()
                .to_ascii_uppercase();
            part.is_empty()
                || part == "."
                || part == ".."
                || part.ends_with('.')
                || matches!(
                    stem.as_str(),
                    "CON"
                        | "PRN"
                        | "AUX"
                        | "NUL"
                        | "COM1"
                        | "COM2"
                        | "COM3"
                        | "COM4"
                        | "COM5"
                        | "COM6"
                        | "COM7"
                        | "COM8"
                        | "COM9"
                        | "LPT1"
                        | "LPT2"
                        | "LPT3"
                        | "LPT4"
                        | "LPT5"
                        | "LPT6"
                        | "LPT7"
                        | "LPT8"
                        | "LPT9"
                )
        })
    {
        return Err(Error::invalid(
            "HyperFrames closure path is not portable and relative",
        ));
    }
    Ok(())
}

/// A bounded handle read. It does not claim to eliminate every concurrent
/// pathname race; Host grants and immutable mounts remain required.
pub fn read_bounded_file(path: &Path, cap: usize) -> Result<Vec<u8>> {
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor)?;
        if metadata.file_type().is_symlink() || reparse(&metadata) {
            return Err(Error::invalid(
                "HyperFrames input contains a link or reparse point",
            ));
        }
    }
    if path
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        return Err(Error::invalid("HyperFrames input path is not normalized"));
    }
    let mut file = File::open(path)?;
    let before = file.metadata()?;
    if !before.is_file() || before.len() == 0 || before.len() > cap as u64 {
        return Err(Error::new(
            ErrorCode::ResourceExhausted,
            "HyperFrames input is not a bounded regular file",
        ));
    }
    let initial_size = before.len() as usize;
    let mut bytes = Vec::with_capacity(initial_size);
    let mut chunk = [0u8; 64 * 1024];
    loop {
        let allowance = chunk.len().min(initial_size - bytes.len() + 1);
        let read = file.read(&mut chunk[..allowance])?;
        if read == 0 {
            break;
        }
        bytes.extend_from_slice(&chunk[..read]);
        if bytes.len() > initial_size {
            return Err(Error::new(
                ErrorCode::Conflict,
                "HyperFrames input grew while being read",
            ));
        }
    }
    let after = file.metadata()?;
    let named = fs::symlink_metadata(path)?;
    if bytes.len() != initial_size
        || !same_file(&before, &after)
        || !same_file(&after, &named)
        || named.file_type().is_symlink()
        || reparse(&named)
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "HyperFrames input changed while being read",
        ));
    }
    Ok(bytes)
}

fn same_file(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    if !a.is_file() || !b.is_file() || a.len() != b.len() || a.modified().ok() != b.modified().ok()
    {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if a.dev() != b.dev() || a.ino() != b.ino() || a.nlink() != 1 || b.nlink() != 1 {
            return false;
        }
    }
    true
}
#[cfg(windows)]
fn reparse(metadata: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & 0x400 != 0
}
#[cfg(not(windows))]
fn reparse(_: &fs::Metadata) -> bool {
    false
}

pub fn validate_output(
    request: &SourceRenderRequest,
    source: &AdmittedSource,
    owner: &OwnerRuntimeProfile,
    output: ToolExecutionOutput,
) -> Result<ProducerResult> {
    // A reaped process does not prove which native artifact it committed. A
    // missing/malformed/mismatched semantic result keeps the original uncertain.
    validate_output_fields(request, source, owner, output).map_err(Error::uncertain)
}

fn validate_output_fields(
    request: &SourceRenderRequest,
    source: &AdmittedSource,
    owner: &OwnerRuntimeProfile,
    output: ToolExecutionOutput,
) -> Result<ProducerResult> {
    if output.exit_code != 0 || !output.stderr.is_empty() || output.stdout.len() > MAX_RESULT_BYTES
    {
        return Err(Error::new(
            ErrorCode::BackendFailed,
            "HyperFrames original Host job did not return a closed successful result",
        ));
    }
    let result: ProducerResult = serde_json::from_slice(&output.stdout)?;
    if result.schema != RESULT_SCHEMA
        || result.request_sha256 != request.binding_sha256()?
        || result.carrier_sha256 != request.source_sha256
        || result.html_sha256 != source.readiness().html_sha256
        || result.runtime_profile_sha256 != request.owner_profile_sha256
        || !result.runtime_closure_verified
        || result.runner_sha256 != owner.runner_sha256
        || result.producer_revision != HYPERFRAMES_REVISION
        || result.producer_version != HYPERFRAMES_VERSION
        || result.producer_status != "complete"
        || !Uuid::parse_str(&result.producer_job_id).is_ok_and(|id| !id.is_nil())
        || result.operation_id != request.operation_id
        || result.clock != source.document().clock
        || result.width != source.document().width
        || result.height != source.document().height
        || !result.complete_report_retained
        || result.pixel_fidelity_verified
        || result.editable_roundtrip_verified
        || result.export_authorized
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "HyperFrames result does not match the admitted source and original request",
        ));
    }
    for (proof, name, cap) in [
        (&result.video, "output.mp4", MAX_MEDIA_BYTES),
        (
            &result.retained_source,
            "source.json",
            MAX_CARRIER_BYTES as u64,
        ),
        (&result.diagnostic, "diagnostic.jsonl", 4 * 1024 * 1024),
        (&result.effective_config, "effective-config.json", 64 * 1024),
    ] {
        digest(&proof.sha256)?;
        if proof.name != name || proof.bytes == 0 || proof.bytes > cap {
            return Err(Error::invalid(
                "HyperFrames result contains a foreign or oversized artifact",
            ));
        }
    }
    if result.retained_source.sha256 != request.source_sha256
        || result.retained_source.bytes != source.export_bytes().len() as u64
    {
        return Err(Error::new(
            ErrorCode::Conflict,
            "HyperFrames retained source differs from the admitted original",
        ));
    }
    Ok(result)
}

/// Re-read the fixed artifacts under the owner-selected original operation.
/// This proves bytes only; it is neither execution provenance nor a decoder,
/// and cannot turn a caller-supplied ProducerResult into an export permission.
pub fn verify_result_artifact_bytes(
    operation_root: &Path,
    request: &SourceRenderRequest,
    source: &AdmittedSource,
    result: &ProducerResult,
) -> Result<()> {
    let observe = || -> Result<()> {
        let operation = operation_root.join(request.operation_name()?);
        for (proof, name, cap) in [
            (&result.video, "output.mp4", MAX_MEDIA_BYTES as usize),
            (&result.retained_source, "source.json", MAX_CARRIER_BYTES),
            (&result.diagnostic, "diagnostic.jsonl", 4 * 1024 * 1024),
            (&result.effective_config, "effective-config.json", 64 * 1024),
        ] {
            if proof.name != name {
                return Err(Error::invalid("HyperFrames original artifact name changed"));
            }
            let bytes = read_bounded_file(&operation.join(name), cap)?;
            if bytes.len() as u64 != proof.bytes || sha256(&bytes) != proof.sha256 {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "HyperFrames original artifact bytes changed",
                ));
            }
            if name == "source.json" && bytes != source.export_bytes() {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "HyperFrames original retained source differs",
                ));
            }
        }
        let bytes = read_bounded_file(&operation.join("result.json"), MAX_RESULT_BYTES)?;
        let retained: ProducerResult = serde_json::from_slice(&bytes)?;
        if retained != *result {
            return Err(Error::new(
                ErrorCode::Conflict,
                "HyperFrames original result file and Host result disagree",
            ));
        }
        Ok(())
    };
    observe().map_err(Error::uncertain)
}

fn retain_original(
    mut error: Error,
    request: &SourceRenderRequest,
    job: Option<&RuntimeToolJob>,
) -> Error {
    // Preserve the SDK error code and outcome_known verbatim. These references
    // identify the original attempt; they never authorize another render.
    error
        .candidates
        .push(format!("hyperframes-operation:{}", request.operation_id));
    if let Some(job) = job {
        error
            .candidates
            .push(format!("hyperframes-host-job:{}", job.id));
    }
    error
}

/// Classify an error after entering the Host start/acknowledgement boundary.
/// SDK54 can return Timeout, Cancelled or a missing/malformed reply after its
/// start message was sent, with outcome_known still true. That is not evidence
/// that the original process never started. No receipt or job is fabricated.
/// Local preflight errors return before this function and remain known.
pub fn classify_original_start_error(
    request: &SourceRenderRequest,
    host_request_id: &str,
    error: Error,
) -> Error {
    let mut error = retain_original(error.uncertain(), request, None);
    error
        .candidates
        .push(format!("hyperframes-host-request:{host_request_id}"));
    error
}

pub struct HyperframesDriver;

#[async_trait]
impl Driver for HyperframesDriver {
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
        Ok(vec![render_capability()])
    }
    async fn execute(&mut self, _: &str, _: &str, _: Value) -> Result<Value> {
        Err(Error::new(
            ErrorCode::PermissionDenied,
            "HyperFrames requires an owner-authorized Driver Host context",
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
        let capability = render_capability();
        if command != RENDER_COMMAND
            || descriptor_digest(&capability.descriptor)? != expected_descriptor
        {
            return Err(Error::new(
                ErrorCode::Conflict,
                "HyperFrames capability or descriptor changed",
            ));
        }
        let request = parse_request(args)?;
        let source_root = workspace_mount(SOURCE_MOUNT)?;
        let runtime_root = workspace_mount(RUNTIME_MOUNT)?;
        let operation_root = workspace_mount(OPERATION_MOUNT)?;
        if source_root == operation_root
            || runtime_root == operation_root
            || source_root == runtime_root
        {
            return Err(Error::invalid(
                "HyperFrames source, runtime and operation mounts must be distinct",
            ));
        }
        let source_bytes =
            read_bounded_file(&source_root.join(request.source_name()?), MAX_CARRIER_BYTES)?;
        let source = admit_source(&source_bytes, &request.source_sha256).map_err(|_| {
            Error::new(
                ErrorCode::Unsupported,
                "HyperFrames source is outside the finite closed profile",
            )
        })?;
        let owner_bytes = read_bounded_file(
            &runtime_root.join("owner-profile.json"),
            MAX_OWNER_PROFILE_BYTES,
        )?;
        if sha256(&owner_bytes) != request.owner_profile_sha256 {
            return Err(Error::new(
                ErrorCode::Conflict,
                "HyperFrames owner profile binding changed",
            ));
        }
        let owner: OwnerRuntimeProfile = serde_json::from_slice(&owner_bytes)?;
        owner.validate()?;
        if operation_root
            .join(request.operation_name()?)
            .try_exists()?
        {
            return Err(retain_original(
                Error::new(
                    ErrorCode::Conflict,
                    "HyperFrames original operation already has preserved state",
                )
                .uncertain(),
                &request,
                None,
            ));
        }
        let stdin = runtime_stdin(&request, &source, &owner)?;
        context.check_cancelled()?;
        let job = context
            .start_runtime_tool_job_args(
                NODE_TOOL,
                runtime_args(),
                stdin,
                Duration::from_millis(HOST_JOB_TIMEOUT_MS),
                Some(RuntimeToolCwd {
                    mount: OPERATION_MOUNT.into(),
                    relative: String::new(),
                }),
            )
            .await
            .map_err(|e| classify_original_start_error(&request, context.request_id(), e))?;
        let _ = context.emit_event("hyperframes.original.started", json!({"operation_id":request.operation_id,"host_job":job,"request_sha256":request.binding_sha256()?}));
        let output = context
            .wait_runtime_tool_job(&job)
            .await
            .map_err(|e| retain_original(e, &request, Some(&job)))?;
        let result = validate_output(&request, &source, &owner, output)
            .map_err(|e| retain_original(e, &request, Some(&job)))?;
        verify_result_artifact_bytes(&operation_root, &request, &source, &result)
            .map_err(|e| retain_original(e, &request, Some(&job)))?;
        // Recheck the same closed mounts after the original. No prepare/render
        // is repeated to recover evidence after a mismatch or an unknown error.
        let source_after =
            read_bounded_file(&source_root.join(request.source_name()?), MAX_CARRIER_BYTES)
                .map_err(|e| retain_original(e.uncertain(), &request, Some(&job)))?;
        let owner_after = read_bounded_file(
            &runtime_root.join("owner-profile.json"),
            MAX_OWNER_PROFILE_BYTES,
        )
        .map_err(|e| retain_original(e.uncertain(), &request, Some(&job)))?;
        if source_after != source_bytes || owner_after != owner_bytes {
            return Err(retain_original(
                Error::new(
                    ErrorCode::Conflict,
                    "HyperFrames source or owner profile changed during the original",
                )
                .uncertain(),
                &request,
                Some(&job),
            ));
        }
        Ok(
            json!({"schema":"motionwright.hyperframes.host-result/1","result":result,"host_job":job,"host_request_id":context.request_id(),
            "request_sha256":request.binding_sha256()?,"evidence_scope":"NATIVE_PRODUCER_COMPLETION_AND_SOURCE_CUSTODY_ONLY"}),
        )
    }
}
