use crate::{
    film::{FilmBuildOptions, build_motion_canvas_segments},
    multi_renderer::{blender_export_path, build_blender_contribution, build_manim_plan},
};
use motionwright_domain::{
    AudioCodec, OutputColorSpace, OutputContainer, RevisionStamp, VideoCodec,
};
use motionwright_manim_profile::ManimRenderProfile;
use motionwright_service::StudioService;
use motionwright_storage::{ProductionReceipt, ProductionReceiptInput};
use semwright_media_time::Rate;
use semwright_native_sdk::{
    Error, ErrorCode, Result as NativeResult, Value, json,
    types::{Envelope, SourceKind},
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
    time::Duration,
};
use tokio::{
    io::AsyncReadExt,
    process::Command,
    time::{Instant, sleep, timeout},
};
use uuid::Uuid;

mod mlt_mezzanine;
mod mlt_multisegment_av;
mod mlt_multisegment_sfx;
mod mlt_timeline;
mod original_mix_av;
#[cfg(test)]
mod path_boundary_tests;
mod recovery;
mod semantic_sfx_av;
pub use mlt_mezzanine::{MltPreparedMezzanines, MltVerifiedMezzanine};
pub use mlt_multisegment_av::{MltMultisegmentAvMasterEvidence, MltMultisegmentAvMasterRequest};
pub use mlt_multisegment_sfx::{
    MltMultisegmentOriginalSfxReadiness, MltMultisegmentSfxMasterEvidence,
    MltMultisegmentSfxMasterRequest, MltMultisegmentSfxSourceRequest,
    preflight_multisegment_original_sfx_clock,
};
pub use mlt_timeline::MltVerifiedLosslessTimeline;
pub use original_mix_av::{
    MltMultisegmentOriginalMixMasterEvidence, MltMultisegmentOriginalMixMasterRequest,
    OriginalMixSemanticSfxProof, OriginalMixSfxSourceReceipt, OriginalMixSourceReadiness,
    OriginalMixSourceRequest, PreparedOriginalMixWav, canonical_original_mix_film_options,
    original_mix_sample_clock, prepare_original_mix_av_audio, resolve_canonical_original_mix_plan,
    verify_original_mix_av_source,
};

pub use recovery::{CrossAppRecoveryReport, RecoveryBlenderProof, StageDisposition};
pub use semantic_sfx_av::{
    PreparedSemanticSfxWav, SemanticSfxAvSourceProof, prepare_semantic_sfx_av_audio,
    verify_semantic_sfx_av_source,
};

const CONNECTION_SCHEMA: &str = "motionwright-semwright-connection/1";
const MAX_CONFIG_BYTES: u64 = 16 * 1024;
const MAX_ARGS_BYTES: usize = 220_000;
const MAX_OUTPUT_BYTES: usize = 1_048_576;
const MAX_RUNTIME_VERSION_BYTES: usize = 4_096;
const RUNTIME_PROBE_DEADLINE_SECS: u64 = 5;
const DEFAULT_DEADLINE_SECS: u64 = 45;
const MLT_DEADLINE_SECS: u64 = 330;
const MOTION_RENDER_TIMEOUT_MS: u64 = 300_000;
const MOTION_RENDER_POLL_DEADLINE_SECS: u64 = 330;
const MOTION_RENDER_POLL_INTERVAL_MS: u64 = 1_000;
const MOTION_PLAN_MAX_OPERATIONS: u32 = 4_096;
const MLT_MEZZANINE_MAX_BYTES: u64 = 512 * 1024 * 1024;
const MLT_MASTER_MAX_BYTES: u64 = 1024 * 1024 * 1024;
const BLENDER_GLTF_MAX_BYTES: u64 = 256 * 1024 * 1024;
const MANIM_VIDEO_MAX_BYTES: u64 = 512 * 1024 * 1024;
const MANIM_RENDER_POLL_DEADLINE_SECS: u64 = 330;
const MANIM_RENDER_POLL_INTERVAL_MS: u64 = 1_000;
const MLT_SYNC_MIN_WINDOW_US: u64 = 10_000;
const MLT_SYNC_MAX_WINDOW_US: u64 = 500_000;

const MOTION_CANVAS_COMMANDS: &[&str] = &[
    "driver.motion-canvas.composition.inspect",
    "driver.motion-canvas.composition.plan",
    "driver.motion-canvas.composition.apply",
    "driver.motion-canvas.composition.verify",
    "driver.motion-canvas.render.start",
    "driver.motion-canvas.render.status",
    "driver.motion-canvas.render.cancel",
    "driver.motion-canvas.render.result",
];

const MLT_COMMANDS: &[&str] = &[
    // Application-owned, typed, closed MLT timeline construction. No general
    // operator/shell/graph scripting escapes are enabled.
    "driver.mlt-video.project.create",
    "driver.mlt-video.project.close",
    "driver.mlt-video.sequence.create",
    "driver.mlt-video.sequence.list",
    "driver.mlt-video.sequence.duration",
    "driver.mlt-video.track.create",
    "driver.mlt-video.track.list",
    "driver.mlt-video.asset.import",
    "driver.mlt-video.clip.insert",
    "driver.mlt-video.render.plan",
    "driver.mlt-video.frames.encode",
    "driver.mlt-video.sync.probe",
    "driver.mlt-video.av.mux",
    "driver.mlt-video.render.start",
    "driver.mlt-video.render.status",
    "driver.mlt-video.render.cancel",
    "driver.mlt-video.render.result",
];

const BLENDER_COMMANDS: &[&str] = &[
    "driver.blender.collection.create",
    "driver.blender.object.create",
    "driver.blender.object.transform",
    "driver.blender.collection.link",
    "driver.blender.material.create",
    "driver.blender.material.assign",
    "driver.blender.export.glb",
];

const MANIM_COMMANDS: &[&str] = &[
    "driver.manim-community.render.start",
    "driver.manim-community.render.status",
    "driver.manim-community.render.cancel",
    "driver.manim-community.render.result",
];

const WORKFLOW_READ_COMMANDS: &[&str] = &[
    "workflow.traces.list",
    "workflow.candidates.list",
    "workflow.patterns.list",
    "workflow.suggestions.list",
    "workflow.proposals.list",
    "workflow.promotions.list",
    "workflow.proposal.plan",
];

const WORKFLOW_ACTION_COMMANDS: &[&str] = &[
    "workflow.record.start",
    "workflow.record.stop",
    "workflow.compile",
    "workflow.suggestion.compile",
    "workflow.proposal.accept",
    "workflow.verify",
    "workflow.replay",
    "workflow.promote",
];

const WORKFLOW_MUTATING_COMMANDS: &[&str] = &[
    "workflow.record.start",
    "workflow.record.stop",
    "workflow.compile",
    "workflow.suggestion.compile",
    "workflow.proposal.accept",
    "workflow.verify",
    "workflow.replay",
    "workflow.promote",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProductionConnection {
    pub schema: String,
    pub executable: PathBuf,
    pub executable_sha256: String,
    pub socket: PathBuf,
    pub session_file: PathBuf,
    pub output_root: PathBuf,
    pub resource: String,
}

fn invalid(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::InvalidArgument, message)
}

fn backend(message: impl Into<String>) -> Error {
    Error::new(ErrorCode::BackendFailed, message)
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

/// A Windows junction/reparse directory can redirect an absolute path
/// without being represented as a Rust symlink. Both are forbidden.
fn is_link_or_reparse(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return true;
        }
    }
    false
}

/// macOS exposes trusted legacy /tmp, /var and /etc aliases to root-managed
/// /private locations. Refusing those fixed OS aliases would break otherwise
/// genuine owner temporary files; it must NOT allow user-created links.
#[cfg(target_os = "macos")]
fn trusted_macos_system_alias(path: &Path) -> Option<PathBuf> {
    let expected = match path.to_str()? {
        "/tmp" => Path::new("/private/tmp"),
        "/var" => Path::new("/private/var"),
        "/etc" => Path::new("/private/etc"),
        _ => return None,
    };
    let private_root = fs::symlink_metadata("/private").ok()?;
    if !private_root.is_dir() || is_link_or_reparse(&private_root) {
        return None;
    }
    let canonical = fs::canonicalize(path).ok()?;
    (canonical.as_path() == expected).then_some(canonical)
}

/// Validate every component, not only the leaf: a non-linked executable under
/// a linked parent is otherwise indistinguishable to the final-file check.
/// Keep Unix permission enforcement on the requested leaf as before; system
/// ancestors such as /tmp may legitimately have a sticky writable directory.
fn safe_metadata(path: &Path) -> NativeResult<fs::Metadata> {
    if !path.is_absolute() {
        return Err(invalid("Production connection paths must be absolute"));
    }
    let mut current = PathBuf::new();
    let mut components = path.components().peekable();
    let mut leaf = None;
    while let Some(component) = components.next() {
        match component {
            Component::Prefix(prefix) => {
                current.push(prefix.as_os_str());
                continue;
            }
            Component::RootDir => current.push(component.as_os_str()),
            Component::Normal(name) => current.push(name),
            Component::CurDir | Component::ParentDir => {
                return Err(invalid("Production connection paths must be normalized"));
            }
        }
        let metadata = fs::symlink_metadata(&current).map_err(|_| {
            Error::new(
                ErrorCode::Unavailable,
                "Production connection path is unavailable",
            )
        })?;
        #[cfg(target_os = "macos")]
        let metadata = if metadata.file_type().is_symlink() {
            if let Some(canonical) = trusted_macos_system_alias(&current) {
                current = canonical;
                fs::symlink_metadata(&current).map_err(|_| {
                    Error::new(
                        ErrorCode::Unavailable,
                        "Production system path alias is unavailable",
                    )
                })?
            } else {
                metadata
            }
        } else {
            metadata
        };
        if is_link_or_reparse(&metadata) {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Production connection paths cannot traverse symlinks or reparse points",
            ));
        }
        if components.peek().is_some() && !metadata.is_dir() {
            return Err(invalid("Production connection parent must be a directory"));
        }
        leaf = Some(metadata);
    }
    let metadata = leaf.ok_or_else(|| invalid("Production connection path is invalid"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o022 != 0 {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Production connection paths cannot be group/world writable",
            ));
        }
    }
    Ok(metadata)
}

fn sha256_file(path: &Path) -> NativeResult<String> {
    let mut file = fs::File::open(path)
        .map_err(|_| Error::new(ErrorCode::Unavailable, "Production file is unavailable"))?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .map_err(|_| backend("Production file could not be verified"))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

fn output_artifact_path(root: &Path, relative: &str, max_bytes: u64) -> NativeResult<PathBuf> {
    if relative.is_empty()
        || relative.len() > 4096
        || relative.contains('\0')
        || relative.contains('\\')
    {
        return Err(invalid("Production artifact path is invalid"));
    }
    let relative_path = Path::new(relative);
    if relative_path.is_absolute()
        || relative_path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(invalid(
            "Production artifact path must be a normalized relative path",
        ));
    }
    // The output root may have been replaced since the owner connection was
    // loaded. Reject substituted ancestors before canonicalizing them.
    if !safe_metadata(root)?.is_dir() {
        return Err(invalid("Production output root must be a plain directory"));
    }
    let root = fs::canonicalize(root).map_err(|_| {
        Error::new(
            ErrorCode::Unavailable,
            "Production output root is unavailable",
        )
    })?;
    let mut lexical = root.clone();
    let mut components = relative_path.components().peekable();
    while let Some(component) = components.next() {
        let Component::Normal(component) = component else {
            return Err(invalid(
                "Production artifact path must contain only normal components",
            ));
        };
        lexical.push(component);
        let metadata = fs::symlink_metadata(&lexical).map_err(|_| {
            Error::new(ErrorCode::Unavailable, "Production artifact is unavailable")
        })?;
        if is_link_or_reparse(&metadata) {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Production artifact paths cannot traverse symlinks or reparse points",
            ));
        }
        if components.peek().is_some() && !metadata.is_dir() {
            return Err(invalid(
                "Production artifact path parent must be a directory",
            ));
        }
    }
    let candidate = fs::canonicalize(&lexical)
        .map_err(|_| Error::new(ErrorCode::Unavailable, "Production artifact is unavailable"))?;
    if !candidate.starts_with(&root) {
        return Err(Error::new(
            ErrorCode::PermissionDenied,
            "Production artifact escaped the owner output root",
        ));
    }
    let metadata = fs::metadata(&candidate)
        .map_err(|_| Error::new(ErrorCode::Unavailable, "Production artifact is unavailable"))?;
    if !metadata.is_file() || metadata.len() == 0 || metadata.len() > max_bytes {
        return Err(invalid("Production artifact is not a bounded regular file"));
    }
    Ok(candidate)
}

/// Read immutable production bytes only after a bounded owner-root and SHA-256
/// check. This function is not exposed as an arbitrary-path desktop command:
/// its callers must first obtain a scoped, server-issued media grant.
pub fn read_verified_production_artifact(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    max_bytes: u64,
) -> NativeResult<Vec<u8>> {
    if !is_sha256(expected_sha256) {
        return Err(invalid("Production artifact digest is malformed"));
    }
    let path = output_artifact_path(root, relative, max_bytes)?;
    let bytes = fs::read(path)
        .map_err(|_| Error::new(ErrorCode::Unavailable, "Production artifact cannot be read"))?;
    if bytes.is_empty()
        || bytes.len() as u64 > max_bytes
        || hex::encode(Sha256::digest(&bytes)) != expected_sha256
    {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Production artifact no longer matches its verified digest",
        ));
    }
    Ok(bytes)
}

fn verify_output_artifact(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    max_bytes: u64,
) -> NativeResult<PathBuf> {
    if !is_sha256(expected_sha256) {
        return Err(invalid("Production artifact digest is malformed"));
    }
    let path = output_artifact_path(root, relative, max_bytes)?;
    if sha256_file(&path)? != expected_sha256 {
        return Err(Error::new(
            ErrorCode::StaleReference,
            "Production artifact digest changed before dispatch",
        ));
    }
    Ok(path)
}

/// Resolve only an exact digest-verified media artifact within the canonical
/// owner output root. Intended for tightly scoped desktop delivery of an
/// existing native master, never as a generic WebView filesystem command.
pub fn verified_native_media_path(
    root: &Path,
    relative: &str,
    expected_sha256: &str,
    max_bytes: u64,
) -> NativeResult<PathBuf> {
    verify_output_artifact(root, relative, expected_sha256, max_bytes)
}

impl ProductionConnection {
    pub fn load(path: impl AsRef<Path>) -> NativeResult<Self> {
        let path = path.as_ref();
        let metadata = safe_metadata(path)?;
        if !metadata.is_file() || metadata.len() > MAX_CONFIG_BYTES {
            return Err(invalid(
                "Production connection file is invalid or too large",
            ));
        }
        let bytes = fs::read(path).map_err(|_| {
            Error::new(
                ErrorCode::Unavailable,
                "Production connection file is unavailable",
            )
        })?;
        let connection: Self = serde_json::from_slice(&bytes)
            .map_err(|_| invalid("Production connection file is malformed"))?;
        connection.validate()?;
        Ok(connection)
    }

    pub fn validate(&self) -> NativeResult<()> {
        if self.schema != CONNECTION_SCHEMA {
            return Err(invalid(
                "Unsupported Motionwright production connection schema",
            ));
        }
        if !is_sha256(&self.executable_sha256)
            || self.resource.is_empty()
            || self.resource.len() > 512
            || self.resource.chars().any(char::is_control)
        {
            return Err(invalid("Production connection identity is invalid"));
        }

        let executable = safe_metadata(&self.executable)?;
        if !executable.is_file() {
            return Err(invalid("Semwright executable must be a regular file"));
        }
        let session = safe_metadata(&self.session_file)?;
        if !session.is_file() || session.len() > MAX_CONFIG_BYTES {
            return Err(invalid("Semwright session file is invalid"));
        }
        let output = safe_metadata(&self.output_root)?;
        if !output.is_dir() {
            return Err(invalid("Production output root must be a directory"));
        }
        let _socket = safe_metadata(&self.socket)?;

        if sha256_file(&self.executable)? != self.executable_sha256 {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Semwright executable digest changed",
            ));
        }
        Ok(())
    }

    pub fn identity(&self) -> NativeResult<String> {
        let bytes = serde_json::to_vec(self)
            .map_err(|_| invalid("Production connection could not be encoded"))?;
        Ok(hex::encode(Sha256::digest(bytes)))
    }
}

#[derive(Debug, Clone)]
struct ExpectedAuthority {
    provider: &'static str,
    source: SourceKind,
    provider_generation_required: bool,
}

fn expected_authority(command: &str) -> Option<ExpectedAuthority> {
    if MOTION_CANVAS_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:motion-canvas",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if MLT_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:mlt-video",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if BLENDER_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:blender",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if MANIM_COMMANDS.contains(&command) {
        Some(ExpectedAuthority {
            provider: "driver:manim-community",
            source: SourceKind::Driver,
            provider_generation_required: true,
        })
    } else if WORKFLOW_READ_COMMANDS.contains(&command)
        || WORKFLOW_ACTION_COMMANDS.contains(&command)
    {
        Some(ExpectedAuthority {
            provider: "semwright-core",
            source: SourceKind::Builtin,
            provider_generation_required: false,
        })
    } else {
        None
    }
}

fn request_digest(command: &str, args: &Value) -> NativeResult<String> {
    let bytes = serde_json::to_vec(&(command, args))
        .map_err(|_| invalid("Production request could not be encoded"))?;
    Ok(hex::encode(Sha256::digest(bytes)))
}

async fn read_bounded<R>(reader: R) -> std::io::Result<Vec<u8>>
where
    R: tokio::io::AsyncRead + Unpin,
{
    let mut bytes = Vec::new();
    let mut limited = reader.take((MAX_OUTPUT_BYTES + 1) as u64);
    limited.read_to_end(&mut bytes).await?;
    Ok(bytes)
}

fn parse_semwright_cli_version(stdout: &[u8]) -> NativeResult<String> {
    if stdout.len() > MAX_RUNTIME_VERSION_BYTES {
        return Err(backend(
            "Semwright CLI version response exceeded the transport budget",
        ));
    }
    let output = std::str::from_utf8(stdout)
        .map_err(|_| backend("Semwright CLI version response is not UTF-8"))?
        .trim();
    let version = output
        .strip_prefix("semwright ")
        .filter(|value| !value.is_empty() && value.len() <= 64)
        .ok_or_else(|| backend("Semwright CLI returned an invalid version response"))?;
    if !version
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
    {
        return Err(backend("Semwright CLI returned an invalid version token"));
    }
    Ok(version.to_owned())
}

#[derive(Debug, Clone)]
pub struct BrokerResult {
    pub value: Value,
    pub request_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemwrightRuntimeProbe {
    pub connection_identity: String,
    pub executable_sha256: String,
    pub observed_version: String,
    pub expected_version: String,
    pub version_compatible: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionCanvasRenderEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub frame_rate: Rate,
    pub segments: Vec<MotionCanvasSegmentEvidence>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MotionCanvasSegmentEvidence {
    pub segment_id: String,
    pub scene_ids: Vec<Uuid>,
    pub frame_count: u64,
    pub plan_ref: String,
    pub fingerprint: String,
    pub job_ref: String,
    pub artifact: Value,
    pub verification: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltAudioArtifact {
    pub relative_path: String,
    pub sha256: String,
    pub sample_rate: u32,
    pub channels: u16,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AvSyncCue {
    pub id: String,
    pub expected_us: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AvSyncSpec {
    pub window_us: u64,
    pub full_scan: bool,
    pub cues: Vec<AvSyncCue>,
}

#[derive(Debug, Clone, Copy)]
pub struct MltAvMasterRequest<'a> {
    pub request_id: &'a str,
    pub deliverable_id: Uuid,
    pub motion: &'a MotionCanvasRenderEvidence,
    pub audio: &'a MltAudioArtifact,
    pub sync: Option<&'a AvSyncSpec>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MltAvMasterEvidence {
    pub project_resource: String,
    pub generation: Uuid,
    pub revision: u64,
    pub deliverable_id: Uuid,
    pub motion_segment_id: String,
    pub frame_rate: Rate,
    pub frame_count: u64,
    pub mezzanine: Value,
    pub source_audio: MltAudioArtifact,
    pub master: Value,
    pub decoded_audio: Value,
    pub sync: Option<Value>,
}

#[derive(Clone)]
pub struct ProductionClient {
    connection: ProductionConnection,
}

impl ProductionClient {
    pub fn new(connection: ProductionConnection) -> NativeResult<Self> {
        connection.validate()?;
        Ok(Self { connection })
    }

    pub fn connection(&self) -> &ProductionConnection {
        &self.connection
    }

    pub async fn probe_runtime(
        &self,
        expected_version: &str,
    ) -> NativeResult<SemwrightRuntimeProbe> {
        if expected_version.is_empty()
            || expected_version.len() > 64
            || !expected_version
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+'))
        {
            return Err(invalid("Expected Semwright CLI version is invalid"));
        }
        self.connection.validate()?;
        let connection_identity = self.connection.identity()?;

        let mut child = Command::new(&self.connection.executable);
        child
            .arg("--version")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        let mut child = child.spawn().map_err(|_| {
            Error::new(ErrorCode::Unavailable, "Semwright CLI could not be started")
        })?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| backend("Semwright CLI stdout unavailable"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| backend("Semwright CLI stderr unavailable"))?;
        let stdout_task = tokio::spawn(read_bounded(stdout));
        let stderr_task = tokio::spawn(read_bounded(stderr));

        let status = match timeout(
            Duration::from_secs(RUNTIME_PROBE_DEADLINE_SECS),
            child.wait(),
        )
        .await
        {
            Ok(result) => result.map_err(|_| backend("Semwright CLI version probe failed"))?,
            Err(_) => {
                let _ = child.kill().await;
                return Err(Error::new(
                    ErrorCode::Timeout,
                    "Semwright CLI version probe timed out",
                ));
            }
        };
        let stdout = stdout_task
            .await
            .map_err(|_| backend("Semwright CLI version output task failed"))?
            .map_err(|_| backend("Semwright CLI version output could not be read"))?;
        let stderr = stderr_task
            .await
            .map_err(|_| backend("Semwright CLI version diagnostic task failed"))?
            .map_err(|_| backend("Semwright CLI version diagnostics could not be read"))?;
        if stdout.len() > MAX_RUNTIME_VERSION_BYTES || stderr.len() > MAX_RUNTIME_VERSION_BYTES {
            return Err(backend(
                "Semwright CLI version response exceeded the transport budget",
            ));
        }
        if !status.success() {
            return Err(backend("Semwright CLI version probe failed"));
        }

        let observed_version = parse_semwright_cli_version(&stdout)?;
        Ok(SemwrightRuntimeProbe {
            connection_identity,
            executable_sha256: self.connection.executable_sha256.clone(),
            version_compatible: observed_version == expected_version,
            observed_version,
            expected_version: expected_version.to_owned(),
        })
    }

    pub async fn execute(
        &self,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<BrokerResult> {
        let expected = expected_authority(command).ok_or_else(|| {
            Error::new(ErrorCode::Unsupported, "Canonical command is not enabled")
        })?;
        let mutation = mutation || WORKFLOW_MUTATING_COMMANDS.contains(&command);
        self.connection.validate()?;
        let encoded = serde_json::to_vec(&args)
            .map_err(|_| invalid("Production command arguments are malformed"))?;
        if encoded.len() > MAX_ARGS_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Production command arguments exceed the transport budget",
            ));
        }

        let mut child = Command::new(&self.connection.executable);
        child
            .arg("--socket")
            .arg(&self.connection.socket)
            .arg("--session-file")
            .arg(&self.connection.session_file)
            .arg("--json")
            .arg("execute")
            .arg(command)
            .arg("--args-json")
            .arg(
                std::str::from_utf8(&encoded)
                    .map_err(|_| invalid("Production arguments are not UTF-8 JSON"))?,
            )
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        let mut child = child.spawn().map_err(|_| {
            Error::new(ErrorCode::Unavailable, "Semwright CLI could not be started")
        })?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| backend("Semwright CLI stdout unavailable"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| backend("Semwright CLI stderr unavailable"))?;
        let stdout_task = tokio::spawn(read_bounded(stdout));
        let stderr_task = tokio::spawn(read_bounded(stderr));
        let deadline = if command.starts_with("driver.mlt-video.")
            || command.starts_with("driver.blender.")
            || command.starts_with("driver.manim-community.")
        {
            MLT_DEADLINE_SECS
        } else {
            DEFAULT_DEADLINE_SECS
        };

        let status = match timeout(Duration::from_secs(deadline), child.wait()).await {
            Ok(result) => result.map_err(|_| backend("Semwright CLI process failed"))?,
            Err(_) => {
                let _ = child.kill().await;
                let error =
                    Error::new(ErrorCode::Timeout, "Canonical production command timed out");
                return Err(if mutation { error.uncertain() } else { error });
            }
        };
        let stdout = stdout_task
            .await
            .map_err(|_| backend("Semwright CLI output task failed"))?
            .map_err(|_| backend("Semwright CLI output could not be read"))?;
        let stderr = stderr_task
            .await
            .map_err(|_| backend("Semwright CLI diagnostic task failed"))?
            .map_err(|_| backend("Semwright CLI diagnostics could not be read"))?;
        if stdout.len() > MAX_OUTPUT_BYTES || stderr.len() > MAX_OUTPUT_BYTES {
            return Err(Error::new(
                ErrorCode::ResourceExhausted,
                "Semwright CLI response exceeded the transport budget",
            ));
        }

        let envelope: Envelope = serde_json::from_slice(&stdout)
            .map_err(|_| backend("Semwright CLI returned an invalid response envelope"))?;
        if envelope.command != command {
            return Err(backend(
                "Semwright CLI response command does not match the request",
            ));
        }
        if !status.success() || !envelope.ok {
            let error = envelope.error.unwrap_or_else(|| {
                backend("Canonical production command failed without a typed error")
            });
            return Err(error);
        }
        let provenance = envelope
            .execution
            .provenance
            .as_ref()
            .ok_or_else(|| backend("Canonical production response has no provenance"))?;
        if provenance.provider != expected.provider
            || provenance.source != expected.source
            || (expected.provider_generation_required && provenance.provider_generation.is_none())
            || !is_sha256(&provenance.descriptor_sha256)
        {
            return Err(backend(
                "Canonical response provenance does not match the expected authority",
            ));
        }
        let value = envelope
            .data
            .ok_or_else(|| backend("Canonical production response has no data"))?;
        Ok(BrokerResult {
            value: json!({
                "data": value,
                "execution": envelope.execution,
                "warnings": envelope.warnings,
                "provenance_checked": true
            }),
            request_id: envelope.request_id,
        })
    }
}

fn response_data(value: &Value) -> NativeResult<&Value> {
    value
        .pointer("/result/data")
        .ok_or_else(|| backend("Canonical production response is missing provider data"))
}

fn retryable_motion_status_error(error: &Error) -> bool {
    // Only an ordinary read-only Timeout gets another fresh status request.
    // Lost provider generations must NOT restart a mutating render job.
    error.code == ErrorCode::Timeout
}

/// The pinned Semwright Motion Canvas driver exposes a *closed* typed
/// RenderFailureClass in render.status. The adjacent error text is deliberately
/// not propagated: renderer diagnostics may contain private local paths,
/// stdout, user media names or other untrusted information.
/// Only exact allowlisted static labels are safe to retain for CI and operators.
fn motion_failure_class(data: &Value) -> &'static str {
    const KNOWN: &[&str] = &[
        "arguments",
        "font_evidence",
        "project_stage",
        "vite_build",
        "frame_export",
        "browser_launch",
        "page_load",
        "render_wait",
        "render_wait_timeout",
        "renderer_state_frame_clock",
        "renderer_state_authoring_protocol",
        "renderer_log_authoring_protocol",
        "renderer_state_playback_protocol",
        "renderer_log_playback_protocol",
        "renderer_log_exporter_missing",
        "renderer_log_async_property",
        "renderer_state_webgl_unavailable",
        "renderer_log_webgl_unavailable",
        "renderer_state_model_invariant",
        "renderer_log_model_invariant",
        "renderer_state_authoring_model",
        "renderer_log_authoring_model",
        "renderer_state_invalid_scene",
        "renderer_log_invalid_scene",
        "renderer_state_range_error",
        "renderer_log_range_error",
        "renderer_state_type_error",
        "renderer_log_type_error",
        "renderer_state_semwright_native",
        "renderer_state_semwright_exporter",
        "renderer_state_motion_core",
        "renderer_state_motion_2d",
        "renderer_state_before_first_frame",
        "renderer_state_after_first_frame",
        "renderer_state_error",
        "renderer_log_error",
        "render_result_aborted",
        "render_result_error",
        "render_result_unknown",
        "render_nonzero",
        "runtime_module_load",
        "runtime_syntax",
        "runtime_permission",
        "runtime_oom",
        "runtime_killed",
        "runtime_cpu_limit",
        "runtime_file_size_limit",
        "runtime_signal",
        "observation",
        "finalize",
        "startup",
    ];
    let reported = data.get("failure_class").and_then(Value::as_str);
    KNOWN
        .iter()
        .copied()
        .find(|name| Some(*name) == reported)
        .unwrap_or("unclassified")
}

/// A render-start was already acknowledged. A failed status observation
/// cannot establish whether that independently running job has completed,
/// crashed, or is still rendering. Preserve the original typed error code
/// and native diagnostic, but keep the *render operation* outcome UNKNOWN.
/// Never interpret an Unavailable provider generation as a known render fail
/// or generate a replacement render.start with a fresh mutation identity.
fn unconfirmed_started_motion_render(mut error: Error) -> Error {
    error.message = format!(
        "Motion Canvas render started but its terminal result is unconfirmed ({:?}). \
         Reconcile the original job/request receipt with canonical Broker/Driver Host evidence; \
         do not redispatch a render automatically.",
        error.code
    );
    error
        .uncertain()
        .recipe_progress(1, "driver.motion-canvas.render.start")
}

/// A provider status/result is evidence only for the *same* render job
/// previously acknowledged by the Broker. The pinned Semwright Motion Canvas
/// JobView always carries job_ref; missing/foreign references are not allowed
/// to turn a status read into a verified native media result.
///
/// Both inputs are app-owned; never embed the opaque provider reference in
/// diagnostic/error text (which could flow into CI or UI).
fn require_acknowledged_motion_job(
    view: &Value,
    acknowledged_job_ref: &str,
    observation: &'static str,
) -> NativeResult<()> {
    if view.get("job_ref").and_then(Value::as_str) != Some(acknowledged_job_ref) {
        return Err(unconfirmed_started_motion_render(backend(format!(
            "Motion Canvas {observation} does not identify the acknowledged native render job"
        ))));
    }
    Ok(())
}

fn required_string(value: &Value, pointer: &str, context: &str) -> NativeResult<String> {
    value
        .pointer(pointer)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| backend(context))
}

/// Source-independent, closed check classification for a failed *caption*
/// verification. Only these static labels may reach a CLI/CI summary; actual
/// findings, provider strings, job references and frame data remain private.
fn native_caption_verification_gate(value: &Value) -> &'static str {
    if value
        .pointer("/report/execution_status")
        .and_then(Value::as_str)
        != Some("completed")
    {
        return "incomplete";
    }
    if value
        .pointer("/report/support_level")
        .and_then(Value::as_str)
        != Some("native")
    {
        return "not_native";
    }
    let Some(checks) = value
        .pointer("/measurement/validation/checks")
        .and_then(Value::as_array)
    else {
        return "checks_missing";
    };
    if checks.is_empty() {
        return "checks_missing";
    }
    for (rule, label) in [
        ("caption-timing", "caption_timing"),
        ("resolved-cues", "resolved_cues"),
        ("native-frame-coverage", "frame_coverage"),
    ] {
        if checks.iter().any(|check| {
            check.get("rule").and_then(Value::as_str) == Some(rule)
                && check.get("verdict").and_then(Value::as_str) != Some("PASS")
        }) {
            return label;
        }
    }
    if checks
        .iter()
        .any(|check| check.get("verdict").and_then(Value::as_str) != Some("PASS"))
    {
        return "other_rule";
    }
    match value
        .pointer("/measurement/findings")
        .and_then(Value::as_array)
    {
        None => "findings_missing",
        Some(findings) if !findings.is_empty() => "unresolved_findings",
        _ => "unclassified",
    }
}

/// Strict existing validator remains authoritative; categorization above is
/// read-only and can never convert a failed check into an accepted render.
pub(crate) fn ensure_native_motion_verification(value: &Value) -> NativeResult<()> {
    if value
        .pointer("/report/execution_status")
        .and_then(Value::as_str)
        != Some("completed")
        || value
            .pointer("/report/support_level")
            .and_then(Value::as_str)
            != Some("native")
    {
        return Err(backend(
            "Motion Canvas composition verification did not complete with native support",
        ));
    }
    let findings = value
        .pointer("/measurement/findings")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Motion Canvas verification findings are unavailable"))?;
    if !findings.is_empty() {
        return Err(backend(
            "Motion Canvas native verification returned unresolved findings",
        ));
    }
    let checks = value
        .pointer("/measurement/validation/checks")
        .and_then(Value::as_array)
        .ok_or_else(|| backend("Motion Canvas verification checks are unavailable"))?;
    if checks.is_empty()
        || checks
            .iter()
            .any(|check| check.get("verdict").and_then(Value::as_str) != Some("PASS"))
    {
        return Err(backend(
            "Motion Canvas native verification did not produce an all-PASS check set",
        ));
    }
    Ok(())
}

#[derive(Clone)]
pub struct ProductionCoordinator {
    service: StudioService,
    client: ProductionClient,
}

impl ProductionCoordinator {
    pub fn new(service: StudioService, connection: ProductionConnection) -> NativeResult<Self> {
        Ok(Self {
            service,
            client: ProductionClient::new(connection)?,
        })
    }

    pub fn receipts(&self, project_id: Uuid, limit: usize) -> NativeResult<Vec<ProductionReceipt>> {
        self.service
            .production_receipts(project_id, limit)
            .map_err(storage_error)
    }

    pub async fn render_motion_canvas_segments(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
    ) -> NativeResult<MotionCanvasRenderEvidence> {
        if request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("Motion Canvas production request id is invalid"));
        }

        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since Motion Canvas production was prepared",
            ));
        }

        let captions_requested = project
            .deliverables
            .iter()
            .any(|profile| profile.id == deliverable_id && profile.burn_in_captions);
        let segments = build_motion_canvas_segments(&project, deliverable_id, options)?;
        if segments.is_empty() {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "The selected revision has no Motion Canvas segments to render",
            ));
        }

        let mut evidence = Vec::with_capacity(segments.len());
        for (index, segment) in segments.into_iter().enumerate() {
            let prefix = format!("{request_id}:motion:{index}");
            let film = serde_json::to_value(&segment.film)
                .map_err(|_| invalid("Canonical Film could not be encoded"))?;

            let planned = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:plan"),
                    "driver.motion-canvas.composition.plan",
                    json!({
                        "film": film,
                        "budget": {
                            "max_iterations": 8,
                            "max_operations": MOTION_PLAN_MAX_OPERATIONS,
                            "max_findings": 1024,
                            "max_observations": 128,
                            "max_elapsed_ms": 120000
                        }
                    }),
                    false,
                )
                .await?;
            let plan_ref = required_string(
                response_data(&planned)?,
                "/plan_ref",
                "Motion Canvas composition plan returned no plan reference",
            )?;

            let applied = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:apply"),
                    "driver.motion-canvas.composition.apply",
                    json!({"plan_ref": plan_ref, "dry_run": false}),
                    true,
                )
                .await?;
            let applied_data = response_data(&applied)?;
            if applied_data.get("applied").and_then(Value::as_bool) != Some(true)
                || applied_data.get("execution_status").and_then(Value::as_str) != Some("completed")
            {
                return Err(backend(
                    "Motion Canvas composition apply did not complete deterministically",
                ));
            }
            let fingerprint = required_string(
                applied_data,
                "/fingerprint",
                "Motion Canvas composition apply returned no source fingerprint",
            )?;

            let started = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:render-start"),
                    "driver.motion-canvas.render.start",
                    json!({
                        "expected_fingerprint": fingerprint,
                        "profile": {
                            "first_frame": 0,
                            "end_frame_exclusive": segment.frame_count,
                            "scale": "full",
                            "transparent": false,
                            "timeout_ms": MOTION_RENDER_TIMEOUT_MS
                        }
                    }),
                    true,
                )
                .await?;
            let job_ref = required_string(
                response_data(&started)?,
                "/job_ref",
                "Motion Canvas render did not return a job reference",
            )?;

            let deadline = Instant::now() + Duration::from_secs(MOTION_RENDER_POLL_DEADLINE_SECS);
            let mut poll = 0_u32;
            let terminal = loop {
                if Instant::now() >= deadline {
                    return Err(unconfirmed_started_motion_render(Error::new(
                        ErrorCode::Timeout,
                        "Motion Canvas render did not reach a terminal state before the bounded deadline",
                    )));
                }
                let status = match self
                    .execute(
                        project_id,
                        expected,
                        &format!("{prefix}:render-status:{poll}"),
                        "driver.motion-canvas.render.status",
                        json!({"job_ref": job_ref}),
                        false,
                    )
                    .await
                {
                    Ok(status) => status,
                    Err(error) if retryable_motion_status_error(&error) => {
                        poll = poll.checked_add(1).ok_or_else(|| {
                            backend("Motion Canvas render poll counter overflowed")
                        })?;
                        sleep(Duration::from_millis(MOTION_RENDER_POLL_INTERVAL_MS)).await;
                        continue;
                    }
                    Err(error) => return Err(unconfirmed_started_motion_render(error)),
                };
                let data = response_data(&status)
                    .map_err(unconfirmed_started_motion_render)?
                    .clone();
                require_acknowledged_motion_job(&data, &job_ref, "status")?;
                match data.get("state").and_then(Value::as_str) {
                    Some("succeeded") => break data,
                    Some("failed") => {
                        // A bounded category and the segment number identify
                        // the failed native lane without leaking provider error
                        // messages or raw receipts to UI/CI.
                        return Err(backend(format!(
                            "Motion Canvas segment {} render failed (category: {})",
                            index + 1,
                            motion_failure_class(&data),
                        )));
                    }
                    Some("cancelled") => {
                        return Err(Error::new(
                            ErrorCode::Cancelled,
                            "Motion Canvas render was cancelled",
                        ));
                    }
                    Some("queued") | Some("starting") | Some("rendering") => {}
                    Some(_) => {
                        return Err(unconfirmed_started_motion_render(backend(
                            "Motion Canvas render returned an unsupported job state",
                        )));
                    }
                    None => {
                        return Err(unconfirmed_started_motion_render(backend(
                            "Motion Canvas render status returned no canonical state",
                        )));
                    }
                }
                poll = poll
                    .checked_add(1)
                    .ok_or_else(|| backend("Motion Canvas render poll counter overflowed"))?;
                sleep(Duration::from_millis(MOTION_RENDER_POLL_INTERVAL_MS)).await;
            };
            if terminal
                .pointer("/artifact/frame_count")
                .and_then(Value::as_u64)
                != Some(segment.frame_count)
            {
                return Err(unconfirmed_started_motion_render(backend(
                    "Motion Canvas terminal artifact frame count does not match the Film realization",
                )));
            }

            let rendered = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:render-result"),
                    "driver.motion-canvas.render.result",
                    json!({"job_ref": job_ref}),
                    false,
                )
                .await
                .map_err(unconfirmed_started_motion_render)?;
            let rendered_data =
                response_data(&rendered).map_err(unconfirmed_started_motion_render)?;
            require_acknowledged_motion_job(rendered_data, &job_ref, "result")?;
            if rendered_data.get("state").and_then(Value::as_str) != Some("succeeded") {
                return Err(unconfirmed_started_motion_render(backend(
                    "Motion Canvas render result is not a successful terminal artifact",
                )));
            }
            let artifact = rendered_data.get("artifact").cloned().ok_or_else(|| {
                unconfirmed_started_motion_render(backend(
                    "Motion Canvas render result has no artifact",
                ))
            })?;
            if artifact.get("frame_count").and_then(Value::as_u64) != Some(segment.frame_count) {
                return Err(unconfirmed_started_motion_render(backend(
                    "Motion Canvas result artifact frame count does not match the Film realization",
                )));
            }

            let verified = self
                .execute(
                    project_id,
                    expected,
                    &format!("{prefix}:verify"),
                    "driver.motion-canvas.composition.verify",
                    json!({"plan_ref": plan_ref, "job_ref": job_ref}),
                    false,
                )
                .await?;
            let verification = response_data(&verified)?.clone();
            if let Err(error) = ensure_native_motion_verification(&verification) {
                if captions_requested {
                    // A failed native measurement must never be suppressed or
                    // treated as pixel evidence. Emit only an exact static
                    // category from the pinned Semwright check vocabulary.
                    return Err(backend(format!(
                        "Motion Canvas caption native verification rejected (gate: {})",
                        native_caption_verification_gate(&verification),
                    )));
                }
                return Err(error);
            }

            evidence.push(MotionCanvasSegmentEvidence {
                segment_id: segment.id,
                scene_ids: segment.scene_ids,
                frame_count: segment.frame_count,
                plan_ref,
                fingerprint,
                job_ref,
                artifact,
                verification,
            });
        }

        Ok(MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_rate: options.frame_rate,
            segments: evidence,
        })
    }

    pub async fn assemble_mlt_av_master(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request: MltAvMasterRequest<'_>,
    ) -> NativeResult<MltAvMasterEvidence> {
        let MltAvMasterRequest {
            request_id,
            deliverable_id,
            motion,
            audio,
            sync,
        } = request;
        if request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("MLT audiovisual production request id is invalid"));
        }

        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since MLT audiovisual production was prepared",
            ));
        }
        if motion.project_resource != project.resource_key()
            || motion.generation != project.generation
            || motion.revision != project.revision
            || motion.deliverable_id != deliverable_id
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motion Canvas evidence does not belong to the selected Motionwright revision",
            ));
        }

        let deliverable = project
            .deliverables
            .iter()
            .find(|profile| profile.id == deliverable_id)
            .ok_or_else(|| invalid("Deliverable profile not found"))?;
        if deliverable.video_codec != VideoCodec::H264
            || deliverable.audio_codec != AudioCodec::Aac
            || deliverable.audio_sample_rate_hz != 48_000
            || deliverable.container != OutputContainer::Mp4
            || deliverable.color_space != OutputColorSpace::Rec709
        {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Pinned MLT final-master path requires an H.264/AAC 48 kHz Rec.709 MP4 deliverable",
            ));
        }
        motion
            .frame_rate
            .validate()
            .map_err(|error| invalid(format!("MLT frame rate is invalid: {error}")))?;
        if i64::from(motion.frame_rate.num) != deliverable.frame_rate.num
            || i64::from(motion.frame_rate.den) != deliverable.frame_rate.den
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motion Canvas evidence frame rate does not match the selected deliverable profile",
            ));
        }
        if motion.segments.len() != 1 {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Direct MLT audiovisual master currently requires one contiguous Motion Canvas segment; multi-segment timeline assembly must use the semantic MLT edit path",
            ));
        }
        let segment = &motion.segments[0];
        if segment.frame_count == 0 || segment.frame_count > 36_000 {
            return Err(invalid(
                "MLT audiovisual frame count is outside certified bounds",
            ));
        }

        let manifest_path = required_string(
            &segment.artifact,
            "/manifest",
            "Motion Canvas artifact has no manifest path",
        )?;
        let manifest_sha256 = required_string(
            &segment.artifact,
            "/manifest_sha256",
            "Motion Canvas artifact has no manifest digest",
        )?;
        verify_output_artifact(
            &self.client.connection().output_root,
            &manifest_path,
            &manifest_sha256,
            8 * 1024 * 1024,
        )?;

        if audio.sample_rate != 48_000 || audio.channels != 2 {
            return Err(Error::new(
                ErrorCode::Unsupported,
                "Pinned MLT final-master path requires a 48 kHz stereo audio master",
            ));
        }
        verify_output_artifact(
            &self.client.connection().output_root,
            &audio.relative_path,
            &audio.sha256,
            MLT_MASTER_MAX_BYTES,
        )?;

        let token = hex::encode(Sha256::digest(request_id.as_bytes()));
        let token = &token[..16];
        let stem = format!("mw-{}-r{}-{token}", project.id.simple(), project.revision);
        let mezzanine_path = format!("{stem}-motion.mkv");
        let master_path = format!("{stem}-master.mp4");

        let encoded = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:frames-encode"),
                "driver.mlt-video.frames.encode",
                json!({
                    "root": "output",
                    "manifest_path": manifest_path,
                    "expected_manifest_sha256": manifest_sha256,
                    "output_path": mezzanine_path,
                    "max_bytes": MLT_MEZZANINE_MAX_BYTES
                }),
                true,
            )
            .await?;
        let mezzanine = response_data(&encoded)?.clone();
        if mezzanine.get("codec").and_then(Value::as_str) != Some("ffv1")
            || mezzanine.get("container").and_then(Value::as_str) != Some("matroska")
            || mezzanine.get("frame_count").and_then(Value::as_u64) != Some(segment.frame_count)
            || mezzanine.get("width").and_then(Value::as_u64) != Some(u64::from(deliverable.width))
            || mezzanine.get("height").and_then(Value::as_u64)
                != Some(u64::from(deliverable.height))
            || mezzanine.get("fps_num").and_then(Value::as_u64)
                != Some(u64::from(motion.frame_rate.num))
            || mezzanine.get("fps_den").and_then(Value::as_u64)
                != Some(u64::from(motion.frame_rate.den))
            || mezzanine.pointer("/media/video").and_then(Value::as_bool) != Some(true)
            || mezzanine.pointer("/media/audio").and_then(Value::as_bool) != Some(false)
        {
            return Err(backend(
                "MLT frame encoding did not produce the expected verified FFV1 mezzanine",
            ));
        }
        let mezzanine_artifact_path = required_string(
            &mezzanine,
            "/artifact/path",
            "MLT frame encoding returned no artifact path",
        )?;
        let mezzanine_sha256 = required_string(
            &mezzanine,
            "/artifact/sha256",
            "MLT frame encoding returned no artifact digest",
        )?;
        verify_output_artifact(
            &self.client.connection().output_root,
            &mezzanine_artifact_path,
            &mezzanine_sha256,
            MLT_MEZZANINE_MAX_BYTES,
        )?;

        let muxed = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:mlt:av-mux"),
                "driver.mlt-video.av.mux",
                json!({
                    "video_root": "output",
                    "video_path": mezzanine_artifact_path,
                    "video_sha256": mezzanine_sha256,
                    "audio_root": "output",
                    "audio_path": audio.relative_path,
                    "audio_sha256": audio.sha256,
                    "width": deliverable.width,
                    "height": deliverable.height,
                    "fps_num": motion.frame_rate.num,
                    "fps_den": motion.frame_rate.den,
                    "frame_count": segment.frame_count,
                    "sample_rate": audio.sample_rate,
                    "channels": audio.channels,
                    "profile": "h264-aac-mp4",
                    "output_path": master_path,
                    "max_bytes": MLT_MASTER_MAX_BYTES
                }),
                true,
            )
            .await?;
        let master = response_data(&muxed)?.clone();
        if master.get("profile").and_then(Value::as_str) != Some("h264-aac-mp4")
            || master.get("frame_count").and_then(Value::as_u64) != Some(segment.frame_count)
            || master.get("sample_rate").and_then(Value::as_u64) != Some(48_000)
            || master.get("channels").and_then(Value::as_u64) != Some(2)
            || master.pointer("/media/video").and_then(Value::as_bool) != Some(true)
            || master.pointer("/media/audio").and_then(Value::as_bool) != Some(true)
        {
            return Err(backend(
                "MLT audiovisual mux did not return the certified H.264/AAC master profile",
            ));
        }
        let master_artifact_path = required_string(
            &master,
            "/artifact/path",
            "MLT audiovisual mux returned no master path",
        )?;
        let master_sha256 = required_string(
            &master,
            "/artifact/sha256",
            "MLT audiovisual mux returned no master digest",
        )?;
        verify_output_artifact(
            &self.client.connection().output_root,
            &master_artifact_path,
            &master_sha256,
            MLT_MASTER_MAX_BYTES,
        )?;
        let decoded_audio_path = required_string(
            &master,
            "/decoded_audio/path",
            "MLT audiovisual mux returned no decoded-audio artifact",
        )?;
        let decoded_audio_sha256 = required_string(
            &master,
            "/decoded_audio/sha256",
            "MLT audiovisual mux returned no decoded-audio digest",
        )?;
        verify_output_artifact(
            &self.client.connection().output_root,
            &decoded_audio_path,
            &decoded_audio_sha256,
            MLT_MASTER_MAX_BYTES,
        )?;

        let sync_evidence = if let Some(spec) = sync {
            if !(MLT_SYNC_MIN_WINDOW_US..=MLT_SYNC_MAX_WINDOW_US).contains(&spec.window_us)
                || spec.cues.is_empty()
                || spec.cues.len() > 16
            {
                return Err(invalid(
                    "MLT sync specification is outside certified bounds",
                ));
            }
            let mut seen = std::collections::BTreeSet::new();
            for cue in &spec.cues {
                if cue.id.is_empty()
                    || cue.id.len() > 96
                    || !cue
                        .id
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
                    || cue.expected_us > 600_000_000
                    || !seen.insert(cue.id.clone())
                {
                    return Err(invalid("MLT sync cue is invalid or duplicated"));
                }
            }
            let probed = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:mlt:sync-probe"),
                    "driver.mlt-video.sync.probe",
                    json!({
                        "root": "output",
                        "path": master_artifact_path,
                        "expected_sha256": master_sha256,
                        "window_us": spec.window_us,
                        "full_scan": spec.full_scan,
                        "cues": spec.cues
                    }),
                    false,
                )
                .await?;
            let probed = response_data(&probed)?.clone();
            if probed.get("artifact_sha256").and_then(Value::as_str) != Some(master_sha256.as_str())
                || (spec.full_scan
                    && probed.get("coverage").and_then(Value::as_str) != Some("full_scan"))
                || probed
                    .get("missing_video")
                    .and_then(Value::as_array)
                    .is_none_or(|missing| !missing.is_empty())
                || probed
                    .get("missing_audio")
                    .and_then(Value::as_array)
                    .is_none_or(|missing| !missing.is_empty())
            {
                return Err(backend(
                    "MLT decoded sync verification did not satisfy the requested cue coverage",
                ));
            }
            Some(probed)
        } else {
            None
        };

        Ok(MltAvMasterEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            motion_segment_id: segment.segment_id.clone(),
            frame_rate: motion.frame_rate,
            frame_count: segment.frame_count,
            mezzanine,
            source_audio: audio.clone(),
            master: master.clone(),
            decoded_audio: master
                .get("decoded_audio")
                .cloned()
                .ok_or_else(|| backend("MLT master lost decoded-audio evidence"))?,
            sync: sync_evidence,
        })
    }

    pub async fn realize_blender_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
    ) -> NativeResult<Value> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since the Blender contribution was prepared",
            ));
        }
        let plan = build_blender_contribution(&project, scene_id)?;
        let collection = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:collection"),
                "driver.blender.collection.create",
                json!({"name": plan.collection_name}),
                true,
            )
            .await?;
        let mut objects = Vec::with_capacity(plan.objects.len());
        for (index, contribution) in plan.objects.iter().enumerate() {
            // Semwright 1.0.0 requires foreground human approval for destructive
            // arbitrary topology replacement. Motionwright's bounded Blender
            // projection only needs rectangles and circles, so it intentionally
            // composes allowlisted primitives plus reversible transforms instead.
            let object = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:object:{index}:create"),
                    "driver.blender.object.create",
                    json!({
                        "name": contribution.name,
                        "primitive": contribution.primitive.as_driver_name(),
                        "location": contribution.location
                    }),
                    true,
                )
                .await?;
            let transform = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:object:{index}:transform"),
                    "driver.blender.object.transform",
                    json!({
                        "name": contribution.name,
                        "location": contribution.location,
                        "rotation": contribution.rotation,
                        "scale": contribution.scale
                    }),
                    true,
                )
                .await?;
            let link = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:object:{index}:link"),
                    "driver.blender.collection.link",
                    json!({
                        "object": contribution.name,
                        "collection": plan.collection_name
                    }),
                    true,
                )
                .await?;
            let material = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:object:{index}:material"),
                    "driver.blender.material.create",
                    json!({
                        "name": contribution.material_name,
                        "color": contribution.color_rgba,
                        "roughness": 0.45,
                        "metallic": 0.0
                    }),
                    true,
                )
                .await?;
            let assignment = self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:object:{index}:material-assign"),
                    "driver.blender.material.assign",
                    json!({
                        "object": contribution.name,
                        "material": contribution.material_name
                    }),
                    true,
                )
                .await?;
            objects.push(json!({
                "node_id": contribution.node_id,
                "primitive": contribution.primitive,
                "object": object,
                "transform": transform,
                "collection_link": link,
                "material": material,
                "material_assignment": assignment
            }));
        }

        let export_path = blender_export_path(&plan);
        let export = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:export"),
                "driver.blender.export.glb",
                json!({
                    "collection": plan.collection_name,
                    "path": export_path,
                    "animations": false
                }),
                true,
            )
            .await?;
        let export_data = response_data(&export)?.clone();
        let exported_path = required_string(
            &export_data,
            "/path",
            "Blender GLB export returned no artifact path",
        )?;
        let exported_sha256 = required_string(
            &export_data,
            "/sha256",
            "Blender GLB export returned no artifact digest",
        )?;
        let exported_bytes = export_data
            .get("bytes")
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Blender GLB export returned no artifact size"))?;
        if export_data.get("changed").and_then(Value::as_bool) != Some(true)
            || export_data.get("format").and_then(Value::as_str) != Some("glb")
            || exported_path != export_path
            || exported_bytes <= 20
            || exported_bytes > BLENDER_GLTF_MAX_BYTES
            || export_data.get("objects").and_then(Value::as_u64) != Some(plan.objects.len() as u64)
        {
            return Err(backend(
                "Blender GLB export did not match the bounded Motionwright contribution",
            ));
        }
        let verified_path = verify_output_artifact(
            &self.client.connection().output_root,
            &exported_path,
            &exported_sha256,
            BLENDER_GLTF_MAX_BYTES,
        )?;

        Ok(json!({
            "renderer": "blender",
            "native_driver": "driver:blender",
            "project_revision": plan.revision,
            "scene_id": plan.scene_id,
            "collection": collection,
            "objects": objects,
            "export_path": export_path,
            "export": export,
            "artifact": {
                "path": exported_path,
                "sha256": exported_sha256,
                "bytes": exported_bytes,
                "verified_path": verified_path
            }
        }))
    }

    pub async fn realize_manim_scene(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        scene_id: Uuid,
        profile: ManimRenderProfile,
    ) -> NativeResult<Value> {
        if request_id.trim().is_empty()
            || request_id.len() > 96
            || request_id.chars().any(char::is_control)
        {
            return Err(invalid("Manim production request id is invalid"));
        }
        profile
            .validate()
            .map_err(|error| invalid(format!("Manim render profile is invalid: {error}")))?;

        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since the Manim contribution was prepared",
            ));
        }

        let projected = build_manim_plan(&project, scene_id)?;
        let plan_value = serde_json::to_value(&projected)
            .map_err(|_| invalid("Manim scene plan could not be encoded"))?;
        let plan: motionwright_manim_profile::ManimScenePlan =
            serde_json::from_value(plan_value.clone())
                .map_err(|_| invalid("Manim scene plan does not match the Driver SDK profile"))?;
        motionwright_manim_profile::validate_plan(&plan)
            .map_err(|error| invalid(format!("Manim scene plan is invalid: {error}")))?;
        let plan_bytes = serde_json::to_vec(&plan)
            .map_err(|_| invalid("Manim scene plan could not be digested"))?;
        let plan_sha256 = hex::encode(Sha256::digest(plan_bytes));

        let started = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:start"),
                "driver.manim-community.render.start",
                json!({"plan": plan_value, "profile": profile}),
                true,
            )
            .await?;
        let job_ref = required_string(
            response_data(&started)?,
            "/job_ref",
            "Manim render did not return a job reference",
        )?;

        let deadline = Instant::now() + Duration::from_secs(MANIM_RENDER_POLL_DEADLINE_SECS);
        let mut poll = 0_u32;
        loop {
            if Instant::now() >= deadline {
                return Err(Error::new(
                    ErrorCode::Timeout,
                    "Manim render did not reach a terminal state before the bounded deadline",
                )
                .uncertain());
            }
            let status = match self
                .execute(
                    project_id,
                    expected,
                    &format!("{request_id}:status:{poll}"),
                    "driver.manim-community.render.status",
                    json!({"job_ref": job_ref}),
                    false,
                )
                .await
            {
                Ok(status) => status,
                Err(error) if error.code == ErrorCode::Timeout => {
                    poll = poll
                        .checked_add(1)
                        .ok_or_else(|| backend("Manim render poll counter overflowed"))?;
                    sleep(Duration::from_millis(MANIM_RENDER_POLL_INTERVAL_MS)).await;
                    continue;
                }
                Err(error) => return Err(error),
            };
            match response_data(&status)?.get("state").and_then(Value::as_str) {
                Some("succeeded") => break,
                Some("failed") => {
                    return Err(backend("Manim Community render reported failure"));
                }
                Some("cancelled") => {
                    return Err(Error::new(
                        ErrorCode::Cancelled,
                        "Manim Community render was cancelled",
                    ));
                }
                Some("rendering") | Some("cancelling") => {}
                Some(_) => {
                    return Err(backend("Manim Community returned an unsupported job state"));
                }
                None => {
                    return Err(backend("Manim Community render status returned no state"));
                }
            }
            poll = poll
                .checked_add(1)
                .ok_or_else(|| backend("Manim render poll counter overflowed"))?;
            sleep(Duration::from_millis(MANIM_RENDER_POLL_INTERVAL_MS)).await;
        }

        let result = self
            .execute(
                project_id,
                expected,
                &format!("{request_id}:result"),
                "driver.manim-community.render.result",
                json!({"job_ref": job_ref}),
                false,
            )
            .await?;
        let result_data = response_data(&result)?;
        if result_data.get("state").and_then(Value::as_str) != Some("succeeded") {
            return Err(backend(
                "Manim render result is not a successful terminal artifact",
            ));
        }
        let artifact = result_data
            .get("artifact")
            .cloned()
            .ok_or_else(|| backend("Manim render result has no artifact"))?;
        let relative_path = required_string(
            &artifact,
            "/relative_path",
            "Manim render artifact has no relative path",
        )?;
        let sha256 = required_string(&artifact, "/sha256", "Manim render artifact has no digest")?;
        let bytes = artifact
            .get("bytes")
            .and_then(Value::as_u64)
            .ok_or_else(|| backend("Manim render artifact has no size"))?;
        if artifact.get("media_type").and_then(Value::as_str) != Some("video/mp4")
            || artifact.get("width").and_then(Value::as_u64) != Some(u64::from(profile.width))
            || artifact.get("height").and_then(Value::as_u64) != Some(u64::from(profile.height))
            || artifact.get("frame_rate").and_then(Value::as_u64)
                != Some(u64::from(profile.frame_rate))
            || artifact.get("plan_sha256").and_then(Value::as_str) != Some(plan_sha256.as_str())
            || bytes == 0
            || bytes > MANIM_VIDEO_MAX_BYTES
        {
            return Err(backend(
                "Manim render artifact does not match the bounded Motionwright scene plan",
            ));
        }
        let verified_path = verify_output_artifact(
            &self.client.connection().output_root,
            &relative_path,
            &sha256,
            MANIM_VIDEO_MAX_BYTES,
        )?;

        Ok(json!({
            "renderer": "manim-community",
            "native_driver": "driver:manim-community",
            "runtime_contract": "Manim Community 0.21.0",
            "project_revision": project.revision,
            "scene_id": scene_id,
            "job_ref": job_ref,
            "plan_sha256": plan_sha256,
            "profile": profile,
            "result": result,
            "artifact": {
                "relative_path": relative_path,
                "sha256": sha256,
                "bytes": bytes,
                "verified_path": verified_path
            }
        }))
    }

    pub async fn execute(
        &self,
        project_id: Uuid,
        expected: &RevisionStamp,
        request_id: &str,
        command: &str,
        args: Value,
        mutation: bool,
    ) -> NativeResult<Value> {
        let project = self.service.project(project_id).map_err(storage_error)?;
        if expected.resource != project.resource_key()
            || expected.generation != project.generation
            || expected.revision != project.revision
        {
            return Err(Error::new(
                ErrorCode::StaleReference,
                "Motionwright project changed since production was prepared",
            ));
        }
        if self.client.connection().resource != project.resource_key() {
            return Err(Error::new(
                ErrorCode::PermissionDenied,
                "Production connection is bound to another Motionwright resource",
            ));
        }
        let job_ref = args
            .get("job_ref")
            .or_else(|| args.get("job"))
            .and_then(Value::as_str)
            .map(str::to_owned);
        let digest = request_digest(command, &args)?;

        if let Some(prior) = self
            .service
            .latest_production_receipt(project_id, request_id)
            .map_err(storage_error)?
        {
            if prior.request_sha256 != digest || prior.command != command {
                return Err(Error::new(
                    ErrorCode::Conflict,
                    "Production request id was reused with different input",
                ));
            }
            if prior.stage == "completed"
                && let Some(result) = prior.payload.get("result")
            {
                return Ok(json!({
                    "replayed": true,
                    "result": result,
                    "receipt": prior
                }));
            }
            let error = Error::new(
                ErrorCode::Conflict,
                "Production request already has a non-terminal or failed receipt; inspect before retrying",
            );
            return Err(if prior.stage == "outcome_unknown" {
                error.uncertain()
            } else {
                error
            });
        }

        self.service
            .append_production_receipt(ProductionReceiptInput {
                project_id,
                generation: project.generation,
                revision: project.revision,
                request_id: request_id.into(),
                request_sha256: digest.clone(),
                command: command.into(),
                stage: "dispatching".into(),
                payload: json!({
                    "mutation": mutation,
                    "connection": self.client.connection().identity()?,
                    "job_ref": job_ref.clone()
                }),
            })
            .map_err(storage_error)?;

        match self.client.execute(command, args, mutation).await {
            Ok(result) => {
                let receipt = self
                    .service
                    .append_production_receipt(ProductionReceiptInput {
                        project_id,
                        generation: project.generation,
                        revision: project.revision,
                        request_id: request_id.into(),
                        request_sha256: digest.clone(),
                        command: command.into(),
                        stage: "completed".into(),
                        payload: json!({
                            "result": result.value,
                            "broker_request_id": result.request_id,
                            "job_ref": job_ref.clone()
                        }),
                    })
                    .map_err(|_| {
                        let error = backend(
                            "Production completed but its local receipt could not be persisted",
                        );
                        if mutation { error.uncertain() } else { error }
                    })?;
                Ok(json!({
                    "replayed": false,
                    "result": receipt.payload["result"],
                    "receipt": receipt
                }))
            }
            Err(error) => {
                let stage = if error.outcome_known {
                    "failed_known"
                } else {
                    "outcome_unknown"
                };
                let _ = self
                    .service
                    .append_production_receipt(ProductionReceiptInput {
                        project_id,
                        generation: project.generation,
                        revision: project.revision,
                        request_id: request_id.into(),
                        request_sha256: digest,
                        command: command.into(),
                        stage: stage.into(),
                        payload: json!({
                            "error": &error,
                            "mutation": mutation,
                            "job_ref": job_ref
                        }),
                    });
                Err(error)
            }
        }
    }
}

fn storage_error(error: motionwright_storage::StorageError) -> Error {
    match error {
        motionwright_storage::StorageError::NotFound => {
            Error::new(ErrorCode::NotFound, "Motionwright project not found")
        }
        motionwright_storage::StorageError::Conflict { .. }
        | motionwright_storage::StorageError::GenerationConflict => Error::new(
            ErrorCode::StaleReference,
            "Motionwright project changed since production was prepared",
        ),
        motionwright_storage::StorageError::RequestReuse => Error::new(
            ErrorCode::Conflict,
            "Production request id was reused with different input",
        ),
        _ => backend("Motionwright production receipt storage failed"),
    }
}

#[cfg(test)]
mod final_master_admission_tests {
    use super::*;
    use crate::{
        assembly::preflight_multi_segment_mlt, mlt_av_audio::preflight_multi_segment_audio,
        mlt_edit_plan::mlt_native_video_recipe,
    };
    use motionwright_domain::{
        AlignmentEvidence, BlendMode, CanvasNode, Change, CoordinateSpace, DeliveryArchetype,
        DeliveryNarrativeRole, DeliveryRecipe, DeliveryRecipeSchema, DeliverySceneIntent,
        NodeStyle, OriginalMixRecipe, Project, RationalTime, TranscriptSegment,
    };
    use motionwright_service::VoiceImportMetadata;
    use std::collections::{BTreeMap, BTreeSet};

    const CANONICAL_MASTER_REQUIRED: &str =
        "The selected deliverable requires its canonical original mix master";
    const SINGLE_MASTER_TARGET_UNSUPPORTED: &str =
        "MLT audiovisual mastering does not support LUFS or true-peak targets";

    #[derive(Clone, Copy)]
    enum LegacyMaster {
        Single,
        MultisegmentVoice,
        MultisegmentSfx,
    }

    #[derive(Clone, Copy)]
    enum MasteringTarget {
        Lufs,
        TruePeak,
    }

    struct AdmissionFixture {
        service: StudioService,
        project: Project,
        connection: ProductionConnection,
        options: FilmBuildOptions,
        rendered: MotionCanvasRenderEvidence,
        visual: Option<MltVerifiedLosslessTimeline>,
        audio: MltAudioArtifact,
        // Release SQLite and source handles before removing their directory.
        root: tempfile::TempDir,
    }

    fn apply(service: &StudioService, project: &Project, key: &str, change: Change) -> Project {
        service
            .apply(project.id, &project.stamp(), key, &change)
            .unwrap()
            .project
    }

    fn original_wav(bus: usize) -> Vec<u8> {
        let data_size = 96_000_u32 * 4;
        let mut wav = Vec::with_capacity(44 + data_size as usize);
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36 + data_size).to_le_bytes());
        wav.extend_from_slice(b"WAVEfmt ");
        wav.extend_from_slice(&16_u32.to_le_bytes());
        wav.extend_from_slice(&1_u16.to_le_bytes());
        wav.extend_from_slice(&2_u16.to_le_bytes());
        wav.extend_from_slice(&48_000_u32.to_le_bytes());
        wav.extend_from_slice(&192_000_u32.to_le_bytes());
        wav.extend_from_slice(&4_u16.to_le_bytes());
        wav.extend_from_slice(&16_u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&data_size.to_le_bytes());
        for frame in 0..96_000 {
            for channel in 0..2 {
                let value = (i16::try_from((frame + channel * 31) % 251).unwrap() - 125)
                    * i16::try_from(bus + 1).unwrap()
                    * 3;
                wav.extend_from_slice(&value.to_le_bytes());
            }
        }
        wav
    }

    fn no_dispatch_connection(root: &Path, resource: String) -> ProductionConnection {
        let executable = root.join(if cfg!(windows) {
            "master-admission-must-not-run.cmd"
        } else {
            "master-admission-must-not-run"
        });
        // This program can only mark an accidental dispatch and fail. It
        // cannot emit a provider envelope, native receipt or successful media.
        let script: &[u8] = if cfg!(windows) {
            b"@echo off\r\n> \"%~dp0native-dispatch-marker\" echo unexpected\r\nexit /b 97\r\n"
        } else {
            b"#!/bin/sh\n: > \"${0%/*}/native-dispatch-marker\"\nexit 97\n"
        };
        let socket = root.join("broker.sock");
        let session_file = root.join("session.json");
        let output_root = root.join("output");
        fs::create_dir(&output_root).unwrap();
        fs::write(&executable, script).unwrap();
        fs::write(&socket, b"").unwrap();
        fs::write(&session_file, b"{}").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            for (path, mode) in [
                (&executable, 0o700),
                (&socket, 0o600),
                (&session_file, 0o600),
                (&output_root, 0o700),
            ] {
                fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
            }
        }
        ProductionConnection {
            schema: CONNECTION_SCHEMA.into(),
            executable_sha256: hex::encode(Sha256::digest(script)),
            executable,
            socket,
            session_file,
            output_root,
            resource,
        }
    }

    fn fixture(
        entry: LegacyMaster,
        canonical_mix: bool,
        target: Option<MasteringTarget>,
    ) -> AdmissionFixture {
        let root = tempfile::Builder::new()
            .prefix("final-master-admission-")
            .tempdir_in(env!("CARGO_MANIFEST_DIR"))
            .unwrap();
        let service = StudioService::open(root.path().join("studio.sqlite3")).unwrap();
        let mut project = service.create_project("Final master admission").unwrap();
        let scene_count = if matches!(entry, LegacyMaster::Single) {
            1
        } else {
            33
        };
        for index in 0..scene_count {
            project = apply(
                &service,
                &project,
                &format!("admission-scene-{index}"),
                Change::AddScene {
                    name: format!("Admission source {index}"),
                    objective: "Saved source and exact frame clock".into(),
                    duration_seconds: 1,
                },
            );
            let scene_id = project.scenes.last().unwrap().id;
            let frames = if scene_count == 1 {
                60
            } else if index == 32 {
                28
            } else {
                1
            };
            project = apply(
                &service,
                &project,
                &format!("admission-clock-{index}"),
                Change::SetSceneDuration {
                    scene_id,
                    duration: RationalTime::new(frames, 30).unwrap(),
                },
            );
            project = apply(
                &service,
                &project,
                &format!("admission-node-{index}"),
                Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::new_v4(),
                        name: "Admission rectangle".into(),
                        kind: "rectangle".into(),
                        parent_id: None,
                        x: 400.0,
                        y: 250.0,
                        width: 160.0,
                        height: 120.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: None,
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#F5F5F2".into()),
                            stroke: None,
                            stroke_width: 0.0,
                            font_family: None,
                            font_size: None,
                            font_weight: None,
                            line_height: None,
                            blend_mode: BlendMode::Normal,
                        },
                        relations: vec![],
                        property_locks: BTreeSet::new(),
                        keyframes: vec![],
                    },
                },
            );
        }
        let paths = [0, 1, 2].map(|bus| root.path().join(format!("original-bus-{bus}.wav")));
        for (bus, path) in paths.iter().enumerate() {
            fs::write(path, original_wav(bus)).unwrap();
        }
        project = service
            .import_voice_file(
                project.id,
                &project.stamp(),
                "admission-voice",
                &paths[0],
                VoiceImportMetadata {
                    name: "Original admission signal".into(),
                    label: "Measured numerical source, not speech".into(),
                    media_type: "audio/wav".into(),
                },
            )
            .unwrap()
            .project;
        let track = project.audio.voice_tracks[0].clone();
        let mut source_ids = [track.asset_id, Uuid::nil(), Uuid::nil()];
        for bus in 1..3 {
            project = service
                .import_asset_file(
                    project.id,
                    &project.stamp(),
                    &format!("admission-bus-{bus}"),
                    &paths[bus],
                    format!("Original admission bus {bus}"),
                    "audio/wav".into(),
                )
                .unwrap()
                .project;
            source_ids[bus] = project.assets.last().unwrap().id;
        }
        let mut profile = project.deliverables[0].clone();
        profile.width = 1280;
        profile.height = 720;
        profile.frame_rate = RationalTime::new(30, 1).unwrap();
        profile.audio_sample_rate_hz = 48_000;
        profile.language = "en".into();
        profile.voice_track_id = Some(track.id);
        profile.video_codec = VideoCodec::H264;
        profile.audio_codec = AudioCodec::Aac;
        profile.container = OutputContainer::Mp4;
        profile.color_space = OutputColorSpace::Rec709;
        profile.captions = false;
        profile.burn_in_captions = false;
        profile.included_scene_ids.clear();
        let deliverable_id = profile.id;
        project = apply(
            &service,
            &project,
            "admission-profile",
            Change::UpsertDeliverable { profile },
        );
        project = apply(
            &service,
            &project,
            "admission-reviewed-window",
            Change::AddTranscriptSegment {
                segment: TranscriptSegment {
                    id: Uuid::new_v4(),
                    voice_track_id: track.id,
                    start: RationalTime::new(6, 5).unwrap(),
                    end: RationalTime::new(3, 2).unwrap(),
                    text: "Authored timing for a numerical test signal".into(),
                    speaker: None,
                    alignment: AlignmentEvidence::Manual,
                },
            },
        );
        let mut mix = project.audio.mix.clone();
        mix.voice_gain_db = 0.0;
        mix.music_gain_db = -12.0;
        mix.target_lufs = match target {
            Some(MasteringTarget::Lufs) => Some(-14.0),
            _ => None,
        };
        mix.target_true_peak_dbfs = match target {
            Some(MasteringTarget::TruePeak) => Some(-1.0),
            _ => None,
        };
        project = apply(
            &service,
            &project,
            "admission-mix-intent",
            Change::SetMixIntent { mix },
        );
        let recipe = DeliveryRecipe {
            schema: DeliveryRecipeSchema::V1,
            scene_intents: project
                .scenes
                .iter()
                .map(|scene| DeliverySceneIntent {
                    scene_id: scene.id,
                    role: Some(DeliveryNarrativeRole::Mechanism),
                    archetype: Some(DeliveryArchetype::Statement),
                })
                .collect(),
            original_mix: canonical_mix.then_some(OriginalMixRecipe {
                music_asset_id: Some(source_ids[1]),
                sfx_asset_id: Some(source_ids[2]),
                sfx_gain_db: -3.0,
                duck_attenuation_db: 12.0,
                duck_attack_samples: 2_400,
                duck_release_samples: 4_800,
            }),
        };
        recipe.validate(&project.scenes, &project.assets).unwrap();
        project = apply(
            &service,
            &project,
            "admission-delivery-recipe",
            Change::SetDeliveryRecipe {
                deliverable_profile_id: deliverable_id,
                recipe: Some(recipe),
            },
        );
        for path in paths {
            fs::remove_file(path).unwrap();
        }
        project.validate().unwrap();
        assert_eq!(project.audio.mix.voice_gain_db, 0.0);
        assert_eq!(project.audio.mix.music_gain_db, -12.0);
        if canonical_mix {
            assert!(project.audio.mix.target_lufs.is_none());
            assert!(project.audio.mix.target_true_peak_dbfs.is_none());
            let plan = resolve_canonical_original_mix_plan(
                &service,
                project.id,
                &project.stamp(),
                deliverable_id,
            )
            .unwrap();
            assert_eq!(plan.sample_frames, 96_000);
            assert_eq!(plan.measured_voice.asset_id, source_ids[0]);
            assert_eq!(plan.original_music.asset_id, source_ids[1]);
            assert_eq!(plan.original_sfx.asset_id, source_ids[2]);
        } else {
            assert!(
                project.production_design.delivery_recipes[&deliverable_id]
                    .original_mix
                    .is_none()
            );
        }
        let connection = no_dispatch_connection(root.path(), project.resource_key());
        let options = canonical_original_mix_film_options(&project, deliverable_id).unwrap();
        let rendered =
            admitted_manifest_fixture(&project, deliverable_id, &options, &connection.output_root);
        let visual = if matches!(entry, LegacyMaster::Single) {
            assert_eq!(rendered.segments.len(), 1);
            assert_eq!(rendered.segments[0].frame_count, 60);
            None
        } else {
            assert_eq!(rendered.segments.len(), 2);
            Some(admitted_visual_fixture(
                &project,
                &options,
                &rendered,
                &connection.output_root,
            ))
        };
        let voice = service
            .verified_master_voice(project.id, &project.stamp(), track.id)
            .unwrap();
        let audio = MltAudioArtifact {
            relative_path: "staged-voice.wav".into(),
            sha256: voice.sha256.clone(),
            sample_rate: 48_000,
            channels: 2,
        };
        fs::copy(
            &voice.path,
            connection.output_root.join(&audio.relative_path),
        )
        .unwrap();
        verify_output_artifact(
            &connection.output_root,
            &audio.relative_path,
            &audio.sha256,
            MLT_MASTER_MAX_BYTES,
        )
        .unwrap();
        if let Some(visual) = &visual {
            let recipe = mlt_multisegment_av::verify_multisegment_visual_source(
                &project,
                deliverable_id,
                &options,
                &rendered,
                visual,
                &connection.output_root,
            )
            .unwrap();
            // The actual CAS/WAV, source clock and ordinary voice gain all
            // pass independently. These checks do not decode the synthetic
            // visual files below or turn them into native render evidence.
            let source = preflight_multi_segment_audio(
                &project,
                deliverable_id,
                &recipe,
                &visual.source,
                &voice,
                &connection.output_root,
            )
            .unwrap();
            assert_eq!(source.total_frames, 60);
            assert_eq!(source.audio_pcm_frames, 96_000);
        }
        AdmissionFixture {
            service,
            project,
            connection,
            options,
            rendered,
            visual,
            audio,
            root,
        }
    }

    // Complete, digest-checked manifests and typed visual records exercise
    // the existing source-admission contract. The indexed PNG/FFV1 content
    // is synthetic; no renderer is invoked or native success claimed.
    fn admitted_manifest_fixture(
        project: &Project,
        deliverable_id: Uuid,
        options: &FilmBuildOptions,
        output_root: &Path,
    ) -> MotionCanvasRenderEvidence {
        let source = build_motion_canvas_segments(project, deliverable_id, options).unwrap();
        let mut segments = Vec::new();
        for (index, segment) in source.iter().enumerate() {
            let directory = format!("render-{index:032x}");
            let manifest_path = format!("{directory}/artifact-manifest.json");
            fs::create_dir(output_root.join(&directory)).unwrap();
            let manifest = json!({
                "plan": {
                    "width": 1280, "height": 720, "fps": 30, "fps_denominator": 1,
                    "frame_count": segment.frame_count, "first_frame": 0,
                    "end_frame_exclusive": segment.frame_count,
                },
                "frames": (0..segment.frame_count).map(|frame| json!({
                    "index": frame, "file": format!("frames/{frame:06}.png"),
                    "bytes": 128, "sha256": "a".repeat(64),
                })).collect::<Vec<_>>(),
            });
            let bytes = serde_json::to_vec(&manifest).unwrap();
            let digest = hex::encode(Sha256::digest(&bytes));
            fs::write(output_root.join(&manifest_path), bytes).unwrap();
            verify_output_artifact(output_root, &manifest_path, &digest, 8 * 1024 * 1024).unwrap();
            segments.push(MotionCanvasSegmentEvidence {
                segment_id: segment.id.clone(), scene_ids: segment.scene_ids.clone(),
                frame_count: segment.frame_count, plan_ref: format!("admission-plan-{index}"),
                fingerprint: format!("admission-fingerprint-{index}"),
                job_ref: format!("admission-job-{index}"),
                artifact: json!({
                    "directory": directory, "manifest": manifest_path,
                    "manifest_sha256": digest, "frame_count": segment.frame_count,
                }),
                verification: json!({
                    "report": {"execution_status": "completed", "support_level": "native"},
                    "measurement": {"findings": [], "validation": {"checks": [{"verdict": "PASS"}]}},
                }),
            });
        }
        MotionCanvasRenderEvidence {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            frame_rate: options.frame_rate,
            segments,
        }
    }

    fn admitted_visual_fixture(
        project: &Project,
        options: &FilmBuildOptions,
        rendered: &MotionCanvasRenderEvidence,
        output_root: &Path,
    ) -> MltVerifiedLosslessTimeline {
        let deliverable_id = rendered.deliverable_id;
        let checked =
            preflight_multi_segment_mlt(project, deliverable_id, options, rendered, output_root)
                .unwrap();
        let recipe = mlt_native_video_recipe(&checked).unwrap();
        assert_eq!(recipe.total_frames, 60);
        let verified_video_segments = recipe
            .clips
            .iter()
            .enumerate()
            .map(|(index, clip)| {
                let bytes = format!("synthetic admission-only segment {index}").into_bytes();
                fs::write(output_root.join(&clip.ffv1_output_path), &bytes).unwrap();
                MltVerifiedMezzanine {
                    segment_id: clip.segment_id.clone(),
                    scene_ids: clip.scene_ids.clone(),
                    output_start_frame: clip.start_frame,
                    frame_count: clip.source_out_exclusive,
                    native_render_job_ref: clip.source_native_job_ref.clone(),
                    source_manifest_sha256: clip.source_manifest_sha256.clone(),
                    artifact_path: clip.ffv1_output_path.clone(),
                    artifact_sha256: hex::encode(Sha256::digest(&bytes)),
                    artifact_bytes: bytes.len() as u64,
                }
            })
            .collect();
        let source = MltPreparedMezzanines {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            input_sha256: recipe.input_sha256.clone(),
            fps_num: 30,
            fps_den: 1,
            total_frames: 60,
            verified_video_segments,
            evidence_scope: "real-source-ffv1-segments-not-composited".into(),
        };
        let bytes = b"synthetic admission-only combined visual";
        let artifact_path = format!(
            "{}-sequence-video-only-ffv1.mkv",
            recipe.owner_output_namespace
        );
        fs::write(output_root.join(&artifact_path), bytes).unwrap();
        MltVerifiedLosslessTimeline {
            project_resource: project.resource_key(),
            generation: project.generation,
            revision: project.revision,
            deliverable_id,
            recipe_sha256: recipe.input_sha256,
            frame_count: 60,
            native_frame_count_observed: Some(60),
            transport_pcm_audio: false,
            native_render_profile: "lossless-video-only".into(),
            provider_project_cleanup: "not_requested_requires_foreground_broker_consent".into(),
            native_job_ref: "render:admission-only-synthetic".into(),
            provider_revision: "admission-only-synthetic".into(),
            artifact_path,
            artifact_sha256: hex::encode(Sha256::digest(bytes)),
            artifact_bytes: bytes.len() as u64,
            source,
            evidence_scope: "actual-native-mlt-ffv1-no-audio-exact-decoded-frames-not-final-master"
                .into(),
        }
    }

    fn output_snapshot(root: &Path) -> BTreeMap<PathBuf, Option<Vec<u8>>> {
        fn visit(root: &Path, folder: &Path, result: &mut BTreeMap<PathBuf, Option<Vec<u8>>>) {
            for entry in fs::read_dir(folder).unwrap() {
                let path = entry.unwrap().path();
                let key = path.strip_prefix(root).unwrap().to_path_buf();
                let kind = fs::symlink_metadata(&path).unwrap();
                assert!(!kind.file_type().is_symlink());
                if kind.is_dir() {
                    result.insert(key, None);
                    visit(root, &path, result);
                } else {
                    assert!(kind.is_file());
                    result.insert(key, Some(fs::read(path).unwrap()));
                }
            }
        }
        let mut result = BTreeMap::new();
        visit(root, root, &mut result);
        result
    }

    async fn assert_admission_rejected(
        entry: LegacyMaster,
        canonical_mix: bool,
        target: Option<MasteringTarget>,
        expected_message: &str,
    ) {
        let f = fixture(entry, canonical_mix, target);
        let before = output_snapshot(&f.connection.output_root);
        let coordinator =
            ProductionCoordinator::new(f.service.clone(), f.connection.clone()).unwrap();
        assert!(coordinator.receipts(f.project.id, 256).unwrap().is_empty());
        let error = match entry {
            LegacyMaster::Single => coordinator
                .assemble_mlt_av_master(
                    f.project.id,
                    &f.project.stamp(),
                    MltAvMasterRequest {
                        request_id: "legacy-final-admission-single",
                        deliverable_id: f.rendered.deliverable_id,
                        motion: &f.rendered,
                        audio: &f.audio,
                        sync: None,
                    },
                )
                .await
                .unwrap_err(),
            LegacyMaster::MultisegmentVoice => coordinator
                .assemble_native_mlt_multisegment_av_master(
                    f.project.id,
                    &f.project.stamp(),
                    MltMultisegmentAvMasterRequest {
                        request_id: "legacy-final-admission-voice",
                        deliverable_id: f.rendered.deliverable_id,
                        options: &f.options,
                        rendered: &f.rendered,
                        visual: f.visual.as_ref().unwrap(),
                        audio: &f.audio,
                    },
                )
                .await
                .unwrap_err(),
            LegacyMaster::MultisegmentSfx => coordinator
                .assemble_native_mlt_multisegment_sfx_master(
                    f.project.id,
                    &f.project.stamp(),
                    MltMultisegmentSfxMasterRequest {
                        request_id: "legacy-final-admission-sfx",
                        deliverable_id: f.rendered.deliverable_id,
                        options: &f.options,
                        rendered: &f.rendered,
                        visual: f.visual.as_ref().unwrap(),
                        audio: &f.audio,
                    },
                )
                .await
                .unwrap_err(),
        };
        // Check effects before the error details, so a failing baseline also
        // exposes an accidental dispatch instead of hiding it behind its error.
        assert!(coordinator.receipts(f.project.id, 256).unwrap().is_empty());
        assert_eq!(output_snapshot(&f.connection.output_root), before);
        assert_eq!(
            fs::symlink_metadata(f.root.path().join("native-dispatch-marker"))
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::NotFound,
        );
        assert_eq!(f.service.project(f.project.id).unwrap(), f.project);
        assert_eq!(error.code, ErrorCode::Unsupported);
        assert_eq!(error.message, expected_message);
        assert!(error.outcome_known);
    }

    #[tokio::test]
    async fn r1_single_master_rejects_saved_original_mix_before_any_effect() {
        assert_admission_rejected(LegacyMaster::Single, true, None, CANONICAL_MASTER_REQUIRED)
            .await;
    }

    #[tokio::test]
    async fn r1_multisegment_voice_rejects_saved_original_mix_before_any_effect() {
        assert_admission_rejected(
            LegacyMaster::MultisegmentVoice,
            true,
            None,
            CANONICAL_MASTER_REQUIRED,
        )
        .await;
    }

    #[tokio::test]
    async fn r1_multisegment_sfx_rejects_saved_original_mix_before_any_effect() {
        // Canonical three-bus and semantic-SFX-only are mutually exclusive
        // source classes. A valid canonical recipe must select the canonical
        // route before a later SFX-only guard reports a missing/foreign cue.
        assert_admission_rejected(
            LegacyMaster::MultisegmentSfx,
            true,
            None,
            CANONICAL_MASTER_REQUIRED,
        )
        .await;
    }

    #[tokio::test]
    async fn r2_single_master_rejects_lufs_without_original_mix_before_any_effect() {
        assert_admission_rejected(
            LegacyMaster::Single,
            false,
            Some(MasteringTarget::Lufs),
            SINGLE_MASTER_TARGET_UNSUPPORTED,
        )
        .await;
    }

    #[tokio::test]
    async fn r2_single_master_rejects_true_peak_without_original_mix_before_any_effect() {
        assert_admission_rejected(
            LegacyMaster::Single,
            false,
            Some(MasteringTarget::TruePeak),
            SINGLE_MASTER_TARGET_UNSUPPORTED,
        )
        .await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use motionwright_domain::{BlendMode, CanvasNode, Change, CoordinateSpace, NodeStyle};
    use motionwright_storage::Store;
    use semwright_media_time::Rate;
    use semwright_motion_authoring::{Archetype, NarrativeRole};
    use semwright_native_sdk::types::{Execution, InvocationProvenance};
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    use std::{collections::BTreeSet, fs::File};

    fn fixture_service() -> (
        StudioService,
        motionwright_domain::Project,
        tempfile::TempDir,
    ) {
        let temp = tempfile::tempdir().unwrap();
        let store = Store::open(temp.path().join("motionwright.sqlite3")).unwrap();
        let service = StudioService::from_store(store);
        let project = service.create_project("Production fixture").unwrap();
        (service, project, temp)
    }

    #[cfg(unix)]
    fn fake_connection(temp: &tempfile::TempDir, resource: String) -> ProductionConnection {
        let executable = temp.path().join("semwright-fake");
        let socket = temp.path().join("broker.sock");
        let session = temp.path().join("session.json");
        let output = temp.path().join("output");
        fs::create_dir(&output).unwrap();
        File::create(&socket).unwrap();
        fs::write(&session, b"{}").unwrap();

        let execution = Execution {
            backend: "driver:motion-canvas".into(),
            duration_ms: 1,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            provenance: Some(InvocationProvenance {
                provider: "driver:motion-canvas".into(),
                source: SourceKind::Driver,
                provider_version: "test".into(),
                capability_version: "test".into(),
                descriptor_sha256: "ab".repeat(32),
                untrusted_metadata: false,
                catalog_revision: 1,
                execution_provider: Some("driver:motion-canvas".into()),
                provider_generation: Some(1),
            }),
            dry_run: false,
        };
        let envelope = Envelope {
            ok: true,
            request_id: "broker-request".into(),
            command: "driver.motion-canvas.composition.inspect".into(),
            data: Some(json!({"film":null})),
            error: None,
            execution,
            warnings: vec![],
        };
        let output_json = serde_json::to_string(&envelope).unwrap();
        fs::write(
            &executable,
            format!(
                "#!/bin/sh\nprintf '%s\\n' '{}'
",
                output_json.replace("'", "'\\''")
            ),
        )
        .unwrap();
        let mut permissions = fs::metadata(&executable).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&executable, permissions).unwrap();
        let mut permissions = fs::metadata(&session).unwrap().permissions();
        permissions.set_mode(0o600);
        fs::set_permissions(&session, permissions).unwrap();
        let mut permissions = fs::metadata(&socket).unwrap().permissions();
        permissions.set_mode(0o600);
        fs::set_permissions(&socket, permissions).unwrap();
        let mut permissions = fs::metadata(&output).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&output, permissions).unwrap();

        ProductionConnection {
            schema: CONNECTION_SCHEMA.into(),
            executable_sha256: sha256_file(&executable).unwrap(),
            executable,
            socket,
            session_file: session,
            output_root: output,
            resource,
        }
    }

    #[cfg(unix)]
    fn fake_render_connection(temp: &tempfile::TempDir, resource: String) -> ProductionConnection {
        let mut connection = fake_connection(temp, resource);
        let execution = Execution {
            backend: "driver:motion-canvas".into(),
            duration_ms: 1,
            policy_decision: "allow".into(),
            fallbacks_attempted: vec![],
            provenance: Some(InvocationProvenance {
                provider: "driver:motion-canvas".into(),
                source: SourceKind::Driver,
                provider_version: "test".into(),
                capability_version: "test".into(),
                descriptor_sha256: "cd".repeat(32),
                untrusted_metadata: false,
                catalog_revision: 1,
                execution_provider: Some("driver:motion-canvas".into()),
                provider_generation: Some(2),
            }),
            dry_run: false,
        };
        let execution_json = serde_json::to_string(&execution).unwrap();
        let script = r#"#!/bin/sh
command="$7"
case "$command" in
  driver.motion-canvas.composition.plan)
    data='{"plan_ref":"plan-native-1","repair":false}'
    ;;
  driver.motion-canvas.composition.apply)
    data='{"applied":true,"execution_status":"completed","fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
    ;;
  driver.motion-canvas.render.start)
    data='{"job_ref":"job-native-1","state":"queued"}'
    ;;
  driver.motion-canvas.render.status)
    data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60}}'
    ;;
  driver.motion-canvas.render.result)
    data='{"job_ref":"job-native-1","state":"succeeded","artifact":{"directory":"render-native-1","frame_count":60,"manifest_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}'
    ;;
  driver.motion-canvas.composition.verify)
    data='{"report":{"execution_status":"completed","support_level":"native"},"measurement":{"findings":[],"validation":{"checks":[{"id":"native-frame-evidence","verdict":"PASS"}]}}}'
    ;;
  *)
    exit 2
    ;;
esac
printf '{"ok":true,"request_id":"broker-render-request","command":"%s","data":%s,"error":null,"execution":__EXECUTION__,"warnings":[]}\n' "$command" "$data"
"#
        .replace("__EXECUTION__", &execution_json);
        fs::write(&connection.executable, script).unwrap();
        let mut permissions = fs::metadata(&connection.executable).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&connection.executable, permissions).unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();
        connection
    }

    fn motion_canvas_fixture(
        service: &StudioService,
        initial: motionwright_domain::Project,
    ) -> motionwright_domain::Project {
        let scene = service
            .apply(
                initial.id,
                &RevisionStamp::from(&initial),
                "render-fixture-scene",
                &Change::AddScene {
                    name: "Native render".into(),
                    objective: "Prove the canonical render path".into(),
                    duration_seconds: 2,
                },
            )
            .unwrap()
            .project;
        let scene_id = scene.scenes[0].id;
        service
            .apply(
                scene.id,
                &RevisionStamp::from(&scene),
                "render-fixture-node",
                &Change::AddCanvasNode {
                    scene_id,
                    node: CanvasNode {
                        id: Uuid::now_v7(),
                        name: "Native title".into(),
                        kind: "text".into(),
                        parent_id: None,
                        x: 240.0,
                        y: 320.0,
                        width: 960.0,
                        height: 160.0,
                        rotation_deg: 0.0,
                        opacity: 1.0,
                        text: Some("Motionwright".into()),
                        coordinate_space: CoordinateSpace::ProjectPixels,
                        z_index: 1,
                        style: NodeStyle {
                            fill: Some("#F5F5F2".into()),
                            stroke: None,
                            stroke_width: 0.0,
                            font_family: Some("Instrument Sans Variable".into()),
                            font_size: Some(64.0),
                            font_weight: Some(700),
                            line_height: Some(1.05),
                            blend_mode: BlendMode::Normal,
                        },
                        relations: vec![],
                        property_locks: BTreeSet::new(),
                        keyframes: vec![],
                    },
                },
            )
            .unwrap()
            .project
    }

    #[test]
    fn motion_failure_category_is_allowlisted_and_never_exposes_raw_driver_errors() {
        assert_eq!(
            motion_failure_class(&json!({
                "state": "failed",
                "failure_class": "renderer_state_frame_clock",
                "error": "/home/customer/private/secret-still-is-not-exposed",
            })),
            "renderer_state_frame_clock",
        );
        assert_eq!(
            motion_failure_class(&json!({
                "state": "failed",
                "failure_class": "../../customer-secrets",
                "error": "/home/customer/private/secret-still-is-not-exposed",
            })),
            "unclassified",
        );
        assert_eq!(
            motion_failure_class(&json!({
                "state": "failed",
                "error": "runtime_tool returned /tmp/owner-private",
            })),
            "unclassified",
        );
        assert_eq!(
            motion_failure_class(&json!({
                "state": "failed",
                "failure_class": "runtime_oom",
            })),
            "runtime_oom",
        );
    }

    #[test]
    fn native_caption_failed_validation_exposes_only_a_closed_rule_category() {
        let secret = "/owner/private/media/take.wav job_ref=secret";
        for (rule, expected) in [
            ("caption-timing", "caption_timing"),
            ("resolved-cues", "resolved_cues"),
            ("native-frame-coverage", "frame_coverage"),
            ("foreign-untrusted-rule", "other_rule"),
        ] {
            let view = json!({
                "report": {"execution_status":"completed", "support_level":"native"},
                "measurement": {
                    "findings": [{"rule": rule, "reason": secret}],
                    "validation": {"checks":[
                        {"rule":"native-frame-coverage","verdict":"PASS"},
                        {"rule":rule,"verdict":"FAIL", "reason":secret}
                    ]}
                },
                "raw_provider_receipt":secret
            });
            assert!(ensure_native_motion_verification(&view).is_err());
            let gate = native_caption_verification_gate(&view);
            assert_eq!(gate, expected);
            assert!(!gate.contains(secret));
        }
        let unknown = json!({
            "report": {"execution_status":"completed", "support_level":"native"},
            "measurement": {"findings":[{"id":"untrusted", "reason":secret}],
                "validation":{"checks":[{"rule":"caption-timing","verdict":"PASS"}]}}
        });
        assert_eq!(
            native_caption_verification_gate(&unknown),
            "unresolved_findings"
        );
        assert!(ensure_native_motion_verification(&unknown).is_err());
        let missing = json!({"report":{"execution_status":"completed","support_level":"native"}});
        assert_eq!(native_caption_verification_gate(&missing), "checks_missing");
        assert!(ensure_native_motion_verification(&missing).is_err());
        let not_native = json!({"report":{"execution_status":"completed","support_level":"mock"}});
        assert_eq!(native_caption_verification_gate(&not_native), "not_native");
    }

    #[test]
    fn runtime_version_parser_is_strict_and_bounded() {
        assert_eq!(
            parse_semwright_cli_version(b"semwright 1.0.0\n").unwrap(),
            "1.0.0"
        );
        assert!(parse_semwright_cli_version(b"semwright\n").is_err());
        assert!(parse_semwright_cli_version(b"other 1.0.0\n").is_err());
        assert!(parse_semwright_cli_version(b"semwright 1.0.0 injected\n").is_err());
        assert!(parse_semwright_cli_version(&vec![b'a'; MAX_RUNTIME_VERSION_BYTES + 1]).is_err());
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn runtime_probe_revalidates_digest_and_reports_version_compatibility() {
        let temp = tempfile::tempdir().unwrap();
        let mut connection = fake_connection(&temp, "project:test".into());
        fs::write(
            &connection.executable,
            b"#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then\n  printf 'semwright 1.0.0\\n'\n  exit 0\nfi\nexit 2\n",
        )
        .unwrap();
        let mut permissions = fs::metadata(&connection.executable).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&connection.executable, permissions).unwrap();
        connection.executable_sha256 = sha256_file(&connection.executable).unwrap();

        let client = ProductionClient::new(connection.clone()).unwrap();
        let probe = client.probe_runtime("1.0.0").await.unwrap();
        assert_eq!(probe.observed_version, "1.0.0");
        assert_eq!(probe.expected_version, "1.0.0");
        assert!(probe.version_compatible);
        assert_eq!(probe.executable_sha256, connection.executable_sha256);
        assert_eq!(probe.connection_identity, connection.identity().unwrap());

        let mismatch = client.probe_runtime("1.1.0").await.unwrap();
        assert!(!mismatch.version_compatible);

        fs::write(
            &connection.executable,
            b"#!/bin/sh\nprintf 'semwright 1.0.0\\n'\n",
        )
        .unwrap();
        let error = client.probe_runtime("1.0.0").await.unwrap_err();
        assert_eq!(error.code, ErrorCode::PermissionDenied);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn started_motion_render_reuses_exact_mutation_receipt_and_preserves_unknown_status() {
        let (service, project, temp) = fixture_service();
        let connection = fake_render_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let expected = RevisionStamp::from(&project);
        let args = json!({
            "expected_fingerprint": "a".repeat(64),
            "profile": {
                "first_frame": 0,
                "end_frame_exclusive": 60,
                "scale": "full",
                "transparent": false,
                "timeout_ms": 300_000
            }
        });
        let started = coordinator
            .execute(
                project.id,
                &expected,
                "reliability-same-job-start",
                "driver.motion-canvas.render.start",
                args.clone(),
                true,
            )
            .await
            .unwrap();
        assert_eq!(started["replayed"], false);
        let same = coordinator
            .execute(
                project.id,
                &expected,
                "reliability-same-job-start",
                "driver.motion-canvas.render.start",
                args,
                true,
            )
            .await
            .unwrap();
        assert_eq!(same["replayed"], true);
        assert_eq!(coordinator.receipts(project.id, 20).unwrap().len(), 2);
        assert_eq!(
            coordinator
                .receipts(project.id, 20)
                .unwrap()
                .iter()
                .filter(|receipt| {
                    receipt.command == "driver.motion-canvas.render.start"
                        && receipt.stage == "dispatching"
                })
                .count(),
            1,
            "a reentered request must not execute another native render mutation"
        );

        let inactive = unconfirmed_started_motion_render(Error::new(
            ErrorCode::Unavailable,
            "Provider capability generation is inactive",
        ));
        assert!(!inactive.outcome_known);
        assert!(inactive.message.contains("Reconcile the original job"));
        assert!(
            coordinator
                .receipts(project.id, 20)
                .unwrap()
                .iter()
                .all(|receipt| receipt.command != "driver.motion-canvas.render.result"),
            "an unconfirmed job must never invent a verified native result"
        );
    }

    #[test]
    fn motion_status_retry_is_narrowly_limited_to_timeouts() {
        let timeout = Error::new(ErrorCode::Timeout, "status observation timed out").uncertain();
        let unavailable = Error::new(ErrorCode::Unavailable, "driver unavailable");
        let backend_failed = Error::new(ErrorCode::BackendFailed, "renderer failed");

        assert!(retryable_motion_status_error(&timeout));
        assert!(!retryable_motion_status_error(&unavailable));
        assert!(!retryable_motion_status_error(&backend_failed));

        let abandoned = unconfirmed_started_motion_render(unavailable);
        assert_eq!(abandoned.code, ErrorCode::Unavailable);
        assert!(!abandoned.outcome_known);
        assert!(abandoned.message.contains("Reconcile the original job"));
        assert!(abandoned.message.contains("do not redispatch"));
        assert_eq!(
            abandoned
                .progress
                .expect("known job start must be retained")
                .failed_step,
            "driver.motion-canvas.render.start"
        );

        let broken_status = unconfirmed_started_motion_render(backend_failed);
        assert_eq!(broken_status.code, ErrorCode::BackendFailed);
        assert!(!broken_status.outcome_known);
        let timed_out = unconfirmed_started_motion_render(timeout);
        assert_eq!(timed_out.code, ErrorCode::Timeout);
        assert!(!timed_out.outcome_known);
    }

    #[test]
    fn production_allowlist_binds_blender_to_the_native_driver() {
        for command in BLENDER_COMMANDS {
            let authority = expected_authority(command).unwrap();
            assert_eq!(authority.provider, "driver:blender");
            assert_eq!(authority.source, SourceKind::Driver);
            assert!(authority.provider_generation_required);
        }
        assert!(expected_authority("driver.blender.python.exec").is_none());

        let manim = expected_authority("driver.manim-community.render.start")
            .expect("bounded Manim render start must be explicitly enabled");
        assert_eq!(manim.provider, "driver:manim-community");
        assert_eq!(manim.source, SourceKind::Driver);
        assert!(manim.provider_generation_required);
        assert!(expected_authority("driver.manim-community.python.exec").is_none());
        assert!(expected_authority("driver.manim.render").is_none());
    }

    #[test]
    fn workflow_surface_is_bounded_and_bound_to_builtin_core() {
        for command in WORKFLOW_READ_COMMANDS
            .iter()
            .chain(WORKFLOW_ACTION_COMMANDS.iter())
        {
            let authority = expected_authority(command).unwrap();
            assert_eq!(authority.provider, "semwright-core");
            assert_eq!(authority.source, SourceKind::Builtin);
            assert!(!authority.provider_generation_required);
        }
        assert!(expected_authority("workflow.trace.delete").is_none());
        assert!(expected_authority("workflow.demote").is_none());
        assert!(expected_authority("workflow.suggestion.dismiss").is_none());
        assert!(WORKFLOW_MUTATING_COMMANDS.contains(&"workflow.replay"));
        assert!(!WORKFLOW_MUTATING_COMMANDS.contains(&"workflow.proposal.plan"));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn coordinator_binds_revision_provenance_and_request_replay() {
        let (service, project, temp) = fixture_service();
        let connection = fake_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let expected = motionwright_domain::RevisionStamp::from(&project);

        let first = coordinator
            .execute(
                project.id,
                &expected,
                "request-one",
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await
            .unwrap();
        assert_eq!(first["replayed"], false);
        assert_eq!(first["result"]["provenance_checked"], true);

        let replay = coordinator
            .execute(
                project.id,
                &expected,
                "request-one",
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await
            .unwrap();
        assert_eq!(replay["replayed"], true);
        assert_eq!(coordinator.receipts(project.id, 10).unwrap().len(), 2);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn motion_canvas_production_is_revision_bound_rendered_and_natively_verified() {
        let (service, initial, temp) = fixture_service();
        let project = motion_canvas_fixture(&service, initial);
        let connection = fake_render_connection(&temp, project.resource_key());
        let coordinator = ProductionCoordinator::new(service, connection).unwrap();
        let expected = RevisionStamp::from(&project);
        let scene_id = project.scenes[0].id;
        let deliverable_id = project.deliverables[0].id;
        let options = FilmBuildOptions {
            frame_rate: Rate::new(30, 1).unwrap(),
            font_family: "Instrument Sans Variable".into(),
            mono_font_family: "IBM Plex Mono".into(),
            scene_intents: vec![crate::film::SceneFilmIntent {
                scene_id,
                role: NarrativeRole::Mechanism,
                archetype: Archetype::Statement,
            }],
        };

        let rendered = coordinator
            .render_motion_canvas_segments(
                project.id,
                &expected,
                "native-render-e2e",
                deliverable_id,
                &options,
            )
            .await
            .unwrap();

        assert_eq!(rendered.project_resource, project.resource_key());
        assert_eq!(rendered.generation, project.generation);
        assert_eq!(rendered.revision, project.revision);
        assert_eq!(rendered.segments.len(), 1);
        assert_eq!(rendered.segments[0].frame_count, 60);
        assert_eq!(rendered.segments[0].artifact["frame_count"], 60);
        assert_eq!(
            rendered.segments[0].verification["report"]["support_level"],
            "native"
        );
        assert_eq!(
            rendered.segments[0].verification["measurement"]["validation"]["checks"][0]["verdict"],
            "PASS"
        );
        assert_eq!(coordinator.receipts(project.id, 100).unwrap().len(), 12);
    }

    #[cfg(unix)]
    mod motion_render_integrity {
        use super::*;
        include!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/src/production/motion_render_integrity_tests.rs"
        ));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn command_allowlist_and_resource_binding_fail_closed() {
        let blender = expected_authority("driver.blender.collection.create")
            .expect("scene-linked Blender collection creation must be explicitly enabled");
        assert_eq!(blender.provider, "driver:blender");
        let link = expected_authority("driver.blender.collection.link")
            .expect("bounded Blender collection linking must be explicitly enabled");
        assert_eq!(link.provider, "driver:blender");
        assert!(link.provider_generation_required);

        let (service, project, temp) = fixture_service();
        let mut connection = fake_connection(&temp, "project:other".into());
        let coordinator = ProductionCoordinator::new(service.clone(), connection.clone()).unwrap();
        let error = coordinator
            .execute(
                project.id,
                &motionwright_domain::RevisionStamp::from(&project),
                "wrong-resource",
                "driver.motion-canvas.composition.inspect",
                json!({}),
                false,
            )
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::PermissionDenied);

        connection.resource = project.resource_key();
        let client = ProductionClient::new(connection).unwrap();
        let error = client
            .execute("driver.unsupported.execute", json!({}), true)
            .await
            .unwrap_err();
        assert_eq!(error.code, ErrorCode::Unsupported);
    }
}
